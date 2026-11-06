# O que veio do Capri

O [Capri](https://github.com/lachydotmcg/capri-desktop) é uma companheira de
desktop feita por fãs: ela senta nas suas janelas, fala, entra nas suas
chamadas, joga com você e vira o "corpo" do Claude Code enquanto ele trabalha. A Akemi absorveu
as ideias que combinam com ela, **reescritas do zero em Python e com o visual
próprio da Akemi**.

| Ideia do Capri | Na Akemi | Onde |
|---|---|---|
| Desktop pet que senta nas janelas | Avatar de desktop (**desativado por enquanto**) | `akemi/avatar/` |
| Code/Husk Mode | Modo Casca (`akemi --casca claude`) | `akemi/casca/` |
| Lembretes em datas | `lembrete` com `quando` ("dia 5 de dezembro às 9h"), entendido direto da frase | `akemi/actions/reminders.py`, `when.py` |
| Ações no PC (configurações, energia, janelas...) | volume exato, Configurações, bateria/disco, desligar, dividir a tela, modo escuro | `akemi/actions/system.py` |
| Lookout ("me avisa quando...") | Vigia da tela (`vigiar_tela`) | `akemi/actions/lookout.py` |
| Routines ("when I say X...") | Rotinas de voz ("quando eu disser 'modo jogo'...") | `akemi/actions/routines.py` |
| Mini games | Minijogos por voz: velha, forca, pedra-papel-tesoura, adivinhação | `akemi/games/` |
| Clips ("clip that") | Clipes pelo OBS ("clipa isso") | `akemi/actions/obs.py` |
| Call mode | Modo chamada ("Akemi, entra na call") | `akemi/chamada.py`, `akemi/tts/mirror.py` |

## Sobre a licença

O Capri usa a licença **PolyForm Noncommercial 1.0.0**, e a arte dele é de uma
artista que prefere ficar anônima. A Akemi é **Apache 2.0**. Para não misturar
as duas, **nenhum código, arte, som ou texto do Capri foi copiado**: só as ideias
de funcionamento, que foram reimplementadas aqui. O desenho da Akemi é original
(formas simples no Tk) e as falas são dela.

Valeu, Capri, pela inspiração.

## Avatar de desktop (desativado por enquanto)

O código está pronto em `akemi/avatar/`, mas o avatar fica **desligado** até ser
testado no Windows: a Akemi não abre o avatar sozinha, e não há mais `--avatar`
nem `/avatar`. Para experimentar mesmo assim, coloque `AVATAR_ENABLED=true` no
`.env` (vale para `akemi` e para `akemi-corpo`).

Quando ligado, ela aparece num canto da tela, por cima de tudo, e:

- **senta no topo da janela em foco** e vai junto quando você troca de janela
  (de longe, ela "teleporta" com uma falhada)
- **mexe a boca quando fala** e mostra a fala num balão (também sem voz, no modo texto)
- **pisca**, respira e muda de cara e de cor com o humor: as pontas do cabelo,
  o detalhe do moletom e a luz da presilha de chip seguem o humor dela
- **cochila** depois de `AVATAR_SLEEP_AFTER_S` sem nada acontecendo (zzz) e acorda
  quando você fala com ela
- **carinho**: passe o mouse indo e voltando em cima da cabeça dela. Ela fica
  animada e, de vez em quando, comenta
- **arraste** para levar ela para onde quiser; ela fica ali até você escolher
  "Sentar na janela" no menu
- **botão direito**: sentar na janela, dormir/acordar, pausar/voltar, esconder
  por 1 minuto, fechar
- no Modo Casca, mostra a **legenda** do que o Claude Code está fazendo

Configurações no `.env`: `AVATAR_SCALE` (0.5 a 3), `AVATAR_PERCH=false` (fica
no canto), `AVATAR_SLEEP_AFTER_S`.

**Skin:** para usar sua própria arte, crie uma pasta com PNGs transparentes e
aponte `AVATAR_SKIN` para ela. Precisa de `parado.png`; `piscando.png`,
`falando.png`, `feliz.png` e `dormindo.png` são opcionais. Com o Pillow instalado
(`pip install -e ".[avatar]"`), as imagens são redimensionadas e a borda fica
limpa. Use só arte que você tem direito de usar.

Requisitos: o `tkinter`, que vem com o Python do python.org no Windows. A
transparência e o sentar nas janelas são do Windows; no Linux e no Mac ela
aparece num quadrinho no canto da tela.

## Modo Casca

Ela vira o corpo do **Claude Code** e do **Codex** enquanto eles trabalham:
você deixa o assistente codando e vai fazer outra coisa, e ela te conta o que
está acontecendo.

```powershell
akemi --casca claude     # ou codex, ou ambos
# reinicie o Claude Code / Codex (no Codex, aceite os hooks quando ele perguntar)
akemi                    # a Akemi, como sempre
akemi --casca desligar   # tira só os hooks dela
```

| Quando o assistente... | Ela... |
|---|---|
| recebe seu pedido | "O Claude já tá nessa." |
| lê, edita, roda ou procura | anota sem falar ("Editando mind.py", "Rodando pytest -q"); aparece no `/casca` |
| tropeça num erro | "O Claude tropeçou num erro." |
| precisa do seu OK | **fala**: "O Claude precisa do seu OK. Quer usar Bash." |
| termina | **fala** a primeira frase da resposta: "O Claude terminou: arrumei o teste." |
| para com erro | **fala**: "O Claude parou com um erro." |
| chama um subagente | "O Claude chamou um ajudante." |
| compacta a memória | "Lá se vai a memória do Claude." (com uma falhada) |

- **Pergunte a ela**: "o que o Claude tá fazendo?", "o que ele fez hoje?". Ela sabe
  as sessões abertas, o que cada uma está fazendo, a última resposta e quantos
  pedidos, tarefas, arquivos editados, erros e aprovações teve hoje. `/casca`
  mostra o mesmo no console.
- `CASCA_VOZ`: `importante` (padrão; fala só quando pedem seu OK, terminam ou
  dão erro), `tudo` (fala também quando começam e quando tropeçam) ou `nada`
  (não fala, mas continua sabendo de tudo e mostra no `/casca`). Pausada
  (`/pausar`), ela não fala nada disso.
- `CASCA=desligado` desliga sem tirar os hooks. `CASCA=auto` (padrão) usa o que
  você ligou com `akemi --casca`.

**Ela só assiste.** Nunca aprova nem responde nada pelo assistente: você
responde no próprio Claude Code ou Codex.

Como funciona e por que é seguro:

- `akemi --casca` coloca os hooks dela no `~/.claude/settings.json` (ou
  `~/.codex/hooks.json`), **mantendo seus outros hooks e configurações**, com
  backup do arquivo (`settings.json.akemi-backup-<data>`). Se o arquivo não for
  JSON válido, ela não mexe. `CLAUDE_CONFIG_DIR` e `CODEX_HOME` são respeitados.
- O hook roda `python -m akemi.casca.hook claude`: lê o evento, deixa só os
  campos que importam (nunca o conteúdo dos arquivos editados nem a saída das
  ferramentas) e manda para a Akemi. No Claude Code ele roda em segundo plano
  (`async`), então nunca atrasa o assistente. Se a Akemi estiver fechada, não
  faz nada.
- A Akemi só escuta em **127.0.0.1**, numa porta aleatória, com um **token novo
  a cada vez que abre**. Os dois ficam em `~/.akemi/casca.json` (só para o seu
  usuário), que é apagado quando ela fecha.
- Chaves de API, tokens, senhas e `usuário:senha@` de URLs são apagados de tudo
  antes de virar legenda, fala ou contexto.
- O que vem do assistente entra como `Trust.PUBLIC`, como o chat da live:
  **nunca dispara ação**. No contexto dela, vai marcado como dado e não ordem.
- Com cérebro na nuvem, o resumo do que o assistente fez vai junto no prompt
  quando você fala com ela.

## Lembretes com data

Além de "daqui a 10 minutos", agora dá para pedir:

- "me lembra amanhã às 9h de ligar pro dentista"
- "me lembra dia 5 de dezembro às 18h do aniversário da Ana"
- "sexta às 10h", "05/12 às 9h", "hoje à noite", "daqui a 2 horas", "em 1 semana"

Os lembretes ficam em `data/lembretes.json` e **sobrevivem a um reinício**. Se
um venceu com ela desligada, ela avisa assim que volta, dizendo que atrasou. A
espera confere o relógio de verdade, então o PC hibernar não perde o horário.
Pergunte "quais lembretes eu tenho?" ou use `/lembretes`.

Um pedido com horário ("me lembra amanhã às 9 de ligar pro mecânico", "põe um
timer de 5 minutos") é entendido **direto da frase, sem o modelo**: funciona
igual com o cérebro reserva local, que é pequeno e erra conta de minutos. Sem
horário ("me lembra de comprar pão"), quem responde é o modelo, que pergunta
quando. Frases como "lembra que eu gosto de café" (um fato) e "me lembra o que
eu falei ontem" (uma pergunta) não viram lembrete.

Hora com "h" ou ":" ("às 8h", "18:30") vale como está. Hora solta ("às 3", "às
nove") é a próxima que vier: "às 3" depois do almoço é 15h. Em outro dia, de 1
a 6 é de tarde ("amanhã às 3" é 15h) e de 7 a 11, de manhã. Ela sempre repete o
horário que entendeu, então dá para corrigir na hora.

Logo depois de um lembrete tocar, "adia 10 minutos" (ou "soneca", "mais meia
hora") marca de novo. "Cancela o lembrete do mecânico" (ou "o último", "o 2")
cancela um só.

## Vigia da tela

Com a visão ligada (`--tela`):

- "me avisa quando aparecer **download concluído**"
- "me avisa quando aparecer **partida encontrada**"
- "para de vigiar" / "o que você está vigiando?" (`/vigias`)

Ela confere o texto que já lê da tela (e a descrição da cena, com modelo de
visão), sem ligar para acentos e maiúsculas e aceitando pequenos erros do OCR.
Quando aparece, ela avisa uma vez. Até 8 vigias ao mesmo tempo. O texto da tela
só é comparado com a sua frase: nada que aparece na tela vira ordem.

## Rotinas de voz

Junte ações que ela já sabe fazer debaixo de uma frase sua:

- "quando eu disser **modo jogo**, abre o Discord e abaixa o volume pra 30"
- "quando eu disser **fim do dia**, pausa a música e fecha o Spotify"

Ela lê os passos e pede o seu "sim" uma vez, na hora de criar. Depois é só dizer
"Akemi, modo jogo" (ou "roda a rotina modo jogo") e ela faz tudo na hora, sem
esperar o modelo e sem perguntar de novo, nem os passos que normalmente pedem
confirmação (como fechar programa): você já aprovou ao criar. Ela conta o que
fez e o que não deu.

- "quais rotinas eu tenho?", "apaga a rotina modo jogo" (`/rotinas` no console)
- Só você cria, apaga e dispara rotinas. Chat da live, tela, visitas, amigos na
  call e o Modo Casca nunca disparam uma, e ela nunca roda rotina por iniciativa própria.
- Até 8 passos. Uma rotina não chama outra e não usa ação proibida. Passo
  inválido é recusado antes de ela perguntar.
- Ficam em `data/rotinas.json`.

## Minijogos

Por voz ou pelo console, sem janela:

| Diga | Jogo |
|---|---|
| "vamos jogar jogo da velha" | você é o X; casas de 1 a 9 ("5", "centro", "canto de cima à esquerda") |
| "bora jogar forca" | ela pensa numa palavra e diz a categoria; "letra a", "é a letra erre", "é banana" |
| "joga pedra papel e tesoura comigo" | melhor de 3; ela escolhe antes de ouvir a sua |
| "quero jogar adivinhação" | número de 1 a 100 em 7 chances; ela diz "mais alto" ou "mais baixo" |

- Com o jogo aberto, cada jogada vai direto para o jogo, sem gastar o modelo.
  Se você falar de outra coisa, ela conversa normalmente e o jogo continua.
- "desisto", "chega", "para o jogo" encerram. Depois do fim, "revanche" ou "de
  novo" abre outra partida. Ela guarda o placar do dia.
- No console, o tabuleiro aparece na tela (`/jogar velha` também abre).
- No jogo da velha ela joga bem, mas de vez em quando se distrai: dá para ganhar.
- As jogadas não vão para a memória dela, só o resultado de cada partida.

## Clipes pelo OBS

"Clipa isso!" e ela manda o OBS salvar os últimos segundos da sua tela (o
**replay buffer**). Funciona com o OBS 28 ou mais novo, que já vem com o
obs-websocket.

No OBS:

1. **Configurações > Saída > Gravação de Replay**: ative e escolha quantos segundos guardar.
2. **Ferramentas > Configurações do Servidor WebSocket**: ative e copie a senha.

```powershell
pip install -e ".[obs]"
```

No `.env`: `OBS_ENABLED=true` e `OBS_PASSWORD=...`. Se o OBS estiver em outro PC
(ou o núcleo da Akemi no servidor), ajuste `OBS_URL` (padrão `ws://127.0.0.1:4455`).

- "clipa isso", "clipa aí", "salva esse clipe", "corta essa": vai na hora, sem
  esperar o modelo (o momento passa rápido). Ela diz o nome do arquivo salvo.
- Se o replay buffer estiver desligado, não tem o que salvar: ela liga na hora e
  avisa que do próximo clipe em diante funciona (`OBS_REPLAY_AUTOSTART=false`
  desliga isso).
- Só você pede clipe. O chat da live não consegue (seria spam de clipes).

## Modo chamada

A Akemi na sua call do Discord (ou Zoom, Teams, WhatsApp, Telegram...).
**Você** entra na call e ela entra **pelo seu microfone**: nada de clicar na
interface do Discord.

```text
Diga "Akemi, entra na call"   → ela se apresenta para o pessoal
"Akemi, avisa o pessoal que eu chego em 15 minutos" → ela fala na call
Na call: "Akemi, que música é essa?" → ela responde na call
"Akemi, sai da call"
```

**Preparação (uma vez, Windows):**

1. Instale o [VB-CABLE](https://vb-cable.com) (gratuito, doação opcional).
2. No `.env`: `CALL_OUTPUT_DEVICE=CABLE Input` (a voz dela vai para o cabo durante a call),
   `SYSTEM_AUDIO_ENABLED=true` (para ela ouvir a call) e uma voz (`TTS_ENGINE=fish`, `kokoro` ou `piper`).
3. Para a **sua** voz também ir pelo cabo: Painel de Som do Windows > **Gravação** >
   seu microfone > Propriedades > **Escutar** > marque "Escutar este dispositivo" e
   escolha **CABLE Input**. O Windows mistura a sua voz e a dela no cabo.
   (Quem preferir, o Voicemeeter faz a mesma mistura.)
4. No Discord: **Configurações de Voz > Dispositivo de entrada = CABLE Output**.
   Desligue o Cancelamento de Eco e a Supressão de Ruído do Discord para a voz dela
   não ser cortada.
5. Use **fone de ouvido** e, de preferência, o reconhecimento da sua voz
   (`akemi --cadastrar-voz`, [VOZ.md](VOZ.md)).

Como ela se comporta:

- Ao entrar, **se apresenta e avisa que está ouvindo** ("não guardo o que vocês
  falam"). Combine antes com o pessoal: ela transcreve o que é dito na call para
  saber quando foi chamada. `CALL_ANNOUNCE=false` tira o aviso.
- Responde quando alguém diz "Akemi", curto e com bom humor. O resto da conversa
  ela só acompanha: **nada de comentário espontâneo** sobre o papo da call, e no
  máximo 6 respostas por minuto.
- O que os amigos falam fica **só na memória RAM, durante a call**, para ela ter
  contexto. Não vai para a memória dela nem para a autoevolução, e é apagado quando
  ela sai. Com cérebro na nuvem, o trecho vai no prompt quando ela responde.
- **Tudo o que ela fala durante a call vai para a call**, inclusive respostas para
  você. Ela é avisada para não falar nada pessoal seu ali.

**Amigos pedem, você confirma** (`CALL_FRIENDS_CAN_ASK=true`, desligado por padrão):

- Um amigo diz "Akemi, abre o Cyberpunk". Ela não abre: pergunta para você,
  "Mateus, pediram na call: abrir programa (Cyberpunk). Você deixa?".
- **Só o seu "sim"** (microfone com a sua voz, terminal ou app) executa. Um amigo
  dizendo "sim" na call não vale.
- Só ações de `CALL_FRIEND_ACTIONS` (padrão: abrir programa ou site, pesquisar,
  música e volume). Fechar programa, rotinas, lembretes e o resto ficam de fora.

**Segurança:** o que vem da call é `Trust.PUBLIC`, como o chat da live. Se a voz
de um amigo vazar da caixa de som para o seu microfone e chegar como "você", ela
percebe que acabou de ouvir aquilo na call e descarta.

No modo servidor + PC, configure o `CALL_OUTPUT_DEVICE` no `.env` do PC (o corpo):
o núcleo liga e desliga o cabo lá. O corpo avisa ao núcleo se tem o cabo.

O que o modo chamada do Capri faz e este ainda não faz: clicar em "Entrar no
canal de voz" sozinha, saber o nome de quem falou, painel de chamada na tela e
ela falar por cima de risadas.

## O que ficou de fora (por enquanto)

- **Jogar os seus jogos** (controlar teclado e mouse dentro do jogo) e **desenhar
  na tela** (depende da janela do avatar, que está desativado).
- **Mods e skins da loja do Capri**: mods com código são um risco de segurança e
  repetem o papel da Oficina e da autoevolução. O avatar já aceita skins locais em PNG.
- **Capri VR** (Meta Quest): é um projeto Unity separado.
