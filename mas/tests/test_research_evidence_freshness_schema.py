"""PostgreSQL contract tests for R1.4 item freshness/drift."""
import concurrent.futures
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests.evidence_snapshot_pg as pg  # noqa: E402
from knowledge.evidence_snapshot import repository as ev_repo  # noqa: E402
from knowledge.evidence_snapshot.validation import validate_fact  # noqa: E402
from research_evidence import freshness_service  # noqa: E402
from research_evidence.freshness_models import (  # noqa: E402
    ResearchEvidenceIntakeItemFreshnessAssessmentCreate,
)


TABLES = (
    "research_evidence_intake_item_freshness_assessment",
    "research_evidence_item_freshness_sequence_allocator",
)
TRIGGERS = (
    (
        "trg_reifa_prepare_insert",
        "research_evidence_intake_item_freshness_assessment",
    ),
    (
        "trg_reifa_no_mutation",
        "research_evidence_intake_item_freshness_assessment",
    ),
)
CONSTRAINTS = (
    "research_evidence_intake_item_freshness_assessment_pkey",
    "uq_reifa_id_project_item",
    "uq_reifa_item_sequence",
    "uq_reifa_item_request",
    "uq_reifa_supersedes_once",
    "fk_reifa_item_project",
    "fk_reifa_supersedes_same_item",
    "fk_reifa_snapshot_project",
    "fk_reifa_blob_project",
    "fk_reifa_fact_project",
    "fk_reifa_fact_metadata_fact",
    "fk_reifa_comparison_item_project",
    "ck_reifa_policy_parameters_object",
    "ck_reifa_policy_provenance",
    "ck_reifa_freshness_window",
    "ck_reifa_drift_status",
    "ck_reifa_comparison_shape",
    "research_evidence_item_freshness_sequence_allocator_pkey",
)
BASIS = datetime(2026, 1, 1, tzinfo=timezone.utc)


class BarrierBeforeFreshnessInsert:
    def __init__(self, conn, barrier):
        self._conn = conn
        self._barrier = barrier
        self._waiting = True

    def execute(self, sql, params=None):
        if (
            self._waiting
            and "INSERT INTO "
            "research_evidence_intake_item_freshness_assessment" in sql
        ):
            self._waiting = False
            self._barrier.wait()
        return self._conn.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._conn, name)


@pytest.fixture
def conn():
    pg.require_dsn()
    connection = pg.connect()
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def schema_v55(conn):
    with pg.fresh_schema(conn) as schema:
        pg.apply_v51_research(conn)
        pg.apply_v52_research(conn)
        pg.apply_v53_research_intake(conn)
        pg.apply_v54_research_review(conn)
        pg.apply_v55_research_freshness(conn)
        yield schema


