"""Generation jobs (with leases), the durable event log and the usage ledger.

Concurrency rules (identical on SQLite and PostgreSQL):
* ``generation_key`` is UNIQUE: concurrent identical requests create exactly one job.
* ``claim`` is a single compare-and-set UPDATE, so exactly one worker wins a job; an expired lease can be taken over.
* Event ids are monotonic per database; clients resume with ``events_after(job_id, last_id)``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import and_, func, insert, or_, select, update

from app.domains.content_engine.models import ACTIVE_STATES, GenerationEvent, Job
from app.infrastructure.database import tables as T
from app.infrastructure.database.engine import Db
from app.infrastructure.database.session import insert_for

J, E, L = T.generation_jobs, T.generation_events, T.usage_ledger


def _now(now):
    return now or T.utcnow()


def _job(r) -> Job:
    m = r._mapping
    return Job(**{k: m[k] for k in ("job_id", "generation_key", "course_id", "section_id", "status", "requested_by", "force", "attempts", "lease_owner",
                                    "lease_expires_at", "heartbeat_at", "error", "version_id", "created_at", "updated_at", "started_at", "completed_at")})


class JobRepository:
    def __init__(self, db: Db):
        self.db = db

    def create_or_get(self, generation_key: str, course_id: str, section_id: str, requested_by: str | None, force: bool = False,
                      now: datetime | None = None) -> tuple[Job, bool]:
        """Returns (job, created). Exactly one caller sees created=True for a given key, however many race."""
        now = _now(now)
        with self.db.begin() as c:
            res = c.execute(insert_for(self.db, J).values(job_id=str(uuid.uuid4()), generation_key=generation_key, course_id=course_id, section_id=section_id,
                                                          status="QUEUED", requested_by=requested_by, force=force, attempts=0, created_at=now, updated_at=now)
                            .on_conflict_do_nothing(index_elements=["generation_key"]).returning(J.c.job_id))
            # RETURNING is the portable way to learn whether *this* statement inserted the row (rowcount is -1 for
            # INSERT .. ON CONFLICT on PostgreSQL with psycopg 3)
            created = res.first() is not None
            row = c.execute(select(J).where(J.c.generation_key == generation_key)).first()
        return _job(row), created

    def get(self, job_id: str) -> Job | None:
        with self.db.begin() as c:
            r = c.execute(select(J).where(J.c.job_id == job_id)).first()
        return _job(r) if r else None

    def requeue_if_terminal(self, job_id: str, now: datetime | None = None) -> bool:
        """FAILED/CANCELLED jobs may be retried; exactly one caller wins the transition back to QUEUED."""
        with self.db.begin() as c:
            res = c.execute(update(J).where(J.c.job_id == job_id, J.c.status.in_(("FAILED", "CANCELLED")))
                            .values(status="QUEUED", error=None, lease_owner=None, lease_expires_at=None, completed_at=None, updated_at=_now(now)))
        return res.rowcount == 1

    def claim(self, job_id: str, owner: str, lease_s: float = 60, now: datetime | None = None) -> bool:
        """Atomically take a QUEUED job, or one whose lease has expired. Returns True for exactly one claimant."""
        now = _now(now)
        free = or_(J.c.status == "QUEUED", and_(J.c.status.in_(("GENERATING", "VALIDATING")), J.c.lease_expires_at < now))
        with self.db.begin() as c:
            res = c.execute(update(J).where(J.c.job_id == job_id, free).values(
                status="GENERATING", lease_owner=owner, lease_expires_at=now + timedelta(seconds=lease_s), heartbeat_at=now,
                attempts=J.c.attempts + 1, started_at=func.coalesce(J.c.started_at, now), updated_at=now))
        return res.rowcount == 1

    def heartbeat(self, job_id: str, owner: str, lease_s: float = 60, now: datetime | None = None) -> bool:
        """Extends the lease only for the current owner (a worker that lost its lease learns it here)."""
        now = _now(now)
        with self.db.begin() as c:
            res = c.execute(update(J).where(J.c.job_id == job_id, J.c.lease_owner == owner, J.c.status.in_(("GENERATING", "VALIDATING")), J.c.lease_expires_at >= now)
                            .values(heartbeat_at=now, lease_expires_at=now + timedelta(seconds=lease_s), updated_at=now))
        return res.rowcount == 1

    def set_status(self, job_id: str, status: str, *, owner: str | None = None, error: str | None = None, version_id: str | None = None,
                   now: datetime | None = None) -> bool:
        """Owner-checked status change; terminal states clear the lease."""
        now = _now(now)
        vals: dict = {"status": status, "updated_at": now}
        if status in ("COMPLETED", "FAILED", "CANCELLED"):
            vals.update(lease_owner=None, lease_expires_at=None, completed_at=now)
        if error is not None:
            vals["error"] = error[:2000]
        if version_id:
            vals["version_id"] = version_id
        cond = [J.c.job_id == job_id] + ([J.c.lease_owner == owner] if owner else [])
        with self.db.begin() as c:
            return c.execute(update(J).where(*cond).values(**vals)).rowcount == 1

    def expired(self, now: datetime | None = None) -> list[Job]:
        now = _now(now)
        with self.db.begin() as c:
            rows = c.execute(select(J).where(J.c.status.in_(("GENERATING", "VALIDATING")), J.c.lease_expires_at < now)).all()
        return [_job(r) for r in rows]

    def active_for(self, course_id: str) -> list[Job]:
        with self.db.begin() as c:
            rows = c.execute(select(J).where(J.c.course_id == course_id, J.c.status.in_(ACTIVE_STATES))).all()
        return [_job(r) for r in rows]

    def latest_for_section(self, course_id: str, section_id: str) -> Job | None:
        with self.db.begin() as c:
            r = c.execute(select(J).where(J.c.course_id == course_id, J.c.section_id == section_id).order_by(J.c.created_at.desc()).limit(1)).first()
        return _job(r) if r else None

    # ---- events --------------------------------------------------------------------------------
    def append_event(self, job_id: str, type_: str, data: dict | None = None, now: datetime | None = None) -> int:
        with self.db.begin() as c:
            res = c.execute(insert(E).values(job_id=job_id, type=type_, data=data or {}, created_at=_now(now)))
            return int(res.inserted_primary_key[0])

    def events_after(self, job_id: str, after_id: int = 0, limit: int = 500) -> list[GenerationEvent]:
        with self.db.begin() as c:
            rows = c.execute(select(E).where(E.c.job_id == job_id, E.c.event_id > after_id).order_by(E.c.event_id).limit(limit)).all()
        return [GenerationEvent(r.event_id, r.job_id, r.type, r.data, r.created_at) for r in rows]

    # ---- usage ledger --------------------------------------------------------------------------
    def record_usage(self, *, job_id: str | None, course_id: str | None, section_id: str | None, provider: str, model: str, tokens_in: int, tokens_out: int,
                     latency_ms: float, outcome: str = "ok", cost_estimate: float = 0.0, now: datetime | None = None) -> None:
        with self.db.begin() as c:
            c.execute(insert(L).values(job_id=job_id, course_id=course_id, section_id=section_id, provider=provider, model=model, tokens_in=tokens_in,
                                       tokens_out=tokens_out, latency_ms=latency_ms, cost_estimate=cost_estimate, outcome=outcome, created_at=_now(now)))

    def usage_totals(self, course_id: str | None = None, section_id: str | None = None) -> dict:
        q = select(func.count(), func.coalesce(func.sum(L.c.tokens_in), 0), func.coalesce(func.sum(L.c.tokens_out), 0), func.coalesce(func.sum(L.c.cost_estimate), 0.0))
        if course_id:
            q = q.where(L.c.course_id == course_id)
        if section_id:
            q = q.where(L.c.section_id == section_id)
        with self.db.begin() as c:
            n, tin, tout, cost = c.execute(q).one()
        return {"calls": int(n), "tokens_in": int(tin), "tokens_out": int(tout), "cost_estimate": float(cost)}
