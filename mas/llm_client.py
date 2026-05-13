"""
v4 Multi-Agent System — LLM Client
Model-agnostic with retry, fallback chain, prompt caching, circuit breaker.
Supports Anthropic Claude and OpenAI GPT.
"""
import asyncio
import json
import re
import time
import logging
from typing import Optional
from pydantic import BaseModel, Field

import anthropic
import openai

from config import (
    Provider, ModelConfig, MODEL_ROUTING, FALLBACK_CHAIN,
    ANTHROPIC_API_KEY, OPENAI_API_KEY,
    MAX_RETRIES, RETRY_DELAYS, REQUEST_TIMEOUT,
    CIRCUIT_BREAKER_THRESHOLD, CIRCUIT_BREAKER_COOLDOWN,
    RUNTIME_LAYER,
)
from extensions.runtime import GatewayRequest, RoutingContext
from runtime.cache import InMemorySemanticCache, NoOpSemanticCache
from runtime.provider_gateway import (
    DefaultProviderGateway,
    TRANSPORT_MALFORMED_RESPONSE,
    normalize_exception_category,
    normalize_error_type,
    select_model_config,
)
from scenarios.engine import run_shadow_evaluation

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """Stops hammering a provider after repeated failures."""
    def __init__(self, threshold: int = CIRCUIT_BREAKER_THRESHOLD,
                 cooldown: float = CIRCUIT_BREAKER_COOLDOWN,
                 clock=None):
        self.threshold = threshold
        self.cooldown = cooldown
        self.failures: dict[str, list[float]] = {}
        self._clock = clock or time.time

    def record_failure(self, key: str):
        now = self._clock()
        self.failures.setdefault(key, [])
        self.failures[key].append(now)
        # Keep only recent failures
        self.failures[key] = [t for t in self.failures[key] if now - t < self.cooldown]

    def is_open(self, key: str) -> bool:
        now = self._clock()
        recent = [t for t in self.failures.get(key, []) if now - t < self.cooldown]
        return len(recent) >= self.threshold

    def reset(self, key: str):
        self.failures[key] = []


class LLMResponse(BaseModel):
    text: str = ""
    ok: bool = True
    error: str = ""
    error_type: str = ""
    model_used: str = ""
    provider_used: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_hit: bool = False
    latency_ms: float = 0.0
    cost_usd: float = 0.0  # v4.3 — populated by call_llm() from per-model pricing
    selected_provider: str = ""
    selected_model: str = ""
    selection_reason: str = ""
    task_profile: str = ""
    fallback_used: bool = False
    fallback_reason: str = ""
    fallback_provider: str = ""
    fallback_model: str = ""
    failed_provider: str = ""
    failed_model: str = ""
    failed_error_type: str = ""
    attempt_count: int = 0
    attempts: list[dict] = Field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        """v4.3 — convenience property for budget tracking."""
        return self.input_tokens + self.output_tokens


# v4.3 — verified per-million-token pricing as of April 2026.
# Source: compliance/eu-ai-act-classification.md and the v2.1 strategy bundle's
# 10-cost-model.csv. These are the prices used to compute LLMResponse.cost_usd.
# When pricing changes, update here AND in v2.1 bundle's 10-cost-model.csv.
MODEL_PRICING_USD_PER_MTOK: dict[str, dict[str, float]] = {
    # Anthropic (verified April 2026)
    "claude-opus-4-6":   {"input": 5.00, "output": 25.00, "cache_read": 0.50},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cache_read": 0.30},
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00, "cache_read": 0.10},
    # OpenAI (verified April 2026 — note that the source documents this v4.3
    # was integrated from quoted GPT-5.4 input at $24.70, which was wrong by
    # ~10×. The actual price is $2.50/$15.)
    "gpt-5":      {"input": 2.50, "output": 15.00, "cache_read": 0.25},
    "gpt-5-mini": {"input": 0.25, "output": 1.00, "cache_read": 0.025},
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int,
                  cache_read_tokens: int = 0) -> float:
    """Compute USD cost for a single LLM call from verified pricing.
    Returns 0.0 if the model is not in the pricing table (with a warning)."""
    pricing = MODEL_PRICING_USD_PER_MTOK.get(model)
    if pricing is None:
        logger.warning(f"no pricing for model {model}; cost recorded as 0.0")
        return 0.0
    cost = (
        (input_tokens / 1_000_000) * pricing["input"]
        + (output_tokens / 1_000_000) * pricing["output"]
        + (cache_read_tokens / 1_000_000) * pricing.get("cache_read", 0.0)
    )
    return round(cost, 6)


