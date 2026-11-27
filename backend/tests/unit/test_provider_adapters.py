import json
import httpx
import pytest
from pydantic import BaseModel
from app.ai.providers.gemini import GeminiProvider
from app.ai.providers.ollama import OllamaProvider
from app.ai.providers.openai_compat import OpenAICompatProvider
from app.core.exceptions import NonRecoverableProviderError, ProviderError, StructuredOutputError


class Out(BaseModel):
    answer: int


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_openai_generate_and_usage():
    def h(req):
        body = json.loads(req.content)
        assert body["model"] == "m" and req.headers["authorization"] == "Bearer k"
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}],
                                         "usage": {"prompt_tokens": 3, "completion_tokens": 2}})
    p = OpenAICompatProvider("m", "http://x/v1", "k", client=client(h))
    r = await p.generate("hello")
    assert r.text == "hi" and r.usage.total == 5 and r.provider == "openai_compat" and r.latency_ms >= 0
    assert p.token_usage().total == 5


async def test_openai_structured_valid_and_invalid():
    ok = lambda req: httpx.Response(200, json={"choices": [{"message": {"content": '```json\n{"answer": 4}\n```'}}]})
    obj, _ = await OpenAICompatProvider("m", "http://x", client=client(ok)).generate_structured("q", Out)
    assert obj.answer == 4
    bad = lambda req: httpx.Response(200, json={"choices": [{"message": {"content": '{"answer": "x"}'}}]})
    with pytest.raises(StructuredOutputError):
        await OpenAICompatProvider("m", "http://x", client=client(bad)).generate_structured("q", Out)


async def test_http_error_classification():
    p4 = OpenAICompatProvider("m", "http://x", client=client(lambda r: httpx.Response(400)))
    with pytest.raises(NonRecoverableProviderError):
        await p4.generate("x")
    p5 = OpenAICompatProvider("m", "http://x", client=client(lambda r: httpx.Response(503)))
    with pytest.raises(ProviderError) as ei:
        await p5.generate("x")
    assert ei.value.recoverable
    p429 = OpenAICompatProvider("m", "http://x", client=client(lambda r: httpx.Response(429)))
    with pytest.raises(ProviderError) as ei:
        await p429.generate("x")
    assert ei.value.recoverable


async def test_timeout_maps_to_recoverable():
    def h(req): raise httpx.ReadTimeout("t")
    with pytest.raises(ProviderError) as ei:
        await OllamaProvider("m", client=client(h)).generate("x")
    assert ei.value.recoverable


async def test_ollama_chat_stream_json_mode_and_cloud_auth():
    seen = {}

    def h(req):
        b = json.loads(req.content)
        seen["auth"], seen["path"] = req.headers.get("authorization"), req.url.path
        if b["stream"]:
            lines = [json.dumps({"message": {"content": "a"}}), json.dumps({"message": {"content": "b"}, "done": True})]
            return httpx.Response(200, content="\n".join(lines))
        if "format" not in b:
            return httpx.Response(200, json={"message": {"content": "plain"}})
        assert b["format"] == "json"
        return httpx.Response(200, json={"message": {"content": '{"answer": 1}'}, "prompt_eval_count": 4, "eval_count": 1})
    p = OllamaProvider("m", "https://ollama.com/api/chat", "k123", client=client(h))  # full endpoint URL is normalised
    assert p.base_url == "https://ollama.com"
    assert [c async for c in p.generate_stream("x")] == ["a", "b"]
    obj, r = await p.generate_structured("x", Out)
    assert obj.answer == 1 and r.usage.prompt_tokens == 4
    assert seen["auth"] == "Bearer k123" and seen["path"] == "/api/chat"
    assert await OllamaProvider("m", client=client(h)).generate("x") and seen["auth"] is None  # local: no auth header


def test_base_url_normalisation_and_cloud_is_not_local():
    from app.ai.providers.ollama import normalize_base_url
    from app.ai.factory import is_local_url
    for u in ("https://ollama.com", "https://ollama.com/", "https://ollama.com/api", "https://ollama.com/api/chat"):
        assert normalize_base_url(u) == "https://ollama.com"
    assert is_local_url("http://localhost:11434") and is_local_url("http://127.0.0.1:11434")
    assert not is_local_url("https://ollama.com")


async def test_gemini_key_in_header_not_url():
    def h(req):
        assert "key=" not in str(req.url) and req.headers["x-goog-api-key"] == "secret"
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "yo"}]}}],
                                         "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1}})
    r = await GeminiProvider("g", "secret", client=client(h)).generate("x")
    assert r.text == "yo" and r.usage.total == 3


async def test_openai_embeddings_and_health():
    def h(req):
        if req.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})
        return httpx.Response(200, json={})
    p = OpenAICompatProvider("m", "http://x/v1", embedding_model="e", client=client(h))
    assert await p.generate_embeddings(["a"]) == [[0.1, 0.2]]
    assert await p.health_check()
    with pytest.raises(NonRecoverableProviderError):
        await OllamaProvider("m", client=client(h)).generate_embeddings(["a"])
