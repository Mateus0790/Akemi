from __future__ import annotations

import sys
from typing import Any, Callable, Optional

import structlog

from . import native
from .ring_buffer import RingBuffer

logger = structlog.get_logger()

TARGET_RATE = 16000
TARGET_CHANNELS = 1
VAD_FRAME_MS = 20


class AudioCapture:
    """Captures WASAPI loopback audio and yields 16 kHz mono PCM for VAD/STT."""

    def __init__(
        self,
        chunk_size: int = 1024,
        sample_rate: int = TARGET_RATE,
        channels: int = TARGET_CHANNELS,
        buffer_size_chunks: int = 250,
    ):
        """Initialize the audio capture.

        ``sample_rate`` / ``channels`` are the *output* format after downmix
        and resample. The WASAPI stream always uses the device native format.
        """
        self.output_rate = sample_rate
        self.output_channels = channels
        self.chunk_size = chunk_size
        self.buffer_size_chunks = buffer_size_chunks
        # Back-compat aliases used by get_recent_audio / older callers.
        self.sample_rate = sample_rate
        self.channels = channels

        self._audio = None
        self._stream = None
        self._pyaudio = None
        self._ring_buffer = RingBuffer(buffer_size_chunks)
        self._is_running = False
        self._callback: Optional[Callable[[bytes], None]] = None
        self._device_rate = sample_rate
        self._device_channels = 1
        # Downmix + resample + quadros de 20 ms (Rust se instalado, senão Python).
        self._pipeline: Optional[Any] = None

    def _audio_callback(self, in_data, frame_count, time_info, status):
        pa = self._pyaudio
        continue_flag = pa.paContinue if pa is not None else 0
        try:
            if status:
                logger.warning("Audio stream status", status=status)
            if not in_data:
                return (None, continue_flag)

            pipeline = self._pipeline
            if pipeline is None:
                return (None, continue_flag)
            for frame in pipeline.push(bytes(in_data)):
                self._ring_buffer.append(frame)
                if self._callback is not None:
                    self._callback(frame)
        except Exception as exc:
            logger.error("Audio callback failed", error=str(exc))
        return (None, continue_flag)

    def _resolve_loopback_device(self, audio):
        pa = self._pyaudio
        if hasattr(audio, "get_default_wasapi_loopback"):
            try:
                return audio.get_default_wasapi_loopback()
            except (OSError, LookupError) as exc:
                logger.warning("get_default_wasapi_loopback failed", error=str(exc))

        try:
            wasapi_info = audio.get_host_api_info_by_type(pa.paWASAPI)
        except OSError as exc:
            raise RuntimeError("WASAPI is not available on this system") from exc

        default_speakers = audio.get_device_info_by_index(
            wasapi_info["defaultOutputDevice"]
        )
        if default_speakers.get("isLoopbackDevice"):
            return default_speakers

        for loopback in audio.get_loopback_device_info_generator():
            if default_speakers["name"] in loopback["name"]:
                return loopback
        raise RuntimeError("No suitable WASAPI loopback device found")

    def start(self, callback: Optional[Callable[[bytes], None]] = None) -> None:
        if sys.platform != "win32":
            raise RuntimeError("AudioCapture requires Windows WASAPI loopback")
        if self._is_running:
            logger.warning("Audio capture already started")
            return

        try:
            import pyaudiowpatch as pyaudio
        except ImportError as exc:
            raise RuntimeError(
                "PyAudioWPatch is required. Install with: pip install -e \".[audio]\""
            ) from exc

        self._callback = callback
        self._pyaudio = pyaudio
        self._audio = pyaudio.PyAudio()
        try:
            loopback_device = self._resolve_loopback_device(self._audio)
            self._device_rate = int(loopback_device["defaultSampleRate"])
            self._device_channels = int(loopback_device["maxInputChannels"]) or 2
            self._pipeline = native.make_pipeline(
                self._device_rate, self._device_channels, self.output_rate, VAD_FRAME_MS
            )

            logger.info(
                "Using loopback device",
                name=loopback_device["name"],
                device_sample_rate=self._device_rate,
                device_channels=self._device_channels,
                output_sample_rate=self.output_rate,
                audio_core=native.BACKEND,
            )

            self._stream = self._audio.open(
                format=pyaudio.paInt16,
                channels=self._device_channels,
                rate=self._device_rate,
                input=True,
                input_device_index=loopback_device["index"],
                frames_per_buffer=self.chunk_size,
                stream_callback=self._audio_callback,
            )
            self._stream.start_stream()
            self._is_running = True
            logger.info("Audio capture started")
        except Exception:
            logger.exception("Failed to start audio capture")
            self.stop()
            raise

    def stop(self) -> None:
        """Stop capture and always release PortAudio resources."""
        self._is_running = False
        stream, audio = self._stream, self._audio
        self._stream = None
        self._audio = None
        self._pyaudio = None
        if self._pipeline is not None:
            self._pipeline.clear()
            self._pipeline = None
        if stream is not None:
            try:
                if stream.is_active():
                    stream.stop_stream()
                stream.close()
            except Exception as exc:
                logger.warning("Error stopping audio stream", error=str(exc))
        if audio is not None:
            try:
                audio.terminate()
            except Exception as extra:
                logger.warning("Error terminating PyAudio", error=str(extra))
        self._ring_buffer.clear()
        logger.info("Audio capture stopped")

    def get_recent_audio(self, seconds: float = 1.0) -> list[bytes]:
        """Return recent *output* frames (16 kHz mono, 20 ms each)."""
        if seconds <= 0:
            return []
        frame_samples = self.output_rate * VAD_FRAME_MS // 1000
        chunks_needed = max(1, int(round((self.output_rate * seconds) / frame_samples)))
        all_chunks = self._ring_buffer.get()
        return all_chunks[-chunks_needed:]

    def is_running(self) -> bool:
        return self._is_running
