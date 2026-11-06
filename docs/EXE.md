# Akemi.exe: a Akemi sem instalar Python

O `Akemi.exe` é a Akemi inteira empacotada com o PyInstaller: Python, voz
(Fish Audio, Piper e Kokoro), microfone, Whisper, visão, áudio do PC, corpo e servidor. É
só baixar, extrair e abrir.

## Baixar

- **Versões marcadas:** página de [Releases](https://github.com/Mateus0790/Akemi/releases),
  arquivo `Akemi-windows.zip`.
- **A mais nova da `main`:** Actions → **Akemi.exe** → a execução mais recente →
  Artifacts → **Akemi-windows**.

Extraia o zip numa pasta sua (ex. `C:\Akemi`), não dentro de `Arquivos de
Programas`: ela grava o `.env` e a pasta `data/` ao lado do `.exe`.

## Primeira vez

1. Abra o `Akemi.exe`. Ela cria o `.env` na mesma pasta e fecha.
2. Abra o `.env` no Bloco de Notas e coloque a sua `NVIDIA_API_KEY` (ou outro
   provider). Tudo o que está no [README](../README.md) sobre o `.env` vale igual.
3. Abra o `Akemi.exe` de novo.

Para testar sem chave: `Akemi.exe --echo` num terminal aberto na pasta.

O Windows SmartScreen pode avisar "o Windows protegeu o computador", porque o
`.exe` não é assinado. Clique em **Mais informações → Executar assim mesmo**.

## Os mesmos comandos de sempre

| Antes (com Python) | Com o .exe |
|---|---|
| `akemi --voz --mic --tela` | `Akemi.exe --voz --mic --tela` |
| `akemi-corpo --voz --mic` | `Akemi.exe corpo --voz --mic` |
| `akemi-servidor` | `Akemi.exe servidor` |
| `akemi-chip exportar chip.akemi` | `Akemi.exe chip exportar chip.akemi` |
| `akemi-debug` | `Akemi.exe debug` |
| `akemi --casca claude` | `Akemi.exe --casca claude` |

Dica: crie um atalho do `Akemi.exe` e, em **Propriedades → Destino**, ponha as
opções no fim (ex. `C:\Akemi\Akemi.exe --voz --mic`). A pasta de trabalho é
sempre a do `.exe`, então o atalho pode ficar em qualquer lugar.

Para guardar o `.env` e a `data/` em outra pasta, defina a variável de ambiente
`AKEMI_HOME`.

## O que não vem dentro

São arquivos grandes ou seus, e ficam na pasta ao lado do `.exe`, como no README:

- **Fish Audio** (voz principal): não tem nada para baixar, só a `FISH_API_KEY`
  e o `FISH_VOICE_ID` no `.env`.
- **Modelos de voz locais** (`data/voices/...`, Piper e Kokoro, que também são a
  voz reserva do Fish Audio): os mesmos comandos de download do README.
  O `python -m piper.download_voices` precisa de Python; sem ele, baixe o `.onnx`
  e o `.onnx.json` da voz direto do
  [Hugging Face do Piper](https://huggingface.co/rhasspy/piper-voices/tree/main/pt/pt_BR)
  para `data/voices/`.
- **Tesseract** (OCR da tela): instalador à parte, igual ao README.
- **O Whisper** baixa o modelo sozinho na primeira vez que ouve algo.
- **Oficina** (ela abrir PR no próprio código): precisa de um clone do
  repositório, do Git e de um Python com `pytest` no PC, porque ela roda os
  testes antes de abrir o PR.

## Atualizar

Baixe o zip novo e extraia por cima. O `.env` e a `data/` (memória, humor,
lembretes, voz cadastrada) não estão no zip, então nada se perde. Se quiser,
faça antes um chip: `Akemi.exe chip exportar backup.akemi`.

## Gerar o .exe você mesmo

No Windows, com Python 3.11+:

```powershell
python -m venv venv
venv\Scripts\activate
pip install ".[exe,audio,stt,mic,vision,tts,kokoro,fish,voz,arquivos,servidor,obs,windows]"
pyinstaller packaging/akemi.spec --noconfirm
```

Sai em `dist\Akemi\`. Só entra o que estiver instalado no venv: deixe um extra
de fora e o `.exe` fica menor, sem aquele recurso. O GitHub Actions faz isso
sozinho a cada mudança (`.github/workflows/exe.yml`) e testa o `.exe` antes de
publicar.

É uma pasta (`Akemi.exe` + `_internal`) e não um arquivo único de propósito:
com tantas DLLs (onnxruntime, Whisper), o arquivo único teria de se extrair
inteiro a cada vez que abre, e demoraria bem mais.
