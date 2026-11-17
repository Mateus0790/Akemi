"""Pure audio helpers used by capture and VAD (no PortAudio dependency)."""

from __future__ import annotations

import numpy as np

PCM16_DTYPE = np.int16
PCM16_MAX = 32767
PCM16_MIN = -32768


def downmix_to_mono_int16(pcm_bytes: bytes, channels: int) -> np.ndarray:
    """Convert interleaved int16 PCM to a mono int16 array."""
    if channels < 1:
        raise ValueError("channels must be >= 1")
    samples = np.frombuffer(pcm_bytes, dtype=PCM16_DTYPE)
    if channels == 1:
        return samples.copy()
    usable = (len(samples) // channels) * channels
    if usable == 0:
        return np.array([], dtype=PCM16_DTYPE)
    framed = samples[:usable].reshape(-1, channels).astype(np.float32)
    mono = framed.mean(axis=1)
    return np.clip(np.rint(mono), PCM16_MIN, PCM16_MAX).astype(PCM16_DTYPE)


def resample_int16(
    samples: np.ndarray,
    src_rate: int,
    dst_rate: int,
    carry: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Linear-resample int16 mono audio.

    Returns (output, leftover_source_samples) so the caller can keep
    fractional frames across PortAudio callbacks.
    """
    if src_rate <= 0 or dst_rate <= 0:
        raise ValueError("sample rates must be positive")
    if samples.dtype != PCM16_DTYPE:
        samples = samples.astype(PCM16_DTYPE)
    if carry is not None and len(carry):
        combined = np.concatenate([carry, samples])
    else:
        combined = samples
    if src_rate == dst_rate:
        return combined, np.array([], dtype=PCM16_DTYPE)
    if len(combined) < 2:
        return np.array([], dtype=PCM16_DTYPE), combined

    ratio = src_rate / dst_rate
    n_out = int((len(combined) - 1) / ratio)
    if n_out <= 0:
        return np.array([], dtype=PCM16_DTYPE), combined

    positions = np.arange(n_out, dtype=np.float64) * ratio
    idx = positions.astype(np.int64)
    frac = positions - idx
    y0 = combined[idx].astype(np.float64)
    y1 = combined[idx + 1].astype(np.float64)
    out = y0 + frac * (y1 - y0)
    consumed = int(np.floor(positions[-1])) + 1
    leftover = combined[consumed:]
    return np.clip(np.rint(out), PCM16_MIN, PCM16_MAX).astype(PCM16_DTYPE), leftover


class StreamResampler:
    """Stateful wrapper around :func:`resample_int16`."""

    def __init__(self, src_rate: int, dst_rate: int):
        self.src_rate = src_rate
        self.dst_rate = dst_rate
        self._carry = np.array([], dtype=PCM16_DTYPE)

    def push(self, samples: np.ndarray) -> np.ndarray:
        out, self._carry = resample_int16(
            samples, self.src_rate, self.dst_rate, self._carry
        )
        return out


class FrameAssembler:
    """Split a PCM byte stream into fixed-size frames (e.g. 20 ms for VAD)."""

    def __init__(self, frame_bytes: int):
        if frame_bytes <= 0:
            raise ValueError("frame_bytes must be positive")
        self.frame_bytes = frame_bytes
        self._buf = bytearray()

    def push(self, data: bytes) -> list[bytes]:
        if not data:
            return []
        self._buf.extend(data)
        frames: list[bytes] = []
        size = self.frame_bytes
        while len(self._buf) >= size:
            frames.append(bytes(self._buf[:size]))
            del self._buf[:size]
        return frames

    def clear(self) -> None:
        self._buf.clear()


class AudioPipeline:
    """Um callback inteiro: bytes do dispositivo (int16 intercalado) → quadros mono.

    Junta downmix, resample e fatiamento. É a versão em Python do
    ``akemi_audio_rs.AudioPipeline`` (Rust), com o mesmo resultado byte a byte;
    :func:`akemi.audio.native.make_pipeline` escolhe uma das duas.
    """

    def __init__(self, device_rate: int, channels: int, output_rate: int = 16000, frame_ms: int = 20):
        if channels < 1:
            raise ValueError("channels must be >= 1")
        self.device_rate = device_rate
        self.output_rate = output_rate
        self.channels = channels
        self.frame_bytes = output_rate * frame_ms // 1000 * 2
        self.frames_out = 0
        self._resampler = StreamResampler(device_rate, output_rate)
        self._frames = FrameAssembler(self.frame_bytes)

    def push(self, data: bytes) -> list[bytes]:
        mono = downmix_to_mono_int16(data, self.channels)
        frames = self._frames.push(self._resampler.push(mono).tobytes())
        self.frames_out += len(frames)
        return frames

    def clear(self) -> None:
        self._resampler = StreamResampler(self.device_rate, self.output_rate)
        self._frames.clear()
