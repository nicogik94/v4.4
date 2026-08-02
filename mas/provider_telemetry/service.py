"""Telemetry postures, the run session, and run-end reconciliation.

═══ The two postures, and what each one is allowed to claim ═══

**A. Observational** (``MAS_PROVIDER_TELEMETRY_POSTURE=observational``)

Default-off; explicitly enabled. It never changes prompts, routing, models,
retry policy, fallback order, breaker decisions, cache keys or phase ordering.
Every telemetry failure fails **open**: a write that cannot happen is counted
and the run continues unchanged. Because of that, an observational run **does
not claim exhaustive coverage**, and both the health endpoint and every export
say so in as many words. It is suitable for operating a service. It is *not*
suitable for a paired experiment.

**B. Strict experiment** (``MAS_PROVIDER_TELEMETRY_POSTURE=strict``)

The only posture suitable for a future RB3 paired experiment. For every actual
provider HTTP attempt:

1. the already-selected candidate and routing decision are frozen into an
   immutable ``SdkInvocationRecord`` — telemetry never selects anything;
2. an immutable attempt identity is minted;
3. a durable attempt-start row is persisted **before** network transport;
4. if that start cannot be persisted, **the provider request is not sent**;
5. the actual SDK/HTTP transport attempt is recorded, SDK-internal retries
   included, each with its own identity and ordinal;
6. a separate completion / provider-failure / cancellation / unknown event is
   appended — never an update;
7. no event replaces a start or an earlier observation;
8. all starts and terminal events are reconciled at a run-end barrier;
9. any start without a terminal event makes the run **incomplete and
   uncertified**;
10. any ambiguous database-write result makes the run **uncertified**.

Strict-mode start persistence is intentionally **fail-closed**. It is not, and
is nowhere described as, observationally behavior-neutral: a run that cannot
record an attempt does not make it. That is the trade a paired experiment needs,
and it is the reason strict mode is off by default.

═══ Why completion delivery may be asynchronous ═══

Completion events are submitted to a bounded queue drained by a dedicated worker
(:mod:`provider_telemetry.delivery`) and are never awaited inside retry
selection, fallback selection, breaker transitions, cache population, a
successful provider return, or response transformation. This is sound *only*
because the start is synchronous: an unmatched start is independently
detectable, so a lost completion downgrades the run to uncertified rather than
silently disappearing.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, AsyncIterator, Iterable, Optional

import config

from . import repository
from .capture import InvocationCapture, capture_scope, guarded
from .delivery import AmbiguousWrite, DrainResult, EventDelivery, NullDelivery
from .identity import (
    TelemetryIdentity,
    current_identity,
    new_uuid,
    worker_id,
)
from .models import (
    DRAIN_DRAINED,
    MIGRATION_NAME,
    POSTURE_OBSERVATIONAL,
    POSTURE_STRICT,
    RECONCILIATION_COMPLETE,
    RECONCILIATION_INCOMPLETE,
    RECONCILIATION_PENDING,
    RECONCILIATION_UNCERTIFIED,
    RUN_EVENT_RECONCILIATION,
    RUN_EVENT_WORKER_DRAINED,
    RUN_EVENT_WORKER_REGISTERED,
    TELEMETRY_SCHEMA_VERSION,
    AttemptEvent,
    HttpAttemptRecord,
    RunEvent,
    SdkInvocationRecord,
    TelemetryCallRecord,
    TelemetryRunRecord,
    canonical_fingerprint,
    utc_now,
)
from .repository import (
    ATTEMPT_TABLE,
    CALL_TABLE,
    INVOCATION_TABLE,
    NullTelemetrySink,
    PostgresTelemetrySink,
    RUN_TABLE,
    classify_write_failure,
    default_pool_provider,
)
from .posture import (
    StrictPostureMisconfigured,
    StrictPostureViolation,
    configuration_problems,
    non_experiment,
    require_strict_scope_posture,
    strict_required,
)
from .transport import TelemetryStartUnavailable

logger = logging.getLogger(__name__)

POSTURE_OFF = "off"
CONFIGURABLE_POSTURES = (POSTURE_OFF, POSTURE_OBSERVATIONAL, POSTURE_STRICT)

# The completeness statement attached to every health report and every export.
# An observational run must never be read as an exhaustive record.
OBSERVATIONAL_COMPLETENESS_NOTICE = (
    "Observational posture: telemetry fails open. Completeness is NOT guaranteed — "
    "events may be missing after a crash, a cancellation, or a sink outage, and no "
    "reconciliation was performed. Do not use for a paired experiment."
)
# A run-level event is written on the shutdown path; a wedged sink must not be
# able to hang a run's exit waiting for one.
RUN_EVENT_WRITE_TIMEOUT = 10.0

# The failure class the transport stamps on an invocation whose strict start
# refused before transport. It is the durable evidence that an invocation with
# no HTTP attempt was *blocked*, rather than one whose attempt row was lost.
STRICT_START_REFUSED_CLASS = "TelemetryStartUnavailable"

STRICT_COMPLETENESS_NOTICE = (
    "Strict posture: every external HTTP attempt persisted a durable start before "
    "transport, and completeness is established only by the run-end reconciliation "
    "reported alongside this artifact."
)


# ─────────────────────────── posture configuration ───────────────────────────


def configured_posture() -> str:
    """The posture this process is configured for. ``off`` unless set."""
    raw = os.getenv(getattr(config, "PROVIDER_TELEMETRY_POSTURE_ENV", ""), "")
    value = str(raw or "").strip().lower()
    if value in CONFIGURABLE_POSTURES:
        return value
    if value:
        logger.warning(
            "unknown provider-telemetry posture %r; telemetry stays off", value[:32]
        )
        return POSTURE_OFF
    # Compatibility with the original boolean flag: it means observational.
    if getattr(config, "provider_attempt_telemetry_enabled", None) and (
        config.provider_attempt_telemetry_enabled()
    ):
        return POSTURE_OBSERVATIONAL
    return POSTURE_OFF


def telemetry_enabled() -> bool:
    return configured_posture() != POSTURE_OFF


def strict_mode_configured() -> bool:
    return configured_posture() == POSTURE_STRICT


# ─────────────────────────── run fingerprints ───────────────────────────


def source_commit() -> str:
    """The commit this process is running, or ``""`` when it cannot be proven.

    An empty value is truthful and is preferable to a guess: a run attestation
    that names the wrong commit is worse than one that admits it does not know.
    """
    override = os.getenv("MAS_SOURCE_COMMIT", "").strip()
    if override:
        return override[:64]

    def read() -> str:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip()[:64] if result.returncode == 0 else ""

    return guarded(read, "", reason="source_commit")


def runtime_fingerprint(**extra: Any) -> str:
    """A fingerprint of the runtime and configuration, never of any prompt."""
    payload = {
        "python": sys.version.split()[0],
        "platform": platform.system(),
        "schema_version": TELEMETRY_SCHEMA_VERSION,
        "migration": MIGRATION_NAME,
        "posture": configured_posture(),
    }
    payload.update({str(key): value for key, value in extra.items()})
    return canonical_fingerprint(payload)


def request_config_fingerprint(
    *,
    provider: str,
    model: str,
    max_tokens: Any,
    temperature: Any,
    thinking_budget: Any,
    max_retries: Any,
    retry_delays: Any,
) -> str:
    """Fingerprint of the request *configuration* — never of the prompt.

    Two attempts sharing this fingerprint were configured identically, which is
    what a paired evaluation needs to assert configuration parity. The prompt is
    excluded on purpose: a prompt digest is still a derived artifact of a prompt,
    so it is simply not part of the identity this column claims to carry.
    """
    return canonical_fingerprint(
        {
            "provider": str(provider or ""),
            "model": str(model or ""),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "thinking_budget": thinking_budget,
            "max_retries": max_retries,
            "retry_delays": list(retry_delays or []),
        }
    )


def routing_decision_fingerprint(
    *,
    provider: str,
    model: str,
    candidate_ordinal: int,
    retry_ordinal: int,
    selection_reason: str = "",
    task_profile: str = "",
) -> str:
    """Fingerprint of the *frozen* routing decision this attempt acts on."""
    return canonical_fingerprint(
        {
            "provider": str(provider or ""),
            "model": str(model or ""),
            "candidate_ordinal": int(candidate_ordinal),
            "retry_ordinal": int(retry_ordinal),
            "selection_reason": str(selection_reason or ""),
            "task_profile": str(task_profile or ""),
        }
    )


# ─────────────────────────── preflight ───────────────────────────


@dataclass(frozen=True)
class PreflightResult:
    """Whether this process may start a run in the requested posture."""

    posture: str
    healthy: bool
    reasons: tuple[str, ...] = ()

    def as_payload(self) -> dict[str, Any]:
        return {
            "posture": self.posture,
            "healthy": self.healthy,
            "reasons": list(self.reasons),
        }


async def strict_preflight(sink: Any) -> PreflightResult:
    """Refuse a strict run whose telemetry cannot possibly be complete.

    A strict run that starts against a disabled flag, an unreachable sink, or a
    schema that does not carry the full contract would produce an artifact that
    looks like evidence and is not. Each of the four checks below has a
    dedicated regression test.
    """
    reasons: list[str] = []
    reasons.extend(f"configuration:{problem}" for problem in configuration_problems())
    posture = configured_posture()
    if posture != POSTURE_STRICT:
        reasons.append(f"posture_is_{posture}")
    if not strict_required():
        # A strict run in a process that has declared itself a non-experiment
        # worker is a contradiction: the guard that makes "no unscoped provider
        # call" true has been switched off, so the run cannot claim it.
        reasons.append("worker_is_not_strict_required")

    pool = None
    try:
        pool = await sink._pool()  # noqa: SLF001 - the sink's own health probe
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        reasons.append(f"sink_unavailable:{type(exc).__name__}")
    else:
        if pool is None:
            # No pool is not "healthy but empty": a strict run whose sink cannot
            # be reached could never persist a start, and starting it would
            # produce an artifact that looks like evidence and is not.
            reasons.append("sink_unavailable:no_pool")

    if pool is not None:
        missing = await _missing_schema_objects(pool)
        if missing:
            reasons.append("schema_incomplete:" + ",".join(sorted(missing))[:160])
        acl_problems = await _acl_problems(pool)
        if acl_problems:
            reasons.append("acl_unsafe:" + ",".join(sorted(acl_problems))[:160])

    return PreflightResult(posture=posture, healthy=not reasons, reasons=tuple(reasons))


# The exact per-relation column counts the contract requires. A one-column table
# named `provider_attempt` must never satisfy preflight, so presence of the name
# is never sufficient: the full write tuple has to be there.
async def _missing_schema_objects(pool) -> set[str]:
    missing: set[str] = set()
    async with pool.acquire() as conn:
        for table in repository.TELEMETRY_TABLES:
            rows = await conn.fetch(
                """
                SELECT a.attname
                FROM pg_attribute a
                JOIN pg_class c ON c.oid = a.attrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = current_schema()
                  AND c.relname = $1 AND c.relkind = 'r'
                  AND a.attnum > 0 AND NOT a.attisdropped
                """,
                table,
            )
            present = {row["attname"] for row in rows}
            if not present:
                missing.add(table)
                continue
            required = set(repository.READ_COLUMNS[table])
            absent = required - present
            if absent:
                missing.add(f"{table}({','.join(sorted(absent))[:80]})")
        for table in repository.APPEND_ONLY_TABLES:
            triggers = await conn.fetch(
                """
                -- `tgenabled` is PostgreSQL's internal "char" type; casting to
                -- text keeps the comparison below driver-independent.
                SELECT t.tgname, t.tgenabled::text AS tgenabled
                FROM pg_trigger t
                JOIN pg_class c ON c.oid = t.tgrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = current_schema()
                  AND c.relname = $1 AND NOT t.tgisinternal
                """,
                table,
            )
            enabled = {row["tgname"] for row in triggers if row["tgenabled"] != "D"}
            if len(enabled) < 2:
                missing.add(f"{table}:append_only_triggers")
    return missing


async def _acl_problems(pool) -> set[str]:
    """Verify the runtime role holds no privilege that could erase the log."""
    problems: set[str] = set()
    async with pool.acquire() as conn:
        for table in repository.APPEND_ONLY_TABLES:
            row = await conn.fetchrow(
                """
                SELECT
                    has_table_privilege(current_user, $1, 'UPDATE')   AS can_update,
                    has_table_privilege(current_user, $1, 'DELETE')   AS can_delete,
                    has_table_privilege(current_user, $1, 'TRUNCATE') AS can_truncate,
                    has_table_privilege(current_user, $1, 'TRIGGER')  AS can_trigger,
                    pg_catalog.pg_get_userbyid(c.relowner) = current_user AS is_owner
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = current_schema() AND c.relname = $1
                """,
                table,
            )
            if row is None:
                problems.add(f"{table}:absent")
                continue
            for privilege in ("can_update", "can_delete", "can_truncate", "can_trigger"):
                if row[privilege]:
                    problems.add(f"{table}:{privilege}")
            if row["is_owner"]:
                problems.add(f"{table}:runtime_is_owner")
    return problems


# ─────────────────────────── attestation ───────────────────────────


@dataclass(frozen=True)
class RunAttestation:
    """The final, checkable claim about one run's telemetry."""

    telemetry_run_id: str
    posture: str
    telemetry_required: bool
    workers: tuple[str, ...] = ()
    non_strict_workers: tuple[str, ...] = ()
    started_events: int = 0
    terminal_events: int = 0
    unmatched_starts: int = 0
    duplicate_terminals: int = 0
    undurable_events: int = 0
    ambiguous_events: int = 0
    dropped_events: int = 0
    capture_failures: int = 0
    unrepresented_capture_failures: int = 0
    expected_phases: tuple[str, ...] = ()
    missing_phases: tuple[str, ...] = ()
    expected_work_digest: str = ""
    calls_without_invocation: int = 0
    invocations_without_attempt: int = 0
    expected_calls: int = 0
    observed_calls: int = 0
    drain_status: str = DRAIN_DRAINED
    reconciliation_status: str = RECONCILIATION_PENDING
    failures: tuple[str, ...] = ()

    @property
    def certified(self) -> bool:
        """True only when every clause of the strict contract is proven."""
        return self.reconciliation_status == RECONCILIATION_COMPLETE and not self.failures

    def as_payload(self) -> dict[str, Any]:
        return {
            "telemetry_run_id": self.telemetry_run_id,
            "posture": self.posture,
            "telemetry_required": self.telemetry_required,
            "workers": list(self.workers),
            "non_strict_workers": list(self.non_strict_workers),
            "started_events": self.started_events,
            "terminal_events": self.terminal_events,
            "unmatched_starts": self.unmatched_starts,
            "duplicate_terminals": self.duplicate_terminals,
            "undurable_events": self.undurable_events,
            "ambiguous_events": self.ambiguous_events,
            "dropped_events": self.dropped_events,
            "capture_failures": self.capture_failures,
            "unrepresented_capture_failures": self.unrepresented_capture_failures,
            "expected_phases": list(self.expected_phases),
            "missing_phases": list(self.missing_phases),
            "expected_work_digest": self.expected_work_digest,
            "calls_without_invocation": self.calls_without_invocation,
            "invocations_without_attempt": self.invocations_without_attempt,
            "expected_calls": self.expected_calls,
            "observed_calls": self.observed_calls,
            "drain_status": self.drain_status,
            "reconciliation_status": self.reconciliation_status,
            "failures": list(self.failures),
            "certified": self.certified,
            "completeness_notice": (
                STRICT_COMPLETENESS_NOTICE
                if self.posture == POSTURE_STRICT
                else OBSERVATIONAL_COMPLETENESS_NOTICE
            ),
        }


