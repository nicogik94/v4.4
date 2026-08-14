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
from psycopg import sql

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
def default_search_path_conn():
    database = f"w8_default_{uuid4().hex[:16]}"
    admin = psycopg.connect(pg.require_dsn(), autocommit=True)
    connection = None
    try:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        connection = psycopg.connect(pg.require_dsn(), dbname=database)
        yield connection
    finally:
        if connection is not None:
            connection.close()
        admin.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                sql.Identifier(database)
            )
        )
        admin.close()


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


def _function_semantic_inventory(conn):
    return conn.execute(
        """
        SELECT function_info.proname,
               pg_catalog.oidvectortypes(function_info.proargtypes),
               function_info.prorettype::regtype::text,
               function_info.proargnames,
               function_info.pronargdefaults,
               language_info.lanname,
               function_info.prosecdef,
               function_info.proconfig,
               function_info.provolatile,
               function_info.proisstrict,
               function_info.proparallel,
               function_info.proretset,
               function_info.proleakproof,
               function_info.prokind,
               function_info.prosrc,
               function_info.proowner,
               function_info.proacl::text
        FROM pg_catalog.pg_proc function_info
        JOIN pg_catalog.pg_namespace namespace
          ON namespace.oid = function_info.pronamespace
        JOIN pg_catalog.pg_language language_info
          ON language_info.oid = function_info.prolang
        WHERE namespace.nspname = pg_catalog.current_schema()
          AND function_info.proname IN (
              'decision_state_reject_snapshot_mutation',
              'decision_state_guard_generation_mutation',
              'decision_state_guard_current_binding',
              'promote_analysis_generation',
              'bootstrap_analysis_generation'
          )
        ORDER BY function_info.proname, function_info.proargtypes::text
        """
    ).fetchall()


def _trigger_semantic_inventory(conn):
    return conn.execute(
        """
        SELECT relation.relname,
               trigger_info.tgname,
               function_namespace.nspname,
               function_info.proname,
               pg_catalog.oidvectortypes(function_info.proargtypes),
               trigger_info.tgenabled,
               trigger_info.tgtype,
               trigger_info.tgattr::text,
               trigger_info.tgqual::text,
               trigger_info.tgnargs,
               pg_catalog.encode(trigger_info.tgargs, 'hex'),
               trigger_info.tgdeferrable,
               trigger_info.tginitdeferred
        FROM pg_catalog.pg_trigger trigger_info
        JOIN pg_catalog.pg_class relation ON relation.oid = trigger_info.tgrelid
        JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
        JOIN pg_catalog.pg_proc function_info ON function_info.oid = trigger_info.tgfoid
        JOIN pg_catalog.pg_namespace function_namespace
          ON function_namespace.oid = function_info.pronamespace
        WHERE namespace.nspname = pg_catalog.current_schema()
          AND relation.relname IN (
              'decision_input_snapshots',
              'analysis_generations',
              'current_analysis_generations'
          )
          AND NOT trigger_info.tgisinternal
        ORDER BY relation.relname, trigger_info.tgname
        """
    ).fetchall()


def _assert_reapply_rejects_without_repair(conn, inventory, message):
    drifted = inventory(conn)
    with pytest.raises(Exception, match=message):
        pg._run_script(conn, V64)
    conn.rollback()
    assert inventory(conn) == drifted


