"""Feature-gated services for canonical append-only evidence-pack ledgers."""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager

import config
from pydantic import BaseModel

from . import claim_support_repository, review_repository
from . import pack_repository as repo
from .pack_models import (
    ResearchEvidencePackAggregate,
    ResearchEvidenceClaimAnnotationRevisionCreate,
    ResearchEvidenceProjectContextRevisionCreate,
    ResearchEvidencePackQuery,
    ResearchEvidenceUsageAuthorizationDecisionCreate,
    UsageAuthorizationDecisionType,
    UsageScope,
)


class ResearchEvidencePackServiceError(RuntimeError):
    pass


class ResearchEvidencePackDisabled(ResearchEvidencePackServiceError):
    pass


ResearchEvidencePackTransactionError = repo.ResearchEvidencePackTransactionError


class ResearchEvidencePackEligibilityError(ResearchEvidencePackServiceError):
    pass


class ResearchEvidencePackLimitError(ResearchEvidencePackEligibilityError):
    pass


def _require_enabled() -> None:
    if not config.research_evidence_enabled():
        raise ResearchEvidencePackDisabled(
            "Research evidence is disabled (set MAS_RESEARCH_EVIDENCE_ENABLED to enable it)"
        )


@contextmanager
def _write(conn, savepoint: str):
    repo.require_read_committed_transaction(conn)
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        yield
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    else:
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")


def _plain_caller_data(value):
    """Recursively detach caller models without invoking trusted serializers."""
    if isinstance(value, BaseModel):
        return {
            name: _plain_caller_data(getattr(value, name))
            for name in type(value).model_fields
        }
    if isinstance(value, Mapping):
        return {key: _plain_caller_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_plain_caller_data(item) for item in value)
    return value


def record_project_context_revision(conn, revision):
    revision = ResearchEvidenceProjectContextRevisionCreate.model_validate(
        _plain_caller_data(revision)
    )
    _require_enabled()
    with _write(conn, "research_evidence_pack_context_write"):
        existing = repo.get_project_context_revision_by_request_id(
            conn, project_id=revision.project_id, request_id=revision.request_id
        )
        if existing is not None:
            return repo.ensure_project_context_retry_matches(existing, revision)
        repo.lock_project(conn, project_id=revision.project_id)
        return repo.insert_project_context_revision(conn, revision)


def get_effective_project_context_revision(conn, *, project_id: str):
    _require_enabled()
    return repo.get_effective_project_context_revision(conn, project_id=project_id)


def record_claim_annotation_revision(conn, revision):
    revision = ResearchEvidenceClaimAnnotationRevisionCreate.model_validate(
        _plain_caller_data(revision)
    )
    _require_enabled()
    with _write(conn, "research_evidence_pack_annotation_write"):
        existing = repo.get_claim_annotation_revision_by_request_id(
            conn, project_id=revision.project_id,
            claim_draft_id=revision.claim_draft_id, request_id=revision.request_id,
        )
        if existing is not None:
            return repo.ensure_claim_annotation_retry_matches(existing, revision)
        repo.lock_project(conn, project_id=revision.project_id)
        repo.require_claim_and_related_claims(
            conn, project_id=revision.project_id,
            claim_draft_id=revision.claim_draft_id,
            related_claim_draft_ids=revision.related_claim_draft_ids,
        )
        return repo.insert_claim_annotation_revision(conn, revision)


def get_effective_claim_annotation_revision(conn, *, project_id: str, claim_draft_id: str):
    _require_enabled()
    return repo.get_effective_claim_annotation_revision(
        conn, project_id=project_id, claim_draft_id=claim_draft_id
    )


def list_effective_project_annotations(conn, *, project_id: str):
    _require_enabled()
    return repo.list_effective_project_annotations(conn, project_id=project_id)


def _require_authorization_eligibility(conn, value, claim_draft_id: str) -> None:
    annotation = repo.get_effective_claim_annotation_revision(
        conn, project_id=value.project_id, claim_draft_id=claim_draft_id
    )
    support = claim_support_repository.get_effective_assessment(
        conn, project_id=value.project_id,
        claim_intake_item_id=value.claim_intake_item_id,
        evidence_intake_item_id=value.evidence_intake_item_id,
    )
    claim_review = review_repository.get_effective_decision(
        conn, project_id=value.project_id,
        research_evidence_intake_item_id=value.claim_intake_item_id,
    )
    evidence_review = review_repository.get_effective_decision(
        conn, project_id=value.project_id,
        research_evidence_intake_item_id=value.evidence_intake_item_id,
    )
    if annotation is None:
        raise ResearchEvidencePackEligibilityError("authorization requires a current claim annotation")
    if support is None or support.locator_resolution != "resolvable" or support.evidence_linkage != "linked" or support.semantic_relationship not in ("support", "qualification"):
        raise ResearchEvidencePackEligibilityError("authorization requires the exact current eligible support assessment")
    if claim_review is None or claim_review.decision_type != "approved" or evidence_review is None or evidence_review.decision_type != "approved":
        raise ResearchEvidencePackEligibilityError("authorization requires exact current claim and evidence approvals")
    available = (
        claim_support_repository.claim_endpoint_is_available(
            conn, project_id=value.project_id, claim_intake_item_id=value.claim_intake_item_id
        )
        and claim_support_repository.evidence_endpoint_is_available(
            conn, project_id=value.project_id, evidence_intake_item_id=value.evidence_intake_item_id
        )
        and claim_support_repository.claim_endpoint_lineage_is_current(
            conn, project_id=value.project_id, claim_intake_item_id=value.claim_intake_item_id
        )
        and claim_support_repository.evidence_endpoint_lineage_is_current(
            conn, project_id=value.project_id, evidence_intake_item_id=value.evidence_intake_item_id
        )
    )
    if not available:
        raise ResearchEvidencePackEligibilityError("authorization endpoints must be available with current lineage")
    evidence_count, claim_count, evidence_present, claim_present = (
        repo.effective_project_pack_capacity(
            conn, project_id=value.project_id, claim_draft_id=claim_draft_id,
            evidence_intake_item_id=value.evidence_intake_item_id,
        )
    )
    if evidence_count >= 50 and not evidence_present:
        raise ResearchEvidencePackLimitError("current pack is limited to 50 distinct evidence source snapshots")
    if claim_count >= 200 and not claim_present:
        raise ResearchEvidencePackLimitError("current pack is limited to 200 distinct canonical claims")


