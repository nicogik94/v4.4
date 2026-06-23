"""PostgreSQL-backed end-to-end tests for the Slice B PR2 operator API.

Drives the real FastAPI routes against a disposable database (TEST_EVIDENCE_PG_DSN)
with ephemeral schemas dropped on exit. ``open_connection`` is redirected to the
ephemeral schema; the authoritative MAS database is never touched. Skipped when no
DSN is configured.

Covered: full happy-path lifecycle (snapshot → ROI fact/context → approve → six
freezes → calculate → operator read → client read), candidate-fact snapshot
constraints, decision lifecycle and illegal transitions, freeze eligibility /
availability / cross-project rules, and blocked / not_applicable calculation
behavior reusing the PR1 engine.
"""
import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests.evidence_snapshot_pg as pg  # noqa: E402
import api  # noqa: E402
import automation_roi_api  # noqa: E402
from knowledge.evidence_snapshot import repository as ev_repo  # noqa: E402

AS_OF = "2026-01-01"

# Role → (candidate-fact JSON payload, context period_basis).
_HOURS = ("baseline_hours_per_period", "post_automation_hours_per_period")


def _fact_payload(role, *, value, currency="USD"):
    if role in _HOURS:
        return {"fact_type": "duration", "value": value, "time_unit": "hours"}, "week"
    if role == "fully_loaded_rate_per_hour":
        return ({"fact_type": "money", "value": value, "currency_code": currency,
                 "as_of_date": AS_OF, "unit": "per_hour"}, None)
    if role == "periods_per_year":
        return {"fact_type": "count", "value": value, "counted_entity": "weeks per year"}, None
    if role in ("annual_recurring_cost", "one_time_implementation_cost"):
        return ({"fact_type": "money", "value": value, "currency_code": currency,
                 "as_of_date": AS_OF}, None)
    raise ValueError(role)


_DEFAULT_VALUES = {
    "baseline_hours_per_period": "10",
    "post_automation_hours_per_period": "2",
    "fully_loaded_rate_per_hour": "50",
    "periods_per_year": "52",
    "annual_recurring_cost": "1000",
    "one_time_implementation_cost": "5000",
}


# ─────────────────────────────── fixtures ───────────────────────────────

@pytest.fixture
def conn():
    pg.require_dsn()
    c = pg.connect()
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def schema_b(conn):
    with pg.fresh_schema(conn) as s:
        pg.apply_v48(conn)
        yield s


@pytest.fixture
def client(schema_b, monkeypatch):
    monkeypatch.setenv("MAS_AUTOMATION_ROI_ENABLED", "true")
    monkeypatch.delenv("MAS_REQUIRE_OPERATOR_AUTH", raising=False)
    monkeypatch.delenv("MAS_OPERATOR_API_KEY", raising=False)
    monkeypatch.setattr(
        automation_roi_api, "open_connection", lambda: pg.connect(schema=schema_b)
    )
    c = TestClient(api.app)
    try:
        yield c
    finally:
        c.close()


# ─────────────────────────────── helpers ───────────────────────────────

def _seed_project(conn, name="roi-api"):
    pid = pg.insert_project(conn, name=name)
    conn.commit()
    return pid


def _seed_snapshot(conn, pid, *, tag="s"):
    blob = ev_repo.insert_or_get_blob(conn, project_id=pid, content_hash=f"h-{tag}", byte_size=8)
    snap = ev_repo.insert_snapshot(
        conn, source_blob_id=blob, project_id=pid, storage_ref=f"/store/{tag}",
    )
    conn.commit()
    return snap


