"""Voice pipeline abstraction: decouples the call handler from STT/TTS wiring.

The call handler (:class:`ghostline.server.fastapi.CallSession`) doesn't
need to know whether Deepgram + ElevenLabs are in the loop or whether a
single realtime WSS handles everything. It talks to a
:class:`VoicePipeline` that abstracts the differences.

Two implementations:
- :class:`TextVoicePipeline`: Deepgram STT → text LLM → ElevenLabs TTS
  (the current, working pipeline)
- :class:`RealtimeVoicePipeline`: bridges Twilio µ-law audio directly to
  a realtime model WSS (e.g. OpenAI ``gpt-realtime-2.1``). This is a
  stub — the actual audio bridging is a future feature, but the interface
  is in place so the call handler can be agnostic.

The composition root (:func:`ghostline.app.SalesAutomationApp.serve`)
calls :func:`select_pipeline` with the model's capabilities and gets
back the right pipeline.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from ghostline.agent.capabilities import ModelCapabilities, VoiceModality
from ghostline.agent.reply_generator import ReplyGenerator
from ghostline.audio.ambient import AmbientNoise
from ghostline.tts.elevenlabs import VoiceService

if TYPE_CHECKING:
    from ghostline.playbook import Playbook

__all__ = (
    "PipelineConfig",
    "PipelineNotSupportedError",
    "RealtimeVoicePipeline",
    "TextVoicePipeline",
    "VoicePipeline",
    "select_pipeline",
)

_LOGGER = logging.getLogger(__name__)


class PipelineNotSupportedError(RuntimeError):
    """Raised when the requested voice pipeline is not yet implemented."""


class VoicePipeline(Protocol):
    """Abstracts the STT → LLM → TTS flow for a single call.

    The call handler calls these methods; the implementation handles the
    details (Deepgram WSS, ElevenLabs HTTP, realtime WSS bridging, etc.).

    Why a Protocol (not an ABC): the text pipeline is a concrete class
    that already works; the realtime pipeline is a stub. A Protocol lets
    duck-typing do the right thing without forcing inheritance.
    """

    @property
    def needs_deepgram(self) -> bool:
        """Whether this pipeline requires a Deepgram WSS connection."""
        ...

    @property
    def needs_elevenlabs(self) -> bool:
        """Whether this pipeline requires the ElevenLabs TTS service."""
        ...

    async def speak_text(self, text: str, voice_id: str, persona: str = "professional") -> bytes:
        """Synthesize ``text`` to PCM audio bytes.

        For the text pipeline: calls ElevenLabs TTS.
        For the realtime pipeline: would send text to the realtime model
        and capture the audio response (not yet implemented).

        Args:
            text: The text to speak.
            voice_id: ElevenLabs voice ID (text pipeline only).
            persona: Persona name for prosody (text pipeline only).

        Returns:
            PCM audio bytes (8 kHz mono 16-bit), ready for µ-law encoding.
        """
        ...

    async def process_utterance(
        self,
        utterance: str,
        history: str | None,
        *,
        current_stage: object,
        context: object | None = None,
    ) -> str:
        """Generate a reply for ``utterance``.

        For the text pipeline: runs the analysis + stage agents via
        :class:`ReplyGenerator`.
        For the realtime pipeline: the realtime model generates replies
        automatically; this method would not be called (the pipeline
        handles turn-taking natively). Implementations may raise
        :class:`PipelineNotSupportedError` if the realtime path doesn't
        support discrete utterance processing.

        Args:
            utterance: The transcribed user utterance.
            history: Optional conversation history string.
            current_stage: The current :class:`SalesStage`.
            context: Optional per-call context for dynamic instructions.

        Returns:
            The reply text (for DB logging).
        """
        ...


@dataclass(slots=True)
class PipelineConfig:
    """Configuration for the active voice pipeline.

    Built by the composition root from :class:`ModelCapabilities` +
    available services. Passed to :func:`select_pipeline`.
    """

    capabilities: ModelCapabilities
    reply_generator: ReplyGenerator | None
    voice_service: VoiceService | None
    ambient: AmbientNoise
    playbook: Playbook | None

    @property
    def modality(self) -> VoiceModality:
        """The voice modality declared by the model."""
        return self.capabilities.modality


class TextVoicePipeline:
    """Deepgram STT → text LLM → ElevenLabs TTS (the current pipeline).

    This is a thin wrapper that delegates to :class:`ReplyGenerator` for
    LLM calls and :class:`VoiceService` for TTS. The Deepgram WSS
    management stays in :class:`CallSession` (it's tightly coupled to
    the Twilio Media Stream WS protocol).
    """

    def __init__(
        self,
        reply_generator: ReplyGenerator,
        voice_service: VoiceService,
        ambient: AmbientNoise,
    ) -> None:
        self._reply_generator = reply_generator
        self._voice_service = voice_service
        self._ambient = ambient

    @property
    def needs_deepgram(self) -> bool:
        """Text pipeline always needs Deepgram for STT."""
        return True

    @property
    def needs_elevenlabs(self) -> bool:
        """Text pipeline always needs ElevenLabs for TTS."""
        return True

    async def speak_text(self, text: str, voice_id: str, persona: str = "professional") -> bytes:
        """Synthesize ``text`` via ElevenLabs and return PCM bytes."""
        return await self._voice_service.synth(text, voice_id, persona)

    async def process_utterance(
        self,
        utterance: str,
        history: str | None,
        *,
        current_stage: object,
        context: object | None = None,
    ) -> str:
        """Generate a reply via the ReplyGenerator."""
        result = await self._reply_generator.generate(
            utterance=utterance,
            current_stage=current_stage,  # type: ignore[arg-type]
            context=context,  # type: ignore[arg-type]
            history=history,
        )
        return result.reply


class RealtimeVoicePipeline:
    """Bridges Twilio µ-law audio directly to a realtime model WSS.

    When the LLM model supports native voice I/O (e.g. OpenAI
    ``gpt-realtime-2.1``), Deepgram and ElevenLabs are both skipped.
    Audio flows: Twilio Media Stream WSS → µ-law↔PCM16 conversion →
    Realtime model WSS → PCM16↔µ-law → Twilio.

    This is a **stub**. The interface is in place so the composition root
    can select it, but the actual audio bridging raises
    :class:`PipelineNotSupportedError` until implemented.

    To implement, you need:
    1. Open a Realtime WSS to the model (OpenAI Agents SDK's
       ``RealtimeSession`` / ``RealtimeAgent`` can handle this)
    2. Convert inbound Twilio µ-law → PCM16 @ 24kHz (Realtime API format)
    3. Convert outbound PCM16 → µ-law @ 8kHz (Twilio format)
    4. Handle interruption events from the Realtime model
    5. Map playbook stages to Realtime session instructions
    """

    def __init__(self, model: str, ambient: AmbientNoise) -> None:
        self._model = model
        self._ambient = ambient

    @property
    def needs_deepgram(self) -> bool:
        """Realtime model handles STT natively — Deepgram not needed."""
        return False

    @property
    def needs_elevenlabs(self) -> bool:
        """Realtime model handles TTS natively — ElevenLabs not needed."""
        return False

    async def speak_text(self, text: str, voice_id: str, persona: str = "professional") -> bytes:
        """Send text to the realtime model and capture audio response.

        Not yet implemented — the realtime model handles TTS natively,
        but extracting discrete audio chunks for specific text requires
        session-level conversation management.
        """
        raise PipelineNotSupportedError(
            f"RealtimeVoicePipeline.speak_text() not yet implemented for model "
            f"{self._model!r}. The realtime model handles TTS natively; this "
            f"method is only needed for out-of-band text (greetings, check-ins)."
        )

    async def process_utterance(
        self,
        utterance: str,
        history: str | None,
        *,
        current_stage: object,
        context: object | None = None,
    ) -> str:
        """Not used in realtime mode — the model handles turn-taking natively."""
        raise PipelineNotSupportedError(
            f"RealtimeVoicePipeline.process_utterance() not applicable for model "
            f"{self._model!r}. Realtime models handle STT+LLM+TTS in a single "
            f"WSS; discrete utterance processing is a text-pipeline concept."
        )


def select_pipeline(config: PipelineConfig) -> VoicePipeline:
    """Select the right voice pipeline based on model capabilities.

    Args:
        config: :class:`PipelineConfig` with capabilities + services.

    Returns:
        A :class:`VoicePipeline` instance ready for use.

    Raises:
        :class:`PipelineNotSupportedError`: If the model declares realtime
            capabilities but the realtime pipeline isn't implemented yet.
    """
    caps = config.capabilities
    _LOGGER.info(
        "Selecting voice pipeline for model=%s (modality=%s, native_stt=%s, "
        "native_tts=%s, source=%s)",
        caps.model,
        caps.modality.value,
        caps.native_stt,
        caps.native_tts,
        caps.source,
    )

    if caps.modality == VoiceModality.REALTIME:
        # The realtime pipeline is a stub — raise so the operator knows
        # the model was detected as realtime-capable but the bridge isn't
        # built yet. They can either implement RealtimeVoicePipeline or
        # switch to a text model.
        _LOGGER.warning(
            "Model %s declares REALTIME modality. The realtime voice pipeline "
            "is not yet implemented. Either: (1) implement RealtimeVoicePipeline "
            "to bridge Twilio µ-law ↔ realtime WSS, or (2) set LITELLM_MODEL to "
            "a text model like 'openai/gpt-4o-mini' to use the text pipeline.",
            caps.model,
        )
        raise PipelineNotSupportedError(
            f"Model {caps.model!r} requires the realtime voice pipeline, which "
            f"is not yet implemented. Use a text model (e.g. 'openai/gpt-4o-mini') "
            f"or implement RealtimeVoicePipeline."
        )

    # Text pipeline (the current, working path)
    if config.reply_generator is None or config.voice_service is None:
        raise PipelineNotSupportedError(
            "Text voice pipeline requires both a ReplyGenerator and a VoiceService. "
            "Ensure the composition root constructed them."
        )
    return TextVoicePipeline(
        reply_generator=config.reply_generator,
        voice_service=config.voice_service,
        ambient=config.ambient,
    )
