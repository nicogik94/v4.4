"""Deterministic Automation ROI engine (Slice B) — pure, no I/O.

Computes Automation ROI from exactly six resolved, approved inputs using
``Decimal`` arithmetic only (never float). Results are returned unrounded for
reproducibility; rounding is a presentation concern handled by projections (PR2).

Identity:
  * ``formula_input_digest`` is a value-identity fingerprint — equal input values
    plus the same ``FORMULA_VERSION`` produce an equal digest and therefore equal
    outputs.
  * ``provenance_fingerprint`` additionally binds the specific frozen-input ids
    and their fact/decision provenance, so identical values sourced from different
    frozen inputs share a ``formula_input_digest`` but differ in provenance.

Any semantic change to a formula requires a new ``FORMULA_VERSION`` and new
golden-vector tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, getcontext
import hashlib
from typing import Mapping, Optional

getcontext().prec = 28

FORMULA_VERSION = "automation_roi.v1"

# Canonical role ordering — fixed; drives serialization and validation.
ROLES: tuple[str, ...] = (
    "baseline_hours_per_period",
    "post_automation_hours_per_period",
    "fully_loaded_rate_per_hour",
    "periods_per_year",
    "annual_recurring_cost",
    "one_time_implementation_cost",
)
MONEY_ROLES = ("fully_loaded_rate_per_hour", "annual_recurring_cost", "one_time_implementation_cost")
HOURS_ROLES = ("baseline_hours_per_period", "post_automation_hours_per_period")

_FIELD_SEP = "\x1f"
_REC_SEP = "\x1e"
_NULL = "∅"  # ∅


@dataclass(frozen=True)
class ResolvedInput:
    """One approved, frozen input as consumed by the engine."""

    input_role: str
    numeric_value: Decimal
    unit: str = ""
    currency_code: Optional[str] = None
    period: Optional[str] = None
    time_unit: Optional[str] = None
    # provenance (not part of the value digest)
    approved_calculation_input_id: Optional[str] = None
    candidate_fact_revision_id: Optional[str] = None
    approval_decision_id: Optional[str] = None


@dataclass
class RoiComputation:
    formula_version: str
    status: str  # 'valid' | 'not_applicable' | 'blocked'
    roi_percent_status: str  # 'computed' | 'not_applicable' | 'blocked'
    currency_code: Optional[str]
    annual_labor_savings: Optional[Decimal]
    annual_net_benefit: Optional[Decimal]
    first_year_net_benefit: Optional[Decimal]
    first_year_roi_percent: Optional[Decimal]
    formula_input_digest: str
    provenance_fingerprint: str
    diagnostics: dict = field(default_factory=dict)


class RoiInputShapeError(ValueError):
    """The supplied input set is not exactly the six required roles."""


# ─────────────────────── frozen-input database-compatibility contract ───────────────────────
# Stable, non-secret reason codes mirroring the immutable database value-shape
# constraints enforced on ``approved_calculation_input``. These are codes only —
# never SQL, paths, credentials, or dynamic detail — so callers can map them to
# fixed operator-facing messages.
COMPAT_RATE_UNIT = "fully_loaded_rate_per_hour_requires_per_hour"
COMPAT_RATE_CURRENCY = "fully_loaded_rate_per_hour_requires_currency"
COMPAT_HOURS_TIME_UNIT = "hours_role_requires_time_unit_hours"
COMPAT_PERIODS_INTEGRAL = "periods_per_year_requires_integer_at_least_one"
COMPAT_MONEY_CURRENCY = "money_role_requires_currency"
COMPAT_NEGATIVE_VALUE = "value_must_be_non_negative"


def input_compatibility_reason(
    input_role: str,
    *,
    numeric_value,
    unit: Optional[str] = None,
    currency_code: Optional[str] = None,
    time_unit: Optional[str] = None,
) -> Optional[str]:
    """Pure database-compatibility check for one frozen-input role.

    Mirrors the immutable ``approved_calculation_input`` value-shape contract
    exactly (no extra constraints) so an incompatible fact is rejected before the
    INSERT instead of surfacing only as a generic database constraint violation:

      * ``baseline_hours_per_period`` / ``post_automation_hours_per_period``:
        ``time_unit == "hours"`` and ``numeric_value >= 0``;
      * ``fully_loaded_rate_per_hour``: ``unit == "per_hour"``, a currency code,
        and ``numeric_value >= 0``;
      * ``periods_per_year``: integral ``numeric_value >= 1``;
      * ``annual_recurring_cost`` / ``one_time_implementation_cost``: a currency
        code and ``numeric_value >= 0``.

    Returns ``None`` when compatible, otherwise a stable reason code. Roles outside
    the canonical six are deferred to the database (defense in depth) by returning
    ``None`` — this helper never widens the contract.
    """
    value = numeric_value if isinstance(numeric_value, Decimal) else Decimal(numeric_value)

    if input_role in HOURS_ROLES:
        if time_unit != "hours":
            return COMPAT_HOURS_TIME_UNIT
        if value < 0:
            return COMPAT_NEGATIVE_VALUE
        return None

    if input_role == "fully_loaded_rate_per_hour":
        if unit != "per_hour":
            return COMPAT_RATE_UNIT
        if not currency_code:
            return COMPAT_RATE_CURRENCY
        if value < 0:
            return COMPAT_NEGATIVE_VALUE
        return None

    if input_role == "periods_per_year":
        if value != value.to_integral_value() or value < 1:
            return COMPAT_PERIODS_INTEGRAL
        return None

    if input_role in ("annual_recurring_cost", "one_time_implementation_cost"):
        if not currency_code:
            return COMPAT_MONEY_CURRENCY
        if value < 0:
            return COMPAT_NEGATIVE_VALUE
        return None

    return None


def canonical_decimal(value: Decimal) -> str:
    """Stable, exponent-free Decimal text. ``-0`` normalizes to ``0``."""
    d = value if isinstance(value, Decimal) else Decimal(value)
    norm = d.normalize()
    if norm == 0:
        return "0"
    text = format(norm, "f")
    return "0" if text in ("-0", "0E+0") else text


def _require_six_roles(inputs: Mapping[str, ResolvedInput]) -> None:
    keys = set(inputs)
    if keys != set(ROLES):
        missing = sorted(set(ROLES) - keys)
        extra = sorted(keys - set(ROLES))
        raise RoiInputShapeError(
            f"calculation requires exactly the six roles; missing={missing} extra={extra}"
        )
    for role, item in inputs.items():
        if item.input_role != role:
            raise RoiInputShapeError(
                f"input for key {role!r} carries mismatched input_role {item.input_role!r}"
            )


def formula_input_digest(inputs: Mapping[str, ResolvedInput]) -> str:
    """Value-identity fingerprint over canonical-ordered values + FORMULA_VERSION."""
    parts = [FORMULA_VERSION]
    for role in ROLES:
        it = inputs[role]
        parts.append(_FIELD_SEP.join((
            role,
            canonical_decimal(it.numeric_value),
            it.unit or _NULL,
            it.currency_code or _NULL,
            it.period or _NULL,
            it.time_unit or _NULL,
        )))
    return hashlib.sha256(_REC_SEP.join(parts).encode("utf-8")).hexdigest()


def provenance_fingerprint(inputs: Mapping[str, ResolvedInput]) -> str:
    """Provenance fingerprint binding the specific frozen-input/fact/decision ids."""
    parts = [FORMULA_VERSION]
    for role in ROLES:
        it = inputs[role]
        parts.append(_FIELD_SEP.join((
            role,
            it.approved_calculation_input_id or _NULL,
            it.candidate_fact_revision_id or _NULL,
            it.approval_decision_id or _NULL,
        )))
    return hashlib.sha256(_REC_SEP.join(parts).encode("utf-8")).hexdigest()


def compute_automation_roi(
    inputs: Mapping[str, ResolvedInput],
    *,
    unavailable_roles: tuple[str, ...] = (),
) -> RoiComputation:
    """Compute an Automation ROI result from exactly the six required inputs.

    Shape errors (missing/extra/mismatched-role) raise ``RoiInputShapeError`` and
    must be surfaced as HTTP 422 with no persisted result. ``unavailable_roles``
    lists roles whose evidence is unavailable/revoked (service-supplied); any such
    role yields a ``blocked`` result. Otherwise compatibility failures yield
    ``blocked``; zero implementation cost yields ``not_applicable``.
    """
    _require_six_roles(inputs)

    digest = formula_input_digest(inputs)
    provenance = provenance_fingerprint(inputs)
    diagnostics: dict = {}

    def _blocked(reason_key: str, detail: str) -> RoiComputation:
        diagnostics[reason_key] = detail
        return RoiComputation(
            formula_version=FORMULA_VERSION, status="blocked", roi_percent_status="blocked",
            currency_code=None, annual_labor_savings=None, annual_net_benefit=None,
            first_year_net_benefit=None, first_year_roi_percent=None,
            formula_input_digest=digest, provenance_fingerprint=provenance, diagnostics=diagnostics,
        )

    if unavailable_roles:
        return _blocked("unavailable_evidence", f"roles unavailable/revoked: {sorted(unavailable_roles)}")

    # Currency compatibility across money roles.
    currencies = {inputs[r].currency_code for r in MONEY_ROLES}
    if len(currencies) != 1 or None in currencies:
        return _blocked("currency_mismatch", f"money roles must share one currency, got {sorted(str(c) for c in currencies)}")
    currency_code = next(iter(currencies))

    # Hours roles must share time_unit='hours' and the same period basis.
    if any(inputs[r].time_unit != "hours" for r in HOURS_ROLES):
        return _blocked("unit_mismatch", "hours roles must use time_unit='hours'")
    periods = {inputs[r].period for r in HOURS_ROLES}
    if len(periods) != 1:
        return _blocked("period_mismatch", f"hours roles must share one period_basis, got {sorted(str(p) for p in periods)}")

    baseline = inputs["baseline_hours_per_period"].numeric_value
    post = inputs["post_automation_hours_per_period"].numeric_value
    rate = inputs["fully_loaded_rate_per_hour"].numeric_value
    periods_per_year = inputs["periods_per_year"].numeric_value
    recurring = inputs["annual_recurring_cost"].numeric_value
    one_time = inputs["one_time_implementation_cost"].numeric_value

    delta = baseline - post
    annual_labor_savings = delta * rate * periods_per_year
    annual_net_benefit = annual_labor_savings - recurring
    first_year_net_benefit = annual_net_benefit - one_time

    if delta < 0:
        diagnostics["negative_hours_delta"] = "post-automation hours exceed baseline; savings are negative"

    if one_time == 0:
        diagnostics["roi_percent"] = "not applicable: one_time_implementation_cost is zero"
        return RoiComputation(
            formula_version=FORMULA_VERSION, status="not_applicable", roi_percent_status="not_applicable",
            currency_code=currency_code, annual_labor_savings=annual_labor_savings,
            annual_net_benefit=annual_net_benefit, first_year_net_benefit=first_year_net_benefit,
            first_year_roi_percent=None, formula_input_digest=digest,
            provenance_fingerprint=provenance, diagnostics=diagnostics,
        )

    first_year_roi_percent = first_year_net_benefit / one_time * Decimal(100)
    return RoiComputation(
        formula_version=FORMULA_VERSION, status="valid", roi_percent_status="computed",
        currency_code=currency_code, annual_labor_savings=annual_labor_savings,
        annual_net_benefit=annual_net_benefit, first_year_net_benefit=first_year_net_benefit,
        first_year_roi_percent=first_year_roi_percent, formula_input_digest=digest,
        provenance_fingerprint=provenance, diagnostics=diagnostics,
    )
