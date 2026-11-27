"""LangChain chat-model adapter over the project's ModelRouter.

Every AI feature (quiz, content, tutor) builds LCEL chains / LangGraph graphs against this one model class, so
provider order, retries, circuit breakers, failover, LLM_LOCAL_ONLY privacy and stream watchdogs all keep working
exactly as before: LangChain/LangGraph own *orchestration and prompting*, the router owns *which model is called*.
"""
from __future__ import annotations

import asyncio
import queue
import threading
from contextlib import aclosing
from typing import Any, AsyncIterator, Callable, Iterator, List, Optional

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import ConfigDict, Field

from app.ai.model_router import Complexity, ModelRouter, TaskRequirements
from app.ai.types import GenerationParams
from app.core.config import get_settings


def _split(messages: List[BaseMessage]) -> tuple[Optional[str], str]:
    """System messages -> provider `system`; everything else is flattened into one transcript prompt."""
    system = "\n\n".join(str(m.content) for m in messages if isinstance(m, SystemMessage)) or None
    rest = [m for m in messages if not isinstance(m, SystemMessage)]
    if len(rest) == 1:
        return system, str(rest[0].content)
    label = {"human": "Student", "ai": "LearnMate"}
    return system, "\n".join(f"{label.get(m.type, m.type.title())}: {m.content}" for m in rest)


