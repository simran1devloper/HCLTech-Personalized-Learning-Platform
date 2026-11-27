"""Shared plumbing for HTTP-based provider adapters."""
from __future__ import annotations

import time
from typing import AsyncIterator, Type

import httpx

from app.ai.structured_output import parse_structured
from app.ai.types import GenerationParams, LLMResponse, M, ModelCapabilities, TokenUsage
from app.core.exceptions import NonRecoverableProviderError, ProviderAccountError, ProviderError, ProviderRateLimitError


def _retry_after(headers) -> float | None:
    """Seconds from a Retry-After header (the HTTP-date form is ignored: normal backoff applies)."""
    try:
        v = float((headers or {}).get("retry-after", ""))
    except (TypeError, ValueError):
        return None
    return v if 0 <= v < 86400 else None


def classify_http_error(status: int, body: str = "", headers=None) -> ProviderError:
    """429: wait and retry. 5xx/408: retry. 401/402/403: fail over without retry. Other 4xx are caller errors: never retry or fail over."""
    if status in (401, 402, 403):
        return ProviderAccountError(f"provider refused the account/key (HTTP {status})")
    if status == 429:
        return ProviderRateLimitError("provider returned HTTP 429", retry_after_s=_retry_after(headers))
    if status == 408 or status >= 500:
        return ProviderError(f"provider returned HTTP {status}")
    return NonRecoverableProviderError(f"provider rejected request (HTTP {status})")


class BaseHTTPProvider:
    name = "base"

    def __init__(self, model: str, base_url: str, api_key: str = "",
                 capabilities: ModelCapabilities | None = None,
                 client: httpx.AsyncClient | None = None):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._caps = capabilities or ModelCapabilities()
        self._client = client or httpx.AsyncClient()
        self._usage = TokenUsage()

    # -- subclass hooks ------------------------------------------------
    async def _call(self, prompt: str, params: GenerationParams, json_mode: bool) -> tuple[str, TokenUsage]:
        raise NotImplementedError

    async def _stream(self, prompt: str, params: GenerationParams) -> AsyncIterator[str]:
        raise NotImplementedError
        yield ""  # pragma: no cover

    # -- interface -----------------------------------------------------
    def model_capabilities(self) -> ModelCapabilities:
        return self._caps

    def token_usage(self) -> TokenUsage:
        return self._usage

    def _account(self, u: TokenUsage) -> None:
        self._usage = TokenUsage(self._usage.prompt_tokens + u.prompt_tokens,
                                 self._usage.completion_tokens + u.completion_tokens)

    async def generate(self, prompt: str, params: GenerationParams = GenerationParams(),
                       *, _json: bool = False) -> LLMResponse:
        t0 = time.perf_counter()
        try:
            text, usage, *extra = await self._call(prompt, params, _json or params.json_mode)
        except httpx.TimeoutException as e:
            raise ProviderError(f"{self.name} timed out") from e
        except httpx.TransportError as e:
            raise ProviderError(f"{self.name} unreachable") from e
        if not text:
            raise ProviderError(f"{self.name} returned an empty response")
        self._account(usage)
        return LLMResponse(text, self.name, self.model, usage, (time.perf_counter() - t0) * 1000,
                           finish_reason=extra[0] if extra else None)

    def generate_stream(self, prompt: str, params: GenerationParams = GenerationParams()) -> AsyncIterator[str]:
        return self._stream(prompt, params)

    async def generate_structured(self, prompt: str, schema: Type[M],
                                  params: GenerationParams = GenerationParams()) -> tuple[M, LLMResponse]:
        resp = await self.generate(prompt, params, _json=True)
        return parse_structured(resp.text, schema), resp

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        raise NonRecoverableProviderError(f"{self.name} does not support embeddings")

    async def health_check(self) -> bool:
        raise NotImplementedError
