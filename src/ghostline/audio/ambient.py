"""Ambient noise loader and mixer.

Loads a background-noise WAV file once at startup and mixes it into outgoing
PCM frames to mask the silence of an AI-driven call. Falls back to synthetic
Gaussian noise if the configured file is missing (preserves lab-mode UX).
"""

from __future__ import annotations

import logging
import wave
from pathlib import Path
from typing import Final

import numpy as np
from scipy.signal import resample_poly

from ghostline.audio.codec import TWILIO_CHUNK_SIZE, mix_pcm, pcm_to_ulaw

__all__ = ("AmbientNoise",)

_LOGGER = logging.getLogger(__name__)

# Target Twilio Media Streams sample rate (8 kHz, single channel).
_TARGET_RATE: Final[int] = 8000
# Default mix ratio; playbook ``ambient_ratio`` overrides per-stage.
_DEFAULT_RATIO: Final[float] = 0.1
# Synthetic fallback noise duration (seconds) when no file is found.
_FALLBACK_SECONDS: Final[int] = 10
# Synthetic fallback noise amplitude (RMS-ish stddev on int16 scale).
_FALLBACK_NOISE_STD: Final[int] = 50


class AmbientNoise:
    """Caches ambient PCM and exposes mixing/encoding helpers.

    Thread-safety: ``mix`` is called from a single asyncio task per call
    (the ``pump_out`` coroutine), so we do not lock. The internal cursor
    is updated atomically per call so concurrent calls on the same instance
    would interleave; the composition root creates one per call if needed.
    """

    def __init__(
        self, path: Path | str = "ambient_noise.wav", target_rate: int = _TARGET_RATE
    ) -> None:
        self.target_rate = target_rate
        self.buffer: np.ndarray[tuple[int], np.dtype[np.int16]]
        self._path = Path(path)
        self.buffer = self._load()
        self._idx = 0

    def _load(self) -> np.ndarray[tuple[int], np.dtype[np.int16]]:
        """Load the ambient file, resampling to ``target_rate`` mono int16."""
        try:
            return self._load_from_file()
        except FileNotFoundError:
            _LOGGER.warning("Ambient noise file not found at %s; using synthetic noise", self._path)
            return self._synthetic_noise()

    def _load_from_file(self) -> np.ndarray[tuple[int], np.dtype[np.int16]]:
        with wave.open(str(self._path), "rb") as wf:
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)
            n_channels = wf.getnchannels()
            frame_rate = wf.getframerate()

        samples = np.frombuffer(raw, dtype="<i2").astype(np.int32)
        if n_channels > 1:
            samples = samples.reshape(-1, n_channels).mean(axis=1).astype(np.int32)
        if frame_rate != self.target_rate:
            samples = resample_poly(samples, self.target_rate, frame_rate).astype(np.int32)
        out = np.clip(samples, -32768, 32767).astype("<i2")
        _LOGGER.info("Loaded ambient noise (%d samples, %d Hz mono)", len(out), self.target_rate)
        return out

    def _synthetic_noise(self) -> np.ndarray[tuple[int], np.dtype[np.int16]]:
        n = self.target_rate * _FALLBACK_SECONDS
        return np.random.normal(0, _FALLBACK_NOISE_STD, n).astype("<i2")

    def _slice(self, needed: int) -> np.ndarray[tuple[int], np.dtype[np.int16]]:
        """Return ``needed`` int16 ambient samples, wrapping the buffer."""
        if len(self.buffer) == 0:
            return np.zeros(needed, dtype="<i2")
        out = np.empty(needed, dtype="<i2")
        written = 0
        while written < needed:
            remaining = needed - written
            chunk_len = min(remaining, len(self.buffer) - self._idx)
            out[written : written + chunk_len] = self.buffer[self._idx : self._idx + chunk_len]
            written += chunk_len
            self._idx = (self._idx + chunk_len) % len(self.buffer)
        return out

    def mix(self, voice_pcm: bytes, level: float = _DEFAULT_RATIO) -> bytes:
        """Mix ambient noise into ``voice_pcm`` at relative weight ``level``."""
        if not voice_pcm:
            return b""
        n_samples = len(voice_pcm) // 2
        ambient = self._slice(n_samples)
        return mix_pcm(voice_pcm, ambient.tobytes(), ambient_ratio=min(level, 1.0) * _DEFAULT_RATIO)

    @staticmethod
    def ulaw(pcm: bytes) -> bytes:
        """Encode PCM to µ-law (delegate to :mod:`ghostline.audio.codec`)."""
        return pcm_to_ulaw(pcm)

    @staticmethod
    def silence_chunk(size: int = TWILIO_CHUNK_SIZE) -> bytes:
        """Return a µ-law silence chunk of ``size`` bytes."""
        return b"\xff" * size
