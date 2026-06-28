"""PostgreSQL-backed contract tests for R1.3 controlled item review."""
import concurrent.futures
import contextlib
import sys
import threading
import uuid
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests.evidence_snapshot_pg as pg  # noqa: E402
from knowledge.evidence_snapshot import repository as ev_repo  # noqa: E402
from knowledge.evidence_snapshot.validation import validate_fact  # noqa: E402
from research_evidence import review_repository as review_repo  # noqa: E402
from research_evidence import review_service  # noqa: E402
from research_evidence.review_models import (  # noqa: E402
    ResearchEvidenceIntakeItemReviewDecisionCreate,
)


TABLES = (
    "research_evidence_intake_item_review_decision",
    "research_evidence_item_review_sequence_allocator",
)
CONSTRAINTS = (
    "research_evidence_intake_item_review_decision_pkey",
    "uq_reird_id_project_item",
    "uq_reird_item_sequence",
    "uq_reird_item_request",
    "uq_reird_supersedes_once",
    "fk_reird_project",
    "fk_reird_item_project",
    "fk_reird_supersedes_same_item",
    "ck_reird_decision_type",
    "ck_reird_sequence_positive",
    "ck_reird_reason_nonblank",
    "ck_reird_decided_by_nonblank",
    "ck_reird_request_id_nonblank",
    "research_evidence_item_review_sequence_allocator_pkey",
    "fk_reirsa_project",
    "fk_reirsa_item_project",
    "ck_reirsa_last_sequence",
)
TRIGGERS = (
    ("trg_reird_prepare_insert", "research_evidence_intake_item_review_decision"),
    ("trg_reird_no_mutation", "research_evidence_intake_item_review_decision"),
)


