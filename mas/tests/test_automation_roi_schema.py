"""PostgreSQL-backed Slice B (v48) schema, integrity, and migration tests.

Run against a disposable PostgreSQL database (TEST_EVIDENCE_PG_DSN), using
ephemeral schemas that are dropped on exit. Skipped when no DSN is provided. The
authoritative MAS database is never touched.
"""
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests.evidence_snapshot_pg as pg  # noqa: E402
import tests.automation_roi_fixtures as fx  # noqa: E402
from knowledge.evidence_snapshot import repository as ev_repo  # noqa: E402
from knowledge.automation_roi import approvals, repository as repo  # noqa: E402
from knowledge.automation_roi.calculator import ROLES  # noqa: E402

VALID_INSERT = (
    "INSERT INTO calculation_result "
    "(project_id, formula_version, status, currency_code, annual_labor_savings, "
    "annual_net_benefit, first_year_net_benefit, first_year_roi_percent, roi_percent_status, "
    "formula_input_digest, provenance_fingerprint) "
    "VALUES (%s,'automation_roi.v1','valid','USD',1,1,1,1,'computed','d','p') RETURNING id::text"
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
    """Fresh init+outcomes+v47 schema with v48 applied on top."""
    with pg.fresh_schema(conn) as s:
        pg.apply_v48(conn)
        yield s


@pytest.fixture
def schema_b49(conn):
    """Fresh init+outcomes+v47 schema with v48 and v49 applied on top."""
    with pg.fresh_schema(conn) as s:
        pg.apply_v48(conn)
        pg.apply_v49(conn)
        yield s


# ── Migration ───────────────────────────────────────────────────────────────

def test_fresh_apply_creates_complete_slice_b(conn, schema_b):
    assert pg.slice_b_tables_present(conn, schema_b) == 5
    for fn in ("slicebo_assert_frozen_matches_fact", "slicebo_assert_result_invariant"):
        assert pg.function_exists(conn, schema_b, fn)
    for trg, tbl in (
        ("trg_cfec_no_mutation", "candidate_fact_extraction_context"),
        ("trg_cfad_no_mutation", "candidate_fact_approval_decision"),
        ("trg_aci_no_mutation", "approved_calculation_input"),
        ("trg_cr_no_mutation", "calculation_result"),
        ("trg_cri_no_mutation", "calculation_result_input"),
        ("trg_aci_value_copy", "approved_calculation_input"),
        ("trg_cr_result_invariant", "calculation_result"),
    ):
        assert pg.trigger_exists(conn, schema_b, trg, tbl), trg


def test_complete_reapply_is_noop(conn, schema_b):
    pg.apply_v48(conn)  # must not raise
    assert pg.slice_b_tables_present(conn, schema_b) == 5


def test_partial_schema_is_rejected(conn, schema_b):
    prior = pg._begin_autocommit(conn)
    conn.execute("DROP TABLE calculation_result_input")
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception) as ei:
        pg.apply_v48(conn)
    assert "partial/divergent" in str(ei.value) or "contract violation" in str(ei.value)


def test_v48_requires_v47(conn):
    schema = f"slicea_test_{uuid.uuid4().hex[:16]}"
    prior = pg._begin_autocommit(conn)
    try:
        conn.execute(f'CREATE SCHEMA "{schema}"')
        conn.execute(f'SET search_path TO "{schema}"')
        pg._run_script(conn, pg.INIT_SQL)
        pg._run_script(conn, pg.OUTCOMES_SQL)  # no v47
        with pytest.raises(Exception) as ei:
            pg._run_script(conn, pg.V48_SQL)
        assert "requires v47" in str(ei.value)
    finally:
        import contextlib
        with contextlib.suppress(Exception):
            conn.rollback()  # clear the aborted migration transaction before cleanup
        conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        pg._restore_autocommit(conn, prior)


# ── Append-only ───────────────────────────────────────────────────────────────

def test_append_only_rejects_mutation(conn, schema_b):
    from knowledge.automation_roi import service
    pid = pg.insert_project(conn, name="append-only")
    conn.commit()
    seeded = fx.seed_six(conn, pid)
    conn.commit()
    service.compute_and_persist(conn, project_id=pid, inputs_by_role=fx.input_map(seeded), computed_by="op")
    conn.commit()
    for tbl in (
        "candidate_fact_extraction_context", "candidate_fact_approval_decision",
        "approved_calculation_input", "calculation_result", "calculation_result_input",
    ):
        with pytest.raises(Exception):
            conn.execute(f"UPDATE {tbl} SET project_id = project_id")
            conn.commit()
        conn.rollback()
        with pytest.raises(Exception):
            conn.execute(f"DELETE FROM {tbl}")
            conn.commit()
        conn.rollback()