@pytest.fixture(autouse=True)
def feature_enabled(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")


def _seed_item(
    conn,
    *,
    project_id=None,
    tag="item",
    content_hash=None,
    kind="candidate_fact",
):
    project_id = project_id or pg.insert_project(conn, name=f"freshness-{tag}")
    blob_id = ev_repo.insert_or_get_blob(
        conn,
        project_id=project_id,
        content_hash=content_hash or f"freshness-{tag}-{uuid.uuid4().hex}",
        byte_size=12,
    )
    snapshot_id = ev_repo.insert_snapshot(
        conn,
        source_blob_id=blob_id,
        project_id=project_id,
        storage_ref=f"/r1.4/{tag}/{uuid.uuid4().hex}",
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
    intake_id = conn.execute(
        """
        INSERT INTO research_evidence_intake
            (project_id, source_snapshot_id, source_metadata_revision_id,
             selection_reason, created_by)
        VALUES (%s, %s, %s, 'Operator selected', 'operator')
        RETURNING id::text
        """,
        (project_id, snapshot_id, source_metadata_id),
    ).fetchone()[0]
    if kind == "candidate_fact":
        item_id = conn.execute(
            """
            INSERT INTO research_evidence_intake_item
                (project_id, research_evidence_intake_id, source_snapshot_id,
                 item_kind, candidate_fact_revision_id,
                 fact_metadata_revision_id, created_by)
            VALUES (%s, %s, %s, 'candidate_fact', %s, %s, 'operator')
            RETURNING id::text
            """,
            (
                project_id,
                intake_id,
                snapshot_id,
                fact_id,
                fact_metadata_id,
            ),
        ).fetchone()[0]
    else:
        item_id = conn.execute(
            """
            INSERT INTO research_evidence_intake_item
                (project_id, research_evidence_intake_id, source_snapshot_id,
                 item_kind, claim_draft_id, created_by)
            VALUES (%s, %s, %s, 'claim_draft', %s, 'operator')
            RETURNING id::text
            """,
            (project_id, intake_id, snapshot_id, claim_id),
        ).fetchone()[0]
    conn.commit()
    return {
        "project": project_id,
        "item": item_id,
        "snapshot": snapshot_id,
        "blob": blob_id,
        "fact": fact_id,
        "fact_metadata": fact_metadata_id,
    }


def _insert_assessment(
    conn,
    seeded,
    *,
    request_id=None,
    comparison_item=None,
    drift_status="not_assessed",
    extra_columns=None,
):
    columns = [
        "project_id",
        "research_evidence_intake_item_id",
        "request_id",
        "policy_identifier",
        "policy_version",
        "policy_parameters_json",
        "policy_fingerprint",
        "evaluator_version",
        "basis_timestamp",
        "fresh_through",
        "comparison_research_evidence_intake_item_id",
        "drift_status",
        "drift_reason",
        "assessed_by",
    ]
    values = [
        seeded["project"],
        seeded["item"],
        request_id or f"request-{uuid.uuid4().hex}",
        "source-age",
        "1",
        '{"max_age_days":30}',
        "sha256:policy",
        "evaluator-1",
        BASIS,
        BASIS + timedelta(days=30),
        comparison_item,
        drift_status,
        f"Reason {drift_status}",
        "operator",
    ]
    if extra_columns:
        for column, value in extra_columns.items():
            columns.append(column)
            values.append(value)
    placeholders = ["%s"] * len(values)
    placeholders[5] = "%s::jsonb"
    return conn.execute(
        f"""
        INSERT INTO research_evidence_intake_item_freshness_assessment
            ({", ".join(columns)})
        VALUES ({", ".join(placeholders)})
        RETURNING id::text, assessment_sequence,
                  supersedes_assessment_id::text, source_snapshot_id::text,
                  source_blob_id::text, candidate_fact_revision_id::text,
                  fact_metadata_revision_id::text, linked_hash_algorithm,
                  linked_content_hash, comparison_source_snapshot_id::text,
                  comparison_content_hash, content_change_detected,
                  drift_status, assessed_at
        """,
        tuple(values),
    ).fetchone()


def _command(seeded, *, request_id):
    return ResearchEvidenceIntakeItemFreshnessAssessmentCreate(
        project_id=seeded["project"],
        research_evidence_intake_item_id=seeded["item"],
        request_id=request_id,
        policy_identifier="source-age",
        policy_version="1",
        policy_parameters_json={"max_age_days": 30},
        policy_fingerprint="sha256:policy",
        evaluator_version="evaluator-1",
        basis_timestamp=BASIS,
        fresh_through=BASIS + timedelta(days=30),
        drift_status="not_assessed",
        drift_reason="No comparison",
        assessed_by="operator",
    )


def test_clean_apply_creates_v55_contract(conn, schema_v55):
    for table in TABLES:
        assert pg.table_exists(conn, schema_v55, table), table
    for constraint in CONSTRAINTS:
        assert pg.constraint_exists(conn, schema_v55, constraint), constraint
    for trigger, table in TRIGGERS:
        assert pg.trigger_exists(conn, schema_v55, trigger, table), trigger
    assert pg.function_exists(
        conn,
        schema_v55,
        "research_evidence_prepare_freshness_assessment_insert",
    )


def test_complete_reapply_preserves_history(conn, schema_v55):
    seeded = _seed_item(conn, tag="reapply")
    _insert_assessment(conn, seeded)
    conn.commit()
    pg.apply_v55_research_freshness(conn)
    assert conn.execute(
        """
        SELECT count(*), max(assessment_sequence)
        FROM research_evidence_intake_item_freshness_assessment
        WHERE project_id = %s AND research_evidence_intake_item_id = %s
        """,
        (seeded["project"], seeded["item"]),
    ).fetchone() == (1, 1)


def test_v55_requires_complete_v54_sequence(conn):
    with pg.fresh_schema(conn):
        pg.apply_v51_research(conn)
        pg.apply_v52_research(conn)
        pg.apply_v53_research_intake(conn)
        with pytest.raises(Exception, match="requires complete v47-v54"):
            pg.apply_v55_research_freshness(conn)


def test_v55_rejects_prepare_function_drift(conn, schema_v55):
    prior = pg._begin_autocommit(conn)
    definition = conn.execute(
        "SELECT pg_get_functiondef("
        "'research_evidence_prepare_freshness_assessment_insert()'"
        "::regprocedure)"
    ).fetchone()[0]
    drifted = definition.replace(
        "v_next := v_last + 1;",
        "v_next := v_last + 1;\n    PERFORM 1;",
    )
    assert drifted != definition
    conn.execute(drifted)
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception, match="divergent freshness prepare function"):
        pg.apply_v55_research_freshness(conn)


def test_v55_rejects_freshness_check_drift(conn, schema_v55):
    prior = pg._begin_autocommit(conn)
    conn.execute(
        "ALTER TABLE research_evidence_intake_item_freshness_assessment "
        "DROP CONSTRAINT ck_reifa_freshness_window"
    )
    conn.execute(
        """
        ALTER TABLE research_evidence_intake_item_freshness_assessment
        ADD CONSTRAINT ck_reifa_freshness_window
        CHECK (fresh_through >= basis_timestamp - interval '1 day')
        """
    )
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception, match="divergent freshness check constraints"):
        pg.apply_v55_research_freshness(conn)


