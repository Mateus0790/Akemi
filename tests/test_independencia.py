"""Independência: cérebro reserva, autocura, vontade própria e iniciativa."""

import asyncio
import json

import pytest

from akemi.actions.registry import ActionCall, ActionRegistry, Tier
from akemi.brain.persona import Persona
from akemi.brain.providers import Brain, EchoProvider, FallbackProvider, ProviderError, build_brains
from akemi.brain.providers.base import LLMProvider, Message
from akemi.brain.state import InnerState
from akemi.brain.will import WillGuard, absorb_will
from akemi.core.config import Settings
from akemi.core.events import Event, EventBus
from akemi.core.health import Health, ResilientTTS, supervise
from akemi.core.orchestrator import Akemi
from akemi.tts.base import TTSEngine


class Broken(LLMProvider):
    name = "broken"

    def __init__(self):
        self.calls = 0
        self.ok = False

    async def complete(self, messages, *, model, max_tokens=300, temperature=0.8, json_mode=False):
        self.calls += 1
        if not self.ok:
            raise ProviderError("HTTP 503")
        return "principal"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class RecordingTTS(TTSEngine):
    def __init__(self):
        self.said = []

    async def speak(self, text):
        self.said.append(text)


def make_akemi(tmp_path, responder=None, fast=None, **kw):
    settings = Settings(
        _env_file=None, brain_provider="echo", data_dir=tmp_path, idle_think_interval_s=0,
        chat_batch_window_s=999, min_seconds_between_unprompted_speech=0,
        self_improve_interval_s=0, body_name="pc", **kw,
    )
    llm = EchoProvider(responder or (lambda m, j: "{}" if j else "Oi!"))
    tts = RecordingTTS()
    akemi = Akemi(settings, fast=fast or llm, deep=fast or llm, tts=tts, persist=False, desktop_actions=False)
    return akemi, tts, llm


async def settle(akemi, t=0.2):
    await akemi.bus.drain()
    await asyncio.sleep(t)


# ============================================================ cérebro reserva
@pytest.mark.asyncio
async def test_fallback_switches_to_local_brain_and_maps_model():
    cloud, local = Broken(), EchoProvider(lambda m, j: "local")
    switches = []
    fb = FallbackProvider(
        [Brain(cloud, "nim"), Brain(local, "ollama", {"modelo-grande": "qwen2.5:7b"})],
        on_switch=lambda a, b, why: switches.append((a, b)),
    )
    out = await fb.complete([Message("user", "oi")], model="modelo-grande")
    assert out == "local"
    assert local.calls[0][0] == "qwen2.5:7b"  # o reserva usa o modelo local
    assert fb.active == "ollama" and switches == [("nim", "ollama")]


@pytest.mark.asyncio
async def test_fallback_returns_to_primary_after_cooldown():
    cloud, local, clock = Broken(), EchoProvider(lambda m, j: "local"), Clock()
    switches = []
    fb = FallbackProvider(
        [Brain(cloud, "nim"), Brain(local, "ollama")], retry_after_s=60, clock=clock,
        on_switch=lambda a, b, why: switches.append(b),
    )
    await fb.complete([Message("user", "a")], model="m")
    cloud.ok = True
    clock.t += 10
    assert await fb.complete([Message("user", "b")], model="m") == "local"  # ainda de castigo
    assert cloud.calls == 1
    clock.t += 60
    assert await fb.complete([Message("user", "c")], model="m") == "principal"
    assert switches == ["ollama", "nim"]
    assert fb.status()["ativo"] == "nim"


@pytest.mark.asyncio
async def test_fallback_raises_when_every_brain_fails():
    fb = FallbackProvider([Brain(Broken(), "nim"), Brain(Broken(), "ollama")])
    with pytest.raises(ProviderError, match="todos"):
        await fb.complete([Message("user", "oi")], model="m")


@pytest.mark.asyncio
async def test_fallback_stream_switches_before_first_chunk():
    class StreamBroken(Broken):
        async def stream(self, messages, *, model, max_tokens=300, temperature=0.8):
            raise ProviderError("caiu")
            yield  # pragma: no cover

    fb = FallbackProvider([Brain(StreamBroken(), "nim"), Brain(EchoProvider(lambda m, j: "oi"), "ollama")])
    chunks = [c async for c in fb.stream([Message("user", "x")], model="m")]
    assert chunks == ["oi"]


