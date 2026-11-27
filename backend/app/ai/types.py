"""Provider-independent LLM types and the provider interface."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Callable, Protocol, Type, TypeVar, runtime_checkable

from pydantic import BaseModel

M = TypeVar("M", bound=BaseModel)


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class GenerationParams:
    temperature: float = 0.7
    max_output_tokens: int = 4096
    timeout_s: float = 60.0
    system: str | None = None
    # Earlier conversation turns, oldest first, as (role, content) with role "user" | "assistant". Sent natively before the prompt.
    history: tuple[tuple[str, str], ...] = ()
    json_mode: bool = False  # ask the provider for JSON output (generate_structured always does)
    # JSON Schema (self-contained, no $ref) for constrained decoding; honoured with json_mode by providers that support it
    json_schema: dict | None = field(default=None, compare=False, repr=False)
    # Streaming watchdogs (seconds, None = off). A model that is slowly producing tokens is healthy; one that is silent is not.
    first_token_timeout_s: float | None = None  # max wait for the first token (covers model load + prompt evaluation)
    idle_timeout_s: float | None = None         # max silence between tokens once streaming has started
    total_timeout_s: float | None = None        # hard cap for the whole stream
    # Called once by the provider when a stream finishes, with the usage/finish reason the provider reported.
    on_stream_end: Callable[["StreamEnd"], None] | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class StreamEnd:
    """Terminal metadata of a streamed generation. Fields are 0/None when the provider does not report them."""
    provider: str = ""
    model: str = ""
    finish_reason: str | None = None  # "length" = hit the token cap
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass(frozen=True)
class LLMResponse:
    text: str
    provider: str
    model: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    latency_ms: float = 0.0
    finish_reason: str | None = None  # "stop" | "length" (hit the token cap) | None if the provider does not say


@dataclass(frozen=True)
class ModelCapabilities:
    structured_output: bool = False
    streaming: bool = True
    embeddings: bool = False
    max_context_tokens: int = 8192
    local: bool = False  # runs on-prem: data never leaves the deployment
    relative_cost: float = 1.0  # unitless, lower = cheaper
    relative_latency: float = 1.0  # unitless, lower = faster
    quality_tier: int = 2  # 1 small .. 3 frontier


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str

    async def generate(self, prompt: str, params: GenerationParams = ...) -> LLMResponse: ...
    def generate_stream(self, prompt: str, params: GenerationParams = ...) -> AsyncIterator[str]: ...
    async def generate_structured(self, prompt: str, schema: Type[M], params: GenerationParams = ...) -> tuple[M, LLMResponse]: ...
    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]: ...
    def model_capabilities(self) -> ModelCapabilities: ...
    def token_usage(self) -> TokenUsage: ...  # cumulative since process start
    async def health_check(self) -> bool: ...
