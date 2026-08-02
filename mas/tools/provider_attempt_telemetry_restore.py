"""Transactional restoration and restoration verification for telemetry exports.

Blocker 13's second half: the previous wave had "no safe supported
importer/restoration path". An export nobody can restore is not a freeze; it is a
file. This module is the supported path, and it refuses to write anything it has
not first proven.

Order of operations, and every step is a gate:

1. **Validate the artifact, before touching the database.** Unsupported export
   version, missing envelope fields, a recomputed digest that disagrees with the
   stored one, a column set that disagrees with this build's contract, or broken
   attempt/event chains — any of these stops the restore with nothing written.
2. **Verify the target schema.** Ownership, ACLs, the guard function's hardening
   and every append-only trigger's *enabled* state are checked before a row is
   inserted, so rows are never restored into a table that is no longer
   append-only.
3. **Restore in one transaction.** Identity columns are restored with
   ``OVERRIDING SYSTEM VALUE`` so the original ordering keys survive exactly;
   any failure rolls the whole thing back.
4. **Advance the sequences.** Each identity is restarted just past the highest
   restored key, so the next append cannot collide with a restored row.
5. **Verify what actually landed.** The restored rows are read back and digested
   again; a mismatch is a failure even though every earlier step passed.
6. **Prove the table still accepts an append.** A probe row is inserted and then
   rolled back to a savepoint — enough to prove privileges, constraints and
   triggers all still admit a new row, while leaving the restored data
   byte-identical to the artifact.

Nothing here masks a failure. Every command returns a nonzero exit and names the
problem; there is no path that reports success on a partial restore.

Usage::

    python -m tools.provider_attempt_telemetry_restore verify  --artifact freeze.json
    python -m tools.provider_attempt_telemetry_restore restore --artifact freeze.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from provider_telemetry import repository  # noqa: E402
from provider_telemetry.models import TELEMETRY_SCHEMA_VERSION  # noqa: E402
from tools import provider_attempt_telemetry_export as export_tool  # noqa: E402
from tools.provider_attempt_telemetry_migrate import (  # noqa: E402
    OWNER_ROLE,
    _as_owner,
    verify_contract,
)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2

# Columns that must be cast explicitly on the way back in. Everything else is
# handed to the driver as-is.
_UUID_COLUMNS = repository.UUID_COLUMNS
_TIMESTAMP_COLUMNS = frozenset(
    {"started_at", "observed_at", "request_started_at", "recorded_at", "applied_at"}
)
_ARRAY_COLUMNS = frozenset({"expected_phases"})


class RestoreError(RuntimeError):
    """The artifact could not be validated, or the restore could not be proven."""


def _open_restore_connection():
    import psycopg

    dsn = (
        os.environ.get("MAS_TELEMETRY_MIGRATION_DSN", "").strip()
        or os.environ.get("DATABASE_URL", "").strip()
    )
    if not dsn:
        raise RestoreError(
            "no restore DSN: set MAS_TELEMETRY_MIGRATION_DSN or DATABASE_URL"
        )
    return psycopg.connect(dsn)


open_restore_connection: Callable[[], Any] = _open_restore_connection


def _emit(payload: dict, stream) -> None:
    stream.write(json.dumps(payload, sort_keys=True, indent=2, default=str))
    stream.write("\n")


# ─────────────────────────── artifact validation ───────────────────────────


def validate_artifact(artifact: dict) -> list[str]:
    """Everything checkable about the artifact alone, before any database work."""
    problems: list[str] = []

    if artifact.get("export_format") != export_tool.EXPORT_FORMAT:
        problems.append(f"unsupported_format:{artifact.get('export_format')!r}")
    version = artifact.get("export_version")
    if version not in export_tool.SUPPORTED_EXPORT_VERSIONS:
        problems.append(f"unsupported_export_version:{version!r}")
    if artifact.get("schema_version") != TELEMETRY_SCHEMA_VERSION:
        problems.append(
            f"schema_version_mismatch:{artifact.get('schema_version')!r}"
            f"!={TELEMETRY_SCHEMA_VERSION}"
        )

    for field in ("selector", "columns", "rows", "relations", "chains",
                  "selector_bound_digest", "column_schema_digest", "complete"):
        if field not in artifact:
            problems.append(f"envelope_field_absent:{field}")
    if problems:
        return problems

    if artifact["column_schema_digest"] != export_tool.column_schema_digest():
        problems.append("column_schema_digest_mismatch")
    for table in repository.TELEMETRY_TABLES:
        expected = list(repository.READ_COLUMNS[table])
        if artifact["columns"].get(table) != expected:
            problems.append(f"column_contract_mismatch:{table}")
        if table not in artifact["rows"]:
            problems.append(f"rows_absent:{table}")

    # Recompute the selector-bound digest: a hand-edited artifact fails here.
    recomputed = export_tool.digest(
        {
            "selector": artifact["selector"],
            "export_version": artifact["export_version"],
            "schema_version": artifact["schema_version"],
            "column_schema_digest": artifact["column_schema_digest"],
            "rows": artifact["rows"],
        }
    )
    if recomputed != artifact["selector_bound_digest"]:
        problems.append("selector_bound_digest_mismatch")

    if not artifact.get("complete"):
        problems.append("artifact_incomplete")

    chains = export_tool.validate_chains(artifact["rows"])
    if not chains["complete"]:
        problems.extend(f"chain:{problem}" for problem in chains["problems"])

    # The same storage contract the source database enforced, re-applied before
    # a single row is written to the target. An artifact is a file: it can be
    # hand-edited, and a restore is precisely the path by which a value that no
    # writer could ever have produced would get into a database.
    values = export_tool.validate_values(artifact["rows"])
    if not values["complete"]:
        problems.extend(f"value:{problem}" for problem in values["problems"])

    return problems


# ─────────────────────────── restore ───────────────────────────


def _insert_sql(table: str, columns: list[str]) -> str:
    placeholders = []
    for column in columns:
        if column in _UUID_COLUMNS:
            placeholders.append("%s::uuid")
        elif column in _TIMESTAMP_COLUMNS:
            placeholders.append("%s::timestamptz")
        elif column in _ARRAY_COLUMNS:
            placeholders.append("%s::text[]")
        else:
            placeholders.append("%s")
    return (
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"OVERRIDING SYSTEM VALUE VALUES ({', '.join(placeholders)})"
    )


def _restore_rows(conn, artifact: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in repository.RESTORABLE_TABLES:
        columns = list(repository.READ_COLUMNS[table])
        rows = artifact["rows"][table]
        sql = _insert_sql(table, columns)
        for row in rows:
            conn.execute(sql, tuple(row[column] for column in columns))
        counts[table] = len(rows)
    return counts


def _advance_sequences(conn, artifact: dict) -> dict[str, Optional[int]]:
    """Restart each identity just past the highest restored key.

    Without this the next append would try to reuse a key a restored row already
    holds, and the unique constraint on the ordering column would reject it —
    a restored database that cannot be written to is not restored.
    """
    restarts: dict[str, Optional[int]] = {}
    for table in repository.RESTORABLE_TABLES:
        key = repository.KEYSET_COLUMN[table]
        row = conn.execute(f"SELECT max({key}) FROM {table}").fetchone()
        highest = row[0] if row and row[0] is not None else 0
        conn.execute(
            f'ALTER TABLE {table} ALTER COLUMN {key} RESTART WITH {int(highest) + 1}'
        )
        restarts[table] = int(highest) + 1
    return restarts


def _probe_append(conn, artifact: dict) -> str:
    """Prove a fresh append still succeeds, then leave no trace of the probe.

    The probe is rolled back to a savepoint on purpose: the restored data must
    remain byte-identical to the artifact, and a retained probe row would change
    both the row count and the artifact digest of any subsequent export.
    """
    runs = artifact["rows"][repository.RUN_TABLE]
    if not runs:
        return "skipped_no_run"
    conn.execute("SAVEPOINT restore_probe")
    try:
        conn.execute(
            "INSERT INTO provider_telemetry_run_event "
            "(event_id, telemetry_run_id, event_kind, worker_id, posture, observed_at) "
            "VALUES (%s::uuid, %s::uuid, 'worker_registered', 'restore-probe', %s, %s)",
            (
                str(uuid.uuid4()),
                runs[0]["telemetry_run_id"],
                runs[0]["posture"],
                datetime.now(timezone.utc),
            ),
        )
    except Exception as exc:
        conn.execute("ROLLBACK TO SAVEPOINT restore_probe")
        raise RestoreError(
            f"a fresh append failed after restoration: {type(exc).__name__}: "
            f"{str(exc)[:300]}"
        ) from exc
    conn.execute("ROLLBACK TO SAVEPOINT restore_probe")
    return "append_verified_and_rolled_back"


def restorable_digest(rows: dict) -> str:
    """A digest over exactly the rows a restore writes.

    The full selector-bound digest covers the migration ledger too, and the
    ledger is (correctly) not restored — so the comparison that proves a restore
    faithful is this one, over the restorable subset.
    """
    return export_tool.digest(
        {table: rows[table] for table in repository.RESTORABLE_TABLES}
    )


def _verify_restored(conn, artifact: dict) -> list[str]:
    """Read back what landed and digest it again against the artifact."""
    problems: list[str] = []
    selector = export_tool.Selector(**artifact["selector"])
    restored = export_tool.build_export(conn, selector)
    if restorable_digest(restored["rows"]) != restorable_digest(artifact["rows"]):
        problems.append("restored_digest_mismatch")
    for table in repository.RESTORABLE_TABLES:
        if len(restored["rows"][table]) != len(artifact["rows"][table]):
            problems.append(
                f"row_count_mismatch:{table}:"
                f"{len(restored['rows'][table])}!={len(artifact['rows'][table])}"
            )
    if not restored["chains"]["complete"]:
        problems.extend(f"restored_chain:{p}" for p in restored["chains"]["problems"])
    return problems


# ─────────────────────────── commands ───────────────────────────


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def cmd_verify(args, stream) -> int:
    artifact = _load(args.artifact)
    problems = validate_artifact(artifact)
    _emit(
        {
            "command": "verify",
            "status": "ok" if not problems else "failed",
            "artifact": args.artifact,
            "export_version": artifact.get("export_version"),
            "selector": artifact.get("selector"),
            "selector_bound_digest": artifact.get("selector_bound_digest"),
            "problems": problems,
        },
        stream,
    )
    return EXIT_OK if not problems else EXIT_FAILED


def cmd_restore(args, stream) -> int:
    artifact = _load(args.artifact)
    problems = validate_artifact(artifact)
    if problems:
        _emit(
            {
                "command": "restore",
                "status": "refused",
                "reason": "artifact_validation_failed",
                "problems": problems,
                "rows_written": 0,
            },
            stream,
        )
        return EXIT_FAILED

    conn = open_restore_connection()
    try:
        # The connection may already be inside a transaction (a caller that set
        # its search_path, for instance); psycopg refuses to toggle autocommit
        # there, and toggling it is not needed when it is already off.
        if conn.autocommit:
            conn.autocommit = False
        schema_problems = verify_contract(conn)
        if schema_problems:
            conn.rollback()
            _emit(
                {
                    "command": "restore",
                    "status": "refused",
                    "reason": "target_schema_contract_failed",
                    "problems": schema_problems,
                    "rows_written": 0,
                },
                stream,
            )
            return EXIT_FAILED

        conn.execute(f'SET LOCAL ROLE "{OWNER_ROLE}"')
        counts = _restore_rows(conn, artifact)
        restarts = _advance_sequences(conn, artifact)
        probe = _probe_append(conn, artifact)
        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:  # pragma: no cover - best effort
            pass
        conn.close()
        _emit(
            {
                "command": "restore",
                "status": "failed",
                "error": f"{type(exc).__name__}: {str(exc)[:600]}",
                "rows_written": 0,
            },
            stream,
        )
        return EXIT_FAILED

    # Post-restore verification runs on a *fresh* connection: it must observe
    # committed state, not the transaction that produced it.
    conn.close()
    confirm = open_restore_connection()
    try:
        # The read-back is an export, so it runs under the same guarantee every
        # export does: one read-only REPEATABLE READ snapshot for the whole
        # verification, rather than a fresh snapshot per statement. The opener
        # already issued statements, so the pinning helper commits that
        # transaction (never rolls it back — `SET search_path` is transactional
        # and a rollback would discard the caller's schema selection) before
        # assigning the transaction mode.
        #
        # The restore role holds no privilege on the telemetry relations — that
        # is the ownership model working — so the read-back assumes the owner
        # role it is a member of, exactly as the ledger access does.
        export_tool.prepare_snapshot_connection(confirm)
        with _as_owner(confirm):
            verification = _verify_restored(confirm, artifact)
    finally:
        try:
            confirm.rollback()
        except Exception:  # pragma: no cover - best effort
            pass
        confirm.close()

    payload = {
        "command": "restore",
        "artifact": args.artifact,
        "selector": artifact["selector"],
        "selector_bound_digest": artifact["selector_bound_digest"],
        "rows_written": counts,
        "sequences_restarted_at": restarts,
        "append_probe": probe,
        "verification_problems": verification,
        "status": "ok" if not verification else "failed",
    }
    _emit(payload, stream)
    return EXIT_OK if not verification else EXIT_FAILED


COMMANDS = {"verify": cmd_verify, "restore": cmd_restore}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="provider_attempt_telemetry_restore",
        description="Validate and transactionally restore a telemetry export artifact.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("verify", "Validate an artifact without touching a database."),
        ("restore", "Validate, then restore transactionally, then re-verify."),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--artifact", required=True, help="Path to the export JSON.")
    return parser


def main(argv: Optional[list[str]] = None, stream=None) -> int:
    stream = stream or sys.stdout
    args = build_parser().parse_args(argv)
    handler = COMMANDS.get(args.command)
    if handler is None:  # pragma: no cover - argparse enforces the choice
        return EXIT_USAGE
    try:
        return handler(args, stream)
    except RestoreError as exc:
        _emit({"command": args.command, "status": "failed", "error": str(exc)[:600]}, stream)
        return EXIT_FAILED


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
