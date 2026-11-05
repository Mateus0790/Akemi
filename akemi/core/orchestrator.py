"""Monta e roda todos os módulos da Akemi."""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import structlog

from akemi.actions.builtin import register_builtin_actions
from akemi.actions.registry import ActionRegistry
from akemi.actions.reminders import Reminders, register_reminder_actions
from akemi.brain.mind import Mind, Models
from akemi.brain.persona import Persona
from akemi.brain.providers import FallbackProvider, LLMProvider, build_brains
from akemi.brain.self_improve import Evolution, Proposal
from akemi.brain.state import InnerState
from akemi.brain.triggers import TriggerSet
from akemi.core.config import Settings
from akemi.core.events import Event, EventBus, Priority, Trust
from akemi.core.health import Health, ResilientTTS, supervise
from akemi.core.pause import PauseSwitch
from akemi.core.senses import Senses
from akemi.live.chat import ChatBatcher, ChatMessage
from akemi.safety.moderation import Moderator
from akemi.storage.memory import Memory
from akemi.tts.base import ConsoleTTS, Speaker, TTSEngine

logger = structlog.get_logger()


def resolve_persona_file(settings: Settings) -> Path:
    """Uma persona vinda de um chip (``data/persona.yaml``) vale mais que a do repositório."""
    installed = settings.data_dir / "persona.yaml"
    return installed if installed.exists() else settings.persona_file


def default_body_name(settings: Settings) -> str:
    return (settings.body_name or socket.gethostname() or "corpo").strip()[:40]


