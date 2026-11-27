"""Skill Explorer API: career skill graph, skill detail, prerequisites/dependents, search, readiness, recommendations and
validated evidence. Thin controllers over `ExplorerService`; no model calls."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.schemas import Page
from app.core.container import Container, get_container
from app.core.exceptions import ValidationFailure
from app.core.security import rate_limited_user
from app.domains.learning_intelligence.proficiency import Evidence, EvidenceKind
from app.domains.skill_intelligence.explorer import ExplorerService
from app.domains.skill_intelligence.mastery import MANUAL_PREFIX, SkillStatus
from app.domains.skill_intelligence.models import CareerSkillGraph, GraphNode, Recommendation

router = APIRouter(prefix="/api/v1", tags=["skill-explorer"])
C = Depends(get_container)
U = Depends(rate_limited_user)
Slug = Query(..., min_length=1, max_length=80)


def _svc(c: Container) -> ExplorerService:
    return c.explorer  # type: ignore[return-value]


class EvidenceIn(BaseModel):
    """Practical evidence only. Quiz/assessment evidence is produced by graded attempts and cannot be posted here, and
    there is no 'mark as complete': a claim must carry a score, a difficulty and a reference to the work."""
    model_config = ConfigDict(extra="forbid")
    kind: Literal["exercise", "coding", "project"]
    score: float = Field(ge=0, le=1)
    difficulty: float = Field(ge=1, le=5)
    n_items: int = Field(1, ge=1, le=10)
    source_id: str = Field(min_length=3, max_length=48, pattern=r"^[A-Za-z0-9_.:/-]+$")  # e.g. repo/PR/exercise id: traceability
    occurred_at: datetime | None = None


@router.get("/careers/{slug}/skill-graph", response_model=CareerSkillGraph)
def career_skill_graph(slug: str, user: str = U, c: Container = C):
    """Nodes (skills in the career + their prerequisites), edges, statuses, metrics and recommendations in ONE round trip."""
    return _svc(c).career_graph(user, slug)


@router.get("/skill-graph", response_model=CareerSkillGraph)
def all_skills_graph(user: str = U, c: Container = C):
    """Career-agnostic graph of every skill with the learner's statuses (no readiness or recommendations: those need a career)."""
    return _svc(c).career_graph(user, None)


@router.get("/careers/{slug}/readiness-breakdown")
def readiness_breakdown(slug: str, user: str = U, c: Container = C):
    """Coverage, mastery and readiness as separate metrics (see domains/skill_intelligence/mastery.py for the formulas)."""
    g = _svc(c).career_graph(user, slug)
    return {"role_slug": slug, "metrics": g.metrics, "critical_gaps": [n for n in g.nodes if n.skill_id in set(g.metrics.critical_gaps)]}


@router.get("/careers/{slug}/recommendations", response_model=list[Recommendation])
def career_recommendations(slug: str, limit: int = Query(5, ge=1, le=20), user: str = U, c: Container = C):
    return _svc(c).recommendations(user, slug, limit)


@router.get("/skills/search", response_model=Page[GraphNode])
def search_skills(q: str | None = Query(None, max_length=80), category: str | None = Query(None, max_length=80),
                  status: SkillStatus | None = None, role: str | None = Query(None, max_length=80),
                  offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200), user: str = U, c: Container = C):
    items, total = _svc(c).search(user, q=q, category=category, status=status, role_slug=role, offset=offset, limit=limit)
    return Page(items=items, total=total, offset=offset, limit=limit)


@router.get("/skills/{skill_id}/detail")
def skill_detail(skill_id: str, role: str | None = Query(None, max_length=80), user: str = U, c: Container = C):
    return _svc(c).skill_detail(user, skill_id, role)


@router.get("/skills/{skill_id}/prerequisites", response_model=list[GraphNode])
def skill_prerequisites(skill_id: str, role: str | None = Query(None, max_length=80), transitive: bool = True, user: str = U, c: Container = C):
    return _svc(c).closure(user, skill_id, role, upstream=True, transitive=transitive)


@router.get("/skills/{skill_id}/dependents", response_model=list[GraphNode])
def skill_dependents(skill_id: str, role: str | None = Query(None, max_length=80), transitive: bool = True, user: str = U, c: Container = C):
    return _svc(c).closure(user, skill_id, role, upstream=False, transitive=transitive)


@router.post("/skills/{skill_id}/evidence", status_code=201)
def add_practical_evidence(skill_id: str, body: EvidenceIn, role: str | None = Query(None, max_length=80), user: str = U, c: Container = C):
    """Record practical evidence. It feeds the same deterministic estimator as assessments (prof-v1) but is flagged as
    manually submitted, so it can raise proficiency/confidence yet can never mark a skill mastered without a graded assessment."""
    svc = _svc(c)
    skill = svc.resolve(skill_id)
    now = datetime.now(timezone.utc)
    when = body.occurred_at or now
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    if when > now + timedelta(minutes=5):
        raise ValidationFailure("occurred_at cannot be in the future")
    c.store.add_evidence(user, [Evidence(skill_id=skill.skill_id, kind=EvidenceKind(body.kind), score=body.score, difficulty=body.difficulty,
                                         n_items=body.n_items, occurred_at=when, source_id=f"{MANUAL_PREFIX}{body.source_id}")])
    return svc.skill_detail(user, skill.skill_id, role)
