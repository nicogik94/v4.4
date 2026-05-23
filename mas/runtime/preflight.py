"""Operator-local runtime preflight diagnostics for the v5 hardening branch."""
from __future__ import annotations

import os
from typing import Iterable

from config import APP_VERSION
from knowledge.files import check_upload_store_writable
from runtime import run_state
import store


async def build_runtime_preflight(*, running_project_ids: Iterable[str] = ()) -> dict:
    """Return machine-readable runtime diagnostics without exposing secrets."""
    running_ids = tuple(running_project_ids or ())
    checks = {
        "version": _check_version(),
        "upload_store": _check_upload_store(),
        "database": await _check_database(),
        "redis": await _check_redis(),
        "run_state": await run_state.get_run_state_posture(
            local_running_count=len(running_ids),
            local_running_project_ids=running_ids,
        ),
        "jobs": _check_jobs(running_ids),
    }
    return {
        "status": _overall_status(checks),
        "version": APP_VERSION or "unknown",
        "operator_only": True,
        "checks": checks,
    }


def _check_version() -> dict:
    if APP_VERSION:
        return {
            "status": "ok",
            "message": "Application version is configured.",
        }
    return {
        "status": "fail",
        "message": "Application version is unavailable.",
    }


def _check_upload_store() -> dict:
    health = check_upload_store_writable()
    return {
        "status": health.status,
        "path": "[operator-local path redacted]",
        "writable": health.writable,
        "message": health.message,
    }


async def _check_database() -> dict:
    configured = bool(os.getenv("DATABASE_URL", "").strip())
    if not configured:
        return {
            "status": "degraded",
            "configured": False,
            "message": "DATABASE_URL is not configured; state persistence uses process memory fallback.",
        }
    try:
        pool = await store._get_pool()
        if pool is None:
            return {
                "status": "fail",
                "configured": True,
                "message": "Database is configured but unavailable; see server logs.",
            }
        async with pool.acquire() as conn:
            await conn.execute("SELECT 1")
    except Exception:
        return {
            "status": "fail",
            "configured": True,
            "message": "Database connectivity check failed; see server logs.",
        }
    return {
        "status": "ok",
        "configured": True,
        "message": "Database connectivity check passed.",
    }


async def _check_redis() -> dict:
    configured = bool(os.getenv("REDIS_URL", "").strip())
    if not configured:
        return {
            "status": "not_configured",
            "configured": False,
            "required": False,
            "message": "REDIS_URL is not configured; Redis is not required for the current runtime.",
        }
    try:
        await _ping_redis(os.getenv("REDIS_URL", ""))
    except Exception:
        return {
            "status": "degraded",
            "configured": True,
            "required": False,
            "message": "Redis is configured but the ping failed; durable job locking is not active in this tranche.",
        }
    return {
        "status": "ok",
        "configured": True,
        "required": False,
        "message": "Redis ping succeeded; durable job locking is not active in this tranche.",
    }


async def _ping_redis(redis_url: str) -> None:
    import redis.asyncio as redis

    client = redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)
    try:
        await client.ping()
    finally:
        close = getattr(client, "aclose", None)
        if close is not None:
            await close()
        else:  # pragma: no cover - compatibility for older redis clients
            await client.close()


def _check_jobs(running_project_ids: Iterable[str]) -> dict:
    running_count = len(list(running_project_ids or []))
    status = "degraded" if running_count else "ok"
    return {
        "status": status,
        "process_local": True,
        "execution_mode": "fastapi_background_tasks",
        "running_count": running_count,
        "message": (
            "One or more workflows are tracked by this API process only."
            if running_count
            else "Workflow execution still runs inside this API process; no workflows are currently marked running locally."
        ),
    }


def _overall_status(checks: dict[str, dict]) -> str:
    statuses = {str(check.get("status", "")) for check in checks.values()}
    if "fail" in statuses:
        return "fail"
    if "degraded" in statuses:
        return "degraded"
    return "ok"
