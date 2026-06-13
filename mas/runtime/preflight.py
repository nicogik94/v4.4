"""Operator-local runtime preflight diagnostics for the v5 hardening branch."""
from __future__ import annotations

import os
import ipaddress
from typing import Iterable
from urllib.parse import urlparse

from config import APP_VERSION
from version import get_git_sha
from knowledge.files import check_upload_store_writable
from runtime import run_state
from runtime import work_queue
import store

_HOST_BINDING_ENV_NAMES = ("MAS_API_HOST", "API_HOST", "UVICORN_HOST", "APP_HOST")
_PUBLIC_EXPOSURE_FLAG_ENV_NAMES = ("MAS_PUBLIC_EXPOSURE", "API_PUBLIC_EXPOSURE", "MAS_PUBLIC_DEPLOYMENT")
_PUBLIC_EXPOSURE_MODE_ENV_NAMES = ("MAS_EXPOSURE_MODE", "API_EXPOSURE_MODE", "MAS_DEPLOYMENT_MODE")
_PUBLIC_BASE_URL_ENV_NAMES = ("MAS_PUBLIC_BASE_URL", "API_PUBLIC_BASE_URL")
_TRUTHY_VALUES = {"1", "true", "yes", "on"}
_PUBLIC_MODE_VALUES = {"public", "internet", "external"}
_PUBLIC_HARDENING_MESSAGE = "auth/multi-tenancy/public hardening are not implemented yet"


async def build_runtime_preflight(*, running_project_ids: Iterable[str] = ()) -> dict:
    """Return machine-readable runtime diagnostics without exposing secrets."""
    running_ids = tuple(running_project_ids or ())
    checks = {
        "version": _check_version(),
        "public_exposure": _check_public_exposure(),
        "upload_store": _check_upload_store(),
        "database": await _check_database(),
        "redis": await _check_redis(),
        "run_state": await run_state.get_run_state_posture(
            local_running_count=len(running_ids),
            local_running_project_ids=running_ids,
        ),
        "workflow_queue": await work_queue.get_queue_posture(),
        "jobs": _check_jobs(running_ids),
    }
    return {
        "status": _overall_status(checks),
        "version": APP_VERSION or "unknown",
        "git_sha": get_git_sha(),
        "operator_only": True,
        "checks": checks,
    }


def _check_public_exposure() -> dict:
    host_source, raw_host = _first_configured_env(_HOST_BINDING_ENV_NAMES)
    host_posture = _classify_host_binding(raw_host, source=host_source)
    intent_source = _public_exposure_intent_source()
    explicit_public_intent = bool(intent_source)
    wildcard_cors = True

    reasons: list[str] = []
    if explicit_public_intent:
        reasons.append("explicit public exposure intent is configured")
    if host_posture["public_host_intent"]:
        reasons.append("host binding points at a public interface")
    if wildcard_cors and (explicit_public_intent or host_posture["public_host_intent"]):
        reasons.append("wildcard CORS is active with public exposure intent")

    if explicit_public_intent or host_posture["public_host_intent"]:
        status = "fail"
        message = (
            f"Public exposure is blocked because {', '.join(reasons)}; "
            f"{_PUBLIC_HARDENING_MESSAGE}."
        )
    elif host_posture["non_local_bind"]:
        status = "degraded"
        message = (
            "Non-local API binding is configured; keep this API operator-local until "
            f"{_PUBLIC_HARDENING_MESSAGE}."
        )
    else:
        status = "ok"
        if host_posture["wildcard_bind"]:
            message = (
                "Wildcard container binding is allowed for local Docker by itself and is not treated as "
                f"public exposure; {_PUBLIC_HARDENING_MESSAGE}."
            )
        else:
            message = (
                "Local/operator-only exposure posture detected; "
                f"{_PUBLIC_HARDENING_MESSAGE}."
            )

    return {
        "status": status,
        "operator_only": True,
        "auth_implemented": False,
        "multi_tenancy_implemented": False,
        "public_hardening_implemented": False,
        "explicit_public_exposure_intent": explicit_public_intent,
        "public_exposure_intent": bool(explicit_public_intent or host_posture["public_host_intent"]),
        "public_exposure_intent_source": intent_source or "",
        "host_binding": host_posture,
        "cors": {
            "allow_origins_source": "api_default",
            "wildcard": wildcard_cors,
            "origin_count": 1,
        },
        "message": message,
    }


