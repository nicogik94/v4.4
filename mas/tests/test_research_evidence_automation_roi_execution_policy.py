from decimal import Decimal

import pytest

from research_evidence.automation_roi_execution_models import (
    AutomationRoiInputManifestItem,
)
from research_evidence.automation_roi_execution_policy import (
    ASSUMPTION_FINGERPRINT,
    FORMULA_FINGERPRINT,
    REQUIRED_ROLES,
    AutomationRoiInputShapeError,
    calculate_automation_roi,
    canonical_decimal,
    input_digest,
    provenance_fingerprint,
)


IDS = tuple(f"00000000-0000-0000-0000-{number:012d}" for number in range(1, 25))


def _inputs(
    *,
    baseline="10",
    post="2",
    rate="50",
    periods="52",
    recurring="1000",
    one_time="5000",
    currency="USD",
    recurring_currency=None,
    post_period="week",
    rate_unit="per_hour",
):
    values = (baseline, post, rate, periods, recurring, one_time)
    result = {}
    for index, (role, value) in enumerate(zip(REQUIRED_ROLES, values)):
        result[role] = AutomationRoiInputManifestItem(
            input_role=role,
            numeric_value=Decimal(value),
            unit=rate_unit if role == "fully_loaded_rate_per_hour" else "",
            period=(
                post_period
                if role == "post_automation_hours_per_period"
                else "week" if role == "baseline_hours_per_period" else None
            ),
            currency_code=(
                recurring_currency
                if role == "annual_recurring_cost" and recurring_currency
                else currency if role in REQUIRED_ROLES[2:3] + REQUIRED_ROLES[4:] else None
            ),
            time_unit="hours" if role in REQUIRED_ROLES[:2] else None,
            binding_id=IDS[index * 4],
            approved_calculation_input_id=IDS[index * 4 + 1],
            candidate_fact_revision_id=IDS[index * 4 + 2],
            approval_decision_id=IDS[index * 4 + 3],
        )
    return result


def test_formula_and_assumption_fingerprints_are_locked():
    assert FORMULA_FINGERPRINT == (
        "260ea8cf45b4d1e58fbb290838bd6da044b9b5ca6eba8874cbbb4ef8596b58f7"
    )
    assert ASSUMPTION_FINGERPRINT == (
        "2a4590fb9d2930fbc46c1fbf3b0d669b950154dd8371fd51bc9399d1afcc2449"
    )


def test_exact_golden_vector_uses_decimal_without_rounding():
    result = calculate_automation_roi(_inputs(one_time="6000"))
    assert result.status == "valid"
    assert result.currency_code == "USD"
    assert result.annual_labor_savings == Decimal("20800")
    assert result.annual_net_benefit == Decimal("19800")
    assert result.first_year_net_benefit == Decimal("13800")
    assert result.first_year_roi_percent == Decimal("230")


def test_fractional_result_is_not_rounded():
    result = calculate_automation_roi(_inputs(one_time="3000"))
    assert result.first_year_roi_percent == Decimal(16800) / Decimal(3000) * 100


@pytest.mark.parametrize(
    ("changes", "diagnostic"),
    [
        ({"rate_unit": "per_day"}, "unit_incompatibility"),
        ({"post_period": "month"}, "period_incompatibility"),
        ({"recurring_currency": "EUR"}, "currency_incompatibility"),
    ],
)
def test_incompatibilities_produce_blocked_result(changes, diagnostic):
    result = calculate_automation_roi(_inputs(**changes))
    assert result.status == "blocked"
    assert result.roi_percent_status == "blocked"
    assert diagnostic in result.diagnostics
    assert result.annual_labor_savings is None


def test_zero_cost_is_not_applicable_and_negative_savings_is_valid():
    zero = calculate_automation_roi(_inputs(one_time="0"))
    assert zero.status == "not_applicable"
    assert zero.first_year_roi_percent is None
    negative = calculate_automation_roi(_inputs(baseline="2", post="10"))
    assert negative.status == "valid"
    assert negative.annual_labor_savings == Decimal("-20800")
    assert negative.diagnostics == {
        "negative_hours_delta": "post_automation_hours_exceed_baseline"
    }


def test_shape_and_fingerprint_contracts_are_deterministic():
    inputs = _inputs()
    assert input_digest(inputs) == input_digest(dict(reversed(tuple(inputs.items()))))
    assert provenance_fingerprint(inputs) == provenance_fingerprint(inputs)
    del inputs["periods_per_year"]
    with pytest.raises(AutomationRoiInputShapeError):
        calculate_automation_roi(inputs)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("296.00"), "296"),
        (Decimal("-0"), "0"),
        (Decimal("1E+2"), "100"),
        (Decimal("0.1234567890123456789"), "0.1234567890123456789"),
    ],
)
def test_decimal_canonicalization(value, expected):
    assert canonical_decimal(value) == expected
