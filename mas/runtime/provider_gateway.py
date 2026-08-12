"""Compatibility gateway around provider calls and semantic cache hooks."""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import re
from typing import Any, Awaitable, Callable

from config import (
    FALLBACK_CHAIN,
    MAX_RETRIES,
    MODEL_ROUTING,
    RETRY_DELAYS,
    TASK_PROFILE_BY_PHASE,
    TASK_PROFILE_MODEL_CANDIDATES,
    ModelConfig,
    Provider,
    RUNTIME_LAYER,
)
from extensions.runtime import (
    GatewayRequest,
    GatewayResponse,
    ProviderSelection,
    RoutingContext,
    RoutingConfig,
)
from provider_telemetry import capture as attempt_capture
from provider_telemetry import posture as telemetry_posture
from provider_telemetry import service as telemetry_service
from provider_telemetry.identity import current_identity, new_uuid, worker_id
from provider_telemetry.models import (
    BREAKER_CLOSED,
    BREAKER_OPEN,
    EVENT_COMPLETED,
    EVENT_PROVIDER_FAILURE,
    EVENT_SKIPPED,
    INVOCATION_PROVIDER_CALL,
    INVOCATION_SKIPPED_CANDIDATE,
    BreakerSnapshot,
    SdkInvocationRecord,
    TelemetryCallRecord,
    UNKNOWN_BREAKER,
    utc_now,
)


logger = logging.getLogger(__name__)

# V7's certified production boundary is Anthropic-only.  Keep the dormant
# OpenAI adapter and candidate metadata available for future provider-resilience
# work, but never make OpenAI eligible in the supported runtime.  In particular,
# credential presence is availability evidence, not release authorization.
V7_SUPPORTED_PROVIDERS = frozenset({Provider.ANTHROPIC})

AnthropicExecutor = Callable[[str, str, str, int, float, int], Awaitable[Any]]
OpenAIExecutor = Callable[[str, str, str, int, float], Awaitable[Any]]
BeforeAttemptHook = Callable[[ModelConfig], Any]

AUTH_ERROR = "auth_error"
QUOTA_EXCEEDED = "quota_exceeded"
RATE_LIMITED = "rate_limited"
TIMEOUT = "timeout"
PROVIDER_UNAVAILABLE = "provider_unavailable"
SERVER_ERROR = "server_error"
CONNECTION_ERROR = "connection_error"
INVALID_REQUEST = "invalid_request"
TRANSPORT_MALFORMED_RESPONSE = "transport_malformed_response"
MODEL_SCHEMA_INVALID = "model_schema_invalid"
UNKNOWN_PROVIDER_ERROR = "unknown_provider_error"
# A transport-level success that carried no usable visible text. These are
# *content contract* failures, not transport outages: the HTTP call succeeded,
# tokens were billed, and the provider still returned nothing this system can
# analyse. They are kept distinct from each other because only one of them has
# a known cause.
OUTPUT_TOKEN_EXHAUSTED = "output_token_exhausted"
EMPTY_PROVIDER_OUTPUT = "empty_provider_output"
PROVIDER_DETAIL_MARKER = "; provider_detail="
_PROVIDER_DETAIL_MESSAGE_MAX_CHARS = 500

RETRYABLE_PROVIDER_ERRORS = {
    RATE_LIMITED,
    TIMEOUT,
    PROVIDER_UNAVAILABLE,
    SERVER_ERROR,
    CONNECTION_ERROR,
    TRANSPORT_MALFORMED_RESPONSE,
}

# Unusable output is deliberately *not* retryable. Re-issuing a byte-identical
# request against the same candidate with the same token budget reproduces the
# same exhaustion, so a retry buys nothing and bills twice. It is fallback
# eligible because a different candidate has a genuinely different output-budget
# regime. Because the breaker is only fed by retryable failures, an unusable
# response also never counts toward opening a circuit: the provider is healthy,
# this request's output contract is not.
UNUSABLE_OUTPUT_ERRORS = {
    OUTPUT_TOKEN_EXHAUSTED,
    EMPTY_PROVIDER_OUTPUT,
}

FALLBACK_ELIGIBLE_ERRORS = RETRYABLE_PROVIDER_ERRORS | UNUSABLE_OUTPUT_ERRORS | {
    AUTH_ERROR,
    QUOTA_EXCEEDED,
}

GOVERNANCE_ERROR_TYPES = {"kill_switch", "budget", "breaker", "approval", "policy"}

_NORMALIZED_STOP_REASONS = {
    "end_turn": "end_turn",
    "stop": "end_turn",
    "max_tokens": "max_tokens",
    "length": "max_tokens",
    "stop_sequence": "stop_sequence",
    "tool_use": "tool_use",
    "tool_calls": "tool_use",
    "pause_turn": "pause_turn",
    "refusal": "refusal",
    "content_filter": "content_filter",
}

# The normalized stop reasons that mean "the model ran out of output budget".
_OUTPUT_EXHAUSTION_STOP_REASONS = frozenset({"max_tokens"})


def normalize_stop_reason(value) -> str:
    """Return a bounded cross-provider stop reason without raw metadata."""
    if value is None:
        return ""
    return _NORMALIZED_STOP_REASONS.get(str(value).strip().lower(), "other")


def has_usable_visible_text(text: Any) -> bool:
    """True when a provider returned visible text a phase could actually use.

    Whitespace-only output counts as unusable: every V4 phase consumes the
    visible text either as JSON or as prose, and neither can be built from it.
    """
    return bool(str(text or "").strip())


def classify_unusable_output(*, text: Any, stop_reason: Any) -> str:
    """Return the failure category for output with no usable visible text.

    Returns "" when the response carries usable text — including *partial*
    text truncated at max_tokens, which stays a success so the deterministic
    structured-output recovery downstream keeps its input.
    """
    if has_usable_visible_text(text):
        return ""
    if normalize_stop_reason(stop_reason) in _OUTPUT_EXHAUSTION_STOP_REASONS:
        return OUTPUT_TOKEN_EXHAUSTED
    return EMPTY_PROVIDER_OUTPUT


def unusable_output_detail(*, text: Any, stop_reason: Any, output_tokens: Any) -> str:
    """Bounded diagnostic for an unusable response.

    Built only from a closed stop-reason vocabulary, an integer token count and
    a fixed shape label. No provider text, refusal text, reasoning text, prompt
    text or header ever reaches it.
    """
    if text is None:
        shape = "none"
    elif str(text) == "":
        shape = "empty"
    else:
        shape = "whitespace"
    try:
        tokens = int(output_tokens or 0)
    except (TypeError, ValueError):
        tokens = 0
    return (
        f"stop_reason={normalize_stop_reason(stop_reason) or 'unset'} "
        f"visible_text={shape} output_tokens={tokens}"
    )


