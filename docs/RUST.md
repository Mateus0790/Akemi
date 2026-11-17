# Núcleo em Rust (opcional)

O `rust-core/` tem uma versão em Rust do caminho quente do áudio: o que roda a
cada callback do PortAudio (downmix → resample para 16 kHz → quadros de 20 ms)
e o VAD. É **opcional**: se o módulo `akemi_audio_rs` estiver instalado, a
captura do áudio do PC, o microfone e o VAD usam ele; se não estiver, tudo
segue em Python como sempre. Nenhuma outra parte da Akemi muda.

O resultado é **o mesmo, byte a byte**. Os testes em `tests/test_rust_audio.py`
comparam os dois em downmix, resample (6 taxas), fluxo em blocos de tamanho
aleatório, o pipeline inteiro (5 configurações de dispositivo) e o VAD (4 modos
× 4 taxas contra o `webrtcvad`).

## Os números de verdade

Medido com `python scripts/benchmark_audio.py` (callback padrão: 1024 amostras
estéreo a 48 kHz = 21,3 ms de áudio):

| Etapa | Python | Rust | Rust mais rápido |
|---|---|---|---|
| Pipeline (downmix + resample + quadros) | 43 µs | 7 µs | ~6× |
| Callback completo (pipeline + VAD) | 48 µs | 16 µs | ~3× |
| Fração de 1 núcleo da CPU | 0,23% | 0,07% | |

O Rust é 3 a 6 vezes mais rápido, mas o Python **já gastava menos de 0,25% da
CPU** nessa parte. Você não vai sentir diferença na latência da Akemi: o tempo
de resposta vem do Whisper (centenas de ms), do modelo de linguagem e do TTS,
que já rodam em código nativo (CTranslate2, onnxruntime) ou na nuvem.

Rode o benchmark no seu PC para ver os seus números.

## Instalar no Windows

### Jeito fácil: wheel pronto (não precisa do Rust)

1. GitHub → **Actions** → **Núcleo Rust** → a execução mais recente →
   **Artifacts** → `akemi-audio-rs-windows`.
2. Descompacte e instale no mesmo Python da Akemi:

   ```powershell
   pip install akemi_audio_rs-0.1.0-cp311-abi3-win_amd64.whl
   ```

O wheel serve para o Python 3.11 em diante.

### Compilando você mesmo

1. Instale o Rust: <https://rustup.rs> (`rustup-init.exe`). Quando ele pedir o
   **Visual Studio Build Tools** (C++), aceite: o VAD tem uma parte em C.
2. Feche e abra o terminal, e confira: `cargo --version`.
3. Na pasta da Akemi:

   ```powershell
   pip install .\rust-core\akemi-audio
   ```

   A primeira compilação leva uns 2 minutos. Não precisa de venv nem do
   `maturin develop`: o `pip` usa o maturin sozinho.

No Linux/macOS é igual: `pip install ./rust-core/akemi-audio`.

## Conferir se está usando

```powershell
python -c "from akemi.audio import native; print(native.describe())"
# {'backend': 'rust', 'version': '0.1.0'}

python scripts/benchmark_audio.py      # Python × Rust no seu PC
pytest tests/test_rust_audio.py        # Rust igual ao Python
```

Nos logs, a captura mostra `audio_core=rust` e o VAD mostra `backend=rust`.

## Desligar

Sem desinstalar, para comparar ou depurar:

```powershell
$env:AKEMI_RUST = "0"      # PowerShell (cmd: set AKEMI_RUST=0)
```

É uma variável do sistema, não do `.env`. Para tirar de vez:
`pip uninstall akemi-audio-rs`.

## Como encaixa

```
PortAudio (bytes do dispositivo)
  └─ native.make_pipeline(...).push(bytes)     ← Rust ou Python
       downmix → resample 16 kHz → quadros de 20 ms
  └─ SpeechSegmenter
       └─ VAD.is_speech(quadro)                 ← motor: Rust ou webrtcvad
  └─ Whisper → Event no barramento
```

| Arquivo | Papel |
|---|---|
| `rust-core/akemi-audio/src/dsp.rs` | DSP puro em Rust, com testes próprios (`cargo test --no-default-features`). |
| `rust-core/akemi-audio/src/lib.rs` | Ligação com o Python (PyO3): `AudioPipeline`, `StreamResampler`, `FrameAssembler`, `Vad`, funções soltas. |
| `rust-core/akemi-audio/akemi_audio_rs.pyi` | Tipos para o editor. |
| `akemi/audio/native.py` | Escolhe Rust ou Python (`BACKEND`, `make_pipeline`, `make_vad_engine`). |
| `akemi/audio/process.py` | `AudioPipeline` em Python: a versão de reserva, com o mesmo contrato. |
| `tests/test_rust_audio.py` | Paridade Rust × Python (pulado se o módulo não estiver instalado). |
| `scripts/benchmark_audio.py` | Mede os dois e confere que dão o mesmo resultado. |
| `.github/workflows/rust.yml` | Compila no Windows e no Linux, testa e publica o wheel do Windows. |

O VAD em Rust usa o `libfvad`, que é o mesmo código do WebRTC VAD do pacote
`webrtcvad`, só separado do resto do WebRTC.

## Próximos passos (o que vale e o que não vale)

Medido antes de decidir:

- **Visão em Rust: não vale.** Miniatura + comparação de uma tela 2560×1440
  leva 1,3 ms em NumPy, e roda cerca de uma vez por segundo. A parte cara é o
  OCR (Tesseract), que já é C++.
- **Barramento de eventos em Rust: não vale.** Publicar e despachar um evento
  leva ~8 µs; o custo real são os handlers, que são corrotinas Python e
  continuariam sendo.
- **Onde a latência está de verdade:** tamanho e dispositivo do Whisper
  (`small` em CPU × GPU), tempo até o primeiro áudio do TTS e do modelo de
  linguagem. Vale medir a volta inteira (fim da sua fala → primeira sílaba
  dela) antes de reescrever qualquer outra parte.
- **Defeito pequeno encontrado no resample** (Python e Rust, de propósito
  iguais): a cada callback ele recomeça a interpolação do zero em vez de
  continuar a fase. Sai ~0,2% mais amostras do que deveria (o áudio fica 0,2%
  mais lento, imperceptível para o Whisper) e há um degrauzinho na emenda de
  cada bloco. A correção é guardar a fase fracionária entre chamadas; os
  testes de paridade garantem que as duas versões mudem juntas.
