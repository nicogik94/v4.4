"""Durable workflow run state and active-run guards."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import os
import re
import uuid
from typing import Any, Iterable

from config import APP_VERSION
import store

logger = logging.getLogger(__name__)

ACTIVE_RUN_STATUSES = ("queued", "running")
TERMINAL_RUN_STATUSES = ("succeeded", "failed")
DEFAULT_WORKFLOW_RUN_STALE_AFTER_SECONDS = 3600
MIN_WORKFLOW_RUN_STALE_AFTER_SECONDS = 300
ABANDONED_RUN_ERROR_SUMMARY = (
    "Workflow run marked failed because it appeared abandoned after runtime restart or timeout."
)

_schema_ready_for_pool: set[int] = set()
_memory_runs: dict[str, "WorkflowRunRecord"] = {}
_memory_active_by_project: dict[str, str] = {}


@dataclass(frozen=True)
class WorkflowRunRecord:
    run_id: str
    project_id: str
    status: str
    current_phase: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    heartbeat_at: str | None
    error_summary: str
    code_version: str


@dataclass(frozen=True)
class WorkflowRunAcquisition:
    created: bool
    run: WorkflowRunRecord
    durable: bool


@dataclass(frozen=True)
class WorkflowRunRecoveryResult:
    status: str
    durable: bool
    recovered_count: int
    stale_active_run_count: int
    stale_after_seconds: int
    message: str


class WorkflowRunStateError(RuntimeError):
    public_message = "Workflow run state is unavailable. Check server-side runtime storage."


async def create_workflow_run(project_id: str, *, code_version: str | None = None) -> WorkflowRunAcquisition:
    """Create a queued run or return the existing active run for this project."""
    version = code_version or APP_VERSION or "unknown"
    pool = await _get_ready_pool()
    if pool is None:
        return _create_memory_run(project_id, version)

    async with pool.acquire() as conn:
        for attempt in range(2):
            row = await _insert_workflow_run(conn, project_id, version)
            if row:
                return WorkflowRunAcquisition(created=True, run=_row_to_record(row), durable=True)
            if attempt == 1:
                break
            recovered = await _recover_stale_project_run_with_conn(
                conn,
                project_id,
                workflow_run_stale_after_seconds(),
            )
            if not recovered:
                break
            logger.info("Recovered stale workflow run for project %s while acquiring a new run", project_id)
        active = await _fetch_active_project_run(conn, project_id)
        if active:
            return WorkflowRunAcquisition(created=False, run=_row_to_record(active), durable=True)
    logger.warning("workflow_runs active index rejected insert but no active row was found for %s", project_id)
    raise WorkflowRunStateError("Unable to acquire workflow run state.")


async def has_active_project_run(project_id: str) -> bool:
    return await get_active_project_run(project_id) is not None


async def get_active_project_run(project_id: str) -> WorkflowRunRecord | None:
    pool = await _get_ready_pool()
    if pool is None:
        active_id = _memory_active_by_project.get(project_id)
        if not active_id:
            return None
        record = _memory_runs.get(active_id)
        if record and record.status in ACTIVE_RUN_STATUSES:
            return record
        return None

    async with pool.acquire() as conn:
        row = await _fetch_active_project_run(conn, project_id)
    return _row_to_record(row) if row else None


async def get_workflow_run(run_id: str) -> WorkflowRunRecord | None:
    pool = await _get_ready_pool()
    if pool is None:
        return _memory_runs.get(run_id)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT run_id, project_id, status, current_phase, created_at,
                   started_at, finished_at, heartbeat_at, error_summary, code_version
            FROM workflow_runs
            WHERE run_id = $1::uuid
            """,
            run_id,
        )
    return _row_to_record(row) if row else None


async def mark_run_running(run_id: str, *, current_phase: str = "") -> WorkflowRunRecord | None:
    return await _update_run(
        run_id,
        status="running",
        current_phase=current_phase,
        set_started=True,
        touch_heartbeat=True,
    )


async def mark_run_phase(run_id: str, current_phase: str) -> WorkflowRunRecord | None:
    return await _update_run(run_id, current_phase=current_phase, touch_heartbeat=True)