def test_v55_rejects_relevant_index_drift(conn, schema_v55):
    prior = pg._begin_autocommit(conn)
    conn.execute("DROP INDEX idx_reifa_comparison_item")
    conn.execute(
        """
        CREATE INDEX idx_reifa_comparison_item
        ON research_evidence_intake_item_freshness_assessment(
            comparison_research_evidence_intake_item_id, project_id
        )
        """
    )
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception, match="divergent freshness indexes"):
        pg.apply_v55_research_freshness(conn)


@pytest.mark.parametrize(
    ("grant_sql", "error"),
    [
        (
            "GRANT SELECT ON "
            "research_evidence_item_freshness_sequence_allocator TO PUBLIC",
            "allocator has PUBLIC privileges",
        ),
        (
            "GRANT EXECUTE ON FUNCTION "
            "research_evidence_prepare_freshness_assessment_insert() TO PUBLIC",
            "divergent freshness prepare function",
        ),
    ],
)
def test_v55_rejects_public_privilege_drift(
    conn, schema_v55, grant_sql, error
):
    prior = pg._begin_autocommit(conn)
    conn.execute(grant_sql)
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception, match=error):
        pg.apply_v55_research_freshness(conn)


def test_v55_rejects_allocator_sequence_drift(conn, schema_v55):
    seeded = _seed_item(conn, tag="allocator-drift")
    _insert_assessment(conn, seeded)
    conn.commit()
    conn.execute(
        """
        UPDATE research_evidence_item_freshness_sequence_allocator
        SET last_sequence = 2
        WHERE project_id = %s AND research_evidence_intake_item_id = %s
        """,
        (seeded["project"], seeded["item"]),
    )
    conn.commit()
    with pytest.raises(Exception, match="allocator diverges from history"):
        pg.apply_v55_research_freshness(conn)


