"""QuestionBankService: database-first question retrieval for assessments.

Order of preference: active+validated bank questions -> matching skill -> target difficulty mix -> different concepts -> not recently seen.
The model is used only to *top up* what is missing (bounded, validated, persisted), never to select, score or analyse."""
from __future__ import annotations

import asyncio
import logging
import math
import os
import random
import re
import string
import threading
import time
import uuid
from dataclasses import dataclass, field

from app.core.config import get_settings
from app.core.exceptions import AppError, ConflictError, ValidationFailure
from app.domains.assessments.bank import generator as gen
from app.domains.assessments.bank.validation import content_hash
from app.infrastructure.database.assessment_store import AssessmentStore
from app.observability.metrics import metrics

log = logging.getLogger("question_bank")
PRACTICAL_TYPES = {"scenario", "practical", "debugging"}
PYTHON_SKILLS = {"python", "data-structures-and-algorithms"}


class QuestionsUnavailable(AppError):
    status_code, code = 503, "questions_unavailable"


WORKER_ID = f"api-{os.getpid()}-{uuid.uuid4().hex[:6]}"
GENERIC_SUFFIXES = ("programming language", "programming", "language", "fundamentals", "basics", "tutorial", "course")
TOPIC_OK = re.compile(r"^[\w .+#&/'()-]+$", re.UNICODE)
GENERATION_TIMEOUT_S: float | None = None   # per model call; None = the provider timeout (LLM_TIMEOUT_S) plus a margin. Tests override it.
WAIT_FOR_RUNNING_S: float | None = None     # how long a second request waits for the first one's generation; None = the generation budget
LEVEL_PRIOR = {"beginner": 1.0, "intermediate": None, "advanced": 4.2, "expert": 4.2}
PARALLEL_MIN_MISSING = 4     # fewer missing questions than this: one call is as fast
PARALLEL_MIN_PER_CALL = 3
PARALLEL_SPARE = 0.2         # ask for this share extra, so a rejected question rarely costs another round
# When several calls write about a topic at once and it has no concept areas yet, each gets its own area: this keeps the calls from
# producing the same questions and keeps the area names few and consistent for the gap analysis.
CONCEPT_AREAS = ("Core concepts", "Practical usage", "Troubleshooting and pitfalls", "Design and performance")


@dataclass
class Availability:
    skill_id: str
    requested: int
    eligible: int
    by_difficulty: dict = field(default_factory=dict)
    concepts: int = 0

    @property
    def missing(self) -> int:
        return max(0, self.requested - self.eligible)

    @property
    def plan(self) -> str:
        """What a start would do: 'question_bank' (no model), 'hybrid' (generate only the missing ones), 'ai_generated' (nothing usable stored yet)."""
        return "question_bank" if self.missing == 0 else "ai_generated" if self.eligible == 0 else "hybrid"


@dataclass
class GenResult:
    added: int = 0
    rejected: int = 0
    llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    error: str | None = None            # timeout | provider | invalid_output | busy


@dataclass
class Populated:
    pending: bool = False               # the wait budget ran out while generation is still going in the background (batches already saved are usable)
    added: int = 0
    llm_calls: int = 0
    deduplicated: bool = False          # another request was already generating for this skill; we waited for it instead of calling the model
    error: str | None = None


@dataclass
class Prepared:
    attempt_id: str
    created: bool
    selection: "Selection"
    source: str                          # question_bank | ai_generated | hybrid
    llm_calls: int
    generated: int
    deduplicated: bool
    error: str | None


@dataclass
class Selection:
    rows: list[dict] = field(default_factory=list)       # chosen light rows, final display order
    shortfall: int = 0                                   # how many fewer than requested the bank could supply
    repeated: int = 0                                    # chosen questions the learner saw in a recent attempt
    distribution: dict = field(default_factory=dict)


def distribution(n: int, prior_level: float | None = None) -> dict:
    """Foundational (difficulty 1-2) / intermediate (3) / advanced (4-5) / practical-scenario slots.
    n=10 -> 3/4/2 + 1 scenario. Proportionate for small n. A known strong learner shifts one slot up; a known weak learner one down."""
    s = 1 if n >= 8 else 0
    r = n - s
    f, a = round(r * 3 / 9), round(r * 2 / 9)
    i = r - f - a
    if prior_level is not None:
        if prior_level >= 3.5 and f > 0:
            f, a = f - 1, a + 1
        elif prior_level <= 1.5 and a > 0:
            f, a = f + 1, a - 1
    return {"foundational": f, "intermediate": i, "advanced": a, "scenario": s}


def _bucket(d: int) -> str:
    return "foundational" if d <= 2 else "intermediate" if d == 3 else "advanced"


