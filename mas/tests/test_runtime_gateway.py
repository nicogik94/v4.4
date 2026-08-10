"""Tests for runtime gateway and semantic cache compatibility."""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import llm_client  # noqa: E402
from config import MODEL_ROUTING, ModelConfig, Provider  # noqa: E402
from extensions.runtime import GatewayRequest, ProviderSelection, RoutingConfig, RoutingContext  # noqa: E402
from runtime.cache import InMemorySemanticCache, NoOpSemanticCache  # noqa: E402
from runtime.provider_gateway import (  # noqa: E402
    AUTH_ERROR,
    CONNECTION_ERROR,
    DefaultProviderGateway,
    EMPTY_PROVIDER_OUTPUT,
    FALLBACK_ELIGIBLE_ERRORS,
    INVALID_REQUEST,
    OUTPUT_TOKEN_EXHAUSTED,
    PROVIDER_UNAVAILABLE,
    QUOTA_EXCEEDED,
    RATE_LIMITED,
    RETRYABLE_PROVIDER_ERRORS,
    SERVER_ERROR,
    TIMEOUT,
    TRANSPORT_MALFORMED_RESPONSE,
    UNUSABLE_OUTPUT_ERRORS,
    build_cache_key,
    classify_unusable_output,
    normalize_exception_category,
    safe_provider_error_detail,
    select_model_candidates,
    select_model_config,
    task_profile_for_phase,
)


class _BreakerStub:
    def __init__(self):
        self.open = set()
        self.reset_calls = []
        self.failure_calls = []

    def is_open(self, provider: str) -> bool:
        return provider in self.open

    def reset(self, provider: str) -> None:
        self.reset_calls.append(provider)

    def record_failure(self, provider: str) -> None:
        self.failure_calls.append(provider)


class _FakeAnthropicBadRequest(Exception):
    status_code = 400
    request_id = "req_test_123"
    type = "invalid_request_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message
        self.body = {
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "message": message,
            },
        }


class _FakeAnthropicLowCreditError(Exception):
    """Simulates Anthropic HTTP 400 when the account credit balance is exhausted."""
    status_code = 400
    request_id = "req_quota_456"
    type = "invalid_request_error"

    def __init__(self):
        msg = (
            "Your credit balance is too low to access the Anthropic API. "
            "Please go to Plans & Billing to upgrade or purchase credits."
        )
        super().__init__(msg)
        self.message = msg
        self.body = {
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "message": msg,
            },
        }


class TestRuntimeRouting(unittest.TestCase):
    def test_select_model_config_preserves_phase_default(self):
        config, selection = select_model_config("classify")

        self.assertEqual(config.model, MODEL_ROUTING["classify"].model)
        self.assertEqual(selection.provider, MODEL_ROUTING["classify"].provider.value)
        self.assertEqual(selection.reason, "phase_routing")
        self.assertEqual(selection.task_profile, "fast_classification")

    def test_phase_to_task_profile_mapping(self):
        cases = {
            "classify": "fast_classification",
            "strategy": "deep_reasoning",
            "report": "report_synthesis",
            "monitor": "monitoring_ops",
            "sqi": "strict_structured_output",
        }

        for phase, expected_profile in cases.items():
            with self.subTest(phase=phase):
                _, selection = select_model_config(phase)
                self.assertEqual(selection.task_profile, expected_profile)
                self.assertEqual(task_profile_for_phase(phase), expected_profile)

    def test_unknown_phase_uses_safe_default(self):
        config, selection = select_model_config("unknown-phase")

        self.assertEqual(config.model, MODEL_ROUTING["audit"].model)
        self.assertEqual(config.provider, MODEL_ROUTING["audit"].provider)
        self.assertEqual(selection.task_profile, "deep_reasoning")

    def test_select_model_config_uses_phase_override(self):
        config, selection = select_model_config(
            "classify",
            routing_config=RoutingConfig(phase_overrides={"classify": "gpt-5-mini"}),
        )

        self.assertEqual(config.model, "gpt-5-mini")
        self.assertEqual(selection.provider, "openai")
        self.assertEqual(selection.reason, "phase_override")
        self.assertEqual(selection.task_profile, "fast_classification")

    def test_explicit_override_wins_over_task_profile_default(self):
        config, selection = select_model_config(
            "classify",
            routing_context=RoutingContext(phase="classify", explicit_model="gpt-5"),
        )

        self.assertEqual(config.model, "gpt-5")
        self.assertEqual(selection.provider, "openai")
        self.assertEqual(selection.reason, "explicit_model")

    def test_task_profile_candidates_follow_phase_default(self):
        candidates = select_model_candidates(
            "classify",
            routing_config=RoutingConfig(
                task_profile_candidates={
                    "fast_classification": ["phase_default", "openai:gpt-5-mini"]
                }
            ),
        )

        self.assertEqual(candidates[0][0].model, MODEL_ROUTING["classify"].model)
        self.assertEqual(candidates[0][1].reason, "phase_routing")
        self.assertEqual(candidates[1][0].model, "gpt-5-mini")
        self.assertEqual(candidates[1][1].reason, "task_profile:fast_classification")

    def test_config_override_wins_over_task_profile_default(self):
        override = ModelConfig(provider=Provider.OPENAI, model="gpt-5-mini")
        config, selection = select_model_config("strategy", config_override=override)

        self.assertEqual(config.model, "gpt-5-mini")
        self.assertEqual(selection.provider, "openai")
        self.assertEqual(selection.reason, "config_override")
        self.assertIsNone(config.min_response_tokens)

    def test_extended_thinking_config_must_reserve_response_text_budget(self):
        with self.assertRaisesRegex(ValueError, "response text"):
            ModelConfig(
                provider=Provider.ANTHROPIC,
                model="claude-opus-4-6",
                max_tokens=8000,
                thinking_budget=20000,
                min_response_tokens=4000,
            )

        strategy = MODEL_ROUTING["strategy"]
        self.assertEqual(strategy.max_tokens, 8000)
        self.assertGreaterEqual(strategy.min_response_tokens, 4000)
        self.assertLessEqual(
            strategy.thinking_budget + strategy.min_response_tokens,
            strategy.max_tokens,
        )

    def test_strategy_reserve_does_not_change_unrelated_phase_thinking_budgets(self):
        expected = {
            "hypotheses": 15000,
            "gauntlet": 2000,
            "audit": 5000,
            "sqi": 5000,
            "report": 10000,
            "scope": 5000,
            "scientific_inventory": 5000,
            "trl_diagnosis": 5000,
            "research_industry_alignment": 5000,
            "ip_protection_axis": 5000,
            "next_level_recommendations": 5000,
            "technical_validation_plan": 5000,
            "industrial_transfer_plan": 5000,
            "readiness_roadmap": 5000,
            "executive_summary": 5000,
        }

        for phase, thinking_budget in expected.items():
            with self.subTest(phase=phase):
                self.assertEqual(MODEL_ROUTING[phase].thinking_budget, thinking_budget)
                self.assertIsNone(MODEL_ROUTING[phase].min_response_tokens)

    def test_llm_response_stop_reason_rejects_raw_provider_metadata(self):
        response = llm_client.LLMResponse(stop_reason="raw-sensitive-provider-detail")

        self.assertEqual(response.stop_reason, "other")

    def test_build_cache_key_is_stable(self):
        request = GatewayRequest(
            phase="audit",
            system_prompt="system",
            user_prompt="prompt",
            routing_context=RoutingContext(phase="audit", complexity_hint="default"),
        )
        selection = ProviderSelection(provider="anthropic", model="claude-sonnet-4-6", reason="phase_routing")

        first = build_cache_key(request, selection)
        second = build_cache_key(request, selection)

        self.assertEqual(first, second)


