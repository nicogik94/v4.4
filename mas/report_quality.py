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

RISK_CLASSIFICATION_WARNING = "Risk classification may understate generated risk content."
CLIENT_BF_CONFIDENCE_CAVEAT = (
    "Current evidence does not meet the confidence threshold for selecting a specific growth lever."
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


def assess_report_quality_context(state: Any) -> ReportQualityContext:
    text = _combined_state_text(state)
    domain_text = _domain_source_text(state)
    uploaded_count = len(_uploaded_files(state))
    imported_evidence_count = len(getattr(state, "imported_evidence", []) or [])
    imported_signal_count = len(getattr(state, "imported_signals", []) or [])
    clarification_count = len(getattr(state, "clarification_answers", []) or [])
    has_locators = has_concrete_evidence_locators(state)
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
        provisional_report=required_clarifications_open,
        required_clarifications_open=required_clarifications_open,
        telemetry_privacy_required=requires_telemetry_privacy_caveat(text),
    )


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


def evidence_maturity_projection(
    state: Any,
    context: ReportQualityContext | None = None,
) -> EvidenceMaturityProjection:
    context = context or assess_report_quality_context(state)
    uploaded_count = len(_uploaded_files(state))
    imported_evidence_count = len(getattr(state, "imported_evidence", []) or [])
    imported_signal_count = len(getattr(state, "imported_signals", []) or [])
    has_locators = context.has_concrete_locators

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
        )

    if not context.sparse_evidence and has_locators and (
        uploaded_count > 0 or imported_evidence_count > 0 or imported_signal_count > 0
    ):
        return EvidenceMaturityProjection(
            maturity="Validated",
            client_use_status="Review for delivery",
            validation_required="Decision-critical locators present",
            uploaded_files=uploaded_count,
            imported_evidence=imported_evidence_count,
            imported_signals=imported_signal_count,
            has_concrete_locators=has_locators,
        )

    return EvidenceMaturityProjection(
        maturity="Partial evidence",
        client_use_status="Validate before client delivery",
        validation_required="Targeted evidence follow-up",
        uploaded_files=uploaded_count,
        imported_evidence=imported_evidence_count,
        imported_signals=imported_signal_count,
        has_concrete_locators=has_locators,
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
    value = re.sub(r"(?<=\d)\.\s+(?=\d)", ".", value)
    citation_repeated = len(re.findall(r"citation unavailable", value, flags=re.I)) > 1
    replacements = [
        (r"\b(?:FMEA-derived labels?|FMEA)\b", "structured risk review"),
        (r"\b(?:RPN|risk priority numbers?)(?:\s*[=:]\s*\d+(?:\.\d+)?|\s+\d+(?:\.\d+)?)?\b", "risk priority score"),
        (r"\b(?:Bayes factor|BF)(?:\s*[=:]\s*\d+(?:\.\d+)?|\s+\d+(?:\.\d+)?)?\b", "structural confidence signal"),
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
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            output.append(line)
            continue
        if in_code_block or _looks_like_json_line(stripped):
            output.append(line)
            continue
        output.append(_normalize_export_line(line, mode))
    return "\n".join(output)


def suppress_client_raw_evidence_ids(text: str) -> str:
    """Hide raw evidence IDs in client-facing text when no concrete locator exists."""
    value = str(text or "")
    value = re.sub(
        r"\s*\[Evidence:\s*[A-Za-z0-9_.:-]+\s*(?:\|[^\]]*)?\]",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(r"\s*\[#\d+\]", "", value)
    return re.sub(r"\b(?:ev|evidence|src)-[A-Za-z0-9_.:-]+\b", "project evidence", value, flags=re.I)


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
    primary_sections = [
        section for section in sections
        if _section_has_threshold_content(section[1]) and _is_primary_threshold_section(section[0])
    ]
    subordinate_sections = [
        section for section in sections
        if _section_has_threshold_content(section[1]) and _is_subordinate_threshold_section(section[0])
    ]
    decision_gate_count = sum(1 for heading, _ in sections if _normalize_heading(heading) == "decision gates")
    projected_sparse_growth_gate = context.sparse_evidence and context.decision_domain == "growth" and decision_gate_count == 0

    if decision_gate_count == 1:
        non_gate_primary = [
            section for section in primary_sections
            if _normalize_heading(section[0]) != "decision gates"
        ]
        if not non_gate_primary:
            return ""
        source_of_truth = "Decision Gates"
        if len(non_gate_primary) == 1:
            return THRESHOLD_CONFLICT_BETWEEN_TEMPLATE.format(
                section_a=source_of_truth,
                section_b=non_gate_primary[0][0],
            )
        if len(non_gate_primary) == 1 and subordinate_sections:
            return THRESHOLD_CONFLICT_BETWEEN_TEMPLATE.format(
                section_a=non_gate_primary[0][0],
                section_b=subordinate_sections[0][0],
            )
        if len(non_gate_primary) >= 2:
            return THRESHOLD_CONFLICT_BETWEEN_TEMPLATE.format(
                section_a=non_gate_primary[0][0],
                section_b=non_gate_primary[1][0],
            )
        return THRESHOLD_CONFLICT_UNKNOWN_WARNING

    if projected_sparse_growth_gate and primary_sections:
        return THRESHOLD_CONFLICT_BETWEEN_TEMPLATE.format(
            section_a="projected Decision Gates",
            section_b=primary_sections[0][0],
        )

    if len(primary_sections) >= 2:
        return THRESHOLD_CONFLICT_BETWEEN_TEMPLATE.format(
            section_a=primary_sections[0][0],
            section_b=primary_sections[1][0],
        )

    text = _strip_decision_gates_sections(_combined_state_text(state))
    if projected_sparse_growth_gate and subordinate_sections and not primary_sections:
        text = _strip_subordinate_threshold_sections(text)
    if _has_conflicting_rho_thresholds(text) or _has_conflicting_canary_thresholds(text):
        return THRESHOLD_CONFLICT_UNKNOWN_WARNING
    return ""


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


def _section_has_threshold_content(value: str) -> bool:
    text = str(value or "")
    return bool(
        re.search(r"\b(threshold|gate|gates|proceed|extend|stop|escalate|kill criteria|circuit breaker|canary)\b", text, re.I)
        and re.search(r"([<>≥≤]\s*\d|\b\d+(?:\.\d+)?\s*%|\b\d+(?:\.\d+)?\s*/\s*\d+|\bBF\b|\bDQ\b|\bD90\b|\bNRR\b|\bCAC\b)", text, re.I)
    )


def _is_primary_threshold_section(heading: str) -> bool:
    normalized = _normalize_heading(heading)
    if normalized in {
        "decision gates",
        "decision matrix",
        "decision thresholds",
        "thresholds",
        "convergence gates",
        "gate policy",
        "spend authorization gate",
    }:
        return True
    return bool(
        re.search(r"\b(thresholds?|decision|gates?|matrix)\b", normalized)
        and not _is_subordinate_threshold_section(heading)
    )


def _is_subordinate_threshold_section(heading: str) -> bool:
    normalized = _normalize_heading(heading)
    subordinate = {
        "monitoring details",
        "monitoring and kill criteria",
        "operator controls",
        "roadmap",
        "key risks",
        "early warning signal",
        "early warning signals",
        "mitigation",
        "stop change course threshold",
        "stop change course thresholds",
        "stop change course",
        "canaries",
        "circuit breakers",
    }
    return normalized in subordinate


def _normalize_heading(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _strip_subordinate_threshold_sections(text: str) -> str:
    source = str(text or "")
    for heading in (
        "Monitoring Details",
        "Monitoring and Kill Criteria",
        "Operator Controls",
        "Roadmap",
        "Key Risks",
        "Early Warning Signal",
        "Early Warning Signals",
        "Mitigation",
        "Stop / Change-Course Threshold",
        "Stop / Change-Course Thresholds",
        "Stop / Change-Course",
        "Canaries",
        "Circuit breakers",
    ):
        source = re.sub(
            rf"(?ims)^#{{1,6}}\s+{re.escape(heading)}\s*$.*?(?=^#{{1,6}}\s+|\Z)",
            "",
            source,
        )
    return source


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
    return value


def _normalize_export_line(line: str, audience: str) -> str:
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
        (r"\bcitation unavailable\b", "No citation available"),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.I)
    text = re.sub(r"\bstructural prior(?:\s+structural prior)+\b", "structural prior", text, flags=re.I)
    text = re.sub(r"\bstructural prior\.\s*\d+\b", "structural prior", text, flags=re.I)
    text = re.sub(r"\brisk priority score\s+\d+(?:\.\d+)?\b", "risk priority score", text, flags=re.I)
    return text


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


def _protect_export_fragments(value: str) -> tuple[str, list[str]]:
    fragments: list[str] = []
    pattern = re.compile(
        r"https?://[^\s)>\]]+|file://[^\s)>\]]+|\b[A-Za-z]:[\\/][^\n|)>\]]+|\\\\[^\n|)>\]]+"
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
