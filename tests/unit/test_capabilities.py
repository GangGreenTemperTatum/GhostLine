"""Tests for the model capability detection system."""

from __future__ import annotations

import pytest

from ghostline.agent.capabilities import (
    DEFAULT_CAPABILITIES,
    CapabilityRegistry,
    ModelCapabilities,
    VoiceModality,
    detect_capabilities,
    register_capabilities,
)


class TestVoiceModality:
    def test_text_is_default(self) -> None:
        assert VoiceModality.TEXT.value == "text"

    def test_all_modalities_distinct(self) -> None:
        values = {m.value for m in VoiceModality}
        assert values == {"text", "realtime", "hybrid_stt", "hybrid_tts"}


class TestModelCapabilities:
    def test_needs_deepgram_true_for_text_model(self) -> None:
        caps = ModelCapabilities(
            model="openai/gpt-4o-mini",
            modality=VoiceModality.TEXT,
            native_stt=False,
            native_tts=False,
            mode="chat",
            source="litellm",
        )
        assert caps.needs_deepgram is True
        assert caps.needs_elevenlabs is True
        assert caps.needs_text_pipeline is True

    def test_needs_deepgram_false_for_realtime_model(self) -> None:
        caps = ModelCapabilities(
            model="openai/gpt-4o-realtime-preview",
            modality=VoiceModality.REALTIME,
            native_stt=True,
            native_tts=True,
            mode="realtime",
            source="litellm",
        )
        assert caps.needs_deepgram is False
        assert caps.needs_elevenlabs is False
        assert caps.needs_text_pipeline is False

    def test_hybrid_stt_needs_elevenlabs_but_not_deepgram(self) -> None:
        caps = ModelCapabilities(
            model="hypothetical/stt-only-model",
            modality=VoiceModality.HYBRID_STT,
            native_stt=True,
            native_tts=False,
            mode="chat",
            source="registry",
        )
        assert caps.needs_deepgram is False
        assert caps.needs_elevenlabs is True

    def test_hybrid_tts_needs_deepgram_but_not_elevenlabs(self) -> None:
        caps = ModelCapabilities(
            model="hypothetical/tts-only-model",
            modality=VoiceModality.HYBRID_TTS,
            native_stt=False,
            native_tts=True,
            mode="chat",
            source="registry",
        )
        assert caps.needs_deepgram is True
        assert caps.needs_elevenlabs is False

    def test_frozen_dataclass(self) -> None:
        caps = ModelCapabilities(
            model="x",
            modality=VoiceModality.TEXT,
            native_stt=False,
            native_tts=False,
            mode="chat",
            source="test",
        )
        with pytest.raises(AttributeError):
            caps.model = "y"  # type: ignore[misc]


class TestCapabilityRegistry:
    def test_register_and_detect_override(self) -> None:
        reg = CapabilityRegistry()
        reg.register("ollama/llama3.1", modality=VoiceModality.TEXT)
        caps = reg.detect("ollama/llama3.1")
        assert caps.modality == VoiceModality.TEXT
        assert caps.source == "registry"
        assert caps.model == "ollama/llama3.1"

    def test_register_realtime_model(self) -> None:
        reg = CapabilityRegistry()
        reg.register("custom/realtime-v1", modality=VoiceModality.REALTIME)
        caps = reg.detect("custom/realtime-v1")
        assert caps.modality == VoiceModality.REALTIME
        assert caps.native_stt is True
        assert caps.native_tts is True
        assert caps.needs_deepgram is False
        assert caps.needs_elevenlabs is False

    def test_register_hybrid_infers_stt_tts(self) -> None:
        reg = CapabilityRegistry()
        reg.register("hybrid/stt-only", modality=VoiceModality.HYBRID_STT)
        caps = reg.detect("hybrid/stt-only")
        assert caps.native_stt is True
        assert caps.native_tts is False

    def test_register_with_explicit_stt_tts(self) -> None:
        reg = CapabilityRegistry()
        reg.register(
            "weird/model",
            modality=VoiceModality.TEXT,
            native_stt=True,
            native_tts=True,
        )
        caps = reg.detect("weird/model")
        assert caps.native_stt is True
        assert caps.native_tts is True
        assert caps.modality == VoiceModality.TEXT

    def test_detect_via_litellm_for_known_text_model(self) -> None:
        reg = CapabilityRegistry()
        caps = reg.detect("openai/gpt-4o-mini")
        assert caps.modality == VoiceModality.TEXT
        assert caps.source == "litellm"
        assert caps.native_stt is False
        assert caps.needs_deepgram is True

    def test_detect_via_litellm_for_known_realtime_model(self) -> None:
        reg = CapabilityRegistry()
        caps = reg.detect("openai/gpt-4o-realtime-preview")
        assert caps.modality == VoiceModality.REALTIME
        assert caps.source == "litellm"
        assert caps.native_stt is True
        assert caps.native_tts is True
        assert caps.needs_deepgram is False

    def test_detect_unknown_model_falls_back_to_default(self) -> None:
        reg = CapabilityRegistry()
        caps = reg.detect("unknownprovider/some-future-model")
        assert caps.modality == VoiceModality.TEXT
        assert caps.source == "default"
        assert caps.native_stt is False
        assert caps.native_tts is False

    def test_override_takes_precedence_over_litellm(self) -> None:
        reg = CapabilityRegistry()
        # Override gpt-4o-mini to claim it's realtime (artificial, tests precedence)
        reg.register("openai/gpt-4o-mini", modality=VoiceModality.REALTIME)
        caps = reg.detect("openai/gpt-4o-mini")
        assert caps.source == "registry"
        assert caps.modality == VoiceModality.REALTIME

    def test_strip_provider_prefix(self) -> None:
        assert CapabilityRegistry._strip_provider_prefix("openai/gpt-4o-mini") == "gpt-4o-mini"
        assert CapabilityRegistry._strip_provider_prefix("anthropic/claude-3") == "claude-3"
        assert CapabilityRegistry._strip_provider_prefix("gpt-4o-mini") == "gpt-4o-mini"


class TestDetectCapabilitiesModuleFunction:
    def test_detect_text_model(self) -> None:
        caps = detect_capabilities("openai/gpt-4o-mini")
        assert caps.modality == VoiceModality.TEXT
        assert caps.needs_deepgram is True

    def test_detect_realtime_model(self) -> None:
        caps = detect_capabilities("openai/gpt-4o-realtime-preview")
        assert caps.modality == VoiceModality.REALTIME
        assert caps.needs_deepgram is False

    def test_register_capabilities_module_function(self) -> None:
        register_capabilities("test/custom-model", modality=VoiceModality.HYBRID_TTS)
        caps = detect_capabilities("test/custom-model")
        assert caps.modality == VoiceModality.HYBRID_TTS
        assert caps.source == "registry"


class TestDefaultCapabilities:
    def test_default_is_text_only(self) -> None:
        assert DEFAULT_CAPABILITIES.modality == VoiceModality.TEXT
        assert DEFAULT_CAPABILITIES.native_stt is False
        assert DEFAULT_CAPABILITIES.native_tts is False
        assert DEFAULT_CAPABILITIES.needs_deepgram is True
        assert DEFAULT_CAPABILITIES.needs_elevenlabs is True
