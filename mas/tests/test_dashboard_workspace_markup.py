"""Lightweight dashboard markup regression checks for the workspace UI."""
import unittest
from pathlib import Path


_HERE = Path(__file__).resolve()
HTML_PATH = None
HTML_V5_PATH = None
DOC_V5_DASHBOARD_PATH = None
for root in (_HERE.parents[1], _HERE.parents[2]):
    candidate = root / "dashboards" / "index.html"
    if candidate.exists():
        HTML_PATH = candidate
    v5_candidate = root / "dashboards" / "index-v5.html"
    if v5_candidate.exists():
        HTML_V5_PATH = v5_candidate
    doc_candidate = root / "docs" / "v5-DASHBOARD-CANONICALIZATION.md"
    if doc_candidate.exists():
        DOC_V5_DASHBOARD_PATH = doc_candidate
    if HTML_PATH is not None and HTML_V5_PATH is not None and DOC_V5_DASHBOARD_PATH is not None:
        break
if HTML_PATH is None:
    HTML_PATH = _HERE.parents[1] / "dashboards" / "index.html"
if HTML_V5_PATH is None:
    HTML_V5_PATH = _HERE.parents[1] / "dashboards" / "index-v5.html"
if DOC_V5_DASHBOARD_PATH is None:
    DOC_V5_DASHBOARD_PATH = _HERE.parents[1] / "docs" / "v5-DASHBOARD-CANONICALIZATION.md"


