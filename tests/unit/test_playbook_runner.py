"""Tests for the playbook runner — exercises the previously-broken fields."""

from __future__ import annotations

from ghostline.playbook import Playbook
from ghostline.playbook_runner import PlaybookRunner, StageExit
from ghostline.taxonomy import SalesStage


def make_playbook(yaml_text: str) -> Playbook:
    return Playbook.from_string(yaml_text)


class TestDefaultProgression:
    def test_advances_to_next_stage_when_no_success_regex(self) -> None:
        pb = make_playbook(
            """
meta: {name: x}
sequence:
  - stage: RAPPORT
  - stage: CREDIBILITY
  - stage: CLOSE
"""
        )
        runner = PlaybookRunner(pb)
        assert runner.current_stage == SalesStage.RAPPORT
        outcome = runner.evaluate("anything the user says")
        assert outcome.current_stage == SalesStage.RAPPORT
        assert outcome.next_stage == SalesStage.CREDIBILITY
        assert outcome.success is False
        assert outcome.exit_ is None
        assert runner.current_stage == SalesStage.CREDIBILITY

    def test_last_stage_without_close_exits_abandoned(self) -> None:
        pb = make_playbook(
            """
meta: {name: x}
sequence:
  - stage: RAPPORT
"""
        )
        runner = PlaybookRunner(pb)
        outcome = runner.evaluate("hi")
        assert outcome.exit_ is not None
        assert outcome.exit_.outcome == "abandoned"
        assert "Last playbook stage reached" in outcome.exit_.reason

    def test_reaching_reporting_exits_compromised(self) -> None:
        pb = make_playbook(
            """
meta: {name: x}
sequence:
  - stage: RAPPORT
  - stage: REPORTING
"""
        )
        runner = PlaybookRunner(pb)
        runner.evaluate("advance past RAPPORT")
        # Now on REPORTING; next evaluate should exit
        outcome = runner.evaluate("reporting time")
        assert outcome.exit_ is not None
        assert outcome.exit_.outcome == "compromised"


class TestSuccessRegex:
    def test_match_advances_via_goto_on_success(self) -> None:
        pb = make_playbook(
            """
meta: {name: x}
sequence:
  - stage: VALIDATION
    success_regex: "\\\\b(yes|ready)\\\\b"
    goto_on_success: CLOSE
  - stage: CLOSE
  - stage: REPORTING
"""
        )
        runner = PlaybookRunner(pb)
        outcome = runner.evaluate("yes I'm ready")
        assert outcome.success is True
        assert outcome.matched_pattern is not None
        assert outcome.next_stage == SalesStage.CLOSE
        assert outcome.exit_ is None  # CLOSE not yet matched

    def test_match_without_goto_advances_to_next_in_sequence(self) -> None:
        pb = make_playbook(
            """
meta: {name: x}
sequence:
  - stage: RAPPORT
    success_regex: "hello"
  - stage: CREDIBILITY
"""
        )
        runner = PlaybookRunner(pb)
        outcome = runner.evaluate("hello there")
        assert outcome.success is True
        assert outcome.next_stage == SalesStage.CREDIBILITY

    def test_no_match_stays_for_retry(self) -> None:
        pb = make_playbook(
            """
meta: {name: x}
sequence:
  - stage: VALIDATION
    success_regex: "\\\\b(yubi|face)\\\\b"
    max_cycles: 3
  - stage: CLOSE
"""
        )
        runner = PlaybookRunner(pb)
        outcome = runner.evaluate("not matching")
        assert outcome.success is False
        assert outcome.next_stage == SalesStage.VALIDATION  # stays
        assert outcome.cycles_remaining == 2

    def test_case_insensitive_match(self) -> None:
        pb = make_playbook(
            """
meta: {name: x}
sequence:
  - stage: VALIDATION
    success_regex: "yes"
    goto_on_success: CLOSE
  - stage: CLOSE
"""
        )
        runner = PlaybookRunner(pb)
        outcome = runner.evaluate("YES I agree")
        assert outcome.success is True

    def test_close_success_regex_exits_compromised(self) -> None:
        pb = make_playbook(
            """
meta: {name: x}
sequence:
  - stage: CLOSE
    success_regex: "\\\\b\\\\d{6}\\\\b"
  - stage: REPORTING
"""
        )
        runner = PlaybookRunner(pb)
        outcome = runner.evaluate("the code is 123456")
        assert outcome.success is True
        assert outcome.exit_ is not None
        assert outcome.exit_.outcome == "compromised"
        assert outcome.matched_pattern == "123456"

    def test_invalid_regex_treated_as_no_match(self) -> None:
        pb = make_playbook(
            """
meta: {name: x}
sequence:
  - stage: RAPPORT
    success_regex: "[unclosed"
    max_cycles: 1
  - stage: CLOSE
"""
        )
        runner = PlaybookRunner(pb)
        outcome = runner.evaluate("anything")
        # Invalid regex shouldn't crash; treat as no match → fail
        assert outcome.success is False


