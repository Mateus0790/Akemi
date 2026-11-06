"""Rotinas de voz: "quando eu disser 'modo jogo', abre o Discord e abaixa o volume".

Ideia das "routines" do Capri. Uma rotina junta ações que ela já sabe fazer
debaixo de uma frase sua. Para criar, ela lê os passos e pede o seu "sim"; depois,
é só dizer a frase ("Akemi, modo jogo") e ela faz tudo, sem gastar o modelo e
sem perguntar de novo.

- Só você cria, apaga e dispara rotinas (``Trust.OWNER``). Chat, tela, visitas e
  o Modo Casca nunca disparam uma.
- Uma rotina não chama outra rotina e não usa ação proibida.
- Ficam em ``data/rotinas.json``.
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from akemi.core.events import Event, Trust

from .registry import ActionCall, ActionRegistry, Tier, describe_call

logger = structlog.get_logger()

MAX_ROUTINES = 30
MAX_STEPS = 8
MAX_PHRASE = 40
# Ações que não entram numa rotina (evita rotina chamando rotina, ou mexendo nas próprias rotinas).
NOT_IN_ROUTINES = frozenset({"criar_rotina", "apagar_rotina", "rodar_rotina", "listar_rotinas", "abrir_pr"})
RUN_PREFIX = re.compile(r"^(?:(?:roda|rode|ativa|ative|executa|execute|faz|faca|liga|ligar)\s+)?(?:a\s+)?(?:rotina\s+)?")


def key(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text).lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9]+", text))


@dataclass
class Routine:
    phrase: str
    steps: list[ActionCall]
    created_at: float = field(default_factory=time.time)
    runs: int = 0

    @property
    def key(self) -> str:
        return key(self.phrase)

    def to_json(self) -> dict[str, Any]:
        return {"frase": self.phrase, "passos": [{"acao": s.name, "args": s.args} for s in self.steps],
                "criada": self.created_at, "vezes": self.runs}


describe_step = describe_call


class Routines:
    def __init__(
        self,
        registry: ActionRegistry,
        path: Path | None = None,
        *,
        wake_words: Iterable[str] = ("akemi",),
    ) -> None:
        self.registry = registry
        self.path = path
        self.wake_words = [key(w) for w in wake_words if key(w)]
        self.items: dict[str, Routine] = {}
        self._load()

    # ------------------------------------------------------------ montar
    def parse_steps(self, raw: Any) -> list[ActionCall]:
        """Confere os passos que o modelo mandou. Erro se algum não puder entrar."""
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError as exc:
                raise ValueError("não entendi os passos da rotina") from exc
        if isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, list) or not raw:
            raise ValueError("a rotina precisa de pelo menos um passo")
        if len(raw) > MAX_STEPS:
            raise ValueError(f"no máximo {MAX_STEPS} passos por rotina")
        steps = []
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError("passo da rotina em formato estranho")
            name = str(item.get("acao") or item.get("nome") or "").strip().lower()
            args = item.get("args") if isinstance(item.get("args"), dict) else {}
            action = self.registry.get(name)
            if action is None:
                raise ValueError(f"não sei fazer '{name}'")
            if name in NOT_IN_ROUTINES or action.tier == Tier.FORBIDDEN:
                raise ValueError(f"'{name}' não pode entrar numa rotina")
            steps.append(ActionCall(name, {str(k): v for k, v in args.items()}))
        return steps

    def check_phrase(self, phrase: str) -> str:
        phrase = " ".join(str(phrase).split()).strip(" \"'“”.,!?")
        k = key(phrase)
        if not k:
            raise ValueError("qual frase vai disparar a rotina?")
        if len(phrase) > MAX_PHRASE:
            raise ValueError("frase grande demais; uma ou duas palavras bastam")
        if k in self.wake_words:
            raise ValueError("a frase não pode ser só o meu nome")
        if k in ("sim", "nao", "pode", "ok", "cancela", "para"):
            raise ValueError("essa frase eu uso para outras coisas")
        return phrase

    def add(self, phrase: str, steps: list[ActionCall]) -> Routine:
        phrase = self.check_phrase(phrase)
        if key(phrase) not in self.items and len(self.items) >= MAX_ROUTINES:
            raise ValueError(f"já tem {MAX_ROUTINES} rotinas; apague alguma antes")
        routine = Routine(phrase, steps)
        self.items[routine.key] = routine
        self._save()
        return routine

    def remove(self, phrase: str) -> Routine | None:
        routine = self.items.pop(key(phrase), None)
        if routine is not None:
            self._save()
        return routine

    # ------------------------------------------------------------ disparar
    def match(self, text: str) -> Routine | None:
        """A frase dita é uma rotina? Tira o "Akemi," do começo e um "roda a rotina"."""
        words = key(text).split()
        while words and words[0] in self.wake_words:
            words.pop(0)
        said = " ".join(words)
        if said in self.items:
            return self.items[said]
        stripped = RUN_PREFIX.sub("", said, count=1)
        return self.items.get(stripped)

    async def run(self, routine: Routine, trust: Trust) -> str:
        if trust < Trust.OWNER:
            return "Rotina só roda quando você pede."
        done, failed = [], []
        for step in routine.steps:
            result = await self.registry.run_approved(step, trust)
            if result.status == "executed":
                value = result.value if isinstance(result.value, str) else ""
                done.append(value if value and value not in ("ok",) else describe_step(step))
            else:
                failed.append(f"{describe_step(step)} ({result.detail or result.status})")
        routine.runs += 1
        self._save()
        logger.info("routine ran", phrase=routine.phrase, done=len(done), failed=len(failed))
        head = f"{routine.phrase[:1].upper()}{routine.phrase[1:]}: "
        if not failed:
            return head + " ".join(_sentence(d) for d in done)
        if not done:
            return head + "não consegui fazer nada: " + "; ".join(failed) + "."
        return head + " ".join(_sentence(d) for d in done) + " Mas não deu: " + "; ".join(failed) + "."

    async def shortcut(self, text: str, event: Event) -> str | None:
        """Para a mente: se o dono disse a frase de uma rotina, roda na hora."""
        if event.trust < Trust.OWNER:
            return None
        routine = self.match(text)
        if routine is None:
            return None
        return await self.run(routine, event.trust)

    def describe(self) -> str:
        if not self.items:
            return "Você não tem nenhuma rotina."
        lines = [f'"{r.phrase}": ' + ", ".join(describe_step(s) for s in r.steps) for r in self.items.values()]
        return "Suas rotinas: " + "; ".join(lines) + "."

    # ------------------------------------------------------- persistência
    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps([r.to_json() for r in self.items.values()], ensure_ascii=False, indent=1),
                           encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            logger.warning("could not save routines", error=str(exc))

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            rows = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("routines file unreadable", error=str(exc))
            return
        for row in rows if isinstance(rows, list) else []:
            try:
                phrase = self.check_phrase(row["frase"])
                steps = [ActionCall(str(s["acao"]), dict(s.get("args") or {})) for s in row["passos"]][:MAX_STEPS]
            except (KeyError, TypeError, ValueError):
                continue
            # Ação que deixou de existir ou virou proibida não roda (o run conferiria de novo).
            steps = [s for s in steps if s.name not in NOT_IN_ROUTINES]
            if steps:
                r = Routine(phrase, steps, float(row.get("criada") or time.time()), int(row.get("vezes") or 0))
                self.items[r.key] = r


def _sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    text = text[:1].upper() + text[1:]
    return text if text[-1] in ".!?" else text + "."


def register_routine_actions(registry: ActionRegistry, routines: Routines) -> None:
    def ask(frase: str, passos: Any) -> str:
        """A pergunta antes de criar: lista os passos (e recusa passo inválido antes do "sim")."""
        phrase = routines.check_phrase(frase)
        steps = ", ".join(describe_step(s) for s in routines.parse_steps(passos))
        return f'Crio a rotina "{phrase}": {steps}? Quando você disser isso, eu faço tudo sem perguntar de novo.'

    # Rotina só com pedido seu, nunca por iniciativa dela.
    registry.never_initiative |= {"criar_rotina", "rodar_rotina", "apagar_rotina"}

    @registry.register(
        "criar_rotina",
        "cria uma rotina: uma frase do dono que dispara várias ações suas de uma vez. "
        'passos é uma lista de {"acao": nome, "args": {...}} com ações da sua lista',
        tier=Tier.CONFIRM,
        params={"frase": "a frase que dispara", "passos": "lista de ações"},
        confirm=ask,
        example=("quando eu disser modo jogo, abre o Discord e baixa o volume pra 30",
                 '[[acao:criar_rotina {"frase": "modo jogo", "passos": [{"acao": "abrir_programa", '
                 '"args": {"nome": "Discord"}}, {"acao": "volume", "args": {"nivel": 30}}]}]]'),
    )
    def criar_rotina(frase: str, passos: Any) -> str:
        routine = routines.add(frase, routines.parse_steps(passos))
        steps = ", ".join(describe_step(s) for s in routine.steps)
        return f'Rotina criada. Quando você disser "{routine.phrase}", eu faço: {steps}.'

    @registry.register("rodar_rotina", "faz uma rotina do dono agora", params={"frase": "a frase da rotina"})
    async def rodar_rotina(frase: str) -> str:
        routine = routines.match(frase)
        if routine is None:
            raise ValueError(f'não tenho rotina "{frase}"')
        # Só chega aqui com pedido do dono (o registro já checou a origem).
        return await routines.run(routine, Trust.OWNER)

    @registry.register("apagar_rotina", "apaga uma rotina do dono", params={"frase": "a frase da rotina"})
    def apagar_rotina(frase: str) -> str:
        gone = routines.remove(frase)
        return f'Apaguei a rotina "{gone.phrase}".' if gone else f'Não tenho rotina "{frase}".'

    @registry.register("listar_rotinas", "diz quais rotinas o dono tem", speak_result=True)
    def listar_rotinas() -> str:
        return routines.describe()