async def mark_run_succeeded(run_id: str, *, current_phase: str = "") -> WorkflowRunRecord | None:
    return await _update_run(
        run_id,
        status="succeeded",
        current_phase=current_phase,
        error_summary="",
        set_finished=True,
        touch_heartbeat=True,
    )


async def mark_run_failed(
    run_id: str,
    *,
    error: BaseException | str,
    current_phase: str = "",
) -> WorkflowRunRecord | None:
    return await _update_run(
        run_id,
        status="failed",
        current_phase=current_phase,
        error_summary=sanitize_error_summary(error),
        set_finished=True,
        touch_heartbeat=True,
    )


async def count_stale_active_runs(
    *,
    stale_after_seconds: int | None = None,
    exclude_project_ids: Iterable[str] = (),
) -> int:
    threshold = _normalize_stale_after_seconds(stale_after_seconds)
    excluded = _normalize_project_id_list(exclude_project_ids)
    pool = await _get_ready_pool()
    if pool is None:
        return _memory_stale_active_count(threshold, excluded)
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM workflow_runs
            WHERE status IN ('queued', 'running')
              AND NOT (project_id = ANY($2::text[]))
              AND COALESCE(heartbeat_at, started_at, created_at)
                    < NOW() - ($1::int * INTERVAL '1 second')
            """,
            threshold,
            excluded,
        )
    return int(value or 0)


async def recover_stale_active_runs(
    *,
    stale_after_seconds: int | None = None,
    exclude_project_ids: Iterable[str] = (),
    limit: int = 100,
) -> WorkflowRunRecoveryResult:
    threshold = _normalize_stale_after_seconds(stale_after_seconds)
    excluded = _normalize_project_id_list(exclude_project_ids)
    bounded_limit = max(1, min(int(limit or 1), 1000))
    try:
        pool = await _get_ready_pool()
    except WorkflowRunStateError:
        logger.warning("Workflow run-state recovery check failed", exc_info=True)
        return WorkflowRunRecoveryResult(
            status="fail",
            durable=False,
            recovered_count=0,
            stale_active_run_count=0,
            stale_after_seconds=threshold,
            message="Workflow run-state recovery failed; see server logs.",
        )
    if pool is None:
        stale_count = _memory_stale_active_count(threshold, excluded)
        return WorkflowRunRecoveryResult(
            status="degraded",
            durable=False,
            recovered_count=0,
            stale_active_run_count=stale_count,
            stale_after_seconds=threshold,
            message="Stale run recovery requires Postgres durable run state.",
        )

    try:
        async with pool.acquire() as conn:
            recovered_rows = await conn.fetch(
                """
                WITH stale AS (
                    SELECT run_id
                    FROM workflow_runs
                    WHERE status IN ('queued', 'running')
                      AND NOT (project_id = ANY($3::text[]))
                      AND COALESCE(heartbeat_at, started_at, created_at)
                            < NOW() - ($1::int * INTERVAL '1 second')
                    ORDER BY COALESCE(heartbeat_at, started_at, created_at) ASC
                    LIMIT $2::int
                )
                UPDATE workflow_runs wr
                SET status = 'failed',
                    finished_at = NOW(),
                    heartbeat_at = NOW(),
                    error_summary = $4
                FROM stale
                WHERE wr.run_id = stale.run_id
                RETURNING wr.run_id
                """,
                threshold,
                bounded_limit,
                excluded,
                ABANDONED_RUN_ERROR_SUMMARY,
            )
            stale_remaining = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM workflow_runs
                WHERE status IN ('queued', 'running')
                  AND NOT (project_id = ANY($2::text[]))
                  AND COALESCE(heartbeat_at, started_at, created_at)
                        < NOW() - ($1::int * INTERVAL '1 second')
                """,
                threshold,
                excluded,
            )
    except Exception:
        logger.warning("Workflow run-state recovery failed", exc_info=True)
        return WorkflowRunRecoveryResult(
            status="fail",
            durable=True,
            recovered_count=0,
            stale_active_run_count=0,
            stale_after_seconds=threshold,
            message="Workflow run-state recovery failed; see server logs.",
        )

    recovered_count = len(recovered_rows or [])
    return WorkflowRunRecoveryResult(
        status="ok",
        durable=True,
        recovered_count=recovered_count,
        stale_active_run_count=int(stale_remaining or 0),
        stale_after_seconds=threshold,
        message=(
            f"Recovered {recovered_count} abandoned workflow run(s)."
            if recovered_count
            else "No abandoned workflow runs required recovery."
        ),
    )


