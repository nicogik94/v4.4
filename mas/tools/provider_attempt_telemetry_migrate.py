"""The authoritative application path for the provider-telemetry schema.

Blocker 12 of the audit: "v63 is not part of an authoritative bootstrap/migration
path." A `.sql` file that only ever runs by hand is not a migration; it is a
suggestion. This module is the path, and it is the only one the documented
bootstrap and restoration procedures reference.

What "authoritative" means here, concretely:

* **Fail on first error.** The connection runs the script as one statement in
  one transaction with the script's own ``BEGIN``/``COMMIT``; psycopg raises on
  the first failing statement and the whole application rolls back. This is the
  programmatic equivalent of ``psql -v ON_ERROR_STOP=1 --single-transaction``,
  and ``--psql`` runs literally that when a ``psql`` binary is available.
* **The source is verified before it is trusted.** The file's SHA-256 is
  computed and recorded, so a ledger entry names the exact bytes that were
  applied rather than a filename that may since have changed.
* **Required roles are verified before anything is attempted**, with a
  diagnostic that names what is missing.
* **Strict postflight, independently of the SQL's own.** The SQL verifies itself
  against a contract embedded in the SQL; this module verifies the result
  against the contract embedded in *Python*
  (``provider_telemetry.repository.READ_COLUMNS``). Two independent statements of
  the same contract have to agree, so a typo in one is caught by the other.
* **Success cannot be reported after a rollback.** The ledger row is read back
  from a *fresh connection* after the applying connection is closed. If the
  transaction rolled back, the row is not there, and this module reports failure
  no matter what the earlier steps appeared to do.

Usage::

    python -m tools.provider_attempt_telemetry_migrate preflight
    python -m tools.provider_attempt_telemetry_migrate apply
    python -m tools.provider_attempt_telemetry_migrate verify
    python -m tools.provider_attempt_telemetry_migrate bootstrap --print-order
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from provider_telemetry import repository  # noqa: E402
from provider_telemetry.models import (  # noqa: E402
    MIGRATION_NAME,
    TELEMETRY_SCHEMA_VERSION,
)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_UNAVAILABLE = 3

MIGRATION_PATH = ROOT / "sql" / MIGRATION_NAME

OWNER_ROLE = "workflow_provider_telemetry_owner"
WRITER_ROLE = "workflow_provider_telemetry_writer"
READER_ROLE = "workflow_provider_telemetry_reader"
REQUIRED_ROLES = (OWNER_ROLE, WRITER_ROLE, READER_ROLE)

# ─────────────────────────── pinned function bodies ───────────────────────────
#
# The guard function's *implementation* is part of the catalog contract, not
# just its name, language, security label and search_path. A body replaced with
# `RETURN OLD` satisfies every other property this tool checks while disarming
# every append-only trigger, and `CREATE OR REPLACE FUNCTION` in the migration
# would then quietly install the correct body over it and report the run as
# `reapplied_noop`. The migration's own preflight refuses that before the
# replacement can happen; this is the second, independent statement of the same
# contract — the SQL checks the schema against a contract embedded in the SQL,
# and this checks it against a contract embedded in Python, so a body edited in
# one place and not the other is caught by the other.
#
# These are PostgreSQL's own stored representations (``pg_proc.prosrc``): the
# exact text between the dollar-quote delimiters in the migration source,
# leading and trailing newline included.
EXPECTED_FUNCTION_BODIES: dict[str, str] = {
    "provider_telemetry_reject_mutation": (
        "\n"
        "BEGIN\n"
        "    RAISE EXCEPTION\n"
        "        'provider telemetry is append-only; % on % is not permitted',\n"
        "        TG_OP, TG_TABLE_NAME\n"
        "        USING ERRCODE = 'restrict_violation';\n"
        "END;\n"
    ),
    "provider_telemetry_array_is_clean": (
        "\n"
        "    SELECT p_values IS NULL\n"
        "        OR (array_position(p_values, NULL) IS NULL\n"
        "            AND cardinality(p_values) = (\n"
        "                SELECT count(DISTINCT item) FROM unnest(p_values) AS item));\n"
    ),
    "provider_telemetry_has_credential_shape": (
        "\n"
        "    SELECT p_value IS NOT NULL AND (\n"
        "        p_value ~* 'sk-ant-'\n"
        "        OR p_value ~* '\\ysk-[A-Za-z0-9_-]{8,}'\n"
        "        OR p_value ~* '\\y[rs]k_(live|test)_[A-Za-z0-9]{8,}'\n"
        "        OR p_value ~* '\\ygh[pousr]_[A-Za-z0-9]{8,}'\n"
        "        OR p_value ~* '\\yxox[baprs]-'\n"
        "        OR p_value ~ '\\y(AKIA|ASIA|AROA|AIDA)[A-Z0-9]{12,}'\n"
        "        OR p_value ~ '\\yAIza[A-Za-z0-9_-]{20,}'\n"
        "        OR p_value ~* 'bearer[[:space:]_.:=-]*[A-Za-z0-9+/=_-]{4,}'\n"
        "        OR p_value ~* 'basic[[:space:]_.:=-]*[A-Za-z0-9+/=_-]{8,}'\n"
        "        OR p_value ~* 'authoriz(ation|ed?)[[:space:]_.:=-]'\n"
        "        OR p_value ~* '(api[_.-]?key|access[_.-]?token|auth[_.-]?token|id[_.-]?token|refresh[_.-]?token|session[_.-]?id|session|secret|passwd|password|credential|cookie|private[_.-]?key)[[:space:]_.-]*[:=]'\n"
        "        OR p_value ~* '\\y(api[_.-]?key|access[_.-]?token|auth[_.-]?token|id[_.-]?token|refresh[_.-]?token|session[_.-]?id|secret|passwd|password|credential|cookie|private[_.-]?key)[[:space:]_.-]+[A-Za-z0-9+/=_-]{6,}'\n"
        "        OR p_value ~* '[a-z][a-z0-9+.-]*://'\n"
        "        OR p_value ~ '@'\n"
        "        OR p_value ~* '%(20|3a|3d|2f|2b)'\n"
        "        OR p_value ~ '\\yeyJ[A-Za-z0-9_-]{8,}\\.[A-Za-z0-9_-]{8,}'\n"
        "    );\n"
    ),
}


def function_body_digest(body: str) -> str:
    """The digest this tool reports for a function body. Never a secret."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


