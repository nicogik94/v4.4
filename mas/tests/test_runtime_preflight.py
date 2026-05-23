"""Regression tests for operator-local runtime preflight diagnostics."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api  # noqa: E402
from config import APP_VERSION, UPLOAD_LAYER  # noqa: E402
from knowledge.files import UploadStoreHealth  # noqa: E402
from runtime import preflight  # noqa: E402
from runtime import run_state as workflow_run_state  # noqa: E402
from runtime import work_queue as workflow_queue  # noqa: E402


class _FakeAcquire:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, query: str):
        self.query = query
        return "SELECT 1"

    async def fetchval(self, query: str, *args):
        self.query = query
        return 0

    async def fetch(self, query: str, *args):
        self.query = query
        return []


class _FakePool:
    def acquire(self):
        return _FakeAcquire()


class _SchemaAwareAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _SchemaAwarePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _SchemaAwareAcquire(self.conn)


class _SchemaAwareConn:
    def __init__(self, *, fail_schema: bool = False, job_counts: dict[str, int] | None = None):
        self.fail_schema = fail_schema
        self.job_counts = job_counts or {}
        self.table_ready = False
        self.job_table_ready = False
        self.schema_statements = []
        self.count_queries = 0

    async def execute(self, query: str):
        normalized = " ".join(query.split()).lower()
        if "select 1" in normalized:
            return "SELECT 1"
        if self.fail_schema and ("workflow_runs" in normalized or "workflow_jobs" in normalized):
            raise RuntimeError('relation "workflow_runtime" does not exist password=secret /app/private/path')
        if "create table if not exists workflow_runs" in normalized:
            self.table_ready = True
        if "create table if not exists workflow_jobs" in normalized:
            self.job_table_ready = True
        if ("workflow_runs" in normalized or "workflow_jobs" in normalized) and (
            "create table" in normalized
            or "alter table" in normalized
            or "create unique index" in normalized
            or "create index" in normalized
        ):
            self.schema_statements.append(normalized)
        return "OK"

    async def fetchval(self, query: str, *args):
        normalized = " ".join(query.split()).lower()
        if "from workflow_runs" in normalized:
            self.count_queries += 1
            if not self.table_ready:
                raise RuntimeError('relation "workflow_runs" does not exist password=secret')
        return 0

    async def fetch(self, query: str, *args):
        normalized = " ".join(query.split()).lower()
        if self.fail_schema and ("workflow_runs" in normalized or "workflow_jobs" in normalized):
            raise RuntimeError('relation "workflow_runtime" does not exist password=secret /app/private/path')
        if "from workflow_jobs" in normalized:
            self.count_queries += 1
            if not self.job_table_ready:
                raise RuntimeError('relation "workflow_jobs" does not exist password=secret')
            return [{"status": status, "count": count} for status, count in self.job_counts.items()]
        return []


def _env(mapping: dict[str, str]):
    return lambda name, default="": mapping.get(name, default)


def _upload_ok():
    return patch(
        "runtime.preflight.check_upload_store_writable",
        return_value=UploadStoreHealth(
            status="ok",
            path=r"C:\redacted\upload_store",
            writable=True,
            message="Upload storage root is writable.",
        ),
    )


class TestRuntimePreflight(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        workflow_run_state._schema_ready_for_pool.clear()
        workflow_queue.clear_schema_cache()
        workflow_run_state.clear_memory_run_state()

    async def asyncTearDown(self):
        workflow_run_state._schema_ready_for_pool.clear()
        workflow_queue.clear_schema_cache()
        workflow_run_state.clear_memory_run_state()

    async def test_upload_store_writable_success_is_reported(self):
        with tempfile.TemporaryDirectory() as tempdir, patch.object(UPLOAD_LAYER, "storage_dir", tempdir):
            result = await preflight.build_runtime_preflight(running_project_ids=[])

        self.assertEqual(result["checks"]["upload_store"]["status"], "ok")
        self.assertTrue(result["checks"]["upload_store"]["writable"])
        self.assertEqual(result["checks"]["upload_store"]["path"], "[operator-local path redacted]")

    async def test_upload_store_failure_is_sanitized(self):
        raw_path = r"C:\private\upload_store\project"
        with patch(
            "runtime.preflight.check_upload_store_writable",
            return_value=UploadStoreHealth(
                status="fail",
                path=raw_path,
                writable=False,
                message="Upload storage is not writable; see server logs for path details.",
            ),
        ):
            result = await preflight.build_runtime_preflight(running_project_ids=[])

        serialized = json.dumps(result)
        self.assertEqual(result["checks"]["upload_store"]["status"], "fail")
        self.assertNotIn(raw_path, serialized)
        self.assertNotIn("Traceback", serialized)

    async def test_database_configured_success_is_checked(self):
        with _upload_ok(), patch("runtime.preflight.os.getenv", side_effect=_env({"DATABASE_URL": "postgres://user:secret@db/app"})):
            with patch("runtime.preflight.store._get_pool", new=AsyncMock(return_value=_FakePool())):
                result = await preflight.build_runtime_preflight(running_project_ids=[])

        serialized = json.dumps(result)
        self.assertEqual(result["checks"]["database"]["status"], "ok")
        self.assertNotIn("secret", serialized)
        self.assertNotIn("postgres://", serialized)

    async def test_database_unconfigured_reports_degraded_memory_fallback(self):
        with _upload_ok(), patch("runtime.preflight.os.getenv", side_effect=_env({})):
            result = await preflight.build_runtime_preflight(running_project_ids=[])

        self.assertEqual(result["checks"]["database"]["status"], "degraded")
        self.assertFalse(result["checks"]["database"]["configured"])

    async def test_database_configured_unavailable_reports_fail(self):
        with _upload_ok(), patch("runtime.preflight.os.getenv", side_effect=_env({"DATABASE_URL": "postgres://user:secret@db/app"})):
            with patch("runtime.preflight.store._get_pool", new=AsyncMock(return_value=None)):
                result = await preflight.build_runtime_preflight(running_project_ids=[])

        serialized = json.dumps(result)
        self.assertEqual(result["checks"]["database"]["status"], "fail")
        self.assertNotIn("secret", serialized)

    async def test_redis_unconfigured_and_configured_failure_are_sanitized(self):
        with _upload_ok(), patch("runtime.preflight.os.getenv", side_effect=_env({})):
            unconfigured = await preflight.build_runtime_preflight(running_project_ids=[])

        self.assertEqual(unconfigured["checks"]["redis"]["status"], "not_configured")

        with _upload_ok(), patch("runtime.preflight.os.getenv", side_effect=_env({"REDIS_URL": "redis://:secret@redis:6379/0"})):
            with patch("runtime.preflight._ping_redis", new=AsyncMock(side_effect=OSError("secret connection failed"))):
                failed = await preflight.build_runtime_preflight(running_project_ids=[])

        serialized = json.dumps(failed)
        self.assertEqual(failed["checks"]["redis"]["status"], "degraded")
        self.assertNotIn("secret", serialized)
        self.assertNotIn("redis://", serialized)

    async def test_jobs_report_process_local_running_state(self):
        with _upload_ok():
            result = await preflight.build_runtime_preflight(running_project_ids=["project-a", "project-b"])

        jobs = result["checks"]["jobs"]
        self.assertEqual(jobs["status"], "degraded")
        self.assertTrue(jobs["process_local"])
        self.assertEqual(jobs["execution_mode"], "durable_queue_api_process_drain")
        self.assertEqual(jobs["running_count"], 2)

    async def test_run_state_posture_reports_durable_guard_when_postgres_available(self):
        with _upload_ok(), patch("runtime.preflight.os.getenv", side_effect=_env({"DATABASE_URL": "postgres://user:secret@db/app"})):
            with patch("runtime.preflight.store._get_pool", new=AsyncMock(return_value=_FakePool())):
                result = await preflight.build_runtime_preflight(running_project_ids=[])

        run_state = result["checks"]["run_state"]
        serialized = json.dumps(result)
        self.assertEqual(run_state["status"], "ok")
        self.assertTrue(run_state["durable_run_state_active"])
        self.assertEqual(run_state["workflow_run_tracking"], "durable_postgres")
        self.assertTrue(run_state["cross_process_run_guard_enabled"])
        self.assertTrue(run_state["stale_recovery_available"])
        self.assertEqual(run_state["last_recovery_check_status"], "ok")
        self.assertEqual(run_state["stale_active_run_count"], 0)
        self.assertEqual(run_state["stale_after_seconds"], 3600)
        queue = result["checks"]["workflow_queue"]
        self.assertEqual(queue["status"], "ok")
        self.assertTrue(queue["durable_queue_active"])
        self.assertTrue(queue["worker_callable"])
        self.assertEqual(queue["queued_job_count"], 0)
        self.assertEqual(queue["running_job_count"], 0)
        self.assertEqual(queue["failed_job_count"], 0)
        self.assertEqual(queue["retry_policy"]["default_max_attempts"], 1)
        self.assertFalse(queue["retry_policy"]["automatic_retries"])
        self.assertTrue(queue["api_process_background_draining"])
        self.assertNotIn("secret", serialized)

    async def test_existing_database_without_workflow_runs_is_ensured_for_preflight(self):
        conn = _SchemaAwareConn()
        pool = _SchemaAwarePool(conn)

        with _upload_ok(), patch("runtime.preflight.os.getenv", side_effect=_env({"DATABASE_URL": "postgres://user:secret@db/app"})):
            with patch("runtime.preflight.store._get_pool", new=AsyncMock(return_value=pool)):
                result = await preflight.build_runtime_preflight(running_project_ids=[])

        run_state = result["checks"]["run_state"]
        self.assertEqual(run_state["status"], "ok")
        self.assertTrue(run_state["durable_run_state_active"])
        self.assertTrue(conn.table_ready)
        self.assertTrue(conn.job_table_ready)
        self.assertTrue(any("create table if not exists workflow_runs" in sql for sql in conn.schema_statements))
        self.assertTrue(any("alter table workflow_runs add column if not exists heartbeat_at" in sql for sql in conn.schema_statements))
        self.assertTrue(any("create table if not exists workflow_jobs" in sql for sql in conn.schema_statements))
        self.assertTrue(any("create unique index if not exists idx_workflow_jobs_active_run" in sql for sql in conn.schema_statements))
        self.assertGreaterEqual(conn.count_queries, 2)

    async def test_preflight_reports_queued_workflow_job_count(self):
        conn = _SchemaAwareConn(job_counts={"queued": 1, "running": 0, "failed": 0})
        pool = _SchemaAwarePool(conn)

        with _upload_ok(), patch("runtime.preflight.os.getenv", side_effect=_env({"DATABASE_URL": "postgres://user:secret@db/app"})):
            with patch("runtime.preflight.store._get_pool", new=AsyncMock(return_value=pool)):
                result = await preflight.build_runtime_preflight(running_project_ids=[])

        queue = result["checks"]["workflow_queue"]
        self.assertEqual(queue["status"], "ok")
        self.assertEqual(queue["queued_job_count"], 1)
        self.assertEqual(queue["running_job_count"], 0)
        self.assertEqual(queue["failed_job_count"], 0)

    async def test_run_state_schema_ensure_is_idempotent_for_same_pool(self):
        conn = _SchemaAwareConn()
        pool = _SchemaAwarePool(conn)

        with _upload_ok(), patch("runtime.preflight.os.getenv", side_effect=_env({"DATABASE_URL": "postgres://user:secret@db/app"})):
            with patch("runtime.preflight.store._get_pool", new=AsyncMock(return_value=pool)):
                first = await preflight.build_runtime_preflight(running_project_ids=[])
                first_schema_count = len(conn.schema_statements)
                second = await preflight.build_runtime_preflight(running_project_ids=[])

        self.assertEqual(first["checks"]["run_state"]["status"], "ok")
        self.assertEqual(second["checks"]["run_state"]["status"], "ok")
        self.assertGreaterEqual(first_schema_count, 5)
        self.assertEqual(len(conn.schema_statements), first_schema_count)
        self.assertGreaterEqual(conn.count_queries, 4)

    async def test_preflight_does_not_report_durable_run_state_active_when_schema_ensure_fails(self):
        conn = _SchemaAwareConn(fail_schema=True)
        pool = _SchemaAwarePool(conn)

        with _upload_ok(), patch("runtime.preflight.os.getenv", side_effect=_env({"DATABASE_URL": "postgres://user:secret@db/app"})):
            with patch("runtime.preflight.store._get_pool", new=AsyncMock(return_value=pool)):
                result = await preflight.build_runtime_preflight(running_project_ids=[])

        run_state = result["checks"]["run_state"]
        serialized = json.dumps(result)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(run_state["status"], "fail")
        self.assertFalse(run_state["durable_run_state_active"])
        self.assertFalse(run_state["cross_process_run_guard_enabled"])
        self.assertFalse(run_state["stale_recovery_available"])
        self.assertEqual(run_state["last_recovery_check_status"], "fail")
        queue = result["checks"]["workflow_queue"]
        self.assertEqual(queue["status"], "fail")
        self.assertFalse(queue["durable_queue_active"])
        self.assertNotIn("secret", serialized)
        self.assertNotIn("/app/private/path", serialized)
        self.assertNotIn('relation "workflow_runs"', serialized)
        self.assertNotIn('relation "workflow_jobs"', serialized)

    def test_workflow_run_stale_timeout_parsing_is_defensively_clamped(self):
        cases = {
            None: 3600,
            "": 3600,
            "not-a-number": 3600,
            "0": 3600,
            "-5": 3600,
            "1": 300,
            "299": 300,
            "300": 300,
            "900": 900,
            1200: 1200,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(workflow_run_state._normalize_stale_after_seconds(raw), expected)

    async def test_run_state_posture_reports_process_local_fallback_without_postgres(self):
        with _upload_ok(), patch("runtime.preflight.os.getenv", side_effect=_env({})):
            with patch("runtime.preflight.store._get_pool", new=AsyncMock(return_value=None)):
                result = await preflight.build_runtime_preflight(running_project_ids=[])

        run_state = result["checks"]["run_state"]
        self.assertEqual(run_state["status"], "degraded")
        self.assertFalse(run_state["durable_run_state_active"])
        self.assertEqual(run_state["workflow_run_tracking"], "process_local")
        self.assertFalse(run_state["cross_process_run_guard_enabled"])
        self.assertFalse(run_state["stale_recovery_available"])
        self.assertEqual(run_state["last_recovery_check_status"], "degraded")
        queue = result["checks"]["workflow_queue"]
        self.assertEqual(queue["status"], "degraded")
        self.assertFalse(queue["durable_queue_active"])
        self.assertTrue(queue["api_process_background_draining"])

    async def test_api_preflight_route_returns_operator_diagnostic(self):
        with patch("api.build_runtime_preflight", new=AsyncMock(return_value={"status": "ok", "version": APP_VERSION, "operator_only": True, "checks": {}})):
            result = await api.runtime_preflight()

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["operator_only"])

    async def test_health_remains_lightweight_and_backward_compatible(self):
        with patch("api.store._get_pool", new=AsyncMock(return_value=object())):
            with patch("api.observability.enabled", return_value=False):
                result = await api.health()

        self.assertEqual(result, {"status": "ok", "version": APP_VERSION, "persistence": "postgres", "tracing": "off"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
