"""Model capability detection — the scalable alternative to string-prefix matching.

Instead of ``if model.startswith("openai/realtime")``, we query LiteLLM's
model metadata registry (which tracks ``mode``, ``supports_audio_input``,
``supports_audio_output`` for every model it knows about) and fall back to
a conservative default for unknown models.

The composition root calls :func:`detect_capabilities` once at startup,
passes the result to :class:`ServerDeps`, and the call handler branches
on the declared modality — not on the model name.

Adding support for a new realtime model is a registry entry, not a code
change scattered across the codebase.

Example::

    caps = detect_capabilities("openai/gpt-4o-realtime-preview")
    if caps.native_voice:
        # Use realtime pipeline (skip Deepgram + ElevenLabs)
    else:
        # Use text pipeline (Deepgram STT → LLM → ElevenLabs TTS)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Final

__all__ = (
    "DEFAULT_CAPABILITIES",
    "CapabilityRegistry",
    "ModelCapabilities",
    "VoiceModality",
    "detect_capabilities",
)

_LOGGER = logging.getLogger(__name__)


class VoiceModality(Enum):
    """How the LLM processes voice.

    - ``TEXT``: Model only handles text. Deepgram STT + ElevenLabs TTS required.
    - ``REALTIME``: Model handles STT+LLM+TTS natively via a single WSS.
      Deepgram and ElevenLabs are skipped; audio bridges Twilio ↔ model WSS.
    - ``HYBRID_STT``: Model can accept audio input but not produce audio
      output. Deepgram is skipped; ElevenLabs TTS is still needed.
      (Reserved — no model currently fits this pattern, but the architecture
      accommodates it.)
    - ``HYBRID_TTS``: Model can produce audio output but not accept audio
      input. ElevenLabs is skipped; Deepgram STT is still needed.
      (Reserved — same as above.)
    """

    TEXT = "text"
    REALTIME = "realtime"
    HYBRID_STT = "hybrid_stt"
    HYBRID_TTS = "hybrid_tts"


@dataclass(slots=True, frozen=True)
class ModelCapabilities:
    """Declares what a specific LLM model can do.

    Attributes:
        model: The LiteLLM model string this describes (e.g. ``openai/gpt-4o-mini``).
        modality: The :class:`VoiceModality` this model requires.
        native_stt: Whether the model can accept audio input directly.
        native_tts: Whether the model can produce audio output directly.
        mode: LiteLLM's ``mode`` field (``"chat"``, ``"realtime"``, etc.).
        source: How the capabilities were determined (``"litellm"``,
            ``"registry"``, ``"default"``, ``"env_override"``).
    """

    model: str
    modality: VoiceModality
    native_stt: bool
    native_tts: bool
    mode: str
    source: str

    @property
    def needs_deepgram(self) -> bool:
        """Whether the Deepgram STT service is required for this model."""
        return not self.native_stt

    @property
    def needs_elevenlabs(self) -> bool:
        """Whether the ElevenLabs TTS service is required for this model."""
        return not self.native_tts

    @property
    def needs_text_pipeline(self) -> bool:
        """Whether the text-based voice pipeline (STT→LLM→TTS) is needed."""
        return self.modality == VoiceModality.TEXT


# Conservative fallback for models not in LiteLLM's registry.
DEFAULT_CAPABILITIES: Final[ModelCapabilities] = ModelCapabilities(
    model="<unknown>",
    modality=VoiceModality.TEXT,
    native_stt=False,
    native_tts=False,
    mode="chat",
    source="default",
)


class CapabilityRegistry:
    """Extensible registry of model capabilities.

    Wraps LiteLLM's ``get_model_info()`` and allows operators to register
    custom entries for models LiteLLM doesn't know about (e.g. local
    Ollama models, new providers).

    Usage::

        registry = CapabilityRegistry()
        registry.register("ollama/llama3.1", modality=VoiceModality.TEXT)
        caps = registry.detect("openai/gpt-4o-realtime-preview")
    """

    def __init__(self) -> None:
        self._overrides: dict[str, ModelCapabilities] = {}

    def register(
        self,
        model: str,
        *,
        modality: VoiceModality,
        native_stt: bool | None = None,
        native_tts: bool | None = None,
        mode: str = "chat",
    ) -> None:
        """Register or override capabilities for a model string.

        Args:
            model: The LiteLLM model string (e.g. ``"ollama/llama3.1"``).
            modality: The voice modality this model supports.
            native_stt: Whether the model accepts audio input. If None,
                inferred from ``modality``.
            native_tts: Whether the model produces audio output. If None,
                inferred from ``modality``.
            mode: LiteLLM-style mode string (``"chat"``, ``"realtime"``, ...).
        """
        if native_stt is None:
            native_stt = modality in (VoiceModality.REALTIME, VoiceModality.HYBRID_STT)
        if native_tts is None:
            native_tts = modality in (VoiceModality.REALTIME, VoiceModality.HYBRID_TTS)
        self._overrides[model] = ModelCapabilities(
            model=model,
            modality=modality,
            native_stt=native_stt,
            native_tts=native_tts,
            mode=mode,
            source="registry",
        )

    def detect(self, model: str) -> ModelCapabilities:
        """Detect capabilities for ``model``.

        Resolution order:
        1. Explicit registry overrides (via :meth:`register`)
        2. LiteLLM's ``get_model_info()`` metadata
        3. :data:`DEFAULT_CAPABILITIES` (text-only, conservative)

        Args:
            model: LiteLLM model string, e.g. ``"openai/gpt-4o-mini"``.

        Returns:
            A :class:`ModelCapabilities` describing the model.
        """
        # 1. Check explicit overrides first
        if model in self._overrides:
            return self._overrides[model]

        # 2. Query LiteLLM's model metadata
        caps = self._query_litellm(model)
        if caps is not None:
            return caps

        # 3. Fall back to conservative default
        _LOGGER.warning(
            "Model %r not found in LiteLLM registry or capability overrides; "
            "defaulting to text-only modality. Register it via "
            "CapabilityRegistry.register() if it supports voice.",
            model,
        )
        return ModelCapabilities(
            model=model,
            modality=DEFAULT_CAPABILITIES.modality,
            native_stt=DEFAULT_CAPABILITIES.native_stt,
            native_tts=DEFAULT_CAPABILITIES.native_tts,
            mode=DEFAULT_CAPABILITIES.mode,
            source="default",
        )

    @staticmethod
    def _strip_provider_prefix(model: str) -> str:
        """Strip the ``provider/`` prefix for LiteLLM's registry lookup.

        LiteLLM's ``get_model_info()`` expects bare model names (e.g.
        ``"gpt-4o-mini"`` not ``"openai/gpt-4o-mini"``).
        """
        if "/" in model:
            return model.split("/", 1)[1]
        return model

    def _query_litellm(self, model: str) -> ModelCapabilities | None:
        """Query LiteLLM's model metadata and translate to capabilities."""
        try:
            import litellm
        except ImportError:
            _LOGGER.debug("litellm not installed; skipping metadata query")
            return None

        bare_model = self._strip_provider_prefix(model)
        try:
            info = litellm.get_model_info(bare_model)
        except Exception:
            _LOGGER.debug("LiteLLM has no metadata for %r", bare_model)
            return None
        if not isinstance(info, dict):
            return None  # type: ignore[unreachable]  # litellm can return None at runtime

        mode = str(info.get("mode", "chat"))
        audio_in = bool(info.get("supports_audio_input", False))
        audio_out = bool(info.get("supports_audio_output", False))

        modality = self._infer_modality(mode, audio_in, audio_out)
        return ModelCapabilities(
            model=model,
            modality=modality,
            native_stt=audio_in,
            native_tts=audio_out,
            mode=mode,
            source="litellm",
        )

    @staticmethod
    def _infer_modality(mode: str, audio_in: bool, audio_out: bool) -> VoiceModality:
        """Infer the :class:`VoiceModality` from LiteLLM metadata."""
        if mode == "realtime" and audio_in and audio_out:
            return VoiceModality.REALTIME
        if audio_in and not audio_out:
            return VoiceModality.HYBRID_STT
        if audio_out and not audio_in:
            return VoiceModality.HYBRID_TTS
        return VoiceModality.TEXT


