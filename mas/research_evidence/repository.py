"""Low-level PostgreSQL helpers for the R1.1 research-evidence sidecar.

All helpers use a caller-supplied synchronous connection. They insert or read
sidecar records only; canonical source/fact provenance remains in Slice A.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from .models import (
    ClaimDraftCreate,
    ClaimDraftRecord,
    EvidenceEventRecord,
    FactMetadataRevisionCreate,
    FactMetadataRevisionRecord,
    SourceMetadataRevisionCreate,
    SourceMetadataRevisionRecord,
)


class SidecarParentNotFound(ValueError):
    """Raised when a required Slice A or project-scoped parent row is absent."""


class SidecarIntegrityError(ValueError):
    """Raised when a sidecar request violates the sidecar contract."""


ENTITY_TYPES = {"source_metadata_revision", "fact_metadata_revision", "claim_draft"}
EVENT_TYPES = {"created", "superseded", "correction_recorded", "withdrawn"}


def insert_source_metadata_revision(
    conn,
    revision: SourceMetadataRevisionCreate,
) -> SourceMetadataRevisionRecord:
    _require_project(conn, revision.project_id)
    _require_snapshot(conn, project_id=revision.project_id, snapshot_id=revision.source_snapshot_id)
    if revision.supersedes_metadata_revision_id:
        _require_source_metadata_supersession(
            conn,
            project_id=revision.project_id,
            source_snapshot_id=revision.source_snapshot_id,
            supersedes_id=revision.supersedes_metadata_revision_id,
        )
    row = conn.execute(
        """
        INSERT INTO research_source_metadata_revision
            (project_id, source_snapshot_id, canonical_source_locator, publisher, author,
             published_at, retrieved_at, citation_label, declared_quality_tier,
             declared_quality_rationale, metadata_json, supersedes_metadata_revision_id,
             created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
        RETURNING id::text, project_id::text, source_snapshot_id::text,
                  canonical_source_locator, publisher, author, published_at,
                  retrieved_at, citation_label, declared_quality_tier,
                  declared_quality_rationale, metadata_json,
                  supersedes_metadata_revision_id::text, created_by, created_at
        """,
        (
            revision.project_id,
            revision.source_snapshot_id,
            revision.canonical_source_locator,
            revision.publisher,
            revision.author,
            revision.published_at,
            revision.retrieved_at,
            revision.citation_label,
            revision.declared_quality_tier,
            revision.declared_quality_rationale,
            _json_object(revision.metadata_json),
            revision.supersedes_metadata_revision_id,
            revision.created_by,
        ),
    ).fetchone()
    return _source_metadata_from_row(row)


def insert_fact_metadata_revision(
    conn,
    revision: FactMetadataRevisionCreate,
) -> FactMetadataRevisionRecord:
    _require_project(conn, revision.project_id)
    _require_fact(conn, project_id=revision.project_id, fact_id=revision.candidate_fact_revision_id)
    if revision.supersedes_candidate_fact_revision_id:
        _require_fact(
            conn,
            project_id=revision.project_id,
            fact_id=revision.supersedes_candidate_fact_revision_id,
        )
    if revision.supersedes_metadata_revision_id:
        _require_fact_metadata_supersession(
            conn,
            project_id=revision.project_id,
            fact_id=revision.candidate_fact_revision_id,
            supersedes_id=revision.supersedes_metadata_revision_id,
        )
    row = conn.execute(
        """
        INSERT INTO research_fact_metadata_revision
            (project_id, candidate_fact_revision_id, stable_fact_key, drift_group_key,
             supersedes_candidate_fact_revision_id, source_char_range, excerpt_hash,
             citation_locator, metadata_json, supersedes_metadata_revision_id, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
        RETURNING id::text, project_id::text, candidate_fact_revision_id::text,
                  stable_fact_key, drift_group_key,
                  supersedes_candidate_fact_revision_id::text, source_char_range,
                  excerpt_hash, citation_locator, metadata_json,
                  supersedes_metadata_revision_id::text, created_by, created_at
        """,
        (
            revision.project_id,
            revision.candidate_fact_revision_id,
            revision.stable_fact_key,
            revision.drift_group_key,
            revision.supersedes_candidate_fact_revision_id,
            revision.source_char_range,
            revision.excerpt_hash,
            revision.citation_locator,
            _json_object(revision.metadata_json),
            revision.supersedes_metadata_revision_id,
            revision.created_by,
        ),
    ).fetchone()
    return _fact_metadata_from_row(row)


def insert_claim_draft(conn, claim: ClaimDraftCreate) -> ClaimDraftRecord:
    _require_project(conn, claim.project_id)
    if claim.supersedes_claim_id:
        _require_claim(conn, project_id=claim.project_id, claim_id=claim.supersedes_claim_id)
    row = conn.execute(
        """
        INSERT INTO research_claim_draft
            (project_id, claim_text, claim_category, supersedes_claim_id, created_by)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id::text, project_id::text, claim_text, claim_category,
                  supersedes_claim_id::text, created_by, created_at
        """,
        (
            claim.project_id,
            claim.claim_text,
            claim.claim_category,
            claim.supersedes_claim_id,
            claim.created_by,
        ),
    ).fetchone()
    return _claim_from_row(row)


def insert_event(
    conn,
    *,
    project_id: str,
    entity_type: str,
    entity_id: str,
    event_type: str,
    actor: str = "",
    details_json: Optional[dict[str, Any]] = None,
) -> EvidenceEventRecord:
    if entity_type not in ENTITY_TYPES:
        raise SidecarIntegrityError(f"unknown entity_type: {entity_type}")
    if event_type not in EVENT_TYPES:
        raise SidecarIntegrityError(f"unknown event_type: {event_type}")
    _require_project(conn, project_id)
    sequence = next_event_sequence(
        conn,
        project_id=project_id,
        entity_type=entity_type,
        entity_id=entity_id,
    )
    row = conn.execute(
        """
        INSERT INTO research_evidence_event
            (project_id, entity_type, entity_id, event_type, event_sequence, actor, details_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
        RETURNING id::text, project_id::text, entity_type, entity_id::text,
                  event_type, event_sequence, occurred_at, actor, details_json
        """,
        (
            project_id,
            entity_type,
            entity_id,
            event_type,
            sequence,
            actor,
            _json_object(details_json or {}),
        ),
    ).fetchone()
    return _event_from_row(row)


def next_event_sequence(conn, *, project_id: str, entity_type: str, entity_id: str) -> int:
    row = conn.execute(
        """
        SELECT COALESCE(MAX(event_sequence), 0) + 1
        FROM research_evidence_event
        WHERE project_id = %s AND entity_type = %s AND entity_id = %s
        """,
        (project_id, entity_type, entity_id),
    ).fetchone()
    return int(row[0])


def list_source_metadata_revisions(
    conn,
    *,
    project_id: str,
    source_snapshot_id: Optional[str] = None,
) -> list[SourceMetadataRevisionRecord]:
    if source_snapshot_id:
        rows = conn.execute(
            """
            SELECT id::text, project_id::text, source_snapshot_id::text,
                   canonical_source_locator, publisher, author, published_at,
                   retrieved_at, citation_label, declared_quality_tier,
                   declared_quality_rationale, metadata_json,
                   supersedes_metadata_revision_id::text, created_by, created_at
            FROM research_source_metadata_revision
            WHERE project_id = %s AND source_snapshot_id = %s
            ORDER BY created_at, id
            """,
            (project_id, source_snapshot_id),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id::text, project_id::text, source_snapshot_id::text,
                   canonical_source_locator, publisher, author, published_at,
                   retrieved_at, citation_label, declared_quality_tier,
                   declared_quality_rationale, metadata_json,
                   supersedes_metadata_revision_id::text, created_by, created_at
            FROM research_source_metadata_revision
            WHERE project_id = %s
            ORDER BY created_at, id
            """,
            (project_id,),
        ).fetchall()
    return [_source_metadata_from_row(row) for row in rows]


def list_fact_metadata_revisions(
    conn,
    *,
    project_id: str,
    candidate_fact_revision_id: Optional[str] = None,
) -> list[FactMetadataRevisionRecord]:
    if candidate_fact_revision_id:
        rows = conn.execute(
            """
            SELECT id::text, project_id::text, candidate_fact_revision_id::text,
                   stable_fact_key, drift_group_key,
                   supersedes_candidate_fact_revision_id::text, source_char_range,
                   excerpt_hash, citation_locator, metadata_json,
                   supersedes_metadata_revision_id::text, created_by, created_at
            FROM research_fact_metadata_revision
            WHERE project_id = %s AND candidate_fact_revision_id = %s
            ORDER BY created_at, id
            """,
            (project_id, candidate_fact_revision_id),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id::text, project_id::text, candidate_fact_revision_id::text,
                   stable_fact_key, drift_group_key,
                   supersedes_candidate_fact_revision_id::text, source_char_range,
                   excerpt_hash, citation_locator, metadata_json,
                   supersedes_metadata_revision_id::text, created_by, created_at
            FROM research_fact_metadata_revision
            WHERE project_id = %s
            ORDER BY created_at, id
            """,
            (project_id,),
        ).fetchall()
    return [_fact_metadata_from_row(row) for row in rows]


def list_claim_drafts(conn, *, project_id: str) -> list[ClaimDraftRecord]:
    rows = conn.execute(
        """
        SELECT id::text, project_id::text, claim_text, claim_category,
               supersedes_claim_id::text, created_by, created_at
        FROM research_claim_draft
        WHERE project_id = %s
        ORDER BY created_at, id
        """,
        (project_id,),
    ).fetchall()
    return [_claim_from_row(row) for row in rows]


def list_events(
    conn,
    *,
    project_id: str,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
) -> list[EvidenceEventRecord]:
    if entity_type and entity_id:
        rows = conn.execute(
            """
            SELECT id::text, project_id::text, entity_type, entity_id::text,
                   event_type, event_sequence, occurred_at, actor, details_json
            FROM research_evidence_event
            WHERE project_id = %s AND entity_type = %s AND entity_id = %s
            ORDER BY event_sequence, occurred_at, id
            """,
            (project_id, entity_type, entity_id),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id::text, project_id::text, entity_type, entity_id::text,
                   event_type, event_sequence, occurred_at, actor, details_json
            FROM research_evidence_event
            WHERE project_id = %s
            ORDER BY occurred_at, id
            """,
            (project_id,),
        ).fetchall()
    return [_event_from_row(row) for row in rows]


def _require_project(conn, project_id: str) -> None:
    row = conn.execute("SELECT 1 FROM projects WHERE id = %s", (project_id,)).fetchone()
    if row is None:
        raise SidecarParentNotFound("project not found")


def _require_snapshot(conn, *, project_id: str, snapshot_id: str) -> None:
    row = conn.execute(
        "SELECT 1 FROM source_snapshot WHERE id = %s AND project_id = %s",
        (snapshot_id, project_id),
    ).fetchone()
    if row is None:
        raise SidecarParentNotFound("source snapshot not found for project")


def _require_fact(conn, *, project_id: str, fact_id: str) -> None:
    row = conn.execute(
        "SELECT 1 FROM candidate_fact_revision WHERE id = %s AND project_id = %s",
        (fact_id, project_id),
    ).fetchone()
    if row is None:
        raise SidecarParentNotFound("candidate fact revision not found for project")


def _require_claim(conn, *, project_id: str, claim_id: str) -> None:
    row = conn.execute(
        "SELECT 1 FROM research_claim_draft WHERE id = %s AND project_id = %s",
        (claim_id, project_id),
    ).fetchone()
    if row is None:
        raise SidecarParentNotFound("claim draft not found for project")


def _require_source_metadata_supersession(
    conn,
    *,
    project_id: str,
    source_snapshot_id: str,
    supersedes_id: str,
) -> None:
    row = conn.execute(
        """
        SELECT 1
        FROM research_source_metadata_revision
        WHERE id = %s AND project_id = %s AND source_snapshot_id = %s
        """,
        (supersedes_id, project_id, source_snapshot_id),
    ).fetchone()
    if row is None:
        raise SidecarParentNotFound("source metadata supersession target not found")


def _require_fact_metadata_supersession(
    conn,
    *,
    project_id: str,
    fact_id: str,
    supersedes_id: str,
) -> None:
    row = conn.execute(
        """
        SELECT 1
        FROM research_fact_metadata_revision
        WHERE id = %s AND project_id = %s AND candidate_fact_revision_id = %s
        """,
        (supersedes_id, project_id, fact_id),
    ).fetchone()
    if row is None:
        raise SidecarParentNotFound("fact metadata supersession target not found")


def _json_object(value: dict[str, Any]) -> str:
    if not isinstance(value, dict):
        raise SidecarIntegrityError("JSON sidecar payload must be an object")
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _json_from_db(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        decoded = json.loads(value)
        if isinstance(decoded, dict):
            return decoded
    raise SidecarIntegrityError("JSON sidecar payload must decode to an object")


def _source_metadata_from_row(row) -> SourceMetadataRevisionRecord:
    return SourceMetadataRevisionRecord(
        id=row[0],
        project_id=row[1],
        source_snapshot_id=row[2],
        canonical_source_locator=row[3],
        publisher=row[4],
        author=row[5],
        published_at=row[6],
        retrieved_at=row[7],
        citation_label=row[8],
        declared_quality_tier=row[9],
        declared_quality_rationale=row[10],
        metadata_json=_json_from_db(row[11]),
        supersedes_metadata_revision_id=row[12],
        created_by=row[13],
        created_at=row[14],
    )


def _fact_metadata_from_row(row) -> FactMetadataRevisionRecord:
    return FactMetadataRevisionRecord(
        id=row[0],
        project_id=row[1],
        candidate_fact_revision_id=row[2],
        stable_fact_key=row[3],
        drift_group_key=row[4],
        supersedes_candidate_fact_revision_id=row[5],
        source_char_range=row[6],
        excerpt_hash=row[7],
        citation_locator=row[8],
        metadata_json=_json_from_db(row[9]),
        supersedes_metadata_revision_id=row[10],
        created_by=row[11],
        created_at=row[12],
    )


def _claim_from_row(row) -> ClaimDraftRecord:
    return ClaimDraftRecord(
        id=row[0],
        project_id=row[1],
        claim_text=row[2],
        claim_category=row[3],
        supersedes_claim_id=row[4],
        created_by=row[5],
        created_at=row[6],
    )


def _event_from_row(row) -> EvidenceEventRecord:
    return EvidenceEventRecord(
        id=row[0],
        project_id=row[1],
        entity_type=row[2],
        entity_id=row[3],
        event_type=row[4],
        event_sequence=row[5],
        occurred_at=row[6],
        actor=row[7],
        details_json=_json_from_db(row[8]),
    )
