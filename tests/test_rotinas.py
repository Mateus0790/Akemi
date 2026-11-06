"""Rotinas de voz: "quando eu disser 'modo jogo', abre o Discord e abaixa o volume"."""

import json

import pytest

from akemi.actions.registry import ActionCall, ActionRegistry, Tier
from akemi.actions.routines import Routines, register_routine_actions
from akemi.core.events import Event, Trust

STEPS = [{"acao": "abrir_programa", "args": {"nome": "Discord"}}, {"acao": "volume", "args": {"nivel": 30}}]


def setup(tmp_path=None):
    reg, calls = ActionRegistry(), []

    @reg.register("abrir_programa", "abre", params={"nome": "programa"})
    def abrir_programa(nome: str) -> str:
        calls.append(("abrir", nome))
        return f"Abrindo {nome}."

    @reg.register("volume", "volume", params={"nivel": "0-100"})
    def volume(nivel: int) -> str:
        calls.append(("volume", nivel))
        return "ok"

    @reg.register("fechar_programa", "fecha", tier=Tier.CONFIRM, params={"nome": "programa"})
    def fechar_programa(nome: str) -> str:
        calls.append(("fechar", nome))
        return f"Fechei {nome}."

    @reg.register("formatar_disco", "nunca", tier=Tier.FORBIDDEN)
    def formatar_disco() -> str:
        calls.append(("formatou",))
        return "x"

    @reg.register("quebra", "sempre dá erro")
    def quebra() -> str:
        raise RuntimeError("deu ruim")

    routines = Routines(reg, tmp_path / "rotinas.json" if tmp_path else None)
    register_routine_actions(reg, routines)
    return reg, routines, calls


@pytest.mark.asyncio
async def test_create_with_confirmation_then_run_by_voice(tmp_path):
    reg, routines, calls = setup(tmp_path)
    r = await reg.request(ActionCall("criar_rotina", {"frase": "Modo jogo", "passos": STEPS}), Trust.OWNER)
    assert r.status == "pending_confirmation"
    assert "abrir programa (Discord), volume (30)" in r.detail and "sem perguntar de novo" in r.detail
    assert routines.items == {}
    r = await reg.resolve_confirmation(True, Trust.OWNER)
    assert r.status == "executed" and 'Quando você disser "Modo jogo"' in r.value
    reply = await routines.shortcut("Akemi, modo jogo!", Event("owner.message", trust=Trust.OWNER))
    assert reply == "Modo jogo: Abrindo Discord. Volume (30)."
    assert calls == [("abrir", "Discord"), ("volume", 30)]
    assert await routines.shortcut("roda a rotina modo jogo", Event("owner.message", trust=Trust.OWNER))
    assert await routines.shortcut("modo jogo agora não", Event("owner.message", trust=Trust.OWNER)) is None
    saved = json.loads((tmp_path / "rotinas.json").read_text())
    assert saved[0]["frase"] == "Modo jogo" and saved[0]["vezes"] == 2
    # sobrevive a um reinício
    _, again, _ = setup(tmp_path)
    assert again.match("modo jogo").steps[0].args == {"nome": "Discord"}


@pytest.mark.asyncio
async def test_only_the_owner():
    reg, routines, calls = setup()
    routines.add("modo jogo", routines.parse_steps(STEPS))
    for trust in (Trust.PUBLIC, Trust.SYSTEM):
        assert await routines.shortcut("modo jogo", Event("owner.message", trust=trust)) is None
        r = await reg.request(ActionCall("rodar_rotina", {"frase": "modo jogo"}), trust)
        assert r.status == "denied"
        r = await reg.request(ActionCall("criar_rotina", {"frase": "x", "passos": STEPS}), trust)
        assert r.status == "denied"
    assert calls == []
    # e nunca por iniciativa dela
    reg.allow_initiative(["rodar_rotina", "abrir_programa"])
    assert reg.initiative == {"abrir_programa"}


