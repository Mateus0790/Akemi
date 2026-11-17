"""Base das vozes na nuvem (Fish Audio e ElevenLabs).

As duas pedem a fala por HTTP e recebem PCM 16 bits mono em streaming. Cada
pedaço que chega vai direto para a placa de som, em blocos curtos que podem
ser interrompidos. Se a nuvem falhar antes do primeiro som, o erro sobe e o
:class:`~akemi.core.health.ResilientTTS` passa a frase para a voz reserva.
Cada voz só diz como montar o pedido e como explicar os erros da sua API.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import httpx
import structlog

from .base import TTSEngine
from .piper_engine import AudioPlayer, SoundDevicePlayer

logger = structlog.get_logger()


class CloudTTSError(RuntimeError):
    """A voz na nuvem não falou (chave, créditos, voz inexistente, sem internet...)."""


class CloudStreamTTS(TTSEngine):
    #: nome que aparece nos erros e nos logs ("Fish Audio", "ElevenLabs")
    service = "nuvem"
    #: prefixo do log de latência (``<log_name> first audio``)
    log_name = "cloud"
    #: extra do pip que traz a saída de áudio
    extra = "fish"
    error_cls: type[CloudTTSError] = CloudTTSError

    def __init__(
        self,
        *,
        sample_rate: int,
        timeout_s: float = 20.0,
        block_ms: int = 100,
        player: AudioPlayer | None = None,
        client: Any = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.timeout_s = timeout_s
        # Junta o que chega da rede em blocos de ~100 ms antes de tocar.
        self._block = max(2, sample_rate * 2 * block_ms // 1000)
        self._client = client
        self._player = player
        self._cancel = threading.Event()
        self._lock = threading.Lock()  # uma fala por vez na placa de som

    # ------------------------------------------------- o que cada voz define
    def check_config(self) -> None:
        """Levanta ``error_cls`` se faltar algo no .env (chave, voz...)."""

    def describe(self) -> dict[str, Any]:
        """O que vai no log quando a voz fica pronta."""
        return {}

    def request(self, text: str) -> tuple[str, dict[str, Any], dict[str, str]]:
        """(url, corpo JSON, cabeçalhos) do pedido de fala."""
        raise NotImplementedError

    def explain_error(self, status: int, body: str) -> str:
        return f"{self.service} respondeu HTTP {status}: {body.strip()[:200]}"

    # ------------------------------------------------------------ carga
    def load(self) -> None:
        """Confere a configuração e prepara a conexão e a saída de áudio. Não gasta créditos."""
        self.check_config()
        if self._client is None:
            self._client = httpx.Client(timeout=httpx.Timeout(self.timeout_s, connect=5.0))
            logger.info(f"{self.log_name} voice ready", **self.describe())
        if self._player is None:
            try:
                self._player = SoundDevicePlayer()
            except RuntimeError as exc:
                raise RuntimeError(f'Instale o extra do {self.service}: pip install -e ".[{self.extra}]"') from exc

    async def warmup(self) -> None:
        await asyncio.to_thread(self.load)

    # ------------------------------------------------------------- fala
    def _play(self, pcm: bytearray) -> bool:
        """Toca as amostras inteiras de ``pcm`` e deixa o byte que sobrar. True = tocou algo."""
        whole = len(pcm) - len(pcm) % 2
        if whole == 0:
            return False
        self._player.play(bytes(pcm[:whole]), self.sample_rate, 1, self._cancel)
        del pcm[:whole]
        return True

    def _play_first(self, pcm: bytearray, played: bool, asked: float) -> bool:
        """Toca; na primeira vez, loga quanto a nuvem levou até o primeiro som."""
        if not played and len(pcm) >= 2:
            logger.info(f"{self.log_name} first audio", ms=round((time.monotonic() - asked) * 1000))
        return self._play(pcm)

    def _speak_sync(self, text: str) -> None:
        self.load()
        url, body, headers = self.request(text)
        with self._lock:
            self._cancel.clear()
            played = False
            asked = time.monotonic()
            try:
                with self._client.stream("POST", url, json=body, headers=headers) as response:
                    if response.status_code >= 400:
                        response.read()
                        raise self.error_cls(self.explain_error(response.status_code, response.text))
                    pending = bytearray()
                    for data in response.iter_bytes():
                        if self._cancel.is_set():
                            return  # fechar o stream corta o download também
                        pending += data
                        if len(pending) >= self._block:
                            played |= self._play_first(pending, played, asked)
                    if not self._cancel.is_set():
                        played |= self._play_first(pending, played, asked)
            except httpx.HTTPError as exc:
                if not played:
                    raise self.error_cls(f"sem conexão com o {self.service}: {type(exc).__name__}: {exc}") from exc
                # A frase já começou a sair na voz principal: repetir inteira na
                # reserva soaria estranho. Fica cortada e a próxima tenta de novo.
                logger.warning(f"{self.log_name} stream cut mid-speech", error=str(exc))

    async def speak(self, text: str) -> None:
        try:
            await asyncio.to_thread(self._speak_sync, text)
        except asyncio.CancelledError:
            self._cancel.set()
            raise

    async def stop(self) -> None:
        self._cancel.set()
