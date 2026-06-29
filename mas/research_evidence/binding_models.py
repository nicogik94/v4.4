"""Strict models for R1.6 consumer-input evidence binding evaluations.

Bindings preserve the independently evaluated source contracts used by one
future consumer input.  A binding disposition is not truth, approval, citation
readiness, execution authorization, or a cross-consumer readiness signal.
"""
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

from .claim_support_models import (
    EvidenceLinkage,
    LocatorResolution,
    SemanticRelationship,
)
from .freshness_models import DriftStatus, FreshnessStatus, require_aware_datetime


ConsumerContract = Literal[
    "deterministic_calculation",
    "scenario_input",
    "report_evidence_register",
]
ConsumerDisposition = Literal[
    "meets_contract",
    "qualified",
    "does_not_meet_contract",
    "indeterminate",
]
BindingReviewStatus = Literal[
    "not_assessed",
    "approved",
    "rejected",
    "needs_revision",
    "withdrawn",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _nonblank(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value.strip()


class ResearchEvidenceConsumerInputBindingCreate(_StrictModel):
    project_id: str
    consumer_contract: ConsumerContract
    consumer_contract_version: str
    binding_set_id: str
    input_key: str
    request_id: str
    evidence_intake_item_id: str
    approved_calculation_input_id: Optional[str] = None
    observation_identity_version: Optional[str] = None
    observation_identity_fingerprint: Optional[str] = None
    claim_intake_item_id: Optional[str] = None
    claim_support_assessment_id: Optional[str] = None
    policy_identifier: str
    policy_version: str
    policy_parameters_json: dict[str, JsonValue]
    policy_fingerprint: str = ""
    evaluator_version: str
    freshness_as_of: datetime
    consumer_disposition: ConsumerDisposition
    disposition_reasons: tuple[str, ...]
    evaluated_by: str

    @field_validator(
        "consumer_contract_version",
        "binding_set_id",
        "input_key",
        "request_id",
        "policy_identifier",
        "policy_version",
        "evaluator_version",
        "evaluated_by",
    )
    @classmethod
    def _validate_nonblank(cls, value: str, info) -> str:
        return _nonblank(value, field_name=info.field_name)

    @field_validator("policy_fingerprint")
    @classmethod
    def _trim_policy_fingerprint(cls, value: str) -> str:
        return value.strip()

    @field_validator("freshness_as_of")
    @classmethod
    def _validate_as_of(cls, value: datetime) -> datetime:
        return require_aware_datetime(value, field_name="freshness_as_of")

    @field_validator("observation_identity_version")
    @classmethod
    def _trim_observation_version(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else _nonblank(
            value, field_name="observation_identity_version"
        )

    @field_validator("observation_identity_fingerprint")
    @classmethod
    def _validate_observation_fingerprint(
        cls, value: Optional[str]
    ) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip().lower()
        if len(cleaned) != 64 or any(c not in "0123456789abcdef" for c in cleaned):
            raise ValueError(
                "observation_identity_fingerprint must be 64 lowercase hex characters"
            )
        return cleaned

    @field_validator("disposition_reasons")
    @classmethod
    def _validate_disposition_reasons(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        if not value:
            raise ValueError("disposition_reasons must contain at least one reason")
        return tuple(
            _nonblank(reason, field_name="disposition_reasons")
            for reason in value
        )

    @model_validator(mode="after")
    def _validate_policy_and_consumer_shape(self):
        if not self.policy_parameters_json and not self.policy_fingerprint:
            raise ValueError(
                "policy parameters or policy fingerprint must be provided"
            )

        pair_fields = (
            self.claim_intake_item_id,
            self.claim_support_assessment_id,
        )
        if any(value is None for value in pair_fields) and any(
            value is not None for value in pair_fields
        ):
            raise ValueError(
                "claim and claim-support assessment references must be all-or-none"
            )

        if self.consumer_contract == "deterministic_calculation":
            if self.approved_calculation_input_id is None:
                raise ValueError(
                    "calculation binding requires approved_calculation_input_id"
                )
            if (
                self.observation_identity_version is not None
                or self.observation_identity_fingerprint is not None
                or self.claim_intake_item_id is not None
            ):
                raise ValueError(
                    "calculation binding cannot carry scenario or claim-pair fields"
                )
        elif self.consumer_contract == "scenario_input":
            if self.approved_calculation_input_id is not None:
                raise ValueError(
                    "scenario binding cannot carry approved_calculation_input_id"
                )
            if (
                self.observation_identity_version is None
                or self.observation_identity_fingerprint is None
            ):
                raise ValueError(
                    "scenario binding requires a versioned observation identity"
                )
        else:
            if self.approved_calculation_input_id is not None:
                raise ValueError(
                    "report binding cannot carry approved_calculation_input_id"
                )
            if (
                self.observation_identity_version is not None
                or self.observation_identity_fingerprint is not None
            ):
                raise ValueError(
                    "report binding cannot carry scenario observation identity"
                )
        return self


class ResearchEvidenceConsumerInputBindingRecord(
    ResearchEvidenceConsumerInputBindingCreate
):
    id: str
    calculation_kind: Optional[str] = None
    source_snapshot_id: str
    source_blob_id: str
    source_metadata_revision_id: str
    candidate_fact_revision_id: str
    fact_metadata_revision_id: str
    availability_status: bool
    retention_basis: tuple[dict[str, JsonValue], ...]
    lineage_is_current: bool
    lineage_basis: tuple[dict[str, JsonValue], ...]
    review_decision_id: Optional[str] = None
    review_decision_sequence: Optional[int] = None
    review_status: BindingReviewStatus
    freshness_assessment_id: Optional[str] = None
    freshness_assessment_sequence: Optional[int] = None
    fresh_through: Optional[datetime] = None
    freshness_status: FreshnessStatus
    drift_status: DriftStatus
    locator_resolution: Optional[LocatorResolution] = None
    evidence_linkage: Optional[EvidenceLinkage] = None
    semantic_relationship: Optional[SemanticRelationship] = None
    binding_sequence: int
    supersedes_binding_id: Optional[str] = None
    evaluated_at: datetime
