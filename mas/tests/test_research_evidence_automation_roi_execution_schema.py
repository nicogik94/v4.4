"""Focused PostgreSQL contract tests for v60 (skipped without approved DSNs)."""
import contextlib
from concurrent.futures import ThreadPoolExecutor
import uuid

import pytest

from knowledge.automation_roi import approvals as roi_approvals
from knowledge.automation_roi import repository as roi_repository
from research_evidence import automation_roi_use_service
from research_evidence import automation_roi_execution_repository as execution_repository
from research_evidence.automation_roi_execution_models import (
    AutomationRoiExecutionRequest,
)
from research_evidence.automation_roi_execution_service import execute_automation_roi
from tests import evidence_snapshot_pg as pg
from tests import automation_roi_fixtures as roi_fixtures
from tests.test_research_evidence_automation_roi_use_schema import (
    FUNCTION_OWNER,
    OBJECT_SCHEMA,
    RUNTIME_ROLE,
    _binding_set,
    _ensure_separate_roles,
    _prepare_v59_foundation,
    _snapshot_command,
)


@pytest.fixture
def conn():
    pg.require_dsn()
    connection = pg.connect()
    try:
        _ensure_separate_roles(connection)
        yield connection
    finally:
        with contextlib.suppress(Exception):
            connection.rollback()
            connection.execute("RESET ROLE")
        connection.close()


