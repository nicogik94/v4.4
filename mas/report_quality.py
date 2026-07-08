"""Deterministic report/export quality helpers.

These helpers are read-only projections over ProjectState. They do not mutate
state, route workflow, call providers, or change export payload schemas.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import unicodedata
from typing import Any


SPARSE_EVIDENCE_CAVEAT = (
    "This is a structured hypothesis map, not a measured audit. Direct evidence "
    "is limited or absent. Treat probabilities, scores, thresholds, and risk "
    "rankings as provisional priors until Sprint 0 validates them."
)

PARTIAL_EVIDENCE_CAVEAT = (
    "Some project evidence was supplied, but validation is incomplete and some "
    "decision-critical evidence is missing or degraded."
)

UPLOADED_KNOWLEDGE_NO_IMPORTED_EVIDENCE_NOTE = (
    "Uploaded knowledge chunks available; imported evidence records unavailable."
)

UNSUPPORTED_EVIDENCE_FILES_WARNING = (
    "Some uploaded evidence files were not ingested. Convert unsupported files "
    "to .md/.txt or enable JSON ingestion."
)

EVIDENCE_CATEGORY_COVERAGE_WARNING = (
    "Some expected evidence categories may be incomplete; verify uploaded source coverage."
)

PROVISIONAL_CLARIFICATION_CAVEAT = (
    "Provisional report: decision-critical clarification questions have not "
    "been answered. This is suitable for internal review only. Answer "
    "clarifications and regenerate before client delivery."
)

PROVISIONAL_CLARIFICATION_NEXT_ACTION = (
    "Next required operator action: answer clarification questions and regenerate the report."
)

TELEMETRY_PRIVACY_CAVEAT = (
    "Log event metadata by default. Do not log raw briefs, uploaded content, "
    "report text, provider payloads, secrets, local paths, API keys, or "
    "sensitive user text unless the operator explicitly marks a session for "
    "qualitative review."
)

SPARSE_PRECISION_RULE = (
    "When evidence is sparse, BF, DQ, RPN, H_norm, correlation/rho, priors, "
    "probabilities, dollars, and percentages must be labeled as priors, "
    "planning gates, or placeholders unless backed by concrete project evidence."
)

SPARSE_CONFIDENCE_RULE = (
    "Moderate confidence in the need for Sprint 0 evidence collection; low "
    "confidence in any specific root cause, impact size, or intervention until "
    "Sprint 0 data is collected."
)

THRESHOLD_WARNING = (
    "Threshold consistency warning: multiple thresholds appear to govern the "
    "same decision. Confirm one decision matrix before acting."
)
THRESHOLD_CONFLICT_UNKNOWN_WARNING = "Possible threshold conflict detected, source unknown."
THRESHOLD_CONFLICT_BETWEEN_TEMPLATE = "Threshold conflict detected between: {section_a} and {section_b}."
CONSTRAINT_ADHERENCE_WARNING = (
    "Constraint adherence warning: generated recommendation may contradict "
    "explicit operator constraints. Review before client delivery."
)

CONSTRAINT_SAFE_PREFIX_PATTERN = re.compile(
    r"(?:^|[\s([{'\";:.,|\-–—])"
    r"(?:do\s+not(?:\s+(?:do|start|launch|recommend|increase|run|execute|pursue))?|"
    r"don't|dont|should\s+not|must\s+not|cannot|can't|not|no|never|without|"
    r"avoid(?:s|ed|ing)?|defer(?:s|red|ring)?|block(?:s|ed|ing)?|"
    r"pause(?:s|d|ing)?|freeze(?:s|d|ing)?|frozen|park(?:s|ed|ing)?|prohibit(?:s|ed|ing)?|"
    r"forbid(?:s|den|ding)?|forbidden|out\s+of\s+scope|off[- ]limits)"
    r"\s*(?:[:\-–—]\s*)?"
    r"(?:(?:the|a|an|any|all|current|new|full|broad|major|large|paid|"
    r"acquisition|growth|campaigns?|budgets?|spend|engineering|critical|"
    r"parallel|three|3)\s+){0,10}$",
    re.I,
)

CONSTRAINT_SAFE_SUFFIX_PATTERN = re.compile(
    r"^\s*(?:[\])}.,:;()|\-–—]\s*)?"
    r"(?:(?:and|or)\s+"
    r"(?:(?:the|a|an|any|all|new|full|broad|major|large|paid|growth|"
    r"engineering|critical|parallel|three|3)\s+){0,8}[\w-]+\s+){0,3}"
    r"(?:(?:is|are|be|being|remain|remains|stay|stays|should\s+remain|"
    r"must\s+remain)\s+)?"
    r"(?:not\s+(?:recommended|allowed|in\s+scope|this\s+month|now)|"
    r"paused|pausing|deferred|blocked|frozen|parked|out\s+of\s+scope|off[- ]limits|"
    r"prohibited|forbidden|do\s+not\s+(?:do|start|launch|recommend|increase)|"
    r"continues?\s+shifting|worsening\s+the\s+cohort)\b",
    re.I,
)

CONSTRAINT_SAFE_HEADING_PATTERN = re.compile(
    r"\b(?:what\s+not\s+to\s+do|do\s+not\s+do|do\s+not\s+start|"
    r"do\s+not\s+launch|not\s+this\s+month|out\s+of\s+scope|paused|"
    r"deferred|blocked|risks?|warning|stop[- ]condition|circuit\s+breaker)\b",
    re.I,
)

CONSTRAINT_SAFE_CONDITIONAL_PREFIX_PATTERN = re.compile(
    r"(?:^|[\s([{'\";:.,|\-–—])"
    r"(?:if|when|unless|whether|risk\s+if|risk\s+of|risks?\s+if|"
    r"warning\s+if|watch\s+if|monitor\s+if|stop\s+if|trigger\s+if|"
    r"consider\s+pausing|consider\s+pause|consider\s+freezing)"
    r"(?:(?:\s+|,)[\w%.-]+){0,10}\s*$",
    re.I,
)

PRIMARY_THRESHOLD_SECTION_NAMES = {
    "decision gates",
    "decision matrix",
    "decision thresholds",
    "decision criteria",
    "thresholds",
    "convergence gates",
    "gate policy",
    "spend authorization gate",
}

SUBORDINATE_THRESHOLD_SECTION_NAMES = {
    "monitoring",
    "monitoring details",
    "monitoring and kill criteria",
    "governance fallback",
    "governance fallback if leadership overrides the diagnostic hold",
    "spend gate",
    "spend gate owner and enforcement",
    "what the team may do during sprint 0",
    "what the team may not do during sprint 0",
    "sprint 0 controls",
    "sprint 0 allowed controls",
    "sprint 0 not allowed controls",
    "sprint 0 evidence pack required",
    "minimum staffing assumption",
    "main limitation of this recommendation",
    "operator controls",
    "roadmap",
    "key risks",
    "early warning signal",
    "early warning signals",
    "mitigation",
    "stop change course threshold",
    "stop change course thresholds",
    "stop change course",
    "stop change course section",
    "stop and change course",
    "stop and change course threshold",
    "stop and change course thresholds",
    "stop change course sections",
    "canaries",
    "circuit breakers",
    "technical appendix",
    "appendix technical analysis",
    "framework references",
    "framework reference",
    "convergence gate status",
    "report appendix",
}

RISK_CLASSIFICATION_WARNING = (
    "Risk classification may understate generated risk content. Review before client delivery."
)
CLIENT_BF_CONFIDENCE_CAVEAT = (
    "Current evidence does not meet the confidence threshold for selecting a specific growth lever."
)

NO_CONCRETE_LOCATORS_CLIENT_NOTE = (
    "No concrete source locators were available for this project; evidence "
    "should be validated in Sprint 0."
)

WAVE2_GRADUATION_MATRIX = """## Wave 2 Graduation Matrix

Proceed to Wave 2 if:
- feature exclusion list remains intact
- telemetry is live and reliable
- clarification workflow reaches the agreed quality/adoption threshold
- template field completion clears the operator-set threshold
- ROI engine canary passes
- support/confusion signals stay below the operator-set threshold
- no major compliance/privacy issue appears

Extend Wave 1 if:
- metrics are directionally positive but underpowered
- sample size is insufficient
- schema overlap is ambiguous
- user feedback is mixed but fixable

Split the workstream if:
- schema overlap is too low
- ROI template does not generalize
- clarification workflow and template abstraction do not share enough reusable scaffolding

