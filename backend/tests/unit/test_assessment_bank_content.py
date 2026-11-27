"""Seed question bank integrity, deterministic validation, distribution and gap analysis (pure, no database, no model)."""
import copy
from collections import Counter

import pytest

from app.domains.assessments import analysis
from app.domains.assessments.bank.seed_data import SEED
from app.domains.assessments.bank.service import distribution
from app.domains.assessments.bank.validation import content_hash, validate_question, verify_python_output

PY = {"Python", "Data Structures and Algorithms"}
PRIORITY = ["Python", "SQL", "Machine Learning", "Agentic AI", "FastAPI", "REST API Design", "Data Structures and Algorithms", "System Design", "RAG", "LangGraph", "MCP",
            "PostgreSQL", "Redis", "Docker", "Distributed Systems"]
ALL = [(skill, q) for skill, qs in SEED.items() for q in qs]


def good(**kw):
    q = dict(concept="Joins", subtopic="Joins", difficulty=2, question_type="single_choice", cognitive_level="understand", question_text="Which join keeps all rows from the left table?",
             options=["INNER JOIN", "LEFT JOIN", "CROSS JOIN", "SELF JOIN"], correct=[1], explanation="LEFT JOIN preserves unmatched left rows.", code_snippet=None)
    return {**q, **kw}


# ------------------------------------------------------------------------------------------------ seed content
def test_all_priority_topics_are_seeded_with_a_real_mix():
    assert list(SEED) == PRIORITY
    for skill in PRIORITY:
        qs = SEED[skill]
        assert len(qs) >= 12, skill
        per = Counter(q["concept"] for q in qs)
        assert 3 <= len(per) <= 6 and min(per.values()) >= 2, f"{skill}: concept areas must each have >=2 questions so a quiz can measure them: {dict(per)}"
        assert len({q["subtopic"] for q in qs}) >= 8, f"{skill}: too few distinct sub-topics"
        assert {1, 2} & {q["difficulty"] for q in qs} and 3 in {q["difficulty"] for q in qs} and {4, 5} & {q["difficulty"] for q in qs}, f"{skill}: difficulty spread"


@pytest.mark.parametrize("skill,q", ALL, ids=[f"{s}:{q['question_text'][:28]}" for s, q in ALL])
def test_every_seed_question_passes_validation(skill, q):
    assert validate_question(q, code_language="python" if skill in PY else None) == []


def test_no_duplicate_questions_in_the_seed_bank():
    for skill, qs in SEED.items():
        hashes = [content_hash(q["question_text"], q["code_snippet"]) for q in qs]
        assert len(hashes) == len(set(hashes)), skill


def test_python_code_output_answers_are_verified_by_actually_running_the_code():
    ran = 0
    for skill, q in ALL:
        if skill in PY and q["question_type"] == "code_output":
            ok, out = verify_python_output(q["code_snippet"], q["options"][q["correct"][0]])
            assert ok, f"{q['question_text']!r}: program printed {out!r}, keyed answer is {q['options'][q['correct'][0]]!r}"
            ran += 1
    assert ran >= 8


# ------------------------------------------------------------------------------------------------ validator rules
@pytest.mark.parametrize("patch,needle", [
    ({"question_text": "Short?"}, "question_text"), ({"question_type": "essay"}, "question_type"), ({"difficulty": 9}, "difficulty"), ({"cognitive_level": "memorize"}, "cognitive_level"),
    ({"concept": ""}, "concept"), ({"explanation": ""}, "explanation"), ({"correct": [7]}, "does not exist"), ({"correct": [-1]}, "does not exist"), ({"correct": []}, "correct"),
    ({"correct": [0, 1]}, "exactly one"), ({"options": ["A", "A ", "B", "C"], "correct": [0]}, "duplicate"), ({"options": ["Yes", "No", "All of the above", "Maybe"], "correct": [0]}, "ambiguous"),
    ({"options": ["Only two", "options"], "correct": [0]}, "options"), ({"question_text": "TODO write a real question about joins here"}, "placeholder"),
    ({"question_type": "code_output", "code_snippet": None}, "code_snippet"),
    ({"question_type": "multiple_select", "options": ["a1", "b2", "c3", "d4"], "correct": [0]}, "at least two"),
    ({"question_type": "multiple_select", "options": ["a1", "b2", "c3", "d4"], "correct": [0, 1, 2, 3]}, "at least two"),
])
def test_validator_rejects_each_defect(patch, needle):
    errs = validate_question(good(**patch))
    assert errs and any(needle in e for e in errs), errs


def test_validator_accepts_good_and_checks_python_syntax():
    assert validate_question(good()) == []
    bad = good(question_type="code_output", code_snippet="def f(:\n  pass", question_text="What does this program print when it runs?")
    assert any("not valid Python" in e for e in validate_question(bad, code_language="python"))
    ok = good(question_type="code_output", code_snippet="print(1)", question_text="What does this program print when it runs?")
    assert validate_question(ok, code_language="python") == []


