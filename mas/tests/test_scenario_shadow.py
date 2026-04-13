"""Focused tests for the shadow-mode scenario policy prototype."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import llm_client  # noqa: E402
from config import SCENARIO_SHADOW  # noqa: E402
from scenarios.engine import ScenarioShadowEngine  # noqa: E402
from scenarios.eval import build_phase_shadow_view  # noqa: E402
from scenarios.policy import BASELINE_SCENARIO_KEY  # noqa: E402
from scenarios.sqlite_store import ScenarioSQLiteStore  # noqa: E402


class _BrokenStore:
    def load_posterior(self, phase, scenario_key):
        raise RuntimeError("sqlite unavailable")


class TestScenarioShadowEngine(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.store = ScenarioSQLiteStore(str(Path(self.tmpdir.name) / "scenario-shadow.sqlite"))
        self.engine = ScenarioShadowEngine(store=self.store)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_bounded_generation_and_sample_cap(self):
        result = self.engine.evaluate_request(
            phase="audit",
            project_id="proj-shadow",
            baseline_selected_provider="anthropic",
            baseline_selected_model="claude-sonnet-4-6",
            actual_provider_used="anthropic",
            actual_model_used="claude-sonnet-4-6",
            response_ok=True,
            latency_ms=1200,
            cost_usd=0.08,
        )

        self.assertTrue(result.available)
        view = build_phase_shadow_view("proj-shadow", "audit", store=self.store)
        self.assertTrue(view.available)
        self.assertLessEqual(len(view.scenarios), SCENARIO_SHADOW.max_scenarios)
        self.assertLessEqual(view.sample_count, SCENARIO_SHADOW.hard_sample_cap)
        self.assertTrue(any(item.is_baseline for item in view.scenarios))

    def test_online_updates_persist_posterior_observation_counts(self):
        self.engine.evaluate_request(
            phase="strategy",
            project_id="proj-updates",
            baseline_selected_provider="anthropic",
            baseline_selected_model="claude-opus-4-6",
            actual_provider_used="anthropic",
            actual_model_used="claude-opus-4-6",
            response_ok=True,
            latency_ms=1500,
            cost_usd=0.11,
        )
        self.engine.evaluate_request(
            phase="strategy",
            project_id="proj-updates",
            baseline_selected_provider="anthropic",
            baseline_selected_model="claude-opus-4-6",
            actual_provider_used="anthropic",
            actual_model_used="claude-opus-4-6",
            response_ok=False,
            latency_ms=2100,
            cost_usd=0.16,
        )

        posterior = self.store.load_posterior("strategy", BASELINE_SCENARIO_KEY)
        self.assertIsNotNone(posterior)
        self.assertEqual(posterior.observation_count, 2)
        self.assertGreaterEqual(posterior.success_alpha, 3.0)
        self.assertGreaterEqual(posterior.success_beta, 2.0)

    def test_sqlite_store_round_trip(self):
        self.engine.evaluate_request(
            phase="classify",
            project_id="proj-sqlite",
            baseline_selected_provider="anthropic",
            baseline_selected_model="claude-haiku-4-5-20251001",
            actual_provider_used="anthropic",
            actual_model_used="claude-haiku-4-5-20251001",
            response_ok=True,
            latency_ms=900,
            cost_usd=0.01,
        )

        request_id = self.store.latest_request_id("proj-sqlite", "classify")
        self.assertTrue(request_id)
        rows = self.store.list_request_observations(request_id, "classify")
        self.assertTrue(rows)
        self.assertTrue(any(row["scenario_key"] == BASELINE_SCENARIO_KEY for row in rows))

    def test_deterministic_fallback_if_shadow_store_breaks(self):
        engine = ScenarioShadowEngine(store=_BrokenStore())

        result = engine.evaluate_request(
            phase="audit",
            project_id="proj-fallback",
            baseline_selected_provider="anthropic",
            baseline_selected_model="claude-sonnet-4-6",
            actual_provider_used="anthropic",
            actual_model_used="claude-sonnet-4-6",
            response_ok=True,
            latency_ms=1200,
            cost_usd=0.08,
        )

        self.assertFalse(result.available)
        self.assertTrue(result.fallback_to_baseline)


class TestScenarioShadowCallLLM(unittest.IsolatedAsyncioTestCase):
    async def test_call_llm_keeps_baseline_response_when_shadow_hook_fails(self):
        fake_response = llm_client.LLMResponse(
            text="shadow-safe",
            ok=True,
            model_used="claude-haiku-4-5-20251001",
            provider_used="anthropic",
            input_tokens=8,
            output_tokens=4,
            latency_ms=12,
            cost_usd=0.01,
        )

        with patch("llm_client._call_anthropic", new=AsyncMock(return_value=fake_response)):
            with patch("llm_client.run_shadow_evaluation", side_effect=RuntimeError("shadow down")):
                response = await llm_client.call_llm("classify", "system", "prompt", project_id="shadow-safe")

        self.assertTrue(response.ok)
        self.assertEqual(response.text, "shadow-safe")
        self.assertEqual(response.provider_used, "anthropic")


if __name__ == "__main__":
    unittest.main(verbosity=2)
