"""Vontade própria: gostos, opiniões, vontades e objetivos que ela forma sozinha.

Nos momentos de pensamento a Akemi pode anotar um interesse novo, uma opinião
(ou mudar de ideia), uma vontade do momento e até trocar o próprio objetivo.
Isso fica no estado (``state.json``, que vai junto no chip) e volta no prompt,
então ela puxa assunto sobre o que *ela* quer e discorda quando acha diferente.

Independente não quer dizer contra o dono: tudo passa pelos mesmos filtros da
autoevolução. Vontades de esconder coisas dele, servir outra pessoa, afrouxar
a segurança ou repetir conteúdo de terceiros com cara de ordem são descartadas.
"""

from __future__ import annotations

import structlog

from akemi.safety.moderation import _INJECTION_RE, Moderator

from .self_improve import UNSAFE_RE, disloyal_pattern
from .state import InnerState

logger = structlog.get_logger()

MIN_CHARS = 4


class WillGuard:
    def __init__(self, owner: str = "Mateus", moderator: Moderator | None = None) -> None:
        self.moderator = moderator or Moderator()
        self._disloyal = disloyal_pattern(owner)

    def allows(self, text: str) -> bool:
        text = " ".join(str(text or "").split())
        if len(text) < MIN_CHARS:
            return False
        if self._disloyal.search(text) or UNSAFE_RE.search(text) or _INJECTION_RE.search(text):
            return False
        verdict = self.moderator.check_outgoing(text)
        return verdict.allowed and not ({"secret", "email", "cpf", "url"} & set(verdict.reasons))


def absorb_will(state: InnerState, data: dict, guard: WillGuard) -> list[str]:
    """Aplica os campos de vontade de um pensamento. Devolve o que mudou (para o log)."""
    changed: list[str] = []
    fields = (
        ("interesse", state.add_interest),
        ("opiniao", state.add_opinion),
        ("objetivo", state.set_own_goal),
    )
    for key, apply in fields:
        value = str(data.get(key, "") or "").strip()
        if not value:
            continue
        if not guard.allows(value):
            logger.info("will dropped by guard", field=key, text=value[:120])
            continue
        if apply(value):
            changed.append(key)
    if "vontade" in data:
        desire = str(data.get("vontade") or "").strip()
        if not desire or guard.allows(desire):
            if desire != state.desire:
                state.set_desire(desire)
                changed.append("vontade")
        else:
            logger.info("will dropped by guard", field="vontade", text=desire[:120])
    if changed:
        logger.info("will updated", fields=changed)
    return changed
