// Notificações: o que ela disser com o app em segundo plano (lembretes, iniciativas, avisos).

import 'package:flutter_local_notifications/flutter_local_notifications.dart';

class Notifier {
  final FlutterLocalNotificationsPlugin _plugin = FlutterLocalNotificationsPlugin();
  bool _ready = false;
  int _next = 0;

  Future<void> init() async {
    try {
      const android = AndroidInitializationSettings('@mipmap/ic_launcher');
      await _plugin.initialize(const InitializationSettings(android: android));
      await _plugin
          .resolvePlatformSpecificImplementation<AndroidFlutterLocalNotificationsPlugin>()
          ?.requestNotificationsPermission();
      _ready = true;
    } catch (_) {
      _ready = false;
    }
  }

  Future<void> show(String title, String body) async {
    if (!_ready) return;
    _next = (_next + 1) % 50;
    await _plugin.show(
      _next,
      title,
      body,
      const NotificationDetails(
        android: AndroidNotificationDetails(
          'akemi_falas',
          'Falas da Akemi',
          channelDescription: 'O que a Akemi diz enquanto o app está em segundo plano',
          importance: Importance.high,
          priority: Priority.high,
        ),
      ),
    );
  }
}
