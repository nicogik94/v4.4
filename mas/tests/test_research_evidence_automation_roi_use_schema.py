"""PostgreSQL contracts for secure R1.6A Automation ROI input snapshots."""
import contextlib
from datetime import timedelta
import os
import uuid

import pytest

from research_evidence import binding_service
from research_evidence.automation_roi_use_models import (
    AutomationRoiInputSnapshotCreate,
)
from research_evidence.automation_roi_use_policy import (
    CONSUMER_CONTRACT,
    CONSUMER_CONTRACT_VERSION,
    EVALUATOR_VERSION,
    POLICY_FINGERPRINT,
    POLICY_IDENTIFIER,
    POLICY_PARAMETERS,
    POLICY_VERSION,
    REQUIRED_ROLES,
)
from research_evidence.automation_roi_use_service import (
    record_automation_roi_input_snapshot,
)
from tests import evidence_snapshot_pg as pg
from tests.test_research_evidence_binding_schema import (
    NOW,
    _command,
    _freshness,
    _review,
    _seed_evidence,
)


MIGRATION_OWNER = "workflow_migration_owner"
FUNCTION_OWNER = "workflow_research_evidence_owner"
RUNTIME_ROLE = "workflow_automation_roi_runtime"
OBJECT_SCHEMA = "research_evidence_automation_roi"
MIGRATION_DSN_ENV = "TEST_EVIDENCE_MIGRATION_PG_DSN"
TABLES = (
    "research_evidence_automation_roi_input_snapshot",
    "research_evidence_automation_roi_input_snapshot_binding",
    "automation_roi_input_snapshot_sequence_allocator",
)
FUNCTIONS = (
    "research_evidence_prepare_automation_roi_snapshot",
    "research_evidence_prepare_automation_roi_snapshot_binding",
    "research_evidence_evaluate_automation_roi_bindings",
    "research_evidence_validate_automation_roi_snapshot",
    "research_evidence_assert_automation_roi_snapshot",
    "research_evidence_create_automation_roi_snapshot",
)


def _default_acl_inventory(conn):
    return conn.execute(
        """
        SELECT owner_role.rolname,
               CASE
                   WHEN default_acl.defaclnamespace = 0 THEN 'GLOBAL'
                   ELSE namespace.nspname
               END,
               default_acl.defaclobjtype,
               COALESCE(grantee_role.rolname, 'PUBLIC'),
               grantor_role.rolname,
               acl.privilege_type,
               acl.is_grantable
        FROM pg_default_acl default_acl
        JOIN pg_roles owner_role
          ON owner_role.oid = default_acl.defaclrole
        LEFT JOIN pg_namespace namespace
          ON namespace.oid = default_acl.defaclnamespace
        CROSS JOIN LATERAL aclexplode(default_acl.defaclacl) acl
        LEFT JOIN pg_roles grantee_role
          ON grantee_role.oid = acl.grantee
        LEFT JOIN pg_roles grantor_role
          ON grantor_role.oid = acl.grantor
        WHERE owner_role.rolname = %s
        ORDER BY 2, 3, 4, 6
        """,
        (FUNCTION_OWNER,),
    ).fetchall()


@contextlib.contextmanager
def _migration_login(schema):
    migration_dsn = os.getenv(MIGRATION_DSN_ENV)
    if not migration_dsn:
        pytest.fail(f"{MIGRATION_DSN_ENV} is required")
    psycopg = pg.psycopg_module()
    connection = psycopg.connect(migration_dsn)
    try:
        connection.execute(f'SET search_path TO "{schema}"')
        connection.commit()
        roles = connection.execute(
            "SELECT session_user, current_user"
        ).fetchone()
        assert roles == (MIGRATION_OWNER, MIGRATION_OWNER)
        yield connection
    finally:
        with contextlib.suppress(Exception):
            connection.rollback()
        connection.close()


