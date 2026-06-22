"""PostgreSQL-backed Slice A schema, trigger, retention, and migration tests.

These run against a genuine PostgreSQL database (TEST_EVIDENCE_PG_DSN). They are
skipped when no disposable database is provided. No SQLite/mock substitution.
"""
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests.evidence_snapshot_pg as pg  # noqa: E402
from knowledge.evidence_snapshot import capture, repository as repo  # noqa: E402
from knowledge.evidence_snapshot.validation import validate_fact  # noqa: E402


@pytest.fixture
def conn():
    pg.require_dsn()
    connection = pg.connect()
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def schema(conn):
    with pg.fresh_schema(conn) as s:
        yield s


SHA = "sha256"


def _seed_blob_snapshot_fact(conn, project_id, *, content_hash="hashA", storage_ref="/store/a"):
    blob_id = repo.insert_or_get_blob(
        conn, project_id=project_id, content_hash=content_hash, byte_size=10, hash_algorithm=SHA,
    )
    snap_id = repo.insert_snapshot(
        conn, source_blob_id=blob_id, project_id=project_id, storage_ref=storage_ref,
    )
    fact_id = repo.insert_fact(
        conn, project_id=project_id, source_snapshot_id=snap_id,
        fact=validate_fact("count", value=5, counted_entity="widgets"),
    )
    conn.commit()
    return blob_id, snap_id, fact_id


# ── 1. Blob/Snapshot responsibility split ──────────────────────────────────

def test_identical_content_one_blob_separate_snapshots(conn, schema):
    pid = pg.insert_project(conn, name="split")
    conn.commit()
    blob1 = repo.insert_or_get_blob(conn, project_id=pid, content_hash="h", byte_size=4)
    snap1 = repo.insert_snapshot(conn, source_blob_id=blob1, project_id=pid, storage_ref="/a")
    blob2 = repo.insert_or_get_blob(conn, project_id=pid, content_hash="h", byte_size=4)
    snap2 = repo.insert_snapshot(conn, source_blob_id=blob2, project_id=pid, storage_ref="/b")
    conn.commit()
    assert blob1 == blob2
    assert snap1 != snap2
    assert conn.execute("SELECT count(*) FROM source_blob").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM source_snapshot").fetchone()[0] == 2


# ── 2. Retry idempotency ────────────────────────────────────────────────────

def test_retry_idempotency_same_operation_one_snapshot(conn, schema):
    pid = pg.insert_project(conn, name="retry")
    conn.commit()
    s1 = capture.capture_upload(
        project_id=pid, content=b"abc", storage_ref="/s/1", operation_id="op-A", connection=conn,
    )
    s1_again = capture.capture_upload(
        project_id=pid, content=b"abc", storage_ref="/s/1", operation_id="op-A", connection=conn,
    )
    assert s1_again == s1  # idempotent retry yields the same committed snapshot
    # New operation id with identical bytes is a new capture event.
    s2 = capture.capture_upload(
        project_id=pid, content=b"abc", storage_ref="/s/2", operation_id="op-B", connection=conn,
    )
    assert s2 != s1
    assert conn.execute("SELECT count(*) FROM source_blob").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM source_snapshot").fetchone()[0] == 2
    assert conn.execute(
        "SELECT count(*) FROM ingest_operation WHERE status = 'committed'"
    ).fetchone()[0] == 2


# ── 3. Project isolation ─────────────────────────────────────────────────────

def test_identical_bytes_distinct_projects_distinct_blobs(conn, schema):
    p1 = pg.insert_project(conn, name="p1")
    p2 = pg.insert_project(conn, name="p2")
    conn.commit()
    b1 = repo.insert_or_get_blob(conn, project_id=p1, content_hash="same", byte_size=3)
    b2 = repo.insert_or_get_blob(conn, project_id=p2, content_hash="same", byte_size=3)
    conn.commit()
    assert b1 != b2
    assert conn.execute("SELECT count(*) FROM source_blob").fetchone()[0] == 2


# ── 4. Append-only enforcement ───────────────────────────────────────────────

