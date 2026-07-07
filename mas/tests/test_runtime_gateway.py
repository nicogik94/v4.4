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
    DefaultProviderGateway,
    INVALID_REQUEST,
    QUOTA_EXCEEDED,
    build_cache_key,
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
