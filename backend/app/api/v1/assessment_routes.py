"""Assessment API: database-first start, autosaved answers, idempotent submit, deterministic results and skill gaps, learning-plan hand-off.

No endpoint here calls a model except ``POST /assessments`` when the bank has *no* questions for the skill (bounded top-up) and the background top-up
it schedules when the bank is short; scoring, gap analysis, history and review are database + arithmetic only."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse

from app.api.v1.schemas import AssessmentCreateIn
from app.core.container import Container, get_container
from app.core.exceptions import ConflictError, NotFoundError
from app.core.security import current_user_id, idempotency, idempotency_key, rate_limited_user
from app.domains.assessments import analysis
from app.domains.assessments.adaptive import attempt_to_evidence
from app.domains.assessments.generation import TERMINAL
from app.domains.assessments.grading import grade
from app.domains.assessments.models import Question, QuestionType
from app.observability.metrics import metrics

import asyncio
from pydantic import BaseModel, Field, field_validator

router = APIRouter(prefix="/api/v1")
C = Depends(get_container)
U = Depends(rate_limited_user)
TYPE_FOR_UI = {"multiple_select": "multiple_select"}  # every other bank type is a single-choice question on the wire


# ------------------------------------------------------------------------------------------------ helpers
def _skill(c: Container, text: str):
    s = c.registry.repo.get(text) or c.registry.resolve(text)
    if not s:
        raise NotFoundError("skill not found")
    return s


def _own(c: Container, user: str, aid: str) -> dict:
    a = c.assessments.attempt_row(user, aid)
    if not a:
        raise NotFoundError("assessment not found")  # not-yours and missing are indistinguishable
    return a


def _snapshots(c: Container, a: dict) -> list[dict]:
    """Rows (with snapshots) for an attempt. Attempts created before the question bank existed have no assignment rows: build them once from the
    legacy in-memory bank and persist, so every attempt is reviewable from the database from then on."""
    rows = c.assessments.attempt_questions(a["attempt_id"])
    if rows:
        return rows
    import json
    from app.infrastructure.database.assessment_store import AQ
    from sqlalchemy import insert
    import uuid
    legacy = [c.questions[q] for q in json.loads(a["question_ids"]) if q in c.questions]
    with c.assessments.db.begin() as conn:
        for i, q in enumerate(legacy):
            snap = {"question_id": q.question_id, "skill_id": q.skill_id, "concept": q.objective or "General", "subtopic": q.objective or "General", "difficulty": q.difficulty,
                    "question_type": "multiple_select" if q.type == QuestionType.MULTI else "single_choice", "cognitive_level": "apply" if q.practical else "understand",
                    "prompt": q.prompt, "code_snippet": None, "options": q.options, "correct": q.correct, "explanation": q.explanation, "solution_approach": "",
                    "practical_example": "", "estimated_time_seconds": 60, "bank_version": q.version, "practical": q.practical}
            conn.execute(insert(AQ).values(id=str(uuid.uuid4()), attempt_id=a["attempt_id"], question_id=q.question_id, question_snapshot=snap, displayed_order=i))
    return c.assessments.attempt_questions(a["attempt_id"])


def _view(row: dict) -> dict:
    """Learner-facing question: never the answer key, explanation or solution."""
    s = row["question_snapshot"]
    return {"question_id": s["question_id"], "type": TYPE_FOR_UI.get(s["question_type"], "multiple_choice"), "kind": s["question_type"], "prompt": s["prompt"],
            "code_snippet": s.get("code_snippet"), "options": s["options"], "difficulty": s["difficulty"], "objective": s["concept"],
            "estimated_time_seconds": s.get("estimated_time_seconds", 60)}


def _as_question(s: dict) -> Question:
    multi = s["question_type"] == "multiple_select"
    return Question(question_id=s["question_id"], skill_id=s["skill_id"], type=QuestionType.MULTI if multi else QuestionType.MCQ, prompt=s["prompt"], difficulty=s["difficulty"],
                    options=s["options"], correct=s["correct"], explanation=s["explanation"], objective=s["concept"], practical=bool(s.get("practical")),
                    provenance="bank", version=s.get("bank_version", 1))


def _shape_ok(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) or (isinstance(v, list) and len(v) <= 12 and all(isinstance(i, int) and not isinstance(i, bool) for i in v)) or (isinstance(v, str) and len(v) <= 5000)


class AnswersIn(BaseModel):
    answers: dict[str, Any] = Field(default_factory=dict, max_length=50)
    times: dict[str, int] | None = Field(None, max_length=50)  # seconds spent per question
    clear: list[str] | None = Field(None, max_length=50)

    @field_validator("answers")
    @classmethod
    def _shapes(cls, v):
        if any(not _shape_ok(x) for x in v.values()):
            raise ValueError("answers must be an option index, a list of option indices, or text")
        return v


class SubmitBody(BaseModel):
    answers: dict[str, Any] = Field(default_factory=dict, max_length=50)  # may be empty: the saved (autosaved) answers are used

    @field_validator("answers")
    @classmethod
    def _shapes(cls, v):
        if any(not _shape_ok(x) for x in v.values()):
            raise ValueError("answers must be an option index, a list of option indices, or text")
        return v


# ------------------------------------------------------------------------------------------------ start
def _generation_payload(c: Container, a: dict, skill_name: str, *, reattached: bool = False) -> dict:
    st = c.generation.state(a)
    rows = c.assessments.attempt_questions(a["attempt_id"])
    return {"assessment_id": a["attempt_id"], "skill_id": a["skill_id"], "skill": skill_name, "status": st["status"], "requested": st["requested"], "total_questions": st["completed"],
            "shortfall": max(0, st["requested"] - st["completed"]), "questions": [_view(r) for r in rows], "generation": st, "reattached": reattached,
            "events_url": f"/api/v1/assessments/{a['attempt_id']}/events", "note": None,
            "source": {"bank_questions": st["completed"], "generated_inline": 0, "llm_calls": 0}}


@router.post("/assessments", status_code=201, tags=["assessments"])
async def create_assessment(body: AssessmentCreateIn, response: Response, user: str = U, c: Container = C, key: str | None = Depends(idempotency_key)):
    """Database-first. Replays safely with the same X-Idempotency-Key (even after a restart).

    * The bank holds the requested number of eligible questions -> **201** with the full attempt, no model call.
    * Otherwise -> **202**: the attempt is created at once with what the bank has, a detached task generates only the missing questions, validates and saves each one,
      and the progress is streamed from ``GET /assessments/{id}/events`` (replayable). ``status`` is ``generating`` until the requested number is assigned.
    * A second Start for a skill that is already generating re-attaches to that attempt instead of generating again."""
    t0 = time.perf_counter()
    if (cached := idempotency.get(user, "assessments", key)) is not None:
        return cached
    skill = await asyncio.to_thread(_skill, c, body.skill)
    prior_attempt = await asyncio.to_thread(c.assessments.attempt_by_key, user, key) if key else None
    active = prior_attempt if prior_attempt and prior_attempt.get("generation_status") else await asyncio.to_thread(c.assessments.active_generation, user, skill.skill_id)
    if active:   # same key replayed, or a second Start while this skill is already generating: attach to that attempt, never generate twice
        await asyncio.to_thread(c.generation.reap_if_stale, active)
        active = await asyncio.to_thread(c.assessments.attempt_by_id, active["attempt_id"])
        if active["generation_status"] == "generating" or (prior_attempt and active["attempt_id"] == prior_attempt["attempt_id"]):
            metrics.inc("assessment_start_total", outcome="reattached")
            out = _generation_payload(c, active, skill.name, reattached=True)
            response.status_code = 202 if out["status"] == "generating" else 200
            return out
    est = (await asyncio.to_thread(c.store.estimates, user)).get(skill.skill_id)
    prior = est.proficiency_score if est else None
    aid, created, sel, _ = await asyncio.to_thread(c.bank.start, user, skill.skill_id, body.n_questions, prior, key, generate_missing=True)
    row = await asyncio.to_thread(c.assessments.attempt_by_id, aid)
    if row and row.get("generation_status") == "generating":
        if created:
            c.generation.launch(row, skill.name, prior)
        metrics.inc("assessment_start_total", outcome="generating")
        metrics.observe("assessment_start_ms", (time.perf_counter() - t0) * 1000)
        response.status_code = 202
        return _generation_payload(c, row, skill.name, reattached=not created)
    rows = await asyncio.to_thread(c.assessments.attempt_questions, aid)
    qs = [_view(r) for r in rows]
    out = {"assessment_id": aid, "skill_id": skill.skill_id, "skill": skill.name, "status": "open", "questions": qs, "requested": body.n_questions, "total_questions": len(qs),
           "shortfall": max(0, body.n_questions - len(qs)), "estimated_minutes": max(1, round(sum(q["estimated_time_seconds"] for q in qs) / 60)),
           "source": {"bank_questions": len(qs), "generated_inline": 0, "llm_calls": 0}, "note": None}
    idempotency.put(user, "assessments", key, out)
    metrics.inc("assessment_start_total", outcome="bank")
    metrics.observe("assessment_start_ms", (time.perf_counter() - t0) * 1000)
    return out


def _sse(ev: dict) -> bytes:
    return f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {json.dumps(ev['payload'], default=str)}\n\n".encode()


@router.get("/assessments/generations/active", tags=["assessments"])
def active_generation(skill: str | None = Query(None, max_length=80), user: str = Depends(current_user_id), c: Container = C):
    """The learner's generating assessment (page refresh / another tab), or ``{"active": null}``. Stale generations are closed first."""
    sid = _skill(c, skill).skill_id if skill else None
    a = c.assessments.active_generation(user, sid)
    if a and c.generation.reap_if_stale(a):
        a = c.assessments.active_generation(user, sid)
    if not a:
        return {"active": None}
    return {"active": _generation_payload(c, a, _skill_name(c, a["skill_id"]), reattached=True)}


