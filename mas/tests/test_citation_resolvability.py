"""Focused tests for T1b citation resolvability."""
import hashlib
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cdp.citation_resolvability import (  # noqa: E402
    CANONICAL_MARKER_PATTERN,
    CANONICAL_MARKER_RE,
    build_defense_pass_result,
    build_evidence_locator_registry,
)
from state import (  # noqa: E402
    DecisionObjects,
    Evidence,
    Hypothesis,
    KnowledgeItem,
    KnowledgeLayerState,
    ProjectState,
    Provenance,
)


def make_cdp_state() -> ProjectState:
    state = ProjectState(project_id="cdp-test", project_name="CDP Test", brief="Assess citation resolvability.")
    state.knowledge_layer = KnowledgeLayerState(
        items=[
            KnowledgeItem(
                evidence_id="ev-exact",
                source_id="src-fixture",
                source_ref="fixture://exact",
                title="Exact evidence",
                locator="fixture://exact#chunk=1",
                provenance=Provenance(external_uri="fixture://exact"),
            ),
            KnowledgeItem(
                evidence_id="ev-unavailable",
                source_id="src-fixture",
                source_ref="fixture://unavailable",
                title="Unavailable locator",
                locator="locator unavailable",
            ),
        ]
    )
    state.imported_evidence = [
        Evidence(
            evidence_id="ev-imported",
            title="Imported evidence",
            provenance=Provenance(source_ref="import://one"),
        )
    ]
    state.decision_objects = DecisionObjects(
        evidences=[
            Evidence(
                evidence_id="ev-decision",
                title="Decision evidence",
                provenance=Provenance(source_ref="decision://one"),
            )
        ]
    )
    state.hypotheses = [
        Hypothesis(id="H1", text="Hypothesis one", evidence_ids=["ev-hypothesis"])
    ]
    return state


