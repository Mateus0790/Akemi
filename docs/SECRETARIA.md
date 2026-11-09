# Secretária nos chats

A Akemi responde quem te manda mensagem (WhatsApp, Telegram, Discord...)
enquanto você está ocupado:

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

As mensagens chegam por uma **ponte**. Há dois jeitos.

### Ponte HTTP (WhatsApp, Discord, SMS, qualquer um)

Com `SECRETARIA_ENABLED`, ela abre `http://127.0.0.1:8766/mensagem`. Quem
estiver no meio (um app de resposta automática, um script, o n8n) manda a
mensagem e recebe a resposta dela:

```http
POST /mensagem
Authorization: Bearer SUA-SENHA
Content-Type: application/json

{"plataforma": "whatsapp", "contato": "Ana", "id": "5511999999999",
 "texto": "oi, o Mateus tá aí?", "grupo": false}
```

```json
{"resposta": "Oi! Aqui é a Akemi...", "replies": [{"message": "Oi! Aqui é a Akemi..."}]}
```

- A senha também pode ir em `X-Akemi-Token` ou em `?token=` na URL.
- Resposta vazia (`"resposta": ""`) quer dizer que ela ficou quieta: você não
  está ocupado, é um grupo, a pessoa está na lista de ignorar...
- Mande `"do_dono": true` quando **você** escrever na conversa: ela sai de cena
  com essa pessoa por 30 minutos.
- Ela também entende o formato do "servidor próprio" do AutoResponder
  (`{"query": {"sender": ..., "message": ..., "isGroup": ...}}`) e devolve
  `replies` do jeito que ele espera.

**WhatsApp pelo celular (Android):** apps de resposta automática, como o
AutoResponder, leem a notificação da mensagem e respondem por você. Crie uma
regra que responde por servidor e aponte para
`http://IP-DO-PC:8766/mensagem?token=SUA-SENHA`. Para o celular alcançar o PC
pela rede de casa, coloque `SECRETARIA_HOST=0.0.0.0` no `.env` (e libere a
porta no firewall do Windows). Teste antes com uma mensagem sua.

**WhatsApp pelo PC:** um script com uma biblioteca do WhatsApp Web (como o
whatsapp-web.js) pode mandar cada mensagem para a ponte e responder com o que
volta.

> Apps e bibliotecas não oficiais do WhatsApp podem ir contra os termos de uso
> dele e levar a conta a ser bloqueada. Use por sua conta e risco.

### Telegram (API oficial)

1. Crie um bot com o [@BotFather](https://t.me/BotFather) e copie o token.
2. No `.env`: `TELEGRAM_BOT_TOKEN=123456:ABC...`
3. Para ela responder **na sua conta**, e não só no bot: no Telegram, vá em
   Configurações → Telegram Business → Chatbots e escolha o seu bot (precisa
   do Telegram Premium). Sem isso, as pessoas falam com ela escrevendo para o bot.

Ela busca as mensagens sozinha (long polling): não precisa abrir porta. Quando
você mesmo responde uma conversa, ela fica quieta com essa pessoa por 30
minutos. Áudio, foto e figurinha viram recado ("mandou um áudio").

## Servidor 24h

No modo núcleo + corpo, a secretária roda no núcleo: ela continua respondendo
com o PC desligado. No `docker-compose.yml`, adicione `SECRETARIA_ENABLED: "true"`
e `SECRETARIA_HOST: 0.0.0.0` e publique a porta `8766:8766` (para o Telegram,
basta o `TELEGRAM_BOT_TOKEN`; não precisa de porta).

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