# Module-level singleton for convenience (the composition root uses this).
_REGISTRY: CapabilityRegistry | None = None


def _get_registry() -> CapabilityRegistry:
    """Return the module-level registry singleton."""
    global _REGISTRY  # noqa: PLW0603 - singleton, set once
    if _REGISTRY is None:
        _REGISTRY = CapabilityRegistry()
    return _REGISTRY


def detect_capabilities(model: str) -> ModelCapabilities:
    """Detect capabilities for ``model`` using the global registry.

    This is the main entrypoint. The composition root calls this once at
    startup and passes the result to :class:`ServerDeps`.

    Args:
        model: LiteLLM model string, e.g. ``"openai/gpt-4o-mini"``.

    Returns:
        A :class:`ModelCapabilities` describing the model's voice I/O
        capabilities.
    """
    return _get_registry().detect(model)


def register_capabilities(
    model: str,
    *,
    modality: VoiceModality,
    native_stt: bool | None = None,
    native_tts: bool | None = None,
    mode: str = "chat",
) -> None:
    """Register custom capabilities for a model (module-level convenience).

    Useful for models LiteLLM doesn't know about::

        register_capabilities("ollama/llama3.1", modality=VoiceModality.TEXT)
    """
    _get_registry().register(
        model, modality=modality, native_stt=native_stt, native_tts=native_tts, mode=mode
    )
