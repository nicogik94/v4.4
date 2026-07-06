from datetime import datetime, timezone
from decimal import Decimal

import pytest

from research_evidence import automation_roi_execution_repository as repository
from research_evidence.automation_roi_execution_models import (
    AutomationRoiExecutionRequest,
)


PROJECT = "00000000-0000-0000-0000-000000000001"
SNAPSHOT = "00000000-0000-0000-0000-000000000002"
RESULT = "00000000-0000-0000-0000-000000000003"
DIGEST = "a" * 64


class QueryResult:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


def _row(operation_digest=None):
    return (
        RESULT,
        PROJECT,
        SNAPSHOT,
        "deterministic_calculation",
        "set-1",
        "key-1",
        operation_digest or repository.operation_digest(PROJECT, SNAPSHOT),
        "server",
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        "automation_roi",
        "automation_roi.v1",
        "a" * 64,
        "automation_roi.assumptions.v1",
        {},
        {},
        DIGEST,
        DIGEST,
        {},
        "valid",
        "USD",
        Decimal("20800"),
        Decimal("19800"),
        Decimal("14800"),
        Decimal("296"),
        "computed",
        {},
    )


class FakeConn:
    autocommit = False

    def __init__(self):
        self.calls = []

    def execute(self, statement, params=None):
        text = str(statement)
        self.calls.append((text, params))
        if "research_evidence_execute_automation_roi" in text:
            return QueryResult((RESULT,))
        if "WHERE id =" in text:
            return QueryResult(_row())
        return QueryResult()


def _request():
    return AutomationRoiExecutionRequest(
        project_id=PROJECT, input_snapshot_id=SNAPSHOT, idempotency_key="key-1"
    )


def test_repository_uses_only_controlled_function_and_server_actor():
    conn = FakeConn()
    result = repository.execute(conn, _request(), requested_by="server")
    assert result.id == RESULT
    controlled = [
        call for call in conn.calls
        if "research_evidence_execute_automation_roi" in call[0]
    ]
    assert len(controlled) == 1
    assert controlled[0][1] == (PROJECT, SNAPSHOT, "key-1", "server")
    assert not any("INSERT INTO" in sql for sql, _ in conn.calls)
    assert conn.calls[0][0].startswith("SAVEPOINT ")
    assert conn.calls[-1][0].startswith("RELEASE SAVEPOINT ")


class DatabaseError(Exception):
    sqlstate = "23505"

    class diag:
        constraint_name = "uq_rearoicr_project_idempotency"


class ConflictConn(FakeConn):
    def execute(self, statement, params=None):
        text = str(statement)
        self.calls.append((text, params))
        if "research_evidence_execute_automation_roi" in text:
            raise DatabaseError()
        if "idempotency_key =" in text:
            return QueryResult(_row(operation_digest="b" * 64))
        return QueryResult()


def test_savepoint_conflict_is_typed():
    with pytest.raises(repository.AutomationRoiExecutionConflict):
        repository.execute(ConflictConn(), _request(), requested_by="server")
