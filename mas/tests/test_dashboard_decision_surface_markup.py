"""Structural + behavioral regression checks for the dashboard decision surfaces.

No JavaScript runtime (node/jsdom) is available in this environment, so these
tests assert the guarantees structurally against the extracted source of each
new render function: untrusted text is always passed through ``escapeHtml``,
risks are capped at three in server order, no sensitive storage field is ever
emitted, and the render path issues no network writes.
"""
import re
import unittest
from pathlib import Path


_HERE = Path(__file__).resolve()
HTML_PATH = None
for root in (_HERE.parents[1], _HERE.parents[2]):
    candidate = root / "dashboards" / "index.html"
    if candidate.exists():
        HTML_PATH = candidate
        break
if HTML_PATH is None:
    HTML_PATH = _HERE.parents[1] / "dashboards" / "index.html"


def _extract_function(html, name):
    """Return the source of ``function name(...) { ... }`` by brace matching."""
    start = html.index(f"function {name}(")
    brace = html.index("{", start)
    depth = 0
    for j in range(brace, len(html)):
        if html[j] == "{":
            depth += 1
        elif html[j] == "}":
            depth -= 1
            if depth == 0:
                return html[start:j + 1]
    raise AssertionError(f"unbalanced braces for {name}")


NEW_RENDER_FUNCTIONS = (
    "dsBoundedText",
    "dsStatePill",
    "dsTopRisks",
    "renderDecisionSnapshot",
    "renderEvReportCard",
    "renderEvKnowledgeStatus",
)


class DashboardDecisionSurfaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HTML_PATH.exists():
            raise unittest.SkipTest("dashboard bundle not mounted")
        cls.html = HTML_PATH.read_text(encoding="utf-8")
        cls.fns = {name: _extract_function(cls.html, name) for name in NEW_RENDER_FUNCTIONS}
        cls.fns["renderEvAudit"] = _extract_function(cls.html, "renderEvAudit")
        cls.fns["renderEvidenceStub"] = _extract_function(cls.html, "renderEvidenceStub")

    # ── 1. Complete decision payload is surfaced on the Overview tab ──────────
    def test_overview_tab_renders_decision_snapshot(self):
        self.assertIn(
            "renderDecisionSnapshot(state.project?.overview, state.project?.workspace, state.project?.overviewError)",
            self.html,
        )
        snap = self.fns["renderDecisionSnapshot"]
        for field in (
            "current_recommendation",
            "decision_summary",
            "decision_object_health",
            "knowledge_health",
            "delivery_review_readiness",
        ):
            self.assertIn(field, snap, field)
        self.assertIn("dsTopRisks(ws)", snap)
        # Links to the dossier instead of duplicating evidence/risk detail.
        self.assertIn('href="#/project/${escapeHtml(pid)}/evidence"', snap)

    # ── 2/3. Dossier phase outputs are not gated by project_type ─────────────
    def test_dossier_phase_outputs_render_by_data_presence(self):
        stub = self.fns["renderEvidenceStub"]
        self.assertNotIn("isStrategic", stub)
        self.assertNotIn("=== 'strategic_audit'", stub)
        for guard, fn in (
            ("if (fs.audit)", "renderEvAudit(fs.audit)"),
            ("if (fs.strategy)", "renderEvStrategy(fs.strategy)"),
            ("if (fs.monitor)", "renderEvMonitor(fs.monitor)"),
            ("if (fs.report)", "renderEvReportCard(fs)"),
        ):
            self.assertIn(guard, stub)
            self.assertIn(fn, stub)
        self.assertIn("if (hasHyps)", stub)
        # Generic table remains only as the no-rich-output fallback.
        self.assertIn("if (phaseSections.length === 0)", stub)
        self.assertIn("renderEvGenericPhases(fs, ptype)", stub)

    def test_audit_card_includes_findings_observation_needs_and_principal_risks(self):
        audit = self.fns["renderEvAudit"]
        self.assertIn("Top findings", audit)
        self.assertIn("Observation needs", audit)
        self.assertIn("Principal risks", audit)
        self.assertIn("a.observation_needs", audit)
        self.assertIn("escapeHtml(o)", audit)

    # ── 2/4. Distinct evidence/knowledge states incl. partial & fetch failed ──
    def test_evidence_knowledge_status_defines_distinct_states(self):
        self.assertIn(
            "renderEvKnowledgeStatus(state.project?.overview, state.project?.workspace, filesRes, filesError, state.project?.overviewError)",
            self.html,
        )
        pill = self.fns["dsStatePill"]
        for state_key in (
            "available",
            "partial",
            "not imported",
            "not generated",
            "unavailable",
            "fetch failed",
        ):
            self.assertIn(f"'{state_key}'", pill)
        status = self.fns["renderEvKnowledgeStatus"]
        self.assertIn("'fetch failed'", status)
        self.assertIn("'not imported'", status)
        self.assertIn("'not generated'", status)
        self.assertIn("'partial'", status)
        # Overview snapshot exposes a fetch-failed branch when /overview errors.
        self.assertIn("overviewError", self.fns["renderDecisionSnapshot"])
        self.assertIn("'fetch failed'", self.fns["dsStatePill"])

    # ── 5. Hostile, HTML-like text always renders as text ────────────────────
    def test_untrusted_text_is_escaped(self):
        # No raw, unescaped interpolation of API-derived fields.
        forbidden_raw = {
            "renderDecisionSnapshot": ["${recommendation}", "${summary}", "${r.title}",
                                       "${r.severity}", "${projStatus}", "${dr.status}",
                                       "${kh.status}", "${maturityLabel}"],
            "renderEvReportCard": ["${heading}", "${preview}", "${report}"],
            "renderEvKnowledgeStatus": ["${maturityLabel}", "${locatorLabel}", "${filesLabel}"],
            "dsBoundedText": ["${full}", "${head}"],
            "dsStatePill": ["${label}", "${stateKey}"],
        }
        for fn, raws in forbidden_raw.items():
            body = self.fns[fn]
            for raw in raws:
                self.assertNotIn(raw, body, f"{fn} interpolates {raw} without escapeHtml")
        # Positive: the escaper is actually applied to those fields.
        self.assertIn("escapeHtml(recommendation)", self.fns["renderDecisionSnapshot"])
        self.assertIn("escapeHtml(r.title", self.fns["renderDecisionSnapshot"])
        self.assertIn("escapeHtml(head)", self.fns["dsBoundedText"])
        self.assertIn("escapeHtml(full)", self.fns["dsBoundedText"])
        self.assertIn("escapeHtml(label", self.fns["dsStatePill"])
        self.assertIn("escapeHtml(heading)", self.fns["renderEvReportCard"])
        self.assertIn("escapeHtml(preview)", self.fns["renderEvReportCard"])

    def test_new_surfaces_never_inject_api_html(self):
        for name, body in self.fns.items():
            self.assertNotIn(".innerHTML", body, name)
            self.assertNotIn("insertAdjacentHTML", body, name)

    # ── 6. Top risks: at most three, in server-provided order ────────────────
    def test_top_risks_capped_at_three_in_server_order(self):
        top = self.fns["dsTopRisks"]
        self.assertIn("slice(0, 3)", top)
        self.assertIn("active_risks", top)
        # Preserves server order when already sorted; otherwise stable severity→title.
        self.assertIn("alreadyOrdered", top)
        self.assertIn("severityRank", top)
        self.assertIn("localeCompare", top)

    # ── 7. No storage_ref / path / raw payload / secret-like field in surfaces ─
    def test_decision_surfaces_emit_no_sensitive_storage_fields(self):
        for name, body in self.fns.items():
            for forbidden in ("storage_ref", "checksum_sha256", "uploaded_by",
                              "local_path", "stored_path"):
                self.assertNotIn(forbidden, body, f"{name} references {forbidden}")

    # ── 8. Rendering / refreshing issues no write requests ───────────────────
    def test_decision_surfaces_issue_no_write_requests(self):
        for name, body in self.fns.items():
            for writer in ("apiPost(", "apiPatch(", "apiDelete(", "apiFormPost(", "fetch("):
                self.assertNotIn(writer, body, f"{name} performs a write/network call")

    # ── Report card stays a concise pointer, never the full narrative ────────
    def test_report_card_is_bounded_and_links_out(self):
        card = self.fns["renderEvReportCard"]
        self.assertIn("slice(0, 240)", card)
        self.assertIn('href="#/project/${escapeHtml(pid)}/report"', card)
        # It must not dump the whole report into the card.
        self.assertNotIn("${escapeHtml(report)}", card)
        self.assertNotIn("renderMarkdownBasic", card)


if __name__ == "__main__":
    unittest.main()
