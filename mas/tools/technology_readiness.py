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

STAGE_GATE_DECISIONS = (
    "proceed",
    "proceed_with_conditions",
    "hold",
    "stop",
)

WORKBOOK_DISCLAIMER = (
    "This workbook is an operator-reviewed readiness assessment. It is not TRL "
    "certification, legal patentability advice, or a guarantee of commercial transfer."
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


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, Iterable):
        return list(value)
    return [value]


def _text_list(value: Any) -> list[str]:
    items: list[str] = []
    for item in _as_list(value):
        if isinstance(item, Mapping):
            label = item.get("evidence_id") or item.get("id") or item.get("title") or item.get("summary")
            if label:
                items.append(str(label).strip())
        else:
            text = str(item).strip()
            if text:
                items.append(text)
    return items


def _evidence_ids(value: Any) -> list[str]:
    ids: list[str] = []
    for item in _as_list(value):
        if isinstance(item, Mapping):
            evidence_id = item.get("evidence_id") or item.get("id")
            if evidence_id:
                ids.append(str(evidence_id).strip())
        else:
            text = str(item).strip()
            if text:
                ids.append(text)
    return sorted({item for item in ids if item})


def _confidence(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"high", "medium", "low"}:
        return text
    if text in {"preliminary", "directional", "partial"}:
        return "low"
    return "medium" if text else "low"


def _stage_gate_required_categories(next_trl: int) -> list[str]:
    if next_trl <= 3:
        return ["scientific_basis", "proof_of_concept"]
    if next_trl == 4:
        return ["reproducibility", "controlled_validation"]
    if next_trl >= 7:
        return ["industrial_validation", "partner_feedback"]
    if next_trl >= 5:
        return ["relevant_environment"]
    return ["scientific_basis"]


def build_stage_gate_decision(assessment: Mapping[str, Any] | None) -> dict[str, Any]:
    """Build a deterministic TRL advancement gate decision.

    The helper uses only supplied assessment fields and evidence categories. It
    never advances a TRL claim on LLM judgment alone.
    """
    data = dict(assessment or {})
    current_trl = normalize_trl(data.get("current_trl") or data.get("trl"))
    next_trl = normalize_trl(
        data.get("next_trl")
        or data.get("next_target_trl")
        or data.get("target_trl")
        or (current_trl + 1 if current_trl else 1)
    )
    if current_trl and next_trl <= current_trl and current_trl < 9:
        next_trl = current_trl + 1
    next_trl = max(1, min(9, next_trl))

    categories = set(
        _category_names(
            data.get("evidence_categories")
            or data.get("evidence")
            or data.get("known_categories")
        )
    )
    required_categories = _stage_gate_required_categories(next_trl)
    blocking_gaps: list[str] = []

    if current_trl == 0:
        blocking_gaps.append("Current TRL is unknown or not assessable; advancement cannot proceed.")

    if next_trl == 4 and not ({"reproducibility", "controlled_validation"} & categories):
        blocking_gaps.append("Missing reproducibility or controlled_validation evidence for TRL 4+.")
    elif next_trl >= 7 and not ({"industrial_validation", "partner_feedback"} & categories):
        blocking_gaps.append("Missing industrial_validation or partner_feedback evidence for TRL 7+.")
    elif next_trl >= 5 and "relevant_environment" not in categories:
        blocking_gaps.append("Missing relevant_environment evidence for TRL 5+.")
    elif next_trl <= 3:
        missing = [category for category in required_categories if category not in categories]
        if missing:
            blocking_gaps.append("Missing required early-stage evidence: " + ", ".join(missing) + ".")

    explicit_required = [
        category
        for category in _category_names(data.get("required_evidence"))
        if category in EVIDENCE_CATEGORIES
    ]
    missing_explicit = [category for category in explicit_required if category not in categories]
    if missing_explicit:
        blocking_gaps.append("Missing required evidence categories: " + ", ".join(sorted(missing_explicit)) + ".")

    ip_claims_present = bool(
        data.get("ip_claims_present")
        or data.get("ip_claims")
        or data.get("ip_protection_axis")
    )
    ip_condition = ""
    if ip_claims_present and "ip_review" not in categories:
        ip_condition = "IP/protection claims need explicit ip_review evidence before external reliance."

    if blocking_gaps:
        decision = "hold"
    elif ip_condition:
        decision = "proceed_with_conditions"
    else:
        decision = "proceed"

    required_evidence = list(dict.fromkeys([*required_categories, *explicit_required]))
    if ip_claims_present and "ip_review" not in required_evidence:
        required_evidence.append("ip_review")

    rationale_parts = []
    if blocking_gaps:
        rationale_parts.append("Advancement is blocked by evidence gaps.")
    elif ip_condition:
        rationale_parts.append("Technical evidence is sufficient for conditional review, but IP evidence is incomplete.")
    else:
        rationale_parts.append("Required evidence categories supplied for this stage gate.")
    if current_trl == 0:
        rationale_parts.append("A defensible current TRL must be established first.")

    return {
        "current_trl": current_trl,
        "next_trl": next_trl,
        "gate_name": f"TRL {current_trl} to TRL {next_trl} advancement gate" if current_trl else f"TRL assessment to TRL {next_trl} gate",
        "decision": decision,
        "blocking_gaps": blocking_gaps + ([ip_condition] if ip_condition and decision == "hold" else []),
        "required_evidence": required_evidence,
        "required_tests": _text_list(data.get("required_tests")),
        "acceptance_criteria": _text_list(data.get("acceptance_criteria") or data.get("advancement_criteria")),
        "owner_suggestions": _text_list(data.get("owner_suggestions") or data.get("suggested_owners")),
        "estimated_time_range": str(data.get("estimated_time_range") or "").strip() or "Operator to estimate",
        "go_no_go_criteria": _text_list(data.get("go_no_go_criteria") or data.get("advancement_criteria")),
        "rationale": " ".join(rationale_parts),
        "confidence": str(data.get("confidence") or "low").strip() or "low",
    }


