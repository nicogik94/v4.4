"""Tests for backend-computed queue/workspace summaries."""
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api  # noqa: E402
from decision_objects import ensure_decision_objects  # noqa: E402
from state import PhaseStatus  # noqa: E402
from workspace import build_workspace_summary  # noqa: E402
from tests.test_decision_objects import make_state  # noqa: E402


class TestWorkspaceSummary(unittest.TestCase):
    def test_completed_workspace_is_backend_computed(self):
        state = make_state("workspace-complete")
        state.report = "final report"
        state.phase_status["report"] = PhaseStatus.COMPLETED

        workspace = build_workspace_summary(state, workflow_running=True)

        self.assertEqual(workspace.project_status, "completed")
        self.assertTrue(workspace.workflow_running)
        self.assertEqual(workspace.decision_object_health.status, "fresh")
        self.assertGreater(workspace.active_risk_count, 0)

    def test_stale_workspace_state_is_exposed(self):
        state = make_state("workspace-stale")
        ensure_decision_objects(state, trigger="pre-stale")
        state.phase_status["strategy"] = PhaseStatus.STALE

        workspace = build_workspace_summary(state)

        self.assertEqual(workspace.project_status, "stale")
        self.assertTrue(workspace.has_stale_downstream)

    def test_pending_approval_drives_review_required(self):
        state = make_state("workspace-approval")
        state.policy_audit_log.append(
            {
                "ts": 1776000100.0,
                "event_type": "policy_gate_blocked",
                "phase": "strategy",
                "details": {
                    "phase": "strategy",
                    "reason": "strategy requires HITL approval",
                    "category": "approval",
                    "requires_hitl": True,
                },
            }
        )

        workspace = build_workspace_summary(state)

        self.assertTrue(workspace.requires_approval)
        self.assertEqual(workspace.project_status, "review_required")
        self.assertEqual(workspace.approvals_panel[0].status.value, "pending")

    def test_rebuild_failed_health_blocks_workspace(self):
        state = make_state("workspace-bad")
        state.gauntlet = type("BrokenGauntlet", (), {"results": ["oops"]})()

        workspace = build_workspace_summary(state)

        self.assertEqual(workspace.decision_object_health.status, "rebuild_failed")
        self.assertEqual(workspace.project_status, "blocked")
        self.assertTrue(workspace.blocking_reasons)

    def test_imported_evidence_pending_analysis_is_exposed(self):
        state = make_state("workspace-import-pending")
        state.report = "final report"
        state.phase_status["report"] = PhaseStatus.COMPLETED
        state.policy_audit_log.append(
            {
                "ts": 1776000200.0,
                "event_type": "connector_import",
                "phase": "report",
                "details": {
                    "analysis_pending": True,
                    "analysis_pending_phase": "report",
                    "evidence_count": 1,
                    "signal_count": 0,
                },
            }
        )

        workspace = build_workspace_summary(state)

        self.assertTrue(workspace.imported_evidence_pending_analysis)
        self.assertEqual(workspace.imported_evidence_pending_phase, "report")
        self.assertIn("Rerun analysis", workspace.imported_evidence_pending_message)

    def test_imported_evidence_pending_analysis_clears_after_successful_rerun(self):
        state = make_state("workspace-import-cleared")
        state.report = "final report"
        state.phase_status["report"] = PhaseStatus.COMPLETED
        import_ts = 1776000200.0
        state.policy_audit_log.append(
            {
                "ts": import_ts,
                "event_type": "connector_import",
                "phase": "report",
                "details": {
                    "analysis_pending": True,
                    "analysis_pending_phase": "report",
                    "evidence_count": 1,
                    "signal_count": 1,
                },
            }
        )
        state.phase_run_completed_at["report"] = datetime.fromtimestamp(import_ts + 60).isoformat()

        workspace = build_workspace_summary(state)

        self.assertFalse(workspace.imported_evidence_pending_analysis)
        self.assertEqual(workspace.imported_evidence_pending_message, "")


class TestWorkspaceApi(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        api.running.clear()

    async def asyncTearDown(self):
        api.running.clear()

    async def test_queue_endpoint_returns_backend_queue_rows(self):
        state = make_state("queue-api")
        with patch("api.store.list_all", new=AsyncMock(return_value=[state])):
            rows = await api.get_project_queue()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].project_id, state.project_id)
        self.assertIn(rows[0].project_status, {"safe_to_proceed", "stale", "blocked", "review_required", "completed"})

    async def test_workspace_endpoint_returns_authoritative_summary(self):
        state = make_state("workspace-api")
        state.report = "final report"
        state.phase_status["report"] = PhaseStatus.COMPLETED
        state.policy_audit_log.append(
            {
                "ts": 1776000200.0,
                "event_type": "connector_import",
                "phase": "report",
                "details": {
                    "analysis_pending": True,
                    "analysis_pending_phase": "report",
                    "evidence_count": 1,
                    "signal_count": 0,
                },
            }
        )
        with patch("api.store.load", new=AsyncMock(return_value=state)):
            summary = await api.get_workspace(state.project_id)

        self.assertEqual(summary.project_id, state.project_id)
        self.assertEqual(summary.current_phase, state.current_phase)
        self.assertIsNotNone(summary.decision_object_health)
        self.assertTrue(summary.imported_evidence_pending_analysis)
