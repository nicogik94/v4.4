from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from research_evidence.pack_models import (
    ResearchEvidencePackAggregate,
    ResearchEvidencePackAuthorizedClaim,
    ResearchEvidencePackAuthorizedEvidence,
    ResearchEvidencePackAuthorizedRelationship,
    ResearchEvidencePackAuthorizedSource,
    ResearchEvidencePackClaimAnnotation,
    ResearchEvidencePackContext,
    ResearchEvidencePackCounts,
    ResearchEvidencePackQuery,
    ResearchEvidenceClaimAnnotationRevisionCreate,
    ResearchEvidenceClaimAnnotationRevisionRecord,
    ResearchEvidenceExplicitProbability,
    ResearchEvidenceProjectContextRevisionCreate,
    ResearchEvidenceUsageAuthorizationDecisionCreate,
)


def uid() -> str:
    return str(uuid4())


def annotation(**overrides):
    values = dict(
        project_id=uid(), claim_draft_id=uid(), request_id=" request ",
        epistemic_status="inference", confidence_label="medium",
        decision_relevance=" Relevant ", supports_statement=" Supports ",
        does_not_prove=" Does not prove ", limitations=[" one ", "two"],
        related_claim_draft_ids=[uid()], operator_notes=" note ", actor=" operator ",
    )
    values.update(overrides)
    return values


def test_context_trims_bounds_and_forbids_server_fields():
    model = ResearchEvidenceProjectContextRevisionCreate(
        project_id=uid(), request_id=" request ", research_question=" question ",
        project_limitations=[" limit "], unresolved_gaps=[" gap "], actor=" actor ",
    )
    assert model.request_id == "request"
    assert model.project_limitations == ("limit",)
    with pytest.raises(ValidationError):
        ResearchEvidenceProjectContextRevisionCreate(
            project_id=uid(), request_id="r", research_question="q",
            project_limitations=[], unresolved_gaps=[], actor="a", context_sequence=1,
        )


@pytest.mark.parametrize("field", ["project_limitations", "unresolved_gaps"])
def test_context_arrays_are_distinct_nonblank_and_bounded(field):
    values = dict(project_id=uid(), request_id="r", research_question="q",
                  project_limitations=[], unresolved_gaps=[], actor="a")
    values[field] = ["same", " same "]
    with pytest.raises(ValidationError):
        ResearchEvidenceProjectContextRevisionCreate(**values)
    values[field] = ["x" * 501]
    with pytest.raises(ValidationError):
        ResearchEvidenceProjectContextRevisionCreate(**values)


def test_annotation_rejects_numeric_confidence_and_self_reference():
    with pytest.raises(ValidationError):
        ResearchEvidenceClaimAnnotationRevisionCreate(**annotation(confidence_label=0.8))
    claim_id = uid()
    with pytest.raises(ValidationError):
        ResearchEvidenceClaimAnnotationRevisionCreate(
            **annotation(claim_draft_id=claim_id, related_claim_draft_ids=[claim_id])
        )


def test_probability_is_complete_finite_bounded_and_six_places():
    value = ResearchEvidenceExplicitProbability(
        value=Decimal("0.123456"), provided_by="source",
        provenance_reference="source:page-1", provenance_note="operator transcription",
    )
    assert value.value == Decimal("0.123456")
    for invalid in ("NaN", "Infinity", "-0.1", "1.1", "0.1234567"):
        with pytest.raises(ValidationError):
            ResearchEvidenceExplicitProbability(
                value=Decimal(invalid), provided_by="source",
                provenance_reference="ref", provenance_note="note",
            )


@pytest.mark.parametrize("value", ["0", "1", "0.000000", "1.000000", "0.123456"])
def test_probability_accepts_finite_boundaries_and_up_to_six_places(value):
    probability = ResearchEvidenceExplicitProbability(
        value=Decimal(value), provided_by="source",
        provenance_reference="ref", provenance_note="note",
    )
    assert probability.value == Decimal(value)


@pytest.mark.parametrize(
    "missing", ["provided_by", "provenance_reference", "provenance_note"],
)
def test_probability_rejects_incomplete_provenance(missing):
    values = {
        "value": Decimal("0.5"), "provided_by": "source",
        "provenance_reference": "ref", "provenance_note": "note",
    }
    values.pop(missing)
    with pytest.raises(ValidationError):
        ResearchEvidenceExplicitProbability(**values)


