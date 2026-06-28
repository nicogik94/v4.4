"""Unit tests for the R1.5 claim-support repository."""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_evidence import claim_support_repository as repo  # noqa: E402
from research_evidence.claim_support_models import (  # noqa: E402
    ResearchEvidenceClaimSupportAssessmentCreate,
    ResearchEvidenceClaimSupportAssessmentRecord,
)


PROJECT = "00000000-0000-0000-0000-000000000001"
CLAIM_ITEM = "00000000-0000-0000-0000-000000000002"
EVIDENCE_ITEM = "00000000-0000-0000-0000-000000000003"
ASSESSMENT = "00000000-0000-0000-0000-000000000004"
IDS = [
    f"00000000-0000-0000-0000-{value:012d}" for value in range(5, 14)
]
ASSESSED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


class Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row

    def fetchall(self):
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
        "claim_intake_item_id": CLAIM_ITEM,
        "evidence_intake_item_id": EVIDENCE_ITEM,
        "request_id": "request-1",
        "locator_resolution": "resolvable",
        "locator_rationale": "Reviewed locator",
        "evidence_linkage": "linked",
        "evidence_linkage_rationale": "Reviewed linkage",
        "semantic_relationship": "support",
        "semantic_relationship_rationale": "Reviewed relationship",
        "assessed_by": "operator",
    }
    values.update(changes)
    return ResearchEvidenceClaimSupportAssessmentCreate(**values)


def _record(**changes):
    values = _create().model_dump()
    values.update(
        {
            "id": ASSESSMENT,
            "assessment_sequence": 1,
            "supersedes_assessment_id": None,
            "claim_draft_id": IDS[0],
            "claim_source_snapshot_id": IDS[1],
            "claim_source_blob_id": IDS[2],
            "claim_source_metadata_revision_id": IDS[3],
            "evidence_source_snapshot_id": IDS[4],
            "evidence_source_blob_id": IDS[5],
            "evidence_source_metadata_revision_id": IDS[6],
            "candidate_fact_revision_id": IDS[7],
            "fact_metadata_revision_id": IDS[8],
            "assessed_at": ASSESSED_AT,
        }
    )
    values.update(changes)
    return ResearchEvidenceClaimSupportAssessmentRecord(**values)


def _row(record=None):
    record = record or _record()
    return tuple(record.model_dump().get(field) for field in (
        "id",
        "project_id",
        "claim_intake_item_id",
        "evidence_intake_item_id",
        "request_id",
        "locator_resolution",
        "locator_rationale",
        "evidence_linkage",
        "evidence_linkage_rationale",
        "semantic_relationship",
        "semantic_relationship_rationale",
        "assessed_by",
        "assessment_sequence",
        "supersedes_assessment_id",
        "claim_draft_id",
        "claim_source_snapshot_id",
        "claim_source_blob_id",
        "claim_source_metadata_revision_id",
        "evidence_source_snapshot_id",
        "evidence_source_blob_id",
        "evidence_source_metadata_revision_id",
        "candidate_fact_revision_id",
        "fact_metadata_revision_id",
        "assessed_at",
    ))


def test_pair_context_lookups_are_project_scoped_and_kind_specific():
    claim_row = (PROJECT, CLAIM_ITEM, IDS[0], IDS[1], IDS[2], IDS[3])
    evidence_row = (
        PROJECT, EVIDENCE_ITEM, IDS[4], IDS[5], IDS[6], IDS[7], IDS[8]
    )
    conn = ScriptedConn([claim_row, evidence_row])
    claim, evidence = repo.require_pair_context(
        conn,
        project_id=PROJECT,
        claim_intake_item_id=CLAIM_ITEM,
        evidence_intake_item_id=EVIDENCE_ITEM,
    )
    assert claim.claim_draft_id == IDS[0]
    assert evidence.candidate_fact_revision_id == IDS[7]
    assert conn.calls[0][1] == (CLAIM_ITEM, PROJECT)
    assert conn.calls[1][1] == (EVIDENCE_ITEM, PROJECT)
    assert "item.item_kind = 'claim_draft'" in conn.calls[0][0]
    assert "item.item_kind = 'candidate_fact'" in conn.calls[1][0]


def test_missing_or_wrong_kind_endpoint_is_scoped_parent_error():
    with pytest.raises(repo.ClaimSupportParentNotFound, match="claim-draft"):
        repo.get_claim_endpoint_context(
            ScriptedConn([None]),
            project_id=PROJECT,
            claim_intake_item_id=CLAIM_ITEM,
        )
    with pytest.raises(repo.ClaimSupportParentNotFound, match="candidate-fact"):
        repo.get_evidence_endpoint_context(
            ScriptedConn([None]),
            project_id=PROJECT,
            evidence_intake_item_id=EVIDENCE_ITEM,
        )


def test_effective_assessment_uses_pair_and_server_sequence():
    conn = ScriptedConn([_row()])
    assert repo.get_effective_assessment(
        conn,
        project_id=PROJECT,
        claim_intake_item_id=CLAIM_ITEM,
        evidence_intake_item_id=EVIDENCE_ITEM,
    ) == _record()
    sql = conn.calls[0][0]
    assert "ORDER BY assessment_sequence DESC" in sql
    assert "assessed_at DESC" not in sql


def test_insert_omits_server_fields_and_uses_repository_savepoint():
    conn = ScriptedConn([None, _row()])
    assert repo.insert_assessment(conn, _create()).id == ASSESSMENT
    insert = next(sql for sql, _ in conn.calls if "INSERT INTO" in sql)
    columns = insert.split("VALUES", 1)[0]
    for field in (
        "assessment_sequence",
        "supersedes_assessment_id",
        "claim_draft_id",
        "candidate_fact_revision_id",
        "assessed_at",
    ):
        assert field not in columns
    assert [sql for sql, _ in conn.calls if "SAVEPOINT" in sql] == [
        "SAVEPOINT research_evidence_claim_support_insert",
        "RELEASE SAVEPOINT research_evidence_claim_support_insert",
    ]


def test_matching_retry_returns_existing_and_changed_dimension_conflicts():
    conn = ScriptedConn([_row()])
    assert repo.insert_assessment(conn, _create()) == _record()
    assert not any("INSERT INTO" in sql for sql, _ in conn.calls)
    with pytest.raises(repo.ClaimSupportRequestConflict, match="different immutable"):
        repo.ensure_retry_matches(
            _record(), _create(semantic_relationship="contradiction")
        )


def test_integrity_failure_is_scoped_and_savepoint_released():
    conn = ScriptedConn([None, IntegrityFailure("constraint")])
    with pytest.raises(repo.ClaimSupportIntegrityError, match="immutable database"):
        repo.insert_assessment(conn, _create())
    assert [sql for sql, _ in conn.calls][-2:] == [
        "ROLLBACK TO SAVEPOINT research_evidence_claim_support_insert",
        "RELEASE SAVEPOINT research_evidence_claim_support_insert",
    ]
