import json
from decimal import Decimal
from pathlib import Path
import sys
import threading
from uuid import uuid4

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests.evidence_snapshot_pg as pg  # noqa: E402
from research_evidence import pack_service  # noqa: E402
from research_evidence.pack_models import (  # noqa: E402
    MAX_PACK_CANDIDATE_REPRESENTATIONS,
    ResearchEvidenceClaimAnnotationRevisionCreate,
    ResearchEvidenceExplicitProbability,
    ResearchEvidenceProjectContextRevisionCreate,
    ResearchEvidenceUsageAuthorizationDecisionCreate,
    UsageScope,
)
from tests.test_research_evidence_claim_support_schema import (  # noqa: E402
    _direct_insert as insert_support,
    _seed_endpoint as seed_endpoint,
    _seed_pair as seed_pair,
)


PACK_TABLES = (
    "research_evidence_project_context_revision",
    "research_evidence_project_context_sequence_allocator",
    "research_evidence_claim_annotation_revision",
    "research_evidence_claim_annotation_sequence_allocator",
    "research_evidence_usage_authorization_decision",
    "research_evidence_usage_authorization_sequence_allocator",
)

REQUIRED_INDEX_MANIFEST = (
    ("research_evidence_project_context_revision",
     "research_evidence_project_context_revision_pkey", None),
    ("research_evidence_project_context_revision", "uq_repcr_id_project", None),
    ("research_evidence_project_context_revision", "uq_repcr_project_sequence", None),
    ("research_evidence_project_context_revision", "uq_repcr_project_request", None),
    ("research_evidence_project_context_revision", "uq_repcr_supersedes_once", None),
    ("research_evidence_project_context_revision", "idx_repcr_project_sequence", None),
    ("research_evidence_project_context_sequence_allocator",
     "research_evidence_project_context_sequence_allocator_pkey", None),
    ("research_evidence_claim_annotation_revision",
     "research_evidence_claim_annotation_revision_pkey", None),
    ("research_evidence_claim_annotation_revision", "uq_recar_id_project_claim", None),
    ("research_evidence_claim_annotation_revision", "uq_recar_claim_sequence", None),
    ("research_evidence_claim_annotation_revision", "uq_recar_claim_request", None),
    ("research_evidence_claim_annotation_revision", "uq_recar_supersedes_once", None),
    ("research_evidence_claim_annotation_revision",
     "idx_recar_project_claim_sequence", None),
    ("research_evidence_claim_annotation_sequence_allocator",
     "research_evidence_claim_annotation_sequence_allocator_pkey", None),
    ("research_evidence_usage_authorization_decision",
     "research_evidence_usage_authorization_decision_pkey", None),
    ("research_evidence_usage_authorization_decision", "uq_reuad_id_project_scope", None),
    ("research_evidence_usage_authorization_decision", "uq_reuad_scope_sequence", None),
    ("research_evidence_usage_authorization_decision", "uq_reuad_scope_request", None),
    ("research_evidence_usage_authorization_decision", "uq_reuad_supersedes_once", None),
    ("research_evidence_usage_authorization_decision", "idx_reuad_scope_sequence", None),
    ("research_evidence_usage_authorization_sequence_allocator",
     "research_evidence_usage_authorization_sequence_allocator_pkey", None),
)

REQUIRED_JSON_COLLECTION_MANIFEST = (
    ("research_evidence_project_context_revision", "project_limitations_json",
     False, False, True, "research_evidence_prepare_project_context_insert"),
    ("research_evidence_project_context_revision", "unresolved_gaps_json",
     False, False, True, "research_evidence_prepare_project_context_insert"),
    ("research_evidence_claim_annotation_revision", "limitations_json",
     False, False, True, "research_evidence_prepare_claim_annotation_insert"),
    ("research_evidence_claim_annotation_revision", "related_claim_draft_ids_json",
     False, False, True, "research_evidence_prepare_claim_annotation_insert"),
)

# Frozen independently from the SQL implementation.  These are exactly the
# code points stripped by Python str.strip() for the R2.0A-1 contract.
PYTHON_STRIP_CODE_POINTS = (
    9, 10, 11, 12, 13,
    28, 29, 30, 31,
    32,
    133,
    160,
    5760,
    8192, 8193, 8194, 8195, 8196, 8197,
    8198, 8199, 8200, 8201, 8202,
    8232, 8233,
    8239,
    8287,
    12288,
)

USAGE_AUTHORIZATION_COLUMNS = [
    (1, "id", "uuid", "NO", None),
    (2, "project_id", "uuid", "NO", None),
    (3, "claim_intake_item_id", "uuid", "NO", None),
    (4, "evidence_intake_item_id", "uuid", "NO", None),
    (5, "claim_support_assessment_id", "uuid", "NO", None),
    (6, "usage_scope", "text", "NO", None),
    (7, "decision", "text", "NO", None),
    (8, "reason", "text", "NO", None),
    (9, "actor", "text", "NO", None),
    (10, "request_id", "text", "NO", None),
    (11, "claim_draft_id", "uuid", "NO", None),
    (12, "claim_annotation_revision_id", "uuid", "NO", None),
    (13, "claim_review_decision_id", "uuid", "NO", None),
    (14, "evidence_review_decision_id", "uuid", "NO", None),
    (15, "decision_sequence", "int4", "NO", None),
    (16, "supersedes_decision_id", "uuid", "YES", None),
    (17, "recorded_at", "timestamptz", "NO", None),
]


@pytest.fixture
def pack_schema():
    conn = pg.connect()
    with pg.fresh_schema(conn) as schema:
        pg.apply_v51_research(conn)
        pg.apply_v52_research(conn)
        pg.apply_v53_research_intake(conn)
        pg.apply_v54_research_review(conn)
        pg.apply_v55_research_freshness(conn)
        pg.apply_v56_research_claim_support(conn)
        yield conn, schema
    conn.close()


@pytest.fixture
def full_topology_schema():
    conn = pg.connect()
    with pg.fresh_schema(conn) as schema:
        pg.apply_v51_through_v60_research_topology(conn, schema)
        try:
            yield conn, schema
        finally:
            pg.drop_schema(conn, "research_evidence_automation_roi")
    conn.close()


@pytest.fixture(autouse=True)
def feature_enabled(monkeypatch):
    monkeypatch.setenv("MAS_RESEARCH_EVIDENCE_ENABLED", "true")


def test_external_role_prerequisite_manifest_is_frozen():
    assert pg.EXTERNAL_ROLE_MANIFEST == {
        "workflow_migration_owner": {
            "login": True, "inherit": False, "superuser": False,
            "createdb": False, "createrole": False,
            "replication": False, "bypassrls": False,
        },
        "workflow_research_evidence_owner": {
            "login": False, "inherit": False, "superuser": False,
            "createdb": False, "createrole": False,
            "replication": False, "bypassrls": False,
        },
        "workflow_automation_roi_runtime": {
            "login": True, "inherit": False, "superuser": False,
            "createdb": False, "createrole": False,
            "replication": False, "bypassrls": False,
        },
    }
    assert pg.EXTERNAL_MEMBERSHIP_MANIFEST == (
        (
            "workflow_research_evidence_owner",
            "workflow_migration_owner",
            False,
            False,
            True,
        ),
    )


def test_external_roles_are_cluster_scoped_and_credentials_are_stable(pack_schema):
    conn, schema = pack_schema
    first = pg.ensure_external_role_prerequisites(conn)
    second = pg.ensure_external_role_prerequisites(conn)
    assert first is second
    assert first.credential_generation_counts == {
        "workflow_migration_owner": 1,
        "workflow_automation_roi_runtime": 1,
    }
    assert pg.external_role_attributes(conn) == pg.EXTERNAL_ROLE_MANIFEST
    assert pg.external_role_memberships(conn) == pg.EXTERNAL_MEMBERSHIP_MANIFEST
    with first.migration_connection(schema) as migration:
        assert migration.execute(
            "SELECT session_user,current_user"
        ).fetchone() == (
            "workflow_migration_owner", "workflow_migration_owner",
        )


def test_matching_login_roles_without_credential_provenance_fail_closed(pack_schema):
    conn, schema = pack_schema
    original = pg.ensure_external_role_prerequisites(conn)
    before = pg.external_role_snapshot(conn)
    unowned = pg.ExternalRoleHarness.for_unknown_provenance_test(conn)
    with pytest.raises(
        pg.ExternalRoleInfrastructureError,
        match="credential provenance",
    ):
        unowned.provision(conn)
    assert pg.external_role_snapshot(conn) == before
    with original.migration_connection(schema) as migration:
        assert migration.execute(
            "SELECT session_user,current_user"
        ).fetchone() == (
            "workflow_migration_owner", "workflow_migration_owner",
        )


def test_divergent_role_fails_closed_in_dedicated_cluster():
    with pg.dedicated_negative_role_cluster() as conn:
        conn.execute(
            """CREATE ROLE workflow_migration_owner
               NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
               NOREPLICATION NOBYPASSRLS"""
        )
        before = conn.execute(
            """SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,
                      rolcreaterole,rolreplication,rolbypassrls
               FROM pg_roles WHERE rolname='workflow_migration_owner'"""
        ).fetchone()
        harness = pg.ExternalRoleHarness.for_unknown_provenance_test(conn)
        with pytest.raises(
            pg.ExternalRoleInfrastructureError,
            match="attribute divergence",
        ):
            harness.provision(conn)
        after = conn.execute(
            """SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,
                      rolcreaterole,rolreplication,rolbypassrls
               FROM pg_roles WHERE rolname='workflow_migration_owner'"""
        ).fetchone()
        memberships = conn.execute(
            """SELECT count(*) FROM pg_auth_members membership
               JOIN pg_roles member ON member.oid=membership.member
               WHERE member.rolname='workflow_migration_owner'"""
        ).fetchone()[0]
        password_is_absent = conn.execute(
            """SELECT rolpassword IS NULL FROM pg_authid
               WHERE rolname='workflow_migration_owner'"""
        ).fetchone()[0]
        assert before == after == (False, False, False, False, False, False, False)
        assert memberships == 0
        assert password_is_absent


def _sql_string_array_valid(conn, values, maximum_items=10, maximum_length=500):
    return conn.execute(
        "SELECT research_evidence_pack_string_array_valid(%s::jsonb,%s,%s)",
        (json.dumps(values, ensure_ascii=False), maximum_items, maximum_length),
    ).fetchone()[0]


@pytest.mark.parametrize(
    "code_point", PYTHON_STRIP_CODE_POINTS,
    ids=lambda value: f"U+{value:04X}",
)
def test_python_and_sql_edge_whitespace_normalization_parity(pack_schema, code_point):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    whitespace = chr(code_point)
    wrapped = f"{whitespace}alpha{whitespace}"
    model = ResearchEvidenceProjectContextRevisionCreate(
        project_id="00000000-0000-0000-0000-000000000001",
        request_id="request", research_question="question",
        project_limitations=[wrapped], unresolved_gaps=[], actor="operator",
    )
    assert model.project_limitations == ("alpha",)
    assert _sql_string_array_valid(conn, [wrapped])
    assert not _sql_string_array_valid(conn, ["alpha", wrapped])


@pytest.mark.parametrize(
    "code_point", PYTHON_STRIP_CODE_POINTS,
    ids=lambda value: f"U+{value:04X}",
)
def test_sql_rejects_frozen_whitespace_only_and_measures_trimmed_length(
    pack_schema, code_point
):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    whitespace = chr(code_point)
    assert not _sql_string_array_valid(conn, [whitespace])
    assert _sql_string_array_valid(conn, [f"{whitespace}{'x' * 500}{whitespace}"])
    assert not _sql_string_array_valid(conn, [f"{whitespace}{'x' * 501}{whitespace}"])


def test_string_array_normalization_preserves_internal_whitespace_and_case(pack_schema):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    values = ["alpha beta", "alpha  beta", "alpha\tbeta", "alpha\nbeta",
              "alpha\u2003beta", "Alpha", "alpha"]
    model = ResearchEvidenceProjectContextRevisionCreate(
        project_id="00000000-0000-0000-0000-000000000001",
        request_id="request", research_question="question",
        project_limitations=values, unresolved_gaps=[], actor="operator",
    )
    assert list(model.project_limitations) == values
    assert _sql_string_array_valid(conn, values)


def test_string_array_normalization_does_not_strip_bom(pack_schema):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    bom_wrapped = "\ufeffalpha\ufeff"
    model = ResearchEvidenceProjectContextRevisionCreate(
        project_id="00000000-0000-0000-0000-000000000001",
        request_id="request", research_question="question",
        project_limitations=[bom_wrapped], unresolved_gaps=[], actor="operator",
    )
    assert model.project_limitations == (bom_wrapped,)
    assert _sql_string_array_valid(conn, [bom_wrapped])
    assert _sql_string_array_valid(conn, ["alpha", bom_wrapped])


def test_string_array_sql_item_count_blank_and_length_boundaries(pack_schema):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    assert _sql_string_array_valid(conn, [])
    assert _sql_string_array_valid(conn, ["alpha", " Alpha ", "a  b"])
    assert not _sql_string_array_valid(conn, [" "])
    assert _sql_string_array_valid(conn, ["x" * 500])
    assert not _sql_string_array_valid(conn, ["x" * 501])
    assert _sql_string_array_valid(conn, [str(index) for index in range(10)])
    assert not _sql_string_array_valid(conn, [str(index) for index in range(11)])


@pytest.mark.parametrize("member", [1, True, {"a": 1}, ["nested"], None])
def test_string_array_sql_rejects_every_non_string_json_member(pack_schema, member):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    assert not _sql_string_array_valid(conn, [member])


def _assert_contract_reapply_fails_without_repair(conn, snapshot_sql):
    before = conn.execute(snapshot_sql).fetchall()
    with pytest.raises(Exception, match="contract violation"):
        pg.apply_v61_research_evidence_pack(conn)
    after = conn.execute(snapshot_sql).fetchall()
    assert after == before


def test_reapply_rejects_renamed_required_column_without_repair(pack_schema):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    conn.execute(
        "ALTER TABLE research_evidence_project_context_sequence_allocator "
        "RENAME COLUMN last_sequence TO last_sequence_drift"
    )
    conn.commit()
    _assert_contract_reapply_fails_without_repair(
        conn,
        """SELECT attnum,attname,atttypid,atttypmod,attnotnull
           FROM pg_attribute
           WHERE attrelid='research_evidence_project_context_sequence_allocator'::regclass
             AND attnum>0 AND NOT attisdropped ORDER BY attnum""",
    )


def test_reapply_rejects_named_check_replaced_with_true_without_repair(pack_schema):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    conn.execute(
        "ALTER TABLE research_evidence_project_context_revision "
        "DROP CONSTRAINT ck_repcr_question"
    )
    conn.execute(
        "ALTER TABLE research_evidence_project_context_revision "
        "ADD CONSTRAINT ck_repcr_question CHECK (true)"
    )
    conn.commit()
    _assert_contract_reapply_fails_without_repair(
        conn,
        """SELECT conname,pg_get_expr(conbin,conrelid),convalidated
           FROM pg_constraint
           WHERE conrelid='research_evidence_project_context_revision'::regclass
             AND conname='ck_repcr_question'""",
    )


def test_reapply_rejects_when_false_mutation_trigger_without_repair(pack_schema):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    conn.execute(
        "DROP TRIGGER trg_repcr_no_mutation "
        "ON research_evidence_project_context_revision"
    )
    conn.execute(
        """CREATE TRIGGER trg_repcr_no_mutation
           BEFORE UPDATE OR DELETE ON research_evidence_project_context_revision
           FOR EACH ROW WHEN (false) EXECUTE FUNCTION slicea_reject_mutation()"""
    )
    conn.execute(
        "ALTER TABLE research_evidence_project_context_revision "
        "ENABLE ALWAYS TRIGGER trg_repcr_no_mutation"
    )
    conn.commit()
    _assert_contract_reapply_fails_without_repair(
        conn,
        """SELECT tgname,tgtype,tgenabled,tgqual IS NULL,pg_get_triggerdef(oid)
           FROM pg_trigger
           WHERE tgrelid='research_evidence_project_context_revision'::regclass
             AND tgname='trg_repcr_no_mutation'""",
    )


def test_reapply_rejects_allocator_history_discontinuity_without_repair(pack_schema):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    project = pg.insert_project(conn, name="corrupt-history")
    conn.execute(
        "ALTER TABLE research_evidence_project_context_revision "
        "DISABLE TRIGGER trg_repcr_prepare_insert"
    )
    ids = [
        "10000000-0000-0000-0000-000000000002",
        "10000000-0000-0000-0000-000000000003",
        "10000000-0000-0000-0000-000000000005",
    ]
    for sequence, row_id, predecessor in (
        (2, ids[0], None), (3, ids[1], ids[0]), (5, ids[2], ids[1])
    ):
        conn.execute(
            """INSERT INTO research_evidence_project_context_revision
               (id,project_id,request_id,research_question,
                project_limitations_json,unresolved_gaps_json,actor,
                context_sequence,supersedes_context_revision_id,recorded_at)
               VALUES(%s,%s,%s,'question','[]','[]','operator',%s,%s,now())""",
            (row_id, project, f"corrupt-{sequence}", sequence, predecessor),
        )
    conn.execute(
        "INSERT INTO research_evidence_project_context_sequence_allocator "
        "(project_id,last_sequence) VALUES(%s,3)", (project,)
    )
    conn.execute(
        "ALTER TABLE research_evidence_project_context_revision "
        "ENABLE ALWAYS TRIGGER trg_repcr_prepare_insert"
    )
    conn.commit()
    _assert_contract_reapply_fails_without_repair(
        conn,
        """SELECT context_sequence,supersedes_context_revision_id::text
           FROM research_evidence_project_context_revision
           UNION ALL
           SELECT last_sequence,NULL
           FROM research_evidence_project_context_sequence_allocator
           ORDER BY 1,2""",
    )


