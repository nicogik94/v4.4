"""Test-only PostgreSQL support for Slice A evidence-snapshot tests.

Applies the real bootstrap ordering (init.sql -> outcomes.sql ->
v47_evidence_snapshot_foundation.sql) against a genuine PostgreSQL database and
provides schema introspection used purely by test assertions for bootstrap,
complete-reapply, and partial-schema state.

This helper exists only under tests/. It is NOT a production migration runner and
ships no runtime schema-state management. The disposable database DSN is supplied
via the TEST_EVIDENCE_PG_DSN environment variable (dependency injection); when it
is unset the Slice A PostgreSQL tests are skipped.
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
OUTCOMES_SQL = SQL_DIR / "outcomes.sql"
V47_SQL = SQL_DIR / "v47_evidence_snapshot_foundation.sql"
V48_SQL = SQL_DIR / "v48_automation_roi_foundation.sql"

# Slice B (Automation ROI) objects — used by the v48 schema tests.
SLICE_B_TABLES = (
    "candidate_fact_extraction_context",
    "candidate_fact_approval_decision",
    "approved_calculation_input",
    "calculation_result",
    "calculation_result_input",
)

DSN_ENV = "TEST_EVIDENCE_PG_DSN"

SLICE_A_TABLES = (
    "source_blob",
    "source_snapshot",
    "candidate_fact_revision",
    "evidence_retention_event",
    "ingest_operation",
)
IMMUTABLE_TABLES = (
    "source_blob",
    "source_snapshot",
    "candidate_fact_revision",
    "evidence_retention_event",
)
SLICE_A_TRIGGERS = (
    ("trg_source_blob_no_mutation", "source_blob"),
    ("trg_source_snapshot_no_mutation", "source_snapshot"),
    ("trg_cfr_no_mutation", "candidate_fact_revision"),
    ("trg_retention_no_mutation", "evidence_retention_event"),
)
XOR_CONSTRAINT = "ck_retention_single_target"
REJECT_FUNCTION = "slicea_reject_mutation"


def dsn() -> Optional[str]:
    return os.getenv(DSN_ENV)


def require_dsn() -> str:
    value = dsn()
    if not value:
        pytest.skip(f"{DSN_ENV} not set; Slice A PostgreSQL tests require a disposable database")
    return value


def psycopg_module():
    try:
        import psycopg
    except ImportError:  # pragma: no cover - environment guard
        pytest.skip("psycopg is not installed; Slice A PostgreSQL tests require it")
    return psycopg


def connect(*, schema: Optional[str] = None, autocommit: bool = False):
    """Open a fresh connection, optionally pinned to a test schema search_path."""
    psycopg = psycopg_module()
    conn = psycopg.connect(require_dsn())
    conn.autocommit = autocommit
    if schema is not None:
        conn.execute(f'SET search_path TO "{schema}"')
        if not autocommit:
            conn.commit()
    return conn


def _begin_autocommit(conn) -> bool:
    """Switch to autocommit, leaving any in-progress transaction first."""
    prior = conn.autocommit
    if not prior:
        conn.rollback()  # autocommit cannot be toggled while INTRANS
    conn.autocommit = True
    return prior


def _restore_autocommit(conn, prior: bool) -> None:
    try:
        conn.autocommit = prior
    except Exception:
        # An aborted (INERROR) transaction blocks toggling; clear and retry.
        conn.rollback()
        conn.autocommit = prior


def _run_script(conn, path: Path) -> None:
    # psycopg3 permits multiple statements in one execute() when there are no
    # parameters; the connection is in autocommit so the script's own
    # transaction boundary (v47) is respected.
    conn.execute(path.read_text(encoding="utf-8"))


def apply_full_schema(conn, schema: str) -> None:
    """Create an isolated schema and apply init -> outcomes -> v47 into it."""
    prior = _begin_autocommit(conn)
    conn.execute(f'CREATE SCHEMA "{schema}"')
    conn.execute(f'SET search_path TO "{schema}"')
    _run_script(conn, INIT_SQL)
    _run_script(conn, OUTCOMES_SQL)
    _run_script(conn, V47_SQL)
    _restore_autocommit(conn, prior)


def apply_v47(conn) -> None:
    """(Re)apply only the Slice A migration into the current search_path schema."""
    prior = _begin_autocommit(conn)
    _run_script(conn, V47_SQL)
    _restore_autocommit(conn, prior)


def apply_v48(conn) -> None:
    """(Re)apply only the Slice B migration into the current search_path schema."""
    prior = _begin_autocommit(conn)
    _run_script(conn, V48_SQL)
    _restore_autocommit(conn, prior)


def slice_b_tables_present(conn, schema: str) -> int:
    """Count how many of the five Slice B tables exist in ``schema``."""
    return sum(1 for t in SLICE_B_TABLES if table_exists(conn, schema, t))


def drop_schema(conn, schema: str) -> None:
    prior = _begin_autocommit(conn)
    conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    _restore_autocommit(conn, prior)


@contextlib.contextmanager
def fresh_schema(conn) -> Iterator[str]:
    """Provide a freshly bootstrapped isolated schema; drop it on exit."""
    schema = f"slicea_test_{uuid.uuid4().hex[:16]}"
    try:
        apply_full_schema(conn, schema)
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


def trigger_exists(conn, schema: str, trigger: str, table: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relname = %s AND t.tgname = %s
        """,
        (schema, table, trigger),
    ).fetchone()
    return row is not None


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


def function_exists(conn, schema: str, function: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = %s AND p.proname = %s
        """,
        (schema, function),
    ).fetchone()
    return row is not None


def classify_schema(conn, schema: str) -> str:
    """Classify Slice A schema state as 'none', 'complete', or 'partial'.

    Test-only introspection: 'none' = no Slice A objects present; 'complete' =
    all five tables, four triggers, the XOR constraint, and the reject function
    present; anything in between is 'partial' (divergent).
    """
    tables_present = [t for t in SLICE_A_TABLES if table_exists(conn, schema, t)]
    triggers_present = [
        trg for trg, tbl in SLICE_A_TRIGGERS if trigger_exists(conn, schema, trg, tbl)
    ]
    xor_present = constraint_exists(conn, schema, XOR_CONSTRAINT)
    fn_present = function_exists(conn, schema, REJECT_FUNCTION)

    nothing = not tables_present and not triggers_present and not fn_present
    if nothing:
        return "none"
    complete = (
        len(tables_present) == len(SLICE_A_TABLES)
        and len(triggers_present) == len(SLICE_A_TRIGGERS)
        and xor_present
        and fn_present
    )
    return "complete" if complete else "partial"


def insert_project(conn, *, name: str = "Slice A project", project_id: Optional[str] = None) -> str:
    if project_id is None:
        row = conn.execute(
            "INSERT INTO projects (name, brief) VALUES (%s, %s) RETURNING id::text",
            (name, ""),
        ).fetchone()
        return row[0]
    conn.execute(
        "INSERT INTO projects (id, name, brief) VALUES (%s, %s, %s)",
        (project_id, name, ""),
    )
    return project_id
