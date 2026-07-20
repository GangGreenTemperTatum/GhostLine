"""Tests for the µ-law codec and ambient mixer."""

from __future__ import annotations

import numpy as np
import pytest

from ghostline.audio.ambient import AmbientNoise
from ghostline.audio.codec import (
    SILENCE_BYTE,
    TWILIO_CHUNK_SIZE,
    mix_pcm,
    normalize_pcm,
    pcm_to_ulaw,
    ulaw_to_pcm,
)


def make_pcm(n: int = 160, freq: float = 440.0, amp: int = 10000) -> bytes:
    """Synthesize a sine wave as 16-bit LE PCM bytes."""
    t = np.arange(n) / 8000.0
    wave_samples = (amp * np.sin(2 * np.pi * freq * t)).astype("<i2")
    return wave_samples.tobytes()


class TestPcmToUlaw:
    def test_output_length_is_half_input(self) -> None:
        pcm = make_pcm(n=320)  # 640 bytes = 320 samples
        out = pcm_to_ulaw(pcm)
        assert len(out) == len(pcm) // 2  # 1 byte per 2-byte sample
        assert len(out) == 320

    def test_empty_input(self) -> None:
        assert pcm_to_ulaw(b"") == b""

    def test_odd_length_raises(self) -> None:
        with pytest.raises(ValueError, match="even"):
            pcm_to_ulaw(b"\x00\x01\x02")

    def test_silence_encodes_as_0xff(self) -> None:
        pcm = np.zeros(100, dtype="<i2").tobytes()
        out = pcm_to_ulaw(pcm)
        assert all(b == SILENCE_BYTE for b in out)

    def test_max_amplitude_does_not_crash(self) -> None:
        pcm = np.full(100, 32767, dtype="<i2").tobytes()
        out = pcm_to_ulaw(pcm)
        assert len(out) == 100
        # Should not be silence (it's a loud sample)
        assert any(b != SILENCE_BYTE for b in out)

    def test_negative_amplitude_handled(self) -> None:
        pcm = np.full(100, -32768, dtype="<i2").tobytes()
        out = pcm_to_ulaw(pcm)
        assert len(out) == 100


class TestUlawToPcm:
    def test_round_trip_preserves_silence(self) -> None:
        silence_ulaw = bytes([SILENCE_BYTE] * 100)
        pcm = ulaw_to_pcm(silence_ulaw)
        samples = np.frombuffer(pcm, dtype="<i2")
        # Silence µ-law decodes to ~0 (within a couple of quantization steps)
        assert np.all(np.abs(samples) < 5)

    def test_round_trip_signal(self) -> None:
        """µ-law is lossy but a strong signal should survive qualitatively."""
        original = make_pcm(n=1000, amp=30000)
        encoded = pcm_to_ulaw(original)
        decoded = ulaw_to_pcm(encoded)
        decoded_samples = np.frombuffer(decoded, dtype="<i2")
        # Energy in the same order of magnitude
        assert np.max(np.abs(decoded_samples)) > 15000

    def test_empty_input(self) -> None:
        assert ulaw_to_pcm(b"") == b""


class TestNormalizePcm:
    def test_normalizes_to_target_peak(self) -> None:
        pcm = make_pcm(n=200, amp=1000)
        out = normalize_pcm(pcm, target_peak=30000)
        peak = int(np.max(np.abs(np.frombuffer(out, dtype="<i2"))))
        assert 29000 <= peak <= 30000

    def test_silence_untouched(self) -> None:
        pcm = np.zeros(100, dtype="<i2").tobytes()
        assert normalize_pcm(pcm, target_peak=30000) == pcm

    def test_invalid_target_raises(self) -> None:
        with pytest.raises(ValueError, match="target_peak"):
            normalize_pcm(make_pcm(10), target_peak=0)

    def test_empty(self) -> None:
        assert normalize_pcm(b"", target_peak=30000) == b""


class TestMixPcm:
    def test_equal_length_mix(self) -> None:
        voice = np.full(100, 1000, dtype="<i2").tobytes()
        ambient = np.full(100, 200, dtype="<i2").tobytes()
        out = mix_pcm(voice, ambient, ambient_ratio=0.5)
        samples = np.frombuffer(out, dtype="<i2")
        assert np.allclose(samples, 1100, atol=1)

    def test_unequal_length_raises(self) -> None:
        with pytest.raises(ValueError, match="same length"):
            mix_pcm(b"\x00" * 10, b"\x00" * 12)

    def test_invalid_ratio_raises(self) -> None:
        with pytest.raises(ValueError, match="ambient_ratio"):
            mix_pcm(b"\x00" * 4, b"\x00" * 4, ambient_ratio=1.5)

    def test_clipping_on_overflow(self) -> None:
        voice = np.full(100, 30000, dtype="<i2").tobytes()
        ambient = np.full(100, 30000, dtype="<i2").tobytes()
        out = mix_pcm(voice, ambient, ambient_ratio=1.0)
        samples = np.frombuffer(out, dtype="<i2")
        # Should clip at int16 max
        assert np.max(samples) == 32767

    def test_empty_returns_empty(self) -> None:
        assert mix_pcm(b"", b"", ambient_ratio=0.5) == b""


class TestAmbientNoise:
    def test_synthetic_fallback_when_file_missing(self, tmp_path: object) -> None:
        an = AmbientNoise(path=tmp_path / "does_not_exist.wav")  # type: ignore[arg-type]
        assert len(an.buffer) > 0
        # Mixing against synthetic noise should not error
        out = an.mix(make_pcm(160), level=0.2)
        assert len(out) == 320  # 160 samples * 2 bytes

    def test_mix_zero_level_returns_unchanged_signal(self, tmp_path: object) -> None:
        an = AmbientNoise(path=tmp_path / "nope.wav")  # type: ignore[arg-type]
        voice = make_pcm(160, amp=5000)
        out = an.mix(voice, level=0.0)
        # Level 0 → effective ratio 0; ambient contribution = 0
        voice_samples = np.frombuffer(voice, dtype="<i2")
        out_samples = np.frombuffer(out, dtype="<i2")
        assert np.allclose(out_samples, voice_samples, atol=2)

    def test_ulaw_delegates_to_codec(self) -> None:
        pcm = make_pcm(160)
        assert AmbientNoise.ulaw(pcm) == pcm_to_ulaw(pcm)

    def test_silence_chunk(self) -> None:
        chunk = AmbientNoise.silence_chunk()
        assert len(chunk) == TWILIO_CHUNK_SIZE
        assert all(b == SILENCE_BYTE for b in chunk)

    def test_wraps_around_buffer(self, tmp_path: object) -> None:
        """Mixing more samples than the buffer holds should wrap without error."""
        an = AmbientNoise(path=tmp_path / "nope.wav")  # type: ignore[arg-type]
        # Synthetic fallback is 10 seconds @ 8 kHz = 80000 samples.
        # Request 160000 samples = 320000 PCM bytes.
        out = an.mix(make_pcm(160000), level=0.3)
        assert len(out) == 320000