CATALOG_DRIFT_CASES = (
    (
        "wrong_column_type",
        ["ALTER TABLE research_evidence_project_context_sequence_allocator "
         "ALTER COLUMN last_sequence TYPE bigint"],
        "SELECT atttypid,atttypmod FROM pg_attribute WHERE "
        "attrelid='research_evidence_project_context_sequence_allocator'::regclass "
        "AND attname='last_sequence'",
    ),
    (
        "extra_column",
        ["ALTER TABLE research_evidence_project_context_sequence_allocator "
         "ADD COLUMN extra_contract_column text"],
        "SELECT attnum,attname FROM pg_attribute WHERE "
        "attrelid='research_evidence_project_context_sequence_allocator'::regclass "
        "AND attnum>0 AND NOT attisdropped ORDER BY attnum",
    ),
    (
        "changed_check_expression",
        ["ALTER TABLE research_evidence_project_context_revision "
         "DROP CONSTRAINT ck_repcr_actor",
         "ALTER TABLE research_evidence_project_context_revision "
         "ADD CONSTRAINT ck_repcr_actor CHECK (char_length(actor)>0)"],
        "SELECT pg_get_constraintdef(oid,true) FROM pg_constraint "
        "WHERE conname='ck_repcr_actor' AND "
        "conrelid='research_evidence_project_context_revision'::regclass",
    ),
    (
        "foreign_key_action",
        ["ALTER TABLE research_evidence_project_context_sequence_allocator "
         "DROP CONSTRAINT fk_repcsa_project",
         "ALTER TABLE research_evidence_project_context_sequence_allocator "
         "ADD CONSTRAINT fk_repcsa_project FOREIGN KEY(project_id) "
         "REFERENCES projects(id) ON DELETE CASCADE"],
        "SELECT confupdtype,confdeltype,confmatchtype FROM pg_constraint "
        "WHERE conname='fk_repcsa_project' AND "
        "conrelid='research_evidence_project_context_sequence_allocator'::regclass",
    ),
    (
        "foreign_key_target",
        ["ALTER TABLE research_evidence_project_context_sequence_allocator "
         "DROP CONSTRAINT fk_repcsa_project",
         "ALTER TABLE research_evidence_project_context_sequence_allocator "
         "ADD CONSTRAINT fk_repcsa_project FOREIGN KEY(project_id) "
         "REFERENCES research_claim_draft(id) ON DELETE RESTRICT"],
        "SELECT confrelid,conkey::text,confkey::text FROM pg_constraint "
        "WHERE conname='fk_repcsa_project' AND "
        "conrelid='research_evidence_project_context_sequence_allocator'::regclass",
    ),
    (
        "trigger_wrong_function",
        ["DROP TRIGGER trg_repcr_prepare_insert ON "
         "research_evidence_project_context_revision",
         "CREATE TRIGGER trg_repcr_prepare_insert BEFORE INSERT ON "
         "research_evidence_project_context_revision FOR EACH ROW "
         "EXECUTE FUNCTION slicea_reject_mutation()",
         "ALTER TABLE research_evidence_project_context_revision ENABLE ALWAYS "
         "TRIGGER trg_repcr_prepare_insert"],
        "SELECT tgfoid,tgtype,tgenabled,tgqual,tgattr FROM pg_trigger WHERE "
        "tgname='trg_repcr_prepare_insert' AND "
        "tgrelid='research_evidence_project_context_revision'::regclass",
    ),
    (
        "trigger_wrong_target",
        ["DROP TRIGGER trg_repcr_no_mutation ON "
         "research_evidence_project_context_revision",
         "CREATE TRIGGER trg_repcr_no_mutation BEFORE UPDATE OR DELETE ON "
         "research_evidence_claim_annotation_revision FOR EACH ROW "
         "EXECUTE FUNCTION slicea_reject_mutation()",
         "ALTER TABLE research_evidence_claim_annotation_revision ENABLE ALWAYS "
         "TRIGGER trg_repcr_no_mutation"],
        "SELECT tgrelid,tgfoid,tgtype,tgenabled FROM pg_trigger "
        "WHERE tgname='trg_repcr_no_mutation'",
    ),
    (
        "trigger_update_of",
        ["DROP TRIGGER trg_repcr_no_mutation ON "
         "research_evidence_project_context_revision",
         "CREATE TRIGGER trg_repcr_no_mutation BEFORE UPDATE OF actor ON "
         "research_evidence_project_context_revision FOR EACH ROW "
         "EXECUTE FUNCTION slicea_reject_mutation()",
         "ALTER TABLE research_evidence_project_context_revision ENABLE ALWAYS "
         "TRIGGER trg_repcr_no_mutation"],
        "SELECT tgtype,tgattr,tgenabled FROM pg_trigger WHERE "
        "tgname='trg_repcr_no_mutation' AND "
        "tgrelid='research_evidence_project_context_revision'::regclass",
    ),
    (
        "trigger_not_always",
        ["ALTER TABLE research_evidence_project_context_revision DISABLE TRIGGER "
         "trg_repcr_no_mutation"],
        "SELECT tgtype,tgattr,tgenabled FROM pg_trigger WHERE "
        "tgname='trg_repcr_no_mutation' AND "
        "tgrelid='research_evidence_project_context_revision'::regclass",
    ),
    (
        "index_key_order",
        ["DROP INDEX idx_repcr_project_sequence",
         "CREATE INDEX idx_repcr_project_sequence ON "
         "research_evidence_project_context_revision(context_sequence DESC,project_id)"],
        "SELECT indkey::text,indoption::text,indisunique,indpred FROM pg_index "
        "WHERE indexrelid='idx_repcr_project_sequence'::regclass",
    ),
    (
        "index_uniqueness",
        ["DROP INDEX idx_repcr_project_sequence",
         "CREATE UNIQUE INDEX idx_repcr_project_sequence ON "
         "research_evidence_project_context_revision(project_id,context_sequence DESC)"],
        "SELECT indkey::text,indoption::text,indisunique,indpred FROM pg_index "
        "WHERE indexrelid='idx_repcr_project_sequence'::regclass",
    ),
    (
        "index_predicate",
        ["DROP INDEX idx_repcr_project_sequence",
         "CREATE INDEX idx_repcr_project_sequence ON "
         "research_evidence_project_context_revision(project_id,context_sequence DESC) "
         "WHERE context_sequence>0"],
        "SELECT indkey::text,indoption::text,indisunique,pg_get_expr(indpred,indrelid) "
        "FROM pg_index WHERE indexrelid='idx_repcr_project_sequence'::regclass",
    ),
    (
        "function_body",
        ["CREATE OR REPLACE FUNCTION research_evidence_pack_string_array_valid"
         "(value jsonb,maximum_items integer,maximum_length integer) "
         "RETURNS boolean LANGUAGE plpgsql IMMUTABLE "
         "SECURITY DEFINER SET search_path=pg_catalog AS $$BEGIN RETURN true; END$$"],
        "SELECT md5(prosrc),prosecdef,proconfig FROM pg_proc WHERE "
        "oid='research_evidence_pack_string_array_valid(jsonb,integer,integer)'::regprocedure",
    ),
    (
        "function_security_invoker",
        ["ALTER FUNCTION research_evidence_pack_string_array_valid"
         "(jsonb,integer,integer) SECURITY INVOKER"],
        "SELECT prosecdef,proconfig FROM pg_proc WHERE "
        "oid='research_evidence_pack_string_array_valid(jsonb,integer,integer)'::regprocedure",
    ),
    (
        "function_search_path",
        ["ALTER FUNCTION research_evidence_pack_string_array_valid"
         "(jsonb,integer,integer) SET search_path=public"],
        "SELECT prosecdef,proconfig FROM pg_proc WHERE "
        "oid='research_evidence_pack_string_array_valid(jsonb,integer,integer)'::regprocedure",
    ),
    (
        "function_overload",
        ["CREATE FUNCTION research_evidence_pack_string_array_valid"
         "(text,integer,integer) RETURNS boolean LANGUAGE sql IMMUTABLE "
         "AS $$SELECT true$$"],
        "SELECT oid::regprocedure::text,prorettype,prolang FROM pg_proc WHERE "
        "pronamespace=current_schema()::regnamespace AND "
        "proname='research_evidence_pack_string_array_valid' ORDER BY 1",
    ),
)


@pytest.mark.parametrize(
    "case,statements,snapshot_sql", CATALOG_DRIFT_CASES,
    ids=[case[0] for case in CATALOG_DRIFT_CASES],
)
def test_exact_catalog_drift_matrix_fails_closed(
    pack_schema, case, statements, snapshot_sql
):
    del case
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    for statement in statements:
        conn.execute(statement)
    conn.commit()
    _assert_contract_reapply_fails_without_repair(conn, snapshot_sql)


@pytest.mark.parametrize(
    "object_kind,target,privilege,snapshot_sql",
    [
        ("table", "research_evidence_project_context_revision", "SELECT",
         "SELECT relacl FROM pg_class WHERE oid="
         "'research_evidence_project_context_revision'::regclass"),
        ("allocator", "research_evidence_project_context_sequence_allocator", "UPDATE",
         "SELECT relacl FROM pg_class WHERE oid="
         "'research_evidence_project_context_sequence_allocator'::regclass"),
        ("function", "research_evidence_pack_string_array_valid"
         "(jsonb,integer,integer)", "EXECUTE",
         "SELECT proacl FROM pg_proc WHERE oid="
         "'research_evidence_pack_string_array_valid(jsonb,integer,integer)'::regprocedure"),
    ],
)
def test_named_role_acl_drift_fails_closed(
    pack_schema, object_kind, target, privilege, snapshot_sql
):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    role = f"v61_acl_probe_{uuid4().hex}"
    conn.execute(f'CREATE ROLE "{role}" NOLOGIN')
    if object_kind == "function":
        conn.execute(f'GRANT {privilege} ON FUNCTION {target} TO "{role}"')
    else:
        conn.execute(f'GRANT {privilege} ON TABLE {target} TO "{role}"')
    conn.commit()
    _assert_contract_reapply_fails_without_repair(conn, snapshot_sql)


def _pack_rows_snapshot(conn):
    orderings = {
        "research_evidence_project_context_revision": "project_id,context_sequence",
        "research_evidence_project_context_sequence_allocator": "project_id",
        "research_evidence_claim_annotation_revision": (
            "project_id,claim_draft_id,annotation_sequence"
        ),
        "research_evidence_claim_annotation_sequence_allocator": (
            "project_id,claim_draft_id"
        ),
        "research_evidence_usage_authorization_decision": (
            "project_id,claim_intake_item_id,evidence_intake_item_id,"
            "usage_scope,decision_sequence"
        ),
        "research_evidence_usage_authorization_sequence_allocator": (
            "project_id,claim_intake_item_id,evidence_intake_item_id,usage_scope"
        ),
    }
    return tuple(
        conn.execute(
            f"SELECT to_jsonb(row_value)::text FROM {relation} row_value "
            f"ORDER BY {orderings[relation]}"
        ).fetchall()
        for relation in PACK_TABLES
    )


def _seed_context_history(conn, tag):
    project = pg.insert_project(conn, name=tag)
    conn.execute(
        """INSERT INTO research_evidence_project_context_revision
           (project_id,request_id,research_question,project_limitations_json,
            unresolved_gaps_json,actor)
           VALUES(%s,%s,'question','[]','[]','operator')""",
        (project, f"{tag}-request"),
    )
    return project


def test_v61_relation_index_collection_and_clean_column_acl_manifests(pack_schema):
    conn, schema = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    relations = conn.execute(
        """SELECT namespace.nspname,relation.relname,
                  relation.relrowsecurity,relation.relforcerowsecurity
           FROM pg_class relation
           JOIN pg_namespace namespace ON namespace.oid=relation.relnamespace
           WHERE namespace.nspname=%s AND relation.relname=ANY(%s::text[])
           ORDER BY relation.relname""",
        (schema, list(PACK_TABLES)),
    ).fetchall()
    assert relations == sorted(
        (schema, relation, False, False) for relation in PACK_TABLES
    )
    assert conn.execute(
        """SELECT count(*) FROM pg_attribute attribute
           JOIN pg_class relation ON relation.oid=attribute.attrelid
           WHERE relation.relnamespace=current_schema()::regnamespace
             AND relation.relname=ANY(%s::text[])
             AND attribute.attnum>0 AND NOT attribute.attisdropped
             AND attribute.attacl IS NOT NULL""",
        (list(PACK_TABLES),),
    ).fetchone()[0] == 0
    indexes = conn.execute(
        """SELECT table_relation.relname,index_relation.relname,
                  index_relation.reloptions
           FROM pg_index index_info
           JOIN pg_class index_relation ON index_relation.oid=index_info.indexrelid
           JOIN pg_class table_relation ON table_relation.oid=index_info.indrelid
           WHERE table_relation.relnamespace=current_schema()::regnamespace
             AND table_relation.relname=ANY(%s::text[])
           ORDER BY table_relation.relname,index_relation.relname""",
        (list(PACK_TABLES),),
    ).fetchall()
    assert indexes == sorted(REQUIRED_INDEX_MANIFEST)
    assert len(REQUIRED_JSON_COLLECTION_MANIFEST) == 4
    for relation, column, sql_nullable, _, _, _ in REQUIRED_JSON_COLLECTION_MANIFEST:
        assert conn.execute(
            """SELECT NOT attribute.attnotnull
               FROM pg_attribute attribute
               WHERE attribute.attrelid=%s::regclass
                 AND attribute.attname=%s""",
            (relation, column),
        ).fetchone() == (sql_nullable,)


@pytest.mark.parametrize(
    "privilege,grant_option",
    [("SELECT", False), ("UPDATE", False), ("SELECT", True)],
    ids=["unexpected-grantee-select", "unexpected-privilege-update",
         "unexpected-grant-option"],
)
def test_reapply_rejects_column_acl_drift_without_repair(
    pack_schema, privilege, grant_option,
):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    _seed_context_history(conn, f"column-acl-{privilege}-{grant_option}")
    assert conn.execute(
        """SELECT attacl IS NULL FROM pg_attribute
           WHERE attrelid='research_evidence_project_context_revision'::regclass
             AND attname='research_question'"""
    ).fetchone() == (True,)
    role = f"v61_column_acl_probe_{uuid4().hex}"
    conn.execute(
        pg.psycopg_module().sql.SQL("CREATE ROLE {} NOLOGIN").format(
            pg.psycopg_module().sql.Identifier(role)
        )
    )
    grant = pg.psycopg_module().sql.SQL(
        f"GRANT {privilege} (research_question) ON TABLE "
        "research_evidence_project_context_revision TO {}"
        + (" WITH GRANT OPTION" if grant_option else "")
    ).format(pg.psycopg_module().sql.Identifier(role))
    conn.execute(grant)
    conn.commit()
    acl_sql = """SELECT attribute.attacl::text,acl.grantee,acl.grantor,
                        acl.privilege_type,acl.is_grantable
                 FROM pg_attribute attribute
                 LEFT JOIN LATERAL aclexplode(attribute.attacl) acl ON true
                 WHERE attribute.attrelid=
                   'research_evidence_project_context_revision'::regclass
                   AND attribute.attname='research_question'
                 ORDER BY 2,3,4,5"""
    acl_before = conn.execute(acl_sql).fetchall()
    rows_before = _pack_rows_snapshot(conn)
    assert acl_before[0][0] is not None
    assert any(row[3:] == (privilege, grant_option) for row in acl_before)
    with pytest.raises(Exception, match="contract violation"):
        pg.apply_v61_research_evidence_pack(conn)
    assert conn.execute(acl_sql).fetchall() == acl_before
    assert _pack_rows_snapshot(conn) == rows_before


def test_reapply_rejects_required_index_reloptions_drift_without_repair(pack_schema):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    _seed_context_history(conn, "index-reloptions")
    assert conn.execute(
        "SELECT reloptions FROM pg_class WHERE "
        "oid='idx_repcr_project_sequence'::regclass"
    ).fetchone() == (None,)
    conn.execute("ALTER INDEX idx_repcr_project_sequence SET (fillfactor=70)")
    conn.commit()
    snapshot_sql = (
        "SELECT reloptions FROM pg_class WHERE "
        "oid='idx_repcr_project_sequence'::regclass"
    )
    options_before = conn.execute(snapshot_sql).fetchall()
    rows_before = _pack_rows_snapshot(conn)
    assert options_before == [(["fillfactor=70"],)]
    with pytest.raises(Exception, match="contract violation"):
        pg.apply_v61_research_evidence_pack(conn)
    assert conn.execute(snapshot_sql).fetchall() == options_before
    assert _pack_rows_snapshot(conn) == rows_before


@pytest.mark.parametrize(
    "statements,expected",
    [
        (("ENABLE ROW LEVEL SECURITY",), (True, False)),
        (("ENABLE ROW LEVEL SECURITY", "FORCE ROW LEVEL SECURITY"), (True, True)),
    ],
    ids=["enable-rls", "enable-and-force-rls"],
)
def test_reapply_rejects_material_relation_state_drift_without_repair(
    pack_schema, statements, expected,
):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    _seed_context_history(conn, f"relation-state-{expected}")
    for statement in statements:
        conn.execute(
            "ALTER TABLE research_evidence_project_context_revision " + statement
        )
    conn.commit()
    snapshot_sql = """SELECT relrowsecurity,relforcerowsecurity
                       FROM pg_class WHERE oid=
                         'research_evidence_project_context_revision'::regclass"""
    assert conn.execute(snapshot_sql).fetchone() == expected
    rows_before = _pack_rows_snapshot(conn)
    with pytest.raises(Exception, match="contract violation"):
        pg.apply_v61_research_evidence_pack(conn)
    assert conn.execute(snapshot_sql).fetchone() == expected
    assert _pack_rows_snapshot(conn) == rows_before