# ── Cross-project / eligibility / approval integrity ──────────────────────────

def test_cross_project_context_rejected(conn, schema_b):
    pid_a = pg.insert_project(conn, name="A")
    pid_b = pg.insert_project(conn, name="B")
    conn.commit()
    blob = ev_repo.insert_or_get_blob(conn, project_id=pid_a, content_hash="x", byte_size=4)
    snap = ev_repo.insert_snapshot(conn, source_blob_id=blob, project_id=pid_a, storage_ref="/x")
    fact_a = ev_repo.insert_fact(
        conn, project_id=pid_a, source_snapshot_id=snap, fact=fx.make_validated_fact("periods_per_year"),
    )
    conn.commit()
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO candidate_fact_extraction_context "
            "(project_id, candidate_fact_revision_id, subject_label, metric_label) VALUES (%s,%s,'s','m')",
            (pid_b, fact_a),
        )
        conn.commit()
    conn.rollback()


def test_ineligible_fact_cannot_be_frozen(conn, schema_b):
    pid = pg.insert_project(conn, name="ineligible")
    conn.commit()
    blob = ev_repo.insert_or_get_blob(conn, project_id=pid, content_hash="y", byte_size=4)
    snap = ev_repo.insert_snapshot(conn, source_blob_id=blob, project_id=pid, storage_ref="/y")
    fact = ev_repo.insert_fact(
        conn, project_id=pid, source_snapshot_id=snap, fact=fx.make_validated_fact("periods_per_year"),
    )
    approvals.append_decision(conn, project_id=pid, candidate_fact_revision_id=fact, decision_type="approve")
    conn.commit()
    # service guard
    with pytest.raises(ValueError):
        repo.freeze_input(conn, project_id=pid, candidate_fact_revision_id=fact, input_role="periods_per_year")
    conn.rollback()
    # DB guard (value-copy trigger / eligibility FK) on a manual insert
    approve_id = approvals.active_approval_id(conn, project_id=pid, candidate_fact_revision_id=fact)
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO approved_calculation_input "
            "(project_id, input_role, candidate_fact_revision_id, approval_decision_id, "
            " resolved_numeric_value, resolved_unit) VALUES (%s,'periods_per_year',%s,%s,52,'')",
            (pid, fact, approve_id),
        )
        conn.commit()
    conn.rollback()


def test_frozen_input_must_reference_active_approve_of_same_fact(conn, schema_b):
    pid = pg.insert_project(conn, name="approve-fk")
    conn.commit()
    cfr, _snap = fx.seed_eligible_fact(conn, pid, "periods_per_year")
    approve_id = approvals.append_decision(
        conn, project_id=pid, candidate_fact_revision_id=cfr, decision_type="approve")
    reject_id = approvals.append_decision(
        conn, project_id=pid, candidate_fact_revision_id=cfr, decision_type="reject",
        revokes_decision_id=approve_id)
    # a separate fact's approve
    cfr2, _ = fx.seed_eligible_fact(conn, pid, "periods_per_year", tag="other")
    other_approve = approvals.append_decision(
        conn, project_id=pid, candidate_fact_revision_id=cfr2, decision_type="approve")
    conn.commit()
    # manual insert referencing a reject decision (not an approve) for cfr → FK fails
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO approved_calculation_input "
            "(project_id, input_role, candidate_fact_revision_id, approval_decision_id, "
            " resolved_numeric_value, resolved_unit) VALUES (%s,'periods_per_year',%s,%s,52,'')",
            (pid, cfr, reject_id),
        )
        conn.commit()
    conn.rollback()
    # referencing another fact's approve
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO approved_calculation_input "
            "(project_id, input_role, candidate_fact_revision_id, approval_decision_id, "
            " resolved_numeric_value, resolved_unit) VALUES (%s,'periods_per_year',%s,%s,52,'')",
            (pid, cfr, other_approve),
        )
        conn.commit()
    conn.rollback()


