from datetime import datetime, timezone
import pytest
from app.domains.assessments.adaptive import attempt_to_evidence, select_next, target_difficulty
from app.domains.assessments.grading import grade
from app.domains.assessments.models import Question, QuestionType, RubricCriterion


def mcq(i="q1", d=2, skill="py", correct=1):
    return Question(question_id=i, skill_id=skill, type=QuestionType.MCQ, prompt=f"p{i}", difficulty=d,
                    options=["a", "b", "c"], correct=[correct], explanation="because")


def test_mcq_and_tf():
    assert grade(mcq(), 1).correct and not grade(mcq(), 0).correct
    assert "because" in grade(mcq(), 0).feedback
    tf = Question(question_id="t", skill_id="py", type=QuestionType.TRUE_FALSE, prompt="x", difficulty=1, correct=[0])
    assert grade(tf, [0]).correct and not grade(tf, 1).correct


def test_multi_partial_credit():
    q = Question(question_id="m", skill_id="s", type=QuestionType.MULTI, prompt="x", difficulty=2,
                 options=list("abcd"), correct=[0, 1])
    assert grade(q, [0, 1]).earned == 1.0
    assert grade(q, [0]).earned == 0.5
    assert grade(q, [0, 2]).earned == 0.0  # 1 right - 1 wrong
    assert grade(q, [2, 3]).earned == 0.0  # floored


def test_unanswered_is_zero():
    assert grade(mcq(), None).earned == 0 and grade(mcq(), "").earned == 0


def test_short_answer_normalised():
    q = Question(question_id="s", skill_id="s", type=QuestionType.SHORT, prompt="x", difficulty=1,
                 accepted_answers=["Event Loop"])
    assert grade(q, "  event   loop! ").correct and not grade(q, "thread").correct


def test_rubric_partial_and_word_boundaries():
    q = Question(question_id="d", skill_id="sd", type=QuestionType.SYSTEM_DESIGN, prompt="design", difficulty=4, rubric=[
        RubricCriterion(id="c1", description="caching", keywords_any=["cache", "redis"], weight=2),
        RubricCriterion(id="c2", description="sharding", keywords_any=["shard", "partition"]),
        RubricCriterion(id="c3", description="queue", keywords_any=["queue"])])
    g = grade(q, "We add a Redis layer and a queue")
    assert g.earned == pytest.approx(0.75) and g.needs_review and "sharding" in g.feedback
    assert grade(q, "cacheable things").earned == 0  # 'cache' must be a whole word


def test_coding_requires_sandbox_and_fails_closed():
    q = Question(question_id="c", skill_id="py", type=QuestionType.CODING, prompt="x", difficulty=3, test_cases=[{"in": 1, "out": 1}])
    assert q.practical
    g = grade(q, "def f(x): return x")
    assert g.needs_review and g.earned == 0

    class Fake:
        def run_tests(self, code, lang, cases): return 1, 2
    g = grade(q, "code", Fake())
    assert g.earned == 0.5 and not g.correct and not g.needs_review


def test_question_validation():
    with pytest.raises(ValueError):
        Question(question_id="x", skill_id="s", type=QuestionType.MCQ, prompt="p", difficulty=1, options=["a", "b"], correct=[5])
    with pytest.raises(ValueError):
        Question(question_id="x", skill_id="s", type=QuestionType.CODING, prompt="p", difficulty=1)


def test_staircase_and_selection_avoid_repeats():
    assert target_difficulty(None, []) == 2
    assert target_difficulty(3.0, [(3, 1.0)]) == 4
    assert target_difficulty(3.0, [(3, 0.0), (2, 0.0)]) == 1
    bank = [mcq(f"q{i}", d=i) for i in range(1, 6)]
    q = select_next(bank, "py", prior_level=3, recent=[], seen_ids=set())
    assert q.difficulty == 3
    q2 = select_next(bank, "py", prior_level=3, recent=[(3, 1.0)], seen_ids={q.question_id})
    assert q2.difficulty == 4 and q2.question_id != q.question_id
    assert select_next(bank, "py", prior_level=3, recent=[], seen_ids={b.question_id for b in bank}) is None
    assert select_next(bank, "other", prior_level=3, recent=[], seen_ids=set()) is None


def test_attempt_to_evidence_skips_unreviewed_and_groups():
    from app.domains.assessments.models import GradeResult
    qs = {"a": mcq("a", 2), "b": mcq("b", 4), "c": mcq("c", 3, skill="sql")}
    gs = [GradeResult(question_id="a", earned=1, correct=True, feedback=""),
          GradeResult(question_id="b", earned=0.5, correct=False, feedback=""),
          GradeResult(question_id="c", earned=0, correct=False, feedback="", needs_review=True)]
    ev = attempt_to_evidence(qs, gs, datetime.now(timezone.utc), "att1")
    assert len(ev) == 1 and ev[0].skill_id == "py" and ev[0].n_items == 2 and ev[0].difficulty == 3.0
    assert ev[0].score == pytest.approx((2 * 1 + 4 * 0.5) / 6, abs=1e-3)
