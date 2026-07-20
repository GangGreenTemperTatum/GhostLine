"""LiteLLM model adapter factory.

Keeps the LLM provider swappable. The Agents SDK's ``LitellmModel`` accepts
any LiteLLM-supported model string (``openai/gpt-4o-mini``,
``anthropic/claude-3-5-sonnet-latest``, ``ollama/llama3.1``, ...). The
active model is selected by the ``LITELLM_MODEL`` env var (see Settings).

Never hard-code ``openai.ChatCompletions`` calls; go through here.
"""

from __future__ import annotations

from typing import Final

from agents.extensions.models.litellm_model import LitellmModel

__all__ = ("DEFAULT_MODEL", "build_model")

# Used when Settings doesn't supply one. Mirrors the legacy default.
DEFAULT_MODEL: Final[str] = "openai/gpt-4o-mini"


def build_model(
    model: str = DEFAULT_MODEL,
    *,
    api_key: str | None = None,
    api_base: str | None = None,
) -> LitellmModel:
    """Construct a :class:`LitellmModel` for the Agents SDK.

    Args:
        model: LiteLLM model string, e.g. ``"openai/gpt-4o-mini"``.
        api_key: Optional API key override (else LiteLLM reads from env).
        api_base: Optional base URL override (for local Ollama, vLLM, ...).

    Returns:
        A ``LitellmModel`` instance ready to pass to ``Agent(model=...)``.
    """
    return LitellmModel(model=model, api_key=api_key, base_url=api_base)
