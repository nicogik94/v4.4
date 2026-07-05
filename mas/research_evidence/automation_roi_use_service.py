"""Feature-gated R1.6A Automation ROI snapshot service."""
from __future__ import annotations

from contextlib import contextmanager

import config

from . import automation_roi_use_repository as repo
from .automation_roi_use_models import (
    AutomationRoiInputSnapshotCreate,
    AutomationRoiInputSnapshotRecord,
)


class AutomationRoiUseDisabled(RuntimeError):
    """Raised when research-evidence snapshot access is disabled."""


class AutomationRoiUseTransactionError(RuntimeError):
    """Raised when caller-owned snapshot atomicity cannot be preserved."""


def _require_enabled() -> None:
    if not config.research_evidence_enabled():
        raise AutomationRoiUseDisabled(
            "Automation ROI evidence snapshots are disabled "
            "(set MAS_RESEARCH_EVIDENCE_ENABLED to enable them)"
        )


@contextmanager
def _snapshot_write(conn):
    if conn.autocommit:
        raise AutomationRoiUseTransactionError(
            "Automation ROI snapshot writes require a non-autocommit connection"
        )
    conn.execute("SAVEPOINT research_evidence_automation_roi_snapshot_write")
    try:
        yield
    except Exception:
        conn.execute(
            "ROLLBACK TO SAVEPOINT research_evidence_automation_roi_snapshot_write"
        )
        conn.execute(
            "RELEASE SAVEPOINT research_evidence_automation_roi_snapshot_write"
        )
        raise
    else:
        conn.execute(
            "RELEASE SAVEPOINT research_evidence_automation_roi_snapshot_write"
        )


def record_automation_roi_input_snapshot(
    conn,
    command: AutomationRoiInputSnapshotCreate,
) -> AutomationRoiInputSnapshotRecord:
    """Append or idempotently return one explicit six-binding snapshot."""
    command = AutomationRoiInputSnapshotCreate.model_validate(
        command.model_dump()
        if isinstance(command, AutomationRoiInputSnapshotCreate)
        else command
    )
    _require_enabled()
    with _snapshot_write(conn):
        existing = repo._get_snapshot_by_request_id(
            conn,
            project_id=command.project_id,
            binding_set_id=command.binding_set_id,
            request_id=command.request_id,
        )
        if existing is not None:
            return repo.ensure_retry_matches(existing, command)
        return repo.insert_snapshot(conn, command)
