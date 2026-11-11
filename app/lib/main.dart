// App da Akemi: a mesma Akemi do PC, no celular.
//
// O chip (memória, jeito, estado) continua no PC ou no servidor. O celular só
// empresta a tela, o microfone e o alto-falante dele.

import 'package:flutter/material.dart';

import 'link.dart';
import 'notifier.dart';
import 'settings.dart';
import 'voice.dart';

const Color akemiPink = Color(0xFFFC3B7F);

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final settings = await AppSettings.load();
  runApp(AkemiApp(settings: settings));
}

class AkemiApp extends StatelessWidget {
  const AkemiApp({super.key, required this.settings});

  final AppSettings settings;

  @override
  Widget build(BuildContext context) {
    ThemeData theme(Brightness b) => ThemeData(
          colorScheme: ColorScheme.fromSeed(seedColor: akemiPink, brightness: b),
          useMaterial3: true,
        );
    return MaterialApp(
      title: 'Akemi',
      debugShowCheckedModeBanner: false,
      theme: theme(Brightness.light),
      darkTheme: theme(Brightness.dark),
      home: ChatPage(settings: settings),
    );
  }
}

// =================================================================== chat
class ChatPage extends StatefulWidget {
  const ChatPage({super.key, required this.settings});

  final AppSettings settings;

  @override
  State<ChatPage> createState() => _ChatPageState();
}

class _ChatPageState extends State<ChatPage> with WidgetsBindingObserver {
  final Voice _voice = Voice();
  final Notifier _notifier = Notifier();
  late final AkemiLink _link;
  final TextEditingController _input = TextEditingController();
  final ScrollController _scroll = ScrollController();
  bool _background = false;
  bool _listening = false;

  AppSettings get _s => widget.settings;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _voice.muted = _s.muted;
    _link = AkemiLink(
      speak: _voice.speak,
      stopSpeaking: _voice.stop,
      onAkemiText: (text) {
        if (_background) _notifier.show(_link.akemiName, text);
      },
    );
    _link.addListener(_onLinkChanged);
    _boot();
  }

  Future<void> _boot() async {
    await _voice.init();
    await _notifier.init();
    if (_s.complete) {
      await _link.start(_s.url, _s.token, _s.name);
    } else if (mounted) {
      WidgetsBinding.instance.addPostFrameCallback((_) => _openSettings());
    }
  }

  void _onLinkChanged() {
    if (!mounted) return;
    setState(() {});
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) {
        _scroll.animateTo(
          _scroll.position.maxScrollExtent,
          duration: const Duration(milliseconds: 200),
          curve: Curves.easeOut,
        );
      }
    });
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    _background = state != AppLifecycleState.resumed;
    if (state == AppLifecycleState.resumed) _link.wakeUp();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _link.removeListener(_onLinkChanged);
    _link.dispose();
    _input.dispose();
    _scroll.dispose();
    super.dispose();
  }

  // ------------------------------------------------------------ ações
  void _send() {
    if (_link.sendText(_input.text)) _input.clear();
  }

  Future<void> _toggleMic() async {
    if (_listening) {
      await _voice.stopListening();
      setState(() => _listening = false);
      return;
    }
    final ok = await _voice.ensureMic();
    if (!mounted) return;
    if (!ok) {
      _toast('Não consegui usar o microfone. Libere a permissão nas configurações do celular.');
      return;
    }
    _link.interrupt();
    setState(() => _listening = true);
    await _voice.listen((words, done) {
      if (!mounted) return;
      setState(() => _input.text = words);
      if (done) {
        setState(() => _listening = false);
        _send();
      }
    });
  }

  void _toggleMute() {
    setState(() {
      _voice.muted = _s.muted = !_s.muted;
    });
    if (_voice.muted) _voice.stop();
    _s.save();
  }

  Future<void> _openSettings() async {
    final changed = await Navigator.of(context).push<bool>(
      MaterialPageRoute(builder: (_) => SettingsPage(settings: _s)),
    );
    if (changed == true && _s.complete) {
      await _link.start(_s.url, _s.token, _s.name);
    }
  }

  void _toast(String text) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
  }

  // ------------------------------------------------------------ tela
  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(
        title: Row(
          children: [
            _StatusDot(status: _link.status),
            const SizedBox(width: 10),
            Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(_link.akemiName),
                Text(
                  _statusText(),
                  style: Theme.of(context).textTheme.labelSmall?.copyWith(color: scheme.onSurfaceVariant),
                ),
              ],
            ),
          ],
        ),
        actions: [
          IconButton(
            tooltip: _s.muted ? 'Ligar a voz' : 'Só texto',
            icon: Icon(_s.muted ? Icons.volume_off : Icons.volume_up),
            onPressed: _toggleMute,
          ),
          IconButton(
            tooltip: _link.paused ? 'Voltar a autonomia' : 'Pausar a autonomia',
            icon: Icon(_link.paused ? Icons.play_arrow : Icons.pause),
            onPressed: _link.connected ? _link.togglePause : null,
          ),
          IconButton(
            tooltip: 'Configurar',
            icon: const Icon(Icons.settings),
            onPressed: _openSettings,
          ),
        ],
      ),
      body: SafeArea(
        child: Column(
          children: [
            if (_link.lastError.isNotEmpty && !_link.connected)
              MaterialBanner(
                content: Text(_link.lastError),
                actions: [TextButton(onPressed: _openSettings, child: const Text('Configurar'))],
              ),
            Expanded(
              child: _link.items.isEmpty
                  ? Center(
                      child: Text(
                        _link.connected ? 'Fala com ela.' : 'Conectando na Akemi...',
                        style: TextStyle(color: scheme.onSurfaceVariant),
                      ),
                    )
                  : ListView.builder(
                      controller: _scroll,
                      padding: const EdgeInsets.all(12),
                      itemCount: _link.items.length,
                      itemBuilder: (_, i) => _Bubble(item: _link.items[i]),
                    ),
            ),
            _Composer(
              controller: _input,
              enabled: _link.connected,
              listening: _listening,
              onSend: _send,
              onMic: _toggleMic,
            ),
          ],
        ),
      ),
    );
  }

  String _statusText() {
    switch (_link.status) {
      case LinkStatus.conectado:
        return _link.paused ? 'pausada (só responde a você)' : 'conectada';
      case LinkStatus.conectando:
        return 'conectando...';
      case LinkStatus.tokenRecusado:
        return 'token recusado';
      case LinkStatus.desconectado:
        return 'desconectada';
    }
  }
}

