"""Contract tests for the structured payloads the dashboard decision surfaces read.

These assert that the *existing* authenticated builders (`build_operator_overview`
and `build_workspace_summary`) already supply every field the Overview snapshot
and the Evidence & Risks dossier render — no new backend projection is required,
and no sensitive storage field leaks into the operator-facing payload.

Pure in-memory `ProjectState` fixtures; no database writes.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from state import (
    AuditOutput,
    FileParseStatus,
    FileParseSummary,
    FMEAItem,
    KnowledgeLayerState,
    ProjectState,
    StrategyAction,
    StrategyOutput,
    UploadedFileManifest,
)
from overview import build_operator_overview
from workspace import build_workspace_summary


def _uploaded_file(*, evidence=0, signal=0, knowledge=1, status=FileParseStatus.COMPLETED):
    return UploadedFileManifest(
        file_id="f1",
        filename="weekly_reporting.txt",
        parser_kind="text",
        import_mode="knowledge",
        parse_summary=FileParseSummary(
            status=status,
            knowledge_item_count=knowledge,
            evidence_count=evidence,
            signal_count=signal,
        ),
    )


def _complete_state():
    state = ProjectState(project_id="complete", project_name="Weekly Client Reporting")
    state.knowledge_layer = KnowledgeLayerState(uploaded_files=[_uploaded_file(evidence=0, signal=0)])
    state.audit = AuditOutput(
        fmea=[
            FMEAItem(component="Template Engine", failure_mode="silent mismatch",
                     effect="wrong client output", s=9, o=7, d=5, rpn=315, action="add validator"),
            FMEAItem(component="ETL Connector", failure_mode="drops rows",
                     effect="missing records", s=6, o=4, d=4, rpn=96, action="row-count check"),
        ],
        top_findings=["Highest RPN 315: template engine silent mismatch"],
        observation_needs=["Log every formatting request for two weeks pre-automation"],
    )
    state.strategy = StrategyOutput(
        executive_strategy="Execute one focused MVP automation of the reporting pipeline.",
        strategies=[StrategyAction(action="Build MVP", justification="Highest-leverage step")],
    )
    return state


class TestOverviewDecisionSurfaceContract(unittest.TestCase):
    def test_complete_payload_supplies_every_overview_field(self):
        overview = build_operator_overview(_complete_state())

        # Decision direction + next action are server-provided.
        self.assertTrue(overview.current_recommendation)
        self.assertNotEqual(overview.current_recommendation, "No recommendation generated yet.")
        self.assertTrue(overview.decision_summary)
        self.assertTrue(overview.next_operator_action)

        # Uploaded files carry sanitized parse + import telemetry.
        self.assertEqual(len(overview.files), 1)
        row = overview.files[0]
        self.assertEqual(row.parse_status, "completed")
        self.assertEqual(row.import_mode, "knowledge")

        ws = overview.workspace
        self.assertGreaterEqual(ws.active_risk_count, 1)
        self.assertTrue(ws.decision_object_health.status)
        self.assertTrue(ws.knowledge_health.status)
        self.assertTrue(ws.delivery_review_readiness.status)

    def test_partial_evidence_payload_distinguishes_uploaded_from_imported(self):
        # A file is uploaded and parsed, but produced no imported evidence/signals
        # and no source locator beyond the knowledge item.
        state = ProjectState(project_id="partial", project_name="Partial evidence")
        state.knowledge_layer = KnowledgeLayerState(
            uploaded_files=[_uploaded_file(evidence=0, signal=0, knowledge=1)]
        )
        overview = build_operator_overview(state)

        self.assertEqual(len(overview.files), 1)
        row = overview.files[0]
        self.assertEqual(row.parse_status, "completed")
        self.assertEqual(row.evidence_count, 0)   # → dashboard state "not imported"
        self.assertEqual(row.signal_count, 0)     # → dashboard state "not imported"
        self.assertGreaterEqual(row.knowledge_item_count, 1)

    def test_no_phase_output_payload_reports_not_generated(self):
        overview = build_operator_overview(ProjectState(project_id="bare", project_name="Bare"))

        self.assertEqual(overview.current_recommendation, "No recommendation generated yet.")
        self.assertEqual(len(overview.files), 0)
        self.assertEqual(overview.workspace.active_risk_count, 0)
        self.assertEqual(len(overview.workspace.active_risks), 0)

    def test_active_risks_are_ordered_by_descending_severity(self):
        ws = build_workspace_summary(_complete_state())
        self.assertGreaterEqual(len(ws.active_risks), 2)
        rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
        ranks = [rank.get((r.severity or "").lower(), 0) for r in ws.active_risks]
        self.assertEqual(ranks, sorted(ranks, reverse=True))
        # Highest-severity risk is first — what the snapshot's top-3 slice surfaces.
        self.assertEqual((ws.active_risks[0].severity or "").lower(), "critical")

    def test_overview_payload_never_leaks_storage_ref_or_paths(self):
        dumped = build_operator_overview(_complete_state()).model_dump(mode="json")
        blob = str(dumped)
        for forbidden in ("storage_ref", "checksum_sha256", "uploaded_by", "local_path"):
            self.assertNotIn(forbidden, blob)


if __name__ == "__main__":
    unittest.main()
