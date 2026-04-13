"""Regression tests for operator dossier editing and rerun invalidation."""
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api
from orchestrator import get_first_unfinished_phase
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


def make_hypotheses_payload() -> list[dict]:
    return [
        {
            "id": "H1",
            "text": "Search-driven briefs will increase CTR",
            "justification": "GSC impressions are high but CTR is low.",
            "signal": "CTR",
            "alpha": 6,
            "beta": 4,
            "confirm": "CTR +15%",
            "reject": "CTR flat",
            "evoi": "high",
            "portfolio_cluster": "editorial",
            "status": "OPEN",
        },
        {
            "id": "H2",
            "text": "Refreshing archive content will lift rankings",
            "justification": "Historical posts have stable impressions but stale metadata.",
            "signal": "Average position",
            "alpha": 5,
            "beta": 5,
            "confirm": "position +2",
            "reject": "no movement",
            "evoi": "medium",
            "portfolio_cluster": "archive",
            "status": "OPEN",
        },
        {
            "id": "H3",
            "text": "Internal linking will reduce orphaned content",
            "justification": "Topic clusters are fragmented and links are sparse.",
            "signal": "Pages/session",
            "alpha": 7,
            "beta": 3,
            "confirm": "pages/session +10%",
            "reject": "no change",
            "evoi": "medium",
            "portfolio_cluster": "ux",
            "status": "OPEN",
        },
    ]


def make_strategy_output() -> StrategyOutput:
    return StrategyOutput(
        preliminary_verdicts=[
            PreliminaryVerdict(id="H1", verdict=Verdict.LIKELY_CONFIRMED, evidence="strong signal"),
            PreliminaryVerdict(id="H2", verdict=Verdict.NEEDS_MONITORING, evidence="mixed signal", monitoring_plan="watch rankings"),
            PreliminaryVerdict(id="H3", verdict=Verdict.LIKELY_REJECTED, evidence="weak signal"),
        ],
        executive_strategy="Concentrate editorial effort on search-aligned briefs.",
        strategies=[
            StrategyAction(
                priority=Priority.CRITICAL,
                action="Build a search-driven editorial brief template",
                justification="Editorial choices need consistent demand signals.",
                evidence_chain="H1 + audit",
                expected_impact="Higher CTR",
                effort="Medium",
                timeline="2 weeks",
                risk_if_ignored="Traffic plateau",
                framework_source="HDD",
            )
        ],
        implementation_sequence="1. Build brief template 2. Pilot on high-impression topics",
        success_metrics=["CTR up 15%"],
        monitoring_plan="Weekly review",
        review_date="2026-04-30",
        confidence="Medium",
        reentry_check="R1 if CTR drops",
    )


def make_monitor_output() -> MonitorOutput:
    return MonitorOutput(
        ooda_schedule=MonitorOODASchedule(
            daily=[MonitorScheduleItem(metric="CTR", owner="editor", source="GSC")],
            weekly=[MonitorScheduleItem(metric="Clicks", owner="seo", source="GA")],
            monthly=[MonitorScheduleItem(metric="Topic share", owner="lead", source="dashboard")],
        ),
        circuit_breakers=[MonitorCircuitBreaker(strategy_ref="S1", trip="CTR down 20%", reset="2 healthy weeks")],
        canaries=[MonitorCanary(signal="CTR", direction="up", window="7d", meaning="headline lift")],
        commitment_score=82,
        commitment_rationale="Named owners and review windows exist.",
    )


