⚠️ **Privacidade e consentimento:** a Akemi pode capturar áudio do sistema e
observar conteúdo da tela. Use somente em dispositivos e contextos nos quais
você tenha autorização e pause a captura quando necessário. Nunca publique
bases de dados (`data/`), logs, transcrições, screenshots ou arquivos `.env`.

# Akemi

Agente de IA autônomo e open-source para Windows, no estilo E.D.I.T.H. com a
presença de uma VTuber de IA: roda em segundo plano, percebe o que acontece no
computador, conversa por voz, responde o chat da live e decide sozinha quando
vale a pena falar ou agir.

Ela não é um chatbot de pergunta e resposta. Tem **estado interno** (humor,
objetivo, tarefas, pensamentos recentes), **memória** de longo prazo e um
**filtro de atenção** que escolhe entre ignorar, observar, responder com um
modelo rápido ou pensar com um modelo grande.

Arquitetura completa: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## O que já funciona

- Barramento de eventos assíncrono com nível de confiança por origem
- Cérebro com dois modelos (rápido + profundo), respostas em streaming frase a frase
- Providers: **NVIDIA NIM**, OpenAI, Ollama, servidor llama.cpp, Anthropic (só `httpx`, sem SDKs)
- Personalidade editável (`akemi/brain/persona.yaml`)
- Estado interno persistente com humor que muda e decai
- Pensamento ocioso: ela reflete sozinha e decide se fala algo
- Memória SQLite (conversas + fatos) com busca por relevância
- Ações com permissão: livres, com confirmação por voz, ou proibidas
- Chat da Twitch (sem dependências, modo anônimo), com agrupamento, seleção, cooldown e limite
- Moderação de entrada e saída (ofensas, segredos, links, tentativas de manipulação)
- Gatilhos para comentários espontâneos (`akemi/brain/triggers.yaml`)
- Captura de áudio do sistema (WASAPI loopback) → VAD → segmentação → Whisper
- **Voz local com Piper** (pt-BR, offline), interrompível, com escolha da saída de áudio
- **Microfone**: fale "Akemi, ..." e ela responde; interrompa falando por cima dela
- **Visão**: ela lê a tela (OCR) quando algo muda e, com um modelo de visão, entende jogos e vídeos
- Modo console para conversar sem áudio

**Ainda falta:** integração com o Hermes, avatar e self-improve.

## Instalação

Requisitos: Python 3.11+. Windows 10/11 para captura de áudio.

```powershell
git clone https://github.com/Mateus0790/Akemi.git
cd Akemi
python -m venv venv
venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env   # coloque sua NVIDIA_API_KEY
```

## Conversar pelo terminal

```powershell
akemi            # usa o provider do .env
akemi --echo     # sem API key, para testar o fluxo
```

Comandos no console:

| Comando | O que faz |
|---|---|
| `qualquer texto` | fala com ela como dono |
| `/chat <nick> <msg>` | simula uma mensagem do chat da live |
| `/olhar` | faz ela olhar a tela agora (com `--tela`) |
| `/tela <texto>` | simula algo visto na tela |
| `/audio <texto>` | simula algo ouvido no PC |
| `/pensar` | força um momento de pensamento |
| `/estado` | mostra humor, objetivo e tarefas |
| `/pausar`, `/voltar` | pausa/retoma a autonomia |

## Voz

```powershell
pip install -e ".[tts]"
python -m piper.download_voices pt_BR-faber-medium --data-dir data/voices
akemi --voz
```

Para deixar a voz ligada sempre, coloque `TTS_ENGINE=piper` no `.env`. Outras
vozes pt-BR: `pt_BR-cadu-medium`, `pt_BR-jeff-medium`, `pt_BR-edresson-low`
(troque também `PIPER_MODEL_PATH`). `PIPER_LENGTH_SCALE` ajusta a velocidade.

Para a live, aponte `TTS_OUTPUT_DEVICE` para um cabo virtual (ex.: VB-Cable) e
capture esse dispositivo no OBS. Enquanto ela fala, o áudio do sistema não é
transcrito, para ela não "ouvir a si mesma".

