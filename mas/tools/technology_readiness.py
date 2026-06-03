"""Deterministic helpers for Technology Readiness & Transfer audits."""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any


TRL_PHASES = {
    "pre_trl_3": {
        "trl": "Pre-TRL 3",
        "phase_name": "Diagnosis",
        "time_range": "1-2 months",
    },
    "trl_3": {
        "trl": "TRL 3",
        "phase_name": "Protection and proof of concept",
        "time_range": "3-6 months",
    },
    "trl_4": {
        "trl": "TRL 4",
        "phase_name": "Controlled technical validation",
        "time_range": "6-12 months",
    },
    "trl_5": {
        "trl": "TRL 5",
        "phase_name": "Relevant environment validation",
        "time_range": "9-18 months",
    },
    "trl_6": {
        "trl": "TRL 6",
        "phase_name": "Demonstration and transfer",
        "time_range": "12-24 months",
    },
}

RESEARCH_INDUSTRY_CRITERIA = (
    "technical_novelty",
    "patentable_potential",
    "industrial_application",
    "functional_advantage",
    "reproducibility",
    "scalability",
    "potential_cost",
    "industrial_interest",
    "regulatory_barriers",
    "trl_4_6_compatibility",
)

IP_PROTECTION_AXES = (
    "material_composition",
    "synthesis_method",
    "specific_use",
    "device_or_system",
    "critical_parameters",
    "know_how",
)

EVIDENCE_CATEGORIES = (
    "scientific_basis",
    "proof_of_concept",
    "reproducibility",
    "controlled_validation",
    "relevant_environment",
    "industrial_validation",
    "cost_scalability",
    "regulatory_review",
    "ip_review",
    "partner_feedback",
)

READINESS_VERDICT_CODES = (
    "not_assessable",
    "pre_trl_diagnosis",
    "ready_for_proof_of_concept",
    "ready_for_controlled_validation",
    "ready_for_relevant_environment_validation",
    "ready_for_industrial_demo",
    "ready_for_transfer_discussion",
    "not_ready_due_to_evidence_gaps",
)


def normalize_trl(value: Any) -> int:
    """Normalize a TRL-like value to an integer in the inclusive 0-9 range."""
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        number = int(value)
    else:
        match = re.search(r"-?\d+(?:\.\d+)?", str(value))
        number = int(float(match.group(0))) if match else 0
    return max(0, min(9, number))


def _score_value(value: Any) -> float | None:
    if isinstance(value, Mapping):
        value = value.get("score")
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if 1 <= score <= 5:
        return score
    return None


def compute_alignment_score(criteria_scores: Mapping[str, Any] | None) -> float:
    """Average valid 1-5 research-industry criterion scores."""
    if not criteria_scores:
        return 0.0
    scores = [
        score
        for criterion, value in criteria_scores.items()
        if criterion in RESEARCH_INDUSTRY_CRITERIA
        for score in [_score_value(value)]
        if score is not None
    ]
    if not scores:
        return 0.0
    return round(sum(scores) / len(scores), 2)


def _category_names(categories: Any) -> list[str]:
    if categories is None:
        return []
    if isinstance(categories, Mapping):
        return [
            str(category).strip()
            for category, present in categories.items()
            if present and str(category).strip()
        ]
    if isinstance(categories, str):
        return [categories.strip()] if categories.strip() else []
    if isinstance(categories, Iterable):
        return [str(category).strip() for category in categories if str(category).strip()]
    return []


def unknown_evidence_categories(categories: Any) -> list[str]:
    """Return supplied evidence categories outside the approved taxonomy."""
    known = set(EVIDENCE_CATEGORIES)
    return sorted({category for category in _category_names(categories) if category not in known})


def compute_evidence_sufficiency(categories: Any) -> dict[str, Any]:
    """Calculate evidence taxonomy coverage for supplied categories."""
    supplied = set(_category_names(categories))
    known = supplied & set(EVIDENCE_CATEGORIES)
    missing = set(EVIDENCE_CATEGORIES) - known
    return {
        "coverage": round(len(known) / len(EVIDENCE_CATEGORIES), 3),
        "coverage_count": len(known),
        "total_categories": len(EVIDENCE_CATEGORIES),
        "known_categories": sorted(known),
        "missing_categories": sorted(missing),
        "unknown_categories": unknown_evidence_categories(supplied),
    }


def overclaim_warnings(assessment: Mapping[str, Any] | None) -> list[str]:
    """Flag evidence, IP, and verdict overclaim risks in a draft assessment."""
    data = dict(assessment or {})
    current_trl = normalize_trl(
        data.get("current_trl")
        or data.get("trl")
        or data.get("target_trl")
    )
    categories = set(_category_names(data.get("evidence_categories") or data.get("evidence")))
    warnings: list[str] = []

    if current_trl >= 4 and not {"reproducibility", "controlled_validation"}.issubset(categories):
        warnings.append("TRL 4+ claimed without reproducibility or controlled validation evidence.")
    if current_trl >= 5 and "relevant_environment" not in categories:
        warnings.append("TRL 5+ claimed without relevant environment evidence.")
    if current_trl >= 7 and not ({"industrial_validation", "operational_validation"} & categories):
        warnings.append("TRL 7+ claimed without operational or industrial validation evidence.")

    specialist_review_required = data.get("specialist_review_required")
    ip_axis = data.get("ip_protection_axis")
    if specialist_review_required is None and isinstance(ip_axis, Mapping):
        specialist_review_required = ip_axis.get("specialist_review_required")
    if specialist_review_required is False:
        warnings.append("IP specialist_review_required=False; legal review cannot be bypassed.")

    verdict_code = data.get("readiness_verdict_code")
    if verdict_code and verdict_code not in READINESS_VERDICT_CODES:
        warnings.append(f"Unknown readiness verdict code: {verdict_code}.")

    return warnings