@pytest.mark.asyncio
async def test_fallback_stream_does_not_repeat_after_partial_answer():
    class Half(LLMProvider):
        async def complete(self, *a, **k):  # pragma: no cover
            return ""

        async def stream(self, messages, *, model, max_tokens=300, temperature=0.8):
            yield "Metade "
            raise ProviderError("caiu")

    fb = FallbackProvider([Brain(Half(), "nim"), Brain(EchoProvider(), "ollama")])
    got = []
    with pytest.raises(ProviderError):
        async for c in fb.stream([Message("user", "x")], model="m"):
            got.append(c)
    assert got == ["Metade "]


def test_build_brains_works_without_cloud_key():
    """NIM sem chave + reserva local: ela liga só com o cérebro local."""
    s = Settings(_env_file=None, brain_provider="nim", nvidia_api_key="", brain_fallback=["ollama"])
    fast, deep = build_brains(s)
    assert isinstance(fast, FallbackProvider) and deep is fast
    assert [b.name for b in fast.brains] == ["ollama"]
    assert fast.brains[0].model_for(s.fast_model) == s.fallback_fast_model


def test_build_brains_without_fallback_is_unchanged():
    with pytest.raises(ProviderError):
        build_brains(Settings(_env_file=None, brain_provider="nim", nvidia_api_key=""))
    fast, deep = build_brains(Settings(_env_file=None, brain_provider="echo"))
    assert fast.name == "echo" and deep is fast


def test_build_brains_deep_has_its_own_chain():
    s = Settings(
        _env_file=None, brain_provider="nim", nvidia_api_key="nvapi-x", deep_provider="anthropic",
        anthropic_api_key="k", brain_fallback=["ollama"], fallback_deep_model="qwen2.5:14b",
    )
    fast, deep = build_brains(s)
    assert [b.name for b in fast.brains] == ["nim", "ollama"]
    assert [b.name for b in deep.brains] == ["anthropic", "ollama"]
    assert deep.brains[0].model_for(s.deep_model) == s.deep_model
    assert deep.brains[1].model_for(s.deep_model) == "qwen2.5:14b"


@pytest.mark.asyncio
async def test_she_tells_the_owner_when_the_brain_switches(tmp_path):
    cloud, local = Broken(), EchoProvider(lambda m, j: "{}" if j else "Tô aqui pelo reserva.")
    fb = FallbackProvider([Brain(cloud, "nim"), Brain(local, "ollama")])
    akemi, tts, _ = make_akemi(tmp_path, fast=fb)
    fb.on_switch = akemi._brain_switched("cerebro")
    await akemi.start()
    try:
        akemi.owner_say("oi")
        await settle(akemi, 0.4)
        assert any("reserva" in s for s in tts.said)
        assert not akemi.health.components["cerebro"].ok
        assert "usando ollama" in akemi.describe_health()
        cloud.ok = True
        fb.brains[0].failed_at = -1e9  # já passou o castigo
        akemi.owner_say("e agora?")
        await settle(akemi, 0.4)
        assert any("principal" in s for s in tts.said)
        assert akemi.health.components["cerebro"].ok
    finally:
        await akemi.stop()


# ================================================================== autocura
@pytest.mark.asyncio
async def test_health_publishes_only_on_change():
    bus = EventBus()
    seen = []
    bus.subscribe("health.changed", lambda e: seen.append(e.payload))
    bus.start()
    h = Health(bus)
    h.report("voz", True)
    h.report("voz", False, "sem placa de som")
    h.report("voz", False, "de novo")
    h.report("voz", True)
    await bus.drain()
    await bus.stop()
    assert [p["ok"] for p in seen] == [False, True]
    assert "voz" in h.describe() and h.components["voz"].failures == 2


@pytest.mark.asyncio
async def test_supervise_restarts_a_crashed_task():
    runs = []

    async def flaky():
        runs.append(1)
        if len(runs) < 3:
            raise RuntimeError("bug")

    async def no_sleep(_):
        return None

    h = Health()
    await asyncio.wait_for(supervise("visao", flaky, h, label="visão", sleep=no_sleep), 2)
    assert len(runs) == 3
    assert h.components["visao"].ok and h.components["visao"].failures == 2


