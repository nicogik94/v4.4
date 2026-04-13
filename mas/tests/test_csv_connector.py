"""Tests for CSV connector ingestion and API integration."""
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api  # noqa: E402
from connectors.csv_connector import CSVConnector  # noqa: E402
from extensions.connectors import CSVColumnMapping, ConnectorImportRequest  # noqa: E402
from ingestion import merge_imported_records  # noqa: E402
from state import DecisionObjectStatus, PhaseStatus  # noqa: E402
from tests.test_decision_objects import make_state  # noqa: E402
from workspace import build_workspace_summary  # noqa: E402


def default_mapping() -> list[CSVColumnMapping]:
    return [
        CSVColumnMapping(column="hypothesis_id", target_type="evidence", target_field="linked_hypothesis_ids"),
        CSVColumnMapping(column="evidence_title", target_type="evidence", target_field="title", required=True, transform=["trim"]),
        CSVColumnMapping(column="evidence_summary", target_type="evidence", target_field="summary", transform=["trim"]),
        CSVColumnMapping(column="signal_name", target_type="signal", target_field="name", required=True, transform=["trim"], signal_kind="performance", confidence=0.8),
        CSVColumnMapping(column="signal_description", target_type="signal", target_field="description", transform=["trim"]),
        CSVColumnMapping(column="hypothesis_id", target_type="signal", target_field="linked_hypothesis_ids"),
    ]


class TestCSVConnector(unittest.TestCase):
    def test_valid_csv_import_normalizes_evidence_and_signal(self):
        connector = CSVConnector()
        csv_text = (
            "hypothesis_id,evidence_title,evidence_summary,signal_name,signal_description,ignored\n"
            "H1, CTR evidence , Search demand is rising , CTR , Weekly CTR movement , extra\n"
        )
        result = connector.ingest(
            ConnectorImportRequest(
                source_ref="project-1:import.csv",
                filename="import.csv",
                raw_text=csv_text,
                initiated_by="operator",
                dry_run=True,
                mapping=default_mapping(),
            )
        )

        self.assertEqual(result.connector_name, "csv")
        self.assertEqual(result.evidence[0].provenance.source_type, "connector_import")
        self.assertEqual(result.evidence[0].provenance.connector, "csv")
        self.assertEqual(result.evidence[0].linked_hypothesis_ids, ["H1"])
        self.assertTrue(result.evidence[0].untrusted_source)
        self.assertEqual(result.signals[0].name, "CTR")
        self.assertEqual(result.signals[0].kind, "performance")
        self.assertEqual(result.signals[0].confidence, 0.8)
        self.assertTrue(result.signals[0].untrusted_source)
        self.assertIn("ignored", result.unknown_columns)
        self.assertEqual(result.imported_rows, 1)

    def test_missing_header_is_reported(self):
        connector = CSVConnector()
        csv_text = "evidence_title,evidence_summary\nTitle only,Summary only\n"
        result = connector.ingest(
            ConnectorImportRequest(
                source_ref="project-2:import.csv",
                filename="import.csv",
                raw_text=csv_text,
                initiated_by="operator",
                dry_run=True,
                mapping=default_mapping(),
            )
        )

        self.assertFalse(result.evidence)
        self.assertFalse(result.signals)
        self.assertTrue(result.row_issues)
        self.assertIn("Required column is missing", result.row_issues[0].message)

    def test_partial_row_failure_allows_signal_only(self):
        connector = CSVConnector()
        csv_text = (
            "hypothesis_id,evidence_title,evidence_summary,signal_name,signal_description\n"
            "H1,,Search demand is rising,CTR,Weekly CTR movement\n"
        )
        mapping = default_mapping()
        result = connector.ingest(
            ConnectorImportRequest(
                source_ref="project-3:import.csv",
                filename="import.csv",
                raw_text=csv_text,
                initiated_by="operator",
                dry_run=True,
                mapping=mapping,
            )
        )

        self.assertEqual(len(result.evidence), 0)
        self.assertEqual(len(result.signals), 1)
        self.assertTrue(result.row_issues)
        self.assertEqual(result.imported_rows, 1)

    def test_mapping_validation_rejects_unsupported_field(self):
        connector = CSVConnector()
        validation = connector.validate(
            ConnectorImportRequest(
                source_ref="project-4:import.csv",
                filename="import.csv",
                raw_text="col\nvalue\n",
                initiated_by="operator",
                mapping=[CSVColumnMapping(column="col", target_type="evidence", target_field="not_a_field")],
            )
        )

        self.assertFalse(validation.ok)
        self.assertIn("Unsupported evidence target_field", validation.row_issues[0].message)

    def test_untrusted_text_is_truncated(self):
        connector = CSVConnector()
        long_summary = "A " * 3000
        csv_text = (
            "hypothesis_id,evidence_title,evidence_summary,signal_name,signal_description\n"
            f"H1,Title,{long_summary},CTR,Desc\n"
        )
        result = connector.ingest(
            ConnectorImportRequest(
                source_ref="project-5:import.csv",
                filename="import.csv",
                raw_text=csv_text,
                initiated_by="operator",
                dry_run=True,
                mapping=default_mapping(),
            )
        )

        self.assertLessEqual(len(result.evidence[0].summary), 2000)
        self.assertTrue(result.evidence[0].untrusted_source)
        self.assertIn("untrusted_source=true", result.evidence[0].provenance.notes)

    def test_repeated_identical_import_keeps_stable_ids(self):
        connector = CSVConnector()
        state = make_state("csv-stable-ids")
        csv_text = (
            "hypothesis_id,evidence_title,evidence_summary,signal_name,signal_description\n"
            "H1,Imported evidence,Imported summary,CTR,Imported signal description\n"
        )
        request = ConnectorImportRequest(
            source_ref="csv-stable-ids:import.csv",
            filename="import.csv",
            raw_text=csv_text,
            initiated_by="operator",
            dry_run=False,
            mapping=default_mapping(),
        )

        first = connector.ingest(request)
        second = connector.ingest(request)
        merge_imported_records(state, evidence=first.evidence, signals=first.signals)
        merge_imported_records(state, evidence=second.evidence, signals=second.signals)

        self.assertEqual(len(state.imported_evidence), 1)
        self.assertEqual(len(state.imported_signals), 1)
        self.assertEqual(first.evidence[0].evidence_id, second.evidence[0].evidence_id)
        self.assertEqual(first.signals[0].signal_id, second.signals[0].signal_id)