@pytest.fixture
def schema_v60(conn, monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")
    with pg.fresh_schema(conn) as schema:
        try:
            _prepare_v59_foundation(conn, schema)
            pg.apply_v59_research_automation_roi_use(conn)
            pg.apply_v60_research_automation_roi_execution(conn)
            yield schema
        finally:
            prior = pg._begin_autocommit(conn)
            conn.execute(f"SET ROLE {FUNCTION_OWNER}")
            conn.execute(f'DROP SCHEMA IF EXISTS "{OBJECT_SCHEMA}" CASCADE')
            conn.execute("RESET ROLE")
            pg._restore_autocommit(conn, prior)


def _approved_snapshot(conn):
    project, binding_set, records, _ = _binding_set(conn, tag="execute")
    snapshot = automation_roi_use_service.record_automation_roi_input_snapshot(
        conn, _snapshot_command(project, binding_set, records)
    )
    conn.commit()
    return project, snapshot


def _snapshot(conn, *, tag, disposition="meets_contract"):
    project, binding_set, records, _ = _binding_set(
        conn, tag=tag, disposition=disposition
    )
    snapshot = automation_roi_use_service.record_automation_roi_input_snapshot(
        conn,
        _snapshot_command(
            project, binding_set, records, request_id=f"snapshot-{tag}"
        ),
    )
    conn.commit()
    return project, binding_set, records, snapshot


def test_fresh_apply_clean_reapply_and_composite_link(conn, schema_v60):
    assert pg.table_exists(
        conn, OBJECT_SCHEMA, "automation_roi_calculation_result"
    )
    pg.apply_v60_research_automation_roi_execution(conn)
    foreign_key = conn.execute(
        """
        SELECT pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE conname = 'fk_rearoicr_snapshot_scope'
          AND conrelid = %s::regclass
        """,
        (
            f"{OBJECT_SCHEMA}.automation_roi_calculation_result",
        ),
    ).fetchone()[0]
    assert (
        "input_snapshot_id, project_id, consumer_contract, binding_set_id"
        in foreign_key
    )


def test_runtime_execution_replay_and_direct_write_denial(conn, schema_v60):
    project, snapshot = _approved_snapshot(conn)
    request = AutomationRoiExecutionRequest(
        project_id=project,
        input_snapshot_id=snapshot.id,
        idempotency_key="execution-1",
    )
    with pg.runtime_connection(schema_v60) as runtime:
        first = execute_automation_roi(
            runtime, request, server_actor="authenticated-service"
        )
        second = execute_automation_roi(
            runtime, request, server_actor="authenticated-service"
        )
        assert second.id == first.id
        assert first.input_snapshot_id == snapshot.id
        assert len(first.input_manifest_json) == 6
        assert first.formula_fingerprint == (
            "260ea8cf45b4d1e58fbb290838bd6da044b9b5ca6eba8874cbbb4ef8596b58f7"
        )
        assert first.annual_labor_savings == 20800
        assert first.annual_net_benefit == 19800
        assert first.first_year_net_benefit == 14800
        assert first.first_year_roi_percent == 296
        with pytest.raises(Exception, match="permission denied"):
            runtime.execute(
                f"INSERT INTO {OBJECT_SCHEMA}."
                "automation_roi_calculation_result(id) VALUES (gen_random_uuid())"
            )
        runtime.rollback()


def test_full_idempotency_matrix_and_lost_response_replay(conn, schema_v60):
    project, binding_set, records, snapshot_one = _snapshot(
        conn, tag="idempotency"
    )
    snapshot_two = automation_roi_use_service.record_automation_roi_input_snapshot(
        conn,
        _snapshot_command(
            project, binding_set, records, request_id="snapshot-idempotency-2"
        ),
    )
    conn.commit()
    first_request = AutomationRoiExecutionRequest(
        project_id=project,
        input_snapshot_id=snapshot_one.id,
        idempotency_key="same-key",
    )
    with pg.runtime_connection(schema_v60) as runtime:
        first = execute_automation_roi(
            runtime, first_request, server_actor="authenticated-service"
        )
        runtime.commit()

        same_key_same_operation = execute_automation_roi(
            runtime, first_request, server_actor="authenticated-service"
        )
        assert same_key_same_operation.id == first.id

        different_key_same_operation = execute_automation_roi(
            runtime,
            AutomationRoiExecutionRequest(
                project_id=project,
                input_snapshot_id=snapshot_one.id,
                idempotency_key="different-key",
            ),
            server_actor="authenticated-service",
        )
        assert different_key_same_operation.id == first.id

        with pytest.raises(execution_repository.AutomationRoiExecutionConflict):
            execute_automation_roi(
                runtime,
                AutomationRoiExecutionRequest(
                    project_id=project,
                    input_snapshot_id=snapshot_two.id,
                    idempotency_key="same-key",
                ),
                server_actor="authenticated-service",
            )

        different_operation = execute_automation_roi(
            runtime,
            AutomationRoiExecutionRequest(
                project_id=project,
                input_snapshot_id=snapshot_two.id,
                idempotency_key="new-operation-key",
            ),
            server_actor="authenticated-service",
        )
        assert different_operation.id != first.id
        runtime.commit()

        # Simulate a committed response that the caller never received.
        lost = runtime.execute(
            f"""
            SELECT {OBJECT_SCHEMA}.research_evidence_execute_automation_roi(
                %s::uuid, %s::uuid, %s, %s
            )::text
            """,
            (
                project,
                snapshot_two.id,
                "unobserved-response-key",
                "authenticated-service",
            ),
        ).fetchone()[0]
        runtime.commit()
        replay = execute_automation_roi(
            runtime,
            AutomationRoiExecutionRequest(
                project_id=project,
                input_snapshot_id=snapshot_two.id,
                idempotency_key="retry-after-lost-response",
            ),
            server_actor="authenticated-service",
        )
        assert replay.id == lost == different_operation.id


def test_non_satisfies_and_cross_project_fail_without_result(conn, schema_v60):
    rejected_project, _, _, rejected_snapshot = _snapshot(
        conn, tag="rejected", disposition="does_not_meet_contract"
    )
    other_project = pg.insert_project(conn, name="wrong execution project")
    conn.commit()
    with pg.runtime_connection(schema_v60) as runtime:
        with pytest.raises(execution_repository.AutomationRoiExecutionRejected):
            execute_automation_roi(
                runtime,
                AutomationRoiExecutionRequest(
                    project_id=rejected_project,
                    input_snapshot_id=rejected_snapshot.id,
                    idempotency_key="not-satisfies",
                ),
                server_actor="authenticated-service",
            )
        with pytest.raises(execution_repository.AutomationRoiExecutionRejected):
            execute_automation_roi(
                runtime,
                AutomationRoiExecutionRequest(
                    project_id=other_project,
                    input_snapshot_id=rejected_snapshot.id,
                    idempotency_key="cross-project",
                ),
                server_actor="authenticated-service",
            )
    assert conn.execute(
        f"""
        SELECT count(*)
        FROM {OBJECT_SCHEMA}.automation_roi_calculation_result
        WHERE input_snapshot_id = %s
        """,
        (rejected_snapshot.id,),
    ).fetchone()[0] == 0


def test_concurrent_same_operation_submissions_replay_one_result(
    conn, schema_v60
):
    project, snapshot = _approved_snapshot(conn)

    def submit(key):
        with pg.runtime_connection(schema_v60) as runtime:
            result = execute_automation_roi(
                runtime,
                AutomationRoiExecutionRequest(
                    project_id=project,
                    input_snapshot_id=snapshot.id,
                    idempotency_key=key,
                ),
                server_actor="authenticated-service",
            )
            runtime.commit()
            return result.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        result_ids = tuple(
            executor.map(
                submit,
                (f"concurrent-{uuid.uuid4()}", f"concurrent-{uuid.uuid4()}"),
            )
        )
    assert result_ids[0] == result_ids[1]
    assert conn.execute(
        f"""
        SELECT count(*)
        FROM {OBJECT_SCHEMA}.automation_roi_calculation_result
        WHERE project_id = %s AND input_snapshot_id = %s
        """,
        (project, snapshot.id),
    ).fetchone()[0] == 1


def _snapshot_with_input_overrides(
    conn,
    monkeypatch,
    *,
    tag,
    values=None,
    currencies=None,
    periods=None,
):
    values = values or {}
    currencies = currencies or {}
    periods = periods or {}
    original = roi_fixtures.seed_and_freeze

    def customized(connection, project_id, role, **kwargs):
        if role not in periods:
            return original(
                connection,
                project_id,
                role,
                value=values.get(role),
                currency=currencies.get(role, "USD"),
                **kwargs,
            )
        fact = roi_fixtures.make_validated_fact(
            role,
            value=values.get(role),
            currency=currencies.get(role, "USD"),
        )
        fixture_tag = kwargs.get("tag", role)
        blob_id = roi_fixtures.ev_repo.insert_or_get_blob(
            connection,
            project_id=project_id,
            content_hash=f"h-{role}-{fixture_tag}",
            byte_size=8,
        )
        snapshot_id = roi_fixtures.ev_repo.insert_snapshot(
            connection,
            source_blob_id=blob_id,
            project_id=project_id,
            storage_ref=f"/store/{role}/{fixture_tag}",
        )
        fact_id, _ = roi_repository.create_eligible_fact(
            connection,
            project_id=project_id,
            source_snapshot_id=snapshot_id,
            fact=fact,
            subject_label="process X",
            metric_label=role,
            period_basis=periods[role],
            source_locator=f"doc#{role}",
            extraction_rationale="operator extracted",
            actor="op",
        )
        roi_approvals.append_decision(
            connection,
            project_id=project_id,
            candidate_fact_revision_id=fact_id,
            decision_type="approve",
            actor="op",
        )
        frozen = roi_repository.freeze_input(
            connection,
            project_id=project_id,
            candidate_fact_revision_id=fact_id,
            input_role=role,
            frozen_by="op",
        )
        return fact_id, snapshot_id, frozen

    with monkeypatch.context() as patch:
        patch.setattr(roi_fixtures, "seed_and_freeze", customized)
        project, _, _, snapshot = _snapshot(conn, tag=tag)
    return project, snapshot


def test_database_blocked_zero_cost_and_negative_savings_behaviors(
    conn, schema_v60, monkeypatch
):
    cases = (
        (
            _snapshot_with_input_overrides(
                conn,
                monkeypatch,
                tag="currency-blocked",
                currencies={"annual_recurring_cost": "EUR"},
            ),
            "blocked",
            "currency_incompatibility",
        ),
        (
            _snapshot_with_input_overrides(
                conn,
                monkeypatch,
                tag="period-blocked",
                periods={"post_automation_hours_per_period": "month"},
            ),
            "blocked",
            "period_incompatibility",
        ),
        (
            _snapshot_with_input_overrides(
                conn,
                monkeypatch,
                tag="zero-cost",
                values={"one_time_implementation_cost": "0"},
            ),
            "not_applicable",
            "roi_percent",
        ),
        (
            _snapshot_with_input_overrides(
                conn,
                monkeypatch,
                tag="negative-savings",
                values={
                    "baseline_hours_per_period": "2",
                    "post_automation_hours_per_period": "10",
                },
            ),
            "valid",
            "negative_hours_delta",
        ),
    )
    with pg.runtime_connection(schema_v60) as runtime:
        for index, ((project, snapshot), status, diagnostic) in enumerate(cases):
            result = execute_automation_roi(
                runtime,
                AutomationRoiExecutionRequest(
                    project_id=project,
                    input_snapshot_id=snapshot.id,
                    idempotency_key=f"behavior-{index}",
                ),
                server_actor="authenticated-service",
            )
            assert result.status == status
            assert diagnostic in result.diagnostics_json
            if status == "blocked":
                assert result.annual_labor_savings is None
            if status == "not_applicable":
                assert result.first_year_roi_percent is None
            if diagnostic == "negative_hours_delta":
                assert result.annual_labor_savings == -20800


def test_runtime_helper_allocator_and_mutation_denials(conn, schema_v60):
    project, snapshot = _approved_snapshot(conn)
    with pg.runtime_connection(schema_v60) as runtime:
        result = execute_automation_roi(
            runtime,
            AutomationRoiExecutionRequest(
                project_id=project,
                input_snapshot_id=snapshot.id,
                idempotency_key="security-result",
            ),
            server_actor="authenticated-service",
        )
        runtime.commit()
        for statement in (
            f"SELECT {OBJECT_SCHEMA}."
            "research_evidence_prepare_automation_roi_result()",
            f"UPDATE {OBJECT_SCHEMA}.automation_roi_calculation_result "
            "SET requested_by = 'forged' WHERE id = %s",
            f"DELETE FROM {OBJECT_SCHEMA}.automation_roi_calculation_result "
            "WHERE id = %s",
        ):
            with pytest.raises(Exception, match="permission denied"):
                runtime.execute(statement, (result.id,) if "%s" in statement else None)
            runtime.rollback()
    prior = pg._begin_autocommit(conn)
    conn.execute(f"SET ROLE {FUNCTION_OWNER}")
    try:
        with pytest.raises(Exception, match="append-only"):
            conn.execute(
                f"UPDATE {OBJECT_SCHEMA}.automation_roi_calculation_result "
                "SET requested_by = 'owner-forged' WHERE id = %s",
                (result.id,),
            )
    finally:
        conn.execute("RESET ROLE")
        pg._restore_autocommit(conn, prior)


def test_fixed_search_path_resists_temporary_shadow_objects(conn, schema_v60):
    project, snapshot = _approved_snapshot(conn)
    with pg.runtime_connection(schema_v60) as runtime:
        runtime.execute(
            """
            CREATE TEMP TABLE automation_roi_calculation_result (
                id uuid, requested_by text
            )
            """
        )
        result = execute_automation_roi(
            runtime,
            AutomationRoiExecutionRequest(
                project_id=project,
                input_snapshot_id=snapshot.id,
                idempotency_key="shadow-resistant",
            ),
            server_actor="authenticated-service",
        )
        assert result.status == "valid"


@pytest.mark.parametrize(
    "mutation",
    [
        (
            "ALTER FUNCTION research_evidence_automation_roi."
            "research_evidence_execute_automation_roi(uuid,uuid,text,text) "
            "SECURITY INVOKER"
        ),
        (
            "ALTER TABLE research_evidence_automation_roi."
            "automation_roi_calculation_result "
            "DISABLE TRIGGER trg_rearoicr_prepare_insert"
        ),
        (
            "GRANT INSERT ON research_evidence_automation_roi."
            "automation_roi_calculation_result "
            "TO workflow_automation_roi_runtime"
        ),
    ],
)
def test_reapply_rejects_function_trigger_and_acl_drift(
    conn, schema_v60, mutation
):
    prior = pg._begin_autocommit(conn)
    conn.execute(f"SET ROLE {FUNCTION_OWNER}")
    conn.execute(mutation)
    conn.execute("RESET ROLE")
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception, match="v60 contract violation"):
        pg.apply_v60_research_automation_roi_execution(conn)