# ─────────────────────────── the session ───────────────────────────


class TelemetrySession:
    """Owns one run's telemetry: the sink, the delivery worker, the accounting."""

    def __init__(
        self,
        *,
        run_record: TelemetryRunRecord,
        sink: Any,
        delivery: Any,
    ) -> None:
        self.run = run_record
        self._sink = sink
        self._delivery = delivery
        self.local_starts = 0
        self.local_start_failures = 0
        self.local_start_ambiguities = 0
        self.local_calls = 0
        self.run_event_failures = 0
        # Capture failures this process could not turn into durable events, and
        # events the delivery queue refused. Both are *undurable*: something
        # happened that telemetry cannot show anyone, so no run carrying one may
        # be certified. Counted separately from delivery's own counters because
        # these never reached the queue at all.
        self.unrepresented_capture_failures = 0
        self.unqueued_events = 0
        self.workers: set[str] = {worker_id()}

    # ── posture ──

    @property
    def posture(self) -> str:
        return self.run.posture

    @property
    def strict(self) -> bool:
        return self.run.posture == POSTURE_STRICT

    @property
    def telemetry_run_id(self) -> str:
        return self.run.telemetry_run_id

    @property
    def delivery(self) -> Any:
        return self._delivery

    # ── synchronous, fail-closed starts ──

    async def persist_attempt_start(self, record: HttpAttemptRecord) -> None:
        """Persist an HTTP attempt start *before* the request is transmitted.

        In strict mode a failure raises :class:`TelemetryStartUnavailable`, which
        the transport lets propagate so the request is never sent. In
        observational mode the failure is counted and the request proceeds.
        """
        await self._persist_start(ATTEMPT_TABLE, record, subject="attempt")

    async def persist_invocation_start(self, record: SdkInvocationRecord) -> None:
        await self._persist_start(INVOCATION_TABLE, record, subject="invocation")

    async def persist_call_start(self, record: TelemetryCallRecord) -> None:
        await self._persist_start(CALL_TABLE, record, subject="call")
        self.local_calls += 1

    async def _persist_start(self, table: str, record: Any, *, subject: str) -> None:
        try:
            await self._sink.append_start(table, record)
        except asyncio.CancelledError:
            self.local_start_ambiguities += 1
            raise
        except AmbiguousWrite as exc:
            self.local_start_ambiguities += 1
            if self.strict:
                raise TelemetryStartUnavailable(
                    f"ambiguous {subject}-start write: {type(exc).__name__}"
                ) from exc
            return
        except Exception as exc:  # noqa: BLE001 - classified, then re-raised or absorbed
            self.local_start_failures += 1
            if classify_write_failure(exc) == "ambiguous":
                self.local_start_ambiguities += 1
            if self.strict:
                raise TelemetryStartUnavailable(
                    f"{subject}-start write failed: {type(exc).__name__}"
                ) from exc
            logger.debug("observational telemetry start failed: %s", type(exc).__name__)
            return
        self.local_starts += 1

    # ── non-blocking completion delivery ──

    def submit_event(self, event: AttemptEvent) -> bool:
        """Hand a completion/observation event to the delivery worker.

        Never awaited by the runtime. Called from retry selection, fallback
        selection, breaker transitions, cache population, successful provider
        return and response transformation — none of which may block.
        """
        return self._delivery.submit(event)

    def submit_events(self, events: Iterable[AttemptEvent]) -> int:
        """Submit a batch, counting anything the queue would not take.

        A refused submission is a completion event that will never exist
        anywhere. The delivery worker counts it too, but counting it here as
        well is what makes the shortfall visible to the caller that produced the
        batch, which is the only place that knows how many events there were.
        """
        submitted = 0
        refused = 0
        for event in events:
            if self.submit_event(event):
                submitted += 1
            else:
                refused += 1
        if refused:
            self.unqueued_events += refused
        return submitted

    def note_unrepresented_capture_failures(self, count: int) -> None:
        """Record capture failures that could not be turned into events.

        Deliberately not a log line: a run whose telemetry failed in a way it
        could not write down is not a run whose telemetry is merely noisy, and
        reconciliation has to be able to refuse it.
        """
        if count > 0:
            self.unrepresented_capture_failures += int(count)

    # ── run lifecycle ──

    async def register_worker(self) -> None:
        self.workers.add(worker_id())
        await self._append_run_event(
            RUN_EVENT_WORKER_REGISTERED, reconciliation_status=RECONCILIATION_PENDING
        )

    async def _append_run_event(
        self, kind: str, *, write_timeout: Optional[float] = None, **fields: Any
    ) -> None:
        event = guarded(
            lambda: RunEvent(
                event_id=new_uuid(),
                telemetry_run_id=self.telemetry_run_id,
                event_kind=kind,
                worker_id=worker_id(),
                posture=self.posture,
                observed_at=utc_now(),
                **fields,
            ),
            None,
            reason=f"run_event:{kind}",
        )
        if event is None:
            return
        try:
            # Bounded: a run-level event is written on the shutdown path, and a
            # wedged sink must not be able to hang a run's exit. A timeout here
            # is a lost run event, which reconciliation already reports — it is
            # never a reason to stop the process from finishing.
            timeout = min(
                RUN_EVENT_WRITE_TIMEOUT, max(1.0, float(write_timeout or RUN_EVENT_WRITE_TIMEOUT))
            )
            await asyncio.wait_for(self._sink.append_event(event), timeout=timeout)
        except asyncio.TimeoutError:
            self.run_event_failures += 1
            logger.warning(
                "provider-telemetry run event %s timed out; the run continues and "
                "the missing event is reflected in reconciliation",
                kind,
            )
        except Exception as exc:  # noqa: BLE001 - run events never break a run
            self.run_event_failures += 1
            logger.warning(
                "provider-telemetry run event %s could not be persisted (%s)",
                kind,
                type(exc).__name__,
            )

    async def drain(self, timeout: float = 30.0) -> DrainResult:
        result = await self._delivery.drain(timeout=timeout)
        await self._append_run_event(
            RUN_EVENT_WORKER_DRAINED,
            drain_status=result.status,
            undurable_events=int(result.counters.get("undurable", 0)),
            ambiguous_events=int(result.counters.get("ambiguous", 0)),
            dropped_events=int(result.counters.get("dropped", 0)),
            terminal_events=int(result.counters.get("delivered", 0)),
            reconciliation_status=RECONCILIATION_PENDING,
        )
        return result

    async def reconcile(self, *, drain_timeout: float = 30.0) -> RunAttestation:
        """The run-end barrier: drain, then prove (or disprove) completeness."""
        drain = await self._delivery.aclose(drain_timeout=drain_timeout)
        counters = drain.counters
        failures: list[str] = []

        if not self.run.telemetry_required and self.strict:
            failures.append("telemetry_not_required")
        if drain.status != DRAIN_DRAINED:
            failures.append(f"drain_{drain.status}")
        undurable = (
            int(counters.get("undurable", 0))
            + self.local_start_failures
            + self.unrepresented_capture_failures
            + self.unqueued_events
        )
        ambiguous = int(counters.get("ambiguous", 0)) + self.local_start_ambiguities
        if undurable:
            failures.append("undurable_events")
        if self.unrepresented_capture_failures:
            # Named separately from the aggregate: "a capture failure could not
            # itself be recorded" is a different operational problem from "an
            # event write failed", and an operator reading the attestation
            # should not have to guess which one happened.
            failures.append("unrepresented_capture_failure")
        if ambiguous:
            failures.append("ambiguous_writes")

        manifest_digest = self.expected_work_digest()
        missing: list[str] = []
        durable = await self._durable_reconciliation()
        if durable is None:
            failures.append("reconciliation_query_unavailable")
            durable = {
                "workers": (),
                "non_strict_workers": (),
                "started": 0,
                "terminal": 0,
                "unmatched": 0,
                "duplicates": 0,
                "calls": 0,
                "capture_failures": 0,
                "declared_phases": (),
                "observed_phases": set(),
                "calls_without_invocation": 0,
                "invocations_without_attempt": 0,
                "prior_reconciliations": (),
            }
        else:
            if durable["unmatched"]:
                failures.append("unmatched_starts")
            if durable["duplicates"]:
                failures.append("duplicate_terminal_events")
            if self.strict and durable["non_strict_workers"]:
                failures.append("non_strict_worker")
            # ── expected work ──
            # The manifest is not advisory. A run that declared phases and did
            # none of them is incomplete, whatever its attempt rows say —
            # including when it has none, which is the case the old
            # reconciliation could not distinguish from "nothing to do".
            declared = tuple(durable.get("declared_phases") or ())
            observed = set(durable.get("observed_phases") or ())
            missing = [phase for phase in declared if phase and phase not in observed]
            if missing:
                failures.append("missing_expected_phase")
            if declared and not durable["calls"]:
                failures.append("expected_work_not_started")
            if durable["calls_without_invocation"]:
                failures.append("call_without_invocation")
            if durable["invocations_without_attempt"]:
                failures.append("invocation_without_http_attempt")
            # An unexpected call is a run doing work it never declared, which
            # makes the manifest a description of something else.
            if declared:
                unexpected = sorted(observed - set(declared) - {""})
                if unexpected:
                    failures.append("unexpected_call_phase")
            # History is append-only, so an earlier unresolved verdict is still
            # there. Writing a clean one on top of it would not erase it, but it
            # would let a reader who looked only at the last row believe the run
            # was certified — so this run refuses to make that claim at all.
            prior = tuple(durable.get("prior_reconciliations") or ())
            if any(
                status != RECONCILIATION_COMPLETE
                and status != RECONCILIATION_PENDING
                for status in prior
            ):
                failures.append("earlier_unresolved_reconciliation")
            if self.strict and durable["capture_failures"]:
                # A strict run is the posture that claims every provider attempt
                # is described. A durable capture_failure event says one of them
                # is not, and no amount of the rest being fine makes that run
                # certifiable — the artifact would assert completeness over a
                # record that names its own gap.
                failures.append("capture_failure_recorded")

        status = RECONCILIATION_COMPLETE
        if failures:
            status = (
                RECONCILIATION_INCOMPLETE
                if "unmatched_starts" in failures
                else RECONCILIATION_UNCERTIFIED
            )

        attestation = RunAttestation(
            telemetry_run_id=self.telemetry_run_id,
            posture=self.posture,
            telemetry_required=self.run.telemetry_required,
            workers=tuple(sorted(durable["workers"])),
            non_strict_workers=tuple(sorted(durable["non_strict_workers"])),
            started_events=int(durable["started"]),
            terminal_events=int(durable["terminal"]),
            unmatched_starts=int(durable["unmatched"]),
            duplicate_terminals=int(durable["duplicates"]),
            undurable_events=undurable,
            ambiguous_events=ambiguous,
            dropped_events=int(counters.get("dropped", 0)),
            capture_failures=int(durable.get("capture_failures", 0)),
            unrepresented_capture_failures=self.unrepresented_capture_failures,
            expected_phases=tuple(durable.get("declared_phases") or ()),
            missing_phases=tuple(sorted(missing)) if failures else (),
            expected_work_digest=manifest_digest,
            calls_without_invocation=int(durable.get("calls_without_invocation", 0)),
            invocations_without_attempt=int(
                durable.get("invocations_without_attempt", 0)
            ),
            expected_calls=self.local_calls,
            observed_calls=int(durable["calls"]),
            drain_status=drain.status,
            reconciliation_status=status,
            failures=tuple(sorted(set(failures))),
        )
        await self._append_run_event(
            RUN_EVENT_RECONCILIATION,
            write_timeout=drain_timeout,
            drain_status=drain.status,
            reconciliation_status=status,
            # Bound on every verdict, not only the clean one, so a reader can
            # tell which manifest an incomplete verdict was about too.
            expected_work_digest=manifest_digest,
            started_events=attestation.started_events,
            terminal_events=attestation.terminal_events,
            unmatched_starts=attestation.unmatched_starts,
            undurable_events=attestation.undurable_events,
            ambiguous_events=attestation.ambiguous_events,
            dropped_events=attestation.dropped_events,
            expected_calls=attestation.expected_calls,
            observed_calls=attestation.observed_calls,
            detail=",".join(attestation.failures)[:256],
        )
        return attestation

    # ── the expected-work manifest ──

    @property
    def expected_phases(self) -> tuple[str, ...]:
        return tuple(self.run.expected_phases)

    def expected_work_digest(self) -> str:
        """The digest a completeness claim about this run is bound to.

        Over the *declared* manifest — the run id and the ordered phases — so
        two runs that declared different work cannot produce the same claim, and
        a claim cannot be re-read later as being about a smaller set of work
        than the one that was declared.
        """
        return canonical_fingerprint(
            {
                "telemetry_run_id": self.telemetry_run_id,
                "expected_phases": list(self.run.expected_phases),
                "schema_version": TELEMETRY_SCHEMA_VERSION,
            }
        )

    async def _durable_reconciliation(self) -> Optional[dict[str, Any]]:
        """Ask the database what actually landed. Never trusts process counters."""
        pool = None
        try:
            pool = await self._sink._pool()  # noqa: SLF001 - health probe
        except Exception:  # noqa: BLE001 - reported by the caller
            return None
        if pool is None:
            return None

        run_id = self.telemetry_run_id
        try:
            async with pool.acquire() as conn:
                started = await conn.fetchval(
                    "SELECT count(*) FROM provider_attempt WHERE telemetry_run_id = $1::uuid",
                    run_id,
                )
                terminal = await conn.fetchval(
                    "SELECT count(*) FROM provider_attempt_event "
                    "WHERE telemetry_run_id = $1::uuid AND is_terminal "
                    "AND subject_kind = 'http_attempt'",
                    run_id,
                )
                unmatched = await conn.fetchval(
                    "SELECT count(*) FROM provider_attempt a "
                    "WHERE a.telemetry_run_id = $1::uuid AND NOT EXISTS ("
                    "  SELECT 1 FROM provider_attempt_event e "
                    "  WHERE e.subject_id = a.attempt_id AND e.is_terminal)",
                    run_id,
                )
                unmatched_invocations = await conn.fetchval(
                    "SELECT count(*) FROM provider_sdk_invocation i "
                    "WHERE i.telemetry_run_id = $1::uuid AND NOT EXISTS ("
                    "  SELECT 1 FROM provider_attempt_event e "
                    "  WHERE e.subject_id = i.invocation_id AND e.is_terminal)",
                    run_id,
                )
                duplicates = await conn.fetchval(
                    "SELECT count(*) FROM (SELECT subject_id FROM provider_attempt_event "
                    "WHERE telemetry_run_id = $1::uuid AND is_terminal "
                    "GROUP BY subject_id HAVING count(*) > 1) AS d",
                    run_id,
                )
                calls = await conn.fetchval(
                    "SELECT count(*) FROM provider_telemetry_call "
                    "WHERE telemetry_run_id = $1::uuid",
                    run_id,
                )
                capture_failures = await conn.fetchval(
                    "SELECT count(*) FROM provider_attempt_event "
                    "WHERE telemetry_run_id = $1::uuid "
                    "AND event_kind = 'capture_failure'",
                    run_id,
                )
                # ── expected work, read back from the database ──
                # The manifest is read from the *run row*, not from this
                # process's memory: a reconciliation that trusted its own
                # in-memory copy of what it expected could not detect a run that
                # declared work and then did none of it, which is exactly the
                # shape of the finding.
                declared = await conn.fetchval(
                    "SELECT expected_phases FROM provider_telemetry_run "
                    "WHERE telemetry_run_id = $1::uuid",
                    run_id,
                )
                phase_rows = await conn.fetch(
                    "SELECT DISTINCT phase FROM provider_telemetry_call "
                    "WHERE telemetry_run_id = $1::uuid",
                    run_id,
                )
                # A call envelope that produced no SDK invocation at all. A
                # governance refusal records a skipped-candidate invocation, so
                # a call with nothing under it is a call whose work is simply
                # missing.
                callless = await conn.fetchval(
                    "SELECT count(*) FROM provider_telemetry_call c "
                    "WHERE c.telemetry_run_id = $1::uuid AND NOT EXISTS ("
                    "  SELECT 1 FROM provider_sdk_invocation i "
                    "  WHERE i.call_id = c.call_id)",
                    run_id,
                )
                # An invocation that reached no HTTP attempt, and carries no
                # durable reason for not having: a skipped or cancelled
                # terminal, or a strict start that refused before transport.
                attemptless = await conn.fetchval(
                    "SELECT count(*) FROM provider_sdk_invocation i "
                    "WHERE i.telemetry_run_id = $1::uuid "
                    "  AND i.invocation_kind = 'provider_call' "
                    "  AND NOT EXISTS ("
                    "    SELECT 1 FROM provider_attempt a "
                    "    WHERE a.invocation_id = i.invocation_id) "
                    "  AND NOT EXISTS ("
                    "    SELECT 1 FROM provider_attempt_event e "
                    "    WHERE e.subject_id = i.invocation_id AND e.is_terminal "
                    "      AND (e.event_kind IN ('skipped', 'cancelled') "
                    "           OR e.failure_class = $2))",
                    run_id,
                    STRICT_START_REFUSED_CLASS,
                )
                # Every reconciliation already written for this run. History is
                # append-only, so an earlier unresolved verdict is still here to
                # be found — and must not be overwritten by a later clean one.
                prior_rows = await conn.fetch(
                    "SELECT reconciliation_status FROM provider_telemetry_run_event "
                    "WHERE telemetry_run_id = $1::uuid "
                    "AND event_kind = 'reconciliation'",
                    run_id,
                )
                # Both relations: a worker that registered and then made no
                # attempt at all would otherwise be invisible here, and "this
                # worker was strict throughout" has to cover the whole worker,
                # not only the part of it that reached a provider.
                worker_rows = await conn.fetch(
                    "SELECT DISTINCT worker_id, posture FROM provider_attempt "
                    "WHERE telemetry_run_id = $1::uuid "
                    "UNION "
                    "SELECT DISTINCT worker_id, posture FROM provider_telemetry_run_event "
                    "WHERE telemetry_run_id = $1::uuid",
                    run_id,
                )
        except Exception:  # noqa: BLE001 - reported by the caller
            return None

        workers = {row["worker_id"] for row in worker_rows}
        non_strict = {
            row["worker_id"] for row in worker_rows if row["posture"] != POSTURE_STRICT
        }
        return {
            "workers": workers,
            "non_strict_workers": non_strict,
            "started": started or 0,
            "terminal": terminal or 0,
            "unmatched": (unmatched or 0) + (unmatched_invocations or 0),
            "duplicates": duplicates or 0,
            "calls": calls or 0,
            "capture_failures": capture_failures or 0,
            "declared_phases": tuple(declared or ()),
            "observed_phases": {row["phase"] for row in phase_rows if row["phase"]},
            "calls_without_invocation": callless or 0,
            "invocations_without_attempt": attemptless or 0,
            "prior_reconciliations": tuple(
                row["reconciliation_status"] for row in prior_rows
            ),
        }

    # ── health ──

    def health(self) -> dict[str, Any]:
        counters = self._delivery.counters.snapshot()
        return {
            "telemetry_run_id": self.telemetry_run_id,
            "posture": self.posture,
            "telemetry_required": self.run.telemetry_required,
            "delivery": counters,
            "local_starts": self.local_starts,
            "local_start_failures": self.local_start_failures,
            "local_start_ambiguities": self.local_start_ambiguities,
            "unrepresented_capture_failures": self.unrepresented_capture_failures,
            "unqueued_events": self.unqueued_events,
            "completeness_guaranteed": False,
            "completeness_notice": (
                STRICT_COMPLETENESS_NOTICE
                if self.strict
                else OBSERVATIONAL_COMPLETENESS_NOTICE
            ),
        }