EXPECTED_FUNCTION_BODY_SHA256: dict[str, str] = {
    name: function_body_digest(body) for name, body in EXPECTED_FUNCTION_BODIES.items()
}

# ─────────────────────────── pinned function ACLs ────────────────────────────
#
# The complete set of roles holding EXECUTE on each protected function. Left
# implicit, this was a drift false negative with real impact: PostgreSQL stores
# an untouched function's ACL as NULL — meaning "PUBLIC may execute" — and the
# two CHECK-constraint helpers were sitting on that default. Revoking PUBLIC
# EXECUTE from `provider_telemetry_array_is_clean` then broke *every* telemetry
# INSERT, because PostgreSQL evaluates a CHECK constraint's function call with
# the inserting role's privileges, while `apply` reported `reapplied_noop` and
# `verify` reported healthy.
#
#   reject_mutation()           owner only. SECURITY DEFINER; the trigger
#                               machinery invokes it, no caller needs it.
#   array_is_clean(text[])      owner + writer. Required by the writer directly:
#   has_credential_shape(text)  without it, INSERT fails with 42501.
#
# The reader is deliberately absent from all three: a CHECK constraint is never
# evaluated on SELECT, so a read-only role needs no EXECUTE anywhere here.
EXPECTED_FUNCTION_ACLS: dict[str, tuple[str, ...]] = {
    "provider_telemetry_reject_mutation": (OWNER_ROLE,),
    "provider_telemetry_array_is_clean": (OWNER_ROLE, WRITER_ROLE),
    "provider_telemetry_has_credential_shape": (OWNER_ROLE, WRITER_ROLE),
}

# Owner, security label and search_path for every protected function, not just
# the guard. The two CHECK helpers run as the *inserting* role, so a helper
# silently turned SECURITY DEFINER, or one whose fixed search_path was reset,
# changes who the constraint's code runs as and what names it resolves — the
# same class of drift the guard's own hardening check exists to catch.
EXPECTED_FUNCTION_HARDENING: dict[str, tuple[str, bool, list[str]]] = {
    "provider_telemetry_reject_mutation": (OWNER_ROLE, True, ["search_path=pg_catalog"]),
    "provider_telemetry_array_is_clean": (OWNER_ROLE, False, ["search_path=pg_catalog"]),
    "provider_telemetry_has_credential_shape": (
        OWNER_ROLE, False, ["search_path=pg_catalog"],
    ),
}

