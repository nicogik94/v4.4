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
import report_freshness
from hypothesis_coverage import assess_hypothesis_variable_coverage
from llm_client import LLMResponse
from monitoring_templates import build_monitoring_template_rows
from runtime import run_state as workflow_run_state
from runtime import work_queue as workflow_queue
from orchestrator import (
    WORKFLOW_PHASE_SEQUENCE,
    _build_report_evidence_locator_register,
    _phase_has_output,
    _sanitize_report_context,
    build_classify_prompt,
    build_hypotheses_prompt,
    build_monitor_prompt,
    build_report_prompt,
    build_strategy_prompt,
    get_first_unfinished_phase,
    is_workflow_complete,
    normalize_strategy_payload,
    run_phase_node,
    run_workflow_sequence,
)
from report_quality import SPARSE_CONFIDENCE_RULE
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


def make_strategy_payload(reentry_check="Re-evaluate at 30 days") -> dict:
    return {
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
        "reentry_check": reentry_check,
    }


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


class TestHypothesisVariableCoverage(unittest.TestCase):
    def test_variable_coverage_detects_covered_categories(self):
        state = ProjectState(
            project_id="coverage-growth",
            project_name="Growth coverage",
            brief=(
                "Decide whether to expand a B2B pilot. The decision depends on "
                "segment demand, acquisition channel quality, pricing, D30 retention, "
                "measurement reliability, and implementation capacity."
            ),
            data="Sparse evidence only; validate before scaling.",
        )
        state.hypotheses = [
            Hypothesis(
                id="H1",
                text="Segment demand from mid-market buyers is the main driver.",
                justification="Validate customer segment demand with interviews before scaling.",
                signal="qualified segment pipeline",
                confirm=">=20 qualified buyers within 6 weeks",
                reject="<5 qualified buyers within 6 weeks",
                portfolio_cluster="demand",
            ),
            Hypothesis(
                id="H2",
                text="Paid acquisition channel quality can produce efficient pipeline.",
                justification="CAC and attribution must be measured before paid activation.",
                signal="channel CAC and conversion attribution",
                confirm="CAC payback within 90 days",
                reject="CAC payback above target by D90",
                portfolio_cluster="channel pricing",
            ),
            Hypothesis(
                id="H3",
                text="Retention and implementation capacity constrain expansion.",
                justification="Owner sign-off and support capacity determine rollout feasibility.",
                signal="D30 retention, support load, accountable owner approval",
                confirm="D30 retention >= 40% and owner approves rollout",
                reject="D30 retention < 25% or support SLA fails",
                portfolio_cluster="retention operations",
            ),
        ]

        coverage = assess_hypothesis_variable_coverage(state)

        for label in (
            "Demand / user segment",
            "Channel / acquisition",
            "Retention / repeat usage",
            "Monetization / pricing",
            "Operational capacity",
            "Data quality / measurement",
            "Owner / decision authority",
            "Time horizon / cadence",
            "Evidence required to validate",
        ):
            self.assertIn(label, coverage.covered_categories)
        self.assertNotIn("Channel / acquisition", coverage.missing_critical_categories)
        self.assertNotIn("Monetization / pricing", coverage.missing_critical_categories)

    def test_missing_critical_categories_are_advisory_and_do_not_mutate_hypotheses(self):
        state = ProjectState(
            project_id="coverage-advisory",
            project_name="Activation coverage",
            brief="Improve onboarding activation for trial users.",
        )
        state.hypotheses = [
            Hypothesis(
                id="H1",
                text="Onboarding friction reduces activation.",
                justification="[Hypothesis] Sparse brief only.",
                signal="activation rate",
                confirm="activation improves",
                reject="activation remains flat",
                portfolio_cluster="activation",
            )
        ]
        before = [hypothesis.model_dump(mode="json") for hypothesis in state.hypotheses]

        coverage = assess_hypothesis_variable_coverage(state)
        after = [hypothesis.model_dump(mode="json") for hypothesis in state.hypotheses]

        self.assertEqual(after, before)
        self.assertIn("Owner / decision authority", coverage.missing_critical_categories)
        self.assertIn("Time horizon / cadence", coverage.missing_critical_categories)
        self.assertTrue(any(need.category == "Owner / decision authority" for need in coverage.evidence_needs))
        self.assertEqual(state.phase_status["hypotheses"], PhaseStatus.PENDING)

    def test_narrow_non_growth_project_does_not_force_pricing_channel_or_retention(self):
        state = ProjectState(
            project_id="coverage-narrow",
            project_name="Claim safety",
            brief="Decide whether a legal-review SLA reduces claim-safety approval risk.",
        )
        state.hypotheses = [
            Hypothesis(
                id="H1",
                text="Legal review SLA reduces claim-safety approval risk.",
                justification="Compliance approval is the decision-critical constraint.",
                signal="approval cycle time measured by compliance owner",
                confirm="legal approval within 24 hours for 4 weeks",
                reject="approval exceeds SLA for two weeks",
                portfolio_cluster="claim-safety compliance",
            )
        ]

        coverage = assess_hypothesis_variable_coverage(state)

        for label in ("Monetization / pricing", "Channel / acquisition", "Retention / repeat usage"):
            self.assertNotIn(label, coverage.missing_critical_categories)
            self.assertIn(label, coverage.not_relevant_categories)
        self.assertIn("Legal / compliance / claim-safety", coverage.covered_categories)

    def test_sparse_evidence_creates_validation_needs_not_measured_claims(self):
        state = ProjectState(
            project_id="coverage-sparse",
            project_name="Sparse",
            brief="Evidence is unknown; evaluate whether onboarding changes matter.",
        )
        state.hypotheses = [
            Hypothesis(
                id="H1",
                text="Onboarding changes may improve activation.",
                justification="[Unknown] No direct evidence supplied.",
                signal="activation metric",
                confirm="activation threshold improves within 30 days",
                reject="activation remains below threshold within 30 days",
                portfolio_cluster="activation",
            )
        ]

        coverage = assess_hypothesis_variable_coverage(state)

        self.assertIn("Evidence required to validate", coverage.covered_categories)
        combined = " ".join(coverage.assumptions_needing_validation)
        self.assertNotIn("measured", combined.lower())
        self.assertNotIn("confirmed", combined.lower())

    def test_hypothesis_prompt_preserves_existing_json_schema(self):
        prompt = build_hypotheses_prompt(ProjectState(project_id="prompt-schema", project_name="Prompt", brief="Improve activation."))

        self.assertIn(
            "Each hypothesis object must include: id, text, justification, signal, alpha, beta, confirm, reject, evoi, portfolio_cluster, status.",
            prompt,
        )
        self.assertIn("Do not add any other keys to hypothesis objects.", prompt)
        self.assertNotIn("variable_coverage", prompt)
        self.assertNotIn("owner_decision_authority", prompt)

    def test_monitoring_template_rows_do_not_mutate_project_state(self):
        state = make_completed_state("monitor-template-no-mutation")
        before = state.model_dump(mode="json")

        rows = build_monitoring_template_rows(state)

        self.assertTrue(rows)
        self.assertEqual(state.model_dump(mode="json"), before)


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

    def test_classify_prompt_includes_cynefin_domain_guardrails(self):
        prompt = build_classify_prompt(
            ProjectState(project_id="domain-guardrails", brief="hi")
        )

        for expected in (
            "Simple:",
            "Complicated:",
            "Complex:",
            "Chaotic:",
            "Confused:",
            "brief is too short",
            "Return `domain` as exactly one of",
        ):
            self.assertIn(expected, prompt)

    def test_strategy_prompt_preserves_traceable_operator_terms(self):
        state = make_completed_state("strategy-traceability")
        prompt = build_strategy_prompt(state)

        self.assertIn("Preserve material operator terms", prompt)
        self.assertIn("technical method names", prompt)
        self.assertIn("named frameworks", prompt)
        self.assertIn("Do not replace load-bearing concepts with vague synonyms", prompt)
        self.assertIn("Make strategy concepts explicit", prompt)
        self.assertIn("Use explicit noun phrases for the decision variables", prompt)
        self.assertIn("Every strategy action's framework_source should name", prompt)

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

    def test_strategy_prompt_includes_original_brief_and_hard_constraint_rules(self):
        state = ProjectState(
            project_id="strategy-constraints",
            project_name="Strategy constraints",
            brief=(
                "Limited capacity this month. Recommend only one focused initiative plus "
                "one small experiment. No major engineering project this month. Budget is "
                "limited to one small experiment. Avoid broad growth spend until the cause is clearer."
            ),
        )

        prompt = build_strategy_prompt(state)

        self.assertIn("OPERATOR HARD CONSTRAINTS", prompt)
        self.assertIn("Limited capacity this month", prompt)
        self.assertIn("one focused initiative plus one small experiment", prompt)
        self.assertIn("Explicit capacity, budget, no-major-project, spend-freeze", prompt)
        self.assertIn("Do not convert a constrained plan into multiple parallel critical tracks", prompt)
        self.assertIn("unless the operator explicitly allowed that capacity", prompt)
        self.assertIn("priority must be exactly one of CRITICAL, HIGH, MEDIUM, LOW", prompt)
        self.assertIn("For deferred/blocked/do-not-do items, use priority LOW", prompt)
        self.assertIn(
            "put \"DEFERRED\", \"BLOCKED\", \"DO NOT START\", or \"DO NOT DO\" in the action/title/justification",
            prompt,
        )

    def test_report_prompt_preserves_constrained_strategy_shape(self):
        state = ProjectState(
            project_id="report-constraints",
            project_name="Report constraints",
            brief=(
                "Limited capacity this month. Only one focused initiative plus one small "
                "experiment is possible. No major engineering project this month. Avoid broad "
                "growth spend until the cause is clearer."
            ),
        )

        prompt = build_report_prompt(state)

        self.assertIn("Preserve constrained strategy shape", prompt)
        self.assertIn("one focused initiative plus one small experiment", prompt)
        self.assertIn("do not expand it into several parallel tracks", prompt)
        self.assertIn("Defer major engineering work or broad growth spend", prompt)
        self.assertIn("unless operator hard constraints require fewer actions", prompt)
        self.assertIn("include only the action count that fits the explicit constraint", prompt)

    def test_report_prompt_includes_factual_safety_and_research_depth_rules(self):
        state = make_completed_state("report-factual-safety")
        state.project_name = "SEO content growth"
        state.brief = "Improve website traffic with SEO content, Search Console, GA4, crawl, and CMS review."
        prompt = build_report_prompt(state)

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
            SPARSE_CONFIDENCE_RULE,
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

    def test_sparse_no_clarification_report_prompt_includes_caveats(self):
        state = ProjectState(
            project_id="sparse-report-prompt",
            project_name="Sparse report prompt",
            brief="Decide productization direction for v4 dashboard exports.",
        )

        prompt = build_report_prompt(state)

        self.assertIn("This is a structured hypothesis map, not a measured audit.", prompt)
        self.assertNotIn(
            "Provisional report: decision-critical clarification questions have not been answered. "
            "This is suitable for internal review only. Answer clarifications and regenerate before client delivery.",
            prompt,
        )
        self.assertIn("BF, DQ, RPN, H_norm, correlation/rho, priors, probabilities, dollars, and percentages", prompt)
        self.assertIn(SPARSE_CONFIDENCE_RULE, prompt)

    def test_productization_report_prompt_uses_product_roles_and_evidence(self):
        state = ProjectState(
            project_id="productization-prompt",
            project_name="Productization prompt",
            brief=(
                "Decide the v4 productization direction for dashboard exports, "
                "regeneration workflow, pilot users, product telemetry, and validation batch."
            ),
        )

        prompt = build_report_prompt(state)

        for expected in (
            "Decision domain: productization",
            "Product Owner",
            "Engineering Lead",
            "Privacy or Data Governance Reviewer",
            "product telemetry",
            "pilot sessions",
            "report validation batch",
            "template schema / field registry validation",
            "Wave 2 Graduation Matrix",
            "operator-set threshold",
        ):
            self.assertIn(expected, prompt)
        for forbidden in (
            "GSC 12-month URL/query export",
            "GA4 audience/acquisition check",
            "CMS/schema/canonical capability check",
            "SEO Lead",
            "Editorial Lead",
            "Web/CMS Owner",
        ):
            self.assertNotIn(forbidden, prompt)

    def test_sparse_generic_growth_prompt_uses_growth_evidence_without_web_leakage(self):
        state = ProjectState(
            project_id="sparse-growth-prompt",
            project_name="Growth performance",
            brief="Improve growth performance across revenue operations, sales, retention, churn, and pipeline.",
        )
        state.report = "Generated text mentions Search Console, GA4, crawl, editorial, CMS/schema capability, SEO Lead, and Web/CMS Owner."

        prompt = build_report_prompt(state)

        for expected in (
            "Decision domain: growth",
            "Growth Lead",
            "Revenue Operations Lead",
            "Product Analytics Lead",
            "cohort retention",
            "CAC / LTV",
            "pipeline conversion",
            "win/loss analysis",
            "product usage / activation",
            "customer success signals",
        ):
            self.assertIn(expected, prompt)
        for forbidden in (
            "Search Console",
            "GA4",
            "crawl/technical evidence",
            "editorial workflow evidence",
            "CMS/schema capability",
            "SEO Lead",
            "Web/CMS Owner",
        ):
            self.assertNotIn(forbidden, prompt)

    def test_sparse_seo_growth_prompt_may_use_web_evidence_when_explicit(self):
        state = ProjectState(
            project_id="seo-growth-prompt",
            project_name="SEO growth",
            brief="Improve website traffic with SEO content, Search Console, GA4, crawl, and CMS workflow evidence.",
        )

        prompt = build_report_prompt(state)

        self.assertIn("Decision domain: seo_content_editorial", prompt)
        self.assertIn("Search Console", prompt)
        self.assertIn("GA4", prompt)
        self.assertIn("Web/CMS Owner", prompt)

    def test_non_seo_domains_do_not_receive_seo_owner_roles(self):
        cases = [
            ("growth", "Improve growth performance across revenue operations, sales, customer success, retention, and pipeline.", "Growth Lead"),
            ("ai_readiness", "Assess AI readiness, data readiness, model governance, security, legal compliance, and training.", "AI Program Lead"),
            ("automation_roi", "Evaluate automation ROI for a manual process workflow with operations, finance, and change training.", "IT/Automation Lead"),
        ]

        for project_id, brief, expected_role in cases:
            with self.subTest(project_id=project_id):
                prompt = build_report_prompt(ProjectState(project_id=project_id, project_name=project_id, brief=brief))

                self.assertIn(expected_role, prompt)
                self.assertNotIn("SEO Lead", prompt)
                self.assertNotIn("Editorial Lead", prompt)
                self.assertNotIn("Web/CMS Owner", prompt)

    def test_report_prompt_includes_telemetry_privacy_rule(self):
        state = ProjectState(
            project_id="telemetry-privacy-prompt",
            project_name="Telemetry privacy",
            brief="Recommend dashboard telemetry, regeneration-event logging, session replay, and product analytics for the product pilot.",
        )

        prompt = build_report_prompt(state)

        self.assertIn("Log event metadata by default.", prompt)
        self.assertIn("Do not log raw briefs, uploaded content, report text, provider payloads, secrets, local paths, API keys, or sensitive user text", prompt)

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
        self.assertIn("This is a structured hypothesis map, not a measured audit.", prompt_text)
        self.assertIn("Provisional report: clarification questions have not been answered.", prompt_text)
        self.assertIn(SPARSE_CONFIDENCE_RULE, prompt_text)
        self.assertIn("domain-specific owner roles", prompt_text)
        self.assertIn("cohort retention", prompt_text)
        self.assertIn("CAC/LTV", prompt_text)
        self.assertIn("product telemetry", prompt_text)
        self.assertIn("template schema / field registry validation", prompt_text)
        self.assertIn("Wave 2 Graduation Matrix", prompt_text)
        self.assertIn("do not invent new numeric thresholds", prompt_text)
        self.assertIn("Log event metadata by default.", prompt_text)
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

    def test_prompt_markdown_files_include_constraint_adherence_language(self):
        strategy_text = Path("prompts/phases/03-strategy.md").read_text(encoding="utf-8")
        report_text = Path("prompts/phases/05-report.md").read_text(encoding="utf-8")

        self.assertIn("Operator hard constraints", strategy_text)
        self.assertIn("one focused initiative plus one small experiment", strategy_text)
        self.assertIn("Do not convert a constrained plan into multiple parallel critical tracks", strategy_text)
        self.assertIn("priority must be exactly one of `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`", strategy_text)
        self.assertIn("For deferred/blocked/do-not-do items, use priority `LOW`", strategy_text)
        self.assertIn("Operator hard constraints", report_text)
        self.assertIn("Do not expand constrained recommendations into several parallel tracks", report_text)
        self.assertIn("Do not force a 5-7 item next-step list", report_text)

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

    def test_strategy_reentry_check_string_passes_unchanged(self):
        payload = make_strategy_payload("Re-evaluate at 30 days")

        strategy = StrategyOutput(**normalize_strategy_payload(payload))

        self.assertEqual(strategy.reentry_check, "Re-evaluate at 30 days")

    def test_strategy_reentry_check_dict_normalizes_to_compact_json(self):
        payload = make_strategy_payload({"triggers": ["R8", "R1"], "target": "monitor"})

        strategy = StrategyOutput(**normalize_strategy_payload(payload))

        self.assertEqual(strategy.reentry_check, '{"target":"monitor","triggers":["R8","R1"]}')

    def test_strategy_reentry_check_list_normalizes_to_compact_json(self):
        payload = make_strategy_payload([{"target": "monitor"}, {"target": "audit"}])

        strategy = StrategyOutput(**normalize_strategy_payload(payload))

        self.assertEqual(strategy.reentry_check, '[{"target":"monitor"},{"target":"audit"}]')

    def test_strategy_reentry_check_missing_normalizes_to_empty_string(self):
        payload = make_strategy_payload()
        del payload["reentry_check"]

        strategy = StrategyOutput(**normalize_strategy_payload(payload))

        self.assertEqual(strategy.reentry_check, "")

    def test_strategy_reentry_check_none_normalizes_to_empty_string(self):
        payload = make_strategy_payload(None)

        strategy = StrategyOutput(**normalize_strategy_payload(payload))

        self.assertEqual(strategy.reentry_check, "")

    def test_strategy_priority_aliases_normalize_to_low_without_losing_semantics(self):
        payload = make_strategy_payload()
        payload["strategies"] = [
            {
                "priority": "DEFERRED",
                "action": "Sales follow-up cadence repair (DEFERRED - data dependency)",
                "justification": "DEFERRED until CRM coverage is measured.",
                "evidence_chain": "H1 + audit",
            },
            {
                "priority": "BLOCKED",
                "action": "Analytics instrumentation repair (BLOCKED - owner approval)",
                "justification": "BLOCKED until RevOps confirms the source of truth.",
                "evidence_chain": "H1 + audit",
            },
            {
                "priority": "DEFER",
                "action": "Defer paid acquisition expansion",
                "justification": "DEFER broad spend until CAC attribution is trusted.",
                "evidence_chain": "H2 + audit",
            },
            {
                "priority": "DO_NOT_START",
                "action": "New lifecycle automation (DO NOT START - data dependency)",
                "justification": "DO NOT START until contact health is repaired.",
                "evidence_chain": "H3 + audit",
            },
            {
                "priority": "DO_NOT_DO",
                "action": "Broad growth spend (DO NOT DO this month)",
                "justification": "DO NOT DO while the operator budget is limited to a small experiment.",
                "evidence_chain": "H2 + audit",
            },
            {
                "priority": "PARKED",
                "action": "Parked data warehouse migration",
                "justification": "PARKED because it would violate the no-major-engineering constraint.",
                "evidence_chain": "H3 + audit",
            },
        ]

        strategy = StrategyOutput(**payload)

        self.assertEqual([item.priority for item in strategy.strategies], [Priority.LOW] * 6)
        visible_text = "\n".join(
            f"{item.action}\n{item.justification}" for item in strategy.strategies
        )
        for marker in ("DEFERRED", "BLOCKED", "DEFER", "DO NOT START", "DO NOT DO", "PARKED"):
            self.assertIn(marker, visible_text)

    def test_unknown_strategy_priority_still_fails_validation(self):
        payload = make_strategy_payload()
        payload["strategies"][0]["priority"] = "SOMEDAY"

        with self.assertRaises(Exception):
            StrategyOutput(**payload)

    def test_strategy_normalization_does_not_weaken_unrelated_validation(self):
        payload = make_strategy_payload({"target": "monitor"})
        payload["preliminary_verdicts"] = [{"verdict": "LIKELY_CONFIRMED"}]

        with self.assertRaises(Exception):
            StrategyOutput(**normalize_strategy_payload(payload))


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

    async def test_hypotheses_null_optional_fields_are_coerced_and_phase_completes(self):
        """Null values on optional string fields (signal, evoi, etc.) must not fail
        the entire phase — they should be coerced to empty string defaults."""
        state = ProjectState(project_id="hypo-null", project_name="Sparse", brief="Improving growth performance")
        state.intake_sanitization_findings = {}
        state.classify = make_completed_state("seed").classify
        payload_with_nulls = [
            {
                "id": "H1",
                "text": "We believe traffic is underperforming.",
                "justification": "[Hypothesis] Based on brief only.",
                "signal": None,          # null optional field
                "alpha": 6,
                "beta": 4,
                "confirm": "CTR >= 5%",
                "reject": "CTR < 2%",
                "evoi": None,            # null optional field
                "portfolio_cluster": None,  # null optional field
                "status": "OPEN",
            },
            {
                "id": "H2",
                "text": "We believe content gap is a key driver.",
                "justification": "[Unknown] No data supplied.",
                "signal": "keyword gap count",
                "alpha": 5,
                "beta": 5,
                "confirm": ">20 gaps identified",
                "reject": "<5 gaps identified",
                "evoi": "high",
                "portfolio_cluster": "distribution",
                "status": "OPEN",
            },
            {
                "id": "H3",
                "text": "We believe technical issues reduce crawlability.",
                "justification": "[Hypothesis] Common pattern for sparse briefs.",
                "signal": None,
                "alpha": 4,
                "beta": 6,
                "confirm": "crawl errors < 5%",
                "reject": "crawl errors > 20%",
                "evoi": "medium",
                "portfolio_cluster": None,
                "status": "OPEN",
            },
        ]
        response = make_response(json.dumps(payload_with_nulls), 15, 8, 0.03)
        with patch("orchestrator.call_llm", new=AsyncMock(return_value=response)):
            with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
                updated = await run_phase_node(state, "hypotheses")

        self.assertEqual(updated.phase_status["hypotheses"], PhaseStatus.COMPLETED)
        self.assertEqual(len(updated.hypotheses), 3)
        self.assertEqual(updated.hypotheses[0].signal, "")
        self.assertEqual(updated.hypotheses[0].evoi, "")
        self.assertEqual(updated.hypotheses[0].portfolio_cluster, "")
        self.assertTrue(updated.sealed)

    async def test_hypotheses_partial_schema_failure_recovers_valid_items(self):
        """If one hypothesis item fails Pydantic validation, the remaining valid
        items must still be stored and the phase must complete."""
        state = ProjectState(project_id="hypo-partial", project_name="Partial", brief="Test")
        state.intake_sanitization_findings = {}
        state.classify = make_completed_state("seed").classify
        payload_mixed = [
            # Valid
            {"id": "H1", "text": "Hypothesis one", "alpha": 6, "beta": 4,
             "confirm": ">60%", "reject": "<30%"},
            # Invalid: missing required "id" field — will be skipped
            {"text": "Missing id field", "alpha": 5, "beta": 5,
             "confirm": ">50%", "reject": "<20%"},
            # Valid
            {"id": "H3", "text": "Hypothesis three", "alpha": 7, "beta": 3,
             "confirm": ">70%", "reject": "<40%"},
            # Valid
            {"id": "H4", "text": "Hypothesis four", "alpha": 5, "beta": 5,
             "confirm": ">55%", "reject": "<25%"},
        ]
        response = make_response(json.dumps(payload_mixed), 14, 7, 0.02)
        with patch("orchestrator.call_llm", new=AsyncMock(return_value=response)):
            with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
                updated = await run_phase_node(state, "hypotheses")

        self.assertEqual(updated.phase_status["hypotheses"], PhaseStatus.COMPLETED)
        self.assertEqual(len(updated.hypotheses), 3)
        self.assertNotIn("Missing id field", [h.text for h in updated.hypotheses])

    def test_hypotheses_prompt_sparse_brief_includes_evidence_guidance(self):
        """When state.data is empty the hypotheses prompt must include the sparse
        evidence note instructing the LLM to label claims as [Hypothesis]/[Unknown]."""
        from orchestrator import build_hypotheses_prompt
        state = ProjectState(
            project_id="sparse-prompt",
            project_name="Sparse project",
            brief="Improving growth performance",
        )
        state.classify = make_completed_state("seed").classify
        prompt = build_hypotheses_prompt(state)
        self.assertIn("SPARSE EVIDENCE NOTE", prompt)
        self.assertIn("[Hypothesis]", prompt)
        self.assertIn("[Unknown]", prompt)
        self.assertIn("Do NOT invent evidence IDs", prompt)

    def test_hypotheses_prompt_data_present_omits_sparse_note(self):
        """When state.data is non-empty the sparse evidence note must not appear."""
        from orchestrator import build_hypotheses_prompt
        state = ProjectState(
            project_id="rich-prompt",
            project_name="Rich project",
            brief="Improving growth performance",
            data="GA4 sessions: 50000/month, CTR 2.1%, bounce 65%",
        )
        state.classify = make_completed_state("seed").classify
        prompt = build_hypotheses_prompt(state)
        self.assertNotIn("SPARSE EVIDENCE NOTE", prompt)

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

    async def test_strategy_phase_normalizes_dict_reentry_check_and_completes(self):
        state = make_completed_state("strategy-phase-dict-reentry-check")
        state.phase_status["strategy"] = PhaseStatus.PENDING
        state.strategy = None
        state.strategy_raw = "previous raw strategy"
        payload = make_strategy_payload({"triggers": ["R8", "R1"], "target": "monitor"})
        response = make_response(json.dumps(payload), 18, 9, 0.04)

        with patch("orchestrator.call_llm", new=AsyncMock(return_value=response)):
            with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
                updated = await run_phase_node(state, "strategy")

        self.assertEqual(updated.phase_status["strategy"], PhaseStatus.COMPLETED)
        self.assertEqual(updated.phase_confidence["strategy"], 1.0)
        self.assertIsNotNone(updated.strategy)
        self.assertIsNone(updated.strategy_raw)
        self.assertEqual(
            updated.strategy.reentry_check,
            '{"target":"monitor","triggers":["R8","R1"]}',
        )

    async def test_strategy_phase_normalizes_deferred_priority_aliases_and_preserves_wording(self):
        state = make_completed_state("strategy-phase-priority-aliases")
        state.phase_status["strategy"] = PhaseStatus.PENDING
        state.strategy = None
        state.strategy_raw = "previous raw strategy"
        payload = make_strategy_payload("Re-evaluate at 30 days")
        payload["strategies"] = [
            {
                "priority": "DEFERRED",
                "action": "Sales Follow-Up Cadence Repair (DO NOT START - data dependency)",
                "justification": "DEFERRED until CRM source coverage and owner approval are available.",
                "evidence_chain": "H1 + audit",
                "expected_impact": "Prevents premature execution.",
                "effort": "Low",
                "timeline": "after data readiness",
                "risk_if_ignored": "Team starts work before the blocker is resolved.",
                "framework_source": "PREMORTEM",
            },
            {
                "priority": "BLOCKED",
                "action": "Lifecycle automation expansion (BLOCKED - attribution gap)",
                "justification": "BLOCKED until the operator resolves attribution and budget constraints.",
                "evidence_chain": "H2 + audit",
                "expected_impact": "Avoids broad spend before measurement is trusted.",
                "effort": "Low",
                "timeline": "after attribution repair",
                "risk_if_ignored": "Budget is spent without a reliable signal.",
                "framework_source": "EVOI",
            },
        ]
        response = make_response(json.dumps(payload), 18, 9, 0.04)

        with patch("orchestrator.call_llm", new=AsyncMock(return_value=response)):
            with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
                updated = await run_phase_node(state, "strategy")

        self.assertEqual(updated.phase_status["strategy"], PhaseStatus.COMPLETED)
        self.assertEqual(updated.phase_confidence["strategy"], 1.0)
        self.assertIsNotNone(updated.strategy)
        self.assertIsNone(updated.strategy_raw)
        self.assertEqual([item.priority for item in updated.strategy.strategies], [Priority.LOW, Priority.LOW])
        self.assertIn("DO NOT START", updated.strategy.strategies[0].action)
        self.assertIn("DEFERRED", updated.strategy.strategies[0].justification)
        self.assertIn("BLOCKED", updated.strategy.strategies[1].action)
        self.assertIn("BLOCKED", updated.strategy.strategies[1].justification)

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

    async def test_pre_attempt_governance_denial_fails_without_retry_or_fallback(self):
        state = make_completed_state("pre-attempt-governance")
        state.phase_status["monitor"] = PhaseStatus.PENDING
        state.monitor = None

        async def fake_call_llm(*args, **kwargs):
            state.kill_switch_active = True
            state.kill_switch_reason = "operator stopped during provider routing"
            gate_result = await kwargs["before_attempt"](None)
            return LLMResponse(
                ok=False,
                error=gate_result["reason"],
                error_type=gate_result["category"],
                model_used="",
                provider_used="",
                fallback_used=False,
                attempt_count=0,
            )

        call_mock = AsyncMock(side_effect=fake_call_llm)
        with patch("orchestrator.call_llm", new=call_mock):
            with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
                updated = await run_phase_node(state, "monitor")

        self.assertEqual(call_mock.await_count, 1)
        self.assertEqual(updated.phase_status["monitor"], PhaseStatus.FAILED)
        self.assertFalse(any(
            event.get("event_type") == "llm_route"
            and event.get("details", {}).get("fallback_used")
            for event in updated.policy_audit_log
        ))

    async def test_schema_invalid_success_uses_existing_repair_path_not_provider_fallback(self):
        state = make_completed_state("schema-invalid-no-provider-fallback")
        state.phase_status["audit"] = PhaseStatus.PENDING
        state.audit = None
        state.audit_raw = None
        first = make_response("not valid audit JSON", 18, 9, 0.04)
        second = make_response("still not valid audit JSON", 18, 9, 0.04)

        call_mock = AsyncMock(side_effect=[first, second])
        with patch("orchestrator.call_llm", new=call_mock):
            with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
                updated = await run_phase_node(state, "audit")

        self.assertEqual(call_mock.await_count, 2)
        self.assertEqual(updated.phase_status["audit"], PhaseStatus.FAILED)
        self.assertEqual(updated.audit_raw, second.text)
        route_events = [
            event.get("details", {})
            for event in updated.policy_audit_log
            if event.get("event_type") == "llm_route"
        ]
        self.assertEqual(len(route_events), 2)
        self.assertFalse(any(event.get("fallback_used") for event in route_events))

    async def test_safe_llm_route_metadata_is_recorded_without_sensitive_content(self):
        state = make_completed_state("safe-route-metadata")
        state.phase_status["monitor"] = PhaseStatus.PENDING
        state.monitor = None
        response = LLMResponse(
            text=json.dumps(make_monitor_payload()),
            ok=True,
            model_used="gpt-5-mini",
            provider_used="openai",
            selected_provider="anthropic",
            selected_model="claude-sonnet-4-6",
            selection_reason="phase_routing",
            task_profile="monitoring_ops",
            fallback_used=True,
            fallback_reason="rate_limited",
            fallback_provider="openai",
            fallback_model="gpt-5-mini",
            failed_provider="anthropic",
            failed_model="claude-sonnet-4-6",
            failed_error_type="rate_limited",
            attempt_count=2,
            input_tokens=14,
            output_tokens=7,
            cost_usd=0.03,
            latency_ms=12,
        )

        with patch("orchestrator.call_llm", new=AsyncMock(return_value=response)):
            with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
                updated = await run_phase_node(state, "monitor")

        route_events = [
            event.get("details", {})
            for event in updated.policy_audit_log
            if event.get("event_type") == "llm_route"
        ]
        self.assertTrue(route_events)
        details = route_events[-1]
        self.assertEqual(details["task_profile"], "monitoring_ops")
        self.assertEqual(details["selected_provider"], "anthropic")
        self.assertEqual(details["final_provider"], "openai")
        self.assertTrue(details["fallback_used"])
        serialized = str(details)
        for sentinel in (
            "RAW_PROMPT_SENTINEL",
            "RAW_RESPONSE_SENTINEL",
            "sk-test-secret",
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "C:\\Users\\example\\secret.txt",
        ):
            self.assertNotIn(sentinel, serialized)

    async def test_report_success_records_generation_metadata(self):
        state = make_completed_state("report-generation-metadata")
        state.phase_status["report"] = PhaseStatus.PENDING
        state.report = None
        response = make_response("new report body", 14, 7, 0.03)

        with patch("orchestrator.call_llm", new=AsyncMock(return_value=response)):
            with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
                with patch("report_freshness.current_code_version", return_value="meta123"):
                    updated = await run_phase_node(state, "report")

        events = [
            event for event in updated.policy_audit_log
            if event.get("event_type") == "report_generated"
        ]
        self.assertTrue(events)
        details = events[-1]["details"]
        self.assertEqual(updated.report, "new report body")
        self.assertEqual(details["phase"], "report")
        self.assertEqual(details["code_version"], "meta123")
        self.assertNotEqual(details["code_version"], report_freshness.UNKNOWN_CODE_VERSION)
        self.assertEqual(details["report_sha256"], report_freshness.report_sha256("new report body"))
        self.assertEqual(details["report_length"], len("new report body"))
        self.assertTrue(details["generated_at"])

    async def test_report_regeneration_records_updated_generation_metadata(self):
        state = make_completed_state("report-regeneration-metadata")
        responses = [
            make_response("first regenerated report", 14, 7, 0.03),
            make_response("second regenerated report", 14, 7, 0.03),
        ]

        with patch("orchestrator.call_llm", new=AsyncMock(side_effect=responses)):
            with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
                with patch("report_freshness.current_code_version", side_effect=["old111", "new222"]):
                    updated = await run_phase_node(state, "report")
                    updated = await run_phase_node(updated, "report")

        events = [
            event for event in updated.policy_audit_log
            if event.get("event_type") == "report_generated"
        ]
        self.assertGreaterEqual(len(events), 2)
        first = events[-2]["details"]
        second = events[-1]["details"]
        self.assertEqual(updated.report, "second regenerated report")
        self.assertEqual(first["code_version"], "old111")
        self.assertEqual(second["code_version"], "new222")
        self.assertEqual(second["report_sha256"], report_freshness.report_sha256("second regenerated report"))
        self.assertNotEqual(first["report_sha256"], second["report_sha256"])

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


    async def test_classify_gate_quality_shortfall_does_not_block_workflow(self):
        """classify gate BF/DQ threshold failures are quality warnings, not
        structural errors.  The sequential runner must force-proceed so that
        sparse/low-evidence projects are not permanently blocked at classify."""
        state = ProjectState(
            project_id="sparse-classify",
            project_name="Sparse",
            brief="Improving growth performance",
        )
        state.intake_sanitization_findings = {}
        executed: list[str] = []

        async def fake_run_phase_node(snapshot: ProjectState, phase: str) -> ProjectState:
            executed.append(phase)
            snapshot.current_phase = phase
            snapshot.phase_status[phase] = PhaseStatus.COMPLETED
            snapshot.phase_confidence[phase] = 1.0
            if phase == "classify":
                snapshot.classify = make_completed_state("seed").classify
            elif phase == "hypotheses":
                snapshot.hypotheses = [Hypothesis(**h) for h in make_hypotheses_payload()]
                snapshot.sealed = True
            elif phase == "gauntlet":
                from state import GauntletOutput, GauntletResult
                snapshot.gauntlet = GauntletOutput(results=[
                    GauntletResult(id="H1", risk_rank=1,
                                   frameworks=[{"fw": "STEELMAN", "finding": "x", "action": True}] * 10,
                                   crux="crux")
                ])
            return snapshot

        def fake_gate(s, p):
            if p == "classify":
                # BF and DQ below threshold — quality shortfall, not structural
                return {
                    "passed": False,
                    "blocking": ["BF=5.0, need >10", "DQ=29%, need >=60%"],
                    "confidence": 1.0,
                }
            return {"passed": True, "blocking": [], "confidence": 1.0}

        with patch("orchestrator.run_phase_node", new=AsyncMock(side_effect=fake_run_phase_node)):
            with patch("orchestrator.check_gate", side_effect=fake_gate):
                updated = await run_workflow_sequence(state)

        # classify must remain COMPLETED (not FAILED) despite quality gate shortfall
        self.assertEqual(updated.phase_status.get("classify"), PhaseStatus.COMPLETED)
        # workflow must have continued past classify
        self.assertIn("hypotheses", executed)

    async def test_classify_gate_missing_output_halts_workflow(self):
        """classify gate structural failure (no output) must still halt the workflow."""
        state = ProjectState(
            project_id="classify-no-output",
            project_name="Sparse",
            brief="Brief",
        )
        state.intake_sanitization_findings = {}
        executed: list[str] = []

        async def fake_run_phase_node(snapshot: ProjectState, phase: str) -> ProjectState:
            executed.append(phase)
            snapshot.current_phase = phase
            # classify completes but produces no output (state.classify remains None)
            snapshot.phase_status[phase] = PhaseStatus.COMPLETED
            snapshot.phase_confidence[phase] = 1.0
            return snapshot

        def fake_gate(s, p):
            if p == "classify":
                return {
                    "passed": False,
                    "blocking": [f"{p} has no output yet"],
                    "confidence": 0.0,
                }
            return {"passed": True, "blocking": [], "confidence": 1.0}

        with patch("orchestrator.run_phase_node", new=AsyncMock(side_effect=fake_run_phase_node)):
            with patch("orchestrator.check_gate", side_effect=fake_gate):
                updated = await run_workflow_sequence(state)

        self.assertEqual(updated.phase_status.get("classify"), PhaseStatus.FAILED)
        # workflow must not have continued past classify
        self.assertNotIn("hypotheses", executed)

    async def test_classify_null_bf_and_dq_fields_are_coerced(self):
        """_store_phase_output must not raise when the LLM returns null for bf or dq."""
        from orchestrator import _store_phase_output
        state = ProjectState(project_id="null-classify", project_name="N", brief="B")
        data = {
            "domain": "Complex",
            "justification": "Sparse brief.",
            "bf": None,    # LLM emitted null
            "variety_env": "Unknown",
            "variety_sys": "Unknown",
            "variety_gaps": "1. No data",
            "variety_decision": "Amplify",
            "ooda": {"observe": "metrics", "orient": "gaps", "decide": "act",
                     "act": "iterate", "freq": "weekly"},
            "rpd_pattern": "",
            "dq": None,    # LLM emitted null
        }
        _store_phase_output(state, "classify", data)
        self.assertIsNotNone(state.classify)
        self.assertEqual(state.classify.bf, 0.0)
        self.assertEqual(state.classify.dq, [0.0, 0.0, 0.0, 0.0])