def make_completed_state(project_id: str = "dossier-complete") -> ProjectState:
    state = ProjectState(project_id=project_id, project_name="Decision dossier", brief="Original brief", data="Original data")
    state.classify = ClassifyOutput(
        domain="Complicated",
        justification="Cause and effect are expert-discoverable.",
        bf=42.0,
        variety_env="editorial, SEO, development",
        variety_sys="WordPress + process",
        variety_gaps="1. No search alignment",
        variety_decision="Amplify through briefs",
        ooda=OODALoop(observe="GSC", orient="editorial backlog", decide="prioritize", act="optimize", freq="weekly"),
        dq=[20, 18, 16, 14],
    )
    state.hypotheses = [Hypothesis(**item) for item in make_hypotheses_payload()]
    state.gauntlet = GauntletOutput(
        results=[GauntletResult(id="H1", risk_rank=1, frameworks=[{"fw": "STEELMAN", "finding": "x", "action": True}] * 10, crux="crux")],
        portfolio_correlation=0.2,
    )
    state.audit = AuditOutput(
        data_based=True,
        fmea=[FMEAItem(component="site", failure_mode="slow", s=5, o=4, d=3, rpn=60, action="compress assets")],
        top_findings=["Improve headline alignment", "Refresh historical content"],
        observation_needs=["Track CTR weekly"],
    )
    state.strategy = make_strategy_output()
    state.monitor = make_monitor_output()
    state.sqi = SQIOutput(
        sqi_overall=78,
        dimensions=[SQIDimension(name="Evidence Quality", score=78, grade="B", finding="grounded")],
    )
    state.report = "Final markdown report"
    state.det_scores = None
    state.phase_summaries = {phase: f"{phase} summary" for phase in ("classify", "hypotheses", "gauntlet", "audit", "strategy", "monitor", "report")}
    state.sealed = True
    state.seal_date = "2026-04-11"
    state.observations = {"owner_note": "keep updated"}
    state.timer_logs = [{"time": "09:00", "label": "standup"}]
    for phase in ("classify", "hypotheses", "gauntlet", "audit", "strategy", "sqi", "monitor", "report"):
        state.phase_status[phase] = PhaseStatus.COMPLETED
        state.phase_confidence[phase] = 1.0
    return state


class TestHypothesisJustificationModel(unittest.TestCase):
    def test_hypothesis_justification_persists_in_state_dump(self):
        state = ProjectState(project_id="justify", project_name="Justify", brief="brief")
        state.hypotheses = [Hypothesis(**make_hypotheses_payload()[0])]

        dumped = state.model_dump(mode="json")

        self.assertEqual(dumped["hypotheses"][0]["justification"], "GSC impressions are high but CTR is low.")


