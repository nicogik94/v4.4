"""Tests for the additive decision-object layer."""
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from decision_objects import (  # noqa: E402
    build_decision_objects,
    compute_source_state_hash,
    ensure_decision_objects,
    mark_decision_objects_stale,
)
from state import (  # noqa: E402
    AuditOutput,
    ClassifyOutput,
    DecisionObjectStatus,
    DetScores,
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
    Prediction,
    PreliminaryVerdict,
    Priority,
    ProjectState,
    SQIDimension,
    SQIOutput,
    StrategyAction,
    StrategyOutput,
    Verdict,
)


def make_state(project_id: str = "decision-objects") -> ProjectState:
    state = ProjectState(project_id=project_id, project_name="UNAM", brief="A decision brief", data="Supporting data")
    state.classify = ClassifyOutput(
        domain="Complicated",
        justification="Editorial and technical levers are separable.",
        bf=42.0,
        variety_env="editorial, seo, dev",
        variety_sys="wordpress",
        variety_gaps="1. No search linkage",
        variety_decision="Amplify",
        ooda=OODALoop(observe="GSC", orient="brief backlog", decide="prioritize", act="optimize", freq="weekly"),
        dq=[20, 18, 16, 14],
    )
    state.hypotheses = [
        Hypothesis(
            id="H1",
            text="Search-driven briefs increase CTR",
            justification="High impressions but low CTR.",
            signal="CTR",
            alpha=6,
            beta=4,
            confirm="CTR up 15%",
            reject="CTR flat",
            evoi="high",
            portfolio_cluster="editorial",
            status="OPEN",
        ),
        Hypothesis(
            id="H2",
            text="Archive refresh improves ranking",
            justification="Old metadata is stale.",
            signal="Average position",
            alpha=5,
            beta=5,
            confirm="Position improves 2 spots",
            reject="No movement",
            evoi="medium",
            portfolio_cluster="archive",
            status="OPEN",
        ),
    ]
    state.gauntlet = GauntletOutput(
        results=[
            GauntletResult(
                id="H1",
                risk_rank=1,
                frameworks=[
                    {"fw": "STEELMAN", "finding": "Editorial workflow lacks query mapping", "action": True},
                    {"fw": "PREMORTEM", "finding": "Institutional review may delay tests", "action": True},
                ],
                crux="Brief template adoption is the main uncertainty",
                top_fmea={"mode": "slow review", "s": 5, "o": 4, "d": 5, "rpn": 100},
            )
        ],
        portfolio_correlation=0.25,
        mece_gaps="No internal linking hypothesis",
    )
    state.audit = AuditOutput(
        data_based=True,
        fmea=[FMEAItem(component="site", failure_mode="slow pages", effect="lower engagement", s=5, o=4, d=3, rpn=60, action="compress assets", evidence="CWV sample")],
        top_findings=["Refresh high-impression archive pages"],
        observation_needs=["Track CTR weekly"],
    )
    state.strategy = StrategyOutput(
        preliminary_verdicts=[
            PreliminaryVerdict(id="H1", verdict=Verdict.LIKELY_CONFIRMED, evidence="Strong intent mismatch"),
            PreliminaryVerdict(id="H2", verdict=Verdict.NEEDS_MONITORING, evidence="Needs a longer refresh window", monitoring_plan="Watch ranking for 4 weeks"),
        ],
        executive_strategy="Adopt search-driven editorial briefs and refresh archive pages first.",
        strategies=[
            StrategyAction(
                priority=Priority.CRITICAL,
                action="Launch search-driven brief template",
                justification="Editorial decisions need demand signals.",
                evidence_chain="H1 + audit finding + FMEA",
                expected_impact="Higher CTR",
                effort="Medium",
                timeline="2 weeks",
                risk_if_ignored="Traffic plateaus",
                framework_source="HDD",
            )
        ],
        success_metrics=["CTR up 15%"],
    )
    state.monitor = MonitorOutput(
        ooda_schedule=MonitorOODASchedule(
            daily=[MonitorScheduleItem(metric="CTR", owner="editor", source="GSC")],
            weekly=[MonitorScheduleItem(metric="Organic clicks", owner="seo", source="GA")],
        ),
        circuit_breakers=[MonitorCircuitBreaker(strategy_ref="S1", trip="CTR down 20%", reset="2 healthy weeks")],
        canaries=[MonitorCanary(signal="CTR", direction="up", window="7d", meaning="Headline lift")],
        commitment_score=81,
        commitment_rationale="Owners and windows are explicit.",
    )
    state.sqi = SQIOutput(
        sqi_overall=79,
        dimensions=[SQIDimension(name="Evidence Quality", score=79, grade="B", finding="grounded")],
    )
    state.det_scores = DetScores(overall=77, specificity=75, mece=70, evidence_linkage=85, consistency=80, actionability=75)
    state.brier_score = 0.12
    state.predictions = [
        Prediction(hypothesis_id="H1", predicted_probability=0.7, actual_outcome=True, phase="strategy", framework_used="HDD"),
    ]
    return state


