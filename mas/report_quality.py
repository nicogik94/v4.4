"""Deterministic report/export quality helpers.

These helpers are read-only projections over ProjectState. They do not mutate
state, route workflow, call providers, or change export payload schemas.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any


SPARSE_EVIDENCE_CAVEAT = (
    "This is a structured hypothesis map, not a measured audit. Direct evidence "
    "is limited or absent. Treat probabilities, scores, thresholds, and risk "
    "rankings as provisional priors until Sprint 0 validates them."
)

PROVISIONAL_CLARIFICATION_CAVEAT = (
    "Provisional report: clarification questions have not been answered. "
    "Recommendations should be reviewed after the operator answers the "
    "decision-critical follow-up questions."
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

NO_CONCRETE_LOCATORS_CLIENT_NOTE = (
    "No concrete citation locators were available for this project; evidence "
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
    telemetry_privacy_required: bool = False
    sparse_evidence_caveat: str = SPARSE_EVIDENCE_CAVEAT
    provisional_clarification_caveat: str = PROVISIONAL_CLARIFICATION_CAVEAT
    telemetry_privacy_caveat: str = TELEMETRY_PRIVACY_CAVEAT


def assess_report_quality_context(state: Any) -> ReportQualityContext:
    text = _combined_state_text(state)
    domain_text = _domain_source_text(state)
    uploaded_count = len(_uploaded_files(state))
    imported_evidence_count = len(getattr(state, "imported_evidence", []) or [])
    imported_signal_count = len(getattr(state, "imported_signals", []) or [])
    clarification_count = len(getattr(state, "clarification_answers", []) or [])
    has_locators = has_concrete_evidence_locators(state)
    zero_clarifications = clarification_count == 0

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

    absent_count = sum(
        [
            uploaded_count == 0,
            imported_evidence_count == 0,
            imported_signal_count == 0,
            zero_clarifications,
            not has_locators,
        ]
    )
    sparse_evidence = absent_count >= 4 or explicit_sparse
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
        provisional_report=sparse_evidence and zero_clarifications,
        telemetry_privacy_required=requires_telemetry_privacy_caveat(text),
    )


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
    layer = getattr(state, "knowledge_layer", None)
    for item in list(getattr(layer, "items", []) or []):
        for value in (
            getattr(item, "locator", ""),
            getattr(item, "source_ref", ""),
            _structured_locator(item),
        ):
            if _is_concrete_locator(value):
                return True
    for evidence in list(getattr(state, "imported_evidence", []) or []):
        provenance = getattr(evidence, "provenance", None)
        if _is_concrete_locator(getattr(provenance, "external_uri", "")):
            return True
    return False


def has_budget_or_spend_evidence(state: Any) -> bool:
    text = _combined_state_text(state)
    return bool(re.search(r"(\bbudget\b|\bspend\b|\bcost\b|\bfinance\b|\bfinancial\b|\brevenue\b|\bmargin\b|\bsavings?\b|\binvoice\b)", text, re.I))


def requires_telemetry_privacy_caveat(text: str) -> bool:
    return bool(TELEMETRY_PATTERN.search(text or ""))


def client_simplify_text(text: str, *, sparse_evidence: bool = False) -> str:
    """Translate technical report wording for client-facing dossier sections."""
    value = str(text or "")
    citation_repeated = len(re.findall(r"citation unavailable", value, flags=re.I)) > 1
    replacements = [
        (r"\b(?:FMEA-derived labels?|FMEA)\b", "structured risk review"),
        (r"\b(?:RPN|risk priority numbers?)(?:\s*[=:]\s*\d+(?:\.\d+)?|\s+\d+(?:\.\d+)?)?\b", "structured risk priority"),
        (r"\b(?:Bayes factor|BF)(?:\s*[=:]\s*\d+(?:\.\d+)?|\s+\d+(?:\.\d+)?)?\b", "internal confidence diagnostic"),
        (r"\bDQ(?:\s*[=:]\s*\d+(?:\.\d+)?|\s+\d+(?:\.\d+)?)?\b", "evidence quality diagnostic"),
        (r"\bH_norm(?:\s*[=:]\s*\d+(?:\.\d+)?|\s+\d+(?:\.\d+)?)?\b", "uncertainty diagnostic"),
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
    return _collapse_blank_lines(value)


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


def threshold_consistency_warnings(state: Any, context: ReportQualityContext | None = None) -> list[str]:
    context = context or assess_report_quality_context(state)
    text = _combined_state_text(state)
    warnings: list[str] = []
    if _has_conflicting_rho_thresholds(text) or _has_conflicting_canary_thresholds(text):
        warnings.append(THRESHOLD_WARNING)
    if _has_high_confidence_language(text) and context.sparse_evidence:
        warnings.append(SPARSE_CONFIDENCE_RULE)
    if _has_exact_dollar_estimate(text) and not context.has_budget_or_spend_evidence:
        warnings.append("Exact dollar estimates appear without budget or spend evidence; treat them as placeholders until Sprint 0 validates cost data.")
    monitor = getattr(state, "monitor", None)
    if monitor and PLACEHOLDER_RATIONALE_PATTERN.match(getattr(monitor, "commitment_rationale", "") or ""):
        warnings.append("Commitment score is unconfirmed; operator confirmation is required before treating it as a score.")
    return _unique(warnings)


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
            simplified = re.sub(r"\b\d+(?:\.\d+)?\s*%", "operator-confirmed threshold required", simplified)
            simplified = re.sub(r"\b\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?\b", "operator-confirmed count threshold required", simplified)
        simplified = re.sub(
            r"\b(?:failure probability|predicted failure probability)\s*(?:of|=|:)?\s*(?:0\.\d+|\d{1,3}\s*%)",
            "high provisional failure risk",
            simplified,
            flags=re.I,
        )
        simplified = re.sub(
            r"\b(?:probability|prior|likelihood|chance)\s*(?:of|=|:)?\s*\d{1,3}\s*%",
            "model-generated prior",
            simplified,
            flags=re.I,
        )
        simplified = re.sub(
            r"\b(?:scenario[_ -]?probability|structural probability|probability|prior|likelihood|chance)\s*(?:of|=|:)?\s*(?:0(?:\.\d+)?|1(?:\.0+)?)\b",
            "model-generated prior",
            simplified,
            flags=re.I,
        )
        simplified = re.sub(
            r"\b\d{1,3}\s*%\s*(?:scenario[_ -]?probability|structural probability|probability|prior|likelihood|chance)\b",
            "model-generated prior",
            simplified,
            flags=re.I,
        )
        simplified = re.sub(
            r"\b(?:0(?:\.\d+)?|1(?:\.0+)?)\s*(?:scenario[_ -]?probability|structural probability|probability|prior|likelihood|chance)\b",
            "model-generated prior",
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
            r"\b\d+(?:\.\d+)?\s*(?:person-hours?|hours?)\b",
            "provisional effort estimate",
            simplified,
            flags=re.I,
        )
        lines.append(simplified)
    return "\n".join(lines)


def _is_provisional_planning_gate(line: str) -> bool:
    return bool(re.search(r"\b(proposed|provisional)\b.*\b(planning gate|gate|threshold)\b", line or "", re.I))


def _cleanup_client_replacement_artifacts(value: str) -> str:
    phrases = [
        "internal confidence diagnostic",
        "evidence quality diagnostic",
        "uncertainty diagnostic",
        "structured risk review",
        "structured risk priority",
        "related-hypothesis risk",
        "schema overlap score",
        "forecast accuracy check",
        "calibration check",
        "model-generated prior",
        "high provisional failure risk",
    ]
    for phrase in phrases:
        escaped = re.escape(phrase)
        value = re.sub(rf"({escaped})(?=[A-Za-z0-9])", r"\1 ", value, flags=re.I)
        value = re.sub(rf"(?:{escaped}\s*){{2,}}", f"{phrase} ", value, flags=re.I)
    value = re.sub(r"[ \t]{2,}", " ", value)
    value = re.sub(r"\s+([,.;:])", r"\1", value)
    value = re.sub(r"([,.;:])(?=\S)", r"\1 ", value)
    return value


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
    return bool(re.search(r"(#|chunk=|row=|page=|upload:|fixture://|http://|https://)", text, re.I))


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