def test_valid_v53_candidate_fact_context_is_accepted_and_sequenced(
    conn, schema_v55
):
    seeded = _seed_item(conn, tag="sequence")
    first = _insert_assessment(conn, seeded, request_id="one")
    second = _insert_assessment(conn, seeded, request_id="two")
    assert first[1] == 1
    assert first[2] is None
    assert first[3:7] == (
        seeded["snapshot"],
        seeded["blob"],
        seeded["fact"],
        seeded["fact_metadata"],
    )
    assert first[7] == "sha256"
    assert first[8]
    assert second[1] == 2
    assert second[2] == first[0]
    assert second[13] >= first[13]


def test_claim_items_are_not_applicable_at_database_and_service(conn, schema_v55):
    claim = _seed_item(conn, tag="claim", kind="claim_draft")
    with pytest.raises(Exception, match="not applicable"):
        _insert_assessment(conn, claim)
    conn.rollback()
    assert (
        freshness_service.item_freshness_status_as_of(
            conn,
            project_id=claim["project"],
            research_evidence_intake_item_id=claim["item"],
            as_of=BASIS,
        )
        == "not_applicable"
    )


def test_hash_change_is_evidence_not_material_drift(conn, schema_v55):
    target = _seed_item(conn, tag="target", content_hash="hash-a")
    comparison = _seed_item(
        conn,
        project_id=target["project"],
        tag="comparison",
        content_hash="hash-b",
    )
    row = _insert_assessment(
        conn,
        target,
        comparison_item=comparison["item"],
        drift_status="no_material_drift",
    )
    assert row[9] == comparison["snapshot"]
    assert row[10] == "hash-b"
    assert row[11] is True
    assert row[12] == "no_material_drift"


def test_same_hash_does_not_prevent_material_drift_judgment(conn, schema_v55):
    target = _seed_item(conn, tag="same-a", content_hash="same-hash")
    comparison = _seed_item(
        conn,
        project_id=target["project"],
        tag="same-b",
        content_hash="same-hash",
    )
    row = _insert_assessment(
        conn,
        target,
        comparison_item=comparison["item"],
        drift_status="material_drift",
    )
    assert row[11] is False
    assert row[12] == "material_drift"


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("assessment_sequence", 9),
        ("supersedes_assessment_id", "00000000-0000-0000-0000-000000000001"),
        ("source_snapshot_id", "00000000-0000-0000-0000-000000000001"),
        ("linked_content_hash", "forged"),
        ("content_change_detected", True),
        ("assessed_at", BASIS),
    ],
)
def test_server_owned_fields_are_rejected(
    conn, schema_v55, column, value
):
    seeded = _seed_item(conn, tag=f"owned-{column}")
    with pytest.raises(Exception, match="server-assigned"):
        _insert_assessment(
            conn, seeded, extra_columns={column: value}
        )
    conn.rollback()


def test_elapsed_time_status_is_read_only(conn, schema_v55):
    seeded = _seed_item(conn, tag="elapsed")
    _insert_assessment(conn, seeded)
    conn.commit()
    before = conn.execute(
        """
        SELECT id::text, assessment_sequence, fresh_through, assessed_at
        FROM research_evidence_intake_item_freshness_assessment
        WHERE project_id = %s AND research_evidence_intake_item_id = %s
        """,
        (seeded["project"], seeded["item"]),
    ).fetchone()
    assert (
        freshness_service.item_freshness_status_as_of(
            conn,
            project_id=seeded["project"],
            research_evidence_intake_item_id=seeded["item"],
            as_of=BASIS + timedelta(days=30),
        )
        == "fresh"
    )
    assert (
        freshness_service.item_freshness_status_as_of(
            conn,
            project_id=seeded["project"],
            research_evidence_intake_item_id=seeded["item"],
            as_of=BASIS + timedelta(days=31),
        )
        == "stale"
    )
    after = conn.execute(
        """
        SELECT id::text, assessment_sequence, fresh_through, assessed_at
        FROM research_evidence_intake_item_freshness_assessment
        WHERE project_id = %s AND research_evidence_intake_item_id = %s
        """,
        (seeded["project"], seeded["item"]),
    ).fetchone()
    assert after == before


