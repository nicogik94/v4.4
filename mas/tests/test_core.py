"""
v4 Multi-Agent System — Test Suite
Tests for deterministic components: gates, invalidation, scoring, Bayesian math.
These do NOT call LLMs — they verify the orchestration logic.
"""
import pytest
import sys
sys.path.insert(0, "..")

from state import (
    ProjectState, ClassifyOutput, Hypothesis, GauntletOutput,
    GauntletResult, AuditOutput, FMEAItem, StrategyOutput,
    StrategyAction, PreliminaryVerdict, SQIOutput, SQIDimension,
    DetScores, Prediction, PhaseStatus, Verdict, Priority, OODALoop
)
from tools.scoring import (
    check_gate, evaluate_reentry_triggers, invalidate_downstream,
    compute_det_scores, compute_posterior, compute_bayes_factor,
    compute_brier_score, compute_ece, summarize_phase_output
)
from config import GATE_CONFIGS, INVALIDATION_MAP, FRAMEWORKS_BY_PHASE


# ═══ FIXTURES ═══

@pytest.fixture
def empty_state():
    return ProjectState(project_id="test-001", project_name="Test", brief="Test brief")


@pytest.fixture
def classified_state():
    s = ProjectState(project_id="test-002", project_name="Test", brief="Test project")
    s.classify = ClassifyOutput(
        domain="Complicated", justification="Test", bf=85.0,
        variety_env="env", variety_sys="sys",
        variety_gaps="1. Gap one. 2. Gap two.",
        variety_decision="Amplify",
        ooda=OODALoop(observe="obs", orient="ori", decide="dec", act="act", freq="Weekly"),
        dq=[20, 15, 18, 12],
    )
    return s


@pytest.fixture
def hypothesized_state(classified_state):
    s = classified_state
    s.hypotheses = [
        Hypothesis(id="H1", text="Hypothesis 1", alpha=6, beta=4, confirm=">80%", reject="<50%", evoi="high", portfolio_cluster="speed"),
        Hypothesis(id="H2", text="Hypothesis 2", alpha=5, beta=5, confirm=">70%", reject="<30%", evoi="medium", portfolio_cluster="accuracy"),
        Hypothesis(id="H3", text="Hypothesis 3", alpha=3, beta=7, confirm=">60%", reject="<40%", evoi="high", portfolio_cluster="retention"),
    ]
    s.sealed = True
    s.seal_date = "2026-04-01"
    return s


@pytest.fixture
def audited_state(hypothesized_state):
    s = hypothesized_state
    s.gauntlet = GauntletOutput(
        results=[GauntletResult(id="H3", risk_rank=1, crux="testable crux")],
        portfolio_correlation=0.32,
    )
    s.audit = AuditOutput(
        data_based=False,
        fmea=[
            FMEAItem(component="Dashboard", failure_mode="No threshold", s=7, o=6, d=4, rpn=168, action="Add indicator"),
            FMEAItem(component="Training", failure_mode="Single session", s=6, o=8, d=5, rpn=240, action="Add refresher"),
        ],
        top_findings=["Dashboard lacks threshold indicator", "Training retention insufficient"],
    )
    return s


@pytest.fixture
def strategy_state(audited_state):
    s = audited_state
    s.strategy = StrategyOutput(
        preliminary_verdicts=[
            PreliminaryVerdict(id="H1", verdict=Verdict.LIKELY_CONFIRMED, evidence="80% success"),
            PreliminaryVerdict(id="H2", verdict=Verdict.NEEDS_MONITORING, evidence="insufficient data"),
            PreliminaryVerdict(id="H3", verdict=Verdict.LIKELY_REJECTED, evidence="only 20% retention"),
        ],
        executive_strategy="Focus on dashboard UX and training refreshers.",
        strategies=[
            StrategyAction(priority=Priority.CRITICAL, action="Add READY/NOT READY indicator",
                           justification="H3 crux: operators can't interpret raw %",
                           evidence_chain="H3 P=30% + FMEA RPN=168 + crux → add indicator",
                           expected_impact="70%+ correct decisions", effort="High", timeline="2 weeks",
                           risk_if_ignored="Wrong harvest timing", framework_source="FMEA[#7]"),
            StrategyAction(priority=Priority.HIGH, action="Implement 48-hour refresher training protocol",
                           justification="FMEA RPN=240: single session insufficient for motor memory",
                           evidence_chain="H1 + FMEA RPN=240 → add refresher at 48h interval",
                           expected_impact="85%+ retention at 7 days", effort="Medium", timeline="1 week",
                           risk_if_ignored="Skills decay within 48 hours", framework_source="PREMORTEM[#2]"),
        ],
        success_metrics=["70%+ correct harvest decisions in <30s", "85%+ 7-day retention", "Median calibration time <3 min"],
    )
    return s