Stop or defer Wave 2 if:
- telemetry is unavailable
- canary fails
- users do not adopt clarification workflow
- support/confusion signals exceed the operator-set threshold
- scope boundary violations recur"""

OWNER_ROLE_MAPS: dict[str, list[str]] = {
    "productization": [
        "Executive Sponsor",
        "Product Owner",
        "Engineering Lead",
        "UX Research Lead",
        "Data/Analytics Owner",
        "Pilot User Recruiter",
        "Operator / QA Reviewer",
        "Privacy or Data Governance Reviewer",
    ],
    "growth": [
        "Executive Sponsor",
        "Growth Lead",
        "Revenue Operations Lead",
        "Sales Lead",
        "Customer Success Lead",
        "Product Analytics Lead",
        "Finance Lead",
    ],
    "ai_readiness": [
        "Executive Sponsor",
        "AI Program Lead",
        "Data Owner",
        "IT/Security Lead",
        "Legal/Compliance Lead",
        "People/Training Lead",
        "Process Owner",
        "Finance Lead",
    ],
    "automation_roi": [
        "Executive Sponsor",
        "Process Owner",
        "Operations Lead",
        "Finance Lead",
        "IT/Automation Lead",
        "Data Owner",
        "Compliance Reviewer",
        "Change/Training Lead",
    ],
    "seo_content_editorial": [
        "Executive Sponsor",
        "SEO Lead",
        "Editorial Lead",
        "Writer/Content Owner",
        "Legal/Compliance Reviewer",
        "Brand/Product Owner",
        "Analytics Owner",
        "Web/CMS Owner",
    ],
    "general_business": [
        "Executive Sponsor",
        "Decision Owner",
        "Operations Lead",
        "Finance Lead",
        "Data/Analytics Owner",
        "Implementation Owner",
        "Risk/Compliance Reviewer",
    ],
}

EVIDENCE_CATEGORY_MAPS: dict[str, list[str]] = {
    "productization": [
        "product telemetry",
        "session/rework logs",
        "report validation batch",
        "user interviews",
        "pilot sessions",
        "export usage/share data",
        "competitor/product gap scan",
        "implementation complexity estimate",
        "privacy/data governance review",
        "template schema / field registry validation",
    ],
    "growth": [
        "cohort retention",
        "CAC / LTV",
        "pipeline conversion",
        "win/loss analysis",
        "product usage / activation",
        "churn interviews",
        "expansion / NRR",
        "pricing and packaging evidence",
        "sales velocity",
        "marketing channel efficiency",
        "customer success signals",
    ],
    "seo_content_editorial": [
        "Search Console",
        "GA4",
        "crawl/technical evidence",
        "keyword research",
        "editorial workflow evidence",
        "CMS/schema capability",
        "legal/brand review artifacts",
        "content performance data",
    ],
    "general_business": [
        "stakeholder interviews",
        "operating metrics",
        "financial/budget evidence",
        "process documentation",
        "risk/compliance review",
        "implementation capacity",
        "pilot results",
    ],
}

TELEMETRY_PATTERN = re.compile(
    r"\b(logs?|logging|event tracking|dashboard telemetry|product analytics|"
    r"session replay|recordings?|transcripts?|user behavior instrumentation|"
    r"regeneration-event logging|rework flags?|usage instrumentation|telemetry)\b",
    re.I,
)

SEO_PATTERN = re.compile(
    r"\b(seo|search console|gsc|ga4|google analytics|editorial|content|cms|"
    r"schema|crawl|crawler|keyword|article workflow|website traffic|web analytics|organic "
    r"traffic|core web vitals|crux|pagespeed|canonical|serp)\b",
    re.I,
)

PRODUCTIZATION_PATTERN = re.compile(
    r"\b(productization|product strategy|product telemetry|pilot users?|dashboard|exports?|"
    r"dossier|regeneration|feature roadmap|feature prioritization|productization direction|"
    r"ux research|template abstraction|roi engine)\b",
    re.I,
)
AI_READINESS_PATTERN = re.compile(
    r"\b(ai readiness|ai program|model governance|llm|machine learning|ml readiness|data readiness)\b",
    re.I,
)
AUTOMATION_ROI_PATTERN = re.compile(
    r"\b(automation roi|automation|rpa|workflow automation|process workflow|manual process|process owner)\b",
    re.I,
)
GROWTH_PATTERN = re.compile(
    r"\b(growth|revenue|sales|pipeline|customer success|conversion|retention|churn|acquisition|cac|ltv|nrr|win/loss)\b",
    re.I,
)

WAVE2_PATTERN = re.compile(
    r"\b(wave 2|wave two|wave 1|wave one|feature roadmap|feature prioritization|"
    r"productization direction|template abstraction|roi engine|graduation gate)\b",
    re.I,
)

UNKNOWN_EVIDENCE_PATTERN = re.compile(
    r"\b(evidence (is )?(unknown|unavailable|absent|missing)|no imported evidence|"
    r"no uploaded files|no concrete locators|citation unavailable|no source material)\b",
    re.I,
)

PLACEHOLDER_RATIONALE_PATTERN = re.compile(
    r"^\s*(|tbd|tbd\s*(?:[—/\-]\s*)?(?:requires\s*)?operator confirmation|"
    r"requires operator confirmation|operator confirmation required|requires confirmation|"
    r"unconfirmed|unavailable|placeholder|none|n/a)\s*$",
    re.I,
)


@dataclass(frozen=True)
class ReportQualityContext:
    sparse_evidence: bool
    evidence_warning: bool
    sparse_reasons: list[str] = field(default_factory=list)
    zero_clarifications: bool = False
    has_concrete_locators: bool = False
    has_budget_or_spend_evidence: bool = False
    decision_domain: str = "general_business"
    owner_roles: list[str] = field(default_factory=list)
    evidence_categories: list[str] = field(default_factory=list)
    provisional_report: bool = False
    required_clarifications_open: bool = False
    telemetry_privacy_required: bool = False
    sparse_evidence_caveat: str = SPARSE_EVIDENCE_CAVEAT
    provisional_clarification_caveat: str = PROVISIONAL_CLARIFICATION_CAVEAT
    provisional_clarification_next_action: str = PROVISIONAL_CLARIFICATION_NEXT_ACTION
    telemetry_privacy_caveat: str = TELEMETRY_PRIVACY_CAVEAT


@dataclass(frozen=True)
class EvidenceMaturityProjection:
    maturity: str
    client_use_status: str
    validation_required: str
    uploaded_files: int = 0
    imported_evidence: int = 0
    imported_signals: int = 0
    has_concrete_locators: bool = False
    citation_marker_count: int = 0
    citation_markers_resolved_count: int = 0
    citation_markers_resolved: bool = False
    concrete_source_locator_count: int = 0
    concrete_source_locators_available: bool = False
    uploaded_file_count: int = 0
    parsed_file_count: int = 0
    rejected_or_unsupported_file_count: int = 0
    imported_evidence_count: int = 0
    imported_evidence_available: bool = False
    imported_signal_count: int = 0
    imported_signals_available: bool = False


@dataclass(frozen=True)
class EvidenceAccountingProjection:
    citation_marker_count: int = 0
    citation_markers_resolved_count: int = 0
    citation_markers_resolved: bool = False
    concrete_source_locator_count: int = 0
    concrete_source_locators_available: bool = False
    uploaded_file_count: int = 0
    parsed_file_count: int = 0
    rejected_or_unsupported_file_count: int = 0
    imported_evidence_count: int = 0
    imported_evidence_available: bool = False
    imported_signal_count: int = 0
    imported_signals_available: bool = False
    parsed_file_display_names: tuple[str, ...] = ()
    rejected_file_display_names: tuple[str, ...] = ()
    explicit_missing_file_display_names: tuple[str, ...] = ()
    unsupported_or_missing_warning: str = ""
    category_coverage_warning: str = ""


@dataclass(frozen=True)
class RiskClassificationGateAssessment:
    warning_applies: bool
    warning_text: str = ""
    selected_classification: str = ""
    normalized_classification: str = ""
    highest_generated_risk_severity: str = ""
    high_or_critical_risk_count: int = 0
    source_counts: dict[str, int] = field(default_factory=dict)
    high_or_critical_risks: tuple[dict[str, str], ...] = field(default_factory=tuple)

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "selected_classification": self.selected_classification,
            "normalized_classification": self.normalized_classification,
            "highest_generated_risk_severity": self.highest_generated_risk_severity,
            "high_or_critical_risk_count": self.high_or_critical_risk_count,
            "source_counts": dict(self.source_counts),
            "high_or_critical_risks": [dict(row) for row in self.high_or_critical_risks],
        }


@dataclass(frozen=True)
class ThresholdSectionClassification:
    section_name: str
    classification: str
    reason: str
    threshold_like: bool = False


@dataclass(frozen=True)
class ConstraintAdherenceProjection:
    warning_applies: bool
    warning_text: str = ""
    detected_constraints: tuple[str, ...] = field(default_factory=tuple)
    contradiction_signals: tuple[str, ...] = field(default_factory=tuple)
    operator_context_preview: str = ""

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "detected_constraints": list(self.detected_constraints),
            "contradiction_signals": list(self.contradiction_signals),
            "operator_context_preview": self.operator_context_preview,
        }


@dataclass(frozen=True)
class DecisionMemoQualityFinding:
    rule_name: str
    message: str
    location: str = ""
    excerpt: str = ""
    severity: str = "advisory"


@dataclass(frozen=True)
class DecisionMemoQualityResult:
    checked: bool
    report_mode: str = "standard"
    output_language: str = "en"
    findings: tuple[DecisionMemoQualityFinding, ...] = ()

    @property
    def status(self) -> str:
        if not self.checked:
            return "not_applicable"
        return "advisory_findings" if self.findings else "ok"


def assess_report_quality_context(state: Any) -> ReportQualityContext:
    text = _combined_state_text(state)
    domain_text = _domain_source_text(state)
    accounting = evidence_accounting_projection(state)
    uploaded_count = accounting.uploaded_file_count
    parsed_file_count = accounting.parsed_file_count
    imported_evidence_count = accounting.imported_evidence_count
    imported_signal_count = accounting.imported_signal_count
    clarification_count = len(getattr(state, "clarification_answers", []) or [])
    has_locators = accounting.concrete_source_locators_available
    zero_clarifications = clarification_count == 0
    required_clarifications_open = has_required_clarifications_open(state)

    sparse_reasons = []
    if uploaded_count == 0:
        sparse_reasons.append("No uploaded files are attached.")
    if imported_evidence_count == 0:
        sparse_reasons.append("No imported evidence records are available.")
    if imported_signal_count == 0:
        sparse_reasons.append("No imported signals are available.")
    if zero_clarifications:
        sparse_reasons.append("No clarification answers have been submitted.")
    if not has_locators:
        sparse_reasons.append("No concrete evidence locators are available.")
    explicit_sparse = bool(UNKNOWN_EVIDENCE_PATTERN.search(text))
    if explicit_sparse:
        sparse_reasons.append("Project context says evidence is unknown, unavailable, absent, or uncited.")

    has_uploaded_knowledge = uploaded_count > 0 or parsed_file_count > 0
    sparse_evidence = (
        not has_uploaded_knowledge
        and imported_evidence_count == 0
        and imported_signal_count == 0
        and not has_locators
    ) or (
        explicit_sparse
        and not has_uploaded_knowledge
        and imported_evidence_count == 0
        and imported_signal_count == 0
    )
    evidence_warning = bool(sparse_reasons) and not sparse_evidence
    domain = infer_decision_domain(domain_text)
    roles = OWNER_ROLE_MAPS.get(domain, OWNER_ROLE_MAPS["general_business"])
    evidence_categories = evidence_categories_for_domain(domain, domain_text)

    return ReportQualityContext(
        sparse_evidence=sparse_evidence,
        evidence_warning=evidence_warning,
        sparse_reasons=sparse_reasons,
        zero_clarifications=zero_clarifications,
        has_concrete_locators=has_locators,
        has_budget_or_spend_evidence=has_budget_or_spend_evidence(state),
        decision_domain=domain,
        owner_roles=list(roles),
        evidence_categories=list(evidence_categories),
        provisional_report=required_clarifications_open,
        required_clarifications_open=required_clarifications_open,
        telemetry_privacy_required=requires_telemetry_privacy_caveat(text),
    )


def assess_risk_classification_gate(state: Any) -> RiskClassificationGateAssessment:
    """Warn when low operator classification conflicts with structured risks.

    This is a read-only projection: it does not rebuild decision objects, mutate
    risk classification, or inspect report prose.
    """
    selected = _risk_gate_text(_field_get(state, "risk_classification"))
    normalized = _normalize_risk_classification(selected)
    rows = _generated_structured_risk_rows(state)
    highest = _highest_risk_severity(row.get("severity", "") for row in rows)
    high_or_critical = tuple(
        row for row in rows if _normalize_risk_severity(row.get("severity")) in {"high", "critical"}
    )
    source_counts: dict[str, int] = {}
    for row in rows:
        source = row.get("source", "") or "unknown"
        source_counts[source] = source_counts.get(source, 0) + 1

    warning_applies = normalized in {"minimal_risk", "low", "low_risk"} and bool(high_or_critical)
    return RiskClassificationGateAssessment(
        warning_applies=warning_applies,
        warning_text=RISK_CLASSIFICATION_WARNING if warning_applies else "",
        selected_classification=selected,
        normalized_classification=normalized,
        highest_generated_risk_severity=highest,
        high_or_critical_risk_count=len(high_or_critical),
        source_counts=source_counts,
        high_or_critical_risks=high_or_critical[:20],
    )


def _generated_structured_risk_rows(state: Any) -> tuple[dict[str, str], ...]:
    rows: list[dict[str, str]] = []
    decision_objects = _field_get(state, "decision_objects")
    for risk in _iter_maybe(_field_get(decision_objects, "risks")):
        source_phase = _risk_gate_text(_field_get(risk, "source_phase"))
        rows.append(
            _risk_gate_row(
                source=f"decision_objects.{source_phase}" if source_phase else "decision_objects",
                severity=_field_get(risk, "severity"),
                title=_field_get(risk, "title") or _field_get(risk, "risk_id") or "Decision-object risk",
                summary=_field_get(risk, "summary"),
            )
        )

    audit = _field_get(state, "audit")
    for item in _iter_maybe(_field_get(audit, "fmea")):
        component = _risk_gate_text(_field_get(item, "component"))
        rows.append(
            _risk_gate_row(
                source="audit.fmea",
                severity=_risk_severity_from_rpn(_intish(_field_get(item, "rpn"))),
                title=f"FMEA: {component}" if component else "FMEA risk",
                summary=(
                    _field_get(item, "failure_mode")
                    or _field_get(item, "effect")
                    or _field_get(item, "action")
                    or ""
                ),
            )
        )
    for item in _iter_maybe(_field_get(audit, "stpa")):
        control_action = _risk_gate_text(_field_get(item, "control_action"))
        rows.append(
            _risk_gate_row(
                source="audit.stpa",
                severity="high",
                title=f"STPA: {control_action}" if control_action else "STPA risk",
                summary=_field_get(item, "hazard") or _field_get(item, "constraint") or "",
            )
        )

    gauntlet = _field_get(state, "gauntlet")
    for result in _iter_maybe(_field_get(gauntlet, "results")):
        result_id = _risk_gate_text(_field_get(result, "id")) or "?"
        rows.append(
            _risk_gate_row(
                source="gauntlet",
                severity=_risk_severity_from_gauntlet(
                    _intish(_field_get(result, "risk_rank")),
                    _field_get(result, "top_fmea"),
                ),
                title=f"Gauntlet risk {result_id}",
                summary=_field_get(result, "crux") or _field_get(result, "fta_cut_set") or "Gauntlet-identified risk",
            )
        )
    return tuple(row for row in rows if row.get("severity"))


def _risk_gate_row(*, source: str, severity: Any, title: Any, summary: Any) -> dict[str, str]:
    return {
        "source": _risk_gate_text(source, limit=80),
        "severity": _normalize_risk_severity(severity),
        "title": _risk_gate_text(title),
        "summary": _risk_gate_text(summary),
    }


def _normalize_risk_classification(value: Any) -> str:
    raw = getattr(value, "value", value)
    return re.sub(r"[\s\-]+", "_", str(raw or "").strip().lower())


def _normalize_risk_severity(value: Any) -> str:
    raw = getattr(value, "value", value)
    normalized = re.sub(r"[\s\-]+", "_", str(raw or "").strip().lower())
    if normalized in {"critical", "crit"}:
        return "critical"
    if normalized in {"high", "severe"}:
        return "high"
    if normalized in {"medium", "med", "moderate"}:
        return "medium"
    if normalized in {"low", "minimal"}:
        return "low"
    return normalized


def _highest_risk_severity(values: Any) -> str:
    ranked = {"low": 1, "medium": 2, "high": 3, "critical": 4}
    highest = ""
    highest_rank = 0
    for value in values:
        severity = _normalize_risk_severity(value)
        rank = ranked.get(severity, 0)
        if rank > highest_rank:
            highest = severity
            highest_rank = rank
    return highest


def _risk_severity_from_rpn(rpn: int) -> str:
    if rpn >= 200:
        return "critical"
    if rpn >= 120:
        return "high"
    if rpn >= 60:
        return "medium"
    return "low"


def _risk_severity_from_gauntlet(risk_rank: int, top_fmea: Any) -> str:
    rpn = _intish(_field_get(top_fmea, "rpn"))
    if rpn:
        return _risk_severity_from_rpn(rpn)
    if risk_rank <= 1:
        return "high"
    if risk_rank == 2:
        return "medium"
    return "low"


def _intish(value: Any) -> int:
    raw = getattr(value, "value", value)
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            return 0


def _risk_gate_text(value: Any, limit: int = 180) -> str:
    raw = getattr(value, "value", value)
    text = re.sub(r"\s+", " ", str(raw or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def has_required_clarifications_open(state: Any) -> bool:
    """Return True when decision-critical clarification work remains open.

    Only generated clarification questions can block client readiness. Missing
    clarification state, empty cycles, or zero answers by themselves are not
    evidence that required clarification work exists.
    """
    questions = _generated_clarification_questions(state)
    if not questions:
        return False

    answered_ids = _answered_clarification_ids(state)
    for question in questions:
        if not _clarification_question_is_required(question):
            continue
        question_id = _clarification_item_id(question)
        if question_id and question_id in answered_ids:
            continue
        if _clarification_question_is_resolved(question):
            continue
        if _clarification_question_is_open(question):
            return True
    return False


def _generated_clarification_questions(state: Any) -> list[Any]:
    questions: list[Any] = []
    cycles = _clarification_get(state, "clarification_cycles")
    if not cycles:
        return questions
    for cycle in _iter_clarification_values(cycles):
        cycle_questions = _clarification_get(cycle, "questions")
        if not cycle_questions:
            continue
        questions.extend(_iter_clarification_values(cycle_questions))
    return questions


def _answered_clarification_ids(state: Any) -> set[str]:
    answered_ids: set[str] = set()
    answers = _clarification_get(state, "clarification_answers")
    if not answers:
        return answered_ids
    if isinstance(answers, dict):
        for key, answer in answers.items():
            key_text = str(key or "").strip()
            if key_text:
                answered_ids.add(key_text)
            if not isinstance(answer, (str, bytes)):
                answer_id = _clarification_item_id(answer)
                if answer_id:
                    answered_ids.add(answer_id)
        return answered_ids
    for answer in _iter_clarification_values(answers):
        answer_id = _clarification_item_id(answer)
        if answer_id:
            answered_ids.add(answer_id)
    return answered_ids


def _clarification_question_is_required(question: Any) -> bool:
    for field_name in ("priority", "severity", "importance"):
        if _normalize_clarification_text(_clarification_get(question, field_name)) in {"critical", "high"}:
            return True
    for field_name in ("required", "is_required"):
        if _boolish(_clarification_get(question, field_name)) is True:
            return True
    return False


def _clarification_question_is_resolved(question: Any) -> bool:
    if _boolish(_clarification_get(question, "answered")) is True:
        return True
    if _boolish(_clarification_get(question, "resolved")) is True:
        return True
    return _normalize_clarification_text(_clarification_get(question, "status")) in {
        "answered",
        "resolved",
        "unavailable",
        "superseded",
        "closed",
        "complete",
        "completed",
        "waived",
        "not_applicable",
        "n/a",
    }


def _clarification_question_is_open(question: Any) -> bool:
    status = _normalize_clarification_text(_clarification_get(question, "status"))
    if not status:
        return True
    return status in {"open", "pending", "unanswered", "required"}


def _clarification_item_id(item: Any) -> str:
    for field_name in ("question_id", "id", "clarification_id"):
        value = _clarification_get(item, field_name)
        text = str(getattr(value, "value", value) or "").strip()
        if text:
            return text
    return ""


def _clarification_get(item: Any, field_name: str, default: Any = None) -> Any:
    if item is None:
        return default
    if isinstance(item, dict):
        return item.get(field_name, default)
    return getattr(item, field_name, default)


def _iter_clarification_values(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, dict):
        return list(value.values())
    if isinstance(value, (str, bytes)):
        return []
    try:
        return list(value)
    except TypeError:
        return []


def _normalize_clarification_text(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip().lower()


def _boolish(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    normalized = _normalize_clarification_text(value)
    if normalized in {"true", "yes", "1"}:
        return True
    if normalized in {"false", "no", "0"}:
        return False
    return None


def evidence_accounting_projection(state: Any) -> EvidenceAccountingProjection:
    """Return export-facing evidence accounting without changing state."""
    uploaded_files = _uploaded_files(state)
    parsed_names = _parsed_file_display_names(uploaded_files)
    explicit_missing = _explicit_missing_evidence_file_names(state, parsed_names)
    rejected_names = _explicit_rejected_file_display_names(state)
    missing_or_rejected = _unique_file_names([*explicit_missing, *rejected_names])
    citation_marker_count, resolved_count = _citation_marker_counts(state)
    concrete_count = _concrete_source_locator_count(state)
    generic_category_warning = ""
    if not missing_or_rejected and _generic_evidence_categories_look_incomplete(state):
        generic_category_warning = EVIDENCE_CATEGORY_COVERAGE_WARNING
    return EvidenceAccountingProjection(
        citation_marker_count=citation_marker_count,
        citation_markers_resolved_count=resolved_count,
        citation_markers_resolved=citation_marker_count > 0 and resolved_count >= citation_marker_count,
        concrete_source_locator_count=concrete_count,
        concrete_source_locators_available=concrete_count > 0,
        uploaded_file_count=len(uploaded_files),
        parsed_file_count=len(parsed_names),
        rejected_or_unsupported_file_count=len(missing_or_rejected),
        imported_evidence_count=len(getattr(state, "imported_evidence", []) or []),
        imported_evidence_available=bool(getattr(state, "imported_evidence", []) or []),
        imported_signal_count=len(getattr(state, "imported_signals", []) or []),
        imported_signals_available=bool(getattr(state, "imported_signals", []) or []),
        parsed_file_display_names=tuple(parsed_names),
        rejected_file_display_names=tuple(missing_or_rejected),
        explicit_missing_file_display_names=tuple(explicit_missing),
        unsupported_or_missing_warning=UNSUPPORTED_EVIDENCE_FILES_WARNING if missing_or_rejected else "",
        category_coverage_warning=generic_category_warning,
    )


def evidence_maturity_projection(
    state: Any,
    context: ReportQualityContext | None = None,
) -> EvidenceMaturityProjection:
    context = context or assess_report_quality_context(state)
    accounting = evidence_accounting_projection(state)
    uploaded_count = accounting.uploaded_file_count
    imported_evidence_count = accounting.imported_evidence_count
    imported_signal_count = accounting.imported_signal_count
    has_locators = accounting.concrete_source_locators_available

    if (
        uploaded_count == 0
        and imported_evidence_count == 0
        and imported_signal_count == 0
        and not has_locators
    ):
        return EvidenceMaturityProjection(
            maturity="Hypothesis-only",
            client_use_status="Internal planning only",
            validation_required="Sprint 0 evidence pack",
            uploaded_files=uploaded_count,
            imported_evidence=imported_evidence_count,
            imported_signals=imported_signal_count,
            has_concrete_locators=has_locators,
            **_evidence_accounting_kwargs(accounting),
        )

    if not context.sparse_evidence and has_locators and (
        imported_evidence_count > 0 or imported_signal_count > 0
    ):
        return EvidenceMaturityProjection(
            maturity="Validated",
            client_use_status="Review for delivery",
            validation_required="Decision-critical locators present",
            uploaded_files=uploaded_count,
            imported_evidence=imported_evidence_count,
            imported_signals=imported_signal_count,
            has_concrete_locators=has_locators,
            **_evidence_accounting_kwargs(accounting),
        )

    return EvidenceMaturityProjection(
        maturity="Partial evidence",
        client_use_status="Validate before client delivery",
        validation_required="Targeted evidence follow-up",
        uploaded_files=uploaded_count,
        imported_evidence=imported_evidence_count,
        imported_signals=imported_signal_count,
        has_concrete_locators=has_locators,
        **_evidence_accounting_kwargs(accounting),
    )


def _evidence_accounting_kwargs(accounting: EvidenceAccountingProjection) -> dict[str, Any]:
    return {
        "citation_marker_count": accounting.citation_marker_count,
        "citation_markers_resolved_count": accounting.citation_markers_resolved_count,
        "citation_markers_resolved": accounting.citation_markers_resolved,
        "concrete_source_locator_count": accounting.concrete_source_locator_count,
        "concrete_source_locators_available": accounting.concrete_source_locators_available,
        "uploaded_file_count": accounting.uploaded_file_count,
        "parsed_file_count": accounting.parsed_file_count,
        "rejected_or_unsupported_file_count": accounting.rejected_or_unsupported_file_count,
        "imported_evidence_count": accounting.imported_evidence_count,
        "imported_evidence_available": accounting.imported_evidence_available,
        "imported_signal_count": accounting.imported_signal_count,
        "imported_signals_available": accounting.imported_signals_available,
    }


def infer_decision_domain(text: str) -> str:
    source = text or ""
    if PRODUCTIZATION_PATTERN.search(source):
        return "productization"
    if AI_READINESS_PATTERN.search(source):
        return "ai_readiness"
    if AUTOMATION_ROI_PATTERN.search(source):
        return "automation_roi"
    if SEO_PATTERN.search(source):
        return "seo_content_editorial"
    if GROWTH_PATTERN.search(source):
        return "growth"
    return "general_business"


def evidence_categories_for_domain(domain: str, source_text: str = "") -> list[str]:
    if domain in EVIDENCE_CATEGORY_MAPS:
        categories = list(EVIDENCE_CATEGORY_MAPS[domain])
        if domain == "productization" and SEO_PATTERN.search(source_text or ""):
            categories.append("explicit CMS/content publishing capability, if relevant")
        return categories
    return EVIDENCE_CATEGORY_MAPS["general_business"]


def requires_productization_wave_matrix(state: Any, context: ReportQualityContext | None = None) -> bool:
    context = context or assess_report_quality_context(state)
    if context.decision_domain == "productization":
        return True
    return bool(WAVE2_PATTERN.search(_domain_source_text(state)))


def has_concrete_evidence_locators(state: Any) -> bool:
    return _concrete_source_locator_count(state) > 0


def has_budget_or_spend_evidence(state: Any) -> bool:
    text = _combined_state_text(state)
    return bool(re.search(r"(\bbudget\b|\bspend\b|\bcost\b|\bfinance\b|\bfinancial\b|\brevenue\b|\bmargin\b|\bsavings?\b|\binvoice\b)", text, re.I))


def constraint_adherence_projection(state: Any) -> ConstraintAdherenceProjection:
    """Detect clear generated-output contradictions against operator constraints.

    This is a read-only advisory projection. It only reads operator-provided
    context for constraints and only inspects generated strategy/report text for
    contradiction signals.
    """
    operator_text = _operator_constraint_source_text(state)
    detected_constraints = _detect_operator_hard_constraints(operator_text)
    if not detected_constraints:
        return ConstraintAdherenceProjection(warning_applies=False)

    generated_text = _generated_strategy_report_text(state)
    contradiction_signals = _detect_constraint_contradiction_signals(
        generated_text,
        detected_constraints,
    )
    warning_applies = bool(contradiction_signals)
    return ConstraintAdherenceProjection(
        warning_applies=warning_applies,
        warning_text=CONSTRAINT_ADHERENCE_WARNING if warning_applies else "",
        detected_constraints=detected_constraints,
        contradiction_signals=contradiction_signals,
        operator_context_preview=_short_constraint_preview(operator_text),
    )


def constraint_adherence_warnings(state: Any) -> list[str]:
    projection = constraint_adherence_projection(state)
    if not projection.warning_applies:
        return []
    details = []
    if projection.detected_constraints:
        details.append("Detected operator constraints: " + ", ".join(projection.detected_constraints) + ".")
    if projection.contradiction_signals:
        details.append("Contradiction signals: " + "; ".join(projection.contradiction_signals) + ".")
    return [" ".join([projection.warning_text, *details]).strip()]


def _operator_constraint_source_text(state: Any) -> str:
    parts = [
        getattr(state, "brief", ""),
        getattr(state, "data_context", ""),
    ]
    for answer in list(getattr(state, "clarification_answers", []) or []):
        parts.append(getattr(answer, "answer_text", ""))
    return "\n".join(str(part) for part in parts if part)


def _detect_operator_hard_constraints(text: str) -> tuple[str, ...]:
    source = str(text or "")
    detected: list[str] = []
    if re.search(
        r"\b(?:limited|constrained|scarce|thin|low)\s+capacity\b|"
        r"\bcapacity\s+(?:is\s+)?(?:limited|constrained|scarce|thin|low)\b",
        source,
        re.I,
    ):
        detected.append("limited capacity")
    if re.search(
        r"\b(?:only\s+)?(?:one|1)\s+(?:focused\s+)?initiative\b.*\b(?:one|1)\s+(?:small\s+)?experiment\b|"
        r"\b(?:only\s+)?(?:one|1)\s+(?:small\s+)?experiment\b.*\b(?:one|1)\s+(?:focused\s+)?initiative\b|"
        r"\bonly\s+(?:one|1)\s+(?:focused\s+)?initiative\b",
        source,
        re.I | re.S,
    ):
        detected.append("one focused initiative plus one small experiment")
    if re.search(
        r"\b(?:no|avoid|defer|without)\s+major\s+engineering\b|"
        r"\bmajor\s+engineering\s+(?:project|work|initiative|track)\s+(?:is\s+)?(?:not\s+allowed|prohibited|off[- ]limits)\b",
        source,
        re.I,
    ):
        detected.append("no major engineering project")
    if re.search(
        r"\b(?:budget|spend)\s+(?:is\s+)?(?:limited|capped|frozen)\b|"
        r"\bbudget\s+limited\s+to\b|"
        r"\b(?:avoid|defer|freeze|no)\s+broad\s+growth\s+spend\b|"
        r"\bspend\s+freeze\b|"
        r"\bno\s+(?:new\s+)?(?:paid\s+acquisition|growth)\s+spend\b",
        source,
        re.I,
    ):
        detected.append("spend or budget limit")
    return tuple(_unique(detected))


def _generated_strategy_report_text(state: Any) -> str:
    parts = [getattr(state, "report", "")]
    strategy = getattr(state, "strategy", None)
    if strategy:
        parts.extend([
            getattr(strategy, "executive_strategy", ""),
            getattr(strategy, "implementation_sequence", ""),
            getattr(strategy, "monitoring_plan", ""),
        ])
        for action in list(getattr(strategy, "strategies", []) or []):
            parts.extend([
                getattr(action, "priority", ""),
                getattr(action, "action", ""),
                getattr(action, "justification", ""),
                getattr(action, "expected_impact", ""),
                getattr(action, "risk_if_ignored", ""),
                getattr(action, "timeline", ""),
            ])
    return "\n".join(str(part) for part in parts if part)


def _detect_constraint_contradiction_signals(
    generated_text: str,
    detected_constraints: tuple[str, ...],
) -> tuple[str, ...]:
    constraints = set(detected_constraints)
    signals: list[str] = []
    capacity_limited = bool(
        constraints
        & {
            "limited capacity",
            "one focused initiative plus one small experiment",
        }
    )
    if capacity_limited and _has_non_negated_constraint_match(
        generated_text,
        re.compile(
            r"\b(?:three|3)\s+parallel\s+critical(?:-priority)?\s+tracks\b|"
            r"\b(?:run|execute|launch|pursue)\s+(?:three|3)\s+critical\s+tracks\s+in\s+parallel\b|"
            r"\bmultiple\s+critical\s+tracks\b|"
            r"\bparallel\s+critical(?:-priority)?\s+tracks\b",
            re.I,
        ),
    ):
        signals.append("multiple parallel critical tracks despite constrained capacity")

    if "no major engineering project" in constraints and _has_non_negated_constraint_match(
        generated_text,
        re.compile(
            r"\b(?:major|large|full)\s+engineering\s+(?:project|initiative|track|work|build)\b|"
            r"\bengineering\s+(?:project|initiative|track|work|build)\b.{0,40}\b(?:major|large|full)\b",
            re.I,
        ),
    ):
        signals.append("major engineering work despite explicit no-major-engineering constraint")

    if "spend or budget limit" in constraints and _has_non_negated_constraint_match(
        generated_text,
        re.compile(
            r"\b(?:increase|increased|scale|expand|raise|ramp(?:\s+up)?)\s+(?:broad\s+)?(?:paid\s+acquisition|growth)\s+spend\b|"
            r"\b(?:increase|increased|scale|expand|raise|ramp(?:\s+up)?)\s+paid\s+(?:spend|budget|budgets)\b|"
            r"\b(?:scale|expand|ramp(?:\s+up)?)\s+paid\s+acquisition\b|"
            r"\b(?:launch|start|run|execute)\s+(?:new\s+)?paid\s+campaigns?\b|"
            r"\b(?:paid\s+acquisition|paid)\s+budgets?\s+(?:increase|increased|expansion|scale-up)\b|"
            r"\b(?:broad|large|major)\s+(?:paid\s+acquisition|growth)\s+spend\b|"
            r"\b(?:paid\s+acquisition|growth|paid)\s+spend\s+(?:increase|increases|increased|expansion|scale-up)\b|"
            r"\blarge\s+acquisition\s+spend\s+increase\b",
            re.I,
        ),
    ):
        signals.append("broad or increased growth spend despite explicit spend constraint")

    return tuple(_unique(signals))


def _has_non_negated_constraint_match(text: str, pattern: re.Pattern[str]) -> bool:
    for segment in _constraint_scan_segments(text):
        for match in pattern.finditer(segment):
            if _constraint_match_is_negated(segment, match.start(), match.end(), match.group(0)):
                continue
            return True
    return False


def _constraint_scan_segments(text: str) -> list[str]:
    segments: list[str] = []
    safe_heading = ""
    for line in re.split(r"\n+", str(text or "")):
        stripped = line.strip()
        if not stripped:
            safe_heading = ""
            continue
        if stripped.startswith("#"):
            safe_heading = ""
        if CONSTRAINT_SAFE_HEADING_PATTERN.search(stripped):
            safe_heading = stripped
        for segment in re.split(r"(?<=[.!?])\s+", stripped):
            segment = segment.strip()
            if not segment:
                continue
            if safe_heading and re.match(r"^(?:[-*]|\d+[.)])\s+", segment):
                segments.append(f"{safe_heading} {segment}")
            else:
                segments.append(segment)
    return segments


def _constraint_match_is_negated(segment: str, match_start: int, match_end: int, match_text: str = "") -> bool:
    prefix = segment[max(0, match_start - 180):match_start]
    suffix = segment[match_end:match_end + 180]
    if re.search(r"\bwhat\s+not\s+to\s+do\b.{0,160}$", prefix, re.I):
        return True
    if _constraint_markdown_row_has_safe_verdict(segment, match_end):
        return True
    if _constraint_prefix_has_shared_safe_list(prefix):
        return True
    if _constraint_match_is_conditional_risk_context(prefix, match_text):
        return True
    if CONSTRAINT_SAFE_PREFIX_PATTERN.search(prefix):
        return True
    return bool(CONSTRAINT_SAFE_SUFFIX_PATTERN.search(suffix))


def _constraint_markdown_row_has_safe_verdict(segment: str, match_end: int) -> bool:
    if "|" not in segment:
        return False
    suffix = segment[match_end:]
    return bool(
        re.search(
            r"\|\s*(?:\*\*)?\s*(?:do\s+not\s+do(?:\s+this\s+month)?|"
            r"do\s+not\s+start|not\s+recommended|deferred|blocked|"
            r"out\s+of\s+scope|paused|parked)(?:\s*(?:\*\*)?)\s*\|",
            suffix,
            re.I,
        )
    )


def _constraint_match_is_conditional_risk_context(prefix: str, match_text: str) -> bool:
    if not (
        CONSTRAINT_SAFE_CONDITIONAL_PREFIX_PATTERN.search(prefix)
        or re.search(
            r"\b(?:if|when|unless|whether|risk\s+if|risk\s+of|risks?\s+if|"
            r"warning\s+if|watch\s+if|monitor\s+if|stop\s+if|trigger\s+if)\b.{0,120}$",
            prefix,
            re.I,
        )
    ):
        return False
    if re.search(r"\brecommended\s+path\b|\brecommend(?:ed|s)?\b", prefix, re.I):
        return False
    return bool(
        re.search(
            r"\b(?:paid\s+)?spend\s+increase(?:s|d)?\b|"
            r"\bpaid\s+acquisition\s+mix\s+continues?\s+shifting\b",
            match_text,
            re.I,
        )
    )


def _constraint_prefix_has_shared_safe_list(prefix: str) -> bool:
    match = re.search(
        r"\b(?:do\s+not|don't|dont|should\s+not|must\s+not|cannot|can't|"
        r"avoid(?:s|ed|ing)?|defer(?:s|red|ring)?|block(?:s|ed|ing)?|"
        r"pause(?:s|d|ing)?|freeze(?:s|d|ing)?|frozen|park(?:s|ed|ing)?|no|never|without|"
        r"prohibit(?:s|ed|ing)?|forbid(?:s|den|ding)?|forbidden)\b"
        r"(?P<body>.{0,140})\b(?:and|or)\s*$",
        prefix,
        re.I,
    )
    if not match:
        return False
    return not bool(
        re.search(
            r"\b(?:then|but|however|recommend(?:ed)?|launch|run|execute|"
            r"increase|scale|expand|raise|ramp(?:\s+up)?|start)\b",
            match.group("body"),
            re.I,
        )
    )


def _short_constraint_preview(text: str, limit: int = 240) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 3)].rstrip() + "..."


def requires_telemetry_privacy_caveat(text: str) -> bool:
    return bool(TELEMETRY_PATTERN.search(text or ""))


def client_simplify_text(text: str, *, sparse_evidence: bool = False) -> str:
    """Translate technical report wording for client-facing dossier sections."""
    value = str(text or "")
    value = re.sub(r"(?<=\d)\.\s+(?=\d)", ".", value)
    value = re.sub(
        r"\bBF\s*=\s*(\d+(?:\.\d+)?)\b(?:\s*[—-]\s*domain complexity confirmed\b)?",
        r"structural BF estimate=\1 (operator trace, not measured posterior)",
        value,
        flags=re.I,
    )
    citation_repeated = len(re.findall(r"citation unavailable", value, flags=re.I)) > 1
    replacements = [
        (r"\b(?:FMEA-derived labels?|FMEA)\b", "structured risk review"),
        (r"\b(?:RPN|risk priority numbers?)(?:\s*[=:]\s*\d+(?:\.\d+)?|\s+\d+(?:\.\d+)?)?\b", "risk priority score"),
        (r"(?<!structural\s)\b(?:Bayes factor|BF)(?:\s*[=:]\s*\d+(?:\.\d+)?|\s+\d+(?:\.\d+)?)?\b", "structural confidence signal"),
        (r"\bDQ(?:\s*[=:]\s*\d+(?:\.\d+)?|\s+\d+(?:\.\d+)?)?\b", "evidence quality signal"),
        (r"\bH_norm(?:\s*[=:]\s*\d+(?:\.\d+)?|\s+\d+(?:\.\d+)?)?\b", "uncertainty signal"),
        (
            r"\b(?:portfolio correlation|correlation coefficient|rho)(?:\s*[=:]\s*[01]?(?:\.\d+)?|\s+[01]?(?:\.\d+)?)?\b"
            r"|\bcorrelation\s*(?:[=:]\s*)?[01](?:\.\d+)?\b"
            r"|ρ\s*[=:]?\s*[01]?(?:\.\d+)?",
            "related-hypothesis risk",
        ),
        (r"\bJaccard(?: index)?(?:\s*[=:]\s*[01]?(?:\.\d+)?|\s+[01]?(?:\.\d+)?)?\b", "schema overlap score"),
        (r"\bBrier score(?:\s*[=:]\s*[01]?(?:\.\d+)?|\s+[01]?(?:\.\d+)?)?\b", "forecast accuracy check"),
        (r"\bECE(?:\s*[=:]\s*[01]?(?:\.\d+)?|\s+[01]?(?:\.\d+)?)?\b", "calibration check"),
    ]
    for pattern, replacement in replacements:
        value = re.sub(pattern, replacement, value, flags=re.I)
    value = _replace_hypothesis_ids(value)
    if sparse_evidence:
        value = _simplify_sparse_precision(value)
    value = re.sub(r"\[#\d+\]", "", value)
    if citation_repeated:
        value = re.sub(r"\s*\(?citation unavailable\)?\.?", "", value, flags=re.I)
        value = f"{NO_CONCRETE_LOCATORS_CLIENT_NOTE}\n\n{value.strip()}"
    value = _cleanup_client_replacement_artifacts(value)
    return normalize_export_text(_collapse_blank_lines(value), audience="client")


def normalize_export_text(text: str, audience: str = "client") -> str:
    """Final export prose cleanup, scoped by audience and protected contexts."""
    mode = "operator" if str(audience or "").lower() == "operator" else "client"
    source = _renumber_repeated_ordered_markers(_join_standalone_list_markers(str(text or "")))
    output: list[str] = []
    in_code_block = False
    lines = source.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            output.append(line)
            index += 1
            continue
        if in_code_block or _looks_like_json_line(stripped):
            output.append(line)
            index += 1
            continue
        if _looks_like_markdown_table_line(line):
            table_lines: list[str] = []
            while index < len(lines) and _looks_like_markdown_table_line(lines[index]):
                table_lines.append(lines[index])
                index += 1
            if _is_protected_export_table_block(table_lines):
                output.extend(table_lines)
            else:
                output.extend(_normalize_export_line(table_line, mode) for table_line in table_lines)
            continue
        output.append(_normalize_export_line(line, mode))
        index += 1
    value = "\n".join(output)
    if mode == "client":
        value = _remove_client_citation_placeholder_noise(value)
    return value


def suppress_client_raw_evidence_ids(text: str) -> str:
    """Hide raw evidence IDs in client-facing text when no concrete locator exists."""
    lines: list[str] = []
    in_code_block = False
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            lines.append(line)
            continue
        if _explicit_operator_or_machine_context(line) and (
            in_code_block
            or _looks_like_json_line(stripped)
            or _explicit_protected_client_context_line(line)
        ):
            lines.append(line)
            continue
        lines.append(_suppress_client_raw_evidence_line(line))
    return "\n".join(lines)


def _suppress_client_raw_evidence_line(line: str) -> str:
    had_raw_evidence_token = bool(
        re.search(
            r"\[Evidence:\s*|\bknowledge[_-][A-Za-z0-9_.:-]+\b|\b(?:ev|evidence|src)[-_][A-Za-z0-9_.:-]+\b|"
            r"\bupload:[^\s|,)>\]]+|\bstorage_ref\s*[:=]|\bsource_ref\s*[:=]",
            str(line or ""),
            re.I,
        )
    )
    value = _remove_marker_orphan_fragments(line)
    value = re.sub(
        r"\s*\[Evidence:\s*[A-Za-z0-9_.:-]+\s*(?:\|[^\]]*)?\]",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(r"\s*\[#\d+\]", "", value)
    value = re.sub(r"\b(?:ev|evidence|src)[-_][A-Za-z0-9_.:-]+\b", "project evidence", value, flags=re.I)
    value = re.sub(r"\bknowledge[_-][A-Za-z0-9_.:-]+\b", "project evidence", value, flags=re.I)
    value = re.sub(r"\bupload:[^\s|,)>\]]+", "uploaded project document", value, flags=re.I)
    value = re.sub(r"\bstorage_ref\s*[:=]\s*[^\s|,)>\]]+", "evidence source unavailable", value, flags=re.I)
    value = re.sub(r"\bsource_ref\s*[:=]\s*[^\s|,)>\]]+", "evidence source unavailable", value, flags=re.I)
    value = re.sub(r"(?i)(api[_-]?key|token|password|secret|credential)\s*[:=]\s*[^,\s|;]+", r"\1=[REDACTED]", value)
    value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
    value = re.sub(r"\bsk-[A-Za-z0-9_\-]{8,}\b", "[REDACTED]", value)
    value = re.sub(r"(?i)\b[A-Z]:[\\/][^\s|]+", "local path redacted", value)
    value = re.sub(r"\\\\[A-Za-z0-9_.-]+\\[^\s|]+", "local path redacted", value)
    value = re.sub(r"file://[^\s|]+", "local path redacted", value, flags=re.I)
    value = re.sub(
        r"\b(?:evidence source unavailable|uploaded project document|project evidence)"
        r"(?:\s+(?:uploaded project document|project document|source))*\s+"
        r"provides evidence interpretation context\b[^.?!]*(?:[.?!]|$)",
        "Evidence source unavailable.",
        value,
        flags=re.I,
    )
    if had_raw_evidence_token:
        value = re.sub(r"\bconfirms\b", "supports", value, flags=re.I)
        value = re.sub(r"\bis confirmed\b", "requires validation", value, flags=re.I)
        value = re.sub(r"\bwas confirmed\b", "requires validation", value, flags=re.I)
    value = _remove_subjectless_evidence_fragments(value)
    return value


def _explicit_operator_or_machine_context(line: str) -> bool:
    return bool(
        re.search(
            r"\b(?:operator[-\s]?only|operator\s+diagnostic|operator\s+trace|machine[_\s-]?archive|"
            r"project_state\.json|phase_outputs\.json|decision_objects\.json|export_manifest\.json)\b",
            str(line or ""),
            re.I,
        )
    )


def _explicit_protected_client_context_line(line: str) -> bool:
    return bool(
        re.search(
            r"\b(?:source\s+excerpt|source\s+text|raw\s+source|quoted\s+source|machine[_\s-]?archive)\b",
            str(line or ""),
            re.I,
        )
    )


def _remove_marker_orphan_fragments(line: str) -> str:
    marker = r"(?:\[Evidence:\s*[A-Za-z0-9_.:-]+\s*(?:\|[^\]]*)?\]|\[#\d+\])"
    orphan_starter = r"(?:suggests|provides evidence|provides context|indicates|supports)"
    return re.sub(
        rf"\s*{marker}\s+{orphan_starter}\b[^.?!]*(?:[.?!]|$)",
        "",
        str(line or ""),
        flags=re.I,
    )


def _remove_subjectless_evidence_fragments(line: str) -> str:
    orphan_starter = r"(?:suggests|provides evidence|provides context|indicates|supports)"
    value = re.sub(
        rf"(^|[.?!][\"”]?\s+){orphan_starter}\b[^.?!]*(?:[.?!]|$)",
        lambda match: match.group(1).rstrip(),
        str(line or ""),
        flags=re.I,
    )
    return value.strip() if str(line or "").strip() else value


def guard_client_bf_confidence(text: str, state: Any) -> str:
    """Keep sparse client prose from implying current causal confidence."""
    value = str(text or "")
    hypothesis_only = evidence_maturity_projection(state).maturity == "Hypothesis-only"
    if not current_bf_below_action_threshold(state) and not hypothesis_only:
        return value
    value = re.sub(
        r"\bconfirmed causal hypothesis\b",
        "candidate causal hypothesis pending Sprint 0 validation",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"\bcausal hypothesis is confirmed\b",
        "causal hypothesis remains unconfirmed",
        value,
        flags=re.I,
    )
    value = _hide_client_trace_values_outside_decision_gates(value)
    if CLIENT_BF_CONFIDENCE_CAVEAT not in value:
        value = "\n\n".join([CLIENT_BF_CONFIDENCE_CAVEAT, value.strip()])
    return value


def current_bf_below_action_threshold(state: Any, threshold: float = 10.0) -> bool:
    classify = getattr(state, "classify", None)
    try:
        return float(getattr(classify, "bf", None)) < threshold
    except (TypeError, ValueError):
        return True


def _hide_client_trace_values_outside_decision_gates(text: str) -> str:
    lines: list[str] = []
    in_decision_gates = False
    in_code_block = False
    for raw_line in str(text or "").splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            lines.append(raw_line)
            continue
        heading = re.match(r"^#{1,6}\s+(.+?)\s*$", stripped)
        if heading:
            in_decision_gates = _normalize_heading(heading.group(1)) == "decision gates"
            lines.append(raw_line)
            continue
        if in_code_block or in_decision_gates or _looks_like_json_line(stripped):
            lines.append(raw_line)
            continue
        lines.append(_hide_client_trace_values(raw_line))
    return "\n".join(lines)


def _hide_client_trace_values(line: str) -> str:
    value = str(line or "")
    value = re.sub(
        r"\b(?:Bayes factor|BF)\s*(?:=|:)?\s*\d+(?:\.\d+)?\b",
        "structural confidence signal",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"\bRPN\s*(?:=|:)?\s*\d+(?:\.\d+)?\b",
        "provisional risk estimate",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"\bDQ\s*(?:=|:)?\s*\d+(?:\.\d+)?\b",
        "diagnostic score",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"\b(?:raw\s+)?(?:prior|priors)\s*(?:=|:)?\s*(?:0(?:\.\d+)?|1(?:\.0+)?|\d{1,3}\s*%)\b",
        "structural prior",
        value,
        flags=re.I,
    )
    return value


def commitment_score_text(score: Any, rationale: str = "") -> str:
    try:
        numeric = float(score)
    except (TypeError, ValueError):
        return "Commitment score: Not scored — requires operator confirmation."
    if PLACEHOLDER_RATIONALE_PATTERN.match(rationale or ""):
        return "Commitment score: Not scored — requires operator confirmation."
    if numeric < 50:
        return (
            f"Commitment score: {_format_number(numeric)} — low. Evidence that would raise it: "
            "confirmed owner, decision deadline, budget/resource bounds, and explicit monitoring cadence."
        )
    return f"Commitment score: {_format_number(numeric)}"


def monitor_has_signals(monitor: Any) -> bool:
    if not monitor:
        return False
    schedule = getattr(monitor, "ooda_schedule", None)
    schedule_items = (
        list(getattr(schedule, "daily", []) or [])
        + list(getattr(schedule, "weekly", []) or [])
        + list(getattr(schedule, "monthly", []) or [])
    )
    return any(
        [
            schedule_items,
            list(getattr(monitor, "canaries", []) or []),
            list(getattr(monitor, "circuit_breakers", []) or []),
            list(getattr(monitor, "chaos_drills", []) or []),
        ]
    )


def monitor_success_metric_lines(monitor: Any, limit: int = 8) -> list[str]:
    if not monitor:
        return []
    lines: list[str] = []
    schedule = getattr(monitor, "ooda_schedule", None)
    for label, entries in (
        ("daily", getattr(schedule, "daily", []) or []),
        ("weekly", getattr(schedule, "weekly", []) or []),
        ("monthly", getattr(schedule, "monthly", []) or []),
    ):
        for entry in entries:
            metric = getattr(entry, "metric", "")
            if metric:
                lines.append(f"{label} monitor: {metric}")
    for canary in list(getattr(monitor, "canaries", []) or []):
        signal = getattr(canary, "signal", "")
        meaning = getattr(canary, "meaning", "")
        if signal:
            lines.append(f"canary: {signal}" + (f" — {meaning}" if meaning else ""))
    for breaker in list(getattr(monitor, "circuit_breakers", []) or []):
        trip = getattr(breaker, "trip", "")
        if trip:
            lines.append(f"circuit breaker: {trip}")
    return lines[:limit]


def assess_decision_memo_pilot_plan_quality(state: Any) -> DecisionMemoQualityResult:
    """Return advisory deterministic QA findings for decision memo / pilot reports.

    The checks are structural and heuristic. They do not verify semantic truth
    and do not block report completion.
    """
    report = str(getattr(state, "report", "") or "")
    report_mode = _decision_memo_generated_report_mode(state)
    output_language = _decision_memo_generated_output_language(state)
    if report_mode != "decision_memo_pilot_plan" or not report.strip():
        return DecisionMemoQualityResult(
            checked=False,
            report_mode=report_mode,
            output_language=output_language,
        )

    headings = _decision_memo_expected_headings(output_language)
    heading_entries = _decision_memo_heading_entries(report)
    sections = _decision_memo_sections(heading_entries)
    appendix_key = _decision_memo_heading_key(headings[-1])
    main_text = _decision_memo_main_text(report, heading_entries, appendix_key)
    exact_markers = _decision_memo_exact_citation_markers(state)
    findings: list[DecisionMemoQualityFinding] = []

    _decision_memo_check_required_sections(findings, headings, sections)
    _decision_memo_check_appendix(findings, report, heading_entries, appendix_key)
    _decision_memo_check_duplicate_headings(findings, heading_entries)
    _decision_memo_check_empty_headings(findings, heading_entries)
    _decision_memo_check_markdown_tables(findings, report)
    _decision_memo_check_truncation(findings, report)
    _decision_memo_check_contradictory_counts(findings, main_text)
    _decision_memo_check_unsupported_numeric_claims(findings, main_text, exact_markers, output_language)
    _decision_memo_check_source_supported_locators(findings, main_text, exact_markers, output_language)
    _decision_memo_check_evidence_certainty_mismatch(findings, main_text, sections, output_language)
    _decision_memo_check_heading_language(findings, heading_entries, output_language)
    _decision_memo_check_monitoring_rows(findings, state, sections, output_language)

    return DecisionMemoQualityResult(
        checked=True,
        report_mode=report_mode,
        output_language=output_language,
        findings=tuple(findings),
    )


def _decision_memo_generated_report_mode(state: Any) -> str:
    language = str(getattr(state, "report_output_language", "") or "").strip()
    mode = str(getattr(state, "report_output_mode", "") or "").strip()
    if language and mode:
        return mode
    return "legacy_metadata_unknown" if getattr(state, "report", None) else "not_generated"


def _decision_memo_generated_output_language(state: Any) -> str:
    mode = str(getattr(state, "report_output_mode", "") or "").strip()
    value = str(getattr(state, "report_output_language", "") or "").strip()
    if mode and value in {"en", "es-MX"}:
        return value
    return "legacy_metadata_unknown" if getattr(state, "report", None) else "not_generated"


def _decision_memo_expected_headings(output_language: str) -> tuple[str, ...]:
    if output_language == "es-MX":
        return (
            "Decisión",
            "Recomendación",
            "Por qué se recomienda",
            "Hechos proporcionados por el operador",
            "Hipótesis y supuestos propuestos",
            "Desconocidos / no proporcionados",
            "Madurez de la evidencia",
            "Siguientes acciones",
            "Señales de monitoreo",
            "Umbrales para cambiar de curso",
            "Apéndice: Análisis técnico",
        )
    return (
        "Decision",
        "Recommendation",
        "Why this is recommended",
        "Operator-supplied facts",
        "Hypotheses and proposed assumptions",
        "Unknowns / not supplied",
        "Evidence maturity",
        "Next actions",
        "Monitoring signals",
        "Change-course thresholds",
        "Appendix: Technical Analysis",
    )


def _decision_memo_other_language_headings(output_language: str) -> tuple[str, ...]:
    return _decision_memo_expected_headings("en" if output_language == "es-MX" else "es-MX")


def _decision_memo_claim_labels(output_language: str) -> tuple[str, ...]:
    common = (
        "Operator-supplied fact",
        "Source-supported claim",
        "Inference",
        "Proposed operator assumption",
        "Unknown / not supplied",
        "Not applicable",
        "Hecho proporcionado por el operador",
        "Afirmación respaldada por fuente",
        "Inferencia",
        "Supuesto propuesto para el operador",
        "Desconocido / no proporcionado",
        "No aplica",
    )
    return common


def _decision_memo_operator_fact_labels(output_language: str) -> tuple[str, ...]:
    return ("Operator-supplied fact", "Hecho proporcionado por el operador")


def _decision_memo_source_supported_labels(output_language: str) -> tuple[str, ...]:
    return ("Source-supported claim", "Afirmación respaldada por fuente")


def _decision_memo_proposed_threshold_labels(output_language: str) -> tuple[str, ...]:
    return ("Proposed operator threshold", "Umbral propuesto por el operador")


def _decision_memo_heading_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", ascii_text.lower()).strip()


def _decision_memo_heading_entries(report: str) -> list[dict[str, Any]]:
    lines = str(report or "").splitlines()
    entries: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        match = re.match(r"^\s{0,3}(#{1,6})\s*(.*?)\s*#*\s*$", line)
        if not match:
            continue
        title = match.group(2).strip()
        entries.append(
            {
                "title": title,
                "key": _decision_memo_heading_key(title),
                "line_number": index + 1,
                "level": len(match.group(1)),
                "body": "",
            }
        )
    for pos, entry in enumerate(entries):
        start = entry["line_number"]
        end = entries[pos + 1]["line_number"] - 1 if pos + 1 < len(entries) else len(lines)
        entry["body"] = "\n".join(lines[start:end]).strip()
    return entries


def _decision_memo_sections(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    sections: dict[str, dict[str, Any]] = {}
    for entry in entries:
        key = entry.get("key", "")
        if key and key not in sections:
            sections[key] = entry
    return sections


def _decision_memo_main_text(report: str, entries: list[dict[str, Any]], appendix_key: str) -> str:
    appendix_line = 0
    for entry in entries:
        if entry.get("key") == appendix_key:
            appendix_line = int(entry.get("line_number") or 0)
            break
    if not appendix_line:
        return str(report or "")
    lines = str(report or "").splitlines()
    return "\n".join(lines[: max(0, appendix_line - 1)])


def _decision_memo_exact_citation_markers(state: Any) -> set[str]:
    try:
        from cdp.citation_resolvability import build_defense_pass_result

        result = build_defense_pass_result(state)
    except Exception:
        return set()
    return {
        str(getattr(resolution, "marker", "") or "")
        for resolution in getattr(result, "resolutions", []) or []
        if getattr(resolution, "status", "") == "resolved_exact"
    }


def _decision_memo_add_finding(
    findings: list[DecisionMemoQualityFinding],
    rule_name: str,
    message: str,
    *,
    location: str = "",
    excerpt: str = "",
) -> None:
    findings.append(
        DecisionMemoQualityFinding(
            rule_name=rule_name,
            message=message,
            location=location,
            excerpt=_decision_memo_excerpt(excerpt),
        )
    )


def _decision_memo_excerpt(value: str, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _decision_memo_check_required_sections(
    findings: list[DecisionMemoQualityFinding],
    headings: tuple[str, ...],
    sections: dict[str, dict[str, Any]],
) -> None:
    for heading in headings:
        if _decision_memo_heading_key(heading) not in sections:
            _decision_memo_add_finding(
                findings,
                "required_memo_section_missing",
                f"Required decision memo section is missing: {heading}.",
                location=heading,
            )


def _decision_memo_check_appendix(
    findings: list[DecisionMemoQualityFinding],
    report: str,
    entries: list[dict[str, Any]],
    appendix_key: str,
) -> None:
    appendix_entries = [entry for entry in entries if entry.get("key") == appendix_key]
    if not appendix_entries:
        _decision_memo_add_finding(
            findings,
            "appendix_missing_or_unseparated",
            "Appendix: Technical Analysis is missing as a Markdown heading.",
        )
        return
    appendix = appendix_entries[0]
    if int(appendix.get("line_number") or 0) <= 2:
        _decision_memo_add_finding(
            findings,
            "appendix_missing_or_unseparated",
            "Technical appendix appears before a substantive main memo.",
            location=f"line {appendix.get('line_number')}",
            excerpt=appendix.get("title", ""),
        )
    if not str(appendix.get("body") or "").strip():
        _decision_memo_add_finding(
            findings,
            "empty_heading",
            "Technical appendix heading has no visible content.",
            location=f"line {appendix.get('line_number')}",
            excerpt=appendix.get("title", ""),
        )
    main_text = _decision_memo_main_text(report, entries, appendix_key)
    if len(re.sub(r"\s+", "", main_text)) < 80:
        _decision_memo_add_finding(
            findings,
            "appendix_missing_or_unseparated",
            "Main memo before the appendix is too short to be clearly separated.",
        )


def _decision_memo_check_duplicate_headings(
    findings: list[DecisionMemoQualityFinding],
    entries: list[dict[str, Any]],
) -> None:
    counts: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        key = entry.get("key", "")
        if key:
            counts.setdefault(key, []).append(entry)
    for key, duplicate_entries in counts.items():
        if len(duplicate_entries) > 1:
            _decision_memo_add_finding(
                findings,
                "duplicate_heading",
                f"Heading appears {len(duplicate_entries)} times: {duplicate_entries[0].get('title')}.",
                location=", ".join(f"line {entry.get('line_number')}" for entry in duplicate_entries),
                excerpt=duplicate_entries[0].get("title", ""),
            )


def _decision_memo_check_empty_headings(
    findings: list[DecisionMemoQualityFinding],
    entries: list[dict[str, Any]],
) -> None:
    for entry in entries:
        if not str(entry.get("title") or "").strip():
            _decision_memo_add_finding(
                findings,
                "empty_heading",
                "Markdown heading has no title.",
                location=f"line {entry.get('line_number')}",
            )
            continue
        body = str(entry.get("body") or "").strip()
        if not body:
            _decision_memo_add_finding(
                findings,
                "empty_heading",
                "Markdown heading has no visible content before the next heading.",
                location=f"line {entry.get('line_number')}",
                excerpt=entry.get("title", ""),
            )


def _decision_memo_check_markdown_tables(findings: list[DecisionMemoQualityFinding], report: str) -> None:
    lines = str(report or "").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if not _decision_memo_table_line(line):
            index += 1
            continue
        start = index
        block: list[str] = []
        while index < len(lines) and _decision_memo_table_line(lines[index]):
            block.append(lines[index])
            index += 1
        if len(block) < 2 or not _decision_memo_separator_row(block[1]):
            _decision_memo_add_finding(
                findings,
                "malformed_markdown_table",
                "Markdown table block is missing a separator row immediately after the header.",
                location=f"line {start + 1}",
                excerpt="\n".join(block[:3]),
            )
            continue
        widths = [_decision_memo_table_width(row) for row in block if not _decision_memo_separator_row(row)]
        if widths and len(set(widths)) > 1:
            _decision_memo_add_finding(
                findings,
                "malformed_markdown_table",
                "Markdown table rows have inconsistent cell counts.",
                location=f"line {start + 1}",
                excerpt="\n".join(block[:4]),
            )


def _decision_memo_table_line(line: str) -> bool:
    stripped = str(line or "").strip()
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2


def _decision_memo_separator_row(line: str) -> bool:
    cells = [cell.strip() for cell in str(line or "").strip().strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells)


def _decision_memo_table_width(line: str) -> int:
    return len(str(line or "").strip().strip("|").split("|"))


def _decision_memo_check_truncation(findings: list[DecisionMemoQualityFinding], report: str) -> None:
    lines = [line.strip() for line in str(report or "").splitlines() if line.strip()]
    if not lines:
        return
    last = lines[-1]
    if last.startswith(("#", "-", "*", "|")) or re.match(r"^\d+[.)]\s+", last):
        return
    word_count = len(re.findall(r"\b\w+\b", last))
    abrupt_tail = re.search(r"[,;:]$|\b(?:and|or|but|because|with|para|con|y|o|porque|que)$", last, re.I)
    complete_punctuation = re.search(r"[.!?)]$", last)
    if abrupt_tail or (word_count >= 8 and not complete_punctuation):
        _decision_memo_add_finding(
            findings,
            "likely_incomplete_final_sentence",
            "Final sentence may be incomplete or abruptly truncated.",
            location="final line",
            excerpt=last,
        )


def _decision_memo_check_contradictory_counts(
    findings: list[DecisionMemoQualityFinding],
    main_text: str,
) -> None:
    lines = str(main_text or "").splitlines()
    count_pattern = re.compile(
        r"\b(?P<count>\d+|one|two|three|four|five|six|seven|eight|nine|ten|"
        r"uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\s+"
        r"(?P<noun>options?|alternatives?|actions?|experiments?|signals?|thresholds?|"
        r"opciones|alternativas|acciones|experimentos|señales|umbrales)\b",
        re.I,
    )
    for index, line in enumerate(lines):
        match = count_pattern.search(line)
        if not match:
            continue
        stated = _decision_memo_count_value(match.group("count"))
        if stated is None:
            continue
        actual = _decision_memo_following_enumeration_count(lines, index + 1)
        if actual > 0 and actual != stated:
            _decision_memo_add_finding(
                findings,
                "contradictory_option_count",
                f"Text states {stated} {match.group('noun')} but the immediately following enumeration has {actual}.",
                location=f"line {index + 1}",
                excerpt=line,
            )


def _decision_memo_count_value(value: str) -> int | None:
    text = str(value or "").lower()
    words = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "uno": 1,
        "dos": 2,
        "tres": 3,
        "cuatro": 4,
        "cinco": 5,
        "seis": 6,
        "siete": 7,
        "ocho": 8,
        "nueve": 9,
        "diez": 10,
    }
    if text.isdigit():
        return int(text)
    return words.get(text)


def _decision_memo_following_enumeration_count(lines: list[str], start_index: int) -> int:
    nonempty: list[str] = []
    for line in lines[start_index: start_index + 12]:
        if line.strip():
            nonempty.append(line)
        elif nonempty:
            break
    if not nonempty:
        return 0
    if _decision_memo_table_line(nonempty[0]):
        return sum(
            1
            for row in nonempty[2:]
            if _decision_memo_table_line(row) and not _decision_memo_separator_row(row)
        )
    count = 0
    for line in nonempty:
        if re.match(r"^\s*(?:[-*]|\d+[.)])\s+", line):
            count += 1
        elif count:
            break
    return count


def _decision_memo_check_unsupported_numeric_claims(
    findings: list[DecisionMemoQualityFinding],
    main_text: str,
    exact_markers: set[str],
    output_language: str,
) -> None:
    for line_number, line in _decision_memo_review_lines(main_text):
        if not _decision_memo_numeric_claim_pattern(line):
            continue
        if _decision_memo_line_has_any_label(line, _decision_memo_operator_fact_labels(output_language)):
            continue
        if _decision_memo_line_has_any_label(line, _decision_memo_proposed_threshold_labels(output_language)):
            continue
        if _decision_memo_line_has_exact_marker(line, exact_markers):
            continue
        _decision_memo_add_finding(
            findings,
            "unsupported_numeric_claim",
            "Main memo contains numeric precision or diagnostic scoring without an operator fact, proposed threshold, or concrete source locator label.",
            location=f"line {line_number}",
            excerpt=line,
        )


def _decision_memo_numeric_claim_pattern(line: str) -> bool:
    text = str(line or "")
    if re.search(r"\b(?:BF|DQ|RPN|rho|H_norm|FMEA|SQI|p\s*=|confidence interval|correlation|probability|probabilidad)\b|ρ", text, re.I):
        return True
    if re.search(r"\$\s*\d|\b\d+(?:\.\d+)?\s*(?:%|pp|x)\b", text, re.I):
        return True
    if _decision_memo_has_unsupported_numeric_range(text):
        return True
    if re.search(r"\b(?:forecast|projection|expected|predicted|precise|pronóstico|proyección|esperado)\b.{0,50}\b\d", text, re.I):
        return True
    return False


def _decision_memo_has_unsupported_numeric_range(text: str) -> bool:
    iso_spans = [
        match.span()
        for match in re.finditer(r"\b\d{4}-\d{2}-\d{2}(?:T\d{6}Z)?\b", text, re.I)
    ]
    range_pattern = re.compile(
        r"\b\d+(?:\.\d+)?\s*(?:to|-|–)\s*\d+(?:\.\d+)?\s*"
        r"(?P<unit>%|pp|usd|mxn|dollars?|pesos?)?\b",
        re.I,
    )
    for match in range_pattern.finditer(text):
        if any(_decision_memo_spans_overlap(match.span(), span) for span in iso_spans):
            continue
        if _decision_memo_numeric_range_is_roadmap_timing(text, match):
            continue
        if match.group("unit"):
            return True
        if _decision_memo_numeric_range_has_sensitive_context(text, match):
            return True
    return False


def _decision_memo_spans_overlap(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def _decision_memo_numeric_range_is_roadmap_timing(text: str, match: re.Match[str]) -> bool:
    prefix = text[max(0, match.start() - 24):match.start()]
    suffix = text[match.end():match.end() + 24]
    return bool(
        re.search(r"\b(?:day|days|día|días)\s*$", prefix, re.I)
        or re.match(r"\s*(?:day|days|día|días)\b", suffix, re.I)
    )


def _decision_memo_numeric_range_has_sensitive_context(text: str, match: re.Match[str]) -> bool:
    context = text[max(0, match.start() - 60):match.end() + 60]
    return bool(
        re.search(
            r"\b(?:price|pricing|cost|budget|spend|forecast|projection|predicted|probability|"
            r"probabilidad|precio|costo|presupuesto|gasto|pronóstico|proyección|esperado|"
            r"usd|mxn|dollars?|pesos?)\b|\$",
            context,
            re.I,
        )
    )


def _decision_memo_check_source_supported_locators(
    findings: list[DecisionMemoQualityFinding],
    main_text: str,
    exact_markers: set[str],
    output_language: str,
) -> None:
    labels = _decision_memo_source_supported_labels(output_language)
    for line_number, line in _decision_memo_review_lines(main_text):
        if not _decision_memo_line_has_any_label(line, labels):
            continue
        if _decision_memo_line_has_exact_marker(line, exact_markers):
            continue
        _decision_memo_add_finding(
            findings,
            "source_supported_without_concrete_locator",
            "Claim is labeled source-supported but does not include a concrete locator resolved against the existing locator register.",
            location=f"line {line_number}",
            excerpt=line,
        )


def _decision_memo_check_evidence_certainty_mismatch(
    findings: list[DecisionMemoQualityFinding],
    main_text: str,
    sections: dict[str, dict[str, Any]],
    output_language: str,
) -> None:
    maturity_key = _decision_memo_heading_key("Madurez de la evidencia" if output_language == "es-MX" else "Evidence maturity")
    maturity_text = str((sections.get(maturity_key) or {}).get("body") or "")
    low_maturity = bool(
        re.search(
            r"\b(hypothesis-only|partial|weak|unavailable|sparse|limited|not supplied|"
            r"hip[oó]tesis|parcial|d[eé]bil|no disponible|limitada|no proporcionad[ao])\b",
            maturity_text,
            re.I,
        )
    )
    if not low_maturity:
        return
    for line_number, line in _decision_memo_review_lines(main_text):
        if _decision_memo_certainty_wording(line) and not _decision_memo_certainty_negated(line):
            _decision_memo_add_finding(
                findings,
                "evidence_maturity_certainty_mismatch",
                "Evidence maturity is weak or partial, but the main memo uses certainty wording.",
                location=f"line {line_number}",
                excerpt=line,
            )


def _decision_memo_certainty_wording(line: str) -> bool:
    return bool(
        re.search(
            r"\b(confirmed|proven|guaranteed|certain|validated|established|definitive|"
            r"confirmado|comprobado|garantizado|cierto|validado|establecido|definitivo)\b",
            str(line or ""),
            re.I,
        )
    )


def _decision_memo_certainty_negated(line: str) -> bool:
    return bool(
        re.search(
            r"\b(not|no|sin|un|not yet|no est[aá]|a[uú]n no|pending|pendiente)\b.{0,30}"
            r"\b(confirmed|proven|guaranteed|certain|validated|established|"
            r"confirmado|comprobado|garantizado|cierto|validado|establecido)\b",
            str(line or ""),
            re.I,
        )
    )


def _decision_memo_check_heading_language(
    findings: list[DecisionMemoQualityFinding],
    entries: list[dict[str, Any]],
    output_language: str,
) -> None:
    present_keys = {_decision_memo_language_heading_key(str(entry.get("title", ""))) for entry in entries}
    wrong_language = [
        heading for heading in _decision_memo_other_language_headings(output_language)
        if _decision_memo_language_heading_key(heading) in present_keys
    ]
    if wrong_language:
        _decision_memo_add_finding(
            findings,
            "heading_language_mismatch",
            f"Report output language is {output_language}, but headings from another required heading set are present.",
            excerpt=", ".join(wrong_language[:4]),
        )


def _decision_memo_language_heading_key(value: str) -> str:
    return re.sub(r"[^\w/]+", " ", str(value or "").casefold(), flags=re.UNICODE).strip()


def _decision_memo_check_monitoring_rows(
    findings: list[DecisionMemoQualityFinding],
    state: Any,
    sections: dict[str, dict[str, Any]],
    output_language: str,
) -> None:
    try:
        from monitoring_templates import build_monitoring_template_rows
    except Exception:
        return
    try:
        rows = build_monitoring_template_rows(state)
    except Exception:
        return
    metrics = [
        _decision_memo_text(row.metric_signal)
        for row in rows
        if _decision_memo_monitoring_metric_is_visible(_decision_memo_text(row.metric_signal))
    ]
    if not metrics:
        return
    section_heading = "Señales de monitoreo" if output_language == "es-MX" else "Monitoring signals"
    section = sections.get(_decision_memo_heading_key(section_heading))
    section_text = _decision_memo_text((section or {}).get("body", ""))
    if not section_text:
        return
    normalized_section = _decision_memo_normalized_text(section_text)
    if any(_decision_memo_normalized_text(metric) in normalized_section for metric in metrics):
        return
    _decision_memo_add_finding(
        findings,
        "monitoring_signals_template_mismatch",
        "Monitoring signals section does not appear to reference any visible rows from the generated monitoring template.",
        location=section_heading,
        excerpt=", ".join(metrics[:4]),
    )


def _decision_memo_monitoring_metric_is_visible(value: str) -> bool:
    normalized = _decision_memo_normalized_text(value)
    return normalized not in {
        "",
        "operator to define",
        "to be confirmed",
        "not supplied",
        "monitoring template placeholder",
    }


def _decision_memo_review_lines(text: str) -> list[tuple[int, str]]:
    rows: list[tuple[int, str]] = []
    in_code = False
    for line_number, raw_line in enumerate(str(text or "").splitlines(), start=1):
        line = raw_line.strip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not line:
            continue
        if line.startswith("#") or _decision_memo_separator_row(line):
            continue
        rows.append((line_number, line))
    return rows


def _decision_memo_line_has_any_label(line: str, labels: tuple[str, ...]) -> bool:
    normalized = _decision_memo_normalized_text(line)
    return any(_decision_memo_normalized_text(label) in normalized for label in labels)


def _decision_memo_line_has_exact_marker(line: str, exact_markers: set[str]) -> bool:
    if not exact_markers:
        return False
    return any(marker and marker in line for marker in exact_markers)


def _decision_memo_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _decision_memo_normalized_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", ascii_text.lower()).strip()


def threshold_consistency_warnings(state: Any, context: ReportQualityContext | None = None) -> list[str]:
    context = context or assess_report_quality_context(state)
    warnings: list[str] = []
    threshold_warning = threshold_conflict_warning(state, context)
    if threshold_warning:
        warnings.append(threshold_warning)
    text = _combined_state_text(state)
    if _has_high_confidence_language(text) and context.sparse_evidence:
        warnings.append(SPARSE_CONFIDENCE_RULE)
    if _has_exact_dollar_estimate(text) and not context.has_budget_or_spend_evidence:
        warnings.append("Exact dollar estimates appear without budget or spend evidence; treat them as placeholders until Sprint 0 validates cost data.")
    monitor = getattr(state, "monitor", None)
    if monitor and PLACEHOLDER_RATIONALE_PATTERN.match(getattr(monitor, "commitment_rationale", "") or ""):
        warnings.append("Commitment score is unconfirmed; operator confirmation is required before treating it as a score.")
    return _unique(warnings)


def threshold_conflict_warning(state: Any, context: ReportQualityContext | None = None) -> str:
    context = context or assess_report_quality_context(state)
    report = str(getattr(state, "report", "") or "")
    sections = _markdown_sections(report)
    classifications = threshold_section_classification(state, context)
    primary_sections = [
        classification for classification in classifications
        if classification.threshold_like and classification.classification == "primary"
    ]
    decision_gate_count = sum(1 for section in primary_sections if _normalize_heading(section.section_name) == "decision gates")
    projected_sparse_growth_gate = context.sparse_evidence and context.decision_domain == "growth" and decision_gate_count == 0

    if decision_gate_count == 1:
        non_gate_primary = [
            section for section in primary_sections
            if _normalize_heading(section.section_name) != "decision gates"
        ]
        if not non_gate_primary:
            return ""
        source_of_truth = "Decision Gates"
        if len(non_gate_primary) == 1:
            return THRESHOLD_CONFLICT_BETWEEN_TEMPLATE.format(
                section_a=source_of_truth,
                section_b=non_gate_primary[0].section_name,
            )
        if len(non_gate_primary) >= 2:
            return THRESHOLD_CONFLICT_BETWEEN_TEMPLATE.format(
                section_a=non_gate_primary[0].section_name,
                section_b=non_gate_primary[1].section_name,
            )
        return THRESHOLD_CONFLICT_UNKNOWN_WARNING

    if projected_sparse_growth_gate and primary_sections:
        return THRESHOLD_CONFLICT_BETWEEN_TEMPLATE.format(
            section_a="projected Decision Gates",
            section_b=primary_sections[0].section_name,
        )

    if len(primary_sections) >= 2:
        return THRESHOLD_CONFLICT_BETWEEN_TEMPLATE.format(
            section_a=primary_sections[0].section_name,
            section_b=primary_sections[1].section_name,
        )

    text = _strip_markdown_heading_sections(report) if sections else (report or _combined_state_text(state))
    if _has_conflicting_rho_thresholds(text) or _has_conflicting_canary_thresholds(text):
        return THRESHOLD_CONFLICT_UNKNOWN_WARNING
    return ""


def threshold_section_classification(
    state: Any,
    context: ReportQualityContext | None = None,
) -> tuple[ThresholdSectionClassification, ...]:
    """Classify report sections for threshold warning/debug output.

    This is intentionally section-name only. It never returns report excerpts,
    uploaded source text, storage refs, or provider payloads.
    """
    report = str(getattr(state, "report", "") or "")
    sections = _markdown_sections(report)
    classifications: list[ThresholdSectionClassification] = []
    preamble = _markdown_preamble(report)
    if preamble.strip() and _section_has_threshold_content(preamble):
        classifications.append(
            ThresholdSectionClassification(
                section_name="Unheaded report text",
                classification="unheaded",
                reason="Threshold-like content appears outside a markdown heading.",
                threshold_like=True,
            )
        )
    for heading, body in sections:
        classifications.append(_classify_threshold_section(heading, body))
    return tuple(classifications)


def _classify_threshold_section(heading: str, body: str) -> ThresholdSectionClassification:
    threshold_like = _section_has_threshold_content(body)
    if not threshold_like:
        return ThresholdSectionClassification(
            section_name=str(heading or "Untitled section").strip() or "Untitled section",
            classification="ignored",
            reason="No threshold-like decision-control content detected.",
            threshold_like=False,
        )
    if _is_subordinate_threshold_section(heading):
        return ThresholdSectionClassification(
            section_name=str(heading or "Untitled section").strip() or "Untitled section",
            classification="subordinate",
            reason="Status/control section; does not define a competing decision system.",
            threshold_like=True,
        )
    if _is_primary_threshold_section(heading):
        return ThresholdSectionClassification(
            section_name=str(heading or "Untitled section").strip() or "Untitled section",
            classification="primary",
            reason="Primary gate, criteria, threshold, or decision-matrix section.",
            threshold_like=True,
        )
    return ThresholdSectionClassification(
        section_name=str(heading or "Untitled section").strip() or "Untitled section",
        classification="ignored",
        reason="Threshold-like content is under a non-gate narrative heading.",
        threshold_like=True,
    )


def _strip_decision_gates_sections(text: str) -> str:
    return re.sub(
        r"(?ims)^#{1,6}\s+Decision Gates\s*$.*?(?=^#{1,6}\s+|\Z)",
        "",
        str(text or ""),
    )


def _markdown_sections(markdown: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    current_heading = ""
    current_lines: list[str] = []
    for line in str(markdown or "").splitlines():
        heading = re.match(r"^#{1,6}\s+(.+?)\s*$", line.strip())
        if heading:
            if current_heading:
                sections.append((current_heading, "\n".join(current_lines)))
            current_heading = heading.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_heading:
        sections.append((current_heading, "\n".join(current_lines)))
    return sections


def _markdown_preamble(markdown: str) -> str:
    match = re.search(r"(?m)^#{1,6}\s+", str(markdown or ""))
    return str(markdown or "")[: match.start()] if match else str(markdown or "")


def _section_has_threshold_content(value: str) -> bool:
    text = str(value or "")
    return bool(
        re.search(r"\b(threshold|gate|gates|proceed|extend|stop|escalate|kill criteria|circuit breaker|canary)\b", text, re.I)
        and re.search(r"([<>≥≤]\s*\d|\b\d+(?:\.\d+)?\s*%|\b\d+(?:\.\d+)?\s*/\s*\d+|\bBF\b|\bDQ\b|\bD90\b|\bNRR\b|\bCAC\b)", text, re.I)
    )


def _is_primary_threshold_section(heading: str) -> bool:
    normalized = _normalize_heading(heading)
    if _is_subordinate_threshold_section(heading):
        return False
    if normalized in PRIMARY_THRESHOLD_SECTION_NAMES:
        return True
    return bool(
        re.search(r"\b(thresholds?|decision|gates?|matrix|criteria)\b", normalized)
    )


def _is_subordinate_threshold_section(heading: str) -> bool:
    normalized = _normalize_heading(heading)
    return normalized in SUBORDINATE_THRESHOLD_SECTION_NAMES


def _normalize_heading(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _strip_subordinate_threshold_sections(text: str) -> str:
    source = str(text or "")
    for heading in (
        "Monitoring",
        "Monitoring Details",
        "Monitoring and Kill Criteria",
        "Governance fallback",
        "Governance fallback if leadership overrides the diagnostic hold",
        "Spend Gate",
        "Spend Gate Owner and Enforcement",
        "What the team may do during Sprint 0",
        "What the team may not do during Sprint 0",
        "Sprint 0 Controls",
        "Sprint 0 allowed controls",
        "Sprint 0 not allowed controls",
        "Sprint 0 Evidence Pack Required",
        "Minimum staffing assumption",
        "Main limitation of this recommendation",
        "Operator Controls",
        "Roadmap",
        "Key Risks",
        "Early Warning Signal",
        "Early Warning Signals",
        "Mitigation",
        "Stop / Change-Course Threshold",
        "Stop / Change-Course Thresholds",
        "Stop / Change-Course",
        "Stop and Change Course",
        "Canaries",
        "Circuit breakers",
        "Technical Appendix",
        "Appendix: Technical Analysis",
        "Framework References",
        "Convergence Gate Status",
        "Report appendix",
    ):
        source = re.sub(
            rf"(?ims)^#{{1,6}}\s+{re.escape(heading)}\s*$.*?(?=^#{{1,6}}\s+|\Z)",
            "",
            source,
        )
    return source


def _strip_markdown_heading_sections(text: str) -> str:
    return re.sub(r"(?ims)^#{1,6}\s+.+?\s*$.*?(?=^#{1,6}\s+|\Z)", "", str(text or ""))


def _has_conflicting_rho_thresholds(text: str) -> bool:
    values = [float(match.group(1)) for match in re.finditer(r"(?:ρ|rho|correlation)[^\n]{0,40}?([01](?:\.\d+)?)", text or "", re.I)]
    return len({round(value, 2) for value in values}) > 1


def _has_conflicting_canary_thresholds(text: str) -> bool:
    canary_lines = [line for line in (text or "").splitlines() if re.search(r"\b(canary|success|threshold|stop|trip)\b", line, re.I)]
    ratios = set()
    percents = set()
    for line in canary_lines:
        for num, denom in re.findall(r"([<>]?\s*\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", line):
            try:
                ratios.add(round(float(re.sub(r"[<>\s]", "", num)) / float(denom), 3))
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        for percent in re.findall(r"([<>]?\s*\d+(?:\.\d+)?)\s*%", line):
            try:
                percents.add(round(float(re.sub(r"[<>\s]", "", percent)) / 100.0, 3))
            except (TypeError, ValueError):
                pass
    all_values = ratios | percents
    return len(all_values) > 1


def _has_high_confidence_language(text: str) -> bool:
    return bool(re.search(r"\b(high confidence|very confident|strong confidence|confidently recommend)\b", text or "", re.I))


def _has_exact_dollar_estimate(text: str) -> bool:
    return bool(re.search(r"\$\s*\d[\d,]*(?:\.\d+)?", text or ""))


def _uploaded_files(state: Any) -> list[Any]:
    layer = getattr(state, "knowledge_layer", None)
    return list(getattr(layer, "uploaded_files", []) or [])


def _parsed_file_display_names(uploaded_files: list[Any]) -> list[str]:
    names: list[str] = []
    for manifest in uploaded_files:
        parse_summary = _field_get(manifest, "parse_summary")
        status = _status_text(_field_get(parse_summary, "status"))
        if status and status not in {"completed", "complete"}:
            continue
        name = _safe_display_file_name(_field_get(manifest, "filename"))
        if name:
            names.append(name)
    return _unique_file_names(names)


def _explicit_missing_evidence_file_names(state: Any, parsed_names: list[str]) -> list[str]:
    parsed_lookup = {_filename_key(name) for name in parsed_names}
    expected = _explicit_expected_file_names(state)
    return [
        name for name in expected
        if _filename_key(name) and _filename_key(name) not in parsed_lookup
    ]


def _explicit_expected_file_names(state: Any) -> list[str]:
    names: list[str] = []
    for field_name in ("expected_evidence_files", "expected_files"):
        names.extend(_file_names_from_value(_field_get(state, field_name)))
    uploaded_manifest = _field_get(state, "uploaded_manifest")
    for row in _iter_maybe(uploaded_manifest):
        status = _status_text(_field_get(row, "status") or _field_get(row, "parse_status"))
        if status in {"expected", "missing", "rejected", "skipped", "unsupported", "failed"}:
            names.extend(_file_names_from_value(row))
    names.extend(_explicit_file_names_in_brief(getattr(state, "brief", "")))
    return _unique_file_names(names)


def _explicit_rejected_file_display_names(state: Any) -> list[str]:
    names: list[str] = []
    for field_name in ("rejected_files", "skipped_files", "unsupported_files"):
        names.extend(_file_names_from_value(_field_get(state, field_name)))
    uploaded_manifest = _field_get(state, "uploaded_manifest")
    for row in _iter_maybe(uploaded_manifest):
        status = _status_text(_field_get(row, "status") or _field_get(row, "parse_status"))
        if status in {"rejected", "skipped", "unsupported", "failed"}:
            names.extend(_file_names_from_value(row))
    return _unique_file_names(names)


def _explicit_file_names_in_brief(value: Any) -> list[str]:
    names: list[str] = []
    for line in str(value or "").splitlines():
        if not re.search(r"\b(expected|evidence|uploaded|manifest|missing|skipped|rejected|source\s+files?|files?)\b", line, re.I):
            continue
        names.extend(_file_name_matches(line))
    return _unique_file_names(names)


def _file_names_from_value(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, dict):
        names: list[str] = []
        for key in ("filename", "file_name", "original_filename", "display_name", "name", "path"):
            names.extend(_file_names_from_value(value.get(key)))
        for item in value.values():
            if isinstance(item, (dict, list, tuple, set)):
                names.extend(_file_names_from_value(item))
        return _unique_file_names(names)
    if isinstance(value, (list, tuple, set)):
        names: list[str] = []
        for item in value:
            names.extend(_file_names_from_value(item))
        return _unique_file_names(names)
    for key in ("filename", "file_name", "original_filename", "display_name", "name", "path"):
        attr = getattr(value, key, None)
        if attr not in (None, ""):
            return _file_names_from_value(attr)
    return _file_name_matches(str(value))


def _file_name_matches(value: str) -> list[str]:
    matches = re.findall(
        r"(?<![A-Za-z0-9_.-])([A-Za-z0-9][A-Za-z0-9_.() -]*\.(?:json|pdf|docx|txt|md|csv|xlsx))(?![A-Za-z0-9_.-])",
        str(value or ""),
        flags=re.I,
    )
    return _unique_file_names(_safe_display_file_name(match) for match in matches)


def _safe_display_file_name(value: Any) -> str:
    text = str(value or "").strip().strip("'\"")
    if not text:
        return ""
    text = re.split(r"[\\/]", text)[-1]
    text = re.sub(r"[^A-Za-z0-9_.() -]+", "", text).strip(" .")
    if not re.search(r"\.(?:json|pdf|docx|txt|md|csv|xlsx)$", text, re.I):
        return ""
    return text


def _filename_key(value: str) -> str:
    return _safe_display_file_name(value).lower()


def _unique_file_names(values: Any) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values or []:
        name = _safe_display_file_name(value)
        key = name.lower()
        if name and key not in seen:
            seen.add(key)
            output.append(name)
    return output


def _status_text(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip().lower()


def _field_get(item: Any, field_name: str, default: Any = None) -> Any:
    if item is None:
        return default
    if isinstance(item, dict):
        return item.get(field_name, default)
    return getattr(item, field_name, default)


def _iter_maybe(value: Any) -> list[Any]:
    if value is None or isinstance(value, (str, bytes)):
        return []
    if isinstance(value, dict):
        return list(value.values())
    try:
        return list(value)
    except TypeError:
        return []


def _citation_marker_counts(state: Any) -> tuple[int, int]:
    try:
        from cdp.citation_resolvability import build_defense_pass_result

        result = build_defense_pass_result(state)
    except Exception:
        return 0, 0
    summary = getattr(result, "summary_counts", {}) or {}
    marker_count = int(summary.get("canonical_marker_count", 0) or len(getattr(result, "markers", []) or []))
    resolved_count = int(summary.get("resolved_exact", 0) or 0) + int(summary.get("resolved_id_only", 0) or 0)
    return marker_count, resolved_count


def _concrete_source_locator_count(state: Any) -> int:
    try:
        from cdp.citation_resolvability import build_evidence_locator_registry

        entries = build_evidence_locator_registry(state)
    except Exception:
        entries = []
    count = 0
    for entry in entries:
        locators = list(getattr(entry, "locators", []) or [])
        if any(_is_concrete_locator(locator) for locator in locators):
            count += 1
    return count


def _generic_evidence_categories_look_incomplete(state: Any) -> bool:
    accounting_text = _domain_source_text(state)
    domain = infer_decision_domain(accounting_text)
    if domain == "general_business":
        return False
    categories = evidence_categories_for_domain(domain, accounting_text)
    if not any(re.search(re.escape(category.split(" / ")[0]), accounting_text, re.I) for category in categories):
        return False
    uploaded_count = len(_uploaded_files(state))
    imported_evidence_count = len(getattr(state, "imported_evidence", []) or [])
    imported_signal_count = len(getattr(state, "imported_signals", []) or [])
    return uploaded_count > 0 and (imported_evidence_count == 0 or imported_signal_count == 0)


def _domain_source_text(state: Any) -> str:
    """Operator-supplied or explicit classification text only.

    Generated reports, model phase outputs, and model-created evidence lists are
    intentionally excluded so stale SEO/CMS wording cannot reclassify a generic
    growth or product decision.
    """
    parts = [
        getattr(state, "project_name", ""),
        getattr(state, "brief", ""),
        getattr(state, "data", ""),
        getattr(state, "risk_classification", ""),
        getattr(state, "risk_classification_rationale", ""),
    ]
    classify = getattr(state, "classify", None)
    if classify:
        parts.extend([getattr(classify, "domain", ""), getattr(classify, "reference_class", "")])
    return "\n".join(str(part) for part in parts if part is not None)


def _combined_state_text(state: Any) -> str:
    parts = [
        getattr(state, "brief", ""),
        getattr(state, "data", ""),
        getattr(state, "report", ""),
    ]
    classify = getattr(state, "classify", None)
    if classify:
        parts.extend([getattr(classify, "domain", ""), getattr(classify, "justification", "")])
    strategy = getattr(state, "strategy", None)
    if strategy:
        parts.extend([
            getattr(strategy, "executive_strategy", ""),
            getattr(strategy, "implementation_sequence", ""),
            getattr(strategy, "monitoring_plan", ""),
            getattr(strategy, "confidence", ""),
        ])
        for action in list(getattr(strategy, "strategies", []) or []):
            parts.extend([
                getattr(action, "action", ""),
                getattr(action, "justification", ""),
                getattr(action, "expected_impact", ""),
                getattr(action, "risk_if_ignored", ""),
            ])
    audit = getattr(state, "audit", None)
    if audit:
        parts.extend(list(getattr(audit, "top_findings", []) or []))
        parts.extend(list(getattr(audit, "observation_needs", []) or []))
    monitor = getattr(state, "monitor", None)
    if monitor:
        parts.append(getattr(monitor, "commitment_rationale", ""))
        parts.extend(monitor_success_metric_lines(monitor, limit=50))
    for evidence in list(getattr(state, "imported_evidence", []) or []):
        parts.extend([getattr(evidence, "title", ""), getattr(evidence, "summary", ""), getattr(evidence, "category", "")])
    layer = getattr(state, "knowledge_layer", None)
    for item in list(getattr(layer, "items", []) or []):
        parts.extend([getattr(item, "title", ""), getattr(item, "summary", ""), getattr(item, "source_ref", "")])
    return "\n".join(str(part) for part in parts if part is not None)


def _replace_hypothesis_ids(value: str) -> str:
    labels = {
        "1": "user-value hypothesis",
        "2": "architecture hypothesis",
        "3": "scope-risk hypothesis",
    }

    def repl(match: re.Match[str]) -> str:
        number = match.group(1)
        return labels.get(number, f"hypothesis {number}")

    return re.sub(r"\bH(\d+)\b", repl, value)


def _simplify_sparse_precision(value: str) -> str:
    lines: list[str] = []
    for line in str(value or "").splitlines():
        if _is_provisional_planning_gate(line) and not re.search(
            r"\b(probability|scenario[_ -]?probability|structural probability|prior|likelihood|chance|failure probability|BF|DQ|RPN|FMEA|Jaccard|Brier|ECE|rho|correlation|portfolio correlation)\b|ρ",
            line,
            re.I,
        ):
            lines.append(line)
            continue
        simplified = line
        if re.search(r"\b(canary|threshold|stop|trip|warning sign|good sign|kill criteria|monitoring)\b", simplified, re.I):
            simplified = re.sub(r"\b\d+(?:\.\d+)?\s*%", "provisional threshold", simplified)
            simplified = re.sub(r"\b\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?\b", "provisional count threshold", simplified)
        simplified = re.sub(
            r"\b(?:failure probability|predicted failure probability)\s*(?:of|=|:)?\s*(?:0\.\d+|\d{1,3}\s*%)",
            "high provisional failure risk",
            simplified,
            flags=re.I,
        )
        simplified = re.sub(
            r"\b(?:probability|prior|likelihood|chance)\s*(?:of|=|:)?\s*\d{1,3}\s*%",
            "structural prior",
            simplified,
            flags=re.I,
        )
        simplified = re.sub(
            r"\b(?:scenario[_ -]?probability|structural probability|probability|prior|likelihood|chance)\s*(?:of|=|:)?\s*(?:0(?:\.\d+)?|1(?:\.0+)?)\b",
            "structural prior",
            simplified,
            flags=re.I,
        )
        simplified = re.sub(
            r"\b\d{1,3}\s*%\s*(?:scenario[_ -]?probability|structural probability|probability|prior|likelihood|chance)\b",
            "structural prior",
            simplified,
            flags=re.I,
        )
        simplified = re.sub(
            r"\b(?:0(?:\.\d+)?|1(?:\.0+)?)\s*(?:scenario[_ -]?probability|structural probability|probability|prior|likelihood|chance)\b",
            "structural prior",
            simplified,
            flags=re.I,
        )
        simplified = re.sub(
            r"\b(expected|projected|predicted)\s+\d{1,3}\s*%\s+(impact|lift|reduction|increase|improvement|conversion|roi)\b",
            r"provisional planning estimate for \2",
            simplified,
            flags=re.I,
        )
        simplified = re.sub(
            r"\$\s*\d[\d,]*(?:\.\d+)?",
            "provisional planning estimate",
            simplified,
        )
        simplified = re.sub(
            r"\b\d+(?:\.\d+)?\s*person[- ]hours?\b",
            "provisional effort estimate",
            simplified,
            flags=re.I,
        )
        lines.append(simplified)
    return "\n".join(lines)


def _is_provisional_planning_gate(line: str) -> bool:
    return bool(re.search(r"\b(proposed|provisional)\b.*\b(planning gate|gate|threshold)\b", line or "", re.I))


def _cleanup_client_replacement_artifacts(value: str) -> str:
    protected, fragments = _protect_export_fragments(str(value or ""))
    value = protected
    phrases = [
        "internal confidence diagnostic",
        "evidence quality diagnostic",
        "uncertainty diagnostic",
        "structured risk priority",
        "model-generated prior",
        "structural confidence signal",
        "evidence quality signal",
        "uncertainty signal",
        "structured risk review",
        "risk priority score",
        "related-hypothesis risk",
        "schema overlap score",
        "forecast accuracy check",
        "calibration check",
        "structural prior",
        "high provisional failure risk",
        "provisional risk estimate",
        "provisional threshold",
    ]
    for phrase in phrases:
        escaped = re.escape(phrase)
        value = re.sub(rf"({escaped})(?=[A-Za-z0-9])", r"\1 ", value, flags=re.I)
        value = re.sub(rf"(?:{escaped}\s*){{2,}}", f"{phrase} ", value, flags=re.I)
    value = re.sub(r"[ \t]{2,}", " ", value)
    value = re.sub(r"\s+([,.;:])", r"\1", value)
    value = re.sub(r"([,.;:])(?=\S)", r"\1 ", value)
    return _restore_export_fragments(value, fragments)


def _normalize_export_line(line: str, audience: str) -> str:
    if _line_has_protected_export_reference(line) or re.search(r"https?://|file://|\b[A-Za-z]:[\\/]|\\\\", str(line or "")):
        return str(line or "")
    protected, fragments = _protect_export_fragments(str(line or ""))
    value = _normalize_common_export_text(protected)
    if audience == "client":
        value = _normalize_client_export_text(value)
    else:
        value = _normalize_operator_export_text(value)
    return _restore_export_fragments(value, fragments)


def _normalize_common_export_text(value: str) -> str:
    text = str(value or "")
    reduced_sprint0 = re.match(
        r"^(\s*)([1-4])\s+(billing reconciliation|cohort retention|funnel conversion|10 churn/user interviews)\s*$",
        text,
        flags=re.I,
    )
    if reduced_sprint0:
        return f"{reduced_sprint0.group(1)}{reduced_sprint0.group(2)}. {reduced_sprint0.group(3)}"
    ordered_prefix = re.match(r"^(\s*\d+\.\s+)(.*)$", text)
    if ordered_prefix:
        return ordered_prefix.group(1) + _normalize_common_export_text(ordered_prefix.group(2))
    text = re.sub(r"(?<=\d)\.\s+(?=\d)", ".", text)
    text = re.sub(
        r"\btarget threshold:\s*>\s*provisional threshold\b",
        "target threshold: above the operator-defined threshold",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\btarget threshold:\s*<\s*provisional threshold\b",
        "target threshold: below the operator-defined threshold",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\btarget threshold\s*<\s*provisional threshold\b",
        "target threshold below the operator-defined threshold",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\bexceeds crux threshold by provisional threshold\b",
        "exceeds the crux threshold by the operator-defined margin",
        text,
        flags=re.I,
    )
    text = re.sub(r">\s*provisional threshold\b", "above the operator-defined threshold", text, flags=re.I)
    text = re.sub(r"<\s*provisional threshold\b", "below the operator-defined threshold", text, flags=re.I)
    text = re.sub(
        r"\bcrosses provisional threshold threshold\b",
        "crosses the operator-defined threshold",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\bprovisional planning estimateK/mo estimate\b",
        "operator-defined planning estimate",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\bprovisional threshold of new ARR\b",
        "the operator-defined share threshold of new ARR",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\babove provisional threshold of ([^.;,\n]+)",
        lambda match: f"above the operator-defined threshold for {match.group(1).strip()}",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\bbelow provisional threshold of ([^.;,\n]+)",
        lambda match: f"below the operator-defined threshold for {match.group(1).strip()}",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\bless than provisional threshold\s+[\"“]([^\"”]+)[\"”]",
        r'below the operator-defined threshold for "\1"',
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\bmore than provisional threshold\s+week[- ]over[- ]week\b",
        "above the operator-defined threshold week over week",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\bless than provisional threshold of the expected signal\b",
        "below the pre-registered interim threshold",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\bprovisional threshold of the planned run time\b",
        "halfway through the planned run time",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\bprovisional threshold of the provisional threshold\b",
        "pre-registered interim threshold",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\bthreshold of the expected signal after threshold of the planned run time\b",
        "expected signal at the planned interim review",
        text,
        flags=re.I,
    )
    text = re.sub(r"\bless than provisional threshold\b", "below the operator-defined threshold", text, flags=re.I)
    text = re.sub(r"\bmore than provisional threshold\b", "above the operator-defined threshold", text, flags=re.I)
    text = re.sub(r"\bprovisional effort estimate\b", "operator-defined effort estimate", text, flags=re.I)
    text = re.sub(r"\bstructural prior s\b", "structural priors", text, flags=re.I)
    text = re.sub(r"\bsystem blindness,\s+", "system blindness,", text, flags=re.I)
    text = re.sub(r"\breference-class prior s\b", "reference-class priors", text, flags=re.I)
    text = re.sub(r"\bmodel-generated prior s\b", "model-generated priors", text, flags=re.I)
    text = re.sub(r"\bdiagnostic score s\b", "diagnostic scores", text, flags=re.I)
    text = re.sub(r"\bprovisional risk estimate s\b", "provisional risk estimates", text, flags=re.I)
    text = re.sub(r"\bgreater than\s+greater than\b", "greater than", text, flags=re.I)
    text = re.sub(
        r"\boperator-confirmed threshold required prior probability\b",
        "unconfirmed model-generated prior probability",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\boperator-confirmed threshold required coverage threshold\b",
        "operator-confirmed coverage threshold",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\b60\s*[–-]\s*operator-confirmed threshold required coverage threshold\b",
        "60% operator-confirmed coverage threshold",
        text,
        flags=re.I,
    )
    text = re.sub(r"\bthreshold required threshold\b", "threshold", text, flags=re.I)
    text = re.sub(r"\bcoverage threshold threshold\b", "coverage threshold", text, flags=re.I)
    text = _normalize_comparator_phrasing(text)
    return text


def _normalize_client_export_text(value: str) -> str:
    text = str(value or "")
    replacements = [
        (r"\bmodel-generated prior probabilities\b", "structural priors"),
        (r"\bmodel-generated priors\b", "structural priors"),
        (r"\bmodel-generated prior probability\b", "structural prior"),
        (r"\bmodel-generated prior\b", "structural prior"),
        (r"\bunconfirmed structural prior probability\b", "structural prior"),
        (r"\bunconfirmed model-generated prior probability\b", "structural prior"),
        (r"\binternal confidence diagnostic\b", "structural confidence signal"),
        (r"\bevidence quality diagnostic\b", "evidence quality signal"),
        (r"\bstructured risk priority\b", "risk priority score"),
        (r"\boperator-confirmed threshold required\b", "provisional threshold"),
        (r"\bcitation unavailable\b", ""),
        (r"\bNo citation available\b", ""),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.I)
    text = re.sub(r"\bstructural prior(?:\s+structural prior)+\b", "structural prior", text, flags=re.I)
    text = re.sub(r"\bstructural prior\.\s*\d+\b", "structural prior", text, flags=re.I)
    text = re.sub(r"\brisk priority score\s+\d+(?:\.\d+)?\b", "risk priority score", text, flags=re.I)
    text = _normalize_client_legal_review_effort_placeholder(text)
    text = _normalize_client_operator_placeholder_language(text)
    return text


def _normalize_client_operator_placeholder_language(value: str) -> str:
    text = str(value or "")
    if _line_has_protected_export_reference(text) or re.search(r"https?://|file://|\b[A-Za-z]:[\\/]|\\\\", text):
        return text
    if _has_concrete_client_timing_value(text):
        return _strip_client_operator_placeholder_labels(text)
    replacements: list[tuple[str, str]] = [
        (
            r"\boperator-defined effort estimate\s+or\s+less\b",
            "planning estimate to validate in Sprint 0",
        ),
        (r"\boperator-defined effort estimate\b", "planning estimate to validate in Sprint 0"),
        (r"\boperator-defined planning estimate\b", "planning estimate to validate in Sprint 0"),
        (
            r"\boperator-defined share threshold of ([^.;,\n|]+)",
            r"\1 share threshold to validate in Sprint 0",
        ),
        (
            r"\boperator-defined threshold for ([^.;,\n|]+)",
            r"threshold for \1 to validate in Sprint 0",
        ),
        (
            r"\boperator-defined threshold week over week\b",
            "week-over-week threshold to validate in Sprint 0",
        ),
        (r"\boperator-defined margin\b", "margin to validate in Sprint 0"),
        (r"\boperator-defined threshold\b", "threshold to validate in Sprint 0"),
        (r"\boperator-defined\b", "to validate in Sprint 0"),
        (r"\bprovisional threshold\b", "threshold to validate in Sprint 0"),
        (r"\bprovisional planning estimate\b", "planning estimate to validate in Sprint 0"),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.I)
    return text


def _has_concrete_client_timing_value(value: str) -> bool:
    text = str(value or "")
    patterns = [
        r"\b\d+(?:\.\d+)?\s*(?:hours?|hrs?|days?|weeks?|months?)\b",
        r"\b\d+(?:\.\d+)?[- ](?:hour|day|week|month)\b",
        r"\b\d+(?:\.\d+)?[- ]day\s+rolling\b",
        r"\bday\s+\d+\b",
        r"\bwithin\s+\d+(?:\.\d+)?\s*(?:hours?|hrs?|days?|weeks?|months?)\b",
    ]
    return any(re.search(pattern, text, re.I) for pattern in patterns)


def _strip_client_operator_placeholder_labels(value: str) -> str:
    text = str(value or "")
    replacements: list[tuple[str, str]] = [
        (r"\btarget threshold:\s*>\s*provisional threshold\b", "target threshold: above the threshold"),
        (r"\btarget threshold:\s*<\s*provisional threshold\b", "target threshold: below the threshold"),
        (r"\btarget threshold\s*<\s*provisional threshold\b", "target threshold below the threshold"),
        (r"\bexceeds crux threshold by provisional threshold\b", "exceeds the crux threshold by the margin"),
        (r"\bless than provisional threshold\b", "below the threshold"),
        (r"\bmore than provisional threshold\b", "above the threshold"),
        (r"\boperator-defined share threshold of ([^.;,\n|]+)", r"\1 share threshold"),
        (r"\boperator-defined threshold for ([^.;,\n|]+)", r"threshold for \1"),
        (r"\boperator-defined threshold week over week\b", "week-over-week threshold"),
        (r"\boperator-defined effort estimate\s+or\s+less\b", "planning estimate"),
        (r"\boperator-defined effort estimate\b", "planning estimate"),
        (r"\boperator-defined planning estimate\b", "planning estimate"),
        (r"\boperator-defined margin\b", "margin"),
        (r"\boperator-defined threshold\b", "threshold"),
        (r"\bprovisional threshold\b", "threshold"),
        (r"\bprovisional planning estimate\b", "planning estimate"),
        (r"\boperator-defined\b", ""),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.I)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    return text.strip()


def _remove_client_citation_placeholder_noise(value: str) -> str:
    lines: list[str] = []
    in_code_block = False
    for line in str(value or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            lines.append(line)
            continue
        if not stripped:
            lines.append(line)
            continue
        if in_code_block or _looks_like_json_line(stripped) or _line_has_protected_export_reference(line):
            lines.append(line)
            continue
        cleaned = _strip_client_citation_placeholder_text(line)
        if not cleaned.strip():
            continue
        if _is_client_citation_placeholder_line(cleaned):
            continue
        lines.append(cleaned)
    return "\n".join(lines)


def _strip_client_citation_placeholder_text(value: str) -> str:
    text = str(value or "")
    if not re.search(
        r"\b(?:Citation|No citation available|citation unavailable|Evidence source unavailable)\b",
        text,
        re.I,
    ):
        return text
    text = re.sub(r"\b(?:No citation available|citation unavailable|Evidence source unavailable)\b\.?", "", text, flags=re.I)
    text = re.sub(r"\bCitation\s*:\s*(?=$|[|])", "", text, flags=re.I)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+([|,.;:])", r"\1", text)
    return text.strip()


def _is_client_citation_placeholder_line(value: str) -> bool:
    stripped = str(value or "").strip()
    if not stripped:
        return False
    normalized = _normalize_heading(stripped)
    if normalized in {
        "citation",
        "citations",
        "citation marker",
        "citation markers",
        "citation locator",
        "citation locators",
        "source locator",
        "source locators",
    }:
        return True
    if re.fullmatch(r"citation\s*:?", stripped, re.I):
        return True
    if _looks_like_markdown_table_line(stripped):
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        nonempty = [cell for cell in cells if cell]
        if nonempty and all(_normalize_heading(cell).startswith("citation") for cell in nonempty):
            return True
    return False


def _normalize_client_legal_review_effort_placeholder(value: str) -> str:
    text = str(value or "")
    if not re.search(r"\boperator-defined effort estimate\s+or\s+less\b", text, re.I):
        return text
    if _line_has_protected_export_reference(text):
        return text

    effort_pattern = re.compile(r"\boperator-defined effort estimate\s+or\s+less\b", re.I)
    context_pattern = re.compile(
        r"\b(?:legal(?:ly)?(?:[-\s]+review(?:ed)?)?|legal[-\s]+review|"
        r"approval\s+step|SLA|claim[-\s]+safety)\b",
        re.I,
    )
    sentence_pattern = re.compile(
        r"([^.!?\n]*\boperator-defined effort estimate\s+or\s+less\b[^.!?\n]*)([.!?]?)",
        re.I,
    )

    def replace_sentence(match: re.Match[str]) -> str:
        sentence = match.group(1)
        if context_pattern.search(sentence):
            sentence = effort_pattern.sub("24 hours or less", sentence)
        return sentence + match.group(2)

    return sentence_pattern.sub(replace_sentence, text)


def _normalize_operator_export_text(value: str) -> str:
    text = str(value or "")
    text = re.sub(
        r"\bBF\s*=\s*(\d+(?:\.\d+)?)\b",
        r"structural BF estimate=\1 (operator trace, not measured posterior)",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\bBF\s+(\d+(?:\.\d+)?)(?!\s*[-–])\b",
        r"structural BF estimate=\1 (operator trace, not measured posterior)",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\bRPN\s*=\s*(\d+(?:\.\d+)?)\b",
        r"RPN=\1, provisional risk estimate",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\bRPN\s+(\d+(?:\.\d+)?)(?!\s*[-–])\b",
        r"RPN=\1, provisional risk estimate",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\bDQ\s*=\s*(\d+(?:\.\d+)?)\b",
        r"DQ=\1, diagnostic score",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\bDQ\s+(\d+(?:\.\d+)?)(?!\s*[-–])\b",
        r"DQ=\1, diagnostic score",
        text,
        flags=re.I,
    )
    return text


def _normalize_comparator_phrasing(value: str) -> str:
    text = str(value or "")
    metric_pattern = r"\b(BF|DQ|RPN|r|rho|D90|NRR|CAC|activation|retention|churn|PMF|Ellis score)\s+greater than\s+(\d+(?:\.\d+)?)"
    text = re.sub(metric_pattern, lambda m: f"{m.group(1)} >{m.group(2)}", text, flags=re.I)
    metric_less_pattern = r"\b(BF|DQ|RPN|r|rho|D90|NRR|CAC|activation|retention|churn|PMF|Ellis score)\s+less than\s+(\d+(?:\.\d+)?)"
    text = re.sub(metric_less_pattern, lambda m: f"{m.group(1)} <{m.group(2)}", text, flags=re.I)
    text = re.sub(r"\bgreater than\s+(\d+(?:\.\d+)?)", r">\1", text, flags=re.I)
    text = re.sub(r"\bless than\s+(\d+(?:\.\d+)?)", r"<\1", text, flags=re.I)
    return text


def _renumber_repeated_ordered_markers(text: str) -> str:
    output: list[str] = []
    in_code_block = False
    sequence = 0
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            output.append(line)
            continue
        if in_code_block or _looks_like_json_line(stripped):
            output.append(line)
            continue
        match = re.match(r"^(\s*)1\.\s+(.+)$", line)
        if match:
            sequence += 1
            output.append(f"{match.group(1)}{sequence}. {match.group(2)}")
            continue
        if re.match(r"^\s*\d+\.\s+", line):
            try:
                sequence = int(re.match(r"^\s*(\d+)\.", line).group(1))  # type: ignore[union-attr]
            except Exception:
                sequence = 0
            output.append(line)
            continue
        if stripped:
            sequence = 0
        else:
            sequence = 0
        output.append(line)
    return "\n".join(output)


def _join_standalone_list_markers(text: str) -> str:
    lines = str(text or "").splitlines()
    output: list[str] = []
    index = 0
    in_code_block = False
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            output.append(line)
            index += 1
            continue
        if (
            not in_code_block
            and stripped in {"-", "*", "•"}
            and index + 1 < len(lines)
            and lines[index + 1].strip()
            and not _looks_like_json_line(lines[index + 1].strip())
        ):
            indent = re.match(r"^(\s*)", line).group(1)  # type: ignore[union-attr]
            output.append(f"{indent}{stripped} {lines[index + 1].strip()}")
            index += 2
            continue
        output.append(line)
        index += 1
    return "\n".join(output)


def _looks_like_markdown_table_line(value: str) -> bool:
    stripped = str(value or "").strip()
    return stripped.startswith("|") and "|" in stripped[1:]


def _is_protected_export_table_block(lines: list[str]) -> bool:
    text = "\n".join(str(line or "") for line in lines)
    if not text.strip():
        return False
    protected_patterns = [
        r"\b(?:source\s+excerpt|source\s+text|raw\s+source|quoted\s+source|source\s+quote|what\s+it\s+says)\b",
        r"\b(?:citation\s+marker|evidence\s+marker|evidence\s+ids?|evidence\s+locator|"
        r"locator\s+availability|locators?|provenance|source\s+ref|source_id|source\s+phase|"
        r"storage_ref|file_id|knowledge_id)\b",
        r"\b(?:machine_archive|machine\s+archive|project_state\.json|phase_outputs\.json|"
        r"decision_objects\.json|clarifications\.json|evidence_locator_register\.json|"
        r"uploaded_file_manifest\.json|export_manifest\.json)\b",
        r"\[Evidence:\s*[A-Za-z0-9_.:-]+",
        r"\[#\d+\]",
        r"\b(?:ev|evidence|knowledge|source|file)[-_][A-Za-z0-9_.:-]+\b",
        r"https?://|file://|upload:[^\s|)>\]]+|storage_ref\s*[:=]|upload_store[\\/]",
        r"\b[A-Za-z]:[\\/]|\\\\",
        r"\{[^{}\n]*\"?[A-Za-z0-9_.-]+\"?\s*:[^{}\n]*\}|\"[A-Za-z0-9_.-]+\"\s*:",
        r"`[^`]+`",
    ]
    return any(re.search(pattern, text, re.I) for pattern in protected_patterns)


def _line_has_protected_export_reference(value: str) -> bool:
    text = str(value or "")
    return bool(
        re.search(
            r"\[Evidence:\s*[A-Za-z0-9_.:-]+|\[#\d+\]|"
            r"\b(?:ev|evidence|knowledge|source|file)[-_][A-Za-z0-9_.:-]+\b|"
            r"\b(?:evidence_id|knowledge_id|file_id|source_id|storage_ref)\b\s*[:=]",
            text,
            re.I,
        )
    )


def _protect_export_fragments(value: str) -> tuple[str, list[str]]:
    fragments: list[str] = []
    pattern = re.compile(
        r"https?://[^\s)>\]]+|file://[^\s)>\]]+|upload:[^\s|)>\]]+|"
        r"storage:[^\s|)>\]]+|upload_store[\\/][^\n|)>\]]+|"
        r"\b[A-Za-z]:[\\/][^\n|)>\]]+|\\\\[^\n|)>\]]+"
    )

    def repl(match: re.Match[str]) -> str:
        fragments.append(match.group(0))
        return f"__EXPORT_PROTECTED_{len(fragments) - 1}__"

    return pattern.sub(repl, value), fragments


def _restore_export_fragments(value: str, fragments: list[str]) -> str:
    text = str(value or "")
    for index, fragment in enumerate(fragments):
        text = text.replace(f"__EXPORT_PROTECTED_{index}__", fragment)
    return text


def _looks_like_json_line(value: str) -> bool:
    stripped = str(value or "").strip()
    if not stripped:
        return False
    return (
        (stripped.startswith("{") and stripped.endswith("}"))
        or (stripped.startswith("[") and stripped.endswith("]"))
    )


def _structured_locator(item: Any) -> str:
    payload = getattr(item, "structured_payload", None)
    if isinstance(payload, dict):
        return str(payload.get("locator", "") or "")
    return ""


def _is_concrete_locator(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if re.search(r"unavailable|unknown|none|missing|\.\.\.", text, re.I):
        return False
    if re.fullmatch(r"chunk\s*=\s*[^;\s]+", text, re.I):
        return False
    if re.match(r"(?i)^(upload:|source[_ -]?chunk|knowledge[_ -]?id)", text):
        return False
    return bool(re.search(r"(row=|page=|sheet=|fixture://|http://|https://)", text, re.I))


def _format_number(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _collapse_blank_lines(value: str) -> str:
    lines = [line.rstrip() for line in str(value or "").splitlines()]
    collapsed: list[str] = []
    blank = False
    for line in lines:
        if not line.strip():
            if not blank:
                collapsed.append("")
            blank = True
        else:
            collapsed.append(line)
            blank = False
    return "\n".join(collapsed).strip()


def _unique(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
