"""Immutable telemetry records and the append-only event model.

The remediated model is a *lifecycle*, not a row. The previous design built one
aggregate row after the fact, which made four of the audit's blockers structural
rather than incidental: a crash left nothing at all (blocker 2), one SDK call
with three internal HTTP retries produced one row (blocker 3), a late
transformation failure overwrote already-captured metadata (blocker 5), and
nothing could prove a call was missing entirely (blocker 9).

Five append-only entities replace it, from coarsest to finest:

``TelemetryRunRecord``
    One per run. Establishes *expected* work: the posture, whether telemetry was
    required, the source commit, the schema and runtime fingerprints.
``TelemetryCallRecord``
    One per logical model call (one gateway ``call()``).
``SdkInvocationRecord``
    One per gateway attempt — one candidate at one retry ordinal, i.e. one
    ``client.messages.create(...)``. Also the home of a *skipped* candidate,
    which never reaches the network at all. Carries the **frozen** routing
    decision: the candidate was already selected before this record existed, and
    nothing in this package can change it.
``HttpAttemptRecord``
    One per **actual HTTP request**, including every retry the SDK performs
    internally. This is the record whose persistence is fail-closed in strict
    mode, written immediately before the bytes leave.
``AttemptEvent``
    Everything that happens *after* a start: terminal outcomes, provider
    metadata observations, transformation failures, capture failures. Events are
    appended and never replace one another, so a rich observation captured early
    survives a failure that happens later.

Nothing here carries a prompt, a response body, a header collection, an
exception message, or a credential. Provider-sourced fields are
:class:`~provider_telemetry.values.ProviderValue` instances validated by
:mod:`provider_telemetry.redaction`, so "the provider sent nothing", "the
provider sent null", "the provider sent something invalid" and "we refused it"
stay six different facts rather than one ``NULL``.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .identity import TelemetryIdentity, new_uuid
from .values import (
    ABSENT,
    ProviderValue,
    VALUE_STATUSES,
)

# ─────────────────────────── versions ───────────────────────────

# Bumped when the meaning of a stored column changes. Readers branch on this
# rather than on column presence.
TELEMETRY_SCHEMA_VERSION = 2

# The migration that must be present for this build to write.
MIGRATION_NAME = "v63_provider_attempt_telemetry_foundation.sql"

# ─────────────────────────── closed vocabularies ───────────────────────────
#
# Every vocabulary below is mirrored as a CHECK constraint in v63, so a writer
# that bypassed these dataclasses still cannot store an uninterpretable value.

POSTURE_OBSERVATIONAL = "observational"
POSTURE_STRICT = "strict"
POSTURES = (POSTURE_OBSERVATIONAL, POSTURE_STRICT)

INVOCATION_PROVIDER_CALL = "provider_call"
INVOCATION_SKIPPED_CANDIDATE = "skipped_candidate"
INVOCATION_KINDS = (INVOCATION_PROVIDER_CALL, INVOCATION_SKIPPED_CANDIDATE)

SUBJECT_SDK_INVOCATION = "sdk_invocation"
SUBJECT_HTTP_ATTEMPT = "http_attempt"
SUBJECT_KINDS = (SUBJECT_SDK_INVOCATION, SUBJECT_HTTP_ATTEMPT)

# Terminal event kinds. Exactly one of these must exist per start.
EVENT_COMPLETED = "completed"
EVENT_PROVIDER_FAILURE = "provider_failure"
EVENT_CANCELLED = "cancelled"
EVENT_UNKNOWN = "unknown"
# Non-terminal event kinds. Any number may exist, and none of them can retract
# or overwrite an earlier one.
EVENT_OBSERVATION = "observation"
EVENT_TRANSFORMATION_FAILURE = "transformation_failure"
EVENT_CAPTURE_FAILURE = "capture_failure"
EVENT_SKIPPED = "skipped"

TERMINAL_EVENT_KINDS = (
    EVENT_COMPLETED,
    EVENT_PROVIDER_FAILURE,
    EVENT_CANCELLED,
    EVENT_UNKNOWN,
    EVENT_SKIPPED,
)
NON_TERMINAL_EVENT_KINDS = (
    EVENT_OBSERVATION,
    EVENT_TRANSFORMATION_FAILURE,
    EVENT_CAPTURE_FAILURE,
)
EVENT_KINDS = TERMINAL_EVENT_KINDS + NON_TERMINAL_EVENT_KINDS

TRANSPORT_RESPONSE = "response"
TRANSPORT_ERROR = "transport_error"
TRANSPORT_CANCELLED = "cancelled"
TRANSPORT_UNKNOWN = "unknown"
TRANSPORT_OUTCOMES = (
    TRANSPORT_RESPONSE,
    TRANSPORT_ERROR,
    TRANSPORT_CANCELLED,
    TRANSPORT_UNKNOWN,
)

BREAKER_CLOSED = "closed"
BREAKER_OPEN = "open"
BREAKER_UNKNOWN = "unknown"
BREAKER_STATES = (BREAKER_CLOSED, BREAKER_OPEN, BREAKER_UNKNOWN)

# Run-level events.
RUN_EVENT_WORKER_REGISTERED = "worker_registered"
RUN_EVENT_WORKER_DRAINED = "worker_drained"
RUN_EVENT_RECONCILIATION = "reconciliation"
RUN_EVENT_KINDS = (
    RUN_EVENT_WORKER_REGISTERED,
    RUN_EVENT_WORKER_DRAINED,
    RUN_EVENT_RECONCILIATION,
)

DRAIN_UNKNOWN = "unknown"
DRAIN_DRAINED = "drained"
DRAIN_FAILED = "failed"
DRAIN_TIMEOUT = "timeout"
DRAIN_STATUSES = (DRAIN_UNKNOWN, DRAIN_DRAINED, DRAIN_FAILED, DRAIN_TIMEOUT)

RECONCILIATION_PENDING = "pending"
RECONCILIATION_COMPLETE = "complete"
RECONCILIATION_INCOMPLETE = "incomplete"
RECONCILIATION_UNCERTIFIED = "uncertified"
RECONCILIATION_STATUSES = (
    RECONCILIATION_PENDING,
    RECONCILIATION_COMPLETE,
    RECONCILIATION_INCOMPLETE,
    RECONCILIATION_UNCERTIFIED,
)

# Provider-sourced fields carried on an observation. Named so the database can
# assert that every one of them has a status, and so an export reader can
# enumerate them without knowing the column list.
OBSERVABLE_PROVIDER_FIELDS = (
    "provider_response_id",
    "effective_model",
    "stop_reason",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
)

_FINGERPRINT_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_EMPTY_FINGERPRINT = "0" * 64

_MAX_LABEL = 128


class TelemetryRecordError(ValueError):
    """A record was constructed outside its closed contract."""


# ─────────────────────────── shared helpers ───────────────────────────


def utc_now() -> datetime:
    """The single clock this package reads for durable timestamps."""
    return datetime.now(timezone.utc)


def require_utc(value: Optional[datetime], label: str, *, required: bool = False):
    """Return an aware UTC datetime, or refuse.

    A naive datetime is refused rather than assumed to be UTC: guessing would
    silently corrupt the temporal ordering an experiment depends on.
    """
    if value is None:
        if required:
            raise TelemetryRecordError(f"{label} is required")
        return None
    if not isinstance(value, datetime):
        raise TelemetryRecordError(f"{label} must be a datetime")
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise TelemetryRecordError(f"{label} must be timezone-aware UTC")
    return value.astimezone(timezone.utc)


def canonical_fingerprint(payload: dict[str, Any]) -> str:
    """Stable SHA-256 over a *metadata* mapping.

    Callers pass configuration and identity only. A prompt digest is still a
    derived artifact of a prompt, so prompts are simply not part of anything
    this function is called with; the one production caller is pinned by a
    static test.
    """
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _fingerprint(value: Any, label: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return _EMPTY_FINGERPRINT
    if not _FINGERPRINT_RE.match(text):
        raise TelemetryRecordError(f"{label} must be 64 lowercase hex characters")
    return text


def _label(value: Any, *, max_chars: int = _MAX_LABEL) -> str:
    if value is None:
        return ""
    text = "".join(ch for ch in str(value) if ch.isprintable())
    return " ".join(text.split())[:max_chars]


def _positive_ordinal(value: Any, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise TelemetryRecordError(f"{label} must be an integer") from exc
    if isinstance(value, bool) or parsed < 1:
        raise TelemetryRecordError(f"{label} must be a positive ordinal")
    return parsed


def _one_of(value: Any, allowed: tuple[str, ...], label: str) -> str:
    text = str(value or "")
    if text not in allowed:
        raise TelemetryRecordError(f"unknown {label}: {text!r}")
    return text


def _provider_value(value: Any, label: str) -> ProviderValue:
    if value is None:
        return ABSENT
    if not isinstance(value, ProviderValue):
        raise TelemetryRecordError(f"{label} must be a ProviderValue")
    if value.status not in VALUE_STATUSES:  # pragma: no cover - ProviderValue guards
        raise TelemetryRecordError(f"unknown status on {label}")
    return value


# ─────────────────────────── breaker snapshot ───────────────────────────


@dataclass(frozen=True)
class BreakerSnapshot:
    """An atomic, nullable reading of the circuit breaker.

    Blocker 6: "A snapshot failure must be unknown, not 'closed with zero
    failures.'" State and count are therefore taken together and either both
    succeed or the whole snapshot is ``unknown`` with a ``None`` count — the
    previous design's independent ``except: state = closed`` /
    ``except: failures = 0`` fallbacks manufactured a reading that was never
    taken.
    """

    state: str = BREAKER_UNKNOWN
    failure_count: Optional[int] = None
    status: str = "unknown"

    def __post_init__(self) -> None:
        set_ = lambda name, value: object.__setattr__(self, name, value)  # noqa: E731
        set_("state", _one_of(self.state, BREAKER_STATES, "breaker state"))
        set_("status", _one_of(self.status, ("valid", "unknown"), "breaker status"))
        if self.status == "unknown":
            if self.state != BREAKER_UNKNOWN or self.failure_count is not None:
                raise TelemetryRecordError(
                    "an unknown breaker snapshot must carry no state and no count"
                )
        else:
            if self.state == BREAKER_UNKNOWN:
                raise TelemetryRecordError(
                    "a valid breaker snapshot must name a state"
                )
            count = self.failure_count
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise TelemetryRecordError(
                    "a valid breaker snapshot must carry a nonnegative count"
                )

    @classmethod
    def unknown(cls) -> "BreakerSnapshot":
        return cls()

    @classmethod
    def observed(cls, *, state: str, failure_count: int) -> "BreakerSnapshot":
        return cls(state=state, failure_count=failure_count, status="valid")


UNKNOWN_BREAKER = BreakerSnapshot.unknown()


# ─────────────────────────── run / call envelopes ───────────────────────────


@dataclass(frozen=True)
class TelemetryRunRecord:
    """The run envelope: what work this run *expects* to produce.

    Attempt rows alone cannot prove that a call is missing — a call that was
    never made and a call whose telemetry was lost look identical. This record,
    plus the per-call envelope below, is what makes a wholly absent call
    detectable (blocker 9).
    """

    telemetry_run_id: str = field(default_factory=new_uuid)
    posture: str = POSTURE_OBSERVATIONAL
    telemetry_required: bool = False
    entry_point: str = "unknown"
    project_uuid: Optional[str] = None
    external_project_id: str = ""
    external_run_id: str = ""
    job_id: str = ""
    source_commit: str = ""
    schema_version: int = TELEMETRY_SCHEMA_VERSION
    runtime_fingerprint: str = _EMPTY_FINGERPRINT
    expected_phases: tuple[str, ...] = ()
    started_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        set_ = lambda name, value: object.__setattr__(self, name, value)  # noqa: E731
        set_("posture", _one_of(self.posture, POSTURES, "posture"))
        set_("telemetry_required", bool(self.telemetry_required))
        set_("entry_point", _label(self.entry_point, max_chars=64))
        set_("external_project_id", _label(self.external_project_id))
        set_("external_run_id", _label(self.external_run_id))
        set_("job_id", _label(self.job_id))
        set_("source_commit", _label(self.source_commit, max_chars=64))
        set_("runtime_fingerprint", _fingerprint(self.runtime_fingerprint, "runtime_fingerprint"))
        set_("schema_version", int(self.schema_version))
        set_("expected_phases", tuple(_label(p, max_chars=64) for p in self.expected_phases))
        set_("started_at", require_utc(self.started_at, "started_at", required=True))
        if self.posture == POSTURE_STRICT and not self.telemetry_required:
            raise TelemetryRecordError("strict posture requires telemetry_required")


@dataclass(frozen=True)
class TelemetryCallRecord:
    """One logical model call: everything one gateway ``call()`` will attempt."""

    call_id: str
    telemetry_run_id: str
    posture: str
    identity: TelemetryIdentity
    worker_id: str
    requested_provider: str
    requested_model: str
    request_config_fingerprint: str = _EMPTY_FINGERPRINT
    routing_decision_fingerprint: str = _EMPTY_FINGERPRINT
    candidate_count: int = 1
    started_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        set_ = lambda name, value: object.__setattr__(self, name, value)  # noqa: E731
        set_("posture", _one_of(self.posture, POSTURES, "posture"))
        set_("worker_id", _label(self.worker_id))
        set_("requested_provider", _label(self.requested_provider, max_chars=64))
        set_("requested_model", _label(self.requested_model, max_chars=128))
        set_(
            "request_config_fingerprint",
            _fingerprint(self.request_config_fingerprint, "request_config_fingerprint"),
        )
        set_(
            "routing_decision_fingerprint",
            _fingerprint(self.routing_decision_fingerprint, "routing_decision_fingerprint"),
        )
        count = int(self.candidate_count or 0)
        if count < 1:
            raise TelemetryRecordError("candidate_count must be a positive ordinal")
        set_("candidate_count", count)
        set_("started_at", require_utc(self.started_at, "started_at", required=True))
        if not isinstance(self.identity, TelemetryIdentity):
            raise TelemetryRecordError("identity must be a TelemetryIdentity")


# ─────────────────────────── starts ───────────────────────────


@dataclass(frozen=True)
class SdkInvocationRecord:
    """One gateway attempt: a frozen candidate at a frozen retry ordinal.

    The candidate and the routing decision are *already made* when this record
    is constructed. Nothing in this package selects, reorders or re-evaluates
    them; ``routing_decision_fingerprint`` exists so an auditor can prove the
    decision recorded here is the decision the gateway acted on.
    """

    invocation_id: str
    call_id: str
    telemetry_run_id: str
    posture: str
    identity: TelemetryIdentity
    worker_id: str
    invocation_kind: str
    provider: str
    requested_model: str
    candidate_ordinal: int
    retry_ordinal: int
    attempt_ordinal: int
    breaker_before: BreakerSnapshot = UNKNOWN_BREAKER
    fallback_candidate: bool = False
    fallback_from_provider: str = ""
    fallback_from_model: str = ""
    request_config_fingerprint: str = _EMPTY_FINGERPRINT
    routing_decision_fingerprint: str = _EMPTY_FINGERPRINT
    started_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        set_ = lambda name, value: object.__setattr__(self, name, value)  # noqa: E731
        set_("posture", _one_of(self.posture, POSTURES, "posture"))
        set_("invocation_kind", _one_of(self.invocation_kind, INVOCATION_KINDS, "invocation_kind"))
        set_("worker_id", _label(self.worker_id))
        set_("provider", _label(self.provider, max_chars=64))
        set_("requested_model", _label(self.requested_model, max_chars=128))
        if not self.provider or not self.requested_model:
            raise TelemetryRecordError("provider and requested_model are required")
        set_("candidate_ordinal", _positive_ordinal(self.candidate_ordinal, "candidate_ordinal"))
        set_("retry_ordinal", _positive_ordinal(self.retry_ordinal, "retry_ordinal"))
        set_("attempt_ordinal", _positive_ordinal(self.attempt_ordinal, "attempt_ordinal"))
        set_("fallback_candidate", bool(self.fallback_candidate))
        set_("fallback_from_provider", _label(self.fallback_from_provider, max_chars=64))
        set_("fallback_from_model", _label(self.fallback_from_model, max_chars=128))
        set_(
            "request_config_fingerprint",
            _fingerprint(self.request_config_fingerprint, "request_config_fingerprint"),
        )
        set_(
            "routing_decision_fingerprint",
            _fingerprint(self.routing_decision_fingerprint, "routing_decision_fingerprint"),
        )
        set_("started_at", require_utc(self.started_at, "started_at", required=True))
        if not isinstance(self.breaker_before, BreakerSnapshot):
            raise TelemetryRecordError("breaker_before must be a BreakerSnapshot")
        if not isinstance(self.identity, TelemetryIdentity):
            raise TelemetryRecordError("identity must be a TelemetryIdentity")
        if self.fallback_candidate and not self.fallback_from_provider:
            raise TelemetryRecordError(
                "a fallback candidate must name the candidate it fell back from"
            )


@dataclass(frozen=True)
class HttpAttemptRecord:
    """One actual HTTP request to a provider — the fail-closed start.

    Blocker 3: an SDK ``create(...)`` that performs three HTTP requests produces
    three of these, each with its own identity and its own
    ``http_retry_ordinal``. The SDK's retry policy is untouched; this record
    only observes it.
    """

    attempt_id: str
    invocation_id: str
    call_id: str
    telemetry_run_id: str
    posture: str
    worker_id: str
    provider: str
    requested_model: str
    http_retry_ordinal: int
    request_started_at: datetime
    request_method: str = "POST"
    request_path: str = ""

    def __post_init__(self) -> None:
        set_ = lambda name, value: object.__setattr__(self, name, value)  # noqa: E731
        set_("posture", _one_of(self.posture, POSTURES, "posture"))
        set_("worker_id", _label(self.worker_id))
        set_("provider", _label(self.provider, max_chars=64))
        set_("requested_model", _label(self.requested_model, max_chars=128))
        set_(
            "http_retry_ordinal",
            _positive_ordinal(self.http_retry_ordinal, "http_retry_ordinal"),
        )
        set_("request_method", _label(self.request_method, max_chars=16).upper())
        # The path only — never the query string, which is where a provider SDK
        # or a proxy would carry a token.
        path = _label(self.request_path, max_chars=128).split("?", 1)[0]
        set_("request_path", path)
        set_(
            "request_started_at",
            require_utc(self.request_started_at, "request_started_at", required=True),
        )
        if not self.provider or not self.requested_model:
            raise TelemetryRecordError("provider and requested_model are required")


# ─────────────────────────── events ───────────────────────────


@dataclass(frozen=True)
class ProviderObservation:
    """Provider-reported metadata, each field with an explicit status."""

    provider_response_id: ProviderValue = ABSENT
    effective_model: ProviderValue = ABSENT
    stop_reason: ProviderValue = ABSENT
    input_tokens: ProviderValue = ABSENT
    output_tokens: ProviderValue = ABSENT
    cache_read_tokens: ProviderValue = ABSENT
    cache_creation_tokens: ProviderValue = ABSENT

    def __post_init__(self) -> None:
        for name in OBSERVABLE_PROVIDER_FIELDS:
            object.__setattr__(self, name, _provider_value(getattr(self, name), name))

    @classmethod
    def all_absent(cls) -> "ProviderObservation":
        return cls()

    @property
    def is_empty(self) -> bool:
        """True when nothing was observed — every field absent or unsupported."""
        return all(
            getattr(self, name).status in ("absent", "unsupported")
            for name in OBSERVABLE_PROVIDER_FIELDS
        )

    def as_payload(self) -> dict[str, Any]:
        return {
            name: getattr(self, name).as_payload() for name in OBSERVABLE_PROVIDER_FIELDS
        }

    def metadata_fingerprint(self) -> str:
        return canonical_fingerprint(self.as_payload())


@dataclass(frozen=True)
class AttemptEvent:
    """One appended observation about a start. Never replaces an earlier one.

    ``event_ordinal`` is assigned by the producer and is unique per subject, so
    two events cannot silently collapse and an out-of-order arrival is visible
    rather than corrective.
    """

    event_id: str
    subject_kind: str
    subject_id: str
    call_id: str
    telemetry_run_id: str
    event_kind: str
    event_ordinal: int
    observed_at: datetime
    worker_id: str = ""
    transport_outcome: str = ""
    http_status: ProviderValue = ABSENT
    provider_request_id: ProviderValue = ABSENT
    retry_after: ProviderValue = ABSENT
    observation: ProviderObservation = field(default_factory=ProviderObservation)
    breaker_after: BreakerSnapshot = UNKNOWN_BREAKER
    error_category: str = ""
    error_identity: str = ""
    failure_class: str = ""
    # Left empty by default so __post_init__ can derive it from the observation:
    # an event that carries provider metadata must fingerprint that metadata, not
    # the zero digest.
    response_metadata_fingerprint: str = ""
    schema_version: int = TELEMETRY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        set_ = lambda name, value: object.__setattr__(self, name, value)  # noqa: E731
        set_("subject_kind", _one_of(self.subject_kind, SUBJECT_KINDS, "subject_kind"))
        set_("event_kind", _one_of(self.event_kind, EVENT_KINDS, "event_kind"))
        set_("event_ordinal", _positive_ordinal(self.event_ordinal, "event_ordinal"))
        set_("observed_at", require_utc(self.observed_at, "observed_at", required=True))
        set_("worker_id", _label(self.worker_id))
        if self.transport_outcome:
            set_(
                "transport_outcome",
                _one_of(self.transport_outcome, TRANSPORT_OUTCOMES, "transport_outcome"),
            )
        for name in ("http_status", "provider_request_id", "retry_after"):
            set_(name, _provider_value(getattr(self, name), name))
        if not isinstance(self.observation, ProviderObservation):
            raise TelemetryRecordError("observation must be a ProviderObservation")
        if not isinstance(self.breaker_after, BreakerSnapshot):
            raise TelemetryRecordError("breaker_after must be a BreakerSnapshot")
        set_("error_category", _label(self.error_category, max_chars=64))
        set_("error_identity", _label(self.error_identity, max_chars=256))
        set_("failure_class", _label(self.failure_class, max_chars=64))
        fingerprint = self.response_metadata_fingerprint
        if fingerprint in ("", None):
            fingerprint = self.observation.metadata_fingerprint()
        set_(
            "response_metadata_fingerprint",
            _fingerprint(fingerprint, "response_metadata_fingerprint"),
        )
        set_("schema_version", int(self.schema_version))

    @property
    def is_terminal(self) -> bool:
        return self.event_kind in TERMINAL_EVENT_KINDS


@dataclass(frozen=True)
class RunEvent:
    """A run-level lifecycle event: worker registration, drain, reconciliation."""

    event_id: str
    telemetry_run_id: str
    event_kind: str
    worker_id: str
    posture: str
    observed_at: datetime
    started_events: int = 0
    terminal_events: int = 0
    unmatched_starts: int = 0
    undurable_events: int = 0
    ambiguous_events: int = 0
    dropped_events: int = 0
    expected_calls: int = 0
    observed_calls: int = 0
    drain_status: str = DRAIN_UNKNOWN
    reconciliation_status: str = RECONCILIATION_PENDING
    # The digest of the run's expected-work manifest. A `complete`
    # reconciliation must carry it, so a completeness claim is bound to the
    # exact set of work the run said it would do — and cannot be re-read later
    # as a claim about some other, smaller set.
    expected_work_digest: str = ""
    detail: str = ""

    def __post_init__(self) -> None:
        set_ = lambda name, value: object.__setattr__(self, name, value)  # noqa: E731
        set_("event_kind", _one_of(self.event_kind, RUN_EVENT_KINDS, "run event_kind"))
        set_("posture", _one_of(self.posture, POSTURES, "posture"))
        set_("worker_id", _label(self.worker_id))
        set_("observed_at", require_utc(self.observed_at, "observed_at", required=True))
        set_("drain_status", _one_of(self.drain_status, DRAIN_STATUSES, "drain_status"))
        set_(
            "reconciliation_status",
            _one_of(self.reconciliation_status, RECONCILIATION_STATUSES, "reconciliation_status"),
        )
        set_("detail", _label(self.detail, max_chars=256))
        digest = str(self.expected_work_digest or "")
        if digest:
            set_("expected_work_digest", _fingerprint(digest, "expected_work_digest"))
        else:
            set_("expected_work_digest", "")
        if (
            self.event_kind == RUN_EVENT_RECONCILIATION
            and self.reconciliation_status == RECONCILIATION_COMPLETE
            and not self.expected_work_digest
        ):
            raise TelemetryRecordError(
                "a complete reconciliation must bind the expected-work manifest digest"
            )
        for name in (
            "started_events",
            "terminal_events",
            "unmatched_starts",
            "undurable_events",
            "ambiguous_events",
            "dropped_events",
            "expected_calls",
            "observed_calls",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise TelemetryRecordError(f"{name} must be a nonnegative integer")


def new_identity() -> str:
    """A fresh identity for a run, call, invocation, attempt or event."""
    return new_uuid()


__all__ = [name for name in dir() if not name.startswith("_")]
