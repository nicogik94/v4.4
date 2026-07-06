"""Controlled PostgreSQL persistence for R1.6B Automation ROI execution."""
from __future__ import annotations

import json
from typing import Optional

from .automation_roi_execution_models import (
    AutomationRoiCalculationResult,
    AutomationRoiExecutionRequest,
)
from .automation_roi_execution_policy import (
    FORMULA_FINGERPRINT,
    FORMULA_IDENTIFIER,
    FORMULA_VERSION,
)


class AutomationRoiExecutionRepositoryError(ValueError):
    """Base typed execution persistence error."""


class AutomationRoiExecutionConflict(AutomationRoiExecutionRepositoryError):
    """The idempotency key already names a different operation."""


class AutomationRoiExecutionRejected(AutomationRoiExecutionRepositoryError):
    """The snapshot is absent, out of scope, incomplete, or not approved."""


class AutomationRoiExecutionIntegrityError(AutomationRoiExecutionRepositoryError):
    """The database rejected the immutable result contract."""


_RESULT_SELECT = """
SELECT id::text, project_id::text, input_snapshot_id::text, consumer_contract,
       binding_set_id, idempotency_key, operation_digest, requested_by,
       computed_at, formula_identifier, formula_version, formula_fingerprint,
       assumption_set_version, assumptions_json, input_manifest_json,
       input_digest, provenance_fingerprint, output_units_json, status,
       currency_code, annual_labor_savings, annual_net_benefit,
       first_year_net_benefit, first_year_roi_percent, roi_percent_status,
       diagnostics_json
FROM research_evidence_automation_roi.automation_roi_calculation_result
"""


def _result_by_id(conn, result_id: str) -> Optional[AutomationRoiCalculationResult]:
    row = conn.execute(_RESULT_SELECT + " WHERE id = %s", (result_id,)).fetchone()
    return None if row is None else _result_from_row(row)


def _result_by_request(
    conn, request: AutomationRoiExecutionRequest
) -> Optional[AutomationRoiCalculationResult]:
    row = conn.execute(
        _RESULT_SELECT
        + " WHERE project_id = %s AND idempotency_key = %s",
        (request.project_id, request.idempotency_key),
    ).fetchone()
    return None if row is None else _result_from_row(row)


def _result_by_operation(
    conn, *, project_id: str, operation_digest_value: str
) -> Optional[AutomationRoiCalculationResult]:
    row = conn.execute(
        _RESULT_SELECT
        + " WHERE project_id = %s AND operation_digest = %s",
        (project_id, operation_digest_value),
    ).fetchone()
    return None if row is None else _result_from_row(row)


def execute(
    conn,
    request: AutomationRoiExecutionRequest,
    *,
    requested_by: str,
) -> AutomationRoiCalculationResult:
    """Invoke only the controlled function, with savepoint conflict recovery."""
    savepoint = "research_evidence_automation_roi_execution"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        row = conn.execute(
            """
            SELECT research_evidence_automation_roi.
                research_evidence_execute_automation_roi(%s::uuid, %s::uuid, %s, %s)::text
            """,
            (
                request.project_id,
                request.input_snapshot_id,
                request.idempotency_key,
                requested_by,
            ),
        ).fetchone()
        if row is None:
            raise AutomationRoiExecutionIntegrityError(
                "controlled execution returned no result identity"
            )
        result = _result_by_id(conn, row[0])
        if result is None:
            raise AutomationRoiExecutionIntegrityError(
                "controlled execution result is not visible"
            )
    except Exception as exc:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        sqlstate = str(getattr(exc, "sqlstate", "") or "")
        constraint = str(
            getattr(getattr(exc, "diag", None), "constraint_name", "") or ""
        )
        if sqlstate == "23505":
            expected_operation = operation_digest(
                request.project_id, request.input_snapshot_id
            )
            existing = _result_by_request(conn, request)
            if existing is not None:
                if existing.operation_digest == expected_operation:
                    return existing
                raise AutomationRoiExecutionConflict(
                    "idempotency_key already identifies a different operation"
                ) from exc
            existing = _result_by_operation(
                conn,
                project_id=request.project_id,
                operation_digest_value=expected_operation,
            )
            if existing is not None:
                return existing
            if constraint == "uq_rearoicr_project_idempotency":
                raise AutomationRoiExecutionConflict(
                    "idempotency_key already identifies a different operation"
                ) from exc
        if sqlstate in {"22023", "23503", "23514"}:
            raise AutomationRoiExecutionRejected(
                "Automation ROI execution request is not eligible"
            ) from exc
        if sqlstate.startswith("23"):
            raise AutomationRoiExecutionIntegrityError(
                "Automation ROI result violates its immutable contract"
            ) from exc
        raise
    else:
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        return result


def operation_digest(project_id: str, input_snapshot_id: str) -> str:
    """Mirror the fixed database operation identity for conflict recovery."""
    import hashlib

    value = (
        f"{project_id}\x1f{input_snapshot_id}\x1f"
        f"{FORMULA_IDENTIFIER}\x1f{FORMULA_VERSION}\x1f"
        f"{FORMULA_FINGERPRINT}"
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _result_from_row(row) -> AutomationRoiCalculationResult:
    values = list(row)
    for index in (13, 14, 17, 25):
        if isinstance(values[index], str):
            values[index] = json.loads(values[index])
    return AutomationRoiCalculationResult(
        **dict(
            zip(
                AutomationRoiCalculationResult.model_fields,
                values,
            )
        )
    )
