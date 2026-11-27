"""LLM fallback that fills *only* what the bank is missing, then runs the same deterministic validation as authored content.

``call`` is injectable (tests use a fake). Generated questions are never executed; those that fail validation are stored as 'rejected' (kept for
audit, never served) so the same bad output is not regenerated and re-paid-for."""
from __future__ import annotations

import logging
import re
from typing import Awaitable, Callable

from app.domains.assessments.bank.validation import content_hash, quality_score, validate_question
from utils.helpers import extract_json_from_text

log = logging.getLogger("question_generator")
Call = Callable[[str], Awaitable[tuple[str, int, int]]]  # prompt -> (text, tokens_in, tokens_out)

SCHEMA = ('{"questions": [{"concept": "short concept name", "difficulty": 1-5, "question_type": "single_choice|multiple_select|code_output|debugging|scenario|conceptual", '
          '"cognitive_level": "remember|understand|apply|analyze|evaluate", "question_text": "...", "code_snippet": null or "code", "options": ["..."], '
          '"answer": "the exact text of the correct option (a list of texts for multiple_select)", "correct": [0-based index of each correct option], "explanation": "why the answer is right"}]}')


def build_prompt(skill: str, n: int, concepts_wanted: list[str], difficulties: list[int], avoid: list[str]) -> str:
    return (f"You write multiple-choice assessment questions about {skill} for software engineers. Be technically accurate; never invent APIs.\n"
            f"Write exactly {n} questions. Target difficulties (1 easiest, 5 hardest), in order: {difficulties}.\n"
            + (f"Prefer these concept areas if they fit: {', '.join(concepts_wanted)}. " if concepts_wanted else
               "First choose 3-5 broad concept areas that cover the skill, then spread the questions evenly across them. ")
            + "Use the same concept-area names consistently (concept = a broad area such as 'Data modeling', never a single trivia item). "
            f"Do NOT repeat existing areas' questions. Existing areas: {'; '.join(avoid[:25]) or 'none'}.\n"
            "Each question has 4 options (multiple_select: 4-5) with exactly one correct option (multiple_select: 2+ correct), no 'all/none of the above', and a clear explanation. "
            "Use multiple_select only when 2 or more options are correct. Keep it compact: options under 12 words, explanation one sentence (max 25 words), no code unless the question needs it.\n"
            f"Output ONLY one JSON object (not an array) matching: {SCHEMA}")


_LETTER = re.compile(r"^\s*\(?[A-Ha-h][\).:]\s+")


def _strip_letter(x):
    return _LETTER.sub("", x) if isinstance(x, str) else x


def normalize_raw(raw: dict) -> dict:
    """Deterministic clean-up of formatting slips small models make, without changing what a question means: option letters ("A) ...") are removed from
    the options and the answer text, option lists pasted into the question text are dropped, and the type follows the actual number of correct options."""
    raw = dict(raw)
    opts = raw.get("options")
    if isinstance(opts, list) and all(isinstance(o, str) for o in opts) and opts and sum(1 for o in opts if _LETTER.match(o)) >= len(opts) - 1:
        raw["options"] = [_strip_letter(o) for o in opts]
        ans = raw.get("answer")
        raw["answer"] = [_strip_letter(a) for a in ans] if isinstance(ans, list) else _strip_letter(ans)
        text = raw.get("question_text")
        if isinstance(text, str):
            lines = text.rstrip().split("\n")
            while len(lines) > 1 and _LETTER.match(lines[-1]):
                lines.pop()
            raw["question_text"] = "\n".join(lines).strip()
    return raw


def reconcile_type(qtype, correct):
    """multiple_select with one correct option is a single-choice question (and the reverse): the key, not the label, is what grading uses."""
    if isinstance(correct, list) and correct:
        if qtype == "multiple_select" and len(set(correct)) == 1:
            return "single_choice"
        if qtype in ("single_choice", "conceptual", "scenario") and len(set(correct)) >= 2:
            return "multiple_select"
    return qtype


