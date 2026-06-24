"""PostgreSQL-backed tests for the operator-workspace read endpoint (Slice B).

Drives the real ``GET /projects/{id}/automation-roi/workspace`` route against a
disposable database (TEST_EVIDENCE_PG_DSN, ephemeral schema). Skipped when no DSN
is configured; the authoritative MAS database is never touched.

Covered (read-model contract): same-project isolation, no raw storage/path/actor/
sequence fields, exact-six readiness (complete + missing), read-performs-no-write,
unavailable-evidence marking, server-derived decision state/permitted actions, and
result history. Plus the disposable-DB E2E: valid / not_applicable / blocked
persist as expected, a malformed role map is 422 with nothing persisted, and the
client-safe preview excludes forbidden operator/internal fields.
"""
import json
import sys
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

# Reuse the established lifecycle helpers verbatim (no parallel seeding logic).
from tests.test_automation_roi_api_pg import (  # noqa: E402
    _approve,
    _calc,
    _create_fact,
    _freeze_six,
    _seed_project,
    _seed_snapshot,
    _DEFAULT_VALUES,
)

# Fields that must never appear anywhere in the operator-safe workspace payload.
FORBIDDEN_SUBSTRINGS = (
    "storage_ref",
    "/store/",
    "captured_by",
    "extracted_by",
    "decided_by",
    "computed_by",
    "frozen_by",
    "decision_seq",
    "source_blob",
)


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
        pg.apply_v49(conn)
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


def _workspace(client, pid):
    resp = client.get(f"/projects/{pid}/automation-roi/workspace")
    assert resp.status_code == 200, resp.text
    return resp.json()


# ─────────────────────────── isolation + safety ───────────────────────────

def test_workspace_is_same_project_isolated(client, conn, schema_b):
    pid_a = _seed_project(conn, name="ws-a")
    pid_b = _seed_project(conn, name="ws-b")
    _freeze_six(client, conn, pid_a)
    snap_b = _seed_snapshot(conn, pid_b, tag="b-only")
    _create_fact(client, pid_b, snap_b, "periods_per_year")

    ws_a = _workspace(client, pid_a)
    a_snaps = {s["source_snapshot_id"] for s in ws_a["snapshots"]}
    assert snap_b not in a_snaps
    assert len(ws_a["candidate_facts"]) == 6  # project A's six, none from B
    assert all(f["source_snapshot_id"] in a_snaps for f in ws_a["candidate_facts"])


def test_workspace_omits_raw_storage_actor_and_sequence_fields(client, conn, schema_b):
    pid = _seed_project(conn, name="ws-safe")
    _freeze_six(client, conn, pid)
    ws = _workspace(client, pid)
    blob = json.dumps(ws)
    for forbidden in FORBIDDEN_SUBSTRINGS:
        assert forbidden not in blob, forbidden


def test_workspace_marks_unavailable_evidence(client, conn, schema_b):
    pid = _seed_project(conn, name="ws-unavail")
    snap = _seed_snapshot(conn, pid, tag="tomb")
    fid = _create_fact(client, pid, snap, "periods_per_year")
    ev_repo.insert_retention_event(
        conn, project_id=pid, event_type="tombstone", source_snapshot_id=snap, reason="redacted",
    )
    conn.commit()

    ws = _workspace(client, pid)
    snap_row = next(s for s in ws["snapshots"] if s["source_snapshot_id"] == snap)
    assert snap_row["available"] is False
    fact_row = next(f for f in ws["candidate_facts"] if f["candidate_fact_revision_id"] == fid)
    assert fact_row["available"] is False


# ─────────────────────────── decision state + readiness ───────────────────────────

def test_workspace_derives_decision_state_and_permitted_actions(client, conn, schema_b):
    pid = _seed_project(conn, name="ws-decision")
    snap = _seed_snapshot(conn, pid, tag="d")
    fid = _create_fact(client, pid, snap, "periods_per_year")

    ws = _workspace(client, pid)
    fact = next(f for f in ws["candidate_facts"] if f["candidate_fact_revision_id"] == fid)
    assert fact["decision_state"] == "none"
    assert fact["permitted_actions"] == ["approve"]
    assert fact["active_approval_id"] is None

    _approve(client, pid, fid)
    ws = _workspace(client, pid)
    fact = next(f for f in ws["candidate_facts"] if f["candidate_fact_revision_id"] == fid)
    assert fact["decision_state"] == "approved"
    assert fact["permitted_actions"] == ["reject", "withdraw"]
    assert fact["active_approval_id"] is not None


