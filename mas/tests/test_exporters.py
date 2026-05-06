"""Tests for profile-based project exports."""
import json
import sys
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

from docx import Document
from fastapi import HTTPException


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
from exporters import (  # noqa: E402
    build_client_dossier_markdown,
    build_machine_archive_payload,
    build_operator_dossier_markdown,
    export_project_profile_bytes,
    sanitize_for_export,
)
from state import (  # noqa: E402
    KnowledgeItem,
    KnowledgeLayerState,
    PhaseStatus,
    UploadedFileManifest,
)
from tests.test_decision_objects import make_state  # noqa: E402


def _docx_text(payload: bytes) -> str:
    document = Document(BytesIO(payload))
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


def make_export_state(project_id: str = "export-profile"):
    state = make_state(project_id)
    state.report = """# Executive Summary
Proceed with a bounded audit pilot [Evidence: ev-market | chunk=1].

# The Decision
Decide whether to fund the pilot.

# Recommended Path
Run the 30-day pilot before the full program.

# Why This Is Recommended
The pilot limits commitment while validating demand.

# Options Considered
| Option | Verdict |
|---|---|
| Pilot | Recommended |

# Evidence Used
| Evidence | What it suggests |
|---|---|
| Market note | There is a concrete source marker. |

# Key Risks
Scope creep and unclear ownership.

# Assumptions and Open Questions
Who owns the pilot remains open.

# Roadmap
Day 1-30: run the audit.

# Next Steps
- Confirm owner.
- Confirm data access.

# Monitoring and Kill Criteria
Stop if data access is unavailable.

# Appendix: Technical Analysis
Technical notes stay here.
"""
    for phase in ("classify", "hypotheses", "gauntlet", "audit", "strategy", "sqi", "monitor", "report"):
        state.phase_status[phase] = PhaseStatus.COMPLETED
        state.phase_confidence[phase] = 1.0
        state.phase_summaries[phase] = f"{phase} summary"
    state.knowledge_layer = KnowledgeLayerState(
        items=[
            KnowledgeItem(
                item_id="ev-market",
                source_id="src-1",
                title="Market note",
                source_ref="upload:file-1:market.pdf#chunk=1",
                structured_payload={"locator": "chunk=1"},
            )
        ],
        uploaded_files=[
            UploadedFileManifest(
                file_id="file-1",
                source_id="src-1",
                filename="market.pdf",
                media_type="application/pdf",
                size_bytes=321,
                storage_ref=r"C:\Users\nicoc\private\market.pdf",
            )
        ],
    )
    state.clarification_cycles = [
        ClarificationCycle(
            cycle_id="cycle-1",
            project_id=state.project_id,
            questions=[
                ClarificationQuestion(
                    question_id="q1",
                    text="Who owns the pilot?",
                    why_it_matters="Ownership controls follow-through.",
                    priority=ClarificationPriority.CRITICAL,
                    affected_phase="strategy",
                    source_gap="stakeholder_audience",
                    status=ClarificationStatus.OPEN,
                )
            ],
        )
    ]
    state.policy_audit_log = [
        {
            "event_type": "policy_gate_blocked",
            "phase": "strategy",
            "details": {
                "api_key": "sk-test-secret",
                "raw_prompt": "hidden prompt",
                "raw_provider_payload": {"token": "provider-token"},
                "chain_of_thought": "hidden reasoning",
                "scenario_probability": 0.91,
                "local_path": r"C:\Users\nicoc\private\payload.json",
            },
        }
    ]
    state.data = r"Local notes at C:\Users\nicoc\private\data.csv with api_key=sk-test-secret"
    return state


