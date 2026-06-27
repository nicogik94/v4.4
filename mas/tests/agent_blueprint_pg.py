"""Test-only PostgreSQL support for Agent Blueprint Studio (v50) schema tests.

Applies init.sql -> v50_agent_blueprint_studio_foundation.sql into a disposable,
isolated schema and provides introspection used purely by test assertions
(bootstrap, complete-reapply, partial-divergent). v50's only schema dependency is
the base ``projects`` table from init.sql.

This helper exists only under tests/. It is NOT a production migration runner. The
disposable database DSN is supplied via TEST_STUDIO_PG_DSN (preferred) or
TEST_EVIDENCE_PG_DSN (shared disposable PG); when neither is set the Studio
PostgreSQL tests are skipped. The authoritative MAS database is never touched.
"""
from __future__ import annotations

import contextlib
import os
import uuid
from pathlib import Path
from typing import Iterator, Optional

import pytest

ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = ROOT / "sql"

INIT_SQL = SQL_DIR / "init.sql"
V50_SQL = SQL_DIR / "v50_agent_blueprint_studio_foundation.sql"

STUDIO_TABLES = (
    "blueprint_project",
    "blueprint_config_revision",
    "blueprint_source_item",
    "blueprint_source_extract",
    "blueprint_artifact",
    "blueprint_artifact_input_binding",
    "blueprint_lint_result",
    "blueprint_lint_finding",
    "blueprint_eval_case",
    "blueprint_eval_run",
    "blueprint_draft_export",
)


def dsn() -> Optional[str]:
    return os.getenv("TEST_STUDIO_PG_DSN") or os.getenv("TEST_EVIDENCE_PG_DSN")


def require_dsn() -> str:
    value = dsn()
    if not value:
        pytest.skip(
            "TEST_STUDIO_PG_DSN/TEST_EVIDENCE_PG_DSN not set; "
            "Studio PostgreSQL tests require a disposable database"
        )
    return value


def psycopg_module():
    try:
        import psycopg
    except ImportError:  # pragma: no cover - environment guard
        pytest.skip("psycopg is not installed; Studio PostgreSQL tests require it")
    return psycopg


def connect(*, autocommit: bool = False):
    psycopg = psycopg_module()
    conn = psycopg.connect(require_dsn())
    conn.autocommit = autocommit
    return conn


def _begin_autocommit(conn) -> bool:
    prior = conn.autocommit
    if not prior:
        conn.rollback()
    conn.autocommit = True
    return prior


def _restore_autocommit(conn, prior: bool) -> None:
    try:
        conn.autocommit = prior
    except Exception:
        conn.rollback()
        conn.autocommit = prior


def _run_script(conn, path: Path) -> None:
    conn.execute(path.read_text(encoding="utf-8"))


def apply_base_and_v50(conn, schema: str) -> None:
    """Create an isolated schema and apply init.sql then v50 into it."""
    prior = _begin_autocommit(conn)
    conn.execute(f'CREATE SCHEMA "{schema}"')
    conn.execute(f'SET search_path TO "{schema}"')
    _run_script(conn, INIT_SQL)
    _run_script(conn, V50_SQL)
    _restore_autocommit(conn, prior)


def apply_v50(conn) -> None:
    """(Re)apply only v50 into the current search_path schema."""
    prior = _begin_autocommit(conn)
    _run_script(conn, V50_SQL)
    _restore_autocommit(conn, prior)


def drop_schema(conn, schema: str) -> None:
    prior = _begin_autocommit(conn)
    conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    _restore_autocommit(conn, prior)


@contextlib.contextmanager
def fresh_schema(conn) -> Iterator[str]:
    schema = f"studio_test_{uuid.uuid4().hex[:16]}"
    try:
        apply_base_and_v50(conn, schema)
        conn.autocommit = False
        conn.execute(f'SET search_path TO "{schema}"')
        conn.commit()
        yield schema
    finally:
        with contextlib.suppress(Exception):
            conn.rollback()
        drop_schema(conn, schema)


# ─────────────────────────────── Introspection ───────────────────────────────


def table_exists(conn, schema: str, table: str) -> bool:
    row = conn.execute("SELECT to_regclass(%s)", (f'"{schema}".{table}',)).fetchone()
    return row[0] is not None


def constraint_exists(conn, schema: str, constraint: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM pg_constraint con
        JOIN pg_namespace n ON n.oid = con.connamespace
        WHERE n.nspname = %s AND con.conname = %s
        """,
        (schema, constraint),
    ).fetchone()
    return row is not None


def studio_tables_present(conn, schema: str) -> int:
    return sum(1 for t in STUDIO_TABLES if table_exists(conn, schema, t))


def trigger_count(conn, schema: str) -> int:
    """Count non-internal triggers on the Studio tables (must be 0 in S1)."""
    row = conn.execute(
        """
        SELECT count(*) FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND NOT t.tgisinternal
          AND c.relname LIKE 'blueprint_%%'
        """,
        (schema,),
    ).fetchone()
    return int(row[0])
