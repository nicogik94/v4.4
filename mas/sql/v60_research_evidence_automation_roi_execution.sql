-- v60 Deterministic Automation ROI Execution From Approved Snapshots (R1.6B)
-- Apply after v59_research_evidence_automation_roi_input_snapshot.sql.
-- This migration is additive. It neither reads from nor changes legacy v48/v49
-- calculation-result persistence.

BEGIN;

DO $preflight$
DECLARE
    v_schema_oid oid;
    v_owner_oid oid;
    v_migration_oid oid;
    v_database_oid oid;
    v_result_tables integer;
    v_result_functions integer;
    v_result_oid oid;
    v_execute_oid oid;
    v_prepare_oid oid;
BEGIN
    IF session_user <> 'workflow_migration_owner'
       OR current_user <> 'workflow_migration_owner' THEN
        RAISE EXCEPTION 'v60 must use a genuine workflow_migration_owner login'
            USING ERRCODE = '42501';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles
        WHERE rolname = 'workflow_migration_owner'
          AND rolcanlogin AND NOT rolinherit AND NOT rolsuper
          AND NOT rolcreaterole AND NOT rolcreatedb
          AND NOT rolreplication AND NOT rolbypassrls
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles
        WHERE rolname = 'workflow_research_evidence_owner'
          AND NOT rolcanlogin AND NOT rolinherit AND NOT rolsuper
          AND NOT rolcreaterole AND NOT rolcreatedb
          AND NOT rolreplication AND NOT rolbypassrls
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles
        WHERE rolname = 'workflow_automation_roi_runtime'
          AND rolcanlogin AND NOT rolinherit AND NOT rolsuper
          AND NOT rolcreaterole AND NOT rolcreatedb
          AND NOT rolreplication AND NOT rolbypassrls
    ) THEN
        RAISE EXCEPTION 'v60 requires the exact canonical role attributes'
            USING ERRCODE = '42501';
    END IF;

    SELECT namespace.oid, owner_role.oid, migration_role.oid, database_info.oid
    INTO v_schema_oid, v_owner_oid, v_migration_oid, v_database_oid
    FROM pg_catalog.pg_namespace namespace
    JOIN pg_catalog.pg_roles owner_role
      ON owner_role.rolname = 'workflow_research_evidence_owner'
     AND namespace.nspowner = owner_role.oid
    JOIN pg_catalog.pg_roles migration_role
      ON migration_role.rolname = 'workflow_migration_owner'
    JOIN pg_catalog.pg_database database_info
      ON database_info.datname = pg_catalog.current_database()
    WHERE namespace.nspname = 'research_evidence_automation_roi';
    IF v_schema_oid IS NULL THEN
        RAISE EXCEPTION
            'v60 requires the preprovisioned, canonically owned dedicated schema'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
    IF pg_catalog.has_database_privilege(
        v_migration_oid, v_database_oid, 'CREATE'
    ) THEN
        RAISE EXCEPTION 'v60 rejects migration-owner database CREATE'
            USING ERRCODE = '42501';
    END IF;
    IF pg_catalog.has_schema_privilege(
        'workflow_migration_owner',
        'research_evidence_automation_roi',
        'USAGE'
    ) OR pg_catalog.has_schema_privilege(
        'workflow_migration_owner',
        'research_evidence_automation_roi',
        'CREATE'
    ) THEN
        RAISE EXCEPTION 'v60 rejects migration-owner dedicated-schema authority'
            USING ERRCODE = '42501';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_namespace namespace
        CROSS JOIN LATERAL pg_catalog.aclexplode(
            COALESCE(
                namespace.nspacl,
                pg_catalog.acldefault('n', namespace.nspowner)
            )
        ) acl
        WHERE namespace.oid = v_schema_oid
          AND acl.grantee = 0
    ) OR NOT pg_catalog.has_schema_privilege(
        'workflow_automation_roi_runtime',
        'research_evidence_automation_roi',
        'USAGE'
    ) OR pg_catalog.has_schema_privilege(
        'workflow_automation_roi_runtime',
        'research_evidence_automation_roi',
        'CREATE'
    ) THEN
        RAISE EXCEPTION 'v60 contract violation: dedicated-schema ACL drift'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
    IF (
        SELECT count(*)
        FROM pg_catalog.pg_namespace namespace
        CROSS JOIN LATERAL pg_catalog.aclexplode(
            COALESCE(
                namespace.nspacl,
                pg_catalog.acldefault('n', namespace.nspowner)
            )
        ) acl
        LEFT JOIN pg_catalog.pg_roles grantee
          ON grantee.oid = acl.grantee
        WHERE namespace.oid = v_schema_oid
          AND (
              (grantee.rolname = 'workflow_research_evidence_owner'
               AND acl.privilege_type IN ('CREATE', 'USAGE'))
              OR
              (grantee.rolname = 'workflow_automation_roi_runtime'
               AND acl.privilege_type = 'USAGE')
          )
    ) <> 3 OR EXISTS (
        SELECT 1
        FROM pg_catalog.pg_namespace namespace
        CROSS JOIN LATERAL pg_catalog.aclexplode(
            COALESCE(
                namespace.nspacl,
                pg_catalog.acldefault('n', namespace.nspowner)
            )
        ) acl
        LEFT JOIN pg_catalog.pg_roles grantee
          ON grantee.oid = acl.grantee
        WHERE namespace.oid = v_schema_oid
          AND NOT (
              (grantee.rolname = 'workflow_research_evidence_owner'
               AND acl.privilege_type IN ('CREATE', 'USAGE')
               AND NOT acl.is_grantable)
              OR
              (grantee.rolname = 'workflow_automation_roi_runtime'
               AND acl.privilege_type = 'USAGE'
               AND NOT acl.is_grantable)
          )
    ) THEN
        RAISE EXCEPTION 'v60 contract violation: normalized schema ACL drift'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF (
        SELECT count(*)
        FROM pg_catalog.pg_auth_members membership
        JOIN pg_catalog.pg_roles granted ON granted.oid = membership.roleid
        JOIN pg_catalog.pg_roles member ON member.oid = membership.member
        WHERE granted.rolname = 'workflow_research_evidence_owner'
          AND member.rolname = 'workflow_migration_owner'
          AND NOT membership.admin_option
    ) <> 1 OR EXISTS (
        WITH RECURSIVE reachable(role_oid) AS (
            SELECT membership.roleid
            FROM pg_catalog.pg_auth_members membership
            JOIN pg_catalog.pg_roles runtime
              ON runtime.oid = membership.member
            WHERE runtime.rolname = 'workflow_automation_roi_runtime'
            UNION
            SELECT membership.roleid
            FROM pg_catalog.pg_auth_members membership
            JOIN reachable ON reachable.role_oid = membership.member
        )
        SELECT 1
        FROM reachable
        JOIN pg_catalog.pg_roles role_info
          ON role_info.oid = reachable.role_oid
        WHERE role_info.rolname IN (
            'workflow_migration_owner', 'workflow_research_evidence_owner'
        )
    ) THEN
        RAISE EXCEPTION 'v60 rejects canonical role-membership drift'
            USING ERRCODE = '42501';
    END IF;

    IF (
        SELECT count(*)
        FROM pg_catalog.pg_class relation
        WHERE relation.relnamespace = v_schema_oid
          AND relation.relkind = 'r'
          AND relation.relname IN (
              'research_evidence_automation_roi_input_snapshot',
              'research_evidence_automation_roi_input_snapshot_binding',
              'automation_roi_input_snapshot_sequence_allocator'
          )
    ) <> 3 OR NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_proc function_info
        WHERE function_info.pronamespace = v_schema_oid
          AND function_info.proname =
              'research_evidence_create_automation_roi_snapshot'
          AND pg_catalog.pg_get_function_identity_arguments(
              function_info.oid
          ) = 'p_project_id uuid, p_binding_set_id text, p_binding_record_ids uuid[], p_request_id text, p_freshness_as_of timestamp with time zone, p_evaluated_by text'
    ) THEN
        RAISE EXCEPTION 'v60 requires the complete immutable v59 foundation'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT count(*), min(relation.oid)
    INTO v_result_tables, v_result_oid
    FROM pg_catalog.pg_class relation
    WHERE relation.relnamespace = v_schema_oid
      AND relation.relkind = 'r'
      AND relation.relname = 'automation_roi_calculation_result';
    SELECT count(*) INTO v_result_functions
    FROM pg_catalog.pg_proc function_info
    WHERE function_info.pronamespace = v_schema_oid
      AND function_info.proname IN (
          'research_evidence_prepare_automation_roi_result',
          'research_evidence_execute_automation_roi'
      );
    IF (v_result_tables = 0 AND v_result_functions <> 0)
       OR (v_result_tables = 1 AND v_result_functions <> 2) THEN
        RAISE EXCEPTION 'v60 contract violation: partial execution state'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF v_result_tables = 0 AND (
        EXISTS (
            SELECT 1 FROM pg_catalog.pg_class relation
            WHERE relation.relnamespace = v_schema_oid
              AND relation.relkind IN ('r', 'v', 'm', 'S', 'f', 'p')
              AND relation.relname NOT IN (
                  'research_evidence_automation_roi_input_snapshot',
                  'research_evidence_automation_roi_input_snapshot_binding',
                  'automation_roi_input_snapshot_sequence_allocator'
              )
        ) OR EXISTS (
            SELECT 1 FROM pg_catalog.pg_proc function_info
            WHERE function_info.pronamespace = v_schema_oid
              AND function_info.proname NOT IN (
                  'research_evidence_prepare_automation_roi_snapshot',
                  'research_evidence_prepare_automation_roi_snapshot_binding',
                  'research_evidence_evaluate_automation_roi_bindings',
                  'research_evidence_validate_automation_roi_snapshot',
                  'research_evidence_assert_automation_roi_snapshot',
                  'research_evidence_create_automation_roi_snapshot'
              )
        )
    ) THEN
        RAISE EXCEPTION
            'v60 contract violation: conflicting first-apply object inventory'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF v_result_tables = 1 THEN
        IF EXISTS (
            SELECT 1
            FROM pg_catalog.pg_class relation
            WHERE relation.relnamespace = v_schema_oid
              AND relation.relkind IN ('r', 'v', 'm', 'S', 'f', 'p')
              AND relation.relname NOT IN (
                  'research_evidence_automation_roi_input_snapshot',
                  'research_evidence_automation_roi_input_snapshot_binding',
                  'automation_roi_input_snapshot_sequence_allocator',
                  'automation_roi_calculation_result'
              )
        ) OR EXISTS (
            SELECT 1 FROM pg_catalog.pg_proc function_info
            WHERE function_info.pronamespace = v_schema_oid
              AND function_info.proname NOT IN (
                  'research_evidence_prepare_automation_roi_snapshot',
                  'research_evidence_prepare_automation_roi_snapshot_binding',
                  'research_evidence_evaluate_automation_roi_bindings',
                  'research_evidence_validate_automation_roi_snapshot',
                  'research_evidence_assert_automation_roi_snapshot',
                  'research_evidence_create_automation_roi_snapshot',
                  'research_evidence_prepare_automation_roi_result',
                  'research_evidence_execute_automation_roi'
              )
        ) THEN
            RAISE EXCEPTION 'v60 contract violation: dedicated-object inventory drift'
                USING ERRCODE = 'invalid_schema_definition';
        END IF;

        IF (
            SELECT count(*)
            FROM pg_catalog.pg_attribute attribute
            WHERE attribute.attrelid = v_result_oid
              AND attribute.attnum > 0 AND NOT attribute.attisdropped
        ) <> 26 OR (
            SELECT relation.relowner
            FROM pg_catalog.pg_class relation
            WHERE relation.oid = v_result_oid
        ) <> v_owner_oid THEN
            RAISE EXCEPTION 'v60 contract violation: result structural or owner drift'
                USING ERRCODE = 'invalid_schema_definition';
        END IF;
        IF (
            SELECT count(*)
            FROM pg_catalog.pg_constraint constraint_info
            WHERE constraint_info.conrelid = v_result_oid
              AND constraint_info.conname IN (
                  'automation_roi_calculation_result_pkey',
                  'uq_rearoicr_project_idempotency',
                  'uq_rearoicr_project_operation',
                  'fk_rearoicr_snapshot_scope',
                  'ck_rearoicr_fixed_contract',
                  'ck_rearoicr_status_shape',
                  'ck_rearoicr_json_shapes',
                  'ck_rearoicr_nonblank',
                  'ck_rearoicr_digests'
              )
        ) <> 9 THEN
            RAISE EXCEPTION 'v60 contract violation: result constraint drift'
                USING ERRCODE = 'invalid_schema_definition';
        END IF;
        IF NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_trigger trigger_info
            WHERE trigger_info.tgrelid = v_result_oid
              AND trigger_info.tgname = 'trg_rearoicr_prepare_insert'
              AND trigger_info.tgenabled = 'A'
              AND trigger_info.tgtype = 7
              AND NOT trigger_info.tgisinternal
        ) OR NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_trigger trigger_info
            WHERE trigger_info.tgrelid = v_result_oid
              AND trigger_info.tgname = 'trg_rearoicr_no_mutation'
              AND trigger_info.tgenabled = 'O'
              AND trigger_info.tgtype = 27
              AND NOT trigger_info.tgisinternal
        ) THEN
            RAISE EXCEPTION 'v60 contract violation: result trigger drift'
                USING ERRCODE = 'invalid_schema_definition';
        END IF;
        IF pg_catalog.has_table_privilege(
            'workflow_automation_roi_runtime',
            v_result_oid,
            'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'
        ) OR NOT pg_catalog.has_table_privilege(
            'workflow_automation_roi_runtime',
            v_result_oid,
            'SELECT'
        ) OR EXISTS (
            SELECT 1
            FROM pg_catalog.pg_class relation
            CROSS JOIN LATERAL pg_catalog.aclexplode(
                COALESCE(
                    relation.relacl,
                    pg_catalog.acldefault('r', relation.relowner)
                )
            ) acl
            WHERE relation.oid = v_result_oid
              AND acl.grantee = 0
        ) THEN
            RAISE EXCEPTION 'v60 contract violation: result ACL drift'
                USING ERRCODE = 'invalid_schema_definition';
        END IF;
        SELECT function_info.oid INTO v_execute_oid
        FROM pg_catalog.pg_proc function_info
        WHERE function_info.pronamespace = v_schema_oid
          AND function_info.proname =
              'research_evidence_execute_automation_roi'
          AND pg_catalog.pg_get_function_identity_arguments(
              function_info.oid
          ) = 'p_project_id uuid, p_input_snapshot_id uuid, p_idempotency_key text, p_requested_by text';
        SELECT function_info.oid INTO v_prepare_oid
        FROM pg_catalog.pg_proc function_info
        WHERE function_info.pronamespace = v_schema_oid
          AND function_info.proname =
              'research_evidence_prepare_automation_roi_result'
          AND pg_catalog.pg_get_function_identity_arguments(
              function_info.oid
          ) = '';
        IF v_execute_oid IS NULL OR v_prepare_oid IS NULL OR NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_proc function_info
            JOIN pg_catalog.pg_roles owner_role
              ON owner_role.oid = function_info.proowner
            WHERE function_info.oid = v_execute_oid
              AND owner_role.rolname = 'workflow_research_evidence_owner'
              AND function_info.prosecdef
              AND function_info.proconfig = ARRAY[
                  'search_path=pg_catalog, research_evidence_automation_roi, pg_temp'
              ]::text[]
              AND pg_catalog.encode(
                  pg_catalog.sha256(
                      pg_catalog.convert_to(function_info.prosrc, 'UTF8')
                  ),
                  'hex'
              ) =
                  '4cd1cecbf6a5380373a1ef8b141d61275690db165aac30db3613b47d0f759735'
        ) THEN
            RAISE EXCEPTION 'v60 contract violation: controlled-function drift'
                USING ERRCODE = 'invalid_schema_definition';
        END IF;
        IF NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_proc function_info
            WHERE function_info.oid = v_prepare_oid
              AND function_info.proowner = v_owner_oid
              AND NOT function_info.prosecdef
              AND function_info.proconfig = ARRAY[
                  'search_path=pg_catalog, research_evidence_automation_roi, pg_temp'
              ]::text[]
              AND pg_catalog.encode(
                  pg_catalog.sha256(
                      pg_catalog.convert_to(function_info.prosrc, 'UTF8')
                  ),
                  'hex'
              ) =
                  'a257d5731a1dd83e115653829fe761a9e0e6d7cc051f42e38d40e071394adb1e'
        ) OR NOT pg_catalog.has_function_privilege(
            'workflow_automation_roi_runtime', v_execute_oid, 'EXECUTE'
        ) OR pg_catalog.has_function_privilege(
            'workflow_automation_roi_runtime', v_prepare_oid, 'EXECUTE'
        ) OR EXISTS (
            SELECT 1
            FROM pg_catalog.pg_proc function_info
            CROSS JOIN LATERAL pg_catalog.aclexplode(
                COALESCE(
                    function_info.proacl,
                    pg_catalog.acldefault('f', function_info.proowner)
                )
            ) acl
            WHERE function_info.oid IN (v_execute_oid, v_prepare_oid)
              AND acl.grantee = 0
        ) THEN
            RAISE EXCEPTION 'v60 contract violation: function owner or ACL drift'
                USING ERRCODE = 'invalid_schema_definition';
        END IF;
    END IF;

    IF (
        SELECT count(*)
        FROM pg_catalog.pg_default_acl default_acl
        JOIN pg_catalog.pg_roles owner_role
          ON owner_role.oid = default_acl.defaclrole
        CROSS JOIN LATERAL pg_catalog.aclexplode(default_acl.defaclacl) acl
        LEFT JOIN pg_catalog.pg_roles grantee
          ON grantee.oid = acl.grantee
        WHERE owner_role.rolname = 'workflow_research_evidence_owner'
          AND default_acl.defaclnamespace = 0
          AND default_acl.defaclobjtype = 'f'
          AND grantee.rolname = 'workflow_research_evidence_owner'
          AND acl.privilege_type = 'EXECUTE'
          AND NOT acl.is_grantable
    ) <> 1 OR EXISTS (
        SELECT 1
        FROM pg_catalog.pg_default_acl default_acl
        JOIN pg_catalog.pg_roles owner_role
          ON owner_role.oid = default_acl.defaclrole
        CROSS JOIN LATERAL pg_catalog.aclexplode(default_acl.defaclacl) acl
        LEFT JOIN pg_catalog.pg_roles grantee
          ON grantee.oid = acl.grantee
        WHERE owner_role.rolname = 'workflow_research_evidence_owner'
          AND NOT (
              default_acl.defaclnamespace = 0
              AND default_acl.defaclobjtype = 'f'
              AND grantee.rolname = 'workflow_research_evidence_owner'
              AND acl.privilege_type = 'EXECUTE'
              AND NOT acl.is_grantable
          )
    ) THEN
        RAISE EXCEPTION 'v60 contract violation: default ACL drift'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
