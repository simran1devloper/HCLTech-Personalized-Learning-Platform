"""PR1 of content-generation v2: streamed sections, silence-based timeouts, per-section provider failover."""
import asyncio

import pytest

from app.ai.model_router import ModelRouter, TaskRequirements
from app.ai.resilience import RetryPolicy
from app.ai.types import GenerationParams, ModelCapabilities, StreamEnd, TokenUsage
from app.core.exceptions import ProviderError
from services import content_generation as cg


class Streamer:
    """Fake provider: `script` is a list of tokens or float delays (seconds of silence)."""
    def __init__(self, name, script, local=True):
        self.name, self.model = name, name + "-m"
        self.script, self.closed, self.calls = script, False, 0
        self.caps = ModelCapabilities(structured_output=True, local=local)

    def model_capabilities(self): return self.caps
    def token_usage(self): return TokenUsage()
    async def health_check(self): return True

    async def generate_stream(self, prompt, params=GenerationParams()):
        self.calls += 1
        try:
            for item in self.script:
                if isinstance(item, float):
                    await asyncio.sleep(item)
                else:
                    yield item
            if params.on_stream_end:
                params.on_stream_end(StreamEnd(self.name, self.model, "stop", 7, 3))
        finally:
            self.closed = True


def router(*ps):
    async def nosleep(d): pass
    return ModelRouter(list(ps), RetryPolicy(max_attempts=1), sleep=nosleep)


async def collect(r, **kw):
    p = GenerationParams(**kw)
    return [t async for t in r.generate_stream("x", p, TaskRequirements(prefer="configured", needs_streaming=True))]


async def test_silent_first_provider_fails_over_before_first_token():
    dead, live = Streamer("dead", [5.0, "never"]), Streamer("live", ["a", "b"])
    out = await collect(router(dead, live), first_token_timeout_s=0.05)
    assert out == ["a", "b"] and dead.closed and dead.calls == 1


async def test_slow_but_steady_stream_is_not_cut_off():
    slow = Streamer("slow", ["a", 0.03, "b", 0.03, "c"])
    assert await collect(router(slow), first_token_timeout_s=0.5, idle_timeout_s=0.2) == ["a", "b", "c"]


async def test_mid_stream_stall_raises_and_closes_upstream():
    s = Streamer("s", ["a", 5.0, "b"])
    with pytest.raises(ProviderError, match="idle"):
        await collect(router(s, Streamer("other", ["z"])), idle_timeout_s=0.05)
    assert s.closed  # no failover after output started: partial text must not be mixed across models


async def test_total_timeout_caps_a_stream_that_never_stalls():
    s = Streamer("s", ["a", 0.04, "b", 0.04, "c", 0.04, "d", 0.04, "e"])
    with pytest.raises(ProviderError):
        await collect(router(s), idle_timeout_s=1.0, total_timeout_s=0.1)


async def test_consumer_closing_the_stream_closes_the_upstream_request():
    s = Streamer("s", ["a", "b", 5.0, "c"])
    it = router(s).generate_stream("x", GenerationParams(), TaskRequirements(prefer="configured", needs_streaming=True))
    assert await it.__anext__() == "a"
    await it.aclose()
    assert s.closed


# ------------------------------------------------------------------ content_generation._call_model
def use_router(monkeypatch, r, **settings):
    from app.core.config import get_settings
    monkeypatch.setattr(cg, "get_router", lambda: r)
    st = get_settings()
    for k, v in settings.items():
        monkeypatch.setattr(st, k, v, raising=False)


async def test_call_model_streams_and_reports_usage(monkeypatch):
    use_router(monkeypatch, router(Streamer("ollama", ["Hello ", "world, this is a section."])), CONTENT_STREAMING=True)
    res = await cg._call_model(cg.SECTIONS[0], "prompt")
    assert res.text == "Hello world, this is a section." and not res.truncated
    assert (res.tokens_in, res.tokens_out, res.provider) == (7, 3, "ollama")


async def test_call_model_empty_stream_is_a_provider_error(monkeypatch):
    use_router(monkeypatch, router(Streamer("ollama", [])), CONTENT_STREAMING=True)
    with pytest.raises(ProviderError):
        await cg._call_model(cg.SECTIONS[0], "prompt")


async def test_section_recovers_on_second_provider_after_silent_first(monkeypatch):
    live_text = "## Overview\n\n" + "A solid paragraph about the topic. " * 5
    use_router(monkeypatch, router(Streamer("ollama", [5.0, "x"]), Streamer("gemini", [live_text], local=False)),
               CONTENT_STREAMING=True, LLM_FIRST_TOKEN_S=0.05, LLM_IDLE_TOKEN_S=0.05)
    value, ok = await cg.generate_section(cg.SECTIONS[0], "Python", "Beginner", 3)
    assert ok and "solid paragraph" in value


# ------------------------------------------------------------------ scheduling
def test_fast_sections_start_first_and_the_rest_keep_display_order():
    names = [s.name for s in cg.start_order(cg.SECTIONS)]
    assert names[:5] == cg.START_ORDER
    rest = [n for n in cg.SECTION_NAMES if n not in cg.START_ORDER]
    assert names[5:] == rest


def test_concurrency_depends_on_local_vs_cloud_first_provider(monkeypatch):
    use_router(monkeypatch, router(Streamer("ollama", [], local=True)), CONTENT_SECTION_CONCURRENCY=2, CONTENT_SECTION_CONCURRENCY_CLOUD=4)
    assert cg.effective_concurrency() == 2
    use_router(monkeypatch, router(Streamer("gemini", [], local=False)), CONTENT_SECTION_CONCURRENCY=2, CONTENT_SECTION_CONCURRENCY_CLOUD=4)
    assert cg.effective_concurrency() == 4


def test_remote_ollama_behind_a_tunnel_still_gets_the_low_concurrency(monkeypatch):
    from app.core.config import get_settings
    remote = Streamer("ollama", [], local=False)          # e.g. ngrok URL: not loopback, so not "local", but still one GPU
    use_router(monkeypatch, router(remote), CONTENT_SECTION_CONCURRENCY=2, CONTENT_SECTION_CONCURRENCY_CLOUD=4)
    monkeypatch.setattr(get_settings(), "OLLAMA_API_KEY", "", raising=False)
    assert cg.effective_concurrency() == 2
    monkeypatch.setattr(get_settings(), "OLLAMA_API_KEY", "ollama-cloud-key", raising=False)   # Ollama Cloud scales like an API
    assert cg.effective_concurrency() == 4
