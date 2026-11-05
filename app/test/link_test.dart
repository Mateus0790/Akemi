import 'dart:convert';

import 'package:akemi_app/link.dart';
import 'package:akemi_app/settings.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  late List<Map<String, dynamic>> sent;
  late List<String> spoken;
  late AkemiLink link;

  setUp(() {
    sent = [];
    spoken = [];
    link = AkemiLink(
      speak: (t) async => spoken.add(t),
      stopSpeaking: () async {},
    )..debugSink = (raw) => sent.add(jsonDecode(raw) as Map<String, dynamic>);
  });

  test('welcome traz o histórico e o nome', () {
    link.handleMessage(jsonEncode({
      'type': 'welcome',
      'paused': false,
      'nome': 'Akemi',
      'historico': [
        {'de': 'voce', 'texto': 'oi'},
        {'de': 'akemi', 'texto': 'Oi! Sumiu, hein.'},
      ],
    }));
    expect(link.connected, isTrue);
    expect(link.items.map((i) => i.text), ['oi', 'Oi! Sumiu, hein.']);
    expect(link.items.first.mine, isTrue);
  });

  test('say fala em voz alta e confirma para o núcleo', () async {
    link.handleMessage(jsonEncode({'type': 'welcome'}));
    link.handleMessage(jsonEncode({'type': 'say', 'id': 7, 'text': 'Tô aqui.'}));
    await Future<void>.delayed(Duration.zero);
    expect(spoken, ['Tô aqui.']);
    expect(sent.last, {'type': 'spoken', 'id': 7, 'status': 'done'});
  });

  test('fala do PC só aparece no chat, sem voz', () async {
    final notified = <String>[];
    link.onAkemiText = notified.add;
    link.handleMessage(jsonEncode({'type': 'fala', 'text': 'Lembrete: água'}));
    await Future<void>.delayed(Duration.zero);
    expect(spoken, isEmpty);
    expect(link.items.single.elsewhere, isTrue);
    expect(notified, ['Lembrete: água']);
  });

  test('mensagem só sai conectado', () {
    expect(link.sendText('oi'), isFalse);
    link.handleMessage(jsonEncode({'type': 'welcome'}));
    expect(link.sendText('  oi akemi  '), isTrue);
    expect(sent.last, {'type': 'owner_message', 'text': 'oi akemi'});
  });

  test('mensagens quebradas são ignoradas', () {
    link.handleMessage('isso não é json');
    link.handleMessage(jsonEncode([1, 2]));
    expect(link.items, isEmpty);
  });

  test('endereço aceita só o IP', () {
    expect(AppSettings.normalizeUrl('192.168.0.10'), 'ws://192.168.0.10:8765');
    expect(AppSettings.normalizeUrl('ws://192.168.0.10:9000/'), 'ws://192.168.0.10:9000');
  });
}
