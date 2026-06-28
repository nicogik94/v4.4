"""Low-level PostgreSQL reads and inserts for R1.3 item review decisions.

This module reads existing v47/v51/v53 records but writes only the v54 review
ledger. Transaction ownership and feature gating belong to ``review_service``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from knowledge.evidence_snapshot import repository as evidence_repo

from .review_models import (
    ResearchEvidenceIntakeItemReviewDecisionCreate,
    ResearchEvidenceIntakeItemReviewDecisionRecord,
)


class ResearchEvidenceReviewRepositoryError(ValueError):
    """Base error for scoped review persistence failures."""


class ReviewParentNotFound(ResearchEvidenceReviewRepositoryError):
    """The project-scoped v53 intake-item graph does not resolve."""


class ReviewIntegrityError(ResearchEvidenceReviewRepositoryError):
    """A review insert violates the immutable v54 contract."""


class ReviewRequestConflict(ReviewIntegrityError):
    """A request ID already identifies a different immutable decision."""


@dataclass(frozen=True)
class ReviewItemContext:
    project_id: str
    item_id: str
    intake_id: str
    item_kind: str
    source_snapshot_id: str
    source_metadata_revision_id: str
    candidate_fact_revision_id: Optional[str]
    fact_metadata_revision_id: Optional[str]
    claim_draft_id: Optional[str]
    approval_available: bool


def get_item_context(
    conn,
    *,
    project_id: str,
    research_evidence_intake_item_id: str,
) -> ReviewItemContext:
    """Resolve the immutable graph and current approval-time availability.

    ``approval_available`` follows v47 retention semantics (tombstone/redact
    block; legal_hold does not) and v51's explicit supersession/withdrawal
    lineage. A claim remains intake-context-bound, not proven by the snapshot.
    """
    row = conn.execute(
        """
        SELECT
            item.project_id::text,
            item.id::text,
            intake.id::text,
            item.item_kind,
            item.source_snapshot_id::text,
            intake.source_metadata_revision_id::text,
            item.candidate_fact_revision_id::text,
            item.fact_metadata_revision_id::text,
            item.claim_draft_id::text,
            (
                NOT EXISTS (
                    SELECT 1
                    FROM research_source_metadata_revision successor
                    WHERE successor.project_id = source_metadata.project_id
                      AND successor.source_snapshot_id =
                          source_metadata.source_snapshot_id
                      AND successor.supersedes_metadata_revision_id =
                          source_metadata.id
                )
                AND NOT EXISTS (
                    SELECT 1
                    FROM research_evidence_event event
                    WHERE event.project_id = source_metadata.project_id
                      AND event.entity_type = 'source_metadata_revision'
                      AND event.entity_id = source_metadata.id
                      AND event.event_type IN ('superseded', 'withdrawn')
                )
                AND (
                    (
                        item.item_kind = 'candidate_fact'
                        AND fact.id IS NOT NULL
                        AND fact_metadata.id IS NOT NULL
                        AND NOT EXISTS (
                            SELECT 1
                            FROM research_fact_metadata_revision successor
                            WHERE successor.project_id =
                                  fact_metadata.project_id
                              AND successor.candidate_fact_revision_id =
                                  fact_metadata.candidate_fact_revision_id
                              AND successor.supersedes_metadata_revision_id =
                                  fact_metadata.id
                        )
                        AND NOT EXISTS (
                            SELECT 1
                            FROM research_fact_metadata_revision replacement
                            WHERE replacement.project_id = fact.project_id
                              AND replacement.supersedes_candidate_fact_revision_id =
                                  fact.id
                        )
                        AND NOT EXISTS (
                            SELECT 1
                            FROM research_evidence_event event
                            WHERE event.project_id = fact_metadata.project_id
                              AND event.entity_type = 'fact_metadata_revision'
                              AND event.entity_id = fact_metadata.id
                              AND event.event_type IN ('superseded', 'withdrawn')
                        )
                    )
                    OR
                    (
                        item.item_kind = 'claim_draft'
                        AND claim.id IS NOT NULL
                        AND NOT EXISTS (
                            SELECT 1
                            FROM research_claim_draft successor
                            WHERE successor.project_id = claim.project_id
                              AND successor.supersedes_claim_id = claim.id
                        )
                        AND NOT EXISTS (
                            SELECT 1
                            FROM research_evidence_event event
                            WHERE event.project_id = claim.project_id
                              AND event.entity_type = 'claim_draft'
                              AND event.entity_id = claim.id
                              AND event.event_type IN ('superseded', 'withdrawn')
                        )
                    )
                )
            ) AS approval_available
        FROM research_evidence_intake_item item
        JOIN research_evidence_intake intake
          ON intake.id = item.research_evidence_intake_id
         AND intake.project_id = item.project_id
         AND intake.source_snapshot_id = item.source_snapshot_id
        JOIN source_snapshot snapshot
          ON snapshot.id = item.source_snapshot_id
         AND snapshot.project_id = item.project_id
        JOIN source_blob blob
          ON blob.id = snapshot.source_blob_id
         AND blob.project_id = snapshot.project_id
        JOIN research_source_metadata_revision source_metadata
          ON source_metadata.id = intake.source_metadata_revision_id
         AND source_metadata.project_id = intake.project_id
         AND source_metadata.source_snapshot_id = intake.source_snapshot_id
        LEFT JOIN candidate_fact_revision fact
          ON fact.id = item.candidate_fact_revision_id
         AND fact.project_id = item.project_id
         AND fact.source_snapshot_id = item.source_snapshot_id
        LEFT JOIN research_fact_metadata_revision fact_metadata
          ON fact_metadata.id = item.fact_metadata_revision_id
         AND fact_metadata.project_id = item.project_id
         AND fact_metadata.candidate_fact_revision_id =
             item.candidate_fact_revision_id
        LEFT JOIN research_claim_draft claim
          ON claim.id = item.claim_draft_id
         AND claim.project_id = item.project_id
        WHERE item.id = %s
          AND item.project_id = %s
        """,
        (research_evidence_intake_item_id, project_id),
    ).fetchone()
    if row is None:
        raise ReviewParentNotFound("research evidence intake item not found for project")
    if row[3] == "candidate_fact":
        retention_available = evidence_repo.fact_available(conn, row[6])
    else:
        retention_available = evidence_repo.snapshot_available(conn, row[4])
    return ReviewItemContext(
        project_id=row[0],
        item_id=row[1],
        intake_id=row[2],
        item_kind=row[3],
        source_snapshot_id=row[4],
        source_metadata_revision_id=row[5],
        candidate_fact_revision_id=row[6],
        fact_metadata_revision_id=row[7],
        claim_draft_id=row[8],
        approval_available=bool(row[9]) and retention_available,
    )


def get_decision_by_request_id(
    conn,
    *,
    project_id: str,
    research_evidence_intake_item_id: str,
    request_id: str,
) -> Optional[ResearchEvidenceIntakeItemReviewDecisionRecord]:
    row = conn.execute(
        """
        SELECT id::text, project_id::text,
               research_evidence_intake_item_id::text, decision_type,
               decision_reason, decided_by, request_id, decision_sequence,
               supersedes_decision_id::text, recorded_at
        FROM research_evidence_intake_item_review_decision
        WHERE project_id = %s
          AND research_evidence_intake_item_id = %s
          AND request_id = %s
        """,
        (project_id, research_evidence_intake_item_id, request_id),
    ).fetchone()
    return None if row is None else _decision_from_row(row)


def get_effective_decision(
    conn,
    *,
    project_id: str,
    research_evidence_intake_item_id: str,
) -> Optional[ResearchEvidenceIntakeItemReviewDecisionRecord]:
    row = conn.execute(
        """
        SELECT id::text, project_id::text,
               research_evidence_intake_item_id::text, decision_type,
               decision_reason, decided_by, request_id, decision_sequence,
               supersedes_decision_id::text, recorded_at
        FROM research_evidence_intake_item_review_decision
        WHERE project_id = %s
          AND research_evidence_intake_item_id = %s
        ORDER BY decision_sequence DESC
        LIMIT 1
        """,
        (project_id, research_evidence_intake_item_id),
    ).fetchone()
    record = None if row is None else _decision_from_row(row)
    if record is not None and record.decision_type == "withdrawn":
        return None
    return record


def ensure_retry_matches(
    existing: ResearchEvidenceIntakeItemReviewDecisionRecord,
    decision: ResearchEvidenceIntakeItemReviewDecisionCreate,
) -> ResearchEvidenceIntakeItemReviewDecisionRecord:
    expected = (
        decision.project_id,
        decision.research_evidence_intake_item_id,
        decision.decision_type,
        decision.decision_reason,
        decision.decided_by,
        decision.request_id,
    )
    actual = (
        existing.project_id,
        existing.research_evidence_intake_item_id,
        existing.decision_type,
        existing.decision_reason,
        existing.decided_by,
        existing.request_id,
    )
    if actual != expected:
        raise ReviewRequestConflict(
            "request_id already identifies a different immutable review decision"
        )
    return existing


def insert_decision(
    conn,
    decision: ResearchEvidenceIntakeItemReviewDecisionCreate,
) -> ResearchEvidenceIntakeItemReviewDecisionRecord:
    existing = get_decision_by_request_id(
        conn,
        project_id=decision.project_id,
        research_evidence_intake_item_id=decision.research_evidence_intake_item_id,
        request_id=decision.request_id,
    )
    if existing is not None:
        return ensure_retry_matches(existing, decision)

    conn.execute("SAVEPOINT research_evidence_review_insert")
    try:
        row = conn.execute(
            """
            INSERT INTO research_evidence_intake_item_review_decision
                (project_id, research_evidence_intake_item_id, decision_type,
                 decision_reason, decided_by, request_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id::text, project_id::text,
                      research_evidence_intake_item_id::text, decision_type,
                      decision_reason, decided_by, request_id, decision_sequence,
                      supersedes_decision_id::text, recorded_at
            """,
            (
                decision.project_id,
                decision.research_evidence_intake_item_id,
                decision.decision_type,
                decision.decision_reason,
                decision.decided_by,
                decision.request_id,
            ),
        ).fetchone()
    except Exception as exc:
        conn.execute("ROLLBACK TO SAVEPOINT research_evidence_review_insert")
        conn.execute("RELEASE SAVEPOINT research_evidence_review_insert")
        if _constraint_name(exc) == "uq_reird_item_request":
            existing = get_decision_by_request_id(
                conn,
                project_id=decision.project_id,
                research_evidence_intake_item_id=(
                    decision.research_evidence_intake_item_id
                ),
                request_id=decision.request_id,
            )
            if existing is not None:
                return ensure_retry_matches(existing, decision)
        if _sqlstate(exc).startswith("23"):
            raise ReviewIntegrityError(
                "review decision violates the immutable database contract"
            ) from exc
        raise
    else:
        conn.execute("RELEASE SAVEPOINT research_evidence_review_insert")
    return _decision_from_row(row)


def _sqlstate(exc: Exception) -> str:
    value = getattr(exc, "sqlstate", None)
    if value:
        return str(value)
    diag = getattr(exc, "diag", None)
    return str(getattr(diag, "sqlstate", "") or "")


def _constraint_name(exc: Exception) -> str:
    diag = getattr(exc, "diag", None)
    return str(getattr(diag, "constraint_name", "") or "")


def _decision_from_row(row) -> ResearchEvidenceIntakeItemReviewDecisionRecord:
    return ResearchEvidenceIntakeItemReviewDecisionRecord(
        id=row[0],
        project_id=row[1],
        research_evidence_intake_item_id=row[2],
        decision_type=row[3],
        decision_reason=row[4],
        decided_by=row[5],
        request_id=row[6],
        decision_sequence=row[7],
        supersedes_decision_id=row[8],
        recorded_at=row[9],
    )