class TestOperatorDossierApi(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        api.running.clear()

    async def asyncTearDown(self):
        api.running.clear()

    async def test_input_patch_invalidates_classify_and_downstream(self):
        state = make_completed_state("input-stale")
        with patch("api.store.load", new=AsyncMock(return_value=state)), patch("api.store.save", new=AsyncMock()) as save_mock:
            response = await api.patch_project_input(
                state.project_id,
                api.PatchProjectInputRequest(brief="Updated brief", data="Updated data"),
            )

        self.assertEqual(response["status"], "updated")
        self.assertIn("classify", response["invalidated_phases"])
        self.assertEqual(state.current_phase, "classify")
        self.assertEqual(get_first_unfinished_phase(state), "classify")
        self.assertEqual(state.phase_status["classify"], PhaseStatus.STALE)
        self.assertEqual(state.phase_status["report"], PhaseStatus.STALE)
        self.assertIsNone(state.classify)
        self.assertIsNone(state.strategy)
        self.assertIsNone(state.monitor)
        self.assertIsNone(state.report)
        self.assertFalse(state.sealed)
        self.assertIsNone(state.seal_date)
        self.assertEqual(state.policy_audit_log[-1]["event_type"], "operator_state_edit")
        save_mock.assert_awaited()

    async def test_phase_patch_rejects_invalid_payload_shape(self):
        state = make_completed_state("bad-payload")
        with patch("api.store.load", new=AsyncMock(return_value=state)):
            with self.assertRaises(HTTPException) as ctx:
                await api.patch_phase_output(state.project_id, "strategy", ["not", "an", "object"])
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_hypotheses_patch_sets_seal_and_invalidates_downstream(self):
        state = make_completed_state("hypothesis-edit")
        new_payload = make_hypotheses_payload()
        new_payload[0]["justification"] = "Updated justification from operator."
        with patch("api.store.load", new=AsyncMock(return_value=state)), patch("api.store.save", new=AsyncMock()):
            response = await api.patch_phase_output(state.project_id, "hypotheses", new_payload)

        self.assertEqual(response["status"], "updated")
        self.assertEqual(state.phase_status["hypotheses"], PhaseStatus.COMPLETED)
        self.assertTrue(state.sealed)
        self.assertTrue(state.seal_date)
        self.assertEqual(state.hypotheses[0].justification, "Updated justification from operator.")
        self.assertEqual(state.current_phase, "gauntlet")
        self.assertEqual(get_first_unfinished_phase(state), "gauntlet")
        self.assertEqual(state.phase_status["gauntlet"], PhaseStatus.STALE)
        self.assertEqual(state.phase_status["report"], PhaseStatus.STALE)

    async def test_strategy_patch_invalidates_sqi_monitor_and_report(self):
        state = make_completed_state("strategy-edit")
        payload = make_strategy_output().model_dump(mode="json")
        payload["strategies"][0]["justification"] = "Operator override justification."
        with patch("api.store.load", new=AsyncMock(return_value=state)), patch("api.store.save", new=AsyncMock()):
            response = await api.patch_phase_output(state.project_id, "strategy", payload)

        self.assertEqual(response["invalidated_phases"], ["sqi", "monitor", "report"])
        self.assertEqual(state.current_phase, "sqi")
        self.assertEqual(get_first_unfinished_phase(state), "sqi")
        self.assertEqual(state.phase_status["strategy"], PhaseStatus.COMPLETED)
        self.assertEqual(state.phase_confidence["strategy"], 1.0)
        self.assertEqual(state.strategy.strategies[0].justification, "Operator override justification.")
        self.assertEqual(state.phase_status["sqi"], PhaseStatus.STALE)
        self.assertEqual(state.phase_status["monitor"], PhaseStatus.STALE)
        self.assertEqual(state.phase_status["report"], PhaseStatus.STALE)

    async def test_monitor_patch_invalidates_only_report(self):
        state = make_completed_state("monitor-edit")
        payload = make_monitor_output().model_dump(mode="json")
        payload["commitment_rationale"] = "Operator refreshed the rationale."
        with patch("api.store.load", new=AsyncMock(return_value=state)), patch("api.store.save", new=AsyncMock()):
            response = await api.patch_phase_output(state.project_id, "monitor", payload)

        self.assertEqual(response["invalidated_phases"], ["report"])
        self.assertEqual(state.current_phase, "report")
        self.assertEqual(get_first_unfinished_phase(state), "report")
        self.assertEqual(state.phase_status["monitor"], PhaseStatus.COMPLETED)
        self.assertEqual(state.monitor.commitment_rationale, "Operator refreshed the rationale.")
        self.assertEqual(state.phase_status["report"], PhaseStatus.STALE)

    async def test_manual_edit_is_blocked_while_run_is_active(self):
        state = make_completed_state("running-edit")
        api.running.add(state.project_id)
        with patch("api.store.load", new=AsyncMock(return_value=state)):
            with self.assertRaises(HTTPException) as ctx:
                await api.patch_project_input(state.project_id, api.PatchProjectInputRequest(data="new"))
        self.assertEqual(ctx.exception.status_code, 409)

    async def test_export_docx_returns_attachment(self):
        state = make_completed_state("export-docx")
        with patch("api.store.load", new=AsyncMock(return_value=state)):
            response = await api.export_project(state.project_id, "docx")
        self.assertEqual(response.media_type, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        self.assertIn("attachment;", response.headers["content-disposition"])
        self.assertIn(".docx", response.headers["content-disposition"])
        self.assertGreater(len(response.body), 500)

    async def test_export_pdf_returns_attachment(self):
        state = make_completed_state("export-pdf")
        with patch("api.store.load", new=AsyncMock(return_value=state)):
            response = await api.export_project(state.project_id, "pdf")
        self.assertEqual(response.media_type, "application/pdf")
        self.assertIn("attachment;", response.headers["content-disposition"])
        self.assertIn(".pdf", response.headers["content-disposition"])
        self.assertGreater(len(response.body), 500)


if __name__ == "__main__":
    unittest.main(verbosity=2)
