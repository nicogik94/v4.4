"""PostgreSQL contract tests for W8.1 state coherence.

These use the repository's disposable PostgreSQL test DSN and skip truthfully
when it is unavailable.  No SQLite substitute is used for concurrency claims.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import asyncio
import json
from pathlib import Path
from uuid import uuid4
from unittest.mock import AsyncMock, patch

import pytest
import psycopg
import asyncpg

import tests.evidence_snapshot_pg as pg
import state_coherence
import store
from state import ProjectState
from tests.test_workflow_runner import make_completed_state


V64 = Path(__file__).resolve().parents[1] / "sql" / "v64_decision_state_coherence_foundation.sql"
SHA = "a" * 64


@pytest.fixture
def conn():
    connection = psycopg.connect(pg.require_dsn())
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def schema(conn):
    name = f"w8_state_{uuid4().hex[:16]}"
    prior = pg._begin_autocommit(conn)
    try:
        conn.execute(f'CREATE SCHEMA "{name}"')
        conn.execute(f'SET search_path TO "{name}"')
        pg._run_script(conn, pg.INIT_SQL)
        pg._run_script(conn, pg.OUTCOMES_SQL)
        pg._run_script(conn, V64)
        pg._restore_autocommit(conn, prior)
        conn.execute(f'SET search_path TO "{name}"')
        conn.commit()
        yield name
    finally:
        conn.rollback()
        prior_cleanup = pg._begin_autocommit(conn)
        conn.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        pg._restore_autocommit(conn, prior_cleanup)


def _project(conn, name="project"):
    project_id = uuid4()
    conn.execute(
        "INSERT INTO projects (id, name, brief) VALUES (%s, %s, 'brief')",
        (project_id, name),
    )
    return project_id


def _snapshot(conn, project_id, decision_id="decision-primary"):
    snapshot_id = uuid4()
    digest = uuid4().hex + uuid4().hex
    conn.execute(
        """
        INSERT INTO decision_input_snapshots (
            id, project_id, decision_id, effective_input_sha256,
            effective_input_json, contract_version
        ) VALUES (%s, %s, %s, %s, '{}'::jsonb, 'effective-decision-input.v1')
        """,
        (snapshot_id, project_id, decision_id, digest),
    )
    return snapshot_id


def _candidate(conn, project_id, snapshot_id, *, decision_id="decision-primary", expected=None):
    generation_id = uuid4()
    conn.execute(
        """
        INSERT INTO analysis_generations (
            id, project_id, decision_id, effective_input_snapshot_id,
            workflow_fingerprint, lifecycle_status,
            expected_base_generation_id, analysis_state_sha256,
            analysis_state_json
        ) VALUES (%s, %s, %s, %s, 'workflow-v1', 'candidate', %s, %s, '{}'::jsonb)
        """,
        (generation_id, project_id, decision_id, snapshot_id, expected, SHA),
    )
    return generation_id


def _validate(conn, generation_id):
    conn.execute(
        "UPDATE analysis_generations SET validated_at = NOW() WHERE id = %s",
        (generation_id,),
    )


def _promote(conn, generation_id, expected=None):
    conn.execute("SELECT promote_analysis_generation(%s, %s)", (generation_id, expected))


def _bootstrap(conn, project_id, snapshot_id, generation_id):
    return conn.execute(
        """
        SELECT bootstrap_analysis_generation(
            %s, %s, 'decision-primary', %s, '{}'::jsonb,
            'effective-decision-input.v1', %s, %s, '{}'::jsonb
        )
        """,
        (snapshot_id, project_id, SHA, generation_id, SHA),
    ).fetchone()[0]


def test_clean_apply_and_exact_reapply(conn, schema):
    assert conn.execute(
        """
        SELECT count(*) FROM pg_class relation
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = %s
          AND relation.relname IN (
              'decision_input_snapshots', 'analysis_generations',
              'current_analysis_generations')
        """,
        (schema,),
    ).fetchone()[0] == 3
    prior = pg._begin_autocommit(conn)
    pg._run_script(conn, V64)
    pg._restore_autocommit(conn, prior)
    conn.execute(f'SET search_path TO "{schema}"')
    conn.commit()
    assert conn.execute(
        "SELECT count(*) FROM pg_trigger WHERE tgname IN ('trg_dis_immutable','trg_ag_immutable','trg_cag_guard')"
    ).fetchone()[0] == 3


def test_catalog_drift_fails_closed_on_reapply(conn, schema):
    prior = pg._begin_autocommit(conn)
    conn.execute("ALTER TABLE analysis_generations DROP CONSTRAINT ck_ag_state_sha256")
    with pytest.raises(Exception, match="catalog drift"):
        pg._run_script(conn, V64)
    conn.rollback()
    pg._restore_autocommit(conn, prior)


def test_same_count_column_and_constraint_drift_fail_closed(conn, schema):
    prior = pg._begin_autocommit(conn)
    conn.execute(
        "ALTER TABLE analysis_generations ALTER COLUMN workflow_fingerprint TYPE varchar(100)"
    )
    with pytest.raises(Exception, match="postflight column drift"):
        pg._run_script(conn, V64)
    conn.rollback()
    conn.execute(
        "ALTER TABLE analysis_generations ALTER COLUMN workflow_fingerprint TYPE text"
    )

    conn.execute("ALTER TABLE analysis_generations DROP CONSTRAINT ck_ag_state_sha256")
    conn.execute(
        "ALTER TABLE analysis_generations ADD CONSTRAINT ck_ag_state_sha256 CHECK (true)"
    )
    with pytest.raises(Exception, match="postflight constraint drift"):
        pg._run_script(conn, V64)
    conn.rollback()
    pg._restore_autocommit(conn, prior)


def test_owner_acl_drift_fails_closed_and_function_acl_is_owner_only(conn, schema):
    assert conn.execute(
        "SELECT has_function_privilege('public', 'promote_analysis_generation(uuid,uuid)', 'EXECUTE')"
    ).fetchone()[0] is False
    prior = pg._begin_autocommit(conn)
    conn.execute("GRANT SELECT ON analysis_generations TO PUBLIC")
    with pytest.raises(Exception, match="owner/ACL drift"):
        pg._run_script(conn, V64)
    conn.rollback()
    pg._restore_autocommit(conn, prior)


def test_explicit_function_grant_and_extra_trigger_fail_closed(conn, schema):
    prior = pg._begin_autocommit(conn)
    conn.execute("CREATE ROLE w8_extra_executor")
    try:
        conn.execute(
            "GRANT EXECUTE ON FUNCTION promote_analysis_generation(uuid,uuid) TO w8_extra_executor"
        )
        with pytest.raises(Exception, match="function owner/ACL drift"):
            pg._run_script(conn, V64)
        conn.rollback()
        conn.execute(
            "REVOKE EXECUTE ON FUNCTION promote_analysis_generation(uuid,uuid) FROM w8_extra_executor"
        )

        conn.execute(
            "ALTER FUNCTION promote_analysis_generation(uuid,uuid) OWNER TO w8_extra_executor"
        )
        with pytest.raises(Exception):
            pg._run_script(conn, V64)
        conn.rollback()
        conn.execute(
            "ALTER FUNCTION promote_analysis_generation(uuid,uuid) OWNER TO CURRENT_USER"
        )

        conn.execute(
            "CREATE TRIGGER trg_w8_extra BEFORE UPDATE ON analysis_generations "
            "FOR EACH ROW EXECUTE FUNCTION decision_state_guard_generation_mutation()"
        )
        with pytest.raises(Exception, match="postflight trigger drift"):
            pg._run_script(conn, V64)
        conn.rollback()
    finally:
        conn.execute("DROP OWNED BY w8_extra_executor")
        conn.execute("DROP ROLE IF EXISTS w8_extra_executor")
        pg._restore_autocommit(conn, prior)


def test_snapshot_and_generation_content_are_immutable(conn, schema):
    project = _project(conn)
    snapshot = _snapshot(conn, project)
    generation = _candidate(conn, project, snapshot)
    conn.commit()

    with pytest.raises(Exception, match="snapshots are immutable"):
        conn.execute(
            "UPDATE decision_input_snapshots SET effective_input_json = '{\"changed\":true}' WHERE id = %s",
            (snapshot,),
        )
    conn.rollback()
    with pytest.raises(Exception, match="identity and content are immutable"):
        conn.execute(
            "UPDATE analysis_generations SET analysis_state_json = '{\"changed\":true}' WHERE id = %s",
            (generation,),
        )
    conn.rollback()


def test_cross_project_snapshot_and_expected_base_references_are_rejected(conn, schema):
    first = _project(conn, "first")
    second = _project(conn, "second")
    first_snapshot = _snapshot(conn, first)
    second_snapshot = _snapshot(conn, second)
    first_generation = _candidate(conn, first, first_snapshot)
    conn.commit()

    with pytest.raises(Exception):
        _candidate(conn, second, first_snapshot)
    conn.rollback()
    with pytest.raises(Exception):
        _candidate(conn, second, second_snapshot, expected=first_generation)
    conn.rollback()


def test_candidate_and_failed_candidate_leave_current_untouched(conn, schema):
    project = _project(conn)
    snapshot = _snapshot(conn, project)
    base = _candidate(conn, project, snapshot)
    _validate(conn, base)
    _promote(conn, base)
    candidate = _candidate(conn, project, snapshot, expected=base)
    conn.commit()

    assert conn.execute(
        "SELECT generation_id FROM current_analysis_generations WHERE project_id = %s",
        (project,),
    ).fetchone()[0] == base
    conn.execute(
        "UPDATE analysis_generations SET lifecycle_status='failed', terminal_at=NOW() WHERE id=%s",
        (candidate,),
    )
    conn.commit()
    assert conn.execute(
        "SELECT generation_id FROM current_analysis_generations WHERE project_id = %s",
        (project,),
    ).fetchone()[0] == base


def test_expected_base_promotion_preserves_previous_generation(conn, schema):
    project = _project(conn)
    snapshot = _snapshot(conn, project)
    base = _candidate(conn, project, snapshot)
    _validate(conn, base)
    _promote(conn, base)
    replacement = _candidate(conn, project, snapshot, expected=base)
    _validate(conn, replacement)
    _promote(conn, replacement, base)
    conn.commit()

    assert conn.execute(
        "SELECT generation_id FROM current_analysis_generations WHERE project_id=%s",
        (project,),
    ).fetchone()[0] == replacement
    assert conn.execute(
        "SELECT lifecycle_status FROM analysis_generations WHERE id=%s", (base,)
    ).fetchone()[0] == "accepted"
    with pytest.raises(Exception, match="historical records"):
        conn.execute("DELETE FROM analysis_generations WHERE id=%s", (base,))
    conn.rollback()


def test_unvalidated_and_stale_base_promotions_fail_closed(conn, schema):
    project = _project(conn)
    snapshot = _snapshot(conn, project)
    base = _candidate(conn, project, snapshot)
    conn.commit()
    with pytest.raises(Exception, match="not been validated"):
        _promote(conn, base)
    conn.rollback()

    _validate(conn, base)
    _promote(conn, base)
    conn.commit()
    stale = _candidate(conn, project, snapshot, expected=None)
    _validate(conn, stale)
    conn.commit()
    with pytest.raises(Exception, match="current analysis changed"):
        _promote(conn, stale)
    conn.rollback()
    assert conn.execute(
        "SELECT generation_id FROM current_analysis_generations WHERE project_id=%s",
        (project,),
    ).fetchone()[0] == base


def test_direct_current_binding_deletion_is_rejected(conn, schema):
    project = _project(conn)
    snapshot = _snapshot(conn, project)
    generation = _candidate(conn, project, snapshot)
    _validate(conn, generation)
    _promote(conn, generation)
    conn.commit()
    with pytest.raises(Exception, match="cannot be deleted directly"):
        conn.execute(
            "DELETE FROM current_analysis_generations WHERE project_id=%s",
            (project,),
        )
    conn.rollback()


def test_explicit_project_deletion_retains_v7_cascade_behavior(conn, schema):
    project = _project(conn)
    snapshot = _snapshot(conn, project)
    generation = _candidate(conn, project, snapshot)
    _validate(conn, generation)
    _promote(conn, generation)
    conn.commit()
    conn.execute("DELETE FROM projects WHERE id=%s", (project,))
    conn.commit()
    assert conn.execute(
        "SELECT count(*) FROM analysis_generations WHERE project_id=%s", (project,)
    ).fetchone()[0] == 0


def test_legacy_bootstrap_is_idempotent_and_creates_one_baseline(conn, schema):
    project = _project(conn)
    snapshot = uuid4()
    generation = uuid4()
    assert _bootstrap(conn, project, snapshot, generation) == generation
    assert _bootstrap(conn, project, snapshot, generation) == generation
    conn.commit()
    assert conn.execute(
        "SELECT count(*) FROM decision_input_snapshots WHERE project_id=%s", (project,)
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT count(*) FROM analysis_generations WHERE project_id=%s", (project,)
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT bootstrap_kind FROM analysis_generations WHERE id=%s", (generation,)
    ).fetchone()[0] == "legacy_baseline"


def test_concurrent_legacy_bootstrap_is_singleton(conn, schema):
    project = _project(conn)
    snapshot = uuid4()
    generation = uuid4()
    conn.commit()

    def attempt(_):
        worker = psycopg.connect(pg.require_dsn(), options=f"-c search_path={schema}")
        try:
            result = _bootstrap(worker, project, snapshot, generation)
            worker.commit()
            return result
        finally:
            worker.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, range(2)))
    assert results == [generation, generation]
    assert conn.execute(
        "SELECT count(*) FROM current_analysis_generations WHERE project_id=%s",
        (project,),
    ).fetchone()[0] == 1


def test_concurrent_promotions_from_same_base_allow_exactly_one(conn, schema):
    project = _project(conn)
    snapshot = _snapshot(conn, project)
    base = _candidate(conn, project, snapshot)
    _validate(conn, base)
    _promote(conn, base)
    first = _candidate(conn, project, snapshot, expected=base)
    second = _candidate(conn, project, snapshot, expected=base)
    _validate(conn, first)
    _validate(conn, second)
    conn.commit()

    def attempt(generation_id):
        worker = psycopg.connect(pg.require_dsn(), options=f"-c search_path={schema}")
        try:
            _promote(worker, generation_id, base)
            worker.commit()
            return "promoted"
        except Exception as exc:
            worker.rollback()
            return str(exc)
        finally:
            worker.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, (first, second)))

    assert results.count("promoted") == 1
    assert sum("current analysis changed" in result for result in results) == 1
    current = conn.execute(
        "SELECT generation_id FROM current_analysis_generations WHERE project_id=%s",
        (project,),
    ).fetchone()[0]
    assert current in (first, second)
    assert conn.execute(
        "SELECT count(*) FROM current_analysis_generations WHERE project_id=%s",
        (project,),
    ).fetchone()[0] == 1


def test_async_repository_creates_validates_and_promotes_complete_generation(conn, schema):
    project = _project(conn)
    conn.commit()
    state = make_completed_state(str(project))

    async def exercise():
        pool = await asyncpg.create_pool(
            pg.require_dsn(), server_settings={"search_path": schema}
        )
        try:
            candidate = await state_coherence.create_candidate(
                pool,
                state,
                workflow_identity="workflow-v1",
                expected_base_generation_id=None,
            )
            await state_coherence.validate_candidate(pool, candidate.generation_id)
            await state_coherence.promote_candidate(
                pool, candidate.generation_id, expected_base_generation_id=None
            )
            return candidate, await state_coherence.current_generation(
                pool, str(project), state_coherence.primary_decision_id(state)
            )
        finally:
            await pool.close()

    candidate, current = asyncio.run(exercise())
    assert current is not None
    assert str(current["id"]) == candidate.generation_id
    payload = current["analysis_state_json"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    assert payload["state"]["report"] == "final report"
    assert state.analysis_generation_id == candidate.generation_id


def test_async_legacy_bootstrap_is_deterministic_and_idempotent(conn, schema):
    project = _project(conn)
    conn.commit()
    state = make_completed_state(str(project))

    async def exercise():
        pool = await asyncpg.create_pool(
            pg.require_dsn(), server_settings={"search_path": schema}
        )
        try:
            first = await state_coherence.bootstrap_current_analysis(pool, state)
            second = await state_coherence.bootstrap_current_analysis(pool, state)
            return first, second
        finally:
            await pool.close()

    first, second = asyncio.run(exercise())
    assert first == second
    assert conn.execute(
        "SELECT count(*) FROM analysis_generations WHERE project_id=%s", (project,)
    ).fetchone()[0] == 1


def test_store_save_atomically_binds_effective_input_identity(conn, schema):
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
    state = ProjectState(
        project_id=str(uuid4()),
        project_name="Store binding",
        brief="Choose A or B",
    )

    async def exercise():
        pool = await asyncpg.create_pool(
            pg.require_dsn(), server_settings={"search_path": schema}
        )
        try:
            store._mem.clear()
            with patch("store._get_pool", new=AsyncMock(return_value=pool)):
                await store.save(state)
                first_snapshot = state.effective_input_snapshot_id
                state.brief = "Choose A or B before Q4"
                state.analysis_generation_id = str(uuid4())
                await store.save(state)
                return first_snapshot
        finally:
            store._mem.clear()
            await pool.close()

    first_snapshot = asyncio.run(exercise())
    assert first_snapshot
    assert state.effective_input_snapshot_id != first_snapshot
    assert state.analysis_generation_id == ""
    assert conn.execute(
        "SELECT count(*) FROM decision_input_snapshots WHERE project_id=%s",
        (state.project_id,),
    ).fetchone()[0] == 2
    persisted = conn.execute(
        "SELECT state_json FROM state_snapshots WHERE project_id=%s", (state.project_id,)
    ).fetchone()[0]
    assert persisted["effective_input_snapshot_id"] == state.effective_input_snapshot_id