@pytest.mark.parametrize("table,pk_getter", [
    ("source_blob", lambda ids: ids[0]),
    ("source_snapshot", lambda ids: ids[1]),
    ("candidate_fact_revision", lambda ids: ids[2]),
])
def test_update_and_delete_rejected_on_immutable_tables(conn, schema, table, pk_getter):
    pid = pg.insert_project(conn, name="immutable")
    conn.commit()
    ids = _seed_blob_snapshot_fact(conn, pid)
    target = pk_getter(ids)

    with pytest.raises(Exception):
        conn.execute(f"UPDATE {table} SET created_by = 'x' WHERE id = %s", (target,))
        conn.commit()
    conn.rollback()

    with pytest.raises(Exception):
        conn.execute(f"DELETE FROM {table} WHERE id = %s", (target,))
        conn.commit()
    conn.rollback()

    # Row still present and unchanged.
    assert conn.execute(f"SELECT count(*) FROM {table} WHERE id = %s", (target,)).fetchone()[0] == 1


def test_retention_event_is_append_only(conn, schema):
    pid = pg.insert_project(conn, name="ret-immutable")
    conn.commit()
    blob, _snap, _fact = _seed_blob_snapshot_fact(conn, pid)
    event_id = repo.insert_retention_event(conn, project_id=pid, event_type="legal_hold", source_blob_id=blob)
    conn.commit()
    with pytest.raises(Exception):
        conn.execute("DELETE FROM evidence_retention_event WHERE id = %s", (event_id,))
        conn.commit()
    conn.rollback()
    assert conn.execute("SELECT count(*) FROM evidence_retention_event").fetchone()[0] == 1


# ── 5. Retention XOR + availability inheritance ──────────────────────────────

def test_retention_requires_exactly_one_target(conn, schema):
    pid = pg.insert_project(conn, name="xor")
    conn.commit()
    blob, snap, _fact = _seed_blob_snapshot_fact(conn, pid)

    with pytest.raises(Exception):  # zero targets
        conn.execute(
            "INSERT INTO evidence_retention_event (project_id, event_type) VALUES (%s, 'tombstone')",
            (pid,),
        )
        conn.commit()
    conn.rollback()

    with pytest.raises(Exception):  # two targets
        conn.execute(
            """INSERT INTO evidence_retention_event
               (project_id, event_type, source_blob_id, source_snapshot_id)
               VALUES (%s, 'tombstone', %s, %s)""",
            (pid, blob, snap),
        )
        conn.commit()
    conn.rollback()

    ok = repo.insert_retention_event(conn, project_id=pid, event_type="tombstone", source_blob_id=blob)
    conn.commit()
    assert ok


def test_legal_hold_does_not_block_availability(conn, schema):
    pid = pg.insert_project(conn, name="hold")
    conn.commit()
    blob, snap, fact = _seed_blob_snapshot_fact(conn, pid)
    repo.insert_retention_event(conn, project_id=pid, event_type="legal_hold", source_blob_id=blob)
    conn.commit()
    assert repo.snapshot_available(conn, snap) is True
    assert repo.fact_available(conn, fact) is True


def test_blob_tombstone_makes_snapshot_and_fact_unavailable(conn, schema):
    pid = pg.insert_project(conn, name="blob-tomb")
    conn.commit()
    blob, snap, fact = _seed_blob_snapshot_fact(conn, pid)
    repo.insert_retention_event(conn, project_id=pid, event_type="tombstone", source_blob_id=blob)
    conn.commit()
    assert repo.snapshot_available(conn, snap) is False
    assert repo.fact_available(conn, fact) is False


def test_snapshot_redact_makes_fact_unavailable(conn, schema):
    pid = pg.insert_project(conn, name="snap-redact")
    conn.commit()
    blob, snap, fact = _seed_blob_snapshot_fact(conn, pid, content_hash="hashB", storage_ref="/store/b")
    repo.insert_retention_event(conn, project_id=pid, event_type="redact", source_snapshot_id=snap)
    conn.commit()
    assert repo.snapshot_available(conn, snap) is False
    assert repo.fact_available(conn, fact) is False


