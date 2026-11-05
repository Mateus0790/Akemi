"""Cadastro da voz do dono: ``akemi --cadastrar-voz``.

Você lê algumas frases em voz alta, no microfone de sempre e no lugar de
sempre (o cadastro aprende também como o seu microfone soa). Ela guarda só a
impressão da voz e os limites em ``data/voz.json``, nunca o áudio.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import numpy as np

from .voice_id import (
    MIN_ENROLL_SECONDS,
    VOICE_FILE,
    Embedder,
    VoiceError,
    VoicePrint,
    build_print,
    pcm_to_float,
    seconds,
)

# Frases variadas, do jeito que você fala com ela no dia a dia.
PHRASES = (
    "Akemi, abre o Spotify e coloca aquela playlist de sempre.",
    "Me lembra daqui a dez minutos de tirar a pizza do forno.",
    "Pausa a música que eu vou entrar numa chamada agora.",
    "Akemi, pesquisa no YouTube um tutorial de violão para iniciante.",
    "Você viu o que aconteceu no jogo ontem à noite? Foi muito doido.",
    "Abaixa o volume um pouquinho, por favor, tá muito alto.",
    "Guarda que eu prefiro café sem açúcar e bem forte de manhã.",
    "Hoje o dia foi cansativo, acho que vou dormir mais cedo.",
)

NextSegment = Callable[[float], Awaitable[bytes | None]]  # espera até N s por uma fala


async def enroll(
    next_segment: NextSegment,
    embed: Embedder,
    *,
    phrases: tuple[str, ...] = PHRASES,
    say: Callable[[str], None] = print,
    model: str = "",
    timeout_s: float = 25.0,
    retries: int = 2,
) -> VoicePrint:
    """Pede cada frase, confere se dá para usar e monta a impressão da voz."""
    embeddings: list[np.ndarray] = []
    for i, phrase in enumerate(phrases, 1):
        for attempt in range(retries + 1):
            say(f"\n[{i}/{len(phrases)}] Leia em voz alta:\n    \"{phrase}\"")
            pcm = await next_segment(timeout_s)
            if pcm is None:
                say("  (não ouvi nada)")
                continue
            if seconds(pcm) < MIN_ENROLL_SECONDS:
                say("  (curto demais: leia a frase inteira, no seu ritmo)")
                continue
            embeddings.append(await asyncio.to_thread(embed, pcm_to_float(pcm)))
            say("  ok")
            break
        else:
            say("  (pulando esta frase)")
    return build_print(embeddings, model)


async def run_enrollment(data_dir: Path, model_path: Path, mic_device: str | None, vad_level: int) -> int:
    """Versão de linha de comando, com o microfone de verdade."""
    from akemi.audio.mic import MicCapture
    from akemi.audio.vad import VAD
    from akemi.stt.transcriber import SpeechSegmenter

    from .voice_id import SherpaEmbedder

    try:
        embed = SherpaEmbedder(model_path)
    except VoiceError as exc:
        print(f"Não deu para começar: {exc}")
        return 1

    loop = asyncio.get_running_loop()
    segments: asyncio.Queue[bytes] = asyncio.Queue()
    segmenter = SpeechSegmenter(VAD(vad_level).is_speech, end_silence_ms=900)

    def on_frame(frame: bytes) -> None:  # thread do PortAudio
        seg = segmenter.push(frame)
        if seg is not None:
            loop.call_soon_threadsafe(segments.put_nowait, seg)

    async def next_segment(timeout: float) -> bytes | None:
        while not segments.empty():  # descarta o que veio antes da frase aparecer
            segments.get_nowait()
        try:
            return await asyncio.wait_for(segments.get(), timeout)
        except asyncio.TimeoutError:
            return None

    device: str | int | None = mic_device or None
    if isinstance(device, str) and device.isdigit():
        device = int(device)
    print(
        "Cadastro da sua voz.\n"
        "Fique no lugar de sempre, com o microfone de sempre, e leia cada frase no seu jeito\n"
        "normal de falar. Silêncio em volta ajuda. Ela guarda só a impressão da voz, não o áudio."
    )
    mic = MicCapture(device)
    mic.start(on_frame)
    try:
        vp = await enroll(next_segment, embed, model=embed.name)
    except VoiceError as exc:
        print(f"\nNão deu: {exc}")
        return 1
    finally:
        mic.stop()
    path = Path(data_dir) / VOICE_FILE
    vp.save(path)
    print(
        f"\nPronto! Voz cadastrada com {vp.samples} frases em {path}.\n"
        f"Ela aceita como sua a partir da nota {vp.accept:.2f} e trata como outra pessoa abaixo de {vp.reject:.2f}.\n"
        "Se ela não te reconhecer muito, cadastre de novo no lugar onde você mais usa."
    )
    return 0
