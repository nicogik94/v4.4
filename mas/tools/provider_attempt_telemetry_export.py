"""Read-only export of provider-attempt telemetry, with completeness proof.

Blocker 13 of the audit: "Export silently truncates, does not validate chain
completeness, and has no safe supported importer/restoration path." The previous
exporter took ``LIMIT 10000`` and emitted a document that looked identical
whether it held every row or the first ten thousand of a million. An artifact
that cannot distinguish "complete" from "truncated" is not evidence.

This exporter fixes that in three ways:

1. **Keyset pagination, not LIMIT.** Every relation is read in ascending
   identity-sequence order, page by page, until the keyset is exhausted. The
   default therefore *is* complete. ``--max-rows`` with ``--on-overflow=fail``
   makes an overflow an explicit, loud failure; ``--on-overflow=truncate``
   emits ``complete: false`` with ``has_more: true`` and the exact cursor, so a
   truncated artifact can never be mistaken for a whole one.
2. **Chain validation.** Every attempt must sit under an exported invocation,
   every invocation under a call, every call under a run, and every start must
   have exactly one terminal event. ``--strict`` turns any break into a nonzero
   exit rather than a footnote.
3. **A real restoration path** — see ``tools/provider_attempt_telemetry_restore``,
   which validates this artifact before writing a single row.

The tool is structurally incapable of changing what it reads: the connection is
pinned ``read_only``, every command ends in ``rollback()``, and this file
contains no write SQL of any kind.

Usage::

    python -m tools.provider_attempt_telemetry_export preflight
    python -m tools.provider_attempt_telemetry_export export --external-run-id r-42
    python -m tools.provider_attempt_telemetry_export export --call-id <uuid> --strict
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Callable, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402
from provider_telemetry import redaction, repository  # noqa: E402
from provider_telemetry.models import TELEMETRY_SCHEMA_VERSION  # noqa: E402
from provider_telemetry.service import (  # noqa: E402
    OBSERVATIONAL_COMPLETENESS_NOTICE,
    STRICT_COMPLETENESS_NOTICE,
    configured_posture,
)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_UNAVAILABLE = 3
EXIT_INCOMPLETE = 4

EXPORT_FORMAT = "provider-attempt-telemetry-export"
EXPORT_VERSION = 2
SUPPORTED_EXPORT_VERSIONS = (2,)

DEFAULT_PAGE_SIZE = 1000
MAX_PAGE_SIZE = 10_000

ON_OVERFLOW_FAIL = "fail"
ON_OVERFLOW_TRUNCATE = "truncate"


def _open_authoritative_connection():
    """The only place this tool obtains a connection."""
    import psycopg

    dsn = (
        os.environ.get("MAS_TELEMETRY_READER_DSN", "").strip()
        or os.environ.get("DATABASE_URL", "").strip()
        or config.DATABASE_URL
    )
    return psycopg.connect(dsn)


open_telemetry_connection: Callable[[], Any] = _open_authoritative_connection


# ─────────────────────────── one snapshot per export ───────────────────────────
#
# READ COMMITTED gives every *statement* its own snapshot. An export issues one
# statement for the selector, one COUNT per relation and one SELECT per page, so
# a concurrent insert landing between any two of them produced an artifact whose
# counts, pages, relations and digest described different states of the database:
# a `total_matching` of 40 beside 41 exported rows, an event whose attempt is
# absent, `complete: true` over a set that was never simultaneously true.
#
# REPEATABLE READ fixes exactly that and nothing more. SERIALIZABLE would add
# predicate locking and serialization failures for no benefit here: the exporter
# only reads, the relations are append-only, and there is no read-write
# dependency for SERIALIZABLE to detect. Under REPEATABLE READ a read-only
# transaction takes one snapshot at its first query and every later statement
# sees precisely that.
EXPORT_ISOLATION_LEVEL = "repeatable read"


class ExportSnapshotLost(RuntimeError):
    """The transaction that took the export's snapshot did not survive it."""


def _pin(conn):
    """Assign the transaction mode every export read runs under.

    Only valid on an idle connection: psycopg refuses to assign these while a
    transaction is in progress, and PostgreSQL would ignore an isolation change
    made after a snapshot was already taken.
    """
    import psycopg

    conn.autocommit = False
    conn.read_only = True
    conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    return conn


