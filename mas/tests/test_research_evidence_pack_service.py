from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from research_evidence import pack_service as service
from research_evidence.pack_models import (
    ResearchEvidencePackAggregate,
    ResearchEvidenceClaimAnnotationRevisionCreate,
    ResearchEvidenceExplicitProbability,
    ResearchEvidenceProjectContextRevisionCreate,
    ResearchEvidenceUsageAuthorizationDecisionCreate,
)


def uid(): return str(uuid4())


class Conn:
    def __init__(self, autocommit=False, isolation="read committed"):
        self.autocommit, self.isolation, self.calls = autocommit, isolation, []
    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        row = (self.isolation,) if sql == "SHOW transaction_isolation" else None
        return SimpleNamespace(fetchone=lambda: row)


def context():
    return ResearchEvidenceProjectContextRevisionCreate(
        project_id=uid(), request_id="r", research_question="q",
        project_limitations=[], unresolved_gaps=[], actor="a",
    )


def authorization():
    return ResearchEvidenceUsageAuthorizationDecisionCreate(
        project_id=uid(), claim_intake_item_id=uid(),
        evidence_intake_item_id=uid(), usage_scope="client_report",
        decision="authorized", reason="approved", actor="operator",
        request_id="authorization-request",
    )


def annotation():
    return ResearchEvidenceClaimAnnotationRevisionCreate(
        project_id=uid(), claim_draft_id=uid(), request_id="annotation-request",
        epistemic_status="inference", confidence_label="medium",
        decision_relevance="relevant", supports_statement="supports",
        does_not_prove="limited", limitations=[], related_claim_draft_ids=[],
        explicit_probability=ResearchEvidenceExplicitProbability(
            value=Decimal("0.500000"), provided_by="operator",
            provenance_reference="calculation", provenance_note="entered by operator",
        ), actor="operator",
    )


@pytest.mark.parametrize("outer_kind", ["mapping", "model"])
def test_annotation_write_strictly_revalidates_copied_nested_probability_before_sql(
    monkeypatch, outer_kind,
):
    monkeypatch.setattr(service.config, "research_evidence_enabled", lambda: True)
    base = annotation()
    invalid_probability = base.explicit_probability.model_copy(
        update={"value": Decimal("0.1234567")},
    )
    value = (
        {**base.model_dump(), "explicit_probability": invalid_probability}
        if outer_kind == "mapping"
        else base.model_copy(update={"explicit_probability": invalid_probability})
    )
    conn = Conn()
    with pytest.raises(ValidationError, match="six decimal"):
        service.record_claim_annotation_revision(conn, value)
    assert conn.calls == []


@pytest.mark.parametrize("isolation", ["repeatable read", "serializable"])
def test_service_rejects_unsupported_isolation_before_business_processing(
    monkeypatch, isolation,
):
    monkeypatch.setattr(service.config, "research_evidence_enabled", lambda: True)
    conn = Conn(isolation=isolation)
    monkeypatch.setattr(
        service.repo, "get_usage_authorization_decision_by_request_id",
        lambda *args, **kwargs: pytest.fail("lookup must follow isolation validation"),
    )
    monkeypatch.setattr(
        service.repo, "lock_project",
        lambda *args, **kwargs: pytest.fail("lock must follow isolation validation"),
    )
    with pytest.raises(service.ResearchEvidencePackTransactionError, match="READ COMMITTED"):
        service.record_usage_authorization_decision(conn, authorization())
    assert [sql for sql, _ in conn.calls] == ["SHOW transaction_isolation"]


def test_service_accepts_read_committed_before_write_savepoint(monkeypatch):
    monkeypatch.setattr(service.config, "research_evidence_enabled", lambda: True)
    conn = Conn()
    value = context()
    existing = object()
    monkeypatch.setattr(
        service.repo, "get_project_context_revision_by_request_id",
        lambda *args, **kwargs: existing,
    )
    monkeypatch.setattr(
        service.repo, "ensure_project_context_retry_matches",
        lambda *args: existing,
    )
    assert service.record_project_context_revision(conn, value) is existing
    assert [sql for sql, _ in conn.calls[:2]] == [
        "SHOW transaction_isolation",
        "SAVEPOINT research_evidence_pack_context_write",
    ]


def test_write_revalidates_before_database_and_rejects_autocommit(monkeypatch):
    monkeypatch.setattr(service.config, "research_evidence_enabled", lambda: True)
    conn = Conn(autocommit=True)
    with pytest.raises(service.ResearchEvidencePackTransactionError):
        service.record_project_context_revision(conn, context())
    assert conn.calls == []


