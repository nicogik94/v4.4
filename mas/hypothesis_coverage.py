"""Advisory hypothesis variable coverage diagnostics.

The helpers in this module are deterministic, read-only projections over
ProjectState. They do not mutate hypotheses, block workflow execution, or claim
that a hypothesis is true.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class VariableCategorySpec:
    key: str
    label: str
    relevance_terms: tuple[str, ...]
    coverage_terms: tuple[str, ...]
    evidence_need: str
    generally_critical: bool = False


@dataclass(frozen=True)
class VariableEvidenceNeed:
    category: str
    evidence_need: str


@dataclass(frozen=True)
class HypothesisVariableCoverage:
    has_hypotheses: bool
    covered_categories: tuple[str, ...]
    missing_critical_categories: tuple[str, ...]
    not_relevant_categories: tuple[str, ...]
    assumptions_needing_validation: tuple[str, ...]
    evidence_needs: tuple[VariableEvidenceNeed, ...]


VARIABLE_CATEGORY_SPECS: tuple[VariableCategorySpec, ...] = (
    VariableCategorySpec(
        key="demand_user_segment",
        label="Demand / user segment",
        relevance_terms=(
            "demand", "user segment", "segment", "customer", "buyer", "icp",
            "persona", "market", "pmf", "product-market", "adoption",
        ),
        coverage_terms=(
            "demand", "segment", "customer", "buyer", "icp", "persona",
            "market", "pmf", "adoption", "user",
        ),
        evidence_need="Segment demand evidence such as interviews, pipeline quality, usage, or willingness-to-act signals.",
    ),
    VariableCategorySpec(
        key="channel_acquisition",
        label="Channel / acquisition",
        relevance_terms=(
            "channel", "acquisition", "campaign", "paid", "organic", "seo",
            "referral", "funnel", "cac", "lead", "pipeline", "gtm",
            "go-to-market", "traffic", "conversion",
        ),
        coverage_terms=(
            "channel", "acquisition", "campaign", "paid", "organic", "seo",
            "referral", "funnel", "cac", "lead", "pipeline", "gtm",
            "traffic", "conversion", "attribution",
        ),
        evidence_need="Channel attribution evidence such as CAC, source mix, conversion, pipeline, or traffic quality.",
    ),
    VariableCategorySpec(
        key="activation_onboarding",
        label="Activation / onboarding",
        relevance_terms=(
            "activation", "onboarding", "signup", "sign-up", "trial",
            "first value", "aha", "setup", "time to value", "activation rate",
        ),
        coverage_terms=(
            "activation", "onboarding", "signup", "trial", "first value",
            "aha", "setup", "time to value", "activation rate",
        ),
        evidence_need="Activation evidence such as onboarding completion, first-value timing, trial conversion, or setup friction.",
    ),
    VariableCategorySpec(
        key="retention_repeat_usage",
        label="Retention / repeat usage",
        relevance_terms=(
            "retention", "repeat", "renewal", "churn", "cohort", "nrr",
            "engagement", "usage", "stickiness", "expansion",
        ),
        coverage_terms=(
            "retention", "repeat", "renewal", "churn", "cohort", "nrr",
            "engagement", "usage", "stickiness", "expansion",
        ),
        evidence_need="Retention evidence such as cohorts, repeat usage, churn, renewal, engagement, or expansion signals.",
    ),
    VariableCategorySpec(
        key="monetization_pricing",
        label="Monetization / pricing",
        relevance_terms=(
            "pricing", "price", "monetization", "revenue", "arr", "mrr",
            "paid plan", "packaging", "margin", "discount", "willingness to pay",
            "ltv", "payback",
        ),
        coverage_terms=(
            "pricing", "price", "monetization", "revenue", "arr", "mrr",
            "paid", "packaging", "margin", "discount", "willingness to pay",
            "ltv", "payback",
        ),
        evidence_need="Pricing evidence such as willingness-to-pay, conversion by package, margin, revenue, or payback data.",
    ),
    VariableCategorySpec(
        key="operational_capacity",
        label="Operational capacity",
        relevance_terms=(
            "capacity", "staffing", "support", "sla", "process", "workflow",
            "ops", "operations", "delivery", "throughput", "handoff",
        ),
        coverage_terms=(
            "capacity", "staffing", "support", "sla", "process", "workflow",
            "ops", "operations", "delivery", "throughput", "handoff",
        ),
        evidence_need="Operational evidence such as staffing, throughput, SLA, support load, or process capacity.",
    ),
    VariableCategorySpec(
        key="data_quality_measurement",
        label="Data quality / measurement",
        relevance_terms=(),
        coverage_terms=(
            "measure", "measurement", "data quality", "instrumentation",
            "telemetry", "metric", "analytics", "attribution", "baseline",
            "sample", "tracked", "dashboard", "observed", "data",
        ),
        evidence_need="Measurement evidence such as instrumentation, baseline data, sample definition, or metric reliability.",
        generally_critical=True,
    ),
    VariableCategorySpec(
        key="legal_compliance_claim_safety",
        label="Legal / compliance / claim-safety",
        relevance_terms=(
            "legal", "compliance", "claim", "claim-safety", "privacy",
            "security", "gdpr", "ai act", "approval", "regulated", "policy",
            "financial", "medical", "risk classification",
        ),
        coverage_terms=(
            "legal", "compliance", "claim", "claim-safety", "privacy",
            "security", "gdpr", "ai act", "approval", "regulated", "policy",
            "financial", "medical", "risk classification",
        ),
        evidence_need="Legal or compliance evidence such as review SLA, approval record, policy constraint, or risk classification.",
    ),
    VariableCategorySpec(
        key="competitive_dynamics",
        label="Competitive dynamics",
        relevance_terms=(
            "competitor", "competitive", "alternative", "substitute",
            "market map", "differentiation", "parity", "positioning",
        ),
        coverage_terms=(
            "competitor", "competitive", "alternative", "substitute",
            "market map", "differentiation", "parity", "positioning",
        ),
        evidence_need="Competitive evidence such as competitor comparison, alternatives, substitution risk, or differentiation signals.",
    ),
    VariableCategorySpec(
        key="implementation_complexity",
        label="Implementation complexity",
        relevance_terms=(
            "implementation", "implement", "engineering", "integration",
            "complexity", "migration", "build", "technical", "dependency",
            "dependencies", "feasibility", "rollout", "launch",
        ),
        coverage_terms=(
            "implementation", "implement", "engineering", "integration",
            "complexity", "migration", "build", "technical", "dependency",
            "dependencies", "feasibility", "rollout", "launch",
        ),
        evidence_need="Implementation evidence such as dependency mapping, technical feasibility, effort, rollout, or integration risk.",
    ),
    VariableCategorySpec(
        key="owner_decision_authority",
        label="Owner / decision authority",
        relevance_terms=(),
        coverage_terms=(
            "owner", "sponsor", "authority", "decision owner", "accountable",
            "leadership", "stakeholder", "sign-off", "approval", "responsible",
        ),
        evidence_need="Ownership evidence such as named decision owner, approval path, accountable sponsor, or sign-off criteria.",
        generally_critical=True,
    ),
    VariableCategorySpec(
        key="time_horizon_cadence",
        label="Time horizon / cadence",
        relevance_terms=(),
        coverage_terms=(
            "day", "days", "week", "weeks", "month", "months", "d30", "d60",
            "d90", "cadence", "timeline", "within", "by ", "sprint",
            "quarter", "deadline",
        ),
        evidence_need="Timing evidence such as validation window, review cadence, deadline, or decision checkpoint.",
        generally_critical=True,
    ),
    VariableCategorySpec(
        key="evidence_required_to_validate",
        label="Evidence required to validate",
        relevance_terms=(),
        coverage_terms=(
            "evidence", "validate", "validation", "experiment", "test",
            "signal:", "signal ", "metric", "threshold", "confirm:",
            "reject:", "sample", "interview", "analysis", "pilot",
            "instrument", "cohort",
        ),
        evidence_need="Validation evidence such as test design, metric threshold, source, sample, or decision gate.",
        generally_critical=True,
    ),
)


def assess_hypothesis_variable_coverage(state: Any) -> HypothesisVariableCoverage:
    """Assess advisory variable coverage for the current hypothesis set."""
    hypotheses = tuple(getattr(state, "hypotheses", None) or ())
    if not hypotheses:
        return HypothesisVariableCoverage(
            has_hypotheses=False,
            covered_categories=(),
            missing_critical_categories=(),
            not_relevant_categories=tuple(spec.label for spec in VARIABLE_CATEGORY_SPECS),
            assumptions_needing_validation=(),
            evidence_needs=(),
        )

    context_text = _normalize_text(_state_context_text(state))
    hypothesis_text = _normalize_text(_hypotheses_text(hypotheses))
    relevance_text = f"{context_text} {hypothesis_text}"

    covered: list[str] = []
    missing: list[str] = []
    not_relevant: list[str] = []
    needs: list[VariableEvidenceNeed] = []
    assumptions: list[str] = []

    for spec in VARIABLE_CATEGORY_SPECS:
        relevant = spec.generally_critical or _contains_any(relevance_text, spec.relevance_terms)
        if not relevant:
            not_relevant.append(spec.label)
            continue
        is_covered = _contains_any(hypothesis_text, spec.coverage_terms)
        if is_covered:
            covered.append(spec.label)
            continue
        missing.append(spec.label)
        needs.append(VariableEvidenceNeed(category=spec.label, evidence_need=spec.evidence_need))
        assumptions.append(f"{spec.label} assumption requires validation.")

    return HypothesisVariableCoverage(
        has_hypotheses=True,
        covered_categories=tuple(covered),
        missing_critical_categories=tuple(missing),
        not_relevant_categories=tuple(not_relevant),
        assumptions_needing_validation=tuple(assumptions),
        evidence_needs=tuple(needs),
    )


def _state_context_text(state: Any) -> str:
    parts: list[str] = [
        str(getattr(state, "project_name", "") or ""),
        str(getattr(state, "brief", "") or ""),
        str(getattr(state, "data", "") or ""),
    ]
    classify = getattr(state, "classify", None)
    if classify is not None:
        for field in (
            "domain",
            "justification",
            "reference_class",
            "variety_env",
            "variety_sys",
            "variety_gaps",
            "variety_decision",
            "rpd_pattern",
            "sensemaking_anchors",
            "expectancy_violations",
            "maturity_assessment",
        ):
            parts.append(str(getattr(classify, field, "") or ""))
    return " ".join(parts)


def _hypotheses_text(hypotheses: tuple[Any, ...]) -> str:
    parts: list[str] = []
    for hypothesis in hypotheses:
        for field in ("text", "justification", "signal", "confirm", "reject", "evoi", "portfolio_cluster"):
            value = getattr(hypothesis, field, "")
            if value:
                parts.append(f"{field}: {value}")
    return " ".join(parts)


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").casefold().replace("_", " ").split())


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term.casefold() in text for term in terms)
