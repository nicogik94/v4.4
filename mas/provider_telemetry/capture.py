"""Capture-failure isolation and provider metadata extraction.

Blocker 4 of the audit: "Telemetry extraction errors can turn a valid provider
response into an application-level provider failure." A response object whose
``id`` is a property that raises, or whose ``__str__`` raises, used to propagate
straight out of the adapter's ``try`` block and be classified as a provider
error — the model answered, the user got a failure, and a retry was issued
against a provider that had done nothing wrong.

Every function in this module that touches provider data runs inside
:func:`guard`, which:

* swallows ``Exception`` and records a ``capture_failure`` event, so telemetry
  can fail without the runtime noticing;
* **re-raises** ``BaseException`` — notably ``asyncio.CancelledError`` and
  ``KeyboardInterrupt`` — because swallowing a cancellation is itself the
  behavioral change the whole wave exists to avoid;
* guards each field read *individually*, so one hostile attribute costs one
  field rather than the whole observation.

Blocker 5 — "later response-transformation failures can overwrite already
captured rich provider metadata" — is answered structurally: this module can only
*append* events. There is no code path that mutates a published observation, so
a transformation failure arriving after a rich observation adds a
``transformation_failure`` event beside it and leaves it intact.
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
from typing import Any, Callable, Iterator, Optional, TypeVar

from . import redaction
from .identity import TelemetryIdentity, new_uuid
from .models import (
    EVENT_CANCELLED,
    EVENT_CAPTURE_FAILURE,
    EVENT_COMPLETED,
    EVENT_OBSERVATION,
    EVENT_PROVIDER_FAILURE,
    EVENT_TRANSFORMATION_FAILURE,
    EVENT_UNKNOWN,
    SUBJECT_HTTP_ATTEMPT,
    SUBJECT_SDK_INVOCATION,
    TRANSPORT_CANCELLED,
    TRANSPORT_ERROR,
    TRANSPORT_RESPONSE,
    TRANSPORT_UNKNOWN,
    AttemptEvent,
    BreakerSnapshot,
    ProviderObservation,
    UNKNOWN_BREAKER,
    utc_now,
)
from .values import (
    ABSENT,
    MISSING,
    UNSUPPORTED,
    ProviderValue,
    exact_nonnegative_int,
    invalid,
    read_attribute,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

# One log line per distinct capture-failure reason per process: a persistent
# defect stays visible in the counters without flooding a run's logs.
_logged_reasons: set[str] = set()


@contextlib.contextmanager
def guard(reason: str = "capture") -> Iterator[None]:
    """Absorb telemetry-only Exceptions. Never absorbs BaseException."""
    try:
        yield
    except Exception as exc:  # noqa: BLE001 - the isolation boundary itself
        _note_capture_failure(reason, exc)


def guarded(fn: Callable[[], T], default: T, *, reason: str = "capture") -> T:
    """Evaluate ``fn`` inside the isolation boundary, or return ``default``."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - the isolation boundary itself
        _note_capture_failure(reason, exc)
        return default


def _note_capture_failure(reason: str, exc: Exception) -> None:
    key = f"{reason}:{type(exc).__name__}"[:96]
    capture = current_capture()
    if capture is not None:
        capture.note_capture_failure(key)
    else:
        _note_outside_any_invocation(key)
    if key not in _logged_reasons:
        _logged_reasons.add(key)
        logger.warning(
            "provider-attempt telemetry capture failed (%s). The provider result, "
            "routing, retry, fallback, circuit-breaker and cache behavior are "
            "unaffected; the failure is recorded as a capture_failure event.",
            key,
        )


def _note_outside_any_invocation(key: str) -> None:
    """A capture failure with no invocation buffer to attach it to.

    Not every guarded read happens inside a capture scope. The circuit-breaker
    snapshot is taken to *build* the invocation record, so it runs before the
    buffer that would hold its failure exists; the same is true of the publish
    step, which runs after the buffer has been drained. Those failures used to
    be logged once and then discarded entirely — the run finished, said nothing,
    and looked clean.

    There is no event that could carry them: ``provider_attempt_event`` requires
    a subject, and there is no subject. So they are recorded as what they
    actually are — a telemetry failure this process could not represent — which
    reconciliation reports and refuses to certify.
    """
    try:
        from . import service

        session = service.current_session()
        if session is None:
            return
        session.note_unrepresented_capture_failures(1)
    except Exception:  # noqa: BLE001 - the isolation boundary must not recurse
        # Deliberately silent and deliberately not routed back through `guarded`:
        # a failure to record a failure to record a failure has to terminate
        # somewhere, and the log line the caller emits already names the reason.
        pass


