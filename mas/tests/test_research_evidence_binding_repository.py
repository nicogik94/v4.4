"""Unit tests for the R1.6 binding repository."""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_evidence import binding_repository as repo  # noqa: E402
from knowledge.evidence_snapshot import repository as evidence_repo  # noqa: E402
from research_evidence.binding_models import (  # noqa: E402
    ResearchEvidenceConsumerInputBindingCreate,
    ResearchEvidenceConsumerInputBindingRecord,
)


PROJECT = "00000000-0000-0000-0000-000000000001"
EVIDENCE_ITEM = "00000000-0000-0000-0000-000000000002"
BINDING_ID = "00000000-0000-0000-0000-000000000003"
IDS = [f"00000000-0000-0000-0000-{n:012d}" for n in range(4, 20)]
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


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
        if isinstance(sql, str) and sql.startswith(
            ("SAVEPOINT ", "RELEASE ", "ROLLBACK ")
        ):
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
        "consumer_contract": "report_evidence_register",
        "consumer_contract_version": "report.v1",
        "binding_set_id": "register-1",
        "input_key": "entry-1",
        "request_id": "request-1",
        "evidence_intake_item_id": EVIDENCE_ITEM,
        "policy_identifier": "policy",
        "policy_version": "1",
        "policy_parameters_json": {"stale": "qualified"},
        "evaluator_version": "evaluator.v1",
        "freshness_as_of": NOW,
        "consumer_disposition": "qualified",
        "disposition_reasons": ("stale",),
        "evaluated_by": "operator",
    }
    values.update(changes)
    return ResearchEvidenceConsumerInputBindingCreate(**values)


def _record(**changes):
    values = _create().model_dump()
    values.update(
        {
            "id": BINDING_ID,
            "calculation_kind": None,
            "source_snapshot_id": IDS[0],
            "source_blob_id": IDS[1],
            "source_metadata_revision_id": IDS[2],
            "candidate_fact_revision_id": IDS[3],
            "fact_metadata_revision_id": IDS[4],
            "availability_status": True,
            "retention_basis": (),
            "lineage_is_current": True,
            "lineage_basis": (),
            "review_decision_id": IDS[5],
            "review_decision_sequence": 1,
            "review_status": "approved",
            "freshness_assessment_id": IDS[6],
            "freshness_assessment_sequence": 1,
            "fresh_through": NOW,
            "freshness_status": "fresh",
            "drift_status": "no_material_drift",
            "locator_resolution": None,
            "evidence_linkage": None,
            "semantic_relationship": None,
            "binding_sequence": 1,
            "supersedes_binding_id": None,
            "evaluated_at": NOW,
        }
    )
    values.update(changes)
    return ResearchEvidenceConsumerInputBindingRecord(**values)


FIELDS = (
    "id", "project_id", "consumer_contract", "consumer_contract_version",
    "binding_set_id", "input_key", "request_id", "evidence_intake_item_id",
    "approved_calculation_input_id", "calculation_kind",
    "observation_identity_version", "observation_identity_fingerprint",
    "claim_intake_item_id", "claim_support_assessment_id",
    "policy_identifier", "policy_version", "policy_parameters_json",
    "policy_fingerprint", "evaluator_version", "freshness_as_of",
    "consumer_disposition", "disposition_reasons", "evaluated_by",
    "source_snapshot_id", "source_blob_id", "source_metadata_revision_id",
    "candidate_fact_revision_id", "fact_metadata_revision_id",
    "availability_status", "retention_basis", "lineage_is_current",
    "lineage_basis", "review_decision_id", "review_decision_sequence",
    "review_status", "freshness_assessment_id",
    "freshness_assessment_sequence", "fresh_through", "freshness_status",
    "drift_status", "locator_resolution", "evidence_linkage",
    "semantic_relationship", "binding_sequence", "supersedes_binding_id",
    "evaluated_at",
)


def _row(record=None):
    record = record or _record()
    return tuple(record.model_dump().get(field) for field in FIELDS)


