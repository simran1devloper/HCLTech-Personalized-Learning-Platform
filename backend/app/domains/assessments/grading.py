"""Deterministic grading. No LLM involvement; coding is delegated to an isolated sandbox."""
from __future__ import annotations

import re
from typing import Any, Protocol

from app.domains.assessments.models import GradeResult, Question, QuestionType


class Sandbox(Protocol):
    """Executes learner code OUTSIDE the API process (container / gVisor / remote runner)."""
    def run_tests(self, code: str, language: str, test_cases: list[dict[str, Any]]) -> tuple[int, int]: ...


class UnavailableSandbox:
    """Fail closed: never run untrusted code in-process when no sandbox is configured."""
    def run_tests(self, code, language, test_cases):
        raise RuntimeError("no code sandbox configured")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s.lower())).strip()


def _contains(text: str, kw: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(_norm(kw))}(?!\w)", text) is not None


def grade_rubric(q: Question, answer: str) -> GradeResult:
    text = _norm(answer or "")
    total = sum(c.weight for c in q.rubric)
    got, missed = 0.0, []
    for c in q.rubric:
        any_ok = (not c.keywords_any) or any(_contains(text, k) for k in c.keywords_any)
        all_ok = all(_contains(text, k) for k in c.keywords_all)
        if (c.keywords_any or c.keywords_all) and any_ok and all_ok:
            got += c.weight
        else:
            missed.append(c.description)
    earned = round(got / total, 4) if total else 0.0
    fb = "All rubric criteria addressed." if not missed else "Missing: " + "; ".join(missed)
    return GradeResult(question_id=q.question_id, earned=earned, correct=earned >= 0.9999, feedback=fb)


def grade(q: Question, answer: Any, sandbox: Sandbox | None = None, language: str = "python") -> GradeResult:
    t = q.type
    if answer is None or answer == [] or answer == "":
        return GradeResult(question_id=q.question_id, earned=0.0, correct=False,
                           feedback=f"No answer given. {q.explanation}".strip())

    if t in (QuestionType.MCQ, QuestionType.TRUE_FALSE):
        picked = answer[0] if isinstance(answer, list) and answer else answer
        ok = picked in q.correct
        return GradeResult(question_id=q.question_id, earned=1.0 if ok else 0.0, correct=ok,
                           feedback="Correct." if ok else f"Incorrect. {q.explanation}".strip())

    if t == QuestionType.MULTI:
        picked = set(answer if isinstance(answer, list) else [answer])
        right = set(q.correct)
        # partial credit: each correct pick +1/|right|, each wrong pick -1/|right|, floor 0
        earned = max(0.0, (len(picked & right) - len(picked - right)) / len(right))
        ok = picked == right
        return GradeResult(question_id=q.question_id, earned=round(earned, 4), correct=ok,
                           feedback="Correct." if ok else f"Partially or fully incorrect. {q.explanation}".strip())

    if t == QuestionType.SHORT and q.accepted_answers:
        ok = _norm(str(answer)) in {_norm(a) for a in q.accepted_answers}
        if ok or not q.rubric:
            return GradeResult(question_id=q.question_id, earned=1.0 if ok else 0.0, correct=ok,
                               feedback="Correct." if ok else f"Incorrect. {q.explanation}".strip())

    if q.rubric:
        r = grade_rubric(q, str(answer))
        # keyword rubrics are a coarse proxy for open-ended answers -> flag mid-range results for human/LLM review
        r.needs_review = t in (QuestionType.SYSTEM_DESIGN, QuestionType.PRACTICAL) and 0 < r.earned < 1
        return r

    if t in (QuestionType.CODING, QuestionType.DEBUGGING):
        try:
            passed, total = (sandbox or UnavailableSandbox()).run_tests(str(answer), language, q.test_cases)
        except Exception:
            return GradeResult(question_id=q.question_id, earned=0.0, correct=False, needs_review=True,
                               feedback="Code could not be executed (sandbox unavailable); not scored.")
        earned = passed / total if total else 0.0
        return GradeResult(question_id=q.question_id, earned=round(earned, 4), correct=passed == total,
                           feedback=f"{passed}/{total} test cases passed.")

    return GradeResult(question_id=q.question_id, earned=0.0, correct=False, needs_review=True,
                       feedback="Unsupported question configuration.")
