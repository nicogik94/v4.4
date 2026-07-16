"""Feature-gate and caller-transaction tests for R1.7."""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_evidence import scenario_input_evaluation_repository as repo  # noqa: E402
from research_evidence import scenario_input_evaluation_service as service  # noqa: E402
from research_evidence.scenario_input_evaluation_models import (  # noqa: E402
    OpaqueHypothesisDescriptor,
    ScenarioInputBindingSelection,
    ScenarioInputEvaluationRequest,
    ScenarioInputManifestRegistration,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class FakeConn:
    def __init__(self, *, autocommit=False):
        self.autocommit = autocommit
        self.calls = []
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0

    def execute(self, statement, params=None):
        self.calls.append((statement, params))
        return self

    def commit(self):
        self.commit_calls += 1

    def rollback(self):
        self.rollback_calls += 1

    def close(self):
        self.close_calls += 1


class TripwireConn:
    def __getattribute__(self, name):
        if name.startswith("__"):
            return super().__getattribute__(name)
        raise AssertionError("connection must not be accessed")


def _manifest():
    return ScenarioInputManifestRegistration(
        project_id="project",
        request_id="manifest-request",
        namespace="scenario.inputs",
        version="1",
        input_keys=("input",),
        registered_by="operator",
    )


def _request():
    return ScenarioInputEvaluationRequest(
        project_id="project",
        request_id="evaluation-request",
        manifest_id="manifest",
        descriptor=OpaqueHypothesisDescriptor(
            namespace="opaque",
            descriptor_version="1",
            descriptor={"caller": "declared"},
            declared_by="operator",
        ),
        selected_bindings=(
            ScenarioInputBindingSelection(
                binding_id="binding",
                dependence_declaration="declared_dependent",
                rationale="Declared only; not inferred.",
            ),
        ),
        freshness_as_of=NOW,
    )


@pytest.mark.parametrize(
    ("operation", "payload"),
    [
        (service.register_scenario_input_manifest, _manifest()),
        (service.create_scenario_input_evaluation, _request()),
    ],
)
def test_feature_gate_defaults_off_before_connection_access(
    monkeypatch, operation, payload
):
    monkeypatch.delenv("MAS_RESEARCH_EVIDENCE_ENABLED", raising=False)
    with pytest.raises(service.ScenarioInputEvaluationDisabled):
        operation(TripwireConn(), payload)


def test_invalid_payload_and_autocommit_fail_before_repository(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    invalid = _request().model_copy(update={"request_id": " "})
    with pytest.raises(ValidationError):
        service.create_scenario_input_evaluation(TripwireConn(), invalid)
    conn = FakeConn(autocommit=True)
    with pytest.raises(service.ScenarioInputEvaluationTransactionError):
        service.create_scenario_input_evaluation(conn, _request())
    assert conn.calls == []


def test_services_use_savepoints_without_owning_connection(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    marker = object()
    monkeypatch.setattr(repo, "register_manifest", lambda *args: marker)
    monkeypatch.setattr(repo, "create_evaluation", lambda *args: marker)
    conn = FakeConn()
    assert service.register_scenario_input_manifest(conn, _manifest()) is marker
    assert service.create_scenario_input_evaluation(conn, _request()) is marker
    statements = [statement for statement, _ in conn.calls]
    assert statements == [
        "SAVEPOINT research_evidence_scenario_manifest_write",
        "RELEASE SAVEPOINT research_evidence_scenario_manifest_write",
        "SAVEPOINT research_evidence_scenario_evaluation_write",
        "RELEASE SAVEPOINT research_evidence_scenario_evaluation_write",
    ]
    assert conn.commit_calls == conn.rollback_calls == conn.close_calls == 0


def test_service_rolls_back_only_its_savepoint(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    monkeypatch.setattr(
        repo,
        "create_evaluation",
        lambda *args: (_ for _ in ()).throw(ValueError("database rejected")),
    )
    conn = FakeConn()
    with pytest.raises(ValueError, match="database rejected"):
        service.create_scenario_input_evaluation(conn, _request())
    assert [statement for statement, _ in conn.calls] == [
        "SAVEPOINT research_evidence_scenario_evaluation_write",
        "ROLLBACK TO SAVEPOINT research_evidence_scenario_evaluation_write",
        "RELEASE SAVEPOINT research_evidence_scenario_evaluation_write",
    ]
    assert conn.rollback_calls == 0
