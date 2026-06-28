"""Feature-gate and transaction tests for R1.3 item review."""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_evidence import review_repository as repo  # noqa: E402
from research_evidence import review_service as service  # noqa: E402
from research_evidence.review_models import (  # noqa: E402
    ResearchEvidenceIntakeItemReviewDecisionCreate,
    ResearchEvidenceIntakeItemReviewDecisionRecord,
)


PROJECT = "00000000-0000-0000-0000-000000000001"
ITEM = "00000000-0000-0000-0000-000000000002"
DECISION = "00000000-0000-0000-0000-000000000003"


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
        "decision_type": "approved",
        "decision_reason": "Reviewed",
        "decided_by": "operator",
        "request_id": "request-1",
    }
    values.update(changes)
    return ResearchEvidenceIntakeItemReviewDecisionCreate(**values)


def _record(**changes):
    values = _create().model_dump()
    values.update(
        {
            "id": DECISION,
            "decision_sequence": 1,
            "supersedes_decision_id": None,
            "recorded_at": datetime.now(timezone.utc),
        }
    )
    values.update(changes)
    return ResearchEvidenceIntakeItemReviewDecisionRecord(**values)


def _context(available=True):
    return repo.ReviewItemContext(
        project_id=PROJECT,
        item_id=ITEM,
        intake_id="00000000-0000-0000-0000-000000000004",
        item_kind="candidate_fact",
        source_snapshot_id="00000000-0000-0000-0000-000000000005",
        source_metadata_revision_id="00000000-0000-0000-0000-000000000006",
        candidate_fact_revision_id="00000000-0000-0000-0000-000000000007",
        fact_metadata_revision_id="00000000-0000-0000-0000-000000000008",
        claim_draft_id=None,
        approval_available=available,
    )


def test_feature_gate_fails_closed_before_connection_access(monkeypatch):
    monkeypatch.delenv("MAS_RESEARCH_EVIDENCE_ENABLED", raising=False)
    with pytest.raises(service.ResearchEvidenceReviewDisabled):
        service.record_item_review_decision(TripwireConn(), _create())


def test_autocommit_is_rejected_before_database_access(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    conn = FakeConn(autocommit=True)
    with pytest.raises(service.ResearchEvidenceReviewTransactionError):
        service.record_item_review_decision(conn, _create())
    assert conn.statements == []


def test_invalid_public_input_is_revalidated_before_connection_access(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    invalid = ResearchEvidenceIntakeItemReviewDecisionCreate.model_construct(
        project_id=PROJECT,
        research_evidence_intake_item_id=ITEM,
        decision_type="approved",
        decision_reason="\t",
        decided_by="operator",
        request_id="request-1",
    )
    with pytest.raises(ValidationError):
        service.record_item_review_decision(TripwireConn(), invalid)


def test_approved_decision_uses_savepoint_and_never_owns_connection(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    conn = FakeConn()
    expected = _record()
    monkeypatch.setattr(repo, "get_item_context", lambda *args, **kwargs: _context())
    monkeypatch.setattr(
        repo, "get_decision_by_request_id", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(repo, "insert_decision", lambda *args, **kwargs: expected)

    assert service.record_item_review_decision(conn, _create()) == expected
    assert [sql for sql, _ in conn.statements] == [
        "SAVEPOINT research_evidence_review_write",
        "RELEASE SAVEPOINT research_evidence_review_write",
    ]
    assert conn.commit_calls == conn.rollback_calls == conn.close_calls == 0


def test_unavailable_approval_rolls_back_only_to_savepoint(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    conn = FakeConn()
    monkeypatch.setattr(
        repo, "get_item_context", lambda *args, **kwargs: _context(False)
    )
    monkeypatch.setattr(
        repo, "get_decision_by_request_id", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        repo,
        "insert_decision",
        lambda *args, **kwargs: pytest.fail("unavailable approval must not insert"),
    )
    with pytest.raises(service.ResearchEvidenceReviewUnavailable):
        service.record_item_review_decision(conn, _create())
    assert [sql for sql, _ in conn.statements] == [
        "SAVEPOINT research_evidence_review_write",
        "ROLLBACK TO SAVEPOINT research_evidence_review_write",
        "RELEASE SAVEPOINT research_evidence_review_write",
    ]
    assert conn.commit_calls == conn.rollback_calls == conn.close_calls == 0


@pytest.mark.parametrize(
    "decision_type", ["rejected", "needs_revision", "withdrawn"]
)
def test_negative_outcomes_are_allowed_when_evidence_is_unavailable(
    monkeypatch, decision_type
):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    expected = _record(decision_type=decision_type)
    monkeypatch.setattr(
        repo, "get_item_context", lambda *args, **kwargs: _context(False)
    )
    monkeypatch.setattr(
        repo, "get_decision_by_request_id", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(repo, "insert_decision", lambda *args, **kwargs: expected)
    assert (
        service.record_item_review_decision(
            FakeConn(), _create(decision_type=decision_type)
        )
        == expected
    )


def test_matching_retry_returns_existing_even_if_now_unavailable(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    existing = _record()
    monkeypatch.setattr(
        repo, "get_item_context", lambda *args, **kwargs: _context(False)
    )
    monkeypatch.setattr(
        repo, "get_decision_by_request_id", lambda *args, **kwargs: existing
    )
    monkeypatch.setattr(
        repo,
        "insert_decision",
        lambda *args, **kwargs: pytest.fail("retry must not append"),
    )
    assert service.record_item_review_decision(FakeConn(), _create()) == existing


@pytest.mark.parametrize(
    "change",
    [
        {"decision_type": "rejected"},
        {"decision_reason": "Changed reason"},
        {"decided_by": "different-operator"},
    ],
)
def test_public_service_rejects_mismatched_retry_without_append(monkeypatch, change):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    conn = FakeConn()
    existing = _record()
    monkeypatch.setattr(
        repo, "get_item_context", lambda *args, **kwargs: _context()
    )
    monkeypatch.setattr(
        repo, "get_decision_by_request_id", lambda *args, **kwargs: existing
    )
    monkeypatch.setattr(
        repo,
        "insert_decision",
        lambda *args, **kwargs: pytest.fail("mismatched retry must not append"),
    )
    with pytest.raises(repo.ReviewRequestConflict, match="different immutable"):
        service.record_item_review_decision(conn, _create(**change))
    assert [sql for sql, _ in conn.statements] == [
        "SAVEPOINT research_evidence_review_write",
        "ROLLBACK TO SAVEPOINT research_evidence_review_write",
        "RELEASE SAVEPOINT research_evidence_review_write",
    ]


def test_future_eligibility_rechecks_context_and_effective_decision(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    monkeypatch.setattr(repo, "get_item_context", lambda *args, **kwargs: _context())
    monkeypatch.setattr(
        repo, "get_effective_decision", lambda *args, **kwargs: _record()
    )
    assert service.item_is_eligible_for_future_use(
        FakeConn(),
        project_id=PROJECT,
        research_evidence_intake_item_id=ITEM,
    )

    monkeypatch.setattr(
        repo, "get_item_context", lambda *args, **kwargs: _context(False)
    )
    assert not service.item_is_eligible_for_future_use(
        FakeConn(),
        project_id=PROJECT,
        research_evidence_intake_item_id=ITEM,
    )