def _configure_readonly_connection(conn):
    """Pin a freshly opened connection read-only and to one snapshot."""
    return _pin(conn)


def prepare_snapshot_connection(conn):
    """Put a caller-owned connection into the state an export read requires.

    Callers whose connection factory already issued statements — the restore
    tool's opener selects a schema and confirms its identity — arrive with a
    transaction open and a snapshot already taken at the wrong isolation level.
    That transaction has to end before the level can be reassigned.

    It ends with a **rollback**, because this tool never commits anything and
    that guarantee is worth keeping literally true rather than nearly true. The
    cost is that ``SET search_path`` is transactional and would be discarded
    with it, so the setting is read first and re-established afterwards through
    ``set_config`` — which takes the value as a parameter and therefore cannot
    be a quoting mistake.
    """
    import psycopg

    if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
        row = conn.execute("SELECT current_setting('search_path')").fetchone()
        search_path = row[0] if row else None
        conn.rollback()
        _pin(conn)
        if search_path:
            conn.execute(
                "SELECT pg_catalog.set_config('search_path', %s, false)",
                (search_path,),
            )
        return conn
    return _pin(conn)


_SNAPSHOT_SQL = (
    "SELECT current_setting('transaction_isolation'), "
    "       current_setting('transaction_read_only'), "
    "       pg_catalog.pg_current_snapshot()::text, "
    "       pg_catalog.now()"
)


def acquire_snapshot(conn) -> dict[str, Any]:
    """Take — and describe — the single snapshot this export reads from.

    Called before anything else touches the database, so this statement is what
    establishes the REPEATABLE READ snapshot. ``now()`` is the transaction's
    start time rather than a wall clock read later on, which is what makes
    ``snapshot_at`` a truthful statement about the data rather than about when
    the file happened to be written.

    An idle connection is pinned here. A connection whose transaction is
    already open — the restore tool's read-back has to ``SET ROLE`` first — must
    have been pinned by its owner with :func:`prepare_snapshot_connection`
    beforehand; if it was not, the isolation check below refuses rather than
    trying to change the rules of a transaction that has already begun.
    """
    import psycopg

    if conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE:
        try:
            _pin(conn)
        except Exception as exc:  # noqa: BLE001 - reported as a lost snapshot
            raise ExportSnapshotLost(
                "the export connection could not be pinned to one read-only "
                f"{EXPORT_ISOLATION_LEVEL} snapshot: {type(exc).__name__}"
            ) from exc

    row = conn.execute(_SNAPSHOT_SQL).fetchone()
    if row is None:  # pragma: no cover - a SELECT of constants always returns
        raise ExportSnapshotLost("the export connection returned no snapshot")
    isolation, read_only, snapshot, started_at = row
    if isolation != EXPORT_ISOLATION_LEVEL:
        raise ExportSnapshotLost(
            f"export requires {EXPORT_ISOLATION_LEVEL} isolation, got {isolation!r}"
        )
    if read_only != "on":
        raise ExportSnapshotLost("the export transaction is not read-only")
    return {
        "isolation_level": isolation,
        "read_only": True,
        "snapshot": snapshot,
        "snapshot_at": started_at,
    }


def verify_snapshot(conn, snapshot: dict[str, Any]) -> None:
    """Prove the export's transaction is the one that took the snapshot.

    A dropped and silently re-established connection, an implicit rollback, or
    anything else that ended the transaction starts a *new* one with a new
    snapshot and a new start time — so the rows already read describe a state
    the artifact can no longer claim to have observed as a whole. Detecting
    that is what stops a connection failure from producing an artifact that
    looks successful; a connection that is simply gone raises here instead.
    """
    row = conn.execute(_SNAPSHOT_SQL).fetchone()
    if row is None:  # pragma: no cover - a SELECT of constants always returns
        raise ExportSnapshotLost("the export connection returned no snapshot")
    isolation, read_only, current, started_at = row
    if (
        isolation != snapshot["isolation_level"]
        or read_only != "on"
        or current != snapshot["snapshot"]
        or started_at != snapshot["snapshot_at"]
    ):
        raise ExportSnapshotLost(
            "the export transaction did not survive the read; the rows already "
            "gathered no longer describe one coherent snapshot"
        )


def _safe_rollback_close(conn) -> None:
    try:
        conn.rollback()
    except Exception:  # pragma: no cover - best effort
        pass
    try:
        conn.close()
    except Exception:  # pragma: no cover - best effort
        pass


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
        default=_json_default,
    )


