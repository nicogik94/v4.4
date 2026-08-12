"""Truthful observation of the OpenAI request V4 actually emitted.

Why this exists
───────────────
The Gate B forensic analysis (run 31616322614) could establish the request shape
only by *deriving* it from certified source code: telemetry recorded a
``request_config_fingerprint`` — a SHA-256 over provider/model/max_tokens/
temperature/thinking_budget/retries — which is not invertible, and nothing
anywhere recorded the two controls that actually explained the failure. The
diagnosis "``max_completion_tokens`` was 6,000 and ``reasoning_effort`` was never
sent" is correct, but it is an argument about source code, not an observation of
a request. A release certification that cannot state what it sent is not
self-supporting, and the next paid evaluation must not have to repeat that
exercise.

This module records the request controls themselves.

What it is *not*
────────────────
It is not a copy of :class:`~config.ModelConfig`. Configuration is what the
product *intended*; this records the mapping the adapter actually handed to the
SDK, which is a different fact and is the one that failed. The distinction is
load-bearing in both directions:

* ``max_tokens`` in ``MODEL_ROUTING`` becomes ``max_completion_tokens`` on the
  wire, but only for models matching ``startswith("gpt-5")``. Recording the
  config would not say which branch ran; recording the mapping does, because the
  branch not taken leaves its key **explicitly absent**.
* ``temperature`` is overridden to the literal ``1`` on that same branch,
  regardless of what the config asked for.

Nothing here is named ``effective_*`` or ``provider_observed_*``. Those names
belong to fields read off a provider *response*. Every field below describes an
outbound request and is named ``request_*`` so the two can never be confused by a
reader, a query or a diff.

Absence is a finding, not a gap
───────────────────────────────
``reasoning_effort`` is recorded as ``absent`` when the mapping did not carry it.
``absent`` means *this build looked and it was not there* — a positive
observation, and the durable proof that V4 did not send the field. It is
deliberately distinct from ``missing``, which means the mapping itself could not
be read and therefore nothing at all is known. A provider's documented default is
never substituted: OpenAI defaulting an unsent ``reasoning_effort`` to
``medium`` is a fact about OpenAI, not about the request V4 emitted, and writing
``medium`` into this record would be a fabrication.

The observation point, and the stronger one that was rejected
────────────────────────────────────────────────────────────
``adapter_sdk_kwargs`` — the mapping the adapter passes to
    ``chat.completions.create(**kwargs)``, observed by wrapping that method for
    the duration of a run (:func:`observe_openai_create`).

    **Its exact epistemic limitation, stated precisely:** it proves what V4
    supplied to the SDK. It does *not* prove what the SDK put on the wire.
    Everything the SDK does after this point — injecting a default, renaming a
    parameter, dropping an unknown one, re-encoding a value — is invisible here.
    A record from this point is evidence about V4's behavior, not about OpenAI's.

The stronger point — reading the serialized body at the instrumented httpx
transport, after every transformation the SDK performs — was implemented,
measured against that limitation, and **deliberately removed**. Two reasons, in
order of weight:

1. **It would have broken a certified structural safety property.** This package
   is pinned by ``test_the_package_never_reads_prompt_or_response_text``, a
   static guard asserting that no module here so much as names ``.content``. The
   guarantee is that the package is *incapable* of reaching message text, not
   that it is careful with it. Reading a request body — which carries the whole
   prompt — replaces a structural guarantee with a promise about an allowlist.
   That is a bad trade for a package whose entire value is being trustworthy
   about what it cannot do.
2. **It would have recorded nothing in the run this exists to serve.**
   ``.github/workflows/evals.yml`` sets ``MAS_EVAL_PROVENANCE=1`` and sets no
   telemetry posture, so ``configured_posture()`` is ``off`` and
   ``llm_client._instrument_provider_client`` returns the SDK client
   **unwrapped**. The transport is inert during Gate A and Gate B.

So the marginal evidence was unavailable exactly where it was wanted, and paid
for with a safety property that holds everywhere. The vocabulary below is still a
closed set of one rather than a bare string, so a future wave that finds a
body-free way to observe the wire can add a point without changing the reader
contract — and every payload names its own ``observation_point``, so no reader
ever has to infer which boundary produced a row.

Why a wrapper and not a line in the adapter
───────────────────────────────────────────
``mas/llm_client.py`` is one of the sixteen paths bound byte-for-byte to the
certified product commit that PR #118's Gate A and Gate B evidence rests on.
Editing it to add a field that changes no behavior would move the PRODUCT
boundary and invalidate that binding — an unreasonable price for an observational
wave. The observation is therefore taken one stack frame outside the certified
adapter, on the SDK's own ``create``, which receives exactly the dict the adapter
built. This wave changes zero certified product bytes.

Bounds
──────
Only the allowlisted scalars below are ever read. Prompts, system text, message
content, tools, credentials, headers and every other key are not read, not
copied, not counted and not digested — the extraction iterates the allowlist, it
never iterates the request. Each value must additionally pass a positive grammar
before it is stored, so a key that *is* allowlisted still cannot smuggle a
structure or a credential into a record.
"""
from __future__ import annotations

