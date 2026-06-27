"""Feature-gated service writes for the R1.1 research-evidence sidecar."""
from __future__ import annotations

from contextlib import contextmanager

import config

from . import repository as repo
from .models import (
    ClaimDraftCreate,
    ClaimDraftRecord,
    EvidenceEventRecord,
    FactMetadataRevisionCreate,
    FactMetadataRevisionRecord,
    SourceMetadataRevisionCreate,
    SourceMetadataRevisionRecord,
    WithdrawalCommand,
)


class ResearchEvidenceDisabled(RuntimeError):
    """Raised when a sidecar write is attempted while the feature flag is off."""


class ResearchEvidenceTransactionError(RuntimeError):
    """Raised when a high-level write cannot preserve caller-owned atomicity."""


def _require_enabled() -> None:
    if not config.research_evidence_enabled():
        raise ResearchEvidenceDisabled(
            "Research evidence metadata sidecar is disabled "
            "(set MAS_RESEARCH_EVIDENCE_ENABLED to enable it)"
        )


@contextmanager
def _sidecar_write(conn):
    if conn.autocommit:
        raise ResearchEvidenceTransactionError(
            "research-evidence writes require a non-autocommit connection"
        )
    conn.execute("SAVEPOINT research_evidence_write")
    try:
        yield
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT research_evidence_write")
        conn.execute("RELEASE SAVEPOINT research_evidence_write")
        raise
    else:
        conn.execute("RELEASE SAVEPOINT research_evidence_write")


def create_source_metadata_revision(
    conn,
    revision: SourceMetadataRevisionCreate,
) -> SourceMetadataRevisionRecord:
    """Atomically insert source metadata and its required audit events."""
    _require_enabled()
    with _sidecar_write(conn):
        record = repo.insert_source_metadata_revision(conn, revision)
        if record.supersedes_metadata_revision_id:
            repo.insert_event(
                conn,
                project_id=record.project_id,
                entity_type="source_metadata_revision",
                entity_id=record.supersedes_metadata_revision_id,
                event_type="superseded",
                actor=record.created_by,
                details_json={"superseded_by_entity_id": record.id},
            )
            event_type = "correction_recorded"
            details = {
                "source_snapshot_id": record.source_snapshot_id,
                "supersedes_entity_id": record.supersedes_metadata_revision_id,
            }
        else:
            event_type = "created"
            details = {"source_snapshot_id": record.source_snapshot_id}
        repo.insert_event(
            conn,
            project_id=record.project_id,
            entity_type="source_metadata_revision",
            entity_id=record.id,
            event_type=event_type,
            actor=record.created_by,
            details_json=details,
        )
    return record


def create_fact_metadata_revision(
    conn,
    revision: FactMetadataRevisionCreate,
) -> FactMetadataRevisionRecord:
    """Atomically insert fact metadata and its required audit events."""
    _require_enabled()
    with _sidecar_write(conn):
        record = repo.insert_fact_metadata_revision(conn, revision)
        if record.supersedes_metadata_revision_id:
            repo.insert_event(
                conn,
                project_id=record.project_id,
                entity_type="fact_metadata_revision",
                entity_id=record.supersedes_metadata_revision_id,
                event_type="superseded",
                actor=record.created_by,
                details_json={"superseded_by_entity_id": record.id},
            )
            event_type = "correction_recorded"
            details = {
                "candidate_fact_revision_id": record.candidate_fact_revision_id,
                "supersedes_entity_id": record.supersedes_metadata_revision_id,
            }
        else:
            event_type = "created"
            details = {"candidate_fact_revision_id": record.candidate_fact_revision_id}
        repo.insert_event(
            conn,
            project_id=record.project_id,
            entity_type="fact_metadata_revision",
            entity_id=record.id,
            event_type=event_type,
            actor=record.created_by,
            details_json=details,
        )
    return record


def create_claim_draft(conn, claim: ClaimDraftCreate) -> ClaimDraftRecord:
    """Atomically insert a draft claim and its required audit events."""
    _require_enabled()
    with _sidecar_write(conn):
        record = repo.insert_claim_draft(conn, claim)
        if record.supersedes_claim_id:
            repo.insert_event(
                conn,
                project_id=record.project_id,
                entity_type="claim_draft",
                entity_id=record.supersedes_claim_id,
                event_type="superseded",
                actor=record.created_by,
                details_json={"superseded_by_entity_id": record.id},
            )
            event_type = "correction_recorded"
            details = {"supersedes_entity_id": record.supersedes_claim_id}
        else:
            event_type = "created"
            details = {}
        repo.insert_event(
            conn,
            project_id=record.project_id,
            entity_type="claim_draft",
            entity_id=record.id,
            event_type=event_type,
            actor=record.created_by,
            details_json=details,
        )
    return record


def withdraw_entity(
    conn,
    *,
    project_id: str,
    entity_type: str,
    entity_id: str,
    actor: str = "",
    reason: str = "",
) -> EvidenceEventRecord:
    """Append one withdrawal event without mutating its target."""
    _require_enabled()
    command = WithdrawalCommand(
        project_id=project_id,
        entity_type=entity_type,
        entity_id=entity_id,
        actor=actor,
        reason=reason,
    )
    with _sidecar_write(conn):
        return repo.insert_event(
            conn,
            project_id=command.project_id,
            entity_type=command.entity_type,
            entity_id=command.entity_id,
            event_type="withdrawn",
            actor=command.actor,
            details_json={"reason": command.reason},
        )