class _StatusDot extends StatelessWidget {
  const _StatusDot({required this.status});

  final LinkStatus status;

  @override
  Widget build(BuildContext context) {
    final color = switch (status) {
      LinkStatus.conectado => Colors.green,
      LinkStatus.conectando => Colors.amber,
      _ => Colors.red,
    };
    return Container(width: 10, height: 10, decoration: BoxDecoration(color: color, shape: BoxShape.circle));
  }
}

class _Bubble extends StatelessWidget {
  const _Bubble({required this.item});

  final ChatItem item;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final mine = item.mine;
    return Align(
      alignment: mine ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.8),
        margin: const EdgeInsets.symmetric(vertical: 4),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        decoration: BoxDecoration(
          color: mine ? scheme.primary : scheme.surfaceContainerHighest,
          borderRadius: BorderRadius.circular(18),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(item.text, style: TextStyle(color: mine ? scheme.onPrimary : scheme.onSurface)),
            if (item.elsewhere)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  'falou no PC',
                  style: Theme.of(context).textTheme.labelSmall?.copyWith(color: scheme.onSurfaceVariant),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _Composer extends StatelessWidget {
  const _Composer({
    required this.controller,
    required this.enabled,
    required this.listening,
    required this.onSend,
    required this.onMic,
  });

  final TextEditingController controller;
  final bool enabled;
  final bool listening;
  final VoidCallback onSend;
  final VoidCallback onMic;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 4, 8, 12),
      child: Row(
        children: [
          Expanded(
            child: TextField(
              controller: controller,
              enabled: enabled,
              textInputAction: TextInputAction.send,
              onSubmitted: (_) => onSend(),
              minLines: 1,
              maxLines: 4,
              decoration: InputDecoration(
                hintText: listening ? 'Ouvindo...' : 'Mensagem para a Akemi',
                filled: true,
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(24), borderSide: BorderSide.none),
                contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
              ),
            ),
          ),
          const SizedBox(width: 6),
          IconButton.filledTonal(
            tooltip: listening ? 'Parar de ouvir' : 'Falar',
            onPressed: enabled ? onMic : null,
            icon: Icon(listening ? Icons.stop : Icons.mic),
            style: listening ? IconButton.styleFrom(backgroundColor: scheme.errorContainer) : null,
          ),
          IconButton.filled(
            tooltip: 'Enviar',
            onPressed: enabled ? onSend : null,
            icon: const Icon(Icons.send),
          ),
        ],
      ),
    );
  }
}

// ========================================================= configuração
class SettingsPage extends StatefulWidget {
  const SettingsPage({super.key, required this.settings});

  final AppSettings settings;

  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  late final TextEditingController _url = TextEditingController(text: widget.settings.url);
  late final TextEditingController _token = TextEditingController(text: widget.settings.token);
  late final TextEditingController _name = TextEditingController(text: widget.settings.name);
  bool _showToken = false;
  String _error = '';

  @override
  void dispose() {
    _url.dispose();
    _token.dispose();
    _name.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final url = AppSettings.normalizeUrl(_url.text);
    final token = _token.text.trim();
    if (url.isEmpty) {
      setState(() => _error = 'Coloque o endereço da Akemi.');
      return;
    }
    if (token.length < 16) {
      setState(() => _error = 'O token tem pelo menos 16 caracteres (é o AKEMI_TOKEN do .env).');
      return;
    }
    final s = widget.settings
      ..url = url
      ..token = token
      ..name = _name.text.trim().isEmpty ? 'celular' : _name.text.trim();
    await s.save();
    if (mounted) Navigator.of(context).pop(true);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Conectar na Akemi')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          const Text(
            'No PC, rode "akemi --celular" (ou deixe o servidor ligado). '
            'Ele mostra o endereço para colocar aqui. O celular precisa estar no mesmo Wi-Fi.',
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _url,
            keyboardType: TextInputType.url,
            autocorrect: false,
            decoration: const InputDecoration(
              labelText: 'Endereço',
              hintText: 'ws://192.168.0.10:8765',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _token,
            obscureText: !_showToken,
            autocorrect: false,
            enableSuggestions: false,
            decoration: InputDecoration(
              labelText: 'Token (AKEMI_TOKEN)',
              border: const OutlineInputBorder(),
              suffixIcon: IconButton(
                icon: Icon(_showToken ? Icons.visibility_off : Icons.visibility),
                onPressed: () => setState(() => _showToken = !_showToken),
              ),
            ),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _name,
            decoration: const InputDecoration(
              labelText: 'Nome deste celular',
              hintText: 'celular',
              border: OutlineInputBorder(),
            ),
          ),
          if (_error.isNotEmpty) ...[
            const SizedBox(height: 12),
            Text(_error, style: TextStyle(color: Theme.of(context).colorScheme.error)),
          ],
          const SizedBox(height: 20),
          FilledButton(onPressed: _save, child: const Text('Salvar e conectar')),
        ],
      ),
    );
  }
}
