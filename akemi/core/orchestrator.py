"""Monta e roda todos os módulos da Akemi."""

from __future__ import annotations

import asyncio
import sys

import structlog

from akemi.actions.builtin import register_builtin_actions
from akemi.actions.registry import ActionRegistry
from akemi.brain.mind import Mind, Models
from akemi.brain.persona import Persona
from akemi.brain.providers import LLMProvider, build_provider
from akemi.brain.state import InnerState
from akemi.brain.triggers import TriggerSet
from akemi.core.config import Settings
from akemi.core.events import Event, EventBus, Priority, Trust
from akemi.core.pause import PauseSwitch
from akemi.live.chat import ChatBatcher, ChatMessage
from akemi.safety.moderation import Moderator
from akemi.storage.memory import Memory
from akemi.tts.base import ConsoleTTS, Speaker, TTSEngine

logger = structlog.get_logger()


def build_tts(settings: Settings) -> TTSEngine:
    if settings.tts_engine == "piper":
        from akemi.tts.piper_engine import PiperTTS, SoundDevicePlayer

        device = settings.tts_output_device or None
        if device is not None and device.isdigit():
            device = int(device)
        try:
            return PiperTTS(
                settings.piper_model_path,
                speaker_id=settings.piper_speaker_id,
                length_scale=settings.piper_length_scale,
                volume=settings.piper_volume,
                player=SoundDevicePlayer(device) if device is not None else None,
            )
        except RuntimeError as exc:
            logger.error("piper unavailable, falling back to console", error=str(exc))
    return ConsoleTTS()


