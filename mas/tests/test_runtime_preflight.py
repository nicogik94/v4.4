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


class _FakeAcquire:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, query: str):
        self.query = query
        return "SELECT 1"


class _FakePool:
    def acquire(self):
        return _FakeAcquire()


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
        self.assertEqual(jobs["running_count"], 2)

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
