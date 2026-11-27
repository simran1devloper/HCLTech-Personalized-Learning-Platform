"""Pure-function tests for the mastery engine, readiness formula and recommendation ranking (no I/O)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.core.exceptions import CycleError
from app.domains.learning_intelligence.proficiency import Evidence, EvidenceKind as K, ProficiencyEstimate
from app.domains.learning_intelligence.skill_gap import Target
from app.domains.skill_intelligence.mastery import (MANUAL_PREFIX, SkillStatus as S, attainment, career_metrics, evaluate, readiness_value, reliability, status_of)
from app.domains.skill_intelligence.models import GraphNode
from app.domains.skill_intelligence.recommend import RecommendConfig, recommend
from app.domains.skill_registry.graph import SkillGraph

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def ev(sid="a", score=0.9, diff=4.0, kind=K.QUIZ, n=10, days=1, src=None):
    return Evidence(skill_id=sid, kind=kind, score=score, difficulty=diff, n_items=n, occurred_at=NOW - timedelta(days=days), source_id=src or f"att-{days}-{score}-{kind.value}")


# ------------------------------------------------------------------ mastery
def test_no_evidence_means_zero_everything():
    m = evaluate("a", 3, [], NOW)
    assert (m.mastery, m.confidence, m.evidence_count, m.mastered) == (0, 0, 0, False)
    assert status_of(m, False) == S.READY and status_of(m, True) == S.NOT_STARTED


def test_one_graded_quiz_is_not_enough_to_master():
    m = evaluate("a", 3, [ev(score=1.0, diff=5, n=10)], NOW)
    assert m.proficiency == 5 and m.confidence < 0.5 and not m.mastered
    assert status_of(m, False) == S.IN_PROGRESS


def test_two_graded_assessments_master_the_skill():
    m = evaluate("a", 3, [ev(score=.9, diff=4, days=1), ev(score=.95, diff=4, days=2)], NOW)
    assert m.mastered and m.attainment == 1 and status_of(m, False) == S.MASTERED


def test_self_reported_evidence_is_ignored():
    m = evaluate("a", 3, [ev(kind=K.SELF_REPORT, score=1, diff=5, n=50, days=d) for d in range(1, 6)], NOW)
    assert m.evidence_count == 0 and m.mastery == 0 and not m.mastered


def test_manual_practical_evidence_raises_proficiency_but_cannot_master_alone():
    manual = [ev(kind=K.PROJECT, score=1, diff=5, n=5, days=d, src=f"{MANUAL_PREFIX}repo-{d}") for d in range(1, 4)]
    m = evaluate("a", 3, manual, NOW)
    assert m.proficiency == 5 and m.confidence >= 0.5 and m.graded_evidence_count == 0 and not m.mastered
    assert status_of(m, False) == S.IN_PROGRESS
    graded = evaluate("a", 3, manual + [ev(score=.9, diff=4)], NOW)
    assert graded.mastered


def test_learning_progress_is_not_mastery():
    m = evaluate("a", 3, [], NOW, learning_progress=1.0)
    assert m.mastery == 0 and not m.mastered and m.learning_progress == 1.0
    assert status_of(m, False) == S.IN_PROGRESS and status_of(m, True) == S.BLOCKED


def test_stale_mastery_needs_revision():
    old = [ev(score=.9, diff=4, days=200), ev(score=.95, diff=4, days=210)]
    m = evaluate("a", 3, old, NOW)
    assert m.revision_reason == "stale" and not m.mastered and status_of(m, False) == S.NEEDS_REVISION


def test_regression_needs_revision():
    items = [ev(score=.95, diff=4, days=30), ev(score=.9, diff=4, days=29), ev(score=.2, diff=4, days=1)]
    m = evaluate("a", 3, items, NOW)
    assert m.revision_reason == "regression" and status_of(m, False) == S.NEEDS_REVISION


def test_status_precedence_blocked_vs_not_started():
    started = evaluate("a", 3, [ev(score=.3, diff=3)], NOW)
    assert status_of(started, True) == S.BLOCKED and status_of(started, False) == S.IN_PROGRESS


def test_formula_components():
    assert attainment(2, 4) == 0.5 and attainment(9, 4) == 1 and attainment(1, 0) == 1
    assert reliability(0) == 0.5 and reliability(0.6) == 1 and reliability(5) == 1
    assert reliability(0.3) == pytest.approx(0.75)


# ------------------------------------------------------------------ readiness
def est(sid, p, c, n=3):
    return ProficiencyEstimate(skill_id=sid, proficiency_score=p, confidence_score=c, evidence_count=n, last_assessed_at=NOW)


def test_readiness_is_importance_weighted_and_deterministic():
    targets = [Target(skill_id="a", target_level=4, importance=1.0), Target(skill_id="b", target_level=4, importance=0.5)]
    e = {"a": est("a", 4, 0.6), "b": est("b", 2, 0.3)}
    # a: 1.0 * 1.0 * 1.0 ; b: 0.5 * 0.5 * 0.75 = 0.1875 ; /1.5
    r, c = readiness_value(targets, e)
    assert r == pytest.approx((1.0 + 0.1875) / 1.5, abs=1e-3) and c == pytest.approx((0.6 + 0.15) / 1.5, abs=1e-3)
    assert readiness_value(targets, e) == (r, c)


def test_low_confidence_discounts_readiness():
    t = [Target(skill_id="a", target_level=4)]
    assert readiness_value(t, {"a": est("a", 4, 0.05)})[0] < readiness_value(t, {"a": est("a", 4, 0.9)})[0] == 1.0


def test_coverage_mastery_and_readiness_are_separate_metrics():
    ms = {"a": evaluate("a", 3, [ev("a", .9, 4, days=1), ev("a", .9, 4, days=2)], NOW),   # mastered
          "b": evaluate("b", 4, [ev("b", .3, 3)], NOW),                                    # covered, weak
          "c": evaluate("c", 3, [], NOW),                                                   # untouched prerequisite
          "d": evaluate("d", 3, [], NOW, learning_progress=1.0)}                            # studied, never assessed
    st = {k: status_of(m, False) for k, m in ms.items()}
    targets = [Target(skill_id="a", target_level=3, importance=1, critical=True), Target(skill_id="b", target_level=4, importance=1, critical=True)]
    x = career_metrics(ms, st, targets)
    assert (x.skills_total, x.skills_covered, x.mastered, x.remaining) == (4, 2, 1, 3)
    assert x.coverage == 0.5                         # 2 of 4 assessed
    assert x.mastery == pytest.approx(sum(m.mastery for m in ms.values()) / 4, abs=1e-3)
    assert x.readiness not in (x.coverage, x.mastery) and 0 < x.readiness < 1
    assert x.critical_gaps == ["b"]
    assert x.in_progress == 2                        # b (in progress) and d (learning only)


def test_empty_scope_is_all_zero():
    x = career_metrics({}, {}, [])
    assert (x.coverage, x.mastery, x.readiness, x.skills_total) == (0, 0, 0, 0)


# ------------------------------------------------------------------ recommendation engine
def node(sid, status=S.READY, *, target=3.0, prof=0.0, conf=0.0, imp=0.0, is_target=False, missing=(), hours=10.0, progress=0.0, critical=False, ev_n=0):
    return GraphNode(skill_id=sid, slug=sid, name=sid.upper(), category="c", difficulty=2, estimated_hours=20, status=status, is_target=is_target, importance=imp,
                     critical=critical, target=target, proficiency=prof, confidence=conf, evidence_count=ev_n, mastery=0, learning_progress=progress,
                     missing_prerequisites=list(missing), prerequisites=[], unlocks=0, depth=0, stage=0, hours_remaining=hours)


G = SkillGraph([("b", "a"), ("c", "b")])  # a -> b -> c  (c requires b requires a)


def chain(**over):
    base = {"a": node("a", S.READY, is_target=False), "b": node("b", S.NOT_STARTED, is_target=True, imp=0.6, missing=["a"]),
            "c": node("c", S.NOT_STARTED, is_target=True, imp=1.0, critical=True, missing=["a", "b"])}
    base.update(over)
    return base


def test_foundation_that_unlocks_the_career_ranks_first_and_blocked_skills_name_their_prerequisites():
    recs = recommend(chain(), G)
    assert recs[0].skill_id == "a"                      # the only actionable skill, and everything flows through it
    assert recs[0].missing_prerequisites == [] and recs[0].suggested_action == "start_learning" and recs[0].unlocks == ["b"]
    c = next(r for r in recs if r.skill_id == "c")      # the most important target is blocked: it names what to learn first
    assert c.suggested_action == "learn_prerequisites" and c.missing_prerequisites == ["a", "b"] and c.next_skill_id == "a"
    assert "learn first" in c.explanation and all(r.explanation for r in recs)


def test_ranking_is_deterministic_and_priorities_are_ordered():
    a, b = recommend(chain(), G), recommend(chain(), G)
    assert a == b
    assert [r.rank for r in a] == [1, 2, 3] and [r.score for r in a] == sorted([r.score for r in a], reverse=True)
    order = {"high": 0, "medium": 1, "low": 2}
    assert [order[r.priority] for r in a] == sorted(order[r.priority] for r in a)


def test_mastered_skills_are_never_recommended():
    nodes = chain(a=node("a", S.MASTERED, prof=3, conf=.8, ev_n=3), b=node("b", S.READY, is_target=True, imp=.6))
    nodes["c"] = node("c", S.NOT_STARTED, is_target=True, imp=1.0, missing=["b"])
    assert "a" not in [r.skill_id for r in recommend(nodes, G)]


def test_revision_and_progress_and_effort_move_the_ranking():
    base = {"x": node("x", S.NEEDS_REVISION, is_target=True, imp=.5, prof=3, conf=.7, ev_n=3), "y": node("y", S.READY, is_target=True, imp=.5)}
    g = SkillGraph()
    assert recommend(base, g)[0].suggested_action in ("revise", "start_learning")
    assert next(r for r in recommend(base, g) if r.skill_id == "x").suggested_action == "revise"
    quick, slow = node("q", is_target=True, imp=.5, hours=2), node("s", is_target=True, imp=.5, hours=60)
    assert recommend({"q": quick, "s": slow}, g)[0].skill_id == "q"
    started = node("p", S.IN_PROGRESS, is_target=True, imp=.5, progress=.8, ev_n=1, prof=1, conf=.3)
    assert recommend({"p": started, "n": node("n", is_target=True, imp=.5)}, g)[0].skill_id == "p"


def test_weights_are_configurable_and_explainable():
    nodes = {"hi": node("hi", is_target=True, imp=1.0, hours=60), "lo": node("lo", is_target=True, imp=.1, hours=1)}
    g = SkillGraph()
    assert recommend(nodes, g, RecommendConfig(w_relevance=1, w_effort=0, w_unblock=0, w_gap=0, w_progress=0, w_uncertainty=0))[0].skill_id == "hi"
    r = recommend(nodes, g)[0]
    assert set(r.drivers) >= {"relevance", "unblock", "gap", "effort"}


def test_take_assessment_when_level_is_high_but_confidence_low():
    n = {"a": node("a", S.IN_PROGRESS, target=3, prof=3.5, conf=.2, ev_n=1, is_target=True, imp=1)}
    assert recommend(n, SkillGraph())[0].suggested_action == "take_assessment"


# ------------------------------------------------------------------ cycles
def test_cycle_detection_in_graph():
    g = SkillGraph([("b", "a"), ("c", "b")])
    assert g.would_create_cycle("a", "c") and not g.would_create_cycle("c", "a")
    with pytest.raises(CycleError):
        g.add_edge("a", "c")
    g2 = SkillGraph([("a", "b"), ("b", "a")])
    assert g2.find_cycle()


# ------------------------------------------------------------------ graph helpers used by the explorer
def test_topological_order_is_prerequisites_first_deterministic_and_deep_safe():
    g = SkillGraph([("b", "a"), ("c", "b"), ("c", "x"), ("d", "c")])
    o = g.topological_order(["a", "b", "c", "d", "x"])
    assert o == ["a", "x", "b", "c", "d"] and o == g.topological_order(["d", "x", "c", "b", "a"])
    assert g.topological_order(["b", "c"]) == ["b", "c"]            # edges leaving the subset are ignored
    deep = SkillGraph([(f"n{i + 1}", f"n{i}") for i in range(5000)])  # far beyond Python's recursion limit
    assert deep.topological_order([f"n{i}" for i in range(5001)])[:3] == ["n0", "n1", "n2"]
    assert SkillGraph([("a", "b"), ("b", "a")]).topological_order(["a", "b"]) == ["a", "b"]   # corrupt cycle: no exception


def test_recommend_scales_to_thousands_of_skills():
    import time
    n = 3000
    nodes = {f"s{i:04d}": node(f"s{i:04d}", S.READY if i < 30 else S.NOT_STARTED, is_target=(i % 25 == 0), imp=0.5, missing=[]) for i in range(n)}
    g = SkillGraph([(f"s{i:04d}", f"s{i - 1 - (i % 7):04d}") for i in range(8, n)])
    t = time.perf_counter(); recs = recommend(nodes, g); assert len(recs) == 5 and time.perf_counter() - t < 2.0