# ── 6. Typed fact: Decimal/NUMERIC behavior at the DB layer ──────────────────

def test_numeric_value_roundtrips_as_decimal(conn, schema):
    pid = pg.insert_project(conn, name="numeric")
    conn.commit()
    blob = repo.insert_or_get_blob(conn, project_id=pid, content_hash="n", byte_size=1)
    snap = repo.insert_snapshot(conn, source_blob_id=blob, project_id=pid, storage_ref="/n")
    fact = repo.insert_fact(
        conn, project_id=pid, source_snapshot_id=snap,
        fact=validate_fact("money", value="1999.95", currency_code="USD", as_of_date=date(2026, 1, 1)),
    )
    conn.commit()
    stored = conn.execute(
        "SELECT numeric_value FROM candidate_fact_revision WHERE id = %s", (fact,)
    ).fetchone()[0]
    assert isinstance(stored, Decimal)
    assert stored == Decimal("1999.95")


def test_db_rejects_text_fact_with_numeric_value(conn, schema):
    pid = pg.insert_project(conn, name="text-guard")
    conn.commit()
    blob = repo.insert_or_get_blob(conn, project_id=pid, content_hash="t", byte_size=1)
    snap = repo.insert_snapshot(conn, source_blob_id=blob, project_id=pid, storage_ref="/t")
    conn.commit()
    with pytest.raises(Exception):  # ck_cfr_numeric_shape
        conn.execute(
            """INSERT INTO candidate_fact_revision
               (project_id, source_snapshot_id, fact_type, numeric_value, text_value)
               VALUES (%s, %s, 'text', 42, 'hello')""",
            (pid, snap),
        )
        conn.commit()
    conn.rollback()


def test_candidate_fact_requires_snapshot(conn, schema):
    pid = pg.insert_project(conn, name="fk")
    conn.commit()
    with pytest.raises(Exception):  # NOT NULL on source_snapshot_id
        conn.execute(
            """INSERT INTO candidate_fact_revision
               (project_id, source_snapshot_id, fact_type, numeric_value)
               VALUES (%s, NULL, 'count', 1)""",
            (pid,),
        )
        conn.commit()
    conn.rollback()


# ── Project deletion rejected while evidence exists ──────────────────────────

def test_project_deletion_rejected_while_evidence_exists(conn, schema):
    pid = pg.insert_project(conn, name="protected")
    conn.commit()
    _seed_blob_snapshot_fact(conn, pid)
    with pytest.raises(Exception):  # FK ON DELETE RESTRICT
        conn.execute("DELETE FROM projects WHERE id = %s", (pid,))
        conn.commit()
    conn.rollback()


# ── 11. Bootstrap, reapply no-op, partial/divergent, and upgrade ─────────────

def test_bootstrapped_schema_is_complete(conn, schema):
    assert pg.classify_schema(conn, schema) == "complete"


def test_complete_reapply_is_noop(conn, schema):
    assert pg.classify_schema(conn, schema) == "complete"
    pg.apply_v47(conn)  # re-run the migration
    assert pg.classify_schema(conn, schema) == "complete"
    # Guarded DDL must not duplicate triggers.
    trigger_count = conn.execute(
        """
        SELECT count(*) FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s
          AND t.tgname IN (
              'trg_source_blob_no_mutation', 'trg_source_snapshot_no_mutation',
              'trg_cfr_no_mutation', 'trg_retention_no_mutation')
        """,
        (schema,),
    ).fetchone()[0]
    assert trigger_count == 4


def test_empty_schema_classifies_none_and_upgrades(conn):
    name = f"slicea_upgrade_{__import__('uuid').uuid4().hex[:12]}"
    conn.autocommit = True
    try:
        conn.execute(f'CREATE SCHEMA "{name}"')
        conn.execute(f'SET search_path TO "{name}"')
        # Base schema only (init -> outcomes), no Slice A objects yet.
        pg._run_script(conn, pg.INIT_SQL)
        pg._run_script(conn, pg.OUTCOMES_SQL)
        assert pg.classify_schema(conn, name) == "none"
        # Transactional Slice A upgrade onto existing base schema.
        pg.apply_v47(conn)
        assert pg.classify_schema(conn, name) == "complete"
    finally:
        conn.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        conn.autocommit = False


