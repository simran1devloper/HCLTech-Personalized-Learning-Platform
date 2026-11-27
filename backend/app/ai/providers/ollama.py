"""Ollama adapter (local models)."""
from __future__ import annotations

import base64
import json
import logging
from urllib.parse import urlsplit
from typing import AsyncIterator

import httpx

from app.ai.providers.base import BaseHTTPProvider, classify_http_error
from app.ai.types import GenerationParams, ModelCapabilities, StreamEnd, TokenUsage

log = logging.getLogger("app.ai.ollama")


def normalize_base_url(url: str) -> str:
    """Accept 'https://ollama.com', '.../api' or a full '.../api/chat' endpoint and return the bare origin+prefix."""
    u = url.strip().rstrip("/")
    for suffix in ("/api/chat", "/api/generate", "/api"):
        if u.endswith(suffix):
            return u[: -len(suffix)]
    return u


class OllamaProvider(BaseHTTPProvider):
    name = "ollama"

    def __init__(self, model: str, base_url: str = "http://localhost:11434", api_key: str = "",
                 capabilities: ModelCapabilities | None = None, embedding_model: str = "", client=None,
                 basic_auth: str = "", num_ctx: int | None = None, disable_thinking: bool = True):
        super().__init__(model, normalize_base_url(base_url), api_key,
                         capabilities or ModelCapabilities(structured_output=True, local=True,
                                                           relative_cost=0.0, relative_latency=2.0,
                                                           quality_tier=1, embeddings=bool(embedding_model)),
                         client)
        self.embedding_model = embedding_model
        self._schema_ok = True  # flips to False if the server does not understand schema-valued `format`
        # Reasoning models (magistral, qwen3, deepseek-r1, ...) stream their chain of thought before the answer and it eats the
        # token budget. Study content wants the answer only, so ask for `think: false` (dropped automatically if unsupported).
        self.disable_thinking = disable_thinking
        self._think_ok = True
        self.num_ctx = num_ctx  # context window to request; Ollama's own default (often 4096) is too small for long outputs
        self._basic = basic_auth  # "user:password" for a gateway in front of Ollama (e.g. ngrok/nginx); never logged

    def _headers(self) -> dict:
        h: dict[str, str] = {}
        if self._basic:
            h["Authorization"] = "Basic " + base64.b64encode(self._basic.encode()).decode()
        elif self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        host = (urlsplit(self.base_url).hostname or "").lower()
        if host.endswith(("ngrok-free.app", "ngrok-free.dev", "ngrok.app", "ngrok.io", "ngrok.dev")):
            h["ngrok-skip-browser-warning"] = "true"  # ngrok's interstitial page would otherwise replace API responses
        return h

    def _body(self, prompt, p: GenerationParams, json_mode, stream=False):
        msgs = ([{"role": "system", "content": p.system}] if p.system else []) + [{"role": r, "content": c} for r, c in p.history] + [{"role": "user", "content": prompt}]
        body = {"model": self.model, "messages": msgs, "stream": stream,
                "options": {"temperature": p.temperature, "num_predict": p.max_output_tokens}}
        if self.num_ctx:
            body["options"]["num_ctx"] = self.num_ctx
        if self.disable_thinking and self._think_ok:
            body["think"] = False
        if json_mode:
            # Ollama >= 0.5 constrains decoding to a JSON Schema passed as `format`; older servers reject it (see _schema_rejected)
            body["format"] = p.json_schema if (p.json_schema and self._schema_ok) else "json"
        return body

    def _rejected(self, status: int, text: str, params: GenerationParams) -> bool:
        """After a 400: work out which optional request feature the server does not understand, switch it off for good and
        return True so the caller retries once. `think` is blamed when the error mentions it, otherwise the schema `format`."""
        if status != 400:
            return False
        if self.disable_thinking and self._think_ok and "think" in text.lower():
            self._think_ok = False
            log.warning("ollama does not support the `think` option; continuing without it")
            return True
        if params.json_schema and self._schema_ok:
            self._schema_ok = False
            log.warning("ollama rejected a JSON-Schema format; falling back to plain json mode")
            return True
        return False

    async def _call(self, prompt, params, json_mode):
        r = await self._client.post(f"{self.base_url}/api/chat", json=self._body(prompt, params, json_mode),
                                    headers=self._headers(), timeout=params.timeout_s)
        if self._rejected(r.status_code, r.text, params):
            r = await self._client.post(f"{self.base_url}/api/chat", json=self._body(prompt, params, json_mode),
                                        headers=self._headers(), timeout=params.timeout_s)
        if r.status_code >= 400:
            raise classify_http_error(r.status_code, headers=r.headers)
        d = r.json()
        return ((d.get("message") or {}).get("content", ""), TokenUsage(d.get("prompt_eval_count", 0), d.get("eval_count", 0)),
                d.get("done_reason"))

    async def _stream(self, prompt, params) -> AsyncIterator[str]:
        for _ in range(2):  # second pass only after a schema rejection (before any token was produced)
            async with self._client.stream("POST", f"{self.base_url}/api/chat",
                                           json=self._body(prompt, params, params.json_mode, True),
                                           headers=self._headers(), timeout=params.timeout_s) as r:
                if r.status_code == 400 and self._rejected(400, (await r.aread()).decode("utf-8", "replace"), params):
                    continue
                if r.status_code >= 400:
                    raise classify_http_error(r.status_code, headers=r.headers)
                async for line in r.aiter_lines():
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    msg = d.get("message") or {}
                    if msg.get("content"):
                        yield msg["content"]
                    elif msg.get("thinking"):
                        yield ""  # reasoning token: proof of life for the stall watchdog, not content
                    if d.get("done"):
                        if params.on_stream_end:
                            params.on_stream_end(StreamEnd(self.name, self.model, d.get("done_reason"),
                                                           d.get("prompt_eval_count", 0), d.get("eval_count", 0)))
                        return

    async def generate_embeddings(self, texts):
        if not self.embedding_model:
            return await super().generate_embeddings(texts)
        r = await self._client.post(f"{self.base_url}/api/embed",
                                    json={"model": self.embedding_model, "input": texts}, headers=self._headers(), timeout=30)
        if r.status_code >= 400:
            raise classify_http_error(r.status_code, headers=r.headers)
        return r.json()["embeddings"]

    async def list_models(self) -> list[str]:
        r = await self._client.get(f"{self.base_url}/api/tags", headers=self._headers(), timeout=15)
        if r.status_code >= 400:
            raise classify_http_error(r.status_code, headers=r.headers)
        return [m["name"] for m in r.json().get("models", [])]

    async def health_check(self) -> bool:
        try:
            return (await self._client.get(f"{self.base_url}/api/tags", headers=self._headers(), timeout=5)).status_code < 400
        except httpx.HTTPError:
            return False
