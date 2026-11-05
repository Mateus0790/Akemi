# Akemi no celular

O chip continua no PC (ou no servidor). O app do celular é mais um corpo: tem
tela, microfone e alto-falante, e conecta **ao lado** do PC, sem derrubar
ele. A memória, o humor e o jeito dela são os mesmos nos dois.

Ela responde **onde você falou por último**: mandou mensagem pelo celular, a
resposta sai no celular. Falou com ela no PC, volta a sair no PC. O celular
também recebe uma cópia do que ela diz no PC (marcada "falou no PC"), e
lembretes, iniciativas e avisos chegam como notificação quando o app está
em segundo plano.

## 1. Baixar o app (Android)

O GitHub compila o APK sozinho a cada mudança em `app/`:

1. No GitHub: **Actions → App do celular** → a execução mais recente (verde).
2. Lá embaixo, em **Artifacts**, baixe `akemi-app` (é um zip com o `app-release.apk`).
3. Passe o APK para o celular e abra. O Android vai pedir para permitir
   "instalar apps desta fonte".

Ao atualizar para uma versão nova, pode ser preciso desinstalar a antiga
antes: o APK de teste é assinado com uma chave diferente a cada compilação.

iPhone: o código é o mesmo (Flutter), mas instalar fora da App Store exige
um Mac e conta de desenvolvedor da Apple. Por enquanto só Android.

## 2. Ligar a porta do celular

No `.env` do PC (ou do servidor), defina o token, com pelo menos 16 caracteres:

```
AKEMI_TOKEN=uma-senha-longa
```

Gere um com `python -c "import secrets; print(secrets.token_urlsafe(24))"`.

**Chip no PC (modo local):**

```powershell
akemi --voz --mic --celular
```

Ela mostra o endereço: `📱 App do celular: servidor ws://192.168.0.15:8765`.
O Windows pode perguntar se libera o Python no firewall: libere em **redes
privadas**.

**Núcleo no servidor (ZimaOS):** não precisa de nada novo. O celular conecta
na mesma porta do `akemi-corpo` (`ws://IP-DO-ZIMA:8765`).

## 3. Conectar o app

Na primeira vez o app abre a tela **Conectar na Akemi**:

- **Endereço**: o que ela mostrou (pode digitar só o IP, `192.168.0.15`)
- **Token**: o mesmo `AKEMI_TOKEN`
- **Nome**: como ela vai chamar este aparelho

Pronto. No app:

| Botão | O que faz |
|---|---|
| 🎤 | fala com ela (a fala dela para na hora) |
| ➤ | manda o texto |
| 🔊 / 🔇 | voz ligada ou só texto |
| ⏸ | pausa a autonomia dela (igual ao `/pausar`) |
| ⚙ | troca endereço, token ou nome |

Pelo celular dá para pedir ações no PC ("abre o Spotify", "pausa a música"),
se o PC estiver ligado. As que pedem "sim" continuam pedindo.

`/corpo` no console mostra se o celular está conectado, e `GET /saude` do
servidor também.

## Segurança

- Só entra quem tem o `AKEMI_TOKEN`. Token errado: a conexão é recusada.
- Funciona **na rede de casa** (mesmo Wi-Fi). A conexão é `ws://`, sem
  criptografia, como a do `akemi-corpo`: em Wi-Fi de casa tudo bem, em Wi-Fi
  público não.
- **Não abra a porta 8765 no roteador.** Para usar fora de casa, use uma VPN
  como o [Tailscale](https://tailscale.com) no PC e no celular, e coloque no
  app o endereço do PC na Tailscale (`ws://100.x.y.z:8765`).
- O token fica salvo no celular. Se perder o aparelho, troque o `AKEMI_TOKEN`.

## Limites desta primeira versão

- O Android pode encerrar o app em segundo plano depois de um tempo para
  economizar bateria. Aí as notificações param até você abrir o app de novo
  (ele reconecta sozinho). Para o app ficar sempre ligado, falta um serviço
  em primeiro plano, que é o próximo passo.
- A voz e o reconhecimento de fala são os do próprio celular (Google). Se
  não tiver a voz em português instalada, ela só escreve.
- Ela não ouve "Akemi, ..." com o app fechado: toque no 🎤.

## Compilar você mesmo

Com o [Flutter](https://docs.flutter.dev/get-started/install) instalado:

```powershell
cd app
flutter create --platforms=android --org com.mateus0790 --project-name akemi_app .
python tool/prepare_android.py
flutter run          # com o celular ligado no USB (depuração USB ativada)
```
