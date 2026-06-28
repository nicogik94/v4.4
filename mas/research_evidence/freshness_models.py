"""Strict models for R1.4 item freshness and drift assessments."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    JsonValue,
    field_validator,
    model_validator,
)


DriftStatus = Literal[
    "not_assessed",
    "no_material_drift",
    "material_drift",
    "indeterminate",
]
FreshnessStatus = Literal["fresh", "stale", "unknown", "not_applicable"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _nonblank(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value.strip()


def require_aware_datetime(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value


class ResearchEvidenceIntakeItemFreshnessAssessmentCreate(_StrictModel):
    project_id: str
    research_evidence_intake_item_id: str
    request_id: str
    policy_identifier: str
    policy_version: str
    policy_parameters_json: dict[str, JsonValue]
    policy_fingerprint: str = ""
    evaluator_version: str
    basis_timestamp: datetime
    fresh_through: datetime
    comparison_research_evidence_intake_item_id: Optional[str] = None
    drift_status: DriftStatus
    drift_reason: str
    assessed_by: str

    @field_validator(
        "request_id",
        "policy_identifier",
        "policy_version",
        "evaluator_version",
        "drift_reason",
        "assessed_by",
    )
    @classmethod
    def _validate_nonblank(cls, value: str, info) -> str:
        return _nonblank(value, field_name=info.field_name)

    @field_validator("policy_fingerprint")
    @classmethod
    def _trim_fingerprint(cls, value: str) -> str:
        return value.strip()

    @field_validator("basis_timestamp", "fresh_through")
    @classmethod
    def _validate_aware_datetime(cls, value: datetime, info) -> datetime:
        return require_aware_datetime(value, field_name=info.field_name)

    @model_validator(mode="after")
    def _validate_provenance_and_window(self):
        if not self.policy_parameters_json and not self.policy_fingerprint:
            raise ValueError(
                "policy parameters or policy fingerprint must be provided"
            )
        if self.fresh_through < self.basis_timestamp:
            raise ValueError("fresh_through must not precede basis_timestamp")
        if (
            self.comparison_research_evidence_intake_item_id
            == self.research_evidence_intake_item_id
        ):
            raise ValueError("comparison intake item must differ from target item")
        return self


class ResearchEvidenceIntakeItemFreshnessAssessmentRecord(
    ResearchEvidenceIntakeItemFreshnessAssessmentCreate
):
    id: str
    assessment_sequence: int
    supersedes_assessment_id: Optional[str] = None
    source_snapshot_id: str
    source_blob_id: str
    candidate_fact_revision_id: str
    fact_metadata_revision_id: str
    linked_hash_algorithm: str
    linked_content_hash: str
    comparison_source_snapshot_id: Optional[str] = None
    comparison_source_blob_id: Optional[str] = None
    comparison_candidate_fact_revision_id: Optional[str] = None
    comparison_fact_metadata_revision_id: Optional[str] = None
    comparison_hash_algorithm: Optional[str] = None
    comparison_content_hash: Optional[str] = None
    content_change_detected: Optional[bool] = None
    assessed_at: datetime
