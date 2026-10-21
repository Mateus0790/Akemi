from akemi.stt.transcriber import SpeechSegmenter

SPEECH, SILENCE = b"S" * 640, b"\x00" * 640


def test_segmenter_emits_segment_with_preroll():
    seg = SpeechSegmenter(lambda f: f == SPEECH, start_frames=3, end_silence_ms=100, preroll_ms=100)
    out = [seg.push(f) for f in [SILENCE] * 5 + [SPEECH] * 10 + [SILENCE] * 5]
    segments = [s for s in out if s]
    assert len(segments) == 1
    # 5 frames de preroll (inclui 2 de silêncio antes) + 7 de fala + 5 de silêncio final
    assert len(segments[0]) == (5 + 7 + 5) * 640
    assert not seg.in_speech


def test_segmenter_ignores_short_blips_and_caps_length():
    seg = SpeechSegmenter(lambda f: f == SPEECH, start_frames=3, max_segment_s=0.2)
    assert all(seg.push(f) is None for f in [SPEECH, SPEECH, SILENCE] * 3)
    out = [seg.push(SPEECH) for _ in range(20)]
    assert any(out)
