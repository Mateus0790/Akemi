"""Fala → texto.

- :class:`SpeechSegmenter`: junta frames de 20 ms em trechos de fala usando o
  VAD (com folga antes/depois para não cortar palavras). Puro, testável.
- :class:`SharedTranscriber`: um Whisper para o mic e o áudio do PC, com a
  fala do dono na frente e o áudio do PC só quando o Whisper está livre.
- :class:`WhisperTranscriber`: faster-whisper rodando numa thread, para não
  travar o loop de eventos. Dependência opcional (extra ``stt``).
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Callable

import numpy as np
import structlog

logger = structlog.get_logger()

FRAME_MS = 20


class SpeechSegmenter:
    def __init__(
        self,
        is_speech: Callable[[bytes], bool],
        *,
        start_frames: int = 6,  # 120 ms de fala para começar
        end_silence_ms: int = 700,
        preroll_ms: int = 300,
        max_segment_s: float = 15.0,
    ) -> None:
        self.is_speech = is_speech
        self.start_frames = start_frames
        self.end_silence_frames = end_silence_ms // FRAME_MS
        self.max_frames = int(max_segment_s * 1000 / FRAME_MS)
        self._preroll: deque[bytes] = deque(maxlen=preroll_ms // FRAME_MS)
        self._voiced_run = 0
        self._silence_run = 0
        self._segment: list[bytes] | None = None

    @property
    def in_speech(self) -> bool:
        return self._segment is not None

    def push(self, frame: bytes) -> bytes | None:
        """Recebe um frame; devolve um segmento completo (PCM16) quando a fala termina."""
        speech = self.is_speech(frame)
        if self._segment is None:
            self._preroll.append(frame)
            self._voiced_run = self._voiced_run + 1 if speech else 0
            if self._voiced_run >= self.start_frames:
                self._segment = list(self._preroll)
                self._preroll.clear()
                self._silence_run = 0
            return None

        self._segment.append(frame)
        self._silence_run = 0 if speech else self._silence_run + 1
        if self._silence_run >= self.end_silence_frames or len(self._segment) >= self.max_frames:
            seg = b"".join(self._segment)
            self._segment = None
            self._voiced_run = 0
            return seg
        return None


class Transcript(str):
    """Texto transcrito com a confiança do Whisper.

    É uma ``str`` para quem só quer o texto; o listener do microfone usa a
    confiança para descartar ruído transcrito como frase ("de sésar.").
    ``queued_s``/``took_s`` dizem quanto tempo esperou a vez e quanto o
    Whisper levou (o cronômetro da resposta usa).
    """

    avg_logprob: float
    no_speech_prob: float
    queued_s: float
    took_s: float

    def __new__(cls, text: str, avg_logprob: float = 0.0, no_speech_prob: float = 0.0) -> "Transcript":
        obj = super().__new__(cls, text)
        obj.avg_logprob = avg_logprob
        obj.no_speech_prob = no_speech_prob
        obj.queued_s = 0.0
        obj.took_s = 0.0
        return obj


class SharedTranscriber:
    """Um Whisper só para o microfone e o áudio do PC, com a sua voz na frente.

    - :meth:`transcribe` (microfone do dono): espera a vez, mas o áudio do PC
      nunca entra na frente dela.
    - :meth:`transcribe_background` (áudio do PC): só roda se o Whisper estiver
      livre e ninguém esperando; senão devolve ``None`` e o trecho é descartado.
      Um trecho do PC que já começou não dá para interromper (até 15 s de
      áudio), por isso o :class:`AmbientAudioInput` nem começa um enquanto você fala.

    A cada ``report_every_s`` loga quanto do tempo o Whisper ficou ocupado
    (``whisper load``): é a parte da Akemi que mais pesa na CPU.
    """

    def __init__(
        self,
        run_sync: Callable[[bytes], str],
        *,
        clock: Callable[[], float] = time.monotonic,
        report_every_s: float = 60.0,
    ) -> None:
        self._run = run_sync
        self._lock = asyncio.Lock()
        self._owner_waiting = 0
        self._clock = clock
        self.report_every_s = report_every_s
        self._reset_window(clock())

    def _reset_window(self, now: float) -> None:
        self._window_start = now
        self._busy = {"mic": 0.0, "pc": 0.0}
        self._count = {"mic": 0, "pc": 0}
        self._skipped = 0

    @property
    def busy(self) -> bool:
        """Transcrevendo agora, ou com a fala do dono esperando a vez."""
        return self._lock.locked() or self._owner_waiting > 0

    async def transcribe(self, pcm16: bytes) -> Transcript:
        asked = self._clock()
        self._owner_waiting += 1
        try:
            await self._lock.acquire()
        finally:
            self._owner_waiting -= 1
        try:
            return await self._timed(pcm16, "mic", asked)
        finally:
            self._lock.release()

    async def transcribe_background(self, pcm16: bytes) -> Transcript | None:
        if self.busy:
            self._skipped += 1
            return None
        async with self._lock:  # livre: pega na hora, sem fila
            return await self._timed(pcm16, "pc", self._clock())

    async def _timed(self, pcm16: bytes, who: str, asked: float) -> Transcript:
        start = self._clock()
        result = await asyncio.to_thread(self._run, pcm16)
        took = self._clock() - start
        if not isinstance(result, Transcript):
            result = Transcript(str(result))
        result.queued_s = start - asked
        result.took_s = took
        self._account(who, took)
        return result

    def load_report(self, now: float | None = None) -> dict[str, float | int]:
        now = self._clock() if now is None else now
        elapsed = max(now - self._window_start, 1e-9)
        return {
            "busy": sum(self._busy.values()) / elapsed,
            "mic_s": self._busy["mic"],
            "mic_segments": self._count["mic"],
            "pc_s": self._busy["pc"],
            "pc_segments": self._count["pc"],
            "pc_skipped": self._skipped,
            "window_s": elapsed,
        }

    def _account(self, who: str, took: float) -> None:
        self._busy[who] += took
        self._count[who] += 1
        now = self._clock()
        if now - self._window_start < self.report_every_s:
            return
        r = self.load_report(now)
        logger.info(
            "whisper load",
            busy=f"{r['busy']:.0%}",
            mic=f"{r['mic_segments']} trechos, {r['mic_s']:.1f}s",
            pc=f"{r['pc_segments']} trechos, {r['pc_s']:.1f}s",
            pc_skipped=r["pc_skipped"],
            window_s=round(r["window_s"]),
        )
        self._reset_window(now)


class WhisperTranscriber:
    def __init__(
        self,
        model_size: str = "small",
        language: str = "pt",
        device: str = "auto",
        initial_prompt: str | None = "Akemi",
        cpu_threads: int = 0,
    ) -> None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover - depende do extra
            raise RuntimeError('Instale o extra de STT: pip install -e ".[stt]"') from exc
        # Na placa de vídeo, pesos int8 com contas em float16 é o mais rápido.
        compute = "int8_float16" if device == "cuda" else "int8"
        self.language = language
        # Dica de vocabulário: ajuda o Whisper a escrever "Akemi" certo.
        self.initial_prompt = initial_prompt
        # cpu_threads=0 é o padrão do faster-whisper (4 threads).
        self._model = WhisperModel(model_size, device=device, compute_type=compute, cpu_threads=cpu_threads)
        # Mic e áudio do sistema dividem o mesmo modelo; a sua voz vai na frente.
        self._shared = SharedTranscriber(self._transcribe_sync)
        logger.info("whisper loaded", model=model_size, device=device, cpu_threads=cpu_threads or "padrão")

    def _transcribe_sync(self, pcm16: bytes) -> Transcript:
        audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = self._model.transcribe(
            audio,
            language=self.language,
            vad_filter=False,
            beam_size=1,
            initial_prompt=self.initial_prompt,
            condition_on_previous_text=False,
        )
        segments = list(segments)
        text = " ".join(s.text.strip() for s in segments).strip()
        if not segments:
            return Transcript(text)
        # Média ponderada pela duração de cada trecho.
        weights = [max(s.end - s.start, 0.01) for s in segments]
        avg = sum(s.avg_logprob * w for s, w in zip(segments, weights)) / sum(weights)
        return Transcript(text, avg, max(s.no_speech_prob for s in segments))

    async def transcribe(self, pcm16: bytes) -> Transcript:
        """Fala do dono: espera a vez, na frente do áudio do PC."""
        return await self._shared.transcribe(pcm16)

    async def transcribe_background(self, pcm16: bytes) -> Transcript | None:
        """Áudio do PC: só se o Whisper estiver livre (``None`` = descartado)."""
        return await self._shared.transcribe_background(pcm16)

    @property
    def busy(self) -> bool:
        return self._shared.busy