class TestSemanticCache(unittest.TestCase):
    def test_noop_cache_misses_cleanly(self):
        cache = NoOpSemanticCache()
        lookup = cache.get("missing")

        self.assertFalse(lookup.hit)
        self.assertIsNone(lookup.response)

    def test_inmemory_cache_round_trip(self):
        cache = InMemorySemanticCache()
        response = SimpleNamespace(
            text="cached",
            model_used="claude-sonnet-4-6",
            provider_used="anthropic",
            cache_hit=False,
            latency_ms=1,
            input_tokens=10,
            output_tokens=5,
            cache_read_tokens=0,
            cost_usd=0.01,
            error="",
            error_type="",
        )
        cache.put("key", response, ttl_seconds=60)
        lookup = cache.get("key")

        self.assertTrue(lookup.hit)
        self.assertEqual(lookup.response.text, "cached")


class TestProviderGateway(unittest.IsolatedAsyncioTestCase):
    def test_safe_provider_error_detail_extracts_bounded_anthropic_fields(self):
        exc = _FakeAnthropicBadRequest(
            "thinking.type enabled is not accepted for this model "
            + ("x" * 900)
        )

        detail = safe_provider_error_detail(exc)

        self.assertIn("status_code=400", detail)
        self.assertIn("exception=FakeAnthropicBadRequest", detail)
        self.assertIn("error_type=invalid_request_error", detail)
        self.assertIn("request_id=req_test_123", detail)
        self.assertIn('message="thinking.type enabled is not accepted', detail)
        self.assertLess(len(detail), 800)

    def test_safe_provider_error_detail_redacts_sensitive_values(self):
        exc = _FakeAnthropicBadRequest(
            "Bad request sk-ant-SECRET Authorization: Bearer SECRET "
            "PROMPT_SENTINEL_DO_NOT_LEAK RAW_RESPONSE_SENTINEL_DO_NOT_LEAK "
            "C:\\Users\\example\\secret.txt"
        )

        detail = safe_provider_error_detail(exc)

        self.assertIn("sk-ant-[REDACTED]", detail)
        self.assertIn("Authorization: Bearer [REDACTED]", detail)
        self.assertNotIn("sk-ant-SECRET", detail)
        self.assertNotIn("Bearer SECRET", detail)
        self.assertNotIn("PROMPT_SENTINEL_DO_NOT_LEAK", detail)
        self.assertNotIn("RAW_RESPONSE_SENTINEL_DO_NOT_LEAK", detail)
        self.assertNotIn("C:\\Users\\example\\secret.txt", detail)

    async def test_invalid_request_preserves_safe_provider_detail_without_fallback(self):
        breaker = _BreakerStub()
        calls: list[str] = []

        async def fake_anthropic(model, system, prompt, max_tokens, temperature, thinking_budget):
            calls.append(model)
            detail = (
                'status_code=400 exception=BadRequestError '
                'error_type=invalid_request_error request_id=req_123 '
                'message="model is not available"'
            )
            return SimpleNamespace(
                text="",
                model_used=model,
                error=(
                    "Provider call failed: category=invalid_request, "
                    f"provider=anthropic, model={model}; provider_detail={detail}"
                ),
                error_type="invalid_request",
            )

        async def fake_openai(model, system, prompt, max_tokens, temperature):
            raise AssertionError("invalid_request should not trigger fallback")

        gateway = DefaultProviderGateway(
            anthropic_executor=fake_anthropic,
            openai_executor=fake_openai,
            cache=NoOpSemanticCache(),
            breaker=breaker,
            max_retries=3,
        )

        response = await gateway.call(
            GatewayRequest(
                phase="classify",
                system_prompt="system",
                user_prompt="prompt",
                routing_context=RoutingContext(phase="classify"),
            )
        )

        stable_prefix = (
            "Provider call failed: category=invalid_request, "
            f"provider=anthropic, model={MODEL_ROUTING['classify'].model}"
        )
        self.assertEqual(calls, [MODEL_ROUTING["classify"].model])
        self.assertTrue(response.error.startswith(stable_prefix))
        self.assertIn("; provider_detail=status_code=400", response.error)
        self.assertIn("error_type=invalid_request_error", response.error)
        self.assertIn("request_id=req_123", response.error)
        self.assertEqual(response.error_type, "invalid_request")
        self.assertFalse(response.fallback_used)
        self.assertEqual(response.attempt_count, 1)
        self.assertEqual(breaker.failure_calls, [])

    async def test_gateway_exception_provider_detail_is_redacted_in_final_error(self):
        async def fake_anthropic(model, system, prompt, max_tokens, temperature, thinking_budget):
            raise _FakeAnthropicBadRequest(
                "Bad request sk-ant-SECRET Authorization: Bearer SECRET "
                "PROMPT_SENTINEL_DO_NOT_LEAK RAW_RESPONSE_SENTINEL_DO_NOT_LEAK "
                "C:\\Users\\example\\secret.txt"
            )

        gateway = DefaultProviderGateway(
            anthropic_executor=fake_anthropic,
            openai_executor=AsyncMock(),
            cache=NoOpSemanticCache(),
            breaker=_BreakerStub(),
            max_retries=1,
        )

        response = await gateway.call(
            GatewayRequest(
                phase="classify",
                system_prompt="system PROMPT_SENTINEL_DO_NOT_LEAK",
                user_prompt="prompt sk-ant-SECRET",
                routing_context=RoutingContext(phase="classify"),
            )
        )

        self.assertTrue(response.error.startswith("Provider call failed: category=invalid_request"))
        self.assertIn("; provider_detail=status_code=400", response.error)
        self.assertIn("error_type=invalid_request_error", response.error)
        combined = str(response.__dict__) + str(response.attempts)
        for sentinel in (
            "sk-ant-SECRET",
            "Authorization: Bearer SECRET",
            "PROMPT_SENTINEL_DO_NOT_LEAK",
            "RAW_RESPONSE_SENTINEL_DO_NOT_LEAK",
            "C:\\Users\\example\\secret.txt",
        ):
            self.assertNotIn(sentinel, combined)

    async def test_gateway_uses_executor_and_returns_runtime_metadata(self):
        async def fake_anthropic(model, system, prompt, max_tokens, temperature, thinking_budget):
            return SimpleNamespace(
                text="gateway ok",
                model_used=model,
                input_tokens=12,
                output_tokens=8,
                cache_read_tokens=0,
                cost_usd=0.02,
                latency_ms=9,
                error="",
                error_type="",
            )

        async def fake_openai(model, system, prompt, max_tokens, temperature):
            raise AssertionError("openai executor should not be used")

        gateway = DefaultProviderGateway(
            anthropic_executor=fake_anthropic,
            openai_executor=fake_openai,
            cache=NoOpSemanticCache(),
            breaker=_BreakerStub(),
            max_retries=1,
        )

        response = await gateway.call(
            GatewayRequest(
                phase="classify",
                system_prompt="system",
                user_prompt="prompt",
                routing_context=RoutingContext(phase="classify"),
            )
        )

        self.assertEqual(response.text, "gateway ok")
        self.assertEqual(response.provider_used, "anthropic")
        self.assertEqual(response.model_used, MODEL_ROUTING["classify"].model)
        self.assertEqual(response.task_profile, "fast_classification")
        self.assertEqual(response.selection_reason, "phase_routing")
        self.assertFalse(response.cache_hit)

    async def test_gateway_logs_cache_hit_and_miss_metadata(self):
        async def fake_anthropic(model, system, prompt, max_tokens, temperature, thinking_budget):
            return SimpleNamespace(
                text="cached path",
                model_used=model,
                input_tokens=7,
                output_tokens=4,
                cache_read_tokens=0,
                cost_usd=0.01,
                latency_ms=3,
                error="",
                error_type="",
            )

        gateway = DefaultProviderGateway(
            anthropic_executor=fake_anthropic,
            openai_executor=AsyncMock(),
            cache=InMemorySemanticCache(),
            breaker=_BreakerStub(),
            routing_config=RoutingConfig(cache_enabled=True, cache_ttl_seconds=60),
            max_retries=1,
        )
        request = GatewayRequest(
            phase="classify",
            system_prompt="system",
            user_prompt="prompt",
            routing_context=RoutingContext(phase="classify"),
            allow_cache=True,
        )

        with patch("runtime.provider_gateway.logger.info") as info_log:
            first = await gateway.call(request)
            second = await gateway.call(request)

        runtime_events = [
            call.args[1]
            for call in info_log.call_args_list
            if call.args and call.args[0] == "runtime.gateway %s"
        ]

        self.assertEqual(first.text, "cached path")
        self.assertEqual(second.text, "cached path")
        self.assertEqual(runtime_events[0]["cache_status"], "miss")
        self.assertEqual(runtime_events[1]["cache_status"], "hit")
        self.assertEqual(runtime_events[0]["task_profile"], "fast_classification")
        self.assertFalse(runtime_events[0]["fallback_used"])
        self.assertFalse(runtime_events[1]["fallback_used"])

    async def test_gateway_logs_when_fallback_is_used(self):
        async def fake_anthropic(model, system, prompt, max_tokens, temperature, thinking_budget):
            if model == MODEL_ROUTING["classify"].model:
                return SimpleNamespace(
                    text="",
                    model_used=model,
                    input_tokens=0,
                    output_tokens=0,
                    cache_read_tokens=0,
                    cost_usd=0.0,
                    latency_ms=2,
                    error="provider failure",
                    error_type="connection_error",
                )
            return SimpleNamespace(
                text="fallback ok",
                model_used=model,
                input_tokens=8,
                output_tokens=5,
                cache_read_tokens=0,
                cost_usd=0.02,
                latency_ms=6,
                error="",
                error_type="",
            )

        gateway = DefaultProviderGateway(
            anthropic_executor=fake_anthropic,
            openai_executor=AsyncMock(),
            cache=NoOpSemanticCache(),
            breaker=_BreakerStub(),
            max_retries=1,
        )
        request = GatewayRequest(
            phase="classify",
            system_prompt="system",
            user_prompt="prompt",
            routing_context=RoutingContext(phase="classify"),
        )

        with patch("runtime.provider_gateway.logger.info") as info_log:
            response = await gateway.call(request)

        runtime_events = [
            call.args[1]
            for call in info_log.call_args_list
            if call.args and call.args[0] == "runtime.gateway %s"
        ]

        self.assertEqual(response.text, "fallback ok")
        self.assertTrue(response.fallback_used)
        self.assertEqual(response.fallback_reason, "connection_error")
        self.assertEqual(response.failed_provider, "anthropic")
        self.assertEqual(response.failed_model, MODEL_ROUTING["classify"].model)
        self.assertEqual(response.fallback_provider, "anthropic")
        self.assertEqual(response.fallback_model, "claude-sonnet-4-6")
        self.assertTrue(runtime_events[-1]["fallback_used"])
        self.assertEqual(runtime_events[-1]["fallback_reason"], "connection_error")
        self.assertEqual(runtime_events[-1]["failed_model"], MODEL_ROUTING["classify"].model)
        self.assertEqual(runtime_events[-1]["fallback_model"], "claude-sonnet-4-6")
        self.assertEqual(runtime_events[-1]["cache_status"], "disabled")

    async def test_unavailable_first_candidate_falls_back_to_second(self):
        async def fake_anthropic(model, system, prompt, max_tokens, temperature, thinking_budget):
            raise AssertionError("anthropic should be skipped when unavailable")

        async def fake_openai(model, system, prompt, max_tokens, temperature):
            return SimpleNamespace(text="openai ok", model_used=model, error="", error_type="")

        gateway = DefaultProviderGateway(
            anthropic_executor=fake_anthropic,
            openai_executor=fake_openai,
            cache=NoOpSemanticCache(),
            breaker=_BreakerStub(),
            routing_config=RoutingConfig(
                task_profile_candidates={"fast_classification": ["phase_default", "openai:gpt-5-mini"]}
            ),
            provider_availability={"anthropic": False, "openai": True},
            max_retries=1,
        )

        response = await gateway.call(
            GatewayRequest(
                phase="classify",
                system_prompt="system",
                user_prompt="prompt",
                routing_context=RoutingContext(phase="classify"),
            )
        )

        self.assertEqual(response.text, "openai ok")
        self.assertTrue(response.fallback_used)
        self.assertEqual(response.fallback_reason, "provider_unavailable")
        self.assertEqual(response.failed_provider, "anthropic")
        self.assertEqual(response.fallback_provider, "openai")
        self.assertEqual(response.fallback_model, "gpt-5-mini")

    async def test_unhealthy_candidate_is_skipped(self):
        breaker = _BreakerStub()
        breaker.open.add(f"anthropic:{MODEL_ROUTING['classify'].model}")

        async def fake_anthropic(model, system, prompt, max_tokens, temperature, thinking_budget):
            return SimpleNamespace(text="healthy fallback", model_used=model, error="", error_type="")

        gateway = DefaultProviderGateway(
            anthropic_executor=fake_anthropic,
            openai_executor=AsyncMock(),
            cache=NoOpSemanticCache(),
            breaker=breaker,
            max_retries=1,
        )

        response = await gateway.call(
            GatewayRequest(
                phase="classify",
                system_prompt="system",
                user_prompt="prompt",
                routing_context=RoutingContext(phase="classify"),
            )
        )

        self.assertEqual(response.text, "healthy fallback")
        self.assertTrue(response.fallback_used)
        self.assertEqual(response.attempts[0]["status"], "skipped")
        self.assertEqual(response.attempts[0]["skip_reason"], "circuit_open")

    async def test_all_providers_failing_returns_safe_failure(self):
        async def fake_anthropic(model, system, prompt, max_tokens, temperature, thinking_budget):
            return SimpleNamespace(
                text="",
                model_used=model,
                error="RAW_RESPONSE_SENTINEL sk-test-secret C:\\Users\\example\\secret.txt",
                error_type="rate_limited",
            )

        async def fake_openai(model, system, prompt, max_tokens, temperature):
            return SimpleNamespace(
                text="",
                model_used=model,
                error="OPENAI_API_KEY RAW_PROMPT_SENTINEL",
                error_type="server_error",
            )

        gateway = DefaultProviderGateway(
            anthropic_executor=fake_anthropic,
            openai_executor=fake_openai,
            cache=NoOpSemanticCache(),
            breaker=_BreakerStub(),
            routing_config=RoutingConfig(
                task_profile_candidates={"fast_classification": ["phase_default", "openai:gpt-5-mini"]}
            ),
            provider_availability={"anthropic": True, "openai": True},
            max_retries=1,
        )

        response = await gateway.call(
            GatewayRequest(
                phase="classify",
                system_prompt="system RAW_PROMPT_SENTINEL",
                user_prompt="prompt sk-test-secret",
                routing_context=RoutingContext(phase="classify"),
            )
        )

        self.assertFalse(response.text)
        self.assertTrue(response.error)
        combined = str(response.__dict__) + str(response.attempts)
        for sentinel in (
            "RAW_RESPONSE_SENTINEL",
            "RAW_PROMPT_SENTINEL",
            "sk-test-secret",
            "OPENAI_API_KEY",
            "C:\\Users\\example\\secret.txt",
        ):
            self.assertNotIn(sentinel, combined)

    async def test_successful_invalid_json_text_does_not_trigger_gateway_fallback(self):
        calls: list[str] = []

        async def fake_anthropic(model, system, prompt, max_tokens, temperature, thinking_budget):
            calls.append(model)
            return SimpleNamespace(text="not json", model_used=model, error="", error_type="")

        gateway = DefaultProviderGateway(
            anthropic_executor=fake_anthropic,
            openai_executor=AsyncMock(),
            cache=NoOpSemanticCache(),
            breaker=_BreakerStub(),
            max_retries=1,
        )

        response = await gateway.call(
            GatewayRequest(
                phase="audit",
                system_prompt="system",
                user_prompt="prompt",
                routing_context=RoutingContext(phase="audit"),
            )
        )

        self.assertEqual(response.text, "not json")
        self.assertFalse(response.fallback_used)
        self.assertEqual(calls, [MODEL_ROUTING["audit"].model])

    async def test_runtime_metadata_sanitizes_failed_primary_and_fallback(self):
        async def fake_anthropic(model, system, prompt, max_tokens, temperature, thinking_budget):
            return SimpleNamespace(
                text="",
                model_used=model,
                error="RAW_RESPONSE_SENTINEL sk-test-secret C:\\Users\\example\\secret.txt",
                error_type="rate_limited",
            )

        async def fake_openai(model, system, prompt, max_tokens, temperature):
            return SimpleNamespace(text="safe fallback", model_used=model, error="", error_type="")

        gateway = DefaultProviderGateway(
            anthropic_executor=fake_anthropic,
            openai_executor=fake_openai,
            cache=NoOpSemanticCache(),
            breaker=_BreakerStub(),
            routing_config=RoutingConfig(
                task_profile_candidates={"fast_classification": ["phase_default", "openai:gpt-5-mini"]}
            ),
            provider_availability={"anthropic": True, "openai": True},
            max_retries=1,
        )
        request = GatewayRequest(
            phase="classify",
            system_prompt="system RAW_PROMPT_SENTINEL ANTHROPIC_API_KEY",
            user_prompt="prompt OPENAI_API_KEY",
            routing_context=RoutingContext(phase="classify"),
        )

        with patch("runtime.provider_gateway.logger.info") as info_log:
            response = await gateway.call(request)

        runtime_events = [
            call.args[1]
            for call in info_log.call_args_list
            if call.args and call.args[0] == "runtime.gateway %s"
        ]

        self.assertEqual(response.text, "safe fallback")
        self.assertTrue(response.fallback_used)
        self.assertEqual(response.failed_provider, "anthropic")
        self.assertEqual(response.failed_error_type, "rate_limited")
        self.assertEqual(response.fallback_provider, "openai")
        self.assertEqual(response.fallback_model, "gpt-5-mini")

        serialized = str(runtime_events[-1])
        for sentinel in (
            "RAW_RESPONSE_SENTINEL",
            "RAW_PROMPT_SENTINEL",
            "sk-test-secret",
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "C:\\Users\\example\\secret.txt",
        ):
            self.assertNotIn(sentinel, serialized)

    async def test_call_llm_still_works_through_gateway(self):
        fake_response = llm_client.LLMResponse(
            text="llm ok",
            stop_reason="max_tokens",
            ok=True,
            model_used=MODEL_ROUTING["classify"].model,
            provider_used="anthropic",
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.01,
            latency_ms=5,
        )

        with (
            patch("llm_client.ANTHROPIC_API_KEY", "test-key"),
            patch("llm_client._call_anthropic", new=AsyncMock(return_value=fake_response)),
        ):
            response = await llm_client.call_llm("classify", "system", "prompt")

        self.assertTrue(response.ok)
        self.assertEqual(response.text, "llm ok")
        self.assertEqual(response.provider_used, "anthropic")
        self.assertEqual(response.model_used, MODEL_ROUTING["classify"].model)
        self.assertEqual(response.stop_reason, "max_tokens")
        self.assertEqual(response.attempt_count, 1)
        self.assertFalse(response.fallback_used)

    async def test_anthropic_max_tokens_stop_reason_is_normalized_on_response(self):
        create_mock = AsyncMock(
            return_value=SimpleNamespace(
                content=[SimpleNamespace(type="text", text="{\"ok\":true}")],
                usage=SimpleNamespace(
                    input_tokens=11,
                    output_tokens=8000,
                    cache_read_input_tokens=0,
                ),
                stop_reason="max_tokens",
            )
        )
        fake_client = SimpleNamespace(messages=SimpleNamespace(create=create_mock))

        with patch("llm_client._get_anthropic", return_value=fake_client):
            response = await llm_client._call_anthropic(
                "claude-opus-4-6", "system", "prompt", 8000, 0.4, 4000
            )

        self.assertTrue(response.ok)
        self.assertEqual(response.text, '{"ok":true}')
        self.assertEqual(response.stop_reason, "max_tokens")
        self.assertEqual(create_mock.await_count, 1)
        self.assertEqual(create_mock.await_args.kwargs["thinking"]["budget_tokens"], 4000)

    async def test_unreserved_direct_anthropic_call_preserves_historical_clamp(self):
        create_mock = AsyncMock(
            return_value=SimpleNamespace(
                content=[SimpleNamespace(type="text", text="partial")],
                usage=SimpleNamespace(
                    input_tokens=11,
                    output_tokens=1,
                    cache_read_input_tokens=0,
                ),
                stop_reason="max_tokens",
            )
        )
        fake_client = SimpleNamespace(messages=SimpleNamespace(create=create_mock))

        with patch("llm_client._get_anthropic", return_value=fake_client):
            response = await llm_client._call_anthropic(
                "claude-opus-4-6", "system", "prompt", 8000, 0.4, 20000
            )

        self.assertTrue(response.ok)
        self.assertEqual(create_mock.await_count, 1)
        self.assertEqual(create_mock.await_args.kwargs["thinking"]["budget_tokens"], 7999)


