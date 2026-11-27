"""Tiny in-process Prometheus-text metrics registry (no external dependency)."""
from __future__ import annotations

import threading
from collections import defaultdict


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple], float] = defaultdict(float)
        self._sums: dict[tuple[str, tuple], list[float]] = defaultdict(lambda: [0.0, 0])

    @staticmethod
    def _k(name, labels):
        return name, tuple(sorted((labels or {}).items()))

    def inc(self, name: str, value: float = 1.0, **labels):
        with self._lock:
            self._counters[self._k(name, labels)] += value

    def observe(self, name: str, value: float, **labels):
        with self._lock:
            s = self._sums[self._k(name, labels)]
            s[0] += value
            s[1] += 1

    def get(self, name: str, **labels) -> float:
        return self._counters.get(self._k(name, labels), 0.0)

    def render(self) -> str:
        def fmt(labels):
            return "{" + ",".join(f'{k}="{v}"' for k, v in labels) + "}" if labels else ""
        lines = []
        with self._lock:
            for (n, l), v in sorted(self._counters.items()):
                lines.append(f"{n}{fmt(l)} {v}")
            for (n, l), (s, c) in sorted(self._sums.items()):
                lines.append(f"{n}_sum{fmt(l)} {s}")
                lines.append(f"{n}_count{fmt(l)} {c}")
        return "\n".join(lines) + "\n"

    def reset(self):
        with self._lock:
            self._counters.clear()
            self._sums.clear()


metrics = Metrics()
