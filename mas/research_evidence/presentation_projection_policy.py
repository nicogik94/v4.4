"""Frozen, versioned presentation disclosure policy for R2.0A-3 projections.

This policy does not create, extend, or replace evidence authorization.
Membership in the current authorized Research Evidence Pack is owned
exclusively by the R2.0A-1/R2.0A-2 usage-authorization contract. The policy
below only fixes the presentation-safe shape of already-authorized pack
content for each canonical ``UsageScope``: which pack fields may appear in a
scope's projection and which of those must always accompany their member.

Semantics are DEFAULT DENY. A field that is not explicitly allowed for a
scope must not appear in that scope's projection. There is no
allow-all-minus-denylist behavior anywhere in this module.
"""
from __future__ import annotations

import hashlib
import json

from .pack_models import UsageScope


PRESENTATION_POLICY_IDENTIFIER = "research_evidence_presentation_disclosure"
PRESENTATION_POLICY_VERSION = "1.0.0"

PRESENTATION_MEMBER_KINDS = (
    "context",
    "claim",
    "probability",
    "source",
    "evidence",
    "relationship",
)

_CONTEXT_SHARED = ("project_limitations", "research_question", "unresolved_gaps")
_CLAIM_SHARED = (
    "claim_category",
    "claim_draft_id",
    "claim_text",
    "confidence_label",
    "does_not_prove",
    "epistemic_status",
    "limitations",
    "supports_statement",
)
_PROBABILITY_SHARED = ("provided_by", "value")
_SOURCE_SHARED_REQUIRED = (
    "author",
    "canonical_source_locator",
    "citation_label",
    "declared_quality_tier",
    "publisher",
    "source_snapshot_id",
)
_SOURCE_SHARED_OPTIONAL = ("published_at", "retrieved_at")
_EVIDENCE_SHARED_REQUIRED = (
    "candidate_fact_revision_id",
    "citation_locator",
    "fact_type",
    "source_snapshot_id",
    "unit",
)
_EVIDENCE_SHARED_OPTIONAL = (
    "as_of_date",
    "counted_entity",
    "currency_code",
    "denominator_context",
    "numerator_context",
    "numeric_value",
    "percentage_basis",
    "percentage_subtype",
    "text_value",
    "time_unit",
)
_RELATIONSHIP_SHARED = (
    "candidate_fact_revision_id",
    "claim_draft_id",
    "semantic_relationship",
    "source_snapshot_id",
)


PRESENTATION_POLICY_PARAMETERS = {
    "default": "deny",
    "disclosure": {
        UsageScope.INTERNAL_ANALYSIS.value: {
            "context": {
                "allow": sorted(
                    _CONTEXT_SHARED
                    + ("context_revision_id", "context_sequence", "recorded_at")
                ),
                "require": sorted(
                    _CONTEXT_SHARED
                    + ("context_revision_id", "context_sequence", "recorded_at")
                ),
            },
            "claim": {
                "allow": sorted(
                    _CLAIM_SHARED
                    + (
                        "annotation_recorded_at",
                        "annotation_revision_id",
                        "annotation_sequence",
                        "decision_relevance",
                        "explicit_probability",
                        "related_claim_draft_ids",
                    )
                ),
                "require": sorted(
                    _CLAIM_SHARED
                    + (
                        "annotation_recorded_at",
                        "annotation_revision_id",
                        "annotation_sequence",
                        "decision_relevance",
                        "related_claim_draft_ids",
                    )
                ),
            },
            "probability": {
                "allow": sorted(
                    _PROBABILITY_SHARED
                    + ("provenance_note", "provenance_reference")
                ),
                "require": sorted(
                    _PROBABILITY_SHARED
                    + ("provenance_note", "provenance_reference")
                ),
            },
            "source": {
                "allow": sorted(
                    _SOURCE_SHARED_REQUIRED
                    + _SOURCE_SHARED_OPTIONAL
                    + (
                        "captured_at",
                        "declared_quality_rationale",
                        "source_blob_id",
                        "source_kind",
                        "source_locator",
                        "source_metadata_revision_id",
                    )
                ),
                "require": sorted(
                    _SOURCE_SHARED_REQUIRED
                    + (
                        "captured_at",
                        "declared_quality_rationale",
                        "source_blob_id",
                        "source_kind",
                        "source_locator",
                        "source_metadata_revision_id",
                    )
                ),
            },
            "evidence": {
                "allow": sorted(
                    _EVIDENCE_SHARED_REQUIRED
                    + _EVIDENCE_SHARED_OPTIONAL
                    + (
                        "fact_metadata_revision_id",
                        "source_char_range",
                        "stable_fact_key",
                    )
                ),
                "require": sorted(
                    _EVIDENCE_SHARED_REQUIRED
                    + ("fact_metadata_revision_id", "stable_fact_key")
                ),
            },
            "relationship": {
                "allow": sorted(
                    _RELATIONSHIP_SHARED
                    + (
                        "authorization_decision_id",
                        "authorization_sequence",
                        "authorized_at",
                        "claim_annotation_revision_id",
                        "claim_intake_item_id",
                        "claim_review_decision_id",
                        "claim_support_assessment_id",
                        "evidence_intake_item_id",
                        "evidence_linkage",
                        "evidence_review_decision_id",
                        "locator_resolution",
                        "usage_scope",
                    )
                ),
                "require": sorted(
                    _RELATIONSHIP_SHARED
                    + (
                        "authorization_decision_id",
                        "authorization_sequence",
                        "authorized_at",
                        "claim_annotation_revision_id",
                        "claim_intake_item_id",
                        "claim_review_decision_id",
                        "claim_support_assessment_id",
                        "evidence_intake_item_id",
                        "evidence_linkage",
                        "evidence_review_decision_id",
                        "locator_resolution",
                        "usage_scope",
                    )
                ),
            },
        },
        UsageScope.OPERATOR_DOSSIER.value: {
            "context": {
                "allow": sorted(_CONTEXT_SHARED + ("recorded_at",)),
                "require": sorted(_CONTEXT_SHARED + ("recorded_at",)),
            },
            "claim": {
                "allow": sorted(
                    _CLAIM_SHARED
                    + (
                        "annotation_recorded_at",
                        "decision_relevance",
                        "explicit_probability",
                        "related_claim_draft_ids",
                    )
                ),
                "require": sorted(
                    _CLAIM_SHARED
                    + (
                        "annotation_recorded_at",
                        "decision_relevance",
                        "related_claim_draft_ids",
                    )
                ),
            },
            "probability": {
                "allow": sorted(
                    _PROBABILITY_SHARED
                    + ("provenance_note", "provenance_reference")
                ),
                "require": sorted(
                    _PROBABILITY_SHARED
                    + ("provenance_note", "provenance_reference")
                ),
            },
            "source": {
                "allow": sorted(
                    _SOURCE_SHARED_REQUIRED
                    + _SOURCE_SHARED_OPTIONAL
                    + (
                        "captured_at",
                        "declared_quality_rationale",
                        "source_kind",
                    )
                ),
                "require": sorted(
                    _SOURCE_SHARED_REQUIRED
                    + (
                        "captured_at",
                        "declared_quality_rationale",
                        "source_kind",
                    )
                ),
            },
            "evidence": {
                "allow": sorted(
                    _EVIDENCE_SHARED_REQUIRED + _EVIDENCE_SHARED_OPTIONAL
                ),
                "require": sorted(_EVIDENCE_SHARED_REQUIRED),
            },
            "relationship": {
                "allow": sorted(_RELATIONSHIP_SHARED + ("authorized_at",)),
                "require": sorted(_RELATIONSHIP_SHARED + ("authorized_at",)),
            },
        },
        UsageScope.CLIENT_REPORT.value: {
            "context": {
                "allow": sorted(_CONTEXT_SHARED),
                "require": sorted(_CONTEXT_SHARED),
            },
            "claim": {
                "allow": sorted(_CLAIM_SHARED + ("explicit_probability",)),
                "require": sorted(_CLAIM_SHARED),
            },
            "probability": {
                "allow": sorted(_PROBABILITY_SHARED),
                "require": sorted(_PROBABILITY_SHARED),
            },
            "source": {
                "allow": sorted(
                    _SOURCE_SHARED_REQUIRED + _SOURCE_SHARED_OPTIONAL
                ),
                "require": sorted(_SOURCE_SHARED_REQUIRED),
            },
            "evidence": {
                "allow": sorted(
                    _EVIDENCE_SHARED_REQUIRED + _EVIDENCE_SHARED_OPTIONAL
                ),
                "require": sorted(_EVIDENCE_SHARED_REQUIRED),
            },
            "relationship": {
                "allow": sorted(_RELATIONSHIP_SHARED),
                "require": sorted(_RELATIONSHIP_SHARED),
            },
        },
    },
}

