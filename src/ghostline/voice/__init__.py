"""Voice pipeline abstraction: decouples the call handler from STT/TTS wiring."""

from __future__ import annotations

from ghostline.voice.pipeline import (
    PipelineConfig,
    PipelineNotSupportedError,
    RealtimeVoicePipeline,
    TextVoicePipeline,
    VoicePipeline,
    select_pipeline,
)

__all__ = (
    "PipelineConfig",
    "PipelineNotSupportedError",
    "RealtimeVoicePipeline",
    "TextVoicePipeline",
    "VoicePipeline",
    "select_pipeline",
)
