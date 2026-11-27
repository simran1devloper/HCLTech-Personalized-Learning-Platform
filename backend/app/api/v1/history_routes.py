"""Content history API: persistent, user-owned content sessions (see docs/implementation/content-history.md).

Every handler authenticates, then delegates to ``ContentHistoryStore`` which scopes every query by ``user_id``; a session that is missing,
deleted or owned by someone else is a plain 404. Reads (list, search, open, section, versions) never call a model.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.api.v1.schemas import Page
from app.core.container import Container, get_container
from app.core.exceptions import NotFoundError, ValidationFailure
from app.core.security import rate_limited_user
from app.infrastructure.database.content_history_store import CONTENT_TYPES, FILTERS, SORTS
from app.observability.metrics import metrics
from services import content_generation as cg
from services import learning_plan as lp
from services import session_generation as sg
from services.pipeline import with_heartbeats

log = logging.getLogger("content_history")
router = APIRouter(prefix="/api/v1/content-sessions", tags=["content-history"])
C = Depends(get_container)
U = Depends(rate_limited_user)
AI_UNAVAILABLE = "The AI service is temporarily unavailable. Please try again."


def _store(c: Container):
    return c.history


def _plan(session: dict) -> dict | None:
    return (session.get("metadata") or {}).get("plan")


def _order(session: dict) -> list[str]:
    """Section order: a plan session defines its own (gap-driven) sections; others use the standard guide."""
    p = _plan(session)
    return [x["key"] for x in p["sections"]] if p else cg.SECTION_NAMES


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _timed(name: str, t0: float) -> None:
    metrics.observe(name, (time.perf_counter() - t0) * 1000)


class SessionCreateIn(BaseModel):
    topic: str = Field(min_length=2, max_length=120)
    level: Literal["Beginner", "Intermediate", "Expert", "Advanced"] = "Intermediate"
    score: int = Field(5, ge=0, le=10)
    content_type: str = "study_guide"
    language: str = Field("en", max_length=10)
    title: str | None = Field(None, max_length=200)
    new_session: bool = False   # True: an intentionally separate session even if one exists for this topic

    @field_validator("topic", "title")
    @classmethod
    def _clean(cls, v):
        return v.replace("<", "").replace(">", "").strip() if isinstance(v, str) else v

    @field_validator("content_type")
    @classmethod
    def _ct(cls, v):
        if v not in CONTENT_TYPES:
            raise ValueError(f"content_type must be one of {CONTENT_TYPES}")
        return v


class SessionPatchIn(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=500)
    tags: list[str] | None = Field(None, max_length=20)

    @field_validator("title", "description")
    @classmethod
    def _clean(cls, v):
        return v.replace("<", "").replace(">", "").strip() if isinstance(v, str) else v

    @field_validator("tags")
    @classmethod
    def _tags(cls, v):
        return [t.replace("<", "").replace(">", "")[:40] for t in v] if v is not None else v


class ContinueIn(BaseModel):
    instruction: str | None = Field(None, min_length=3, max_length=500)  # omitted: resume / fill every missing section


class FeedbackIn(BaseModel):
    section_key: str | None = Field(None, max_length=60)
    rating: int | None = Field(None, ge=1, le=5)
    comment: str | None = Field(None, max_length=2000)


def _list(c: Container, user: str, q, status, content_type, sort, offset, limit) -> Page[dict]:
    t0 = time.perf_counter()
    if status not in FILTERS:
        raise ValidationFailure(f"status must be one of {FILTERS}")
    if content_type and content_type not in CONTENT_TYPES:
        raise ValidationFailure(f"content_type must be one of {CONTENT_TYPES}")
    if sort not in SORTS:
        raise ValidationFailure(f"sort must be one of {tuple(SORTS)}")
    sg.recover_abandoned(_store(c))
    items, total = _store(c).list_sessions(user, q=q, status=status, content_type=content_type, sort=sort, offset=offset, limit=limit)
    _timed("content_history_list_ms", t0)
    return Page(items=items, total=total, offset=offset, limit=limit)


@router.get("", response_model=Page[dict])
def list_sessions(q: str | None = Query(None, max_length=80), status: str = "all", content_type: str | None = None, sort: str = "updated",
                  offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100), user: str = U, c: Container = C):
    return _list(c, user, q, status, content_type, sort, offset, limit)


@router.get("/search", response_model=Page[dict])  # declared before /{session_id} so "search" is never taken for an id
def search_sessions(q: str = Query(..., min_length=1, max_length=80), status: str = "all", content_type: str | None = None, sort: str = "updated",
                    offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100), user: str = U, c: Container = C):
    return _list(c, user, q, status, content_type, sort, offset, limit)


@router.post("", status_code=201)
def create_session(body: SessionCreateIn, response: Response, user: str = U, c: Container = C):
    """Creates the session only (no model call). Returns the existing live session for the same topic+level unless ``new_session`` is set."""
    s, created = sg.open_session(_store(c), user, body.topic, body.level, body.score, new_session=body.new_session, content_type=body.content_type, language=body.language)
    if body.title and created:
        s = _store(c).update_session(user, s["id"], title=body.title) or s
    if not created:
        response.status_code = 200
    return {**s, "created": created}


def _session_or_404(c: Container, user: str, sid: str, touch: bool = False) -> dict:
    s = _store(c).get_session(user, sid, touch=touch)
    if not s:
        raise NotFoundError("content session not found")
    return s


@router.get("/{session_id}")
def get_session(session_id: str, user: str = U, c: Container = C):
    """Session + section metadata/status + active jobs. Content is fetched separately (``/content`` or per section): lazy by design."""
    t0 = time.perf_counter()
    sg.recover_abandoned(_store(c))
    s = _session_or_404(c, user, session_id, touch=True)
    out = {**s, "sections": _store(c).list_sections(user, session_id, _order(s)) or [], "active_jobs": _store(c).jobs_for_session(user, session_id, active_only=True) or []}
    _timed("content_history_open_ms", t0)
    return out


@router.patch("/{session_id}")
def patch_session(session_id: str, body: SessionPatchIn, user: str = U, c: Container = C):
    s = _store(c).update_session(user, session_id, title=body.title, description=body.description, tags=body.tags)
    if not s:
        raise NotFoundError("content session not found")
    return s


@router.delete("/{session_id}", status_code=204)
def delete_session(session_id: str, user: str = U, c: Container = C):
    if not _store(c).delete(user, session_id):
        raise NotFoundError("content session not found")
    return Response(status_code=204)


@router.post("/{session_id}/archive")
def archive_session(session_id: str, user: str = U, c: Container = C):
    if not _store(c).archive(user, session_id):
        raise NotFoundError("content session not found")
    return _session_or_404(c, user, session_id)


@router.post("/{session_id}/restore")
def restore_session(session_id: str, user: str = U, c: Container = C):
    if not _store(c).restore(user, session_id):
        raise NotFoundError("content session not found")
    return _session_or_404(c, user, session_id)


@router.post("/{session_id}/duplicate", status_code=201)
def duplicate_session(session_id: str, user: str = U, c: Container = C):
    s = _store(c).duplicate(user, session_id)
    if not s:
        raise NotFoundError("content session not found")
    return s


# ---------------------------------------------------------------------------------------------------- content (read-only, no LLM)
@router.get("/{session_id}/sections")
def list_sections(session_id: str, user: str = U, c: Container = C):
    s = _session_or_404(c, user, session_id)
    return {"items": _store(c).list_sections(user, session_id, _order(s)) or []}


@router.get("/{session_id}/content")
def get_content(session_id: str, user: str = U, c: Container = C):
    """Every completed section's active content in one round trip (what the study view restores on open). Zero model calls."""
    t0 = time.perf_counter()
    s = _session_or_404(c, user, session_id, touch=True)
    contents = _store(c).get_contents(user, session_id)
    metrics.inc("content_history_content_reads_total")
    _timed("content_history_content_ms", t0)
    return {"session_id": session_id, "topic": s["topic"], "level": s["learning_level"], "sections": contents,
            "incomplete_sections": [n for n in _order(s) if n not in contents]}


