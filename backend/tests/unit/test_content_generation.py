import asyncio
import json

import httpx
import pytest

from services import content_generation as cg
from services.content_generation import SECTIONS, Section, build_prompt, generate_section, iter_sections, parse_section, tidy_markdown

MD = Section("deep_dive", "markdown", 100, "x")
GOOD = {
    "markdown": "## Heading\n\nA sufficiently long paragraph about the topic that is clearly complete.\n",
    "key_concepts": json.dumps({"key_concepts": [{"term": f"T{i}", "definition": f"D{i}"} for i in range(6)]}),
    "flashcards": json.dumps({"flashcards": [{"question": f"Q{i}", "answer": f"A{i}"} for i in range(8)]}),
    "recommendations": json.dumps({"weak_areas": ["a"], "next_topics": [{"title": "t", "reason": "r"}], "practice_suggestions": ["p"], "resources": []}),
    "text": "Hello learner, welcome to the topic!",
}


def good_output(sec):
    return GOOD[sec.name] if sec.kind == "json" else GOOD[sec.kind]


# ---------------- parsing ----------------
def test_tidy_markdown_strips_outer_fence_closes_code_and_drops_partial_line():
    assert tidy_markdown("```markdown\n## A\ntext.\n```", False) == "## A\ntext."
    assert tidy_markdown("## A\n```python\nx = 1", False).endswith("x = 1\n```")
    assert tidy_markdown("## A\nComplete sentence.\nHalf writt", True) == "## A\nComplete sentence."
    assert tidy_markdown("## A\nComplete sentence.\nHalf writt", False).endswith("Half writt")  # only trimmed when truncated


def test_parse_sections_validate_shape():
    assert parse_section(SECTIONS[1], GOOD["key_concepts"], False)[0] == {"term": "T0", "definition": "D0"}
    assert len(parse_section(SECTIONS[7], GOOD["flashcards"], False)) == 8
    assert parse_section(SECTIONS[8], GOOD["recommendations"], False)["weak_areas"] == ["a"]
    for sec, bad in [(SECTIONS[1], '{"key_concepts": [{"term": "x"}]}'), (SECTIONS[7], "not json"),
                     (SECTIONS[8], '{"weak_areas": []}'), (MD, "short")]:
        with pytest.raises(Exception):
            parse_section(sec, bad, False)


def test_every_section_fits_even_a_4096_token_context():
    """Regression guard for the original failure: prompt + max output must fit Ollama's common default context."""
    for sec in SECTIONS:
        prompt_tokens = len(build_prompt(sec, "Distributed systems consensus algorithms", "Intermediate", 6)) // 3  # pessimistic chars/token
        assert prompt_tokens + sec.max_tokens <= 4096, sec.name
    assert {s.name for s in SECTIONS} == set(cg._defaults("t", "Beginner", 1))


# ---------------- generation ----------------
async def test_section_retries_once_then_succeeds():
    calls = []

    async def call(sec, prompt):
        calls.append(1)
        return ("oops", False) if len(calls) == 1 else (good_output(sec), False)
    value, ok = await generate_section(SECTIONS[7], "Python", "Beginner", 3, call)
    assert ok and len(value) == 8 and len(calls) == 2


async def test_section_failure_falls_back_without_raising():
    async def call(sec, prompt):
        raise RuntimeError("provider down")
    value, ok = await generate_section(SECTIONS[0], "Python", "Beginner", 3, call)
    assert not ok and "could not be generated" in value


async def test_all_sections_generated_with_bounded_concurrency_and_prompt_markers():
    running, peak, prompts = 0, 0, {}

    async def call(sec, prompt):
        nonlocal running, peak
        running += 1; peak = max(peak, running); prompts[sec.name] = prompt
        await asyncio.sleep(0.01)
        running -= 1
        return good_output(sec), False
    got = {n: (v, ok) async for n, v, ok in iter_sections("Python", "Expert", 9, concurrency=3, call=call)}
    assert set(got) == set(cg.SECTION_NAMES) and all(ok for _, ok in got.values())
    assert peak <= 3
    assert "SECTION: deep_dive" in prompts["deep_dive"] and "Expert" in prompts["deep_dive"] and "9/10" in prompts["summary"]


async def test_only_regenerates_requested_sections():
    async def call(sec, prompt):
        return good_output(sec), False
    got = [n async for n, _, _ in iter_sections("Python", "Beginner", 2, only=["flashcards", "summary"], call=call)]
    assert sorted(got) == ["flashcards", "summary"]


async def test_closing_the_stream_cancels_in_flight_model_calls():
    started, cancelled = asyncio.Event(), []

    async def call(sec, prompt):
        if sec.name == "summary":
            return good_output(sec), False
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(sec.name)
            raise
    it = iter_sections("Python", "Beginner", 2, concurrency=3, call=call)
    first = await it.__anext__()
    await started.wait()
    await it.aclose()  # what Starlette does when the client disconnects
    assert first[0] == "summary" and len(cancelled) >= 1


# ---------------- provider plumbing ----------------
async def test_ollama_sends_num_ctx_and_reports_length_finish():
    seen = {}

    def h(req):
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"message": {"content": "partial"}, "done_reason": "length", "eval_count": 5})
    from app.ai.providers.ollama import OllamaProvider
    from app.ai.types import GenerationParams
    p = OllamaProvider("m", num_ctx=8192, client=httpx.AsyncClient(transport=httpx.MockTransport(h)))
    r = await p.generate("x", GenerationParams(max_output_tokens=50))
    assert seen["options"]["num_ctx"] == 8192 and seen["options"]["num_predict"] == 50 and r.finish_reason == "length"
    p2 = OllamaProvider("m", client=httpx.AsyncClient(transport=httpx.MockTransport(h)))
    await p2.generate("x"); assert "num_ctx" not in seen["options"]


async def test_router_max_attempts_override_prevents_stacked_long_retries():
    from app.ai.model_router import ModelRouter, TaskRequirements
    from app.ai.resilience import RetryPolicy
    from app.ai.types import ModelCapabilities, TokenUsage
    from app.core.exceptions import ProviderError

    class Slow:
        name, model, calls = "s", "m", 0
        def model_capabilities(self): return ModelCapabilities()
        def token_usage(self): return TokenUsage()
        async def generate(self, prompt, params=None):
            Slow.calls += 1; raise ProviderError("timed out")

    async def nosleep(d): pass
    r = ModelRouter([Slow()], RetryPolicy(max_attempts=3), sleep=nosleep)
    with pytest.raises(ProviderError):
        await r.generate("x", req=TaskRequirements(max_attempts=1))
    assert Slow.calls == 1
