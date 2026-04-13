"""Tests for runtime gateway and semantic cache compatibility."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import llm_client  # noqa: E402
from config import MODEL_ROUTING  # noqa: E402
from extensions.runtime import GatewayRequest, ProviderSelection, RoutingConfig, RoutingContext  # noqa: E402
from runtime.cache import InMemorySemanticCache, NoOpSemanticCache  # noqa: E402
from runtime.provider_gateway import DefaultProviderGateway, build_cache_key, select_model_config  # noqa: E402


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


class TestRuntimeRouting(unittest.TestCase):
    def test_select_model_config_preserves_phase_default(self):
        config, selection = select_model_config("classify")

        self.assertEqual(config.model, MODEL_ROUTING["classify"].model)
        self.assertEqual(selection.provider, MODEL_ROUTING["classify"].provider.value)
        self.assertEqual(selection.reason, "phase_routing")

    def test_select_model_config_uses_phase_override(self):
        config, selection = select_model_config(
            "classify",
            routing_config=RoutingConfig(phase_overrides={"classify": "gpt-5-mini"}),
        )

        self.assertEqual(config.model, "gpt-5-mini")
        self.assertEqual(selection.provider, "openai")
        self.assertEqual(selection.reason, "phase_override")

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
                    error_type="network",
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
        self.assertTrue(runtime_events[-1]["fallback_used"])
        self.assertEqual(runtime_events[-1]["cache_status"], "disabled")

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

        with patch("llm_client._call_anthropic", new=AsyncMock(return_value=fake_response)):
            response = await llm_client.call_llm("classify", "system", "prompt")

        self.assertTrue(response.ok)
        self.assertEqual(response.text, "llm ok")
        self.assertEqual(response.provider_used, "anthropic")
        self.assertEqual(response.model_used, MODEL_ROUTING["classify"].model)


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
