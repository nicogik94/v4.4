"""Feature-gated, caller-transaction-owned R1.3 item review services."""
from __future__ import annotations

from contextlib import contextmanager

import config

from . import review_repository as repo
from .review_models import (
    ResearchEvidenceIntakeItemReviewDecisionCreate,
    ResearchEvidenceIntakeItemReviewDecisionRecord,
)


class ResearchEvidenceReviewDisabled(RuntimeError):
    """Raised when item review is attempted while research evidence is disabled."""


class ResearchEvidenceReviewTransactionError(RuntimeError):
    """Raised when caller-owned review atomicity cannot be preserved."""


class ResearchEvidenceReviewUnavailable(ValueError):
    """A positive decision is blocked by current retention or lineage state."""


def _require_enabled() -> None:
    if not config.research_evidence_enabled():
        raise ResearchEvidenceReviewDisabled(
            "Research evidence review is disabled "
            "(set MAS_RESEARCH_EVIDENCE_ENABLED to enable it)"
        )


@contextmanager
def _review_write(conn):
    if conn.autocommit:
        raise ResearchEvidenceReviewTransactionError(
            "research-evidence review writes require a non-autocommit connection"
        )
    conn.execute("SAVEPOINT research_evidence_review_write")
    try:
        yield
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT research_evidence_review_write")
        conn.execute("RELEASE SAVEPOINT research_evidence_review_write")
        raise
    else:
        conn.execute("RELEASE SAVEPOINT research_evidence_review_write")


def record_item_review_decision(
    conn,
    decision: ResearchEvidenceIntakeItemReviewDecisionCreate,
) -> ResearchEvidenceIntakeItemReviewDecisionRecord:
    """Append or idempotently return one item-scoped operator decision."""
    decision = ResearchEvidenceIntakeItemReviewDecisionCreate.model_validate(
        decision.model_dump()
        if isinstance(decision, ResearchEvidenceIntakeItemReviewDecisionCreate)
        else decision
    )
    _require_enabled()
    with _review_write(conn):
        context = repo.get_item_context(
            conn,
            project_id=decision.project_id,
            research_evidence_intake_item_id=(
                decision.research_evidence_intake_item_id
            ),
        )
        existing = repo.get_decision_by_request_id(
            conn,
            project_id=decision.project_id,
            research_evidence_intake_item_id=(
                decision.research_evidence_intake_item_id
            ),
            request_id=decision.request_id,
        )
        if existing is not None:
            return repo.ensure_retry_matches(existing, decision)
        if decision.decision_type == "approved" and not context.approval_available:
            raise ResearchEvidenceReviewUnavailable(
                "item evidence is unavailable, superseded, or withdrawn"
            )
        return repo.insert_decision(conn, decision)


def item_is_eligible_for_future_use(
    conn,
    *,
    project_id: str,
    research_evidence_intake_item_id: str,
) -> bool:
    """Evaluate review eligibility without connecting it to any consumer.

    For claim items this means only an operator-approved draft claim in an
    available intake context; it does not prove the claim or make it citation-ready.
    """
    _require_enabled()
    context = repo.get_item_context(
        conn,
        project_id=project_id,
        research_evidence_intake_item_id=research_evidence_intake_item_id,
    )
    effective = repo.get_effective_decision(
        conn,
        project_id=project_id,
        research_evidence_intake_item_id=research_evidence_intake_item_id,
    )
    return bool(
        context.approval_available
        and effective is not None
        and effective.decision_type == "approved"
    )
