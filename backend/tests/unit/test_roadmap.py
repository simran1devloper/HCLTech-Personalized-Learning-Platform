from datetime import datetime, timezone
import pytest
from app.core.exceptions import ConflictError
from app.domains.learning_intelligence.proficiency import ProficiencyEstimate
from app.domains.learning_intelligence.skill_gap import Target
from app.domains.roadmap_engine.engine import complete_milestone, generate, recompile
from app.domains.roadmap_engine.models import ItemStatus
from app.domains.skill_registry.repository import InMemorySkillRepository
from app.domains.skill_registry.seed import seed
from app.domains.skill_registry.service import SkillRegistry


@pytest.fixture
def ctx():
    r = SkillRegistry(InMemorySkillRepository()); seed(r)
    skills = {s.skill_id: s for s in r.repo.list(limit=999)[0]}
    return r, skills, r.graph()


def sid(r, n): return r.resolve(n, 1.0).skill_id


def est(r, **kv):
    out = {}
    for n, (lvl, conf) in kv.items():
        s = sid(r, n.replace("_", " "))
        out[s] = ProficiencyEstimate(skill_id=s, proficiency_score=lvl, confidence_score=conf, evidence_count=2,
                                     last_assessed_at=datetime.now(timezone.utc))
    return out


def T(r, n, lvl): return Target(skill_id=sid(r, n), target_level=lvl)


def names(rm, *st):
    return [m.skill_name for m in rm.milestones if not st or m.status in st]


def test_generate_orders_prereqs_first_and_adds_missing_ones(ctx):
    r, skills, g = ctx
    rm = generate("u1", "x", [T(r, "FastAPI", 3.5)], {}, skills, g)
    n = names(rm)
    assert n[-1] == "FastAPI" and n.index("Python") < n.index("API Development") < n.index("FastAPI")
    assert [m.order for m in rm.milestones] == list(range(1, len(n) + 1))
    assert rm.total_hours > 0 and rm.estimated_weeks == pytest.approx(rm.total_hours / rm.hours_per_week, abs=0.1)
    assert all(m.objectives and m.checkpoint and m.completion_criteria for m in rm.milestones)


def test_generate_skips_mastered_when_confident_but_not_when_unsure(ctx):
    r, skills, g = ctx
    rm = generate("u", "x", [T(r, "Python", 3), T(r, "FastAPI", 3)], est(r, Python=(4, .9)), skills, g)
    assert "Python" not in names(rm)
    rm2 = generate("u", "x", [T(r, "Python", 3), T(r, "FastAPI", 3)], est(r, Python=(4, .1)), skills, g)
    assert "Python" in names(rm2)  # high score but almost no evidence: keep it


def test_hours_scale_with_gap(ctx):
    r, skills, g = ctx
    big = generate("u", "x", [T(r, "Redis", 4)], est(r, Python=(4, .9)), skills, g).milestones[-1]
    small = generate("u", "x", [T(r, "Redis", 4)], est(r, Python=(4, .9), Redis=(3, .9)), skills, g).milestones[-1]
    assert small.estimated_hours < big.estimated_hours


def test_recompile_no_change_returns_same_version_and_ids(ctx):
    r, skills, g = ctx
    tg = [T(r, "FastAPI", 3.5)]
    rm = generate("u", "x", tg, {}, skills, g)
    rm2, diff = recompile(rm, tg, {}, skills, g)
    assert not diff.changed and rm2.version == rm.version and rm2 is rm


def test_recompile_skips_newly_mastered_and_preserves_ids(ctx):
    r, skills, g = ctx
    tg = [T(r, "FastAPI", 3.5)]
    rm = generate("u", "x", tg, {}, skills, g)
    ids = {m.skill_name: m.item_id for m in rm.milestones}
    rm2, diff = recompile(rm, tg, est(r, Python=(4.5, .9)), skills, g)
    assert rm2.version == 2 and "Python" in diff.skipped
    assert {m.skill_name: m.item_id for m in rm2.milestones if m.status == ItemStatus.PENDING} == {k: v for k, v in ids.items() if k != "Python"} | {}
    assert "skipped" in " ".join(rm2.change_summary)
    assert [m.order for m in rm2.milestones if m.status != ItemStatus.SUPERSEDED] == sorted(m.order for m in rm2.milestones)


def test_completed_progress_preserved_and_weak_skill_reintroduced(ctx):
    r, skills, g = ctx
    tg = [T(r, "Python", 3)]
    e_good = est(r, Python=(3.5, .9))
    rm = generate("u", "x", tg, {}, skills, g)
    complete_milestone(rm, rm.milestones[0].item_id, e_good)
    rm2, diff = recompile(rm, tg, est(r, Python=(1.5, .9)), skills, g)  # regression after a later assessment
    assert diff.preserved_completed == 1 and diff.reintroduced == ["Python"]
    kinds = [(m.kind, m.status) for m in rm2.milestones]
    assert ("learn", ItemStatus.COMPLETED) in kinds and ("review", ItemStatus.PENDING) in kinds
    rm3, diff3 = recompile(rm2, tg, est(r, Python=(1.5, .9)), skills, g)  # idempotent: no duplicate review item
    assert not diff3.changed and sum(1 for m in rm3.milestones if m.kind == "review") == 1


def test_new_target_added_and_dropped_target_superseded(ctx):
    r, skills, g = ctx
    rm = generate("u", "x", [T(r, "Redis", 3)], {}, skills, g)
    rm2, diff = recompile(rm, [T(r, "Redis", 3), T(r, "Docker", 3)], {}, skills, g)
    assert "Docker" in diff.added and "Redis" in names(rm2, ItemStatus.PENDING, ItemStatus.BLOCKED)
    rm3, diff3 = recompile(rm2, [T(r, "Redis", 3)], {}, skills, g)
    assert diff3.removed == ["Docker"]
    assert any(m.status == ItemStatus.SUPERSEDED for m in rm3.milestones)  # history retained


def test_completion_is_evidence_gated(ctx):
    r, skills, g = ctx
    rm = generate("u", "x", [T(r, "Python", 3)], {}, skills, g)
    with pytest.raises(ConflictError):
        complete_milestone(rm, rm.milestones[0].item_id, {})
    m = complete_milestone(rm, rm.milestones[0].item_id, est(r, Python=(3.2, .8)))
    assert m.status == ItemStatus.COMPLETED and rm.total_hours == 0


def test_blocked_detection(ctx):
    r, skills, g = ctx
    tg = [T(r, "FastAPI", 3)]
    rm = generate("u", "x", tg, {}, skills, g)
    # simulate an inconsistent roadmap: prerequisite skill planned AFTER dependent
    fa = next(m for m in rm.milestones if m.skill_name == "FastAPI")
    fa.order = 0
    from app.domains.roadmap_engine.engine import _evaluate_blocking, RoadmapConfig
    _evaluate_blocking(rm, {}, RoadmapConfig())
    assert fa.status == ItemStatus.BLOCKED and fa.blocked_by
    with pytest.raises(ConflictError):
        complete_milestone(rm, fa.item_id, est(r, FastAPI=(5, 1)))
