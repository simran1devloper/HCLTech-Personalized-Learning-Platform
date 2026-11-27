import base64
import json

import httpx

from app.ai.providers.ollama import OllamaProvider


def client(h):
    return httpx.AsyncClient(transport=httpx.MockTransport(h))


async def test_basic_auth_matches_the_python_client_snippet_and_ngrok_header():
    seen = {}

    def h(req):
        seen.update(req.headers)
        return httpx.Response(200, json={"message": {"content": "Hello!"}, "done": True})
    p = OllamaProvider("llama3.1:8b", "https://abc-123.ngrok-free.app", basic_auth="friend:pw", client=client(h))
    assert (await p.generate("Hello!")).text == "Hello!"
    # identical to base64.b64encode(b"friend:pw") in the reference snippet
    assert seen["authorization"] == "Basic " + base64.b64encode(b"friend:pw").decode()
    assert seen["ngrok-skip-browser-warning"] == "true"


async def test_basic_auth_wins_over_bearer_and_is_absent_for_plain_local():
    seen = {}
    def h(req):
        seen.clear(); seen.update(req.headers)
        return httpx.Response(200, json={"message": {"content": "x"}})
    await OllamaProvider("m", "https://h.example.com", api_key="K", basic_auth="u:p", client=client(h)).generate("x")
    assert seen["authorization"].startswith("Basic ")
    await OllamaProvider("m", "http://localhost:11434", client=client(h)).generate("x")
    assert "authorization" not in seen and "ngrok-skip-browser-warning" not in seen


def test_remote_ollama_needs_credentials_and_is_classed_as_cloud():
    from app.ai.factory import build_router
    from app.core.config import AppSettings
    base = dict(LLM_PROVIDERS="ollama", OLLAMA_BASE_URL="https://x.ngrok-free.app", OLLAMA_MODEL="m", GEMINI_API_KEY="")
    import pytest
    with pytest.raises(RuntimeError):
        build_router(AppSettings(**base, OLLAMA_API_KEY="", OLLAMA_BASIC_AUTH="", _env_file=None))
    r = build_router(AppSettings(**base, OLLAMA_BASIC_AUTH="u:p", _env_file=None))
    assert r.providers[0].model_capabilities().local is False  # LLM_LOCAL_ONLY=true would refuse this host
