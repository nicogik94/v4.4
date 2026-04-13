"""Compatibility gateway around provider calls and semantic cache hooks."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any, Awaitable, Callable

from config import (
    FALLBACK_CHAIN,
    MAX_RETRIES,
    MODEL_ROUTING,
    RETRY_DELAYS,
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


def runtime_routing_config() -> RoutingConfig:
    return RoutingConfig(
        default_provider=RUNTIME_LAYER.default_provider.value,
        routing_strategy=RUNTIME_LAYER.routing_strategy,
        cache_enabled=RUNTIME_LAYER.cache_enabled,
        cache_ttl_seconds=RUNTIME_LAYER.cache_ttl_seconds,
        phase_overrides=dict(RUNTIME_LAYER.phase_model_overrides),
        complexity_routes=dict(RUNTIME_LAYER.complexity_model_overrides),
    )


def build_cache_key(request: GatewayRequest, selection: ProviderSelection) -> str:
    payload = {
        "phase": request.phase,
        "provider": selection.provider,
        "model": selection.model,
        "task_type": request.routing_context.task_type,
        "complexity_hint": request.routing_context.complexity_hint,
        "risk_classification": request.routing_context.risk_classification,
        "explicit_model": request.routing_context.explicit_model,
        "system_prompt": request.system_prompt,
        "user_prompt": request.user_prompt,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def select_model_config(
    phase: str,
    *,
    config_override: ModelConfig | None = None,
    routing_context: RoutingContext | None = None,
    routing_config: RoutingConfig | None = None,
) -> tuple[ModelConfig, ProviderSelection]:
    if config_override is not None:
        return config_override, ProviderSelection(
            provider=config_override.provider.value,
            model=config_override.model,
            reason="config_override",
        )

    routing_context = routing_context or RoutingContext(phase=phase)
    routing_config = routing_config or runtime_routing_config()
    base = MODEL_ROUTING.get(phase, MODEL_ROUTING["audit"])

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

    if not selected_model:
        return base, ProviderSelection(
            provider=base.provider.value,
            model=base.model,
            reason=reason,
        )

    provider = _infer_provider(selected_model) or base.provider or Provider(routing_config.default_provider)
    selected = ModelConfig(
        provider=provider,
        model=selected_model,
        max_tokens=base.max_tokens,
        temperature=base.temperature,
        thinking_budget=base.thinking_budget if provider == Provider.ANTHROPIC else 0,
    )
    return selected, ProviderSelection(provider=provider.value, model=selected_model, reason=reason)


class DefaultProviderGateway:
    def __init__(
        self,
        *,
        anthropic_executor: AnthropicExecutor,
        openai_executor: OpenAIExecutor,
        cache,
        breaker,
        routing_config: RoutingConfig | None = None,
    ):
        self._anthropic_executor = anthropic_executor
        self._openai_executor = openai_executor
        self._cache = cache
        self._breaker = breaker
        self._routing_config = routing_config or runtime_routing_config()

    async def call(
        self,
        request: GatewayRequest,
        *,
        config_override: ModelConfig | None = None,
    ) -> GatewayResponse:
        config, selection = select_model_config(
            request.phase,
            config_override=config_override,
            routing_context=request.routing_context,
            routing_config=self._routing_config,
        )

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
                self._log_runtime_event(request, selection, cached)
                return cached

        response = await self._call_with_fallbacks(config, request)
        response.cache_status = cache_status
        if response.error:
            self._log_runtime_event(request, selection, response)
            return response
        if cache_allowed:
            self._cache.put(cache_key, response, ttl_seconds=self._routing_config.cache_ttl_seconds)
        self._log_runtime_event(request, selection, response)
        return response

    async def _call_with_fallbacks(self, config: ModelConfig, request: GatewayRequest) -> GatewayResponse:
        fallback_used = False
        for attempt in range(MAX_RETRIES):
            if self._breaker.is_open(config.provider.value):
                logger.warning("Circuit breaker OPEN for %s, skipping to fallback", config.provider.value)
                break

            response = await self._execute(config, request)
            if not response.error:
                self._breaker.reset(config.provider.value)
                response.fallback_used = fallback_used
                return response

            if response.error_type == "auth":
                return response

            self._breaker.record_failure(config.provider.value)
            logger.warning(
                "Attempt %s/%s failed for %s: %s",
                attempt + 1,
                MAX_RETRIES,
                config.model,
                response.error,
            )
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])

        for fallback_model in FALLBACK_CHAIN.get(config.provider, []):
            logger.info("Trying fallback: %s", fallback_model)
            fallback_used = True
            fallback_config = ModelConfig(
                provider=config.provider,
                model=fallback_model,
                max_tokens=config.max_tokens,
                temperature=config.temperature,
                thinking_budget=config.thinking_budget if config.provider == Provider.ANTHROPIC else 0,
            )
            response = await self._execute(fallback_config, request)
            if not response.error:
                response.fallback_used = fallback_used
                return response

        alt_provider = Provider.OPENAI if config.provider == Provider.ANTHROPIC else Provider.ANTHROPIC
        if not self._breaker.is_open(alt_provider.value):
            alt_models = FALLBACK_CHAIN.get(alt_provider, [])
            if alt_models:
                logger.info("Cross-provider fallback to %s: %s", alt_provider.value, alt_models[0])
                fallback_used = True
                alt_config = ModelConfig(
                    provider=alt_provider,
                    model=alt_models[0],
                    max_tokens=config.max_tokens,
                    temperature=config.temperature,
                    thinking_budget=config.thinking_budget if alt_provider == Provider.ANTHROPIC else 0,
                )
                response = await self._execute(alt_config, request)
                if not response.error:
                    response.fallback_used = fallback_used
                    return response

        return GatewayResponse(
            text="",
            model_used=config.model,
            provider_used=config.provider.value,
            cache_status="disabled",
            fallback_used=fallback_used,
            error="All providers exhausted",
            error_type="exhausted",
        )

    async def _execute(self, config: ModelConfig, request: GatewayRequest) -> GatewayResponse:
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
        return GatewayResponse(
            text=getattr(raw, "text", ""),
            model_used=getattr(raw, "model_used", config.model),
            provider_used=config.provider.value,
            cache_hit=False,
            cache_status="disabled",
            fallback_used=False,
            latency_ms=int(getattr(raw, "latency_ms", 0) or 0),
            input_tokens=int(getattr(raw, "input_tokens", 0) or 0),
            output_tokens=int(getattr(raw, "output_tokens", 0) or 0),
            cache_read_tokens=int(getattr(raw, "cache_read_tokens", 0) or 0),
            cost_usd=float(getattr(raw, "cost_usd", 0.0) or 0.0),
            error=getattr(raw, "error", ""),
            error_type=getattr(raw, "error_type", ""),
        )

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
                "selected_provider": selection.provider,
                "selected_model": selection.model,
                "final_provider": response.provider_used or selection.provider,
                "final_model": response.model_used or selection.model,
                "selection_reason": selection.reason,
                "cache_enabled": bool(self._routing_config.cache_enabled),
                "cache_status": response.cache_status,
                "latency_ms": response.latency_ms,
                "fallback_used": response.fallback_used,
                "ok": not bool(response.error),
                "error_type": response.error_type,
            },
        )


def _infer_provider(model_name: str) -> Provider | None:
    name = (model_name or "").lower()
    if "claude" in name:
        return Provider.ANTHROPIC
    if name.startswith("gpt"):
        return Provider.OPENAI
    return None
