"""Conversation memory under a token budget.

The newest turns are kept verbatim; an over-long turn is shortened, never dropped (dropping it would make "how does it work?" unanswerable).
Turns that no longer fit are condensed into an extractive summary (no extra model call)."""
from __future__ import annotations

import re

CHARS_PER_TOKEN = 3.5  # conservative: code and punctuation tokenise denser than prose


def tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 1


def shorten(text: str, max_tokens: int) -> str:
    """Keep the beginning and the end (definitions come first, conclusions and code tend to come last)."""
    limit = int(max_tokens * CHARS_PER_TOKEN)
    if len(text) <= limit:
        return text
    head = int(limit * 0.7)
    tail = max(0, limit - head - 20)
    return text[:head] + "\n[... shortened ...]\n" + (text[-tail:] if tail else "")


def _usable(m: dict) -> bool:
    return m.get("status", "complete") in ("complete", "cancelled", "interrupted", "failed") and bool((m.get("content") or "").strip())


def _gist(text: str, n: int) -> str:
    line = re.sub(r"```.*?(```|$)", " [code] ", text, flags=re.S)
    line = re.sub(r"[#*`>|]+", " ", line)
    line = " ".join(line.split())
    return line[:n] + ("…" if len(line) > n else "")


def _summary(older: list[dict], budget: int) -> str:
    lines: list[str] = []
    used = 0
    for m in reversed(older):
        line = (f"- Learner asked: {_gist(m['content'], 140)}" if m["role"] == "user" else f"  You answered about: {_gist(m['content'], 100)}")
        if used + tokens(line) > budget:
            break
        lines.insert(0, line)
        used += tokens(line)
    first = next((m for m in older if m["role"] == "user"), None)   # the opening question usually names the topic
    if first and lines and _gist(first["content"], 140) not in lines[0]:
        lines.insert(0, f"- Conversation started with: {_gist(first['content'], 140)}")
    return "\n".join(lines)


def build_memory(messages: list[dict], budget_tokens: int) -> tuple[list[dict], str]:
    """Return (recent turns as [{role, content}], summary of older turns). Consecutive same-role turns are merged because some
    providers require strict alternation (e.g. after a failed reply the learner has two questions in a row)."""
    msgs: list[dict] = []
    for m in messages:
        if not _usable(m):
            continue
        if msgs and msgs[-1]["role"] == m["role"]:
            msgs[-1] = {"role": m["role"], "content": msgs[-1]["content"] + "\n\n" + m["content"]}
        else:
            msgs.append({"role": m["role"], "content": m["content"]})
    if not msgs or budget_tokens <= 0:
        return [], ""
    summary_budget = min(250, budget_tokens // 5)
    available = budget_tokens - summary_budget
    per_turn = max(80, available // 2)
    kept: list[dict] = []
    used = 0
    i = len(msgs)
    while i > 0:
        m = msgs[i - 1]
        text = shorten(m["content"], per_turn)
        cost = tokens(text)
        if used + cost > available:
            if len(kept) >= 2:
                break
            text = shorten(m["content"], max(40, (available - used) // (2 - len(kept))))   # the last exchange always survives
            cost = tokens(text)
        kept.insert(0, {"role": m["role"], "content": text})
        used += cost
        i -= 1
    if kept and kept[0]["role"] == "assistant" and len(kept) > 1:   # history sent to a provider starts with the learner
        kept = kept[1:]
        i += 1
    return kept, _summary(msgs[:i], summary_budget)
