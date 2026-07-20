"""Tests for the playbook loader and schema."""

from __future__ import annotations

from pathlib import Path

import pytest

from ghostline.playbook import Playbook, PlaybookError
from ghostline.taxonomy import SalesStage

# ─── Fixtures ────────────────────────────────────────────────────────

VALID_YAML = """
meta:
  name: Test Playbook
  version: 1.5
  author: tester
defaults:
  persona: professional
  silent_until: 6
  ambient_ratio: 0.08
  temperature: 0.7
sequence:
  - stage: RAPPORT
    custom_prompt: "Hello there"
  - stage: CREDIBILITY
    persona: confident
  - stage: CLOSE
    success_regex: "\\\\b\\\\d{6}\\\\b"
    goto_on_success: REPORTING
  - stage: REPORTING
    operator_note: "Captured OTP"
"""


@pytest.fixture
def playbook() -> Playbook:
    return Playbook.from_string(VALID_YAML)


class TestLoading:
    def test_loads_from_string(self, playbook: Playbook) -> None:
        assert playbook.meta.name == "Test Playbook"
        assert playbook.meta.version == 1.5
        assert playbook.meta.author == "tester"

    def test_loads_from_file(self, tmp_path: Path) -> None:
        p = tmp_path / "pb.yaml"
        p.write_text(VALID_YAML)
        pb = Playbook.from_file(p)
        assert pb.source_path == p
        assert pb.meta.name == "Test Playbook"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(PlaybookError, match="not found"):
            Playbook.from_file(tmp_path / "nope.yaml")

    def test_invalid_yaml_raises(self) -> None:
        with pytest.raises(PlaybookError, match="Invalid YAML"):
            Playbook.from_string("meta: [unclosed")

    def test_top_level_must_be_mapping(self) -> None:
        with pytest.raises(PlaybookError, match="top-level must be a mapping"):
            Playbook.from_string("- just\n- a\n- list")

    def test_empty_sequence_raises(self) -> None:
        with pytest.raises(PlaybookError, match="at least one stage"):
            Playbook.from_string("meta: {name: x}\nsequence: []")

    def test_duplicate_stage_raises(self) -> None:
        yaml_text = """
meta: {name: dup}
sequence:
  - stage: RAPPORT
  - stage: RAPPORT
"""
        with pytest.raises(PlaybookError, match="duplicates stage RAPPORT"):
            Playbook.from_string(yaml_text)

    def test_unknown_stage_name_raises(self) -> None:
        yaml_text = """
meta: {name: x}
sequence:
  - stage: NOT_A_REAL_STAGE
"""
        with pytest.raises(PlaybookError):
            Playbook.from_string(yaml_text)


class TestDefaults:
    def test_defaults_applied(self, playbook: Playbook) -> None:
        assert playbook.defaults.persona == "professional"
        assert playbook.defaults.silent_until == 6
        assert playbook.defaults.ambient_ratio == pytest.approx(0.08)

    def test_defaults_optional(self) -> None:
        yaml_text = """
meta: {name: min}
sequence:
  - stage: RAPPORT
"""
        pb = Playbook.from_string(yaml_text)
        # Built-in defaults
        assert pb.defaults.persona == "professional"
        assert pb.defaults.silent_until == 8
        assert pb.defaults.ambient_ratio == pytest.approx(0.1)


