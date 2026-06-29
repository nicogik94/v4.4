"""Feature-gate, transaction, evaluation, and read tests for R1.6."""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_evidence import binding_repository as repo  # noqa: E402
from research_evidence import binding_service as service  # noqa: E402
from research_evidence.binding_models import (  # noqa: E402
    ResearchEvidenceConsumerInputBindingCreate,
    ResearchEvidenceConsumerInputBindingRecord,
)


PROJECT = "00000000-0000-0000-0000-000000000001"
EVIDENCE_ITEM = "00000000-0000-0000-0000-000000000002"
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class FakeConn:
    def __init__(self, *, autocommit=False):
        self.autocommit = autocommit
        self.statements = []
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
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
            "id": PROJECT,
            "calculation_kind": None,
            "source_snapshot_id": PROJECT,
            "source_blob_id": EVIDENCE_ITEM,
            "source_metadata_revision_id": PROJECT,
            "candidate_fact_revision_id": EVIDENCE_ITEM,
            "fact_metadata_revision_id": PROJECT,
            "availability_status": False,
            "retention_basis": ({"event_id": "retention"},),
            "lineage_is_current": False,
            "lineage_basis": ({"identity": "successor"},),
            "review_status": "withdrawn",
            "freshness_status": "stale",
            "drift_status": "material_drift",
            "binding_sequence": 1,
            "evaluated_at": NOW,
        }
    )
    values.update(changes)
    return ResearchEvidenceConsumerInputBindingRecord(**values)


def test_feature_gate_fails_closed_before_connection_access(monkeypatch):
    monkeypatch.delenv("MAS_RESEARCH_EVIDENCE_ENABLED", raising=False)
    with pytest.raises(service.ResearchEvidenceBindingDisabled):
        service.record_consumer_input_binding(TripwireConn(), _create())


def test_invalid_input_and_autocommit_fail_before_repository_work(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    invalid = ResearchEvidenceConsumerInputBindingCreate.model_construct(
        **_create().model_dump(exclude={"request_id"}),
        request_id=" ",
    )
    with pytest.raises(ValidationError):
        service.record_consumer_input_binding(TripwireConn(), invalid)
    conn = FakeConn(autocommit=True)
    with pytest.raises(service.ResearchEvidenceBindingTransactionError):
        service.record_consumer_input_binding(conn, _create())
    assert conn.statements == []


def _allow_evaluation(monkeypatch):
    monkeypatch.setattr(
        repo, "get_binding_by_request_id", lambda *args, **kwargs: None
    )


def test_write_delegates_one_evaluation_and_uses_caller_savepoint(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    _allow_evaluation(monkeypatch)
    expected = _record()
    captured = {}

    def insert(conn, binding):
        captured["binding"] = binding
        return expected

    monkeypatch.setattr(repo, "insert_binding", insert)
    conn = FakeConn()
    assert service.record_consumer_input_binding(conn, _create()) == expected
    assert captured["binding"] == _create()
    assert [sql for sql, _ in conn.statements] == [
        "SAVEPOINT research_evidence_binding_write",
        "RELEASE SAVEPOINT research_evidence_binding_write",
    ]
    assert conn.commit_calls == conn.rollback_calls == conn.close_calls == 0


def test_service_performs_no_staged_source_status_reads(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    events = []
    monkeypatch.setattr(
        repo, "get_binding_by_request_id", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        repo,
        "insert_binding",
        lambda *args, **kwargs: events.append("one_statement_bundle")
        or _record(),
    )
    service.record_consumer_input_binding(FakeConn(), _create())
    assert events == ["one_statement_bundle"]


def test_calculation_and_pair_shapes_reach_one_statement_repository(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    _allow_evaluation(monkeypatch)
    inserted = []
    monkeypatch.setattr(
        repo,
        "insert_binding",
        lambda conn, binding: inserted.append(binding) or _record(),
    )

    calculation = _create(
        consumer_contract="deterministic_calculation",
        consumer_contract_version="automation-roi.v1",
        input_key="periods_per_year",
        approved_calculation_input_id=PROJECT,
    )
    service.record_consumer_input_binding(
        FakeConn(),
        calculation,
    )

    pair = _create(
        claim_intake_item_id=PROJECT,
        claim_support_assessment_id=EVIDENCE_ITEM,
    )
    service.record_consumer_input_binding(
        FakeConn(),
        pair,
    )
    assert inserted == [calculation, pair]


def test_matching_retry_does_not_reevaluate_status(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    existing = _record()
    monkeypatch.setattr(
        repo, "get_binding_by_request_id", lambda *args, **kwargs: existing
    )
    monkeypatch.setattr(
        repo,
        "insert_binding",
        lambda *args, **kwargs: pytest.fail("retry must not reevaluate"),
    )
    assert (
        service.record_consumer_input_binding(FakeConn(), _create())
        == existing
    )


def test_separate_reads_never_form_aggregate_readiness(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    monkeypatch.setattr(
        repo, "get_effective_binding", lambda *args, **kwargs: _record()
    )
    identity = {
        "project_id": PROJECT,
        "consumer_contract": "report_evidence_register",
        "binding_set_id": "register-1",
        "input_key": "entry-1",
    }
    conn = FakeConn()
    assert service.binding_availability_status(conn, **identity) is False
    assert service.binding_retention_basis(conn, **identity)[0]["event_id"]
    assert service.binding_lineage_is_current(conn, **identity) is False
    assert service.binding_review_status(conn, **identity) == "withdrawn"
    assert service.binding_freshness_status(conn, **identity) == "stale"
    assert service.binding_drift_status(conn, **identity) == "material_drift"
    assert service.binding_locator_resolution(conn, **identity) is None
    assert service.binding_evidence_linkage(conn, **identity) is None
    assert service.binding_semantic_relationship(conn, **identity) is None
    assert service.binding_consumer_disposition(conn, **identity) == "qualified"
    assert conn.statements == []
