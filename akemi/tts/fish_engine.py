"""Voz principal na nuvem com o Fish Audio (https://fish.audio).

Qualidade de estúdio, voz clonada ou escolhida na biblioteca do Fish Audio, e
o áudio começa a tocar enquanto ainda está chegando. Precisa de internet e de
uma chave da API; se o Fish Audio cair (sem internet, sem créditos, chave
errada), o :class:`~akemi.core.health.ResilientTTS` passa a frase para a voz
local reserva (``TTS_FALLBACK``: Kokoro ou Piper), então nada se perde.

Configuração (``.env``)::

    pip install -e ".[fish]"
    TTS_ENGINE=fish
    FISH_API_KEY=...         # https://fish.audio/app/api-keys
    FISH_VOICE_ID=...        # id da voz: o final do link fish.audio/m/<id>
    # FISH_MODEL=s2.1-pro-free   # grátis, para testar

O Fish Audio devolve PCM 16 bits mono, tocado em streaming pela base
:class:`~akemi.tts.cloud_engine.CloudStreamTTS`.
"""

from __future__ import annotations

import json
from typing import Any

from .cloud_engine import CloudStreamTTS, CloudTTSError
from .piper_engine import AudioPlayer

DEFAULT_BASE_URL = "https://api.fish.audio"
DEFAULT_MODEL = "s2.1-pro"
KEYS_URL = "https://fish.audio/app/api-keys"


class FishAudioError(CloudTTSError):
    """O Fish Audio não falou (chave, créditos, voz inexistente, sem internet...)."""


def explain_error(status: int, body: str) -> str:
    """Transforma a resposta de erro da API numa frase que o dono entende."""
    message = body.strip()
    try:
        data = json.loads(body)
        if isinstance(data, dict):
            message = str(data.get("message") or data.get("detail") or data.get("reason") or message)
    except ValueError:
        pass
    message = message[:200]
    if status == 401:
        return f"o Fish Audio recusou a chave: confira a FISH_API_KEY ({KEYS_URL})"
    if status == 402:
        return "acabaram os créditos do Fish Audio (para testar de graça: FISH_MODEL=s2.1-pro-free)"
    if status == 404:
        return f"o Fish Audio não achou essa voz: confira o FISH_VOICE_ID ({message})"
    if status == 429:
        return "o Fish Audio pediu para ir mais devagar (limite de pedidos)"
    if status >= 500:
        return f"o Fish Audio está fora do ar ou sobrecarregado (HTTP {status})"
    return f"o Fish Audio respondeu HTTP {status}: {message}"


class FishAudioTTS(CloudStreamTTS):
    service = "Fish Audio"
    log_name = "fish"
    extra = "fish"
    error_cls = FishAudioError

    def __init__(
        self,
        api_key: str,
        *,
        voice_id: str = "",
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        latency: str = "balanced",
        speed: float = 1.0,
        volume_db: float = 0.0,
        language: str = "",
        sample_rate: int = 44100,
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
        self.url = (base_url.strip() or DEFAULT_BASE_URL).rstrip("/") + "/v1/tts"
        self.latency = latency
        self.speed = speed
        self.volume_db = volume_db
        self.language = language.strip()

    def check_config(self) -> None:
        if not self.api_key:
            raise FishAudioError(f"falta a FISH_API_KEY no .env (crie uma em {KEYS_URL})")

    def describe(self) -> dict[str, Any]:
        return {"model": self.model, "voice": self.voice_id or "padrão"}

    def explain_error(self, status: int, body: str) -> str:
        return explain_error(status, body)

    def request_body(self, text: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "text": text,
            "format": "pcm",  # PCM16 mono cru: toca sem precisar decodificar
            "sample_rate": self.sample_rate,
            "latency": self.latency,
            "normalize": True,
        }
        if self.voice_id:
            body["reference_id"] = self.voice_id
        if self.language:
            body["language"] = self.language
        if self.speed != 1.0 or self.volume_db != 0.0:
            body["prosody"] = {"speed": self.speed, "volume": self.volume_db}
        return body

    def request(self, text: str) -> tuple[str, dict[str, Any], dict[str, str]]:
        headers = {"Authorization": f"Bearer {self.api_key}", "model": self.model}
        return self.url, self.request_body(text), headers
