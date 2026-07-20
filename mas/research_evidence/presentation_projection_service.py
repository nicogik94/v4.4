"""Feature-gated, read-only presentation projection over the authorized pack.

The service reuses the public R2.0A-2 assembly contract for membership and
adds no authorization, persistence, SQL, or transaction behavior of its own.
Projection itself is a pure function of one validated pack aggregate and the
frozen presentation disclosure policy.
"""
from __future__ import annotations

import config
from pydantic import ValidationError

from . import pack_service
from . import presentation_projection_policy as _policy
from .pack_models import (
    ResearchEvidencePackAggregate,
    ResearchEvidencePackQuery,
    UsageScope,
)
from .presentation_projection_models import (
    ResearchEvidencePresentationClaim,
    ResearchEvidencePresentationContext,
    ResearchEvidencePresentationEvidence,
    ResearchEvidencePresentationProbability,
    ResearchEvidencePresentationProjection,
    ResearchEvidencePresentationRelationship,
    ResearchEvidencePresentationSource,
    canonical_presentation_projection_descriptor,
    presentation_projection_fingerprint,
)


class ResearchEvidencePresentationProjectionError(RuntimeError):
    pass


class ResearchEvidencePresentationProjectionDisabled(
    ResearchEvidencePresentationProjectionError
):
    pass


class ResearchEvidencePresentationProjectionIntegrityError(
    ResearchEvidencePresentationProjectionError
):
    pass


def _require_enabled() -> None:
    if not config.research_evidence_enabled():
        raise ResearchEvidencePresentationProjectionDisabled(
            "Research evidence is disabled (set MAS_RESEARCH_EVIDENCE_ENABLED to enable it)"
        )


def _context_values(context) -> dict:
    return {
        "research_question": context.research_question,
        "project_limitations": context.project_limitations,
        "unresolved_gaps": context.unresolved_gaps,
        "recorded_at": context.recorded_at,
        "context_revision_id": context.context_revision_id,
        "context_sequence": context.context_sequence,
    }


def _probability_values(probability) -> dict:
    return {
        "value": probability.value,
        "provided_by": probability.provided_by,
        "provenance_reference": probability.provenance_reference,
        "provenance_note": probability.provenance_note,
    }


def _claim_values(claim, usage_scope: UsageScope) -> dict:
    annotation = claim.annotation
    probability = annotation.explicit_probability
    return {
        "claim_draft_id": claim.claim_draft_id,
        "claim_text": claim.claim_text,
        "claim_category": claim.claim_category,
        "epistemic_status": annotation.epistemic_status,
        "confidence_label": annotation.confidence_label,
        "supports_statement": annotation.supports_statement,
        "does_not_prove": annotation.does_not_prove,
        "limitations": annotation.limitations,
        "decision_relevance": annotation.decision_relevance,
        "related_claim_draft_ids": annotation.related_claim_draft_ids,
        "explicit_probability": None if probability is None else _projected_member(
            ResearchEvidencePresentationProbability, "probability", usage_scope,
            _probability_values(probability),
        ),
        "annotation_revision_id": annotation.annotation_revision_id,
        "annotation_sequence": annotation.annotation_sequence,
        "annotation_recorded_at": annotation.recorded_at,
    }


def _source_values(source) -> dict:
    return {
        "source_snapshot_id": source.source_snapshot_id,
        "canonical_source_locator": source.canonical_source_locator,
        "publisher": source.publisher,
        "author": source.author,
        "citation_label": source.citation_label,
        "declared_quality_tier": source.declared_quality_tier,
        "published_at": source.published_at,
        "retrieved_at": source.retrieved_at,
        "source_kind": source.source_kind,
        "captured_at": source.captured_at,
        "declared_quality_rationale": source.declared_quality_rationale,
        "source_locator": source.source_locator,
        "source_blob_id": source.source_blob_id,
        "source_metadata_revision_id": source.source_metadata_revision_id,
    }


def _evidence_values(evidence) -> dict:
    return {
        "candidate_fact_revision_id": evidence.candidate_fact_revision_id,
        "source_snapshot_id": evidence.source_snapshot_id,
        "fact_type": evidence.fact_type,
        "unit": evidence.unit,
        "citation_locator": evidence.citation_locator,
        "numeric_value": evidence.numeric_value,
        "text_value": evidence.text_value,
        "currency_code": evidence.currency_code,
        "as_of_date": evidence.as_of_date,
        "numerator_context": evidence.numerator_context,
        "denominator_context": evidence.denominator_context,
        "percentage_basis": evidence.percentage_basis,
        "percentage_subtype": evidence.percentage_subtype,
        "time_unit": evidence.time_unit,
        "counted_entity": evidence.counted_entity,
        "fact_metadata_revision_id": evidence.fact_metadata_revision_id,
        "stable_fact_key": evidence.stable_fact_key,
        "source_char_range": evidence.source_char_range,
    }


