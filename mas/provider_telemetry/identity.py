"""The telemetry identity model.

The audit's blocker 8 was that "known identities are missing or unusable on
supported entry points" and blocker "do not cast non-UUID evaluation identifiers
into PostgreSQL UUID columns". Both come from the same mistake: treating one
`project_id` string as if it were simultaneously a relational key, an external
label and a run name.

This module separates them for good. Each level is a distinct field with a
distinct type discipline:

``project_uuid``
    Optional relational key. Only ever a genuine UUID; anything else is refused
    here and stored as ``external_project_id`` instead, so no code path can hand
    ``"eval-suite-3"`` to a PostgreSQL ``UUID`` column.
``external_project_id``
    Free-form external project identity (evaluation suites, CLI labels). TEXT.
``run_id`` / ``job_id`` / ``call_id`` / ``phase``
    Workflow-level identities. TEXT — deliberately not UUID-typed, because a CLI
    run and an evaluation run legitimately name themselves.
``worker_id``
    Process/worker identity, minted once per process.
``sdk_invocation_id`` / ``http_attempt_id``
    Minted per SDK ``create(...)`` call and per actual HTTP request; these are
    genuine UUIDs because this package alone mints them.

Truthful absence is a first-class answer: a caller with no run identity records
an empty ``run_id`` rather than an invented one. What is *not* acceptable is a
supported entry point that *has* an identity and drops it, which is what the
entry-point matrix below exists to prevent.
"""
from __future__ import annotations

import contextlib
import contextvars
import os
import socket
import uuid
from dataclasses import dataclass, replace
from typing import Iterator, Optional

# ─────────────────────────── entry points ───────────────────────────
#
# Closed vocabulary. An entry point absent from this tuple cannot be recorded,
# which is what makes `test_entry_point_matrix` able to assert completeness
# rather than merely assert that whatever was passed round-tripped.

ENTRY_POINT_API_WORKFLOW_RUN = "api_workflow_run"
ENTRY_POINT_API_MANUAL_PHASE = "api_manual_phase"
ENTRY_POINT_CLI_WORKFLOW = "cli_workflow"
ENTRY_POINT_CLI_SINGLE_PHASE = "cli_single_phase"
ENTRY_POINT_EVALUATION_PHASE = "evaluation_phase"
ENTRY_POINT_EVALUATION_JUDGE = "evaluation_judge"
ENTRY_POINT_CDP_REPORT_GATEWAY = "cdp_report_gateway"
ENTRY_POINT_T1A_VALIDATION = "t1a_validation"
ENTRY_POINT_DIRECT_GATEWAY = "direct_gateway"
ENTRY_POINT_UNKNOWN = "unknown"

ENTRY_POINTS = (
    ENTRY_POINT_API_WORKFLOW_RUN,
    ENTRY_POINT_API_MANUAL_PHASE,
    ENTRY_POINT_CLI_WORKFLOW,
    ENTRY_POINT_CLI_SINGLE_PHASE,
    ENTRY_POINT_EVALUATION_PHASE,
    ENTRY_POINT_EVALUATION_JUDGE,
    ENTRY_POINT_CDP_REPORT_GATEWAY,
    ENTRY_POINT_T1A_VALIDATION,
    ENTRY_POINT_DIRECT_GATEWAY,
    ENTRY_POINT_UNKNOWN,
)

# Entry points that must carry a truthful run identity when one exists. The
# integration tests iterate exactly this tuple, so adding a supported entry
# point without wiring identity into it fails the suite.
IDENTITY_BEARING_ENTRY_POINTS = (
    ENTRY_POINT_API_WORKFLOW_RUN,
    ENTRY_POINT_API_MANUAL_PHASE,
    ENTRY_POINT_CLI_WORKFLOW,
    ENTRY_POINT_CLI_SINGLE_PHASE,
    ENTRY_POINT_EVALUATION_PHASE,
    ENTRY_POINT_EVALUATION_JUDGE,
    ENTRY_POINT_CDP_REPORT_GATEWAY,
    ENTRY_POINT_T1A_VALIDATION,
)

_MAX_IDENTITY_CHARS = 128


class TelemetryIdentityError(ValueError):
    """An identity value was supplied in a shape this model refuses to store."""


def new_uuid() -> str:
    """A fresh identity minted by this package. Always a genuine UUID."""
    return str(uuid.uuid4())


