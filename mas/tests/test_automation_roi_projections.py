"""Pure (no-database) tests for the Slice B PR2 read projections.

Exercise the operator audit projection and the allowlist-only client-safe
projection directly against synthetic bundles, so the allowlist boundary is
proven without a PostgreSQL dependency.
"""
import json
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from knowledge.automation_roi import projections  # noqa: E402
from knowledge.automation_roi.calculator import ROLES  # noqa: E402

# Sentinels for internal-only fields that must never reach the client view.
SECRET_FACT_UUID = "11111111-1111-1111-1111-111111111111"
SECRET_INPUT_UUID = "22222222-2222-2222-2222-222222222222"
SECRET_DECISION_UUID = "33333333-3333-3333-3333-333333333333"
SECRET_LOCATOR = "SECRET_SOURCE_LOCATOR_/var/store/file.csv#cell"
SECRET_RANGE = "SECRET_CHAR_RANGE_10:42"
SECRET_RATIONALE = "SECRET_EXTRACTION_RATIONALE"
SECRET_ACTOR = "SECRET_OPERATOR_IDENTITY"
SECRET_DIAGNOSTIC = "SECRET_INTERNAL_DIAGNOSTIC_DETAIL"
SECRET_DIGEST = "deadbeefdigest"
SECRET_PROVENANCE = "deadbeefprovenance"

ALL_SECRETS = [
    SECRET_FACT_UUID, SECRET_INPUT_UUID, SECRET_DECISION_UUID, SECRET_LOCATOR,
    SECRET_RANGE, SECRET_RATIONALE, SECRET_ACTOR, SECRET_DIAGNOSTIC,
    SECRET_DIGEST, SECRET_PROVENANCE,
]

_RESOLVED = {
    "baseline_hours_per_period": (Decimal("10"), "", None, "week", "hours"),
    "post_automation_hours_per_period": (Decimal("2"), "", None, "week", "hours"),
    "fully_loaded_rate_per_hour": (Decimal("50"), "per_hour", "USD", None, None),
    "periods_per_year": (Decimal("52"), "", None, None, None),
    "annual_recurring_cost": (Decimal("1000"), "", "USD", None, None),
    "one_time_implementation_cost": (Decimal("5000"), "", "USD", None, None),
}


def _input(role, *, available=True):
    value, unit, currency, period, time_unit = _RESOLVED[role]
    return {
        "input_role": role,
        "approved_calculation_input_id": SECRET_INPUT_UUID,
        "candidate_fact_revision_id": SECRET_FACT_UUID,
        "approval_decision_id": SECRET_DECISION_UUID,
        "resolved_numeric_value": value,
        "resolved_unit": unit,
        "resolved_currency_code": currency,
        "resolved_period": period,
        "resolved_time_unit": time_unit,
        "as_of_date": date(2026, 1, 1),
        "subject_label": "Process X",
        "metric_label": role,
        "period_basis": period,
        "source_locator": SECRET_LOCATOR,
        "source_char_range": SECRET_RANGE,
        "extraction_rationale": SECRET_RATIONALE,
        "extracted_by": SECRET_ACTOR,
        "decision_type": "approve",
        "decided_by": SECRET_ACTOR,
        "decided_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
        "decision_reason": "looks right",
        "decision_seq": 1,
        "available": available,
    }


def _bundle(status="valid", *, diagnostics=None, roi_percent=Decimal("296"),
            unavailable_roles=()):
    diagnostics = diagnostics or {}
    if status == "blocked":
        result = {
            "id": "rid", "status": "blocked", "formula_version": "automation_roi.v1",
            "currency_code": None, "annual_labor_savings": None, "annual_net_benefit": None,
            "first_year_net_benefit": None, "first_year_roi_percent": None,
            "roi_percent_status": "blocked", "formula_input_digest": SECRET_DIGEST,
            "provenance_fingerprint": SECRET_PROVENANCE, "diagnostics": diagnostics,
            "computed_by": SECRET_ACTOR, "computed_at": datetime(2026, 1, 3, tzinfo=timezone.utc),
        }
    else:
        result = {
            "id": "rid", "status": status, "formula_version": "automation_roi.v1",
            "currency_code": "USD", "annual_labor_savings": Decimal("20800"),
            "annual_net_benefit": Decimal("19800"),
            "first_year_net_benefit": Decimal("14800"),
            "first_year_roi_percent": roi_percent if status == "valid" else None,
            "roi_percent_status": "computed" if status == "valid" else "not_applicable",
            "formula_input_digest": SECRET_DIGEST, "provenance_fingerprint": SECRET_PROVENANCE,
            "diagnostics": diagnostics, "computed_by": SECRET_ACTOR,
            "computed_at": datetime(2026, 1, 3, tzinfo=timezone.utc),
        }
    inputs = [_input(r, available=r not in unavailable_roles) for r in ROLES]
    return {"project_id": "pid", "result": result, "inputs": inputs}


