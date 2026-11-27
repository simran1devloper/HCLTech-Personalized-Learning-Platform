"""AI Tutor orchestration and response streaming.

    question persisted -> classify -> context builder -> tool (read now / propose writes) -> prompt + memory -> model stream
    -> reply persisted (complete | cancelled | failed), with partial text kept

SSE events (the first four names predate this module and are kept for compatibility):
    meta      conversation and message ids        (message_start)
    token     {text}                               (content_delta)
    done      final status                         (message_complete)
    error     {code, message, partial}
    status    {stage}: processing | retrieving_context | executing_tool | generating
    intent    classification         context  {sources}
    tool_start / tool_result         cancelled        (message_cancelled)
Comment lines (": ping") keep the connection alive while the model is silent, which also lets the server notice a disconnect."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable

from app.agents.workflows.tutor_graph import stream_tutor_tokens
from app.ai.langchain_adapter import RouterChatModel
from app.ai.model_router import Complexity, ModelRouter
from app.core.config import AppSettings, get_settings
from app.core.container import Container
from app.core.exceptions import AppError, ConflictError
from app.domains.tutoring import learner_context, prompts, tools
from app.domains.tutoring.context import build_context
from app.domains.tutoring.intent import Classification, SkillRef, classify
from app.domains.tutoring.memory import build_memory, tokens
from app.observability.metrics import metrics

log = logging.getLogger("app.tutor")

HEARTBEAT_S = 5.0
POLL_S = 0.2
CHECKPOINT_S = 2.0
STALE_CLAIM_S = 60.0


# ------------------------------------------------------------------ one active generation per conversation (process-local, like the rate limiter)
@dataclass
class Generation:
    conv_id: str
    cancel: threading.Event = field(default_factory=threading.Event)
    message_id: int | None = None
    started: bool = False
    claimed_at: float = field(default_factory=time.monotonic)


class Registry:
    def __init__(self):
        self._lock = threading.Lock()
        self._by_conv: dict[str, Generation] = {}

    def claim(self, conv_id: str) -> Generation:
        with self._lock:
            g = self._by_conv.get(conv_id)
            if g and (g.started or time.monotonic() - g.claimed_at < STALE_CLAIM_S):
                raise ConflictError("A reply is already being generated in this conversation. Stop it or wait for it to finish.")
            g = self._by_conv[conv_id] = Generation(conv_id)
            return g

    def release(self, g: Generation) -> None:
        with self._lock:
            if self._by_conv.get(g.conv_id) is g:
                del self._by_conv[g.conv_id]

    def active(self, conv_id: str) -> Generation | None:
        return self._by_conv.get(conv_id)


generations = Registry()


def reconcile(c: Container, conv_id: str, messages: list[dict]) -> list[dict]:
    """A reply left in `streaming` with no running generation was cut off (e.g. a server restart): say so and keep what arrived."""
    live = generations.active(conv_id)
    for m in messages:
        if m["status"] == "streaming" and not (live and live.message_id == m["message_id"]):
            m["status"] = "interrupted"
            c.tutor.set_message(m["message_id"], status="interrupted")
    return messages


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@dataclass
class Options:
    skill: str | None = None
    language: str = "en"
    mode: str = "auto"
    level: str = "auto"
    career_goal: str | None = None


def context_window(s: AppSettings) -> int:
    if s.TUTOR_CONTEXT_WINDOW_TOKENS > 0:
        return s.TUTOR_CONTEXT_WINDOW_TOKENS
    uses_ollama = "ollama" in [p.strip().lower() for p in s.LLM_PROVIDERS.split(",")]
    return (s.OLLAMA_NUM_CTX or 4096) if uses_ollama else 16000


def friendly_error(e: AppError) -> str:
    return {
        "ai_provider_rate_limited": "The AI service is busy right now. Please retry in a few seconds.",
        "ai_provider_stalled": "The AI model did not respond in time. Local models can be slow or busy with another task; please retry.",
        "ai_provider_account_error": "The AI provider rejected the server's API key or quota. An administrator needs to check the provider settings.",
        "circuit_open": "The AI service failed repeatedly and is paused for a short moment. Please retry in about 30 seconds.",
    }.get(e.code, "The AI service is unavailable. Please retry.")


def _previous_classification(history: list[dict]) -> Classification | None:
    for m in reversed(history):
        raw = ((m.get("meta") or {}).get("classification"))
        if m["role"] == "assistant" and raw:
            try:
                return Classification.model_validate(raw)
            except ValueError:
                return None
    return None


async def run_turn(c: Container, user: str, generation: Generation, question: dict, opts: Options, router_getter: Callable[[], ModelRouter]) -> AsyncIterator[str]:
    """Answer one stored question. Owns the assistant message from creation to its final status."""
    st = get_settings()
    conv_id = generation.conv_id
    t0 = time.perf_counter()
    reply_id: int | None = None
    parts: list[str] = []
    cls: Classification | None = None
    finished = False
    meta: dict = {}
    details: dict = {}
    first_token_ms: float | None = None
    generation.started = True
    try:
        history = [m for m in c.tutor.thread(user, conv_id) if m["message_id"] < question["message_id"]]
        reply_id = c.store.add_message(user, conv_id, "assistant", "", status="streaming")
        generation.message_id = reply_id
        yield sse("meta", {"conversation_id": conv_id, "message_id": reply_id, "user_message_id": question["message_id"]})
        yield sse("status", {"stage": "processing"})

        # ---- intent
        refs = learner_context.skill_refs(c)
        focus: SkillRef | None = None
        if opts.skill:
            sk = c.registry.repo.get(opts.skill) or c.registry.resolve(opts.skill)
            focus = next((r for r in refs if sk and r.skill_id == sk.skill_id), None)
        message = question["content"]
        cls = classify(message, refs, mode=opts.mode, level=opts.level, focus=focus, previous=_previous_classification(history))
        if opts.skill and not focus and not cls.topic:
            cls.topic = opts.skill[:80]
        action = tools.plan(c, user, cls, message, career=opts.career_goal)
        pasted = len(message) > 600
        if cls.intent == "document_qa" and not pasted:
            action = tools.Plan(reply="I can't read uploaded files yet: document upload is not available in LearnAI. "
                                      "Paste the text here (up to about 8,000 characters) and ask your question about it.")
        cls.requires_tool, cls.tool_name = action.tool is not None, action.tool
        details["classification"] = cls.model_dump()
        yield sse("intent", cls.model_dump())

        # ---- learner context (only what this intent needs)
        ctx = learner_context.LearnerContext()
        if focus and cls.intent != "general_knowledge":
            cls.requires_context = True
        if cls.requires_context and not action.reply and not action.confirm_text:
            yield sse("status", {"stage": "retrieving_context"})
            try:
                ctx = learner_context.gather(c, user, cls, career=opts.career_goal, focus_skill_id=focus.skill_id if focus else None)
            except AppError:
                raise
            except Exception:  # noqa: BLE001  a failing module must not stop an ordinary answer
                log.exception("learner context retrieval failed")
                metrics.inc("tutor_context_errors_total")
                details["context_unavailable"] = True
            details["sources"] = ctx.sources
            if ctx.sources or details.get("context_unavailable"):
                yield sse("context", {"sources": ctx.sources, "unavailable": bool(details.get("context_unavailable"))})

        # ---- tools
        tool_result: dict | None = None
        if action.tool:
            if action.confirm_text:
                ex = c.tutor.add_execution(user, reply_id, action.tool, action.args or {}, "pending")
                yield sse("tool_result", ex)
            else:
                yield sse("status", {"stage": "executing_tool"})
                yield sse("tool_start", {"tool": action.tool})
                status, tool_result = await tools.run_tool(c, user, action.tool, action.args or {}, key=f"tutor-{reply_id}")
                ex = c.tutor.add_execution(user, reply_id, action.tool, action.args or {}, status, tool_result)
                yield sse("tool_result", ex)
                if status == "failed":
                    action.reply = f"I couldn't read that from LearnAI: {tool_result['message']}"
                    tool_result = None

        # ---- answers that need no model: confirmations, missing input, and "there is no data" (never let a model invent it)
        fixed = action.confirm_text or action.reply
        if not fixed and tool_result and tool_result.get("empty"):
            fixed = tool_result["empty"]
        if fixed:
            parts.append(fixed)
            yield sse("token", {"text": fixed})
            meta = {"provider": "learnai", "model": "rules", "input_tokens": 0, "output_tokens": 0}
        else:
            yield sse("status", {"stage": "generating"})
            window = context_window(st)
            max_out = min(prompts.max_output_tokens(cls, st.TUTOR_MAX_OUTPUT_TOKENS), max(256, window // 2))
            system = prompts.system_prompt(cls, opts.language)
            items = ctx.items
            if tool_result and tool_result.get("data"):
                items = [*items, learner_context.ContextItem(owner_id=user, kind="tool", priority=1.0, untrusted=True,
                                                             text=f"{tool_result['title']}: {json.dumps(tool_result['data'], default=str)[:1400]}")]
            learner_block = ""
            if items:
                built = await build_context(user, message, items, budget_tokens=min(700, window // 5))
                learner_block = built["context_text"]
            budget = window - max_out - tokens(system) - tokens(learner_block) - tokens(message) - 120
            recent, summary = build_memory(history, max(0, budget))
            complexity = (Complexity.HIGH if cls.intent in prompts.HIGH_COMPLEXITY
                          else Complexity.LOW if cls.intent in prompts.LOW_COMPLEXITY else Complexity.MEDIUM)
            model = RouterChatModel(router_getter=router_getter, native_history=True, temperature=0.4 if cls.intent in ("code_generation", "debugging") else 0.6,
                                    max_output_tokens=max_out, complexity=complexity, prefer=st.TUTOR_ROUTING,
                                    timeout_s=max(st.TUTOR_FIRST_TOKEN_S, st.TUTOR_IDLE_TOKEN_S) + 30, first_token_timeout_s=st.TUTOR_FIRST_TOKEN_S,
                                    idle_timeout_s=st.TUTOR_IDLE_TOKEN_S, total_timeout_s=st.LLM_STREAM_TOTAL_S)
            state = {"message": message, "language": opts.language, "context": learner_block, "summary": summary, "recent": recent, "system": system}

            queue: asyncio.Queue = asyncio.Queue()

            async def pump():
                try:
                    async with aclosing(stream_tutor_tokens(model, state, on_end=meta.update)) as stream:
                        async for tok in stream:
                            await queue.put(("token", tok))
                    await queue.put(("end", None))
                except Exception as e:  # noqa: BLE001  re-raised by the consumer
                    await queue.put(("error", e))

            producer = asyncio.create_task(pump())
            last_beat = last_save = time.monotonic()
            try:
                while True:
                    if generation.cancel.is_set():
                        break
                    try:
                        kind, value = await asyncio.wait_for(queue.get(), POLL_S)
                    except asyncio.TimeoutError:
                        if time.monotonic() - last_beat >= HEARTBEAT_S:
                            last_beat = time.monotonic()
                            yield ": ping\n\n"
                        continue
                    if kind == "error":
                        raise value
                    if kind == "end":
                        break
                    if first_token_ms is None:
                        first_token_ms = (time.perf_counter() - t0) * 1000
                        metrics.observe("tutor_first_token_ms", first_token_ms)
                    parts.append(value)
                    last_beat = time.monotonic()
                    if last_beat - last_save >= CHECKPOINT_S:
                        last_save = last_beat
                        c.tutor.set_message(reply_id, content="".join(parts))
                    yield sse("token", {"text": value})
            finally:
                if not producer.done():   # stop, disconnect or failure: close the upstream request
                    producer.cancel()
                    try:
                        await producer
                    except asyncio.CancelledError:
                        pass
            if not meta.get("input_tokens") and not meta.get("output_tokens"):
                sent = system + learner_block + summary + message + "".join(m["content"] for m in recent)
                meta.update(input_tokens=tokens(sent), output_tokens=tokens("".join(parts)) if parts else 0)
                details["tokens_estimated"] = True

        text = "".join(parts)
        cancelled = generation.cancel.is_set()
        if not cancelled and not text.strip():
            raise AppError("empty response")
        status = "cancelled" if cancelled else "complete"
        details["followups"] = [] if cancelled or fixed else prompts.followups(cls)
        details["truncated"] = meta.get("finish_reason") == "length"
        c.tutor.set_message(reply_id, content=text, status=status)
        finished = True
        metrics.inc("tutor_replies_total", status=status, intent=cls.intent)
        yield sse("cancelled" if cancelled else "done", {"conversation_id": conv_id, "message_id": reply_id, "status": status,
                                                         "followups": details["followups"], "truncated": details["truncated"],
                                                         "model": meta.get("model"), "provider": meta.get("provider")})
    except AppError as e:
        finished = True
        code = e.code
        metrics.inc("tutor_stream_errors_total", code=code)
        log.warning("tutor reply failed: %s (%s)", code, e.message)
        details["error_code"] = code
        if reply_id is not None:
            c.tutor.set_message(reply_id, content="".join(parts), status="failed")
        yield sse("error", {"code": code, "message": friendly_error(e), "partial": bool(parts), "message_id": reply_id})
    except Exception as e:  # noqa: BLE001  never leak internals into the stream
        finished = True
        unconfigured = isinstance(e, RuntimeError) and "no LLM provider" in str(e)
        code = "ai_not_configured" if unconfigured else "internal_error"
        metrics.inc("tutor_stream_errors_total", code=code)
        log.exception("tutor reply failed")
        details["error_code"] = code
        if reply_id is not None:
            c.tutor.set_message(reply_id, content="".join(parts), status="failed")
        yield sse("error", {"code": code, "message": ("No AI provider is configured on the server." if unconfigured else "Something went wrong. Please retry."),
                            "partial": bool(parts), "message_id": reply_id})
    finally:
        try:
            if reply_id is not None:
                if not finished:   # the client went away: keep the partial answer
                    c.tutor.set_message(reply_id, content="".join(parts), status="cancelled")
                    metrics.inc("tutor_replies_total", status="disconnected", intent=cls.intent if cls else "unknown")
                latency = (time.perf_counter() - t0) * 1000
                metrics.observe("tutor_completion_ms", latency)
                c.tutor.save_meta(reply_id, provider=meta.get("provider") or None, model=meta.get("model") or None, intent=cls.intent if cls else None,
                                  prompt_version=prompts.PROMPT_VERSION, input_tokens=meta.get("input_tokens", 0), output_tokens=meta.get("output_tokens", 0),
                                  tokens_estimated=bool(details.pop("tokens_estimated", False)), first_token_ms=first_token_ms, latency_ms=latency,
                                  error_code=details.pop("error_code", None), details=details)
        finally:
            generations.release(generation)