class BarrierBeforeReviewInsert:
    def __init__(self, conn, barrier):
        self._conn = conn
        self._barrier = barrier
        self._waiting = True

    def execute(self, sql, params=None):
        if (
            self._waiting
            and "INSERT INTO research_evidence_intake_item_review_decision" in sql
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
def schema_v54(conn):
    with pg.fresh_schema(conn) as schema:
        pg.apply_v51_research(conn)
        pg.apply_v52_research(conn)
        pg.apply_v53_research_intake(conn)
        pg.apply_v54_research_review(conn)
        yield schema


@pytest.fixture(autouse=True)
def feature_enabled(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")


def _seed_source(conn, project_id: str, *, tag: str):
    blob_id = ev_repo.insert_or_get_blob(
        conn,
        project_id=project_id,
        content_hash=f"review-{tag}-{uuid.uuid4().hex}",
        byte_size=12,
    )
    snapshot_id = ev_repo.insert_snapshot(
        conn,
        source_blob_id=blob_id,
        project_id=project_id,
        storage_ref=f"/r1.3/{tag}/{uuid.uuid4().hex}",
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
    return {
        "blob": blob_id,
        "snapshot": snapshot_id,
        "source_metadata": source_metadata_id,
        "fact": fact_id,
        "fact_metadata": fact_metadata_id,
        "claim": claim_id,
    }


def _seed_item(conn, *, kind: str = "candidate_fact", tag: str = "item"):
    project_id = pg.insert_project(conn, name=f"review-{tag}")
    source = _seed_source(conn, project_id, tag=tag)
    intake_id = conn.execute(
        """
        INSERT INTO research_evidence_intake
            (project_id, source_snapshot_id, source_metadata_revision_id,
             selection_reason, created_by)
        VALUES (%s, %s, %s, 'Operator selected', 'operator')
        RETURNING id::text
        """,
        (project_id, source["snapshot"], source["source_metadata"]),
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
                source["snapshot"],
                source["fact"],
                source["fact_metadata"],
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
            (project_id, intake_id, source["snapshot"], source["claim"]),
        ).fetchone()[0]
    conn.commit()
    return {
        "project": project_id,
        "intake": intake_id,
        "item": item_id,
        **source,
    }


def _insert_decision(
    conn,
    seeded,
    *,
    decision_type="approved",
    request_id=None,
    predecessor=None,
):
    columns = [
        "project_id",
        "research_evidence_intake_item_id",
        "decision_type",
        "decision_reason",
        "decided_by",
        "request_id",
    ]
    values = [
        seeded["project"],
        seeded["item"],
        decision_type,
        f"Reason {decision_type}",
        "operator",
        request_id or f"request-{uuid.uuid4().hex}",
    ]
    placeholders = ["%s"] * len(values)
    if predecessor is not None:
        columns.append("supersedes_decision_id")
        values.append(predecessor)
        placeholders.append("%s")
    return conn.execute(
        f"""
        INSERT INTO research_evidence_intake_item_review_decision
            ({", ".join(columns)})
        VALUES ({", ".join(placeholders)})
        RETURNING id::text, decision_sequence, supersedes_decision_id::text
        """,
        tuple(values),
    ).fetchone()


def _command(
    seeded,
    *,
    decision_type="approved",
    request_id="request-1",
    decision_reason=None,
    decided_by="operator",
):
    return ResearchEvidenceIntakeItemReviewDecisionCreate(
        project_id=seeded["project"],
        research_evidence_intake_item_id=seeded["item"],
        decision_type=decision_type,
        decision_reason=decision_reason or f"Reason {decision_type}",
        decided_by=decided_by,
        request_id=request_id,
    )


def _set_trigger_mode(conn, *, table, trigger, mode):
    prior = pg._begin_autocommit(conn)
    try:
        conn.execute(f"ALTER TABLE {table} {mode} TRIGGER {trigger}")
    finally:
        pg._restore_autocommit(conn, prior)


def _trigger_mode(conn, *, table, trigger):
    return conn.execute(
        """
        SELECT t.tgenabled
        FROM pg_trigger t
        WHERE t.tgname = %s
          AND t.tgrelid = %s::regclass
          AND NOT t.tgisinternal
        """,
        (trigger, table),
    ).fetchone()[0]


def test_clean_apply_creates_exact_v54_contract(conn, schema_v54):
    for table in TABLES:
        assert pg.table_exists(conn, schema_v54, table), table
    for constraint in CONSTRAINTS:
        assert pg.constraint_exists(conn, schema_v54, constraint), constraint
    for trigger, table in TRIGGERS:
        assert pg.trigger_exists(conn, schema_v54, trigger, table), trigger
    assert pg.function_exists(
        conn, schema_v54, "research_evidence_prepare_item_review_insert"
    )


def test_complete_v54_reapply_is_safe(conn, schema_v54):
    seeded = _seed_item(conn, tag="reapply")
    _insert_decision(conn, seeded)
    conn.commit()
    pg.apply_v54_research_review(conn)
    assert conn.execute(
        "SELECT last_sequence "
        "FROM research_evidence_item_review_sequence_allocator "
        "WHERE project_id = %s AND research_evidence_intake_item_id = %s",
        (seeded["project"], seeded["item"]),
    ).fetchone()[0] == 1


def test_v54_rejects_missing_v53_dependency(conn):
    with pg.fresh_schema(conn):
        pg.apply_v51_research(conn)
        pg.apply_v52_research(conn)
        with pytest.raises(Exception, match="requires complete v47/v51/v52/v53"):
            pg.apply_v54_research_review(conn)


def test_v54_rejects_partial_state(conn, schema_v54):
    prior = pg._begin_autocommit(conn)
    conn.execute(
        "DROP TABLE research_evidence_intake_item_review_decision CASCADE"
    )
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception, match="partial/divergent"):
        pg.apply_v54_research_review(conn)


def test_v54_rejects_divergent_decision_check(conn, schema_v54):
    prior = pg._begin_autocommit(conn)
    conn.execute(
        "ALTER TABLE research_evidence_intake_item_review_decision "
        "DROP CONSTRAINT ck_reird_decision_type"
    )
    conn.execute(
        """
        ALTER TABLE research_evidence_intake_item_review_decision
        ADD CONSTRAINT ck_reird_decision_type
        CHECK (decision_type IN (
            'approved', 'rejected', 'needs_revision', 'withdrawn', 'released'
        ))
        """
    )
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception, match="divergent decision/nonblank checks"):
        pg.apply_v54_research_review(conn)


def test_v54_rejects_function_drift(conn, schema_v54):
    prior = pg._begin_autocommit(conn)
    definition = conn.execute(
        "SELECT pg_get_functiondef("
        "'research_evidence_prepare_item_review_insert()'::regprocedure)"
    ).fetchone()[0]
    for sentinel in (
        "NEW.decision_sequence IS NOT NULL",
        "FOR UPDATE",
        "malformed review decision chain",
        "NEW.recorded_at := clock_timestamp()",
    ):
        assert sentinel in definition
    drifted = definition.replace(
        "v_next := v_last + 1;",
        "v_next := v_last + 1;\n    PERFORM 1;",
    )
    assert drifted != definition
    conn.execute(drifted)
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception, match="divergent review insert function"):
        pg.apply_v54_research_review(conn)