# Indexes whose exact definition is load-bearing for a *correctness* claim
# rather than for performance, and which the Python-side contract therefore
# names independently of the SQL file's own index contract. Dropping or
# weakening either of these silently removes a guarantee reconciliation relies
# on, so `verify` refuses a schema that no longer carries them exactly.
EXPECTED_INDEX_DEFINITIONS: dict[str, str] = {
    "idx_provider_attempt_event_one_terminal": (
        "CREATE UNIQUE INDEX idx_provider_attempt_event_one_terminal "
        "ON provider_attempt_event USING btree (subject_id) WHERE is_terminal"
    ),
    "idx_provider_attempt_event_terminal": (
        "CREATE INDEX idx_provider_attempt_event_terminal "
        "ON provider_attempt_event USING btree "
        "(telemetry_run_id, subject_kind, subject_id) WHERE is_terminal"
    ),
}

# The documented bootstrap ordering. v63 is free-standing — it declares no
# foreign key — so it may be applied at any point after the base schema exists,
# and may equally be restored on its own into an empty database.
BOOTSTRAP_ORDER = (
    "init.sql",
    "outcomes.sql",
    "v47_evidence_snapshot_foundation.sql",
    "v48_automation_roi_foundation.sql",
    "v49_automation_roi_calculation_idempotency.sql",
    "v51_research_evidence_sidecar_foundation.sql",
    "v52_research_evidence_audit_integrity.sql",
    "v53_research_evidence_intake_foundation.sql",
    "v54_research_evidence_review_foundation.sql",
    "v55_research_evidence_freshness_foundation.sql",
    "v56_research_evidence_claim_support_foundation.sql",
    "v57_research_evidence_binding_foundation.sql",
    "v58_research_evidence_scenario_input_evaluation_foundation.sql",
    "v59_research_evidence_automation_roi_input_snapshot.sql",
    "v60_research_evidence_automation_roi_execution.sql",
    "v61_research_evidence_pack_foundation.sql",
    MIGRATION_NAME,
)


class MigrationError(RuntimeError):
    """The migration could not be applied, or could not be proven applied."""


def _open_migration_connection():
    """The only place this tool obtains a connection.

    Tests replace this with a callable returning a connection to a disposable
    database; production uses MAS_TELEMETRY_MIGRATION_DSN or DATABASE_URL.
    """
    import psycopg

    dsn = (
        os.environ.get("MAS_TELEMETRY_MIGRATION_DSN", "").strip()
        or os.environ.get("DATABASE_URL", "").strip()
    )
    if not dsn:
        raise MigrationError(
            "no migration DSN: set MAS_TELEMETRY_MIGRATION_DSN or DATABASE_URL"
        )
    return psycopg.connect(dsn)


open_migration_connection: Callable[[], Any] = _open_migration_connection


def migration_sha256(path: Path = MIGRATION_PATH) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _emit(payload: dict, stream) -> None:
    stream.write(json.dumps(payload, sort_keys=True, indent=2, default=str))
    stream.write("\n")


# ─────────────────────────── verification ───────────────────────────


def missing_roles(conn) -> list[str]:
    rows = conn.execute(
        "SELECT rolname FROM pg_catalog.pg_roles WHERE rolname = ANY(%s)",
        (list(REQUIRED_ROLES),),
    ).fetchall()
    present = {row[0] for row in rows}
    return [role for role in REQUIRED_ROLES if role not in present]


