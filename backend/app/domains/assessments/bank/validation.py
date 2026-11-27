"""Deterministic validation for bank questions. Nothing enters the active bank (validation_status='validated') without passing this.

No model is involved. LLM-generated questions get exactly the same checks as authored ones. ``verify_python_output`` additionally *executes* a
snippet; it is used for trusted (authored) content in tests and is intentionally NOT applied to LLM output, which would mean running untrusted code."""
from __future__ import annotations

import ast
import hashlib
import re
import subprocess
import sys
import tempfile

from app.infrastructure.database.tables import COGNITIVE_LEVELS, QUESTION_TYPES

SINGLE = {"single_choice", "code_output", "debugging", "scenario", "conceptual", "practical"}
AMBIGUOUS_OPTION = re.compile(r"^\s*(all|none) of the (above|these)\b|^\s*both [a-d] and [a-d]\b|^\s*[a-d] and [a-d]\s*$", re.I)
PLACEHOLDER = re.compile(r"lorem ipsum|\btodo\b|\bplaceholder\b|\bxxx+\b|<question>|\[insert", re.I)
_norm = lambda s: re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", str(s).lower())).strip()  # noqa: E731


def content_hash(question_text: str, code_snippet: str | None = None) -> str:
    """Identity of a question within a skill: normalized text (+ code). Used for de-duplication and deterministic seeding."""
    return hashlib.sha256((_norm(question_text) + "\n" + _norm(code_snippet or "")).encode()).hexdigest()


def validate_question(q: dict, *, code_language: str | None = None) -> list[str]:
    """Returns a list of problems; empty means valid. ``q`` uses the bank field names (correct = list of option indices)."""
    errs: list[str] = []
    text = q.get("question_text")
    if not isinstance(text, str) or not 15 <= len(text.strip()) <= 1500:
        errs.append("question_text must be 15-1500 characters")
    elif PLACEHOLDER.search(text):
        errs.append("question_text looks like a placeholder")
    t = q.get("question_type")
    if t not in QUESTION_TYPES:
        errs.append(f"question_type must be one of {QUESTION_TYPES}")
    if q.get("difficulty") not in (1, 2, 3, 4, 5):
        errs.append("difficulty must be 1-5")
    if q.get("cognitive_level") not in COGNITIVE_LEVELS:
        errs.append(f"cognitive_level must be one of {COGNITIVE_LEVELS}")
    for f in ("concept", "subtopic"):
        v = q.get(f)
        if not isinstance(v, str) or not 2 <= len(v.strip()) <= 120:
            errs.append(f"{f} (concept mapping) is required")
    exp = q.get("explanation")
    if not isinstance(exp, str) or len(exp.strip()) < 15:
        errs.append("explanation is required (at least 15 characters)")

    opts, correct = q.get("options"), q.get("correct")
    if not isinstance(opts, list) or not all(isinstance(o, str) and o.strip() and len(o) <= 400 for o in opts):
        errs.append("options must be a list of non-empty strings")
    else:
        lo, hi = (4, 8) if t == "multiple_select" else (3, 6)
        if not lo <= len(opts) <= hi:
            errs.append(f"{t} needs {lo}-{hi} options")
        normed = [_norm(o) for o in opts]
        if len(set(normed)) != len(normed):
            errs.append("duplicate options")
        if any(AMBIGUOUS_OPTION.search(o) for o in opts):
            errs.append("ambiguous option ('all/none of the above', 'both A and B')")
        if not isinstance(correct, list) or not correct or not all(isinstance(i, int) and not isinstance(i, bool) for i in correct):
            errs.append("correct must be a non-empty list of option indices")
        else:
            if any(not 0 <= i < len(opts) for i in correct):
                errs.append("correct references an option that does not exist")
            if len(set(correct)) != len(correct):
                errs.append("correct contains duplicates")
            if t in SINGLE and len(correct) != 1:
                errs.append(f"{t} needs exactly one correct option")
            if t == "multiple_select" and not 2 <= len(correct) < len(opts):
                errs.append("multiple_select needs at least two correct options and at least one incorrect")
    code = q.get("code_snippet")
    if t in ("code_output", "debugging") and not (isinstance(code, str) and code.strip()):
        errs.append(f"{t} questions need a code_snippet")
    if isinstance(code, str) and code.strip():
        if len(code) > 4000:
            errs.append("code_snippet too long")
        if (code_language or q.get("code_language")) == "python":
            try:
                ast.parse(code)
            except SyntaxError as e:
                errs.append(f"code_snippet is not valid Python ({e.msg})")
        elif code.count("```"):
            errs.append("code_snippet must not contain markdown fences")
    return errs


def quality_score(q: dict) -> float:
    """Transparent 0-1 heuristic stored with the question (not used to gate validity): richer explanations and examples score higher."""
    s = 0.5
    s += 0.2 if len(q.get("explanation", "")) >= 60 else 0.0
    s += 0.1 if q.get("solution_approach") else 0.0
    s += 0.1 if q.get("practical_example") else 0.0
    s += 0.1 if q.get("question_type") in ("scenario", "debugging", "code_output", "practical") else 0.0
    return round(min(1.0, s), 2)


def verify_python_output(code: str, expected: str, timeout_s: float = 4.0) -> tuple[bool, str]:
    """Execute a TRUSTED snippet in an isolated interpreter (-I, empty env, temp cwd, hard timeout) and compare stdout with ``expected``."""
    with tempfile.TemporaryDirectory() as d:
        try:
            r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=timeout_s, cwd=d, env={})
        except subprocess.TimeoutExpired:
            return False, "timeout"
    out = r.stdout.strip()
    return (r.returncode == 0 and out == expected.strip()), out or r.stderr.strip()[-200:]
