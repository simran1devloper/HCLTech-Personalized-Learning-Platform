from datetime import datetime, timedelta, timezone
from app.domains.learning_intelligence.proficiency import Evidence, EvidenceKind, estimate, ProficiencyConfig

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def ev(score, diff=4, n=10, days=0, kind=EvidenceKind.QUIZ, sid="s"):
    return Evidence(skill_id="py", kind=kind, score=score, difficulty=diff, n_items=n,
                    occurred_at=NOW - timedelta(days=days), source_id=sid)


def test_no_evidence_is_zero_confidence_zero():
    e = estimate("py", [], NOW)
    assert e.proficiency_score == 0 and e.confidence_score == 0 and e.evidence_count == 0


def test_deterministic_and_versioned():
    items = [ev(0.8), ev(0.6, days=30)]
    a, b = estimate("py", items, NOW), estimate("py", list(reversed(items)), NOW)
    assert a.proficiency_score == b.proficiency_score and a.proficiency_version == "prof-v1"


def test_difficulty_matters():
    assert estimate("py", [ev(1.0, diff=5)], NOW).proficiency_score > estimate("py", [ev(1.0, diff=2)], NOW).proficiency_score


def test_recency_decay_prefers_new_evidence():
    old_good_new_bad = estimate("py", [ev(1.0, days=365, sid="old"), ev(0.2, days=0, sid="new")], NOW)
    new_good_old_bad = estimate("py", [ev(1.0, days=0), ev(0.2, days=365)], NOW)
    assert new_good_old_bad.proficiency_score > old_good_new_bad.proficiency_score


def test_small_samples_are_less_reliable():
    assert estimate("py", [ev(1.0, n=1)], NOW).confidence_score < estimate("py", [ev(1.0, n=10)], NOW).confidence_score


def test_more_evidence_raises_confidence_conflict_lowers_it():
    one = estimate("py", [ev(0.8)], NOW).confidence_score
    many = estimate("py", [ev(0.8), ev(0.8), ev(0.8)], NOW).confidence_score
    conflicted = estimate("py", [ev(1.0), ev(0.0), ev(1.0)], NOW).confidence_score
    assert many > one and conflicted < many


def test_practical_evidence_weighs_more_and_bounds_hold():
    mixed = estimate("py", [ev(0.2, kind=EvidenceKind.QUIZ), ev(1.0, kind=EvidenceKind.PROJECT)], NOW)
    assert mixed.proficiency_score > 0.5 * (0.2 * 4 + 1.0 * 4)
    assert 0 <= mixed.confidence_score <= 1 and 0 <= mixed.proficiency_score <= 5


def test_explainability_shares_sum_to_one_and_ignores_other_skills():
    items = [ev(0.5, sid="a"), ev(0.9, sid="b"), Evidence(skill_id="other", kind=EvidenceKind.QUIZ, score=1, difficulty=5, occurred_at=NOW)]
    e = estimate("py", items, NOW)
    assert e.evidence_count == 2 and abs(sum(c.share for c in e.explanation) - 1) < 0.01
    assert {c.source_id for c in e.explanation} == {"a", "b"}


def test_naive_datetimes_tolerated():
    e = Evidence(skill_id="py", kind=EvidenceKind.QUIZ, score=1, difficulty=3, occurred_at=datetime(2025, 12, 1))
    assert estimate("py", [e], NOW).proficiency_score == 3.0
