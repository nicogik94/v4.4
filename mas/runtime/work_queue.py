"""Durable Postgres-backed workflow worker queue."""
from __future__ import annotations

from dataclasses import dataclass
import logging
import uuid
from typing import Any

from runtime import run_state
import store

logger = logging.getLogger(__name__)

ACTIVE_JOB_STATUSES = ("queued", "running")
TERMINAL_JOB_STATUSES = ("succeeded", "failed")
DEFAULT_MAX_WORKFLOW_JOB_ATTEMPTS = 1
QUEUE_ENQUEUE_ERROR_SUMMARY = (
    "Workflow run could not be queued because durable workflow queue storage was unavailable."
)

_schema_ready_for_pool: set[int] = set()


@dataclass(frozen=True)
class WorkflowJobRecord:
    job_id: str
    run_id: str
    project_id: str
    status: str
    attempt_count: int
    max_attempts: int
    created_at: str
    available_at: str
    started_at: str | None
    finished_at: str | None
    error_summary: str


@dataclass(frozen=True)
class WorkflowJobEnqueue:
    created: bool
    job: WorkflowJobRecord
    durable: bool


class WorkflowQueueError(RuntimeError):
    public_message = "Workflow queue is unavailable. Check server-side runtime storage."


async def enqueue_workflow_job(
    run_id: str,
    project_id: str,
    *,
    max_attempts: int | None = None,
) -> WorkflowJobEnqueue:
    """Create a queued workflow job or return the existing active job for this run."""
    attempts = _normalize_max_attempts(max_attempts)
    pool = await _get_ready_pool()
    if pool is None:
        raise WorkflowQueueError("Durable workflow queue requires Postgres.")

    job_id = str(uuid.uuid4())
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO workflow_jobs (
                job_id, run_id, project_id, status, attempt_count, max_attempts, available_at
            )
            VALUES ($1::uuid, $2::uuid, $3, 'queued', 0, $4, NOW())
            ON CONFLICT (run_id) WHERE status IN ('queued', 'running')
            DO NOTHING
            RETURNING job_id, run_id, project_id, status, attempt_count, max_attempts,
                      created_at, available_at, started_at, finished_at, error_summary
            """,
            job_id,
            run_id,
            project_id,
            attempts,
        )
        if row:
            return WorkflowJobEnqueue(created=True, job=_row_to_record(row), durable=True)
        active = await _fetch_active_run_job(conn, run_id)
        if active:
            return WorkflowJobEnqueue(created=False, job=_row_to_record(active), durable=True)
    logger.warning("workflow_jobs active index rejected insert but no active job was found for %s", run_id)
    raise WorkflowQueueError("Unable to enqueue workflow job.")


async def claim_next_workflow_job() -> WorkflowJobRecord | None:
    """Atomically claim the next available queued workflow job."""
    pool = await _get_ready_pool()
    if pool is None:
        return None

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            WITH next_job AS (
                SELECT j.job_id
                FROM workflow_jobs j
                JOIN workflow_runs wr ON wr.run_id = j.run_id
                WHERE j.status = 'queued'
                  AND j.available_at <= NOW()
                  AND j.attempt_count < j.max_attempts
                  AND wr.status IN ('queued', 'running')
                ORDER BY j.available_at ASC, j.created_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            UPDATE workflow_jobs j
            SET status = 'running',
                started_at = NOW(),
                attempt_count = j.attempt_count + 1,
                error_summary = ''
            FROM next_job
            WHERE j.job_id = next_job.job_id
            RETURNING j.job_id, j.run_id, j.project_id, j.status, j.attempt_count, j.max_attempts,
                      j.created_at, j.available_at, j.started_at, j.finished_at, j.error_summary
            """
        )
    return _row_to_record(row) if row else None


