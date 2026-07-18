"""Strict contracts for the canonical append-only research evidence pack."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .claim_support_models import EvidenceLinkage, LocatorResolution, SemanticRelationship


MAX_PACK_SOURCES = 50
MAX_PACK_CLAIMS = 200
MAX_PACK_RELATIONSHIPS = MAX_PACK_SOURCES * MAX_PACK_CLAIMS


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


class _ImmutableStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


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


ResearchEvidencePackFactType = Literal[
    "money", "rate", "percentage", "duration", "count", "categorical", "text",
]


class ResearchEvidencePackQuery(_ImmutableStrictModel):
    project_id: str
    usage_scope: UsageScope

    @field_validator("project_id", mode="before")
    @classmethod
    def _validate_project_id(cls, value: object) -> str:
        return _uuid(value, "project_id")


class ResearchEvidencePackContext(_ImmutableStrictModel):
    context_revision_id: str
    context_sequence: int
    research_question: str
    project_limitations: tuple[str, ...] = ()
    unresolved_gaps: tuple[str, ...] = ()
    recorded_at: datetime

    @field_validator("context_revision_id", mode="before")
    @classmethod
    def _validate_context_revision_id(cls, value: object) -> str:
        return _uuid(value, "context_revision_id")

    @field_validator("context_sequence")
    @classmethod
    def _validate_context_sequence(cls, value: int) -> int:
        if value < 1:
            raise ValueError("context_sequence must be positive")
        return value

    @field_validator("research_question", mode="before")
    @classmethod
    def _validate_research_question(cls, value: object) -> str:
        return _text(value, "research_question", 2000)

    @field_validator("project_limitations", "unresolved_gaps", mode="before")
    @classmethod
    def _validate_context_arrays(cls, value: object, info) -> tuple[str, ...]:
        return _string_array(value, info.field_name, 10)

    @field_validator("recorded_at")
    @classmethod
    def _validate_context_recorded_at(cls, value: datetime) -> datetime:
        return _aware(value, "recorded_at")


class ResearchEvidencePackExplicitProbability(_ImmutableStrictModel):
    value: Decimal
    provided_by: ProbabilityProvidedBy
    provenance_reference: str
    provenance_note: str

    @field_validator("value")
    @classmethod
    def _validate_probability_value(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value < 0 or value > 1:
            raise ValueError("value must be finite and between 0 and 1")
        if max(0, -value.as_tuple().exponent) > 6:
            raise ValueError("value must have at most six decimal places")
        return value

    @field_validator("provenance_reference", mode="before")
    @classmethod
    def _validate_probability_reference(cls, value: object) -> str:
        return _text(value, "provenance_reference", 500)

    @field_validator("provenance_note", mode="before")
    @classmethod
    def _validate_probability_note(cls, value: object) -> str:
        return _text(value, "provenance_note", 1000)


class ResearchEvidencePackClaimAnnotation(_ImmutableStrictModel):
    annotation_revision_id: str
    claim_draft_id: str
    annotation_sequence: int
    epistemic_status: EpistemicStatus
    confidence_label: ConfidenceLabel
    decision_relevance: str
    supports_statement: str
    does_not_prove: str
    limitations: tuple[str, ...] = ()
    related_claim_draft_ids: tuple[str, ...] = ()
    explicit_probability: Optional[ResearchEvidencePackExplicitProbability] = None
    recorded_at: datetime

    @field_validator("annotation_revision_id", "claim_draft_id", mode="before")
    @classmethod
    def _validate_annotation_ids(cls, value: object, info) -> str:
        return _uuid(value, info.field_name)

    @field_validator("annotation_sequence")
    @classmethod
    def _validate_annotation_sequence(cls, value: int) -> int:
        if value < 1:
            raise ValueError("annotation_sequence must be positive")
        return value

    @field_validator("decision_relevance", mode="before")
    @classmethod
    def _validate_decision_relevance(cls, value: object) -> str:
        return _text(value, "decision_relevance", 1000)

    @field_validator("supports_statement", "does_not_prove", mode="before")
    @classmethod
    def _validate_annotation_statements(cls, value: object, info) -> str:
        return _text(value, info.field_name, 2000)

    @field_validator("limitations", mode="before")
    @classmethod
    def _validate_annotation_limitations(cls, value: object) -> tuple[str, ...]:
        return _string_array(value, "limitations", 10)

    @field_validator("related_claim_draft_ids", mode="before")
    @classmethod
    def _validate_annotation_related_claims(cls, value: object) -> tuple[str, ...]:
        return _uuid_array(value, "related_claim_draft_ids", 20)

    @field_validator("recorded_at")
    @classmethod
    def _validate_annotation_recorded_at(cls, value: datetime) -> datetime:
        return _aware(value, "recorded_at")

    @model_validator(mode="after")
    def _reject_annotation_self_reference(self):
        if self.claim_draft_id in self.related_claim_draft_ids:
            raise ValueError("related claims cannot include the annotated claim")
        return self


class ResearchEvidencePackAuthorizedClaim(_ImmutableStrictModel):
    claim_draft_id: str
    claim_text: str
    claim_category: str = ""
    annotation: ResearchEvidencePackClaimAnnotation

    @field_validator("claim_draft_id", mode="before")
    @classmethod
    def _validate_claim_id(cls, value: object) -> str:
        return _uuid(value, "claim_draft_id")

    @field_validator("claim_text", mode="before")
    @classmethod
    def _validate_claim_text(cls, value: object) -> str:
        return _text(value, "claim_text", 10000)

    @model_validator(mode="after")
    def _require_matching_annotation_claim(self):
        if self.annotation.claim_draft_id != self.claim_draft_id:
            raise ValueError("claim annotation must identify the aggregate claim")
        return self


class ResearchEvidencePackAuthorizedSource(_ImmutableStrictModel):
    source_snapshot_id: str
    source_blob_id: str
    source_metadata_revision_id: str
    source_kind: str = ""
    source_locator: str = ""
    captured_at: datetime
    canonical_source_locator: str = ""
    publisher: str = ""
    author: str = ""
    published_at: Optional[datetime] = None
    retrieved_at: Optional[datetime] = None
    citation_label: str = ""
    declared_quality_tier: str = ""
    declared_quality_rationale: str = ""

    @field_validator(
        "source_snapshot_id", "source_blob_id", "source_metadata_revision_id",
        mode="before",
    )
    @classmethod
    def _validate_source_ids(cls, value: object, info) -> str:
        return _uuid(value, info.field_name)

    @field_validator("captured_at")
    @classmethod
    def _validate_captured_at(cls, value: datetime) -> datetime:
        return _aware(value, "captured_at")

    @field_validator("published_at", "retrieved_at")
    @classmethod
    def _validate_optional_source_times(
        cls, value: Optional[datetime], info,
    ) -> Optional[datetime]:
        return None if value is None else _aware(value, info.field_name)


class ResearchEvidencePackAuthorizedEvidence(_ImmutableStrictModel):
    candidate_fact_revision_id: str
    source_snapshot_id: str
    fact_metadata_revision_id: str
    fact_type: ResearchEvidencePackFactType
    numeric_value: Optional[Decimal] = None
    text_value: Optional[str] = None
    unit: str = ""
    currency_code: Optional[str] = None
    as_of_date: Optional[date] = None
    numerator_context: Optional[str] = None
    denominator_context: Optional[str] = None
    percentage_basis: Optional[str] = None
    percentage_subtype: Optional[str] = None
    time_unit: Optional[str] = None
    counted_entity: Optional[str] = None
    stable_fact_key: str = ""
    source_char_range: Optional[str] = None
    citation_locator: str = ""

    @field_validator(
        "candidate_fact_revision_id", "source_snapshot_id",
        "fact_metadata_revision_id", mode="before",
    )
    @classmethod
    def _validate_evidence_ids(cls, value: object, info) -> str:
        return _uuid(value, info.field_name)

    @model_validator(mode="after")
    def _validate_fact_shape(self):
        numeric = self.fact_type in {"money", "rate", "percentage", "duration", "count"}
        if numeric != (self.numeric_value is not None):
            raise ValueError("numeric fact types require numeric_value only")
        if self.numeric_value is not None and not self.numeric_value.is_finite():
            raise ValueError("numeric_value must be finite")
        if self.fact_type in {"categorical", "text"} and not self.text_value:
            raise ValueError("categorical and text facts require text_value")
        if self.fact_type == "count" and self.numeric_value != self.numeric_value.to_integral_value():
            raise ValueError("count facts require an integral numeric_value")
        if self.fact_type == "money" and (
            self.currency_code is None
            or len(self.currency_code) != 3
            or self.as_of_date is None
        ):
            raise ValueError("money facts require currency_code and as_of_date")
        return self


class ResearchEvidencePackAuthorizedRelationship(_ImmutableStrictModel):
    authorization_decision_id: str
    claim_intake_item_id: str
    evidence_intake_item_id: str
    claim_support_assessment_id: str
    claim_draft_id: str
    candidate_fact_revision_id: str
    source_snapshot_id: str
    claim_annotation_revision_id: str
    claim_review_decision_id: str
    evidence_review_decision_id: str
    usage_scope: UsageScope
    authorization_sequence: int
    authorized_at: datetime
    locator_resolution: LocatorResolution
    evidence_linkage: EvidenceLinkage
    semantic_relationship: SemanticRelationship

    @field_validator(
        "authorization_decision_id", "claim_intake_item_id",
        "evidence_intake_item_id", "claim_support_assessment_id",
        "claim_draft_id", "candidate_fact_revision_id", "source_snapshot_id",
        "claim_annotation_revision_id", "claim_review_decision_id",
        "evidence_review_decision_id", mode="before",
    )
    @classmethod
    def _validate_relationship_ids(cls, value: object, info) -> str:
        return _uuid(value, info.field_name)

    @field_validator("authorization_sequence")
    @classmethod
    def _validate_authorization_sequence(cls, value: int) -> int:
        if value < 1:
            raise ValueError("authorization_sequence must be positive")
        return value

    @field_validator("authorized_at")
    @classmethod
    def _validate_authorized_at(cls, value: datetime) -> datetime:
        return _aware(value, "authorized_at")


class ResearchEvidencePackCounts(_ImmutableStrictModel):
    source_count: int = Field(default=0, ge=0, le=MAX_PACK_SOURCES)
    claim_count: int = Field(default=0, ge=0, le=MAX_PACK_CLAIMS)
    evidence_count: int = Field(default=0, ge=0, le=MAX_PACK_RELATIONSHIPS)
    relationship_count: int = Field(default=0, ge=0, le=MAX_PACK_RELATIONSHIPS)


class ResearchEvidencePackAggregate(_ImmutableStrictModel):
    project_id: str
    usage_scope: UsageScope
    context: Optional[ResearchEvidencePackContext] = None
    claims: tuple[ResearchEvidencePackAuthorizedClaim, ...] = ()
    sources: tuple[ResearchEvidencePackAuthorizedSource, ...] = ()
    evidence: tuple[ResearchEvidencePackAuthorizedEvidence, ...] = ()
    relationships: tuple[ResearchEvidencePackAuthorizedRelationship, ...] = ()
    counts: ResearchEvidencePackCounts = Field(default_factory=ResearchEvidencePackCounts)

    @field_validator("project_id", mode="before")
    @classmethod
    def _validate_aggregate_project_id(cls, value: object) -> str:
        return _uuid(value, "project_id")

    @model_validator(mode="after")
    def _validate_bounded_consistent_aggregate(self):
        actual = (
            len(self.sources), len(self.claims), len(self.evidence),
            len(self.relationships),
        )
        declared = (
            self.counts.source_count, self.counts.claim_count,
            self.counts.evidence_count, self.counts.relationship_count,
        )
        if actual != declared:
            raise ValueError("pack counts must match aggregate collections")

        if not self.relationships and (
            self.context is not None or self.claims or self.sources or self.evidence
        ):
            raise ValueError("an empty pack must contain only empty aggregate state")

        claim_ids = tuple(item.claim_draft_id for item in self.claims)
        source_ids = tuple(item.source_snapshot_id for item in self.sources)
        evidence_ids = tuple(item.candidate_fact_revision_id for item in self.evidence)
        if claim_ids != tuple(sorted(claim_ids)) or len(set(claim_ids)) != len(claim_ids):
            raise ValueError("claims must be unique and ordered by claim_draft_id")
        if source_ids != tuple(sorted(source_ids)) or len(set(source_ids)) != len(source_ids):
            raise ValueError("sources must be unique and ordered by source_snapshot_id")
        evidence_order = tuple(
            (item.source_snapshot_id, item.candidate_fact_revision_id)
            for item in self.evidence
        )
        if evidence_order != tuple(sorted(evidence_order)) or len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("evidence must be unique and canonically ordered")

        relationship_order = tuple(
            (
                item.claim_draft_id, item.source_snapshot_id,
                item.candidate_fact_revision_id, item.authorization_sequence,
                item.authorization_decision_id,
            )
            for item in self.relationships
        )
        canonical_relationships = tuple(
            (item.claim_draft_id, item.candidate_fact_revision_id)
            for item in self.relationships
        )
        if relationship_order != tuple(sorted(relationship_order)):
            raise ValueError("relationships must use canonical ordering")
        if len(set(canonical_relationships)) != len(canonical_relationships):
            raise ValueError("canonical claim/evidence relationships must be unique")

        claims_by_id = {item.claim_draft_id: item for item in self.claims}
        source_id_set = set(source_ids)
        evidence_by_id = {
            item.candidate_fact_revision_id: item for item in self.evidence
        }
        for item in self.relationships:
            if item.usage_scope != self.usage_scope:
                raise ValueError("relationship usage scope must match the pack scope")
            claim = claims_by_id.get(item.claim_draft_id)
            if claim is None:
                raise ValueError("relationship references an absent claim")
            if item.source_snapshot_id not in source_id_set:
                raise ValueError("relationship references an absent source")
            evidence_item = evidence_by_id.get(item.candidate_fact_revision_id)
            if evidence_item is None:
                raise ValueError("relationship references absent evidence")
            if evidence_item.source_snapshot_id != item.source_snapshot_id:
                raise ValueError("relationship evidence must use its canonical source")
            if (
                claim.annotation.annotation_revision_id
                != item.claim_annotation_revision_id
            ):
                raise ValueError("relationship references a non-current pack annotation")
        return self