class TestDecisionObjects(unittest.TestCase):
    def test_project_state_loads_without_decision_objects_field(self):
        state = make_state("legacy")
        payload = state.model_dump(mode="json")
        payload.pop("decision_objects", None)

        loaded = ProjectState.model_validate(payload)

        self.assertIsNone(loaded.decision_objects)

    def test_build_populates_links_and_provenance(self):
        state = make_state("build-links")

        decision_objects = build_decision_objects(state)

        self.assertEqual(decision_objects.status, DecisionObjectStatus.FRESH)
        self.assertTrue(decision_objects.schema_version)
        self.assertTrue(decision_objects.rebuilt_at)
        self.assertTrue(decision_objects.source_state_hash)
        self.assertIsNotNone(decision_objects.primary_decision)
        self.assertEqual(decision_objects.primary_decision.hypothesis_ids, ["H1", "H2"])
        self.assertGreaterEqual(len(decision_objects.risks), 1)
        self.assertGreaterEqual(len(decision_objects.evidences), 1)
        self.assertGreaterEqual(len(decision_objects.signals), 1)
        self.assertGreaterEqual(len(decision_objects.actions), 1)
        self.assertTrue(state.hypotheses[0].evidence_ids)
        for evidence in decision_objects.evidences:
            self.assertTrue(evidence.provenance.source_type)
            self.assertTrue(evidence.provenance.source_ref)
            self.assertTrue(evidence.provenance.captured_at)
            self.assertTrue(evidence.provenance.captured_by)
        for signal in decision_objects.signals:
            self.assertTrue(signal.provenance.source_type)
            self.assertTrue(signal.provenance.source_ref)

    def test_stable_ids_hold_across_equivalent_rebuilds(self):
        state = make_state("stable-ids")

        first = build_decision_objects(state)
        second = build_decision_objects(state)

        self.assertEqual(first.source_state_hash, second.source_state_hash)
        self.assertEqual(first.primary_decision.decision_id, second.primary_decision.decision_id)
        self.assertEqual([item.risk_id for item in first.risks], [item.risk_id for item in second.risks])
        self.assertEqual([item.evidence_id for item in first.evidences], [item.evidence_id for item in second.evidences])
        self.assertEqual([item.signal_id for item in first.signals], [item.signal_id for item in second.signals])
        self.assertEqual([item.action_id for item in first.actions], [item.action_id for item in second.actions])

    def test_hash_changes_when_source_state_changes(self):
        state = make_state("hash-change")
        before = compute_source_state_hash(state)
        state.brief = "Updated brief"
        after = compute_source_state_hash(state)

        self.assertNotEqual(before, after)

    def test_mark_stale_and_rebuild_refreshes_status(self):
        state = make_state("freshness")
        ensure_decision_objects(state, trigger="test")
        original_hash = state.decision_objects.source_state_hash

        state.data = "Updated data"
        mark_decision_objects_stale(state, "operator update")
        self.assertEqual(state.decision_objects.status, DecisionObjectStatus.STALE)

        ensure_decision_objects(state, trigger="test-refresh")
        self.assertEqual(state.decision_objects.status, DecisionObjectStatus.FRESH)
        self.assertNotEqual(state.decision_objects.source_state_hash, original_hash)

    def test_rebuild_failure_sets_rebuild_failed_status(self):
        state = make_state("rebuild-failure")
        state.gauntlet = type("BrokenGauntlet", (), {"results": ["oops"]})()

        result = ensure_decision_objects(state, trigger="test-failure")

        self.assertEqual(result.status, DecisionObjectStatus.REBUILD_FAILED)
        self.assertTrue(result.rebuild_error)

    def test_operator_edit_switches_provenance_type(self):
        state = make_state("operator-edit")
        state.policy_audit_log.append(
            {
                "ts": 1776000000.0,
                "event_type": "operator_state_edit",
                "phase": "hypotheses",
                "details": {"section": "hypotheses", "edited_by": "operator"},
            }
        )

        result = build_decision_objects(state)
        hypothesis_signal = next(signal for signal in result.signals if signal.source_phase == "hypotheses")

        self.assertEqual(hypothesis_signal.provenance.source_type, "operator_edit")
        self.assertEqual(hypothesis_signal.provenance.captured_by, "operator")
