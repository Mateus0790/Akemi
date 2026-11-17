"""Voz na nuvem com o ElevenLabs (https://elevenlabs.io).

Alternativa ao Fish Audio: vozes da biblioteca do ElevenLabs (ou uma clonada
lá), com o áudio tocando enquanto ainda está chegando. Precisa de internet e de
uma chave da API; se o ElevenLabs cair (sem internet, sem créditos, chave
errada), o :class:`~akemi.core.health.ResilientTTS` passa a frase para a voz
local reserva (``TTS_FALLBACK``: Kokoro ou Piper), então nada se perde.

Configuração (``.env``)::

    pip install -e ".[elevenlabs]"
    TTS_ENGINE=elevenlabs
    ELEVENLABS_API_KEY=...        # https://elevenlabs.io/app/settings/api-keys
    ELEVENLABS_VOICE_ID=...       # id da voz (no site: a voz > "Copy voice ID")
    # ELEVENLABS_MODEL=eleven_flash_v2_5   # mais rápido para começar a falar

O pedido usa ``output_format=pcm_<taxa>``: PCM 16 bits mono cru, tocado em
streaming pela base :class:`~akemi.tts.cloud_engine.CloudStreamTTS`.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from .cloud_engine import CloudStreamTTS, CloudTTSError
from .piper_engine import AudioPlayer

DEFAULT_BASE_URL = "https://api.elevenlabs.io"
DEFAULT_MODEL = "eleven_multilingual_v2"
KEYS_URL = "https://elevenlabs.io/app/settings/api-keys"
VOICES_URL = "https://elevenlabs.io/app/voice-library"
# Taxas que a API aceita em PCM. 44100 e 48000 só nos planos pagos mais altos.
PCM_RATES = (8000, 16000, 22050, 24000, 44100, 48000)


class ElevenLabsError(CloudTTSError):
    """O ElevenLabs não falou (chave, créditos, voz inexistente, sem internet...)."""


def _detail(body: str) -> tuple[str, str]:
    """(status, mensagem) do erro da API: ``{"detail": {"status": ..., "message": ...}}``."""
    message = body.strip()
    try:
        data = json.loads(body)
    except ValueError:
        return "", message[:200]
    detail = data.get("detail", data) if isinstance(data, dict) else data
    if isinstance(detail, dict):
        return str(detail.get("status") or detail.get("code") or ""), str(detail.get("message") or message)[:200]
    if isinstance(detail, list) and detail and isinstance(detail[0], dict):  # erro de validação (422)
        return "validation", str(detail[0].get("msg") or message)[:200]
    return "", str(detail)[:200]


def explain_error(status: int, body: str) -> str:
    """Transforma a resposta de erro da API numa frase que o dono entende."""
    code, message = _detail(body)
    if code == "quota_exceeded" or status == 402:
        return "acabaram os créditos do ElevenLabs (veja o uso em elevenlabs.io/app/subscription)"
    if code == "voice_not_found" or status == 404:
        return f"o ElevenLabs não achou essa voz: confira o ELEVENLABS_VOICE_ID ({message})"
    if code == "missing_permissions":
        return f"a chave do ElevenLabs não tem permissão de Text to Speech: libere em {KEYS_URL}"
    if "output_format" in message or "pcm" in message.lower():
        return f"o seu plano do ElevenLabs não aceita essa taxa: use ELEVENLABS_SAMPLE_RATE=24000 ({message})"
    if status == 401:
        return f"o ElevenLabs recusou a chave: confira a ELEVENLABS_API_KEY ({KEYS_URL})"
    if status == 429:
        return "o ElevenLabs pediu para ir mais devagar (limite de pedidos ao mesmo tempo)"
    if status >= 500:
        return f"o ElevenLabs está fora do ar ou sobrecarregado (HTTP {status})"
    return f"o ElevenLabs respondeu HTTP {status}: {message}"


class ElevenLabsTTS(CloudStreamTTS):
    service = "ElevenLabs"
    log_name = "elevenlabs"
    extra = "elevenlabs"
    error_cls = ElevenLabsError

    def __init__(
        self,
        api_key: str,
        *,
        voice_id: str = "",
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        stability: float | None = None,
        similarity_boost: float | None = None,
        style: float | None = None,
        speed: float = 1.0,
        language: str = "",
        sample_rate: int = 24000,
        timeout_s: float = 20.0,
        block_ms: int = 100,
        player: AudioPlayer | None = None,
        client: Any = None,
    ) -> None:
        super().__init__(sample_rate=sample_rate, timeout_s=timeout_s, block_ms=block_ms,
                         player=player, client=client)
        self.api_key = api_key.strip()
        self.voice_id = voice_id.strip()
        self.model = model.strip() or DEFAULT_MODEL
        self.base_url = (base_url.strip() or DEFAULT_BASE_URL).rstrip("/")
        self.stability = stability
        self.similarity_boost = similarity_boost
        self.style = style
        self.speed = speed
        self.language = language.strip()

    def check_config(self) -> None:
        if not self.api_key:
            raise ElevenLabsError(f"falta a ELEVENLABS_API_KEY no .env (crie uma em {KEYS_URL})")
        if not self.voice_id:
            raise ElevenLabsError(f"falta o ELEVENLABS_VOICE_ID no .env (escolha uma voz em {VOICES_URL})")
        if self.sample_rate not in PCM_RATES:
            rates = ", ".join(map(str, PCM_RATES))
            raise ElevenLabsError(f"ELEVENLABS_SAMPLE_RATE={self.sample_rate} não existe; use uma destas: {rates}")

    def describe(self) -> dict[str, Any]:
        return {"model": self.model, "voice": self.voice_id}

    def explain_error(self, status: int, body: str) -> str:
        return explain_error(status, body)

    @property
    def url(self) -> str:
        voice = quote(self.voice_id, safe="")
        return f"{self.base_url}/v1/text-to-speech/{voice}/stream?output_format=pcm_{self.sample_rate}"

    def request_body(self, text: str) -> dict[str, Any]:
        body: dict[str, Any] = {"text": text, "model_id": self.model}
        if self.language:
            # Só os modelos flash/turbo v2.5 e v3 aceitam forçar o idioma.
            body["language_code"] = self.language
        settings: dict[str, Any] = {}
        if self.stability is not None:
            settings["stability"] = self.stability
        if self.similarity_boost is not None:
            settings["similarity_boost"] = self.similarity_boost
        if self.style is not None:
            settings["style"] = self.style
        if self.speed != 1.0:
            settings["speed"] = self.speed
        if settings:
            body["voice_settings"] = settings
        return body

    def request(self, text: str) -> tuple[str, dict[str, Any], dict[str, str]]:
        return self.url, self.request_body(text), {"xi-api-key": self.api_key}