## Microfone

```powershell
pip install -e ".[mic,tts]"
akemi --listar-mics          # se o padrão do Windows não for o mic certo
akemi --voz --mic
```

Diga **"Akemi, ..."** e ela responde. Nos 20 segundos seguintes dá para
continuar a conversa sem repetir o nome. Falar mais alto por cima dela a
interrompe. Para ela ouvir tudo sem precisar do nome, use `MIC_MODE=always`.

O que você fala com outras pessoas, sem o nome dela, é descartado na hora e não
vai para a memória. Ela também ignora a própria voz vazando da caixa de som e
as frases que o Whisper inventa no silêncio ("obrigado por assistir"). Mesmo
assim, **com fone de ouvido funciona bem melhor**.

## Visão

```powershell
pip install -e ".[vision]"
akemi --tela
```

O OCR usa o **Tesseract**, que é instalado à parte: baixe o instalador do
UB Mannheim (github.com/UB-Mannheim/tesseract/wiki) e marque o idioma
**Portuguese**. A pasta padrão é encontrada sozinha; se instalar em outro
lugar, defina `TESSERACT_CMD`.

Se o Tesseract foi instalado sem o português, dá para baixar os idiomas na
pasta do projeto (não precisa de administrador):

```powershell
mkdir data\tessdata
foreach ($l in "por","eng","osd") { curl.exe -sSL -o data\tessdata\$l.traineddata https://github.com/tesseract-ocr/tessdata_fast/raw/main/$l.traineddata }
```

A cada `SCREEN_INTERVAL_S` ela tira um print, mas só lê quando a tela muda de
verdade e para de mudar, ou quando você troca de janela. Num vídeo em tela
cheia ela não fica lendo sem parar. O que ela vê entra no contexto dela, e
erros ou um "game over" podem gerar um comentário espontâneo
(`akemi/brain/triggers.yaml`). No console, `/olhar` mostra o que ela está vendo.

O OCR só lê texto. Para ela entender jogos, vídeos e imagens, configure um
modelo de visão em `VISION_MODEL` (ex.: `meta/llama-3.2-11b-vision-instruct`
no NIM, ou `qwen2.5vl` no Ollama). Ele é chamado no máximo a cada
`VISION_DESCRIBE_INTERVAL_S`.

**Privacidade:** ela não lê janelas cujo título tenha "senha", "password",
gerenciadores de senha, "banco" ou aba anônima (`SCREEN_IGNORE_WINDOWS`
substitui essa lista). Prints não são salvos em disco. O texto lido vai para o
provider do LLM, então com provider na nuvem ele sai do PC. `/pausar`
desliga a visão na hora.

Para ligar o chat da Twitch, preencha `TWITCH_CHANNEL` no `.env`. Para ouvir o
áudio do PC: `pip install -e ".[audio,stt]"` e `SYSTEM_AUDIO_ENABLED=true`.

## Segurança: o chat nunca manda nela

Tudo que vem de fora (chat, tela, áudio do PC) entra como `Trust.PUBLIC`. A
checagem de permissão usa a origem do evento, não o que o modelo escreveu, então
mesmo que um viewer convença o LLM a pedir uma ação, ela é negada em código.
Detalhes em [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#segurança).

## Debug de áudio

```powershell
pip install -e ".[audio]"
akemi-debug
```

Abre o loopback dos alto-falantes, converte para PCM 16 kHz mono e imprime
`Speech detected` quando o VAD dispara.

## Testes

```powershell
pytest
```

Os testes não precisam de rede, API key nem placa de som (usam um LLM falso).

## Self-improve (planejado)

Módulo opcional que analisa erros dos logs, gera um patch, roda os testes numa
worktree isolada e abre um Pull Request com descrição sanitizada (no máximo 1 por
dia, com rollback automático). Mesmo com cérebro 100% local, ele publicaria PRs
públicos — por isso fica desligado por padrão.

## Contribuindo

Veja [CONTRIBUTING.md](CONTRIBUTING.md).

## Licença

Apache 2.0 — veja [LICENSE](LICENSE).
