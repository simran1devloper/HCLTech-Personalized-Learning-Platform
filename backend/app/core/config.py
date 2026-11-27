"""Application settings for the new `app` package (reads the same .env as legacy `config.py`)."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_DEFAULT_SECRET = "change-this-secret-key-in-production"


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore")

    ENVIRONMENT: str = "development"
    SECRET_KEY: str = INSECURE_DEFAULT_SECRET
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24

    # Local database (SQLite). ":memory:" for tests.
    DATABASE_PATH: str = "data/app.db"

    # LLM providers: comma-separated priority list. Default: local Ollama first, Gemini (cloud) as fallback.
    LLM_PROVIDERS: str = "ollama,gemini"
    # true => never send learner data to a cloud provider (only local providers are eligible)
    LLM_LOCAL_ONLY: bool = False
    LLM_MAX_OUTPUT_TOKENS: int = 8192
    # Study-material generation: sections generated concurrently (match the model server's parallelism)
    # Self-hosted Ollama serves ONE request at a time unless started with OLLAMA_NUM_PARALLEL (its log shows `n_seq_max`). A second
    # request would just queue server-side, get no first token, and be cut off by the stall watchdog. Raise only if your server
    # really runs requests in parallel (GPU box, OLLAMA_NUM_PARALLEL>1).
    CONTENT_SECTION_CONCURRENCY: int = 1
    # Target wall-clock for one study guide. After each section the pipeline measures the model's real speed (tokens/s) and
    # shrinks the remaining long sections, or defers optional ones to "generate on demand", so the guide finishes in time.
    # 0 = no budget (always full length).
    CONTENT_TIME_BUDGET_S: float = 600.0
    CONTENT_SECTION_CONCURRENCY_CLOUD: int = 10  # used when the first provider is a cloud API: a whole guide at once
    CONTENT_GLOBAL_CONCURRENCY: int = 20  # generation calls in flight across ALL learners (study guides + quiz questions); 0 = no cap
    # Hosted providers write new quiz questions in this many simultaneous calls (1 = a single call). Not used for local Ollama.
    QUESTION_PARALLEL_BATCHES: int = 4
    # Stream section output and watch for stalls (instead of one blocking request with a flat timeout). false = legacy.
    CONTENT_STREAMING: bool = True
    # Send each JSON section's schema to the provider for constrained decoding (falls back to plain JSON mode automatically)
    CONTENT_NATIVE_SCHEMA: bool = True
    # Few-shot examples in the format-heavy section prompts, and the Mermaid-diagram instruction for the long sections
    CONTENT_FEWSHOT: bool = True
    CONTENT_MERMAID: bool = True
    LLM_FIRST_TOKEN_S: float = 120.0  # model load + prompt evaluation (and queueing behind another request on the server)
    LLM_IDLE_TOKEN_S: float = 30.0    # max silence between tokens once the model has started answering
    LLM_STREAM_TOTAL_S: float = 600.0 # hard cap for one section stream
    CONTENT_CACHE_TTL_S: int = 24 * 3600
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash-lite"
    # Ollama: local server (http://localhost:11434) or Ollama Cloud (https://ollama.com + OLLAMA_API_KEY)
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_API_KEY: str = ""
    OLLAMA_BASIC_AUTH: str = ""  # "user:password" when Ollama sits behind a Basic-auth gateway (e.g. an ngrok tunnel)
    OLLAMA_MODEL: str = "llama3.1:8b"
    # Context window requested from Ollama (prompt + output must fit). 0 = use the server default.
    OLLAMA_NUM_CTX: int = 4096  # prompts are < 700 tokens and outputs <= 1800: a bigger window only wastes RAM (KV cache) and time
    # Reasoning models stream a long chain of thought first and it uses up the output budget; study content wants the answer only.
    OLLAMA_DISABLE_THINKING: bool = True
    # OpenRouter (hosted models behind one key). Put OPENROUTER_API_KEY in .env and add "openrouter" to LLM_PROVIDERS.
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_MODEL: str = "openai/gpt-4o-mini"
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    OPENROUTER_REFERER: str = "http://localhost:8000"   # optional attribution headers OpenRouter shows in its dashboard
    OPENROUTER_TITLE: str = "LearnAI"
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    LLM_TIMEOUT_S: float = 180.0  # local models can be slow
    # Question-bank population (new quiz topics). A request waits at most this long for generation, then starts with the questions already saved
    # (>= QUESTION_MIN_USABLE) while the rest keeps generating in the background. 0 batch size = auto (small batches for local Ollama, one call otherwise).
    QUESTION_GENERATION_BUDGET_S: float = 240.0
    QUESTION_BATCH_SIZE: int = 0
    QUESTION_MIN_USABLE: int = 3
    LLM_MAX_ATTEMPTS: int = 3

    # Rollout flags for the agentic content platform (see TASKLIST.md). Defaults keep today's behaviour unchanged.
    CONTENT_PIPELINE: Literal["legacy", "v2"] = "legacy"      # legacy: current section generation; v2: new content engine
    GENERATION_BACKEND: Literal["inline", "queue"] = "inline"  # inline: generate inside the request; queue: durable workers
    CACHE_BACKEND: Literal["memory", "redis"] = "memory"
    STORE_BACKEND: Literal["sqlite", "postgres"] = "sqlite"
    ATOM_REUSE: Literal["off", "on"] = "off"
    AGENT_PIPELINE: Literal["off", "on"] = "off"
    REVIEW_GATES: Literal["off", "on"] = "off"
    DATABASE_URL: str = ""   # used when STORE_BACKEND=postgres, e.g. postgresql+psycopg://user:pass@host:5432/db

    # AI Tutor. Context window 0 = automatic (OLLAMA_NUM_CTX while "ollama" is a configured provider, otherwise 16000).
    TUTOR_CONTEXT_WINDOW_TOKENS: int = 0
    TUTOR_MAX_OUTPUT_TOKENS: int = 1600
    TUTOR_FIRST_TOKEN_S: float = 180.0   # a CPU-only local model can need minutes to read the prompt
    TUTOR_IDLE_TOKEN_S: float = 60.0
    TUTOR_ROUTING: Literal["configured", "balanced"] = "configured"   # "balanced": order providers by task complexity
    TUTOR_COST_INPUT_PER_MTOK: float = 0.0    # USD per million tokens, used only for the cost estimate in /tutor/usage (0 = not configured)
    TUTOR_COST_OUTPUT_PER_MTOK: float = 0.0

    # Optional infra
    REDIS_URL: str = ""
    RATE_LIMIT_PER_MINUTE: int = 60
    SEARCH_PROVIDER: str = ""  # optional external search; empty = disabled

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() == "production"

    def assert_safe_for_environment(self) -> None:
        if self.is_production and (self.SECRET_KEY == INSECURE_DEFAULT_SECRET or len(self.SECRET_KEY) < 32):
            raise RuntimeError("SECRET_KEY must be set to a strong value (>=32 chars) in production")


@lru_cache
def get_settings() -> AppSettings:
    return AppSettings()
