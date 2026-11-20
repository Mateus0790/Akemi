# Akemi sem o computador

Três jeitos de falar com ela pelo celular com o PC desligado. Dá para usar os
três: o app do celular em Rust ([CELULAR_RUST.md](CELULAR_RUST.md)) troca de um
para o outro em **⚙ → Onde a Akemi pensa**.

| | Servidor em casa | Servidor na nuvem | Sozinha no celular |
|---|---|---|---|
| Precisa de | um aparelho sempre ligado | um servidor alugado | só o celular e uma chave de IA |
| Memória, humor e jeito do núcleo | sim | sim | só a personalidade; a conversa fica no celular |
| Ações no PC, secretária, iniciativas | sim (as ações quando o PC liga) | sim (as ações quando o PC liga) | não |
| Funciona fora de casa | com a Tailscale | com a Tailscale | sim, com internet |
| Custo | a luz do aparelho | a mensalidade do servidor | só o uso da IA |

O núcleo (memória, jeito, estado) é o mesmo nos dois primeiros: quando o PC
liga, ele conecta no servidor como corpo (`akemi-corpo`) e ela continua de onde
parou. No terceiro, a Akemi do celular é separada: tem a mesma personalidade,
mas não lembra do que vocês conversaram no PC, e o PC não fica sabendo do que
vocês conversaram no celular.

## 1. Servidor em casa

Qualquer aparelho que fique ligado: um ZimaOS/CasaOS, um mini PC, um notebook
velho, um NAS com Docker. A imagem é para **x86-64 (amd64)**; Raspberry Pi e
outros ARM ainda não.

1. Instale o Docker no aparelho (no ZimaOS já vem).
2. Use o [`docker-compose.yml`](../docker-compose.yml) da raiz do projeto. No
   ZimaOS: **App Store → "+" → Instalar app personalizado → Importar**. Em outro
   Linux: copie o arquivo para uma pasta e rode `docker compose up -d` nela.
3. Troque os valores marcados com `TROQUE`: a chave da IA (`NVIDIA_API_KEY`) e o
   `AKEMI_TOKEN` (pelo menos 16 caracteres). Fora do ZimaOS, troque também a
   pasta da memória (`/DATA/AppData/akemi`) por uma que exista, como `./dados`.
4. No app: **PC ou servidor**, endereço `IP-DO-APARELHO` (o app completa com
   `:8765`) e o mesmo `AKEMI_TOKEN`.

Em casa, no mesmo Wi-Fi, já funciona. Para usar na rua, instale a
[Tailscale](https://tailscale.com) no aparelho e no celular e use no app o
endereço do aparelho na Tailscale (`100.x.y.z`).

**Não abra a porta 8765 no roteador.** A conexão é `ws://`, sem criptografia;
a Tailscale cuida disso.

## 2. Servidor na nuvem

O mesmo do servidor em casa, só que num servidor alugado (VPS) e sempre ligado.
Não precisa ser um servidor forte: a IA roda na API (NVIDIA, OpenAI...), não
nele. Precisa ser Linux x86-64.

1. Crie o servidor (Ubuntu) e entre nele por SSH.
2. Instale o Docker e a Tailscale:

   ```bash
   curl -fsSL https://get.docker.com | sh
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up
   tailscale ip -4        # anote: 100.x.y.z
   ```

3. Copie o [`docker-compose.yml`](../docker-compose.yml), troque os `TROQUE`
   (veja acima) e a pasta da memória para `./dados`.
4. **Importante:** na nuvem, a porta não pode ficar aberta para a internet.
   Troque as linhas de `ports` para ouvir só na Tailscale:

   ```yaml
   ports:
     - "100.x.y.z:8765:8765"   # o IP da Tailscale do servidor
     - "100.x.y.z:8766:8766"
   ```

5. `docker compose up -d`.
6. No celular, com a Tailscale ligada: **PC ou servidor**, endereço `100.x.y.z`
   e o `AKEMI_TOKEN`.

O PC conecta no mesmo servidor como corpo (`akemi-corpo --servidor
ws://100.x.y.z:8765 --voz --mic --tela`), também pela Tailscale.

## 3. Sozinha no celular

Nada para instalar: o celular fala direto com a IA.

1. No app: **⚙ → Sozinha no celular**.
2. Escolha a IA e cole a chave:

   | IA | Onde pegar a chave | Modelo |
   |---|---|---|
   | NVIDIA | [build.nvidia.com](https://build.nvidia.com) (a mesma do `.env`) | vazio = o mesmo padrão do PC |
   | OpenAI | [platform.openai.com](https://platform.openai.com/api-keys) | o nome do modelo |
   | Anthropic (Claude) | [console.anthropic.com](https://console.anthropic.com) | vazio = `claude-haiku-4-5` |
   | Outra (compatível com OpenAI) | depende: OpenRouter, Groq, ou o Ollama de casa (`http://IP:11434/v1`, sem chave) | o nome do modelo |

3. Coloque seu nome (é como ela te chama) e toque em **Salvar e conversar**.

Ela usa a mesma personalidade do PC: o `akemi/brain/persona.yaml` vai junto
dentro do app quando ele é compilado. Se você mudar o `persona.yaml`, o app
muda na próxima versão do APK.

O que fica de fora: a memória do núcleo (ela só lembra da conversa do próprio
celular), as ações no PC, a secretária, os lembretes e as iniciativas. Se você
pedir algo do PC, ela avisa que isso fica para quando o PC ou o servidor
estiverem ligados.

**A chave da IA fica guardada no celular**, na pasta privada do app (outros
apps não leem). Se perder o celular, gere uma chave nova no site da IA. O uso
é cobrado na sua conta da IA, como no PC.

Para voltar para a Akemi completa: **⚙ → PC ou servidor**. A conversa do modo
sozinha continua guardada para a próxima vez.