def test_wrong_owner_drift_fails_closed(pack_schema):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    role = f"v61_owner_probe_{uuid4().hex}"
    conn.execute(f'CREATE ROLE "{role}" NOLOGIN')
    conn.execute(
        f'ALTER TABLE research_evidence_project_context_sequence_allocator '
        f'OWNER TO "{role}"'
    )
    conn.commit()
    _assert_contract_reapply_fails_without_repair(
        conn,
        """SELECT relowner,relacl FROM pg_class WHERE
           oid='research_evidence_project_context_sequence_allocator'::regclass""",
    )


def _context_history_snapshot(conn):
    return conn.execute(
        """SELECT id::text,project_id::text,context_sequence,
                  supersedes_context_revision_id::text,
                  project_limitations_json::text,unresolved_gaps_json::text
           FROM research_evidence_project_context_revision ORDER BY id"""
    ).fetchall()


def test_reapply_rejects_required_trigger_name_on_unrelated_relation_without_repair(
    pack_schema,
):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    project = pg.insert_project(conn, name="unrelated-trigger-target-history")
    conn.execute(
        """INSERT INTO research_evidence_project_context_revision
           (project_id,request_id,research_question,project_limitations_json,
            unresolved_gaps_json,actor)
           VALUES(%s,'unrelated-trigger-target','question','[]','[]','operator')""",
        (project,),
    )
    conn.execute(
        "DROP TRIGGER trg_repcr_no_mutation "
        "ON research_evidence_project_context_revision"
    )
    conn.execute("CREATE TABLE v61_unrelated_trigger_target (id integer)")
    conn.execute(
        """CREATE TRIGGER trg_repcr_no_mutation
           BEFORE UPDATE OR DELETE ON v61_unrelated_trigger_target
           FOR EACH ROW EXECUTE FUNCTION slicea_reject_mutation()"""
    )
    conn.execute(
        "ALTER TABLE v61_unrelated_trigger_target "
        "ENABLE ALWAYS TRIGGER trg_repcr_no_mutation"
    )
    conn.commit()

    history_sql = (
        "SELECT to_jsonb(row_value)::text FROM {} row_value ORDER BY {}"
    )
    histories_before = tuple(
        conn.execute(history_sql.format(relation, ordering)).fetchall()
        for relation, ordering in (
            (
                "research_evidence_project_context_revision",
                "project_id,context_sequence",
            ),
            (
                "research_evidence_claim_annotation_revision",
                "project_id,claim_draft_id,annotation_sequence",
            ),
            (
                "research_evidence_usage_authorization_decision",
                "project_id,claim_intake_item_id,evidence_intake_item_id,"
                "usage_scope,decision_sequence",
            ),
        )
    )
    allocators_before = tuple(
        conn.execute(history_sql.format(relation, ordering)).fetchall()
        for relation, ordering in (
            (
                "research_evidence_project_context_sequence_allocator",
                "project_id",
            ),
            (
                "research_evidence_claim_annotation_sequence_allocator",
                "project_id,claim_draft_id",
            ),
            (
                "research_evidence_usage_authorization_sequence_allocator",
                "project_id,claim_intake_item_id,evidence_intake_item_id,usage_scope",
            ),
        )
    )
    trigger_sql = """SELECT c.relname,t.tgname,t.tgfoid,t.tgtype,t.tgenabled,
                            t.tgattr::text,t.tgqual,t.tgargs
                     FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
                     WHERE c.oid=%s::regclass AND NOT t.tgisinternal
                     ORDER BY t.tgname"""
    intended_before = conn.execute(
        trigger_sql, ("research_evidence_project_context_revision",)
    ).fetchall()
    unrelated_before = conn.execute(
        trigger_sql, ("v61_unrelated_trigger_target",)
    ).fetchall()
    assert not any(row[1] == "trg_repcr_no_mutation" for row in intended_before)
    assert [row[1] for row in unrelated_before] == ["trg_repcr_no_mutation"]

    with pytest.raises(Exception, match="contract violation"):
        pg.apply_v61_research_evidence_pack(conn)

    assert conn.execute(
        trigger_sql, ("research_evidence_project_context_revision",)
    ).fetchall() == intended_before
    assert conn.execute(
        trigger_sql, ("v61_unrelated_trigger_target",)
    ).fetchall() == unrelated_before
    assert tuple(
        conn.execute(history_sql.format(relation, ordering)).fetchall()
        for relation, ordering in (
            (
                "research_evidence_project_context_revision",
                "project_id,context_sequence",
            ),
            (
                "research_evidence_claim_annotation_revision",
                "project_id,claim_draft_id,annotation_sequence",
            ),
            (
                "research_evidence_usage_authorization_decision",
                "project_id,claim_intake_item_id,evidence_intake_item_id,"
                "usage_scope,decision_sequence",
            ),
        )
    ) == histories_before
    assert tuple(
        conn.execute(history_sql.format(relation, ordering)).fetchall()
        for relation, ordering in (
            (
                "research_evidence_project_context_sequence_allocator",
                "project_id",
            ),
            (
                "research_evidence_claim_annotation_sequence_allocator",
                "project_id,claim_draft_id",
            ),
            (
                "research_evidence_usage_authorization_sequence_allocator",
                "project_id,claim_intake_item_id,evidence_intake_item_id,usage_scope",
            ),
        )
    ) == allocators_before


def test_reapply_rejects_unique_constraint_masquerade_without_repair(
    full_topology_schema,
):
    conn, _ = full_topology_schema
    pg.apply_v61_research_evidence_pack(conn)
    project = pg.insert_project(conn, name="unique-masquerade-history")
    conn.execute(
        """INSERT INTO research_evidence_project_context_revision
           (project_id,request_id,research_question,project_limitations_json,
            unresolved_gaps_json,actor)
           VALUES(%s,'unique-masquerade','question','[]','[]','operator')""",
        (project,),
    )
    conn.execute(
        "ALTER TABLE research_evidence_project_context_revision "
        "DROP CONSTRAINT uq_repcr_project_request"
    )
    conn.execute(
        "ALTER TABLE research_evidence_project_context_revision "
        "ADD CONSTRAINT uq_repcr_project_request CHECK (true)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX uq_repcr_project_request ON "
        "research_evidence_project_context_revision(project_id,request_id)"
    )
    conn.commit()
    history_before = _context_history_snapshot(conn)
    catalog_sql = """SELECT c.conname,c.contype,c.conkey::text,c.conindid,
                            i.indexrelid::regclass::text,i.indisunique,
                            i.indkey::text
                     FROM pg_constraint c
                     LEFT JOIN pg_index i
                       ON i.indexrelid='uq_repcr_project_request'::regclass
                     WHERE c.conrelid=
                       'research_evidence_project_context_revision'::regclass
                       AND c.conname='uq_repcr_project_request'"""
    catalog_before = conn.execute(catalog_sql).fetchall()
    with pytest.raises(Exception, match="contract violation"):
        pg.apply_v61_research_evidence_pack(conn)
    assert conn.execute(catalog_sql).fetchall() == catalog_before
    assert catalog_before[0][1] == "c"
    assert catalog_before[0][4] == "uq_repcr_project_request"
    assert catalog_before[0][5]
    assert _context_history_snapshot(conn) == history_before


@pytest.mark.parametrize(
    "relation",
    [
        "research_evidence_project_context_revision",
        "research_evidence_project_context_sequence_allocator",
    ],
    ids=["ledger", "allocator"],
)
def test_reapply_rejects_extra_noninternal_trigger_without_repair(
    pack_schema, relation,
):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    project = pg.insert_project(conn, name=f"extra-trigger-{relation}")
    conn.execute(
        """INSERT INTO research_evidence_project_context_revision
           (project_id,request_id,research_question,project_limitations_json,
            unresolved_gaps_json,actor)
           VALUES(%s,'extra-trigger-history','question','[]','[]','operator')""",
        (project,),
    )
    conn.execute(
        f"CREATE TRIGGER v61_extra_noninternal BEFORE TRUNCATE ON {relation} "
        "FOR EACH STATEMENT EXECUTE FUNCTION slicea_reject_mutation()"
    )
    conn.commit()
    history_before = _context_history_snapshot(conn)
    trigger_sql = """SELECT c.relname,t.tgname,t.tgfoid,t.tgtype,t.tgenabled,
                            t.tgattr::text,t.tgqual,t.tgargs
                     FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
                     WHERE c.oid=ANY(ARRAY[
                       'research_evidence_project_context_revision'::regclass,
                       'research_evidence_project_context_sequence_allocator'::regclass,
                       'research_evidence_claim_annotation_revision'::regclass,
                       'research_evidence_claim_annotation_sequence_allocator'::regclass,
                       'research_evidence_usage_authorization_decision'::regclass,
                       'research_evidence_usage_authorization_sequence_allocator'::regclass])
                       AND NOT t.tgisinternal ORDER BY c.relname,t.tgname"""
    triggers_before = conn.execute(trigger_sql).fetchall()
    with pytest.raises(Exception, match="contract violation"):
        pg.apply_v61_research_evidence_pack(conn)
    assert conn.execute(trigger_sql).fetchall() == triggers_before
    assert any(row[1] == "v61_extra_noninternal" for row in triggers_before)
    assert _context_history_snapshot(conn) == history_before


def _create_drift_collation(conn):
    conn.execute('CREATE COLLATION v61_drift_collation FROM "C"')
    return conn.execute(
        "SELECT oid FROM pg_collation WHERE collname='v61_drift_collation' "
        "AND collnamespace=current_schema()::regnamespace"
    ).fetchone()[0]


def test_reapply_rejects_column_collation_drift_without_repair(pack_schema):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    drift_oid = _create_drift_collation(conn)
    expected_oid = conn.execute(
        "SELECT typcollation FROM pg_type WHERE oid='text'::regtype"
    ).fetchone()[0]
    assert drift_oid != expected_oid
    conn.execute(
        'ALTER TABLE research_evidence_project_context_revision '
        'ALTER COLUMN research_question TYPE text '
        'COLLATE "v61_drift_collation" USING research_question::text'
    )
    conn.commit()
    snapshot_sql = """SELECT attcollation FROM pg_attribute WHERE attrelid=
                       'research_evidence_project_context_revision'::regclass
                       AND attname='research_question'"""
    assert conn.execute(snapshot_sql).fetchone()[0] == drift_oid
    _assert_contract_reapply_fails_without_repair(conn, snapshot_sql)


def test_reapply_rejects_index_collation_drift_without_repair(pack_schema):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    drift_oid = _create_drift_collation(conn)
    conn.execute("DROP INDEX idx_reuad_scope_sequence")
    conn.execute(
        'ALTER TABLE research_evidence_usage_authorization_decision '
        'ALTER COLUMN usage_scope TYPE text COLLATE "v61_drift_collation" '
        'USING usage_scope::text'
    )
    conn.execute(
        'CREATE INDEX idx_reuad_scope_sequence ON '
        'research_evidence_usage_authorization_decision('
        'project_id,claim_intake_item_id,evidence_intake_item_id,'
        'usage_scope COLLATE "v61_drift_collation",decision_sequence DESC)'
    )
    conn.commit()
    snapshot_sql = """SELECT a.attcollation,i.indcollation::text
                       FROM pg_attribute a CROSS JOIN pg_index i
                       WHERE a.attrelid=
                         'research_evidence_usage_authorization_decision'::regclass
                         AND a.attname='usage_scope'
                         AND i.indexrelid='idx_reuad_scope_sequence'::regclass"""
    before = conn.execute(snapshot_sql).fetchone()
    assert before[0] == drift_oid
    assert str(drift_oid) in before[1].split()
    _assert_contract_reapply_fails_without_repair(conn, snapshot_sql)


@pytest.mark.parametrize(
    "object_kind,target,privilege,acl_type",
    [
        ("table", "research_evidence_project_context_revision", "SELECT", "r"),
        ("allocator", "research_evidence_project_context_sequence_allocator",
         "UPDATE", "r"),
        ("function", "research_evidence_pack_string_array_valid"
         "(jsonb,integer,integer)", "EXECUTE", "f"),
    ],
)
def test_reapply_rejects_owner_acl_loss_without_repair(
    pack_schema, object_kind, target, privilege, acl_type,
):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    catalog = "pg_proc" if object_kind == "function" else "pg_class"
    oid_cast = "regprocedure" if object_kind == "function" else "regclass"
    owner_column = "proowner" if object_kind == "function" else "relowner"
    acl_column = "proacl" if object_kind == "function" else "relacl"
    owner = conn.execute(
        f"SELECT pg_get_userbyid({owner_column}) FROM {catalog} "
        f"WHERE oid=%s::{oid_cast}", (target,),
    ).fetchone()[0]
    identifier = pg.psycopg_module().sql.Identifier(owner)
    if object_kind == "function":
        statement = pg.psycopg_module().sql.SQL(
            f"REVOKE {privilege} ON FUNCTION {target} FROM {{}}"
        ).format(identifier)
    else:
        statement = pg.psycopg_module().sql.SQL(
            f"REVOKE {privilege} ON TABLE {target} FROM {{}}"
        ).format(identifier)
    conn.execute(statement)
    conn.commit()
    snapshot_sql = f"""SELECT acl.grantee,acl.grantor,acl.privilege_type,
                               acl.is_grantable
                        FROM {catalog} object
                        CROSS JOIN LATERAL aclexplode(coalesce(
                          object.{acl_column},
                          acldefault('{acl_type}',object.{owner_column}))) acl
                        WHERE object.oid='{target}'::{oid_cast}
                        ORDER BY 1,2,3,4"""
    _assert_contract_reapply_fails_without_repair(conn, snapshot_sql)


def _insert_raw_annotation(conn, claim, related_json, *, limitations_json="[]"):
    return conn.execute(
        """INSERT INTO research_evidence_claim_annotation_revision
           (project_id,claim_draft_id,request_id,epistemic_status,
            confidence_label,decision_relevance,supports_statement,
            does_not_prove,limitations_json,related_claim_draft_ids_json,
            operator_notes,actor)
           VALUES(%s,%s,%s,'reported_fact','high','relevant','supports',
                  'limited',%s::jsonb,%s::jsonb,NULL,'operator')
           RETURNING id::text,annotation_sequence""",
        (claim["project"], claim["claim"], f"raw-{uuid4().hex}",
         limitations_json, related_json),
    ).fetchone()


@pytest.mark.parametrize(
    "column", ["project_limitations_json", "unresolved_gaps_json"]
)
@pytest.mark.parametrize(
    "input_kind,value,accepted",
    [("sql-null", None, False), ("json-null", "null", False),
     ("empty-array", "[]", True)],
)
def test_project_context_required_collections_distinguish_sql_and_json_null(
    pack_schema, column, input_kind, value, accepted,
):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    project = pg.insert_project(conn, name=f"context-{column}-{input_kind}")
    values = {
        "project_limitations_json": "[]",
        "unresolved_gaps_json": "[]",
    }
    values[column] = value
    statement = pg.psycopg_module().sql.SQL(
        """INSERT INTO research_evidence_project_context_revision
           (project_id,request_id,research_question,project_limitations_json,
            unresolved_gaps_json,actor)
           VALUES(%s,%s,'question',%s::jsonb,%s::jsonb,'operator')
           RETURNING {}"""
    ).format(pg.psycopg_module().sql.Identifier(column))
    params = (
        project, f"context-{column}-{input_kind}",
        values["project_limitations_json"], values["unresolved_gaps_json"],
    )
    before = _pack_rows_snapshot(conn)
    if accepted:
        stored = conn.execute(statement, params).fetchone()[0]
        assert stored == []
        assert conn.execute(
            """SELECT count(*),min(context_sequence),max(context_sequence)
               FROM research_evidence_project_context_revision
               WHERE project_id=%s""",
            (project,),
        ).fetchone() == (1, 1, 1)
        assert conn.execute(
            """SELECT last_sequence
               FROM research_evidence_project_context_sequence_allocator
               WHERE project_id=%s""",
            (project,),
        ).fetchone() == (1,)
    else:
        with pytest.raises(Exception):
            with conn.transaction():
                conn.execute(statement, params)
        assert _pack_rows_snapshot(conn) == before


@pytest.mark.parametrize(
    "column", ["limitations_json", "related_claim_draft_ids_json"]
)
@pytest.mark.parametrize(
    "input_kind,value,accepted",
    [("sql-null", None, False), ("json-null", "null", False),
     ("empty-array", "[]", True)],
)
def test_claim_annotation_required_collections_distinguish_sql_and_json_null(
    pack_schema, column, input_kind, value, accepted,
):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    claim, _ = seed_pair(conn, tag=f"annotation-{column}-{input_kind}")
    values = {"limitations_json": "[]", "related_claim_draft_ids_json": "[]"}
    values[column] = value
    statement = pg.psycopg_module().sql.SQL(
        """INSERT INTO research_evidence_claim_annotation_revision
           (project_id,claim_draft_id,request_id,epistemic_status,
            confidence_label,decision_relevance,supports_statement,
            does_not_prove,limitations_json,related_claim_draft_ids_json,
            operator_notes,actor)
           VALUES(%s,%s,%s,'reported_fact','high','relevant','supports',
                  'limited',%s::jsonb,%s::jsonb,NULL,'operator')
           RETURNING {}"""
    ).format(pg.psycopg_module().sql.Identifier(column))
    params = (
        claim["project"], claim["claim"], f"annotation-{column}-{input_kind}",
        values["limitations_json"], values["related_claim_draft_ids_json"],
    )
    before = _pack_rows_snapshot(conn)
    if accepted:
        stored = conn.execute(statement, params).fetchone()[0]
        assert stored == []
        assert conn.execute(
            """SELECT count(*),min(annotation_sequence),max(annotation_sequence)
               FROM research_evidence_claim_annotation_revision
               WHERE project_id=%s AND claim_draft_id=%s""",
            (claim["project"], claim["claim"]),
        ).fetchone() == (1, 1, 1)
        assert conn.execute(
            """SELECT last_sequence
               FROM research_evidence_claim_annotation_sequence_allocator
               WHERE project_id=%s AND claim_draft_id=%s""",
            (claim["project"], claim["claim"]),
        ).fetchone() == (1,)
    else:
        with pytest.raises(Exception):
            with conn.transaction():
                conn.execute(statement, params)
        assert _pack_rows_snapshot(conn) == before


