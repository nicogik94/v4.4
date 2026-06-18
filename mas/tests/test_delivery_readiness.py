"""Focused tests for the advisory delivery review readiness projection."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api  # noqa: E402
from clarifications import (  # noqa: E402
    ClarificationCycle,
    ClarificationPriority,
    ClarificationQuestion,
    ClarificationStatus,
)
from delivery_readiness import (  # noqa: E402
    DELIVERY_REVIEW_READINESS_CAVEATS,
    build_delivery_review_readiness,
)
from state import KnowledgeItem, KnowledgeLayerState, ProjectState  # noqa: E402
from workspace import build_workspace_summary  # noqa: E402


def make_ready_state(project_id: str = "delivery-ready") -> ProjectState:
    state = ProjectState(project_id=project_id, project_name="Delivery readiness", brief="Review a final report.")
    state.clarification_cycles = [
        ClarificationCycle(project_id=state.project_id, cycle_id="clarifications-clear", questions=[])
    ]
    state.knowledge_layer = KnowledgeLayerState(
        items=[
            KnowledgeItem(
                item_id="ev-ready",
                evidence_id="ev-ready",
                source_id="src",
                source_ref="fixture://ready#chunk=1",
                locator="chunk=1",
                title="Ready evidence",
            )
        ]
    )
    state.report = "# Executive Summary\nThe recommendation is supported [Evidence: ev-ready | chunk=1]."
    return state


def make_required_question(question_id: str = "q-required") -> ClarificationQuestion:
    return ClarificationQuestion(
        question_id=question_id,
        text="What decision deadline applies?",
        why_it_matters="Timing affects delivery review.",
        priority=ClarificationPriority.HIGH,
        affected_phase="strategy",
        source_gap="decision_deadline",
        status=ClarificationStatus.OPEN,
    )


class TestDeliveryReadinessProjection(unittest.TestCase):
    def test_readiness_blocks_when_required_clarifications_are_open(self):
        state = make_ready_state("delivery-clarification-blocked")
        state.clarification_cycles = [
            ClarificationCycle(
                project_id=state.project_id,
                cycle_id="clarifications-open",
                questions=[make_required_question()],
            )
        ]

        readiness = build_delivery_review_readiness(state.project_id, state)

        self.assertFalse(readiness.review_ready)
        self.assertEqual(readiness.status, "blocked_for_review")
        self.assertTrue(any("required clarification" in reason for reason in readiness.blocking_reasons))
        self.assertEqual(readiness.source_signals["clarifications"]["open_required_count"], 1)
        self.assertEqual(readiness.caveats, DELIVERY_REVIEW_READINESS_CAVEATS)

    def test_readiness_blocks_when_evidence_review_has_unresolved_or_malformed_markers(self):
        cases = [
            ("unresolved", "[Evidence: ev-missing | chunk=1]", "unknown_evidence_id"),
            ("malformed", "[Evidence: ev-ready \\| chunk=1]", "malformed"),
        ]
        for name, marker, status_key in cases:
            with self.subTest(name=name):
                state = make_ready_state(f"delivery-evidence-{name}")
                state.report = f"# Executive Summary\nGenerated claim needs review {marker}."

                readiness = build_delivery_review_readiness(state.project_id, state)

                self.assertIn(readiness.status, {"blocked_for_review", "needs_operator_review"})
                self.assertEqual(readiness.status, "blocked_for_review")
                self.assertGreater(
                    readiness.source_signals["evidence_review"]["hard_blocking_status_counts"][status_key],
                    0,
                )
                self.assertTrue(any("Evidence review" in reason for reason in readiness.blocking_reasons))

    def test_readiness_needs_operator_review_when_source_signals_are_unknown(self):
        state = ProjectState(project_id="delivery-unknown", project_name="Unknown", brief="Review later.")
        state.clarification_cycles = [
            ClarificationCycle(project_id=state.project_id, cycle_id="clarifications-clear", questions=[])
        ]

        readiness = build_delivery_review_readiness(state.project_id, state)

        self.assertFalse(readiness.review_ready)
        self.assertEqual(readiness.status, "needs_operator_review")
        self.assertEqual(readiness.blocking_reasons, [])
        self.assertEqual(readiness.source_signals["evidence_review"]["status"], "unknown")
        self.assertTrue(readiness.review_warnings)

    def test_readiness_can_be_ready_for_human_review_only_when_no_blockers_are_present(self):
        state = make_ready_state("delivery-ready-clean")

        readiness = build_delivery_review_readiness(state.project_id, state)

        self.assertTrue(readiness.review_ready)
        self.assertEqual(readiness.status, "ready_for_human_review")
        self.assertEqual(readiness.blocking_reasons, [])
        self.assertEqual(readiness.review_warnings, [])
        self.assertIn("clarifications", readiness.source_signals)
        self.assertIn("evidence_review", readiness.source_signals)
        self.assertEqual(readiness.caveats, DELIVERY_REVIEW_READINESS_CAVEATS)

    def test_caveats_are_always_present(self):
        blocked = make_ready_state("delivery-caveats-blocked")
        blocked.clarification_cycles = [
            ClarificationCycle(
                project_id=blocked.project_id,
                cycle_id="clarifications-open",
                questions=[make_required_question()],
            )
        ]
        unknown = ProjectState(project_id="delivery-caveats-unknown", project_name="Unknown", brief="Review later.")
        unknown.clarification_cycles = [
            ClarificationCycle(project_id=unknown.project_id, cycle_id="clarifications-clear", questions=[])
        ]
        ready = make_ready_state("delivery-caveats-ready")

        for state in (blocked, unknown, ready):
            with self.subTest(project_id=state.project_id):
                readiness = build_delivery_review_readiness(state.project_id, state)
                self.assertEqual(readiness.caveats, DELIVERY_REVIEW_READINESS_CAVEATS)
                self.assertIn("Human review remains mandatory.", readiness.caveats)

    def test_projection_does_not_mutate_project_state(self):
        state = make_ready_state("delivery-projection-non-mutation")
        before = state.model_dump(mode="json")

        readiness = build_delivery_review_readiness(state.project_id, state)

        self.assertEqual(readiness.project_id, state.project_id)
        self.assertEqual(state.model_dump(mode="json"), before)

    def test_payload_does_not_use_delivery_approval_language(self):
        state = make_ready_state("delivery-language")

        payload = build_delivery_review_readiness(state.project_id, state).model_dump(mode="json")
        text = json.dumps(payload, sort_keys=True).lower()

        forbidden = [
            "safe_to_send",
            "delivery_approved",
            "delivery_gate_passed",
            "delivery approval",
            "approved",
        ]
        for phrase in forbidden:
            self.assertNotIn(phrase, text)

    def test_workspace_summary_exposes_delivery_review_readiness(self):
        state = make_ready_state("delivery-workspace")

        workspace = build_workspace_summary(state)

        self.assertEqual(workspace.delivery_review_readiness.project_id, state.project_id)
        self.assertIn(workspace.delivery_review_readiness.status, {
            "ready_for_human_review",
            "needs_operator_review",
            "blocked_for_review",
        })


class TestDeliveryReadinessEndpoint(unittest.IsolatedAsyncioTestCase):
    async def test_endpoint_does_not_mutate_or_save_or_hydrate_decision_objects(self):
        state = make_ready_state("delivery-endpoint-non-mutation")
        before = state.model_dump(mode="json")
        save_mock = AsyncMock(side_effect=AssertionError("store.save must not be called"))
        ensure_mock = Mock(side_effect=AssertionError("ensure_decision_objects must not be called"))

        with patch("api.store.load", new=AsyncMock(return_value=state)):
            with patch("api.store.save", new=save_mock):
                with patch("api.ensure_decision_objects", new=ensure_mock):
                    response = await api.get_delivery_review_readiness(state.project_id)

        self.assertEqual(response.project_id, state.project_id)
        self.assertEqual(state.model_dump(mode="json"), before)
        save_mock.assert_not_called()
        ensure_mock.assert_not_called()

    async def test_endpoint_returns_404_for_missing_project(self):
        with patch("api.store.load", new=AsyncMock(return_value=None)):
            with self.assertRaises(api.HTTPException) as ctx:
                await api.get_delivery_review_readiness("missing-project")

        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
