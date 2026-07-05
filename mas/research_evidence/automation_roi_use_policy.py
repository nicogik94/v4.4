"""Canonical R1.6A Automation ROI evidence-use policy.

This module evaluates only immutable R1.6 binding records selected explicitly by
ID. It neither resolves effective bindings nor authorizes calculation execution.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from .binding_models import ResearchEvidenceConsumerInputBindingRecord


CONSUMER_CONTRACT = "deterministic_calculation"
CONSUMER_CONTRACT_VERSION = "automation_roi.evidence_input.v1"
POLICY_IDENTIFIER = "automation_roi.evidence_use"
POLICY_VERSION = "1"
EVALUATOR_VERSION = "automation_roi.evidence_use.evaluator.v1"
CALCULATION_KIND = "automation_roi"

REQUIRED_ROLES: tuple[str, ...] = (
    "baseline_hours_per_period",
    "post_automation_hours_per_period",
    "fully_loaded_rate_per_hour",
    "periods_per_year",
    "annual_recurring_cost",
    "one_time_implementation_cost",
)

POLICY_PARAMETERS = {
    "binding_record_must_be_current": True,
    "calculation_kind": CALCULATION_KIND,
    "consumer_contract": CONSUMER_CONTRACT,
    "does_not_satisfy": {
        "availability_status": [False],
        "consumer_disposition": ["does_not_meet_contract"],
        "disposition_reasons": ["contradiction_declared"],
        "drift_status": ["material_drift"],
        "lineage_is_current": [False],
        "review_status": ["rejected", "needs_revision", "withdrawn"],
    },
    "indeterminate": {
        "consumer_disposition": ["indeterminate"],
        "drift_status": ["not_assessed", "indeterminate"],
        "freshness_status": ["unknown"],
        "review_status": ["not_assessed"],
    },
    "qualified": {
        "consumer_disposition": ["qualified"],
        "freshness_status": ["stale"],
    },
    "required_roles": list(REQUIRED_ROLES),
    "satisfies": {
        "availability_status": True,
        "consumer_disposition": "meets_contract",
        "drift_status": "no_material_drift",
        "freshness_status": "fresh",
        "lineage_is_current": True,
        "review_status": "approved",
    },
    "status_precedence": [
        "does_not_satisfy",
        "indeterminate",
        "qualified",
        "satisfies",
    ],
}


def canonical_policy_json() -> str:
    return json.dumps(POLICY_PARAMETERS, sort_keys=True, separators=(",", ":"))


POLICY_FINGERPRINT = hashlib.sha256(
    canonical_policy_json().encode("utf-8")
).hexdigest()


@dataclass(frozen=True)
class PolicyEvaluation:
    status: str
    reasons: tuple[str, ...]


def evaluate_binding_set(
    bindings: Sequence[ResearchEvidenceConsumerInputBindingRecord],
    *,
    project_id: str,
    binding_set_id: str,
    freshness_as_of: datetime,
    successor_binding_ids: Iterable[str] = (),
) -> PolicyEvaluation:
    """Evaluate one complete, explicit six-binding set."""
    successors = set(successor_binding_ids)
    hard: list[str] = []
    indeterminate: list[str] = []
    qualified: list[str] = []

    ordered = sorted(bindings, key=lambda item: _role_order(item.input_key))
    for binding in ordered:
        role = binding.input_key
        _append_set_mismatches(
            hard,
            binding,
            project_id=project_id,
            binding_set_id=binding_set_id,
            freshness_as_of=freshness_as_of,
        )
        if binding.id in successors:
            hard.append(_reason(role, "binding_record_superseded"))
        if not binding.availability_status:
            hard.append(_reason(role, "evidence_unavailable"))
        if not binding.lineage_is_current:
            hard.append(_reason(role, "lineage_not_current"))
        if binding.review_status in {"rejected", "needs_revision", "withdrawn"}:
            hard.append(_reason(role, f"review_{binding.review_status}"))
        if binding.drift_status == "material_drift":
            hard.append(_reason(role, "material_drift"))
        if binding.consumer_disposition == "does_not_meet_contract":
            hard.append(_reason(role, "consumer_does_not_meet_contract"))
        if "contradiction_declared" in binding.disposition_reasons:
            hard.append(_reason(role, "contradiction_declared"))

        if binding.review_status == "not_assessed":
            indeterminate.append(_reason(role, "review_not_assessed"))
        if binding.freshness_status == "unknown":
            indeterminate.append(_reason(role, "freshness_unknown"))
        if binding.drift_status in {"not_assessed", "indeterminate"}:
            indeterminate.append(_reason(role, f"drift_{binding.drift_status}"))
        if binding.consumer_disposition == "indeterminate":
            indeterminate.append(_reason(role, "consumer_indeterminate"))

        if binding.freshness_status == "stale":
            qualified.append(_reason(role, "freshness_stale"))
        if binding.consumer_disposition == "qualified":
            qualified.append(_reason(role, "consumer_qualified"))

    if hard:
        return PolicyEvaluation("does_not_satisfy", _ordered_unique(hard))
    if indeterminate:
        return PolicyEvaluation("indeterminate", _ordered_unique(indeterminate))
    if qualified:
        return PolicyEvaluation("qualified", _ordered_unique(qualified))
    return PolicyEvaluation("satisfies", ("policy_satisfied",))


def _append_set_mismatches(
    reasons: list[str],
    binding: ResearchEvidenceConsumerInputBindingRecord,
    *,
    project_id: str,
    binding_set_id: str,
    freshness_as_of: datetime,
) -> None:
    expected = (
        ("project_mismatch", binding.project_id, project_id),
        ("consumer_contract_mismatch", binding.consumer_contract, CONSUMER_CONTRACT),
        (
            "consumer_contract_version_mismatch",
            binding.consumer_contract_version,
            CONSUMER_CONTRACT_VERSION,
        ),
        ("binding_set_mismatch", binding.binding_set_id, binding_set_id),
        ("calculation_kind_mismatch", binding.calculation_kind, CALCULATION_KIND),
        ("policy_identifier_mismatch", binding.policy_identifier, POLICY_IDENTIFIER),
        ("policy_version_mismatch", binding.policy_version, POLICY_VERSION),
        ("policy_parameters_mismatch", binding.policy_parameters_json, POLICY_PARAMETERS),
        ("policy_fingerprint_mismatch", binding.policy_fingerprint, POLICY_FINGERPRINT),
        ("evaluator_version_mismatch", binding.evaluator_version, EVALUATOR_VERSION),
        ("freshness_as_of_mismatch", binding.freshness_as_of, freshness_as_of),
    )
    for code, actual, required in expected:
        if actual != required:
            reasons.append(_reason(binding.input_key, code))
    if any(
        value is not None
        for value in (
            binding.claim_intake_item_id,
            binding.claim_support_assessment_id,
            binding.locator_resolution,
            binding.evidence_linkage,
            binding.semantic_relationship,
        )
    ):
        reasons.append(_reason(binding.input_key, "claim_semantics_present"))


def _role_order(role: str) -> tuple[int, str]:
    try:
        return REQUIRED_ROLES.index(role), role
    except ValueError:
        return len(REQUIRED_ROLES), role


def _reason(role: str, code: str) -> str:
    return f"role:{role}:{code}"


def _ordered_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
