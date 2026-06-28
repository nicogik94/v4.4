"""Feature-gate, transaction, and read-only tests for R1.4 freshness."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_evidence import freshness_repository as repo  # noqa: E402
from research_evidence import freshness_service as service  # noqa: E402
from research_evidence.freshness_models import (  # noqa: E402
    ResearchEvidenceIntakeItemFreshnessAssessmentCreate,
    ResearchEvidenceIntakeItemFreshnessAssessmentRecord,
)


PROJECT = "00000000-0000-0000-0000-000000000001"
ITEM = "00000000-0000-0000-0000-000000000002"
ASSESSMENT = "00000000-0000-0000-0000-000000000003"
BASIS = datetime(2026, 1, 1, tzinfo=timezone.utc)


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
        "research_evidence_intake_item_id": ITEM,
        "request_id": "request-1",
        "policy_identifier": "source-age",
        "policy_version": "1",
        "policy_parameters_json": {"max_age_days": 30},
        "policy_fingerprint": "",
        "evaluator_version": "evaluator-1",
        "basis_timestamp": BASIS,
        "fresh_through": BASIS + timedelta(days=30),
        "comparison_research_evidence_intake_item_id": None,
        "drift_status": "not_assessed",
        "drift_reason": "No comparison",
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
            "source_snapshot_id": PROJECT,
            "source_blob_id": ITEM,
            "candidate_fact_revision_id": ASSESSMENT,
            "fact_metadata_revision_id": PROJECT,
            "linked_hash_algorithm": "sha256",
            "linked_content_hash": "hash",
            "comparison_source_snapshot_id": None,
            "comparison_source_blob_id": None,
            "comparison_candidate_fact_revision_id": None,
            "comparison_fact_metadata_revision_id": None,
            "comparison_hash_algorithm": None,
            "comparison_content_hash": None,
            "content_change_detected": None,
            "assessed_at": BASIS + timedelta(hours=1),
        }
    )
    values.update(changes)
    return ResearchEvidenceIntakeItemFreshnessAssessmentRecord(**values)


def test_feature_gate_fails_closed_before_connection_access(monkeypatch):
    monkeypatch.delenv("MAS_RESEARCH_EVIDENCE_ENABLED", raising=False)
    with pytest.raises(service.ResearchEvidenceFreshnessDisabled):
        service.record_item_freshness_assessment(TripwireConn(), _create())


def test_invalid_input_and_autocommit_fail_before_database_work(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    invalid = ResearchEvidenceIntakeItemFreshnessAssessmentCreate.model_construct(
        **_create().model_dump(exclude={"request_id"}),
        request_id="\t",
    )
    with pytest.raises(ValidationError):
        service.record_item_freshness_assessment(TripwireConn(), invalid)

    conn = FakeConn(autocommit=True)
    with pytest.raises(service.ResearchEvidenceFreshnessTransactionError):
        service.record_item_freshness_assessment(conn, _create())
    assert conn.statements == []


def test_write_uses_caller_owned_savepoint_and_never_owns_connection(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    conn = FakeConn()
    expected = _record()
    monkeypatch.setattr(repo, "get_item_kind", lambda *args, **kwargs: "candidate_fact")
    monkeypatch.setattr(
        repo, "get_assessment_by_request_id", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(repo, "insert_assessment", lambda *args, **kwargs: expected)
    assert service.record_item_freshness_assessment(conn, _create()) == expected
    assert [sql for sql, _ in conn.statements] == [
        "SAVEPOINT research_evidence_freshness_write",
        "RELEASE SAVEPOINT research_evidence_freshness_write",
    ]
    assert conn.commit_calls == conn.rollback_calls == conn.close_calls == 0


def test_claim_draft_write_is_not_applicable_and_rolls_back_savepoint(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    conn = FakeConn()
    monkeypatch.setattr(repo, "get_item_kind", lambda *args, **kwargs: "claim_draft")
    with pytest.raises(service.ResearchEvidenceFreshnessNotApplicable):
        service.record_item_freshness_assessment(conn, _create())
    assert [sql for sql, _ in conn.statements] == [
        "SAVEPOINT research_evidence_freshness_write",
        "ROLLBACK TO SAVEPOINT research_evidence_freshness_write",
        "RELEASE SAVEPOINT research_evidence_freshness_write",
    ]


def test_matching_retry_is_returned_without_append(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    existing = _record()
    monkeypatch.setattr(repo, "get_item_kind", lambda *args, **kwargs: "candidate_fact")
    monkeypatch.setattr(
        repo,
        "get_assessment_by_request_id",
        lambda *args, **kwargs: existing,
    )
    monkeypatch.setattr(
        repo,
        "insert_assessment",
        lambda *args, **kwargs: pytest.fail("retry must not append"),
    )
    assert (
        service.record_item_freshness_assessment(FakeConn(), _create())
        == existing
    )


def test_read_only_status_uses_only_item_kind_sequence_and_fresh_through(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    conn = FakeConn()
    monkeypatch.setattr(repo, "get_item_kind", lambda *args, **kwargs: "candidate_fact")
    monkeypatch.setattr(
        repo, "get_effective_assessment", lambda *args, **kwargs: _record()
    )
    assert (
        service.item_freshness_status_as_of(
            conn,
            project_id=PROJECT,
            research_evidence_intake_item_id=ITEM,
            as_of=BASIS + timedelta(days=30),
        )
        == "fresh"
    )
    assert (
        service.item_freshness_status_as_of(
            conn,
            project_id=PROJECT,
            research_evidence_intake_item_id=ITEM,
            as_of=BASIS + timedelta(days=30, microseconds=1),
        )
        == "stale"
    )
    assert conn.statements == []


def test_read_only_status_handles_unknown_and_claim_without_writes(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    conn = FakeConn()
    monkeypatch.setattr(repo, "get_item_kind", lambda *args, **kwargs: "candidate_fact")
    monkeypatch.setattr(
        repo, "get_effective_assessment", lambda *args, **kwargs: None
    )
    assert (
        service.item_freshness_status_as_of(
            conn,
            project_id=PROJECT,
            research_evidence_intake_item_id=ITEM,
            as_of=BASIS,
        )
        == "unknown"
    )
    monkeypatch.setattr(repo, "get_item_kind", lambda *args, **kwargs: "claim_draft")
    assert (
        service.item_freshness_status_as_of(
            conn,
            project_id=PROJECT,
            research_evidence_intake_item_id=ITEM,
            as_of=BASIS,
        )
        == "not_applicable"
    )
    assert conn.statements == []


def test_read_only_status_rejects_naive_as_of_before_connection_access(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    with pytest.raises(ValueError, match="timezone"):
        service.item_freshness_status_as_of(
            TripwireConn(),
            project_id=PROJECT,
            research_evidence_intake_item_id=ITEM,
            as_of=BASIS.replace(tzinfo=None),
        )