class _RunStateAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _RunStatePool:
    def __init__(self):
        self.conn = _RunStateConn()

    def acquire(self):
        return _RunStateAcquire(self.conn)


class _RunStateConn:
    def __init__(self):
        self.rows = {}
        self.active_by_project = {}
        self.jobs = {}
        self.active_jobs_by_run = {}
        self.heartbeat_counter = 0
        self.job_counter = 0

    async def execute(self, query, *args):
        return "OK"

    async def fetchval(self, query, *args):
        normalized = " ".join(query.split()).upper()
        if "COALESCE(HEARTBEAT_AT" in normalized:
            excluded = self._excluded_projects(args)
            return sum(
                1
                for row in self.rows.values()
                if self._is_active_stale(row) and row["project_id"] not in excluded
            )
        if "FROM WORKFLOW_RUNS" in normalized:
            return sum(1 for row in self.rows.values() if row["status"] in workflow_run_state.ACTIVE_RUN_STATUSES)
        return len(self.active_by_project)

    async def fetch(self, query, *args):
        normalized = " ".join(query.split()).upper()
        if "FROM WORKFLOW_JOBS" in normalized and "GROUP BY STATUS" in normalized:
            counts = {}
            for row in self.jobs.values():
                counts[row["status"]] = counts.get(row["status"], 0) + 1
            return [{"status": status, "count": count} for status, count in counts.items()]
        if "WITH STALE AS" in normalized and "UPDATE WORKFLOW_RUNS" in normalized:
            limit = int(args[1])
            excluded = self._excluded_projects(args)
            recovered = []
            for row in sorted(self.rows.values(), key=lambda item: item.get("heartbeat_at") or ""):
                if len(recovered) >= limit:
                    break
                if row["project_id"] in excluded:
                    continue
                if self._is_active_stale(row):
                    row["status"] = "failed"
                    row["finished_at"] = "2026-05-22T00:02:00+00:00"
                    row["heartbeat_at"] = self._next_heartbeat()
                    row["error_summary"] = args[3]
                    self.active_by_project.pop(row["project_id"], None)
                    recovered.append({"run_id": row["run_id"]})
            return recovered
        return []

    async def fetchrow(self, query, *args):
        normalized = " ".join(query.split()).upper()
        if normalized.startswith("INSERT INTO WORKFLOW_JOBS"):
            job_id, run_id, project_id, attempts = args
            if run_id in self.active_jobs_by_run:
                return None
            row = {
                "job_id": job_id,
                "run_id": run_id,
                "project_id": project_id,
                "status": "queued",
                "attempt_count": 0,
                "max_attempts": attempts,
                "created_at": "2026-05-22T00:00:00+00:00",
                "available_at": "2026-05-22T00:00:00+00:00",
                "started_at": None,
                "finished_at": None,
                "error_summary": "",
            }
            self.jobs[job_id] = row
            self.active_jobs_by_run[run_id] = job_id
            return row
        if normalized.startswith("WITH NEXT_JOB") and "UPDATE WORKFLOW_JOBS" in normalized:
            for row in sorted(self.jobs.values(), key=lambda item: (item["available_at"], item["created_at"])):
                run = self.rows.get(row["run_id"])
                if (
                    row["status"] == "queued"
                    and row["attempt_count"] < row["max_attempts"]
                    and run
                    and run["status"] in workflow_run_state.ACTIVE_RUN_STATUSES
                ):
                    row["status"] = "running"
                    row["started_at"] = "2026-05-22T00:01:00+00:00"
                    row["attempt_count"] += 1
                    row["error_summary"] = ""
                    return row
            return None
        if normalized.startswith("SELECT") and "FROM WORKFLOW_JOBS" in normalized and "WHERE JOB_ID" in normalized:
            return self.jobs.get(args[0])
        if normalized.startswith("SELECT") and "FROM WORKFLOW_JOBS" in normalized and "WHERE RUN_ID" in normalized:
            run_id = args[0]
            job_id = self.active_jobs_by_run.get(run_id)
            return self.jobs.get(job_id) if job_id else None
        if normalized.startswith("UPDATE WORKFLOW_JOBS"):
            job_id, status, set_finished, error_summary = args
            row = self.jobs.get(job_id)
            if not row:
                return None
            row["status"] = status
            if set_finished:
                row["finished_at"] = "2026-05-22T00:02:00+00:00"
            row["error_summary"] = error_summary
            if status in workflow_queue.TERMINAL_JOB_STATUSES:
                self.active_jobs_by_run.pop(row["run_id"], None)
            return row
        if normalized.startswith("INSERT INTO WORKFLOW_RUNS"):
            run_id, project_id, version = args
            if project_id in self.active_by_project:
                return None
            row = {
                "run_id": run_id,
                "project_id": project_id,
                "status": "queued",
                "current_phase": "",
                "created_at": "2026-05-22T00:00:00+00:00",
                "started_at": None,
                "finished_at": None,
                "heartbeat_at": self._next_heartbeat(),
                "error_summary": "",
                "code_version": version,
            }
            self.rows[run_id] = row
            self.active_by_project[project_id] = run_id
            return row
        if normalized.startswith("SELECT") and "WHERE PROJECT_ID" in normalized:
            project_id = args[0]
            run_id = self.active_by_project.get(project_id)
            return self.rows.get(run_id) if run_id else None
        if normalized.startswith("SELECT") and "WHERE RUN_ID" in normalized:
            return self.rows.get(args[0])
        if normalized.startswith("UPDATE WORKFLOW_RUNS") and "WHERE PROJECT_ID" in normalized:
            project_id, threshold, summary = args
            run_id = self.active_by_project.get(project_id)
            row = self.rows.get(run_id) if run_id else None
            if row and self._is_active_stale(row):
                row["status"] = "failed"
                row["finished_at"] = "2026-05-22T00:02:00+00:00"
                row["heartbeat_at"] = self._next_heartbeat()
                row["error_summary"] = summary
                self.active_by_project.pop(project_id, None)
                return {"run_id": row["run_id"]}
            return None
        if normalized.startswith("UPDATE WORKFLOW_RUNS"):
            run_id, status, current_phase, set_started, set_finished, error_summary, touch_heartbeat = args
            row = self.rows.get(run_id)
            if not row:
                return None
            if status is not None:
                row["status"] = status
            if current_phase is not None:
                row["current_phase"] = current_phase
            if set_started and row["started_at"] is None:
                row["started_at"] = "2026-05-22T00:01:00+00:00"
            if set_finished:
                row["finished_at"] = "2026-05-22T00:02:00+00:00"
            if error_summary is not None:
                row["error_summary"] = error_summary
            if touch_heartbeat:
                row["heartbeat_at"] = self._next_heartbeat()
            if row["status"] in workflow_run_state.TERMINAL_RUN_STATUSES:
                self.active_by_project.pop(row["project_id"], None)
            return row
        return None

    def mark_stale(self, run_id):
        self.rows[run_id]["heartbeat_at"] = "stale"

    def _is_active_stale(self, row):
        return row["status"] in workflow_run_state.ACTIVE_RUN_STATUSES and row.get("heartbeat_at") == "stale"

    def _next_heartbeat(self):
        self.heartbeat_counter += 1
        return f"2026-05-22T00:{self.heartbeat_counter:02d}:00+00:00"

    def _excluded_projects(self, args):
        for arg in args:
            if isinstance(arg, (list, tuple, set)):
                return {str(item) for item in arg}
        return set()


