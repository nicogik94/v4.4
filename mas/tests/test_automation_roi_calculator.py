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
    FORMULA_VERSION,
    ResolvedInput,
    RoiInputShapeError,
    canonical_decimal,
    compute_automation_roi,
    formula_input_digest,
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


class TestCanonicalDecimal(unittest.TestCase):
    def test_canonical_forms(self):
        self.assertEqual(canonical_decimal(Decimal("296.00")), "296")
        self.assertEqual(canonical_decimal(Decimal("-0")), "0")
        self.assertEqual(canonical_decimal(Decimal("0.425")), "0.425")
        self.assertEqual(canonical_decimal(Decimal("20800")), "20800")
        self.assertEqual(canonical_decimal(Decimal("1E+2")), "100")


if __name__ == "__main__":
    unittest.main(verbosity=2)
