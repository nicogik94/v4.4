"""Strict immutable contracts for explicitly authorized presentation projections.

A presentation projection is a deterministic, omission-only reshaping of one
validated R2.0A-2 ``ResearchEvidencePackAggregate`` for the pack's own
``UsageScope``. Projections never change pack membership, never transform
retained values, and never add derived content. The frozen disclosure policy
decides field visibility per scope with DEFAULT DENY semantics, and every
projection binds the policy identity plus a recomputable content fingerprint.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import presentation_projection_policy as _policy
from .claim_support_models import EvidenceLinkage, LocatorResolution, SemanticRelationship
from .pack_models import (
    ConfidenceLabel,
    EpistemicStatus,
    ProbabilityProvidedBy,
    ResearchEvidencePackCounts,
    ResearchEvidencePackFactType,
    UsageScope,
    _aware,
    _string_array,
    _text,
    _uuid,
    _uuid_array,
)


PRESENTATION_PROJECTION_DESCRIPTOR_VERSION = (
    "research-evidence-presentation-projection-v1"
)


class _ImmutableStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ResearchEvidencePresentationProbability(_ImmutableStrictModel):
    value: Decimal
    provided_by: ProbabilityProvidedBy
    provenance_reference: Optional[str] = None
    provenance_note: Optional[str] = None

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
    def _validate_probability_reference(cls, value: object) -> Optional[str]:
        return None if value is None else _text(value, "provenance_reference", 500)

    @field_validator("provenance_note", mode="before")
    @classmethod
    def _validate_probability_note(cls, value: object) -> Optional[str]:
        return None if value is None else _text(value, "provenance_note", 1000)


class ResearchEvidencePresentationContext(_ImmutableStrictModel):
    research_question: str
    project_limitations: tuple[str, ...]
    unresolved_gaps: tuple[str, ...]
    recorded_at: Optional[datetime] = None
    context_revision_id: Optional[str] = None
    context_sequence: Optional[int] = None

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
    def _validate_context_recorded_at(
        cls, value: Optional[datetime],
    ) -> Optional[datetime]:
        return None if value is None else _aware(value, "recorded_at")

    @field_validator("context_revision_id", mode="before")
    @classmethod
    def _validate_context_revision_id(cls, value: object) -> Optional[str]:
        return None if value is None else _uuid(value, "context_revision_id")

    @field_validator("context_sequence")
    @classmethod
    def _validate_context_sequence(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value < 1:
            raise ValueError("context_sequence must be positive")
        return value


class ResearchEvidencePresentationClaim(_ImmutableStrictModel):
    claim_draft_id: str
    claim_text: str
    claim_category: str
    epistemic_status: EpistemicStatus
    confidence_label: ConfidenceLabel
    supports_statement: str
    does_not_prove: str
    limitations: tuple[str, ...]
    decision_relevance: Optional[str] = None
    related_claim_draft_ids: Optional[tuple[str, ...]] = None
    explicit_probability: Optional[ResearchEvidencePresentationProbability] = None
    annotation_revision_id: Optional[str] = None
    annotation_sequence: Optional[int] = None
    annotation_recorded_at: Optional[datetime] = None

    @field_validator("claim_draft_id", mode="before")
    @classmethod
    def _validate_claim_id(cls, value: object) -> str:
        return _uuid(value, "claim_draft_id")

    @field_validator("claim_text", mode="before")
    @classmethod
    def _validate_claim_text(cls, value: object) -> str:
        return _text(value, "claim_text", 10000)

    @field_validator("supports_statement", "does_not_prove", mode="before")
    @classmethod
    def _validate_claim_statements(cls, value: object, info) -> str:
        return _text(value, info.field_name, 2000)

    @field_validator("limitations", mode="before")
    @classmethod
    def _validate_claim_limitations(cls, value: object) -> tuple[str, ...]:
        return _string_array(value, "limitations", 10)

    @field_validator("decision_relevance", mode="before")
    @classmethod
    def _validate_decision_relevance(cls, value: object) -> Optional[str]:
        return None if value is None else _text(value, "decision_relevance", 1000)

    @field_validator("related_claim_draft_ids", mode="before")
    @classmethod
    def _validate_related_claims(cls, value: object) -> Optional[tuple[str, ...]]:
        if value is None:
            return None
        return _uuid_array(value, "related_claim_draft_ids", 20)

    @field_validator("annotation_revision_id", mode="before")
    @classmethod
    def _validate_annotation_revision_id(cls, value: object) -> Optional[str]:
        return None if value is None else _uuid(value, "annotation_revision_id")

    @field_validator("annotation_sequence")
    @classmethod
    def _validate_annotation_sequence(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value < 1:
            raise ValueError("annotation_sequence must be positive")
        return value

    @field_validator("annotation_recorded_at")
    @classmethod
    def _validate_annotation_recorded_at(
        cls, value: Optional[datetime],
    ) -> Optional[datetime]:
        return None if value is None else _aware(value, "annotation_recorded_at")

    @model_validator(mode="after")
    def _reject_claim_self_reference(self):
        if (
            self.related_claim_draft_ids is not None
            and self.claim_draft_id in self.related_claim_draft_ids
        ):
            raise ValueError("related claims cannot include the projected claim")
        return self


class ResearchEvidencePresentationSource(_ImmutableStrictModel):
    source_snapshot_id: str
    canonical_source_locator: str
    publisher: str
    author: str
    citation_label: str
    declared_quality_tier: str
    published_at: Optional[datetime] = None
    retrieved_at: Optional[datetime] = None
    source_kind: Optional[str] = None
    captured_at: Optional[datetime] = None
    declared_quality_rationale: Optional[str] = None
    source_locator: Optional[str] = None
    source_blob_id: Optional[str] = None
    source_metadata_revision_id: Optional[str] = None

    @field_validator("source_snapshot_id", mode="before")
    @classmethod
    def _validate_source_snapshot_id(cls, value: object) -> str:
        return _uuid(value, "source_snapshot_id")

    @field_validator("source_blob_id", "source_metadata_revision_id", mode="before")
    @classmethod
    def _validate_optional_source_ids(cls, value: object, info) -> Optional[str]:
        return None if value is None else _uuid(value, info.field_name)

    @field_validator("published_at", "retrieved_at", "captured_at")
    @classmethod
    def _validate_optional_source_times(
        cls, value: Optional[datetime], info,
    ) -> Optional[datetime]:
        return None if value is None else _aware(value, info.field_name)


class ResearchEvidencePresentationEvidence(_ImmutableStrictModel):
    candidate_fact_revision_id: str
    source_snapshot_id: str
    fact_type: ResearchEvidencePackFactType
    unit: str
    citation_locator: str
    numeric_value: Optional[Decimal] = None
    text_value: Optional[str] = None
    currency_code: Optional[str] = None
    as_of_date: Optional[date] = None
    numerator_context: Optional[str] = None
    denominator_context: Optional[str] = None
    percentage_basis: Optional[str] = None
    percentage_subtype: Optional[str] = None
    time_unit: Optional[str] = None
    counted_entity: Optional[str] = None
    fact_metadata_revision_id: Optional[str] = None
    stable_fact_key: Optional[str] = None
    source_char_range: Optional[str] = None

    @field_validator("candidate_fact_revision_id", "source_snapshot_id", mode="before")
    @classmethod
    def _validate_evidence_ids(cls, value: object, info) -> str:
        return _uuid(value, info.field_name)

    @field_validator("fact_metadata_revision_id", mode="before")
    @classmethod
    def _validate_fact_metadata_revision_id(cls, value: object) -> Optional[str]:
        return None if value is None else _uuid(value, "fact_metadata_revision_id")

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


class ResearchEvidencePresentationRelationship(_ImmutableStrictModel):
    claim_draft_id: str
    candidate_fact_revision_id: str
    source_snapshot_id: str
    semantic_relationship: SemanticRelationship
    authorized_at: Optional[datetime] = None
    usage_scope: Optional[UsageScope] = None
    authorization_sequence: Optional[int] = None
    authorization_decision_id: Optional[str] = None
    claim_intake_item_id: Optional[str] = None
    evidence_intake_item_id: Optional[str] = None
    claim_support_assessment_id: Optional[str] = None
    claim_annotation_revision_id: Optional[str] = None
    claim_review_decision_id: Optional[str] = None
    evidence_review_decision_id: Optional[str] = None
    locator_resolution: Optional[LocatorResolution] = None
    evidence_linkage: Optional[EvidenceLinkage] = None

    @field_validator(
        "claim_draft_id", "candidate_fact_revision_id", "source_snapshot_id",
        mode="before",
    )
    @classmethod
    def _validate_relationship_ids(cls, value: object, info) -> str:
        return _uuid(value, info.field_name)

    @field_validator(
        "authorization_decision_id", "claim_intake_item_id",
        "evidence_intake_item_id", "claim_support_assessment_id",
        "claim_annotation_revision_id", "claim_review_decision_id",
        "evidence_review_decision_id", mode="before",
    )
    @classmethod
    def _validate_optional_relationship_ids(cls, value: object, info) -> Optional[str]:
        return None if value is None else _uuid(value, info.field_name)

    @field_validator("semantic_relationship")
    @classmethod
    def _validate_semantic_relationship(cls, value: SemanticRelationship):
        if value not in ("support", "qualification"):
            raise ValueError(
                "projected relationships must be authorized support or qualification"
            )
        return value

    @field_validator("locator_resolution")
    @classmethod
    def _validate_locator_resolution(cls, value):
        if value is not None and value != "resolvable":
            raise ValueError("projected relationships require resolvable locators")
        return value

    @field_validator("evidence_linkage")
    @classmethod
    def _validate_evidence_linkage(cls, value):
        if value is not None and value != "linked":
            raise ValueError("projected relationships require linked evidence")
        return value

    @field_validator("authorized_at")
    @classmethod
    def _validate_authorized_at(cls, value: Optional[datetime]) -> Optional[datetime]:
        return None if value is None else _aware(value, "authorized_at")

    @field_validator("authorization_sequence")
    @classmethod
    def _validate_authorization_sequence(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value < 1:
            raise ValueError("authorization_sequence must be positive")
        return value


def _canonical_value(value):
    """Canonicalize one projected value for deterministic fingerprinting."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, Enum):
        return _canonical_value(value.value)
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("canonical datetimes must be timezone-aware")
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, BaseModel):
        return {
            name: _canonical_value(getattr(value, name))
            for name in sorted(type(value).model_fields)
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    raise ValueError(
        f"unsupported canonical projection value type: {type(value).__name__}"
    )


def canonical_presentation_projection_descriptor(
    *,
    project_id: str,
    usage_scope: UsageScope,
    context,
    claims,
    sources,
    evidence,
    relationships,
    counts,
) -> str:
    """Serialize projected content into the documented canonical descriptor.

    The descriptor is compact JSON with sorted mapping keys, UTF-8 text, and
    ``ensure_ascii=False``. Omitted fields serialize as null, Decimals as
    exact scale-preserving strings, aware datetimes as UTC ISO-8601 via
    ``astimezone(UTC).isoformat()``, dates as ISO-8601, enums as their values,
    and UUIDs as canonical lowercase strings. Collections keep canonical
    member order. Runtime timestamps are never part of the descriptor.
    """
    scope = UsageScope(usage_scope)
    payload = {
        "descriptor": PRESENTATION_PROJECTION_DESCRIPTOR_VERSION,
        "policy_identifier": _policy.PRESENTATION_POLICY_IDENTIFIER,
        "policy_version": _policy.PRESENTATION_POLICY_VERSION,
        "policy_fingerprint": _policy.PRESENTATION_POLICY_FINGERPRINT,
        "content": {
            "project_id": _uuid(project_id, "project_id"),
            "usage_scope": scope.value,
            "context": _canonical_value(context),
            "claims": _canonical_value(tuple(claims)),
            "sources": _canonical_value(tuple(sources)),
            "evidence": _canonical_value(tuple(evidence)),
            "relationships": _canonical_value(tuple(relationships)),
            "counts": _canonical_value(counts),
        },
    }
    return json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    )


