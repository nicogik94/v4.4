"""Strict model tests for R1.4 freshness and drift assessments."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_evidence.freshness_models import (  # noqa: E402
    ResearchEvidenceIntakeItemFreshnessAssessmentCreate,
)


PROJECT = "00000000-0000-0000-0000-000000000001"
ITEM = "00000000-0000-0000-0000-000000000002"
COMPARISON = "00000000-0000-0000-0000-000000000003"
BASIS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _values():
    return {
        "project_id": PROJECT,
        "research_evidence_intake_item_id": ITEM,
        "request_id": " request-1 ",
        "policy_identifier": " source-age ",
        "policy_version": " 1 ",
        "policy_parameters_json": {"max_age_days": 30},
        "policy_fingerprint": " sha256:policy ",
        "evaluator_version": " evaluator-1 ",
        "basis_timestamp": BASIS,
        "fresh_through": BASIS + timedelta(days=30),
        "comparison_research_evidence_intake_item_id": COMPARISON,
        "drift_status": "no_material_drift",
        "drift_reason": " reviewed ",
        "assessed_by": " operator ",
    }


def test_create_model_is_strict_and_normalizes_operator_fields():
    model = ResearchEvidenceIntakeItemFreshnessAssessmentCreate(**_values())
    assert model.request_id == "request-1"
    assert model.policy_identifier == "source-age"
    assert model.policy_version == "1"
    assert model.policy_fingerprint == "sha256:policy"
    assert model.evaluator_version == "evaluator-1"
    assert model.drift_reason == "reviewed"
    assert model.assessed_by == "operator"

    for field, value in (
        ("assessment_sequence", 1),
        ("supersedes_assessment_id", PROJECT),
        ("linked_content_hash", "hash"),
        ("content_change_detected", True),
        ("assessed_at", BASIS),
        ("approval_available", True),
    ):
        values = _values()
        values[field] = value
        with pytest.raises(ValidationError):
            ResearchEvidenceIntakeItemFreshnessAssessmentCreate(**values)


@pytest.mark.parametrize(
    "field",
    [
        "request_id",
        "policy_identifier",
        "policy_version",
        "evaluator_version",
        "drift_reason",
        "assessed_by",
    ],
)
def test_create_model_rejects_blank_provenance(field):
    values = _values()
    values[field] = "\t "
    with pytest.raises(ValidationError):
        ResearchEvidenceIntakeItemFreshnessAssessmentCreate(**values)


@pytest.mark.parametrize(
    "drift_status",
    [
        "not_assessed",
        "no_material_drift",
        "material_drift",
        "indeterminate",
    ],
)
def test_exact_drift_statuses_are_accepted(drift_status):
    values = _values()
    values["drift_status"] = drift_status
    assert (
        ResearchEvidenceIntakeItemFreshnessAssessmentCreate(**values).drift_status
        == drift_status
    )


def test_policy_snapshot_or_fingerprint_is_required():
    values = _values()
    values["policy_parameters_json"] = {}
    values["policy_fingerprint"] = " "
    with pytest.raises(ValidationError, match="parameters or policy fingerprint"):
        ResearchEvidenceIntakeItemFreshnessAssessmentCreate(**values)


def test_timestamps_must_be_aware_and_window_must_be_nonnegative():
    values = _values()
    values["basis_timestamp"] = BASIS.replace(tzinfo=None)
    with pytest.raises(ValidationError, match="timezone"):
        ResearchEvidenceIntakeItemFreshnessAssessmentCreate(**values)

    values = _values()
    values["fresh_through"] = BASIS - timedelta(seconds=1)
    with pytest.raises(ValidationError, match="must not precede"):
        ResearchEvidenceIntakeItemFreshnessAssessmentCreate(**values)


def test_comparison_must_not_be_the_target_item():
    values = _values()
    values["comparison_research_evidence_intake_item_id"] = ITEM
    with pytest.raises(ValidationError, match="must differ"):
        ResearchEvidenceIntakeItemFreshnessAssessmentCreate(**values)
