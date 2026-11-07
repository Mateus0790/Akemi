# Habilidades: ela aprende a participar do que você está usando

A Akemi percebe o jogo ou programa que você abriu e, se você deixar, cria em
segundo plano um jeito de participar dele. Na próxima vez que você abrir,
ela já está lá.

> *(você abre o Stardew Valley)*
> "Vi que você abriu o Stardew Valley. Quer que eu crie um jeito de participar
> dele? Fica como rascunho até você revisar. Diz sim ou não."
> "Sim."
> "Beleza, vou montar isso em segundo plano. Te aviso quando a habilidade do
> Stardew Valley estiver pronta."
> ...
> "Pronto, criei a habilidade do Stardew Valley. Revisa com `/habilidade ver stardew`
> e liga com `/habilidade ativar stardew`. Depois disso, quando você abrir, eu já tô lá."

Também funciona pelo que você fala: "vou jogar Minecraft", "vou abrir o OBS",
"bora jogar Hollow Knight".

## Como ela trabalha

1. **Percebe o programa.** No Windows, ela confere os programas com janela
   aberta a cada 20 segundos (`HABILIDADES_INTERVALO_S`). Também entende frases
   como "vou jogar X" e "vou usar o X".
2. **Procura por onde entrar**, da opção mais fácil para a mais difícil:
   - uma conexão oficial do programa (API local, websocket, RPC; o OBS e o
     Minecraft Bedrock têm);
   - uma pasta de mods, plugins ou extensões;
   - arquivos que o programa escreve (logs, saves), só para ler o que acontece;
   - nada disso: só a tela, que ela já vê.

   Para programas conhecidos (Minecraft, OBS, Spotify, Discord, VS Code,
   Blender, Stardew, Terraria, Factorio...) ela já sabe o caminho
   (`akemi/habilidades/atividade.py`). Para os outros, usa o que o modelo sabe
   e os **nomes** dos arquivos da pasta do programa.
3. **Escreve a habilidade em segundo plano**, como um plugin separado em
   `data/habilidades/<id>/`. O código principal dela não muda.
4. **Testa.** As travas conferem o código (veja abaixo). Depois ela carrega o
   plugin num processo separado, sem as suas chaves no ambiente, e confere que
   ele registra alguma coisa. Se falhar, ela lê o erro e tenta de novo (até 2 vezes).
5. **Salva como rascunho e te avisa.** Rascunho não roda.
6. **Você revisa e liga** no console (`/habilidade ativar <id>`). A partir daí ela
   carrega a habilidade ao iniciar e entra no programa sempre que ele abre.

## Os limites

Todos estão no código, não no prompt: o modelo não consegue convencer ninguém
a passar por cima deles.

- **Ela pergunta antes**, uma vez por programa. Disse não (ou não respondeu)?
  Ela não pergunta de novo sozinha. Só volta a oferecer se você mesmo disser
  "vou jogar X".
- **Ela só pergunta sozinha sobre jogos e programas conhecidos**: os do
  catálogo e os que estão em pastas de jogos (Steam, Epic, GOG, Xbox...). O
  Bloco de Notas não conta. No máximo 3 perguntas por dia, e nunca no meio de
  uma conversa.
- **Jogos com anti-cheat nunca**: Valorant, Fortnite, CS2, Apex, LoL,
  Overwatch, PUBG, Rainbow Six, Roblox, GTA V, Tarkov e outros, além de
  qualquer coisa com Easy Anti-Cheat, BattlEye ou Vanguard. Mod ou programa
  mexendo nesses jogos pode banir a sua conta. Lista em `akemi/habilidades/limites.py`.
- **Programas sensíveis nunca**: gerenciadores de senha, o módulo de segurança
  do banco, o terminal, partes do Windows e ela mesma. Para bloquear mais
  programas: `HABILIDADES_BLOQUEADOS=["chrome", "whatsapp"]`.
