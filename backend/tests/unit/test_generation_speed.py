"""Parallel content generation: rate limits are waited out without tripping the breaker, calls are capped process-wide, and a
whole guide survives a burst of HTTP 429."""
import asyncio

import httpx
import pytest

from app.ai import limits
from app.ai.model_router import ModelRouter, TaskRequirements
from app.ai.providers.base import classify_http_error
from app.ai.providers.openai_compat import OpenAICompatProvider
from app.ai.resilience import CircuitBreaker, RetryPolicy, rate_limit_delay, retry_async
from app.ai.types import GenerationParams, LLMResponse, ModelCapabilities, TokenUsage
from app.core.config import get_settings
from app.core.exceptions import ProviderError, ProviderRateLimitError
from services import content_generation as cg

STREAM = TaskRequirements(prefer="configured", needs_streaming=True)


class Provider:
    """Hosted fake: answers every section prompt; the first `limited` calls get HTTP 429."""
    def __init__(self, name="openrouter", limited=0, delay=0.0, local=False):
        self.name, self.model = name, name + "-m"
        self.limited, self.delay, self.calls, self.running, self.peak = limited, delay, 0, 0, 0
        self.caps = ModelCapabilities(structured_output=True, local=local)

    def model_capabilities(self): return self.caps
    def token_usage(self): return TokenUsage()
    async def health_check(self): return True

    async def generate(self, prompt, params=GenerationParams()):
        self.calls += 1
        if self.calls <= self.limited:
            raise ProviderRateLimitError("429")
        return LLMResponse("ok", self.name, self.model)

    async def generate_stream(self, prompt, params=GenerationParams()):
        self.calls += 1
        if self.calls <= self.limited:
            raise ProviderRateLimitError("429")
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            await asyncio.sleep(self.delay)
            yield answer_for(prompt)
        finally:
            self.running -= 1


def answer_for(prompt: str) -> str:
    section = prompt.split("SECTION: ")[1].split()[0]
    json_answers = {
        "key_concepts": '{"key_concepts": [' + ",".join(f'{{"term": "Term {i}", "definition": "A sufficiently long definition number {i} of the term."}}' for i in range(6)) + "]}",
        "flashcards": '{"flashcards": [' + ",".join(f'{{"question": "What is thing {i}?", "answer": "It is the thing numbered {i} here."}}' for i in range(8)) + "]}",
        "recommendations": '{"weak_areas": ["a area", "b area"], "next_topics": [{"title": "Next one", "reason": "Because it follows."}], '
                           '"practice_suggestions": ["Build a thing."], "resources": [{"title": "Docs", "description": "The official documentation."}]}',
    }
    return json_answers.get(section, f"## {section}\n\n" + "A solid explanatory sentence about the topic. " * 6)


def router(*providers, sleeps=None, **policy):
    async def nosleep(d):
        if sleeps is not None:
            sleeps.append(d)
    return ModelRouter(list(providers), RetryPolicy(max_attempts=1, **policy), breaker_threshold=5, sleep=nosleep)


async def collect(r):
    return [t async for t in r.generate_stream("SECTION: summary x", GenerationParams(), STREAM)]


# ---------------- classification and delays ----------------
def test_429_is_a_rate_limit_carrying_retry_after():
    e = classify_http_error(429, headers={"retry-after": "7"})
    assert isinstance(e, ProviderRateLimitError) and e.recoverable and e.retry_after_s == 7
    assert classify_http_error(429, headers={"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}).retry_after_s is None
    assert not isinstance(classify_http_error(503), ProviderRateLimitError)


async def test_http_adapter_reads_the_retry_after_header():
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(429, headers={"Retry-After": "3"}, text="{}")))
    with pytest.raises(ProviderRateLimitError) as e:
        async for _ in OpenAICompatProvider("m", client=http).generate_stream("x"):
            pass
    assert e.value.retry_after_s == 3


def test_delay_grows_is_bounded_jittered_and_gives_up():
    p = RetryPolicy(rate_limit_retries=3, rate_limit_base_delay=1, rate_limit_max_delay=20)
    d = [rate_limit_delay(p, n) for n in (1, 2, 3)]
    assert 0.5 <= d[0] <= 1 and 1 <= d[1] <= 2 and 2 <= d[2] <= 4 and rate_limit_delay(p, 4) is None
    assert len({round(rate_limit_delay(p, 1), 6) for _ in range(20)}) > 1          # parallel calls do not retry in lockstep
    assert 8 <= rate_limit_delay(p, 1, retry_after_s=8) <= 10
    assert rate_limit_delay(p, 1, retry_after_s=120) is None                       # asked to wait far too long: say so now


async def test_rate_limit_retries_do_not_consume_normal_attempts():
    calls, sleeps = [], []

    async def fn():
        calls.append(1)
        if len(calls) <= 2:
            raise ProviderRateLimitError("429")
        if len(calls) == 3:
            raise ProviderError("500")
        return "ok"

    async def sleep(d): sleeps.append(d)
    assert await retry_async(fn, RetryPolicy(max_attempts=2), sleep) == "ok" and len(calls) == 4 and len(sleeps) == 3


