"""Tests for backend-computed queue/workspace summaries."""
import sys
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


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
from decision_objects import ensure_decision_objects  # noqa: E402
from overview import build_operator_overview  # noqa: E402
from state import (  # noqa: E402
    REPORT_MODE_DECISION_MEMO_PILOT_PLAN,
    ClassifyOutput,
    DQScores,
    PhaseStatus,
    ProjectState,
)


def _classified_output(*, dq: list[float]) -> ClassifyOutput:
    """A classify output in the shape _store_phase_output persists."""
    return ClassifyOutput(
        domain="Complex",
        justification="Cause and effect are only coherent in retrospect.",
        bf=20.0,
        variety_gaps="1. No offline mode",
        dq=dq,
    )
from workspace import build_workspace_summary  # noqa: E402
from tests.test_decision_objects import make_state  # noqa: E402


class TestWorkspaceSummary(unittest.TestCase):
    def test_missing_dq_is_none_not_zero(self):
        state = ProjectState(project_id="workspace-dq-missing", project_name="DQ missing")

        workspace = build_workspace_summary(state)

        # An unscored project has no DQ total. Reporting 0.0 would be a
        # real-looking score of zero rather than an absent one.
        self.assertIsNone(workspace.score_summary.dq_total)

    def test_dq_is_read_from_the_classification_output_that_records_it(self):
        # The state shape a normal classify run produces: _store_phase_output
        # writes the four DQ values onto state.classify.
        state = ProjectState(
            project_id="workspace-dq-classified",
            project_name="DQ classified",
            classify=_classified_output(dq=[20, 15, 18, 12]),
        )

        workspace = build_workspace_summary(state)

        self.assertEqual(workspace.score_summary.dq_total, 65.0)

    def test_all_default_classify_dq_is_reported_as_missing(self):
        state = ProjectState(
            project_id="workspace-dq-default",
            project_name="DQ default",
            classify=_classified_output(dq=[0.0, 0.0, 0.0, 0.0]),
        )

        workspace = build_workspace_summary(state)

        # An all-zero breakdown is the untouched default, so it reads as absent.
        # A genuine exact-zero score is indistinguishable from it under the
        # current storage contract; that limit is documented, not redesigned here.
        self.assertIsNone(workspace.score_summary.dq_total)

    def test_legacy_persisted_dq_model_is_still_honored(self):
        # Nothing writes state.dq today; this covers a persisted state that
        # already carries values.
        state = ProjectState(
            project_id="workspace-dq-legacy",
            project_name="DQ legacy",
            dq=DQScores(frame=20, alt=15, info=18, val=12),
        )

        workspace = build_workspace_summary(state)

        self.assertEqual(workspace.score_summary.dq_total, 65.0)

    def test_dashboard_renders_missing_dq_as_em_dash(self):
        missing = ProjectState(project_id="overview-dq-missing", project_name="DQ missing")
        present = ProjectState(
            project_id="overview-dq-present",
            project_name="DQ present",
            classify=_classified_output(dq=[20, 15, 18, 12]),
        )

        def dq_card(state):
            cards = build_operator_overview(state).key_metrics
            return next(card for card in cards if card.label == "DQ total")

        self.assertEqual(dq_card(missing).value, "—")
        self.assertEqual(dq_card(present).value, "65.00")

    def test_completed_workspace_is_backend_computed(self):
        state = make_state("workspace-complete")
        state.report = "final report"
        state.phase_status["report"] = PhaseStatus.COMPLETED

        workspace = build_workspace_summary(state, workflow_running=True)

        self.assertEqual(workspace.project_status, "completed")
        self.assertTrue(workspace.workflow_running)
        self.assertEqual(workspace.decision_object_health.status, "fresh")
        self.assertGreater(workspace.active_risk_count, 0)

    def test_workspace_exposes_additive_ingestion_provenance_labels(self):
        state = make_state("workspace-provenance")
        state.ingestion_contract_version = "case.v1"
        state.ingestion_source = "crm"
        state.ingestion_external_case_id = "case-123"
        state.ingestion_metadata = {"segment": "midmarket", "priority": "high"}

        workspace = build_workspace_summary(state)

        self.assertEqual(workspace.input_contract.contract_version, "case.v1")
        self.assertEqual(workspace.input_contract.source, "crm")
        self.assertEqual(workspace.input_contract.external_case_id, "case-123")
        self.assertEqual(workspace.input_contract.metadata_keys, ["priority", "segment"])
        self.assertEqual(workspace.response_metadata.response_schema_version, "workspace.summary.v1")
        self.assertEqual(workspace.response_metadata.generated_by, "mas.workspace")
        self.assertEqual(workspace.response_metadata.provenance, "backend_computed")
        self.assertEqual(workspace.response_metadata.input_contract_version, "case.v1")

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

    def test_clarification_summary_is_exposed_without_changing_project_status(self):
        state = make_state("workspace-clarifications")
        state.clarification_cycles = [
            ClarificationCycle(
                project_id=state.project_id,
                cycle_id="cycle-1",
                questions=[
                    ClarificationQuestion(
                        question_id="q-critical",
                        text="What is the decision deadline?",
                        why_it_matters="Timing changes the strategy.",
                        priority=ClarificationPriority.CRITICAL,
                        affected_phase="classify",
                        source_gap="decision_deadline",
                        status=ClarificationStatus.OPEN,
                    )
                ],
            )
        ]

        workspace = build_workspace_summary(state)

        self.assertEqual(workspace.clarification_summary.total_questions, 1)
        self.assertEqual(workspace.clarification_summary.open_required_count, 1)
        self.assertEqual(workspace.clarification_summary.latest_cycle_status, "required_open")
        self.assertNotEqual(workspace.project_status, "blocked")
        self.assertNotIn("clarification", " ".join(workspace.blocking_reasons).lower())

    def test_overview_next_action_prioritizes_required_clarifications(self):
        state = make_state("overview-clarifications")
        state.report = "final report"
        state.phase_status["report"] = PhaseStatus.COMPLETED
        state.clarification_cycles = [
            ClarificationCycle(
                project_id=state.project_id,
                cycle_id="cycle-1",
                questions=[
                    ClarificationQuestion(
                        question_id="q-high",
                        text="What budget limit applies?",
                        why_it_matters="Resource bounds keep actions realistic.",
                        priority=ClarificationPriority.HIGH,
                        affected_phase="strategy",
                        source_gap="budget_resource_constraints",
                        status=ClarificationStatus.OPEN,
                    )
                ],
            )
        ]

        overview = build_operator_overview(state)

        self.assertIn("Answer critical/high clarification", overview.next_operator_action)
        clarification_metric = next(card for card in overview.key_metrics if card.label == "Clarifications")
        self.assertEqual(clarification_metric.value, "0/1")
        self.assertIn("1 required open", clarification_metric.detail)