def test_revocation_integrity(conn, schema_b):
    pid = pg.insert_project(conn, name="revoke")
    conn.commit()
    cfr, _ = fx.seed_eligible_fact(conn, pid, "periods_per_year")
    approve = approvals.append_decision(
        conn, project_id=pid, candidate_fact_revision_id=cfr, decision_type="approve")
    conn.commit()
    # reject without revokes_decision_id → shape CHECK
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO candidate_fact_approval_decision "
            "(project_id, candidate_fact_revision_id, decision_type, decision_seq) "
            "VALUES (%s,%s,'reject',99)", (pid, cfr))
        conn.commit()
    conn.rollback()
    # first withdraw revoking the approve → ok
    approvals.append_decision(
        conn, project_id=pid, candidate_fact_revision_id=cfr, decision_type="withdraw",
        revokes_decision_id=approve)
    conn.commit()
    # second revoke of the same approve → revoke-once unique
    with pytest.raises(Exception):
        approvals.append_decision(
            conn, project_id=pid, candidate_fact_revision_id=cfr, decision_type="reject",
            revokes_decision_id=approve)
        conn.commit()
    conn.rollback()


def test_per_role_typing_check(conn, schema_b):
    pid = pg.insert_project(conn, name="typing")
    conn.commit()
    # a money fact with a non-'per_hour' unit, frozen as the rate role → CHECK violation
    from knowledge.evidence_snapshot.validation import validate_fact
    blob = ev_repo.insert_or_get_blob(conn, project_id=pid, content_hash="rate", byte_size=4)
    snap = ev_repo.insert_snapshot(conn, source_blob_id=blob, project_id=pid, storage_ref="/rate")
    bad = validate_fact("money", value=50, currency_code="USD", as_of_date=fx.AS_OF, unit="wrong")
    cfr, _ = repo.create_eligible_fact(
        conn, project_id=pid, source_snapshot_id=snap, fact=bad,
        subject_label="s", metric_label="rate", source_locator="d")
    approvals.append_decision(conn, project_id=pid, candidate_fact_revision_id=cfr, decision_type="approve")
    conn.commit()
    with pytest.raises(Exception):
        repo.freeze_input(
            conn, project_id=pid, candidate_fact_revision_id=cfr,
            input_role="fully_loaded_rate_per_hour")
        conn.commit()
    conn.rollback()


@pytest.mark.parametrize(
    "resolved_numeric, resolved_unit, label",
    [
        (999, "", "wrong_numeric_value"),   # source fact value is 52
        (52, "bogus_unit", "wrong_unit"),   # source fact unit is ''
    ],
)
def test_value_copy_trigger_rejects_mismatch(conn, schema_b, resolved_numeric, resolved_unit, label):
    """slicebo_assert_frozen_matches_fact rejects a frozen input whose resolved_*
    diverges from the immutable source fact, and no invalid row persists."""
    pid = pg.insert_project(conn, name=f"value-copy-{label}")
    conn.commit()
    # An eligible count fact: numeric_value=52, unit='', no currency/time_unit/period.
    cfr, _ = fx.seed_eligible_fact(conn, pid, "periods_per_year")
    approve = approvals.append_decision(
        conn, project_id=pid, candidate_fact_revision_id=cfr, decision_type="approve")
    conn.commit()

    with pytest.raises(Exception) as ei:
        conn.execute(
            "INSERT INTO approved_calculation_input "
            "(project_id, input_role, candidate_fact_revision_id, approval_decision_id, "
            " resolved_numeric_value, resolved_unit) "
            "VALUES (%s, 'periods_per_year', %s, %s, %s, %s)",
            (pid, cfr, approve, resolved_numeric, resolved_unit),
        )
        conn.commit()
    # Specific to the value-copy trigger (SQLSTATE 23514 check_violation), not the
    # role-typing CHECK: periods_per_year typing accepts 999/'' and ignores unit.
    assert "must equal the source fact" in str(ei.value) or getattr(ei.value, "sqlstate", "") == "23514"
    conn.rollback()

    remaining = conn.execute(
        "SELECT count(*) FROM approved_calculation_input WHERE candidate_fact_revision_id = %s",
        (cfr,),
    ).fetchone()[0]
    assert remaining == 0


# ── Result-input role match + deferred invariant ──────────────────────────────

def test_result_input_role_match_and_duplicate(conn, schema_b):
    from knowledge.automation_roi import service
    pid = pg.insert_project(conn, name="role-match")
    conn.commit()
    seeded = fx.seed_six(conn, pid)
    conn.commit()
    rid = service.compute_and_persist(
        conn, project_id=pid, inputs_by_role=fx.input_map(seeded), computed_by="op")
    conn.commit()
    # duplicate (result, role)
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO calculation_result_input "
            "(project_id, calculation_result_id, approved_calculation_input_id, input_role) "
            "VALUES (%s,%s,%s,'periods_per_year')",
            (pid, rid, seeded["periods_per_year"]["input"]))
        conn.commit()
    conn.rollback()
    # role mismatch: link a periods input under a different role → composite FK fails
    blocked = conn.execute(
        "INSERT INTO calculation_result "
        "(project_id, formula_version, status, formula_input_digest, provenance_fingerprint) "
        "VALUES (%s,'automation_roi.v1','blocked','d','p') RETURNING id::text", (pid,)).fetchone()[0]
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO calculation_result_input "
            "(project_id, calculation_result_id, approved_calculation_input_id, input_role) "
            "VALUES (%s,%s,%s,'annual_recurring_cost')",
            (pid, blocked, seeded["periods_per_year"]["input"]))
        conn.commit()
    conn.rollback()


