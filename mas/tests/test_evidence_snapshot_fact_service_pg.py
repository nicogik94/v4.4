"""Disposable-PostgreSQL coverage for the bounded candidate-fact service.

Proves the wrapper against a genuine v47 schema: it binds a validated fact to an
existing same-project snapshot, rejects a foreign-project snapshot, preserves
caller transaction ownership (rollback discards it; the service never commits),
and appends rather than mutates. Skips unless TEST_EVIDENCE_PG_DSN is set.
"""
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests.evidence_snapshot_pg as pg  # noqa: E402
from knowledge.evidence_snapshot import fact_service  # noqa: E402
from knowledge.evidence_snapshot.validation import validate_fact  # noqa: E402


@pytest.fixture(autouse=True)
def snapshot_enabled(monkeypatch):
    monkeypatch.setenv("MAS_EVIDENCE_SNAPSHOT_ENABLED", "true")


@pytest.fixture
def schema():
    import psycopg

    conn = pg.connect()
    with pg.fresh_schema(conn) as schema_name:
        # The service now requires an EXPLICITLY pinned READ COMMITTED level;
        # pin it here (no active transaction) before invoking the service.
        conn.isolation_level = psycopg.IsolationLevel.READ_COMMITTED
        yield conn, schema_name
    conn.close()


def _project_with_snapshot(conn, tag):
    project_id = pg.insert_project(conn, name=f"fact-{tag}")
    blob = ev_blob(conn, project_id, tag)
    snapshot = pg_snapshot(conn, project_id, blob, tag)
    return project_id, snapshot


def ev_blob(conn, project_id, tag):
    from knowledge.evidence_snapshot import repository as ev_repo

    return ev_repo.insert_or_get_blob(
        conn, project_id=project_id,
        content_hash=f"{tag}-{uuid.uuid4().hex}", byte_size=17,
    )


def pg_snapshot(conn, project_id, blob, tag):
    from knowledge.evidence_snapshot import repository as ev_repo

    return ev_repo.insert_snapshot(
        conn, source_blob_id=blob, project_id=project_id,
        storage_ref=f"/facts/{tag}/{uuid.uuid4().hex}",
    )


def _count_facts(conn, project_id):
    return conn.execute(
        "SELECT count(*) FROM candidate_fact_revision WHERE project_id = %s",
        (project_id,),
    ).fetchone()[0]


def test_creates_fact_bound_to_same_project_snapshot(schema):
    conn, _ = schema
    project_id, snapshot = _project_with_snapshot(conn, "bind")
    fact_id = fact_service.create_candidate_fact_revision(
        conn, project_id=project_id, source_snapshot_id=snapshot,
        fact=validate_fact("count", value=11, counted_entity="records"),
        created_by="op",
    )
    row = conn.execute(
        """SELECT source_snapshot_id::text, fact_type, numeric_value
           FROM candidate_fact_revision WHERE id = %s""",
        (fact_id,),
    ).fetchone()
    assert row[0] == snapshot and row[1] == "count"


def test_rejects_foreign_project_snapshot(schema):
    conn, _ = schema
    project_a, snapshot_a = _project_with_snapshot(conn, "a")
    project_b = pg.insert_project(conn, name="fact-b")
    with pytest.raises(fact_service.CandidateFactSourceSnapshotNotFound):
        fact_service.create_candidate_fact_revision(
            conn, project_id=project_b, source_snapshot_id=snapshot_a,
            fact=validate_fact("count", value=1, counted_entity="x"),
        )
    conn.rollback()


def test_caller_rollback_discards_fact_service_never_commits(schema):
    conn, _ = schema
    project_id, snapshot = _project_with_snapshot(conn, "rollback")
    conn.commit()
    assert _count_facts(conn, project_id) == 0

    fact_service.create_candidate_fact_revision(
        conn, project_id=project_id, source_snapshot_id=snapshot,
        fact=validate_fact("count", value=5, counted_entity="rows"),
    )
    # the service did not commit; a caller rollback discards the fact entirely
    conn.rollback()
    assert _count_facts(conn, project_id) == 0


def test_appends_multiple_facts_on_commit(schema):
    conn, _ = schema
    project_id, snapshot = _project_with_snapshot(conn, "append")
    for value in (1, 2, 3):
        fact_service.create_candidate_fact_revision(
            conn, project_id=project_id, source_snapshot_id=snapshot,
            fact=validate_fact("count", value=value, counted_entity="rows"),
        )
    conn.commit()
    assert _count_facts(conn, project_id) == 3