def reset_capture_log_latch() -> None:
    """Test support: forget which reasons have already been logged."""
    _logged_reasons.clear()


# ─────────────────────────── guarded field reads ───────────────────────────


# A read that *failed* is not a field that was *absent*. Conflating them would
# make telemetry claim the provider sent nothing when the truth is that this
# build could not look — precisely the kind of confident-but-false statement the
# whole value model exists to prevent.
_CAPTURE_FAILED = object()


def _read(source: Any, name: str) -> Any:
    """Read one attribute, distinguishing "not there" from "could not read".

    A provider object is untrusted: ``response.id`` may be a descriptor that
    raises, and a hostile or merely buggy one must cost this field and nothing
    else — but it must not be recorded as the provider having omitted the field.
    """
    if source is _CAPTURE_FAILED:
        return _CAPTURE_FAILED
    return guarded(
        lambda: read_attribute(source, name), _CAPTURE_FAILED, reason=f"read:{name}"
    )


def _text_value(source: Any, name: str, validator: Callable[[Any], ProviderValue]) -> ProviderValue:
    raw = _read(source, name)
    if raw is _CAPTURE_FAILED:
        return invalid("capture_failed")
    return guarded(lambda: validator(raw), invalid("capture_failed"), reason=f"validate:{name}")


def _int_value(source: Any, name: str) -> ProviderValue:
    raw = _read(source, name)
    if raw is _CAPTURE_FAILED:
        return invalid("capture_failed")
    return guarded(
        lambda: exact_nonnegative_int(raw), invalid("capture_failed"), reason=f"usage:{name}"
    )


# ─────────────────────────── capture context ───────────────────────────


