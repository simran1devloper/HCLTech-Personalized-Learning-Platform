"""v1 REST API. Thin controllers: validation + authorization here, logic in domains."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, Query

from app.api.v1.schemas import (DiscoverSkillsIn, GoalIn, Page, ResourceDiscoverIn, RoadmapGenerateIn, SkillOut)
from app.core.container import Container, get_container
from app.core.exceptions import NotFoundError
from app.core.security import idempotency, idempotency_key, rate_limited_user
from app.domains.learning_intelligence.skill_gap import analyze
from app.domains.roadmap_engine.engine import complete_milestone, generate, recompile

router = APIRouter(prefix="/api/v1")
C = Depends(get_container)
U = Depends(rate_limited_user)


def _skill_out(c: Container, s) -> SkillOut:
    g = c.registry.graph()
    return SkillOut(**s.model_dump(mode="json", exclude={"created_at", "updated_at", "estimated_hours"}),
                    prerequisites=sorted(g.direct_prerequisites(s.skill_id)))


def _skill_or_404(c: Container, text: str):
    s = c.registry.repo.get(text) or c.registry.resolve(text)
    if not s:
        raise NotFoundError("skill not found")
    return s


# ---------------- skills ----------------
@router.get("/skills", response_model=Page[SkillOut], tags=["skills"])
def list_skills(category: str | None = None, q: str | None = Query(None, max_length=80), status: str | None = None,
                offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200), user: str = U, c: Container = C):
    items, total = c.registry.repo.list(category=category, status=status, q=q, offset=offset, limit=limit)
    return Page(items=[_skill_out(c, s) for s in items], total=total, offset=offset, limit=limit)


@router.get("/skills/{skill_id}", response_model=SkillOut, tags=["skills"])
def get_skill(skill_id: str, user: str = U, c: Container = C):
    return _skill_out(c, _skill_or_404(c, skill_id))


@router.post("/skills/discover", response_model=list[SkillOut], tags=["skills"])
def discover_skills(body: DiscoverSkillsIn, user: str = U, c: Container = C):
    """Maps free text to canonical skills; unknown names are registered as PROPOSED (never duplicated)."""
    return [_skill_out(c, c.registry.get_or_propose(n)) for n in body.names]


# ---------------- goals & careers ----------------
@router.get("/careers", tags=["careers"])
def list_careers(user: str = U, c: Container = C):
    return [{"slug": r.slug, "title": r.title, "requirements": [x.model_dump() for x in r.requirements]}
            for r in c.careers.list_roles()]


@router.post("/goals", status_code=201, tags=["goals"])
def create_goal(body: GoalIn, user: str = U, c: Container = C):
    if not c.careers.get_role(body.role_slug):
        raise NotFoundError("unknown career role")
    goal = {"goal_id": str(uuid4()), "role_slug": body.role_slug, "hours_per_week": body.hours_per_week,
            "created_at": datetime.now(timezone.utc).isoformat()}
    return c.store.add_goal(user, goal)


@router.get("/goals", tags=["goals"])
def list_goals(user: str = U, c: Container = C):
    return c.store.list_goals(user)


@router.get("/careers/{slug}/readiness", tags=["careers"])
def readiness(slug: str, user: str = U, c: Container = C):
    role = c.careers.get_role(slug)
    if not role:
        raise NotFoundError("unknown career role")
    return c.careers.readiness(role, c.store.estimates(user))


# assessments live in assessment_routes.py (question bank, attempts, results, skill gaps, learning plans)


# ---------------- proficiency / gaps ----------------
@router.get("/proficiency", tags=["proficiency"])
def proficiency(user: str = U, c: Container = C):
    return {"items": [e.model_dump(mode="json") for e in c.store.estimates(user).values()]}


@router.get("/skill-gaps", tags=["proficiency"])
def skill_gaps(role: str = Query(..., max_length=80), user: str = U, c: Container = C):
    r = c.careers.get_role(role)
    if not r:
        raise NotFoundError("unknown career role")
    skills = {s.skill_id: s for s in c.registry.repo.list(limit=100000)[0]}
    return analyze(c.careers.targets_for(r), c.store.estimates(user), skills, c.registry.graph(), c.registry.min_levels())


# ---------------- roadmaps ----------------
def _skills(c): return {s.skill_id: s for s in c.registry.repo.list(limit=100000)[0]}


def _own_roadmap(c: Container, user: str, rid: str):
    versions = c.store.roadmap_versions(user, rid)
    if not versions:
        raise NotFoundError("roadmap not found")
    return versions


@router.post("/roadmaps/generate", status_code=201, tags=["roadmaps"])
def generate_roadmap(body: RoadmapGenerateIn, user: str = U, c: Container = C, key: str | None = Depends(idempotency_key)):
    if (cached := idempotency.get(user, "roadmaps", key)) is not None:
        return cached
    role = c.careers.get_role(body.role_slug)
    if not role:
        raise NotFoundError("unknown career role")
    rm = generate(user, role.slug, c.careers.targets_for(role), c.store.estimates(user), _skills(c), c.registry.graph(),
                  hours_per_week=body.hours_per_week)
    c.store.save_roadmap(rm)
    idempotency.put(user, "roadmaps", key, rm)
    return rm


@router.get("/roadmaps/{roadmap_id}", tags=["roadmaps"])
def get_roadmap(roadmap_id: str, version: int | None = None, user: str = U, c: Container = C):
    versions = _own_roadmap(c, user, roadmap_id)
    if version is None:
        return versions[-1]
    v = next((x for x in versions if x.version == version), None)
    if not v:
        raise NotFoundError("roadmap version not found")
    return v


@router.post("/roadmaps/{roadmap_id}/replan", tags=["roadmaps"])
def replan_roadmap(roadmap_id: str, user: str = U, c: Container = C):
    versions = _own_roadmap(c, user, roadmap_id)
    prev = versions[-1]
    role = c.careers.get_role(prev.goal)
    new, diff = recompile(prev, c.careers.targets_for(role), c.store.estimates(user), _skills(c), c.registry.graph())
    if new is not prev:
        c.store.save_roadmap(new)  # new immutable version; history retained
    return {"roadmap": new, "changed": diff.changed, "diff": diff}


@router.post("/roadmaps/{roadmap_id}/milestones/{item_id}/complete", tags=["roadmaps"])
def complete(roadmap_id: str, item_id: str, user: str = U, c: Container = C):
    versions = _own_roadmap(c, user, roadmap_id)
    rm = versions[-1]
    m = complete_milestone(rm, item_id, c.store.estimates(user))
    c.store.save_roadmap(rm)
    return m


# ---------------- resources ----------------
@router.post("/resources/discover", tags=["resources"])
async def discover_resources(body: ResourceDiscoverIn, user: str = U, c: Container = C):
    skill = _skill_or_404(c, body.skill)
    est = c.store.estimates(user).get(skill.skill_id)
    ranked = await c.resources.discover(skill.skill_id, [skill.name, *skill.aliases[:2]],
                                        est.proficiency_score if est else None, body.limit)
    return {"items": [{"resource": r, "score": s} for r, s in ranked], "warnings": c.resources.warnings,
            "sources_configured": len(c.resources.adapters)}