END;
$preflight$;

-- The owner needs this only while installing the append-only trigger.
DO $temporary_trigger_acl$
DECLARE
    v_upstream_schema text;
BEGIN
    SELECT upstream_namespace.nspname INTO STRICT v_upstream_schema
    FROM pg_catalog.pg_constraint constraint_info
    JOIN pg_catalog.pg_class child_relation
      ON child_relation.oid = constraint_info.conrelid
    JOIN pg_catalog.pg_namespace child_namespace
      ON child_namespace.oid = child_relation.relnamespace
    JOIN pg_catalog.pg_class upstream_relation
      ON upstream_relation.oid = constraint_info.confrelid
    JOIN pg_catalog.pg_namespace upstream_namespace
      ON upstream_namespace.oid = upstream_relation.relnamespace
    WHERE child_namespace.nspname = 'research_evidence_automation_roi'
      AND child_relation.relname =
          'research_evidence_automation_roi_input_snapshot_binding'
      AND constraint_info.conname = 'fk_rearoisb_binding_scope';
    EXECUTE format(
        'GRANT EXECUTE ON FUNCTION %I.slicea_reject_mutation() '
        'TO workflow_research_evidence_owner',
        v_upstream_schema
    );
END;
$temporary_trigger_acl$;

SET ROLE workflow_research_evidence_owner;

