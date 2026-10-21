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
- Modo console para conversar sem áudio

**Ainda falta:** microfone do dono, visão/OCR, integração com o Hermes, avatar
e self-improve.

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
