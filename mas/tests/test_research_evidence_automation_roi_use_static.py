"""Static security boundaries for the R1.6A Automation ROI foundation."""
import ast
import hashlib
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = ROOT / "sql" / "v59_research_evidence_automation_roi_input_snapshot.sql"
SQL_RAW = SQL_PATH.read_text(encoding="utf-8")
POLICY = (
    ROOT / "research_evidence" / "automation_roi_use_policy.py"
).read_text(encoding="utf-8")
MODELS = (
    ROOT / "research_evidence" / "automation_roi_use_models.py"
).read_text(encoding="utf-8")
REPOSITORY = (
    ROOT / "research_evidence" / "automation_roi_use_repository.py"
).read_text(encoding="utf-8")
SERVICE = (
    ROOT / "research_evidence" / "automation_roi_use_service.py"
).read_text(encoding="utf-8")
PACKAGE_INIT = (
    ROOT / "research_evidence" / "__init__.py"
).read_text(encoding="utf-8")
PG_HELPER = (ROOT / "tests" / "evidence_snapshot_pg.py").read_text(
    encoding="utf-8"
)
SCHEMA_TEST = (
    ROOT / "tests" / "test_research_evidence_automation_roi_use_schema.py"
).read_text(encoding="utf-8")
PACKAGE = POLICY + MODELS + REPOSITORY + SERVICE

R1_6A_IDENTIFIERS = (
    "research_evidence_automation_roi",
    "research_evidence_automation_roi_input_snapshot",
    "research_evidence_automation_roi_input_snapshot_binding",
    "automation_roi_input_snapshot_sequence_allocator",
    "research_evidence_prepare_automation_roi_snapshot",
    "research_evidence_prepare_automation_roi_snapshot_binding",
    "research_evidence_evaluate_automation_roi_bindings",
    "research_evidence_validate_automation_roi_snapshot",
    "research_evidence_assert_automation_roi_snapshot",
    "research_evidence_create_automation_roi_snapshot",
    "trg_rearoisb_prepare_insert",
    "trg_rearois_prepare_insert",
    "trg_rearois_no_mutation",
    "trg_rearoisb_no_mutation",
    "trg_rearois_complete",
    "idx_rearois_scope_sequence",
    "idx_rearoisb_binding",
    "research_evidence_automation_roi_input_snapshot_pkey",
    "uq_rearois_id_project_scope",
    "uq_rearois_scope_sequence",
    "uq_rearois_scope_request",
    "uq_rearois_supersedes_once",
    "fk_rearois_supersedes_same_scope",
    "ck_rearois_fixed_contract",
    "ck_rearois_status",
    "ck_rearois_json_shapes",
    "ck_rearois_nonblank",
    "ck_rearois_fingerprint",
    "pk_rearoisa",
    "ck_rearoisa_fixed_contract",
    "ck_rearoisa_sequence",
    "research_evidence_automation_roi_input_snapshot_binding_pkey",
    "uq_rearoisb_snapshot_role",
    "uq_rearoisb_snapshot_binding",
    "fk_rearoisb_snapshot_project",
    "ck_rearoisb_role",
    "ck_rearoisb_nonblank",
    "fk_rearois_project",
    "fk_rearoisa_project",
    "fk_rearoisb_binding_scope",
)


