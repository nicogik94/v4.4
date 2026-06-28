"""Feature-gated R1.5 claim-support writes and separate read-only inputs."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Optional

import config

from . import claim_support_repository as repo
from . import freshness_service, review_repository
from .claim_support_models import (
    EvidenceLinkage,
    LocatorResolution,
    ResearchEvidenceClaimSupportAssessmentCreate,
    ResearchEvidenceClaimSupportAssessmentRecord,
    SemanticRelationship,
)
from .freshness_models import FreshnessStatus
from .review_models import DecisionType


class ResearchEvidenceClaimSupportDisabled(RuntimeError):
    """Raised when claim-support access is feature-disabled."""


class ResearchEvidenceClaimSupportTransactionError(RuntimeError):
    """Raised when caller-owned assessment atomicity cannot be preserved."""


def _require_enabled() -> None:
    if not config.research_evidence_enabled():
        raise ResearchEvidenceClaimSupportDisabled(
            "Research evidence claim support is disabled "
            "(set MAS_RESEARCH_EVIDENCE_ENABLED to enable it)"
        )


@contextmanager
def _claim_support_write(conn):
    if conn.autocommit:
        raise ResearchEvidenceClaimSupportTransactionError(
            "research-evidence claim-support writes require "
            "a non-autocommit connection"
        )
    conn.execute("SAVEPOINT research_evidence_claim_support_write")
    try:
        yield
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT research_evidence_claim_support_write")
        conn.execute("RELEASE SAVEPOINT research_evidence_claim_support_write")
        raise
    else:
        conn.execute("RELEASE SAVEPOINT research_evidence_claim_support_write")


def record_claim_support_assessment(
    conn,
    assessment: ResearchEvidenceClaimSupportAssessmentCreate,
) -> ResearchEvidenceClaimSupportAssessmentRecord:
    """Append or idempotently return one operator-declared pair assessment."""
    assessment = ResearchEvidenceClaimSupportAssessmentCreate.model_validate(
        assessment.model_dump()
        if isinstance(assessment, ResearchEvidenceClaimSupportAssessmentCreate)
        else assessment
    )
    _require_enabled()
    with _claim_support_write(conn):
        repo.require_pair_context(
            conn,
            project_id=assessment.project_id,
            claim_intake_item_id=assessment.claim_intake_item_id,
            evidence_intake_item_id=assessment.evidence_intake_item_id,
        )
        existing = repo.get_assessment_by_request_id(
            conn,
            project_id=assessment.project_id,
            claim_intake_item_id=assessment.claim_intake_item_id,
            evidence_intake_item_id=assessment.evidence_intake_item_id,
            request_id=assessment.request_id,
        )
        if existing is not None:
            return repo.ensure_retry_matches(existing, assessment)
        return repo.insert_assessment(conn, assessment)


def get_effective_claim_support_assessment(
    conn,
    *,
    project_id: str,
    claim_intake_item_id: str,
    evidence_intake_item_id: str,
) -> Optional[ResearchEvidenceClaimSupportAssessmentRecord]:
    """Return the latest pair assessment without evaluating any other contract."""
    _require_enabled()
    repo.require_pair_context(
        conn,
        project_id=project_id,
        claim_intake_item_id=claim_intake_item_id,
        evidence_intake_item_id=evidence_intake_item_id,
    )
    return repo.get_effective_assessment(
        conn,
        project_id=project_id,
        claim_intake_item_id=claim_intake_item_id,
        evidence_intake_item_id=evidence_intake_item_id,
    )


def list_effective_claim_support_assessments(
    conn,
    *,
    project_id: str,
    claim_intake_item_id: str,
) -> list[ResearchEvidenceClaimSupportAssessmentRecord]:
    """List latest pair assessments for one claim without readiness aggregation."""
    _require_enabled()
    repo.get_claim_endpoint_context(
        conn,
        project_id=project_id,
        claim_intake_item_id=claim_intake_item_id,
    )
    return repo.list_effective_assessments_for_claim(
        conn,
        project_id=project_id,
        claim_intake_item_id=claim_intake_item_id,
    )


def claim_support_locator_resolution(
    conn,
    *,
    project_id: str,
    claim_intake_item_id: str,
    evidence_intake_item_id: str,
) -> Optional[LocatorResolution]:
    assessment = get_effective_claim_support_assessment(
        conn,
        project_id=project_id,
        claim_intake_item_id=claim_intake_item_id,
        evidence_intake_item_id=evidence_intake_item_id,
    )
    return None if assessment is None else assessment.locator_resolution


def claim_support_evidence_linkage(
    conn,
    *,
    project_id: str,
    claim_intake_item_id: str,
    evidence_intake_item_id: str,
) -> Optional[EvidenceLinkage]:
    assessment = get_effective_claim_support_assessment(
        conn,
        project_id=project_id,
        claim_intake_item_id=claim_intake_item_id,
        evidence_intake_item_id=evidence_intake_item_id,
    )
    return None if assessment is None else assessment.evidence_linkage


def claim_support_semantic_relationship(
    conn,
    *,
    project_id: str,
    claim_intake_item_id: str,
    evidence_intake_item_id: str,
) -> Optional[SemanticRelationship]:
    assessment = get_effective_claim_support_assessment(
        conn,
        project_id=project_id,
        claim_intake_item_id=claim_intake_item_id,
        evidence_intake_item_id=evidence_intake_item_id,
    )
    return None if assessment is None else assessment.semantic_relationship


def claim_support_claim_is_available(
    conn,
    *,
    project_id: str,
    claim_intake_item_id: str,
) -> bool:
    _require_enabled()
    return repo.claim_endpoint_is_available(
        conn,
        project_id=project_id,
        claim_intake_item_id=claim_intake_item_id,
    )


def claim_support_evidence_is_available(
    conn,
    *,
    project_id: str,
    evidence_intake_item_id: str,
) -> bool:
    _require_enabled()
    return repo.evidence_endpoint_is_available(
        conn,
        project_id=project_id,
        evidence_intake_item_id=evidence_intake_item_id,
    )


def claim_support_claim_lineage_is_current(
    conn,
    *,
    project_id: str,
    claim_intake_item_id: str,
) -> bool:
    _require_enabled()
    return repo.claim_endpoint_lineage_is_current(
        conn,
        project_id=project_id,
        claim_intake_item_id=claim_intake_item_id,
    )


def claim_support_evidence_lineage_is_current(
    conn,
    *,
    project_id: str,
    evidence_intake_item_id: str,
) -> bool:
    _require_enabled()
    return repo.evidence_endpoint_lineage_is_current(
        conn,
        project_id=project_id,
        evidence_intake_item_id=evidence_intake_item_id,
    )


def claim_support_claim_review_decision(
    conn,
    *,
    project_id: str,
    claim_intake_item_id: str,
) -> Optional[DecisionType]:
    _require_enabled()
    repo.get_claim_endpoint_context(
        conn,
        project_id=project_id,
        claim_intake_item_id=claim_intake_item_id,
    )
    decision = review_repository.get_effective_decision(
        conn,
        project_id=project_id,
        research_evidence_intake_item_id=claim_intake_item_id,
    )
    return None if decision is None else decision.decision_type


def claim_support_evidence_review_decision(
    conn,
    *,
    project_id: str,
    evidence_intake_item_id: str,
) -> Optional[DecisionType]:
    _require_enabled()
    repo.get_evidence_endpoint_context(
        conn,
        project_id=project_id,
        evidence_intake_item_id=evidence_intake_item_id,
    )
    decision = review_repository.get_effective_decision(
        conn,
        project_id=project_id,
        research_evidence_intake_item_id=evidence_intake_item_id,
    )
    return None if decision is None else decision.decision_type


def claim_support_evidence_freshness_status_as_of(
    conn,
    *,
    project_id: str,
    evidence_intake_item_id: str,
    as_of: datetime,
) -> FreshnessStatus:
    """Return only v55 freshness; do not combine it with support or review."""
    _require_enabled()
    repo.get_evidence_endpoint_context(
        conn,
        project_id=project_id,
        evidence_intake_item_id=evidence_intake_item_id,
    )
    return freshness_service.item_freshness_status_as_of(
        conn,
        project_id=project_id,
        research_evidence_intake_item_id=evidence_intake_item_id,
        as_of=as_of,
    )