def test_deferred_invariant_missing_role(conn, schema_b):
    pid = pg.insert_project(conn, name="missing-role")
    conn.commit()
    seeded = fx.seed_six(conn, pid)
    conn.commit()
    rid = conn.execute(VALID_INSERT, (pid,)).fetchone()[0]
    for role in list(ROLES)[:5]:  # only five links
        conn.execute(
            "INSERT INTO calculation_result_input "
            "(project_id, calculation_result_id, approved_calculation_input_id, input_role) "
            "VALUES (%s,%s,%s,%s)", (pid, rid, seeded[role]["input"], role))
    with pytest.raises(Exception):
        conn.commit()
    conn.rollback()


def test_deferred_invariant_revoked_input_blocks_valid(conn, schema_b):
    pid = pg.insert_project(conn, name="revoked-valid")
    conn.commit()
    seeded = fx.seed_six(conn, pid)
    conn.commit()
    cfr = seeded["annual_recurring_cost"]["cfr"]
    approve = approvals.active_approval_id(conn, project_id=pid, candidate_fact_revision_id=cfr)
    approvals.append_decision(
        conn, project_id=pid, candidate_fact_revision_id=cfr, decision_type="withdraw",
        revokes_decision_id=approve)
    conn.commit()
    rid = conn.execute(VALID_INSERT, (pid,)).fetchone()[0]
    for role in ROLES:
        conn.execute(
            "INSERT INTO calculation_result_input "
            "(project_id, calculation_result_id, approved_calculation_input_id, input_role) "
            "VALUES (%s,%s,%s,%s)", (pid, rid, seeded[role]["input"], role))
    with pytest.raises(Exception):
        conn.commit()
    conn.rollback()


def test_blocked_result_allows_unavailable_input(conn, schema_b):
    pid = pg.insert_project(conn, name="blocked-ok")
    conn.commit()
    seeded = fx.seed_six(conn, pid)
    conn.commit()
    ev_repo.insert_retention_event(
        conn, project_id=pid, event_type="tombstone",
        candidate_fact_revision_id=seeded["annual_recurring_cost"]["cfr"])
    conn.commit()
    rid = conn.execute(
        "INSERT INTO calculation_result "
        "(project_id, formula_version, status, formula_input_digest, provenance_fingerprint) "
        "VALUES (%s,'automation_roi.v1','blocked','d','p') RETURNING id::text", (pid,)).fetchone()[0]
    for role in ROLES:
        conn.execute(
            "INSERT INTO calculation_result_input "
            "(project_id, calculation_result_id, approved_calculation_input_id, input_role) "
            "VALUES (%s,%s,%s,%s)", (pid, rid, seeded[role]["input"], role))
    conn.commit()  # blocked is exempt from the active/available requirement
    assert repo.get_result(conn, project_id=pid, result_id=rid)["status"] == "blocked"


# ── v49 calculation_request migration contract ────────────────────────────────

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def test_v49_fresh_apply_creates_request_objects(conn, schema_b49):
    assert pg.table_exists(conn, schema_b49, "calculation_request")
    assert pg.function_exists(conn, schema_b49, "sliceb_creq_guard")
    assert pg.trigger_exists(
        conn, schema_b49, "trg_creq_controlled_transition", "calculation_request")
    for c in ("uq_creq_request_identity", "uq_creq_operation_identity",
              "fk_creq_result_project", "ck_creq_status_shape"):
        assert pg.constraint_exists(conn, schema_b49, c), c


def test_v49_complete_reapply_is_noop(conn, schema_b49):
    pg.apply_v49(conn)  # must not raise
    assert pg.table_exists(conn, schema_b49, "calculation_request")


def test_v49_requires_complete_v48(conn, schema_b):
    # v48 present but incomplete (drop a Slice B table) → v49 refuses.
    prior = pg._begin_autocommit(conn)
    conn.execute("DROP TABLE calculation_result_input")
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception) as ei:
        pg.apply_v49(conn)
    assert "requires v48" in str(ei.value)


