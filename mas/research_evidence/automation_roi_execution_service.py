"""Transaction boundary for deterministic R1.6B Automation ROI execution."""
from __future__ import annotations

from contextlib import contextmanager

import config

from . import automation_roi_execution_repository as repository
from .automation_roi_execution_models import (
    AutomationRoiCalculationResult,
    AutomationRoiExecutionRequest,
)


class AutomationRoiExecutionDisabled(RuntimeError):
    """Research-evidence execution is feature-disabled."""


class AutomationRoiExecutionTransactionError(RuntimeError):
    """Caller-owned atomicity cannot be preserved."""


def _require_enabled() -> None:
    if not config.research_evidence_enabled():
        raise AutomationRoiExecutionDisabled(
            "Automation ROI evidence execution is disabled"
        )


@contextmanager
def _execution_write(conn):
    if conn.autocommit:
        raise AutomationRoiExecutionTransactionError(
            "Automation ROI execution requires a non-autocommit connection"
        )
    savepoint = "research_evidence_automation_roi_execution_service"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        yield
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    else:
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")


def execute_automation_roi(
    conn,
    request: AutomationRoiExecutionRequest,
    *,
    server_actor: str,
) -> AutomationRoiCalculationResult:
    """Execute an approved immutable snapshot; actor is server context, not request."""
    _require_enabled()
    payload = request.model_dump() if isinstance(
        request, AutomationRoiExecutionRequest
    ) else request
    request = AutomationRoiExecutionRequest.model_validate(payload)
    if not isinstance(server_actor, str) or not server_actor.strip():
        raise ValueError("server_actor must not be blank")
    with _execution_write(conn):
        return repository.execute(
            conn, request, requested_by=server_actor.strip()
        )