# ═══ GATE TESTS ═══

class TestGates:
    def test_classify_gate_passes(self, classified_state):
        classified_state.phase_confidence["classify"] = 0.8
        result = check_gate(classified_state, "classify")
        assert result["passed"] is True
        assert result["blocking"] == []

    def test_classify_gate_fails_low_bf(self, empty_state):
        empty_state.classify = ClassifyOutput(domain="Complex", bf=5.0, variety_gaps="gap", dq=[10, 10, 10, 10])
        empty_state.phase_confidence["classify"] = 0.8
        result = check_gate(empty_state, "classify")
        assert result["passed"] is False
        assert any("BF" in b for b in result["blocking"])

    def test_classify_gate_fails_low_dq(self, empty_state):
        empty_state.classify = ClassifyOutput(domain="Complex", bf=20.0, variety_gaps="gap", dq=[10, 10, 10, 10])
        empty_state.phase_confidence["classify"] = 0.8
        result = check_gate(empty_state, "classify")
        assert result["passed"] is False
        assert any("DQ" in b for b in result["blocking"])

    def test_classify_gate_fails_no_gaps(self, empty_state):
        empty_state.classify = ClassifyOutput(domain="Complex", bf=20.0, variety_gaps="", dq=[20, 20, 20, 20])
        empty_state.phase_confidence["classify"] = 0.8
        result = check_gate(empty_state, "classify")
        assert result["passed"] is False

    def test_hypotheses_gate_needs_sealed(self, hypothesized_state):
        hypothesized_state.sealed = False
        hypothesized_state.phase_confidence["hypotheses"] = 0.8
        result = check_gate(hypothesized_state, "hypotheses")
        assert result["passed"] is False
        assert any("sealed" in b.lower() for b in result["blocking"])

    def test_hypotheses_gate_needs_minimum_3(self, classified_state):
        classified_state.hypotheses = [Hypothesis(id="H1", text="only one")]
        classified_state.sealed = True
        classified_state.phase_confidence["hypotheses"] = 0.8
        result = check_gate(classified_state, "hypotheses")
        assert result["passed"] is False

    def test_audit_gate_needs_fmea(self, hypothesized_state):
        hypothesized_state.audit = AuditOutput(fmea=[], top_findings=["finding"])
        hypothesized_state.phase_confidence["audit"] = 0.8
        result = check_gate(hypothesized_state, "audit")
        assert result["passed"] is False

    def test_empty_phase_fails(self, empty_state):
        result = check_gate(empty_state, "classify")
        assert result["passed"] is False


# ═══ INVALIDATION TESTS ═══

class TestInvalidation:
    def test_classify_invalidates_all_downstream(self, strategy_state):
        invalidated = invalidate_downstream(strategy_state, "classify")
        assert "hypotheses" in invalidated
        assert "strategy" in invalidated
        assert strategy_state.hypotheses is None
        assert strategy_state.strategy is None

    def test_audit_invalidates_strategy(self, strategy_state):
        invalidated = invalidate_downstream(strategy_state, "audit")
        assert "strategy" in invalidated
        assert strategy_state.strategy is None
        # But hypotheses should be preserved
        assert strategy_state.hypotheses is not None

    def test_report_invalidates_nothing(self, strategy_state):
        invalidated = invalidate_downstream(strategy_state, "report")
        assert invalidated == []

    def test_invalidation_map_complete(self):
        """Every phase in INVALIDATION_MAP should be a valid phase."""
        all_phases = {"classify", "hypotheses", "gauntlet", "audit", "strategy", "sqi", "monitor", "report"}
        for source, targets in INVALIDATION_MAP.items():
            assert source in all_phases, f"Unknown source: {source}"
            for t in targets:
                assert t in all_phases, f"Unknown target: {t}"


# ═══ SCORING TESTS ═══

class TestDetScores:
    def test_computes_from_strategy(self, strategy_state):
        scores = compute_det_scores(strategy_state.strategy)
        assert scores is not None
        assert 0 <= scores.overall <= 100
        assert 0 <= scores.specificity <= 100
        assert 0 <= scores.mece <= 100
        assert 0 <= scores.evidence_linkage <= 100
        assert 0 <= scores.consistency <= 100
        assert 0 <= scores.actionability <= 100

    def test_evidence_linkage_detects_framework_references(self, strategy_state):
        scores = compute_det_scores(strategy_state.strategy)
        # Both strategies reference H_ and FMEA RPN
        assert scores.evidence_linkage > 50

    def test_consistency_detects_no_contradictions(self, strategy_state):
        scores = compute_det_scores(strategy_state.strategy)
        assert scores.consistency == 100
        assert scores.contradictions == []

    def test_none_strategy_returns_none(self):
        assert compute_det_scores(None) is None

    def test_empty_strategy_returns_none(self):
        assert compute_det_scores(StrategyOutput()) is None


