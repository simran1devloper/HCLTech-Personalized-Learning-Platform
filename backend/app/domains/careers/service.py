"""Career readiness = evidence-based competency coverage of a role's requirements.
It is NOT a hiring probability and contains no labour-market estimate."""
from __future__ import annotations

from pydantic import BaseModel

from app.domains.careers.models import CareerRole, SkillRequirement
from app.domains.learning_intelligence.proficiency import ProficiencyEstimate
from app.domains.learning_intelligence.skill_gap import Target
from app.domains.skill_intelligence.mastery import readiness_value
from app.domains.skill_registry.service import SkillRegistry

R = SkillRequirement


def _r(*rows):
    return [R(skill_slug=s, target_level=t, importance=i, critical=c) for s, t, i, c in rows]


ROLES = [
    CareerRole(slug="python-backend-engineer", title="Python Backend Engineer", requirements=_r(
        ("python", 4, 1.0, True), ("fastapi", 3, 0.9, False), ("sql", 3.5, 0.9, True), ("postgresql", 3, 0.8, False),
        ("api-development", 3.5, 0.9, True), ("redis", 2, 0.5, False), ("docker", 3, 0.7, False), ("system-design", 3, 0.7, False))),
    CareerRole(slug="ai-engineer", title="AI Engineer", requirements=_r(
        ("python", 4, 1.0, True), ("llm-fundamentals", 3.5, 1.0, True), ("prompt-engineering", 3.5, 0.8, False),
        ("rag", 3.5, 0.9, True), ("tool-calling", 3, 0.8, False), ("agentic-ai", 3, 0.8, False), ("mcp", 2.5, 0.5, False),
        ("langgraph", 2.5, 0.5, False), ("fastapi", 3, 0.6, False), ("docker", 2.5, 0.5, False))),
    CareerRole(slug="machine-learning-engineer", title="Machine Learning Engineer", requirements=_r(
        ("python", 4, 1.0, True), ("machine-learning", 4, 1.0, True), ("deep-learning", 3.5, 0.8, False), ("sql", 3, 0.6, False),
        ("docker", 3, 0.6, False), ("cloud-computing", 2.5, 0.5, False), ("system-design", 2.5, 0.5, False))),
    CareerRole(slug="data-engineer", title="Data Engineer", requirements=_r(
        ("python", 3.5, 0.9, True), ("sql", 4.5, 1.0, True), ("postgresql", 3.5, 0.8, False), ("distributed-systems", 3, 0.7, False),
        ("cloud-computing", 3, 0.7, False), ("docker", 3, 0.6, False))),
    CareerRole(slug="full-stack-ai-engineer", title="Full Stack AI Engineer", requirements=_r(
        ("python", 3.5, 1.0, True), ("fastapi", 3, 0.8, False), ("llm-fundamentals", 3, 0.9, True), ("rag", 3, 0.8, False),
        ("agentic-ai", 2.5, 0.6, False), ("sql", 3, 0.7, False), ("docker", 2.5, 0.5, False), ("system-design", 2.5, 0.6, False))),
    CareerRole(slug="devops-engineer", title="DevOps Engineer", requirements=_r(
        ("docker", 4, 1.0, True), ("kubernetes", 3.5, 0.9, True), ("ci-cd", 4, 1.0, True), ("cloud-computing", 3.5, 0.8, False),
        ("python", 3, 0.6, False), ("system-design", 3, 0.6, False), ("distributed-systems", 2.5, 0.4, False))),
    CareerRole(slug="cloud-engineer", title="Cloud Engineer", requirements=_r(
        ("cloud-computing", 4, 1.0, True), ("docker", 3.5, 0.8, True), ("kubernetes", 3.5, 0.8, False), ("ci-cd", 3, 0.7, False),
        ("distributed-systems", 3, 0.7, False), ("system-design", 3, 0.6, False), ("python", 3, 0.5, False))),
    CareerRole(slug="cybersecurity-engineer", title="Cybersecurity Engineer", requirements=_r(
        ("application-security", 4, 1.0, True), ("network-security", 4, 1.0, True), ("cloud-computing", 3, 0.7, False), ("python", 3, 0.6, False),
        ("docker", 2.5, 0.5, False), ("system-design", 2.5, 0.5, False))),
    CareerRole(slug="full-stack-developer", title="Full Stack Developer", requirements=_r(
        ("javascript", 4, 1.0, True), ("python", 3, 0.7, False), ("api-development", 3.5, 0.9, True), ("rest-api-design", 3, 0.7, False),
        ("sql", 3, 0.8, False), ("git", 3, 0.6, False), ("docker", 2.5, 0.5, False), ("system-design", 2.5, 0.5, False))),
    CareerRole(slug="software-engineer", title="Software Engineer", requirements=_r(
        ("python", 3.5, 0.8, True), ("sql", 3, 0.7, False), ("api-development", 3, 0.8, True), ("system-design", 3, 0.8, False),
        ("docker", 2.5, 0.5, False), ("cloud-computing", 2.5, 0.5, False))),
]


class ReadinessReport(BaseModel):
    role_slug: str
    readiness: float  # 0..1 importance-weighted validated mastery (attainment x evidence reliability)
    evidence_confidence: float  # 0..1 importance-weighted confidence in the evidence behind it
    critical_gaps: list[str]
    unassessed: list[str]
    basis: str = ("Evidence-based competency coverage of this role's curated skill requirements. "
                  "Not a prediction of hiring outcomes and not a labour-market estimate.")


class CareerService:
    def __init__(self, registry: SkillRegistry, roles: list[CareerRole] | None = None):
        self.registry = registry
        self._roles = {r.slug: r for r in (roles if roles is not None else ROLES)}

    def list_roles(self) -> list[CareerRole]:
        return sorted(self._roles.values(), key=lambda r: r.slug)

    def get_role(self, slug: str) -> CareerRole | None:
        return self._roles.get(slug)

    def targets_for(self, role: CareerRole) -> list[Target]:
        out = []
        for rq in role.requirements:
            s = self.registry.resolve(rq.skill_slug, 1.0)
            if s is None:  # registry drift: skip rather than fabricate a skill
                continue
            out.append(Target(skill_id=s.skill_id, target_level=rq.target_level, importance=rq.importance,
                              critical=rq.critical))
        return out

    def readiness(self, role: CareerRole, estimates: dict[str, ProficiencyEstimate]) -> ReadinessReport:
        targets = self.targets_for(role)
        readiness, confidence = readiness_value(targets, estimates)  # one formula for the whole app (see skill_intelligence/mastery.py)
        crit, unassessed = [], []
        for t in targets:
            e = estimates.get(t.skill_id)
            cur = e.proficiency_score if e else 0.0
            name = self.registry.repo.get(t.skill_id).name
            if e is None or e.evidence_count == 0:
                unassessed.append(name)
            if t.critical and cur < t.target_level:
                crit.append(name)
        return ReadinessReport(role_slug=role.slug, readiness=readiness, evidence_confidence=confidence,
                               critical_gaps=sorted(crit), unassessed=sorted(unassessed))

    def match_roles(self, estimates: dict[str, ProficiencyEstimate]) -> list[ReadinessReport]:
        reps = [self.readiness(r, estimates) for r in self.list_roles()]
        return sorted(reps, key=lambda r: (-r.readiness, r.role_slug))