class TestWorkflowRunStateDurability(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        api.running.clear()
        workflow_run_state._schema_ready_for_pool.clear()
        workflow_queue.clear_schema_cache()
        workflow_run_state.clear_memory_run_state()

    async def asyncTearDown(self):
        api.running.clear()
        workflow_run_state._schema_ready_for_pool.clear()
        workflow_queue.clear_schema_cache()
        workflow_run_state.clear_memory_run_state()

    async def test_postgres_run_state_creation_and_active_guard(self):
        pool = _RunStatePool()
        with patch("runtime.run_state.store._get_pool", new=AsyncMock(return_value=pool)):
            first = await workflow_run_state.create_workflow_run("durable-project", code_version="4.4.0")
            duplicate = await workflow_run_state.create_workflow_run("durable-project", code_version="4.4.0")
            await workflow_run_state.mark_run_running(first.run.run_id, current_phase="classify")
            running_record = await workflow_run_state.get_workflow_run(first.run.run_id)

        self.assertTrue(first.created)
        self.assertTrue(first.durable)
        self.assertEqual(first.run.status, "queued")
        self.assertFalse(duplicate.created)
        self.assertTrue(duplicate.durable)
        self.assertEqual(duplicate.run.run_id, first.run.run_id)
        self.assertEqual(running_record.status, "running")
        self.assertEqual(running_record.current_phase, "classify")

    async def test_stale_queued_run_is_detected_and_marked_failed(self):
        pool = _RunStatePool()
        with patch("runtime.run_state.store._get_pool", new=AsyncMock(return_value=pool)):
            acquisition = await workflow_run_state.create_workflow_run("stale-queued", code_version="4.4.0")
            pool.conn.mark_stale(acquisition.run.run_id)

            stale_count = await workflow_run_state.count_stale_active_runs()
            recovery = await workflow_run_state.recover_stale_active_runs()
            record = await workflow_run_state.get_workflow_run(acquisition.run.run_id)

        self.assertEqual(stale_count, 1)
        self.assertEqual(recovery.status, "ok")
        self.assertEqual(recovery.recovered_count, 1)
        self.assertEqual(record.status, "failed")
        self.assertEqual(record.error_summary, workflow_run_state.ABANDONED_RUN_ERROR_SUMMARY)
        self.assertNotIn("Traceback", record.error_summary)
        self.assertNotIn("C:\\", record.error_summary)

    async def test_stale_running_run_is_detected_and_marked_failed(self):
        pool = _RunStatePool()
        with patch("runtime.run_state.store._get_pool", new=AsyncMock(return_value=pool)):
            acquisition = await workflow_run_state.create_workflow_run("stale-running", code_version="4.4.0")
            await workflow_run_state.mark_run_running(acquisition.run.run_id, current_phase="audit")
            pool.conn.mark_stale(acquisition.run.run_id)

            recovery = await workflow_run_state.recover_stale_active_runs()
            record = await workflow_run_state.get_workflow_run(acquisition.run.run_id)

        self.assertEqual(recovery.recovered_count, 1)
        self.assertEqual(record.status, "failed")
        self.assertEqual(record.current_phase, "audit")
        self.assertEqual(record.error_summary, workflow_run_state.ABANDONED_RUN_ERROR_SUMMARY)

    async def test_fresh_active_run_is_not_marked_stale(self):
        pool = _RunStatePool()
        with patch("runtime.run_state.store._get_pool", new=AsyncMock(return_value=pool)):
            acquisition = await workflow_run_state.create_workflow_run("fresh-active", code_version="4.4.0")

            stale_count = await workflow_run_state.count_stale_active_runs()
            recovery = await workflow_run_state.recover_stale_active_runs()
            record = await workflow_run_state.get_workflow_run(acquisition.run.run_id)

        self.assertEqual(stale_count, 0)
        self.assertEqual(recovery.recovered_count, 0)
        self.assertEqual(record.status, "queued")

    async def test_locally_running_project_is_excluded_from_stale_recovery(self):
        pool = _RunStatePool()
        with patch("runtime.run_state.store._get_pool", new=AsyncMock(return_value=pool)):
            acquisition = await workflow_run_state.create_workflow_run("locally-running", code_version="4.4.0")
            pool.conn.mark_stale(acquisition.run.run_id)

            stale_count = await workflow_run_state.count_stale_active_runs(
                exclude_project_ids=["locally-running"]
            )
            recovery = await workflow_run_state.recover_stale_active_runs(
                exclude_project_ids=["locally-running"]
            )
            record = await workflow_run_state.get_workflow_run(acquisition.run.run_id)

        self.assertEqual(stale_count, 0)
        self.assertEqual(recovery.recovered_count, 0)
        self.assertEqual(record.status, "queued")

    async def test_phase_progress_updates_run_heartbeat(self):
        pool = _RunStatePool()
        with patch("runtime.run_state.store._get_pool", new=AsyncMock(return_value=pool)):
            acquisition = await workflow_run_state.create_workflow_run("heartbeat-project", code_version="4.4.0")
            initial = await workflow_run_state.get_workflow_run(acquisition.run.run_id)
            updated = await workflow_run_state.mark_run_phase(acquisition.run.run_id, "strategy")

        self.assertNotEqual(updated.heartbeat_at, initial.heartbeat_at)
        self.assertEqual(updated.current_phase, "strategy")

    async def test_workflow_queue_enqueue_and_claim_is_durable(self):
        pool = _RunStatePool()
        with patch("runtime.run_state.store._get_pool", new=AsyncMock(return_value=pool)):
            acquisition = await workflow_run_state.create_workflow_run("queue-project", code_version="4.4.0")
            enqueue = await workflow_queue.enqueue_workflow_job(acquisition.run.run_id, acquisition.run.project_id)
            duplicate = await workflow_queue.enqueue_workflow_job(acquisition.run.run_id, acquisition.run.project_id)
            claimed = await workflow_queue.claim_next_workflow_job()
            second_claim = await workflow_queue.claim_next_workflow_job()

        self.assertTrue(enqueue.created)
        self.assertTrue(enqueue.durable)
        self.assertFalse(duplicate.created)
        self.assertEqual(duplicate.job.job_id, enqueue.job.job_id)
        self.assertEqual(claimed.job_id, enqueue.job.job_id)
        self.assertEqual(claimed.status, "running")
        self.assertEqual(claimed.attempt_count, 1)
        self.assertEqual(claimed.max_attempts, workflow_queue.DEFAULT_MAX_WORKFLOW_JOB_ATTEMPTS)
        self.assertIsNone(second_claim)

    async def test_workflow_queue_terminal_transitions_are_sanitized(self):
        pool = _RunStatePool()
        with patch("runtime.run_state.store._get_pool", new=AsyncMock(return_value=pool)):
            acquisition = await workflow_run_state.create_workflow_run("queue-failure", code_version="4.4.0")
            enqueue = await workflow_queue.enqueue_workflow_job(acquisition.run.run_id, acquisition.run.project_id)
            failed = await workflow_queue.mark_job_failed(
                enqueue.job.job_id,
                error=r"Traceback (most recent call last): C:\private\secret.py password=abc123",
            )

            second_run = await workflow_run_state.create_workflow_run("queue-success", code_version="4.4.0")
            second_job = await workflow_queue.enqueue_workflow_job(second_run.run.run_id, second_run.run.project_id)
            succeeded = await workflow_queue.mark_job_succeeded(second_job.job.job_id)

        self.assertEqual(failed.status, "failed")
        self.assertNotIn("Traceback", failed.error_summary)
        self.assertNotIn("C:\\", failed.error_summary)
        self.assertNotIn("abc123", failed.error_summary)
        self.assertEqual(succeeded.status, "succeeded")
        self.assertEqual(succeeded.error_summary, "")

    async def test_queue_drain_marks_job_and_run_succeeded(self):
        pool = _RunStatePool()
        state = make_completed_state("queue-drain-success")
        with patch("runtime.run_state.store._get_pool", new=AsyncMock(return_value=pool)):
            acquisition = await workflow_run_state.create_workflow_run(state.project_id, code_version="4.4.0")
            enqueue = await workflow_queue.enqueue_workflow_job(acquisition.run.run_id, state.project_id)
            with patch("api.store.load", new=AsyncMock(return_value=state)):
                with patch("api.store.save", new=AsyncMock()):
                    await api._drain_workflow_queue()
            job = await workflow_queue.get_workflow_job(enqueue.job.job_id)
            run = await workflow_run_state.get_workflow_run(acquisition.run.run_id)
            active = await workflow_run_state.has_active_project_run(state.project_id)

        self.assertEqual(job.status, "succeeded")
        self.assertEqual(run.status, "succeeded")
        self.assertFalse(active)

    async def test_queue_drain_marks_job_and_run_failed_with_sanitized_summary(self):
        pool = _RunStatePool()
        state = ProjectState(project_id="queue-drain-failure", project_name="Failure", brief="Run fails")
        error = RuntimeError(r"Traceback (most recent call last): C:\private\workflow.py token=abc123")
        with patch("runtime.run_state.store._get_pool", new=AsyncMock(return_value=pool)):
            acquisition = await workflow_run_state.create_workflow_run(state.project_id, code_version="4.4.0")
            enqueue = await workflow_queue.enqueue_workflow_job(acquisition.run.run_id, state.project_id)
            with patch("api.store.load", new=AsyncMock(return_value=state)):
                with patch("api.run_workflow_sequence", new=AsyncMock(side_effect=error)):
                    await api._drain_workflow_queue()
            job = await workflow_queue.get_workflow_job(enqueue.job.job_id)
            run = await workflow_run_state.get_workflow_run(acquisition.run.run_id)

        self.assertEqual(job.status, "failed")
        self.assertEqual(run.status, "failed")
        self.assertNotIn("Traceback", job.error_summary)
        self.assertNotIn("C:\\", job.error_summary)
        self.assertNotIn("abc123", job.error_summary)

    async def test_stale_project_run_is_recovered_before_new_workflow_start(self):
        pool = _RunStatePool()
        state = ProjectState(project_id="stale-before-start", project_name="Recovery", brief="Run after stale")
        with patch("runtime.run_state.store._get_pool", new=AsyncMock(return_value=pool)):
            stale = await workflow_run_state.create_workflow_run(state.project_id, code_version="4.4.0")
            pool.conn.mark_stale(stale.run.run_id)
            with patch("api.store.load", new=AsyncMock(return_value=state)):
                response = await api.run_full_workflow(state.project_id, BackgroundTasks())
            stale_record = await workflow_run_state.get_workflow_run(stale.run.run_id)
            active = await workflow_run_state.get_active_project_run(state.project_id)

        self.assertEqual(response["status"], "started")
        self.assertIn("run_id", response)
        self.assertNotEqual(response["run_id"], stale.run.run_id)
        self.assertEqual(stale_record.status, "failed")
        self.assertEqual(stale_record.error_summary, workflow_run_state.ABANDONED_RUN_ERROR_SUMMARY)
        self.assertEqual(active.run_id, response["run_id"])

    async def test_run_endpoint_enqueues_durable_job(self):
        pool = _RunStatePool()
        state = ProjectState(project_id="endpoint-enqueue", project_name="Queue", brief="Run once")
        with patch("runtime.run_state.store._get_pool", new=AsyncMock(return_value=pool)):
            with patch("api.store.load", new=AsyncMock(return_value=state)):
                response = await api.run_full_workflow(state.project_id, BackgroundTasks())
            counts = await workflow_queue.count_workflow_jobs()

        self.assertEqual(response["status"], "started")
        self.assertIn("run_id", response)
        self.assertEqual(counts["queued"], 1)
        self.assertEqual(counts["running"], 0)

    async def test_run_endpoint_leaves_job_queued_when_local_drain_schedule_fails(self):
        class FailingBackgroundTasks:
            def add_task(self, *args, **kwargs):
                raise RuntimeError(r"scheduler failed at C:\private\worker.py password=abc123")

        pool = _RunStatePool()
        state = ProjectState(project_id="schedule-failure", project_name="Queue", brief="Run once")
        with patch("runtime.run_state.store._get_pool", new=AsyncMock(return_value=pool)):
            with patch("api.store.load", new=AsyncMock(return_value=state)):
                response = await api.run_full_workflow(state.project_id, FailingBackgroundTasks())
            counts = await workflow_queue.count_workflow_jobs()
            active = await workflow_run_state.get_active_project_run(state.project_id)

        self.assertEqual(response["status"], "started")
        self.assertIn("run_id", response)
        self.assertEqual(counts["queued"], 1)
        self.assertIsNotNone(active)
        self.assertEqual(active.status, "queued")

    async def test_fresh_durable_duplicate_run_still_conflicts(self):
        pool = _RunStatePool()
        state = ProjectState(project_id="fresh-duplicate", project_name="Duplicate", brief="Run once")
        with patch("runtime.run_state.store._get_pool", new=AsyncMock(return_value=pool)):
            with patch("api.store.load", new=AsyncMock(return_value=state)):
                first = await api.run_full_workflow(state.project_id, BackgroundTasks())
            api.running.clear()
            with patch("api.store.load", new=AsyncMock(return_value=state)):
                with self.assertRaises(HTTPException) as ctx:
                    await api.run_full_workflow(state.project_id, BackgroundTasks())

        self.assertEqual(first["status"], "started")
        self.assertEqual(ctx.exception.status_code, 409)


class TestApiRunWorkflow(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        api.running.clear()
        workflow_queue.clear_schema_cache()
        workflow_run_state.clear_memory_run_state()
        self.run_state_pool_patch = patch(
            "runtime.run_state.store._get_pool",
            new=AsyncMock(return_value=None),
        )
        self.run_state_pool_patch.start()

    async def asyncTearDown(self):
        self.run_state_pool_patch.stop()
        api.running.clear()
        workflow_queue.clear_schema_cache()
        workflow_run_state.clear_memory_run_state()

    async def test_run_returns_already_complete_for_finished_project(self):
        state = make_completed_state("already-complete")
        with patch("api.store.load", new=AsyncMock(return_value=state)):
            response = await api.run_full_workflow(state.project_id, BackgroundTasks())
        self.assertEqual(response["status"], "already_complete")
        self.assertFalse(await workflow_run_state.has_active_project_run(state.project_id))

    def test_workflow_phase_sequence_preserves_v44_order(self):
        self.assertEqual(
            WORKFLOW_PHASE_SEQUENCE,
            ("classify", "hypotheses", "gauntlet", "audit", "strategy", "sqi", "monitor", "report"),
        )

    async def test_ingestion_metadata_does_not_change_run_start_or_phase_sequence(self):
        state = ProjectState(
            project_id="ingestion-run-start",
            project_name="Ingestion run start",
            brief="Run with versioned ingestion metadata.",
            data="Supporting signal.",
            ingestion_contract_version="case.v1",
            ingestion_source="crm",
            ingestion_external_case_id="case-456",
            ingestion_metadata={"segment": "enterprise"},
        )

        with patch("api.store.load", new=AsyncMock(return_value=state)):
            response = await api.run_full_workflow(state.project_id, BackgroundTasks())

        active = await workflow_run_state.get_active_project_run(state.project_id)
        record = await workflow_run_state.get_workflow_run(response["run_id"])
        self.assertEqual(
            WORKFLOW_PHASE_SEQUENCE,
            ("classify", "hypotheses", "gauntlet", "audit", "strategy", "sqi", "monitor", "report"),
        )
        self.assertEqual(response["status"], "started")
        self.assertEqual(response["project_id"], state.project_id)
        self.assertIn("run_id", response)
        self.assertIsNotNone(active)
        self.assertEqual(active.run_id, response["run_id"])
        self.assertEqual(record.status, "queued")
        self.assertEqual(record.current_phase, "")
        self.assertIn(state.project_id, api.running)

    async def test_run_rejects_when_workflow_is_process_local_running(self):
        state = make_completed_state("running-project-run")
        api.running.add(state.project_id)
        with patch("api.store.load", new=AsyncMock(return_value=state)):
            with self.assertRaises(HTTPException) as ctx:
                await api.run_full_workflow(state.project_id, BackgroundTasks())
        self.assertEqual(ctx.exception.status_code, 409)

    async def test_workflow_run_state_transitions_queued_running_succeeded(self):
        acquisition = await workflow_run_state.create_workflow_run(
            "run-state-project",
            code_version="4.4.0",
        )

        self.assertTrue(acquisition.created)
        self.assertEqual(acquisition.run.status, "queued")
        self.assertTrue(await workflow_run_state.has_active_project_run("run-state-project"))

        running_record = await workflow_run_state.mark_run_running(
            acquisition.run.run_id,
            current_phase="classify",
        )
        self.assertEqual(running_record.status, "running")
        self.assertEqual(running_record.current_phase, "classify")

        succeeded = await workflow_run_state.mark_run_succeeded(
            acquisition.run.run_id,
            current_phase="report",
        )
        self.assertEqual(succeeded.status, "succeeded")
        self.assertEqual(succeeded.current_phase, "report")
        self.assertIsNotNone(succeeded.finished_at)
        self.assertFalse(await workflow_run_state.has_active_project_run("run-state-project"))

    async def test_duplicate_active_run_is_blocked_after_local_running_set_is_lost(self):
        state = ProjectState(project_id="durable-duplicate", project_name="Duplicate", brief="Run once")

        with patch("api.store.load", new=AsyncMock(return_value=state)):
            first = await api.run_full_workflow(state.project_id, BackgroundTasks())

        self.assertEqual(first["status"], "started")
        self.assertIn("run_id", first)

        api.running.clear()
        with patch("api.store.load", new=AsyncMock(return_value=state)):
            with self.assertRaises(HTTPException) as ctx:
                await api.run_full_workflow(state.project_id, BackgroundTasks())

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail, "Workflow already running")

    async def test_failed_background_run_persists_sanitized_error_summary(self):
        state = ProjectState(project_id="failed-run-project", project_name="Failure", brief="Run fails")
        acquisition = await workflow_run_state.create_workflow_run(state.project_id, code_version="4.4.0")
        error = RuntimeError(
            r"Traceback (most recent call last): C:\private\workspace\secret.py token=abc123"
        )

        with patch("api.store.load", new=AsyncMock(return_value=state)):
            with patch("api.run_workflow_sequence", new=AsyncMock(side_effect=error)):
                await api._run_workflow(state.project_id, acquisition.run.run_id)

        record = await workflow_run_state.get_workflow_run(acquisition.run.run_id)
        self.assertEqual(record.status, "failed")
        self.assertIn("Workflow failed", record.error_summary)
        self.assertNotIn("Traceback", record.error_summary)
        self.assertNotIn("C:\\", record.error_summary)
        self.assertNotIn("abc123", record.error_summary)
        self.assertFalse(await workflow_run_state.has_active_project_run(state.project_id))

    async def test_manual_phase_rejects_when_workflow_is_running(self):
        state = make_completed_state("running-project")
        api.running.add(state.project_id)
        with patch("api.store.load", new=AsyncMock(return_value=state)):
            with self.assertRaises(HTTPException) as ctx:
                await api.run_single_phase_endpoint(state.project_id, api.RunPhaseRequest(phase="report"))
        self.assertEqual(ctx.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main(verbosity=2)