@router.get("/assessments/{assessment_id}/generation", tags=["assessments"])
def generation_state(assessment_id: str, user: str = Depends(current_user_id), c: Container = C):
    """State recovery without streaming: status, saved/requested counts and the saved questions (no answers)."""
    a = _own(c, user, assessment_id)
    if c.generation.reap_if_stale(a):
        a = _own(c, user, assessment_id)
    return c.generation.state(a)


@router.get("/assessments/{assessment_id}/events", tags=["assessments"])
async def generation_events(assessment_id: str, request: Request, after: int = Query(0, ge=0), user: str = Depends(current_user_id), c: Container = C):
    """Server-Sent Events for one generation: replays every event after ``after`` (or the ``Last-Event-ID`` header on an automatic reconnect), then follows live.
    The stream ends after ``completed`` / ``error`` / ``cancelled``. Events come from the database, so any process can serve a reconnect."""
    a = await asyncio.to_thread(_own, c, user, assessment_id)
    if not a.get("generation_status"):
        raise NotFoundError("this assessment was not generated")
    try:
        cursor = max(after, int(request.headers.get("last-event-id", 0) or 0))
    except ValueError:
        cursor = after
    gen = c.generation

    async def stream():
        nonlocal cursor
        ev, entry = gen.subscribe(assessment_id)
        idle, quiet = 0.0, 0
        try:
            yield b"retry: 2000\n\n"
            while True:
                ev.clear()   # clear BEFORE reading: an event written between the read and the wait still wakes us
                rows = await asyncio.to_thread(c.assessments.events_after, assessment_id, cursor)
                for r in rows:
                    cursor = r["seq"]
                    yield _sse(r)
                    if r["type"] in TERMINAL:
                        return
                if rows:
                    idle, quiet = 0.0, 0
                    continue
                cur = await asyncio.to_thread(c.assessments.attempt_by_id, assessment_id)
                if cur and cur["generation_status"] != "generating":
                    quiet += 1
                    if quiet >= 3:   # terminal state with every event already delivered
                        return
                elif cur:
                    await asyncio.to_thread(gen.reap_if_stale, cur)
                try:
                    await asyncio.wait_for(ev.wait(), 1.0)
                except asyncio.TimeoutError:
                    idle += 1.0
                    if idle >= 15.0:
                        idle = 0.0
                        yield b": keep-alive\n\n"
                if await request.is_disconnected():
                    return
        finally:
            gen.unsubscribe(assessment_id, entry)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/assessments/{assessment_id}/generation/cancel", tags=["assessments"])
