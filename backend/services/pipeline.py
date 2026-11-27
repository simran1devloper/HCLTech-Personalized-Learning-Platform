"""
services/pipeline.py — shared helpers for streaming *agentic pipeline* progress to the UI.

Every stage event is real execution state (never simulated):
    {"id": "generate", "label": "...", "status": "pending|running|retrying|done|failed|skipped",
     "attempt": 1, "duration_ms": 1234, "detail": "..."}
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

Stage = Dict[str, Any]


def stage_list(*items: tuple) -> List[Stage]:
    """(id, label) or (id, label, icon) per stage."""
    return [{"id": t[0], "label": t[1], "status": "pending", **({"icon": t[2]} if len(t) > 2 and t[2] else {})} for t in items]


async def with_heartbeats(events, interval: float = 15.0):
    """Wrap an async event iterator: yields each event, or None every `interval` seconds of silence (the route turns that
    into an SSE comment line so proxies such as nginx/ngrok never drop an idle connection). Closing this closes `events`."""
    it = events.__aiter__()
    nxt = None
    try:
        while True:
            if nxt is None:
                nxt = asyncio.ensure_future(it.__anext__())
            done, _ = await asyncio.wait({nxt}, timeout=interval)
            if not done:
                yield None
                continue
            try:
                ev = nxt.result()
            except StopAsyncIteration:
                nxt = None
                return
            nxt = None
            yield ev
    finally:
        if nxt is not None:
            nxt.cancel()
            await asyncio.gather(nxt, return_exceptions=True)
        aclose = getattr(it, "aclose", None)
        if aclose:
            await aclose()


class Tracker:
    """Emits stage events onto an asyncio queue and records per-stage duration."""

    def __init__(self, queue: "asyncio.Queue[Optional[dict]]"):
        self.q = queue
        self._t0: Dict[str, float] = {}

    def chunk(self, name: str, **data: Any) -> None:
        """Provisional partial output of a section (replaced by the validated `section` event)."""
        self.q.put_nowait({"event": "chunk", "data": {"name": name, **data}})

    def emit(self, stage_id: str, label: str, status: str, **kw: Any) -> None:
        now = time.perf_counter()
        if status in ("running", "retrying"):
            self._t0.setdefault(stage_id, now)
        data: Stage = {"id": stage_id, "label": label, "status": status, **{k: v for k, v in kw.items() if v is not None}}
        if status in ("done", "failed", "skipped") and stage_id in self._t0:
            data["duration_ms"] = int((now - self._t0.pop(stage_id)) * 1000)
        self.q.put_nowait({"event": "stage", "data": data})
