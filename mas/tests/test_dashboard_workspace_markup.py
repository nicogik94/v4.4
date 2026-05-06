"""Lightweight dashboard markup regression checks for the workspace UI."""
import unittest
from pathlib import Path


_HERE = Path(__file__).resolve()
HTML_PATH = None
HTML_V5_PATH = None
for root in (_HERE.parents[1], _HERE.parents[2]):
    candidate = root / "dashboards" / "index.html"
    if candidate.exists():
        HTML_PATH = candidate
    v5_candidate = root / "dashboards" / "index-v5.html"
    if v5_candidate.exists():
        HTML_V5_PATH = v5_candidate
    if HTML_PATH is not None and HTML_V5_PATH is not None:
        break
if HTML_PATH is None:
    HTML_PATH = _HERE.parents[1] / "dashboards" / "index.html"
if HTML_V5_PATH is None:
    HTML_V5_PATH = _HERE.parents[1] / "dashboards" / "index-v5.html"


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
