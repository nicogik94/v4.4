"""Feature-gated, caller-transaction-owned R1.2 intake writes."""
from __future__ import annotations

from contextlib import contextmanager

import config

from . import intake_repository as repo
from .intake_models import (
    ResearchEvidenceIntakeCreate,
    ResearchEvidenceIntakeItemCreate,
    ResearchEvidenceIntakeItemRecord,
    ResearchEvidenceIntakeRecord,
)


class ResearchEvidenceIntakeDisabled(RuntimeError):
    """Raised when an intake write is attempted while the feature is disabled."""


class ResearchEvidenceIntakeTransactionError(RuntimeError):
    """Raised when caller-owned atomicity cannot be preserved."""


class ResearchEvidenceSnapshotUnavailable(ValueError):
    """The selected canonical snapshot is tombstoned or redacted."""


def _require_enabled() -> None:
    if not config.research_evidence_enabled():
        raise ResearchEvidenceIntakeDisabled(
            "Research evidence intake is disabled "
            "(set MAS_RESEARCH_EVIDENCE_ENABLED to enable it)"
        )


@contextmanager
def _intake_write(conn):
    if conn.autocommit:
        raise ResearchEvidenceIntakeTransactionError(
            "research-evidence intake writes require a non-autocommit connection"
        )
    conn.execute("SAVEPOINT research_evidence_intake_write")
    try:
        yield
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT research_evidence_intake_write")
        conn.execute("RELEASE SAVEPOINT research_evidence_intake_write")
        raise
    else:
        conn.execute("RELEASE SAVEPOINT research_evidence_intake_write")


def create_intake(
    conn,
    intake: ResearchEvidenceIntakeCreate,
) -> ResearchEvidenceIntakeRecord:
    """Create one operator-selected draft intake around an existing snapshot."""
    intake = ResearchEvidenceIntakeCreate.model_validate(
        intake.model_dump()
        if isinstance(intake, ResearchEvidenceIntakeCreate)
        else intake
    )
    _require_enabled()
    with _intake_write(conn):
        if not repo.snapshot_is_available(
            conn,
            project_id=intake.project_id,
            source_snapshot_id=intake.source_snapshot_id,
        ):
            raise ResearchEvidenceSnapshotUnavailable(
                "source snapshot is tombstoned or redacted"
            )
        return repo.insert_intake(conn, intake)


def create_intake_item(
    conn,
    item: ResearchEvidenceIntakeItemCreate,
) -> ResearchEvidenceIntakeItemRecord:
    """Create one draft binding, deriving its snapshot from the intake."""
    item = ResearchEvidenceIntakeItemCreate.model_validate(
        item.model_dump()
        if isinstance(item, ResearchEvidenceIntakeItemCreate)
        else item
    )
    _require_enabled()
    with _intake_write(conn):
        intake = repo.get_intake(
            conn,
            project_id=item.project_id,
            intake_id=item.research_evidence_intake_id,
        )
        if intake is None:
            raise repo.IntakeParentNotFound(
                "research evidence intake not found for project"
            )
        if not repo.snapshot_is_available(
            conn,
            project_id=item.project_id,
            source_snapshot_id=intake.source_snapshot_id,
        ):
            raise ResearchEvidenceSnapshotUnavailable(
                "source snapshot is tombstoned or redacted"
            )
        return repo.insert_item(
            conn,
            item,
            source_snapshot_id=intake.source_snapshot_id,
        )
