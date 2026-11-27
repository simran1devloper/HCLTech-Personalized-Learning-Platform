"""Capability-aware model routing with fallback, retries and circuit breakers.

Rules:
* A provider is *eligible* only if it satisfies every hard requirement
  (structured output, local-only for privacy, context size). Ineligible providers are never used,
  even as a last-resort fallback -> no silent capability/privacy downgrade.
* Eligible providers are ordered by a score from task complexity, latency/cost preference and quality tier.
* Recoverable failures are retried (bounded, jittered) then we fail over to the next eligible provider.
  Non-recoverable failures (4xx, schema violations) are surfaced immediately.
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import aclosing
from dataclasses import dataclass, replace
from enum import Enum
from typing import AsyncIterator, Type

from app.ai.resilience import CircuitBreaker, RetryPolicy, rate_limit_delay, retry_async
from app.ai.types import GenerationParams, LLMProvider, LLMResponse, M
from app.core.exceptions import CapabilityError, CircuitOpenError, ProviderError, ProviderRateLimitError, ProviderStallError
from app.observability.metrics import metrics

log = logging.getLogger("app.ai.router")


class Complexity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class TaskRequirements:
    complexity: Complexity = Complexity.MEDIUM
    needs_structured: bool = False
    needs_streaming: bool = False
    needs_embeddings: bool = False
    local_only: bool = False  # privacy: learner data must not leave the deployment
    prompt_tokens_estimate: int = 0
    max_attempts: int | None = None  # override the router's retry count for this call (e.g. 1 for long generations)
    prefer: str = "balanced"  # "latency" | "cost" | "quality" | "balanced" | "configured" (keep LLM_PROVIDERS order)


class ModelRouter:
    def __init__(self, providers: list[LLMProvider], retry: RetryPolicy | None = None,
                 breaker_threshold: int = 5, breaker_reset_s: float = 30.0, sleep=None):
        if not providers:
            raise ValueError("at least one provider is required")
        self.providers = providers
        self.retry = retry or RetryPolicy()
        self._breakers = {id(p): CircuitBreaker(breaker_threshold, breaker_reset_s) for p in providers}
        self._sleep = sleep

    # ---- selection ----------------------------------------------------
    @staticmethod
    def _eligible(p: LLMProvider, req: TaskRequirements) -> bool:
        c = p.model_capabilities()
        if req.needs_structured and not c.structured_output:
            return False
        if req.needs_streaming and not c.streaming:
            return False
        if req.needs_embeddings and not c.embeddings:
            return False
        if req.local_only and not c.local:
            return False
        if req.prompt_tokens_estimate and req.prompt_tokens_estimate > c.max_context_tokens:
            return False
        return True

    @staticmethod
    def _score(p: LLMProvider, req: TaskRequirements) -> float:
        c = p.model_capabilities()
        want_quality = {Complexity.LOW: 1, Complexity.MEDIUM: 2, Complexity.HIGH: 3}[req.complexity]
        s = -abs(c.quality_tier - want_quality) * 2.0
        if c.quality_tier < want_quality:
            s -= 3.0  # underpowered model for a hard task is worse than overpowered for an easy one
        w = {"latency": (0.5, 2.0), "cost": (2.0, 0.5), "quality": (0.2, 0.2), "balanced": (1.0, 1.0)}[req.prefer]
        s -= w[0] * c.relative_cost + w[1] * c.relative_latency
        return s

    def select(self, req: TaskRequirements) -> list[LLMProvider]:
        eligible = [p for p in self.providers if self._eligible(p, req)]
        if not eligible:
            raise CapabilityError("no configured provider satisfies the task requirements",
                                  details={"structured": req.needs_structured, "local_only": req.local_only,
                                           "streaming": req.needs_streaming})
        if req.prefer == "configured":  # operator-defined priority (e.g. local Ollama first, Gemini fallback)
            return eligible
        # stable sort keeps configuration order as tie-break
        return sorted(eligible, key=lambda p: -self._score(p, req))

    # ---- execution ----------------------------------------------------
    async def _run(self, p: LLMProvider, fn, max_attempts: int | None = None, wait_on_rate_limit: bool = True):
        br = self._breakers[id(p)]
        br.before_call()

        async def attempt():
            return await fn(p)
        try:
            kw = {"sleep": self._sleep} if self._sleep else {}
            policy = replace(self.retry, max_attempts=max_attempts) if max_attempts else self.retry
            result = await retry_async(attempt, policy, wait_on_rate_limit=wait_on_rate_limit, **kw)
        except ProviderError as e:
            if e.recoverable and not isinstance(e, ProviderRateLimitError):
                br.record_failure()
            else:
                br.release()  # caller error or "slow down": not a sign of poor provider health
            raise
        except BaseException:
            br.release()
            raise
        br.record_success()
        return result

    def _record(self, resp: LLMResponse, task: str):
        metrics.inc("llm_requests_total", provider=resp.provider, model=resp.model, task=task)
        metrics.inc("llm_prompt_tokens_total", resp.usage.prompt_tokens, provider=resp.provider)
        metrics.inc("llm_completion_tokens_total", resp.usage.completion_tokens, provider=resp.provider)
        metrics.observe("llm_latency_ms", resp.latency_ms, provider=resp.provider, model=resp.model)

    async def _with_failover(self, req: TaskRequirements, fn, task: str):
        last: Exception | None = None
        candidates = self.select(req)
        for i, p in enumerate(candidates):
            try:   # a rate limit is waited out only when there is no other provider left to try
                result = await self._run(p, fn, req.max_attempts, wait_on_rate_limit=i == len(candidates) - 1)
                return result
            except CircuitOpenError as e:
                last = e
                continue
            except ProviderError as e:
                last = e
                metrics.inc("llm_failures_total", provider=p.name, task=task)
                if not e.recoverable:
                    raise
                log.warning("provider %s failed (%s); trying next", p.name, type(e).__name__)
        assert last is not None
        raise last

    async def generate(self, prompt: str, params: GenerationParams = GenerationParams(),
                       req: TaskRequirements = TaskRequirements()) -> LLMResponse:
        resp = await self._with_failover(req, lambda p: p.generate(prompt, params), "generate")
        self._record(resp, "generate")
        return resp

    async def generate_structured(self, prompt: str, schema: Type[M], params: GenerationParams = GenerationParams(),
                                  req: TaskRequirements | None = None) -> tuple[M, LLMResponse]:
        req = req or TaskRequirements(needs_structured=True)
        if not req.needs_structured:
            req = TaskRequirements(**{**req.__dict__, "needs_structured": True})
        obj, resp = await self._with_failover(req, lambda p: p.generate_structured(prompt, schema, params), "structured")
        self._record(resp, "structured")
        return obj, resp

    @staticmethod
    async def _watched(p: LLMProvider, prompt: str, params: GenerationParams) -> AsyncIterator[str]:
        """Yield the provider's tokens, enforcing first-token / idle / total timeouts. A stall raises a recoverable
        ProviderError (so the router can fail over when nothing was produced yet) and closes the upstream request."""
        it = p.generate_stream(prompt, params).__aiter__()
        deadline = time.monotonic() + params.total_timeout_s if params.total_timeout_s else None
        first = True
        try:
            while True:
                limit = params.first_token_timeout_s if first else params.idle_timeout_s
                if deadline is not None:
                    limit = min(limit, deadline - time.monotonic()) if limit else deadline - time.monotonic()
                try:
                    chunk = await (asyncio.wait_for(it.__anext__(), max(limit, 0.001)) if limit else it.__anext__())
                except StopAsyncIteration:
                    return
                except asyncio.TimeoutError:
                    metrics.inc("llm_stream_stalls_total", provider=p.name, phase="first_token" if first else "idle")
                    raise ProviderStallError(f"{p.name} sent no first token before the timeout" if first
                                             else f"{p.name} went silent mid-stream (idle timeout)") from None
                if not chunk:  # liveness only (e.g. a reasoning token): restarts the silence timer, is not output
                    continue
                first = False
                yield chunk
        finally:
            aclose = getattr(it, "aclose", None)
            if aclose:  # stop the upstream HTTP request (the model server stops generating)
                await aclose()

    async def generate_stream(self, prompt: str, params: GenerationParams = GenerationParams(),
                              req: TaskRequirements = TaskRequirements(needs_streaming=True)) -> AsyncIterator[str]:
        """Fail over only before the first token; mid-stream failures surface to the caller. A rate limit before the first token
        is waited out on the last candidate (earlier ones fail over at once)."""
        last: Exception | None = None
        candidates = self.select(req)
        for i, p in enumerate(candidates):
            br = self._breakers[id(p)]
            throttled = 0
            while True:
                try:
                    br.before_call()
                except CircuitOpenError as e:
                    last = e
                    break
                started = False
                try:
                    async with aclosing(self._watched(p, prompt, params)) as watched:  # closing us closes the upstream request
                        async for chunk in watched:
                            started = True
                            yield chunk
                    br.record_success()
                    metrics.inc("llm_requests_total", provider=p.name, model=p.model, task="stream")
                    return
                except ProviderError as e:
                    limited = isinstance(e, ProviderRateLimitError)
                    if e.recoverable and not limited:
                        br.record_failure()
                    else:
                        br.release()
                    if limited:
                        metrics.inc("llm_rate_limited_total", provider=p.name)
                    if started or not e.recoverable:
                        raise
                    last = e
                    if limited and i == len(candidates) - 1:
                        delay = rate_limit_delay(self.retry, throttled + 1, e.retry_after_s)
                        if delay is not None:
                            throttled += 1
                            log.warning("provider %s is rate limiting; retry %d in %.1fs", p.name, throttled, delay)
                            await (self._sleep or asyncio.sleep)(delay)
                            continue
                    log.warning("provider %s produced no output (%s); trying next", p.name, e)
                    break
                except BaseException:  # cancelled or closed by the caller: never leave a half-open probe stuck
                    br.release()
                    raise
        assert last is not None
        raise last

    async def health(self) -> dict[str, bool]:
        return {p.name: await p.health_check() for p in self.providers}
