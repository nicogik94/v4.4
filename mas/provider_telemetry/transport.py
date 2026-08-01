"""Transport-level instrumentation of the actual provider HTTP boundary.

Blocker 3 of the audit: "Anthropic and OpenAI SDK retries create multiple HTTP
requests represented by one aggregate telemetry row." Both SDKs retry
*internally*: one ``client.messages.create(...)`` can issue three HTTP requests,
sleep between them, and return a success whose row would have claimed a single
attempt with a single duration. A paired experiment reading that row would be
comparing invented numbers.

The instrumentation point is therefore ``httpx.AsyncBaseTransport``. Both SDKs
are httpx-based; ``handle_async_request`` is called **exactly once per actual
HTTP request**, including every SDK-internal retry, and — unlike httpx event
hooks — it also sees connection-level failures, where no response object is ever
produced.

The instrumentation is applied by wrapping the transports of the client the SDK
**already built for itself** (:func:`instrument_sdk_client`), never by handing
the SDK a client of ours. Both SDKs accept an ``http_client``, and using it was
the AUD-2 defect: the SDK's own default client is a ``DefaultAsyncHttpxClient``
carrying the SDK's connection limits (1000 / 100, against httpx's 100 / 20) and
``follow_redirects=True`` (against httpx's ``False``), so supplying any client
of our own silently reconfigured the runtime's network behavior — from a flag
documented as observational.

What this module deliberately does *not* do:

* it does not construct a provider's HTTP client, or change any of its
  configuration. It wraps transports in place and nothing else.
* it does not change ``max_retries``. Setting it to zero would make telemetry
  accurate by making the runtime worse, which is a behavioral policy change and
  out of scope. The SDK's retry policy is preserved exactly and merely observed.
* it does not read request or response **bodies**, and it does not store the
  header collection. Exactly three headers are consulted by name —
  ``request-id``, ``x-request-id`` and ``retry-after`` — and each value must pass
  its positive grammar before it is stored.
* it does not read the URL's query string. Only the path is kept, because a
  query string is where a proxy or a misconfigured base URL would carry a token.

In **strict** mode the attempt-start row is persisted here, synchronously,
immediately before the bytes leave, and a failure to persist prevents the
request from being sent. That is intentionally fail-closed and is *not*
behavior-neutral; it is the only posture suitable for a paired experiment,
precisely because it can promise that no external HTTP attempt exists without a
durable start.
"""
from __future__ import annotations

import logging
import sys
from typing import Any

from .capture import current_capture, guarded
from .identity import new_uuid, worker_id
from .models import (
    EVENT_CANCELLED,
    EVENT_COMPLETED,
    EVENT_PROVIDER_FAILURE,
    EVENT_UNKNOWN,
    POSTURE_STRICT,
    TRANSPORT_RESPONSE,
    HttpAttemptRecord,
    utc_now,
)
from . import redaction
from .values import ABSENT, MISSING

logger = logging.getLogger(__name__)

# The only response headers this package ever reads, by exact name.
PROVIDER_REQUEST_ID_HEADERS = ("request-id", "x-request-id")
RETRY_AFTER_HEADER = "retry-after"


class TelemetryStartUnavailable(RuntimeError):
    """A strict-mode attempt start could not be persisted, so nothing was sent.

    Surfaced to the SDK as a transport failure. The SDK's own retry policy then
    applies unchanged — each retry is itself a fresh attempt that must persist
    its own start, so a persistently unavailable sink means no provider request
    is made at all, which is exactly the fail-closed guarantee strict mode sells.
    """


def _header(headers: Any, name: str) -> Any:
    """Read one header by exact name. Never enumerates the collection."""
    if headers is None:
        return MISSING
    try:
        value = headers.get(name)
    except Exception:  # noqa: BLE001 - exotic header container
        return MISSING
    return MISSING if value is None else value


def _provider_request_id(headers: Any):
    for name in PROVIDER_REQUEST_ID_HEADERS:
        raw = _header(headers, name)
        if raw is not MISSING:
            return redaction.provider_request_id(raw)
    return ABSENT


def _request_path(request: Any) -> str:
    def read() -> str:
        url = getattr(request, "url", None)
        path = getattr(url, "path", "") or ""
        return str(path)

    return guarded(read, "", reason="request_path")


def _http_transport_base():
    import httpx

    return httpx.AsyncBaseTransport