# v4.3 — Multi-provider routing scaffold.
#
# The v2.1 enterprise strategy recommends multi-provider routing as the default
# production posture. v4.3 ships a routing scaffold; full LiteLLM/OpenRouter
# integration is a v5 deliverable.
#
# Why scaffold and not full integration:
#   - LiteLLM is a real dependency that adds 100MB+ to the container
#   - OpenRouter requires an additional API key and account setup
#   - The current v4.3 deployment runs against direct Anthropic + OpenAI APIs
#     and the existing config-based fallback in config.py is sufficient
#   - Doing the integration in v5 lets us pair it with the cost dashboard work
#     in dashboards/index.html (which currently doesn't show per-model breakdowns)
#
# What the scaffold gives you NOW:
#   - A single function (route_model) the orchestrator can call to pick a model
#     based on phase + complexity + risk classification
#   - A clean upgrade path: replace route_model's body with a LiteLLM call
#     without touching the orchestrator
#
# To enable LiteLLM in v5:
#   1. Add `litellm>=1.50.0` to requirements.txt
#   2. Replace route_model's body with `litellm.completion(model=..., ...)`
#   3. Set the LITELLM_PROXY_URL env var in .env.example
#   4. Update PROVIDER routing in config.py to defer to LiteLLM
#   5. Wire LiteLLM cost callbacks back into LLMResponse.cost_usd

# Global clients
_anthropic: Optional[anthropic.AsyncAnthropic] = None
_openai: Optional[openai.AsyncOpenAI] = None
_breaker = CircuitBreaker()
_semantic_cache = None


def _get_anthropic():
    global _anthropic
    if _anthropic is None:
        _anthropic = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY, timeout=REQUEST_TIMEOUT)
    return _anthropic


def _get_openai():
    global _openai
    if _openai is None:
        _openai = openai.AsyncOpenAI(api_key=OPENAI_API_KEY, timeout=REQUEST_TIMEOUT)
    return _openai


def _get_semantic_cache():
    global _semantic_cache
    if RUNTIME_LAYER.cache_enabled:
        if not isinstance(_semantic_cache, InMemorySemanticCache):
            _semantic_cache = InMemorySemanticCache()
    else:
        if not isinstance(_semantic_cache, NoOpSemanticCache):
            _semantic_cache = NoOpSemanticCache()
    return _semantic_cache


def _get_provider_gateway() -> DefaultProviderGateway:
    return DefaultProviderGateway(
        anthropic_executor=_call_anthropic,
        openai_executor=_call_openai,
        cache=_get_semantic_cache(),
        breaker=_breaker,
        provider_availability={
            Provider.ANTHROPIC.value: bool(ANTHROPIC_API_KEY),
            Provider.OPENAI.value: bool(OPENAI_API_KEY),
        },
    )


def _safe_provider_error(category: str, provider: str, model: str) -> str:
    return f"Provider call failed: category={category}, provider={provider}, model={model}"


