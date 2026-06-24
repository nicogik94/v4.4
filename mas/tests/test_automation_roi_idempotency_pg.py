"""PostgreSQL-backed tests for Automation ROI calculation idempotency (v49).

Drives ``service.request_calculation`` against a disposable database
(TEST_EVIDENCE_PG_DSN) with ephemeral schemas dropped on exit. The authoritative
MAS database is never touched; skipped when no DSN is configured.

Covers the dual-identity contract end to end:
  * the four key×digest behavior cases;
  * two concurrent same-digest requests create exactly one result;
  * two concurrent same-key/different-digest requests produce one commit + one 409;
  * an invalid request leaves no reservation behind (claim rolled back);
  * blocked and not_applicable results replay correctly;
  * an explicit lost-response replay (commit, then resend the same key);
  * cross-project isolation of both identities.
"""
import sys
import threading
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests.evidence_snapshot_pg as pg  # noqa: E402
import tests.automation_roi_fixtures as fx  # noqa: E402
from knowledge.automation_roi import approvals, repository as repo, service  # noqa: E402


@pytest.fixture
def conn():
    pg.require_dsn()
    c = pg.connect()
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def schema_b49(conn):
    with pg.fresh_schema(conn) as s:
        pg.apply_v48(conn)
        pg.apply_v49(conn)
        yield s


def _request(conn, pid, inputs, key):
    out = service.request_calculation(
        conn, project_id=pid, inputs_by_role=inputs, idempotency_key=key, computed_by="op")
    conn.commit()
    return out


def _result_count(conn, pid):
    return conn.execute(
        "SELECT count(*) FROM calculation_result WHERE project_id = %s", (pid,)).fetchone()[0]


def _request_count(conn, pid):
    return conn.execute(
        "SELECT count(*) FROM calculation_request WHERE project_id = %s", (pid,)).fetchone()[0]


# ─────────────────────────── four behavior cases ───────────────────────────

def test_same_key_same_digest_replays(conn, schema_b49):
    pid = pg.insert_project(conn, name="same-same")
    conn.commit()
    im = fx.input_map(fx.seed_six(conn, pid))
    conn.commit()
    first = _request(conn, pid, im, "K1")
    second = _request(conn, pid, im, "K1")
    assert first.replayed is False
    assert second.replayed is True
    assert second.result_id == first.result_id
    assert _result_count(conn, pid) == 1
    assert _request_count(conn, pid) == 1


def test_same_key_different_digest_conflicts(conn, schema_b49):
    pid = pg.insert_project(conn, name="same-diff")
    conn.commit()
    im_a = fx.input_map(fx.seed_six(conn, pid))
    im_b = fx.input_map(fx.seed_six(conn, pid))
    conn.commit()
    _request(conn, pid, im_a, "K1")
    with pytest.raises(service.RequestKeyConflict):
        service.request_calculation(
            conn, project_id=pid, inputs_by_role=im_b, idempotency_key="K1", computed_by="op")
    conn.rollback()
    assert _result_count(conn, pid) == 1


def test_different_key_same_digest_replays(conn, schema_b49):
    pid = pg.insert_project(conn, name="diff-same")
    conn.commit()
    im = fx.input_map(fx.seed_six(conn, pid))
    conn.commit()
    first = _request(conn, pid, im, "K1")
    second = _request(conn, pid, im, "K2")
    assert second.replayed is True
    assert second.result_id == first.result_id
    assert _result_count(conn, pid) == 1


def test_different_key_different_digest_new_result(conn, schema_b49):
    pid = pg.insert_project(conn, name="diff-diff")
    conn.commit()
    im_a = fx.input_map(fx.seed_six(conn, pid))
    im_b = fx.input_map(fx.seed_six(conn, pid))
    conn.commit()
    first = _request(conn, pid, im_a, "K1")
    second = _request(conn, pid, im_b, "K2")
    assert first.replayed is False and second.replayed is False
    assert first.result_id != second.result_id
    assert _result_count(conn, pid) == 2


# ─────────────────────────── concurrency ───────────────────────────