class InvocationCapture:
    """Append-only capture buffer for one SDK invocation.

    Owns the event ordinals for its invocation and for every HTTP attempt made
    inside it. Events are appended in the order they are observed and are handed
    to the delivery queue by the caller; nothing here writes to a database and
    nothing here can block.
    """

    __slots__ = (
        "invocation_id",
        "call_id",
        "telemetry_run_id",
        "posture",
        "worker_id",
        "provider",
        "requested_model",
        "identity",
        "events",
        "http_attempts",
        "_ordinals",
        "_sdk_terminal_recorded",
        "capture_failures",
    )

    def __init__(
        self,
        *,
        invocation_id: str,
        call_id: str,
        telemetry_run_id: str,
        posture: str,
        worker_id: str,
        provider: str,
        requested_model: str,
        identity: Optional[TelemetryIdentity] = None,
    ) -> None:
        self.invocation_id = invocation_id
        self.call_id = call_id
        self.telemetry_run_id = telemetry_run_id
        self.posture = posture
        self.worker_id = worker_id
        self.provider = provider
        self.requested_model = requested_model
        self.identity = identity
        self.events: list[AttemptEvent] = []
        self.http_attempts: list[Any] = []
        self._ordinals: dict[str, int] = {}
        self._sdk_terminal_recorded = False
        self.capture_failures: list[str] = []

    # ── ordinals ──

    def next_ordinal(self, subject_id: str) -> int:
        current = self._ordinals.get(subject_id, 0) + 1
        self._ordinals[subject_id] = current
        return current

    @property
    def sdk_terminal_recorded(self) -> bool:
        return self._sdk_terminal_recorded

    def note_capture_failure(self, reason: str) -> None:
        self.capture_failures.append(reason)

    # ── appends ──

    def append(
        self,
        *,
        subject_kind: str,
        subject_id: str,
        event_kind: str,
        observed_at=None,
        **fields: Any,
    ) -> Optional[AttemptEvent]:
        """Append one event. Returns None if constructing it failed.

        Deliberately tolerant: a malformed event is dropped and counted rather
        than raised, because raising here would reach the runtime.
        """
        def build() -> AttemptEvent:
            return AttemptEvent(
                event_id=new_uuid(),
                subject_kind=subject_kind,
                subject_id=subject_id,
                call_id=self.call_id,
                telemetry_run_id=self.telemetry_run_id,
                event_kind=event_kind,
                event_ordinal=self.next_ordinal(subject_id),
                observed_at=observed_at or utc_now(),
                worker_id=self.worker_id,
                **fields,
            )

        event = guarded(build, None, reason=f"event:{event_kind}")
        if event is None:
            return None
        self.events.append(event)
        if event.is_terminal and subject_kind == SUBJECT_SDK_INVOCATION:
            self._sdk_terminal_recorded = True
        return event

    def record_observation(
        self, observation: ProviderObservation, *, observed_at=None
    ) -> Optional[AttemptEvent]:
        """Append rich provider metadata. Never replaces an earlier observation."""
        return self.append(
            subject_kind=SUBJECT_SDK_INVOCATION,
            subject_id=self.invocation_id,
            event_kind=EVENT_OBSERVATION,
            observed_at=observed_at,
            observation=observation,
        )

    def record_terminal(
        self,
        event_kind: str,
        *,
        observation: Optional[ProviderObservation] = None,
        error_category: str = "",
        error_identity: str = "",
        failure_class: str = "",
        breaker_after: BreakerSnapshot = UNKNOWN_BREAKER,
        observed_at=None,
    ) -> Optional[AttemptEvent]:
        """Append the invocation's single terminal event.

        ``failure_class`` is stored rather than discarded because it is the
        durable evidence distinguishing an invocation that was *blocked before
        transport* — a strict start that refused — from one whose HTTP attempt
        row was simply lost. Reconciliation reads exactly that distinction.
        """
        if self._sdk_terminal_recorded:
            # A second terminal state would make the chain ambiguous. Record the
            # attempt to write one as a capture failure instead.
            self.note_capture_failure("duplicate_terminal")
            return None
        return self.append(
            subject_kind=SUBJECT_SDK_INVOCATION,
            subject_id=self.invocation_id,
            event_kind=event_kind,
            observed_at=observed_at,
            observation=observation or ProviderObservation(),
            error_category=error_category,
            error_identity=error_identity,
            failure_class=failure_class,
            breaker_after=breaker_after,
        )

    def record_transformation_failure(
        self, exc: BaseException, *, error_category: str = ""
    ) -> Optional[AttemptEvent]:
        """Append a transformation failure *beside* whatever was captured.

        This is blocker 5's answer in one method: the rich observation recorded
        before the transformation is already in ``self.events`` and is never
        touched.
        """
        identity = guarded(lambda: redaction.exception_identity(exc), "", reason="transform")
        return self.append(
            subject_kind=SUBJECT_SDK_INVOCATION,
            subject_id=self.invocation_id,
            event_kind=EVENT_TRANSFORMATION_FAILURE,
            error_category=error_category,
            error_identity=identity,
            failure_class=guarded(lambda: redaction.failure_class(exc), "", reason="transform"),
        )

    def record_capture_failure(self, reason: str) -> Optional[AttemptEvent]:
        return self.append(
            subject_kind=SUBJECT_SDK_INVOCATION,
            subject_id=self.invocation_id,
            event_kind=EVENT_CAPTURE_FAILURE,
            failure_class=str(reason)[:64],
        )

    def flush_capture_failures(self) -> int:
        """Turn every noted capture failure into a durable event.

        ``note_capture_failure`` is called from the isolation boundary itself —
        from ``guard``/``guarded``, wherever a provider attribute read, a
        validation, a fingerprint, a breaker snapshot or an event construction
        failed — and until this method existed those notes lived and died in a
        process-local list. A telemetry failure that leaves no durable trace is
        indistinguishable from telemetry that had nothing to say, which is the
        one thing a completeness claim cannot tolerate.

        Returns the number of noted failures this could **not** represent. A
        nonzero return is not a lost log line: the caller turns it into a
        run-level undurable outcome, because the failure to record a failure is
        itself something reconciliation has to refuse to certify.

        Draining twice yields nothing the second time. Constructing the events
        cannot raise: ``append`` is already tolerant, and a reason that will not
        build is counted rather than propagated — reaching the runtime from here
        would be the exact behavioral change this whole module exists to avoid.
        """
        pending, self.capture_failures = self.capture_failures, []
        unrepresented = 0
        for reason in pending:
            # Guarded so that a failure *while recording a failure* is counted
            # rather than raised, and so the loop still reaches the rest.
            recorded = guarded(
                lambda reason=reason: self.record_capture_failure(reason),
                None,
                reason="capture_failure_event",
            )
            if recorded is None:
                unrepresented += 1
        # Anything `guarded` noted while running the loop above would otherwise
        # be flushed on the next call and look like a fresh failure; it belongs
        # to this flush, so it is counted here and not carried forward.
        unrepresented += len(self.capture_failures)
        self.capture_failures = []
        return unrepresented

    # ── HTTP attempt events ──

    def record_http_terminal(
        self,
        attempt_id: str,
        *,
        event_kind: str,
        transport_outcome: str,
        http_status: ProviderValue = ABSENT,
        provider_request_id: ProviderValue = ABSENT,
        retry_after: ProviderValue = ABSENT,
        failure_class: str = "",
        error_identity: str = "",
        observed_at=None,
    ) -> Optional[AttemptEvent]:
        return self.append(
            subject_kind=SUBJECT_HTTP_ATTEMPT,
            subject_id=attempt_id,
            event_kind=event_kind,
            observed_at=observed_at,
            transport_outcome=transport_outcome,
            http_status=http_status,
            provider_request_id=provider_request_id,
            retry_after=retry_after,
            failure_class=failure_class,
            error_identity=error_identity,
        )