def test_call_llm_default_shadow_store_uses_pytest_temp_sqlite(scenario_shadow_sqlite_path):
    fake_response = llm_client.LLMResponse(
        text="llm shadow ok",
        ok=True,
        model_used=MODEL_ROUTING["classify"].model,
        provider_used="anthropic",
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.01,
        latency_ms=5,
    )

    async def exercise_gateway_default_shadow_store():
        with (
            patch("llm_client.ANTHROPIC_API_KEY", "test-key"),
            patch("llm_client._call_anthropic", new=AsyncMock(return_value=fake_response)),
        ):
            return await llm_client.call_llm("classify", "system", "prompt")

    scenario_config = llm_client.run_shadow_evaluation.__globals__["SCENARIO_SHADOW"]
    assert Path(scenario_config.sqlite_path) == scenario_shadow_sqlite_path
    assert not scenario_shadow_sqlite_path.exists()

    response = asyncio.run(exercise_gateway_default_shadow_store())

    assert response.ok
    assert scenario_shadow_sqlite_path.is_file()


class TestOpenAICompatibility(unittest.IsolatedAsyncioTestCase):
    async def test_gpt5_uses_max_completion_tokens(self):
        create_mock = AsyncMock(
            return_value=SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
                usage=SimpleNamespace(prompt_tokens=11, completion_tokens=6),
            )
        )
        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock)))

        with patch("llm_client._get_openai", return_value=fake_client):
            response = await llm_client._call_openai("gpt-5-mini", "system", "prompt", 123, 0.2)

        kwargs = create_mock.await_args.kwargs
        self.assertEqual(kwargs["max_completion_tokens"], 123)
        self.assertEqual(kwargs["temperature"], 1)
        self.assertNotIn("max_tokens", kwargs)
        self.assertTrue(response.ok)

    async def test_openai_length_finish_reason_normalizes_to_max_tokens(self):
        create_mock = AsyncMock(
            return_value=SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content="partial"),
                    finish_reason="length",
                )],
                usage=SimpleNamespace(prompt_tokens=11, completion_tokens=6),
            )
        )
        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock)))

        with patch("llm_client._get_openai", return_value=fake_client):
            response = await llm_client._call_openai(
                "gpt-5-mini", "system", "prompt", 123, 0.2
            )

        self.assertTrue(response.ok)
        self.assertEqual(response.stop_reason, "max_tokens")


