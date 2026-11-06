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

O Fish Audio devolve PCM 16 bits mono. Cada pedaço que chega vai direto para
a placa de som, em blocos curtos que podem ser interrompidos.
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

import httpx
import structlog

from .base import TTSEngine
from .piper_engine import AudioPlayer, SoundDevicePlayer

logger = structlog.get_logger()

DEFAULT_BASE_URL = "https://api.fish.audio"
DEFAULT_MODEL = "s2.1-pro"
KEYS_URL = "https://fish.audio/app/api-keys"


class FishAudioError(RuntimeError):
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


class FishAudioTTS(TTSEngine):
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
        self.api_key = api_key.strip()
        self.voice_id = voice_id.strip()
        self.model = model.strip() or DEFAULT_MODEL
        self.url = (base_url.strip() or DEFAULT_BASE_URL).rstrip("/") + "/v1/tts"
        self.latency = latency
        self.speed = speed
        self.volume_db = volume_db
        self.language = language.strip()
        self.sample_rate = sample_rate
        self.timeout_s = timeout_s
        # Junta o que chega da rede em blocos de ~100 ms antes de tocar.
        self._block = max(2, sample_rate * 2 * block_ms // 1000)
        self._client = client
        self._player = player
        self._cancel = threading.Event()
        self._lock = threading.Lock()  # uma fala por vez na placa de som

    # ------------------------------------------------------------ carga
    def load(self) -> None:
        """Confere a chave e prepara a conexão e a saída de áudio. Não gasta créditos."""
        if not self.api_key:
            raise FishAudioError(f"falta a FISH_API_KEY no .env (crie uma em {KEYS_URL})")
        if self._client is None:
            self._client = httpx.Client(timeout=httpx.Timeout(self.timeout_s, connect=5.0))
            logger.info("fish audio voice ready", model=self.model, voice=self.voice_id or "padrão")
        if self._player is None:
            try:
                self._player = SoundDevicePlayer()
            except RuntimeError as exc:
                raise RuntimeError('Instale o extra do Fish Audio: pip install -e ".[fish]"') from exc

    async def warmup(self) -> None:
        await asyncio.to_thread(self.load)

    # ------------------------------------------------------------- fala
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

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "model": self.model}

    def _play(self, pcm: bytearray) -> bool:
        """Toca as amostras inteiras de ``pcm`` e deixa o byte que sobrar. True = tocou algo."""
        whole = len(pcm) - len(pcm) % 2
        if whole == 0:
            return False
        self._player.play(bytes(pcm[:whole]), self.sample_rate, 1, self._cancel)
        del pcm[:whole]
        return True

    def _speak_sync(self, text: str) -> None:
        self.load()
        with self._lock:
            self._cancel.clear()
            played = False
            try:
                with self._client.stream("POST", self.url, json=self.request_body(text),
                                         headers=self._headers()) as response:
                    if response.status_code >= 400:
                        response.read()
                        raise FishAudioError(explain_error(response.status_code, response.text))
                    pending = bytearray()
                    for data in response.iter_bytes():
                        if self._cancel.is_set():
                            return  # fechar o stream corta o download também
                        pending += data
                        if len(pending) >= self._block:
                            played |= self._play(pending)
                    if not self._cancel.is_set():
                        played |= self._play(pending)
            except httpx.HTTPError as exc:
                if not played:
                    raise FishAudioError(f"sem conexão com o Fish Audio: {type(exc).__name__}: {exc}") from exc
                # A frase já começou a sair na voz principal: repetir inteira na
                # reserva soaria estranho. Fica cortada e a próxima tenta de novo.
                logger.warning("fish audio stream cut mid-speech", error=str(exc))

    async def speak(self, text: str) -> None:
        try:
            await asyncio.to_thread(self._speak_sync, text)
        except asyncio.CancelledError:
            self._cancel.set()
            raise

    async def stop(self) -> None:
        self._cancel.set()