def _section_or_404(c: Container, user: str, sid: str, key: str) -> dict:
    sec = _store(c).get_section(user, sid, key)
    if not sec:
        raise NotFoundError("section not found")
    return sec


@router.get("/{session_id}/sections/{section_key}")
def get_section(session_id: str, section_key: str, user: str = U, c: Container = C):
    return _section_or_404(c, user, session_id, section_key)


@router.get("/{session_id}/sections/{section_key}/versions")
def section_versions(session_id: str, section_key: str, include_content: bool = False, user: str = U, c: Container = C):
    v = _store(c).versions(user, session_id, section_key, include_content=include_content)
    if v is None:
        raise NotFoundError("section not found")
    return {"items": v}


@router.post("/{session_id}/sections/{section_key}/versions/{version}/activate")
def activate_version(session_id: str, section_key: str, version: int, user: str = U, c: Container = C):
    m = _store(c).activate_version(user, session_id, section_key, version)
    if not m:
        raise NotFoundError("version not found")
    return m


@router.get("/{session_id}/events")
def session_events(session_id: str, after_id: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500), user: str = U, c: Container = C):
    """Durable generation events for this session; a client that lost its SSE connection replays from the last ``id`` it saw."""
    ev = _store(c).session_events(user, session_id, after_id, limit)
    if ev is None:
        raise NotFoundError("content session not found")
    return {"items": ev, "last_id": ev[-1]["id"] if ev else after_id}


