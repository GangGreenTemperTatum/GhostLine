"""Pure-numpy µ-law codec and PCM helpers.

The legacy code used ``audioop``, which is deprecated in Python 3.13 and
removed in 3.14. We implement the standard ITU-T G.711 µ-law algorithm in
numpy so it is vectorized, dependency-free, and forward-compatible.
"""

from __future__ import annotations

from typing import Final

import numpy as np
from numpy.typing import NDArray

__all__ = (
    "MU_LAW_MAX",
    "mix_pcm",
    "normalize_pcm",
    "pcm_to_ulaw",
    "ulaw_to_pcm",
)

# G.711 µ-law parameter (ITU-T standard).
_MU: Final[float] = 255.0
# Bias used in the reference decoder for symmetric zero.
_BIAS: Final[int] = 0x84
# Clip threshold for the encoder input.
_CLIP: Final[int] = 32635
# Sentinel "silent" µ-law byte (negative full-scale ≈ silence on PSTN).
SILENCE_BYTE: Final[int] = 0xFF

# Maximum µ-law byte value.
MU_LAW_MAX: Final[int] = 255

# Standard Twilio Media Streams chunk size for 8 kHz µ-law audio (20ms frames).
TWILIO_CHUNK_SIZE: Final[int] = 160

# 16-bit PCM range.
_PCM_INT16_MIN: Final[int] = -32768
_PCM_INT16_MAX: Final[int] = 32767


def pcm_to_ulaw(pcm: bytes) -> bytes:
    """Encode 16-bit little-endian PCM bytes into 8-bit µ-law bytes.

    Args:
        pcm: 16-bit signed LE PCM samples, ``len(pcm)`` must be even.

    Returns:
        ``len(pcm)//2`` µ-law bytes, ready for Twilio Media Stream frames.
    """
    if len(pcm) % 2 != 0:
        raise ValueError(f"PCM buffer length must be even, got {len(pcm)}")
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.int32)

    # Clip to encoder range
    clipped = np.clip(samples, -_CLIP, _CLIP)
    sign = (clipped < 0).astype(np.int32) * 0x80
    magnitude = np.abs(clipped + _BIAS)

    # Compute log-segment encoding (standard ITU-T table approximated).
    exponent = np.zeros_like(magnitude, dtype=np.int32)
    # Find highest set bit position relative to 0x100 boundary.
    # Iterate low→high so the largest matching threshold wins (overwrites
    # lower exponents).
    for bit in range(1, 8):
        threshold = 1 << (bit + 7)
        mask = magnitude >= threshold
        exponent[mask] = bit

    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    ulaw_byte = ~(sign | (exponent << 4) | mantissa) & 0xFF
    # Silence special case: encode exact 0 as 0xFF (silence).
    ulaw_byte = np.where(samples == 0, SILENCE_BYTE, ulaw_byte)
    return ulaw_byte.astype(np.uint8).tobytes()


def ulaw_to_pcm(ulaw: bytes) -> bytes:
    """Decode 8-bit µ-law bytes into 16-bit little-endian PCM bytes.

    Args:
        ulaw: µ-law samples.

    Returns:
        ``len(ulaw)*2`` PCM bytes.
    """
    if len(ulaw) == 0:
        return b""
    u = np.frombuffer(ulaw, dtype=np.uint8).astype(np.int32)
    # Bitwise invert (per G.711 spec).
    u = ~u & 0xFF

    sign = u & 0x80
    exponent = (u >> 4) & 0x07
    mantissa = u & 0x0F

    magnitude = ((mantissa << 3) + _BIAS) << exponent
    magnitude -= _BIAS
    sample = np.where(sign != 0, -magnitude, magnitude)
    sample = np.clip(sample, _PCM_INT16_MIN, _PCM_INT16_MAX).astype("<i2")
    return sample.tobytes()


def normalize_pcm(pcm: bytes, target_peak: int = 30000) -> bytes:
    """Peak-normalize 16-bit PCM to ``target_peak`` (32767 = max).

    Args:
        pcm: 16-bit signed LE PCM samples.
        target_peak: Desired absolute peak amplitude (1..32767).

    Returns:
        Normalized PCM bytes with the same length.
    """
    if not 1 <= target_peak <= _PCM_INT16_MAX:
        raise ValueError(f"target_peak must be in [1, {_PCM_INT16_MAX}]; got {target_peak}")
    if len(pcm) == 0:
        return b""
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    peak = float(np.max(np.abs(samples)))
    if peak < 1.0:
        return pcm  # near-silent: leave untouched
    gain = target_peak / peak
    scaled = np.clip(samples * gain, _PCM_INT16_MIN, _PCM_INT16_MAX).astype("<i2")
    out_bytes: bytes = scaled.tobytes()
    return out_bytes


def mix_pcm(voice_pcm: bytes, ambient_pcm: bytes, ambient_ratio: float = 0.1) -> bytes:
    """Mix two 16-bit PCM byte streams of equal length.

    Args:
        voice_pcm: Primary (voice) PCM samples.
        ambient_pcm: Secondary (ambient) PCM samples; must be same length.
        ambient_ratio: Mix weight for the ambient stream in ``[0, 1]``.

    Returns:
        Mixed PCM bytes, clipped to int16 range.
    """
    if len(voice_pcm) != len(ambient_pcm):
        raise ValueError(
            f"PCM buffers must be same length; got {len(voice_pcm)} vs {len(ambient_pcm)}"
        )
    if not 0.0 <= ambient_ratio <= 1.0:
        raise ValueError(f"ambient_ratio must be in [0, 1]; got {ambient_ratio}")
    if len(voice_pcm) == 0:
        return b""

    voice = np.frombuffer(voice_pcm, dtype="<i2").astype(np.int32)
    ambient = np.frombuffer(ambient_pcm, dtype="<i2").astype(np.int32)
    mixed = np.clip(voice + (ambient * ambient_ratio).astype(np.int32), -32768, 32767)
    return mixed.astype("<i2").tobytes()


def _silence_pcm(num_samples: int) -> NDArray[np.int16]:
    """Return a zeroed PCM array of ``num_samples`` int16 samples."""
    return np.zeros(num_samples, dtype="<i2")