PRESENTATION_POLICY_CANONICAL_JSON = json.dumps(
    PRESENTATION_POLICY_PARAMETERS,
    ensure_ascii=False,
    separators=(",", ":"),
    sort_keys=True,
)
PRESENTATION_POLICY_FINGERPRINT = hashlib.sha256(
    PRESENTATION_POLICY_CANONICAL_JSON.encode("utf-8")
).hexdigest()


def allowed_presentation_fields(
    usage_scope: UsageScope, member_kind: str,
) -> frozenset[str]:
    """Return the frozen explicit field allowlist for one scope and member."""
    return frozenset(_member_policy(usage_scope, member_kind)["allow"])


def required_presentation_fields(
    usage_scope: UsageScope, member_kind: str,
) -> frozenset[str]:
    """Return the fields that must accompany a projected member for a scope."""
    return frozenset(_member_policy(usage_scope, member_kind)["require"])


def _member_policy(usage_scope: UsageScope, member_kind: str) -> dict:
    scope = UsageScope(usage_scope)
    if member_kind not in PRESENTATION_MEMBER_KINDS:
        raise ValueError(f"unknown presentation member kind: {member_kind}")
    return PRESENTATION_POLICY_PARAMETERS["disclosure"][scope.value][member_kind]


def _validate_frozen_policy() -> None:
    disclosure = PRESENTATION_POLICY_PARAMETERS["disclosure"]
    if PRESENTATION_POLICY_PARAMETERS["default"] != "deny":
        raise ValueError("presentation policy must be default deny")
    if set(disclosure) != {scope.value for scope in UsageScope}:
        raise ValueError("presentation policy must cover every usage scope exactly")
    for scope_value, members in disclosure.items():
        if set(members) != set(PRESENTATION_MEMBER_KINDS):
            raise ValueError(
                f"presentation policy for {scope_value} must cover every member kind"
            )
        for member_kind, policy in members.items():
            allow, require = policy["allow"], policy["require"]
            for collection in (allow, require):
                if len(set(collection)) != len(collection):
                    raise ValueError(
                        f"{scope_value}/{member_kind} policy fields must be distinct"
                    )
                if list(collection) != sorted(collection):
                    raise ValueError(
                        f"{scope_value}/{member_kind} policy fields must be sorted"
                    )
            if not set(require) <= set(allow):
                raise ValueError(
                    f"{scope_value}/{member_kind} required fields must be allowed"
                )


_validate_frozen_policy()
