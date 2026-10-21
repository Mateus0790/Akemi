"""Monta e roda todos os módulos da Akemi."""

from __future__ import annotations

import asyncio

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
from akemi.core.senses import Senses
from akemi.live.chat import ChatBatcher, ChatMessage
from akemi.safety.moderation import Moderator
from akemi.storage.memory import Memory
from akemi.tts.base import ConsoleTTS, Speaker, TTSEngine

logger = structlog.get_logger()


def build_tts(settings: Settings) -> TTSEngine:
    if settings.tts_engine == "console":
        return ConsoleTTS()
    from akemi.tts.piper_engine import SoundDevicePlayer

    device = settings.tts_output_device or None
    if device is not None and device.isdigit():
        device = int(device)
    try:
        player = SoundDevicePlayer(device) if device is not None else None
        if settings.tts_engine == "kokoro":
            from akemi.tts.kokoro_engine import KokoroTTS

            return KokoroTTS(
                settings.kokoro_model_path,
                settings.kokoro_voices_path,
                voice=settings.kokoro_voice,
                speed=settings.kokoro_speed,
                player=player,
            )
        from akemi.tts.piper_engine import PiperTTS

        return PiperTTS(
            settings.piper_model_path,
            speaker_id=settings.piper_speaker_id,
            length_scale=settings.piper_length_scale,
            volume=settings.piper_volume,
            player=player,
        )
    except RuntimeError as exc:
        logger.error("tts unavailable, falling back to console", engine=settings.tts_engine, error=str(exc))
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
        self.senses = Senses(
            self.bus, settings, self.speaker, self.pause, vision_fallback=self.models.fast
        )

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
        self.senses.start()
        if self.senses.screen is not None:
            screen = self.senses.screen
            self.mind.look = lambda: screen.step(force=True, publish=False)
        if self.senses.voice_input is not None:
            self.bus.subscribe("speech.finished", self._on_speech_finished)
        logger.info(
            "akemi started",
            fast=self.models.fast_model,
            deep=self.models.deep_model,
            provider=self.settings.brain_provider,
        )

    def _on_speech_finished(self, event: Event) -> None:
        # Depois que ela responde a você (ou fala por conta própria), dá
        # para continuar a conversa sem repetir o nome dela.
        if event.payload.get("origin_trust", 0) >= Trust.SYSTEM:
            self.senses.mark_exchange()

    @property
    def screen(self):
        return self.senses.screen

    @property
    def voice_input(self):
        return self.senses.voice_input

    async def stop(self) -> None:
        await self.senses.stop()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self.state_path:
            self.state.save(self.state_path)
        for p in {id(self.models.fast): self.models.fast, id(self.models.deep): self.models.deep}.values():
            await p.aclose()
        self.memory.close()
