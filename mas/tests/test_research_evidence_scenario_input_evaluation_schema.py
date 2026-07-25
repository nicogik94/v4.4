"""Focused PostgreSQL contract tests for the unrun R1.7 migration."""
import concurrent.futures
import contextlib
import json
import sys
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests.evidence_snapshot_pg as pg  # noqa: E402
import tests.test_research_evidence_binding_schema as v57fx  # noqa: E402
from research_evidence import binding_service  # noqa: E402
from research_evidence import scenario_input_evaluation_service as service  # noqa: E402
from research_evidence.scenario_input_evaluation_models import (  # noqa: E402
    EVALUATION_POLICY_FINGERPRINT,
    EVALUATION_POLICY_IDENTIFIER,
    EVALUATION_POLICY_PARAMETERS,
    EVALUATION_POLICY_VERSION,
    EVALUATOR_VERSION,
    OpaqueHypothesisDescriptor,
    REASON_ORDER,
    ScenarioInputBindingSelection,
    ScenarioInputEvaluationRequest,
    ScenarioInputManifestRegistration,
    canonical_manifest_descriptor,
    manifest_fingerprint,
)


AS_OF = v57fx.NOW + timedelta(days=1)
MANIFEST_VECTOR_DESCRIPTOR = (
    "scenario-input-manifest-v1\n"
    "namespace=11:scenario.ns\n"
    "version=2:v1\n"
    "cardinality=3\n"
    "key=5:alpha\n"
    "key=4:zeta\n"
    "key=7:áccent\n"
)
MANIFEST_VECTOR_SHA256 = (
    "0aaf8b278d98a8821913af720e59b90659e116c9eff5b9d867c314142e5468e6"
)
OPAQUE_VECTOR_JSON = '{"z":[true,"x"],"a":1}'
OPAQUE_VECTOR_NORMALIZED = '{"a": 1, "z": [true, "x"]}'
OPAQUE_VECTOR_SHA256 = (
    "61f8e11673996e2f711bc8f88802be5e30384d8b8ca42f1b8dfd3a1cd032d9bb"
)
REQUEST_VECTOR_JSON = (
    '{"selected_bindings":[],"binding_contract":'
    '{"consumer_contract":"scenario_input","binding_set_id":"set-1"},'
    '"freshness_as_of":"2026-01-01T00:00:00.000000Z",'
    '"request_id":"request-fixed",'
    '"project_id":"00000000-0000-0000-0000-000000000001"}'
)
REQUEST_VECTOR_NORMALIZED = (
    '{"project_id": "00000000-0000-0000-0000-000000000001", '
    '"request_id": "request-fixed", '
    '"freshness_as_of": "2026-01-01T00:00:00.000000Z", '
    '"binding_contract": {"binding_set_id": "set-1", '
    '"consumer_contract": "scenario_input"}, "selected_bindings": []}'
)
REQUEST_VECTOR_SHA256 = (
    "892584262f0f0c4219de137e4a09052196152f676b788b724881529943962a86"
)
PERSISTED_VECTOR_PROJECT_ID = "00000000-0000-0000-0000-000000000101"
PERSISTED_VECTOR_MANIFEST_ID = "00000000-0000-0000-0000-000000000102"
PERSISTED_VECTOR_BINDING_IDS = (
    "00000000-0000-0000-0000-000000000201",
    "00000000-0000-0000-0000-000000000202",
    "00000000-0000-0000-0000-000000000203",
)
PERSISTED_VECTOR_REQUEST_ID = "evaluation-literal-vector"
PERSISTED_VECTOR_POLICY_JSON = (
    '{"dependence_outcomes":{"declared_dependent":"qualified",'
    '"declared_independent_not_verified":"qualified",'
    '"not_assessed":"indeterminate"},"reason_order":'
    '["evidence_unavailable","lineage_not_current","review_rejected",'
    '"review_needs_revision","review_withdrawn","material_drift",'
    '"selected_binding_successor","binding_does_not_meet_contract",'
    '"review_not_assessed","freshness_unknown","drift_not_assessed",'
    '"drift_indeterminate","binding_indeterminate",'
    '"dependence_not_assessed","freshness_stale","binding_qualified",'
    '"dependence_declared_dependent",'
    '"dependence_declared_independent_not_verified"],'
    '"satisfies_nonempty_manifest_reachable":false,'
    '"status_precedence":["does_not_satisfy","indeterminate",'
    '"qualified","satisfies"]}'
)
PERSISTED_VECTOR_POLICY_SHA256 = (
    "70d65b9b32fcf55dfef889a5dbde6d9679bf76e7ae57389d559a9416a6c2a699"
)
PERSISTED_VECTOR_REQUEST_SHA256 = (
    "25c4f7c47886ae3c72629d0c764095cf59f5818013c211f0df50996e4fddda93"
)
TABLES = (
    "research_evidence_scenario_input_manifest",
    "research_evidence_scenario_input_manifest_item",
    "research_evidence_scenario_input_evaluation",
    "research_evidence_scenario_input_evaluation_input",
    "research_evidence_scenario_input_evaluation_sequence_allocator",
)
ALLOCATOR_LIFECYCLE_CONTRACT = "CONTRACT_A_NO_PREALLOCATION"
INDEX_CONTRACT = {
    "research_evidence_scenario_input_manifest_pkey": (
        "research_evidence_scenario_input_manifest",
        "research_evidence_scenario_input_manifest_pkey",
        (("id", "uuid_ops"),),
        (),
    ),
    "uq_resim_id_project": (
        "research_evidence_scenario_input_manifest",
        "uq_resim_id_project",
        (("id", "uuid_ops"), ("project_id", "uuid_ops")),
        (),
    ),
    "uq_resim_project_request": (
        "research_evidence_scenario_input_manifest",
        "uq_resim_project_request",
        (("project_id", "uuid_ops"), ("registration_request_id", "text_ops")),
        (),
    ),
    "pk_resimi": (
        "research_evidence_scenario_input_manifest_item",
        "pk_resimi",
        (("manifest_id", "uuid_ops"), ("input_key", "text_ops")),
        (),
    ),
    "uq_resimi_ordinal": (
        "research_evidence_scenario_input_manifest_item",
        "uq_resimi_ordinal",
        (("manifest_id", "uuid_ops"), ("item_ordinal", "int4_ops")),
        (),
    ),
    "uq_resimi_project_key": (
        "research_evidence_scenario_input_manifest_item",
        "uq_resimi_project_key",
        (
            ("manifest_id", "uuid_ops"),
            ("project_id", "uuid_ops"),
            ("input_key", "text_ops"),
        ),
        (),
    ),
    "research_evidence_scenario_input_evaluation_pkey": (
        "research_evidence_scenario_input_evaluation",
        "research_evidence_scenario_input_evaluation_pkey",
        (("id", "uuid_ops"),),
        (),
    ),
    "uq_resie_id_scope": (
        "research_evidence_scenario_input_evaluation",
        "uq_resie_id_scope",
        (
            ("id", "uuid_ops"),
            ("project_id", "uuid_ops"),
            ("manifest_id", "uuid_ops"),
            ("binding_set_id", "text_ops"),
            ("descriptor_namespace", "text_ops"),
            ("descriptor_version", "text_ops"),
            ("descriptor_fingerprint", "text_ops"),
        ),
        (),
    ),
    "uq_resie_id_project_manifest": (
        "research_evidence_scenario_input_evaluation",
        "uq_resie_id_project_manifest",
        (
            ("id", "uuid_ops"),
            ("project_id", "uuid_ops"),
            ("manifest_id", "uuid_ops"),
        ),
        (),
    ),
    "uq_resie_project_request": (
        "research_evidence_scenario_input_evaluation",
        "uq_resie_project_request",
        (("project_id", "uuid_ops"), ("request_id", "text_ops")),
        (),
    ),
    "uq_resie_scope_sequence": (
        "research_evidence_scenario_input_evaluation",
        "uq_resie_scope_sequence",
        (
            ("project_id", "uuid_ops"),
            ("manifest_id", "uuid_ops"),
            ("binding_set_id", "text_ops"),
            ("descriptor_namespace", "text_ops"),
            ("descriptor_version", "text_ops"),
            ("descriptor_fingerprint", "text_ops"),
            ("evaluation_sequence", "int4_ops"),
        ),
        (),
    ),
    "uq_resie_predecessor_once": (
        "research_evidence_scenario_input_evaluation",
        "uq_resie_predecessor_once",
        (("predecessor_evaluation_id", "uuid_ops"),),
        (),
    ),
    "pk_resiei": (
        "research_evidence_scenario_input_evaluation_input",
        "pk_resiei",
        (("evaluation_id", "uuid_ops"), ("input_key", "text_ops")),
        (),
    ),
    "uq_resiei_manifest_key": (
        "research_evidence_scenario_input_evaluation_input",
        "uq_resiei_manifest_key",
        (
            ("evaluation_id", "uuid_ops"),
            ("manifest_id", "uuid_ops"),
            ("input_key", "text_ops"),
        ),
        (),
    ),
    "uq_resiei_binding": (
        "research_evidence_scenario_input_evaluation_input",
        "uq_resiei_binding",
        (("evaluation_id", "uuid_ops"), ("selected_binding_id", "uuid_ops")),
        (),
    ),
    "pk_resie_allocator": (
        "research_evidence_scenario_input_evaluation_sequence_allocator",
        "pk_resie_allocator",
        (
            ("project_id", "uuid_ops"),
            ("manifest_id", "uuid_ops"),
            ("binding_set_id", "text_ops"),
            ("descriptor_namespace", "text_ops"),
            ("descriptor_version", "text_ops"),
            ("descriptor_fingerprint", "text_ops"),
        ),
        (),
    ),
    "idx_resim_project_namespace_version": (
        "research_evidence_scenario_input_manifest",
        None,
        (
            ("project_id", "uuid_ops"),
            ("manifest_namespace", "text_ops"),
            ("manifest_version", "text_ops"),
            ("registered_at", "timestamptz_ops"),
            ("id", "uuid_ops"),
        ),
        ("manifest_fingerprint", "input_cardinality"),
    ),
    "idx_resie_scope_sequence": (
        "research_evidence_scenario_input_evaluation",
        None,
        (
            ("project_id", "uuid_ops"),
            ("manifest_id", "uuid_ops"),
            ("binding_set_id", "text_ops"),
            ("descriptor_namespace", "text_ops"),
            ("descriptor_version", "text_ops"),
            ("descriptor_fingerprint", "text_ops"),
            ("evaluation_sequence", "int4_ops"),
        ),
        ("predecessor_evaluation_id", "evaluation_status", "evaluated_at"),
    ),
    "idx_resiei_binding": (
        "research_evidence_scenario_input_evaluation_input",
        None,
        (("selected_binding_id", "uuid_ops"), ("evaluation_id", "uuid_ops")),
        ("input_key", "input_status"),
    ),
}
INDEX_INVENTORY = tuple(INDEX_CONTRACT)
EXPLICIT_INDEX_INVENTORY = tuple(
    name
    for name, (_, constraint, _, _) in INDEX_CONTRACT.items()
    if constraint is None
)
FUNCTION_INVENTORY = (
    "research_evidence_scenario_input_policy_state",
    "research_evidence_prepare_scenario_input_manifest",
    "research_evidence_link_scenario_input_manifest_items",
    "research_evidence_prepare_scenario_input_manifest_item",
    "research_evidence_check_scenario_input_manifest",
    "research_evidence_prepare_scenario_input_evaluation",
    "research_evidence_link_scenario_input_evaluation_inputs",
    "research_evidence_prepare_scenario_input_evaluation_input",
    "research_evidence_check_scenario_input_evaluation",
    "research_evidence_register_scenario_input_manifest",
    "research_evidence_create_scenario_input_evaluation",
)
FUNCTION_SIGNATURES = {
    "research_evidence_scenario_input_policy_state":
        "(boolean,boolean,text,text,text,text,boolean,text)",
    "research_evidence_register_scenario_input_manifest":
        "(uuid,text,text,text,jsonb,text)",
    "research_evidence_create_scenario_input_evaluation": "(jsonb)",
    **{
        name: "()"
        for name in FUNCTION_INVENTORY
        if name not in {
            "research_evidence_scenario_input_policy_state",
            "research_evidence_register_scenario_input_manifest",
            "research_evidence_create_scenario_input_evaluation",
        }
    },
}
TRIGGER_INVENTORY = (
    "trg_resim_prepare_insert",
    "trg_resim_link_items",
    "trg_resim_no_mutation",
    "trg_resimi_prepare_insert",
    "trg_resimi_no_mutation",
    "trg_resim_complete",
    "trg_resimi_complete",
    "trg_resie_prepare_insert",
    "trg_resie_link_inputs",
    "trg_resie_no_mutation",
    "trg_resiei_prepare_insert",
    "trg_resiei_no_mutation",
    "trg_resie_complete",
    "trg_resiei_complete",
)
TRIGGER_TABLE_INVENTORY = {
    **{
        name: "research_evidence_scenario_input_manifest"
        for name in (
            "trg_resim_prepare_insert",
            "trg_resim_link_items",
            "trg_resim_no_mutation",
            "trg_resim_complete",
        )
    },
    **{
        name: "research_evidence_scenario_input_manifest_item"
        for name in (
            "trg_resimi_prepare_insert",
            "trg_resimi_no_mutation",
            "trg_resimi_complete",
        )
    },
    **{
        name: "research_evidence_scenario_input_evaluation"
        for name in (
            "trg_resie_prepare_insert",
            "trg_resie_link_inputs",
            "trg_resie_no_mutation",
            "trg_resie_complete",
        )
    },
    **{
        name: "research_evidence_scenario_input_evaluation_input"
        for name in (
            "trg_resiei_prepare_insert",
            "trg_resiei_no_mutation",
            "trg_resiei_complete",
        )
    },
}
TRIGGER_FUNCTION_INVENTORY = {
    "trg_resim_prepare_insert":
        "research_evidence_prepare_scenario_input_manifest",
    "trg_resim_link_items":
        "research_evidence_link_scenario_input_manifest_items",
    "trg_resim_no_mutation": "slicea_reject_mutation",
    "trg_resimi_prepare_insert":
        "research_evidence_prepare_scenario_input_manifest_item",
    "trg_resimi_no_mutation": "slicea_reject_mutation",
    "trg_resim_complete":
        "research_evidence_check_scenario_input_manifest",
    "trg_resimi_complete":
        "research_evidence_check_scenario_input_manifest",
    "trg_resie_prepare_insert":
        "research_evidence_prepare_scenario_input_evaluation",
    "trg_resie_link_inputs":
        "research_evidence_link_scenario_input_evaluation_inputs",
    "trg_resie_no_mutation": "slicea_reject_mutation",
    "trg_resiei_prepare_insert":
        "research_evidence_prepare_scenario_input_evaluation_input",
    "trg_resiei_no_mutation": "slicea_reject_mutation",
    "trg_resie_complete":
        "research_evidence_check_scenario_input_evaluation",
    "trg_resiei_complete":
        "research_evidence_check_scenario_input_evaluation",
}
CONSTRAINT_TABLE_INVENTORY = {
    **{
        name: "research_evidence_scenario_input_manifest"
        for name in (
            "research_evidence_scenario_input_manifest_pkey",
            "uq_resim_id_project",
            "uq_resim_project_request",
            "fk_resim_project",
            "ck_resim_nonblank",
            "ck_resim_keys",
            "ck_resim_cardinality",
            "ck_resim_fingerprint",
        )
    },
    **{
        name: "research_evidence_scenario_input_manifest_item"
        for name in (
            "pk_resimi",
            "uq_resimi_ordinal",
            "uq_resimi_project_key",
            "fk_resimi_manifest",
            "ck_resimi_nonblank",
            "ck_resimi_ordinal",
        )
    },
    **{
        name: "research_evidence_scenario_input_evaluation"
        for name in (
            "research_evidence_scenario_input_evaluation_pkey",
            "uq_resie_id_scope",
            "uq_resie_id_project_manifest",
            "uq_resie_project_request",
            "uq_resie_scope_sequence",
            "uq_resie_predecessor_once",
            "fk_resie_project",
            "fk_resie_manifest",
            "fk_resie_predecessor",
            "ck_resie_nonblank",
            "ck_resie_fingerprints",
            "ck_resie_status",
            "ck_resie_json_shapes",
            "ck_resie_policy",
            "ck_resie_sequence",
        )
    },
    **{
        name: "research_evidence_scenario_input_evaluation_input"
        for name in (
            "pk_resiei",
            "uq_resiei_manifest_key",
            "uq_resiei_binding",
            "fk_resiei_evaluation",
            "fk_resiei_manifest_item",
            "fk_resiei_binding",
            "ck_resiei_status",
            "ck_resiei_dependence",
            "ck_resiei_nonblank",
            "ck_resiei_json",
        )
    },
    **{
        name: (
            "research_evidence_scenario_input_evaluation_sequence_allocator"
        )
        for name in (
            "pk_resie_allocator",
            "fk_resie_allocator_manifest",
            "fk_resie_allocator_last",
            "ck_resie_allocator_sequence",
        )
    },
}
INVENTORY_MUTATIONS = tuple(
    (f"table:{table}", f"GRANT SELECT ON {table} TO PUBLIC")
    for table in TABLES
) + tuple(
    (
        f"constraint:{name}",
        f"COMMENT ON CONSTRAINT {name} ON {table} IS 'v58:drift'",
    )
    for name, table in CONSTRAINT_TABLE_INVENTORY.items()
) + tuple(
    (f"index:{name}", f"COMMENT ON INDEX {name} IS 'v58:drift'")
    for name in EXPLICIT_INDEX_INVENTORY
) + tuple(
    (
        f"trigger:{name}",
        f"ALTER TABLE {table} DISABLE TRIGGER {name}",
    )
    for name, table in TRIGGER_TABLE_INVENTORY.items()
) + tuple(
    (
        f"function:{name}",
        f"GRANT EXECUTE ON FUNCTION {name}{FUNCTION_SIGNATURES[name]} "
        "TO PUBLIC",
    )
    for name in FUNCTION_INVENTORY
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
def schema_v58(conn):
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
        yield schema


@pytest.fixture(autouse=True)
def feature_enabled(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")


def _apply_v58_public_postgresql16_baseline(conn):
    """Apply the real v58 predecessor chain in the default public namespace."""
    assert 160000 <= int(conn.execute(
        "SELECT current_setting('server_version_num')"
    ).fetchone()[0]) < 170000
    conn.execute("SET search_path TO public")
    for path in (pg.INIT_SQL, pg.OUTCOMES_SQL, pg.V47_SQL):
        pg._run_script(conn, path)
    for apply in (
        pg.apply_v48,
        pg.apply_v49,
        pg.apply_v51_research,
        pg.apply_v52_research,
        pg.apply_v53_research_intake,
        pg.apply_v54_research_review,
        pg.apply_v55_research_freshness,
        pg.apply_v56_research_claim_support,
        pg.apply_v57_research_binding,
    ):
        apply(conn)


def _v58_owner_topology(conn):
    table_rows = conn.execute(
        """
        SELECT c.relname, pg_get_userbyid(c.relowner),
               pg_get_userbyid(database_info.datdba)
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_database database_info
          ON database_info.datname = current_database()
        WHERE n.nspname = current_schema()
          AND c.relkind = 'r'
          AND c.relname = ANY(%s)
        ORDER BY c.relname
        """,
        (list(TABLES),),
    ).fetchall()
    function_rows = conn.execute(
        """
        SELECT p.proname, pg_get_userbyid(p.proowner),
               pg_get_userbyid(database_info.datdba)
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        JOIN pg_database database_info
          ON database_info.datname = current_database()
        WHERE n.nspname = current_schema()
          AND p.proname = ANY(%s)
        ORDER BY p.proname
        """,
        (list(FUNCTION_INVENTORY),),
    ).fetchall()
    return table_rows, function_rows


def _assert_v58_database_owned(conn):
    table_rows, function_rows = _v58_owner_topology(conn)
    assert len(table_rows) == 5
    assert len(function_rows) == 11
    assert all(owner == database_owner for _, owner, database_owner in table_rows)
    assert all(
        owner == database_owner
        for _, owner, database_owner in function_rows
    )


def test_postgresql16_public_pg_database_owner_v58_first_apply_and_reapply():
    """Certify the PostgreSQL 15/16 production ownership topology."""
    with pg.dedicated_negative_role_cluster() as conn:
        _apply_v58_public_postgresql16_baseline(conn)
        identity = conn.execute(
            """
            SELECT pg_get_userbyid(database_info.datdba),
                   pg_get_userbyid(namespace.nspowner),
                   current_user
            FROM pg_database database_info
            JOIN pg_namespace namespace ON namespace.nspname = 'public'
            WHERE database_info.datname = current_database()
            """
        ).fetchone()
        assert identity == ("postgres", "pg_database_owner", "postgres")

        pg.apply_v58_research_scenario_input_evaluation(conn)
        _assert_v58_database_owned(conn)
        assert conn.execute(
            """
            SELECT pg_get_userbyid(nspowner)
            FROM pg_namespace WHERE nspname = 'public'
            """
        ).fetchone()[0] == "pg_database_owner"

        pg.apply_v58_research_scenario_input_evaluation(conn)
        _assert_v58_database_owned(conn)


def test_postgresql16_v58_reapply_after_canonical_v59_schema_transfer():
    """Keep v58 objects DB-owned after the ratified upstream schema transfer."""
    with pg.dedicated_negative_role_cluster() as conn:
        _apply_v58_public_postgresql16_baseline(conn)
        pg.apply_v58_research_scenario_input_evaluation(conn)
        pg.ensure_external_role_prerequisites(conn)
        pg.assign_v59_upstream_migration_ownership(conn, "public")

        assert conn.execute(
            """
            SELECT pg_get_userbyid(nspowner)
            FROM pg_namespace WHERE nspname = 'public'
            """
        ).fetchone()[0] == pg.MIGRATION_OWNER
        _assert_v58_database_owned(conn)

        pg.apply_v58_research_scenario_input_evaluation(conn)
        _assert_v58_database_owned(conn)


def test_postgresql16_v58_reapply_rejects_owner_and_v59_security_drift():
    """Exercise every load-bearing Model C owner/security rejection."""
    with pg.dedicated_negative_role_cluster() as conn:
        psycopg = pg.psycopg_module()
        _apply_v58_public_postgresql16_baseline(conn)
        pg.apply_v58_research_scenario_input_evaluation(conn)
        pg.ensure_external_role_prerequisites(conn)
        pg.assign_v59_upstream_migration_ownership(conn, "public")
        database = conn.execute("SELECT current_database()").fetchone()[0]
        database_owner = conn.execute(
            """
            SELECT pg_get_userbyid(datdba)
            FROM pg_database WHERE datname = current_database()
            """
        ).fetchone()[0]
        unrelated = f"v58_unrelated_{uuid.uuid4().hex[:12]}"
        grantor = f"v58_grantor_{uuid.uuid4().hex[:12]}"
        conn.execute(
            psycopg.sql.SQL("CREATE ROLE {} NOLOGIN").format(
                psycopg.sql.Identifier(unrelated)
            )
        )
        conn.execute(
            psycopg.sql.SQL("CREATE ROLE {} NOLOGIN").format(
                psycopg.sql.Identifier(grantor)
            )
        )

        # 1. A protected table cannot drift from the concrete database owner.
        conn.execute(
            psycopg.sql.SQL(
                "ALTER TABLE research_evidence_scenario_input_manifest "
                "OWNER TO {}"
            ).format(psycopg.sql.Identifier(unrelated))
        )
        with pytest.raises(Exception, match="divergent table ownership"):
            pg.apply_v58_research_scenario_input_evaluation(conn)
        conn.execute(
            psycopg.sql.SQL(
                "ALTER TABLE research_evidence_scenario_input_manifest "
                "OWNER TO {}"
            ).format(psycopg.sql.Identifier(database_owner))
        )

        # 2. A protected function has the same concrete-owner contract.
        conn.execute(
            psycopg.sql.SQL(
                "ALTER FUNCTION "
                "research_evidence_create_scenario_input_evaluation(jsonb) "
                "OWNER TO {}"
            ).format(psycopg.sql.Identifier(unrelated))
        )
        with pytest.raises(Exception, match="missing/divergent functions"):
            pg.apply_v58_research_scenario_input_evaluation(conn)
        conn.execute(
            psycopg.sql.SQL(
                "ALTER FUNCTION "
                "research_evidence_create_scenario_input_evaluation(jsonb) "
                "OWNER TO {}"
            ).format(psycopg.sql.Identifier(database_owner))
        )

        # 3. An arbitrary namespace owner is never trusted.
        conn.execute(
            psycopg.sql.SQL("ALTER SCHEMA public OWNER TO {}").format(
                psycopg.sql.Identifier(unrelated)
            )
        )
        with pytest.raises(Exception, match="untrusted schema owner"):
            pg.apply_v58_research_scenario_input_evaluation(conn)
        conn.execute(
            "ALTER SCHEMA public OWNER TO workflow_migration_owner"
        )

        # 4. Migration-owner schema trust requires exact role attributes.
        conn.execute("ALTER ROLE workflow_migration_owner INHERIT")
        with pytest.raises(Exception, match="non-canonical v59 role attributes"):
            pg.apply_v58_research_scenario_input_evaluation(conn)
        conn.execute("ALTER ROLE workflow_migration_owner NOINHERIT")

        # 5. The migration owner must not retain database CREATE.
        conn.execute(
            psycopg.sql.SQL(
                "GRANT CREATE ON DATABASE {} TO workflow_migration_owner"
            ).format(psycopg.sql.Identifier(database))
        )
        with pytest.raises(Exception, match="migration-owner database CREATE"):
            pg.apply_v58_research_scenario_input_evaluation(conn)
        conn.execute(
            psycopg.sql.SQL(
                "REVOKE CREATE ON DATABASE {} FROM workflow_migration_owner"
            ).format(psycopg.sql.Identifier(database))
        )

        # 6. The canonical owner-to-migration membership is mandatory.
        conn.execute(
            "REVOKE workflow_research_evidence_owner "
            "FROM workflow_migration_owner"
        )
        with pytest.raises(Exception, match="exact canonical v59 owner membership"):
            pg.apply_v58_research_scenario_input_evaluation(conn)
        conn.execute(
            "GRANT workflow_research_evidence_owner "
            "TO workflow_migration_owner "
            "WITH ADMIN FALSE, INHERIT FALSE, SET TRUE"
        )

        # 7. PostgreSQL-16 membership options are exact.
        conn.execute(
            "GRANT workflow_research_evidence_owner "
            "TO workflow_migration_owner "
            "WITH ADMIN FALSE, INHERIT TRUE, SET TRUE"
        )
        with pytest.raises(Exception, match="exact canonical v59 owner membership"):
            pg.apply_v58_research_scenario_input_evaluation(conn)
        conn.execute(
            "GRANT workflow_research_evidence_owner "
            "TO workflow_migration_owner "
            "WITH ADMIN FALSE, INHERIT FALSE, SET TRUE"
        )

        # 8. A second grantor row for the canonical membership is rejected.
        conn.execute(
            psycopg.sql.SQL(
                "GRANT workflow_research_evidence_owner TO {} "
                "WITH ADMIN OPTION"
            ).format(psycopg.sql.Identifier(grantor))
        )
        conn.execute(
            psycopg.sql.SQL("SET ROLE {}").format(
                psycopg.sql.Identifier(grantor)
            )
        )
        conn.execute(
            "GRANT workflow_research_evidence_owner "
            "TO workflow_migration_owner "
            "WITH ADMIN FALSE, INHERIT FALSE, SET TRUE"
        )
        conn.execute("RESET ROLE")
        assert conn.execute(
            """
            SELECT count(*)
            FROM pg_auth_members membership
            JOIN pg_roles granted ON granted.oid = membership.roleid
            JOIN pg_roles member_role ON member_role.oid = membership.member
            WHERE granted.rolname = 'workflow_research_evidence_owner'
              AND member_role.rolname = 'workflow_migration_owner'
            """
        ).fetchone()[0] == 2
        with pytest.raises(Exception, match="exact canonical v59 owner membership"):
            pg.apply_v58_research_scenario_input_evaluation(conn)
        conn.execute(
            psycopg.sql.SQL(
                "REVOKE workflow_research_evidence_owner "
                "FROM workflow_migration_owner GRANTED BY {}"
            ).format(psycopg.sql.Identifier(grantor))
        )
        conn.execute(
            psycopg.sql.SQL(
                "REVOKE workflow_research_evidence_owner FROM {}"
            ).format(psycopg.sql.Identifier(grantor))
        )

        # 9-10. Runtime can reach neither migration nor owner authority.
        for granted_role in (
            "workflow_migration_owner",
            "workflow_research_evidence_owner",
        ):
            conn.execute(
                psycopg.sql.SQL(
                    "GRANT {} TO workflow_automation_roi_runtime "
                    "WITH ADMIN FALSE, INHERIT FALSE, SET TRUE"
                ).format(psycopg.sql.Identifier(granted_role))
            )
            with pytest.raises(
                Exception, match="runtime role-membership escalation paths"
            ):
                pg.apply_v58_research_scenario_input_evaluation(conn)
            conn.execute(
                psycopg.sql.SQL(
                    "REVOKE {} FROM workflow_automation_roi_runtime"
                ).format(psycopg.sql.Identifier(granted_role))
            )

        # 11-12. ACL closure remains owner-only for tables and functions.
        conn.execute(
            psycopg.sql.SQL(
                "GRANT SELECT ON "
                "research_evidence_scenario_input_evaluation TO {}"
            ).format(psycopg.sql.Identifier(unrelated))
        )
        with pytest.raises(Exception, match="tables have non-owner ACL"):
            pg.apply_v58_research_scenario_input_evaluation(conn)
        conn.execute(
            psycopg.sql.SQL(
                "REVOKE SELECT ON "
                "research_evidence_scenario_input_evaluation FROM {}"
            ).format(psycopg.sql.Identifier(unrelated))
        )
        conn.execute(
            psycopg.sql.SQL(
                "GRANT EXECUTE ON FUNCTION "
                "research_evidence_create_scenario_input_evaluation(jsonb) TO {}"
            ).format(psycopg.sql.Identifier(unrelated))
        )
        with pytest.raises(Exception, match="functions have non-owner ACL"):
            pg.apply_v58_research_scenario_input_evaluation(conn)


def _binding(
    conn,
    evidence,
    *,
    input_key="input-a",
    binding_set_id="scenario-set",
    request_id="binding-a",
    **changes,
):
    v57fx._review(
        conn, evidence, request_id=f"review-{request_id}"
    )
    v57fx._freshness(
        conn, evidence, request_id=f"freshness-{request_id}"
    )
    command_changes = {
        "consumer_contract": "scenario_input",
        "consumer_contract_version": "scenario-observation.v1",
        "binding_set_id": binding_set_id,
        "input_key": input_key,
        "observation_identity_version": "opaque-observation.v1",
        "observation_identity_fingerprint": "a" * 64,
        "policy_fingerprint": "b" * 64,
    }
    command_changes.update(changes)
    return binding_service.record_consumer_input_binding(
        conn,
        v57fx._command(
            evidence,
            request_id=request_id,
            **command_changes,
        ),
    )


def _clone_binding_with_fixed_identity(
    conn,
    *,
    template_id,
    binding_id,
    binding_set_id,
    input_key,
    request_id,
):
    return conn.execute(
        """
        INSERT INTO research_evidence_consumer_input_binding
        SELECT (
            jsonb_populate_record(
                NULL::research_evidence_consumer_input_binding,
                (
                    to_jsonb(template)
                    || jsonb_build_object(
                        'id', %s::uuid,
                        'binding_set_id', %s::text,
                        'input_key', %s::text,
                        'request_id', %s::text
                    )
                )
                - 'binding_sequence'
                - 'supersedes_binding_id'
                - 'evaluated_at'
            )
        ).*
        FROM research_evidence_consumer_input_binding template
        WHERE template.id = %s
        RETURNING id::text
        """,
        (
            binding_id,
            binding_set_id,
            input_key,
            request_id,
            template_id,
        ),
    ).fetchone()[0]


def _manifest(
    conn,
    project_id,
    keys=("input-a",),
    *,
    request_id="manifest-1",
):
    return service.register_scenario_input_manifest(
        conn,
        ScenarioInputManifestRegistration(
            project_id=project_id,
            request_id=request_id,
            namespace="scenario.expected-inputs",
            version="1",
            input_keys=keys,
            registered_by="operator",
        ),
    )


def _evaluation_request(
    project_id,
    manifest_id,
    bindings,
    *,
    request_id="evaluation-1",
    descriptor=None,
    dependence="not_assessed",
):
    return ScenarioInputEvaluationRequest(
        project_id=project_id,
        request_id=request_id,
        manifest_id=manifest_id,
        descriptor=OpaqueHypothesisDescriptor(
            namespace="caller.opaque-hypothesis",
            descriptor_version="draft-v1",
            descriptor=descriptor or {"opaque": "caller-declared"},
            declared_by="operator",
        ),
        selected_bindings=tuple(
            ScenarioInputBindingSelection(
                binding_id=binding.id,
                dependence_declaration=dependence,
                rationale=f"{dependence} was caller-declared.",
            )
            for binding in bindings
        ),
        freshness_as_of=AS_OF,
    )


def _seed_one(conn, *, tag="base"):
    evidence = v57fx._seed_evidence(conn, tag=tag)
    binding = _binding(conn, evidence, request_id=f"binding-{tag}")
    manifest = _manifest(
        conn, evidence["project"], request_id=f"manifest-{tag}"
    )
    return evidence, binding, manifest


def _seed_policy_binding(
    conn,
    *,
    tag,
    available=True,
    lineage_current=True,
    review_status="approved",
    freshness_status="fresh",
    drift_status="no_material_drift",
    disposition="meets_contract",
):
    evidence = v57fx._seed_evidence(conn, tag=tag)
    approval_seeded = (
        not available
        or not lineage_current
        or review_status == "withdrawn"
    )
    if approval_seeded:
        v57fx._review(
            conn,
            evidence,
            decision="approved",
            request_id=f"review-approved-{tag}",
        )
    if not available:
        v57fx.ev_repo.insert_retention_event(
            conn,
            project_id=evidence["project"],
            event_type="tombstone",
            candidate_fact_revision_id=evidence["fact"],
            reason="Policy-vector unavailability.",
            created_by="operator",
        )
    if not lineage_current:
        conn.execute(
            """
            INSERT INTO research_fact_metadata_revision(
                project_id, candidate_fact_revision_id,
                supersedes_metadata_revision_id, created_by
            ) VALUES (%s, %s, %s, 'operator')
            """,
            (
                evidence["project"],
                evidence["fact"],
                evidence["fact_metadata"],
            ),
        )
    if (
        review_status != "not_assessed"
        and not (approval_seeded and review_status == "approved")
    ):
        v57fx._review(
            conn,
            evidence,
            decision=review_status,
            request_id=f"review-{tag}",
        )
    if freshness_status != "unknown":
        fresh_through = (
            AS_OF + timedelta(days=1)
            if freshness_status == "fresh"
            else AS_OF - timedelta(days=1)
        )
        v57fx._freshness(
            conn,
            evidence,
            request_id=f"freshness-{tag}",
            fresh_through=fresh_through,
            drift_status=drift_status,
        )
    binding = binding_service.record_consumer_input_binding(
        conn,
        v57fx._command(
            evidence,
            request_id=f"binding-{tag}",
            consumer_contract="scenario_input",
            consumer_contract_version="scenario-observation.v1",
            binding_set_id=f"set-{tag}",
            input_key="input-a",
            observation_identity_version="opaque-observation.v1",
            observation_identity_fingerprint="a" * 64,
            policy_fingerprint="b" * 64,
            consumer_disposition=disposition,
            disposition_reasons=(f"vector_{tag}",),
        ),
    )
    manifest = _manifest(
        conn,
        evidence["project"],
        request_id=f"manifest-{tag}",
    )
    return evidence, binding, manifest


def _wait_until(predicate, *, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("timed out waiting for PostgreSQL lock state")


@contextlib.contextmanager
def _expected_database_failure(conn, savepoint):
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        yield
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    else:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise AssertionError("database statement unexpectedly succeeded")


def _project_state(conn, project_id):
    header_count, child_count, allocator_count, last_sequence = conn.execute(
        """
        SELECT
            (SELECT count(*)
             FROM research_evidence_scenario_input_evaluation
             WHERE project_id = %s),
            (SELECT count(*)
             FROM research_evidence_scenario_input_evaluation_input
             WHERE project_id = %s),
            (SELECT count(*)
             FROM
                 research_evidence_scenario_input_evaluation_sequence_allocator
             WHERE project_id = %s),
            COALESCE((
                SELECT sum(last_sequence)
                FROM
                    research_evidence_scenario_input_evaluation_sequence_allocator
                WHERE project_id = %s
            ), 0)
        """,
        (project_id, project_id, project_id, project_id),
    ).fetchone()
    return header_count, child_count, allocator_count, last_sequence


def _project_provenance_state(conn, project_id):
    headers = conn.execute(
        """
        SELECT id::text, request_id, request_payload_json,
               request_fingerprint, manifest_id::text, manifest_version,
               manifest_cardinality, manifest_fingerprint,
               descriptor_namespace, descriptor_version, descriptor_json,
               descriptor_fingerprint, descriptor_declared_by,
               consumer_contract_version, binding_set_id,
               binding_policy_identifier, binding_policy_version,
               binding_policy_fingerprint, binding_evaluator_version,
               freshness_as_of, evaluation_policy_identifier,
               evaluation_policy_version, evaluation_policy_parameters_json,
               evaluation_policy_fingerprint, evaluator_version,
               evaluation_status, reason_codes_json, evaluation_sequence,
               predecessor_evaluation_id::text, evaluated_at
        FROM research_evidence_scenario_input_evaluation
        WHERE project_id = %s
        ORDER BY evaluation_sequence, id
        """,
        (project_id,),
    ).fetchall()
    children = conn.execute(
        """
        SELECT evaluation_id::text, manifest_id::text, input_key,
               selected_binding_id::text, consumer_contract, binding_set_id,
               binding_sequence, selected_binding_has_successor,
               availability_status, lineage_is_current, review_status,
               freshness_status, drift_status, binding_disposition,
               dependence_declaration, dependence_rationale, input_status,
               reason_codes_json, linked_at
        FROM research_evidence_scenario_input_evaluation_input
        WHERE project_id = %s
        ORDER BY evaluation_id, input_key COLLATE "C", selected_binding_id
        """,
        (project_id,),
    ).fetchall()
    allocators = conn.execute(
        """
        SELECT manifest_id::text, binding_set_id, descriptor_namespace,
               descriptor_version, descriptor_fingerprint, last_sequence,
               last_evaluation_id::text, allocator_updated_at
        FROM research_evidence_scenario_input_evaluation_sequence_allocator
        WHERE project_id = %s
        ORDER BY manifest_id, binding_set_id, descriptor_namespace,
                 descriptor_version, descriptor_fingerprint
        """,
        (project_id,),
    ).fetchall()
    return headers, children, allocators


def _manifest_state(conn, project_id):
    return conn.execute(
        """
        SELECT
            (SELECT count(*)
             FROM research_evidence_scenario_input_manifest
             WHERE project_id = %s),
            (SELECT count(*)
             FROM research_evidence_scenario_input_manifest_item
             WHERE project_id = %s)
        """,
        (project_id, project_id),
    ).fetchone()


def _corrupt_v58_immutable(
    conn, *, table, mutation_trigger, statement, params
):
    prior = pg._begin_autocommit(conn)
    try:
        conn.execute(
            f"ALTER TABLE {table} DISABLE TRIGGER {mutation_trigger}"
        )
        conn.execute("SET session_replication_role = replica")
        try:
            conn.execute(statement, params)
        finally:
            conn.execute("SET session_replication_role = origin")
            conn.execute(
                f"ALTER TABLE {table} ENABLE ALWAYS TRIGGER "
                f"{mutation_trigger}"
            )
    finally:
        pg._restore_autocommit(conn, prior)


def test_v58_objects_reapply_and_exact_manifest_database_parity(
    conn, schema_v58
):
    assert all(pg.table_exists(conn, schema_v58, table) for table in TABLES)
    evidence = v57fx._seed_evidence(conn, tag="manifest-parity")
    manifest = _manifest(
        conn,
        evidence["project"],
        ("zeta", "áccent", "alpha"),
        request_id="manifest-parity",
    )
    expected = canonical_manifest_descriptor(
        "scenario.expected-inputs", "1", ("zeta", "áccent", "alpha")
    )
    assert manifest.input_keys == ("alpha", "zeta", "áccent")
    assert manifest.input_cardinality == 3
    assert manifest.structural_descriptor == expected
    assert manifest.manifest_fingerprint == manifest_fingerprint(expected)
    assert [item.item_ordinal for item in manifest.items] == [1, 2, 3]
    assert all(item.linked_at == manifest.registered_at for item in manifest.items)
    pg.apply_v58_research_scenario_input_evaluation(conn)


def test_named_manifest_descriptor_and_digest_are_persisted_database_values(
    conn, schema_v58
):
    project_id = pg.insert_project(conn, name="manifest-literal-vector")
    manifest = service.register_scenario_input_manifest(
        conn,
        ScenarioInputManifestRegistration(
            project_id=project_id,
            request_id="manifest-literal-vector",
            namespace="scenario.ns",
            version="v1",
            input_keys=("zeta", "áccent", "alpha"),
            registered_by="operator",
        ),
    )
    stored = conn.execute(
        """
        SELECT structural_descriptor, manifest_fingerprint,
               canonical_input_keys_json, input_cardinality
        FROM research_evidence_scenario_input_manifest
        WHERE id = %s
        """,
        (manifest.id,),
    ).fetchone()
    assert stored == (
        MANIFEST_VECTOR_DESCRIPTOR,
        MANIFEST_VECTOR_SHA256,
        ["alpha", "zeta", "áccent"],
        3,
    )


def test_manifest_retry_conflict_and_append_only_items(conn, schema_v58):
    evidence = v57fx._seed_evidence(conn, tag="manifest-retry")
    first = _manifest(conn, evidence["project"], ("a", "b"))
    retry = _manifest(conn, evidence["project"], ("b", "a"))
    assert retry == first
    baseline = _manifest_state(conn, evidence["project"])
    with pytest.raises(Exception, match="different immutable manifest"):
        with _expected_database_failure(conn, "reject_manifest_retry"):
            _manifest(conn, evidence["project"], ("a", "changed"))
    assert _manifest_state(conn, evidence["project"]) == baseline
    with pytest.raises(Exception, match="append-only"):
        with _expected_database_failure(conn, "reject_manifest_item_update"):
            conn.execute(
                """
                UPDATE research_evidence_scenario_input_manifest_item
                SET input_key = 'forged'
                WHERE manifest_id = %s
                """,
                (first.id,),
            )
    assert _manifest_state(conn, evidence["project"]) == baseline


def test_empty_manifest_may_register_but_cannot_persist_satisfies(
    conn, schema_v58
):
    project_id = pg.insert_project(conn, name="empty-manifest")
    manifest = _manifest(conn, project_id, (), request_id="empty")
    assert manifest.input_cardinality == 0
    payload = {
        "project_id": project_id,
        "request_id": "empty-evaluation",
        "manifest_id": manifest.id,
        "descriptor": {
            "namespace": "opaque",
            "descriptor_version": "1",
            "descriptor": {"empty": True},
            "declared_by": "operator",
        },
        "selected_bindings": [],
        "freshness_as_of": AS_OF.isoformat(),
    }
    baseline = _project_state(conn, project_id)
    with pytest.raises(Exception, match="missing or incomplete"):
        with _expected_database_failure(conn, "reject_empty_manifest"):
            conn.execute(
                "SELECT * FROM "
                "research_evidence_create_scenario_input_evaluation"
                "(%s::jsonb)",
                (json.dumps(payload),),
            )
    assert _project_state(conn, project_id) == baseline


@pytest.mark.parametrize(
    ("values", "expected_status", "expected_reasons"),
    [
        ((False, True, "approved", "fresh", "no_material_drift",
          "meets_contract", False, "not_assessed"),
         "does_not_satisfy", ("evidence_unavailable", "dependence_not_assessed")),
        ((True, False, "approved", "fresh", "no_material_drift",
          "meets_contract", False, "declared_dependent"),
         "does_not_satisfy", ("lineage_not_current",
                              "dependence_declared_dependent")),
        ((True, True, "rejected", "fresh", "no_material_drift",
          "meets_contract", False, "not_assessed"),
         "does_not_satisfy", ("review_rejected", "dependence_not_assessed")),
        ((True, True, "needs_revision", "fresh", "no_material_drift",
          "meets_contract", False, "declared_dependent"),
         "does_not_satisfy", ("review_needs_revision",
                              "dependence_declared_dependent")),
        ((True, True, "withdrawn", "fresh", "no_material_drift",
          "meets_contract", False, "declared_dependent"),
         "does_not_satisfy", ("review_withdrawn",
                              "dependence_declared_dependent")),
        ((True, True, "approved", "fresh", "material_drift",
          "meets_contract", False, "not_assessed"),
         "does_not_satisfy", ("material_drift", "dependence_not_assessed")),
        ((True, True, "approved", "fresh", "no_material_drift",
          "meets_contract", True, "declared_dependent"),
         "does_not_satisfy", ("selected_binding_successor",
                              "dependence_declared_dependent")),
        ((True, True, "approved", "fresh", "no_material_drift",
          "does_not_meet_contract", False, "declared_dependent"),
         "does_not_satisfy", ("binding_does_not_meet_contract",
                              "dependence_declared_dependent")),
        ((True, True, "not_assessed", "unknown", "not_assessed",
          "indeterminate", False, "not_assessed"),
         "indeterminate", ("review_not_assessed", "freshness_unknown",
                           "drift_not_assessed", "binding_indeterminate",
                           "dependence_not_assessed")),
        ((True, True, "approved", "fresh", "indeterminate",
          "meets_contract", False, "declared_dependent"),
         "indeterminate", ("drift_indeterminate",
                           "dependence_declared_dependent")),
        ((True, True, "approved", "stale", "no_material_drift",
          "qualified", False, "declared_independent_not_verified"),
         "qualified", ("freshness_stale", "binding_qualified",
                       "dependence_declared_independent_not_verified")),
    ],
)
def test_database_policy_every_precedence_branch(
    conn, schema_v58, values, expected_status, expected_reasons
):
    row = conn.execute(
        """
        SELECT input_status, reason_codes_json
        FROM research_evidence_scenario_input_policy_state(
            %s, %s, %s, %s, %s, %s, %s, %s
        )
        """,
        values,
    ).fetchone()
    assert row[0] == expected_status
    assert tuple(row[1]) == expected_reasons
    assert tuple(
        sorted(row[1], key=REASON_ORDER.index)
    ) == expected_reasons


@pytest.mark.parametrize(
    ("dependence", "expected_status", "reason"),
    [
        ("not_assessed", "indeterminate", "dependence_not_assessed"),
        ("declared_dependent", "qualified", "dependence_declared_dependent"),
        (
            "declared_independent_not_verified",
            "qualified",
            "dependence_declared_independent_not_verified",
        ),
    ],
)
def test_persisted_policy_provenance_and_fail_closed_dependence(
    conn, schema_v58, dependence, expected_status, reason
):
    evidence, binding, manifest = _seed_one(conn, tag=dependence)
    record = service.create_scenario_input_evaluation(
        conn,
        _evaluation_request(
            evidence["project"],
            manifest.id,
            (binding,),
            request_id=f"evaluation-{dependence}",
            dependence=dependence,
        ),
    )
    assert record.evaluation_policy_identifier == EVALUATION_POLICY_IDENTIFIER
    assert record.evaluation_policy_version == EVALUATION_POLICY_VERSION
    assert record.evaluation_policy_parameters == EVALUATION_POLICY_PARAMETERS
    assert record.evaluation_policy_fingerprint == EVALUATION_POLICY_FINGERPRINT
    assert record.evaluator_version == EVALUATOR_VERSION
    assert record.evaluation_status == expected_status
    assert record.inputs[0].input_status == expected_status
    assert reason in record.reason_codes
    assert reason in record.inputs[0].reason_codes
    assert record.evaluation_status != "satisfies"


@pytest.mark.parametrize(
    (
        "tag", "state", "dependence", "successor",
        "expected_status", "expected_reasons",
    ),
    [
        (
            "unavailable", {"available": False}, "declared_dependent", False,
            "does_not_satisfy",
            ("evidence_unavailable", "dependence_declared_dependent"),
        ),
        (
            "lineage", {"lineage_current": False}, "declared_dependent", False,
            "does_not_satisfy",
            ("lineage_not_current", "dependence_declared_dependent"),
        ),
        (
            "rejected", {"review_status": "rejected"}, "not_assessed", False,
            "does_not_satisfy",
            ("review_rejected", "dependence_not_assessed"),
        ),
        (
            "needs-revision", {"review_status": "needs_revision"},
            "declared_dependent", False, "does_not_satisfy",
            ("review_needs_revision", "dependence_declared_dependent"),
        ),
        (
            "withdrawn", {"review_status": "withdrawn"},
            "declared_dependent", False, "does_not_satisfy",
            ("review_withdrawn", "dependence_declared_dependent"),
        ),
        (
            "material-drift", {"drift_status": "material_drift"},
            "not_assessed", False, "does_not_satisfy",
            ("material_drift", "dependence_not_assessed"),
        ),
        (
            "successor", {}, "declared_dependent", True,
            "does_not_satisfy",
            ("selected_binding_successor", "dependence_declared_dependent"),
        ),
        (
            "does-not-meet", {"disposition": "does_not_meet_contract"},
            "declared_dependent", False, "does_not_satisfy",
            (
                "binding_does_not_meet_contract",
                "dependence_declared_dependent",
            ),
        ),
        (
            "review-not-assessed", {"review_status": "not_assessed"},
            "declared_dependent", False, "indeterminate",
            ("review_not_assessed", "dependence_declared_dependent"),
        ),
        (
            "unknown", {"freshness_status": "unknown"},
            "declared_dependent", False, "indeterminate",
            (
                "freshness_unknown", "drift_not_assessed",
                "dependence_declared_dependent",
            ),
        ),
        (
            "drift-not-assessed", {"drift_status": "not_assessed"},
            "declared_dependent", False, "indeterminate",
            ("drift_not_assessed", "dependence_declared_dependent"),
        ),
        (
            "drift-indeterminate", {"drift_status": "indeterminate"},
            "declared_dependent", False, "indeterminate",
            ("drift_indeterminate", "dependence_declared_dependent"),
        ),
        (
            "binding-indeterminate", {"disposition": "indeterminate"},
            "declared_dependent", False, "indeterminate",
            ("binding_indeterminate", "dependence_declared_dependent"),
        ),
        (
            "stale", {"freshness_status": "stale"},
            "declared_independent_not_verified", False, "qualified",
            (
                "freshness_stale",
                "dependence_declared_independent_not_verified",
            ),
        ),
        (
            "binding-qualified", {"disposition": "qualified"},
            "declared_dependent", False, "qualified",
            ("binding_qualified", "dependence_declared_dependent"),
        ),
        (
            "meets-contract", {}, "not_assessed", False, "indeterminate",
            ("dependence_not_assessed",),
        ),
        (
            "approved-fresh-current", {},
            "declared_independent_not_verified", False, "qualified",
            ("dependence_declared_independent_not_verified",),
        ),
    ],
)
def test_every_policy_branch_is_persisted_with_exact_header_child_parity(
    conn,
    schema_v58,
    tag,
    state,
    dependence,
    successor,
    expected_status,
    expected_reasons,
):
    evidence, binding, manifest = _seed_policy_binding(
        conn, tag=tag, **state
    )
    if successor:
        binding_service.record_consumer_input_binding(
            conn,
            v57fx._command(
                evidence,
                request_id=f"successor-{tag}",
                consumer_contract="scenario_input",
                consumer_contract_version="scenario-observation.v1",
                binding_set_id=binding.binding_set_id,
                input_key=binding.input_key,
                observation_identity_version="opaque-observation.v1",
                observation_identity_fingerprint="a" * 64,
                policy_fingerprint="b" * 64,
            ),
        )
    record = service.create_scenario_input_evaluation(
        conn,
        _evaluation_request(
            evidence["project"],
            manifest.id,
            (binding,),
            request_id=f"evaluation-{tag}",
            dependence=dependence,
        ),
    )
    assert record.evaluation_policy_identifier == EVALUATION_POLICY_IDENTIFIER
    assert record.evaluation_policy_version == EVALUATION_POLICY_VERSION
    assert record.evaluation_policy_parameters == EVALUATION_POLICY_PARAMETERS
    assert record.evaluation_policy_fingerprint == EVALUATION_POLICY_FINGERPRINT
    assert record.evaluator_version == EVALUATOR_VERSION
    assert record.evaluation_status == expected_status
    assert record.reason_codes == expected_reasons
    assert len(record.inputs) == 1
    assert record.inputs[0].input_status == expected_status
    assert record.inputs[0].reason_codes == expected_reasons
    header = conn.execute(
        """
        SELECT evaluation_policy_identifier, evaluation_policy_version,
               evaluation_policy_parameters_json,
               evaluation_policy_fingerprint, evaluator_version,
               evaluation_status, reason_codes_json
        FROM research_evidence_scenario_input_evaluation
        WHERE id = %s
        """,
        (record.id,),
    ).fetchone()
    assert header == (
        EVALUATION_POLICY_IDENTIFIER,
        EVALUATION_POLICY_VERSION,
        EVALUATION_POLICY_PARAMETERS,
        EVALUATION_POLICY_FINGERPRINT,
        EVALUATOR_VERSION,
        expected_status,
        list(expected_reasons),
    )
    children = conn.execute(
        """
        SELECT input_status, reason_codes_json
        FROM research_evidence_scenario_input_evaluation_input
        WHERE evaluation_id = %s
        ORDER BY input_key COLLATE "C", selected_binding_id
        """,
        (record.id,),
    ).fetchall()
    assert children == [(expected_status, list(expected_reasons))]


def test_opaque_descriptor_is_database_normalized_and_noncanonical(
    conn, schema_v58
):
    evidence, binding, manifest = _seed_one(conn, tag="descriptor")
    record = service.create_scenario_input_evaluation(
        conn,
        _evaluation_request(
            evidence["project"],
            manifest.id,
            (binding,),
            descriptor={"z": 1, "a": {"decimal": 1.00}},
        ),
    )
    expected = conn.execute(
        """
        SELECT encode(
            sha256(convert_to(%s::jsonb::text, 'UTF8')), 'hex'
        )
        """,
        (json.dumps({"a": {"decimal": 1.0}, "z": 1}),),
    ).fetchone()[0]
    assert record.descriptor_fingerprint == expected
    assert record.descriptor == {"a": {"decimal": 1.0}, "z": 1}
    assert "canonical" not in record.request_payload["descriptor"]


def test_named_jsonb_descriptor_and_request_fingerprint_vectors(
    conn, schema_v58
):
    descriptor_normalized, descriptor_fingerprint = conn.execute(
        """
        SELECT %s::jsonb::text,
               encode(sha256(convert_to(%s::jsonb::text, 'UTF8')), 'hex')
        """,
        (OPAQUE_VECTOR_JSON, OPAQUE_VECTOR_JSON),
    ).fetchone()
    assert descriptor_normalized == OPAQUE_VECTOR_NORMALIZED
    assert descriptor_fingerprint == OPAQUE_VECTOR_SHA256

    request_normalized, request_fingerprint = conn.execute(
        """
        SELECT %s::jsonb::text,
               encode(sha256(convert_to(%s::jsonb::text, 'UTF8')), 'hex')
        """,
        (REQUEST_VECTOR_JSON, REQUEST_VECTOR_JSON),
    ).fetchone()
    assert request_normalized == REQUEST_VECTOR_NORMALIZED
    assert request_fingerprint == REQUEST_VECTOR_SHA256


def test_independent_literal_vectors_reach_persisted_header_and_children(
    conn, schema_v58
):
    project_id = pg.insert_project(
        conn,
        name="scenario-input-literal-vector",
        project_id=PERSISTED_VECTOR_PROJECT_ID,
    )
    input_keys = ("alpha", "zeta", "áccent")
    fixed_binding_ids = []
    for index, (input_key, binding_id) in enumerate(
        zip(input_keys, PERSISTED_VECTOR_BINDING_IDS)
    ):
        evidence = v57fx._seed_evidence(
            conn,
            tag=f"literal-vector-{index}",
            project_id=project_id,
        )
        template = _binding(
            conn,
            evidence,
            input_key=f"template-{index}",
            binding_set_id=f"template-vector-set-{index}",
            request_id=f"template-vector-binding-{index}",
        )
        fixed_binding_ids.append(
            _clone_binding_with_fixed_identity(
                conn,
                template_id=template.id,
                binding_id=binding_id,
                binding_set_id="vector-set",
                input_key=input_key,
                request_id=f"literal-vector-binding-{index}",
            )
        )
    assert tuple(fixed_binding_ids) == PERSISTED_VECTOR_BINDING_IDS

    conn.execute(
        """
        INSERT INTO research_evidence_scenario_input_manifest(
            id, project_id, registration_request_id, manifest_namespace,
            manifest_version, canonical_input_keys_json, registered_by
        ) VALUES (%s, %s, 'literal-vector-manifest', 'scenario.ns',
                  'v1', %s::jsonb, 'vector-operator')
        """,
        (
            PERSISTED_VECTOR_MANIFEST_ID,
            project_id,
            json.dumps(("zeta", "áccent", "alpha"), ensure_ascii=False),
        ),
    )
    manifest = service.repo.get_manifest(
        conn, manifest_id=PERSISTED_VECTOR_MANIFEST_ID
    )
    assert manifest.id == PERSISTED_VECTOR_MANIFEST_ID
    assert manifest.version == "v1"
    assert manifest.manifest_fingerprint == MANIFEST_VECTOR_SHA256

    declarations = {
        PERSISTED_VECTOR_BINDING_IDS[0]: (
            "not_assessed",
            "No dependence assessment was performed.",
        ),
        PERSISTED_VECTOR_BINDING_IDS[1]: (
            "declared_dependent",
            "Inputs are operationally coupled.",
        ),
        PERSISTED_VECTOR_BINDING_IDS[2]: (
            "declared_independent_not_verified",
            "Caller declared independence; not verified.",
        ),
    }
    record = service.create_scenario_input_evaluation(
        conn,
        ScenarioInputEvaluationRequest(
            project_id=project_id,
            request_id=PERSISTED_VECTOR_REQUEST_ID,
            manifest_id=PERSISTED_VECTOR_MANIFEST_ID,
            descriptor=OpaqueHypothesisDescriptor(
                namespace="opaque.vector",
                descriptor_version="v1",
                descriptor={"z": [True, "x"], "a": 1},
                declared_by="vector-operator",
            ),
            selected_bindings=tuple(
                ScenarioInputBindingSelection(
                    binding_id=binding_id,
                    dependence_declaration=declarations[binding_id][0],
                    rationale=declarations[binding_id][1],
                )
                for binding_id in reversed(PERSISTED_VECTOR_BINDING_IDS)
            ),
            freshness_as_of=AS_OF,
        ),
    )

    literal_policy_parameters = json.loads(PERSISTED_VECTOR_POLICY_JSON)
    expected_selected = [
        {
            "binding_id": binding_id,
            "dependence_declaration": declarations[binding_id][0],
            "rationale": declarations[binding_id][1],
        }
        for binding_id in PERSISTED_VECTOR_BINDING_IDS
    ]
    expected_request_payload = {
        "project_id": PERSISTED_VECTOR_PROJECT_ID,
        "request_id": PERSISTED_VECTOR_REQUEST_ID,
        "manifest": {
            "id": PERSISTED_VECTOR_MANIFEST_ID,
            "version": "v1",
            "fingerprint": MANIFEST_VECTOR_SHA256,
        },
        "descriptor": {
            "namespace": "opaque.vector",
            "descriptor_version": "v1",
            "descriptor": {"a": 1, "z": [True, "x"]},
            "fingerprint": OPAQUE_VECTOR_SHA256,
            "declared_by": "vector-operator",
        },
        "selected_bindings": expected_selected,
        "binding_contract": {
            "consumer_contract": "scenario_input",
            "consumer_contract_version": "scenario-observation.v1",
            "binding_set_id": "vector-set",
            "policy_identifier": "binding-policy",
            "policy_version": "1",
            "policy_fingerprint": "b" * 64,
            "evaluator_version": "binding-evaluator.v1",
        },
        "evaluation_policy": {
            "identifier": "scenario_input.evidence_evaluation",
            "version": "1",
            "parameters": literal_policy_parameters,
            "fingerprint": PERSISTED_VECTOR_POLICY_SHA256,
            "evaluator_version":
                "scenario_input.evidence_evaluation.evaluator.v1",
        },
        "freshness_as_of": "2026-01-02T00:00:00.000000Z",
    }
    header = conn.execute(
        """
        SELECT descriptor_json, descriptor_json::text,
               descriptor_fingerprint, request_payload_json,
               request_fingerprint, manifest_id::text, manifest_version,
               manifest_fingerprint, evaluation_policy_identifier,
               evaluation_policy_version, evaluation_policy_parameters_json,
               evaluation_policy_fingerprint, evaluator_version,
               evaluation_status, reason_codes_json
        FROM research_evidence_scenario_input_evaluation
        WHERE id = %s
        """,
        (record.id,),
    ).fetchone()
    assert header == (
        {"a": 1, "z": [True, "x"]},
        OPAQUE_VECTOR_NORMALIZED,
        OPAQUE_VECTOR_SHA256,
        expected_request_payload,
        PERSISTED_VECTOR_REQUEST_SHA256,
        PERSISTED_VECTOR_MANIFEST_ID,
        "v1",
        MANIFEST_VECTOR_SHA256,
        "scenario_input.evidence_evaluation",
        "1",
        literal_policy_parameters,
        PERSISTED_VECTOR_POLICY_SHA256,
        "scenario_input.evidence_evaluation.evaluator.v1",
        "indeterminate",
        [
            "dependence_not_assessed",
            "dependence_declared_dependent",
            "dependence_declared_independent_not_verified",
        ],
    )
    children = conn.execute(
        """
        SELECT input_key, selected_binding_id::text,
               dependence_declaration, dependence_rationale,
               input_status, reason_codes_json
        FROM research_evidence_scenario_input_evaluation_input
        WHERE evaluation_id = %s
        ORDER BY input_key COLLATE "C"
        """,
        (record.id,),
    ).fetchall()
    assert children == [
        (
            "alpha",
            PERSISTED_VECTOR_BINDING_IDS[0],
            "not_assessed",
            "No dependence assessment was performed.",
            "indeterminate",
            ["dependence_not_assessed"],
        ),
        (
            "zeta",
            PERSISTED_VECTOR_BINDING_IDS[1],
            "declared_dependent",
            "Inputs are operationally coupled.",
            "qualified",
            ["dependence_declared_dependent"],
        ),
        (
            "áccent",
            PERSISTED_VECTOR_BINDING_IDS[2],
            "declared_independent_not_verified",
            "Caller declared independence; not verified.",
            "qualified",
            ["dependence_declared_independent_not_verified"],
        ),
    ]


def test_exact_uuid_selection_retry_sequence_and_predecessor(conn, schema_v58):
    evidence, binding, manifest = _seed_one(conn, tag="sequence")
    request = _evaluation_request(evidence["project"], manifest.id, (binding,))
    first = service.create_scenario_input_evaluation(conn, request)
    assert service.create_scenario_input_evaluation(conn, request) == first
    second = service.create_scenario_input_evaluation(
        conn,
        _evaluation_request(
            evidence["project"],
            manifest.id,
            (binding,),
            request_id="evaluation-2",
            descriptor={"opaque": "caller-declared"},
        ),
    )
    assert (first.evaluation_sequence, second.evaluation_sequence) == (1, 2)
    assert second.predecessor_evaluation_id == first.id
    baseline = _project_state(conn, evidence["project"])
    chain = conn.execute(
        """
        SELECT id::text, evaluation_sequence, predecessor_evaluation_id::text
        FROM research_evidence_scenario_input_evaluation
        WHERE project_id = %s
        ORDER BY evaluation_sequence
        """,
        (evidence["project"],),
    ).fetchall()
    with pytest.raises(Exception, match="different immutable evaluation"):
        with _expected_database_failure(conn, "reject_retry_conflict"):
            service.create_scenario_input_evaluation(
                conn,
                _evaluation_request(
                    evidence["project"],
                    manifest.id,
                    (binding,),
                    descriptor={"changed": True},
                ),
            )
    assert _project_state(conn, evidence["project"]) == baseline
    assert conn.execute(
        """
        SELECT id::text, evaluation_sequence, predecessor_evaluation_id::text
        FROM research_evidence_scenario_input_evaluation
        WHERE project_id = %s
        ORDER BY evaluation_sequence
        """,
        (evidence["project"],),
    ).fetchall() == chain


def test_clean_evaluation_history_reapply_reaches_persistence_checks(
    conn, schema_v58
):
    evidence, binding, manifest = _seed_one(conn, tag="clean-reapply")
    record = service.create_scenario_input_evaluation(
        conn,
        _evaluation_request(
            evidence["project"],
            manifest.id,
            (binding,),
            request_id="clean-reapply",
        ),
    )
    conn.commit()
    stored_fingerprint, recomputed_fingerprint = conn.execute(
        """
        SELECT request_fingerprint,
               encode(sha256(convert_to(request_payload_json::text, 'UTF8')),
                      'hex')
        FROM research_evidence_scenario_input_evaluation
        WHERE id = %s
        """,
        (record.id,),
    ).fetchone()
    assert stored_fingerprint == record.request_fingerprint
    assert stored_fingerprint == recomputed_fingerprint
    pg.apply_v58_research_scenario_input_evaluation(conn)
    assert service.repo.get_evaluation(conn, evaluation_id=record.id) == record


@pytest.mark.parametrize(
    "mutation",
    (
        lambda payload: payload.pop("manifest_id"),
        lambda payload: payload.update({"extra": True}),
        lambda payload: payload.update({"selected_bindings": []}),
        lambda payload: payload["selected_bindings"][0].update(
            {"rationale": " "}
        ),
        lambda payload: payload["selected_bindings"].append(
            dict(payload["selected_bindings"][0])
        ),
    ),
)
def test_malformed_structural_request_is_atomic(
    conn, schema_v58, mutation
):
    evidence, binding, manifest = _seed_one(
        conn, tag=f"malformed-{uuid.uuid4().hex[:6]}"
    )
    payload = _evaluation_request(
        evidence["project"], manifest.id, (binding,)
    ).canonical_database_payload()
    mutation(payload)
    baseline = _project_state(conn, evidence["project"])
    with pytest.raises(Exception):
        with _expected_database_failure(conn, "reject_malformed_request"):
            conn.execute(
                "SELECT * FROM "
                "research_evidence_create_scenario_input_evaluation(%s)",
                (json.dumps(payload),),
            )
    assert _project_state(conn, evidence["project"]) == baseline


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("binding_set_id", "other-set"),
        ("policy_identifier", "other-policy"),
        ("policy_version", "2"),
        ("policy_fingerprint", "c" * 64),
        ("evaluator_version", "other-evaluator"),
        ("freshness_as_of", AS_OF + timedelta(seconds=1)),
    ],
)
def test_cross_binding_coherence_is_rejected_atomically(
    conn, schema_v58, field, value
):
    first_evidence = v57fx._seed_evidence(conn, tag=f"coherence-a-{field}")
    second_evidence = v57fx._seed_evidence(
        conn,
        tag=f"coherence-b-{field}",
        project_id=first_evidence["project"],
    )
    first = _binding(
        conn, first_evidence, input_key="input-a",
        request_id=f"binding-a-{field}"
    )
    changes = {field: value}
    second = _binding(
        conn,
        second_evidence,
        input_key="input-b",
        request_id=f"binding-b-{field}",
        **changes,
    )
    manifest = _manifest(
        conn,
        first_evidence["project"],
        ("input-a", "input-b"),
        request_id=f"manifest-{field}",
    )
    baseline = _project_state(conn, first_evidence["project"])
    with pytest.raises(Exception):
        with _expected_database_failure(
            conn, f"reject_cross_coherence_{field}"
        ):
            service.create_scenario_input_evaluation(
                conn,
                _evaluation_request(
                    first_evidence["project"],
                    manifest.id,
                    (first, second),
                    request_id=f"evaluation-{field}",
                ),
            )
    assert _project_state(conn, first_evidence["project"]) == baseline


def test_cross_project_contract_and_extra_manifest_key_are_rejected(
    conn, schema_v58
):
    evidence, binding, manifest = _seed_one(conn, tag="cross-scope")
    other = v57fx._seed_evidence(conn, tag="cross-project")
    other_binding = _binding(
        conn, other, input_key="input-a", request_id="other-project-binding"
    )
    baseline = _project_state(conn, evidence["project"])
    with pytest.raises(Exception):
        with _expected_database_failure(conn, "reject_cross_project"):
            service.create_scenario_input_evaluation(
                conn,
                _evaluation_request(
                    evidence["project"],
                    manifest.id,
                    (other_binding,),
                    request_id="cross-project",
                ),
            )
    assert _project_state(conn, evidence["project"]) == baseline

    report_binding = binding_service.record_consumer_input_binding(
        conn,
        v57fx._command(
            evidence,
            request_id="report-contract",
            input_key="input-a",
            binding_set_id="scenario-set",
            policy_fingerprint="b" * 64,
        ),
    )
    with pytest.raises(Exception):
        with _expected_database_failure(conn, "reject_cross_contract"):
            service.create_scenario_input_evaluation(
                conn,
                _evaluation_request(
                    evidence["project"],
                    manifest.id,
                    (report_binding,),
                    request_id="cross-contract",
                ),
            )
    assert _project_state(conn, evidence["project"]) == baseline

    second_evidence = v57fx._seed_evidence(
        conn, tag="extra-key", project_id=evidence["project"]
    )
    extra = _binding(
        conn,
        second_evidence,
        input_key="input-b",
        request_id="extra-key-binding",
    )
    with pytest.raises(Exception):
        with _expected_database_failure(conn, "reject_extra_key"):
            service.create_scenario_input_evaluation(
                conn,
                _evaluation_request(
                    evidence["project"],
                    manifest.id,
                    (binding, extra),
                    request_id="extra-key",
                ),
            )
    assert _project_state(conn, evidence["project"]) == baseline


def test_direct_dml_cannot_forge_derived_header_or_child(conn, schema_v58):
    evidence, binding, manifest = _seed_one(conn, tag="direct-forgery")
    payload = _evaluation_request(
        evidence["project"], manifest.id, (binding,)
    ).canonical_database_payload()
    baseline = _project_state(conn, evidence["project"])
    with pytest.raises(Exception, match="server-owned"):
        with _expected_database_failure(conn, "reject_forged_header"):
            conn.execute(
                """
                INSERT INTO research_evidence_scenario_input_evaluation(
                    request_payload_json, evaluation_status
                ) VALUES (%s::jsonb, 'satisfies')
                """,
                (json.dumps(payload),),
            )
    assert _project_state(conn, evidence["project"]) == baseline


def test_ordinary_direct_header_insert_preserves_database_derivation(
    conn, schema_v58
):
    evidence, binding, manifest = _seed_one(conn, tag="direct-legitimate")
    request = _evaluation_request(
        evidence["project"],
        manifest.id,
        (binding,),
        request_id="direct-legitimate",
    )
    evaluation_id = conn.execute(
        """
        INSERT INTO research_evidence_scenario_input_evaluation(
            request_payload_json
        ) VALUES (%s::jsonb)
        RETURNING id::text
        """,
        (json.dumps(request.canonical_database_payload()),),
    ).fetchone()[0]
    record = service.repo.get_evaluation(
        conn, evaluation_id=evaluation_id
    )
    assert record.project_id == request.project_id
    assert record.request_id == request.request_id
    assert record.manifest_id == request.manifest_id
    assert record.evaluation_policy_fingerprint == EVALUATION_POLICY_FINGERPRINT
    assert record.evaluation_status == "indeterminate"
    assert len(record.inputs) == 1
    assert record.inputs[0].selected_binding_id == binding.id
    assert record.inputs[0].linked_at == record.evaluated_at


def test_direct_dml_complete_anti_forgery_matrix(conn, schema_v58):
    evidence, binding, manifest = _seed_one(conn, tag="direct-matrix")
    request = _evaluation_request(
        evidence["project"], manifest.id, (binding,), request_id="matrix"
    )
    payload = request.canonical_database_payload()
    record = service.create_scenario_input_evaluation(conn, request)
    unselected_binding = binding_service.record_consumer_input_binding(
        conn,
        v57fx._command(
            evidence,
            request_id="direct-matrix-valid-unselected-binding",
            consumer_contract="scenario_input",
            consumer_contract_version=binding.consumer_contract_version,
            binding_set_id=binding.binding_set_id,
            input_key=binding.input_key,
            observation_identity_version="opaque-observation.v1",
            observation_identity_fingerprint="a" * 64,
            policy_identifier=binding.policy_identifier,
            policy_version=binding.policy_version,
            policy_parameters_json=binding.policy_parameters_json,
            policy_fingerprint=binding.policy_fingerprint,
            evaluator_version=binding.evaluator_version,
            freshness_as_of=binding.freshness_as_of,
        ),
    )
    assert unselected_binding.id != binding.id
    assert (
        unselected_binding.project_id,
        unselected_binding.consumer_contract,
        unselected_binding.consumer_contract_version,
        unselected_binding.binding_set_id,
        unselected_binding.input_key,
        unselected_binding.policy_identifier,
        unselected_binding.policy_version,
        unselected_binding.policy_fingerprint,
        unselected_binding.evaluator_version,
        unselected_binding.freshness_as_of,
    ) == (
        binding.project_id,
        "scenario_input",
        binding.consumer_contract_version,
        binding.binding_set_id,
        binding.input_key,
        binding.policy_identifier,
        binding.policy_version,
        binding.policy_fingerprint,
        binding.evaluator_version,
        binding.freshness_as_of,
    )
    assert unselected_binding.id not in {
        selected["binding_id"]
        for selected in record.request_payload["selected_bindings"]
    }
    assert conn.execute(
        """
        SELECT count(*)
        FROM research_evidence_consumer_input_binding
        WHERE id = %s AND project_id = %s
          AND consumer_contract = 'scenario_input'
        """,
        (unselected_binding.id, evidence["project"]),
    ).fetchone()[0] == 1
    persisted_baseline = _project_provenance_state(
        conn, evidence["project"]
    )
    header_forgeries = (
        ("evaluation_status", "'satisfies'", "trg_resie_prepare_insert"),
        ("reason_codes_json", "'[]'::jsonb", "trg_resie_prepare_insert"),
        (
            "evaluation_policy_identifier", "'forged'",
            "trg_resie_prepare_insert",
        ),
        (
            "evaluation_policy_version", "'999'",
            "trg_resie_prepare_insert",
        ),
        (
            "evaluation_policy_parameters_json", "'{}'::jsonb",
            "trg_resie_prepare_insert",
        ),
        (
            "evaluation_policy_fingerprint", f"'{('0' * 64)}'",
            "trg_resie_prepare_insert",
        ),
        (
            "evaluator_version", "'forged-evaluator'",
            "trg_resie_prepare_insert",
        ),
        (
            "consumer_contract_version", "'forged-contract'",
            "trg_resie_prepare_insert",
        ),
        (
            "binding_policy_identifier", "'forged-policy'",
            "trg_resie_prepare_insert",
        ),
        (
            "binding_policy_version", "'999'",
            "trg_resie_prepare_insert",
        ),
        (
            "binding_policy_fingerprint", f"'{('4' * 64)}'",
            "trg_resie_prepare_insert",
        ),
        (
            "binding_evaluator_version", "'forged-binding-evaluator'",
            "trg_resie_prepare_insert",
        ),
        (
            "manifest_fingerprint", f"'{('1' * 64)}'",
            "trg_resie_prepare_insert",
        ),
        (
            "descriptor_fingerprint", f"'{('2' * 64)}'",
            "trg_resie_prepare_insert",
        ),
        (
            "request_fingerprint", f"'{('3' * 64)}'",
            "trg_resie_prepare_insert",
        ),
        ("evaluation_sequence", "999", "trg_resie_prepare_insert"),
        (
            "predecessor_evaluation_id", "gen_random_uuid()",
            "trg_resie_prepare_insert",
        ),
        ("evaluated_at", "clock_timestamp()", "trg_resie_prepare_insert"),
    )
    for index, (column, expression, expected_rule) in enumerate(
        header_forgeries
    ):
        assert expected_rule == "trg_resie_prepare_insert"
        forged_payload = {
            **payload,
            "request_id": f"matrix-forged-header-{index}",
        }
        with pytest.raises(Exception, match="server-owned"):
            with _expected_database_failure(
                conn, f"reject_header_forgery_{index}"
            ):
                conn.execute(
                    "INSERT INTO "
                    "research_evidence_scenario_input_evaluation"
                    f"(request_payload_json, {column}) "
                    f"VALUES (%s::jsonb, {expression})",
                    (json.dumps(forged_payload),),
                )
        assert _project_provenance_state(
            conn, evidence["project"]
        ) == persisted_baseline

    with pytest.raises(Exception):
        with _expected_database_failure(conn, "reject_child_before_header"):
            conn.execute(
                """
                INSERT INTO
                    research_evidence_scenario_input_evaluation_input(
                        evaluation_id, selected_binding_id
                    ) VALUES (%s, %s)
                """,
                (str(uuid.uuid4()), binding.id),
            )
    assert _project_provenance_state(
        conn, evidence["project"]
    ) == persisted_baseline

    child_forgeries = (
        ("linked_at", "clock_timestamp()", "trg_resiei_prepare_insert"),
        (
            "dependence_declaration", "'declared_dependent'",
            "trg_resiei_prepare_insert",
        ),
        (
            "dependence_rationale", "'forged rationale'",
            "trg_resiei_prepare_insert",
        ),
        ("input_status", "'satisfies'", "trg_resiei_prepare_insert"),
        ("reason_codes_json", "'[]'::jsonb", "trg_resiei_prepare_insert"),
    )
    for index, (column, expression, expected_rule) in enumerate(
        child_forgeries
    ):
        assert expected_rule == "trg_resiei_prepare_insert"
        with pytest.raises(Exception, match="server-owned"):
            with _expected_database_failure(
                conn, f"reject_child_forgery_{index}"
            ):
                conn.execute(
                    "INSERT INTO "
                    "research_evidence_scenario_input_evaluation_input"
                    f"(evaluation_id, selected_binding_id, {column}) "
                    f"VALUES (%s, %s, {expression})",
                    (record.id, binding.id),
                )
        assert _project_provenance_state(
            conn, evidence["project"]
        ) == persisted_baseline

    outside_payload_rule = "trg_resiei_prepare_insert"
    assert outside_payload_rule == "trg_resiei_prepare_insert"
    with pytest.raises(
        Exception, match="binding is not selected by evaluation payload"
    ):
        with _expected_database_failure(
            conn, "reject_valid_binding_outside_payload"
        ):
            conn.execute(
                """
                INSERT INTO
                    research_evidence_scenario_input_evaluation_input(
                        evaluation_id, selected_binding_id
                    ) VALUES (%s, %s)
                """,
                (record.id, unselected_binding.id),
            )
    assert _project_provenance_state(
        conn, evidence["project"]
    ) == persisted_baseline

    for savepoint, selected_binding_id in (
        ("reject_child_after_header", binding.id),
        ("reject_duplicate_child", binding.id),
        ("reject_missing_binding", str(uuid.uuid4())),
    ):
        with pytest.raises(Exception):
            with _expected_database_failure(conn, savepoint):
                conn.execute(
                    """
                    INSERT INTO
                        research_evidence_scenario_input_evaluation_input(
                            evaluation_id, selected_binding_id
                        ) VALUES (%s, %s)
                    """,
                    (record.id, selected_binding_id),
                )
        assert _project_provenance_state(
            conn, evidence["project"]
        ) == persisted_baseline

    with pytest.raises(Exception, match="append-only"):
        with _expected_database_failure(conn, "reject_missing_child"):
            conn.execute(
                """
                DELETE FROM research_evidence_scenario_input_evaluation_input
                WHERE evaluation_id = %s
                """,
                (record.id,),
            )
    assert _project_provenance_state(
        conn, evidence["project"]
    ) == persisted_baseline


def test_c_e_o_and_per_scope_successors_use_precommit_barriers(
    conn, schema_v58
):
    first_evidence = v57fx._seed_evidence(conn, tag="successor-lock-a")
    second_evidence = v57fx._seed_evidence(
        conn,
        tag="successor-lock-b",
        project_id=first_evidence["project"],
    )
    first_binding = _binding(
        conn, first_evidence, input_key="input-a",
        request_id="binding-lock-a"
    )
    second_binding = _binding(
        conn, second_evidence, input_key="input-b",
        request_id="binding-lock-b"
    )
    bindings = (first_binding, second_binding)
    manifest = _manifest(
        conn,
        first_evidence["project"],
        ("input-a", "input-b"),
        request_id="manifest-successor-lock",
    )
    initial = service.create_scenario_input_evaluation(
        conn,
        _evaluation_request(
            first_evidence["project"],
            manifest.id,
            bindings,
            request_id="initial",
        ),
    )
    conn.commit()
    scope = (
        first_evidence["project"],
        manifest.id,
        first_binding.binding_set_id,
        "caller.opaque-hypothesis",
        "draft-v1",
        initial.descriptor_fingerprint,
    )

    blocker = pg.connect(schema=schema_v58)
    evaluator = pg.connect(schema=schema_v58)
    successors = [
        pg.connect(schema=schema_v58),
        pg.connect(schema=schema_v58),
    ]
    observer = pg.connect(schema=schema_v58, autocommit=True)
    successor_ready = [threading.Event() for _ in successors]
    successor_release = [threading.Event() for _ in successors]
    try:
        blocker.execute(
            """
            SELECT 1
            FROM research_evidence_scenario_input_evaluation_sequence_allocator
            WHERE project_id = %s AND manifest_id = %s AND binding_set_id = %s
              AND descriptor_namespace = %s AND descriptor_version = %s
              AND descriptor_fingerprint = %s
            FOR UPDATE
            """,
            scope,
        )
        blocker_pid = blocker.execute(
            "SELECT pg_backend_pid()"
        ).fetchone()[0]
        evaluator_pid = evaluator.execute(
            "SELECT pg_backend_pid()"
        ).fetchone()[0]
        successor_pids = [
            successor.execute("SELECT pg_backend_pid()").fetchone()[0]
            for successor in successors
        ]
        observer_pid = observer.execute(
            "SELECT pg_backend_pid()"
        ).fetchone()[0]
        assert len({
            blocker_pid, evaluator_pid, observer_pid, *successor_pids
        }) == 5

        def evaluate():
            result = service.create_scenario_input_evaluation(
                evaluator,
                _evaluation_request(
                    first_evidence["project"],
                    manifest.id,
                    bindings,
                    request_id="blocked-evaluation",
                ),
            )
            evaluator.commit()
            return result

        def append_successor(index):
            successor = successors[index]
            evidence = (first_evidence, second_evidence)[index]
            binding = bindings[index]
            result = binding_service.record_consumer_input_binding(
                successor,
                v57fx._command(
                    evidence,
                    request_id=f"successor-{index}",
                    consumer_contract="scenario_input",
                    consumer_contract_version="scenario-observation.v1",
                    binding_set_id=binding.binding_set_id,
                    input_key=binding.input_key,
                    observation_identity_version="opaque-observation.v1",
                    observation_identity_fingerprint="a" * 64,
                    policy_fingerprint="b" * 64,
                ),
            )
            successor_ready[index].set()
            if not successor_release[index].wait(timeout=10):
                raise AssertionError(
                    "successor commit barrier was not released"
                )
            successor.commit()
            return result

        def probe_allocator_lock(index):
            binding = bindings[index]
            return successors[index].execute(
                """
                SELECT 1
                FROM
                    research_evidence_consumer_input_binding_sequence_allocator
                WHERE project_id = %s
                  AND consumer_contract = 'scenario_input'
                  AND binding_set_id = %s
                  AND input_key = %s
                FOR UPDATE
                """,
                (
                    first_evidence["project"],
                    binding.binding_set_id,
                    binding.input_key,
                ),
            ).fetchone()

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            evaluation_future = pool.submit(evaluate)
            _wait_until(
                lambda: (
                    blocker_pid
                    in observer.execute(
                        "SELECT pg_blocking_pids(%s)", (evaluator_pid,)
                    ).fetchone()[0]
                    and observer.execute(
                        """
                        SELECT wait_event_type = 'Lock'
                        FROM pg_stat_activity WHERE pid = %s
                        """,
                        (evaluator_pid,),
                    ).fetchone()[0]
                )
            )
            assert blocker_pid in observer.execute(
                "SELECT pg_blocking_pids(%s)", (evaluator_pid,)
            ).fetchone()[0]
            assert observer.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM pg_locks
                    WHERE pid = %s AND granted
                      AND relation =
                          'research_evidence_consumer_input_binding_sequence_allocator'
                          ::regclass
                )
                """,
                (evaluator_pid,),
            ).fetchone()[0]

            lock_probe_futures = [
                pool.submit(probe_allocator_lock, index)
                for index in range(len(successors))
            ]
            proven_v57_scopes = []
            for index, successor_pid in enumerate(successor_pids):
                _wait_until(
                    lambda pid=successor_pid: (
                        evaluator_pid
                        in observer.execute(
                            "SELECT pg_blocking_pids(%s)", (pid,)
                        ).fetchone()[0]
                        and observer.execute(
                            """
                            SELECT wait_event_type = 'Lock'
                            FROM pg_stat_activity WHERE pid = %s
                            """,
                            (pid,),
                        ).fetchone()[0]
                    )
                )
                assert evaluator_pid in observer.execute(
                    "SELECT pg_blocking_pids(%s)", (successor_pid,)
                ).fetchone()[0]
                proven_v57_scopes.append(
                    (
                        bindings[index].binding_set_id,
                        bindings[index].input_key,
                    )
                )
            assert proven_v57_scopes == [
                (binding.binding_set_id, binding.input_key)
                for binding in bindings
            ]
            for successor in successors:
                successor.cancel()
            for future in lock_probe_futures:
                with pytest.raises(Exception):
                    future.result(timeout=10)
            for successor in successors:
                successor.rollback()

            successor_futures = [
                pool.submit(append_successor, index)
                for index in range(len(successors))
            ]
            for successor_pid in successor_pids:
                _wait_until(
                    lambda pid=successor_pid: (
                        evaluator_pid
                        in observer.execute(
                            "SELECT pg_blocking_pids(%s)", (pid,)
                        ).fetchone()[0]
                        and observer.execute(
                            """
                            SELECT wait_event_type = 'Lock'
                            FROM pg_stat_activity WHERE pid = %s
                            """,
                            (pid,),
                        ).fetchone()[0]
                    )
                )
                assert evaluator_pid in observer.execute(
                    "SELECT pg_blocking_pids(%s)", (successor_pid,)
                ).fetchone()[0]
            blocker.commit()
            try:
                for ready in successor_ready:
                    _wait_until(ready.is_set)
                persisted_row = _wait_until(
                    lambda: observer.execute(
                        """
                        SELECT id::text
                        FROM research_evidence_scenario_input_evaluation
                        WHERE project_id = %s
                          AND request_id = 'blocked-evaluation'
                        """,
                        (first_evidence["project"],),
                    ).fetchone()
                )
                assert all(
                    not future.done() for future in successor_futures
                )
                historical = service.repo.get_evaluation(
                    observer, evaluation_id=persisted_row[0]
                )
                assert historical is not None
                assert len(historical.inputs) == len(bindings)
                assert all(
                    not child.selected_binding_has_successor
                    for child in historical.inputs
                )
                assert "selected_binding_successor" not in (
                    historical.reason_codes
                )
                assert evaluation_future.result(timeout=10) == historical
            finally:
                for release in successor_release:
                    release.set()
            successor_records = [
                future.result(timeout=10) for future in successor_futures
            ]

        assert len(historical.inputs) == 2
        assert all(
            not child.selected_binding_has_successor
            for child in historical.inputs
        )
        assert "selected_binding_successor" not in historical.reason_codes
        assert [
            record.binding_sequence for record in successor_records
        ] == [
            binding.binding_sequence + 1 for binding in bindings
        ]
        stored = service.repo.get_evaluation(
            conn, evaluation_id=historical.id
        )
        assert stored == historical
    finally:
        for release in successor_release:
            release.set()
        for connection in (blocker, evaluator, *successors, observer):
            try:
                connection.rollback()
            except Exception:
                pass
            connection.close()


@pytest.mark.parametrize(
    "mutation_sql",
    (
        "ALTER TABLE research_evidence_scenario_input_manifest "
        "ADD COLUMN drift text",
        "ALTER TABLE research_evidence_scenario_input_evaluation "
        "ALTER COLUMN evaluation_status DROP NOT NULL",
        "ALTER TABLE research_evidence_scenario_input_manifest "
        "ALTER COLUMN manifest_version TYPE varchar "
        "USING manifest_version::varchar",
        "ALTER TABLE research_evidence_scenario_input_evaluation "
        "ALTER COLUMN evaluated_at SET DEFAULT clock_timestamp()",
        "DROP INDEX idx_resiei_binding; "
        "CREATE INDEX idx_resiei_binding "
        "ON research_evidence_scenario_input_evaluation_input(input_key)",
        "GRANT SELECT ON "
        "research_evidence_scenario_input_evaluation_sequence_allocator "
        "TO PUBLIC",
        "GRANT EXECUTE ON FUNCTION "
        "research_evidence_create_scenario_input_evaluation(jsonb) TO PUBLIC",
    ),
)
def test_v58_reapply_rejects_same_name_and_shape_drift(
    conn, schema_v58, mutation_sql
):
    prior = pg._begin_autocommit(conn)
    conn.execute(mutation_sql)
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception, match="v58 contract violation"):
        pg.apply_v58_research_scenario_input_evaluation(conn)


def test_v58_reapply_rejects_function_trigger_and_history_drift(
    conn, schema_v58
):
    prior = pg._begin_autocommit(conn)
    conn.execute(
        """
        CREATE OR REPLACE FUNCTION
            research_evidence_create_scenario_input_evaluation(
                p_request_payload jsonb
            )
        RETURNS SETOF research_evidence_scenario_input_evaluation
        LANGUAGE sql AS 'SELECT * FROM
            research_evidence_scenario_input_evaluation WHERE false'
        """
    )
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception, match="functions"):
        pg.apply_v58_research_scenario_input_evaluation(conn)


def test_v58_reapply_rejects_trigger_update_scope_drift(conn, schema_v58):
    pg.apply_v58_research_scenario_input_evaluation(conn)

    def trigger_contract():
        return conn.execute(
            """
            SELECT n.nspname, c.relname, t.tgname, t.tgtype, p.proname,
                   t.tgenabled, t.tgisinternal, t.tgnargs, t.tgqual,
                   t.tgoldtable, t.tgnewtable, t.tgconstraint <> 0,
                   t.tgdeferrable, t.tginitdeferred, t.tgattr::text
            FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_proc p ON p.oid = t.tgfoid
            WHERE n.nspname = current_schema()
              AND c.relname =
                  'research_evidence_scenario_input_manifest_item'
              AND t.tgname = 'trg_resimi_no_mutation'
              AND NOT t.tgisinternal
            """
        ).fetchone()

    baseline = trigger_contract()
    assert baseline is not None
    assert baseline[-1] == ""

    prior = pg._begin_autocommit(conn)
    try:
        conn.execute(
            """
            DROP TRIGGER trg_resimi_no_mutation
            ON research_evidence_scenario_input_manifest_item
            """
        )
        conn.execute(
            """
            CREATE TRIGGER trg_resimi_no_mutation
            BEFORE DELETE OR UPDATE OF input_key
            ON research_evidence_scenario_input_manifest_item
            FOR EACH ROW
            EXECUTE FUNCTION slicea_reject_mutation()
            """
        )
        conn.execute(
            """
            ALTER TABLE research_evidence_scenario_input_manifest_item
            ENABLE ALWAYS TRIGGER trg_resimi_no_mutation
            """
        )
        mutated = trigger_contract()
    finally:
        pg._restore_autocommit(conn, prior)

    assert mutated is not None
    assert mutated[-1] != baseline[-1]
    assert mutated[:-1] == baseline[:-1]
    with pytest.raises(
        Exception, match="v58 contract violation: divergent triggers"
    ):
        pg.apply_v58_research_scenario_input_evaluation(conn)


@pytest.mark.parametrize(
    ("category", "mutation_sql"),
    (
        (
            "explicit",
            "DROP INDEX idx_resiei_binding; "
            "CREATE INDEX idx_resiei_binding "
            "ON research_evidence_scenario_input_evaluation_input"
            "(input_key DESC NULLS FIRST)",
        ),
        (
            "primary-key-backed",
            "ALTER TABLE research_evidence_scenario_input_manifest "
            "DROP CONSTRAINT research_evidence_scenario_input_manifest_pkey; "
            "ALTER TABLE research_evidence_scenario_input_manifest "
            "ADD CONSTRAINT research_evidence_scenario_input_manifest_pkey "
            "PRIMARY KEY (id, project_id)",
        ),
        (
            "unique-constraint-backed",
            "ALTER TABLE research_evidence_scenario_input_manifest "
            "DROP CONSTRAINT uq_resim_project_request; "
            "ALTER TABLE research_evidence_scenario_input_manifest "
            "ADD CONSTRAINT uq_resim_project_request "
            "UNIQUE (project_id, manifest_namespace, "
            "registration_request_id)",
        ),
    ),
)
def test_index_inventory_rejects_same_name_drift_in_every_category(
    conn, schema_v58, category, mutation_sql
):
    assert category in {
        "explicit", "primary-key-backed", "unique-constraint-backed"
    }
    prior = pg._begin_autocommit(conn)
    conn.execute(mutation_sql)
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception, match="v58 contract violation"):
        pg.apply_v58_research_scenario_input_evaluation(conn)


@pytest.mark.parametrize(
    "mutation_sql",
    (
        "ALTER TABLE research_evidence_scenario_input_evaluation "
        "DROP CONSTRAINT ck_resie_status; "
        "ALTER TABLE research_evidence_scenario_input_evaluation "
        "ADD CONSTRAINT ck_resie_status CHECK (true)",
        "ALTER TABLE research_evidence_scenario_input_evaluation_input "
        "DROP CONSTRAINT fk_resiei_manifest_item; "
        "ALTER TABLE research_evidence_scenario_input_evaluation_input "
        "ADD CONSTRAINT fk_resiei_manifest_item "
        "FOREIGN KEY (manifest_id, project_id, input_key) "
        "REFERENCES research_evidence_scenario_input_manifest_item"
        "(manifest_id, project_id, input_key) DEFERRABLE",
        "ALTER TABLE research_evidence_scenario_input_manifest "
        "ALTER COLUMN id DROP DEFAULT",
        "ALTER TABLE research_evidence_scenario_input_evaluation "
        "DISABLE TRIGGER trg_resie_prepare_insert",
    ),
)
def test_v58_reapply_rejects_constraint_default_and_trigger_definition_drift(
    conn, schema_v58, mutation_sql
):
    prior = pg._begin_autocommit(conn)
    conn.execute(mutation_sql)
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception, match="v58 contract violation"):
        pg.apply_v58_research_scenario_input_evaluation(conn)


@pytest.mark.parametrize(
    "corruption",
    (
        "deleted_allocator",
        "malformed_allocator",
        "latest_history_mismatch",
        "historyless_allocator",
    ),
)
def test_contract_a_reapply_rejects_bidirectional_allocator_history_drift(
    conn, schema_v58, corruption
):
    assert ALLOCATOR_LIFECYCLE_CONTRACT == "CONTRACT_A_NO_PREALLOCATION"
    evidence, binding, manifest = _seed_one(
        conn, tag=f"allocator-contract-{corruption}"
    )
    records = []
    if corruption != "historyless_allocator":
        records.append(
            service.create_scenario_input_evaluation(
                conn,
                _evaluation_request(
                    evidence["project"],
                    manifest.id,
                    (binding,),
                    request_id="allocator-contract-first",
                ),
            )
        )
        if corruption == "latest_history_mismatch":
            records.append(
                service.create_scenario_input_evaluation(
                    conn,
                    _evaluation_request(
                        evidence["project"],
                        manifest.id,
                        (binding,),
                        request_id="allocator-contract-second",
                    ),
                )
            )
    conn.commit()
    prior = pg._begin_autocommit(conn)
    if corruption == "deleted_allocator":
        conn.execute(
            "DELETE FROM "
            "research_evidence_scenario_input_evaluation_sequence_allocator "
            "WHERE last_evaluation_id = %s",
            (records[-1].id,),
        )
    elif corruption == "malformed_allocator":
        conn.execute(
            "UPDATE "
            "research_evidence_scenario_input_evaluation_sequence_allocator "
            "SET last_sequence = last_sequence + 1 "
            "WHERE last_evaluation_id = %s",
            (records[-1].id,),
        )
    elif corruption == "latest_history_mismatch":
        conn.execute(
            "UPDATE "
            "research_evidence_scenario_input_evaluation_sequence_allocator "
            "SET last_evaluation_id = %s "
            "WHERE last_evaluation_id = %s",
            (records[0].id, records[-1].id),
        )
    else:
        conn.execute(
            """
            INSERT INTO
                research_evidence_scenario_input_evaluation_sequence_allocator(
                    project_id, manifest_id, binding_set_id,
                    descriptor_namespace, descriptor_version,
                    descriptor_fingerprint, last_sequence,
                    last_evaluation_id, allocator_updated_at
                )
            VALUES (%s, %s, 'historyless-set', 'historyless', '1',
                    %s, 0, NULL, clock_timestamp())
            """,
            (evidence["project"], manifest.id, "f" * 64),
        )
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception, match="v58 contract violation"):
        pg.apply_v58_research_scenario_input_evaluation(conn)


def test_contract_a_allocator_scope_uniqueness_guard_is_closed(
    conn, schema_v58
):
    assert ALLOCATOR_LIFECYCLE_CONTRACT == "CONTRACT_A_NO_PREALLOCATION"
    prior = pg._begin_autocommit(conn)
    conn.execute(
        "ALTER TABLE "
        "research_evidence_scenario_input_evaluation_sequence_allocator "
        "DROP CONSTRAINT pk_resie_allocator"
    )
    pg._restore_autocommit(conn, prior)
    with pytest.raises(
        Exception, match="missing constraints|constraint backing indexes"
    ):
        pg.apply_v58_research_scenario_input_evaluation(conn)


@pytest.mark.parametrize(
    ("inventory_item", "mutation_sql"),
    INVENTORY_MUTATIONS,
    ids=[item[0] for item in INVENTORY_MUTATIONS],
)
def test_each_closed_inventory_item_has_same_name_reapply_mutation_proof(
    conn, schema_v58, inventory_item, mutation_sql
):
    prior = pg._begin_autocommit(conn)
    conn.execute(mutation_sql)
    pg._restore_autocommit(conn, prior)
    with pytest.raises(Exception, match="v58 contract violation"):
        pg.apply_v58_research_scenario_input_evaluation(conn)


@pytest.mark.parametrize(
    "corruption",
    (
        "allocator",
        "predecessor",
        "child_timestamp",
        "manifest_item",
        "request_membership",
        "child_dependence",
        "child_policy",
        "aggregate_status",
    ),
)
def test_v58_reapply_rejects_allocator_predecessor_child_and_manifest_drift(
    conn, schema_v58, corruption
):
    evidence, binding, manifest = _seed_one(conn, tag=f"history-{corruption}")
    first = service.create_scenario_input_evaluation(
        conn,
        _evaluation_request(
            evidence["project"], manifest.id, (binding,), request_id="first"
        ),
    )
    second = service.create_scenario_input_evaluation(
        conn,
        _evaluation_request(
            evidence["project"], manifest.id, (binding,), request_id="second"
        ),
    )
    conn.commit()
    if corruption == "allocator":
        statement = (
            "UPDATE "
            "research_evidence_scenario_input_evaluation_sequence_allocator "
            "SET last_sequence = last_sequence + 1 "
            "WHERE last_evaluation_id = %s"
        )
        params = (second.id,)
    elif corruption == "predecessor":
        table = "research_evidence_scenario_input_evaluation"
        trigger = "trg_resie_no_mutation"
        statement = (
            "UPDATE research_evidence_scenario_input_evaluation "
            "SET predecessor_evaluation_id = NULL WHERE id = %s"
        )
        params = (second.id,)
    elif corruption == "child_timestamp":
        table = "research_evidence_scenario_input_evaluation_input"
        trigger = "trg_resiei_no_mutation"
        statement = (
            "UPDATE research_evidence_scenario_input_evaluation_input "
            "SET linked_at = linked_at + interval '1 second' "
            "WHERE evaluation_id = %s"
        )
        params = (first.id,)
    elif corruption == "manifest_item":
        table = "research_evidence_scenario_input_manifest_item"
        trigger = "trg_resimi_no_mutation"
        statement = (
            "DELETE FROM research_evidence_scenario_input_manifest_item "
            "WHERE manifest_id = %s"
        )
        params = (manifest.id,)
    elif corruption == "request_membership":
        table = "research_evidence_scenario_input_evaluation"
        trigger = "trg_resie_no_mutation"
        statement = (
            "UPDATE research_evidence_scenario_input_evaluation "
            "SET request_payload_json = "
            "jsonb_set(request_payload_json, '{selected_bindings}', '[]'), "
            "request_fingerprint = encode(sha256(convert_to("
            "jsonb_set(request_payload_json, "
            "'{selected_bindings}', '[]')::text, 'UTF8')), 'hex') "
            "WHERE id = %s"
        )
        params = (first.id,)
    elif corruption == "child_dependence":
        table = "research_evidence_scenario_input_evaluation_input"
        trigger = "trg_resiei_no_mutation"
        statement = (
            "UPDATE research_evidence_scenario_input_evaluation_input "
            "SET dependence_declaration = 'declared_dependent', "
            "dependence_rationale = 'administrative drift' "
            "WHERE evaluation_id = %s"
        )
        params = (first.id,)
    elif corruption == "child_policy":
        table = "research_evidence_scenario_input_evaluation_input"
        trigger = "trg_resiei_no_mutation"
        statement = (
            "UPDATE research_evidence_scenario_input_evaluation_input "
            "SET reason_codes_json = '[\"binding_qualified\"]'::jsonb "
            "WHERE evaluation_id = %s"
        )
        params = (first.id,)
    else:
        table = "research_evidence_scenario_input_evaluation"
        trigger = "trg_resie_no_mutation"
        statement = (
            "UPDATE research_evidence_scenario_input_evaluation "
            "SET evaluation_status = 'satisfies', reason_codes_json = '[]' "
            "WHERE id = %s"
        )
        params = (first.id,)
    if corruption == "allocator":
        prior = pg._begin_autocommit(conn)
        conn.execute(statement, params)
        pg._restore_autocommit(conn, prior)
    else:
        _corrupt_v58_immutable(
            conn,
            table=table,
            mutation_trigger=trigger,
            statement=statement,
            params=params,
        )
    with pytest.raises(Exception, match="v58 contract violation"):
        pg.apply_v58_research_scenario_input_evaluation(conn)


def test_exact_catalog_shape_actions_deferrability_defaults_and_acl(
    conn, schema_v58
):
    counts = dict(
        conn.execute(
            """
            SELECT table_name, count(*)::integer
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = ANY(%s)
            GROUP BY table_name
            """,
            (list(TABLES),),
        ).fetchall()
    )
    assert counts == dict(zip(TABLES, (11, 5, 31, 20, 9)))
    defaults = conn.execute(
        """
        SELECT table_name, column_name, column_default
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = ANY(%s)
          AND column_default IS NOT NULL
        ORDER BY table_name, column_name
        """,
        (list(TABLES),),
    ).fetchall()
    assert defaults == [
        (
            "research_evidence_scenario_input_evaluation",
            "id",
            "gen_random_uuid()",
        ),
        (
            "research_evidence_scenario_input_manifest",
            "id",
            "gen_random_uuid()",
        ),
    ]
    constraints = conn.execute(
        """
        SELECT conname, contype, convalidated, condeferrable,
               condeferred, confdeltype
        FROM pg_constraint
        WHERE connamespace = current_schema()::regnamespace
          AND conrelid = ANY(%s::regclass[])
        """,
        (list(TABLES),),
    ).fetchall()
    assert constraints
    assert all(row[2] for row in constraints)
    allocator_fk = next(
        row for row in constraints if row[0] == "fk_resie_allocator_last"
    )
    assert allocator_fk[3:5] == (True, True)
    assert all(
        row[5] in (" ", "r")
        for row in constraints
        if row[1] == "f"
    )
    assert conn.execute(
        """
        SELECT count(*)
        FROM information_schema.role_table_grants
        WHERE grantee = 'PUBLIC' AND table_schema = current_schema()
          AND table_name = ANY(%s)
        """,
        (list(TABLES),),
    ).fetchone()[0] == 0
    table_acl = conn.execute(
        """
        SELECT c.relname, c.relowner, acl.grantee
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN LATERAL aclexplode(
            COALESCE(c.relacl, acldefault('r', c.relowner))
        ) acl
        WHERE n.nspname = current_schema()
          AND c.relname = ANY(%s)
        """,
        (list(TABLES),),
    ).fetchall()
    assert table_acl
    assert all(owner == grantee for _, owner, grantee in table_acl)
    function_acl = conn.execute(
        """
        SELECT p.proname, p.proowner, acl.grantee
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        CROSS JOIN LATERAL aclexplode(
            COALESCE(p.proacl, acldefault('f', p.proowner))
        ) acl
        WHERE n.nspname = current_schema()
          AND p.proname = ANY(%s)
        """,
        (list(FUNCTION_INVENTORY),),
    ).fetchall()
    assert function_acl
    assert all(owner == grantee for _, owner, grantee in function_acl)


def test_closed_v58_object_inventory_and_clean_reapply(conn, schema_v58):
    table_rows = conn.execute(
        """
        SELECT c.relname, c.relowner, database_info.datdba
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_database database_info
          ON database_info.datname = current_database()
        WHERE n.nspname = current_schema()
          AND c.relkind = 'r' AND c.relname = ANY(%s)
        """,
        (list(TABLES),),
    ).fetchall()
    tables = {
        row[0]
        for row in table_rows
    }
    assert all(
        owner == database_owner
        for _, owner, database_owner in table_rows
    )
    index_rows = conn.execute(
        """
        SELECT index_class.oid, index_class.relname, table_class.relname,
               constraint_info.conname, am.amname,
               index_info.indnkeyatts, index_info.indnatts,
               index_info.indisunique, index_info.indisprimary,
               index_info.indimmediate, index_info.indisvalid,
               index_info.indisready, index_info.indislive,
               pg_get_expr(index_info.indpred, index_info.indrelid),
               pg_get_expr(index_info.indexprs, index_info.indrelid),
               ARRAY(
                   SELECT attribute.attname
                   FROM unnest(index_info.indkey::smallint[])
                        WITH ORDINALITY key(attnum, position)
                   JOIN pg_attribute attribute
                     ON attribute.attrelid = index_info.indrelid
                    AND attribute.attnum = key.attnum
                   WHERE key.position <= index_info.indnkeyatts
                   ORDER BY key.position
               ) AS key_columns,
               ARRAY(
                   SELECT attribute.attname
                   FROM unnest(index_info.indkey::smallint[])
                        WITH ORDINALITY key(attnum, position)
                   JOIN pg_attribute attribute
                     ON attribute.attrelid = index_info.indrelid
                    AND attribute.attnum = key.attnum
                   WHERE key.position > index_info.indnkeyatts
                   ORDER BY key.position
               ) AS include_columns,
               ARRAY(
                   SELECT opclass.opcname
                   FROM unnest(
                       index_info.indkey::smallint[],
                       index_info.indclass::oid[],
                       index_info.indcollation::oid[],
                       index_info.indoption::smallint[]
                   ) WITH ORDINALITY index_key(
                       attnum, opclass, collation_oid, options, position
                   )
                   JOIN pg_attribute attribute
                     ON attribute.attrelid = index_info.indrelid
                    AND attribute.attnum = index_key.attnum
                   JOIN pg_opclass opclass
                     ON opclass.oid = index_key.opclass
                   WHERE index_key.position <= index_info.indnkeyatts
                   ORDER BY index_key.position
               ) AS opclasses,
               ARRAY(
                   SELECT index_key.collation_oid = attribute.attcollation
                   FROM unnest(
                       index_info.indkey::smallint[],
                       index_info.indclass::oid[],
                       index_info.indcollation::oid[],
                       index_info.indoption::smallint[]
                   ) WITH ORDINALITY index_key(
                       attnum, opclass, collation_oid, options, position
                   )
                   JOIN pg_attribute attribute
                     ON attribute.attrelid = index_info.indrelid
                    AND attribute.attnum = index_key.attnum
                   WHERE index_key.position <= index_info.indnkeyatts
                   ORDER BY index_key.position
               ) AS collations_match_columns,
               ARRAY(
                   SELECT index_key.options
                   FROM unnest(index_info.indoption::smallint[])
                        WITH ORDINALITY index_key(options, position)
                   ORDER BY index_key.position
               ) AS indoptions,
               COALESCE(obj_description(index_class.oid, 'pg_class') =
                   'v58:' || md5(regexp_replace(
                       pg_get_indexdef(index_class.oid),
                       '[[:space:]]+', '', 'g'
                   )), false) AS explicit_index_sealed
        FROM pg_index index_info
        JOIN pg_class index_class
          ON index_class.oid = index_info.indexrelid
        JOIN pg_class table_class
          ON table_class.oid = index_info.indrelid
        JOIN pg_namespace n ON n.oid = index_class.relnamespace
        JOIN pg_am am ON am.oid = index_class.relam
        LEFT JOIN pg_constraint constraint_info
          ON constraint_info.conindid = index_class.oid
         AND constraint_info.conrelid = table_class.oid
         AND constraint_info.contype IN ('p', 'u')
        WHERE n.nspname = current_schema()
          AND index_class.relname = ANY(%s)
        """,
        (list(INDEX_INVENTORY),),
    ).fetchall()
    indexes = {
        row[1]
        for row in index_rows
    }
    for row in index_rows:
        (
            index_oid, name, table_name, backing_constraint, method,
            key_count, attribute_count, unique, primary, immediate,
            valid, ready, live, predicate, expressions, key_columns,
            include_columns, opclasses, collations_match_columns,
            indoptions, explicit_index_sealed,
        ) = row
        (
            expected_table, expected_constraint, expected_keys,
            expected_include,
        ) = INDEX_CONTRACT[name]
        assert index_oid > 0
        assert (table_name, backing_constraint, method) == (
            expected_table, expected_constraint, "btree"
        )
        assert key_count == len(expected_keys)
        assert attribute_count == len(expected_keys) + len(expected_include)
        assert tuple(key_columns) == tuple(key for key, _ in expected_keys)
        assert tuple(include_columns) == expected_include
        assert tuple(opclasses) == tuple(
            opclass for _, opclass in expected_keys
        )
        assert tuple(collations_match_columns) == (True,) * len(expected_keys)
        assert tuple(indoptions) == (0,) * len(expected_keys)
        assert predicate is None
        assert expressions is None
        assert unique is (expected_constraint is not None)
        assert primary is (
            expected_constraint is not None
            and (
                expected_constraint.endswith("_pkey")
                or expected_constraint.startswith("pk_")
            )
        )
        assert immediate
        assert all((valid, ready, live))
        assert explicit_index_sealed is (expected_constraint is None)
    constraints = {
        (row[0], row[1])
        for row in conn.execute(
            """
            SELECT con.conname, c.relname
            FROM pg_constraint con
            JOIN pg_class c ON c.oid = con.conrelid
            WHERE con.connamespace = current_schema()::regnamespace
              AND c.relname = ANY(%s)
              AND con.convalidated
              AND obj_description(con.oid, 'pg_constraint') =
                  'v58:' || md5(regexp_replace(
                      pg_get_constraintdef(con.oid, true),
                      '[[:space:]]+', '', 'g'
                  ))
            """,
            (list(TABLES),),
        ).fetchall()
    }
    functions = {
        row[0]
        for row in conn.execute(
            """
            SELECT proname FROM pg_proc
            WHERE pronamespace = current_schema()::regnamespace
              AND proname = ANY(%s)
            """,
            (list(FUNCTION_INVENTORY),),
        ).fetchall()
    }
    trigger_rows = conn.execute(
        """
        SELECT t.tgname, c.relname, p.proname, t.tgtype, t.tgattr::text,
               t.tgenabled, t.tgnargs, t.tgqual, t.tgoldtable, t.tgnewtable,
               t.tgconstraint <> 0, t.tgdeferrable, t.tginitdeferred
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_proc p ON p.oid = t.tgfoid
        WHERE NOT t.tgisinternal AND t.tgname = ANY(%s)
        """,
        (list(TRIGGER_INVENTORY),),
    ).fetchall()
    triggers = {row[0] for row in trigger_rows}
    for row in trigger_rows:
        (
            name, table, function, trigger_type, trigger_attributes,
            enabled, nargs, condition, old_transition, new_transition,
            constraint_trigger,
            deferrable, initially_deferred,
        ) = row
        expected_type = (
            27 if name.endswith("no_mutation")
            else 7 if name.endswith("prepare_insert")
            else 5
        )
        assert (table, function, trigger_type, enabled, nargs) == (
            TRIGGER_TABLE_INVENTORY[name],
            TRIGGER_FUNCTION_INVENTORY[name],
            expected_type,
            "A",
            0,
        )
        assert condition is None
        assert old_transition is None
        assert new_transition is None
        assert trigger_attributes == ""
        is_complete = name.endswith("complete")
        assert (constraint_trigger, deferrable, initially_deferred) == (
            is_complete, is_complete, is_complete
        )
    assert tables == set(TABLES)
    assert indexes == set(INDEX_INVENTORY)
    assert constraints == {
        (name, table) for name, table in CONSTRAINT_TABLE_INVENTORY.items()
    }
    assert functions == set(FUNCTION_INVENTORY)
    assert triggers == set(TRIGGER_INVENTORY)
    pg.apply_v58_research_scenario_input_evaluation(conn)


def test_reapply_rejects_named_nonowner_table_and_function_acl(
    conn, schema_v58
):
    role_name = f"v58_acl_probe_{uuid.uuid4().hex[:12]}"
    created = False
    prior = pg._begin_autocommit(conn)
    try:
        try:
            conn.execute(f'CREATE ROLE "{role_name}" NOLOGIN')
            created = True
        except Exception as exc:
            pytest.skip(
                "test database identity cannot create the disposable "
                f"NOLOGIN ACL probe role: {exc}"
            )

        conn.execute(
            "GRANT SELECT ON "
            "research_evidence_scenario_input_evaluation "
            f'TO "{role_name}"'
        )
        with pytest.raises(
            Exception, match="tables have non-owner ACL privileges"
        ):
            pg.apply_v58_research_scenario_input_evaluation(conn)
        conn.execute("ROLLBACK")
        conn.execute(
            "REVOKE ALL ON "
            "research_evidence_scenario_input_evaluation "
            f'FROM "{role_name}"'
        )
        pg.apply_v58_research_scenario_input_evaluation(conn)

        conn.execute(
            "GRANT EXECUTE ON FUNCTION "
            "research_evidence_create_scenario_input_evaluation(jsonb) "
            f'TO "{role_name}"'
        )
        with pytest.raises(
            Exception, match="functions have non-owner ACL privileges"
        ):
            pg.apply_v58_research_scenario_input_evaluation(conn)
        conn.execute("ROLLBACK")
        conn.execute(
            "REVOKE ALL ON FUNCTION "
            "research_evidence_create_scenario_input_evaluation(jsonb) "
            f'FROM "{role_name}"'
        )
        pg.apply_v58_research_scenario_input_evaluation(conn)
    finally:
        if created:
            conn.execute(f'DROP OWNED BY "{role_name}"')
            conn.execute(f'DROP ROLE IF EXISTS "{role_name}"')
            assert conn.execute(
                "SELECT count(*) FROM pg_roles WHERE rolname = %s",
                (role_name,),
            ).fetchone()[0] == 0
        pg._restore_autocommit(conn, prior)


def test_reapply_rejects_table_owner_drift_with_disposable_role(
    conn, schema_v58
):
    role_name = f"v58_owner_probe_{uuid.uuid4().hex[:12]}"
    owner_name = conn.execute(
        """
        SELECT pg_get_userbyid(c.relowner)
        FROM pg_class c
        WHERE c.oid =
            'research_evidence_scenario_input_manifest'::regclass
        """
    ).fetchone()[0]
    current_name = conn.execute("SELECT current_user").fetchone()[0]
    quoted_role = f'"{role_name}"'
    quoted_owner = '"' + owner_name.replace('"', '""') + '"'
    quoted_current = '"' + current_name.replace('"', '""') + '"'
    created = False
    owner_changed = False
    prior = pg._begin_autocommit(conn)
    try:
        try:
            conn.execute(f"CREATE ROLE {quoted_role} NOLOGIN")
            created = True
            conn.execute(f"GRANT {quoted_role} TO {quoted_current}")
            conn.execute(
                "ALTER TABLE research_evidence_scenario_input_manifest "
                f"OWNER TO {quoted_role}"
            )
            owner_changed = True
        except Exception as exc:
            pytest.skip(
                "test database identity cannot perform the disposable "
                f"table-owner mutation: {exc}"
            )
        with pytest.raises(Exception, match="divergent table ownership"):
            pg.apply_v58_research_scenario_input_evaluation(conn)
        conn.execute("ROLLBACK")
    finally:
        if owner_changed:
            conn.execute(
                "ALTER TABLE research_evidence_scenario_input_manifest "
                f"OWNER TO {quoted_owner}"
            )
        if created:
            conn.execute(f"REVOKE {quoted_role} FROM {quoted_current}")
            conn.execute(f"DROP OWNED BY {quoted_role}")
            conn.execute(f"DROP ROLE IF EXISTS {quoted_role}")
            assert conn.execute(
                "SELECT count(*) FROM pg_roles WHERE rolname = %s",
                (role_name,),
            ).fetchone()[0] == 0
        pg._restore_autocommit(conn, prior)
