"""Disposable-PostgreSQL tests for v52 research-evidence audit integrity."""
from concurrent.futures import ThreadPoolExecutor
import sys
import threading
import uuid
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests.evidence_snapshot_pg as pg  # noqa: E402
from research_evidence import repository as repo, service  # noqa: E402
from research_evidence.models import ClaimDraftCreate  # noqa: E402


@pytest.fixture
def conn():
    pg.require_dsn()
    connection = pg.connect()
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def schema_v52(conn):
    with pg.fresh_schema(conn) as schema:
        pg.apply_v51_research(conn)
        pg.apply_v52_research(conn)
        yield schema


@pytest.fixture(autouse=True)
def sidecar_enabled(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")


def _insert_claim(conn, project_id: str, text: str = "Draft claim") -> str:
    return conn.execute(
        """
        INSERT INTO research_claim_draft (project_id, claim_text, created_by)
        VALUES (%s, %s, 'operator')
        RETURNING id::text
        """,
        (project_id, text),
    ).fetchone()[0]


def _insert_event(
    conn,
    *,
    project_id: str,
    entity_type: str,
    entity_id: str,
    event_type: str = "created",
    event_sequence=None,
) -> int:
    if event_sequence is None:
        row = conn.execute(
            """
            INSERT INTO research_evidence_event
                (project_id, entity_type, entity_id, event_type)
            VALUES (%s, %s, %s, %s)
            RETURNING event_sequence
            """,
            (project_id, entity_type, entity_id, event_type),
        ).fetchone()
    else:
        row = conn.execute(
            """
            INSERT INTO research_evidence_event
                (project_id, entity_type, entity_id, event_type, event_sequence)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING event_sequence
            """,
            (project_id, entity_type, entity_id, event_type, event_sequence),
        ).fetchone()
    return int(row[0])


def test_v52_requires_complete_v51(conn):
    with pg.fresh_schema(conn):
        with pytest.raises(Exception, match="requires complete v51"):
            pg.apply_v52_research(conn)


def test_v52_fresh_apply_and_complete_reapply(conn, schema_v52):
    assert pg.table_exists(
        conn, schema_v52, "research_evidence_event_sequence_allocator"
    )
    assert pg.function_exists(
        conn, schema_v52, "research_evidence_prepare_event_insert"
    )
    assert pg.trigger_exists(
        conn, schema_v52, "trg_ree_prepare_insert", "research_evidence_event"
    )
    pg.apply_v52_research(conn)


def test_v52_rejects_partial_state(conn, schema_v52):
    prior = pg._begin_autocommit(conn)
    conn.execute("DROP TRIGGER trg_ree_prepare_insert ON research_evidence_event")
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception, match="partial/divergent"):
        pg.apply_v52_research(conn)


@pytest.mark.parametrize(
    ("constraint_name", "replacement", "message"),
    [
        (
            "fk_reesa_project",
            """
            ALTER TABLE research_evidence_event_sequence_allocator
            ADD CONSTRAINT fk_reesa_project
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
            """,
            "divergent allocator foreign key",
        ),
        (
            "ck_reesa_entity_type",
            """
            ALTER TABLE research_evidence_event_sequence_allocator
            ADD CONSTRAINT ck_reesa_entity_type CHECK (
                entity_type IN (
                    'source_metadata_revision',
                    'fact_metadata_revision',
                    'claim_draft',
                    'unexpected_type'
                )
            )
            """,
            "divergent allocator entity-type check",
        ),
        (
            "ck_reesa_last_sequence",
            """
            ALTER TABLE research_evidence_event_sequence_allocator
            ADD CONSTRAINT ck_reesa_last_sequence CHECK (last_sequence >= 0)
            """,
            "divergent allocator last-sequence check",
        ),
        (
            "research_evidence_event_sequence_allocator_pkey",
            """
            ALTER TABLE research_evidence_event_sequence_allocator
            ADD CONSTRAINT research_evidence_event_sequence_allocator_pkey
            PRIMARY KEY (project_id, entity_type)
            """,
            "divergent allocator primary key",
        ),
    ],
    ids=("foreign-key", "entity-type-check", "last-sequence-check", "primary-key"),
)
def test_v52_reapply_rejects_altered_allocator_constraint(
    conn, schema_v52, constraint_name, replacement, message
):
    conn.execute(
        f"""
        ALTER TABLE research_evidence_event_sequence_allocator
        DROP CONSTRAINT {constraint_name}
        """
    )
    conn.execute(replacement)
    conn.commit()

    with pytest.raises(Exception, match=message):
        pg.apply_v52_research(conn)