def test_v54_rejects_append_only_function_drift_with_sentinel_retained(
    conn, schema_v54
):
    prior = pg._begin_autocommit(conn)
    definition = conn.execute(
        "SELECT pg_get_functiondef('slicea_reject_mutation()'::regprocedure)"
    ).fetchone()[0]
    assert "Slice A record is append-only" in definition
    drifted = definition.replace(
        "BEGIN",
        "BEGIN\n    NULL;",
        1,
    )
    assert drifted != definition
    conn.execute(drifted)
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception, match="append-only guard"):
        pg.apply_v54_research_review(conn)


@pytest.mark.parametrize(
    ("table", "trigger", "divergent_mode", "error"),
    [
        (
            "source_blob",
            "trg_source_blob_no_mutation",
            "DISABLE",
            "exact v47/v51/v53 append-only guards",
        ),
        (
            "source_blob",
            "trg_source_blob_no_mutation",
            "ENABLE REPLICA",
            "exact v47/v51/v53 append-only guards",
        ),
        (
            "research_evidence_intake_item_review_decision",
            "trg_reird_no_mutation",
            "DISABLE",
            "missing or divergent triggers",
        ),
        (
            "research_evidence_intake_item_review_decision",
            "trg_reird_no_mutation",
            "ENABLE REPLICA",
            "missing or divergent triggers",
        ),
    ],
)
def test_v54_reapply_rejects_noncanonical_append_only_trigger_mode_and_restores(
    conn, schema_v54, table, trigger, divergent_mode, error
):
    _set_trigger_mode(
        conn,
        table=table,
        trigger=trigger,
        mode=divergent_mode,
    )
    assert _trigger_mode(conn, table=table, trigger=trigger) in {"D", "R"}

    with pytest.raises(Exception, match=error):
        pg.apply_v54_research_review(conn)
    pg._restore_autocommit(conn, False)

    _set_trigger_mode(
        conn,
        table=table,
        trigger=trigger,
        mode="ENABLE",
    )
    assert _trigger_mode(conn, table=table, trigger=trigger) == "O"
    pg.apply_v54_research_review(conn)


def test_v54_rejects_same_name_divergent_prerequisite_fk(conn, schema_v54):
    prior = pg._begin_autocommit(conn)
    conn.execute(
        "ALTER TABLE research_evidence_intake_item "
        "DROP CONSTRAINT fk_reii_fact_project"
    )
    conn.execute(
        "ALTER TABLE research_evidence_intake_item "
        "ADD CONSTRAINT fk_reii_fact_project "
        "FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT"
    )
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception, match="complete v53 parent graph"):
        pg.apply_v54_research_review(conn)


def test_v54_rejects_review_id_default_drift(conn, schema_v54):
    prior = pg._begin_autocommit(conn)
    conn.execute(
        "ALTER TABLE research_evidence_intake_item_review_decision "
        "ALTER COLUMN id SET DEFAULT "
        "'00000000-0000-0000-0000-000000000000'::uuid"
    )
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception, match="divergent review-decision id default"):
        pg.apply_v54_research_review(conn)