@pytest.mark.asyncio
async def test_bad_steps_are_refused_before_asking():
    reg, routines, _ = setup()
    for passos, why in (
        ([{"acao": "formatar_disco"}], "não pode entrar"),
        ([{"acao": "voar"}], "não sei fazer"),
        ([{"acao": "rodar_rotina", "args": {"frase": "x"}}], "não pode entrar"),
        ([], "pelo menos um passo"),
        ([{"acao": "volume", "args": {"nivel": 1}}] * 9, "no máximo"),
        ("isso não é json", "não entendi"),
    ):
        r = await reg.request(ActionCall("criar_rotina", {"frase": "x", "passos": passos}), Trust.OWNER)
        assert r.status == "error" and why in r.detail, (passos, r)
        assert reg.pending is None
    r = await reg.request(ActionCall("criar_rotina", {"frase": "Akemi", "passos": STEPS}), Trust.OWNER)
    assert r.status == "error" and "meu nome" in r.detail
    r = await reg.request(ActionCall("criar_rotina", {"frase": "sim", "passos": STEPS}), Trust.OWNER)
    assert r.status == "error"
    r = await reg.request(ActionCall("criar_rotina", {"frase": "x"}), Trust.OWNER)
    assert r.status == "error"


@pytest.mark.asyncio
async def test_confirmed_steps_run_without_asking_again_and_failures_are_told():
    reg, routines, calls = setup()
    routines.add("fim do dia", routines.parse_steps([
        {"acao": "fechar_programa", "args": {"nome": "Discord"}}, {"acao": "quebra"},
        {"acao": "volume", "args": {"nivel": 10}},
    ]))
    reply = await routines.run(routines.match("fim do dia"), Trust.OWNER)
    assert calls == [("fechar", "Discord"), ("volume", 10)]
    assert reply == "Fim do dia: Fechei Discord. Volume (10). Mas não deu: quebra (deu ruim)."
    assert reg.pending is None


@pytest.mark.asyncio
async def test_list_and_delete():
    reg, routines, _ = setup()
    r = await reg.request(ActionCall("listar_rotinas", {}), Trust.OWNER)
    assert r.value == "Você não tem nenhuma rotina."
    routines.add("modo jogo", routines.parse_steps(STEPS))
    r = await reg.request(ActionCall("listar_rotinas", {}), Trust.OWNER)
    assert r.value == 'Suas rotinas: "modo jogo": abrir programa (Discord), volume (30).'
    r = await reg.request(ActionCall("apagar_rotina", {"frase": "Modo Jogo"}), Trust.OWNER)
    assert r.value == 'Apaguei a rotina "modo jogo".' and routines.match("modo jogo") is None


@pytest.mark.asyncio
async def test_she_runs_a_routine_through_the_mind(tmp_path):
    """Dita no console/microfone, a frase roda a rotina sem passar pelo modelo."""
    from akemi.brain.providers import EchoProvider
    from akemi.core.config import Settings
    from akemi.core.orchestrator import Akemi
    from akemi.tts.base import ConsoleTTS

    said = []
    llm = EchoProvider(lambda m, j: "isso não devia ser chamado")
    settings = Settings(_env_file=None, brain_provider="echo", data_dir=tmp_path, idle_think_interval_s=0,
                        self_improve_interval_s=0)
    akemi = Akemi(settings, fast=llm, deep=llm, tts=ConsoleTTS(printer=said.append), persist=False,
                  desktop_actions=False)
    akemi.routines.add("bom dia", akemi.routines.parse_steps([{"acao": "horario"}]))
    await akemi.start()
    try:
        akemi.owner_say("Akemi, bom dia")
        await akemi.bus.drain()
        assert len(said) == 1 and said[0].startswith("🗣️  Akemi: Bom dia: ")
        assert "não devia" not in said[0]
    finally:
        await akemi.stop()