class TestCircuitBreaker(unittest.TestCase):
    def test_process_local_breaker_opens_and_resets_by_candidate_key(self):
        now = {"value": 0.0}
        breaker = llm_client.CircuitBreaker(threshold=2, cooldown=10, clock=lambda: now["value"])
        key = "anthropic:claude-sonnet-4-6"

        breaker.record_failure(key)
        self.assertFalse(breaker.is_open(key))
        breaker.record_failure(key)
        self.assertTrue(breaker.is_open(key))

        now["value"] = 11.0
        self.assertFalse(breaker.is_open(key))

        breaker.record_failure(key)
        breaker.record_failure(key)
        self.assertTrue(breaker.is_open(key))
        breaker.reset(key)
        self.assertFalse(breaker.is_open(key))


class TestQuotaExhaustionFallback(unittest.IsolatedAsyncioTestCase):
    """Anthropic credit-balance exhaustion (HTTP 400) must classify as quota_exceeded
    and trigger the OpenAI fallback chain, not stop as invalid_request."""

    def test_anthropic_credit_balance_low_classifies_as_quota_exceeded(self):
        exc = _FakeAnthropicLowCreditError()
        self.assertEqual(normalize_exception_category(exc), QUOTA_EXCEEDED)

    def test_generic_anthropic_400_invalid_request_does_not_classify_as_quota_exceeded(self):
        exc = _FakeAnthropicBadRequest("model does not support extended thinking")
        self.assertEqual(normalize_exception_category(exc), INVALID_REQUEST)

    async def test_judge_config_override_falls_back_to_openai_on_quota_exceeded(self):
        anthropic_calls: list[str] = []
        openai_calls: list[str] = []

        async def fake_anthropic(model, system, prompt, max_tokens, temperature, thinking_budget):
            anthropic_calls.append(model)
            raise _FakeAnthropicLowCreditError()

        async def fake_openai(model, system, prompt, max_tokens, temperature):
            openai_calls.append(model)
            return SimpleNamespace(
                text="openai judge ok",
                model_used=model,
                input_tokens=20,
                output_tokens=10,
                cache_read_tokens=0,
                cost_usd=0.01,
                latency_ms=5,
                error="",
                error_type="",
            )

        gateway = DefaultProviderGateway(
            anthropic_executor=fake_anthropic,
            openai_executor=fake_openai,
            cache=NoOpSemanticCache(),
            breaker=_BreakerStub(),
            routing_config=RoutingConfig(
                task_profile_candidates={"deep_reasoning": ["phase_default", "openai:gpt-5"]}
            ),
            provider_availability={"anthropic": True, "openai": True},
            max_retries=3,
        )

        response = await gateway.call(
            GatewayRequest(
                phase="eval_judge",
                system_prompt="You are a judge.",
                user_prompt="Score this output.",
                routing_context=RoutingContext(phase="eval_judge"),
            ),
            config_override=ModelConfig(
                provider=Provider.ANTHROPIC,
                model="claude-sonnet-4-6",
                max_tokens=1000,
                temperature=0.0,
            ),
        )

        self.assertEqual(response.text, "openai judge ok")
        self.assertFalse(response.error)
        self.assertTrue(response.fallback_used)
        self.assertEqual(response.failed_provider, "anthropic")
        self.assertEqual(response.failed_model, "claude-sonnet-4-6")
        self.assertEqual(response.failed_error_type, QUOTA_EXCEEDED)
        self.assertEqual(response.fallback_provider, "openai")
        self.assertEqual(response.fallback_model, "gpt-5")
        self.assertEqual(len(anthropic_calls), 1, "quota-exhausted Anthropic must not be retried")
        self.assertEqual(openai_calls, ["gpt-5"])

    async def test_quota_exhaustion_missing_openai_preserves_clear_error(self):
        async def fake_anthropic(model, system, prompt, max_tokens, temperature, thinking_budget):
            raise _FakeAnthropicLowCreditError()

        gateway = DefaultProviderGateway(
            anthropic_executor=fake_anthropic,
            openai_executor=AsyncMock(side_effect=AssertionError("openai should not be called")),
            cache=NoOpSemanticCache(),
            breaker=_BreakerStub(),
            routing_config=RoutingConfig(
                task_profile_candidates={"deep_reasoning": ["phase_default", "openai:gpt-5"]}
            ),
            provider_availability={"anthropic": True, "openai": False},
            max_retries=1,
        )

        response = await gateway.call(
            GatewayRequest(
                phase="eval_judge",
                system_prompt="You are a judge.",
                user_prompt="Score this output.",
                routing_context=RoutingContext(phase="eval_judge"),
            ),
            config_override=ModelConfig(
                provider=Provider.ANTHROPIC,
                model="claude-sonnet-4-6",
                max_tokens=1000,
                temperature=0.0,
            ),
        )

        self.assertTrue(response.error)
        self.assertIn("quota_exceeded", response.error)
        self.assertEqual(response.error_type, QUOTA_EXCEEDED)
        self.assertFalse(response.fallback_used)
        self.assertEqual(response.failed_provider, "anthropic")
        self.assertEqual(response.failed_error_type, QUOTA_EXCEEDED)

    async def test_quota_exhaustion_fallback_metadata_preserved(self):
        async def fake_anthropic(model, system, prompt, max_tokens, temperature, thinking_budget):
            raise _FakeAnthropicLowCreditError()

        async def fake_openai(model, system, prompt, max_tokens, temperature):
            return SimpleNamespace(
                text="fallback text",
                model_used=model,
                input_tokens=5,
                output_tokens=3,
                cache_read_tokens=0,
                cost_usd=0.005,
                latency_ms=4,
                error="",
                error_type="",
            )

        gateway = DefaultProviderGateway(
            anthropic_executor=fake_anthropic,
            openai_executor=fake_openai,
            cache=NoOpSemanticCache(),
            breaker=_BreakerStub(),
            routing_config=RoutingConfig(
                task_profile_candidates={"deep_reasoning": ["phase_default", "openai:gpt-5"]}
            ),
            provider_availability={"anthropic": True, "openai": True},
            max_retries=1,
        )

        response = await gateway.call(
            GatewayRequest(
                phase="eval_judge",
                system_prompt="system",
                user_prompt="prompt",
                routing_context=RoutingContext(phase="eval_judge"),
            ),
            config_override=ModelConfig(
                provider=Provider.ANTHROPIC,
                model="claude-sonnet-4-6",
                max_tokens=1000,
                temperature=0.0,
            ),
        )

        self.assertTrue(response.fallback_used)
        self.assertEqual(response.failed_provider, "anthropic")
        self.assertEqual(response.failed_model, "claude-sonnet-4-6")
        self.assertEqual(response.failed_error_type, QUOTA_EXCEEDED)
        self.assertEqual(response.fallback_provider, "openai")
        self.assertEqual(response.fallback_model, "gpt-5")
        self.assertFalse(response.error)

    async def test_quota_exhausted_anthropic_candidate_not_retried_repeatedly(self):
        """Quota-exhausted Anthropic must break out of the retry loop immediately
        (QUOTA_EXCEEDED is not in RETRYABLE_PROVIDER_ERRORS)."""
        anthropic_call_count = 0

        async def fake_anthropic(model, system, prompt, max_tokens, temperature, thinking_budget):
            nonlocal anthropic_call_count
            anthropic_call_count += 1
            raise _FakeAnthropicLowCreditError()

        async def fake_openai(model, system, prompt, max_tokens, temperature):
            return SimpleNamespace(text="ok", model_used=model, error="", error_type="")

        gateway = DefaultProviderGateway(
            anthropic_executor=fake_anthropic,
            openai_executor=fake_openai,
            cache=NoOpSemanticCache(),
            breaker=_BreakerStub(),
            routing_config=RoutingConfig(
                task_profile_candidates={"deep_reasoning": ["phase_default", "openai:gpt-5"]}
            ),
            provider_availability={"anthropic": True, "openai": True},
            max_retries=5,
        )

        await gateway.call(
            GatewayRequest(
                phase="eval_judge",
                system_prompt="system",
                user_prompt="prompt",
                routing_context=RoutingContext(phase="eval_judge"),
            ),
            config_override=ModelConfig(
                provider=Provider.ANTHROPIC,
                model="claude-sonnet-4-6",
                max_tokens=1000,
                temperature=0.0,
            ),
        )

        self.assertEqual(anthropic_call_count, 1, "quota-exhausted candidate must not be retried")