def _function_body(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(
        item
        for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and item.name == name
    )
    return ast.get_source_segment(source, node)


def _sql_statements(source: str) -> tuple[str, ...]:
    statements = []
    start = 0
    index = 0
    quote = None
    dollar_tag = None
    while index < len(source):
        if dollar_tag is not None:
            if source.startswith(dollar_tag, index):
                index += len(dollar_tag)
                dollar_tag = None
            else:
                index += 1
            continue
        character = source[index]
        if quote is not None:
            if character == quote:
                if index + 1 < len(source) and source[index + 1] == quote:
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if character in ("'", '"'):
            quote = character
            index += 1
            continue
        if character == "$":
            match = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$", source[index:])
            if match:
                dollar_tag = match.group(0)
                index += len(dollar_tag)
                continue
        if character == ";":
            statement = source[start:index].strip()
            if statement:
                statements.append(statement)
            start = index + 1
        index += 1
    trailing = source[start:].strip()
    if trailing:
        statements.append(trailing)
    return tuple(statements)


def test_v59_is_manual_additive_and_preserves_merged_v58_identity():
    assert SQL_RAW.count("BEGIN;") == 1
    assert SQL_RAW.count("COMMIT;") == 1
    assert "Apply manually after v58_research_evidence" in SQL_RAW
    assert SQL_RAW.count("CREATE TABLE IF NOT EXISTS") == 3
    assert "v59_research_evidence_automation_roi_input_snapshot.sql" in (
        PACKAGE_INIT + PG_HELPER
    )
    assert "v58_research_evidence_scenario_input_evaluation_foundation.sql" in (
        PACKAGE_INIT + PG_HELPER
    )
    assert "apply_v58_research_scenario_input_evaluation(conn)" in SCHEMA_TEST
    assert "apply_v59_research_automation_roi_use(conn)" in SCHEMA_TEST
    migration_calls = [
        "apply_v48(conn)",
        "apply_v51_research(conn)",
        "apply_v52_research(conn)",
        "apply_v53_research_intake(conn)",
        "apply_v54_research_review(conn)",
        "apply_v55_research_freshness(conn)",
        "apply_v56_research_claim_support(conn)",
        "apply_v57_research_binding(conn)",
        "apply_v58_research_scenario_input_evaluation(conn)",
        "apply_v59_research_automation_roi_use(conn)",
    ]
    assert [SCHEMA_TEST.index(call) for call in migration_calls] == sorted(
        SCHEMA_TEST.index(call) for call in migration_calls
    )
    for forbidden in ("DROP TABLE", "DROP SCHEMA", "CREATE VIEW"):
        assert forbidden not in SQL_RAW


def test_v59_constraint_deferrability_alias_avoids_reserved_keyword():
    bare_deferrable_output = re.compile(
        r"""
        \bexpected\s*\(
        [^)]*
        (?<![A-Za-z0-9_])deferrable(?![A-Za-z0-9_])
        [^)]*
        \)\s*(?:AS\s*\(|LEFT\s+JOIN)
        """,
        re.IGNORECASE | re.DOTALL | re.VERBOSE,
    )
    assert bare_deferrable_output.search(SQL_RAW) is None
    assert '"deferrable"' not in SQL_RAW
    assert SQL_RAW.count("constraint_info.condeferrable,") == 2
    assert (
        "t.tgdeferrable IS DISTINCT FROM expected.is_deferrable"
        in SQL_RAW
    )

    migration_calls = [
        "apply_v48(conn)",
        "apply_v51_research(conn)",
        "apply_v52_research(conn)",
        "apply_v53_research_intake(conn)",
        "apply_v54_research_review(conn)",
        "apply_v55_research_freshness(conn)",
        "apply_v56_research_claim_support(conn)",
        "apply_v57_research_binding(conn)",
        "apply_v58_research_scenario_input_evaluation(conn)",
        "apply_v59_research_automation_roi_use(conn)",
    ]
    positions = [SCHEMA_TEST.index(call) for call in migration_calls]
    assert positions == sorted(positions)


def test_source_owned_identifier_inventory_fits_postgresql_limit():
    encoded_lengths = {
        identifier: len(identifier.encode("utf-8"))
        for identifier in R1_6A_IDENTIFIERS
    }
    assert max(encoded_lengths.values()) <= 63
    assert (
        "automation_roi_input_snapshot_sequence_allocator"
        in R1_6A_IDENTIFIERS
    )
    for invalid in (
        "research_evidence_automation_roi_input_snapshot_sequence_allocator",
        "research_evidence_automation_roi_input_snapshot_sequence_alloca",
    ):
        assert invalid not in R1_6A_IDENTIFIERS
        assert invalid not in SQL_RAW
    assert SQL_RAW.count(
        "pg_catalog.octet_length(upstream_namespace.nspname::text) <= 63"
    ) == 6
    assert SQL_RAW.count(
        "pg_catalog.octet_length(project_relation.relname::text) <= 63"
    ) == 3
    assert SQL_RAW.count(
        "pg_catalog.octet_length(project_attribute.attname::text) <= 63"
    ) == 3


def test_upstream_schema_resolution_is_catalog_oid_bound_and_guarded():
    assert "current_schema()" not in SQL_RAW
    resolution_blocks = (
        "preflight",
        "temporary_upstream_acl",
        "upstream_foreign_keys",
        "triggers",
        "remove_temporary_upstream_acl",
        "upstream_runtime_acl",
    )
    for block_name in resolution_blocks:
        block = re.search(
            rf"DO \${block_name}\$(.*?)\${block_name}\$;",
            SQL_RAW,
            flags=re.DOTALL,
        )
        assert block is not None
        source = block.group(1)
        for required in (
            "FROM pg_catalog.pg_constraint constraint_info",
            "JOIN pg_catalog.pg_class binding_relation",
            "JOIN pg_catalog.pg_namespace binding_namespace",
            "JOIN pg_catalog.pg_class upstream_relation",
            "JOIN pg_catalog.pg_namespace upstream_namespace",
            "binding_relation.oid = constraint_info.conrelid",
            "upstream_relation.oid = constraint_info.confrelid",
            "constraint_info.conname = 'fk_recib_calculation_input_role'",
            "binding_relation.relname =",
            "'research_evidence_consumer_input_binding'",
            "upstream_relation.relname = 'approved_calculation_input'",
            "upstream_namespace.oid = binding_namespace.oid",
        ):
            assert required in source
        guard = (
            "IF v_upstream_schema_count <> 1 "
            "OR v_upstream_schema IS NULL THEN"
        )
        assert guard in source
        resolution_start = source.index(
            "SELECT count(*), min(upstream_namespace.nspname::text)"
        )
        resolution = source[resolution_start : source.index(guard)]
        assert "COALESCE" not in resolution
        assert source.index(guard) < source.index("format(")
        assert (
            "'v59 requires exactly one validated upstream schema'"
            in source
        )

    assert (
        "test_v59_applies_without_owner_ambient_upstream_schema"
        in SCHEMA_TEST
    )
    assert (
        "test_v59_reapply_rejects_missing_upstream_schema_anchor"
        in SCHEMA_TEST
    )
    assert (
        "test_v59_reapply_rejects_ambiguous_upstream_schema_anchor"
        in SCHEMA_TEST
    )


def test_upstream_project_fk_acl_is_oid_bound_narrow_and_preflighted():
    for block_name in (
        "preflight",
        "temporary_upstream_acl",
        "upstream_foreign_keys",
    ):
        block = re.search(
            rf"DO \${block_name}\$(.*?)\${block_name}\$;",
            SQL_RAW,
            flags=re.DOTALL,
        )
        assert block is not None
        source = block.group(1)
        for required in (
            "project_relation.oid = project_foreign_key.confrelid",
            "project_primary_key.conrelid = project_relation.oid",
            "project_primary_key.contype = 'p'",
            "project_primary_key.conkey = project_foreign_key.confkey",
            "project_attribute.attrelid = project_relation.oid",
            "project_attribute.attnum = project_foreign_key.confkey[1]",
            "project_foreign_key.conname = 'fk_recib_project'",
            "binding_namespace.nspname = v_upstream_schema",
            "project_namespace.oid = binding_namespace.oid",
        ):
            assert required in source
        project_guard = (
            "IF v_project_target_count <> 1\n"
            "       OR v_projects_relation IS NULL\n"
            "       OR v_project_id_column IS NULL THEN"
        )
        assert project_guard in source
        assert source.index(project_guard) < source.index("format(")

    temporary_acl = re.search(
        r"DO \$temporary_upstream_acl\$(.*?)"
        r"\$temporary_upstream_acl\$;",
        SQL_RAW,
        flags=re.DOTALL,
    ).group(1)
    assert re.search(
        r"GRANT USAGE ON SCHEMA %I\s+"
        r"TO workflow_research_evidence_owner",
        temporary_acl,
    )
    assert re.search(
        r"GRANT REFERENCES \(%I\) ON TABLE %I\.%I\s+"
        r"TO workflow_research_evidence_owner",
        temporary_acl,
    )
    assert (
        "v_project_id_column, v_upstream_schema, v_projects_relation"
        in temporary_acl
    )
    assert "GRANT REFERENCES ON TABLE %I.projects" not in SQL_RAW

    preflight = re.search(
        r"DO \$preflight\$(.*?)\$preflight\$;",
        SQL_RAW,
        flags=re.DOTALL,
    ).group(1)
    for required in (
        "IF v_tables = 3 THEN",
        "attribute.attacl",
        "acl.is_grantable",
        "'REFERENCES'::text",
        "'workflow_automation_roi_runtime'",
        "acl.grantee = 0",
    ):
        assert required in preflight
    assert preflight.index("IF v_tables = 3 THEN") < preflight.index(
        "attribute.attacl"
    )

    remove_acl = re.search(
        r"DO \$remove_temporary_upstream_acl\$(.*?)"
        r"\$remove_temporary_upstream_acl\$;",
        SQL_RAW,
        flags=re.DOTALL,
    ).group(1)
    assert "projects" not in remove_acl
    assert "research_evidence_consumer_input_binding" in remove_acl
    assert SQL_RAW.index("$temporary_upstream_acl$;") < SQL_RAW.index(
        "DO $upstream_foreign_keys$"
    )

    assert (
        "test_upstream_project_fk_acl_is_exact_and_column_scoped"
        in SCHEMA_TEST
    )
    for mutation in (
        "REVOKE REFERENCES (id) ON TABLE {upstream}.projects",
        "GRANT REFERENCES ON TABLE {upstream}.projects",
        "TO workflow_research_evidence_owner WITH GRANT OPTION",
        "GRANT SELECT ON TABLE {upstream}.projects",
    ):
        assert mutation in SCHEMA_TEST


def test_dedicated_acl_statements_are_bounded_by_owner_role_context():
    statements = _sql_statements(SQL_RAW)
    owner_sets = [
        index
        for index, statement in enumerate(statements)
        if statement.rstrip().endswith(
            "SET ROLE workflow_research_evidence_owner"
        )
    ]
    owner_resets = [
        index
        for index, statement in enumerate(statements)
        if statement == "RESET ROLE"
    ]
    migration_set = statements.index("SET ROLE workflow_migration_owner")
    assert len(owner_sets) == 2
    assert len(owner_resets) == 2
    validation_set, mutation_set = owner_sets
    validation_reset, mutation_reset = owner_resets
    assert (
        validation_set
        < validation_reset
        < mutation_set
        < mutation_reset
        < migration_set
    )
    validation_window = "\n".join(
        statements[validation_set + 1 : validation_reset]
    )
    assert "$owner_read_validation$" in validation_window
    for forbidden in (
        "CREATE ",
        "ALTER ",
        "DROP ",
        "GRANT ",
        "REVOKE ",
        "TRUNCATE ",
        "INSERT ",
        "UPDATE ",
        "DELETE ",
    ):
        assert forbidden not in validation_window

    dedicated_acl = tuple(
        index
        for index, statement in enumerate(statements)
        if "research_evidence_automation_roi" in statement
        and statement.lstrip().startswith(
            (
                "GRANT ",
                "REVOKE ",
                "ALTER DEFAULT PRIVILEGES ",
            )
        )
    )
    assert dedicated_acl
    assert mutation_set < min(dedicated_acl)
    assert max(dedicated_acl) < mutation_reset
    for index in dedicated_acl:
        statement = statements[index]
        assert "TO workflow_migration_owner" not in statement
        assert "FROM workflow_migration_owner" not in statement

    temporary_upstream_acl = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("DO $temporary_upstream_acl$")
    )
    upstream_runtime_acl = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("DO $upstream_runtime_acl$")
    )
    remove_temporary_acl = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("DO $remove_temporary_upstream_acl$")
    )
    assert temporary_upstream_acl < upstream_runtime_acl < mutation_set
    assert migration_set < remove_temporary_acl

    preflight = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("DO $preflight$")
    )
    assert preflight < temporary_upstream_acl
    assert (
        "test_v59_applies_from_canonical_migration_login"
        in SCHEMA_TEST
    )
    assert (
        "test_migration_owner_without_set_role_cannot_change_dedicated_acl"
        in SCHEMA_TEST
    )
    assert (
        "test_v59_reapply_preserves_dedicated_acl_drift_after_rejection"
        in SCHEMA_TEST
    )


