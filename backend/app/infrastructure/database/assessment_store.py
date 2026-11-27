"""Persistence for the assessment question bank and for assessment attempts (SQLAlchemy Core; SQLite and PostgreSQL).

* The bank is the canonical question store; selection reads only *light* columns, full rows (answers, explanations) are fetched for the chosen few.
* An attempt stores a **snapshot** of every assigned question, so history stays reviewable after the bank changes, and grading never depends on the bank.
* Attempt creation is one transaction and idempotent per (learner, idempotency_key); submission is a compare-and-set, so exactly one caller wins.
* No model is called from this module."""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.infrastructure.database import tables as T
from app.infrastructure.database.engine import Db
from app.infrastructure.database.session import insert_for

B, A, AQ, CR, GJ, GE = (T.assessment_question_bank, T.assessment_attempts, T.assessment_attempt_questions, T.assessment_concept_results, T.question_generation_jobs,
                        T.assessment_generation_events)
LIGHT = [B.c.id, B.c.concept, B.c.subtopic, B.c.difficulty, B.c.question_type, B.c.cognitive_level, B.c.estimated_time_seconds, B.c.quality_score]


def _now(now):
    return now or T.utcnow()


class AssessmentStore:
    def __init__(self, db: Db):
        self.db = db

    # ------------------------------------------------------------------------------------------- bank: writes
    def insert_question(self, skill_id: str, q: dict, *, content_hash: str, source_type: str, validation_status: str, quality: float, question_id: str | None = None,
                        now: datetime | None = None) -> tuple[str | None, bool]:
        """Insert-or-ignore on (skill_id, content_hash). Returns (id, created); id is the existing row's id when it was a duplicate."""
        now, qid = _now(now), question_id or str(uuid.uuid4())
        with self.db.begin() as c:
            res = c.execute(insert_for(self.db, B).values(
                id=qid, skill_id=skill_id, topic=(q.get("topic") or q["concept"])[:120], subtopic=q["subtopic"][:120], concept=q["concept"][:120], question_text=q["question_text"],
                question_type=q["question_type"], difficulty=q["difficulty"], options=q["options"], correct_answer=q["correct"], explanation=q["explanation"],
                solution_approach=q.get("solution_approach") or "", practical_example=q.get("practical_example") or "", code_snippet=q.get("code_snippet"),
                code_language=q.get("code_language"), tags=q.get("tags") or [], cognitive_level=q["cognitive_level"], estimated_time_seconds=q.get("estimated_time_seconds") or 60,
                source_type=source_type, validation_status=validation_status, quality_score=quality, version=1, is_active=validation_status == "validated",
                content_hash=content_hash, created_at=now, updated_at=now).on_conflict_do_nothing(index_elements=["skill_id", "content_hash"]).returning(B.c.id))
            created = res.first() is not None
            existing = None if created else c.execute(select(B.c.id).where(B.c.skill_id == skill_id, B.c.content_hash == content_hash)).scalar_one_or_none()
        return (qid if created else existing), created

    def set_status(self, question_id: str, *, validation_status: str | None = None, is_active: bool | None = None, now: datetime | None = None) -> bool:
        vals: dict[str, Any] = {"updated_at": _now(now)}
        if validation_status:
            vals["validation_status"] = validation_status
            vals["is_active"] = validation_status == "validated" if is_active is None else is_active
        elif is_active is not None:
            vals["is_active"] = is_active
        with self.db.begin() as c:
            return c.execute(update(B).where(B.c.id == question_id).values(**vals)).rowcount == 1

    def count_by_source(self, source_type: str) -> int:
        with self.db.begin() as c:
            return c.execute(select(func.count()).select_from(B).where(B.c.source_type == source_type)).scalar_one()

    # ------------------------------------------------------------------------------------------- bank: reads
    def candidates(self, skill_id: str) -> list[dict]:
        """Active, validated questions for a skill: light columns only (no options, answers or explanations)."""
        with self.db.begin() as c:
            rows = c.execute(select(*LIGHT).where(B.c.skill_id == skill_id, B.c.is_active.is_(True), B.c.validation_status == "validated")).all()
        return [dict(r._mapping) for r in rows]

    def full(self, ids: list[str]) -> dict[str, dict]:
        if not ids:
            return {}
        with self.db.begin() as c:
            rows = c.execute(select(B).where(B.c.id.in_(ids))).all()
        return {r.id: dict(r._mapping) for r in rows}

    def availability(self) -> dict[str, dict]:
        """skill_id -> {total, concepts, by_difficulty, minutes} over active validated questions, in one grouped query."""
        with self.db.begin() as c:
            rows = c.execute(select(B.c.skill_id, B.c.difficulty, func.count(), func.count(func.distinct(B.c.concept)), func.sum(B.c.estimated_time_seconds))
                             .where(B.c.is_active.is_(True), B.c.validation_status == "validated").group_by(B.c.skill_id, B.c.difficulty)).all()
            concepts = dict(c.execute(select(B.c.skill_id, func.count(func.distinct(B.c.concept))).where(B.c.is_active.is_(True), B.c.validation_status == "validated")
                                      .group_by(B.c.skill_id)).all())
        out: dict[str, dict] = {}
        for sid, diff, n, _k, secs in rows:
            d = out.setdefault(sid, {"total": 0, "concepts": concepts.get(sid, 0), "by_difficulty": {}, "avg_seconds": 0, "_secs": 0})
            d["total"] += n
            d["by_difficulty"][int(diff)] = n
            d["_secs"] += int(secs or 0)
        for d in out.values():
            d["avg_seconds"] = round(d.pop("_secs") / max(1, d["total"]))
        return out

    def topics(self, skill_id: str) -> list[dict]:
        with self.db.begin() as c:
            rows = c.execute(select(B.c.concept, func.count(), func.min(B.c.difficulty), func.max(B.c.difficulty)).where(
                B.c.skill_id == skill_id, B.c.is_active.is_(True), B.c.validation_status == "validated").group_by(B.c.concept).order_by(B.c.concept)).all()
        return [{"concept": r[0], "questions": r[1], "min_difficulty": r[2], "max_difficulty": r[3]} for r in rows]

    def recently_assigned(self, user_id: str, skill_id: str, last_attempts: int = 3) -> set[str]:
        """Question ids shown to this user in their most recent attempts for the skill (used to avoid immediate repeats)."""
        with self.db.begin() as c:
            att = [a for (a,) in c.execute(select(A.c.attempt_id).where(A.c.learner_id == user_id, A.c.skill_id == skill_id).order_by(A.c.started_at.desc()).limit(last_attempts))]
            if not att:
                return set()
            return {q for (q,) in c.execute(select(AQ.c.question_id).where(AQ.c.attempt_id.in_(att)))}

    # ------------------------------------------------------------------------------------------- attempts
    def create_attempt(self, *, attempt_id: str, user_id: str, skill_id: str, questions: list[dict], difficulty_level: str, idempotency_key: str | None,
                       kind: str = "assessment", topic: str | None = None, generation_source: str | None = None, requested: int | None = None,
                       now: datetime | None = None) -> tuple[str, bool]:
        """One transaction: attempt row + one assignment row per question. Returns (attempt_id, created).
        A repeated (user, idempotency_key) returns the original attempt and writes nothing.
        ``requested`` starts the generation lifecycle: the attempt is created with whatever the bank supplied (possibly nothing) and filled by ``append_question``."""
        now = _now(now)
        gen = {"requested_questions": requested, "generation_status": "generating", "generation_updated_at": now} if requested is not None else {}
        with self.db.begin() as c:
            res = c.execute(insert_for(self.db, A).values(
                attempt_id=attempt_id, learner_id=user_id, skill_id=skill_id, question_ids=json.dumps([q["question_id"] for q in questions]), status="open", results="[]",
                started_at=now.isoformat(), total_questions=len(questions), answered_questions=0, correct_answers=0, difficulty_level=difficulty_level, created_at=now,
                idempotency_key=idempotency_key, kind=kind, topic=topic, generation_source=generation_source, **gen).on_conflict_do_nothing(index_elements=["learner_id", "idempotency_key"]).returning(A.c.attempt_id))
            if res.first() is None:  # idempotent replay
                existing = c.execute(select(A.c.attempt_id).where(A.c.learner_id == user_id, A.c.idempotency_key == idempotency_key)).scalar_one()
                return existing, False
            for i, q in enumerate(questions):
                c.execute(insert(AQ).values(id=str(uuid.uuid4()), attempt_id=attempt_id, question_id=q["question_id"], question_snapshot=q, displayed_order=i))
        return attempt_id, True

    # ------------------------------------------------------------------------------------------- generation lifecycle (persistent; any process can serve it)
    def append_question(self, attempt_id: str, snap: dict, limit: int, now: datetime | None = None) -> int | None:
        """Assign one validated question to a generating attempt, atomically. Returns the new assigned count, or None when it is a duplicate or the attempt is already full."""
        now = _now(now)
        with self.db.begin() as c:
            rows = [q for (q,) in c.execute(select(AQ.c.question_id).where(AQ.c.attempt_id == attempt_id).order_by(AQ.c.displayed_order))]
            if snap["question_id"] in rows or len(rows) >= limit:
                return None
            c.execute(insert(AQ).values(id=str(uuid.uuid4()), attempt_id=attempt_id, question_id=snap["question_id"], question_snapshot=snap, displayed_order=len(rows)))
            rows.append(snap["question_id"])
            c.execute(update(A).where(A.c.attempt_id == attempt_id).values(total_questions=len(rows), question_ids=json.dumps(rows), generation_updated_at=now))
        return len(rows)

    def add_event(self, attempt_id: str, type_: str, payload: dict, now: datetime | None = None) -> int:
        """Append an event with the next sequence number (the primary key (attempt_id, seq) makes concurrent writers retry instead of colliding)."""
        now = _now(now)
        for _ in range(5):
            try:
                with self.db.begin() as c:
                    seq = (c.execute(select(func.max(GE.c.seq)).where(GE.c.attempt_id == attempt_id)).scalar_one() or 0) + 1
                    c.execute(insert(GE).values(attempt_id=attempt_id, seq=seq, type=type_, payload={**payload, "seq": seq}, created_at=now))
                    c.execute(update(A).where(A.c.attempt_id == attempt_id).values(generation_updated_at=now))
                return seq
            except IntegrityError:
                continue
        raise RuntimeError("could not append generation event")

    def events_after(self, attempt_id: str, after: int = 0, limit: int = 500) -> list[dict]:
        with self.db.begin() as c:
            rows = c.execute(select(GE.c.seq, GE.c.type, GE.c.payload).where(GE.c.attempt_id == attempt_id, GE.c.seq > after).order_by(GE.c.seq).limit(limit)).all()
        return [{"seq": r.seq, "type": r.type, "payload": r.payload} for r in rows]

    def set_generation(self, attempt_id: str, *, status: str | None = None, error: str | None = None, difficulty_level: str | None = None, now: datetime | None = None) -> None:
        vals: dict[str, Any] = {"generation_updated_at": _now(now)}
        if status:
            vals["generation_status"] = status
        if error is not None:
            vals["generation_error"] = error[:200]
        if difficulty_level:
            vals["difficulty_level"] = difficulty_level
        with self.db.begin() as c:
            c.execute(update(A).where(A.c.attempt_id == attempt_id).values(**vals))

    def finish_generation_if_active(self, attempt_id: str, status: str, error: str | None = None) -> bool:
        """Compare-and-set generating -> terminal state: exactly one writer (runner, reaper or cancel endpoint) wins."""
        with self.db.begin() as c:
            return c.execute(update(A).where(A.c.attempt_id == attempt_id, A.c.generation_status == "generating").values(
                generation_status=status, generation_error=(error or None) and error[:200], generation_updated_at=T.utcnow())).rowcount == 1

    def question_count(self, attempt_id: str) -> int:
        with self.db.begin() as c:
            return int(c.execute(select(func.count()).select_from(AQ).where(AQ.c.attempt_id == attempt_id)).scalar_one())

    def last_seq(self, attempt_id: str) -> int:
        with self.db.begin() as c:
            return int(c.execute(select(func.max(GE.c.seq)).where(GE.c.attempt_id == attempt_id)).scalar_one() or 0)

    def attempt_by_id(self, attempt_id: str) -> dict | None:
        """Internal (runner / reaper): no ownership check. Routes use ``attempt_row`` with the user."""
        with self.db.begin() as c:
            r = c.execute(select(A).where(A.c.attempt_id == attempt_id)).first()
        return dict(r._mapping) if r else None

    def request_cancel(self, user_id: str, attempt_id: str) -> bool:
        with self.db.begin() as c:
            return c.execute(update(A).where(A.c.attempt_id == attempt_id, A.c.learner_id == user_id, A.c.generation_status == "generating").values(cancel_requested=True)).rowcount == 1

    def active_generation(self, user_id: str, skill_id: str | None = None) -> dict | None:
        q = select(A).where(A.c.learner_id == user_id, A.c.generation_status == "generating", A.c.kind == "assessment")
        if skill_id:
            q = q.where(A.c.skill_id == skill_id)
        with self.db.begin() as c:
            r = c.execute(q.order_by(A.c.started_at.desc()).limit(1)).first()
        return dict(r._mapping) if r else None

    def attempt_row(self, user_id: str, attempt_id: str) -> dict | None:
        with self.db.begin() as c:
            r = c.execute(select(A).where(A.c.attempt_id == attempt_id, A.c.learner_id == user_id)).first()
        return dict(r._mapping) if r else None

    def attempt_by_key(self, user_id: str, key: str) -> dict | None:
        with self.db.begin() as c:
            r = c.execute(select(A).where(A.c.learner_id == user_id, A.c.idempotency_key == key)).first()
        return dict(r._mapping) if r else None

    def attempt_questions(self, attempt_id: str) -> list[dict]:
        with self.db.begin() as c:
            rows = c.execute(select(AQ).where(AQ.c.attempt_id == attempt_id).order_by(AQ.c.displayed_order)).all()
        return [dict(r._mapping) for r in rows]

    def save_answers(self, user_id: str, attempt_id: str, answers: dict[str, Any], times: dict[str, int] | None = None, now: datetime | None = None) -> dict | None:
        """Autosave while the attempt is open. Returns {'answered': n} or None (not yours); raises KeyError('closed'|unknown ids) for invalid use."""
        now, times = _now(now), times or {}
        with self.db.begin() as c:
            a = c.execute(select(A.c.status).where(A.c.attempt_id == attempt_id, A.c.learner_id == user_id)).first()
            if not a:
                return None
            if a.status != "open":
                raise KeyError("closed")
            ids = {q for (q,) in c.execute(select(AQ.c.question_id).where(AQ.c.attempt_id == attempt_id))}
            if set(answers) - ids:
                raise KeyError("unknown")
            for qid, val in answers.items():
                vals = {"selected_answer": val, "answered_at": now}
                if qid in times:
                    vals["time_taken_seconds"] = max(0, min(int(times[qid]), 36000))
                c.execute(update(AQ).where(AQ.c.attempt_id == attempt_id, AQ.c.question_id == qid).values(**vals))
            n = c.execute(select(func.count()).select_from(AQ).where(AQ.c.attempt_id == attempt_id, AQ.c.selected_answer.is_not(None))).scalar_one()
            c.execute(update(A).where(A.c.attempt_id == attempt_id).values(answered_questions=n))
        return {"answered": int(n)}

    def clear_answer(self, user_id: str, attempt_id: str, question_id: str) -> None:
        with self.db.begin() as c:
            if c.execute(select(A.c.attempt_id).where(A.c.attempt_id == attempt_id, A.c.learner_id == user_id, A.c.status == "open")).first():
                c.execute(update(AQ).where(AQ.c.attempt_id == attempt_id, AQ.c.question_id == question_id).values(selected_answer=None, answered_at=None))

    def finalize(self, *, user_id: str, attempt_id: str, grades: dict[str, dict], concept_rows: list[dict], score: float, correct: float, answered: int,
                 legacy_results: list[dict], now: datetime | None = None) -> bool:
        """Compare-and-set open -> completed. Only the winning caller writes results (and gets True); a duplicate submission changes nothing."""
        now = _now(now)
        with self.db.begin() as c:
            won = c.execute(update(A).where(A.c.attempt_id == attempt_id, A.c.learner_id == user_id, A.c.status == "open").values(
                status="completed", completed_at=now.isoformat(), submitted_at=now, results=json.dumps(legacy_results, default=str), score=score,
                correct_answers=int(round(correct)), answered_questions=answered)).rowcount == 1
            if not won:
                return False
            for qid, g in grades.items():
                c.execute(update(AQ).where(AQ.c.attempt_id == attempt_id, AQ.c.question_id == qid).values(is_correct=g["is_correct"], earned=g["earned"], submitted_at=now))
            for r in concept_rows:
                c.execute(insert(CR).values(id=str(uuid.uuid4()), attempt_id=attempt_id, user_id=user_id, created_at=now, **r))
        return True

    def concept_results(self, attempt_id: str) -> list[dict]:
        with self.db.begin() as c:
            rows = c.execute(select(CR).where(CR.c.attempt_id == attempt_id).order_by(CR.c.accuracy, CR.c.concept)).all()
        return [dict(r._mapping) for r in rows]

    def concept_history(self, user_id: str, skill_id: str, limit: int = 200) -> list[dict]:
        with self.db.begin() as c:
            rows = c.execute(select(CR).where(CR.c.user_id == user_id, CR.c.skill_id == skill_id).order_by(CR.c.created_at.desc()).limit(limit)).all()
        return [dict(r._mapping) for r in rows]

    def link_learning_session(self, user_id: str, attempt_id: str, session_id: str) -> bool:
        """Set once (compare-and-set on NULL) so concurrent 'generate plan' clicks create one linked session."""
        with self.db.begin() as c:
            return c.execute(update(A).where(A.c.attempt_id == attempt_id, A.c.learner_id == user_id, A.c.learning_session_id.is_(None))
                             .values(learning_session_id=session_id)).rowcount == 1

    # ------------------------------------------------------------------------------------------- diagnostic sessions
    def list_sessions(self, user_id: str, kind: str = "diagnostic", limit: int = 20, offset: int = 0) -> tuple[list[dict], int]:
        with self.db.begin() as c:
            total = c.execute(select(func.count()).select_from(A).where(A.c.learner_id == user_id, A.c.kind == kind)).scalar_one()
            rows = c.execute(select(A.c.attempt_id, A.c.skill_id, A.c.topic, A.c.status, A.c.total_questions, A.c.answered_questions, A.c.correct_answers, A.c.score,
                                    A.c.generation_source, A.c.started_at, A.c.completed_at, A.c.learning_session_id).where(A.c.learner_id == user_id, A.c.kind == kind)
                             .order_by(A.c.started_at.desc()).limit(limit).offset(offset)).all()
        return [dict(r._mapping) for r in rows], int(total)

    # ------------------------------------------------------------------------------------------- population jobs (the generation lock)
    def acquire_generation(self, skill_id: str, worker: str, requested: int, lease_s: float = 90, now: datetime | None = None) -> tuple[str | None, str | None]:
        """Try to become the one populator of ``skill_id``. Returns (job_id, None) when acquired, or (None, running_job_id) when another request holds it.
        UNIQUE(skill_id, active) makes this atomic across processes; an expired lease (crashed worker) is failed and taken over."""
        from datetime import timedelta
        now = _now(now)
        for _attempt in range(2):
            with self.db.begin() as c:
                jid = str(uuid.uuid4())
                got = c.execute(insert_for(self.db, GJ).values(id=jid, skill_id=skill_id, status="RUNNING", active=1, worker_id=worker, lease_expires_at=now + timedelta(seconds=lease_s),
                                                               requested=requested, created_at=now).on_conflict_do_nothing(index_elements=["skill_id", "active"]).returning(GJ.c.id)).first()
                if got:
                    return jid, None
                running = c.execute(select(GJ.c.id, GJ.c.lease_expires_at).where(GJ.c.skill_id == skill_id, GJ.c.active == 1)).first()
                if running and running.lease_expires_at is not None and running.lease_expires_at < now:   # stale: its worker died
                    c.execute(update(GJ).where(GJ.c.id == running.id, GJ.c.active == 1).values(status="FAILED", active=None, error="lease expired", completed_at=now))
                    continue
                if running:
                    return None, running.id
        return None, None

    def extend_generation(self, job_id: str, lease_s: float, now: datetime | None = None) -> bool:
        """Heartbeat: a long, batched generation keeps its lock instead of looking like a crashed worker."""
        from datetime import timedelta
        now = _now(now)
        with self.db.begin() as c:
            return c.execute(update(GJ).where(GJ.c.id == job_id, GJ.c.active == 1).values(lease_expires_at=now + timedelta(seconds=lease_s))).rowcount == 1

    def finish_generation(self, job_id: str, *, ok: bool, added: int = 0, rejected: int = 0, llm_calls: int = 0, tokens_in: int = 0, tokens_out: int = 0,
                          error: str | None = None, now: datetime | None = None) -> None:
        with self.db.begin() as c:
            c.execute(update(GJ).where(GJ.c.id == job_id).values(status="COMPLETED" if ok else "FAILED", active=None, added=added, rejected=rejected, llm_calls=llm_calls,
                                                                 tokens_in=tokens_in, tokens_out=tokens_out, error=(error or None) and error[:200], completed_at=_now(now)))

    def generation_job(self, job_id: str) -> dict | None:
        with self.db.begin() as c:
            r = c.execute(select(GJ).where(GJ.c.id == job_id)).first()
        return dict(r._mapping) if r else None

    def generation_stats(self) -> dict:
        """Totals for monitoring: jobs, model calls, tokens and questions added across all topic-population jobs."""
        with self.db.begin() as c:
            r = c.execute(select(func.count(), func.coalesce(func.sum(GJ.c.llm_calls), 0), func.coalesce(func.sum(GJ.c.tokens_in), 0), func.coalesce(func.sum(GJ.c.tokens_out), 0),
                                 func.coalesce(func.sum(GJ.c.added), 0), func.count(func.distinct(GJ.c.skill_id))).where(GJ.c.status == "COMPLETED")).one()
        return {"jobs": int(r[0]), "llm_calls": int(r[1]), "tokens_in": int(r[2]), "tokens_out": int(r[3]), "questions_added": int(r[4]), "topics_populated": int(r[5])}