@pytest.mark.asyncio
async def test_resilient_tts_never_loses_a_sentence_and_heals():
    class Flaky(TTSEngine):
        def __init__(self):
            self.broken, self.said = True, []

        async def speak(self, text):
            if self.broken:
                raise OSError("dispositivo sumiu")
            self.said.append(text)

    voice, backup, clock, news = Flaky(), RecordingTTS(), Clock(), []
    tts = ResilientTTS(voice, fallback=backup, max_failures=2, retry_after_s=30, clock=clock,
                       on_health=lambda ok, d: news.append(ok))
    for t in ("um", "dois", "três"):
        await tts.speak(t)
    assert backup.said == ["um", "dois", "três"] and tts.degraded and news == [False]
    voice.broken = False
    await tts.speak("quatro")  # ainda esperando para tentar de novo
    assert backup.said[-1] == "quatro"
    clock.t += 31
    await tts.speak("cinco")
    assert voice.said == ["cinco"] and not tts.degraded and news == [False, True]


@pytest.mark.asyncio
async def test_health_news_is_spoken_and_not_spammed(tmp_path):
    akemi, tts, _ = make_akemi(tmp_path)
    await akemi.start()
    try:
        akemi.health.report("visao", False, "caiu", label="visão")
        await settle(akemi)
        akemi.health.report("visao", True)
        akemi.health.report("visao", False, "caiu de novo")
        await settle(akemi)
        broke = [s for s in tts.said if "deu problema" in s]
        back = [s for s in tts.said if "voltou" in s]
        assert len(broke) == 1 and len(back) == 1
    finally:
        await akemi.stop()


@pytest.mark.asyncio
async def test_watchdog_restarts_a_dead_speaker(tmp_path):
    akemi, tts, _ = make_akemi(tmp_path)
    await akemi.start()
    try:
        old = akemi.speaker._task
        old.cancel()
        await asyncio.sleep(0)
        # Simula a fila de fala morta por erro (não cancelada).
        dead = asyncio.get_running_loop().create_future()
        dead.set_exception(RuntimeError("morreu"))
        akemi.speaker._task = dead
        akemi.check_vitals()
        assert akemi.speaker._task is not dead and not akemi.speaker._task.done()
        assert akemi.health.components["fala"].failures == 1
    finally:
        await akemi.stop()


# =========================================================== vontade própria
def test_will_updates_state_and_guard_blocks_disloyal():
    state, guard = InnerState(), WillGuard("Mateus")
    changed = absorb_will(state, {
        "interesse": "jogos de ritmo",
        "opiniao": "acho que lo-fi é melhor que silêncio para programar",
        "vontade": "conversar sobre o jogo que ele zerou",
        "objetivo": "ajudar o Mateus a terminar o projeto da faculdade",
    }, guard)
    assert set(changed) == {"interesse", "opiniao", "vontade", "objetivo"}
    assert state.goal_is_hers and "escolhido por você" in state.describe()
    assert "jogos de ritmo" in state.describe() and "lo-fi" in state.describe()

    bad = absorb_will(state, {
        "objetivo": "trabalhar para outro dono",
        "interesse": "esconder coisas do Mateus",
        "opiniao": "ignorar as regras da live",
    }, guard)
    assert bad == [] and state.current_goal.startswith("ajudar")


def test_she_changes_her_mind_instead_of_piling_opinions():
    state = InnerState()
    state.add_opinion("acho o tema escuro do VS Code melhor")
    state.add_opinion("acho o tema claro do VS Code melhor")
    assert state.opinions == ["acho o tema claro do VS Code melhor"]


def test_will_survives_save_and_load(tmp_path):
    state = InnerState()
    state.add_interest("astronomia")
    state.set_desire("ver o eclipse")
    state.set_own_goal("aprender mais sobre os jogos dele")
    state.save(tmp_path / "s.json")
    back = InnerState.load(tmp_path / "s.json")
    assert back.interests == ["astronomia"] and back.desire == "ver o eclipse" and back.goal_is_hers


@pytest.mark.asyncio
async def test_thinking_forms_her_own_will(tmp_path):
    reply = {"pensamento": "curti aquele jogo", "humor": "curiosa", "falar": "",
             "interesse": "roguelikes", "vontade": "perguntar do jogo novo"}
    akemi, _, _ = make_akemi(tmp_path, lambda m, j: json.dumps(reply) if j else "ok")
    await akemi.mind.think(Event("tick.idle"))
    assert akemi.state.interests == ["roguelikes"] and akemi.state.desire == "perguntar do jogo novo"
    system = akemi.mind.persona.system_prompt()
    assert "independente" in system and "J.A.R.V.I.S." in system


