"""Ações que a Akemi pode executar, com níveis de permissão aplicados em código.

Regra de ouro: **o nível de confiança vem do evento que originou o pedido, não
do que o modelo diz**. Se a resposta foi gerada a partir do chat da live
(Trust.PUBLIC), nenhuma ação é executada — mesmo que o LLM tenha sido
convencido a pedir uma. Isso não depende do prompt; é verificado aqui.

Formato que o modelo usa para pedir uma ação, no meio da resposta::

    [[acao:guardar_fato {"texto": "o Mateus prefere café sem açúcar"}]]
"""

from __future__ import annotations

import inspect
import json
import re
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Awaitable, Callable

import structlog

from akemi.core.events import Trust

logger = structlog.get_logger()

ACTION_CALL_RE = re.compile(r"\[\[\s*acao\s*:\s*([\w.-]+)\s*(\{.*?\})?\s*\]\]", re.S | re.I)


class Tier(IntEnum):
    FREE = 0  # executa direto (anotar, lembrar, consultar)
    CONFIRM = 1  # pede "sim" do dono antes (abrir programa, enviar mensagem)
    FORBIDDEN = 2  # nunca automaticamente


ActionFn = Callable[..., Any | Awaitable[Any]]


@dataclass
class Action:
    name: str
    description: str
    fn: ActionFn
    tier: Tier = Tier.FREE
    params: dict[str, str] = field(default_factory=dict)  # nome -> descrição
    speak_result: bool = False  # se o retorno deve ser falado (ex.: horário)
    # Pergunta de confirmação, com os parâmetros ("Fecho o {nome}?"), ou uma
    # função que recebe os argumentos e devolve a pergunta.
    confirm: str | Callable[[dict[str, Any]], str] = ""
    example: tuple[str, str] | None = None  # (pedido do dono, tag): modelos seguem exemplos


@dataclass
class ActionCall:
    name: str
    args: dict[str, Any]


@dataclass
class ActionResult:
    name: str
    status: str  # executed | pending_confirmation | denied | error | unknown
    detail: str = ""
    value: Any = None


@dataclass
class PendingConfirmation:
    call: ActionCall
    created_at: float = field(default_factory=time.time)


def parse_action_calls(text: str) -> tuple[str, list[ActionCall]]:
    """Separa o texto falado das chamadas de ação embutidas."""
    calls: list[ActionCall] = []
    for m in ACTION_CALL_RE.finditer(text):
        try:
            args = json.loads(m.group(2)) if m.group(2) else {}
            if not isinstance(args, dict):
                args = {}
        except ValueError:
            args = {}
        calls.append(ActionCall(m.group(1).lower(), args))
    spoken = ACTION_CALL_RE.sub("", text)
    spoken = re.sub(r"\[\[.*?\]\]", "", spoken, flags=re.S)  # tags malformadas
    return re.sub(r"\s+", " ", spoken).strip(), calls


