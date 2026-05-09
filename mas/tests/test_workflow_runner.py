"""Regression tests for the resumable sequential workflow runner."""
import json
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks, HTTPException


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api
from llm_client import LLMResponse
from orchestrator import (
    _build_report_evidence_locator_register,
    _phase_has_output,
    _sanitize_report_context,
    build_monitor_prompt,
    build_report_prompt,
    get_first_unfinished_phase,
    is_workflow_complete,
    run_phase_node,
    run_workflow_sequence,
)
from state import (
    AuditOutput,
    ClassifyOutput,
    FMEAItem,
    GauntletOutput,
    GauntletResult,
    Hypothesis,
    KnowledgeItem,
    KnowledgeLayerState,
    MonitorCanary,
    MonitorCircuitBreaker,
    MonitorOODASchedule,
    MonitorOutput,
    MonitorScheduleItem,
    OODALoop,
    PhaseStatus,
    PreliminaryVerdict,
    Priority,
    Provenance,
    ProjectState,
    SQIDimension,
    SQIOutput,
    StrategyAction,
    StrategyOutput,
    Verdict,
)


def make_response(text: str, input_tokens: int = 10, output_tokens: int = 5,
                  cost_usd: float = 0.01) -> LLMResponse:
    return LLMResponse(
        text=text,
        ok=True,
        model_used="claude-sonnet-4-6",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
    )


def make_hypotheses_payload() -> list[dict]:
    return [
        {"id": "H1", "text": "Test hypothesis 1", "alpha": 6, "beta": 4, "confirm": ">70%", "reject": "<30%"},
        {"id": "H2", "text": "Test hypothesis 2", "alpha": 5, "beta": 5, "confirm": ">70%", "reject": "<30%"},
        {"id": "H3", "text": "Test hypothesis 3", "alpha": 7, "beta": 3, "confirm": ">70%", "reject": "<30%"},
    ]


def make_monitor_payload() -> dict:
    return {
        "ooda_schedule": {
            "daily": [{"metric": "CTR", "owner": "editor", "source": "GSC"}],
            "weekly": [{"metric": "Organic clicks", "owner": "seo", "source": "GA"}],
            "monthly": [{"metric": "Topic share", "owner": "lead", "source": "dashboard"}],
        },
        "circuit_breakers": [{"strategy_ref": "S1", "trip": "CTR drops 20%", "reset": "2 healthy weeks"}],
        "canaries": [
            {"signal": "CTR", "direction": "up", "window": "7d", "meaning": "headline improvement"},
            {"signal": "Impressions", "direction": "up", "window": "7d", "meaning": "ranking lift"},
            {"signal": "Bounce rate", "direction": "down", "window": "7d", "meaning": "better UX"},
        ],
        "chaos_drills": [{"what": "Tracking outage", "when": "monthly", "measure": "time to detect"}],
        "hro_principles_active": ["preoccupation_with_failure"],
        "reentry_watch": ["R1", "R8"],
        "commitment_score": 81,
        "commitment_rationale": "Owners and metrics are explicit.",
    }


def make_strategy_output() -> StrategyOutput:
    return StrategyOutput(
        preliminary_verdicts=[
            PreliminaryVerdict(id="H1", verdict=Verdict.LIKELY_CONFIRMED, evidence="strong"),
            PreliminaryVerdict(id="H2", verdict=Verdict.NEEDS_MONITORING, evidence="mixed", monitoring_plan="watch CTR"),
            PreliminaryVerdict(id="H3", verdict=Verdict.LIKELY_REJECTED, evidence="weak"),
        ],
        executive_strategy="Focus on search-driven editorial planning.",
        strategies=[
            StrategyAction(
                priority=Priority.CRITICAL,
                action="Create search-driven briefs",
                justification="Align content with demand.",
                evidence_chain="H1 + audit",
                expected_impact="Higher CTR",
                effort="Medium",
                timeline="2 weeks",
                risk_if_ignored="Traffic stagnates",
                framework_source="HDD",
            )
        ],
        success_metrics=["CTR up 15%"],
    )


def make_completed_state(project_id: str = "workflow-complete") -> ProjectState:
    state = ProjectState(project_id=project_id, project_name="Workflow", brief="Test brief")
    state.intake_sanitization_findings = {}
    state.classify = ClassifyOutput(
        domain="Complicated",
        justification="Known structure",
        bf=42.0,
        variety_env="env",
        variety_sys="sys",
        variety_gaps="1. gap",
        variety_decision="Amplify",
        ooda=OODALoop(observe="obs", orient="ori", decide="dec", act="act", freq="weekly"),
        dq=[20, 18, 16, 14],
    )
    state.hypotheses = [Hypothesis(**item) for item in make_hypotheses_payload()]
    state.gauntlet = GauntletOutput(
        results=[GauntletResult(id="H1", risk_rank=1, frameworks=[{"fw": "STEELMAN", "finding": "x", "action": True}] * 10, crux="crux")]
    )
    state.audit = AuditOutput(
        data_based=True,
        fmea=[FMEAItem(component="site", failure_mode="slow", s=5, o=4, d=3, rpn=60, action="fix")],
        top_findings=["Improve page speed"],
    )
    state.strategy = make_strategy_output()
    state.monitor = MonitorOutput(
        ooda_schedule=MonitorOODASchedule(
            daily=[MonitorScheduleItem(metric="CTR", owner="editor", source="GSC")]
        ),
        circuit_breakers=[MonitorCircuitBreaker(strategy_ref="S1", trip="CTR down 20%", reset="2 healthy weeks")],
        canaries=[MonitorCanary(signal="CTR", direction="up", window="7d", meaning="improving")],
        commitment_score=80,
        commitment_rationale="Assigned owners",
    )
    state.sqi = SQIOutput(
        sqi_overall=75,
        dimensions=[SQIDimension(name="Evidence Quality", score=75, grade="B", finding="good enough")],
    )
    state.report = "final report"
    state.sealed = True
    state.seal_date = "2026-04-11"
    for phase in ("classify", "hypotheses", "gauntlet", "audit", "strategy", "sqi", "monitor", "report"):
        state.phase_status[phase] = PhaseStatus.COMPLETED
        state.phase_confidence[phase] = 1.0
    return state