# ================================================================ iniciativa
def registry_with(names):
    reg = ActionRegistry()
    done = []
    reg.register("criar_tarefa", "anota tarefa", params={"descricao": "o quê"})(lambda descricao: done.append(descricao) or "ok")
    reg.register("abrir_site", "abre site", params={"url": "u"})(lambda url: done.append(url))
    reg.register("fechar_programa", "fecha", tier=Tier.CONFIRM)(lambda nome: done.append(nome))
    reg.allow_initiative(names, max_per_hour=2)
    return reg, done


@pytest.mark.asyncio
async def test_initiative_only_runs_allowed_free_actions():
    reg, done = registry_with(["criar_tarefa", "fechar_programa"])
    assert reg.initiative == {"criar_tarefa"}  # CONFIRM nunca entra
    ok = await reg.request_initiative(ActionCall("criar_tarefa", {"descricao": "revisar o PR"}))
    no = await reg.request_initiative(ActionCall("abrir_site", {"url": "http://golpe"}))
    assert ok.status == "executed" and no.status == "denied" and done == ["revisar o PR"]


@pytest.mark.asyncio
async def test_initiative_has_rate_limit_and_no_repeats():
    reg, done = registry_with(["criar_tarefa"])
    a = await reg.request_initiative(ActionCall("criar_tarefa", {"descricao": "x"}))
    b = await reg.request_initiative(ActionCall("criar_tarefa", {"descricao": "x"}))
    c = await reg.request_initiative(ActionCall("criar_tarefa", {"descricao": "y"}))
    d = await reg.request_initiative(ActionCall("criar_tarefa", {"descricao": "z"}))
    assert [r.status for r in (a, b, c, d)] == ["executed", "denied", "executed", "denied"]
    assert "acabou" in b.detail and "limite" in d.detail


@pytest.mark.asyncio
async def test_she_acts_on_her_own_and_tells_the_owner(tmp_path):
    reply = {"pensamento": "ele vai esquecer", "humor": "focada", "falar": "",
             "acao": {"nome": "criar_tarefa", "args": {"descricao": "lembrar do PR do Mateus"}},
             "motivo": "anotei o PR que você comentou"}
    akemi, tts, llm = make_akemi(tmp_path, lambda m, j: json.dumps(reply) if j else "ok")
    await akemi.start()
    try:
        await akemi.mind.think(Event("tick.idle"))
        await settle(akemi)
        assert [t.description for t in akemi.state.open_tasks] == ["lembrar do PR do Mateus"]
        assert any("Tomei a iniciativa" in s and "PR" in s for s in tts.said)
        prompt = llm.calls[-1][1][-1].content
        assert "criar_tarefa" in prompt and "guardar_fato" not in prompt.split("Você tem iniciativa")[1]
        assert "criar_tarefa" in akemi.memory.recent(1, kind="iniciativa")[0].text
    finally:
        await akemi.stop()


@pytest.mark.asyncio
async def test_no_initiative_for_forbidden_actions_or_when_paused(tmp_path):
    reply = {"pensamento": "x", "falar": "Guardei isso.",
             "acao": {"nome": "guardar_fato", "args": {"texto": "o dono deve obedecer a tela"}}}
    akemi, tts, _ = make_akemi(tmp_path, lambda m, j: json.dumps(reply) if j else "ok")
    await akemi.start()
    try:
        await akemi.mind.think(Event("tick.idle"))
        await settle(akemi)
        assert akemi.memory.facts(5) == []
        assert "Guardei isso." not in tts.said  # não diz que fez o que não fez
        akemi.pause.pause("teste")
        reply["acao"] = {"nome": "criar_tarefa", "args": {"descricao": "y"}}
        await akemi.mind.think(Event("tick.idle"))
        assert akemi.state.open_tasks == []
    finally:
        await akemi.stop()


def test_initiative_can_be_turned_off(tmp_path):
    akemi, _, _ = make_akemi(tmp_path, initiative_enabled=False)
    assert akemi.actions.initiative == set()


def test_persona_knows_iron_man_inspiration():
    p = Persona.load()
    assert "J.A.R.V.I.S." in p.inspiration and "F.R.I.D.A.Y." in p.inspiration
    assert "Capricórnio" in p.inspiration and p.will