class TestMaxCyclesAndGotoOnFail:
    def test_exhausted_max_cycles_triggers_goto_on_fail(self) -> None:
        pb = make_playbook(
            """
meta: {name: x}
sequence:
  - stage: URGENCY
    max_cycles: 2
    goto_on_fail: OBJECTION
  - stage: OBJECTION
  - stage: CLOSE
"""
        )
        runner = PlaybookRunner(pb)
        # First attempt: no match, retry
        o1 = runner.evaluate("not yet")
        assert o1.next_stage == SalesStage.URGENCY
        assert o1.cycles_remaining == 1
        # Second attempt: max_cycles hit, goto_on_fail
        o2 = runner.evaluate("still not")
        assert o2.success is False
        assert o2.next_stage == SalesStage.OBJECTION
        assert o2.cycles_remaining == 0
        assert runner.current_stage == SalesStage.OBJECTION

    def test_no_goto_on_fail_uses_default_progression(self) -> None:
        pb = make_playbook(
            """
meta: {name: x}
sequence:
  - stage: URGENCY
    max_cycles: 1
  - stage: CLOSE
"""
        )
        runner = PlaybookRunner(pb)
        outcome = runner.evaluate("no match")
        assert outcome.success is False
        assert outcome.next_stage == SalesStage.CLOSE  # default progression

    def test_max_cycles_one_means_single_attempt(self) -> None:
        pb = make_playbook(
            """
meta: {name: x}
sequence:
  - stage: RAPPORT
    success_regex: "hello"
    max_cycles: 1
    goto_on_fail: CLOSE
  - stage: CLOSE
"""
        )
        runner = PlaybookRunner(pb)
        outcome = runner.evaluate("nothing relevant here")
        assert outcome.success is False
        assert outcome.cycles_remaining == 0
        assert outcome.next_stage == SalesStage.CLOSE


class TestGotoBranching:
    def test_goto_on_success_skips_intermediate_stages(self) -> None:
        pb = make_playbook(
            """
meta: {name: x}
sequence:
  - stage: URGENCY
    success_regex: "ready"
    goto_on_success: CLOSE
  - stage: TRIAL_CLOSE
  - stage: OBJECTION
  - stage: CLOSE
"""
        )
        runner = PlaybookRunner(pb)
        outcome = runner.evaluate("I'm ready")
        assert outcome.success is True
        assert outcome.next_stage == SalesStage.CLOSE  # skips TRIAL_CLOSE + OBJECTION

    def test_goto_on_fail_loops_back(self) -> None:
        pb = make_playbook(
            """
meta: {name: x}
sequence:
  - stage: OBJECTION
    max_cycles: 1
    goto_on_fail: PROOF
  - stage: PROOF
    max_cycles: 1
    goto_on_fail: OBJECTION
"""
        )
        runner = PlaybookRunner(pb)
        # OBJECTION fails → goto PROOF
        o1 = runner.evaluate("no")
        assert o1.next_stage == SalesStage.PROOF
        # PROOF fails → goto OBJECTION (loop back)
        o2 = runner.evaluate("still no")
        assert o2.next_stage == SalesStage.OBJECTION


class TestForceExit:
    def test_force_exit_records_current_stage(self) -> None:
        pb = make_playbook(
            """
meta: {name: x}
sequence:
  - stage: RAPPORT
  - stage: CLOSE
"""
        )
        runner = PlaybookRunner(pb)
        runner.evaluate("advance to CLOSE")
        exit_ = runner.force_exit("abandoned", "caller hung up")
        assert isinstance(exit_, StageExit)
        assert exit_.outcome == "abandoned"
        assert exit_.final_stage == SalesStage.CLOSE
        assert "caller hung up" in exit_.reason


class TestAdvanceTo:
    def test_advance_to_sets_current_stage(self) -> None:
        pb = make_playbook(
            """
meta: {name: x}
sequence:
  - stage: RAPPORT
  - stage: CLOSE
"""
        )
        runner = PlaybookRunner(pb)
        runner.advance_to(SalesStage.CLOSE)
        assert runner.current_stage == SalesStage.CLOSE

    def test_advance_to_stage_not_in_playbook(self) -> None:
        pb = make_playbook(
            """
meta: {name: x}
sequence:
  - stage: RAPPORT
"""
        )
        runner = PlaybookRunner(pb)
        runner.advance_to(SalesStage.OBJECTION)
        assert runner.current_stage == SalesStage.OBJECTION
        # Evaluating should fall through to default progression via enum
        outcome = runner.evaluate("hi")
        assert outcome.current_stage == SalesStage.OBJECTION
