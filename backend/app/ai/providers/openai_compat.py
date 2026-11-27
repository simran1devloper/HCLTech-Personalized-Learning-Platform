"""OpenAI-compatible adapter (OpenAI, vLLM, LM Studio, OpenRouter, ...)."""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from app.ai.providers.base import BaseHTTPProvider, classify_http_error
from app.ai.types import GenerationParams, ModelCapabilities, StreamEnd, TokenUsage
from app.core.exceptions import ProviderError


class OpenAICompatProvider(BaseHTTPProvider):
    name = "openai_compat"

    def __init__(self, model: str, base_url: str = "https://api.openai.com/v1", api_key: str = "",
                 capabilities: ModelCapabilities | None = None, embedding_model: str = "", client=None):
        super().__init__(model, base_url, api_key,
                         capabilities or ModelCapabilities(structured_output=True, embeddings=bool(embedding_model)),
                         client)
        self.embedding_model = embedding_model

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def _body(self, prompt: str, p: GenerationParams, json_mode: bool, stream=False) -> dict:
        msgs = ([{"role": "system", "content": p.system}] if p.system else []) + [{"role": r, "content": c} for r, c in p.history] + [{"role": "user", "content": prompt}]
        body = {"model": self.model, "messages": msgs, "temperature": p.temperature,
                "max_tokens": p.max_output_tokens, "stream": stream}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        return body

    async def _call(self, prompt, params, json_mode):
        r = await self._client.post(f"{self.base_url}/chat/completions", headers=self._headers(),
                                    json=self._body(prompt, params, json_mode), timeout=params.timeout_s)
        if r.status_code >= 400:
            raise classify_http_error(r.status_code, headers=r.headers)
        d = r.json()
        choice = (d.get("choices") or [{}])[0]
        text = choice.get("message", {}).get("content") or ""
        u = d.get("usage") or {}
        return text, TokenUsage(u.get("prompt_tokens", 0), u.get("completion_tokens", 0)), choice.get("finish_reason")

    async def _stream(self, prompt, params) -> AsyncIterator[str]:
        async with self._client.stream("POST", f"{self.base_url}/chat/completions", headers=self._headers(),
                                       json=self._body(prompt, params, params.json_mode, True), timeout=params.timeout_s) as r:
            if r.status_code >= 400:
                raise classify_http_error(r.status_code, headers=r.headers)
            finish = None
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if event.get("error"):                       # some gateways report a mid-stream failure inside a 200 response
                    raise ProviderError(f"{self.name} reported an error while streaming")
                usage = event.get("usage") or {}
                choice = (event.get("choices") or [{}])[0]
                delta = (choice.get("delta") or {}).get("content")
                if delta:
                    yield delta
                finish = choice.get("finish_reason") or finish
                if (usage or choice.get("finish_reason")) and params.on_stream_end:   # usage may arrive in a later, choice-less chunk
                    params.on_stream_end(StreamEnd(self.name, self.model, finish, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)))

    async def generate_embeddings(self, texts):
        if not self.embedding_model:
            return await super().generate_embeddings(texts)
        r = await self._client.post(f"{self.base_url}/embeddings", headers=self._headers(),
                                    json={"model": self.embedding_model, "input": texts}, timeout=30)
        if r.status_code >= 400:
            raise classify_http_error(r.status_code, headers=r.headers)
        return [x["embedding"] for x in r.json()["data"]]

    async def health_check(self) -> bool:
        try:
            r = await self._client.get(f"{self.base_url}/models", headers=self._headers(), timeout=5)
            return r.status_code < 400
        except httpx.HTTPError:
            return False