def _safe_detail_scalar(value: Any, *, max_chars: int = _PROVIDER_DETAIL_MESSAGE_MAX_CHARS) -> str:
    text = str(value or "")
    text = " ".join(text.split())
    if not text:
        return ""
    redactions = [
        (r"sk-ant-[A-Za-z0-9_\-]+", "sk-ant-[REDACTED]"),
        (r"sk-[A-Za-z0-9_\-]{8,}", "sk-[REDACTED]"),
        (r"(?i)authorization\s*:\s*bearer\s+[^,\s;\"']+", "Authorization: Bearer [REDACTED]"),
        (r"(?i)(api[_-]?key|token|secret)\s*[:=]\s*[^,\s;\"']+", r"\1=[REDACTED]"),
        (r"PROMPT_SENTINEL_DO_NOT_LEAK", "[REDACTED_PROMPT_SENTINEL]"),
        (r"RAW_PROMPT_SENTINEL", "[REDACTED_PROMPT_SENTINEL]"),
        (r"RAW_RESPONSE_SENTINEL_DO_NOT_LEAK", "[REDACTED_RESPONSE_SENTINEL]"),
        (r"RAW_RESPONSE_SENTINEL", "[REDACTED_RESPONSE_SENTINEL]"),
        (r"[A-Za-z]:\\[^,\s\"']+", "[REDACTED_PATH]"),
        (r"/(?:home|Users|tmp|var|etc|mnt)/[^,\s\"']+", "[REDACTED_PATH]"),
    ]
    for pattern, replacement in redactions:
        text = re.sub(pattern, replacement, text)
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "..."
    return text


def _safe_detail_token(value: Any, *, max_chars: int = 160) -> str:
    text = _safe_detail_scalar(value, max_chars=max_chars)
    return re.sub(r"[^A-Za-z0-9_.:@\-]+", "_", text).strip("_")


def _body_path(body: Any, *path: str) -> Any:
    current = body
    for key in path:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            current = getattr(current, key, None)
        if current is None:
            return None
    return current


def safe_provider_error_detail(exc: Exception) -> str:
    """Return bounded provider error detail safe for CI logs and artifacts."""
    body = getattr(exc, "body", None)
    status_code = getattr(exc, "status_code", None)
    request_id = getattr(exc, "request_id", None)
    if not request_id:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
        if headers:
            request_id = headers.get("request-id") or headers.get("x-request-id")

    error_type = (
        getattr(exc, "type", None)
        or _body_path(body, "error", "type")
        or _body_path(body, "type")
    )
    message = (
        _body_path(body, "error", "message")
        or _body_path(body, "message")
        or getattr(exc, "message", "")
        or str(exc)
    )

    fields: list[str] = []
    if isinstance(status_code, int):
        fields.append(f"status_code={status_code}")
    exception_name = _safe_detail_token(exc.__class__.__name__, max_chars=80)
    if exception_name and (fields or error_type or request_id or message):
        fields.append(f"exception={exception_name}")
    safe_error_type = _safe_detail_token(error_type, max_chars=120)
    if safe_error_type:
        fields.append(f"error_type={safe_error_type}")
    safe_request_id = _safe_detail_token(request_id, max_chars=160)
    if safe_request_id:
        fields.append(f"request_id={safe_request_id}")
    safe_message = _safe_detail_scalar(message)
    if safe_message:
        fields.append(f'message="{safe_message}"')
    return " ".join(fields)


def provider_error_detail_from_message(message: str) -> str:
    if PROVIDER_DETAIL_MARKER not in (message or ""):
        return ""
    return (message or "").split(PROVIDER_DETAIL_MARKER, 1)[1].strip()


def runtime_routing_config() -> RoutingConfig:
    task_profile_candidates = {
        key: list(value)
        for key, value in TASK_PROFILE_MODEL_CANDIDATES.items()
    }
    for key, value in RUNTIME_LAYER.task_profile_model_candidates.items():
        task_profile_candidates[key] = list(value)
    return RoutingConfig(
        default_provider=RUNTIME_LAYER.default_provider.value,
        routing_strategy=RUNTIME_LAYER.routing_strategy,
        cache_enabled=RUNTIME_LAYER.cache_enabled,
        cache_ttl_seconds=RUNTIME_LAYER.cache_ttl_seconds,
        phase_overrides=dict(RUNTIME_LAYER.phase_model_overrides),
        complexity_routes=dict(RUNTIME_LAYER.complexity_model_overrides),
        task_profile_candidates=task_profile_candidates,
    )