async def _call_anthropic(
    model: str, system: str, prompt: str, max_tokens: int,
    temperature: float, thinking_budget: int = 0
) -> LLMResponse:
    """Call Claude API with prompt caching on system prompt."""
    client = _get_anthropic()
    start = time.time()
    try:
        # Anthropic requires temperature=1 when extended thinking is enabled.
        effective_thinking_budget = 0
        if thinking_budget > 0 and max_tokens > 1:
            effective_thinking_budget = min(thinking_budget, max_tokens - 1)
            if effective_thinking_budget != thinking_budget:
                logger.warning(
                    f"Clamping thinking budget for {model} from {thinking_budget} "
                    f"to {effective_thinking_budget} because max_tokens={max_tokens}"
                )
        effective_temperature = 1 if effective_thinking_budget > 0 else temperature
        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": effective_temperature,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": prompt}],
        }
        if effective_thinking_budget > 0:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": effective_thinking_budget}

        response = await client.messages.create(**kwargs)

        text_parts = []
        if not hasattr(response, "content") or response.content is None:
            return LLMResponse(
                ok=False,
                error=_safe_provider_error(TRANSPORT_MALFORMED_RESPONSE, Provider.ANTHROPIC.value, model),
                error_type=TRANSPORT_MALFORMED_RESPONSE,
                model_used=model,
                latency_ms=(time.time() - start) * 1000,
            )
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
        text = "\n".join(text_parts)

        usage = response.usage
        in_tok = getattr(usage, "input_tokens", 0)
        out_tok = getattr(usage, "output_tokens", 0)
        cache_tok = getattr(usage, "cache_read_input_tokens", 0)
        return LLMResponse(
            text=text, ok=True, model_used=model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_read_tokens=cache_tok,
            cost_usd=estimate_cost(model, in_tok, out_tok, cache_tok),
            latency_ms=(time.time() - start) * 1000,
        )
    except Exception as exc:
        category = normalize_exception_category(exc)
        return LLMResponse(
            ok=False,
            error=_safe_provider_error(category, Provider.ANTHROPIC.value, model),
            error_type=category,
            model_used=model,
            latency_ms=(time.time() - start) * 1000,
        )


async def _call_openai(
    model: str, system: str, prompt: str, max_tokens: int, temperature: float
) -> LLMResponse:
    """Call OpenAI API."""
    client = _get_openai()
    start = time.time()
    try:
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        if model.startswith("gpt-5"):
            kwargs["max_completion_tokens"] = max_tokens
            kwargs["temperature"] = 1
        else:
            kwargs["max_tokens"] = max_tokens
            kwargs["temperature"] = temperature

        response = await client.chat.completions.create(**kwargs)
        if not getattr(response, "choices", None):
            return LLMResponse(
                ok=False,
                error=_safe_provider_error(TRANSPORT_MALFORMED_RESPONSE, Provider.OPENAI.value, model),
                error_type=TRANSPORT_MALFORMED_RESPONSE,
                model_used=model,
                latency_ms=(time.time() - start) * 1000,
            )
        text = response.choices[0].message.content or ""
        usage = response.usage
        in_tok = getattr(usage, "prompt_tokens", 0)
        out_tok = getattr(usage, "completion_tokens", 0)
        return LLMResponse(
            text=text, ok=True, model_used=model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=estimate_cost(model, in_tok, out_tok),
            latency_ms=(time.time() - start) * 1000,
        )
    except Exception as exc:
        category = normalize_exception_category(exc)
        return LLMResponse(
            ok=False,
            error=_safe_provider_error(category, Provider.OPENAI.value, model),
            error_type=category,
            model_used=model,
            latency_ms=(time.time() - start) * 1000,
        )


def route_model(phase: str, complexity: str = "default",
                risk_classification: str = "minimal_risk") -> ModelConfig:
    """v4.3 complexity-aware routing scaffold.

    The current Decision Engine routes by phase only (MODEL_ROUTING dict in
    config.py). This function adds complexity and risk dimensions on top:

      - "trivial" complexity → always Haiku 4.5 / GPT-5-mini regardless of phase
      - "default" complexity → fall through to phase-based MODEL_ROUTING
      - "complex" complexity → bump up one tier (Sonnet → Opus, GPT-5-mini → GPT-5.4)
      - high_risk classification → never downgrade below Sonnet 4.6 even on
        trivial complexity, because the audit trail must be defensible

    The complexity parameter is currently set by the orchestrator based on
    project size and brief length. A future v5 upgrade could derive it from
    a learned classifier (cf. RouteLLM). The risk_classification parameter
    comes from policy.py and reflects the EU AI Act tier.

    NOTE: this is a scaffold. Full multi-provider routing via LiteLLM or
    OpenRouter is a v5 theme. The function exists now so the orchestrator
    can call it and the routing logic has one place to grow into.
    """
    routing_context = RoutingContext(
        phase=phase,
        complexity_hint=complexity,
        risk_classification=risk_classification,
    )
    config, _ = select_model_config(phase, routing_context=routing_context)
    if complexity in {"trivial", "complex"}:
        logger.debug("route_model via runtime gateway: phase=%s complexity=%s", phase, complexity)
    return config