def _first_configured_env(names: Iterable[str]) -> tuple[str, str]:
    for name in names:
        value = os.getenv(name, "")
        if value.strip():
            return name, value
    return "", ""


def _public_exposure_intent_source() -> str:
    for name in _PUBLIC_EXPOSURE_FLAG_ENV_NAMES:
        if _env_truthy(os.getenv(name, "")):
            return name
    for name in _PUBLIC_EXPOSURE_MODE_ENV_NAMES:
        if os.getenv(name, "").strip().lower() in _PUBLIC_MODE_VALUES:
            return name
    for name in _PUBLIC_BASE_URL_ENV_NAMES:
        value = os.getenv(name, "")
        if not value.strip():
            continue
        host_posture = _classify_host_binding(value, source=name)
        if host_posture["public_host_intent"]:
            return name
    return ""


def _env_truthy(value: str) -> bool:
    return value.strip().lower() in _TRUTHY_VALUES


def _classify_host_binding(raw_value: str, *, source: str = "") -> dict:
    host = _normalize_host_value(raw_value)
    if not host:
        return {
            "configured": False,
            "source": source or "default",
            "classification": "local_default",
            "reported_value": "localhost",
            "local_bind": True,
            "wildcard_bind": False,
            "non_local_bind": False,
            "public_host_intent": False,
        }

    ip_address = _parse_ip_address(host)
    if ip_address is not None:
        if ip_address.is_loopback:
            classification = "local"
        elif ip_address.is_unspecified:
            classification = "wildcard_container"
        elif ip_address.is_private or ip_address.is_link_local:
            classification = "private_network"
        elif ip_address.is_global:
            classification = "public"
        else:
            classification = "non_local"
    elif host == "localhost" or host.endswith(".localhost"):
        classification = "local"
    elif host.endswith(".local"):
        classification = "private_network"
    elif host in {"*", "all"}:
        classification = "wildcard_container"
    else:
        classification = "public"

    wildcard = classification == "wildcard_container"
    local = classification == "local"
    public = classification == "public"
    non_local = not local and not wildcard
    return {
        "configured": True,
        "source": source,
        "classification": classification,
        "reported_value": _safe_reported_host_value(host, classification),
        "local_bind": local,
        "wildcard_bind": wildcard,
        "non_local_bind": non_local,
        "public_host_intent": public,
    }


def _normalize_host_value(raw_value: str) -> str:
    value = str(raw_value or "").strip()
    if not value:
        return ""
    if "://" in value:
        parsed = urlparse(value)
        value = parsed.hostname or ""
    elif value.startswith("[") and "]" in value:
        value = value[1:value.find("]")]
    elif value.count(":") == 1:
        host, port = value.rsplit(":", 1)
        if port.isdigit():
            value = host
    return value.strip().strip("[]").rstrip(".").lower()


def _parse_ip_address(host: str):
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _safe_reported_host_value(host: str, classification: str) -> str:
    if classification in {"local", "wildcard_container"}:
        return host
    return "[configured non-local host redacted]"


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
        "execution_mode": "durable_queue_api_process_drain",
        "running_count": running_count,
        "message": (
            "One or more workflows are currently draining inside this API process."
            if running_count
            else "Workflow jobs are durable when Postgres is active, but draining still runs inside this API process."
        ),
    }


def _overall_status(checks: dict[str, dict]) -> str:
    statuses = {str(check.get("status", "")) for check in checks.values()}
    if "fail" in statuses:
        return "fail"
    if "degraded" in statuses:
        return "degraded"
    return "ok"