def test_v54_rejects_allocator_drift(conn, schema_v54):
    seeded = _seed_item(conn, tag="allocator-drift")
    _insert_decision(conn, seeded)
    conn.commit()
    conn.execute(
        "UPDATE research_evidence_item_review_sequence_allocator "
        "SET last_sequence = 2 "
        "WHERE project_id = %s AND research_evidence_intake_item_id = %s",
        (seeded["project"], seeded["item"]),
    )
    conn.commit()
    with pytest.raises(Exception, match="allocator diverges"):
        pg.apply_v54_research_review(conn)


def test_v54_rejects_allocator_public_privileges(conn, schema_v54):
    prior = pg._begin_autocommit(conn)
    conn.execute(
        "GRANT SELECT ON research_evidence_item_review_sequence_allocator "
        "TO PUBLIC"
    )
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception, match="allocator has PUBLIC privileges"):
        pg.apply_v54_research_review(conn)


def test_project_scope_and_all_whitespace_constraints(conn, schema_v54):
    seeded = _seed_item(conn, tag="constraints")
    other_project = pg.insert_project(conn, name="other")
    conn.commit()
    with pytest.raises(Exception):
        conn.execute(
            """
            INSERT INTO research_evidence_intake_item_review_decision
                (project_id, research_evidence_intake_item_id, decision_type,
                 decision_reason, decided_by, request_id)
            VALUES (%s, %s, 'rejected', 'reason', 'operator', 'cross-project')
            """,
            (other_project, seeded["item"]),
        )
        conn.commit()
    conn.rollback()

    for column in ("decision_reason", "decided_by", "request_id"):
        values = {
            "decision_reason": "reason",
            "decided_by": "operator",
            "request_id": f"blank-{column}",
        }
        values[column] = "\t\n\r\f\v"
        with pytest.raises(Exception) as exc:
            conn.execute(
                """
                INSERT INTO research_evidence_intake_item_review_decision
                    (project_id, research_evidence_intake_item_id, decision_type,
                     decision_reason, decided_by, request_id)
                VALUES (%s, %s, 'rejected', %s, %s, %s)
                """,
                (
                    seeded["project"],
                    seeded["item"],
                    values["decision_reason"],
                    values["decided_by"],
                    values["request_id"],
                ),
            )
            conn.commit()
        conn.rollback()
        assert f"ck_reird_{'reason' if column == 'decision_reason' else column}_nonblank" in str(
            exc.value
        )


def test_server_assigns_linear_sequence_and_effective_order(conn, schema_v54):
    seeded = _seed_item(conn, tag="linear")
    first = _insert_decision(conn, seeded, decision_type="approved")
    second = _insert_decision(conn, seeded, decision_type="needs_revision")
    third = _insert_decision(conn, seeded, decision_type="approved")
    conn.commit()
    assert first[1:] == (1, None)
    assert second[1:] == (2, first[0])
    assert third[1:] == (3, second[0])
    effective = review_repo.get_effective_decision(
        conn,
        project_id=seeded["project"],
        research_evidence_intake_item_id=seeded["item"],
    )
    assert effective is not None
    assert effective.id == third[0]
    assert effective.decision_sequence == 3


def test_caller_owned_sequence_and_recorded_time_are_rejected(conn, schema_v54):
    seeded = _seed_item(conn, tag="server-owned")
    for column, value in (
        ("decision_sequence", 1),
        ("recorded_at", "2020-01-01T00:00:00Z"),
    ):
        with pytest.raises(Exception, match=f"{column} is server-assigned"):
            conn.execute(
                f"""
                INSERT INTO research_evidence_intake_item_review_decision
                    (project_id, research_evidence_intake_item_id, decision_type,
                     decision_reason, decided_by, request_id, {column})
                VALUES (%s, %s, 'rejected', 'reason', 'operator', %s, %s)
                """,
                (
                    seeded["project"],
                    seeded["item"],
                    f"server-owned-{column}",
                    value,
                ),
            )
            conn.commit()
        conn.rollback()


