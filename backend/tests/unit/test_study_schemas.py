"""PR2 of content-generation v2: Pydantic section contracts, native schema, repair retry."""
import json

import httpx
import pytest

from app.ai.providers.gemini import GeminiProvider, _gemini_schema
from app.ai.providers.ollama import OllamaProvider
from app.ai.types import GenerationParams
from app.domains.content_engine import study_schemas as ss
from services import content_generation as cg
from services import prompt_registry as pr

SEC = {s.name: s for s in cg.SECTIONS}


# ---------------------------------------------------------------- models
def test_key_concepts_unwrap_filter_and_cap():
    items = [{"term": f"T{i}", "definition": f"D{i}"} for i in range(10)] + [{"term": "", "definition": "x"}, "junk"]
    assert len(ss.validate_section("key_concepts", {"key_concepts": items})) == 8
    assert ss.validate_section("key_concepts", items[:3]) == items[:3]  # bare list accepted


def test_violations_are_repairable_messages_not_tracebacks():
    with pytest.raises(ss.SchemaViolation) as e:
        ss.validate_section("key_concepts", {"key_concepts": [{"term": "a", "definition": "b"}]})
    assert e.value.errors and "key_concepts" in e.value.errors[0] and "at least 3" in e.value.errors[0]
    with pytest.raises(ss.SchemaViolation, match="JSON object"):
        ss.validate_section("recommendations", ["not", "an", "object"])
    with pytest.raises(ss.SchemaViolation, match="at least one"):
        ss.validate_section("recommendations", {})
    assert isinstance(ss.SchemaViolation(["x"]), ValueError)  # existing `except ValueError` paths keep working


def test_recommendations_shape_is_unchanged():
    r = ss.validate_section("recommendations", {"weak_areas": [{"area": "joins"}], "next_topics": ["Locks"],
                                                "resources": [{"name": "Docs", "why": "x"}]})
    assert r == {"weak_areas": ["joins"], "practice_suggestions": [], "next_topics": [{"title": "Locks", "reason": ""}],
                 "resources": [{"title": "Docs", "description": ""}]}


@pytest.mark.parametrize("name", sorted(ss.SECTION_MODELS))
def test_provider_schema_is_self_contained_and_stable(name):
    text = json.dumps(ss.provider_schema(name))
    assert "$ref" not in text and "$defs" not in text and '"title": "' not in text.replace('"title": {"type"', "")
    assert ss.provider_schema(name) == ss.provider_schema(name)


def test_schema_marks_every_field_required_and_keeps_title_fields():
    s = ss.provider_schema("recommendations")["properties"]["next_topics"]["items"]
    assert set(s["required"]) == {"title", "reason"} and "title" in s["properties"]
    assert ss.provider_schema("key_concepts")["properties"]["key_concepts"]["minItems"] == 3


def test_schema_change_changes_the_cache_fingerprint():
    assert pr.guide_prompt_fingerprint().startswith("g") and ss.schema_fingerprint() in ss.schema_fingerprint() + "x"
    assert pr.section_schema("summary") is None and pr.section_schema("flashcards")


# ---------------------------------------------------------------- parse_section
def test_parse_section_reports_non_json_and_truncation():
    with pytest.raises(ss.SchemaViolation, match="not valid JSON"):
        cg.parse_section(SEC["flashcards"], "Sure! Here are some cards", False)
    with pytest.raises(ss.SchemaViolation, match="cut off"):
        cg.parse_section(SEC["flashcards"], '{"flashcards": [{"question": "a", "ans', True)


