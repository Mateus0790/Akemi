# App da Akemi (Flutter)

A mesma Akemi do PC, no celular. Como baixar, ligar e conectar:
[docs/CELULAR.md](../docs/CELULAR.md).

- `lib/link.dart`: conexão com o núcleo (protocolo em `akemi/net/companion.py`)
- `lib/main.dart`: telas de chat e de conexão
- `lib/voice.dart`: voz e microfone do celular
- `lib/notifier.dart`: notificações em segundo plano
- `tool/prepare_android.py`: ajuste do Android depois do `flutter create`
