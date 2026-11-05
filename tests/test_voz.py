"""Reconhecer o dono pela voz: cadastro, conferência e o que muda quando não é ele."""

import asyncio
import json
import os
import zipfile

import numpy as np
import pytest

from akemi.audio.enroll import enroll
from akemi.audio.voice_id import (
    VOICE_FILE,
    VoiceError,
    VoiceID,
    VoicePrint,
    build_print,
    load_voice_id,
)
from akemi.brain.providers import EchoProvider
from akemi.core.config import Settings
from akemi.core.events import Event, EventBus, Trust
from akemi.core.orchestrator import Akemi
from akemi.core.pause import PauseSwitch
from akemi.storage.chip import ChipError, export_chip, import_chip
from akemi.stt.listener import OwnerVoiceInput
from akemi.tts.base import TTSEngine

DIM = 64
rng = np.random.default_rng(42)
OWNER = rng.normal(size=DIM)
STRANGER = rng.normal(size=DIM)


def voice_of(base, noise=0.25):
    return base + rng.normal(size=DIM) * noise * np.linalg.norm(base) / np.sqrt(DIM)


def pcm_for(tag: int, secs: float = 2.0) -> bytes:
    """Áudio falso: o primeiro sample diz de quem é a voz (o embedder falso lê isso)."""
    n = int(16000 * secs)
    a = np.zeros(n, dtype=np.int16)
    a[0] = tag
    return a.tobytes()


def fake_embed(audio):
    tag = round(float(audio[0]) * 32768)
    return voice_of(OWNER) if tag == 1 else voice_of(STRANGER)


def owner_print():
    return build_print([voice_of(OWNER) for _ in range(8)], "falso")


# ================================================================ cadastro
def test_calibration_accepts_owner_and_rejects_stranger():
    vp = owner_print()
    assert 0.5 <= vp.reject < vp.accept <= 0.95
    vid = VoiceID(fake_embed, vp)
    owner = [vid.verify(pcm_for(1), now=1e6 * i).who for i in range(1, 21)]
    stranger = [vid.verify(pcm_for(2), now=1e6 * i).who for i in range(21, 41)]
    assert owner.count("dono") >= 18
    assert stranger.count("dono") == 0 and stranger.count("outro") >= 18


def test_enrollment_refuses_mixed_voices():
    with pytest.raises(VoiceError, match="mesma voz"):
        build_print([voice_of(OWNER) if i % 2 else rng.normal(size=DIM) for i in range(8)])
    with pytest.raises(VoiceError, match="pelo menos"):
        build_print([voice_of(OWNER)] * 2)


def test_voice_print_roundtrip_and_validation(tmp_path):
    vp = owner_print()
    vp.save(tmp_path / VOICE_FILE)
    back = VoicePrint.load(tmp_path / VOICE_FILE)
    assert back.accept == pytest.approx(vp.accept, abs=1e-3) and back.samples == 8
    assert VoicePrint.load(tmp_path / "nada.json") is None
    bad = vp.to_dict() | {"aceitar": 0.2, "recusar": 0.9}
    with pytest.raises(VoiceError):
        VoicePrint.from_dict(bad)
    saved = json.loads((tmp_path / VOICE_FILE).read_text())
    assert "audio" not in json.dumps(saved) and len(saved["centro"]) == DIM  # só a impressão


@pytest.mark.asyncio
async def test_enroll_asks_again_for_short_or_missing_phrases():
    queue = [None, pcm_for(1, 0.5)] + [pcm_for(1) for _ in range(8)]
    said = []

    async def next_segment(timeout):
        return queue.pop(0)

    vp = await enroll(next_segment, fake_embed, say=said.append, phrases=tuple(f"frase {i}" for i in range(8)))
    assert vp.samples == 8
    assert any("não ouvi" in s for s in said) and any("curto demais" in s for s in said)