import contextlib
import contextvars
import math
from typing import Any, Callable, Iterator, Optional

from . import redaction
from .capture import (
    SHAPE_ABSENT,
    SHAPE_INVALID,
    SHAPE_MISSING,
    SHAPE_NULL,
    SHAPE_UNKNOWN,
    SHAPE_VALID,
    current_capture,
    guard,
    guarded,
)
from .values import MISSING, exact_nonnegative_int

# ─────────────────────────── observation points ───────────────────────────

POINT_ADAPTER_KWARGS = "adapter_sdk_kwargs"
# A closed set of one. See the module docstring for the serialized-body point
# that was implemented and then removed, and why.
OBSERVATION_POINTS = (POINT_ADAPTER_KWARGS,)

# ─────────────────────────── the allowlist ───────────────────────────
#
# ``(record field, request key)``. This tuple is the *only* source of keys read
# out of a request mapping or a parsed body; there is no other indexing path in
# this module, which is what makes "no prompt can enter a record" a structural
# property rather than a promise. Adding a key here is the only way to widen what
# is captured, and `test_provider_request_shape` asserts the tuple's exact
# contents so widening it cannot happen silently.
OPENAI_REQUEST_ALLOWLIST = (
    ("request_model", "model"),
    ("request_max_completion_tokens", "max_completion_tokens"),
    ("request_max_tokens", "max_tokens"),
    ("request_reasoning_effort", "reasoning_effort"),
    ("request_temperature", "temperature"),
)

OUTBOUND_REQUEST_FIELDS = tuple(name for name, _ in OPENAI_REQUEST_ALLOWLIST) + (
    "request_api_surface",
)

# OpenAI's documented reasoning-effort vocabulary. A well-formed value outside it
# is recorded as ``unknown`` carrying the token — never as ``valid``, because
# this build cannot vouch for the semantics of a value it does not know, and
# never as ``invalid``, because the provider is free to add levels.
KNOWN_REASONING_EFFORTS = frozenset({"minimal", "low", "medium", "high"})

# The Chat Completions parameter family these fields belong to. Recorded so a
# future migration to a different request surface — where ``max_output_tokens``
# and a nested ``reasoning`` object replace these names — cannot be misread as
# this one silently reporting absence for every field.
SURFACE_CHAT_COMPLETIONS = "chat.completions"

_MAX_DETAIL = 32
# Temperature is a small scalar. The bound refuses an absurdity rather than
# clamping it, because a clamped value would be a number nobody sent.
_MAX_ABS_TEMPERATURE = 1e6


def _shape(status: str, *, value: Any = None, detail: str = "") -> dict[str, Any]:
    """One request field: a status, and a value only when the status carries one."""
    cleaned = "".join(
        ch for ch in str(detail or "") if ch.isprintable() and ch != " "
    )[:_MAX_DETAIL]
    return {"status": status, "value": value, "detail": cleaned}


# ─────────────────────────── value classifiers ───────────────────────────


