"""Study workspace API: per-learner reading state (understood / bookmark / note), the generation pipeline report, and per-section knowledge checks.

Nothing here calls a model. Knowledge checks reuse the question bank and the attempt tables (kind='knowledge_check', so they never appear in Assessments or
Diagnostic history and never feed proficiency); answers stay on the server until submission. Ownership is enforced by the stores (a foreign id is a plain 404)."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.v1.assessment_routes import _own, _snapshots, _view, grade_and_finalize
from app.api.v1.history_routes import _order, _session_or_404
from app.core.container import Container, get_container
from app.core.exceptions import NotFoundError
from app.core.security import rate_limited_user
from app.observability.metrics import metrics
from services import session_generation as sg

router = APIRouter(prefix="/api/v1", tags=["study-workspace"])
C = Depends(get_container)
U = Depends(rate_limited_user)


class StateIn(BaseModel):
    understood: bool | None = None
    bookmarked: bool | None = None
    note: str | None = Field(None, max_length=5000)
    opened: bool = False                      # the learner opened the section (reading progress)


class CheckIn(BaseModel):
    round: int = Field(0, ge=0, le=50)       # a new round gives a fresh set; the same round is idempotent (refresh safe)
    n: int = Field(3, ge=1, le=5)


class CheckSubmit(BaseModel):
    answers: dict = Field(default_factory=dict, max_length=10)


def _valid_key(session: dict, key: str) -> bool:
    return key in _order(session) or sg.is_known_section(key)


@router.get("/content-sessions/{session_id}/state")
def get_state(session_id: str, user: str = U, c: Container = C):
    s = _session_or_404(c, user, session_id)
    st = c.history.get_states(user, session_id) or {}
    order = _order(s)
    core = [k for k in order]
    done = sum(1 for k in core if st.get(k, {}).get("understood"))
    read = sum(1 for k in core if st.get(k, {}).get("opened_at"))
    return {"sections": st, "progress": {"total": len(core), "understood": done, "opened": read, "bookmarked": sum(1 for v in st.values() if v["bookmarked"]),
                                          "understood_pct": round(100 * done / len(core)) if core else 0, "reading_pct": round(100 * read / len(core)) if core else 0}}


@router.put("/content-sessions/{session_id}/sections/{section_key}/state")
def put_state(session_id: str, section_key: str, body: StateIn, user: str = U, c: Container = C):
    s = _session_or_404(c, user, session_id)
    if not _valid_key(s, section_key):
        raise NotFoundError("section not found")
    r = c.history.set_state(user, session_id, section_key, understood=body.understood, bookmarked=body.bookmarked, note=body.note, opened=body.opened)
    if r is None:
        raise NotFoundError("content session not found")
    return r


@router.get("/content-sessions/{session_id}/pipeline")
def pipeline(session_id: str, user: str = U, c: Container = C):
    """What the generation really did, per section: status, reused vs generated, tokens, latency, attempts and the last error. Read-only; no model call."""
    s = _session_or_404(c, user, session_id)
    rows = c.history.pipeline_rows(user, session_id, _order(s)) or []
    done = [r for r in rows if r["status"] == "COMPLETED"]
    gen = [r for r in done if r["source"] == "generated"]
    return {"sections": rows, "active_jobs": len(c.history.jobs_for_session(user, session_id, active_only=True) or []),
            "totals": {"sections": len(rows), "completed": len(done), "generated": len(gen), "reused": len(done) - len(gen), "failed": sum(1 for r in rows if r["status"] == "FAILED"),
                       "input_tokens": sum(r["input_tokens"] for r in rows), "output_tokens": sum(r["output_tokens"] for r in rows),
                       "latency_ms": round(sum(r["latency_ms"] for r in gen))},
            "profile": (s.get("context") or {}).get("profile")}


@router.post("/content-sessions/{session_id}/sections/{section_key}/knowledge-check")
async def start_check(session_id: str, section_key: str, body: CheckIn | None = None, user: str = U, c: Container = C):
    """Up to ``n`` questions from the saved question bank for this session's topic (weak concept areas first). Never generates: with no saved questions the
    answer is ``available: false`` and the UI says so."""
    body = body or CheckIn()
    s = await asyncio.to_thread(_session_or_404, c, user, session_id)
    if not _valid_key(s, section_key):
        raise NotFoundError("section not found")
    skill = await asyncio.to_thread(c.bank.resolve_skill, s["topic"])
    weak = tuple(((s.get("context") or {}).get("profile") or {}).get("weak") or [])
    if skill:
        aid, _created, sel, _ = await asyncio.to_thread(c.bank.start, user, skill.skill_id, body.n, None, f"kc:{session_id}:{section_key}:{body.round}", kind="knowledge_check",
                                                        topic=s["topic"], source="question_bank", prefer_concepts=weak)
        if sel.rows:
            a = await asyncio.to_thread(c.assessments.attempt_row, user, aid)
            rows = await asyncio.to_thread(_snapshots, c, a)
            metrics.inc("knowledge_check_started_total")
            return {"available": True, "check_id": aid, "status": a["status"], "questions": [_view(r) for r in rows], "answers": {}}
    return {"available": False, "check_id": None, "questions": [], "message": "There are no saved questions for this topic yet, so there is no knowledge check to take."}


@router.post("/knowledge-checks/{check_id}/submit")
def submit_check(check_id: str, body: CheckSubmit, user: str = U, c: Container = C):
    a = _own(c, user, check_id)
    if a.get("kind") != "knowledge_check":
        raise NotFoundError("knowledge check not found")
    rows = _snapshots(c, a)
    if a["status"] != "completed":
        grade_and_finalize(c, user, a, body.answers, add_evidence=False)
        metrics.inc("knowledge_check_submitted_total")
        rows = c.assessments.attempt_questions(check_id)
    correct = sum(1 for r in rows if r["is_correct"])
    return {"check_id": check_id, "score": correct, "total": len(rows), "passed": bool(rows) and correct / len(rows) >= 0.67,
            "results": [{"question_id": r["question_snapshot"]["question_id"], "correct": bool(r["is_correct"]), "selected": r["selected_answer"],
                         "correct_options": r["question_snapshot"]["correct"], "explanation": r["question_snapshot"]["explanation"]} for r in rows]}
