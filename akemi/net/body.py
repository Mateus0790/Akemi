"""Corpo da Akemi: roda no PC do dono e conecta ao núcleo (servidor).

O corpo não pensa. Ele ouve (microfone, áudio do PC), vê (tela) e fala (Piper):

    akemi-corpo --servidor ws://192.168.0.10:8765 --voz --mic --tela

Tudo que os sentidos percebem vai para o núcleo; o que o núcleo manda falar
toca aqui. Se a conexão cai, ele tenta de novo sozinho.

No terminal do corpo:
    qualquer texto      fala com ela (como dono)
    /olhar              faz ela olhar a tela agora (com --tela)
    /pausar | /voltar   pausa/retoma a autonomia dela
    /sair
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import sys
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from akemi.actions.desktop import PC_ACTIONS, register_desktop_actions
from akemi.actions.registry import ActionCall, ActionRegistry
from akemi.core.config import Settings
from akemi.core.events import Event, EventBus
from akemi.core.pause import PauseSwitch
from akemi.core.senses import Senses
from akemi.tts.base import TTSEngine

from . import protocol as proto

logger = structlog.get_logger()

OnSpoken = Callable[[int, str], Awaitable[None] | None]


class BodyVoice:
    """Toca as falas que o núcleo manda, uma por vez, e avisa quando termina.

    Também é o "estado de fala" que os sentidos consultam: enquanto ela fala,
    o áudio do PC não é transcrito e o microfone ignora o eco da voz dela.
    """

    def __init__(self, engine: TTSEngine, on_spoken: OnSpoken) -> None:
        self.engine = engine
        self.on_spoken = on_spoken
        self._queue: asyncio.Queue[tuple[int, str]] = asyncio.Queue()
        self._recent: deque[tuple[float, str]] = deque(maxlen=10)
        self._current: asyncio.Task | None = None
        self._task: asyncio.Task | None = None
        self.spoken: list[str] = []

    def enqueue(self, sid: int, text: str) -> None:
        self._queue.put_nowait((sid, text))

    async def _notify(self, sid: int, status: str) -> None:
        result = self.on_spoken(sid, status)
        if inspect.isawaitable(result):
            await result

    async def stop_all(self) -> None:
        """Para a fala atual e descarta a fila (interrompida pelo dono/núcleo)."""
        dropped = []
        while not self._queue.empty():
            dropped.append(self._queue.get_nowait()[0])
        if self._current and not self._current.done():
            self._current.cancel()
        await self.engine.stop()
        for sid in dropped:
            await self._notify(sid, "interrupted")

    async def run(self) -> None:
        while True:
            sid, text = await self._queue.get()
            self._recent.append((time.time(), text))
            self._current = asyncio.create_task(self.engine.speak(text))
            status = "done"
            try:
                await self._current
                self.spoken.append(text)
            except asyncio.CancelledError:
                if self._task is not None and self._task.cancelling():
                    raise
                status = "interrupted"
            except Exception:
                status = "error"
                logger.exception("tts failed")
            self._recent.append((time.time(), text))  # fim da fala (janela de eco)
            await self._notify(sid, status)

    @property
    def speaking(self) -> bool:
        return self._current is not None and not self._current.done()

    @property
    def recent_speech(self) -> list[tuple[float, str]]:
        return list(self._recent)

    def start(self) -> asyncio.Task:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name="body-voice")
        return self._task

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


class TokenRejected(RuntimeError):
    pass


class Body:
    def __init__(
        self,
        settings: Settings,
        url: str,
        token: str,
        engine: TTSEngine,
        *,
        actions: ActionRegistry | None = None,
    ) -> None:
        self.settings = settings
        self.url = url
        self.token = token
        # Ações do PC que o núcleo pode pedir (só as de PC_ACTIONS são aceitas).
        if actions is None:
            actions = ActionRegistry()
            register_desktop_actions(actions)
        self.actions = actions
        self._action_tasks: set[asyncio.Task] = set()
        self.bus = EventBus()
        self.pause = PauseSwitch()
        self.voice = BodyVoice(engine, self._on_spoken)
        self.senses = Senses(self.bus, settings, self.voice, self.pause)
        self.conn: Any = None
        self.connected = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

        # O que os sentidos percebem vai para o núcleo.
        self.bus.subscribe("owner.message", self._fwd_owner_message)
        self.bus.subscribe("owner.speech_started", self._fwd_speech_started)
        self.bus.subscribe("ambient.*", self._fwd_ambient)

    # -------------------------------------------------------------- envio
    async def send(self, type_: str, **fields: Any) -> bool:
        conn = self.conn
        if conn is None:
            if type_ == "owner_message":
                logger.warning("not connected to the core; message lost", text=fields.get("text"))
            return False
        try:
            await conn.send(proto.encode(type_, **fields))
            return True
        except Exception as exc:
            logger.warning("send to core failed", error=str(exc))
            return False

    async def _on_spoken(self, sid: int, status: str) -> None:
        await self.send("spoken", id=sid, status=status)

    async def _fwd_owner_message(self, event: Event) -> None:
        await self.send("owner_message", text=event.text)

    async def _fwd_speech_started(self, event: Event) -> None:
        await self.voice.stop_all()  # para na hora, sem esperar a rede
        await self.send("owner_speech_started")

    async def _fwd_ambient(self, event: Event) -> None:
        await self.send("ambient", kind=event.kind.split(".", 1)[1], text=event.text)

    async def owner_say(self, text: str) -> None:
        """Texto digitado no terminal do corpo."""
        await self.voice.stop_all()
        await self.send("owner_message", text=text)

    # ------------------------------------------------------------ recepção
    async def handle(self, msg: dict) -> None:
        kind = msg["type"]
        if kind == "say":
            if isinstance(msg.get("id"), int) and msg.get("text"):
                self.voice.enqueue(msg["id"], msg["text"])
        elif kind == "stop":
            await self.voice.stop_all()
        elif kind == "exchange":
            self.senses.mark_exchange()
        elif kind == "action":
            # Em task própria: abrir programa pode levar ~1 s e o loop de
            # mensagens (fala, interrupção) não pode esperar.
            task = asyncio.create_task(self._run_action(msg))
            self._action_tasks.add(task)
            task.add_done_callback(self._action_tasks.discard)
        elif kind in ("welcome", "paused"):
            if msg.get("paused"):
                self.pause.pause("núcleo")
            else:
                self.pause.resume()
        else:
            logger.warning("unknown message from core", type=kind)

    async def _run_action(self, msg: dict) -> None:
        aid, name, args = msg.get("id"), str(msg.get("name", "")), msg.get("args")
        if name not in PC_ACTIONS or not isinstance(args, dict):
            # O núcleo só pede ações do PC. Qualquer outra coisa é recusada aqui,
            # mesmo vinda de um núcleo autenticado.
            logger.warning("action refused by body", action=name)
            await self.send("action_result", id=aid, status="denied", detail="ação não permitida no PC")
            return
        result = await self.actions.execute_authorized(ActionCall(name, args))
        logger.info("action executed for core", action=name, status=result.status)
        value = result.value if isinstance(result.value, (str, int, float, bool)) or result.value is None else str(result.value)
        await self.send("action_result", id=aid, status=result.status, detail=result.detail, value=value)

    async def session(self, conn: Any) -> None:
        """Uma conexão: autentica e processa mensagens até cair."""
        await conn.send(proto.encode("hello", token=self.token, version=proto.VERSION))
        self.conn = conn
        self.connected.set()
        try:
            async for raw in conn:
                try:
                    await self.handle(proto.decode(raw))
                except proto.ProtocolError as exc:
                    logger.warning("bad message from core", error=str(exc))
        except Exception as exc:  # fechamento com código de erro (ex.: token recusado)
            logger.info("connection to core ended", error=str(exc))
        finally:
            self.conn = None
            self.connected.clear()
            await self.voice.stop_all()  # sem núcleo, sem fala pela metade
        if getattr(conn, "close_code", None) == proto.CLOSE_UNAUTHORIZED:
            raise TokenRejected("o núcleo recusou o AKEMI_TOKEN")

    async def connect_forever(self) -> None:
        from websockets.asyncio.client import connect

        delay = 1.0
        while True:
            try:
                async with connect(self.url, proxy=None, open_timeout=10) as conn:
                    logger.info("connected to core", url=self.url)
                    delay = 1.0
                    await self.session(conn)
                logger.warning("core closed the connection")
            except TokenRejected:
                raise
            except Exception as exc:
                logger.warning("core unreachable", url=self.url, error=str(exc), retry_in=delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)

    # -------------------------------------------------------------- ciclo
    async def start(self) -> None:
        warmup = getattr(self.voice.engine, "warmup", None)
        if warmup is not None:
            try:
                await warmup()
            except Exception as exc:
                from akemi.tts.base import ConsoleTTS

                logger.error("tts failed to load, falling back to console", error=str(exc))
                self.voice.engine = ConsoleTTS()
        self._tasks += [self.bus.start(), self.voice.start()]
        self.senses.start()

    async def stop(self) -> None:
        await self.senses.stop()
        await self.voice.stop()
        for t in [*self._tasks, *self._action_tasks]:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self.conn is not None:
            await self.conn.close()


async def _ainput(prompt: str) -> str:
    return await asyncio.to_thread(input, prompt)


async def console(body: Body) -> None:
    print("Corpo da Akemi. Digite para falar com ela, /sair para encerrar.\n")
    while True:
        try:
            line = (await _ainput("você> ")).strip()
        except EOFError:
            return
        if not line:
            continue
        if line in ("/sair", "/exit", "/quit"):
            return
        if line == "/ajuda":
            print(__doc__)
        elif line == "/olhar":
            if body.senses.screen is None:
                print("  (visão desligada: rode com --tela)")
                continue
            seen = await body.senses.screen.step(force=True)
            print(f"  (viu: {seen})" if seen else "  (nada legível na tela)")
        elif line == "/pausar":
            await body.send("pause")
        elif line == "/voltar":
            await body.send("resume")
        else:
            if not body.connected.is_set():
                print("  (sem conexão com o núcleo)")
                continue
            await body.owner_say(line)


async def run(settings: Settings, url: str, interactive: bool = True) -> None:
    from akemi.core.orchestrator import build_tts

    body = Body(settings, url, settings.akemi_token, build_tts(settings))
    await body.start()
    link = asyncio.create_task(body.connect_forever(), name="core-link")
    try:
        if interactive:
            ui = asyncio.create_task(console(body), name="console")
            done, _ = await asyncio.wait({ui, link}, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                if t is link and t.exception():
                    print(f"\n{t.exception()}", file=sys.stderr)
            ui.cancel()
        else:
            await link
    finally:
        link.cancel()
        await asyncio.gather(link, return_exceptions=True)
        await body.stop()


def main() -> None:
    from akemi.core.logging_setup import configure_logging

    parser = argparse.ArgumentParser(description="Corpo da Akemi (conecta ao núcleo)")
    parser.add_argument("--servidor", help="endereço do núcleo, ex. ws://192.168.0.10:8765")
    parser.add_argument("--voz", action="store_true", help="fala em voz alta (voz do TTS_ENGINE; padrão Piper)")
    parser.add_argument("--mic", action="store_true", help="ouve você pelo microfone")
    parser.add_argument("--tela", action="store_true", help="olha a tela (OCR)")
    parser.add_argument("--audio-pc", action="store_true", help="ouve o áudio do PC")
    parser.add_argument("--sem-terminal", action="store_true", help="não lê comandos do teclado")
    args = parser.parse_args()

    settings = Settings()
    if args.voz:
        if settings.tts_engine == "console":  # TTS_ENGINE do .env escolhe a voz
            settings.tts_engine = "piper"
    settings.mic_enabled |= args.mic
    settings.screen_enabled |= args.tela
    settings.system_audio_enabled |= args.audio_pc
    url = args.servidor or settings.server_url
    if not url:
        parser.error("informe --servidor ou SERVER_URL no .env")
    if not url.startswith(("ws://", "wss://")):
        url = "ws://" + url
    if not settings.akemi_token:
        parser.error("defina AKEMI_TOKEN no .env (o mesmo do servidor)")
    configure_logging(settings.log_level, settings.log_json)
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
    try:
        asyncio.run(run(settings, url, interactive=not args.sem_terminal))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