CREATE TABLE IF NOT EXISTS research_evidence_automation_roi.
automation_roi_calculation_result (
    id UUID NOT NULL,
    project_id UUID NOT NULL,
    input_snapshot_id UUID NOT NULL,
    consumer_contract TEXT NOT NULL,
    binding_set_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    operation_digest TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL,
    formula_identifier TEXT NOT NULL,
    formula_version TEXT NOT NULL,
    formula_fingerprint TEXT NOT NULL,
    assumption_set_version TEXT NOT NULL,
    assumptions_json JSONB NOT NULL,
    input_manifest_json JSONB NOT NULL,
    input_digest TEXT NOT NULL,
    provenance_fingerprint TEXT NOT NULL,
    output_units_json JSONB NOT NULL,
    status TEXT NOT NULL,
    currency_code TEXT,
    annual_labor_savings NUMERIC,
    annual_net_benefit NUMERIC,
    first_year_net_benefit NUMERIC,
    first_year_roi_percent NUMERIC,
    roi_percent_status TEXT NOT NULL,
    diagnostics_json JSONB NOT NULL,
    CONSTRAINT automation_roi_calculation_result_pkey PRIMARY KEY (id),
    CONSTRAINT uq_rearoicr_project_idempotency
        UNIQUE (project_id, idempotency_key),
    CONSTRAINT uq_rearoicr_project_operation
        UNIQUE (project_id, operation_digest),
    CONSTRAINT fk_rearoicr_snapshot_scope
        FOREIGN KEY (
            input_snapshot_id, project_id, consumer_contract, binding_set_id
        )
        REFERENCES research_evidence_automation_roi.
            research_evidence_automation_roi_input_snapshot(
            id, project_id, consumer_contract, binding_set_id
        ) ON DELETE RESTRICT,
    CONSTRAINT ck_rearoicr_fixed_contract CHECK (
        consumer_contract = 'deterministic_calculation'
        AND formula_identifier = 'automation_roi'
        AND formula_version = 'automation_roi.v1'
        AND formula_fingerprint =
            '260ea8cf45b4d1e58fbb290838bd6da044b9b5ca6eba8874cbbb4ef8596b58f7'
        AND assumption_set_version = 'automation_roi.assumptions.v1'
        AND assumptions_json =
            '{"annualization":"periods_per_year","currency_conversion":"none","execution_authority":"immutable_v59_snapshot_stored_policy_status","first_year_cost_treatment":"annual_recurring_plus_one_time_implementation","rounding":"none"}'::jsonb
        AND output_units_json =
            '{"annual_labor_savings":"currency_per_year","annual_net_benefit":"currency_per_year","first_year_net_benefit":"currency_first_year","first_year_roi_percent":"percent"}'::jsonb
    ),
    CONSTRAINT ck_rearoicr_status_shape CHECK (
        (status = 'blocked'
         AND roi_percent_status = 'blocked'
         AND currency_code IS NULL
         AND annual_labor_savings IS NULL
         AND annual_net_benefit IS NULL
         AND first_year_net_benefit IS NULL
         AND first_year_roi_percent IS NULL)
        OR
        (status = 'not_applicable'
         AND roi_percent_status = 'not_applicable'
         AND currency_code IS NOT NULL
         AND annual_labor_savings IS NOT NULL
         AND annual_net_benefit IS NOT NULL
         AND first_year_net_benefit IS NOT NULL
         AND first_year_roi_percent IS NULL)
        OR
        (status = 'valid'
         AND roi_percent_status = 'computed'
         AND currency_code IS NOT NULL
         AND annual_labor_savings IS NOT NULL
         AND annual_net_benefit IS NOT NULL
         AND first_year_net_benefit IS NOT NULL
         AND first_year_roi_percent IS NOT NULL)
    ),
    CONSTRAINT ck_rearoicr_json_shapes CHECK (
        jsonb_typeof(assumptions_json) = 'object'
        AND jsonb_typeof(input_manifest_json) = 'object'
        AND input_manifest_json ?& ARRAY[
            'baseline_hours_per_period',
            'post_automation_hours_per_period',
            'fully_loaded_rate_per_hour',
            'periods_per_year',
            'annual_recurring_cost',
            'one_time_implementation_cost'
        ]
        AND jsonb_typeof(output_units_json) = 'object'
        AND jsonb_typeof(diagnostics_json) = 'object'
    ),
    CONSTRAINT ck_rearoicr_nonblank CHECK (
        binding_set_id !~ '^[[:space:]]*$'
        AND idempotency_key !~ '^[[:space:]]*$'
        AND requested_by !~ '^[[:space:]]*$'
    ),
    CONSTRAINT ck_rearoicr_digests CHECK (
        operation_digest ~ '^[0-9a-f]{64}$'
        AND formula_fingerprint ~ '^[0-9a-f]{64}$'
        AND input_digest ~ '^[0-9a-f]{64}$'
        AND provenance_fingerprint ~ '^[0-9a-f]{64}$'
    )
);

