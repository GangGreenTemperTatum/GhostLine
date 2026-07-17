"""Axel: the root Agent for GhostLine's persuasion engine.

Axel is the entry point the server's call handler invokes. It delegates to
per-stage sub-agents built by :func:`build_stage_agents`. The
:class:`CallContext` carries the playbook, current stage, and latest
analysis across runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agents import Agent

from ghostline.agent.model import DEFAULT_MODEL, build_model
from ghostline.agent.stage_handoffs import CallContext, build_stage_agents
from ghostline.playbook import Playbook
from ghostline.taxonomy import SalesStage

if TYPE_CHECKING:
    from agents.extensions.models.litellm_model import LitellmModel

__all__ = ("AxelAgent", "build_axel")


class AxelAgent:
    """Composition of the root agent + per-stage sub-agents + call context.

    Attributes:
        root: The Agents SDK ``Agent`` callers pass to ``Runner.run``.
        stages: Per-stage sub-agents keyed by :class:`SalesStage`.
        context: The per-call :class:`CallContext` passed into runs.
    """

    def __init__(
        self,
        root: Agent[CallContext],
        stages: dict[SalesStage, Agent[CallContext]],
        context: CallContext,
    ) -> None:
        self.root = root
        self.stages = stages
        self.context = context

    @property
    def current_stage(self) -> SalesStage:
        """The stage the call is currently in."""
        return self.context.current_stage

    def advance_to(self, stage: SalesStage) -> None:
        """Update the call context to reflect a stage transition."""
        self.context.current_stage = stage


def build_axel(
    playbook: Playbook,
    *,
    model: LitellmModel | str | None = None,
    default_model: str = DEFAULT_MODEL,
) -> AxelAgent:
    """Build the root Axel agent + sub-agents for a call.

    Args:
        playbook: The loaded playbook for this call.
        model: LiteLLM model instance/string, or None to use ``default_model``.
        default_model: Fallback model string.
    """
    if model is None:
        model = build_model(default_model)

    stages = build_stage_agents(playbook, model=model, default_model=default_model)
    first_stage = playbook.first_stage
    first_agent = stages.get(first_stage)
    if first_agent is None:  # pragma: no cover - invariant
        raise ValueError(f"Playbook first stage {first_stage!r} has no agent")

    def root_instructions(_ctx: object, _agent: Agent[CallContext]) -> str:
        return (
            "You are Axel, the GhostLine root operative. Delegate to the "
            f"appropriate stage agent. The call is currently in {first_stage.name}. "
            "Stay in persona, follow the playbook, and never break character."
        )

    root = Agent[CallContext](
        name="axel-root",
        instructions=root_instructions,
        model=model,
        handoffs=list(stages.values()),
    )
    context = CallContext(playbook=playbook, current_stage=first_stage)
    return AxelAgent(root=root, stages=stages, context=context)