# ---------------------------------------------------------------- repair loop
async def test_invalid_output_is_repaired_with_the_errors_in_the_prompt():
    prompts = []
    good = json.dumps({"flashcards": [{"question": f"Q{i}", "answer": f"A{i}"} for i in range(5)]})

    async def call(sec, prompt):
        prompts.append(prompt)
        return (json.dumps({"flashcards": [{"question": "only one", "answer": "card"}]}), False) if len(prompts) == 1 else (good, False)
    value, ok = await cg.generate_section(SEC["flashcards"], "Python", "Beginner", 3, call)
    assert ok and len(value) == 5 and len(prompts) == 2
    assert "SECTION: flashcards" in prompts[1] and "rejected" in prompts[1] and "at least 3" in prompts[1]
    assert "only one" in prompts[1] and "rejected" not in prompts[0]


async def test_provider_error_retries_the_original_prompt_not_a_repair():
    prompts = []
    good = json.dumps({"flashcards": [{"question": f"Q{i}", "answer": f"A{i}"} for i in range(4)]})
    from app.core.exceptions import ProviderError

    async def call(sec, prompt):
        prompts.append(prompt)
        if len(prompts) == 1:
            raise ProviderError("down")
        return good, False
    _, ok = await cg.generate_section(SEC["flashcards"], "Python", "Beginner", 3, call)
    assert ok and prompts[0] == prompts[1]


async def test_repair_does_not_apply_to_markdown_sections():
    prompts = []

    async def call(sec, prompt):
        prompts.append(prompt)
        return "x", False  # too short
    _, ok = await cg.generate_section(SEC["summary"], "Python", "Beginner", 3, call)
    assert not ok and prompts[0] == prompts[1]


