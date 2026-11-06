"""O que cada parte precisa instalado. O núcleo no Docker só tem o extra [servidor]."""

import subprocess
import sys

# Pacotes dos extras de áudio, voz e visão: nenhum vem na imagem do servidor.
OPTIONAL = ["webrtcvad", "pyaudiowpatch", "sounddevice", "faster_whisper", "ctranslate2", "sherpa_onnx",
            "piper", "kokoro_onnx", "mss", "PIL", "pytesseract"]


def test_server_starts_without_audio_extras():
    code = (
        "import sys\n"
        f"for name in {OPTIONAL!r}:\n"
        "    sys.modules[name] = None  # importar vira ImportError, como se não estivesse instalado\n"
        "import akemi.net.server\n"
        "import akemi.audio.voice_id\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
