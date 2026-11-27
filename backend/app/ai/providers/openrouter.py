"""OpenRouter (https://openrouter.ai): one API key, many hosted models, OpenAI-compatible protocol.

Identical wire format to OpenAI, plus two optional attribution headers OpenRouter documents (``HTTP-Referer`` and ``X-Title``). The key is read from
``OPENROUTER_API_KEY`` (settings / .env) and is never logged or returned to clients."""
from __future__ import annotations

from app.ai.providers.openai_compat import OpenAICompatProvider
from app.ai.types import ModelCapabilities

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterProvider(OpenAICompatProvider):
    name = "openrouter"

    def __init__(self, model: str, api_key: str, base_url: str = DEFAULT_BASE_URL, referer: str = "", title: str = "", capabilities: ModelCapabilities | None = None, client=None):
        super().__init__(model, base_url, api_key,
                         capabilities or ModelCapabilities(structured_output=True, local=False, relative_cost=0.3, relative_latency=0.6, quality_tier=3), client=client)
        self.referer, self.title = referer, title

    def _headers(self) -> dict:
        h = super()._headers()
        if self.referer:
            h["HTTP-Referer"] = self.referer
        if self.title:
            h["X-Title"] = self.title
        return h
