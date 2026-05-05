"""Internal Bayesian scenario adapter models.

These models are intentionally internal-only. They do not define an API,
renderer, persistence contract, workflow route, or buyer-facing probability
surface.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ScenarioPriorSource(str, Enum):
    HYPOTHESIS = "hypothesis"
    PRIOR_SNAPSHOT = "prior_snapshot"
    CALIBRATION_SNAPSHOT = "calibration_snapshot"
    OPERATOR = "operator"
    DEFAULT = "default"


class ScenarioUpdateMethod(str, Enum):
    BETA_BINOMIAL = "beta_binomial"
    LOG_ODDS = "log_odds"
    NONE = "none"


class ScenarioDisplayMode(str, Enum):
    INTERNAL_ONLY = "internal_only"
    VERBAL_ONLY = "verbal_only"
    INTERNAL_NUMERIC_ALLOWED = "internal_numeric_allowed"


ScenarioEvidenceStance = Literal["supports", "refutes", "neutral", "mixed", "unknown"]
ScenarioUpdateStatus = Literal["updated", "contradiction"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ScenarioFalsifier(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observable: str
    threshold: str
    time_window: str

    @field_validator("observable", "threshold", "time_window")
    @classmethod
    def _require_non_empty(cls, value: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            raise ValueError("falsifier fields must be non-empty")
        return cleaned


class ScenarioEvidenceObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    stance: ScenarioEvidenceStance
    source_record_id: str | None = None
    source_ref: str | None = None
    provenance_id: str | None = None
    locator: str | None = None
    captured_at: datetime | None = None

    @field_validator("evidence_id")
    @classmethod
    def _require_evidence_id(cls, value: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            raise ValueError("evidence_id must be non-empty")
        return cleaned

    @field_validator("source_record_id", "source_ref", "provenance_id", "locator", mode="before")
    @classmethod
    def _empty_string_to_none(cls, value: object) -> object:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None


class BayesianScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    decision_ref: str | None = None
    hypothesis_ref: str
    prior_alpha: float
    prior_beta: float
    prior_source: ScenarioPriorSource
    evidence_refs: list[str] = Field(default_factory=list)
    update_method: ScenarioUpdateMethod
    posterior_alpha: float | None = None
    posterior_beta: float | None = None
    posterior_mean: float | None = None
    posterior_ci_low_90: float | None = None
    posterior_ci_high_90: float | None = None
    calibrated_label: str = ""
    evidence_count_n: int = 0
    display_mode: ScenarioDisplayMode = ScenarioDisplayMode.INTERNAL_ONLY
    assumptions: list[str] = Field(default_factory=list)
    falsifier: ScenarioFalsifier
    uncertainty_note: str
    reviewer_note: str | None = None
    reviewer_signoff_ts: datetime | None = None
    created_ts: datetime = Field(default_factory=_utc_now)
    updated_ts: datetime = Field(default_factory=_utc_now)

    @field_validator("scenario_id", "hypothesis_ref", "uncertainty_note")
    @classmethod
    def _require_non_empty_text(cls, value: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            raise ValueError("field must be non-empty")
        return cleaned

    @field_validator("prior_alpha", "prior_beta")
    @classmethod
    def _require_positive_prior(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("prior alpha and beta must be positive")
        return value

    @field_validator("posterior_alpha", "posterior_beta")
    @classmethod
    def _require_positive_posterior_if_present(cls, value: float | None) -> float | None:
        if value is not None and value <= 0:
            raise ValueError("posterior alpha and beta must be positive when present")
        return value

    @field_validator("posterior_mean", "posterior_ci_low_90", "posterior_ci_high_90")
    @classmethod
    def _require_probability_if_present(cls, value: float | None) -> float | None:
        if value is not None and not 0.0 <= value <= 1.0:
            raise ValueError("posterior probability fields must be between 0 and 1")
        return value

    @field_validator("evidence_count_n")
    @classmethod
    def _require_non_negative_count(cls, value: int) -> int:
        if value < 0:
            raise ValueError("evidence_count_n must be non-negative")
        return value

    @field_validator("assumptions", mode="before")
    @classmethod
    def _require_assumptions_list(cls, value: object) -> object:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("assumptions must be a list")
        return value

    @model_validator(mode="after")
    def _validate_internal_display_and_assumptions(self) -> BayesianScenario:
        if (
            self.display_mode == ScenarioDisplayMode.INTERNAL_NUMERIC_ALLOWED
            and self.evidence_count_n < 10
        ):
            raise ValueError("internal numeric display requires at least 10 evidence observations")
        if not self.assumptions and not self.uncertainty_note.strip():
            raise ValueError("empty assumptions require an uncertainty_note explaining the gap")
        if (
            self.posterior_ci_low_90 is not None
            and self.posterior_ci_high_90 is not None
            and self.posterior_ci_low_90 > self.posterior_ci_high_90
        ):
            raise ValueError("posterior_ci_low_90 cannot exceed posterior_ci_high_90")
        return self


class ScenarioUpdateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ScenarioUpdateStatus
    scenario: BayesianScenario
    evidence_refs: list[str] = Field(default_factory=list)
    contradiction_reason: str = ""
    contradictory_evidence_refs: list[str] = Field(default_factory=list)