REPORT_EVIDENCE_MARKER_RE = re.compile(r"\[Evidence: [^\]\n]+ \| [^\]\n]+\]")
REPORT_MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")
REPORT_LOAD_BEARING_SECTIONS = {
    "executive summary",
    "recommended path",
    "why this is recommended",
    "evidence used",
    "key risks",
    "assumptions and open questions",
    "monitoring and kill criteria",
}


def report_load_bearing_marker_counts(report: str) -> dict[str, int]:
    counts = {section: 0 for section in REPORT_LOAD_BEARING_SECTIONS}
    current_section = ""
    for raw_line in (report or "").splitlines():
        line = raw_line.strip()
        heading = REPORT_MARKDOWN_HEADING_RE.match(line)
        if heading:
            current_section = heading.group(1).strip().lower()
        if current_section in counts and REPORT_EVIDENCE_MARKER_RE.search(line):
            counts[current_section] += len(REPORT_EVIDENCE_MARKER_RE.findall(line))
    return counts


class TestWorkflowHelpers(unittest.TestCase):

    def test_report_prompt_includes_parseable_evidence_locator_register(self):
        state = make_completed_state("report-locator-register")
        state.knowledge_layer = KnowledgeLayerState(
            items=[
                KnowledgeItem(
                    evidence_id="ev-market-note",
                    source_id="src-market",
                    source_ref="fixture://market-note",
                    locator="upload:file-1:market-note.pdf#chunk=2",
                    title="Market note",
                    summary="RAW_EVIDENCE_BODY_SHOULD_NOT_APPEAR",
                    provenance=Provenance(external_uri="upload:file-1"),
                )
            ]
        )

        prompt = build_report_prompt(state)

        self.assertIn("PROJECT EVIDENCE LOCATORS:", prompt)
        header = "PHASE 5: Final report. Write a client-facing decision memo for non-technical business decision-makers."
        self.assertLess(prompt.index(header), prompt.index("PROJECT EVIDENCE LOCATORS:"))
        self.assertLess(prompt.index("PROJECT EVIDENCE LOCATORS:"), prompt.index("MANDATORY REPORT CITATION DISCIPLINE:"))
        self.assertLess(prompt.index("MANDATORY REPORT CITATION DISCIPLINE:"), prompt.index("Report structure:"))
        self.assertLess(prompt.index("PROJECT EVIDENCE LOCATORS:"), prompt.index("DOMAIN:"))
        self.assertIn("[Evidence: ev-market-note | upload:file-1:market-note.pdf#chunk=2]", prompt)
        self.assertIn("source_ref=fixture://market-note", prompt)
        self.assertIn("source_id=src-market", prompt)
        self.assertIn("title=Market note", prompt)
        self.assertIn("external_uri=upload:file-1", prompt)
        self.assertNotIn("RAW_EVIDENCE_BODY_SHOULD_NOT_APPEAR", prompt)

    def test_report_prompt_includes_citation_discipline_instructions(self):
        prompt = build_report_prompt(make_completed_state("report-citation-discipline"))

        self.assertIn("MANDATORY REPORT CITATION DISCIPLINE:", prompt)
        self.assertIn("Final report project-evidence citations must use concrete markers copied from PROJECT EVIDENCE LOCATORS", prompt)
        self.assertIn("Use the literal pipe character `|`. Do not escape it as `\\|`.", prompt)
        self.assertIn("Valid example: [Evidence: ev-market-note | chunk=2]", prompt)
        self.assertIn("Invalid: [Evidence: ev-market-note \\| chunk=2]", prompt)
        self.assertIn("Never output placeholder evidence markers.", prompt)
        self.assertIn("Do not output [Evidence: ...] or angle-bracket templates in the final report.", prompt)
        for invalid_marker in (
            "[Evidence: ...]",
            "[Evidence: <evidence_id> | <locator>]",
            "[Evidence: evidence_id | locator]",
            "[Evidence: ev-market-note | ...]",
            "[Evidence: ... | ...]",
        ):
            self.assertIn(f"Invalid: {invalid_marker}", prompt)
        self.assertIn("Each citation marker must contain exactly one evidence ID and one locator", prompt)
        self.assertIn("do not put semicolons or multiple Evidence tokens inside one marker", prompt)
        self.assertIn("Every evidence marker in the final report must copy a real evidence_id and locator from PROJECT EVIDENCE LOCATORS", prompt)
        self.assertIn("Do not invent evidence IDs, source names, metrics, pages, rows, chunks, customers, or provenance", prompt)
        self.assertIn("Framework markers such as [#24] are methodology references, not project evidence citations", prompt)
        self.assertIn("Do not cite the act of recommending; cite the empirical evidence behind the recommendation", prompt)
        self.assertIn("Evidence markers identify source material; they do not by themselves prove the recommendation or semantic support for a claim", prompt)
        self.assertIn("Do not claim that citation or locator resolvability proves semantic support", prompt)
        self.assertIn("If no concrete locator is available or no supplied evidence supports the claim", prompt)
        self.assertIn("[Inference], [Hypothesis], [Unknown], or write citation unavailable", prompt)
        self.assertNotIn("[Evidence: <evidence_id> | <locator>] is the only canonical project-evidence citation format", prompt)
        self.assertNotIn("Use only these evidence IDs for [Evidence: <evidence_id> | <locator>] citations.", prompt)

    def test_report_prompt_citation_discipline_names_load_bearing_sections(self):
        prompt = build_report_prompt(make_completed_state("report-load-bearing-citations"))

        for section in (
            "Executive Summary",
            "Recommended Path",
            "Why This Is Recommended",
            "Evidence Used",
            "Key Risks",
            "Assumptions and Open Questions",
            "Monitoring and Kill Criteria",
        ):
            self.assertIn(section, prompt)
        self.assertIn("if a section contains an empirical claim supported by supplied project evidence", prompt)
        self.assertIn("Never fabricate a marker to satisfy the citation rule", prompt)

    def test_report_prompt_uses_client_memo_headings_in_order(self):
        prompt = build_report_prompt(make_completed_state("report-client-memo-headings"))

        headings = (
            "# Executive Summary",
            "# The Decision",
            "# Recommended Path",
            "# Why This Is Recommended",
            "# Options Considered",
            "# Evidence Used",
            "# Key Risks",
            "# Assumptions and Open Questions",
            "# Roadmap",
            "# Next Steps",
            "# Monitoring and Kill Criteria",
            "# Appendix: Technical Analysis",
        )
        positions = [prompt.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))

    def test_report_prompt_includes_client_artifact_constraints(self):
        prompt = build_report_prompt(make_completed_state("report-client-artifact-constraints"))

        self.assertIn("## At a Glance", prompt)
        self.assertIn("At a glance", prompt)
        self.assertIn("two-column Markdown table", prompt)
        self.assertIn("Field and Detail", prompt)
        self.assertIn("do not use raw comparison symbols", prompt)
        for item in ("Decision", "Recommendation", "Confidence level", "Biggest risk", "Next action"):
            self.assertIn(item, prompt)
        self.assertIn("Separate recommendation strength from evidence strength", prompt)
        self.assertIn("Evidence strength labels must be one of: strong, moderate, weak, unavailable, inference only", prompt)
        self.assertIn("Do not invent owners, dates, metrics, thresholds, budgets, customer facts, evidence, or commitments", prompt)
        self.assertIn("TBD — requires operator confirmation", prompt)
        self.assertIn("Use a table with: option, upside, downside, best use case, verdict", prompt)
        self.assertIn("Use a table with: evidence, what it suggests, evidence strength, caveat, citation marker if available", prompt)
        self.assertIn("Use a table with: risk, why it matters, early warning signal, mitigation, owner/role if known", prompt)
        self.assertIn("Use a table with: unresolved assumption or question, why it matters, how to resolve it, owner/role if known, status", prompt)
        self.assertIn("Include a 7/30/60/90-day roadmap table", prompt)
        self.assertIn("Include 5-7 concrete next actions", prompt)
        self.assertIn("Prefer \"stop/change-course threshold\" over unexplained \"kill criterion.\"", prompt)
        self.assertIn("Move framework-heavy content here: FMEA, HAZOP, SQI, Causal Inference, HRO, Red Team, Ablation", prompt)
        self.assertNotIn("Evidence Gauge", prompt)
        self.assertNotIn("Defense Index", prompt)
        self.assertNotIn("claim cards", prompt.lower())

    def test_report_prompt_includes_factual_safety_and_research_depth_rules(self):
        prompt = build_report_prompt(make_completed_state("report-factual-safety"))

        for expected in (
            "GA4 data thresholds are system-defined",
            "Use INP for responsiveness",
            "Core Web Vitals and page experience align with Google Search ranking systems",
            "Prioritize Article and BreadcrumbList structured data",
            "Consider FAQPage only where the page type and Google's current eligibility rules apply",
            "Structured data can make pages eligible for search features",
            "hypothesis-driven diagnostic memo",
            "not yet a completed evidence-backed SEO audit",
            "Evidence Maturity",
            "Sprint 0 Evidence Pack Required",
            "Moderate confidence in the intervention sequence",
            "low-to-moderate confidence in the size of impact",
            "high confidence that Sprint 0 diagnostics are necessary",
            "Named owners require operator confirmation",
        ):
            self.assertIn(expected, prompt)
        for expected in (
            "GSC 12-month URL/query export",
            "GA4 audience/acquisition check",
            "CrUX or PageSpeed field data",
            "site crawl export",
            "URL inventory with publish/update dates",
            "keyword research sample",
            "editorial workflow/process confirmation",
            "CMS/schema/canonical capability check",
        ):
            self.assertIn(expected, prompt)
        for forbidden in (
            "500 MAU threshold",
            "GA4 Hispanic segment",
            "Google Signals threshold for Hispanic segment",
            "FID/INP",
            "direct ranking signal",
            "FAQPage for rich-result capture",
            "guaranteed rich results",
        ):
            self.assertNotIn(forbidden, prompt)

    def test_report_context_sanitizer_removes_unsafe_exact_phrasing(self):
        unsafe = (
            "500 MAU threshold; GA4 Hispanic segment; "
            "Google Signals threshold for Hispanic segment; FID/INP; "
            "Core Web Vitals are a direct ranking signal; "
            "Implement FAQPage schema; FAQPage for rich-result capture; "
            "18-34 female Hispanic segment visible in GA4; guaranteed rich results"
        )

        sanitized = _sanitize_report_context(unsafe)

        for forbidden in (
            "500 MAU threshold",
            "GA4 Hispanic segment",
            "Google Signals threshold for Hispanic segment",
            "FID/INP",
            "Core Web Vitals are a direct ranking signal",
            "Implement FAQPage schema",
            "FAQPage for rich-result capture",
            "18-34 female Hispanic segment visible in GA4",
            "guaranteed rich results",
        ):
            self.assertNotIn(forbidden, sanitized)
        self.assertIn("INP", sanitized)
        self.assertIn("FAQPage only where page type", sanitized)
        self.assertIn("eligibility", sanitized)

    def test_report_prompt_treats_clarifications_as_context_not_evidence(self):
        prompt = build_report_prompt(make_completed_state("report-clarification-context"))

        self.assertIn("clarification_cycles", prompt)
        self.assertIn("clarification_answers", prompt)
        self.assertIn("include them only as assumptions, open questions, unavailable context, or operator-provided context", prompt)
        self.assertIn("Unanswered clarification questions remain unresolved questions", prompt)
        self.assertIn("Clarification answers and questions are not empirical evidence", prompt)
        self.assertIn("must not be cited with project evidence markers", prompt)
        self.assertIn("must not be placed in the Evidence Used table as cited facts", prompt)

    def test_report_prompt_keeps_framework_heavy_material_in_appendix(self):
        prompt = build_report_prompt(make_completed_state("report-appendix-frameworks"))

        self.assertIn("# Appendix: Technical Analysis", prompt)
        self.assertIn("Move framework-heavy content here: FMEA, HAZOP, SQI, Causal Inference, HRO, Red Team, Ablation", prompt)
        for old_heading in (
            "# FINAL VERDICTS",
            "# CAUSAL VERIFICATION",
            "# DEFENSE AUDIT",
            "# HRO DEBRIEF",
            "# RED TEAM",
            "# ABLATION",
            "# AGENT CARDS",
            "# META-LEARNER INPUT",
        ):
            self.assertNotIn(old_heading, prompt)

    def test_report_prompt_includes_internal_evidence_citation_check(self):
        prompt = build_report_prompt(make_completed_state("report-internal-citation-check"))

        self.assertIn("EVIDENCE CITATION CHECK BEFORE FINAL OUTPUT:", prompt)
        self.assertIn("Use this checklist internally. Do not render it as a separate buyer-facing report section.", prompt)
        self.assertIn("Every empirical load-bearing claim either has a concrete evidence marker copied from PROJECT EVIDENCE LOCATORS", prompt)
        self.assertIn("No framework marker is used as project evidence", prompt)
        self.assertIn("No evidence ID or locator is invented", prompt)

    def test_report_prompt_filters_missing_locator_entries(self):
        state = make_completed_state("report-missing-locator")
        state.hypotheses[0].evidence_ids = ["ev-no-locator"]

        prompt = build_report_prompt(state)

        self.assertIn("No project evidence locators supplied", prompt)
        self.assertNotIn("[Evidence: ev-no-locator", prompt)
        self.assertNotIn("locator unavailable", prompt)

    def test_report_phase_prompt_file_contains_matching_citation_discipline(self):
        prompt_text = Path("prompts/phases/05-report.md").read_text(encoding="utf-8")

        self.assertIn("## Citation discipline", prompt_text)
        self.assertIn("Final report project-evidence citations must use concrete markers copied from `PROJECT EVIDENCE LOCATORS`", prompt_text)
        self.assertIn("Use the literal pipe character `|`. Do not escape it as `\\|`.", prompt_text)
        self.assertIn("Valid example: [Evidence: ev-market-note | chunk=2]", prompt_text)
        self.assertIn("Invalid: [Evidence: ev-market-note \\| chunk=2]", prompt_text)
        self.assertIn("Never output placeholder evidence markers.", prompt_text)
        self.assertIn("Do not output [Evidence: ...] or angle-bracket templates in the final report.", prompt_text)
        for invalid_marker in (
            "[Evidence: ...]",
            "[Evidence: <evidence_id> | <locator>]",
            "[Evidence: evidence_id | locator]",
            "[Evidence: ev-market-note | ...]",
            "[Evidence: ... | ...]",
        ):
            self.assertIn(f"Invalid: {invalid_marker}", prompt_text)
        self.assertIn("Each citation marker must contain exactly one evidence ID and one locator", prompt_text)
        self.assertIn("do not put semicolons or multiple Evidence tokens inside one marker", prompt_text)
        self.assertIn("Every evidence marker in the final report must copy a real evidence_id and locator from `PROJECT EVIDENCE LOCATORS`", prompt_text)
        self.assertIn("Framework markers such as [#24] are methodology references, not project evidence citations", prompt_text)
        self.assertIn("Do not cite the act of recommending; cite the empirical evidence behind the recommendation", prompt_text)
        self.assertIn("EXECUTIVE SUMMARY", prompt_text)
        self.assertIn("DECISION LOGIC", prompt_text)
        self.assertIn("EVIDENCE STRENGTH", prompt_text)
        self.assertIn("FINAL VERDICTS", prompt_text)
        self.assertIn("STRATEGY RESULTS", prompt_text)
        self.assertIn("MONITORING AND KILL CRITERIA", prompt_text)
        self.assertIn("## Evidence citation check before final output", prompt_text)
        self.assertIn("Use this checklist internally. Do not render it as a separate buyer-facing report section.", prompt_text)
        self.assertIn("concrete evidence marker copied from `PROJECT EVIDENCE LOCATORS`", prompt_text)
        self.assertIn("[Inference]", prompt_text)
        self.assertIn("[Hypothesis]", prompt_text)
        self.assertIn("[Unknown]", prompt_text)
        self.assertIn("## Report quality and factual safety", prompt_text)
        self.assertIn("## At a Glance", prompt_text)
        self.assertIn("Field` and `Detail` headers", prompt_text)
        self.assertIn("do not use raw comparison symbols", prompt_text)
        self.assertIn("Evidence Maturity", prompt_text)
        self.assertIn("Sprint 0 Evidence Pack Required", prompt_text)
        self.assertIn("GA4 data thresholds are system-defined", prompt_text)
        self.assertIn("Use INP for responsiveness", prompt_text)
        self.assertIn("Core Web Vitals and page experience align with Google Search ranking systems", prompt_text)
        self.assertIn("Consider FAQPage only where the page type and Google's current eligibility rules apply", prompt_text)
        self.assertIn("Structured data can make pages eligible for search features", prompt_text)
        for forbidden in (
            "500 MAU threshold",
            "GA4 Hispanic segment",
            "Google Signals threshold for Hispanic segment",
            "FID/INP",
            "direct ranking signal",
            "FAQPage for rich-result capture",
            "guaranteed rich results",
        ):
            self.assertNotIn(forbidden, prompt_text)
        self.assertNotIn("`[Evidence: <evidence_id> | <locator>]` is the only canonical project-evidence citation format", prompt_text)
        self.assertNotIn("canonical `[Evidence: ...]` marker", prompt_text)

    def test_report_prompt_raw_markdown_parser_detects_load_bearing_markers(self):
        report = """# EXECUTIVE SUMMARY
- Market demand increased [Evidence: ev-market | upload:file-market.pdf#page=2].

# WHY THIS IS RECOMMENDED
- Capacity is constrained [Evidence: ev-capacity | upload:file-capacity.xlsx#row=7].

# RED TEAM [#28]
- This is a methodology section [#28].
"""

        counts = report_load_bearing_marker_counts(report)

        self.assertEqual(counts["executive summary"], 1)
        self.assertEqual(counts["why this is recommended"], 1)
        self.assertEqual(counts["recommended path"], 0)
        self.assertNotIn("red team [#28]", counts)

    def test_report_evidence_locator_helper_does_not_mutate_state(self):
        state = make_completed_state("report-locator-no-mutation")
        state.knowledge_layer = KnowledgeLayerState(
            items=[
                KnowledgeItem(
                    evidence_id="ev-no-mutation",
                    source_ref="fixture://no-mutation",
                    locator="fixture://no-mutation#row=1",
                    title="No mutation note",
                )
            ]
        )
        before = state.model_dump(mode="json")

        _build_report_evidence_locator_register(state)

        self.assertEqual(state.model_dump(mode="json"), before)

    def test_report_evidence_locator_helper_excludes_raw_summaries(self):
        state = make_completed_state("report-locator-no-summary")
        state.knowledge_layer = KnowledgeLayerState(
            items=[
                KnowledgeItem(
                    evidence_id="ev-no-summary",
                    locator="fixture://summary#chunk=1",
                    title="Safe title",
                    summary="RAW_SUMMARY_SHOULD_NOT_APPEAR",
                    normalized_summary="RAW_NORMALIZED_SUMMARY_SHOULD_NOT_APPEAR",
                )
            ]
        )

        register = _build_report_evidence_locator_register(state)

        self.assertIn("[Evidence: ev-no-summary | fixture://summary#chunk=1]", register)
        self.assertIn("title=Safe title", register)
        self.assertNotIn("RAW_SUMMARY_SHOULD_NOT_APPEAR", register)
        self.assertNotIn("RAW_NORMALIZED_SUMMARY_SHOULD_NOT_APPEAR", register)

    def test_build_monitor_prompt_declares_required_schema(self):
        prompt = build_monitor_prompt(make_completed_state("monitor-prompt"))
        self.assertIn('"ooda_schedule"', prompt)
        self.assertIn('"circuit_breakers"', prompt)
        self.assertIn("Return ONE JSON object", prompt)
        self.assertIn("Include at least 3 canaries", prompt)

    def test_first_unfinished_phase_detects_missing_monitor_output(self):
        state = make_completed_state("missing-monitor")
        state.monitor = None
        self.assertEqual(get_first_unfinished_phase(state), "monitor")
        self.assertFalse(is_workflow_complete(state))

    def test_first_unfinished_phase_prefers_failed_phase(self):
        state = make_completed_state("failed-gauntlet")
        state.phase_status["gauntlet"] = PhaseStatus.FAILED
        self.assertEqual(get_first_unfinished_phase(state), "gauntlet")

    def test_raw_only_audit_does_not_count_as_output(self):
        state = make_completed_state("raw-only-audit")
        state.audit = None
        state.audit_raw = "raw diagnostic audit output"

        self.assertFalse(_phase_has_output(state, "audit"))

    def test_raw_only_strategy_does_not_count_as_output(self):
        state = make_completed_state("raw-only-strategy")
        state.strategy = None
        state.strategy_raw = "raw diagnostic strategy output"

        self.assertFalse(_phase_has_output(state, "strategy"))

    def test_completed_raw_only_audit_is_first_unfinished_phase(self):
        state = make_completed_state("completed-raw-only-audit")
        state.audit = None
        state.audit_raw = "raw diagnostic audit output"
        state.phase_status["audit"] = PhaseStatus.COMPLETED

        self.assertEqual(get_first_unfinished_phase(state), "audit")
        self.assertFalse(is_workflow_complete(state))

    def test_completed_raw_only_strategy_is_first_unfinished_phase(self):
        state = make_completed_state("completed-raw-only-strategy")
        state.strategy = None
        state.strategy_raw = "raw diagnostic strategy output"
        state.phase_status["strategy"] = PhaseStatus.COMPLETED

        self.assertEqual(get_first_unfinished_phase(state), "strategy")
        self.assertFalse(is_workflow_complete(state))


