from datetime import datetime, timezone
import pytest
from app.domains.careers.service import CareerService
from app.domains.learning_intelligence.proficiency import ProficiencyEstimate
from app.domains.learning_intelligence.skill_gap import PriorityConfig, Target, analyze
from app.domains.skill_registry.repository import InMemorySkillRepository
from app.domains.skill_registry.seed import seed
from app.domains.skill_registry.service import SkillRegistry


@pytest.fixture
def reg():
    r = SkillRegistry(InMemorySkillRepository()); seed(r); return r


def est(reg, **levels):
    out = {}
    for name, (lvl, conf) in levels.items():
        sid = reg.resolve(name.replace("_", " "), 1.0).skill_id
        out[sid] = ProficiencyEstimate(skill_id=sid, proficiency_score=lvl, confidence_score=conf, evidence_count=3,
                                       last_assessed_at=datetime.now(timezone.utc))
    return out


def run(reg, estimates, targets, cfg=PriorityConfig()):
    skills = {s.skill_id: s for s in reg.repo.list(limit=999)[0]}
    return analyze(targets, estimates, skills, reg.graph(), reg.min_levels(), cfg)


def T(reg, name, lvl, imp=1.0, crit=False):
    return Target(skill_id=reg.resolve(name, 1.0).skill_id, target_level=lvl, importance=imp, critical=crit)


def test_spec_example_gaps(reg):
    e = est(reg, Python=(4, .8), FastAPI=(2, .6), PostgreSQL=(3, .7), System_Design=(1, .5), LangGraph=(1, .5))
    targets = [T(reg, "Python", 4), T(reg, "FastAPI", 4), T(reg, "PostgreSQL", 4), T(reg, "System Design", 4), T(reg, "LangGraph", 3)]
    rep = run(reg, e, targets)
    names = {i.name: i for i in rep.items}
    assert "Python" not in names  # no gap
    assert names["FastAPI"].gap == 2 and names["System Design"].gap == 3
    assert all(i.explanation for i in rep.items)
    assert rep.items == sorted(rep.items, key=lambda i: (-i.priority, i.name))


def test_recommended_next_is_unblocked(reg):
    e = est(reg, Python=(4, .9))
    rep = run(reg, e, [T(reg, "FastAPI", 4), T(reg, "API Development", 3), T(reg, "Async Programming", 3)])
    by_id = {i.skill_id: i for i in rep.items}
    rec = by_id[rep.recommended_next]
    assert not rec.blocked and rec.name in {"Async Programming"}  # API Dev and FastAPI are blocked behind it


def test_all_blocked_recommends_earliest_prereq(reg):
    rep = run(reg, {}, [T(reg, "FastAPI", 4)])
    assert rep.items[0].blocked
    assert reg.repo.get(rep.recommended_next).name == "Python"


def test_priority_config_changes_ordering(reg):
    e = est(reg, Python=(4, .9))
    ts = [T(reg, "Redis", 4, imp=0.2), T(reg, "Docker", 3, imp=1.0)]
    relevance_first = run(reg, e, ts, PriorityConfig(w_relevance=1, w_gap=0, w_unlocks=0, w_uncertainty=0, w_effort=0))
    gap_first = run(reg, e, ts, PriorityConfig(w_relevance=0, w_gap=1, w_unlocks=0, w_uncertainty=0, w_effort=0))
    assert relevance_first.items[0].name == "Docker" and gap_first.items[0].name == "Redis"


def test_unlocking_skill_gets_boost(reg):
    ts = [T(reg, "Python", 4), T(reg, "FastAPI", 4)]
    rep = run(reg, {}, ts, PriorityConfig(w_relevance=0, w_gap=0, w_unlocks=1, w_uncertainty=0, w_effort=0))
    assert rep.items[0].name == "Python"


def test_readiness_bounds_and_labels(reg):
    cs = CareerService(reg)
    role = cs.get_role("ai-engineer")
    none = cs.readiness(role, {})
    assert none.readiness == 0 and none.evidence_confidence == 0 and "Python" in none.critical_gaps
    assert "hiring" in none.basis.lower()
    strong = est(reg, **{k.replace(" ", "_").replace("-", "_"): (5, .9) for k in
                         ["Python", "LLM Fundamentals", "Prompt Engineering", "RAG", "Tool Calling", "Agentic AI", "MCP", "LangGraph", "FastAPI", "Docker"]})
    full = cs.readiness(role, strong)
    assert full.readiness == 1.0 and full.critical_gaps == [] and full.unassessed == []


def test_role_requirements_resolve_to_real_skills(reg):
    cs = CareerService(reg)
    for role in cs.list_roles():
        assert len(cs.targets_for(role)) == len(role.requirements), role.slug


def test_match_roles_ranks(reg):
    cs = CareerService(reg)
    e = est(reg, Python=(4, .9), SQL=(4.5, .9), PostgreSQL=(3.5, .9), Distributed_Systems=(3, .8), Cloud_Computing=(3, .8), Docker=(3, .8))
    assert cs.match_roles(e)[0].role_slug == "data-engineer"
