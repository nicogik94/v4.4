"""PostgreSQL-backed contract tests for R1.2 controlled intake."""
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
from research_evidence import intake_service  # noqa: E402
from research_evidence.intake_models import (  # noqa: E402
    ResearchEvidenceIntakeCreate,
    ResearchEvidenceIntakeItemCreate,
)


TABLES = ("research_evidence_intake", "research_evidence_intake_item")
CONSTRAINTS = (
    "research_evidence_intake_pkey",
    "uq_rei_id_project",
    "uq_rei_id_project_snapshot",
    "fk_rei_project",
    "fk_rei_snapshot_project",
    "fk_rei_source_metadata_snapshot",
    "ck_rei_intake_method",
    "ck_rei_state_draft",
    "ck_rei_selection_reason_nonblank",
    "ck_rei_created_by_nonblank",
    "research_evidence_intake_item_pkey",
    "uq_reii_id_project",
    "fk_reii_project",
    "fk_reii_intake_snapshot",
    "fk_reii_fact_project",
    "fk_reii_fact_metadata_fact",
    "fk_reii_claim_project",
    "ck_reii_item_kind",
    "ck_reii_state_draft",
    "ck_reii_created_by_nonblank",
    "ck_reii_target_shape",
)
TRIGGERS = (
    ("trg_rei_no_mutation", "research_evidence_intake"),
    ("trg_reii_no_mutation", "research_evidence_intake_item"),
    ("trg_reii_validate_snapshot", "research_evidence_intake_item"),
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
def schema_v53(conn):
    with pg.fresh_schema(conn) as schema:
        pg.apply_v51_research(conn)
        pg.apply_v52_research(conn)
        pg.apply_v53_research_intake(conn)
        yield schema


@pytest.fixture(autouse=True)
def feature_enabled(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")


def _seed_source(conn, project_id: str, *, tag: str):
    blob_id = ev_repo.insert_or_get_blob(
        conn,
        project_id=project_id,
        content_hash=f"hash-{tag}-{uuid.uuid4().hex}",
        byte_size=12,
    )
    snapshot_id = ev_repo.insert_snapshot(
        conn,
        source_blob_id=blob_id,
        project_id=project_id,
        storage_ref=f"/r1.2/{tag}/{uuid.uuid4().hex}",
    )
    fact_id = ev_repo.insert_fact(
        conn,
        project_id=project_id,
        source_snapshot_id=snapshot_id,
        fact=validate_fact("count", value=7, counted_entity="records"),
        created_by="operator",
    )
    source_metadata_id = conn.execute(
        """
        INSERT INTO research_source_metadata_revision
            (project_id, source_snapshot_id, citation_label, created_by)
        VALUES (%s, %s, %s, 'operator')
        RETURNING id::text
        """,
        (project_id, snapshot_id, f"source-{tag}"),
    ).fetchone()[0]
    fact_metadata_id = conn.execute(
        """
        INSERT INTO research_fact_metadata_revision
            (project_id, candidate_fact_revision_id, stable_fact_key, created_by)
        VALUES (%s, %s, %s, 'operator')
        RETURNING id::text
        """,
        (project_id, fact_id, f"fact-{tag}"),
    ).fetchone()[0]
    claim_id = conn.execute(
        """
        INSERT INTO research_claim_draft (project_id, claim_text, created_by)
        VALUES (%s, %s, 'operator')
        RETURNING id::text
        """,
        (project_id, f"Draft claim {tag}"),
    ).fetchone()[0]
    conn.commit()
    return {
        "blob": blob_id,
        "snapshot": snapshot_id,
        "source_metadata": source_metadata_id,
        "fact": fact_id,
        "fact_metadata": fact_metadata_id,
        "claim": claim_id,
    }


def _insert_intake(conn, project_id: str, source: dict[str, str]) -> str:
    return conn.execute(
        """
        INSERT INTO research_evidence_intake
            (project_id, source_snapshot_id, source_metadata_revision_id,
             selection_reason, created_by)
        VALUES (%s, %s, %s, 'Operator selected this source', 'operator')
        RETURNING id::text
        """,
        (project_id, source["snapshot"], source["source_metadata"]),
    ).fetchone()[0]


def _insert_fact_item(
    conn,
    project_id: str,
    intake_id: str,
    source: dict[str, str],
) -> str:
    return conn.execute(
        """
        INSERT INTO research_evidence_intake_item
            (project_id, research_evidence_intake_id, source_snapshot_id,
             item_kind, candidate_fact_revision_id, fact_metadata_revision_id,
             created_by)
        VALUES (%s, %s, %s, 'candidate_fact', %s, %s, 'operator')
        RETURNING id::text
        """,
        (
            project_id,
            intake_id,
            source["snapshot"],
            source["fact"],
            source["fact_metadata"],
        ),
    ).fetchone()[0]


def _insert_claim_item(
    conn,
    project_id: str,
    intake_id: str,
    source: dict[str, str],
) -> str:
    return conn.execute(
        """
        INSERT INTO research_evidence_intake_item
            (project_id, research_evidence_intake_id, source_snapshot_id,
             item_kind, claim_draft_id, created_by)
        VALUES (%s, %s, %s, 'claim_draft', %s, 'operator')
        RETURNING id::text
        """,
        (project_id, intake_id, source["snapshot"], source["claim"]),
    ).fetchone()[0]


def test_clean_apply_creates_exact_v53_contract(conn, schema_v53):
    for table in TABLES:
        assert pg.table_exists(conn, schema_v53, table), table
    for constraint in CONSTRAINTS:
        assert pg.constraint_exists(conn, schema_v53, constraint), constraint
    for trigger, table in TRIGGERS:
        assert pg.trigger_exists(conn, schema_v53, trigger, table), trigger
    assert pg.function_exists(
        conn, schema_v53, "research_evidence_intake_validate_item_snapshot"
    )


def test_complete_v53_reapply_is_safe(conn, schema_v53):
    pg.apply_v53_research_intake(conn)
    for table in TABLES:
        assert pg.table_exists(conn, schema_v53, table)


def test_v53_rejects_missing_v51_dependency(conn):
    with pg.fresh_schema(conn):
        with pytest.raises(Exception, match="requires complete v51"):
            pg.apply_v53_research_intake(conn)


def test_v53_rejects_missing_v52_dependency(conn):
    with pg.fresh_schema(conn):
        pg.apply_v51_research(conn)
        with pytest.raises(Exception, match="requires complete v52"):
            pg.apply_v53_research_intake(conn)


def test_v53_rejects_partial_state(conn, schema_v53):
    prior = pg._begin_autocommit(conn)
    conn.execute("DROP TABLE research_evidence_intake_item CASCADE")
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception, match="partial/divergent"):
        pg.apply_v53_research_intake(conn)


def test_v53_rejects_divergent_foreign_key(conn, schema_v53):
    prior = pg._begin_autocommit(conn)
    conn.execute(
        "ALTER TABLE research_evidence_intake DROP CONSTRAINT fk_rei_project"
    )
    conn.execute(
        """
        ALTER TABLE research_evidence_intake
        ADD CONSTRAINT fk_rei_project
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        """
    )
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception, match="divergent foreign keys"):
        pg.apply_v53_research_intake(conn)