def cancel_generation(assessment_id: str, user: str = Depends(current_user_id), c: Container = C):
    """Stop generating. Questions already validated and saved stay assigned (and in the bank). Idempotent: cancelling a finished generation is a no-op."""
    a = _own(c, user, assessment_id)
    c.generation.cancel(user, assessment_id)
    return c.generation.state(c.assessments.attempt_by_id(a["attempt_id"]) or a)


# ------------------------------------------------------------------------------------------------ open / autosave
@router.get("/assessments/{assessment_id}", tags=["assessments"])
def get_assessment(assessment_id: str, user: str = U, c: Container = C):
    """Re-open an attempt (refresh, another device): questions without answers, plus the answers already saved."""
    a = _own(c, user, assessment_id)
    if c.generation.reap_if_stale(a):
        a = _own(c, user, assessment_id)
    rows = _snapshots(c, a)
    names = {s.skill_id: s.name for s in c.registry.repo.list(limit=100000)[0]}
    return {"assessment_id": a["attempt_id"], "skill_id": a["skill_id"], "skill": names.get(a["skill_id"], a["skill_id"]), "status": a["status"], "started_at": a["started_at"],
            "total_questions": len(rows), "answered_questions": sum(1 for r in rows if r["selected_answer"] is not None), "questions": [_view(r) for r in rows],
            "answers": {r["question_snapshot"]["question_id"]: r["selected_answer"] for r in rows if r["selected_answer"] is not None} if a["status"] == "open" else {},
            "learning_session_id": a.get("learning_session_id"), "requested": a.get("requested_questions") or len(rows),
            "generation_status": a.get("generation_status")}