def verify_contract(conn) -> list[str]:
    """Verify the applied schema against the Python-side contract.

    Independent of the SQL file's own postflight on purpose: this reads the
    column tuples that the writer and the exporter actually use, so a schema that
    satisfies the SQL but not the code is still a failure.
    """
    problems: list[str] = []

    for table in repository.TELEMETRY_TABLES:
        rows = conn.execute(
            """
            SELECT a.attname
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = current_schema() AND c.relname = %s
              AND c.relkind = 'r' AND a.attnum > 0 AND NOT a.attisdropped
            """,
            (table,),
        ).fetchall()
        present = {row[0] for row in rows}
        if not present:
            problems.append(f"table_absent:{table}")
            continue
        # A one-column table named `provider_attempt` fails right here.
        absent = set(repository.READ_COLUMNS[table]) - present
        if absent:
            problems.append(f"columns_absent:{table}:{','.join(sorted(absent))}")

    for table in repository.APPEND_ONLY_TABLES:
        rows = conn.execute(
            """
            SELECT t.tgname, t.tgenabled::text
            FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = current_schema() AND c.relname = %s
              AND NOT t.tgisinternal
            """,
            (table,),
        ).fetchall()
        enabled = {name for name, state in rows if state == "O"}
        for suffix in ("no_mutation", "no_truncate"):
            expected = f"trg_{table}_{suffix}"
            if expected not in enabled:
                problems.append(f"trigger_missing_or_disabled:{expected}")

        owner = conn.execute(
            """
            SELECT pg_catalog.pg_get_userbyid(c.relowner)
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = current_schema() AND c.relname = %s
            """,
            (table,),
        ).fetchone()
        if owner and owner[0] != OWNER_ROLE:
            problems.append(f"wrong_owner:{table}:{owner[0]}")

        acl = conn.execute(
            """
            SELECT
                has_table_privilege(%s, %s, 'INSERT'),
                has_table_privilege(%s, %s, 'SELECT'),
                has_table_privilege(%s, %s, 'UPDATE'),
                has_table_privilege(%s, %s, 'DELETE'),
                has_table_privilege(%s, %s, 'TRUNCATE'),
                has_table_privilege(%s, %s, 'TRIGGER'),
                has_table_privilege(%s, %s, 'SELECT')
            """,
            (
                WRITER_ROLE, table, WRITER_ROLE, table, WRITER_ROLE, table,
                WRITER_ROLE, table, WRITER_ROLE, table, WRITER_ROLE, table,
                READER_ROLE, table,
            ),
        ).fetchone()
        can_insert, can_select, can_update, can_delete, can_truncate, can_trigger, reader_select = acl
        if not (can_insert and can_select):
            problems.append(f"writer_cannot_append:{table}")
        for name, granted in (
            ("update", can_update),
            ("delete", can_delete),
            ("truncate", can_truncate),
            ("trigger", can_trigger),
        ):
            if granted:
                problems.append(f"writer_holds_{name}:{table}")
        if not reader_select:
            problems.append(f"reader_cannot_select:{table}")

    fn = conn.execute(
        """
        SELECT p.prosecdef, p.proconfig
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = current_schema()
          AND p.proname = 'provider_telemetry_reject_mutation'
        """
    ).fetchone()
    if fn is None:
        problems.append("guard_function_absent")
    elif not fn[0] or fn[1] != ["search_path=pg_catalog"]:
        problems.append("guard_function_unhardened")

    problems.extend(_function_body_problems(conn))
    problems.extend(_function_hardening_problems(conn))
    problems.extend(_function_acl_problems(conn))
    problems.extend(_index_problems(conn))
    return problems


def _function_body_problems(conn) -> list[str]:
    """Compare the stored implementation of each protected function, exactly.

    Reported as its own problem code so a caller can tell "the guard is not
    hardened" (a configuration difference) from "the guard does not do what the
    guard is supposed to do" (a body someone rewrote).
    """
    problems: list[str] = []
    rows = conn.execute(
        """
        SELECT p.proname, p.prosrc, p.pronargs
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = current_schema()
          AND p.proname LIKE 'provider\\_telemetry\\_%'
        """
    ).fetchall()
    stored = {name: body for name, body, _ in rows}

    for name, expected in EXPECTED_FUNCTION_BODIES.items():
        actual = stored.get(name)
        if actual is None:
            problems.append(f"function_absent:{name}")
            continue
        if actual != expected:
            # The digest of what is *there*, never the body itself: a tampered
            # body is attacker-controlled text and does not belong in a
            # diagnostic that gets logged and pasted into tickets.
            problems.append(
                f"function_body_drift:{name}:{function_body_digest(actual)}"
            )

    unexpected = sorted(set(stored) - set(EXPECTED_FUNCTION_BODIES))
    for name in unexpected:
        problems.append(f"unexpected_protected_function:{name}")
    return problems


def _function_hardening_problems(conn) -> list[str]:
    """Owner, SECURITY DEFINER/INVOKER and search_path, for every helper.

    The guard's own hardening is checked separately and keeps its historical
    problem code; this covers the two CHECK-constraint helpers, whose drift had
    no Python-side check at all even though they run with the inserting role's
    privileges.
    """
    problems: list[str] = []
    rows = conn.execute(
        """
        SELECT p.proname, pg_catalog.pg_get_userbyid(p.proowner),
               p.prosecdef, p.proconfig
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = current_schema() AND p.proname = ANY(%s)
        """,
        (list(EXPECTED_FUNCTION_HARDENING),),
    ).fetchall()
    seen = {row[0]: row[1:] for row in rows}

    for name, (owner, secdef, config) in EXPECTED_FUNCTION_HARDENING.items():
        row = seen.get(name)
        if row is None:
            continue  # absence is reported once, by the body contract
        actual_owner, actual_secdef, actual_config = row
        if actual_owner != owner:
            problems.append(f"function_wrong_owner:{name}:{actual_owner}")
        if bool(actual_secdef) is not secdef:
            problems.append(
                f"function_security_drift:{name}:"
                f"{'definer' if actual_secdef else 'invoker'}"
            )
        if list(actual_config or []) != config:
            problems.append(
                f"function_search_path_drift:{name}:"
                f"{','.join(actual_config or []) or '<none>'}"
            )
    return problems