def _openai_response(content, finish_reason, completion_tokens=64):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=content),
            finish_reason=finish_reason,
        )],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=completion_tokens),
    )


def _anthropic_response(blocks, stop_reason, output_tokens=64):
    return SimpleNamespace(
        content=blocks,
        usage=SimpleNamespace(
            input_tokens=11,
            output_tokens=output_tokens,
            cache_read_input_tokens=0,
        ),
        stop_reason=stop_reason,
    )


class TestUnusableOpenAIOutput(unittest.IsolatedAsyncioTestCase):
    """A GPT-5 turn that spends its whole completion budget on reasoning returns
    no visible text. That must never leave the adapter as a success."""

    async def _call(self, content, finish_reason, completion_tokens=64):
        create_mock = AsyncMock(
            return_value=_openai_response(content, finish_reason, completion_tokens)
        )
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock))
        )
        with patch("llm_client._get_openai", return_value=fake_client):
            return await llm_client._call_openai("gpt-5", "system", "prompt", 8000, 0.4)

    async def test_o1_none_content_with_length_stop_is_output_exhaustion(self):
        response = await self._call(None, "length", completion_tokens=8000)

        self.assertFalse(response.ok)
        self.assertEqual(response.error_type, OUTPUT_TOKEN_EXHAUSTED)
        self.assertEqual(response.text, "")
        self.assertEqual(response.stop_reason, "max_tokens")
        self.assertIn("visible_text=none", response.error)
        self.assertIn("output_tokens=8000", response.error)
        # Billed usage survives the failure: these tokens were charged.
        self.assertEqual(response.output_tokens, 8000)

    async def test_o2_empty_content_with_length_stop_is_output_exhaustion(self):
        response = await self._call("", "length", completion_tokens=8000)

        self.assertFalse(response.ok)
        self.assertEqual(response.error_type, OUTPUT_TOKEN_EXHAUSTED)
        self.assertEqual(response.text, "")
        self.assertIn("visible_text=empty", response.error)

    async def test_o3_empty_content_with_normal_stop_is_empty_provider_output(self):
        response = await self._call("", "stop")

        self.assertFalse(response.ok)
        self.assertEqual(response.error_type, EMPTY_PROVIDER_OUTPUT)
        self.assertEqual(response.text, "")
        self.assertIn("stop_reason=end_turn", response.error)

    async def test_o3_whitespace_only_content_is_empty_provider_output(self):
        response = await self._call("   \n\t ", "stop")

        self.assertFalse(response.ok)
        self.assertEqual(response.error_type, EMPTY_PROVIDER_OUTPUT)
        self.assertIn("visible_text=whitespace", response.error)

    async def test_o4_partial_content_with_length_stop_stays_a_success(self):
        response = await self._call('{"executive_strategy": "partial', "length")

        self.assertTrue(response.ok)
        self.assertEqual(response.error, "")
        self.assertEqual(response.error_type, "")
        self.assertEqual(response.text, '{"executive_strategy": "partial')
        self.assertEqual(response.stop_reason, "max_tokens")

    async def test_o5_normal_completion_does_not_regress(self):
        response = await self._call('{"ok":true}', "stop")

        self.assertTrue(response.ok)
        self.assertEqual(response.error, "")
        self.assertEqual(response.text, '{"ok":true}')
        self.assertEqual(response.stop_reason, "end_turn")