def test_normally_validated_nested_probability_remains_accepted():
    probability = ResearchEvidenceExplicitProbability(
        value=Decimal("0.123456"), provided_by="operator",
        provenance_reference="calculation", provenance_note="reviewed",
    )
    model = ResearchEvidenceClaimAnnotationRevisionCreate(
        **annotation(explicit_probability=probability),
    )
    assert model.explicit_probability is probability


def test_record_requires_positive_sequence_and_aware_timestamp():
    base = ResearchEvidenceClaimAnnotationRevisionCreate(**annotation()).model_dump()
    with pytest.raises(ValidationError):
        ResearchEvidenceClaimAnnotationRevisionRecord(
            **base, id=uid(), annotation_sequence=0,
            supersedes_annotation_revision_id=None,
            recorded_at=datetime.now(timezone.utc),
        )
    with pytest.raises(ValidationError):
        ResearchEvidenceClaimAnnotationRevisionRecord(
            **base, id=uid(), annotation_sequence=1,
            supersedes_annotation_revision_id=None, recorded_at=datetime.now(),
        )


def test_usage_create_has_only_caller_fields_and_normalizes_uuids():
    project_id = str(uuid4()).upper()
    model = ResearchEvidenceUsageAuthorizationDecisionCreate(
        project_id=project_id, claim_intake_item_id=uid(), evidence_intake_item_id=uid(),
        usage_scope="client_report", decision="authorized", reason=" reason ",
        actor=" actor ", request_id=" request ",
    )
    assert model.project_id == str(UUID(project_id))
    assert model.reason == "reason"
    with pytest.raises(ValidationError):
        ResearchEvidenceUsageAuthorizationDecisionCreate(
            **model.model_dump(), decision_sequence=1
        )


def aggregate_members(*, scope="client_report"):
    claim_id = uid()
    source_id = uid()
    fact_id = uid()
    annotation = ResearchEvidencePackClaimAnnotation(
        annotation_revision_id=uid(), claim_draft_id=claim_id,
        annotation_sequence=1, epistemic_status="reported_fact",
        confidence_label="high", decision_relevance="decision relevant",
        supports_statement="supports the claim", does_not_prove="causality",
        limitations=(), related_claim_draft_ids=(), recorded_at=datetime.now(timezone.utc),
    )
    claim = ResearchEvidencePackAuthorizedClaim(
        claim_draft_id=claim_id, claim_text="Authorized claim",
        claim_category="fact", annotation=annotation,
    )
    source = ResearchEvidencePackAuthorizedSource(
        source_snapshot_id=source_id, source_blob_id=uid(),
        source_metadata_revision_id=uid(), captured_at=datetime.now(timezone.utc),
    )
    evidence = ResearchEvidencePackAuthorizedEvidence(
        candidate_fact_revision_id=fact_id, source_snapshot_id=source_id,
        fact_metadata_revision_id=uid(), fact_type="text", text_value="Evidence",
    )
    relationship = ResearchEvidencePackAuthorizedRelationship(
        authorization_decision_id=uid(), claim_intake_item_id=uid(),
        evidence_intake_item_id=uid(), claim_support_assessment_id=uid(),
        claim_draft_id=claim_id, candidate_fact_revision_id=fact_id,
        source_snapshot_id=source_id,
        claim_annotation_revision_id=annotation.annotation_revision_id,
        claim_review_decision_id=uid(), evidence_review_decision_id=uid(),
        usage_scope=scope, authorization_sequence=1,
        authorized_at=datetime.now(timezone.utc), locator_resolution="resolvable",
        evidence_linkage="linked", semantic_relationship="support",
    )
    context_model = ResearchEvidencePackContext(
        context_revision_id=uid(), context_sequence=1,
        research_question="What should be decided?", project_limitations=(),
        unresolved_gaps=(), recorded_at=datetime.now(timezone.utc),
    )
    return context_model, claim, source, evidence, relationship


def test_aggregate_query_requires_uuid_and_explicit_valid_scope():
    query = ResearchEvidencePackQuery(project_id=uid(), usage_scope="client_report")
    assert query.usage_scope.value == "client_report"
    with pytest.raises(ValidationError):
        ResearchEvidencePackQuery(project_id=uid())
    with pytest.raises(ValidationError):
        ResearchEvidencePackQuery(project_id=uid(), usage_scope="all_scopes")
    with pytest.raises(ValidationError):
        ResearchEvidencePackQuery(project_id="not-a-uuid", usage_scope="client_report")