# ─────────────────────────── operator projection ───────────────────────────

def test_operator_projection_includes_required_audit_data():
    proj = projections.operator_projection(_bundle("valid", diagnostics={"note": "x"}))

    assert proj["schema_version"] == projections.OPERATOR_PROJECTION_SCHEMA_VERSION
    assert proj["status"] == "valid"
    assert proj["formula_version"] == "automation_roi.v1"
    assert proj["formula_input_digest"] == SECRET_DIGEST
    assert proj["provenance_fingerprint"] == SECRET_PROVENANCE
    assert proj["diagnostics"] == {"note": "x"}
    assert proj["all_evidence_available"] is True
    assert proj["annual_labor_savings"] == "20800"

    assert len(proj["inputs"]) == len(ROLES)
    sample = proj["inputs"][0]
    # Audit identifiers, provenance, availability, and decision metadata present.
    for key in (
        "approved_calculation_input_id", "candidate_fact_revision_id",
        "approval_decision_id", "available", "source_locator", "approval_decision",
    ):
        assert key in sample
    assert sample["approval_decision"]["decided_by"] == SECRET_ACTOR
    # Decimals are serialized as strings (never float).
    assert isinstance(sample["resolved_numeric_value"], str)


def test_operator_projection_reports_availability_for_tombstoned_evidence():
    proj = projections.operator_projection(
        _bundle("blocked", unavailable_roles=("fully_loaded_rate_per_hour",))
    )
    assert proj["status"] == "blocked"
    assert proj["all_evidence_available"] is False
    unavailable = [i for i in proj["inputs"] if not i["available"]]
    assert len(unavailable) == 1
    assert unavailable[0]["input_role"] == "fully_loaded_rate_per_hour"


# ─────────────────────────── client projection ───────────────────────────

def _assert_no_secrets(payload):
    blob = json.dumps(payload)
    for secret in ALL_SECRETS:
        assert secret not in blob, f"client view leaked internal value: {secret}"


def test_client_projection_valid_excludes_internal_only_fields():
    proj = projections.client_projection(
        _bundle("valid", diagnostics={
            "currency_mismatch": SECRET_DIAGNOSTIC,  # non-allowlisted → dropped
            "negative_hours_delta": "ignored detail",  # allowlisted → safe sentence
        })
    )
    _assert_no_secrets(proj)

    assert proj["schema_version"] == projections.CLIENT_PROJECTION_SCHEMA_VERSION
    assert proj["status"] == "valid"
    assert proj["result"]["currency"] == "USD"
    assert proj["result"]["annual_labor_savings"] == "20800.00"
    assert proj["result"]["first_year_roi_percent"] == "296.00"

    # Allowlisted caveat present by its safe wording; raw diagnostic text absent.
    assert any("baseline" in c for c in proj["caveats"])
    assert SECRET_DIAGNOSTIC not in json.dumps(proj["caveats"])

    # Assumptions carry safe labels + values but no ids, locators, or actor identity.
    assert len(proj["assumptions"]) == len(ROLES)
    keys = set().union(*(a.keys() for a in proj["assumptions"]))
    assert keys == {"role", "label", "value", "unit", "currency", "period"}
    labels = [a["label"] for a in proj["assumptions"]]
    assert all(label and "Process X" in label for label in labels)


def test_client_projection_not_applicable_has_caveat_and_no_roi_percent():
    proj = projections.client_projection(_bundle("not_applicable"))
    assert proj["status"] == "not_applicable"
    assert proj["result"]["first_year_roi_percent"] is None
    assert any("not applicable" in c.lower() for c in proj["caveats"])


def test_client_projection_blocked_omits_values_and_source_content():
    proj = projections.client_projection(
        _bundle("blocked", unavailable_roles=tuple(ROLES))
    )
    _assert_no_secrets(proj)
    assert proj["status"] == "blocked"
    assert proj["result"] is None
    assert proj["assumptions"] == []
    assert len(proj["caveats"]) == 1
    # A single safe availability caveat — no source content or reason detail.
    assert "cannot be shown" in proj["caveats"][0]


def test_client_projection_omits_label_when_source_unavailable():
    # not_applicable result whose one input's evidence has become unavailable:
    # the numeric assumption may remain, but its source label must be withheld.
    bundle = _bundle("not_applicable", unavailable_roles=("periods_per_year",))
    proj = projections.client_projection(bundle)
    by_role = {a["role"]: a for a in proj["assumptions"]}
    assert by_role["periods_per_year"]["label"] is None
    assert by_role["periods_per_year"]["value"] == "52"
    assert by_role["baseline_hours_per_period"]["label"] is not None


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