def _sql_text(statement):
    return (
        statement
        if isinstance(statement, str)
        else statement.as_string()
    )


def test_canonical_availability_component_is_composable_and_parameter_safe():
    component = evidence_repo.fact_availability_sql(
        sql.Placeholder(),
        fact_id_params=(IDS[3],),
    )
    assert component.params == (["tombstone", "redact"], IDS[3])
    assert IDS[3] not in _sql_text(component.expression)
    with pytest.raises(TypeError, match="Identifier or Placeholder"):
        evidence_repo.fact_availability_sql("unsafe SQL")
    with pytest.raises(TypeError, match="Identifier or Placeholder"):
        evidence_repo.fact_availability_sql(sql.SQL("unsafe SQL"))


def test_effective_binding_uses_scope_and_server_sequence():
    conn = ScriptedConn([_row()])
    result = repo.get_effective_binding(
        conn,
        project_id=PROJECT,
        consumer_contract="report_evidence_register",
        binding_set_id="register-1",
        input_key="entry-1",
    )
    assert result == _record()
    assert "ORDER BY binding_sequence DESC" in conn.calls[0][0]
    assert "evaluated_at DESC" not in conn.calls[0][0]


def test_insert_gathers_entire_status_bundle_in_one_statement_and_savepoint():
    conn = ScriptedConn([None, _row()])
    assert repo.insert_binding(conn, _create()).id == BINDING_ID
    status_calls = [
        (_sql_text(statement), params)
        for statement, params in conn.calls
        if "evaluated_context AS MATERIALIZED" in _sql_text(statement)
    ]
    assert len(status_calls) == 1
    insert, params = status_calls[0]
    assert params[-1] == ["tombstone", "redact"]
    assert PROJECT not in insert
    assert EVIDENCE_ITEM not in insert
    assert "LOCK TABLE" not in insert
    for table in (
        "evidence_retention_event",
        "research_source_metadata_revision",
        "research_fact_metadata_revision",
        "research_evidence_event",
        "research_evidence_intake_item_review_decision",
        "research_evidence_intake_item_freshness_assessment",
        "research_evidence_claim_support_assessment",
    ):
        assert table in insert
    columns = insert.split(
        "INSERT INTO research_evidence_consumer_input_binding", 1
    )[1].split("SELECT", 1)[0]
    for field in (
        "source_snapshot_id",
        "review_decision_id",
        "freshness_assessment_id",
        "availability_status",
        "lineage_is_current",
    ):
        assert field in columns
    for field in (
        "binding_sequence",
        "supersedes_binding_id",
        "evaluated_at",
    ):
        assert field not in columns
    for field in (
        "source_snapshot_id",
        "source_blob_id",
        "candidate_fact_revision_id",
    ):
        assert f"context.{field}" in insert
    assert [
        _sql_text(statement)
        for statement, _ in conn.calls
        if "SAVEPOINT" in _sql_text(statement)
    ] == [
        "SAVEPOINT research_evidence_binding_insert",
        "RELEASE SAVEPOINT research_evidence_binding_insert",
    ]


def test_matching_retry_returns_existing_and_changed_payload_conflicts():
    conn = ScriptedConn([_row()])
    assert repo.insert_binding(conn, _create()) == _record()
    assert not any(
        "INSERT INTO" in _sql_text(statement)
        for statement, _ in conn.calls
    )
    with pytest.raises(repo.BindingRequestConflict, match="different immutable"):
        repo.ensure_retry_matches(
            _record(), _create(consumer_disposition="does_not_meet_contract")
        )


def test_integrity_failure_is_scoped_and_savepoint_released():
    conn = ScriptedConn([None, IntegrityFailure("constraint")])
    with pytest.raises(repo.BindingIntegrityError, match="immutable database"):
        repo.insert_binding(conn, _create())
    assert [sql for sql, _ in conn.calls][-2:] == [
        "ROLLBACK TO SAVEPOINT research_evidence_binding_insert",
        "RELEASE SAVEPOINT research_evidence_binding_insert",
    ]