class TestPhaseBookkeeping(unittest.IsolatedAsyncioTestCase):
    async def test_hypotheses_success_sets_seal_and_confidence(self):
        state = ProjectState(project_id="hypo", project_name="Hypo", brief="brief")
        state.intake_sanitization_findings = {}
        state.classify = make_completed_state("seed").classify
        response = make_response(json.dumps(make_hypotheses_payload()), 12, 6, 0.02)
        with patch("orchestrator.call_llm", new=AsyncMock(return_value=response)):
            with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
                updated = await run_phase_node(state, "hypotheses")

        self.assertEqual(updated.phase_status["hypotheses"], PhaseStatus.COMPLETED)
        self.assertEqual(updated.phase_confidence["hypotheses"], 1.0)
        self.assertTrue(updated.sealed)
        self.assertTrue(updated.seal_date)

    async def test_audit_phase_repairs_truncated_object_when_required_fields_are_complete(self):
        state = make_completed_state("audit-phase-truncated-repair")
        state.phase_status["audit"] = PhaseStatus.PENDING
        state.audit = None
        state.audit_raw = "previous raw audit"
        payload = {
            "data_based": False,
            "fmea": [
                {
                    "component": "Editorial planning",
                    "failure_mode": "No keyword demand check",
                    "effect": "Low organic reach",
                    "s": 8,
                    "o": 7,
                    "d": 6,
                    "rpn": 336,
                    "action": "Add keyword gate",
                    "evidence": "Predicted from workflow gap",
                }
            ],
            "hazop": [
                {
                    "node": "Topic selection",
                    "guide_word": "NO",
                    "deviation": "No search demand validation",
                    "consequence": "Misaligned articles",
                    "evidence": "Predicted",
                }
            ],
            "stpa": [
                {
                    "control_action": "Approve topic",
                    "uca_type": "not provided",
                    "hazard": "Wrong target query",
                    "constraint": "Require keyword brief",
                }
            ],
            "fta": {"top_event": "Organic reach stays low", "cut_sets": ["No demand gate"], "prevention": "Add gate"},
            "swiss_cheese": {"layers": ["Brief", "Review"], "holes": ["No SEO check"]},
            "top_findings": ["Keyword demand is not part of topic approval."],
            "h_norm_estimate": "0.22",
            "observation_needs": ["GSC query export"],
        }
        truncated = json.dumps(payload)[:-1] + ', "appendix": "output truncates after audit fields'
        response = make_response(truncated, 18, 9, 0.04)

        with patch("orchestrator.call_llm", new=AsyncMock(return_value=response)):
            with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
                updated = await run_phase_node(state, "audit")

        self.assertEqual(updated.phase_status["audit"], PhaseStatus.COMPLETED)
        self.assertEqual(updated.phase_confidence["audit"], 1.0)
        self.assertIsNotNone(updated.audit)
        self.assertIsNone(updated.audit_raw)
        self.assertEqual(len(updated.audit.fmea), 1)
        self.assertEqual(updated.audit.fmea[0].component, "Editorial planning")
        self.assertEqual(updated.audit.top_findings, ["Keyword demand is not part of topic approval."])
        self.assertEqual(updated.audit.observation_needs, ["GSC query export"])

    async def test_malformed_audit_stores_raw_and_fails_phase(self):
        state = make_completed_state("audit-phase-malformed")
        state.phase_status["audit"] = PhaseStatus.PENDING
        state.audit = None
        state.audit_raw = None
        response = make_response("this is not valid audit JSON", 18, 9, 0.04)

        with patch("orchestrator.call_llm", new=AsyncMock(return_value=response)):
            with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
                updated = await run_phase_node(state, "audit")

        self.assertEqual(updated.phase_status["audit"], PhaseStatus.FAILED)
        self.assertEqual(updated.phase_confidence["audit"], 0.0)
        self.assertIsNone(updated.audit)
        self.assertEqual(updated.audit_raw, response.text)
        self.assertFalse(_phase_has_output(updated, "audit"))

    async def test_strategy_phase_repairs_truncated_object_when_required_fields_are_complete(self):
        state = make_completed_state("strategy-phase-truncated-repair")
        state.phase_status["strategy"] = PhaseStatus.PENDING
        state.strategy = None
        state.strategy_raw = "previous raw strategy"
        payload = {
            "preliminary_verdicts": [
                {
                    "id": "H1",
                    "verdict": "LIKELY_CONFIRMED",
                    "evidence": "Initial signal in user research",
                    "monitoring_plan": "Track weekly retention",
                }
            ],
            "executive_strategy": "Ship the keyword brief gate before the next editorial cycle.",
            "strategies": [
                {
                    "priority": "HIGH",
                    "action": "Implement keyword demand gate within sprint",
                    "justification": "Closes editorial planning gap",
                    "evidence_chain": "Audit FMEA RPN 336",
                    "expected_impact": "Lift organic reach by 20% within one quarter",
                    "effort": "2 weeks",
                    "timeline": "next sprint",
                    "risk_if_ignored": "Continued underperformance",
                    "framework_source": "FMEA",
                }
            ],
            "implementation_sequence": "Brief -> review -> publish",
            "success_metrics": ["Organic sessions"],
            "monitoring_plan": "Weekly GSC review",
            "review_date": "2026-06-01",
            "confidence": "moderate",
            "reentry_check": "Re-evaluate at 30 days",
        }
        truncated = json.dumps(payload)[:-1] + ', "appendix": "output truncates after strategy fields'
        response = make_response(truncated, 18, 9, 0.04)

        with patch("orchestrator.call_llm", new=AsyncMock(return_value=response)):
            with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
                updated = await run_phase_node(state, "strategy")

        self.assertEqual(updated.phase_status["strategy"], PhaseStatus.COMPLETED)
        self.assertEqual(updated.phase_confidence["strategy"], 1.0)
        self.assertIsNotNone(updated.strategy)
        self.assertIsNone(updated.strategy_raw)
        self.assertEqual(len(updated.strategy.strategies), 1)
        self.assertEqual(
            updated.strategy.executive_strategy,
            "Ship the keyword brief gate before the next editorial cycle.",
        )
        self.assertEqual(updated.strategy.preliminary_verdicts[0].id, "H1")

    async def test_malformed_strategy_stores_raw_and_fails_phase(self):
        state = make_completed_state("strategy-phase-malformed")
        state.phase_status["strategy"] = PhaseStatus.PENDING
        state.strategy = None
        state.strategy_raw = None
        response = make_response("this is not valid strategy JSON", 18, 9, 0.04)

        with patch("orchestrator.call_llm", new=AsyncMock(return_value=response)):
            with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
                updated = await run_phase_node(state, "strategy")

        self.assertEqual(updated.phase_status["strategy"], PhaseStatus.FAILED)
        self.assertEqual(updated.phase_confidence["strategy"], 0.0)
        self.assertIsNone(updated.strategy)
        self.assertEqual(updated.strategy_raw, response.text)
        self.assertFalse(_phase_has_output(updated, "strategy"))

    async def test_monitor_success_stores_output_and_confidence(self):
        state = make_completed_state("monitor-phase")
        state.phase_status["monitor"] = PhaseStatus.PENDING
        state.monitor = None
        response = make_response(json.dumps(make_monitor_payload()), 14, 7, 0.03)
        with patch("orchestrator.call_llm", new=AsyncMock(return_value=response)):
            with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
                updated = await run_phase_node(state, "monitor")

        self.assertEqual(updated.phase_status["monitor"], PhaseStatus.COMPLETED)
        self.assertEqual(updated.phase_confidence["monitor"], 1.0)
        self.assertIsNotNone(updated.monitor)
        self.assertEqual(updated.monitor.commitment_score, 81)

    async def test_monitor_phase_repairs_truncated_object_when_required_fields_are_complete(self):
        state = make_completed_state("monitor-phase-truncated-repair")
        state.phase_status["monitor"] = PhaseStatus.PENDING
        state.monitor = None
        payload = make_monitor_payload()
        truncated = (
            json.dumps(
                {
                    "ooda_schedule": payload["ooda_schedule"],
                    "circuit_breakers": payload["circuit_breakers"],
                    "canaries": payload["canaries"],
                    "chaos_drills": payload["chaos_drills"],
                    "hro_principles_active": payload["hro_principles_active"],
                    "reentry_watch": payload["reentry_watch"],
                    "commitment_score": payload["commitment_score"],
                    "commitment_rationale": payload["commitment_rationale"],
                }
            )[:-1]
            + ', "appendix": "output truncates after required monitor fields'
        )
        response = make_response(truncated, 14, 7, 0.03)

        with patch("orchestrator.call_llm", new=AsyncMock(return_value=response)):
            with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
                updated = await run_phase_node(state, "monitor")

        self.assertEqual(updated.phase_status["monitor"], PhaseStatus.COMPLETED)
        self.assertEqual(updated.phase_confidence["monitor"], 1.0)
        self.assertIsNotNone(updated.monitor)
        self.assertEqual(updated.monitor.commitment_score, 81)
        self.assertEqual(len(updated.monitor.canaries), 3)


