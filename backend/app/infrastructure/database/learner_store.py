"""Learner-state persistence on SQLAlchemy Core. Every method takes learner_id and filters by it: there is no API to
read another learner's rows."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, insert, select, update

from app.domains.learning_intelligence.proficiency import Evidence, ProficiencyEstimate, estimate
from app.domains.roadmap_engine.models import Roadmap
from app.infrastructure.database.engine import Db
from app.infrastructure.database.session import insert_for, utc_iso
from app.infrastructure.database import tables as T


@dataclass
class Attempt:
    attempt_id: str
    learner_id: str
    skill_id: str
    question_ids: list[str]
    started_at: datetime
    status: str = "open"  # open | completed
    completed_at: datetime | None = None
    results: list[dict] = field(default_factory=list)


class LearnerStore:
    def __init__(self, db: Db):
        self.db = db

    # ---- evidence / proficiency ----
    def evidence_for(self, learner_id: str) -> list[Evidence]:
        e = T.skill_evidence
        with self.db.begin() as c:
            rows = c.execute(select(e.c.skill_id, e.c.kind, e.c.score, e.c.difficulty, e.c.n_items, e.c.occurred_at, e.c.source_id)
                             .where(e.c.learner_id == learner_id).order_by(e.c.evidence_id)).all()
        return [Evidence(**dict(r._mapping)) for r in rows]

    def estimates(self, learner_id: str, now: datetime | None = None) -> dict[str, ProficiencyEstimate]:
        now = now or datetime.now(timezone.utc)
        ev = self.evidence_for(learner_id)
        return {sid: estimate(sid, ev, now) for sid in {x.skill_id for x in ev}}

    def add_evidence(self, learner_id: str, items: list[Evidence]) -> None:
        now = datetime.now(timezone.utc)
        e, snap = T.skill_evidence, T.proficiency_snapshots
        with self.db.begin() as c:             # evidence + snapshots commit together or not at all
            for x in items:
                c.execute(insert_for(self.db, e).values(learner_id=learner_id, skill_id=x.skill_id, kind=x.kind.value, score=x.score,
                                                        difficulty=x.difficulty, n_items=x.n_items, source_id=x.source_id,
                                                        occurred_at=x.occurred_at.isoformat()).on_conflict_do_nothing())
            rows = c.execute(select(e.c.skill_id, e.c.kind, e.c.score, e.c.difficulty, e.c.n_items, e.c.occurred_at, e.c.source_id)
                             .where(e.c.learner_id == learner_id).order_by(e.c.evidence_id)).all()
            all_ev = [Evidence(**dict(r._mapping)) for r in rows]
            for sid in {x.skill_id for x in items}:
                est = estimate(sid, all_ev, now)
                c.execute(insert(snap).values(learner_id=learner_id, skill_id=sid, proficiency_score=est.proficiency_score,
                                              confidence_score=est.confidence_score, evidence_count=est.evidence_count,
                                              proficiency_version=est.proficiency_version,
                                              last_assessed_at=est.last_assessed_at.isoformat() if est.last_assessed_at else None,
                                              snapshot_at=now.isoformat()))

    def snapshots(self, learner_id: str, skill_id: str | None = None) -> list[dict]:
        s = T.proficiency_snapshots
        q = select(s).where(s.c.learner_id == learner_id)
        if skill_id:
            q = q.where(s.c.skill_id == skill_id)
        with self.db.begin() as c:
            return [dict(r._mapping) for r in c.execute(q.order_by(s.c.snapshot_id))]

    # ---- attempts ----
    def save_attempt(self, a: Attempt) -> None:
        t = T.assessment_attempts
        vals = dict(attempt_id=a.attempt_id, learner_id=a.learner_id, skill_id=a.skill_id, question_ids=json.dumps(a.question_ids), status=a.status,
                    results=json.dumps(a.results, default=str), started_at=a.started_at.isoformat(),
                    completed_at=a.completed_at.isoformat() if a.completed_at else None)
        stmt = insert_for(self.db, t).values(**vals).on_conflict_do_update(
            index_elements=["attempt_id"], set_={"status": vals["status"], "results": vals["results"], "completed_at": vals["completed_at"]})
        with self.db.begin() as c:
            c.execute(stmt)

    @staticmethod
    def _attempt(r) -> Attempt:
        m = r._mapping
        return Attempt(attempt_id=m["attempt_id"], learner_id=m["learner_id"], skill_id=m["skill_id"], question_ids=json.loads(m["question_ids"]),
                       started_at=datetime.fromisoformat(m["started_at"]), status=m["status"], results=json.loads(m["results"]),
                       completed_at=datetime.fromisoformat(m["completed_at"]) if m["completed_at"] else None)

    def get_attempt(self, learner_id: str, attempt_id: str) -> Attempt | None:
        t = T.assessment_attempts
        with self.db.begin() as c:
            r = c.execute(select(t).where(t.c.attempt_id == attempt_id, t.c.learner_id == learner_id)).first()
        return self._attempt(r) if r else None

    def list_attempts(self, learner_id: str, limit: int = 100, offset: int = 0, kind: str = "assessment") -> tuple[list[Attempt], int]:
        """Assessment history. Diagnostic Study sessions share the table but are a separate experience (kind='diagnostic'), so they are excluded here."""
        t = T.assessment_attempts
        # an attempt still being generated, or one that ended with nothing saved, is not history yet
        shown = (t.c.learner_id == learner_id) & (t.c.kind == kind) & (t.c.generation_status.is_(None) | ((t.c.generation_status != "generating") & (t.c.total_questions > 0)))
        with self.db.begin() as c:
            total = c.execute(select(func.count()).select_from(t).where(shown)).scalar_one()
            rows = c.execute(select(t).where(shown).order_by(t.c.started_at.desc()).limit(limit).offset(offset)).all()
        return [self._attempt(r) for r in rows], total

    def seen_question_ids(self, learner_id: str) -> set[str]:
        t = T.assessment_attempts
        out: set[str] = set()
        with self.db.begin() as c:
            for (q,) in c.execute(select(t.c.question_ids).where(t.c.learner_id == learner_id)):
                out.update(json.loads(q))
        return out

    # ---- roadmaps (immutable versions; same-version upsert only for in-place progress such as completion) ----
    def save_roadmap(self, rm: Roadmap) -> None:
        t = T.learning_paths
        stmt = insert_for(self.db, t).values(roadmap_id=rm.roadmap_id, version=rm.version, learner_id=rm.learner_id, body=rm.model_dump_json(),
                                             created_at=utc_iso())
        stmt = stmt.on_conflict_do_update(index_elements=["roadmap_id", "version"], set_={"body": rm.model_dump_json()})
        with self.db.begin() as c:
            c.execute(stmt)

    def roadmap_versions(self, learner_id: str, roadmap_id: str) -> list[Roadmap]:
        t = T.learning_paths
        with self.db.begin() as c:
            rows = c.execute(select(t.c.body).where(t.c.roadmap_id == roadmap_id, t.c.learner_id == learner_id).order_by(t.c.version)).all()
        return [Roadmap.model_validate_json(r[0]) for r in rows]

    def list_roadmaps(self, learner_id: str) -> list[Roadmap]:
        """Latest version of each of the learner's roadmaps, newest first."""
        t = T.learning_paths
        latest = select(t.c.roadmap_id, func.max(t.c.version).label("v")).where(t.c.learner_id == learner_id).group_by(t.c.roadmap_id).subquery()
        q = (select(t.c.body).join(latest, (t.c.roadmap_id == latest.c.roadmap_id) & (t.c.version == latest.c.v))
             .where(t.c.learner_id == learner_id).order_by(t.c.created_at.desc()))
        with self.db.begin() as c:
            return [Roadmap.model_validate_json(r[0]) for r in c.execute(q)]

    # ---- goals ----
    def add_goal(self, learner_id: str, goal: dict) -> dict:
        with self.db.begin() as c:
            c.execute(insert(T.learning_goals).values(goal_id=goal["goal_id"], learner_id=learner_id, role_slug=goal["role_slug"],
                                                      hours_per_week=goal["hours_per_week"], created_at=goal["created_at"]))
        return goal

    def list_goals(self, learner_id: str) -> list[dict]:
        g = T.learning_goals
        with self.db.begin() as c:
            rows = c.execute(select(g).where(g.c.learner_id == learner_id).order_by(g.c.created_at)).all()
        return [{k: r._mapping[k] for k in ("goal_id", "role_slug", "hours_per_week", "created_at")} for r in rows]

    # ---- tutor conversations ----
    def create_conversation(self, learner_id: str, conv_id: str, title: str, skill_id: str | None, language: str) -> None:
        now = utc_iso()
        with self.db.begin() as c:
            c.execute(insert(T.tutor_conversations).values(conversation_id=conv_id, learner_id=learner_id, title=title[:80], skill_id=skill_id,
                                                           language=language, created_at=now, updated_at=now))

    def get_conversation(self, learner_id: str, conv_id: str) -> dict | None:
        t = T.tutor_conversations
        with self.db.begin() as c:
            r = c.execute(select(t).where(t.c.conversation_id == conv_id, t.c.learner_id == learner_id)).first()
        return dict(r._mapping) if r else None

    def add_message(self, learner_id: str, conv_id: str, role: str, content: str, *, status: str = "complete", client_message_id: str | None = None) -> int:
        if not self.get_conversation(learner_id, conv_id):
            raise KeyError("conversation not found")  # ownership enforced at the storage layer too
        now = utc_iso()
        with self.db.begin() as c:
            mid = c.execute(insert(T.tutor_messages).values(conversation_id=conv_id, role=role, content=content, created_at=now, status=status,
                                                            client_message_id=client_message_id)).inserted_primary_key[0]
            c.execute(update(T.tutor_conversations).where(T.tutor_conversations.c.conversation_id == conv_id).values(updated_at=now))
        return int(mid)

    def messages(self, learner_id: str, conv_id: str, limit: int = 200) -> list[dict]:
        if not self.get_conversation(learner_id, conv_id):
            return []
        m = T.tutor_messages
        with self.db.begin() as c:
            rows = c.execute(select(m.c.role, m.c.content, m.c.created_at).where(m.c.conversation_id == conv_id, m.c.status != "superseded")
                             .order_by(m.c.message_id).limit(limit)).all()
        return [dict(r._mapping) for r in rows]
