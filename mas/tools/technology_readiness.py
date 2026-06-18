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
    "certification, legal patentability advice, or a guarantee of commercial transfer. "
    "Evidence/provenance fields support traceability; they do not prove every claim."
)

READINESS_RADAR_DIMENSIONS = (
    "technical_readiness",
    "evidence_readiness",
    "ip_readiness",
    "market_application_readiness",
    "scaling_readiness",
    "regulatory_readiness",
    "transfer_readiness",
    "partner_readiness",
)

PORTFOLIO_PRIORITY_VALUES = (
    "high",
    "medium",
    "low",
    "defer",
    "not_assessable",
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
        rationale_parts.append("Technical evidence is sufficient for conditional operator review, but IP evidence is incomplete.")
    else:
        rationale_parts.append("Required evidence categories are supplied for operator review at this stage gate.")
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


def _score_cap_for_evidence(score: float, categories: set[str], required: set[str], cap: float = 2.0) -> float:
    if not categories:
        return min(score, 1.0)
    if required and not (categories & required):
        return min(score, cap)
    return score


def _radar_confidence(categories: set[str], required: set[str]) -> str:
    if not categories:
        return "low"
    if required and required.issubset(categories):
        return "high"
    if required and (categories & required):
        return "medium"
    return "low"


def _radar_row(score: float, confidence: str, evidence_summary: str, top_gap: str, next_action: str) -> dict[str, Any]:
    return {
        "score": round(max(0.0, min(5.0, score)), 2),
        "confidence": confidence,
        "evidence_summary": evidence_summary,
        "top_gap": top_gap,
        "next_action": next_action,
    }


def build_readiness_radar_scorecard(assessment: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Build deterministic readiness radar scores with evidence caps."""
    data = dict(assessment or {})
    categories = set(_category_names(data.get("evidence_categories") or data.get("evidence")))
    current_trl = normalize_trl(data.get("current_trl") or data.get("trl"))
    alignment = _score_value(data.get("research_industry_alignment_score")) or _score_value(data.get("overall_alignment_score")) or 0.0
    coverage = compute_evidence_sufficiency(categories)
    coverage_score = coverage["coverage"] * 5

    technical_score = _score_cap_for_evidence(min(5.0, current_trl / 2), categories, {"scientific_basis", "proof_of_concept"})
    evidence_score = 0.0 if not categories else coverage_score
    ip_score = 4.0 if "ip_review" in categories else min(2.0, 1.0 + (1.0 if "scientific_basis" in categories else 0.0))
    market_score = _score_cap_for_evidence(alignment, categories, {"industrial_validation", "partner_feedback", "proof_of_concept"})
    scaling_score = _score_cap_for_evidence(4.0 if "cost_scalability" in categories else 2.0, categories, {"cost_scalability"})
    regulatory_score = _score_cap_for_evidence(4.0 if "regulatory_review" in categories else 2.0, categories, {"regulatory_review"})
    transfer_score = _score_cap_for_evidence(4.0 if {"industrial_validation", "partner_feedback"} & categories else 2.0, categories, {"industrial_validation", "partner_feedback"})
    partner_score = 4.0 if "partner_feedback" in categories else min(2.0, transfer_score)

    return {
        "technical_readiness": _radar_row(
            technical_score,
            _radar_confidence(categories, {"scientific_basis", "proof_of_concept"}),
            "TRL estimate grounded in supplied scientific and proof-of-concept evidence.",
            "Scientific basis or proof-of-concept evidence missing." if not {"scientific_basis", "proof_of_concept"} <= categories else "Next technical gate evidence still needs operator review.",
            "Collect proof-of-concept and controlled validation evidence.",
        ),
        "evidence_readiness": _radar_row(
            evidence_score,
            "high" if coverage["coverage"] >= 0.7 else "medium" if coverage["coverage"] >= 0.3 else "low",
            f"{coverage['coverage_count']}/{coverage['total_categories']} evidence categories supplied.",
            "Decision-critical evidence categories remain incomplete.",
            "Fill missing evidence categories before external reliance.",
        ),
        "ip_readiness": _radar_row(
            ip_score,
            "medium" if "ip_review" in categories else "low",
            "IP review evidence supplied." if "ip_review" in categories else "No explicit IP review evidence supplied.",
            "Missing IP review caps IP readiness.",
            "Run preliminary IP/protection review with specialist review required.",
        ),
        "market_application_readiness": _radar_row(
            market_score,
            _radar_confidence(categories, {"partner_feedback", "industrial_validation", "proof_of_concept"}),
            "Market/application score reflects alignment score and validation evidence.",
            "Industrial or partner evidence is incomplete.",
            "Validate priority applications with partner or industry feedback.",
        ),
        "scaling_readiness": _radar_row(
            scaling_score,
            "medium" if "cost_scalability" in categories else "low",
            "Cost/scalability evidence supplied." if "cost_scalability" in categories else "Cost/scalability evidence missing.",
            "Scaling readiness is capped without cost/scalability evidence.",
            "Estimate cost, throughput, and scale constraints.",
        ),
        "regulatory_readiness": _radar_row(
            regulatory_score,
            "medium" if "regulatory_review" in categories else "low",
            "Regulatory review evidence supplied." if "regulatory_review" in categories else "Regulatory review evidence missing.",
            "Regulatory barriers remain unreviewed.",
            "Complete preliminary regulatory review before transfer discussion.",
        ),
        "transfer_readiness": _radar_row(
            transfer_score,
            _radar_confidence(categories, {"industrial_validation", "partner_feedback"}),
            "Transfer score reflects industrial validation or partner feedback evidence.",
            "Industrial validation or partner feedback missing.",
            "Prepare partner validation brief and industrial demo evidence.",
        ),
        "partner_readiness": _radar_row(
            partner_score,
            "medium" if "partner_feedback" in categories else "low",
            "Partner feedback evidence supplied." if "partner_feedback" in categories else "No partner feedback evidence supplied.",
            "Partner readiness is capped without partner feedback.",
            "Collect partner feedback before transfer claims.",
        ),
    }


def build_tto_handoff_package(assessment: Mapping[str, Any] | None) -> dict[str, Any]:
    """Build an operator-reviewed TTO handoff package without legal claims."""
    data = dict(assessment or {})
    technology_name = str(data.get("technology_name") or "Technology under review").strip()
    categories = set(_category_names(data.get("evidence_categories") or data.get("evidence")))
    current_trl = normalize_trl(data.get("current_trl") or data.get("trl"))
    missing = compute_evidence_sufficiency(categories)["missing_categories"]
    disclosure_warning = (
        "Disclosure risk before publication or external demos; specialist review required before relying on IP/protection conclusions."
    )

    return {
        "invention_disclosure_draft": {
            "title": technology_name,
            "readiness_context": f"Evidence-traceable planning estimate: TRL {current_trl}." if current_trl else "TRL not assessable.",
            "operator_review_note": "Operator-reviewed draft for specialist intake; not legal advice.",
        },
        "non_confidential_summary": (
            f"{technology_name}: non-confidential overview for transfer discussion. "
            "Do not include enabling confidential details until specialist review is complete."
        ),
        "confidential_technical_appendix_outline": [
            "Scientific basis and proof-of-concept evidence",
            "Critical parameters and know-how boundaries",
            "Controlled validation protocol and acceptance criteria",
            "Evidence gaps and why a higher TRL is not yet justified",
        ],
        "ip_review_questions": [
            "Which protection axes need specialist review before disclosure?",
            "What prior-art or freedom-to-operate review is required?",
            "What information must remain confidential before external demos?",
            "Specialist review required before legal or patentability reliance.",
        ],
        "partner_validation_brief": (
            "Use a non-confidential validation brief first; request partner feedback on relevant-environment needs, "
            "scale constraints, and acceptance criteria."
        ),
        "commercialization_route_options": [
            "sponsored validation",
            "joint development discussion",
            "license or option discussion after specialist review",
            "internal maturation before external transfer",
        ],
        "evidence_checklist_before_external_disclosure": [
            *missing,
            "specialist IP/protection review",
            "operator approval for non-confidential summary",
        ],
        "disclosure_risk_notes": [
            disclosure_warning,
            "Separate confidential technical appendix from non-confidential partner summary.",
            "Do not claim legal patentability, certified TRL, or guaranteed transfer.",
        ],
    }


def rank_technology_readiness_portfolio(items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Rank technology-readiness candidates using deterministic evidence gates."""
    ranked: list[dict[str, Any]] = []
    for item in items or []:
        data = dict(item or {})
        categories = set(_category_names(data.get("evidence_categories") or data.get("evidence")))
        sufficiency = compute_evidence_sufficiency(categories)
        current_trl = normalize_trl(data.get("current_trl") or data.get("trl"))
        target_trl = normalize_trl(data.get("target_trl") or data.get("next_target_trl") or (current_trl + 1 if current_trl else 0))
        alignment = _score_value(data.get("research_industry_alignment_score")) or _score_value(data.get("overall_alignment_score")) or 0.0
        stage_gate = build_stage_gate_decision(
            {
                "current_trl": current_trl,
                "next_target_trl": target_trl,
                "evidence_categories": categories,
                "ip_claims_present": data.get("ip_claims_present"),
            }
        )
        validation_gap_count = len(stage_gate["blocking_gaps"])
        transfer_readiness = 1.0
        if "industrial_validation" in categories:
            transfer_readiness += 2.0
        if "partner_feedback" in categories:
            transfer_readiness += 2.0
        transfer_readiness = min(5.0, transfer_readiness)

        unsupported_high_trl = current_trl >= 5 and sufficiency["coverage"] < 0.3
        if current_trl == 0:
            priority = "not_assessable"
        elif sufficiency["coverage"] < 0.2 and not data.get("strategic_option"):
            priority = "defer"
        elif unsupported_high_trl:
            priority = "defer"
        elif sufficiency["coverage"] >= 0.5 and alignment >= 3.5 and validation_gap_count == 0:
            priority = "high"
        elif sufficiency["coverage"] >= 0.3 and alignment >= 2.5:
            priority = "medium"
        else:
            priority = "low"

        score = (
            sufficiency["coverage"] * 40
            + alignment * 8
            + transfer_readiness * 5
            + min(current_trl, 6) * 2
            - validation_gap_count * 8
            - (25 if unsupported_high_trl else 0)
        )
        ranked.append(
            {
                "project_id": data.get("project_id") or data.get("technology_name") or "unknown",
                "technology_name": data.get("technology_name") or data.get("project_id") or "unknown",
                "current_trl": current_trl,
                "target_trl": target_trl,
                "evidence_sufficiency": sufficiency["coverage"],
                "research_industry_alignment_score": alignment,
                "ip_risk": data.get("ip_risk") or ("review_missing" if "ip_review" not in categories else "reviewed"),
                "validation_gap_count": validation_gap_count,
                "transfer_readiness": round(transfer_readiness, 2),
                "estimated_time_to_next_trl": data.get("estimated_time_to_next_trl") or data.get("estimated_time_range") or "not_estimated",
                "recommended_priority": priority,
                "rationale": (
                    f"Evidence coverage {sufficiency['coverage']:.2f}, alignment {alignment:.1f}, "
                    f"validation gaps {validation_gap_count}; unsupported high-TRL claim={unsupported_high_trl}."
                ),
                "_rank_score": round(score, 3),
            }
        )

    priority_order = {value: idx for idx, value in enumerate(PORTFOLIO_PRIORITY_VALUES)}
    ranked.sort(key=lambda row: (priority_order.get(row["recommended_priority"], 99), -row["_rank_score"], row["project_id"]))
    for row in ranked:
        row.pop("_rank_score", None)
    return ranked


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