def test_reapply_rejects_partial_and_extra_object_state(conn, schema_v60):
    prior = pg._begin_autocommit(conn)
    conn.execute(f"SET ROLE {FUNCTION_OWNER}")
    conn.execute(
        f"DROP FUNCTION {OBJECT_SCHEMA}."
        "research_evidence_prepare_automation_roi_result() CASCADE"
    )
    conn.execute("RESET ROLE")
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception, match="partial execution state"):
        pg.apply_v60_research_automation_roi_execution(conn)


def test_absent_dedicated_schema_is_rejected(conn):
    with pg.fresh_schema(conn) as upstream:
        _prepare_v59_foundation(conn, upstream, provision_dedicated=False)
        with pytest.raises(
            Exception,
            match="preprovisioned, canonically owned dedicated schema",
        ):
            pg.apply_v60_research_automation_roi_execution(conn)


def test_misowned_dedicated_schema_is_rejected(conn):
    with pg.fresh_schema(conn) as upstream:
        _prepare_v59_foundation(conn, upstream)
        pg.apply_v59_research_automation_roi_use(conn)
        prior = pg._begin_autocommit(conn)
        conn.execute(
            f"ALTER SCHEMA {OBJECT_SCHEMA} OWNER TO workflow_migration_owner"
        )
        pg._restore_autocommit(conn, prior)
        try:
            with pytest.raises(
                Exception,
                match="preprovisioned, canonically owned dedicated schema",
            ):
                pg.apply_v60_research_automation_roi_execution(conn)
        finally:
            prior = pg._begin_autocommit(conn)
            conn.execute(f"DROP SCHEMA IF EXISTS {OBJECT_SCHEMA} CASCADE")
            pg._restore_autocommit(conn, prior)


