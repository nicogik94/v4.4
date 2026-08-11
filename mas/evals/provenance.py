"""Eval failure provenance — schema ``eval_provenance.v1``.

Why this exists
───────────────
A release eval that reports ``4/12 passed`` is only interpretable if an auditor
can tell eight independent analytical-quality failures from eight cases whose
model returned nothing to analyse. The V7 live observation could not: its
artifacts carried a score, a judge rationale and deterministic check results,
and nothing at all about what the provider actually returned. Whether a phase
saw an empty response, whether a structured repair was issued, whether the run
kept going after a phase terminally failed — none of it survived into the
report, so the score could not be attributed to a cause.

This module records that evidence. It records **only** evidence:

* it never changes what the eval executes, in what order, or how long for;
* it never changes ``CaseResult.passed``, the pass rate, the threshold or
  ``summary.ok``; the provenance below is informational and is read by nobody
  who decides anything;
* it never stores a prompt, a response, a refusal, reasoning text, a header, a
  credential, or a digest of any of them. Visible output is recorded as a status
  and a character count, and a refusal as a status alone.

How it collects
───────────────
The runtime already observes every provider attempt; what it lacked here was
somewhere to put the observation. The recorder below is two things at once:

1. an **eval-local sink** for the runtime's own attempt telemetry — the harness
   binds it for the duration of a case, so invocation starts and attempt events
   land in eval memory instead of PostgreSQL (which CI does not have, and which
   no uploaded artifact could carry anyway);
2. an **observer** for response-shape metadata — content status, visible length,
   refusal status and reasoning tokens — which the durable relations have no
   column for and which the adapter discards a few lines after receiving it.

The two channels join on ``invocation_id``. Everything is bounded: a case that
somehow produced thousands of attempts records the first few hundred and says
so, rather than growing without limit inside a CI job.

Epistemics
──────────
Provider-sourced fields keep their status. ``missing`` (the container could not
be reached), ``absent`` (the container was there and did not carry the field),
``null``, ``empty``, ``nonempty``, ``valid``, ``unsupported`` (the API has no
such field at all), ``invalid`` and ``unknown`` are nine different facts and are
never collapsed into each other. A reasoning-token count is an exact integer
only when the provider reported one; there is no path here that infers one.

Fields this build cannot observe without changing a certified runtime file are
recorded as ``unknown`` with a reason, never guessed. ``strategy_recovery_*`` is
the standing example: the deterministic truncated-payload repair happens inside
the orchestrator and leaves no signal outside it.
"""
from __future__ import annotations

import os
from typing import Any, Iterable, Optional

SCHEMA_VERSION = "eval_provenance.v1"

# Explicitly enabled where it is wanted, off everywhere else. There is no code
# path that turns this on implicitly.
PROVENANCE_ENV = "MAS_EVAL_PROVENANCE"
_TRUE = {"1", "true", "yes", "on"}

# ─────────────────────────── closed vocabularies ───────────────────────────

STATUS_MISSING = "missing"
STATUS_ABSENT = "absent"
STATUS_NULL = "null"
STATUS_EMPTY = "empty"
STATUS_NONEMPTY = "nonempty"
STATUS_VALID = "valid"
STATUS_INVALID = "invalid"
STATUS_UNSUPPORTED = "unsupported"
STATUS_UNKNOWN = "unknown"

VALUE_STATUSES = (
    STATUS_MISSING,
    STATUS_ABSENT,
    STATUS_NULL,
    STATUS_EMPTY,
    STATUS_NONEMPTY,
    STATUS_VALID,
    STATUS_INVALID,
    STATUS_UNSUPPORTED,
    STATUS_UNKNOWN,
)

CAPTURE_MODE_TELEMETRY = "telemetry_observational"
CAPTURE_MODE_DISABLED = "disabled"
CAPTURE_MODE_MOCK = "mock"
CAPTURE_MODE_DEFERRED = "deferred_to_process_posture"
CAPTURE_MODES = (
    CAPTURE_MODE_TELEMETRY,
    CAPTURE_MODE_DISABLED,
    CAPTURE_MODE_MOCK,
    CAPTURE_MODE_DEFERRED,
)

PHASE_COMPLETED = "completed"
PHASE_STRUCTURAL_FAILURE = "structural_failure"
PHASE_SKIPPED = "skipped"
PHASE_EXPECTED_HALT = "expected_halt"
PHASE_UNKNOWN = "unknown"
PHASE_FINAL_STATUSES = (
    PHASE_COMPLETED,
    PHASE_STRUCTURAL_FAILURE,
    PHASE_SKIPPED,
    PHASE_EXPECTED_HALT,
    PHASE_UNKNOWN,
)

PARSE_PARSED = "parsed"
PARSE_FAILED = "parse_failed"
PARSE_NOT_REACHED = "not_reached"
PARSE_UNKNOWN = "unknown"
PARSE_RESULTS = (PARSE_PARSED, PARSE_FAILED, PARSE_NOT_REACHED, PARSE_UNKNOWN)

OUTCOME_SUCCEEDED = "succeeded"
OUTCOME_FAILED = "failed"
OUTCOME_NOT_ATTEMPTED = "not_attempted"
OUTCOME_UNKNOWN = "unknown"
OUTCOMES = (OUTCOME_SUCCEEDED, OUTCOME_FAILED, OUTCOME_NOT_ATTEMPTED, OUTCOME_UNKNOWN)