def test_canonical_roles_and_owner_contract_are_explicit():
    for role in (
        "workflow_migration_owner",
        "workflow_research_evidence_owner",
        "workflow_automation_roi_runtime",
    ):
        assert role in SQL_RAW
        assert role in SCHEMA_TEST
    assert "SET ROLE workflow_research_evidence_owner" in SQL_RAW
    assert "SET LOCAL ROLE" not in SQL_RAW
    assert SQL_RAW.count("OWNER TO workflow_research_evidence_owner") == 9
    assert "divergent table owners" in SQL_RAW
    assert "owner_role.rolname" in SQL_RAW
    assert "divergent functions" in SQL_RAW
    assert "p.proowner" in SQL_RAW
    assert "p.prosecdef" in SQL_RAW
    assert "CREATE SCHEMA IF NOT EXISTS research_evidence_automation_roi" in SQL_RAW
    assert "namespace.nspowner" in SQL_RAW
    assert "exact canonical role-membership graph" in SQL_RAW
    for attribute in (
        "NOT rolsuper",
        "NOT rolcreaterole",
        "NOT rolcreatedb",
        "NOT rolreplication",
        "NOT rolbypassrls",
    ):
        assert SQL_RAW.count(attribute) == 3
    assert "WITH RECURSIVE reachable(role_oid)" in SQL_RAW
    assert "runtime role-membership escalation paths" in SQL_RAW
    assert "attname IN ('inherit_option', 'set_option')" in SQL_RAW
    assert "EXECUTE $membership_options$" in SQL_RAW
    assert "NOT membership.inherit_option" in SQL_RAW
    assert "membership.set_option" in SQL_RAW
    assert "non-inherited, SET-enabled deployment membership" in SQL_RAW
    for drift_attribute in (
        "INHERIT",
        "SUPERUSER",
        "CREATEROLE",
        "CREATEDB",
        "REPLICATION",
        "BYPASSRLS",
    ):
        assert f'("{drift_attribute}",' in SCHEMA_TEST
    for membership_options in (
        "WITH ADMIN TRUE, INHERIT FALSE, SET TRUE",
        "WITH ADMIN FALSE, INHERIT TRUE, SET TRUE",
        "WITH ADMIN FALSE, INHERIT FALSE, SET FALSE",
    ):
        assert membership_options in SCHEMA_TEST


