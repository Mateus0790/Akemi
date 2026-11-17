# App do celular em Rust

A mesma Akemi do PC, no Android, agora feita em Rust. O código fica em
`rust-core/akemi-celular`. Ele faz o mesmo que o [app em Flutter](CELULAR.md)
e usa o mesmo protocolo (`akemi/net/companion.py`): você pode usar qualquer um
dos dois, e a configuração do PC é a mesma.

- **Tela**: [Slint](https://slint.dev), com o teclado do Android de verdade
  (acentos, corretor, sugestões, copiar e colar)
- **Conversa**: WebSocket com o núcleo, reconecta sozinho quando cai
- **Voz**: a do próprio celular (TextToSpeech do Android), igual ao app Flutter
- **Notificações**: o que ela disser com o app em segundo plano (lembretes,
  iniciativas, avisos). Tocar na notificação volta para o app
- **O rosto dela no topo** muda: parada, falando, ou dormindo quando está
  pausada ou desconectada

## 1. Baixar o APK

O GitHub compila o APK a cada mudança em `rust-core/akemi-celular`:

1. No GitHub: **Actions → Celular (Rust)** → a execução mais recente (verde).
2. Em **Artifacts**, baixe `akemi-celular-apk` (é um zip com o `akemi-celular.apk`).
3. Passe o APK para o celular e abra. O Android vai pedir para permitir
   "instalar apps desta fonte".

O APK é para celulares de 64 bits (ARM64, praticamente todos de 2017 para cá)
com Android 8 ou mais novo. Ele se chama **Akemi** e tem o rosto dela no ícone.
Pode ficar instalado ao lado do app Flutter (são apps diferentes).

Ao atualizar, pode ser preciso desinstalar a versão antiga antes: o APK de
teste é assinado com uma chave nova a cada compilação.

## 2. Ligar a porta do celular no PC

Igual ao app Flutter ([CELULAR.md, passo 2](CELULAR.md#2-ligar-a-porta-do-celular)):
`AKEMI_TOKEN` no `.env` e `akemi --voz --mic --celular` (ou o servidor no ZimaOS).

## 3. Conectar

Na primeira vez o app abre **Conectar na Akemi**: endereço (pode ser só o IP,
`192.168.0.15`), o `AKEMI_TOKEN` e o nome do aparelho.

| Botão | O que faz |
|---|---|
| ➤ (ou Enter) | manda a mensagem |
| 🔊 / 🔇 | voz ligada ou só texto |
| ⏸ / ▶ | pausa a autonomia dela (igual ao `/pausar`) |
| ⚙ | troca endereço, token ou nome |

**Falar em vez de digitar:** toque no 🎤 do teclado (Gboard e a maioria dos
teclados têm). Começou a digitar ou ditar enquanto ela falava? Ela para na hora,
como quando você fala com ela no PC.

## Diferenças para o app Flutter

| | Flutter (`app/`) | Rust (`rust-core/akemi-celular`) |
|---|---|---|
| Microfone | botão 🎤 no app | o 🎤 do teclado |
| iPhone | o código serve (falta Mac) | só Android por enquanto |

O resto (protocolo, token, reconexão, histórico, notificações, pausa, voz) é igual.

## Segurança

As mesmas regras do [app Flutter](CELULAR.md#segurança): só entra quem tem o
`AKEMI_TOKEN`, a conexão é `ws://` (sem criptografia) e é para a rede de casa.
Fora de casa, use a [Tailscale](https://tailscale.com) e coloque no app o
endereço do PC na Tailscale. Não abra a porta 8765 no roteador.

## Testar no PC, sem celular

A mesma tela abre numa janela no PC (sem voz e sem notificação):

```powershell
cargo run --manifest-path rust-core/Cargo.toml -p akemi-celular
```

Os testes conferem a lógica, a rede e uma conversa inteira com o servidor de
verdade do celular:

```powershell
cargo test --manifest-path rust-core/Cargo.toml -p akemi-celular
cargo build --manifest-path rust-core/Cargo.toml -p akemi-celular --example conversa
python rust-core/akemi-celular/tests/nucleo_de_teste.py
```

## Compilar o APK você mesmo

Precisa do [Android Studio](https://developer.android.com/studio) (ou só o SDK e
o NDK), do Java 17 e do [Rust](https://rustup.rs):

```powershell
rustup target add aarch64-linux-android
cargo install cargo-apk
$env:ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk"
$env:ANDROID_NDK_ROOT = "$env:ANDROID_HOME\ndk\<versão>"
cd rust-core
cargo apk run -p akemi-celular --lib     # com o celular no USB (depuração USB ativada)
```

## Como o código está dividido

| Arquivo | O que faz |
|---|---|
| `src/core.rs` | o protocolo e o estado da conversa (sem rede, testado) |
| `src/link.rs` | a linha de rede (conecta, reconecta) e a linha de voz |
| `src/settings.rs` | endereço, token e nome, salvos no celular |
| `src/app.rs` | liga a tela à conversa |
| `src/android.rs` | a entrada no Android |
| `src/droid.rs` | voz e notificações do Android (JNI) |
| `ui/app.slint` | a tela |

## Limites desta primeira versão

Os mesmos do app Flutter: o Android pode fechar o app em segundo plano depois de
um tempo (aí as notificações param até você abrir de novo; ele reconecta
sozinho), e ela não ouve "Akemi, ..." com o app fechado.