class TestUnusableAnthropicOutput(unittest.IsolatedAsyncioTestCase):
    """The same invariant at the primary provider: a reply carrying no text
    block is not an analytical result either."""

    async def _call(self, blocks, stop_reason, output_tokens=64):
        create_mock = AsyncMock(
            return_value=_anthropic_response(blocks, stop_reason, output_tokens)
        )
        fake_client = SimpleNamespace(messages=SimpleNamespace(create=create_mock))
        with patch("llm_client._get_anthropic", return_value=fake_client):
            return await llm_client._call_anthropic(
                "claude-opus-4-6", "system", "prompt", 8000, 0.4, 4000
            )

    async def test_a1_no_usable_text_block_is_a_typed_failure(self):
        response = await self._call(
            [SimpleNamespace(type="thinking", thinking="reasoned privately")],
            "end_turn",
        )

        self.assertFalse(response.ok)
        self.assertEqual(response.error_type, EMPTY_PROVIDER_OUTPUT)
        self.assertEqual(response.text, "")
        # The thinking block's content never reaches the diagnostic.
        self.assertNotIn("reasoned privately", response.error)

    async def test_a1_no_content_blocks_at_all_is_a_typed_failure(self):
        response = await self._call([], "end_turn")

        self.assertFalse(response.ok)
        self.assertEqual(response.error_type, EMPTY_PROVIDER_OUTPUT)

    async def test_a2_empty_text_with_max_tokens_is_output_exhaustion(self):
        response = await self._call(
            [SimpleNamespace(type="text", text="")], "max_tokens", output_tokens=8000
        )

        self.assertFalse(response.ok)
        self.assertEqual(response.error_type, OUTPUT_TOKEN_EXHAUSTED)
        self.assertEqual(response.stop_reason, "max_tokens")
        self.assertIn("output_tokens=8000", response.error)
        self.assertEqual(response.output_tokens, 8000)

    async def test_a3_partial_text_with_max_tokens_stays_a_success(self):
        response = await self._call(
            [SimpleNamespace(type="text", text='{"executive_strategy": "partial')],
            "max_tokens",
        )

        self.assertTrue(response.ok)
        self.assertEqual(response.error, "")
        self.assertEqual(response.text, '{"executive_strategy": "partial')
        self.assertEqual(response.stop_reason, "max_tokens")

    async def test_a4_normal_completion_does_not_regress(self):
        response = await self._call(
            [SimpleNamespace(type="text", text='{"ok":true}')], "end_turn"
        )

        self.assertTrue(response.ok)
        self.assertEqual(response.error, "")
        self.assertEqual(response.text, '{"ok":true}')


