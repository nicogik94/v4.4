"""Narrow insert/read repository for Slice A evidence-snapshot records.

Append-only at the database layer (enforced by triggers in
``sql/v47_evidence_snapshot_foundation.sql``); this interface deliberately offers
only inserts, status transitions on IngestOperation, and reads. There is no
update or delete path for the four immutable tables, and no in-memory fallback.

All functions operate on a caller-supplied synchronous ``psycopg`` connection so
the authoritative MAS database (production) or a dependency-injected disposable
database (tests) can be used interchangeably. Connection lifecycle and the
feature flag are owned by the caller (see ``capture.py``).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from .validation import ValidatedFact

# Retention event types that block future *use* via the availability resolver.
_BLOCKING_EVENT_TYPES = ("tombstone", "redact")


@dataclass
class IngestOperationRecord:
    id: str
    project_id: str
    operation_id: str
    status: str
    source_snapshot_id: Optional[str]
    detail: str
    created_at: datetime
    updated_at: datetime
    existed: bool = False


# ─────────────────────────────── Projects (test/support) ───────────────────────────────

def insert_project(conn, *, name: str, brief: str = "") -> str:
    """Insert a minimal parent project row and return its id."""
    row = conn.execute(
        "INSERT INTO projects (name, brief) VALUES (%s, %s) RETURNING id::text",
        (name, brief),
    ).fetchone()
    return row[0]


# ─────────────────────────────── SourceBlob ───────────────────────────────

def insert_or_get_blob(
    conn,
    *,
    project_id: str,
    content_hash: str,
    byte_size: int,
    hash_algorithm: str = "sha256",
    created_by: str = "",
) -> str:
    """Return the blob id for this project's content identity, creating it if new.

    Deduplicates only within a project via unique(project_id, hash_algorithm,
    content_hash). Identical bytes in different projects yield distinct blobs.
    """
    row = conn.execute(
        """
        INSERT INTO source_blob (project_id, hash_algorithm, content_hash, byte_size, created_by)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (project_id, hash_algorithm, content_hash) DO NOTHING
        RETURNING id::text
        """,
        (project_id, hash_algorithm, content_hash, byte_size, created_by),
    ).fetchone()
    if row is not None:
        return row[0]
    existing = conn.execute(
        """
        SELECT id::text FROM source_blob
        WHERE project_id = %s AND hash_algorithm = %s AND content_hash = %s
        """,
        (project_id, hash_algorithm, content_hash),
    ).fetchone()
    return existing[0]


# ─────────────────────────────── SourceSnapshot ───────────────────────────────

def insert_snapshot(
    conn,
    *,
    source_blob_id: str,
    project_id: str,
    storage_ref: str,
    source_kind: str = "",
    source_locator: str = "",
    ingest_operation_id: str = "",
    captured_by: str = "",
) -> str:
    """Insert one immutable capture event. Never deduplicates by content hash."""
    if not storage_ref:
        raise ValueError("Snapshot creation requires a stable existing storage_ref.")
    row = conn.execute(
        """
        INSERT INTO source_snapshot
            (source_blob_id, project_id, storage_ref, source_kind, source_locator,
             ingest_operation_id, captured_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id::text
        """,
        (source_blob_id, project_id, storage_ref, source_kind, source_locator,
         ingest_operation_id, captured_by),
    ).fetchone()
    return row[0]


def find_snapshots_by_storage_ref(conn, storage_ref: str) -> list[str]:
    """Return snapshot ids whose capture references this exact storage_ref."""
    rows = conn.execute(
        "SELECT id::text FROM source_snapshot WHERE storage_ref = %s",
        (storage_ref,),
    ).fetchall()
    return [r[0] for r in rows]


# ─────────────────────────────── IngestOperation ───────────────────────────────

def create_or_get_ingest_operation(
    conn,
    *,
    project_id: str,
    operation_id: str,
    detail: str = "",
) -> IngestOperationRecord:
    """Idempotently create an ingest operation keyed by (project_id, operation_id).

    A repeated operation_id returns the existing row (``existed=True``) so retries
    do not create duplicate capture events. A new operation_id is a new capture
    event even when the bytes are identical.
    """
    row = conn.execute(
        """
        INSERT INTO ingest_operation (project_id, operation_id, status, detail)
        VALUES (%s, %s, 'pending', %s)
        ON CONFLICT (project_id, operation_id) DO NOTHING
        RETURNING id::text, status, source_snapshot_id::text, detail, created_at, updated_at
        """,
        (project_id, operation_id, detail),
    ).fetchone()
    existed = row is None
    if existed:
        row = conn.execute(
            """
            SELECT id::text, status, source_snapshot_id::text, detail, created_at, updated_at
            FROM ingest_operation
            WHERE project_id = %s AND operation_id = %s
            """,
            (project_id, operation_id),
        ).fetchone()
    return IngestOperationRecord(
        id=row[0], project_id=project_id, operation_id=operation_id, status=row[1],
        source_snapshot_id=row[2], detail=row[3], created_at=row[4], updated_at=row[5],
        existed=existed,
    )


def set_ingest_status(
    conn,
    *,
    operation_pk: str,
    status: str,
    source_snapshot_id: Optional[str] = None,
    detail: Optional[str] = None,
) -> None:
    """Transition the operational status of an ingest operation (the only mutable record)."""
    conn.execute(
        """
        UPDATE ingest_operation
        SET status = %s,
            source_snapshot_id = COALESCE(%s, source_snapshot_id),
            detail = COALESCE(%s, detail),
            updated_at = NOW()
        WHERE id = %s
        """,
        (status, source_snapshot_id, detail, operation_pk),
    )


def get_ingest_operation(conn, *, project_id: str, operation_id: str) -> Optional[IngestOperationRecord]:
    row = conn.execute(
        """
        SELECT id::text, status, source_snapshot_id::text, detail, created_at, updated_at
        FROM ingest_operation WHERE project_id = %s AND operation_id = %s
        """,
        (project_id, operation_id),
    ).fetchone()
    if row is None:
        return None
    return IngestOperationRecord(
        id=row[0], project_id=project_id, operation_id=operation_id, status=row[1],
        source_snapshot_id=row[2], detail=row[3], created_at=row[4], updated_at=row[5],
        existed=True,
    )


# ─────────────────────────────── CandidateFactRevision ───────────────────────────────

def insert_fact(
    conn,
    *,
    project_id: str,
    source_snapshot_id: str,
    fact: ValidatedFact,
    created_by: str = "",
) -> str:
    """Insert a validated, typed, source-derived fact bound to one snapshot."""
    if not source_snapshot_id:
        raise ValueError("CandidateFactRevision requires a source_snapshot_id (NOT NULL).")
    row = conn.execute(
        """
        INSERT INTO candidate_fact_revision
            (project_id, source_snapshot_id, fact_type, numeric_value, text_value, unit,
             currency_code, as_of_date, numerator_context, denominator_context,
             percentage_basis, percentage_subtype, time_unit, counted_entity, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id::text
        """,
        (
            project_id, source_snapshot_id, fact.fact_type, fact.numeric_value,
            fact.text_value, fact.unit, fact.currency_code, fact.as_of_date,
            fact.numerator_context, fact.denominator_context, fact.percentage_basis,
            fact.percentage_subtype, fact.time_unit, fact.counted_entity, created_by,
        ),
    ).fetchone()
    return row[0]


# ─────────────────────────────── EvidenceRetentionEvent ───────────────────────────────

def insert_retention_event(
    conn,
    *,
    project_id: str,
    event_type: str,
    source_blob_id: Optional[str] = None,
    source_snapshot_id: Optional[str] = None,
    candidate_fact_revision_id: Optional[str] = None,
    reason: str = "",
    created_by: str = "",
) -> str:
    """Append one retention event targeting exactly one blob, snapshot, or fact.

    The database enforces the single-target XOR constraint and the allowed event
    types (legal_hold, tombstone, redact).
    """
    row = conn.execute(
        """
        INSERT INTO evidence_retention_event
            (project_id, event_type, source_blob_id, source_snapshot_id,
             candidate_fact_revision_id, reason, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id::text
        """,
        (project_id, event_type, source_blob_id, source_snapshot_id,
         candidate_fact_revision_id, reason, created_by),
    ).fetchone()
    return row[0]


# ─────────────────────────────── Availability resolver ───────────────────────────────
# Availability is derived, never stored. legal_hold never affects availability;
# only tombstone/redact do.

def snapshot_available(conn, snapshot_id: str) -> bool:
    """A snapshot is unavailable when it, or its blob, is tombstoned or redacted."""
    row = conn.execute(
        """
        SELECT NOT EXISTS (
            SELECT 1
            FROM source_snapshot s
            JOIN evidence_retention_event e
              ON e.event_type = ANY(%s)
             AND (e.source_snapshot_id = s.id OR e.source_blob_id = s.source_blob_id)
            WHERE s.id = %s
        )
        """,
        (list(_BLOCKING_EVENT_TYPES), snapshot_id),
    ).fetchone()
    return bool(row[0])


def fact_available(conn, fact_id: str) -> bool:
    """A fact is unavailable when the fact, its snapshot, or its blob is tombstoned or redacted."""
    row = conn.execute(
        """
        SELECT NOT EXISTS (
            SELECT 1
            FROM candidate_fact_revision f
            JOIN source_snapshot s ON s.id = f.source_snapshot_id
            JOIN evidence_retention_event e
              ON e.event_type = ANY(%s)
             AND (
                   e.candidate_fact_revision_id = f.id
                OR e.source_snapshot_id = s.id
                OR e.source_blob_id = s.source_blob_id
             )
            WHERE f.id = %s
        )
        """,
        (list(_BLOCKING_EVENT_TYPES), fact_id),
    ).fetchone()
    return bool(row[0])
