"""Low-level PostgreSQL reads and inserts for R1.2 draft intake records.

This module never writes canonical v47 or R1.1 v51/v52 records. Transaction
ownership and feature gating belong to ``intake_service``.
"""
from __future__ import annotations

from typing import Optional

from .intake_models import (
    ResearchEvidenceIntakeCreate,
    ResearchEvidenceIntakeItemCreate,
    ResearchEvidenceIntakeItemRecord,
    ResearchEvidenceIntakeRecord,
)


class ResearchEvidenceIntakeRepositoryError(ValueError):
    """Base error for scoped intake persistence failures."""


class IntakeParentNotFound(ResearchEvidenceIntakeRepositoryError):
    """A required project-scoped canonical or sidecar parent is absent."""


class IntakeIntegrityError(ResearchEvidenceIntakeRepositoryError):
    """A requested intake binding violates the R1.2 contract."""


def snapshot_is_available(conn, *, project_id: str, source_snapshot_id: str) -> bool:
    """Return canonical availability, rejecting a missing same-project snapshot."""
    row = conn.execute(
        """
        SELECT NOT EXISTS (
            SELECT 1
            FROM evidence_retention_event e
            WHERE e.project_id = s.project_id
              AND e.event_type IN ('tombstone', 'redact')
              AND (
                    e.source_snapshot_id = s.id
                 OR e.source_blob_id = s.source_blob_id
              )
        )
        FROM source_snapshot s
        WHERE s.id = %s AND s.project_id = %s
        """,
        (source_snapshot_id, project_id),
    ).fetchone()
    if row is None:
        raise IntakeParentNotFound("source snapshot not found for project")
    return bool(row[0])


def insert_intake(
    conn,
    intake: ResearchEvidenceIntakeCreate,
) -> ResearchEvidenceIntakeRecord:
    _require_source_metadata(
        conn,
        project_id=intake.project_id,
        source_snapshot_id=intake.source_snapshot_id,
        source_metadata_revision_id=intake.source_metadata_revision_id,
    )
    row = conn.execute(
        """
        INSERT INTO research_evidence_intake
            (project_id, source_snapshot_id, source_metadata_revision_id,
             selection_reason, created_by)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id::text, project_id::text, source_snapshot_id::text,
                  source_metadata_revision_id::text, intake_method, state,
                  selection_reason, created_by, created_at
        """,
        (
            intake.project_id,
            intake.source_snapshot_id,
            intake.source_metadata_revision_id,
            intake.selection_reason,
            intake.created_by,
        ),
    ).fetchone()
    return _intake_from_row(row)


def get_intake(
    conn,
    *,
    project_id: str,
    intake_id: str,
) -> Optional[ResearchEvidenceIntakeRecord]:
    row = conn.execute(
        """
        SELECT id::text, project_id::text, source_snapshot_id::text,
               source_metadata_revision_id::text, intake_method, state,
               selection_reason, created_by, created_at
        FROM research_evidence_intake
        WHERE id = %s AND project_id = %s
        """,
        (intake_id, project_id),
    ).fetchone()
    return None if row is None else _intake_from_row(row)


def insert_item(
    conn,
    item: ResearchEvidenceIntakeItemCreate,
    *,
    source_snapshot_id: str,
) -> ResearchEvidenceIntakeItemRecord:
    """Insert a binding using the server-derived intake snapshot."""
    if item.item_kind == "candidate_fact":
        _require_fact_metadata(
            conn,
            project_id=item.project_id,
            source_snapshot_id=source_snapshot_id,
            candidate_fact_revision_id=item.candidate_fact_revision_id or "",
            fact_metadata_revision_id=item.fact_metadata_revision_id or "",
        )
    elif item.item_kind == "claim_draft":
        _require_claim(
            conn,
            project_id=item.project_id,
            claim_draft_id=item.claim_draft_id or "",
        )
    else:  # Model validation should make this unreachable.
        raise IntakeIntegrityError(f"unknown item_kind: {item.item_kind}")

    row = conn.execute(
        """
        INSERT INTO research_evidence_intake_item
            (project_id, research_evidence_intake_id, source_snapshot_id,
             item_kind, candidate_fact_revision_id, fact_metadata_revision_id,
             claim_draft_id, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id::text, project_id::text,
                  research_evidence_intake_id::text, source_snapshot_id::text,
                  item_kind, candidate_fact_revision_id::text,
                  fact_metadata_revision_id::text, claim_draft_id::text,
                  state, created_by, created_at
        """,
        (
            item.project_id,
            item.research_evidence_intake_id,
            source_snapshot_id,
            item.item_kind,
            item.candidate_fact_revision_id,
            item.fact_metadata_revision_id,
            item.claim_draft_id,
            item.created_by,
        ),
    ).fetchone()
    return _item_from_row(row)


def _require_source_metadata(
    conn,
    *,
    project_id: str,
    source_snapshot_id: str,
    source_metadata_revision_id: str,
) -> None:
    row = conn.execute(
        """
        SELECT 1
        FROM research_source_metadata_revision
        WHERE id = %s AND project_id = %s AND source_snapshot_id = %s
        """,
        (source_metadata_revision_id, project_id, source_snapshot_id),
    ).fetchone()
    if row is None:
        raise IntakeParentNotFound(
            "source metadata revision not found for project and snapshot"
        )


def _require_fact_metadata(
    conn,
    *,
    project_id: str,
    source_snapshot_id: str,
    candidate_fact_revision_id: str,
    fact_metadata_revision_id: str,
) -> None:
    row = conn.execute(
        """
        SELECT 1
        FROM candidate_fact_revision f
        JOIN research_fact_metadata_revision m
          ON m.candidate_fact_revision_id = f.id
         AND m.project_id = f.project_id
        WHERE f.id = %s
          AND f.project_id = %s
          AND f.source_snapshot_id = %s
          AND m.id = %s
        """,
        (
            candidate_fact_revision_id,
            project_id,
            source_snapshot_id,
            fact_metadata_revision_id,
        ),
    ).fetchone()
    if row is None:
        raise IntakeParentNotFound(
            "fact metadata revision not found for project, fact, and snapshot"
        )


def _require_claim(conn, *, project_id: str, claim_draft_id: str) -> None:
    row = conn.execute(
        """
        SELECT 1
        FROM research_claim_draft
        WHERE id = %s AND project_id = %s
        """,
        (claim_draft_id, project_id),
    ).fetchone()
    if row is None:
        raise IntakeParentNotFound("claim draft not found for project")


def _intake_from_row(row) -> ResearchEvidenceIntakeRecord:
    return ResearchEvidenceIntakeRecord(
        id=row[0],
        project_id=row[1],
        source_snapshot_id=row[2],
        source_metadata_revision_id=row[3],
        intake_method=row[4],
        state=row[5],
        selection_reason=row[6],
        created_by=row[7],
        created_at=row[8],
    )


def _item_from_row(row) -> ResearchEvidenceIntakeItemRecord:
    return ResearchEvidenceIntakeItemRecord(
        id=row[0],
        project_id=row[1],
        research_evidence_intake_id=row[2],
        source_snapshot_id=row[3],
        item_kind=row[4],
        candidate_fact_revision_id=row[5],
        fact_metadata_revision_id=row[6],
        claim_draft_id=row[7],
        state=row[8],
        created_by=row[9],
        created_at=row[10],
    )