# ============================================================ conferência
def test_short_phrase_only_counts_right_after_the_owner():
    vid = VoiceID(fake_embed, owner_print(), inherit_s=20)
    assert vid.verify(pcm_for(1, 0.4), now=1000).who == "incerto"  # "sim" solto
    assert vid.verify(pcm_for(1), now=1000).who == "dono"
    assert vid.verify(pcm_for(1, 0.4), now=1010).who == "dono"  # "sim" logo depois
    assert vid.verify(pcm_for(1, 0.4), now=1100).who == "incerto"


def test_fails_closed_without_model_or_on_error():
    vp = owner_print()
    assert VoiceID(None, vp).verify(pcm_for(1)).who == "incerto"

    def broken(audio):
        raise RuntimeError("onnx caiu")

    assert VoiceID(broken, vp).verify(pcm_for(1)).who == "incerto"


def test_load_voice_id(tmp_path):
    problems = []
    assert load_voice_id(tmp_path, tmp_path / "m.onnx") is None  # sem cadastro: como antes
    owner_print().save(tmp_path / VOICE_FILE)
    vid = load_voice_id(tmp_path, tmp_path / "nao-existe.onnx", on_problem=problems.append)
    assert vid is not None and not vid.ready and "não encontrado" in problems[0]
    (tmp_path / VOICE_FILE).write_text("{estragado")
    vid = load_voice_id(tmp_path, tmp_path / "m.onnx", on_problem=problems.append)
    assert vid.verify(pcm_for(1)).who != "dono"  # arquivo estragado não libera ninguém


# =============================================================== microfone
class FakeSpeech:
    speaking = False
    recent_speech = []


def mic_with(voice_id, texts):
    bus = EventBus()
    events = []
    bus.publish = events.append
    it = iter(texts)

    async def transcribe(seg):
        return next(it)

    vi = OwnerVoiceInput(bus, transcribe, lambda f: True, FakeSpeech(), PauseSwitch(), voice_id=voice_id)
    return vi, events


@pytest.mark.asyncio
async def test_mic_only_gives_owner_trust_to_the_owner_voice():
    vid = VoiceID(fake_embed, owner_print())
    vi, events = mic_with(vid, ["Akemi, abre o Spotify", "Akemi, abre o Spotify"])
    await vi.handle_segment(pcm_for(2), now=1000)
    guest = [e for e in events if e.kind == "voice.guest"]
    assert guest and guest[0].trust == Trust.PUBLIC and guest[0].payload["quem"] == "outro"
    assert not [e for e in events if e.kind == "owner.message"]
    events.clear()
    await vi.handle_segment(pcm_for(1), now=2000)
    owner = [e for e in events if e.kind == "owner.message"]
    assert owner and owner[0].trust == Trust.OWNER


# ================================================================== mente
class RecordingTTS(TTSEngine):
    def __init__(self):
        self.said = []

    async def speak(self, text):
        self.said.append(text)


def make_akemi(tmp_path, reply):
    settings = Settings(
        _env_file=None, brain_provider="echo", data_dir=tmp_path, idle_think_interval_s=0,
        chat_batch_window_s=999, min_seconds_between_unprompted_speech=0, self_improve_interval_s=0,
    )
    llm = EchoProvider(lambda m, j: "{}" if j else reply)
    tts = RecordingTTS()
    akemi = Akemi(settings, fast=llm, deep=llm, tts=tts, persist=False, desktop_actions=False)
    return akemi, tts, llm