def test_stale_and_wrong_item_predecessors_are_rejected(conn, schema_v54):
    seeded = _seed_item(conn, tag="stale")
    first = _insert_decision(conn, seeded)
    second = _insert_decision(conn, seeded, decision_type="rejected")
    conn.commit()
    with pytest.raises(Exception, match="stale review predecessor"):
        _insert_decision(
            conn,
            seeded,
            decision_type="approved",
            predecessor=first[0],
        )
        conn.commit()
    conn.rollback()

    other = _seed_item(conn, tag="wrong-item")
    other_first = _insert_decision(conn, other, decision_type="rejected")
    conn.commit()
    with pytest.raises(Exception, match="stale review predecessor"):
        _insert_decision(
            conn,
            seeded,
            decision_type="approved",
            predecessor=other_first[0],
        )
        conn.commit()
    conn.rollback()
    assert second[1] == 2


def test_duplicate_request_is_rejected_and_service_retry_is_idempotent(
    conn, schema_v54
):
    seeded = _seed_item(conn, tag="request")
    first = review_service.record_item_review_decision(
        conn, _command(seeded, request_id="stable-request")
    )
    conn.commit()
    retry = review_service.record_item_review_decision(
        conn, _command(seeded, request_id="stable-request")
    )
    assert retry.id == first.id
    assert retry.decision_sequence == 1
    assert conn.execute(
        "SELECT count(*) "
        "FROM research_evidence_intake_item_review_decision "
        "WHERE project_id = %s AND research_evidence_intake_item_id = %s",
        (seeded["project"], seeded["item"]),
    ).fetchone()[0] == 1

    with pytest.raises(Exception) as exc:
        _insert_decision(
            conn,
            seeded,
            decision_type="rejected",
            request_id="stable-request",
        )
        conn.commit()
    conn.rollback()
    assert "uq_reird_item_request" in str(exc.value)


@pytest.mark.parametrize(
    "change",
    [
        {"decision_type": "rejected"},
        {"decision_reason": "Changed reason"},
        {"decided_by": "different-operator"},
    ],
)
def test_postgresql_service_retry_mismatch_does_not_append(
    conn, schema_v54, change
):
    seeded = _seed_item(conn, tag=f"retry-mismatch-{next(iter(change))}")
    request_id = "stable-mismatch-request"
    first = review_service.record_item_review_decision(
        conn, _command(seeded, request_id=request_id)
    )
    conn.commit()

    with pytest.raises(review_repo.ReviewRequestConflict):
        review_service.record_item_review_decision(
            conn,
            _command(seeded, request_id=request_id, **change),
        )

    rows = conn.execute(
        "SELECT id::text, decision_sequence "
        "FROM research_evidence_intake_item_review_decision "
        "WHERE project_id = %s AND research_evidence_intake_item_id = %s",
        (seeded["project"], seeded["item"]),
    ).fetchall()
    assert rows == [(first.id, 1)]


