"""PostgreSQL-backed Slice B lifecycle / reproducibility tests.

Exercises repositories + approvals + the deterministic engine end-to-end against a
disposable database (TEST_EVIDENCE_PG_DSN), with ephemeral schemas dropped on
exit. Skipped without a DSN. The authoritative MAS database is never touched.
"""
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests.evidence_snapshot_pg as pg  # noqa: E402
import tests.automation_roi_fixtures as fx  # noqa: E402
from knowledge.automation_roi import approvals, repository as repo, service  # noqa: E402
from knowledge.automation_roi.calculator import ROLES, compute_automation_roi  # noqa: E402


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


def test_full_lifecycle_valid_and_reproducible(conn, schema_b):
    pid = pg.insert_project(conn, name="lifecycle")
    conn.commit()
    seeded = fx.seed_six(conn, pid)
    conn.commit()
    rid = service.compute_and_persist(
        conn, project_id=pid, inputs_by_role=fx.input_map(seeded), computed_by="op")
    conn.commit()

    result = repo.get_result(conn, project_id=pid, result_id=rid)
    assert result["status"] == "valid"
    assert result["annual_labor_savings"] == Decimal("20800")
    assert result["first_year_net_benefit"] == Decimal("14800")
    assert result["first_year_roi_percent"] == Decimal("296")

    links = repo.list_result_input_ids(conn, project_id=pid, result_id=rid)
    assert set(links) == set(ROLES)

    # Reproduce from the persisted frozen inputs → identical value digest + outputs.
    resolved = {
        role: repo.as_resolved_input(repo.load_frozen_input(conn, project_id=pid, input_id=iid))
        for role, iid in links.items()
    }
    recomputed = compute_automation_roi(resolved)
    assert recomputed.formula_input_digest == result["formula_input_digest"]
    assert recomputed.first_year_net_benefit == result["first_year_net_benefit"]


def test_zero_implementation_cost_is_not_applicable(conn, schema_b):
    pid = pg.insert_project(conn, name="zero-cost")
    conn.commit()
    seeded = fx.seed_six(conn, pid, overrides={"one_time_implementation_cost": "0"})
    conn.commit()
    rid = service.compute_and_persist(
        conn, project_id=pid, inputs_by_role=fx.input_map(seeded), computed_by="op")
    conn.commit()
    result = repo.get_result(conn, project_id=pid, result_id=rid)
    assert result["status"] == "not_applicable"
    assert result["first_year_roi_percent"] is None
    assert result["annual_labor_savings"] == Decimal("20800")


def test_withdrawn_input_yields_blocked(conn, schema_b):
    pid = pg.insert_project(conn, name="withdrawn")
    conn.commit()
    seeded = fx.seed_six(conn, pid)
    conn.commit()
    cfr = seeded["fully_loaded_rate_per_hour"]["cfr"]
    approve = approvals.active_approval_id(conn, project_id=pid, candidate_fact_revision_id=cfr)
    approvals.append_decision(
        conn, project_id=pid, candidate_fact_revision_id=cfr, decision_type="withdraw",
        revokes_decision_id=approve)
    conn.commit()
    rid = service.compute_and_persist(
        conn, project_id=pid, inputs_by_role=fx.input_map(seeded), computed_by="op")
    conn.commit()  # blocked persists with all six linked
    result = repo.get_result(conn, project_id=pid, result_id=rid)
    assert result["status"] == "blocked"
    assert result["first_year_roi_percent"] is None


def test_shape_error_persists_nothing(conn, schema_b):
    pid = pg.insert_project(conn, name="shape")
    conn.commit()
    seeded = fx.seed_six(conn, pid)
    conn.commit()
    bad = fx.input_map(seeded)
    del bad["periods_per_year"]  # only five roles
    with pytest.raises(service.CalculationRequestError):
        service.compute_and_persist(conn, project_id=pid, inputs_by_role=bad, computed_by="op")
    conn.rollback()
    count = conn.execute(
        "SELECT count(*) FROM calculation_result WHERE project_id = %s", (pid,)).fetchone()[0]
    assert count == 0


def test_freeze_without_approval_raises(conn, schema_b):
    pid = pg.insert_project(conn, name="no-approval")
    conn.commit()
    cfr, _ = fx.seed_eligible_fact(conn, pid, "periods_per_year")
    conn.commit()
    with pytest.raises(ValueError):
        repo.freeze_input(
            conn, project_id=pid, candidate_fact_revision_id=cfr, input_role="periods_per_year")
    conn.rollback()


def test_effective_status_transitions(conn, schema_b):
    pid = pg.insert_project(conn, name="status")
    conn.commit()
    cfr, _ = fx.seed_eligible_fact(conn, pid, "periods_per_year")
    conn.commit()
    assert approvals.effective_status(conn, project_id=pid, candidate_fact_revision_id=cfr) == "none"
    approve = approvals.append_decision(
        conn, project_id=pid, candidate_fact_revision_id=cfr, decision_type="approve")
    conn.commit()
    assert approvals.effective_status(conn, project_id=pid, candidate_fact_revision_id=cfr) == "approved"
    approvals.append_decision(
        conn, project_id=pid, candidate_fact_revision_id=cfr, decision_type="withdraw",
        revokes_decision_id=approve)
    conn.commit()
    assert approvals.effective_status(conn, project_id=pid, candidate_fact_revision_id=cfr) == "revoked"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
