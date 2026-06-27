"""PostgreSQL-backed schema tests for the R1.1 research-evidence sidecar."""
import contextlib
import sys
import uuid
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests.evidence_snapshot_pg as pg  # noqa: E402
from knowledge.evidence_snapshot import repository as ev_repo  # noqa: E402
from knowledge.evidence_snapshot.validation import validate_fact  # noqa: E402


SIDECAR_TABLES = (
    "research_source_metadata_revision",
    "research_fact_metadata_revision",
    "research_claim_draft",
    "research_evidence_event",
)

SIDECAR_TRIGGERS = (
    ("trg_rsmr_no_mutation", "research_source_metadata_revision"),
    ("trg_rfmr_no_mutation", "research_fact_metadata_revision"),
    ("trg_rcd_no_mutation", "research_claim_draft"),
    ("trg_ree_no_mutation", "research_evidence_event"),
)

REQUIRED_CONSTRAINTS = (
    "uq_rsmr_id_project",
    "uq_rsmr_id_project_snapshot",
    "fk_rsmr_snapshot_project",
    "fk_rsmr_supersedes_same_snapshot",
    "ck_rsmr_metadata_object",
    "uq_rfmr_id_project",
    "uq_rfmr_id_project_fact",
    "fk_rfmr_fact_project",
    "fk_rfmr_supersedes_fact_project",
    "fk_rfmr_supersedes_same_fact",
    "ck_rfmr_metadata_object",
    "uq_rcd_id_project",
    "fk_rcd_supersedes_claim_project",
    "ck_rcd_claim_text_present",
    "uq_ree_entity_sequence",
    "ck_ree_entity_type",
    "ck_ree_event_type",
    "ck_ree_details_object",
)


@pytest.fixture
def conn():
    pg.require_dsn()
    connection = pg.connect()
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def schema_r51(conn):
    with pg.fresh_schema(conn) as s:
        pg.apply_v51_research(conn)
        yield s


def _seed_snapshot_fact(conn, project_id: str):
    blob_id = ev_repo.insert_or_get_blob(
        conn,
        project_id=project_id,
        content_hash=f"hash-{uuid.uuid4().hex}",
        byte_size=12,
    )
    snapshot_id = ev_repo.insert_snapshot(
        conn,
        source_blob_id=blob_id,
        project_id=project_id,
        storage_ref=f"/sidecar/{uuid.uuid4().hex}",
    )
    fact_id = ev_repo.insert_fact(
        conn,
        project_id=project_id,
        source_snapshot_id=snapshot_id,
        fact=validate_fact("count", value=7, counted_entity="records"),
    )
    conn.commit()
    return snapshot_id, fact_id


def _seed_sidecar_rows(conn, project_id: str):
    snapshot_id, fact_id = _seed_snapshot_fact(conn, project_id)
    source_meta_id = conn.execute(
        """
        INSERT INTO research_source_metadata_revision
            (project_id, source_snapshot_id, canonical_source_locator)
        VALUES (%s, %s, 'doc#1')
        RETURNING id::text
        """,
        (project_id, snapshot_id),
    ).fetchone()[0]
    fact_meta_id = conn.execute(
        """
        INSERT INTO research_fact_metadata_revision
            (project_id, candidate_fact_revision_id, stable_fact_key)
        VALUES (%s, %s, 'fact-key')
        RETURNING id::text
        """,
        (project_id, fact_id),
    ).fetchone()[0]
    claim_id = conn.execute(
        """
        INSERT INTO research_claim_draft (project_id, claim_text)
        VALUES (%s, 'Draft claim')
        RETURNING id::text
        """,
        (project_id,),
    ).fetchone()[0]
    event_id = conn.execute(
        """
        INSERT INTO research_evidence_event
            (project_id, entity_type, entity_id, event_type, event_sequence)
        VALUES (%s, 'claim_draft', %s, 'created', 1)
        RETURNING id::text
        """,
        (project_id, claim_id),
    ).fetchone()[0]
    conn.commit()
    return source_meta_id, fact_meta_id, claim_id, event_id


