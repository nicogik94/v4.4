"""Fixed deterministic formula registry for R1.6B Automation ROI execution."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping, Optional

from .automation_roi_execution_models import AutomationRoiInputManifestItem


FORMULA_IDENTIFIER = "automation_roi"
FORMULA_VERSION = "automation_roi.v1"
ASSUMPTION_SET_VERSION = "automation_roi.assumptions.v1"
CONSUMER_CONTRACT = "deterministic_calculation"

REQUIRED_ROLES: tuple[str, ...] = (
    "baseline_hours_per_period",
    "post_automation_hours_per_period",
    "fully_loaded_rate_per_hour",
    "periods_per_year",
    "annual_recurring_cost",
    "one_time_implementation_cost",
)

FORMULA_CONTRACT = {
    "assumption_rules": {
        "approved_snapshot_is_execution_authority": True,
        "annual_recurring_cost_is_subtracted_annually": True,
        "one_time_implementation_cost_is_subtracted_in_first_year": True,
        "periods_per_year_is_the_annualization_multiplier": True,
    },
    "currency_rules": {
        "fx_conversion": False,
        "money_roles_must_share_one_currency": True,
    },
    "equations": {
        "annual_labor_savings": "(baseline_hours_per_period-post_automation_hours_per_period)*fully_loaded_rate_per_hour*periods_per_year",
        "annual_net_benefit": "annual_labor_savings-annual_recurring_cost",
        "first_year_net_benefit": "annual_net_benefit-one_time_implementation_cost",
        "first_year_roi_percent": "first_year_net_benefit/one_time_implementation_cost*100",
    },
    "negative_savings_behavior": "valid_with_negative_hours_delta_diagnostic",
    "period_rules": {
        "hours_roles_must_share_one_nonblank_period": True,
        "periods_per_year_must_be_integral_and_at_least_one": True,
    },
    "required_roles": list(REQUIRED_ROLES),
    "rounding_policy": "no_calculation_time_rounding_full_numeric_precision",
    "unit_rules": {
        "fully_loaded_rate_per_hour": {"unit": "per_hour"},
        "hours_roles": {"time_unit": "hours"},
    },
    "zero_cost_behavior": "roi_percent_not_applicable",
}

ASSUMPTIONS = {
    "annualization": "periods_per_year",
    "currency_conversion": "none",
    "execution_authority": "immutable_v59_snapshot_stored_policy_status",
    "first_year_cost_treatment": "annual_recurring_plus_one_time_implementation",
    "rounding": "none",
}

OUTPUT_UNITS = {
    "annual_labor_savings": "currency_per_year",
    "annual_net_benefit": "currency_per_year",
    "first_year_net_benefit": "currency_first_year",
    "first_year_roi_percent": "percent",
}


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


FORMULA_FINGERPRINT = hashlib.sha256(
    canonical_json(FORMULA_CONTRACT).encode("utf-8")
).hexdigest()
ASSUMPTION_FINGERPRINT = hashlib.sha256(
    canonical_json(ASSUMPTIONS).encode("utf-8")
).hexdigest()


def canonical_decimal(value: Decimal) -> str:
    decimal_value = value if isinstance(value, Decimal) else Decimal(value)
    if not decimal_value.is_finite():
        raise ValueError("decimal value must be finite")
    normalized = decimal_value.normalize()
    if normalized == 0:
        return "0"
    return format(normalized, "f")


def input_digest(inputs: Mapping[str, AutomationRoiInputManifestItem]) -> str:
    return hashlib.sha256(
        canonical_json(
            {
                role: {
                    "currency_code": inputs[role].currency_code,
                    "numeric_value": canonical_decimal(inputs[role].numeric_value),
                    "period": inputs[role].period,
                    "time_unit": inputs[role].time_unit,
                    "unit": inputs[role].unit,
                }
                for role in REQUIRED_ROLES
            }
        ).encode("utf-8")
    ).hexdigest()


def provenance_fingerprint(
    inputs: Mapping[str, AutomationRoiInputManifestItem],
) -> str:
    return hashlib.sha256(
        canonical_json(
            {
                role: {
                    "approved_calculation_input_id": (
                        inputs[role].approved_calculation_input_id
                    ),
                    "approval_decision_id": inputs[role].approval_decision_id,
                    "binding_id": inputs[role].binding_id,
                    "candidate_fact_revision_id": (
                        inputs[role].candidate_fact_revision_id
                    ),
                }
                for role in REQUIRED_ROLES
            }
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class AutomationRoiComputation:
    status: str
    roi_percent_status: str
    currency_code: Optional[str]
    annual_labor_savings: Optional[Decimal]
    annual_net_benefit: Optional[Decimal]
    first_year_net_benefit: Optional[Decimal]
    first_year_roi_percent: Optional[Decimal]
    diagnostics: dict[str, str]


class AutomationRoiInputShapeError(ValueError):
    """The resolved manifest is not exactly the fixed six-role contract."""


def calculate_automation_roi(
    inputs: Mapping[str, AutomationRoiInputManifestItem],
) -> AutomationRoiComputation:
    if set(inputs) != set(REQUIRED_ROLES) or len(inputs) != len(REQUIRED_ROLES):
        raise AutomationRoiInputShapeError(
            "calculation requires exactly the six canonical roles"
        )
    if any(inputs[role].input_role != role for role in REQUIRED_ROLES):
        raise AutomationRoiInputShapeError("manifest role keys do not match items")

    diagnostics: dict[str, str] = {}

    def blocked(code: str) -> AutomationRoiComputation:
        return AutomationRoiComputation(
            "blocked", "blocked", None, None, None, None, None, {code: code}
        )

    hours = ("baseline_hours_per_period", "post_automation_hours_per_period")
    money = (
        "fully_loaded_rate_per_hour",
        "annual_recurring_cost",
        "one_time_implementation_cost",
    )
    if any(inputs[role].time_unit != "hours" for role in hours):
        return blocked("unit_incompatibility")
    if inputs["fully_loaded_rate_per_hour"].unit != "per_hour":
        return blocked("unit_incompatibility")
    periods = {inputs[role].period for role in hours}
    if len(periods) != 1 or None in periods or "" in periods:
        return blocked("period_incompatibility")
    currencies = {inputs[role].currency_code for role in money}
    if len(currencies) != 1 or None in currencies or "" in currencies:
        return blocked("currency_incompatibility")
    periods_per_year = inputs["periods_per_year"].numeric_value
    if periods_per_year < 1 or periods_per_year != periods_per_year.to_integral():
        return blocked("period_incompatibility")

    baseline = inputs["baseline_hours_per_period"].numeric_value
    post = inputs["post_automation_hours_per_period"].numeric_value
    rate = inputs["fully_loaded_rate_per_hour"].numeric_value
    recurring = inputs["annual_recurring_cost"].numeric_value
    one_time = inputs["one_time_implementation_cost"].numeric_value

    delta = baseline - post
    annual_labor_savings = delta * rate * periods_per_year
    annual_net_benefit = annual_labor_savings - recurring
    first_year_net_benefit = annual_net_benefit - one_time
    if delta < 0:
        diagnostics["negative_hours_delta"] = (
            "post_automation_hours_exceed_baseline"
        )
    if one_time == 0:
        diagnostics["roi_percent"] = "not_applicable_zero_implementation_cost"
        return AutomationRoiComputation(
            "not_applicable",
            "not_applicable",
            next(iter(currencies)),
            annual_labor_savings,
            annual_net_benefit,
            first_year_net_benefit,
            None,
            diagnostics,
        )
    return AutomationRoiComputation(
        "valid",
        "computed",
        next(iter(currencies)),
        annual_labor_savings,
        annual_net_benefit,
        first_year_net_benefit,
        first_year_net_benefit / one_time * Decimal(100),
        diagnostics,
    )