def _token_count(raw: Any) -> dict[str, Any]:
    """An exact nonnegative token budget, or the epistemic state replacing one.

    Delegates to the package's existing counter contract, so ``True`` is not 1,
    ``6000.0`` is not 6000 and ``"6000"`` is not 6000 here either. A budget that
    is not an exact integer is a defect worth seeing, and rounding it would
    manufacture a number nobody supplied.
    """
    if raw is MISSING:
        return _shape(SHAPE_ABSENT)
    if raw is None:
        return _shape(SHAPE_NULL)
    classified = guarded(
        lambda: exact_nonnegative_int(raw), None, reason="request:token_count"
    )
    if classified is None:
        return _shape(SHAPE_INVALID, detail="capture_failed")
    if classified.status == "valid":
        return _shape(SHAPE_VALID, value=int(classified.value))
    return _shape(SHAPE_INVALID, detail=classified.detail or classified.status)


def _reasoning_effort(raw: Any) -> dict[str, Any]:
    """A reasoning-effort level, only from the closed vocabulary.

    The ``absent`` result this returns for a mapping that omits the key is the
    single most important value in the module: it is the durable, positive
    evidence that V4 did not ask for a reasoning level, as distinct from asking
    for the provider's default.
    """
    if raw is MISSING:
        return _shape(SHAPE_ABSENT)
    if raw is None:
        return _shape(SHAPE_NULL)
    if isinstance(raw, bool) or not isinstance(raw, str):
        return _shape(SHAPE_INVALID, detail=type(raw).__name__[:16])
    # ``provider_model`` is reused as this package's general bounded-identifier
    # grammar — length bound, NFKC normalization, invisible-codepoint refusal and
    # credential scan — not because an effort level is a model name. The closed
    # vocabulary is checked separately below.
    validated = guarded(
        lambda: redaction.provider_model(raw), None, reason="request:reasoning_effort"
    )
    if validated is None:
        return _shape(SHAPE_INVALID, detail="capture_failed")
    if not validated.is_valid:
        return _shape(SHAPE_INVALID, detail=validated.detail or validated.status)
    if validated.value not in KNOWN_REASONING_EFFORTS:
        # Already grammar-checked and credential-scanned, so it is a bounded
        # identifier and safe to carry as a diagnostic.
        return _shape(SHAPE_UNKNOWN, detail=validated.value)
    return _shape(SHAPE_VALID, value=validated.value)


def _model_name(raw: Any) -> dict[str, Any]:
    if raw is MISSING:
        return _shape(SHAPE_ABSENT)
    if raw is None:
        return _shape(SHAPE_NULL)
    validated = guarded(
        lambda: redaction.provider_model(raw), None, reason="request:model"
    )
    if validated is None:
        return _shape(SHAPE_INVALID, detail="capture_failed")
    if not validated.is_valid:
        return _shape(SHAPE_INVALID, detail=validated.detail or validated.status)
    return _shape(SHAPE_VALID, value=validated.value)


def _temperature(raw: Any) -> dict[str, Any]:
    """A sampling temperature: any finite real the request actually carried.

    ``bool`` is refused for the same reason a count refuses it. A non-finite
    float is refused outright rather than stored: ``NaN`` and ``Infinity`` are
    not JSON, so a record carrying one could not be serialized into the artifact
    this evidence exists to travel in.
    """
    if raw is MISSING:
        return _shape(SHAPE_ABSENT)
    if raw is None:
        return _shape(SHAPE_NULL)
    if isinstance(raw, bool):
        return _shape(SHAPE_INVALID, detail="bool")
    if not isinstance(raw, (int, float)):
        return _shape(SHAPE_INVALID, detail=type(raw).__name__[:16])
    if not math.isfinite(raw):
        return _shape(SHAPE_INVALID, detail="non_finite")
    if abs(raw) > _MAX_ABS_TEMPERATURE:
        return _shape(SHAPE_INVALID, detail="range")
    return _shape(SHAPE_VALID, value=raw)


_CLASSIFIERS: dict[str, Callable[[Any], dict[str, Any]]] = {
    "request_model": _model_name,
    "request_max_completion_tokens": _token_count,
    "request_max_tokens": _token_count,
    "request_reasoning_effort": _reasoning_effort,
    "request_temperature": _temperature,
}


