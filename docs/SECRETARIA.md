# Secretária nos chats

A Akemi responde quem te manda mensagem no WhatsApp, Telegram, Discord e
Teams enquanto você está ocupado:

- **Diz que você está ocupado** e quando volta, se você disse.
- **Anota os recados** e conta tudo quando você volta.
- **Responde o que você deixou ela contar** ("se perguntarem, pode dizer que eu
  chego às 19h"). O resto da sua vida ela não conta.
- **Te avisa na hora** se alguém disser que é urgente.

```
Você:  Akemi, tô em reunião até as 15h.
Akemi: Beleza, fico de secretária. Se te mandarem mensagem, digo que você tá
       em reunião até hoje às 15:00 e anoto os recados.

  (no WhatsApp)
  Ana:   oi, o Mateus vai no churrasco sábado?
  Akemi: Oi! Aqui é a Akemi, a assistente do Mateus. Ele tá numa reunião até as
         15h, mas eu passo o recado e ele te responde!

Você:  Voltei.
Akemi: Bem-vindo de volta! Recados: Ana mandou no WhatsApp: oi, o Mateus vai
       no churrasco sábado?
```

## Ligar

No `.env`:

```
SECRETARIA_ENABLED=true
AKEMI_TOKEN=uma-senha-longa        # ou SECRETARIA_TOKEN, só para a ponte
```

Gere a senha com `python -c "import secrets; print(secrets.token_urlsafe(24))"`.
Depois é só abrir a Akemi (`akemi`). No terminal, `/dm Ana oi, ele tá aí?`
simula uma mensagem, sem precisar ligar nenhum chat, e `/recados` mostra se
você está ocupado, o que ela pode contar e os recados.

## Falando com ela

| Você diz | O que acontece |
|---|---|
| "tô ocupado", "tô em reunião", "tô em aula", "modo não perturbe", "fica de secretária" | ela passa a responder as mensagens |
| "tô em reunião até as 15h", "tô ocupado por 2 horas", "tô em aula até amanhã" | igual, e diz quando você volta. Na hora, tira sozinha e conta os recados |
| "vou no dentista, cuida do zap até as 4" | o modelo entende e liga do mesmo jeito |
| "voltei", "tô livre", "acabou a reunião", "desliga o modo ocupado" | para de responder e conta quem mandou o quê |
| "se perguntarem, pode dizer que eu chego em casa às 19h" | ela passa a responder isso (`pode_contar`) |
| "não conta mais aquilo das 19h" | ela esquece essa informação (`nao_contar`) |
| "alguém me mandou mensagem?" | conta os recados (`ler_recados`) |

Frases como "tô ocupado demais pra isso" não ligam nada: ela só liga sozinha
quando a frase é claramente um pedido. No resto, quem decide é o modelo.

O que você deixou contar fica em `data/recados.json`, junto com o status e os
recados que ela ainda não te contou. Se a informação diz para quem é ("pode
dizer pra minha mãe que..."), ela tenta contar só para essa pessoa, mas o nome
vem do próprio chat. Não ponha ali nada que não possa chegar em qualquer um.

## Ligando os chats

Cada app deixa entrar de um jeito, e nem todos deixam responder pela sua conta:

| Chat | Como ela entra | Responde na sua conta? | Oficial? |
|---|---|---|---|
| **WhatsApp** | aparelho conectado (QR code), pelo script `pontes/whatsapp` | sim, marcado com 🤖 | não: risco de bloqueio |
| **Telegram** | bot do @BotFather + Telegram Business | sim, com Premium (senão, no bot) | sim |
| **Discord** | bot nos seus servidores | não: responde quando te marcam e na DM do bot | sim |
| **Teams** | login Microsoft (conta de trabalho ou escola) | sim, nas conversas 1:1, marcado com 🤖 | sim |

O que ela manda **pela sua conta** vai sempre marcado com 🤖, porque quem
recebe vê o seu nome. Em todos, quando **você** responde uma conversa, ela
fica quieta com aquela pessoa por 30 minutos, e o que não é texto (áudio,
foto, figurinha) vira recado ("mandou um áudio").

### WhatsApp

Precisa do [Node.js](https://nodejs.org) 18 ou mais novo. Com a Akemi aberta
(e `SECRETARIA_ENABLED=true`):

```powershell
cd pontes\whatsapp
npm install
npm start
```

Na primeira vez aparece um QR code: no celular, abra o WhatsApp → Aparelhos
conectados → Conectar um aparelho e escaneie. Depois ele lembra (pasta
`pontes/whatsapp/.sessao`, não compartilhe). O script lê o `AKEMI_TOKEN` do
`.env` da Akemi sozinho.

- Ele entra como um aparelho conectado, igual ao WhatsApp Web, e manda cada
  mensagem para a Akemi. Ela decide se responde (só quando você está ocupado,
  por padrão).
- A conversa continua **não lida** para você: ela responde sem marcar como lida.
- Com a Akemi no servidor (Docker), aponte o script para lá:
  `AKEMI_PONTE=http://IP-DO-SERVIDOR:8766/mensagem`.
- `SECRETARIA_IGNORAR` aceita o número, ex. `["5511999999999"]`.

> O script usa o [whatsapp-web.js](https://github.com/pedroslopez/whatsapp-web.js),
> que não é oficial. O WhatsApp pode bloquear contas que usam automação: use
> por sua conta e risco (de preferência, teste antes com um número que não é o principal).

**Sem PC ligado (Android):** apps de resposta automática, como o
AutoResponder, leem a notificação e respondem por servidor. Aponte a regra
para `http://IP-DO-PC:8766/mensagem?token=SUA-SENHA` e coloque
`SECRETARIA_HOST=0.0.0.0` no `.env` (libere a porta no firewall). A ponte
entende o formato dele (veja "Ponte HTTP" abaixo). Teste antes com uma mensagem sua.

### Telegram

1. Crie um bot com o [@BotFather](https://t.me/BotFather) e copie o token.
2. No `.env`: `TELEGRAM_BOT_TOKEN=123456:ABC...`
3. Para ela responder **na sua conta**, e não só no bot: no Telegram, vá em
   Configurações → Telegram Business → Chatbots e escolha o seu bot (precisa
   do Telegram Premium). Sem isso, as pessoas falam com ela escrevendo para o bot.

Ela busca as mensagens sozinha (long polling): não precisa abrir porta.

### Discord

O Discord não deixa nenhum bot ler as suas DMs pessoais, e automatizar a sua
conta ("selfbot") é proibido e dá banimento. Por isso ela entra como bot, do
jeito permitido:

- **Num servidor:** quando alguém te marca (@você) ou responde uma mensagem
  sua enquanto você está ocupado, ela responde no canal. Ela nunca marca
  ninguém (nada de @everyone). Se você escrever no canal, ela sai de cena ali.
- **Na DM do bot:** quem escrever para ela deixa recado.
- **"Não perturbe"** (com `DISCORD_PRESENCA=true`): colocar esse status no
  Discord já liga o modo ocupado; voltar a ficar online desliga e ela conta os recados.

Para ligar:

1. Em [discord.com/developers/applications](https://discord.com/developers/applications):
   New Application → Bot → Reset Token, e copie o token.
2. Ainda em Bot, em Privileged Gateway Intents, ligue **Message Content Intent**
   (e **Presence Intent**, se for usar `DISCORD_PRESENCA`).
3. Em OAuth2 → URL Generator, marque `bot` e as permissões View Channels, Send
   Messages e Read Message History. Abra o link e adicione o bot nos seus servidores.
4. Seu ID: no Discord, Configurações → Avançado → Modo desenvolvedor; depois
   clique com o direito no seu nome → Copiar ID.
5. No `.env` (e `pip install -e ".[discord]"`):

```
DISCORD_BOT_TOKEN=o-token-do-bot
DISCORD_DONO_ID=123456789012345678
# DISCORD_PRESENCA=true
```

### Microsoft Teams

Funciona com conta de **trabalho ou escola** (o Teams pessoal não tem API),
pela API oficial da Microsoft (Graph). Responder conversas do trabalho com uma
IA pode ir contra as regras da sua empresa: confira antes, e muitas empresas
exigem que o TI aprove o app.

- **Conversas 1:1** (e grupos, com `SECRETARIA_GRUPOS=true`): ela responde
  pela sua conta, marcada com 🤖.
- **Status do Teams** (`TEAMS_PRESENCA`, ligado por padrão): em reunião, numa
  ligação, apresentando ou em "Não incomodar", ela liga o modo ocupado sozinha
  e **não fala nada** (para a voz dela não entrar no seu microfone). Quando
  você fica livre, ela conta os recados. Um modo ocupado que você ligou ("tô
  em aula") não é desligado pelo Teams.

Para ligar:

1. Em [entra.microsoft.com](https://entra.microsoft.com): Identidade →
   Aplicativos → Registros de aplicativo → Novo registro. Nome: Akemi. Não
   precisa de URI de redirecionamento.
2. Em Autenticação, ligue **Permitir fluxos de clientes públicos**.
3. Em Permissões de API → Microsoft Graph → Permissões delegadas, adicione
   `User.Read`, `Chat.Read`, `ChatMessage.Send` e `Presence.Read`.
4. Copie o **ID do aplicativo (cliente)**. Se o app for só da sua empresa
   ("somente este diretório"), copie também o **ID do diretório (locatário)**.
5. No `.env`:

```
TEAMS_CLIENT_ID=00000000-0000-0000-0000-000000000000
# TEAMS_TENANT=id-do-diretorio   # se o app for só da sua empresa
```

6. Uma vez: `akemi --teams-login`. Ela mostra um código para você digitar em
   microsoft.com/devicelogin e guarda o acesso em `data/teams.json` (é uma
   chave: não compartilhe). Se o acesso vencer, ela avisa no log para rodar de novo.

Ela olha as conversas a cada `TEAMS_INTERVALO_S` segundos (15 por padrão) e
só responde o que chegou depois que ela ligou.

### Ponte HTTP (qualquer outro)

Com `SECRETARIA_ENABLED`, ela abre `http://127.0.0.1:8766/mensagem`. É por
ela que o script do WhatsApp entra, e serve para qualquer outra coisa (SMS,
n8n, um script seu): manda a mensagem e recebe a resposta dela.

```http
POST /mensagem
Authorization: Bearer SUA-SENHA
Content-Type: application/json

{"plataforma": "whatsapp", "contato": "Ana", "id": "5511999999999",
 "texto": "oi, o Mateus tá aí?", "grupo": false}
```

```json
{"resposta": "🤖 Oi! Aqui é a Akemi...", "replies": [{"message": "🤖 Oi! Aqui é a Akemi..."}]}
```

- A senha também pode ir em `X-Akemi-Token` ou em `?token=` na URL.
- Resposta vazia (`"resposta": ""`) quer dizer que ela ficou quieta: você não
  está ocupado, é um grupo, a pessoa está na lista de ignorar...
- Mande `"do_dono": true` quando **você** escrever na conversa: ela sai de cena
  com essa pessoa por 30 minutos.
- A resposta vem com 🤖 (sai pela sua conta). `"marcar": false` tira a marca,
  para quando quem manda é um bot próprio.
- Ela também entende o formato do "servidor próprio" do AutoResponder
  (`{"query": {"sender": ..., "message": ..., "isGroup": ...}}`) e devolve
  `replies` do jeito que ele espera.

## Servidor 24h

No modo núcleo + corpo, a secretária roda no núcleo: ela continua respondendo
com o PC desligado. Telegram, Discord e Teams funcionam sem abrir porta. Para a
ponte HTTP (WhatsApp), adicione `SECRETARIA_ENABLED: "true"` e
`SECRETARIA_HOST: 0.0.0.0` no `docker-compose.yml`, publique a porta
`8766:8766` e aponte o script com `AKEMI_PONTE`.

## Configuração

| Variável | Padrão | O que faz |
|---|---|---|
| `SECRETARIA_ENABLED` | `false` | liga a secretária |
| `SECRETARIA_QUANDO` | `ocupado` | `ocupado` = só enquanto você está ocupado; `sempre` = responde toda mensagem |
| `SECRETARIA_TOKEN` | (o `AKEMI_TOKEN`) | senha da ponte HTTP, com pelo menos 16 caracteres |
| `SECRETARIA_HOST` | `127.0.0.1` | `0.0.0.0` para o celular alcançar pela rede |
| `SECRETARIA_PORT` | `8766` | porta da ponte |
| `SECRETARIA_GRUPOS` | `false` | responder em grupos |
| `SECRETARIA_IGNORAR` | `[]` | nomes ou números que ela nunca responde, ex. `["Chefe","5511999999999"]` |
| `SECRETARIA_MAX_POR_CONTATO` | `4` | respostas por pessoa a cada 10 minutos (o resto vira só recado) |
| `TELEGRAM_BOT_TOKEN` | | liga o Telegram |
| `DISCORD_BOT_TOKEN` | | liga o Discord |
| `DISCORD_DONO_ID` | | seu ID no Discord (para saber quando te marcam) |
| `DISCORD_PRESENCA` | `false` | "Não perturbe" no Discord liga o modo ocupado |
| `TEAMS_CLIENT_ID` | | liga o Teams (depois rode `akemi --teams-login`) |
| `TEAMS_TENANT` | `organizations` | o ID do diretório, se o app for só da sua empresa |
| `TEAMS_PRESENCA` | `true` | reunião ou ligação no Teams liga o modo ocupado |
| `TEAMS_INTERVALO_S` | `15` | de quanto em quanto tempo ela olha o Teams |

## Privacidade e segurança

- **Ela sempre diz que é a Akemi, sua assistente.** Nunca finge ser você, nunca
  marca compromisso nem promete nada em seu nome.
- **Quem manda mensagem não manda nela.** As mensagens entram como
  `Trust.PUBLIC`: nenhuma ação roda, mesmo que o modelo seja convencido a
  pedir uma (fica só no log). Tentativas de manipulação são marcadas pela
  moderação.
- **Ela não conta o que sabe de você.** A conversa com o contato não leva sua
  memória, seus fatos, suas tarefas, o humor dela nem o que ela vê na tela:
  só o que você deixou contar e se você está ocupado.
- **Links e e-mails** só saem se estiverem no que você deixou contar. Chaves
  de API e CPF nunca saem. Ofensas são bloqueadas (na entrada e na saída).
- **Limites:** respostas por pessoa, no total por minuto e nada de grupos por
  padrão. `/pausar` também faz ela parar de responder os chats.
- **O que ela ouve fica com você:** os recados ficam em `data/recados.json`
  até ela te contar, e a conversa com cada pessoa fica só na memória RAM. Com
  o cérebro na nuvem, as mensagens vão para o provider do LLM.
- **Se o cérebro cair**, ela responde com uma frase pronta ("ele tá ocupado,
  mas eu passo seu recado") e o recado fica anotado do mesmo jeito.
