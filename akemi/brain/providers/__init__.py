"""LLM providers e fábrica a partir das configurações."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import structlog

from .anthropic_provider import AnthropicProvider
from .base import LLMProvider, Message, ProviderError
from .echo_provider import EchoProvider
from .fallback import Brain, FallbackProvider
from .openai_provider import OpenAICompatibleProvider

if TYPE_CHECKING:
    from akemi.core.config import Settings

logger = structlog.get_logger()

__all__ = [
    "AnthropicProvider",
    "Brain",
    "EchoProvider",
    "FallbackProvider",
    "LLMProvider",
    "Message",
    "OpenAICompatibleProvider",
    "ProviderError",
    "build_brains",
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


SwitchCallback = Callable[[str, str, str], None]


def build_brains(
    settings: "Settings", on_switch: SwitchCallback | None = None
) -> tuple[LLMProvider, LLMProvider]:
    """Monta o cérebro rápido e o profundo, com os reservas de ``BRAIN_FALLBACK``.

    Sem reserva configurado, devolve os providers simples (como antes). Com
    reserva, cada um vira um :class:`FallbackProvider`: se o principal cair, ela
    troca sozinha. Um cérebro que nem dá para montar (ex.: NIM sem API key) é
    pulado, desde que sobre algum.
    """
    reserves = [n for n in settings.brain_fallback if n != settings.brain_provider]
    fb_fast = settings.fallback_fast_model
    fb_deep = settings.fallback_deep_model or fb_fast
    reserve_map = {settings.fast_model: fb_fast, settings.deep_model: fb_deep}
    built: dict[str, LLMProvider] = {}

    def get(name: str) -> LLMProvider | None:
        if name not in built:
            try:
                built[name] = build_provider(name, settings)
            except ProviderError as exc:
                if not reserves:
                    raise
                logger.warning("brain unavailable, skipping", brain=name, error=str(exc))
                return None
        return built[name]

    def chain(first: str) -> LLMProvider:
        names = [first, *(n for n in reserves if n != first)]
        brains = []
        for name in names:
            provider = get(name)
            if provider is None:
                continue
            # O principal usa os nomes do .env; os reservas, os modelos locais.
            brains.append(Brain(provider, name, {} if name in (settings.brain_provider, first) else reserve_map))
        if not brains:
            raise ProviderError("nenhum cérebro disponível (confira as chaves e BRAIN_FALLBACK)")
        if len(brains) == 1 and not reserves:
            return brains[0].provider
        return FallbackProvider(brains, retry_after_s=settings.brain_retry_after_s, on_switch=on_switch)

    fast = chain(settings.brain_provider)
    deep_name = settings.effective_deep_provider
    if deep_name == settings.brain_provider:
        return fast, fast
    return fast, chain(deep_name)
