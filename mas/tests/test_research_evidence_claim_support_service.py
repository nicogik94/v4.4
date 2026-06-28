"""Feature-gate, transaction, and separate-input tests for R1.5."""
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_evidence import claim_support_repository as repo  # noqa: E402
from research_evidence import claim_support_service as service  # noqa: E402
from research_evidence.claim_support_models import (  # noqa: E402
    ResearchEvidenceClaimSupportAssessmentCreate,
    ResearchEvidenceClaimSupportAssessmentRecord,
)


PROJECT = "00000000-0000-0000-0000-000000000001"
CLAIM_ITEM = "00000000-0000-0000-0000-000000000002"
EVIDENCE_ITEM = "00000000-0000-0000-0000-000000000003"
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
        "claim_intake_item_id": CLAIM_ITEM,
        "evidence_intake_item_id": EVIDENCE_ITEM,
        "request_id": "request",
        "locator_resolution": "resolvable",
        "locator_rationale": "Locator reviewed",
        "evidence_linkage": "linked",
        "evidence_linkage_rationale": "Link reviewed",
        "semantic_relationship": "support",
        "semantic_relationship_rationale": "Relationship reviewed",
        "assessed_by": "operator",
    }
    values.update(changes)
    return ResearchEvidenceClaimSupportAssessmentCreate(**values)


def _record(**changes):
    values = _create().model_dump()
    values.update(
        {
            "id": PROJECT,
            "assessment_sequence": 1,
            "supersedes_assessment_id": None,
            "claim_draft_id": CLAIM_ITEM,
            "claim_source_snapshot_id": PROJECT,
            "claim_source_blob_id": CLAIM_ITEM,
            "claim_source_metadata_revision_id": EVIDENCE_ITEM,
            "evidence_source_snapshot_id": EVIDENCE_ITEM,
            "evidence_source_blob_id": PROJECT,
            "evidence_source_metadata_revision_id": CLAIM_ITEM,
            "candidate_fact_revision_id": EVIDENCE_ITEM,
            "fact_metadata_revision_id": PROJECT,
            "assessed_at": NOW,
        }
    )
    values.update(changes)
    return ResearchEvidenceClaimSupportAssessmentRecord(**values)


def _allow_pair(monkeypatch):
    monkeypatch.setattr(repo, "require_pair_context", lambda *args, **kwargs: (None, None))


def test_feature_gate_fails_closed_before_connection_access(monkeypatch):
    monkeypatch.delenv("MAS_RESEARCH_EVIDENCE_ENABLED", raising=False)
    with pytest.raises(service.ResearchEvidenceClaimSupportDisabled):
        service.record_claim_support_assessment(TripwireConn(), _create())


def test_invalid_input_and_autocommit_fail_before_repository_work(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    invalid = ResearchEvidenceClaimSupportAssessmentCreate.model_construct(
        **_create().model_dump(exclude={"locator_rationale"}),
        locator_rationale=" ",
    )
    with pytest.raises(ValidationError):
        service.record_claim_support_assessment(TripwireConn(), invalid)
    conn = FakeConn(autocommit=True)
    with pytest.raises(service.ResearchEvidenceClaimSupportTransactionError):
        service.record_claim_support_assessment(conn, _create())
    assert conn.statements == []


def test_write_uses_caller_savepoint_without_connection_ownership(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    _allow_pair(monkeypatch)
    expected = _record()
    monkeypatch.setattr(
        repo, "get_assessment_by_request_id", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(repo, "insert_assessment", lambda *args, **kwargs: expected)
    conn = FakeConn()
    assert service.record_claim_support_assessment(conn, _create()) == expected
    assert [sql for sql, _ in conn.statements] == [
        "SAVEPOINT research_evidence_claim_support_write",
        "RELEASE SAVEPOINT research_evidence_claim_support_write",
    ]
    assert conn.commit_calls == conn.rollback_calls == conn.close_calls == 0


def test_pair_context_failure_rolls_back_service_savepoint(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    monkeypatch.setattr(
        repo,
        "require_pair_context",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            repo.ClaimSupportParentNotFound("wrong project")
        ),
    )
    conn = FakeConn()
    with pytest.raises(repo.ClaimSupportParentNotFound):
        service.record_claim_support_assessment(conn, _create())
    assert [sql for sql, _ in conn.statements] == [
        "SAVEPOINT research_evidence_claim_support_write",
        "ROLLBACK TO SAVEPOINT research_evidence_claim_support_write",
        "RELEASE SAVEPOINT research_evidence_claim_support_write",
    ]


def test_matching_retry_is_returned_without_append(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    _allow_pair(monkeypatch)
    existing = _record()
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
    assert service.record_claim_support_assessment(FakeConn(), _create()) == existing


def test_dimension_reads_remain_independent(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    _allow_pair(monkeypatch)
    monkeypatch.setattr(
        repo, "get_effective_assessment", lambda *args, **kwargs: _record(
            locator_resolution="unresolvable",
            evidence_linkage="linked",
            semantic_relationship="qualification",
        )
    )
    conn = FakeConn()
    kwargs = {
        "project_id": PROJECT,
        "claim_intake_item_id": CLAIM_ITEM,
        "evidence_intake_item_id": EVIDENCE_ITEM,
    }
    assert service.claim_support_locator_resolution(conn, **kwargs) == "unresolvable"
    assert service.claim_support_evidence_linkage(conn, **kwargs) == "linked"
    assert (
        service.claim_support_semantic_relationship(conn, **kwargs)
        == "qualification"
    )
    assert conn.statements == []


def test_availability_lineage_review_and_freshness_inputs_stay_separate(
    monkeypatch,
):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    conn = FakeConn()
    monkeypatch.setattr(
        repo, "claim_endpoint_is_available", lambda *args, **kwargs: False
    )
    monkeypatch.setattr(
        repo, "evidence_endpoint_is_available", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(
        repo, "claim_endpoint_lineage_is_current", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(
        repo, "evidence_endpoint_lineage_is_current", lambda *args, **kwargs: False
    )
    monkeypatch.setattr(
        repo, "get_claim_endpoint_context", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(
        repo, "get_evidence_endpoint_context", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(
        service.review_repository,
        "get_effective_decision",
        lambda *args, **kwargs: SimpleNamespace(decision_type="approved"),
    )
    monkeypatch.setattr(
        service.freshness_service,
        "item_freshness_status_as_of",
        lambda *args, **kwargs: "stale",
    )
    assert not service.claim_support_claim_is_available(
        conn, project_id=PROJECT, claim_intake_item_id=CLAIM_ITEM
    )
    assert service.claim_support_evidence_is_available(
        conn, project_id=PROJECT, evidence_intake_item_id=EVIDENCE_ITEM
    )
    assert service.claim_support_claim_lineage_is_current(
        conn, project_id=PROJECT, claim_intake_item_id=CLAIM_ITEM
    )
    assert not service.claim_support_evidence_lineage_is_current(
        conn, project_id=PROJECT, evidence_intake_item_id=EVIDENCE_ITEM
    )
    assert service.claim_support_claim_review_decision(
        conn, project_id=PROJECT, claim_intake_item_id=CLAIM_ITEM
    ) == "approved"
    assert service.claim_support_evidence_review_decision(
        conn, project_id=PROJECT, evidence_intake_item_id=EVIDENCE_ITEM
    ) == "approved"
    assert service.claim_support_evidence_freshness_status_as_of(
        conn,
        project_id=PROJECT,
        evidence_intake_item_id=EVIDENCE_ITEM,
        as_of=NOW,
    ) == "stale"