def test_related_claim_ids_reject_non_string_json_members_without_side_effects(
    pack_schema,
):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    claim, _ = seed_pair(conn, tag="related-member-types")
    numeric_uuid_text = "11111111111111111111111111111111"
    conn.execute(
        """INSERT INTO research_claim_draft(id,project_id,claim_text,created_by)
           VALUES(%s::uuid,%s,'Numeric UUID target','operator')""",
        (numeric_uuid_text, claim["project"]),
    )
    conn.commit()
    cases = [
        int(numeric_uuid_text), True, {"uuid": numeric_uuid_text},
        [numeric_uuid_text], None,
    ]
    for member in cases:
        before = conn.execute(
            """SELECT
                 (SELECT count(*) FROM research_evidence_claim_annotation_revision
                   WHERE project_id=%s AND claim_draft_id=%s),
                 (SELECT count(*) FROM
                   research_evidence_claim_annotation_sequence_allocator
                   WHERE project_id=%s AND claim_draft_id=%s)""",
            (claim["project"], claim["claim"],
             claim["project"], claim["claim"]),
        ).fetchone()
        with pytest.raises(Exception):
            with conn.transaction():
                _insert_raw_annotation(conn, claim, json.dumps([member]))
        after = conn.execute(
            """SELECT
                 (SELECT count(*) FROM research_evidence_claim_annotation_revision
                   WHERE project_id=%s AND claim_draft_id=%s),
                 (SELECT count(*) FROM
                   research_evidence_claim_annotation_sequence_allocator
                   WHERE project_id=%s AND claim_draft_id=%s)""",
            (claim["project"], claim["claim"],
             claim["project"], claim["claim"]),
        ).fetchone()
        assert after == before == (0, 0)


def test_json_string_collections_store_canonical_edge_trimmed_values(pack_schema):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    edge = "".join(chr(code_point) for code_point in PYTHON_STRIP_CODE_POINTS)
    wrapped = f"{edge}Alpha{edge[::-1]}"
    internal = f"Alpha{edge}Beta"
    bom_wrapped = "\ufeffalpha\ufeff"
    raw_values = [wrapped, internal, "alpha", bom_wrapped]
    expected = ["Alpha", internal, "alpha", bom_wrapped]
    project = pg.insert_project(conn, name="canonical-string-storage")
    row = conn.execute(
        """INSERT INTO research_evidence_project_context_revision
           (project_id,request_id,research_question,project_limitations_json,
            unresolved_gaps_json,actor)
           VALUES(%s,'canonical-storage','question',%s::jsonb,%s::jsonb,'operator')
           RETURNING project_limitations_json,unresolved_gaps_json""",
        (project, json.dumps(raw_values, ensure_ascii=False),
         json.dumps(raw_values, ensure_ascii=False)),
    ).fetchone()
    claim = seed_endpoint(
        conn, project_id=project, kind="claim_draft", tag="canonical-storage-claim",
    )
    annotation_id, _ = _insert_raw_annotation(
        conn, claim, "[]", limitations_json=json.dumps(raw_values, ensure_ascii=False),
    )
    stored_limitations = conn.execute(
        "SELECT limitations_json FROM research_evidence_claim_annotation_revision "
        "WHERE id=%s", (annotation_id,),
    ).fetchone()[0]
    assert row[0] == expected
    assert row[1] == expected
    assert stored_limitations == expected
    assert internal in stored_limitations
    assert "Alpha" in stored_limitations and "alpha" in stored_limitations
    assert bom_wrapped in stored_limitations


def test_related_claim_ids_store_canonical_uuid_text_and_reject_normalized_duplicates(
    pack_schema,
):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    claim, _ = seed_pair(conn, tag="canonical-related-uuid")
    canonical = "abcdefab-cdef-abcd-efab-cdefabcdefab"
    compact_upper = canonical.replace("-", "").upper()
    conn.execute(
        """INSERT INTO research_claim_draft(id,project_id,claim_text,created_by)
           VALUES(%s::uuid,%s,'Canonical UUID target','operator')""",
        (canonical, claim["project"]),
    )
    annotation_id, _ = _insert_raw_annotation(
        conn, claim, json.dumps([compact_upper]),
    )
    stored = conn.execute(
        "SELECT related_claim_draft_ids_json "
        "FROM research_evidence_claim_annotation_revision WHERE id=%s",
        (annotation_id,),
    ).fetchone()[0]
    assert stored == [canonical]
    duplicate_claim = seed_endpoint(
        conn, project_id=claim["project"], kind="claim_draft",
        tag="canonical-related-uuid-duplicate-base",
    )
    with pytest.raises(Exception):
        with conn.transaction():
            _insert_raw_annotation(
                conn, duplicate_claim,
                json.dumps([compact_upper, canonical]),
            )


def _insert_context_chain(conn, project, count=3):
    rows = []
    for sequence in range(1, count + 1):
        rows.append(conn.execute(
            """INSERT INTO research_evidence_project_context_revision
               (project_id,request_id,research_question,project_limitations_json,
                unresolved_gaps_json,actor)
               VALUES(%s,%s,'question','[]','[]','operator')
               RETURNING id::text,context_sequence""",
            (project, f"context-{uuid4().hex}-{sequence}"),
        ).fetchone())
    return rows


@pytest.mark.parametrize(
    "corruption",
    ["missing_first", "internal_gap", "above_allocator", "allocator_above",
     "allocator_below", "history_without_allocator", "orphan_allocator",
     "skipped_predecessor", "cycle", "wrong_scope"],
)
def test_project_context_history_corruption_matrix(pack_schema, corruption):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    project = pg.insert_project(conn, name=f"history-{corruption}")
    rows = _insert_context_chain(conn, project)
    conn.execute("ALTER TABLE research_evidence_project_context_revision "
                 "DISABLE TRIGGER trg_repcr_no_mutation")
    if corruption == "missing_first":
        conn.execute("UPDATE research_evidence_project_context_revision SET "
                     "supersedes_context_revision_id=NULL WHERE project_id=%s "
                     "AND context_sequence=2", (project,))
        conn.execute("DELETE FROM research_evidence_project_context_revision "
                     "WHERE project_id=%s AND context_sequence=1", (project,))
    elif corruption == "internal_gap":
        conn.execute("UPDATE research_evidence_project_context_revision SET "
                     "supersedes_context_revision_id=NULL WHERE project_id=%s "
                     "AND context_sequence=2", (project,))
        conn.execute("UPDATE research_evidence_project_context_revision SET "
                     "supersedes_context_revision_id=%s WHERE project_id=%s "
                     "AND context_sequence=3", (rows[0][0], project))
        conn.execute("DELETE FROM research_evidence_project_context_revision "
                     "WHERE project_id=%s AND context_sequence=2", (project,))
    elif corruption in ("above_allocator", "allocator_below"):
        conn.execute("UPDATE research_evidence_project_context_sequence_allocator "
                     "SET last_sequence=2 WHERE project_id=%s", (project,))
    elif corruption == "allocator_above":
        conn.execute("UPDATE research_evidence_project_context_sequence_allocator "
                     "SET last_sequence=4 WHERE project_id=%s", (project,))
    elif corruption == "history_without_allocator":
        conn.execute("DELETE FROM research_evidence_project_context_sequence_allocator "
                     "WHERE project_id=%s", (project,))
    elif corruption == "orphan_allocator":
        orphan = pg.insert_project(conn, name="orphan-allocator")
        conn.execute("INSERT INTO research_evidence_project_context_sequence_allocator "
                     "VALUES(%s,0)", (orphan,))
    elif corruption == "skipped_predecessor":
        conn.execute("UPDATE research_evidence_project_context_revision SET "
                     "supersedes_context_revision_id=NULL WHERE project_id=%s "
                     "AND context_sequence=2", (project,))
        conn.execute("UPDATE research_evidence_project_context_revision SET "
                     "supersedes_context_revision_id=%s WHERE project_id=%s "
                     "AND context_sequence=3", (rows[0][0], project))
    elif corruption == "cycle":
        conn.execute("UPDATE research_evidence_project_context_revision SET "
                     "supersedes_context_revision_id=NULL WHERE project_id=%s "
                     "AND context_sequence=3", (project,))
        conn.execute("UPDATE research_evidence_project_context_revision SET "
                     "supersedes_context_revision_id=%s WHERE project_id=%s "
                     "AND context_sequence=1", (rows[1][0], project))
    elif corruption == "wrong_scope":
        other_project = pg.insert_project(conn, name="wrong-scope-predecessor")
        other_row = _insert_context_chain(conn, other_project, count=1)[0]
        conn.execute("SET session_replication_role=replica")
        conn.execute("UPDATE research_evidence_project_context_revision SET "
                     "supersedes_context_revision_id=%s WHERE project_id=%s "
                     "AND context_sequence=3", (other_row[0], project))
        conn.execute("SET session_replication_role=origin")
    conn.execute("ALTER TABLE research_evidence_project_context_revision "
                 "ENABLE ALWAYS TRIGGER trg_repcr_no_mutation")
    conn.commit()
    _assert_contract_reapply_fails_without_repair(
        conn,
        """SELECT id::text,project_id::text,context_sequence,
                  supersedes_context_revision_id::text
           FROM research_evidence_project_context_revision
           UNION ALL
           SELECT NULL,project_id::text,last_sequence,NULL
           FROM research_evidence_project_context_sequence_allocator
           ORDER BY 2,3,1 NULLS FIRST""",
    )


def _seed_authorization_scope(conn, tag, decisions=("authorized", "revoked", "authorized")):
    claim, evidence = seed_pair(conn, tag=tag)
    for item, suffix in ((claim["item"], "claim"), (evidence["item"], "evidence")):
        conn.execute(
            """INSERT INTO research_evidence_intake_item_review_decision
               (project_id,research_evidence_intake_item_id,decision_type,
                decision_reason,decided_by,request_id)
               VALUES(%s,%s,'approved','Reviewed','operator',%s)""",
            (claim["project"], item, f"{tag}-review-{suffix}"),
        )
    insert_support(conn, claim, evidence, request_id=f"{tag}-support")
    conn.execute(
        """INSERT INTO research_evidence_claim_annotation_revision
           (project_id,claim_draft_id,request_id,epistemic_status,
            confidence_label,decision_relevance,supports_statement,
            does_not_prove,limitations_json,related_claim_draft_ids_json,
            operator_notes,actor)
           VALUES(%s,%s,%s,'reported_fact','high','relevant','supports',
                  'limited','[]','[]',NULL,'operator')""",
        (claim["project"], claim["claim"], f"{tag}-annotation-1"),
    )
    for index, decision in enumerate(decisions, 1):
        conn.execute(
            """INSERT INTO research_evidence_usage_authorization_decision
               (project_id,claim_intake_item_id,evidence_intake_item_id,
                usage_scope,decision,reason,actor,request_id)
               VALUES(%s,%s,%s,'client_report',%s,'reason','operator',%s)""",
            (claim["project"], claim["item"], evidence["item"], decision,
             f"{tag}-authorization-{index}"),
        )
    return claim, evidence


def test_claim_annotation_history_rejects_skipped_predecessor(pack_schema):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    claim, _ = seed_pair(conn, tag="annotation-history")
    ids = []
    for sequence in range(1, 4):
        ids.append(conn.execute(
            """INSERT INTO research_evidence_claim_annotation_revision
               (project_id,claim_draft_id,request_id,epistemic_status,
                confidence_label,decision_relevance,supports_statement,
                does_not_prove,limitations_json,related_claim_draft_ids_json,
                operator_notes,actor)
               VALUES(%s,%s,%s,'inference','medium','relevant','supports',
                      'limited','[]','[]',NULL,'operator') RETURNING id::text""",
            (claim["project"], claim["claim"], f"annotation-history-{sequence}"),
        ).fetchone()[0])
    conn.execute("ALTER TABLE research_evidence_claim_annotation_revision "
                 "DISABLE TRIGGER trg_recar_no_mutation")
    conn.execute("UPDATE research_evidence_claim_annotation_revision SET "
                 "supersedes_annotation_revision_id=NULL WHERE id=%s", (ids[1],))
    conn.execute("UPDATE research_evidence_claim_annotation_revision SET "
                 "supersedes_annotation_revision_id=%s WHERE id=%s",
                 (ids[0], ids[2]))
    conn.execute("ALTER TABLE research_evidence_claim_annotation_revision "
                 "ENABLE ALWAYS TRIGGER trg_recar_no_mutation")
    conn.commit()
    _assert_contract_reapply_fails_without_repair(
        conn,
        """SELECT id::text,annotation_sequence,
                  supersedes_annotation_revision_id::text
           FROM research_evidence_claim_annotation_revision ORDER BY 2""",
    )


@pytest.mark.parametrize(
    "corruption",
    ["invalid_first", "repeated_transition", "invalid_predecessor",
     "allocator_above", "allocator_below", "history_without_allocator"],
)
def test_authorization_history_corruption_matrix(pack_schema, corruption):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    claim, evidence = _seed_authorization_scope(conn, f"auth-{corruption}")
    scope = (claim["project"], claim["item"], evidence["item"], "client_report")
    conn.execute("ALTER TABLE research_evidence_usage_authorization_decision "
                 "DISABLE TRIGGER trg_reuad_no_mutation")
    if corruption == "invalid_first":
        conn.execute("UPDATE research_evidence_usage_authorization_decision SET "
                     "decision='revoked' WHERE project_id=%s AND "
                     "claim_intake_item_id=%s AND evidence_intake_item_id=%s AND "
                     "usage_scope=%s AND decision_sequence=1", scope)
    elif corruption == "repeated_transition":
        conn.execute("UPDATE research_evidence_usage_authorization_decision SET "
                     "decision='revoked' WHERE project_id=%s AND "
                     "claim_intake_item_id=%s AND evidence_intake_item_id=%s AND "
                     "usage_scope=%s AND decision_sequence=3", scope)
    elif corruption == "invalid_predecessor":
        conn.execute("UPDATE research_evidence_usage_authorization_decision SET "
                     "supersedes_decision_id=NULL WHERE project_id=%s AND "
                     "claim_intake_item_id=%s AND evidence_intake_item_id=%s AND "
                     "usage_scope=%s AND decision_sequence=3", scope)
    elif corruption == "allocator_above":
        conn.execute("UPDATE research_evidence_usage_authorization_sequence_allocator "
                     "SET last_sequence=4 WHERE project_id=%s AND "
                     "claim_intake_item_id=%s AND evidence_intake_item_id=%s AND "
                     "usage_scope=%s", scope)
    elif corruption == "allocator_below":
        conn.execute("UPDATE research_evidence_usage_authorization_sequence_allocator "
                     "SET last_sequence=2 WHERE project_id=%s AND "
                     "claim_intake_item_id=%s AND evidence_intake_item_id=%s AND "
                     "usage_scope=%s", scope)
    elif corruption == "history_without_allocator":
        conn.execute("DELETE FROM research_evidence_usage_authorization_sequence_allocator "
                     "WHERE project_id=%s AND claim_intake_item_id=%s AND "
                     "evidence_intake_item_id=%s AND usage_scope=%s", scope)
    conn.execute("ALTER TABLE research_evidence_usage_authorization_decision "
                 "ENABLE ALWAYS TRIGGER trg_reuad_no_mutation")
    conn.commit()
    _assert_contract_reapply_fails_without_repair(
        conn,
        """SELECT id::text,decision_sequence,decision,supersedes_decision_id::text
           FROM research_evidence_usage_authorization_decision
           UNION ALL
           SELECT NULL,last_sequence,NULL,NULL
           FROM research_evidence_usage_authorization_sequence_allocator
           ORDER BY 2,1 NULLS FIRST""",
    )


def _logical_pack_snapshot(conn):
    snapshots = {}
    orderings = {
        "research_evidence_project_context_revision": "project_id,context_sequence",
        "research_evidence_project_context_sequence_allocator": "project_id",
        "research_evidence_claim_annotation_revision":
            "project_id,claim_draft_id,annotation_sequence",
        "research_evidence_claim_annotation_sequence_allocator":
            "project_id,claim_draft_id",
        "research_evidence_usage_authorization_decision":
            "project_id,claim_intake_item_id,evidence_intake_item_id,usage_scope,decision_sequence",
        "research_evidence_usage_authorization_sequence_allocator":
            "project_id,claim_intake_item_id,evidence_intake_item_id,usage_scope",
    }
    for table, ordering in orderings.items():
        snapshots[table] = conn.execute(
            f"SELECT to_jsonb(row_value)::text FROM {table} row_value ORDER BY {ordering}"
        ).fetchall()
    snapshots["catalog"] = conn.execute(
        """SELECT 'relation',c.relname,c.oid::text,c.relfilenode::text
           FROM pg_class c WHERE c.relnamespace=current_schema()::regnamespace
             AND c.relname=ANY(%s)
           UNION ALL
           SELECT 'function',p.proname,p.oid::text,md5(p.prosrc)
           FROM pg_proc p WHERE p.pronamespace=current_schema()::regnamespace
             AND p.proname=ANY(%s)
           UNION ALL
           SELECT 'trigger',t.tgname,t.oid::text,t.tgfoid::text
           FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
           WHERE c.relnamespace=current_schema()::regnamespace
             AND NOT t.tgisinternal AND t.tgname=ANY(ARRAY[
               'trg_repcr_prepare_insert','trg_repcr_no_mutation',
               'trg_recar_prepare_insert','trg_recar_no_mutation',
               'trg_reuad_prepare_insert','trg_reuad_no_mutation'])
           ORDER BY 1,2""",
        (list(PACK_TABLES), [
            "research_evidence_pack_string_array_valid",
            "research_evidence_prepare_project_context_insert",
            "research_evidence_prepare_claim_annotation_insert",
            "research_evidence_prepare_usage_authorization_insert",
        ]),
    ).fetchall()
    return snapshots