def test_duplicate_sequence_and_branch_constraints_reject_when_trigger_bypassed(
    conn, schema_v54
):
    seeded = _seed_item(conn, tag="constraint-defense")
    first = _insert_decision(conn, seeded)
    second = _insert_decision(conn, seeded, decision_type="rejected")
    conn.commit()

    conn.execute(
        "ALTER TABLE research_evidence_intake_item_review_decision "
        "DISABLE TRIGGER trg_reird_prepare_insert"
    )
    with pytest.raises(Exception) as exc:
        conn.execute(
            """
            INSERT INTO research_evidence_intake_item_review_decision
                (project_id, research_evidence_intake_item_id, decision_type,
                 decision_sequence, decision_reason, decided_by, request_id,
                 recorded_at)
            VALUES (%s, %s, 'rejected', 2, 'duplicate', 'operator',
                    'duplicate-sequence', NOW())
            """,
            (seeded["project"], seeded["item"]),
        )
        conn.commit()
    conn.rollback()
    assert "uq_reird_item_sequence" in str(exc.value)

    conn.execute(
        "ALTER TABLE research_evidence_intake_item_review_decision "
        "DISABLE TRIGGER trg_reird_prepare_insert"
    )
    with pytest.raises(Exception) as exc:
        conn.execute(
            """
            INSERT INTO research_evidence_intake_item_review_decision
                (project_id, research_evidence_intake_item_id, decision_type,
                 decision_sequence, supersedes_decision_id, decision_reason,
                 decided_by, request_id, recorded_at)
            VALUES (%s, %s, 'approved', 3, %s, 'branch', 'operator',
                    'branch', NOW())
            """,
            (seeded["project"], seeded["item"], first[0]),
        )
        conn.commit()
    conn.rollback()
    assert "uq_reird_supersedes_once" in str(exc.value)
    assert second[2] == first[0]


def test_composite_predecessor_fk_rejects_other_item_and_project_without_trigger(
    conn, schema_v54
):
    target = _seed_item(conn, tag="predecessor-fk-target")
    same_project_item = conn.execute(
        """
        INSERT INTO research_evidence_intake_item
            (project_id, research_evidence_intake_id, source_snapshot_id,
             item_kind, claim_draft_id, created_by)
        VALUES (%s, %s, %s, 'claim_draft', %s, 'operator')
        RETURNING id::text
        """,
        (
            target["project"],
            target["intake"],
            target["snapshot"],
            target["claim"],
        ),
    ).fetchone()[0]
    same_project = {**target, "item": same_project_item}
    same_project_predecessor = _insert_decision(conn, same_project)[0]
    cross_project = _seed_item(conn, tag="predecessor-fk-cross-project")
    cross_project_predecessor = _insert_decision(conn, cross_project)[0]
    conn.commit()

    with conn.transaction(force_rollback=True):
        conn.execute(
            "ALTER TABLE research_evidence_intake_item_review_decision "
            "DISABLE TRIGGER trg_reird_prepare_insert"
        )
        for request_id, predecessor in (
            ("same-project-different-item", same_project_predecessor),
            ("cross-project", cross_project_predecessor),
        ):
            with pytest.raises(Exception) as exc:
                with conn.transaction():
                    conn.execute(
                        """
                        INSERT INTO research_evidence_intake_item_review_decision
                            (project_id, research_evidence_intake_item_id,
                             decision_type, decision_sequence,
                             supersedes_decision_id, decision_reason,
                             decided_by, request_id, recorded_at)
                        VALUES (%s, %s, 'rejected', 1, %s, 'fk defense',
                                'operator', %s, NOW())
                        """,
                        (
                            target["project"],
                            target["item"],
                            predecessor,
                            request_id,
                        ),
                    )
            assert "fk_reird_supersedes_same_item" in str(exc.value)


def test_review_records_are_append_only_and_deletion_is_restrictive(conn, schema_v54):
    seeded = _seed_item(conn, tag="append-only")
    decision = _insert_decision(conn, seeded)
    conn.commit()
    with pytest.raises(Exception, match="append-only"):
        conn.execute(
            "UPDATE research_evidence_intake_item_review_decision "
            "SET decided_by = decided_by WHERE id = %s",
            (decision[0],),
        )
        conn.commit()
    conn.rollback()
    with pytest.raises(Exception, match="append-only"):
        conn.execute(
            "DELETE FROM research_evidence_intake_item_review_decision "
            "WHERE id = %s",
            (decision[0],),
        )
        conn.commit()
    conn.rollback()
    with pytest.raises(Exception):
        conn.execute(
            "DELETE FROM research_evidence_intake_item WHERE id = %s",
            (seeded["item"],),
        )
        conn.commit()
    conn.rollback()