def test_security_definer_paths_are_fixed_and_trusted():
    assert "SET search_path FROM CURRENT" not in SQL_RAW
    exact_path = "pg_catalog, research_evidence_automation_roi, pg_temp"
    configured_paths = re.findall(
        r"CREATE OR REPLACE FUNCTION[\s\S]*?"
        r"SET search_path = ([^\n]+)\n"
        r"AS \$function_body\$",
        SQL_RAW,
    )
    assert configured_paths == [exact_path] * 6
    for configured_path in configured_paths:
        assert [part.strip() for part in configured_path.split(",")] == [
            "pg_catalog",
            "research_evidence_automation_roi",
            "pg_temp",
        ]
        assert "$user" not in configured_path
        assert "public" not in configured_path.lower()
    assert "p.proconfig IS DISTINCT FROM" in SQL_RAW
    assert "ARRAY['search_path=' || expected.search_path]::text[]" in SQL_RAW
    assert "REVOKE ALL ON SCHEMA research_evidence_automation_roi" in SQL_RAW
    assert "workflow_automation_roi_runtime" in SQL_RAW
    assert "current_schema() || '.research_evidence_automation_roi" not in SQL_RAW


def test_function_catalog_reads_are_explicitly_pg_catalog_qualified():
    definitions = re.findall(
        r"CREATE OR REPLACE FUNCTION\s+"
        r"research_evidence_automation_roi\.\s*"
        r"([a-z0-9_]+)\s*\([^)]*\)[\s\S]*?"
        r"AS \$function_body\$(.*?)\$function_body\$;",
        SQL_RAW,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert len(definitions) == 6
    unqualified_catalog_name = re.compile(
        r"(?<!pg_catalog\.)\bpg_(?!(?:catalog|temp)\b)[a-z0-9_]*\b",
        flags=re.IGNORECASE,
    )
    assert not unqualified_catalog_name.search(SQL_RAW)
    for name, body in definitions:
        assert not unqualified_catalog_name.search(body), name
    for qualified_catalog in (
        "pg_catalog.pg_constraint",
        "pg_catalog.pg_class",
        "pg_catalog.pg_inherits",
        "pg_catalog.pg_namespace",
        "pg_catalog.pg_roles",
    ):
        assert qualified_catalog in SQL_RAW
    for metadata_function in (
        "acldefault",
        "aclexplode",
        "current_schema",
        "has_function_privilege",
        "has_schema_privilege",
        "has_table_privilege",
        "oidvectortypes",
        "to_regclass",
        "to_regnamespace",
        "to_regprocedure",
    ):
        unqualified_call = re.compile(
            rf"(?<!pg_catalog\.)\b{metadata_function}\s*\(",
            flags=re.IGNORECASE,
        )
        assert not unqualified_call.search(SQL_RAW), metadata_function
    for ordinary_builtin in (
        "::text",
        " p_project_id uuid",
        "format(",
        "clock_timestamp()",
    ):
        assert ordinary_builtin in SQL_RAW


def test_function_bodies_schema_qualify_all_r1_6a_objects():
    bodies = re.findall(
        r"AS \$function_body\$(.*?)\$function_body\$;",
        SQL_RAW,
        flags=re.DOTALL,
    )
    assert len(bodies) == 6
    object_names = (
        "research_evidence_automation_roi_input_snapshot",
        "research_evidence_automation_roi_input_snapshot_binding",
        "automation_roi_input_snapshot_sequence_allocator",
        "research_evidence_evaluate_automation_roi_bindings",
        "research_evidence_validate_automation_roi_snapshot",
    )
    for body in bodies:
        normalized = re.sub(r"\s+", " ", body)
        normalized = normalized.replace(".' '", ".")
        normalized = re.sub(r"\.\s+", ".", normalized)
        for object_name in object_names:
            assert not re.search(
                rf"(?<!\.)\b{object_name}\b",
                normalized,
            ), object_name


def test_function_implementation_hashes_are_source_owned_and_preflighted():
    definitions = dict(
        re.findall(
            r"CREATE OR REPLACE FUNCTION\s+"
            r"research_evidence_automation_roi\.\s*"
            r"([a-z0-9_]+)\s*\([^)]*\)[\s\S]*?"
            r"AS \$function_body\$(.*?)\$function_body\$;",
            SQL_RAW,
            flags=re.IGNORECASE | re.DOTALL,
        )
    )
    assert len(definitions) == 6
    implementation_contract = SQL_RAW.split(
        "SELECT string_agg(expected.name", 1
    )[1].split(
        "IF v_missing IS NOT NULL THEN", 1
    )[0]
    for name, body in definitions.items():
        source_sha256 = hashlib.sha256(body.encode("utf-8")).hexdigest()
        assert implementation_contract.count(source_sha256) == 1
        contract_row = implementation_contract.split(
            f"'{name}'", 1
        )[1].split(
            "\n            )", 1
        )[0]
        assert source_sha256 in contract_row
    for required_field in (
        "p.prosrc",
        "p.probin",
        "pg_catalog.oidvectortypes(p.proargtypes)",
        "language.lanname",
        "pg_get_function_result(p.oid)",
        "p.prosecdef",
        "p.proconfig",
        "p.proowner",
        "p.prokind",
        "p.provolatile",
        "p.proisstrict",
        "p.proleakproof",
        "p.proparallel",
        "p.procost",
        "p.prorows",
        "'prosupport'",
        "p.proretset",
        "p.provariadic",
        "p.pronargdefaults",
        "'prosqlbody'",
    ):
        assert required_field in implementation_contract
    first_replacement = SQL_RAW.index("CREATE OR REPLACE FUNCTION")
    for required_field in (
        "p.prosrc",
        "p.probin",
        "p.prokind",
        "p.provolatile",
        "p.proisstrict",
        "p.proleakproof",
        "p.proparallel",
        "p.procost",
        "p.prorows",
        "'prosupport'",
        "p.proretset",
        "p.provariadic",
        "p.pronargdefaults",
        "'prosqlbody'",
    ):
        assert SQL_RAW.index(required_field) < first_replacement


def test_relation_state_contract_is_source_owned_and_preflighted():
    relation_contract = SQL_RAW.split(
        "WITH expected(\n"
        "                table_name, relation_kind, persistence,",
        1,
    )[1].split(
        "RAISE EXCEPTION\n"
        "                'v59 contract violation: divergent relation state'",
        1,
    )[0]
    for field in (
        "relation.relkind",
        "relation.relpersistence",
        "relation.relrowsecurity",
        "relation.relforcerowsecurity",
        "relation.reloptions",
        "relation.relreplident",
        "relation.relispartition",
        "inheritance.inhrelid",
        "inheritance.inhparent",
    ):
        assert field in relation_contract
    assert relation_contract.count("'r'::\"char\", 'p'::\"char\"") == 3
    assert relation_contract.count("'d'::\"char\", false") == 3
    assert relation_contract.count("0::bigint, 0::bigint") == 3
    assert SQL_RAW.index("relation.relpersistence") < SQL_RAW.index(
        "CREATE OR REPLACE FUNCTION"
    )


def test_runtime_acl_is_entry_function_only_and_public_is_closed():
    assert re.search(
        r"REVOKE ALL ON TABLE[\s\S]+FROM PUBLIC, "
        r"workflow_automation_roi_runtime",
        SQL_RAW,
    )
    assert re.search(
        r"REVOKE ALL ON FUNCTION[\s\S]+FROM PUBLIC, "
        r"workflow_automation_roi_runtime",
        SQL_RAW,
    )
    runtime_grants = re.findall(
        r"GRANT\s+EXECUTE\s+ON\s+FUNCTION\s+"
        r"research_evidence_automation_roi\.\s*"
        r"research_evidence_create_automation_roi_snapshot\s*"
        r"\(\s*uuid\s*,\s*text\s*,\s*uuid\[\]\s*,\s*text\s*,"
        r"\s*timestamptz\s*,\s*text\s*\)\s*"
        r"TO\s+workflow_automation_roi_runtime\s*;",
        SQL_RAW,
        flags=re.IGNORECASE,
    )
    assert len(runtime_grants) == 1
    all_runtime_execute_grants = re.findall(
        r"GRANT\s+EXECUTE\s+ON\s+FUNCTION[^;]*?"
        r"TO\s+workflow_automation_roi_runtime\s*;",
        SQL_RAW,
        flags=re.IGNORECASE,
    )
    assert all_runtime_execute_grants == runtime_grants
    assert "GRANT SELECT ON TABLE" in SQL_RAW
    assert "research_evidence_automation_roi_input_snapshot_binding" in SQL_RAW
    assert "runtime ACL drift" in SQL_RAW
    assert "privilege drift" in SQL_RAW
    assert "REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC" in SQL_RAW
    assert "default_acl.defaclobjtype AS object_type" in SQL_RAW
    assert "'f'::\"char\"" in SQL_RAW


def test_upstream_acl_contract_expands_public_and_function_defaults():
    default_validation = SQL_RAW.split(
        "WITH expected(\n                default_role", 1
    )[1].split("trusted-schema default ACL drift", 1)[0]
    assert "'GLOBAL'::text" in default_validation
    assert "'f'::\"char\"" in default_validation
    assert "'EXECUTE'::text" in default_validation
    assert "pg_catalog.aclexplode(" in default_validation
    assert "default_acl.defaclacl" in default_validation
    assert "pg_catalog.acldefault" not in default_validation
    assert "SELECT * FROM expected EXCEPT SELECT * FROM actual" in default_validation
    assert "SELECT * FROM actual EXCEPT SELECT * FROM expected" in default_validation
    assert (
        "ALTER DEFAULT PRIVILEGES FOR ROLE "
        "workflow_research_evidence_owner\n"
        "    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
    ) in SQL_RAW
    assert (
        "ALTER DEFAULT PRIVILEGES FOR ROLE "
        "workflow_research_evidence_owner\n"
        "    IN SCHEMA research_evidence_automation_roi\n"
        "    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
    ) not in SQL_RAW
    assert "acldefault('f', function_info.proowner)" in SQL_RAW
    assert re.search(
        r"REVOKE\s+ALL\s+ON\s+FUNCTION\s+%I\.slicea_reject_mutation\(\)"
        r"\s+FROM\s+PUBLIC",
        SQL_RAW,
        flags=re.IGNORECASE,
    )
    assert re.search(
        r"REVOKE\s+EXECUTE\s+ON\s+FUNCTION\s+%I\.slicea_reject_mutation\(\)"
        r"\s+FROM\s+workflow_research_evidence_owner",
        SQL_RAW,
        flags=re.IGNORECASE,
    )
    for mutation in (
        "GRANT USAGE ON SCHEMA {upstream} TO PUBLIC",
        "research_evidence_consumer_input_binding\n        TO PUBLIC",
        "approved_calculation_input\n        TO PUBLIC",
        "research_evidence_consumer_input_binding_sequence_allocator\n"
        "        TO PUBLIC",
        "slicea_reject_mutation()\n        TO PUBLIC",
        "REVOKE USAGE ON SCHEMA {upstream}",
        "GRANT EXECUTE ON FUNCTION {upstream}.slicea_reject_mutation()",
    ):
        assert mutation in SCHEMA_TEST


def test_complete_index_inventory_has_no_precomparison_exclusions():
    index_contract = SQL_RAW.split(
        "WITH expected_base(\n"
        "                index_name, table_name, is_primary, is_unique, "
        "key_definitions",
        1,
    )[1].split(
        "RAISE EXCEPTION 'v59 contract violation: divergent indexes'",
        1,
    )[0]
    for field in (
        "access_method.amname::text",
        "index_info.indisprimary",
        "index_info.indisunique",
        "index_info.indisexclusion",
        "index_info.indisvalid",
        "index_info.indisready",
        "index_info.indislive",
        "index_info.indimmediate",
        "index_info.indnullsnotdistinct",
        "index_info.indisclustered",
        "index_info.indisreplident",
        "index_info.indnkeyatts",
        "index_info.indnatts",
        "index_info.indpred",
        "index_info.indexprs",
        "pg_get_indexdef",
    ):
        assert field in index_contract
    for excluded_filter in (
        "AND access_method.amname",
        "AND index_info.indisvalid",
        "AND index_info.indisready",
        "AND index_info.indislive",
        "AND NOT index_info.indisunique",
        "AND NOT index_info.indisprimary",
        "AND NOT index_info.indisexclusion",
        "AND index_info.indpred IS NULL",
        "AND index_info.indexprs IS NULL",
    ):
        assert excluded_filter not in index_contract
    for mutation in (
        "idx_rearois_unexpected_partial",
        "idx_rearois_unexpected_expression",
        "idx_rearois_unexpected_hash",
        "idx_rearois_unexpected_unique",
        "project_id, input_role",
    ):
        assert mutation in SCHEMA_TEST


def test_tgattr_mutation_uses_bounded_test_only_helper_execute():
    drift_test = _function_body(
        SCHEMA_TEST,
        "test_v59_reapply_rejects_trigger_update_scope_tgattr_drift",
    )
    grant = "GRANT EXECUTE ON FUNCTION {helper} TO {FUNCTION_OWNER}"
    revoke = "REVOKE EXECUTE ON FUNCTION {helper} FROM {FUNCTION_OWNER}"
    reapply = "pg.apply_v59_research_automation_roi_use(conn)"
    assert "conn.execute(\"RESET ROLE\")" in drift_test
    assert grant in drift_test
    assert revoke in drift_test
    assert "finally:" in drift_test
    assert "has_function_privilege" in drift_test
    assert drift_test.index(grant) < drift_test.index(revoke)
    assert drift_test.index(revoke) < drift_test.index(reapply)


def test_pg_temp_shadow_regression_is_bounded_and_cleans_up():
    shadow_test = _function_body(
        SCHEMA_TEST,
        "test_runtime_entry_ignores_session_catalog_shadows",
    )
    for required in (
        "has_database_privilege",
        "CREATE TEMP TABLE pg_constraint",
        "CREATE TEMP TABLE pg_class",
        "CREATE TEMP TABLE pg_namespace",
        "GRANT SELECT ON TABLE pg_temp.",
        "record_automation_roi_input_snapshot",
        "REVOKE SELECT ON TABLE pg_temp.",
        "DROP TABLE IF EXISTS pg_temp.",
        "to_regclass('pg_temp.pg_constraint')",
        "permanent_shadows == 0",
    ):
        assert required in shadow_test
    assert "GRANT ALL" not in shadow_test
    assert "GRANT CREATE" not in shadow_test


def test_function_body_drift_regression_preserves_metadata_and_rejects():
    drift_test = _function_body(
        SCHEMA_TEST,
        "test_v59_reapply_rejects_function_body_drift_before_replacement",
    )
    for preserved_contract in (
        "SET ROLE {FUNCTION_OWNER}",
        "RETURNS uuid",
        "LANGUAGE plpgsql",
        "SECURITY DEFINER",
        (
            "SET search_path = pg_catalog, "
            "research_evidence_automation_roi, pg_temp"
        ),
    ):
        assert preserved_contract in drift_test
    assert "intentional_body_drift" in drift_test
    assert "pg.apply_v59_research_automation_roi_use(conn)" in drift_test
    assert 'match="divergent functions"' in drift_test
    assert drift_test.index(
        "pg.apply_v59_research_automation_roi_use(conn)"
    ) < drift_test.index(
        'assert "intentional_body_drift" in installed_source'
    )


def test_runtime_has_no_direct_write_helper_or_allocator_contract():
    runtime_test = _function_body(
        SCHEMA_TEST, "test_runtime_can_use_only_controlled_entry_function"
    )
    assert "research_evidence_create_automation_roi_snapshot" not in runtime_test
    assert "record_automation_roi_input_snapshot" in runtime_test
    assert "research_evidence_validate_automation_roi_snapshot" in runtime_test
    assert (
        "automation_roi_input_snapshot_sequence_allocator"
        in runtime_test
    )
    assert "with pytest.raises(Exception)" in runtime_test
    assert "INSERT,UPDATE,DELETE,TRUNCATE" in SCHEMA_TEST
    assert "has_schema_privilege" in SCHEMA_TEST
    assert "'CREATE'" in SCHEMA_TEST


def test_allocator_append_only_and_reapply_guards_are_preserved():
    assert "FOR UPDATE OF allocator" in SQL_RAW
    assert "slicea_reject_mutation()" in SQL_RAW
    assert "DEFERRABLE INITIALLY DEFERRED" in SQL_RAW
    assert "ENABLE ALWAYS TRIGGER trg_rearois_prepare_insert" in SQL_RAW
    assert "ENABLE ALWAYS TRIGGER trg_rearoisb_prepare_insert" in SQL_RAW
    assert "allocator integrity drift" in SQL_RAW
    assert "predecessor chain drift" in SQL_RAW
    assert "snapshot completeness drift" in SQL_RAW
    assert "t.tgattr <> ''::int2vector" in SQL_RAW
    assert "divergent keys" in SQL_RAW
    assert "divergent foreign keys" in SQL_RAW
    assert "divergent checks" in SQL_RAW
    assert "divergent indexes" in SQL_RAW
    assert "attribute.atttypmod" in SQL_RAW
    assert "if conn.autocommit" in SERVICE
    assert "SAVEPOINT research_evidence_automation_roi_snapshot_write" in SERVICE
    assert ".commit(" not in PACKAGE
    assert ".rollback(" not in PACKAGE
    assert ".close(" not in PACKAGE


def test_column_count_validator_is_literal_oid_bound_and_privilege_independent():
    validator = SQL_RAW.split(
        "WITH expected(table_name, expected_count) AS (", 1
    )[1].split(
        "RAISE EXCEPTION 'v59 contract violation: divergent column count'", 1
    )[0]
    assert "19::bigint" in validator
    assert "8::bigint" in validator
    assert "4::bigint" in validator
    assert "LEFT JOIN pg_catalog.pg_namespace" in validator
    assert "LEFT JOIN pg_catalog.pg_class" in validator
    assert "LEFT JOIN pg_catalog.pg_attribute" in validator
    assert "relation.relnamespace = namespace.oid" in validator
    assert "attribute.attrelid = relation.oid" in validator
    assert "attribute.attnum > 0" in validator
    assert "NOT attribute.attisdropped" in validator
    assert "relation_count <> 1" in validator
    assert "actual_count <> expected_count" in validator
    assert "information_schema.columns" not in validator
    assert "to_regclass" not in validator
    assert "::regclass" not in validator
    assert (
        "def test_v59_reapply_rejects_extra_active_column_before_repair"
        in SCHEMA_TEST
    )


def test_check_validator_is_oid_bound_and_uses_canonical_literal_inventory():
    preflight = re.search(
        r"DO \$preflight\$(.*?)\$preflight\$;",
        SQL_RAW,
        flags=re.DOTALL,
    ).group(1)
    validator = preflight.split(
        "'ck_rearois_fixed_contract'::text", 1
    )[1].split(
        "SELECT string_agg(expected.name, ', ' ORDER BY expected.name)",
        1,
    )[0]
    assert "pg_catalog.pg_constraint" in validator
    assert "pg_catalog.pg_class" in validator
    assert "pg_catalog.pg_namespace" in validator
    assert "relation.oid = constraint_info.conrelid" in validator
    assert "namespace.oid = relation.relnamespace" in validator
    assert "constraint_info.contype = 'c'" in validator
    assert "constraint_info.convalidated" in validator
    assert "pg_catalog.pg_get_expr(" in validator
    assert "information_schema" not in validator
    assert "to_regclass" not in validator
    assert "::regclass" not in validator
    assert "current_schema()" not in validator
    assert (
        '"qualified":{"freshness_status":["stale"]'
        in validator
    )
    assert (
        "def test_v59_reapply_rejects_check_drift_without_repair"
        in SCHEMA_TEST
    )


def test_runtime_acl_validator_uses_exact_cardinality_oid_overloads():
    validator = SQL_RAW.split(
        "WITH resolved AS (", 1
    )[1].split(
        "RAISE EXCEPTION 'v59 contract violation: runtime ACL drift'", 1
    )[0]
    for catalog in (
        "pg_catalog.pg_namespace",
        "pg_catalog.pg_roles",
        "pg_catalog.pg_class",
        "pg_catalog.pg_proc",
    ):
        assert catalog in validator
    for expected_count in (
        "schema_count <> 1",
        "runtime_role_count <> 1",
        "snapshot_count <> 1",
        "binding_count <> 1",
        "allocator_count <> 1",
        "entry_count <> 1",
        "helper_count <> 1",
    ):
        assert expected_count in validator
    assert "runtime_role_oid, schema_oid" in validator
    assert "runtime_role_oid, snapshot_oid" in validator
    assert "runtime_role_oid, binding_oid" in validator
    assert "runtime_role_oid,\n                    allocator_oid" in validator
    assert "runtime_role_oid, entry_oid" in validator
    assert "runtime_role_oid, helper_oid" in validator
    assert "format('%I.%I'" not in validator
    assert "to_regclass" not in validator
    assert "::regclass" not in validator
    assert "information_schema" not in validator
    assert "current_schema()" not in validator
    assert (
        "def test_v59_reapply_uses_oid_acl_probes_without_migration_schema_usage"
        in SCHEMA_TEST
    )


def test_reapply_authority_map_has_one_read_only_owner_validation_window():
    assert "-- M: canonical roles/membership" in SQL_RAW
    assert "-- O: allocator/history" in SQL_RAW
    assert "-- X: none." in SQL_RAW
    owner_start = SQL_RAW.index(
        "SET ROLE workflow_research_evidence_owner;\n\n"
        "DO $owner_read_validation$"
    )
    owner_end = SQL_RAW.index(
        "$owner_read_validation$;\n\nRESET ROLE;",
        owner_start,
    )
    window = SQL_RAW[owner_start:owner_end]
    for required in (
        "allocator integrity drift",
        "predecessor chain drift",
        "snapshot completeness drift",
        "session_user <> 'workflow_migration_owner'",
        "current_user <> 'workflow_research_evidence_owner'",
    ):
        assert required in window
    for forbidden in (
        "\nCREATE ",
        "\nALTER ",
        "\nDROP ",
        "\nGRANT ",
        "\nREVOKE ",
        "\nINSERT ",
        "\nUPDATE ",
        "\nDELETE ",
        "\nTRUNCATE ",
        "CREATE OR REPLACE",
    ):
        assert forbidden not in window
    restored = SQL_RAW.index(
        "DO $migration_context_restored$",
        owner_end,
    )
    first_repair = SQL_RAW.index(
        "CREATE SCHEMA IF NOT EXISTS research_evidence_automation_roi"
    )
    assert owner_start < owner_end < restored < first_repair
    restoration = SQL_RAW[restored:first_repair]
    assert "session_user <> 'workflow_migration_owner'" in restoration
    assert "current_user <> 'workflow_migration_owner'" in restoration
    assert (
        "def test_v59_owner_read_validation_context_and_restoration"
        in SCHEMA_TEST
    )


def test_trusted_schema_reapply_uses_literal_normalized_acl_inventory():
    validation = SQL_RAW.split(
        "IF pg_catalog.to_regnamespace(v_object_schema) IS NOT NULL THEN", 1
    )[1].split("IF v_tables = 3 THEN", 1)[0]
    assert "SELECT namespace.nspacl::text" in validation
    assert "INTO v_schema_acl_raw" in validation
    assert "pg_catalog.aclexplode(" in validation
    assert "pg_catalog.acldefault('n', namespace.nspowner)" in validation
    assert "grantee, grantor, privilege_type, is_grantable" in validation
    assert "'workflow_research_evidence_owner'::text" in validation
    assert "'workflow_automation_roi_runtime'::text" in validation
    assert "SELECT * FROM expected EXCEPT SELECT * FROM actual" in validation
    assert "SELECT * FROM actual EXCEPT SELECT * FROM expected" in validation
    assert "nspacl =" not in validation
    assert "nspacl <>" not in validation
    assert "trusted-schema normalized ACL drift" in validation
    first_acl_repair = min(
        SQL_RAW.index(statement)
        for statement in (
            "REVOKE ALL ON SCHEMA research_evidence_automation_roi",
            "GRANT USAGE ON SCHEMA research_evidence_automation_roi",
        )
    )
    assert SQL_RAW.index("trusted-schema normalized ACL drift") < first_acl_repair


def test_repository_write_is_controlled_function_only():
    repository_write = REPOSITORY.split(
        "def insert_snapshot", 1
    )[1].split("_SNAPSHOT_SELECT", 1)[0]
    assert "research_evidence_create_automation_roi_snapshot" in repository_write
    assert (
        "research_evidence_automation_roi."
        in repository_write
    )
    assert "INSERT INTO" not in repository_write
    assert "evaluate_binding_set" not in repository_write
    assert "policy_evaluation_status" not in repository_write
    assert "binding_record_ids" in MODELS
    assert "p_binding_record_ids uuid[]" in SQL_RAW
    assert "selected.id = ANY($2)" in SQL_RAW
    assert "USING p_project_id, p_binding_record_ids" in SQL_RAW


def test_schema_tests_cover_security_and_reapply_drift_matrix():
    for required_test in (
        "test_canonical_role_graph_and_dedicated_schema_acl",
        "test_v59_reapply_rejects_structural_and_function_drift",
        "test_v59_reapply_rejects_schema_owner_and_create_acl_drift",
        "test_v59_reapply_rejects_normalized_schema_acl_drift_without_repair",
        "test_v59_reapply_rejects_default_acl_drift_without_repair",
        "test_v59_reapply_rejects_extra_active_column_before_repair",
        "test_v59_reapply_rejects_check_drift_without_repair",
        "test_v59_reapply_uses_oid_acl_probes_without_migration_schema_usage",
        "test_v59_owner_read_validation_context_and_restoration",
        "test_v59_reapply_rejects_runtime_membership_escalation",
        "test_v59_reapply_rejects_role_attribute_drift",
        "test_v59_reapply_rejects_membership_option_drift",
        "test_v59_reapply_rejects_trigger_update_scope_tgattr_drift",
        "test_runtime_entry_ignores_session_catalog_shadows",
        "test_v59_reapply_rejects_function_body_drift_before_replacement",
        "test_v59_reapply_rejects_function_semantic_attribute_drift",
        "test_v59_reapply_rejects_function_signature_metadata_drift",
        "test_v59_reapply_rejects_relation_state_drift",
        "test_v59_reapply_rejects_missing_or_extra_upstream_acl",
        "test_v59_reapply_rejects_complete_index_inventory_drift",
        "test_v59_inventory_ignores_same_named_objects_in_other_schema",
    ):
        assert f"def {required_test}" in SCHEMA_TEST
    for required_contract in (
        "DROP NOT NULL",
        "SET DEFAULT 'forged'",
        "DROP CONSTRAINT uq_rearois_scope_request",
        "DROP CONSTRAINT fk_rearoisb_binding_scope",
        "DROP CONSTRAINT ck_rearois_status",
        "DROP INDEX research_evidence_automation_roi.idx_rearoisb_binding",
        "SECURITY INVOKER",
        "UPDATE OF evaluated_by",
        "GRANT CREATE ON SCHEMA research_evidence_automation_roi",
        "GRANT DELETE ON {upstream}",
        "REVOKE UPDATE ON {upstream}",
        "GRANT EXECUTE ON FUNCTION {upstream}.slicea_reject_mutation()",
        "GRANT USAGE ON SCHEMA {upstream} TO PUBLIC",
        "idx_rearois_unexpected_partial",
        "idx_rearois_unexpected_expression",
        "idx_rearois_unexpected_hash",
        "idx_rearois_unexpected_unique",
        ") IMMUTABLE",
        ") STRICT",
        ") LEAKPROOF",
        ") PARALLEL SAFE",
        ") COST 7",
        ") ROWS 7",
        ") SUPPORT pg_catalog.text_starts_with_support",
        "proretset = NOT function_info.proretset",
        "provariadic = 'pg_catalog.text'::pg_catalog.regtype::pg_catalog.oid",
        "pronargdefaults = function_info.pronargdefaults + 1",
        "ENABLE ROW LEVEL SECURITY",
        "FORCE ROW LEVEL SECURITY",
        "REPLICA IDENTITY FULL",
        "SET (fillfactor = 70)",
        "SET UNLOGGED",
        "unexpected_rearois_inheritance_child",
    ):
        assert required_contract in SCHEMA_TEST


def test_v59_harness_requires_one_oid_validated_upstream_before_apply():
    assertion = _function_body(
        PG_HELPER, "assert_single_v59_upstream_schema"
    )
    apply_v59 = _function_body(
        PG_HELPER, "apply_v59_research_automation_roi_use"
    )
    migration_connection = _function_body(
        PG_HELPER, "v59_migration_connection"
    )
    for catalog in (
        "pg_catalog.pg_constraint",
        "pg_catalog.pg_class",
        "pg_catalog.pg_namespace",
    ):
        assert catalog in assertion
    assert "constraint_info.confrelid" in assertion
    assert "upstream_namespace.oid = binding_namespace.oid" in assertion
    assert "len(rows) != 1" in assertion
    assert "pytest harness found" in assertion
    assert (
        "assert_single_v59_upstream_schema(conn)"
        in apply_v59
    )
    assert "TEST_EVIDENCE_MIGRATION_PG_DSN" in PG_HELPER
    assert "psycopg.connect(migration_dsn)" in migration_connection
    assert "session_user = 'workflow_migration_owner'" in migration_connection
    assert "current_user = 'workflow_migration_owner'" in migration_connection
    assert "identity != (True, True)" in migration_connection
    assert "SET ROLE workflow_migration_owner" not in migration_connection
    assert apply_v59.index(
        "assert_single_v59_upstream_schema(conn)"
    ) < apply_v59.index(
        "v59_migration_connection(upstream_schema)"
    )
    assert "_run_script(migration, V59_RESEARCH_AUTOMATION_ROI_USE_SQL)" in (
        apply_v59
    )
    assert "_run_script(conn, V59_RESEARCH_AUTOMATION_ROI_USE_SQL)" not in (
        apply_v59
    )
    assert apply_v59.index(
        "_run_script(migration, V59_RESEARCH_AUTOMATION_ROI_USE_SQL)"
    ) < apply_v59.index(
        'conn.execute("RESET ROLE")'
    )


def test_r1_6a_functional_harness_uses_genuine_runtime_login():
    runtime_connection = _function_body(PG_HELPER, "runtime_connection")
    assert "TEST_EVIDENCE_RUNTIME_PG_DSN" in PG_HELPER
    assert "psycopg.connect(runtime_dsn)" in runtime_connection
    assert (
        "session_user = 'workflow_automation_roi_runtime'"
        in runtime_connection
    )
    assert (
        "current_user = 'workflow_automation_roi_runtime'"
        in runtime_connection
    )
    assert "identity != (True, True)" in runtime_connection
    assert "SET ROLE workflow_automation_roi_runtime" not in runtime_connection
    assert "def runtime(schema_v59):" in SCHEMA_TEST
    assert "_role_connection" not in SCHEMA_TEST
    assert re.search(
        r"record_automation_roi_input_snapshot\(\s*conn\b",
        SCHEMA_TEST,
    ) is None
    assert "SET ROLE workflow_automation_roi_runtime" not in SCHEMA_TEST


def test_no_prohibited_product_surface_or_r1_7_runtime_coupling():
    import_lines = "\n".join(
        line
        for line in PACKAGE.splitlines()
        if line.startswith("import ") or line.startswith("from ")
    )
    for forbidden in (
        "automation_roi.service",
        "automation_roi.repository",
        "automation_roi.calculator",
        "api",
        "scenario_input_evaluation",
        "orchestrator",
        "export",
        "retrieval",
        "monitor",
        "prompt",
    ):
        assert forbidden not in import_lines
    for forbidden in (
        "compute_automation_roi",
        "insert_calculation_result",
        "render_citation",
        "generate_report",
    ):
        assert forbidden not in PACKAGE