# ═══ BAYESIAN TESTS ═══

class TestBayesian:
    def test_posterior_update(self):
        alpha, beta = compute_posterior(3, 7, successes=5, failures=2)
        assert alpha == 8
        assert beta == 9

    def test_bayes_factor_strong(self):
        bf = compute_bayes_factor(9, 1)  # P = 0.9
        assert bf == 9.0

    def test_bayes_factor_weak(self):
        bf = compute_bayes_factor(5, 5)  # P = 0.5
        assert bf == 1.0

    def test_brier_score_perfect(self):
        preds = [
            Prediction(hypothesis_id="H1", predicted_probability=1.0, actual_outcome=True, phase="classify"),
            Prediction(hypothesis_id="H2", predicted_probability=0.0, actual_outcome=False, phase="classify"),
        ]
        assert compute_brier_score(preds) == 0.0

    def test_brier_score_random(self):
        preds = [
            Prediction(hypothesis_id="H1", predicted_probability=0.5, actual_outcome=True, phase="classify"),
            Prediction(hypothesis_id="H2", predicted_probability=0.5, actual_outcome=False, phase="classify"),
        ]
        assert compute_brier_score(preds) == 0.25

    def test_brier_score_no_data(self):
        assert compute_brier_score([]) is None

    def test_ece_perfect_calibration(self):
        # All predictions at 1.0 with outcome True = perfect calibration
        preds = [Prediction(hypothesis_id=f"H{i}", predicted_probability=1.0, actual_outcome=True, phase="") for i in range(10)]
        ece = compute_ece(preds)
        assert ece is not None
        assert ece < 0.01


# ═══ RE-ENTRY TRIGGER TESTS ═══

class TestReentryTriggers:
    def test_r4_fires_on_high_correlation(self, hypothesized_state):
        hypothesized_state.gauntlet = GauntletOutput(portfolio_correlation=0.7, results=[])
        triggers = evaluate_reentry_triggers(hypothesized_state)
        r4 = [t for t in triggers if t.get("condition") == "portfolio_rho_gt_05"]
        assert len(r4) == 1

    def test_r5_fires_when_all_rejected(self, strategy_state):
        for v in strategy_state.strategy.preliminary_verdicts:
            v.verdict = Verdict.LIKELY_REJECTED
        triggers = evaluate_reentry_triggers(strategy_state)
        r5 = [t for t in triggers if t.get("condition") == "all_hypotheses_futile"]
        assert len(r5) == 1

    def test_r6_fires_when_majority_rejected(self, strategy_state):
        strategy_state.strategy.preliminary_verdicts[0].verdict = Verdict.LIKELY_REJECTED
        strategy_state.strategy.preliminary_verdicts[1].verdict = Verdict.LIKELY_REJECTED
        # 2/3 rejected = 67% > 50%
        triggers = evaluate_reentry_triggers(strategy_state)
        r6 = [t for t in triggers if t.get("condition") == "majority_futile"]
        assert len(r6) == 1

    def test_no_triggers_on_healthy_state(self, strategy_state):
        strategy_state.gauntlet.portfolio_correlation = 0.3
        triggers = evaluate_reentry_triggers(strategy_state)
        assert len(triggers) == 0


# ═══ CONTEXT SUMMARIZER TESTS ═══

class TestSummarizer:
    def test_classify_summary(self, classified_state):
        summary = summarize_phase_output("classify", classified_state)
        assert "DOMAIN:Complicated" in summary
        assert "BF=" in summary

    def test_hypotheses_summary(self, hypothesized_state):
        summary = summarize_phase_output("hypotheses", hypothesized_state)
        assert "H1" in summary
        assert "P=" in summary

    def test_empty_phase_returns_empty(self, empty_state):
        summary = summarize_phase_output("classify", empty_state)
        assert summary == ""


# ═══ FRAMEWORK DISTRIBUTION TESTS ═══

class TestFrameworkDistribution:
    def test_all_30_frameworks_assigned(self):
        all_assigned = set()
        for frameworks in FRAMEWORKS_BY_PHASE.values():
            for fw in frameworks:
                # Extract tag number
                tag = fw.split("]")[0] + "]"
                all_assigned.add(tag)
        for i in range(1, 31):
            assert f"[#{i}]" in all_assigned, f"Framework [#{i}] not assigned to any phase"

    def test_no_phase_has_more_than_10(self):
        for phase, frameworks in FRAMEWORKS_BY_PHASE.items():
            assert len(frameworks) <= 10, f"{phase} has {len(frameworks)} frameworks (max 10)"


# ═══ RUN ═══

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