async def get_workflow_job(job_id: str) -> WorkflowJobRecord | None:
    pool = await _get_ready_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT job_id, run_id, project_id, status, attempt_count, max_attempts,
                   created_at, available_at, started_at, finished_at, error_summary
            FROM workflow_jobs
            WHERE job_id = $1::uuid
            """,
            job_id,
        )
    return _row_to_record(row) if row else None


async def mark_job_succeeded(job_id: str) -> WorkflowJobRecord | None:
    return await _update_job(job_id, status="succeeded", error_summary="", set_finished=True)


async def mark_job_failed(job_id: str, *, error: BaseException | str) -> WorkflowJobRecord | None:
    return await _update_job(
        job_id,
        status="failed",
        error_summary=run_state.sanitize_error_summary(error),
        set_finished=True,
    )


async def get_queue_posture() -> dict[str, Any]:
    try:
        pool = await _get_ready_pool()
    except WorkflowQueueError:
        logger.warning("Workflow queue readiness check failed", exc_info=True)
        return {
            "status": "fail",
            "durable_queue_active": False,
            "worker_callable": True,
            "queued_job_count": 0,
            "running_job_count": 0,
            "failed_job_count": 0,
            "retry_policy": _retry_policy_summary(),
            "api_process_background_draining": True,
            "message": "Workflow queue table is unavailable; see server logs.",
        }
    if pool is None:
        return {
            "status": "degraded",
            "durable_queue_active": False,
            "worker_callable": True,
            "queued_job_count": 0,
            "running_job_count": 0,
            "failed_job_count": 0,
            "retry_policy": _retry_policy_summary(),
            "api_process_background_draining": True,
            "message": "Workflow queue is inactive because Postgres is unavailable.",
        }

    try:
        counts = await count_workflow_jobs()
    except WorkflowQueueError:
        logger.warning("Unable to count workflow queue jobs", exc_info=True)
        return {
            "status": "fail",
            "durable_queue_active": False,
            "worker_callable": True,
            "queued_job_count": 0,
            "running_job_count": 0,
            "failed_job_count": 0,
            "retry_policy": _retry_policy_summary(),
            "api_process_background_draining": True,
            "message": "Workflow queue counts are unavailable; see server logs.",
        }

    return {
        "status": "ok",
        "durable_queue_active": True,
        "worker_callable": True,
        "queued_job_count": counts.get("queued", 0),
        "running_job_count": counts.get("running", 0),
        "failed_job_count": counts.get("failed", 0),
        "retry_policy": _retry_policy_summary(),
        "api_process_background_draining": True,
        "message": "Workflow jobs are persisted in Postgres and drained by the API process.",
    }


async def count_workflow_jobs() -> dict[str, int]:
    pool = await _get_ready_pool()
    if pool is None:
        return {status: 0 for status in ("queued", "running", "succeeded", "failed")}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT status, COUNT(*) AS count
            FROM workflow_jobs
            GROUP BY status
            """
        )
    counts = {status: 0 for status in ("queued", "running", "succeeded", "failed")}
    for row in rows or []:
        counts[str(_row_get(row, "status"))] = int(_row_get(row, "count") or 0)
    return counts


def clear_schema_cache() -> None:
    _schema_ready_for_pool.clear()


def _retry_policy_summary() -> dict[str, Any]:
    return {
        "default_max_attempts": DEFAULT_MAX_WORKFLOW_JOB_ATTEMPTS,
        "automatic_retries": False,
        "message": "Retry metadata is recorded; automatic workflow retries are disabled in this tranche.",
    }


async def _get_ready_pool():
    try:
        pool = await store._get_pool()
        if pool is None:
            return None
        await _ensure_schema(pool)
        return pool
    except Exception as exc:
        raise WorkflowQueueError("Workflow queue readiness check failed.") from exc


