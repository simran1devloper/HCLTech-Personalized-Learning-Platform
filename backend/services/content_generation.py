"""
services/content_generation.py — study material generated section by section.

Why sections instead of one request:
  * One JSON object with ~10 long sections needs far more tokens than a typical model context (Ollama often
    defaults to 4096), so it was truncated, failed to parse and was regenerated in full — up to 3 times.
  * Each section here is a small, focused request. Markdown sections are returned as plain markdown (nothing to
    break), list sections as small JSON. A failed section is retried once on its own and never forces the rest
    to be regenerated.
  * Sections are yielded as they finish so callers can stream progress, and cancelling the consumer cancels the
    in-flight model requests (nothing is left running on the model server).
"""
from __future__ import annotations

import ast
import asyncio
import contextvars
import json
from contextlib import aclosing
import logging
import re
import time
from dataclasses import dataclass, replace
from typing import Any, AsyncIterator, Dict, List, Optional, TypedDict

from langchain_core.messages import AIMessageChunk
from langgraph.graph import END, START, StateGraph

from app.ai.langchain_adapter import RouterChatModel
from app.ai.limits import generation_slot

from app.ai.model_router import Complexity, TaskRequirements
from app.ai.runtime import get_router
from app.core.exceptions import ProviderError, ProviderRateLimitError, ProviderStallError
from app.core.config import get_settings
from app.observability.metrics import metrics
from services import content_budget as cb
from services import personalization as pz
from services import prompt_registry as pr
from app.domains.content_engine.study_schemas import (SchemaViolation, coerce_text, normalise_recommendations,  # noqa: F401
                                                      validate_section)
from app.domains.content_engine.streaming import ArrayItemScanner, MarkdownChunker
from app.domains.content_engine.study_schemas import ITEM_MODELS, validate_item
from app.domains.content_engine.validators.mermaid import process_markdown
from utils.helpers import extract_json_from_text

log = logging.getLogger("content_generation")



@dataclass(frozen=True)
class Section:
    name: str
    kind: str  # "markdown" | "json" | "text"
    max_tokens: int
    instructions: str


# Section definitions (instructions, budgets, versions) live in services/prompt_registry.py.
SECTIONS: List[Section] = [Section(p.section, p.kind, p.max_tokens, p.instructions) for p in pr.SECTION_PROMPTS.values()]
SECTION_NAMES = [s.name for s in SECTIONS]
SECTION_SCHEMAS = frozenset(n for n in SECTION_NAMES if pr.section_schema(n))


def _defaults(topic: str, level: str, score: int) -> Dict[str, Any]:
    return {
        "summary": f"## {topic}\n\nThis overview could not be generated. Please regenerate this section.",
        "key_concepts": [],
        "deep_dive": f"## Deep dive: {topic}\n\nThis section could not be generated. Please regenerate it.",
        "real_world_applications": "This section could not be generated. Please regenerate it.",
        "viva_voice": "This section could not be generated. Please regenerate it.",
        "practical_guide": "This section could not be generated. Please regenerate it.",
        "learning_roadmap": "This section could not be generated. Please regenerate it.",
        "flashcards": [],
        "recommendations": {"weak_areas": [], "next_topics": [], "practice_suggestions": [], "resources": []},
        "agent_intro": f"Hi! I'm your tutor for {topic}. You scored {score}/10 ({level}). Ask me anything to get started.",
    }


def build_prompt(sec: Section, topic: str, level: str, score: int, profile: Optional[dict] = None) -> str:
    """Stable prefix (shared by all requests for this section) followed by the request-specific tail."""
    if sec.name in pr.SECTION_PROMPTS:
        prompt = pr.build_section_prompt(sec.name, topic, level, score) + pz.prompt_line(profile, sec.name)
        if sec.kind == "markdown" and sec.max_tokens < pr.SECTION_PROMPTS[sec.name].max_tokens:  # shortened to fit the time budget
            prompt += f"\nLength: about {int(sec.max_tokens * 0.6)} words at most. Be concise and keep every part, just shorter."
        return prompt
    return f"{pr.PREFIX}\nSECTION: {sec.name}\n{sec.instructions}\n---\nTopic: {topic}\nLearner level: {level} ({score}/10)"


# ---------------------------------------------------------------------------------------------- parsing
_OUTER_FENCE = re.compile(r"^\s*```(?:markdown|md)?\s*\n(.*?)\n?```\s*$", re.S | re.I)


