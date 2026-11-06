⚠️ **Privacidade e consentimento:** a Akemi pode capturar áudio do sistema e
observar conteúdo da tela. Use somente em dispositivos e contextos nos quais
você tenha autorização e pause a captura quando necessário. Nunca publique
bases de dados (`data/`), logs, transcrições, screenshots ou arquivos `.env`.

# Akemi

Agente de IA autônomo e open-source para Windows, inspirado nas IAs do Homem
de Ferro (J.A.R.V.I.S., F.R.I.D.A.Y., E.D.I.T.H.) e no Projeto Capricórnio de
*Cyberpunk: Edgerunners 2*, com a presença de uma VTuber de IA: roda em segundo plano, percebe o que acontece no
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
- Chat da live com agrupamento, seleção, cooldown e limite (sem plataforma ligada: teste com `/chat`)
- Moderação de entrada e saída (ofensas, segredos, links, tentativas de manipulação)
- Gatilhos para comentários espontâneos (`akemi/brain/triggers.yaml`)
- Captura de áudio do sistema (WASAPI loopback) → VAD → segmentação → Whisper
- **Voz local com Piper** (pt-BR, offline), interrompível, com escolha da saída de áudio
- **Microfone**: fale "Akemi, ..." e ela responde; interrompa falando por cima dela
- **Visão**: ela lê a tela (OCR) quando algo muda e, com um modelo de visão, entende jogos e vídeos
- Modo console para conversar sem áudio

- **Projeto Capricórnio** ([docs/CAPRICORNIO.md](docs/CAPRICORNIO.md)):
  - chip de identidade portátil e assinado (`akemi-chip`)
  - ela sabe em que corpo está e percebe quando troca
  - autoevolução independente, mas sempre leal a você (`/evolucao`, `/desfazer N`)
  - ela sabe no que foi inspirada e conta se perguntarem

- **Independência** ([docs/INDEPENDENCIA.md](docs/INDEPENDENCIA.md)):
  - cérebro reserva local (Ollama/llama.cpp): se a nuvem cair, ela troca sozinha e volta depois
  - autocura: reinicia o que quebrar (chat, voz, rotinas) e te avisa (`/saude`)
  - vontade própria: gostos, opiniões, vontades e objetivos que ela forma sozinha
  - iniciativa estilo J.A.R.V.I.S.: faz ações seguras por conta própria e te conta

- **Reconhece a sua voz** ([docs/VOZ.md](docs/VOZ.md)): `akemi --cadastrar-voz`. Outra
  pessoa no microfone conversa com ela, mas não manda nela
- **Oficina** ([docs/OFICINA.md](docs/OFICINA.md)): ela muda o próprio código e abre um
  PR para você revisar ("abre um PR para ...", `/pr`)
- **App do celular** ([docs/CELULAR.md](docs/CELULAR.md)): a mesma Akemi no
  Android, ao lado do PC. Ela responde onde você falou por último (`akemi --celular`)