# ─────────────────────────── extraction ───────────────────────────


def _read_key(mapping: Any, key: str) -> Any:
    """Read one allowlisted key, distinguishing "not there" from "unreadable"."""
    return guarded(lambda: mapping.get(key, MISSING), MISSING, reason=f"request:{key}")


def _unreadable(detail: str) -> dict[str, Any]:
    """Every field marked as not-looked-at, with one shared reason.

    ``missing`` rather than ``absent`` throughout: the mapping could not be read,
    so nothing was established about any field. Reporting ``absent`` here would
    claim an observation that was never made — and would, in particular, forge
    exactly the ``reasoning_effort`` absence proof this module exists to produce.
    """
    return {
        name: _shape(SHAPE_MISSING, detail=detail) for name in _CLASSIFIERS
    }


def openai_request_fields(mapping: Any) -> dict[str, dict[str, Any]]:
    """Classify the allowlisted controls of one OpenAI request mapping.

    ``mapping`` is either the adapter's ``kwargs`` or a parsed request body. Only
    the allowlist is consulted; the mapping is never iterated, never copied and
    never serialized, so a key this module does not name cannot reach a record
    even if a future adapter adds one.
    """
    if mapping is None:
        return _unreadable("mapping_missing")
    if not isinstance(mapping, dict):
        return _unreadable(type(mapping).__name__[:16])
    fields: dict[str, dict[str, Any]] = {}
    for name, key in OPENAI_REQUEST_ALLOWLIST:
        raw = _read_key(mapping, key)
        classifier = _CLASSIFIERS[name]
        fields[name] = guarded(
            lambda raw=raw, classifier=classifier: classifier(raw),
            _shape(SHAPE_INVALID, detail="capture_failed"),
            reason=f"request:classify:{name}",
        )
    return fields


def openai_request_shape(mapping: Any, *, surface: str = SURFACE_CHAT_COMPLETIONS):
    """A full request-shape payload observed at the adapter boundary."""
    payload: dict[str, Any] = dict(openai_request_fields(mapping))
    payload["request_api_surface"] = _shape(SHAPE_VALID, value=surface)
    payload["observation_point"] = POINT_ADAPTER_KWARGS
    return payload


# ─────────────────────────── the observer ───────────────────────────
#
# Bound by a caller that wants the evidence — today the evaluation harness — and
# absent everywhere else, exactly like the response-shape observer beside it.
# Nothing here writes to a database: the durable relations have no column for a
# request control, and adding a field to the durable type that durable storage
# does not carry is the defect that pattern already exists to avoid.

_request_observer: contextvars.ContextVar[Optional[Any]] = contextvars.ContextVar(
    "provider_telemetry_request_shape_observer", default=None
)


@contextlib.contextmanager
def request_shape_scope(observer: Optional[Any]) -> Iterator[Optional[Any]]:
    """Bind an observer for outbound request shape. Default is none at all."""
    token = _request_observer.set(observer)
    try:
        yield observer
    finally:
        _request_observer.reset(token)


def current_request_shape_observer() -> Optional[Any]:
    return _request_observer.get()


def publish_request_shape(builder: Callable[[], dict[str, Any]]) -> None:
    """Hand a request-shape payload to a bound observer. A no-op when there is none.

    Wrapped in the isolation boundary in full, and called from inside the
    adapter's own ``try``: a builder that raises, an observer that raises or a
    hostile mapping must cost this record and nothing else. Letting an exception
    escape here would surface as a provider failure for a call the provider
    answered correctly — the precise defect the capture guard exists to prevent.
    ``guard`` re-raises ``BaseException``, so a cancellation still propagates.
    """
    observer = _request_observer.get()
    if observer is None:
        return
    with guard("request_shape"):
        payload = builder()
        buffer = current_capture()
        if buffer is not None:
            payload["invocation_id"] = buffer.invocation_id
            payload["call_id"] = buffer.call_id
            payload["provider"] = buffer.provider
            payload["requested_model"] = buffer.requested_model
        observer.record_request_shape(payload)


