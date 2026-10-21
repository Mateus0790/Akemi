"""Provedor para qualquer API compatível com OpenAI Chat Completions.

Cobre NVIDIA NIM, OpenAI, Ollama (``/v1``), servidor do llama.cpp, LM Studio,
Groq, OpenRouter etc. Usa só ``httpx`` para não depender de SDKs pesados.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import httpx
import structlog

from .base import LLMProvider, Message, ProviderError

logger = structlog.get_logger()


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        *,
        name: str = "openai-compatible",
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.name = name
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout
        )

    @staticmethod
    def _body(messages, model, max_tokens, temperature, json_mode=False, stream=False):
        body = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        return body

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        max_tokens: int = 300,
        temperature: float = 0.8,
        json_mode: bool = False,
    ) -> str:
        body = self._body(messages, model, max_tokens, temperature, json_mode)
        try:
            resp = await self._client.post("/chat/completions", json=body)
            if resp.status_code == 400 and json_mode:
                # Nem todo modelo aceita response_format; tenta de novo sem ele.
                body.pop("response_format", None)
                resp = await self._client.post("/chat/completions", json=body)
            resp.raise_for_status()
            data = resp.json()
            return (data["choices"][0]["message"].get("content") or "").strip()
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise ProviderError(f"{self.name}: {exc}") from exc

    async def stream(
        self,
        messages: list[Message],
        *,
        model: str,
        max_tokens: int = 300,
        temperature: float = 0.8,
    ) -> AsyncIterator[str]:
        body = self._body(messages, model, max_tokens, temperature, stream=True)
        try:
            async with self._client.stream("POST", "/chat/completions", json=body) as resp:
                resp.raise_for_status()
                done = False
                # Consome o stream até o fim (sem ``break``) para o httpx
                # fechar os geradores internos corretamente.
                async for line in resp.aiter_lines():
                    if done or not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk == "[DONE]":
                        done = True
                        continue
                    try:
                        delta = json.loads(chunk)["choices"][0]["delta"].get("content")
                    except (ValueError, KeyError, IndexError):
                        continue
                    if delta:
                        yield delta
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name}: {exc}") from exc

    async def aclose(self) -> None:
        await self._client.aclose()
