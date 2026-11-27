"""OpenRouter: same wire format as OpenAI plus attribution headers; wired through settings; skipped without a key; never used when local-only is on."""
import json

import httpx
import pytest

from app.ai.factory import build_router
from app.ai.model_router import Complexity, TaskRequirements
from app.ai.providers.openrouter import OpenRouterProvider
from app.ai.types import GenerationParams
from app.core.config import AppSettings
from app.core.exceptions import ProviderAccountError, ProviderError


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_request_goes_to_openrouter_with_bearer_key_and_attribution_headers():
    seen = {}

    def h(req):
        seen["url"], seen["headers"], seen["body"] = str(req.url), req.headers, json.loads(req.content)
        return httpx.Response(200, json={"model": "openai/gpt-4o-mini", "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 7, "completion_tokens": 3}})
    p = OpenRouterProvider("openai/gpt-4o-mini", "sk-or-test", referer="http://localhost:8000", title="LearnAI", client=client(h))
    r = await p.generate("Say hello", GenerationParams(max_output_tokens=100))
    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert seen["headers"]["authorization"] == "Bearer sk-or-test" and seen["headers"]["http-referer"] == "http://localhost:8000" and seen["headers"]["x-title"] == "LearnAI"
    assert seen["body"]["model"] == "openai/gpt-4o-mini" and seen["body"]["max_tokens"] == 100 and seen["body"]["messages"] == [{"role": "user", "content": "Say hello"}]
    assert r.text == "hello" and r.provider == "openrouter" and r.usage.total == 10 and p.token_usage().total == 10


async def test_json_mode_and_optional_headers():
    seen = {}

    def h(req):
        seen["headers"], seen["body"] = req.headers, json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
    p = OpenRouterProvider("m", "k", client=client(h))                       # no referer / title configured: those headers are simply absent
    await p.generate("x", GenerationParams(json_mode=True))
    assert seen["body"]["response_format"] == {"type": "json_object"} and "http-referer" not in seen["headers"] and "x-title" not in seen["headers"]


async def test_bad_key_is_an_account_error_and_server_errors_are_retryable_without_leaking_the_key():
    p401 = OpenRouterProvider("m", "sk-or-secret", client=client(lambda r: httpx.Response(401, json={"error": {"message": "No auth credentials found"}})))
    with pytest.raises(ProviderAccountError) as e:
        await p401.generate("x")
    assert "sk-or-secret" not in str(e.value) and not e.value.retryable
    p503 = OpenRouterProvider("m", "k", client=client(lambda r: httpx.Response(503)))
    with pytest.raises(ProviderError) as e2:
        await p503.generate("x")
    assert e2.value.recoverable and e2.value.retryable


def settings(**kw):
    base = dict(SECRET_KEY="x" * 40, LLM_PROVIDERS="openrouter,ollama", OPENROUTER_API_KEY="", OLLAMA_BASE_URL="http://localhost:11434", OLLAMA_API_KEY="", GEMINI_API_KEY="", OPENAI_API_KEY="")
    return AppSettings(**{**base, **kw})


def test_settings_defaults_match_the_reference_configuration():
    s = settings()
    assert s.OPENROUTER_BASE_URL == "https://openrouter.ai/api/v1" and s.OPENROUTER_MODEL == "openai/gpt-4o-mini" and s.OPENROUTER_REFERER == "http://localhost:8000" and s.OPENROUTER_TITLE == "LearnAI"


def test_openrouter_is_skipped_without_a_key_and_used_first_when_configured():
    names = lambda s: [p.name for p in build_router(s).providers]  # noqa: E731
    assert names(settings()) == ["ollama"]                                                       # listed but no key: ignored, Ollama still works
    assert names(settings(OPENROUTER_API_KEY="   ")) == ["ollama"]                               # a blank key is not a key
    assert names(settings(OPENROUTER_API_KEY="sk-or-1")) == ["openrouter", "ollama"]            # priority order follows LLM_PROVIDERS
    assert names(settings(OPENROUTER_API_KEY="sk-or-1", LLM_PROVIDERS="ollama,openrouter")) == ["ollama", "openrouter"]
    only = build_router(settings(OPENROUTER_API_KEY="sk-or-1", LLM_PROVIDERS="openrouter")).providers[0]
    assert only.model == "openai/gpt-4o-mini" and only.base_url == "https://openrouter.ai/api/v1" and only.referer == "http://localhost:8000" and only.title == "LearnAI"
    custom = build_router(settings(OPENROUTER_API_KEY="sk-or-1", LLM_PROVIDERS="openrouter", OPENROUTER_MODEL="meta-llama/llama-3.1-8b-instruct")).providers[0]
    assert custom.model == "meta-llama/llama-3.1-8b-instruct"


def test_the_key_is_stripped_and_never_part_of_the_provider_repr_or_capabilities():
    p = build_router(settings(OPENROUTER_API_KEY="  sk-or-secret \n", LLM_PROVIDERS="openrouter")).providers[0]
    assert p.api_key == "sk-or-secret" and "sk-or-secret" not in repr(p.model_capabilities())


async def test_cloud_provider_is_never_used_when_local_only_is_on():
    router = build_router(settings(OPENROUTER_API_KEY="sk-or-1", LLM_PROVIDERS="openrouter"))
    assert router.providers[0].model_capabilities().local is False
    from app.core.exceptions import CapabilityError
    with pytest.raises(CapabilityError):
        await router.generate("x", GenerationParams(), TaskRequirements(complexity=Complexity.MEDIUM, local_only=True))
