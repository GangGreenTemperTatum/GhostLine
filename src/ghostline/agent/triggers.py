"""Psychological trigger selector — preserved from legacy ``nlp.select_psychological_trigger``.

Exposed as a pure function so it can be unit-tested without an LLM, and as
a function tool so the agent can call it dynamically during a run.
"""

from __future__ import annotations

import secrets
from typing import Final

from ghostline.agent.analysis import AnalysisResult
from ghostline.taxonomy import PSYCH_TRIGGERS, SalesStage

__all__ = ("DEFAULT_TRIGGER", "select_trigger")

DEFAULT_TRIGGER: Final[str] = "acknowledge"
# Interest-level threshold above which we bias toward closing-flavour triggers.
_HIGH_INTEREST_THRESHOLD: Final[float] = 0.8


def _biased_choice(options: list[str]) -> str:
    """Cryptographically-fair pick from a non-empty list (no RNG seed needed)."""
    return options[secrets.randbelow(len(options))]


def select_trigger(
    stage: SalesStage,
    analysis: AnalysisResult,
    *,
    rng: object | None = None,
) -> str:
    """Pick a psychological trigger for ``stage`` biased by ``analysis``.

    Mirrors the legacy heuristics:

    - Negative sentiment → empathy-flavoured triggers (``feel``/``reciprocity``)
    - Active objection → objection-handling triggers (``objection``/``felt``)
    - High interest → close-flavoured triggers (``close``/``future``)
    - Otherwise → uniform random pick from the stage's trigger list

    Args:
        stage: Current conversation stage.
        analysis: The structured analysis of the latest utterance.
        rng: Reserved for backwards compatibility; selection uses
            :mod:`secrets` so callers don't need to seed an RNG.

    Returns:
        A single trigger name string.
    """
    del rng  # kept for API stability; not used
    triggers = PSYCH_TRIGGERS.get(stage, [])
    if not triggers:
        return DEFAULT_TRIGGER

    if analysis.sentiment_score < 0:
        relevant = [t for t in triggers if "feel" in t or "reciprocity" in t]
    elif analysis.objection_type:
        relevant = [t for t in triggers if "objection" in t or "felt" in t]
    elif analysis.interest_level > _HIGH_INTEREST_THRESHOLD:
        relevant = [t for t in triggers if "close" in t or "future" in t]
    else:
        relevant = triggers

    return _biased_choice(relevant if relevant else triggers)
