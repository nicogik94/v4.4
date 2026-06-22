"""Typed CandidateFactRevision validation tests (Slice A). No database required."""
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from knowledge.evidence_snapshot.validation import (  # noqa: E402
    FactValidationError,
    validate_fact,
)


def test_money_requires_currency_and_as_of_date():
    fact = validate_fact(
        "money", value="1999.95", currency_code="usd", as_of_date=date(2026, 1, 1),
    )
    assert fact.numeric_value == Decimal("1999.95")
    assert isinstance(fact.numeric_value, Decimal)
    assert fact.currency_code == "USD"
    assert fact.as_of_date == date(2026, 1, 1)

    with pytest.raises(FactValidationError):
        validate_fact("money", value="10", as_of_date=date(2026, 1, 1))  # no currency
    with pytest.raises(FactValidationError):
        validate_fact("money", value="10", currency_code="USD")  # no as_of_date
    with pytest.raises(FactValidationError):
        validate_fact("money", value="10", currency_code="ZZZ", as_of_date=date(2026, 1, 1))


def test_float_input_is_rejected_for_numeric_facts():
    # Never float: prevents binary floating-point error from entering a fact.
    with pytest.raises(FactValidationError):
        validate_fact("money", value=19.99, currency_code="USD", as_of_date=date(2026, 1, 1))
    with pytest.raises(FactValidationError):
        validate_fact("duration", value=3.5, time_unit="days")


def test_rate_requires_numerator_and_denominator_context():
    fact = validate_fact(
        "rate", value="0.42", numerator_context="clicks", denominator_context="impressions",
    )
    assert fact.numeric_value == Decimal("0.42")
    with pytest.raises(FactValidationError):
        validate_fact("rate", value="0.42", numerator_context="clicks")
    with pytest.raises(FactValidationError):
        validate_fact("rate", value="0.42", denominator_context="impressions")


def test_percentage_bounds_enforced_by_subtype():
    ok = validate_fact("percentage", value="42.5", percentage_basis="of revenue", percentage_subtype="share_0_100")
    assert ok.numeric_value == Decimal("42.5")
    frac = validate_fact("percentage", value="0.5", percentage_basis="of revenue", percentage_subtype="share_0_1")
    assert frac.numeric_value == Decimal("0.5")

    with pytest.raises(FactValidationError):
        validate_fact("percentage", value="150", percentage_basis="x", percentage_subtype="share_0_100")
    with pytest.raises(FactValidationError):
        validate_fact("percentage", value="1.5", percentage_basis="x", percentage_subtype="share_0_1")
    with pytest.raises(FactValidationError):
        validate_fact("percentage", value="10", percentage_basis="x", percentage_subtype="unknown")
    with pytest.raises(FactValidationError):
        validate_fact("percentage", value="10", percentage_subtype="share_0_100")  # no basis


def test_duration_requires_known_time_unit():
    fact = validate_fact("duration", value="14", time_unit="days")
    assert fact.numeric_value == Decimal("14")
    assert fact.time_unit == "days"
    with pytest.raises(FactValidationError):
        validate_fact("duration", value="14", time_unit="fortnights")
    with pytest.raises(FactValidationError):
        validate_fact("duration", value="-1", time_unit="days")


def test_count_must_be_integral_with_entity():
    fact = validate_fact("count", value=7, counted_entity="active users")
    assert fact.numeric_value == Decimal("7")
    assert fact.counted_entity == "active users"
    with pytest.raises(FactValidationError):
        validate_fact("count", value="7.5", counted_entity="users")  # non-integral
    with pytest.raises(FactValidationError):
        validate_fact("count", value=7)  # no counted entity


def test_text_and_categorical_never_become_numeric():
    text_fact = validate_fact("text", text="Qualitative observation about market sentiment.")
    assert text_fact.numeric_value is None
    assert text_fact.text_value

    cat = validate_fact("categorical", text="north-region")
    assert cat.numeric_value is None

    # Passing a numeric value to a text fact is rejected, not silently coerced.
    with pytest.raises(FactValidationError):
        validate_fact("text", value="42")
    with pytest.raises(FactValidationError):
        validate_fact("categorical", text="")  # empty text


def test_unknown_fact_type_rejected():
    with pytest.raises(FactValidationError):
        validate_fact("ratio", value="1")