def build_telemetry_transport(inner: Any, *, provider: str):
    """Wrap an httpx transport so every HTTP attempt is observed.

    Built lazily against the installed httpx so importing this package does not
    require httpx to be present.
    """
    base = _http_transport_base()

    class TelemetryTransport(base):  # type: ignore[misc, valid-type]
        """One ``handle_async_request`` call == one actual provider HTTP attempt."""

        def __init__(self, wrapped: Any, provider_name: str) -> None:
            self._inner = wrapped
            self._provider = provider_name

        async def __aenter__(self):  # pragma: no cover - delegation
            await self._inner.__aenter__()
            return self

        async def __aexit__(self, *exc_info):  # pragma: no cover - delegation
            return await self._inner.__aexit__(*exc_info)

        async def aclose(self) -> None:  # pragma: no cover - delegation
            await self._inner.aclose()

        async def handle_async_request(self, request):
            capture = current_capture()
            if capture is None:
                # The worker-level invariant, checked at the wire. A
                # strict-required process must not make a provider request
                # outside its experiment, and this is the last point at which
                # that can still be true — every entry point, every helper and
                # every direct SDK use funnels through here. It raises before
                # the bytes leave.
                _enforce_worker_posture()
                # Not capturing, and permitted: the wrapper is transparent and
                # adds one context-variable read to the request path.
                return await self._inner.handle_async_request(request)

            attempt_id = new_uuid()
            ordinal = len(capture.http_attempts) + 1
            started_at = utc_now()

            record = guarded(
                lambda: HttpAttemptRecord(
                    attempt_id=attempt_id,
                    invocation_id=capture.invocation_id,
                    call_id=capture.call_id,
                    telemetry_run_id=capture.telemetry_run_id,
                    posture=capture.posture,
                    worker_id=capture.worker_id or worker_id(),
                    provider=self._provider,
                    requested_model=capture.requested_model,
                    http_retry_ordinal=ordinal,
                    request_started_at=started_at,
                    request_method=getattr(request, "method", "POST"),
                    request_path=_request_path(request),
                ),
                None,
                reason="http_attempt_record",
            )

            if record is not None:
                capture.http_attempts.append(record)
                try:
                    await _persist_start(record)
                except TelemetryStartUnavailable:
                    # Strict posture, and the session already decided: do not
                    # send. Propagated to the SDK as a transport failure.
                    raise
                except Exception as exc:  # noqa: BLE001 - posture decides
                    # Belt and braces. The session is responsible for the
                    # fail-open/fail-closed decision, but a session that raises
                    # an unexpected error must not be able to stop an
                    # observational request: observational posture never lets
                    # telemetry change what the runtime does.
                    if capture.posture == POSTURE_STRICT:
                        raise TelemetryStartUnavailable(
                            f"attempt start failed: {type(exc).__name__}"
                        ) from exc
                    logger.warning(
                        "observational telemetry could not persist an attempt "
                        "start (%s); the provider request proceeds unchanged and "
                        "completeness is not guaranteed",
                        type(exc).__name__,
                    )
            elif capture.posture == POSTURE_STRICT:
                # Strict mode cannot send a request it is unable to describe.
                raise TelemetryStartUnavailable(
                    "strict telemetry could not construct an attempt-start record"
                )

            try:
                response = await self._inner.handle_async_request(request)
            except BaseException as exc:  # noqa: BLE001 - re-raised unchanged
                _record_transport_failure(capture, attempt_id, exc)
                raise

            _record_transport_response(capture, attempt_id, response)
            return response

    return TelemetryTransport(inner, provider)


def _enforce_worker_posture() -> None:
    """The process-wide strict guard, imported late to avoid a cycle."""
    from . import posture

    posture.enforce_provider_call("http_transport")


async def _persist_start(record: HttpAttemptRecord) -> None:
    """Write the attempt start. Fail-closed in strict mode, fail-open otherwise."""
    from . import service

    session = service.current_session()
    if session is None:
        if record.posture == POSTURE_STRICT:
            raise TelemetryStartUnavailable(
                "strict telemetry has no active session to persist an attempt start"
            )
        return
    await session.persist_attempt_start(record)


def _record_transport_failure(capture: Any, attempt_id: str, exc: BaseException) -> None:
    from .capture import transport_outcome_for_exception

    failure_class = guarded(
        lambda: redaction.failure_class(exc), "unclassified", reason="failure_class"
    )
    identity = guarded(lambda: redaction.exception_identity(exc), "", reason="error_identity")
    outcome = transport_outcome_for_exception(exc)
    kind = EVENT_CANCELLED if outcome == "cancelled" else (
        EVENT_PROVIDER_FAILURE if isinstance(exc, Exception) else EVENT_UNKNOWN
    )
    capture.record_http_terminal(
        attempt_id,
        event_kind=kind,
        transport_outcome=outcome,
        failure_class=failure_class,
        error_identity=identity,
    )