async def _ensure_schema(pool) -> None:
    pool_key = id(pool)
    if pool_key in _schema_ready_for_pool:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_jobs (
                job_id UUID PRIMARY KEY,
                run_id UUID NOT NULL,
                project_id TEXT NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'queued',
                attempt_count INT NOT NULL DEFAULT 0,
                max_attempts INT NOT NULL DEFAULT 1,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                started_at TIMESTAMPTZ,
                finished_at TIMESTAMPTZ,
                error_summary TEXT NOT NULL DEFAULT ''
            )
            """
        )
        await conn.execute("ALTER TABLE workflow_jobs ADD COLUMN IF NOT EXISTS job_id UUID")
        await conn.execute("ALTER TABLE workflow_jobs ADD COLUMN IF NOT EXISTS run_id UUID")
        await conn.execute("ALTER TABLE workflow_jobs ADD COLUMN IF NOT EXISTS project_id TEXT")
        await conn.execute("ALTER TABLE workflow_jobs ADD COLUMN IF NOT EXISTS status VARCHAR(20)")
        await conn.execute("ALTER TABLE workflow_jobs ADD COLUMN IF NOT EXISTS attempt_count INT")
        await conn.execute("ALTER TABLE workflow_jobs ADD COLUMN IF NOT EXISTS max_attempts INT")
        await conn.execute("ALTER TABLE workflow_jobs ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ")
        await conn.execute("ALTER TABLE workflow_jobs ADD COLUMN IF NOT EXISTS available_at TIMESTAMPTZ")
        await conn.execute("ALTER TABLE workflow_jobs ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ")
        await conn.execute("ALTER TABLE workflow_jobs ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ")
        await conn.execute("ALTER TABLE workflow_jobs ADD COLUMN IF NOT EXISTS error_summary TEXT")
        await conn.execute(
            """
            UPDATE workflow_jobs
            SET status = COALESCE(status, 'queued'),
                attempt_count = COALESCE(attempt_count, 0),
                max_attempts = CASE
                    WHEN max_attempts IS NULL OR max_attempts < 1 THEN 1
                    ELSE max_attempts
                END,
                created_at = COALESCE(created_at, NOW()),
                available_at = COALESCE(available_at, created_at, NOW()),
                error_summary = COALESCE(error_summary, '')
            WHERE status IS NULL
               OR attempt_count IS NULL
               OR max_attempts IS NULL
               OR max_attempts < 1
               OR created_at IS NULL
               OR available_at IS NULL
               OR error_summary IS NULL
            """
        )
        await conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_jobs_active_run
            ON workflow_jobs(run_id)
            WHERE status IN ('queued', 'running')
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_workflow_jobs_available
            ON workflow_jobs(status, available_at, created_at)
            WHERE status = 'queued'
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_workflow_jobs_project_created
            ON workflow_jobs(project_id, created_at DESC)
            """
        )
    _schema_ready_for_pool.add(pool_key)


async def _fetch_active_run_job(conn, run_id: str):
    return await conn.fetchrow(
        """
        SELECT job_id, run_id, project_id, status, attempt_count, max_attempts,
               created_at, available_at, started_at, finished_at, error_summary
        FROM workflow_jobs
        WHERE run_id = $1::uuid AND status IN ('queued', 'running')
        ORDER BY created_at DESC
        LIMIT 1
        """,
        run_id,
    )


async def _update_job(
    job_id: str,
    *,
    status: str,
    error_summary: str,
    set_finished: bool,
) -> WorkflowJobRecord | None:
    pool = await _get_ready_pool()
    if pool is None:
        return None

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE workflow_jobs
            SET status = $2,
                finished_at = CASE
                    WHEN $3 THEN NOW()
                    ELSE finished_at
                END,
                error_summary = $4
            WHERE job_id = $1::uuid
            RETURNING job_id, run_id, project_id, status, attempt_count, max_attempts,
                      created_at, available_at, started_at, finished_at, error_summary
            """,
            job_id,
            status,
            set_finished,
            error_summary,
        )
    return _row_to_record(row) if row else None


def _normalize_max_attempts(value: int | None) -> int:
    try:
        parsed = int(value) if value is not None else DEFAULT_MAX_WORKFLOW_JOB_ATTEMPTS
    except (TypeError, ValueError):
        return DEFAULT_MAX_WORKFLOW_JOB_ATTEMPTS
    return max(1, parsed)


def _row_to_record(row) -> WorkflowJobRecord:
    return WorkflowJobRecord(
        job_id=str(_row_get(row, "job_id")),
        run_id=str(_row_get(row, "run_id")),
        project_id=str(_row_get(row, "project_id")),
        status=str(_row_get(row, "status")),
        attempt_count=int(_row_get(row, "attempt_count") or 0),
        max_attempts=int(_row_get(row, "max_attempts") or DEFAULT_MAX_WORKFLOW_JOB_ATTEMPTS),
        created_at=_datetime_to_iso(_row_get(row, "created_at")),
        available_at=_datetime_to_iso(_row_get(row, "available_at")),
        started_at=_datetime_to_iso(_row_get(row, "started_at")) if _row_get(row, "started_at") else None,
        finished_at=_datetime_to_iso(_row_get(row, "finished_at")) if _row_get(row, "finished_at") else None,
        error_summary=str(_row_get(row, "error_summary") or ""),
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
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