CREATE INDEX IF NOT EXISTS idx_rearoicr_snapshot
    ON research_evidence_automation_roi.automation_roi_calculation_result(
        input_snapshot_id, project_id
    );

CREATE OR REPLACE FUNCTION research_evidence_automation_roi.
research_evidence_prepare_automation_roi_result()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, research_evidence_automation_roi, pg_temp
AS $function_body$
DECLARE
    v_owner oid;
BEGIN
    SELECT relation.relowner INTO v_owner
    FROM pg_catalog.pg_class relation
    WHERE relation.oid = TG_RELID;
    IF (SELECT role_info.oid FROM pg_catalog.pg_roles role_info
        WHERE role_info.rolname = current_user) <> v_owner THEN
        RAISE EXCEPTION
            'Automation ROI results require the controlled execution function'
            USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END;
$function_body$;

CREATE OR REPLACE FUNCTION research_evidence_automation_roi.
research_evidence_execute_automation_roi(
    p_project_id uuid,
    p_input_snapshot_id uuid,
    p_idempotency_key text,
    p_requested_by text
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, research_evidence_automation_roi, pg_temp
AS $function_body$
DECLARE
    v_snapshot record;
    v_upstream_schema text;
    v_manifest jsonb;
    v_provenance jsonb;
    v_operation_digest text;
    v_input_digest text;
    v_provenance_fingerprint text;
    v_existing record;
    v_result_id uuid;
    v_count integer;
    v_baseline numeric;
    v_post numeric;
    v_rate numeric;
    v_periods numeric;
    v_recurring numeric;
    v_one_time numeric;
    v_currency text;
    v_annual_labor numeric;
    v_annual_net numeric;
    v_first_year_net numeric;
    v_roi numeric;
    v_status text;
    v_roi_status text;
    v_diagnostics jsonb := '{}'::jsonb;
BEGIN
    IF p_project_id IS NULL OR p_input_snapshot_id IS NULL
       OR p_idempotency_key IS NULL OR btrim(p_idempotency_key) = ''
       OR p_requested_by IS NULL OR btrim(p_requested_by) = '' THEN
        RAISE EXCEPTION 'execution request identity must be complete'
            USING ERRCODE = '22023';
    END IF;

    SELECT snapshot.* INTO v_snapshot
    FROM research_evidence_automation_roi.
        research_evidence_automation_roi_input_snapshot snapshot
    WHERE snapshot.id = p_input_snapshot_id
      AND snapshot.project_id = p_project_id
      AND snapshot.consumer_contract = 'deterministic_calculation';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'approved Automation ROI snapshot is not in project scope'
            USING ERRCODE = '22023';
    END IF;
    IF v_snapshot.completeness_status <> 'complete'
       OR v_snapshot.policy_evaluation_status <> 'satisfies' THEN
        RAISE EXCEPTION 'snapshot stored policy status does not authorize execution'
            USING ERRCODE = '22023';
    END IF;

    SELECT count(*)::integer INTO v_count
    FROM research_evidence_automation_roi.
        research_evidence_automation_roi_input_snapshot_binding child
    WHERE child.snapshot_id = v_snapshot.id
      AND child.project_id = v_snapshot.project_id
      AND child.consumer_contract = v_snapshot.consumer_contract
      AND child.binding_set_id = v_snapshot.binding_set_id;
    IF v_count <> 6 THEN
        RAISE EXCEPTION 'snapshot does not contain exactly six role bindings'
            USING ERRCODE = '22023';
    END IF;

    v_operation_digest := pg_catalog.encode(
        pg_catalog.sha256(
            pg_catalog.convert_to(
                p_project_id::text || chr(31) ||
                p_input_snapshot_id::text || chr(31) ||
                'automation_roi' || chr(31) ||
                'automation_roi.v1' || chr(31) ||
                '260ea8cf45b4d1e58fbb290838bd6da044b9b5ca6eba8874cbbb4ef8596b58f7',
                'UTF8'
            )
        ),
        'hex'
    );

    SELECT result.id, result.operation_digest INTO v_existing
    FROM research_evidence_automation_roi.automation_roi_calculation_result result
    WHERE result.project_id = p_project_id
      AND result.idempotency_key = btrim(p_idempotency_key);
    IF FOUND THEN
        IF v_existing.operation_digest = v_operation_digest THEN
            RETURN v_existing.id;
        END IF;
        RAISE EXCEPTION 'idempotency key identifies a different operation'
            USING ERRCODE = '23505',
                  CONSTRAINT = 'uq_rearoicr_project_idempotency';
    END IF;
    SELECT result.id, result.operation_digest INTO v_existing
    FROM research_evidence_automation_roi.automation_roi_calculation_result result
    WHERE result.project_id = p_project_id
      AND result.operation_digest = v_operation_digest;
    IF FOUND THEN
        RETURN v_existing.id;
    END IF;

    SELECT upstream_namespace.nspname INTO STRICT v_upstream_schema
    FROM pg_catalog.pg_constraint constraint_info
    JOIN pg_catalog.pg_class upstream_relation
      ON upstream_relation.oid = constraint_info.confrelid
    JOIN pg_catalog.pg_namespace upstream_namespace
      ON upstream_namespace.oid = upstream_relation.relnamespace
    WHERE constraint_info.conrelid =
        'research_evidence_automation_roi.'
        'research_evidence_automation_roi_input_snapshot_binding'::regclass
      AND constraint_info.conname = 'fk_rearoisb_binding_scope';

    EXECUTE format(
        $query$
        SELECT jsonb_object_agg(
                   child.input_role,
                   jsonb_build_object(
                       'input_role', child.input_role,
                       'numeric_value', input.resolved_numeric_value,
                       'unit', input.resolved_unit,
                       'period', input.resolved_period,
                       'currency_code', input.resolved_currency_code,
                       'time_unit', input.resolved_time_unit,
                       'binding_id', binding.id,
                       'approved_calculation_input_id', input.id,
                       'candidate_fact_revision_id',
                           input.candidate_fact_revision_id,
                       'approval_decision_id', input.approval_decision_id
                   )
               ),
               jsonb_object_agg(
                   child.input_role,
                   jsonb_build_object(
                       'binding_id', binding.id,
                       'approved_calculation_input_id', input.id,
                       'candidate_fact_revision_id',
                           input.candidate_fact_revision_id,
                       'approval_decision_id', input.approval_decision_id
                   )
               ),
               count(*)::integer
        FROM research_evidence_automation_roi.
            research_evidence_automation_roi_input_snapshot_binding child
        JOIN %I.research_evidence_consumer_input_binding binding
          ON binding.id = child.binding_record_id
         AND binding.project_id = child.project_id
         AND binding.consumer_contract = child.consumer_contract
         AND binding.binding_set_id = child.binding_set_id
         AND binding.input_key = child.input_role
        JOIN %I.approved_calculation_input input
          ON input.id = binding.approved_calculation_input_id
         AND input.project_id = binding.project_id
         AND input.input_role = binding.input_key
        WHERE child.snapshot_id = $1
          AND child.project_id = $2
        $query$,
        v_upstream_schema, v_upstream_schema
    ) INTO v_manifest, v_provenance, v_count
      USING p_input_snapshot_id, p_project_id;
    IF v_count <> 6 THEN
        RAISE EXCEPTION 'snapshot input resolution is incomplete'
            USING ERRCODE = '22023';
    END IF;

    v_input_digest := pg_catalog.encode(
        pg_catalog.sha256(pg_catalog.convert_to(v_manifest::text, 'UTF8')),
        'hex'
    );
    v_provenance_fingerprint := pg_catalog.encode(
        pg_catalog.sha256(pg_catalog.convert_to(v_provenance::text, 'UTF8')),
        'hex'
    );

    v_baseline := (v_manifest->'baseline_hours_per_period'
        ->>'numeric_value')::numeric;
    v_post := (v_manifest->'post_automation_hours_per_period'
        ->>'numeric_value')::numeric;
    v_rate := (v_manifest->'fully_loaded_rate_per_hour'
        ->>'numeric_value')::numeric;
    v_periods := (v_manifest->'periods_per_year'
        ->>'numeric_value')::numeric;
    v_recurring := (v_manifest->'annual_recurring_cost'
        ->>'numeric_value')::numeric;
    v_one_time := (v_manifest->'one_time_implementation_cost'
        ->>'numeric_value')::numeric;

    IF v_manifest->'baseline_hours_per_period'->>'time_unit' IS DISTINCT FROM
           'hours'
       OR v_manifest->'post_automation_hours_per_period'->>'time_unit'
           IS DISTINCT FROM 'hours'
       OR v_manifest->'fully_loaded_rate_per_hour'->>'unit'
           IS DISTINCT FROM 'per_hour' THEN
        v_status := 'blocked';
        v_roi_status := 'blocked';
        v_diagnostics := '{"unit_incompatibility":"unit_incompatibility"}';
    ELSIF nullif(
              v_manifest->'baseline_hours_per_period'->>'period', ''
          ) IS NULL
       OR (v_manifest->'baseline_hours_per_period'->>'period')
          IS DISTINCT FROM
          (v_manifest->'post_automation_hours_per_period'->>'period')
       OR v_periods < 1 OR v_periods <> trunc(v_periods) THEN
        v_status := 'blocked';
        v_roi_status := 'blocked';
        v_diagnostics := '{"period_incompatibility":"period_incompatibility"}';
    ELSIF nullif(
              v_manifest->'fully_loaded_rate_per_hour'->>'currency_code', ''
          ) IS NULL
       OR (v_manifest->'fully_loaded_rate_per_hour'->>'currency_code')
          IS DISTINCT FROM
          (v_manifest->'annual_recurring_cost'->>'currency_code')
       OR (v_manifest->'fully_loaded_rate_per_hour'->>'currency_code')
          IS DISTINCT FROM
          (v_manifest->'one_time_implementation_cost'->>'currency_code') THEN
        v_status := 'blocked';
        v_roi_status := 'blocked';
        v_diagnostics :=
            '{"currency_incompatibility":"currency_incompatibility"}';
    ELSE
        v_currency :=
            v_manifest->'fully_loaded_rate_per_hour'->>'currency_code';
        v_annual_labor := (v_baseline - v_post) * v_rate * v_periods;
        v_annual_net := v_annual_labor - v_recurring;
        v_first_year_net := v_annual_net - v_one_time;
        IF v_post > v_baseline THEN
            v_diagnostics := v_diagnostics || jsonb_build_object(
                'negative_hours_delta',
                'post_automation_hours_exceed_baseline'
            );
        END IF;
        IF v_one_time = 0 THEN
            v_status := 'not_applicable';
            v_roi_status := 'not_applicable';
            v_diagnostics := v_diagnostics || jsonb_build_object(
                'roi_percent', 'not_applicable_zero_implementation_cost'
            );
        ELSE
            v_status := 'valid';
            v_roi_status := 'computed';
            v_roi := v_first_year_net / v_one_time * 100;
        END IF;
    END IF;

    v_result_id := gen_random_uuid();
    INSERT INTO research_evidence_automation_roi.
        automation_roi_calculation_result (
            id, project_id, input_snapshot_id, consumer_contract,
            binding_set_id, idempotency_key, operation_digest, requested_by,
            computed_at, formula_identifier, formula_version,
            formula_fingerprint, assumption_set_version, assumptions_json,
            input_manifest_json, input_digest, provenance_fingerprint,
            output_units_json, status, currency_code, annual_labor_savings,
            annual_net_benefit, first_year_net_benefit,
            first_year_roi_percent, roi_percent_status, diagnostics_json
        )
    VALUES (
        v_result_id, p_project_id, p_input_snapshot_id,
        'deterministic_calculation', v_snapshot.binding_set_id,
        btrim(p_idempotency_key), v_operation_digest, btrim(p_requested_by),
        clock_timestamp(), 'automation_roi', 'automation_roi.v1',
        '260ea8cf45b4d1e58fbb290838bd6da044b9b5ca6eba8874cbbb4ef8596b58f7',
        'automation_roi.assumptions.v1',
        '{"annualization":"periods_per_year","currency_conversion":"none","execution_authority":"immutable_v59_snapshot_stored_policy_status","first_year_cost_treatment":"annual_recurring_plus_one_time_implementation","rounding":"none"}'::jsonb,
        v_manifest, v_input_digest, v_provenance_fingerprint,
        '{"annual_labor_savings":"currency_per_year","annual_net_benefit":"currency_per_year","first_year_net_benefit":"currency_first_year","first_year_roi_percent":"percent"}'::jsonb,
        v_status, v_currency, v_annual_labor, v_annual_net,
        v_first_year_net, v_roi, v_roi_status, v_diagnostics
    );
    RETURN v_result_id;
END;
$function_body$;

ALTER TABLE research_evidence_automation_roi.
    automation_roi_calculation_result
    OWNER TO workflow_research_evidence_owner;
ALTER FUNCTION research_evidence_automation_roi.
    research_evidence_prepare_automation_roi_result()
    OWNER TO workflow_research_evidence_owner;
ALTER FUNCTION research_evidence_automation_roi.
    research_evidence_execute_automation_roi(uuid, uuid, text, text)
    OWNER TO workflow_research_evidence_owner;

DO $triggers$
DECLARE
    v_upstream_schema text;
BEGIN
    SELECT upstream_namespace.nspname INTO STRICT v_upstream_schema
    FROM pg_catalog.pg_constraint constraint_info
    JOIN pg_catalog.pg_class upstream_relation
      ON upstream_relation.oid = constraint_info.confrelid
    JOIN pg_catalog.pg_namespace upstream_namespace
      ON upstream_namespace.oid = upstream_relation.relnamespace
    WHERE constraint_info.conrelid =
        'research_evidence_automation_roi.'
        'research_evidence_automation_roi_input_snapshot_binding'::regclass
      AND constraint_info.conname = 'fk_rearoisb_binding_scope';
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_trigger
        WHERE tgname = 'trg_rearoicr_prepare_insert'
          AND tgrelid =
              'research_evidence_automation_roi.'
              'automation_roi_calculation_result'::regclass
          AND NOT tgisinternal
    ) THEN
        CREATE TRIGGER trg_rearoicr_prepare_insert
            BEFORE INSERT ON research_evidence_automation_roi.
                automation_roi_calculation_result
            FOR EACH ROW EXECUTE FUNCTION research_evidence_automation_roi.
                research_evidence_prepare_automation_roi_result();
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_trigger
        WHERE tgname = 'trg_rearoicr_no_mutation'
          AND tgrelid =
              'research_evidence_automation_roi.'
              'automation_roi_calculation_result'::regclass
          AND NOT tgisinternal
    ) THEN
        EXECUTE format(
            'CREATE TRIGGER trg_rearoicr_no_mutation
             BEFORE UPDATE OR DELETE
             ON research_evidence_automation_roi.
                 automation_roi_calculation_result
             FOR EACH ROW EXECUTE FUNCTION %I.slicea_reject_mutation()',
            v_upstream_schema
        );
    END IF;
