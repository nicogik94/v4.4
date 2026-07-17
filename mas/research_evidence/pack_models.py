"""Strict contracts for the canonical append-only research evidence pack."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class EpistemicStatus(str, Enum):
    REPORTED_FACT = "reported_fact"
    OBSERVATION = "observation"
    ESTIMATE = "estimate"
    INFERENCE = "inference"
    ASSUMPTION = "assumption"
    HYPOTHESIS = "hypothesis"


class ConfidenceLabel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class ProbabilityProvidedBy(str, Enum):
    SOURCE = "source"
    OPERATOR = "operator"


class UsageScope(str, Enum):
    INTERNAL_ANALYSIS = "internal_analysis"
    OPERATOR_DOSSIER = "operator_dossier"
    CLIENT_REPORT = "client_report"


class UsageAuthorizationDecisionType(str, Enum):
    AUTHORIZED = "authorized"
    REVOKED = "revoked"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _uuid(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a UUID string")
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"{field_name} must be a UUID string") from exc


def _text(value: object, field_name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    value = value.strip()
    if len(value) > maximum:
        raise ValueError(f"{field_name} must be at most {maximum} characters")
    return value


def _optional_text(value: object, field_name: str, maximum: int) -> Optional[str]:
    if value is None:
        return None
    return _text(value, field_name, maximum)


def _string_array(value: object, field_name: str, maximum_items: int) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be an array")
    if len(value) > maximum_items:
        raise ValueError(f"{field_name} must have at most {maximum_items} items")
    cleaned = tuple(_text(item, field_name, 500) for item in value)
    if len(set(cleaned)) != len(cleaned):
        raise ValueError(f"{field_name} items must be distinct")
    return cleaned


def _uuid_array(value: object, field_name: str, maximum_items: int) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be an array")
    if len(value) > maximum_items:
        raise ValueError(f"{field_name} must have at most {maximum_items} items")
    cleaned = tuple(_uuid(item, field_name) for item in value)
    if len(set(cleaned)) != len(cleaned):
        raise ValueError(f"{field_name} items must be distinct")
    return cleaned


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


class ResearchEvidenceExplicitProbability(_StrictModel):
    value: Decimal
    provided_by: ProbabilityProvidedBy
    provenance_reference: str
    provenance_note: str

    @field_validator("value")
    @classmethod
    def _validate_value(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value < 0 or value > 1:
            raise ValueError("value must be finite and between 0 and 1")
        if max(0, -value.as_tuple().exponent) > 6:
            raise ValueError("value must have at most six decimal places")
        return value

    @field_validator("provenance_reference")
    @classmethod
    def _validate_reference(cls, value: str) -> str:
        return _text(value, "provenance_reference", 500)

    @field_validator("provenance_note")
    @classmethod
    def _validate_note(cls, value: str) -> str:
        return _text(value, "provenance_note", 1000)


class ResearchEvidenceProjectContextRevisionCreate(_StrictModel):
    project_id: str
    request_id: str
    research_question: str
    project_limitations: tuple[str, ...] = ()
    unresolved_gaps: tuple[str, ...] = ()
    actor: str

    @field_validator("project_id", mode="before")
    @classmethod
    def _validate_project(cls, value: object) -> str:
        return _uuid(value, "project_id")

    @field_validator("request_id", mode="before")
    @classmethod
    def _validate_request(cls, value: object) -> str:
        return _text(value, "request_id", 128)

    @field_validator("research_question", mode="before")
    @classmethod
    def _validate_question(cls, value: object) -> str:
        return _text(value, "research_question", 2000)

    @field_validator("project_limitations", "unresolved_gaps", mode="before")
    @classmethod
    def _validate_arrays(cls, value: object, info) -> tuple[str, ...]:
        return _string_array(value, info.field_name, 10)

    @field_validator("actor", mode="before")
    @classmethod
    def _validate_actor(cls, value: object) -> str:
        return _text(value, "actor", 200)


class ResearchEvidenceProjectContextRevisionRecord(ResearchEvidenceProjectContextRevisionCreate):
    id: str
    context_sequence: int
    supersedes_context_revision_id: Optional[str] = None
    recorded_at: datetime

    @field_validator("id", "supersedes_context_revision_id", mode="before")
    @classmethod
    def _validate_ids(cls, value: object, info):
        return None if value is None else _uuid(value, info.field_name)

    @field_validator("context_sequence")
    @classmethod
    def _validate_sequence(cls, value: int) -> int:
        if value < 1:
            raise ValueError("context_sequence must be positive")
        return value

    @field_validator("recorded_at")
    @classmethod
    def _validate_recorded_at(cls, value: datetime) -> datetime:
        return _aware(value, "recorded_at")


class ResearchEvidenceClaimAnnotationRevisionCreate(_StrictModel):
    project_id: str
    claim_draft_id: str
    request_id: str
    epistemic_status: EpistemicStatus
    confidence_label: ConfidenceLabel
    decision_relevance: str
    supports_statement: str
    does_not_prove: str
    limitations: tuple[str, ...] = ()
    related_claim_draft_ids: tuple[str, ...] = ()
    operator_notes: Optional[str] = None
    explicit_probability: Optional[ResearchEvidenceExplicitProbability] = None
    actor: str

    @field_validator("project_id", "claim_draft_id", mode="before")
    @classmethod
    def _validate_ids(cls, value: object, info) -> str:
        return _uuid(value, info.field_name)

    @field_validator("request_id", mode="before")
    @classmethod
    def _validate_request(cls, value: object) -> str:
        return _text(value, "request_id", 128)

    @field_validator("decision_relevance", mode="before")
    @classmethod
    def _validate_relevance(cls, value: object) -> str:
        return _text(value, "decision_relevance", 1000)

    @field_validator("supports_statement", "does_not_prove", mode="before")
    @classmethod
    def _validate_statements(cls, value: object, info) -> str:
        return _text(value, info.field_name, 2000)

    @field_validator("limitations", mode="before")
    @classmethod
    def _validate_limitations(cls, value: object) -> tuple[str, ...]:
        return _string_array(value, "limitations", 10)

    @field_validator("related_claim_draft_ids", mode="before")
    @classmethod
    def _validate_related(cls, value: object) -> tuple[str, ...]:
        return _uuid_array(value, "related_claim_draft_ids", 20)

    @field_validator("operator_notes", mode="before")
    @classmethod
    def _validate_operator_notes(cls, value: object) -> Optional[str]:
        return _optional_text(value, "operator_notes", 2000)

    @field_validator("actor", mode="before")
    @classmethod
    def _validate_actor(cls, value: object) -> str:
        return _text(value, "actor", 200)

    @model_validator(mode="after")
    def _no_self_reference(self):
        if self.claim_draft_id in self.related_claim_draft_ids:
            raise ValueError("related claims cannot include the annotated claim")
        return self


class ResearchEvidenceClaimAnnotationRevisionRecord(ResearchEvidenceClaimAnnotationRevisionCreate):
    id: str
    annotation_sequence: int
    supersedes_annotation_revision_id: Optional[str] = None
    recorded_at: datetime

    @field_validator("id", "supersedes_annotation_revision_id", mode="before")
    @classmethod
    def _validate_record_ids(cls, value: object, info):
        return None if value is None else _uuid(value, info.field_name)

    @field_validator("annotation_sequence")
    @classmethod
    def _validate_sequence(cls, value: int) -> int:
        if value < 1:
            raise ValueError("annotation_sequence must be positive")
        return value

    @field_validator("recorded_at")
    @classmethod
    def _validate_recorded_at(cls, value: datetime) -> datetime:
        return _aware(value, "recorded_at")


class ResearchEvidenceUsageAuthorizationDecisionCreate(_StrictModel):
    project_id: str
    claim_intake_item_id: str
    evidence_intake_item_id: str
    usage_scope: UsageScope
    decision: UsageAuthorizationDecisionType
    reason: str
    actor: str
    request_id: str

    @field_validator("project_id", "claim_intake_item_id", "evidence_intake_item_id", mode="before")
    @classmethod
    def _validate_ids(cls, value: object, info) -> str:
        return _uuid(value, info.field_name)

    @field_validator("reason", mode="before")
    @classmethod
    def _validate_reason(cls, value: object) -> str:
        return _text(value, "reason", 1000)

    @field_validator("actor", mode="before")
    @classmethod
    def _validate_actor(cls, value: object) -> str:
        return _text(value, "actor", 200)

    @field_validator("request_id", mode="before")
    @classmethod
    def _validate_request(cls, value: object) -> str:
        return _text(value, "request_id", 128)


class ResearchEvidenceUsageAuthorizationDecisionRecord(ResearchEvidenceUsageAuthorizationDecisionCreate):
    id: str
    claim_support_assessment_id: str
    claim_draft_id: str
    claim_annotation_revision_id: str
    claim_review_decision_id: str
    evidence_review_decision_id: str
    decision_sequence: int
    supersedes_decision_id: Optional[str] = None
    recorded_at: datetime

    @field_validator(
        "id", "claim_support_assessment_id", "claim_draft_id",
        "claim_annotation_revision_id", "claim_review_decision_id",
        "evidence_review_decision_id", "supersedes_decision_id", mode="before",
    )
    @classmethod
    def _validate_record_ids(cls, value: object, info):
        return None if value is None else _uuid(value, info.field_name)

    @field_validator("decision_sequence")
    @classmethod
    def _validate_sequence(cls, value: int) -> int:
        if value < 1:
            raise ValueError("decision_sequence must be positive")
        return value

    @field_validator("recorded_at")
    @classmethod
    def _validate_recorded_at(cls, value: datetime) -> datetime:
        return _aware(value, "recorded_at")