class TestSequentialRunner(unittest.IsolatedAsyncioTestCase):
    async def test_runner_resumes_from_monitor_and_reruns_report(self):
        state = make_completed_state("resume-monitor")
        state.phase_status["monitor"] = PhaseStatus.PENDING
        state.monitor = None
        executed: list[str] = []
        persisted: list[tuple[str, str]] = []

        async def fake_persist(snapshot: ProjectState):
            persisted.append(
                (
                    snapshot.current_phase,
                    snapshot.phase_status.get(snapshot.current_phase, PhaseStatus.PENDING).value
                    if hasattr(snapshot.phase_status.get(snapshot.current_phase, PhaseStatus.PENDING), "value")
                    else str(snapshot.phase_status.get(snapshot.current_phase, PhaseStatus.PENDING)),
                )
            )

        async def fake_run_phase_node(snapshot: ProjectState, phase: str) -> ProjectState:
            executed.append(phase)
            snapshot.current_phase = phase
            snapshot.phase_status[phase] = PhaseStatus.COMPLETED
            snapshot.phase_confidence[phase] = 1.0
            if phase == "monitor":
                snapshot.monitor = MonitorOutput(**make_monitor_payload())
            elif phase == "report":
                snapshot.report = "rerun report"
            return snapshot

        with patch("orchestrator.run_phase_node", new=AsyncMock(side_effect=fake_run_phase_node)):
            with patch("orchestrator.check_gate", side_effect=lambda s, p: {"passed": True, "blocking": [], "confidence": s.phase_confidence.get(p, 1.0)}):
                updated = await run_workflow_sequence(state, persist_state=fake_persist)

        self.assertEqual(executed, ["monitor", "report"])
        self.assertEqual(updated.report, "rerun report")
        self.assertEqual(updated.phase_status["monitor"], PhaseStatus.COMPLETED)
        self.assertGreaterEqual(len(persisted), 4)

    async def test_runner_halts_after_raw_only_audit_failure(self):
        state = make_completed_state("halt-after-raw-audit")
        state.phase_status["audit"] = PhaseStatus.PENDING
        state.audit = None
        state.audit_raw = None
        state.phase_status["strategy"] = PhaseStatus.PENDING
        state.strategy = None
        state.strategy_raw = None
        response = make_response("this is not valid audit JSON", 18, 9, 0.04)

        with patch("orchestrator.call_llm", new=AsyncMock(return_value=response)):
            with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
                updated = await run_workflow_sequence(state)

        self.assertEqual(updated.phase_status["audit"], PhaseStatus.FAILED)
        self.assertEqual(updated.audit_raw, response.text)
        self.assertIsNone(updated.audit)
        self.assertEqual(updated.phase_status["strategy"], PhaseStatus.STALE)
        self.assertIsNone(updated.strategy)


class TestApiRunWorkflow(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        api.running.clear()

    async def asyncTearDown(self):
        api.running.clear()

    async def test_run_returns_already_complete_for_finished_project(self):
        state = make_completed_state("already-complete")
        with patch("api.store.load", new=AsyncMock(return_value=state)):
            response = await api.run_full_workflow(state.project_id, BackgroundTasks())
        self.assertEqual(response["status"], "already_complete")

    async def test_manual_phase_rejects_when_workflow_is_running(self):
        state = make_completed_state("running-project")
        api.running.add(state.project_id)
        with patch("api.store.load", new=AsyncMock(return_value=state)):
            with self.assertRaises(HTTPException) as ctx:
                await api.run_single_phase_endpoint(state.project_id, api.RunPhaseRequest(phase="report"))
        self.assertEqual(ctx.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main(verbosity=2)