def test_empty_pack_is_typed_immutable_and_uses_tuple_collections():
    pack = ResearchEvidencePackAggregate(
        project_id=uid(), usage_scope="internal_analysis",
    )
    assert pack.counts == ResearchEvidencePackCounts()
    assert pack.context is None
    assert pack.claims == pack.sources == pack.evidence == pack.relationships == ()
    with pytest.raises(ValidationError):
        pack.project_id = uid()


def test_populated_aggregate_validates_scope_counts_and_canonical_members():
    context_model, claim, source, evidence, relationship = aggregate_members()
    pack = ResearchEvidencePackAggregate(
        project_id=uid(), usage_scope="client_report", context=context_model,
        claims=[claim], sources=[source], evidence=[evidence],
        relationships=[relationship],
        counts=ResearchEvidencePackCounts(
            source_count=1, claim_count=1, evidence_count=1,
            relationship_count=1,
        ),
    )
    assert isinstance(pack.claims, tuple)
    assert pack.relationships[0].usage_scope == pack.usage_scope
    with pytest.raises(ValidationError, match="counts"):
        ResearchEvidencePackAggregate(
            **{**pack.model_dump(), "counts": ResearchEvidencePackCounts()}
        )
    wrong_scope = relationship.model_copy(update={"usage_scope": "operator_dossier"})
    with pytest.raises(ValidationError, match="scope"):
        ResearchEvidencePackAggregate(
            **{**pack.model_dump(), "relationships": [wrong_scope]}
        )
    wrong_annotation = relationship.model_copy(
        update={"claim_annotation_revision_id": uid()},
    )
    with pytest.raises(ValidationError, match="non-current pack annotation"):
        ResearchEvidencePackAggregate(
            **{**pack.model_dump(), "relationships": [wrong_annotation]}
        )


@pytest.mark.parametrize(
    ("orphan_kind", "message"),
    [
        ("claim", "relationship-reachable claims"),
        ("source", "relationship-reachable sources"),
        ("evidence", "relationship-reachable evidence"),
    ],
)
def test_populated_aggregate_rejects_unreferenced_members(orphan_kind, message):
    context_model, claim, source, evidence, relationship = aggregate_members()
    claims = [claim]
    sources = [source]
    evidence_items = [evidence]
    if orphan_kind == "claim":
        orphan_id = uid()
        claims.append(claim.model_copy(update={
            "claim_draft_id": orphan_id,
            "annotation": claim.annotation.model_copy(update={
                "annotation_revision_id": uid(), "claim_draft_id": orphan_id,
            }),
        }))
    elif orphan_kind == "source":
        sources.append(source.model_copy(update={
            "source_snapshot_id": uid(), "source_blob_id": uid(),
            "source_metadata_revision_id": uid(),
        }))
    else:
        evidence_items.append(evidence.model_copy(update={
            "candidate_fact_revision_id": uid(),
            "fact_metadata_revision_id": uid(),
        }))
    claims.sort(key=lambda item: item.claim_draft_id)
    sources.sort(key=lambda item: item.source_snapshot_id)
    evidence_items.sort(
        key=lambda item: (item.source_snapshot_id, item.candidate_fact_revision_id)
    )
    with pytest.raises(ValidationError, match=message):
        ResearchEvidencePackAggregate(
            project_id=uid(), usage_scope="client_report", context=context_model,
            claims=claims, sources=sources, evidence=evidence_items,
            relationships=[relationship],
            counts=ResearchEvidencePackCounts(
                source_count=len(sources), claim_count=len(claims),
                evidence_count=len(evidence_items), relationship_count=1,
            ),
        )


def test_aggregate_caps_and_empty_state_are_fail_closed():
    with pytest.raises(ValidationError):
        ResearchEvidencePackCounts(source_count=51)
    with pytest.raises(ValidationError):
        ResearchEvidencePackCounts(claim_count=201)
    with pytest.raises(ValidationError):
        ResearchEvidencePackCounts(relationship_count=10001)
    context_model, _, _, _, _ = aggregate_members()
    with pytest.raises(ValidationError, match="empty pack"):
        ResearchEvidencePackAggregate(
            project_id=uid(), usage_scope="client_report", context=context_model,
        )
