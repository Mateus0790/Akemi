import pytest

from akemi.actions.registry import ActionCall, ActionRegistry, Tier, parse_action_calls, parse_yes_no
from akemi.core.events import Trust


def make_registry():
    reg = ActionRegistry()
    calls = []

    @reg.register("anotar", "anota", params={"texto": "t"})
    def anotar(texto: str):
        calls.append(texto)
        return "ok"

    @reg.register("abrir", "abre", tier=Tier.CONFIRM)
    async def abrir():
        calls.append("abriu")
        return "aberto"

    return reg, calls


def test_parse_action_calls_separates_speech():
    spoken, calls = parse_action_calls('Pode deixar! [[acao:anotar {"texto": "café"}]] [[humor:animada]]')
    assert spoken == "Pode deixar!"
    assert calls[0].name == "anotar" and calls[0].args == {"texto": "café"}


def test_parse_action_calls_tolerates_bad_json():
    _, calls = parse_action_calls("[[acao:anotar {quebrado}]]")
    assert calls == [] or calls[0].args == {}


@pytest.mark.asyncio
async def test_public_origin_can_never_run_actions():
    reg, calls = make_registry()
    r = await reg.request(ActionCall("anotar", {"texto": "x"}), Trust.PUBLIC)
    assert r.status == "denied" and calls == []
    r = await reg.request(ActionCall("anotar", {"texto": "x"}), Trust.SYSTEM)
    assert r.status == "denied"


@pytest.mark.asyncio
async def test_owner_free_action_runs():
    reg, calls = make_registry()
    r = await reg.request(ActionCall("anotar", {"texto": "x", "lixo": 1}), Trust.OWNER)
    assert r.status == "executed" and calls == ["x"]


@pytest.mark.asyncio
async def test_confirm_flow():
    reg, calls = make_registry()
    r = await reg.request(ActionCall("abrir", {}), Trust.OWNER)
    assert r.status == "pending_confirmation" and calls == []
    # Só o dono confirma
    r = await reg.resolve_confirmation(True, Trust.PUBLIC)
    assert r.status == "denied" and calls == []
    r = await reg.resolve_confirmation(True, Trust.OWNER)
    assert r.status == "executed" and calls == ["abriu"]
    assert reg.pending is None


def test_yes_no():
    assert parse_yes_no("sim, pode abrir") is True
    assert parse_yes_no("não precisa") is False
    assert parse_yes_no("qual o clima hoje?") is None
