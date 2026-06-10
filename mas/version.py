"""Canonical version and git SHA provenance for the v4 MAS."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

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