def _questions_from(data) -> list:
    """Models in JSON mode return one object, not an array: accept {"questions": [...]}, any object holding a list, a single question object, or an array."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if "question_text" in data:
            return [data]
        return next((v for v in data.values() if isinstance(v, list)), [])
    return []


def resolve_correct(options, correct, answer):
    """The key as option indices. Small models are far more reliable at repeating the correct option's text than at counting, so a text that matches an option
    exactly wins; otherwise the index is used, and an index that is out of range by exactly one (a 1-based key) is shifted. Anything else is left for
    validation to reject."""
    if isinstance(options, list) and answer is not None:
        texts = answer if isinstance(answer, list) else [answer]
        norm = [str(o).strip().lower() for o in options]
        hits = [norm.index(str(t).strip().lower()) for t in texts if str(t).strip().lower() in norm]
        if hits and len(hits) == len(texts):
            return sorted(set(hits))
    if isinstance(correct, int) and not isinstance(correct, bool):
        correct = [correct]
    if isinstance(options, list) and isinstance(correct, list) and correct and all(isinstance(i, int) and not isinstance(i, bool) for i in correct) \
            and max(correct) >= len(options) and min(correct) >= 1 and max(correct) <= len(options):
        return [i - 1 for i in correct]
    return correct


def keys_disagree(options, correct, answer) -> bool:
    """True when the model names a correct option by text AND by index and they point at different options: a confused question we should not guess about."""
    if not (isinstance(options, list) and answer is not None):
        return False
    norm = [str(o).strip().lower() for o in options]
    texts = answer if isinstance(answer, list) else [answer]
    hits = sorted({norm.index(str(t).strip().lower()) for t in texts if str(t).strip().lower() in norm})
    idx = [correct] if isinstance(correct, int) and not isinstance(correct, bool) else correct
    if not hits or len(hits) != len(texts) or not isinstance(idx, list) or not idx or not all(isinstance(i, int) and not isinstance(i, bool) for i in idx):
        return False                       # nothing comparable (the text did not match an option, or no usable index): resolve_correct decides
    shifted = [i - 1 for i in idx] if max(idx) >= len(options) and min(idx) >= 1 and max(idx) <= len(options) else idx
    return sorted(set(shifted)) != hits


async def generate(skill: str, n: int, *, call: Call, concepts_wanted: list[str] | None = None, difficulties: list[int] | None = None, avoid: list[str] | None = None,
                   code_language: str | None = None, on_item: Callable[[int], None] | None = None) -> dict:
    """Returns {'valid': [question dicts with hash/quality], 'rejected': [(question, errors)], 'tokens_in', 'tokens_out'}."""
    prompt = build_prompt(skill, n, concepts_wanted or [], difficulties or [2] * n, avoid or [])
    # on_item(k): k question objects have been fully streamed so far (live progress); only calls that support streaming get it
    text, tin, tout = await (call(prompt, on_item=on_item) if on_item else call(prompt))
    try:
        data = extract_json_from_text(text)
    except Exception:  # noqa: BLE001
        data = None
    data = _questions_from(data)
    valid, rejected = [], []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        raw = normalize_raw(raw)
        if not isinstance(raw, dict):
            continue
        q = {"concept": str(raw.get("concept", "")).strip(), "subtopic": str(raw.get("concept", "")).strip(), "difficulty": raw.get("difficulty"),
             "question_type": reconcile_type(raw.get("question_type"), resolve_correct(raw.get("options"), raw.get("correct"), raw.get("answer"))), "cognitive_level": raw.get("cognitive_level"), "question_text": raw.get("question_text"),
             "code_snippet": raw.get("code_snippet") or None, "options": raw.get("options"), "correct": resolve_correct(raw.get("options"), raw.get("correct"), raw.get("answer")), "explanation": raw.get("explanation"),
             "code_language": code_language if raw.get("code_snippet") else None}
        errs = validate_question(q, code_language=code_language)
        if keys_disagree(raw.get("options"), raw.get("correct"), raw.get("answer")):
            errs = [*errs, "the stated answer text and answer index point at different options"]
        if errs:
            rejected.append((q, errs))
        else:
            q["_hash"], q["_quality"] = content_hash(q["question_text"], q["code_snippet"]), quality_score(q)
            valid.append(q)
    return {"valid": valid, "rejected": rejected, "tokens_in": tin, "tokens_out": tout}


async def router_call(prompt: str, on_item: Callable[[int], None] | None = None) -> tuple[str, int, int]:
    """Default model call through the project's provider router (structured/JSON mode).
    With ``on_item`` the answer is streamed and the callback receives the number of complete questions seen so far, so the UI progress moves
    while the model is still writing instead of jumping from 0% to 100% when a whole batch returns."""
    from app.ai.limits import generation_slot
    async with generation_slot():
        return await _router_call_now(prompt, on_item)


async def _router_call_now(prompt: str, on_item: Callable[[int], None] | None) -> tuple[str, int, int]:
    from app.ai.model_router import Complexity, TaskRequirements
    from app.ai.runtime import get_router
    from app.ai.types import GenerationParams, StreamEnd
    from app.core.config import get_settings
    s = get_settings()
    req = TaskRequirements(complexity=Complexity.MEDIUM, needs_structured=True, local_only=s.LLM_LOCAL_ONLY, prefer="configured", max_attempts=1)
    if on_item is None:
        resp = await get_router().generate(prompt, GenerationParams(temperature=0.5, max_output_tokens=3000, timeout_s=s.LLM_TIMEOUT_S, json_mode=True), req)
        return resp.text, resp.usage.prompt_tokens, resp.usage.completion_tokens
    from contextlib import aclosing
    from app.domains.content_engine.streaming import ArrayItemScanner
    end: list[StreamEnd] = []
    params = GenerationParams(temperature=0.5, max_output_tokens=3000, timeout_s=s.LLM_TIMEOUT_S, json_mode=True, on_stream_end=end.append,
                              first_token_timeout_s=s.LLM_FIRST_TOKEN_S, idle_timeout_s=s.LLM_IDLE_TOKEN_S, total_timeout_s=s.LLM_STREAM_TOTAL_S)
    scanner, parts, seen = ArrayItemScanner(), [], 0
    async with aclosing(get_router().generate_stream(prompt, params, TaskRequirements(**{**req.__dict__, "needs_streaming": True}))) as stream:
        async for tok in stream:
            parts.append(tok)
            if (new := len(scanner.feed(tok))):
                seen += new
                on_item(seen)  # only when another question completes
    e = end[-1] if end else StreamEnd()
    return "".join(parts), e.prompt_tokens, e.completion_tokens
