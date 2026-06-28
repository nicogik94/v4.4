"""Model tests for R1.5 pair-scoped claim-support assessments."""
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_evidence.claim_support_models import (  # noqa: E402
    ResearchEvidenceClaimSupportAssessmentCreate,
)


def _assessment(**changes):
    values = {
        "project_id": "project",
        "claim_intake_item_id": "claim-item",
        "evidence_intake_item_id": "evidence-item",
        "request_id": "request-1",
        "locator_resolution": "resolvable",
        "locator_rationale": "Stored locator was reviewed.",
        "evidence_linkage": "linked",
        "evidence_linkage_rationale": "The selected item is the intended evidence.",
        "semantic_relationship": "support",
        "semantic_relationship_rationale": "Operator assessed supporting context.",
        "assessed_by": "operator",
    }
    values.update(changes)
    return ResearchEvidenceClaimSupportAssessmentCreate(**values)


def test_dimensions_accept_independent_contract_values():
    assessment = _assessment(
        locator_resolution="unresolvable",
        evidence_linkage="linked",
        semantic_relationship="contradiction",
    )
    assert assessment.locator_resolution == "unresolvable"
    assert assessment.evidence_linkage == "linked"
    assert assessment.semantic_relationship == "contradiction"


@pytest.mark.parametrize(
    ("field", "values"),
    [
        (
            "locator_resolution",
            ("not_assessed", "resolvable", "unresolvable", "indeterminate"),
        ),
        (
            "evidence_linkage",
            ("not_assessed", "linked", "not_linked", "indeterminate"),
        ),
        (
            "semantic_relationship",
            (
                "not_assessed",
                "support",
                "contradiction",
                "qualification",
                "insufficient_evidence",
            ),
        ),
    ],
)
def test_declared_dimension_enums(field, values):
    for value in values:
        assert getattr(_assessment(**{field: value}), field) == value
    with pytest.raises(ValidationError):
        _assessment(**{field: "automatic_truth"})


@pytest.mark.parametrize(
    "field",
    (
        "request_id",
        "locator_rationale",
        "evidence_linkage_rationale",
        "semantic_relationship_rationale",
        "assessed_by",
    ),
)
def test_each_rationale_and_identity_field_is_nonblank(field):
    with pytest.raises(ValidationError, match="blank"):
        _assessment(**{field: " \t "})


def test_pair_endpoints_must_differ_and_extra_fields_are_forbidden():
    with pytest.raises(ValidationError, match="must differ"):
        _assessment(evidence_intake_item_id="claim-item")
    with pytest.raises(ValidationError):
        ResearchEvidenceClaimSupportAssessmentCreate(
            **_assessment().model_dump(),
            citation_ready=True,
        )