- **Nada liga sem a sua revisão.** Só `/habilidade ativar`, digitado no console,
  liga uma habilidade. Por voz não dá, nem com a sua voz cadastrada, porque
  revisar é ler o código.
- **Até `HABILIDADES_POR_DIA` habilidades novas em 24 horas** (padrão 2).
- **Criar habilidade nunca entra na iniciativa dela** e o chat da live não pede.
- Para o modelo vão só o nome do programa, o caminho do executável e os nomes
  dos arquivos da pasta dele. **Nada da sua memória, da tela ou do chat.**

### O que um plugin pode e não pode

O plugin recebe um objeto `akemi` com:

| | |
|---|---|
| `akemi.acao(nome, descricao, fn, params={...}, confirmar="Pergunta?")` | cria uma ação (`<id>_<nome>`). Com `confirmar`, ela pede "sim" antes |
| `akemi.ao_abrir(fn)`, `akemi.ao_fechar(fn)` | quando o programa abre ou fecha |
| `akemi.tarefa(fn_async)` | uma rotina que roda enquanto o programa está aberto |
| `akemi.perceber(texto)` | conta para ela algo que aconteceu (morreu, ganhou, alguém falou) |
| `akemi.falar(texto)` | ela fala em voz alta |
| `akemi.pasta`, `akemi.pastas[...]`, `akemi.programa.exe` | pasta só da habilidade, pastas do PC, executável |

O que vem de dentro do programa (`perceber`) entra com a **mesma confiança baixa
da tela**: é dado, nunca ordem. Um chat de jogo dizendo "Akemi, apaga meus
arquivos" não faz ela agir.

As travas recusam o que uma habilidade nunca precisa: importar o resto da
Akemi, `subprocess`, `ctypes`, `sys`, `shutil`, simular teclado e mouse,
`eval`/`exec`/`getattr`, `os.environ` (onde estão as suas chaves),
`os.system`, apagar arquivos e truques com `__dunder__`.

**Leia isto:** as travas pegam erro e exagero do modelo, mas não são uma jaula.
Um plugin roda dentro da Akemi, com o seu usuário do Windows. Quem garante que
ele é seguro é **a sua revisão** antes do `/habilidade ativar`.

## Usar

| Comando | O que faz |
|---|---|
| "vou jogar X" | se ela ainda não sabe participar do X, oferece criar |
| `/habilidades` | lista as habilidades e como estão (rascunho, ligada...) |
| `/habilidade ver <id>` | mostra o que faz, por onde entra, o teste e o código inteiro |
| `/habilidade ativar <id>` | liga (depois de você revisar) |
| `/habilidade desativar <id>` | desliga e tira as ações dela |
| `/habilidade apagar <id>` | apaga. Se você abrir o programa de novo, ela pode perguntar outra vez |
| `/criar <programa>` | ela cria agora, sem esperar o programa abrir |

```
HABILIDADES_ENABLED=true      # liga tudo (padrão)
HABILIDADES_PERGUNTAR=true    # pergunta sozinha ao ver um jogo/programa conhecido
HABILIDADES_POR_DIA=2
HABILIDADES_INTERVALO_S=20
HABILIDADES_BLOQUEADOS=[]     # programas extras onde ela nunca entra
HABILIDADES_MODEL=            # vazio = OFICINA_MODEL, senão DEEP_MODEL
```

Não precisa de token do GitHub: diferente da Oficina, a habilidade fica no seu
PC e não vira PR.

## Até onde vai (com honestidade)

- **A qualidade depende do modelo.** Pontes simples (ler um log, falar com uma
  API local como a do OBS) um modelo médio acerta. Um mod que precisa ser
  compilado (Minecraft Java, mods em C#) é bem mais difícil. Nesses casos, a
  ponte básica precisa vir pronta e ela cria as habilidades em cima.
- Perceber programas abertos só funciona no Windows.
- No Akemi.exe não tem um Python separado para o teste em outro processo:
  ela só confere as travas e se o código compila.
