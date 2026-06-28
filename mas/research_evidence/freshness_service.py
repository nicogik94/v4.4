"""Feature-gated R1.4 freshness writes and read-only evaluation."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime

import config

from . import freshness_repository as repo
from .freshness_models import (
    FreshnessStatus,
    ResearchEvidenceIntakeItemFreshnessAssessmentCreate,
    ResearchEvidenceIntakeItemFreshnessAssessmentRecord,
    require_aware_datetime,
)


class ResearchEvidenceFreshnessDisabled(RuntimeError):
    """Raised when research-evidence freshness is feature-disabled."""


class ResearchEvidenceFreshnessTransactionError(RuntimeError):
    """Raised when caller-owned assessment atomicity cannot be preserved."""


class ResearchEvidenceFreshnessNotApplicable(ValueError):
    """Freshness assessments do not apply to claim-draft intake items."""


def _require_enabled() -> None:
    if not config.research_evidence_enabled():
        raise ResearchEvidenceFreshnessDisabled(
            "Research evidence freshness is disabled "
            "(set MAS_RESEARCH_EVIDENCE_ENABLED to enable it)"
        )


@contextmanager
def _freshness_write(conn):
    if conn.autocommit:
        raise ResearchEvidenceFreshnessTransactionError(
            "research-evidence freshness writes require a non-autocommit connection"
        )
    conn.execute("SAVEPOINT research_evidence_freshness_write")
    try:
        yield
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT research_evidence_freshness_write")
        conn.execute("RELEASE SAVEPOINT research_evidence_freshness_write")
        raise
    else:
        conn.execute("RELEASE SAVEPOINT research_evidence_freshness_write")


def record_item_freshness_assessment(
    conn,
    assessment: ResearchEvidenceIntakeItemFreshnessAssessmentCreate,
) -> ResearchEvidenceIntakeItemFreshnessAssessmentRecord:
    """Append or idempotently return one candidate-fact assessment."""
    assessment = ResearchEvidenceIntakeItemFreshnessAssessmentCreate.model_validate(
        assessment.model_dump()
        if isinstance(
            assessment, ResearchEvidenceIntakeItemFreshnessAssessmentCreate
        )
        else assessment
    )
    _require_enabled()
    with _freshness_write(conn):
        item_kind = repo.get_item_kind(
            conn,
            project_id=assessment.project_id,
            research_evidence_intake_item_id=(
                assessment.research_evidence_intake_item_id
            ),
        )
        if item_kind == "claim_draft":
            raise ResearchEvidenceFreshnessNotApplicable(
                "claim-draft intake items are not applicable to freshness"
            )
        existing = repo.get_assessment_by_request_id(
            conn,
            project_id=assessment.project_id,
            research_evidence_intake_item_id=(
                assessment.research_evidence_intake_item_id
            ),
            request_id=assessment.request_id,
        )
        if existing is not None:
            return repo.ensure_retry_matches(existing, assessment)
        return repo.insert_assessment(conn, assessment)


def item_freshness_status_as_of(
    conn,
    *,
    project_id: str,
    research_evidence_intake_item_id: str,
    as_of: datetime,
) -> FreshnessStatus:
    """Derive effective freshness without writing or consulting other contracts."""
    as_of = require_aware_datetime(as_of, field_name="as_of")
    _require_enabled()
    item_kind = repo.get_item_kind(
        conn,
        project_id=project_id,
        research_evidence_intake_item_id=research_evidence_intake_item_id,
    )
    if item_kind == "claim_draft":
        return "not_applicable"
    assessment = repo.get_effective_assessment(
        conn,
        project_id=project_id,
        research_evidence_intake_item_id=research_evidence_intake_item_id,
    )
    if assessment is None:
        return "unknown"
    return "fresh" if as_of <= assessment.fresh_through else "stale"
