"""Deterministic simulated model server for reproducible benchmarks.

It exercises the *real* section-generation code (prompts, parsing, retries, caching, events) with controllable
latency. Latency is simulated: ttft + output_tokens / throughput, scaled down by `time_scale` so runs are fast.
Reported times are converted back to *simulated seconds* (wall / time_scale). Token counts for the output are
the section's token budget times `fill`; input tokens are estimated as len(prompt)//4 (fake mode only).
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import List, Set

from services import content_generation as cg


@dataclass
class FakeModel:
    throughput_tps: float = 7.0   # measured on the hosted model (indicative)
    ttft_s: float = 2.0
    server_slots: int = 2         # parallel requests the "server" can process (others queue)
    time_scale: float = 0.005     # wall seconds per simulated second
    fill: float = 0.75            # fraction of the section token budget actually produced
    fail_sections: Set[str] = field(default_factory=set)   # sections that always fail (for failure scenarios)
    ledger: List[dict] = field(default_factory=list)
    _sem: asyncio.Semaphore | None = None

    def _slots(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.server_slots)
        return self._sem

    def reset(self) -> None:
        self.ledger.clear()

    @property
    def calls(self) -> int:
        return len(self.ledger)

    async def __call__(self, sec: cg.Section, prompt: str) -> cg.CallResult:
        tokens_in = max(1, len(prompt) // 4)
        tokens_out = int(sec.max_tokens * self.fill)
        simulated = self.ttft_s + tokens_out / self.throughput_tps
        async with self._slots():
            await asyncio.sleep(simulated * self.time_scale)
        failed = sec.name in self.fail_sections
        self.ledger.append({"section": sec.name, "tokens_in": tokens_in, "tokens_out": 0 if failed else tokens_out,
                            "simulated_s": simulated, "failed": failed})
        if failed:
            from app.core.exceptions import ProviderError
            raise ProviderError("simulated provider failure")
        return cg.CallResult(_output(sec, tokens_out), False, tokens_in, tokens_out, simulated * 1000, "fake", "fake")


def _output(sec: cg.Section, tokens: int) -> str:
    words = " ".join(f"word{i}" for i in range(max(10, tokens * 3 // 4)))
    if sec.kind == "markdown":
        return f"## {sec.name}\n\n{words}\n"
    if sec.kind == "text":
        return "Hello learner, welcome to this study session. " + words
    if sec.name == "key_concepts":
        return json.dumps({"key_concepts": [{"term": f"T{i}", "definition": f"D{i}"} for i in range(6)]})
    if sec.name == "flashcards":
        return json.dumps({"flashcards": [{"question": f"Q{i}", "answer": f"A{i}"} for i in range(8)]})
    return json.dumps({"weak_areas": ["a"], "next_topics": [{"title": "t", "reason": "r"}], "practice_suggestions": ["p"],
                       "resources": [{"title": "r", "description": "d"}]})