def test_content_hash_ignores_case_whitespace_and_punctuation():
    assert content_hash("Which JOIN keeps  all rows?") == content_hash("which join keeps all rows")


# ------------------------------------------------------------------------------------------------ distribution
def test_ten_question_distribution_is_3_4_2_plus_one_scenario():
    d = distribution(10)
    assert d == {"foundational": 3, "intermediate": 4, "advanced": 2, "scenario": 1}


@pytest.mark.parametrize("n", [1, 2, 3, 5, 7, 10, 15, 20])
def test_distribution_always_sums_to_n_and_is_proportionate(n):
    d = distribution(n)
    assert sum(d.values()) == n and all(v >= 0 for v in d.values())
    if n == 3:
        assert d == {"foundational": 1, "intermediate": 1, "advanced": 1, "scenario": 0}   # proportionate, not rigid percentages


def test_distribution_adapts_to_known_proficiency():
    base = distribution(10)
    strong, weak = distribution(10, 4.2), distribution(10, 1.0)
    assert strong["advanced"] == base["advanced"] + 1 and strong["foundational"] == base["foundational"] - 1
    assert weak["foundational"] == base["foundational"] + 1 and weak["advanced"] == base["advanced"] - 1
    assert distribution(10, 2.5) == base and sum(strong.values()) == sum(weak.values()) == 10


# ------------------------------------------------------------------------------------------------ analysis
def items(spec):
    return [dict(concept=c, earned=e, difficulty=d, question_type="single_choice", cognitive_level="apply") for c, e, d in spec]


def test_concept_accuracy_levels_and_unknown_for_small_samples():
    rows = {r["concept"]: r for r in analysis.concept_results(items([
        ("Types", 1, 1), ("Types", 1, 2), ("Funcs", 1, 2), ("Funcs", 1, 3), ("Funcs", 0, 3), ("OOP", 0, 3), ("OOP", 1, 2), ("Async", 0, 4), ("Async", 0, 3), ("Solo", 0, 1)]))}
    assert rows["Types"]["accuracy"] == 1.0 and rows["Types"]["proficiency_level"] == "strong"
    assert rows["Funcs"]["accuracy"] == pytest.approx(0.6667, abs=1e-3) and rows["Funcs"]["proficiency_level"] == "developing"
    assert rows["OOP"]["proficiency_level"] == "developing" and not rows["OOP"]["identified_gap"]
    assert rows["Async"]["proficiency_level"] == "needs_improvement" and rows["Async"]["identified_gap"]
    assert rows["Solo"]["proficiency_level"] == "unknown" and not rows["Solo"]["identified_gap"]       # one question is not evidence


@pytest.mark.parametrize("acc,n,level", [(1.0, 2, "strong"), (0.85, 20, "strong"), (0.84, 20, "good"), (0.70, 10, "good"), (0.69, 10, "developing"), (0.50, 10, "developing"),
                                         (0.49, 10, "needs_improvement"), (0.0, 2, "needs_improvement"), (0.0, 1, "unknown"), (1.0, 1, "unknown")])
def test_threshold_mapping_is_exactly_the_documented_one(acc, n, level):
    assert analysis.level_for(acc, n) == level


def test_gap_report_structure_order_and_caveat():
    its = items([("Types", 1, 1), ("Types", 1, 2), ("Async", 0, 4), ("Async", 0, 3), ("OOP", 0, 2), ("OOP", 0, 3), ("Decorators", 0, 4), ("Decorators", 1, 3), ("Solo", 1, 1)])
    rep = analysis.gap_report("Python", "py", its, analysis.concept_results(its))
    assert rep["overall_score"] == round(100 * 4 / 9, 1) and rep["skill"] == "Python" and rep["questions"] == 9
    assert rep["strong_concepts"] == ["Types"] and rep["critical_gaps"] == ["Async", "OOP"] and rep["weak_concepts"] == []
    assert rep["unknown_concepts"] == ["Solo"] and "Decorators" not in rep["critical_gaps"]
    assert rep["recommended_learning_order"] == ["OOP", "Async"]                       # foundational (lower difficulty) first
    assert [s["key"] for s in rep["recommended_sections"]] == ["gap-oop", "gap-async"] and rep["recommended_sections"][0]["priority"] == "critical"
    assert "short assessment" in rep["caveat"] and "9 questions" in rep["caveat"] and "no AI model" in rep["basis"]
    assert {r["difficulty"] for r in rep["by_difficulty"]} == {1, 2, 3, 4}


def test_gap_report_for_a_perfect_attempt_has_no_gaps():
    its = items([("A", 1, 1), ("A", 1, 2), ("B", 1, 3), ("B", 1, 4)])
    rep = analysis.gap_report("X", "x", its, analysis.concept_results(its))
    assert rep["critical_gaps"] == rep["weak_concepts"] == rep["recommended_learning_order"] == [] and rep["overall_score"] == 100.0 and rep["overall_level"] == "strong"
