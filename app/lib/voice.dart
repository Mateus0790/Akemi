// Voz e microfone do próprio celular (os do sistema: funciona offline na maioria dos aparelhos).

import 'package:flutter_tts/flutter_tts.dart';
import 'package:speech_to_text/speech_to_text.dart';

class Voice {
  final FlutterTts _tts = FlutterTts();
  final SpeechToText _stt = SpeechToText();
  bool muted = false;
  bool micReady = false;

  Future<void> init() async {
    try {
      await _tts.setLanguage('pt-BR');
      await _tts.awaitSpeakCompletion(true);
    } catch (_) {
      // Sem voz em português instalada: ela continua escrevendo no chat.
    }
  }

  /// Pede a permissão do microfone na primeira vez que você toca no botão.
  Future<bool> ensureMic() async {
    if (micReady) return true;
    try {
      micReady = await _stt.initialize(onError: (_) {}, onStatus: (_) {});
    } catch (_) {
      micReady = false;
    }
    return micReady;
  }

  Future<void> speak(String text) async {
    if (muted) return;
    await _tts.speak(text);
  }

  Future<void> stop() async {
    try {
      await _tts.stop();
    } catch (_) {}
  }

  bool get listening => _stt.isListening;

  Future<void> listen(void Function(String words, bool done) onResult) async {
    await _stt.listen(
      onResult: (r) => onResult(r.recognizedWords, r.finalResult),
      localeId: 'pt_BR',
      listenFor: const Duration(seconds: 30),
      pauseFor: const Duration(seconds: 3),
      listenOptions: SpeechListenOptions(partialResults: true, cancelOnError: true),
    );
  }

  Future<void> stopListening() => _stt.stop();
}
