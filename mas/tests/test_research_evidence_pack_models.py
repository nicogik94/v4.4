from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from research_evidence.pack_models import (
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