def test_context_retry_resolves_before_lock_and_uses_service_savepoint(monkeypatch):
    monkeypatch.setattr(service.config, "research_evidence_enabled", lambda: True)
    value=context(); existing=object(); conn=Conn()
    monkeypatch.setattr(service.repo,"get_project_context_revision_by_request_id",lambda *a,**k: existing)
    monkeypatch.setattr(service.repo,"ensure_project_context_retry_matches",lambda *a: existing)
    monkeypatch.setattr(service.repo,"lock_project",lambda *a,**k: pytest.fail("retry must precede lock"))
    assert service.record_project_context_revision(conn,value) is existing
    assert conn.calls[0][0] == "SHOW transaction_isolation"
    assert conn.calls[1][0].startswith("SAVEPOINT research_evidence_pack_context_write")
    assert conn.calls[-1][0].startswith("RELEASE SAVEPOINT research_evidence_pack_context_write")


def test_handled_failure_rolls_back_only_service_savepoint(monkeypatch):
    monkeypatch.setattr(service.config, "research_evidence_enabled", lambda: True)
    monkeypatch.setattr(service.repo,"get_project_context_revision_by_request_id",lambda *a,**k: None)
    monkeypatch.setattr(service.repo,"lock_project",lambda *a,**k: (_ for _ in ()).throw(ValueError("bad")))
    conn=Conn()
    with pytest.raises(ValueError): service.record_project_context_revision(conn,context())
    sql=[call[0] for call in conn.calls]
    assert any(s.startswith("ROLLBACK TO SAVEPOINT") for s in sql)
    assert not any(s in ("ROLLBACK","COMMIT") for s in sql)
    assert sql[-1].startswith("RELEASE SAVEPOINT")


def test_absent_authorization_fails_closed(monkeypatch):
    monkeypatch.setattr(service.config, "research_evidence_enabled", lambda: True)
    monkeypatch.setattr(service.repo,"get_effective_usage_authorization_decision",lambda *a,**k: None)
    assert service.claim_evidence_usage_is_authorized(
        Conn(),project_id=uid(),claim_intake_item_id=uid(),evidence_intake_item_id=uid(),usage_scope="client_report"
    ) is False


def test_disabled_gate_keeps_existing_meaning(monkeypatch):
    monkeypatch.setattr(service.config, "research_evidence_enabled", lambda: False)
    with pytest.raises(service.ResearchEvidencePackDisabled):
        service.get_effective_project_context_revision(Conn(),project_id=uid())


def test_authorization_matching_retry_rechecks_after_lock_and_short_circuits(monkeypatch):
    monkeypatch.setattr(service.config, "research_evidence_enabled", lambda: True)
    value = authorization()
    existing = object()
    events = []
    lookups = iter((None, existing))

    def lookup(*args, **kwargs):
        events.append("lookup")
        return next(lookups)

    monkeypatch.setattr(
        service.repo, "get_usage_authorization_decision_by_request_id", lookup,
    )
    monkeypatch.setattr(
        service.repo, "lock_project",
        lambda *args, **kwargs: events.append("lock"),
    )
    monkeypatch.setattr(
        service.repo, "ensure_usage_authorization_retry_matches",
        lambda winner, loser: events.append("match") or winner,
    )
    monkeypatch.setattr(
        service.claim_support_repository, "require_pair_context",
        lambda *args, **kwargs: pytest.fail("retry must precede eligibility"),
    )
    monkeypatch.setattr(
        service.repo, "effective_project_pack_member_counts",
        lambda *args, **kwargs: pytest.fail("retry must precede counts"),
    )
    monkeypatch.setattr(
        service.repo, "insert_usage_authorization_decision",
        lambda *args, **kwargs: pytest.fail("retry must precede insert"),
    )

    assert service.record_usage_authorization_decision(Conn(), value) is existing
    assert events == ["lookup", "lock", "lookup", "match"]


