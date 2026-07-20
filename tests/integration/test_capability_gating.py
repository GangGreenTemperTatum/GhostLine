"""Tests for capability-gated behavior in the call session.

Verifies that:
- Text models → Deepgram WSS is opened
- Realtime models → Deepgram WSS is skipped
- The modality is logged on session creation
- select_pipeline raises for realtime (stub) and returns TextVoicePipeline for text
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_mock

from ghostline.agent.capabilities import (
    ModelCapabilities,
    VoiceModality,
)
from ghostline.agent.reply_generator import ReplyGenerator
from ghostline.audio.ambient import AmbientNoise
from ghostline.persistence.repository import Repository
from ghostline.playbook import Playbook
from ghostline.server.fastapi import CallSession, ServerDeps
from ghostline.tts.elevenlabs import VoiceService
from ghostline.voice.pipeline import (
    PipelineConfig,
    PipelineNotSupportedError,
    TextVoicePipeline,
    select_pipeline,
)


def _start_event() -> str:
    """A Twilio Media Stream 'start' event JSON string."""
    return json.dumps(
        {
            "event": "start",
            "start": {
                "streamSid": "ZX_test",
                "callSid": "CA_test_caps",
            },
        }
    )


def _stop_event() -> str:
    """A Twilio Media Stream 'stop' event JSON string."""
    return json.dumps({"event": "stop"})


def _make_caps(modality: VoiceModality) -> ModelCapabilities:
    """Build a ModelCapabilities for the given modality."""
    if modality == VoiceModality.REALTIME:
        return ModelCapabilities(
            model="openai/gpt-4o-realtime-preview",
            modality=VoiceModality.REALTIME,
            native_stt=True,
            native_tts=True,
            mode="realtime",
            source="litellm",
        )
    if modality == VoiceModality.HYBRID_STT:
        return ModelCapabilities(
            model="hybrid/stt-only",
            modality=VoiceModality.HYBRID_STT,
            native_stt=True,
            native_tts=False,
            mode="chat",
            source="registry",
        )
    return ModelCapabilities(
        model="openai/gpt-4o-mini",
        modality=VoiceModality.TEXT,
        native_stt=False,
        native_tts=False,
        mode="chat",
        source="litellm",
    )


@pytest.fixture
def playbook() -> Playbook:
    return Playbook.from_string(
        """
meta: {name: Cap Test}
defaults: {persona: professional}
sequence:
  - stage: RAPPORT
    custom_prompt: "Hello from capability test."
  - stage: CLOSE
  - stage: REPORTING