@pytest.mark.parametrize("target", ["snapshot", "blob", "fact"])
@pytest.mark.parametrize("event_type", ["tombstone", "redact"])
def test_approved_is_blocked_by_exact_v47_unavailability(
    conn, schema_v54, target, event_type
):
    seeded = _seed_item(conn, tag=f"{target}-{event_type}")
    target_parameter = {
        "snapshot": "source_snapshot_id",
        "blob": "source_blob_id",
        "fact": "candidate_fact_revision_id",
    }[target]
    kwargs = {target_parameter: seeded[target]}
    ev_repo.insert_retention_event(
        conn,
        project_id=seeded["project"],
        event_type=event_type,
        reason="retention",
        created_by="operator",
        **kwargs,
    )
    conn.commit()
    with pytest.raises(review_service.ResearchEvidenceReviewUnavailable):
        review_service.record_item_review_decision(
            conn, _command(seeded, request_id=f"{target}-{event_type}")
        )
    conn.rollback()


def test_legal_hold_is_nonblocking_and_negative_outcomes_survive_unavailability(
    conn, schema_v54
):
    legal = _seed_item(conn, tag="legal-hold")
    ev_repo.insert_retention_event(
        conn,
        project_id=legal["project"],
        event_type="legal_hold",
        source_snapshot_id=legal["snapshot"],
        reason="hold",
        created_by="operator",
    )
    conn.commit()
    approved = review_service.record_item_review_decision(
        conn, _command(legal, request_id="legal-hold")
    )
    assert approved.decision_type == "approved"
    conn.commit()

    unavailable = _seed_item(conn, tag="negative-unavailable")
    ev_repo.insert_retention_event(
        conn,
        project_id=unavailable["project"],
        event_type="tombstone",
        source_snapshot_id=unavailable["snapshot"],
        reason="retention",
        created_by="operator",
    )
    conn.commit()
    for index, decision_type in enumerate(("rejected", "needs_revision")):
        record = review_service.record_item_review_decision(
            conn,
            _command(
                unavailable,
                decision_type=decision_type,
                request_id=f"negative-{index}",
            ),
        )
        assert record.decision_type == decision_type
    conn.commit()
    withdrawn = review_service.record_item_review_decision(
        conn,
        _command(
            unavailable,
            decision_type="withdrawn",
            request_id="negative-withdrawn",
        ),
    )
    assert withdrawn.decision_type == "withdrawn"


@pytest.mark.parametrize(
    ("kind", "lineage_action"),
    [
        ("candidate_fact", "source_metadata_superseded"),
        ("candidate_fact", "source_metadata_withdrawn"),
        ("candidate_fact", "fact_metadata_superseded"),
        ("candidate_fact", "fact_replaced"),
        ("claim_draft", "claim_superseded"),
        ("claim_draft", "claim_withdrawn"),
    ],
)
def test_approved_is_blocked_by_exact_v51_lineage(
    conn, schema_v54, kind, lineage_action
):
    seeded = _seed_item(conn, kind=kind, tag=lineage_action)
    if lineage_action == "source_metadata_superseded":
        conn.execute(
            """
            INSERT INTO research_source_metadata_revision
                (project_id, source_snapshot_id,
                 supersedes_metadata_revision_id, created_by)
            VALUES (%s, %s, %s, 'operator')
            """,
            (seeded["project"], seeded["snapshot"], seeded["source_metadata"]),
        )
    elif lineage_action == "source_metadata_withdrawn":
        _insert_event(
            conn, seeded, "source_metadata_revision", seeded["source_metadata"]
        )
    elif lineage_action == "fact_metadata_superseded":
        conn.execute(
            """
            INSERT INTO research_fact_metadata_revision
                (project_id, candidate_fact_revision_id,
                 supersedes_metadata_revision_id, created_by)
            VALUES (%s, %s, %s, 'operator')
            """,
            (seeded["project"], seeded["fact"], seeded["fact_metadata"]),
        )
    elif lineage_action == "fact_replaced":
        replacement = ev_repo.insert_fact(
            conn,
            project_id=seeded["project"],
            source_snapshot_id=seeded["snapshot"],
            fact=validate_fact("count", value=8, counted_entity="records"),
            created_by="operator",
        )
        conn.execute(
            """
            INSERT INTO research_fact_metadata_revision
                (project_id, candidate_fact_revision_id,
                 supersedes_candidate_fact_revision_id, created_by)
            VALUES (%s, %s, %s, 'operator')
            """,
            (seeded["project"], replacement, seeded["fact"]),
        )
    elif lineage_action == "claim_superseded":
        conn.execute(
            """
            INSERT INTO research_claim_draft
                (project_id, claim_text, supersedes_claim_id, created_by)
            VALUES (%s, 'Replacement claim', %s, 'operator')
            """,
            (seeded["project"], seeded["claim"]),
        )
    else:
        _insert_event(conn, seeded, "claim_draft", seeded["claim"])
    conn.commit()

    with pytest.raises(review_service.ResearchEvidenceReviewUnavailable):
        review_service.record_item_review_decision(
            conn, _command(seeded, request_id=lineage_action)
        )
    conn.rollback()