class NullTelemetrySession:
    """What a disabled build holds. Every method is a no-op."""

    posture = POSTURE_OFF
    strict = False
    telemetry_run_id = ""

    def __init__(self) -> None:
        self._delivery = NullDelivery()

    @property
    def delivery(self) -> Any:
        return self._delivery

    async def persist_attempt_start(self, record: Any) -> None:
        return None

    async def persist_invocation_start(self, record: Any) -> None:
        return None

    async def persist_call_start(self, record: Any) -> None:
        return None

    def submit_event(self, event: Any) -> bool:
        return False

    def submit_events(self, events: Iterable[Any]) -> int:
        return 0

    async def register_worker(self) -> None:
        return None

    async def drain(self, timeout: float = 30.0) -> DrainResult:
        return DrainResult(DRAIN_DRAINED, self._delivery.counters.snapshot())

    async def reconcile(self, *, drain_timeout: float = 30.0) -> RunAttestation:
        return RunAttestation(
            telemetry_run_id="",
            posture=POSTURE_OBSERVATIONAL,
            telemetry_required=False,
            reconciliation_status=RECONCILIATION_PENDING,
            failures=("telemetry_disabled",),
        )

    def health(self) -> dict[str, Any]:
        return {
            "posture": POSTURE_OFF,
            "completeness_guaranteed": False,
            "completeness_notice": "Telemetry is disabled; no provider attempts are recorded.",
        }


