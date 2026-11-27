"""Persistent content history (PostgreSQL / SQLite): sessions, sections, versions, jobs and durable events.

Rules enforced here (not in the HTTP layer), so no caller can forget them:
* **Ownership**: every public method takes ``user_id`` and only ever touches rows of sessions owned by that user and not
  soft-deleted. A foreign or missing session is indistinguishable (returns ``None`` / ``False``), so ids reveal nothing.
* **Never overwrite**: a section's active content changes only by inserting a new immutable row in
  ``content_section_versions`` and then pointing the section at it, in one transaction.
* **One generation per (session, section, target version)**: ``generation_key`` is UNIQUE; ``claim`` is a single
  compare-and-set UPDATE; leases expire, so a crashed worker's job can be taken over.
* Nothing in this module calls a model.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, delete, func, insert, or_, select, update

from app.infrastructure.database import tables as T
from app.infrastructure.database.engine import Db
from app.infrastructure.database.session import insert_for

ST = T.content_section_state
S, SEC, VER, JOB, EV, TAG, FB = (T.content_sessions, T.content_sections, T.content_section_versions, T.content_generation_jobs,
                                 T.content_generation_events, T.content_session_tags, T.content_session_feedback)

CONTENT_TYPES = ("study_guide", "course", "technical_deep_dive", "practical_implementation", "roadmap", "assessment", "interview_preparation", "other")
SORTS = {"updated": S.c.updated_at.desc(), "created": S.c.created_at.desc(), "accessed": func.coalesce(S.c.last_accessed_at, S.c.created_at).desc(),
         "alphabetical": func.lower(S.c.title).asc()}
FILTERS = ("all", "completed", "in_progress", "failed", "archived")
EXTRA_PREFIX = "extra-"   # follow-up sections ("explain X with an example") live beside the guide's own sections
_WS = re.compile(r"\s+")


def normalize_topic(topic: str) -> str:
    return _WS.sub(" ", topic.strip().lower())[:200]


def default_title(topic: str) -> str:
    t = _WS.sub(" ", topic.strip())
    return (t[:1].upper() + t[1:])[:200] if t else "Untitled session"


def content_hash(content: Any) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()).hexdigest()


def _search_text(title: str, description: str, topic: str, content_type: str, tags: list[str], concepts: list[str]) -> str:
    return " \n".join([title, description, topic, content_type.replace("_", " "), *tags, *concepts]).lower()[:8000]


def _now(now):
    return now or T.utcnow()


def derive_status(total: int, completed: int, running: bool, failed: int, archived: bool) -> str:
    if archived:
        return "archived"
    if running:
        return "generating"
    if total and completed >= total:
        return "completed"
    return "failed" if failed and not completed else "active"


class ContentHistoryStore:
    def __init__(self, db: Db):
        self.db = db

    # ------------------------------------------------------------------------------------------- sessions
    @staticmethod
    def _owned(session_id: str, user_id: str):
        return and_(S.c.id == session_id, S.c.user_id == user_id, S.c.deleted_at.is_(None))

    @staticmethod
    def _session(r) -> dict:
        m = dict(r._mapping)
        m["metadata"] = m.get("metadata") or {}
        m["context"] = m.get("context") or {}
        m.pop("search_text", None)
        return m

    def create_session(self, user_id: str, topic: str, level: str, sections: list[tuple[str, str]], *, content_type: str = "study_guide", language: str = "en",
                       request: dict | None = None, title: str | None = None, description: str = "", metadata_extra: dict | None = None,
                       now: datetime | None = None) -> dict:
        """Creates a session and one NOT_STARTED row per section, atomically."""
        now, sid = _now(now), str(uuid.uuid4())
        content_type = content_type if content_type in CONTENT_TYPES else "other"
        title = (title or default_title(topic)).strip()[:200]
        description = description or f"{content_type.replace('_', ' ').capitalize()} for {topic.strip()[:120]} · {level}"
        ctx = {"topic": topic.strip(), "level": level, "objectives": [], "generated_sections": [], "concepts": [], "pending": [sk for sk, _ in sections],
               "preferences": {"language": language}}
        with self.db.begin() as c:
            c.execute(insert(S).values(id=sid, user_id=user_id, title=title, description=description[:500], topic=topic.strip()[:200], normalized_topic=normalize_topic(topic),
                                       content_type=content_type, learning_level=level, language=language, status="active", total_sections=len(sections),
                                       completed_sections=0, metadata={"request": request or {}, "concepts": [], **(metadata_extra or {})}, context=ctx,
                                       search_text=_search_text(title, description, topic, content_type, [], [str(x) for x in (metadata_extra or {}).get("concepts", [])]), created_at=now, updated_at=now))
            for key, label in sections:
                c.execute(insert(SEC).values(id=str(uuid.uuid4()), session_id=sid, section_key=key, section_title=label, status="NOT_STARTED", content_version=0,
                                             created_at=now, updated_at=now))
        return self.get_session(user_id, sid)  # type: ignore[return-value]

    def topic_progress(self, user_id: str) -> list[dict]:
        """Study progress per live session (id, title, normalized_topic, status, total/completed sections, updated_at): the learning
        side of skill mastery. Read-only; a session never creates evidence."""
        with self.db.begin() as c:
            rows = c.execute(select(S.c.id, S.c.title, S.c.normalized_topic, S.c.status, S.c.total_sections, S.c.completed_sections, S.c.updated_at)
                             .where(S.c.user_id == user_id, S.c.deleted_at.is_(None)).order_by(S.c.updated_at.desc())).all()
        return [dict(r._mapping) for r in rows]

    def find_reusable_session(self, user_id: str, topic: str, level: str, content_type: str = "study_guide") -> dict | None:
        """The user's most recently updated live (not archived) session for the same topic+level, so reloads/reconnects never fork a new one."""
        with self.db.begin() as c:
            r = c.execute(select(S).where(S.c.user_id == user_id, S.c.deleted_at.is_(None), S.c.archived_at.is_(None), S.c.normalized_topic == normalize_topic(topic),
                                          S.c.learning_level == level, S.c.content_type == content_type).order_by(S.c.updated_at.desc()).limit(1)).first()
        return self._session(r) if r else None

    def get_session(self, user_id: str, session_id: str, *, touch: bool = False, now: datetime | None = None) -> dict | None:
        with self.db.begin() as c:
            if touch:
                c.execute(update(S).where(self._owned(session_id, user_id)).values(last_accessed_at=_now(now)))
            r = c.execute(select(S).where(self._owned(session_id, user_id))).first()
            if not r:
                return None
            out = self._session(r)
            out["tags"] = [t for (t,) in c.execute(select(TAG.c.tag).where(TAG.c.session_id == session_id).order_by(TAG.c.tag))]
        return out

    def list_sessions(self, user_id: str, *, q: str | None = None, status: str = "all", content_type: str | None = None, sort: str = "updated",
                      offset: int = 0, limit: int = 20) -> tuple[list[dict], int]:
        """Server-side search, filter, sort and pagination. Reads only the summary columns; section content is never loaded."""
        cond = [S.c.user_id == user_id, S.c.deleted_at.is_(None)]
        if status == "archived":
            cond.append(S.c.archived_at.is_not(None))
        else:
            cond.append(S.c.archived_at.is_(None))
            if status == "completed":
                cond.append(S.c.status == "completed")
            elif status == "in_progress":
                cond.append(S.c.status.in_(("active", "generating")))
            elif status == "failed":
                cond.append(S.c.status == "failed")
        if content_type:
            cond.append(S.c.content_type == content_type)
        for term in [t for t in _WS.split((q or "").strip().lower()) if t][:6]:
            cond.append(S.c.search_text.contains(term, autoescape=True))
        cols = [S.c.id, S.c.title, S.c.description, S.c.topic, S.c.content_type, S.c.learning_level, S.c.language, S.c.status, S.c.total_sections, S.c.completed_sections,
                S.c.created_at, S.c.updated_at, S.c.last_accessed_at, S.c.archived_at]
        with self.db.begin() as c:
            total = c.execute(select(func.count()).select_from(S).where(*cond)).scalar_one()
            rows = c.execute(select(*cols).where(*cond).order_by(SORTS.get(sort, SORTS["updated"]), S.c.id).offset(offset).limit(limit)).all()
            ids = [r.id for r in rows]
            tags: dict[str, list[str]] = {}
            if ids:
                for sid, tag in c.execute(select(TAG.c.session_id, TAG.c.tag).where(TAG.c.session_id.in_(ids)).order_by(TAG.c.tag)):
                    tags.setdefault(sid, []).append(tag)
        return [{**dict(r._mapping), "tags": tags.get(r.id, [])} for r in rows], int(total)

    def update_session(self, user_id: str, session_id: str, *, title: str | None = None, description: str | None = None, tags: list[str] | None = None,
                       now: datetime | None = None) -> dict | None:
        now = _now(now)
        with self.db.begin() as c:
            r = c.execute(select(S).where(self._owned(session_id, user_id))).first()
            if not r:
                return None
            vals: dict = {"updated_at": now}
            if title is not None:
                vals["title"] = title.strip()[:200]
            if description is not None:
                vals["description"] = description.strip()[:500]
            if tags is not None:
                clean = sorted({t.strip().lower()[:40] for t in tags if t.strip()})[:20]
                c.execute(delete(TAG).where(TAG.c.session_id == session_id))
                for t in clean:
                    c.execute(insert(TAG).values(session_id=session_id, tag=t, created_at=now))
            c.execute(update(S).where(S.c.id == session_id).values(**vals))
            self._refresh_search(c, session_id)
        return self.get_session(user_id, session_id)

    def _refresh_search(self, c, session_id: str) -> None:
        r = c.execute(select(S.c.title, S.c.description, S.c.topic, S.c.content_type, S.c["metadata"]).where(S.c.id == session_id)).first()
        tags = [t for (t,) in c.execute(select(TAG.c.tag).where(TAG.c.session_id == session_id))]
        concepts = [str(x) for x in ((r._mapping["metadata"] or {}).get("concepts") or [])]
        c.execute(update(S).where(S.c.id == session_id).values(search_text=_search_text(r.title, r.description, r.topic, r.content_type, tags, concepts)))

    def archive(self, user_id: str, session_id: str, now: datetime | None = None) -> bool:
        now = _now(now)
        with self.db.begin() as c:
            res = c.execute(update(S).where(self._owned(session_id, user_id), S.c.archived_at.is_(None)).values(archived_at=now, status="archived", updated_at=now))
            ok = res.rowcount == 1
            if ok:
                self._audit(c, user_id, "content_session.archive", session_id, now)
        return ok or self._exists(user_id, session_id)

    def restore(self, user_id: str, session_id: str, now: datetime | None = None) -> bool:
        now = _now(now)
        with self.db.begin() as c:
            if not c.execute(select(S.c.id).where(self._owned(session_id, user_id))).first():
                return False
            c.execute(update(S).where(S.c.id == session_id).values(archived_at=None, updated_at=now))
            self._recompute(c, session_id, now)
            self._audit(c, user_id, "content_session.restore", session_id, now)
        return True

    def delete(self, user_id: str, session_id: str, now: datetime | None = None) -> bool:
        """Soft delete (``deleted_at``): the row disappears from every user-facing query, is purged by retention, and the action is audited."""
        now = _now(now)
        with self.db.begin() as c:
            res = c.execute(update(S).where(self._owned(session_id, user_id)).values(deleted_at=now, updated_at=now))
            if res.rowcount != 1:
                return False
            c.execute(update(JOB).where(JOB.c.session_id == session_id, JOB.c.status.in_(("QUEUED", "RUNNING"))).values(status="CANCELLED", completed_at=now, updated_at=now))
            self._audit(c, user_id, "content_session.delete", session_id, now)
        return True

    def purge_deleted(self, older_than: timedelta, now: datetime | None = None) -> int:
        """Retention: hard-delete soft-deleted sessions (cascades to sections, versions, jobs, events, tags, feedback)."""
        with self.db.begin() as c:
            return c.execute(delete(S).where(S.c.deleted_at.is_not(None), S.c.deleted_at < _now(now) - older_than)).rowcount

    def duplicate(self, user_id: str, session_id: str, now: datetime | None = None) -> dict | None:
        """A new, independent session holding the active content of every completed section as its version 1."""
        now = _now(now)
        src = self.get_session(user_id, session_id)
        if not src:
            return None
        secs = self.list_sections(user_id, session_id) or []
        dup = self.create_session(user_id, src["topic"], src["learning_level"], [(s["section_key"], s["section_title"]) for s in secs], content_type=src["content_type"],
                                  language=src["language"], request=src["metadata"].get("request"), title=f"{src['title']} (copy)"[:200],
                                  description=src["description"], now=now)
        for s in secs:
            if s["status"] == "COMPLETED":
                full = self.get_section(user_id, session_id, s["section_key"])
                if full and full["content"] is not None:
                    self.save_section(user_id, dup["id"], s["section_key"], full["content"], prompt_version=full.get("prompt_version") or "", provider=full.get("model_provider") or "",
                                      model=full.get("model_name") or "", now=now)
        return self.get_session(user_id, dup["id"])

    def _exists(self, user_id: str, session_id: str) -> bool:
        with self.db.begin() as c:
            return c.execute(select(S.c.id).where(self._owned(session_id, user_id))).first() is not None

    def _audit(self, c, user_id: str, action: str, session_id: str, now: datetime, details: dict | None = None) -> None:
        c.execute(insert(T.audit_log).values(user_id=user_id, action=action, entity="content_session", entity_id=session_id, details=details or {}, created_at=now))

    # ------------------------------------------------------------------------------------------- sections
    @staticmethod
    def _section_meta(r) -> dict:
        m = r._mapping
        return {"section_key": m["section_key"], "section_title": m["section_title"], "status": m["status"], "content_version": m["content_version"],
                "has_content": m["content_hash"] is not None, "content_hash": m["content_hash"], "model_provider": m["model_provider"], "model_name": m["model_name"],
                "input_tokens": m["input_tokens"], "output_tokens": m["output_tokens"], "updated_at": m["updated_at"]}

    def list_sections(self, user_id: str, session_id: str, order: list[str] | None = None) -> list[dict] | None:
        """Section metadata and statuses only (no content): cheap enough to call on every page load."""
        if not self._exists(user_id, session_id):
            return None
        cols = [SEC.c.section_key, SEC.c.section_title, SEC.c.status, SEC.c.content_version, SEC.c.content_hash, SEC.c.model_provider, SEC.c.model_name, SEC.c.input_tokens,
                SEC.c.output_tokens, SEC.c.updated_at]
        with self.db.begin() as c:
            rows = c.execute(select(*cols).where(SEC.c.session_id == session_id)).all()
        pos = {k: i for i, k in enumerate(order or [])}
        return sorted((self._section_meta(r) for r in rows), key=lambda s: (pos.get(s["section_key"], 999), s["section_key"]))

    def add_section(self, user_id: str, session_id: str, key: str, title: str, now: datetime | None = None) -> bool:
        """Attach an additional (follow-up) section to an existing session. Never touches existing sections."""
        now = _now(now)
        with self.db.begin() as c:
            if not c.execute(select(S.c.id).where(self._owned(session_id, user_id))).first():
                return False
            c.execute(insert_for(self.db, SEC).values(id=str(uuid.uuid4()), session_id=session_id, section_key=key, section_title=title[:120], status="NOT_STARTED",
                                                      content_version=0, created_at=now, updated_at=now).on_conflict_do_nothing(index_elements=["session_id", "section_key"]))
        return True

    def get_section(self, user_id: str, session_id: str, key: str) -> dict | None:
        if not self._exists(user_id, session_id):
            return None
        with self.db.begin() as c:
            r = c.execute(select(SEC).where(SEC.c.session_id == session_id, SEC.c.section_key == key)).first()
        return {**self._section_meta(r), "content": r.content, "prompt_version": r.prompt_version, "schema_version": r.schema_version} if r else None

    def get_contents(self, user_id: str, session_id: str, keys: list[str] | None = None) -> dict[str, Any]:
        """Active content of completed sections, keyed by section_key (one query)."""
        if not self._exists(user_id, session_id):
            return {}
        q = select(SEC.c.section_key, SEC.c.content).where(SEC.c.session_id == session_id, SEC.c.status == "COMPLETED", SEC.c.content.is_not(None))
        if keys:
            q = q.where(SEC.c.section_key.in_(keys))
        with self.db.begin() as c:
            return {k: v for k, v in c.execute(q)}

    def versions(self, user_id: str, session_id: str, key: str, include_content: bool = False) -> list[dict] | None:
        if not self._exists(user_id, session_id):
            return None
        cols = [VER.c.version, VER.c.content_hash, VER.c.prompt_version, VER.c.model_provider, VER.c.model_name, VER.c.input_tokens, VER.c.output_tokens, VER.c.created_at, VER.c.job_id]
        if include_content:
            cols.append(VER.c.content)
        with self.db.begin() as c:
            sec = c.execute(select(SEC.c.id, SEC.c.content_version).where(SEC.c.session_id == session_id, SEC.c.section_key == key)).first()
            if not sec:
                return None
            rows = c.execute(select(*cols).where(VER.c.section_id == sec.id).order_by(VER.c.version.desc())).all()
        return [{**dict(r._mapping), "active": r.version == sec.content_version} for r in rows]

    def save_section(self, user_id: str, session_id: str, key: str, content: Any, *, prompt_version: str = "", schema_version: str = "1", provider: str = "", model: str = "",
                     tokens_in: int = 0, tokens_out: int = 0, latency_ms: float = 0.0, job_id: str | None = None, force_new: bool = False,
                     now: datetime | None = None) -> dict | None:
        """Persist a completed section as a new immutable version and make it active, atomically.

        Idempotent: identical content to the active version creates nothing (unless ``force_new``), so a retried or duplicated worker
        cannot inflate history. Returns the section metadata, or ``None`` if the session is not the caller's."""
        now, h = _now(now), content_hash(content)
        with self.db.begin() as c:
            if not c.execute(select(S.c.id).where(self._owned(session_id, user_id))).first():
                return None
            sec = c.execute(select(SEC).where(SEC.c.session_id == session_id, SEC.c.section_key == key)).first()
            if not sec:
                return None
            if sec.status == "COMPLETED" and sec.content_hash == h and not force_new:
                return self._section_meta(sec)
            nxt = c.execute(select(func.coalesce(func.max(VER.c.version), 0)).where(VER.c.section_id == sec.id)).scalar_one() + 1
            c.execute(insert(VER).values(id=str(uuid.uuid4()), section_id=sec.id, session_id=session_id, version=nxt, content=content, content_hash=h, prompt_version=prompt_version,
                                         schema_version=schema_version, model_provider=provider, model_name=model, input_tokens=tokens_in, output_tokens=tokens_out,
                                         generation_latency_ms=latency_ms, job_id=job_id, created_at=now))
            c.execute(update(SEC).where(SEC.c.id == sec.id).values(content=content, content_hash=h, content_version=nxt, status="COMPLETED", prompt_version=prompt_version,
                                                                   schema_version=schema_version, model_provider=provider, model_name=model, input_tokens=tokens_in,
                                                                   output_tokens=tokens_out, generation_latency_ms=latency_ms, updated_at=now))
            if key == "key_concepts" and isinstance(content, list):
                terms = [str(x.get("term")) for x in content if isinstance(x, dict) and x.get("term")][:40]
                meta = dict(c.execute(select(S.c["metadata"]).where(S.c.id == session_id)).scalar_one() or {})
                meta["concepts"] = terms
                c.execute(update(S).where(S.c.id == session_id).values(metadata=meta))
            self._recompute(c, session_id, now)
            self._refresh_search(c, session_id)
            r = c.execute(select(SEC).where(SEC.c.id == sec.id)).first()
        return self._section_meta(r)

    def activate_version(self, user_id: str, session_id: str, key: str, version: int, now: datetime | None = None) -> dict | None:
        """Make an earlier version active again. Nothing is deleted; the choice itself is recorded as the active pointer."""
        now = _now(now)
        with self.db.begin() as c:
            if not c.execute(select(S.c.id).where(self._owned(session_id, user_id))).first():
                return None
            sec = c.execute(select(SEC.c.id).where(SEC.c.session_id == session_id, SEC.c.section_key == key)).first()
            v = c.execute(select(VER).where(VER.c.section_id == sec.id, VER.c.version == version)).first() if sec else None
            if not v:
                return None
            c.execute(update(SEC).where(SEC.c.id == sec.id).values(content=v.content, content_hash=v.content_hash, content_version=v.version, status="COMPLETED",
                                                                   prompt_version=v.prompt_version, model_provider=v.model_provider, model_name=v.model_name, updated_at=now))
            self._recompute(c, session_id, now)
            r = c.execute(select(SEC).where(SEC.c.id == sec.id)).first()
        return self._section_meta(r)

    def set_section_status(self, session_id: str, key: str, status: str, now: datetime | None = None) -> None:
        """Internal (worker) status change. A COMPLETED section is never downgraded: a failed regeneration leaves the old content active."""
        now = _now(now)
        with self.db.begin() as c:
            c.execute(update(SEC).where(SEC.c.session_id == session_id, SEC.c.section_key == key, or_(SEC.c.status != "COMPLETED", SEC.c.content.is_(None)))
                      .values(status=status, updated_at=now))
            self._recompute(c, session_id, now)

    def _recompute(self, c, session_id: str, now: datetime) -> None:
        core = and_(SEC.c.session_id == session_id, ~SEC.c.section_key.startswith(EXTRA_PREFIX, autoescape=True))  # follow-ups never count toward progress
        total, done, failed = c.execute(select(func.count(), func.coalesce(func.sum(case((SEC.c.status == "COMPLETED", 1), else_=0)), 0),
                                               func.coalesce(func.sum(case((SEC.c.status == "FAILED", 1), else_=0)), 0)).where(core)).one()
        running = c.execute(select(func.count()).where(JOB.c.session_id == session_id, JOB.c.status.in_(("QUEUED", "RUNNING")))).scalar_one() > 0
        archived = c.execute(select(S.c.archived_at).where(S.c.id == session_id)).scalar_one() is not None
        generated = [k for (k,) in c.execute(select(SEC.c.section_key).where(SEC.c.session_id == session_id, SEC.c.status == "COMPLETED"))]
        pending = [k for (k,) in c.execute(select(SEC.c.section_key).where(core, SEC.c.status != "COMPLETED"))]
        ctx = dict(c.execute(select(S.c.context).where(S.c.id == session_id)).scalar_one() or {})
        ctx.update(generated_sections=generated, pending=pending)
        c.execute(update(S).where(S.c.id == session_id).values(total_sections=int(total), completed_sections=int(done), updated_at=now, context=ctx,
                                                                status=derive_status(int(total), int(done), running, int(failed), archived)))

    def recompute(self, session_id: str) -> None:
        with self.db.begin() as c:
            self._recompute(c, session_id, T.utcnow())

    def update_context(self, user_id: str, session_id: str, patch: dict) -> None:
        with self.db.begin() as c:
            r = c.execute(select(S.c.context).where(self._owned(session_id, user_id))).first()
            if r:
                c.execute(update(S).where(S.c.id == session_id).values(context={**(r[0] or {}), **patch}))

    # ------------------------------------------------------------------------------------------- jobs
    @staticmethod
    def _job(r) -> dict:
        return dict(r._mapping)

    def create_or_get_job(self, session_id: str, key: str, generation_key: str, *, max_attempts: int = 3, now: datetime | None = None) -> tuple[dict, bool]:
        """(job, created). Exactly one of any number of racing callers sees created=True for a generation_key."""
        now = _now(now)
        with self.db.begin() as c:
            sec_id = c.execute(select(SEC.c.id).where(SEC.c.session_id == session_id, SEC.c.section_key == key)).scalar_one_or_none()
            res = c.execute(insert_for(self.db, JOB).values(id=str(uuid.uuid4()), session_id=session_id, section_id=sec_id, section_key=key, generation_key=generation_key,
                                                            status="QUEUED", attempt_count=0, max_attempts=max_attempts, created_at=now, updated_at=now)
                            .on_conflict_do_nothing(index_elements=["generation_key"]).returning(JOB.c.id))
            created = res.first() is not None
            row = c.execute(select(JOB).where(JOB.c.generation_key == generation_key)).first()
        return self._job(row), created

    def get_job(self, job_id: str) -> dict | None:
        with self.db.begin() as c:
            r = c.execute(select(JOB).where(JOB.c.id == job_id)).first()
        return self._job(r) if r else None

    def claim_job(self, job_id: str, worker: str, lease_s: float = 60, now: datetime | None = None) -> bool:
        """Compare-and-set: take a QUEUED job, or a RUNNING one whose lease expired. Exactly one claimant wins; attempts are capped."""
        now = _now(now)
        free = or_(JOB.c.status == "QUEUED", and_(JOB.c.status == "RUNNING", JOB.c.lease_expires_at < now))
        with self.db.begin() as c:
            res = c.execute(update(JOB).where(JOB.c.id == job_id, free, JOB.c.attempt_count < JOB.c.max_attempts).values(
                status="RUNNING", worker_id=worker, lease_expires_at=now + timedelta(seconds=lease_s), heartbeat_at=now, attempt_count=JOB.c.attempt_count + 1,
                started_at=func.coalesce(JOB.c.started_at, now), updated_at=now, error_code=None, error_message=None))
            if res.rowcount == 1:
                sid, key = c.execute(select(JOB.c.session_id, JOB.c.section_key).where(JOB.c.id == job_id)).one()
                c.execute(update(SEC).where(SEC.c.session_id == sid, SEC.c.section_key == key, or_(SEC.c.status != "COMPLETED", SEC.c.content.is_(None)))
                          .values(status="GENERATING", updated_at=now))
                self._recompute(c, sid, now)
        return res.rowcount == 1

    def heartbeat(self, job_id: str, worker: str, lease_s: float = 60, now: datetime | None = None) -> bool:
        now = _now(now)
        with self.db.begin() as c:
            return c.execute(update(JOB).where(JOB.c.id == job_id, JOB.c.worker_id == worker, JOB.c.status == "RUNNING", JOB.c.lease_expires_at >= now)
                             .values(heartbeat_at=now, lease_expires_at=now + timedelta(seconds=lease_s), updated_at=now)).rowcount == 1

    def finish_job(self, job_id: str, status: str, *, worker: str | None = None, error_code: str | None = None, error_message: str | None = None,
                   now: datetime | None = None) -> bool:
        """Owner-checked terminal transition; also settles the section and session status."""
        now = _now(now)
        cond = [JOB.c.id == job_id, JOB.c.status.in_(("QUEUED", "RUNNING"))] + ([JOB.c.worker_id == worker] if worker else [])
        with self.db.begin() as c:
            res = c.execute(update(JOB).where(*cond).values(status=status, completed_at=now, lease_expires_at=None, updated_at=now, error_code=error_code,
                                                            error_message=(error_message or None) and error_message[:2000]))
            if res.rowcount == 1:
                sid, key = c.execute(select(JOB.c.session_id, JOB.c.section_key).where(JOB.c.id == job_id)).one()
                if status != "COMPLETED":  # an unfinished section goes back to FAILED (retry) or NOT_STARTED (cancelled); completed content is untouched
                    c.execute(update(SEC).where(SEC.c.session_id == sid, SEC.c.section_key == key, SEC.c.status == "GENERATING")
                              .values(status="FAILED" if status == "FAILED" else "NOT_STARTED", updated_at=now))
                self._recompute(c, sid, now)
        return res.rowcount == 1

    def requeue_job(self, job_id: str, now: datetime | None = None) -> bool:
        """FAILED / CANCELLED jobs can be retried by the user; exactly one caller wins the transition. Resets the attempt budget."""
        with self.db.begin() as c:
            return c.execute(update(JOB).where(JOB.c.id == job_id, JOB.c.status.in_(("FAILED", "CANCELLED"))).values(
                status="QUEUED", attempt_count=0, completed_at=None, error_code=None, error_message=None, worker_id=None, updated_at=_now(now))).rowcount == 1

    def recover_abandoned(self, now: datetime | None = None) -> int:
        """Jobs whose worker died (lease expired): CANCELLED (resumable) while attempts remain, otherwise FAILED. Completed sections are never touched,
        so resuming never repeats finished LLM work. Safe to call from any process at any time."""
        now = _now(now)
        n = 0
        with self.db.begin() as c:
            rows = c.execute(select(JOB.c.id, JOB.c.session_id, JOB.c.section_key, JOB.c.attempt_count, JOB.c.max_attempts)
                             .where(JOB.c.status == "RUNNING", JOB.c.lease_expires_at < now)).all()
            for r in rows:
                again = r.attempt_count < r.max_attempts
                # CANCELLED = "interrupted, resumable" (requeue_job / a new request retries it); FAILED once automatic attempts are used up
                if c.execute(update(JOB).where(JOB.c.id == r.id, JOB.c.status == "RUNNING", JOB.c.lease_expires_at < now).values(
                        status="CANCELLED" if again else "FAILED", worker_id=None, lease_expires_at=None, completed_at=now, updated_at=now,
                        error_code="interrupted" if again else "attempts_exhausted", error_message="worker lease expired")).rowcount == 1:
                    n += 1
                    c.execute(update(SEC).where(SEC.c.session_id == r.session_id, SEC.c.section_key == r.section_key, SEC.c.status == "GENERATING")
                              .values(status="NOT_STARTED" if again else "FAILED", updated_at=now))
                    self._recompute(c, r.session_id, now)
        return n

    def jobs_for_session(self, user_id: str, session_id: str, active_only: bool = False) -> list[dict] | None:
        if not self._exists(user_id, session_id):
            return None
        q = select(JOB).where(JOB.c.session_id == session_id).order_by(JOB.c.created_at.desc()).limit(100)
        if active_only:
            q = q.where(JOB.c.status.in_(("QUEUED", "RUNNING")))
        with self.db.begin() as c:
            return [self._job(r) for r in c.execute(q)]

    # ------------------------------------------------------------------------------------------- durable events
    def append_event(self, job_id: str, event_type: str, payload: dict | None = None, now: datetime | None = None) -> int:
        """Per-job monotonic sequence (UNIQUE(job_id, event_sequence) makes concurrent writers fail loudly instead of reordering)."""
        with self.db.begin() as c:
            seq = c.execute(select(func.coalesce(func.max(EV.c.event_sequence), 0)).where(EV.c.job_id == job_id)).scalar_one() + 1
            c.execute(insert(EV).values(job_id=job_id, event_sequence=seq, event_type=event_type, payload=payload or {}, created_at=_now(now)))
        return int(seq)

    def session_events(self, user_id: str, session_id: str, after_id: int = 0, limit: int = 200) -> list[dict] | None:
        """Events across the session's jobs in global insertion order; a reconnecting client resumes from the last ``id`` it saw."""
        if not self._exists(user_id, session_id):
            return None
        with self.db.begin() as c:
            rows = c.execute(select(EV.c.id, EV.c.job_id, EV.c.event_sequence, EV.c.event_type, EV.c.payload, EV.c.created_at, JOB.c.section_key)
                             .join(JOB, JOB.c.id == EV.c.job_id).where(JOB.c.session_id == session_id, EV.c.id > after_id).order_by(EV.c.id).limit(limit)).all()
        return [dict(r._mapping) for r in rows]

    # ------------------------------------------------------------------------------------------- feedback
    def add_feedback(self, user_id: str, session_id: str, *, section_key: str | None, rating: int | None, comment: str | None, now: datetime | None = None) -> bool:
        if not self._exists(user_id, session_id):
            return False
        with self.db.begin() as c:
            c.execute(insert(FB).values(id=str(uuid.uuid4()), session_id=session_id, user_id=user_id, section_key=section_key, rating=rating,
                                        comment=(comment or "")[:2000] or None, created_at=_now(now)))
        return True

    def feedback(self, user_id: str, session_id: str) -> list[dict]:
        if not self._exists(user_id, session_id):
            return []
        with self.db.begin() as c:
            return [dict(r._mapping) for r in c.execute(select(FB).where(FB.c.session_id == session_id, FB.c.user_id == user_id).order_by(FB.c.created_at.desc()))]

    # ------------------------------------------------------------------------------------------- per-learner reading state
    def get_states(self, user_id: str, session_id: str) -> dict[str, dict] | None:
        """{section_key: {understood, bookmarked, note, opened_at}} for the learner's own session; None if it is not theirs."""
        if not self._exists(user_id, session_id):
            return None
        with self.db.begin() as c:
            rows = c.execute(select(ST).where(ST.c.session_id == session_id)).all()
        return {r.section_key: {"understood": r.understood, "bookmarked": r.bookmarked, "note": r.note, "opened_at": r.opened_at, "updated_at": r.updated_at} for r in rows}

    def set_state(self, user_id: str, session_id: str, key: str, *, understood: bool | None = None, bookmarked: bool | None = None, note: str | None = None,
                  opened: bool = False, now: datetime | None = None) -> dict | None:
        """Upsert only the fields given (idempotent; a later request never clobbers fields it did not send)."""
        now = _now(now)
        with self.db.begin() as c:
            if not c.execute(select(S.c.id).where(self._owned(session_id, user_id))).first():
                return None
            c.execute(insert_for(self.db, ST).values(session_id=session_id, section_key=key, updated_at=now).on_conflict_do_nothing(index_elements=["session_id", "section_key"]))
            vals: dict = {"updated_at": now}
            if understood is not None:
                vals["understood"] = understood
            if bookmarked is not None:
                vals["bookmarked"] = bookmarked
            if note is not None:
                vals["note"] = note[:5000]
            if opened:
                vals["opened_at"] = now
            c.execute(update(ST).where(ST.c.session_id == session_id, ST.c.section_key == key).values(**vals))
            r = c.execute(select(ST).where(ST.c.session_id == session_id, ST.c.section_key == key)).first()
        return {"understood": r.understood, "bookmarked": r.bookmarked, "note": r.note, "opened_at": r.opened_at, "updated_at": r.updated_at}

    # ------------------------------------------------------------------------------------------- pipeline report
    def pipeline_rows(self, user_id: str, session_id: str, order: list[str]) -> list[dict] | None:
        """One row per section with what really happened: status, version, tokens, latency, whether it was reused or generated, the latest job's outcome."""
        if not self._exists(user_id, session_id):
            return None
        with self.db.begin() as c:
            secs = c.execute(select(SEC).where(SEC.c.session_id == session_id)).all()
            jobs = {}
            for j in c.execute(select(JOB).where(JOB.c.session_id == session_id).order_by(JOB.c.created_at)).all():
                jobs[j.section_key] = j                       # later rows overwrite earlier: the most recent job per section
        pos = {k: i for i, k in enumerate(order)}
        out = []
        for r in sorted(secs, key=lambda x: (pos.get(x.section_key, 999), x.section_key)):
            j = jobs.get(r.section_key)
            done = r.status == "COMPLETED"
            out.append({"section_key": r.section_key, "title": r.section_title, "status": r.status, "version": r.content_version,
                        "source": ("generated" if (r.input_tokens or r.output_tokens or (j and j.status == "COMPLETED")) else "reused") if done else None,
                        "input_tokens": r.input_tokens, "output_tokens": r.output_tokens, "latency_ms": r.generation_latency_ms,
                        "attempts": j.attempt_count if j else 0, "job_status": j.status if j else None, "error": j.error_message if j and j.status in ("FAILED", "CANCELLED") else None,
                        "error_code": j.error_code if j and j.status in ("FAILED", "CANCELLED") else None, "updated_at": r.updated_at})
        return out