def _insert_event(conn, seeded, entity_type, entity_id):
    conn.execute(
        """
        INSERT INTO research_evidence_event
            (project_id, entity_type, entity_id, event_type, actor)
        VALUES (%s, %s, %s, 'withdrawn', 'operator')
        """,
        (seeded["project"], entity_type, entity_id),
    )


def test_two_connections_receive_contiguous_sequences(conn, schema_v54):
    seeded = _seed_item(conn, tag="concurrent")
    barrier = threading.Barrier(2)

    def append(request_id):
        worker = pg.connect(schema=schema_v54)
        try:
            synchronized = BarrierBeforeReviewInsert(worker, barrier)
            row = _insert_decision(
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
        "SELECT last_sequence "
        "FROM research_evidence_item_review_sequence_allocator "
        "WHERE project_id = %s AND research_evidence_intake_item_id = %s",
        (seeded["project"], seeded["item"]),
    ).fetchone()[0] == 2


def test_two_connection_retry_uses_one_immutable_decision(conn, schema_v54):
    seeded = _seed_item(conn, tag="concurrent-retry")
    barrier = threading.Barrier(2)

    def append():
        worker = pg.connect(schema=schema_v54)
        try:
            synchronized = BarrierBeforeReviewInsert(worker, barrier)
            record = review_service.record_item_review_decision(
                synchronized,
                _command(seeded, request_id="same-concurrent-request"),
            )
            worker.commit()
            return record.id, record.decision_sequence
        finally:
            worker.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda _: append(), range(2)))

    assert rows[0] == rows[1]
    assert rows[0][1] == 1
    assert conn.execute(
        "SELECT count(*) "
        "FROM research_evidence_intake_item_review_decision "
        "WHERE project_id = %s AND research_evidence_intake_item_id = %s",
        (seeded["project"], seeded["item"]),
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT last_sequence "
        "FROM research_evidence_item_review_sequence_allocator "
        "WHERE project_id = %s AND research_evidence_intake_item_id = %s",
        (seeded["project"], seeded["item"]),
    ).fetchone()[0] == 1


def test_failed_v54_apply_does_not_mutate_parent_history(conn):
    schema = f"v54_parent_history_{uuid.uuid4().hex[:12]}"
    prior = pg._begin_autocommit(conn)
    try:
        conn.execute(f'CREATE SCHEMA "{schema}"')
        conn.execute(f'SET search_path TO "{schema}"')
        for path in (
            pg.INIT_SQL,
            pg.OUTCOMES_SQL,
            pg.V47_SQL,
            pg.V51_RESEARCH_SQL,
            pg.V52_RESEARCH_SQL,
            pg.V53_RESEARCH_INTAKE_SQL,
        ):
            pg._run_script(conn, path)
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
            "CREATE TABLE research_evidence_intake_item_review_decision "
            "(id uuid PRIMARY KEY)"
        )
        with pytest.raises(Exception):
            pg._run_script(conn, pg.V54_RESEARCH_REVIEW_SQL)
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