class TestCSVImportApi(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        api.running.clear()

    async def asyncTearDown(self):
        api.running.clear()

    async def test_api_import_persists_records_and_updates_workspace(self):
        state = make_state("csv-import-api")
        state.report = "final report"
        state.phase_status["report"] = PhaseStatus.COMPLETED
        csv_text = (
            "hypothesis_id,evidence_title,evidence_summary,signal_name,signal_description\n"
            "H1,Imported evidence,Imported summary,CTR,Imported signal description\n"
        )
        request = api.CSVImportRequest(
            filename="import.csv",
            csv_text=csv_text,
            actor="operator",
            dry_run=False,
            mapping=[api.CSVImportMappingRequest(**item.__dict__) for item in default_mapping()],
        )

        with patch("api.store.load", new=AsyncMock(return_value=state)), patch("api.store.save", new=AsyncMock()) as save_mock:
            response = await api.import_csv(state.project_id, request)

        self.assertEqual(response["status"], "imported")
        self.assertEqual(response["evidence_count"], 1)
        self.assertEqual(response["signal_count"], 1)
        self.assertEqual(len(state.imported_evidence), 1)
        self.assertEqual(len(state.imported_signals), 1)
        self.assertEqual(state.decision_objects.status, DecisionObjectStatus.FRESH)
        workspace = build_workspace_summary(state)
        self.assertTrue(any(item.title == "Imported evidence" for item in workspace.evidence_timeline))
        self.assertTrue(workspace.imported_evidence_pending_analysis)
        self.assertEqual(workspace.imported_evidence_pending_phase, "report")
        save_mock.assert_awaited()

    async def test_dry_run_does_not_persist_records(self):
        state = make_state("csv-import-dry-run")
        request = api.CSVImportRequest(
            filename="import.csv",
            csv_text="hypothesis_id,evidence_title,signal_name\nH1,Preview only,CTR\n",
            actor="operator",
            dry_run=True,
            mapping=[
                api.CSVImportMappingRequest(column="hypothesis_id", target_type="evidence", target_field="linked_hypothesis_ids"),
                api.CSVImportMappingRequest(column="evidence_title", target_type="evidence", target_field="title"),
                api.CSVImportMappingRequest(column="signal_name", target_type="signal", target_field="name"),
            ],
        )

        with patch("api.store.load", new=AsyncMock(return_value=state)), patch("api.store.save", new=AsyncMock()) as save_mock:
            response = await api.import_csv(state.project_id, request)

        self.assertEqual(response["status"], "validated")
        self.assertFalse(response["persisted"])
        self.assertEqual(len(state.imported_evidence), 0)
        self.assertEqual(len(state.imported_signals), 0)
        save_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main(verbosity=2)
