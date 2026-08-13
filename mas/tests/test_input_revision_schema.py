"""Genuine PostgreSQL W8.2 migration, atomicity, and concurrency proofs."""
from __future__ import annotations

import asyncio
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


def test_reapply_rejects_function_acl_drift_without_repair(conn, schema):
    conn.execute("GRANT EXECUTE ON FUNCTION input_revision_guard_mutation() TO PUBLIC")
    conn.commit()
    prior = pg._begin_autocommit(conn)
    with pytest.raises(Exception, match="v65 preflight function owner/ACL drift"):
        pg._run_script(conn, V65)
    conn.rollback()
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
