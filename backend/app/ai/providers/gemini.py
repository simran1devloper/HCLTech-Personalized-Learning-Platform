"""Gemini adapter over the public REST API (no SDK dependency; key sent in header, not URL)."""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from app.ai.providers.base import BaseHTTPProvider, classify_http_error
from app.ai.types import GenerationParams, ModelCapabilities, StreamEnd, TokenUsage
from app.core.exceptions import ProviderAccountError


def _gemini_schema(schema: dict) -> dict:
    """Gemini's responseSchema is an OpenAPI subset: types are upper-case and only a few keywords are understood."""
    def conv(n):
        if isinstance(n, dict):
            return {k: ({a: conv(b) for a, b in v.items()} if k == "properties" else conv(v))
                    for k, v in n.items() if k in ("type", "properties", "items", "required", "minItems", "maxItems", "enum")}
        return n
    out = conv(schema)

    def upper(n):
        if isinstance(n, dict):
            return {k: (v.upper() if k == "type" and isinstance(v, str) else upper(v)) for k, v in n.items()}
        return n
    return upper(out)


def _error(status: int, body: str, headers=None):
    """Gemini reports a bad key as HTTP 400, which would otherwise be treated as a caller error and block failover."""
    if status == 400 and ("API_KEY_INVALID" in body or "API key not valid" in body):
        return ProviderAccountError("provider refused the account/key (invalid API key)")
    return classify_http_error(status, headers=headers)


class GeminiProvider(BaseHTTPProvider):
    name = "gemini"

    def __init__(self, model: str, api_key: str, base_url: str = "https://generativelanguage.googleapis.com/v1beta",
                 capabilities: ModelCapabilities | None = None, embedding_model: str = "text-embedding-004",
                 client=None):
        super().__init__(model, base_url, api_key,
                         capabilities or ModelCapabilities(structured_output=True, embeddings=True,
                                                           max_context_tokens=1_000_000, relative_cost=1.0,
                                                           relative_latency=1.0, quality_tier=2),
                         client)
        self.embedding_model = embedding_model

    def _headers(self):
        return {"x-goog-api-key": self.api_key}

    def _body(self, prompt, p: GenerationParams, json_mode):
        cfg = {"temperature": p.temperature, "maxOutputTokens": p.max_output_tokens}
        if json_mode:
            cfg["responseMimeType"] = "application/json"
            if p.json_schema:
                cfg["responseSchema"] = _gemini_schema(p.json_schema)
        turns = [("model" if r == "assistant" else "user", c) for r, c in p.history] + [("user", prompt)]
        body = {"contents": [{"role": r, "parts": [{"text": c}]} for r, c in turns], "generationConfig": cfg}
        if p.system:
            body["systemInstruction"] = {"parts": [{"text": p.system}]}
        return body

    @staticmethod
    def _text(d: dict) -> str:
        parts = ((d.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        return "".join(x.get("text", "") for x in parts)

    async def _call(self, prompt, params, json_mode):
        r = await self._client.post(f"{self.base_url}/models/{self.model}:generateContent", headers=self._headers(),
                                    json=self._body(prompt, params, json_mode), timeout=params.timeout_s)
        if r.status_code >= 400:
            raise _error(r.status_code, r.text, r.headers)
        d = r.json()
        u = d.get("usageMetadata") or {}
        fr = ((d.get("candidates") or [{}])[0].get("finishReason") or "").lower()
        return (self._text(d), TokenUsage(u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0)),
                "length" if fr == "max_tokens" else (fr or None))

    async def _stream(self, prompt, params) -> AsyncIterator[str]:
        async with self._client.stream("POST", f"{self.base_url}/models/{self.model}:streamGenerateContent?alt=sse",
                                       headers=self._headers(), json=self._body(prompt, params, params.json_mode),
                                       timeout=params.timeout_s) as r:
            if r.status_code >= 400:
                raise _error(r.status_code, (await r.aread()).decode("utf-8", "replace"), r.headers)
            async for line in r.aiter_lines():
                if line.startswith("data:"):
                    try:
                        d = json.loads(line[5:])
                        t = self._text(d)
                    except json.JSONDecodeError:
                        continue
                    if t:
                        yield t
                    cand = (d.get("candidates") or [{}])[0] if isinstance(d, dict) else {}
                    if cand.get("finishReason") and params.on_stream_end:
                        u = d.get("usageMetadata") or {}
                        fr = cand["finishReason"].lower()
                        params.on_stream_end(StreamEnd(self.name, self.model, "length" if fr == "max_tokens" else fr,
                                                       u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0)))

    async def generate_embeddings(self, texts):
        r = await self._client.post(
            f"{self.base_url}/models/{self.embedding_model}:batchEmbedContents", headers=self._headers(),
            json={"requests": [{"model": f"models/{self.embedding_model}", "content": {"parts": [{"text": t}]}}
                               for t in texts]}, timeout=30)
        if r.status_code >= 400:
            raise classify_http_error(r.status_code, headers=r.headers)
        return [e["values"] for e in r.json()["embeddings"]]

    async def health_check(self):
        try:
            r = await self._client.get(f"{self.base_url}/models/{self.model}", headers=self._headers(), timeout=5)
            return r.status_code < 400
        except httpx.HTTPError:
            return False
