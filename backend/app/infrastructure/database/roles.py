"""User roles (admin, reviewer, learner, rater) and the audit log (decision D1)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, insert, select

from app.infrastructure.database import tables as T
from app.infrastructure.database.engine import Db
from app.infrastructure.database.session import insert_for

ROLES = ("admin", "reviewer", "learner", "rater")


class RoleRepository:
    def __init__(self, db: Db):
        self.db = db

    def grant(self, user_id: str, role: str, granted_by: str | None = None, now: datetime | None = None) -> None:
        if role not in ROLES:
            raise ValueError(f"unknown role: {role}")
        with self.db.begin() as c:
            c.execute(insert_for(self.db, T.user_roles).values(user_id=user_id, role=role, granted_by=granted_by, granted_at=now or T.utcnow())
                      .on_conflict_do_nothing(index_elements=["user_id", "role"]))

    def revoke(self, user_id: str, role: str) -> None:
        with self.db.begin() as c:
            c.execute(delete(T.user_roles).where(T.user_roles.c.user_id == user_id, T.user_roles.c.role == role))

    def roles_of(self, user_id: str) -> set[str]:
        """Every user is at least a learner (existing accounts need no migration)."""
        with self.db.begin() as c:
            rows = c.execute(select(T.user_roles.c.role).where(T.user_roles.c.user_id == user_id)).all()
        return {r[0] for r in rows} | {"learner"}

    def audit(self, user_id: str | None, action: str, entity: str | None = None, entity_id: str | None = None, details: dict | None = None,
              now: datetime | None = None) -> None:
        with self.db.begin() as c:
            c.execute(insert(T.audit_log).values(user_id=user_id, action=action, entity=entity, entity_id=entity_id, details=details or {}, created_at=now or T.utcnow()))

    def audit_entries(self, user_id: str | None = None, limit: int = 100) -> list[dict]:
        a = T.audit_log
        q = select(a).order_by(a.c.audit_id.desc()).limit(limit)
        if user_id:
            q = q.where(a.c.user_id == user_id)
        with self.db.begin() as c:
            return [dict(r._mapping) for r in c.execute(q)]