class TestCitationResolvability(unittest.TestCase):
    def test_schema_version_and_strict_canonical_regex(self):
        state = make_cdp_state()
        state.report = "Claim [Evidence: ev-exact | fixture://exact#chunk=1]."

        result = build_defense_pass_result(state)

        self.assertEqual(result.schema_version, "cdp.v0.1")
        self.assertEqual(
            CANONICAL_MARKER_PATTERN,
            r"\[Evidence:\s+[^\s|]+\s+\|\s+[^\]]+\]",
        )
        self.assertIsNotNone(CANONICAL_MARKER_RE.fullmatch("[Evidence: ev-exact | fixture://exact#chunk=1]"))
        self.assertIsNone(CANONICAL_MARKER_RE.fullmatch("[Evidence: ev-exact \\| fixture://exact#chunk=1]"))

    def test_registry_uses_existing_metadata_without_converting_unavailable_locator_to_concrete(self):
        registry = {entry.evidence_id: entry for entry in build_evidence_locator_registry(make_cdp_state())}

        self.assertEqual(registry["ev-exact"].locators, ["fixture://exact#chunk=1"])
        self.assertTrue(registry["ev-exact"].has_concrete_locator)
        self.assertEqual(registry["ev-unavailable"].locators, [])
        self.assertFalse(registry["ev-unavailable"].has_concrete_locator)
        self.assertEqual(registry["ev-imported"].locators, [])
        self.assertEqual(registry["ev-decision"].locators, [])
        self.assertEqual(registry["ev-hypothesis"].locators, [])

    def test_resolver_covers_all_required_statuses(self):
        state = make_cdp_state()
        state.report = "\n".join(
            [
                "# EXECUTIVE SUMMARY",
                "- Exact [Evidence: ev-exact | fixture://exact#chunk=1].",
                "- Known id only [Evidence: ev-imported | locator unavailable].",
                "- Known concrete id with unavailable locator [Evidence: ev-exact | locator unavailable].",
                "- Unknown [Evidence: ev-missing | fixture://missing#chunk=1].",
                "- Mismatch [Evidence: ev-exact | fixture://wrong#chunk=9].",
                "- Escaped [Evidence: ev-exact \\| fixture://exact#chunk=1].",
            ]
        )

        result = build_defense_pass_result(state)
        statuses = [resolution.status for resolution in result.resolutions]

        self.assertIn("resolved_exact", statuses)
        self.assertIn("resolved_id_only", statuses)
        self.assertIn("unknown_evidence_id", statuses)
        self.assertIn("locator_mismatch", statuses)
        self.assertIn("malformed", statuses)
        self.assertEqual(result.summary_counts["resolved_exact"], 1)
        self.assertEqual(result.summary_counts["resolved_id_only"], 2)
        self.assertEqual(result.summary_counts["unknown_evidence_id"], 1)
        self.assertEqual(result.summary_counts["locator_mismatch"], 1)
        self.assertEqual(result.summary_counts["malformed"], 1)

    def test_resolved_id_only_is_review_eligible_and_weaker_than_exact(self):
        state = make_cdp_state()
        state.report = "\n".join(
            [
                "# EXECUTIVE SUMMARY",
                "- Exact [Evidence: ev-exact | fixture://exact#chunk=1].",
                "- ID only [Evidence: ev-imported | locator unavailable].",
            ]
        )

        result = build_defense_pass_result(state)
        exact = next(item for item in result.resolutions if item.status == "resolved_exact")
        id_only = next(item for item in result.resolutions if item.status == "resolved_id_only")

        self.assertFalse(exact.review_eligible)
        self.assertTrue(id_only.review_eligible)
        self.assertIn("resolved_id_only", result.claims_requiring_review[0])

    def test_placeholder_and_template_markers_are_malformed_not_normalized(self):
        state = make_cdp_state()
        malformed_markers = [
            "[Evidence: ...]",
            "[Evidence: <evidence_id> | <locator>]",
            "[Evidence: evidence_id | locator]",
            "[Evidence: ev-exact | ...]",
            "[Evidence: ... | ...]",
            "[Evidence: ev-exact \\| fixture://exact#chunk=1]",
        ]
        state.report = "\n".join(f"- Bad {marker}." for marker in malformed_markers)

        result = build_defense_pass_result(state)

        self.assertEqual(result.malformed_candidates, malformed_markers)
        self.assertEqual(result.summary_counts["canonical_marker_count"], 0)
        self.assertEqual(result.summary_counts["malformed"], len(malformed_markers))
        self.assertEqual([item.marker for item in result.resolutions], malformed_markers)

    def test_project_state_is_not_mutated_and_report_text_is_preserved_byte_for_byte(self):
        state = make_cdp_state()
        state.report = "# EXECUTIVE SUMMARY\r\n- Revenue grew 20% [Evidence: ev-exact | fixture://exact#chunk=1].\r\n"
        before_dump = state.model_dump(mode="json")
        before_report = state.report

        result = build_defense_pass_result(state)

        self.assertEqual(state.model_dump(mode="json"), before_dump)
        self.assertEqual(state.report, before_report)
        self.assertTrue(result.report_text_preserved)
        self.assertEqual(result.source_report_sha256, hashlib.sha256(before_report.encode("utf-8")).hexdigest())
        self.assertEqual(result.source_report_length, len(before_report))

    def test_repeated_runs_are_deterministic(self):
        state = make_cdp_state()
        state.report = "\n".join(
            [
                "# EXECUTIVE SUMMARY",
                "- Demand increased 12% [Evidence: ev-exact | fixture://exact#chunk=1].",
                "- Imported basis [Evidence: ev-imported | locator unavailable].",
            ]
        )

        first = build_defense_pass_result(state)
        second = build_defense_pass_result(state)

        self.assertEqual(first.model_dump(mode="json"), second.model_dump(mode="json"))

    def test_load_bearing_findings_are_line_level_review_only(self):
        state = make_cdp_state()
        state.report = "\n".join(
            [
                "# EXECUTIVE SUMMARY",
                "- Revenue increased 20% after rollout.",
                "- Capacity risk is unknown [Unknown].",
                "- Demand improved [Evidence: ev-exact | fixture://exact#chunk=1].",
                "# BACKGROUND",
                "- Revenue increased 20% after rollout.",
            ]
        )

        result = build_defense_pass_result(state)

        self.assertEqual(len(result.load_bearing_reviews), 1)
        finding = result.load_bearing_reviews[0]
        self.assertTrue(finding.review_only)
        self.assertEqual(finding.section, "executive summary")
        self.assertEqual(finding.line_number, 2)
        self.assertIn("review_only", finding.reason)
        self.assertIn("load_bearing_review", result.claims_requiring_review[-1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
