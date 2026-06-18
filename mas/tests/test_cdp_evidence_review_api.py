"""Read-only CDP evidence-review API surface tests."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api  # noqa: E402
from cdp.review_caveats import CDP_REVIEW_CAVEATS, RESOLVER_STATUSES  # noqa: E402
from explainability import build_project_trace  # noqa: E402
from state import Evidence, KnowledgeItem, KnowledgeLayerState, ProjectState  # noqa: E402


def make_review_state(project_id: str = "cdp-api") -> ProjectState:
    state = ProjectState(project_id=project_id, project_name="CDP API", brief="Review citations.")
    state.knowledge_layer = KnowledgeLayerState(
        items=[
            KnowledgeItem(
                item_id="ev-exact",
                source_id="src",
                source_ref="fixture://exact",
                title="Exact",
                structured_payload={"locator": "chunk=1"},
            ),
            KnowledgeItem(
                item_id="ev-id-only",
                source_id="src",
                source_ref="fixture://id-only",
                title="ID only",
                structured_payload={"category": "no_locator"},
            ),
        ]
    )
    state.report = "\n".join(
        [
            "# EXECUTIVE SUMMARY",
            "- Exact marker [Evidence: ev-exact | chunk=1].",
            "- ID-only marker [Evidence: ev-id-only | locator unavailable].",
            "- Unknown marker [Evidence: ev-missing | chunk=9].",
            "- Mismatched locator [Evidence: ev-exact | chunk=2].",
            "- Malformed marker [Evidence: ev-exact \\| anchor].",
            "- Revenue increased 20% after rollout.",
        ]
    )
    return state


class TestCdpEvidenceReviewApi(unittest.IsolatedAsyncioTestCase):
    async def test_endpoint_returns_valid_read_only_payload(self):
        state = make_review_state("cdp-api-valid")

        with patch("api.store.load", new=AsyncMock(return_value=state)):
            response = await api.get_evidence_review(state.project_id)

        payload = response.model_dump(mode="json")
        json.dumps(payload)
        self.assertEqual(response.project_id, state.project_id)
        self.assertEqual(response.schema_version, "cdp.v0.1")
        self.assertEqual(response.source, "ProjectState.report")
        self.assertTrue(response.read_only)
        self.assertTrue(response.review_only)
        self.assertTrue(response.report_text_preserved)
        self.assertEqual(response.summary_counts["resolved_exact"], 1)
        self.assertEqual(len(response.resolutions), 5)
        self.assertEqual(len(response.load_bearing_reviews), 1)

    async def test_endpoint_returns_404_for_missing_project(self):
        with patch("api.store.load", new=AsyncMock(return_value=None)):
            with self.assertRaises(api.HTTPException) as ctx:
                await api.get_evidence_review("missing-project")

        self.assertEqual(ctx.exception.status_code, 404)

    async def test_endpoint_includes_anti_overclaiming_labels_and_caveats(self):
        state = make_review_state("cdp-api-caveats")

        with patch("api.store.load", new=AsyncMock(return_value=state)):
            response = await api.get_evidence_review(state.project_id)

        self.assertIn("CDP v0.1 is review-only citation resolvability.", response.anti_overclaiming_labels)
        self.assertIn("Resolved markers are traceability aids only.", response.anti_overclaiming_caveats)
        self.assertIn("CDP does not verify semantic support.", response.anti_overclaiming_caveats)
        self.assertIn("CDP does not prove full claim defensibility.", response.anti_overclaiming_caveats)
        self.assertIn("CDP does not approve delivery.", response.anti_overclaiming_caveats)
        self.assertIn("CDP does not rewrite, strip, or correct report text.", response.anti_overclaiming_caveats)

    async def test_endpoint_exposes_expected_resolver_statuses(self):
        state = make_review_state("cdp-api-statuses")

        with patch("api.store.load", new=AsyncMock(return_value=state)):
            response = await api.get_evidence_review(state.project_id)

        status_counts = {item.status: item.count for item in response.resolver_statuses}
        self.assertEqual(set(status_counts), set(RESOLVER_STATUSES))
        self.assertEqual(status_counts["resolved_exact"], 1)
        self.assertEqual(status_counts["resolved_id_only"], 1)
        self.assertEqual(status_counts["unknown_evidence_id"], 1)
        self.assertEqual(status_counts["locator_mismatch"], 1)
        self.assertEqual(status_counts["malformed"], 1)
        descriptions = {item.status: item.description for item in response.resolver_statuses}
        self.assertIn("weaker than resolved_exact", descriptions["resolved_id_only"])

    async def test_empty_report_and_missing_registry_do_not_crash(self):
        state = ProjectState(project_id="cdp-api-empty", project_name="Empty", brief="No report.")

        with patch("api.store.load", new=AsyncMock(return_value=state)):
            response = await api.get_evidence_review(state.project_id)

        self.assertEqual(response.summary_counts["canonical_marker_count"], 0)
        self.assertIn("raw_report_missing", response.missing_inputs)
        self.assertIn("evidence_locator_registry_empty", response.missing_inputs)
        self.assertEqual(response.resolutions, [])

    async def test_malformed_markers_surface_as_review_items(self):
        state = make_review_state("cdp-api-malformed")

        with patch("api.store.load", new=AsyncMock(return_value=state)):
            response = await api.get_evidence_review(state.project_id)

        self.assertIn("[Evidence: ev-exact \\| anchor]", response.malformed_candidates)
        self.assertTrue(any(item.status == "malformed" and item.review_eligible for item in response.resolutions))
        self.assertTrue(any(item.startswith("malformed:") for item in response.claims_requiring_review))

    async def test_endpoint_does_not_mutate_or_save_or_hydrate_decision_objects(self):
        state = make_review_state("cdp-api-non-mutation")
        before = state.model_dump(mode="json")
        save_mock = AsyncMock(side_effect=AssertionError("store.save must not be called"))
        ensure_mock = Mock(side_effect=AssertionError("ensure_decision_objects must not be called"))

        with patch("api.store.load", new=AsyncMock(return_value=state)):
            with patch("api.store.save", new=save_mock):
                with patch("api.ensure_decision_objects", new=ensure_mock):
                    response = await api.get_evidence_review(state.project_id)

        self.assertEqual(response.project_id, state.project_id)
        self.assertEqual(state.model_dump(mode="json"), before)
        save_mock.assert_not_called()
        ensure_mock.assert_not_called()


class TestCdpDecisionTraceSummary(unittest.TestCase):
    def test_project_trace_evidence_review_summary_is_compact_read_only_and_caveated(self):
        state = ProjectState(project_id="cdp-trace-summary", project_name="Trace", brief="Review.")
        state.imported_evidence = [Evidence(evidence_id="ev-id-only", title="ID-only evidence")]
        state.report = "\n".join(
            [
                "# EXECUTIVE SUMMARY",
                "- ID-only marker [Evidence: ev-id-only | locator unavailable].",
                "- Malformed marker [Evidence: ev-id-only \\| anchor].",
                "- Revenue increased 20% after rollout.",
            ]
        )
        before = state.model_dump(mode="json")

        trace = build_project_trace(state)

        self.assertEqual(state.model_dump(mode="json"), before)
        self.assertTrue(trace.evidence_review.read_only)
        self.assertTrue(trace.evidence_review.review_only)
        self.assertEqual(trace.evidence_review.schema_version, "cdp.v0.1")
        self.assertEqual(trace.evidence_review.summary_counts["malformed"], 1)
        self.assertEqual(trace.evidence_review.review_item_count, 3)
        self.assertEqual(trace.evidence_review.caveats, list(CDP_REVIEW_CAVEATS))
        trace_payload = trace.evidence_review.model_dump(mode="json")
        self.assertNotIn("resolutions", trace_payload)
        self.assertNotIn("load_bearing_reviews", trace_payload)


class TestCdpRuntimeCoupling(unittest.TestCase):
    def test_runtime_surfaces_do_not_import_internal_cdp_review_tool(self):
        for rel_path in ("api.py", "explainability.py", "workspace.py", "dashboards/index.html"):
            path = ROOT / rel_path
            if not path.exists():
                continue
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("tools.cdp_review", source)
            self.assertNotIn("from tools import cdp_review", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
