"""Structured analysis result, replacing the legacy regex-parsed JSON hack.

The legacy ``nlp.analyze_sentiment`` asked GPT-3.5 to "return a JSON object"
and then regex-extracted ``{.*}`` from the response, falling back to a stub
on parse failure. The Agents SDK supports Pydantic ``output_type`` directly,
so we get validated structured output for free.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ("AnalysisResult",)


class AnalysisResult(BaseModel):
    """LLM-produced assessment of the target's latest utterance.

    Used by the trigger selector and stage-transition logic. Fields mirror
    the legacy ``analyze_sentiment`` JSON contract so existing behaviour is
    preserved.
    """

    sentiment_score: float = Field(
        ...,
        ge=-1.0,
        le=1.0,
        description="Emotional valence of the target's utterance (-1=negative, 0=neutral, +1=positive).",
    )
    interest_level: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Estimated engagement / buying signal strength (0..1).",
    )
    objection_type: str | None = Field(
        default=None,
        description="If the target raised an objection, a short label like 'price', 'timing', 'trust'. None otherwise.",
    )
    key_concerns: list[str] = Field(
        default_factory=list,
        description="Top 1-3 concerns surfaced in the utterance.",
    )
    buying_signals: list[str] = Field(
        default_factory=list,
        description="Positive indicators that the target is moving toward commitment.",
    )
    suggested_approach: str = Field(
        ...,
        description="One-sentence recommendation for the next reply strategy.",
    )
