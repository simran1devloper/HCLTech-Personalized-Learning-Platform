"""Diagnostic Study API: database-first quiz start with AI only as a fallback (separate experience from Assessments; same question bank and attempt tables).

    topic -> normalise -> resolve canonical skill -> count eligible validated questions
        enough            -> database only                       (generation_source = question_bank, zero model calls)
        none              -> generate, validate, save, then use  (ai_generated)
        some, not enough  -> generate ONLY the missing ones      (hybrid)

``check`` is a read-only preview of that decision (no model, nothing created) so the UI can describe what will really happen."""
from __future__ import annotations

import asyncio
import time
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.v1.assessment_routes import _own, _snapshots, _view, grade_and_finalize
from app.core.container import Container, get_container
from app.core.exceptions import NotFoundError
from app.core.security import idempotency_key, rate_limited_user
from app.observability.metrics import metrics

router = APIRouter(prefix="/api/v1/diagnostic-study", tags=["diagnostic-study"])
C = Depends(get_container)
U = Depends(rate_limited_user)
Level = Literal["Beginner", "Intermediate", "Advanced", "Expert"]


class StartIn(BaseModel):
    topic: str = Field(min_length=1, max_length=200)   # free text; normalised and validated by the service
    question_count: int = Field(10, ge=3, le=20)
    level: Level | None = None


class SubmitIn(BaseModel):
    answers: dict = Field(default_factory=dict, max_length=50)


def _level_for(pct: float) -> str:
    return "Beginner" if pct < 50 else "Intermediate" if pct < 80 else "Expert"      # same bands as the legacy 0-4 / 5-7 / 8-10 diagnostic


def _answer_text(options: list[str], sel) -> str | None:
    if sel is None or sel == [] or sel == "":
        return None
    if isinstance(sel, list):
        return ", ".join(options[i] for i in sel if isinstance(i, int) and 0 <= i < len(options)) or None
    return options[sel] if isinstance(sel, int) and 0 <= sel < len(options) else str(sel)


def _session(c: Container, user: str, a: dict) -> dict:
    """Learner view of a session: questions without answers, saved answers while open, the shaped result once submitted."""
    rows = _snapshots(c, a)
    out = {"session_id": a["attempt_id"], "topic": a.get("topic") or "", "skill_id": a["skill_id"], "status": a["status"], "question_count": len(rows),
           "generation_source": a.get("generation_source"), "started_at": a["started_at"], "questions": [_view(r) for r in rows],
           "answers": {r["question_snapshot"]["question_id"]: r["selected_answer"] for r in rows if r["selected_answer"] is not None} if a["status"] == "open" else {},
           "answered_questions": sum(1 for r in rows if r["selected_answer"] is not None), "result": None}
    if a["status"] == "completed":
        pct = a["score"] or 0.0
        out["result"] = {"topic": out["topic"], "score": round(pct * len(rows) / 100), "total": len(rows), "score_pct": pct, "score_10": round(pct / 10), "level": _level_for(pct),
                         "breakdown": [{"question": r["question_snapshot"]["prompt"], "user_answer": _answer_text(r["question_snapshot"]["options"], r["selected_answer"]),
                                        "correct_answer": _answer_text(r["question_snapshot"]["options"], r["question_snapshot"]["correct"]), "correct": bool(r["is_correct"]),
                                        "explanation": r["question_snapshot"]["explanation"]} for r in rows]}
    return out


@router.get("/suggest")
def suggest(q: str = Query("", max_length=80), user: str = U, c: Container = C):
    """Canonical topic suggestions (registry names and aliases). Free text is always accepted; this is only a convenience."""
    return {"items": c.bank.suggest(q)}


@router.post("/check")
async def check(body: StartIn, user: str = U, c: Container = C):
    """What a start would do for this topic, decided from the database only. Creates nothing and never calls a model."""
    topic = c.bank.normalize_topic(body.topic)
    skill = await asyncio.to_thread(c.bank.resolve_skill, topic)
    if not skill:
        return {"topic": topic, "canonical_topic": None, "known": False, "plan": "ai_generated", "eligible": 0, "requested": body.question_count, "missing": body.question_count, "will_call_ai": True}
    av = await asyncio.to_thread(c.bank.check_question_availability, skill.skill_id, body.question_count)
    return {"topic": topic, "canonical_topic": skill.name, "known": True, "plan": av.plan, "eligible": av.eligible, "requested": av.requested, "missing": av.missing,
            "will_call_ai": av.missing > 0}


@router.post("/start", status_code=201)
async def start(body: StartIn, user: str = U, c: Container = C, key: str | None = Depends(idempotency_key)):
    t0 = time.perf_counter()
    if key and (prev := await asyncio.to_thread(c.assessments.attempt_by_key, user, key)):          # refresh / double click / retry: the same session, no work
        return {**_start_payload(_session(c, user, prev)), "resumed": True, "requested_count": body.question_count}
    topic = c.bank.normalize_topic(body.topic)
    skill, registered = await asyncio.to_thread(c.bank.resolve_or_register, topic)
    p = await c.bank.prepare(user, skill, body.question_count, level=body.level, key=key, kind="diagnostic", topic=skill.name if not registered else topic)
    a = await asyncio.to_thread(c.assessments.attempt_row, user, p.attempt_id)
    out = _start_payload(_session(c, user, a))
    n = out["question_count"]
    out.update(requested_count=body.question_count, resumed=not p.created, shortfall=max(0, body.question_count - n),
               note=(f"Only {n} of the {body.question_count} requested questions could be prepared right now." if n < body.question_count else None))
    metrics.inc("diagnostic_start_total", source=p.source)
    metrics.inc("diagnostic_llm_calls_total", p.llm_calls)
    metrics.observe("diagnostic_start_ms", (time.perf_counter() - t0) * 1000, source=p.source)
    return out


def _start_payload(s: dict) -> dict:
    return {"session_id": s["session_id"], "topic": s["topic"], "question_count": s["question_count"], "questions": s["questions"], "generation_source": s["generation_source"],
            "status": "ready", "answers": s["answers"]}


@router.get("/sessions")
def sessions(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100), user: str = U, c: Container = C):
    items, total = c.assessments.list_sessions(user, "diagnostic", limit, offset)
    return {"items": [{"session_id": r["attempt_id"], "topic": r["topic"], "status": r["status"], "question_count": r["total_questions"], "answered_questions": r["answered_questions"],
                       "score_pct": r["score"], "level": _level_for(r["score"]) if r["status"] == "completed" and r["score"] is not None else None, "generation_source": r["generation_source"],
                       "started_at": r["started_at"], "completed_at": r["completed_at"]} for r in items], "total": total, "offset": offset, "limit": limit}


@router.get("/sessions/{session_id}")
def get_session(session_id: str, user: str = U, c: Container = C):
    a = _own(c, user, session_id)
    if a.get("kind") != "diagnostic":
        raise NotFoundError("session not found")
    return _session(c, user, a)


@router.post("/sessions/{session_id}/submit")
def submit(session_id: str, body: SubmitIn, user: str = U, c: Container = C):
    """Server-side grading from the stored snapshots; idempotent. Does not touch proficiency (that is the Assessments experience)."""
    a = _own(c, user, session_id)
    if a.get("kind") != "diagnostic":
        raise NotFoundError("session not found")
    if a["status"] != "completed":
        won = grade_and_finalize(c, user, a, body.answers, add_evidence=False)
        metrics.inc("diagnostic_submitted_total", outcome="first" if won else "duplicate")
        a = c.assessments.attempt_row(user, session_id)
    return _session(c, user, a)