@router.put("/assessments/{assessment_id}/answers", tags=["assessments"])
def save_answers(assessment_id: str, body: AnswersIn, user: str = U, c: Container = C):
    """Autosave: stores answers as they are chosen (idempotent, order-independent). 409 once submitted or for foreign question ids."""
    a = _own(c, user, assessment_id)
    if a.get("generation_status") == "generating":
        raise ConflictError("questions are still being generated")
    _snapshots(c, a)
    try:
        r = c.assessments.save_answers(user, assessment_id, body.answers, body.times)
        for qid in body.clear or []:
            c.assessments.clear_answer(user, assessment_id, qid)
    except KeyError as e:
        if e.args[0] == "closed":
            raise ConflictError("assessment already submitted")
        raise ConflictError("answers reference questions outside this assessment")
    if r is None:
        raise NotFoundError("assessment not found")
    return {"saved": len(body.answers), "answered_questions": r["answered"], "total_questions": a["total_questions"] or len(c.assessments.attempt_questions(assessment_id))}


# ------------------------------------------------------------------------------------------------ submit / results
def _skill_name(c: Container, skill_id: str) -> str:
    s = c.registry.repo.get(skill_id)
    return s.name if s else skill_id


def _items(rows: list[dict]) -> list[dict]:
    return [{"concept": r["question_snapshot"]["concept"], "difficulty": r["question_snapshot"]["difficulty"], "question_type": r["question_snapshot"]["question_type"],
             "cognitive_level": r["question_snapshot"]["cognitive_level"], "earned": r["earned"] if r["earned"] is not None else 0.0} for r in rows]