def _concurrent(schema, pid, calls):
    """Run ``calls`` (list of (inputs, key)) in parallel, each on its own
    connection/transaction; return a list of (outcome, exception) per call."""
    results = [None] * len(calls)
    barrier = threading.Barrier(len(calls))

    def run(i, inputs, key):
        c = pg.connect(schema=schema)
        try:
            barrier.wait()
            try:
                out = service.request_calculation(
                    c, project_id=pid, inputs_by_role=inputs, idempotency_key=key, computed_by="op")
                c.commit()
                results[i] = (out, None)
            except Exception as exc:  # noqa: BLE001 - recorded for assertions
                c.rollback()
                results[i] = (None, exc)
        finally:
            c.close()

    threads = [threading.Thread(target=run, args=(i, inp, key))
               for i, (inp, key) in enumerate(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return results


def test_concurrent_same_digest_creates_one_result(conn, schema_b49):
    pid = pg.insert_project(conn, name="conc-same-digest")
    conn.commit()
    im = fx.input_map(fx.seed_six(conn, pid))
    conn.commit()
    # Same inputs (same digest), different keys, run concurrently.
    results = _concurrent(schema_b49, pid, [(im, "A"), (im, "B")])
    errors = [e for _o, e in results if e is not None]
    outcomes = [o for o, _e in results if o is not None]
    assert errors == [], errors
    assert len(outcomes) == 2
    # Exactly one result, and both calls resolve to it.
    assert _result_count(conn, pid) == 1
    assert {o.result_id for o in outcomes} == {outcomes[0].result_id}
    assert sum(1 for o in outcomes if o.replayed) == 1  # one computed, one replayed


def test_concurrent_same_key_different_digest_one_commit_one_409(conn, schema_b49):
    pid = pg.insert_project(conn, name="conc-key-conflict")
    conn.commit()
    im_a = fx.input_map(fx.seed_six(conn, pid))
    im_b = fx.input_map(fx.seed_six(conn, pid))
    conn.commit()
    results = _concurrent(schema_b49, pid, [(im_a, "K"), (im_b, "K")])
    outcomes = [o for o, _e in results if o is not None]
    conflicts = [e for _o, e in results if isinstance(e, service.RequestKeyConflict)]
    other = [e for _o, e in results if e is not None and not isinstance(e, service.RequestKeyConflict)]
    assert other == [], other
    assert len(outcomes) == 1
    assert len(conflicts) == 1
    assert _result_count(conn, pid) == 1


# ─────────────────────────── rollback / replay variants ───────────────────────────

def test_invalid_request_leaves_no_reservation(conn, schema_b49):
    pid = pg.insert_project(conn, name="invalid")
    conn.commit()
    im = fx.input_map(fx.seed_six(conn, pid))
    conn.commit()
    im = dict(im)
    im["periods_per_year"] = str(uuid.uuid4())  # valid uuid, unknown row
    with pytest.raises(ValueError):
        service.request_calculation(
            conn, project_id=pid, inputs_by_role=im, idempotency_key="BAD", computed_by="op")
    conn.rollback()
    assert _request_count(conn, pid) == 0
    assert _result_count(conn, pid) == 0


def test_not_applicable_replays(conn, schema_b49):
    pid = pg.insert_project(conn, name="na-replay")
    conn.commit()
    im = fx.input_map(fx.seed_six(conn, pid, overrides={"one_time_implementation_cost": "0"}))
    conn.commit()
    first = _request(conn, pid, im, "K1")
    assert repo.get_result(conn, project_id=pid, result_id=first.result_id)["status"] == "not_applicable"
    second = _request(conn, pid, im, "K1")
    assert second.replayed is True
    assert second.result_id == first.result_id


def test_blocked_replays(conn, schema_b49):
    pid = pg.insert_project(conn, name="blocked-replay")
    conn.commit()
    seeded = fx.seed_six(conn, pid)
    conn.commit()
    # Withdraw the approval of one frozen input's fact → that role is unavailable.
    cfr = seeded["annual_recurring_cost"]["cfr"]
    active = approvals.active_approval_id(conn, project_id=pid, candidate_fact_revision_id=cfr)
    approvals.append_decision(
        conn, project_id=pid, candidate_fact_revision_id=cfr, decision_type="withdraw",
        revokes_decision_id=active)
    conn.commit()
    im = fx.input_map(seeded)
    first = _request(conn, pid, im, "K1")
    assert repo.get_result(conn, project_id=pid, result_id=first.result_id)["status"] == "blocked"
    second = _request(conn, pid, im, "K1")
    assert second.replayed is True
    assert second.result_id == first.result_id


def test_lost_response_replay_no_duplicate(conn, schema_b49):
    # Simulate a lost/timed-out response: the first request committed server-side,
    # the client never saw it and resends with the SAME key.
    pid = pg.insert_project(conn, name="lost-response")
    conn.commit()
    im = fx.input_map(fx.seed_six(conn, pid))
    conn.commit()
    first = _request(conn, pid, im, "K1")  # committed
    second = _request(conn, pid, im, "K1")  # resent
    assert second.replayed is True
    assert second.result_id == first.result_id
    assert _result_count(conn, pid) == 1
    assert _request_count(conn, pid) == 1


def test_cross_project_isolation(conn, schema_b49):
    pid_a = pg.insert_project(conn, name="iso-a")
    pid_b = pg.insert_project(conn, name="iso-b")
    conn.commit()
    im_a = fx.input_map(fx.seed_six(conn, pid_a))
    im_b = fx.input_map(fx.seed_six(conn, pid_b))
    conn.commit()
    # Same idempotency key in both projects → independent identities, no conflict.
    out_a = _request(conn, pid_a, im_a, "SAME")
    out_b = _request(conn, pid_b, im_b, "SAME")
    assert out_a.replayed is False and out_b.replayed is False
    assert out_a.result_id != out_b.result_id
    assert _result_count(conn, pid_a) == 1
    assert _result_count(conn, pid_b) == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
