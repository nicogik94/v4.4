"""Typed validation for Slice A CandidateFactRevision values.

Direct, typed, source-derived facts only. Numeric values are normalized to
``Decimal`` (NUMERIC-compatible) and ``float`` inputs are rejected outright so a
fact can never silently inherit binary floating-point error. Text and categorical
facts never carry a numeric value — text must never silently convert into a
numeric fact.

This is a narrow internal validation interface used by tests and deliberate typed
callers. It does not extract facts from arbitrary documents, spreadsheets, or CSV
cells, and it is not wired to any endpoint or UI.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

# Fact types that store a numeric (NUMERIC/Decimal) value.
NUMERIC_FACT_TYPES = frozenset({"money", "rate", "percentage", "duration", "count"})
# Fact types that store textual evidence only.
TEXT_FACT_TYPES = frozenset({"categorical", "text"})
FACT_TYPES = NUMERIC_FACT_TYPES | TEXT_FACT_TYPES

# A pragmatic ISO-4217 subset. Membership is validated; the list is deliberately
# conservative rather than exhaustive so unknown codes fail closed.
ISO_4217_CURRENCIES = frozenset({
    "USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "CNY", "HKD",
    "SGD", "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "INR", "BRL", "MXN",
    "ZAR", "KRW", "TRY", "RUB", "AED", "SAR", "ILS", "THB", "IDR", "MYR",
})

DURATION_UNITS = frozenset({
    "seconds", "minutes", "hours", "days", "weeks", "months", "quarters", "years",
})

# Percentage subtypes define their own bounds. ``None`` bound means unbounded.
PERCENTAGE_SUBTYPE_BOUNDS: dict[str, tuple[Optional[Decimal], Optional[Decimal]]] = {
    "share_0_100": (Decimal("0"), Decimal("100")),   # e.g. 42.5 (%)
    "share_0_1": (Decimal("0"), Decimal("1")),        # e.g. 0.425 (fraction)
    "change": (None, None),                            # period-over-period change
    "margin": (None, Decimal("100")),                  # margins capped at 100%
}


class FactValidationError(ValueError):
    """Raised when a candidate fact value fails typed validation."""


@dataclass
class ValidatedFact:
    """Normalized, validated fact ready for persistence."""

    fact_type: str
    numeric_value: Optional[Decimal] = None
    text_value: Optional[str] = None
    unit: str = ""
    currency_code: Optional[str] = None
    as_of_date: Optional[date] = None
    numerator_context: Optional[str] = None
    denominator_context: Optional[str] = None
    percentage_basis: Optional[str] = None
    percentage_subtype: Optional[str] = None
    time_unit: Optional[str] = None
    counted_entity: Optional[str] = None

    def as_row(self) -> dict[str, Any]:
        return {
            "fact_type": self.fact_type,
            "numeric_value": self.numeric_value,
            "text_value": self.text_value,
            "unit": self.unit,
            "currency_code": self.currency_code,
            "as_of_date": self.as_of_date,
            "numerator_context": self.numerator_context,
            "denominator_context": self.denominator_context,
            "percentage_basis": self.percentage_basis,
            "percentage_subtype": self.percentage_subtype,
            "time_unit": self.time_unit,
            "counted_entity": self.counted_entity,
        }


def _to_decimal(value: Any) -> Decimal:
    """Coerce to Decimal while rejecting float (and bool) inputs."""
    if isinstance(value, bool):
        raise FactValidationError("Boolean is not a valid numeric fact value.")
    if isinstance(value, float):
        raise FactValidationError(
            "float values are not permitted; pass Decimal, int, or a numeric string."
        )
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise FactValidationError("Empty string is not a valid numeric value.")
        try:
            return Decimal(text)
        except InvalidOperation as exc:
            raise FactValidationError(f"Not a valid numeric value: {value!r}") from exc
    raise FactValidationError(f"Unsupported numeric type: {type(value).__name__}")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise FactValidationError(message)


def validate_fact(
    fact_type: str,
    *,
    value: Any = None,
    text: Optional[str] = None,
    unit: str = "",
    currency_code: Optional[str] = None,
    as_of_date: Optional[date] = None,
    numerator_context: Optional[str] = None,
    denominator_context: Optional[str] = None,
    percentage_basis: Optional[str] = None,
    percentage_subtype: Optional[str] = None,
    time_unit: Optional[str] = None,
    counted_entity: Optional[str] = None,
) -> ValidatedFact:
    """Validate and normalize one typed source-derived fact.

    Numeric profiles require ``value`` (Decimal/int/str — never float); text and
    categorical profiles require ``text`` and forbid ``value``.
    """
    if fact_type not in FACT_TYPES:
        raise FactValidationError(f"Unknown fact_type: {fact_type!r}")

    if fact_type in TEXT_FACT_TYPES:
        _require(value is None, f"{fact_type} facts must not carry a numeric value.")
        _require(
            isinstance(text, str) and text.strip() != "",
            f"{fact_type} facts require non-empty textual evidence.",
        )
        return ValidatedFact(fact_type=fact_type, text_value=text, unit=unit)

    # Numeric profiles below.
    _require(value is not None, f"{fact_type} facts require a numeric value.")
    numeric = _to_decimal(value)

    if fact_type == "money":
        _require(currency_code is not None, "money facts require an ISO-4217 currency code.")
        code = str(currency_code).strip().upper()
        _require(code in ISO_4217_CURRENCIES, f"Unknown ISO-4217 currency code: {currency_code!r}")
        _require(isinstance(as_of_date, date), "money facts require an as_of_date.")
        return ValidatedFact(
            fact_type="money", numeric_value=numeric, unit=unit,
            currency_code=code, as_of_date=as_of_date,
        )

    if fact_type == "rate":
        _require(
            bool(numerator_context and numerator_context.strip()),
            "rate facts require a numerator_context.",
        )
        _require(
            bool(denominator_context and denominator_context.strip()),
            "rate facts require a denominator_context.",
        )
        return ValidatedFact(
            fact_type="rate", numeric_value=numeric, unit=unit,
            numerator_context=numerator_context, denominator_context=denominator_context,
        )

    if fact_type == "percentage":
        _require(bool(percentage_basis and percentage_basis.strip()), "percentage facts require a basis.")
        _require(
            percentage_subtype in PERCENTAGE_SUBTYPE_BOUNDS,
            f"Unknown percentage subtype: {percentage_subtype!r}",
        )
        low, high = PERCENTAGE_SUBTYPE_BOUNDS[percentage_subtype]
        if low is not None:
            _require(numeric >= low, f"percentage {numeric} below subtype lower bound {low}.")
        if high is not None:
            _require(numeric <= high, f"percentage {numeric} above subtype upper bound {high}.")
        return ValidatedFact(
            fact_type="percentage", numeric_value=numeric, unit=unit,
            percentage_basis=percentage_basis, percentage_subtype=percentage_subtype,
        )

    if fact_type == "duration":
        _require(numeric >= 0, "duration facts must be non-negative.")
        unit_value = (time_unit or "").strip().lower()
        _require(unit_value in DURATION_UNITS, f"Unknown duration time_unit: {time_unit!r}")
        return ValidatedFact(
            fact_type="duration", numeric_value=numeric, unit=unit, time_unit=unit_value,
        )

    if fact_type == "count":
        _require(numeric == numeric.to_integral_value(), "count facts must be integral.")
        _require(
            bool(counted_entity and counted_entity.strip()),
            "count facts require a counted-entity description.",
        )
        return ValidatedFact(
            fact_type="count", numeric_value=numeric, unit=unit, counted_entity=counted_entity,
        )

    raise FactValidationError(f"Unhandled fact_type: {fact_type!r}")  # pragma: no cover