# ---------------------------------------------------------------------------------------------- normalisation
_scalar_text = coerce_text  # moved to the content engine schemas (kept here for existing imports)


def _as_container(text: str) -> Any:
    """If a model returned a JSON object/array or a Python-style dict/list repr as TEXT, return the parsed value."""
    t = text.strip()
    if not t or t[0] not in "{[" or t[-1] not in "}]":
        return None
    try:
        return json.loads(t)
    except ValueError:
        pass
    try:
        v = ast.literal_eval(t)                      # safe: literals only (handles {'a': 1, 'b': None})
        return v if isinstance(v, (dict, list)) else None
    except (ValueError, SyntaxError):
        return None


def container_to_markdown(v: Any, depth: int = 0) -> str:
    if isinstance(v, dict):
        lines = []
        for k, x in v.items():
            label = str(k).replace("_", " ").strip().capitalize()
            if isinstance(x, (dict, list)) and x:
                lines.append(f"{'#' * min(6, depth + 3)} {label}\n\n{container_to_markdown(x, depth + 1)}")
            else:
                lines.append(f"**{label}:** {_scalar_text(x)}")
        return "\n\n".join(lines)
    if isinstance(v, list):
        return "\n".join(f"- {container_to_markdown(x, depth + 1) if isinstance(x, (dict, list)) else _scalar_text(x)}" for x in v)
    return _scalar_text(v)


def tidy_markdown(text: str, truncated: bool) -> str:
    t = text.strip()
    parsed = _as_container(t)
    if parsed is not None:                             # the model returned data instead of prose: render it as readable Markdown
        t = container_to_markdown(parsed)
    m = _OUTER_FENCE.match(t)
    if m:  # model wrapped the whole answer in ```markdown
        t = m.group(1).strip()
    if truncated:
        lines = t.split("\n")
        if len(lines) > 1 and not re.search(r"[.!?:)`*|>\]]\s*$", lines[-1]):
            lines = lines[:-1]  # drop the half-written last line
        t = "\n".join(lines).rstrip()
    if t.count("```") % 2:  # close a code block left open by truncation
        t += "\n```"
    return t


def parse_section(sec: Section, raw: str, truncated: bool, final: bool = True) -> Any:
    """Returns the section value or raises ValueError if unusable. With final=False an invalid Mermaid diagram raises
    SchemaViolation (so the caller can ask for a repair); with final=True it is removed and the section is kept."""
    if sec.kind == "markdown":
        t = tidy_markdown(raw, truncated)
        if len(t) < 40:
            raise ValueError("section too short")
        t, diagram_errors = process_markdown(t, final=final)
        if diagram_errors:
            raise SchemaViolation(diagram_errors)
        if len(t) < 40:
            raise ValueError("section too short")
        return t
    if sec.kind == "text":
        t = raw.strip().strip('"').strip()
        if len(t) < 10:
            raise ValueError("text too short")
        return t
    data = extract_json_from_text(raw)
    if data is None:
        raise SchemaViolation(["the output is not valid JSON"
                               + (" (it was cut off: keep every item shorter)" if truncated else " (return a single JSON object, nothing else)")])
    if sec.name not in SECTION_SCHEMAS:
        raise ValueError(f"unknown json section {sec.name}")
    return validate_section(sec.name, data)  # raises SchemaViolation with repairable messages


# ---------------------------------------------------------------------------------------------- generation
class SectionState(TypedDict, total=False):
    """LangGraph state of one section's generate -> validate -> repair loop."""
    attempt: int
    prompt: str
    last: str          # human-readable reason of the last failed attempt
    result: Any        # CallResult of the latest model call (None if the call itself failed)
    value: Any
    ok: bool


@dataclass(frozen=True)
class CallResult:
    """One model call as seen by the section generator, including provider-reported usage."""
    text: str
    truncated: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0
    model: str = ""
    provider: str = ""


def _as_result(r: Any) -> CallResult:
    """Accepts a CallResult or the legacy (text, truncated) tuple used by simple fakes."""
    if isinstance(r, CallResult):
        return r
    text, truncated = r
    return CallResult(text, bool(truncated))


def _native_schema(sec: Section) -> Optional[dict]:
    """Tier 1 (constrained decoding): the section's JSON Schema, unless disabled with CONTENT_NATIVE_SCHEMA=false."""
    return pr.section_schema(sec.name) if sec.kind == "json" and get_settings().CONTENT_NATIVE_SCHEMA else None