class TestUnusableOutputGatewayContract(unittest.IsolatedAsyncioTestCase):
    """The invariant at the gateway: enforced for *any* executor, not only the
    two shipped adapters."""

    def _gateway(self, *, anthropic, openai, breaker, openai_available=True, max_retries=3):
        return DefaultProviderGateway(
            anthropic_executor=anthropic,
            openai_executor=openai,
            cache=NoOpSemanticCache(),
            breaker=breaker,
            routing_config=RoutingConfig(
                task_profile_candidates={"deep_reasoning": ["phase_default"]}
            ),
            provider_availability={"anthropic": True, "openai": openai_available},
            max_retries=max_retries,
        )

    async def _call(self, gateway):
        return await gateway.call(
            GatewayRequest(
                phase="strategy",
                system_prompt="system",
                user_prompt="prompt",
                routing_context=RoutingContext(phase="strategy"),
            )
        )

    async def test_g1_empty_output_never_appears_as_a_successful_attempt(self):
        """An executor that reports no error and no text still fails closed."""
        breaker = _BreakerStub()
        calls: list[str] = []

        async def fake_anthropic(model, system, prompt, max_tokens, temperature, thinking_budget):
            calls.append(model)
            return SimpleNamespace(
                text="",
                stop_reason="max_tokens",
                model_used=model,
                input_tokens=11,
                output_tokens=8000,
                cache_read_tokens=0,
                cost_usd=0.2,
                latency_ms=9,
                error="",
                error_type="",
            )

        gateway = self._gateway(
            anthropic=fake_anthropic,
            openai=AsyncMock(side_effect=AssertionError("openai is unavailable here")),
            breaker=breaker,
            openai_available=False,
        )

        response = await self._call(gateway)

        self.assertTrue(response.error)
        self.assertEqual(response.error_type, OUTPUT_TOKEN_EXHAUSTED)
        self.assertEqual(response.text, "")
        statuses = {attempt["status"] for attempt in response.attempts}
        self.assertNotIn("success", statuses)
        self.assertTrue(
            all(
                attempt["error_type"] == OUTPUT_TOKEN_EXHAUSTED
                for attempt in response.attempts
                if attempt["status"] == "failed"
            )
        )
        self.assertTrue(calls, "the anthropic candidates must actually be attempted")

    async def test_g2_g4_unusable_output_never_retries_the_same_candidate(self):
        """max_retries=3 must not turn one empty answer into three billed ones."""
        breaker = _BreakerStub()
        calls: list[str] = []

        async def fake_anthropic(model, system, prompt, max_tokens, temperature, thinking_budget):
            calls.append(model)
            return SimpleNamespace(
                text="", stop_reason="max_tokens", model_used=model,
                input_tokens=11, output_tokens=8000, cache_read_tokens=0,
                cost_usd=0.2, latency_ms=9, error="", error_type="",
            )

        gateway = self._gateway(
            anthropic=fake_anthropic,
            openai=AsyncMock(side_effect=AssertionError("openai is unavailable here")),
            breaker=breaker,
            openai_available=False,
            max_retries=3,
        )

        response = await self._call(gateway)

        self.assertEqual(
            len(calls), len(set(calls)),
            "no candidate may be called twice for unusable output",
        )
        self.assertEqual(response.attempt_count, len(calls))
        self.assertNotIn(OUTPUT_TOKEN_EXHAUSTED, RETRYABLE_PROVIDER_ERRORS)

    async def test_g3_unusable_output_falls_back_to_the_next_candidate(self):
        breaker = _BreakerStub()
        openai_calls: list[str] = []

        async def fake_anthropic(model, system, prompt, max_tokens, temperature, thinking_budget):
            return SimpleNamespace(
                text="", stop_reason="max_tokens", model_used=model,
                input_tokens=11, output_tokens=8000, cache_read_tokens=0,
                cost_usd=0.2, latency_ms=9, error="", error_type="",
            )

        async def fake_openai(model, system, prompt, max_tokens, temperature):
            openai_calls.append(model)
            return SimpleNamespace(
                text='{"recovered": true}', stop_reason="end_turn", model_used=model,
                input_tokens=20, output_tokens=10, cache_read_tokens=0,
                cost_usd=0.01, latency_ms=5, error="", error_type="",
            )

        gateway = self._gateway(
            anthropic=fake_anthropic, openai=fake_openai, breaker=breaker
        )

        response = await self._call(gateway)

        self.assertEqual(response.text, '{"recovered": true}')
        self.assertFalse(response.error)
        self.assertTrue(response.fallback_used)
        self.assertEqual(response.failed_provider, "anthropic")
        self.assertEqual(response.failed_error_type, OUTPUT_TOKEN_EXHAUSTED)
        self.assertEqual(response.fallback_provider, "openai")
        self.assertTrue(openai_calls)
        self.assertIn(OUTPUT_TOKEN_EXHAUSTED, FALLBACK_ELIGIBLE_ERRORS)
        self.assertIn(EMPTY_PROVIDER_OUTPUT, FALLBACK_ELIGIBLE_ERRORS)

    async def test_g5_unusable_output_does_not_trip_the_circuit_breaker(self):
        """A provider answering promptly with an empty body is reachable and
        healthy; only this request's output contract failed."""
        breaker = _BreakerStub()

        async def fake_anthropic(model, system, prompt, max_tokens, temperature, thinking_budget):
            return SimpleNamespace(
                text="", stop_reason="end_turn", model_used=model,
                input_tokens=11, output_tokens=0, cache_read_tokens=0,
                cost_usd=0.0, latency_ms=9, error="", error_type="",
            )

        gateway = self._gateway(
            anthropic=fake_anthropic,
            openai=AsyncMock(side_effect=AssertionError("openai is unavailable here")),
            breaker=breaker,
            openai_available=False,
        )

        response = await self._call(gateway)

        self.assertEqual(response.error_type, EMPTY_PROVIDER_OUTPUT)
        self.assertFalse(response.retryable)
        self.assertEqual(
            breaker.failure_calls, [],
            "a content-contract failure must not be recorded as a transport outage",
        )

    async def test_g6_existing_transport_error_semantics_do_not_regress(self):
        for category in (
            RATE_LIMITED, TIMEOUT, PROVIDER_UNAVAILABLE,
            SERVER_ERROR, CONNECTION_ERROR, TRANSPORT_MALFORMED_RESPONSE,
        ):
            with self.subTest(category=category):
                self.assertIn(category, RETRYABLE_PROVIDER_ERRORS)
                self.assertIn(category, FALLBACK_ELIGIBLE_ERRORS)
        for category in (AUTH_ERROR, QUOTA_EXCEEDED):
            with self.subTest(category=category):
                self.assertNotIn(category, RETRYABLE_PROVIDER_ERRORS)
                self.assertIn(category, FALLBACK_ELIGIBLE_ERRORS)
        self.assertNotIn(INVALID_REQUEST, FALLBACK_ELIGIBLE_ERRORS)
        self.assertEqual(
            UNUSABLE_OUTPUT_ERRORS & RETRYABLE_PROVIDER_ERRORS, set(),
            "unusable output must never be retried in place",
        )

    async def test_usable_text_with_max_tokens_still_succeeds_through_the_gateway(self):
        """Case C at the gateway: partial output stays available downstream."""
        async def fake_anthropic(model, system, prompt, max_tokens, temperature, thinking_budget):
            return SimpleNamespace(
                text='{"executive_strategy": "partial', stop_reason="max_tokens",
                model_used=model, input_tokens=11, output_tokens=8000,
                cache_read_tokens=0, cost_usd=0.2, latency_ms=9,
                error="", error_type="",
            )

        gateway = self._gateway(
            anthropic=fake_anthropic,
            openai=AsyncMock(side_effect=AssertionError("no fallback expected")),
            breaker=_BreakerStub(),
            openai_available=False,
        )

        response = await self._call(gateway)

        self.assertFalse(response.error)
        self.assertEqual(response.text, '{"executive_strategy": "partial')
        self.assertEqual(response.stop_reason, "max_tokens")


