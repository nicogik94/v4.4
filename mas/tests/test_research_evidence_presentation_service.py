import inspect
from uuid import uuid4

import pytest
from pydantic import ValidationError

from research_evidence import pack_service
from research_evidence import presentation_projection_policy as policy
from research_evidence import presentation_projection_service as service
from research_evidence.pack_models import ResearchEvidencePackAggregate, UsageScope
from research_evidence.pack_repository import (
    ResearchEvidencePackIntegrityError,
    ResearchEvidencePackParentNotFound,
)
from research_evidence.pack_service import ResearchEvidencePackLimitError
from tests.test_research_evidence_presentation_models import (
    build_pack,
    build_two_member_pack,
    pack_ids,
)


def uid() -> str:
    return str(uuid4())


@pytest.fixture(autouse=True)
def feature_enabled(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")


def test_disabled_gate_fails_with_projection_error_before_assembly(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "false")

    def forbidden(*args, **kwargs):
        raise AssertionError("assembly must not run while disabled")

    monkeypatch.setattr(pack_service, "assemble_research_evidence_pack", forbidden)
    with pytest.raises(service.ResearchEvidencePresentationProjectionDisabled):
        service.project_research_evidence_presentation(
            object(), project_id=uid(), usage_scope="client_report",
        )


def test_identity_and_scope_validated_before_gate_and_assembly(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "false")

    def forbidden(*args, **kwargs):
        raise AssertionError("assembly must not run for invalid identity")

    monkeypatch.setattr(pack_service, "assemble_research_evidence_pack", forbidden)
    with pytest.raises(ValidationError):
        service.project_research_evidence_presentation(
            object(), project_id="not-a-uuid", usage_scope="client_report",
        )
    with pytest.raises(ValidationError):
        service.project_research_evidence_presentation(
            object(), project_id=uid(), usage_scope="not-a-scope",
        )


def test_service_projects_empty_pack_and_passes_validated_identity(monkeypatch):
    project_id = uid()
    calls = []

    def fake_assemble(conn, *, project_id, usage_scope):
        calls.append((conn, project_id, usage_scope))
        return ResearchEvidencePackAggregate(
            project_id=project_id, usage_scope=usage_scope,
        )

    monkeypatch.setattr(
        pack_service, "assemble_research_evidence_pack", fake_assemble,
    )
    conn = object()
    projection = service.project_research_evidence_presentation(
        conn, project_id=project_id.upper(), usage_scope="operator_dossier",
    )
    assert calls == [(conn, project_id, UsageScope.OPERATOR_DOSSIER)]
    assert projection.project_id == project_id
    assert projection.usage_scope is UsageScope.OPERATOR_DOSSIER
    assert projection.claims == ()
    assert projection.relationships == ()
    assert projection.counts.relationship_count == 0
    assert projection.policy_identifier == policy.PRESENTATION_POLICY_IDENTIFIER
    assert projection.policy_version == policy.PRESENTATION_POLICY_VERSION
    assert projection.policy_fingerprint == policy.PRESENTATION_POLICY_FINGERPRINT


@pytest.mark.parametrize("pack_error", [
    ResearchEvidencePackParentNotFound("project not found"),
    ResearchEvidencePackIntegrityError("malformed persisted state"),
    ResearchEvidencePackLimitError("pack exceeds capacity"),
])
def test_service_preserves_pack_error_identity(monkeypatch, pack_error):
    def failing(conn, *, project_id, usage_scope):
        raise pack_error

    monkeypatch.setattr(pack_service, "assemble_research_evidence_pack", failing)
    with pytest.raises(type(pack_error)) as excinfo:
        service.project_research_evidence_presentation(
            object(), project_id=uid(), usage_scope="internal_analysis",
        )
    assert excinfo.value is pack_error


def test_service_touches_connection_only_through_pack_assembly(monkeypatch):
    pack = build_pack("client_report")

    def fake_assemble(conn, *, project_id, usage_scope):
        return pack

    monkeypatch.setattr(
        pack_service, "assemble_research_evidence_pack", fake_assemble,
    )
    projection = service.project_research_evidence_presentation(
        object(), project_id=pack.project_id, usage_scope="client_report",
    )
    assert projection.counts.relationship_count == 1


def test_pure_projection_rejects_foreign_inputs():
    pack = build_pack("client_report")
    for foreign in (None, {}, pack.model_dump(), object()):
        with pytest.raises(service.ResearchEvidencePresentationProjectionError):
            service.project_research_evidence_pack(foreign)


def test_pure_projection_derives_scope_from_pack_only():
    parameters = list(
        inspect.signature(service.project_research_evidence_pack).parameters
    )
    assert parameters == ["pack"]
    for scope in UsageScope:
        projection = service.project_research_evidence_pack(build_pack(scope))
        assert projection.usage_scope is scope


def test_pure_projection_is_deterministic():
    ids = pack_ids()
    first = service.project_research_evidence_pack(
        build_pack("operator_dossier", ids=ids)
    )
    second = service.project_research_evidence_pack(
        build_pack("operator_dossier", ids=ids)
    )
    assert first == second
    assert first.projection_fingerprint == second.projection_fingerprint


def test_builder_preserves_membership_ordering_and_counts():
    pack = build_two_member_pack("client_report")
    projection = service.project_research_evidence_pack(pack)
    assert tuple(item.claim_draft_id for item in projection.claims) == tuple(
        item.claim_draft_id for item in pack.claims
    )
    assert tuple(item.source_snapshot_id for item in projection.sources) == tuple(
        item.source_snapshot_id for item in pack.sources
    )
    assert tuple(
        item.candidate_fact_revision_id for item in projection.evidence
    ) == tuple(item.candidate_fact_revision_id for item in pack.evidence)
    assert tuple(
        (item.claim_draft_id, item.source_snapshot_id, item.candidate_fact_revision_id)
        for item in projection.relationships
    ) == tuple(
        (item.claim_draft_id, item.source_snapshot_id, item.candidate_fact_revision_id)
        for item in pack.relationships
    )
    assert projection.counts == pack.counts


def test_builder_wraps_member_validation_failures_with_chaining(monkeypatch):
    original = policy.allowed_presentation_fields

    def dropping(scope, member_kind):
        allowed = original(scope, member_kind)
        if member_kind == "claim":
            return frozenset(allowed - {"claim_text"})
        return allowed

    monkeypatch.setattr(policy, "allowed_presentation_fields", dropping)
    with pytest.raises(
        service.ResearchEvidencePresentationProjectionIntegrityError
    ) as excinfo:
        service.project_research_evidence_pack(build_pack("client_report"))
    assert isinstance(excinfo.value.__cause__, ValidationError)


def test_builder_rejects_policy_fields_it_cannot_map(monkeypatch):
    original = policy.allowed_presentation_fields

    def widened(scope, member_kind):
        allowed = original(scope, member_kind)
        if member_kind == "source":
            return frozenset(allowed | {"mystery_field"})
        return allowed

    monkeypatch.setattr(policy, "allowed_presentation_fields", widened)
    with pytest.raises(
        service.ResearchEvidencePresentationProjectionIntegrityError,
        match="unmapped source fields",
    ):
        service.project_research_evidence_pack(build_pack("client_report"))


def test_service_full_pipeline_applies_scope_disclosure(monkeypatch):
    for scope, expect_raw_locator, expect_relevance in (
        (UsageScope.INTERNAL_ANALYSIS, True, True),
        (UsageScope.OPERATOR_DOSSIER, False, True),
        (UsageScope.CLIENT_REPORT, False, False),
    ):
        pack = build_pack(scope)

        def fake_assemble(conn, *, project_id, usage_scope, _pack=pack):
            return _pack

        monkeypatch.setattr(
            pack_service, "assemble_research_evidence_pack", fake_assemble,
        )
        projection = service.project_research_evidence_presentation(
            object(), project_id=pack.project_id, usage_scope=scope,
        )
        assert (projection.sources[0].source_locator is not None) is expect_raw_locator
        assert (
            projection.claims[0].decision_relevance is not None
        ) is expect_relevance
        assert projection.relationships[0].semantic_relationship == "support"
        assert projection.claims[0].does_not_prove == (
            pack.claims[0].annotation.does_not_prove
        )