def _record_transport_response(capture: Any, attempt_id: str, response: Any) -> None:
    headers = guarded(lambda: getattr(response, "headers", None), None, reason="headers")
    status = guarded(
        lambda: redaction.http_status(getattr(response, "status_code", MISSING)),
        ABSENT,
        reason="http_status",
    )
    request_id = guarded(lambda: _provider_request_id(headers), ABSENT, reason="request_id")
    retry_after_raw = _header(headers, RETRY_AFTER_HEADER)
    retry_after = (
        ABSENT
        if retry_after_raw is MISSING
        else guarded(lambda: redaction.retry_after(retry_after_raw), ABSENT, reason="retry_after")
    )
    capture.record_http_terminal(
        attempt_id,
        event_kind=EVENT_COMPLETED,
        transport_outcome=TRANSPORT_RESPONSE,
        http_status=status,
        provider_request_id=request_id,
        retry_after=retry_after,
    )


# ─────────────────────────── SDK client construction ───────────────────────────


class TelemetryTransportUnsupported(RuntimeError):
    """This httpx build cannot be instrumented without changing its routing.

    Raised rather than returning a client that *looks* instrumented: silently
    losing the environment's proxy configuration would mean telemetry-on and
    telemetry-off take different network routes, which is the one thing an
    instrumentation layer must never do.
    """


# The two attributes this module reaches into on an ``httpx.AsyncClient``. They
# are private, so their presence is asserted rather than assumed, and their
# absence is a loud failure.
_CLIENT_TRANSPORT_ATTR = "_transport"
_CLIENT_MOUNTS_ATTR = "_mounts"

# Set on a client once its transports are wrapped, so a second call cannot nest
# a wrapper inside a wrapper and double-count every HTTP attempt.
_INSTRUMENTED_MARKER = "_mas_provider_telemetry_instrumented"


def instrument_http_client(client: Any, *, provider: str):
    """Wrap every transport an already-constructed httpx client will route to.

    ``httpx.AsyncClient`` decides *at construction time* whether to read
    ``HTTP_PROXY`` / ``HTTPS_PROXY`` / ``NO_PROXY``::

        allow_env_proxies = trust_env and transport is None

    so handing it ``transport=`` — which is what this module used to do — turns
    environment proxy support off for the whole client without saying so. A
    deployment behind a mandatory egress proxy would route directly with
    telemetry on and through the proxy with telemetry off: two different network
    paths, one of which probably does not work, decided by a flag that claims to
    be observational.

    Wrapping *after* construction inverts that. httpx builds its own transport
    and its own proxy mounts from the environment exactly as it would with
    telemetry off — including the ``None`` mounts ``NO_PROXY`` produces, which
    mean "fall back to the default transport" and are therefore left alone — and
    each resulting transport is then wrapped in place. The client's routing
    table, TLS configuration, timeouts, limits, connection pool, ``trust_env``,
    redirect policy and shutdown semantics are whatever httpx made them.
    """
    for attribute in (_CLIENT_TRANSPORT_ATTR, _CLIENT_MOUNTS_ATTR):
        if not hasattr(client, attribute):
            raise TelemetryTransportUnsupported(
                f"this httpx client exposes no {attribute}; provider telemetry "
                "will not silently replace its transport configuration"
            )

    if getattr(client, _INSTRUMENTED_MARKER, False):
        raise TelemetryTransportUnsupported(
            "this httpx client is already instrumented; wrapping it twice would "
            "record two attempts for every HTTP request"
        )

    default = getattr(client, _CLIENT_TRANSPORT_ATTR)
    if default is None:  # pragma: no cover - httpx always builds one
        raise TelemetryTransportUnsupported("httpx client has no default transport")

    # Both replacements are computed before either is installed: a client that
    # ends up with a wrapped default transport and unwrapped mounts would route
    # some URLs through instrumentation and others around it, which is a
    # different client again. Either the whole routing table is instrumented or
    # none of it is.
    wrapped_default = build_telemetry_transport(default, provider=provider)
    wrapped_mounts = {
        # A ``None`` mount is httpx's "use the default transport" marker — it is
        # exactly how NO_PROXY exempts a host — so it must stay None rather than
        # become a wrapper around nothing.
        pattern: (None if mounted is None
                  else build_telemetry_transport(mounted, provider=provider))
        for pattern, mounted in (getattr(client, _CLIENT_MOUNTS_ATTR) or {}).items()
    }

    setattr(client, _CLIENT_TRANSPORT_ATTR, wrapped_default)
    setattr(client, _CLIENT_MOUNTS_ATTR, wrapped_mounts)
    setattr(client, _INSTRUMENTED_MARKER, True)
    return client


class TelemetrySdkShapeUnsupported(TelemetryTransportUnsupported):
    """The installed provider SDK does not have the shape telemetry pins.

    A subclass of :class:`TelemetryTransportUnsupported` so every caller that
    already fails closed on "this cannot be instrumented" keeps doing so. It is
    raised rather than worked around because the only available workaround —
    handing the SDK a client telemetry built itself — is precisely the defect
    this contract exists to prevent.
    """