def test_assessment_history_is_append_only(conn, schema_v55):
    seeded = _seed_item(conn, tag="append-only")
    row = _insert_assessment(conn, seeded)
    conn.commit()
    with pytest.raises(Exception, match="append-only"):
        conn.execute(
            """
            UPDATE research_evidence_intake_item_freshness_assessment
            SET drift_reason = 'changed'
            WHERE id = %s
            """,
            (row[0],),
        )
    conn.rollback()
    with pytest.raises(Exception, match="append-only"):
        conn.execute(
            "DELETE FROM research_evidence_intake_item_freshness_assessment "
            "WHERE id = %s",
            (row[0],),
        )
    conn.rollback()


def test_effective_freshness_does_not_consult_review_or_availability(
    conn, schema_v55
):
    seeded = _seed_item(conn, tag="separate")
    _insert_assessment(conn, seeded)
    conn.commit()
    assert (
        freshness_service.item_freshness_status_as_of(
            conn,
            project_id=seeded["project"],
            research_evidence_intake_item_id=seeded["item"],
            as_of=BASIS,
        )
        == "fresh"
    )
    assert conn.execute(
        """
        SELECT count(*)
        FROM research_evidence_intake_item_review_decision
        WHERE project_id = %s AND research_evidence_intake_item_id = %s
        """,
        (seeded["project"], seeded["item"]),
    ).fetchone()[0] == 0


def test_two_connections_receive_contiguous_assessment_sequences(
    conn, schema_v55
):
    seeded = _seed_item(conn, tag="concurrent")
    barrier = threading.Barrier(2)

    def append(request_id):
        worker = pg.connect(schema=schema_v55)
        try:
            synchronized = BarrierBeforeFreshnessInsert(worker, barrier)
            row = _insert_assessment(
                synchronized, seeded, request_id=request_id
            )
            worker.commit()
            return row
        finally:
            worker.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(append, ("concurrent-a", "concurrent-b")))

    ordered = sorted(rows, key=lambda row: row[1])
    assert [row[1] for row in ordered] == [1, 2]
    assert ordered[0][2] is None
    assert ordered[1][2] == ordered[0][0]
    assert conn.execute(
        """
        SELECT last_sequence
        FROM research_evidence_item_freshness_sequence_allocator
        WHERE project_id = %s AND research_evidence_intake_item_id = %s
        """,
        (seeded["project"], seeded["item"]),
    ).fetchone()[0] == 2


def test_two_connection_retry_uses_one_immutable_assessment(conn, schema_v55):
    seeded = _seed_item(conn, tag="concurrent-retry")
    barrier = threading.Barrier(2)

    def append():
        worker = pg.connect(schema=schema_v55)
        try:
            synchronized = BarrierBeforeFreshnessInsert(worker, barrier)
            record = freshness_service.record_item_freshness_assessment(
                synchronized,
                _command(seeded, request_id="same-concurrent-request"),
            )
            worker.commit()
            return record.id, record.assessment_sequence
        finally:
            worker.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda _: append(), range(2)))

    assert rows[0] == rows[1]
    assert rows[0][1] == 1
    assert conn.execute(
        """
        SELECT count(*)
        FROM research_evidence_intake_item_freshness_assessment
        WHERE project_id = %s AND research_evidence_intake_item_id = %s
        """,
        (seeded["project"], seeded["item"]),
    ).fetchone()[0] == 1
    assert conn.execute(
        """
        SELECT last_sequence
        FROM research_evidence_item_freshness_sequence_allocator
        WHERE project_id = %s AND research_evidence_intake_item_id = %s
        """,
        (seeded["project"], seeded["item"]),
    ).fetchone()[0] == 1