def _function_acl_problems(conn) -> list[str]:
    """Compare each protected function's EXECUTE grantees against the contract.

    Two independent questions are asked, because they fail differently:

    * the catalog's ACL is exactly the pinned set — this catches a *widening*
      (an added grantee) as well as a narrowing, and it is the check that
      notices ``proacl IS NULL``, PostgreSQL's storage for "PUBLIC may execute";
    * the writer can actually execute the two CHECK-constraint helpers — this is
      the one whose failure means every telemetry INSERT is refused, and it is
      asked through the privilege system rather than through the ACL text so a
      privilege reachable some other way is not reported as missing.
    """
    problems: list[str] = []
    rows = conn.execute(
        """
        SELECT p.proname,
               coalesce((
                   SELECT array_agg(DISTINCT
                              CASE WHEN a.grantee = 0 THEN 'PUBLIC'
                                   ELSE pg_catalog.pg_get_userbyid(a.grantee) END)
                   FROM aclexplode(coalesce(
                            p.proacl, pg_catalog.acldefault('f', p.proowner))) AS a
                   WHERE a.privilege_type = 'EXECUTE'
               ), ARRAY[]::text[]),
               has_function_privilege(%s, p.oid, 'EXECUTE'),
               has_function_privilege('public', p.oid, 'EXECUTE'),
               has_function_privilege(%s, p.oid, 'EXECUTE')
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = current_schema()
          AND p.proname = ANY(%s)
        """,
        (WRITER_ROLE, READER_ROLE, list(EXPECTED_FUNCTION_ACLS)),
    ).fetchall()
    seen = {row[0]: row[1:] for row in rows}

    for name, expected in EXPECTED_FUNCTION_ACLS.items():
        row = seen.get(name)
        if row is None:
            # Absence is already reported by the body contract; naming it again
            # here would double-count one missing object.
            continue
        grantees, writer_execute, public_execute, reader_execute = row
        if tuple(sorted(grantees)) != tuple(sorted(expected)):
            problems.append(
                f"function_acl_drift:{name}:{','.join(sorted(grantees)) or '<none>'}"
            )
        if public_execute:
            problems.append(f"function_execute_public:{name}")
        if reader_execute:
            problems.append(f"function_execute_reader:{name}")
        if WRITER_ROLE in expected and not writer_execute:
            # The material consequence, stated as its own code: a writer that
            # cannot execute a CHECK helper cannot insert a telemetry row at all.
            problems.append(f"writer_cannot_execute:{name}")
        if WRITER_ROLE not in expected and writer_execute:
            problems.append(f"function_execute_writer:{name}")
    return problems