def as_project_uuid(value: object) -> Optional[str]:
    """Return ``value`` as a canonical UUID string, or ``None``.

    ``None`` is the honest answer for an identifier that is not a UUID — the
    caller's external identity is preserved separately. Returning ``None`` here
    is what stops a non-UUID evaluation identifier from reaching a ``UUID``
    column and raising at INSERT time (or, worse, being coerced).
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return str(uuid.UUID(text))
    except (ValueError, AttributeError, TypeError):
        return None


def _label(value: object) -> str:
    """Bound and flatten a free-form identity label.

    Newlines and control characters are dropped rather than stored: an identity
    label is written into export artifacts and log lines, and a value able to
    carry a line break can forge a record boundary there.
    """
    if value is None:
        return ""
    text = str(value)
    text = "".join(ch for ch in text if ch.isprintable())
    text = " ".join(text.split())
    return text[:_MAX_IDENTITY_CHARS]


@dataclass(frozen=True)
class TelemetryIdentity:
    """Everything known about *who* is making a provider call."""

    entry_point: str = ENTRY_POINT_UNKNOWN
    project_uuid: Optional[str] = None
    external_project_id: str = ""
    run_id: str = ""
    job_id: str = ""
    phase: str = ""

    def __post_init__(self) -> None:
        set_ = lambda name, value: object.__setattr__(self, name, value)  # noqa: E731
        entry_point = str(self.entry_point or ENTRY_POINT_UNKNOWN)
        if entry_point not in ENTRY_POINTS:
            raise TelemetryIdentityError(f"unknown entry point: {entry_point!r}")
        set_("entry_point", entry_point)
        set_("project_uuid", as_project_uuid(self.project_uuid))
        set_("external_project_id", _label(self.external_project_id))
        set_("run_id", _label(self.run_id))
        set_("job_id", _label(self.job_id))
        set_("phase", _label(self.phase))

    @property
    def has_run_identity(self) -> bool:
        return bool(self.run_id)

    def merged(self, **updates: object) -> "TelemetryIdentity":
        """Overlay only the fields the caller actually supplied.

        An entry point that knows the phase but not the run must not blank the
        run identity an outer scope already established.
        """
        supplied = {
            name: value
            for name, value in updates.items()
            if value not in (None, "")
        }
        if not supplied:
            return self
        return replace(self, **supplied)  # type: ignore[arg-type]

    def as_payload(self) -> dict[str, object]:
        return {
            "entry_point": self.entry_point,
            "project_uuid": self.project_uuid,
            "external_project_id": self.external_project_id,
            "run_id": self.run_id,
            "job_id": self.job_id,
            "phase": self.phase,
        }


EMPTY_IDENTITY = TelemetryIdentity()

_identity: contextvars.ContextVar[TelemetryIdentity] = contextvars.ContextVar(
    "provider_telemetry_identity", default=EMPTY_IDENTITY
)


@contextlib.contextmanager
def bind_identity(
    *,
    entry_point: Optional[str] = None,
    project_id: object = None,
    project_uuid: object = None,
    external_project_id: object = None,
    run_id: object = None,
    job_id: object = None,
    phase: object = None,
) -> Iterator[TelemetryIdentity]:
    """Bind (or extend) telemetry identity for the enclosing scope.

    ``project_id`` is the convenience form used by call sites that hold a single
    project identifier and do not know whether it is relational: it is routed to
    ``project_uuid`` when it parses as a UUID and to ``external_project_id``
    otherwise, so no caller has to make that judgement.
    """
    updates: dict[str, object] = {}
    if entry_point is not None:
        updates["entry_point"] = entry_point
    if project_uuid is not None:
        updates["project_uuid"] = project_uuid
    if external_project_id is not None:
        updates["external_project_id"] = external_project_id
    if project_id is not None:
        resolved = as_project_uuid(project_id)
        if resolved is not None:
            updates.setdefault("project_uuid", resolved)
        elif str(project_id).strip():
            updates.setdefault("external_project_id", project_id)
    if run_id is not None:
        updates["run_id"] = run_id
    if job_id is not None:
        updates["job_id"] = job_id
    if phase is not None:
        updates["phase"] = phase

    identity = _identity.get().merged(**updates)
    token = _identity.set(identity)
    try:
        yield identity
    finally:
        _identity.reset(token)


def current_identity() -> TelemetryIdentity:
    return _identity.get()


# ─────────────────────────── worker identity ───────────────────────────
#
# One per process, minted at import. A run's attestation lists every worker that
# contributed, and reconciliation is per-worker, so this value has to be stable
# for the process's whole life and unique across processes on one host.

def _mint_worker_id() -> str:
    try:
        host = socket.gethostname()
    except Exception:  # pragma: no cover - exotic host configuration
        host = "unknown-host"
    return _label(f"{host}:{os.getpid()}:{uuid.uuid4().hex[:12]}")


WORKER_ID = _mint_worker_id()


def worker_id() -> str:
    return WORKER_ID