async def _call_model(sec: Section, prompt: str) -> CallResult:
    async with generation_slot():  # shared cap; waiting happens before any provider timeout starts
        return await _call_model_now(sec, prompt)


async def _call_model_now(sec: Section, prompt: str) -> CallResult:
    s = get_settings()
    req = TaskRequirements(complexity=Complexity.MEDIUM, needs_structured=sec.kind == "json", local_only=s.LLM_LOCAL_ONLY,
                           prefer="configured", max_attempts=1)  # retries are handled per section below
    if s.CONTENT_STREAMING:
        return await _call_model_stream(sec, prompt, req)
    msg = await _section_model(sec, s.LLM_TIMEOUT_S, req).ainvoke(prompt)
    md, um = msg.response_metadata, msg.usage_metadata or {}
    return CallResult(str(msg.content), bool(md.get("truncated")), um.get("input_tokens", 0), um.get("output_tokens", 0),
                      float(md.get("latency_ms", 0.0)), md.get("model", ""), md.get("provider", ""))


def _section_model(sec: Section, timeout_s: float, req: TaskRequirements, **kw) -> RouterChatModel:
    """LangChain chat model for one section (router resolved lazily so `get_router` stays patchable/overridable)."""
    return RouterChatModel(router_getter=lambda: get_router(), temperature=0.6 if sec.kind == "json" else 0.7,
                           max_output_tokens=sec.max_tokens, timeout_s=timeout_s, json_mode=sec.kind == "json",
                           json_schema=_native_schema(sec), complexity=req.complexity, max_attempts=req.max_attempts, **kw)


# Receives every streamed token of the section currently being generated in this task (set by generate_section).
_token_sink: contextvars.ContextVar = contextvars.ContextVar("content_token_sink", default=None)


def _clock() -> float:
    return time.monotonic()  # indirection so tests can control the progress throttle


class SectionStreamer:
    """Live preview of one section attempt: `chunk` events (markdown blocks / list items) and a progress line on its stage."""

    def __init__(self, sec: Section, tracker, sid: str, label: str, attempt: int, attempts: int):
        self.sec, self.tracker, self.sid, self.label, self.attempt, self.attempts = sec, tracker, sid, label, attempt, attempts
        self.seq = 0
        self.items = 0
        self._text: List[str] = []
        self._chunker = MarkdownChunker() if sec.kind in ("markdown", "text") else None
        self._scanner = ArrayItemScanner() if sec.name in ITEM_MODELS else None
        self._last_report = 0.0
        self._first = True

    def _chunk(self, kind: str, **kw) -> None:
        self.tracker.chunk(self.sec.name, attempt=self.attempt, seq=self.seq, kind=kind, **kw)
        self.seq += 1

    def feed(self, tok: str) -> None:
        self._text.append(tok)
        if self._chunker:
            if (delta := self._chunker.feed(tok)):
                self._chunk("md", delta=delta)
        if self._scanner:
            for obj in self._scanner.feed(tok):
                item = validate_item(self.sec.name, obj)
                if item is not None and self.items < 10:
                    self.items += 1
                    self._chunk("item", item=item)
        now = _clock()
        if self._first or now - self._last_report >= 1.0:
            words = len("".join(self._text).split())
            detail = "The model started answering…" if self._first else f"Writing… {words} words so far"
            self._first, self._last_report = False, now
            self.tracker.emit(self.sid, self.label, "retrying" if self.attempt > 1 else "running",
                              attempt=self.attempt, max_attempts=self.attempts, detail=detail)


async def _call_model_stream(sec: Section, prompt: str, req: TaskRequirements) -> CallResult:
    """Stream the section and judge health by *silence*, not elapsed time: a slow model that keeps producing tokens
    is left alone, a silent one is cut off after LLM_FIRST_TOKEN_S / LLM_IDLE_TOKEN_S (the router then fails over to the
    next provider if nothing was produced yet). The upstream request is closed on stall or cancellation."""
    s = get_settings()
    model = _section_model(sec, max(s.LLM_FIRST_TOKEN_S, s.LLM_IDLE_TOKEN_S), req, first_token_timeout_s=s.LLM_FIRST_TOKEN_S,
                           idle_timeout_s=s.LLM_IDLE_TOKEN_S, total_timeout_s=s.LLM_STREAM_TOTAL_S)
    t0 = time.perf_counter()
    parts: List[str] = []
    final: Optional[AIMessageChunk] = None
    async with aclosing(model.astream(prompt)) as stream:  # closing it closes the upstream request
        async for chunk in stream:
            if chunk.content:
                tok = str(chunk.content)
                parts.append(tok)
                sink = _token_sink.get()
                if sink:
                    try:
                        sink(tok)
                    except Exception:  # live preview must never break generation
                        log.exception("token sink failed")
            if chunk.chunk_position == "last":
                final = chunk
    text = "".join(parts)
    if not text.strip():
        raise ProviderError("the model returned an empty response")
    md, um = (final.response_metadata, final.usage_metadata or {}) if final else ({}, {})
    return CallResult(text, bool(md.get("truncated")), um.get("input_tokens", 0), um.get("output_tokens", 0),
                      (time.perf_counter() - t0) * 1000, md.get("model", ""), md.get("provider", ""))


