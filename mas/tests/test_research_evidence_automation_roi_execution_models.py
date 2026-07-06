from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from research_evidence.automation_roi_execution_models import (
    AutomationRoiCalculationResult,
    AutomationRoiExecutionRequest,
)


PROJECT = "00000000-0000-0000-0000-000000000001"
SNAPSHOT = "00000000-0000-0000-0000-000000000002"
DIGEST = "a" * 64


def test_request_accepts_only_three_fields_and_normalizes_identity():
    request = AutomationRoiExecutionRequest(
        project_id=PROJECT,
        input_snapshot_id=SNAPSHOT,
        idempotency_key="  retry-1  ",
    )
    assert request.model_dump() == {
        "project_id": PROJECT,
        "input_snapshot_id": SNAPSHOT,
        "idempotency_key": "retry-1",
    }
    with pytest.raises(ValidationError):
        AutomationRoiExecutionRequest(
            project_id=PROJECT,
            input_snapshot_id=SNAPSHOT,
            idempotency_key="retry-1",
            requested_by="caller-controlled",
        )


@pytest.mark.parametrize("field", ["project_id", "input_snapshot_id"])
def test_request_rejects_non_uuid_scope(field):
    values = {
        "project_id": PROJECT,
        "input_snapshot_id": SNAPSHOT,
        "idempotency_key": "retry-1",
    }
    values[field] = "not-a-uuid"
    with pytest.raises(ValidationError):
        AutomationRoiExecutionRequest(**values)


def _result(**changes):
    values = {
        "id": "00000000-0000-0000-0000-000000000003",
        "project_id": PROJECT,
        "input_snapshot_id": SNAPSHOT,
        "consumer_contract": "deterministic_calculation",
        "binding_set_id": "set-1",
        "idempotency_key": "retry-1",
        "operation_digest": DIGEST,
        "requested_by": "server-actor",
        "computed_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "formula_identifier": "automation_roi",
        "formula_version": "automation_roi.v1",
        "formula_fingerprint": DIGEST,
        "assumption_set_version": "automation_roi.assumptions.v1",
        "assumptions_json": {},
        "input_manifest_json": {},
        "input_digest": DIGEST,
        "provenance_fingerprint": DIGEST,
        "output_units_json": {},
        "status": "valid",
        "currency_code": "USD",
        "annual_labor_savings": Decimal("20800"),
        "annual_net_benefit": Decimal("19800"),
        "first_year_net_benefit": Decimal("14800"),
        "first_year_roi_percent": Decimal("296"),
        "roi_percent_status": "computed",
        "diagnostics_json": {},
    }
    values.update(changes)
    return AutomationRoiCalculationResult(**values)


def test_result_status_shapes_are_strict():
    assert _result().first_year_roi_percent == Decimal("296")
    assert _result(
        status="not_applicable",
        roi_percent_status="not_applicable",
        first_year_roi_percent=None,
    ).status == "not_applicable"
    assert _result(
        status="blocked",
        roi_percent_status="blocked",
        currency_code=None,
        annual_labor_savings=None,
        annual_net_benefit=None,
        first_year_net_benefit=None,
        first_year_roi_percent=None,
    ).status == "blocked"
    with pytest.raises(ValidationError):
        _result(status="blocked", roi_percent_status="blocked")


def test_result_rejects_noncanonical_digests_and_unknown_fields():
    with pytest.raises(ValidationError):
        _result(operation_digest="ABC")
    with pytest.raises(ValidationError):
        AutomationRoiCalculationResult(**_result().model_dump(), output="forged")
