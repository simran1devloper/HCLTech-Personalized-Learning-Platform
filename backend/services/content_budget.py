"""Time-budget planning for one study guide.

A study guide is ~9,500 tokens at most. A cloud model writes that in under a minute; a CPU-only 8B model at ~4 tokens/s needs
40 minutes. Instead of letting every section hit a timeout, the pipeline *measures* the model's real speed after each section and
plans what still fits into the remaining time:

    1. everything at full length            (fast model)
    2. core sections full, optional deferred (medium)
    3. core markdown sections shortened      (slow)
    4. lowest-priority core sections deferred (very slow)

Deferred sections are not failures: the learner can generate them on demand. JSON and text sections are never shortened (a cut-off
JSON list is invalid); they are only ever deferred as a last resort.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Optional

from services import prompt_registry as pr

SAFETY = 0.85            # plan for 85% of the measured speed: speed varies with prompt length, swapping and thermal throttling
EMA_ALPHA = 0.5          # weight of the newest measurement

_speed: Dict[str, float] = {}   # tokens/second per model, remembered for the life of the process (seeds the next guide)


def record_speed(model_key: str, tokens: int, seconds: float) -> Optional[float]:
    """Fold one finished section into the model's effective tokens/s (includes queueing and prompt evaluation, i.e. what a user
    actually waits for). Ignores samples too small or too fast to be meaningful (fakes, cache hits)."""
    if tokens < 30 or seconds < 0.5:
        return _speed.get(model_key)
    tps = tokens / seconds
    prev = _speed.get(model_key)
    _speed[model_key] = tps if prev is None else EMA_ALPHA * tps + (1 - EMA_ALPHA) * prev
    return _speed[model_key]


def speed(model_key: str) -> Optional[float]:
    return _speed.get(model_key)


def reset_speeds() -> None:
    _speed.clear()


@dataclass(frozen=True)
class Item:
    name: str
    kind: str        # markdown | json | text
    max_tokens: int


def _expected(tokens: int) -> float:
    return tokens * pr.EXPECTED_FRACTION


def plan_tokens(items: Iterable[Item], remaining_s: float, tps: Optional[float], parallel: int = 1) -> Dict[str, Optional[int]]:
    """Allowed max_tokens per section (None = defer to on-demand). Unknown speed or no time limit => everything at full length."""
    items = list(items)
    full: Dict[str, Optional[int]] = {i.name: i.max_tokens for i in items}
    if not tps or tps <= 0 or remaining_s is None:
        return full
    avail = max(0.0, remaining_s) * tps * max(1, parallel) * SAFETY
    if sum(_expected(i.max_tokens) for i in items) <= avail:
        return full

    rank = {n: k for k, n in enumerate(pr.CORE_SECTIONS + pr.OPTIONAL_SECTIONS)}
    ordered = sorted(items, key=lambda i: rank.get(i.name, len(rank)))
    core = [i for i in ordered if i.name in pr.CORE_SECTIONS]
    optional = [i for i in ordered if i.name not in pr.CORE_SECTIONS]

    core_full = sum(_expected(i.max_tokens) for i in core)
    out: Dict[str, Optional[int]] = {}
    if core_full <= avail:                       # tier 2: all core sections at full length, as many optional ones as still fit
        left = avail - core_full
        for i in core:
            out[i.name] = i.max_tokens
        for i in optional:
            if _expected(i.max_tokens) <= left:
                out[i.name] = i.max_tokens
                left -= _expected(i.max_tokens)
            else:
                out[i.name] = None
        return out

    for i in optional:                           # tiers 3-4: no room for optional sections at all
        out[i.name] = None
    kept = list(core)
    while True:
        fixed = sum(_expected(i.max_tokens) for i in kept if i.kind != "markdown")
        shrinkable = [i for i in kept if i.kind == "markdown"]
        want = sum(_expected(i.max_tokens) for i in shrinkable)
        floor = sum(_expected(pr.MIN_TOKENS.get(i.name, i.max_tokens)) for i in shrinkable)
        if fixed + floor <= avail or len(kept) <= 1:
            break
        kept.pop()                               # defer the lowest-priority core section and re-check
    room = max(0.0, avail - fixed)
    ratio = 1.0 if not want else min(1.0, room / want)
    for i in core:
        if i not in kept:
            out[i.name] = None
        elif i.kind == "markdown":
            out[i.name] = max(pr.MIN_TOKENS.get(i.name, i.max_tokens), int(i.max_tokens * ratio))
        else:
            out[i.name] = i.max_tokens
    return out
