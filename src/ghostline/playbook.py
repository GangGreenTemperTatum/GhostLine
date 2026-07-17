"""YAML playbook loader and schema.

A playbook is the operator-authored call flow. The schema is intentionally
small and backward-compatible with the playbooks shipped in ``playbooks/``.

Top-level keys:

- ``meta``: name / author / version (informational)
- ``defaults``: persona / silent_until / ambient_ratio / temperature applied
  to every stage unless the stage overrides
- ``sequence``: ordered list of stage entries

Per-stage keys (all optional except ``stage``):

- ``stage``       : SalesStage name (required)
- ``persona``     : one of PROSODY_VARIATIONS keys
- ``custom_prompt``: instruction text merged into the agent's prompt
- ``success_regex``: if matched against the user utterance, this stage is
                     considered "passed"
- ``goto_on_success``: SalesStage to jump to when success_regex matches
- ``goto_on_fail``: SalesStage to jump to when max_cycles exhausted without
                    success
- ``max_cycles``  : how many times to retry this stage before goto_on_fail
- ``silent_until``: seconds of silence before the silence monitor nudges
- ``ambient_ratio``: 0..1 mix weight for ambient noise on this stage's TTS
- ``language_hint``: BCP-47 tag passed to the agent for multilingual stages
- ``operator_note``: free text recorded in REPORTING stage
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ghostline.taxonomy import SalesStage

__all__ = (
    "Playbook",
    "PlaybookDefaults",
    "PlaybookError",
    "PlaybookMeta",
    "StageConfig",
)

_DEFAULT_PERSONA: Final[str] = "professional"
_DEFAULT_SILENT_UNTIL: Final[int] = 8
_DEFAULT_AMBIENT_RATIO: Final[float] = 0.1
_DEFAULT_TEMPERATURE: Final[float] = 0.7
_DEFAULT_MAX_CYCLES: Final[int] = 1


class PlaybookError(ValueError):
    """Raised when a playbook file fails schema validation."""


class PlaybookMeta(BaseModel):
    """Informational metadata; not enforced at runtime."""

    model_config = ConfigDict(extra="allow")
    name: str = "untitled"
    version: float = 1.0
    author: str | None = None


class PlaybookDefaults(BaseModel):
    """Global defaults applied to every stage unless overridden."""

    model_config = ConfigDict(extra="forbid")
    persona: str = _DEFAULT_PERSONA
    silent_until: int = _DEFAULT_SILENT_UNTIL
    ambient_ratio: float = _DEFAULT_AMBIENT_RATIO
    temperature: float = _DEFAULT_TEMPERATURE


class StageConfig(BaseModel):
    """One entry in a playbook's ``sequence``."""

    model_config = ConfigDict(extra="forbid")
    stage: SalesStage
    persona: str | None = None
    custom_prompt: str | None = None
    success_regex: str | None = None
    goto_on_success: SalesStage | None = None
    goto_on_fail: SalesStage | None = None
    max_cycles: int = Field(default=_DEFAULT_MAX_CYCLES, ge=1)
    silent_until: int | None = None
    ambient_ratio: float | None = None
    language_hint: str | None = None
    operator_note: str | None = None

    @field_validator("stage", "goto_on_success", "goto_on_fail", mode="before")
    @classmethod
    def _coerce_stage_name(cls, v: Any) -> Any:
        """Allow YAML to use stage names like 'RAPPORT' instead of int values."""
        if isinstance(v, str):
            try:
                return SalesStage[v]
            except KeyError as exc:
                raise ValueError(f"Unknown SalesStage name: {v!r}") from exc
        return v

    @field_validator("ambient_ratio")
    @classmethod
    def _validate_ambient_ratio(cls, v: float | None) -> float | None:
        if v is not None and not 0.0 <= v <= 1.0:
            raise ValueError("ambient_ratio must be in [0, 1]")
        return v

    @field_validator("silent_until")
    @classmethod
    def _validate_silent_until(cls, v: int | None) -> int | None:
        if v is not None and v < 0:
            raise ValueError("silent_until must be >= 0")
        return v


