import 'package:akemi_app/main.dart';
import 'package:akemi_app/settings.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('tela de conexão pede o token completo', (tester) async {
    await tester.pumpWidget(MaterialApp(home: SettingsPage(settings: AppSettings())));
    expect(find.text('Conectar na Akemi'), findsOneWidget);
    await tester.enterText(find.byType(TextField).at(0), '192.168.0.10');
    await tester.enterText(find.byType(TextField).at(1), 'curto');
    await tester.tap(find.text('Salvar e conectar'));
    await tester.pump();
    expect(find.textContaining('16 caracteres'), findsOneWidget);
  });
}