class TestWorkspaceApi(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        api.running.clear()

    async def asyncTearDown(self):
        api.running.clear()

    async def test_queue_endpoint_returns_backend_queue_rows(self):
        state = make_state("queue-api")
        state.ingestion_contract_version = "case.v1"
        state.ingestion_source = "api"
        state.ingestion_external_case_id = "queue-case"
        state.ingestion_metadata = {"team": "ops"}
        with patch("api.store.list_all", new=AsyncMock(return_value=[state])):
            rows = await api.get_project_queue()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].project_id, state.project_id)
        self.assertIn(rows[0].project_status, {"safe_to_proceed", "stale", "blocked", "review_required", "completed"})
        self.assertEqual(rows[0].input_contract.contract_version, "case.v1")
        self.assertEqual(rows[0].input_contract.source, "api")
        self.assertEqual(rows[0].input_contract.external_case_id, "queue-case")
        self.assertEqual(rows[0].input_contract.metadata_keys, ["team"])
        self.assertEqual(rows[0].response_metadata.response_schema_version, "workspace.queue_item.v1")
        self.assertEqual(rows[0].response_metadata.input_contract_version, "case.v1")

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
        self.assertIsNotNone(summary.clarification_summary)

    async def test_workspace_report_output_metadata_and_rerun_notice(self):
        state = make_state("workspace-report-output")
        state.report = "old English report"
        state.output_language = "es-MX"
        state.report_mode = REPORT_MODE_DECISION_MEMO_PILOT_PLAN
        state.report_output_language = "en"
        state.report_output_mode = "standard"
        state.phase_status["report"] = PhaseStatus.COMPLETED

        workspace = build_workspace_summary(state)

        self.assertTrue(workspace.report_output.rerun_required)
        self.assertEqual(workspace.report_output.current_output_language, "es-MX")
        self.assertEqual(workspace.report_output.metadata_status, "generated")
        self.assertEqual(workspace.report_output.generated_output_language, "en")
        self.assertIn("Rerun the report phase", workspace.report_output.rerun_notice)

    async def test_patch_project_input_updates_output_config_without_relabeling_report(self):
        state = make_state("patch-output-config")
        state.report = "old English report"
        state.report_output_language = "en"
        state.report_output_mode = "standard"
        state.phase_status["report"] = PhaseStatus.COMPLETED
        save = AsyncMock()

        with patch("api.store.load", new=AsyncMock(return_value=state)), patch("api.store.save", new=save):
            response = await api.patch_project_input(
                state.project_id,
                api.PatchProjectInputRequest(
                    output_language="es-MX",
                    report_mode=REPORT_MODE_DECISION_MEMO_PILOT_PLAN,
                ),
            )

        saved_state = save.await_args.args[0]
        self.assertEqual(response["invalidated_phases"], ["report"])
        self.assertEqual(saved_state.report, "old English report")
        self.assertEqual(saved_state.output_language, "es-MX")
        self.assertEqual(saved_state.report_mode, REPORT_MODE_DECISION_MEMO_PILOT_PLAN)
        self.assertEqual(saved_state.report_output_language, "en")
        self.assertEqual(saved_state.report_output_mode, "standard")
        self.assertEqual(saved_state.phase_status["report"], PhaseStatus.STALE)

    async def test_patch_project_input_rejects_invalid_output_config(self):
        state = make_state("patch-output-config-invalid")
        with patch("api.store.load", new=AsyncMock(return_value=state)):
            with self.assertRaises(api.HTTPException) as ctx:
                await api.patch_project_input(
                    state.project_id,
                    api.PatchProjectInputRequest(output_language="pt-BR"),
                )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("output_language must be one of", ctx.exception.detail)

    async def test_create_project_accepts_legacy_ingestion_payload(self):
        save = AsyncMock()
        with patch("api.store.save", new=save), patch("decision_events.append", new=AsyncMock()):
            response = await api.create_project(
                {
                    "name": "Legacy intake",
                    "brief": "Decide whether to launch the pilot.",
                    "data": "Existing customer interview notes.",
                }
            )

        saved_state = save.await_args.args[0]
        self.assertEqual(response.name, "Legacy intake")
        self.assertEqual(saved_state.project_name, "Legacy intake")
        self.assertEqual(saved_state.brief, "Decide whether to launch the pilot.")
        self.assertEqual(saved_state.data, "Existing customer interview notes.")
        self.assertEqual(saved_state.ingestion_contract_version, "legacy.v1")
        self.assertEqual(saved_state.ingestion_source, "operator")
        self.assertEqual(saved_state.ingestion_external_case_id, "")
        self.assertEqual(saved_state.ingestion_metadata, {})

    async def test_create_project_accepts_case_v1_ingestion_payload(self):
        save = AsyncMock()
        payload = {
            "contract_version": "case.v1",
            "name": "Case intake",
            "brief": "Decide whether to expand onboarding automation.",
            "data": "Activation is 41% for the last cohort.",
            "source": "crm",
            "external_case_id": "case-123",
            "metadata": {"segment": "midmarket", "priority": "high"},
        }

        with patch("api.store.save", new=save), patch("decision_events.append", new=AsyncMock()):
            response = await api.create_project(payload)

        saved_state = save.await_args.args[0]
        self.assertEqual(response.name, "Case intake")
        self.assertEqual(saved_state.project_name, "Case intake")
        self.assertEqual(saved_state.brief, "Decide whether to expand onboarding automation.")
        self.assertEqual(saved_state.data, "Activation is 41% for the last cohort.")
        self.assertEqual(saved_state.ingestion_contract_version, "case.v1")
        self.assertEqual(saved_state.ingestion_source, "crm")
        self.assertEqual(saved_state.ingestion_external_case_id, "case-123")
        self.assertEqual(saved_state.ingestion_metadata, {"segment": "midmarket", "priority": "high"})

    async def test_create_project_accepts_output_language_and_report_mode(self):
        save = AsyncMock()
        with patch("api.store.save", new=save), patch("decision_events.append", new=AsyncMock()):
            await api.create_project(
                {
                    "name": "Decision memo intake",
                    "brief": "Decide whether to run a pilot.",
                    "output_language": "es-MX",
                    "report_mode": REPORT_MODE_DECISION_MEMO_PILOT_PLAN,
                }
            )

        saved_state = save.await_args.args[0]
        self.assertEqual(saved_state.output_language, "es-MX")
        self.assertEqual(saved_state.report_mode, REPORT_MODE_DECISION_MEMO_PILOT_PLAN)
        self.assertIsNone(saved_state.report_output_language)
        self.assertIsNone(saved_state.report_output_mode)

    async def test_create_project_rejects_invalid_output_config(self):
        for field, value, expected in (
            ("output_language", "fr", "output_language must be one of"),
            ("report_mode", "memo", "report_mode must be one of"),
        ):
            with self.subTest(field=field):
                with self.assertRaises(api.HTTPException) as ctx:
                    await api.create_project(
                        {
                            "name": "Bad output config",
                            "brief": "Decide whether to run a pilot.",
                            field: value,
                        }
                    )
                self.assertEqual(ctx.exception.status_code, 400)
                self.assertIn(expected, ctx.exception.detail)

    async def test_create_project_rejects_mixed_legacy_and_case_payload(self):
        with self.assertRaises(api.HTTPException) as ctx:
            await api.create_project(
                {
                    "name": "Conflicting legacy name",
                    "case": {
                        "contract_version": "case.v1",
                        "name": "Case intake",
                        "brief": "Decide whether to expand onboarding automation.",
                    },
                }
            )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("cannot mix legacy fields", ctx.exception.detail)

    async def test_project_state_has_backward_compatible_ingestion_defaults(self):
        state = ProjectState(project_id="defaults", project_name="Defaults", brief="Brief")

        self.assertEqual(state.ingestion_contract_version, "legacy.v1")
        self.assertEqual(state.ingestion_source, "operator")
        self.assertEqual(state.ingestion_external_case_id, "")
        self.assertEqual(state.ingestion_metadata, {})
        self.assertEqual(state.output_language, "en")
        self.assertEqual(state.report_mode, "standard")
        self.assertIsNone(state.report_output_language)
        self.assertIsNone(state.report_output_mode)

    async def test_old_project_state_payload_loads_with_ingestion_defaults(self):
        state = ProjectState.model_validate(
            {
                "project_id": "old-payload",
                "project_name": "Old payload",
                "brief": "Stored before ingestion metadata existed.",
                "data": "Legacy supporting data.",
            }
        )

        workspace = build_workspace_summary(state)

        self.assertEqual(state.ingestion_contract_version, "legacy.v1")
        self.assertEqual(state.ingestion_source, "operator")
        self.assertEqual(state.ingestion_external_case_id, "")
        self.assertEqual(state.ingestion_metadata, {})
        self.assertEqual(state.output_language, "en")
        self.assertEqual(state.report_mode, "standard")
        self.assertIsNone(state.report_output_language)
        self.assertIsNone(state.report_output_mode)
        self.assertEqual(workspace.input_contract.contract_version, "legacy.v1")
        self.assertEqual(workspace.input_contract.source, "operator")
        self.assertEqual(workspace.input_contract.external_case_id, "")
        self.assertEqual(workspace.input_contract.metadata_keys, [])
        self.assertEqual(workspace.report_output.metadata_status, "not_generated")

    async def test_legacy_report_missing_generated_metadata_is_unknown(self):
        state = ProjectState.model_validate(
            {
                "project_id": "legacy-report-metadata",
                "project_name": "Legacy report metadata",
                "brief": "Stored before generated metadata existed.",
                "report": "Legacy report body.",
            }
        )

        workspace = build_workspace_summary(state)

        self.assertIsNone(state.report_output_language)
        self.assertIsNone(state.report_output_mode)
        self.assertEqual(workspace.report_output.metadata_status, "legacy_metadata_unknown")
        self.assertIsNone(workspace.report_output.generated_output_language)
        self.assertIsNone(workspace.report_output.generated_report_mode)
        self.assertTrue(workspace.report_output.rerun_required)
        self.assertIn("before output metadata was recorded", workspace.report_output.rerun_notice)


class TestRequestIdMiddleware(unittest.TestCase):
    def test_request_id_middleware_echoes_safe_supplied_value(self):
        client = TestClient(api.app)
        try:
            response = client.get(
                "/missing",
                headers={"X-Request-ID": "req_123-ABC.456:trace"},
            )
        finally:
            client.close()

        self.assertEqual(response.headers["X-Request-ID"], "req_123-ABC.456:trace")
        self.assertNotIn("req_123-ABC.456:trace", response.text)

    def test_request_id_middleware_generates_uuid_when_absent_or_unsafe(self):
        client = TestClient(api.app)
        try:
            absent_response = client.get("/missing")
            unsafe_response = client.get("/missing", headers={"X-Request-ID": "bad request id"})
        finally:
            client.close()

        absent_request_id = absent_response.headers["X-Request-ID"]
        unsafe_request_id = unsafe_response.headers["X-Request-ID"]
        uuid.UUID(absent_request_id)
        uuid.UUID(unsafe_request_id)
        self.assertNotEqual(unsafe_request_id, "bad request id")
        self.assertNotIn(absent_request_id, absent_response.text)
        self.assertNotIn(unsafe_request_id, unsafe_response.text)