def local_senses(settings: Settings, tts: TTSEngine, desktop_actions: bool) -> list[str]:
    senses = []
    if not isinstance(tts, ConsoleTTS):
        senses.append("voz")
    if settings.mic_enabled:
        senses.append("microfone")
    if settings.screen_enabled:
        senses.append("tela")
    if settings.system_audio_enabled:
        senses.append("audio_pc")
    if desktop_actions:
        senses.append("acoes")
    return senses


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

            return ResilientTTS(KokoroTTS(
                settings.kokoro_model_path,
                settings.kokoro_voices_path,
                voice=settings.kokoro_voice,
                speed=settings.kokoro_speed,
                player=player,
            ))
        from akemi.tts.piper_engine import PiperTTS

        return ResilientTTS(PiperTTS(
            settings.piper_model_path,
            speaker_id=settings.piper_speaker_id,
            length_scale=settings.piper_length_scale,
            volume=settings.piper_volume,
            player=player,
        ))
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
        desktop_actions: bool = True,
    ) -> None:
        self.settings = settings
        self.bus = EventBus()
        self.health = Health(self.bus)
        self.pause = PauseSwitch()
        data = settings.data_dir
        self.state_path = data / "state.json" if persist else None
        self.state = InnerState.load(self.state_path) if self.state_path else InnerState()
        self.memory = memory or Memory(data / "memory.sqlite3" if persist else ":memory:")
        self.moderator = Moderator.from_file(data / "blocklist.txt")

        if fast is None:
            fast, built_deep = build_brains(settings, on_switch=self._brain_switched("cerebro"))
            if built_deep is not fast and isinstance(built_deep, FallbackProvider):
                built_deep.on_switch = self._brain_switched("cerebro_profundo")
            deep = deep or built_deep
        deep = deep or fast
        self.models = Models(fast, settings.fast_model, deep, settings.deep_model, settings.llm_temperature)

        self.actions = ActionRegistry()
        register_builtin_actions(self.actions, self.memory, self.state)
        self.reminders = Reminders(self._remind)
        register_reminder_actions(self.actions, self.reminders)
        if desktop_actions:
            from akemi.actions.desktop import register_desktop_actions

            register_desktop_actions(self.actions)
        self.allow_initiative()

        self.persist = persist
        self.desktop_actions = desktop_actions
        # Persona base (yaml) + o que ela aprendeu e você aprovou.
        self.base_persona = Persona.load(resolve_persona_file(settings), owner=settings.owner_name)
        self.evolution = Evolution(
            data / "evolucao.json" if persist else None, self.moderator,
            owner=settings.owner_name, autonomous=settings.self_improve_autonomous,
        )
        # Só o modo local é "corpo" do próprio núcleo; o servidor anuncia o corpo remoto.
        self.local_body = True
        self.phone = None  # CompanionHub, quando o app do celular está liberado

        self.mind = Mind(
            self.bus,
            self.models,
            self.evolution.apply(self.base_persona),
            self.state,
            self.memory,
            self.actions,
            self.pause,
            triggers=TriggerSet.load(),
            owner_name=settings.owner_name,
            min_unprompted_gap_s=settings.min_seconds_between_unprompted_speech,
            state_path=self.state_path,
            health=self.health,
            initiative=settings.initiative_enabled,
        )
        engine = tts or build_tts(settings)
        if isinstance(engine, ResilientTTS) and engine.on_health is None:
            engine.on_health = lambda ok, detail: self.health.report("voz", ok, detail, label="voz")
        self.speaker = Speaker(self.bus, engine, self.moderator)
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

    # ------------------------------------------------------------ autocura
    def allow_initiative(self) -> None:
        """(Re)aplica a lista de iniciativa: chamar de novo depois de registrar ações."""
        self.actions.allow_initiative(
            self.settings.initiative_actions if self.settings.initiative_enabled else [],
            self.settings.initiative_max_per_hour,
        )

    def _brain_switched(self, part: str):
        def on_switch(previous: str, now: str, reason: str) -> None:
            provider = self.models.fast if part == "cerebro" else self.models.deep
            primary = getattr(provider, "primary", now)
            ok = now == primary
            detail = "principal de volta" if ok else f"usando o reserva ({now}); {previous} falhou: {reason[:80]}"
            label = "cérebro" if part == "cerebro" else "cérebro profundo"
            self.health.report(part, ok, detail, label=label)

        return on_switch

    def _supervised(self, name: str, factory, label: str = "") -> asyncio.Task:
        return asyncio.create_task(supervise(name, factory, self.health, label=label), name=name)

    def health_report(self) -> dict:
        report = {"partes": self.health.snapshot()}
        for key, provider in (("cerebro", self.models.fast), ("cerebro_profundo", self.models.deep)):
            if isinstance(provider, FallbackProvider):
                report[key] = provider.status()
        return report

    def describe_health(self) -> str:
        lines = [self.health.describe()]
        if not isinstance(self.models.fast, FallbackProvider):
            lines.append(f"  cérebro: {self.models.fast.name} (sem reserva: veja BRAIN_FALLBACK)")
        for key, provider in (("cérebro", self.models.fast), ("cérebro profundo", self.models.deep)):
            if isinstance(provider, FallbackProvider):
                st = provider.status()
                if key == "cérebro profundo" and provider is self.models.fast:
                    continue
                reserves = ", ".join(
                    f"{b['nome']}{'' if b['ok'] else ' (fora, tenta em ' + str(b['volta_em_s']) + 's)'}"
                    for b in st["cerebros"]
                )
                lines.append(f"  {key}: usando {st['ativo']} | ordem: {reserves}")
        return "\n".join(lines)

    # ----------------------------------------------------------- entradas
    def owner_say(self, text: str, source: str = "console") -> None:
        self.bus.publish(Event("owner.speech_started", source=source, trust=Trust.OWNER, priority=Priority.URGENT))
        self.bus.publish(Event("owner.message", {"text": text}, source=source, trust=Trust.OWNER, priority=Priority.HIGH))

    def _remind(self, text: str) -> None:
        self.bus.publish(
            Event("speech.say", {"text": f"Ei, {self.settings.owner_name}, lembrete: {text}", "origin": "lembrete"},
                  source="lembrete", trust=Trust.SYSTEM, priority=Priority.HIGH)
        )

    def chat_message(self, user: str, text: str, platform: str = "console") -> bool:
        return self.chat.add(ChatMessage(user=user, text=text, platform=platform))

    def observe(self, kind: str, text: str) -> None:
        """Algo percebido (tela/áudio). Sempre PUBLIC: pode conter texto de terceiros."""
        self.bus.publish(Event(f"ambient.{kind}", {"text": text}, source=kind, trust=Trust.PUBLIC, priority=Priority.LOW))

    # ------------------------------------------------------- autoevolução
    def _refresh_persona(self) -> None:
        self.mind.persona = self.evolution.apply(self.base_persona)

    async def evolve(self, force: bool = False) -> list[Proposal]:
        """Ela relê as conversas e muda a si mesma (ou propõe, sem modo autônomo)."""
        made = await self.evolution.reflect(
            self.models.fast, self.models.fast_model, self.memory, self.mind.persona,
            min_conversations=self.settings.self_improve_min_conversations, force=force,
        )
        if not made:
            return made
        if self.evolution.autonomous:
            self._refresh_persona()
        if not force and not self.pause.paused:
            self.bus.publish(Event(
                "speech.say",
                {"text": self._evolution_news(made), "origin": "evolucao"},
                source="evolucao", trust=Trust.SYSTEM, priority=Priority.LOW,
            ))
        return made

    def _evolution_news(self, made: list[Proposal]) -> str:
        """O que ela conta ao dono depois de mudar (lealdade: nada escondido)."""
        if not self.evolution.autonomous:
            return "Pensei numas mudanças em mim. Dá uma olhada quando puder."
        first = made[0]
        what = f": {first.text}" if first.kind != "exemplo" else " no jeito de te responder"
        more = " E mais uma coisinha, tá no histórico." if len(made) > 1 else ""
        return f"Mudei uma coisa em mim{what}.{more} Se não curtir, é só desfazer."

    def decide_evolution(self, action: str, pid: int) -> Proposal:
        """``aprovar`` | ``rejeitar`` | ``desfazer``. Só o dono chama isto."""
        fn = {"aprovar": self.evolution.approve, "rejeitar": self.evolution.reject,
              "desfazer": self.evolution.revert}.get(action)
        if fn is None:
            raise ValueError(f"ação desconhecida: {action}")
        prop = fn(pid)
        self._refresh_persona()
        return prop

    async def _evolution_ticker(self) -> None:
        while True:
            await asyncio.sleep(self.settings.self_improve_interval_s)
            try:
                await self.evolve()
            except Exception as exc:  # nunca derruba a Akemi
                logger.warning("evolution round failed", error=str(exc))

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
        self._tasks.append(self._supervised("chat-batcher", self.chat.run, "leitura do chat"))
        if self.settings.idle_think_interval_s > 0:
            self._tasks.append(self._supervised("idle", self._idle_ticker, "rotina de pensamentos"))
        if self.persist and self.settings.self_improve_interval_s > 0:
            self._tasks.append(self._supervised("evolution", self._evolution_ticker, "rotina de evolução"))
        self._tasks.append(asyncio.create_task(self._watchdog(), name="watchdog"))
        self.senses.start()
        if self.senses.screen is not None:
            screen = self.senses.screen
            self.mind.look = lambda: screen.step(force=True, publish=False)
        if self.senses.voice_input is not None:
            self.bus.subscribe("speech.finished", self._on_speech_finished)
        if self.local_body:
            self.bus.publish(Event(
                "body.connected",
                {"nome": default_body_name(self.settings),
                 "sentidos": local_senses(self.settings, self.speaker.engine, self.desktop_actions)},
                source="local", trust=Trust.SYSTEM,
            ))
        logger.info(
            "akemi started",
            fast=self.models.fast_model,
            deep=self.models.deep_model,
            provider=self.settings.brain_provider,
        )

    async def _watchdog(self, interval_s: float = 15.0) -> None:
        """Confere de tempos em tempos as partes que não são tarefas supervisionadas
        (barramento e fila de voz) e religa o que tiver parado."""
        while True:
            await asyncio.sleep(interval_s)
            try:
                self.check_vitals()
            except Exception:  # o vigia nunca derruba ninguém
                logger.exception("watchdog failed")

    def check_vitals(self) -> None:
        for name, label, task, restart in (
            ("barramento", "sistema nervoso", self.bus._task, self.bus.start),
            ("fala", "fila de fala", self.speaker._task, self.speaker.start),
        ):
            if task is not None and task.done() and not task.cancelled():
                exc = task.exception()
                self.health.report(name, False, f"parou: {exc!r}", label=label)
                new = restart()
                if new not in self._tasks:
                    self._tasks.append(new)
                self.health.report(name, True, "religada", label=label)

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
