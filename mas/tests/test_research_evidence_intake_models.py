"""Strict model tests for R1.2 controlled research-evidence intake."""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_evidence.intake_models import (  # noqa: E402
    ResearchEvidenceIntakeCreate,
    ResearchEvidenceIntakeItemCreate,
    ResearchEvidenceIntakeItemRecord,
    ResearchEvidenceIntakeRecord,
)


PROJECT = "00000000-0000-0000-0000-000000000001"
SNAPSHOT = "00000000-0000-0000-0000-000000000002"
SOURCE_METADATA = "00000000-0000-0000-0000-000000000003"
INTAKE = "00000000-0000-0000-0000-000000000004"
FACT = "00000000-0000-0000-0000-000000000005"
FACT_METADATA = "00000000-0000-0000-0000-000000000006"
CLAIM = "00000000-0000-0000-0000-000000000007"


def _intake_values():
    return {
        "project_id": PROJECT,
        "source_snapshot_id": SNAPSHOT,
        "source_metadata_revision_id": SOURCE_METADATA,
        "selection_reason": "  Operator selected source  ",
        "created_by": "  operator  ",
    }


def _fact_item_values():
    return {
        "project_id": PROJECT,
        "research_evidence_intake_id": INTAKE,
        "item_kind": "candidate_fact",
        "candidate_fact_revision_id": FACT,
        "fact_metadata_revision_id": FACT_METADATA,
        "created_by": "operator",
    }


def test_intake_create_trims_required_operator_fields():
    model = ResearchEvidenceIntakeCreate(**_intake_values())
    assert model.selection_reason == "Operator selected source"
    assert model.created_by == "operator"


@pytest.mark.parametrize("field", ["selection_reason", "created_by"])
@pytest.mark.parametrize("value", ["", " ", "\t\n"])
def test_intake_create_rejects_blank_operator_fields(field, value):
    values = _intake_values()
    values[field] = value
    with pytest.raises(ValidationError):
        ResearchEvidenceIntakeCreate(**values)


def test_public_intake_create_rejects_server_and_later_wave_fields():
    for field, value in (
        ("state", "draft"),
        ("intake_method", "operator_selected_existing_snapshot"),
        ("event_sequence", 1),
        ("approval_status", "pending"),
        ("release_state", "released"),
        ("freshness_status", "fresh"),
        ("calculation_id", "calc-1"),
    ):
        values = _intake_values()
        values[field] = value
        with pytest.raises(ValidationError):
            ResearchEvidenceIntakeCreate(**values)


def test_public_item_create_omits_server_derived_snapshot_and_state():
    fields = set(ResearchEvidenceIntakeItemCreate.model_fields)
    assert "source_snapshot_id" not in fields
    assert "state" not in fields
    assert "intake_method" not in fields
    assert "event_sequence" not in fields

    for field, value in (
        ("source_snapshot_id", SNAPSHOT),
        ("state", "draft"),
        ("approval_status", "pending"),
        ("citation", "client citation"),
        ("freshness_status", "fresh"),
    ):
        values = _fact_item_values()
        values[field] = value
        with pytest.raises(ValidationError):
            ResearchEvidenceIntakeItemCreate(**values)


def test_candidate_fact_item_requires_exact_fact_shape():
    model = ResearchEvidenceIntakeItemCreate(**_fact_item_values())
    assert model.item_kind == "candidate_fact"

    invalid = [
        {},
        {"fact_metadata_revision_id": None},
        {"candidate_fact_revision_id": None},
        {"claim_draft_id": CLAIM},
        {"item_kind": "unknown"},
    ]
    for changes in invalid:
        values = _fact_item_values()
        values.update(changes)
        if not changes:
            values.pop("candidate_fact_revision_id")
            values.pop("fact_metadata_revision_id")
        with pytest.raises(ValidationError):
            ResearchEvidenceIntakeItemCreate(**values)


def test_claim_item_requires_exact_claim_shape():
    model = ResearchEvidenceIntakeItemCreate(
        project_id=PROJECT,
        research_evidence_intake_id=INTAKE,
        item_kind="claim_draft",
        claim_draft_id=CLAIM,
        created_by="operator",
    )
    assert model.claim_draft_id == CLAIM

    with pytest.raises(ValidationError):
        ResearchEvidenceIntakeItemCreate(
            project_id=PROJECT,
            research_evidence_intake_id=INTAKE,
            item_kind="claim_draft",
            claim_draft_id=CLAIM,
            candidate_fact_revision_id=FACT,
            created_by="operator",
        )


def test_item_rejects_blank_actor_before_persistence():
    values = _fact_item_values()
    values["created_by"] = "  "
    with pytest.raises(ValidationError):
        ResearchEvidenceIntakeItemCreate(**values)


def test_record_models_expose_only_server_result_fields():
    now = datetime.now(timezone.utc)
    intake = ResearchEvidenceIntakeRecord(
        **_intake_values(),
        id=INTAKE,
        intake_method="operator_selected_existing_snapshot",
        state="draft",
        created_at=now,
    )
    item = ResearchEvidenceIntakeItemRecord(
        **_fact_item_values(),
        id="00000000-0000-0000-0000-000000000008",
        source_snapshot_id=SNAPSHOT,
        state="draft",
        created_at=now,
    )
    assert intake.state == item.state == "draft"
    assert item.source_snapshot_id == SNAPSHOT