def catalog_fingerprint(conn) -> str:
    """A digest of the whole catalog contract for the telemetry schema.

    Everything a reapplication could silently repair is in here: function
    bodies and their configuration, constraint definitions, index definitions,
    trigger types and enabled state, column types, ownership and ACLs. Taken
    before and after an application, it turns ``reapplied_noop`` from an
    assumption into a checked claim — a run that repaired drift produces two
    different fingerprints and is not a no-op, whatever the ledger says.
    """
    tables = list(repository.TELEMETRY_TABLES)
    parts: list[str] = []

    parts.append("functions:" + repr(conn.execute(
        """
        SELECT p.proname, p.prosrc, p.prosecdef, p.proconfig, l.lanname,
               p.provolatile, p.proisstrict, p.proparallel, p.pronargs,
               pg_catalog.format_type(p.prorettype, NULL),
               pg_catalog.pg_get_userbyid(p.proowner),
               coalesce(p.proacl::text, '<default>')
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        JOIN pg_language l ON l.oid = p.prolang
        WHERE n.nspname = current_schema() AND p.proname LIKE 'provider\\_telemetry\\_%'
        ORDER BY p.proname, p.pronargs
        """
    ).fetchall()))

    parts.append("constraints:" + repr(conn.execute(
        """
        SELECT c.relname, con.conname, pg_catalog.pg_get_constraintdef(con.oid)
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = current_schema() AND c.relname = ANY(%s)
        ORDER BY c.relname, con.conname
        """,
        (tables,),
    ).fetchall()))

    parts.append("indexes:" + repr(conn.execute(
        "SELECT tablename, indexname, indexdef FROM pg_indexes "
        "WHERE schemaname = current_schema() AND tablename = ANY(%s) "
        "ORDER BY tablename, indexname",
        (tables,),
    ).fetchall()))

    parts.append("triggers:" + repr(conn.execute(
        """
        SELECT c.relname, t.tgname, t.tgtype, t.tgenabled::text, t.tgnargs,
               t.tgqual IS NULL, p.proname
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_proc p ON p.oid = t.tgfoid
        WHERE n.nspname = current_schema() AND NOT t.tgisinternal
          AND c.relname = ANY(%s)
        ORDER BY c.relname, t.tgname
        """,
        (tables,),
    ).fetchall()))

    parts.append("columns:" + repr(conn.execute(
        """
        SELECT c.relname, a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod),
               a.attnotnull, a.attidentity::text,
               coalesce(pg_catalog.pg_get_expr(d.adbin, d.adrelid), '')
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
        WHERE n.nspname = current_schema() AND c.relname = ANY(%s)
          AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY c.relname, a.attname
        """,
        (tables,),
    ).fetchall()))

    parts.append("relations:" + repr(conn.execute(
        """
        SELECT c.relname, c.relpersistence::text,
               pg_catalog.pg_get_userbyid(c.relowner),
               coalesce(c.relacl::text, '<default>')
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = current_schema() AND c.relname = ANY(%s)
        ORDER BY c.relname
        """,
        (tables,),
    ).fetchall()))

    return hashlib.sha256("".join(parts).encode("utf-8")).hexdigest()


def _index_problems(conn) -> list[str]:
    """Verify the indexes that carry a correctness guarantee, by definition."""
    problems: list[str] = []
    rows = conn.execute(
        """
        SELECT indexname, indexdef FROM pg_indexes
        WHERE schemaname = current_schema() AND indexname = ANY(%s)
        """,
        (list(EXPECTED_INDEX_DEFINITIONS),),
    ).fetchall()
    schema = conn.execute("SELECT current_schema()").fetchone()[0]
    stored = {name: definition.replace(f"{schema}.", "") for name, definition in rows}
    for name, expected in EXPECTED_INDEX_DEFINITIONS.items():
        actual = stored.get(name)
        if actual is None:
            problems.append(f"index_absent:{name}")
        elif actual != expected:
            problems.append(f"index_definition_drift:{name}")
    return problems


# ─────────────────────────── commands ───────────────────────────


def cmd_preflight(args, stream) -> int:
    conn = open_migration_connection()
    try:
        conn.autocommit = True
        absent = missing_roles(conn)
        applied = _ledger_entries(conn)
        payload = {
            "command": "preflight",
            "migration": MIGRATION_NAME,
            "migration_sha256": migration_sha256(),
            "schema_version": TELEMETRY_SCHEMA_VERSION,
            "required_roles": list(REQUIRED_ROLES),
            "missing_roles": absent,
            "contract_problems": verify_contract(conn) if not absent else ["roles_absent"],
            "ledger": applied,
        }
        payload["status"] = "ok" if not absent and not payload["contract_problems"] else "unready"
        _emit(payload, stream)
        return EXIT_OK if payload["status"] == "ok" else EXIT_UNAVAILABLE
    finally:
        conn.close()


@contextlib.contextmanager
def _as_owner(conn):
    """Assume the telemetry owner role for the duration of a block.

    The migration role holds no privilege on the telemetry relations — that is
    the point of the ownership model — so every ledger read and write is done by
    assuming the owner role it is a member of, and giving it straight back.
    """
    conn.execute(f'SET ROLE "{OWNER_ROLE}"')
    try:
        yield conn
    finally:
        conn.execute("RESET ROLE")


def _ledger_entries(conn) -> list[dict]:
    try:
        with _as_owner(conn):
            rows = conn.execute(
                "SELECT migration_name, migration_sha256, schema_version, applied_at, "
                "applied_by, outcome FROM provider_telemetry_migration_ledger "
                "ORDER BY ledger_sequence"
            ).fetchall()
    except Exception:
        try:
            conn.rollback()
        except Exception:  # pragma: no cover - best effort
            pass
        return []
    return [
        {
            "migration_name": row[0],
            "migration_sha256": row[1],
            "schema_version": row[2],
            "applied_at": row[3].isoformat() if row[3] else None,
            "applied_by": row[4],
            "outcome": row[5],
        }
        for row in rows
    ]


