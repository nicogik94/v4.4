"""Feature-gate and transaction tests for the R1.6A snapshot service."""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from research_evidence import automation_roi_use_repository as repo
from research_evidence import automation_roi_use_service as service
from research_evidence.automation_roi_use_models import (
    AutomationRoiInputSnapshotCreate,
)


IDS = tuple(f"00000000-0000-0000-0000-{n:012d}" for n in range(1, 7))
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class FakeConn:
    def __init__(self, autocommit=False):
        self.autocommit = autocommit
        self.statements = []

    def execute(self, statement, params=None):
        self.statements.append((statement, params))
        return self


class TripwireConn:
    def __getattribute__(self, name):
        if name.startswith("__"):
            return super().__getattribute__(name)
        raise AssertionError("connection must not be accessed")


def _command(**changes):
    values = {
        "project_id": IDS[0],
        "binding_set_id": "set-1",
        "binding_record_ids": IDS,
        "request_id": "request-1",
        "freshness_as_of": NOW,
        "evaluated_by": "operator",
    }
    values.update(changes)
    return AutomationRoiInputSnapshotCreate(**values)


def test_feature_gate_and_validation_fail_before_connection_access(monkeypatch):
    monkeypatch.delenv("MAS_RESEARCH_EVIDENCE_ENABLED", raising=False)
    with pytest.raises(service.AutomationRoiUseDisabled):
        service.record_automation_roi_input_snapshot(TripwireConn(), _command())

    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    invalid = AutomationRoiInputSnapshotCreate.model_construct(
        **_command().model_dump(exclude={"request_id"}), request_id=" "
    )
    with pytest.raises(ValidationError):
        service.record_automation_roi_input_snapshot(TripwireConn(), invalid)


def test_autocommit_is_rejected_without_repository_work(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    conn = FakeConn(autocommit=True)
    with pytest.raises(service.AutomationRoiUseTransactionError):
        service.record_automation_roi_input_snapshot(conn, _command())
    assert conn.statements == []


def test_service_uses_savepoint_and_idempotent_retry(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    expected = object()
    monkeypatch.setattr(
        repo, "_get_snapshot_by_request_id", lambda *a, **k: expected
    )
    monkeypatch.setattr(
        repo, "ensure_retry_matches", lambda existing, command: existing
    )
    monkeypatch.setattr(
        repo,
        "insert_snapshot",
        lambda *a, **k: pytest.fail("retry must not insert"),
    )
    conn = FakeConn()
    assert service.record_automation_roi_input_snapshot(conn, _command()) is expected
    assert [statement for statement, _ in conn.statements] == [
        "SAVEPOINT research_evidence_automation_roi_snapshot_write",
        "RELEASE SAVEPOINT research_evidence_automation_roi_snapshot_write",
    ]
