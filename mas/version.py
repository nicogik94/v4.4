"""Canonical version and git SHA provenance for the v4 MAS."""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

APP_VERSION = "4.4.0"


def get_git_sha() -> str:
    """Return a short git SHA for provenance stamping.

    Resolution order:
    1. V4_GIT_SHA env var (explicit override)
    2. GIT_SHA env var
    3. GITHUB_SHA env var (set automatically in GitHub Actions)
    4. git rev-parse --short HEAD if .git is reachable
    5. "unknown"
    """
    for var in ("V4_GIT_SHA", "GIT_SHA", "GITHUB_SHA"):
        value = os.getenv(var, "").strip()
        if value:
            return value[:12]

    try:
        repo_root = _find_repo_root()
        if repo_root is not None:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
                cwd=str(repo_root),
            )
            sha = result.stdout.strip()
            if sha:
                return sha
    except Exception:
        pass

    return "unknown"


def _find_repo_root() -> Path | None:
    candidate = Path(__file__).resolve().parent
    for _ in range(6):
        if (candidate / ".git").exists():
            return candidate
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Exact build provenance (V4.4 pilot integrity P0-5)
#
# A real pre-pilot machine archive carried ``code_version=4.4.0`` and
# ``git_sha=unknown``. A release name is not a build: the artifact could not be
# reproduced, and nothing failed because of it. Two structural causes — the
# export manifest resolved the SHA *at export time* (so a checkout-less
# container stamped whatever it could see, which was nothing), and the SHA was
# never written to the project state, so it could not survive the run.
#
# ``get_git_sha`` above is unchanged: /health and /runtime/preflight keep their
# short-SHA display behaviour. The helpers below are the strict provenance
# contract used for evidence:
#   * an exact 40-character commit id, or the empty string;
#   * ``git_sha_status`` is ``exact`` or ``unavailable`` — never collapsed, never
#     a ref name, a short SHA or a fabricated value;
#   * the run's identity is stamped once and never rewritten by a later
#     environment, including upgrading ``unavailable`` to a SHA discovered
#     afterwards, which would attribute the run to a build that may not have
#     produced it.
# ═══════════════════════════════════════════════════════════════════════════

BUILD_PROVENANCE_SCHEMA_VERSION = "build_provenance.v1"

GIT_SHA_EXACT = "exact"
GIT_SHA_UNAVAILABLE = "unavailable"
GIT_SHA_STATUSES = (GIT_SHA_EXACT, GIT_SHA_UNAVAILABLE)

SOURCE_GITHUB_SHA = "github_sha"
SOURCE_ENV_OVERRIDE = "env_override"
SOURCE_GIT_REV_PARSE = "git_rev_parse"
SOURCE_UNAVAILABLE = "unavailable"

ORIGIN_RECORDED_RUN = "recorded_run"
ORIGIN_AMBIENT = "ambient"

_EXACT_SHA_RX = re.compile(r"^[0-9a-f]{40}$")
_ENV_OVERRIDE_NAMES = ("V4_GIT_SHA", "GIT_SHA")


class ExactBuildProvenanceError(RuntimeError):
    """Raised when a path requiring reproducible build evidence cannot get it."""


@dataclass(frozen=True)
class BuildProvenance:
    code_version: str
    git_sha: str
    git_sha_status: str
    git_sha_source: str
    recorded_at: str = ""
    origin: str = ORIGIN_AMBIENT

    @property
    def is_exact(self) -> bool:
        return self.git_sha_status == GIT_SHA_EXACT and bool(self.git_sha)

    def as_dict(self) -> dict:
        return {
            "schema_version": BUILD_PROVENANCE_SCHEMA_VERSION,
            "code_version": self.code_version,
            "git_sha": self.git_sha,
            "git_sha_status": self.git_sha_status,
            "git_sha_source": self.git_sha_source,
            "recorded_at": self.recorded_at,
            "origin": self.origin,
        }


def normalize_exact_sha(value: object) -> str:
    """An exact lowercase 40-character commit SHA, or ``""`` for anything else."""
    if not isinstance(value, str):
        return ""
    candidate = value.strip().lower()
    return candidate if _EXACT_SHA_RX.fullmatch(candidate) else ""