def _prereq_gaps(c: Container, user: str, skill_id: str) -> list[dict]:
    """Prerequisite skills (from the skill graph) the learner has not shown enough proficiency in. Uses stored proficiency only."""
    g, est, out = c.registry.graph(), c.store.estimates(user), []
    mins = c.registry.min_levels()
    for pid in sorted(g.direct_prerequisites(skill_id)):
        need = mins.get((skill_id, pid), 2.0)
        have = est.get(pid)
        score = have.proficiency_score if have else None
        if score is None or score < need:
            p = c.registry.repo.get(pid)
            out.append({"skill_id": pid, "name": p.name if p else pid, "required": need, "current": score})
    return out[:4]


def _results(c: Container, user: str, a: dict) -> dict:
    rows = c.assessments.attempt_questions(a["attempt_id"])
    graded = [r for r in rows if r["earned"] is not None]
    n = len(rows)
    score_pct = round(100 * sum(r["earned"] for r in graded) / n, 1) if n else None
    est = c.store.estimates(user).get(a["skill_id"])
    concepts = c.assessments.concept_results(a["attempt_id"])
    items = _items(rows)
    report = analysis.gap_report(_skill_name(c, a["skill_id"]), a["skill_id"], items, analysis.concept_results(items) if not concepts else _fmt(concepts), _prereq_gaps(c, user, a["skill_id"]))
    t = [r["time_taken_seconds"] for r in rows if r["time_taken_seconds"] is not None]
    legacy = [{"question_id": r["question_snapshot"]["question_id"], "earned": r["earned"] or 0.0, "correct": bool(r["is_correct"]), "feedback": r["question_snapshot"]["explanation"],
               "needs_review": False} for r in rows]
    started = datetime.fromisoformat(a["started_at"])
    submitted = a["submitted_at"] or datetime.now(timezone.utc)
    return {"assessment_id": a["attempt_id"], "status": a["status"], "skill_id": a["skill_id"], "skill": report["skill"], "score": round(score_pct / 100, 3) if score_pct is not None else None,
            "score_pct": score_pct, "correct_count": sum(1 for r in rows if r["is_correct"]), "incorrect_count": sum(1 for r in rows if r["is_correct"] is False), "total_questions": n,
            "answered_questions": a["answered_questions"], "time_taken_seconds": int(sum(t)) if t else max(0, int((submitted - (started if started.tzinfo else started.replace(tzinfo=timezone.utc))).total_seconds())),
            "proficiency_level": report["overall_level"], "difficulty_level": a["difficulty_level"], "results": legacy,
            "proficiency": est.model_dump(mode="json") if est and a["status"] == "completed" else None, "concepts": report["concepts"], "skill_gaps": report,
            "review": [{"question_id": r["question_snapshot"]["question_id"], "prompt": r["question_snapshot"]["prompt"], "code_snippet": r["question_snapshot"].get("code_snippet"),
                        "options": r["question_snapshot"]["options"], "selected": r["selected_answer"], "correct_options": r["question_snapshot"]["correct"], "is_correct": r["is_correct"],
                        "explanation": r["question_snapshot"]["explanation"], "solution_approach": r["question_snapshot"].get("solution_approach"), "concept": r["question_snapshot"]["concept"],
                        "difficulty": r["question_snapshot"]["difficulty"]} for r in rows],
            "learning_session_id": a.get("learning_session_id")}


def _fmt(db_rows: list[dict]) -> list[dict]:
    return [{"concept": r["concept"], "questions_attempted": r["questions_attempted"], "correct_answers": r["correct_answers"], "accuracy": r["accuracy"],
             "proficiency_level": r["proficiency_level"], "identified_gap": r["identified_gap"]} for r in db_rows]