async def recover_stale_project_run(
    project_id: str,
    *,
    stale_after_seconds: int | None = None,
) -> WorkflowRunRecoveryResult:
    threshold = _normalize_stale_after_seconds(stale_after_seconds)
    try:
        pool = await _get_ready_pool()
    except WorkflowRunStateError:
        logger.warning("Project workflow run-state recovery check failed", exc_info=True)
        return WorkflowRunRecoveryResult(
            status="fail",
            durable=False,
            recovered_count=0,
            stale_active_run_count=0,
            stale_after_seconds=threshold,
            message="Workflow run-state recovery failed; see server logs.",
        )
    if pool is None:
        return WorkflowRunRecoveryResult(
            status="degraded",
            durable=False,
            recovered_count=0,
            stale_active_run_count=0,
            stale_after_seconds=threshold,
            message="Project stale run recovery requires Postgres durable run state.",
        )

    try:
        async with pool.acquire() as conn:
            recovered_count = await _recover_stale_project_run_with_conn(conn, project_id, threshold)
    except Exception:
        logger.warning("Project workflow run-state recovery failed", exc_info=True)
        return WorkflowRunRecoveryResult(
            status="fail",
            durable=True,
            recovered_count=0,
            stale_active_run_count=0,
            stale_after_seconds=threshold,
            message="Workflow run-state recovery failed; see server logs.",
        )

    return WorkflowRunRecoveryResult(
        status="ok",
        durable=True,
        recovered_count=recovered_count,
        stale_active_run_count=0,
        stale_after_seconds=threshold,
        message=(
            "Recovered abandoned workflow run for this project."
            if recovered_count
            else "No abandoned workflow run required project recovery."
        ),
    )