def _ensure_separate_roles(conn):
    """Provision canonical roles only inside an approved disposable database."""
    prior = pg._begin_autocommit(conn)
    try:
        conn.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_roles
                    WHERE rolname = 'workflow_research_evidence_owner'
                ) THEN
                    CREATE ROLE workflow_research_evidence_owner
                        NOLOGIN NOINHERIT NOSUPERUSER NOCREATEROLE
                        NOCREATEDB NOREPLICATION NOBYPASSRLS;
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM pg_roles
                    WHERE rolname = 'workflow_migration_owner'
                ) THEN
                    CREATE ROLE workflow_migration_owner
                        LOGIN NOINHERIT NOSUPERUSER NOCREATEROLE
                        NOCREATEDB NOREPLICATION NOBYPASSRLS;
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM pg_roles
                    WHERE rolname = 'workflow_automation_roi_runtime'
                ) THEN
                    CREATE ROLE workflow_automation_roi_runtime
                        LOGIN NOINHERIT NOSUPERUSER NOCREATEROLE
                        NOCREATEDB NOREPLICATION NOBYPASSRLS;
                END IF;
                ALTER ROLE workflow_research_evidence_owner
                    NOLOGIN NOINHERIT NOSUPERUSER NOCREATEROLE
                    NOCREATEDB NOREPLICATION NOBYPASSRLS;
                ALTER ROLE workflow_migration_owner
                    LOGIN NOINHERIT NOSUPERUSER NOCREATEROLE
                    NOCREATEDB NOREPLICATION NOBYPASSRLS;
                ALTER ROLE workflow_automation_roi_runtime
                    LOGIN NOINHERIT NOSUPERUSER NOCREATEROLE
                    NOCREATEDB NOREPLICATION NOBYPASSRLS;
                IF (
                    SELECT count(*) = 2
                    FROM pg_attribute
                    WHERE attrelid =
                              'pg_catalog.pg_auth_members'::regclass
                      AND attname IN ('inherit_option', 'set_option')
                      AND NOT attisdropped
                ) THEN
                    EXECUTE
                        'GRANT workflow_research_evidence_owner '
                        'TO workflow_migration_owner '
                        'WITH ADMIN FALSE, INHERIT FALSE, SET TRUE';
                ELSE
                    GRANT workflow_research_evidence_owner
                        TO workflow_migration_owner;
                END IF;
            END
            $$;
            """
        )
    finally:
        pg._restore_autocommit(conn, prior)


@pytest.fixture
def conn():
    pg.require_dsn()
    connection = pg.connect()
    try:
        _ensure_separate_roles(connection)
        prior = pg._begin_autocommit(connection)
        connection.execute(f"SET ROLE {MIGRATION_OWNER}")
        pg._restore_autocommit(connection, prior)
        yield connection
    finally:
        with contextlib.suppress(Exception):
            connection.rollback()
            connection.execute("RESET ROLE")
        connection.close()


@pytest.fixture
def schema_v59(conn):
    with pg.fresh_schema(conn) as schema:
        pg.apply_v48(conn)
        pg.apply_v51_research(conn)
        pg.apply_v52_research(conn)
        pg.apply_v53_research_intake(conn)
        pg.apply_v54_research_review(conn)
        pg.apply_v55_research_freshness(conn)
        pg.apply_v56_research_claim_support(conn)
        pg.apply_v57_research_binding(conn)
        pg.apply_v58_research_scenario_input_evaluation(conn)
        pg.apply_v59_research_automation_roi_use(conn)
        try:
            yield schema
        finally:
            prior = pg._begin_autocommit(conn)
            conn.execute(f"SET ROLE {FUNCTION_OWNER}")
            conn.execute(f'DROP SCHEMA IF EXISTS "{OBJECT_SCHEMA}" CASCADE')
            conn.execute(f"SET ROLE {MIGRATION_OWNER}")
            pg._restore_autocommit(conn, prior)


@pytest.fixture(autouse=True)
def feature_enabled(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")


@pytest.fixture
def runtime(schema_v59):
    with pg.runtime_connection(schema_v59) as connection:
        yield connection


def _binding_set(conn, *, tag="snapshot", disposition="meets_contract"):
    project_id = pg.insert_project(conn, name=f"R1.6A {tag}")
    binding_set_id = f"set-{uuid.uuid4().hex}"
    records = []
    evidence_by_role = {}
    for index, role in enumerate(REQUIRED_ROLES):
        evidence = _seed_evidence(
            conn,
            tag=f"{tag}-{index}",
            project_id=project_id,
            role=role,
        )
        evidence_by_role[role] = evidence
        _review(conn, evidence, request_id=f"review-{tag}-{index}")
        _freshness(conn, evidence, request_id=f"fresh-{tag}-{index}")
        records.append(
            binding_service.record_consumer_input_binding(
                conn,
                _command(
                    evidence,
                    request_id=f"binding-{tag}-{index}",
                    consumer_contract=CONSUMER_CONTRACT,
                    consumer_contract_version=CONSUMER_CONTRACT_VERSION,
                    binding_set_id=binding_set_id,
                    input_key=role,
                    approved_calculation_input_id=evidence["frozen_input"],
                    policy_identifier=POLICY_IDENTIFIER,
                    policy_version=POLICY_VERSION,
                    policy_parameters_json=POLICY_PARAMETERS,
                    policy_fingerprint=POLICY_FINGERPRINT,
                    evaluator_version=EVALUATOR_VERSION,
                    freshness_as_of=NOW + timedelta(days=1),
                    consumer_disposition=disposition,
                    disposition_reasons=("inputs_observed",),
                ),
            )
        )
    return project_id, binding_set_id, tuple(records), evidence_by_role


def _snapshot_command(project_id, binding_set_id, records, *, request_id="snap-1"):
    return AutomationRoiInputSnapshotCreate(
        project_id=project_id,
        binding_set_id=binding_set_id,
        binding_record_ids=tuple(record.id for record in records),
        request_id=request_id,
        freshness_as_of=NOW + timedelta(days=1),
        evaluated_by="operator",
    )


def test_v59_identity_sequence_and_clean_reapply(conn, schema_v59):
    assert pg.V59_RESEARCH_AUTOMATION_ROI_USE_SQL.name == (
        "v59_research_evidence_automation_roi_input_snapshot.sql"
    )
    for table in TABLES:
        assert pg.table_exists(conn, OBJECT_SCHEMA, table)
    assert pg.table_exists(
        conn, schema_v59, "research_evidence_scenario_input_evaluation"
    )
    pg.apply_v59_research_automation_roi_use(conn)


def test_allocator_relation_uses_only_canonical_identifier(conn, schema_v59):
    relation_names = conn.execute(
        """
        SELECT relation.relname
        FROM pg_class relation
        JOIN pg_namespace namespace
          ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = %s
          AND relation.relname = ANY(%s)
        ORDER BY relation.relname
        """,
        (
            OBJECT_SCHEMA,
            [
                "automation_roi_input_snapshot_sequence_allocator",
                (
                    "research_evidence_automation_roi_input_snapshot_"
                    "sequence_allocator"
                ),
                (
                    "research_evidence_automation_roi_input_snapshot_"
                    "sequence_alloca"
                ),
            ],
        ),
    ).fetchall()
    assert relation_names == [
        ("automation_roi_input_snapshot_sequence_allocator",)
    ]


def test_v59_applies_from_canonical_migration_login(conn):
    with pg.fresh_schema(conn) as upstream_schema:
        pg.apply_v48(conn)
        pg.apply_v51_research(conn)
        pg.apply_v52_research(conn)
        pg.apply_v53_research_intake(conn)
        pg.apply_v54_research_review(conn)
        pg.apply_v55_research_freshness(conn)
        pg.apply_v56_research_claim_support(conn)
        pg.apply_v57_research_binding(conn)
        pg.apply_v58_research_scenario_input_evaluation(conn)
        try:
            with _migration_login(upstream_schema) as migration:
                role_contract = migration.execute(
                    """
                    SELECT rolname, rolinherit
                    FROM pg_roles
                    WHERE rolname = ANY(%s)
                    ORDER BY rolname
                    """,
                    (
                        [
                            FUNCTION_OWNER,
                            MIGRATION_OWNER,
                            RUNTIME_ROLE,
                        ],
                    ),
                ).fetchall()
                assert role_contract == [
                    (RUNTIME_ROLE, False),
                    (MIGRATION_OWNER, False),
                    (FUNCTION_OWNER, False),
                ]
                assert migration.execute(
                    """
                    SELECT pg_has_role(
                        current_user,
                        %s,
                        'SET'
                    )
                    """,
                    (FUNCTION_OWNER,),
                ).fetchone()[0]
                pg.apply_v59_research_automation_roi_use(migration)
        finally:
            prior = pg._begin_autocommit(conn)
            try:
                conn.execute(f"SET ROLE {FUNCTION_OWNER}")
                conn.execute(
                    f"DROP SCHEMA IF EXISTS {OBJECT_SCHEMA} CASCADE"
                )
                conn.execute(f"SET ROLE {MIGRATION_OWNER}")
            finally:
                pg._restore_autocommit(conn, prior)


def test_migration_owner_without_set_role_cannot_change_dedicated_acl(
    conn, schema_v59
):
    with _migration_login(schema_v59) as migration:
        with pytest.raises(Exception, match="permission denied"):
            migration.execute(
                f"""
                REVOKE SELECT ON TABLE {OBJECT_SCHEMA}.
                    research_evidence_automation_roi_input_snapshot
                FROM {RUNTIME_ROLE}
                """
            )
        migration.rollback()
    assert conn.execute(
        """
        SELECT has_table_privilege(
            %s,
            'research_evidence_automation_roi.'
            'research_evidence_automation_roi_input_snapshot',
            'SELECT'
        )
        """,
        (RUNTIME_ROLE,),
    ).fetchone()[0]


def test_v59_reapply_uses_oid_acl_probes_without_migration_schema_usage(
    conn, schema_v59
):
    with _migration_login(schema_v59) as migration:
        assert not migration.execute(
            """
            SELECT has_schema_privilege(
                current_user,
                %s,
                'USAGE'
            )
            """,
            (OBJECT_SCHEMA,),
        ).fetchone()[0]
        pg.apply_v59_research_automation_roi_use(migration)


def test_v59_owner_read_validation_context_and_restoration(conn, schema_v59):
    with _migration_login(schema_v59) as migration:
        assert migration.execute(
            "SELECT session_user, current_user"
        ).fetchone() == (MIGRATION_OWNER, MIGRATION_OWNER)
        migration.execute(f"SET ROLE {FUNCTION_OWNER}")
        assert migration.execute(
            "SELECT session_user, current_user"
        ).fetchone() == (MIGRATION_OWNER, FUNCTION_OWNER)
        migration.execute("RESET ROLE")
        assert migration.execute(
            "SELECT session_user, current_user"
        ).fetchone() == (MIGRATION_OWNER, MIGRATION_OWNER)
        pg.apply_v59_research_automation_roi_use(migration)


def test_v59_reapply_preserves_dedicated_acl_drift_after_rejection(
    conn, schema_v59
):
    conn.execute(
        f"""
        REVOKE SELECT ON TABLE {OBJECT_SCHEMA}.
            research_evidence_automation_roi_input_snapshot
        FROM {RUNTIME_ROLE}
        """
    )
    conn.commit()
    with _migration_login(schema_v59) as migration:
        with pytest.raises(Exception, match="runtime ACL drift"):
            pg.apply_v59_research_automation_roi_use(migration)
    assert not conn.execute(
        """
        SELECT has_table_privilege(
            %s,
            'research_evidence_automation_roi.'
            'research_evidence_automation_roi_input_snapshot',
            'SELECT'
        )
        """,
        (RUNTIME_ROLE,),
    ).fetchone()[0]


def test_v59_applies_without_owner_ambient_upstream_schema(conn):
    with pg.fresh_schema(conn) as upstream_schema:
        pg.apply_v48(conn)
        pg.apply_v51_research(conn)
        pg.apply_v52_research(conn)
        pg.apply_v53_research_intake(conn)
        pg.apply_v54_research_review(conn)
        pg.apply_v55_research_freshness(conn)
        pg.apply_v56_research_claim_support(conn)
        pg.apply_v57_research_binding(conn)
        pg.apply_v58_research_scenario_input_evaluation(conn)
        prior = pg._begin_autocommit(conn)
        try:
            conn.execute(
                f'REVOKE ALL ON SCHEMA "{upstream_schema}" '
                f"FROM {FUNCTION_OWNER}"
            )
            conn.execute(f'SET search_path TO "{upstream_schema}"')
            conn.execute(f"SET ROLE {FUNCTION_OWNER}")
            assert conn.execute(
                "SELECT pg_catalog.current_schema()"
            ).fetchone()[0] is None
            conn.execute(f"SET ROLE {MIGRATION_OWNER}")
        finally:
            pg._restore_autocommit(conn, prior)
        try:
            pg.apply_v59_research_automation_roi_use(conn)
        finally:
            prior = pg._begin_autocommit(conn)
            try:
                conn.execute(f"SET ROLE {FUNCTION_OWNER}")
                conn.execute(
                    f"DROP SCHEMA IF EXISTS {OBJECT_SCHEMA} CASCADE"
                )
                conn.execute(f"SET ROLE {MIGRATION_OWNER}")
            finally:
                pg._restore_autocommit(conn, prior)


def test_v59_reapply_rejects_missing_upstream_schema_anchor(
    conn, schema_v59
):
    prior = pg._begin_autocommit(conn)
    try:
        conn.execute(
            f"""
            ALTER TABLE "{schema_v59}".
                research_evidence_consumer_input_binding
            DROP CONSTRAINT fk_recib_calculation_input_role
            """
        )
    finally:
        pg._restore_autocommit(conn, prior)
    with pytest.raises(
        Exception,
        match="v59 requires exactly one validated upstream schema",
    ):
        pg.apply_v59_research_automation_roi_use(conn)


def test_v59_reapply_rejects_ambiguous_upstream_schema_anchor(
    conn, schema_v59
):
    duplicate_schema = f"duplicate_upstream_{uuid.uuid4().hex}"
    prior = pg._begin_autocommit(conn)
    try:
        conn.execute(f'CREATE SCHEMA "{duplicate_schema}"')
        conn.execute(
            f"""
            CREATE TABLE "{duplicate_schema}".approved_calculation_input (
                id uuid NOT NULL,
                input_role text NOT NULL,
                project_id uuid NOT NULL,
                UNIQUE (id, input_role, project_id)
            );
            CREATE TABLE "{duplicate_schema}".
                research_evidence_consumer_input_binding (
                approved_calculation_input_id uuid,
                input_key text,
                project_id uuid,
                CONSTRAINT fk_recib_calculation_input_role
                    FOREIGN KEY (
                        approved_calculation_input_id, input_key, project_id
                    )
                    REFERENCES "{duplicate_schema}".
                        approved_calculation_input(
                        id, input_role, project_id
                    )
            )
            """
        )
    finally:
        pg._restore_autocommit(conn, prior)
    try:
        with pytest.raises(
            Exception,
            match="v59 requires exactly one validated upstream schema",
        ):
            pg.apply_v59_research_automation_roi_use(conn)
    finally:
        prior = pg._begin_autocommit(conn)
        try:
            conn.execute(f'DROP SCHEMA "{duplicate_schema}" CASCADE')
        finally:
            pg._restore_autocommit(conn, prior)


def test_canonical_role_graph_and_dedicated_schema_acl(conn, schema_v59):
    roles = conn.execute(
        """
        SELECT rolname, rolcanlogin, rolinherit, rolsuper, rolcreaterole,
               rolcreatedb, rolreplication, rolbypassrls
        FROM pg_roles
        WHERE rolname = ANY(%s)
        ORDER BY rolname
        """,
        ([FUNCTION_OWNER, MIGRATION_OWNER, RUNTIME_ROLE],),
    ).fetchall()
    assert roles == sorted(
        (
            (FUNCTION_OWNER, False, False, False, False, False, False, False),
            (MIGRATION_OWNER, True, False, False, False, False, False, False),
            (RUNTIME_ROLE, True, False, False, False, False, False, False),
        )
    )
    memberships = conn.execute(
        """
        SELECT granted_role.rolname, member_role.rolname,
               membership.admin_option,
               to_jsonb(membership)->>'inherit_option',
               to_jsonb(membership)->>'set_option'
        FROM pg_auth_members membership
        JOIN pg_roles granted_role ON granted_role.oid = membership.roleid
        JOIN pg_roles member_role ON member_role.oid = membership.member
        WHERE granted_role.rolname = ANY(%s)
           OR member_role.rolname = ANY(%s)
        ORDER BY granted_role.rolname, member_role.rolname
        """,
        (
            [FUNCTION_OWNER, MIGRATION_OWNER, RUNTIME_ROLE],
            [FUNCTION_OWNER, MIGRATION_OWNER, RUNTIME_ROLE],
        ),
    ).fetchall()
    options_supported = conn.execute(
        """
        SELECT count(*) = 2
        FROM pg_attribute
        WHERE attrelid = 'pg_catalog.pg_auth_members'::regclass
          AND attname IN ('inherit_option', 'set_option')
          AND NOT attisdropped
        """
    ).fetchone()[0]
    assert memberships == [
        (
            FUNCTION_OWNER,
            MIGRATION_OWNER,
            False,
            "false" if options_supported else None,
            "true" if options_supported else None,
        )
    ]

    schema_acl = conn.execute(
        """
        SELECT COALESCE(grantee.rolname, 'PUBLIC'),
               grantor.rolname, acl.privilege_type, acl.is_grantable
        FROM pg_namespace namespace
        JOIN pg_roles owner_role ON owner_role.oid = namespace.nspowner
        CROSS JOIN LATERAL aclexplode(
            COALESCE(namespace.nspacl, acldefault('n', namespace.nspowner))
        ) acl
        LEFT JOIN pg_roles grantee ON grantee.oid = acl.grantee
        LEFT JOIN pg_roles grantor ON grantor.oid = acl.grantor
        WHERE namespace.nspname = %s
          AND owner_role.rolname = %s
        ORDER BY 1, 3
        """,
        (OBJECT_SCHEMA, FUNCTION_OWNER),
    ).fetchall()
    assert schema_acl == [
        (RUNTIME_ROLE, FUNCTION_OWNER, "USAGE", False),
        (FUNCTION_OWNER, FUNCTION_OWNER, "CREATE", False),
        (FUNCTION_OWNER, FUNCTION_OWNER, "USAGE", False),
    ]
    assert _default_acl_inventory(conn) == [
        (
            FUNCTION_OWNER,
            "GLOBAL",
            "f",
            FUNCTION_OWNER,
            FUNCTION_OWNER,
            "EXECUTE",
            False,
        )
    ]


def test_upstream_project_fk_acl_is_exact_and_column_scoped(
    conn, schema_v59
):
    owner_schema_acl = conn.execute(
        """
        SELECT acl.privilege_type, acl.is_grantable
        FROM pg_namespace namespace
        CROSS JOIN LATERAL aclexplode(namespace.nspacl) acl
        JOIN pg_roles grantee ON grantee.oid = acl.grantee
        WHERE namespace.nspname = %s
          AND grantee.rolname = %s
        ORDER BY acl.privilege_type
        """,
        (schema_v59, FUNCTION_OWNER),
    ).fetchall()
    assert owner_schema_acl == [("USAGE", False)]

    project_table_acl = conn.execute(
        """
        SELECT COALESCE(grantee.rolname, 'PUBLIC'),
               acl.privilege_type, acl.is_grantable
        FROM pg_class relation
        JOIN pg_namespace namespace
          ON namespace.oid = relation.relnamespace
        CROSS JOIN LATERAL aclexplode(relation.relacl) acl
        LEFT JOIN pg_roles grantee ON grantee.oid = acl.grantee
        WHERE namespace.nspname = %s
          AND relation.relname = 'projects'
          AND (
              acl.grantee = 0
              OR grantee.rolname = ANY(%s)
          )
        ORDER BY 1, 2
        """,
        (
            schema_v59,
            [FUNCTION_OWNER, RUNTIME_ROLE],
        ),
    ).fetchall()
    assert project_table_acl == []

    project_column_acl = conn.execute(
        """
        SELECT attribute.attname,
               COALESCE(grantee.rolname, 'PUBLIC'),
               acl.privilege_type, acl.is_grantable
        FROM pg_class relation
        JOIN pg_namespace namespace
          ON namespace.oid = relation.relnamespace
        JOIN pg_attribute attribute
          ON attribute.attrelid = relation.oid
         AND attribute.attnum > 0
         AND NOT attribute.attisdropped
        CROSS JOIN LATERAL aclexplode(attribute.attacl) acl
        LEFT JOIN pg_roles grantee ON grantee.oid = acl.grantee
        WHERE namespace.nspname = %s
          AND relation.relname = 'projects'
          AND (
              acl.grantee = 0
              OR grantee.rolname = ANY(%s)
          )
        ORDER BY 1, 2, 3
        """,
        (
            schema_v59,
            [FUNCTION_OWNER, RUNTIME_ROLE],
        ),
    ).fetchall()
    assert project_column_acl == [
        ("id", FUNCTION_OWNER, "REFERENCES", False)
    ]

    owner_upstream_table_acl = conn.execute(
        """
        SELECT relation.relname, acl.privilege_type
        FROM pg_class relation
        JOIN pg_namespace namespace
          ON namespace.oid = relation.relnamespace
        CROSS JOIN LATERAL aclexplode(relation.relacl) acl
        JOIN pg_roles grantee ON grantee.oid = acl.grantee
        WHERE namespace.nspname = %s
          AND grantee.rolname = %s
          AND relation.relname = ANY(%s)
        ORDER BY relation.relname, acl.privilege_type
        """,
        (
            schema_v59,
            FUNCTION_OWNER,
            [
                "research_evidence_consumer_input_binding",
                "approved_calculation_input",
                "research_evidence_consumer_input_binding_sequence_allocator",
            ],
        ),
    ).fetchall()
    assert owner_upstream_table_acl == [
        ("approved_calculation_input", "SELECT"),
        ("research_evidence_consumer_input_binding", "SELECT"),
        (
            "research_evidence_consumer_input_binding_sequence_allocator",
            "SELECT",
        ),
        (
            "research_evidence_consumer_input_binding_sequence_allocator",
            "UPDATE",
        ),
    ]

    projects = f'"{schema_v59}".projects'
    assert not conn.execute(
        """
        SELECT has_schema_privilege(%s, %s, 'CREATE')
        """,
        (FUNCTION_OWNER, schema_v59),
    ).fetchone()[0]
    assert not conn.execute(
        """
        SELECT has_table_privilege(
            %s, %s, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'
        )
        """,
        (FUNCTION_OWNER, projects),
    ).fetchone()[0]
    assert conn.execute(
        """
        SELECT has_column_privilege(%s, %s, 'id', 'REFERENCES')
        """,
        (FUNCTION_OWNER, projects),
    ).fetchone()[0]


def test_complete_explicit_set_persists_policy_provenance(
    conn, schema_v59, runtime
):
    project, binding_set, records, _ = _binding_set(conn)
    conn.commit()
    snapshot = record_automation_roi_input_snapshot(
        runtime, _snapshot_command(project, binding_set, records)
    )
    assert snapshot.snapshot_sequence == 1
    assert snapshot.completeness_status == "complete"
    assert snapshot.policy_evaluation_status == "satisfies"
    assert snapshot.evaluation_reasons == ("policy_satisfied",)
    assert snapshot.policy_fingerprint == POLICY_FINGERPRINT
    assert {item.input_role for item in snapshot.bindings} == set(REQUIRED_ROLES)
    assert {item.binding_record_id for item in snapshot.bindings} == {
        record.id for record in records
    }


@pytest.mark.parametrize(
    ("disposition", "expected_status"),
    [
        ("qualified", "qualified"),
        ("indeterminate", "indeterminate"),
    ],
)
def test_database_authoritative_non_satisfying_statuses_persist(
    conn, schema_v59, runtime, disposition, expected_status
):
    project, binding_set, records, _ = _binding_set(
        conn, tag=f"status-{disposition}", disposition=disposition
    )
    conn.commit()
    snapshot = record_automation_roi_input_snapshot(
        runtime, _snapshot_command(project, binding_set, records)
    )
    assert snapshot.policy_evaluation_status == expected_status


def test_retry_and_server_owned_sequence_predecessor(
    conn, schema_v59, runtime
):
    project, binding_set, records, _ = _binding_set(conn, tag="sequence")
    conn.commit()
    command = _snapshot_command(project, binding_set, records)
    first = record_automation_roi_input_snapshot(runtime, command)
    assert record_automation_roi_input_snapshot(runtime, command).id == first.id
    second = record_automation_roi_input_snapshot(
        runtime,
        _snapshot_command(
            project, binding_set, records, request_id="snap-sequence-2"
        ),
    )
    assert second.snapshot_sequence == 2
    assert second.supersedes_snapshot_id == first.id


def test_owner_definer_search_path_and_exact_acl_contract(conn, schema_v59):
    owners = conn.execute(
        """
        SELECT c.relname, owner_role.rolname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_roles owner_role ON owner_role.oid = c.relowner
        WHERE n.nspname = %s AND c.relname = ANY(%s)
        ORDER BY c.relname
        """,
        (OBJECT_SCHEMA, list(TABLES)),
    ).fetchall()
    assert owners == sorted((name, FUNCTION_OWNER) for name in TABLES)

    relation_state = conn.execute(
        """
        SELECT relation.relname, relation.relkind, relation.relpersistence,
               relation.relrowsecurity, relation.relforcerowsecurity,
               relation.reloptions, relation.relreplident,
               relation.relispartition,
               (
                   SELECT count(*)
                   FROM pg_catalog.pg_inherits inheritance
                   WHERE inheritance.inhrelid = relation.oid
               ),
               (
                   SELECT count(*)
                   FROM pg_catalog.pg_inherits inheritance
                   WHERE inheritance.inhparent = relation.oid
               )
        FROM pg_catalog.pg_class relation
        JOIN pg_catalog.pg_namespace namespace
          ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = %s AND relation.relname = ANY(%s)
        ORDER BY relation.relname
        """,
        (OBJECT_SCHEMA, list(TABLES)),
    ).fetchall()
    assert relation_state == sorted(
        (name, "r", "p", False, False, None, "d", False, 0, 0)
        for name in TABLES
    )

    functions = conn.execute(
        """
        SELECT function_info.proname, owner_role.rolname,
               function_info.prosecdef, function_info.proconfig,
               function_info.prokind, function_info.provolatile,
               function_info.proisstrict, function_info.proleakproof,
               function_info.proparallel, function_info.procost,
               function_info.prorows,
               COALESCE(
                   pg_catalog.to_jsonb(function_info)->>'prosupport',
                   '-'
               ),
               function_info.proretset, function_info.provariadic,
               function_info.pronargdefaults,
               (
                   pg_catalog.to_jsonb(function_info)->>'prosqlbody'
                   IS NOT NULL
               )
        FROM pg_catalog.pg_proc function_info
        JOIN pg_catalog.pg_namespace namespace
          ON namespace.oid = function_info.pronamespace
        JOIN pg_catalog.pg_roles owner_role
          ON owner_role.oid = function_info.proowner
        WHERE namespace.nspname = %s
          AND function_info.proname = ANY(%s)
        ORDER BY function_info.proname
        """,
        (OBJECT_SCHEMA, list(FUNCTIONS)),
    ).fetchall()
    assert len(functions) == 6
    for (
        name,
        owner,
        security_definer,
        configuration,
        function_kind,
        volatility,
        is_strict,
        is_leakproof,
        parallel_safety,
        execution_cost,
        row_estimate,
        support_function,
        returns_set,
        variadic_type,
        argument_defaults,
        sql_body_present,
    ) in functions:
        assert owner == FUNCTION_OWNER
        assert security_definer is (
            name
            in {
                "research_evidence_evaluate_automation_roi_bindings",
                "research_evidence_assert_automation_roi_snapshot",
                "research_evidence_create_automation_roi_snapshot",
            }
        )
        expected_path = (
            "search_path=pg_catalog, research_evidence_automation_roi, pg_temp"
        )
        assert configuration == [expected_path]
        assert function_kind == "f"
        assert volatility == "v"
        assert is_strict is False
        assert is_leakproof is False
        assert parallel_safety == "u"
        assert execution_cost == 100
        assert row_estimate == (
            1000
            if name == "research_evidence_evaluate_automation_roi_bindings"
            else 0
        )
        assert support_function == "-"
        assert returns_set is (
            name == "research_evidence_evaluate_automation_roi_bindings"
        )
        assert variadic_type == 0
        assert argument_defaults == 0
        assert sql_body_present is False

    for table in TABLES[:2]:
        assert conn.execute(
            "SELECT has_table_privilege(%s, %s, 'SELECT')",
            (RUNTIME_ROLE, f'"{OBJECT_SCHEMA}".{table}'),
        ).fetchone()[0]
        assert not conn.execute(
            "SELECT has_table_privilege(%s, %s, 'INSERT,UPDATE,DELETE,TRUNCATE')",
            (RUNTIME_ROLE, f'"{OBJECT_SCHEMA}".{table}'),
        ).fetchone()[0]
    assert not conn.execute(
        "SELECT has_table_privilege(%s, %s, 'SELECT,UPDATE')",
        (
            RUNTIME_ROLE,
            f'"{OBJECT_SCHEMA}".'
            "automation_roi_input_snapshot_sequence_allocator",
        ),
    ).fetchone()[0]
    assert not conn.execute(
        "SELECT has_schema_privilege(%s, %s, 'CREATE')",
        (RUNTIME_ROLE, OBJECT_SCHEMA),
    ).fetchone()[0]


def test_runtime_can_use_only_controlled_entry_function(
    conn, schema_v59, runtime
):
    project, binding_set, records, _ = _binding_set(conn, tag="runtime")
    conn.commit()
    created = record_automation_roi_input_snapshot(
        runtime, _snapshot_command(project, binding_set, records)
    )
    runtime.commit()
    assert created.policy_evaluation_status == "satisfies"
    assert runtime.execute(
        """
        SELECT count(*)
        FROM research_evidence_automation_roi.
            research_evidence_automation_roi_input_snapshot
        WHERE id = %s
        """,
        (created.id,),
    ).fetchone()[0] == 1
    for statement in (
        """
        INSERT INTO research_evidence_automation_roi.
            automation_roi_input_snapshot_sequence_allocator
            (project_id, consumer_contract, binding_set_id, last_sequence)
        VALUES ('00000000-0000-0000-0000-000000000001',
                'deterministic_calculation', 'forbidden', 0)
        """,
        """
        SELECT research_evidence_automation_roi.
            research_evidence_validate_automation_roi_snapshot(NULL)
        """,
        """
        SELECT *
        FROM research_evidence_automation_roi.
            automation_roi_input_snapshot_sequence_allocator
        """,
    ):
        with pytest.raises(Exception):
            runtime.execute(statement)
        runtime.rollback()


def test_runtime_entry_ignores_session_catalog_shadows(
    conn, schema_v59, runtime
):
    project, binding_set, records, _ = _binding_set(
        conn, tag="catalog-shadow"
    )
    conn.commit()
    shadow_relations = ("pg_constraint", "pg_class", "pg_namespace")
    try:
        assert runtime.execute(
            """
            SELECT pg_catalog.has_database_privilege(
                current_user, current_database(), 'TEMP'
            )
            """
        ).fetchone()[0]
        runtime.execute(
            """
            CREATE TEMP TABLE pg_constraint (
                conrelid pg_catalog.oid,
                conname pg_catalog.name,
                confrelid pg_catalog.oid
            );
            CREATE TEMP TABLE pg_class (
                oid pg_catalog.oid,
                relnamespace pg_catalog.oid
            );
            CREATE TEMP TABLE pg_namespace (
                oid pg_catalog.oid,
                nspname pg_catalog.name
            );
            INSERT INTO pg_temp.pg_constraint
                (conrelid, conname, confrelid)
            VALUES (
                (
                    'research_evidence_automation_roi.'
                    || 'research_evidence_automation_roi_input_snapshot_binding'
                )
                    ::pg_catalog.regclass,
                'fk_rearoisb_binding_scope',
                900001
            );
            INSERT INTO pg_temp.pg_class (oid, relnamespace)
            VALUES (900001, 900002);
            INSERT INTO pg_temp.pg_namespace (oid, nspname)
            VALUES (900002, 'shadow_schema_must_not_be_used');
            """
        )
        for relation in shadow_relations:
            runtime.execute(
                f"GRANT SELECT ON TABLE pg_temp.{relation} TO {FUNCTION_OWNER}"
            )
        assert runtime.execute(
            """
            SELECT pg_catalog.to_regclass('pg_constraint')
                   = pg_catalog.to_regclass('pg_temp.pg_constraint')
            """
        ).fetchone()[0]

        created = record_automation_roi_input_snapshot(
            runtime, _snapshot_command(project, binding_set, records)
        )
        runtime.commit()
        assert created.policy_evaluation_status == "satisfies"
    finally:
        with contextlib.suppress(Exception):
            runtime.rollback()
        for relation in shadow_relations:
            with contextlib.suppress(Exception):
                runtime.rollback()
                runtime.execute(
                    f"REVOKE SELECT ON TABLE pg_temp.{relation} "
                    f"FROM {FUNCTION_OWNER}"
                )
                runtime.commit()
            with contextlib.suppress(Exception):
                runtime.rollback()
                runtime.execute(
                    f"DROP TABLE IF EXISTS pg_temp.{relation}"
                )
                runtime.commit()
        runtime.rollback()
        remaining = runtime.execute(
            """
            SELECT pg_catalog.to_regclass('pg_temp.pg_constraint'),
                   pg_catalog.to_regclass('pg_temp.pg_class'),
                   pg_catalog.to_regclass('pg_temp.pg_namespace')
            """
        ).fetchone()
        permanent_shadows = runtime.execute(
            """
            SELECT count(*)
            FROM pg_catalog.pg_class relation
            JOIN pg_catalog.pg_namespace namespace
              ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname =
                      'research_evidence_automation_roi'
              AND relation.relname = ANY(%s)
            """,
            (list(shadow_relations),),
        ).fetchone()[0]
        assert remaining == (None, None, None)
        assert permanent_shadows == 0


def test_append_only_and_owner_equality_guards_remain_closed(
    conn, schema_v59, runtime
):
    project, binding_set, records, _ = _binding_set(conn, tag="append-only")
    conn.commit()
    snapshot = record_automation_roi_input_snapshot(
        runtime, _snapshot_command(project, binding_set, records)
    )
    runtime.commit()
    with pytest.raises(Exception, match="append-only"):
        conn.execute(
            """
            UPDATE research_evidence_automation_roi.
                research_evidence_automation_roi_input_snapshot
            SET evaluated_by = 'changed' WHERE id = %s
            """,
            (snapshot.id,),
        )
    conn.rollback()

    with pytest.raises(Exception):
        runtime.execute(
            """
            INSERT INTO research_evidence_automation_roi.
                research_evidence_automation_roi_input_snapshot
                (project_id) VALUES (%s)
            """,
            (project,),
        )
    runtime.rollback()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            """
            ALTER FUNCTION research_evidence_automation_roi.
                research_evidence_create_automation_roi_snapshot(
                uuid, text, uuid[], text, timestamptz, text
            ) RESET search_path
            """,
            "divergent functions",
        ),
        (
            """
            GRANT EXECUTE ON FUNCTION research_evidence_automation_roi.
                research_evidence_validate_automation_roi_snapshot(uuid)
            TO workflow_automation_roi_runtime
            """,
            "privilege drift",
        ),
        (
            """
            GRANT UPDATE ON research_evidence_automation_roi.
                research_evidence_automation_roi_input_snapshot
            TO workflow_automation_roi_runtime
            """,
            "privilege drift",
        ),
    ],
)
def test_reapply_rejects_security_contract_drift(
    conn, schema_v59, mutation, message
):
    conn.execute(f"SET ROLE {FUNCTION_OWNER}")
    conn.execute(mutation)
    conn.commit()
    conn.execute(f"SET ROLE {MIGRATION_OWNER}")
    with pytest.raises(Exception, match=message):
        pg.apply_v59_research_automation_roi_use(conn)


def test_reapply_rejects_table_owner_drift(conn, schema_v59):
    conn.execute("RESET ROLE")
    conn.execute(
        """
        ALTER TABLE research_evidence_automation_roi.
            research_evidence_automation_roi_input_snapshot
        OWNER TO workflow_migration_owner
        """
    )
    conn.commit()
    conn.execute(f"SET ROLE {MIGRATION_OWNER}")
    with pytest.raises(Exception, match="divergent table owners"):
        pg.apply_v59_research_automation_roi_use(conn)


def test_reapply_rejects_function_owner_drift(conn, schema_v59):
    conn.execute("RESET ROLE")
    conn.execute(
        """
        ALTER FUNCTION research_evidence_automation_roi.
            research_evidence_create_automation_roi_snapshot(
            uuid, text, uuid[], text, timestamptz, text
        ) OWNER TO workflow_migration_owner
        """
    )
    conn.commit()
    conn.execute(f"SET ROLE {MIGRATION_OWNER}")
    with pytest.raises(Exception, match="divergent functions"):
        pg.apply_v59_research_automation_roi_use(conn)


def test_v59_reapply_rejects_function_body_drift_before_replacement(
    conn, schema_v59
):
    conn.execute(f"SET ROLE {FUNCTION_OWNER}")
    conn.execute(
        """
        CREATE OR REPLACE FUNCTION research_evidence_automation_roi.
        research_evidence_create_automation_roi_snapshot(
            p_project_id uuid,
            p_binding_set_id text,
            p_binding_record_ids uuid[],
            p_request_id text,
            p_freshness_as_of timestamptz,
            p_evaluated_by text
        )
        RETURNS uuid
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, research_evidence_automation_roi, pg_temp
        AS $intentional_body_drift$
        BEGIN
            RAISE EXCEPTION 'intentional_body_drift';
        END;
        $intentional_body_drift$
        """
    )
    conn.commit()
    conn.execute(f"SET ROLE {MIGRATION_OWNER}")
    with pytest.raises(Exception, match="divergent functions"):
        pg.apply_v59_research_automation_roi_use(conn)
    installed_source = conn.execute(
        """
        SELECT function_info.prosrc
        FROM pg_catalog.pg_proc function_info
        JOIN pg_catalog.pg_namespace namespace
          ON namespace.oid = function_info.pronamespace
        WHERE namespace.nspname = %s
          AND function_info.proname =
              'research_evidence_create_automation_roi_snapshot'
          AND pg_catalog.oidvectortypes(function_info.proargtypes) =
              'uuid, text, uuid[], text, timestamp with time zone, text'
        """,
        (OBJECT_SCHEMA,),
    ).fetchone()[0]
    assert "intentional_body_drift" in installed_source


@pytest.mark.parametrize(
    "mutation",
    [
        """
        ALTER FUNCTION research_evidence_automation_roi.
            research_evidence_create_automation_roi_snapshot(
                uuid, text, uuid[], text, timestamptz, text
            ) IMMUTABLE
        """,
        """
        ALTER FUNCTION research_evidence_automation_roi.
            research_evidence_create_automation_roi_snapshot(
                uuid, text, uuid[], text, timestamptz, text
            ) STRICT
        """,
        """
        ALTER FUNCTION research_evidence_automation_roi.
            research_evidence_create_automation_roi_snapshot(
                uuid, text, uuid[], text, timestamptz, text
            ) LEAKPROOF
        """,
        """
        ALTER FUNCTION research_evidence_automation_roi.
            research_evidence_create_automation_roi_snapshot(
                uuid, text, uuid[], text, timestamptz, text
            ) PARALLEL SAFE
        """,
        """
        ALTER FUNCTION research_evidence_automation_roi.
            research_evidence_create_automation_roi_snapshot(
                uuid, text, uuid[], text, timestamptz, text
            ) COST 7
        """,
        """
        ALTER FUNCTION research_evidence_automation_roi.
            research_evidence_evaluate_automation_roi_bindings(
                uuid, text, uuid[], timestamptz, timestamptz
            ) ROWS 7
        """,
        """
        ALTER FUNCTION research_evidence_automation_roi.
            research_evidence_create_automation_roi_snapshot(
                uuid, text, uuid[], text, timestamptz, text
            ) SUPPORT pg_catalog.text_starts_with_support
        """,
    ],
)
def test_v59_reapply_rejects_function_semantic_attribute_drift(
    conn, schema_v59, mutation
):
    conn.execute("RESET ROLE")
    conn.execute(mutation)
    conn.commit()
    conn.execute(f"SET ROLE {MIGRATION_OWNER}")
    with pytest.raises(Exception, match="divergent functions"):
        pg.apply_v59_research_automation_roi_use(conn)


@pytest.mark.parametrize(
    "catalog_assignment",
    [
        "proretset = NOT function_info.proretset",
        "provariadic = 'pg_catalog.text'::pg_catalog.regtype::pg_catalog.oid",
        "pronargdefaults = function_info.pronargdefaults + 1",
    ],
)
def test_v59_reapply_rejects_function_signature_metadata_drift(
    conn, schema_v59, catalog_assignment
):
    conn.execute("RESET ROLE")
    result = conn.execute(
        f"""
        UPDATE pg_catalog.pg_proc function_info
        SET {catalog_assignment}
        FROM pg_catalog.pg_namespace namespace
        WHERE namespace.oid = function_info.pronamespace
          AND namespace.nspname = %s
          AND function_info.proname =
              'research_evidence_create_automation_roi_snapshot'
          AND pg_catalog.oidvectortypes(function_info.proargtypes) =
              'uuid, text, uuid[], text, timestamp with time zone, text'
        """,
        (OBJECT_SCHEMA,),
    )
    assert result.rowcount == 1
    conn.commit()
    conn.execute(f"SET ROLE {MIGRATION_OWNER}")
    with pytest.raises(Exception, match="divergent functions"):
        pg.apply_v59_research_automation_roi_use(conn)


def test_public_has_no_r1_6a_object_privileges(conn, schema_v59):
    public_rows = conn.execute(
        """
        SELECT c.relname, acl.privilege_type
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN LATERAL aclexplode(
            COALESCE(c.relacl, acldefault('r', c.relowner))
        ) acl
        WHERE n.nspname = %s
          AND c.relname = ANY(%s)
          AND acl.grantee = 0
        UNION ALL
        SELECT p.proname, acl.privilege_type
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        CROSS JOIN LATERAL aclexplode(
            COALESCE(p.proacl, acldefault('f', p.proowner))
        ) acl
        WHERE n.nspname = %s
          AND p.proname = ANY(%s)
          AND acl.grantee = 0
        """,
        (OBJECT_SCHEMA, list(TABLES), OBJECT_SCHEMA, list(FUNCTIONS)),
    ).fetchall()
    assert public_rows == []
    assert not conn.execute(
        """
        SELECT has_schema_privilege('public', %s, 'USAGE')
            OR has_schema_privilege('public', %s, 'CREATE')
        """,
        (OBJECT_SCHEMA, OBJECT_SCHEMA),
    ).fetchone()[0]


@pytest.mark.parametrize("table_name", TABLES)
def test_v59_reapply_rejects_extra_active_column_before_repair(
    conn, schema_v59, table_name
):
    conn.execute(f"SET ROLE {FUNCTION_OWNER}")
    conn.execute(
        f"""
        ALTER TABLE {OBJECT_SCHEMA}.{table_name}
        ADD COLUMN unexpected_reapply_column text
        """
    )
    conn.commit()
    conn.execute(f"SET ROLE {MIGRATION_OWNER}")
    with pytest.raises(Exception, match="divergent column count"):
        pg.apply_v59_research_automation_roi_use(conn)
    conn.rollback()
    assert conn.execute(
        """
        SELECT count(*) = 1
        FROM pg_attribute attribute
        JOIN pg_class relation ON relation.oid = attribute.attrelid
        JOIN pg_namespace namespace
          ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = %s
          AND relation.relname = %s
          AND attribute.attname = 'unexpected_reapply_column'
          AND attribute.attnum > 0
          AND NOT attribute.attisdropped
        """,
        (OBJECT_SCHEMA, table_name),
    ).fetchone()[0]
    conn.execute(f"SET ROLE {FUNCTION_OWNER}")
    conn.execute(
        f"""
        ALTER TABLE {OBJECT_SCHEMA}.{table_name}
        DROP COLUMN unexpected_reapply_column
        """
    )
    conn.commit()
    conn.execute(f"SET ROLE {MIGRATION_OWNER}")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            """
            ALTER TABLE research_evidence_automation_roi.
                research_evidence_automation_roi_input_snapshot
            DROP CONSTRAINT ck_rearois_fingerprint
            """,
            "divergent checks ck_rearois_fingerprint",
        ),
        (
            """
            ALTER TABLE research_evidence_automation_roi.
                research_evidence_automation_roi_input_snapshot
            ADD CONSTRAINT ck_rearois_unexpected CHECK (true)
            """,
            "divergent check inventory",
        ),
        (
            """
            ALTER TABLE research_evidence_automation_roi.
                research_evidence_automation_roi_input_snapshot
            DROP CONSTRAINT ck_rearois_fingerprint;
            ALTER TABLE research_evidence_automation_roi.
                research_evidence_automation_roi_input_snapshot_binding
            ADD CONSTRAINT ck_rearois_fingerprint CHECK (true)
            """,
            "divergent checks ck_rearois_fingerprint",
        ),
        (
            """
            ALTER TABLE research_evidence_automation_roi.
                research_evidence_automation_roi_input_snapshot
            DROP CONSTRAINT ck_rearois_fingerprint;
            ALTER TABLE research_evidence_automation_roi.
                research_evidence_automation_roi_input_snapshot
            ADD CONSTRAINT ck_rearois_fingerprint
                CHECK (policy_fingerprint = 'forged')
            """,
            "divergent checks ck_rearois_fingerprint",
        ),
        (
            """
            ALTER TABLE research_evidence_automation_roi.
                research_evidence_automation_roi_input_snapshot
            DROP CONSTRAINT ck_rearois_fingerprint;
            ALTER TABLE research_evidence_automation_roi.
                research_evidence_automation_roi_input_snapshot
            ADD CONSTRAINT ck_rearois_fingerprint
                CHECK (policy_fingerprint ~ '^[0-9a-f]{64}$') NOT VALID
            """,
            "divergent checks ck_rearois_fingerprint",
        ),
    ],
)
def test_v59_reapply_rejects_check_drift_without_repair(
    conn, schema_v59, mutation, message
):
    conn.execute(f"SET ROLE {FUNCTION_OWNER}")
    conn.execute(mutation)
    conn.commit()
    conn.execute(f"SET ROLE {MIGRATION_OWNER}")
    with pytest.raises(Exception, match=message):
        pg.apply_v59_research_automation_roi_use(conn)
    conn.rollback()
    check_count = conn.execute(
        """
        SELECT count(*)
        FROM pg_constraint constraint_info
        JOIN pg_class relation
          ON relation.oid = constraint_info.conrelid
        JOIN pg_namespace namespace
          ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = %s
          AND constraint_info.contype = 'c'
        """,
        (OBJECT_SCHEMA,),
    ).fetchone()[0]
    fingerprint_rows = conn.execute(
        """
        SELECT relation.relname,
               constraint_info.convalidated,
               pg_get_expr(
                   constraint_info.conbin,
                   constraint_info.conrelid,
                   true
               )
        FROM pg_constraint constraint_info
        JOIN pg_class relation
          ON relation.oid = constraint_info.conrelid
        JOIN pg_namespace namespace
          ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = %s
          AND constraint_info.conname = 'ck_rearois_fingerprint'
        """,
        (OBJECT_SCHEMA,),
    ).fetchall()
    expected_fingerprint = [
        (
            "research_evidence_automation_roi_input_snapshot",
            True,
            "policy_fingerprint ~ '^[0-9a-f]{64}$'::text",
        )
    ]
    assert check_count != 9 or fingerprint_rows != expected_fingerprint


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            """
            ALTER TABLE research_evidence_automation_roi.
                research_evidence_automation_roi_input_snapshot
            ALTER COLUMN evaluated_by DROP NOT NULL
            """,
            "divergent columns",
        ),
        (
            """
            ALTER TABLE research_evidence_automation_roi.
                research_evidence_automation_roi_input_snapshot
            ALTER COLUMN evaluated_by SET DEFAULT 'forged'
            """,
            "divergent columns",
        ),
        (
            """
            ALTER TABLE research_evidence_automation_roi.
                research_evidence_automation_roi_input_snapshot
            DROP CONSTRAINT uq_rearois_scope_request
            """,
            "divergent keys",
        ),
        (
            """
            ALTER TABLE research_evidence_automation_roi.
                research_evidence_automation_roi_input_snapshot_binding
            DROP CONSTRAINT fk_rearoisb_binding_scope
            """,
            "divergent foreign keys",
        ),
        (
            """
            ALTER TABLE research_evidence_automation_roi.
                research_evidence_automation_roi_input_snapshot
            DROP CONSTRAINT ck_rearois_status
            """,
            "divergent checks",
        ),
        (
            """
            DROP INDEX research_evidence_automation_roi.idx_rearoisb_binding
            """,
            "divergent indexes",
        ),
        (
            """
            ALTER TABLE research_evidence_automation_roi.
                research_evidence_automation_roi_input_snapshot
            DISABLE TRIGGER trg_rearois_prepare_insert
            """,
            "divergent triggers",
        ),
        (
            """
            ALTER FUNCTION research_evidence_automation_roi.
                research_evidence_create_automation_roi_snapshot(
                    uuid, text, uuid[], text, timestamptz, text
                ) SECURITY INVOKER
            """,
            "divergent functions",
        ),
    ],
)
def test_v59_reapply_rejects_structural_and_function_drift(
    conn, schema_v59, mutation, message
):
    conn.execute(f"SET ROLE {FUNCTION_OWNER}")
    conn.execute(mutation)
    conn.commit()
    conn.execute(f"SET ROLE {MIGRATION_OWNER}")
    with pytest.raises(Exception, match=message):
        pg.apply_v59_research_automation_roi_use(conn)


@pytest.mark.parametrize(
    "mutation",
    [
        """
        ALTER TABLE research_evidence_automation_roi.
            research_evidence_automation_roi_input_snapshot
        ENABLE ROW LEVEL SECURITY
        """,
        """
        ALTER TABLE research_evidence_automation_roi.
            research_evidence_automation_roi_input_snapshot
        FORCE ROW LEVEL SECURITY
        """,
        """
        ALTER TABLE research_evidence_automation_roi.
            research_evidence_automation_roi_input_snapshot
        REPLICA IDENTITY FULL
        """,
        """
        ALTER TABLE research_evidence_automation_roi.
            research_evidence_automation_roi_input_snapshot
        SET (fillfactor = 70)
        """,
        """
        ALTER TABLE research_evidence_automation_roi.
            automation_roi_input_snapshot_sequence_allocator
        SET UNLOGGED
        """,
        """
        CREATE TABLE research_evidence_automation_roi.
            unexpected_rearois_inheritance_child ()
        INHERITS (
            research_evidence_automation_roi.
                research_evidence_automation_roi_input_snapshot
        )
        """,
    ],
)
def test_v59_reapply_rejects_relation_state_drift(
    conn, schema_v59, mutation
):
    conn.execute(f"SET ROLE {FUNCTION_OWNER}")
    conn.execute(mutation)
    conn.commit()
    conn.execute(f"SET ROLE {MIGRATION_OWNER}")
    with pytest.raises(Exception, match="divergent relation state"):
        pg.apply_v59_research_automation_roi_use(conn)


@pytest.mark.parametrize(
    "mutation",
    [
        """
        CREATE INDEX idx_rearois_unexpected_partial
        ON research_evidence_automation_roi.
            research_evidence_automation_roi_input_snapshot(request_id)
        WHERE snapshot_sequence > 0
        """,
        """
        CREATE INDEX idx_rearois_unexpected_expression
        ON research_evidence_automation_roi.
            research_evidence_automation_roi_input_snapshot(
                (lower(request_id))
            )
        """,
        """
        CREATE INDEX idx_rearois_unexpected_hash
        ON research_evidence_automation_roi.
            research_evidence_automation_roi_input_snapshot
        USING hash (request_id)
        """,
        """
        CREATE UNIQUE INDEX idx_rearois_unexpected_unique
        ON research_evidence_automation_roi.
            research_evidence_automation_roi_input_snapshot(id, request_id)
        """,
        """
        DROP INDEX research_evidence_automation_roi.idx_rearoisb_binding;
        CREATE INDEX idx_rearoisb_binding
        ON research_evidence_automation_roi.
            research_evidence_automation_roi_input_snapshot_binding(
                project_id, input_role
            )
        """,
    ],
)
def test_v59_reapply_rejects_complete_index_inventory_drift(
    conn, schema_v59, mutation
):
    conn.execute(f"SET ROLE {FUNCTION_OWNER}")
    conn.execute(mutation)
    conn.commit()
    conn.execute(f"SET ROLE {MIGRATION_OWNER}")
    with pytest.raises(Exception, match="divergent indexes"):
        pg.apply_v59_research_automation_roi_use(conn)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            """
            ALTER SCHEMA research_evidence_automation_roi
            OWNER TO workflow_migration_owner
            """,
            "trusted-schema owner drift",
        ),
        (
            """
            GRANT CREATE ON SCHEMA research_evidence_automation_roi
            TO workflow_migration_owner
            """,
            "trusted-schema normalized ACL drift",
        ),
    ],
)
def test_v59_reapply_rejects_schema_owner_and_create_acl_drift(
    conn, schema_v59, mutation, message
):
    conn.execute("RESET ROLE")
    conn.execute(mutation)
    conn.commit()
    conn.execute(f"SET ROLE {MIGRATION_OWNER}")
    try:
        with pytest.raises(Exception, match=message):
            pg.apply_v59_research_automation_roi_use(conn)
    finally:
        conn.rollback()
        conn.execute("RESET ROLE")
        conn.execute(
            """
            ALTER SCHEMA research_evidence_automation_roi
            OWNER TO workflow_research_evidence_owner
            """
        )
        conn.execute(
            """
            REVOKE CREATE ON SCHEMA research_evidence_automation_roi
            FROM workflow_migration_owner
            """
        )
        conn.commit()
        conn.execute(f"SET ROLE {MIGRATION_OWNER}")


@pytest.mark.parametrize(
    ("mutation", "probe"),
    [
        (
            f"REVOKE USAGE ON SCHEMA {OBJECT_SCHEMA} FROM {RUNTIME_ROLE}",
            """
            SELECT NOT has_schema_privilege(%s, %s, 'USAGE')
            """,
        ),
        (
            f"GRANT CREATE ON SCHEMA {OBJECT_SCHEMA} TO {RUNTIME_ROLE}",
            """
            SELECT has_schema_privilege(%s, %s, 'CREATE')
            """,
        ),
        (
            f"GRANT USAGE ON SCHEMA {OBJECT_SCHEMA} TO PUBLIC",
            """
            SELECT has_schema_privilege('public', %s, 'USAGE')
            """,
        ),
        (
            f"GRANT USAGE ON SCHEMA {OBJECT_SCHEMA} TO {MIGRATION_OWNER}",
            """
            SELECT has_schema_privilege(%s, %s, 'USAGE')
            """,
        ),
        (
            f"GRANT USAGE ON SCHEMA {OBJECT_SCHEMA} TO SESSION_USER",
            """
            SELECT has_schema_privilege(current_user, %s, 'USAGE')
            """,
        ),
        (
            f"""
            GRANT USAGE ON SCHEMA {OBJECT_SCHEMA}
            TO {RUNTIME_ROLE} WITH GRANT OPTION
            """,
            """
            SELECT bool_or(acl.is_grantable)
            FROM pg_namespace namespace
            CROSS JOIN LATERAL aclexplode(namespace.nspacl) acl
            JOIN pg_roles grantee ON grantee.oid = acl.grantee
            WHERE namespace.nspname = %s
              AND grantee.rolname = %s
              AND acl.privilege_type = 'USAGE'
            """,
        ),
    ],
)
def test_v59_reapply_rejects_normalized_schema_acl_drift_without_repair(
    conn, schema_v59, mutation, probe
):
    conn.execute("RESET ROLE")
    conn.execute(f"SET ROLE {FUNCTION_OWNER}")
    conn.execute(mutation)
    conn.commit()
    conn.execute("RESET ROLE")
    conn.execute(f"SET ROLE {MIGRATION_OWNER}")
    with pytest.raises(Exception, match="trusted-schema normalized ACL drift"):
        pg.apply_v59_research_automation_roi_use(conn)
    conn.rollback()
    conn.execute("RESET ROLE")
    if "bool_or(acl.is_grantable)" in probe:
        parameters = (OBJECT_SCHEMA, RUNTIME_ROLE)
    elif probe.count("%s") == 2:
        parameters = (RUNTIME_ROLE, OBJECT_SCHEMA)
    else:
        parameters = (OBJECT_SCHEMA,)
    assert conn.execute(probe, parameters).fetchone()[0] is True


@pytest.mark.parametrize(
    ("mutation", "restore"),
    [
        (
            f"""
            ALTER DEFAULT PRIVILEGES FOR ROLE {FUNCTION_OWNER}
            GRANT EXECUTE ON FUNCTIONS TO PUBLIC
            """,
            f"""
            ALTER DEFAULT PRIVILEGES FOR ROLE {FUNCTION_OWNER}
            REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC
            """,
        ),
        (
            f"""
            ALTER DEFAULT PRIVILEGES FOR ROLE {FUNCTION_OWNER}
            GRANT EXECUTE ON FUNCTIONS TO {RUNTIME_ROLE}
            """,
            f"""
            ALTER DEFAULT PRIVILEGES FOR ROLE {FUNCTION_OWNER}
            REVOKE EXECUTE ON FUNCTIONS FROM {RUNTIME_ROLE}
            """,
        ),
        (
            f"""
            ALTER DEFAULT PRIVILEGES FOR ROLE {FUNCTION_OWNER}
            GRANT EXECUTE ON FUNCTIONS TO {RUNTIME_ROLE}
            WITH GRANT OPTION
            """,
            f"""
            ALTER DEFAULT PRIVILEGES FOR ROLE {FUNCTION_OWNER}
            REVOKE EXECUTE ON FUNCTIONS FROM {RUNTIME_ROLE}
            """,
        ),
        (
            f"""
            ALTER DEFAULT PRIVILEGES FOR ROLE {FUNCTION_OWNER}
            GRANT SELECT ON TABLES TO {RUNTIME_ROLE}
            """,
            f"""
            ALTER DEFAULT PRIVILEGES FOR ROLE {FUNCTION_OWNER}
            REVOKE SELECT ON TABLES FROM {RUNTIME_ROLE}
            """,
        ),
        (
            f"""
            ALTER DEFAULT PRIVILEGES FOR ROLE {FUNCTION_OWNER}
            IN SCHEMA {OBJECT_SCHEMA}
            GRANT EXECUTE ON FUNCTIONS TO {RUNTIME_ROLE}
            """,
            f"""
            ALTER DEFAULT PRIVILEGES FOR ROLE {FUNCTION_OWNER}
            IN SCHEMA {OBJECT_SCHEMA}
            REVOKE EXECUTE ON FUNCTIONS FROM {RUNTIME_ROLE}
            """,
        ),
    ],
)
def test_v59_reapply_rejects_default_acl_drift_without_repair(
    conn, schema_v59, mutation, restore
):
    expected = _default_acl_inventory(conn)
    assert len(expected) == 1
    conn.execute(f"SET ROLE {FUNCTION_OWNER}")
    conn.execute(mutation)
    conn.commit()
    conn.execute(f"SET ROLE {MIGRATION_OWNER}")
    mutated = _default_acl_inventory(conn)
    assert mutated != expected
    with pytest.raises(Exception, match="trusted-schema default ACL drift"):
        pg.apply_v59_research_automation_roi_use(conn)
    conn.rollback()
    assert _default_acl_inventory(conn) == mutated
    conn.execute(f"SET ROLE {FUNCTION_OWNER}")
    conn.execute(restore)
    conn.commit()
    conn.execute(f"SET ROLE {MIGRATION_OWNER}")
    assert _default_acl_inventory(conn) == expected


def test_v59_reapply_rejects_runtime_membership_escalation(conn, schema_v59):
    conn.execute("RESET ROLE")
    conn.execute(
        "GRANT workflow_migration_owner TO workflow_automation_roi_runtime"
    )
    conn.commit()
    conn.execute(f"SET ROLE {MIGRATION_OWNER}")
    try:
        with pytest.raises(
            Exception,
            match="runtime role-membership escalation paths",
        ):
            pg.apply_v59_research_automation_roi_use(conn)
    finally:
        conn.rollback()
        conn.execute("RESET ROLE")
        conn.execute(
            "REVOKE workflow_migration_owner FROM workflow_automation_roi_runtime"
        )
        conn.commit()
        conn.execute(f"SET ROLE {MIGRATION_OWNER}")


@pytest.mark.parametrize(
    ("role", "drift_attribute", "safe_attribute"),
    [
        (role, drift, safe)
        for role in (MIGRATION_OWNER, FUNCTION_OWNER, RUNTIME_ROLE)
        for drift, safe in (
            ("INHERIT", "NOINHERIT"),
            ("SUPERUSER", "NOSUPERUSER"),
            ("CREATEROLE", "NOCREATEROLE"),
            ("CREATEDB", "NOCREATEDB"),
            ("REPLICATION", "NOREPLICATION"),
            ("BYPASSRLS", "NOBYPASSRLS"),
        )
    ],
)
def test_v59_reapply_rejects_role_attribute_drift(
    conn, schema_v59, role, drift_attribute, safe_attribute
):
    conn.execute("RESET ROLE")
    conn.execute(f"ALTER ROLE {role} {drift_attribute}")
    conn.commit()
    conn.execute(f"SET ROLE {MIGRATION_OWNER}")
    try:
        with pytest.raises(Exception, match="canonical migration"):
            pg.apply_v59_research_automation_roi_use(conn)
    finally:
        conn.rollback()
        conn.execute("RESET ROLE")
        conn.execute(f"ALTER ROLE {role} {safe_attribute}")
        conn.commit()
        conn.execute(f"SET ROLE {MIGRATION_OWNER}")


@pytest.mark.parametrize(
    "membership_options",
    [
        "WITH ADMIN TRUE, INHERIT FALSE, SET TRUE",
        "WITH ADMIN FALSE, INHERIT TRUE, SET TRUE",
        "WITH ADMIN FALSE, INHERIT FALSE, SET FALSE",
    ],
)
def test_v59_reapply_rejects_membership_option_drift(
    conn, schema_v59, membership_options
):
    options_supported = conn.execute(
        """
        SELECT count(*) = 2
        FROM pg_attribute
        WHERE attrelid = 'pg_catalog.pg_auth_members'::regclass
          AND attname IN ('inherit_option', 'set_option')
          AND NOT attisdropped
        """
    ).fetchone()[0]
    if not options_supported:
        pytest.skip("membership options are unavailable on this PostgreSQL version")
    conn.execute("RESET ROLE")
    conn.execute(
        "GRANT workflow_research_evidence_owner "
        "TO workflow_migration_owner "
        f"{membership_options}"
    )
    conn.commit()
    conn.execute(f"SET ROLE {MIGRATION_OWNER}")
    try:
        with pytest.raises(
            Exception,
            match=(
                "exact canonical role-membership graph"
                "|non-inherited, SET-enabled deployment membership"
            ),
        ):
            pg.apply_v59_research_automation_roi_use(conn)
    finally:
        conn.rollback()
        conn.execute("RESET ROLE")
        conn.execute(
            "GRANT workflow_research_evidence_owner "
            "TO workflow_migration_owner "
            "WITH ADMIN FALSE, INHERIT FALSE, SET TRUE"
        )
        conn.commit()
        conn.execute(f"SET ROLE {MIGRATION_OWNER}")


def test_v59_reapply_rejects_trigger_update_scope_tgattr_drift(
    conn, schema_v59
):
    helper = f'"{schema_v59}".slicea_reject_mutation()'
    conn.execute("RESET ROLE")
    conn.execute(
        f"GRANT EXECUTE ON FUNCTION {helper} TO {FUNCTION_OWNER}"
    )
    conn.commit()
    try:
        conn.execute(f"SET ROLE {FUNCTION_OWNER}")
        conn.execute(
            f"""
            DROP TRIGGER trg_rearois_no_mutation
            ON research_evidence_automation_roi.
                research_evidence_automation_roi_input_snapshot;
            CREATE TRIGGER trg_rearois_no_mutation
            BEFORE DELETE OR UPDATE OF evaluated_by
            ON research_evidence_automation_roi.
                research_evidence_automation_roi_input_snapshot
            FOR EACH ROW
            EXECUTE FUNCTION "{schema_v59}".slicea_reject_mutation()
            """
        )
        conn.commit()
    finally:
        conn.rollback()
        conn.execute("RESET ROLE")
        conn.execute(
            f"REVOKE EXECUTE ON FUNCTION {helper} FROM {FUNCTION_OWNER}"
        )
        conn.commit()
        conn.execute(f"SET ROLE {MIGRATION_OWNER}")
    assert not conn.execute(
        """
        SELECT has_function_privilege(
            %s, to_regprocedure(%s), 'EXECUTE'
        )
        """,
        (FUNCTION_OWNER, helper),
    ).fetchone()[0]
    with pytest.raises(Exception, match="divergent triggers"):
        pg.apply_v59_research_automation_roi_use(conn)


@pytest.mark.parametrize(
    "mutation",
    [
        """
        REVOKE USAGE ON SCHEMA {upstream}
        FROM workflow_research_evidence_owner
        """,
        """
        REVOKE REFERENCES (id) ON TABLE {upstream}.projects
        FROM workflow_research_evidence_owner
        """,
        """
        REVOKE REFERENCES (id) ON TABLE {upstream}.projects
        FROM workflow_research_evidence_owner;
        GRANT REFERENCES ON TABLE {upstream}.projects
        TO workflow_research_evidence_owner
        """,
        """
        GRANT REFERENCES (id) ON TABLE {upstream}.projects
        TO workflow_research_evidence_owner WITH GRANT OPTION
        """,
        """
        GRANT SELECT ON TABLE {upstream}.projects
        TO workflow_research_evidence_owner
        """,
        """
        REVOKE SELECT ON {upstream}.
            research_evidence_consumer_input_binding
        FROM workflow_research_evidence_owner
        """,
        """
        REVOKE SELECT ON {upstream}.approved_calculation_input
        FROM workflow_research_evidence_owner
        """,
        """
        REVOKE SELECT ON {upstream}.
            research_evidence_consumer_input_binding_sequence_allocator
        FROM workflow_research_evidence_owner
        """,
        """
        REVOKE UPDATE ON {upstream}.
            research_evidence_consumer_input_binding_sequence_allocator
        FROM workflow_research_evidence_owner
        """,
        """
        GRANT DELETE ON {upstream}.
            research_evidence_consumer_input_binding
        TO workflow_research_evidence_owner
        """,
        """
        GRANT INSERT ON {upstream}.
            approved_calculation_input
        TO workflow_research_evidence_owner
        """,
        """
        GRANT EXECUTE ON FUNCTION {upstream}.slicea_reject_mutation()
        TO workflow_research_evidence_owner
        """,
        """
        GRANT USAGE ON SCHEMA {upstream}
        TO workflow_automation_roi_runtime
        """,
        """
        GRANT SELECT ON {upstream}.
            research_evidence_consumer_input_binding_sequence_allocator
        TO workflow_automation_roi_runtime
        """,
        """
        GRANT EXECUTE ON FUNCTION {upstream}.slicea_reject_mutation()
        TO workflow_automation_roi_runtime
        """,
        """
        GRANT USAGE ON SCHEMA {upstream} TO PUBLIC
        """,
        """
        GRANT SELECT ON {upstream}.
            research_evidence_consumer_input_binding
        TO PUBLIC
        """,
        """
        GRANT SELECT ON {upstream}.approved_calculation_input
        TO PUBLIC
        """,
        """
        GRANT SELECT ON {upstream}.
            research_evidence_consumer_input_binding_sequence_allocator
        TO PUBLIC
        """,
        """
        GRANT EXECUTE ON FUNCTION {upstream}.slicea_reject_mutation()
        TO PUBLIC
        """,
    ],
)
def test_v59_reapply_rejects_missing_or_extra_upstream_acl(
    conn, schema_v59, mutation
):
    conn.execute(mutation.format(upstream=f'"{schema_v59}"'))
    conn.commit()
    with pytest.raises(
        Exception,
        match="function-owner ACL drift|privilege drift",
    ):
        pg.apply_v59_research_automation_roi_use(conn)


def test_v59_inventory_ignores_same_named_objects_in_other_schema(
    conn, schema_v59
):
    conn.execute(
        f"""
        CREATE FUNCTION "{schema_v59}".
            research_evidence_create_automation_roi_snapshot()
        RETURNS void LANGUAGE sql AS 'SELECT NULL'
        """
    )
    conn.commit()
    pg.apply_v59_research_automation_roi_use(conn)