def test_valid_populated_histories_are_exact_noop_on_reapply(pack_schema):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    for name in ("populated-context-a", "populated-context-b"):
        project = pg.insert_project(conn, name=name)
        _insert_context_chain(conn, project, count=3)
    claim, evidence = _seed_authorization_scope(conn, "populated-auth")
    conn.execute(
        """INSERT INTO research_evidence_claim_annotation_revision
           (project_id,claim_draft_id,request_id,epistemic_status,
            confidence_label,decision_relevance,supports_statement,
            does_not_prove,limitations_json,related_claim_draft_ids_json,
            operator_notes,actor)
           VALUES(%s,%s,'populated-annotation-2','observation','medium',
                  'relevant','supports','limited','[" edge "]','[]',NULL,'operator')""",
        (claim["project"], claim["claim"]),
    )
    conn.execute(
        """INSERT INTO research_evidence_usage_authorization_decision
           (project_id,claim_intake_item_id,evidence_intake_item_id,usage_scope,
            decision,reason,actor,request_id)
           VALUES(%s,%s,%s,'internal_analysis','authorized','reason','operator',
                  'populated-other-scope')""",
        (claim["project"], claim["item"], evidence["item"]),
    )
    conn.commit()
    before = _logical_pack_snapshot(conn)
    pg.apply_v61_research_evidence_pack(conn)
    assert _logical_pack_snapshot(conn) == before


def test_complete_predecessor_topology_clean_apply_and_noop_reapply(
    full_topology_schema
):
    conn, schema = full_topology_schema
    roles_before = pg.external_role_snapshot(conn)
    role_oids_before = pg.cluster_role_oids(conn)
    database = conn.execute("SELECT current_database()").fetchone()[0]
    assert not conn.execute(
        "SELECT has_database_privilege(%s,%s,'CREATE')",
        ("workflow_migration_owner", database),
    ).fetchone()[0]
    assert conn.execute(
        """SELECT
             has_schema_privilege('workflow_research_evidence_owner',
                                  'research_evidence_automation_roi','USAGE'),
             has_schema_privilege('workflow_research_evidence_owner',
                                  'research_evidence_automation_roi','CREATE'),
             has_schema_privilege('workflow_automation_roi_runtime',
                                  'research_evidence_automation_roi','USAGE'),
             has_schema_privilege('workflow_automation_roi_runtime',
                                  'research_evidence_automation_roi','CREATE'),
             has_schema_privilege('workflow_migration_owner',
                                  'research_evidence_automation_roi','USAGE'),
             has_schema_privilege('workflow_migration_owner',
                                  'research_evidence_automation_roi','CREATE')"""
    ).fetchone() == (True, True, True, False, False, False)
    assert pg.external_role_memberships(conn) == pg.EXTERNAL_MEMBERSHIP_MANIFEST
    with pg.runtime_connection(schema) as runtime:
        assert runtime.execute(
            "SELECT session_user,current_user"
        ).fetchone() == (
            "workflow_automation_roi_runtime",
            "workflow_automation_roi_runtime",
        )
    assert conn.execute(
        "SELECT to_regclass(%s) IS NOT NULL",
        (f'"{schema}".research_evidence_consumer_input_binding',),
    ).fetchone()[0]
    assert conn.execute(
        "SELECT to_regclass('research_evidence_automation_roi.automation_roi_calculation_result') "
        "IS NOT NULL"
    ).fetchone()[0]
    pg.apply_v61_research_evidence_pack(conn)
    assert pg.external_role_snapshot(conn) == roles_before
    assert pg.cluster_role_oids(conn) == role_oids_before
    before = _logical_pack_snapshot(conn)
    pg.apply_v61_research_evidence_pack(conn)
    after = _logical_pack_snapshot(conn)
    assert after == before
    assert pg.external_role_snapshot(conn) == roles_before
    assert pg.cluster_role_oids(conn) == role_oids_before
    assert len(before["catalog"]) == 16


def test_clean_apply_and_exact_noop_reapply(pack_schema):
    conn, schema = pack_schema
    prerequisite_name_collisions = conn.execute(
        """SELECT table_name, column_name, data_type, udt_name
           FROM information_schema.columns
           WHERE table_schema=%s
             AND ((column_name='id' AND data_type<>'uuid')
                  OR (column_name='project_id' AND data_type<>'uuid'))
           ORDER BY table_name, ordinal_position""",
        (schema,),
    ).fetchall()
    assert prerequisite_name_collisions == [
        ("calibration_metrics", "id", "integer", "int4"),
        ("framework_effectiveness", "id", "integer", "int4"),
        ("project_patterns", "id", "integer", "int4"),
        ("workflow_jobs", "project_id", "text", "text"),
        ("workflow_runs", "project_id", "text", "text"),
    ]
    pg.apply_v61_research_evidence_pack(conn)
    columns = conn.execute(
        """SELECT ordinal_position, column_name, udt_name, is_nullable, column_default
           FROM information_schema.columns
           WHERE table_schema=%s
             AND table_name='research_evidence_usage_authorization_decision'
           ORDER BY ordinal_position""",
        (schema,),
    ).fetchall()
    assert columns == USAGE_AUTHORIZATION_COLUMNS
    before = conn.execute(
        """SELECT c.relname, c.relfilenode FROM pg_class c JOIN pg_namespace n
           ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relname=ANY(%s)
           ORDER BY c.relname""", (schema, list(PACK_TABLES)),
    ).fetchall()
    pg.apply_v61_research_evidence_pack(conn)
    after = conn.execute(
        """SELECT c.relname, c.relfilenode FROM pg_class c JOIN pg_namespace n
           ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relname=ANY(%s)
           ORDER BY c.relname""", (schema, list(PACK_TABLES)),
    ).fetchall()
    assert len(before) == 6
    assert after == before


def test_partial_state_fails_closed(pack_schema):
    conn, _ = pack_schema
    conn.execute("CREATE TABLE research_evidence_project_context_revision (id uuid)")
    conn.commit()
    with pytest.raises(Exception, match="partial/divergent"):
        pg.apply_v61_research_evidence_pack(conn)


def test_catalog_drift_fails_closed(pack_schema):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    conn.execute("ALTER TABLE research_evidence_project_context_revision DISABLE TRIGGER trg_repcr_no_mutation")
    conn.commit()
    with pytest.raises(Exception, match="contract violation"):
        pg.apply_v61_research_evidence_pack(conn)


def test_ledgers_are_append_only_with_always_triggers(pack_schema):
    conn, schema = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    rows = conn.execute(
        """SELECT t.tgname, t.tgenabled, t.tgtype FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
           JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s
           AND t.tgname=ANY(%s) ORDER BY t.tgname""",
        (schema, [
            "trg_repcr_prepare_insert", "trg_repcr_no_mutation",
            "trg_recar_prepare_insert", "trg_recar_no_mutation",
            "trg_reuad_prepare_insert", "trg_reuad_no_mutation",
        ]),
    ).fetchall()
    assert rows == [
        ("trg_recar_no_mutation", "A", 27),
        ("trg_recar_prepare_insert", "A", 7),
        ("trg_repcr_no_mutation", "A", 27),
        ("trg_repcr_prepare_insert", "A", 7),
        ("trg_reuad_no_mutation", "A", 27),
        ("trg_reuad_prepare_insert", "A", 7),
    ]


def test_context_sequence_predecessor_idempotency_and_mutation_guard(pack_schema):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    project = pg.insert_project(conn, name="pack-context")
    first = conn.execute(
        """INSERT INTO research_evidence_project_context_revision
           (project_id,request_id,research_question,project_limitations_json,
            unresolved_gaps_json,actor) VALUES(%s,'r1','Question?','[]','[]','operator')
           RETURNING id::text,context_sequence,supersedes_context_revision_id::text""",
        (project,),
    ).fetchone()
    second = conn.execute(
        """INSERT INTO research_evidence_project_context_revision
           (project_id,request_id,research_question,project_limitations_json,
            unresolved_gaps_json,actor) VALUES(%s,'r2','Question 2?','[]','[]','operator')
           RETURNING context_sequence,supersedes_context_revision_id::text""",
        (project,),
    ).fetchone()
    assert first[1:] == (1, None)
    assert second == (2, first[0])
    conn.execute("SAVEPOINT mutation_test")
    with pytest.raises(Exception):
        conn.execute("UPDATE research_evidence_project_context_revision SET actor='other' WHERE id=%s", (first[0],))
    conn.execute("ROLLBACK TO SAVEPOINT mutation_test")
    conn.execute("RELEASE SAVEPOINT mutation_test")


def test_authorization_fails_closed_after_new_annotation(pack_schema):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    claim, evidence = seed_pair(conn, tag="pack-auth")
    for item, request in ((claim["item"], "claim-review"), (evidence["item"], "evidence-review")):
        conn.execute(
            """INSERT INTO research_evidence_intake_item_review_decision
               (project_id,research_evidence_intake_item_id,decision_type,
                decision_reason,decided_by,request_id)
               VALUES(%s,%s,'approved','Reviewed','operator',%s)""",
            (claim["project"], item, request),
        )
    insert_support(conn, claim, evidence, request_id="support-1")
    annotation_sql = """INSERT INTO research_evidence_claim_annotation_revision
      (project_id,claim_draft_id,request_id,epistemic_status,confidence_label,
       decision_relevance,supports_statement,does_not_prove,limitations_json,
       related_claim_draft_ids_json,operator_notes,actor)
      VALUES(%s,%s,%s,'reported_fact','high','Decision relevant','Supports claim',
       'Does not prove causality','[]','[]',NULL,'operator')"""
    conn.execute(annotation_sql, (claim["project"], claim["claim"], "annotation-1"))
    conn.execute(
        """INSERT INTO research_evidence_usage_authorization_decision
           (project_id,claim_intake_item_id,evidence_intake_item_id,usage_scope,
            decision,reason,actor,request_id)
           VALUES(%s,%s,%s,'client_report','authorized','Explicitly authorized','operator','auth-1')""",
        (claim["project"], claim["item"], evidence["item"]),
    )
    assert pack_service.claim_evidence_usage_is_authorized(
        conn, project_id=claim["project"], claim_intake_item_id=claim["item"],
        evidence_intake_item_id=evidence["item"], usage_scope="client_report",
    )
    assert pack_service.repo.effective_project_pack_member_counts(
        conn, project_id=claim["project"],
    ) == (1, 1)
    assert pack_service.assemble_research_evidence_pack(
        conn, project_id=claim["project"], usage_scope="client_report",
    ).counts.relationship_count == 1
    conn.execute(annotation_sql, (claim["project"], claim["claim"], "annotation-2"))
    assert not pack_service.claim_evidence_usage_is_authorized(
        conn, project_id=claim["project"], claim_intake_item_id=claim["item"],
        evidence_intake_item_id=evidence["item"], usage_scope="client_report",
    )
    assert pack_service.repo.effective_project_pack_member_counts(
        conn, project_id=claim["project"],
    ) == (0, 0)
    assert pack_service.assemble_research_evidence_pack(
        conn, project_id=claim["project"], usage_scope="client_report",
    ).counts.relationship_count == 0


def _prepare_service_authorization(conn, tag, *, usage_scope="client_report"):
    claim, evidence = seed_pair(conn, tag=tag)
    for item, suffix in ((claim["item"], "claim"), (evidence["item"], "evidence")):
        conn.execute(
            """INSERT INTO research_evidence_intake_item_review_decision
               (project_id,research_evidence_intake_item_id,decision_type,
                decision_reason,decided_by,request_id)
               VALUES(%s,%s,'approved','Reviewed','operator',%s)""",
            (claim["project"], item, f"{tag}-review-{suffix}"),
        )
    insert_support(conn, claim, evidence, request_id=f"{tag}-support")
    conn.execute(
        """INSERT INTO research_evidence_claim_annotation_revision
           (project_id,claim_draft_id,request_id,epistemic_status,
            confidence_label,decision_relevance,supports_statement,
            does_not_prove,limitations_json,related_claim_draft_ids_json,
            operator_notes,actor)
           VALUES(%s,%s,%s,'reported_fact','high','relevant','supports',
                  'limited','[]','[]',NULL,'operator')""",
        (claim["project"], claim["claim"], f"{tag}-annotation"),
    )
    value = ResearchEvidenceUsageAuthorizationDecisionCreate(
        project_id=claim["project"], claim_intake_item_id=claim["item"],
        evidence_intake_item_id=evidence["item"], usage_scope=usage_scope,
        decision="authorized", reason="approved", actor="operator",
        request_id=f"{tag}-authorization",
    )
    record = pack_service.record_usage_authorization_decision(conn, value)
    return claim, evidence, value, record


def _wrap_existing_fact(conn, evidence, *, fact_metadata_id, tag):
    intake_id = conn.execute(
        """INSERT INTO research_evidence_intake
           (project_id,source_snapshot_id,source_metadata_revision_id,
            selection_reason,created_by)
           VALUES(%s,%s,%s,%s,'operator') RETURNING id::text""",
        (
            evidence["project"], evidence["snapshot"],
            evidence["source_metadata"], f"duplicate wrapper {tag}",
        ),
    ).fetchone()[0]
    item_id = conn.execute(
        """INSERT INTO research_evidence_intake_item
           (project_id,research_evidence_intake_id,source_snapshot_id,item_kind,
            candidate_fact_revision_id,fact_metadata_revision_id,created_by)
           VALUES(%s,%s,%s,'candidate_fact',%s,%s,'operator')
           RETURNING id::text""",
        (
            evidence["project"], intake_id, evidence["snapshot"],
            evidence["fact"], fact_metadata_id,
        ),
    ).fetchone()[0]
    wrapped = dict(evidence)
    wrapped.update({
        "intake": intake_id, "item": item_id,
        "fact_metadata": fact_metadata_id,
    })
    return wrapped


def _authorize_duplicate_wrapper(conn, claim, evidence, *, tag):
    _approve_item(
        conn, claim["project"], evidence["item"], f"{tag}-review",
    )
    insert_support(conn, claim, evidence, request_id=f"{tag}-support")
    return pack_service.record_usage_authorization_decision(
        conn,
        ResearchEvidenceUsageAuthorizationDecisionCreate(
            project_id=claim["project"],
            claim_intake_item_id=claim["item"],
            evidence_intake_item_id=evidence["item"],
            usage_scope="client_report", decision="authorized",
            reason="duplicate wrapper authorized", actor="operator",
            request_id=f"{tag}-authorization",
        ),
    )


def _revoke_wrapper(conn, claim, evidence, *, tag):
    return pack_service.record_usage_authorization_decision(
        conn,
        ResearchEvidenceUsageAuthorizationDecisionCreate(
            project_id=claim["project"],
            claim_intake_item_id=claim["item"],
            evidence_intake_item_id=evidence["item"],
            usage_scope="client_report", decision="revoked",
            reason="duplicate wrapper revoked", actor="operator",
            request_id=f"{tag}-revocation",
        ),
    )


