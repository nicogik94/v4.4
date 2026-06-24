"""Pure unit + golden-vector tests for the deterministic Automation ROI engine.

No database required.
"""
import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from knowledge.automation_roi.calculator import (  # noqa: E402
    COMPAT_HOURS_TIME_UNIT,
    COMPAT_MONEY_CURRENCY,
    COMPAT_NEGATIVE_VALUE,
    COMPAT_PERIODS_INTEGRAL,
    COMPAT_RATE_CURRENCY,
    COMPAT_RATE_UNIT,
    FORMULA_VERSION,
    ROLES,
    ResolvedInput,
    RoiInputShapeError,
    canonical_decimal,
    canonical_request_digest,
    compute_automation_roi,
    formula_input_digest,
    input_compatibility_reason,
    provenance_fingerprint,
)


def _inputs(
    *, baseline="10", post="2", rate="50", periods="52", recurring="1000", one_time="5000",
    rate_ccy="USD", recurring_ccy="USD", one_time_ccy="USD",
    baseline_period="week", post_period="week", ids=False,
):
    def pid(role):
        return f"aci-{role}" if ids else None

    return {
        "baseline_hours_per_period": ResolvedInput(
            "baseline_hours_per_period", Decimal(baseline), time_unit="hours",
            period=baseline_period, approved_calculation_input_id=pid("b")),
        "post_automation_hours_per_period": ResolvedInput(
            "post_automation_hours_per_period", Decimal(post), time_unit="hours",
            period=post_period, approved_calculation_input_id=pid("p")),
        "fully_loaded_rate_per_hour": ResolvedInput(
            "fully_loaded_rate_per_hour", Decimal(rate), unit="per_hour",
            currency_code=rate_ccy, approved_calculation_input_id=pid("r")),
        "periods_per_year": ResolvedInput(
            "periods_per_year", Decimal(periods), approved_calculation_input_id=pid("y")),
        "annual_recurring_cost": ResolvedInput(
            "annual_recurring_cost", Decimal(recurring), currency_code=recurring_ccy,
            approved_calculation_input_id=pid("rc")),
        "one_time_implementation_cost": ResolvedInput(
            "one_time_implementation_cost", Decimal(one_time), currency_code=one_time_ccy,
            approved_calculation_input_id=pid("ot")),
    }


class TestGoldenVectors(unittest.TestCase):
    def test_valid_golden_vector(self):
        r = compute_automation_roi(_inputs())
        self.assertEqual(r.status, "valid")
        self.assertEqual(r.roi_percent_status, "computed")
        self.assertEqual(r.currency_code, "USD")
        self.assertEqual(r.annual_labor_savings, Decimal("20800"))
        self.assertEqual(r.annual_net_benefit, Decimal("19800"))
        self.assertEqual(r.first_year_net_benefit, Decimal("14800"))
        self.assertEqual(canonical_decimal(r.first_year_roi_percent), "296")
        self.assertEqual(r.formula_version, FORMULA_VERSION)

    def test_zero_implementation_cost_is_not_applicable(self):
        r = compute_automation_roi(_inputs(one_time="0"))
        self.assertEqual(r.status, "not_applicable")
        self.assertEqual(r.roi_percent_status, "not_applicable")
        self.assertEqual(r.annual_labor_savings, Decimal("20800"))
        self.assertEqual(r.first_year_net_benefit, Decimal("19800"))
        self.assertIsNone(r.first_year_roi_percent)

    def test_negative_hours_delta_is_valid_with_caveat(self):
        r = compute_automation_roi(_inputs(baseline="2", post="10"))
        self.assertEqual(r.status, "valid")
        self.assertEqual(r.annual_labor_savings, Decimal("-20800"))
        self.assertIn("negative_hours_delta", r.diagnostics)

    def test_currency_mismatch_is_blocked(self):
        r = compute_automation_roi(_inputs(recurring_ccy="EUR"))
        self.assertEqual(r.status, "blocked")
        self.assertIsNone(r.first_year_roi_percent)
        self.assertIn("currency_mismatch", r.diagnostics)

    def test_period_mismatch_is_blocked(self):
        r = compute_automation_roi(_inputs(post_period="month"))
        self.assertEqual(r.status, "blocked")
        self.assertIn("period_mismatch", r.diagnostics)

    def test_unavailable_role_is_blocked(self):
        r = compute_automation_roi(_inputs(), unavailable_roles=("annual_recurring_cost",))
        self.assertEqual(r.status, "blocked")
        self.assertIn("unavailable_evidence", r.diagnostics)


