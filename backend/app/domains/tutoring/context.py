"""Tutor context selection under a token budget. Never ships the full history to the LLM.

Authorization: every input is scoped by `learner_id` at the repository layer; `build_context` additionally
refuses any item whose owner differs, so a bug upstream cannot leak another learner's data into a prompt."""
from __future__ import annotations

import re
from typing import Awaitable, Callable

from pydantic import BaseModel

from app.agents.safety import UNTRUSTED_PREAMBLE, fence_untrusted
from app.core.exceptions import ForbiddenError


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class ContextItem(BaseModel):
    owner_id: str | None  # learner id for private items; None for shared (public resources)
    kind: str  # proficiency | mistake | roadmap | lesson | resource | message
    text: str
    priority: float = 0.5  # static importance
    untrusted: bool = False  # retrieved / external text


def _tok(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", s.lower()))


def relevance(query: str, item: ContextItem) -> float:
    q, t = _tok(query), _tok(item.text)
    overlap = len(q & t) / (len(q) or 1)
    return 0.6 * overlap + 0.4 * item.priority


async def build_context(learner_id: str, query: str, items: list[ContextItem], budget_tokens: int = 1500,
                        summarize: Callable[[list[str]], Awaitable[str]] | None = None,
                        history: list[dict] | None = None, keep_recent: int = 4) -> dict:
    for it in items:
        if it.owner_id is not None and it.owner_id != learner_id:
            raise ForbiddenError("context item belongs to another learner")
    ranked = sorted(items, key=lambda i: -relevance(query, i))
    chosen: list[ContextItem] = []
    used = 0
    reserve = budget_tokens // 3 if history else 0  # leave room for conversation
    for it in ranked:
        cost = estimate_tokens(it.text)
        if used + cost > budget_tokens - reserve:
            continue
        chosen.append(it)
        used += cost
    history = history or []
    recent, older = history[-keep_recent:], history[:-keep_recent]
    summary = ""
    if older:
        texts = [f"{m['role']}: {m['content']}" for m in older]
        summary = await summarize(texts) if summarize else " | ".join(t[:80] for t in texts[-6:])
    hist_budget = budget_tokens - used
    kept_recent: list[dict] = []
    for m in reversed(recent):
        c = estimate_tokens(m["content"])
        if c > hist_budget:
            break
        kept_recent.insert(0, m)
        hist_budget -= c
    sections = [f"[{it.kind}] " + (fence_untrusted(it.text, it.kind) if it.untrusted else it.text) for it in chosen]
    prompt_context = UNTRUSTED_PREAMBLE + "\n" + "\n".join(sections)
    return {"context_text": prompt_context, "summary": summary, "recent": kept_recent,
            "included": [(i.kind, i.text[:40]) for i in chosen], "tokens_used": budget_tokens - hist_budget}
