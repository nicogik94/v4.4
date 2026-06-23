"""Flag-gate and payload-validation tests for the Slice B PR2 API (no database).

These prove two boundaries without touching PostgreSQL:

* with ``MAS_AUTOMATION_ROI_ENABLED`` off, every new endpoint returns 404 and
  never opens a database connection (so nothing can be written);
* with the flag on, malformed payloads (extra fields, bad enums, an invalid
  exact-six calculation map) are rejected with 422 *before* any connection is
  opened, so no CalculationResult or other record is written.

``open_connection`` is replaced with a tripwire that fails the test if any route
reaches the database.
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api  # noqa: E402
import automation_roi_api  # noqa: E402


PID = "00000000-0000-0000-0000-0000000000aa"
FID = "00000000-0000-0000-0000-0000000000bb"
RID = "00000000-0000-0000-0000-0000000000cc"
SNAP = "00000000-0000-0000-0000-0000000000dd"
IID = "00000000-0000-0000-0000-0000000000ee"

VALID_SIX = {
    "baseline_hours_per_period": "00000000-0000-0000-0000-000000000001",
    "post_automation_hours_per_period": "00000000-0000-0000-0000-000000000002",
    "fully_loaded_rate_per_hour": "00000000-0000-0000-0000-000000000003",
    "periods_per_year": "00000000-0000-0000-0000-000000000004",
    "annual_recurring_cost": "00000000-0000-0000-0000-000000000005",
    "one_time_implementation_cost": "00000000-0000-0000-0000-000000000006",
}

WRITE_REQUESTS = [
    ("post", f"/projects/{PID}/automation-roi/candidate-facts",
     {"source_snapshot_id": SNAP, "fact": {"fact_type": "count", "value": "52",
                                           "counted_entity": "weeks"},
      "subject_label": "Process X", "metric_label": "periods"}),
    ("post", f"/projects/{PID}/automation-roi/candidate-facts/{FID}/decisions",
     {"decision_type": "approve"}),
    ("post", f"/projects/{PID}/automation-roi/inputs",
     {"candidate_fact_revision_id": FID, "approval_decision_id": RID,
      "input_role": "periods_per_year"}),
    ("post", f"/projects/{PID}/automation-roi/calculations", {"inputs": VALID_SIX}),
]
READ_REQUESTS = [
    ("get", f"/projects/{PID}/automation-roi/calculations/{RID}", None),
    ("get", f"/projects/{PID}/automation-roi/calculations/{RID}/client", None),
    ("get", f"/projects/{PID}/automation-roi/workspace", None),
]
ALL_REQUESTS = WRITE_REQUESTS + READ_REQUESTS


@pytest.fixture
def tripwire(monkeypatch):
    """Fail loudly if any route tries to open a database connection."""
    def _boom(*_a, **_k):
        raise AssertionError("open_connection must not be called in this test")

    monkeypatch.setattr(automation_roi_api, "open_connection", _boom)
    client = TestClient(api.app)
    try:
        yield client
    finally:
        client.close()


def _clear_auth(monkeypatch):
    monkeypatch.delenv("MAS_REQUIRE_OPERATOR_AUTH", raising=False)
    monkeypatch.delenv("MAS_OPERATOR_API_KEY", raising=False)


def _send(client, method, path, body):
    if method == "get":
        return client.get(path)
    return client.post(path, json=body)


# ─────────────────────────── flag off → 404, no DB ───────────────────────────

@pytest.mark.parametrize("method,path,body", ALL_REQUESTS)
def test_flag_off_returns_404_and_opens_no_connection(monkeypatch, tripwire, method, path, body):
    _clear_auth(monkeypatch)
    monkeypatch.delenv("MAS_AUTOMATION_ROI_ENABLED", raising=False)

    resp = _send(tripwire, method, path, body)

    assert resp.status_code == 404
    # The tripwire guarantees no connection was opened, hence nothing was written.


# ─────────────────────────── flag on → 422 before DB ───────────────────────────

@pytest.fixture
def flag_on(monkeypatch, tripwire):
    _clear_auth(monkeypatch)
    monkeypatch.setenv("MAS_AUTOMATION_ROI_ENABLED", "true")
    return tripwire


def test_candidate_fact_rejects_unknown_field(flag_on):
    resp = flag_on.post(
        f"/projects/{PID}/automation-roi/candidate-facts",
        json={"source_snapshot_id": SNAP, "fact": {"fact_type": "count", "value": "52",
                                                   "counted_entity": "weeks"},
              "subject_label": "Process X", "metric_label": "periods",
              "raw_excerpt": "copied source text"},
    )
    assert resp.status_code == 422


def test_candidate_fact_rejects_unknown_fact_field(flag_on):
    resp = flag_on.post(
        f"/projects/{PID}/automation-roi/candidate-facts",
        json={"source_snapshot_id": SNAP,
              "fact": {"fact_type": "count", "value": "52", "counted_entity": "weeks",
                       "smuggled": "x"},
              "subject_label": "Process X", "metric_label": "periods"},
    )
    assert resp.status_code == 422


def test_candidate_fact_rejects_float_value(flag_on):
    resp = flag_on.post(
        f"/projects/{PID}/automation-roi/candidate-facts",
        json={"source_snapshot_id": SNAP,
              "fact": {"fact_type": "money", "value": 12.5, "currency_code": "USD",
                       "as_of_date": "2026-01-01"},
              "subject_label": "Process X", "metric_label": "cost"},
    )
    assert resp.status_code == 422


def test_decision_rejects_unknown_decision_type(flag_on):
    resp = flag_on.post(
        f"/projects/{PID}/automation-roi/candidate-facts/{FID}/decisions",
        json={"decision_type": "supersede"},
    )
    assert resp.status_code == 422


def test_decision_rejects_client_supplied_sequence(flag_on):
    resp = flag_on.post(
        f"/projects/{PID}/automation-roi/candidate-facts/{FID}/decisions",
        json={"decision_type": "approve", "decision_seq": 7},
    )
    assert resp.status_code == 422


def test_freeze_rejects_extra_resolved_fields(flag_on):
    resp = flag_on.post(
        f"/projects/{PID}/automation-roi/inputs",
        json={"candidate_fact_revision_id": FID, "approval_decision_id": RID,
              "input_role": "periods_per_year", "resolved_numeric_value": "999"},
    )
    assert resp.status_code == 422


def test_freeze_rejects_unknown_role(flag_on):
    resp = flag_on.post(
        f"/projects/{PID}/automation-roi/inputs",
        json={"candidate_fact_revision_id": FID, "approval_decision_id": RID,
              "input_role": "made_up_role"},
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("bad_inputs", [
    {k: v for k, v in VALID_SIX.items() if k != "periods_per_year"},  # missing one
    {**VALID_SIX, "extra_role": "00000000-0000-0000-0000-000000000007"},  # extra
])
def test_calculation_rejects_non_exact_six_map(flag_on, bad_inputs):
    resp = flag_on.post(
        f"/projects/{PID}/automation-roi/calculations", json={"inputs": bad_inputs}
    )
    assert resp.status_code == 422


def test_calculation_rejects_client_supplied_result_fields(flag_on):
    resp = flag_on.post(
        f"/projects/{PID}/automation-roi/calculations",
        json={"inputs": VALID_SIX, "status": "valid", "formula_version": "x"},
    )
    assert resp.status_code == 422


def test_calculation_rejects_non_uuid_input_id(flag_on):
    resp = flag_on.post(
        f"/projects/{PID}/automation-roi/calculations",
        json={"inputs": {**VALID_SIX, "periods_per_year": "not-a-uuid"}},
    )
    assert resp.status_code == 422


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