def test_workspace_readiness_reports_complete_and_missing(client, conn, schema_b):
    pid = _seed_project(conn, name="ws-ready")
    # Freeze only five of the six roles → incomplete, one missing.
    for role in list(_DEFAULT_VALUES)[:5]:
        snap = _seed_snapshot(conn, pid, tag=role)
        fid = _create_fact(client, pid, snap, role)
        did = _approve(client, pid, fid)
        client.post(
            f"/projects/{pid}/automation-roi/inputs",
            json={"candidate_fact_revision_id": fid, "approval_decision_id": did, "input_role": role},
        )
    ws = _workspace(client, pid)
    assert ws["role_readiness"]["complete"] is False
    assert ws["role_readiness"]["missing_roles"] == [list(_DEFAULT_VALUES)[5]]

    # Freeze the sixth → complete.
    last = list(_DEFAULT_VALUES)[5]
    snap = _seed_snapshot(conn, pid, tag=last)
    fid = _create_fact(client, pid, snap, last)
    did = _approve(client, pid, fid)
    client.post(
        f"/projects/{pid}/automation-roi/inputs",
        json={"candidate_fact_revision_id": fid, "approval_decision_id": did, "input_role": last},
    )
    ws = _workspace(client, pid)
    assert ws["role_readiness"]["complete"] is True
    assert ws["role_readiness"]["missing_roles"] == []


def test_workspace_read_performs_no_writes(client, conn, schema_b):
    pid = _seed_project(conn, name="ws-readonly")
    _freeze_six(client, conn, pid)

    def _counts():
        return tuple(
            conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "source_snapshot",
                "candidate_fact_revision",
                "candidate_fact_approval_decision",
                "approved_calculation_input",
                "calculation_result",
            )
        )

    conn.commit()
    before = _counts()
    for _ in range(3):
        _workspace(client, pid)
    conn.commit()
    assert _counts() == before


# ─────────────────────────── disposable-DB E2E ───────────────────────────

def test_e2e_valid_result_listed_in_history(client, conn, schema_b):
    pid = _seed_project(conn, name="e2e-valid")
    inputs, _ = _freeze_six(client, conn, pid)
    resp = _calc(client, pid, inputs)
    assert resp.status_code == 201, resp.text
    rid = resp.json()["result_id"]

    ws = _workspace(client, pid)
    result = next(r for r in ws["results"] if r["result_id"] == rid)
    assert result["status"] == "valid"
    assert result["formula_version"] == "automation_roi.v1"


def test_e2e_zero_cost_persists_not_applicable(client, conn, schema_b):
    pid = _seed_project(conn, name="e2e-na")
    inputs, _ = _freeze_six(client, conn, pid, overrides={"one_time_implementation_cost": "0"})
    resp = _calc(client, pid, inputs)
    assert resp.status_code == 201, resp.text
    rid = resp.json()["result_id"]
    ws = _workspace(client, pid)
    assert next(r for r in ws["results"] if r["result_id"] == rid)["status"] == "not_applicable"


def test_e2e_unavailable_evidence_persists_blocked(client, conn, schema_b):
    pid = _seed_project(conn, name="e2e-blocked")
    inputs, facts = _freeze_six(client, conn, pid)
    fid, _did, snap = facts["fully_loaded_rate_per_hour"]
    ev_repo.insert_retention_event(
        conn, project_id=pid, event_type="tombstone", source_snapshot_id=snap, reason="redacted",
    )
    conn.commit()
    resp = _calc(client, pid, inputs)
    assert resp.status_code == 201, resp.text
    rid = resp.json()["result_id"]
    ws = _workspace(client, pid)
    assert next(r for r in ws["results"] if r["result_id"] == rid)["status"] == "blocked"


def test_e2e_malformed_role_map_is_422_and_persists_nothing(client, conn, schema_b):
    pid = _seed_project(conn, name="e2e-422")
    inputs, _ = _freeze_six(client, conn, pid)
    bad = {k: v for k, v in inputs.items() if k != "periods_per_year"}  # missing one role
    resp = _calc(client, pid, bad, key="malformed-role-map")
    assert resp.status_code == 422
    ws = _workspace(client, pid)
    assert ws["results"] == []


def test_e2e_client_preview_excludes_forbidden_fields(client, conn, schema_b):
    pid = _seed_project(conn, name="e2e-client")
    inputs, _ = _freeze_six(client, conn, pid)
    resp = _calc(client, pid, inputs)
    assert resp.status_code == 201, resp.text
    rid = resp.json()["result_id"]
    cl = client.get(f"/projects/{pid}/automation-roi/calculations/{rid}/client").json()
    blob = json.dumps(cl)
    for forbidden in ("storage_ref", "/store/", "source_locator", "doc#",
                      "provenance_fingerprint", "formula_input_digest",
                      "approval_decision_id", "candidate_fact_revision_id", "decision_seq"):
        assert forbidden not in blob, forbidden


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