# The orchestrator's own failure categories, allow-listed so a category this
# build does not recognise becomes `other` rather than an unbounded string
# copied out of a diagnostic.
STRUCTURAL_FAILURE_KINDS = (
    "json_parse",
    "json_shape",
    "schema_validation",
    "missing_output",
    "provider_error",
    "quota_exceeded",
    "policy_blocked",
    "phase_configuration",
    "none",
    "other",
)
_PARSE_FAILURE_KINDS = frozenset({"json_parse", "json_shape"})
_STRUCTURED_FAILURE_KINDS = frozenset(
    {"json_parse", "json_shape", "schema_validation", "missing_output"}
)
_PROVIDER_FAILURE_KINDS = frozenset({"provider_error", "quota_exceeded"})

FAILURE_ANALYTICAL = "analytical_quality_failure"
FAILURE_STRUCTURAL = "structural_output_failure"
FAILURE_PROVIDER = "provider_failure"
FAILURE_AGGREGATION = "aggregation_failure"
FAILURE_UNKNOWN = "unknown"
FAILURE_NONE = "none"
FAILURE_PROVENANCE_CATEGORIES = (
    FAILURE_ANALYTICAL,
    FAILURE_STRUCTURAL,
    FAILURE_PROVIDER,
    FAILURE_AGGREGATION,
    FAILURE_UNKNOWN,
    FAILURE_NONE,
)

# Normalized stop reasons that mean "the model ran out of room", across both
# provider vocabularies.
LENGTH_STOP_REASONS = frozenset({"length", "max_tokens"})

HALT_CONFUSED = "confused_classification"
_CONFUSED_HALT_MARKER = "workflow halted after Confused classification"

JUDGE_PHASE = "eval_judge"

# Bounds. A run that exceeds one records the overflow as a note rather than
# growing without limit inside a CI job.
MAX_CALLS = 128
MAX_INVOCATIONS = 256
MAX_EVENTS = 1024
MAX_SHAPES = 256

NOTE_CALL_CAP = "call_cap_reached"
NOTE_INVOCATION_CAP = "invocation_cap_reached"
NOTE_EVENT_CAP = "event_cap_reached"
NOTE_SHAPE_CAP = "shape_cap_reached"
NOTE_RECORDER_FAULT = "recorder_fault"
NOTE_HARNESS_EXCEPTION = "harness_exception_recorded"

# Fields this build cannot observe from outside a certified runtime file.
UNOBSERVABLE_IN_ORCHESTRATOR = "not_observable_outside_certified_orchestrator"


def provenance_enabled(environ: Optional[dict] = None) -> bool:
    """True only when a caller asked for provenance explicitly."""
    source = os.environ if environ is None else environ
    return str(source.get(PROVENANCE_ENV, "") or "").strip().lower() in _TRUE


# ─────────────────────────── value envelopes ───────────────────────────


def value(status: str, *, val: Any = None, detail: str = "") -> dict[str, Any]:
    """One provenance field: a status, and a value only when it carries one."""
    return {
        "status": status if status in VALUE_STATUSES else STATUS_UNKNOWN,
        "value": val,
        "detail": _token(detail),
    }


def unknown(detail: str = "") -> dict[str, Any]:
    return value(STATUS_UNKNOWN, detail=detail)


def _token(text: Any, *, limit: int = 64) -> str:
    """Bound a diagnostic note to a short, printable, single-line token."""
    cleaned = "".join(
        ch for ch in str(text or "") if ch.isprintable() and ch != " "
    )
    return cleaned[:limit]


def _label(text: Any, *, limit: int = 128) -> str:
    cleaned = "".join(ch for ch in str(text or "") if ch.isprintable())
    return " ".join(cleaned.split())[:limit]


def _ordinal(raw: Any) -> Optional[int]:
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else None


def _from_provider_value(raw: Any) -> dict[str, Any]:
    """Translate a runtime provider value into this schema's envelope.

    The runtime's statuses (``absent``/``null``/``valid``/``invalid``/
    ``unsupported``/``redacted``/``unknown_value``) are a superset of the ones
    kept here; the two that have no counterpart become ``invalid`` with the
    original status as the reason, so nothing is silently reported as a value
    that was never read.
    """
    status = _token(getattr(raw, "status", "") or "")
    stored = getattr(raw, "stored", None)
    detail = _token(getattr(raw, "detail", "") or "")
    if status == "valid":
        return value(STATUS_VALID, val=stored, detail=detail)
    if status in (STATUS_ABSENT, STATUS_NULL, STATUS_UNSUPPORTED, STATUS_INVALID):
        return value(status, detail=detail)
    if not status:
        return unknown("unreadable")
    return value(STATUS_INVALID, detail=detail or status)


def _shape_field(shape: Optional[dict], name: str) -> dict[str, Any]:
    if not isinstance(shape, dict):
        return unknown("no_shape_record")
    field = shape.get(name)
    if not isinstance(field, dict):
        return unknown("no_shape_field")
    return value(
        _token(field.get("status") or STATUS_UNKNOWN),
        val=field.get("value"),
        detail=_token(field.get("detail") or ""),
    )


