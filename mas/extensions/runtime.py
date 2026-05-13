"""Provider gateway and semantic-cache contracts for runtime controls."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class RoutingContext:
    phase: str
    task_type: str = ""
    complexity_hint: str = ""
    risk_classification: str = "minimal_risk"
    explicit_model: str = ""


@dataclass
class ProviderSelection:
    provider: str
    model: str
    reason: str = ""
    task_profile: str = ""


@dataclass
class GatewayRequest:
    phase: str
    system_prompt: str
    user_prompt: str
    routing_context: RoutingContext = field(default_factory=lambda: RoutingContext(phase="audit"))
    cache_key: str = ""
    allow_cache: bool = False


@dataclass
class GatewayResponse:
    text: str
    model_used: str = ""
    provider_used: str = ""
    selected_model: str = ""
    selected_provider: str = ""
    selection_reason: str = ""
    task_profile: str = ""
    cache_hit: bool = False
    cache_status: str = "disabled"
    fallback_used: bool = False
    fallback_reason: str = ""
    fallback_provider: str = ""
    fallback_model: str = ""
    failed_provider: str = ""
    failed_model: str = ""
    failed_error_type: str = ""
    attempt_count: int = 0
    retryable: bool = False
    attempts: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    error: str = ""
    error_type: str = ""


@dataclass
class RoutingConfig:
    default_provider: str = ""
    routing_strategy: str = "phase"
    cache_enabled: bool = False
    cache_ttl_seconds: int = 0
    phase_overrides: dict[str, str] = field(default_factory=dict)
    complexity_routes: dict[str, str] = field(default_factory=dict)
    task_profile_candidates: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class CacheLookupResult:
    hit: bool = False
    response: GatewayResponse | None = None


class SemanticCache(Protocol):
    def get(self, key: str) -> CacheLookupResult:
        ...

    def put(self, key: str, response: GatewayResponse, ttl_seconds: int = 0) -> None:
        ...


class ProviderGateway(Protocol):
    async def call(self, request: GatewayRequest, **kwargs: Any) -> GatewayResponse:
        ...