def test_v51_requires_v47(conn):
    schema = f"research_nov47_{uuid.uuid4().hex[:12]}"
    prior = pg._begin_autocommit(conn)
    try:
        conn.execute(f'CREATE SCHEMA "{schema}"')
        conn.execute(f'SET search_path TO "{schema}"')
        pg._run_script(conn, pg.INIT_SQL)
        pg._run_script(conn, pg.OUTCOMES_SQL)
        with pytest.raises(Exception) as ei:
            pg._run_script(conn, pg.V51_RESEARCH_SQL)
        assert "requires complete v47" in str(ei.value)
    finally:
        with contextlib.suppress(Exception):
            conn.rollback()
        conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        pg._restore_autocommit(conn, prior)


def test_fresh_apply_creates_complete_sidecar_schema(conn, schema_r51):
    for table in SIDECAR_TABLES:
        assert pg.table_exists(conn, schema_r51, table), table
    for constraint in REQUIRED_CONSTRAINTS:
        assert pg.constraint_exists(conn, schema_r51, constraint), constraint
    for trigger, table in SIDECAR_TRIGGERS:
        assert pg.trigger_exists(conn, schema_r51, trigger, table), trigger


def test_complete_reapply_is_noop(conn, schema_r51):
    pg.apply_v51_research(conn)
    for table in SIDECAR_TABLES:
        assert pg.table_exists(conn, schema_r51, table), table


def test_partial_schema_is_rejected(conn, schema_r51):
    prior = pg._begin_autocommit(conn)
    conn.execute("DROP TABLE research_fact_metadata_revision CASCADE")
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception) as ei:
        pg.apply_v51_research(conn)
    assert "partial/divergent" in str(ei.value) or "contract violation" in str(ei.value)


@pytest.mark.parametrize("table,index", [
    ("research_source_metadata_revision", 0),
    ("research_fact_metadata_revision", 1),
    ("research_claim_draft", 2),
    ("research_evidence_event", 3),
])
def test_sidecar_tables_are_append_only(conn, schema_r51, table, index):
    project_id = pg.insert_project(conn, name="append-only")
    conn.commit()
    ids = _seed_sidecar_rows(conn, project_id)
    target_id = ids[index]

    with pytest.raises(Exception):
        conn.execute(f"UPDATE {table} SET project_id = project_id WHERE id = %s", (target_id,))
        conn.commit()
    conn.rollback()

    with pytest.raises(Exception):
        conn.execute(f"DELETE FROM {table} WHERE id = %s", (target_id,))
        conn.commit()
    conn.rollback()

    assert conn.execute(f"SELECT count(*) FROM {table} WHERE id = %s", (target_id,)).fetchone()[0] == 1


def test_cross_project_source_and_fact_parent_links_are_rejected(conn, schema_r51):
    project_a = pg.insert_project(conn, name="project-a")
    project_b = pg.insert_project(conn, name="project-b")
    conn.commit()
    snapshot_a, fact_a = _seed_snapshot_fact(conn, project_a)

    with pytest.raises(Exception):
        conn.execute(
            """
            INSERT INTO research_source_metadata_revision (project_id, source_snapshot_id)
            VALUES (%s, %s)
            """,
            (project_b, snapshot_a),
        )
        conn.commit()
    conn.rollback()

    with pytest.raises(Exception):
        conn.execute(
            """
            INSERT INTO research_fact_metadata_revision (project_id, candidate_fact_revision_id)
            VALUES (%s, %s)
            """,
            (project_b, fact_a),
        )
        conn.commit()
    conn.rollback()


def test_claim_table_has_no_source_or_fact_relationship(conn, schema_r51):
    columns = {
        row[0]
        for row in conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = 'research_claim_draft'
            """,
            (schema_r51,),
        ).fetchall()
    }
    assert "source_snapshot_id" not in columns
    assert "candidate_fact_revision_id" not in columns
