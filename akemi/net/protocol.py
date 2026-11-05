"""Protocolo entre o núcleo (servidor) e o corpo (PC do dono), em JSON sobre WebSocket.

Corpo → núcleo
    ``hello``          {token, version, corpo}  primeira mensagem, obrigatória;
                                              ``corpo`` = {nome, sentidos} (opcional)
    ``owner_message``  {text}                 o dono falou/digitou (Trust.OWNER)
    ``owner_speech_started`` {}               o dono começou a falar (interrompe)
    ``ambient``        {kind, text}           tela/áudio do PC (Trust.PUBLIC)
    ``guest_message``  {text, quem}           alguém falou no mic, mas a voz não é a do
                                              dono (``quem`` = outro | incerto; Trust.PUBLIC)
    ``spoken``         {id, status}           terminou de falar o ``say`` ``id``
    ``action_result``  {id, status, value, detail}  resultado de um ``action``
    ``pause`` / ``resume`` {}                 liga/desliga a autonomia
    ``evolucao``       {acao, id}             lista (``listar``), ``evoluir``, ou
                                              ``aprovar``/``rejeitar``/``desfazer`` #id

Núcleo → corpo
    ``welcome``        {paused, owner}        autenticado
    ``say``            {id, text, origin_trust}  fale isto
    ``stop``           {}                     pare de falar e descarte a fila
    ``paused``         {paused}               estado da pausa mudou
    ``action``         {id, name, args}       execute esta ação do PC (o núcleo já
                                              checou o dono e o "sim"; o corpo só
                                              aceita as de ``PC_ACTIONS``)
    ``evolucao``       {texto}                resposta a um ``evolucao`` do corpo
"""

from __future__ import annotations

import json
import re
from typing import Any

VERSION = 1
MAX_TEXT = 4000
AMBIENT_KINDS = {"screen", "audio"}
SENSES = ("voz", "microfone", "tela", "audio_pc", "acoes")
EVOLUTION_ACTIONS = {"listar", "evoluir", "aprovar", "rejeitar", "desfazer"}

# Códigos de fechamento (4000–4999 são livres para a aplicação).
CLOSE_UNAUTHORIZED = 4401
CLOSE_REPLACED = 4409
CLOSE_BAD_MESSAGE = 4400


class ProtocolError(ValueError):
    pass


def encode(type_: str, **fields: Any) -> str:
    return json.dumps({"type": type_, **fields}, ensure_ascii=False)


def decode(raw: str | bytes) -> dict[str, Any]:
    """Valida o formato básico. Texto grande é cortado em vez de recusado."""
    try:
        msg = json.loads(raw)
    except ValueError as exc:
        raise ProtocolError("JSON inválido") from exc
    if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
        raise ProtocolError("mensagem sem 'type'")
    if "text" in msg:
        if not isinstance(msg["text"], str):
            raise ProtocolError("'text' precisa ser string")
        msg["text"] = msg["text"][:MAX_TEXT]
    return msg


def body_info(raw: Any) -> tuple[str, list[str]]:
    """Valida o ``corpo`` do ``hello``: nome curto e só sentidos conhecidos."""
    raw = raw if isinstance(raw, dict) else {}
    name = re.sub(r"[^\w .-]", "", str(raw.get("nome", "") or ""))[:40].strip() or "corpo"
    senses = raw.get("sentidos", [])
    senses = [s for s in SENSES if isinstance(senses, list) and s in senses]
    return name, senses
