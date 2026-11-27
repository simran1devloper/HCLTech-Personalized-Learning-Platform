"""Assessment generation lifecycle: bank first, model only for what is missing, every question persisted and announced as it is saved.

One detached task per attempt (``AssessmentGeneration.launch``). It writes its progress to the database (attempt columns + ``assessment_generation_events``), never only to memory,
so a browser refresh, an SSE reconnect or another API process can replay the events (``store.events_after``) and recover the state (``state``).

Event order for one question: ``question_generated`` -> ``question_validated`` -> ``question_saved`` -> ``progress``; ``completed`` / ``error`` / ``cancelled`` end the stream.
``completed`` is only ever emitted when the requested number of validated questions is assigned to the attempt. ``progress`` is saved / requested: never estimated."""
from __future__ import annotations

import asyncio
import logging
import random
import threading
import time
from datetime import datetime, timezone

from app.domains.assessments.bank.service import QuestionBankService
from app.infrastructure.database.assessment_store import AssessmentStore
from app.observability.metrics import metrics

log = logging.getLogger("assessment_generation")
TERMINAL = {"completed", "error", "cancelled"}
STATUS_OF = {"completed": "completed", "error": "failed", "cancelled": "cancelled"}
MAX_ROUNDS = 3        # bank re-check + model round trips for the *missing* questions only; a round that adds nothing ends the run
STALE_S = 300.0       # a generating attempt with no event / heartbeat for this long and no live task in this process is reaped as interrupted


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def public_question(snap: dict, index: int, source: str) -> dict:
    """What the browser may see about a saved question: never the answer key, options' correctness or the explanation."""
    return {"question_id": snap["question_id"], "index": index, "concept": snap["concept"], "difficulty": snap["difficulty"], "question_type": snap["question_type"],
            "prompt": snap["prompt"][:140], "source": source}


