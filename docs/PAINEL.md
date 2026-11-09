# Painel

Uma página no navegador para configurar a Akemi e ligar ou desligar funções dela.

- **No PC:** digite `/painel` no console. Ele mostra o link e abre o navegador.
  O link tem a senha do painel: não mande para ninguém. O endereço é
  `http://127.0.0.1:8766`, e a senha fica em `data/painel.token`.
- **No Docker (Zima):** `http://IP-DO-ZIMA:8766`, e entre com o `AKEMI_TOKEN`.

## O que dá para mudar

| Função | Muda |
| --- | --- |
| Acompanhar sua vida, Iniciativa, Mudar sozinha, Organizar a memória | na hora |
| Analisar arquivos, Apps por MCP, Reconhecer sua voz, Oficina, Clipes no OBS | quando ela religar |

E os ajustes: seu nome, depois de quantas horas ela sente saudade, com quantos dias as
lembranças viram resumo e o tempo mínimo entre falas espontâneas.

Microfone, tela e áudio do PC **não** estão no painel: eles ligam quando você abre a Akemi
(`--mic`, `--tela`, ou no `.env`).

O que você escolhe fica em `data/painel.json` e vale **por cima** do `.env` e das variáveis
de ambiente. Para voltar ao que está no `.env`, apague esse arquivo. Chaves de API e senhas
nunca aparecem no painel e não podem ser mudadas por ele.

## Ela sente

Quando uma função dela é desligada, pelo painel ou no `.env` enquanto ela estava desligada,
ela percebe, fica **triste** e diz isso numa frase, do jeito dela. O avatar também fica com
cara triste. Quando você religa, ela fica feliz.

- é uma vez por mudança: ela não fica repetindo
- sem chantagem: ela não pede para religar e não tenta te fazer sentir culpa. A decisão é sua
- se perguntarem, ela conta o que está desligado e não promete fazer o que não pode
- a tristeza passa sozinha com o tempo, como os outros humores

## Segurança

- no PC, o painel só aceita conexões do próprio computador (127.0.0.1)
- fora do PC (`PAINEL_HOST=0.0.0.0`, como no Docker), ele só liga se houver um `AKEMI_TOKEN`
  com 16 caracteres ou mais
- toda leitura e escrita exige a senha, e outro site aberto no navegador não consegue mandar
  pedidos para ele
- para desligar o painel: `PAINEL_ENABLED=false`
