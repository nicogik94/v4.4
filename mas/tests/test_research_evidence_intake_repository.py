"""Unit tests for the R1.2 low-level intake repository."""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_evidence import intake_repository as repo  # noqa: E402
from research_evidence.intake_models import (  # noqa: E402
    ResearchEvidenceIntakeCreate,
    ResearchEvidenceIntakeItemCreate,
)


PROJECT = "00000000-0000-0000-0000-000000000001"
SNAPSHOT = "00000000-0000-0000-0000-000000000002"
SOURCE_METADATA = "00000000-0000-0000-0000-000000000003"
INTAKE = "00000000-0000-0000-0000-000000000004"
FACT = "00000000-0000-0000-0000-000000000005"
FACT_METADATA = "00000000-0000-0000-0000-000000000006"
CLAIM = "00000000-0000-0000-0000-000000000007"
ITEM = "00000000-0000-0000-0000-000000000008"
NOW = datetime.now(timezone.utc)


class Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class ScriptedConn:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if not self.rows:
            raise AssertionError("unexpected repository query")
        return Result(self.rows.pop(0))


def _intake_create():
    return ResearchEvidenceIntakeCreate(
        project_id=PROJECT,
        source_snapshot_id=SNAPSHOT,
        source_metadata_revision_id=SOURCE_METADATA,
        selection_reason="Operator selected",
        created_by="operator",
    )


def test_snapshot_availability_is_project_scoped_and_uses_retention_events():
    conn = ScriptedConn([(True,)])
    assert repo.snapshot_is_available(
        conn, project_id=PROJECT, source_snapshot_id=SNAPSHOT
    )
    sql, params = conn.calls[0]
    assert "evidence_retention_event" in sql
    assert "'tombstone', 'redact'" in sql
    assert "s.id = %s AND s.project_id = %s" in sql
    assert params == (SNAPSHOT, PROJECT)


def test_missing_snapshot_is_a_scoped_parent_error():
    conn = ScriptedConn([None])
    with pytest.raises(repo.IntakeParentNotFound, match="source snapshot"):
        repo.snapshot_is_available(
            conn, project_id=PROJECT, source_snapshot_id=SNAPSHOT
        )


def test_insert_intake_requires_matching_metadata_and_returns_server_contract():
    row = (
        INTAKE,
        PROJECT,
        SNAPSHOT,
        SOURCE_METADATA,
        "operator_selected_existing_snapshot",
        "draft",
        "Operator selected",
        "operator",
        NOW,
    )
    conn = ScriptedConn([(1,), row])
    record = repo.insert_intake(conn, _intake_create())
    assert record.id == INTAKE
    assert record.state == "draft"
    assert record.intake_method == "operator_selected_existing_snapshot"
    assert conn.calls[0][1] == (SOURCE_METADATA, PROJECT, SNAPSHOT)
    insert_sql, insert_params = conn.calls[1]
    assert "INSERT INTO research_evidence_intake" in insert_sql
    assert insert_params == (
        PROJECT,
        SNAPSHOT,
        SOURCE_METADATA,
        "Operator selected",
        "operator",
    )


def test_insert_intake_rejects_metadata_snapshot_mismatch_before_insert():
    conn = ScriptedConn([None])
    with pytest.raises(repo.IntakeParentNotFound, match="metadata revision"):
        repo.insert_intake(conn, _intake_create())
    assert len(conn.calls) == 1


def test_get_intake_is_project_scoped():
    conn = ScriptedConn([None])
    assert repo.get_intake(conn, project_id=PROJECT, intake_id=INTAKE) is None
    assert conn.calls[0][1] == (INTAKE, PROJECT)


def test_insert_fact_item_uses_server_snapshot_and_matching_metadata():
    item = ResearchEvidenceIntakeItemCreate(
        project_id=PROJECT,
        research_evidence_intake_id=INTAKE,
        item_kind="candidate_fact",
        candidate_fact_revision_id=FACT,
        fact_metadata_revision_id=FACT_METADATA,
        created_by="operator",
    )
    row = (
        ITEM,
        PROJECT,
        INTAKE,
        SNAPSHOT,
        "candidate_fact",
        FACT,
        FACT_METADATA,
        None,
        "draft",
        "operator",
        NOW,
    )
    conn = ScriptedConn([(1,), row])
    record = repo.insert_item(conn, item, source_snapshot_id=SNAPSHOT)
    assert record.source_snapshot_id == SNAPSHOT
    assert conn.calls[0][1] == (FACT, PROJECT, SNAPSHOT, FACT_METADATA)
    assert conn.calls[1][1] == (
        PROJECT,
        INTAKE,
        SNAPSHOT,
        "candidate_fact",
        FACT,
        FACT_METADATA,
        None,
        "operator",
    )


def test_insert_claim_item_checks_same_project_claim():
    item = ResearchEvidenceIntakeItemCreate(
        project_id=PROJECT,
        research_evidence_intake_id=INTAKE,
        item_kind="claim_draft",
        claim_draft_id=CLAIM,
        created_by="operator",
    )
    row = (
        ITEM,
        PROJECT,
        INTAKE,
        SNAPSHOT,
        "claim_draft",
        None,
        None,
        CLAIM,
        "draft",
        "operator",
        NOW,
    )
    conn = ScriptedConn([(1,), row])
    record = repo.insert_item(conn, item, source_snapshot_id=SNAPSHOT)
    assert record.claim_draft_id == CLAIM
    assert conn.calls[0][1] == (CLAIM, PROJECT)


def test_fact_snapshot_or_metadata_mismatch_stops_before_insert():
    item = ResearchEvidenceIntakeItemCreate(
        project_id=PROJECT,
        research_evidence_intake_id=INTAKE,
        item_kind="candidate_fact",
        candidate_fact_revision_id=FACT,
        fact_metadata_revision_id=FACT_METADATA,
        created_by="operator",
    )
    conn = ScriptedConn([None])
    with pytest.raises(repo.IntakeParentNotFound, match="fact metadata"):
        repo.insert_item(conn, item, source_snapshot_id=SNAPSHOT)
    assert len(conn.calls) == 1