def test_reapply_rejects_result_owner_drift(conn, schema_v60):
    prior = pg._begin_autocommit(conn)
    conn.execute(
        f"ALTER TABLE {OBJECT_SCHEMA}.automation_roi_calculation_result "
        "OWNER TO workflow_migration_owner"
    )
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception, match="structural or owner drift"):
        pg.apply_v60_research_automation_roi_execution(conn)


def test_reapply_rejects_default_acl_drift(conn, schema_v60):
    prior = pg._begin_autocommit(conn)
    conn.execute(f"SET ROLE {FUNCTION_OWNER}")
    conn.execute(
        "ALTER DEFAULT PRIVILEGES GRANT EXECUTE ON FUNCTIONS "
        f"TO {RUNTIME_ROLE}"
    )
    conn.execute("RESET ROLE")
    pg._restore_autocommit(conn, prior)
    try:
        with pytest.raises(Exception, match="default ACL drift"):
            pg.apply_v60_research_automation_roi_execution(conn)
    finally:
        prior = pg._begin_autocommit(conn)
        conn.execute(f"SET ROLE {FUNCTION_OWNER}")
        conn.execute(
            "ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS "
            f"FROM {RUNTIME_ROLE}"
        )
        conn.execute("RESET ROLE")
        pg._restore_autocommit(conn, prior)


def test_append_only_public_closure_and_runtime_acl(conn, schema_v60):
    assert not conn.execute(
        """
        SELECT has_table_privilege(
            'workflow_automation_roi_runtime',
            %s,
            'INSERT,UPDATE,DELETE'
        )
        """,
        (f"{OBJECT_SCHEMA}.automation_roi_calculation_result",),
    ).fetchone()[0]
    assert not conn.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM pg_class relation
            CROSS JOIN LATERAL aclexplode(
                COALESCE(
                    relation.relacl,
                    acldefault('r', relation.relowner)
                )
            ) acl
            WHERE relation.oid = %s::regclass
              AND acl.grantee = 0
        )
        """,
        (f"{OBJECT_SCHEMA}.automation_roi_calculation_result",),
    ).fetchone()[0]
    assert conn.execute(
        """
        SELECT has_function_privilege(
            %s,
            %s,
            'EXECUTE'
        )
        """,
        (
            RUNTIME_ROLE,
            f"{OBJECT_SCHEMA}.research_evidence_execute_automation_roi"
            "(uuid,uuid,text,text)",
        ),
    ).fetchone()[0]
