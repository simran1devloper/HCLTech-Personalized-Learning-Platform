"""LangChain adapter + LangGraph tutor graph run on the ModelRouter contract."""
from app.agents.workflows.tutor_graph import stream_tutor_tokens
from app.ai.langchain_adapter import RouterChatModel
from app.ai.types import LLMResponse, StreamEnd, TokenUsage


class FakeRouter:
    def __init__(self):
        self.calls = []

    async def generate(self, prompt, params=None, req=None):
        self.calls.append((prompt, params, req))
        return LLMResponse("hi", "fake", "m", TokenUsage(3, 2), 5.0, "stop")

    async def generate_stream(self, prompt, params=None, req=None):
        self.calls.append((prompt, params, req))
        for t in ("a", "b"):
            yield t
        params.on_stream_end(StreamEnd("fake", "m", "stop", 4, 2))


async def test_adapter_invoke_carries_system_usage_and_metadata():
    r = FakeRouter()
    m = RouterChatModel(router_getter=lambda: r, json_mode=True)
    out = await m.ainvoke([("system", "SYS"), ("human", "q")])
    assert out.content == "hi" and out.usage_metadata["total_tokens"] == 5 and out.response_metadata["provider"] == "fake"
    prompt, params, req = r.calls[0]
    assert prompt == "q" and params.system == "SYS" and params.json_mode and req.needs_structured


async def test_tutor_graph_streams_tokens_with_context_and_language():
    r = FakeRouter()
    m = RouterChatModel(router_getter=lambda: r, sync_router_getter=lambda: r)
    toks = [t async for t in stream_tutor_tokens(m, {"message": "why?", "language": "hinglish", "context": "Python: 2/5",
                                                     "summary": "", "recent": [{"role": "user", "content": "earlier"}]})]
    assert "".join(toks) == "ab"
    prompt, params, _ = r.calls[0]
    assert "Hinglish" in params.system and "Python: 2/5" in params.system and "earlier" in prompt and prompt.endswith("why?")


def test_adapter_sync_invoke_and_stream():
    r = FakeRouter()
    m = RouterChatModel(router_getter=lambda: r, sync_router_getter=lambda: r)
    assert m.invoke("q").content == "hi"
    assert "".join(c.content for c in m.stream("q")) == "ab"


async def test_sync_invoke_inside_running_loop_does_not_deadlock():
    m = RouterChatModel(router_getter=lambda: FakeRouter(), sync_router_getter=lambda: FakeRouter())
    assert m.invoke("q").content == "hi"  # called from async code: runs on its own thread/loop