def _apply_with_psql(path: Path, dsn: str) -> None:
    """Literal ``psql -v ON_ERROR_STOP=1 --single-transaction`` application."""
    result = subprocess.run(
        [
            "psql",
            dsn,
            "-v", "ON_ERROR_STOP=1",
            "--single-transaction",
            "--no-psqlrc",
            "-f", str(path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise MigrationError(
            f"psql exited {result.returncode}: {result.stderr.strip()[:800]}"
        )


def cmd_apply(args, stream) -> int:
    path = MIGRATION_PATH
    if not path.is_file():
        raise MigrationError(f"migration source is absent: {path}")
    digest = migration_sha256(path)
    expected = str(getattr(args, "expect_sha256", "") or "").strip().lower()
    if expected and expected != digest:
        # The source has changed since the caller last reviewed it. Refusing is
        # the point: a migration runner that applies whatever is on disk cannot
        # be part of a reproducible restoration procedure.
        _emit(
            {
                "command": "apply",
                "status": "refused",
                "reason": "source_digest_mismatch",
                "expected_sha256": expected,
                "actual_sha256": digest,
            },
            stream,
        )
        return EXIT_FAILED

    conn = open_migration_connection()
    applied_by = ""
    try:
        conn.autocommit = True
        absent = missing_roles(conn)
        if absent:
            _emit(
                {
                    "command": "apply",
                    "status": "refused",
                    "reason": "required_roles_absent",
                    "missing_roles": absent,
                },
                stream,
            )
            return EXIT_UNAVAILABLE

        applied_by = conn.execute("SELECT current_user").fetchone()[0]
        already = bool(_ledger_entries(conn))

        # ── The catalog contract as it stands *before* the script runs ──
        # A ledger entry means the migration committed here once, so the whole
        # foundation is present and any difference this run makes is repaired
        # drift rather than progress. The SQL's own preflight refuses the
        # tampering it can name — a rewritten guard body, a widened function
        # ACL, changed ownership — before `CREATE OR REPLACE FUNCTION` can undo
        # the evidence. This fingerprint closes the rest of the category: the
        # unconditional GRANT/REVOKE block would silently restore a *narrowed*
        # table ACL, the postflight would then pass, and the run would be
        # recorded `reapplied_noop`. Comparing the fingerprint afterwards turns
        # that claim into something checked rather than assumed.
        fingerprint_before = catalog_fingerprint(conn) if already else ""

        if getattr(args, "psql", False):
            psql_dsn = (
                os.environ.get("MAS_TELEMETRY_MIGRATION_DSN", "").strip()
                or os.environ.get("DATABASE_URL", "").strip()
            )
            if not psql_dsn:
                # Running psql against an empty DSN would silently fall back to
                # libpq defaults and apply the migration to whatever database
                # that resolves to. Refuse instead.
                raise MigrationError(
                    "--psql requires MAS_TELEMETRY_MIGRATION_DSN or DATABASE_URL"
                )
            _apply_with_psql(path, psql_dsn)
        else:
            # One execute of the whole script. The script carries its own
            # BEGIN/COMMIT, so any failure aborts and rolls back everything.
            conn.execute(path.read_text(encoding="utf-8"))

        problems = verify_contract(conn)
        if problems:
            raise MigrationError("postflight failed: " + "; ".join(problems[:10]))

        # `reapplied_noop` is a claim about what this run *did*, so it is
        # checked rather than inferred from the ledger: if the catalog contract
        # is not byte-identical to what it was before the script ran, this run
        # changed something and is not a no-op.
        if already:
            fingerprint_after = catalog_fingerprint(conn)
            if fingerprint_after != fingerprint_before:
                raise MigrationError(
                    "reapplication changed the catalog contract "
                    f"({fingerprint_before[:16]} -> {fingerprint_after[:16]}); "
                    "this is repaired drift, not a no-op"
                )

        ledger_id = str(uuid.uuid4())
        with _as_owner(conn):
            conn.execute(
                "INSERT INTO provider_telemetry_migration_ledger "
                "(ledger_id, migration_name, migration_sha256, schema_version, "
                " applied_at, applied_by, outcome) "
                "VALUES (%s::uuid, %s, %s, %s, %s, %s, %s)",
                (
                    ledger_id,
                    MIGRATION_NAME,
                    digest,
                    TELEMETRY_SCHEMA_VERSION,
                    _now(),
                    applied_by,
                    "reapplied_noop" if already else "applied",
                ),
            )
    except Exception as exc:
        _emit(
            {
                "command": "apply",
                "status": "failed",
                "migration": MIGRATION_NAME,
                "migration_sha256": digest,
                "error": f"{type(exc).__name__}: {str(exc)[:600]}",
            },
            stream,
        )
        return EXIT_FAILED
    finally:
        conn.close()

    # ── Success is only reportable from a *fresh* connection ──
    # If the transaction above rolled back, the ledger row is not there, and no
    # amount of apparent success earlier in this function can override that.
    confirm = open_migration_connection()
    try:
        confirm.autocommit = True
        entries = _ledger_entries(confirm)
        durable = [e for e in entries if e["migration_sha256"] == digest]
        problems = verify_contract(confirm)
        if not durable or problems:
            _emit(
                {
                    "command": "apply",
                    "status": "failed",
                    "reason": "not_durable_after_commit",
                    "ledger_rows_for_digest": len(durable),
                    "contract_problems": problems,
                },
                stream,
            )
            return EXIT_FAILED
        _emit(
            {
                "command": "apply",
                "status": "ok",
                "migration": MIGRATION_NAME,
                "migration_sha256": digest,
                "schema_version": TELEMETRY_SCHEMA_VERSION,
                "applied_by": applied_by,
                "outcome": durable[-1]["outcome"],
                "ledger_entries": len(entries),
                "contract_problems": [],
            },
            stream,
        )
        return EXIT_OK
    finally:
        confirm.close()


def cmd_verify(args, stream) -> int:
    conn = open_migration_connection()
    try:
        conn.autocommit = True
        problems = verify_contract(conn)
        _emit(
            {
                "command": "verify",
                "status": "ok" if not problems else "failed",
                "migration": MIGRATION_NAME,
                "migration_sha256": migration_sha256(),
                "contract_problems": problems,
                "ledger": _ledger_entries(conn),
            },
            stream,
        )
        return EXIT_OK if not problems else EXIT_FAILED
    finally:
        conn.close()


def cmd_bootstrap(args, stream) -> int:
    """Print the documented bootstrap/restoration ordering."""
    _emit(
        {
            "command": "bootstrap",
            "status": "ok",
            "order": list(BOOTSTRAP_ORDER),
            "telemetry_migration": MIGRATION_NAME,
            "telemetry_migration_sha256": migration_sha256(),
            "notes": [
                "v62 is permanently unused; see the header of the v63 source.",
                f"{MIGRATION_NAME} declares no foreign key, so it may be applied at "
                "any point after the base schema and may be restored on its own "
                "into an empty database.",
                f"{MIGRATION_NAME} requires the roles {', '.join(REQUIRED_ROLES)} to "
                "pre-exist; it creates no role and sets no password.",
                "Apply only through: python -m tools.provider_attempt_telemetry_migrate apply",
            ],
        },
        stream,
    )
    return EXIT_OK


COMMANDS = {
    "preflight": cmd_preflight,
    "apply": cmd_apply,
    "verify": cmd_verify,
    "bootstrap": cmd_bootstrap,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="provider_attempt_telemetry_migrate",
        description="Authoritative application path for the provider-telemetry schema.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("preflight", help="Report role, schema and ledger readiness.")
    apply_parser = subparsers.add_parser("apply", help="Apply the migration transactionally.")
    apply_parser.add_argument(
        "--expect-sha256", default="", help="Refuse to apply unless the source matches."
    )
    apply_parser.add_argument(
        "--psql",
        action="store_true",
        help="Apply via psql -v ON_ERROR_STOP=1 --single-transaction.",
    )
    subparsers.add_parser("verify", help="Verify the applied schema contract.")
    bootstrap = subparsers.add_parser("bootstrap", help="Print the bootstrap ordering.")
    bootstrap.add_argument("--print-order", action="store_true", default=True)
    return parser


def main(argv: Optional[list[str]] = None, stream=None) -> int:
    stream = stream or sys.stdout
    args = build_parser().parse_args(argv)
    handler = COMMANDS.get(args.command)
    if handler is None:  # pragma: no cover - argparse enforces the choice
        return EXIT_USAGE
    try:
        return handler(args, stream)
    except MigrationError as exc:
        _emit({"command": args.command, "status": "failed", "error": str(exc)[:600]}, stream)
        return EXIT_FAILED


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