async def call_llm(
    phase: str, system: str, prompt: str,
    config_override: Optional[ModelConfig] = None,
    *,
    project_id: str = "",
    before_attempt=None,
) -> LLMResponse:
    """
    Call an LLM with retry, fallback chain, and circuit breaker.
    Uses the model routing table unless config_override is provided.

    v4.3: optional complexity-based routing via route_model() — see below.
    Full LiteLLM/OpenRouter integration is a v5 theme; this scaffold gives
    the orchestrator the hooks it needs without requiring the multi-provider
    gateway to be deployed yet.
    """
    routing_context = RoutingContext(phase=phase)
    selected_config, selection = select_model_config(
        phase,
        config_override=config_override,
        routing_context=routing_context,
    )
    gateway_request = GatewayRequest(
        phase=phase,
        system_prompt=system,
        user_prompt=prompt,
        routing_context=routing_context,
        allow_cache=RUNTIME_LAYER.cache_enabled,
    )
    gateway = _get_provider_gateway()
    resp = await gateway.call(
        gateway_request,
        config_override=config_override,
        before_attempt=before_attempt,
    )
    result = LLMResponse(
        text=resp.text,
        ok=not bool(resp.error),
        error=resp.error,
        error_type=normalize_error_type(resp.error_type) if resp.error_type else "",
        model_used=resp.model_used,
        provider_used=resp.provider_used,
        input_tokens=resp.input_tokens,
        output_tokens=resp.output_tokens,
        cache_read_tokens=resp.cache_read_tokens,
        cache_hit=resp.cache_hit,
        latency_ms=resp.latency_ms,
        cost_usd=resp.cost_usd,
        selected_provider=resp.selected_provider,
        selected_model=resp.selected_model,
        selection_reason=resp.selection_reason,
        task_profile=resp.task_profile,
        fallback_used=resp.fallback_used,
        fallback_reason=resp.fallback_reason,
        fallback_provider=resp.fallback_provider,
        fallback_model=resp.fallback_model,
        failed_provider=resp.failed_provider,
        failed_model=resp.failed_model,
        failed_error_type=resp.failed_error_type,
        attempt_count=resp.attempt_count,
        attempts=resp.attempts,
    )
    try:
        run_shadow_evaluation(
            phase=phase,
            project_id=project_id,
            baseline_selected_provider=selection.provider,
            baseline_selected_model=selected_config.model,
            actual_provider_used=result.provider_used,
            actual_model_used=result.model_used,
            response_ok=result.ok,
            latency_ms=result.latency_ms,
            cost_usd=result.cost_usd,
        )
    except Exception as exc:
        logger.warning("scenario shadow hook skipped for %s: %s", phase, exc)
    return result


def parse_json(text):
    """Parse JSON from LLM output, tolerating markdown fences and prose.

    Tries in order:
      1. Strict parse of the trimmed text
      2. Strip markdown fences (```json ... ``` or ``` ... ```) then parse
      3. Extract first balanced {...} block and parse
      4. Extract first balanced [...] block and parse
    Returns None if all attempts fail.
    """
    if not text:
        return None
    try:
        return json.loads(text.strip())
    except (json.JSONDecodeError, ValueError):
        pass
    fence_pattern = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)
    match = fence_pattern.search(text)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            pass
    for open_char, close_char in (("{", "}"), ("[", "]")):
        start = text.find(open_char)
        if start == -1:
            continue
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == open_char:
                depth += 1
            elif ch == close_char:
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        return json.loads(candidate)
                    except (json.JSONDecodeError, ValueError):
                        break
    return None
