"""A process-wide cap on concurrent generation calls (study guides and quiz questions).

Each guide runs many sections at once; without a shared cap, N learners generating together would multiply that by N and hit the
provider's rate limits. Calls above the cap wait here, outside the provider timeouts."""
from __future__ import annotations

import asyncio
import time
import weakref
from contextlib import asynccontextmanager

from app.core.config import get_settings
from app.observability.metrics import metrics

# one semaphore per event loop (asyncio primitives cannot be shared across loops)
_slots: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, tuple[int, asyncio.Semaphore]]" = weakref.WeakKeyDictionary()


@asynccontextmanager
async def generation_slot():
    limit = get_settings().CONTENT_GLOBAL_CONCURRENCY
    if limit <= 0:
        yield
        return
    loop = asyncio.get_running_loop()
    entry = _slots.get(loop)
    if entry is None or entry[0] != limit:
        entry = _slots[loop] = (limit, asyncio.Semaphore(limit))
    t0 = time.perf_counter()
    async with entry[1]:
        metrics.observe("generation_slot_wait_ms", (time.perf_counter() - t0) * 1000)
        yield
