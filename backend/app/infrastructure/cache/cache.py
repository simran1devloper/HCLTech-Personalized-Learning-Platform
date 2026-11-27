"""Cache with versioned deterministic keys, TTL, stampede protection and graceful failure."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any, Awaitable, Callable, Protocol

from app.observability.metrics import metrics

CACHE_SCHEMA_VERSION = "v1"


def make_key(namespace: str, *, user_id: str | None = None, version: str | int = 1, **parts: Any) -> str:
    """Deterministic key. Private/personalised entries MUST pass user_id so they can never be shared across users.
    Parts are canonical-JSON hashed so ordering and spacing cannot change the key."""
    digest = hashlib.sha256(json.dumps(parts, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()[:32]
    scope = f"u:{user_id}" if user_id else "shared"
    return f"{CACHE_SCHEMA_VERSION}:{namespace}:{version}:{scope}:{digest}"


class CacheBackend(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl: int) -> None: ...
    async def delete_prefix(self, prefix: str) -> None: ...


class InMemoryBackend:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._d: dict[str, tuple[float, str]] = {}
        self._clock = clock

    async def get(self, key):
        v = self._d.get(key)
        if not v:
            return None
        if v[0] <= self._clock():
            self._d.pop(key, None)
            return None
        return v[1]

    async def set(self, key, value, ttl):
        self._d[key] = (self._clock() + ttl, value)

    async def delete_prefix(self, prefix):
        for k in [k for k in self._d if k.startswith(prefix)]:
            del self._d[k]


class RedisBackend:
    def __init__(self, client):
        self.r = client

    async def get(self, key):
        return await self.r.get(key)

    async def set(self, key, value, ttl):
        await self.r.set(key, value, ex=ttl)

    async def delete_prefix(self, prefix):
        async for k in self.r.scan_iter(match=prefix + "*", count=200):
            await self.r.delete(k)


class Cache:
    def __init__(self, backend: CacheBackend):
        self.backend = backend
        self._locks: dict[str, asyncio.Lock] = {}

    async def get(self, key: str, ns: str = "") -> Any | None:
        try:
            raw = await self.backend.get(key)
        except Exception:  # Redis down => behave as a miss, never fail the request
            metrics.inc("cache_errors_total", namespace=ns)
            return None
        metrics.inc("cache_hits_total" if raw is not None else "cache_misses_total", namespace=ns)
        return None if raw is None else json.loads(raw)

    async def set(self, key: str, value: Any, ttl: int, ns: str = "") -> None:
        try:
            await self.backend.set(key, json.dumps(value, default=str), ttl)
        except Exception:
            metrics.inc("cache_errors_total", namespace=ns)

    async def invalidate(self, namespace: str) -> None:
        """Drop every entry in a namespace. For per-user/per-entity invalidation prefer bumping the `version`
        passed to make_key (O(1), no scan); old entries then expire via TTL."""
        try:
            await self.backend.delete_prefix(f"{CACHE_SCHEMA_VERSION}:{namespace}:")
        except Exception:
            metrics.inc("cache_errors_total", namespace=namespace)

    async def get_or_set(self, key: str, ttl: int, producer: Callable[[], Awaitable[Any]], ns: str = "") -> Any:
        """Single-flight: concurrent misses for one key run `producer` once (per process)."""
        hit = await self.get(key, ns)
        if hit is not None:
            return hit
        lock = self._locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                hit = await self.get(key, ns)
                if hit is not None:
                    return hit
                value = await producer()
                await self.set(key, value, ttl, ns)
                return value
        finally:
            if not lock.locked():
                self._locks.pop(key, None)
