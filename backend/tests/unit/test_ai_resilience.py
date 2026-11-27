import random
import pytest
from app.ai.resilience import CircuitBreaker, RetryPolicy, backoff_delay, retry_async
from app.core.exceptions import CircuitOpenError, NonRecoverableProviderError, ProviderError


def test_backoff_bounded_and_jittered():
    pol = RetryPolicy(base_delay=1, max_delay=4, jitter=0.5)
    rng = random.Random(1)
    for attempt in range(1, 10):
        d = backoff_delay(pol, attempt, rng)
        assert 0 < d <= 4
    assert backoff_delay(pol, 1, random.Random(1)) != backoff_delay(pol, 1, random.Random(2))


async def test_retry_recoverable_then_success():
    calls, sleeps = [], []

    async def fn():
        calls.append(1)
        if len(calls) < 3:
            raise ProviderError("boom")
        return "ok"

    async def sleep(d): sleeps.append(d)
    assert await retry_async(fn, RetryPolicy(max_attempts=3), sleep) == "ok"
    assert len(calls) == 3 and len(sleeps) == 2


async def test_non_recoverable_not_retried():
    calls = []

    async def fn():
        calls.append(1)
        raise NonRecoverableProviderError("bad request")

    async def sleep(d): pass
    with pytest.raises(NonRecoverableProviderError):
        await retry_async(fn, RetryPolicy(max_attempts=5), sleep)
    assert len(calls) == 1


async def test_retry_exhaustion_raises_last():
    async def fn(): raise ProviderError("x")
    async def sleep(d): pass
    with pytest.raises(ProviderError):
        await retry_async(fn, RetryPolicy(max_attempts=2), sleep)


def test_circuit_breaker_lifecycle():
    t = [0.0]
    br = CircuitBreaker(failure_threshold=2, reset_timeout=10, clock=lambda: t[0])
    br.record_failure(); assert br.state == "closed"
    br.record_failure(); assert br.state == "open"
    with pytest.raises(CircuitOpenError):
        br.before_call()
    t[0] = 11
    assert br.state == "half_open"
    br.before_call()  # one probe allowed
    with pytest.raises(CircuitOpenError):
        br.before_call()
    br.record_failure()  # failed probe reopens
    assert br.state == "open"
    t[0] = 25
    br.before_call(); br.record_success()
    assert br.state == "closed"
