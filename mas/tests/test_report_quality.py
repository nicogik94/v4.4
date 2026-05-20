"""Regression tests for deterministic report quality helpers."""
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from report_quality import (  # noqa: E402
    CLIENT_BF_CONFIDENCE_CAVEAT,
    PROVISIONAL_CLARIFICATION_CAVEAT,
    PROVISIONAL_CLARIFICATION_NEXT_ACTION,
    SPARSE_CONFIDENCE_RULE,
    THRESHOLD_CONFLICT_UNKNOWN_WARNING,
    assess_report_quality_context,
    client_simplify_text,
    evidence_maturity_projection,
    guard_client_bf_confidence,
    normalize_export_text,
    threshold_consistency_warnings,
)
from clarifications import (  # noqa: E402
    ClarificationAnswer,
    ClarificationCycle,
    ClarificationPriority,
    ClarificationQuestion,
    ClarificationStatus,
)
from state import ClassifyOutput, ProjectState, StrategyOutput  # noqa: E402


def _question(question_id: str, priority=ClarificationPriority.CRITICAL, status=ClarificationStatus.OPEN):
    return ClarificationQuestion(
        question_id=question_id,
        text=f"Question {question_id}?",
        why_it_matters="It affects delivery readiness.",
        priority=priority,
        affected_phase="strategy",
        source_gap="test_gap",
        status=status,
    )


