"""Genuine PostgreSQL W8.2 migration, atomicity, and concurrency proofs."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4
from unittest.mock import AsyncMock, patch

import asyncpg
import psycopg
import pytest

import api
import input_revisions
import state_coherence
import store
from clarifications import ClarificationAnswer, ClarificationStatus
from state import ProjectState
import tests.evidence_snapshot_pg as pg
from tests.test_workflow_runner import make_completed_state


V64 = Path(__file__).resolve().parents[1] / "sql" / "v64_decision_state_coherence_foundation.sql"
V65 = Path(__file__).resolve().parents[1] / "sql" / "v65_governed_input_revision_lifecycle.sql"


@pytest.fixture
def conn():
    connection = psycopg.connect(pg.require_dsn())
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def schema(conn):
    name = f"w82_revision_{uuid4().hex[:16]}"
    prior = pg._begin_autocommit(conn)
    try:
        conn.execute(f'CREATE SCHEMA "{name}"')
        conn.execute(f'SET search_path TO "{name}"')
        pg._run_script(conn, pg.INIT_SQL)
        pg._run_script(conn, pg.OUTCOMES_SQL)
        pg._run_script(conn, V64)
        pg._run_script(conn, V65)
        pg._restore_autocommit(conn, prior)
        conn.execute(f'SET search_path TO "{name}"')
        conn.execute(
            """
            CREATE TABLE state_snapshots (
                project_id UUID PRIMARY KEY,
                state_json JSONB NOT NULL,
                version INT NOT NULL DEFAULT 1,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
        conn.commit()
        yield name
    finally:
        conn.rollback()
        prior_cleanup = pg._begin_autocommit(conn)
        conn.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        pg._restore_autocommit(conn, prior_cleanup)


def test_clean_apply_exact_reapply_and_prior_v64_reapply(conn, schema):
    prior = pg._begin_autocommit(conn)
    pg._run_script(conn, V65)
    # v65 intentionally adds no constraint/trigger to a v64-owned relation, so
    # W8.1's exact migration remains safely reapplicable after W8.2.
    pg._run_script(conn, V64)
    pg._restore_autocommit(conn, prior)
    conn.execute(f'SET search_path TO "{schema}"')
    assert conn.execute("SELECT count(*) FROM input_revisions").fetchone()[0] == 0


def test_reapply_rejects_column_drift_without_repair(conn, schema):
    conn.execute("ALTER TABLE input_revisions ALTER COLUMN source_kind TYPE varchar(100)")
    conn.commit()
    prior = pg._begin_autocommit(conn)
    with pytest.raises(Exception, match="v65 preflight column drift"):
        pg._run_script(conn, V65)
    conn.rollback()
    pg._restore_autocommit(conn, prior)
    conn.execute(f'SET search_path TO "{schema}"')
    assert conn.execute(
        """
        SELECT data_type FROM information_schema.columns
        WHERE table_schema=%s AND table_name='input_revisions' AND column_name='source_kind'
        """,
        (schema,),
    ).fetchone()[0] == "character varying"


def test_reapply_rejects_trigger_drift_without_repair(conn, schema):
    conn.execute("ALTER TABLE input_revisions DISABLE TRIGGER trg_ir_lifecycle_guard")
    conn.commit()
    prior = pg._begin_autocommit(conn)
    with pytest.raises(Exception, match="v65 preflight trigger semantic drift"):
        pg._run_script(conn, V65)
    conn.rollback()
    pg._restore_autocommit(conn, prior)
    conn.execute(f'SET search_path TO "{schema}"')
    assert conn.execute(
        """
        SELECT tgenabled FROM pg_catalog.pg_trigger
        WHERE tgrelid='input_revisions'::regclass
          AND tgname='trg_ir_lifecycle_guard'
        """
    ).fetchone()[0] == "D"


def test_reapply_rejects_constraint_and_function_drift_without_repair(conn, schema):
    conn.execute("ALTER TABLE input_revisions DROP CONSTRAINT ck_ir_sha256")
    conn.execute(
        "ALTER TABLE input_revisions ADD CONSTRAINT ck_ir_sha256 CHECK (true)"
    )
    conn.commit()
    prior = pg._begin_autocommit(conn)
    with pytest.raises(Exception, match="v65 preflight constraint drift"):
        pg._run_script(conn, V65)
    conn.rollback()
    pg._restore_autocommit(conn, prior)
    conn.execute(f'SET search_path TO "{schema}"')

    conn.execute("ALTER TABLE input_revisions DROP CONSTRAINT ck_ir_sha256")
    conn.execute(
        """
        ALTER TABLE input_revisions ADD CONSTRAINT ck_ir_sha256
        CHECK (patch_sha256 ~ '^[0-9a-f]{64}$')
        """
    )
    conn.execute(
        """
        CREATE OR REPLACE FUNCTION input_revision_guard_mutation()
        RETURNS trigger LANGUAGE plpgsql SET search_path FROM CURRENT
        AS $$ BEGIN RETURN NEW; END; $$
        """
    )
    conn.commit()
    prior = pg._begin_autocommit(conn)
    with pytest.raises(Exception, match="v65 preflight function semantic drift"):
        pg._run_script(conn, V65)
    conn.rollback()
    pg._restore_autocommit(conn, prior)
    conn.execute(f'SET search_path TO "{schema}"')
    assert conn.execute(
        """
        SELECT prosrc FROM pg_catalog.pg_proc
        WHERE oid='input_revision_guard_mutation()'::regprocedure
        """
    ).fetchone()[0] == " BEGIN RETURN NEW; END; "


def test_reapply_rejects_function_acl_drift_without_repair(conn, schema):
    conn.execute("GRANT EXECUTE ON FUNCTION input_revision_guard_mutation() TO PUBLIC")
    conn.commit()
    prior = pg._begin_autocommit(conn)
    with pytest.raises(Exception, match="v65 preflight function owner/ACL drift"):
        pg._run_script(conn, V65)
    conn.rollback()
    pg._restore_autocommit(conn, prior)
    conn.execute(f'SET search_path TO "{schema}"')
    assert conn.execute(
        "SELECT has_function_privilege('public', 'input_revision_guard_mutation()', 'EXECUTE')"
    ).fetchone()[0]


def test_reapply_rejects_relation_acl_drift_without_repair(conn, schema):
    conn.execute("GRANT SELECT ON input_revisions TO PUBLIC")
    conn.commit()
    prior = pg._begin_autocommit(conn)
    with pytest.raises(Exception, match="v65 preflight relation owner/ACL drift"):
        pg._run_script(conn, V65)
    conn.rollback()
    pg._restore_autocommit(conn, prior)
    conn.execute(f'SET search_path TO "{schema}"')
    assert conn.execute(
        "SELECT has_table_privilege('public', 'input_revisions', 'SELECT')"
    ).fetchone()[0]


def test_reapply_rejects_relation_owner_drift_without_repair(conn, schema):
    original_owner = conn.execute("SELECT current_user").fetchone()[0]
    drift_owner = f"w82_drift_{uuid4().hex[:12]}"
    conn.execute(f'CREATE ROLE "{drift_owner}"')
    try:
        conn.execute(f'ALTER TABLE input_revisions OWNER TO "{drift_owner}"')
        conn.commit()
        prior = pg._begin_autocommit(conn)
        with pytest.raises(Exception, match="v65 preflight relation owner/ACL drift"):
            pg._run_script(conn, V65)
        conn.rollback()
        pg._restore_autocommit(conn, prior)
        conn.execute(f'SET search_path TO "{schema}"')
        assert conn.execute(
            """
            SELECT owner.rolname
            FROM pg_catalog.pg_class relation
            JOIN pg_catalog.pg_namespace namespace ON namespace.oid=relation.relnamespace
            JOIN pg_catalog.pg_roles owner ON owner.oid=relation.relowner
            WHERE namespace.nspname=%s AND relation.relname='input_revisions'
            """,
            (schema,),
        ).fetchone()[0] == drift_owner
    finally:
        conn.rollback()
        conn.execute(f'ALTER TABLE "{schema}".input_revisions OWNER TO "{original_owner}"')
        conn.execute(f'DROP ROLE IF EXISTS "{drift_owner}"')
        conn.commit()


def test_reapply_rejects_security_definer_without_repair(conn, schema):
    conn.execute("ALTER FUNCTION input_revision_guard_mutation() SECURITY DEFINER")
    conn.commit()
    prior = pg._begin_autocommit(conn)
    with pytest.raises(Exception, match="v65 preflight function semantic drift"):
        pg._run_script(conn, V65)
    conn.rollback()
    pg._restore_autocommit(conn, prior)
    conn.execute(f'SET search_path TO "{schema}"')
    assert conn.execute(
        """
        SELECT prosecdef FROM pg_catalog.pg_proc
        WHERE oid='input_revision_guard_mutation()'::regprocedure
        """
    ).fetchone()[0]


def test_reapply_rejects_function_search_path_drift_without_repair(conn, schema):
    conn.execute("ALTER FUNCTION input_revision_guard_mutation() SET search_path TO pg_catalog")
    conn.commit()
    prior = pg._begin_autocommit(conn)
    with pytest.raises(Exception, match="v65 preflight function semantic drift"):
        pg._run_script(conn, V65)
    conn.rollback()
    pg._restore_autocommit(conn, prior)
    conn.execute(f'SET search_path TO "{schema}"')
    assert conn.execute(
        """
        SELECT proconfig FROM pg_catalog.pg_proc
        WHERE oid='input_revision_guard_mutation()'::regprocedure
        """
    ).fetchone()[0] == ["search_path=pg_catalog"]


def test_reapply_rejects_function_owner_drift_without_repair(conn, schema):
    original_owner = conn.execute("SELECT current_user").fetchone()[0]
    drift_owner = f"w82_function_drift_{uuid4().hex[:12]}"
    conn.execute(f'CREATE ROLE "{drift_owner}"')
    try:
        conn.execute(
            f'ALTER FUNCTION input_revision_guard_mutation() OWNER TO "{drift_owner}"'
        )
        conn.commit()
        prior = pg._begin_autocommit(conn)
        with pytest.raises(Exception, match="v65 preflight function owner/ACL drift"):
            pg._run_script(conn, V65)
        conn.rollback()
        pg._restore_autocommit(conn, prior)
        conn.execute(f'SET search_path TO "{schema}"')
        assert conn.execute(
            """
            SELECT owner.rolname
            FROM pg_catalog.pg_proc function_info
            JOIN pg_catalog.pg_roles owner ON owner.oid=function_info.proowner
            WHERE function_info.oid='input_revision_guard_mutation()'::regprocedure
            """
        ).fetchone()[0] == drift_owner
    finally:
        conn.rollback()
        conn.execute(
            f'ALTER FUNCTION "{schema}".input_revision_guard_mutation() OWNER TO "{original_owner}"'
        )
        conn.execute(f'DROP ROLE IF EXISTS "{drift_owner}"')
        conn.commit()


@pytest.mark.parametrize(
    ("tamper_sql", "expected"),
    [
        (
            """
            DROP TRIGGER trg_ir_lifecycle_guard ON input_revisions;
            CREATE TRIGGER trg_ir_lifecycle_guard BEFORE DELETE ON input_revisions
            FOR EACH ROW EXECUTE FUNCTION input_revision_guard_mutation()
            """,
            {"table": "input_revisions", "tgtype": 11},
        ),
        (
            """
            DROP TRIGGER trg_ir_lifecycle_guard ON input_revisions;
            CREATE TRIGGER trg_ir_lifecycle_guard
            BEFORE UPDATE OF lifecycle_status OR DELETE ON input_revisions
            FOR EACH ROW EXECUTE FUNCTION input_revision_guard_mutation()
            """,
            {"table": "input_revisions", "tgattr_nonempty": True},
        ),
        (
            """
            DROP TRIGGER trg_ir_lifecycle_guard ON input_revisions;
            CREATE TRIGGER trg_ir_lifecycle_guard BEFORE UPDATE OR DELETE ON input_revisions
            FOR EACH ROW WHEN (OLD.lifecycle_status IS NOT NULL)
            EXECUTE FUNCTION input_revision_guard_mutation()
            """,
            {"table": "input_revisions", "predicate": True},
        ),
        (
            """
            DROP TRIGGER trg_ir_lifecycle_guard ON input_revisions;
            CREATE TRIGGER trg_ir_lifecycle_guard BEFORE UPDATE OR DELETE ON input_revisions
            FOR EACH ROW EXECUTE FUNCTION input_revision_guard_mutation('tampered')
            """,
            {"table": "input_revisions", "tgnargs": 1},
        ),
        (
            """
            DROP TRIGGER trg_ir_lifecycle_guard ON input_revisions;
            CREATE TRIGGER trg_ir_lifecycle_guard BEFORE UPDATE OR DELETE ON projects
            FOR EACH ROW EXECUTE FUNCTION input_revision_guard_mutation()
            """,
            {"table": "projects"},
        ),
    ],
    ids=["event", "update-of", "predicate", "arguments", "attachment"],
)
def test_reapply_rejects_trigger_contract_drift_without_repair(
    conn, schema, tamper_sql, expected
):
    conn.execute(tamper_sql)
    conn.commit()
    prior = pg._begin_autocommit(conn)
    with pytest.raises(Exception, match="v65 preflight trigger semantic drift"):
        pg._run_script(conn, V65)
    conn.rollback()
    pg._restore_autocommit(conn, prior)
    conn.execute(f'SET search_path TO "{schema}"')
    row = conn.execute(
        """
        SELECT relation.relname, trigger_info.tgtype, trigger_info.tgattr::text,
               trigger_info.tgqual IS NOT NULL, trigger_info.tgnargs
        FROM pg_catalog.pg_trigger trigger_info
        JOIN pg_catalog.pg_class relation ON relation.oid=trigger_info.tgrelid
        JOIN pg_catalog.pg_namespace namespace ON namespace.oid=relation.relnamespace
        WHERE namespace.nspname=%s AND trigger_info.tgname='trg_ir_lifecycle_guard'
        """,
        (schema,),
    ).fetchone()
    assert row[0] == expected["table"]
    if "tgtype" in expected:
        assert row[1] == expected["tgtype"]
    if expected.get("tgattr_nonempty"):
        assert row[2]
    if expected.get("predicate"):
        assert row[3]
    if "tgnargs" in expected:
        assert row[4] == expected["tgnargs"]


def test_reapply_rejects_same_name_wrong_index_without_repair(conn, schema):
    conn.execute("DROP INDEX idx_ir_scope_status_created")
    conn.execute(
        "CREATE INDEX idx_ir_scope_status_created ON input_revisions (decision_id, project_id)"
    )
    conn.commit()
    wrong_definition = conn.execute(
        "SELECT pg_get_indexdef('idx_ir_scope_status_created'::regclass)"
    ).fetchone()[0]
    prior = pg._begin_autocommit(conn)
    with pytest.raises(Exception, match="v65 preflight index semantic drift"):
        pg._run_script(conn, V65)
    conn.rollback()
    pg._restore_autocommit(conn, prior)
    conn.execute(f'SET search_path TO "{schema}"')
    assert conn.execute(
        "SELECT pg_get_indexdef('idx_ir_scope_status_created'::regclass)"
    ).fetchone()[0] == wrong_definition


def test_first_apply_rejects_partial_same_name_index_without_repair(conn):
    partial_schema = f"w82_partial_{uuid4().hex[:16]}"
    prior = pg._begin_autocommit(conn)
    try:
        conn.execute(f'CREATE SCHEMA "{partial_schema}"')
        conn.execute(f'SET search_path TO "{partial_schema}"')
        pg._run_script(conn, pg.INIT_SQL)
        pg._run_script(conn, pg.OUTCOMES_SQL)
        pg._run_script(conn, V64)
        conn.execute("CREATE INDEX idx_ir_expected_base ON projects (id)")
        with pytest.raises(Exception, match="v65 partial input-revision schema detected"):
            pg._run_script(conn, V65)
        conn.rollback()
        conn.execute(f'SET search_path TO "{partial_schema}"')
        assert conn.execute(
            "SELECT pg_get_indexdef('idx_ir_expected_base'::regclass)"
        ).fetchone()[0].endswith("USING btree (id)")
        assert conn.execute("SELECT to_regclass('input_revisions')").fetchone()[0] is None
    finally:
        conn.rollback()
        conn.execute(f'DROP SCHEMA IF EXISTS "{partial_schema}" CASCADE')
        pg._restore_autocommit(conn, prior)


async def _pool(schema: str):
    return await asyncpg.create_pool(
        pg.require_dsn(), min_size=1, max_size=10, server_settings={"search_path": schema}
    )


async def _seed_completed(pool):
    state = make_completed_state(str(uuid4()))
    await store.save(state)
    generation_id = await state_coherence.bootstrap_current_analysis(pool, state)
    state.analysis_generation_id = generation_id
    await store.save(state)
    return state, generation_id


def _run(coro):
    return asyncio.run(coro)


def test_propose_reject_apply_lineage_and_current_generation_behavior(conn, schema):
    async def exercise():
        pool = await _pool(schema)
        try:
            store._mem.clear()
            with patch("store._get_pool", new=AsyncMock(return_value=pool)):
                state, generation_id = await _seed_completed(pool)
                before = state.model_dump(mode="json")
                first = await input_revisions.propose_revision(
                    state.project_id,
                    {"brief": "Choose B"},
                    rationale="Approved framing update",
                    source_kind="operator_api",
                )
                second = await input_revisions.propose_revision(
                    state.project_id,
                    {"data": "Updated evidence"},
                    rationale="Competing update",
                    source_kind="operator_api",
                )
                rejected = await input_revisions.propose_revision(
                    state.project_id,
                    {"project_name": "Rejected rename"},
                    rationale="Rename proposal",
                    source_kind="operator_api",
                )
                await input_revisions.reject_revision(
                    state.project_id,
                    rejected.revision_id,
                    rejected_by="operator",
                    rejection_rationale="Keep existing name",
                )

                async with pool.acquire() as db:
                    proposed_state = await store.load_conn(db, state.project_id)
                assert proposed_state is not None
                assert proposed_state.brief == before["brief"]
                assert proposed_state.data == before["data"]
                assert first.expected_base_snapshot_id == second.expected_base_snapshot_id

                application = await input_revisions.apply_revision(
                    state.project_id,
                    first.revision_id,
                    applied_by="operator",
                    transform=api._apply_direct_input_revision,
                )
                with pytest.raises(input_revisions.StaleInputRevision):
                    await input_revisions.apply_revision(
                        state.project_id,
                        second.revision_id,
                        applied_by="operator",
                        transform=api._apply_direct_input_revision,
                    )

                async with pool.acquire() as db:
                    snapshot = await db.fetchrow(
                        """
                        SELECT predecessor_snapshot_id, change_cause_id
                        FROM decision_input_snapshots WHERE id=$1::uuid
                        """,
                        application.revision.resulting_snapshot_id,
                    )
                    current = await db.fetchval(
                        "SELECT generation_id FROM current_analysis_generations WHERE project_id=$1::uuid",
                        state.project_id,
                    )
                    generation_count = await db.fetchval(
                        "SELECT count(*) FROM analysis_generations WHERE project_id=$1::uuid",
                        state.project_id,
                    )
                    persisted = await store.load_conn(db, state.project_id)
                return (
                    state,
                    first,
                    rejected,
                    application,
                    snapshot,
                    current,
                    generation_count,
                    persisted,
                    generation_id,
                )
        finally:
            store._mem.clear()
            await pool.close()

    (
        state,
        first,
        rejected,
        application,
        snapshot,
        current,
        generation_count,
        persisted,
        generation_id,
    ) = _run(exercise())
    assert application.revision.status == input_revisions.APPLIED
    assert str(snapshot["predecessor_snapshot_id"]) == first.expected_base_snapshot_id
    assert snapshot["change_cause_id"] == first.revision_id
    assert str(current) == generation_id
    assert generation_count == 1
    assert persisted is not None and persisted.analysis_generation_id == ""
    assert rejected.expected_base_snapshot_id == first.expected_base_snapshot_id

    with pytest.raises(Exception, match="terminal input revision lifecycle is immutable"):
        conn.execute(
            "UPDATE input_revisions SET lifecycle_status='rejected' WHERE id=%s",
            (application.revision.revision_id,),
        )
    conn.rollback()
    with pytest.raises(Exception, match="decision input snapshots are immutable"):
        conn.execute(
            "UPDATE decision_input_snapshots SET change_cause_id='tampered' WHERE id=%s",
            (application.revision.resulting_snapshot_id,),
        )
    conn.rollback()


def test_stale_ordinary_save_cannot_undo_applied_revision(conn, schema):
    async def exercise():
        pool = await _pool(schema)
        try:
            store._mem.clear()
            with patch("store._get_pool", new=AsyncMock(return_value=pool)):
                state_a, generation_id = await _seed_completed(pool)
                stale_a = state_a.model_copy(deep=True)
                proposal = await input_revisions.propose_revision(
                    state_a.project_id,
                    {"brief": "Authoritative state B"},
                    rationale="Governed A to B transition",
                    source_kind="test",
                )
                application = await input_revisions.apply_revision(
                    state_a.project_id,
                    proposal.revision_id,
                    applied_by="operator",
                    transform=api._apply_direct_input_revision,
                )

                with pytest.raises(
                    store.DirectInputAuthorityError,
                    match="governed direct inputs may change only",
                ):
                    await store.save(stale_a)

                async with pool.acquire() as db:
                    persisted = await store.load_conn(db, state_a.project_id)
                    revision = await db.fetchrow(
                        """
                        SELECT lifecycle_status, expected_base_snapshot_id,
                               resulting_snapshot_id
                        FROM input_revisions WHERE id=$1::uuid
                        """,
                        proposal.revision_id,
                    )
                    snapshot = await db.fetchrow(
                        """
                        SELECT predecessor_snapshot_id, change_cause_id,
                               effective_input_json
                        FROM decision_input_snapshots WHERE id=$1::uuid
                        """,
                        application.revision.resulting_snapshot_id,
                    )
                    current = await db.fetchval(
                        """
                        SELECT generation_id FROM current_analysis_generations
                        WHERE project_id=$1::uuid
                        """,
                        state_a.project_id,
                    )
                    generation_count = await db.fetchval(
                        "SELECT count(*) FROM analysis_generations WHERE project_id=$1::uuid",
                        state_a.project_id,
                    )
                return (
                    persisted,
                    proposal,
                    application,
                    revision,
                    snapshot,
                    str(current),
                    generation_count,
                    generation_id,
                )
        finally:
            store._mem.clear()
            await pool.close()

    (
        persisted,
        proposal,
        application,
        revision,
        snapshot,
        current,
        generation_count,
        generation_id,
    ) = _run(exercise())
    assert persisted is not None and persisted.brief == "Authoritative state B"
    assert revision["lifecycle_status"] == input_revisions.APPLIED
    assert str(revision["expected_base_snapshot_id"]) == proposal.expected_base_snapshot_id
    assert str(revision["resulting_snapshot_id"]) == application.revision.resulting_snapshot_id
    assert str(snapshot["predecessor_snapshot_id"]) == proposal.expected_base_snapshot_id
    assert snapshot["change_cause_id"] == proposal.revision_id
    snapshot_payload = snapshot["effective_input_json"]
    if isinstance(snapshot_payload, str):
        snapshot_payload = json.loads(snapshot_payload)
    assert snapshot_payload == state_coherence.effective_input_payload(persisted)
    assert current == generation_id
    assert generation_count == 1


def test_ordinary_non_direct_authority_saves_remain_allowed(conn, schema):
    async def exercise():
        pool = await _pool(schema)
        try:
            store._mem.clear()
            with patch("store._get_pool", new=AsyncMock(return_value=pool)):
                state, _ = await _seed_completed(pool)
                direct = state_coherence.direct_input_projection(state)

                state.policy_audit_log.append({"event": "workflow-only-save"})
                await store.save(state)

                state.risk_classification = "limited_risk"
                state.risk_classification_rationale = "Operator risk authority"
                await store.save(state)

                state.clarification_answers.append(
                    ClarificationAnswer(
                        answer_id="answer-1",
                        question_id="question-1",
                        answer_text="The deadline is Q4.",
                        status=ClarificationStatus.ANSWERED,
                        answered_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
                    )
                )
                await store.save(state)

                state.analysis_input_attestations["audit"] = {
                    "knowledge": {
                        "status": "used",
                        "projection_fingerprint": "1" * 64,
                        "policy_fingerprint": "2" * 64,
                        "items": [
                            {
                                "item_id": "knowledge-item",
                                "source_id": "knowledge-source",
                                "projection_sha256": "3" * 64,
                            }
                        ],
                    }
                }
                await store.save(state)

                state.analysis_input_attestations["strategy"] = {
                    "research_evidence": {
                        "status": "used",
                        "usage_scope": "internal_analysis",
                        "projection_fingerprint": "4" * 64,
                        "policy_identifier": "research-policy",
                        "policy_version": "1",
                        "policy_fingerprint": "5" * 64,
                        "sources": [{"source_snapshot_id": "research-snapshot"}],
                    }
                }
                await store.save(state)

                async with pool.acquire() as db:
                    persisted = await store.load_conn(db, state.project_id)
                    version = await db.fetchval(
                        "SELECT version FROM state_snapshots WHERE project_id=$1::uuid",
                        state.project_id,
                    )
                return direct, persisted, version
        finally:
            store._mem.clear()
            await pool.close()

    direct, persisted, version = _run(exercise())
    assert persisted is not None
    assert state_coherence.direct_input_projection(persisted) == direct
    assert persisted.risk_classification == "limited_risk"
    assert persisted.clarification_answers[-1].answer_id == "answer-1"
    assert persisted.analysis_input_attestations["audit"]["knowledge"]["status"] == "used"
    assert (
        persisted.analysis_input_attestations["strategy"]["research_evidence"]["status"]
        == "used"
    )
    assert version == 7


def test_w8_1_to_w8_2_metadata_only_rebind_remains_allowed(conn, schema):
    async def exercise():
        pool = await _pool(schema)
        try:
            store._mem.clear()
            state = ProjectState(
                project_id=str(uuid4()),
                project_name="Legacy W8.1 project",
                brief="Choose the existing option",
            )
            legacy_snapshot_id = str(uuid4())
            legacy_payload = state_coherence.effective_input_payload(state)
            legacy_payload["contract_version"] = "effective-decision-input.v1"
            legacy_payload["question"].pop("project_name")
            legacy_payload.pop("operator_monitoring_input")
            state.effective_input_snapshot_id = legacy_snapshot_id
            async with pool.acquire() as db:
                async with db.transaction():
                    await db.execute(
                        "INSERT INTO projects (id,name,brief) VALUES ($1::uuid,$2,$3)",
                        state.project_id,
                        state.project_name,
                        state.brief,
                    )
                    await db.execute(
                        """
                        INSERT INTO decision_input_snapshots (
                            id,project_id,decision_id,effective_input_sha256,
                            effective_input_json,contract_version
                        ) VALUES ($1::uuid,$2::uuid,$3,$4,$5::jsonb,$6)
                        """,
                        legacy_snapshot_id,
                        state.project_id,
                        state_coherence.primary_decision_id(state),
                        "a" * 64,
                        json.dumps(legacy_payload),
                        "effective-decision-input.v1",
                    )
                    await db.execute(
                        """
                        INSERT INTO state_snapshots (project_id,state_json)
                        VALUES ($1::uuid,$2::jsonb)
                        """,
                        state.project_id,
                        json.dumps(state.model_dump(mode="json")),
                    )

            with patch("store._get_pool", new=AsyncMock(return_value=pool)):
                proposal = await input_revisions.propose_revision(
                    state.project_id,
                    {"brief": "Proposed after metadata rebind"},
                    rationale="Rebind compatibility proof",
                    source_kind="test",
                )
            async with pool.acquire() as db:
                persisted = await store.load_conn(db, state.project_id)
                snapshot = await db.fetchrow(
                    """
                    SELECT predecessor_snapshot_id, change_cause_id, contract_version
                    FROM decision_input_snapshots WHERE id=$1::uuid
                    """,
                    proposal.expected_base_snapshot_id,
                )
            return state, proposal, persisted, snapshot, legacy_snapshot_id
        finally:
            store._mem.clear()
            await pool.close()

    state, proposal, persisted, snapshot, legacy_snapshot_id = _run(exercise())
    assert persisted is not None
    assert state_coherence.direct_input_projection(persisted) == state_coherence.direct_input_projection(state)
    assert proposal.expected_base_snapshot_id == persisted.effective_input_snapshot_id
    assert str(snapshot["predecessor_snapshot_id"]) == legacy_snapshot_id
    assert snapshot["change_cause_id"] is None
    assert snapshot["contract_version"] == "effective-decision-input.v2"


def test_revision_change_cause_cannot_be_faked_cross_scoped_or_exceed_patch(conn, schema):
    async def exercise():
        pool = await _pool(schema)
        try:
            store._mem.clear()
            with patch("store._get_pool", new=AsyncMock(return_value=pool)):
                state, _ = await _seed_completed(pool)
                proposal = await input_revisions.propose_revision(
                    state.project_id,
                    {"brief": "Authorized brief only"},
                    rationale="Exact patch authority",
                    source_kind="test",
                )
                other_state, _ = await _seed_completed(pool)
                other_proposal = await input_revisions.propose_revision(
                    other_state.project_id,
                    {"brief": "Other-project authorized brief"},
                    rationale="Cross-scope rejection proof",
                    source_kind="test",
                )
                fake_candidate = state.model_copy(deep=True)
                fake_candidate.brief = "Unauthorized arbitrary cause"
                async with pool.acquire() as db:
                    with pytest.raises(
                        store.DirectInputAuthorityError,
                        match="change cause does not exist",
                    ):
                        async with db.transaction():
                            await store.save_conn(
                                db,
                                fake_candidate,
                                predecessor_snapshot_id=proposal.expected_base_snapshot_id,
                                revision_change=store.RevisionChange(
                                    revision_id=str(uuid4()), applied_by="operator"
                                ),
                            )

                cross_scope_candidate = state.model_copy(deep=True)
                cross_scope_candidate.brief = "Other-project authorized brief"
                async with pool.acquire() as db:
                    with pytest.raises(
                        store.DirectInputAuthorityError,
                        match="does not match the persisted decision base",
                    ):
                        async with db.transaction():
                            await store.save_conn(
                                db,
                                cross_scope_candidate,
                                predecessor_snapshot_id=proposal.expected_base_snapshot_id,
                                revision_change=store.RevisionChange(
                                    revision_id=other_proposal.revision_id,
                                    applied_by="operator",
                                ),
                            )

                exact_candidate = state.model_copy(deep=True)
                exact_candidate.brief = "Authorized brief only"
                async with pool.acquire() as db:
                    with pytest.raises(
                        store.DirectInputAuthorityError,
                        match="requires an explicit transaction",
                    ):
                        await store.save_conn(
                            db,
                            exact_candidate,
                            predecessor_snapshot_id=proposal.expected_base_snapshot_id,
                            revision_change=store.RevisionChange(
                                revision_id=proposal.revision_id,
                                applied_by="operator",
                            ),
                        )

                excessive_candidate = state.model_copy(deep=True)
                excessive_candidate.brief = "Authorized brief only"
                excessive_candidate.data = "Unapproved extra data change"
                async with pool.acquire() as db:
                    with pytest.raises(
                        store.DirectInputAuthorityError,
                        match="exact direct-input delta",
                    ):
                        async with db.transaction():
                            await store.save_conn(
                                db,
                                excessive_candidate,
                                predecessor_snapshot_id=proposal.expected_base_snapshot_id,
                                revision_change=store.RevisionChange(
                                    revision_id=proposal.revision_id,
                                    applied_by="operator",
                                ),
                            )

                async with pool.acquire() as db:
                    persisted = await store.load_conn(db, state.project_id)
                    status = await db.fetchval(
                        "SELECT lifecycle_status FROM input_revisions WHERE id=$1::uuid",
                        proposal.revision_id,
                    )
                return state, persisted, status
        finally:
            store._mem.clear()
            await pool.close()

    state, persisted, status = _run(exercise())
    assert persisted is not None
    assert state_coherence.direct_input_projection(persisted) == state_coherence.direct_input_projection(state)
    assert status == input_revisions.PROPOSED


@pytest.mark.parametrize("ordinary_kind", ["stale", "risk-only"])
def test_concurrent_ordinary_save_cannot_undo_revision_apply(conn, schema, ordinary_kind):
    async def exercise():
        pool = await _pool(schema)
        try:
            store._mem.clear()
            with patch("store._get_pool", new=AsyncMock(return_value=pool)):
                state, _ = await _seed_completed(pool)
                ordinary = state.model_copy(deep=True)
                if ordinary_kind == "risk-only":
                    ordinary.risk_classification = "limited_risk"
                    ordinary.risk_classification_rationale = "Concurrent authority update"
                proposal = await input_revisions.propose_revision(
                    state.project_id,
                    {"brief": f"Concurrent governed B ({ordinary_kind})"},
                    rationale="Race proof",
                    source_kind="test",
                )
                start = asyncio.Event()

                async def apply():
                    await start.wait()
                    result = await input_revisions.apply_revision(
                        state.project_id,
                        proposal.revision_id,
                        applied_by="operator",
                        transform=api._apply_direct_input_revision,
                    )
                    return result.revision.status

                async def ordinary_save():
                    await start.wait()
                    try:
                        await store.save(ordinary)
                        return "saved"
                    except store.DirectInputAuthorityError:
                        return "rejected"

                tasks = [asyncio.create_task(apply()), asyncio.create_task(ordinary_save())]
                start.set()
                outcomes = await asyncio.gather(*tasks)
                async with pool.acquire() as db:
                    persisted = await store.load_conn(db, state.project_id)
                    status = await db.fetchval(
                        "SELECT lifecycle_status FROM input_revisions WHERE id=$1::uuid",
                        proposal.revision_id,
                    )
                return proposal, persisted, status, outcomes
        finally:
            store._mem.clear()
            await pool.close()

    proposal, persisted, status, outcomes = _run(exercise())
    assert outcomes[0] == input_revisions.APPLIED
    assert outcomes[1] in {"saved", "rejected"}
    assert persisted is not None
    assert persisted.brief == f"Concurrent governed B ({ordinary_kind})"
    assert persisted.effective_input_snapshot_id != proposal.expected_base_snapshot_id
    assert status == input_revisions.APPLIED


def test_stale_save_waits_for_inflight_apply_then_fails_closed(conn, schema):
    async def exercise():
        pool = await _pool(schema)
        try:
            store._mem.clear()
            with patch("store._get_pool", new=AsyncMock(return_value=pool)):
                state, _ = await _seed_completed(pool)
                stale = state.model_copy(deep=True)
                proposal = await input_revisions.propose_revision(
                    state.project_id,
                    {"brief": "Inflight authoritative B"},
                    rationale="Ordered stale-writer race",
                    source_kind="test",
                )
                apply_reached_precommit = asyncio.Event()
                release_apply = asyncio.Event()

                async def pause_before_commit():
                    apply_reached_precommit.set()
                    await release_apply.wait()

                with patch(
                    "input_revisions._application_fault_point",
                    new=AsyncMock(side_effect=pause_before_commit),
                ):
                    apply_task = asyncio.create_task(
                        input_revisions.apply_revision(
                            state.project_id,
                            proposal.revision_id,
                            applied_by="operator",
                            transform=api._apply_direct_input_revision,
                        )
                    )
                    await apply_reached_precommit.wait()
                    stale_task = asyncio.create_task(store.save(stale))
                    await asyncio.sleep(0.05)
                    stale_was_waiting = not stale_task.done()
                    release_apply.set()
                    application = await apply_task
                    with pytest.raises(store.DirectInputAuthorityError):
                        await stale_task

                async with pool.acquire() as db:
                    persisted = await store.load_conn(db, state.project_id)
                    status = await db.fetchval(
                        "SELECT lifecycle_status FROM input_revisions WHERE id=$1::uuid",
                        proposal.revision_id,
                    )
                return stale_was_waiting, application, persisted, status
        finally:
            store._mem.clear()
            await pool.close()

    stale_was_waiting, application, persisted, status = _run(exercise())
    assert stale_was_waiting
    assert application.revision.status == input_revisions.APPLIED
    assert persisted is not None and persisted.brief == "Inflight authoritative B"
    assert status == input_revisions.APPLIED


def test_unchanged_direct_input_save_can_commit_before_apply(conn, schema):
    async def exercise():
        pool = await _pool(schema)
        try:
            store._mem.clear()
            with patch("store._get_pool", new=AsyncMock(return_value=pool)):
                state, _ = await _seed_completed(pool)
                proposal = await input_revisions.propose_revision(
                    state.project_id,
                    {"brief": "Governed B after workflow save"},
                    rationale="Ordered unchanged-input race",
                    source_kind="test",
                )
                async with pool.acquire() as ordinary_conn:
                    async with ordinary_conn.transaction():
                        ordinary = await store.load_conn(
                            ordinary_conn, state.project_id, for_update=True
                        )
                        assert ordinary is not None
                        ordinary.policy_audit_log.append(
                            {"event_type": "ordinary_workflow_save_won_race"}
                        )
                        await store.save_conn(ordinary_conn, ordinary)
                        apply_task = asyncio.create_task(
                            input_revisions.apply_revision(
                                state.project_id,
                                proposal.revision_id,
                                applied_by="operator",
                                transform=api._apply_direct_input_revision,
                            )
                        )
                        await asyncio.sleep(0.05)
                        apply_was_waiting = not apply_task.done()
                application = await apply_task
                async with pool.acquire() as db:
                    persisted = await store.load_conn(db, state.project_id)
                return apply_was_waiting, application, persisted
        finally:
            store._mem.clear()
            await pool.close()

    apply_was_waiting, application, persisted = _run(exercise())
    assert apply_was_waiting
    assert application.revision.status == input_revisions.APPLIED
    assert persisted is not None and persisted.brief == "Governed B after workflow save"
    assert any(
        event.get("event_type") == "ordinary_workflow_save_won_race"
        for event in persisted.policy_audit_log
    )


def test_cross_project_snapshot_references_fail_closed(conn, schema):
    project_a = uuid4()
    project_b = uuid4()
    conn.execute(
        "INSERT INTO projects (id,name,brief) VALUES (%s,'A','a'),(%s,'B','b')",
        (project_a, project_b),
    )
    snapshot = uuid4()
    conn.execute(
        """
        INSERT INTO decision_input_snapshots (
            id,project_id,decision_id,effective_input_sha256,effective_input_json,contract_version
        ) VALUES (%s,%s,'decision-a',%s,'{}'::jsonb,'test')
        """,
        (snapshot, project_a, uuid4().hex + uuid4().hex),
    )
    with pytest.raises(Exception, match="fk_ir_expected_base_same_scope"):
        conn.execute(
            """
            INSERT INTO input_revisions (
                id,project_id,decision_id,expected_base_snapshot_id,patch_json,
                patch_sha256,affected_field_paths,rationale,source_kind,proposed_by
            ) VALUES (%s,%s,'decision-a',%s,'{"brief":"x"}'::jsonb,%s,
                      ARRAY['input.brief'],'cross scope','test','operator')
            """,
            (uuid4(), project_b, snapshot, "a" * 64),
        )
    conn.rollback()


def test_concurrent_double_apply_and_retry_yield_exactly_one_application(conn, schema):
    async def exercise():
        pool = await _pool(schema)
        try:
            with patch("store._get_pool", new=AsyncMock(return_value=pool)):
                state, _ = await _seed_completed(pool)
                proposal = await input_revisions.propose_revision(
                    state.project_id,
                    {"brief": "Concurrent winner"},
                    rationale="Concurrency proof",
                    source_kind="test",
                )

                async def attempt():
                    try:
                        result = await input_revisions.apply_revision(
                            state.project_id,
                            proposal.revision_id,
                            applied_by="operator",
                            transform=api._apply_direct_input_revision,
                        )
                        return result.revision.status
                    except input_revisions.InputRevisionConflict:
                        return "conflict"

                outcomes = await asyncio.gather(attempt(), attempt())
                retry = await attempt()
                async with pool.acquire() as db:
                    applied_count = await db.fetchval(
                        """
                        SELECT count(*) FROM input_revisions
                        WHERE id=$1::uuid AND lifecycle_status='applied'
                        """,
                        proposal.revision_id,
                    )
                return outcomes, retry, applied_count
        finally:
            store._mem.clear()
            await pool.close()

    outcomes, retry, applied_count = _run(exercise())
    assert sorted(outcomes) == ["applied", "conflict"]
    assert retry == "conflict"
    assert applied_count == 1


def test_concurrent_different_proposals_same_base_allow_at_most_one(conn, schema):
    async def exercise():
        pool = await _pool(schema)
        try:
            with patch("store._get_pool", new=AsyncMock(return_value=pool)):
                state, _ = await _seed_completed(pool)
                proposals = [
                    await input_revisions.propose_revision(
                        state.project_id,
                        patch_json,
                        rationale=label,
                        source_kind="test",
                    )
                    for patch_json, label in (
                        ({"brief": "First concurrent change"}, "first"),
                        ({"data": "Second concurrent change"}, "second"),
                    )
                ]

                async def attempt(proposal):
                    try:
                        await input_revisions.apply_revision(
                            state.project_id,
                            proposal.revision_id,
                            applied_by="operator",
                            transform=api._apply_direct_input_revision,
                        )
                        return "applied"
                    except input_revisions.StaleInputRevision:
                        return "stale"

                outcomes = await asyncio.gather(*(attempt(item) for item in proposals))
                async with pool.acquire() as db:
                    applied_count = await db.fetchval(
                        """
                        SELECT count(*) FROM input_revisions
                        WHERE project_id=$1::uuid AND lifecycle_status='applied'
                        """,
                        state.project_id,
                    )
                return outcomes, applied_count
        finally:
            store._mem.clear()
            await pool.close()

    outcomes, applied_count = _run(exercise())
    assert sorted(outcomes) == ["applied", "stale"]
    assert applied_count == 1


def test_apply_vs_direct_patch_share_observed_exact_base(conn, schema):
    async def exercise():
        pool = await _pool(schema)
        try:
            with patch("store._get_pool", new=AsyncMock(return_value=pool)):
                state, _ = await _seed_completed(pool)
                proposal = await input_revisions.propose_revision(
                    state.project_id,
                    {"brief": "Explicit proposal"},
                    rationale="Explicit",
                    source_kind="test",
                )
                observed_base = proposal.expected_base_snapshot_id

                async def explicit():
                    try:
                        await input_revisions.apply_revision(
                            state.project_id,
                            proposal.revision_id,
                            applied_by="operator",
                            transform=api._apply_direct_input_revision,
                        )
                        return "applied"
                    except input_revisions.StaleInputRevision:
                        return "stale"

                async def direct():
                    try:
                        result = await input_revisions.create_and_apply_revision(
                            state.project_id,
                            {"data": "Direct patch"},
                            rationale="Compatibility patch",
                            source_kind="direct_patch_compatibility",
                            applied_by="operator",
                            transform=api._apply_direct_input_revision,
                            expected_base_snapshot_id=observed_base,
                        )
                        assert result is not None
                        return "applied"
                    except input_revisions.StaleInputRevision:
                        return "stale"

                outcomes = await asyncio.gather(explicit(), direct())
                async with pool.acquire() as db:
                    applied_count = await db.fetchval(
                        """
                        SELECT count(*) FROM input_revisions
                        WHERE project_id=$1::uuid AND lifecycle_status='applied'
                        """,
                        state.project_id,
                    )
                return outcomes, applied_count
        finally:
            store._mem.clear()
            await pool.close()

    outcomes, applied_count = _run(exercise())
    assert sorted(outcomes) == ["applied", "stale"]
    assert applied_count == 1


def test_existing_patch_is_one_durable_revision_and_exact_retry_is_noop(conn, schema):
    async def exercise():
        pool = await _pool(schema)
        try:
            with patch("store._get_pool", new=AsyncMock(return_value=pool)):
                state, generation_id = await _seed_completed(pool)
                first = await api.patch_project_input(
                    state.project_id,
                    api.PatchProjectInputRequest(brief="Durable compatibility patch"),
                )
                second = await api.patch_project_input(
                    state.project_id,
                    api.PatchProjectInputRequest(brief="Durable compatibility patch"),
                )
                async with pool.acquire() as db:
                    revisions = await db.fetch(
                        "SELECT lifecycle_status FROM input_revisions WHERE project_id=$1::uuid",
                        state.project_id,
                    )
                    current = await db.fetchval(
                        "SELECT generation_id FROM current_analysis_generations WHERE project_id=$1::uuid",
                        state.project_id,
                    )
                return first, second, revisions, current, generation_id
        finally:
            store._mem.clear()
            await pool.close()

    first, second, revisions, current, generation_id = _run(exercise())
    assert first["status"] == "updated"
    assert first["revision_id"]
    assert second["status"] == "unchanged"
    assert second["revision_id"] == ""
    assert [row["lifecycle_status"] for row in revisions] == [input_revisions.APPLIED]
    assert str(current) == generation_id


def test_injected_precommit_failure_rolls_back_state_snapshot_and_application(conn, schema):
    async def exercise():
        pool = await _pool(schema)
        try:
            with patch("store._get_pool", new=AsyncMock(return_value=pool)):
                state, _ = await _seed_completed(pool)
                proposal = await input_revisions.propose_revision(
                    state.project_id,
                    {"brief": "Must roll back"},
                    rationale="Fault proof",
                    source_kind="test",
                )
                async with pool.acquire() as db:
                    before_state = await db.fetchval(
                        "SELECT state_json FROM state_snapshots WHERE project_id=$1::uuid",
                        state.project_id,
                    )
                    before_snapshots = await db.fetchval(
                        "SELECT count(*) FROM decision_input_snapshots WHERE project_id=$1::uuid",
                        state.project_id,
                    )
                with patch(
                    "input_revisions._application_fault_point",
                    new=AsyncMock(side_effect=RuntimeError("injected before commit")),
                ):
                    with pytest.raises(RuntimeError, match="injected before commit"):
                        await input_revisions.apply_revision(
                            state.project_id,
                            proposal.revision_id,
                            applied_by="operator",
                            transform=api._apply_direct_input_revision,
                        )
                async with pool.acquire() as db:
                    after_state = await db.fetchval(
                        "SELECT state_json FROM state_snapshots WHERE project_id=$1::uuid",
                        state.project_id,
                    )
                    after_snapshots = await db.fetchval(
                        "SELECT count(*) FROM decision_input_snapshots WHERE project_id=$1::uuid",
                        state.project_id,
                    )
                    status = await db.fetchval(
                        "SELECT lifecycle_status FROM input_revisions WHERE id=$1::uuid",
                        proposal.revision_id,
                    )
                return before_state, after_state, before_snapshots, after_snapshots, status
        finally:
            store._mem.clear()
            await pool.close()

    before_state, after_state, before_snapshots, after_snapshots, status = _run(exercise())
    assert after_state == before_state
    assert after_snapshots == before_snapshots
    assert status == input_revisions.PROPOSED