# ───────────────────── the adapter-boundary observation ─────────────────────
#
# Why this is a wrapper around the SDK's own ``create`` rather than a line inside
# ``llm_client._call_openai``: the adapter lives in ``mas/llm_client.py``, which
# is one of the sixteen paths bound byte-for-byte to the certified product
# commit. Editing it would move the PRODUCT boundary — breaking the binding PR
# #118's Gate A and Gate B evidence rests on — to add a field that changes no
# behavior. A wave whose entire claim is behavior-neutrality should not be the
# one that invalidates a certification, so the observation is taken immediately
# *outside* the certified adapter instead, at the first point that still sees the
# real mapping.
#
# What it costs epistemically: nothing relative to reading the mapping inside the
# adapter. ``create(**kwargs)`` receives exactly the dict ``_call_openai`` built,
# with the same keys and the same values, one stack frame later.


class _Wrapped:
    """Marks a patched ``create`` so a nested scope cannot double-wrap it."""

    __slots__ = ("original", "call")

    def __init__(self, original, call):
        self.original = original
        self.call = call

    def __call__(self, *args, **kwargs):
        return self.call(*args, **kwargs)


@contextlib.contextmanager
def observe_openai_create(owner: Any) -> Iterator[Any]:
    """Observe every ``create(**kwargs)`` on ``owner`` for the duration.

    ``owner`` is either the SDK's ``AsyncCompletions`` class or a single
    completions instance. The wrapper publishes the allowlisted controls and then
    **returns the original call's result unchanged and un-awaited**, so the
    caller's ``await`` behaves exactly as it did before: no extra coroutine
    layer, no altered arguments, no swallowed exception, and no change to
    streaming semantics.

    A no-op if the shape is not what it pins, and reversible on exit.
    """
    original = guarded(lambda: getattr(owner, "create"), None, reason="request:sdk_create")
    if original is None or isinstance(original, _Wrapped):
        yield owner
        return

    def call(*args, **kwargs):
        # Publishing first, and guarded, so evidence exists even if the call
        # raises — and so a telemetry defect can never become a provider error.
        publish_request_shape(lambda: openai_request_shape(kwargs))
        return original(*args, **kwargs)

    # Whether `create` is defined on `owner` itself decides how it is put back:
    # deleting an attribute that was inherited restores the inherited one, while
    # deleting one the owner defined would remove it outright.
    owned = guarded(lambda: "create" in vars(owner), False, reason="request:sdk_owned")

    def install() -> bool:
        setattr(owner, "create", _Wrapped(original, call))
        return True

    installed = guarded(install, False, reason="request:sdk_patch")
    try:
        yield owner
    finally:
        if installed:
            with guard("request:sdk_restore"):
                if owned:
                    setattr(owner, "create", original)
                else:
                    delattr(owner, "create")


def openai_completions_class():
    """The SDK class whose ``create`` the adapter calls, or ``None``.

    Pinned by name and resolved through the SDK's own public re-export. Returns
    ``None`` rather than guessing when the installed SDK does not have this
    shape: an unobserved request must read as unobserved, never as a request
    that carried nothing.
    """

    def resolve():
        from openai.resources.chat.completions import AsyncCompletions

        return AsyncCompletions

    return guarded(resolve, None, reason="request:sdk_shape")


@contextlib.contextmanager
def observe_openai_sdk_requests() -> Iterator[Any]:
    """Bind the adapter-boundary observation for the installed OpenAI SDK."""
    owner = openai_completions_class()
    if owner is None:
        yield None
        return
    with observe_openai_create(owner):
        yield owner


__all__ = [
    "KNOWN_REASONING_EFFORTS",
    "OBSERVATION_POINTS",
    "OPENAI_REQUEST_ALLOWLIST",
    "OUTBOUND_REQUEST_FIELDS",
    "POINT_ADAPTER_KWARGS",
    "SURFACE_CHAT_COMPLETIONS",
    "current_request_shape_observer",
    "observe_openai_create",
    "observe_openai_sdk_requests",
    "openai_completions_class",
    "openai_request_fields",
    "openai_request_shape",
    "publish_request_shape",
    "request_shape_scope",
]
