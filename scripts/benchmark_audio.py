"""Compara o núcleo de áudio em Python com o em Rust (``akemi_audio_rs``).

    python scripts/benchmark_audio.py

Mede o que roda a cada callback do PortAudio: downmix + resample + quadros de
20 ms + VAD em cada quadro. Também confere que os dois dão o mesmo resultado.
Sem o Rust instalado, mede só o Python e diz como instalar (docs/RUST.md).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from akemi.audio.process import AudioPipeline  # noqa: E402

try:
    import akemi_audio_rs as rs
except ImportError:
    rs = None


def _signal(rate: int, channels: int, seconds: float) -> np.ndarray:
    rng = np.random.default_rng(0)
    t = np.arange(int(rate * seconds)) / rate
    voice = sum(np.sin(2 * np.pi * 150 * k * t) / k for k in range(1, 10))
    voice *= np.clip(np.sin(2 * np.pi * 3 * t), 0, None) * 8000
    mono = voice + rng.normal(0, 400, len(t))
    return np.clip(np.repeat(mono[:, None], channels, axis=1), -32768, 32767).astype(np.int16)


def _python_vad(mode: int):
    import webrtcvad

    return webrtcvad.Vad(mode)


def _time(fn: Callable[[], object]) -> float:
    """Microssegundos por chamada (melhor de 5 rodadas)."""
    fn()
    n, start = 0, time.perf_counter()
    while time.perf_counter() - start < 0.05:
        fn()
        n += 1
    best = float("inf")
    for _ in range(5):
        t0 = time.perf_counter()
        for _ in range(n):
            fn()
        best = min(best, (time.perf_counter() - t0) / n)
    return best * 1e6


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rate", type=int, default=48000, help="taxa do dispositivo (padrão 48000)")
    ap.add_argument("--channels", type=int, default=2, help="canais do dispositivo (padrão 2)")
    ap.add_argument("--block", type=int, default=1024, help="amostras por callback (padrão 1024)")
    args = ap.parse_args()

    block_ms = args.block / args.rate * 1000
    audio = _signal(args.rate, args.channels, 4.0)
    blocks = [audio[i : i + args.block].tobytes() for i in range(0, len(audio) - args.block, args.block)]
    print(f"Callback: {args.block} amostras x {args.channels} canais a {args.rate} Hz = {block_ms:.1f} ms de áudio")
    print(f"Núcleo Rust: {'akemi_audio_rs ' + rs.__version__ if rs else 'NÃO instalado (veja docs/RUST.md)'}\n")

    def runner(pipeline_cls, vad) -> Callable[[], object]:
        pipeline = pipeline_cls(args.rate, args.channels)
        state = {"i": 0}

        def step() -> int:
            block = blocks[state["i"] % len(blocks)]
            state["i"] += 1
            speech = 0
            for frame in pipeline.push(block):
                speech += vad.is_speech(frame, 16000)
            return speech

        return step

    rows = []
    py_pipe = AudioPipeline(args.rate, args.channels)
    rows.append(("pipeline (sem VAD)", "Python", _time(lambda: py_pipe.push(blocks[0]))))
    try:
        rows.append(("callback completo", "Python", _time(runner(AudioPipeline, _python_vad(2)))))
    except ImportError:
        print("(webrtcvad não instalado: pulando o VAD em Python)")
    if rs is not None:
        rs_pipe = rs.AudioPipeline(args.rate, args.channels)
        rows.append(("pipeline (sem VAD)", "Rust", _time(lambda: rs_pipe.push(blocks[0]))))
        rows.append(("callback completo", "Rust", _time(runner(rs.AudioPipeline, rs.Vad(2)))))

    print(f"{'etapa':<22}{'núcleo':<9}{'por callback':>14}{'% do tempo real':>18}")
    for name, core, us in rows:
        print(f"{name:<22}{core:<9}{us:>11.1f} µs{us / (block_ms * 1000) * 100:>17.3f}%")

    by = {(n, c): us for n, c, us in rows}
    for name in ("pipeline (sem VAD)", "callback completo"):
        if (name, "Python") in by and (name, "Rust") in by:
            print(f"\n{name}: Rust {by[(name, 'Python')] / by[(name, 'Rust')]:.1f}x mais rápido", end="")
    print()

    if rs is not None:
        py_p, rs_p = AudioPipeline(args.rate, args.channels), rs.AudioPipeline(args.rate, args.channels)
        same = all(py_p.push(b) == rs_p.push(b) for b in blocks)
        print(f"\nMesmo resultado byte a byte nos {len(blocks)} blocos: {'sim' if same else 'NÃO'}")
        if not same:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