_capture: contextvars.ContextVar[Optional[InvocationCapture]] = contextvars.ContextVar(
    "provider_telemetry_capture", default=None
)


@contextlib.contextmanager
def capture_scope(capture: Optional[InvocationCapture]) -> Iterator[Optional[InvocationCapture]]:
    """Bind a capture buffer for exactly one SDK invocation."""
    token = _capture.set(capture)
    try:
        yield capture
    finally:
        _capture.reset(token)


def current_capture() -> Optional[InvocationCapture]:
    return _capture.get()


def is_capturing() -> bool:
    """True when a caller opened a capture buffer for the current invocation.

    Adapters gate all capture work on this, so a disabled build pays for one
    context-variable read and nothing else.
    """
    return _capture.get() is not None


# ─────────────────────── response-shape observation ───────────────────────
#
# The durable relations carry identity, effective model, stop reason and usage.
# They carry nothing about the *shape* of what came back: an adapter-visible
# text that was exactly empty, a message field the provider sent as an explicit
# null, a refusal, or a reasoning-token count. A release observation that cannot
# tell "the model answered badly" from "the model returned nothing" is not
# interpretable, and none of those four facts survives past the adapter.
#
# They are published to an **observer** rather than added to ProviderObservation
# on purpose. ProviderObservation is the durable record: every field on it has a
# column, a status column and a CHECK, and adding a field that is never written
# would put something in the durable type that durable storage does not carry.
# The observer is bound by a caller that wants the extra evidence — today, the
# evaluation harness — and is absent everywhere else, so a build nobody asked
# pays one context-variable read and stores nothing at all.
#
# What is recorded is a status and, for visible text, a character count. The
# text itself, any prefix of it, and any digest of it are never read into a
# stored value: a digest of a response is still derived from the response.

SHAPE_MISSING = "missing"
SHAPE_ABSENT = "absent"
SHAPE_NULL = "null"
SHAPE_EMPTY = "empty"
SHAPE_NONEMPTY = "nonempty"
SHAPE_VALID = "valid"
SHAPE_INVALID = "invalid"
SHAPE_UNSUPPORTED = "unsupported"
SHAPE_UNKNOWN = "unknown"