async def get_run_state_posture(
    *,
    local_running_count: int = 0,
    local_running_project_ids: Iterable[str] = (),
) -> dict[str, Any]:
    threshold = workflow_run_stale_after_seconds()
    excluded = _normalize_project_id_list(local_running_project_ids)
    recovery = await recover_stale_active_runs(
        stale_after_seconds=threshold,
        exclude_project_ids=excluded,
    )
    try:
        pool = await _get_ready_pool()
    except WorkflowRunStateError:
        logger.warning("Workflow run-state readiness check failed", exc_info=True)
        return {
            "status": "fail",
            "durable_run_state_active": False,
            "workflow_run_tracking": "unknown",
            "cross_process_run_guard_enabled": False,
            "active_run_count": 0,
            "stale_recovery_available": False,
            "stale_active_run_count": recovery.stale_active_run_count,
            "last_recovery_check_status": recovery.status,
            "recovered_count": recovery.recovered_count,
            "stale_after_seconds": threshold,
            "local_running_count": local_running_count,
            "message": "Workflow run-state table is unavailable; see server logs.",
        }
    if pool is None:
        return {
            "status": "degraded",
            "durable_run_state_active": False,
            "workflow_run_tracking": "process_local",
            "cross_process_run_guard_enabled": False,
            "active_run_count": _memory_active_count(),
            "stale_recovery_available": False,
            "stale_active_run_count": recovery.stale_active_run_count,
            "last_recovery_check_status": recovery.status,
            "recovered_count": recovery.recovered_count,
            "stale_after_seconds": threshold,
            "local_running_count": local_running_count,
            "message": "Workflow run state is process-local because Postgres is unavailable.",
        }

    active_count = 0
    try:
        async with pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT COUNT(*) FROM workflow_runs WHERE status IN ('queued', 'running')"
            )
            active_count = int(value or 0)
            stale_value = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM workflow_runs
                WHERE status IN ('queued', 'running')
                  AND NOT (project_id = ANY($2::text[]))
                  AND COALESCE(heartbeat_at, started_at, created_at)
                        < NOW() - ($1::int * INTERVAL '1 second')
                """,
                threshold,
                excluded,
            )
            stale_count = int(stale_value or 0)
    except Exception:
        logger.warning("Unable to count active workflow runs", exc_info=True)
        return {
            "status": "fail",
            "durable_run_state_active": False,
            "workflow_run_tracking": "unknown",
            "cross_process_run_guard_enabled": False,
            "active_run_count": 0,
            "stale_recovery_available": False,
            "stale_active_run_count": 0,
            "last_recovery_check_status": "fail",
            "recovered_count": recovery.recovered_count,
            "stale_after_seconds": threshold,
            "local_running_count": local_running_count,
            "message": "Workflow run-state table is unavailable; see server logs.",
        }

    posture_status = "fail" if recovery.status == "fail" else "ok"
    return {
        "status": posture_status,
        "durable_run_state_active": True,
        "workflow_run_tracking": "durable_postgres",
        "cross_process_run_guard_enabled": True,
        "active_run_count": active_count,
        "stale_recovery_available": recovery.status != "fail",
        "stale_active_run_count": stale_count,
        "last_recovery_check_status": recovery.status,
        "recovered_count": recovery.recovered_count,
        "stale_after_seconds": threshold,
        "local_running_count": local_running_count,
        "message": (
            "Workflow run recovery failed; see server logs."
            if recovery.status == "fail"
            else "Workflow run state is persisted in Postgres with an active-run guard."
        ),
    }


def workflow_run_stale_after_seconds() -> int:
    return _normalize_stale_after_seconds(os.getenv("WORKFLOW_RUN_STALE_AFTER_SECONDS", ""))


def _normalize_stale_after_seconds(value: int | str | None) -> int:
    try:
        parsed = int(value) if value is not None and str(value).strip() else DEFAULT_WORKFLOW_RUN_STALE_AFTER_SECONDS
    except (TypeError, ValueError):
        return DEFAULT_WORKFLOW_RUN_STALE_AFTER_SECONDS
    if parsed <= 0:
        return DEFAULT_WORKFLOW_RUN_STALE_AFTER_SECONDS
    return max(parsed, MIN_WORKFLOW_RUN_STALE_AFTER_SECONDS)


def _normalize_project_id_list(project_ids: Iterable[str]) -> list[str]:
    return [str(project_id) for project_id in (project_ids or []) if str(project_id)]


def sanitize_error_summary(error: BaseException | str, *, max_length: int = 320) -> str:
    text = str(error or "Workflow failed").replace("\r", " ").replace("\n", " ")
    text = re.sub(r"Traceback \(most recent call last\):.*", "Workflow failed", text)
    text = re.sub(r"[A-Za-z]:\\[^\s:;,\"]+", "[local path redacted]", text)
    text = re.sub(r"(?<![A-Za-z0-9_:/])/(?:[^/\s:;,\"]+/)+[^/\s:;,\"]+", "[local path redacted]", text)
    text = re.sub(r"(?i)(api[_-]?key|token|password|secret)=\S+", r"\1=[redacted]", text)
    text = " ".join(text.split())
    if not text:
        text = "Workflow failed"
    if len(text) > max_length:
        text = text[: max_length - 3].rstrip() + "..."
    return text


def clear_memory_run_state() -> None:
    _memory_runs.clear()
    _memory_active_by_project.clear()


async def _get_ready_pool():
    try:
        pool = await store._get_pool()
        if pool is None:
            return None
        await _ensure_schema(pool)
        return pool
    except Exception as exc:
        raise WorkflowRunStateError("Workflow run state readiness check failed.") from exc


async def _ensure_schema(pool) -> None:
    pool_key = id(pool)
    if pool_key in _schema_ready_for_pool:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_runs (
                run_id UUID PRIMARY KEY,
                project_id TEXT NOT NULL,
                status VARCHAR(20) NOT NULL,
                current_phase VARCHAR(50) NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                started_at TIMESTAMPTZ,
                finished_at TIMESTAMPTZ,
                heartbeat_at TIMESTAMPTZ,
                error_summary TEXT NOT NULL DEFAULT '',
                code_version VARCHAR(50) NOT NULL DEFAULT ''
            )
            """
        )
        await conn.execute(
            "ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ"
        )
        await conn.execute(
            """
            UPDATE workflow_runs
            SET heartbeat_at = COALESCE(heartbeat_at, started_at, created_at)
            WHERE heartbeat_at IS NULL
            """
        )
        await conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_runs_active_project
            ON workflow_runs(project_id)
            WHERE status IN ('queued', 'running')
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_workflow_runs_project_created
            ON workflow_runs(project_id, created_at DESC)
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_workflow_runs_active_heartbeat
            ON workflow_runs(status, heartbeat_at)
            WHERE status IN ('queued', 'running')
            """
        )
    _schema_ready_for_pool.add(pool_key)


async def _insert_workflow_run(conn, project_id: str, code_version: str):
    return await conn.fetchrow(
        """
        INSERT INTO workflow_runs (
            run_id, project_id, status, current_phase, code_version, heartbeat_at
        )
        VALUES ($1::uuid, $2, 'queued', '', $3, NOW())
        ON CONFLICT (project_id) WHERE status IN ('queued', 'running')
        DO NOTHING
        RETURNING run_id, project_id, status, current_phase, created_at,
                  started_at, finished_at, heartbeat_at, error_summary, code_version
        """,
        str(uuid.uuid4()),
        project_id,
        code_version,
    )


async def _recover_stale_project_run_with_conn(conn, project_id: str, threshold: int) -> int:
    row = await conn.fetchrow(
        """
        UPDATE workflow_runs
        SET status = 'failed',
            finished_at = NOW(),
            heartbeat_at = NOW(),
            error_summary = $3
        WHERE project_id = $1
          AND status IN ('queued', 'running')
          AND COALESCE(heartbeat_at, started_at, created_at)
                < NOW() - ($2::int * INTERVAL '1 second')
        RETURNING run_id
        """,
        project_id,
        threshold,
        ABANDONED_RUN_ERROR_SUMMARY,
    )
    return 1 if row else 0


async def _fetch_active_project_run(conn, project_id: str):
    return await conn.fetchrow(
        """
        SELECT run_id, project_id, status, current_phase, created_at,
               started_at, finished_at, heartbeat_at, error_summary, code_version
        FROM workflow_runs
        WHERE project_id = $1 AND status IN ('queued', 'running')
        ORDER BY created_at DESC
        LIMIT 1
        """,
        project_id,
    )


async def _update_run(
    run_id: str,
    *,
    status: str | None = None,
    current_phase: str | None = None,
    error_summary: str | None = None,
    set_started: bool = False,
    set_finished: bool = False,
    touch_heartbeat: bool = False,
) -> WorkflowRunRecord | None:
    pool = await _get_ready_pool()
    if pool is None:
        return _update_memory_run(
            run_id,
            status=status,
            current_phase=current_phase,
            error_summary=error_summary,
            set_started=set_started,
            set_finished=set_finished,
            touch_heartbeat=touch_heartbeat,
        )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE workflow_runs
            SET status = COALESCE($2, status),
                current_phase = COALESCE($3, current_phase),
                started_at = CASE
                    WHEN $4 AND started_at IS NULL THEN NOW()
                    ELSE started_at
                END,
                finished_at = CASE
                    WHEN $5 THEN NOW()
                    ELSE finished_at
                END,
                error_summary = COALESCE($6, error_summary),
                heartbeat_at = CASE
                    WHEN $7 THEN NOW()
                    ELSE heartbeat_at
                END
            WHERE run_id = $1::uuid
            RETURNING run_id, project_id, status, current_phase, created_at,
                      started_at, finished_at, heartbeat_at, error_summary, code_version
            """,
            run_id,
            status,
            current_phase,
            set_started,
            set_finished,
            error_summary,
            touch_heartbeat,
        )
    return _row_to_record(row) if row else None


