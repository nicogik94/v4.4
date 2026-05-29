"""Lightweight dashboard markup regression checks for the canonical workspace UI."""
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
    def test_canonical_dashboard_entrypoint_uses_v5_experience(self):
        if not HTML_PATH.exists():
            self.skipTest("dashboard bundle is not mounted in this execution environment")
        html = HTML_PATH.read_text(encoding="utf-8")

        self.assertIn("v5 canonical dashboard", html)
        self.assertIn("canonical operator workflow", html)
        self.assertIn("Portfolio / Operator summary", html)
        self.assertIn("command palette", html)
        self.assertIn("Follow-up questions", html)
        self.assertIn("Missing information", html)
        self.assertIn("Bayesian advisory", html)
        self.assertIn("Demo framing", html)
        self.assertIn("Framing label only", html)
        self.assertIn("does not create vertical runtime packs", html)

        self.assertNotIn("controlled experimental dashboard", html)
        self.assertNotIn("controlled local demo", html)
        self.assertNotIn("canonical dashboard remains index.html", html)
        self.assertNotIn("v5 controlled local demo", html)

    def test_canonical_dashboard_api_routes_and_workflow_controls_are_present(self):
        if not HTML_PATH.exists():
            self.skipTest("dashboard bundle is not mounted in this execution environment")
        html = HTML_PATH.read_text(encoding="utf-8")

        for route in (
            "/projects/queue",
            "/projects/${pid}/workspace",
            "/projects/${pid}/overview",
            "/projects/${pid}/policy-audit",
            "/projects/${pid}/files",
            "/projects/${pid}/knowledge",
            "/projects/${pid}/clarifications",
            "/projects/${pid}/report",
            "/projects/${pid}/outcomes",
            "/projects/${state.selectedProjectId}/run",
            "/projects/${state.selectedProjectId}/kill",
            "/projects/${pid}/input",
            "/calibration/priors",
            "/calibration/deltas",
            "/calibration/framework-performance?days=30",
        ):
            self.assertIn(route, html)

        self.assertIn("applyApiBaseInput", html)
        self.assertIn("normalizeApiBase", html)
        self.assertIn("API_BASE_STORAGE_KEY", html)
        self.assertIn("query-param", html)
        self.assertIn("localStorage", html)
        self.assertIn("apiUrl = () => API_BASE", html)
        self.assertIn("http://localhost:8000", html)

        for phase in ("classify", "hypotheses", "gauntlet", "audit", "strategy", "sqi", "monitor", "report"):
            self.assertIn(phase, html)
        self.assertIn(
            "['classify', 'hypotheses', 'gauntlet', 'audit', 'strategy', 'sqi', 'monitor', 'report']",
            html,
        )

    def test_canonical_runtime_readiness_details_are_present(self):
        if not HTML_PATH.exists():
            self.skipTest("dashboard bundle is not mounted in this execution environment")
        html = HTML_PATH.read_text(encoding="utf-8")

        self.assertIn("/health", html)
        self.assertIn("/runtime/preflight", html)
        self.assertIn("/runtime/release-readiness", html)
        self.assertIn("diagnosticPill", html)
        self.assertIn("runtime-readiness-panel", html)
        self.assertIn("Runtime readiness details", html)
        self.assertIn("Release blockers", html)
        self.assertIn("Release warnings", html)
        self.assertIn("Preflight failed/degraded checks", html)
        self.assertIn("runtimePreflightItems", html)
        self.assertIn("runtimeReleaseItems", html)
        self.assertIn("renderRuntimeReadinessPanel", html)

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

        for label in (
            "Report",
            "Client dossier",
            "Operator dossier",
            "Machine archive",
            "Client monitoring template",
            "Operator monitoring template",
        ):
            self.assertIn(label, html)

        for profile in (
            "report",
            "client_dossier",
            "operator_dossier",
            "machine_archive",
            "client_monitoring_template",
            "operator_monitoring_template",
        ):
            self.assertIn(profile, html)

        for fmt in ("pdf", "docx", "zip", "xlsx"):
            self.assertIn(fmt, html)
        self.assertIn("/export?profile=", html)
        self.assertIn("/export/${fmt}", html)
        self.assertIn("Download DOCX", html)
        self.assertIn("Download PDF", html)

    def test_index_v5_is_compatibility_entrypoint(self):
        if not HTML_V5_PATH.exists():
            self.skipTest("dashboard v5 compatibility entrypoint is not mounted")
        html = HTML_V5_PATH.read_text(encoding="utf-8")

        self.assertIn("compatibility entry point", html)
        self.assertIn("canonical/default operator dashboard", html)
        self.assertIn("dashboards/index.html", html)
        self.assertIn("window.location.replace", html)
        self.assertIn("index.html", html)
        self.assertNotIn("controlled experimental", html)
        self.assertNotIn("not promoted", html)
        self.assertNotIn("canonical dashboard remains", html)

    def test_v5_dashboard_canonicalization_doc_is_updated(self):
        if not DOC_V5_DASHBOARD_PATH.exists():
            self.skipTest("v5 dashboard canonicalization doc is not mounted in this execution environment")
        doc = DOC_V5_DASHBOARD_PATH.read_text(encoding="utf-8")

        self.assertIn("`dashboards/index.html` is the canonical/default", doc)
        self.assertIn("`dashboards/index-v5.html` remains as a compatibility", doc)
        self.assertIn("Runtime Readiness", doc)
        self.assertIn("/runtime/preflight", doc)
        self.assertIn("/runtime/release-readiness", doc)
        self.assertIn("client monitoring XLSX", doc)
        self.assertIn("operator monitoring XLSX", doc)
        self.assertIn("docker compose port app 8000", doc)
        self.assertIn("`http://localhost:8000` may work as a fallback", doc)
        self.assertNotIn("controlled experimental local demo dashboard", doc)
        self.assertNotIn("Do not describe it as canonical", doc)