"""
    )


@pytest.fixture
def ambient(tmp_path: object) -> AmbientNoise:
    return AmbientNoise(path=tmp_path / "nope.wav")  # type: ignore[arg-type]


@pytest.fixture
def voice_service(mocker: pytest_mock.MockerFixture) -> VoiceService:
    svc = mocker.MagicMock(spec=VoiceService)
    svc.synth = AsyncMock(return_value=b"\x00\x01" * 800)
    svc.aclose = AsyncMock()
    return svc  # type: ignore[return-value]


@pytest.fixture
def text_caps() -> ModelCapabilities:
    return _make_caps(VoiceModality.TEXT)


@pytest.fixture
def realtime_caps() -> ModelCapabilities:
    return _make_caps(VoiceModality.REALTIME)


class TestServerDepsCapabilities:
    def test_defaults_to_none_when_not_provided(
        self, tmp_repo: Repository, voice_service: VoiceService, ambient: AmbientNoise
    ) -> None:
        deps = ServerDeps(
            repository=tmp_repo,
            voice_service=voice_service,
            ambient=ambient,
            deepgram_api_key="key",
        )
        assert deps.capabilities is None


class TestCallSessionModalityLogging:
    """Verify the session logs the active voice modality."""

    async def test_text_modality_logged(
        self,
        tmp_repo: Repository,
        voice_service: VoiceService,
        ambient: AmbientNoise,
        playbook: Playbook,
        text_caps: ModelCapabilities,
        mocker: pytest_mock.MockerFixture,
    ) -> None:
        mock_deepgram_connect = mocker.patch(
            "ghostline.server.fastapi.DeepgramClient.connect",
            new=AsyncMock(),
        )
        mocker.patch(
            "ghostline.server.fastapi.DeepgramClient.close",
            new=AsyncMock(),
        )
        deps = ServerDeps(
            repository=tmp_repo,
            voice_service=voice_service,
            ambient=ambient,
            deepgram_api_key="key",
            playbook=playbook,
            voice_id="vid",
            capabilities=text_caps,
        )
        ws = MagicMock()
        ws.accept = AsyncMock()
        ws.receive_text = AsyncMock(side_effect=[_start_event(), _stop_event()])
        ws.close = AsyncMock()
        session = CallSession(deps, ws, "vid", "test", "professional")
        await session.run()
        # Deepgram should have been opened for text modality
        assert mock_deepgram_connect.called

    async def test_realtime_modality_skips_deepgram(
        self,
        tmp_repo: Repository,
        voice_service: VoiceService,
        ambient: AmbientNoise,
        playbook: Playbook,
        realtime_caps: ModelCapabilities,
        mocker: pytest_mock.MockerFixture,
    ) -> None:
        # This is the critical test: realtime models must NOT open Deepgram
        mock_deepgram_connect = mocker.patch(
            "ghostline.server.fastapi.DeepgramClient.connect",
            new=AsyncMock(),
        )
        mocker.patch(
            "ghostline.server.fastapi.DeepgramClient.close",
            new=AsyncMock(),
        )
        deps = ServerDeps(
            repository=tmp_repo,
            voice_service=voice_service,
            ambient=ambient,
            deepgram_api_key="key",
            playbook=playbook,
            voice_id="vid",
            capabilities=realtime_caps,
        )
        ws = MagicMock()
        ws.accept = AsyncMock()
        ws.receive_text = AsyncMock(side_effect=[_start_event(), _stop_event()])
        ws.close = AsyncMock()
        session = CallSession(deps, ws, "vid", "test", "professional")
        await session.run()
        # Deepgram.connect must NOT have been called
        assert not mock_deepgram_connect.called


class TestSelectPipeline:
    """Verify select_pipeline picks the right pipeline based on capabilities."""

    def test_text_model_returns_text_pipeline(
        self,
        voice_service: VoiceService,
        ambient: AmbientNoise,
        playbook: Playbook,
        text_caps: ModelCapabilities,
        mocker: pytest_mock.MockerFixture,
    ) -> None:
        reply_gen = mocker.MagicMock(spec=ReplyGenerator)
        config = PipelineConfig(
            capabilities=text_caps,
            reply_generator=reply_gen,
            voice_service=voice_service,
            ambient=ambient,
            playbook=playbook,
        )
        pipeline = select_pipeline(config)
        assert isinstance(pipeline, TextVoicePipeline)
        assert pipeline.needs_deepgram is True
        assert pipeline.needs_elevenlabs is True

    def test_realtime_model_raises_not_supported(
        self,
        voice_service: VoiceService,
        ambient: AmbientNoise,
        playbook: Playbook,
        realtime_caps: ModelCapabilities,
        mocker: pytest_mock.MockerFixture,
    ) -> None:
        reply_gen = mocker.MagicMock(spec=ReplyGenerator)
        config = PipelineConfig(
            capabilities=realtime_caps,
            reply_generator=reply_gen,
            voice_service=voice_service,
            ambient=ambient,
            playbook=playbook,
        )
        with pytest.raises(PipelineNotSupportedError, match="realtime"):
            select_pipeline(config)

    def test_text_pipeline_missing_reply_generator_raises(
        self,
        voice_service: VoiceService,
        ambient: AmbientNoise,
        playbook: Playbook,
        text_caps: ModelCapabilities,
    ) -> None:
        config = PipelineConfig(
            capabilities=text_caps,
            reply_generator=None,
            voice_service=voice_service,
            ambient=ambient,
            playbook=playbook,
        )
        with pytest.raises(PipelineNotSupportedError, match="ReplyGenerator"):
            select_pipeline(config)

    def test_text_pipeline_missing_voice_service_raises(
        self,
        ambient: AmbientNoise,
        playbook: Playbook,
        text_caps: ModelCapabilities,
        mocker: pytest_mock.MockerFixture,
    ) -> None:
        reply_gen = mocker.MagicMock(spec=ReplyGenerator)
        config = PipelineConfig(
            capabilities=text_caps,
            reply_generator=reply_gen,
            voice_service=None,
            ambient=ambient,
            playbook=playbook,
        )
        with pytest.raises(PipelineNotSupportedError, match="VoiceService"):
            select_pipeline(config)
