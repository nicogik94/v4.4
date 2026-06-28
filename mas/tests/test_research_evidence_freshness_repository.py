"""Unit tests for the R1.4 freshness repository."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_evidence import freshness_repository as repo  # noqa: E402
from research_evidence.freshness_models import (  # noqa: E402
    ResearchEvidenceIntakeItemFreshnessAssessmentCreate,
    ResearchEvidenceIntakeItemFreshnessAssessmentRecord,
)


PROJECT = "00000000-0000-0000-0000-000000000001"
ITEM = "00000000-0000-0000-0000-000000000002"
COMPARISON = "00000000-0000-0000-0000-000000000003"
ASSESSMENT = "00000000-0000-0000-0000-000000000004"
SNAPSHOT = "00000000-0000-0000-0000-000000000005"
BLOB = "00000000-0000-0000-0000-000000000006"
FACT = "00000000-0000-0000-0000-000000000007"
FACT_METADATA = "00000000-0000-0000-0000-000000000008"
COMPARISON_SNAPSHOT = "00000000-0000-0000-0000-000000000009"
COMPARISON_BLOB = "00000000-0000-0000-0000-000000000010"
COMPARISON_FACT = "00000000-0000-0000-0000-000000000011"
COMPARISON_FACT_METADATA = "00000000-0000-0000-0000-000000000012"
BASIS = datetime(2026, 1, 1, tzinfo=timezone.utc)


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
        "request_id": "request-1",
        "policy_identifier": "source-age",
        "policy_version": "1",
        "policy_parameters_json": {"max_age_days": 30},
        "policy_fingerprint": "sha256:policy",
        "evaluator_version": "evaluator-1",
        "basis_timestamp": BASIS,
        "fresh_through": BASIS + timedelta(days=30),
        "comparison_research_evidence_intake_item_id": COMPARISON,
        "drift_status": "no_material_drift",
        "drift_reason": "Reviewed",
        "assessed_by": "operator",
    }
    values.update(changes)
    return ResearchEvidenceIntakeItemFreshnessAssessmentCreate(**values)


def _record(**changes):
    values = _create().model_dump()
    values.update(
        {
            "id": ASSESSMENT,
            "assessment_sequence": 1,
            "supersedes_assessment_id": None,
            "source_snapshot_id": SNAPSHOT,
            "source_blob_id": BLOB,
            "candidate_fact_revision_id": FACT,
            "fact_metadata_revision_id": FACT_METADATA,
            "linked_hash_algorithm": "sha256",
            "linked_content_hash": "base-hash",
            "comparison_source_snapshot_id": COMPARISON_SNAPSHOT,
            "comparison_source_blob_id": COMPARISON_BLOB,
            "comparison_candidate_fact_revision_id": COMPARISON_FACT,
            "comparison_fact_metadata_revision_id": COMPARISON_FACT_METADATA,
            "comparison_hash_algorithm": "sha256",
            "comparison_content_hash": "comparison-hash",
            "content_change_detected": True,
            "assessed_at": BASIS + timedelta(hours=1),
        }
    )
    values.update(changes)
    return ResearchEvidenceIntakeItemFreshnessAssessmentRecord(**values)


def _row(record=None):
    record = record or _record()
    return (
        record.id,
        record.project_id,
        record.research_evidence_intake_item_id,
        record.request_id,
        record.policy_identifier,
        record.policy_version,
        record.policy_parameters_json,
        record.policy_fingerprint,
        record.evaluator_version,
        record.basis_timestamp,
        record.fresh_through,
        record.comparison_research_evidence_intake_item_id,
        record.drift_status,
        record.drift_reason,
        record.assessed_by,
        record.assessment_sequence,
        record.supersedes_assessment_id,
        record.source_snapshot_id,
        record.source_blob_id,
        record.candidate_fact_revision_id,
        record.fact_metadata_revision_id,
        record.linked_hash_algorithm,
        record.linked_content_hash,
        record.comparison_source_snapshot_id,
        record.comparison_source_blob_id,
        record.comparison_candidate_fact_revision_id,
        record.comparison_fact_metadata_revision_id,
        record.comparison_hash_algorithm,
        record.comparison_content_hash,
        record.content_change_detected,
        record.assessed_at,
    )


def test_item_kind_lookup_is_project_scoped():
    conn = ScriptedConn([("candidate_fact",)])
    assert (
        repo.get_item_kind(
            conn,
            project_id=PROJECT,
            research_evidence_intake_item_id=ITEM,
        )
        == "candidate_fact"
    )
    assert conn.calls[0][1] == (ITEM, PROJECT)


def test_missing_item_is_parent_error():
    with pytest.raises(repo.FreshnessParentNotFound, match="intake item"):
        repo.get_item_kind(
            ScriptedConn([None]),
            project_id=PROJECT,
            research_evidence_intake_item_id=ITEM,
        )


def test_effective_assessment_uses_server_sequence_only():
    conn = ScriptedConn([_row()])
    assert (
        repo.get_effective_assessment(
            conn,
            project_id=PROJECT,
            research_evidence_intake_item_id=ITEM,
        )
        == _record()
    )
    sql = conn.calls[0][0]
    assert "ORDER BY assessment_sequence DESC" in sql
    assert "assessed_at DESC" not in sql


def test_insert_omits_all_server_owned_fields_and_uses_savepoint():
    conn = ScriptedConn([None, _row()])
    assert repo.insert_assessment(conn, _create()).id == ASSESSMENT
    insert = next(sql for sql, _ in conn.calls if "INSERT INTO" in sql)
    columns = insert.split("VALUES", 1)[0]
    for field in (
        "assessment_sequence",
        "supersedes_assessment_id",
        "source_snapshot_id",
        "linked_content_hash",
        "content_change_detected",
        "assessed_at",
    ):
        assert field not in columns
    assert [sql for sql, _ in conn.calls if "SAVEPOINT" in sql] == [
        "SAVEPOINT research_evidence_freshness_insert",
        "RELEASE SAVEPOINT research_evidence_freshness_insert",
    ]


def test_matching_retry_returns_existing_and_mismatch_conflicts():
    conn = ScriptedConn([_row()])
    assert repo.insert_assessment(conn, _create()).id == ASSESSMENT
    assert not any("INSERT INTO" in sql for sql, _ in conn.calls)

    with pytest.raises(repo.FreshnessRequestConflict, match="different immutable"):
        repo.ensure_retry_matches(
            _record(), _create(drift_status="material_drift")
        )


def test_integrity_failure_is_scoped_and_savepoint_is_released():
    conn = ScriptedConn([None, IntegrityFailure("constraint")])
    with pytest.raises(repo.FreshnessIntegrityError, match="immutable database"):
        repo.insert_assessment(conn, _create())
    assert [sql for sql, _ in conn.calls][-2:] == [
        "ROLLBACK TO SAVEPOINT research_evidence_freshness_insert",
        "RELEASE SAVEPOINT research_evidence_freshness_insert",
    ]
