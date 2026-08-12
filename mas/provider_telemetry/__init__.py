"""Provider attempt telemetry (R2, remediated).

Durable, append-only, immutable metadata for every provider attempt the runtime
gateway makes — recorded at the granularity of the **actual HTTP request**, so an
SDK that retried internally three times produces three attempt identities rather
than one aggregate row that averages them.

Two postures, and only one of them is suitable for an experiment:

``observational``
    Default-off, explicitly enabled. Never changes prompts, routing, models,
    retry policy, fallback order, breaker decisions, cache keys or phase
    ordering. Telemetry failures fail open. **Completeness is not guaranteed**
    and is never claimed — the health report and every export say so.

``strict``
    Every actual HTTP attempt persists a durable start *before* transport, and a
    start that cannot be persisted means the request is not sent. Completion
    events are delivered asynchronously (an unmatched start is independently
    detectable) and the run is reconciled at a run-end barrier. A run is
    certified only when every start has exactly one truthful terminal state, no
    event is undurable, no write was ambiguous, and every worker drained.

    Strict-mode start persistence is intentionally fail-closed and is **not**
    behavior-neutral.

See :mod:`provider_telemetry.service` for the postures, :mod:`.transport` for the
HTTP-attempt boundary, :mod:`.delivery` for non-blocking completion delivery, and
:mod:`.redaction` for the positive grammars that keep provider-controlled text
out of durable storage.
"""
from __future__ import annotations

from .capture import (
    COUNT_STATUSES,
    PRESENCE_STATUSES,
    RESPONSE_SHAPE_FIELDS,
    SHAPE_ABSENT,
    SHAPE_EMPTY,
    SHAPE_INVALID,
    SHAPE_MISSING,
    SHAPE_NONEMPTY,
    SHAPE_NULL,
    SHAPE_UNKNOWN,
    SHAPE_UNSUPPORTED,
    SHAPE_VALID,
    InvocationCapture,
    anthropic_response_shape,
    capture_scope,
    current_capture,
    current_response_shape_observer,
    guard,
    guarded,
    is_capturing,
    observe_anthropic_response,
    observe_exception,
    observe_openai_response,
    openai_response_shape,
    reset_capture_log_latch,
    response_shape_scope,
)
from .delivery import (
    AmbiguousWrite,
    DeliveryCounters,
    DrainResult,
    EventDelivery,
    NullDelivery,
)
from .identity import (
    ENTRY_POINTS,
    ENTRY_POINT_API_MANUAL_PHASE,
    ENTRY_POINT_API_WORKFLOW_RUN,
    ENTRY_POINT_CDP_REPORT_GATEWAY,
    ENTRY_POINT_CLI_SINGLE_PHASE,
    ENTRY_POINT_CLI_WORKFLOW,
    ENTRY_POINT_DIRECT_GATEWAY,
    ENTRY_POINT_EVALUATION_JUDGE,
    ENTRY_POINT_EVALUATION_PHASE,
    ENTRY_POINT_T1A_VALIDATION,
    ENTRY_POINT_UNKNOWN,
    IDENTITY_BEARING_ENTRY_POINTS,
    TelemetryIdentity,
    TelemetryIdentityError,
    as_project_uuid,
    bind_identity,
    current_identity,
    new_uuid,
    worker_id,
)
from .models import (
    BREAKER_CLOSED,
    BREAKER_OPEN,
    BREAKER_STATES,
    BREAKER_UNKNOWN,
    DRAIN_STATUSES,
    EVENT_CANCELLED,
    EVENT_CAPTURE_FAILURE,
    EVENT_COMPLETED,
    EVENT_KINDS,
    EVENT_OBSERVATION,
    EVENT_PROVIDER_FAILURE,
    EVENT_SKIPPED,
    EVENT_TRANSFORMATION_FAILURE,
    EVENT_UNKNOWN,
    INVOCATION_KINDS,
    INVOCATION_PROVIDER_CALL,
    INVOCATION_SKIPPED_CANDIDATE,
    MIGRATION_NAME,
    NON_TERMINAL_EVENT_KINDS,
    OBSERVABLE_PROVIDER_FIELDS,
    POSTURE_OBSERVATIONAL,
    POSTURE_STRICT,
    POSTURES,
    RECONCILIATION_STATUSES,
    SUBJECT_HTTP_ATTEMPT,
    SUBJECT_KINDS,
    SUBJECT_SDK_INVOCATION,
    TELEMETRY_SCHEMA_VERSION,
    TERMINAL_EVENT_KINDS,
    TRANSPORT_OUTCOMES,
    AttemptEvent,
    BreakerSnapshot,
    HttpAttemptRecord,
    ProviderObservation,
    RunEvent,
    SdkInvocationRecord,
    TelemetryCallRecord,
    TelemetryRecordError,
    TelemetryRunRecord,
    canonical_fingerprint,
    new_identity,
    utc_now,
)
from .repository import (
    APPEND_ONLY_TABLES,
    ATTEMPT_TABLE,
    CALL_TABLE,
    EVENT_TABLE,
    INVOCATION_TABLE,
    LEDGER_TABLE,
    READ_COLUMNS,
    RUN_EVENT_TABLE,
    RUN_TABLE,
    TELEMETRY_TABLES,
    WRITE_COLUMNS,
    NullTelemetrySink,
    PostgresTelemetrySink,
    ProviderTelemetryStorageUnavailable,
    classify_write_failure,
    row_to_export_dict,
)
from .service import (
    OBSERVATIONAL_COMPLETENESS_NOTICE,
    POSTURE_OFF,
    STRICT_COMPLETENESS_NOTICE,
    NullTelemetrySession,
    PreflightResult,
    RunAttestation,
    StrictPreflightFailed,
    TelemetrySession,
    configured_posture,
    current_session,
    open_invocation_capture,
    request_config_fingerprint,
    routing_decision_fingerprint,
    run_session,
    runtime_fingerprint,
    source_commit,
    strict_mode_configured,
    strict_preflight,
    telemetry_enabled,
    telemetry_scope,
)
from .posture import (
    NON_EXPERIMENT_ENV,
    StrictPostureMisconfigured,
    StrictPostureViolation,
    configuration_problems,
    enforce_provider_call,
    non_experiment,
    require_strict_scope_posture,
    require_valid_configuration,
    scope_state,
    strict_required,
)
from .request_shape import (
    KNOWN_REASONING_EFFORTS,
    OBSERVATION_POINTS,
    OPENAI_REQUEST_ALLOWLIST,
    OUTBOUND_REQUEST_FIELDS,
    POINT_ADAPTER_KWARGS,
    SURFACE_CHAT_COMPLETIONS,
    current_request_shape_observer,
    observe_openai_create,
    observe_openai_sdk_requests,
    openai_completions_class,
    openai_request_fields,
    openai_request_shape,
    request_shape_scope,
)
from .transport import (
    TelemetrySdkShapeUnsupported,
    TelemetryStartUnavailable,
    TelemetryTransportUnsupported,
    build_telemetry_transport,
    instrument_http_client,
    instrument_sdk_client,
    is_instrumented,
    sdk_http_client,
)
from .values import (
    MAX_USAGE_VALUE,
    VALUE_ABSENT,
    VALUE_INVALID,
    VALUE_NULL,
    VALUE_REDACTED,
    VALUE_STATUSES,
    VALUE_UNKNOWN_VALUE,
    VALUE_UNSUPPORTED,
    VALUE_VALID,
    ProviderValue,
    exact_nonnegative_int,
)

__all__ = [name for name in dir() if not name.startswith("_")]