class RouterChatModel(BaseChatModel):
    """`BaseChatModel` that delegates to a ModelRouter. Usage and provider/model are surfaced in response metadata."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    router_getter: Optional[Callable[[], ModelRouter]] = Field(default=None, exclude=True)
    sync_router_getter: Optional[Callable[[], ModelRouter]] = Field(default=None, exclude=True)  # tests / custom wiring
    temperature: float = 0.7
    max_output_tokens: int = 4096
    timeout_s: Optional[float] = None
    json_mode: bool = False
    json_schema: Optional[dict] = None
    native_history: bool = False  # send earlier turns as real chat messages instead of flattening them into one transcript
    prefer: str = "configured"
    complexity: Complexity = Complexity.MEDIUM
    max_attempts: Optional[int] = None
    first_token_timeout_s: Optional[float] = None
    idle_timeout_s: Optional[float] = None
    total_timeout_s: Optional[float] = None

    @property
    def _llm_type(self) -> str:
        return "model-router"

    def _router(self) -> ModelRouter:
        if self.router_getter:
            return self.router_getter()
        from app.ai.runtime import get_router  # lazy: importing this module never needs credentials
        return get_router()

    def _req(self, streaming: bool) -> TaskRequirements:
        return TaskRequirements(complexity=self.complexity, needs_structured=self.json_mode, needs_streaming=streaming,
                                local_only=get_settings().LLM_LOCAL_ONLY, prefer=self.prefer, max_attempts=self.max_attempts)

    def _params(self, messages: List[BaseMessage], **extra: Any) -> tuple[str, GenerationParams]:
        turns = [m for m in messages if not isinstance(m, SystemMessage)]
        if self.native_history and len(turns) > 1 and turns[-1].type == "human":
            system, prompt = _split([m for m in messages if isinstance(m, SystemMessage)] + turns[-1:])
            extra["history"] = tuple(("assistant" if m.type == "ai" else "user", str(m.content)) for m in turns[:-1])
        else:
            system, prompt = _split(messages)
        timeout = self.timeout_s if self.timeout_s is not None else get_settings().LLM_TIMEOUT_S
        return prompt, GenerationParams(
            temperature=self.temperature, max_output_tokens=self.max_output_tokens, timeout_s=timeout, system=system,
            json_mode=self.json_mode, json_schema=self.json_schema, first_token_timeout_s=self.first_token_timeout_s,
            idle_timeout_s=self.idle_timeout_s, total_timeout_s=self.total_timeout_s, **extra)

    async def _agenerate(self, messages, stop=None, run_manager: Optional[AsyncCallbackManagerForLLMRun] = None, router: Optional[ModelRouter] = None, **kw) -> ChatResult:
        prompt, params = self._params(messages)
        resp = await (router or self._router()).generate(prompt, params, self._req(False))
        msg = AIMessage(content=resp.text,
                        usage_metadata={"input_tokens": resp.usage.prompt_tokens, "output_tokens": resp.usage.completion_tokens,
                                        "total_tokens": resp.usage.total},
                        response_metadata={"provider": resp.provider, "model": resp.model, "finish_reason": resp.finish_reason,
                                           "latency_ms": resp.latency_ms, "truncated": resp.finish_reason == "length"})
        return ChatResult(generations=[ChatGeneration(message=msg)])

    async def _astream(self, messages, stop=None, run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
                       router: Optional[ModelRouter] = None, **kw) -> AsyncIterator[ChatGenerationChunk]:
        from app.ai.types import StreamEnd
        end: list[StreamEnd] = []
        prompt, params = self._params(messages, on_stream_end=end.append)
        async with aclosing((router or self._router()).generate_stream(prompt, params, self._req(True))) as stream:
            async for tok in stream:
                chunk = ChatGenerationChunk(message=AIMessageChunk(content=tok))
                if run_manager:
                    await run_manager.on_llm_new_token(tok, chunk=chunk)
                yield chunk
        e = end[-1] if end else StreamEnd()
        yield ChatGenerationChunk(message=AIMessageChunk(
            content="", chunk_position="last",
            usage_metadata={"input_tokens": e.prompt_tokens, "output_tokens": e.completion_tokens,
                            "total_tokens": e.prompt_tokens + e.completion_tokens},
            response_metadata={"provider": e.provider, "model": e.model, "finish_reason": e.finish_reason,
                               "truncated": e.finish_reason == "length"}))

    # Sync entry points (invoke/stream/batch from sync code, LangGraph sync nodes). The async path is the source of truth.
    # It runs on ONE persistent background loop with its own router: HTTP clients are bound to the loop that first used
    # them, so sharing the app's router (or a throw-away loop per call) would break the async path.
    def _generate(self, messages, stop=None, run_manager: Optional[CallbackManagerForLLMRun] = None, **kw) -> ChatResult:
        return _SYNC.run(self._agenerate(messages, stop, None, router=self._sync_router(), **kw))

    def _stream(self, messages, stop=None, run_manager: Optional[CallbackManagerForLLMRun] = None, **kw) -> Iterator[ChatGenerationChunk]:
        done, q = object(), queue.Queue()

        async def pump():
            try:
                async with aclosing(self._astream(messages, stop, None, router=self._sync_router(), **kw)) as it:
                    async for c in it:
                        q.put(c)
            except BaseException as e:  # surfaced to the consuming thread
                q.put(e)
            finally:
                q.put(done)

        fut = _SYNC.submit(pump())
        try:
            while (item := q.get()) is not done:
                if isinstance(item, BaseException):
                    raise item
                if run_manager:
                    run_manager.on_llm_new_token(str(item.message.content), chunk=item)
                yield item
        finally:
            fut.cancel()  # consumer stopped early: stop the upstream request

    def _sync_router(self) -> ModelRouter:
        if self.sync_router_getter:
            return self.sync_router_getter()
        return _SYNC.router()

    def with_options(self, **kw: Any) -> "RouterChatModel":
        return self.model_copy(update=kw)


class _SyncRunner:
    """A single daemon thread running an event loop for sync callers, plus the router bound to that loop."""

    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._router: Optional[ModelRouter] = None
        self._lock = threading.Lock()

    def _ensure(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                threading.Thread(target=loop.run_forever, name="langchain-sync-loop", daemon=True).start()
                self._loop = loop
            return self._loop

    def router(self) -> ModelRouter:
        with self._lock:
            if self._router is None:
                from app.ai.factory import build_router
                self._router = build_router(get_settings())
            return self._router

    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._ensure())

    def run(self, coro):
        if threading.current_thread().name == "langchain-sync-loop":
            raise RuntimeError("sync call from the sync loop itself would deadlock")
        return self.submit(coro).result()


_SYNC = _SyncRunner()


def get_chat_model(**kw: Any) -> RouterChatModel:
    return RouterChatModel(**kw)
