import pytest
from pydantic import BaseModel
from app.ai.model_router import Complexity, ModelRouter, TaskRequirements
from app.ai.resilience import RetryPolicy
from app.ai.types import GenerationParams, LLMResponse, ModelCapabilities, TokenUsage
from app.core.exceptions import CapabilityError, NonRecoverableProviderError, ProviderError


class Fake:
    def __init__(self, name, caps=None, fail=None):
        self.name, self.model = name, name + "-m"
        self.caps = caps or ModelCapabilities(structured_output=True)
        self.fail, self.calls = fail, 0

    def model_capabilities(self): return self.caps
    def token_usage(self): return TokenUsage()
    async def health_check(self): return True
    async def generate_embeddings(self, t): return []

    async def generate(self, prompt, params=GenerationParams()):
        self.calls += 1
        if self.fail:
            raise self.fail
        return LLMResponse("ok", self.name, self.model, TokenUsage(1, 1), 5.0)

    async def generate_stream(self, prompt, params=GenerationParams()):
        self.calls += 1
        if self.fail:
            raise self.fail
        yield "x"

    async def generate_structured(self, prompt, schema, params=GenerationParams()):
        r = await self.generate(prompt, params)
        return schema(v=1), r


class S(BaseModel):
    v: int


async def nosleep(d): pass


def router(*ps, attempts=2):
    return ModelRouter(list(ps), RetryPolicy(max_attempts=attempts), sleep=nosleep)


async def test_failover_on_recoverable():
    a, b = Fake("a", fail=ProviderError("down")), Fake("b")
    r = await router(a, b).generate("x")
    assert r.provider == "b" and a.calls == 2  # retried then failed over


async def test_no_failover_or_retry_on_non_recoverable():
    a, b = Fake("a", fail=NonRecoverableProviderError("bad")), Fake("b")
    with pytest.raises(NonRecoverableProviderError):
        await router(a, b).generate("x")
    assert a.calls == 1 and b.calls == 0


async def test_structured_requirement_never_downgraded():
    weak = Fake("weak", ModelCapabilities(structured_output=False))
    with pytest.raises(CapabilityError):
        await router(weak).generate_structured("x", S)


async def test_privacy_local_only_never_uses_cloud():
    cloud, local = Fake("cloud"), Fake("local", ModelCapabilities(structured_output=True, local=True))
    r = await router(cloud, local).generate("x", req=TaskRequirements(local_only=True))
    assert r.provider == "local" and cloud.calls == 0
    with pytest.raises(CapabilityError):
        await router(cloud).generate("x", req=TaskRequirements(local_only=True))


async def test_local_failure_does_not_leak_to_cloud():
    cloud, local = Fake("cloud"), Fake("local", ModelCapabilities(local=True), fail=ProviderError("down"))
    with pytest.raises(ProviderError):
        await router(cloud, local).generate("x", req=TaskRequirements(local_only=True))
    assert cloud.calls == 0


def test_complexity_routing_prefers_fit():
    small = Fake("small", ModelCapabilities(quality_tier=1, relative_cost=0.1, relative_latency=0.5))
    big = Fake("big", ModelCapabilities(quality_tier=3, relative_cost=3, relative_latency=2))
    r = router(big, small)
    assert r.select(TaskRequirements(complexity=Complexity.LOW))[0] is small
    assert r.select(TaskRequirements(complexity=Complexity.HIGH))[0] is big


async def test_circuit_opens_and_skips_provider():
    a, b = Fake("a", fail=ProviderError("down")), Fake("b")
    rt = ModelRouter([a, b], RetryPolicy(max_attempts=1), breaker_threshold=2, sleep=nosleep)
    for _ in range(3):
        await rt.generate("x")
    assert a.calls == 2  # third request skipped a: circuit open


async def test_stream_failover_before_first_token_only():
    a, b = Fake("a", fail=ProviderError("down")), Fake("b")
    out = [c async for c in router(a, b).generate_stream("x")]
    assert out == ["x"]


async def test_metrics_recorded():
    from app.observability.metrics import metrics
    metrics.reset()
    await router(Fake("a")).generate("x")
    assert metrics.get("llm_prompt_tokens_total", provider="a") == 1
    assert "llm_requests_total" in metrics.render()


async def test_account_errors_fail_over_without_retrying():
    from app.core.exceptions import ProviderAccountError
    a, b = Fake("a", fail=ProviderAccountError("HTTP 402")), Fake("b")
    r = await router(a, b, attempts=3).generate("x")
    assert r.provider == "b" and a.calls == 1  # no retry on the account failure, but cloud fallback still used


async def test_http_status_classification():
    from app.ai.providers.base import classify_http_error
    from app.core.exceptions import NonRecoverableProviderError, ProviderAccountError
    assert isinstance(classify_http_error(402), ProviderAccountError) and classify_http_error(402).recoverable
    assert not classify_http_error(402).retryable
    assert classify_http_error(503).retryable and isinstance(classify_http_error(400), NonRecoverableProviderError)
