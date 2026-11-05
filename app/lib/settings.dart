// Onde está a Akemi e como entrar (guardado no celular).

import 'package:shared_preferences/shared_preferences.dart';

class AppSettings {
  AppSettings({this.url = '', this.token = '', this.name = 'celular', this.muted = false});

  String url;
  String token;
  String name;
  bool muted;

  bool get complete => url.isNotEmpty && token.length >= 16;

  static Future<AppSettings> load() async {
    final p = await SharedPreferences.getInstance();
    return AppSettings(
      url: p.getString('url') ?? '',
      token: p.getString('token') ?? '',
      name: p.getString('name') ?? 'celular',
      muted: p.getBool('muted') ?? false,
    );
  }

  Future<void> save() async {
    final p = await SharedPreferences.getInstance();
    await p.setString('url', url);
    await p.setString('token', token);
    await p.setString('name', name);
    await p.setBool('muted', muted);
  }

  /// Aceita "192.168.0.10", "192.168.0.10:8765" ou o endereço completo.
  static String normalizeUrl(String input) {
    var s = input.trim();
    if (s.isEmpty) return s;
    if (!s.contains('://')) s = 'ws://$s';
    final uri = Uri.tryParse(s);
    if (uri != null && !uri.hasPort && (uri.scheme == 'ws')) {
      s = uri.replace(port: 8765).toString();
    }
    return s.endsWith('/') ? s.substring(0, s.length - 1) : s;
  }
}