# The private boundary this module depends on, stated once, explicitly.
#
# Both ``anthropic.AsyncAnthropic`` and ``openai.AsyncOpenAI`` build their own
# ``httpx.AsyncClient`` in ``AsyncAPIClient.__init__`` and keep it on ``_client``.
# That attribute is private, and nothing about it is guaranteed across SDK
# releases — so it is *asserted* on every call rather than assumed, and a change
# in shape is a loud failure instead of a silent fallback.
#
# The alternative, passing ``http_client=`` to the SDK constructor, is what this
# module used to do and is exactly the defect being fixed: the SDK's own default
# client is *not* a bare ``httpx.AsyncClient``. It is a ``DefaultAsyncHttpxClient``
# subclass carrying the SDK's own limits (1000 / 100), ``follow_redirects=True``,
# TCP keepalive socket options and, for Anthropic, an explicitly built proxy mount
# table. Supplying any client of our own silently replaces all of it.
_SDK_HTTP_CLIENT_ATTR = "_client"
# Each SDK publicly re-exports the class it uses for its own default client, so
# "the SDK built this client with its own defaults" is checkable without reaching
# any further into private internals than ``_client`` itself.
_SDK_DEFAULT_CLIENT_EXPORT = "DefaultAsyncHttpxClient"


def sdk_http_client(sdk_client: Any):
    """The httpx client a constructed provider SDK client built for itself.

    Fails loudly unless the installed SDK still has the pinned shape: a private
    ``_client`` holding an ``httpx.AsyncClient`` that is an instance of the SDK's
    own published ``DefaultAsyncHttpxClient``. The last check is the load-bearing
    one — it is what distinguishes "the SDK applied its own defaults" from "some
    caller supplied a plain httpx client", and the whole point of this remediation
    is that those two are not the same client.
    """
    import httpx

    root = type(sdk_client).__module__.split(".")[0]
    sdk_module = sys.modules.get(root)
    if sdk_module is None:  # pragma: no cover - the SDK is imported by its caller
        raise TelemetrySdkShapeUnsupported(
            f"provider SDK package {root!r} is not importable for shape pinning"
        )

    if not hasattr(sdk_client, _SDK_HTTP_CLIENT_ATTR):
        raise TelemetrySdkShapeUnsupported(
            f"{root} client exposes no {_SDK_HTTP_CLIENT_ATTR}; provider telemetry "
            "will not substitute a client of its own"
        )
    http_client = getattr(sdk_client, _SDK_HTTP_CLIENT_ATTR)
    if not isinstance(http_client, httpx.AsyncClient):
        raise TelemetrySdkShapeUnsupported(
            f"{root}.{_SDK_HTTP_CLIENT_ATTR} is not an httpx.AsyncClient "
            f"(got {type(http_client).__name__})"
        )

    default_cls = getattr(sdk_module, _SDK_DEFAULT_CLIENT_EXPORT, None)
    if default_cls is None:
        raise TelemetrySdkShapeUnsupported(
            f"{root} publishes no {_SDK_DEFAULT_CLIENT_EXPORT}; telemetry cannot "
            "confirm the SDK applied its own client defaults"
        )
    if not isinstance(http_client, default_cls):
        raise TelemetrySdkShapeUnsupported(
            f"{root}.{_SDK_HTTP_CLIENT_ATTR} is a {type(http_client).__name__}, not "
            f"the SDK's own {_SDK_DEFAULT_CLIENT_EXPORT}; its effective connection "
            "limits and redirect policy are not the SDK's"
        )
    return http_client


def instrument_sdk_client(sdk_client: Any, *, provider: str):
    """Instrument the client an already-constructed provider SDK made for itself.

    This is the only supported way telemetry reaches the provider HTTP boundary.
    The SDK is constructed exactly as a telemetry-off build constructs it — no
    ``http_client=`` argument, no telemetry-chosen timeout, limits, redirect
    policy, proxy mounts, TLS settings or socket options — and the client it
    built is then instrumented *in place* by wrapping its transports.

    The result is one client, with the SDK's configuration, whose transports
    happen to be observed. There is no second client, and there is no path on
    which telemetry constructs one: if the SDK's shape is not the pinned shape
    this raises, and the caller either fails closed (strict) or leaves the SDK
    entirely uninstrumented (observational). It never downgrades to a client with
    different effective behavior.
    """
    instrument_http_client(sdk_http_client(sdk_client), provider=provider)
    return sdk_client


def is_instrumented(client: Any) -> bool:
    """Whether this httpx client's transports are telemetry-wrapped."""
    return bool(getattr(client, _INSTRUMENTED_MARKER, False))