class TestProfileExporterHelpers(unittest.TestCase):
    def test_report_profile_exports_report_only(self):
        state = make_export_state("report-only")
        state.report = "Standalone report only."

        payload, media_type, filename = export_project_profile_bytes(state, "report", "docx")
        text = _docx_text(payload)

        self.assertEqual(media_type, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        self.assertIn("report-only-report-", filename)
        self.assertIn("Standalone report only.", text)
        self.assertNotIn("Executive Summary", text)
        self.assertNotIn("classify summary", text)

    def test_client_dossier_includes_expected_sections_and_safe_cdp_wording(self):
        markdown = build_client_dossier_markdown(make_export_state("client-profile"))

        for expected in (
            "Executive Summary",
            "Roadmap",
            "Next Steps",
            "Key Risks",
            "Assumptions and Open Questions",
            "Monitoring and Kill Criteria",
            "Citation locator review summary — confirms marker/locator availability only, not semantic support.",
            "Who owns the pilot?",
        ):
            self.assertIn(expected, markdown)
        for forbidden in (
            "policy_audit_log",
            "raw_prompt",
            "raw_provider_payload",
            "sk-test-secret",
            "chain_of_thought",
            "scenario_probability",
            "semantic proof",
            "defensibility score",
            "evidence validated",
            "claim proven",
        ):
            self.assertNotIn(forbidden, markdown)

    def test_operator_dossier_includes_summaries_and_excludes_unsafe_detail(self):
        markdown = build_operator_dossier_markdown(make_export_state("operator-profile"))

        for expected in (
            "Phase Summaries",
            "Hypotheses",
            "Audit Findings and Observation Needs",
            "Strategy Actions and Success Metrics",
            "Monitoring Plan and Circuit Breakers",
            "Clarifications",
            "Evidence Locator Register",
            "Decision Trace Summary",
            "Risk and Policy Summary",
            "policy_gate_blocked=1",
        ):
            self.assertIn(expected, markdown)
        for forbidden in (
            "sk-test-secret",
            "raw_provider_payload",
            "raw_prompt",
            "chain_of_thought",
            r"C:\Users\nicoc",
        ):
            self.assertNotIn(forbidden, markdown)

    def test_machine_archive_contains_expected_sanitized_files(self):
        state = make_export_state("archive-profile")
        payload, media_type, filename = export_project_profile_bytes(state, "machine_archive", "zip")

        self.assertEqual(media_type, "application/zip")
        self.assertIn("archive-profile-machine_archive-", filename)
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            names = set(archive.namelist())
            self.assertNotIn("raw_project_state.json", names)
            self.assertTrue(
                {
                    "export_manifest.json",
                    "project_state.json",
                    "report.md",
                    "phase_outputs.json",
                    "clarifications.json",
                    "evidence_locator_register.json",
                    "uploaded_file_manifest.json",
                    "policy_summary.json",
                }.issubset(names)
            )
            combined = "\n".join(archive.read(name).decode("utf-8") for name in names)
            self.assertNotIn("sk-test-secret", combined)
            self.assertNotIn("hidden reasoning", combined)
            self.assertNotIn("hidden prompt", combined)
            self.assertNotIn("provider-token", combined)
            self.assertNotIn(r"C:\Users\nicoc", combined)

            manifest = json.loads(archive.read("export_manifest.json").decode("utf-8"))
            self.assertEqual(manifest["export_schema_version"], "1.0")
            self.assertEqual(manifest["export_profile"], "machine_archive")
            self.assertEqual(manifest["export_format"], "zip")
            self.assertIn("project_state.json", manifest["included_files"])

            uploads = json.loads(archive.read("uploaded_file_manifest.json").decode("utf-8"))
            self.assertEqual(uploads[0]["file_id"], "file-1")
            self.assertEqual(uploads[0]["original_filename"], "market.pdf")
            self.assertEqual(uploads[0]["content_type"], "application/pdf")
            self.assertEqual(uploads[0]["size_bytes"], 321)
            self.assertEqual(uploads[0]["storage_ref"], "[REDACTED]")

    def test_invalid_profile_and_format_combinations_raise(self):
        state = make_export_state("invalid-profile")

        with self.assertRaises(ValueError):
            export_project_profile_bytes(state, "unknown", "pdf")
        with self.assertRaises(ValueError):
            export_project_profile_bytes(state, "client_dossier", "zip")
        with self.assertRaises(ValueError):
            export_project_profile_bytes(state, "machine_archive", "pdf")
        with self.assertRaises(ValueError):
            export_project_profile_bytes(state, "report", "exe")

    def test_sanitizer_redacts_sensitive_keys_and_paths(self):
        payload = {
            "api_key": "sk-test-secret",
            "nested": {
                "raw_provider_payload": {"token": "provider-token"},
                "note": r"See C:\Users\nicoc\private\file.txt",
            },
        }

        sanitized = sanitize_for_export(payload, "machine_archive", mode="redact")

        self.assertEqual(sanitized["api_key"], "[REDACTED]")
        self.assertEqual(sanitized["nested"]["raw_provider_payload"], "[REDACTED]")
        self.assertNotIn(r"C:\Users\nicoc", sanitized["nested"]["note"])

    def test_build_machine_archive_payload_has_no_raw_project_state_file(self):
        payload = build_machine_archive_payload(make_export_state("payload-profile"))

        self.assertIn("project_state.json", payload)
        self.assertNotIn("raw_project_state.json", payload)


class TestProfileExportApi(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        api.running.clear()

    async def asyncTearDown(self):
        api.running.clear()

    async def test_legacy_export_route_still_returns_attachments(self):
        state = make_export_state("legacy-route")
        with patch("api.store.load", new=AsyncMock(return_value=state)):
            docx_response = await api.export_project(state.project_id, "docx")
            pdf_response = await api.export_project(state.project_id, "pdf")

        self.assertEqual(docx_response.media_type, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        self.assertEqual(pdf_response.media_type, "application/pdf")
        self.assertIn("attachment;", docx_response.headers["content-disposition"])
        self.assertIn("attachment;", pdf_response.headers["content-disposition"])
        self.assertIn(".docx", docx_response.headers["content-disposition"])
        self.assertIn(".pdf", pdf_response.headers["content-disposition"])

    async def test_query_route_defaults_profile_to_report(self):
        state = make_export_state("query-default")
        state.report = "Only this report content."
        with patch("api.store.load", new=AsyncMock(return_value=state)):
            response = await api.export_project_profile(state.project_id, format="docx")

        self.assertEqual(response.media_type, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        self.assertIn("query-default-report-", response.headers["content-disposition"])
        text = _docx_text(response.body)
        self.assertIn("Only this report content.", text)
        self.assertNotIn("classify summary", text)

    async def test_query_route_rejects_missing_or_invalid_format(self):
        state = make_export_state("query-invalid")
        with patch("api.store.load", new=AsyncMock(return_value=state)):
            with self.assertRaises(HTTPException) as missing:
                await api.export_project_profile(state.project_id)
            with self.assertRaises(HTTPException) as unknown:
                await api.export_project_profile(state.project_id, profile="bogus", format="pdf")
            with self.assertRaises(HTTPException) as invalid_combo:
                await api.export_project_profile(state.project_id, profile="client_dossier", format="zip")

        self.assertEqual(missing.exception.status_code, 400)
        self.assertEqual(unknown.exception.status_code, 400)
        self.assertEqual(invalid_combo.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