def _bulk_authorize_duplicate_wrappers(
    conn, claim, evidence, *, start_ordinal, count,
):
    """Create valid current wrapper heads set-wise in disposable PostgreSQL."""
    stop_ordinal = start_ordinal + count - 1
    conn.execute(
        """CREATE TEMP TABLE IF NOT EXISTS r2a2_candidate_map (
             ordinal integer PRIMARY KEY,
             intake_id uuid NOT NULL,
             item_id uuid NOT NULL,
             review_id uuid NOT NULL,
             support_id uuid NOT NULL,
             authorization_id uuid NOT NULL
           )"""
    )
    conn.execute(
        """INSERT INTO r2a2_candidate_map
           SELECT ordinal,gen_random_uuid(),gen_random_uuid(),gen_random_uuid(),
                  gen_random_uuid(),gen_random_uuid()
           FROM generate_series(%s::integer,%s::integer) ordinal""",
        (start_ordinal, stop_ordinal),
    )
    conn.execute(
        """INSERT INTO research_evidence_intake
           (id,project_id,source_snapshot_id,source_metadata_revision_id,
            selection_reason,created_by)
           SELECT intake_id,%s,%s,%s,
                  'bounded duplicate wrapper '||ordinal,'operator'
           FROM r2a2_candidate_map WHERE ordinal BETWEEN %s AND %s""",
        (
            claim["project"], evidence["snapshot"],
            evidence["source_metadata"], start_ordinal, stop_ordinal,
        ),
    )
    conn.execute(
        """INSERT INTO research_evidence_intake_item
           (id,project_id,research_evidence_intake_id,source_snapshot_id,
            item_kind,candidate_fact_revision_id,fact_metadata_revision_id,
            created_by)
           SELECT item_id,%s,intake_id,%s,'candidate_fact',%s,%s,'operator'
           FROM r2a2_candidate_map WHERE ordinal BETWEEN %s AND %s""",
        (
            claim["project"], evidence["snapshot"], evidence["fact"],
            evidence["fact_metadata"], start_ordinal, stop_ordinal,
        ),
    )

    conn.execute(
        "ALTER TABLE research_evidence_intake_item_review_decision "
        "DISABLE TRIGGER trg_reird_prepare_insert"
    )
    conn.execute(
        """INSERT INTO research_evidence_intake_item_review_decision
           (id,project_id,research_evidence_intake_item_id,decision_type,
            decision_sequence,supersedes_decision_id,decision_reason,
            decided_by,request_id,recorded_at)
           SELECT review_id,%s,item_id,'approved',1,NULL,'Reviewed','operator',
                  'bounded-review-'||ordinal,clock_timestamp()
           FROM r2a2_candidate_map WHERE ordinal BETWEEN %s AND %s""",
        (claim["project"], start_ordinal, stop_ordinal),
    )
    conn.execute(
        """INSERT INTO research_evidence_item_review_sequence_allocator
           (project_id,research_evidence_intake_item_id,last_sequence)
           SELECT %s,item_id,1 FROM r2a2_candidate_map
           WHERE ordinal BETWEEN %s AND %s""",
        (claim["project"], start_ordinal, stop_ordinal),
    )
    conn.execute(
        "ALTER TABLE research_evidence_intake_item_review_decision "
        "ENABLE ALWAYS TRIGGER trg_reird_prepare_insert"
    )

    conn.execute(
        "ALTER TABLE research_evidence_claim_support_assessment "
        "DISABLE TRIGGER trg_recsa_prepare_insert"
    )
    conn.execute(
        """INSERT INTO research_evidence_claim_support_assessment
           (id,project_id,claim_intake_item_id,evidence_intake_item_id,
            request_id,locator_resolution,locator_rationale,evidence_linkage,
            evidence_linkage_rationale,semantic_relationship,
            semantic_relationship_rationale,assessed_by,assessment_sequence,
            supersedes_assessment_id,claim_draft_id,claim_source_snapshot_id,
            claim_source_blob_id,claim_source_metadata_revision_id,
            evidence_source_snapshot_id,evidence_source_blob_id,
            evidence_source_metadata_revision_id,candidate_fact_revision_id,
            fact_metadata_revision_id,assessed_at)
           SELECT support_id,%s,%s,item_id,'bounded-support-'||ordinal,
                  'resolvable','Stored locator was reviewed.','linked',
                  'The evidence item is the intended link.','support',
                  'Operator assessed supporting context.','operator',1,NULL,
                  %s,%s,%s,%s,%s,%s,%s,%s,%s,clock_timestamp()
           FROM r2a2_candidate_map WHERE ordinal BETWEEN %s AND %s""",
        (
            claim["project"], claim["item"], claim["claim"],
            claim["snapshot"], claim["blob"], claim["source_metadata"],
            evidence["snapshot"], evidence["blob"],
            evidence["source_metadata"], evidence["fact"],
            evidence["fact_metadata"], start_ordinal, stop_ordinal,
        ),
    )
    conn.execute(
        """INSERT INTO research_evidence_claim_support_sequence_allocator
           (project_id,claim_intake_item_id,evidence_intake_item_id,last_sequence)
           SELECT %s,%s,item_id,1 FROM r2a2_candidate_map
           WHERE ordinal BETWEEN %s AND %s""",
        (claim["project"], claim["item"], start_ordinal, stop_ordinal),
    )
    conn.execute(
        "ALTER TABLE research_evidence_claim_support_assessment "
        "ENABLE ALWAYS TRIGGER trg_recsa_prepare_insert"
    )

    claim_review_id = conn.execute(
        """SELECT id::text FROM research_evidence_intake_item_review_decision
           WHERE project_id=%s AND research_evidence_intake_item_id=%s
           ORDER BY decision_sequence DESC LIMIT 1""",
        (claim["project"], claim["item"]),
    ).fetchone()[0]
    annotation_id = conn.execute(
        """SELECT id::text FROM research_evidence_claim_annotation_revision
           WHERE project_id=%s AND claim_draft_id=%s
           ORDER BY annotation_sequence DESC LIMIT 1""",
        (claim["project"], claim["claim"]),
    ).fetchone()[0]
    conn.execute(
        "ALTER TABLE research_evidence_usage_authorization_decision "
        "DISABLE TRIGGER trg_reuad_prepare_insert"
    )
    conn.execute(
        """INSERT INTO research_evidence_usage_authorization_decision
           (id,project_id,claim_intake_item_id,evidence_intake_item_id,
            claim_support_assessment_id,usage_scope,decision,reason,actor,
            request_id,claim_draft_id,claim_annotation_revision_id,
            claim_review_decision_id,evidence_review_decision_id,
            decision_sequence,supersedes_decision_id,recorded_at)
           SELECT authorization_id,%s,%s,item_id,support_id,'client_report',
                  'authorized','bounded duplicate authorized','operator',
                  'bounded-authorization-'||ordinal,%s,%s,%s,review_id,
                  1,NULL,clock_timestamp()
           FROM r2a2_candidate_map WHERE ordinal BETWEEN %s AND %s""",
        (
            claim["project"], claim["item"], claim["claim"], annotation_id,
            claim_review_id, start_ordinal, stop_ordinal,
        ),
    )
    conn.execute(
        """INSERT INTO research_evidence_usage_authorization_sequence_allocator
           (project_id,claim_intake_item_id,evidence_intake_item_id,usage_scope,
            last_sequence)
           SELECT %s,%s,item_id,'client_report',1 FROM r2a2_candidate_map
           WHERE ordinal BETWEEN %s AND %s""",
        (claim["project"], claim["item"], start_ordinal, stop_ordinal),
    )
    conn.execute(
        "ALTER TABLE research_evidence_usage_authorization_decision "
        "ENABLE ALWAYS TRIGGER trg_reuad_prepare_insert"
    )


def _assert_bulk_candidate_topology(conn, *, project_id, expected_count):
    assert conn.execute(
        """SELECT t.tgname,t.tgenabled
           FROM pg_trigger t
           WHERE t.tgname=ANY(ARRAY[
             'trg_reird_prepare_insert','trg_recsa_prepare_insert',
             'trg_reuad_prepare_insert'])
           ORDER BY t.tgname"""
    ).fetchall() == [
        ("trg_recsa_prepare_insert", "A"),
        ("trg_reird_prepare_insert", "A"),
        ("trg_reuad_prepare_insert", "A"),
    ]
    assert conn.execute(
        """SELECT count(*),count(DISTINCT evidence.candidate_fact_revision_id)
           FROM research_evidence_usage_authorization_decision decision
           JOIN research_evidence_usage_authorization_sequence_allocator auth_head
             ON auth_head.project_id=decision.project_id
            AND auth_head.claim_intake_item_id=decision.claim_intake_item_id
            AND auth_head.evidence_intake_item_id=decision.evidence_intake_item_id
            AND auth_head.usage_scope=decision.usage_scope
            AND auth_head.last_sequence=decision.decision_sequence
           JOIN research_evidence_claim_support_assessment support
             ON support.id=decision.claim_support_assessment_id
            AND support.project_id=decision.project_id
            AND support.claim_intake_item_id=decision.claim_intake_item_id
            AND support.evidence_intake_item_id=decision.evidence_intake_item_id
           JOIN research_evidence_claim_support_sequence_allocator support_head
             ON support_head.project_id=support.project_id
            AND support_head.claim_intake_item_id=support.claim_intake_item_id
            AND support_head.evidence_intake_item_id=support.evidence_intake_item_id
            AND support_head.last_sequence=support.assessment_sequence
           JOIN research_evidence_intake_item_review_decision claim_review
             ON claim_review.id=decision.claim_review_decision_id
            AND claim_review.project_id=decision.project_id
            AND claim_review.research_evidence_intake_item_id=
                decision.claim_intake_item_id
           JOIN research_evidence_item_review_sequence_allocator claim_review_head
             ON claim_review_head.project_id=claim_review.project_id
            AND claim_review_head.research_evidence_intake_item_id=
                claim_review.research_evidence_intake_item_id
            AND claim_review_head.last_sequence=claim_review.decision_sequence
           JOIN research_evidence_intake_item_review_decision evidence_review
             ON evidence_review.id=decision.evidence_review_decision_id
            AND evidence_review.project_id=decision.project_id
            AND evidence_review.research_evidence_intake_item_id=
                decision.evidence_intake_item_id
           JOIN research_evidence_item_review_sequence_allocator evidence_review_head
             ON evidence_review_head.project_id=evidence_review.project_id
            AND evidence_review_head.research_evidence_intake_item_id=
                evidence_review.research_evidence_intake_item_id
            AND evidence_review_head.last_sequence=evidence_review.decision_sequence
           JOIN research_evidence_intake_item evidence
             ON evidence.id=decision.evidence_intake_item_id
            AND evidence.project_id=decision.project_id
            AND evidence.candidate_fact_revision_id=
                support.candidate_fact_revision_id
            AND evidence.fact_metadata_revision_id=
                support.fact_metadata_revision_id
           WHERE decision.project_id=%s
             AND decision.usage_scope='client_report'
             AND decision.decision='authorized'
             AND support.locator_resolution='resolvable'
             AND support.evidence_linkage='linked'
             AND support.semantic_relationship IN ('support','qualification')
             AND claim_review.decision_type='approved'
             AND evidence_review.decision_type='approved'""",
        (project_id,),
    ).fetchone() == (expected_count, 1)


def _plan_nodes(node):
    yield node
    for child in node.get("Plans", ()):
        yield from _plan_nodes(child)


def test_assembly_query_returns_current_bounded_pack_and_typed_empty_scope(
    pack_schema,
):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    claim, evidence, value, authorization = _prepare_service_authorization(
        conn, "assembly-current",
    )
    context = pack_service.record_project_context_revision(
        conn,
        ResearchEvidenceProjectContextRevisionCreate(
            project_id=value.project_id,
            request_id="assembly-current-context",
            research_question="Which evidence is currently authorized?",
            project_limitations=("Bounded test context",),
            unresolved_gaps=("No external consumer",),
            actor="operator",
        ),
    )

    before = _pack_rows_snapshot(conn)
    assembled = pack_service.assemble_research_evidence_pack(
        conn, project_id=value.project_id, usage_scope=UsageScope.CLIENT_REPORT,
    )
    assert assembled == pack_service.assemble_research_evidence_pack(
        conn, project_id=value.project_id, usage_scope="client_report",
    )
    assert _pack_rows_snapshot(conn) == before
    assert assembled.project_id == value.project_id
    assert assembled.usage_scope is UsageScope.CLIENT_REPORT
    assert assembled.counts.model_dump() == {
        "source_count": 1,
        "claim_count": 1,
        "evidence_count": 1,
        "relationship_count": 1,
    }
    assert assembled.context.context_revision_id == context.id
    assert assembled.claims[0].claim_draft_id == claim["claim"]
    assert assembled.sources[0].source_snapshot_id == evidence["snapshot"]
    assert assembled.evidence[0].candidate_fact_revision_id == evidence["fact"]
    assert assembled.relationships[0].authorization_decision_id == authorization.id
    assert assembled.relationships[0].usage_scope is UsageScope.CLIENT_REPORT

    empty = pack_service.assemble_research_evidence_pack(
        conn, project_id=value.project_id, usage_scope="internal_analysis",
    )
    assert empty.project_id == value.project_id
    assert empty.usage_scope is UsageScope.INTERNAL_ANALYSIS
    assert empty.context is None
    assert empty.counts.model_dump() == {
        "source_count": 0,
        "claim_count": 0,
        "evidence_count": 0,
        "relationship_count": 0,
    }
    assert empty.claims == empty.sources == empty.evidence == empty.relationships == ()


def test_assembly_conflicting_parallel_metadata_roots_fail_closed(pack_schema):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    claim, evidence, value, _ = _prepare_service_authorization(
        conn, "assembly-conflicting-root",
    )
    conflicting_metadata = conn.execute(
        """INSERT INTO research_fact_metadata_revision
           (project_id,candidate_fact_revision_id,stable_fact_key,
            citation_locator,source_char_range,excerpt_hash,created_by)
           VALUES(%s,%s,'conflicting-stable-key','conflicting-citation',
                  '30-40','conflicting-excerpt','operator')
           RETURNING id::text""",
        (evidence["project"], evidence["fact"]),
    ).fetchone()[0]
    duplicate = _wrap_existing_fact(
        conn, evidence, fact_metadata_id=conflicting_metadata,
        tag="conflicting-root",
    )
    _authorize_duplicate_wrapper(
        conn, claim, duplicate, tag="conflicting-root",
    )
    before = _pack_rows_snapshot(conn)
    with pytest.raises(
        pack_service.repo.ResearchEvidencePackIntegrityError,
        match="conflicting canonical evidence",
    ):
        pack_service.assemble_research_evidence_pack(
            conn, project_id=value.project_id, usage_scope="client_report",
        )
    assert _pack_rows_snapshot(conn) == before
    assert conn.execute("SELECT 1").fetchone() == (1,)


def test_assembly_identical_wrappers_collapse_after_deterministic_comparison(
    pack_schema,
):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    claim, evidence, value, first_authorization = _prepare_service_authorization(
        conn, "assembly-identical-wrapper",
    )
    identical_metadata = conn.execute(
        """INSERT INTO research_fact_metadata_revision
           (project_id,candidate_fact_revision_id,stable_fact_key,
            drift_group_key,supersedes_candidate_fact_revision_id,
            source_char_range,excerpt_hash,citation_locator,metadata_json,
            supersedes_metadata_revision_id,created_by)
           SELECT project_id,candidate_fact_revision_id,stable_fact_key,
                  drift_group_key,supersedes_candidate_fact_revision_id,
                  source_char_range,excerpt_hash,citation_locator,metadata_json,
                  NULL,created_by
           FROM research_fact_metadata_revision WHERE id=%s
           RETURNING id::text""",
        (evidence["fact_metadata"],),
    ).fetchone()[0]
    assert identical_metadata != evidence["fact_metadata"]
    duplicate = _wrap_existing_fact(
        conn, evidence, fact_metadata_id=identical_metadata,
        tag="identical-wrapper",
    )
    second_authorization = _authorize_duplicate_wrapper(
        conn, claim, duplicate, tag="identical-wrapper",
    )
    packs = [
        pack_service.assemble_research_evidence_pack(
            conn, project_id=value.project_id, usage_scope="client_report",
        )
        for _ in range(3)
    ]
    assert packs[0] == packs[1] == packs[2]
    assert packs[0].counts.relationship_count == 1
    assert packs[0].counts.evidence_count == 1
    expected = (
        first_authorization.id
        if evidence["item"] < duplicate["item"]
        else second_authorization.id
    )
    assert packs[0].relationships[0].authorization_decision_id == expected
    expected_metadata = (
        evidence["fact_metadata"]
        if evidence["item"] < duplicate["item"]
        else identical_metadata
    )
    assert packs[0].evidence[0].fact_metadata_revision_id == expected_metadata


def test_candidate_boundary_is_exact_and_gates_wide_postgresql_work(pack_schema):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    claim, evidence, value, _ = _prepare_service_authorization(
        conn, "assembly-candidate-boundary",
    )
    _bulk_authorize_duplicate_wrappers(
        conn, claim, evidence, start_ordinal=1,
        count=MAX_PACK_CANDIDATE_REPRESENTATIONS - 1,
    )
    conn.commit()
    _assert_bulk_candidate_topology(
        conn, project_id=value.project_id,
        expected_count=MAX_PACK_CANDIDATE_REPRESENTATIONS,
    )

    exact = pack_service.assemble_research_evidence_pack(
        conn, project_id=value.project_id, usage_scope="client_report",
    )
    assert exact.counts.relationship_count == 1
    assert exact.counts.evidence_count == 1
    assert conn.execute(
        """SELECT count(*) FROM
           research_evidence_usage_authorization_sequence_allocator
           WHERE project_id=%s AND usage_scope='client_report'""",
        (value.project_id,),
    ).fetchone()[0] == MAX_PACK_CANDIDATE_REPRESENTATIONS

    _bulk_authorize_duplicate_wrappers(
        conn, claim, evidence,
        start_ordinal=MAX_PACK_CANDIDATE_REPRESENTATIONS,
        count=1,
    )
    conn.commit()
    _assert_bulk_candidate_topology(
        conn, project_id=value.project_id,
        expected_count=MAX_PACK_CANDIDATE_REPRESENTATIONS + 1,
    )

    params = (
        value.project_id, "client_report",
        MAX_PACK_CANDIDATE_REPRESENTATIONS + 1,
        MAX_PACK_CANDIDATE_REPRESENTATIONS,
    )
    document = conn.execute(
        "EXPLAIN (ANALYZE,FORMAT JSON,COSTS OFF,TIMING OFF,SUMMARY OFF) "
        + pack_service.repo._PACK_ASSEMBLY_SELECT,
        params,
    ).fetchone()[0]
    nodes = list(_plan_nodes(document[0]["Plan"]))
    head_plans = [
        node for node in nodes
        if node.get("Subplan Name") == "CTE bounded_authorization_heads"
    ]
    assert len(head_plans) == 1
    head_plan = head_plans[0]
    assert head_plan["Node Type"] == "Limit"
    assert head_plan["Actual Rows"] == MAX_PACK_CANDIDATE_REPRESENTATIONS + 1
    head_nodes = list(_plan_nodes(head_plan))
    assert not {
        node["Node Type"] for node in head_nodes
    } & {
        "Sort", "Incremental Sort", "Unique", "Aggregate", "Hash",
        "Hash Join", "Materialize", "Memoize",
    }
    assert {
        node["Relation Name"] for node in head_nodes
        if "Relation Name" in node
    } == {"research_evidence_usage_authorization_sequence_allocator"}
    decision_nodes = [
        node for node in nodes
        if node.get("Relation Name") ==
        "research_evidence_usage_authorization_decision"
        and node not in head_nodes
    ]
    assert decision_nodes
    assert all(node.get("Actual Loops") == 0 for node in decision_nodes)
    wide_relations = {
        "research_evidence_intake_item", "research_evidence_intake",
        "source_snapshot", "source_blob", "research_source_metadata_revision",
        "research_claim_draft", "candidate_fact_revision",
        "research_fact_metadata_revision",
        "research_evidence_claim_annotation_revision",
        "research_evidence_claim_support_assessment",
        "research_evidence_intake_item_review_decision",
        "evidence_retention_event", "research_evidence_event",
    }
    wide_nodes = [
        node for node in nodes if node.get("Relation Name") in wide_relations
        and node not in head_nodes
    ]
    assert wide_nodes
    assert all(node.get("Actual Loops") == 0 for node in wide_nodes)

    with pytest.raises(
        pack_service.repo.ResearchEvidencePackCapacityError,
        match="authorization wrapper heads",
    ):
        pack_service.repo.assemble_effective_project_pack(
            conn, project_id=value.project_id,
            usage_scope=UsageScope.CLIENT_REPORT,
        )
    assert conn.execute("SELECT 1").fetchone() == (1,)