def grade_and_finalize(c: Container, user: str, a: dict, body_answers: dict, *, add_evidence: bool) -> bool:
    """Grade from the stored snapshots and finalize the attempt (compare-and-set: exactly one caller wins). Returns True for the winning caller.
    ``add_evidence`` feeds proficiency; the Diagnostic Study sets it False because it is a separate experience from skill assessments."""
    rows = _snapshots(c, a)
    ids = {r["question_snapshot"]["question_id"] for r in rows}
    unknown = set(body_answers) - ids
    if unknown:
        raise ConflictError("answers reference questions outside this assessment", details={"unknown": sorted(unknown)})
    answers = {r["question_snapshot"]["question_id"]: r["selected_answer"] for r in rows if r["selected_answer"] is not None} | body_answers
    qs = {r["question_snapshot"]["question_id"]: _as_question(r["question_snapshot"]) for r in rows}
    grades = [grade(qs[qid], answers.get(qid)) for qid in qs]
    gmap = {g.question_id: {"earned": g.earned, "is_correct": g.correct} for g in grades}
    n = len(grades)
    earned = sum(g.earned for g in grades)
    score = round(100 * earned / n, 1) if n else 0.0
    items = [{"concept": r["question_snapshot"]["concept"], "earned": gmap[r["question_snapshot"]["question_id"]]["earned"]} for r in rows]
    crs = [{"skill_id": a["skill_id"], **{k: v for k, v in r.items()}} for r in analysis.concept_results(items)]
    won = c.assessments.finalize(user_id=user, attempt_id=a["attempt_id"], grades=gmap, concept_rows=crs, score=score, correct=earned,
                                 answered=len([1 for v in answers.values() if v not in (None, [], "")]), legacy_results=[g.model_dump() for g in grades])
    if won and add_evidence and answers:  # proficiency is only recalculated for an attempt with at least one real answer, and only by the winning caller
        c.store.add_evidence(user, attempt_to_evidence(qs, grades, datetime.now(timezone.utc), a["attempt_id"]))
    return won


@router.post("/assessments/{assessment_id}/submit", tags=["assessments"])
def submit_assessment(assessment_id: str, body: SubmitBody, user: str = U, c: Container = C):
    """Grades from the stored snapshots (the bank may have changed since). Idempotent: a repeated submission returns the stored result and changes nothing."""
    t0 = time.perf_counter()
    a = _own(c, user, assessment_id)
    if a.get("generation_status") == "generating":
        raise ConflictError("questions are still being generated")
    if a.get("kind") == "diagnostic":
        raise ConflictError("this is a Diagnostic Study session; submit it from the Diagnostic Study")  # keeps its answers out of proficiency evidence
    _snapshots(c, a)
    if a["status"] == "completed":
        return {**_results(c, user, a), "already_submitted": True}
    won = grade_and_finalize(c, user, a, body.answers, add_evidence=True)
    if won:
        metrics.inc("assessment_submitted_total")
    metrics.observe("assessment_submit_ms", (time.perf_counter() - t0) * 1000)
    return {**_results(c, user, c.assessments.attempt_row(user, assessment_id)), "already_submitted": not won}


@router.get("/assessments/{assessment_id}/results", tags=["assessments"])
def assessment_results(assessment_id: str, user: str = U, c: Container = C):
    a = _own(c, user, assessment_id)
    if a["status"] != "completed":
        raise ConflictError("assessment not submitted yet")
    return _results(c, user, a)


@router.get("/assessments/{assessment_id}/skill-gaps", tags=["assessments"])
def assessment_skill_gaps(assessment_id: str, user: str = U, c: Container = C):
    """Structured, deterministic skill-gap report for a submitted attempt (no model call)."""
    a = _own(c, user, assessment_id)
    if a["status"] != "completed":
        raise ConflictError("assessment not submitted yet")
    return _results(c, user, a)["skill_gaps"]


@router.post("/assessments/{assessment_id}/generate-learning-plan", status_code=201, tags=["assessments"])
def generate_learning_plan(assessment_id: str, user: str = U, c: Container = C):
    """Creates (once) a content-history session targeting only this attempt's gaps and links it to the attempt. Generation then runs through the
    normal session endpoints (open the session; ``continue`` generates the sections). Repeated calls return the same session."""
    from services.learning_plan import create_plan_session
    a = _own(c, user, assessment_id)
    if a["status"] != "completed":
        raise ConflictError("assessment not submitted yet")
    existing = a.get("learning_session_id")
    if existing and c.history.get_session(user, existing):
        return {"session_id": existing, "created": False}
    report = _results(c, user, a)["skill_gaps"]
    session = create_plan_session(c.history, user, report, assessment_id)
    if not c.assessments.link_learning_session(user, assessment_id, session["id"]):  # lost a race with a concurrent click: use the winner's session
        c.history.delete(user, session["id"])
        a2 = c.assessments.attempt_row(user, assessment_id)
        return {"session_id": a2["learning_session_id"], "created": False}
    metrics.inc("learning_plan_created_total")
    return {"session_id": session["id"], "created": True, "plan": session["metadata"]["plan"]}


