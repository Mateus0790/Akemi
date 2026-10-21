# Arquitetura da Akemi

A ideia: sair do modelo "pergunta → resposta" (chatbot) para um **agente contínuo**
com estado interno, que percebe o que acontece, decide sozinho quando agir e
mantém tarefas em segundo plano — estilo E.D.I.T.H., com a presença de uma
VTuber de IA.

```
             PERCEPÇÃO                          MENTE                        SAÍDA
 ┌───────────────────────────┐      ┌───────────────────────────┐   ┌──────────────────┐
 │ voz/console do dono  OWNER│─┐    │  AttentionFilter          │   │ Speaker          │
 │ chat Twitch         PUBLIC│─┤    │  ignorar / observar /     │   │  moderação final │
 │ áudio do PC (STT)   PUBLIC│─┼──► │  considerar / rápido /    │──►│  fila + interrupção
 │ tela (OCR)          PUBLIC│─┤ Bus│  profundo                 │   │  TTS             │
 │ timer de pensamento SYSTEM│─┘    │        │                  │   └──────────────────┘
 └───────────────────────────┘      │  Persona + InnerState     │   ┌──────────────────┐
                                    │  + Memória + contexto     │──►│ ActionRegistry   │
                                    │        │                  │   │  checa Trust     │
                                    │  LLM rápido | LLM grande  │   │  FREE / CONFIRM  │
                                    └───────────────────────────┘   └──────────────────┘
```

## Módulos

| Módulo | Papel |
|---|---|
| `core/events.py` | Barramento pub/sub assíncrono. Cada evento carrega `Trust` (OWNER, SYSTEM, PUBLIC). |
| `core/orchestrator.py` | Monta tudo e roda as tasks (barramento, voz, chat, pensamento ocioso, áudio). |
| `brain/attention.py` | Decide se e com qual modelo reagir. É o que resolve a latência: o 70B só entra quando vale. |
| `brain/mind.py` | Monta o prompt, gera em streaming, fala frase por frase, executa ações, pensa quando ociosa. |
| `brain/state.py` | Humor (com decaimento), objetivo atual, tarefas, últimos pensamentos. Persiste em `data/state.json`. |
| `brain/persona.yaml` | Personalidade editável. |
| `brain/triggers.yaml` | Quando algo percebido merece ser considerado para comentário espontâneo. |
| `actions/` | Ações com níveis de permissão. |
| `live/` | Chat da Twitch (IRC), agrupamento, seleção e limite de respostas. |
| `safety/moderation.py` | Filtro de entrada (chat) e de saída (tudo que vira voz). |
| `storage/memory.py` | Memória episódica (SQLite FTS5) + fatos. |
| `stt/` | Segmentação de fala pelo VAD + Whisper. |
| `tts/base.py` | `Speaker`: único caminho para falar; interrompe quando o dono começa a falar. |

## Dois modelos

- **Rápido** (`FAST_MODEL`, ex. Llama 3.1 8B): triagem, chat da live, comentários,
  pensamento ocioso, respostas simples ao dono.
- **Profundo** (`DEEP_MODEL`, ex. Llama 3.3 70B): só quando o dono pede algo
  complexo (`looks_complex` — explicar, planejar, código, perguntas longas).

Respostas saem em streaming e são cortadas em frases (`SentenceStreamer`), então
o TTS começa a falar antes do modelo terminar.

## "Consciência"

Não é consciência real; é continuidade. O que dá essa sensação:

1. **Estado interno** que vai em todo prompt (humor, objetivo, tarefas, pensamentos recentes).
2. **Pensamento ocioso** (`tick.idle`): a cada `IDLE_THINK_INTERVAL_S` ela reflete,
   ajusta o humor e *decide* se fala algo — na maioria das vezes fica quieta.
3. **Memória** de conversas e fatos, recuperada por relevância.
4. **Humor** muda com as interações (`[[humor:X]]`) e decai com o tempo.

## Segurança

**Regra de ouro: conteúdo vindo de fora nunca dispara ação.**

- A confiança é do **evento de origem**, não do texto do modelo. Uma resposta
  gerada a partir do chat (`Trust.PUBLIC`) passa pelo `ActionRegistry` e é
  negada, mesmo que o modelo tenha sido convencido a pedir a ação. Isso é
  verificado em código (`tests/test_mind_integration.py`).
- Tela e áudio do PC também são `PUBLIC` — podem conter texto de terceiros
  (prompt injection numa página, num vídeo).
- Ações `CONFIRM` precisam de "sim" do dono; ninguém mais pode confirmar.
- `abrir_programa` só abre itens de uma lista permitida.
- Toda fala passa pela moderação de saída: remove chaves de API, e-mails, CPF,
  links e markdown; bloqueia ofensas.
- O chat é agrupado, deduplicado, com cooldown por usuário e limite por minuto.

## Protocolo de ações

O modelo pede ações com tags no texto, que nunca são faladas:

```
Pode deixar! [[acao:lembrar {"texto": "o Mateus prefere café sem açúcar"}]]
```

Para criar uma ação nova:

```python
@registry.register("tocar_musica", "toca uma música no Spotify",
                   tier=Tier.CONFIRM, params={"nome": "música"})
async def tocar_musica(nome: str) -> str:
    ...
```

## Próximos passos

1. TTS de verdade (Piper local ou serviço de voz) implementando `TTSEngine`.
2. Microfone do dono → `owner.message` (hoje o áudio capturado é o *loopback*,
   ou seja, o que sai das caixas de som — por isso entra como `ambient.audio`).
3. Visão: diferença de quadros + OCR → `ambient.screen`.
4. Hermes: mover chat, timers e memória para o servidor (24/7) e conversar com
   a Akemi pelo barramento via LAN.
5. Busca semântica (embeddings) na memória.
6. Avatar (VTube Studio API) reagindo ao humor.
7. Self-improve por último.