class AssessmentGeneration:
    def __init__(self, bank: QuestionBankService, store: AssessmentStore):
        self.bank, self.store = bank, store
        self.tasks: dict[str, asyncio.Task] = {}
        self._cancelled_locally: set[str] = set()
        self._waiters: dict[str, set[tuple[asyncio.AbstractEventLoop, asyncio.Event]]] = {}
        self._wlock = threading.Lock()

    # ------------------------------------------------------------------ waking SSE readers (the database is the source of truth; this only removes polling delay)
    def subscribe(self, attempt_id: str) -> tuple[asyncio.Event, tuple]:
        ev, loop = asyncio.Event(), asyncio.get_running_loop()
        entry = (loop, ev)
        with self._wlock:
            self._waiters.setdefault(attempt_id, set()).add(entry)
        return ev, entry

    def unsubscribe(self, attempt_id: str, entry: tuple) -> None:
        with self._wlock:
            self._waiters.get(attempt_id, set()).discard(entry)

    def _notify(self, attempt_id: str) -> None:
        with self._wlock:
            targets = list(self._waiters.get(attempt_id, ()))
        for loop, ev in targets:
            loop.call_soon_threadsafe(ev.set)

    # ------------------------------------------------------------------ state (refresh / reconnect recovery)
    def state(self, a: dict) -> dict:
        rows = self.store.attempt_questions(a["attempt_id"])
        requested = a.get("requested_questions") or len(rows)
        done = len(rows)
        last = self.store.last_seq(a["attempt_id"])
        return {"assessment_id": a["attempt_id"], "status": a.get("generation_status") or "completed", "requested": requested, "completed": done,
                "progress": round(100 * done / requested, 1) if requested else 100.0, "last_seq": last, "error": a.get("generation_error"),
                "questions": [public_question(r["question_snapshot"], i, "saved") for i, r in enumerate(rows)]}

    def reap_if_stale(self, a: dict) -> bool:
        """A generating attempt nobody is working on (its process died) is closed as interrupted; everything saved so far stays assigned."""
        if a.get("generation_status") != "generating" or a["attempt_id"] in self.tasks:
            return False
        beat = a.get("generation_updated_at")
        if beat is not None and (datetime.now(timezone.utc) - beat).total_seconds() < STALE_S:
            return False
        if self.store.finish_generation_if_active(a["attempt_id"], "failed", "interrupted"):
            self._event(a["attempt_id"], "error", a.get("requested_questions") or 0, reason="interrupted", message="Question generation was interrupted. Saved questions were kept.")
            metrics.inc("assessment_generation_total", outcome="interrupted")
            return True
        return False

    # ------------------------------------------------------------------ events
    def _event(self, attempt_id: str, type_: str, requested: int, **extra) -> int:
        done = self.store.question_count(attempt_id)
        status = STATUS_OF.get(type_, "generating")
        payload = {"assessment_id": attempt_id, "type": type_, "status": status, "completed": done, "requested": requested,
                   "progress": round(100 * done / requested, 1) if requested else 100.0, "timestamp": _iso(), **extra}
        seq = self.store.add_event(attempt_id, type_, payload)
        self._notify(attempt_id)
        return seq

    # ------------------------------------------------------------------ control
    def launch(self, attempt: dict, skill_name: str, prior: float | None) -> asyncio.Task:
        aid = attempt["attempt_id"]
        task = asyncio.ensure_future(self._run(attempt, skill_name, prior))
        self.tasks[aid] = task
        task.add_done_callback(lambda t: (self.tasks.pop(aid, None) if self.tasks.get(aid) is t else None, self._cancelled_locally.discard(aid)))
        return task

    def cancel(self, user_id: str, attempt_id: str) -> bool:
        """Persist the request (any process sees it) and interrupt the local task. Saved questions are kept. False when nothing was generating."""
        if not self.store.request_cancel(user_id, attempt_id):
            return False
        task = self.tasks.get(attempt_id)
        if task is not None and not task.done():
            self._cancelled_locally.add(attempt_id)
            task.get_loop().call_soon_threadsafe(task.cancel)
        else:   # no live runner anywhere near here: close it now rather than leave the learner waiting for the reaper
            a = self.store.attempt_by_id(attempt_id)
            if a and a.get("generation_updated_at") and (datetime.now(timezone.utc) - a["generation_updated_at"]).total_seconds() >= STALE_S \
                    and self.store.finish_generation_if_active(attempt_id, "cancelled"):
                self._event(attempt_id, "cancelled", a["requested_questions"] or 0, message="Generation was stopped.")
        return True

    # ------------------------------------------------------------------ the run
    async def _run(self, a: dict, skill_name: str, prior: float | None) -> None:
        aid, user, skill_id, n = a["attempt_id"], a["learner_id"], a["skill_id"], a["requested_questions"]
        t0 = time.perf_counter()
        rng = random.Random(aid)
        first_ms: list[float] = []
        stats = {"generated": 0, "rejected": 0, "llm_calls": 0, "bank": 0}
        last_cancel_check: list = [0.0, False]

        def assigned_ids() -> set[str]:
            return {r["question_id"] for r in self.store.attempt_questions(aid)}

        def count() -> int:
            return self.store.question_count(aid)

        def cancel_flag() -> bool:
            if last_cancel_check[1]:
                return True
            if time.monotonic() - last_cancel_check[0] < 1.0:
                return False
            last_cancel_check[0] = time.monotonic()
            row = self.store.attempt_by_id(aid)
            last_cancel_check[1] = bool(row and row.get("cancel_requested"))
            return last_cancel_check[1]

        def saved(snap: dict, index: int, source: str) -> None:
            if not first_ms:
                first_ms.append((time.perf_counter() - t0) * 1000)
                metrics.observe("assessment_time_to_first_question_ms", first_ms[0])
            self._event(aid, "question_saved", n, question=public_question(snap, index, source), stage="saving")
            self._event(aid, "progress", n, stage="finalizing" if count() >= n else "generating")

        def assign_bank(exclude: set[str]) -> None:
            need = n - len(exclude)
            if need <= 0:
                return
            sel = self.bank.select(user, skill_id, need, prior, rng, exclude=exclude)
            full = self.store.full([r["id"] for r in sel.rows])
            for r in sel.rows:
                snap = self.bank.snapshot(full[r["id"]], rng)
                if (idx := self.store.append_question(aid, snap, n)) is not None:
                    stats["bank"] += 1
                    saved(snap, idx - 1, "bank")

        def on_question(qid: str, q: dict, created: bool) -> bool:
            stats["generated"] += 1
            self._event(aid, "question_generated", n, stage="validating", generated=stats["generated"],
                        question={"concept": q.get("concept"), "difficulty": q.get("difficulty"), "question_type": q.get("question_type")})
            if qid is None or qid in assigned_ids() or count() >= n:
                self._event(aid, "question_validated", n, valid=False, reason="duplicate", stage="generating", question={"concept": q.get("concept")})
                metrics.inc("assessment_generation_duplicates_total")
                return False
            self._event(aid, "question_validated", n, valid=True, stage="saving", question={"question_id": qid, "concept": q.get("concept"), "difficulty": q.get("difficulty")})
            t_save = time.perf_counter()
            snap = self.bank.snapshot(self.store.full([qid])[qid], rng)
            idx = self.store.append_question(aid, snap, n)
            metrics.observe("assessment_question_save_ms", (time.perf_counter() - t_save) * 1000)
            if idx is None:
                return False
            saved(snap, idx - 1, "generated" if created else "bank")
            return True

        def on_rejected(q: dict, errs: list[str]) -> None:
            stats["rejected"] += 1
            self._event(aid, "question_generated", n, stage="validating", generated=stats["generated"] + stats["rejected"], question={"concept": q.get("concept")})
            self._event(aid, "question_validated", n, valid=False, reason="invalid", errors=[e[:120] for e in errs[:3]], stage="generating", question={"concept": q.get("concept")})

        def on_partial(k: int) -> None:      # model activity only: questions streamed so far, NOT progress (they are not validated or saved yet)
            self._event(aid, "generation_activity", n, stage="generating", streamed=k)

        def should_stop() -> bool:
            return count() >= n or cancel_flag()

        outcome, reason, message = "completed", None, None
        try:
            have = self.store.attempt_questions(aid)
            self._event(aid, "generation_started", n, stage="checking_bank", from_bank=len(have))
            for i, r in enumerate(have):                         # served from the bank when the attempt was created: announce them in order
                stats["bank"] += 1
                saved(r["question_snapshot"], i, "bank")
            for rnd in range(MAX_ROUNDS):
                if count() >= n or self.store.attempt_by_id(aid).get("cancel_requested"):
                    break
                if rnd:                                          # another request may have filled the bank meanwhile: use that before paying for the model
                    assign_bank(assigned_ids())
                    if count() >= n:
                        break
                missing = n - count()
                rows = [r["question_snapshot"] for r in self.store.attempt_questions(aid)]
                chosen = [{"concept": s["concept"], "difficulty": s["difficulty"]} for s in rows]
                self._event(aid, "progress", n, stage="generating", missing=missing, round=rnd + 1)
                pop = await self.bank.populate(skill_name, skill_id, missing, difficulties=self.bank.identify_missing_difficulties(chosen, n, prior),
                                               concepts=self.bank.identify_missing_concepts(skill_id, chosen) if chosen else [], on_question=on_question,
                                               on_rejected=on_rejected, should_stop=should_stop, on_partial=on_partial)
                stats["llm_calls"] += pop.llm_calls
                if count() < n and not pop.deduplicated and pop.added == 0:
                    outcome, reason = "error", pop.error or "invalid_output"
                    break
            if outcome == "completed" and self.store.attempt_by_id(aid).get("cancel_requested") and count() < n:
                outcome = "cancelled"
            elif outcome == "completed" and count() < n:
                outcome, reason = "error", reason or "incomplete"
        except asyncio.CancelledError:
            outcome, reason = ("cancelled", None) if aid in self._cancelled_locally else ("error", "interrupted")
            message = "Generation was stopped." if outcome == "cancelled" else "Question generation was interrupted. Saved questions were kept."
        except Exception as e:  # noqa: BLE001 - e.g. a database failure: saved questions stay, the learner gets a retryable error
            log.exception("assessment generation %s failed", aid)
            outcome, reason = "error", "internal"
            message = type(e).__name__

        done = count()
        if outcome == "completed" and done < n:                  # never report completion without the requested number of validated questions
            outcome, reason = "error", "incomplete"
        status = STATUS_OF["completed" if outcome == "completed" else outcome]
        if self.store.finish_generation_if_active(aid, status, reason):
            try:
                self._event(aid, outcome, n, stage=outcome, reason=reason, message=(message or self.bank.unavailable_message(reason, skill_name)) if outcome == "error" else message,
                            saved_kept=done, llm_calls=stats["llm_calls"], rejected=stats["rejected"], from_bank=stats["bank"], duration_ms=round((time.perf_counter() - t0) * 1000))
            except Exception:  # noqa: BLE001
                log.exception("could not write terminal event for %s", aid)
        metrics.inc("assessment_generation_total", outcome=outcome)
        metrics.observe("assessment_generation_total_ms", (time.perf_counter() - t0) * 1000)
        metrics.inc("assessment_llm_calls_total", stats["llm_calls"])
        log.info("assessment %s %s: %d/%d saved (bank %d, generated %d, rejected %d, llm_calls %d) in %.0f ms, first question %s ms", aid, outcome, done, n, stats["bank"],
                 stats["generated"], stats["rejected"], stats["llm_calls"], (time.perf_counter() - t0) * 1000, round(first_ms[0]) if first_ms else "-")