def test_default_search_path_fresh_apply_and_exact_reapply(default_search_path_conn):
    conn = default_search_path_conn
    default_search_path = '"$user", public'
    assert conn.execute("SHOW search_path").fetchone()[0] == default_search_path
    assert conn.execute("SELECT current_schema()").fetchone()[0] == "public"

    prior = pg._begin_autocommit(conn)
    pg._run_script(conn, pg.INIT_SQL)
    pg._run_script(conn, pg.OUTCOMES_SQL)
    pg._run_script(conn, V64)

    assert conn.execute("SHOW search_path").fetchone()[0] == default_search_path
    assert conn.execute(
        """
        SELECT count(*) FROM pg_catalog.pg_class relation
        JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public'
          AND relation.relkind = 'r'
          AND relation.relname IN (
              'decision_input_snapshots', 'analysis_generations',
              'current_analysis_generations')
        """
    ).fetchone()[0] == 3

    inventory = _function_semantic_inventory(conn)
    assert len(inventory) == 5
    assert {tuple(row[7] or ()) for row in inventory} == {("search_path=public",)}
    assert all("$user" not in setting for row in inventory for setting in row[7])

    pg._run_script(conn, V64)
    assert conn.execute("SHOW search_path").fetchone()[0] == default_search_path
    assert _function_semantic_inventory(conn) == inventory
    pg._restore_autocommit(conn, prior)


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
        """
        SELECT count(*) FROM pg_trigger trigger_info
        JOIN pg_class relation ON relation.oid = trigger_info.tgrelid
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = %s
          AND trigger_info.tgname IN (
              'trg_dis_immutable','trg_ag_immutable','trg_cag_guard'
          )
        """,
        (schema,),
    ).fetchone()[0] == 3


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param(
            """
            CREATE OR REPLACE FUNCTION promote_analysis_generation(
                candidate_id UUID, expected_base_id UUID DEFAULT NULL
            ) RETURNS VOID LANGUAGE plpgsql SET search_path FROM CURRENT
              AS $$ BEGIN NULL; END; $$
            """,
            id="body",
        ),
        pytest.param(
            "ALTER FUNCTION promote_analysis_generation(UUID, UUID) "
            "SET search_path = pg_catalog",
            id="search_path",
        ),
        pytest.param(
            "ALTER FUNCTION promote_analysis_generation(UUID, UUID) "
            "SET work_mem = '64kB'",
            id="additional_config",
        ),
        pytest.param(
            """
            DROP FUNCTION promote_analysis_generation(UUID, UUID);
            CREATE FUNCTION promote_analysis_generation(candidate_id UUID)
            RETURNS VOID LANGUAGE plpgsql SET search_path FROM CURRENT
            AS $$ BEGIN NULL; END; $$;
            REVOKE ALL ON FUNCTION promote_analysis_generation(UUID) FROM PUBLIC
            """,
            id="signature",
        ),
    ],
)
def test_reapply_rejects_function_body_config_search_path_and_signature_before_repair(
    conn, schema, mutation
):
    prior = pg._begin_autocommit(conn)
    canonical = _function_semantic_inventory(conn)
    conn.execute(mutation)
    assert _function_semantic_inventory(conn) != canonical
    _assert_reapply_rejects_without_repair(
        conn, _function_semantic_inventory, "preflight function semantic drift"
    )
    pg._restore_autocommit(conn, prior)


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param(
            """
            DROP TRIGGER trg_ag_immutable ON analysis_generations;
            CREATE TRIGGER trg_ag_immutable
            BEFORE UPDATE OR DELETE ON analysis_generations
            FOR EACH ROW EXECUTE FUNCTION decision_state_reject_snapshot_mutation()
            """,
            id="attachment",
        ),
        pytest.param(
            "ALTER TABLE analysis_generations DISABLE TRIGGER trg_ag_immutable",
            id="enabled_state",
        ),
        pytest.param(
            """
            DROP TRIGGER trg_ag_immutable ON analysis_generations;
            CREATE TRIGGER trg_ag_immutable
            BEFORE DELETE ON analysis_generations
            FOR EACH ROW EXECUTE FUNCTION decision_state_guard_generation_mutation()
            """,
            id="event_type",
        ),
        pytest.param(
            """
            DROP TRIGGER trg_ag_immutable ON analysis_generations;
            CREATE TRIGGER trg_ag_immutable
            BEFORE UPDATE OF lifecycle_status OR DELETE ON analysis_generations
            FOR EACH ROW EXECUTE FUNCTION decision_state_guard_generation_mutation()
            """,
            id="narrowed_update_of_tgattr",
        ),
        pytest.param(
            """
            DROP TRIGGER trg_ag_immutable ON analysis_generations;
            CREATE TRIGGER trg_ag_immutable
            BEFORE UPDATE OR DELETE ON analysis_generations
            FOR EACH ROW WHEN (OLD.id IS NOT NULL)
            EXECUTE FUNCTION decision_state_guard_generation_mutation()
            """,
            id="predicate",
        ),
        pytest.param(
            """
            DROP TRIGGER trg_ag_immutable ON analysis_generations;
            CREATE TRIGGER trg_ag_immutable
            BEFORE UPDATE OR DELETE ON analysis_generations
            FOR EACH ROW EXECUTE FUNCTION
                decision_state_guard_generation_mutation('narrowed')
            """,
            id="arguments",
        ),
    ],
)
def test_reapply_rejects_trigger_semantic_drift_before_drop_create_repair(
    conn, schema, mutation
):
    prior = pg._begin_autocommit(conn)
    canonical = _trigger_semantic_inventory(conn)
    conn.execute(mutation)
    assert _trigger_semantic_inventory(conn) != canonical
    _assert_reapply_rejects_without_repair(
        conn, _trigger_semantic_inventory, "preflight trigger semantic drift"
    )
    pg._restore_autocommit(conn, prior)


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