- **Ideias do [Capri](https://github.com/lachydotmcg/capri-desktop)** ([docs/CAPRI.md](docs/CAPRI.md)):
  - **Modo Casca** (`akemi --casca claude`): ela vira o corpo do Claude Code e do Codex
    enquanto eles trabalham e avisa quando pedem seu OK ou terminam
  - **lembretes com data** ("dia 5 de dezembro às 9h") que sobrevivem a um reinício
  - **vigia da tela**: "me avisa quando aparecer download concluído"
  - **rotinas de voz**: "quando eu disser 'modo jogo', abre o Discord e abaixa o volume"
  - **minijogos por voz**: jogo da velha, forca, pedra-papel-tesoura e adivinhação
  - **clipes pelo OBS**: "clipa isso" salva os últimos segundos da tela
  - **modo chamada**: "Akemi, entra na call" e ela fala e responde na sua call do Discord

**Ainda falta:** avatar. Um avatar de desktop já está no código (`akemi/avatar/`),
mas fica desativado por enquanto ([docs/CAPRI.md](docs/CAPRI.md#avatar-de-desktop-desativado-por-enquanto)).

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
| `/estado` | mostra humor, objetivo, tarefas, gostos e opiniões |
| `/saude` | como ela está por dentro: cérebro em uso, voz, autocura |
| `/pr <pedido>`, `/prs` | ela abre um PR no próprio código; lista os PRs dela |
| `/voz` | reconhecimento da sua voz: cadastro e quem ela ouviu |
| `analisa a pasta X` | ela lê em segundo plano e conta depois ([docs/ARQUIVOS.md](docs/ARQUIVOS.md)) |
| `/pausar`, `/voltar` | pausa/retoma a autonomia |
| `/corpo` | mostra o corpo atual e os sentidos dele |
| `/evolucao`, `/evoluir` | lista ou pede propostas de mudança nela mesma |
| `/desfazer N` | tira uma mudança que ela fez em si (`/aprovar`/`/rejeitar` no modo com aprovação) |
| `/chip <arquivo>` | grava a identidade dela num chip |
| `/casca` | o que o Claude Code/Codex estão fazendo ([docs/CAPRI.md](docs/CAPRI.md)) |
| `/lembretes`, `/vigias` | lembretes pendentes; o que ela está vigiando na tela |
| `/rotinas` | suas rotinas de voz |
| `/jogar <jogo>` | velha, forca, pedra ou adivinhacao (ou diga "vamos jogar forca") |
| `/chamada` | entra ou sai da call de voz ([docs/CAPRI.md](docs/CAPRI.md#modo-chamada)) |

## Voz

```powershell
pip install -e ".[tts]"
python -m piper.download_voices pt_BR-faber-medium --data-dir data/voices
akemi --voz
```

Para deixar a voz ligada sempre, coloque `TTS_ENGINE=piper` no `.env`. Outras
vozes pt-BR: `pt_BR-cadu-medium`, `pt_BR-jeff-medium`, `pt_BR-edresson-low`
(troque também `PIPER_MODEL_PATH`). `PIPER_LENGTH_SCALE` ajusta a velocidade.

### Voz feminina (Kokoro)

O Piper só tem vozes masculinas em pt-BR. O **Kokoro** tem a `pf_dora`, também
offline e com qualidade melhor (cerca de 120 MB):

```powershell
pip install -e ".[kokoro]"
mkdir data\voices\kokoro
foreach ($f in "kokoro-v1.0.int8.onnx","voices-v1.0.bin") { curl.exe -L -o data\voices\kokoro\$f https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/$f }
```

No `.env`: `TTS_ENGINE=kokoro`. Depois é só `akemi --voz`. `KOKORO_SPEED`
ajusta a velocidade (0.95 deixa a fala mais calma).

O modelo int8 é pequeno, mas soa um pouco áspero. Para uma voz mais suave,
baixe também `kokoro-v1.0.fp16.onnx` (177 MB, mesmo link) e use
`KOKORO_MODEL_PATH=data/voices/kokoro/kokoro-v1.0.fp16.onnx`. Na CPU ele
gera a voz na mesma velocidade.

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

Para ouvir o
áudio do PC: `pip install -e ".[audio,stt]"` e `SYSTEM_AUDIO_ENABLED=true`.

## Funcionando 24h (servidor + PC)

A Akemi pode ser dividida em duas partes:

- **Núcleo**, num servidor sempre ligado (Docker, ex. ZimaOS): mente, memória,
  humor, pensamentos e chat da live. Não precisa de GPU, porque o LLM fica no NIM.
- **Corpo**, no seu PC: microfone, tela, áudio do PC e voz. Conecta ao núcleo
  pela rede de casa.

Com o PC desligado, ela continua pensando e respondendo no chat. Quando o PC
liga, ela ganha olhos e voz de novo. A memória é uma só.

### 1. Núcleo no ZimaOS

1. Gere uma senha para ligar os dois lados:
   `python -c "import secrets; print(secrets.token_urlsafe(24))"`
2. No ZimaOS: **App Store → "+" → Instalar app personalizado → Importar** e
   cole o [`docker-compose.yml`](docker-compose.yml). Troque `NVIDIA_API_KEY`
   e `AKEMI_TOKEN` (os valores com `TROQUE`).
3. A memória fica em `/DATA/AppData/akemi` no Zima.

A imagem `ghcr.io/mateus0790/akemi:latest` é publicada pelo GitHub Actions a
cada merge na `main`. Na primeira vez, deixe o pacote público no GitHub (perfil
→ Packages → akemi → Package settings → Change visibility → Public); senão o
ZimaOS não consegue baixar.

Para conferir: abra `http://IP-DO-ZIMA:8765/saude` no navegador.

### 2. Corpo no PC

No `.env` do PC, coloque o mesmo `AKEMI_TOKEN` e o endereço do Zima:

```
AKEMI_TOKEN=a-mesma-senha-do-servidor
SERVER_URL=ws://192.168.0.10:8765
```

```powershell
pip install -e ".[corpo,mic,tts,vision]"
akemi-corpo --voz --mic --tela
```

O corpo reconecta sozinho se o servidor reiniciar. No terminal dele dá para
digitar para ela, usar `/olhar`, `/pausar` e `/voltar`.

O modo antigo (tudo no PC, `akemi`) continua funcionando.

## Ações no PC

Peça por voz ou pelo terminal:

| Exemplo | O que ela faz | Pede "sim"? |
|---|---|---|
| "abre o Discord", "abre o vs code" | abre qualquer programa ou jogo do Menu Iniciar | não |
| "fecha o Spotify" | fecha a janela, como clicar no X (o programa pode perguntar se quer salvar) | **sim** |
| "pausa a música", "pula essa música" | teclas de mídia (Spotify, YouTube, etc.) | não |
| "aumenta o volume", "abaixa o som 20%" | volume do Windows | não |
| "pesquisa receita de bolo no YouTube" | abre a pesquisa no navegador | não |
| "me lembra em 10 minutos de tirar a pizza" | avisa por voz na hora | não |
| "me lembra amanhã às 9h de ligar pro dentista" | lembrete com data (fica salvo) | não |
| "me avisa quando aparecer download concluído" | vigia a tela (com `--tela`) | não |
| "quando eu disser modo jogo, abre o Discord e baixa o volume" | cria uma rotina | **sim**, ao criar |
| "clipa isso" | salva o replay buffer do OBS (`OBS_ENABLED`) | não |
| "Akemi, entra na call" | modo chamada ([docs/CAPRI.md](docs/CAPRI.md#modo-chamada)) | não |
| "lembra que eu gosto de café sem açúcar" | guarda na memória | não |

Toda ação exige que o pedido venha de você (microfone ou terminal). O chat da
live, a tela, o áudio do PC e os pensamentos dela nunca disparam ação, nem as
"livres". Lembretes ficam em `data/lembretes.json` e sobrevivem a um reinício. As
ações do PC funcionam no Windows.

No modo servidor + corpo, o núcleo decide (confere que foi você e pede o "sim")
e o corpo executa no PC. O corpo só aceita as ações da lista acima, então nem
um núcleo comprometido consegue mandar ele rodar outra coisa. Com o PC
desligado, ela responde que o PC não está conectado.

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

## Oficina: ela abre PR no próprio código

Peça "abre um PR para ..." (ou `/pr <pedido>` no console) e ela muda o próprio
código numa cópia isolada, roda os testes e abre um PR como rascunho para você
revisar. Ela nunca faz merge, não mexe nos arquivos de segurança e fica
desligada até você configurar um token. Veja [docs/OFICINA.md](docs/OFICINA.md).

## Modo Casca

```powershell
akemi --casca claude       # ela vira o corpo do Claude Code (codex | ambos | desligar)
```

O Modo Casca liga hooks no Claude Code/Codex sem mexer nos seus outros hooks,
escuta só em 127.0.0.1 com token e nunca aprova nada por você. Detalhes e
segurança em [docs/CAPRI.md](docs/CAPRI.md).

## Contribuindo

Veja [CONTRIBUTING.md](CONTRIBUTING.md).

## Licença

Apache 2.0 — veja [LICENSE](LICENSE).

O Modo Casca, os lembretes com data, o vigia da tela, as rotinas, os minijogos, os
clipes, o modo chamada e o avatar (ainda desativado) foram inspirados
no [Capri](https://github.com/lachydotmcg/capri-desktop) e reescritos do zero:
nenhum código ou arte dele foi copiado (o Capri usa uma licença não comercial).