class TestShapeErrors(unittest.TestCase):
    def test_missing_role_raises(self):
        bad = _inputs()
        del bad["periods_per_year"]
        with self.assertRaises(RoiInputShapeError):
            compute_automation_roi(bad)

    def test_extra_role_raises(self):
        bad = _inputs()
        bad["surplus_role"] = bad["periods_per_year"]
        with self.assertRaises(RoiInputShapeError):
            compute_automation_roi(bad)

    def test_wrong_role_keying_raises(self):
        bad = _inputs()
        # value filed under the wrong key (its input_role disagrees with the key)
        bad["periods_per_year"] = bad["annual_recurring_cost"]
        with self.assertRaises(RoiInputShapeError):
            compute_automation_roi(bad)


class TestFingerprints(unittest.TestCase):
    def test_identical_values_identical_digest_and_outputs(self):
        a = compute_automation_roi(_inputs())
        b = compute_automation_roi(_inputs())
        self.assertEqual(a.formula_input_digest, b.formula_input_digest)
        self.assertEqual(a.first_year_net_benefit, b.first_year_net_benefit)

    def test_same_values_different_ids_share_value_digest_differ_in_provenance(self):
        no_ids = _inputs(ids=False)
        with_ids = _inputs(ids=True)
        self.assertEqual(
            formula_input_digest(no_ids), formula_input_digest(with_ids),
            "value digest must ignore frozen-input ids",
        )
        self.assertNotEqual(
            provenance_fingerprint(no_ids), provenance_fingerprint(with_ids),
            "provenance must bind frozen-input ids",
        )

    def test_value_change_changes_value_digest(self):
        self.assertNotEqual(
            formula_input_digest(_inputs(rate="50")),
            formula_input_digest(_inputs(rate="60")),
        )


class TestCanonicalRequestDigest(unittest.TestCase):
    """The exact calculation-operation identity (v49 idempotency)."""

    PID = "11111111-1111-1111-1111-111111111111"

    def _map(self, **over):
        base = {role: f"aci-{role}" for role in ROLES}
        base.update(over)
        return base

    def test_stable_under_role_insertion_order(self):
        ordered = {role: f"aci-{role}" for role in ROLES}
        shuffled = {role: f"aci-{role}" for role in reversed(ROLES)}
        self.assertEqual(
            canonical_request_digest(self.PID, ordered),
            canonical_request_digest(self.PID, shuffled),
        )

    def test_is_64_char_hex(self):
        d = canonical_request_digest(self.PID, self._map())
        self.assertEqual(len(d), 64)
        int(d, 16)  # hex

    def test_changing_one_input_id_changes_digest(self):
        self.assertNotEqual(
            canonical_request_digest(self.PID, self._map()),
            canonical_request_digest(self.PID, self._map(periods_per_year="aci-OTHER")),
        )

    def test_changing_project_changes_digest(self):
        other = "22222222-2222-2222-2222-222222222222"
        self.assertNotEqual(
            canonical_request_digest(self.PID, self._map()),
            canonical_request_digest(other, self._map()),
        )

    def test_distinct_ids_same_values_differ(self):
        # Two operations whose VALUES are identical but whose frozen-input ids
        # differ must produce different operation identities — this is exactly the
        # case the value-based formula_input_digest cannot separate, which is why it
        # must never be a uniqueness key.
        a = self._map()
        b = {role: f"other-{role}" for role in ROLES}
        self.assertNotEqual(
            canonical_request_digest(self.PID, a),
            canonical_request_digest(self.PID, b),
        )

    def test_requires_exactly_six_roles(self):
        short = {role: f"aci-{role}" for role in list(ROLES)[:5]}
        with self.assertRaises(ValueError):
            canonical_request_digest(self.PID, short)
        extra = self._map(seventh="aci-x")
        with self.assertRaises(ValueError):
            canonical_request_digest(self.PID, extra)


class TestCanonicalDecimal(unittest.TestCase):
    def test_canonical_forms(self):
        self.assertEqual(canonical_decimal(Decimal("296.00")), "296")
        self.assertEqual(canonical_decimal(Decimal("-0")), "0")
        self.assertEqual(canonical_decimal(Decimal("0.425")), "0.425")
        self.assertEqual(canonical_decimal(Decimal("20800")), "20800")
        self.assertEqual(canonical_decimal(Decimal("1E+2")), "100")