def record_usage_authorization_decision(conn, decision):
    decision = ResearchEvidenceUsageAuthorizationDecisionCreate.model_validate(
        _plain_caller_data(decision)
    )
    _require_enabled()
    with _write(conn, "research_evidence_pack_authorization_write"):
        existing = repo.get_usage_authorization_decision_by_request_id(
            conn, project_id=decision.project_id,
            claim_intake_item_id=decision.claim_intake_item_id,
            evidence_intake_item_id=decision.evidence_intake_item_id,
            usage_scope=decision.usage_scope, request_id=decision.request_id,
        )
        if existing is not None:
            return repo.ensure_usage_authorization_retry_matches(existing, decision)
        repo.lock_project(conn, project_id=decision.project_id)
        existing = repo.get_usage_authorization_decision_by_request_id(
            conn, project_id=decision.project_id,
            claim_intake_item_id=decision.claim_intake_item_id,
            evidence_intake_item_id=decision.evidence_intake_item_id,
            usage_scope=decision.usage_scope, request_id=decision.request_id,
        )
        if existing is not None:
            return repo.ensure_usage_authorization_retry_matches(existing, decision)
        claim, _ = claim_support_repository.require_pair_context(
            conn, project_id=decision.project_id,
            claim_intake_item_id=decision.claim_intake_item_id,
            evidence_intake_item_id=decision.evidence_intake_item_id,
        )
        current = repo.get_effective_usage_authorization_decision(
            conn, project_id=decision.project_id,
            claim_intake_item_id=decision.claim_intake_item_id,
            evidence_intake_item_id=decision.evidence_intake_item_id,
            usage_scope=decision.usage_scope,
        )
        if current is None and decision.decision != UsageAuthorizationDecisionType.AUTHORIZED:
            raise ResearchEvidencePackEligibilityError("first usage decision must be authorized")
        if current is not None and current.decision == decision.decision:
            raise ResearchEvidencePackEligibilityError("usage decisions must alternate authorization and revocation")
        if decision.decision == UsageAuthorizationDecisionType.AUTHORIZED:
            _require_authorization_eligibility(conn, decision, claim.claim_draft_id)
        return repo.insert_usage_authorization_decision(conn, decision)


def get_effective_usage_authorization_decision(conn, *, project_id: str, claim_intake_item_id: str, evidence_intake_item_id: str, usage_scope: UsageScope):
    _require_enabled()
    return repo.get_effective_usage_authorization_decision(
        conn, project_id=project_id, claim_intake_item_id=claim_intake_item_id,
        evidence_intake_item_id=evidence_intake_item_id, usage_scope=usage_scope,
    )


def list_effective_project_authorizations(conn, *, project_id: str):
    _require_enabled()
    return repo.list_effective_project_authorizations(conn, project_id=project_id)


def claim_evidence_usage_is_authorized(conn, *, project_id: str, claim_intake_item_id: str, evidence_intake_item_id: str, usage_scope: UsageScope) -> bool:
    _require_enabled()
    decision = repo.get_effective_usage_authorization_decision(
        conn, project_id=project_id, claim_intake_item_id=claim_intake_item_id,
        evidence_intake_item_id=evidence_intake_item_id, usage_scope=usage_scope,
    )
    return repo.usage_authorization_is_effective(conn, decision)


def assemble_research_evidence_pack(
    conn, *, project_id: str, usage_scope: UsageScope,
) -> ResearchEvidencePackAggregate:
    """Return the immutable current pack for one explicit project/scope pair."""
    query = ResearchEvidencePackQuery(
        project_id=project_id, usage_scope=usage_scope,
    )
    _require_enabled()
    try:
        return repo.assemble_effective_project_pack(
            conn, project_id=query.project_id, usage_scope=query.usage_scope,
        )
    except repo.ResearchEvidencePackCapacityError as exc:
        raise ResearchEvidencePackLimitError(str(exc)) from exc