# ------------------------------------------------------------------------------------------------ question-bank metadata
@router.get("/assessment-question-bank/skills", tags=["assessments"])
def bank_skills(q: str | None = Query(None, max_length=80), user: str = U, c: Container = C):
    """Skills with how many validated questions the bank holds, the learner's current proficiency, and a 'recommended' flag. Cached metadata only."""
    avail, est = c.bank.availability(), c.store.estimates(user)
    skills = [s for s in c.registry.repo.list(limit=100000)[0] if getattr(s, "status", "active") != "deprecated"]
    term = (q or "").strip().lower()
    out = []
    for s in skills:
        if term and term not in s.name.lower() and not any(term in a.lower() for a in s.aliases):
            continue
        a = avail.get(s.skill_id)
        e = est.get(s.skill_id)
        out.append({"skill_id": s.skill_id, "name": s.name, "category": s.category, "question_count": a["total"] if a else 0, "concepts": a["concepts"] if a else 0,
                    "proficiency": e.proficiency_score if e else None, "assessed": bool(e), "est_seconds_per_question": a["avg_seconds"] if a else 60})
    ranked = sorted((x for x in out if x["question_count"] >= 10), key=lambda x: (x["assessed"], x["proficiency"] if x["proficiency"] is not None else -1, x["name"]))
    rec = {x["skill_id"] for x in ranked[:4]}
    for x in out:
        x["recommended"] = x["skill_id"] in rec
    out.sort(key=lambda x: (-x["recommended"], -(x["question_count"] > 0), x["name"]))
    return {"items": out, "total": len(out)}


@router.get("/assessment-question-bank/skills/{skill_id}/availability", tags=["assessments"])
def bank_availability(skill_id: str, user: str = U, c: Container = C):
    s = _skill(c, skill_id)
    a = c.bank.availability().get(s.skill_id) or {"total": 0, "concepts": 0, "by_difficulty": {}, "avg_seconds": 60}
    return {"skill_id": s.skill_id, "name": s.name, **a, "ready_for": [n for n in (3, 5, 10, 15, 20) if a["total"] >= n],
            "estimated_minutes": {str(n): max(1, round(n * a["avg_seconds"] / 60)) for n in (3, 5, 10, 15, 20)}}


@router.get("/assessment-question-bank/skills/{skill_id}/generation-progress", tags=["assessments"])
def bank_generation_progress(skill_id: str, user: str = U, c: Container = C):
    """How much of the question generation for this skill is done (``percent``), polled by the Assessments page while Start is waiting. Reads memory only."""
    s = _skill(c, skill_id)
    return {"skill_id": s.skill_id, **c.bank.generation_progress(s.skill_id)}


@router.post("/assessment-question-bank/skills/{skill_id}/generation/stop", tags=["assessments"])
def bank_stop_generation(skill_id: str, user: str = U, c: Container = C):
    """Stop the running generation for this skill. Questions already generated and validated stay saved."""
    s = _skill(c, skill_id)
    return {"skill_id": s.skill_id, "stopped": c.bank.stop_generation(s.skill_id), **c.bank.generation_progress(s.skill_id)}


@router.get("/assessment-question-bank/skills/{skill_id}/topics", tags=["assessments"])
def bank_topics(skill_id: str, user: str = U, c: Container = C):
    s = _skill(c, skill_id)
    return {"skill_id": s.skill_id, "items": c.assessments.topics(s.skill_id)}