class ActionRegistry:
    def __init__(self, confirm_timeout_s: float = 60.0) -> None:
        self._actions: dict[str, Action] = {}
        self._pending: PendingConfirmation | None = None
        self.confirm_timeout_s = confirm_timeout_s
        # Iniciativa: ações que ela pode fazer sozinha, sem o dono pedir.
        self.initiative: set[str] = set()
        self.initiative_max_per_hour = 6
        self.initiative_repeat_gap_s = 1800.0
        self._initiative_log: list[tuple[float, str]] = []  # (quando, assinatura)

    def allow_initiative(self, names, max_per_hour: int = 6) -> None:
        """Define o que ela pode fazer por conta própria. Só ações FREE entram."""
        self.initiative = {n for n in names if (a := self._actions.get(n)) and a.tier == Tier.FREE}
        self.initiative_max_per_hour = max(0, int(max_per_hour))

    def register(
        self,
        name: str,
        description: str,
        tier: Tier = Tier.FREE,
        params: dict[str, str] | None = None,
        speak_result: bool = False,
        confirm: str | Callable[[dict[str, Any]], str] = "",
        example: tuple[str, str] | None = None,
    ) -> Callable[[ActionFn], ActionFn]:
        def deco(fn: ActionFn) -> ActionFn:
            self._actions[name] = Action(name, description, fn, tier, params or {}, speak_result, confirm, example)
            return fn

        return deco

    def add(self, action: Action) -> None:
        self._actions[action.name] = action

    def get(self, name: str) -> Action | None:
        return self._actions.get(name)

    def describe_for_prompt(self) -> str:
        """Lista das ações disponíveis, para colocar no prompt do dono."""
        if not self._actions:
            return ""
        lines = [
            "Você pode executar ações escrevendo, no fim da resposta, uma tag como "
            '[[acao:nome {"param": "valor"}]]. Use quando o dono pedir ou for claramente útil.',
            "IMPORTANTE: só a tag faz a ação acontecer. Nunca diga que abriu, fechou, pausou ou "
            "agendou algo sem escrever a tag correspondente.",
            "Ações disponíveis:",
        ]
        for a in self._actions.values():
            if a.tier == Tier.FORBIDDEN:
                continue
            ps = ", ".join(f"{k}: {v}" for k, v in a.params.items()) or "sem parâmetros"
            conf = " (pede confirmação)" if a.tier == Tier.CONFIRM else ""
            ex = f' Ex.: "{a.example[0]}" -> {a.example[1]}' if a.example else ""
            lines.append(f"- {a.name}{conf}: {a.description} [{ps}].{ex}")
        return "\n".join(lines)

    def describe_initiative(self) -> str:
        """As ações que ela pode tomar sozinha, para o prompt do pensamento."""
        names = [n for n in sorted(self.initiative) if n in self._actions]
        if not names:
            return ""
        lines = []
        for n in names:
            a = self._actions[n]
            ps = ", ".join(f"{k}: {v}" for k, v in a.params.items()) or "sem parâmetros"
            lines.append(f"- {n}: {a.description} [{ps}]")
        return "\n".join(lines)

    # ------------------------------------------------------------ execução
    async def request_initiative(self, call: ActionCall) -> ActionResult:
        """Ela decidiu sozinha fazer algo (estilo J.A.R.V.I.S.).

        Não usa confiança de dono: em vez disso, só passam as ações da lista
        de iniciativa (todas FREE e reversíveis), com limite por hora e sem
        repetir a mesma ação seguida. Nada que pede "sim" entra aqui.
        """
        action = self._actions.get(call.name)
        if action is None:
            return ActionResult(call.name, "unknown", "ação inexistente")
        if call.name not in self.initiative or action.tier != Tier.FREE:
            logger.warning("initiative denied: not allowed", action=call.name)
            return ActionResult(call.name, "denied", "fora da lista de iniciativa")
        now = time.time()
        self._initiative_log = [(t, sig) for t, sig in self._initiative_log if now - t < 3600]
        if len(self._initiative_log) >= self.initiative_max_per_hour:
            return ActionResult(call.name, "denied", "limite de iniciativas por hora")
        sig = f"{call.name} {json.dumps(call.args, sort_keys=True, ensure_ascii=False)}"
        if any(s == sig and now - t < self.initiative_repeat_gap_s for t, s in self._initiative_log):
            return ActionResult(call.name, "denied", "acabou de fazer isso")
        self._initiative_log.append((now, sig))
        logger.info("initiative action", action=call.name)
        return await self._run(action, call)

    async def request(self, call: ActionCall, trust: Trust) -> ActionResult:
        action = self._actions.get(call.name)
        if action is None:
            return ActionResult(call.name, "unknown", "ação inexistente")
        if trust < Trust.OWNER:
            logger.warning("action denied: untrusted origin", action=call.name, trust=trust.name)
            return ActionResult(call.name, "denied", "origem não confiável")
        if action.tier == Tier.FORBIDDEN:
            return ActionResult(call.name, "denied", "ação proibida")
        if action.tier == Tier.CONFIRM:
            self._pending = PendingConfirmation(call)
            return ActionResult(call.name, "pending_confirmation", self._confirm_text(action, call))
        return await self._run(action, call)

    def _confirm_text(self, action: Action, call: ActionCall) -> str:
        if action.confirm:
            try:
                question = action.confirm(call.args) if callable(action.confirm) else action.confirm.format(**call.args)
                return question + " Diz sim ou não."
            except (KeyError, IndexError, ValueError, TypeError, AttributeError):
                pass
        args = ", ".join(f"{k}={v}" for k, v in call.args.items())
        return f"Posso executar {action.name}{f' ({args})' if args else ''}? Diz sim ou não."

    @property
    def pending(self) -> PendingConfirmation | None:
        if self._pending and time.time() - self._pending.created_at > self.confirm_timeout_s:
            self._pending = None
        return self._pending

    async def resolve_confirmation(self, approved: bool, trust: Trust) -> ActionResult | None:
        pending = self.pending
        if pending is None:
            return None
        if trust < Trust.OWNER:
            return ActionResult(pending.call.name, "denied", "só o dono confirma")
        self._pending = None
        if not approved:
            return ActionResult(pending.call.name, "denied", "dono recusou")
        action = self._actions[pending.call.name]
        return await self._run(action, pending.call)

    async def execute_authorized(self, call: ActionCall) -> ActionResult:
        """Executa sem checar confiança nem pedir confirmação.

        Só para o corpo: o núcleo já checou que o pedido veio do dono e já
        pegou o "sim". Quem chama precisa restringir *quais* ações aceita.
        """
        action = self._actions.get(call.name)
        if action is None:
            return ActionResult(call.name, "unknown", "ação inexistente")
        return await self._run(action, call)

    async def _run(self, action: Action, call: ActionCall) -> ActionResult:
        try:
            sig = inspect.signature(action.fn)
            accepted = {k: v for k, v in call.args.items() if k in sig.parameters}
            value = action.fn(**accepted)
            if inspect.isawaitable(value):
                value = await value
            logger.info("action executed", action=action.name)
            return ActionResult(action.name, "executed", "", value)
        except Exception as exc:
            logger.exception("action failed", action=action.name)
            return ActionResult(action.name, "error", str(exc))


YES_RE = re.compile(r"^\s*(sim|pode|manda|confirmo|isso|ok|beleza|bora|yes)\b", re.I)
NO_RE = re.compile(r"^\s*(n[aã]o|cancela|para|deixa|esquece|no)\b", re.I)


def parse_yes_no(text: str) -> bool | None:
    if YES_RE.search(text):
        return True
    if NO_RE.search(text):
        return False
    return None