# Two closed vocabularies over one universe. `missing` and `absent` are kept
# apart deliberately: `missing` means the *container* was not reachable, so the
# field could not even be looked for, and `absent` means the container was there
# and did not carry the field. Collapsing them would make a response that
# omitted one counter indistinguishable from an SDK shape this build could not
# read at all.
PRESENCE_STATUSES = (
    SHAPE_MISSING,
    SHAPE_ABSENT,
    SHAPE_NULL,
    SHAPE_EMPTY,
    SHAPE_NONEMPTY,
    SHAPE_UNSUPPORTED,
    SHAPE_INVALID,
    SHAPE_UNKNOWN,
)
COUNT_STATUSES = (
    SHAPE_MISSING,
    SHAPE_ABSENT,
    SHAPE_NULL,
    SHAPE_VALID,
    SHAPE_UNSUPPORTED,
    SHAPE_INVALID,
    SHAPE_UNKNOWN,
)

RESPONSE_SHAPE_FIELDS = (
    "content_status",
    "visible_content_length",
    "refusal_status",
    "reasoning_tokens",
)

_MAX_SHAPE_DETAIL = 32


def _shape(status: str, *, value: Any = None, detail: str = "") -> dict[str, Any]:
    """One shape field: a status, a value only when the status carries one."""
    cleaned = "".join(
        ch for ch in str(detail or "") if ch.isprintable() and ch != " "
    )[:_MAX_SHAPE_DETAIL]
    return {"status": status, "value": value, "detail": cleaned}


def _container_status(container: Any, *, detail: str) -> Optional[dict[str, Any]]:
    """A verdict about the container itself, or ``None`` when it is readable."""
    if container is _CAPTURE_FAILED:
        return _shape(SHAPE_INVALID, detail="capture_failed")
    if container is MISSING or container is None:
        return _shape(SHAPE_MISSING, detail=detail)
    return None


def _text_presence(container: Any, name: str, *, with_length: bool) -> dict[str, Any]:
    """Classify a provider text field without reading the text into a value.

    ``with_length`` is the only thing that ever leaves this function carrying a
    number, and it is a character count. A refusal is classified with
    ``with_length=False`` because its length is a property of provider-authored
    prose that nothing here has a reason to keep.
    """
    verdict = _container_status(container, detail="container_missing")
    if verdict is not None:
        return verdict
    raw = _read(container, name)
    if raw is _CAPTURE_FAILED:
        return _shape(SHAPE_INVALID, detail="capture_failed")
    if raw is MISSING:
        return _shape(SHAPE_ABSENT)
    if raw is None:
        return _shape(SHAPE_NULL)
    if not isinstance(raw, str):
        return _shape(SHAPE_INVALID, detail=type(raw).__name__[:16])
    length = guarded(lambda: len(raw), None, reason=f"length:{name}")
    if length is None:
        return _shape(SHAPE_INVALID, detail="length_unreadable")
    if not with_length:
        return _shape(SHAPE_EMPTY if length == 0 else SHAPE_NONEMPTY)
    return _shape(
        SHAPE_EMPTY if length == 0 else SHAPE_NONEMPTY,
        value=int(length),
    )


def _visible_length(presence: dict[str, Any]) -> dict[str, Any]:
    """The character count that goes beside a content status, or an honest gap."""
    status = presence.get("status")
    value = presence.get("value")
    if status in (SHAPE_EMPTY, SHAPE_NONEMPTY) and isinstance(value, int):
        return _shape(SHAPE_VALID, value=value)
    if status in (SHAPE_MISSING, SHAPE_ABSENT, SHAPE_NULL, SHAPE_UNSUPPORTED):
        return _shape(status, detail=str(presence.get("detail") or ""))
    return _shape(SHAPE_INVALID, detail=str(presence.get("detail") or "unreadable"))


def _count_from(container: Any, name: str, *, container_detail: str) -> dict[str, Any]:
    """An exact nonnegative count, or the epistemic state that replaces one."""
    verdict = _container_status(container, detail=container_detail)
    if verdict is not None:
        return verdict
    raw = _read(container, name)
    if raw is _CAPTURE_FAILED:
        return _shape(SHAPE_INVALID, detail="capture_failed")
    classified = guarded(
        lambda: exact_nonnegative_int(raw), None, reason=f"count:{name}"
    )
    if classified is None:
        return _shape(SHAPE_INVALID, detail="capture_failed")
    if classified.status == "valid":
        return _shape(SHAPE_VALID, value=int(classified.value))
    if classified.status == "absent":
        return _shape(SHAPE_ABSENT)
    if classified.status == "null":
        return _shape(SHAPE_NULL)
    return _shape(SHAPE_INVALID, detail=classified.detail or classified.status)