class TestInputCompatibility(unittest.TestCase):
    """The shared frozen-input database-compatibility contract (pure, no I/O)."""

    # ─── hours roles: time_unit == 'hours' and value >= 0 ───
    def test_hours_roles_compatible(self):
        for role in ("baseline_hours_per_period", "post_automation_hours_per_period"):
            self.assertIsNone(input_compatibility_reason(
                role, numeric_value=Decimal("0"), time_unit="hours"))
            self.assertIsNone(input_compatibility_reason(
                role, numeric_value=Decimal("10"), time_unit="hours"))

    def test_hours_roles_wrong_time_unit_incompatible(self):
        self.assertEqual(
            input_compatibility_reason(
                "baseline_hours_per_period", numeric_value=Decimal("10"), time_unit="minutes"),
            COMPAT_HOURS_TIME_UNIT,
        )

    def test_hours_roles_negative_value_incompatible(self):
        self.assertEqual(
            input_compatibility_reason(
                "post_automation_hours_per_period", numeric_value=Decimal("-1"), time_unit="hours"),
            COMPAT_NEGATIVE_VALUE,
        )

    # ─── fully_loaded_rate_per_hour: unit == 'per_hour' + currency + value >= 0 ───
    def test_rate_per_hour_with_usd_and_non_negative_is_compatible(self):
        self.assertIsNone(input_compatibility_reason(
            "fully_loaded_rate_per_hour", numeric_value=Decimal("50"),
            unit="per_hour", currency_code="USD"))
        self.assertIsNone(input_compatibility_reason(
            "fully_loaded_rate_per_hour", numeric_value=Decimal("0"),
            unit="per_hour", currency_code="USD"))

    def test_rate_usd_per_hour_unit_is_incompatible(self):
        self.assertEqual(
            input_compatibility_reason(
                "fully_loaded_rate_per_hour", numeric_value=Decimal("50"),
                unit="USD/hour", currency_code="USD"),
            COMPAT_RATE_UNIT,
        )

    def test_rate_per_hou_unit_is_incompatible(self):
        self.assertEqual(
            input_compatibility_reason(
                "fully_loaded_rate_per_hour", numeric_value=Decimal("50"),
                unit="per_hou", currency_code="USD"),
            COMPAT_RATE_UNIT,
        )

    def test_rate_without_currency_is_incompatible(self):
        self.assertEqual(
            input_compatibility_reason(
                "fully_loaded_rate_per_hour", numeric_value=Decimal("50"),
                unit="per_hour", currency_code=None),
            COMPAT_RATE_CURRENCY,
        )

    def test_rate_negative_value_is_incompatible(self):
        self.assertEqual(
            input_compatibility_reason(
                "fully_loaded_rate_per_hour", numeric_value=Decimal("-1"),
                unit="per_hour", currency_code="USD"),
            COMPAT_NEGATIVE_VALUE,
        )

    # ─── periods_per_year: integral value >= 1 ───
    def test_periods_per_year_compatible(self):
        self.assertIsNone(input_compatibility_reason(
            "periods_per_year", numeric_value=Decimal("52")))
        self.assertIsNone(input_compatibility_reason(
            "periods_per_year", numeric_value=Decimal("1")))

    def test_periods_per_year_non_integral_or_below_one_incompatible(self):
        self.assertEqual(
            input_compatibility_reason("periods_per_year", numeric_value=Decimal("52.5")),
            COMPAT_PERIODS_INTEGRAL,
        )
        self.assertEqual(
            input_compatibility_reason("periods_per_year", numeric_value=Decimal("0")),
            COMPAT_PERIODS_INTEGRAL,
        )

    # ─── annual_recurring_cost / one_time_implementation_cost: currency + value >= 0 ───
    def test_money_roles_compatible(self):
        for role in ("annual_recurring_cost", "one_time_implementation_cost"):
            self.assertIsNone(input_compatibility_reason(
                role, numeric_value=Decimal("0"), currency_code="USD"))
            self.assertIsNone(input_compatibility_reason(
                role, numeric_value=Decimal("1000"), currency_code="USD"))

    def test_money_roles_without_currency_incompatible(self):
        self.assertEqual(
            input_compatibility_reason(
                "annual_recurring_cost", numeric_value=Decimal("1000"), currency_code=None),
            COMPAT_MONEY_CURRENCY,
        )

    def test_money_roles_negative_value_incompatible(self):
        self.assertEqual(
            input_compatibility_reason(
                "one_time_implementation_cost", numeric_value=Decimal("-5"), currency_code="USD"),
            COMPAT_NEGATIVE_VALUE,
        )

    def test_unknown_role_defers_to_database(self):
        # Roles outside the canonical six are not widened by this helper.
        self.assertIsNone(input_compatibility_reason(
            "not_a_real_role", numeric_value=Decimal("1")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
