"""Build a ModelRouter from settings; credentials stay configurable, never hard-coded."""
from __future__ import annotations

from urllib.parse import urlsplit

from app.ai.model_router import ModelRouter
from app.ai.providers.gemini import GeminiProvider
from app.ai.providers.ollama import OllamaProvider
from app.ai.providers.openai_compat import OpenAICompatProvider
from app.ai.providers.openrouter import OpenRouterProvider
from app.ai.resilience import RetryPolicy
from app.ai.types import ModelCapabilities
from app.core.config import AppSettings


def is_local_url(url: str) -> bool:
    """True only for loopback/private hosts; an https://ollama.com endpoint is CLOUD for privacy purposes."""
    import ipaddress
    host = (urlsplit(url).hostname or "").lower()
    if host in ("localhost", "host.docker.internal") or host.endswith(".local"):
        return True
    try:
        a = ipaddress.ip_address(host)
        return a.is_loopback or a.is_private
    except ValueError:
        return False


def build_router(s: AppSettings) -> ModelRouter:
    providers = []
    for name in [x.strip().lower() for x in s.LLM_PROVIDERS.split(",") if x.strip()]:
        if name == "gemini" and s.GEMINI_API_KEY:
            providers.append(GeminiProvider(s.GEMINI_MODEL, s.GEMINI_API_KEY))
        elif name == "ollama":
            local = is_local_url(s.OLLAMA_BASE_URL)
            if not local and not (s.OLLAMA_API_KEY or s.OLLAMA_BASIC_AUTH):
                continue  # a remote Ollama must be authenticated
            caps = ModelCapabilities(structured_output=True, local=local, relative_cost=0.0 if local else 0.5,
                                     relative_latency=2.0 if local else 0.8, quality_tier=2)
            providers.append(OllamaProvider(s.OLLAMA_MODEL, s.OLLAMA_BASE_URL, s.OLLAMA_API_KEY, capabilities=caps, basic_auth=s.OLLAMA_BASIC_AUTH,
                                            num_ctx=s.OLLAMA_NUM_CTX or None, disable_thinking=s.OLLAMA_DISABLE_THINKING))
        elif name == "openrouter" and s.OPENROUTER_API_KEY.strip():       # without a key the entry is skipped, so listing it is always safe
            providers.append(OpenRouterProvider(s.OPENROUTER_MODEL, s.OPENROUTER_API_KEY.strip(), s.OPENROUTER_BASE_URL, s.OPENROUTER_REFERER, s.OPENROUTER_TITLE))
        elif name in ("openai", "openai_compat") and (s.OPENAI_API_KEY or "localhost" in s.OPENAI_BASE_URL):
            providers.append(OpenAICompatProvider(s.OPENAI_MODEL, s.OPENAI_BASE_URL, s.OPENAI_API_KEY))
    if not providers:
        raise RuntimeError("no LLM provider configured (check LLM_PROVIDERS and credentials)")
    return ModelRouter(providers, RetryPolicy(max_attempts=s.LLM_MAX_ATTEMPTS))