def test_function_acl_owner_and_extra_trigger_drift_fail_closed_without_repair(
    conn, schema
):
    prior = pg._begin_autocommit(conn)
    extra_executor = f"w8_extra_executor_{uuid4().hex[:8]}"
    conn.execute(sql.SQL("CREATE ROLE {}").format(sql.Identifier(extra_executor)))
    try:
        canonical_functions = _function_semantic_inventory(conn)
        conn.execute(
            sql.SQL(
                "GRANT EXECUTE ON FUNCTION "
                "promote_analysis_generation(uuid,uuid) TO {}"
            ).format(sql.Identifier(extra_executor))
        )
        assert _function_semantic_inventory(conn) != canonical_functions
        _assert_reapply_rejects_without_repair(
            conn, _function_semantic_inventory, "preflight function owner/ACL drift"
        )
        conn.execute(
            sql.SQL(
                "REVOKE EXECUTE ON FUNCTION "
                "promote_analysis_generation(uuid,uuid) FROM {}"
            ).format(sql.Identifier(extra_executor))
        )

        conn.execute(
            "REVOKE EXECUTE ON FUNCTION promote_analysis_generation(uuid,uuid) FROM CURRENT_USER"
        )
        assert _function_semantic_inventory(conn) != canonical_functions
        _assert_reapply_rejects_without_repair(
            conn, _function_semantic_inventory, "preflight function owner/ACL drift"
        )
        conn.execute(
            "GRANT EXECUTE ON FUNCTION promote_analysis_generation(uuid,uuid) TO CURRENT_USER"
        )

        conn.execute(
            sql.SQL(
                "ALTER FUNCTION promote_analysis_generation(uuid,uuid) OWNER TO {}"
            ).format(sql.Identifier(extra_executor))
        )
        assert _function_semantic_inventory(conn) != canonical_functions
        _assert_reapply_rejects_without_repair(
            conn, _function_semantic_inventory, "preflight function owner/ACL drift"
        )
        conn.execute(
            "ALTER FUNCTION promote_analysis_generation(uuid,uuid) OWNER TO CURRENT_USER"
        )

        canonical_triggers = _trigger_semantic_inventory(conn)
        conn.execute(
            "CREATE TRIGGER trg_w8_extra BEFORE UPDATE ON analysis_generations "
            "FOR EACH ROW EXECUTE FUNCTION decision_state_guard_generation_mutation()"
        )
        assert _trigger_semantic_inventory(conn) != canonical_triggers
        _assert_reapply_rejects_without_repair(
            conn, _trigger_semantic_inventory, "preflight trigger semantic drift"
        )
    finally:
        conn.rollback()
        conn.execute(
            sql.SQL("DROP OWNED BY {}").format(sql.Identifier(extra_executor))
        )
        conn.execute(
            sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(extra_executor))
        )
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