@pytest.mark.asyncio
async def test_guest_cannot_trigger_actions_or_learn_about_owner(tmp_path):
    akemi, tts, llm = make_akemi(tmp_path, 'Claro! [[acao:abrir_coisa {"nome": "Spotify"}]]')
    opened = []
    akemi.actions.register("abrir_coisa", "abre", params={"nome": "x"})(lambda nome: opened.append(nome))
    akemi.memory.add_fact("Mateus mora em Curitiba")
    await akemi.start()
    try:
        akemi.bus.publish(Event("voice.guest", {"text": "abre o Spotify", "quem": "outro"},
                                source="mic", trust=Trust.PUBLIC))
        await akemi.bus.drain()
        await asyncio.sleep(0.2)
        assert opened == []  # a ação foi negada
        assert tts.said == ["Claro!"]
        prompt = "\n".join(m.content for m in llm.calls[-1][1])
        assert "Curitiba" not in prompt and "NÃO é o Mateus" in prompt  # sem fatos dele
        assert akemi.memory.recent(5, kind="conversa") == []  # autoevolução não aprende com visita
        assert akemi.memory.recent(5, kind="visita")
        assert any(k == "visita" for _, k, _ in akemi.mind.ambient)  # ele fica sabendo depois
    finally:
        await akemi.stop()


@pytest.mark.asyncio
async def test_guest_saying_yes_does_not_confirm_owner_action(tmp_path):
    from akemi.actions.registry import ActionCall, Tier

    akemi, tts, _ = make_akemi(tmp_path, "Só o Mateus confirma isso.")
    closed = []
    akemi.actions.register("fechar", "fecha", tier=Tier.CONFIRM)(lambda: closed.append(1))
    await akemi.start()
    try:
        await akemi.actions.request(ActionCall("fechar", {}), Trust.OWNER)
        akemi.bus.publish(Event("voice.guest", {"text": "sim", "quem": "incerto"}, source="mic", trust=Trust.PUBLIC))
        await akemi.bus.drain()
        await asyncio.sleep(0.2)
        assert closed == [] and akemi.actions.pending is not None
    finally:
        await akemi.stop()


# =================================================================== chip
def test_voice_travels_in_the_chip(tmp_path):
    old, new = tmp_path / "velho", tmp_path / "novo"
    old.mkdir()
    owner_print().save(old / VOICE_FILE)
    chip = tmp_path / "a.chip"
    export_chip(chip, data_dir=old, persona_file=tmp_path / "x.yaml", owner="Mateus")
    import_chip(chip, data_dir=new)
    assert VoicePrint.load(new / VOICE_FILE).samples == 8


def test_chip_with_broken_voice_is_refused(tmp_path):
    data = tmp_path / "d"
    data.mkdir()
    owner_print().save(data / VOICE_FILE)
    chip = tmp_path / "a.chip"
    export_chip(chip, data_dir=data, persona_file=tmp_path / "x.yaml", owner="Mateus")
    # Troca o voz.json por um inválido e conserta o hash, como faria alguém mexendo no zip.
    import hashlib

    bad = b'{"versao": 1, "centro": [1, 2], "aceitar": 0.1, "recusar": 0.5}'
    with zipfile.ZipFile(chip) as zf:
        items = {n: zf.read(n) for n in zf.namelist()}
    manifest = json.loads(items["manifest.json"])
    manifest["arquivos"][VOICE_FILE] = hashlib.sha256(bad).hexdigest()
    items["manifest.json"], items[VOICE_FILE] = json.dumps(manifest).encode(), bad
    with zipfile.ZipFile(chip, "w") as zf:
        for n, b in items.items():
            zf.writestr(n, b)
    with pytest.raises(ChipError, match="voz"):
        import_chip(chip, data_dir=tmp_path / "novo")


# ============================================== com o modelo de verdade (opcional)
@pytest.mark.skipif(not os.environ.get("AKEMI_VOICE_MODEL"), reason="defina AKEMI_VOICE_MODEL para testar o modelo real")
def test_real_model_loads_and_embeds():
    from akemi.audio.voice_id import SherpaEmbedder

    emb = SherpaEmbedder(os.environ["AKEMI_VOICE_MODEL"])
    tone = (np.sin(np.linspace(0, 2000, 32000)) * 0.3).astype(np.float32)
    v = emb(tone)
    assert v.ndim == 1 and v.size >= 64 and np.isfinite(v).all()
