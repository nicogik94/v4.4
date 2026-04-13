"""Regression tests for the resumable sequential workflow runner."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks, HTTPException


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api
from llm_client import LLMResponse
from orchestrator import (
    build_monitor_prompt,
    get_first_unfinished_phase,
    is_workflow_complete,
    run_phase_node,
    run_workflow_sequence,
)
from state import (
    AuditOutput,
    ClassifyOutput,
    FMEAItem,
    GauntletOutput,
    GauntletResult,
    Hypothesis,
    MonitorCanary,
    MonitorCircuitBreaker,
    MonitorOODASchedule,
    MonitorOutput,
    MonitorScheduleItem,
    OODALoop,
    PhaseStatus,
    PreliminaryVerdict,
    Priority,
    ProjectState,
    SQIDimension,
    SQIOutput,
    StrategyAction,
    StrategyOutput,
    Verdict,
)


def make_response(text: str, input_tokens: int = 10, output_tokens: int = 5,
                  cost_usd: float = 0.01) -> LLMResponse:
    return LLMResponse(
        text=text,
        ok=True,
        model_used="claude-sonnet-4-6",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
    )


def make_hypotheses_payload() -> list[dict]:
    return [
        {"id": "H1", "text": "Test hypothesis 1", "alpha": 6, "beta": 4, "confirm": ">70%", "reject": "<30%"},
        {"id": "H2", "text": "Test hypothesis 2", "alpha": 5, "beta": 5, "confirm": ">70%", "reject": "<30%"},
        {"id": "H3", "text": "Test hypothesis 3", "alpha": 7, "beta": 3, "confirm": ">70%", "reject": "<30%"},
    ]


def make_monitor_payload() -> dict:
    return {
        "ooda_schedule": {
            "daily": [{"metric": "CTR", "owner": "editor", "source": "GSC"}],
            "weekly": [{"metric": "Organic clicks", "owner": "seo", "source": "GA"}],
            "monthly": [{"metric": "Topic share", "owner": "lead", "source": "dashboard"}],
        },
        "circuit_breakers": [{"strategy_ref": "S1", "trip": "CTR drops 20%", "reset": "2 healthy weeks"}],
        "canaries": [
            {"signal": "CTR", "direction": "up", "window": "7d", "meaning": "headline improvement"},
            {"signal": "Impressions", "direction": "up", "window": "7d", "meaning": "ranking lift"},
            {"signal": "Bounce rate", "direction": "down", "window": "7d", "meaning": "better UX"},
        ],
        "chaos_drills": [{"what": "Tracking outage", "when": "monthly", "measure": "time to detect"}],
        "hro_principles_active": ["preoccupation_with_failure"],
        "reentry_watch": ["R1", "R8"],
        "commitment_score": 81,
        "commitment_rationale": "Owners and metrics are explicit.",
    }


def make_strategy_output() -> StrategyOutput:
    return StrategyOutput(
        preliminary_verdicts=[
            PreliminaryVerdict(id="H1", verdict=Verdict.LIKELY_CONFIRMED, evidence="strong"),
            PreliminaryVerdict(id="H2", verdict=Verdict.NEEDS_MONITORING, evidence="mixed", monitoring_plan="watch CTR"),
            PreliminaryVerdict(id="H3", verdict=Verdict.LIKELY_REJECTED, evidence="weak"),
        ],
        executive_strategy="Focus on search-driven editorial planning.",
        strategies=[
            StrategyAction(
                priority=Priority.CRITICAL,
                action="Create search-driven briefs",
                justification="Align content with demand.",
                evidence_chain="H1 + audit",
                expected_impact="Higher CTR",
                effort="Medium",
                timeline="2 weeks",
                risk_if_ignored="Traffic stagnates",
                framework_source="HDD",
            )
        ],
        success_metrics=["CTR up 15%"],
    )


def make_completed_state(project_id: str = "workflow-complete") -> ProjectState:
    state = ProjectState(project_id=project_id, project_name="Workflow", brief="Test brief")
    state.intake_sanitization_findings = {}
    state.classify = ClassifyOutput(
        domain="Complicated",
        justification="Known structure",
        bf=42.0,
        variety_env="env",
        variety_sys="sys",
        variety_gaps="1. gap",
        variety_decision="Amplify",
        ooda=OODALoop(observe="obs", orient="ori", decide="dec", act="act", freq="weekly"),
        dq=[20, 18, 16, 14],
    )
    state.hypotheses = [Hypothesis(**item) for item in make_hypotheses_payload()]
    state.gauntlet = GauntletOutput(
        results=[GauntletResult(id="H1", risk_rank=1, frameworks=[{"fw": "STEELMAN", "finding": "x", "action": True}] * 10, crux="crux")]
    )
    state.audit = AuditOutput(
        data_based=True,
        fmea=[FMEAItem(component="site", failure_mode="slow", s=5, o=4, d=3, rpn=60, action="fix")],
        top_findings=["Improve page speed"],
    )
    state.strategy = make_strategy_output()
    state.monitor = MonitorOutput(
        ooda_schedule=MonitorOODASchedule(
            daily=[MonitorScheduleItem(metric="CTR", owner="editor", source="GSC")]
        ),
        circuit_breakers=[MonitorCircuitBreaker(strategy_ref="S1", trip="CTR down 20%", reset="2 healthy weeks")],
        canaries=[MonitorCanary(signal="CTR", direction="up", window="7d", meaning="improving")],
        commitment_score=80,
        commitment_rationale="Assigned owners",
    )
    state.sqi = SQIOutput(
        sqi_overall=75,
        dimensions=[SQIDimension(name="Evidence Quality", score=75, grade="B", finding="good enough")],
    )
    state.report = "final report"
    state.sealed = True
    state.seal_date = "2026-04-11"
    for phase in ("classify", "hypotheses", "gauntlet", "audit", "strategy", "sqi", "monitor", "report"):
        state.phase_status[phase] = PhaseStatus.COMPLETED
        state.phase_confidence[phase] = 1.0
    return state


class TestWorkflowHelpers(unittest.TestCase):
    def test_build_monitor_prompt_declares_required_schema(self):
        prompt = build_monitor_prompt(make_completed_state("monitor-prompt"))
        self.assertIn('"ooda_schedule"', prompt)
        self.assertIn('"circuit_breakers"', prompt)
        self.assertIn("Return ONE JSON object", prompt)
        self.assertIn("Include at least 3 canaries", prompt)

    def test_first_unfinished_phase_detects_missing_monitor_output(self):
        state = make_completed_state("missing-monitor")
        state.monitor = None
        self.assertEqual(get_first_unfinished_phase(state), "monitor")
        self.assertFalse(is_workflow_complete(state))

    def test_first_unfinished_phase_prefers_failed_phase(self):
        state = make_completed_state("failed-gauntlet")
        state.phase_status["gauntlet"] = PhaseStatus.FAILED
        self.assertEqual(get_first_unfinished_phase(state), "gauntlet")


class TestPhaseBookkeeping(unittest.IsolatedAsyncioTestCase):
    async def test_hypotheses_success_sets_seal_and_confidence(self):
        state = ProjectState(project_id="hypo", project_name="Hypo", brief="brief")
        state.intake_sanitization_findings = {}
        state.classify = make_completed_state("seed").classify
        response = make_response(json.dumps(make_hypotheses_payload()), 12, 6, 0.02)
        with patch("orchestrator.call_llm", new=AsyncMock(return_value=response)):
            with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
                updated = await run_phase_node(state, "hypotheses")

        self.assertEqual(updated.phase_status["hypotheses"], PhaseStatus.COMPLETED)
        self.assertEqual(updated.phase_confidence["hypotheses"], 1.0)
        self.assertTrue(updated.sealed)
        self.assertTrue(updated.seal_date)

    async def test_monitor_success_stores_output_and_confidence(self):
        state = make_completed_state("monitor-phase")
        state.phase_status["monitor"] = PhaseStatus.PENDING
        state.monitor = None
        response = make_response(json.dumps(make_monitor_payload()), 14, 7, 0.03)
        with patch("orchestrator.call_llm", new=AsyncMock(return_value=response)):
            with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
                updated = await run_phase_node(state, "monitor")

        self.assertEqual(updated.phase_status["monitor"], PhaseStatus.COMPLETED)
        self.assertEqual(updated.phase_confidence["monitor"], 1.0)
        self.assertIsNotNone(updated.monitor)
        self.assertEqual(updated.monitor.commitment_score, 81)


class TestSequentialRunner(unittest.IsolatedAsyncioTestCase):
    async def test_runner_resumes_from_monitor_and_reruns_report(self):
        state = make_completed_state("resume-monitor")
        state.phase_status["monitor"] = PhaseStatus.PENDING
        state.monitor = None
        executed: list[str] = []
        persisted: list[tuple[str, str]] = []

        async def fake_persist(snapshot: ProjectState):
            persisted.append(
                (
                    snapshot.current_phase,
                    snapshot.phase_status.get(snapshot.current_phase, PhaseStatus.PENDING).value
                    if hasattr(snapshot.phase_status.get(snapshot.current_phase, PhaseStatus.PENDING), "value")
                    else str(snapshot.phase_status.get(snapshot.current_phase, PhaseStatus.PENDING)),
                )
            )

        async def fake_run_phase_node(snapshot: ProjectState, phase: str) -> ProjectState:
            executed.append(phase)
            snapshot.current_phase = phase
            snapshot.phase_status[phase] = PhaseStatus.COMPLETED
            snapshot.phase_confidence[phase] = 1.0
            if phase == "monitor":
                snapshot.monitor = MonitorOutput(**make_monitor_payload())
            elif phase == "report":
                snapshot.report = "rerun report"
            return snapshot

        with patch("orchestrator.run_phase_node", new=AsyncMock(side_effect=fake_run_phase_node)):
            with patch("orchestrator.check_gate", side_effect=lambda s, p: {"passed": True, "blocking": [], "confidence": s.phase_confidence.get(p, 1.0)}):
                updated = await run_workflow_sequence(state, persist_state=fake_persist)

        self.assertEqual(executed, ["monitor", "report"])
        self.assertEqual(updated.report, "rerun report")
        self.assertEqual(updated.phase_status["monitor"], PhaseStatus.COMPLETED)
        self.assertGreaterEqual(len(persisted), 4)


class TestApiRunWorkflow(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        api.running.clear()

    async def asyncTearDown(self):
        api.running.clear()

    async def test_run_returns_already_complete_for_finished_project(self):
        state = make_completed_state("already-complete")
        with patch("api.store.load", new=AsyncMock(return_value=state)):
            response = await api.run_full_workflow(state.project_id, BackgroundTasks())
        self.assertEqual(response["status"], "already_complete")

    async def test_manual_phase_rejects_when_workflow_is_running(self):
        state = make_completed_state("running-project")
        api.running.add(state.project_id)
        with patch("api.store.load", new=AsyncMock(return_value=state)):
            with self.assertRaises(HTTPException) as ctx:
                await api.run_single_phase_endpoint(state.project_id, api.RunPhaseRequest(phase="report"))
        self.assertEqual(ctx.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main(verbosity=2)