def _relationship_values(relationship) -> dict:
    return {
        "claim_draft_id": relationship.claim_draft_id,
        "candidate_fact_revision_id": relationship.candidate_fact_revision_id,
        "source_snapshot_id": relationship.source_snapshot_id,
        "semantic_relationship": relationship.semantic_relationship,
        "authorized_at": relationship.authorized_at,
        "usage_scope": relationship.usage_scope,
        "authorization_sequence": relationship.authorization_sequence,
        "authorization_decision_id": relationship.authorization_decision_id,
        "claim_intake_item_id": relationship.claim_intake_item_id,
        "evidence_intake_item_id": relationship.evidence_intake_item_id,
        "claim_support_assessment_id": relationship.claim_support_assessment_id,
        "claim_annotation_revision_id": relationship.claim_annotation_revision_id,
        "claim_review_decision_id": relationship.claim_review_decision_id,
        "evidence_review_decision_id": relationship.evidence_review_decision_id,
        "locator_resolution": relationship.locator_resolution,
        "evidence_linkage": relationship.evidence_linkage,
    }


def _projected_member(model_cls, member_kind: str, usage_scope: UsageScope, values: dict):
    """Retain exactly the allowlisted fields; everything else stays omitted."""
    allowed = _policy.allowed_presentation_fields(usage_scope, member_kind)
    missing = allowed - set(values)
    if missing:
        raise ResearchEvidencePresentationProjectionIntegrityError(
            f"presentation policy allows unmapped {member_kind} fields: "
            f"{sorted(missing)}"
        )
    return model_cls(**{name: values[name] for name in allowed})


def _require_preserved_membership(pack, claims, sources, evidence, relationships):
    if tuple(item.claim_draft_id for item in claims) != tuple(
        item.claim_draft_id for item in pack.claims
    ):
        raise ResearchEvidencePresentationProjectionIntegrityError(
            "projection must preserve pack claim membership exactly"
        )
    if tuple(item.source_snapshot_id for item in sources) != tuple(
        item.source_snapshot_id for item in pack.sources
    ):
        raise ResearchEvidencePresentationProjectionIntegrityError(
            "projection must preserve pack source membership exactly"
        )
    if tuple(item.candidate_fact_revision_id for item in evidence) != tuple(
        item.candidate_fact_revision_id for item in pack.evidence
    ):
        raise ResearchEvidencePresentationProjectionIntegrityError(
            "projection must preserve pack evidence membership exactly"
        )
    projected_links = tuple(
        (item.claim_draft_id, item.source_snapshot_id, item.candidate_fact_revision_id)
        for item in relationships
    )
    pack_links = tuple(
        (item.claim_draft_id, item.source_snapshot_id, item.candidate_fact_revision_id)
        for item in pack.relationships
    )
    if projected_links != pack_links:
        raise ResearchEvidencePresentationProjectionIntegrityError(
            "projection must preserve pack relationships exactly"
        )


def project_research_evidence_pack(
    pack: ResearchEvidencePackAggregate,
) -> ResearchEvidencePresentationProjection:
    """Project one validated pack for its own scope under the frozen policy."""
    if not isinstance(pack, ResearchEvidencePackAggregate):
        raise ResearchEvidencePresentationProjectionError(
            "presentation projection requires a validated research evidence pack aggregate"
        )
    scope = pack.usage_scope
    try:
        context = None if pack.context is None else _projected_member(
            ResearchEvidencePresentationContext, "context", scope,
            _context_values(pack.context),
        )
        claims = tuple(
            _projected_member(
                ResearchEvidencePresentationClaim, "claim", scope,
                _claim_values(item, scope),
            )
            for item in pack.claims
        )
        sources = tuple(
            _projected_member(
                ResearchEvidencePresentationSource, "source", scope,
                _source_values(item),
            )
            for item in pack.sources
        )
        evidence = tuple(
            _projected_member(
                ResearchEvidencePresentationEvidence, "evidence", scope,
                _evidence_values(item),
            )
            for item in pack.evidence
        )
        relationships = tuple(
            _projected_member(
                ResearchEvidencePresentationRelationship, "relationship", scope,
                _relationship_values(item),
            )
            for item in pack.relationships
        )
        _require_preserved_membership(pack, claims, sources, evidence, relationships)
        fingerprint = presentation_projection_fingerprint(
            canonical_presentation_projection_descriptor(
                project_id=pack.project_id,
                usage_scope=scope,
                context=context,
                claims=claims,
                sources=sources,
                evidence=evidence,
                relationships=relationships,
                counts=pack.counts,
            )
        )
        return ResearchEvidencePresentationProjection(
            project_id=pack.project_id,
            usage_scope=scope,
            policy_identifier=_policy.PRESENTATION_POLICY_IDENTIFIER,
            policy_version=_policy.PRESENTATION_POLICY_VERSION,
            policy_fingerprint=_policy.PRESENTATION_POLICY_FINGERPRINT,
            context=context,
            claims=claims,
            sources=sources,
            evidence=evidence,
            relationships=relationships,
            counts=pack.counts,
            projection_fingerprint=fingerprint,
        )
    except ValidationError as exc:
        raise ResearchEvidencePresentationProjectionIntegrityError(
            "projected pack content violates the presentation projection contract"
        ) from exc


def project_research_evidence_presentation(
    conn, *, project_id: str, usage_scope: UsageScope,
) -> ResearchEvidencePresentationProjection:
    """Assemble the current pack via R2.0A-2 and project it for its scope."""
    query = ResearchEvidencePackQuery(
        project_id=project_id, usage_scope=usage_scope,
    )
    _require_enabled()
    pack = pack_service.assemble_research_evidence_pack(
        conn, project_id=query.project_id, usage_scope=query.usage_scope,
    )
    return project_research_evidence_pack(pack)
