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
from dataclasses import dataclass
from typing import Optional
from pydantic import BaseModel, Field, field_validator

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
from provider_telemetry import capture as attempt_capture
from provider_telemetry.models import EVENT_OBSERVATION, SUBJECT_SDK_INVOCATION
from provider_telemetry import identity as telemetry_identity
from provider_telemetry import posture as telemetry_posture
from provider_telemetry import service as telemetry_service
from provider_telemetry import transport as telemetry_transport
from runtime.cache import InMemorySemanticCache, NoOpSemanticCache
from runtime.provider_gateway import (
    DefaultProviderGateway,
    TRANSPORT_MALFORMED_RESPONSE,
    normalize_exception_category,
    normalize_error_type,
    safe_provider_error_detail,
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
    stop_reason: str = ""
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

    @field_validator("stop_reason", mode="before")
    @classmethod
    def _normalize_stop_reason_field(cls, value):
        return normalize_stop_reason(value)

    @property
    def total_tokens(self) -> int:
        """v4.3 — convenience property for budget tracking."""
        return self.input_tokens + self.output_tokens


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


def normalize_stop_reason(value) -> str:
    """Return a bounded cross-provider stop reason without raw metadata."""
    if value is None:
        return ""
    return _NORMALIZED_STOP_REASONS.get(str(value).strip().lower(), "other")


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


def _instrument_provider_client(sdk_client, provider: str):
    """Observe the HTTP boundary of an SDK client the SDK configured itself.

    Telemetry never supplies ``http_client=``. It used to, and that was the
    defect: the SDK's own default client is not a bare ``httpx.AsyncClient`` but
    a ``DefaultAsyncHttpxClient`` carrying the SDK's connection limits (1000 /
    100 rather than httpx's 100 / 20), ``follow_redirects=True`` rather than
    httpx's ``False``, TCP keepalive socket options and — for Anthropic — an
    explicitly constructed proxy mount table. Handing the SDK any client of our
    own silently replaced all of it, so turning on a flag documented as
    observational changed the runtime's effective network configuration.

    The SDK is therefore constructed exactly as a telemetry-off build constructs
    it, and only then are that client's transports wrapped in place. Same client,
    same limits, same redirect policy, same proxies, same TLS, same pool, same
    lifecycle — with instrumentation on the transports it already had.

    The SDK's own ``max_retries`` is likewise left at its default. Setting it to
    zero would make one telemetry row equal one HTTP request by making the
    runtime less resilient; instead the transport observes each of the SDK's
    internal retries and gives each its own attempt identity.
    """
    if telemetry_service.configured_posture() == telemetry_service.POSTURE_OFF:
        return sdk_client
    if telemetry_posture.strict_required():
        # A strict-required worker cannot fall back to an uninstrumented client:
        # that client would carry no transport guard, and the process-wide "no
        # unscoped provider request" invariant would have a hole in it exactly
        # where instrumentation failed. Raising here fails the process at client
        # construction, which is before any request exists.
        return telemetry_transport.instrument_sdk_client(sdk_client, provider=provider)
    # Observational never changes what the runtime does. If this SDK build
    # cannot be instrumented, the failure is recorded and the SDK keeps the
    # client it built — which is the telemetry-off client, unmodified. The one
    # thing that must not happen here is substituting a different client.
    attempt_capture.guarded(
        lambda: telemetry_transport.instrument_sdk_client(sdk_client, provider=provider),
        None,
        reason="http_client",
    )
    return sdk_client


def _get_anthropic():
    global _anthropic
    if _anthropic is None:
        _anthropic = _instrument_provider_client(
            anthropic.AsyncAnthropic(
                api_key=ANTHROPIC_API_KEY, timeout=REQUEST_TIMEOUT
            ),
            Provider.ANTHROPIC.value,
        )
    return _anthropic


def _get_openai():
    global _openai
    if _openai is None:
        _openai = _instrument_provider_client(
            openai.AsyncOpenAI(api_key=OPENAI_API_KEY, timeout=REQUEST_TIMEOUT),
            Provider.OPENAI.value,
        )
    return _openai


def reset_provider_clients() -> None:
    """Drop the cached SDK clients so a posture change takes effect.

    Test support and operational support: the instrumented transport is chosen
    once per process at client construction, so a process that changes posture
    has to rebuild its clients.
    """
    global _anthropic, _openai
    _anthropic = None
    _openai = None


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


def _safe_provider_error(
    category: str,
    provider: str,
    model: str,
    provider_detail: str = "",
) -> str:
    message = f"Provider call failed: category={category}, provider={provider}, model={model}"
    if provider_detail:
        message += f"; provider_detail={provider_detail}"
    return message



# ═══════════ adapter-level telemetry (isolated from the provider result) ═══════
#
# Every helper below is a no-op when nobody is capturing, and none of them can
# raise into the adapter: the extraction, validation, redaction and fingerprint
# work all runs inside provider_telemetry.capture's isolation boundary. A
# response object whose `id` is a property that raises costs one field, not a
# provider failure.


def _record_observation(observe, response) -> None:
    """Read the provider's own metadata FIRST, while the raw object still exists.

    `response.id`, `response.model` and the stop reason live only on this object;
    a few lines later the adapter has reduced it to text plus counters and they
    are gone forever. Capturing here — before any transformation and before the
    malformed-response guard — is what lets a response that is unusable to the
    engine still yield a durable provider identity.
    """
    capture = attempt_capture.current_capture()
    if capture is None:
        return
    observation = attempt_capture.guarded(
        lambda: observe(response), None, reason="observe"
    )
    if observation is not None:
        capture.record_observation(observation)


def _record_malformed(category: str) -> None:
    """Append a classification beside whatever was already observed.

    Appended, never merged over: the id and usage captured a moment ago stay
    exactly as they were.
    """
    capture = attempt_capture.current_capture()
    if capture is None:
        return
    capture.append(
        subject_kind=SUBJECT_SDK_INVOCATION,
        subject_id=capture.invocation_id,
        event_kind=EVENT_OBSERVATION,
        error_category=category,
    )


def _record_adapter_exception(exc: BaseException, category: str) -> None:
    """Record an exception raised anywhere inside the adapter.

    If rich metadata was already captured, this is a *transformation* failure —
    the provider answered and the adapter could not use the answer — and it is
    appended beside that metadata rather than replacing it. If nothing was
    captured, the call never got far enough to observe anything and this is a
    plain provider-failure observation.
    """
    capture = attempt_capture.current_capture()
    if capture is None:
        return
    if capture.events:
        capture.record_transformation_failure(exc, error_category=category)
        return
    failure_class, identity = attempt_capture.observe_exception(exc)
    capture.append(
        subject_kind=SUBJECT_SDK_INVOCATION,
        subject_id=capture.invocation_id,
        event_kind=EVENT_OBSERVATION,
        error_category=category,
        error_identity=identity,
        failure_class=failure_class,
    )


async def _call_anthropic(
    model: str, system: str, prompt: str, max_tokens: int,
    temperature: float, thinking_budget: int = 0
) -> LLMResponse:
    """Call Claude API with prompt caching on system prompt."""
    client = _get_anthropic()
    start = time.time()
    # `start` is untouched: latency_ms keeps its exact previous derivation. The
    # authoritative request/response instants now come from the instrumented
    # transport, one pair per actual HTTP attempt, so they survive an SDK retry
    # instead of being averaged into one bracket. When nobody is capturing,
    # `capturing` is False and this function does no extra work at all.
    capturing = attempt_capture.is_capturing()
    try:
        # Anthropic requires temperature=1 when extended thinking is enabled.
        # Preserve the historical adapter behavior for direct/unreserved
        # callers. Strategy's routed ModelConfig now prevents this clamp by
        # reserving 4,000 response tokens before the adapter is reached.
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

        if capturing:
            _record_observation(attempt_capture.observe_anthropic_response, response)

        text_parts = []
        if not hasattr(response, "content") or response.content is None:
            if capturing:
                _record_malformed(TRANSPORT_MALFORMED_RESPONSE)
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
            stop_reason=normalize_stop_reason(getattr(response, "stop_reason", None)),
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_read_tokens=cache_tok,
            cost_usd=estimate_cost(model, in_tok, out_tok, cache_tok),
            latency_ms=(time.time() - start) * 1000,
        )
    except Exception as exc:
        category = normalize_exception_category(exc)
        if capturing:
            _record_adapter_exception(exc, category)
        return LLMResponse(
            ok=False,
            error=_safe_provider_error(
                category,
                Provider.ANTHROPIC.value,
                model,
                safe_provider_error_detail(exc),
            ),
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
    # See _call_anthropic: `start` untouched; HTTP-attempt instants come from the
    # instrumented transport.
    capturing = attempt_capture.is_capturing()
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

        if capturing:
            _record_observation(attempt_capture.observe_openai_response, response)

        if not getattr(response, "choices", None):
            if capturing:
                _record_malformed(TRANSPORT_MALFORMED_RESPONSE)
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
            stop_reason=normalize_stop_reason(
                getattr(response.choices[0], "finish_reason", None)
            ),
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=estimate_cost(model, in_tok, out_tok),
            latency_ms=(time.time() - start) * 1000,
        )
    except Exception as exc:
        category = normalize_exception_category(exc)
        if capturing:
            _record_adapter_exception(exc, category)
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
    # The worker invariant, at the other supported provider entry point. Checked
    # before routing so a strict-required worker with no telemetry scope fails
    # here rather than several layers into a request it is not allowed to make.
    telemetry_posture.enforce_provider_call("llm_client.call_llm")
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
        # Telemetry attribution only. Not part of the cache key and never read by
        # routing (see build_cache_key / select_model_candidates).
        project_id=project_id,
    )
    gateway = _get_provider_gateway()
    # Bind the identity this call is made under. `bind_identity` only overlays
    # the fields supplied here, so an outer scope's entry point, run and job
    # identity — bound by the API workflow runner, the CLI, the evaluation
    # harness or a tool — survives and is recorded alongside the phase. The
    # signature is unchanged: callers and their test doubles pin it.
    with telemetry_identity.bind_identity(
        project_id=project_id or None,
        phase=phase or None,
    ):
        resp = await gateway.call(
            gateway_request,
            config_override=config_override,
            before_attempt=before_attempt,
        )
    result = LLMResponse(
        text=resp.text,
        stop_reason=resp.stop_reason,
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


@dataclass(frozen=True)
class JSONRoot:
    """The first JSON root present in an LLM response."""

    fragment: str
    complete: bool
    value: object | None


def first_json_root(text: str) -> JSONRoot | None:
    """Locate exactly one first JSON root without promoting nested values.

    Markdown fences are inert wrapper text: the earliest object/array opener
    still starts the root. Once that opener is selected, malformed or truncated
    content fails in place; later or nested JSON values are never scanned as
    replacement roots.
    """
    if not text:
        return None

    stripped = text.strip()
    try:
        return JSONRoot(stripped, True, json.loads(stripped))
    except (json.JSONDecodeError, ValueError):
        pass

    source = text

    start = _first_structural_json_opener(source)
    if start < 0:
        return None

    stack: list[str] = []
    in_string = False
    escape = False
    pairs = {"}": "{", "]": "["}
    for index in range(start, len(source)):
        char = source[index]
        if escape:
            escape = False
            continue
        if char == "\\" and in_string:
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char in "{[":
            stack.append(char)
            continue
        if char in "}]":
            if not stack or stack[-1] != pairs[char]:
                return JSONRoot(source[start:index + 1], False, None)
            stack.pop()
            if not stack:
                fragment = source[start:index + 1]
                try:
                    value = json.loads(fragment)
                except (json.JSONDecodeError, ValueError):
                    value = None
                return JSONRoot(fragment, True, value)

    return JSONRoot(source[start:], False, None)


_JSON_PROSE_INTRO_RE = re.compile(
    r"(?:json|result|output|response|payload|data|object|array)"
    r"(?:\s+(?:is|follows|below))?\s*:\s*$",
    re.I,
)


def _first_structural_json_opener(source: str) -> int:
    """Select one earliest structural JSON candidate and never abandon it.

    A root begins at the first non-whitespace character, inside a markdown
    fence, after an explicit JSON/result introduction, or at an opener whose
    following token is lexically valid JSON. This keeps ordinary prose such as
    ``[JSON follows]`` or ``use {braces}`` inert while ensuring that malformed
    content *inside an already selected root* cannot promote a later/nested
    value.
    """
    first_content = len(source) - len(source.lstrip())
    search_start = 0
    while search_start < len(source):
        object_start = source.find("{", search_start)
        array_start = source.find("[", search_start)
        starts = [index for index in (object_start, array_start) if index >= 0]
        if not starts:
            return -1
        candidate = min(starts)
        if _is_structural_json_opener(source, candidate, first_content):
            return candidate
        search_start = candidate + 1
    return -1


def _is_structural_json_opener(source: str, index: int, first_content: int) -> bool:
    if index == first_content:
        return True

    prefix = source[:index]
    if prefix.count("```") % 2 == 1:
        fence_tail = prefix.rsplit("```", 1)[-1].strip().lower()
        if fence_tail in {"", "json"}:
            return True

    next_index = index + 1
    while next_index < len(source) and source[next_index].isspace():
        next_index += 1
    next_char = source[next_index] if next_index < len(source) else ""
    opener = source[index]
    lexically_plausible = (
        next_char in {'"', "}"}
        if opener == "{"
        else next_char in {'"', "{", "[", "]", "-", "t", "f", "n"}
        or next_char.isdigit()
    )
    if lexically_plausible:
        return True

    # A malformed response introduced as JSON/result data has still begun its
    # first root. Selecting it here is what prevents a nested valid object from
    # becoming a replacement root. Arbitrary prose punctuation is not selected.
    return bool(_JSON_PROSE_INTRO_RE.search(prefix[-160:]))


def parse_json(text, *, expected_root_type: type | None = None):
    """Parse JSON from LLM output, tolerating markdown fences and prose.

    ``expected_root_type`` is intentionally diagnostic-only. The parser always
    preserves the actual first root, including a list returned where an object
    was expected. Callers can then report the wrong shape without scanning into
    that list for a nested object. An incomplete first root returns ``None``.
    Returns None if all attempts fail.
    """
    del expected_root_type
    root = first_json_root(text)
    if root is None or not root.complete:
        return None
    return root.value