def test_partial_schema_is_detected(conn, schema):
    assert pg.classify_schema(conn, schema) == "complete"
    prior = pg._begin_autocommit(conn)
    conn.execute("DROP TABLE candidate_fact_revision CASCADE")
    try:
        assert pg.classify_schema(conn, schema) == "partial"
    finally:
        pg._restore_autocommit(conn, prior)


# ── 2 (defect): v47 itself rejects partial / divergent schema ───────────────

def test_v47_rejects_partial_schema(conn):
    """A stray Slice A object present, but not the complete contract → v47 fails."""
    name = f"slicea_partial_{__import__('uuid').uuid4().hex[:12]}"
    prior = pg._begin_autocommit(conn)
    try:
        conn.execute(f'CREATE SCHEMA "{name}"')
        conn.execute(f'SET search_path TO "{name}"')
        pg._run_script(conn, pg.INIT_SQL)
        pg._run_script(conn, pg.OUTCOMES_SQL)
        # Intentionally partial: one Slice A table exists, nothing else.
        conn.execute("CREATE TABLE source_blob (id UUID PRIMARY KEY DEFAULT gen_random_uuid())")
        assert pg.classify_schema(conn, name) == "partial"
        with pytest.raises(Exception):  # preflight RAISE inside the migration
            pg._run_script(conn, pg.V47_SQL)
        conn.rollback()  # clear the aborted migration transaction
        # Migration rolled back: still partial, not silently completed.
        assert pg.classify_schema(conn, name) == "partial"
    finally:
        conn.rollback()
        conn.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        pg._restore_autocommit(conn, prior)


def test_v47_rejects_divergent_complete_schema(conn, schema):
    """A complete schema with a missing trigger (divergent) → v47 fails."""
    assert pg.classify_schema(conn, schema) == "complete"
    prior = pg._begin_autocommit(conn)
    try:
        conn.execute("DROP TRIGGER trg_cfr_no_mutation ON candidate_fact_revision")
        assert pg.classify_schema(conn, schema) == "partial"
        with pytest.raises(Exception):
            pg._run_script(conn, pg.V47_SQL)
        conn.rollback()  # clear the aborted migration transaction
    finally:
        pg._restore_autocommit(conn, prior)


def test_v47_rejects_definition_divergence_with_correct_names(conn, schema):
    """All expected names exist, but definitions are wrong → v47 must reject.

    This proves the migration validates the contract by catalog metadata, not by
    object names: the name-based classifier still reports 'complete'.
    """
    assert pg.classify_schema(conn, schema) == "complete"
    prior = pg._begin_autocommit(conn)
    try:
        # (a) uq_source_blob_id_project: same name, WRONG column pair.
        conn.execute("ALTER TABLE source_blob DROP CONSTRAINT uq_source_blob_id_project CASCADE")
        conn.execute("ALTER TABLE source_blob ADD CONSTRAINT uq_source_blob_id_project UNIQUE (id, hash_algorithm)")
        # Restore the FK names dropped by CASCADE (definitions now diverge, names exist).
        conn.execute("ALTER TABLE source_snapshot ADD CONSTRAINT fk_snapshot_blob_project "
                     "FOREIGN KEY (source_blob_id) REFERENCES source_blob(id)")
        conn.execute("ALTER TABLE evidence_retention_event ADD CONSTRAINT fk_ret_blob_project "
                     "FOREIGN KEY (source_blob_id) REFERENCES source_blob(id)")
        # (b) trg_cfr_no_mutation: same name, WRONG event (BEFORE INSERT, not UPDATE/DELETE).
        conn.execute("DROP TRIGGER trg_cfr_no_mutation ON candidate_fact_revision")
        conn.execute("CREATE TRIGGER trg_cfr_no_mutation BEFORE INSERT ON candidate_fact_revision "
                     "FOR EACH ROW EXECUTE FUNCTION slicea_reject_mutation()")

        # Name-only classification is fooled — every expected name is still present.
        assert pg.classify_schema(conn, schema) == "complete"

        # The real v47 migration rejects on definition mismatch.
        with pytest.raises(Exception):
            pg._run_script(conn, pg.V47_SQL)
        conn.rollback()
    finally:
        pg._restore_autocommit(conn, prior)