def digest(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _emit(payload: dict, stream) -> None:
    stream.write(json.dumps(payload, default=_json_default, sort_keys=True, indent=2))
    stream.write("\n")


# ─────────────────────────── selector ───────────────────────────


class Selector:
    """An exact, self-describing description of what an artifact covers.

    Recorded verbatim in the export and folded into the artifact digest, so two
    artifacts with the same digest necessarily answered the same question.
    """

    FIELDS = (
        "telemetry_run_id",
        "project_id",
        "external_project_id",
        "external_run_id",
        "job_id",
        "call_id",
        "worker_id",
    )

    def __init__(self, **values: Any) -> None:
        self.values = {
            name: str(values.get(name, "") or "").strip() for name in self.FIELDS
        }

    def __bool__(self) -> bool:
        return any(self.values.values())

    def as_payload(self) -> dict[str, str]:
        return dict(self.values)

    def get(self, name: str) -> str:
        return self.values.get(name, "")


def _resolve_run_ids(conn, selector: Selector) -> Optional[list[str]]:
    """Resolve the selector to the set of telemetry runs it covers.

    ``None`` means "every run", which is only reachable with an empty selector.
    """
    if selector.get("call_id"):
        rows = conn.execute(
            "SELECT DISTINCT telemetry_run_id FROM provider_telemetry_call "
            "WHERE call_id = %s::uuid",
            (selector.get("call_id"),),
        ).fetchall()
        return [str(row[0]) for row in rows]

    clauses: list[str] = []
    params: list[Any] = []
    if selector.get("telemetry_run_id"):
        clauses.append("telemetry_run_id = %s::uuid")
        params.append(selector.get("telemetry_run_id"))
    if selector.get("project_id"):
        clauses.append("project_id = %s::uuid")
        params.append(selector.get("project_id"))
    if selector.get("external_project_id"):
        clauses.append("external_project_id = %s")
        params.append(selector.get("external_project_id"))
    if selector.get("external_run_id"):
        clauses.append("external_run_id = %s")
        params.append(selector.get("external_run_id"))
    if selector.get("job_id"):
        clauses.append("job_id = %s")
        params.append(selector.get("job_id"))

    if clauses:
        sql = "SELECT telemetry_run_id FROM provider_telemetry_run WHERE " + " AND ".join(clauses)
        rows = conn.execute(sql, tuple(params)).fetchall()
        return [str(row[0]) for row in rows]

    if selector.get("worker_id"):
        rows = conn.execute(
            "SELECT DISTINCT telemetry_run_id FROM provider_attempt WHERE worker_id = %s",
            (selector.get("worker_id"),),
        ).fetchall()
        return [str(row[0]) for row in rows]

    return None


def _relation_filter(
    table: str, selector: Selector, run_ids: Optional[list[str]]
) -> tuple[list[str], list[Any]]:
    columns = set(repository.READ_COLUMNS[table])
    clauses: list[str] = []
    params: list[Any] = []
    if run_ids is not None and "telemetry_run_id" in columns:
        clauses.append("telemetry_run_id = ANY(%s::uuid[])")
        params.append(run_ids)
    if selector.get("call_id") and "call_id" in columns:
        clauses.append("call_id = %s::uuid")
        params.append(selector.get("call_id"))
    if selector.get("worker_id") and "worker_id" in columns:
        clauses.append("worker_id = %s")
        params.append(selector.get("worker_id"))
    return clauses, params


# ─────────────────────────── reading ───────────────────────────


def _count(conn, table: str, clauses: list[str], params: list[Any]) -> int:
    sql = repository.count_sql(table, where=clauses)
    row = conn.execute(sql, tuple(params)).fetchone()
    return int(row[0]) if row else 0


def _read_relation(
    conn,
    table: str,
    clauses: list[str],
    params: list[Any],
    *,
    page_size: int,
    max_rows: Optional[int],
) -> tuple[list[dict], bool, Optional[int], Optional[int]]:
    """Read one relation by keyset, page by page.

    Returns ``(rows, has_more, first_key, last_key)``. ``has_more`` can only be
    True when ``max_rows`` cut the read short — exhausting the keyset always
    yields False, which is what makes "complete" a fact rather than a hope.
    """
    key = repository.KEYSET_COLUMN[table]
    columns = ", ".join(repository.READ_COLUMNS[table])
    rows: list[dict] = []
    cursor: Optional[int] = None
    has_more = False

    while True:
        page_clauses = list(clauses)
        page_params = list(params)
        if cursor is not None:
            page_clauses.append(f"{key} > %s")
            page_params.append(cursor)
        sql = f"SELECT {columns} FROM {table}"
        if page_clauses:
            sql += " WHERE " + " AND ".join(page_clauses)
        sql += f" ORDER BY {key} ASC LIMIT %s"
        page_params.append(page_size)

        result = conn.execute(sql, tuple(page_params))
        names = [description[0] for description in result.description]
        page = [dict(zip(names, raw)) for raw in result.fetchall()]
        if not page:
            break

        for raw in page:
            if max_rows is not None and len(rows) >= max_rows:
                has_more = True
                break
            rows.append(repository.row_to_export_dict(table, raw))
        cursor = page[-1][key]
        if has_more or len(page) < page_size:
            break

    first_key = rows[0][key] if rows else None
    last_key = rows[-1][key] if rows else None
    return rows, has_more, first_key, last_key


# ─────────────────────────── chain validation ───────────────────────────


# The stored columns that carry provider-supplied text, and the validator each
# one's value had to pass on the way in. Restating the mapping here is what lets
# an artifact be checked against the same contract the writer applied, without
# the reader having to trust that it was.
PROVIDER_TEXT_COLUMNS: dict[str, Any] = {
    "provider_response_id": redaction.provider_response_id,
    "provider_request_id": redaction.provider_request_id,
    "effective_model": redaction.provider_model,
    "stop_reason": redaction.stop_reason,
    "retry_after": redaction.retry_after,
}


def validate_values(relations: dict[str, list[dict]]) -> dict[str, Any]:
    """Re-apply the storage contract to every provider-sourced value present.

    An export is the point where telemetry leaves the database that constrained
    it, and a restore is where it re-enters one. Checking here means a value
    that should never have been stored — a credential in a column typed as an
    identifier, a value present without a ``valid`` status — is named by the
    artifact rather than carried quietly into the next database.

    Diagnostics carry the column and the row's identity, never the value.
    """
    problems: list[str] = []
    checked = 0
    for row in relations.get(repository.EVENT_TABLE, ()):
        for column, validator in PROVIDER_TEXT_COLUMNS.items():
            value = row.get(column)
            status = row.get(f"{column}_status")
            if value is None:
                if status == "valid":
                    problems.append(f"valid_status_without_value:{column}:{row.get('event_id')}")
                continue
            checked += 1
            if status != "valid":
                problems.append(f"value_without_valid_status:{column}:{row.get('event_id')}")
            result = validator(value)
            if not result.is_valid or result.value != value:
                # `result.status` is this package's own vocabulary — `redacted`,
                # `invalid`, `unknown_value` — so it is safe to report; the
                # offending value is not, and is never included.
                problems.append(
                    f"unsafe_stored_value:{column}:{result.status}:{row.get('event_id')}"
                )
    return {
        "checked_values": checked,
        "problems": sorted(set(problems)),
        "complete": not problems,
    }


def validate_chains(relations: dict[str, list[dict]]) -> dict[str, Any]:
    """Prove that every exported row sits in a complete lineage.

    A break is never repaired and never omitted: it is named, counted, and
    reported. In ``--strict`` mode any break fails the export outright, because
    an artifact with a dangling attempt cannot support a claim about what a run
    did.
    """
    runs = {row["telemetry_run_id"] for row in relations[repository.RUN_TABLE]}
    calls = {row["call_id"] for row in relations[repository.CALL_TABLE]}
    invocations = {
        row["invocation_id"] for row in relations[repository.INVOCATION_TABLE]
    }
    attempts = {row["attempt_id"] for row in relations[repository.ATTEMPT_TABLE]}

    events = relations[repository.EVENT_TABLE]
    terminal_by_subject: dict[str, int] = {}
    for event in events:
        if event["is_terminal"]:
            subject = event["subject_id"]
            terminal_by_subject[subject] = terminal_by_subject.get(subject, 0) + 1

    problems: list[str] = []

    orphan_calls = [
        row["call_id"]
        for row in relations[repository.CALL_TABLE]
        if row["telemetry_run_id"] not in runs
    ]
    orphan_invocations = [
        row["invocation_id"]
        for row in relations[repository.INVOCATION_TABLE]
        if row["call_id"] not in calls
    ]
    orphan_attempts = [
        row["attempt_id"]
        for row in relations[repository.ATTEMPT_TABLE]
        if row["invocation_id"] not in invocations
    ]
    orphan_events = [
        row["event_id"]
        for row in events
        if row["subject_id"] not in attempts and row["subject_id"] not in invocations
    ]

    unmatched_attempts = [
        row["attempt_id"]
        for row in relations[repository.ATTEMPT_TABLE]
        if terminal_by_subject.get(row["attempt_id"], 0) == 0
    ]
    unmatched_invocations = [
        row["invocation_id"]
        for row in relations[repository.INVOCATION_TABLE]
        if terminal_by_subject.get(row["invocation_id"], 0) == 0
    ]
    duplicate_terminals = [
        subject for subject, count in terminal_by_subject.items() if count > 1
    ]

    for name, values in (
        ("orphan_calls", orphan_calls),
        ("orphan_invocations", orphan_invocations),
        ("orphan_attempts", orphan_attempts),
        ("orphan_events", orphan_events),
        ("unmatched_attempt_starts", unmatched_attempts),
        ("unmatched_invocation_starts", unmatched_invocations),
        ("duplicate_terminal_events", duplicate_terminals),
    ):
        if values:
            problems.append(f"{name}={len(values)}")

    # The HTTP-attempt/retry relationship is *derived*, never stored: within one
    # SDK invocation the attempts are ordered by http_retry_ordinal, so every
    # attempt but the highest was superseded by a retry and the highest is final
    # for that invocation. Deriving it is exact; storing it would require
    # guessing at the time of writing whether another retry would follow.
    per_invocation: dict[str, list[dict]] = {}
    for row in relations[repository.ATTEMPT_TABLE]:
        per_invocation.setdefault(row["invocation_id"], []).append(row)
    retry_relationships: dict[str, str] = {}
    for invocation_id, rows in per_invocation.items():
        ordered = sorted(rows, key=lambda r: r["http_retry_ordinal"])
        for row in ordered[:-1]:
            retry_relationships[row["attempt_id"]] = "superseded_by_retry"
        if ordered:
            retry_relationships[ordered[-1]["attempt_id"]] = "final_for_invocation"

    return {
        "complete": not problems,
        "problems": problems,
        "counts": {
            "runs": len(runs),
            "calls": len(calls),
            "invocations": len(invocations),
            "http_attempts": len(attempts),
            "events": len(events),
            "terminal_events": sum(terminal_by_subject.values()),
        },
        "orphan_calls": orphan_calls[:50],
        "orphan_invocations": orphan_invocations[:50],
        "orphan_attempts": orphan_attempts[:50],
        "orphan_events": orphan_events[:50],
        "unmatched_attempt_starts": unmatched_attempts[:50],
        "unmatched_invocation_starts": unmatched_invocations[:50],
        "duplicate_terminal_events": duplicate_terminals[:50],
        "http_retry_relationship": retry_relationships,
    }


def reconciliation_summary(relations: dict[str, list[dict]]) -> dict[str, Any]:
    """The run-end reconciliation as the database recorded it."""
    events = [
        row
        for row in relations[repository.RUN_EVENT_TABLE]
        if row["event_kind"] == "reconciliation"
    ]
    if not events:
        return {"present": False, "status": "absent", "runs": []}
    latest: dict[str, dict] = {}
    for row in sorted(events, key=lambda r: r["run_event_sequence"]):
        latest[row["telemetry_run_id"]] = row
    statuses = {row["reconciliation_status"] for row in latest.values()}
    return {
        "present": True,
        "status": "complete" if statuses == {"complete"} else "not_complete",
        "runs": [
            {
                "telemetry_run_id": run_id,
                "reconciliation_status": row["reconciliation_status"],
                "drain_status": row["drain_status"],
                "unmatched_starts": row["unmatched_starts"],
                "undurable_events": row["undurable_events"],
                "ambiguous_events": row["ambiguous_events"],
                "dropped_events": row["dropped_events"],
                "expected_calls": row["expected_calls"],
                "observed_calls": row["observed_calls"],
                "detail": row["detail"],
            }
            for run_id, row in sorted(latest.items())
        ],
    }


def column_schema_digest() -> str:
    """A digest over the exact column contract this artifact was read through."""
    return digest(
        {
            table: list(repository.READ_COLUMNS[table])
            for table in repository.TELEMETRY_TABLES
        }
    )


# ─────────────────────────── commands ───────────────────────────


def _selector_from_args(args) -> Selector:
    return Selector(
        telemetry_run_id=getattr(args, "telemetry_run_id", ""),
        project_id=getattr(args, "project_id", ""),
        external_project_id=getattr(args, "external_project_id", ""),
        external_run_id=getattr(args, "external_run_id", ""),
        job_id=getattr(args, "job_id", ""),
        call_id=getattr(args, "call_id", ""),
        worker_id=getattr(args, "worker_id", ""),
    )


def cmd_preflight(args, stream) -> int:
    posture = configured_posture()
    database_url_configured = bool(
        os.getenv("DATABASE_URL", "").strip()
        or os.getenv("MAS_TELEMETRY_READER_DSN", "").strip()
    )
    payload = {
        "command": "preflight",
        "status": "ok",
        "read_only": True,
        "committed": False,
        "isolation_level": EXPORT_ISOLATION_LEVEL,
        "posture": posture,
        "database_configured": database_url_configured,
        "export_format": EXPORT_FORMAT,
        "export_version": EXPORT_VERSION,
        "schema_version": TELEMETRY_SCHEMA_VERSION,
        "completeness_notice": (
            STRICT_COMPLETENESS_NOTICE if posture == "strict"
            else OBSERVATIONAL_COMPLETENESS_NOTICE
        ),
    }
    if not database_url_configured:
        payload["status"] = "unavailable"
        payload["diagnostic"] = "no database is configured for the export path"
        _emit(payload, stream)
        return EXIT_UNAVAILABLE

    conn = _configure_readonly_connection(open_telemetry_connection())
    try:
        present = []
        missing = []
        for table in repository.TELEMETRY_TABLES:
            row = conn.execute(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n "
                "ON n.oid = c.relnamespace WHERE n.nspname = current_schema() "
                "AND c.relkind = 'r' AND c.relname = %s",
                (table,),
            ).fetchone()
            (present if row and row[0] else missing).append(table)
        payload["tables_present"] = present
        payload["tables_missing"] = missing
        if missing:
            payload["status"] = "unavailable"
            payload["diagnostic"] = (
                "apply sql/v63_provider_attempt_telemetry_foundation.sql via "
                "python -m tools.provider_attempt_telemetry_migrate apply"
            )
            _emit(payload, stream)
            return EXIT_UNAVAILABLE
        _emit(payload, stream)
        return EXIT_OK
    finally:
        _safe_rollback_close(conn)


def build_export(
    conn,
    selector: Selector,
    *,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_rows: Optional[int] = None,
    on_overflow: str = ON_OVERFLOW_TRUNCATE,
) -> dict[str, Any]:
    # Everything from here to verify_snapshot() below — selector resolution,
    # every COUNT, every page of every relation, and therefore every digest
    # input — runs inside one transaction on one connection against one
    # snapshot. Keyset pagination does not open a new one: the pages are further
    # statements in the same transaction.
    snapshot = acquire_snapshot(conn)
    run_ids = _resolve_run_ids(conn, selector)

    relations: dict[str, list[dict]] = {}
    relation_meta: dict[str, dict[str, Any]] = {}
    overflowed: list[str] = []

    for table in repository.TELEMETRY_TABLES:
        clauses, params = _relation_filter(table, selector, run_ids)
        total = _count(conn, table, clauses, params)
        rows, has_more, first_key, last_key = _read_relation(
            conn, table, clauses, params, page_size=page_size, max_rows=max_rows
        )
        relations[table] = rows
        relation_meta[table] = {
            "total_matching": total,
            "exported": len(rows),
            "has_more": has_more or len(rows) < total,
            "complete": (not has_more) and len(rows) == total,
            "keyset_column": repository.KEYSET_COLUMN[table],
            "first_key": first_key,
            "last_key": last_key,
        }
        if not relation_meta[table]["complete"]:
            overflowed.append(table)

    # Read before any claim is made about the rows: if the transaction ended,
    # this raises and no artifact is produced at all.
    verify_snapshot(conn, snapshot)

    chains = validate_chains(relations)
    values = validate_values(relations)
    reconciliation = reconciliation_summary(relations)
    complete = not overflowed and chains["complete"]

    postures = {row["posture"] for row in relations[repository.RUN_TABLE]}
    notice = (
        STRICT_COMPLETENESS_NOTICE
        if postures == {"strict"}
        else OBSERVATIONAL_COMPLETENESS_NOTICE
    )

    payload: dict[str, Any] = {
        "export_format": EXPORT_FORMAT,
        "export_version": EXPORT_VERSION,
        "schema_version": TELEMETRY_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        # What the rows describe, and when. `exported_at` is the snapshot's own
        # start time, not the moment this document was serialized: the two can
        # differ by the whole duration of a large export, and only the first is
        # a true statement about the data.
        "exported_at": snapshot["snapshot_at"],
        "transaction": {
            "isolation_level": snapshot["isolation_level"],
            "read_only": snapshot["read_only"],
            "snapshot": snapshot["snapshot"],
            "single_snapshot": True,
        },
        "selector": selector.as_payload(),
        "selected_run_ids": sorted(run_ids) if run_ids is not None else None,
        "columns": {
            table: list(repository.READ_COLUMNS[table])
            for table in repository.TELEMETRY_TABLES
        },
        "column_schema_digest": column_schema_digest(),
        "relations": relation_meta,
        "complete": complete,
        "overflowed_relations": overflowed,
        "on_overflow": on_overflow,
        "chains": chains,
        "values": values,
        "reconciliation": reconciliation,
        "completeness_notice": notice,
        "rows": relations,
    }
    # The digest binds the selector to the rows: an artifact cannot be re-labelled
    # as covering a different question without changing its digest.
    payload["selector_bound_digest"] = digest(
        {
            "selector": payload["selector"],
            "export_version": EXPORT_VERSION,
            "schema_version": TELEMETRY_SCHEMA_VERSION,
            "column_schema_digest": payload["column_schema_digest"],
            "rows": relations,
        }
    )
    return payload


def cmd_export(args, stream) -> int:
    selector = _selector_from_args(args)
    page_size = max(1, min(int(getattr(args, "page_size", DEFAULT_PAGE_SIZE)), MAX_PAGE_SIZE))
    raw_max = getattr(args, "max_rows", 0)
    max_rows = int(raw_max) if raw_max else None
    on_overflow = getattr(args, "on_overflow", ON_OVERFLOW_TRUNCATE)
    strict = bool(getattr(args, "strict", False))

    conn = _configure_readonly_connection(open_telemetry_connection())
    try:
        payload = build_export(
            conn,
            selector,
            page_size=page_size,
            max_rows=max_rows,
            on_overflow=on_overflow,
        )
    except ExportSnapshotLost as exc:
        # No artifact at all. A partial read whose transaction ended cannot be
        # downgraded to "incomplete" — incompleteness is a statement about a
        # snapshot, and this is the case where there is no longer one.
        _emit(
            {
                "command": "export",
                "status": "failed",
                "read_only": True,
                "committed": False,
                "diagnostic": f"export snapshot was lost: {exc}",
                "selector": selector.as_payload(),
            },
            stream,
        )
        return EXIT_FAILED
    finally:
        # Guaranteed on every path, including the raise above: the read-only
        # transaction is rolled back and the connection closed.
        _safe_rollback_close(conn)

    payload["read_only"] = True
    payload["committed"] = False

    if not payload["complete"] and on_overflow == ON_OVERFLOW_FAIL:
        payload["status"] = "failed"
        payload["diagnostic"] = (
            "the selector matches more rows than --max-rows permits and "
            "--on-overflow=fail was requested; the artifact is NOT complete"
        )
        _emit(payload, stream)
        return EXIT_FAILED

    if strict and not payload["chains"]["complete"]:
        payload["status"] = "failed"
        payload["diagnostic"] = (
            "strict export refused: attempt/event chains are incomplete "
            + ", ".join(payload["chains"]["problems"])
        )
        _emit(payload, stream)
        return EXIT_INCOMPLETE

    # A value that should never have been storable is a defect in the artifact
    # whether or not the lineage is intact, and a strict export is exactly the
    # artifact nobody may quietly carry it into.
    if strict and not payload["values"]["complete"]:
        payload["status"] = "failed"
        payload["diagnostic"] = (
            "strict export refused: stored provider values violate the storage "
            "contract: " + ", ".join(payload["values"]["problems"][:10])
        )
        _emit(payload, stream)
        return EXIT_INCOMPLETE

    payload["status"] = "ok" if payload["complete"] else "incomplete"
    _emit(payload, stream)
    return EXIT_OK if payload["complete"] else EXIT_INCOMPLETE


def cmd_list(args, stream) -> int:
    """A compact listing: counts and keys, without the row bodies."""
    selector = _selector_from_args(args)
    conn = _configure_readonly_connection(open_telemetry_connection())
    try:
        # One snapshot here too: a listing whose per-relation counts came from
        # different snapshots would report a set of numbers that never held at
        # the same time.
        snapshot = acquire_snapshot(conn)
        run_ids = _resolve_run_ids(conn, selector)
        summary = {}
        for table in repository.TELEMETRY_TABLES:
            clauses, params = _relation_filter(table, selector, run_ids)
            summary[table] = _count(conn, table, clauses, params)
        verify_snapshot(conn, snapshot)
    except ExportSnapshotLost as exc:
        _emit(
            {
                "command": "list",
                "status": "failed",
                "read_only": True,
                "committed": False,
                "diagnostic": f"export snapshot was lost: {exc}",
                "selector": selector.as_payload(),
            },
            stream,
        )
        return EXIT_FAILED
    finally:
        _safe_rollback_close(conn)
    _emit(
        {
            "command": "list",
            "status": "ok",
            "read_only": True,
            "committed": False,
            "exported_at": snapshot["snapshot_at"],
            "transaction": {
                "isolation_level": snapshot["isolation_level"],
                "read_only": snapshot["read_only"],
                "snapshot": snapshot["snapshot"],
                "single_snapshot": True,
            },
            "selector": selector.as_payload(),
            "selected_run_ids": sorted(run_ids) if run_ids is not None else None,
            "row_counts": summary,
        },
        stream,
    )
    return EXIT_OK


COMMANDS = {
    "preflight": cmd_preflight,
    "list": cmd_list,
    "export": cmd_export,
}


def _add_selector_arguments(parser) -> None:
    parser.add_argument("--telemetry-run-id", default="", help="Exact telemetry run UUID.")
    parser.add_argument("--project-id", default="", help="Exact relational project UUID.")
    parser.add_argument(
        "--external-project-id", default="", help="Exact non-UUID project identity."
    )
    parser.add_argument("--external-run-id", default="", help="Exact workflow run identity.")
    parser.add_argument("--job-id", default="", help="Exact job identity.")
    parser.add_argument("--call-id", default="", help="Exact model-call UUID.")
    parser.add_argument("--worker-id", default="", help="Exact worker/process identity.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="provider_attempt_telemetry_export",
        description=(
            "Read-only export of provider-attempt telemetry. Reads by keyset to "
            "completion by default; never writes; every command rolls back."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("preflight", help="Report posture, database and schema readiness.")

    listing = subparsers.add_parser("list", help="Row counts for a selector.")
    _add_selector_arguments(listing)

    export = subparsers.add_parser("export", help="Emit a complete, digest-stamped artifact.")
    _add_selector_arguments(export)
    export.add_argument(
        "--page-size", type=int, default=DEFAULT_PAGE_SIZE,
        help=f"Keyset page size (1..{MAX_PAGE_SIZE}).",
    )
    export.add_argument(
        "--max-rows", type=int, default=0,
        help="Cap rows per relation. 0 (default) reads every matching row.",
    )
    export.add_argument(
        "--on-overflow", choices=(ON_OVERFLOW_FAIL, ON_OVERFLOW_TRUNCATE),
        default=ON_OVERFLOW_TRUNCATE,
        help="Behavior when --max-rows is exceeded.",
    )
    export.add_argument(
        "--strict", action="store_true",
        help="Refuse to emit an artifact whose attempt/event chains are broken.",
    )
    return parser


def main(argv: Optional[list[str]] = None, stream=None) -> int:
    stream = stream or sys.stdout
    args = build_parser().parse_args(argv)
    handler = COMMANDS.get(args.command)
    if handler is None:  # pragma: no cover - argparse enforces the choice
        return EXIT_USAGE
    return handler(args, stream)


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