# ─────────────────────────── session binding ───────────────────────────

_session: contextvars.ContextVar[Optional[TelemetrySession]] = contextvars.ContextVar(
    "provider_telemetry_session", default=None
)


def current_session() -> Optional[TelemetrySession]:
    return _session.get()


class StrictPreflightFailed(RuntimeError):
    """A strict run refused to start because telemetry could not be complete."""


def build_sink(sink: Any = None) -> Any:
    if sink is not None:
        return sink
    if not telemetry_enabled():
        return NullTelemetrySink()
    return PostgresTelemetrySink(default_pool_provider)


@contextlib.asynccontextmanager
async def run_session(
    *,
    identity: Optional[TelemetryIdentity] = None,
    posture: Optional[str] = None,
    sink: Any = None,
    capacity: int = 4096,
    drain_timeout: float = 30.0,
    expected_phases: Iterable[str] = (),
) -> AsyncIterator[Any]:
    """Open a telemetry run, and reconcile it at the barrier on the way out.

    A strict run refuses to start when :func:`strict_preflight` fails: producing
    an artifact that looks like experiment evidence but cannot be complete is
    worse than producing none.
    """
    resolved = (posture or configured_posture()).lower()
    # Checked before anything else, including the off short-circuit: a
    # strict-required worker that opened an `off` scope would be a worker with a
    # hole in it, and the hole is exactly what MAJ-3 describes.
    require_strict_scope_posture(resolved)
    if resolved == POSTURE_OFF:
        session: Any = NullTelemetrySession()
        token = _session.set(None)
        try:
            yield session
        finally:
            _session.reset(token)
        return

    identity = identity or current_identity()
    sink = build_sink(sink)

    if resolved == POSTURE_STRICT:
        preflight = await strict_preflight(sink)
        if not preflight.healthy:
            raise StrictPreflightFailed(
                "strict provider telemetry preflight failed: "
                + "; ".join(preflight.reasons)
            )

    run_record = TelemetryRunRecord(
        telemetry_run_id=new_uuid(),
        posture=resolved,
        telemetry_required=resolved == POSTURE_STRICT,
        entry_point=identity.entry_point,
        project_uuid=identity.project_uuid,
        external_project_id=identity.external_project_id,
        external_run_id=identity.run_id,
        job_id=identity.job_id,
        source_commit=source_commit(),
        runtime_fingerprint=runtime_fingerprint(),
        expected_phases=tuple(expected_phases),
        started_at=utc_now(),
    )

    delivery = EventDelivery(sink, posture=resolved, capacity=capacity, owner=worker_id())
    await delivery.start()
    session = TelemetrySession(run_record=run_record, sink=sink, delivery=delivery)

    try:
        await sink.append_start(RUN_TABLE, run_record)
    except Exception as exc:  # noqa: BLE001 - strict refuses, observational continues
        if resolved == POSTURE_STRICT:
            await delivery.aclose(drain_timeout=1.0)
            raise StrictPreflightFailed(
                f"strict run envelope could not be persisted: {type(exc).__name__}"
            ) from exc
        logger.warning(
            "observational telemetry run envelope was not persisted (%s); "
            "completeness is not guaranteed",
            type(exc).__name__,
        )

    token = _session.set(session)
    try:
        await session.register_worker()
        yield session
    finally:
        _session.reset(token)
        with contextlib.suppress(Exception):
            session.last_attestation = await session.reconcile(drain_timeout=drain_timeout)