def test_v53_rejects_divergent_draft_check(conn, schema_v53):
    prior = pg._begin_autocommit(conn)
    conn.execute(
        "ALTER TABLE research_evidence_intake DROP CONSTRAINT ck_rei_state_draft"
    )
    conn.execute(
        """
        ALTER TABLE research_evidence_intake
        ADD CONSTRAINT ck_rei_state_draft CHECK (state IN ('draft', 'released'))
        """
    )
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception, match="divergent method or draft-only checks"):
        pg.apply_v53_research_intake(conn)


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        ("intake_method", "automated", "ck_rei_intake_method"),
        ("state", "released", "ck_rei_state_draft"),
        ("selection_reason", "   ", "ck_rei_selection_reason_nonblank"),
        ("selection_reason", "\t", "ck_rei_selection_reason_nonblank"),
        ("created_by", "   ", "ck_rei_created_by_nonblank"),
        ("created_by", "\t", "ck_rei_created_by_nonblank"),
    ],
)
def test_intake_database_constraints(
    conn, schema_v53, column, value, constraint
):
    project_id = pg.insert_project(conn, name=f"intake-check-{column}")
    conn.commit()
    source = _seed_source(conn, project_id, tag=column)
    values = {
        "intake_method": "operator_selected_existing_snapshot",
        "state": "draft",
        "selection_reason": "reason",
        "created_by": "operator",
    }
    values[column] = value
    with pytest.raises(Exception) as exc:
        conn.execute(
            """
            INSERT INTO research_evidence_intake
                (project_id, source_snapshot_id, source_metadata_revision_id,
                 intake_method, state, selection_reason, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                project_id,
                source["snapshot"],
                source["source_metadata"],
                values["intake_method"],
                values["state"],
                values["selection_reason"],
                values["created_by"],
            ),
        )
        conn.commit()
    conn.rollback()
    assert constraint in str(exc.value)


def test_item_state_actor_and_xor_constraints(conn, schema_v53):
    project_id = pg.insert_project(conn, name="item-checks")
    conn.commit()
    source = _seed_source(conn, project_id, tag="item-checks")
    intake_id = _insert_intake(conn, project_id, source)
    conn.commit()

    invalid_statements = (
        (
            """
            INSERT INTO research_evidence_intake_item
                (project_id, research_evidence_intake_id, source_snapshot_id,
                 item_kind, claim_draft_id, state, created_by)
            VALUES (%s, %s, %s, 'claim_draft', %s, 'released', 'operator')
            """,
            (
                project_id,
                intake_id,
                source["snapshot"],
                source["claim"],
            ),
            "ck_reii_state_draft",
        ),
        (
            """
            INSERT INTO research_evidence_intake_item
                (project_id, research_evidence_intake_id, source_snapshot_id,
                 item_kind, claim_draft_id, created_by)
            VALUES (%s, %s, %s, 'claim_draft', %s, '  ')
            """,
            (
                project_id,
                intake_id,
                source["snapshot"],
                source["claim"],
            ),
            "ck_reii_created_by_nonblank",
        ),
        (
            """
            INSERT INTO research_evidence_intake_item
                (project_id, research_evidence_intake_id, source_snapshot_id,
                 item_kind, candidate_fact_revision_id, created_by)
            VALUES (%s, %s, %s, 'candidate_fact', %s, 'operator')
            """,
            (
                project_id,
                intake_id,
                source["snapshot"],
                source["fact"],
            ),
            "ck_reii_target_shape",
        ),
        (
            """
            INSERT INTO research_evidence_intake_item
                (project_id, research_evidence_intake_id, source_snapshot_id,
                 item_kind, candidate_fact_revision_id, fact_metadata_revision_id,
                 claim_draft_id, created_by)
            VALUES (%s, %s, %s, 'candidate_fact', %s, %s, %s, 'operator')
            """,
            (
                project_id,
                intake_id,
                source["snapshot"],
                source["fact"],
                source["fact_metadata"],
                source["claim"],
            ),
            "ck_reii_target_shape",
        ),
    )
    for sql, params, constraint in invalid_statements:
        with pytest.raises(Exception) as exc:
            conn.execute(sql, params)
            conn.commit()
        conn.rollback()
        assert constraint in str(exc.value)


def test_cross_project_and_metadata_snapshot_mismatches_are_rejected(
    conn, schema_v53
):
    project_a = pg.insert_project(conn, name="project-a")
    project_b = pg.insert_project(conn, name="project-b")
    conn.commit()
    source_a = _seed_source(conn, project_a, tag="a")
    source_b = _seed_source(conn, project_b, tag="b")

    with pytest.raises(Exception):
        conn.execute(
            """
            INSERT INTO research_evidence_intake
                (project_id, source_snapshot_id, source_metadata_revision_id,
                 selection_reason, created_by)
            VALUES (%s, %s, %s, 'cross project', 'operator')
            """,
            (project_b, source_a["snapshot"], source_a["source_metadata"]),
        )
        conn.commit()
    conn.rollback()

    with pytest.raises(Exception) as exc:
        conn.execute(
            """
            INSERT INTO research_evidence_intake
                (project_id, source_snapshot_id, source_metadata_revision_id,
                 selection_reason, created_by)
            VALUES (%s, %s, %s, 'wrong metadata', 'operator')
            """,
            (project_a, source_a["snapshot"], source_b["source_metadata"]),
        )
        conn.commit()
    conn.rollback()
    assert "fk_rei_source_metadata_snapshot" in str(exc.value)


def test_item_snapshot_must_equal_intake_snapshot(conn, schema_v53):
    project_id = pg.insert_project(conn, name="intake-snapshot")
    conn.commit()
    source_a = _seed_source(conn, project_id, tag="same-project-a")
    source_b = _seed_source(conn, project_id, tag="same-project-b")
    intake_id = _insert_intake(conn, project_id, source_a)
    conn.commit()

    with pytest.raises(Exception) as exc:
        conn.execute(
            """
            INSERT INTO research_evidence_intake_item
                (project_id, research_evidence_intake_id, source_snapshot_id,
                 item_kind, claim_draft_id, created_by)
            VALUES (%s, %s, %s, 'claim_draft', %s, 'operator')
            """,
            (project_id, intake_id, source_b["snapshot"], source_b["claim"]),
        )
        conn.commit()
    conn.rollback()
    assert "fk_reii_intake_snapshot" in str(exc.value)


def test_fact_snapshot_mismatch_is_rejected_by_narrow_trigger(conn, schema_v53):
    project_id = pg.insert_project(conn, name="fact-snapshot")
    conn.commit()
    source_a = _seed_source(conn, project_id, tag="fact-a")
    source_b = _seed_source(conn, project_id, tag="fact-b")
    intake_id = _insert_intake(conn, project_id, source_a)
    conn.commit()

    with pytest.raises(Exception, match="does not belong to snapshot"):
        conn.execute(
            """
            INSERT INTO research_evidence_intake_item
                (project_id, research_evidence_intake_id, source_snapshot_id,
                 item_kind, candidate_fact_revision_id, fact_metadata_revision_id,
                 created_by)
            VALUES (%s, %s, %s, 'candidate_fact', %s, %s, 'operator')
            """,
            (
                project_id,
                intake_id,
                source_a["snapshot"],
                source_b["fact"],
                source_b["fact_metadata"],
            ),
        )
        conn.commit()
    conn.rollback()


def test_fact_metadata_must_belong_to_bound_fact(conn, schema_v53):
    project_id = pg.insert_project(conn, name="fact-metadata")
    conn.commit()
    source = _seed_source(conn, project_id, tag="fact-meta-a")
    other = _seed_source(conn, project_id, tag="fact-meta-b")
    intake_id = _insert_intake(conn, project_id, source)
    conn.commit()

    with pytest.raises(Exception) as exc:
        conn.execute(
            """
            INSERT INTO research_evidence_intake_item
                (project_id, research_evidence_intake_id, source_snapshot_id,
                 item_kind, candidate_fact_revision_id, fact_metadata_revision_id,
                 created_by)
            VALUES (%s, %s, %s, 'candidate_fact', %s, %s, 'operator')
            """,
            (
                project_id,
                intake_id,
                source["snapshot"],
                source["fact"],
                other["fact_metadata"],
            ),
        )
        conn.commit()
    conn.rollback()
    assert "fk_reii_fact_metadata_fact" in str(exc.value)


@pytest.mark.parametrize("kind", ["candidate_fact", "claim_draft"])
def test_duplicate_bindings_are_rejected(conn, schema_v53, kind):
    project_id = pg.insert_project(conn, name=f"duplicate-{kind}")
    conn.commit()
    source = _seed_source(conn, project_id, tag=kind)
    intake_id = _insert_intake(conn, project_id, source)
    if kind == "candidate_fact":
        _insert_fact_item(conn, project_id, intake_id, source)
        index_name = "uq_reii_intake_candidate_fact"
        insert = _insert_fact_item
    else:
        _insert_claim_item(conn, project_id, intake_id, source)
        index_name = "uq_reii_intake_claim_draft"
        insert = _insert_claim_item
    conn.commit()

    with pytest.raises(Exception) as exc:
        insert(conn, project_id, intake_id, source)
        conn.commit()
    conn.rollback()
    assert index_name in str(exc.value)


@pytest.mark.parametrize(
    ("table", "seed_item"),
    [
        ("research_evidence_intake", False),
        ("research_evidence_intake_item", True),
    ],
)
def test_v53_records_are_append_only(conn, schema_v53, table, seed_item):
    project_id = pg.insert_project(conn, name=f"append-{table}")
    conn.commit()
    source = _seed_source(conn, project_id, tag=table)
    intake_id = _insert_intake(conn, project_id, source)
    target_id = (
        _insert_claim_item(conn, project_id, intake_id, source)
        if seed_item
        else intake_id
    )
    conn.commit()

    with pytest.raises(Exception, match="append-only"):
        conn.execute(
            f"UPDATE {table} SET created_by = created_by WHERE id = %s",
            (target_id,),
        )
        conn.commit()
    conn.rollback()
    with pytest.raises(Exception, match="append-only"):
        conn.execute(f"DELETE FROM {table} WHERE id = %s", (target_id,))
        conn.commit()
    conn.rollback()


def test_v53_foreign_keys_restrict_project_deletion(conn, schema_v53):
    project_id = pg.insert_project(conn, name="restrict-project")
    conn.commit()
    source = _seed_source(conn, project_id, tag="restrict")
    _insert_intake(conn, project_id, source)
    conn.commit()

    with pytest.raises(Exception):
        conn.execute("DELETE FROM projects WHERE id = %s", (project_id,))
        conn.commit()
    conn.rollback()


@pytest.mark.parametrize("event_type", ["tombstone", "redact"])
def test_service_rejects_unavailable_snapshot_at_intake_creation(
    conn, schema_v53, event_type
):
    project_id = pg.insert_project(conn, name=f"unavailable-{event_type}")
    conn.commit()
    source = _seed_source(conn, project_id, tag=event_type)
    ev_repo.insert_retention_event(
        conn,
        project_id=project_id,
        event_type=event_type,
        source_snapshot_id=source["snapshot"],
        reason="operator retention action",
        created_by="operator",
    )
    conn.commit()

    with pytest.raises(intake_service.ResearchEvidenceSnapshotUnavailable):
        intake_service.create_intake(
            conn,
            ResearchEvidenceIntakeCreate(
                project_id=project_id,
                source_snapshot_id=source["snapshot"],
                source_metadata_revision_id=source["source_metadata"],
                selection_reason="Operator selected",
                created_by="operator",
            ),
        )
    conn.rollback()


def test_service_rechecks_availability_before_item_creation(conn, schema_v53):
    project_id = pg.insert_project(conn, name="unavailable-after-intake")
    conn.commit()
    source = _seed_source(conn, project_id, tag="after-intake")
    intake = intake_service.create_intake(
        conn,
        ResearchEvidenceIntakeCreate(
            project_id=project_id,
            source_snapshot_id=source["snapshot"],
            source_metadata_revision_id=source["source_metadata"],
            selection_reason="Operator selected",
            created_by="operator",
        ),
    )
    conn.commit()
    ev_repo.insert_retention_event(
        conn,
        project_id=project_id,
        event_type="tombstone",
        source_snapshot_id=source["snapshot"],
        reason="operator retention action",
        created_by="operator",
    )
    conn.commit()

    with pytest.raises(intake_service.ResearchEvidenceSnapshotUnavailable):
        intake_service.create_intake_item(
            conn,
            ResearchEvidenceIntakeItemCreate(
                project_id=project_id,
                research_evidence_intake_id=intake.id,
                item_kind="claim_draft",
                claim_draft_id=source["claim"],
                created_by="operator",
            ),
        )
    conn.rollback()


def test_failed_v53_apply_does_not_mutate_parent_history(conn):
    schema = f"v53_parent_history_{uuid.uuid4().hex[:12]}"
    prior = pg._begin_autocommit(conn)
    try:
        conn.execute(f'CREATE SCHEMA "{schema}"')
        conn.execute(f'SET search_path TO "{schema}"')
        pg._run_script(conn, pg.INIT_SQL)
        pg._run_script(conn, pg.OUTCOMES_SQL)
        pg._run_script(conn, pg.V47_SQL)
        pg._run_script(conn, pg.V51_RESEARCH_SQL)
        pg._run_script(conn, pg.V52_RESEARCH_SQL)
        project_id = pg.insert_project(conn, name="history")
        claim_id = conn.execute(
            """
            INSERT INTO research_claim_draft
                (project_id, claim_text, created_by)
            VALUES (%s, 'Immutable history', 'operator')
            RETURNING id::text
            """,
            (project_id,),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO research_evidence_event
                (project_id, entity_type, entity_id, event_type, actor)
            VALUES (%s, 'claim_draft', %s, 'created', 'operator')
            """,
            (project_id, claim_id),
        )
        before = conn.execute(
            "SELECT count(*), max(event_sequence) FROM research_evidence_event"
        ).fetchone()
        conn.execute(
            "CREATE TABLE research_evidence_intake (id uuid PRIMARY KEY)"
        )
        with pytest.raises(Exception):
            pg._run_script(conn, pg.V53_RESEARCH_INTAKE_SQL)
        conn.rollback()
        after = conn.execute(
            "SELECT count(*), max(event_sequence) FROM research_evidence_event"
        ).fetchone()
        assert after == before
    finally:
        with contextlib.suppress(Exception):
            conn.rollback()
        conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        pg._restore_autocommit(conn, prior)
