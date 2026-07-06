import pytest
from pydantic import ValidationError

from research_evidence import automation_roi_execution_repository as repository
from research_evidence import automation_roi_execution_service as service
from research_evidence.automation_roi_execution_models import (
    AutomationRoiExecutionRequest,
)


PROJECT = "00000000-0000-0000-0000-000000000001"
SNAPSHOT = "00000000-0000-0000-0000-000000000002"


class FakeConn:
    def __init__(self, autocommit=False):
        self.autocommit = autocommit
        self.calls = []

    def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        return self


class Tripwire:
    def __getattribute__(self, name):
        if name.startswith("__"):
            return super().__getattribute__(name)
        raise AssertionError("connection must not be accessed")


def _request():
    return AutomationRoiExecutionRequest(
        project_id=PROJECT, input_snapshot_id=SNAPSHOT, idempotency_key="key-1"
    )


def test_disabled_and_invalid_requests_fail_before_io(monkeypatch):
    monkeypatch.delenv("MAS_RESEARCH_EVIDENCE_ENABLED", raising=False)
    with pytest.raises(service.AutomationRoiExecutionDisabled):
        service.execute_automation_roi(Tripwire(), _request(), server_actor="server")
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    invalid = AutomationRoiExecutionRequest.model_construct(
        project_id=PROJECT, input_snapshot_id=SNAPSHOT, idempotency_key=" "
    )
    with pytest.raises(ValidationError):
        service.execute_automation_roi(Tripwire(), invalid, server_actor="server")


def test_service_derives_actor_outside_request_and_owns_savepoint(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    expected = object()
    captured = {}

    def execute(conn, request, *, requested_by):
        captured["requested_by"] = requested_by
        return expected

    monkeypatch.setattr(repository, "execute", execute)
    conn = FakeConn()
    assert (
        service.execute_automation_roi(
            conn, _request(), server_actor=" authenticated-service "
        )
        is expected
    )
    assert captured == {"requested_by": "authenticated-service"}
    assert [sql for sql, _ in conn.calls] == [
        "SAVEPOINT research_evidence_automation_roi_execution_service",
        "RELEASE SAVEPOINT research_evidence_automation_roi_execution_service",
    ]


def test_autocommit_and_blank_server_actor_are_rejected(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    with pytest.raises(service.AutomationRoiExecutionTransactionError):
        service.execute_automation_roi(
            FakeConn(autocommit=True), _request(), server_actor="server"
        )
    with pytest.raises(ValueError):
        service.execute_automation_roi(FakeConn(), _request(), server_actor=" ")