END;
$triggers$;

ALTER TABLE research_evidence_automation_roi.
    automation_roi_calculation_result
    ENABLE ALWAYS TRIGGER trg_rearoicr_prepare_insert;

REVOKE ALL ON TABLE research_evidence_automation_roi.
    automation_roi_calculation_result
    FROM PUBLIC, workflow_automation_roi_runtime;
REVOKE ALL ON FUNCTION
    research_evidence_automation_roi.
        research_evidence_prepare_automation_roi_result(),
    research_evidence_automation_roi.
        research_evidence_execute_automation_roi(uuid, uuid, text, text)
    FROM PUBLIC, workflow_automation_roi_runtime;
GRANT SELECT ON TABLE research_evidence_automation_roi.
    automation_roi_calculation_result
    TO workflow_automation_roi_runtime;
GRANT EXECUTE ON FUNCTION research_evidence_automation_roi.
    research_evidence_execute_automation_roi(uuid, uuid, text, text)
    TO workflow_automation_roi_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE workflow_research_evidence_owner
    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

RESET ROLE;
SET ROLE workflow_migration_owner;

DO $remove_temporary_trigger_acl$
DECLARE
    v_upstream_schema text;
BEGIN
    SELECT upstream_namespace.nspname INTO STRICT v_upstream_schema
    FROM pg_catalog.pg_constraint constraint_info
    JOIN pg_catalog.pg_class child_relation
      ON child_relation.oid = constraint_info.conrelid
    JOIN pg_catalog.pg_namespace child_namespace
      ON child_namespace.oid = child_relation.relnamespace
    JOIN pg_catalog.pg_class upstream_relation
      ON upstream_relation.oid = constraint_info.confrelid
    JOIN pg_catalog.pg_namespace upstream_namespace
      ON upstream_namespace.oid = upstream_relation.relnamespace
    WHERE child_namespace.nspname = 'research_evidence_automation_roi'
      AND child_relation.relname =
          'research_evidence_automation_roi_input_snapshot_binding'
      AND constraint_info.conname = 'fk_rearoisb_binding_scope';
    EXECUTE format(
        'REVOKE EXECUTE ON FUNCTION %I.slicea_reject_mutation() '
        'FROM workflow_research_evidence_owner',
        v_upstream_schema
    );
END;
$remove_temporary_trigger_acl$;

COMMIT;
