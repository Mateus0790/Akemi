"""Habilidades: ela percebe o que você está usando e cria um jeito de participar.

Exemplo::

    (você abre o Stardew Valley)
    Akemi: "Vi que você abriu o Stardew Valley. Quer que eu crie um jeito de
            participar dele? Fica como rascunho até você revisar. Diz sim ou não."
    Você:  "sim"
    Akemi: "Beleza, vou montar isso em segundo plano enquanto você joga."
    ...
    Akemi: "Pronto, criei a habilidade do Stardew Valley (ações: stardew_valley_status).
            Revisa com /habilidade ver stardew-valley e liga com /habilidade ativar stardew-valley."
    (na próxima vez que você abrir o jogo, ela já está lá)

Os limites, todos em código:

- **Pergunta antes**, uma vez por programa. Disse não? Ela não pergunta de novo
  sozinha (só se você mesmo disser "vou jogar X").
- **Jogos com anti-cheat e programas sensíveis** nunca (``limites.py``).
- **Nada liga sem a sua revisão**: o que ela cria fica como rascunho e só roda
  depois de ``/habilidade ativar`` digitado no console. Por voz não ativa.
- **Plugin separado**: cada habilidade vive em ``data/habilidades/<id>/``, com
  acesso só ao que o :class:`~akemi.habilidades.plugin.PluginContext` dá. O código
  principal dela não muda.
- Até ``HABILIDADES_POR_DIA`` criações em 24 horas (padrão 2).
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from akemi.actions.registry import ActionCall, ActionRegistry, Tier
from akemi.core.events import Event, Trust

from .atividade import CATALOG, ActivityWatcher, Program, from_phrase, known_program
from .construtor import Builder, BuildResult
from .limites import blocked, program_slug
from .plugin import Plugin, PluginContext, PluginError, check_code, load, read_folder

__all__ = ["ActivityWatcher", "Builder", "Habilidades", "Program", "register_skill_actions"]

logger = structlog.get_logger()

STATE_FILE = "estado.json"
ASK_PER_DAY = 3  # perguntas espontâneas por dia, no máximo
STATUS_LABEL = {
    "perguntado": "perguntei e você não quis (ou não respondeu)",
    "criando": "criando agora",
    "rascunho": "rascunho esperando sua revisão",
    "ativa": "ligada",
    "desativada": "desligada",
    "falhou": "tentei e não deu",
}


GAME_FOLDERS = ("steamapps", "epic games", "gog galaxy", "xboxgames", "riot games", "ubisoft", "ea games",
                "games", "jogos", ".minecraft", "itch")


def interesting(program: Program) -> bool:
    """Vale perguntar sozinha? Só para o catálogo e jogos. O resto, só se você disser "vou usar X"."""
    if program.key in CATALOG:
        return True
    exe = program.exe.replace("\\", "/").lower()
    return any(f"/{g}/" in exe or f"/{g}" in exe for g in GAME_FOLDERS)


@dataclass
class Habilidades:
    root: Path
    actions: ActionRegistry
    say: Callable[[str], None]
    observe: Callable[[str, str], None]
    builder: Builder | None = None
    ask: Callable[[ActionCall, str], bool] | None = None  # pergunta espontânea; devolve se perguntou
    blocked_extra: list[str] = field(default_factory=list)
    per_day: int = 2
    ask_unprompted: bool = True
    plugins: dict[str, Plugin] = field(default_factory=dict)
    seen: dict[str, Program] = field(default_factory=dict)  # programas abertos agora
    tasks: list[asyncio.Task] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self._state: dict = self._read_state()

    # ------------------------------------------------------------- estado
    @property
    def state_path(self) -> Path:
        return self.root / STATE_FILE

    def _read_state(self) -> dict:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        raw = raw if isinstance(raw, dict) else {}
        raw.setdefault("programas", {})
        raw.setdefault("criadas", [])
        raw.setdefault("perguntas", [])
        return raw

    def _save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    def status(self, key: str) -> str:
        return str(self._state["programas"].get(key, {}).get("status", ""))

    def _set(self, key: str, status: str, name: str = "", detail: str = "") -> None:
        item = self._state["programas"].setdefault(key, {})
        item.update({"status": status, "em": time.time()})
        if name:
            item["nome"] = name
        if detail:
            item["detalhe"] = detail
        self._save()

    def _recent(self, field_name: str) -> int:
        day = time.time() - 86400
        self._state[field_name] = [t for t in self._state[field_name] if t >= day]
        return len(self._state[field_name])

    # -------------------------------------------------------------- limites
    def why_not(self, program: Program) -> str | None:
        """Por que ela não cria habilidade para esse programa agora (None = pode)."""
        for name in (program.key, program.name):
            if why := blocked(name, self.blocked_extra):
                return why
        if self.builder is None:
            return "a criação de habilidades está desligada"
        if self.status(program.key) == "ativa":
            return f"já tenho uma habilidade ligada para o {program.name}"
        if self.builder.busy or self.status(program.key) == "criando":
            return "já estou criando uma habilidade"
        if self._recent("criadas") >= self.per_day:
            return f"já criei {self.per_day} habilidade(s) nas últimas 24 horas"
        return None

    # ----------------------------------------------------------- programas
    def resolve(self, text: str) -> Program:
        """O programa que ele quis dizer: um aberto agora, um do catálogo ou só o nome."""
        key = program_slug(text)
        for prog in self.seen.values():
            if key in (prog.key, program_slug(prog.name)):
                return prog
        if key in CATALOG:
            return known_program(key)
        return from_phrase(f"vou usar {text}") or Program(key, text.strip().title())

    async def on_open(self, program: Program) -> None:
        self.seen[program.key] = program
        if (plugin := self.plugins.get(program.key)) is not None:
            logger.info("habilidade: program opened", habilidade=program.key)
            await plugin.opened(program)
            return
        if not self.ask_unprompted or self.ask is None or self.status(program.key) or not interesting(program):
            return
        if self.why_not(program) is not None or self._recent("perguntas") >= ASK_PER_DAY:
            return
        call = ActionCall("criar_habilidade", {"programa": program.name})
        question = (f"Vi que você abriu o {program.name}. Quer que eu crie um jeito de participar dele? "
                    "Fica como rascunho até você revisar. Diz sim ou não.")
        if self.ask(call, question):
            self._state["perguntas"].append(time.time())
            self._set(program.key, "perguntado", program.name)

    async def on_close(self, program: Program) -> None:
        self.seen.pop(program.key, None)
        if (plugin := self.plugins.get(program.key)) is not None:
            await plugin.closed()

    async def shortcut(self, text: str, event: Event):
        """ "vou jogar X": se ela ainda não tem nada para o X, oferece criar."""
        program = from_phrase(text)
        if program is None or event.trust < Trust.OWNER or program.key in self.plugins:
            return None
        if self.status(program.key) in ("rascunho", "criando") or self.why_not(program):
            return None
        result = await self.actions.request(ActionCall("criar_habilidade", {"programa": program.name}), event.trust)
        if result.status != "pending_confirmation":
            return None
        return f"Boa! {result.detail}"

    # -------------------------------------------------------------- criar
    def start_build(self, programa: str, pedido: str = "") -> str:
        program = self.resolve(programa)
        if not program.key:
            raise ValueError("não entendi qual programa")
        if why := self.why_not(program):
            raise ValueError(why)
        self._state["criadas"].append(time.time())
        self._set(program.key, "criando", program.name)
        task = asyncio.get_running_loop().create_task(self._build(program, pedido), name=f"criar-{program.key}")
        self.tasks.append(task)
        return f"Beleza, vou montar isso em segundo plano. Te aviso quando a habilidade do {program.name} estiver pronta."

    async def _build(self, program: Program, wish: str) -> BuildResult:
        assert self.builder is not None
        result = await self.builder.build(program, wish)
        if result.status == "rascunho":
            self._set(program.key, "rascunho", program.name, result.summary)
            self.say(f"Pronto, criei a habilidade do {program.name}. Revisa com /habilidade ver {result.id} "
                     f"e liga com /habilidade ativar {result.id}. Depois disso, quando você abrir, eu já tô lá.")
        else:
            self._set(program.key, "falhou", program.name, result.detail)
            self.say(f"Tentei criar a habilidade do {program.name}, mas não deu: {result.detail[:160]}")
        return result

    # --------------------------------------------------- ligar e desligar
    def _context(self, manifest) -> PluginContext:
        folder = self.root / manifest.id / "dados"
        folder.mkdir(parents=True, exist_ok=True)

        def register(action) -> None:
            if self.actions.get(action.name) is not None and not action.name.startswith(manifest.id.replace("-", "_")):
                raise PluginError(f"a ação {action.name} já existe")
            self.actions.add(action)

        return PluginContext(
            manifest, folder, self.seen.get(manifest.programa), register_action=register,
            say=self.say, observe=self.observe,
        )

    def _load(self, pid: str) -> Plugin:
        plugin = load(self.root / pid, self._context)
        self.plugins[plugin.manifest.id] = plugin
        return plugin

    def load_active(self) -> list[str]:
        """Liga as habilidades que você já ativou (ao iniciar)."""
        loaded = []
        for key, item in self._state["programas"].items():
            if item.get("status") != "ativa":
                continue
            try:
                self._load(key)
                loaded.append(key)
            except (PluginError, Exception) as exc:  # um plugin quebrado não derruba ela
                logger.warning("habilidade failed to load", habilidade=key, error=str(exc))
                self._set(key, "desativada", detail=f"não carregou: {exc}"[:300])
        return loaded

    async def activate(self, pid: str) -> str:
        """Só pelo console: digitar ali é você, depois de revisar."""
        pid = program_slug(pid)
        if pid in self.plugins:
            return f"A habilidade {pid} já está ligada."
        if not (self.root / pid / "habilidade.py").exists():
            raise ValueError(f"não tenho a habilidade {pid}")
        manifest, _ = read_folder(self.root / pid)
        if why := blocked(manifest.programa, self.blocked_extra) or blocked(manifest.nome, self.blocked_extra):
            raise ValueError(why)
        plugin = self._load(pid)
        self._set(pid, "ativa", manifest.nome)
        if (prog := self.seen.get(manifest.programa)) is not None:
            await plugin.opened(prog)
        return f"Liguei a habilidade do {manifest.nome}."

    async def deactivate(self, pid: str) -> str:
        pid = program_slug(pid)
        plugin = self.plugins.pop(pid, None)
        if plugin is None:
            return f"A habilidade {pid} não está ligada."
        await plugin.closed()
        for name in plugin.context.actions:
            self.actions.unregister(name)
        self._set(pid, "desativada")
        return f"Desliguei a habilidade do {plugin.manifest.nome}."

    async def remove(self, pid: str) -> str:
        pid = program_slug(pid)
        await self.deactivate(pid)
        shutil.rmtree(self.root / pid, ignore_errors=True)
        self._state["programas"].pop(pid, None)
        self._save()
        return f"Apaguei a habilidade {pid}. Se você abrir o programa de novo, posso perguntar outra vez."

    # ------------------------------------------------------------ mostrar
    def show(self, pid: str) -> str:
        pid = program_slug(pid)
        folder = self.root / pid
        if not (folder / "habilidade.py").exists():
            return f"  (não tenho a habilidade {pid}; veja /habilidades)"
        try:
            manifest, code = read_folder(folder)
        except PluginError as exc:
            return f"  ({exc})"
        problems = check_code(code)
        lines = [
            f"  {manifest.nome} ({manifest.id}): {STATUS_LABEL.get(self.status(pid), self.status(pid) or '—')}",
            f"  o que faz: {manifest.descricao or '—'}",
            f"  por onde entra: {manifest.ponte or '—'}",
            f"  teste: {self._state['programas'].get(pid, {}).get('detalhe') or '—'}",
            f"  travas: {'ok' if not problems else '; '.join(problems)}",
            f"  código para revisar: {folder / 'habilidade.py'}",
            "", *("    " + line for line in code.splitlines()),
        ]
        return "\n".join(lines)

    def describe(self) -> str:
        items = self._state["programas"]
        if not items:
            return "  (nenhuma habilidade ainda: abra um programa ou diga \"vou jogar X\")"
        lines = []
        for key, item in sorted(items.items()):
            st = item.get("status", "")
            open_now = " (aberto agora)" if key in self.seen else ""
            detail = f": {item['detalhe']}" if item.get("detalhe") and st in ("falhou", "desativada") else ""
            lines.append(f"  {key}: {item.get('nome', key)}, {STATUS_LABEL.get(st, st)}{open_now}{detail}")
        return "\n".join(lines)

    def describe_for_prompt(self) -> str:
        bits = []
        for key, prog in self.seen.items():
            if key in self.plugins:
                acts = ", ".join(self.plugins[key].context.actions)
                bits.append(f"{prog.name} está aberto e você está dentro dele pela sua habilidade (ações: {acts})")
        drafts = [i.get("nome", k) for k, i in self._state["programas"].items() if i.get("status") == "rascunho"]
        if drafts:
            bits.append("habilidades esperando a revisão do dono: " + ", ".join(drafts))
        return ("Habilidades: " + "; ".join(bits) + ".") if bits else ""

    async def stop(self) -> None:
        for plugin in list(self.plugins.values()):
            await plugin.closed()
        for task in self.tasks:
            task.cancel()


def register_skill_actions(registry: ActionRegistry, skills: Habilidades) -> None:
    @registry.register(
        "criar_habilidade",
        "cria em segundo plano um jeito de você participar de um programa ou jogo (vira rascunho para o dono revisar)",
        tier=Tier.CONFIRM, speak_result=True,
        params={"programa": "nome do programa ou jogo", "pedido": "o que o dono quer que você faça lá (opcional)"},
        confirm=lambda programa, pedido="": (
            f"Quer que eu crie um jeito de participar do {programa}"
            f"{f' ({pedido})' if pedido else ''} enquanto você usa? Fica como rascunho até você revisar."
        ),
        example=("cria um jeito de você participar do Stardew", '[[acao:criar_habilidade {"programa": "Stardew Valley"}]]'),
    )
    def criar_habilidade(programa: str, pedido: str = "") -> str:
        return skills.start_build(programa, pedido)

    registry.never_initiative.add("criar_habilidade")

    @registry.register("habilidades", "diz quais habilidades de programas você tem e como estão", speak_result=True)
    def habilidades() -> str:
        items = skills._state["programas"]
        if not items:
            return "Ainda não criei nenhuma habilidade."
        return "; ".join(f"{i.get('nome', k)}: {STATUS_LABEL.get(i.get('status', ''), '')}" for k, i in items.items())