def _create_fact(client, pid, snap, role, *, value=None, currency="USD"):
    payload, period = _fact_payload(role, value=value or _DEFAULT_VALUES[role], currency=currency)
    body = {
        "source_snapshot_id": snap,
        "fact": payload,
        "subject_label": "Process X",
        "metric_label": role,
        "source_locator": f"doc#{role}",
        "extraction_rationale": "operator extracted",
    }
    if period is not None:
        body["period_basis"] = period
    resp = client.post(f"/projects/{pid}/automation-roi/candidate-facts", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["candidate_fact_revision_id"]


def _approve(client, pid, fact_id):
    resp = client.post(
        f"/projects/{pid}/automation-roi/candidate-facts/{fact_id}/decisions",
        json={"decision_type": "approve"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["approval_decision_id"]


def _freeze(client, pid, fact_id, decision_id, role):
    resp = client.post(
        f"/projects/{pid}/automation-roi/inputs",
        json={"candidate_fact_revision_id": fact_id, "approval_decision_id": decision_id,
              "input_role": role},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["approved_calculation_input_id"]


def _freeze_six(client, conn, pid, *, overrides=None):
    overrides = overrides or {}
    inputs = {}
    facts = {}
    for role in _DEFAULT_VALUES:
        snap = _seed_snapshot(conn, pid, tag=role)
        fid = _create_fact(client, pid, snap, role, value=overrides.get(role))
        did = _approve(client, pid, fid)
        inputs[role] = _freeze(client, pid, fid, did, role)
        facts[role] = (fid, did, snap)
    return inputs, facts


# ─────────────────────────────── happy path ───────────────────────────────

def test_full_lifecycle_operator_and_client_reads(client, conn, schema_b):
    pid = _seed_project(conn)
    inputs, _facts = _freeze_six(client, conn, pid)

    resp = client.post(f"/projects/{pid}/automation-roi/calculations", json={"inputs": inputs})
    assert resp.status_code == 201, resp.text
    created = resp.json()
    rid = created["result_id"]
    assert created["status"] == "valid"

    # Operator read: full audit data present.
    op = client.get(f"/projects/{pid}/automation-roi/calculations/{rid}").json()
    assert op["status"] == "valid"
    assert op["annual_labor_savings"] == "20800"
    # Engine division yields scale-2 Decimal('296.00'); serialized full-precision.
    assert op["first_year_roi_percent"] == "296.00"
    assert op["formula_version"] == "automation_roi.v1"
    assert op["all_evidence_available"] is True
    assert len(op["inputs"]) == 6
    assert all(i["approval_decision"]["decision_type"] == "approve" for i in op["inputs"])
    assert op["provenance_fingerprint"] and op["formula_input_digest"]

    # Client read: allowlisted, export-ready, no internal content.
    cl = client.get(f"/projects/{pid}/automation-roi/calculations/{rid}/client").json()
    assert cl["schema_version"].startswith("automation_roi.client")
    assert cl["status"] == "valid"
    assert cl["result"]["annual_labor_savings"] == "20800.00"
    assert cl["result"]["first_year_roi_percent"] == "296.00"
    import json as _json
    cl_blob = _json.dumps(cl)
    for forbidden in ("storage_ref", "/store/", "source_locator", "doc#", "provenance_fingerprint",
                      "formula_input_digest", "approval_decision_id", "candidate_fact_revision_id"):
        assert forbidden not in cl_blob


def test_zero_one_time_cost_is_not_applicable(client, conn, schema_b):
    pid = _seed_project(conn, name="zero-cost")
    inputs, _ = _freeze_six(client, conn, pid, overrides={"one_time_implementation_cost": "0"})
    resp = client.post(f"/projects/{pid}/automation-roi/calculations", json={"inputs": inputs})
    assert resp.status_code == 201, resp.text
    rid = resp.json()["result_id"]
    cl = client.get(f"/projects/{pid}/automation-roi/calculations/{rid}/client").json()
    assert cl["status"] == "not_applicable"
    assert cl["result"]["first_year_roi_percent"] is None
    assert any("not applicable" in c.lower() for c in cl["caveats"])


def test_withdrawn_input_yields_blocked_client_view(client, conn, schema_b):
    pid = _seed_project(conn, name="blocked")
    inputs, facts = _freeze_six(client, conn, pid)
    # Withdraw the approval of one already-frozen input's fact.
    fid, _did, _snap = facts["fully_loaded_rate_per_hour"]
    wd = client.post(
        f"/projects/{pid}/automation-roi/candidate-facts/{fid}/decisions",
        json={"decision_type": "withdraw"},
    )
    assert wd.status_code == 201, wd.text

    resp = client.post(f"/projects/{pid}/automation-roi/calculations", json={"inputs": inputs})
    assert resp.status_code == 201, resp.text
    rid = resp.json()["result_id"]
    assert resp.json()["status"] == "blocked"

    cl = client.get(f"/projects/{pid}/automation-roi/calculations/{rid}/client").json()
    assert cl["status"] == "blocked"
    assert cl["result"] is None
    assert cl["assumptions"] == []
    assert len(cl["caveats"]) == 1


# ─────────────────────────── candidate-fact constraints ───────────────────────────

def test_candidate_fact_rejects_cross_project_snapshot(client, conn, schema_b):
    pid = _seed_project(conn, name="p1")
    other = _seed_project(conn, name="p2")
    snap_other = _seed_snapshot(conn, other, tag="other")
    payload, _ = _fact_payload("periods_per_year", value="52")
    resp = client.post(
        f"/projects/{pid}/automation-roi/candidate-facts",
        json={"source_snapshot_id": snap_other, "fact": payload,
              "subject_label": "X", "metric_label": "periods"},
    )
    assert resp.status_code == 404


def test_candidate_fact_rejects_unavailable_snapshot(client, conn, schema_b):
    pid = _seed_project(conn, name="unavail")
    snap = _seed_snapshot(conn, pid, tag="tomb")
    ev_repo.insert_retention_event(
        conn, project_id=pid, event_type="tombstone", source_snapshot_id=snap, reason="redacted",
    )
    conn.commit()
    payload, _ = _fact_payload("periods_per_year", value="52")
    resp = client.post(
        f"/projects/{pid}/automation-roi/candidate-facts",
        json={"source_snapshot_id": snap, "fact": payload,
              "subject_label": "X", "metric_label": "periods"},
    )
    assert resp.status_code == 409


def test_candidate_fact_invalid_fact_value_is_422(client, conn, schema_b):
    pid = _seed_project(conn, name="badfact")
    snap = _seed_snapshot(conn, pid, tag="bad")
    resp = client.post(
        f"/projects/{pid}/automation-roi/candidate-facts",
        json={"source_snapshot_id": snap,
              "fact": {"fact_type": "money", "value": "50"},  # missing currency + as_of_date
              "subject_label": "X", "metric_label": "cost"},
    )
    assert resp.status_code == 422


# ─────────────────────────── decision lifecycle ───────────────────────────

def test_decision_lifecycle_and_illegal_transitions(client, conn, schema_b):
    pid = _seed_project(conn, name="decisions")
    snap = _seed_snapshot(conn, pid, tag="d")
    fid = _create_fact(client, pid, snap, "periods_per_year")
    decisions = f"/projects/{pid}/automation-roi/candidate-facts/{fid}/decisions"

    # reject with no active approval → 409
    assert client.post(decisions, json={"decision_type": "reject"}).status_code == 409
    # approve → 201
    assert client.post(decisions, json={"decision_type": "approve"}).status_code == 201
    # approve again while active → 409
    assert client.post(decisions, json={"decision_type": "approve"}).status_code == 409
    # withdraw the active approve → 201
    assert client.post(decisions, json={"decision_type": "withdraw"}).status_code == 201
    # re-approve after withdraw → 201 (new active approval)
    assert client.post(decisions, json={"decision_type": "approve"}).status_code == 201


def test_decision_unknown_fact_is_404(client, conn, schema_b):
    pid = _seed_project(conn, name="dnf")
    missing = "00000000-0000-0000-0000-0000000000ff"
    resp = client.post(
        f"/projects/{pid}/automation-roi/candidate-facts/{missing}/decisions",
        json={"decision_type": "approve"},
    )
    assert resp.status_code == 404


# ─────────────────────────── freeze rules ───────────────────────────

def test_freeze_requires_active_approval(client, conn, schema_b):
    pid = _seed_project(conn, name="freeze-noapprove")
    snap = _seed_snapshot(conn, pid, tag="fz")
    fid = _create_fact(client, pid, snap, "periods_per_year")
    did = _approve(client, pid, fid)
    # withdraw before freezing → no active approval
    client.post(f"/projects/{pid}/automation-roi/candidate-facts/{fid}/decisions",
                json={"decision_type": "withdraw"})
    resp = client.post(
        f"/projects/{pid}/automation-roi/inputs",
        json={"candidate_fact_revision_id": fid, "approval_decision_id": did,
              "input_role": "periods_per_year"},
    )
    assert resp.status_code == 409


def test_freeze_rejects_unavailable_fact(client, conn, schema_b):
    pid = _seed_project(conn, name="freeze-unavail")
    snap = _seed_snapshot(conn, pid, tag="fu")
    fid = _create_fact(client, pid, snap, "periods_per_year")
    did = _approve(client, pid, fid)
    ev_repo.insert_retention_event(
        conn, project_id=pid, event_type="tombstone", source_snapshot_id=snap, reason="gone",
    )
    conn.commit()
    resp = client.post(
        f"/projects/{pid}/automation-roi/inputs",
        json={"candidate_fact_revision_id": fid, "approval_decision_id": did,
              "input_role": "periods_per_year"},
    )
    assert resp.status_code == 409


def test_freeze_rejects_cross_project_fact(client, conn, schema_b):
    pid = _seed_project(conn, name="fp1")
    other = _seed_project(conn, name="fp2")
    snap = _seed_snapshot(conn, other, tag="cross")
    fid = _create_fact(client, other, snap, "periods_per_year")
    did = _approve(client, other, fid)
    resp = client.post(
        f"/projects/{pid}/automation-roi/inputs",
        json={"candidate_fact_revision_id": fid, "approval_decision_id": did,
              "input_role": "periods_per_year"},
    )
    assert resp.status_code == 409


# ─────────────────────────── calculation input map ───────────────────────────

def test_calculation_unknown_input_id_is_422_and_persists_nothing(client, conn, schema_b):
    pid = _seed_project(conn, name="calc-unknown")
    inputs, _ = _freeze_six(client, conn, pid)
    bad = dict(inputs)
    bad["periods_per_year"] = "00000000-0000-0000-0000-0000000000ab"  # valid uuid, unknown row
    resp = client.post(f"/projects/{pid}/automation-roi/calculations", json={"inputs": bad})
    assert resp.status_code == 422
    count = conn.execute("SELECT count(*) FROM calculation_result WHERE project_id = %s", (pid,)).fetchone()[0]
    assert count == 0


def test_calculation_wrong_role_input_is_422(client, conn, schema_b):
    pid = _seed_project(conn, name="calc-wrongrole")
    inputs, _ = _freeze_six(client, conn, pid)
    swapped = dict(inputs)
    swapped["periods_per_year"], swapped["annual_recurring_cost"] = (
        inputs["annual_recurring_cost"], inputs["periods_per_year"],
    )
    resp = client.post(f"/projects/{pid}/automation-roi/calculations", json={"inputs": swapped})
    assert resp.status_code == 422


# ─────────────────────── frozen-input compatibility (422) ───────────────────────

_RATE_UNIT_DETAIL = "Fully loaded rate per hour requires unit 'per_hour'."


def _create_rate_fact_with_unit(client, pid, snap, *, unit, value="50", currency="USD"):
    """Create + approve a money fact whose unit is operator-chosen (not 'per_hour')."""
    body = {
        "source_snapshot_id": snap,
        "fact": {"fact_type": "money", "value": value, "currency_code": currency,
                 "as_of_date": AS_OF, "unit": unit},
        "subject_label": "Process X",
        "metric_label": "fully_loaded_rate_per_hour",
        "source_locator": "doc#rate",
        "extraction_rationale": "operator extracted",
    }
    resp = client.post(f"/projects/{pid}/automation-roi/candidate-facts", json=body)
    assert resp.status_code == 201, resp.text
    fid = resp.json()["candidate_fact_revision_id"]
    did = _approve(client, pid, fid)
    return fid, did


def _frozen_count(conn, pid):
    return conn.execute(
        "SELECT count(*) FROM approved_calculation_input WHERE project_id = %s", (pid,)
    ).fetchone()[0]


def test_freeze_rate_usd_per_hour_unit_is_422_and_persists_nothing(client, conn, schema_b):
    pid = _seed_project(conn, name="compat-usdhour")
    snap = _seed_snapshot(conn, pid, tag="usdhour")
    fid, did = _create_rate_fact_with_unit(client, pid, snap, unit="USD/hour")
    before = _frozen_count(conn, pid)
    resp = client.post(
        f"/projects/{pid}/automation-roi/inputs",
        json={"candidate_fact_revision_id": fid, "approval_decision_id": did,
              "input_role": "fully_loaded_rate_per_hour"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == _RATE_UNIT_DETAIL
    assert _frozen_count(conn, pid) == before


def test_freeze_rate_per_hou_unit_is_422_and_persists_nothing(client, conn, schema_b):
    pid = _seed_project(conn, name="compat-perhou")
    snap = _seed_snapshot(conn, pid, tag="perhou")
    fid, did = _create_rate_fact_with_unit(client, pid, snap, unit="per_hou")
    before = _frozen_count(conn, pid)
    resp = client.post(
        f"/projects/{pid}/automation-roi/inputs",
        json={"candidate_fact_revision_id": fid, "approval_decision_id": did,
              "input_role": "fully_loaded_rate_per_hour"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == _RATE_UNIT_DETAIL
    assert _frozen_count(conn, pid) == before


def test_freeze_rate_compatible_per_hour_unit_succeeds(client, conn, schema_b):
    pid = _seed_project(conn, name="compat-ok")
    snap = _seed_snapshot(conn, pid, tag="ok")
    fid, did = _create_rate_fact_with_unit(client, pid, snap, unit="per_hour")
    before = _frozen_count(conn, pid)
    resp = client.post(
        f"/projects/{pid}/automation-roi/inputs",
        json={"candidate_fact_revision_id": fid, "approval_decision_id": did,
              "input_role": "fully_loaded_rate_per_hour"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["input_role"] == "fully_loaded_rate_per_hour"
    assert _frozen_count(conn, pid) == before + 1


def test_result_not_found_is_404(client, conn, schema_b):
    pid = _seed_project(conn, name="missing-result")
    missing = "00000000-0000-0000-0000-0000000000cd"
    assert client.get(f"/projects/{pid}/automation-roi/calculations/{missing}").status_code == 404
    assert client.get(
        f"/projects/{pid}/automation-roi/calculations/{missing}/client"
    ).status_code == 404


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
