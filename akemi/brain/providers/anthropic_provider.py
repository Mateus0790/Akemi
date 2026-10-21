"""Provedor Anthropic (Messages API) via httpx."""

from __future__ import annotations

import httpx

from .base import LLMProvider, Message, ProviderError

API_VERSION = "2023-06-01"


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.anthropic.com/v1",
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={
                "x-api-key": api_key,
                "anthropic-version": API_VERSION,
                "content-type": "application/json",
            },
        )

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        max_tokens: int = 300,
        temperature: float = 0.8,
        json_mode: bool = False,
    ) -> str:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        if json_mode:
            system += "\n\nResponda somente com um objeto JSON válido."
        convo = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role != "system"
        ] or [{"role": "user", "content": "..."}]
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": min(temperature, 1.0),
            "system": system,
            "messages": convo,
        }
        try:
            resp = await self._client.post("/messages", json=body)
            resp.raise_for_status()
            parts = resp.json().get("content", [])
            return "".join(p.get("text", "") for p in parts).strip()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"anthropic: {exc}") from exc

    async def aclose(self) -> None:
        await self._client.aclose()
