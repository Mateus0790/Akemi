"""Os sentidos da Akemi: microfone, áudio do PC e tela.

Ficam separados da mente para rodar em dois lugares: junto com ela (modo
local, ``akemi``) ou no PC do dono conectado a um núcleo remoto (``akemi-corpo``).
Em ambos os casos os sentidos só publicam eventos num barramento.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable

import structlog

from akemi.brain.providers import LLMProvider, build_provider
from akemi.core.config import Settings
from akemi.core.events import EventBus
from akemi.core.pause import PauseSwitch
from akemi.stt.listener import SpeechState

logger = structlog.get_logger()


class Senses:
    def __init__(
        self,
        bus: EventBus,
        settings: Settings,
        speech_state: SpeechState,
        pause: PauseSwitch,
        *,
        vision_fallback: LLMProvider | None = None,
    ) -> None:
        self.bus = bus
        self.settings = settings
        self.speech_state = speech_state
        self.pause = pause
        # Provider para o modelo de visão quando VISION_PROVIDER não é outro.
        self.vision_fallback = vision_fallback
        self.voice_input = None
        self.screen = None
        self._tasks: list[asyncio.Task] = []
        self._stt = None
        self._audio_capture = None
        self._mic = None
        self._screen_grabber = None
        self._vision_provider: LLMProvider | None = None

    def start(self) -> None:
        """Liga o que estiver habilitado no ``settings``. Um sentido que falha
        (sem placa de som, sem Tesseract) não impede os outros."""
        s = self.settings
        for enabled, name, starter in (
            (s.system_audio_enabled, "system audio", self.start_system_audio),
            (s.mic_enabled, "mic", self.start_mic),
            (s.screen_enabled, "screen vision", self.start_screen),
        ):
            if not enabled:
                continue
            try:
                starter()
            except Exception as exc:
                logger.error(f"{name} unavailable", error=str(exc))

    # ------------------------------------------------------------ áudio
    def _transcriber(self):
        if self._stt is None:
            from akemi.stt.transcriber import WhisperTranscriber

            self._stt = WhisperTranscriber(self.settings.whisper_model, self.settings.stt_language)
        return self._stt

    def _frame_bridge(self, name: str, handler) -> Callable[[bytes], None]:
        """Liga a thread do PortAudio ao loop asyncio: devolve o callback de frame
        e cria a task que entrega cada frame a ``handler``."""
        loop = asyncio.get_running_loop()
        frames: asyncio.Queue[bytes] = asyncio.Queue(maxsize=500)

        def put(frame: bytes) -> None:
            if not frames.full():
                frames.put_nowait(frame)

        def on_frame(frame: bytes) -> None:  # thread do PortAudio
            loop.call_soon_threadsafe(put, frame)

        async def pump() -> None:
            while True:
                try:
                    await handler(await frames.get())
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("audio pipeline failed", pipeline=name)

        self._tasks.append(asyncio.create_task(pump(), name=name))
        return on_frame

    def start_system_audio(self) -> None:
        if sys.platform != "win32":
            logger.warning("system audio capture only works on Windows; skipping")
            return
        from akemi.audio.capture import AudioCapture
        from akemi.audio.vad import VAD
        from akemi.stt.listener import AmbientAudioInput

        stt = self._transcriber()
        ambient = AmbientAudioInput(
            self.bus, stt.transcribe, VAD(self.settings.vad_aggressiveness).is_speech,
            self.speech_state, self.pause,
        )
        self._audio_capture = AudioCapture()
        self._audio_capture.start(callback=self._frame_bridge("system-audio", ambient.feed))

    def start_mic(self) -> None:
        from akemi.audio.mic import MicCapture
        from akemi.audio.vad import VAD
        from akemi.stt.listener import OwnerVoiceInput

        s = self.settings
        stt = self._transcriber()
        device = s.mic_device or None
        if device is not None and device.isdigit():
            device = int(device)
        self.voice_input = OwnerVoiceInput(
            self.bus, stt.transcribe, VAD(s.vad_aggressiveness).is_speech,
            self.speech_state, self.pause,
            mode=s.mic_mode,
            wake_words=s.wake_words,
            followup_window_s=s.followup_window_s,
            barge_in_rms=s.barge_in_rms,
        )
        self._mic = MicCapture(device)
        self._mic.start(self._frame_bridge("mic", self.voice_input.feed))

    def mark_exchange(self) -> None:
        """Ela acabou de falar com você: dá para responder sem repetir o nome."""
        if self.voice_input is not None:
            self.voice_input.mark_exchange()

    # ------------------------------------------------------------- visão
    def start_screen(self) -> None:
        from akemi.vision.ocr import TesseractOCR
        from akemi.vision.screenshot import ScreenGrabber, active_window_title
        from akemi.vision.watcher import ScreenWatcher

        s = self.settings
        ocr = TesseractOCR(s.ocr_lang, s.tesseract_cmd or None, s.tessdata_dir)
        describe = None
        if s.vision_model:
            from akemi.vision.describe import ScreenDescriber

            provider_name = s.vision_provider or s.brain_provider
            provider = self.vision_fallback
            if provider is None or provider_name != s.brain_provider:
                provider = self._vision_provider = build_provider(provider_name, s)
            describe = ScreenDescriber(provider, s.vision_model).describe
        self._screen_grabber = ScreenGrabber(s.screen_monitor)
        self.screen = ScreenWatcher(
            self.bus, self._screen_grabber.grab, ocr.read, self.pause,
            window_title=active_window_title,
            describe=describe,
            interval_s=s.screen_interval_s,
            describe_interval_s=s.vision_describe_interval_s,
            change_threshold=s.screen_change_threshold,
            ignored_windows=s.screen_ignore_windows or None,
        )
        self._tasks.append(asyncio.create_task(self.screen.run(), name="screen"))

    async def stop(self) -> None:
        if self._audio_capture is not None:
            self._audio_capture.stop()
        if self._mic is not None:
            self._mic.stop()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self._screen_grabber is not None:
            self._screen_grabber.close()
        if self._vision_provider is not None:
            await self._vision_provider.aclose()
