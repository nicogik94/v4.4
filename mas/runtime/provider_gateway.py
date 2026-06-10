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


logger = logging.getLogger(__name__)

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

FALLBACK_ELIGIBLE_ERRORS = RETRYABLE_PROVIDER_ERRORS | {
    AUTH_ERROR,
    QUOTA_EXCEEDED,
}

GOVERNANCE_ERROR_TYPES = {"kill_switch", "budget", "breaker", "approval", "policy"}


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

    async def call(
        self,
        request: GatewayRequest,
        *,
        config_override: ModelConfig | None = None,
        before_attempt: BeforeAttemptHook | None = None,
    ) -> GatewayResponse:
        candidates = select_model_candidates(
            request.phase,
            config_override=config_override,
            routing_context=request.routing_context,
            routing_config=self._routing_config,
        )
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
                continue

            for retry_index in range(self._max_retries):
                governance_response = await self._run_before_attempt(before_attempt, config)
                if governance_response is not None:
                    governance_response.attempts = attempts
                    governance_response.attempt_count = attempt_count
                    self._apply_selection_metadata(governance_response, initial_selection, initial_config)
                    return governance_response

                attempt_count += 1
                response = await self._execute(config, request)
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
                    return response

                retryable = category in RETRYABLE_PROVIDER_ERRORS
                response.retryable = retryable
                attempts.append(_attempt_metadata(config, selection, "failed", category, ""))
                failed_provider, failed_model, failed_error_type = _first_failure(
                    failed_provider, failed_model, failed_error_type, config, category
                )
                fallback_reason = fallback_reason or category
                last_error_type = category
                last_error = response.error
                if retryable:
                    self._breaker.record_failure(key)

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
        else:
            headline_error = last_error or "All configured provider candidates failed or were unavailable"
            headline_error_type = last_error_type

        return GatewayResponse(
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
        )

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
        return GatewayResponse(
            text=getattr(raw, "text", ""),
            model_used=getattr(raw, "model_used", config.model) or config.model,
            provider_used=config.provider.value,
            cache_hit=False,
            cache_status="disabled",
            fallback_used=False,
            latency_ms=int(getattr(raw, "latency_ms", 0) or 0),
            input_tokens=int(getattr(raw, "input_tokens", 0) or 0),
            output_tokens=int(getattr(raw, "output_tokens", 0) or 0),
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
    ) -> GatewayResponse:
        response.attempts = attempts
        response.attempt_count = attempt_count
        response.failed_provider = failed_provider
        response.failed_model = failed_model
        response.failed_error_type = failed_error_type
        response.fallback_reason = fallback_reason
        response.fallback_used = False
        self._apply_selection_metadata(response, initial_selection, initial_config)
        return response

    def _provider_available(self, provider: Provider) -> bool:
        if self._provider_availability is None:
            return True
        return bool(self._provider_availability.get(provider.value, False))

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