class TestProviderOutputBudgetParity(unittest.TestCase):
    """Pins the parity finding this remediation documents but does not change:
    Strategy's response-token reservation is an Anthropic-only guarantee."""

    def test_openai_strategy_candidates_carry_no_response_token_reservation(self):
        candidates = select_model_candidates("strategy")
        openai_configs = [
            config for config, _ in candidates
            if config.provider is Provider.OPENAI
        ]
        self.assertTrue(openai_configs, "strategy must have OpenAI fallback candidates")
        for config in openai_configs:
            with self.subTest(model=config.model):
                # thinking_budget is dropped for OpenAI, which is correct — it is
                # an Anthropic control. But min_response_tokens rides along and
                # nothing consumes it: ModelConfig only validates it against
                # thinking_budget, and _call_openai never reads it. So no part of
                # max_completion_tokens is reserved for visible text.
                self.assertEqual(config.thinking_budget, 0)
                self.assertEqual(config.min_response_tokens, 4000)
                self.assertEqual(config.max_tokens, 8000)

    def test_anthropic_strategy_primary_reserves_response_tokens_locally(self):
        config = MODEL_ROUTING["strategy"]

        self.assertIs(config.provider, Provider.ANTHROPIC)
        self.assertEqual(config.thinking_budget, 4000)
        self.assertEqual(config.min_response_tokens, 4000)
        # The local invariant that makes the reservation real: thinking can never
        # be budgeted into the tokens reserved for the response.
        self.assertLessEqual(
            config.thinking_budget + config.min_response_tokens, config.max_tokens
        )
        with self.assertRaises(ValueError):
            ModelConfig(
                provider=Provider.ANTHROPIC,
                model="claude-opus-4-6",
                max_tokens=8000,
                thinking_budget=6000,
                min_response_tokens=4000,
            )


class TestUnusableOutputClassifier(unittest.TestCase):
    def test_classifier_separates_exhaustion_from_other_empty_output(self):
        cases = [
            (None, "length", OUTPUT_TOKEN_EXHAUSTED),
            ("", "max_tokens", OUTPUT_TOKEN_EXHAUSTED),
            ("   ", "max_tokens", OUTPUT_TOKEN_EXHAUSTED),
            ("", "stop", EMPTY_PROVIDER_OUTPUT),
            ("", "refusal", EMPTY_PROVIDER_OUTPUT),
            ("", None, EMPTY_PROVIDER_OUTPUT),
            ("text", "max_tokens", ""),
            ("text", "stop", ""),
            (" x ", "stop", ""),
        ]
        for text, stop_reason, expected in cases:
            with self.subTest(text=text, stop_reason=stop_reason):
                self.assertEqual(
                    classify_unusable_output(text=text, stop_reason=stop_reason),
                    expected,
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
