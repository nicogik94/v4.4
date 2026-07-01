"""Repository tests for database-authoritative R1.7 writes."""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_evidence import scenario_input_evaluation_repository as repo  # noqa: E402
from research_evidence.scenario_input_evaluation_models import (  # noqa: E402
    OpaqueHypothesisDescriptor,
    ScenarioInputBindingSelection,
    ScenarioInputEvaluationRequest,
    ScenarioInputManifestRegistration,
)


PROJECT = "00000000-0000-0000-0000-000000000001"
MANIFEST = "00000000-0000-0000-0000-000000000002"
BINDING = "00000000-0000-0000-0000-000000000003"
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class ScriptedConn:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    def execute(self, statement, params=None):
        self.calls.append((statement, params))
        if not self.rows:
            raise AssertionError(f"unexpected query: {statement}")
        value = self.rows.pop(0)
        if isinstance(value, Exception):
            raise value
        return Result(value)


class DatabaseFailure(Exception):
    sqlstate = "23514"
    diag = None


def _request():
    return ScenarioInputEvaluationRequest(
        project_id=PROJECT,
        request_id="evaluation-request",
        manifest_id=MANIFEST,
        descriptor=OpaqueHypothesisDescriptor(
            namespace="opaque.namespace",
            descriptor_version="draft-v1",
            descriptor={"not": "canonical"},
            declared_by="operator",
        ),
        selected_bindings=(
            ScenarioInputBindingSelection(
                binding_id=BINDING,
                dependence_declaration="not_assessed",
                rationale="Not assessed.",
            ),
        ),
        freshness_as_of=NOW,
    )


def test_manifest_registration_calls_only_database_registration_function(
    monkeypatch,
):
    expected = object()
    monkeypatch.setattr(repo, "get_manifest", lambda *args, **kwargs: expected)
    conn = ScriptedConn([(MANIFEST,)])
    registration = ScenarioInputManifestRegistration(
        project_id=PROJECT,
        request_id="manifest-request",
        namespace="scenario.inputs",
        version="1",
        input_keys=("second", "first"),
        registered_by="operator",
    )
    assert repo.register_manifest(conn, registration) is expected
    statement, params = conn.calls[0]
    assert "research_evidence_register_scenario_input_manifest" in statement
    assert "cardinality" not in statement
    assert "fingerprint" not in statement
    assert params[4] == '["second","first"]'


def test_evaluation_sends_one_json_payload_and_uses_no_effective_lookup(
    monkeypatch,
):
    expected = object()
    monkeypatch.setattr(repo, "get_evaluation", lambda *args, **kwargs: expected)
    conn = ScriptedConn([("00000000-0000-0000-0000-000000000004",)])
    assert repo.create_evaluation(conn, _request()) is expected
    statement, params = conn.calls[0]
    assert "research_evidence_create_scenario_input_evaluation" in statement
    assert len(params) == 1
    assert BINDING in params[0]
    repository_text = Path(repo.__file__).read_text(encoding="utf-8")
    assert "get_effective_binding" not in repository_text
    assert "ORDER BY binding_sequence DESC" not in repository_text
    assert "latest" not in repository_text.lower()


def test_database_structural_failures_are_scoped():
    conn = ScriptedConn([DatabaseFailure("structural mismatch")])
    with pytest.raises(
        repo.ScenarioInputEvaluationIntegrityError,
        match="immutable database contract",
    ):
        repo.create_evaluation(conn, _request())


@pytest.mark.parametrize(
    ("message", "error_type"),
    [
        (
            "immutable manifest request conflict",
            repo.ScenarioInputManifestRequestConflict,
        ),
        (
            "immutable evaluation request conflict",
            repo.ScenarioInputEvaluationRequestConflict,
        ),
    ],
)
def test_immutable_request_conflicts_are_distinct(message, error_type):
    exc = DatabaseFailure(message)
    with pytest.raises(error_type):
        repo._raise_scoped(exc, manifest="manifest" in message)