# ---------------------------------------------------------------- providers
def ollama(handler):
    return OllamaProvider("m", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_ollama_sends_schema_as_format_and_falls_back_when_rejected():
    seen = []
    schema = ss.provider_schema("flashcards")

    def h(req):
        body = json.loads(req.content)
        seen.append(body.get("format"))
        if isinstance(body.get("format"), dict):
            return httpx.Response(400, json={"error": "invalid format"})
        return httpx.Response(200, json={"message": {"content": "{}"}, "done_reason": "stop"})
    p = ollama(h)
    params = GenerationParams(json_mode=True, json_schema=schema)
    await p.generate("x", params)
    assert seen == [schema, "json"]            # tried the schema, downgraded once
    await p.generate("x", params)
    assert seen[2:] == ["json"]                # and remembers
    await p.generate("x", GenerationParams())  # no json mode: the request carries no `format` at all
    assert seen[-1] is None


async def test_ollama_stream_uses_schema_and_downgrades_on_400():
    seen = []

    def h(req):
        body = json.loads(req.content)
        seen.append(body["format"])
        if isinstance(body["format"], dict):
            return httpx.Response(400, json={"error": "nope"})
        return httpx.Response(200, content=b'{"message":{"content":"{}"},"done":true,"done_reason":"stop"}\n')
    p = ollama(h)
    out = [t async for t in p.generate_stream("x", GenerationParams(json_mode=True, json_schema=ss.provider_schema("flashcards")))]
    assert out == ["{}"] and isinstance(seen[0], dict) and seen[1] == "json"


def test_gemini_schema_is_openapi_subset_with_uppercase_types():
    g = _gemini_schema(ss.provider_schema("recommendations"))
    assert g["type"] == "OBJECT" and g["properties"]["weak_areas"]["items"]["type"] == "STRING"
    assert "title" in g["properties"]["next_topics"]["items"]["properties"]   # a *field* named title survives
    p = GeminiProvider("g", api_key="k", client=httpx.AsyncClient())
    body = p._body("x", GenerationParams(json_mode=True, json_schema=ss.provider_schema("key_concepts")), True)
    cfg = body["generationConfig"]
    assert cfg["responseMimeType"] == "application/json" and cfg["responseSchema"]["properties"]["key_concepts"]["minItems"] == 3
    assert "responseSchema" not in p._body("x", GenerationParams(json_mode=True), True)["generationConfig"]


async def test_call_model_passes_schema_only_for_json_sections(monkeypatch):
    got = {}

    class R:
        providers = []

        async def generate(self, prompt, params, req):
            from app.ai.types import LLMResponse
            got[prompt] = params.json_schema
            return LLMResponse("text", "p", "m")
    from app.core.config import get_settings
    monkeypatch.setattr(cg, "get_router", lambda: R())
    monkeypatch.setattr(get_settings(), "CONTENT_STREAMING", False, raising=False)
    await cg._call_model(SEC["flashcards"], "j")
    await cg._call_model(SEC["summary"], "m")
    assert got["j"]["properties"]["flashcards"]["minItems"] == 3 and got["m"] is None
    monkeypatch.setattr(get_settings(), "CONTENT_NATIVE_SCHEMA", False, raising=False)
    await cg._call_model(SEC["flashcards"], "j2")
    assert got["j2"] is None


# ---------------------------------------------------------------- reasoning models (found against a real magistral server)
async def test_ollama_asks_reasoning_models_not_to_think_and_drops_the_option_if_unsupported():
    seen = []

    def h(req):
        body = json.loads(req.content)
        seen.append(body.get("think"))
        if body.get("think") is False and len(seen) == 1:
            return httpx.Response(400, json={"error": '"m" does not support thinking'})
        return httpx.Response(200, json={"message": {"content": "ok"}, "done_reason": "stop"})
    p = ollama(h)
    await p.generate("x")
    await p.generate("x")
    assert seen == [False, None, None]                # tried once, retried without, remembered
    off = OllamaProvider("m", disable_thinking=False, client=httpx.AsyncClient(transport=httpx.MockTransport(h)))
    await off.generate("x")
    assert seen[-1] is None


async def test_think_rejection_does_not_hide_a_schema_rejection():
    seen = []

    def h(req):
        body = json.loads(req.content)
        seen.append((body.get("think"), body.get("format")))
        if isinstance(body.get("format"), dict):
            return httpx.Response(400, json={"error": "invalid format"})
        return httpx.Response(200, json={"message": {"content": "{}"}, "done_reason": "stop"})
    await ollama(h).generate("x", GenerationParams(json_mode=True, json_schema=ss.provider_schema("flashcards")))
    assert [f if isinstance(f, str) else "schema" for _, f in seen] == ["schema", "json"] and seen[0][0] is False


async def test_reasoning_tokens_keep_the_stream_alive_but_are_not_output():
    def h(req):
        lines = [{"message": {"content": "", "thinking": "hmm"}, "done": False}] * 3 + \
                [{"message": {"content": "Answer"}, "done": False}, {"message": {"content": ""}, "done": True, "done_reason": "stop"}]
        return httpx.Response(200, content=("\n".join(json.dumps(x) for x in lines) + "\n").encode())
    p = ollama(h)
    assert [t async for t in p.generate_stream("x")] == ["", "", "", "Answer"]
    from app.ai.model_router import ModelRouter, TaskRequirements
    from app.ai.resilience import RetryPolicy
    r = ModelRouter([p], RetryPolicy(max_attempts=1))
    out = [t async for t in r.generate_stream("x", GenerationParams(), TaskRequirements(prefer="configured", needs_streaming=True))]
    assert out == ["Answer"]                           # the router never forwards empty liveness chunks


async def test_long_thinking_is_not_mistaken_for_a_stall():
    import asyncio
    from app.ai.model_router import ModelRouter, TaskRequirements
    from app.ai.resilience import RetryPolicy
    from tests.unit.test_stream_watchdog import Streamer
    s = Streamer("ollama", ["", 0.04, "", 0.04, "", 0.04, "", "done"])    # silent for >first-token budget overall, but alive throughout
    r = ModelRouter([s], RetryPolicy(max_attempts=1))
    out = [t async for t in r.generate_stream("x", GenerationParams(first_token_timeout_s=0.08, idle_timeout_s=0.08),
                                              TaskRequirements(prefer="configured", needs_streaming=True))]
    assert out == ["done"]
