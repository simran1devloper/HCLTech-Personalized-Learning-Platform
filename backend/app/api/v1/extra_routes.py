"""Read models used by the web app: history and analytics. The AI Tutor lives in `tutor_routes.py`."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query

from app.ai.model_router import Complexity, TaskRequirements
from app.ai.runtime import get_router
from app.ai.types import GenerationParams
from app.api.v1.schemas import Page
from app.core.config import get_settings
from app.core.container import Container, get_container
from app.core.exceptions import AppError, NotFoundError
from app.core.security import rate_limited_user
from app.domains.analytics.service import build_insights, build_summary
from app.domains.learning_intelligence.skill_gap import analyze
from app.domains.roadmap_engine.models import ItemStatus

router = APIRouter(prefix="/api/v1")
C = Depends(get_container)
U = Depends(rate_limited_user)
_DONE = (ItemStatus.COMPLETED, ItemStatus.SKIPPED_MASTERED)


def _names(c: Container) -> dict[str, str]:
    return {s.skill_id: s.name for s in c.registry.repo.list(limit=100000)[0]}


# ---------------- history ----------------
@router.get("/assessments", response_model=Page[dict], tags=["assessments"])
def list_assessments(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100), user: str = U, c: Container = C):
    names = _names(c)
    attempts, total = c.store.list_attempts(user, limit, offset)
    items = [{"assessment_id": a.attempt_id, "skill_id": a.skill_id, "skill": names.get(a.skill_id, a.skill_id), "status": a.status,
              "started_at": a.started_at.isoformat(), "completed_at": a.completed_at.isoformat() if a.completed_at else None,
              "n_questions": len(a.question_ids),
              "score": round(sum(r["earned"] for r in a.results) / len(a.results), 3) if a.results else None} for a in attempts]
    return Page(items=items, total=total, offset=offset, limit=limit)


@router.get("/proficiency/{skill_id}/history", tags=["proficiency"])
def proficiency_history(skill_id: str, user: str = U, c: Container = C):
    skill = c.registry.repo.get(skill_id)
    if not skill:
        raise NotFoundError("skill not found")
    est = c.store.estimates(user).get(skill_id)
    ev = [e.model_dump(mode="json") for e in c.store.evidence_for(user) if e.skill_id == skill_id]
    return {"skill_id": skill_id, "skill": skill.name, "current": est.model_dump(mode="json") if est else None,
            "snapshots": c.store.snapshots(user, skill_id), "evidence": ev}


@router.get("/roadmaps", tags=["roadmaps"])
def list_roadmaps(user: str = U, c: Container = C):
    return {"items": [{"roadmap_id": r.roadmap_id, "goal": r.goal, "version": r.version, "hours_per_week": r.hours_per_week,
                       "estimated_weeks": r.estimated_weeks, "created_at": r.created_at.isoformat(),
                       "milestones_total": len(_live(r)), "milestones_done": sum(m.status in _DONE for m in _live(r))}
                      for r in c.store.list_roadmaps(user)]}


def _live(r):
    return [m for m in r.milestones if m.status != ItemStatus.SUPERSEDED]


@router.get("/roadmaps/{roadmap_id}/versions", tags=["roadmaps"])
def roadmap_versions(roadmap_id: str, user: str = U, c: Container = C):
    vs = c.store.roadmap_versions(user, roadmap_id)
    if not vs:
        raise NotFoundError("roadmap not found")
    return {"items": vs}


# ---------------- analytics ----------------
def _summary(c: Container, user: str) -> dict:
    now = datetime.now(timezone.utc)
    attempts, _ = c.store.list_attempts(user, 1000)
    done = [{"completed_at": a.completed_at or a.started_at, "skill_id": a.skill_id, "n_questions": len(a.question_ids),
             "score": round(sum(r["earned"] for r in a.results) / len(a.results), 3)}
            for a in attempts if a.status == "completed" and a.results]
    rms = [{"roadmap_id": r.roadmap_id, "goal": r.goal, "version": r.version,
            "done": sum(m.status in _DONE for m in _live(r)), "total": len(_live(r))} for r in c.store.list_roadmaps(user)]
    return build_summary(attempts=done, snapshots=c.store.snapshots(user), estimates=c.store.estimates(user),
                         roadmaps=rms, skill_names=_names(c), now=now)


def _readiness_trend(c: Container, user: str, role, max_points: int = 30) -> list[dict]:
    """Readiness replayed as-of each snapshot date, using the latest snapshot per skill known at that time."""
    from app.domains.learning_intelligence.proficiency import ProficiencyEstimate
    snaps = c.store.snapshots(user)
    dates = sorted({r["snapshot_at"][:10] for r in snaps})[-max_points:]
    out = []
    for d in dates:
        latest: dict[str, dict] = {}
        for r in snaps:  # snapshots are ordered by id (chronological)
            if r["snapshot_at"][:10] <= d:
                latest[r["skill_id"]] = r
        est = {sid: ProficiencyEstimate(skill_id=sid, proficiency_score=r["proficiency_score"], confidence_score=r["confidence_score"],
                                        evidence_count=r["evidence_count"], last_assessed_at=None) for sid, r in latest.items()}
        rep = c.careers.readiness(role, est)
        out.append({"date": d, "readiness": rep.readiness, "evidence_confidence": rep.evidence_confidence})
    return out


@router.get("/analytics/summary", tags=["analytics"])
def analytics_summary(user: str = U, c: Container = C):
    s = _summary(c, user)
    goals = c.store.list_goals(user)
    s["career"] = None
    if goals:
        role = c.careers.get_role(goals[-1]["role_slug"])
        if role:
            est = c.store.estimates(user)
            rep = c.careers.readiness(role, est)
            skills = {x.skill_id: x for x in c.registry.repo.list(limit=100000)[0]}
            gap = analyze(c.careers.targets_for(role), est, skills, c.registry.graph(), c.registry.min_levels())
            trend = _readiness_trend(c, user, role)
            s["career"] = {"readiness_trend": trend, "role_slug": role.slug, "title": role.title, "readiness": rep.readiness,
                           "evidence_confidence": rep.evidence_confidence, "critical_gaps": rep.critical_gaps,
                           "unassessed": rep.unassessed, "basis": rep.basis,
                           "gap_summary": {"open_gaps": len(gap.items), "blocked": sum(i.blocked for i in gap.items),
                                           "top": [i.model_dump(include={"skill_id", "name", "current", "target", "gap", "priority", "blocked"}) for i in gap.items[:5]]},
                           "recommended_next": ({"skill_id": gap.recommended_next, "name": skills[gap.recommended_next].name}
                                                if gap.recommended_next else None)}
    s["insights"] = build_insights(s, {"recommended_next_name": s["career"]["recommended_next"]["name"],
                                       "top": s["career"]["gap_summary"]["top"][:1]}
                                   if s["career"] and s["career"]["recommended_next"] else None)
    s["insights_basis"] = "rule-based observations computed from your stored data (not model-generated)"
    return s


@router.post("/analytics/insights/narrate", tags=["analytics"])
async def narrate_insights(user: str = U, c: Container = C):
    """Optional LLM narration of the deterministic facts. Only aggregate numbers (no names/emails) are sent."""
    s = _summary(c, user)
    facts = {k: s[k] for k in ("skills_assessed", "assessments_completed", "consistency", "strongest", "weakest")}
    prompt = ("You are a concise learning coach. In at most 4 short bullet points, summarise these learner statistics "
              "and suggest one concrete next step. Use only the numbers given; do not invent data.\n" + json.dumps(facts, default=str))
    st = get_settings()
    try:
        r = await get_router().generate(prompt, GenerationParams(temperature=0.3, max_output_tokens=300, timeout_s=st.LLM_TIMEOUT_S),
                                        TaskRequirements(complexity=Complexity.LOW, local_only=st.LLM_LOCAL_ONLY, prefer="configured"))
    except Exception:
        raise AppError("The AI service is unavailable right now.", details={})
    return {"text": r.text.strip(), "provider": r.provider, "model": r.model, "basis": "model-generated narration of the figures above"}
