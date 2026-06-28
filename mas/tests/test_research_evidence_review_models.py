"""Strict model tests for R1.3 controlled item review."""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_evidence.review_models import (  # noqa: E402
    ResearchEvidenceIntakeItemReviewDecisionCreate,
    ResearchEvidenceIntakeItemReviewDecisionRecord,
)


PROJECT = "00000000-0000-0000-0000-000000000001"
ITEM = "00000000-0000-0000-0000-000000000002"


def _values():
    return {
        "project_id": PROJECT,
        "research_evidence_intake_item_id": ITEM,
        "decision_type": "approved",
        "decision_reason": "  Reviewed by operator  ",
        "decided_by": "  operator  ",
        "request_id": "  request-1  ",
    }


def test_create_model_is_strict_and_trims_operator_fields():
    model = ResearchEvidenceIntakeItemReviewDecisionCreate(**_values())
    assert model.decision_reason == "Reviewed by operator"
    assert model.decided_by == "operator"
    assert model.request_id == "request-1"

    forbidden = (
        ("decision_sequence", 1),
        ("supersedes_decision_id", PROJECT),
        ("recorded_at", datetime.now(timezone.utc)),
        ("source_snapshot_id", PROJECT),
        ("candidate_fact_revision_id", PROJECT),
        ("fact_metadata_revision_id", PROJECT),
        ("claim_draft_id", PROJECT),
        ("effective_status", "approved"),
        ("approval_eligible", True),
    )
    for field, value in forbidden:
        values = _values()
        values[field] = value
        with pytest.raises(ValidationError):
            ResearchEvidenceIntakeItemReviewDecisionCreate(**values)


@pytest.mark.parametrize("field", ["decision_reason", "decided_by", "request_id"])
@pytest.mark.parametrize("value", ["", "   ", "\t", "\n", "\r\f\v"])
def test_create_model_rejects_all_blank_operator_fields(field, value):
    values = _values()
    values[field] = value
    with pytest.raises(ValidationError):
        ResearchEvidenceIntakeItemReviewDecisionCreate(**values)


@pytest.mark.parametrize(
    "decision_type", ["approved", "rejected", "needs_revision", "withdrawn"]
)
def test_create_model_accepts_exact_decision_types(decision_type):
    values = _values()
    values["decision_type"] = decision_type
    assert (
        ResearchEvidenceIntakeItemReviewDecisionCreate(**values).decision_type
        == decision_type
    )


@pytest.mark.parametrize("decision_type", ["approve", "released", "pending", ""])
def test_create_model_rejects_other_decision_types(decision_type):
    values = _values()
    values["decision_type"] = decision_type
    with pytest.raises(ValidationError):
        ResearchEvidenceIntakeItemReviewDecisionCreate(**values)


def test_record_exposes_only_server_result_fields():
    record = ResearchEvidenceIntakeItemReviewDecisionRecord(
        **_values(),
        id="00000000-0000-0000-0000-000000000003",
        decision_sequence=1,
        supersedes_decision_id=None,
        recorded_at=datetime.now(timezone.utc),
    )
    assert record.decision_sequence == 1
    assert record.supersedes_decision_id is None
