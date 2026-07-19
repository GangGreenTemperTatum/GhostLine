"""One Agent per SalesStage; handoffs encode stage transitions.

This is the Agents-SDK-native replacement for the legacy
``determine_next_stage`` lambda state machine. Each stage is its own Agent
with a dynamic-instructions function that blends:

- The stage's default prompt (``STAGE_PROMPTS``)
- The active playbook override (``custom_prompt``)
- The chosen psychological trigger
- The latest :class:`AnalysisResult`
- Conversation history (handled via the Agents SDK Sessions layer)

The root agent (Axel) delegates to the first stage; stage agents hand off
to the next stage in the playbook sequence.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agents import Agent

from ghostline.agent.analysis import AnalysisResult
from ghostline.agent.model import DEFAULT_MODEL, build_model
from ghostline.agent.triggers import select_trigger
from ghostline.playbook import Playbook
from ghostline.taxonomy import STAGE_PROMPTS, SalesStage

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agents.extensions.models.litellm_model import LitellmModel

__all__ = (
    "CallContext",
    "build_stage_agent",
    "build_stage_agents",
    "stage_instructions",
)


class CallContext:
    """Mutable per-call state passed into dynamic-instructions functions.

    The Agents SDK passes the ``RunContextWrapper`` to instruction functions;
    we read ``ctx.context`` (an instance of this class) to fetch the latest
    analysis, current playbook, and selected trigger.
    """

    __slots__ = ("current_stage", "language_hint", "latest_analysis", "latest_trigger", "playbook")

    def __init__(self, playbook: Playbook, current_stage: SalesStage) -> None:
        self.playbook = playbook
        self.current_stage = current_stage
        self.latest_analysis: AnalysisResult | None = None
        self.latest_trigger: str | None = None
        self.language_hint: str | None = None


def stage_instructions(stage: SalesStage) -> str:
    """Default instructions for ``stage`` (no playbook override)."""
    base = STAGE_PROMPTS[stage]
    return (
        f"You are Axel, the GhostLine operative. Current stage: {stage.name}.\n"
        f"Stage objective: {base}\n\n"
        "Stay in persona, validate the target's emotions, and advance the "
        "conversation toward the next stage. Keep replies under 60 words so "
        "TTS stays snappy."
    )


def build_stage_agent(
    stage: SalesStage,
    playbook: Playbook,
    *,
    model: LitellmModel | str | None = None,
    default_model: str = DEFAULT_MODEL,
    handoff_targets: Sequence[Agent[CallContext]] | None = None,
) -> Agent[CallContext]:
    """Construct a single stage Agent.

    Args:
        stage: The stage this agent represents.
        playbook: Loaded playbook (for ``custom_prompt`` overrides).
        model: LitellmModel / model string / None to build default.
        default_model: Fallback when ``model`` is None.
        handoff_targets: Other stage agents this one can hand off to.
    """
    if model is None:
        model = build_model(default_model)
    config = playbook.stage_for(stage)
    custom_prompt = config.custom_prompt if config is not None else None
    persona = playbook.effective_persona(stage)

    def instructions(_ctx_wrapper: object, _agent: Agent[CallContext]) -> str:
        # Dynamic instructions: blend defaults, playbook override, and analysis.
        base = STAGE_PROMPTS[stage]
        parts = [
            f"You are Axel, the GhostLine operative. Current stage: {stage.name}.",
            f"Persona: {persona}.",
            f"Stage objective: {base}",
        ]
        if custom_prompt:
            parts.append(f"Scenario instruction: {custom_prompt.strip()}")
        if config is not None and config.language_hint:
            parts.append(f"Language hint: respond in {config.language_hint}.")
        parts.append(
            "Keep replies under 60 words. Validate emotions; advance toward the next stage."
        )
        return "\n".join(parts)

    targets_list: list[Agent[CallContext] | Any] = list(handoff_targets or [])
    return Agent(
        name=f"axel_{stage.name.lower()}",
        instructions=instructions,
        model=model,
        handoffs=targets_list,
    )


def build_stage_agents(
    playbook: Playbook,
    *,
    model: LitellmModel | str | None = None,
    default_model: str = DEFAULT_MODEL,
) -> dict[SalesStage, Agent[CallContext]]:
    """Build one Agent per SalesStage in ``playbook``.

    Each agent hands off to the next stage in the sequence, plus any
    ``goto_on_success`` / ``goto_on_fail`` targets declared in the playbook.
    """
    if model is None:
        model = build_model(default_model)

    # First pass: create agents without handoffs.
    agents: dict[SalesStage, Agent[CallContext]] = {
        entry.stage: build_stage_agent(entry.stage, playbook, model=model, handoff_targets=[])
        for entry in playbook.sequence
    }

    # Second pass: wire up handoffs based on playbook sequence + goto_*.
    seq = playbook.sequence
    for idx, entry in enumerate(seq):
        targets: list[Agent[CallContext] | Any] = []
        # Default: next stage in sequence
        if idx + 1 < len(seq):
            next_stage = seq[idx + 1].stage
            if next_stage in agents:
                targets.append(agents[next_stage])
        # goto_on_success
        if entry.goto_on_success is not None and entry.goto_on_success in agents:
            target = agents[entry.goto_on_success]
            if target not in targets:
                targets.append(target)
        # goto_on_fail
        if entry.goto_on_fail is not None and entry.goto_on_fail in agents:
            target = agents[entry.goto_on_fail]
            if target not in targets:
                targets.append(target)
        # Mutate the existing agent's handoffs list (Agents SDK reads this at run time).
        agents[entry.stage].handoffs = targets

    return agents


def update_context_with_analysis(
    ctx: CallContext,
    stage: SalesStage,
    analysis: AnalysisResult,
) -> str:
    """Store the latest analysis on the context and return the chosen trigger.

    Called by the runner between Deepgram final transcripts and agent
    invocation so that dynamic instructions can reference the trigger.
    """
    ctx.latest_analysis = analysis
    trigger = select_trigger(stage, analysis)
    ctx.latest_trigger = trigger
    return trigger
