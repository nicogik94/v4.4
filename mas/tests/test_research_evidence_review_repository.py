"""Unit tests for the R1.3 review repository."""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_evidence import review_repository as repo  # noqa: E402
from research_evidence.review_models import (  # noqa: E402
    ResearchEvidenceIntakeItemReviewDecisionCreate,
    ResearchEvidenceIntakeItemReviewDecisionRecord,
)


PROJECT = "00000000-0000-0000-0000-000000000001"
ITEM = "00000000-0000-0000-0000-000000000002"
INTAKE = "00000000-0000-0000-0000-000000000003"
SNAPSHOT = "00000000-0000-0000-0000-000000000004"
SOURCE_METADATA = "00000000-0000-0000-0000-000000000005"
FACT = "00000000-0000-0000-0000-000000000006"
FACT_METADATA = "00000000-0000-0000-0000-000000000007"
DECISION = "00000000-0000-0000-0000-000000000008"
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
        if sql.startswith(("SAVEPOINT ", "RELEASE ", "ROLLBACK ")):
            return Result(None)
        if not self.rows:
            raise AssertionError(f"unexpected repository query: {sql}")
        row = self.rows.pop(0)
        if isinstance(row, Exception):
            raise row
        return Result(row)


class IntegrityFailure(Exception):
    sqlstate = "23514"
    diag = None


def _create(**changes):
    values = {
        "project_id": PROJECT,
        "research_evidence_intake_item_id": ITEM,
        "decision_type": "approved",
        "decision_reason": "Reviewed",
        "decided_by": "operator",
        "request_id": "request-1",
    }
    values.update(changes)
    return ResearchEvidenceIntakeItemReviewDecisionCreate(**values)


def _record(**changes):
    values = _create().model_dump()
    values.update(
        {
            "id": DECISION,
            "decision_sequence": 1,
            "supersedes_decision_id": None,
            "recorded_at": NOW,
        }
    )
    values.update(changes)
    return ResearchEvidenceIntakeItemReviewDecisionRecord(**values)


def _row(record=None):
    record = record or _record()
    return (
        record.id,
        record.project_id,
        record.research_evidence_intake_item_id,
        record.decision_type,
        record.decision_reason,
        record.decided_by,
        record.request_id,
        record.decision_sequence,
        record.supersedes_decision_id,
        record.recorded_at,
    )


@pytest.mark.parametrize(
    ("item_kind", "candidate_fact_id", "claim_id", "helper_name", "helper_id"),
    [
        ("candidate_fact", FACT, None, "fact_available", FACT),
        (
            "claim_draft",
            None,
            "00000000-0000-0000-0000-000000000009",
            "snapshot_available",
            SNAPSHOT,
        ),
    ],
)
def test_context_query_is_project_scoped_and_uses_canonical_v47_availability(
    monkeypatch,
    item_kind,
    candidate_fact_id,
    claim_id,
    helper_name,
    helper_id,
):
    conn = ScriptedConn(
        [
            (
                PROJECT,
                ITEM,
                INTAKE,
                item_kind,
                SNAPSHOT,
                SOURCE_METADATA,
                candidate_fact_id,
                FACT_METADATA if candidate_fact_id else None,
                claim_id,
                True,
            )
        ]
    )
    calls = []
    monkeypatch.setattr(
        repo.evidence_repo,
        helper_name,
        lambda helper_conn, entity_id: calls.append((helper_conn, entity_id)) or True,
    )
    context = repo.get_item_context(
        conn, project_id=PROJECT, research_evidence_intake_item_id=ITEM
    )
    assert context.approval_available
    assert calls == [(conn, helper_id)]
    sql, params = conn.calls[0]
    assert params == (ITEM, PROJECT)
    assert "evidence_retention_event" not in sql
    assert "supersedes_metadata_revision_id" in sql
    assert "supersedes_candidate_fact_revision_id" in sql
    assert "supersedes_claim_id" in sql
    assert "event.event_type IN ('superseded', 'withdrawn')" in sql


def test_canonical_v47_unavailability_combines_with_local_lineage(monkeypatch):
    conn = ScriptedConn(
        [
            (
                PROJECT,
                ITEM,
                INTAKE,
                "candidate_fact",
                SNAPSHOT,
                SOURCE_METADATA,
                FACT,
                FACT_METADATA,
                None,
                True,
            )
        ]
    )
    monkeypatch.setattr(repo.evidence_repo, "fact_available", lambda *args: False)
    context = repo.get_item_context(
        conn, project_id=PROJECT, research_evidence_intake_item_id=ITEM
    )
    assert not context.approval_available


def test_missing_project_scoped_item_is_parent_error():
    conn = ScriptedConn([None])
    with pytest.raises(repo.ReviewParentNotFound, match="intake item"):
        repo.get_item_context(
            conn, project_id=PROJECT, research_evidence_intake_item_id=ITEM
        )


def test_effective_decision_uses_sequence_only_and_withdrawn_means_none():
    conn = ScriptedConn([_row()])
    result = repo.get_effective_decision(
        conn, project_id=PROJECT, research_evidence_intake_item_id=ITEM
    )
    assert result == _record()
    sql = conn.calls[0][0]
    assert "ORDER BY decision_sequence DESC" in sql
    assert "recorded_at DESC" not in sql

    withdrawn = _record(decision_type="withdrawn")
    assert (
        repo.get_effective_decision(
            ScriptedConn([_row(withdrawn)]),
            project_id=PROJECT,
            research_evidence_intake_item_id=ITEM,
        )
        is None
    )


def test_insert_omits_server_owned_fields_and_uses_nested_savepoint():
    conn = ScriptedConn([None, _row()])
    result = repo.insert_decision(conn, _create())
    assert result.id == DECISION
    insert = next(call for call in conn.calls if "INSERT INTO" in call[0])
    assert "decision_sequence" not in insert[0].split("VALUES", 1)[0]
    assert "supersedes_decision_id" not in insert[0].split("VALUES", 1)[0]
    assert "recorded_at" not in insert[0].split("VALUES", 1)[0]
    assert [call[0] for call in conn.calls if "SAVEPOINT" in call[0]] == [
        "SAVEPOINT research_evidence_review_insert",
        "RELEASE SAVEPOINT research_evidence_review_insert",
    ]


def test_matching_request_retry_returns_existing_without_insert():
    conn = ScriptedConn([_row()])
    assert repo.insert_decision(conn, _create()).id == DECISION
    assert not any("INSERT INTO" in sql for sql, _ in conn.calls)


def test_request_retry_with_different_payload_is_conflict():
    with pytest.raises(repo.ReviewRequestConflict, match="different immutable"):
        repo.ensure_retry_matches(_record(), _create(decision_type="rejected"))


def test_database_integrity_failure_is_scoped_and_nested_savepoint_is_released():
    conn = ScriptedConn([None, IntegrityFailure("database constraint")])
    with pytest.raises(repo.ReviewIntegrityError, match="immutable database"):
        repo.insert_decision(conn, _create())
    assert [sql for sql, _ in conn.calls][-2:] == [
        "ROLLBACK TO SAVEPOINT research_evidence_review_insert",
        "RELEASE SAVEPOINT research_evidence_review_insert",
    ]