def test_v47_clean_bootstrap_and_complete_reapply_still_pass(conn):
    """Clean bootstrap succeeds; a complete re-apply is a verified no-op."""
    name = f"slicea_boot_{__import__('uuid').uuid4().hex[:12]}"
    prior = pg._begin_autocommit(conn)
    try:
        conn.execute(f'CREATE SCHEMA "{name}"')
        conn.execute(f'SET search_path TO "{name}"')
        pg._run_script(conn, pg.INIT_SQL)
        pg._run_script(conn, pg.OUTCOMES_SQL)
        pg._run_script(conn, pg.V47_SQL)          # clean bootstrap
        assert pg.classify_schema(conn, name) == "complete"
        pg._run_script(conn, pg.V47_SQL)          # complete re-apply: no-op
        assert pg.classify_schema(conn, name) == "complete"
    finally:
        conn.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        pg._restore_autocommit(conn, prior)


# ── 1 (defect): project-consistent composite FKs reject cross-project links ──

def test_cross_project_snapshot_rejected(conn, schema):
    pa = pg.insert_project(conn, name="A")
    pb = pg.insert_project(conn, name="B")
    conn.commit()
    blob_a = repo.insert_or_get_blob(conn, project_id=pa, content_hash="x", byte_size=1)
    conn.commit()
    with pytest.raises(Exception):  # fk_snapshot_blob_project
        conn.execute(
            """INSERT INTO source_snapshot (source_blob_id, project_id, storage_ref)
               VALUES (%s, %s, '/x')""",
            (blob_a, pb),
        )
        conn.commit()
    conn.rollback()


def test_cross_project_fact_rejected(conn, schema):
    pa = pg.insert_project(conn, name="A")
    pb = pg.insert_project(conn, name="B")
    conn.commit()
    blob_a = repo.insert_or_get_blob(conn, project_id=pa, content_hash="x", byte_size=1)
    snap_a = repo.insert_snapshot(conn, source_blob_id=blob_a, project_id=pa, storage_ref="/a")
    conn.commit()
    with pytest.raises(Exception):  # fk_cfr_snapshot_project
        conn.execute(
            """INSERT INTO candidate_fact_revision
               (project_id, source_snapshot_id, fact_type, numeric_value, counted_entity)
               VALUES (%s, %s, 'count', 1, 'x')""",
            (pb, snap_a),
        )
        conn.commit()
    conn.rollback()


def test_cross_project_ingest_operation_rejected(conn, schema):
    pa = pg.insert_project(conn, name="A")
    pb = pg.insert_project(conn, name="B")
    conn.commit()
    blob_a = repo.insert_or_get_blob(conn, project_id=pa, content_hash="x", byte_size=1)
    snap_a = repo.insert_snapshot(conn, source_blob_id=blob_a, project_id=pa, storage_ref="/a")
    conn.commit()
    with pytest.raises(Exception):  # fk_ingest_snapshot_project
        conn.execute(
            """INSERT INTO ingest_operation (project_id, operation_id, status, source_snapshot_id)
               VALUES (%s, 'op-x', 'committed', %s)""",
            (pb, snap_a),
        )
        conn.commit()
    conn.rollback()


def test_cross_project_retention_target_rejected(conn, schema):
    pa = pg.insert_project(conn, name="A")
    pb = pg.insert_project(conn, name="B")
    conn.commit()
    blob_a = repo.insert_or_get_blob(conn, project_id=pa, content_hash="x", byte_size=1)
    conn.commit()
    with pytest.raises(Exception):  # fk_ret_blob_project
        conn.execute(
            """INSERT INTO evidence_retention_event (project_id, event_type, source_blob_id)
               VALUES (%s, 'tombstone', %s)""",
            (pb, blob_a),
        )
        conn.commit()
    conn.rollback()
