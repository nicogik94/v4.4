"""V4.4 pilot integrity P0-1/2/3 — deterministic checks over structured state.

Each test states the observed defect it protects against. Nothing here asserts
anything about natural-language matching: the checks read declared integers,
declared identifiers and declared enums, so these tests do too.

Deterministic and offline: no provider, no network, no database.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import decision_integrity as di  # noqa: E402
from delivery_readiness import build_delivery_review_readiness  # noqa: E402
from monitoring_templates import build_monitoring_template_rows  # noqa: E402
from orchestrator import build_monitor_prompt, build_report_prompt  # noqa: E402
from state import (  # noqa: E402
    Evidence,
    KnowledgeItem,
    KnowledgeLayerState,
    MeasurableCriterion,
    MonitorCanary,
    MonitorCircuitBreaker,
    MonitorOutput,
    PhaseStatus,
    Priority,
    ProjectState,
    RumeltTest,
    SQIDimension,
    SQIOutput,
    StrategyAction,
    StrategyOutput,
    ThresholdDerivation,
    ThresholdProvenance,
)
from tools.scoring import summarize_phase_output  # noqa: E402


WARM_CONTACT_OBJECTION = (
    "Validating demand through warm contacts confounds actual willingness to pay "
    "with social reciprocity, so a positive signal cannot establish real demand."
)
UNSUPPORTED_TRIP = "Stop the pilot if economic viability falls below $800 per project."

SILENT_REPORT = (
    "# Executive Summary\n"
    "We recommend a bounded demand-validation pilot.\n\n"
    "# Recommended Path\n"
    "Interview five warm introductions and close three of them at list price.\n\n"
    "# Appendix: Technical Analysis\n"
    "Diagnostics are recorded in the machine archive.\n"
)


def _state(*, report: str = SILENT_REPORT) -> ProjectState:
    state = ProjectState(
        project_id="pilot-integrity",
        project_name="Bounded validation pilot",
        brief="Decide whether to run a bounded validation pilot.",
    )
    state.strategy = StrategyOutput(
        executive_strategy="Run a bounded demand-validation pilot.",
        strategies=[StrategyAction(priority=Priority.CRITICAL, action="Interview warm introductions.")],
        success_metrics=["Three paid commitments inside four weeks"],
        monitoring_plan="Weekly review.",
        review_date="2026-09-01",
        confidence="medium",
        reentry_check="none",
    )
    state.sqi = SQIOutput(
        sqi_overall=61.0,
        dimensions=[
            SQIDimension(name="Evidence Quality", score=38.0, grade="F", finding=WARM_CONTACT_OBJECTION),
            SQIDimension(name="Specificity", score=82.0, grade="B", finding="Actions name owners and dates."),
        ],
        weakest_link=WARM_CONTACT_OBJECTION,
    )
    state.report = report
    for phase in ("classify", "hypotheses", "gauntlet", "audit", "strategy", "sqi", "monitor", "report"):
        state.phase_status[phase] = PhaseStatus.COMPLETED
    return state


# ══════════════════════════════════════════════════════════════════════════
# P0-1
# ══════════════════════════════════════════════════════════════════════════


class TestMaterialSQIFindings(unittest.TestCase):
    def test_weakest_link_and_failing_dimension_are_material(self):
        kinds = {f.kind for f in di.material_sqi_findings(_state())}
        self.assertIn(di.KIND_WEAKEST_LINK, kinds)
        self.assertIn(di.KIND_DIMENSION_FAILURE, kinds)

    def test_passing_dimension_is_not_material(self):
        statements = " ".join(f.statement for f in di.material_sqi_findings(_state()))
        self.assertNotIn("Actions name owners and dates", statements)

    def test_default_rumelt_test_is_absent_data_not_failure(self):
        """``RumeltTest`` defaults every sub-test to ``pass=False``."""
        state = _state()
        state.sqi = SQIOutput(sqi_overall=75.0)
        self.assertEqual(di.material_sqi_findings(state), ())

    def test_explicit_rumelt_failure_with_a_note_is_material(self):
        state = _state()
        state.sqi.rumelt_test = RumeltTest(
            consistency={"pass": True, "note": ""},
            consonance={"pass": False, "note": "The pilot ignores the stated capacity limit."},
            advantage={"pass": True, "note": ""},
            feasibility={"pass": True, "note": ""},
        )
        rumelt = [f for f in di.material_sqi_findings(state) if f.kind == di.KIND_RUMELT_FAILURE]
        self.assertEqual(len(rumelt), 1)
        self.assertIn("capacity limit", rumelt[0].statement)

    def test_opposite_test_that_is_not_stupid_is_material(self):
        state = _state()
        state.sqi.opposite_test = [
            {"strategy": "Warm contacts", "opposite": "Cold prospects", "is_stupid": False, "verdict": "defensible"}
        ]
        self.assertIn(di.KIND_OPPOSITE_TEST, {f.kind for f in di.material_sqi_findings(state)})

    def test_no_sqi_output_yields_no_findings(self):
        state = _state()
        state.sqi = None
        self.assertEqual(di.material_sqi_findings(state), ())


class TestSQIObjectionsNeverDisappear(unittest.TestCase):
    """The observed defect: the objection was absent from the final report."""

    def test_material_objection_reaches_the_final_report(self):
        state = _state()
        self.assertNotIn("reciprocity", (state.report or "").lower())
        di.apply_decision_integrity(state)
        self.assertIn("reciprocity", (state.report or "").lower())
        self.assertIn(di.SURFACED_SECTION_MARKER, state.report)

    def test_surfacing_is_unconditional_not_inferred_from_report_prose(self):
        """A report that already discusses the objection is still surfaced."""
        state = _state(report=SILENT_REPORT + "\nWarm contacts confound reciprocity with demand.\n")
        di.apply_decision_integrity(state)
        self.assertIn(di.SURFACED_SECTION_MARKER, state.report)

    def test_a_revised_sqi_output_removes_the_finding(self):
        """The structured resolution: SQI re-runs and no longer objects."""
        state = _state()
        state.sqi = SQIOutput(sqi_overall=88.0, dimensions=[SQIDimension(name="Evidence Quality", score=90.0, grade="A")])
        report = di.apply_decision_integrity(state)
        self.assertEqual(report.sqi_findings, ())
        self.assertNotIn(di.SURFACED_SECTION_MARKER, state.report)

    def test_apply_is_idempotent(self):
        state = _state()
        di.apply_decision_integrity(state)
        first = state.report
        di.apply_decision_integrity(state)
        self.assertEqual(state.report, first)
        self.assertEqual(state.report.count(di.SURFACED_SECTION_MARKER), 1)

    def test_surfacing_is_recorded_in_the_policy_audit_log(self):
        state = _state()
        di.apply_decision_integrity(state)
        self.assertIn(
            "decision_integrity_surfaced",
            [event.get("event_type") for event in state.policy_audit_log],
        )

    def test_spanish_report_uses_spanish_headings(self):
        state = _state()
        state.report_output_language = "es-MX"
        di.apply_decision_integrity(state)
        self.assertIn("Objeciones", state.report)


class TestSQIReachesDownstreamPrompts(unittest.TestCase):
    """The structural root cause: SQI reached no downstream prompt at all."""

    def test_phase_summary_includes_material_objections(self):
        summary = summarize_phase_output("sqi", _state())
        self.assertIn("reciprocity", summary.lower())

    def test_report_prompt_includes_material_objections(self):
        self.assertIn("reciprocity", build_report_prompt(_state()).lower())

    def test_monitor_prompt_includes_material_objections(self):
        self.assertIn("reciprocity", build_monitor_prompt(_state()).lower())


# ══════════════════════════════════════════════════════════════════════════
# P0-2
# ══════════════════════════════════════════════════════════════════════════


class TestMeasurableCriteria(unittest.TestCase):
    def _with_criteria(self, *criteria: MeasurableCriterion) -> ProjectState:
        state = _state()
        state.strategy.success_metrics = []
        state.strategy.measurable_criteria = list(criteria)
        return state

    def test_observed_defect_three_successes_from_two_eligible_observations(self):
        state = self._with_criteria(
            MeasurableCriterion(
                criterion_id="C1",
                statement="At least 3 of 5 prospects accept the price point.",
                required_successes=3,
                population=5,
                eligible_observations=2,
                hard_gate=True,
            )
        )
        assessed = di.assess_measurable_criteria(state)
        self.assertEqual([c.status for c in assessed], [di.STATUS_IMPOSSIBLE])
        self.assertEqual(assessed[0].code, di.CODE_REQUIRED_EXCEEDS_ELIGIBLE)

    def test_the_same_criterion_is_feasible_with_enough_observations(self):
        state = self._with_criteria(
            MeasurableCriterion(required_successes=3, population=5, eligible_observations=5)
        )
        self.assertEqual([c.status for c in di.assess_measurable_criteria(state)], [di.STATUS_FEASIBLE])

    def test_required_exceeds_population(self):
        state = self._with_criteria(MeasurableCriterion(required_successes=7, population=5))
        assessed = di.assess_measurable_criteria(state)
        self.assertEqual(assessed[0].code, di.CODE_REQUIRED_EXCEEDS_POPULATION)

    def test_eligible_exceeds_population_is_an_incompatible_denominator(self):
        state = self._with_criteria(
            MeasurableCriterion(required_successes=2, population=5, eligible_observations=8)
        )
        assessed = di.assess_measurable_criteria(state)
        self.assertEqual(assessed[0].code, di.CODE_ELIGIBLE_EXCEEDS_POPULATION)

    def test_deadline_makes_the_target_unreachable(self):
        state = self._with_criteria(
            MeasurableCriterion(required_successes=3, population=5, eligible_observations=5, observations_by_deadline=2)
        )
        assessed = di.assess_measurable_criteria(state)
        self.assertEqual(assessed[0].code, di.CODE_DEADLINE_UNREACHABLE)

    def test_every_criterion_ends_in_one_of_three_explicit_states(self):
        state = self._with_criteria(
            MeasurableCriterion(required_successes=3, population=5, eligible_observations=2),
            MeasurableCriterion(required_successes=2, population=5),
            MeasurableCriterion(statement="Customers are happier."),
        )
        statuses = [c.status for c in di.assess_measurable_criteria(state)]
        self.assertEqual(statuses, [di.STATUS_IMPOSSIBLE, di.STATUS_FEASIBLE, di.STATUS_NOT_MACHINE_CHECKABLE])
        self.assertTrue(set(statuses) <= set(di.CRITERION_STATUSES))

    def test_a_criterion_with_no_counts_is_not_machine_checkable_not_feasible(self):
        state = self._with_criteria(MeasurableCriterion(statement="Retention improves.", hard_gate=True))
        assessed = di.assess_measurable_criteria(state)
        self.assertEqual(assessed[0].status, di.STATUS_NOT_MACHINE_CHECKABLE)
        self.assertNotEqual(assessed[0].status, di.STATUS_FEASIBLE)

    def test_legacy_prose_metric_ratio_is_read_but_nothing_else_is(self):
        state = _state()
        state.strategy.measurable_criteria = []
        state.strategy.success_metrics = [
            "At least 9 of 6 pilot accounts renew.",
            "Customers report a smoother onboarding experience.",
        ]
        assessed = di.assess_measurable_criteria(state)
        self.assertEqual(assessed[0].status, di.STATUS_IMPOSSIBLE)
        self.assertEqual(assessed[0].code, di.CODE_REQUIRED_EXCEEDS_POPULATION)
        self.assertEqual(assessed[1].status, di.STATUS_NOT_MACHINE_CHECKABLE)

    def test_legacy_prose_metrics_are_never_promoted_to_hard_gates(self):
        state = _state()
        state.strategy.success_metrics = ["At least 3 of 5 accounts renew."]
        self.assertFalse(any(c.hard_gate for c in di.assess_measurable_criteria(state)))


class TestCriteriaGates(unittest.TestCase):
    def test_impossible_criterion_blocks_delivery_review(self):
        state = _state()
        state.strategy.measurable_criteria = [
            MeasurableCriterion(statement="3 of 5", required_successes=3, eligible_observations=2, hard_gate=True)
        ]
        readiness = build_delivery_review_readiness(state.project_id, state)
        self.assertEqual(readiness.status, "blocked_for_review")
        self.assertTrue(any("Impossible measurable criterion" in r for r in readiness.blocking_reasons))

    def test_unchecked_hard_gate_requires_review_and_is_not_ignored(self):
        state = _state()
        state.strategy.success_metrics = []
        state.strategy.measurable_criteria = [
            MeasurableCriterion(statement="Retention improves.", hard_gate=True)
        ]
        readiness = build_delivery_review_readiness(state.project_id, state)
        self.assertTrue(
            any("could not be checked automatically" in w for w in readiness.review_warnings),
            readiness.review_warnings,
        )
        self.assertNotEqual(readiness.status, "ready_for_human_review")

    def test_impossible_criterion_is_surfaced_on_the_report(self):
        state = _state()
        state.strategy.measurable_criteria = [
            MeasurableCriterion(statement="3 of 5 prospects", required_successes=3, eligible_observations=2)
        ]
        di.apply_decision_integrity(state)
        self.assertIn("only 2 eligible observation(s)", state.report)

    def test_certification_rejects_an_impossible_criterion(self):
        state = _state()
        state.strategy.measurable_criteria = [
            MeasurableCriterion(required_successes=3, eligible_observations=2)
        ]
        with self.assertRaises(di.DecisionIntegrityError):
            di.require_decision_integrity(state)

    def test_impossible_hard_gate_still_blocks_delivery_review(self):
        state = _state()
        state.strategy.success_metrics = []
        state.strategy.measurable_criteria = [
            MeasurableCriterion(
                statement="3 of 5 prospects", required_successes=3, eligible_observations=2, hard_gate=True
            )
        ]
        readiness = build_delivery_review_readiness(state.project_id, state)
        self.assertEqual(readiness.status, "blocked_for_review")

    def test_unevaluable_hard_gate_cannot_silently_pass_certification(self):
        """A gate the system cannot evaluate is not certifiable."""
        state = _state()
        state.strategy.success_metrics = []
        state.strategy.measurable_criteria = [
            MeasurableCriterion(statement="Retention improves.", hard_gate=True)
        ]
        self.assertEqual(
            [c.status for c in di.assess_measurable_criteria(state)],
            [di.STATUS_NOT_MACHINE_CHECKABLE],
        )
        with self.assertRaises(di.DecisionIntegrityError) as caught:
            di.require_decision_integrity(state)
        self.assertIn("not machine-checkable", str(caught.exception))

    def test_unevaluable_advisory_criterion_does_not_block_certification(self):
        """Nothing is gating on it, so it is reported and does not fail closed."""
        state = _state()
        state.strategy.success_metrics = []
        state.strategy.measurable_criteria = [
            MeasurableCriterion(statement="Retention improves.", hard_gate=False)
        ]
        state.monitor = MonitorOutput()
        self.assertEqual(
            [c.status for c in di.assess_measurable_criteria(state)],
            [di.STATUS_NOT_MACHINE_CHECKABLE],
        )
        di.require_decision_integrity(state)  # must not raise

    def test_unevaluable_advisory_criterion_does_not_block_delivery_review(self):
        state = _state()
        state.strategy.success_metrics = []
        state.strategy.measurable_criteria = [
            MeasurableCriterion(statement="Retention improves.", hard_gate=False)
        ]
        readiness = build_delivery_review_readiness(state.project_id, state)
        self.assertNotEqual(readiness.status, "blocked_for_review")
        self.assertFalse(
            any("could not be checked automatically" in w for w in readiness.review_warnings)
        )


# ══════════════════════════════════════════════════════════════════════════
# P0-3
# ══════════════════════════════════════════════════════════════════════════


def _monitor_state(provenance: ThresholdProvenance | None = None, **kwargs) -> ProjectState:
    state = _state(**kwargs)
    state.monitor = MonitorOutput(
        circuit_breakers=[
            MonitorCircuitBreaker(
                strategy_ref="Pilot economics",
                trip=UNSUPPORTED_TRIP,
                reset="Two profitable projects.",
                threshold_provenance=provenance,
            )
        ]
    )
    return state


class TestThresholdProvenance(unittest.TestCase):
    def test_no_declared_provenance_is_unsupported(self):
        state = _monitor_state()
        cls, reason = di.classify_control_provenance(state.monitor.circuit_breakers[0], state)
        self.assertEqual(cls, di.PROVENANCE_UNSUPPORTED)
        self.assertIn("No threshold provenance", reason)

    def test_numeric_coincidence_in_the_brief_does_not_confer_provenance(self):
        """The whole point: the same digits elsewhere prove nothing."""
        state = _monitor_state()
        state.brief = "We never take projects below $800 in gross margin."
        cls, _ = di.classify_control_provenance(state.monitor.circuit_breakers[0], state)
        self.assertEqual(cls, di.PROVENANCE_UNSUPPORTED)

    def test_operator_stated_requires_an_operator_reference(self):
        bare = _monitor_state(ThresholdProvenance(provenance_class="operator_stated"))
        self.assertEqual(
            di.classify_control_provenance(bare.monitor.circuit_breakers[0], bare)[0],
            di.PROVENANCE_UNSUPPORTED,
        )
        good = _monitor_state(
            ThresholdProvenance(provenance_class="operator_stated", operator_reference="Operator floor: $800/project")
        )
        self.assertEqual(
            di.classify_control_provenance(good.monitor.circuit_breakers[0], good)[0],
            di.PROVENANCE_OPERATOR_STATED,
        )

    def test_evidence_backed_requires_an_id_that_resolves(self):
        """A citation that resolves to nothing is a fabrication, not a gap."""
        state = _monitor_state(ThresholdProvenance(provenance_class="source_evidence_backed", evidence_id="ev-1"))
        self.assertEqual(
            di.classify_control_provenance(state.monitor.circuit_breakers[0], state)[0],
            di.PROVENANCE_UNSUPPORTED,
        )

    def test_derivation_must_recompute(self):
        wrong = _monitor_state(
            ThresholdProvenance(
                provenance_class="reproducible_derived",
                derivation=ThresholdDerivation(inputs=[40.0, 20.0], operation="product", result=900.0),
            )
        )
        self.assertEqual(
            di.classify_control_provenance(wrong.monitor.circuit_breakers[0], wrong)[0],
            di.PROVENANCE_UNSUPPORTED,
        )
        right = _monitor_state(
            ThresholdProvenance(
                provenance_class="reproducible_derived",
                derivation=ThresholdDerivation(inputs=[40.0, 20.0], operation="product", result=800.0),
            )
        )
        self.assertEqual(
            di.classify_control_provenance(right.monitor.circuit_breakers[0], right)[0],
            di.PROVENANCE_REPRODUCIBLE_DERIVED,
        )

    def test_unrecognised_class_is_unsupported(self):
        state = _monitor_state(ThresholdProvenance(provenance_class="vibes"))
        self.assertEqual(
            di.classify_control_provenance(state.monitor.circuit_breakers[0], state)[0],
            di.PROVENANCE_UNSUPPORTED,
        )


def _knowledge_state(provenance: ThresholdProvenance, payload: dict) -> ProjectState:
    state = _monitor_state(provenance)
    state.knowledge_layer = KnowledgeLayerState(
        items=[
            KnowledgeItem(
                item_id="ki-1",
                evidence_id="ev-1",
                title="Finance extract",
                summary="Contribution per project.",
                structured_payload=payload,
            )
        ]
    )
    return state


class TestSourceEvidenceLinkage(unittest.TestCase):
    """A resolving citation proves identity, not that the evidence says the number."""

    def _classify(self, state: ProjectState) -> tuple[str, str]:
        return di.classify_control_provenance(state.monitor.circuit_breakers[0], state)

    def test_a_valid_but_unrelated_evidence_id_cannot_authorize_a_threshold(self):
        state = _monitor_state(
            ThresholdProvenance(provenance_class="source_evidence_backed", evidence_id="ev-1")
        )
        state.imported_evidence = [Evidence(evidence_id="ev-1", title="Unrelated churn extract")]
        provenance_class, reason = self._classify(state)
        self.assertEqual(provenance_class, di.PROVENANCE_REQUIRES_OPERATOR_REVIEW)
        self.assertNotIn(provenance_class, di.AUTHORITATIVE_PROVENANCE)
        self.assertIn("traceability only", reason)

    def test_naming_a_value_without_a_structured_claim_still_requires_review(self):
        state = _knowledge_state(
            ThresholdProvenance(
                provenance_class="source_evidence_backed",
                evidence_id="ev-1",
                threshold_value=800.0,
                evidence_value_key="contribution_per_project_usd",
            ),
            payload={"unrelated_metric": 42},
        )
        provenance_class, reason = self._classify(state)
        self.assertEqual(provenance_class, di.PROVENANCE_REQUIRES_OPERATOR_REVIEW)
        self.assertIn("no structured value", reason)

    def test_a_structured_claim_that_states_a_different_number_is_unsupported(self):
        state = _knowledge_state(
            ThresholdProvenance(
                provenance_class="source_evidence_backed",
                evidence_id="ev-1",
                threshold_value=800.0,
                evidence_value_key="contribution_per_project_usd",
            ),
            payload={"contribution_per_project_usd": 610.0},
        )
        provenance_class, reason = self._classify(state)
        self.assertEqual(provenance_class, di.PROVENANCE_UNSUPPORTED)
        self.assertIn("610", reason)

    def test_a_verified_structured_claim_is_authoritative(self):
        state = _knowledge_state(
            ThresholdProvenance(
                provenance_class="source_evidence_backed",
                evidence_id="ev-1",
                threshold_value=800.0,
                evidence_value_key="contribution_per_project_usd",
            ),
            payload={"contribution_per_project_usd": 800.0},
        )
        provenance_class, _ = self._classify(state)
        self.assertEqual(provenance_class, di.PROVENANCE_SOURCE_EVIDENCE)
        self.assertIn(provenance_class, di.AUTHORITATIVE_PROVENANCE)

    def test_an_unverified_source_backed_threshold_is_advisory_in_the_control_surface(self):
        state = _monitor_state(
            ThresholdProvenance(provenance_class="source_evidence_backed", evidence_id="ev-1")
        )
        state.imported_evidence = [Evidence(evidence_id="ev-1", title="Unrelated churn extract")]
        cells = [row.stop_change_threshold for row in build_monitoring_template_rows(state)]
        carrying = [cell for cell in cells if "800" in cell]
        self.assertTrue(carrying)
        for cell in carrying:
            self.assertIn(di.ADVISORY_THRESHOLD_PREFIX, cell)

    def test_an_unverified_source_backed_threshold_requires_review_and_fails_certification(self):
        state = _monitor_state(
            ThresholdProvenance(provenance_class="source_evidence_backed", evidence_id="ev-1")
        )
        state.imported_evidence = [Evidence(evidence_id="ev-1", title="Unrelated churn extract")]
        self.assertTrue(
            any("not authoritative" in w for w in di.delivery_review_warnings(state)),
            di.delivery_review_warnings(state),
        )
        with self.assertRaises(di.DecisionIntegrityError):
            di.require_decision_integrity(state)

    def test_operator_stated_is_unchanged_by_the_linkage_rule(self):
        state = _monitor_state(
            ThresholdProvenance(provenance_class="operator_stated", operator_reference="Operator floor: $800/project")
        )
        self.assertEqual(self._classify(state)[0], di.PROVENANCE_OPERATOR_STATED)
        di.require_decision_integrity(state)  # must not raise

    def test_reproducible_derived_is_unchanged_by_the_linkage_rule(self):
        state = _monitor_state(
            ThresholdProvenance(
                provenance_class="reproducible_derived",
                derivation=ThresholdDerivation(inputs=[40.0, 20.0], operation="product", result=800.0),
            )
        )
        self.assertEqual(self._classify(state)[0], di.PROVENANCE_REPRODUCIBLE_DERIVED)
        di.require_decision_integrity(state)  # must not raise


class TestControlSurface(unittest.TestCase):
    """An unsupported threshold must never read as authoritative."""

    def _threshold_cells(self, state: ProjectState) -> list[str]:
        return [row.stop_change_threshold for row in build_monitoring_template_rows(state)]

    def test_unsupported_threshold_is_demoted(self):
        state = _monitor_state()
        carrying = [c for c in self._threshold_cells(state) if "800" in c]
        self.assertTrue(carrying)
        for cell in carrying:
            self.assertIn(di.ADVISORY_THRESHOLD_PREFIX, cell)

    def test_verified_threshold_stays_authoritative(self):
        state = _monitor_state(
            ThresholdProvenance(provenance_class="operator_stated", operator_reference="Operator floor")
        )
        carrying = [c for c in self._threshold_cells(state) if "800" in c]
        self.assertTrue(carrying)
        for cell in carrying:
            self.assertNotIn(di.ADVISORY_THRESHOLD_PREFIX, cell)

    def test_cells_without_a_numeric_threshold_are_untouched(self):
        state = _state()
        state.monitor = MonitorOutput(
            circuit_breakers=[MonitorCircuitBreaker(strategy_ref="S1", trip="stop now", reset="resume")]
        )
        for cell in self._threshold_cells(state):
            self.assertNotIn(di.ADVISORY_THRESHOLD_PREFIX, cell)

    def test_demotion_is_idempotent(self):
        once = di.control_surface_threshold(UNSUPPORTED_TRIP, di.PROVENANCE_UNSUPPORTED)
        twice = di.control_surface_threshold(once, di.PROVENANCE_UNSUPPORTED)
        self.assertEqual(once, twice)
        self.assertEqual(twice.count(di.ADVISORY_THRESHOLD_PREFIX), 1)

    def test_canary_provenance_is_honoured(self):
        state = _state()
        state.monitor = MonitorOutput(
            canaries=[
                MonitorCanary(
                    signal="Margin below 12%",
                    direction="down",
                    window="14d",
                    meaning="Margin eroding below 12%",
                    threshold_provenance=ThresholdProvenance(
                        provenance_class="operator_stated", operator_reference="Operator floor 12%"
                    ),
                )
            ]
        )
        assessed = di.assess_control_thresholds(state)
        self.assertEqual([a.provenance_class for a in assessed], [di.PROVENANCE_OPERATOR_STATED])

    def test_certification_rejects_an_unsupported_hard_control(self):
        with self.assertRaises(di.DecisionIntegrityError):
            di.require_decision_integrity(_monitor_state())


class TestReportProjectionIsPure(unittest.TestCase):
    def test_building_the_report_does_not_mutate_state(self):
        state = _monitor_state()
        before = state.model_dump(mode="json")
        di.build_decision_integrity_report(state)
        di.assess_measurable_criteria(state)
        di.assess_control_thresholds(state)
        self.assertEqual(state.model_dump(mode="json"), before)


if __name__ == "__main__":
    unittest.main()
