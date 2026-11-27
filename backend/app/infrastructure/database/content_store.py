"""Canonical content storage: topics, courses, sections, versions and blocks (SQLAlchemy Core, SQLite and PostgreSQL)."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime

from sqlalchemy import func, insert, select, update

from app.domains.content_engine.contracts import SECTION_IDS
from app.domains.content_engine.models import ContentVersion, Course, CourseSpec, SectionState
from app.domains.content_engine.versioning import Versions
from app.infrastructure.database import tables as T
from app.infrastructure.database.engine import Db
from app.infrastructure.database.session import insert_for


def course_key(spec: CourseSpec) -> str:
    return hashlib.sha256(json.dumps(spec.normalized(), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _version(r, blocks: list[dict] | None = None) -> ContentVersion:
    m = r._mapping
    return ContentVersion(m["version_id"], m["course_id"], m["section_id"], m["content_version"], m["schema_version"], m["prompt_version"], m["model"],
                          m["provider"], m["status"], m["quality_status"], m["payload"], m["tokens_in"], m["tokens_out"], m["created_at"], m["completed_at"], blocks or [])


class ContentStore:
    def __init__(self, db: Db, section_ids: tuple[str, ...] = SECTION_IDS):
        self.db, self.section_ids = db, section_ids

    # ---- courses -------------------------------------------------------------------------------
    def ensure_course(self, spec: CourseSpec, created_by: str | None = None, now: datetime | None = None) -> Course:
        """Create-or-get by identity. Safe under concurrent calls (unique course_key / topic_norm)."""
        from app.infrastructure.database.tables import utcnow
        now = now or utcnow()
        n, key = spec.normalized(), course_key(spec)
        with self.db.begin() as c:
            c.execute(insert_for(self.db, T.topics).values(topic_id=str(uuid.uuid4()), topic_norm=n["topic"], display=spec.topic.strip()[:200], domain=n["domain"],
                                                           created_at=now).on_conflict_do_nothing(index_elements=["topic_norm"]))
            topic_id = c.execute(select(T.topics.c.topic_id).where(T.topics.c.topic_norm == n["topic"])).scalar_one()
            c.execute(insert_for(self.db, T.courses).values(course_id=str(uuid.uuid4()), course_key=key, topic_id=topic_id, level=n["level"], audience=n["audience"],
                                                            goal=n["goal"], language=n["language"], title=f"{spec.topic.strip()[:150]} ({n['level']})",
                                                            created_by=created_by, created_at=now, updated_at=now).on_conflict_do_nothing(index_elements=["course_key"]))
            course = self._course(c, T.courses.c.course_key == key)
            for sid in self.section_ids:
                c.execute(insert_for(self.db, T.sections).values(course_id=course.course_id, section_id=sid, status="NOT_STARTED", updated_at=now)
                          .on_conflict_do_nothing(index_elements=["course_id", "section_id"]))
        return course

    def _course(self, c, where) -> Course | None:
        co, to = T.courses, T.topics
        r = c.execute(select(co, to.c.display, to.c.domain).join(to, to.c.topic_id == co.c.topic_id).where(where)).first()
        if not r:
            return None
        m = r._mapping
        return Course(m["course_id"], m["course_key"], m["topic_id"], m["display"], m["level"], m["domain"], m["audience"], m["goal"], m["language"], m["title"],
                      m["created_by"], m["created_at"])

    def get_course(self, course_id: str) -> Course | None:
        with self.db.begin() as c:
            return self._course(c, T.courses.c.course_id == course_id)

    # ---- sections ------------------------------------------------------------------------------
    def list_sections(self, course_id: str) -> list[SectionState]:
        s = T.sections
        with self.db.begin() as c:
            rows = c.execute(select(s).where(s.c.course_id == course_id)).all()
        order = {sid: i for i, sid in enumerate(self.section_ids)}
        return sorted((SectionState(r.course_id, r.section_id, r.status, r.current_version, r.updated_at) for r in rows), key=lambda x: order.get(x.section_id, 99))

    def get_section(self, course_id: str, section_id: str) -> SectionState | None:
        s = T.sections
        with self.db.begin() as c:
            r = c.execute(select(s).where(s.c.course_id == course_id, s.c.section_id == section_id)).first()
        return SectionState(r.course_id, r.section_id, r.status, r.current_version, r.updated_at) if r else None

    def set_section_status(self, course_id: str, section_id: str, status: str, now: datetime | None = None) -> None:
        from app.infrastructure.database.tables import utcnow
        s = T.sections
        with self.db.begin() as c:
            c.execute(update(s).where(s.c.course_id == course_id, s.c.section_id == section_id).values(status=status, updated_at=now or utcnow()))

    # ---- versions and blocks -------------------------------------------------------------------
    def save_version(self, course_id: str, section_id: str, versions: Versions, *, payload=None, blocks: list[dict] | None = None, status: str = "complete",
                     tokens_in: int = 0, tokens_out: int = 0, now: datetime | None = None) -> ContentVersion:
        """Atomically store a new immutable version (with its blocks) and point the section at it."""
        from app.infrastructure.database.tables import utcnow
        now = now or utcnow()
        cv, cb, s = T.content_versions, T.content_blocks, T.sections
        vid = str(uuid.uuid4())
        with self.db.begin() as c:
            nxt = (c.execute(select(func.coalesce(func.max(cv.c.content_version), 0)).where(cv.c.course_id == course_id, cv.c.section_id == section_id)).scalar_one()) + 1
            c.execute(insert(cv).values(version_id=vid, course_id=course_id, section_id=section_id, content_version=nxt, schema_version=versions.schema_version,
                                        prompt_version=versions.prompt_version, model=versions.model, provider=versions.provider, status=status, payload=payload,
                                        tokens_in=tokens_in, tokens_out=tokens_out, created_at=now, completed_at=now if status == "complete" else None))
            for i, b in enumerate(blocks or []):
                c.execute(insert(cb).values(version_id=vid, ordinal=i, block_id=b["id"], type=b["type"], payload=b, created_at=now))
            if status == "complete":
                c.execute(update(s).where(s.c.course_id == course_id, s.c.section_id == section_id).values(status="COMPLETED", current_version=nxt, updated_at=now))
            row = c.execute(select(cv).where(cv.c.version_id == vid)).first()
        return _version(row, list(blocks or []))

    def current_content(self, course_id: str, section_id: str) -> ContentVersion | None:
        cv, cb, s = T.content_versions, T.content_blocks, T.sections
        with self.db.begin() as c:
            cur = c.execute(select(s.c.current_version).where(s.c.course_id == course_id, s.c.section_id == section_id)).scalar_one_or_none()
            if cur is None:
                return None
            row = c.execute(select(cv).where(cv.c.course_id == course_id, cv.c.section_id == section_id, cv.c.content_version == cur)).first()
            if not row:
                return None
            blocks = [r[0] for r in c.execute(select(cb.c.payload).where(cb.c.version_id == row.version_id).order_by(cb.c.ordinal))]
        return _version(row, blocks)

    def versions(self, course_id: str, section_id: str) -> list[ContentVersion]:
        cv = T.content_versions
        with self.db.begin() as c:
            rows = c.execute(select(cv).where(cv.c.course_id == course_id, cv.c.section_id == section_id).order_by(cv.c.content_version)).all()
        return [_version(r) for r in rows]

    def set_quality(self, version_id: str, quality_status: str) -> None:
        with self.db.begin() as c:
            c.execute(update(T.content_versions).where(T.content_versions.c.version_id == version_id).values(quality_status=quality_status))