def build_cache_key(request: GatewayRequest, selection: ProviderSelection) -> str:
    payload = {
        "phase": request.phase,
        "provider": selection.provider,
        "model": selection.model,
        "task_profile": selection.task_profile,
        "task_type": request.routing_context.task_type,
        "complexity_hint": request.routing_context.complexity_hint,
        "risk_classification": request.routing_context.risk_classification,
        "explicit_model": request.routing_context.explicit_model,
        "system_prompt": request.system_prompt,
        "user_prompt": request.user_prompt,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def task_profile_for_phase(phase: str) -> str:
    return TASK_PROFILE_BY_PHASE.get(phase, TASK_PROFILE_BY_PHASE["audit"])


def select_model_config(
    phase: str,
    *,
    config_override: ModelConfig | None = None,
    routing_context: RoutingContext | None = None,
    routing_config: RoutingConfig | None = None,
) -> tuple[ModelConfig, ProviderSelection]:
    candidates = select_model_candidates(
        phase,
        config_override=config_override,
        routing_context=routing_context,
        routing_config=routing_config,
    )
    return candidates[0]


def select_model_candidates(
    phase: str,
    *,
    config_override: ModelConfig | None = None,
    routing_context: RoutingContext | None = None,
    routing_config: RoutingConfig | None = None,
) -> list[tuple[ModelConfig, ProviderSelection]]:
    routing_context = routing_context or RoutingContext(phase=phase)
    routing_config = routing_config or runtime_routing_config()
    task_profile = task_profile_for_phase(phase)
    base = _phase_default_config(phase)

    if config_override is not None:
        primary = (
            config_override,
            ProviderSelection(
                provider=config_override.provider.value,
                model=config_override.model,
                reason="config_override",
                task_profile=task_profile,
            ),
        )
        return _dedupe_candidates(
            [primary] + _profile_and_chain_candidates(phase, base, routing_config, task_profile)
        )

    selected_model = ""
    reason = "phase_routing"
    if routing_context.explicit_model:
        selected_model = routing_context.explicit_model
        reason = "explicit_model"
    elif routing_config.phase_overrides.get(phase):
        selected_model = routing_config.phase_overrides[phase]
        reason = "phase_override"
    elif routing_context.complexity_hint and routing_config.complexity_routes.get(routing_context.complexity_hint):
        selected_model = routing_config.complexity_routes[routing_context.complexity_hint]
        reason = f"complexity:{routing_context.complexity_hint}"

    candidates: list[tuple[ModelConfig, ProviderSelection]] = []
    if selected_model:
        selected = _config_from_model_alias(selected_model, base, routing_config.default_provider)
        candidates.append((
            selected,
            ProviderSelection(
                provider=selected.provider.value,
                model=selected.model,
                reason=reason,
                task_profile=task_profile,
            ),
        ))
    else:
        candidates.append((
            base,
            ProviderSelection(
                provider=base.provider.value,
                model=base.model,
                reason=reason,
                task_profile=task_profile,
            ),
        ))

    candidates.extend(_profile_and_chain_candidates(phase, base, routing_config, task_profile))
    return _dedupe_candidates(candidates)


def _phase_default_config(phase: str) -> ModelConfig:
    return MODEL_ROUTING.get(phase, MODEL_ROUTING["audit"])


def _profile_and_chain_candidates(
    phase: str,
    base: ModelConfig,
    routing_config: RoutingConfig,
    task_profile: str,
) -> list[tuple[ModelConfig, ProviderSelection]]:
    candidates: list[tuple[ModelConfig, ProviderSelection]] = []
    aliases = routing_config.task_profile_candidates.get(task_profile)
    if aliases is None:
        aliases = TASK_PROFILE_MODEL_CANDIDATES.get(task_profile, [])

    for alias in aliases:
        alias = (alias or "").strip()
        if not alias:
            continue
        if alias == "phase_default":
            config = base
            reason = "phase_default"
        else:
            config = _config_from_model_alias(alias, base, routing_config.default_provider)
            reason = f"task_profile:{task_profile}"
        candidates.append((
            config,
            ProviderSelection(
                provider=config.provider.value,
                model=config.model,
                reason=reason,
                task_profile=task_profile,
            ),
        ))

    for provider in (base.provider, Provider.OPENAI if base.provider == Provider.ANTHROPIC else Provider.ANTHROPIC):
        for model in FALLBACK_CHAIN.get(provider, []):
            config = ModelConfig(
                provider=provider,
                model=model,
                max_tokens=base.max_tokens,
                temperature=base.temperature,
                thinking_budget=base.thinking_budget if provider == Provider.ANTHROPIC else 0,
                # Dropped for the same reason as thinking_budget: both are
                # Anthropic-only controls on `base`, and this chain candidate may
                # be a different provider. Carrying the reservation across that
                # boundary would claim a guarantee _call_openai never implements.
                min_response_tokens=(
                    base.min_response_tokens if provider == Provider.ANTHROPIC else None
                ),
            )
            candidates.append((
                config,
                ProviderSelection(
                    provider=provider.value,
                    model=model,
                    reason=f"fallback_chain:{provider.value}",
                    task_profile=task_profile,
                ),
            ))
    return candidates


def _config_from_model_alias(alias: str, base: ModelConfig, default_provider: str) -> ModelConfig:
    provider: Provider | None = None
    model = alias
    if ":" in alias:
        provider_name, model = alias.split(":", 1)
        provider = Provider(provider_name.strip())
        model = model.strip()
    else:
        provider = _infer_provider(model)
    provider = provider or Provider(default_provider or base.provider.value)
    return ModelConfig(
        provider=provider,
        model=model,
        max_tokens=base.max_tokens,
        temperature=base.temperature,
        thinking_budget=base.thinking_budget if provider == Provider.ANTHROPIC else 0,
        # See _profile_and_chain_candidates: an alias may resolve to a provider
        # other than `base`'s, and the reservation does not survive that crossing.
        min_response_tokens=(
            base.min_response_tokens if provider == Provider.ANTHROPIC else None
        ),
    )


def _dedupe_candidates(
    candidates: list[tuple[ModelConfig, ProviderSelection]]
) -> list[tuple[ModelConfig, ProviderSelection]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[tuple[ModelConfig, ProviderSelection]] = []
    for config, selection in candidates:
        key = (config.provider.value, config.model)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((config, selection))
    return deduped


class _UsageLedger:
    """Cumulative provider usage for one logical gateway call.

    A ``GatewayResponse`` returned by ``_call_with_fallbacks`` describes the
    *logical call*, not one attempt: it already carries ``attempt_count``, the
    ``attempts`` list and the fallback identity. Its usage fields are consumed
    exactly once per logical call by the budget ledger — ``call_llm`` copies
    them onto ``LLMResponse`` and the orchestrator hands them to
    ``policy.record_consumption_to_state``, which also increments
    ``llm_call_count`` by one. So the usage a logical response reports has to be
    that call's *total* provider spend. Reporting only the attempt whose
    response object happened to be returned makes every other executed attempt
    invisible to the ``max_total_cost_usd`` cap even though it was billed.

    Two properties keep the accounting honest:

    * every executed attempt is recorded exactly once, at the single point where
      a real provider response first exists — so a candidate skipped as
      unavailable, skipped with the breaker open, or refused by governance
      before the provider was called contributes nothing;
    * ``apply`` *assigns* the totals rather than adding to them, so no return
      path can count the same response twice however often it runs.

    Per-attempt truth is unaffected: the ``attempts`` metadata and the
    provider-attempt telemetry records stay per-attempt and are never
    overwritten with these aggregates.
    """

    __slots__ = ("input_tokens", "output_tokens", "cache_read_tokens", "cost_usd")

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0
        self.cost_usd = 0.0

    def record(self, response: GatewayResponse) -> None:
        """Add one executed provider attempt's *measured* usage.

        Usage the provider did not report arrives here as zero and is added as
        zero. Nothing is estimated, inferred or back-filled: a transport failure
        that never produced a billable response still contributes nothing.
        """
        self.input_tokens += int(response.input_tokens or 0)
        self.output_tokens += int(response.output_tokens or 0)
        self.cache_read_tokens += int(response.cache_read_tokens or 0)
        self.cost_usd += float(response.cost_usd or 0.0)

    def apply(self, response: GatewayResponse) -> GatewayResponse:
        """Publish the logical-call totals onto the response being returned."""
        response.input_tokens = self.input_tokens
        response.output_tokens = self.output_tokens
        response.cache_read_tokens = self.cache_read_tokens
        response.cost_usd = self.cost_usd
        return response


class DefaultProviderGateway:
    def __init__(
        self,
        *,
        anthropic_executor: AnthropicExecutor,
        openai_executor: OpenAIExecutor,
        cache,
        breaker,
        routing_config: RoutingConfig | None = None,
        provider_availability: dict[str, bool] | None = None,
        max_retries: int = MAX_RETRIES,
        retry_delays: list[float] | tuple[float, ...] = RETRY_DELAYS,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        telemetry: Any | None = None,
    ):
        self._anthropic_executor = anthropic_executor
        self._openai_executor = openai_executor
        self._cache = cache
        self._breaker = breaker
        self._routing_config = routing_config or runtime_routing_config()
        self._provider_availability = provider_availability
        self._max_retries = max(1, int(max_retries or 1))
        self._retry_delays = list(retry_delays)
        self._sleep = sleep
        # Telemetry session override. Left as None in production, where the
        # session is discovered from the ambient run scope; injected directly by
        # tests. When there is no session, `_capturing` is False and not one
        # extra clock read, breaker read, context-variable set or statement is
        # issued anywhere in this module.
        self._telemetry = telemetry

    async def call(
        self,
        request: GatewayRequest,
        *,
        config_override: ModelConfig | None = None,
        before_attempt: BeforeAttemptHook | None = None,
    ) -> GatewayResponse:
        # The worker invariant, checked at the top of the stack. A
        # strict-required process refuses here — before routing, before the
        # cache, before any candidate is selected — so a caller that forgot to
        # open a telemetry scope gets a clear failure rather than a mysterious
        # transport error several layers down. The transport wrapper checks the
        # same invariant independently, which is what covers a path that never
        # came through this method at all.
        telemetry_posture.enforce_provider_call("provider_gateway.call")
        candidates = select_model_candidates(
            request.phase,
            config_override=config_override,
            routing_context=request.routing_context,
            routing_config=self._routing_config,
        )
        candidates = [
            candidate
            for candidate in candidates
            if candidate[0].provider in V7_SUPPORTED_PROVIDERS
        ]
        if not candidates:
            raise RuntimeError("V7 routing invariant violated: no supported provider candidate")
        config, selection = candidates[0]

        cache_key = request.cache_key or build_cache_key(request, selection)
        cache_status = "disabled"
        cache_allowed = self._routing_config.cache_enabled and request.allow_cache
        if self._routing_config.cache_enabled and not request.allow_cache:
            cache_status = "bypass"
        if cache_allowed:
            cache_status = "miss"
            lookup = self._cache.get(cache_key)
            if lookup.hit and lookup.response is not None:
                cached = lookup.response
                cached.cache_hit = True
                cached.cache_status = "hit"
                self._apply_selection_metadata(cached, selection, config)
                self._log_runtime_event(request, selection, cached)
                return cached

        response = await self._call_with_fallbacks(candidates, request, before_attempt)
        response.cache_status = cache_status
        if response.error:
            self._log_runtime_event(request, selection, response)
            return response
        if cache_allowed:
            self._cache.put(cache_key, response, ttl_seconds=self._routing_config.cache_ttl_seconds)
        self._log_runtime_event(request, selection, response)
        return response

    async def _call_with_fallbacks(
        self,
        candidates: list[tuple[ModelConfig, ProviderSelection]],
        request: GatewayRequest,
        before_attempt: BeforeAttemptHook | None,
    ) -> GatewayResponse:
        initial_config, initial_selection = candidates[0]
        attempts: list[dict[str, Any]] = []
        failed_provider = ""
        failed_model = ""
        failed_error_type = ""
        fallback_reason = ""
        last_error_type = PROVIDER_UNAVAILABLE
        last_error = "No configured provider candidates were available"
        attempt_count = 0
        # The first failure that came from a candidate actually called, as
        # opposed to one skipped as unavailable or breaker-open. Only used to
        # keep the headline truthful; see the headline selection below.
        attempted_error_type = ""
        attempted_error = ""
        # Provider spend for this logical call. Advanced only by a real
        # `_execute` result and read by every return path below, so work that
        # actually happened cannot disappear because the attempt that did it was
        # not the attempt that answered.
        usage = _UsageLedger()

        # ─── telemetry state ───
        # Nothing below this line is read by a routing, retry, fallback or
        # breaker decision. The one place telemetry can change behavior is
        # documented and deliberate: in *strict* posture a start that cannot be
        # persisted raises out of `persist_invocation_start`, and the provider
        # request is not made. Observational posture never raises.
        session = self._session()
        capturing = session is not None
        call_id = new_uuid() if capturing else ""
        telemetry_ordinal = 0
        if capturing:
            await self._persist_call_start(
                session=session,
                call_id=call_id,
                request=request,
                config=initial_config,
                selection=initial_selection,
                candidate_count=len(candidates),
            )

        for candidate_index, (config, selection) in enumerate(candidates):
            key = _candidate_key(config)
            if not self._provider_available(config.provider):
                category = PROVIDER_UNAVAILABLE
                attempts.append(_attempt_metadata(config, selection, "skipped", category, "provider_unavailable"))
                failed_provider, failed_model, failed_error_type = _first_failure(
                    failed_provider, failed_model, failed_error_type, config, category
                )
                fallback_reason = fallback_reason or category
                last_error_type = category
                last_error = _safe_error_message(category, config.provider.value, config.model)
                if capturing:
                    telemetry_ordinal += 1
                    await self._record_skipped_candidate(
                        session=session,
                        request=request,
                        config=config,
                        selection=selection,
                        key=key,
                        call_id=call_id,
                        attempt_ordinal=telemetry_ordinal,
                        candidate_index=candidate_index,
                        error_category=category,
                        initial_config=initial_config,
                    )
                continue
            if self._breaker.is_open(key):
                category = PROVIDER_UNAVAILABLE
                attempts.append(_attempt_metadata(config, selection, "skipped", category, "circuit_open"))
                failed_provider, failed_model, failed_error_type = _first_failure(
                    failed_provider, failed_model, failed_error_type, config, category
                )
                fallback_reason = fallback_reason or category
                last_error_type = category
                last_error = _safe_error_message(category, config.provider.value, config.model)
                if capturing:
                    telemetry_ordinal += 1
                    await self._record_skipped_candidate(
                        session=session,
                        request=request,
                        config=config,
                        selection=selection,
                        key=key,
                        call_id=call_id,
                        attempt_ordinal=telemetry_ordinal,
                        candidate_index=candidate_index,
                        error_category=category,
                        initial_config=initial_config,
                        breaker_open=True,
                    )
                continue

            for retry_index in range(self._max_retries):
                governance_response = await self._run_before_attempt(before_attempt, config)
                if governance_response is not None:
                    # A governance refusal is an *authorized* no-call outcome,
                    # and reconciliation can only treat it as one if it is
                    # durable. Without this the call envelope would carry no
                    # invocation at all, which is indistinguishable from work
                    # that simply went missing.
                    if capturing:
                        telemetry_ordinal += 1
                        await self._record_skipped_candidate(
                            session=session,
                            request=request,
                            config=config,
                            selection=selection,
                            key=key,
                            call_id=call_id,
                            attempt_ordinal=telemetry_ordinal,
                            candidate_index=candidate_index,
                            error_category=normalize_error_type(
                                governance_response.error_type
                            ),
                            initial_config=initial_config,
                        )
                    governance_response.attempts = attempts
                    governance_response.attempt_count = attempt_count
                    self._apply_selection_metadata(governance_response, initial_selection, initial_config)
                    # The refused call adds nothing — no provider was reached —
                    # but spend from attempts that ran before the gate closed
                    # must still reach the budget ledger.
                    return usage.apply(governance_response)

                attempt_count += 1
                if capturing:
                    telemetry_ordinal += 1
                # The invocation start is persisted *before* the provider is
                # called. In strict posture a failure here raises and the request
                # is never sent; that is the fail-closed guarantee, and it is the
                # only telemetry-caused behavior change in this module.
                probe = (
                    await self._begin_invocation(
                        session=session,
                        request=request,
                        config=config,
                        selection=selection,
                        key=key,
                        call_id=call_id,
                        attempt_ordinal=telemetry_ordinal,
                        candidate_index=candidate_index,
                        retry_index=retry_index,
                        initial_config=initial_config,
                    )
                    if capturing
                    else None
                )
                try:
                    if probe is None:
                        response = await self._execute(config, request)
                    else:
                        with probe.scope():
                            response = await self._execute(config, request)
                except BaseException as exc:  # noqa: BLE001 - re-raised unchanged
                    # A cancellation (or any escape past `_execute`) must leave a
                    # truthful terminal state rather than an unmatched start.
                    if probe is not None:
                        probe.record_escape(exc, self._breaker_snapshot(key))
                        self._publish(session, probe)
                    raise
                # The one accumulation point, reached only once a provider
                # response actually exists. Everything below either returns this
                # response or moves on to another candidate, and both outcomes
                # owe the budget ledger whatever this attempt just cost.
                usage.record(response)
                category = normalize_error_type(response.error_type)
                if response.error:
                    provider_detail = provider_error_detail_from_message(response.error)
                    response.error_type = category
                    response.error = _safe_error_message(
                        category,
                        config.provider.value,
                        config.model,
                        provider_detail=provider_detail,
                    )

                if not response.error:
                    self._breaker.reset(key)
                    if probe is not None:
                        # Submitted, never awaited: cache population and the
                        # successful provider return below must not wait on a
                        # database write.
                        probe.record_terminal(
                            EVENT_COMPLETED, "", self._breaker_snapshot(key)
                        )
                        self._publish(session, probe)
                    response.attempt_count = attempt_count
                    response.attempts = attempts + [
                        _attempt_metadata(config, selection, "success", "", "")
                    ]
                    response.fallback_used = bool(failed_provider) or candidate_index > 0
                    response.fallback_reason = fallback_reason
                    response.failed_provider = failed_provider
                    response.failed_model = failed_model
                    response.failed_error_type = failed_error_type
                    if response.fallback_used:
                        response.fallback_provider = config.provider.value
                        response.fallback_model = response.model_used or config.model
                    self._apply_selection_metadata(response, initial_selection, initial_config)
                    # Winner-only usage would erase every earlier executed
                    # attempt. The ledger already holds this response's own
                    # usage, so assigning the totals adds the earlier spend
                    # without counting the winner twice.
                    return usage.apply(response)

                retryable = category in RETRYABLE_PROVIDER_ERRORS
                response.retryable = retryable
                attempts.append(_attempt_metadata(config, selection, "failed", category, ""))
                failed_provider, failed_model, failed_error_type = _first_failure(
                    failed_provider, failed_model, failed_error_type, config, category
                )
                fallback_reason = fallback_reason or category
                last_error_type = category
                last_error = response.error
                if not attempted_error_type:
                    attempted_error_type = category
                    attempted_error = response.error
                if retryable:
                    self._breaker.record_failure(key)

                if probe is not None:
                    # Submitted before the retry / fallback / breaker decisions
                    # below, and never awaited, so none of them can be delayed or
                    # reordered by a slow or wedged telemetry sink.
                    probe.record_terminal(
                        EVENT_PROVIDER_FAILURE, category, self._breaker_snapshot(key)
                    )
                    self._publish(session, probe)

                if category not in FALLBACK_ELIGIBLE_ERRORS:
                    return self._final_error_response(
                        response,
                        initial_config,
                        initial_selection,
                        attempts,
                        attempt_count,
                        failed_provider,
                        failed_model,
                        failed_error_type,
                        fallback_reason,
                        usage,
                    )

                if category not in RETRYABLE_PROVIDER_ERRORS:
                    break

                if retry_index < self._max_retries - 1:
                    delay = self._retry_delays[min(retry_index, len(self._retry_delays) - 1)] if self._retry_delays else 0
                    if delay > 0:
                        await self._sleep(delay)

        # When the primary failure was quota_exceeded, preserve it as the headline
        # even if all subsequent fallback candidates were unavailable or also failed.
        if failed_error_type == QUOTA_EXCEEDED:
            headline_error = _safe_error_message(QUOTA_EXCEEDED, failed_provider, failed_model)
            headline_error_type = QUOTA_EXCEEDED
        elif attempted_error_type in UNUSABLE_OUTPUT_ERRORS:
            # Same rule, same reason. A provider that answered promptly with an
            # unusable body is not "unavailable", and reporting the trailing
            # skipped candidate would tell an operator to investigate a provider
            # outage that never happened.
            headline_error = attempted_error
            headline_error_type = attempted_error_type
        else:
            headline_error = last_error or "All configured provider candidates failed or were unavailable"
            headline_error_type = last_error_type

        # Built fresh rather than reusing an attempt's response, so it starts
        # with zero usage. That is exactly why the ledger has to be applied: the
        # candidates that ran were billed, and a terminal failure is the case
        # where losing their spend is most dangerous — nothing downstream ever
        # sees a successful response to account for it instead.
        return usage.apply(GatewayResponse(
            text="",
            model_used=initial_config.model,
            provider_used=initial_config.provider.value,
            selected_model=initial_config.model,
            selected_provider=initial_config.provider.value,
            selection_reason=initial_selection.reason,
            task_profile=initial_selection.task_profile,
            cache_status="disabled",
            fallback_used=False,
            fallback_reason=fallback_reason,
            failed_provider=failed_provider,
            failed_model=failed_model,
            failed_error_type=failed_error_type,
            attempt_count=attempt_count,
            attempts=attempts,
            error=headline_error,
            error_type=headline_error_type,
            retryable=headline_error_type in RETRYABLE_PROVIDER_ERRORS,
        ))

    async def _execute(self, config: ModelConfig, request: GatewayRequest) -> GatewayResponse:
        try:
            if config.provider == Provider.ANTHROPIC:
                raw = await self._anthropic_executor(
                    config.model,
                    request.system_prompt,
                    request.user_prompt,
                    config.max_tokens,
                    config.temperature,
                    config.thinking_budget,
                )
            else:
                raw = await self._openai_executor(
                    config.model,
                    request.system_prompt,
                    request.user_prompt,
                    config.max_tokens,
                    config.temperature,
                )
        except Exception as exc:
            category = normalize_exception_category(exc)
            # An executor that raised past the adapter (so no adapter-level
            # observation exists) still yields a sanitized, message-free failure
            # identity. It is *appended*, never merged over a richer observation
            # the adapter may already have captured.
            capture = attempt_capture.current_capture()
            if capture is not None:
                _failure_class, identity = attempt_capture.observe_exception(exc)
                capture.append(
                    subject_kind="sdk_invocation",
                    subject_id=capture.invocation_id,
                    event_kind="observation",
                    error_category=category,
                    error_identity=identity,
                    failure_class=_failure_class,
                )
            return GatewayResponse(
                text="",
                model_used=config.model,
                provider_used=config.provider.value,
                error=_safe_error_message(
                    category,
                    config.provider.value,
                    config.model,
                    provider_detail=safe_provider_error_detail(exc),
                ),
                error_type=category,
            )

        category = normalize_error_type(getattr(raw, "error_type", ""))
        error = ""
        if getattr(raw, "error", ""):
            error = _safe_error_message(
                category,
                config.provider.value,
                config.model,
                provider_detail=provider_error_detail_from_message(getattr(raw, "error", "")),
            )

        text = getattr(raw, "text", "")
        stop_reason = getattr(raw, "stop_reason", "")
        output_tokens = int(getattr(raw, "output_tokens", 0) or 0)
        if not error:
            # The invariant, enforced provider-neutrally at the one point every
            # executor's result passes through. The adapters classify this too,
            # so in production this is a backstop; it is what makes the guarantee
            # hold for *any* executor rather than only the two shipped ones.
            #
            # Every invocation this gateway makes is text-required: neither
            # adapter sends tools, so there is no supported response shape whose
            # value lives somewhere other than the visible text.
            unusable = classify_unusable_output(text=text, stop_reason=stop_reason)
            if unusable:
                category = unusable
                error = _safe_error_message(
                    category,
                    config.provider.value,
                    config.model,
                    provider_detail=unusable_output_detail(
                        text=text,
                        stop_reason=stop_reason,
                        output_tokens=output_tokens,
                    ),
                )

        return GatewayResponse(
            text=text if has_usable_visible_text(text) else "",
            stop_reason=stop_reason,
            model_used=getattr(raw, "model_used", config.model) or config.model,
            provider_used=config.provider.value,
            cache_hit=False,
            cache_status="disabled",
            fallback_used=False,
            latency_ms=int(getattr(raw, "latency_ms", 0) or 0),
            input_tokens=int(getattr(raw, "input_tokens", 0) or 0),
            output_tokens=output_tokens,
            cache_read_tokens=int(getattr(raw, "cache_read_tokens", 0) or 0),
            cost_usd=float(getattr(raw, "cost_usd", 0.0) or 0.0),
            error=error,
            error_type=category if error else "",
            retryable=category in RETRYABLE_PROVIDER_ERRORS,
        )

    async def _run_before_attempt(
        self,
        before_attempt: BeforeAttemptHook | None,
        config: ModelConfig,
    ) -> GatewayResponse | None:
        if before_attempt is None:
            return None
        result = before_attempt(config)
        if inspect.isawaitable(result):
            result = await result
        if result is None:
            return None
        if isinstance(result, GatewayResponse):
            response = result
        elif isinstance(result, dict):
            category = str(result.get("category") or "policy")
            response = GatewayResponse(
                text="",
                model_used=config.model,
                provider_used=config.provider.value,
                error=str(result.get("reason") or "governance gate blocked provider execution"),
                error_type=category,
                retryable=False,
            )
        else:
            response = GatewayResponse(
                text="",
                model_used=config.model,
                provider_used=config.provider.value,
                error="governance gate blocked provider execution",
                error_type="policy",
                retryable=False,
            )
        if response.error_type not in GOVERNANCE_ERROR_TYPES:
            response.error_type = "policy"
        response.fallback_used = False
        return response

    def _final_error_response(
        self,
        response: GatewayResponse,
        initial_config: ModelConfig,
        initial_selection: ProviderSelection,
        attempts: list[dict[str, Any]],
        attempt_count: int,
        failed_provider: str,
        failed_model: str,
        failed_error_type: str,
        fallback_reason: str,
        usage: _UsageLedger,
    ) -> GatewayResponse:
        response.attempts = attempts
        response.attempt_count = attempt_count
        response.failed_provider = failed_provider
        response.failed_model = failed_model
        response.failed_error_type = failed_error_type
        response.fallback_reason = fallback_reason
        response.fallback_used = False
        self._apply_selection_metadata(response, initial_selection, initial_config)
        # A terminal failure is still billable. This response carries its own
        # attempt's usage already; the ledger adds whatever earlier executed
        # attempts cost before this one ended the call.
        return usage.apply(response)

    def _provider_available(self, provider: Provider) -> bool:
        if self._provider_availability is None:
            return True
        return bool(self._provider_availability.get(provider.value, False))

    # ═══════════════════ provider-attempt telemetry ═══════════════════
    #
    # Everything below records what happened. Nothing below is read by any
    # decision above it: the routing loop's control flow, the retry counts, the
    # fallback ordering and the circuit-breaker transitions are identical
    # whether a telemetry session is bound or not.
    #
    # There is exactly one exception, and it is deliberate: in *strict* posture
    # `persist_invocation_start` and the transport-level attempt start are
    # fail-closed, so a run that cannot record an attempt does not make it. That
    # is the trade a paired experiment requires and is documented as such —
    # strict posture is never described as behavior-neutral.

    def _session(self):
        """The telemetry session for this call, or None when telemetry is off."""
        if self._telemetry is not None:
            return self._telemetry
        return telemetry_service.current_session()

    @property
    def _capturing(self) -> bool:
        return self._session() is not None

    def _breaker_snapshot(self, key: str) -> BreakerSnapshot:
        """An *atomic* breaker reading, or an honest unknown.

        State and failure count are taken together inside one guard. A partial
        reading is never assembled: the previous design's independent
        `except: state = closed` / `except: failures = 0` fallbacks reported
        "closed with zero failures" for a snapshot that was never taken, which is
        a fabricated observation rather than a missing one.
        """
        def read() -> BreakerSnapshot:
            state = BREAKER_OPEN if self._breaker.is_open(key) else BREAKER_CLOSED
            recorded = getattr(self._breaker, "failures", None)
            if not isinstance(recorded, dict):
                # A shape this build does not know how to read is not a failure
                # to read: nothing raised, and `unknown` is the truthful answer.
                return UNKNOWN_BREAKER
            failures = len(recorded.get(key) or [])
            return BreakerSnapshot.observed(state=state, failure_count=failures)

        # Through the isolation boundary rather than a bare `except`, so a
        # breaker that *raises* is recorded as a capture failure instead of
        # silently becoming an `unknown` nobody can distinguish from a breaker
        # this build simply did not understand. The returned value is identical
        # either way, so nothing about routing changes.
        return attempt_capture.guarded(
            read, UNKNOWN_BREAKER, reason="breaker_snapshot"
        )

    def _routing_fingerprint(
        self,
        config: ModelConfig,
        selection: ProviderSelection,
        candidate_index: int,
        retry_index: int,
    ) -> str:
        return telemetry_service.routing_decision_fingerprint(
            provider=config.provider.value,
            model=config.model,
            candidate_ordinal=candidate_index + 1,
            retry_ordinal=retry_index + 1,
            selection_reason=getattr(selection, "reason", "") or "",
            task_profile=getattr(selection, "task_profile", "") or "",
        )

    def _config_fingerprint(self, config: ModelConfig) -> str:
        return telemetry_service.request_config_fingerprint(
            provider=config.provider.value,
            model=config.model,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            thinking_budget=config.thinking_budget,
            max_retries=self._max_retries,
            retry_delays=self._retry_delays,
        )

    def _identity_for(self, request: GatewayRequest):
        """Merge the ambient identity with whatever this request carries."""
        identity = current_identity()
        return identity.merged(
            phase=request.phase or identity.phase,
            project_uuid=request.project_id or None,
        ) if request.project_id or request.phase else identity

    async def _persist_call_start(
        self,
        *,
        session,
        call_id: str,
        request: GatewayRequest,
        config: ModelConfig,
        selection: ProviderSelection,
        candidate_count: int,
    ) -> None:
        record = attempt_capture.guarded(
            lambda: TelemetryCallRecord(
                call_id=call_id,
                telemetry_run_id=session.telemetry_run_id,
                posture=session.posture,
                identity=self._identity_for(request),
                worker_id=worker_id(),
                requested_provider=config.provider.value,
                requested_model=config.model,
                request_config_fingerprint=self._config_fingerprint(config),
                routing_decision_fingerprint=self._routing_fingerprint(
                    config, selection, 0, 0
                ),
                candidate_count=max(1, int(candidate_count or 1)),
                started_at=utc_now(),
            ),
            None,
            reason="call_record",
        )
        if record is not None:
            await session.persist_call_start(record)

    def _invocation_record(
        self,
        *,
        session,
        request: GatewayRequest,
        config: ModelConfig,
        selection: ProviderSelection,
        key: str,
        call_id: str,
        attempt_ordinal: int,
        candidate_index: int,
        retry_index: int,
        initial_config: ModelConfig,
        invocation_kind: str,
        breaker_before: BreakerSnapshot,
    ):
        is_fallback = candidate_index > 0
        return attempt_capture.guarded(
            lambda: SdkInvocationRecord(
                invocation_id=new_uuid(),
                call_id=call_id,
                telemetry_run_id=session.telemetry_run_id,
                posture=session.posture,
                identity=self._identity_for(request),
                worker_id=worker_id(),
                invocation_kind=invocation_kind,
                provider=config.provider.value,
                requested_model=config.model,
                # Ordinals are 1-based: "the first attempt" and "no attempt" have
                # to be distinguishable.
                candidate_ordinal=candidate_index + 1,
                retry_ordinal=retry_index + 1,
                attempt_ordinal=attempt_ordinal,
                breaker_before=breaker_before,
                fallback_candidate=is_fallback,
                fallback_from_provider=initial_config.provider.value if is_fallback else "",
                fallback_from_model=initial_config.model if is_fallback else "",
                request_config_fingerprint=self._config_fingerprint(config),
                routing_decision_fingerprint=self._routing_fingerprint(
                    config, selection, candidate_index, retry_index
                ),
                started_at=utc_now(),
            ),
            None,
            reason="invocation_record",
        )

    async def _record_skipped_candidate(
        self,
        *,
        session,
        request: GatewayRequest,
        config: ModelConfig,
        selection: ProviderSelection,
        key: str,
        call_id: str,
        attempt_ordinal: int,
        candidate_index: int,
        error_category: str,
        initial_config: ModelConfig,
        breaker_open: bool = False,
    ) -> None:
        snapshot = self._breaker_snapshot(key)
        if breaker_open and snapshot.status == "valid":
            snapshot = BreakerSnapshot.observed(
                state=BREAKER_OPEN, failure_count=snapshot.failure_count or 0
            )
        record = self._invocation_record(
            session=session,
            request=request,
            config=config,
            selection=selection,
            key=key,
            call_id=call_id,
            attempt_ordinal=attempt_ordinal,
            candidate_index=candidate_index,
            retry_index=0,
            initial_config=initial_config,
            invocation_kind=INVOCATION_SKIPPED_CANDIDATE,
            breaker_before=snapshot,
        )
        if record is None:
            return
        await session.persist_invocation_start(record)
        probe = _InvocationProbe(record, session)
        probe.capture.record_terminal(
            EVENT_SKIPPED, error_category=error_category, breaker_after=snapshot
        )
        self._publish(session, probe)

    async def _begin_invocation(
        self,
        *,
        session,
        request: GatewayRequest,
        config: ModelConfig,
        selection: ProviderSelection,
        key: str,
        call_id: str,
        attempt_ordinal: int,
        candidate_index: int,
        retry_index: int,
        initial_config: ModelConfig,
    ):
        record = self._invocation_record(
            session=session,
            request=request,
            config=config,
            selection=selection,
            key=key,
            call_id=call_id,
            attempt_ordinal=attempt_ordinal,
            candidate_index=candidate_index,
            retry_index=retry_index,
            initial_config=initial_config,
            invocation_kind=INVOCATION_PROVIDER_CALL,
            breaker_before=self._breaker_snapshot(key),
        )
        if record is None:
            return None
        await session.persist_invocation_start(record)
        return _InvocationProbe(record, session)

    @staticmethod
    def _publish(session, probe) -> None:
        """Hand every buffered event to the delivery worker. Never awaits."""
        if probe is None or session is None:
            return
        attempt_capture.guarded(
            lambda: session.submit_events(probe.drain()), 0, reason="publish"
        )

    def _apply_selection_metadata(
        self,
        response: GatewayResponse,
        selection: ProviderSelection,
        config: ModelConfig,
    ) -> None:
        response.selected_provider = selection.provider or config.provider.value
        response.selected_model = selection.model or config.model
        response.selection_reason = selection.reason
        response.task_profile = selection.task_profile
        response.provider_used = response.provider_used or config.provider.value
        response.model_used = response.model_used or config.model

    def _log_runtime_event(
        self,
        request: GatewayRequest,
        selection: ProviderSelection,
        response: GatewayResponse,
    ) -> None:
        logger.info(
            "runtime.gateway %s",
            {
                "phase": request.phase,
                "task_profile": response.task_profile or selection.task_profile,
                "selected_provider": response.selected_provider or selection.provider,
                "selected_model": response.selected_model or selection.model,
                "final_provider": response.provider_used or selection.provider,
                "final_model": response.model_used or selection.model,
                "selection_reason": response.selection_reason or selection.reason,
                "cache_enabled": bool(self._routing_config.cache_enabled),
                "cache_status": response.cache_status,
                "latency_ms": response.latency_ms,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "fallback_used": response.fallback_used,
                "fallback_reason": response.fallback_reason,
                "failed_provider": response.failed_provider,
                "failed_model": response.failed_model,
                "failed_error_type": response.failed_error_type,
                "fallback_provider": response.fallback_provider,
                "fallback_model": response.fallback_model,
                "attempt_count": response.attempt_count,
                "attempts": response.attempts,
                "ok": not bool(response.error),
                "error_type": response.error_type,
                "provider_detail": provider_error_detail_from_message(response.error),
            },
        )


class _InvocationProbe:
    """Brackets exactly one SDK invocation for telemetry.

    Holds the already-persisted start record and an append-only capture buffer.
    It owns no database connection and performs no I/O: events accumulate here
    and are handed to the delivery worker by the gateway, so nothing on the
    provider's critical path ever waits on a write.
    """

    __slots__ = ("record", "session", "capture", "_scope")

    def __init__(self, record, session) -> None:
        self.record = record
        self.session = session
        self.capture = attempt_capture.InvocationCapture(
            invocation_id=record.invocation_id,
            call_id=record.call_id,
            telemetry_run_id=record.telemetry_run_id,
            posture=record.posture,
            worker_id=record.worker_id,
            provider=record.provider,
            requested_model=record.requested_model,
            identity=record.identity,
        )
        self._scope = None

    def scope(self):
        """Bind the capture buffer for the duration of the provider call.

        The adapter and the instrumented HTTP transport both publish into it,
        which is how an SDK-internal retry gets its own attempt identity without
        any executor signature changing.
        """
        return attempt_capture.capture_scope(self.capture)

    def record_terminal(self, event_kind, error_category, breaker_after) -> None:
        self.capture.record_terminal(
            event_kind,
            error_category=error_category or "",
            breaker_after=breaker_after,
        )

    def record_escape(self, exc: BaseException, breaker_after) -> None:
        """Terminal state for an exception that escaped past the executor.

        Cancellation is classified as cancellation, never as a provider failure:
        conflating the two would make a cancelled run look like a provider
        outage in exactly the artifact meant to tell them apart.
        """
        kind = attempt_capture.terminal_kind_for_exception(exc)
        failure_class, identity = attempt_capture.observe_exception(exc)
        self.capture.record_terminal(
            kind,
            error_category="",
            error_identity=identity,
            # Kept, not discarded: a `TelemetryStartUnavailable` here is the
            # durable record that this invocation was refused *before*
            # transport, which is what lets reconciliation tell it apart from an
            # invocation whose attempt row went missing.
            failure_class=failure_class,
            breaker_after=breaker_after,
        )

    def drain(self):
        """Take the buffered events. Draining twice yields nothing the second time.

        Capture failures are materialized here, at the one production point that
        every invocation passes through on its way out. Everything the isolation
        boundary absorbed during this invocation — a provider attribute that
        raised, a validation that blew up, a fingerprint, a breaker snapshot, an
        event that would not construct — has been accumulating as a note on the
        buffer; this is where each of those becomes a durable ``capture_failure``
        event instead of a list entry that dies with the process.

        Anything that could not be represented even as a capture-failure event is
        reported to the session as an undurable outcome, which reconciliation
        refuses to certify. That is the honest end state: telemetry failed, and
        it could not write down that it failed.
        """
        unrepresented = attempt_capture.guarded(
            self.capture.flush_capture_failures, 0, reason="flush_capture_failures"
        )
        if unrepresented and self.session is not None:
            attempt_capture.guarded(
                lambda: self.session.note_unrepresented_capture_failures(unrepresented),
                None,
                reason="note_unrepresented",
            )
        events = list(self.capture.events)
        self.capture.events.clear()
        return events


def normalize_error_type(error_type: str) -> str:
    raw = (error_type or "").strip().lower()
    aliases = {
        "": UNKNOWN_PROVIDER_ERROR,
        "auth": AUTH_ERROR,
        "auth_error": AUTH_ERROR,
        "authentication": AUTH_ERROR,
        "authentication_error": AUTH_ERROR,
        "config": AUTH_ERROR,
        "configuration": AUTH_ERROR,
        "quota": QUOTA_EXCEEDED,
        "quota_exceeded": QUOTA_EXCEEDED,
        "insufficient_quota": QUOTA_EXCEEDED,
        "rate": RATE_LIMITED,
        "rate_limit": RATE_LIMITED,
        "rate_limited": RATE_LIMITED,
        "network": CONNECTION_ERROR,
        "connection": CONNECTION_ERROR,
        "connection_error": CONNECTION_ERROR,
        "timeout": TIMEOUT,
        "provider_unavailable": PROVIDER_UNAVAILABLE,
        "unavailable": PROVIDER_UNAVAILABLE,
        "server": SERVER_ERROR,
        "server_error": SERVER_ERROR,
        "invalid_request": INVALID_REQUEST,
        "bad_request": INVALID_REQUEST,
        "transport_malformed_response": TRANSPORT_MALFORMED_RESPONSE,
        "malformed": TRANSPORT_MALFORMED_RESPONSE,
        "model_schema_invalid": MODEL_SCHEMA_INVALID,
        "schema": MODEL_SCHEMA_INVALID,
        "output_token_exhausted": OUTPUT_TOKEN_EXHAUSTED,
        "empty_provider_output": EMPTY_PROVIDER_OUTPUT,
        "unknown_provider_error": UNKNOWN_PROVIDER_ERROR,
    }
    if raw in aliases:
        return aliases[raw]
    if raw in GOVERNANCE_ERROR_TYPES:
        return raw
    return UNKNOWN_PROVIDER_ERROR


_QUOTA_EXHAUSTION_MARKERS = (
    "credit balance is too low",
    "balance is too low",
    "insufficient credits",
    "purchase credits",
    "quota exceeded",
    "insufficient quota",
    "exceeded your quota",
)


def _is_quota_exhaustion_exception(exc: Exception) -> bool:
    """Return True only when the exception body/message contains unambiguous quota/credit exhaustion language."""
    parts: list[str] = [str(exc).lower()]
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        body_msg = _body_path(body, "error", "message") or _body_path(body, "message") or ""
        parts.append(str(body_msg).lower())
    exc_message = getattr(exc, "message", None)
    if exc_message:
        parts.append(str(exc_message).lower())
    combined = " ".join(parts)
    return any(marker in combined for marker in _QUOTA_EXHAUSTION_MARKERS)


def normalize_exception_category(exc: Exception) -> str:
    name = exc.__class__.__name__.lower()
    status = getattr(exc, "status_code", None)
    text = str(exc).lower()
    if "auth" in name or status in {401, 403}:
        return AUTH_ERROR
    if "rate" in name or status == 429:
        if any(marker in text for marker in ("quota", "credit", "insufficient")):
            return QUOTA_EXCEEDED
        return RATE_LIMITED
    if "timeout" in name or isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return TIMEOUT
    if "connection" in name:
        return CONNECTION_ERROR
    if status == 503:
        return PROVIDER_UNAVAILABLE
    if isinstance(status, int) and 500 <= status < 600:
        return SERVER_ERROR
    if isinstance(status, int) and 400 <= status < 500:
        if _is_quota_exhaustion_exception(exc):
            return QUOTA_EXCEEDED
        return INVALID_REQUEST
    return UNKNOWN_PROVIDER_ERROR


def _safe_error_message(
    category: str,
    provider: str,
    model: str,
    *,
    provider_detail: str = "",
) -> str:
    category = normalize_error_type(category)
    message = f"Provider call failed: category={category}, provider={provider}, model={model}"
    if provider_detail:
        message += f"{PROVIDER_DETAIL_MARKER}{provider_detail}"
    return message


def _candidate_key(config: ModelConfig) -> str:
    return f"{config.provider.value}:{config.model}"


def _first_failure(
    failed_provider: str,
    failed_model: str,
    failed_error_type: str,
    config: ModelConfig,
    category: str,
) -> tuple[str, str, str]:
    if failed_provider:
        return failed_provider, failed_model, failed_error_type
    return config.provider.value, config.model, category


def _attempt_metadata(
    config: ModelConfig,
    selection: ProviderSelection,
    status: str,
    error_type: str,
    skip_reason: str,
) -> dict[str, Any]:
    return {
        "provider": config.provider.value,
        "model": config.model,
        "task_profile": selection.task_profile,
        "selection_reason": selection.reason,
        "status": status,
        "error_type": error_type,
        "skip_reason": skip_reason,
    }


def _infer_provider(model_name: str) -> Provider | None:
    name = (model_name or "").lower()
    if "claude" in name:
        return Provider.ANTHROPIC
    if name.startswith("gpt"):
        return Provider.OPENAI
    return None