@dataclass(slots=True)
class Playbook:
    """A loaded, validated playbook."""

    meta: PlaybookMeta
    defaults: PlaybookDefaults
    sequence: list[StageConfig]
    source_path: Path | None = None

    def stage_for(self, stage: SalesStage) -> StageConfig | None:
        """Return the first :class:`StageConfig` matching ``stage``, or None."""
        for entry in self.sequence:
            if entry.stage == stage:
                return entry
        return None

    def effective_persona(self, stage: SalesStage) -> str:
        """Persona for ``stage``: stage override or playbook default."""
        entry = self.stage_for(stage)
        if entry is not None and entry.persona is not None:
            return entry.persona
        return self.defaults.persona

    def effective_silent_until(self, stage: SalesStage) -> int:
        """Silence threshold for ``stage``: stage override or default."""
        entry = self.stage_for(stage)
        if entry is not None and entry.silent_until is not None:
            return entry.silent_until
        return self.defaults.silent_until

    def effective_ambient_ratio(self, stage: SalesStage) -> float:
        """Ambient mix weight for ``stage``: stage override or default."""
        entry = self.stage_for(stage)
        if entry is not None and entry.ambient_ratio is not None:
            return entry.ambient_ratio
        return self.defaults.ambient_ratio

    @property
    def stage_names(self) -> list[str]:
        """Names of stages in sequence order (for logging)."""
        return [e.stage.name for e in self.sequence]

    @classmethod
    def from_file(cls, path: Path | str) -> Playbook:
        """Load and validate a playbook YAML file."""
        p = Path(path)
        if not p.exists():
            raise PlaybookError(f"Playbook file not found: {p}")
        try:
            data = yaml.safe_load(p.read_text())
        except yaml.YAMLError as exc:
            raise PlaybookError(f"Invalid YAML in {p}: {exc}") from exc
        if not isinstance(data, dict):
            raise PlaybookError(f"Playbook {p} top-level must be a mapping")
        return cls._from_dict(data, source_path=p)

    @classmethod
    def from_string(cls, yaml_text: str, *, source: str = "<string>") -> Playbook:
        """Load and validate a playbook from a YAML string (tests)."""
        try:
            data = yaml.safe_load(yaml_text)
        except yaml.YAMLError as exc:
            raise PlaybookError(f"Invalid YAML: {exc}") from exc
        if not isinstance(data, dict):
            raise PlaybookError("Playbook top-level must be a mapping")
        return cls._from_dict(data, source_path=Path(source))

    @classmethod
    def _from_dict(cls, data: dict[str, Any], *, source_path: Path) -> Playbook:
        meta_raw = data.get("meta", {})
        if not isinstance(meta_raw, dict):
            raise PlaybookError("'meta' must be a mapping")
        defaults_raw = data.get("defaults", {})
        if not isinstance(defaults_raw, dict):
            raise PlaybookError("'defaults' must be a mapping")
        seq_raw = data.get("sequence", [])
        if not isinstance(seq_raw, list):
            raise PlaybookError("'sequence' must be a list")

        try:
            meta = PlaybookMeta(**meta_raw)
        except ValueError as exc:
            raise PlaybookError(f"Invalid meta: {exc}") from exc
        try:
            defaults = PlaybookDefaults(**defaults_raw)
        except ValueError as exc:
            raise PlaybookError(f"Invalid defaults: {exc}") from exc

        stages: list[StageConfig] = []
        seen: set[SalesStage] = set()
        for idx, entry_raw in enumerate(seq_raw):
            if not isinstance(entry_raw, dict):
                raise PlaybookError(f"sequence[{idx}] must be a mapping")
            try:
                stage = StageConfig(**entry_raw)
            except ValueError as exc:
                raise PlaybookError(f"sequence[{idx}] invalid: {exc}") from exc
            if stage.stage in seen:
                raise PlaybookError(f"sequence[{idx}] duplicates stage {stage.stage.name}")
            seen.add(stage.stage)
            stages.append(stage)

        if not stages:
            raise PlaybookError("sequence must contain at least one stage")

        return cls(
            meta=meta,
            defaults=defaults,
            sequence=stages,
            source_path=source_path,
        )

    @property
    def first_stage(self) -> SalesStage:
        """The first stage in the sequence (call entrypoint)."""
        return self.sequence[0].stage

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"Playbook(name={self.meta.name!r}, stages={self.stage_names}, "
            f"source={self.source_path})"
        )

    # Helper for callers who want a plain dict snapshot (logging).
    def to_summary(self) -> dict[str, Any]:
        """Return a JSON-serialisable summary of this playbook."""
        return {
            "name": self.meta.name,
            "version": self.meta.version,
            "defaults": self.defaults.model_dump(),
            "stages": [
                {
                    "stage": s.stage.name,
                    "persona": s.persona,
                    "has_custom_prompt": s.custom_prompt is not None,
                    "success_regex": s.success_regex,
                    "goto_on_success": s.goto_on_success.name if s.goto_on_success else None,
                    "goto_on_fail": s.goto_on_fail.name if s.goto_on_fail else None,
                    "max_cycles": s.max_cycles,
                    "silent_until": s.silent_until,
                    "ambient_ratio": s.ambient_ratio,
                    "language_hint": s.language_hint,
                }
                for s in self.sequence
            ],
        }