@contextlib.asynccontextmanager
async def telemetry_scope(
    *,
    entry_point: str,
    project_id: Any = None,
    run_id: Any = None,
    job_id: Any = None,
    phase: Any = None,
    expected_phases: Iterable[str] = (),
    **session_kwargs: Any,
) -> AsyncIterator[Any]:
    """Bind identity and open a telemetry run in one step.

    The single helper every supported entry point uses, so "this entry point
    carries truthful identity" is one call rather than a convention. With
    telemetry off it costs one context-variable set and one no-op session.
    """
    from .identity import bind_identity

    with bind_identity(
        entry_point=entry_point,
        project_id=project_id,
        run_id=run_id,
        job_id=job_id,
        phase=phase,
    ):
        async with run_session(expected_phases=expected_phases, **session_kwargs) as session:
            yield session


# ─────────────────────────── capture helper ───────────────────────────


def open_invocation_capture(
    *,
    invocation_id: str,
    call_id: str,
    provider: str,
    requested_model: str,
    identity: Optional[TelemetryIdentity] = None,
) -> Any:
    """Build the capture buffer for one SDK invocation, bound to this session."""
    session = current_session()
    if session is None:
        return capture_scope(None)
    capture = InvocationCapture(
        invocation_id=invocation_id,
        call_id=call_id,
        telemetry_run_id=session.telemetry_run_id,
        posture=session.posture,
        worker_id=worker_id(),
        provider=provider,
        requested_model=requested_model,
        identity=identity or current_identity(),
    )
    return capture_scope(capture)


__all__ = [
    "CONFIGURABLE_POSTURES",
    "OBSERVATIONAL_COMPLETENESS_NOTICE",
    "POSTURE_OFF",
    "POSTURE_OBSERVATIONAL",
    "POSTURE_STRICT",
    "STRICT_COMPLETENESS_NOTICE",
    "PreflightResult",
    "RunAttestation",
    "StrictPreflightFailed",
    "TelemetrySession",
    "NullTelemetrySession",
    "build_sink",
    "configured_posture",
    "current_session",
    "open_invocation_capture",
    "request_config_fingerprint",
    "routing_decision_fingerprint",
    "run_session",
    "runtime_fingerprint",
    "source_commit",
    "strict_mode_configured",
    "StrictPostureMisconfigured",
    "StrictPostureViolation",
    "strict_preflight",
    "strict_required",
    "telemetry_enabled",
    "telemetry_scope",
]
