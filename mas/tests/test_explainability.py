"""Focused tests for the explainability / decision-trace layer."""
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api  # noqa: E402
from explainability import build_explainability_report, build_phase_trace, build_project_trace  # noqa: E402
from knowledge.registry import ensure_knowledge_layer, upsert_source_entry  # noqa: E402
from knowledge.sync import sync_offline_source  # noqa: E402
from state import PhaseStatus, SourceRegistryEntry  # noqa: E402
from tests.test_decision_objects import make_state  # noqa: E402


def make_completed_state(project_id: str = "explainability") :
    state = make_state(project_id)
    state.report = "# Final report\nDecision summary"
    state.current_phase = "report"
    state.sealed = True
    state.seal_date = "2026-04-12"
    for phase in ("classify", "hypotheses", "gauntlet", "audit", "strategy", "sqi", "monitor", "report"):
        state.phase_status[phase] = PhaseStatus.COMPLETED
        state.phase_confidence[phase] = 1.0
        state.phase_run_completed_at[phase] = datetime(2026, 4, 12, 12, 0, 0).isoformat()
    state.budget_consumed["llm_call_count"] = 8
    state.budget_consumed["total_tokens"] = 12345
    state.budget_consumed["total_cost_usd"] = 0.4567
    return state


class TestExplainabilityDerivation(unittest.TestCase):
    def test_phase_trace_derives_frameworks_inputs_and_gate(self):
        state = make_completed_state("trace-phase")

        trace = build_phase_trace(state, "strategy")

        self.assertEqual(trace.phase, "strategy")
        self.assertTrue(trace.frameworks_used)
        self.assertTrue(trace.inputs_used)
        self.assertIn("Turn the working hypotheses", trace.purpose)
        self.assertTrue(trace.gate_result.configured)
        self.assertTrue(trace.gate_result.passed)
        self.assertIn("Proceed", trace.next_step)

    def test_completed_phase_prefers_recorded_gate_outcome_when_recompute_differs(self):
        state = make_completed_state("trace-gate-history")
        state.sealed = False

        trace = build_phase_trace(state, "hypotheses")

        self.assertEqual(trace.status, "completed")
        self.assertTrue(trace.gate_result.passed)
        self.assertEqual(trace.gate_result.source, "recorded_completion")
        self.assertIn("persisted completed outcome", trace.gate_result.note)

    def test_strategy_explanations_include_evidence_and_deterministic_checks(self):
        state = make_completed_state("trace-action")
        state.sealed = False

        report = build_explainability_report(state)

        self.assertTrue(report.strategy_explanations)
        explanation = report.strategy_explanations[0]
        self.assertTrue(explanation.supporting_evidence)
        self.assertTrue(explanation.supporting_findings)
        self.assertTrue(any("Det score overall" in item for item in explanation.deterministic_checks))
        self.assertTrue(any("source=" in item for item in explanation.deterministic_checks))
        self.assertEqual(explanation.confidence_label, state.strategy.confidence)

    def test_logic_separation_distinguishes_sources(self):
        state = make_completed_state("trace-logic")
        ensure_knowledge_layer(state)
        upsert_source_entry(
            state,
            SourceRegistryEntry(
                source_id="src-trace",
                name="Trace fixture",
                source_kind="offline_fixture",
                connector_type="offline_fixture",
                owner="operator",
                access_mode="manual",
                sensitivity="internal",
            ),
        )
        sync_offline_source(
            state,
            "src-trace",
            [{"source_ref": "fixture://trace/1", "title": "Fresh note", "observed_at": datetime.now().isoformat()}],
            actor="operator",
        )

        report = build_explainability_report(state)

        self.assertTrue(any("Gate check" in item for item in report.logic_separation.deterministic_logic))
        self.assertTrue(any("Executive strategy" in item for item in report.logic_separation.model_judgment))
        self.assertTrue(any("Risk classification" in item for item in report.logic_separation.policy_enforcement))
        self.assertTrue(any("LLM calls" in item for item in report.logic_separation.runtime_metadata))
        self.assertTrue(any("not yet used in prompt-facing reasoning" in item for item in report.logic_separation.knowledge_inputs))

    def test_uncertainty_summary_is_derived_from_existing_artifacts(self):
        state = make_completed_state("trace-uncertainty")

        report = build_explainability_report(state)

        self.assertTrue(report.uncertainty_summary.open_questions)
        self.assertTrue(report.uncertainty_summary.would_change_conclusion)
        self.assertTrue(report.uncertainty_summary.monitor_next)


class TestExplainabilityApi(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        api.running.clear()

    async def asyncTearDown(self):
        api.running.clear()

    async def test_trace_and_explain_routes_expose_derived_views(self):
        state = make_completed_state("trace-api")

        with patch("api.store.load", new=AsyncMock(return_value=state)):
            trace = await api.get_project_trace(state.project_id)
            phase_trace = await api.get_phase_trace(state.project_id, "strategy")
            explain = await api.get_explainability(state.project_id)

        self.assertEqual(trace.project_id, state.project_id)
        self.assertEqual(len(trace.phases), 8)
        self.assertEqual(phase_trace.phase, "strategy")
        self.assertTrue(explain.strategy_explanations)
        self.assertIn("Trace summaries separate deterministic checks", explain.overview)


if __name__ == "__main__":
    unittest.main(verbosity=2)