def _create_memory_run(project_id: str, code_version: str) -> WorkflowRunAcquisition:
    active_id = _memory_active_by_project.get(project_id)
    if active_id:
        active = _memory_runs.get(active_id)
        if active and active.status in ACTIVE_RUN_STATUSES:
            return WorkflowRunAcquisition(created=False, run=active, durable=False)

    run = WorkflowRunRecord(
        run_id=str(uuid.uuid4()),
        project_id=project_id,
        status="queued",
        current_phase="",
        created_at=_now_iso(),
        started_at=None,
        finished_at=None,
        heartbeat_at=_now_iso(),
        error_summary="",
        code_version=code_version,
    )
    _memory_runs[run.run_id] = run
    _memory_active_by_project[project_id] = run.run_id
    return WorkflowRunAcquisition(created=True, run=run, durable=False)


def _update_memory_run(
    run_id: str,
    *,
    status: str | None,
    current_phase: str | None,
    error_summary: str | None,
    set_started: bool,
    set_finished: bool,
    touch_heartbeat: bool,
) -> WorkflowRunRecord | None:
    existing = _memory_runs.get(run_id)
    if existing is None:
        return None
    next_status = status or existing.status
    record = WorkflowRunRecord(
        run_id=existing.run_id,
        project_id=existing.project_id,
        status=next_status,
        current_phase=current_phase if current_phase is not None else existing.current_phase,
        created_at=existing.created_at,
        started_at=existing.started_at or (_now_iso() if set_started else None),
        finished_at=_now_iso() if set_finished else existing.finished_at,
        heartbeat_at=_now_iso() if touch_heartbeat else existing.heartbeat_at,
        error_summary=error_summary if error_summary is not None else existing.error_summary,
        code_version=existing.code_version,
    )
    _memory_runs[run_id] = record
    if next_status in TERMINAL_RUN_STATUSES:
        _memory_active_by_project.pop(existing.project_id, None)
    elif next_status in ACTIVE_RUN_STATUSES:
        _memory_active_by_project[existing.project_id] = run_id
    return record


