"""Small, dependency-free statistics helpers for benchmark reports."""
from __future__ import annotations

import math
from typing import Iterable, Sequence


def percentile(values: Sequence[float], p: float) -> float:
    """Linear-interpolation percentile (the 'inclusive' method used by NumPy's default). p in [0, 100]."""
    if not values:
        raise ValueError("percentile of empty sequence")
    if not 0 <= p <= 100:
        raise ValueError("p must be within [0, 100]")
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    k = (len(xs) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return float(xs[lo] + (xs[hi] - xs[lo]) * (k - lo))


def summarise(values: Iterable[float]) -> dict:
    xs = list(values)
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "min": min(xs), "p50": percentile(xs, 50), "p95": percentile(xs, 95), "max": max(xs),
            "mean": sum(xs) / len(xs)}


def relative_spread(a: float, b: float) -> float:
    """|a-b| / max(a,b); 0 when both are 0. Used to check two runs agree within a tolerance."""
    m = max(abs(a), abs(b))
    return 0.0 if m == 0 else abs(a - b) / m