def test_revoked_wrapper_heads_consume_candidate_boundary(pack_schema, monkeypatch):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    claim, evidence, value, _ = _prepare_service_authorization(
        conn, "assembly-revoked-head-boundary",
    )
    _revoke_wrapper(conn, claim, evidence, tag="revoked-head-original")
    duplicate = _wrap_existing_fact(
        conn, evidence, fact_metadata_id=evidence["fact_metadata"],
        tag="revoked-head-duplicate-1",
    )
    _authorize_duplicate_wrapper(
        conn, claim, duplicate, tag="revoked-head-duplicate-1",
    )
    _revoke_wrapper(conn, claim, duplicate, tag="revoked-head-duplicate-1")
    monkeypatch.setattr(
        pack_service.repo, "MAX_PACK_CANDIDATE_REPRESENTATIONS", 2,
    )
    assert pack_service.assemble_research_evidence_pack(
        conn, project_id=value.project_id, usage_scope="client_report",
    ).counts.relationship_count == 0

    overflow = _wrap_existing_fact(
        conn, evidence, fact_metadata_id=evidence["fact_metadata"],
        tag="revoked-head-duplicate-2",
    )
    _authorize_duplicate_wrapper(
        conn, claim, overflow, tag="revoked-head-duplicate-2",
    )
    _revoke_wrapper(conn, claim, overflow, tag="revoked-head-duplicate-2")
    with pytest.raises(
        pack_service.repo.ResearchEvidencePackCapacityError,
        match="authorization wrapper heads",
    ):
        pack_service.repo.assemble_effective_project_pack(
            conn, project_id=value.project_id,
            usage_scope=UsageScope.CLIENT_REPORT,
        )
    assert conn.execute("SELECT 1").fetchone() == (1,)


def test_backward_allocator_head_cannot_resurrect_historical_authorization(
    pack_schema,
):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    claim, evidence, value, _ = _prepare_service_authorization(
        conn, "assembly-backward-authorization-head",
    )
    _revoke_wrapper(
        conn, claim, evidence, tag="assembly-backward-authorization-head",
    )
    conn.execute(
        """UPDATE research_evidence_usage_authorization_sequence_allocator
           SET last_sequence=1
           WHERE project_id=%s AND claim_intake_item_id=%s
             AND evidence_intake_item_id=%s AND usage_scope='client_report'""",
        (value.project_id, claim["item"], evidence["item"]),
    )
    before = _pack_rows_snapshot(conn)
    assembled = pack_service.assemble_research_evidence_pack(
        conn, project_id=value.project_id, usage_scope="client_report",
    )
    assert assembled.counts.relationship_count == 0
    assert assembled.relationships == ()
    assert _pack_rows_snapshot(conn) == before
    assert conn.execute("SELECT 1").fetchone() == (1,)


def test_genuine_transition_exception_enters_bounded_repository_recovery(
    pack_schema, monkeypatch,
):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    _, _, value, first = _prepare_service_authorization(
        conn, "genuine-transition-recovery"
    )
    candidate = value.model_copy(update={
        "request_id": "genuine-transition-recovery-second-request",
    })
    recovered = first.model_copy(update={"request_id": candidate.request_id})
    lookups = iter((None, recovered))
    lookup_count = 0

    def lookup(*args, **kwargs):
        nonlocal lookup_count
        lookup_count += 1
        return next(lookups)

    monkeypatch.setattr(
        pack_service.repo, "get_usage_authorization_decision_by_request_id", lookup
    )
    before = _pack_rows_snapshot(conn)
    assert pack_service.repo.insert_usage_authorization_decision(
        conn, candidate
    ) is recovered
    assert lookup_count == 2
    assert _pack_rows_snapshot(conn) == before
    assert conn.execute("SELECT 1").fetchone() == (1,)


@pytest.mark.parametrize(
    "invalidation",
    ["annotation", "support", "claim_review", "evidence_review"],
)
def test_effective_authorization_basis_matrix_and_reauthorization(
    pack_schema, invalidation,
):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    tag = f"basis-{invalidation}"
    claim, evidence, value, first = _prepare_service_authorization(conn, tag)
    assert pack_service.claim_evidence_usage_is_authorized(
        conn, project_id=value.project_id,
        claim_intake_item_id=value.claim_intake_item_id,
        evidence_intake_item_id=value.evidence_intake_item_id,
        usage_scope=value.usage_scope,
    )
    assert pack_service.repo.effective_project_pack_member_counts(
        conn, project_id=value.project_id,
    ) == (1, 1)

    if invalidation == "annotation":
        conn.execute(
            """INSERT INTO research_evidence_claim_annotation_revision
               (project_id,claim_draft_id,request_id,epistemic_status,
                confidence_label,decision_relevance,supports_statement,
                does_not_prove,limitations_json,related_claim_draft_ids_json,
                operator_notes,actor)
               VALUES(%s,%s,%s,'inference','medium','relevant','supports',
                      'limited','[]','[]',NULL,'operator')""",
            (value.project_id, claim["claim"], f"{tag}-annotation-2"),
        )
    elif invalidation == "support":
        insert_support(conn, claim, evidence, request_id=f"{tag}-support-2")
    else:
        item = claim["item"] if invalidation == "claim_review" else evidence["item"]
        conn.execute(
            """INSERT INTO research_evidence_intake_item_review_decision
               (project_id,research_evidence_intake_item_id,decision_type,
                decision_reason,decided_by,request_id)
               VALUES(%s,%s,'approved','Re-reviewed','operator',%s)""",
            (value.project_id, item, f"{tag}-{invalidation}-2"),
        )

    assert not pack_service.claim_evidence_usage_is_authorized(
        conn, project_id=value.project_id,
        claim_intake_item_id=value.claim_intake_item_id,
        evidence_intake_item_id=value.evidence_intake_item_id,
        usage_scope=value.usage_scope,
    )
    assert pack_service.list_effective_project_authorizations(
        conn, project_id=value.project_id,
    ) == []
    assert pack_service.repo.effective_project_pack_member_counts(
        conn, project_id=value.project_id,
    ) == (0, 0)
    assert pack_service.assemble_research_evidence_pack(
        conn, project_id=value.project_id, usage_scope=value.usage_scope,
    ).counts.relationship_count == 0

    revoked = pack_service.record_usage_authorization_decision(
        conn,
        value.model_copy(update={
            "decision": "revoked", "reason": "refresh bases",
            "request_id": f"{tag}-revocation",
        }),
    )
    restored = pack_service.record_usage_authorization_decision(
        conn,
        value.model_copy(update={
            "reason": "reauthorized on current bases",
            "request_id": f"{tag}-reauthorization",
        }),
    )
    assert revoked.supersedes_decision_id == first.id
    assert restored.supersedes_decision_id == revoked.id
    assert restored.decision_sequence == 3
    assert pack_service.claim_evidence_usage_is_authorized(
        conn, project_id=value.project_id,
        claim_intake_item_id=value.claim_intake_item_id,
        evidence_intake_item_id=value.evidence_intake_item_id,
        usage_scope=value.usage_scope,
    )
    assert pack_service.repo.effective_project_pack_member_counts(
        conn, project_id=value.project_id,
    ) == (1, 1)
    assert pack_service.assemble_research_evidence_pack(
        conn, project_id=value.project_id, usage_scope=value.usage_scope,
    ).counts.relationship_count == 1


def test_revocation_has_no_fallback_and_scopes_are_independent(pack_schema):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    claim, evidence, client, client_record = _prepare_service_authorization(
        conn, "scope-matrix",
    )
    internal = client.model_copy(update={
        "usage_scope": "internal_analysis", "request_id": "scope-internal",
    })
    dossier = client.model_copy(update={
        "usage_scope": "operator_dossier", "request_id": "scope-dossier",
    })
    pack_service.record_usage_authorization_decision(conn, internal)
    pack_service.record_usage_authorization_decision(conn, dossier)
    assert pack_service.repo.effective_project_pack_member_counts(
        conn, project_id=client.project_id,
    ) == (1, 1)
    assert pack_service.assemble_research_evidence_pack(
        conn, project_id=client.project_id, usage_scope="client_report",
    ).counts.relationship_count == 1
    assert pack_service.assemble_research_evidence_pack(
        conn, project_id=client.project_id, usage_scope="internal_analysis",
    ).counts.relationship_count == 1
    for scope in ("client_report", "internal_analysis", "operator_dossier"):
        assert pack_service.claim_evidence_usage_is_authorized(
            conn, project_id=client.project_id,
            claim_intake_item_id=client.claim_intake_item_id,
            evidence_intake_item_id=client.evidence_intake_item_id,
            usage_scope=scope,
        )

    revocation = pack_service.record_usage_authorization_decision(
        conn,
        client.model_copy(update={
            "decision": "revoked", "reason": "client use revoked",
            "request_id": "scope-client-revoked",
        }),
    )
    assert revocation.supersedes_decision_id == client_record.id
    assert not pack_service.claim_evidence_usage_is_authorized(
        conn, project_id=client.project_id,
        claim_intake_item_id=client.claim_intake_item_id,
        evidence_intake_item_id=client.evidence_intake_item_id,
        usage_scope="client_report",
    )
    assert pack_service.claim_evidence_usage_is_authorized(
        conn, project_id=client.project_id,
        claim_intake_item_id=client.claim_intake_item_id,
        evidence_intake_item_id=client.evidence_intake_item_id,
        usage_scope="internal_analysis",
    )
    assert pack_service.repo.effective_project_pack_member_counts(
        conn, project_id=client.project_id,
    ) == (1, 1)
    assert pack_service.assemble_research_evidence_pack(
        conn, project_id=client.project_id, usage_scope="client_report",
    ).counts.relationship_count == 0
    assert pack_service.assemble_research_evidence_pack(
        conn, project_id=client.project_id, usage_scope="internal_analysis",
    ).counts.relationship_count == 1
    effective_ids = {
        record.id for record in pack_service.list_effective_project_authorizations(
            conn, project_id=client.project_id,
        )
    }
    assert client_record.id not in effective_ids
    assert len(effective_ids) == 2


def test_withdrawn_claim_is_ineffective_and_uncounted(pack_schema):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    claim, _, value, _ = _prepare_service_authorization(conn, "withdrawn-claim")
    conn.execute(
        """INSERT INTO research_evidence_event
           (project_id,entity_type,entity_id,event_type)
           VALUES(%s,'claim_draft',%s,'withdrawn')""",
        (value.project_id, claim["claim"]),
    )
    assert not pack_service.claim_evidence_usage_is_authorized(
        conn, project_id=value.project_id,
        claim_intake_item_id=value.claim_intake_item_id,
        evidence_intake_item_id=value.evidence_intake_item_id,
        usage_scope=value.usage_scope,
    )
    assert pack_service.repo.effective_project_pack_member_counts(
        conn, project_id=value.project_id,
    ) == (0, 0)


def test_outer_transaction_rollback_and_commit_remain_caller_owned(pack_schema):
    conn, schema = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    _, _, client, _ = _prepare_service_authorization(conn, "outer-transaction")
    conn.commit()
    candidate = client.model_copy(update={
        "usage_scope": "operator_dossier", "request_id": "outer-dossier",
    })
    caller = pg.connect(schema=schema)
    try:
        caller.execute("BEGIN ISOLATION LEVEL READ COMMITTED")
        written = pack_service.record_usage_authorization_decision(caller, candidate)
        assert written.usage_scope.value == "operator_dossier"
        caller.rollback()
        assert conn.execute(
            """SELECT count(*) FROM research_evidence_usage_authorization_decision
               WHERE project_id=%s AND request_id=%s""",
            (candidate.project_id, candidate.request_id),
        ).fetchone()[0] == 0

        caller.execute("BEGIN ISOLATION LEVEL READ COMMITTED")
        persisted = pack_service.record_usage_authorization_decision(caller, candidate)
        caller.commit()
        assert conn.execute(
            """SELECT id::text FROM research_evidence_usage_authorization_decision
               WHERE project_id=%s AND request_id=%s""",
            (candidate.project_id, candidate.request_id),
        ).fetchone()[0] == persisted.id
    finally:
        caller.rollback()
        caller.close()


@pytest.mark.parametrize("outer_kind", ["mapping", "model"])
def test_nested_probability_excess_precision_is_rejected_without_sql_side_effects(
    pack_schema, outer_kind,
):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    claim, _ = seed_pair(conn, tag=f"nested-probability-{outer_kind}")
    valid_probability = ResearchEvidenceExplicitProbability(
        value=Decimal("0.123456"), provided_by="operator",
        provenance_reference="calculation", provenance_note="operator entry",
    )
    base = ResearchEvidenceClaimAnnotationRevisionCreate(
        project_id=claim["project"], claim_draft_id=claim["claim"],
        request_id=f"nested-probability-{outer_kind}",
        epistemic_status="estimate", confidence_label="medium",
        decision_relevance="relevant", supports_statement="supports",
        does_not_prove="limited", limitations=[], related_claim_draft_ids=[],
        explicit_probability=valid_probability, actor="operator",
    )
    invalid_probability = valid_probability.model_copy(
        update={"value": Decimal("0.1234567")},
    )
    candidate = (
        {**base.model_dump(), "explicit_probability": invalid_probability}
        if outer_kind == "mapping"
        else base.model_copy(update={"explicit_probability": invalid_probability})
    )
    with pytest.raises(ValidationError, match="six decimal"):
        pack_service.record_claim_annotation_revision(conn, candidate)
    assert conn.execute(
        "SELECT count(*) FROM research_evidence_claim_annotation_revision "
        "WHERE project_id=%s AND claim_draft_id=%s",
        (claim["project"], claim["claim"]),
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT count(*) FROM research_evidence_claim_annotation_sequence_allocator "
        "WHERE project_id=%s AND claim_draft_id=%s",
        (claim["project"], claim["claim"]),
    ).fetchone()[0] == 0


@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
@pytest.mark.parametrize("entrypoint", ["service", "repository"])
def test_postgresql_write_entrypoints_reject_unsupported_isolation_before_mutation(
    pack_schema, isolation, entrypoint,
):
    conn, schema = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    conn.commit()
    value = ResearchEvidenceUsageAuthorizationDecisionCreate(
        project_id=str(uuid4()), claim_intake_item_id=str(uuid4()),
        evidence_intake_item_id=str(uuid4()), usage_scope="client_report",
        decision="authorized", reason="must not reach business processing",
        actor="operator", request_id=f"isolation-{entrypoint}-{isolation}",
    )
    caller = pg.connect(schema=schema)
    try:
        caller.execute(f"BEGIN ISOLATION LEVEL {isolation}")
        target = (
            pack_service.record_usage_authorization_decision
            if entrypoint == "service"
            else pack_service.repo.insert_usage_authorization_decision
        )
        error_type = (
            pack_service.ResearchEvidencePackTransactionError
            if entrypoint == "service"
            else pack_service.repo.ResearchEvidencePackTransactionError
        )
        with pytest.raises(error_type, match="READ COMMITTED"):
            target(caller, value)
        assert caller.execute("SELECT 1").fetchone()[0] == 1
        caller.rollback()
        assert conn.execute(
            "SELECT count(*) FROM research_evidence_usage_authorization_decision"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT count(*) FROM research_evidence_usage_authorization_sequence_allocator"
        ).fetchone()[0] == 0
    finally:
        caller.rollback()
        caller.close()


def test_cross_project_claim_and_evidence_are_rejected_without_sequence(pack_schema):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    claim_a, evidence_a, value_a, _ = _prepare_service_authorization(
        conn, "cross-project-a",
    )
    claim_b, evidence_b = seed_pair(conn, tag="cross-project-b")
    attempts = (
        value_a.model_copy(update={
            "claim_intake_item_id": claim_b["item"],
            "request_id": "cross-project-claim",
            "usage_scope": "internal_analysis",
        }),
        value_a.model_copy(update={
            "evidence_intake_item_id": evidence_b["item"],
            "request_id": "cross-project-evidence",
            "usage_scope": "operator_dossier",
        }),
    )
    for attempt in attempts:
        with pytest.raises(ValueError):
            pack_service.record_usage_authorization_decision(conn, attempt)
    assert conn.execute(
        """SELECT count(*)
           FROM research_evidence_usage_authorization_sequence_allocator
           WHERE project_id=%s""",
        (claim_a["project"],),
    ).fetchone()[0] == 1
    project_a_pack = pack_service.assemble_research_evidence_pack(
        conn, project_id=claim_a["project"], usage_scope="client_report",
    )
    project_b_pack = pack_service.assemble_research_evidence_pack(
        conn, project_id=claim_b["project"], usage_scope="client_report",
    )
    assert {item.claim_draft_id for item in project_a_pack.claims} == {
        claim_a["claim"],
    }
    assert project_b_pack.counts.relationship_count == 0