# ─────────────────────────── the recorder ───────────────────────────


class EvalProvenanceRecorder:
    """Bounded, in-memory collector for one eval case.

    Doubles as the eval-local sink for the runtime's attempt telemetry and as
    the observer for response-shape metadata. Every entry point is total: a
    malformed record is counted and dropped, never raised, because this object
    sits on the provider call path and a recorder defect must not be able to
    change a provider result, a retry, a fallback, a breaker transition or a
    cache decision.
    """

    def __init__(self, case_id: str = "") -> None:
        self.case_id = _label(case_id, limit=64)
        self.calls: list[dict[str, Any]] = []
        self.invocations: list[dict[str, Any]] = []
        self.events: dict[str, list[dict[str, Any]]] = {}
        self.shapes: dict[str, dict[str, Any]] = {}
        self.judge_record: dict[str, Any] = {}
        self.notes: list[str] = []
        self.event_count = 0

    # ── notes ──

    def note(self, token: str) -> None:
        cleaned = _token(token)
        if cleaned and cleaned not in self.notes:
            self.notes.append(cleaned)

    # ── sink protocol (awaited by the runtime before transport) ──

    async def append_start(self, table: Any, record: Any) -> None:
        """Absorb an invocation/call start. Never raises, never blocks.

        The record is classified by what it carries rather than by which
        relation it was headed for, so a caller that renames a relation cannot
        silently turn this into a no-op.
        """
        try:
            self._absorb_start(record)
        except Exception:  # noqa: BLE001 - the isolation boundary itself
            self.note(NOTE_RECORDER_FAULT)

    async def append_event(self, event: Any) -> None:
        """Absorb one attempt event. Run-level events are not eval evidence."""
        try:
            self._absorb_event(event)
        except Exception:  # noqa: BLE001 - the isolation boundary itself
            self.note(NOTE_RECORDER_FAULT)

    # ── response-shape observer ──

    def record_response_shape(self, payload: Any) -> None:
        try:
            if not isinstance(payload, dict):
                return
            invocation_id = _label(payload.get("invocation_id") or "", limit=64)
            if not invocation_id:
                return
            if invocation_id in self.shapes:
                # Append-only in spirit: the first shape observed for an
                # invocation is the one the adapter saw, and a later one would
                # be describing something else.
                return
            if len(self.shapes) >= MAX_SHAPES:
                self.note(NOTE_SHAPE_CAP)
                return
            self.shapes[invocation_id] = {
                name: payload.get(name)
                for name in (
                    "content_status",
                    "visible_content_length",
                    "refusal_status",
                    "reasoning_tokens",
                )
            }
        except Exception:  # noqa: BLE001 - the isolation boundary itself
            self.note(NOTE_RECORDER_FAULT)

    # ── classification ──

    def _absorb_start(self, record: Any) -> None:
        invocation_id = getattr(record, "invocation_id", None)
        candidate_ordinal = getattr(record, "candidate_ordinal", None)
        if invocation_id and candidate_ordinal is not None:
            self._absorb_invocation(record)
            return
        if getattr(record, "call_id", None) and hasattr(record, "candidate_count"):
            self._absorb_call(record)
            return
        # A run envelope or an HTTP attempt start. Neither adds anything the
        # ledger reads, and neither is retained.

    def _absorb_call(self, record: Any) -> None:
        if len(self.calls) >= MAX_CALLS:
            self.note(NOTE_CALL_CAP)
            return
        identity = getattr(record, "identity", None)
        self.calls.append(
            {
                "call_ordinal": len(self.calls) + 1,
                "call_id": _label(getattr(record, "call_id", ""), limit=64),
                "phase": _label(getattr(identity, "phase", "") or "", limit=64),
                "requested_provider": _label(
                    getattr(record, "requested_provider", ""), limit=64
                ),
                "requested_model": _label(
                    getattr(record, "requested_model", ""), limit=128
                ),
                "candidate_count": _ordinal(getattr(record, "candidate_count", None)),
            }
        )

    def _absorb_invocation(self, record: Any) -> None:
        if len(self.invocations) >= MAX_INVOCATIONS:
            self.note(NOTE_INVOCATION_CAP)
            return
        identity = getattr(record, "identity", None)
        self.invocations.append(
            {
                "invocation_ordinal": len(self.invocations) + 1,
                "invocation_id": _label(getattr(record, "invocation_id", ""), limit=64),
                "call_id": _label(getattr(record, "call_id", ""), limit=64),
                "phase": _label(getattr(identity, "phase", "") or "", limit=64),
                "invocation_kind": _label(
                    getattr(record, "invocation_kind", ""), limit=32
                ),
                "provider": _label(getattr(record, "provider", ""), limit=64),
                "requested_model": _label(
                    getattr(record, "requested_model", ""), limit=128
                ),
                "candidate_ordinal": _ordinal(getattr(record, "candidate_ordinal", None)),
                "retry_ordinal": _ordinal(getattr(record, "retry_ordinal", None)),
                "attempt_ordinal": _ordinal(getattr(record, "attempt_ordinal", None)),
                "fallback_candidate": bool(getattr(record, "fallback_candidate", False)),
                "request_config_fingerprint": _token(
                    getattr(record, "request_config_fingerprint", ""), limit=64
                ),
                "routing_decision_fingerprint": _token(
                    getattr(record, "routing_decision_fingerprint", ""), limit=64
                ),
            }
        )

    def _absorb_event(self, event: Any) -> None:
        subject_kind = getattr(event, "subject_kind", None)
        if subject_kind != "sdk_invocation":
            return
        if self.event_count >= MAX_EVENTS:
            self.note(NOTE_EVENT_CAP)
            return
        subject_id = _label(getattr(event, "subject_id", ""), limit=64)
        if not subject_id:
            return
        observation = getattr(event, "observation", None)
        self.event_count += 1
        self.events.setdefault(subject_id, []).append(
            {
                "event_kind": _label(getattr(event, "event_kind", ""), limit=32),
                "event_ordinal": _ordinal(getattr(event, "event_ordinal", None)),
                "is_terminal": bool(getattr(event, "is_terminal", False)),
                "error_category": _token(getattr(event, "error_category", ""), limit=64),
                "failure_class": _token(getattr(event, "failure_class", ""), limit=64),
                "effective_model": _from_provider_value(
                    getattr(observation, "effective_model", None)
                ),
                "stop_reason": _from_provider_value(
                    getattr(observation, "stop_reason", None)
                ),
                "input_tokens": _from_provider_value(
                    getattr(observation, "input_tokens", None)
                ),
                "output_tokens": _from_provider_value(
                    getattr(observation, "output_tokens", None)
                ),
                "cache_read_tokens": _from_provider_value(
                    getattr(observation, "cache_read_tokens", None)
                ),
            }
        )

    # ── projection ──

    def invocation_records(self, *, phases: Iterable[str] = ()) -> list[dict[str, Any]]:
        """Per-invocation provenance, joined across both capture channels."""
        wanted = set(phases)
        records = []
        for entry in self.invocations:
            if wanted and entry["phase"] not in wanted:
                continue
            records.append(self._invocation_record(entry))
        return records

    def _invocation_record(self, entry: dict[str, Any]) -> dict[str, Any]:
        events = self.events.get(entry["invocation_id"], [])
        rich = _richest_event(events)
        terminal = next((event for event in events if event["is_terminal"]), None)
        shape = self.shapes.get(entry["invocation_id"])
        return {
            "case_id": self.case_id,
            "phase": entry["phase"],
            "logical_call_id": entry["call_id"],
            "invocation_id": entry["invocation_id"],
            "invocation_ordinal": entry["invocation_ordinal"],
            "invocation_kind": entry["invocation_kind"],
            "provider": entry["provider"],
            "requested_model": entry["requested_model"],
            "effective_model": rich["effective_model"] if rich else unknown("no_event"),
            "candidate_ordinal": entry["candidate_ordinal"],
            "retry_ordinal": entry["retry_ordinal"],
            "attempt_ordinal": entry["attempt_ordinal"],
            "fallback_candidate": entry["fallback_candidate"],
            # The gateway folds the selection reason and the task profile into
            # the routing fingerprint rather than exposing them on the record a
            # sink receives, so they are reported as unobserved and the
            # fingerprint is carried instead — it binds this attempt to the
            # decision the gateway acted on without inventing the label.
            "selection_reason": unknown("carried_in_routing_fingerprint"),
            "task_profile": unknown("carried_in_routing_fingerprint"),
            "request_config_fingerprint": entry["request_config_fingerprint"],
            "routing_decision_fingerprint": entry["routing_decision_fingerprint"],
            "stop_reason": rich["stop_reason"] if rich else unknown("no_event"),
            "input_tokens": rich["input_tokens"] if rich else unknown("no_event"),
            "output_tokens": rich["output_tokens"] if rich else unknown("no_event"),
            "cache_read_tokens": (
                rich["cache_read_tokens"] if rich else unknown("no_event")
            ),
            "reasoning_tokens": _shape_field(shape, "reasoning_tokens"),
            "content_status": _shape_field(shape, "content_status"),
            "visible_content_length": _shape_field(shape, "visible_content_length"),
            "refusal_status": _shape_field(shape, "refusal_status"),
            "terminal_event_kind": terminal["event_kind"] if terminal else "",
            "provider_error_category": terminal["error_category"] if terminal else "",
            "failure_class": terminal["failure_class"] if terminal else "",
        }