def _memory_active_count() -> int:
    return sum(
        1
        for run_id in _memory_active_by_project.values()
        if (run := _memory_runs.get(run_id)) and run.status in ACTIVE_RUN_STATUSES
    )


def _memory_stale_active_count(stale_after_seconds: int, exclude_project_ids: Iterable[str] = ()) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)
    excluded = set(exclude_project_ids or [])
    count = 0
    for run_id in _memory_active_by_project.values():
        run = _memory_runs.get(run_id)
        if not run or run.status not in ACTIVE_RUN_STATUSES:
            continue
        if run.project_id in excluded:
            continue
        observed = _parse_datetime(run.heartbeat_at or run.started_at or run.created_at)
        if observed and observed < cutoff:
            count += 1
    return count


def _row_to_record(row) -> WorkflowRunRecord:
    return WorkflowRunRecord(
        run_id=str(_row_get(row, "run_id")),
        project_id=str(_row_get(row, "project_id")),
        status=str(_row_get(row, "status")),
        current_phase=str(_row_get(row, "current_phase") or ""),
        created_at=_datetime_to_iso(_row_get(row, "created_at")),
        started_at=_datetime_to_iso(_row_get(row, "started_at")) if _row_get(row, "started_at") else None,
        finished_at=_datetime_to_iso(_row_get(row, "finished_at")) if _row_get(row, "finished_at") else None,
        heartbeat_at=_datetime_to_iso(_row_get(row, "heartbeat_at")) if _row_get(row, "heartbeat_at") else None,
        error_summary=str(_row_get(row, "error_summary") or ""),
        code_version=str(_row_get(row, "code_version") or ""),
    )


def _row_get(row, key: str, default=None):
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        getter = getattr(row, "get", None)
        if getter is not None:
            return getter(key, default)
        return default


def _datetime_to_iso(value) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
