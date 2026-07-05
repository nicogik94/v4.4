"""PostgreSQL persistence for R1.6A Automation ROI input snapshots."""
from __future__ import annotations

import json
from typing import Optional

from .automation_roi_use_models import (
    AutomationRoiInputSnapshotBindingRecord,
    AutomationRoiInputSnapshotCreate,
    AutomationRoiInputSnapshotRecord,
)
from .automation_roi_use_policy import CONSUMER_CONTRACT


class AutomationRoiUseRepositoryError(ValueError):
    """Base error for scoped snapshot persistence failures."""


class AutomationRoiBindingSetIncomplete(AutomationRoiUseRepositoryError):
    """The explicit binding IDs do not resolve to exactly six canonical roles."""


class AutomationRoiSnapshotIntegrityError(AutomationRoiUseRepositoryError):
    """The immutable snapshot contract was rejected by PostgreSQL."""


class AutomationRoiSnapshotRequestConflict(AutomationRoiSnapshotIntegrityError):
    """A request ID already identifies a different immutable snapshot."""


def _get_snapshot_by_request_id(
    conn,
    *,
    project_id: str,
    binding_set_id: str,
    request_id: str,
) -> Optional[AutomationRoiInputSnapshotRecord]:
    row = conn.execute(
        _SNAPSHOT_SELECT
        + """
        WHERE project_id = %s
          AND consumer_contract = %s
          AND binding_set_id = %s
          AND request_id = %s
        """,
        (project_id, CONSUMER_CONTRACT, binding_set_id, request_id),
    ).fetchone()
    return None if row is None else _snapshot_from_row(conn, row)


def ensure_retry_matches(
    existing: AutomationRoiInputSnapshotRecord,
    command: AutomationRoiInputSnapshotCreate,
) -> AutomationRoiInputSnapshotRecord:
    actual_ids = tuple(
        sorted(binding.binding_record_id for binding in existing.bindings)
    )
    expected_ids = tuple(sorted(command.binding_record_ids))
    if (
        existing.project_id != command.project_id
        or existing.binding_set_id != command.binding_set_id
        or existing.request_id != command.request_id
        or existing.freshness_as_of != command.freshness_as_of
        or existing.evaluated_by != command.evaluated_by
        or actual_ids != expected_ids
    ):
        raise AutomationRoiSnapshotRequestConflict(
            "request_id already identifies a different immutable Automation ROI snapshot"
        )
    return existing


def insert_snapshot(
    conn, command: AutomationRoiInputSnapshotCreate
) -> AutomationRoiInputSnapshotRecord:
    conn.execute("SAVEPOINT research_evidence_automation_roi_snapshot_insert")
    try:
        created = conn.execute(
            """
            SELECT research_evidence_automation_roi.
                research_evidence_create_automation_roi_snapshot(
                %s::uuid, %s, %s::uuid[], %s, %s::timestamptz, %s
            )::text
            """,
            (
                command.project_id,
                command.binding_set_id,
                list(command.binding_record_ids),
                command.request_id,
                command.freshness_as_of,
                command.evaluated_by,
            ),
        ).fetchone()
        row = conn.execute(
            _SNAPSHOT_SELECT + " WHERE id = %s AND project_id = %s",
            (created[0], command.project_id),
        ).fetchone()
        if row is None:
            raise AutomationRoiSnapshotIntegrityError(
                "database write did not return an Automation ROI snapshot"
            )
    except Exception as exc:
        conn.execute(
            "ROLLBACK TO SAVEPOINT research_evidence_automation_roi_snapshot_insert"
        )
        conn.execute(
            "RELEASE SAVEPOINT research_evidence_automation_roi_snapshot_insert"
        )
        if _constraint_name(exc) == "uq_rearois_scope_request":
            existing = _get_snapshot_by_request_id(
                conn,
                project_id=command.project_id,
                binding_set_id=command.binding_set_id,
                request_id=command.request_id,
            )
            if existing is not None:
                return ensure_retry_matches(existing, command)
        if _sqlstate(exc).startswith("23"):
            raise AutomationRoiSnapshotIntegrityError(
                "Automation ROI snapshot violates the immutable database contract"
            ) from exc
        raise
    else:
        conn.execute(
            "RELEASE SAVEPOINT research_evidence_automation_roi_snapshot_insert"
        )
    return _snapshot_from_row(conn, row)


_SNAPSHOT_SELECT = """
SELECT
    id::text, project_id::text, consumer_contract, consumer_contract_version,
    binding_set_id, snapshot_sequence, request_id, policy_identifier,
    policy_version, policy_parameters_json, policy_fingerprint,
    evaluator_version, freshness_as_of, completeness_status,
    policy_evaluation_status, evaluation_reasons_json, evaluated_by,
    supersedes_snapshot_id::text, evaluated_at
FROM research_evidence_automation_roi.
    research_evidence_automation_roi_input_snapshot
"""


def _snapshot_from_row(conn, row) -> AutomationRoiInputSnapshotRecord:
    child_rows = conn.execute(
        """
        SELECT id::text, snapshot_id::text, project_id::text,
               consumer_contract, binding_set_id, input_role,
               binding_record_id::text, linked_at
        FROM research_evidence_automation_roi.
            research_evidence_automation_roi_input_snapshot_binding
        WHERE snapshot_id = %s AND project_id = %s
        ORDER BY input_role
        """,
        (row[0], row[1]),
    ).fetchall()
    parameters = _json_from_db(row[9])
    reasons = _json_from_db(row[15])
    return AutomationRoiInputSnapshotRecord(
        id=row[0],
        project_id=row[1],
        consumer_contract=row[2],
        consumer_contract_version=row[3],
        binding_set_id=row[4],
        snapshot_sequence=row[5],
        request_id=row[6],
        policy_identifier=row[7],
        policy_version=row[8],
        policy_parameters_json=parameters,
        policy_fingerprint=row[10],
        evaluator_version=row[11],
        freshness_as_of=row[12],
        completeness_status=row[13],
        policy_evaluation_status=row[14],
        evaluation_reasons=tuple(reasons),
        evaluated_by=row[16],
        supersedes_snapshot_id=row[17],
        evaluated_at=row[18],
        bindings=tuple(
            AutomationRoiInputSnapshotBindingRecord(
                id=child[0],
                snapshot_id=child[1],
                project_id=child[2],
                consumer_contract=child[3],
                binding_set_id=child[4],
                input_role=child[5],
                binding_record_id=child[6],
                linked_at=child[7],
            )
            for child in child_rows
        ),
    )


def _json_from_db(value):
    return json.loads(value) if isinstance(value, str) else value


def _sqlstate(exc: Exception) -> str:
    return str(getattr(exc, "sqlstate", "") or "")


def _constraint_name(exc: Exception) -> str:
    diag = getattr(exc, "diag", None)
    return str(getattr(diag, "constraint_name", "") or "")