# ---------------- router ----------------
async def test_stream_waits_out_a_rate_limit_and_the_breaker_stays_closed():
    p, sleeps = Provider(limited=3), []
    r = router(p, sleeps=sleeps)
    assert await collect(r) and p.calls == 4 and len(sleeps) == 3
    many = Provider(limited=40)
    r = router(many, rate_limit_retries=0)
    for _ in range(12):                                  # far more than the breaker threshold of 5
        with pytest.raises(ProviderRateLimitError):
            await collect(r)
    assert many.calls == 12 and r._breakers[id(many)].state == "closed"


async def test_rate_limit_fails_over_at_once_when_another_provider_exists():
    first, second, sleeps = Provider("a", limited=99), Provider("b"), []
    assert await collect(router(first, second, sleeps=sleeps)) and (first.calls, second.calls, sleeps) == (1, 1, [])
    first, second = Provider("a", limited=99), Provider("b")
    assert (await router(first, second, sleeps=sleeps).generate("x")).provider == "b" and first.calls == 1 and not sleeps


async def test_non_streaming_calls_wait_too_and_eventually_give_up():
    p, sleeps = Provider(limited=2), []
    assert (await router(p, sleeps=sleeps).generate("x")).text == "ok" and len(sleeps) == 2
    p = Provider(limited=99)
    with pytest.raises(ProviderRateLimitError):
        await router(p).generate("x")
    assert p.calls == 4


async def test_a_cancelled_half_open_probe_does_not_block_the_provider_forever():
    t = [0.0]
    p = Provider(delay=5)
    r = router(p)
    br = r._breakers[id(p)] = CircuitBreaker(1, 10, clock=lambda: t[0])
    br.record_failure()
    t[0] = 11                                             # half-open
    task = asyncio.create_task(collect(r))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    br.before_call()                                      # a new probe is allowed


# ---------------- process-wide cap ----------------
async def test_generation_slots_cap_concurrency(monkeypatch):
    monkeypatch.setattr(get_settings(), "CONTENT_GLOBAL_CONCURRENCY", 3)
    running, peak = 0, 0

    async def job():
        nonlocal running, peak
        async with limits.generation_slot():
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.01)
            running -= 1
    await asyncio.gather(*[job() for _ in range(12)])
    assert peak == 3
    monkeypatch.setattr(get_settings(), "CONTENT_GLOBAL_CONCURRENCY", 0)
    peak = 0
    await asyncio.gather(*[job() for _ in range(12)])
    assert peak == 12


# ---------------- whole guides ----------------
def use(monkeypatch, r):
    monkeypatch.setattr(cg, "get_router", lambda: r)
    monkeypatch.setattr(get_settings(), "CONTENT_STREAMING", True)


async def guide(topic="Kafka"):
    return [ev["data"] async for ev in cg.run_pipeline(topic, "Intermediate", 6) if ev["event"] == "section"]


def test_default_hosted_concurrency_covers_a_whole_guide(monkeypatch):
    use(monkeypatch, router(Provider()))
    assert type(get_settings()).model_fields["CONTENT_SECTION_CONCURRENCY_CLOUD"].default == len(cg.SECTIONS) == 10


async def test_all_sections_of_a_hosted_guide_are_written_at_once(monkeypatch):
    p = Provider(delay=0.05)
    use(monkeypatch, router(p))
    monkeypatch.setattr(get_settings(), "CONTENT_SECTION_CONCURRENCY_CLOUD", 10)
    out = await guide()
    assert p.peak == 10 and len(out) == 10 and all(s["ok"] for s in out)


async def test_concurrent_guides_share_the_global_cap_and_all_complete(monkeypatch):
    p = Provider(delay=0.02)
    use(monkeypatch, router(p))
    monkeypatch.setattr(get_settings(), "CONTENT_SECTION_CONCURRENCY_CLOUD", 10)
    monkeypatch.setattr(get_settings(), "CONTENT_GLOBAL_CONCURRENCY", 12)
    results = await asyncio.gather(guide("Kafka"), guide("Redis"), guide("Docker"))
    assert p.peak <= 12 and all(len(r) == 10 and all(s["ok"] for s in r) for r in results)


async def test_a_burst_of_429_does_not_lose_any_section(monkeypatch):
    p = Provider(limited=15, delay=0.01)                  # more rejections than the breaker threshold, all at once
    use(monkeypatch, router(p))
    out = await guide()
    assert len(out) == 10 and all(s["ok"] for s in out), [s["name"] for s in out if not s["ok"]]


async def test_learning_plans_use_the_same_concurrency_as_guides(monkeypatch):
    import inspect

    from services import learning_plan as lp
    assert inspect.signature(lp.stream_plan).parameters["concurrency"].default is None
    assert "cg.effective_concurrency()" in inspect.getsource(lp.stream_plan)
