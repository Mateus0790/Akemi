"""LLM providers e fábrica a partir das configurações."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .anthropic_provider import AnthropicProvider
from .base import LLMProvider, Message, ProviderError
from .echo_provider import EchoProvider
from .openai_provider import OpenAICompatibleProvider

if TYPE_CHECKING:
    from akemi.core.config import Settings

__all__ = [
    "AnthropicProvider",
    "EchoProvider",
    "LLMProvider",
    "Message",
    "OpenAICompatibleProvider",
    "ProviderError",
    "build_provider",
]


def build_provider(name: str, settings: "Settings") -> LLMProvider:
    t = settings.llm_timeout_s
    if name == "nim":
        if not settings.nvidia_api_key:
            raise ProviderError("NVIDIA_API_KEY não configurada (.env)")
        return OpenAICompatibleProvider(
            settings.nim_base_url, settings.nvidia_api_key, name="nim", timeout=t
        )
    if name == "openai":
        return OpenAICompatibleProvider(
            settings.openai_base_url, settings.openai_api_key, name="openai", timeout=t
        )
    if name == "ollama":
        return OpenAICompatibleProvider(settings.ollama_base_url, name="ollama", timeout=t)
    if name == "llamacpp":
        return OpenAICompatibleProvider(
            settings.llamacpp_base_url, name="llamacpp", timeout=t
        )
    if name == "anthropic":
        return AnthropicProvider(settings.anthropic_api_key, timeout=t)
    if name == "echo":
        return EchoProvider()
    raise ProviderError(f"provider desconhecido: {name}")
