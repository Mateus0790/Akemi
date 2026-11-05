// Conexão com o núcleo da Akemi (o mesmo protocolo do akemi-corpo do PC).
//
// O celular se apresenta como "celular": entra ao lado do PC, sem derrubar ele.
// Ela responde onde você falou por último. Ver akemi/net/companion.py.

import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

const int protocolVersion = 1;
const int closeUnauthorized = 4401;
const int closeBadMessage = 4400;

enum LinkStatus { desconectado, conectando, conectado, tokenRecusado }

class ChatItem {
  ChatItem(this.from, this.text, {this.elsewhere = false}) : at = DateTime.now();

  final String from; // 'voce' | 'akemi'
  final String text;
  final bool elsewhere; // ela disse isso em outro lugar (no PC)
  final DateTime at;

  bool get mine => from == 'voce';
}

typedef Speak = Future<void> Function(String text);

class AkemiLink extends ChangeNotifier {
  AkemiLink({required this.speak, required this.stopSpeaking, this.onAkemiText});

  /// Fala em voz alta e só termina quando acabar de falar.
  final Speak speak;
  final Future<void> Function() stopSpeaking;

  /// Tudo o que ela disser passa aqui (para as notificações).
  void Function(String text)? onAkemiText;

  final List<ChatItem> items = [];
  LinkStatus status = LinkStatus.desconectado;
  bool paused = false;
  String akemiName = 'Akemi';
  String lastError = '';

  /// Nos testes: recebe o que seria mandado pela rede.
  @visibleForTesting
  void Function(String raw)? debugSink;

  WebSocketChannel? _channel;
  StreamSubscription<dynamic>? _sub;
  Timer? _retry;
  String _url = '';
  String _token = '';
  String _name = '';
  bool _wanted = false;
  bool _disposed = false;
  int _delay = 1;

  final List<Map<String, dynamic>> _sayQueue = [];
  final Set<Object?> _interrupted = {};
  Object? _currentId;
  bool _speaking = false;

  bool get connected => status == LinkStatus.conectado;

  // ------------------------------------------------------------ conexão
  Future<void> start(String url, String token, String name) async {
    _url = url.trim();
    _token = token.trim();
    _name = name.trim();
    _wanted = true;
    _delay = 1;
    lastError = '';
    _retry?.cancel();
    await _close();
    unawaited(_connect());
  }

  Future<void> stop() async {
    _wanted = false;
    _retry?.cancel();
    await _close();
    _set(LinkStatus.desconectado);
  }

  /// O app voltou para a frente: se caiu, tenta já em vez de esperar o próximo intervalo.
  void wakeUp() {
    if (_wanted && _channel == null) {
      _retry?.cancel();
      _delay = 1;
      unawaited(_connect());
    }
  }

  void _set(LinkStatus s) {
    status = s;
    if (!_disposed) notifyListeners();
  }

  Future<void> _close() async {
    final sub = _sub;
    final channel = _channel;
    _sub = null;
    _channel = null;
    await sub?.cancel();
    await channel?.sink.close();
  }

  Future<void> _connect() async {
    if (!_wanted || _disposed) return;
    _set(LinkStatus.conectando);
    final Uri uri;
    try {
      uri = Uri.parse(_url);
      if (uri.scheme != 'ws' && uri.scheme != 'wss') throw const FormatException();
    } catch (_) {
      lastError = 'Endereço inválido. Use algo como ws://192.168.0.10:8765';
      _wanted = false;
      _set(LinkStatus.desconectado);
      return;
    }
    final channel = WebSocketChannel.connect(uri);
    _channel = channel;
    try {
      await channel.ready.timeout(const Duration(seconds: 10));
    } catch (_) {
      if (identical(_channel, channel)) _channel = null;
      lastError = 'Não achei a Akemi em $_url. O PC está ligado e no mesmo Wi-Fi?';
      _scheduleRetry();
      return;
    }
    if (!identical(_channel, channel)) return; // trocaram o endereço no meio
    _sub = channel.stream.listen(
      (raw) => handleMessage(raw is String ? raw : utf8.decode(raw as List<int>)),
      onDone: () => _onClosed(channel),
      onError: (Object _) => _onClosed(channel),
      cancelOnError: true,
    );
    _send('hello', {
      'token': _token,
      'version': protocolVersion,
      'corpo': {
        'nome': _name.isEmpty ? 'celular' : _name,
        'tipo': 'celular',
        'sentidos': ['voz', 'microfone'],
      },
    });
  }

