"""Feature-gated service writes for the R1.1 research-evidence sidecar."""
from __future__ import annotations

import config

from . import repository as repo
from .models import (
    ClaimDraftCreate,
    ClaimDraftRecord,
    FactMetadataRevisionCreate,
    FactMetadataRevisionRecord,
    SourceMetadataRevisionCreate,
    SourceMetadataRevisionRecord,
)


class ResearchEvidenceDisabled(RuntimeError):
    """Raised when a sidecar write is attempted while the feature flag is off."""


def _require_enabled() -> None:
    if not config.research_evidence_enabled():
        raise ResearchEvidenceDisabled(
            "Research evidence metadata sidecar is disabled "
            "(set MAS_RESEARCH_EVIDENCE_ENABLED to enable it)"
        )


def create_source_metadata_revision(
    conn,
    revision: SourceMetadataRevisionCreate,
) -> SourceMetadataRevisionRecord:
    """Insert source metadata and its audit event on the same connection."""
    _require_enabled()
    record = repo.insert_source_metadata_revision(conn, revision)
    repo.insert_event(
        conn,
        project_id=record.project_id,
        entity_type="source_metadata_revision",
        entity_id=record.id,
        event_type="correction_recorded" if record.supersedes_metadata_revision_id else "created",
        actor=record.created_by,
        details_json={"source_snapshot_id": record.source_snapshot_id},
    )
    return record


def create_fact_metadata_revision(
    conn,
    revision: FactMetadataRevisionCreate,
) -> FactMetadataRevisionRecord:
    """Insert fact metadata and its audit event on the same connection."""
    _require_enabled()
    record = repo.insert_fact_metadata_revision(conn, revision)
    repo.insert_event(
        conn,
        project_id=record.project_id,
        entity_type="fact_metadata_revision",
        entity_id=record.id,
        event_type="correction_recorded" if record.supersedes_metadata_revision_id else "created",
        actor=record.created_by,
        details_json={"candidate_fact_revision_id": record.candidate_fact_revision_id},
    )
    return record


def create_claim_draft(conn, claim: ClaimDraftCreate) -> ClaimDraftRecord:
    """Insert an isolated draft claim and its audit event on the same connection."""
    _require_enabled()
    record = repo.insert_claim_draft(conn, claim)
    repo.insert_event(
        conn,
        project_id=record.project_id,
        entity_type="claim_draft",
        entity_id=record.id,
        event_type="correction_recorded" if record.supersedes_claim_id else "created",
        actor=record.created_by,
        details_json={},
    )
    return record