@router.post("/{session_id}/feedback", status_code=201)
def add_feedback(session_id: str, body: FeedbackIn, user: str = U, c: Container = C):
    if body.rating is None and not body.comment:
        raise ValidationFailure("provide a rating or a comment")
    if body.section_key is not None and not sg.is_known_section(body.section_key):
        raise ValidationFailure("unknown section")
    if not _store(c).add_feedback(user, session_id, section_key=body.section_key, rating=body.rating, comment=body.comment):
        raise NotFoundError("content session not found")
    return {"ok": True}


# ---------------------------------------------------------------------------------------------------- generation (SSE, same protocol as /learning)
def _stream(gen) -> StreamingResponse:
    async def events():
        sent = 0
        try:
            async for ev in with_heartbeats(gen):
                if ev is None:  # idle: SSE comment keeps proxies (nginx/ngrok) from closing the connection
                    yield ": ping\n\n"
                    continue
                if ev["event"] == "section" and ev["data"].get("ok"):
                    sent += 1
                yield _sse(ev["event"], ev["data"])
        except Exception:
            log.exception("content session stream failed")
            yield _sse("error", {"message": AI_UNAVAILABLE, "partial": sent > 0})
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _core_key(session: dict, key: str) -> str:
    if key not in _order(session):
        raise ValidationFailure("only the guide's own sections can be generated or regenerated here; use /continue with an instruction for additional content")
    return key


def _run(c: Container, user: str, s: dict, **kw):
    """Plan sessions generate through the learning-plan pipeline; every other session through the standard guide pipeline."""
    return lp.stream_plan(_store(c), user, s, **kw) if _plan(s) else sg.stream_session(_store(c), user, s, **kw)


@router.post("/{session_id}/sections/{section_key}/generate")
async def generate_section(session_id: str, section_key: str, user: str = U, c: Container = C):
    """Generates the section only if it is missing; a completed section is streamed back from the database (no model call)."""
    s = _session_or_404(c, user, session_id)
    return _stream(_run(c, user, s, only=[_core_key(s, section_key)]))


@router.post("/{session_id}/sections/{section_key}/regenerate")
async def regenerate_section(session_id: str, section_key: str, user: str = U, c: Container = C):
    """A new *version* of this one section. The previous version stays available and active until the new one completes and validates."""
    s = _session_or_404(c, user, session_id)
    return _stream(_run(c, user, s, only=[_core_key(s, section_key)], force=True))


@router.post("/{session_id}/continue")
async def continue_session(session_id: str, body: ContinueIn | None = None, user: str = U, c: Container = C):
    """No instruction: resume — generate only what is missing. With an instruction: attach a new follow-up section to this session."""
    s = _session_or_404(c, user, session_id)
    if body and body.instruction:
        return _stream(sg.stream_followup(_store(c), user, s, body.instruction))
    return _stream(_run(c, user, s))
