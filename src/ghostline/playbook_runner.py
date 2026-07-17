"""Stage execution engine: implements the previously-no-op playbook fields.

This module is pure logic — no I/O, no async — so it's exhaustively
unit-testable. The agent/server layers call into here to decide stage
transitions and exit conditions.

Responsibilities:

- Evaluate ``success_regex`` against user utterances
- Track per-stage cycle counts and enforce ``max_cycles``
- Resolve ``goto_on_success`` / ``goto_on_fail`` transitions
- Default progression: next stage in the playbook sequence
- Detect terminal conditions (REPORTING reached, or last stage exits)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

from ghostline.playbook import Playbook, StageConfig
from ghostline.taxonomy import SalesStage

__all__ = (
    "PlaybookRunner",
    "StageExit",
    "StageOutcome",
)

# Outcome labels recorded in the calls.outcome column.
_OUTCOME_COMPROMISED: Final[str] = "compromised"
_OUTCOME_ABANDONED: Final[str] = "abandoned"
_OUTCOME_IN_PROGRESS: Final[str] = "in_progress"


@dataclass(slots=True, frozen=True)
class StageOutcome:
    """Result of evaluating one user utterance against the current stage.

    Attributes:
        current_stage: The stage we evaluated the utterance in.
        next_stage: The stage the runner recommends transitioning to.
            May equal ``current_stage`` (retry within max_cycles).
        success: Whether ``success_regex`` matched.
        cycles_remaining: How many more attempts allowed at ``current_stage``
            before ``goto_on_fail`` triggers. ``-1`` means unbounded (no
            max_cycles set or already exhausted).
        exit_: If set, the call should terminate with this :class:`StageExit`.
        matched_pattern: The substring of the user utterance that matched
            ``success_regex`` (None if no match).
    """

    current_stage: SalesStage
    next_stage: SalesStage
    success: bool
    cycles_remaining: int
    exit_: StageExit | None = None
    matched_pattern: str | None = None


@dataclass(slots=True, frozen=True)
class StageExit:
    """A terminal call state."""

    outcome: str  # "compromised" | "abandoned"
    final_stage: SalesStage
    reason: str


@dataclass(slots=True)
class _StageState:
    """Mutable per-stage cycle tracker."""

    cycles: int = 0  # how many times we've evaluated this stage
    matched_once: bool = False


@dataclass(slots=True)
class PlaybookRunner:
    """Stateful per-call runner that enforces playbook semantics.

    Instantiate one per call. The runner is not thread-safe; the call's
    ``pump_out`` coroutine is the only writer.
    """

    playbook: Playbook
    _stage_states: dict[SalesStage, _StageState] = field(default_factory=dict)
    _current_stage: SalesStage | None = None

    def __post_init__(self) -> None:
        # Initialise state for every stage in the playbook.
        for entry in self.playbook.sequence:
            self._stage_states.setdefault(entry.stage, _StageState())
        self._current_stage = self.playbook.first_stage

    @property
    def current_stage(self) -> SalesStage:
        """The stage the call is currently in."""
        if self._current_stage is None:  # pragma: no cover - invariant
            raise RuntimeError("Runner has no current stage")
        return self._current_stage

    def advance_to(self, stage: SalesStage) -> None:
        """Force a transition to ``stage``.

        Used by the agent layer for dynamic handoffs that bypass
        success_regex.
        """
        self._stage_states.setdefault(stage, _StageState())
        self._current_stage = stage

    def evaluate(self, user_utterance: str) -> StageOutcome:
        """Evaluate ``user_utterance`` against the current stage.

        This is the main entrypoint. Returns a :class:`StageOutcome` describing
        the recommended next stage, whether the stage succeeded, and any
        terminal exit condition.
        """
        if self._current_stage is None:  # pragma: no cover - invariant
            raise RuntimeError("Runner has no current stage")
        stage = self._current_stage
        config = self.playbook.stage_for(stage)
        if config is None:
            # Stage not in playbook; fall back to default progression.
            return self._default_progression(stage)

        state = self._stage_states[stage]
        state.cycles += 1

        # 1. Check success_regex
        matched = self._match_success(config.success_regex, user_utterance)
        if matched is not None:
            state.matched_once = True
            return self._on_success(stage, config, matched)

        # 2. No match. Check if we've exhausted max_cycles.
        if state.cycles >= config.max_cycles:
            return self._on_fail(stage, config)

        # 3. Retry: stay on the same stage.
        cycles_remaining = max(config.max_cycles - state.cycles, 0)
        return StageOutcome(
            current_stage=stage,
            next_stage=stage,
            success=False,
            cycles_remaining=cycles_remaining,
        )

    def _on_success(self, stage: SalesStage, config: StageConfig, matched: str) -> StageOutcome:
        """Handle a successful success_regex match."""
        # goto_on_success, else default next stage
        goto = config.goto_on_success
        next_stage = goto if goto is not None else self._next_in_sequence(stage)

        # If success_regex on CLOSE stage matched → compromised
        exit_: StageExit | None = None
        if stage == SalesStage.CLOSE:
            exit_ = StageExit(
                outcome=_OUTCOME_COMPROMISED,
                final_stage=stage,
                reason=f"CLOSE success_regex matched: {matched!r}",
            )
        elif next_stage == SalesStage.REPORTING:
            # Reaching REPORTING via goto is a successful close.
            exit_ = StageExit(
                outcome=_OUTCOME_COMPROMISED,
                final_stage=stage,
                reason=f"Reached REPORTING from {stage.name}",
            )

        self._current_stage = next_stage
        return StageOutcome(
            current_stage=stage,
            next_stage=next_stage,
            success=True,
            cycles_remaining=0,
            exit_=exit_,
            matched_pattern=matched,
        )

    def _on_fail(self, stage: SalesStage, config: StageConfig) -> StageOutcome:
        """Handle max_cycles exhaustion without a success_regex match."""
        goto = config.goto_on_fail
        if goto is not None:
            next_stage = goto
            self._current_stage = next_stage
            return StageOutcome(
                current_stage=stage,
                next_stage=next_stage,
                success=False,
                cycles_remaining=0,
            )
        # No goto_on_fail → default progression
        return self._default_progression(stage)

    def _default_progression(self, stage: SalesStage) -> StageOutcome:
        """Advance to the next stage in the playbook sequence, or exit."""
        next_stage = self._next_in_sequence(stage)
        self._current_stage = next_stage

        # If we just landed on REPORTING or the sequence ended, exit.
        if next_stage == SalesStage.REPORTING:
            return StageOutcome(
                current_stage=stage,
                next_stage=next_stage,
                success=False,
                cycles_remaining=0,
                exit_=StageExit(
                    outcome=_OUTCOME_COMPROMISED,
                    final_stage=stage,
                    reason=f"Reached REPORTING via default progression from {stage.name}",
                ),
            )
        if next_stage == stage:
            # We were on the last stage and there's no further progression.
            return StageOutcome(
                current_stage=stage,
                next_stage=stage,
                success=False,
                cycles_remaining=0,
                exit_=StageExit(
                    outcome=_OUTCOME_ABANDONED,
                    final_stage=stage,
                    reason="Last playbook stage reached without explicit close",
                ),
            )
        return StageOutcome(
            current_stage=stage,
            next_stage=next_stage,
            success=False,
            cycles_remaining=0,
        )

    def _next_in_sequence(self, stage: SalesStage) -> SalesStage:
        """Return the stage after ``stage`` in the playbook sequence.

        If ``stage`` is the last entry, returns ``stage`` unchanged.
        """
        seq = self.playbook.sequence
        for idx, entry in enumerate(seq):
            if entry.stage == stage:
                if idx + 1 < len(seq):
                    return seq[idx + 1].stage
                return stage
        # Stage not in sequence: default to next SalesStage enum value.
        members = list(SalesStage)
        try:
            pos = members.index(stage)
        except ValueError:
            return stage
        return members[min(pos + 1, len(members) - 1)]

    @staticmethod
    def _match_success(pattern: str | None, utterance: str) -> str | None:
        """Return the matched substring if ``pattern`` matches ``utterance``.

        Returns ``None`` if pattern is None or no match.
        """
        if pattern is None:
            return None
        try:
            m = re.search(pattern, utterance, re.IGNORECASE | re.MULTILINE)
        except re.error:
            return None
        if m is None:
            return None
        return m.group(0)

    def force_exit(self, outcome: str, reason: str) -> StageExit:
        """Operator-triggered exit (e.g. caller hung up or stop event)."""
        return StageExit(
            outcome=outcome,
            final_stage=self.current_stage,
            reason=reason,
        )