def test_allocator_initializes_from_existing_maximum(conn):
    with pg.fresh_schema(conn):
        pg.apply_v51_research(conn)
        project_id = pg.insert_project(conn, name="allocator-seed")
        claim_id = _insert_claim(conn, project_id)
        conn.execute(
            """
            INSERT INTO research_evidence_event
                (project_id, entity_type, entity_id, event_type, event_sequence)
            VALUES (%s, 'claim_draft', %s, 'created', 7)
            """,
            (project_id, claim_id),
        )
        conn.commit()

        pg.apply_v52_research(conn)
        assert _insert_event(
            conn,
            project_id=project_id,
            entity_type="claim_draft",
            entity_id=claim_id,
            event_type="withdrawn",
        ) == 8


@pytest.mark.parametrize("bad_history", ["orphan", "cross_project", "wrong_type"])
def test_v52_refuses_invalid_existing_history(conn, bad_history):
    with pg.fresh_schema(conn):
        pg.apply_v51_research(conn)
        project_a = pg.insert_project(conn, name="history-a")
        project_b = pg.insert_project(conn, name="history-b")
        claim_a = _insert_claim(conn, project_a)

        if bad_history == "orphan":
            conn.execute(
                """
                INSERT INTO research_evidence_event
                    (project_id, entity_type, entity_id, event_type, event_sequence)
                VALUES (%s, 'claim_draft', %s, 'created', 1)
                """,
                (project_a, str(uuid.uuid4())),
            )
        elif bad_history == "cross_project":
            conn.execute(
                """
                INSERT INTO research_evidence_event
                    (project_id, entity_type, entity_id, event_type, event_sequence)
                VALUES (%s, 'claim_draft', %s, 'created', 1)
                """,
                (project_b, claim_a),
            )
        else:
            conn.execute(
                "ALTER TABLE research_evidence_event DROP CONSTRAINT ck_ree_entity_type"
            )
            conn.execute(
                """
                INSERT INTO research_evidence_event
                    (project_id, entity_type, entity_id, event_type, event_sequence)
                VALUES (%s, 'invalid_type', %s, 'created', 1)
                """,
                (project_a, claim_a),
            )
            conn.execute(
                """
                ALTER TABLE research_evidence_event
                ADD CONSTRAINT ck_ree_entity_type CHECK (
                    entity_type IN (
                        'source_metadata_revision',
                        'fact_metadata_revision',
                        'claim_draft'
                    )
                ) NOT VALID
                """
            )
        conn.commit()

        message = "invalid entity types" if bad_history == "wrong_type" else "orphan or cross-project"
        with pytest.raises(Exception, match=message):
            pg.apply_v52_research(conn)


def test_direct_sql_target_integrity_and_trigger_owned_sequence(conn, schema_v52):
    project_a = pg.insert_project(conn, name="direct-a")
    project_b = pg.insert_project(conn, name="direct-b")
    claim_a = _insert_claim(conn, project_a)
    conn.commit()

    assert _insert_event(
        conn,
        project_id=project_a,
        entity_type="claim_draft",
        entity_id=claim_a,
    ) == 1
    assert _insert_event(
        conn,
        project_id=project_a,
        entity_type="claim_draft",
        entity_id=claim_a,
        event_type="withdrawn",
        event_sequence=999,
    ) == 2
    conn.commit()

    for project_id, entity_type, entity_id in (
        (project_a, "claim_draft", str(uuid.uuid4())),
        (project_b, "claim_draft", claim_a),
        (project_a, "wrong_type", claim_a),
    ):
        with pytest.raises(Exception):
            _insert_event(
                conn,
                project_id=project_id,
                entity_type=entity_type,
                entity_id=entity_id,
            )
        conn.rollback()


