"""Append-only persistence, and the exact column contract shared with export.

Six append-only relations, no ``UPDATE`` statement anywhere in this package, and
one column tuple per relation that the writer, the reader, the exporter and the
importer all consume — so the four can never drift apart.

The write path holds no lock on anything a run writes: the telemetry relations
declare no foreign key to application tables, so a telemetry statement can never
block a Decision Engine write, can never fail because a parent row is missing,
and the whole set is restorable on its own into an empty database.

Write results are classified into three states, not two. ``failed`` means the
server rejected the statement and the row definitively does not exist;
``ambiguous`` means the connection failed at a point where the row may or may
not have committed. Blocker "any ambiguous database-write result makes the run
uncertified" is only expressible because the distinction is made here, at the
one place that can still see the exception type.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Protocol

from .delivery import AmbiguousWrite
from .models import (
    OBSERVABLE_PROVIDER_FIELDS,
    AttemptEvent,
    HttpAttemptRecord,
    RunEvent,
    SdkInvocationRecord,
    TelemetryCallRecord,
    TelemetryRunRecord,
)

# ─────────────────────────── relations ───────────────────────────

RUN_TABLE = "provider_telemetry_run"
RUN_EVENT_TABLE = "provider_telemetry_run_event"
CALL_TABLE = "provider_telemetry_call"
INVOCATION_TABLE = "provider_sdk_invocation"
ATTEMPT_TABLE = "provider_attempt"
EVENT_TABLE = "provider_attempt_event"
LEDGER_TABLE = "provider_telemetry_migration_ledger"

TELEMETRY_TABLES = (
    RUN_TABLE,
    RUN_EVENT_TABLE,
    CALL_TABLE,
    INVOCATION_TABLE,
    ATTEMPT_TABLE,
    EVENT_TABLE,
    LEDGER_TABLE,
)

# Relations that carry an append-only guard trigger pair (UPDATE/DELETE and
# TRUNCATE). The ledger is included: a migration history that can be rewritten
# proves nothing.
APPEND_ONLY_TABLES = TELEMETRY_TABLES

# Relations a restore writes. The migration ledger is deliberately excluded: it
# records how *this* database's schema came to exist, not what a run did. A
# target database already has its own ledger describing its own application, and
# overwriting it with the source's history would replace a true statement with a
# false one (and collide on the ledger's own identity besides). The ledger is
# still carried in an export as provenance — it is read, never restored.
RESTORABLE_TABLES = tuple(t for t in TELEMETRY_TABLES if t != LEDGER_TABLE)

RUN_COLUMNS: tuple[str, ...] = (
    "telemetry_run_id",
    "posture",
    "telemetry_required",
    "entry_point",
    "project_id",
    "external_project_id",
    "external_run_id",
    "job_id",
    "source_commit",
    "schema_version",
    "runtime_fingerprint",
    "expected_phases",
    "started_at",
)

RUN_EVENT_COLUMNS: tuple[str, ...] = (
    "event_id",
    "telemetry_run_id",
    "event_kind",
    "worker_id",
    "posture",
    "observed_at",
    "started_events",
    "terminal_events",
    "unmatched_starts",
    "undurable_events",
    "ambiguous_events",
    "dropped_events",
    "expected_calls",
    "observed_calls",
    "drain_status",
    "reconciliation_status",
    "expected_work_digest",
    "detail",
)

CALL_COLUMNS: tuple[str, ...] = (
    "call_id",
    "telemetry_run_id",
    "posture",
    "entry_point",
    "project_id",
    "external_project_id",
    "external_run_id",
    "job_id",
    "phase",
    "worker_id",
    "requested_provider",
    "requested_model",
    "request_config_fingerprint",
    "routing_decision_fingerprint",
    "candidate_count",
    "started_at",
)

INVOCATION_COLUMNS: tuple[str, ...] = (
    "invocation_id",
    "call_id",
    "telemetry_run_id",
    "posture",
    "entry_point",
    "project_id",
    "external_project_id",
    "external_run_id",
    "job_id",
    "phase",
    "worker_id",
    "invocation_kind",
    "provider",
    "requested_model",
    "candidate_ordinal",
    "retry_ordinal",
    "attempt_ordinal",
    "breaker_state_before",
    "breaker_failure_count_before",
    "breaker_snapshot_status_before",
    "fallback_candidate",
    "fallback_from_provider",
    "fallback_from_model",
    "request_config_fingerprint",
    "routing_decision_fingerprint",
    "started_at",
)

ATTEMPT_COLUMNS: tuple[str, ...] = (
    "attempt_id",
    "invocation_id",
    "call_id",
    "telemetry_run_id",
    "posture",
    "worker_id",
    "provider",
    "requested_model",
    "http_retry_ordinal",
    "request_method",
    "request_path",
    "request_started_at",
)

EVENT_COLUMNS: tuple[str, ...] = (
    "event_id",
    "subject_kind",
    "subject_id",
    "call_id",
    "telemetry_run_id",
    "event_kind",
    "event_ordinal",
    "is_terminal",
    "observed_at",
    "worker_id",
    "transport_outcome",
    "http_status",
    "http_status_status",
    "provider_request_id",
    "provider_request_id_status",
    "retry_after",
    "retry_after_status",
    "provider_response_id",
    "provider_response_id_status",
    "effective_model",
    "effective_model_status",
    "stop_reason",
    "stop_reason_status",
    "input_tokens",
    "input_tokens_status",
    "output_tokens",
    "output_tokens_status",
    "cache_read_tokens",
    "cache_read_tokens_status",
    "cache_creation_tokens",
    "cache_creation_tokens_status",
    "breaker_state_after",
    "breaker_failure_count_after",
    "breaker_snapshot_status_after",
    "error_category",
    "error_identity",
    "failure_class",
    "value_details",
    "response_metadata_fingerprint",
    "schema_version",
)

LEDGER_COLUMNS: tuple[str, ...] = (
    "ledger_id",
    "migration_name",
    "migration_sha256",
    "schema_version",
    "applied_at",
    "applied_by",
    "outcome",
)

# Columns the database assigns. Readers select them; writers never supply them.
DATABASE_ASSIGNED: dict[str, tuple[str, ...]] = {
    RUN_TABLE: ("run_sequence", "recorded_at"),
    RUN_EVENT_TABLE: ("run_event_sequence", "recorded_at"),
    CALL_TABLE: ("call_sequence", "recorded_at"),
    INVOCATION_TABLE: ("invocation_sequence", "recorded_at"),
    ATTEMPT_TABLE: ("attempt_sequence", "recorded_at"),
    EVENT_TABLE: ("event_sequence", "recorded_at"),
    LEDGER_TABLE: ("ledger_sequence",),
}

WRITE_COLUMNS: dict[str, tuple[str, ...]] = {
    RUN_TABLE: RUN_COLUMNS,
    RUN_EVENT_TABLE: RUN_EVENT_COLUMNS,
    CALL_TABLE: CALL_COLUMNS,
    INVOCATION_TABLE: INVOCATION_COLUMNS,
    ATTEMPT_TABLE: ATTEMPT_COLUMNS,
    EVENT_TABLE: EVENT_COLUMNS,
    LEDGER_TABLE: LEDGER_COLUMNS,
}

READ_COLUMNS: dict[str, tuple[str, ...]] = {
    table: DATABASE_ASSIGNED[table] + WRITE_COLUMNS[table] for table in TELEMETRY_TABLES
}

# Columns typed UUID in PostgreSQL. Listed explicitly so a string identifier is
# cast deliberately rather than by accident, and so a non-UUID identity can
# never reach one of them (see identity.as_project_uuid).
UUID_COLUMNS = frozenset(
    {
        "telemetry_run_id",
        "call_id",
        "invocation_id",
        "attempt_id",
        "event_id",
        "subject_id",
        "project_id",
        "ledger_id",
    }
)

# The keyset each relation is paginated by. Chosen so the order is total and
# stable across a restore: the identity sequence is monotone per relation.
KEYSET_COLUMN: dict[str, str] = {
    RUN_TABLE: "run_sequence",
    RUN_EVENT_TABLE: "run_event_sequence",
    CALL_TABLE: "call_sequence",
    INVOCATION_TABLE: "invocation_sequence",
    ATTEMPT_TABLE: "attempt_sequence",
    EVENT_TABLE: "event_sequence",
    LEDGER_TABLE: "ledger_sequence",
}


class ProviderTelemetryStorageUnavailable(RuntimeError):
    """Durable storage was not reachable."""


def insert_sql(table: str, *, placeholder: str = "asyncpg") -> str:
    columns = WRITE_COLUMNS[table]
    if placeholder == "asyncpg":
        values = ", ".join(
            f"${index}::uuid" if column in UUID_COLUMNS else f"${index}"
            for index, column in enumerate(columns, start=1)
        )
    else:
        values = ", ".join(
            "%s::uuid" if column in UUID_COLUMNS else "%s" for column in columns
        )
    return f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({values})"


# ─────────────────────────── row construction ───────────────────────────


def _identity_columns(identity: Any) -> dict[str, Any]:
    return {
        "entry_point": identity.entry_point,
        "project_id": identity.project_uuid,
        "external_project_id": identity.external_project_id,
        "external_run_id": identity.run_id,
        "job_id": identity.job_id,
        "phase": identity.phase,
    }


def run_row(record: TelemetryRunRecord) -> tuple[Any, ...]:
    values = {
        "telemetry_run_id": record.telemetry_run_id,
        "posture": record.posture,
        "telemetry_required": record.telemetry_required,
        "entry_point": record.entry_point,
        "project_id": record.project_uuid,
        "external_project_id": record.external_project_id,
        "external_run_id": record.external_run_id,
        "job_id": record.job_id,
        "source_commit": record.source_commit,
        "schema_version": record.schema_version,
        "runtime_fingerprint": record.runtime_fingerprint,
        "expected_phases": list(record.expected_phases),
        "started_at": record.started_at,
    }
    return tuple(values[column] for column in RUN_COLUMNS)


def run_event_row(event: RunEvent) -> tuple[Any, ...]:
    values = {
        "event_id": event.event_id,
        "telemetry_run_id": event.telemetry_run_id,
        "event_kind": event.event_kind,
        "worker_id": event.worker_id,
        "posture": event.posture,
        "observed_at": event.observed_at,
        "started_events": event.started_events,
        "terminal_events": event.terminal_events,
        "unmatched_starts": event.unmatched_starts,
        "undurable_events": event.undurable_events,
        "ambiguous_events": event.ambiguous_events,
        "dropped_events": event.dropped_events,
        "expected_calls": event.expected_calls,
        "observed_calls": event.observed_calls,
        "drain_status": event.drain_status,
        "reconciliation_status": event.reconciliation_status,
        "expected_work_digest": event.expected_work_digest,
        "detail": event.detail,
    }
    return tuple(values[column] for column in RUN_EVENT_COLUMNS)


def call_row(record: TelemetryCallRecord) -> tuple[Any, ...]:
    values = {
        "call_id": record.call_id,
        "telemetry_run_id": record.telemetry_run_id,
        "posture": record.posture,
        "worker_id": record.worker_id,
        "requested_provider": record.requested_provider,
        "requested_model": record.requested_model,
        "request_config_fingerprint": record.request_config_fingerprint,
        "routing_decision_fingerprint": record.routing_decision_fingerprint,
        "candidate_count": record.candidate_count,
        "started_at": record.started_at,
    }
    values.update(_identity_columns(record.identity))
    return tuple(values[column] for column in CALL_COLUMNS)


def invocation_row(record: SdkInvocationRecord) -> tuple[Any, ...]:
    values = {
        "invocation_id": record.invocation_id,
        "call_id": record.call_id,
        "telemetry_run_id": record.telemetry_run_id,
        "posture": record.posture,
        "worker_id": record.worker_id,
        "invocation_kind": record.invocation_kind,
        "provider": record.provider,
        "requested_model": record.requested_model,
        "candidate_ordinal": record.candidate_ordinal,
        "retry_ordinal": record.retry_ordinal,
        "attempt_ordinal": record.attempt_ordinal,
        "breaker_state_before": record.breaker_before.state,
        "breaker_failure_count_before": record.breaker_before.failure_count,
        "breaker_snapshot_status_before": record.breaker_before.status,
        "fallback_candidate": record.fallback_candidate,
        "fallback_from_provider": record.fallback_from_provider,
        "fallback_from_model": record.fallback_from_model,
        "request_config_fingerprint": record.request_config_fingerprint,
        "routing_decision_fingerprint": record.routing_decision_fingerprint,
        "started_at": record.started_at,
    }
    values.update(_identity_columns(record.identity))
    return tuple(values[column] for column in INVOCATION_COLUMNS)


def attempt_row(record: HttpAttemptRecord) -> tuple[Any, ...]:
    values = {
        "attempt_id": record.attempt_id,
        "invocation_id": record.invocation_id,
        "call_id": record.call_id,
        "telemetry_run_id": record.telemetry_run_id,
        "posture": record.posture,
        "worker_id": record.worker_id,
        "provider": record.provider,
        "requested_model": record.requested_model,
        "http_retry_ordinal": record.http_retry_ordinal,
        "request_method": record.request_method,
        "request_path": record.request_path,
        "request_started_at": record.request_started_at,
    }
    return tuple(values[column] for column in ATTEMPT_COLUMNS)


def _value_details(event: AttemptEvent) -> str:
    """A bounded, closed-vocabulary summary of why values were refused.

    Every token on both sides of the ``=`` comes from *this* package's
    vocabulary — never from provider text — except a stop reason that already
    passed its positive grammar. That is what makes it safe to store as a plain
    column and to constrain with a CHECK.
    """
    parts: list[str] = []
    named = {
        "http_status": event.http_status,
        "provider_request_id": event.provider_request_id,
        "retry_after": event.retry_after,
    }
    for name in OBSERVABLE_PROVIDER_FIELDS:
        named[name] = getattr(event.observation, name)
    for name, value in named.items():
        if value.detail:
            parts.append(f"{name}={value.detail}")
    return ";".join(parts)[:512]


def event_row(event: AttemptEvent) -> tuple[Any, ...]:
    observation = event.observation
    values: dict[str, Any] = {
        "event_id": event.event_id,
        "subject_kind": event.subject_kind,
        "subject_id": event.subject_id,
        "call_id": event.call_id,
        "telemetry_run_id": event.telemetry_run_id,
        "event_kind": event.event_kind,
        "event_ordinal": event.event_ordinal,
        "is_terminal": event.is_terminal,
        "observed_at": event.observed_at,
        "worker_id": event.worker_id,
        "transport_outcome": event.transport_outcome,
        "http_status": event.http_status.stored,
        "http_status_status": event.http_status.status,
        "provider_request_id": event.provider_request_id.stored,
        "provider_request_id_status": event.provider_request_id.status,
        "retry_after": event.retry_after.stored,
        "retry_after_status": event.retry_after.status,
        "breaker_state_after": event.breaker_after.state,
        "breaker_failure_count_after": event.breaker_after.failure_count,
        "breaker_snapshot_status_after": event.breaker_after.status,
        "error_category": event.error_category,
        "error_identity": event.error_identity,
        "failure_class": event.failure_class,
        "value_details": _value_details(event),
        "response_metadata_fingerprint": event.response_metadata_fingerprint,
        "schema_version": event.schema_version,
    }
    for name in OBSERVABLE_PROVIDER_FIELDS:
        provider_value = getattr(observation, name)
        values[name] = provider_value.stored
        values[f"{name}_status"] = provider_value.status
    return tuple(values[column] for column in EVENT_COLUMNS)


ROW_BUILDERS = {
    RUN_TABLE: run_row,
    RUN_EVENT_TABLE: run_event_row,
    CALL_TABLE: call_row,
    INVOCATION_TABLE: invocation_row,
    ATTEMPT_TABLE: attempt_row,
    EVENT_TABLE: event_row,
}


# ─────────────────────────── write-result classification ───────────────────────────


def classify_write_failure(exc: BaseException) -> str:
    """``"failed"`` when the row definitively is not there, else ``"ambiguous"``.

    A ``PostgresError`` means the server received the statement, evaluated it and
    rejected it — the row does not exist. Anything else (a dropped connection, a
    socket timeout, an interface error) leaves the outcome genuinely unknown,
    and pretending otherwise in either direction would corrupt a run's
    completeness claim.
    """
    try:
        import asyncpg
    except ImportError:  # pragma: no cover - environment guard
        asyncpg = None  # type: ignore[assignment]
    if asyncpg is not None and isinstance(exc, asyncpg.PostgresError):
        return "failed"
    if isinstance(exc, (ValueError, TypeError)):
        # The row could not even be built into a statement.
        return "failed"
    return "ambiguous"


class TelemetrySink(Protocol):
    """The seam the service writes through."""

    async def append_start(self, table: str, record: Any) -> None: ...

    async def append_event(self, event: Any) -> None: ...


# ─────────────────────────── PostgreSQL sink ───────────────────────────


class PostgresTelemetrySink:
    """Durable sink backed by the authoritative MAS PostgreSQL database.

    Deliberately has no in-memory fallback. A telemetry log that silently
    degrades to process memory reports success while producing nothing an
    experiment can restore — the exact failure mode of ``decision_events`` that
    this wave exists not to repeat.
    """

    def __init__(self, pool_provider) -> None:
        self._pool_provider = pool_provider

    async def _pool(self):
        pool = await self._pool_provider()
        if pool is None:
            raise ProviderTelemetryStorageUnavailable(
                "no PostgreSQL pool is available for provider-attempt telemetry"
            )
        return pool

    async def append(self, table: str, record: Any) -> None:
        builder = ROW_BUILDERS[table]
        row = builder(record)
        pool = await self._pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute(insert_sql(table), *row)
        except Exception as exc:  # noqa: BLE001 - classified, then re-raised
            if classify_write_failure(exc) == "ambiguous":
                raise AmbiguousWrite(type(exc).__name__) from exc
            raise

    async def append_start(self, table: str, record: Any) -> None:
        await self.append(table, record)

    async def append_event(self, event: Any) -> None:
        table = RUN_EVENT_TABLE if isinstance(event, RunEvent) else EVENT_TABLE
        await self.append(table, event)


class NullTelemetrySink:
    """What a disabled build holds. Never opens a connection."""

    async def append(self, table: str, record: Any) -> None:
        return None

    async def append_start(self, table: str, record: Any) -> None:
        return None

    async def append_event(self, event: Any) -> None:
        return None


async def default_pool_provider():
    """The shared authoritative pool, or a storage-unavailable failure."""
    try:
        from store import _get_pool

        return await _get_pool()
    except Exception as exc:  # noqa: BLE001 - classified by the caller
        raise ProviderTelemetryStorageUnavailable(type(exc).__name__) from exc


# ─────────────────────────── read projection ───────────────────────────


def as_utc_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            # TIMESTAMPTZ always reads back aware; a naive value here would mean
            # the column type changed underneath us, so say so rather than guess.
            raise ValueError("provider telemetry timestamps must read back as UTC-aware")
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


_TIMESTAMP_COLUMNS = frozenset(
    {"started_at", "observed_at", "request_started_at", "recorded_at", "applied_at"}
)
_ARRAY_COLUMNS = frozenset({"expected_phases"})


def row_to_export_dict(table: str, row: Any) -> dict[str, Any]:
    """A stable, JSON-safe projection of one stored row, keyed by column name."""

    def get(column: str) -> Any:
        try:
            return row[column]
        except (TypeError, KeyError, IndexError):
            return getattr(row, column, None)

    payload: dict[str, Any] = {}
    for column in READ_COLUMNS[table]:
        value = get(column)
        if column in _TIMESTAMP_COLUMNS:
            value = as_utc_iso(value)
        elif column in UUID_COLUMNS and value is not None:
            value = str(value)
        elif column in _ARRAY_COLUMNS:
            value = list(value or [])
        payload[column] = value
    return payload


def select_sql(
    table: str,
    *,
    where: Iterable[str] = (),
    placeholder: str = "psycopg",
) -> str:
    """A keyset-ordered SELECT over the exact read column tuple."""
    columns = ", ".join(READ_COLUMNS[table])
    sql = f"SELECT {columns} FROM {table}"
    clauses = list(where)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += f" ORDER BY {KEYSET_COLUMN[table]} ASC"
    return sql


def count_sql(table: str, *, where: Iterable[str] = ()) -> str:
    sql = f"SELECT count(*) FROM {table}"
    clauses = list(where)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    return sql