def test_authorization_conflicting_retry_rechecks_after_lock_and_short_circuits(monkeypatch):
    monkeypatch.setattr(service.config, "research_evidence_enabled", lambda: True)
    value = authorization()
    existing = object()
    events = []
    lookups = iter((None, existing))

    def lookup(*args, **kwargs):
        events.append("lookup")
        return next(lookups)

    def conflict(*args):
        events.append("conflict")
        raise service.repo.ResearchEvidencePackRequestConflict("different payload")

    monkeypatch.setattr(
        service.repo, "get_usage_authorization_decision_by_request_id", lookup,
    )
    monkeypatch.setattr(
        service.repo, "lock_project",
        lambda *args, **kwargs: events.append("lock"),
    )
    monkeypatch.setattr(
        service.repo, "ensure_usage_authorization_retry_matches", conflict,
    )
    monkeypatch.setattr(
        service.claim_support_repository, "require_pair_context",
        lambda *args, **kwargs: pytest.fail("retry must precede eligibility"),
    )
    monkeypatch.setattr(
        service.repo, "effective_project_pack_member_counts",
        lambda *args, **kwargs: pytest.fail("retry must precede counts"),
    )
    monkeypatch.setattr(
        service.repo, "insert_usage_authorization_decision",
        lambda *args, **kwargs: pytest.fail("retry must precede insert"),
    )

    with pytest.raises(service.repo.ResearchEvidencePackRequestConflict):
        service.record_usage_authorization_decision(Conn(), value)
    assert events == ["lookup", "lock", "lookup", "conflict"]


def test_assembly_service_disabled_fails_before_repository(monkeypatch):
    monkeypatch.setattr(service.config, "research_evidence_enabled", lambda: False)
    monkeypatch.setattr(
        service.repo, "assemble_effective_project_pack",
        lambda *args, **kwargs: pytest.fail("disabled service must not query"),
    )
    with pytest.raises(service.ResearchEvidencePackDisabled):
        service.assemble_research_evidence_pack(
            Conn(), project_id=uid(), usage_scope="client_report",
        )


def test_assembly_service_returns_typed_empty_pack_and_propagates_explicit_scope(
    monkeypatch,
):
    monkeypatch.setattr(service.config, "research_evidence_enabled", lambda: True)
    project_id = uid()
    expected = ResearchEvidencePackAggregate(
        project_id=project_id, usage_scope="operator_dossier",
    )
    calls = []

    def assemble(conn, **kwargs):
        calls.append((conn, kwargs))
        return expected

    monkeypatch.setattr(service.repo, "assemble_effective_project_pack", assemble)
    conn = Conn()
    first = service.assemble_research_evidence_pack(
        conn, project_id=project_id, usage_scope="operator_dossier",
    )
    second = service.assemble_research_evidence_pack(
        conn, project_id=project_id, usage_scope="operator_dossier",
    )
    assert first is second is expected
    assert calls == [
        (conn, {"project_id": project_id, "usage_scope": expected.usage_scope}),
        (conn, {"project_id": project_id, "usage_scope": expected.usage_scope}),
    ]
    assert conn.calls == []


def test_assembly_service_validates_identity_and_scope_before_feature_or_repository(
    monkeypatch,
):
    feature_calls = []
    monkeypatch.setattr(
        service.config, "research_evidence_enabled",
        lambda: feature_calls.append("feature") or True,
    )
    monkeypatch.setattr(
        service.repo, "assemble_effective_project_pack",
        lambda *args, **kwargs: pytest.fail("invalid input must not query"),
    )
    with pytest.raises(ValidationError):
        service.assemble_research_evidence_pack(
            Conn(), project_id="invalid", usage_scope="client_report",
        )
    with pytest.raises(ValidationError):
        service.assemble_research_evidence_pack(
            Conn(), project_id=uid(), usage_scope="all_scopes",
        )
    assert feature_calls == []


def test_assembly_service_preserves_repository_failures_and_maps_capacity(monkeypatch):
    monkeypatch.setattr(service.config, "research_evidence_enabled", lambda: True)
    project_id = uid()
    failure = service.repo.ResearchEvidencePackIntegrityError("corrupt state")
    monkeypatch.setattr(
        service.repo, "assemble_effective_project_pack",
        lambda *args, **kwargs: (_ for _ in ()).throw(failure),
    )
    with pytest.raises(service.repo.ResearchEvidencePackIntegrityError) as raised:
        service.assemble_research_evidence_pack(
            Conn(), project_id=project_id, usage_scope="internal_analysis",
        )
    assert raised.value is failure

    capacity = service.repo.ResearchEvidencePackCapacityError("over capacity")
    monkeypatch.setattr(
        service.repo, "assemble_effective_project_pack",
        lambda *args, **kwargs: (_ for _ in ()).throw(capacity),
    )
    with pytest.raises(service.ResearchEvidencePackLimitError, match="over capacity"):
        service.assemble_research_evidence_pack(
            Conn(), project_id=project_id, usage_scope="internal_analysis",
        )