LABELS = {
    "summary": "Write summary", "key_concepts": "Extract key concepts", "deep_dive": "Write technical deep dive",
    "real_world_applications": "Collect real-world applications", "viva_voice": "Prepare interview questions",
    "practical_guide": "Write practical implementation guide", "learning_roadmap": "Plan learning roadmap", "flashcards": "Create flashcards",
    "recommendations": "Build recommendations", "agent_intro": "Write tutor introduction",
}


async def _call_with_preview(call, sec: Section, prompt: str, streamer: Optional[SectionStreamer]):
    """Run the model call with the section's live preview attached (tokens reach `streamer` while it streams)."""
    if streamer is None:
        return await call(sec, prompt)
    token = _token_sink.set(streamer.feed)
    try:
        return await call(sec, prompt)
    finally:
        _token_sink.reset(token)


async def generate_section(sec: Section, topic: str, level: str, score: int,
                           call=None, attempts: int = 2, tracker=None, stats: Optional[dict] = None,
                           profile: Optional[dict] = None) -> tuple[Any, bool]:
    """Returns (value, ok). Never raises for model/parse failures; cancellation propagates.
    `tracker` (services.pipeline.Tracker) receives real stage events: running -> retrying -> done|failed."""
    call = call or _call_model
    sec = pz.scale_section(sec, profile)   # weak-area sections get a larger budget, expert overviews a smaller one
    base_prompt = build_prompt(sec, topic, level, score, profile)
    sid, label = f"section:{sec.name}", LABELS[sec.name]

    # LangGraph: call_model -> parse -> (ok: END | invalid & attempts left: call_model with a repair prompt | exhausted: END)
    async def call_model(st: SectionState) -> SectionState:
        attempt = st["attempt"] + 1
        if tracker:
            tracker.emit(sid, label, "retrying" if attempt > 1 else "running", attempt=attempt, max_attempts=attempts,
                         detail=f"Retrying after: {st['last']}" if attempt > 1 else "Waiting for the model…")
        streamer = SectionStreamer(sec, tracker, sid, label, attempt, attempts) if tracker else None
        try:
            res = _as_result(await _call_with_preview(call, sec, st["prompt"], streamer))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # provider error: retry the original prompt (it says nothing about earlier output)
            if isinstance(e, ProviderError):
                metrics.inc("content_section_calls_total", section=sec.name, outcome="provider_error")
            log.warning("section %s attempt %d failed: %s", sec.name, attempt, type(e).__name__)
            return {"attempt": attempt, "result": None, "prompt": base_prompt, "last": _failure_reason(e)}
        _record_usage(sec.name, res, "ok")
        return {"attempt": attempt, "result": res}

    async def parse(st: SectionState) -> SectionState:
        res, attempt = st["result"], st["attempt"]
        try:
            value = parse_section(sec, res.text, res.truncated, final=attempt == attempts)
        except SchemaViolation as v:
            # Repair retry: show the model exactly what was wrong instead of re-rolling the same prompt
            for err in v.errors[:3]:
                metrics.inc("content_schema_violation_total", section=sec.name, field=err.split(":", 1)[0][:40])
            if attempt < attempts:
                metrics.inc("content_repair_total", section=sec.name)
            log.warning("section %s attempt %d failed: %s", sec.name, attempt, type(v).__name__)
            return {"prompt": pr.build_repair_prompt(base_prompt, res.text, v.errors, sec.kind), "last": _failure_reason(v)}
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("section %s attempt %d failed: %s", sec.name, attempt, type(e).__name__)
            return {"prompt": base_prompt, "last": _failure_reason(e)}
        if stats is not None:  # lets the scheduler measure real speed for the time budget
            stats["tokens_out"] = res.tokens_out or (len(res.text) // 4)
            stats["seconds"] = res.latency_ms / 1000.0
        if tracker:
            tracker.emit(sid, label, "done", attempt=attempt, detail=_describe(sec, value, res.truncated),
                         tokens_in=res.tokens_in or None, tokens_out=res.tokens_out or None)
        return {"value": value, "ok": True}

    def after_call(st: SectionState) -> str:
        return "parse" if st.get("result") is not None else after_parse(st)

    def after_parse(st: SectionState) -> str:
        return END if st.get("ok") or st["attempt"] >= attempts else "call_model"

    g = StateGraph(SectionState)
    g.add_node("call_model", call_model)
    g.add_node("parse", parse)
    g.add_edge(START, "call_model")
    g.add_conditional_edges("call_model", after_call, {"parse": "parse", "call_model": "call_model", END: END})
    g.add_conditional_edges("parse", after_parse, {"call_model": "call_model", END: END})
    final = await g.compile().ainvoke({"attempt": 0, "prompt": base_prompt, "last": "", "ok": False, "result": None},
                                      config={"recursion_limit": 4 * attempts + 4})
    if final.get("ok"):
        return final["value"], True
    if tracker:
        tracker.emit(sid, label, "failed", attempt=attempts, max_attempts=attempts,
                     detail=f"Gave up: {final['last']}. Can be regenerated.")
    return _defaults(topic, level, score)[sec.name], False


def _failure_reason(e: Exception) -> str:
    return ("the AI service is limiting how fast it accepts requests" if isinstance(e, ProviderRateLimitError)
            else "the model was too slow to answer (busy, overloaded or low on memory)" if isinstance(e, ProviderStallError)
            else "the AI service did not respond" if isinstance(e, ProviderError)
            else f"output was invalid ({e.errors[0]})" if isinstance(e, SchemaViolation) else "output was unusable")


def _record_usage(section: str, res: CallResult, outcome: str) -> None:
    """Per-section usage ledger (Prometheus-style counters). Tokens are provider-reported, never estimated."""
    metrics.inc("content_section_calls_total", section=section, outcome=outcome)
    metrics.inc("content_tokens_in_total", res.tokens_in, section=section)
    metrics.inc("content_tokens_out_total", res.tokens_out, section=section)
    metrics.observe("content_section_latency_ms", res.latency_ms, section=section)


def _describe(sec: Section, value: Any, truncated: bool) -> str:
    if isinstance(value, str):
        n = len(value.split())
        return f"{n} words" + (" (trimmed at the length limit)" if truncated else "")
    if isinstance(value, list):
        return f"{len(value)} items"
    if isinstance(value, dict):
        return f"{sum(len(v) for v in value.values() if isinstance(v, list))} items"
    return "done"


# Fast, small sections first so the page fills quickly; the long markdown sections follow in display order.
START_ORDER = ["summary", "key_concepts", "flashcards", "recommendations", "agent_intro"]


def effective_concurrency() -> int:
    """Sections generated at once. A self-hosted model server shares one GPU/CPU, so it gets the lower
    CONTENT_SECTION_CONCURRENCY; hosted APIs get CONTENT_SECTION_CONCURRENCY_CLOUD. Ollama counts as self-hosted even behind a
    tunnel (ngrok) or on a remote machine: "not on this host" does not mean "scales like a cloud API"."""
    s = get_settings()
    try:
        first = get_router().providers[0]
        self_hosted = first.model_capabilities().local or getattr(first, "name", "") == "ollama" and not s.OLLAMA_API_KEY
    except Exception:  # no router configured (tests / misconfiguration): use the conservative value
        self_hosted = True
    return max(1, s.CONTENT_SECTION_CONCURRENCY if self_hosted else s.CONTENT_SECTION_CONCURRENCY_CLOUD)


def start_order(sections: List[Section]) -> List[Section]:
    rank = {n: i for i, n in enumerate(START_ORDER)}
    return sorted(sections, key=lambda s_: rank.get(s_.name, len(rank)))  # stable: others keep display order


def _speed_key() -> str:
    try:
        return get_router().providers[0].model
    except Exception:
        return "default"


async def run_pipeline(topic: str, level: str, score: int, only: Optional[List[str]] = None,
                       concurrency: Optional[int] = None, call=None, tracker_queue=None, profile: Optional[dict] = None) -> AsyncIterator[dict]:
    """Yields {'event':'stage'|'section', 'data':...} in real time. Closing the iterator cancels in-flight model calls."""
    from services.pipeline import Tracker
    wanted = [s_ for s_ in SECTIONS if not only or s_.name in only]
    parallel = max(1, concurrency or effective_concurrency())
    sem = asyncio.Semaphore(parallel)
    q: asyncio.Queue = tracker_queue or asyncio.Queue()
    tracker = Tracker(q)
    budget_s = get_settings().CONTENT_TIME_BUDGET_S
    t_start = _clock()
    pending = {s_.name: s_ for s_ in wanted}   # sections not started yet: the time budget is planned over these
    skey = _speed_key()

    def budgeted(sec: Section) -> Optional[Section]:
        """The section as it should run now (possibly shortened), or None if it must be deferred to keep the guide on time."""
        todo = [cb.Item(n, s_.kind, s_.max_tokens) for n, s_ in pending.items()]
        pending.pop(sec.name, None)
        if budget_s <= 0:
            return sec
        plan = cb.plan_tokens(todo, budget_s - (_clock() - t_start), cb.speed(skey), parallel)
        allowed = plan.get(sec.name, sec.max_tokens)
        if allowed is None:
            return None
        return sec if allowed >= sec.max_tokens else replace(sec, max_tokens=allowed)

    async def run(sec: Section):
        async with sem:  # FIFO: sections start in priority order (see start_order)
            eff = budgeted(sec)
            if eff is None:
                tracker.emit(f"section:{sec.name}", LABELS[sec.name], "skipped",
                             detail="Deferred to stay within the time budget. Generate it on demand when you need it.")
                metrics.inc("content_section_calls_total", section=sec.name, outcome="deferred")
                q.put_nowait({"event": "section", "data": {"name": sec.name, "value": _defaults(topic, level, score)[sec.name],
                                                           "ok": False, "skipped": True}})
                return
            stats: dict = {}
            value, ok = await generate_section(eff, topic, level, score, call, tracker=tracker, stats=stats, profile=profile)
            if ok and stats:
                cb.record_speed(skey, int(stats.get("tokens_out", 0)), float(stats.get("seconds", 0)))
            q.put_nowait({"event": "section", "data": {"name": sec.name, "value": value, "ok": ok,
                                                       **({"shortened": True} if eff is not sec else {})}})

    async def all_done():
        await asyncio.gather(*tasks, return_exceptions=True)
        q.put_nowait(None)

    tasks = [asyncio.create_task(run(sec_)) for sec_ in start_order(wanted)]
    closer = asyncio.create_task(all_done())
    first_text = False
    try:
        while (item := await q.get()) is not None:
            if not first_text and item["event"] == "chunk":
                first_text = True
                metrics.observe("content_first_text_ms", (_clock() - t_start) * 1000)
            yield item
        metrics.observe("content_guide_total_ms", (_clock() - t_start) * 1000, sections=len(wanted))
    finally:
        for t_ in tasks:  # client went away / caller stopped: cancel in-flight model requests
            if not t_.done():
                t_.cancel()
        closer.cancel()
        await asyncio.gather(*tasks, closer, return_exceptions=True)


async def iter_sections(topic: str, level: str, score: int, only: Optional[List[str]] = None,
                        concurrency: Optional[int] = None, call=None) -> AsyncIterator[tuple[str, Any, bool]]:
    """Yields (section_name, value, ok) as each section completes (non-streaming callers and tests)."""
    # aclosing: closing this generator must close the inner pipeline so in-flight model calls are cancelled
    async with aclosing(run_pipeline(topic, level, score, only, concurrency, call)) as events:
        async for ev in events:
            if ev["event"] == "section":
                d = ev["data"]
                yield d["name"], d["value"], d["ok"]


def assemble(sections: Dict[str, Any], topic: str, level: str, score: int) -> Dict[str, Any]:
    out = _defaults(topic, level, score)
    out.update({k: v for k, v in sections.items() if k in out})
    return out


def cache_key(topic: str, level: str, score: int) -> str:
    from app.infrastructure.cache.cache import make_key
    r = get_router()
    model = r.providers[0].model if r.providers else "none"
    # content depends only on topic/level/score/model: safe to share across learners (no personal data)
    return make_key("study-content", version=pr.guide_prompt_fingerprint(), topic=topic.strip().lower(), level=level, score=score, model=model)


def dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)