def _richest_event(events: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """The event carrying provider metadata, preferring an observation.

    Observations are appended before the terminal event and are never replaced,
    so the first one that actually read something off the response is the
    truthful source; a terminal event is the fallback when none did.
    """
    for event in events:
        if event["effective_model"]["status"] == STATUS_VALID:
            return event
    for event in events:
        if event["stop_reason"]["status"] == STATUS_VALID:
            return event
    return events[0] if events else None


# ─────────────────────────── the phase ledger ───────────────────────────


def _phase_status(output: Any, phase: str) -> str:
    statuses = output.get("phase_status") if isinstance(output, dict) else None
    if not isinstance(statuses, dict):
        return ""
    return _token(statuses.get(phase) or "", limit=32)


def _failure_kind(output: Any, phase: str) -> str:
    details = output.get("phase_failure_details") if isinstance(output, dict) else None
    if not isinstance(details, dict):
        return "none"
    entry = details.get(phase)
    if not isinstance(entry, dict):
        return "none"
    category = _token(entry.get("category") or "", limit=64)
    if not category:
        return "none"
    return category if category in STRUCTURAL_FAILURE_KINDS else "other"


def _halt_reason(output: Any) -> str:
    metadata = output.get("ingestion_metadata") if isinstance(output, dict) else None
    errors = metadata.get("eval_errors") if isinstance(metadata, dict) else None
    if not isinstance(errors, list):
        return ""
    for entry in errors:
        if _CONFUSED_HALT_MARKER in str(entry):
            return HALT_CONFUSED
    return ""


def _harness_exception_phases(output: Any, phases: Iterable[str]) -> list[str]:
    """Phases whose eval-harness ``except`` branch fired.

    The recorded message is a raw exception string and is deliberately not read:
    only the phase prefix, which comes from this harness's own closed list.
    """
    metadata = output.get("ingestion_metadata") if isinstance(output, dict) else None
    errors = metadata.get("eval_errors") if isinstance(metadata, dict) else None
    if not isinstance(errors, list):
        return []
    seen = []
    for entry in errors:
        text = str(entry)
        for phase in phases:
            if text.startswith(f"{phase}: ") and phase not in seen:
                seen.append(phase)
    return seen


def phase_ledger(
    *,
    output: Any,
    phases: Iterable[str],
    recorder: Optional[EvalProvenanceRecorder],
    invocation_records: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """One record per declared phase, in declared order."""
    ordered = list(phases)
    halt = _halt_reason(output)
    exception_phases = set(_harness_exception_phases(output, ordered))
    halted = False
    records: list[dict[str, Any]] = []
    all_calls = recorder.calls if recorder is not None else []
    if invocation_records is None:
        invocation_records = (
            recorder.invocation_records() if recorder is not None else []
        )

    for phase in ordered:
        status = _phase_status(output, phase)
        calls = [call for call in all_calls if call["phase"] == phase]
        invocations = [
            record for record in invocation_records if record["phase"] == phase
        ]
        started = bool(calls) or status in ("completed", "failed", "running")
        final_status = _final_status(status, started=started, halted=halted)
        kind = _failure_kind(output, phase)
        first = invocations[0] if invocations else None
        repair_attempted = len(calls) > 1
        records.append(
            {
                "phase": phase,
                "phase_started": started,
                "phase_final_status": final_status,
                "first_response_invocation_id": first["invocation_id"] if first else "",
                "first_response_status": (
                    first["content_status"] if first else unknown("no_invocation")
                ),
                "first_visible_content_length": (
                    first["visible_content_length"] if first else unknown("no_invocation")
                ),
                "first_refusal_status": (
                    first["refusal_status"] if first else unknown("no_invocation")
                ),
                "first_stop_reason": (
                    first["stop_reason"] if first else unknown("no_invocation")
                ),
                "first_parse_result": _parse_result(
                    final_status=final_status,
                    kind=kind,
                    repair_attempted=repair_attempted,
                    started=started,
                    observed=recorder is not None,
                ),
                "first_parse_result_derivation": (
                    "inferred_from_logical_call_count_and_phase_outcome"
                    if recorder is not None
                    else "not_observed"
                ),
                "structured_repair_attempted": repair_attempted,
                "structured_repair_result": _repair_result(
                    final_status=final_status,
                    repair_attempted=repair_attempted,
                    observed=recorder is not None,
                ),
                "strategy_recovery_attempted": unknown(UNOBSERVABLE_IN_ORCHESTRATOR),
                "strategy_recovery_result": unknown(UNOBSERVABLE_IN_ORCHESTRATOR),
                "structural_failure_kind": kind,
                "harness_exception_recorded": phase in exception_phases,
                "logical_call_count": len(calls),
                "invocation_count": len(invocations),
                "empty_visible_output_count": sum(
                    1
                    for record in invocations
                    if record["content_status"]["status"] == STATUS_EMPTY
                ),
                "length_stop_count": sum(
                    1
                    for record in invocations
                    if str(record["stop_reason"].get("value") or "")
                    in LENGTH_STOP_REASONS
                ),
            }
        )
        if not halted and halt and phase == "classify" and status == "completed":
            halted = True

    _mark_continuation(records)
    return records


def _final_status(status: str, *, started: bool, halted: bool) -> str:
    if status == "completed":
        return PHASE_COMPLETED
    if status == "failed":
        return PHASE_STRUCTURAL_FAILURE
    if not started:
        return PHASE_EXPECTED_HALT if halted else PHASE_SKIPPED
    return PHASE_UNKNOWN


def _parse_result(
    *, final_status: str, kind: str, repair_attempted: bool, started: bool, observed: bool
) -> str:
    if not observed:
        return PARSE_UNKNOWN
    if not started:
        return PARSE_NOT_REACHED
    if repair_attempted:
        return PARSE_FAILED
    if kind in _PROVIDER_FAILURE_KINDS or kind == "policy_blocked":
        return PARSE_NOT_REACHED
    if kind in _PARSE_FAILURE_KINDS:
        return PARSE_FAILED
    if final_status == PHASE_COMPLETED or kind in ("schema_validation", "missing_output"):
        return PARSE_PARSED
    return PARSE_UNKNOWN


def _repair_result(*, final_status: str, repair_attempted: bool, observed: bool) -> str:
    if not observed:
        return OUTCOME_UNKNOWN
    if not repair_attempted:
        return OUTCOME_NOT_ATTEMPTED
    if final_status == PHASE_COMPLETED:
        return OUTCOME_SUCCEEDED
    if final_status == PHASE_STRUCTURAL_FAILURE:
        return OUTCOME_FAILED
    return OUTCOME_UNKNOWN


def _mark_continuation(records: list[dict[str, Any]]) -> None:
    """Whether the eval kept going after a phase terminally failed.

    Observed, not corrected. The harness continues after a failed phase today
    and continues after one now; this field is what makes that visible in the
    artifact instead of only in the source.
    """
    for index, record in enumerate(records):
        later_started = any(later["phase_started"] for later in records[index + 1 :])
        record["continued_after_structural_failure"] = bool(
            record["phase_final_status"] == PHASE_STRUCTURAL_FAILURE and later_started
        )


# ─────────────────────────── judge provenance ───────────────────────────


def judge_provenance(
    *,
    requested_provider: str = "",
    requested_model: str = "",
    requested_max_tokens: Any = None,
    requested_temperature: Any = None,
    input_chars_pre_truncation: Any = None,
    input_chars_post_truncation: Any = None,
    response: Any = None,
    recorder: Optional[EvalProvenanceRecorder] = None,
) -> dict[str, Any]:
    """Provenance for the judge call. Configuration is observed, never altered.

    Three different identities are recorded because they are three different
    facts, and conflating them is how an artifact ends up asserting something no
    provider ever said:

    ``requested_*``
        What the harness asked for. Source: `judge_config`. Always known.

    ``selected_*`` / ``used_provider``
        What the gateway decided to call, and which provider it routed to.
        Source: the gateway's own record of its routing. Always known when a
        call was attempted, and still *not* provider evidence.

    ``effective_model``
        What the provider said it actually ran, read off the response by the
        telemetry channel. Source: provider observation, and **nothing else** --
        when the provider supplies no model identity the envelope says so rather
        than falling back to the requested or selected name.

    `LLMResponse.model_used` is deliberately not consulted for the last of
    these: every construction site in the adapters sets it from the *requested*
    model, so reading it as provider evidence reports the request back as though
    the provider had confirmed it. That was F-2.

    The judge's prompt text and response text are not read here. The rationale
    the harness already retains is unchanged and is not duplicated.
    """
    pre = _ordinal(input_chars_pre_truncation)
    post = _ordinal(input_chars_post_truncation)
    invocations = (
        recorder.invocation_records(phases=(JUDGE_PHASE,)) if recorder is not None else []
    )
    # The gateway returns as soon as a candidate succeeds, so the LAST judge
    # invocation is the one that produced the answer. Reading the first would
    # describe a *failed* Anthropic attempt whenever the judge fell back to
    # OpenAI -- exactly the case Gate B exists to exercise.
    answering = invocations[-1] if invocations else None
    record = {
        "requested_provider": _label(requested_provider, limit=64),
        "requested_model": _label(requested_model, limit=128),
        "requested_max_tokens": _ordinal(requested_max_tokens),
        "requested_temperature": (
            requested_temperature
            if isinstance(requested_temperature, (int, float))
            and not isinstance(requested_temperature, bool)
            else None
        ),
        "input_chars_pre_truncation": pre,
        "input_chars_post_truncation": post,
        "input_truncated": bool(pre is not None and post is not None and post < pre),
        "invocation_count": len(invocations),
        # Runtime routing fact, not a provider echo: no provider returns its own
        # identity, so the honest name for "which provider the gateway called"
        # is `used_provider`. It is never called an *observed* effective provider.
        "used_provider": _label(
            getattr(response, "provider_used", "") or "", limit=64
        ),
        "selected_provider": _label(
            getattr(response, "selected_provider", "") or "", limit=64
        ),
        "selected_model": _label(
            getattr(response, "selected_model", "") or "", limit=128
        ),
        # Provider observation only. No fallback to requested or selected.
        "effective_model": (
            answering["effective_model"] if answering else unknown("no_invocation")
        ),
        "selection_reason": _label(
            getattr(response, "selection_reason", "") or "", limit=64
        ),
        "task_profile": _label(getattr(response, "task_profile", "") or "", limit=64),
        "fallback_used": bool(getattr(response, "fallback_used", False)),
        "attempt_count": _ordinal(getattr(response, "attempt_count", None)),
        "response_ok": bool(getattr(response, "ok", False)),
        "error_type": _token(getattr(response, "error_type", "") or "", limit=64),
        # Usage is provider observation, so it is carried in envelopes like the
        # rest of it. `LLMResponse` defaults these counters to 0, which made a
        # call that never reached a provider indistinguishable from one that
        # genuinely reported zero -- the same mislabeling as F-2, one field over.
        "input_tokens": answering["input_tokens"] if answering else unknown("no_invocation"),
        "output_tokens": (
            answering["output_tokens"] if answering else unknown("no_invocation")
        ),
        "cache_read_tokens": (
            answering["cache_read_tokens"] if answering else unknown("no_invocation")
        ),
        "stop_reason": answering["stop_reason"] if answering else unknown("no_invocation"),
        "reasoning_tokens": (
            answering["reasoning_tokens"] if answering else unknown("no_invocation")
        ),
        "content_status": (
            answering["content_status"] if answering else unknown("no_invocation")
        ),
        "visible_content_length": (
            answering["visible_content_length"] if answering else unknown("no_invocation")
        ),
        "refusal_status": (
            answering["refusal_status"] if answering else unknown("no_invocation")
        ),
    }
    return record


# ─────────────────────────── per-case assembly ───────────────────────────


def empty_case_provenance(*, case_id: str = "", capture_mode: str = CAPTURE_MODE_DISABLED) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "captured": False,
        "capture_mode": capture_mode if capture_mode in CAPTURE_MODES else CAPTURE_MODE_DISABLED,
        "case_id": _label(case_id, limit=64),
        "halt_reason": "",
        "phases": [],
        "invocations": [],
        "judge": {},
        "notes": [],
        "counters": _counters([], []),
    }


def case_provenance(
    *,
    case_id: str,
    output: Any,
    phases: Iterable[str],
    recorder: Optional[EvalProvenanceRecorder],
    judge: Optional[dict[str, Any]] = None,
    capture_mode: str = CAPTURE_MODE_TELEMETRY,
) -> dict[str, Any]:
    """The per-case ledger written into the shard report."""
    ordered = list(phases)
    invocations = recorder.invocation_records() if recorder is not None else []
    ledger = phase_ledger(
        output=output,
        phases=ordered,
        recorder=recorder,
        invocation_records=invocations,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "captured": recorder is not None,
        "capture_mode": capture_mode if capture_mode in CAPTURE_MODES else CAPTURE_MODE_DISABLED,
        "case_id": _label(case_id, limit=64),
        "halt_reason": _halt_reason(output),
        "phases": ledger,
        "invocations": invocations,
        "judge": dict(judge or {}),
        "notes": list(recorder.notes) if recorder is not None else [],
        "counters": _counters(ledger, invocations),
    }


def _counters(
    ledger: list[dict[str, Any]], invocations: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "phase_count": len(ledger),
        "invocation_count": len(invocations),
        "structural_failure_phases": [
            record["phase"]
            for record in ledger
            if record["phase_final_status"] == PHASE_STRUCTURAL_FAILURE
        ],
        "empty_visible_output_event_count": sum(
            1
            for record in invocations
            if record["content_status"]["status"] == STATUS_EMPTY
        ),
        "explicit_length_stop_event_count": sum(
            1
            for record in invocations
            if str(record["stop_reason"].get("value") or "") in LENGTH_STOP_REASONS
        ),
        "reasoning_token_evidence_available_count": sum(
            1
            for record in invocations
            if record["reasoning_tokens"]["status"] == STATUS_VALID
        ),
        "continued_after_structural_failure": any(
            record["continued_after_structural_failure"] for record in ledger
        ),
    }


# ─────────────────────────── aggregate provenance ───────────────────────────


def failure_provenance(case: Any) -> dict[str, Any]:
    """Informational attribution for one case. Never a scoring input.

    A case that passed is ``none``. A case that failed is attributed from what
    was observed, and from nothing else: with no provenance the answer is
    ``unknown`` rather than a guess that it must have been analytical quality.
    """
    passed = bool(_case_field(case, "passed", False))
    provenance = _case_field(case, "provenance", {}) or {}
    if passed:
        return {"passed": True, "categories": [], "primary": FAILURE_NONE}
    if not isinstance(provenance, dict) or not provenance.get("captured"):
        return {"passed": False, "categories": [], "primary": FAILURE_UNKNOWN}

    categories: list[str] = []
    for record in provenance.get("phases") or []:
        kind = str(record.get("structural_failure_kind") or "")
        if kind in _PROVIDER_FAILURE_KINDS and FAILURE_PROVIDER not in categories:
            categories.append(FAILURE_PROVIDER)
        if kind in _STRUCTURED_FAILURE_KINDS and FAILURE_STRUCTURAL not in categories:
            categories.append(FAILURE_STRUCTURAL)
    judge = provenance.get("judge") or {}
    if isinstance(judge, dict) and judge and not judge.get("response_ok", True):
        if FAILURE_PROVIDER not in categories:
            categories.append(FAILURE_PROVIDER)
    if not categories:
        categories.append(FAILURE_ANALYTICAL)
    primary = (
        FAILURE_PROVIDER
        if FAILURE_PROVIDER in categories
        else FAILURE_STRUCTURAL
        if FAILURE_STRUCTURAL in categories
        else FAILURE_ANALYTICAL
    )
    return {"passed": False, "categories": categories, "primary": primary}


def _case_field(case: Any, name: str, default: Any) -> Any:
    if isinstance(case, dict):
        return case.get(name, default)
    return getattr(case, name, default)


def _status_key(record: Any, name: str) -> str:
    """A counter key drawn from the closed vocabulary, or ``unknown``.

    A status this schema does not define cannot become a key: an aggregate whose
    key set is decided by the contents of a downloaded artifact is not a closed
    vocabulary any more.
    """
    if not isinstance(record, dict):
        return STATUS_UNKNOWN
    field = record.get(name)
    status = (field or {}).get("status") if isinstance(field, dict) else None
    return status if status in VALUE_STATUSES else STATUS_UNKNOWN


def aggregate_provenance(
    cases: Iterable[Any], *, aggregation_errors: Iterable[str] = ()
) -> dict[str, Any]:
    """Aggregate counters over whatever provenance the shards carried.

    Purely additive and purely informational. A historical summary whose cases
    carry no provenance at all aggregates to an honest set of zeroes with
    ``cases_with_provenance = 0`` — it is never reported as evidence of clean
    execution.
    """
    structural_case_ids: list[str] = []
    structural_phases: dict[str, int] = {}
    content_counts: dict[str, int] = {}
    refusal_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    continued_case_ids: list[str] = []
    empty_events = 0
    length_stops = 0
    reasoning_evidence = 0
    with_provenance = 0
    invocation_total = 0
    judge_truncated = 0

    for case in cases:
        # Aggregation reads shard reports that arrived as downloaded artifacts,
        # so every value that becomes a key or a list entry below is bounded
        # here rather than trusted. A corrupt shard can make a counter wrong; it
        # must not be able to make the aggregate report unbounded.
        case_id = _label(_case_field(case, "case_id", ""), limit=64)
        attribution = failure_provenance(case)
        category_counts[attribution["primary"]] = (
            category_counts.get(attribution["primary"], 0) + 1
        )
        provenance = _case_field(case, "provenance", {}) or {}
        if not isinstance(provenance, dict) or not provenance.get("captured"):
            continue
        with_provenance += 1
        counters = provenance.get("counters") or {}
        phases = [
            _token(name, limit=64)
            for name in (counters.get("structural_failure_phases") or [])
        ]
        if phases:
            structural_case_ids.append(case_id)
            for phase in phases:
                structural_phases[phase] = structural_phases.get(phase, 0) + 1
        if counters.get("continued_after_structural_failure"):
            continued_case_ids.append(case_id)
        empty_events += int(counters.get("empty_visible_output_event_count") or 0)
        length_stops += int(counters.get("explicit_length_stop_event_count") or 0)
        reasoning_evidence += int(
            counters.get("reasoning_token_evidence_available_count") or 0
        )
        invocations = provenance.get("invocations") or []
        invocation_total += len(invocations)
        for record in invocations:
            content = _status_key(record, "content_status")
            refusal = _status_key(record, "refusal_status")
            content_counts[content] = content_counts.get(content, 0) + 1
            refusal_counts[refusal] = refusal_counts.get(refusal, 0) + 1
        judge = provenance.get("judge") or {}
        if isinstance(judge, dict) and judge.get("input_truncated"):
            judge_truncated += 1

    errors = list(aggregation_errors)
    if errors:
        category_counts[FAILURE_AGGREGATION] = (
            category_counts.get(FAILURE_AGGREGATION, 0) + len(errors)
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "cases_with_provenance": with_provenance,
        "invocation_record_count": invocation_total,
        "cases_with_structural_failure": len(structural_case_ids),
        "structural_failure_case_ids": structural_case_ids,
        "structural_failure_count": sum(structural_phases.values()),
        "structural_failure_phases": dict(sorted(structural_phases.items())),
        "empty_visible_output_event_count": empty_events,
        "explicit_length_stop_event_count": length_stops,
        "reasoning_token_evidence_available_count": reasoning_evidence,
        "content_status_counts": dict(sorted(content_counts.items())),
        "refusal_status_counts": dict(sorted(refusal_counts.items())),
        "cases_continued_after_structural_failure": continued_case_ids,
        "judge_inputs_truncated_count": judge_truncated,
        "failure_provenance_counts": dict(sorted(category_counts.items())),
        # Said in as many words on every artifact: this block explains a result,
        # it does not produce one, and nothing here was used to compute the
        # release verdict beside it.
        "informational_only": True,
        "scoring_notice": (
            "Provenance is observational. It does not change CaseResult.passed, "
            "passed, total, pass_rate, threshold or ok, and no corrected or "
            "counterfactual pass rate is derived from it."
        ),
    }


__all__ = [
    "CAPTURE_MODES",
    "CAPTURE_MODE_DEFERRED",
    "CAPTURE_MODE_DISABLED",
    "CAPTURE_MODE_MOCK",
    "CAPTURE_MODE_TELEMETRY",
    "FAILURE_PROVENANCE_CATEGORIES",
    "JUDGE_PHASE",
    "LENGTH_STOP_REASONS",
    "MAX_CALLS",
    "MAX_EVENTS",
    "MAX_INVOCATIONS",
    "MAX_SHAPES",
    "PARSE_RESULTS",
    "PHASE_FINAL_STATUSES",
    "PROVENANCE_ENV",
    "SCHEMA_VERSION",
    "STRUCTURAL_FAILURE_KINDS",
    "VALUE_STATUSES",
    "EvalProvenanceRecorder",
    "aggregate_provenance",
    "case_provenance",
    "empty_case_provenance",
    "failure_provenance",
    "judge_provenance",
    "phase_ledger",
    "provenance_enabled",
    "unknown",
    "value",
]
