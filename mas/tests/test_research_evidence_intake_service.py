"""Feature-gate and transaction-boundary tests for R1.2 intake services."""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_evidence import intake_repository as repo  # noqa: E402
from research_evidence import intake_service as service  # noqa: E402
from research_evidence.intake_models import (  # noqa: E402
    ResearchEvidenceIntakeCreate,
    ResearchEvidenceIntakeItemCreate,
    ResearchEvidenceIntakeRecord,
)


PROJECT = "00000000-0000-0000-0000-000000000001"
SNAPSHOT = "00000000-0000-0000-0000-000000000002"
SOURCE_METADATA = "00000000-0000-0000-0000-000000000003"
INTAKE = "00000000-0000-0000-0000-000000000004"
FACT = "00000000-0000-0000-0000-000000000005"
FACT_METADATA = "00000000-0000-0000-0000-000000000006"


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


def _intake_create():
    return ResearchEvidenceIntakeCreate(
        project_id=PROJECT,
        source_snapshot_id=SNAPSHOT,
        source_metadata_revision_id=SOURCE_METADATA,
        selection_reason="Operator selected",
        created_by="operator",
    )


def _intake_record():
    return ResearchEvidenceIntakeRecord(
        **_intake_create().model_dump(),
        id=INTAKE,
        intake_method="operator_selected_existing_snapshot",
        state="draft",
        created_at=datetime.now(timezone.utc),
    )


def _fact_item():
    return ResearchEvidenceIntakeItemCreate(
        project_id=PROJECT,
        research_evidence_intake_id=INTAKE,
        item_kind="candidate_fact",
        candidate_fact_revision_id=FACT,
        fact_metadata_revision_id=FACT_METADATA,
        created_by="operator",
    )


def test_feature_gate_fails_closed_before_connection_access(monkeypatch):
    monkeypatch.delenv("MAS_RESEARCH_EVIDENCE_ENABLED", raising=False)
    with pytest.raises(service.ResearchEvidenceIntakeDisabled):
        service.create_intake(TripwireConn(), _intake_create())


def test_autocommit_is_rejected_before_database_write(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    conn = FakeConn(autocommit=True)
    with pytest.raises(service.ResearchEvidenceIntakeTransactionError):
        service.create_intake(conn, _intake_create())
    assert conn.statements == []


def test_invalid_operator_fields_are_revalidated_before_connection_access(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    invalid_intake = ResearchEvidenceIntakeCreate.model_construct(
        project_id=PROJECT,
        source_snapshot_id=SNAPSHOT,
        source_metadata_revision_id=SOURCE_METADATA,
        selection_reason=" ",
        created_by="operator",
    )
    with pytest.raises(ValidationError):
        service.create_intake(TripwireConn(), invalid_intake)

    invalid_item = ResearchEvidenceIntakeItemCreate.model_construct(
        project_id=PROJECT,
        research_evidence_intake_id=INTAKE,
        item_kind="candidate_fact",
        candidate_fact_revision_id=FACT,
        fact_metadata_revision_id=None,
        claim_draft_id=None,
        created_by="operator",
    )
    with pytest.raises(ValidationError):
        service.create_intake_item(TripwireConn(), invalid_item)


def test_create_intake_uses_savepoint_and_never_owns_connection(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    conn = FakeConn()
    expected = _intake_record()
    monkeypatch.setattr(repo, "snapshot_is_available", lambda *args, **kwargs: True)
    monkeypatch.setattr(repo, "insert_intake", lambda *args, **kwargs: expected)

    result = service.create_intake(conn, _intake_create())

    assert result == expected
    assert [statement[0] for statement in conn.statements] == [
        "SAVEPOINT research_evidence_intake_write",
        "RELEASE SAVEPOINT research_evidence_intake_write",
    ]
    assert conn.commit_calls == conn.rollback_calls == conn.close_calls == 0


def test_unavailable_snapshot_rolls_back_only_to_savepoint(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    conn = FakeConn()
    monkeypatch.setattr(repo, "snapshot_is_available", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        repo,
        "insert_intake",
        lambda *args, **kwargs: pytest.fail("unavailable snapshot must not insert"),
    )

    with pytest.raises(service.ResearchEvidenceSnapshotUnavailable):
        service.create_intake(conn, _intake_create())

    assert [statement[0] for statement in conn.statements] == [
        "SAVEPOINT research_evidence_intake_write",
        "ROLLBACK TO SAVEPOINT research_evidence_intake_write",
        "RELEASE SAVEPOINT research_evidence_intake_write",
    ]
    assert conn.rollback_calls == conn.commit_calls == conn.close_calls == 0


def test_item_rechecks_availability_and_derives_snapshot(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    conn = FakeConn()
    intake = _intake_record()
    captured = {}

    monkeypatch.setattr(repo, "get_intake", lambda *args, **kwargs: intake)
    monkeypatch.setattr(repo, "snapshot_is_available", lambda *args, **kwargs: True)

    def fake_insert(_conn, item, *, source_snapshot_id):
        captured["item"] = item
        captured["source_snapshot_id"] = source_snapshot_id
        return "created"

    monkeypatch.setattr(repo, "insert_item", fake_insert)
    assert service.create_intake_item(conn, _fact_item()) == "created"
    assert captured["source_snapshot_id"] == SNAPSHOT
    assert "source_snapshot_id" not in type(captured["item"]).model_fields


def test_item_rejects_missing_project_scoped_intake(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    conn = FakeConn()
    monkeypatch.setattr(repo, "get_intake", lambda *args, **kwargs: None)
    with pytest.raises(repo.IntakeParentNotFound, match="intake"):
        service.create_intake_item(conn, _fact_item())
    assert "ROLLBACK TO SAVEPOINT" in conn.statements[1][0]


def test_item_rechecks_unavailable_snapshot_before_insert(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    conn = FakeConn()
    monkeypatch.setattr(repo, "get_intake", lambda *args, **kwargs: _intake_record())
    monkeypatch.setattr(repo, "snapshot_is_available", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        repo,
        "insert_item",
        lambda *args, **kwargs: pytest.fail("unavailable snapshot must not bind"),
    )
    with pytest.raises(service.ResearchEvidenceSnapshotUnavailable):
        service.create_intake_item(conn, _fact_item())


def test_repository_failure_preserves_outer_transaction_and_connection(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    conn = FakeConn()
    monkeypatch.setattr(repo, "snapshot_is_available", lambda *args, **kwargs: True)

    def fail(*args, **kwargs):
        raise repo.IntakeIntegrityError("duplicate")

    monkeypatch.setattr(repo, "insert_intake", fail)
    with pytest.raises(repo.IntakeIntegrityError, match="duplicate"):
        service.create_intake(conn, _intake_create())

    assert [statement[0] for statement in conn.statements][-2:] == [
        "ROLLBACK TO SAVEPOINT research_evidence_intake_write",
        "RELEASE SAVEPOINT research_evidence_intake_write",
    ]
    assert conn.rollback_calls == conn.commit_calls == conn.close_calls == 0
