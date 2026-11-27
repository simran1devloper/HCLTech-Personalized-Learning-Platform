from __future__ import annotations

import json

from sqlalchemy import delete, func, insert, or_, select, update

from app.domains.skill_registry.models import Dependency, Skill
from app.infrastructure.database.engine import Db
from app.infrastructure.database.session import insert_for
from app.infrastructure.database.tables import skill_dependencies as deps
from app.infrastructure.database.tables import skills as t

_COLS = ("skill_id", "slug", "name", "description", "category", "domain", "difficulty", "estimated_hours",
         "aliases", "parent_skill_id", "status", "created_at", "updated_at")


def _row(r) -> Skill:
    d = dict(r._mapping)
    d["aliases"] = json.loads(d["aliases"])
    return Skill(**d)


def _vals(s: Skill) -> dict:
    d = s.model_dump(mode="json")
    d["aliases"] = json.dumps(d["aliases"])
    return {c: d[c] for c in _COLS}


class SqlSkillRepository:
    """Skill registry persistence (SQLite and PostgreSQL through SQLAlchemy Core)."""

    def __init__(self, db: Db):
        self.db = db

    def get(self, skill_id):
        with self.db.begin() as c:
            r = c.execute(select(t).where(t.c.skill_id == skill_id)).first()
        return _row(r) if r else None

    def get_by_slug(self, slug):
        with self.db.begin() as c:
            r = c.execute(select(t).where(t.c.slug == slug)).first()
        return _row(r) if r else None

    def list(self, *, category=None, status=None, q=None, offset=0, limit=50):
        conds = []
        if category:
            conds.append(t.c.category == category)
        if status:
            conds.append(t.c.status == status)
        if q:
            like = f"%{q.lower().replace('%', '').replace('_', '')}%"
            conds.append(or_(func.lower(t.c.name).like(like), func.lower(t.c.aliases).like(like)))
        with self.db.begin() as c:
            total = c.execute(select(func.count()).select_from(t).where(*conds)).scalar_one()
            rows = c.execute(select(t).where(*conds).order_by(t.c.slug).limit(limit).offset(offset)).all()
        return [_row(r) for r in rows], total

    def add(self, skill):
        with self.db.begin() as c:
            c.execute(insert(t).values(**_vals(skill)))
        return skill

    def update(self, skill):
        v = _vals(skill)
        with self.db.begin() as c:
            c.execute(update(t).where(t.c.skill_id == skill.skill_id).values({k: x for k, x in v.items() if k != "skill_id"}))
        return skill

    def list_dependencies(self):
        with self.db.begin() as c:
            return [Dependency(**dict(r._mapping)) for r in c.execute(select(deps))]

    def add_dependency(self, dep):
        stmt = insert_for(self.db, deps).values(skill_id=dep.skill_id, prerequisite_id=dep.prerequisite_id, min_proficiency=dep.min_proficiency)
        stmt = stmt.on_conflict_do_update(index_elements=["skill_id", "prerequisite_id"], set_={"min_proficiency": dep.min_proficiency})
        with self.db.begin() as c:
            c.execute(stmt)

    def remove_dependency(self, skill_id, prerequisite_id):
        with self.db.begin() as c:
            c.execute(delete(deps).where(deps.c.skill_id == skill_id, deps.c.prerequisite_id == prerequisite_id))