def _approve_item(conn, project_id, item_id, request_id):
    conn.execute(
        """INSERT INTO research_evidence_intake_item_review_decision
           (project_id,research_evidence_intake_item_id,decision_type,
            decision_reason,decided_by,request_id)
           VALUES(%s,%s,'approved','Reviewed','operator',%s)""",
        (project_id, item_id, request_id),
    )


def _annotate_claim(conn, project_id, claim_id, request_id):
    conn.execute(
        """INSERT INTO research_evidence_claim_annotation_revision
           (project_id,claim_draft_id,request_id,epistemic_status,
            confidence_label,decision_relevance,supports_statement,
            does_not_prove,limitations_json,related_claim_draft_ids_json,
            operator_notes,actor)
           VALUES(%s,%s,%s,'reported_fact','high','relevant','supports',
                  'limited','[]','[]',NULL,'operator')""",
        (project_id, claim_id, request_id),
    )


def test_source_limit_is_effective_distinct_and_does_not_consume_rejection(pack_schema):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    claim, first_evidence = seed_pair(conn, tag="source-limit")
    _approve_item(conn, claim["project"], claim["item"], "source-limit-claim-review")
    _annotate_claim(conn, claim["project"], claim["claim"], "source-limit-annotation")
    evidence_items = [first_evidence]
    evidence_items.extend(
        seed_endpoint(
            conn, project_id=claim["project"], kind="candidate_fact",
            tag=f"source-limit-evidence-{index}",
        )
        for index in range(2, 52)
    )
    accepted = []
    for index, evidence in enumerate(evidence_items[:50], 1):
        _approve_item(
            conn, claim["project"], evidence["item"],
            f"source-limit-evidence-review-{index}",
        )
        insert_support(
            conn, claim, evidence, request_id=f"source-limit-support-{index}",
        )
        accepted.append(pack_service.record_usage_authorization_decision(
            conn,
            ResearchEvidenceUsageAuthorizationDecisionCreate(
                project_id=claim["project"],
                claim_intake_item_id=claim["item"],
                evidence_intake_item_id=evidence["item"],
                usage_scope="client_report", decision="authorized",
                reason="approved", actor="operator",
                request_id=f"source-limit-authorization-{index}",
            ),
        ))
    assert pack_service.repo.effective_project_pack_member_counts(
        conn, project_id=claim["project"],
    ) == (50, 1)
    bounded = pack_service.assemble_research_evidence_pack(
        conn, project_id=claim["project"], usage_scope="client_report",
    )
    assert bounded.counts.source_count == 50
    assert bounded.counts.relationship_count == 50
    rejected_evidence = evidence_items[50]
    _approve_item(
        conn, claim["project"], rejected_evidence["item"],
        "source-limit-evidence-review-51",
    )
    insert_support(
        conn, claim, rejected_evidence, request_id="source-limit-support-51",
    )
    with pytest.raises(pack_service.ResearchEvidencePackLimitError, match="50"):
        pack_service.record_usage_authorization_decision(
            conn,
            ResearchEvidenceUsageAuthorizationDecisionCreate(
                project_id=claim["project"],
                claim_intake_item_id=claim["item"],
                evidence_intake_item_id=rejected_evidence["item"],
                usage_scope="client_report", decision="authorized",
                reason="approved", actor="operator",
                request_id="source-limit-authorization-51",
            ),
        )
    assert pack_service.repo.effective_project_pack_member_counts(
        conn, project_id=claim["project"],
    ) == (50, 1)
    assert conn.execute(
        """SELECT count(*)
           FROM research_evidence_usage_authorization_sequence_allocator
           WHERE project_id=%s AND claim_intake_item_id=%s
             AND evidence_intake_item_id=%s""",
        (claim["project"], claim["item"], rejected_evidence["item"]),
    ).fetchone()[0] == 0


def test_claim_limit_is_effective_distinct_and_does_not_consume_rejection(pack_schema):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    first_claim, evidence = seed_pair(conn, tag="claim-limit")
    _approve_item(
        conn, first_claim["project"], evidence["item"],
        "claim-limit-evidence-review",
    )
    claims = [first_claim]
    claims.extend(
        seed_endpoint(
            conn, project_id=first_claim["project"], kind="claim_draft",
            tag=f"claim-limit-claim-{index}",
        )
        for index in range(2, 202)
    )
    for index, claim in enumerate(claims[:200], 1):
        _approve_item(
            conn, first_claim["project"], claim["item"],
            f"claim-limit-claim-review-{index}",
        )
        _annotate_claim(
            conn, first_claim["project"], claim["claim"],
            f"claim-limit-annotation-{index}",
        )
        insert_support(
            conn, claim, evidence, request_id=f"claim-limit-support-{index}",
        )
        pack_service.record_usage_authorization_decision(
            conn,
            ResearchEvidenceUsageAuthorizationDecisionCreate(
                project_id=first_claim["project"],
                claim_intake_item_id=claim["item"],
                evidence_intake_item_id=evidence["item"],
                usage_scope="client_report", decision="authorized",
                reason="approved", actor="operator",
                request_id=f"claim-limit-authorization-{index}",
            ),
        )
    assert pack_service.repo.effective_project_pack_member_counts(
        conn, project_id=first_claim["project"],
    ) == (1, 200)
    bounded = pack_service.assemble_research_evidence_pack(
        conn, project_id=first_claim["project"], usage_scope="client_report",
    )
    assert bounded.counts.claim_count == 200
    assert bounded.counts.relationship_count == 200
    rejected_claim = claims[200]
    _approve_item(
        conn, first_claim["project"], rejected_claim["item"],
        "claim-limit-claim-review-201",
    )
    _annotate_claim(
        conn, first_claim["project"], rejected_claim["claim"],
        "claim-limit-annotation-201",
    )
    insert_support(
        conn, rejected_claim, evidence, request_id="claim-limit-support-201",
    )
    with pytest.raises(pack_service.ResearchEvidencePackLimitError, match="200"):
        pack_service.record_usage_authorization_decision(
            conn,
            ResearchEvidenceUsageAuthorizationDecisionCreate(
                project_id=first_claim["project"],
                claim_intake_item_id=rejected_claim["item"],
                evidence_intake_item_id=evidence["item"],
                usage_scope="client_report", decision="authorized",
                reason="approved", actor="operator",
                request_id="claim-limit-authorization-201",
            ),
        )
    assert pack_service.repo.effective_project_pack_member_counts(
        conn, project_id=first_claim["project"],
    ) == (1, 200)
    assert conn.execute(
        """SELECT count(*)
           FROM research_evidence_usage_authorization_sequence_allocator
           WHERE project_id=%s AND claim_intake_item_id=%s
             AND evidence_intake_item_id=%s""",
        (first_claim["project"], rejected_claim["item"], evidence["item"]),
    ).fetchone()[0] == 0


@pytest.mark.parametrize(
    "mode",
    ["matching", "conflicting", "valid_transition"],
)
def test_concurrent_service_requests_serialize_after_project_lock(
    pack_schema, monkeypatch, mode,
):
    conn, schema = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    claim, evidence = seed_pair(conn, tag="concurrent-matching")
    for item, request in (
        (claim["item"], "concurrent-claim-review"),
        (evidence["item"], "concurrent-evidence-review"),
    ):
        conn.execute(
            """INSERT INTO research_evidence_intake_item_review_decision
               (project_id,research_evidence_intake_item_id,decision_type,
                decision_reason,decided_by,request_id)
               VALUES(%s,%s,'approved','Reviewed','operator',%s)""",
            (claim["project"], item, request),
        )
    insert_support(conn, claim, evidence, request_id="concurrent-support")
    conn.execute(
        """INSERT INTO research_evidence_claim_annotation_revision
           (project_id,claim_draft_id,request_id,epistemic_status,
            confidence_label,decision_relevance,supports_statement,
            does_not_prove,limitations_json,related_claim_draft_ids_json,
            operator_notes,actor)
           VALUES(%s,%s,'concurrent-annotation','reported_fact','high',
                  'relevant','supports','limited','[]','[]',NULL,'operator')""",
        (claim["project"], claim["claim"]),
    )
    conn.commit()

    value = ResearchEvidenceUsageAuthorizationDecisionCreate(
        project_id=claim["project"], claim_intake_item_id=claim["item"],
        evidence_intake_item_id=evidence["item"], usage_scope="client_report",
        decision="authorized", reason="approved", actor="operator",
        request_id="concurrent-authorization",
    )
    value_b = value
    if mode == "conflicting":
        value_b = value.model_copy(update={"reason": "different payload"})
    elif mode == "valid_transition":
        value_b = value.model_copy(update={
            "decision": "revoked", "reason": "revoked by operator",
            "request_id": "concurrent-revocation",
        })
    caller_a = pg.connect(schema=schema)
    caller_b = pg.connect(schema=schema)
    a_locked = threading.Event()
    allow_a_return = threading.Event()
    b_started = threading.Event()
    outcomes = {}
    errors = {}
    original_lock = pack_service.repo.lock_project

    def observed_lock(connection, *, project_id):
        original_lock(connection, project_id=project_id)
        if threading.current_thread().name == "caller-a":
            a_locked.set()
            assert allow_a_return.wait(5), "caller A was not released by harness"

    def invoke(name, connection, request_value, started=None):
        try:
            if started is not None:
                started.set()
            outcomes[name] = pack_service.record_usage_authorization_decision(
                connection, request_value,
            )
        except BaseException as exc:  # every worker failure is captured
            errors[name] = exc

    monkeypatch.setattr(pack_service.repo, "lock_project", observed_lock)
    thread_a = threading.Thread(
        target=invoke, args=("a", caller_a, value), name="caller-a", daemon=True,
    )
    thread_b = threading.Thread(
        target=invoke, args=("b", caller_b, value_b, b_started),
        name="caller-b", daemon=True,
    )
    try:
        for connection in (caller_a, caller_b):
            connection.execute("BEGIN ISOLATION LEVEL READ COMMITTED")
            connection.execute("SET LOCAL lock_timeout = '5s'")
            connection.execute("SET LOCAL statement_timeout = '10s'")
        backend_b = caller_b.execute("SELECT pg_backend_pid()").fetchone()[0]
        thread_a.start()
        assert a_locked.wait(5), "caller A did not acquire the project lock"
        thread_b.start()
        assert b_started.wait(5), "caller B did not start"
        wait_state = None
        for _ in range(5000):
            wait_state = conn.execute(
                "SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s",
                (backend_b,),
            ).fetchone()
            if wait_state and wait_state[0] == "Lock":
                break
        assert wait_state and wait_state[0] == "Lock", (
            f"caller B did not reach observable lock wait; state={wait_state!r}"
        )
        allow_a_return.set()
        thread_a.join(5)
        assert not thread_a.is_alive(), "caller A did not return to harness"
        assert "a" not in errors, repr(errors.get("a"))
        caller_a.commit()
        thread_b.join(5)
        assert not thread_b.is_alive(), "caller B did not finish after caller A commit"
        if mode == "conflicting":
            assert isinstance(
                errors.get("b"),
                pack_service.repo.ResearchEvidencePackRequestConflict,
            )
            assert set(outcomes) == {"a"}
            caller_b.rollback()
        else:
            assert errors == {}
            if mode == "matching":
                assert outcomes["a"].id == outcomes["b"].id
            else:
                assert outcomes["a"].id != outcomes["b"].id
                assert outcomes["b"].supersedes_decision_id == outcomes["a"].id
                assert outcomes["b"].decision_sequence == 2
            caller_b.commit()
        row = conn.execute(
            """SELECT count(*), min(decision_sequence), max(decision_sequence),
                      max(request_id) FILTER (WHERE decision_sequence=1)
               FROM research_evidence_usage_authorization_decision
               WHERE project_id=%s AND claim_intake_item_id=%s
                 AND evidence_intake_item_id=%s AND usage_scope='client_report'""",
            (claim["project"], claim["item"], evidence["item"]),
        ).fetchone()
        if mode == "valid_transition":
            assert row == (2, 1, 2, value.request_id)
        else:
            assert row == (1, 1, 1, value.request_id)
        allocator = conn.execute(
            """SELECT last_sequence
               FROM research_evidence_usage_authorization_sequence_allocator
               WHERE project_id=%s AND claim_intake_item_id=%s
                 AND evidence_intake_item_id=%s AND usage_scope='client_report'""",
            (claim["project"], claim["item"], evidence["item"]),
        ).fetchone()[0]
        assert allocator == (2 if mode == "valid_transition" else 1)
    finally:
        allow_a_return.set()
        for thread in (thread_a, thread_b):
            if thread.is_alive():
                thread.join(1)
        for connection in (caller_a, caller_b):
            try:
                connection.rollback()
            finally:
                connection.close()


@pytest.mark.parametrize("mode", ["matching", "conflicting"])
def test_concurrent_direct_repository_requests_serialize_and_preserve_caller_control(
    pack_schema, mode,
):
    conn, schema = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    claim, evidence, service_value, _ = _prepare_service_authorization(
        conn, f"direct-race-{mode}",
    )
    value = service_value.model_copy(update={
        "usage_scope": UsageScope.OPERATOR_DOSSIER,
        "request_id": f"direct-race-{mode}-request",
    })
    value_b = (
        value.model_copy(update={"reason": "different caller payload"})
        if mode == "conflicting" else value
    )
    conn.commit()
    caller_a = pg.connect(schema=schema)
    caller_b = pg.connect(schema=schema)
    outcomes = {}
    errors = {}

    def invoke(name, connection, candidate):
        try:
            outcomes[name] = pack_service.repo.insert_usage_authorization_decision(
                connection, candidate,
            )
        except BaseException as exc:
            errors[name] = exc

    thread_a = threading.Thread(
        target=invoke, args=("a", caller_a, value), daemon=True,
    )
    thread_b = threading.Thread(
        target=invoke, args=("b", caller_b, value_b), daemon=True,
    )
    try:
        for caller in (caller_a, caller_b):
            caller.execute("BEGIN ISOLATION LEVEL READ COMMITTED")
            caller.execute("SET LOCAL lock_timeout = '5s'")
            caller.execute("SET LOCAL statement_timeout = '10s'")
        backend_b = caller_b.execute("SELECT pg_backend_pid()").fetchone()[0]
        thread_a.start()
        thread_a.join(5)
        assert not thread_a.is_alive(), "caller A repository insertion did not return"
        assert "a" not in errors, repr(errors.get("a"))
        thread_b.start()
        wait_state = None
        for _ in range(5000):
            wait_state = conn.execute(
                "SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s",
                (backend_b,),
            ).fetchone()
            if wait_state and wait_state[0] == "Lock":
                break
        assert wait_state and wait_state[0] == "Lock", (
            f"caller B did not reach project-row lock wait; state={wait_state!r}"
        )
        caller_a.commit()
        thread_b.join(5)
        assert not thread_b.is_alive(), "caller B did not resume after caller A commit"
        if mode == "matching":
            assert errors == {}
            assert outcomes["a"].id == outcomes["b"].id
            caller_b.commit()
        else:
            assert isinstance(
                errors.get("b"),
                pack_service.repo.ResearchEvidencePackRequestConflict,
            )
            assert set(outcomes) == {"a"}
            assert caller_b.execute("SELECT 1").fetchone()[0] == 1
            caller_b.rollback()
        assert conn.execute(
            """SELECT count(*),count(DISTINCT request_id),
                      min(decision_sequence),max(decision_sequence)
               FROM research_evidence_usage_authorization_decision
               WHERE project_id=%s AND claim_intake_item_id=%s
                 AND evidence_intake_item_id=%s AND usage_scope='operator_dossier'""",
            (claim["project"], claim["item"], evidence["item"]),
        ).fetchone() == (1, 1, 1, 1)
        assert conn.execute(
            """SELECT last_sequence
               FROM research_evidence_usage_authorization_sequence_allocator
               WHERE project_id=%s AND claim_intake_item_id=%s
                 AND evidence_intake_item_id=%s AND usage_scope='operator_dossier'""",
            (claim["project"], claim["item"], evidence["item"]),
        ).fetchone()[0] == 1
    finally:
        for thread in (thread_a, thread_b):
            if thread.is_alive():
                thread.join(1)
        for caller in (caller_a, caller_b):
            try:
                caller.rollback()
            finally:
                caller.close()


def test_effective_authorization_listing_uses_documented_canonical_order(pack_schema):
    conn, _ = pack_schema
    pg.apply_v61_research_evidence_pack(conn)
    _, _, client, _ = _prepare_service_authorization(conn, "effective-order")
    for scope in ("operator_dossier", "internal_analysis"):
        pack_service.record_usage_authorization_decision(
            conn,
            client.model_copy(update={
                "usage_scope": UsageScope(scope),
                "request_id": f"effective-order-{scope}",
            }),
        )
    expected = ["client_report", "internal_analysis", "operator_dossier"]
    for _ in range(3):
        assert [
            item.usage_scope.value
            for item in pack_service.list_effective_project_authorizations(
                conn, project_id=client.project_id,
            )
        ] == expected


def test_migration_is_single_transaction_and_does_not_edit_prior_migrations():
    text = pg.V61_RESEARCH_EVIDENCE_PACK_SQL.read_text(encoding="utf-8")
    assert text.count("BEGIN;") == 1
    assert text.count("COMMIT;") == 1
    assert "ON DELETE RESTRICT" in text
    assert "GRANT " not in text
    assert "FROM PUBLIC" in text
    assert Path(pg.V56_RESEARCH_CLAIM_SUPPORT_SQL).name.startswith("v56_")