def openai_response_shape(response: Any) -> dict[str, Any]:
    """Shape metadata for an OpenAI Chat Completions response.

    Reads the first choice's message the same way the observation above reads
    the finish reason, so a hostile container costs one field rather than the
    whole record.
    """
    usage = _read(response, "usage")
    message = _read(_first_choice(response), "message")
    content = _text_presence(message, "content", with_length=True)
    return {
        "content_status": _shape(
            content["status"], detail=content["detail"]
        ),
        "visible_content_length": _visible_length(content),
        "refusal_status": _text_presence(message, "refusal", with_length=False),
        "reasoning_tokens": _count_from(
            _read(usage, "completion_tokens_details"),
            "reasoning_tokens",
            container_detail="details_missing" if usage is not MISSING else "usage_missing",
        ),
    }


def anthropic_response_shape(response: Any) -> dict[str, Any]:
    """Shape metadata for an Anthropic Message.

    The Messages API exposes neither a refusal field nor a reasoning-token
    counter, which is ``unsupported`` — a permanent property of the API, not a
    response that happened to omit them. Visible text lives in typed content
    blocks rather than one string field, and this build does not claim to
    reconstruct it, so the two content fields are an explicit ``unknown``.
    """
    return {
        "content_status": _shape(SHAPE_UNKNOWN, detail="block_shaped"),
        "visible_content_length": _shape(SHAPE_UNKNOWN, detail="block_shaped"),
        "refusal_status": _shape(SHAPE_UNSUPPORTED),
        "reasoning_tokens": _shape(SHAPE_UNSUPPORTED),
    }


_shape_observer: contextvars.ContextVar[Optional[Any]] = contextvars.ContextVar(
    "provider_telemetry_response_shape_observer", default=None
)


@contextlib.contextmanager
def response_shape_scope(observer: Optional[Any]) -> Iterator[Optional[Any]]:
    """Bind an observer for response-shape metadata. Default is none at all."""
    token = _shape_observer.set(observer)
    try:
        yield observer
    finally:
        _shape_observer.reset(token)


def current_response_shape_observer() -> Optional[Any]:
    return _shape_observer.get()


def _publish_response_shape(builder: Callable[[Any], dict[str, Any]], response: Any) -> None:
    """Hand shape metadata to a bound observer. A no-op when there is none.

    Wrapped in the isolation boundary in full: an observer that raises, a
    builder that raises and a hostile response object all cost the shape record
    and nothing else. The provider result, the ProviderObservation built beside
    it, routing, retry, fallback, breaker state and the cache are untouched
    either way, and ``guard`` re-raises BaseException so a cancellation still
    propagates unchanged.
    """
    observer = _shape_observer.get()
    if observer is None:
        return
    with guard("response_shape"):
        payload = builder(response)
        buffer = current_capture()
        if buffer is not None:
            payload["invocation_id"] = buffer.invocation_id
            payload["call_id"] = buffer.call_id
            payload["provider"] = buffer.provider
            payload["requested_model"] = buffer.requested_model
        observer.record_response_shape(payload)


# ─────────────────────────── provider extraction ───────────────────────────


def _first_choice(response: Any) -> Any:
    """The first choice of a completion, or :data:`MISSING`."""
    choices = _read(response, "choices")

    def pick() -> Any:
        if choices is MISSING or choices is _CAPTURE_FAILED or not choices:
            return MISSING
        try:
            return choices[0]
        except (TypeError, IndexError, KeyError):
            return MISSING

    return guarded(pick, MISSING, reason="read:choices")