class QuestionBankService:
    def __init__(self, store: AssessmentStore, call: gen.Call | None = None, registry=None):
        self.store, self.call, self.registry = store, call, registry
        self._avail: tuple[float, dict] | None = None
        self._lock = threading.Lock()
        self._topping: set[str] = set()
        self._tasks: dict = {}
        self._progress: dict[str, dict] = {}   # skill_id -> {state, target, added}; process-local, for the Assessments progress bar

    # ------------------------------------------------------------------ cache (process-local; the database stays canonical)
    def availability(self, ttl_s: float = 60.0) -> dict[str, dict]:
        with self._lock:
            if self._avail and time.monotonic() - self._avail[0] < ttl_s:
                metrics.inc("assessment_availability_cache_total", outcome="hit")
                return self._avail[1]
        data = self.store.availability()
        with self._lock:
            self._avail = (time.monotonic(), data)
        metrics.inc("assessment_availability_cache_total", outcome="miss")
        return data

    def invalidate(self) -> None:
        with self._lock:
            self._avail = None

    # ------------------------------------------------------------------ selection
    def select(self, user_id: str, skill_id: str, n: int, prior_level: float | None, rng: random.Random, prefer_concepts: tuple[str, ...] | frozenset = (), exclude: set[str] | frozenset = frozenset()) -> Selection:
        t0 = time.perf_counter()
        cands = [q for q in self.store.candidates(skill_id) if q["id"] not in exclude]   # ``exclude``: already assigned to the attempt being completed
        recent = self.store.recently_assigned(user_id, skill_id) if cands else set()
        metrics.observe("assessment_candidate_query_ms", (time.perf_counter() - t0) * 1000)
        if prefer_concepts:   # e.g. a knowledge check for a learner with known weak areas: draw from those areas whenever they hold enough questions
            focused = [q for q in cands if q["concept"] in prefer_concepts]
            if len(focused) >= n:
                cands = focused
        dist = distribution(n, prior_level)
        chosen: list[dict] = []
        used: dict[str, int] = {}
        used_sub: dict[str, int] = {}
        taken: set[str] = set()

        def pick(pool: list[dict], center: float | None = None) -> dict | None:
            pool = [q for q in pool if q["id"] not in taken]
            if not pool:
                return None
            # nearest difficulty (only when borrowing across buckets), then diversity (fewest picks from this concept), then random for variety
            q = min(pool, key=lambda x: (round(abs(x["difficulty"] - center)) if center is not None else 0, 0 if x["concept"] in prefer_concepts else 1, used.get(x["concept"], 0), used_sub.get(x["subtopic"], 0), rng.random()))
            taken.add(q["id"])
            used[q["concept"]] = used.get(q["concept"], 0) + 1
            used_sub[q["subtopic"]] = used_sub.get(q["subtopic"], 0) + 1
            chosen.append(q)
            return q

        def fill(pool: list[dict], center: float) -> None:
            """One slot, in preference order: unseen in the bucket -> unseen at the nearest difficulty -> seen in the bucket -> anything left."""
            unseen = [q for q in cands if q["id"] not in taken and q["id"] not in recent]
            in_bucket_unseen = [q for q in pool if q["id"] not in recent]
            if pick(in_bucket_unseen) or pick(unseen, center) or pick(pool) or pick([q for q in cands], center):
                return

        centers = {"foundational": 1.5, "intermediate": 3.0, "advanced": 4.5}
        by_bucket = {b: [q for q in cands if _bucket(q["difficulty"]) == b] for b in centers}
        for _ in range(dist["scenario"]):
            sc = [q for q in cands if q["question_type"] in PRACTICAL_TYPES]
            pick([q for q in sc if q["id"] not in recent]) or pick(sc)
        for b in ("advanced", "intermediate", "foundational"):
            for _ in range(dist[b]):
                fill(by_bucket[b], centers[b])
        while len(chosen) < n and pick(cands) is not None:  # rounding leftovers
            pass
        rng.shuffle(chosen)
        return Selection(rows=chosen[:n], shortfall=max(0, n - len(chosen)), repeated=sum(1 for q in chosen if q["id"] in recent), distribution=dist)

    @staticmethod
    def snapshot(row: dict, rng: random.Random) -> dict:
        """Learner-assigned copy of a bank row: options shuffled (the correct indices are remapped), everything needed to grade and review later."""
        opts = list(row["options"])
        order = list(range(len(opts)))
        rng.shuffle(order)
        correct = sorted(order.index(i) for i in row["correct_answer"])
        return {"question_id": row["id"], "skill_id": row["skill_id"], "concept": row["concept"], "subtopic": row["subtopic"], "difficulty": row["difficulty"],
                "question_type": row["question_type"], "cognitive_level": row["cognitive_level"], "prompt": row["question_text"], "code_snippet": row["code_snippet"],
                "options": [opts[i] for i in order], "correct": correct, "explanation": row["explanation"], "solution_approach": row["solution_approach"],
                "practical_example": row["practical_example"], "estimated_time_seconds": row["estimated_time_seconds"], "bank_version": row["version"],
                "practical": row["question_type"] in PRACTICAL_TYPES}

    def start(self, user_id: str, skill_id: str, n: int, prior_level: float | None, idempotency_key: str | None, *, kind: str = "assessment", topic: str | None = None,
              source: str | None = None, prefer_concepts: tuple[str, ...] | frozenset = (), generate_missing: bool = False) -> tuple[str, bool, Selection, bool]:
        """Select, snapshot and persist an attempt in one transaction. Returns (attempt_id, created, selection, from_cache_replay).
        With ``generate_missing`` a short bank does not shrink the attempt: it is created with what the bank has (possibly nothing) in the ``generating`` state
        and the caller completes it (``AssessmentGeneration``)."""
        t0 = time.perf_counter()
        attempt_id = str(uuid.uuid4())
        rng = random.Random(attempt_id)
        sel = self.select(user_id, skill_id, n, prior_level, rng, prefer_concepts)
        generating = generate_missing and sel.shortfall > 0
        if not sel.rows and not generating:
            return "", False, sel, False
        full = self.store.full([r["id"] for r in sel.rows])
        snaps = [self.snapshot(full[r["id"]], rng) for r in sel.rows]
        mean_d = sum(s["difficulty"] for s in snaps) / len(snaps) if snaps else 3.0
        level = "foundational" if mean_d < 2.3 else "intermediate" if mean_d < 3.5 else "advanced"
        aid, created = self.store.create_attempt(attempt_id=attempt_id, user_id=user_id, skill_id=skill_id, questions=snaps, difficulty_level=level, idempotency_key=idempotency_key,
                                                kind=kind, topic=topic, generation_source=source, requested=n if generating else None)
        metrics.observe("assessment_question_retrieval_ms", (time.perf_counter() - t0) * 1000)
        if created:
            metrics.inc("assessment_questions_served_total", len(snaps), source="bank")
        return aid, created, sel, not created

    # ------------------------------------------------------------------ topics: deterministic normalisation and resolution (never an LLM)
    @staticmethod
    def normalize_topic(text: str) -> str:
        """Whitespace-normalised, markup-free display form of what the user typed. Raises ValidationFailure for empty / unsupported input."""
        t = re.sub(r"\s+", " ", str(text or "")).strip()
        if len(t) < 2:
            raise ValidationFailure("Enter a topic of at least two characters.")
        if len(t) > 80:
            raise ValidationFailure("Topics can be at most 80 characters.")
        if not TOPIC_OK.match(t) or not re.search(r"[A-Za-z0-9]", t):
            raise ValidationFailure("That topic contains characters we cannot use. Try letters, numbers and spaces.")
        return t

    @staticmethod
    def topic_candidates(text: str) -> list[str]:
        """Lookup forms, most specific first: 'python programming' -> ['python programming', 'python']."""
        t = text.lower()
        out = [t]
        for suf in GENERIC_SUFFIXES:
            if t.endswith(" " + suf) and len(t) > len(suf) + 1:
                out.append(t[: -len(suf)].strip())
                break
        return out

    def resolve_skill(self, text: str):
        """Canonical skill for free text via exact slug / explicit alias only (no fuzzy matching, so 'java' never becomes 'javascript'). None if unknown."""
        for cand in self.topic_candidates(text):
            s = self.registry.resolve(cand, 1.0)
            if s:
                return s
        return None

    def resolve_or_register(self, text: str):
        """Resolve, or register the topic as a PROPOSED skill so generated questions have a canonical home (and 'rust' / 'rust programming' share it)."""
        s = self.resolve_skill(text)
        if s:
            return s, False
        name = string.capwords(text.lower()) if text == text.lower() or text.isupper() else text
        alias = [c for c in self.topic_candidates(text)[1:] if len(c) >= 2]
        from app.domains.skill_registry.models import SkillStatus
        from sqlalchemy.exc import IntegrityError
        try:
            return self.registry.create_skill(name, status=SkillStatus.PROPOSED, aliases=alias), True
        except (ConflictError, IntegrityError):  # another request registered it first (check-then-insert race): use theirs
            s = self.resolve_skill(text)
            if s:
                return s, False
            raise

    def suggest(self, q: str, limit: int = 8) -> list[dict]:
        """Typeahead from the canonical registry (names and aliases) with how many questions each skill already has. Suggestions only; free text is always allowed."""
        t = q.strip().lower()
        if not t:
            return []
        avail = self.availability()
        out = []
        for s in self.registry.repo.list(limit=100000)[0]:
            names = [s.name.lower(), *[a.lower() for a in s.aliases]]
            rank = 0 if any(n == t for n in names) else 1 if any(n.startswith(t) for n in names) else 2 if any(t in n for n in names) else None
            if rank is None:
                continue
            a = avail.get(s.skill_id)
            out.append((rank, -(a["total"] if a else 0), s.name, {"skill_id": s.skill_id, "name": s.name, "category": s.category, "question_count": a["total"] if a else 0}))
        return [x[3] for x in sorted(out)[:limit]]

    # ------------------------------------------------------------------ availability and gap analysis (deterministic, database only)
    def check_question_availability(self, skill_id: str, n: int) -> Availability:
        cands = self.store.candidates(skill_id)
        by: dict[int, int] = {}
        for q in cands:
            by[q["difficulty"]] = by.get(q["difficulty"], 0) + 1
        return Availability(skill_id, n, len(cands), by, len({q["concept"] for q in cands}))

    def get_eligible_questions(self, user_id: str, skill_id: str, n: int, prior_level: float | None = None, rng: random.Random | None = None) -> Selection:
        return self.select(user_id, skill_id, n, prior_level, rng or random.Random())

    @staticmethod
    def calculate_missing_question_count(availability: Availability) -> int:
        return availability.missing

    def identify_missing_concepts(self, skill_id: str, chosen: list[dict], limit: int = 4) -> list[str]:
        """Concept areas the bank covers least among the questions already chosen: where new questions add the most coverage. Empty for a brand-new skill."""
        covered: dict[str, int] = {t["concept"]: 0 for t in self.store.topics(skill_id)}
        for q in chosen:
            covered[q["concept"]] = covered.get(q["concept"], 0) + 1
        return [c for c, _ in sorted(covered.items(), key=lambda kv: (kv[1], kv[0]))[:limit]]

    @staticmethod
    def identify_missing_difficulties(chosen: list[dict], n: int, prior_level: float | None = None) -> list[int]:
        """Target difficulties for the slots the chosen questions leave open, following the same foundational/intermediate/advanced/scenario mix."""
        want = distribution(n, prior_level)
        have = {"foundational": 0, "intermediate": 0, "advanced": 0}
        for q in chosen:
            have[_bucket(q["difficulty"])] += 1
        out: list[int] = []
        for b, picks in (("advanced", (4, 5)), ("intermediate", (3,)), ("foundational", (1, 2))):
            gap = max(0, want[b] + (want["scenario"] if b == "intermediate" else 0) - have[b])  # scenario slot is most naturally intermediate
            out += [picks[i % len(picks)] for i in range(gap)]
        while len(out) < n - len(chosen):
            out.append(3)
        return out[: n - len(chosen)]

    # ------------------------------------------------------------------ generation (only what is missing) behind a cross-process lock
    @staticmethod
    def call_timeout() -> float:
        return GENERATION_TIMEOUT_S if GENERATION_TIMEOUT_S is not None else get_settings().LLM_TIMEOUT_S + 15

    @staticmethod
    def budget_s() -> float:
        return get_settings().QUESTION_GENERATION_BUDGET_S

    def batch_plan(self, missing: int) -> tuple[int, bool]:
        """(first batch size, adaptive). Local Ollama starts with ONE question and sizes later batches from the measured speed; a configured size is fixed;
        hosted providers (or an injected call) get a single call."""
        configured = get_settings().QUESTION_BATCH_SIZE
        if configured > 0:
            return min(configured, missing), False
        if self.call is None:
            try:
                from app.ai.runtime import get_router
                if getattr(get_router().providers[0], "name", "") == "ollama":
                    return min(1, missing), missing > 1
            except Exception:  # noqa: BLE001 - provider introspection is best effort
                pass
        return missing, False

    def batch_size(self, missing: int) -> int:
        return self.batch_plan(missing)[0]

    def parallel_batches(self, missing: int) -> int:
        """Simultaneous model calls for one generation round. Only for a hosted provider with automatic batch sizing: a self-hosted
        model server would just queue them."""
        s = get_settings()
        if s.QUESTION_PARALLEL_BATCHES < 2 or s.QUESTION_BATCH_SIZE > 0 or self.call is not None or missing < PARALLEL_MIN_MISSING:
            return 1
        try:
            from app.ai.runtime import get_router
            first = get_router().providers[0]
            if getattr(first, "name", "") == "ollama" or first.model_capabilities().local:
                return 1
        except Exception:  # noqa: BLE001 - provider introspection is best effort
            return 1
        asked = missing + math.ceil(missing * PARALLEL_SPARE)
        return max(1, min(s.QUESTION_PARALLEL_BATCHES, math.ceil(asked / PARALLEL_MIN_PER_CALL)))

    @staticmethod
    def split_batches(missing: int, parallel: int, difficulties: list[int], concepts: list[str]) -> list[tuple[int, list[int], list[str]]]:
        """(size, target difficulties, concept areas) per simultaneous call. Asks for a few spares, spreads difficulties across the
        calls and gives every call different concept areas."""
        asked = missing + math.ceil(missing * PARALLEL_SPARE)
        k = max(1, min(parallel, math.ceil(asked / PARALLEL_MIN_PER_CALL)))
        wanted_levels = list(difficulties[:missing]) or [3]
        levels = [wanted_levels[i % len(wanted_levels)] for i in range(asked)]
        areas = list(dict.fromkeys(concepts)) or list(CONCEPT_AREAS)
        out = []
        for i in range(k):
            mine = levels[i::k]
            out.append((len(mine), mine, areas[i::k] or [areas[i % len(areas)]]))
        return out

    async def generate_missing_questions(self, skill_name: str, skill_id: str, missing: int, *, difficulties: list[int] | None = None, concepts: list[str] | None = None,
                                         timeout_s: float | None = None, on_batch=None, on_partial=None, on_question=None, on_rejected=None, should_stop=None) -> GenResult:
        """Generate ``missing`` questions in batches; each batch is validated deterministically and **saved immediately**, so a later timeout or failure never loses
        what was already produced. Never raises for model / validation failures: returns the reason (``error`` is set only when nothing at all was added).

        ``on_question(question_id, question, created) -> bool`` runs for every validated question right after it is persisted in the bank and returns whether it was
        taken (assigned to the caller's attempt); only taken questions count as added, so duplicates are replaced by the next call instead of being counted.
        ``on_rejected(question, errors)`` reports a question that failed validation; ``should_stop()`` ends the loop early (the caller already has enough)."""
        if missing <= 0:
            return GenResult()
        out = GenResult()
        call_timeout = self.call_timeout() if timeout_s is None else timeout_s
        lang = "python" if skill_name.lower().replace(" ", "-") in PYTHON_SKILLS else None
        have = self.store.availability().get(skill_id, {}).get("by_difficulty", {})
        diffs = list(difficulties or (sorted(range(1, 6), key=lambda d: have.get(d, 0))[:max(1, missing)] * missing)[:missing])
        wanted = concepts if concepts is not None else [t["concept"] for t in sorted(self.store.topics(skill_id), key=lambda t: t["questions"])[:6]]
        size, adaptive = self.batch_plan(missing)
        size = max(1, size)
        failure: str | None = None
        empty_streak = 0
        parallel = self.parallel_batches(missing)
        call_cap = (parallel * 2 + 2) if parallel > 1 else -(-missing // size) * 2 + 2   # a few rejected batches are normal for small models; never loop forever
        started = time.monotonic()
        streamed: dict[int, int] = {}                   # call number -> questions fully streamed so far (progress only)

        def progress_for(call_no: int):
            if not on_partial or self.call is not None:
                return None

            def report(k: int) -> None:
                streamed[call_no] = k
                on_partial(min(missing, out.added + sum(streamed.values())))
            return report

        async def request(call_no: int, n: int, batch_diffs: list[int], batch_concepts: list[str]) -> tuple[int, dict | None, str | None]:
            """One model call -> (call number, validated result, failure reason). One retry for a transient provider error."""
            for attempt in (1, 2):   # a local server swapping models, a dropped connection; never retry a timeout
                try:
                    res = await asyncio.wait_for(gen.generate(skill_name, n, call=self.call or gen.router_call, concepts_wanted=batch_concepts, difficulties=batch_diffs,
                                                              avoid=[t["concept"] for t in self.store.topics(skill_id)], code_language=lang,
                                                              on_item=progress_for(call_no)), call_timeout)
                    return call_no, res, None
                except asyncio.TimeoutError:
                    log.warning("question generation batch timed out for %s", skill_name)
                    return call_no, None, "timeout"
                except Exception as e:  # noqa: BLE001 - provider down, auth, rate limit, ...
                    msg = str(e)
                    why = "timeout" if "timed out" in msg.lower() or "timeout" in type(e).__name__.lower() else "provider"   # the provider's own timeout is still a timeout
                    log.warning("question generation failed for %s (attempt %d): %s: %s", skill_name, attempt, type(e).__name__, msg[:200])
                    if why == "timeout" or attempt == 2:
                        return call_no, None, why
                    await asyncio.sleep(2)
            return call_no, None, "provider"

        def save(res: dict) -> int:
            """Persist one result immediately. Returns how many questions count towards `missing`."""
            out.tokens_in += res["tokens_in"]
            out.tokens_out += res["tokens_out"]
            metrics.inc("assessment_question_generation_tokens_total", res["tokens_in"], direction="in")
            metrics.inc("assessment_question_generation_tokens_total", res["tokens_out"], direction="out")
            batch_added = 0
            for q in res["valid"]:
                qid, created = self.store.insert_question(skill_id, q, content_hash=q["_hash"], source_type="llm", validation_status="validated", quality=q["_quality"])
                if parallel > 1 and out.added + batch_added >= missing:
                    continue                        # a spare: stays in the bank for the next quiz, not part of this request
                batch_added += int(on_question(qid, q, created)) if on_question else int(created)
            for q, _errs in res["rejected"]:  # kept as 'rejected' for audit; never served
                out.rejected += 1
                metrics.inc("assessment_questions_rejected_total")
                if on_rejected:
                    on_rejected(q, _errs)
                try:
                    if all(q.get(k) is not None for k in ("question_text", "options", "correct", "explanation", "difficulty", "question_type", "cognitive_level")) and q["difficulty"] in (1, 2, 3, 4, 5):
                        self.store.insert_question(skill_id, {**q, "subtopic": q.get("subtopic") or q["concept"] or "unknown", "concept": q["concept"] or "unknown"},
                                                   content_hash=content_hash(str(q.get("question_text")), q.get("code_snippet")), source_type="llm", validation_status="rejected", quality=0.0)
                except Exception:  # noqa: BLE001 - audit trail is best effort (e.g. violates DB check constraints)
                    pass
            out.added += batch_added
            if batch_added:
                self.invalidate()
            if on_batch:
                on_batch(out.added)
            for _q, errs in res["rejected"]:
                log.info("rejected generated question for %s: %s", skill_name, "; ".join(errs)[:160])
            return batch_added

        while out.added < missing and not failure and out.llm_calls < call_cap and not (should_stop and should_stop()):
            remaining = missing - out.added
            added_before = out.added
            if parallel > 1 and remaining >= PARALLEL_MIN_MISSING:
                batches = self.split_batches(remaining, parallel, diffs[out.added:], wanted)[: call_cap - out.llm_calls]
            else:
                n = min(size if parallel == 1 else remaining, remaining)
                batches = [(n, diffs[out.added: out.added + n] or [3] * n, wanted)]
            tasks = []
            streamed.clear()
            for n, batch_diffs, batch_concepts in batches:
                out.llm_calls += 1
                metrics.inc("assessment_question_generation_calls_total")
                tasks.append(asyncio.ensure_future(request(out.llm_calls, n, batch_diffs, batch_concepts)))
            t_batch = time.monotonic()
            errors: list[str] = []
            answered = 0
            try:
                for next_done in asyncio.as_completed(tasks):
                    call_no, res, why = await next_done
                    streamed.pop(call_no, None)
                    if res is None:
                        errors.append(why or "provider")
                    else:
                        answered += 1
                        batch_added = save(res)
                        if adaptive and batch_added:
                            # size the next batch from the measured speed so one call uses at most about half the timeout: a CPU-bound 8B model stays at 1 question
                            # per call, a fast local server finishes the rest in one more call
                            per_q = max(0.001, (time.monotonic() - t_batch) / batch_added)
                            size = max(1, min(missing - out.added, int((call_timeout * 0.5) / per_q)))
                    if out.added >= missing or (should_stop and should_stop()):
                        break                       # enough: stop paying for the calls still running
            finally:
                for t in tasks:
                    if not t.done():
                        t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            if errors and not answered:
                failure = errors[0]                 # every call of this round failed
                break
            empty_streak = 0 if out.added > added_before else empty_streak + 1
            if empty_streak >= 2:
                failure = "invalid_output"                 # two rounds in a row produced nothing usable: more of the same would only burn time
        if out.llm_calls:
            metrics.observe("quiz_generation_ms", (time.monotonic() - started) * 1000, parallel=parallel)
        if out.added == 0:
            out.error = failure or "invalid_output"
        return out

    def generation_progress(self, skill_id: str) -> dict:
        """Live state of question generation for a skill: ``state`` is idle | running | done | stopped | failed, with ``percent`` (0-100, None while the target is not known yet)."""
        p = self._progress.get(skill_id)
        if not p:
            return {"state": "idle", "target": 0, "added": 0, "percent": None}
        t, a = p["target"], max(p["added"], p.get("inflight", 0))  # saved questions, or more if a batch is still streaming in
        pct = 100 if p["state"] == "done" else (min(99, int(100 * a / t)) if t else None)
        return {"state": p["state"], "target": t, "added": a, "percent": pct}

    def stop_generation(self, skill_id: str) -> bool:
        """Cancel the running generation for a skill. Batches already validated and saved stay in the bank. Returns False when nothing was running here."""
        task = self._tasks.get(skill_id)
        if task is None or task.done():
            return False
        p = self._progress.setdefault(skill_id, {"target": 0, "added": 0})
        p["state"] = "stopped"
        task.get_loop().call_soon_threadsafe(task.cancel)
        return True

    def _spawn(self, key: str, coro) -> "asyncio.Task":
        """Run generation detached from any single HTTP request: a slow model keeps working (and saving batches) after a request stops waiting or disconnects."""
        task = asyncio.ensure_future(coro)
        self._tasks[key] = task

        def _done(t: "asyncio.Task") -> None:
            if self._tasks.get(key) is t:
                self._tasks.pop(key, None)
            if not t.cancelled() and t.exception():
                log.warning("background question generation for %s failed: %r", key, t.exception())
        task.add_done_callback(_done)
        return task

    async def populate(self, skill_name: str, skill_id: str, missing: int, *, total: int | None = None, difficulties: list[int] | None = None, concepts: list[str] | None = None,
                       timeout_s: float | None = None, wait_s: float | None = None, on_question=None, on_rejected=None, should_stop=None,
                       on_partial=None, on_batch=None) -> Populated:
        """Make sure ``skill_id`` gets its missing questions exactly once however many requests ask at the same time.

        The database row UNIQUE(skill_id, active) is the lock: one request generates; the others wait for that job and reuse its questions (no second model call).
        After acquiring the lock the bank is re-counted, because the previous holder may have just finished. The lease is extended after every batch."""
        wait_s = (WAIT_FOR_RUNNING_S if WAIT_FOR_RUNNING_S is not None else self.budget_s()) if wait_s is None else wait_s
        lease = self.call_timeout() + 60
        job, running = await asyncio.to_thread(self.store.acquire_generation, skill_id, WORKER_ID, missing, lease)
        if job is None:
            metrics.inc("question_generation_duplicates_prevented_total")
            deadline = time.monotonic() + wait_s
            j = await asyncio.to_thread(self.store.generation_job, running) if running else None
            while j and j["status"] == "RUNNING" and time.monotonic() < deadline:
                await asyncio.sleep(0.25)
                j = await asyncio.to_thread(self.store.generation_job, running)
            if not j or j["status"] == "RUNNING":
                return Populated(deduplicated=True, error="busy", pending=True)
            return Populated(added=j["added"], deduplicated=True, error=None if j["status"] == "COMPLETED" else "provider")
        finished = False
        try:
            if total is not None:
                have = len(await asyncio.to_thread(self.store.candidates, skill_id))
                missing = max(0, total - have)
                if missing == 0:  # someone finished between our check and the lock
                    await asyncio.to_thread(self.store.finish_generation, job, ok=True)
                    finished = True
                    metrics.inc("question_generation_jobs_total", outcome="skipped")
                    return Populated(deduplicated=True)
            prog = self._progress[skill_id] = {"state": "running", "target": missing, "added": 0}

            def beat(n: int) -> None:   # runs in this thread between batches; cheap
                prog["added"] = n
                prog["inflight"] = n
                self.store.extend_generation(job, lease)
                if on_batch:
                    on_batch(n)
            def partial(n: int) -> None:  # questions fully streamed so far (not yet validated/saved): moves the bar while a batch is in flight
                prog["inflight"] = max(prog.get("inflight", 0), n)
                if on_partial:
                    on_partial(n)
            r = await self.generate_missing_questions(skill_name, skill_id, missing, difficulties=difficulties, concepts=concepts, timeout_s=timeout_s, on_batch=beat, on_partial=partial,
                                                      on_question=on_question, on_rejected=on_rejected, should_stop=should_stop)
            await asyncio.to_thread(self.store.finish_generation, job, ok=r.error is None or r.added > 0, added=r.added, rejected=r.rejected, llm_calls=r.llm_calls,
                                    tokens_in=r.tokens_in, tokens_out=r.tokens_out, error=r.error)
            finished = True
            prog["added"] = r.added
            prog["state"] = "done" if r.error is None or r.added else "failed"
            metrics.inc("question_generation_jobs_total", outcome="ok" if r.added else (r.error or "empty"))
            return Populated(added=r.added, llm_calls=r.llm_calls, error=None if r.added else (r.error or "invalid_output"))
        finally:
            if not finished:  # cancelled (shutdown, or the learner pressed Stop) or crashed: free the lock now rather than wait for the lease to expire
                if (pp := self._progress.get(skill_id)) and pp.get("state") == "running":
                    pp["state"] = "failed"
                self.store.finish_generation(job, ok=False, error="cancelled")

    async def ensure_questions(self, skill_name: str, skill_id: str, missing: int, *, total: int | None = None, difficulties: list[int] | None = None,
                               concepts: list[str] | None = None, budget_s: float | None = None) -> Populated:
        """Start (or join) background generation for the skill and wait up to the budget. If the budget runs out the work **continues** and keeps saving batches;
        the caller gets ``pending=True`` and decides whether what is saved so far is enough. Generation never dies because one request stopped waiting."""
        budget = self.budget_s() if budget_s is None else budget_s
        existing = self._tasks.get(skill_id)
        if existing is not None and existing.get_loop() is not asyncio.get_running_loop():
            existing = None   # another loop's task cannot be awaited here; the database lock still makes this request wait for it instead of generating again
        task = existing or self._spawn(skill_id, self.populate(skill_name, skill_id, missing, total=total, difficulties=difficulties, concepts=concepts))
        try:
            return await asyncio.wait_for(asyncio.shield(task), budget)
        except asyncio.TimeoutError:
            return Populated(pending=True)
        except asyncio.CancelledError:
            if task.cancelled():   # the learner pressed Stop: the generation task was cancelled, not this request
                return Populated(error="stopped")
            raise

    async def top_up(self, skill_name: str, skill_id: str, missing: int, timeout_s: float | None = None) -> int:
        """Background top-up (Assessments shortfall): fill ``missing`` questions, coordinated, validated, persisted. Returns how many were added."""
        return (await self.populate(skill_name, skill_id, missing, timeout_s=timeout_s)).added

    # ------------------------------------------------------------------ the database-first decision
    @staticmethod
    def unavailable_message(reason: str | None, topic: str) -> str:
        """Plain-language explanation for each way preparing questions can fail (never raw provider output)."""
        return {"timeout": f"The AI service took too long to generate questions for {topic}.",
                "provider": "The AI service is unavailable right now.",
                "invalid_output": "The AI service returned questions that did not pass validation.",
                "stopped": f"Question generation for {topic} was stopped. Questions already generated are saved.",
                "busy": f"Questions for {topic} are still being prepared.",
                "preparing": f"Questions for {topic} are still being generated. A local model can take a few minutes, and what is finished is already saved."
                }.get(reason or "", "No questions are available for this topic yet.") + " Nothing is lost; please try again in a moment."

    async def prepare(self, user_id: str, skill, n: int, *, level: str | None = None, key: str | None = None, kind: str = "diagnostic", topic: str | None = None) -> Prepared:
        """Topic exists -> database. Topic missing -> generate, validate, save. Partially there -> generate only the missing ones. Then assign and persist the attempt.

        Generation runs in the background and is waited on for at most the budget: a slow model yields a reduced (clearly reported) quiz of at least
        QUESTION_MIN_USABLE saved questions while generation continues for the next learner, or a retryable "still preparing" error when fewer are ready."""
        prior = LEVEL_PRIOR.get((level or "").lower())
        av = await asyncio.to_thread(self.check_question_availability, skill.skill_id, n)
        before = av.eligible
        pop = Populated()
        if av.missing:
            sel0 = await asyncio.to_thread(self.select, user_id, skill.skill_id, min(n, av.eligible), prior, random.Random(0)) if av.eligible else Selection()
            pop = await self.ensure_questions(skill.name, skill.skill_id, av.missing, total=n, difficulties=self.identify_missing_difficulties(sel0.rows, n, prior),
                                              concepts=self.identify_missing_concepts(skill.skill_id, sel0.rows) if av.eligible else [])
        eligible_now = (await asyncio.to_thread(self.check_question_availability, skill.skill_id, n)).eligible
        gained = max(0, eligible_now - before)
        source = "question_bank" if not av.missing or gained == 0 else ("ai_generated" if before == 0 else "hybrid")
        reason = "preparing" if pop.pending and pop.error != "busy" else pop.error
        if av.missing and eligible_now < min(get_settings().QUESTION_MIN_USABLE, n):   # too few to be a meaningful quiz: say so, keep what is saved, allow retry
            why = ("busy" if pop.error == "busy" else "preparing") if pop.pending else (pop.error or "invalid_output")
            raise QuestionsUnavailable(self.unavailable_message(why, topic or skill.name), details={"reason": why, "topic": topic or skill.name, "ready": eligible_now, "requested": n})
        aid, created, sel, _ = await asyncio.to_thread(self.start, user_id, skill.skill_id, n, prior, key, kind=kind, topic=topic, source=source)
        if not sel.rows:
            raise QuestionsUnavailable(self.unavailable_message(pop.error, topic or skill.name), details={"reason": pop.error or "no_questions", "topic": topic or skill.name})
        if not created:                                    # idempotent replay: report the source the original attempt recorded
            row = await asyncio.to_thread(self.store.attempt_row, user_id, aid)
            source = (row or {}).get("generation_source") or source
        return Prepared(aid, created, sel, source, pop.llm_calls, gained, pop.deduplicated, (reason or "preparing") if sel.shortfall else None)
