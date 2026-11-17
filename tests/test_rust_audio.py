"""O núcleo de áudio em Rust tem que dar o mesmo resultado do Python, byte a byte.

Só roda com o ``akemi_audio_rs`` instalado (``pip install ./rust-core/akemi-audio``);
sem ele os testes são pulados e a Akemi segue no Python.
"""

import importlib

import numpy as np
import pytest

from akemi.audio import process

rs = pytest.importorskip("akemi_audio_rs")


def _noise(n: int, seed: int, scale: float = 9000.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = rng.normal(0, scale, n)
    extremes = [32767, -32768, 32767, -32768][:n]  # extremos também
    x[: len(extremes)] = extremes
    return np.clip(x, -32768, 32767).astype(np.int16)


def _speechlike(rate: int, seconds: float = 3.0, seed: int = 0) -> np.ndarray:
    """Voz sintética: harmônicos com entonação, sílabas, pausas e um trecho de chiado."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(rate * seconds)) / rate
    f0 = 140 + 60 * np.sin(2 * np.pi * 0.7 * t)
    phase = 2 * np.pi * np.cumsum(f0) / rate
    voiced = sum(np.sin(k * phase) / k for k in range(1, 12))
    env = np.clip(np.sin(2 * np.pi * 3.5 * t), 0, None) ** 0.6
    gate = np.sin(2 * np.pi * 0.5 * t) > -0.3
    x = voiced * env * gate * 8000 + rng.normal(0, 300, len(t))
    x[-rate // 2 :] = rng.normal(0, 6000, rate // 2)
    return np.clip(x, -32768, 32767).astype(np.int16)


@pytest.mark.parametrize("channels", [1, 2, 6])
def test_downmix_matches_python(channels):
    pcm = _noise(4096 * channels + 1, seed=channels)  # sobra uma amostra solta no fim
    expected = process.downmix_to_mono_int16(pcm.tobytes(), channels)
    assert rs.downmix_to_mono_int16(pcm.tobytes(), channels) == expected.tobytes()


def test_downmix_rejects_bad_input():
    with pytest.raises(ValueError):
        rs.downmix_to_mono_int16(b"\x00\x00", 0)
    with pytest.raises(ValueError):
        rs.downmix_to_mono_int16(b"\x00\x00\x00", 1)  # meia amostra


@pytest.mark.parametrize("src", [8000, 16000, 22050, 44100, 48000, 96000])
def test_resample_matches_python(src):
    x = _noise(5000, seed=src)
    carry = _noise(3, seed=1)
    out, left = process.resample_int16(x, src, 16000, carry)
    rout, rleft = rs.resample_int16(x.tobytes(), src, 16000, carry.tobytes())
    assert rout == out.tobytes()
    assert rleft == left.tobytes()


@pytest.mark.parametrize("src", [44100, 48000])
def test_stream_resampler_matches_python_in_random_chunks(src):
    x = _noise(src, seed=7)
    py, rust = process.StreamResampler(src, 16000), rs.StreamResampler(src, 16000)
    sizes = np.random.default_rng(3).integers(1, 1500, 200)
    pos = 0
    for size in sizes:
        chunk = x[pos : pos + size]
        pos += size
        assert rust.push(chunk.tobytes()) == py.push(chunk).tobytes()


def test_frame_assembler_matches_python():
    py, rust = process.FrameAssembler(640), rs.FrameAssembler(640)
    data = _noise(20000, seed=2).tobytes()
    for start in range(0, len(data), 777):
        assert rust.push(data[start : start + 777]) == py.push(data[start : start + 777])
    assert rust.pending == len(py._buf)
    with pytest.raises(ValueError):
        rs.FrameAssembler(0)


@pytest.mark.parametrize(("rate", "channels"), [(48000, 2), (44100, 2), (16000, 1), (48000, 8), (96000, 2)])
def test_pipeline_matches_python_byte_for_byte(rate, channels):
    """O caminho inteiro de um callback, com blocos de tamanhos variados."""
    x = _noise(rate * channels * 2, seed=rate + channels)  # 2 s
    py = process.AudioPipeline(rate, channels)
    rust = rs.AudioPipeline(rate, channels)
    assert rust.frame_bytes == py.frame_bytes == 640
    frame_bytes = 2 * channels
    sizes = np.random.default_rng(rate).integers(64, 2048, 400) * frame_bytes
    data = x.tobytes()
    pos = 0
    for size in sizes:
        chunk = data[pos : pos + size]
        pos += size
        assert rust.push(chunk) == py.push(chunk)
    assert rust.frames_out == py.frames_out > 0


def test_pipeline_clear_and_errors():
    p = rs.AudioPipeline(48000, 2)
    p.push(b"\x01\x00" * 1000)
    p.clear()
    fresh = rs.AudioPipeline(48000, 2)
    block = _noise(4096, seed=9).tobytes()
    assert p.push(block) == fresh.push(block)
    with pytest.raises(ValueError):
        rs.AudioPipeline(48000, 0)
    with pytest.raises(ValueError):
        p.push(b"\x00")


@pytest.mark.parametrize("rate", [8000, 16000, 32000, 48000])
@pytest.mark.parametrize("mode", [0, 1, 2, 3])
def test_vad_matches_webrtcvad(rate, mode):
    webrtcvad = pytest.importorskip("webrtcvad")
    py, rust = webrtcvad.Vad(mode), rs.Vad(mode, rate)
    x = _speechlike(rate, seed=mode)
    n = rate * 20 // 1000
    decisions = []
    for i in range(0, len(x) - n, n):
        frame = x[i : i + n].tobytes()
        expected = py.is_speech(frame, rate)
        assert rust.is_speech(frame) == expected, f"quadro {i // n}"
        decisions.append(expected)
    assert any(decisions) and not all(decisions)  # o sinal testa os dois lados


def test_vad_rejects_bad_frames_and_settings():
    v = rs.Vad(2, 16000)
    assert v.is_speech(b"\x00" * 640) is False
    with pytest.raises(ValueError):
        v.is_speech(b"\x00\x01")
    with pytest.raises(ValueError):
        v.is_speech(b"")
    with pytest.raises(ValueError):
        rs.Vad(4)
    with pytest.raises(ValueError):
        rs.Vad(2, 22050)
    # Taxa diferente por chamada, como no webrtcvad.
    assert v.is_speech(b"\x00" * 960, 48000) is False
    assert v.sample_rate == 48000


@pytest.fixture
def native(monkeypatch):
    """O módulo de escolha, recarregado; no fim volta ao estado do ambiente original."""
    from akemi.audio import native as mod

    monkeypatch.delenv("AKEMI_RUST", raising=False)
    importlib.reload(mod)
    yield mod
    monkeypatch.undo()
    importlib.reload(mod)


def test_akemi_uses_rust_when_installed_and_python_when_disabled(native, monkeypatch):
    from akemi.audio.vad import VAD

    assert native.BACKEND == "rust"
    assert type(native.make_pipeline(48000, 2)).__module__ == "akemi_audio_rs"
    vad = VAD(2)
    assert vad.backend == "rust"
    vad.is_speech(b"\x00" * 640)
    assert (vad.frames, vad.speech_frames, vad.speech_ratio) == (1, 0, 0.0)

    monkeypatch.setenv("AKEMI_RUST", "0")
    importlib.reload(native)
    assert native.BACKEND == "python"
    assert isinstance(native.make_pipeline(48000, 2), process.AudioPipeline)
    assert VAD(2).backend == "python"