class TestStageConfig:
    def test_stages_loaded_in_order(self, playbook: Playbook) -> None:
        assert playbook.stage_names == ["RAPPORT", "CREDIBILITY", "CLOSE", "REPORTING"]

    def test_first_stage(self, playbook: Playbook) -> None:
        assert playbook.first_stage == SalesStage.RAPPORT

    def test_stage_for_returns_config(self, playbook: Playbook) -> None:
        config = playbook.stage_for(SalesStage.CLOSE)
        assert config is not None
        assert config.success_regex is not None
        assert config.goto_on_success == SalesStage.REPORTING

    def test_stage_for_returns_none_for_missing(self, playbook: Playbook) -> None:
        assert playbook.stage_for(SalesStage.DISCOVERY) is None

    def test_max_cycles_defaults_to_one(self) -> None:
        yaml_text = """
meta: {name: x}
sequence:
  - stage: RAPPORT
"""
        pb = Playbook.from_string(yaml_text)
        assert pb.sequence[0].max_cycles == 1

    def test_max_cycles_must_be_positive(self) -> None:
        yaml_text = """
meta: {name: x}
sequence:
  - stage: RAPPORT
    max_cycles: 0
"""
        with pytest.raises(PlaybookError, match="max_cycles"):
            Playbook.from_string(yaml_text)

    def test_ambient_ratio_must_be_in_range(self) -> None:
        yaml_text = """
meta: {name: x}
sequence:
  - stage: RAPPORT
    ambient_ratio: 1.5
"""
        with pytest.raises(PlaybookError, match="ambient_ratio"):
            Playbook.from_string(yaml_text)

    def test_silent_until_must_be_nonnegative(self) -> None:
        yaml_text = """
meta: {name: x}
sequence:
  - stage: RAPPORT
    silent_until: -1
"""
        with pytest.raises(PlaybookError, match="silent_until"):
            Playbook.from_string(yaml_text)

    def test_extra_field_rejected(self) -> None:
        yaml_text = """
meta: {name: x}
sequence:
  - stage: RAPPORT
    bogus_field: yes
"""
        with pytest.raises(PlaybookError):
            Playbook.from_string(yaml_text)


class TestEffectiveValues:
    def test_stage_overrides_default_persona(self, playbook: Playbook) -> None:
        assert playbook.effective_persona(SalesStage.CREDIBILITY) == "confident"

    def test_falls_back_to_default_persona(self, playbook: Playbook) -> None:
        assert playbook.effective_persona(SalesStage.RAPPORT) == "professional"

    def test_stage_overrides_silent_until(self) -> None:
        yaml_text = """
meta: {name: x}
defaults: {silent_until: 10}
sequence:
  - stage: RAPPORT
    silent_until: 3
"""
        pb = Playbook.from_string(yaml_text)
        assert pb.effective_silent_until(SalesStage.RAPPORT) == 3

    def test_falls_back_to_default_silent_until(self) -> None:
        yaml_text = """
meta: {name: x}
defaults: {silent_until: 10}
sequence:
  - stage: RAPPORT
"""
        pb = Playbook.from_string(yaml_text)
        assert pb.effective_silent_until(SalesStage.RAPPORT) == 10

    def test_stage_overrides_ambient_ratio(self) -> None:
        yaml_text = """
meta: {name: x}
defaults: {ambient_ratio: 0.1}
sequence:
  - stage: RAPPORT
    ambient_ratio: 0.25
"""
        pb = Playbook.from_string(yaml_text)
        assert pb.effective_ambient_ratio(SalesStage.RAPPORT) == pytest.approx(0.25)

    def test_stage_not_in_playbook_uses_defaults(self, playbook: Playbook) -> None:
        # DISCOVERY isn't in the playbook; should still return defaults
        assert playbook.effective_persona(SalesStage.DISCOVERY) == "professional"
        assert playbook.effective_silent_until(SalesStage.DISCOVERY) == 6


class TestSummary:
    def test_to_summary_is_serializable(self, playbook: Playbook) -> None:
        summary = playbook.to_summary()
        assert summary["name"] == "Test Playbook"
        assert summary["version"] == 1.5
        assert len(summary["stages"]) == 4
        close_summary = next(s for s in summary["stages"] if s["stage"] == "CLOSE")
        assert close_summary["goto_on_success"] == "REPORTING"
        assert close_summary["success_regex"] is not None


class TestBundledPlaybooks:
    """Regression: the playbooks shipped in playbooks/ must load unchanged."""

    @pytest.mark.parametrize(
        "filename",
        [
            "executive_spearphish_multi-lingual.yaml",
            "hr_benefits_open_enrollment_phish.yaml",
            "vendor_payment_change_ceo_whaling.yaml",
            "zero_day_it_patch.yaml",
            "it-test.yaml",
        ],
    )
    def test_bundled_playbook_loads(self, filename: str) -> None:
        path = Path(__file__).resolve().parents[2] / "playbooks" / filename
        if not path.exists():
            pytest.skip(f"bundled playbook {filename} not present")
        pb = Playbook.from_file(path)
        assert len(pb.sequence) >= 1
        assert pb.first_stage == SalesStage.RAPPORT