def test_concurrent_same_entity_inserts_are_consecutive(conn, schema_v52):
    project_id = pg.insert_project(conn, name="concurrent")
    claim_id = _insert_claim(conn, project_id)
    conn.commit()
    barrier = threading.Barrier(2)

    def insert_concurrently():
        worker = pg.connect(schema=schema_v52)
        try:
            barrier.wait(timeout=10)
            sequence = _insert_event(
                worker,
                project_id=project_id,
                entity_type="claim_draft",
                entity_id=claim_id,
                event_type="withdrawn",
            )
            worker.commit()
            return sequence
        finally:
            worker.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        sequences = sorted(executor.map(lambda _index: insert_concurrently(), range(2)))
    assert sequences == [1, 2]


def test_failed_event_does_not_consume_sequence(conn, schema_v52):
    project_id = pg.insert_project(conn, name="failed-event")
    claim_id = _insert_claim(conn, project_id)
    conn.commit()
    assert _insert_event(
        conn,
        project_id=project_id,
        entity_type="claim_draft",
        entity_id=claim_id,
    ) == 1
    conn.commit()

    with pytest.raises(Exception):
        _insert_event(
            conn,
            project_id=project_id,
            entity_type="claim_draft",
            entity_id=claim_id,
            event_type="not_allowed",
        )
    conn.rollback()

    assert _insert_event(
        conn,
        project_id=project_id,
        entity_type="claim_draft",
        entity_id=claim_id,
        event_type="withdrawn",
    ) == 2


def test_autocommit_service_write_is_rejected_before_insert(conn, schema_v52):
    project_id = pg.insert_project(conn, name="autocommit")
    conn.commit()
    conn.autocommit = True
    try:
        with pytest.raises(service.ResearchEvidenceTransactionError):
            service.create_claim_draft(
                conn,
                ClaimDraftCreate(project_id=project_id, claim_text="must not persist"),
            )
        assert conn.execute(
            "SELECT count(*) FROM research_claim_draft WHERE project_id = %s",
            (project_id,),
        ).fetchone()[0] == 0
    finally:
        conn.autocommit = False


def test_failed_event_insert_rolls_back_sidecar_row(conn, schema_v52, monkeypatch):
    project_id = pg.insert_project(conn, name="event-failure")
    conn.commit()

    def fail_event(*args, **kwargs):
        raise RuntimeError("injected event failure")

    monkeypatch.setattr(repo, "insert_event", fail_event)
    with pytest.raises(RuntimeError, match="injected event failure"):
        service.create_claim_draft(
            conn,
            ClaimDraftCreate(project_id=project_id, claim_text="must roll back"),
        )

    assert conn.execute(
        "SELECT count(*) FROM research_claim_draft WHERE project_id = %s",
        (project_id,),
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT count(*) FROM research_evidence_event WHERE project_id = %s",
        (project_id,),
    ).fetchone()[0] == 0


def test_failed_correction_rolls_back_new_row_and_events(
    conn, schema_v52, monkeypatch
):
    project_id = pg.insert_project(conn, name="correction-failure")
    first = service.create_claim_draft(
        conn,
        ClaimDraftCreate(
            project_id=project_id,
            claim_text="original",
            created_by="operator",
        ),
    )
    original_insert_event = repo.insert_event
    calls = 0

    def fail_second_event(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected correction event failure")
        return original_insert_event(*args, **kwargs)

    monkeypatch.setattr(repo, "insert_event", fail_second_event)
    with pytest.raises(RuntimeError, match="injected correction event failure"):
        service.create_claim_draft(
            conn,
            ClaimDraftCreate(
                project_id=project_id,
                claim_text="replacement",
                supersedes_claim_id=first.id,
                created_by="operator",
            ),
        )

    claims = repo.list_claim_drafts(conn, project_id=project_id)
    events = repo.list_events(
        conn,
        project_id=project_id,
        entity_type="claim_draft",
        entity_id=first.id,
    )
    assert [claim.id for claim in claims] == [first.id]
    assert [event.event_type for event in events] == ["created"]
