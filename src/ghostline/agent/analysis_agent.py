"""Analysis agent: produces a structured :class:`AnalysisResult` from an utterance.

Replaces the legacy ``nlp.analyze_sentiment`` regex-JSON hack. Uses the
Agents SDK's ``output_type=AnalysisResult`` to get validated structured
output without manual parsing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agents import Agent

from ghostline.agent.analysis import AnalysisResult
from ghostline.agent.model import DEFAULT_MODEL, build_model

if TYPE_CHECKING:
    from agents.extensions.models.litellm_model import LitellmModel

__all__ = ("ANALYSIS_INSTRUCTIONS", "build_analysis_agent")

ANALYSIS_INSTRUCTIONS = """\
You analyze live sales / social-engineering conversations to extract
structured intelligence. For each user utterance you receive, return a
strict AnalysisResult object with calibrated numeric fields and short
string labels. Be conservative: only flag an objection_type if the user
explicitly pushed back. Only list buying_signals if they actually
indicate forward motion. Never invent fields beyond the schema.
"""


def build_analysis_agent(
    model: LitellmModel | str | None = None,
    *,
    default_model: str = DEFAULT_MODEL,
) -> Agent[None]:
    """Construct the analysis agent with ``output_type=AnalysisResult``.

    Args:
        model: A LitellmModel instance, a model string, or None to build
            a default LiteLLM model from ``default_model``.
        default_model: Fallback model string when ``model`` is None.
    """
    if model is None:
        model = build_model(default_model)
    return Agent(
        name="ghostline-analyzer",
        instructions=ANALYSIS_INSTRUCTIONS,
        model=model,
        output_type=AnalysisResult,
    )
