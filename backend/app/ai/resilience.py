"""Retry with bounded exponential backoff + jitter, and a circuit breaker."""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

from app.core.exceptions import CircuitOpenError, ProviderError, ProviderRateLimitError

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0
    jitter: float = 0.5  # fraction of delay randomised
    # HTTP 429 has its own allowance (it does not consume max_attempts)
    rate_limit_retries: int = 3
    rate_limit_base_delay: float = 1.0
    rate_limit_max_delay: float = 20.0


def backoff_delay(policy: RetryPolicy, attempt: int, rng: random.Random | None = None) -> float:
    """attempt is 1-based (delay applied *after* failed attempt N). Always <= max_delay."""
    r = rng or random
    raw = min(policy.max_delay, policy.base_delay * (2 ** (attempt - 1)))
    low = raw * (1 - policy.jitter)
    return low + r.random() * (raw - low)


def rate_limit_delay(policy: RetryPolicy, retry: int, retry_after_s: float | None = None, rng: random.Random | None = None) -> float | None:
    """How long to wait before rate-limit retry number `retry` (1-based), or None when waiting is pointless: the allowance is
    used up, or the provider asks for longer than we are willing to hold the request. Jittered so parallel calls spread out."""
    if retry > policy.rate_limit_retries:
        return None
    r = rng or random
    raw = min(policy.rate_limit_max_delay, policy.rate_limit_base_delay * (2 ** (retry - 1)))
    delay = raw * (1 - policy.jitter) + r.random() * raw * policy.jitter
    if retry_after_s is not None:
        if retry_after_s > policy.rate_limit_max_delay:
            return None
        delay = max(delay, min(policy.rate_limit_max_delay, retry_after_s * (1 + 0.25 * r.random())))
    return delay


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    wait_on_rate_limit: bool = True,
) -> T:
    """`wait_on_rate_limit=False` surfaces a 429 at once, for a caller that has another provider to try instead."""
    last: Exception | None = None
    attempt, throttled = 1, 0
    while attempt <= policy.max_attempts:
        try:
            return await fn()
        except ProviderRateLimitError as e:
            delay = rate_limit_delay(policy, throttled + 1, e.retry_after_s) if wait_on_rate_limit else None
            if delay is None:
                raise
            throttled += 1
            await sleep(delay)
        except ProviderError as e:
            last = e
            if not e.recoverable or not e.retryable or isinstance(e, CircuitOpenError):
                raise  # non-recoverable failures are never retried
            if attempt == policy.max_attempts:
                break
            await sleep(backoff_delay(policy, attempt))
            attempt += 1
        except asyncio.TimeoutError as e:
            last = e
            if attempt == policy.max_attempts:
                break
            await sleep(backoff_delay(policy, attempt))
            attempt += 1
    assert last is not None
    raise last


class CircuitBreaker:
    """closed -> open (after N consecutive failures) -> half-open (after cooldown) -> closed."""

    def __init__(self, failure_threshold: int = 5, reset_timeout: float = 30.0,
                 clock: Callable[[], float] = time.monotonic):
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_in_flight = False

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        if self._clock() - self._opened_at >= self.reset_timeout:
            return "half_open"
        return "open"

    def before_call(self) -> None:
        s = self.state
        if s == "open":
            raise CircuitOpenError("circuit open")
        if s == "half_open":
            if self._probe_in_flight:
                raise CircuitOpenError("circuit half-open, probe in flight")
            self._probe_in_flight = True

    def release(self) -> None:
        """The call ended without telling us anything about provider health (rate limit, caller error, cancellation)."""
        self._probe_in_flight = False

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._probe_in_flight = False

    def record_failure(self) -> None:
        self._probe_in_flight = False
        self._failures += 1
        if self._failures >= self.failure_threshold or self._opened_at is not None:
            self._opened_at = self._clock()
