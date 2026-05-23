"""Focused tests for the internal CDP review CLI tool."""
import asyncio
import io
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cdp.citation_resolvability import build_defense_pass_result  # noqa: E402
from extensions.runtime import GatewayResponse  # noqa: E402
from state import KnowledgeItem, KnowledgeLayerState, ProjectState  # noqa: E402
from tools import cdp_review  # noqa: E402


def make_state(report: str) -> ProjectState:
    state = ProjectState(project_id="cdp-tool", project_name="CDP Tool", brief="Review CDP output.")
    state.report = report
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
                structured_payload={"locator": "locator unavailable"},
            ),
        ]
    )
    return state


class TestCdpReviewTool(unittest.TestCase):
    def test_parser_requires_project_id(self):
        with self.assertRaises(SystemExit):
            cdp_review.build_parser().parse_args([])

    def test_regenerate_requires_confirmation_before_provider_call(self):
        stderr = io.StringIO()
        with patch("tools.cdp_review.generate_report", new=AsyncMock()) as generator:
            with redirect_stderr(stderr):
                code = cdp_review.main(["--project-id", "p1", "--regenerate-report"])

        self.assertEqual(code, 2)
        generator.assert_not_called()
        self.assertIn("--regenerate-report requires --confirm-regenerate", stderr.getvalue())

    def test_rate_helper_returns_fraction_or_none(self):
        self.assertEqual(cdp_review._rate(1, 2), 0.5)
        self.assertEqual(cdp_review._rate(0, 3), 0.0)
        self.assertIsNone(cdp_review._rate(0, 0))

    def test_zero_resolved_markers_can_still_pass_when_tool_completes(self):
        state = make_state("No canonical markers in this report.")

        result = asyncio.run(
            cdp_review.review_project(
                "cdp-tool",
                cdp_review.ReviewOptions(),
                loader=AsyncMock(return_value=state),
            )
        )

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["total_markers"], 0)
        self.assertIsNone(result["resolved_exact_rate"])
        self.assertIsNone(result["resolved_id_only_rate"])
        self.assertIsNone(result["malformed_rate"])
        self.assertIsNone(result["review_eligible_resolution_rate"])

    def test_provider_inconclusive_depends_on_required_primary_route(self):
        state = make_state("[Evidence: ev-exact | chunk=1]")
        response = GatewayResponse(
            text="[Evidence: ev-exact | chunk=1]",
            provider_used="openai",
            model_used="gpt-5",
            fallback_used=True,
        )

        result = asyncio.run(
            cdp_review.review_project(
                "cdp-tool",
                cdp_review.ReviewOptions(regenerate_report=True, require_primary_anthropic=True),
                loader=AsyncMock(return_value=state),
                report_generator=AsyncMock(return_value=response),
            )
        )

        self.assertEqual(result["status"], "PROVIDER_INCONCLUSIVE")
        self.assertEqual(result["status_reason"], "provider_route_not_primary_anthropic")

        no_requirement_result = asyncio.run(
            cdp_review.review_project(
                "cdp-tool",
                cdp_review.ReviewOptions(regenerate_report=True, require_primary_anthropic=False),
                loader=AsyncMock(return_value=state),
                report_generator=AsyncMock(return_value=response),
            )
        )

        self.assertEqual(no_requirement_result["status"], "PASS")

    def test_output_contains_exact_anti_overclaiming_labels(self):
        state = make_state("[Evidence: ev-exact | chunk=1]")
        result = build_defense_pass_result(state)
        serialized = cdp_review.serialize_review_result(
            project_id=state.project_id,
            project_name=state.project_name,
            report_text=state.report,
            result=result,
            regenerated_report=False,
            sample_limit=3,
        )
        payload = cdp_review.build_payload([serialized])
        summary = cdp_review.human_summary(payload)

        self.assertEqual(payload["anti_overclaiming_labels"], cdp_review.ANTI_OVERCLAIMING_LABELS)
        for label in cdp_review.ANTI_OVERCLAIMING_LABELS:
            self.assertIn(label, summary)

    def test_human_summary_prints_locator_precision_caveat_when_id_only_dominates(self):
        item = {
            "project_id": "p1",
            "project_name": "Project",
            "status": "PASS",
            "status_reason": "completed",
            "regenerated_report": False,
            "report_has_markers": True,
            "total_markers": 3,
            "resolver_status_counts": {
                "resolved_exact": 1,
                "resolved_id_only": 2,
                "unknown_evidence_id": 0,
                "locator_mismatch": 0,
                "malformed": 0,
            },
            "resolved_exact_rate": 1 / 3,
            "resolved_id_only_rate": 2 / 3,
            "malformed_rate": 0.0,
            "review_eligible_resolution_count": 2,
            "review_eligible_resolution_rate": 2 / 3,
            "load_bearing_review_count": 0,
            "report_text_preserved": True,
        }

        summary = cdp_review.human_summary(cdp_review.build_payload([item]))

        self.assertIn(cdp_review.LOCATOR_PRECISION_CAVEAT, summary)

    def test_human_summary_prints_stronger_all_id_only_note(self):
        item = {
            "project_id": "p1",
            "project_name": "Project",
            "status": "PASS",
            "status_reason": "completed",
            "regenerated_report": False,
            "report_has_markers": True,
            "total_markers": 2,
            "resolver_status_counts": {
                "resolved_exact": 0,
                "resolved_id_only": 2,
                "unknown_evidence_id": 0,
                "locator_mismatch": 0,
                "malformed": 0,
            },
            "resolved_exact_rate": 0.0,
            "resolved_id_only_rate": 1.0,
            "malformed_rate": 0.0,
            "review_eligible_resolution_count": 2,
            "review_eligible_resolution_rate": 1.0,
            "load_bearing_review_count": 0,
            "report_text_preserved": True,
        }

        summary = cdp_review.human_summary(cdp_review.build_payload([item]))

        self.assertIn(cdp_review.ALL_ID_ONLY_CAVEAT, summary)
        self.assertNotIn(cdp_review.LOCATOR_PRECISION_CAVEAT, summary)

    def test_store_save_is_not_called_and_state_is_not_mutated(self):
        state = make_state("[Evidence: ev-exact | chunk=1]")
        before = state.model_dump(mode="json")

        with patch("store.load", new=AsyncMock(return_value=state)):
            with patch("store.save", side_effect=AssertionError("store.save must not be called")) as save:
                result = asyncio.run(cdp_review.review_project("cdp-tool", cdp_review.ReviewOptions()))

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(state.model_dump(mode="json"), before)
        save.assert_not_called()

    def test_regeneration_mutates_only_copy(self):
        state = make_state("[Evidence: ev-exact | chunk=1]")
        before = state.model_dump(mode="json")
        seen = {}

        async def fake_generator(_project_id, copied_state):
            seen["copied_state"] = copied_state
            copied_state.project_name = "mutated copy"
            return GatewayResponse(
                text="[Evidence: ev-exact | chunk=1]",
                provider_used="anthropic",
                model_used="claude-sonnet-4-6",
                fallback_used=False,
            )

        result = asyncio.run(
            cdp_review.review_project(
                "cdp-tool",
                cdp_review.ReviewOptions(regenerate_report=True, require_primary_anthropic=True),
                loader=AsyncMock(return_value=state),
                report_generator=fake_generator,
            )
        )

        self.assertEqual(result["status"], "PASS")
        self.assertIsNot(seen["copied_state"], state)
        self.assertEqual(state.model_dump(mode="json"), before)

    def test_regeneration_path_does_not_call_store_save(self):
        state = make_state("[Evidence: ev-exact | chunk=1]")
        response = GatewayResponse(
            text="[Evidence: ev-exact | chunk=1]",
            provider_used="anthropic",
            model_used="claude-sonnet-4-6",
            fallback_used=False,
        )

        with patch("store.load", new=AsyncMock(return_value=state)):
            with patch("store.save", side_effect=AssertionError("store.save must not be called")) as save:
                result = asyncio.run(
                    cdp_review.review_project(
                        "cdp-tool",
                        cdp_review.ReviewOptions(regenerate_report=True),
                        report_generator=AsyncMock(return_value=response),
                    )
                )

        self.assertEqual(result["status"], "PASS")
        save.assert_not_called()

    def test_report_gateway_request_disables_cache(self):
        request = cdp_review.build_report_request("project-1", "system", "prompt")

        self.assertFalse(request.allow_cache)
        self.assertEqual(request.phase, "report")


if __name__ == "__main__":
    unittest.main(verbosity=2)
