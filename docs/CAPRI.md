# O que veio do Capri

O [Capri](https://github.com/lachydotmcg/capri-desktop) é uma companheira de
desktop feita por fãs: ela senta nas suas janelas, fala, entra nas suas
chamadas e vira o "corpo" do Claude Code enquanto ele trabalha. A Akemi absorveu
as ideias que combinam com ela, **reescritas do zero em Python e com o visual
próprio da Akemi**.

| Ideia do Capri | Na Akemi | Onde |
|---|---|---|
| Desktop pet que senta nas janelas | Avatar de desktop (**desativado por enquanto**) | `akemi/avatar/` |
| Code/Husk Mode | Modo Casca (`akemi --casca claude`) | `akemi/casca/` |
| Lembretes em datas | `lembrete` com `quando` ("dia 5 de dezembro às 9h") | `akemi/actions/reminders.py`, `when.py` |
| Lookout ("me avisa quando...") | Vigia da tela (`vigiar_tela`) | `akemi/actions/lookout.py` |

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

## Vigia da tela

Com a visão ligada (`--tela`):

- "me avisa quando aparecer **download concluído**"
- "me avisa quando aparecer **partida encontrada**"
- "para de vigiar" / "o que você está vigiando?" (`/vigias`)

Ela confere o texto que já lê da tela (e a descrição da cena, com modelo de
visão), sem ligar para acentos e maiúsculas e aceitando pequenos erros do OCR.
Quando aparece, ela avisa uma vez. Até 8 vigias ao mesmo tempo. O texto da tela
só é comparado com a sua frase: nada que aparece na tela vira ordem.

## O que ficou de fora (por enquanto)

- **Entrar nas chamadas do Discord** por você: depende de clicar na interface do
  Discord e de um cabo de áudio virtual. Grande demais para esta leva.
- **Jogar seus jogos e minijogos** (xadrez, jogo da velha), **clipes** e
  **desenhar na tela**.
- **Mods e skins da loja do Capri**: o avatar já aceita skins locais em PNG.
- **Capri VR** (Meta Quest).
