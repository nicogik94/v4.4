"""Lightweight dashboard markup regression checks for the canonical workspace UI."""
import re
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
        self.assertIn("classify, hypotheses, gauntlet, audit, strategy, sqi, monitor, report", html)
        self.assertIn("Portfolio Operations", html)
        self.assertIn("command palette", html)
        self.assertIn("Operator review support", html)
        self.assertIn("Missing information for operator review", html)
        self.assertIn("renderClarificationMetrics", html)
        self.assertIn("renderClarificationReviewRows", html)
        self.assertIn("operatorReviewSupportNextAction", html)
        self.assertIn("Saved answer review", html)
        self.assertIn("Answer preview", html)
        self.assertIn("required open", html)
        self.assertIn("derived_summary", html)
        self.assertIn("review_rows", html)
        self.assertIn("Bayesian advisory", html)
        self.assertIn("Demo framing", html)
        self.assertIn("Framing label only", html)
        self.assertIn("does not create vertical runtime packs", html)
        self.assertIn('value="technology_readiness"', html)
        self.assertIn("Technology Readiness &amp; Transfer Audit", html)
        self.assertIn("Assess technology maturity, TRL, evidence gaps, IP/protection considerations, validation plan, transfer readiness, and next-level recommendations.", html)

        self.assertNotIn("controlled experimental dashboard", html)
        self.assertNotIn("controlled local demo", html)
        self.assertNotIn("canonical dashboard remains index.html", html)
        self.assertNotIn("v5 controlled local demo", html)
        self.assertNotIn("Portfolio / Operator summary", html)
        self.assertNotIn("Follow-up questions", html)
        self.assertNotIn("chatbot", html)
        self.assertNotIn("workflow gate", html)

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

        for tab_value, label in (
            ("overview", "Overview"),
            ("workflow", "Workflow"),
            ("evidence", "Evidence & Risks"),
            ("trace", "Decision Trace"),
            ("report", "Report & Export"),
        ):
            self.assertIn(f"{tab_value}: '{label}'", html)
        self.assertIn("const WORKSPACE_TABS = ['overview', 'workflow', 'evidence', 'trace', 'report']", html)
        self.assertIn("const tabs = WORKSPACE_TABS", html)
        self.assertIn("normalizeWorkspaceTab", html)
        for legacy_tab, current_tab in (
            ("decide", "overview"),
            ("outcomes", "overview"),
            ("calibration", "overview"),
            ("bayesian", "trace"),
            ("audit", "trace"),
        ):
            self.assertIn(f"{legacy_tab}: '{current_tab}'", html)
        self.assertIn('data-tab="${t}"', html)
        self.assertIn("WORKSPACE_TABS.forEach(t =>", html)
        self.assertIn("title: `Go to ${TAB_LABELS[t]}`", html)
        self.assertNotIn("const tabs = ['decide', 'evidence', 'outcomes', 'calibration', 'report', 'audit']", html)

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
        self.assertIn("Local runtime readiness only", html)
        self.assertIn("Release blockers", html)
        self.assertIn("Release warnings", html)
        self.assertIn("Preflight failed/degraded checks", html)
        self.assertIn("runtimePreflightItems", html)
        self.assertIn("runtimeReleaseItems", html)
        self.assertIn("renderRuntimeReadinessPanel", html)
        self.assertIn("renderRuntimeHealthPanel", html)
        self.assertIn("runtime-health-panel", html)
        self.assertIn("Runtime health", html)
        self.assertIn("Global runtime only. This is separate from project lifecycle state.", html)
        self.assertIn("Persistence / database", html)
        self.assertIn("Durable run-state", html)
        self.assertIn("Workflow queue", html)
        self.assertIn("Release gate", html)
        self.assertIn("Freshness", html)
        self.assertIn("runtimeHealthSummary", html)
        self.assertIn("syncRuntimeHealthPanel", html)
        self.assertGreaterEqual(html.count("${renderRuntimeHealthPanel()}"), 2)
        self.assertNotIn("Runtime readiness details", html)

    def test_portfolio_operations_landing_and_filters_are_present(self):
        if not HTML_PATH.exists():
            self.skipTest("dashboard bundle is not mounted in this execution environment")
        html = HTML_PATH.read_text(encoding="utf-8")

        for expected in (
            "#/                    → Portfolio Operations landing view",
            "renderPortfolioOverview",
            "Portfolio Operations",
            "Start from Portfolio signals on the left",
            "portfolio-landing-card",
            "Decision Attention Queue",
            "Showing the current project result set.",
        ):
            self.assertIn(expected, html)

        self.assertIn("const PORTFOLIO_FILTERS = [", html)
        for filter_key, label in (
            ("needs_attention", "Needs attention"),
            ("active", "Active"),
            ("complete", "Complete"),
            ("all", "All"),
        ):
            self.assertIn(f"key: '{filter_key}', label: '{label}'", html)
        self.assertIn("renderPortfolioFilterControl", html)
        self.assertIn("data-portfolio-filter", html)
        self.assertIn("portfolio-filter-btn", html)
        self.assertIn("filterPortfolioProjects", html)

        self.assertIn("function isCompletedProject(project)", html)
        self.assertIn("project_status || '').toLowerCase() === 'completed'", html)
        self.assertIn("if (isCompletedProject(project)) return [];", html)
        self.assertIn("if (filterKey === 'complete') return rows.filter(isCompletedProject);", html)
        self.assertIn("not complete and not attention", html)
        self.assertIn("project_status completed", html)
        self.assertIn("Open the project workspace to review report and exports.", html)

        self.assertIn("function projectAttentionReasons(project)", html)
        self.assertIn("function projectNeedsAttention(project)", html)
        self.assertIn("attention-row", html)
        self.assertIn("requires_approval", html)
        self.assertIn("has_stale_downstream", html)
        self.assertIn("active_risk_count", html)
        self.assertIn("No projects currently match attention signals from queue fields.", html)

    def test_portfolio_v3_visual_hooks_preserve_live_markup(self):
        if not HTML_PATH.exists():
            self.skipTest("dashboard bundle is not mounted in this execution environment")
        html = HTML_PATH.read_text(encoding="utf-8")

        for expected in (
            "--shell: #211F1B",
            "--surface-3: #ECE7DE",
            ".portfolio-filter-btn[data-portfolio-filter=\"needs_attention\"].active",
            ".portfolio-filter-btn[data-portfolio-filter=\"active\"].active",
            ".portfolio-filter-btn[data-portfolio-filter=\"complete\"].active",
            ".attention-row::before",
            ".attention-row.attention-blocked::before",
            ".attention-row.attention-approval::before",
            ".portfolio-project-row.completed::before",
            ".portfolio-project-row.review-required::before",
            ".portfolio-project-row.selected",
            ".project-row.completed .project-name",
            ".project-row.review-required .project-name",
            ".signal.warn",
            ".signal.bad",
            "function attentionToneClass(project)",
            "function projectRowStateClasses(project)",
        ):
            self.assertIn(expected, html)

        self.assertIn('button class="portfolio-project-row ${escapeHtml(projectRowStateClasses(p))}" type="button" data-pid=', html)
        self.assertIn('button class="attention-row ${escapeHtml(attentionToneClass(p))}', html)
        self.assertIn("document.querySelectorAll('.ops-list-item[data-pid], .attention-row[data-pid], .portfolio-project-row[data-pid]')", html)
        self.assertIn("routeTo(`project/${pid}`)", html)
        self.assertIn("if (filterKey === 'complete') return rows.filter(isCompletedProject);", html)
        self.assertIn("if (isCompletedProject(project)) return [];", html)

    def test_runtime_health_v3_visual_hooks_are_present(self):
        if not HTML_PATH.exists():
            self.skipTest("dashboard bundle is not mounted in this execution environment")
        html = HTML_PATH.read_text(encoding="utf-8")

        for expected in (
            ".runtime-health-panel",
            ".runtime-health-panel.ok",
            ".runtime-health-panel.warn",
            ".runtime-health-panel.err",
            '.runtime-health-panel[data-runtime-status="fetch_failed"]',
            ".runtime-health-head",
            ".runtime-health-grid",
            ".runtime-health-item",
            ".runtime-health-item.ok .value",
            ".runtime-health-item.warn .value",
            ".runtime-health-item.err .value",
            'data-runtime-status="${escapeHtml(summary.status)}"',
            "Runtime health",
            "Global runtime only. This is separate from project lifecycle state.",
            "fetch failed",
        ):
            self.assertIn(expected, html)

        self.assertNotIn("runtime retry", html.lower())
        self.assertNotIn("Retry runtime", html)

    def test_v3_prototype_only_controls_and_copy_are_absent(self):
        if not HTML_PATH.exists():
            self.skipTest("dashboard bundle is not mounted in this execution environment")
        html = HTML_PATH.read_text(encoding="utf-8")

        for forbidden in (
            "Review States",
            "All values illustrative",
            "Analysis Type",
            "illustrative mock",
            "api.decide.local:8800",
        ):
            self.assertNotIn(forbidden, html)

    def test_workspace_tabs_use_portfolio_operations_structure(self):
        if not HTML_PATH.exists():
            self.skipTest("dashboard bundle is not mounted in this execution environment")
        html = HTML_PATH.read_text(encoding="utf-8")

        for expected in (
            "activeTab: 'overview'",
            "const WORKSPACE_TABS = ['overview', 'workflow', 'evidence', 'trace', 'report']",
            "const tabs = WORKSPACE_TABS",
            "if (state.activeTab === 'overview') return renderDecide();",
            "if (state.activeTab === 'workflow') return renderOutcomesTab();",
            "if (state.activeTab === 'evidence') return renderEvidenceStub();",
            "if (state.activeTab === 'trace') return renderAuditStub();",
            "if (state.activeTab === 'report') return renderReportStub();",
            "if (state.activeTab === 'overview') sections = renderInspectorDecide();",
            "else if (state.activeTab === 'workflow') sections = renderInspectorCalibration();",
            "else if (state.activeTab === 'evidence') sections = renderInspectorEvidence();",
            "else if (state.activeTab === 'trace') sections = renderInspectorAudit();",
            "else if (state.activeTab === 'report') sections = renderInspectorReport();",
        ):
            self.assertIn(expected, html)

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
            "Client-safe after review",
            "Operator-only",
            "Internal archive",
        ):
            self.assertIn(label, html)

        for profile in (
            "report",
            "decision_memo_pilot_plan",
            "client_dossier",
            "operator_dossier",
            "machine_archive",
            "client_monitoring_template",
            "operator_monitoring_template",
            "technology_readiness_workbook",
        ):
            self.assertIn(profile, html)

        for fmt in ("pdf", "docx", "zip", "xlsx"):
            self.assertIn(fmt, html)
        self.assertIn("/export?profile=", html)
        self.assertIn("reportProfileExportUrl", html)
        self.assertIn("profileExportUrl(pid, 'report', fmt)", html)
        self.assertIn("legacyExportUrl", html)
        self.assertIn("/export/${encodeURIComponent(fmt)}", html)
        self.assertIn("/projects/{id}/export?profile=report&format=docx", html)
        self.assertIn("/projects/{id}/export?profile=report&format=pdf", html)
        self.assertIn("Download DOCX", html)
        self.assertIn("Download PDF", html)
        self.assertIn("Download monitoring XLSX — Client", html)
        self.assertIn("Download monitoring XLSX — Operator", html)
        self.assertIn("data-profile-export=\"client_monitoring_template\"", html)
        self.assertIn("data-profile-export=\"operator_monitoring_template\"", html)
        self.assertIn("generatedReportMode", html)
        self.assertIn("generatedReportMetadataStatus", html)
        self.assertIn("decisionMemoExportSupported", html)
        self.assertIn("metadata_status", html)
        self.assertIn("report_output?.generated_report_mode", html)
        self.assertIn("renderReportRerunNotice", html)
        self.assertIn("renderReportQualityNotice", html)

        for old_label in (
            "Client dossier",
            "Operator dossier",
            "Machine archive",
            "Client monitoring template",
            "Operator monitoring template",
        ):
            self.assertNotIn(old_label, html)

    def test_export_profile_selector_renders_unique_supported_options(self):
        if not HTML_PATH.exists():
            self.skipTest("dashboard bundle is not mounted in this execution environment")
        html = HTML_PATH.read_text(encoding="utf-8")

        standard_keys_match = re.search(r"const STANDARD_EXPORT_PROFILE_KEYS = \[(.*?)\];", html)
        self.assertIsNotNone(standard_keys_match)
        standard_keys = re.findall(r"'([^']+)'", standard_keys_match.group(1))
        self.assertEqual(standard_keys, ["report", "operator_dossier", "machine_archive"])

        labels = []
        for key in standard_keys:
            profile_match = re.search(rf"\n  {re.escape(key)}: \{{(.*?)\n  \}},", html, re.DOTALL)
            self.assertIsNotNone(profile_match, key)
            label_match = re.search(r"label: '([^']+)'", profile_match.group(1))
            self.assertIsNotNone(label_match, key)
            labels.append(label_match.group(1))

        self.assertEqual(labels.count("Client-safe after review"), 1)
        self.assertEqual(labels.count("Operator-only"), 1)
        self.assertEqual(labels.count("Internal archive"), 1)
        self.assertIn("const profiles = exportProfilesForProject();", html)
        self.assertIn("profiles.map(([value, config])", html)
        self.assertNotIn("Object.entries(EXPORT_PROFILES).map", html)

        self.assertIn("function isTechnologyReadinessProject(project)", html)
        self.assertIn("function technologyReadinessWorkbookExportSupported()", html)
        self.assertIn("isTechnologyReadinessProject(project) && technologyReadinessWorkbookExportSupported()", html)
        self.assertIn("keys.push(TECHNOLOGY_READINESS_EXPORT_PROFILE_KEY)", html)
        self.assertIn("keys.splice(1, 0, 'decision_memo_pilot_plan')", html)

    def test_dashboard_surfaces_supported_input_contract_labels_only(self):
        if not HTML_PATH.exists():
            self.skipTest("dashboard bundle is not mounted in this execution environment")
        html = HTML_PATH.read_text(encoding="utf-8")

        self.assertIn("renderInputContractTags", html)
        self.assertIn("workspace?.input_contract", html)
        self.assertIn("Input contract:", html)
        self.assertNotIn("Request ID:", html)
        self.assertNotIn("Run ID:", html)

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