def presentation_projection_fingerprint(descriptor: str) -> str:
    return hashlib.sha256(descriptor.encode("utf-8")).hexdigest()


class ResearchEvidencePresentationProjection(_ImmutableStrictModel):
    project_id: str
    usage_scope: UsageScope
    policy_identifier: str
    policy_version: str
    policy_fingerprint: str
    context: Optional[ResearchEvidencePresentationContext] = None
    claims: tuple[ResearchEvidencePresentationClaim, ...] = ()
    sources: tuple[ResearchEvidencePresentationSource, ...] = ()
    evidence: tuple[ResearchEvidencePresentationEvidence, ...] = ()
    relationships: tuple[ResearchEvidencePresentationRelationship, ...] = ()
    counts: ResearchEvidencePackCounts = Field(
        default_factory=ResearchEvidencePackCounts
    )
    projection_fingerprint: str

    @field_validator("project_id", mode="before")
    @classmethod
    def _validate_projection_project_id(cls, value: object) -> str:
        return _uuid(value, "project_id")

    @model_validator(mode="after")
    def _validate_projection(self):
        self._require_pinned_policy_identity()
        self._require_scope_disclosure()
        self._require_consistent_membership()
        self._require_matching_fingerprint()
        return self

    def _require_pinned_policy_identity(self) -> None:
        pinned = (
            _policy.PRESENTATION_POLICY_IDENTIFIER,
            _policy.PRESENTATION_POLICY_VERSION,
            _policy.PRESENTATION_POLICY_FINGERPRINT,
        )
        declared = (
            self.policy_identifier, self.policy_version, self.policy_fingerprint,
        )
        if declared != pinned:
            raise ValueError(
                "projection must declare the frozen presentation policy identity"
            )

    def _require_consistent_membership(self) -> None:
        actual = (
            len(self.sources), len(self.claims), len(self.evidence),
            len(self.relationships),
        )
        declared = (
            self.counts.source_count, self.counts.claim_count,
            self.counts.evidence_count, self.counts.relationship_count,
        )
        if actual != declared:
            raise ValueError("projection counts must match projected collections")

        if not self.relationships and (
            self.context is not None or self.claims or self.sources or self.evidence
        ):
            raise ValueError(
                "an empty projection must contain only empty projected state"
            )

        claim_ids = tuple(item.claim_draft_id for item in self.claims)
        source_ids = tuple(item.source_snapshot_id for item in self.sources)
        evidence_ids = tuple(
            item.candidate_fact_revision_id for item in self.evidence
        )
        if claim_ids != tuple(sorted(claim_ids)) or len(set(claim_ids)) != len(claim_ids):
            raise ValueError("projected claims must be unique and ordered by claim_draft_id")
        if source_ids != tuple(sorted(source_ids)) or len(set(source_ids)) != len(source_ids):
            raise ValueError("projected sources must be unique and ordered by source_snapshot_id")
        evidence_order = tuple(
            (item.source_snapshot_id, item.candidate_fact_revision_id)
            for item in self.evidence
        )
        if evidence_order != tuple(sorted(evidence_order)) or len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("projected evidence must be unique and canonically ordered")

        relationship_order = tuple(
            (
                item.claim_draft_id, item.source_snapshot_id,
                item.candidate_fact_revision_id,
            )
            for item in self.relationships
        )
        canonical_relationships = tuple(
            (item.claim_draft_id, item.candidate_fact_revision_id)
            for item in self.relationships
        )
        if relationship_order != tuple(sorted(relationship_order)):
            raise ValueError("projected relationships must use canonical ordering")
        if len(set(canonical_relationships)) != len(canonical_relationships):
            raise ValueError("projected claim/evidence relationships must be unique")

        claims_by_id = {item.claim_draft_id: item for item in self.claims}
        source_id_set = set(source_ids)
        evidence_by_id = {
            item.candidate_fact_revision_id: item for item in self.evidence
        }
        for item in self.evidence:
            if item.source_snapshot_id not in source_id_set:
                raise ValueError("projected evidence references an absent source")
        for item in self.relationships:
            if item.usage_scope is not None and item.usage_scope != self.usage_scope:
                raise ValueError(
                    "projected relationship scope must match the projection scope"
                )
            claim = claims_by_id.get(item.claim_draft_id)
            if claim is None:
                raise ValueError("projected relationship references an absent claim")
            if item.source_snapshot_id not in source_id_set:
                raise ValueError("projected relationship references an absent source")
            evidence_item = evidence_by_id.get(item.candidate_fact_revision_id)
            if evidence_item is None:
                raise ValueError("projected relationship references absent evidence")
            if evidence_item.source_snapshot_id != item.source_snapshot_id:
                raise ValueError(
                    "projected relationship evidence must use its canonical source"
                )
            if (
                item.claim_annotation_revision_id is not None
                and claim.annotation_revision_id is not None
                and item.claim_annotation_revision_id != claim.annotation_revision_id
            ):
                raise ValueError(
                    "projected relationship references a non-current claim annotation"
                )

        reachable_claim_ids = {item.claim_draft_id for item in self.relationships}
        reachable_source_ids = {item.source_snapshot_id for item in self.relationships}
        reachable_evidence_ids = {
            item.candidate_fact_revision_id for item in self.relationships
        }
        if set(claim_ids) != reachable_claim_ids:
            raise ValueError(
                "projected claims must equal relationship-reachable claims"
            )
        if source_id_set != reachable_source_ids:
            raise ValueError(
                "projected sources must equal relationship-reachable sources"
            )
        if set(evidence_ids) != reachable_evidence_ids:
            raise ValueError(
                "projected evidence must equal relationship-reachable evidence"
            )

    def _require_scope_disclosure(self) -> None:
        members: list[tuple[str, BaseModel]] = []
        if self.context is not None:
            members.append(("context", self.context))
        for claim in self.claims:
            members.append(("claim", claim))
            if claim.explicit_probability is not None:
                members.append(("probability", claim.explicit_probability))
        members.extend(("source", item) for item in self.sources)
        members.extend(("evidence", item) for item in self.evidence)
        members.extend(("relationship", item) for item in self.relationships)

        for member_kind, member in members:
            allowed = _policy.allowed_presentation_fields(
                self.usage_scope, member_kind,
            )
            required = _policy.required_presentation_fields(
                self.usage_scope, member_kind,
            )
            model_fields = set(type(member).model_fields)
            unknown = (allowed | required) - model_fields
            if unknown:
                raise ValueError(
                    f"presentation policy names unknown {member_kind} fields: "
                    f"{sorted(unknown)}"
                )
            for name in model_fields:
                value = getattr(member, name)
                if name not in allowed and value is not None:
                    raise ValueError(
                        f"{member_kind} field {name} is not allowed for scope "
                        f"{self.usage_scope.value}"
                    )
                if name in required and value is None:
                    raise ValueError(
                        f"{member_kind} field {name} is required for scope "
                        f"{self.usage_scope.value}"
                    )

    def _require_matching_fingerprint(self) -> None:
        expected = presentation_projection_fingerprint(
            canonical_presentation_projection_descriptor(
                project_id=self.project_id,
                usage_scope=self.usage_scope,
                context=self.context,
                claims=self.claims,
                sources=self.sources,
                evidence=self.evidence,
                relationships=self.relationships,
                counts=self.counts,
            )
        )
        if self.projection_fingerprint != expected:
            raise ValueError(
                "projection fingerprint must match canonical projected content"
            )