def build_claim_ledger(assessment: Mapping[str, Any] | None) -> dict[str, Any]:
    """Map readiness claims to supplied evidence, limitations, and validation needs."""
    data = dict(assessment or {})
    categories = set(_category_names(data.get("evidence_categories") or data.get("evidence")))
    current_trl = normalize_trl(data.get("current_trl") or data.get("trl"))
    confidence = _confidence(data.get("confidence"))
    why_not_higher = str(data.get("why_not_higher") or "").strip()
    current_evidence_ids = _evidence_ids(data.get("evidence_ids") or data.get("current_trl_evidence_ids"))
    required_evidence = _text_list(data.get("required_evidence"))
    warnings: list[str] = []
    claims: list[dict[str, Any]] = []

    current_label = "fact" if current_evidence_ids and confidence == "high" else "inference" if current_evidence_ids else "hypothesis"
    if confidence == "high" and not current_evidence_ids:
        warnings.append("Unsupported high-confidence current TRL claim has no evidence IDs.")

    claims.append(
        {
            "claim_id": "trl-current",
            "claim": f"Current defensible TRL is {current_trl}." if current_trl else "Current TRL is not assessable from supplied evidence.",
            "label": current_label,
            "confidence": confidence if current_evidence_ids else "low",
            "evidence_ids": current_evidence_ids,
            "limitations": [why_not_higher] if why_not_higher else ["Current TRL depends on operator-reviewed evidence completeness."],
            "validate_with": [] if current_evidence_ids else (required_evidence or ["Provide evidence IDs supporting the current TRL."]),
            "would_change_if": "Additional controlled or relevant-environment evidence supports a higher TRL.",
            "related_phase": "trl_diagnosis",
            "related_trl": current_trl,
        }
    )

    verdict = data.get("readiness_verdict") or data.get("readiness_verdict_code")
    if verdict:
        verdict_evidence_ids = _evidence_ids(data.get("verdict_evidence_ids"))
        if confidence == "high" and not verdict_evidence_ids:
            warnings.append("Unsupported high-confidence readiness verdict has no evidence IDs.")
        claims.append(
            {
                "claim_id": "readiness-verdict",
                "claim": str(verdict),
                "label": "inference" if verdict_evidence_ids else "hypothesis",
                "confidence": confidence if verdict_evidence_ids else "low",
                "evidence_ids": verdict_evidence_ids,
                "limitations": [why_not_higher] if why_not_higher else ["Readiness verdict is preliminary."],
                "validate_with": [] if verdict_evidence_ids else (required_evidence or ["Collect missing stage-gate evidence."]),
                "would_change_if": "Blocking evidence gaps are resolved or a specialist review changes the risk view.",
                "related_phase": "executive_summary",
                "related_trl": current_trl,
            }
        )

    ip_claims = _text_list(data.get("ip_claims"))
    ip_axis = data.get("ip_protection_axis")
    if not ip_claims and isinstance(ip_axis, Mapping):
        ip_claims = [
            str(axis)
            for axis in IP_PROTECTION_AXES
            if axis in ip_axis and _as_mapping(ip_axis.get(axis))
        ]
    ip_evidence_ids = _evidence_ids(data.get("ip_evidence_ids"))
    ip_has_review = "ip_review" in categories
    for idx, claim in enumerate(ip_claims, start=1):
        label = "inference" if ip_has_review and ip_evidence_ids else "hypothesis"
        if confidence == "high" and not ip_evidence_ids:
            warnings.append(f"Unsupported high-confidence IP claim {idx} has no evidence IDs.")
        claims.append(
            {
                "claim_id": f"ip-{idx}",
                "claim": claim,
                "label": label,
                "confidence": confidence if ip_evidence_ids else "low",
                "evidence_ids": ip_evidence_ids,
                "limitations": ["Preliminary IP/protection review only; specialist review required."],
                "validate_with": [] if ip_has_review and ip_evidence_ids else ["ip_review"],
                "would_change_if": "IP specialist review, prior-art review, or disclosure history changes.",
                "related_phase": "ip_protection_axis",
                "related_trl": current_trl,
            }
        )

    return {"claims": claims, "warnings": warnings}


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

    if current_trl >= 4 and not ({"reproducibility", "controlled_validation"} & categories):
        warnings.append("TRL 4+ claimed without reproducibility or controlled validation evidence.")
    if current_trl >= 5 and "relevant_environment" not in categories:
        warnings.append("TRL 5+ claimed without relevant environment evidence.")
    if current_trl >= 7 and not ({"industrial_validation", "partner_feedback", "operational_validation"} & categories):
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