def observe_anthropic_response(response: Any) -> ProviderObservation:
    """Read identity, effective model, stop reason and usage off a Message.

    Called while the raw ``Message`` is still intact — before the adapter
    reduces it to text plus counters, at which point ``id``, ``model`` and
    ``stop_reason`` no longer exist anywhere.
    """
    usage = _read(response, "usage")
    observation = ProviderObservation(
        provider_response_id=_text_value(response, "id", redaction.provider_response_id),
        effective_model=_text_value(response, "model", redaction.provider_model),
        stop_reason=_text_value(response, "stop_reason", redaction.stop_reason),
        input_tokens=_int_value(usage, "input_tokens"),
        output_tokens=_int_value(usage, "output_tokens"),
        cache_read_tokens=_int_value(usage, "cache_read_input_tokens"),
        cache_creation_tokens=_int_value(usage, "cache_creation_input_tokens"),
    )
    _publish_response_shape(anthropic_response_shape, response)
    return observation


def observe_openai_response(response: Any) -> ProviderObservation:
    """Read identity, effective model, finish reason and usage off a completion.

    ``finish_reason`` is normalized onto ``stop_reason`` so a paired evaluation
    compares one field across both providers. The Chat Completions API reports
    no cache-creation counter at all, which is recorded as ``unsupported`` —
    a permanent property of the API, distinct from a response that merely
    omitted it.
    """
    usage = _read(response, "usage")
    choice = _first_choice(response)
    cached = _read(usage, "prompt_tokens_details")

    observation = ProviderObservation(
        provider_response_id=_text_value(response, "id", redaction.provider_response_id),
        effective_model=_text_value(response, "model", redaction.provider_model),
        stop_reason=_text_value(choice, "finish_reason", redaction.stop_reason),
        input_tokens=_int_value(usage, "prompt_tokens"),
        output_tokens=_int_value(usage, "completion_tokens"),
        cache_read_tokens=_int_value(cached, "cached_tokens"),
        cache_creation_tokens=UNSUPPORTED,
    )
    _publish_response_shape(openai_response_shape, response)
    return observation


def observe_exception(exc: BaseException) -> tuple[str, str]:
    """Sanitized ``(failure_class, error_identity)`` for a provider exception."""
    failure = guarded(lambda: redaction.failure_class(exc), "unclassified", reason="failure_class")
    identity = guarded(lambda: redaction.exception_identity(exc), "", reason="error_identity")
    return failure, identity


def terminal_kind_for_exception(exc: BaseException) -> str:
    """Classify a terminal state without ever treating cancellation as failure."""
    import asyncio

    if isinstance(exc, asyncio.CancelledError):
        return EVENT_CANCELLED
    if isinstance(exc, Exception):
        return EVENT_PROVIDER_FAILURE
    return EVENT_UNKNOWN


def transport_outcome_for_exception(exc: BaseException) -> str:
    import asyncio

    if isinstance(exc, asyncio.CancelledError):
        return TRANSPORT_CANCELLED
    if isinstance(exc, Exception):
        return TRANSPORT_ERROR
    return TRANSPORT_UNKNOWN


__all__ = [
    "COUNT_STATUSES",
    "EVENT_CANCELLED",
    "EVENT_CAPTURE_FAILURE",
    "EVENT_COMPLETED",
    "EVENT_OBSERVATION",
    "EVENT_PROVIDER_FAILURE",
    "EVENT_TRANSFORMATION_FAILURE",
    "EVENT_UNKNOWN",
    "PRESENCE_STATUSES",
    "RESPONSE_SHAPE_FIELDS",
    "SHAPE_ABSENT",
    "SHAPE_EMPTY",
    "SHAPE_INVALID",
    "SHAPE_MISSING",
    "SHAPE_NONEMPTY",
    "SHAPE_NULL",
    "SHAPE_UNKNOWN",
    "SHAPE_UNSUPPORTED",
    "SHAPE_VALID",
    "TRANSPORT_RESPONSE",
    "InvocationCapture",
    "anthropic_response_shape",
    "capture_scope",
    "current_capture",
    "current_response_shape_observer",
    "guard",
    "guarded",
    "is_capturing",
    "observe_anthropic_response",
    "observe_exception",
    "observe_openai_response",
    "openai_response_shape",
    "reset_capture_log_latch",
    "response_shape_scope",
    "terminal_kind_for_exception",
    "transport_outcome_for_exception",
]