def test_v49_partial_schema_is_rejected(conn, schema_b49):
    # Remove the guard trigger → reapply must refuse the partial/divergent v49.
    prior = pg._begin_autocommit(conn)
    conn.execute("DROP TRIGGER trg_creq_controlled_transition ON calculation_request")
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception) as ei:
        pg.apply_v49(conn)
    assert "partial/divergent" in str(ei.value) or "contract violation" in str(ei.value)


# ── v49 controlled-transition guard ───────────────────────────────────────────

def _insert_pending(conn, pid, *, key, digest=DIGEST_A):
    return conn.execute(
        "INSERT INTO calculation_request "
        "(project_id, formula_version, idempotency_key, canonical_request_digest, status) "
        "VALUES (%s, 'automation_roi.v1', %s, %s, 'pending') RETURNING id::text",
        (pid, key, digest),
    ).fetchone()[0]


def test_guard_rejects_delete_of_pending(conn, schema_b49):
    pid = pg.insert_project(conn, name="guard-del")
    conn.commit()
    _insert_pending(conn, pid, key="k-del")
    conn.commit()
    with pytest.raises(Exception):
        conn.execute("DELETE FROM calculation_request")
        conn.commit()
    conn.rollback()


def test_guard_rejects_non_pending_to_committed_update(conn, schema_b49):
    pid = pg.insert_project(conn, name="guard-stay")
    conn.commit()
    req = _insert_pending(conn, pid, key="k-stay")
    conn.commit()
    # Staying pending (here: changing an immutable field) is not the allowed
    # transition and is rejected.
    with pytest.raises(Exception):
        conn.execute(
            "UPDATE calculation_request SET idempotency_key = 'changed' WHERE id = %s", (req,))
        conn.commit()
    conn.rollback()


def test_guard_allows_pending_to_committed_then_freezes(conn, schema_b49):
    from knowledge.automation_roi import service
    pid = pg.insert_project(conn, name="guard-commit")
    conn.commit()
    seeded = fx.seed_six(conn, pid)
    conn.commit()
    # A real result to link.
    rid = service.compute_and_persist(
        conn, project_id=pid, inputs_by_role=fx.input_map(seeded), computed_by="op")
    conn.commit()

    req = _insert_pending(conn, pid, key="k-commit", digest=DIGEST_A)
    conn.commit()
    # Valid transition pending -> committed with the result link → permitted.
    conn.execute(
        "UPDATE calculation_request SET status='committed', "
        "result_calculation_result_id=%s, committed_at=NOW() WHERE id=%s", (rid, req))
    conn.commit()
    row = conn.execute(
        "SELECT status, result_calculation_result_id::text FROM calculation_request WHERE id=%s",
        (req,)).fetchone()
    assert row[0] == "committed" and row[1] == rid

    # Any further mutation of a committed row is rejected.
    with pytest.raises(Exception):
        conn.execute("UPDATE calculation_request SET requested_by='x' WHERE id=%s", (req,))
        conn.commit()
    conn.rollback()
    with pytest.raises(Exception):
        conn.execute("DELETE FROM calculation_request WHERE id=%s", (req,))
        conn.commit()
    conn.rollback()


def test_guard_rejects_immutable_field_change_on_commit(conn, schema_b49):
    from knowledge.automation_roi import service
    pid = pg.insert_project(conn, name="guard-immutable")
    conn.commit()
    seeded = fx.seed_six(conn, pid)
    conn.commit()
    rid = service.compute_and_persist(
        conn, project_id=pid, inputs_by_role=fx.input_map(seeded), computed_by="op")
    conn.commit()
    req = _insert_pending(conn, pid, key="k-immut", digest=DIGEST_B)
    conn.commit()
    # Transition is otherwise valid, but it also mutates an immutable field → reject.
    with pytest.raises(Exception):
        conn.execute(
            "UPDATE calculation_request SET status='committed', "
            "result_calculation_result_id=%s, committed_at=NOW(), formula_version='x' WHERE id=%s",
            (rid, req))
        conn.commit()
    conn.rollback()


def test_request_dual_unique_identities(conn, schema_b49):
    pid = pg.insert_project(conn, name="dual-unique")
    conn.commit()
    _insert_pending(conn, pid, key="dup-key", digest=DIGEST_A)
    conn.commit()
    # Same (project, idempotency_key) → request-identity unique violation.
    with pytest.raises(Exception):
        _insert_pending(conn, pid, key="dup-key", digest=DIGEST_B)
        conn.commit()
    conn.rollback()
    # Same (project, canonical_request_digest) → operation-identity unique violation.
    with pytest.raises(Exception):
        _insert_pending(conn, pid, key="other-key", digest=DIGEST_A)
        conn.commit()
    conn.rollback()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
