"""API tests for the shadow-mode scenario policy read surfaces."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api  # noqa: E402
from scenarios.engine import ScenarioShadowEngine  # noqa: E402
from scenarios.sqlite_store import ScenarioSQLiteStore  # noqa: E402
from tests.test_decision_objects import make_state  # noqa: E402


class TestScenarioShadowApi(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        api.running.clear()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.store = ScenarioSQLiteStore(str(Path(self.tmpdir.name) / "scenario-shadow.sqlite"))
        self.engine = ScenarioShadowEngine(store=self.store)

    async def asyncTearDown(self):
        api.running.clear()
        self.tmpdir.cleanup()

    async def test_shadow_routes_expose_latest_project_and_phase_views(self):
        state = make_state("scenario-api")
        self.engine.evaluate_request(
            phase="audit",
            project_id=state.project_id,
            baseline_selected_provider="anthropic",
            baseline_selected_model="claude-sonnet-4-6",
            actual_provider_used="anthropic",
            actual_model_used="claude-sonnet-4-6",
            response_ok=True,
            latency_ms=1100,
            cost_usd=0.07,
        )

        with patch("api.store.load", new=AsyncMock(return_value=state)):
            with patch("scenarios.eval._get_store", return_value=self.store):
                project_view = await api.get_project_scenario_shadow(state.project_id)
                phase_view = await api.get_project_scenario_shadow_phase(state.project_id, "audit")

        self.assertTrue(project_view.available)
        self.assertTrue(project_view.phases)
        self.assertEqual(project_view.phases[0].phase, "audit")
        self.assertTrue(phase_view.available)
        self.assertEqual(phase_view.phase, "audit")
        self.assertTrue(phase_view.baseline_executed)
        self.assertTrue(phase_view.scenarios)
        self.assertTrue(phase_view.comparison_against_baseline)


if __name__ == "__main__":
    unittest.main(verbosity=2)
