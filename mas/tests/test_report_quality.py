"""Regression tests for deterministic report quality helpers."""
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from report_quality import (  # noqa: E402
    SPARSE_CONFIDENCE_RULE,
    assess_report_quality_context,
    client_simplify_text,
)
from state import ProjectState, StrategyOutput  # noqa: E402


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
            "structured risk priority",
            "related-hypothesis risk",
            "model-generated prior",
            "high provisional failure risk",
            "proposed planning gate is more than 15% activation",
        ):
            self.assertIn(expected, simplified)

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
        self.assertIn("internal confidence diagnostic values", simplified)
        self.assertIn("internal confidence diagnostic progress", simplified)
        self.assertIn("internal confidence diagnostic stalled", simplified)
        self.assertIn("evidence quality diagnostic baseline", simplified)


if __name__ == "__main__":
    unittest.main()