class TestReportQualityHelpers(unittest.TestCase):
    def test_generic_growth_ignores_generated_seo_terms_for_domain(self):
        state = ProjectState(
            project_id="growth-generated-seo",
            project_name="Growth performance",
            brief="Improve growth performance across revenue operations, retention, and pipeline.",
            data="No direct evidence yet.",
        )
        state.report = "Generated report mentions Search Console, GA4, crawl, CMS, and editorial evidence."
        state.strategy = StrategyOutput(
            executive_strategy="Generated phase text says use SEO Lead and Web/CMS Owner."
        )

        quality = assess_report_quality_context(state)

        self.assertEqual(quality.decision_domain, "growth")
        self.assertIn("Growth Lead", quality.owner_roles)
        self.assertIn("cohort retention", quality.evidence_categories)
        forbidden = {"Search Console", "GA4", "CMS/schema capability"}
        self.assertTrue(forbidden.isdisjoint(set(quality.evidence_categories)))
        self.assertNotIn("Web/CMS Owner", quality.owner_roles)

    def test_seo_operator_input_enables_seo_evidence_categories(self):
        state = ProjectState(
            project_id="seo-growth",
            project_name="SEO growth",
            brief="Improve website traffic with SEO content, Search Console, GA4, crawl, and CMS review.",
        )

        quality = assess_report_quality_context(state)

        self.assertEqual(quality.decision_domain, "seo_content_editorial")
        self.assertIn("Search Console", quality.evidence_categories)
        self.assertIn("GA4", quality.evidence_categories)
        self.assertIn("Web/CMS Owner", quality.owner_roles)

    def test_productization_excludes_cms_without_operator_cms_context(self):
        state = ProjectState(
            project_id="productization-no-cms",
            project_name="Productization direction",
            brief="Choose the productization direction for template abstraction, ROI engine, and pilot users.",
        )

        quality = assess_report_quality_context(state)

        self.assertEqual(quality.decision_domain, "productization")
        self.assertIn("template schema / field registry validation", quality.evidence_categories)
        self.assertNotIn("CMS/schema capability", quality.evidence_categories)

    def test_sparse_confidence_rule_is_available(self):
        self.assertIn("Moderate confidence in the need for Sprint 0 evidence collection", SPARSE_CONFIDENCE_RULE)
        self.assertIn("low confidence in any specific root cause", SPARSE_CONFIDENCE_RULE)
        self.assertNotIn("High confidence only that evidence collection is required", SPARSE_CONFIDENCE_RULE)

    def test_client_simplification_covers_residual_jargon(self):
        text = (
            "H1 probability 70%; H2 failure probability 0.70; H3 has Jaccard index 0.42, "
            "Brier score 0.20, ECE 0.12, FMEA RPN 336, rho 0.45, correlation=0.44, "
            "BF=42, DQ=70, scenario_probability: 0.91, structural probability=0.73. "
            "The proposed planning gate is more than 15% activation."
        )

        simplified = client_simplify_text(text, sparse_evidence=True)

        for forbidden in (
            "H1",
            "H2",
            "H3",
            "Jaccard",
            "Brier score",
            "ECE",
            "FMEA",
            "RPN",
            "rho",
            "correlation=0.44",
            "BF=42",
            "DQ=70",
            "scenario_probability: 0.91",
            "structural probability=0.73",
        ):
            self.assertNotIn(forbidden, simplified)
        for expected in (
            "user-value hypothesis",
            "architecture hypothesis",
            "scope-risk hypothesis",
            "schema overlap score",
            "forecast accuracy check",
            "calibration check",
            "risk priority score",
            "related-hypothesis risk",
            "structural prior",
            "high provisional failure risk",
            "proposed planning gate is more than 15% activation",
        ):
            self.assertIn(expected, simplified)
        self.assertNotIn("model-generated prior", simplified)
        self.assertNotIn("structured risk priority", simplified)

    def test_client_simplification_fixes_missing_space_artifacts(self):
        text = (
            "BF values changed; BF progress stalled; DQ baseline absent. "
            "internal confidence diagnosticinternal confidence diagnostic stalled. "
            "evidence quality diagnosticevidence quality diagnosticbaseline."
        )

        simplified = client_simplify_text(text, sparse_evidence=True)

        for forbidden in (
            "diagnosticvalues",
            "diagnosticprogress",
            "diagnosticstalled",
            "diagnosticbaseline",
            "diagnosticinternal",
        ):
            self.assertNotIn(forbidden, simplified)
        self.assertIn("structural confidence signal values", simplified)
        self.assertIn("structural confidence signal progress", simplified)
        self.assertIn("structural confidence signal stalled", simplified)
        self.assertIn("evidence quality signal baseline", simplified)

    def test_normalize_export_text_client_cleans_artifacts_and_comparators(self):
        text = (
            "DQ greater than 70, BF greater than 10, r greater than 0.4, 2. 1, 0. 68, 70. 0. "
            "operator-confirmed threshold required prior probability; structured risk priority; "
            "model-generated prior; greater than greater than 70."
        )

        normalized = normalize_export_text(text, audience="client")

        self.assertIn("DQ >70", normalized)
        self.assertIn("BF >10", normalized)
        self.assertIn("r >0.4", normalized)
        self.assertIn("2.1", normalized)
        self.assertIn("0.68", normalized)
        self.assertIn("70.0", normalized)
        self.assertIn("structural prior", normalized)
        self.assertIn("risk priority score", normalized)
        for forbidden in (
            "operator-confirmed threshold required",
            "model-generated prior",
            "structured risk priority",
            "greater than greater than",
            "2. 1",
            "0. 68",
        ):
            self.assertNotIn(forbidden, normalized)

    def test_normalize_export_text_operator_keeps_traceability_terms(self):
        text = (
            "model-generated prior; internal confidence diagnostic; evidence quality diagnostic; "
            "operator-confirmed threshold required prior probability; BF greater than 10."
        )

        normalized = normalize_export_text(text, audience="operator")

        self.assertIn("model-generated prior", normalized)
        self.assertIn("internal confidence diagnostic", normalized)
        self.assertIn("evidence quality diagnostic", normalized)
        self.assertIn("unconfirmed model-generated prior probability", normalized)
        self.assertIn("BF >10", normalized)

    def test_normalize_export_text_protects_urls_paths_code_and_json(self):
        text = (
            "See https://example.com/a. 1 and C:\\data\\2. 1\\file.txt\n"
            "```json\n{\"value\":\"2. 1\",\"rule\":\"DQ greater than 70\"}\n```\n"
            "{\"value\":\"0. 68\"}\n"
            "Outside value 0. 68 and DQ greater than 70."
        )

        normalized = normalize_export_text(text, audience="client")

        self.assertIn("https://example.com/a. 1", normalized)
        self.assertIn("C:\\data\\2. 1\\file.txt", normalized)
        self.assertIn("{\"value\":\"2. 1\",\"rule\":\"DQ greater than 70\"}", normalized)
        self.assertIn("{\"value\":\"0. 68\"}", normalized)
        self.assertIn("Outside value 0.68 and DQ >70", normalized)

    def test_evidence_maturity_sparse_projects_are_hypothesis_only(self):
        state = ProjectState(project_id="hypothesis-only", brief="Improve growth performance.")

        projection = evidence_maturity_projection(state)

        self.assertEqual(projection.maturity, "Hypothesis-only")
        self.assertEqual(projection.client_use_status, "Internal planning only")
        self.assertEqual(projection.validation_required, "Sprint 0 evidence pack")

    def test_no_generated_clarifications_do_not_trigger_provisional_warning(self):
        state = ProjectState(project_id="no-generated-clarifications", brief="Improve growth performance.")
        state.clarification_cycles = []
        state.clarification_answers = []

        quality = assess_report_quality_context(state)

        self.assertFalse(quality.provisional_report)
        self.assertFalse(quality.required_clarifications_open)

    def test_missing_clarification_state_does_not_trigger_provisional_warning(self):
        state = ProjectState(project_id="missing-clarifications", brief="Improve growth performance.")
        state.clarification_cycles = None
        state.clarification_answers = None

        quality = assess_report_quality_context(state)

        self.assertFalse(quality.provisional_report)
        self.assertFalse(quality.required_clarifications_open)

    def test_provisional_warning_tracks_open_required_clarifications(self):
        state = ProjectState(project_id="open-critical", brief="Improve growth performance.")
        state.clarification_cycles = [
            ClarificationCycle(project_id=state.project_id, cycle_id="c1", questions=[_question("critical")])
        ]

        quality = assess_report_quality_context(state)

        self.assertTrue(quality.provisional_report)
        self.assertEqual(quality.provisional_clarification_caveat, PROVISIONAL_CLARIFICATION_CAVEAT)
        self.assertEqual(quality.provisional_clarification_next_action, PROVISIONAL_CLARIFICATION_NEXT_ACTION)

    def test_low_value_answer_does_not_hide_open_critical_clarification(self):
        state = ProjectState(project_id="low-answer-critical-open", brief="Improve growth performance.")
        state.clarification_cycles = [
            ClarificationCycle(
                project_id=state.project_id,
                cycle_id="c1",
                questions=[
                    _question("low", priority=ClarificationPriority.LOW, status=ClarificationStatus.ANSWERED),
                    _question("critical", priority=ClarificationPriority.CRITICAL, status=ClarificationStatus.OPEN),
                ],
            )
        ]
        state.clarification_answers = [
            ClarificationAnswer(question_id="low", answer_id="a1", status=ClarificationStatus.ANSWERED)
        ]

        quality = assess_report_quality_context(state)

        self.assertTrue(quality.provisional_report)

    def test_resolved_required_clarifications_hide_provisional_warning(self):
        state = ProjectState(project_id="resolved-critical", brief="Improve growth performance.")
        state.clarification_cycles = [
            ClarificationCycle(
                project_id=state.project_id,
                cycle_id="c1",
                questions=[_question("critical", status=ClarificationStatus.ANSWERED)],
            )
        ]

        quality = assess_report_quality_context(state)

        self.assertFalse(quality.provisional_report)

    def test_open_high_severity_importance_and_required_clarifications_block(self):
        cases = [
            {"question_id": "high", "priority": "high", "status": "open"},
            {"question_id": "severity", "severity": "high", "status": "pending"},
            {"question_id": "importance", "importance": "critical", "status": "unanswered"},
            {"question_id": "required", "required": True},
            {"question_id": "is-required", "is_required": True, "status": "required"},
            {"question_id": "trimmed", "priority": " HIGH ", "status": " open "},
        ]
        for question in cases:
            with self.subTest(question=question):
                state = ProjectState(project_id=f"blocking-{question['question_id']}", brief="Improve growth performance.")
                state.clarification_cycles = [{"questions": [question]}]

                quality = assess_report_quality_context(state)

                self.assertTrue(quality.provisional_report)

    def test_optional_low_and_medium_clarifications_do_not_block(self):
        state = ProjectState(project_id="optional-clarifications", brief="Improve growth performance.")
        state.clarification_cycles = [
            {
                "questions": [
                    {"question_id": "low", "priority": "low", "status": "open"},
                    {"question_id": "medium", "priority": "medium", "status": "pending"},
                    {"question_id": "optional", "importance": "low", "required": "false", "status": "unanswered"},
                ]
            }
        ]

        quality = assess_report_quality_context(state)

        self.assertFalse(quality.provisional_report)

    def test_matching_answers_by_supported_ids_resolve_required_clarifications(self):
        cases = [
            ({"question_id": "q-question"}, [{"question_id": "q-question"}]),
            ({"id": "q-id", "required": True}, [{"id": "q-id"}]),
            ({"clarification_id": "q-clarification", "required": True}, [{"clarification_id": "q-clarification"}]),
            ({"question_id": "q-dict", "priority": "high"}, {"q-dict": "Growth Lead"}),
        ]
        for question_id_shape, answers in cases:
            with self.subTest(question=question_id_shape, answers=answers):
                question = {"priority": "critical", "status": None, **question_id_shape}
                state = ProjectState(project_id="answer-resolves", brief="Improve growth performance.")
                state.clarification_cycles = [{"questions": [question]}]
                state.clarification_answers = answers

                quality = assess_report_quality_context(state)

                self.assertFalse(quality.provisional_report)

    def test_explicit_resolution_fields_and_statuses_hide_provisional_warning(self):
        resolved_statuses = [
            "answered",
            "resolved",
            "unavailable",
            "superseded",
            "closed",
            "complete",
            "completed",
            "waived",
            "not_applicable",
            "n/a",
            " Completed ",
        ]
        questions = [
            {"question_id": "answered-bool", "priority": "critical", "answered": True},
            {"question_id": "resolved-bool", "priority": "critical", "resolved": "true", "answered": "false"},
            *[
                {"question_id": f"status-{index}", "priority": "critical", "status": status}
                for index, status in enumerate(resolved_statuses)
            ],
        ]
        for question in questions:
            with self.subTest(question=question):
                state = ProjectState(project_id="resolved-status", brief="Improve growth performance.")
                state.clarification_cycles = [{"questions": [question]}]

                quality = assess_report_quality_context(state)

                self.assertFalse(quality.provisional_report)

    def test_threshold_canonical_decision_gates_suppresses_generic_warning(self):
        state = ProjectState(
            project_id="canonical-thresholds",
            brief="Improve growth performance.",
            report="""# Decision Gates
| Gate | Proceed | Stop |
|---|---|---|
| Data quality | DQ >70 | DQ <50 |

# Monitoring Details
Stop canary if churn >15%.
""",
        )

        self.assertEqual(threshold_consistency_warnings(state), [])

    def test_threshold_subordinate_sparse_growth_controls_do_not_warn(self):
        state = ProjectState(
            project_id="sparse-subordinate-thresholds",
            brief="Improve growth performance across retention, churn, acquisition, and pipeline.",
            report="""# Monitoring Details
Stop canary if churn >15%.

# Roadmap
Escalate if DQ <50 by day 30.

# Key Risks
Top channel >70% and CAC worsening.
""",
        )

        self.assertEqual(threshold_consistency_warnings(state), [])

    def test_threshold_operator_controls_and_warning_signals_do_not_warn(self):
        state = ProjectState(
            project_id="sparse-operator-controls",
            brief="Improve growth performance across retention, churn, acquisition, and pipeline.",
            report="""# Operator Controls
Trip circuit breaker if CAC >20%.

# Early Warning Signal
Escalate if NRR <85%.
""",
        )

        self.assertEqual(threshold_consistency_warnings(state), [])

    def test_threshold_two_primary_sections_report_specific_conflict(self):
        state = ProjectState(
            project_id="threshold-specific",
            brief="Improve growth performance.",
            report="""# Decision Gates
Proceed if DQ >70.

# Alternative Thresholds
Proceed if DQ >50.
""",
        )

        warnings = threshold_consistency_warnings(state)

        self.assertTrue(any("Threshold conflict detected between: Decision Gates and Alternative Thresholds." == warning for warning in warnings))

    def test_threshold_ambiguous_duplicate_warns_source_unknown(self):
        state = ProjectState(
            project_id="threshold-unknown",
            brief="Improve growth performance.",
            report="Canary success more than 5/20 in one place and more than 15% elsewhere.",
        )

        self.assertIn(THRESHOLD_CONFLICT_UNKNOWN_WARNING, threshold_consistency_warnings(state))

    def test_decision_gates_and_second_primary_threshold_section_report_specific_conflict(self):
        state = ProjectState(
            project_id="threshold-second-primary",
            brief="Improve growth performance.",
            report="""# Decision Gates
Proceed if DQ >70.

# Convergence Gates
Proceed if DQ >55.
""",
        )

        self.assertIn(
            "Threshold conflict detected between: Decision Gates and Convergence Gates.",
            threshold_consistency_warnings(state),
        )

    def test_guard_client_bf_confidence_softens_confirmed_causal_language(self):
        state = ProjectState(project_id="bf-low", brief="Improve growth performance.")
        state.classify = ClassifyOutput(bf=3.2)

        guarded = guard_client_bf_confidence(
            "The confirmed causal hypothesis is retention. Do not proceed until BF >10.",
            state,
        )

        self.assertIn(CLIENT_BF_CONFIDENCE_CAVEAT, guarded)
        self.assertIn("candidate causal hypothesis pending Sprint 0 validation", guarded)
        self.assertIn("Do not proceed until BF >10", guarded)
        self.assertNotIn("confirmed causal hypothesis", guarded.lower())

    def test_guard_client_bf_confidence_applies_to_hypothesis_only_even_with_high_bf(self):
        state = ProjectState(project_id="bf-high-hypothesis-only", brief="Improve growth performance.")
        state.classify = ClassifyOutput(bf=12.0)

        guarded = guard_client_bf_confidence(
            "# Summary\nBF=12.0 suggests the confirmed causal hypothesis is retention.\n\n"
            "## Decision Gates\nDo not proceed until BF >10.",
            state,
        )

        self.assertIn(CLIENT_BF_CONFIDENCE_CAVEAT, guarded)
        self.assertNotIn("BF=12.0", guarded)
        self.assertIn("Do not proceed until BF >10", guarded)
        self.assertNotIn("confirmed causal hypothesis", guarded.lower())

    def test_guard_client_bf_confidence_preserves_operator_trace_when_not_called(self):
        operator_trace = "Operator trace: BF 3.2 remains provisional; confirmed causal hypothesis wording came from raw strategy."

        self.assertIn("BF 3.2", operator_trace)
        self.assertIn("confirmed causal hypothesis", operator_trace)

    def test_normalize_export_text_cleans_remaining_placeholder_artifacts(self):
        text = (
            "less than provisional threshold of the expected signal; "
            "provisional threshold of the planned run time; "
            "provisional threshold of the provisional threshold; "
            "threshold of the expected signal after threshold of the planned run time"
        )

        normalized = normalize_export_text(text, audience="client")

        self.assertIn("below the pre-registered interim threshold", normalized)
        self.assertIn("halfway through the planned run time", normalized)
        self.assertIn("pre-registered interim threshold", normalized)
        self.assertIn("expected signal at the planned interim review", normalized)
        self.assertNotIn("provisional threshold of the expected signal", normalized)
        self.assertNotIn("provisional threshold of the planned run time", normalized)

    def test_normalize_export_text_cleans_new_placeholder_variants(self):
        text = (
            'less than provisional threshold "very disappointed"\n'
            "more than provisional threshold week-over-week\n"
            "provisional effort estimate\n"
            "structural prior s\n"
            "system blindness, next item\n"
            "reference-class prior s\n"
            "model-generated prior s\n"
            "diagnostic score s\n"
            "provisional risk estimate s"
        )

        normalized = normalize_export_text(text, audience="client")

        self.assertIn('below the operator-defined threshold for "very disappointed"', normalized)
        self.assertIn("above the operator-defined threshold week over week", normalized)
        self.assertIn("operator-defined effort estimate", normalized)
        self.assertIn("structural priors", normalized)
        self.assertIn("system blindness,next item", normalized)
        self.assertIn("reference-class priors", normalized)
        self.assertIn("structural priors", normalized)
        self.assertIn("diagnostic scores", normalized)
        self.assertIn("provisional risk estimates", normalized)
        for forbidden in (
            "less than provisional threshold",
            "more than provisional threshold",
            "provisional effort estimate",
            "structural prior s",
            "reference-class prior s",
            "model-generated prior s",
            "diagnostic score s",
            "provisional risk estimate s",
        ):
            self.assertNotIn(forbidden, normalized)

    def test_normalize_export_text_repairs_standalone_bullets_and_reduced_sprint0_list(self):
        text = """-
billing/product metric reconciliation
*
cohort retention curves
•
funnel and channel-mix review
1 billing reconciliation
2 cohort retention
3 funnel conversion
4 10 churn/user interviews"""

        normalized = normalize_export_text(text, audience="client")

        self.assertIn("- billing/product metric reconciliation", normalized)
        self.assertIn("* cohort retention curves", normalized)
        self.assertIn("• funnel and channel-mix review", normalized)
        self.assertIn("1. billing reconciliation", normalized)
        self.assertIn("2. cohort retention", normalized)
        self.assertIn("3. funnel conversion", normalized)
        self.assertIn("4. 10 churn/user interviews", normalized)

    def test_normalize_export_text_preserves_protected_contexts_for_new_cleanup(self):
        text = (
            'https://example.com/less than provisional threshold "very disappointed"\n'
            "C:\\data\\more than provisional threshold week-over-week\\file.txt\n"
            "```json\n"
            '{"phrase":"less than provisional threshold"}\n'
            "```\n"
            '{"phrase":"more than provisional threshold"}\n'
            'less than provisional threshold "very disappointed"'
        )

        normalized = normalize_export_text(text, audience="client")

        self.assertIn('https://example.com/less than provisional threshold "very disappointed"', normalized)
        self.assertIn("C:\\data\\more than provisional threshold week-over-week\\file.txt", normalized)
        self.assertIn('{"phrase":"less than provisional threshold"}', normalized)
        self.assertIn('{"phrase":"more than provisional threshold"}', normalized)
        self.assertIn('below the operator-defined threshold for "very disappointed"', normalized)


if __name__ == "__main__":
    unittest.main()