class TestDashboardWorkspaceMarkup(unittest.TestCase):
    def test_workspace_tab_and_queue_api_are_present(self):
        if not HTML_PATH.exists():
            self.skipTest("dashboard bundle is not mounted in this execution environment")
        html = HTML_PATH.read_text(encoding="utf-8")

        self.assertIn('data-subtab="overview"', html)
        self.assertIn('data-subtab="workspace"', html)
        self.assertIn('data-subtab="trace"', html)
        self.assertIn("/projects/queue", html)
        self.assertIn("/overview", html)
        self.assertIn("/workspace", html)
        self.assertIn("/explain", html)
        self.assertIn("/files", html)
        self.assertIn("apiFormPost", html)
        self.assertIn("renderOverview", html)
        self.assertIn("Start here", html)
        self.assertIn("Decision trace", html)
        self.assertIn("Control log", html)
        self.assertIn("Add sources", html)
        self.assertIn("Source library", html)
        self.assertIn("No sources yet.", html)
        self.assertIn("Why this", html)
        self.assertIn("What to watch", html)
        self.assertIn("Next step", html)
        self.assertIn("External context used", html)
        self.assertIn("Control and system detail", html)
        self.assertIn("structured CSV/XLSX import", html)
        self.assertIn("renderTrace", html)
        self.assertIn("Evidence Timeline", html)
        self.assertIn("Approval Inbox", html)
        self.assertIn("Decision Object Health", html)
        self.assertIn("Per-Phase Trace", html)
        self.assertIn("Strategy Evidence Chains", html)
        self.assertIn("Gate note:", html)
        self.assertIn("Knowledge used:", html)
        self.assertIn("Retrieval visibility", html)
        self.assertIn("Blocked reasons:", html)
        self.assertIn("eligible", html)
        self.assertIn("imported_evidence_pending_analysis", html)
        self.assertIn("Rerun analysis to incorporate", html)
        self.assertIn("Status axes", html)
        self.assertIn("derived decision objects synchronized with stored state", html)
        self.assertIn("knowledge:", html)
        self.assertIn("external knowledge freshness", html)

    def test_report_regeneration_control_is_present(self):
        if not HTML_PATH.exists():
            self.skipTest("dashboard bundle is not mounted in this execution environment")
        html = HTML_PATH.read_text(encoding="utf-8")

        self.assertIn("Regenerate report", html)
        self.assertIn("Rebuild the final report from the latest saved phase outputs.", html)
        self.assertIn("This will regenerate and replace the current report from the latest saved analysis. Continue?", html)
        self.assertIn("regenerateReport", html)
        self.assertIn("/phase", html)
        self.assertIn("phase", html)
        self.assertIn("report", html)
        self.assertIn("renderReport(pid, fullState, workspace)", html)
        self.assertIn("Save report", html)
        self.assertIn("Report markdown", html)
        self.assertIn("/projects/${pid}/export/${fmt}", html)

    def test_canonical_export_profile_controls_are_present(self):
        if not HTML_PATH.exists():
            self.skipTest("dashboard bundle is not mounted in this execution environment")
        html = HTML_PATH.read_text(encoding="utf-8")

        self.assertIn("renderExportProfileControl", html)
        self.assertIn("exportProjectProfile", html)
        self.assertIn("syncExportProfileFormatOptions", html)
        self.assertIn("export-profile-select", html)
        self.assertIn("export-format-select", html)
        self.assertIn("export-profile-submit", html)
        self.assertIn("Profile export", html)
        self.assertIn("report: {", html)
        self.assertIn("client_dossier", html)
        self.assertIn("operator_dossier", html)
        self.assertIn("machine_archive", html)
        self.assertIn("Report", html)
        self.assertIn("Client dossier", html)
        self.assertIn("Operator dossier", html)
        self.assertIn("Machine archive", html)
        self.assertIn("pdf", html)
        self.assertIn("docx", html)
        self.assertIn("zip", html)
        self.assertIn("/export?profile=", html)
        self.assertIn("/export/", html)
        self.assertIn("/projects/${pid}/export/${fmt}", html)
        self.assertIn("downloadExport", html)
        self.assertIn("Regenerate report", html)
        self.assertIn("clarification-panel", html)
        self.assertIn("DRAFT_NAMESPACE = 'v4.draft'", html)
        self.assertIn("Drafts.attach(", html)

    def test_canonical_clarification_panel_is_present(self):
        if not HTML_PATH.exists():
            self.skipTest("dashboard bundle is not mounted in this execution environment")
        html = HTML_PATH.read_text(encoding="utf-8")

        self.assertIn("/clarifications", html)
        self.assertIn("Clarification questions", html)
        self.assertIn("clarification-panel", html)
        self.assertIn("renderClarificationPanel", html)
        self.assertIn("wireClarificationPanel", html)
        self.assertIn("Generate questions", html)
        self.assertIn("Save answer", html)
        self.assertIn("Mark unavailable", html)
        self.assertIn("/clarifications/cycles", html)
        self.assertIn("/clarifications/answers", html)
        self.assertIn("Regenerate report", html)
        self.assertIn("/projects/${pid}/export/${fmt}", html)

    def test_local_draft_persistence_is_present(self):
        if not HTML_PATH.exists():
            self.skipTest("dashboard bundle is not mounted in this execution environment")
        html = HTML_PATH.read_text(encoding="utf-8")

        self.assertIn("Local browser draft persistence", html)
        self.assertIn("DRAFT_NAMESPACE = 'v4.draft'", html)
        self.assertIn("const Drafts =", html)
        self.assertIn("Drafts.attach(", html)
        self.assertIn("Drafts.clear(", html)
        self.assertIn("Drafts.key(", html)
        self.assertIn("localStorage.setItem", html)
        self.assertIn("localStorage.removeItem", html)
        self.assertIn("DRAFT_DEBOUNCE_MS = 400", html)
        self.assertIn("beforeunload", html)
        self.assertIn("draft-status", html)
        self.assertIn("Restore draft", html)
        self.assertIn("Clear draft", html)
        self.assertIn("Draft saved locally.", html)
        self.assertIn("Restored local draft.", html)
        self.assertIn("Draft too large to save locally.", html)
        self.assertIn("Replace current value with the local draft?", html)

        self.assertIn("NEW_PROJECT_DRAFT_FIELDS", html)
        self.assertIn("{ id: 'np-name', field: 'name' }", html)
        self.assertIn("{ id: 'np-brief', field: 'brief' }", html)
        self.assertIn("{ id: 'np-data', field: 'data' }", html)
        self.assertIn("{ id: 'np-rationale', field: 'rationale' }", html)
        self.assertIn("Drafts.attach(id, Drafts.key('new_project', field))", html)
        self.assertIn("Drafts.key('project', 'dossier_brief', pid)", html)
        self.assertIn("Drafts.key('project', 'dossier_data', pid)", html)
        self.assertIn("Drafts.key('project', 'report_editor', pid)", html)
        self.assertIn("upload_mapping_${prefix}", html)

        self.assertIn(
            "NEW_PROJECT_DRAFT_FIELDS.forEach(({ field }) => Drafts.clear(Drafts.key('new_project', field)));",
            html,
        )
        self.assertIn(
            "Drafts.clear(Drafts.key('project', 'dossier_brief', pid));",
            html,
        )
        self.assertIn(
            "Drafts.clear(Drafts.key('project', 'report_editor', pid));",
            html,
        )
        self.assertIn("Drafts.clear(mappingDraftKey);", html)

    def test_v5_clarification_markup_is_present(self):
        if not HTML_V5_PATH.exists():
            self.skipTest("dashboard v5 bundle is not mounted in this execution environment")
        html = HTML_V5_PATH.read_text(encoding="utf-8")

        self.assertIn("/clarifications", html)
        self.assertIn("Follow-up questions", html)
        self.assertIn("Missing information", html)
        self.assertIn("generateClarificationCycle", html)
        self.assertIn("submitClarificationAnswer", html)
        self.assertIn("markClarificationUnavailable", html)
        self.assertIn("Mark unavailable", html)

    def test_v5_export_profile_controls_are_present(self):
        if not HTML_V5_PATH.exists():
            self.skipTest("dashboard v5 bundle is not mounted in this execution environment")
        html = HTML_V5_PATH.read_text(encoding="utf-8")

        self.assertIn("renderExportProfileControl", html)
        self.assertIn("exportProjectProfile", html)
        self.assertIn("syncExportProfileFormatOptions", html)
        self.assertIn("export-profile-select", html)
        self.assertIn("export-format-select", html)
        self.assertIn("export-profile-submit", html)

        self.assertIn("Report", html)
        self.assertIn("Client dossier", html)
        self.assertIn("Operator dossier", html)
        self.assertIn("Machine archive", html)
        self.assertIn("Client monitoring template", html)
        self.assertIn("Operator monitoring template", html)
        self.assertIn("Final report only.", html)
        self.assertIn("Stakeholder-ready export.", html)
        self.assertIn("Internal review export with phase summaries.", html)
        self.assertIn("Internal ZIP archive for backup/debug.", html)
        self.assertIn("Client-safe monitoring workbook for post-review tracking.", html)
        self.assertIn("Operator workbook with trace fields for monitoring follow-up.", html)
        self.assertIn("Internal use only.", html)
        self.assertIn("Contains sanitized project archive files.", html)
        self.assertIn("Export may be incomplete until the report phase runs.", html)

        self.assertIn("/export?profile=", html)
        self.assertIn("report", html)
        self.assertIn("client_dossier", html)
        self.assertIn("operator_dossier", html)
        self.assertIn("machine_archive", html)
        self.assertIn("client_monitoring_template", html)
        self.assertIn("operator_monitoring_template", html)
        self.assertIn("pdf", html)
        self.assertIn("docx", html)
        self.assertIn("zip", html)
        self.assertIn("xlsx", html)

        self.assertIn("/export/${fmt}", html)
        self.assertIn("Download DOCX", html)
        self.assertIn("Download PDF", html)
        self.assertIn("downloadExport", html)

    def test_v5_dashboard_canonicalization_readiness_markup_is_present(self):
        if not HTML_V5_PATH.exists():
            self.skipTest("dashboard v5 bundle is not mounted in this execution environment")
        html = HTML_V5_PATH.read_text(encoding="utf-8")

        self.assertIn("v5 controlled local demo", html)
        self.assertIn("controlled experimental dashboard", html)
        self.assertIn("canonical dashboard remains index.html", html)
        self.assertIn("Demo framing", html)
        self.assertIn("does not create vertical runtime packs", html)
        self.assertNotIn("Picks the phase sequence and diagnostic track.", html)

        self.assertIn("applyApiBaseInput", html)
        self.assertIn("normalizeApiBase", html)
        self.assertIn("API_BASE_STORAGE_KEY", html)
        self.assertIn("query-param", html)
        self.assertIn("localStorage", html)
        self.assertIn("apiUrl = () => API_BASE", html)
        self.assertIn("http://localhost:8000", html)

        self.assertIn("/health", html)
        self.assertIn("/runtime/preflight", html)
        self.assertIn("/runtime/release-readiness", html)
        self.assertIn("diagnosticPill", html)

        self.assertIn("/projects/${pid}/files", html)
        self.assertIn("UPLOAD_ACCEPT_KNOWLEDGE", html)
        self.assertIn("uploaded", html)
        self.assertIn("parsed", html)
        self.assertIn("rejected", html)

        for phase in ("classify", "hypotheses", "gauntlet", "audit", "strategy", "sqi", "monitor", "report"):
            self.assertIn(phase, html)
        self.assertIn(
            "['classify', 'hypotheses', 'gauntlet', 'audit', 'strategy', 'sqi', 'monitor', 'report']",
            html,
        )

    def test_v5_dashboard_canonicalization_doc_is_present(self):
        if not DOC_V5_DASHBOARD_PATH.exists():
            self.skipTest("v5 dashboard canonicalization doc is not mounted in this execution environment")
        doc = DOC_V5_DASHBOARD_PATH.read_text(encoding="utf-8")

        self.assertIn("`dashboards/index.html` remains the canonical", doc)
        self.assertIn("controlled experimental local demo dashboard", doc)
        self.assertIn("Parity Checklist", doc)
        self.assertIn("/runtime/preflight", doc)
        self.assertIn("/runtime/release-readiness", doc)
        self.assertIn("client monitoring XLSX", doc)
        self.assertIn("operator monitoring XLSX", doc)
        self.assertIn("docker compose port app 8000", doc)
        self.assertIn("`http://localhost:8000` may work as a fallback", doc)
        self.assertIn("Do not describe it as canonical", doc)
