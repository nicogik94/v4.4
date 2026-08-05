"""Deterministic hardening tests for support-phase reliability.

These tests use small fake states and mocked LLM responses. They do not call
external providers and do not introduce new workflow behavior.
"""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import orchestrator  # noqa: E402
from config import GATE_CONFIGS  # noqa: E402
from delivery_readiness import build_delivery_review_readiness  # noqa: E402
from llm_client import LLMResponse  # noqa: E402
from orchestrator import (  # noqa: E402
    build_audit_prompt,
    build_monitor_prompt,
    build_strategy_prompt,
    run_phase_node,
)
from policy import PHASE_ACTION_MAP, BreakerState, Reversibility, policy_gate  # noqa: E402
from state import (  # noqa: E402
    ClassifyOutput,
    GauntletOutput,
    Hypothesis,
    MonitorCanary,
    MonitorChaosDrill,
    MonitorCircuitBreaker,
    MonitorOODASchedule,
    MonitorOutput,
    MonitorScheduleItem,
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
from tools.scoring import check_gate, summarize_phase_output  # noqa: E402
from workspace import build_workspace_summary  # noqa: E402


def _state(project_id: str = "support-phase") -> ProjectState:
    state = ProjectState(
        project_id=project_id,
        project_name="Support Phase Fixture",
        brief="Decide whether to pilot an internal quality-control workflow.",
    )
    state.intake_sanitization_findings = {}
    return state


def _hypotheses(count: int) -> list[Hypothesis]:
    return [
        Hypothesis(
            id=f"H{index}",
            text=f"Hypothesis {index}",
            justification="Decision-relevant uncertainty.",
            signal="measurable",
            alpha=6,
            beta=4,
            confirm="metric improves",
            reject="metric deteriorates",
            evoi="medium",
            portfolio_cluster="quality",
        )
        for index in range(1, count + 1)
    ]


def _classify_output() -> ClassifyOutput:
    return ClassifyOutput(
        domain="Complicated",
        justification="Known operational decision with measurable constraints.",
        bf=24,
        variety_env="operators, constraints, telemetry",
        variety_sys="workflow, owners, review cadence",
        variety_gaps="operator capacity and measurement lag",
        variety_decision="Amplify reliable signal, dampen noisy work.",
        dq=[18, 16, 15, 14],
    )


def _strategy_output() -> StrategyOutput:
    return StrategyOutput(
        preliminary_verdicts=[
            PreliminaryVerdict(
                id="H1",
                verdict=Verdict.NEEDS_MONITORING,
                evidence="Initial signal is promising but not settled.",
                monitoring_plan="Watch weekly defect rate and review load.",
            ),
            PreliminaryVerdict(id="H2", verdict=Verdict.LIKELY_CONFIRMED, evidence="Baseline supports pilot."),
        ],
        executive_strategy="Run a bounded internal pilot with explicit operator review before expansion.",
        strategies=[
            StrategyAction(
                priority=Priority.CRITICAL,
                action="Run a two-week quality-control pilot",
                justification="Tests the highest-risk workflow assumption before rollout.",
                evidence_chain="H1 + audit finding",
                expected_impact="Reduce defect rate by 10% if the hypothesis is right.",
                effort="Medium",
                timeline="2 weeks",
                risk_if_ignored="Scaling noisy process creates avoidable rework.",
                framework_source="FMEA",
            )
        ],
        success_metrics=["Defect rate", "Operator review load"],
        monitoring_plan="Review pilot metrics weekly before any broader rollout.",
    )


def _gauntlet_payload() -> dict:
    frameworks = [
        {"fw": f"FW{index}", "finding": f"finding {index}", "action": index % 2 == 0}
        for index in range(1, 11)
    ]
    return {
        "results": [
            {
                "id": f"H{index}",
                "risk_rank": index,
                "frameworks": list(frameworks),
                "crux": f"testable crux {index}",
                "top_fmea": {"mode": "missed signal", "s": 5, "o": 4, "d": 3, "rpn": 60},
                "fta_cut_set": "measurement gap + owner gap",
            }
            for index in range(1, 4)
        ],
        "portfolio_correlation": 0.41,
        "mece_gaps": "capacity boundary needs review",
        "thompson_priority": "H1",
        "evoi_ranking": "H1 > H2 > H3",
    }


def _monitor_output() -> MonitorOutput:
    return MonitorOutput(
        ooda_schedule=MonitorOODASchedule(
            daily=[MonitorScheduleItem(metric="Defect rate", owner="ops lead", source="QA log")],
            weekly=[MonitorScheduleItem(metric="Review load", owner="operator", source="workspace")],
            monthly=[MonitorScheduleItem(metric="Pilot stability", owner="sponsor", source="review memo")],
        ),
        circuit_breakers=[
            MonitorCircuitBreaker(
                strategy_ref="S1",
                trip="Defect rate worsens for two consecutive reviews.",
                reset="Two healthy reviews and operator approval to continue.",
            )
        ],
        canaries=[
            MonitorCanary(signal="Defect rate", direction="down", window="7d", meaning="quality improving"),
            MonitorCanary(signal="Review load", direction="down", window="7d", meaning="operator burden falling"),
            MonitorCanary(signal="Escalations", direction="down", window="7d", meaning="risk contained"),
        ],
        chaos_drills=[
            MonitorChaosDrill(what="Simulate missing telemetry", when="before rollout", measure="time to detect")
        ],
        hro_principles_active=["preoccupation_with_failure"],
        reentry_watch=["R1", "R8"],
        commitment_score=64,
        commitment_rationale="Owners exist, but reset criteria still require operator review.",
    )


def _sqi_payload() -> dict:
    return {
        "sqi_overall": 42,
        "dimensions": [
            {"name": "Evidence Quality", "score": 35, "grade": "D", "finding": "Evidence is thin."},
            {"name": "Falsifiability", "score": 50, "grade": "C", "finding": "Kill criteria exist."},
        ],
        "rumelt_test": {
            "consistency": {"pass": False, "note": "Evidence and action are not fully aligned."},
            "consonance": {"pass": True, "note": "Fits environment."},
            "advantage": {"pass": False, "note": "Advantage is not proven."},
            "feasibility": {"pass": True, "note": "Pilot is feasible."},
        },
        "opposite_test": [{"strategy": "pilot", "opposite": "do nothing", "is_stupid": False, "verdict": "review"}],
        "wwhtbt": [{"strategy": "pilot", "must_be_true": "operators can measure defects", "kill_criterion": "no telemetry"}],
        "conflicts": [{"field": "evidence_chain", "issue": "thin source"}],
        "weakest_link": "Evidence Quality",
        "improvement_actions": ["Collect baseline defect data", "Confirm operator review owner"],
    }


def _fake_response(payload: dict) -> LLMResponse:
    return LLMResponse(
        text=json.dumps(payload),
        ok=True,
        model_used="fake-model",
        provider_used="fake-provider",
        input_tokens=12,
        output_tokens=8,
        cost_usd=0.0,
    )


def test_graph_hypotheses_node_skips_gauntlet_when_hypothesis_threshold_is_not_met():
    async def fake_run_phase(state: ProjectState, phase: str) -> ProjectState:
        if phase == "hypotheses":
            state.hypotheses = _hypotheses(2)
            state.phase_status["hypotheses"] = PhaseStatus.COMPLETED
        elif phase == "gauntlet":
            state.gauntlet = GauntletOutput(**_gauntlet_payload())
        return state

    with patch("orchestrator.run_phase_node", new=AsyncMock(side_effect=fake_run_phase)) as run_mock:
        graph = orchestrator.build_workflow_graph()
        updated = asyncio.run(graph.nodes["hypotheses"].runnable.ainvoke(_state("gauntlet-threshold-skip")))

    assert [call.args[1] for call in run_mock.await_args_list] == ["hypotheses"]
    assert len(updated.hypotheses) == 2
    assert updated.gauntlet is None


def test_graph_hypotheses_node_runs_gauntlet_at_three_hypotheses_and_preserves_shape():
    async def fake_run_phase(state: ProjectState, phase: str) -> ProjectState:
        if phase == "hypotheses":
            state.hypotheses = _hypotheses(3)
            state.phase_status["hypotheses"] = PhaseStatus.COMPLETED
        elif phase == "gauntlet":
            state.gauntlet = GauntletOutput(**_gauntlet_payload())
            state.phase_status["gauntlet"] = PhaseStatus.COMPLETED
        return state

    with patch("orchestrator.run_phase_node", new=AsyncMock(side_effect=fake_run_phase)) as run_mock:
        graph = orchestrator.build_workflow_graph()
        updated = asyncio.run(graph.nodes["hypotheses"].runnable.ainvoke(_state("gauntlet-threshold-run")))

    assert [call.args[1] for call in run_mock.await_args_list] == ["hypotheses", "gauntlet"]
    assert updated.gauntlet is not None
    assert len(updated.gauntlet.results) == 3
    assert len(updated.gauntlet.results[0].frameworks) == 10
    assert updated.gauntlet.results[0].top_fmea["rpn"] == 60


def test_failed_strategy_graph_node_aborts_before_sqi_scoring_monitor_and_report():
    async def fake_run_phase(state: ProjectState, phase: str) -> ProjectState:
        if phase == "strategy":
            state.strategy = None
            state.phase_status["strategy"] = PhaseStatus.FAILED
        else:
            raise AssertionError(f"unexpected downstream provider phase: {phase}")
        return state

    with patch("orchestrator.run_phase_node", new=AsyncMock(side_effect=fake_run_phase)) as run_mock:
        graph = orchestrator.build_workflow_graph()
        updated = asyncio.run(
            graph.nodes["strategy"].runnable.ainvoke(_state("failed-strategy-graph"))
        )

    assert [call.args[1] for call in run_mock.await_args_list] == ["strategy"]
    assert orchestrator.route_after_strategy(updated) == "abort"
    assert ("strategy", "scoring") not in graph.edges
    assert "strategy" in graph.branches


def test_completed_strategy_and_sqi_graph_route_allows_scoring():
    state = _state("valid-strategy-graph-route")
    state.strategy = _strategy_output()
    state.sqi = SQIOutput(sqi_overall=80)
    state.phase_status["strategy"] = PhaseStatus.COMPLETED
    state.phase_status["sqi"] = PhaseStatus.COMPLETED

    assert orchestrator.route_after_strategy(state) == "scoring"


def test_gauntlet_absence_is_safe_for_downstream_prompts():
    state = _state("gauntlet-absent")
    state.classify = _classify_output()
    state.hypotheses = _hypotheses(2)
    state.strategy = _strategy_output()

    audit_prompt = build_audit_prompt(state)
    strategy_prompt = build_strategy_prompt(state)

    assert "PHASE 2: Audit" in audit_prompt
    assert "PHASE 3: Generate STRATEGY PLAN" in strategy_prompt
    assert "GAUNTLET:" not in audit_prompt
    assert "GAUNTLET:" not in strategy_prompt


def test_gauntlet_phase_is_internal_and_not_a_delivery_approval_gate():
    state = _state("gauntlet-policy")
    decision = policy_gate(state, "gauntlet", PHASE_ACTION_MAP["gauntlet"])

    assert PHASE_ACTION_MAP["gauntlet"] == Reversibility.REVERSIBLE_INTERNAL
    assert decision.allowed is True
    assert decision.requires_hitl_approval is False
    assert decision.breach_category is None


def test_sqi_phase_preserves_weakest_link_shape_from_structured_output():
    state = _state("sqi-shape")
    state.strategy = _strategy_output()
    state.phase_status["strategy"] = PhaseStatus.COMPLETED
    state.phase_status["sqi"] = PhaseStatus.PENDING

    with patch("orchestrator.call_llm", new=AsyncMock(return_value=_fake_response(_sqi_payload()))):
        with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
            updated = asyncio.run(run_phase_node(state, "sqi"))

    assert updated.phase_status["sqi"] == PhaseStatus.COMPLETED
    assert updated.phase_confidence["sqi"] == 1.0
    assert updated.sqi is not None
    assert updated.sqi.sqi_overall == 42
    assert updated.sqi.weakest_link == "Evidence Quality"
    assert updated.sqi.dimensions[0].name == "Evidence Quality"
    assert "Collect baseline defect data" in updated.sqi.improvement_actions


def test_low_sqi_remains_advisory_and_does_not_block_or_approve_delivery():
    state = _state("sqi-advisory")
    state.sqi = SQIOutput(
        sqi_overall=22,
        dimensions=[SQIDimension(name="Evidence Quality", score=22, grade="F", finding="thin evidence")],
        weakest_link="Evidence Quality",
        improvement_actions=["Collect better evidence"],
    )

    workspace = build_workspace_summary(state)
    payload_text = json.dumps(workspace.model_dump(mode="json"), sort_keys=True).lower()

    assert workspace.score_summary.sqi_overall == 22
    assert workspace.blocking_reasons == []
    assert "sqi" not in " ".join(workspace.blocking_reasons).lower()
    for forbidden in (
        "delivery approval",
        "delivery_approved",
        "delivery_gate_passed",
        "safe_to_send",
    ):
        assert forbidden not in payload_text


def test_monitor_output_is_plan_structure_not_autonomous_live_monitoring():
    state = _state("monitor-plan")
    state.strategy = _strategy_output()
    state.monitor = _monitor_output()

    prompt = build_monitor_prompt(state)
    summary = summarize_phase_output("monitor", state)
    monitor_text = json.dumps(state.monitor.model_dump(mode="json"), sort_keys=True).lower()

    assert '"ooda_schedule"' in prompt
    assert '"circuit_breakers"' in prompt
    assert "practical monitoring plan" in prompt
    assert "CRITICAL STRATEGIES" in prompt
    assert "NEEDS_MONITORING TARGETS" in prompt
    assert "MONITOR:commitment=64" in summary
    assert "canaries=3" in summary
    assert "breakers=1" in summary
    for forbidden in (
        "autonomous",
        "auto_execute",
        "auto execute",
        "send email",
        "webhook",
        "public user",
    ):
        assert forbidden not in monitor_text


def test_monitor_gate_matches_existing_human_driven_config():
    state = _state("monitor-gate")
    state.monitor = _monitor_output()
    state.phase_confidence["monitor"] = 0.5

    gate = check_gate(state, "monitor")

    assert GATE_CONFIGS["monitor"].required_fields == []
    assert GATE_CONFIGS["monitor"].min_confidence == 0.5
    assert gate == {"passed": True, "blocking": [], "confidence": 0.5}


def test_policy_gate_reports_open_phase_breaker_without_auto_action():
    state = _state("breaker-policy")
    state.phase_breakers = {"monitor": {"state": BreakerState.OPEN.value, "failure_count": 3}}

    decision = policy_gate(state, "monitor", PHASE_ACTION_MAP["monitor"])

    assert decision.allowed is False
    assert decision.breach_category == "breaker"
    assert "phase circuit breaker OPEN for monitor" == decision.reason
    assert state.monitor is None
    assert state.phase_breakers["monitor"]["state"] == "open"


def test_workspace_and_readiness_surface_kill_failed_and_breaker_reasons():
    state = _state("blocking-reasons")
    state.kill_switch_active = True
    state.kill_switch_reason = "operator paused before client review"
    state.phase_status["monitor"] = PhaseStatus.FAILED
    state.budget_caps["max_consecutive_failures"] = 3
    state.budget_consumed["consecutive_failures"] = 3
    state.phase_breakers = {"strategy": {"state": "open", "failure_count": 3}}

    workspace = build_workspace_summary(state)
    readiness = build_delivery_review_readiness(
        state.project_id,
        state,
        workspace_summary=workspace,
    )

    expected_fragments = (
        "Kill switch active: operator paused before client review",
        "Phase monitor failed",
        "Budget circuit breaker open (3 consecutive failures)",
        "Phase breaker open: strategy",
    )
    for fragment in expected_fragments:
        assert any(fragment in reason for reason in workspace.blocking_reasons)
        assert any(fragment in reason for reason in readiness.blocking_reasons)
    assert workspace.project_status == "blocked"
    assert readiness.status == "blocked_for_review"
    assert readiness.source_signals["phase_state"]["status"] == "blocked"