class Akemi:
    def __init__(
        self,
        settings: Settings,
        *,
        fast: LLMProvider | None = None,
        deep: LLMProvider | None = None,
        tts: TTSEngine | None = None,
        memory: Memory | None = None,
        persist: bool = True,
    ) -> None:
        self.settings = settings
        self.bus = EventBus()
        self.pause = PauseSwitch()
        data = settings.data_dir
        self.state_path = data / "state.json" if persist else None
        self.state = InnerState.load(self.state_path) if self.state_path else InnerState()
        self.memory = memory or Memory(data / "memory.sqlite3" if persist else ":memory:")
        self.moderator = Moderator.from_file(data / "blocklist.txt")

        fast = fast or build_provider(settings.brain_provider, settings)
        if deep is None:
            deep = (
                fast
                if settings.effective_deep_provider == settings.brain_provider
                else build_provider(settings.effective_deep_provider, settings)
            )
        self.models = Models(fast, settings.fast_model, deep, settings.deep_model, settings.llm_temperature)

        self.actions = ActionRegistry()
        register_builtin_actions(self.actions, self.memory, self.state)

        self.mind = Mind(
            self.bus,
            self.models,
            Persona.load(settings.persona_file, owner=settings.owner_name),
            self.state,
            self.memory,
            self.actions,
            self.pause,
            triggers=TriggerSet.load(),
            owner_name=settings.owner_name,
            min_unprompted_gap_s=settings.min_seconds_between_unprompted_speech,
            state_path=self.state_path,
        )
        self.speaker = Speaker(self.bus, tts or build_tts(settings), self.moderator)
        self.chat = ChatBatcher(
            self.bus,
            self.moderator,
            name="akemi",
            window_s=settings.chat_batch_window_s,
            max_batches_per_minute=settings.chat_max_replies_per_minute,
        )
        self._tasks: list[asyncio.Task] = []
        self._audio_capture = None
        self._mic = None
        self._stt = None
        self.voice_input = None
        self.screen = None
        self._screen_grabber = None
        self._vision_provider: LLMProvider | None = None

    # ----------------------------------------------------------- entradas
    def owner_say(self, text: str, source: str = "console") -> None:
        self.bus.publish(Event("owner.speech_started", source=source, trust=Trust.OWNER, priority=Priority.URGENT))
        self.bus.publish(Event("owner.message", {"text": text}, source=source, trust=Trust.OWNER, priority=Priority.HIGH))

    def chat_message(self, user: str, text: str, platform: str = "console") -> bool:
        return self.chat.add(ChatMessage(user=user, text=text, platform=platform))

    def observe(self, kind: str, text: str) -> None:
        """Algo percebido (tela/áudio). Sempre PUBLIC: pode conter texto de terceiros."""
        self.bus.publish(Event(f"ambient.{kind}", {"text": text}, source=kind, trust=Trust.PUBLIC, priority=Priority.LOW))

    # ----------------------------------------------------------- ciclo
    async def _idle_ticker(self) -> None:
        while True:
            await asyncio.sleep(self.settings.idle_think_interval_s)
            self.bus.publish(Event("tick.idle", source="timer", priority=Priority.LOW))

    async def start(self) -> None:
        self.mind.register()
        self.speaker.register()
        warmup = getattr(self.speaker.engine, "warmup", None)
        if warmup is not None:
            try:
                await warmup()
            except Exception as exc:
                logger.error("tts failed to load, falling back to console", error=str(exc))
                self.speaker.engine = ConsoleTTS()
        self._tasks += [self.bus.start(), self.speaker.start()]
        self._tasks.append(asyncio.create_task(self.chat.run(), name="chat-batcher"))
        if self.settings.idle_think_interval_s > 0:
            self._tasks.append(asyncio.create_task(self._idle_ticker(), name="idle"))
        if self.settings.twitch_channel:
            from akemi.live.twitch import TwitchChat

            twitch = TwitchChat(
                self.settings.twitch_channel, self.chat,
                self.settings.twitch_nick, self.settings.twitch_oauth_token,
            )
            self._tasks.append(asyncio.create_task(twitch.run(), name="twitch"))
        if self.settings.system_audio_enabled:
            self._start_system_audio()
        if self.settings.mic_enabled:
            try:
                self._start_mic()
            except Exception as exc:
                logger.error("mic unavailable", error=str(exc))
        if self.settings.screen_enabled:
            try:
                self._start_screen()
            except Exception as exc:
                logger.error("screen vision unavailable", error=str(exc))
        logger.info(
            "akemi started",
            fast=self.models.fast_model,
            deep=self.models.deep_model,
            provider=self.settings.brain_provider,
        )

    # ------------------------------------------------------------ áudio
    def _transcriber(self):
        if self._stt is None:
            from akemi.stt.transcriber import WhisperTranscriber

            self._stt = WhisperTranscriber(self.settings.whisper_model, self.settings.stt_language)
        return self._stt

    def _frame_bridge(self, name: str, handler) -> callable:
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

    def _start_system_audio(self) -> None:
        if sys.platform != "win32":
            logger.warning("system audio capture only works on Windows; skipping")
            return
        from akemi.audio.capture import AudioCapture
        from akemi.audio.vad import VAD
        from akemi.stt.listener import AmbientAudioInput

        stt = self._transcriber()
        ambient = AmbientAudioInput(
            self.bus, stt.transcribe, VAD(self.settings.vad_aggressiveness).is_speech,
            self.speaker, self.pause,
        )
        self._audio_capture = AudioCapture()
        self._audio_capture.start(callback=self._frame_bridge("system-audio", ambient.feed))

    def _start_mic(self) -> None:
        from akemi.audio.mic import MicCapture
        from akemi.audio.vad import VAD
        from akemi.stt.listener import OwnerVoiceInput

        stt = self._transcriber()
        device = self.settings.mic_device or None
        if device is not None and device.isdigit():
            device = int(device)
        self.voice_input = OwnerVoiceInput(
            self.bus, stt.transcribe, VAD(self.settings.vad_aggressiveness).is_speech,
            self.speaker, self.pause,
            mode=self.settings.mic_mode,
            wake_words=self.settings.wake_words,
            followup_window_s=self.settings.followup_window_s,
            barge_in_rms=self.settings.barge_in_rms,
        )

        def on_speech_finished(event: Event) -> None:
            # Depois que ela responde a você (ou fala por conta própria), dá
            # para continuar a conversa sem repetir o nome dela.
            if event.payload.get("origin_trust", 0) >= Trust.SYSTEM:
                self.voice_input.mark_exchange()

        self.bus.subscribe("speech.finished", on_speech_finished)
        self._mic = MicCapture(device)
        self._mic.start(self._frame_bridge("mic", self.voice_input.feed))

    # ------------------------------------------------------------- visão
    def _start_screen(self) -> None:
        from akemi.vision.ocr import TesseractOCR
        from akemi.vision.screenshot import ScreenGrabber, active_window_title
        from akemi.vision.watcher import ScreenWatcher

        s = self.settings
        ocr = TesseractOCR(s.ocr_lang, s.tesseract_cmd or None, s.tessdata_dir)
        describe = None
        if s.vision_model:
            from akemi.vision.describe import ScreenDescriber

            provider_name = s.vision_provider or s.brain_provider
            if provider_name != s.brain_provider:
                self._vision_provider = build_provider(provider_name, s)
            describe = ScreenDescriber(self._vision_provider or self.models.fast, s.vision_model).describe
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
        if self._screen_grabber is not None:
            self._screen_grabber.close()
        if self.state_path:
            self.state.save(self.state_path)
        providers = [self.models.fast, self.models.deep, self._vision_provider]
        for p in {id(p): p for p in providers if p is not None}.values():
            await p.aclose()
        self.memory.close()