  void _onClosed(WebSocketChannel channel) {
    if (!identical(channel, _channel)) return;
    final code = channel.closeCode;
    _channel = null;
    _sub = null;
    _stopLocal(notify: false);
    if (code == closeUnauthorized) {
      lastError = 'A Akemi recusou o token. Confira o AKEMI_TOKEN.';
      _wanted = false;
      _set(LinkStatus.tokenRecusado);
      return;
    }
    if (code == closeBadMessage) {
      lastError = channel.closeReason ?? 'Conexão recusada.';
    }
    _scheduleRetry();
  }

  void _scheduleRetry() {
    if (!_wanted || _disposed) {
      _set(LinkStatus.desconectado);
      return;
    }
    _set(LinkStatus.conectando);
    _retry?.cancel();
    _retry = Timer(Duration(seconds: _delay), () => unawaited(_connect()));
    _delay = _delay >= 15 ? 30 : _delay * 2;
  }

  void _send(String type, [Map<String, dynamic> fields = const {}]) {
    final raw = jsonEncode({'type': type, ...fields});
    final sink = debugSink;
    if (sink != null) {
      sink(raw);
      return;
    }
    _channel?.sink.add(raw);
  }

  // ------------------------------------------------------------ recepção
  @visibleForTesting
  void handleMessage(String raw) {
    final Object? decoded;
    try {
      decoded = jsonDecode(raw);
    } catch (_) {
      return;
    }
    if (decoded is! Map<String, dynamic>) return;
    final msg = decoded;
    switch (msg['type']) {
      case 'welcome':
        _delay = 1;
        lastError = '';
        paused = msg['paused'] == true;
        akemiName = msg['nome'] is String ? msg['nome'] as String : 'Akemi';
        items.clear();
        final history = msg['historico'];
        if (history is List) {
          for (final h in history) {
            if (h is Map && h['texto'] is String) {
              items.add(ChatItem(h['de'] == 'voce' ? 'voce' : 'akemi', h['texto'] as String));
            }
          }
        }
        _set(LinkStatus.conectado);
      case 'say':
        final text = msg['text'];
        if (text is String && text.isNotEmpty) {
          _add(ChatItem('akemi', text));
          onAkemiText?.call(text);
          _sayQueue.add(msg);
          unawaited(_drain());
        }
      case 'fala':
        final text = msg['text'];
        if (text is String && text.isNotEmpty) {
          _add(ChatItem('akemi', text, elsewhere: true));
          onAkemiText?.call(text);
        }
      case 'stop':
        _stopLocal();
      case 'paused':
        paused = msg['paused'] == true;
        if (!_disposed) notifyListeners();
    }
  }

  void _add(ChatItem item) {
    items.add(item);
    if (items.length > 200) items.removeRange(0, items.length - 200);
    if (!_disposed) notifyListeners();
  }

  Future<void> _drain() async {
    if (_speaking) return;
    _speaking = true;
    while (_sayQueue.isNotEmpty) {
      final msg = _sayQueue.removeAt(0);
      _currentId = msg['id'];
      var result = 'done';
      try {
        await speak(msg['text'] as String);
      } catch (_) {
        result = 'error';
      }
      if (_interrupted.remove(msg['id'])) result = 'interrupted';
      _send('spoken', {'id': msg['id'], 'status': result});
    }
    _currentId = null;
    _speaking = false;
  }

  /// Para de falar e avisa o núcleo do que ficou sem falar.
  void _stopLocal({bool notify = true}) {
    for (final msg in _sayQueue) {
      if (notify) _send('spoken', {'id': msg['id'], 'status': 'interrupted'});
    }
    _sayQueue.clear();
    if (_currentId != null) _interrupted.add(_currentId);
    unawaited(stopSpeaking());
  }

  // ------------------------------------------------------------- envio
  /// Você digitou ou falou algo para ela.
  bool sendText(String text) {
    text = text.trim();
    if (text.isEmpty || !connected) return false;
    _stopLocal();
    _add(ChatItem('voce', text));
    _send('owner_message', {'text': text});
    return true;
  }

  /// Você começou a falar no microfone: ela para na hora.
  void interrupt() {
    if (!connected) return;
    _stopLocal();
    _send('owner_speech_started');
  }

  void togglePause() {
    if (!connected) return;
    _send(paused ? 'resume' : 'pause');
  }

  @override
  void dispose() {
    _disposed = true;
    _wanted = false;
    _retry?.cancel();
    unawaited(_close());
    super.dispose();
  }
}