def current_build_provenance(environ: Mapping[str, str] | None = None) -> BuildProvenance:
    """The build this process is running."""
    source_env: Mapping[str, str] = os.environ if environ is None else environ
    sha, source = _resolve_exact_sha(source_env)
    return BuildProvenance(
        code_version=APP_VERSION or "unknown",
        git_sha=sha,
        git_sha_status=GIT_SHA_EXACT if sha else GIT_SHA_UNAVAILABLE,
        git_sha_source=source,
    )


def record_build_provenance(state, environ: Mapping[str, str] | None = None) -> BuildProvenance:
    """Stamp the run's build identity once and keep it."""
    existing = recorded_build_provenance(state)
    if existing is not None:
        return existing
    ambient = current_build_provenance(environ)
    recorded = BuildProvenance(
        code_version=ambient.code_version,
        git_sha=ambient.git_sha,
        git_sha_status=ambient.git_sha_status,
        git_sha_source=ambient.git_sha_source,
        recorded_at=datetime.now(timezone.utc).isoformat(),
        origin=ORIGIN_RECORDED_RUN,
    )
    if hasattr(state, "build_provenance"):
        state.build_provenance = recorded.as_dict()
    return recorded


def recorded_build_provenance(state) -> BuildProvenance | None:
    payload = getattr(state, "build_provenance", None)
    if not isinstance(payload, dict) or not payload:
        return None
    status = str(payload.get("git_sha_status") or "")
    if status not in GIT_SHA_STATUSES:
        return None
    return BuildProvenance(
        code_version=str(payload.get("code_version") or ""),
        git_sha=normalize_exact_sha(payload.get("git_sha")),
        git_sha_status=status,
        git_sha_source=str(payload.get("git_sha_source") or SOURCE_UNAVAILABLE),
        recorded_at=str(payload.get("recorded_at") or ""),
        origin=ORIGIN_RECORDED_RUN,
    )


def state_build_provenance(state, environ: Mapping[str, str] | None = None) -> BuildProvenance:
    """The run's recorded build, or the exporting environment's as a fallback."""
    recorded = recorded_build_provenance(state)
    return recorded if recorded is not None else current_build_provenance(environ)


def export_provenance_payload(state, environ: Mapping[str, str] | None = None) -> dict:
    """Provenance fields for an export/archive manifest. Pure."""
    provenance = state_build_provenance(state, environ)
    return {
        # Named apart from the manifest's own ``code_version``: that one is the
        # *current* code identity the freshness check compares against, this one
        # is the build recorded with the run.
        "build_code_version": provenance.code_version,
        "git_sha": provenance.git_sha,
        "git_sha_status": provenance.git_sha_status,
        "git_sha_source": provenance.git_sha_source,
        "git_sha_origin": provenance.origin,
        "build_recorded_at": provenance.recorded_at,
        "exact_build_provenance": provenance.is_exact,
    }


def require_exact_build_provenance(state=None, environ: Mapping[str, str] | None = None) -> BuildProvenance:
    """Fail closed when reproducible build evidence is required but absent."""
    provenance = (
        current_build_provenance(environ) if state is None else state_build_provenance(state, environ)
    )
    if not provenance.is_exact:
        raise ExactBuildProvenanceError(
            "Exact build provenance is required but unavailable "
            f"(status={provenance.git_sha_status}, source={provenance.git_sha_source}). "
            "Re-run from a git checkout or supply GITHUB_SHA/V4_GIT_SHA as an exact commit SHA."
        )
    return provenance


def _resolve_exact_sha(environ: Mapping[str, str]) -> tuple[str, str]:
    candidate = normalize_exact_sha(environ.get("GITHUB_SHA", ""))
    if candidate:
        return candidate, SOURCE_GITHUB_SHA
    for name in _ENV_OVERRIDE_NAMES:
        candidate = normalize_exact_sha(environ.get(name, ""))
        if candidate:
            return candidate, SOURCE_ENV_OVERRIDE
    candidate = normalize_exact_sha(_git_head_sha())
    if candidate:
        return candidate, SOURCE_GIT_REV_PARSE
    return "", SOURCE_UNAVAILABLE


def _git_head_sha() -> str:
    """``git rev-parse HEAD`` from the package root, or ``""``."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except Exception:  # noqa: BLE001 - provenance never breaks a run
        return ""
    return completed.stdout if completed.returncode == 0 else ""
