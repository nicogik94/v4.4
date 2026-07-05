-- v59 Research-Evidence Automation ROI Input Snapshot Foundation (R1.6A)
-- Additive provenance-only snapshots over explicit immutable v57 binding IDs.
--
-- Apply manually after v58_research_evidence_scenario_input_evaluation_foundation.sql.
-- Required provisioned roles:
--   workflow_migration_owner          LOGIN NOINHERIT, deployment only
--   workflow_research_evidence_owner  NOLOGIN NOINHERIT, object owner
--   workflow_automation_roi_runtime    LOGIN NOINHERIT, restricted caller
-- Required provisioned schema:
--   research_evidence_automation_roi owned by workflow_research_evidence_owner
--
-- Non-goals: no calculation execution or result linkage; no scenario, report,
-- API, UI, export, retrieval, workflow, prompt, or monitoring changes.

BEGIN;

DO $preflight$
DECLARE
    v_object_schema constant text := 'research_evidence_automation_roi';
    v_upstream_schema text;
    v_upstream_schema_count integer;
    v_projects_relation text;
    v_project_id_column text;
    v_project_target_count integer;
    v_tables integer;
    v_missing text;
    v_schema_acl_raw text;
    v_membership_options_supported boolean;
    v_membership_options_valid boolean;
    v_database_oid oid;
    v_migration_role_oid oid;
    v_function_owner_oid oid;
    v_object_schema_oid oid;
    v_object_schema_owner_oid oid;
BEGIN
    IF current_user <> 'workflow_migration_owner' THEN
        RAISE EXCEPTION
            'v59 must be applied as workflow_migration_owner'
            USING ERRCODE = '42501';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles
        WHERE rolname = 'workflow_migration_owner'
          AND rolcanlogin AND NOT rolinherit
          AND NOT rolsuper AND NOT rolcreaterole AND NOT rolcreatedb
          AND NOT rolreplication AND NOT rolbypassrls
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles
        WHERE rolname = 'workflow_research_evidence_owner'
          AND NOT rolcanlogin AND NOT rolinherit
          AND NOT rolsuper AND NOT rolcreaterole AND NOT rolcreatedb
          AND NOT rolreplication AND NOT rolbypassrls
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles
        WHERE rolname = 'workflow_automation_roi_runtime'
          AND rolcanlogin AND NOT rolinherit
          AND NOT rolsuper AND NOT rolcreaterole AND NOT rolcreatedb
          AND NOT rolreplication AND NOT rolbypassrls
    ) THEN
        RAISE EXCEPTION
            'v59 requires the canonical migration, function-owner, and runtime roles'
            USING ERRCODE = '42501';
    END IF;

    SELECT database_info.oid,
           migration_role.oid,
           function_owner.oid,
           object_schema.oid,
           object_schema.nspowner
    INTO v_database_oid,
         v_migration_role_oid,
         v_function_owner_oid,
         v_object_schema_oid,
         v_object_schema_owner_oid
    FROM pg_catalog.pg_database database_info
    JOIN pg_catalog.pg_roles migration_role
      ON migration_role.rolname = 'workflow_migration_owner'
    JOIN pg_catalog.pg_roles function_owner
      ON function_owner.rolname = 'workflow_research_evidence_owner'
    LEFT JOIN pg_catalog.pg_namespace object_schema
      ON object_schema.nspname = v_object_schema
    WHERE database_info.datname = pg_catalog.current_database();

    IF v_object_schema_oid IS NULL THEN
        RAISE EXCEPTION
            'v59 requires pre-provisioned dedicated schema %',
            v_object_schema
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
    IF v_object_schema_owner_oid IS DISTINCT FROM v_function_owner_oid THEN
        RAISE EXCEPTION
            'v59 contract violation: trusted-schema owner drift'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
    IF pg_catalog.has_database_privilege(
           v_migration_role_oid, v_database_oid, 'CREATE'
       ) THEN
        RAISE EXCEPTION
            'v59 rejects migration-owner database CREATE'
            USING ERRCODE = '42501';
    END IF;

    SELECT count(*) = 2 INTO v_membership_options_supported
    FROM pg_catalog.pg_attribute
    WHERE attrelid = 'pg_catalog.pg_auth_members'::regclass
      AND attname IN ('inherit_option', 'set_option')
      AND NOT attisdropped;
    IF v_membership_options_supported THEN
        EXECUTE $membership_options$
            SELECT count(*) = 1
            FROM pg_catalog.pg_auth_members membership
            JOIN pg_catalog.pg_roles granted_role
              ON granted_role.oid = membership.roleid
            JOIN pg_catalog.pg_roles member_role
              ON member_role.oid = membership.member
            WHERE granted_role.rolname =
                      'workflow_research_evidence_owner'
              AND member_role.rolname = 'workflow_migration_owner'
              AND NOT membership.admin_option
              AND NOT membership.inherit_option
              AND membership.set_option
        $membership_options$
        INTO v_membership_options_valid;
        IF NOT v_membership_options_valid THEN
            RAISE EXCEPTION
                'v59 requires non-inherited, SET-enabled deployment membership'
                USING ERRCODE = '42501';
        END IF;
    END IF;

    IF EXISTS (
        WITH RECURSIVE reachable(role_oid) AS (
            SELECT membership.roleid
            FROM pg_catalog.pg_auth_members membership
            JOIN pg_catalog.pg_roles runtime_role
              ON runtime_role.oid = membership.member
            WHERE runtime_role.rolname =
                      'workflow_automation_roi_runtime'
            UNION
            SELECT membership.roleid
            FROM pg_catalog.pg_auth_members membership
            JOIN reachable ON reachable.role_oid = membership.member
        )
        SELECT 1
        FROM reachable
        JOIN pg_catalog.pg_roles reached_role ON reached_role.oid = reachable.role_oid
        WHERE reached_role.rolname IN (
            'workflow_migration_owner',
            'workflow_research_evidence_owner'
        )
    ) THEN
        RAISE EXCEPTION
            'v59 rejects runtime role-membership escalation paths'
            USING ERRCODE = '42501';
    END IF;

    IF (
        SELECT count(*)
        FROM pg_catalog.pg_auth_members membership
        JOIN pg_catalog.pg_roles granted_role ON granted_role.oid = membership.roleid
        JOIN pg_catalog.pg_roles member_role ON member_role.oid = membership.member
        WHERE (
            granted_role.rolname = ANY (ARRAY[
                'workflow_migration_owner',
                'workflow_research_evidence_owner',
                'workflow_automation_roi_runtime'
            ])
            OR member_role.rolname = ANY (ARRAY[
                'workflow_migration_owner',
                'workflow_research_evidence_owner',
                'workflow_automation_roi_runtime'
            ])
        )
          AND granted_role.rolname = 'workflow_research_evidence_owner'
          AND member_role.rolname = 'workflow_migration_owner'
          AND NOT membership.admin_option
    ) <> 1 OR EXISTS (
        SELECT 1
        FROM pg_catalog.pg_auth_members membership
        JOIN pg_catalog.pg_roles granted_role ON granted_role.oid = membership.roleid
        JOIN pg_catalog.pg_roles member_role ON member_role.oid = membership.member
        WHERE (
            granted_role.rolname = ANY (ARRAY[
                'workflow_migration_owner',
                'workflow_research_evidence_owner',
                'workflow_automation_roi_runtime'
            ])
            OR member_role.rolname = ANY (ARRAY[
                'workflow_migration_owner',
                'workflow_research_evidence_owner',
                'workflow_automation_roi_runtime'
            ])
        )
          AND NOT (
              granted_role.rolname = 'workflow_research_evidence_owner'
              AND member_role.rolname = 'workflow_migration_owner'
              AND NOT membership.admin_option
          )
    ) THEN
        RAISE EXCEPTION
            'v59 requires the exact canonical role-membership graph'
            USING ERRCODE = '42501';
    END IF;

    SELECT count(*), min(upstream_namespace.nspname::text)
    INTO v_upstream_schema_count, v_upstream_schema
    FROM pg_catalog.pg_constraint constraint_info
    JOIN pg_catalog.pg_class binding_relation
      ON binding_relation.oid = constraint_info.conrelid
    JOIN pg_catalog.pg_namespace binding_namespace
      ON binding_namespace.oid = binding_relation.relnamespace
    JOIN pg_catalog.pg_class upstream_relation
      ON upstream_relation.oid = constraint_info.confrelid
    JOIN pg_catalog.pg_namespace upstream_namespace
      ON upstream_namespace.oid = upstream_relation.relnamespace
    WHERE constraint_info.conname = 'fk_recib_calculation_input_role'
      AND constraint_info.contype = 'f'
      AND constraint_info.connamespace = binding_namespace.oid
      AND binding_relation.relname =
          'research_evidence_consumer_input_binding'
      AND binding_relation.relkind = 'r'
      AND upstream_relation.relname = 'approved_calculation_input'
      AND upstream_relation.relkind = 'r'
      AND upstream_namespace.oid = binding_namespace.oid
      AND pg_catalog.octet_length(upstream_namespace.nspname::text) <= 63;
    IF v_upstream_schema_count <> 1 OR v_upstream_schema IS NULL THEN
        RAISE EXCEPTION
            'v59 requires exactly one validated upstream schema'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT count(*),
           min(project_relation.relname::text),
           min(project_attribute.attname::text)
    INTO v_project_target_count, v_projects_relation, v_project_id_column
    FROM pg_catalog.pg_constraint project_foreign_key
    JOIN pg_catalog.pg_class binding_relation
      ON binding_relation.oid = project_foreign_key.conrelid
    JOIN pg_catalog.pg_namespace binding_namespace
      ON binding_namespace.oid = binding_relation.relnamespace
    JOIN pg_catalog.pg_class project_relation
      ON project_relation.oid = project_foreign_key.confrelid
    JOIN pg_catalog.pg_namespace project_namespace
      ON project_namespace.oid = project_relation.relnamespace
    JOIN pg_catalog.pg_constraint project_primary_key
      ON project_primary_key.conrelid = project_relation.oid
     AND project_primary_key.connamespace = project_namespace.oid
     AND project_primary_key.contype = 'p'
     AND project_primary_key.conkey = project_foreign_key.confkey
    JOIN pg_catalog.pg_attribute project_attribute
      ON project_attribute.attrelid = project_relation.oid
     AND project_attribute.attnum = project_foreign_key.confkey[1]
     AND NOT project_attribute.attisdropped
    WHERE project_foreign_key.conname = 'fk_recib_project'
      AND project_foreign_key.contype = 'f'
      AND project_foreign_key.connamespace = binding_namespace.oid
      AND binding_namespace.nspname = v_upstream_schema
      AND binding_relation.relname =
          'research_evidence_consumer_input_binding'
      AND binding_relation.relkind = 'r'
      AND project_relation.relkind = 'r'
      AND project_namespace.oid = binding_namespace.oid
      AND pg_catalog.octet_length(project_relation.relname::text) <= 63
      AND pg_catalog.octet_length(project_attribute.attname::text) <= 63
      AND pg_catalog.array_length(project_foreign_key.confkey, 1) = 1;
    IF v_project_target_count <> 1
       OR v_projects_relation IS NULL
       OR v_project_id_column IS NULL THEN
        RAISE EXCEPTION
            'v59 requires exactly one validated upstream project target'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF pg_catalog.to_regclass(
        format('%I.research_evidence_consumer_input_binding', v_upstream_schema)
    ) IS NULL OR pg_catalog.to_regclass(
        format(
            '%I.research_evidence_consumer_input_binding_sequence_allocator',
            v_upstream_schema
        )
    ) IS NULL THEN
        RAISE EXCEPTION 'v59 requires complete v57 consumer-input bindings'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
    IF pg_catalog.to_regclass(
        format('%I.research_evidence_scenario_input_evaluation', v_upstream_schema)
    ) IS NULL THEN
        RAISE EXCEPTION 'v59 requires merged R1.7 v58 scenario-input evaluation'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
    IF pg_catalog.to_regclass(
        format('%I.approved_calculation_input', v_upstream_schema)
    ) IS NULL OR pg_catalog.to_regprocedure(
        format('%I.slicea_reject_mutation()', v_upstream_schema)
    ) IS NULL THEN
        RAISE EXCEPTION
            'v59 requires v48 calculation inputs and append-only enforcement'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT count(*) INTO v_tables
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = v_object_schema
      AND c.relkind = 'r'
      AND c.relname = ANY (ARRAY[
          'research_evidence_automation_roi_input_snapshot',
          'research_evidence_automation_roi_input_snapshot_binding',
          'automation_roi_input_snapshot_sequence_allocator'
      ]);
    IF v_tables NOT IN (0, 3) THEN
        RAISE EXCEPTION
            'v59 contract violation: partial Automation ROI snapshot schema (%)',
            v_tables USING ERRCODE = 'invalid_schema_definition';
    END IF;
    IF v_tables = 0 AND EXISTS (
        SELECT 1
        FROM pg_catalog.pg_proc p
        JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = v_object_schema
          AND p.proname = ANY (ARRAY[
              'research_evidence_prepare_automation_roi_snapshot',
              'research_evidence_prepare_automation_roi_snapshot_binding',
              'research_evidence_evaluate_automation_roi_bindings',
              'research_evidence_validate_automation_roi_snapshot',
              'research_evidence_assert_automation_roi_snapshot',
              'research_evidence_create_automation_roi_snapshot'
          ])
    ) THEN
        RAISE EXCEPTION
            'v59 contract violation: partial Automation ROI snapshot functions'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
    IF v_tables = 0 AND (
        EXISTS (
            SELECT 1
            FROM pg_catalog.pg_class relation
            WHERE relation.relnamespace = v_object_schema_oid
        )
        OR EXISTS (
            SELECT 1
            FROM pg_catalog.pg_proc function_info
            WHERE function_info.pronamespace = v_object_schema_oid
        )
    ) THEN
        RAISE EXCEPTION
            'v59 contract violation: conflicting first-apply dedicated-object inventory'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF v_object_schema_oid IS NOT NULL THEN
        SELECT namespace.nspacl::text
        INTO v_schema_acl_raw
        FROM pg_catalog.pg_namespace namespace
        WHERE namespace.oid = v_object_schema_oid;

        IF EXISTS (
            WITH expected(
                grantee, grantor, privilege_type, is_grantable
            ) AS (
                VALUES
                    (
                        'workflow_research_evidence_owner'::text,
                        'workflow_research_evidence_owner'::text,
                        'CREATE'::text,
                        false
                    ),
                    (
                        'workflow_research_evidence_owner'::text,
                        'workflow_research_evidence_owner'::text,
                        'USAGE'::text,
                        false
                    ),
                    (
                        'workflow_automation_roi_runtime'::text,
                        'workflow_research_evidence_owner'::text,
                        'USAGE'::text,
                        false
                    )
            ),
            actual AS (
                SELECT
                    COALESCE(grantee_role.rolname, 'PUBLIC') AS grantee,
                    grantor_role.rolname AS grantor,
                    acl.privilege_type,
                    acl.is_grantable
                FROM pg_catalog.pg_namespace namespace
                CROSS JOIN LATERAL pg_catalog.aclexplode(
                    COALESCE(
                        namespace.nspacl,
                        pg_catalog.acldefault('n', namespace.nspowner)
                    )
                ) acl
                LEFT JOIN pg_catalog.pg_roles grantee_role
                  ON grantee_role.oid = acl.grantee
                LEFT JOIN pg_catalog.pg_roles grantor_role
                  ON grantor_role.oid = acl.grantor
                WHERE namespace.oid = v_object_schema_oid
            ),
            differences AS (
                (SELECT * FROM expected EXCEPT SELECT * FROM actual)
                UNION ALL
                (SELECT * FROM actual EXCEPT SELECT * FROM expected)
            )
            SELECT 1 FROM differences
        ) THEN
            RAISE EXCEPTION
                'v59 contract violation: trusted-schema normalized ACL drift (raw nspacl=%)',
                v_schema_acl_raw
                USING ERRCODE = 'invalid_schema_definition';
        END IF;

        IF NOT pg_catalog.has_schema_privilege(
            'workflow_research_evidence_owner', v_object_schema, 'USAGE'
        ) OR NOT pg_catalog.has_schema_privilege(
            'workflow_research_evidence_owner', v_object_schema, 'CREATE'
        ) OR NOT pg_catalog.has_schema_privilege(
            'workflow_automation_roi_runtime', v_object_schema, 'USAGE'
        ) OR pg_catalog.has_schema_privilege(
            'workflow_automation_roi_runtime', v_object_schema, 'CREATE'
        ) THEN
            RAISE EXCEPTION
                'v59 contract violation: trusted-schema effective ACL drift'
                USING ERRCODE = 'invalid_schema_definition';
        END IF;

        IF EXISTS (
            WITH expected(
                default_role, default_namespace, object_type,
                grantee, grantor, privilege_type, is_grantable
            ) AS (
                VALUES (
                    'workflow_research_evidence_owner'::text,
                    'GLOBAL'::text,
                    'f'::"char",
                    'workflow_research_evidence_owner'::text,
                    'workflow_research_evidence_owner'::text,
                    'EXECUTE'::text,
                    false
                )
            ),
            actual AS (
                SELECT
                    owner_role.rolname AS default_role,
                    CASE
                        WHEN default_acl.defaclnamespace = 0
                            THEN 'GLOBAL'::text
                        ELSE namespace.nspname::text
                    END AS default_namespace,
                    default_acl.defaclobjtype AS object_type,
                    COALESCE(grantee_role.rolname, 'PUBLIC') AS grantee,
                    grantor_role.rolname AS grantor,
                    acl.privilege_type,
                    acl.is_grantable
                FROM pg_catalog.pg_default_acl default_acl
                JOIN pg_catalog.pg_roles owner_role
                  ON owner_role.oid = default_acl.defaclrole
                LEFT JOIN pg_catalog.pg_namespace namespace
                  ON namespace.oid = default_acl.defaclnamespace
                CROSS JOIN LATERAL pg_catalog.aclexplode(
                    default_acl.defaclacl
                ) acl
                LEFT JOIN pg_catalog.pg_roles grantee_role
                  ON grantee_role.oid = acl.grantee
                LEFT JOIN pg_catalog.pg_roles grantor_role
                  ON grantor_role.oid = acl.grantor
                WHERE owner_role.rolname =
                    'workflow_research_evidence_owner'
            ),
            differences AS (
                (SELECT * FROM expected EXCEPT SELECT * FROM actual)
                UNION ALL
                (SELECT * FROM actual EXCEPT SELECT * FROM expected)
            )
            SELECT 1 FROM differences
        ) THEN
            RAISE EXCEPTION
                'v59 contract violation: trusted-schema default ACL drift'
                USING ERRCODE = 'invalid_schema_definition';
        END IF;
    END IF;

    IF pg_catalog.has_schema_privilege(
           v_migration_role_oid, v_object_schema_oid, 'USAGE'
       ) THEN
        RAISE EXCEPTION
            'v59 rejects migration-owner dedicated-schema USAGE'
            USING ERRCODE = '42501';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_class relation
        CROSS JOIN LATERAL pg_catalog.aclexplode(
            COALESCE(
                relation.relacl,
                pg_catalog.acldefault('r', relation.relowner)
            )
        ) acl
        WHERE relation.relnamespace = v_object_schema_oid
          AND acl.grantee = v_migration_role_oid
          AND relation.relowner <> v_migration_role_oid
    ) OR EXISTS (
        SELECT 1
        FROM pg_catalog.pg_proc function_info
        CROSS JOIN LATERAL pg_catalog.aclexplode(
            COALESCE(
                function_info.proacl,
                pg_catalog.acldefault('f', function_info.proowner)
            )
        ) acl
        WHERE function_info.pronamespace = v_object_schema_oid
          AND acl.grantee = v_migration_role_oid
          AND function_info.proowner <> v_migration_role_oid
    ) THEN
        RAISE EXCEPTION
            'v59 rejects migration-owner direct dedicated-object access'
            USING ERRCODE = '42501';
    END IF;

    IF v_tables = 3 THEN
        SELECT string_agg(c.relname, ', ' ORDER BY c.relname) INTO v_missing
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_catalog.pg_roles owner_role ON owner_role.oid = c.relowner
        WHERE n.nspname = v_object_schema
          AND c.relname = ANY (ARRAY[
              'research_evidence_automation_roi_input_snapshot',
              'research_evidence_automation_roi_input_snapshot_binding',
              'automation_roi_input_snapshot_sequence_allocator'
          ])
          AND owner_role.rolname <> 'workflow_research_evidence_owner';
        IF v_missing IS NOT NULL THEN
            RAISE EXCEPTION 'v59 contract violation: divergent table owners %',
                v_missing USING ERRCODE = 'invalid_schema_definition';
        END IF;

        IF EXISTS (
            WITH expected(
                table_name, relation_kind, persistence,
                row_security, force_row_security, relation_options,
                replica_identity, is_partition, parent_count, child_count
            ) AS (
                VALUES
                    (
                        'research_evidence_automation_roi_input_snapshot'::text,
                        'r'::"char", 'p'::"char", false, false, NULL::text[],
                        'd'::"char", false, 0::bigint, 0::bigint
                    ),
                    (
                        'research_evidence_automation_roi_input_snapshot_binding',
                        'r'::"char", 'p'::"char", false, false, NULL::text[],
                        'd'::"char", false, 0::bigint, 0::bigint
                    ),
                    (
                        'automation_roi_input_snapshot_sequence_allocator',
                        'r'::"char", 'p'::"char", false, false, NULL::text[],
                        'd'::"char", false, 0::bigint, 0::bigint
                    )
            ),
            actual AS (
                SELECT relation.relname::text,
                       relation.relkind,
                       relation.relpersistence,
                       relation.relrowsecurity,
                       relation.relforcerowsecurity,
                       relation.reloptions,
                       relation.relreplident,
                       relation.relispartition,
                       (
                           SELECT count(*)::bigint
                           FROM pg_catalog.pg_inherits inheritance
                           WHERE inheritance.inhrelid = relation.oid
                       ),
                       (
                           SELECT count(*)::bigint
                           FROM pg_catalog.pg_inherits inheritance
                           WHERE inheritance.inhparent = relation.oid
                       )
                FROM pg_catalog.pg_class relation
                JOIN pg_catalog.pg_namespace namespace
                  ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = v_object_schema
                  AND relation.relname = ANY (ARRAY[
                      'research_evidence_automation_roi_input_snapshot',
                      'research_evidence_automation_roi_input_snapshot_binding',
                      'automation_roi_input_snapshot_sequence_allocator'
                  ])
            )
            (SELECT * FROM expected EXCEPT SELECT * FROM actual)
            UNION ALL
            (SELECT * FROM actual EXCEPT SELECT * FROM expected)
        ) THEN
            RAISE EXCEPTION
                'v59 contract violation: divergent relation state'
                USING ERRCODE = 'invalid_schema_definition';
        END IF;

        IF EXISTS (
            WITH expected(table_name, expected_count) AS (
                VALUES
                    (
                        'research_evidence_automation_roi_input_snapshot'::text,
                        19::bigint
                    ),
                    (
                        'research_evidence_automation_roi_input_snapshot_binding',
                        8::bigint
                    ),
                    (
                        'automation_roi_input_snapshot_sequence_allocator',
                        4::bigint
                    )
            ),
            actual AS (
                SELECT expected.table_name,
                       expected.expected_count,
                       count(DISTINCT relation.oid)::bigint
                           AS relation_count,
                       count(attribute.attnum)::bigint AS actual_count
                FROM expected
                LEFT JOIN pg_catalog.pg_namespace namespace
                  ON namespace.nspname = v_object_schema
                LEFT JOIN pg_catalog.pg_class relation
                  ON relation.relnamespace = namespace.oid
                 AND relation.relname = expected.table_name
                LEFT JOIN pg_catalog.pg_attribute attribute
                  ON attribute.attrelid = relation.oid
                 AND attribute.attnum > 0
                 AND NOT attribute.attisdropped
                GROUP BY expected.table_name, expected.expected_count
            )
            SELECT 1
            FROM actual
            WHERE relation_count <> 1
               OR actual_count <> expected_count
        ) THEN
            RAISE EXCEPTION 'v59 contract violation: divergent column count'
                USING ERRCODE = 'invalid_schema_definition';
        END IF;

        IF EXISTS (
            WITH expected(
                table_name, ordinal_position, column_name, type_oid,
                type_modifier, not_null, default_expression
            ) AS (
                VALUES
                    ('research_evidence_automation_roi_input_snapshot'::text, 1, 'id'::text, 'uuid'::regtype::oid, -1, true, 'gen_random_uuid()'::text),
                    ('research_evidence_automation_roi_input_snapshot', 2, 'project_id', 'uuid'::regtype::oid, -1, true, NULL),
                    ('research_evidence_automation_roi_input_snapshot', 3, 'consumer_contract', 'text'::regtype::oid, -1, true, NULL),
                    ('research_evidence_automation_roi_input_snapshot', 4, 'consumer_contract_version', 'text'::regtype::oid, -1, true, NULL),
                    ('research_evidence_automation_roi_input_snapshot', 5, 'binding_set_id', 'text'::regtype::oid, -1, true, NULL),
                    ('research_evidence_automation_roi_input_snapshot', 6, 'snapshot_sequence', 'integer'::regtype::oid, -1, true, NULL),
                    ('research_evidence_automation_roi_input_snapshot', 7, 'request_id', 'text'::regtype::oid, -1, true, NULL),
                    ('research_evidence_automation_roi_input_snapshot', 8, 'policy_identifier', 'text'::regtype::oid, -1, true, NULL),
                    ('research_evidence_automation_roi_input_snapshot', 9, 'policy_version', 'text'::regtype::oid, -1, true, NULL),
                    ('research_evidence_automation_roi_input_snapshot', 10, 'policy_parameters_json', 'jsonb'::regtype::oid, -1, true, NULL),
                    ('research_evidence_automation_roi_input_snapshot', 11, 'policy_fingerprint', 'text'::regtype::oid, -1, true, NULL),
                    ('research_evidence_automation_roi_input_snapshot', 12, 'evaluator_version', 'text'::regtype::oid, -1, true, NULL),
                    ('research_evidence_automation_roi_input_snapshot', 13, 'freshness_as_of', 'timestamptz'::regtype::oid, -1, true, NULL),
                    ('research_evidence_automation_roi_input_snapshot', 14, 'completeness_status', 'text'::regtype::oid, -1, true, NULL),
                    ('research_evidence_automation_roi_input_snapshot', 15, 'policy_evaluation_status', 'text'::regtype::oid, -1, true, NULL),
                    ('research_evidence_automation_roi_input_snapshot', 16, 'evaluation_reasons_json', 'jsonb'::regtype::oid, -1, true, NULL),
                    ('research_evidence_automation_roi_input_snapshot', 17, 'evaluated_by', 'text'::regtype::oid, -1, true, NULL),
                    ('research_evidence_automation_roi_input_snapshot', 18, 'supersedes_snapshot_id', 'uuid'::regtype::oid, -1, false, NULL),
                    ('research_evidence_automation_roi_input_snapshot', 19, 'evaluated_at', 'timestamptz'::regtype::oid, -1, true, NULL),
                    ('automation_roi_input_snapshot_sequence_allocator', 1, 'project_id', 'uuid'::regtype::oid, -1, true, NULL),
                    ('automation_roi_input_snapshot_sequence_allocator', 2, 'consumer_contract', 'text'::regtype::oid, -1, true, NULL),
                    ('automation_roi_input_snapshot_sequence_allocator', 3, 'binding_set_id', 'text'::regtype::oid, -1, true, NULL),
                    ('automation_roi_input_snapshot_sequence_allocator', 4, 'last_sequence', 'integer'::regtype::oid, -1, true, NULL),
                    ('research_evidence_automation_roi_input_snapshot_binding', 1, 'id', 'uuid'::regtype::oid, -1, true, 'gen_random_uuid()'),
                    ('research_evidence_automation_roi_input_snapshot_binding', 2, 'snapshot_id', 'uuid'::regtype::oid, -1, true, NULL),
                    ('research_evidence_automation_roi_input_snapshot_binding', 3, 'project_id', 'uuid'::regtype::oid, -1, true, NULL),
                    ('research_evidence_automation_roi_input_snapshot_binding', 4, 'consumer_contract', 'text'::regtype::oid, -1, true, NULL),
                    ('research_evidence_automation_roi_input_snapshot_binding', 5, 'binding_set_id', 'text'::regtype::oid, -1, true, NULL),
                    ('research_evidence_automation_roi_input_snapshot_binding', 6, 'input_role', 'text'::regtype::oid, -1, true, NULL),
                    ('research_evidence_automation_roi_input_snapshot_binding', 7, 'binding_record_id', 'uuid'::regtype::oid, -1, true, NULL),
                    ('research_evidence_automation_roi_input_snapshot_binding', 8, 'linked_at', 'timestamptz'::regtype::oid, -1, true, NULL)
            ),
            actual AS (
                SELECT relation.relname::text,
                       attribute.attnum::integer,
                       attribute.attname::text,
                       attribute.atttypid::oid,
                       attribute.atttypmod::integer,
                       attribute.attnotnull,
                       pg_catalog.pg_get_expr(
                           default_value.adbin,
                           default_value.adrelid,
                           true
                       )::text
                FROM pg_catalog.pg_class relation
                JOIN pg_catalog.pg_namespace namespace
                  ON namespace.oid = relation.relnamespace
                JOIN pg_catalog.pg_attribute attribute
                  ON attribute.attrelid = relation.oid
                 AND attribute.attnum > 0
                 AND NOT attribute.attisdropped
                LEFT JOIN pg_catalog.pg_attrdef default_value
                  ON default_value.adrelid = relation.oid
                 AND default_value.adnum = attribute.attnum
                WHERE namespace.nspname = v_object_schema
                  AND relation.relname = ANY (ARRAY[
                      'research_evidence_automation_roi_input_snapshot',
                      'research_evidence_automation_roi_input_snapshot_binding',
                      'automation_roi_input_snapshot_sequence_allocator'
                  ])
            )
            (SELECT * FROM expected EXCEPT SELECT * FROM actual)
            UNION ALL
            (SELECT * FROM actual EXCEPT SELECT * FROM expected)
        ) THEN
            RAISE EXCEPTION 'v59 contract violation: divergent columns'
                USING ERRCODE = 'invalid_schema_definition';
        END IF;

        IF EXISTS (
            WITH expected(
                constraint_name, table_name, constraint_type,
                columns, is_deferrable, initially_deferred
            ) AS (
                VALUES
                    ('research_evidence_automation_roi_input_snapshot_pkey'::text, 'research_evidence_automation_roi_input_snapshot'::text, 'p'::"char", ARRAY['id']::text[], false, false),
                    ('uq_rearois_id_project_scope', 'research_evidence_automation_roi_input_snapshot', 'u'::"char", ARRAY['id','project_id','consumer_contract','binding_set_id']::text[], false, false),
                    ('uq_rearois_scope_sequence', 'research_evidence_automation_roi_input_snapshot', 'u'::"char", ARRAY['project_id','consumer_contract','binding_set_id','snapshot_sequence']::text[], false, false),
                    ('uq_rearois_scope_request', 'research_evidence_automation_roi_input_snapshot', 'u'::"char", ARRAY['project_id','consumer_contract','binding_set_id','request_id']::text[], false, false),
                    ('uq_rearois_supersedes_once', 'research_evidence_automation_roi_input_snapshot', 'u'::"char", ARRAY['supersedes_snapshot_id']::text[], false, false),
                    ('pk_rearoisa', 'automation_roi_input_snapshot_sequence_allocator', 'p'::"char", ARRAY['project_id','consumer_contract','binding_set_id']::text[], false, false),
                    ('research_evidence_automation_roi_input_snapshot_binding_pkey', 'research_evidence_automation_roi_input_snapshot_binding', 'p'::"char", ARRAY['id']::text[], false, false),
                    ('uq_rearoisb_snapshot_role', 'research_evidence_automation_roi_input_snapshot_binding', 'u'::"char", ARRAY['snapshot_id','input_role']::text[], false, false),
                    ('uq_rearoisb_snapshot_binding', 'research_evidence_automation_roi_input_snapshot_binding', 'u'::"char", ARRAY['snapshot_id','binding_record_id']::text[], false, false)
            ),
            actual AS (
                SELECT constraint_info.conname::text,
                       relation.relname::text,
                       constraint_info.contype,
                       ARRAY(
                           SELECT attribute.attname::text
                           FROM unnest(constraint_info.conkey)
                               WITH ORDINALITY key_column(attnum, position)
                           JOIN pg_catalog.pg_attribute attribute
                             ON attribute.attrelid = relation.oid
                            AND attribute.attnum = key_column.attnum
                           ORDER BY key_column.position
                       ),
                       constraint_info.condeferrable,
                       constraint_info.condeferred
                FROM pg_catalog.pg_constraint constraint_info
                JOIN pg_catalog.pg_class relation
                  ON relation.oid = constraint_info.conrelid
                JOIN pg_catalog.pg_namespace namespace
                  ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = v_object_schema
                  AND constraint_info.contype IN ('p', 'u')
            )
            (SELECT * FROM expected EXCEPT SELECT * FROM actual)
            UNION ALL
            (SELECT * FROM actual EXCEPT SELECT * FROM expected)
        ) THEN
            RAISE EXCEPTION 'v59 contract violation: divergent keys'
                USING ERRCODE = 'invalid_schema_definition';
        END IF;

        IF EXISTS (
            WITH expected(
                constraint_name, table_name, columns,
                referenced_table, referenced_columns,
                match_type, update_action, delete_action,
                is_deferrable, initially_deferred
            ) AS (
                VALUES
                    ('fk_rearois_project'::text, 'research_evidence_automation_roi_input_snapshot'::text, ARRAY['project_id']::text[], format('%I.%I', v_upstream_schema, v_projects_relation)::text, ARRAY[v_project_id_column]::text[], 's'::"char", 'a'::"char", 'r'::"char", false, false),
                    ('fk_rearois_supersedes_same_scope', 'research_evidence_automation_roi_input_snapshot', ARRAY['supersedes_snapshot_id','project_id','consumer_contract','binding_set_id']::text[], 'research_evidence_automation_roi.research_evidence_automation_roi_input_snapshot', ARRAY['id','project_id','consumer_contract','binding_set_id']::text[], 's'::"char", 'a'::"char", 'r'::"char", false, false),
                    ('fk_rearoisa_project', 'automation_roi_input_snapshot_sequence_allocator', ARRAY['project_id']::text[], format('%I.%I', v_upstream_schema, v_projects_relation)::text, ARRAY[v_project_id_column]::text[], 's'::"char", 'a'::"char", 'r'::"char", false, false),
                    ('fk_rearoisb_snapshot_project', 'research_evidence_automation_roi_input_snapshot_binding', ARRAY['snapshot_id','project_id','consumer_contract','binding_set_id']::text[], 'research_evidence_automation_roi.research_evidence_automation_roi_input_snapshot', ARRAY['id','project_id','consumer_contract','binding_set_id']::text[], 's'::"char", 'a'::"char", 'r'::"char", false, false),
                    ('fk_rearoisb_binding_scope', 'research_evidence_automation_roi_input_snapshot_binding', ARRAY['binding_record_id','project_id','consumer_contract','binding_set_id','input_role']::text[], format('%I.research_evidence_consumer_input_binding', v_upstream_schema)::text, ARRAY['id','project_id','consumer_contract','binding_set_id','input_key']::text[], 's'::"char", 'a'::"char", 'r'::"char", false, false)
            ),
            actual AS (
                SELECT constraint_info.conname::text,
                       relation.relname::text,
                       ARRAY(
                           SELECT attribute.attname::text
                           FROM unnest(constraint_info.conkey)
                               WITH ORDINALITY key_column(attnum, position)
                           JOIN pg_catalog.pg_attribute attribute
                             ON attribute.attrelid = relation.oid
                            AND attribute.attnum = key_column.attnum
                           ORDER BY key_column.position
                       ),
                       format(
                           '%I.%I',
                           referenced_namespace.nspname,
                           referenced_relation.relname
                       ),
                       ARRAY(
                           SELECT attribute.attname::text
                           FROM unnest(constraint_info.confkey)
                               WITH ORDINALITY key_column(attnum, position)
                           JOIN pg_catalog.pg_attribute attribute
                             ON attribute.attrelid = referenced_relation.oid
                            AND attribute.attnum = key_column.attnum
                           ORDER BY key_column.position
                       ),
                       constraint_info.confmatchtype,
                       constraint_info.confupdtype,
                       constraint_info.confdeltype,
                       constraint_info.condeferrable,
                       constraint_info.condeferred
                FROM pg_catalog.pg_constraint constraint_info
                JOIN pg_catalog.pg_class relation
                  ON relation.oid = constraint_info.conrelid
                JOIN pg_catalog.pg_namespace namespace
                  ON namespace.oid = relation.relnamespace
                JOIN pg_catalog.pg_class referenced_relation
                  ON referenced_relation.oid = constraint_info.confrelid
                JOIN pg_catalog.pg_namespace referenced_namespace
                  ON referenced_namespace.oid =
                     referenced_relation.relnamespace
                WHERE namespace.nspname = v_object_schema
                  AND constraint_info.contype = 'f'
            )
            (SELECT * FROM expected EXCEPT SELECT * FROM actual)
            UNION ALL
            (SELECT * FROM actual EXCEPT SELECT * FROM expected)
        ) THEN
            RAISE EXCEPTION 'v59 contract violation: divergent foreign keys'
                USING ERRCODE = 'invalid_schema_definition';
        END IF;

        IF EXISTS (
            WITH expected_base(
                index_name, table_name, is_primary, is_unique, key_definitions
            ) AS (
                VALUES
                    ('research_evidence_automation_roi_input_snapshot_pkey'::text, 'research_evidence_automation_roi_input_snapshot'::text, true, true, ARRAY['id']::text[]),
                    ('uq_rearois_id_project_scope', 'research_evidence_automation_roi_input_snapshot', false, true, ARRAY['id','project_id','consumer_contract','binding_set_id']::text[]),
                    ('uq_rearois_scope_sequence', 'research_evidence_automation_roi_input_snapshot', false, true, ARRAY['project_id','consumer_contract','binding_set_id','snapshot_sequence']::text[]),
                    ('uq_rearois_scope_request', 'research_evidence_automation_roi_input_snapshot', false, true, ARRAY['project_id','consumer_contract','binding_set_id','request_id']::text[]),
                    ('uq_rearois_supersedes_once', 'research_evidence_automation_roi_input_snapshot', false, true, ARRAY['supersedes_snapshot_id']::text[]),
                    ('pk_rearoisa', 'automation_roi_input_snapshot_sequence_allocator', true, true, ARRAY['project_id','consumer_contract','binding_set_id']::text[]),
                    ('research_evidence_automation_roi_input_snapshot_binding_pkey', 'research_evidence_automation_roi_input_snapshot_binding', true, true, ARRAY['id']::text[]),
                    ('uq_rearoisb_snapshot_role', 'research_evidence_automation_roi_input_snapshot_binding', false, true, ARRAY['snapshot_id','input_role']::text[]),
                    ('uq_rearoisb_snapshot_binding', 'research_evidence_automation_roi_input_snapshot_binding', false, true, ARRAY['snapshot_id','binding_record_id']::text[]),
                    ('idx_rearois_scope_sequence', 'research_evidence_automation_roi_input_snapshot', false, false, ARRAY['project_id','consumer_contract','binding_set_id','snapshot_sequence']::text[]),
                    ('idx_rearoisb_binding', 'research_evidence_automation_roi_input_snapshot_binding', false, false, ARRAY['project_id','binding_record_id']::text[])
            ),
            expected AS (
                SELECT index_name, table_name, 'btree'::text AS access_method,
                       is_primary, is_unique, false AS is_exclusion,
                       true AS is_valid, true AS is_ready, true AS is_live,
                       true AS is_immediate, false AS nulls_not_distinct,
                       false AS is_clustered, false AS is_replica_identity,
                       cardinality(key_definitions)::smallint AS key_count,
                       cardinality(key_definitions)::smallint AS attribute_count,
                       key_definitions, ARRAY[]::text[] AS included_definitions,
                       NULL::text AS predicate_expression,
                       NULL::text AS index_expressions,
                       format(
                           'create%sindex%son%s.%susingbtree(%s)',
                           CASE WHEN is_unique THEN 'unique' ELSE '' END,
                           index_name, v_object_schema, table_name,
                           array_to_string(key_definitions, ',')
                       )::text AS normalized_definition
                FROM expected_base
            ),
            actual AS (
                SELECT index_relation.relname::text,
                       table_relation.relname::text,
                       access_method.amname::text,
                       index_info.indisprimary,
                       index_info.indisunique,
                       index_info.indisexclusion,
                       index_info.indisvalid,
                       index_info.indisready,
                       index_info.indislive,
                       index_info.indimmediate,
                       index_info.indnullsnotdistinct,
                       index_info.indisclustered,
                       index_info.indisreplident,
                       index_info.indnkeyatts,
                       index_info.indnatts,
                       ARRAY(
                           SELECT pg_catalog.pg_get_indexdef(
                               index_info.indexrelid, position, true
                           )::text
                           FROM generate_series(
                               1, index_info.indnkeyatts
                           ) position
                           ORDER BY position
                       ),
                       ARRAY(
                           SELECT pg_catalog.pg_get_indexdef(
                               index_info.indexrelid, position, true
                           )::text
                           FROM generate_series(
                               index_info.indnkeyatts + 1,
                               index_info.indnatts
                           ) position
                           ORDER BY position
                       ),
                       pg_catalog.pg_get_expr(
                           index_info.indpred, index_info.indrelid, true
                       )::text,
                       pg_catalog.pg_get_expr(
                           index_info.indexprs, index_info.indrelid, true
                       )::text,
                       lower(regexp_replace(
                           replace(
                               pg_catalog.pg_get_indexdef(
                                   index_info.indexrelid, 0, true
                               ),
                               '"', ''
                           ),
                           '[[:space:]]+', '', 'g'
                       ))::text
                FROM pg_catalog.pg_index index_info
                JOIN pg_catalog.pg_class index_relation
                  ON index_relation.oid = index_info.indexrelid
                JOIN pg_catalog.pg_namespace namespace
                  ON namespace.oid = index_relation.relnamespace
                JOIN pg_catalog.pg_class table_relation
                  ON table_relation.oid = index_info.indrelid
                JOIN pg_catalog.pg_am access_method
                  ON access_method.oid = index_relation.relam
                WHERE namespace.nspname = v_object_schema
                  AND table_relation.relnamespace = namespace.oid
                  AND table_relation.relname = ANY (ARRAY[
                      'research_evidence_automation_roi_input_snapshot',
                      'research_evidence_automation_roi_input_snapshot_binding',
                      'automation_roi_input_snapshot_sequence_allocator'
                  ])
            )
            (SELECT * FROM expected EXCEPT SELECT * FROM actual)
            UNION ALL
            (SELECT * FROM actual EXCEPT SELECT * FROM expected)
        ) THEN
            RAISE EXCEPTION 'v59 contract violation: divergent indexes'
                USING ERRCODE = 'invalid_schema_definition';
        END IF;
        IF EXISTS (
            SELECT 1
            FROM pg_catalog.pg_class relation
            WHERE relation.relnamespace = v_object_schema_oid
              AND relation.relname <> ALL (ARRAY[
                  'research_evidence_automation_roi_input_snapshot',
                  'research_evidence_automation_roi_input_snapshot_binding',
                  'automation_roi_input_snapshot_sequence_allocator',
                  'research_evidence_automation_roi_input_snapshot_pkey',
                  'uq_rearois_id_project_scope',
                  'uq_rearois_scope_sequence',
                  'uq_rearois_scope_request',
                  'uq_rearois_supersedes_once',
                  'pk_rearoisa',
                  'research_evidence_automation_roi_input_snapshot_binding_pkey',
                  'uq_rearoisb_snapshot_role',
                  'uq_rearoisb_snapshot_binding',
                  'idx_rearois_scope_sequence',
                  'idx_rearoisb_binding'
              ])
        ) THEN
            RAISE EXCEPTION
                'v59 contract violation: divergent dedicated relation inventory'
                USING ERRCODE = 'invalid_schema_definition';
        END IF;

        SELECT string_agg(
            expected.constraint_name,
            ', ' ORDER BY expected.constraint_name
        ) INTO v_missing
        FROM (VALUES
            (
                'ck_rearois_fixed_contract'::text,
                'research_evidence_automation_roi_input_snapshot'::text,
                'consumer_contract=''deterministic_calculation''andconsumer_contract_version=''automation_roi.evidence_input.v1''andpolicy_identifier=''automation_roi.evidence_use''andpolicy_version=''1''andevaluator_version=''automation_roi.evidence_use.evaluator.v1''andpolicy_fingerprint=''ca7aadce968c35f9839d79b61a4cbb62fe9bc05fcc692e6c773ee36ec4a13c9d''andpolicy_parameters_json=''{"qualified":{"freshness_status":["stale"],"consumer_disposition":["qualified"]},"satisfies":{"drift_status":"no_material_drift","review_status":"approved","freshness_status":"fresh","lineage_is_current":true,"availability_status":true,"consumer_disposition":"meets_contract"},"indeterminate":{"drift_status":["not_assessed","indeterminate"],"review_status":["not_assessed"],"freshness_status":["unknown"],"consumer_disposition":["indeterminate"]},"required_roles":["baseline_hours_per_period","post_automation_hours_per_period","fully_loaded_rate_per_hour","periods_per_year","annual_recurring_cost","one_time_implementation_cost"],"calculation_kind":"automation_roi","does_not_satisfy":{"drift_status":["material_drift"],"review_status":["rejected","needs_revision","withdrawn"],"lineage_is_current":[false],"availability_status":[false],"disposition_reasons":["contradiction_declared"],"consumer_disposition":["does_not_meet_contract"]},"consumer_contract":"deterministic_calculation","status_precedence":["does_not_satisfy","indeterminate","qualified","satisfies"],"binding_record_must_be_current":true}''andcompleteness_status=''complete'''::text
            ),
            (
                'ck_rearois_status',
                'research_evidence_automation_roi_input_snapshot',
                'policy_evaluation_status=anyarray[''satisfies'',''qualified'',''does_not_satisfy'',''indeterminate'']'
            ),
            (
                'ck_rearois_json_shapes',
                'research_evidence_automation_roi_input_snapshot',
                'jsonb_typeofpolicy_parameters_json=''object''andjsonb_typeofevaluation_reasons_json=''array''andjsonb_array_lengthevaluation_reasons_json>=1'
            ),
            (
                'ck_rearois_nonblank',
                'research_evidence_automation_roi_input_snapshot',
                'binding_set_id!~''^[[:space:]]*$''andrequest_id!~''^[[:space:]]*$''andevaluated_by!~''^[[:space:]]*$'''
            ),
            (
                'ck_rearois_fingerprint',
                'research_evidence_automation_roi_input_snapshot',
                'policy_fingerprint~''^[0-9a-f]{64}$'''
            ),
            (
                'ck_rearoisa_fixed_contract',
                'automation_roi_input_snapshot_sequence_allocator',
                'consumer_contract=''deterministic_calculation''andbinding_set_id!~''^[[:space:]]*$'''
            ),
            (
                'ck_rearoisa_sequence',
                'automation_roi_input_snapshot_sequence_allocator',
                'last_sequence>=0'
            ),
            (
                'ck_rearoisb_role',
                'research_evidence_automation_roi_input_snapshot_binding',
                'input_role=anyarray[''baseline_hours_per_period'',''post_automation_hours_per_period'',''fully_loaded_rate_per_hour'',''periods_per_year'',''annual_recurring_cost'',''one_time_implementation_cost'']'
            ),
            (
                'ck_rearoisb_nonblank',
                'research_evidence_automation_roi_input_snapshot_binding',
                'consumer_contract=''deterministic_calculation''andbinding_set_id!~''^[[:space:]]*$'''
            )
        ) expected(constraint_name, table_name, normalized_expression)
        WHERE NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_constraint constraint_info
            JOIN pg_catalog.pg_class relation
              ON relation.oid = constraint_info.conrelid
            JOIN pg_catalog.pg_namespace namespace
              ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = v_object_schema
              AND relation.relname = expected.table_name
              AND constraint_info.conname = expected.constraint_name
              AND constraint_info.contype = 'c'
              AND constraint_info.convalidated
              AND NOT constraint_info.condeferrable
              AND NOT constraint_info.condeferred
              AND replace(
                  replace(
                      translate(
                          regexp_replace(
                              lower(pg_catalog.pg_get_expr(
                                  constraint_info.conbin,
                                  constraint_info.conrelid,
                                  true
                              )),
                              '[[:space:]]+', '', 'g'
                          ),
                          '()', ''
                      ),
                      '::text', ''
                  ),
                  '::jsonb', ''
              ) = expected.normalized_expression
        );
        IF v_missing IS NOT NULL THEN
            RAISE EXCEPTION 'v59 contract violation: divergent checks %',
                v_missing USING ERRCODE = 'invalid_schema_definition';
        END IF;
        IF (
            SELECT count(*)
            FROM pg_catalog.pg_constraint constraint_info
            JOIN pg_catalog.pg_class relation
              ON relation.oid = constraint_info.conrelid
            JOIN pg_catalog.pg_namespace namespace
              ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = v_object_schema
              AND constraint_info.contype = 'c'
        ) <> 9 THEN
            RAISE EXCEPTION
                'v59 contract violation: divergent check inventory'
                USING ERRCODE = 'invalid_schema_definition';
        END IF;

        SELECT string_agg(expected.name, ', ' ORDER BY expected.name)
        INTO v_missing
        FROM (VALUES
            (
                'research_evidence_prepare_automation_roi_snapshot'::text,
                ''::text, 'trigger'::text, false,
                'pg_catalog, research_evidence_automation_roi, pg_temp'::text,
                '2d9d6115d8bfcaa794ef154050f8b592f6ab15ad0edf8c5d9f00aec6c2f4a127'::text,
                NULL::text,
                'f'::"char", 'v'::"char", false, false, 'u'::"char",
                100::real, 0::real, '-'::text, false, 0::oid, 0::smallint,
                false
            ),
            (
                'research_evidence_prepare_automation_roi_snapshot_binding',
                '', 'trigger', false,
                'pg_catalog, research_evidence_automation_roi, pg_temp',
                '8676bfcdd392a23fa47521e367c1f5929559d3b43637851b8bf1883965a0d884',
                NULL,
                'f', 'v', false, false, 'u',
                100, 0, '-', false, 0, 0, false
            ),
            (
                'research_evidence_evaluate_automation_roi_bindings',
                'uuid, text, uuid[], timestamp with time zone, '
                    || 'timestamp with time zone',
                'TABLE(policy_status text, reason_codes jsonb)',
                true, 'pg_catalog, research_evidence_automation_roi, pg_temp',
                'd4a44442a448ee3cd3fc0a3a17412ad8a4c98addfb41d7179adbc2fefd3aeb9d',
                NULL,
                'f', 'v', false, false, 'u',
                100, 1000, '-', true, 0, 0, false
            ),
            (
                'research_evidence_validate_automation_roi_snapshot',
                'uuid', 'void', false,
                'pg_catalog, research_evidence_automation_roi, pg_temp',
                '0063589f8a2255c19e32b74a95bc2d3ad5a53ac96abbbd10c9b8f19ec10847bf',
                NULL,
                'f', 'v', false, false, 'u',
                100, 0, '-', false, 0, 0, false
            ),
            (
                'research_evidence_assert_automation_roi_snapshot',
                '', 'trigger', true,
                'pg_catalog, research_evidence_automation_roi, pg_temp',
                'f40335dc8a9cb11c8bd64b6e622bc88f04c368c37fae0cb92cd8773e05ed66c8',
                NULL,
                'f', 'v', false, false, 'u',
                100, 0, '-', false, 0, 0, false
            ),
            (
                'research_evidence_create_automation_roi_snapshot',
                'uuid, text, uuid[], text, timestamp with time zone, text',
                'uuid', true,
                'pg_catalog, research_evidence_automation_roi, pg_temp',
                'c763cb182e25a6dd469b07b66cbd1d6a4e93016f9988024a98af87c0e32c2cd6',
                NULL,
                'f', 'v', false, false, 'u',
                100, 0, '-', false, 0, 0, false
            )
        ) expected(
            name, identity_arguments, result_type,
            security_definer, search_path, source_sha256, binary_reference,
            function_kind, volatility, is_strict, is_leakproof,
            parallel_safety, execution_cost, row_estimate,
            support_function, returns_set, variadic_type_oid,
            argument_defaults, sql_body_present
        )
        LEFT JOIN pg_catalog.pg_namespace n ON n.nspname = v_object_schema
        LEFT JOIN pg_catalog.pg_proc p
          ON p.pronamespace = n.oid
         AND p.proname = expected.name
         AND pg_catalog.oidvectortypes(p.proargtypes) =
             expected.identity_arguments
        LEFT JOIN pg_catalog.pg_roles owner_role ON owner_role.oid = p.proowner
        LEFT JOIN pg_catalog.pg_language language ON language.oid = p.prolang
        WHERE p.oid IS NULL
           OR language.lanname IS DISTINCT FROM 'plpgsql'
           OR pg_catalog.pg_get_function_result(p.oid)
                IS DISTINCT FROM expected.result_type
           OR p.prosecdef IS DISTINCT FROM expected.security_definer
           OR p.proconfig IS DISTINCT FROM
                ARRAY['search_path=' || expected.search_path]::text[]
           OR p.prokind IS DISTINCT FROM expected.function_kind
           OR p.provolatile IS DISTINCT FROM expected.volatility
           OR p.proisstrict IS DISTINCT FROM expected.is_strict
           OR p.proleakproof IS DISTINCT FROM expected.is_leakproof
           OR p.proparallel IS DISTINCT FROM expected.parallel_safety
           OR p.procost IS DISTINCT FROM expected.execution_cost
           OR p.prorows IS DISTINCT FROM expected.row_estimate
           OR COALESCE(
                  pg_catalog.to_jsonb(p)->>'prosupport',
                  '-'
              ) IS DISTINCT FROM expected.support_function
           OR p.proretset IS DISTINCT FROM expected.returns_set
           OR p.provariadic IS DISTINCT FROM expected.variadic_type_oid
           OR p.pronargdefaults IS DISTINCT FROM expected.argument_defaults
           OR (
                  pg_catalog.to_jsonb(p)->>'prosqlbody' IS NOT NULL
              ) IS DISTINCT FROM expected.sql_body_present
           OR pg_catalog.encode(
                  pg_catalog.sha256(
                      pg_catalog.convert_to(p.prosrc, 'UTF8')
                  ),
                  'hex'
              ) IS DISTINCT FROM expected.source_sha256
           OR p.probin IS DISTINCT FROM expected.binary_reference
           OR owner_role.rolname IS DISTINCT FROM
                'workflow_research_evidence_owner';
        IF v_missing IS NOT NULL THEN
            RAISE EXCEPTION 'v59 contract violation: divergent functions %',
                v_missing USING ERRCODE = 'invalid_schema_definition';
        END IF;
        IF (
            SELECT count(*)
            FROM pg_catalog.pg_proc function_info
            JOIN pg_catalog.pg_namespace namespace
              ON namespace.oid = function_info.pronamespace
            WHERE namespace.nspname = v_object_schema
        ) <> 6 THEN
            RAISE EXCEPTION
                'v59 contract violation: divergent function inventory'
                USING ERRCODE = 'invalid_schema_definition';
        END IF;

        IF EXISTS (
            SELECT 1
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            CROSS JOIN LATERAL pg_catalog.aclexplode(
                COALESCE(
                    c.relacl,
                    pg_catalog.acldefault('r', c.relowner)
                )
            ) acl
            LEFT JOIN pg_catalog.pg_roles grantee ON grantee.oid = acl.grantee
            WHERE n.nspname = v_object_schema
              AND c.relname = ANY (ARRAY[
                  'research_evidence_automation_roi_input_snapshot',
                  'research_evidence_automation_roi_input_snapshot_binding',
                  'automation_roi_input_snapshot_sequence_allocator'
              ])
              AND acl.grantee <> c.relowner
              AND NOT (
                  grantee.rolname = 'workflow_automation_roi_runtime'
                  AND acl.privilege_type = 'SELECT'
                  AND c.relname IN (
                      'research_evidence_automation_roi_input_snapshot',
                      'research_evidence_automation_roi_input_snapshot_binding'
                  )
              )
        ) OR EXISTS (
            SELECT 1
            FROM pg_catalog.pg_proc p
            JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
            CROSS JOIN LATERAL pg_catalog.aclexplode(
                COALESCE(
                    p.proacl,
                    pg_catalog.acldefault('f', p.proowner)
                )
            ) acl
            LEFT JOIN pg_catalog.pg_roles grantee ON grantee.oid = acl.grantee
            WHERE n.nspname = v_object_schema
              AND p.proname = ANY (ARRAY[
                  'research_evidence_prepare_automation_roi_snapshot',
                  'research_evidence_prepare_automation_roi_snapshot_binding',
                  'research_evidence_evaluate_automation_roi_bindings',
                  'research_evidence_validate_automation_roi_snapshot',
                  'research_evidence_assert_automation_roi_snapshot',
                  'research_evidence_create_automation_roi_snapshot'
              ])
              AND acl.grantee <> p.proowner
              AND NOT (
                  grantee.rolname = 'workflow_automation_roi_runtime'
                  AND acl.privilege_type = 'EXECUTE'
                  AND p.proname =
                      'research_evidence_create_automation_roi_snapshot'
              )
        ) OR EXISTS (
            SELECT 1
            FROM pg_catalog.pg_proc function_info
            JOIN pg_catalog.pg_namespace namespace
              ON namespace.oid = function_info.pronamespace
            CROSS JOIN LATERAL pg_catalog.aclexplode(
                COALESCE(
                    function_info.proacl,
                    pg_catalog.acldefault('f', function_info.proowner)
                )
            ) acl
            LEFT JOIN pg_catalog.pg_roles grantee ON grantee.oid = acl.grantee
            WHERE namespace.nspname = v_upstream_schema
              AND function_info.proname = 'slicea_reject_mutation'
              AND pg_catalog.oidvectortypes(function_info.proargtypes) = ''
              AND acl.privilege_type = 'EXECUTE'
              AND (
                  acl.grantee = 0
                  OR grantee.rolname IN (
                      'workflow_research_evidence_owner',
                      'workflow_automation_roi_runtime'
                  )
              )
        ) OR NOT pg_catalog.has_schema_privilege(
            'workflow_research_evidence_owner', v_upstream_schema, 'USAGE'
        ) OR pg_catalog.has_schema_privilege(
            'workflow_research_evidence_owner', v_upstream_schema, 'CREATE'
        ) OR EXISTS (
            WITH expected(privilege_type) AS (
                VALUES ('USAGE'::text)
            ),
            actual AS (
                SELECT acl.privilege_type
                FROM pg_catalog.pg_namespace namespace
                CROSS JOIN LATERAL pg_catalog.aclexplode(
                    COALESCE(
                        namespace.nspacl,
                        pg_catalog.acldefault('n', namespace.nspowner)
                    )
                ) acl
                JOIN pg_catalog.pg_roles grantee ON grantee.oid = acl.grantee
                WHERE namespace.nspname = v_upstream_schema
                  AND grantee.rolname =
                      'workflow_research_evidence_owner'
            )
            (SELECT * FROM expected EXCEPT SELECT * FROM actual)
            UNION ALL
            (SELECT * FROM actual EXCEPT SELECT * FROM expected)
        ) OR EXISTS (
            SELECT 1
            FROM pg_catalog.pg_namespace namespace
            CROSS JOIN LATERAL pg_catalog.aclexplode(
                COALESCE(
                    namespace.nspacl,
                    pg_catalog.acldefault('n', namespace.nspowner)
                )
            ) acl
            LEFT JOIN pg_catalog.pg_roles grantee ON grantee.oid = acl.grantee
            WHERE namespace.nspname = v_upstream_schema
              AND (
                  acl.grantee = 0
                  OR grantee.rolname =
                      'workflow_automation_roi_runtime'
              )
        ) OR EXISTS (
            SELECT 1
            FROM pg_catalog.pg_class relation
            JOIN pg_catalog.pg_namespace namespace
              ON namespace.oid = relation.relnamespace
            CROSS JOIN LATERAL pg_catalog.aclexplode(
                COALESCE(
                    relation.relacl,
                    pg_catalog.acldefault('r', relation.relowner)
                )
            ) acl
            JOIN pg_catalog.pg_roles grantee ON grantee.oid = acl.grantee
            WHERE namespace.nspname = v_upstream_schema
              AND relation.relname = v_projects_relation
              AND grantee.rolname =
                  'workflow_research_evidence_owner'
        ) OR EXISTS (
            WITH expected(
                column_name, privilege_type, is_grantable
            ) AS (
                VALUES (
                    v_project_id_column,
                    'REFERENCES'::text,
                    false
                )
            ),
            actual AS (
                SELECT attribute.attname::text,
                       acl.privilege_type,
                       acl.is_grantable
                FROM pg_catalog.pg_class relation
                JOIN pg_catalog.pg_namespace namespace
                  ON namespace.oid = relation.relnamespace
                JOIN pg_catalog.pg_attribute attribute
                  ON attribute.attrelid = relation.oid
                 AND attribute.attnum > 0
                 AND NOT attribute.attisdropped
                CROSS JOIN LATERAL pg_catalog.aclexplode(
                    attribute.attacl
                ) acl
                JOIN pg_catalog.pg_roles grantee
                  ON grantee.oid = acl.grantee
                WHERE namespace.nspname = v_upstream_schema
                  AND relation.relname = v_projects_relation
                  AND grantee.rolname =
                      'workflow_research_evidence_owner'
            )
            (SELECT * FROM expected EXCEPT SELECT * FROM actual)
            UNION ALL
            (SELECT * FROM actual EXCEPT SELECT * FROM expected)
        ) OR EXISTS (
            SELECT 1
            FROM pg_catalog.pg_class relation
            JOIN pg_catalog.pg_namespace namespace
              ON namespace.oid = relation.relnamespace
            CROSS JOIN LATERAL pg_catalog.aclexplode(
                COALESCE(
                    relation.relacl,
                    pg_catalog.acldefault('r', relation.relowner)
                )
            ) acl
            LEFT JOIN pg_catalog.pg_roles grantee
              ON grantee.oid = acl.grantee
            WHERE namespace.nspname = v_upstream_schema
              AND relation.relname = v_projects_relation
              AND (
                  acl.grantee = 0
                  OR grantee.rolname =
                      'workflow_automation_roi_runtime'
              )
        ) OR EXISTS (
            SELECT 1
            FROM pg_catalog.pg_class relation
            JOIN pg_catalog.pg_namespace namespace
              ON namespace.oid = relation.relnamespace
            JOIN pg_catalog.pg_attribute attribute
              ON attribute.attrelid = relation.oid
             AND attribute.attnum > 0
             AND NOT attribute.attisdropped
            CROSS JOIN LATERAL pg_catalog.aclexplode(
                attribute.attacl
            ) acl
            LEFT JOIN pg_catalog.pg_roles grantee
              ON grantee.oid = acl.grantee
            WHERE namespace.nspname = v_upstream_schema
              AND relation.relname = v_projects_relation
              AND (
                  acl.grantee = 0
                  OR grantee.rolname =
                      'workflow_automation_roi_runtime'
              )
        ) OR EXISTS (
            SELECT 1
            FROM pg_catalog.pg_class relation
            JOIN pg_catalog.pg_namespace namespace
              ON namespace.oid = relation.relnamespace
            JOIN pg_catalog.pg_roles owner_role
              ON owner_role.oid = relation.relowner
            WHERE namespace.nspname = v_upstream_schema
              AND relation.relname = v_projects_relation
              AND owner_role.rolname =
                  'workflow_research_evidence_owner'
        ) THEN
            RAISE EXCEPTION 'v59 contract violation: privilege drift'
                USING ERRCODE = 'invalid_schema_definition';
        END IF;

        SELECT string_agg(expected.name, ', ' ORDER BY expected.name)
        INTO v_missing
        FROM (VALUES
            (
                'trg_rearois_prepare_insert'::text,
                'research_evidence_automation_roi_input_snapshot'::text,
                'research_evidence_automation_roi'::text,
                'research_evidence_prepare_automation_roi_snapshot'::text,
                7::smallint, 'A'::"char", false, false, false
            ),
            (
                'trg_rearoisb_prepare_insert',
                'research_evidence_automation_roi_input_snapshot_binding',
                'research_evidence_automation_roi',
                'research_evidence_prepare_automation_roi_snapshot_binding',
                7::smallint, 'A'::"char", false, false, false
            ),
            (
                'trg_rearois_no_mutation',
                'research_evidence_automation_roi_input_snapshot',
                v_upstream_schema,
                'slicea_reject_mutation',
                27::smallint, 'O'::"char", false, false, false
            ),
            (
                'trg_rearoisb_no_mutation',
                'research_evidence_automation_roi_input_snapshot_binding',
                v_upstream_schema,
                'slicea_reject_mutation',
                27::smallint, 'O'::"char", false, false, false
            ),
            (
                'trg_rearois_complete',
                'research_evidence_automation_roi_input_snapshot',
                'research_evidence_automation_roi',
                'research_evidence_assert_automation_roi_snapshot',
                5::smallint, 'O'::"char", true, true, true
            )
        ) expected(
            name, table_name, function_schema, function_name,
            trigger_type, enabled,
            is_deferrable, initially_deferred, is_constraint
        )
        LEFT JOIN pg_catalog.pg_namespace n ON n.nspname = v_object_schema
        LEFT JOIN pg_catalog.pg_class c
          ON c.relnamespace = n.oid
         AND c.relname = expected.table_name
        LEFT JOIN pg_catalog.pg_trigger t
          ON t.tgrelid = c.oid
         AND t.tgname = expected.name
        LEFT JOIN pg_catalog.pg_proc p ON p.oid = t.tgfoid
        LEFT JOIN pg_catalog.pg_namespace function_namespace
          ON function_namespace.oid = p.pronamespace
        WHERE t.oid IS NULL
           OR function_namespace.nspname
                IS DISTINCT FROM expected.function_schema
           OR p.proname IS DISTINCT FROM expected.function_name
           OR t.tgtype IS DISTINCT FROM expected.trigger_type
           OR t.tgenabled IS DISTINCT FROM expected.enabled
           OR t.tgdeferrable IS DISTINCT FROM expected.is_deferrable
           OR t.tginitdeferred IS DISTINCT FROM expected.initially_deferred
           OR t.tgisinternal
           OR t.tgnargs <> 0
           OR t.tgattr <> ''::int2vector
           OR t.tgqual IS NOT NULL
           OR t.tgoldtable IS NOT NULL
           OR t.tgnewtable IS NOT NULL
           OR (t.tgconstraint <> 0) IS DISTINCT FROM expected.is_constraint;
        IF v_missing IS NOT NULL THEN
            RAISE EXCEPTION 'v59 contract violation: divergent triggers %',
                v_missing USING ERRCODE = 'invalid_schema_definition';
        END IF;
        IF (
            SELECT count(*)
            FROM pg_catalog.pg_trigger trigger_info
            JOIN pg_catalog.pg_class relation ON relation.oid = trigger_info.tgrelid
            JOIN pg_catalog.pg_namespace namespace
              ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = v_object_schema
              AND relation.relname = ANY (ARRAY[
                  'research_evidence_automation_roi_input_snapshot',
                  'research_evidence_automation_roi_input_snapshot_binding'
              ])
              AND NOT trigger_info.tgisinternal
        ) <> 5 THEN
            RAISE EXCEPTION
                'v59 contract violation: divergent trigger inventory'
                USING ERRCODE = 'invalid_schema_definition';
        END IF;

        IF EXISTS (
            WITH resolved AS (
                SELECT
                    (
                        SELECT count(*)
                        FROM pg_catalog.pg_namespace namespace
                        WHERE namespace.nspname = v_object_schema
                    ) AS schema_count,
                    (
                        SELECT min(namespace.oid)
                        FROM pg_catalog.pg_namespace namespace
                        WHERE namespace.nspname = v_object_schema
                    ) AS schema_oid,
                    (
                        SELECT count(*)
                        FROM pg_catalog.pg_roles runtime_role
                        WHERE runtime_role.rolname =
                            'workflow_automation_roi_runtime'
                    ) AS runtime_role_count,
                    (
                        SELECT min(runtime_role.oid)
                        FROM pg_catalog.pg_roles runtime_role
                        WHERE runtime_role.rolname =
                            'workflow_automation_roi_runtime'
                    ) AS runtime_role_oid,
                    (
                        SELECT count(*)
                        FROM pg_catalog.pg_class relation
                        JOIN pg_catalog.pg_namespace namespace
                          ON namespace.oid = relation.relnamespace
                        WHERE namespace.nspname = v_object_schema
                          AND relation.relname =
                            'research_evidence_automation_roi_input_snapshot'
                    ) AS snapshot_count,
                    (
                        SELECT min(relation.oid)
                        FROM pg_catalog.pg_class relation
                        JOIN pg_catalog.pg_namespace namespace
                          ON namespace.oid = relation.relnamespace
                        WHERE namespace.nspname = v_object_schema
                          AND relation.relname =
                            'research_evidence_automation_roi_input_snapshot'
                    ) AS snapshot_oid,
                    (
                        SELECT count(*)
                        FROM pg_catalog.pg_class relation
                        JOIN pg_catalog.pg_namespace namespace
                          ON namespace.oid = relation.relnamespace
                        WHERE namespace.nspname = v_object_schema
                          AND relation.relname =
                            'research_evidence_automation_roi_input_snapshot_binding'
                    ) AS binding_count,
                    (
                        SELECT min(relation.oid)
                        FROM pg_catalog.pg_class relation
                        JOIN pg_catalog.pg_namespace namespace
                          ON namespace.oid = relation.relnamespace
                        WHERE namespace.nspname = v_object_schema
                          AND relation.relname =
                            'research_evidence_automation_roi_input_snapshot_binding'
                    ) AS binding_oid,
                    (
                        SELECT count(*)
                        FROM pg_catalog.pg_class relation
                        JOIN pg_catalog.pg_namespace namespace
                          ON namespace.oid = relation.relnamespace
                        WHERE namespace.nspname = v_object_schema
                          AND relation.relname =
                            'automation_roi_input_snapshot_sequence_allocator'
                    ) AS allocator_count,
                    (
                        SELECT min(relation.oid)
                        FROM pg_catalog.pg_class relation
                        JOIN pg_catalog.pg_namespace namespace
                          ON namespace.oid = relation.relnamespace
                        WHERE namespace.nspname = v_object_schema
                          AND relation.relname =
                            'automation_roi_input_snapshot_sequence_allocator'
                    ) AS allocator_oid,
                    (
                        SELECT count(*)
                        FROM pg_catalog.pg_proc function_info
                        JOIN pg_catalog.pg_namespace namespace
                          ON namespace.oid = function_info.pronamespace
                        WHERE namespace.nspname = v_object_schema
                          AND function_info.proname =
                            'research_evidence_create_automation_roi_snapshot'
                          AND pg_catalog.oidvectortypes(
                              function_info.proargtypes
                          ) =
                            'uuid, text, uuid[], text, timestamp with time zone, text'
                    ) AS entry_count,
                    (
                        SELECT min(function_info.oid)
                        FROM pg_catalog.pg_proc function_info
                        JOIN pg_catalog.pg_namespace namespace
                          ON namespace.oid = function_info.pronamespace
                        WHERE namespace.nspname = v_object_schema
                          AND function_info.proname =
                            'research_evidence_create_automation_roi_snapshot'
                          AND pg_catalog.oidvectortypes(
                              function_info.proargtypes
                          ) =
                            'uuid, text, uuid[], text, timestamp with time zone, text'
                    ) AS entry_oid,
                    (
                        SELECT count(*)
                        FROM pg_catalog.pg_proc function_info
                        JOIN pg_catalog.pg_namespace namespace
                          ON namespace.oid = function_info.pronamespace
                        WHERE namespace.nspname = v_object_schema
                          AND function_info.proname =
                            'research_evidence_validate_automation_roi_snapshot'
                          AND pg_catalog.oidvectortypes(
                              function_info.proargtypes
                          ) = 'uuid'
                    ) AS helper_count,
                    (
                        SELECT min(function_info.oid)
                        FROM pg_catalog.pg_proc function_info
                        JOIN pg_catalog.pg_namespace namespace
                          ON namespace.oid = function_info.pronamespace
                        WHERE namespace.nspname = v_object_schema
                          AND function_info.proname =
                            'research_evidence_validate_automation_roi_snapshot'
                          AND pg_catalog.oidvectortypes(
                              function_info.proargtypes
                          ) = 'uuid'
                    ) AS helper_oid
            )
            SELECT 1
            FROM resolved
            WHERE schema_count <> 1
               OR runtime_role_count <> 1
               OR snapshot_count <> 1
               OR binding_count <> 1
               OR allocator_count <> 1
               OR entry_count <> 1
               OR helper_count <> 1
               OR NOT pg_catalog.has_schema_privilege(
                    runtime_role_oid, schema_oid, 'USAGE'
               )
               OR pg_catalog.has_schema_privilege(
                    runtime_role_oid, schema_oid, 'CREATE'
               )
               OR NOT pg_catalog.has_table_privilege(
                    runtime_role_oid, snapshot_oid, 'SELECT'
               )
               OR NOT pg_catalog.has_table_privilege(
                    runtime_role_oid, binding_oid, 'SELECT'
               )
               OR pg_catalog.has_table_privilege(
                    runtime_role_oid,
                    allocator_oid,
                    'SELECT,INSERT,UPDATE,DELETE,TRUNCATE'
               )
               OR NOT pg_catalog.has_function_privilege(
                    runtime_role_oid, entry_oid, 'EXECUTE'
               )
               OR pg_catalog.has_function_privilege(
                    runtime_role_oid, helper_oid, 'EXECUTE'
               )
        ) THEN
            RAISE EXCEPTION 'v59 contract violation: runtime ACL drift'
                USING ERRCODE = 'invalid_schema_definition';
        END IF;

        IF EXISTS (
            WITH expected(table_name, privilege_type) AS (
                VALUES
                    ('research_evidence_consumer_input_binding'::text, 'SELECT'::text),
                    ('approved_calculation_input', 'SELECT'),
                    (
                        'research_evidence_consumer_input_binding_sequence_allocator',
                        'SELECT'
                    ),
                    (
                        'research_evidence_consumer_input_binding_sequence_allocator',
                        'UPDATE'
                    )
            ),
            actual AS (
                SELECT relation.relname AS table_name, acl.privilege_type
                FROM pg_catalog.pg_class relation
                JOIN pg_catalog.pg_namespace namespace
                  ON namespace.oid = relation.relnamespace
                CROSS JOIN LATERAL pg_catalog.aclexplode(
                    COALESCE(
                        relation.relacl,
                        pg_catalog.acldefault('r', relation.relowner)
                    )
                ) acl
                JOIN pg_catalog.pg_roles grantee ON grantee.oid = acl.grantee
                WHERE namespace.nspname = v_upstream_schema
                  AND relation.relname = ANY (ARRAY[
                      'research_evidence_consumer_input_binding',
                      'approved_calculation_input',
                      'research_evidence_consumer_input_binding_sequence_allocator'
                  ])
                  AND grantee.rolname =
                      'workflow_research_evidence_owner'
            )
            (SELECT * FROM expected EXCEPT SELECT * FROM actual)
            UNION ALL
            (SELECT * FROM actual EXCEPT SELECT * FROM expected)
        ) OR EXISTS (
            SELECT 1
            FROM pg_catalog.pg_class relation
            JOIN pg_catalog.pg_namespace namespace
              ON namespace.oid = relation.relnamespace
            CROSS JOIN LATERAL pg_catalog.aclexplode(
                COALESCE(
                    relation.relacl,
                    pg_catalog.acldefault('r', relation.relowner)
                )
            ) acl
            LEFT JOIN pg_catalog.pg_roles grantee ON grantee.oid = acl.grantee
            WHERE namespace.nspname = v_upstream_schema
              AND relation.relname = ANY (ARRAY[
                  'research_evidence_consumer_input_binding',
                  'approved_calculation_input',
                  'research_evidence_consumer_input_binding_sequence_allocator'
              ])
              AND (
                  acl.grantee = 0
                  OR grantee.rolname =
                      'workflow_automation_roi_runtime'
              )
        ) OR EXISTS (
            SELECT 1
            FROM pg_catalog.pg_class relation
            JOIN pg_catalog.pg_namespace namespace
              ON namespace.oid = relation.relnamespace
            JOIN pg_catalog.pg_roles owner_role ON owner_role.oid = relation.relowner
            WHERE namespace.nspname = v_upstream_schema
              AND relation.relname = ANY (ARRAY[
                  'research_evidence_consumer_input_binding',
                  'approved_calculation_input',
                  'research_evidence_consumer_input_binding_sequence_allocator'
              ])
              AND owner_role.rolname =
                  'workflow_research_evidence_owner'
        ) THEN
            RAISE EXCEPTION 'v59 contract violation: function-owner ACL drift'
                USING ERRCODE = 'invalid_schema_definition';
        END IF;

    END IF;
END;
$preflight$;

-- Reapply pre-repair authority map:
-- M: canonical roles/membership; schema/default/object/upstream ACLs; relation,
--    column, constraint, index, trigger, function, and runtime OID inventories.
-- O: allocator/history, predecessor-chain, and snapshot-completeness data reads.
-- X: none. The O window below is read-only and precedes every repair operation.
SET ROLE workflow_research_evidence_owner;

DO $owner_read_validation$
BEGIN
    IF session_user <> 'workflow_migration_owner'
       OR current_user <> 'workflow_research_evidence_owner' THEN
        RAISE EXCEPTION
            'v59 owner validation requires migration session and owner role'
            USING ERRCODE = '42501';
    END IF;

    IF (
        SELECT count(*)
        FROM pg_catalog.pg_class relation
        JOIN pg_catalog.pg_namespace namespace
          ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'research_evidence_automation_roi'
          AND relation.relname = ANY (ARRAY[
              'research_evidence_automation_roi_input_snapshot',
              'research_evidence_automation_roi_input_snapshot_binding',
              'automation_roi_input_snapshot_sequence_allocator'
          ])
          AND relation.relkind = 'r'
    ) = 3 THEN
        IF EXISTS (
            SELECT 1
            FROM (
                SELECT project_id, consumer_contract, binding_set_id,
                       count(*)::integer AS row_count,
                       min(snapshot_sequence) AS min_sequence,
                       max(snapshot_sequence) AS max_sequence
                FROM research_evidence_automation_roi.
                    research_evidence_automation_roi_input_snapshot
                GROUP BY project_id, consumer_contract, binding_set_id
            ) history
            FULL JOIN
                research_evidence_automation_roi.
                    automation_roi_input_snapshot_sequence_allocator
                allocator
              USING (project_id, consumer_contract, binding_set_id)
            WHERE history.project_id IS NULL
               OR allocator.project_id IS NULL
               OR history.row_count <> allocator.last_sequence
               OR history.min_sequence <> 1
               OR history.max_sequence <> allocator.last_sequence
        ) THEN
            RAISE EXCEPTION 'v59 contract violation: allocator integrity drift'
                USING ERRCODE = 'invalid_schema_definition';
        END IF;

        IF EXISTS (
            SELECT 1
            FROM research_evidence_automation_roi.
                research_evidence_automation_roi_input_snapshot snapshot
            LEFT JOIN research_evidence_automation_roi.
                research_evidence_automation_roi_input_snapshot predecessor
              ON predecessor.id = snapshot.supersedes_snapshot_id
             AND predecessor.project_id = snapshot.project_id
             AND predecessor.consumer_contract = snapshot.consumer_contract
             AND predecessor.binding_set_id = snapshot.binding_set_id
             AND predecessor.snapshot_sequence =
                 snapshot.snapshot_sequence - 1
            WHERE (
                snapshot.snapshot_sequence = 1
                AND snapshot.supersedes_snapshot_id IS NOT NULL
            ) OR (
                snapshot.snapshot_sequence > 1
                AND predecessor.id IS NULL
            )
        ) THEN
            RAISE EXCEPTION 'v59 contract violation: predecessor chain drift'
                USING ERRCODE = 'invalid_schema_definition';
        END IF;

        IF EXISTS (
            SELECT 1
            FROM research_evidence_automation_roi.
                research_evidence_automation_roi_input_snapshot snapshot
            LEFT JOIN LATERAL (
                SELECT count(*)::integer AS row_count,
                       count(DISTINCT child.input_role)::integer AS role_count,
                       count(*) FILTER (
                           WHERE child.linked_at = snapshot.evaluated_at
                       )::integer AS coherent_time_count
                FROM research_evidence_automation_roi.
                    research_evidence_automation_roi_input_snapshot_binding child
                WHERE child.snapshot_id = snapshot.id
                  AND child.project_id = snapshot.project_id
            ) children ON true
            WHERE children.row_count <> 6
               OR children.role_count <> 6
               OR children.coherent_time_count <> 6
        ) THEN
            RAISE EXCEPTION
                'v59 contract violation: snapshot completeness drift'
                USING ERRCODE = 'invalid_schema_definition';
        END IF;
    END IF;
END;
$owner_read_validation$;

RESET ROLE;

DO $migration_context_restored$
BEGIN
    IF session_user <> 'workflow_migration_owner'
       OR current_user <> 'workflow_migration_owner' THEN
        RAISE EXCEPTION
            'v59 migration-owner context was not restored'
            USING ERRCODE = '42501';
    END IF;
END;
$migration_context_restored$;

DO $temporary_upstream_acl$
DECLARE
    v_upstream_schema text;
    v_upstream_schema_count integer;
    v_projects_relation text;
    v_project_id_column text;
    v_project_target_count integer;
BEGIN
    SELECT count(*), min(upstream_namespace.nspname::text)
    INTO v_upstream_schema_count, v_upstream_schema
    FROM pg_catalog.pg_constraint constraint_info
    JOIN pg_catalog.pg_class binding_relation
      ON binding_relation.oid = constraint_info.conrelid
    JOIN pg_catalog.pg_namespace binding_namespace
      ON binding_namespace.oid = binding_relation.relnamespace
    JOIN pg_catalog.pg_class upstream_relation
      ON upstream_relation.oid = constraint_info.confrelid
    JOIN pg_catalog.pg_namespace upstream_namespace
      ON upstream_namespace.oid = upstream_relation.relnamespace
    WHERE constraint_info.conname = 'fk_recib_calculation_input_role'
      AND constraint_info.contype = 'f'
      AND constraint_info.connamespace = binding_namespace.oid
      AND binding_relation.relname =
          'research_evidence_consumer_input_binding'
      AND binding_relation.relkind = 'r'
      AND upstream_relation.relname = 'approved_calculation_input'
      AND upstream_relation.relkind = 'r'
      AND upstream_namespace.oid = binding_namespace.oid
      AND pg_catalog.octet_length(upstream_namespace.nspname::text) <= 63;
    IF v_upstream_schema_count <> 1 OR v_upstream_schema IS NULL THEN
        RAISE EXCEPTION
            'v59 requires exactly one validated upstream schema'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT count(*),
           min(project_relation.relname::text),
           min(project_attribute.attname::text)
    INTO v_project_target_count, v_projects_relation, v_project_id_column
    FROM pg_catalog.pg_constraint project_foreign_key
    JOIN pg_catalog.pg_class binding_relation
      ON binding_relation.oid = project_foreign_key.conrelid
    JOIN pg_catalog.pg_namespace binding_namespace
      ON binding_namespace.oid = binding_relation.relnamespace
    JOIN pg_catalog.pg_class project_relation
      ON project_relation.oid = project_foreign_key.confrelid
    JOIN pg_catalog.pg_namespace project_namespace
      ON project_namespace.oid = project_relation.relnamespace
    JOIN pg_catalog.pg_constraint project_primary_key
      ON project_primary_key.conrelid = project_relation.oid
     AND project_primary_key.connamespace = project_namespace.oid
     AND project_primary_key.contype = 'p'
     AND project_primary_key.conkey = project_foreign_key.confkey
    JOIN pg_catalog.pg_attribute project_attribute
      ON project_attribute.attrelid = project_relation.oid
     AND project_attribute.attnum = project_foreign_key.confkey[1]
     AND NOT project_attribute.attisdropped
    WHERE project_foreign_key.conname = 'fk_recib_project'
      AND project_foreign_key.contype = 'f'
      AND project_foreign_key.connamespace = binding_namespace.oid
      AND binding_namespace.nspname = v_upstream_schema
      AND binding_relation.relname =
          'research_evidence_consumer_input_binding'
      AND binding_relation.relkind = 'r'
      AND project_relation.relkind = 'r'
      AND project_namespace.oid = binding_namespace.oid
      AND pg_catalog.octet_length(project_relation.relname::text) <= 63
      AND pg_catalog.octet_length(project_attribute.attname::text) <= 63
      AND pg_catalog.array_length(project_foreign_key.confkey, 1) = 1;
    IF v_project_target_count <> 1
       OR v_projects_relation IS NULL
       OR v_project_id_column IS NULL THEN
        RAISE EXCEPTION
            'v59 requires exactly one validated upstream project target'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    EXECUTE format(
        'REVOKE ALL ON FUNCTION %I.slicea_reject_mutation()
         FROM PUBLIC, workflow_research_evidence_owner,
              workflow_automation_roi_runtime',
        v_upstream_schema
    );
    EXECUTE format(
        'REVOKE ALL ON SCHEMA %I
         FROM PUBLIC, workflow_research_evidence_owner,
              workflow_automation_roi_runtime',
        v_upstream_schema
    );
    EXECUTE format(
        'GRANT USAGE ON SCHEMA %I
         TO workflow_research_evidence_owner',
        v_upstream_schema
    );
    EXECUTE format(
        'REVOKE ALL ON TABLE
             %I.research_evidence_consumer_input_binding,
             %I.approved_calculation_input,
             %I.research_evidence_consumer_input_binding_sequence_allocator
         FROM PUBLIC, workflow_research_evidence_owner,
              workflow_automation_roi_runtime',
        v_upstream_schema, v_upstream_schema, v_upstream_schema
    );
    EXECUTE format(
        'GRANT REFERENCES (%I) ON TABLE %I.%I
         TO workflow_research_evidence_owner',
        v_project_id_column, v_upstream_schema, v_projects_relation
    );
    EXECUTE format(
        'GRANT REFERENCES ON TABLE
             %I.research_evidence_consumer_input_binding
         TO workflow_research_evidence_owner',
        v_upstream_schema
    );
    EXECUTE format(
        'GRANT EXECUTE ON FUNCTION %I.slicea_reject_mutation()
         TO workflow_research_evidence_owner',
        v_upstream_schema
    );
END;
$temporary_upstream_acl$;

DO $upstream_runtime_acl$
DECLARE
    v_upstream_schema text;
    v_upstream_schema_count integer;
BEGIN
    SELECT count(*), min(upstream_namespace.nspname::text)
    INTO v_upstream_schema_count, v_upstream_schema
    FROM pg_catalog.pg_constraint constraint_info
    JOIN pg_catalog.pg_class binding_relation
      ON binding_relation.oid = constraint_info.conrelid
    JOIN pg_catalog.pg_namespace binding_namespace
      ON binding_namespace.oid = binding_relation.relnamespace
    JOIN pg_catalog.pg_class upstream_relation
      ON upstream_relation.oid = constraint_info.confrelid
    JOIN pg_catalog.pg_namespace upstream_namespace
      ON upstream_namespace.oid = upstream_relation.relnamespace
    WHERE constraint_info.conname = 'fk_recib_calculation_input_role'
      AND constraint_info.contype = 'f'
      AND constraint_info.connamespace = binding_namespace.oid
      AND binding_relation.relname =
          'research_evidence_consumer_input_binding'
      AND binding_relation.relkind = 'r'
      AND upstream_relation.relname = 'approved_calculation_input'
      AND upstream_relation.relkind = 'r'
      AND upstream_namespace.oid = binding_namespace.oid
      AND pg_catalog.octet_length(upstream_namespace.nspname::text) <= 63;
    IF v_upstream_schema_count <> 1 OR v_upstream_schema IS NULL THEN
        RAISE EXCEPTION
            'v59 requires exactly one validated upstream schema'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    EXECUTE format(
        'GRANT SELECT ON TABLE
             %I.research_evidence_consumer_input_binding,
             %I.approved_calculation_input
         TO workflow_research_evidence_owner',
        v_upstream_schema, v_upstream_schema
    );
    EXECUTE format(
        'GRANT SELECT, UPDATE ON TABLE
             %I.research_evidence_consumer_input_binding_sequence_allocator
         TO workflow_research_evidence_owner',
        v_upstream_schema
    );
    EXECUTE format(
        'GRANT USAGE ON SCHEMA %I
         TO workflow_research_evidence_owner',
        v_upstream_schema
    );
END;
$upstream_runtime_acl$;

SET ROLE workflow_research_evidence_owner;

REVOKE ALL ON SCHEMA research_evidence_automation_roi
    FROM PUBLIC, workflow_automation_roi_runtime;
GRANT USAGE ON SCHEMA research_evidence_automation_roi
    TO workflow_automation_roi_runtime;

CREATE TABLE IF NOT EXISTS
research_evidence_automation_roi.
research_evidence_automation_roi_input_snapshot (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL,
    consumer_contract TEXT NOT NULL,
    consumer_contract_version TEXT NOT NULL,
    binding_set_id TEXT NOT NULL,
    snapshot_sequence INTEGER NOT NULL,
    request_id TEXT NOT NULL,
    policy_identifier TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    policy_parameters_json JSONB NOT NULL,
    policy_fingerprint TEXT NOT NULL,
    evaluator_version TEXT NOT NULL,
    freshness_as_of TIMESTAMPTZ NOT NULL,
    completeness_status TEXT NOT NULL,
    policy_evaluation_status TEXT NOT NULL,
    evaluation_reasons_json JSONB NOT NULL,
    evaluated_by TEXT NOT NULL,
    supersedes_snapshot_id UUID,
    evaluated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT research_evidence_automation_roi_input_snapshot_pkey
        PRIMARY KEY (id),
    CONSTRAINT uq_rearois_id_project_scope
        UNIQUE (id, project_id, consumer_contract, binding_set_id),
    CONSTRAINT uq_rearois_scope_sequence
        UNIQUE (
            project_id, consumer_contract, binding_set_id, snapshot_sequence
        ),
    CONSTRAINT uq_rearois_scope_request
        UNIQUE (project_id, consumer_contract, binding_set_id, request_id),
    CONSTRAINT uq_rearois_supersedes_once UNIQUE (supersedes_snapshot_id),
    CONSTRAINT fk_rearois_supersedes_same_scope
        FOREIGN KEY (
            supersedes_snapshot_id, project_id, consumer_contract, binding_set_id
        )
        REFERENCES research_evidence_automation_roi.
            research_evidence_automation_roi_input_snapshot(
            id, project_id, consumer_contract, binding_set_id
        ) ON DELETE RESTRICT,
    CONSTRAINT ck_rearois_fixed_contract CHECK (
        consumer_contract = 'deterministic_calculation'
        AND consumer_contract_version = 'automation_roi.evidence_input.v1'
        AND policy_identifier = 'automation_roi.evidence_use'
        AND policy_version = '1'
        AND evaluator_version = 'automation_roi.evidence_use.evaluator.v1'
        AND policy_fingerprint =
            'ca7aadce968c35f9839d79b61a4cbb62fe9bc05fcc692e6c773ee36ec4a13c9d'
        AND policy_parameters_json =
            '{"binding_record_must_be_current":true,"calculation_kind":"automation_roi","consumer_contract":"deterministic_calculation","does_not_satisfy":{"availability_status":[false],"consumer_disposition":["does_not_meet_contract"],"disposition_reasons":["contradiction_declared"],"drift_status":["material_drift"],"lineage_is_current":[false],"review_status":["rejected","needs_revision","withdrawn"]},"indeterminate":{"consumer_disposition":["indeterminate"],"drift_status":["not_assessed","indeterminate"],"freshness_status":["unknown"],"review_status":["not_assessed"]},"qualified":{"consumer_disposition":["qualified"],"freshness_status":["stale"]},"required_roles":["baseline_hours_per_period","post_automation_hours_per_period","fully_loaded_rate_per_hour","periods_per_year","annual_recurring_cost","one_time_implementation_cost"],"satisfies":{"availability_status":true,"consumer_disposition":"meets_contract","drift_status":"no_material_drift","freshness_status":"fresh","lineage_is_current":true,"review_status":"approved"},"status_precedence":["does_not_satisfy","indeterminate","qualified","satisfies"]}'::jsonb
        AND completeness_status = 'complete'
    ),
    CONSTRAINT ck_rearois_status CHECK (
        policy_evaluation_status IN (
            'satisfies', 'qualified', 'does_not_satisfy', 'indeterminate'
        )
    ),
    CONSTRAINT ck_rearois_json_shapes CHECK (
        jsonb_typeof(policy_parameters_json) = 'object'
        AND jsonb_typeof(evaluation_reasons_json) = 'array'
        AND jsonb_array_length(evaluation_reasons_json) >= 1
    ),
    CONSTRAINT ck_rearois_nonblank CHECK (
        binding_set_id !~ '^[[:space:]]*$'
        AND request_id !~ '^[[:space:]]*$'
        AND evaluated_by !~ '^[[:space:]]*$'
    ),
    CONSTRAINT ck_rearois_fingerprint CHECK (
        policy_fingerprint ~ '^[0-9a-f]{64}$'
    )
);

CREATE TABLE IF NOT EXISTS
research_evidence_automation_roi.
automation_roi_input_snapshot_sequence_allocator (
    project_id UUID NOT NULL,
    consumer_contract TEXT NOT NULL,
    binding_set_id TEXT NOT NULL,
    last_sequence INTEGER NOT NULL,
    CONSTRAINT pk_rearoisa
        PRIMARY KEY (project_id, consumer_contract, binding_set_id),
    CONSTRAINT ck_rearoisa_fixed_contract CHECK (
        consumer_contract = 'deterministic_calculation'
        AND binding_set_id !~ '^[[:space:]]*$'
    ),
    CONSTRAINT ck_rearoisa_sequence CHECK (last_sequence >= 0)
);

CREATE TABLE IF NOT EXISTS
research_evidence_automation_roi.
research_evidence_automation_roi_input_snapshot_binding (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    snapshot_id UUID NOT NULL,
    project_id UUID NOT NULL,
    consumer_contract TEXT NOT NULL,
    binding_set_id TEXT NOT NULL,
    input_role TEXT NOT NULL,
    binding_record_id UUID NOT NULL,
    linked_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT research_evidence_automation_roi_input_snapshot_binding_pkey
        PRIMARY KEY (id),
    CONSTRAINT uq_rearoisb_snapshot_role UNIQUE (snapshot_id, input_role),
    CONSTRAINT uq_rearoisb_snapshot_binding
        UNIQUE (snapshot_id, binding_record_id),
    CONSTRAINT fk_rearoisb_snapshot_project
        FOREIGN KEY (
            snapshot_id, project_id, consumer_contract, binding_set_id
        )
        REFERENCES research_evidence_automation_roi.
            research_evidence_automation_roi_input_snapshot(
            id, project_id, consumer_contract, binding_set_id
        ) ON DELETE RESTRICT,
    CONSTRAINT ck_rearoisb_role CHECK (
        input_role IN (
            'baseline_hours_per_period',
            'post_automation_hours_per_period',
            'fully_loaded_rate_per_hour',
            'periods_per_year',
            'annual_recurring_cost',
            'one_time_implementation_cost'
        )
    ),
    CONSTRAINT ck_rearoisb_nonblank CHECK (
        consumer_contract = 'deterministic_calculation'
        AND binding_set_id !~ '^[[:space:]]*$'
    )
);

DO $upstream_foreign_keys$
DECLARE
    v_upstream_schema text;
    v_upstream_schema_count integer;
    v_projects_relation text;
    v_project_id_column text;
    v_project_target_count integer;
BEGIN
    SELECT count(*), min(upstream_namespace.nspname::text)
    INTO v_upstream_schema_count, v_upstream_schema
    FROM pg_catalog.pg_constraint constraint_info
    JOIN pg_catalog.pg_class binding_relation
      ON binding_relation.oid = constraint_info.conrelid
    JOIN pg_catalog.pg_namespace binding_namespace
      ON binding_namespace.oid = binding_relation.relnamespace
    JOIN pg_catalog.pg_class upstream_relation
      ON upstream_relation.oid = constraint_info.confrelid
    JOIN pg_catalog.pg_namespace upstream_namespace
      ON upstream_namespace.oid = upstream_relation.relnamespace
    WHERE constraint_info.conname = 'fk_recib_calculation_input_role'
      AND constraint_info.contype = 'f'
      AND constraint_info.connamespace = binding_namespace.oid
      AND binding_relation.relname =
          'research_evidence_consumer_input_binding'
      AND binding_relation.relkind = 'r'
      AND upstream_relation.relname = 'approved_calculation_input'
      AND upstream_relation.relkind = 'r'
      AND upstream_namespace.oid = binding_namespace.oid
      AND pg_catalog.octet_length(upstream_namespace.nspname::text) <= 63;
    IF v_upstream_schema_count <> 1 OR v_upstream_schema IS NULL THEN
        RAISE EXCEPTION
            'v59 requires exactly one validated upstream schema'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT count(*),
           min(project_relation.relname::text),
           min(project_attribute.attname::text)
    INTO v_project_target_count, v_projects_relation, v_project_id_column
    FROM pg_catalog.pg_constraint project_foreign_key
    JOIN pg_catalog.pg_class binding_relation
      ON binding_relation.oid = project_foreign_key.conrelid
    JOIN pg_catalog.pg_namespace binding_namespace
      ON binding_namespace.oid = binding_relation.relnamespace
    JOIN pg_catalog.pg_class project_relation
      ON project_relation.oid = project_foreign_key.confrelid
    JOIN pg_catalog.pg_namespace project_namespace
      ON project_namespace.oid = project_relation.relnamespace
    JOIN pg_catalog.pg_constraint project_primary_key
      ON project_primary_key.conrelid = project_relation.oid
     AND project_primary_key.connamespace = project_namespace.oid
     AND project_primary_key.contype = 'p'
     AND project_primary_key.conkey = project_foreign_key.confkey
    JOIN pg_catalog.pg_attribute project_attribute
      ON project_attribute.attrelid = project_relation.oid
     AND project_attribute.attnum = project_foreign_key.confkey[1]
     AND NOT project_attribute.attisdropped
    WHERE project_foreign_key.conname = 'fk_recib_project'
      AND project_foreign_key.contype = 'f'
      AND project_foreign_key.connamespace = binding_namespace.oid
      AND binding_namespace.nspname = v_upstream_schema
      AND binding_relation.relname =
          'research_evidence_consumer_input_binding'
      AND binding_relation.relkind = 'r'
      AND project_relation.relkind = 'r'
      AND project_namespace.oid = binding_namespace.oid
      AND pg_catalog.octet_length(project_relation.relname::text) <= 63
      AND pg_catalog.octet_length(project_attribute.attname::text) <= 63
      AND pg_catalog.array_length(project_foreign_key.confkey, 1) = 1;
    IF v_project_target_count <> 1
       OR v_projects_relation IS NULL
       OR v_project_id_column IS NULL THEN
        RAISE EXCEPTION
            'v59 requires exactly one validated upstream project target'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_constraint constraint_info
        JOIN pg_catalog.pg_namespace namespace
          ON namespace.oid = constraint_info.connamespace
        WHERE namespace.nspname = 'research_evidence_automation_roi'
          AND constraint_info.conname = 'fk_rearois_project'
          AND constraint_info.conrelid =
              'research_evidence_automation_roi.'
              'research_evidence_automation_roi_input_snapshot'::regclass
    ) THEN
        EXECUTE format(
            'ALTER TABLE research_evidence_automation_roi.
                 research_evidence_automation_roi_input_snapshot
             ADD CONSTRAINT fk_rearois_project
             FOREIGN KEY (project_id)
             REFERENCES %I.%I(%I) ON DELETE RESTRICT',
            v_upstream_schema, v_projects_relation, v_project_id_column
        );
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_constraint constraint_info
        JOIN pg_catalog.pg_namespace namespace
          ON namespace.oid = constraint_info.connamespace
        WHERE namespace.nspname = 'research_evidence_automation_roi'
          AND constraint_info.conname = 'fk_rearoisa_project'
          AND constraint_info.conrelid =
              'research_evidence_automation_roi.'
              'automation_roi_input_snapshot_sequence_allocator'
              ::regclass
    ) THEN
        EXECUTE format(
            'ALTER TABLE research_evidence_automation_roi.
                 automation_roi_input_snapshot_sequence_allocator
             ADD CONSTRAINT fk_rearoisa_project
             FOREIGN KEY (project_id)
             REFERENCES %I.%I(%I) ON DELETE RESTRICT',
            v_upstream_schema, v_projects_relation, v_project_id_column
        );
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_constraint constraint_info
        JOIN pg_catalog.pg_namespace namespace
          ON namespace.oid = constraint_info.connamespace
        WHERE namespace.nspname = 'research_evidence_automation_roi'
          AND constraint_info.conname = 'fk_rearoisb_binding_scope'
          AND constraint_info.conrelid =
              'research_evidence_automation_roi.'
              'research_evidence_automation_roi_input_snapshot_binding'::regclass
    ) THEN
        EXECUTE format(
            'ALTER TABLE research_evidence_automation_roi.
                 research_evidence_automation_roi_input_snapshot_binding
             ADD CONSTRAINT fk_rearoisb_binding_scope
             FOREIGN KEY (
                 binding_record_id, project_id, consumer_contract,
                 binding_set_id, input_role
             )
             REFERENCES %I.research_evidence_consumer_input_binding(
                 id, project_id, consumer_contract, binding_set_id, input_key
             ) ON DELETE RESTRICT',
            v_upstream_schema
        );
    END IF;
END;
$upstream_foreign_keys$;

CREATE INDEX IF NOT EXISTS idx_rearois_scope_sequence
    ON research_evidence_automation_roi.
        research_evidence_automation_roi_input_snapshot(
        project_id, consumer_contract, binding_set_id, snapshot_sequence
    );
CREATE INDEX IF NOT EXISTS idx_rearoisb_binding
    ON research_evidence_automation_roi.
        research_evidence_automation_roi_input_snapshot_binding(
        project_id, binding_record_id
    );

CREATE OR REPLACE FUNCTION research_evidence_automation_roi.
research_evidence_prepare_automation_roi_snapshot()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, research_evidence_automation_roi, pg_temp
AS $function_body$
DECLARE
    v_owner oid;
    v_last integer;
    v_count integer;
    v_min integer;
    v_max integer;
    v_current uuid;
BEGIN
    SELECT relowner INTO v_owner
    FROM pg_catalog.pg_class
    WHERE oid = TG_RELID;
    IF (
        SELECT oid
        FROM pg_catalog.pg_roles
        WHERE rolname = current_user
    ) <> v_owner THEN
        RAISE EXCEPTION
            'Automation ROI snapshots must use the controlled database write function'
            USING ERRCODE = '42501';
    END IF;
    IF NEW.snapshot_sequence IS NOT NULL
       OR NEW.supersedes_snapshot_id IS NOT NULL THEN
        RAISE EXCEPTION 'snapshot sequence and predecessor are server-assigned'
            USING ERRCODE = '23514';
    END IF;
    NEW.evaluated_at := clock_timestamp();

    EXECUTE format(
        'INSERT INTO %I.automation_roi_input_snapshot_sequence_allocator
             (project_id, consumer_contract, binding_set_id, last_sequence)
         VALUES ($1, $2, $3, 0)
         ON CONFLICT (project_id, consumer_contract, binding_set_id) DO NOTHING',
        TG_TABLE_SCHEMA
    ) USING NEW.project_id, NEW.consumer_contract, NEW.binding_set_id;
    EXECUTE format(
        'SELECT last_sequence
         FROM %I.automation_roi_input_snapshot_sequence_allocator
         WHERE project_id = $1 AND consumer_contract = $2 AND binding_set_id = $3
         FOR UPDATE',
        TG_TABLE_SCHEMA
    ) INTO v_last
    USING NEW.project_id, NEW.consumer_contract, NEW.binding_set_id;
    EXECUTE format(
        'SELECT count(*)::integer, min(snapshot_sequence), max(snapshot_sequence)
         FROM %I.research_evidence_automation_roi_input_snapshot
         WHERE project_id = $1 AND consumer_contract = $2 AND binding_set_id = $3',
        TG_TABLE_SCHEMA
    ) INTO v_count, v_min, v_max
    USING NEW.project_id, NEW.consumer_contract, NEW.binding_set_id;
    IF v_count <> v_last
       OR (v_last = 0 AND (v_min IS NOT NULL OR v_max IS NOT NULL))
       OR (v_last > 0 AND (v_min <> 1 OR v_max <> v_last)) THEN
        RAISE EXCEPTION 'malformed Automation ROI snapshot chain'
            USING ERRCODE = '23514';
    END IF;
    IF v_last > 0 THEN
        EXECUTE format(
            'SELECT id
             FROM %I.research_evidence_automation_roi_input_snapshot
             WHERE project_id = $1 AND consumer_contract = $2
               AND binding_set_id = $3 AND snapshot_sequence = $4',
            TG_TABLE_SCHEMA
        ) INTO v_current
        USING NEW.project_id, NEW.consumer_contract, NEW.binding_set_id, v_last;
        IF v_current IS NULL THEN
            RAISE EXCEPTION 'malformed Automation ROI snapshot predecessor'
                USING ERRCODE = '23514';
        END IF;
        NEW.supersedes_snapshot_id := v_current;
    END IF;
    NEW.snapshot_sequence := v_last + 1;
    EXECUTE format(
        'UPDATE %I.automation_roi_input_snapshot_sequence_allocator
         SET last_sequence = $4
         WHERE project_id = $1 AND consumer_contract = $2 AND binding_set_id = $3',
        TG_TABLE_SCHEMA
    ) USING NEW.project_id, NEW.consumer_contract, NEW.binding_set_id,
            NEW.snapshot_sequence;
    RETURN NEW;
END;
$function_body$;

CREATE OR REPLACE FUNCTION research_evidence_automation_roi.
research_evidence_evaluate_automation_roi_bindings(
    p_project_id uuid,
    p_binding_set_id text,
    p_binding_record_ids uuid[],
    p_freshness_as_of timestamptz,
    p_evaluated_at timestamptz
)
RETURNS TABLE(policy_status text, reason_codes jsonb)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, research_evidence_automation_roi, pg_temp
AS $function_body$
DECLARE
    binding record;
    v_upstream_schema text;
    v_count integer := 0;
    v_roles text[] := ARRAY[]::text[];
    v_hard text[] := ARRAY[]::text[];
    v_indeterminate text[] := ARRAY[]::text[];
    v_qualified text[] := ARRAY[]::text[];
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

    FOR binding IN EXECUTE format(
        $query$
        SELECT selected.*,
               EXISTS (
                   SELECT 1
                   FROM %I.research_evidence_consumer_input_binding successor
                   WHERE successor.supersedes_binding_id = selected.id
                     AND successor.evaluated_at <= $3
               ) AS has_successor
        FROM %I.research_evidence_consumer_input_binding selected
        WHERE selected.project_id = $1
          AND selected.id = ANY($2)
        ORDER BY CASE selected.input_key
            WHEN 'baseline_hours_per_period' THEN 1
            WHEN 'post_automation_hours_per_period' THEN 2
            WHEN 'fully_loaded_rate_per_hour' THEN 3
            WHEN 'periods_per_year' THEN 4
            WHEN 'annual_recurring_cost' THEN 5
            WHEN 'one_time_implementation_cost' THEN 6
            ELSE 7
        END, selected.input_key, selected.id
        $query$,
        v_upstream_schema, v_upstream_schema
    ) USING p_project_id, p_binding_record_ids, p_evaluated_at
    LOOP
        v_count := v_count + 1;
        v_roles := array_append(v_roles, binding.input_key);
        IF binding.project_id IS DISTINCT FROM p_project_id THEN
            v_hard := array_append(
                v_hard, 'role:' || binding.input_key || ':project_mismatch'
            );
        END IF;
        IF binding.consumer_contract IS DISTINCT FROM
           'deterministic_calculation' THEN
            v_hard := array_append(
                v_hard,
                'role:' || binding.input_key || ':consumer_contract_mismatch'
            );
        END IF;
        IF binding.consumer_contract_version IS DISTINCT FROM
           'automation_roi.evidence_input.v1' THEN
            v_hard := array_append(
                v_hard,
                'role:' || binding.input_key
                    || ':consumer_contract_version_mismatch'
            );
        END IF;
        IF binding.binding_set_id IS DISTINCT FROM p_binding_set_id THEN
            v_hard := array_append(
                v_hard, 'role:' || binding.input_key || ':binding_set_mismatch'
            );
        END IF;
        IF binding.calculation_kind IS DISTINCT FROM 'automation_roi' THEN
            v_hard := array_append(
                v_hard,
                'role:' || binding.input_key || ':calculation_kind_mismatch'
            );
        END IF;
        IF binding.policy_identifier IS DISTINCT FROM
           'automation_roi.evidence_use' THEN
            v_hard := array_append(
                v_hard,
                'role:' || binding.input_key || ':policy_identifier_mismatch'
            );
        END IF;
        IF binding.policy_version IS DISTINCT FROM '1' THEN
            v_hard := array_append(
                v_hard, 'role:' || binding.input_key || ':policy_version_mismatch'
            );
        END IF;
        IF binding.policy_parameters_json IS DISTINCT FROM
           '{"binding_record_must_be_current":true,"calculation_kind":"automation_roi","consumer_contract":"deterministic_calculation","does_not_satisfy":{"availability_status":[false],"consumer_disposition":["does_not_meet_contract"],"disposition_reasons":["contradiction_declared"],"drift_status":["material_drift"],"lineage_is_current":[false],"review_status":["rejected","needs_revision","withdrawn"]},"indeterminate":{"consumer_disposition":["indeterminate"],"drift_status":["not_assessed","indeterminate"],"freshness_status":["unknown"],"review_status":["not_assessed"]},"qualified":{"consumer_disposition":["qualified"],"freshness_status":["stale"]},"required_roles":["baseline_hours_per_period","post_automation_hours_per_period","fully_loaded_rate_per_hour","periods_per_year","annual_recurring_cost","one_time_implementation_cost"],"satisfies":{"availability_status":true,"consumer_disposition":"meets_contract","drift_status":"no_material_drift","freshness_status":"fresh","lineage_is_current":true,"review_status":"approved"},"status_precedence":["does_not_satisfy","indeterminate","qualified","satisfies"]}'::jsonb
        THEN
            v_hard := array_append(
                v_hard,
                'role:' || binding.input_key || ':policy_parameters_mismatch'
            );
        END IF;
        IF binding.policy_fingerprint IS DISTINCT FROM
           'ca7aadce968c35f9839d79b61a4cbb62fe9bc05fcc692e6c773ee36ec4a13c9d'
        THEN
            v_hard := array_append(
                v_hard,
                'role:' || binding.input_key || ':policy_fingerprint_mismatch'
            );
        END IF;
        IF binding.evaluator_version IS DISTINCT FROM
           'automation_roi.evidence_use.evaluator.v1' THEN
            v_hard := array_append(
                v_hard,
                'role:' || binding.input_key || ':evaluator_version_mismatch'
            );
        END IF;
        IF binding.freshness_as_of IS DISTINCT FROM p_freshness_as_of THEN
            v_hard := array_append(
                v_hard,
                'role:' || binding.input_key || ':freshness_as_of_mismatch'
            );
        END IF;
        IF binding.claim_intake_item_id IS NOT NULL
           OR binding.claim_support_assessment_id IS NOT NULL
           OR binding.locator_resolution IS NOT NULL
           OR binding.evidence_linkage IS NOT NULL
           OR binding.semantic_relationship IS NOT NULL THEN
            v_hard := array_append(
                v_hard, 'role:' || binding.input_key || ':claim_semantics_present'
            );
        END IF;
        IF binding.has_successor THEN
            v_hard := array_append(
                v_hard,
                'role:' || binding.input_key || ':binding_record_superseded'
            );
        END IF;
        IF NOT binding.availability_status THEN
            v_hard := array_append(
                v_hard, 'role:' || binding.input_key || ':evidence_unavailable'
            );
        END IF;
        IF NOT binding.lineage_is_current THEN
            v_hard := array_append(
                v_hard, 'role:' || binding.input_key || ':lineage_not_current'
            );
        END IF;
        IF binding.review_status IN ('rejected', 'needs_revision', 'withdrawn')
        THEN
            v_hard := array_append(
                v_hard,
                'role:' || binding.input_key || ':review_' || binding.review_status
            );
        END IF;
        IF binding.drift_status = 'material_drift' THEN
            v_hard := array_append(
                v_hard, 'role:' || binding.input_key || ':material_drift'
            );
        END IF;
        IF binding.consumer_disposition = 'does_not_meet_contract' THEN
            v_hard := array_append(
                v_hard,
                'role:' || binding.input_key
                    || ':consumer_does_not_meet_contract'
            );
        END IF;
        IF binding.disposition_reasons_json ? 'contradiction_declared' THEN
            v_hard := array_append(
                v_hard, 'role:' || binding.input_key || ':contradiction_declared'
            );
        END IF;
        IF binding.review_status = 'not_assessed' THEN
            v_indeterminate := array_append(
                v_indeterminate,
                'role:' || binding.input_key || ':review_not_assessed'
            );
        END IF;
        IF binding.freshness_status = 'unknown' THEN
            v_indeterminate := array_append(
                v_indeterminate,
                'role:' || binding.input_key || ':freshness_unknown'
            );
        END IF;
        IF binding.drift_status IN ('not_assessed', 'indeterminate') THEN
            v_indeterminate := array_append(
                v_indeterminate,
                'role:' || binding.input_key || ':drift_' || binding.drift_status
            );
        END IF;
        IF binding.consumer_disposition = 'indeterminate' THEN
            v_indeterminate := array_append(
                v_indeterminate,
                'role:' || binding.input_key || ':consumer_indeterminate'
            );
        END IF;
        IF binding.freshness_status = 'stale' THEN
            v_qualified := array_append(
                v_qualified, 'role:' || binding.input_key || ':freshness_stale'
            );
        END IF;
        IF binding.consumer_disposition = 'qualified' THEN
            v_qualified := array_append(
                v_qualified, 'role:' || binding.input_key || ':consumer_qualified'
            );
        END IF;
    END LOOP;

    IF v_count <> 6
       OR (SELECT count(DISTINCT role) FROM unnest(v_roles) role) <> 6
       OR NOT v_roles @> ARRAY[
           'baseline_hours_per_period',
           'post_automation_hours_per_period',
           'fully_loaded_rate_per_hour',
           'periods_per_year',
           'annual_recurring_cost',
           'one_time_implementation_cost'
       ]::text[] THEN
        RAISE EXCEPTION
            'selected binding IDs must resolve to exactly the six canonical ROI roles'
            USING ERRCODE = '23514';
    END IF;
    IF cardinality(v_hard) > 0 THEN
        policy_status := 'does_not_satisfy';
        reason_codes := to_jsonb(v_hard);
    ELSIF cardinality(v_indeterminate) > 0 THEN
        policy_status := 'indeterminate';
        reason_codes := to_jsonb(v_indeterminate);
    ELSIF cardinality(v_qualified) > 0 THEN
        policy_status := 'qualified';
        reason_codes := to_jsonb(v_qualified);
    ELSE
        policy_status := 'satisfies';
        reason_codes := '["policy_satisfied"]'::jsonb;
    END IF;
    RETURN NEXT;
END;
$function_body$;

CREATE OR REPLACE FUNCTION
research_evidence_automation_roi.
research_evidence_prepare_automation_roi_snapshot_binding()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, research_evidence_automation_roi, pg_temp
AS $function_body$
DECLARE
    v_owner oid;
    v_linked_at timestamptz;
BEGIN
    SELECT relowner INTO v_owner
    FROM pg_catalog.pg_class
    WHERE oid = TG_RELID;
    IF (
        SELECT oid
        FROM pg_catalog.pg_roles
        WHERE rolname = current_user
    ) <> v_owner THEN
        RAISE EXCEPTION
            'Automation ROI snapshot bindings must use the controlled database write function'
            USING ERRCODE = '42501';
    END IF;
    SELECT evaluated_at INTO v_linked_at
    FROM research_evidence_automation_roi.
        research_evidence_automation_roi_input_snapshot
    WHERE id = NEW.snapshot_id
      AND project_id = NEW.project_id
      AND consumer_contract = NEW.consumer_contract
      AND binding_set_id = NEW.binding_set_id;
    IF v_linked_at IS NULL THEN
        RAISE EXCEPTION 'Automation ROI snapshot binding has no scoped header'
            USING ERRCODE = '23514';
    END IF;
    NEW.linked_at := v_linked_at;
    RETURN NEW;
END;
$function_body$;

CREATE OR REPLACE FUNCTION
research_evidence_automation_roi.
research_evidence_validate_automation_roi_snapshot(p_snapshot_id uuid)
RETURNS void
LANGUAGE plpgsql
SET search_path = pg_catalog, research_evidence_automation_roi, pg_temp
AS $function_body$
DECLARE
    v_upstream_schema text;
    v_snapshot research_evidence_automation_roi.
        research_evidence_automation_roi_input_snapshot%ROWTYPE;
    v_binding_ids uuid[];
    v_count integer;
    v_roles integer;
    v_invalid integer;
    v_status text;
    v_reasons jsonb;
BEGIN
    SELECT * INTO STRICT v_snapshot
    FROM research_evidence_automation_roi.
        research_evidence_automation_roi_input_snapshot
    WHERE id = p_snapshot_id;
    SELECT count(*)::integer, count(DISTINCT input_role)::integer,
           array_agg(binding_record_id ORDER BY input_role)
    INTO v_count, v_roles, v_binding_ids
    FROM research_evidence_automation_roi.
        research_evidence_automation_roi_input_snapshot_binding
    WHERE snapshot_id = p_snapshot_id;
    IF v_count <> 6 OR v_roles <> 6 THEN
        RAISE EXCEPTION 'Automation ROI snapshot requires exactly six roles'
            USING ERRCODE = '23514';
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
        SELECT count(*)::integer
        FROM research_evidence_automation_roi.
            research_evidence_automation_roi_input_snapshot_binding child
        JOIN research_evidence_automation_roi.
            research_evidence_automation_roi_input_snapshot snapshot
          ON snapshot.id = child.snapshot_id
        JOIN %I.research_evidence_consumer_input_binding binding
          ON binding.id = child.binding_record_id
        LEFT JOIN %I.approved_calculation_input input
          ON input.id = binding.approved_calculation_input_id
         AND input.project_id = binding.project_id
         AND input.input_role = binding.input_key
        WHERE child.snapshot_id = $1
          AND (
              child.project_id <> snapshot.project_id
              OR child.binding_set_id <> snapshot.binding_set_id
              OR binding.project_id <> snapshot.project_id
              OR binding.consumer_contract <> 'deterministic_calculation'
              OR binding.binding_set_id <> snapshot.binding_set_id
              OR binding.calculation_kind <> 'automation_roi'
              OR binding.input_key <> child.input_role
              OR input.id IS NULL
              OR input.calculation_kind <> 'automation_roi'
              OR child.linked_at <> snapshot.evaluated_at
              OR binding.claim_intake_item_id IS NOT NULL
              OR binding.claim_support_assessment_id IS NOT NULL
              OR binding.semantic_relationship IS NOT NULL
          )
        $query$,
        v_upstream_schema, v_upstream_schema
    ) INTO v_invalid USING p_snapshot_id;
    IF v_invalid <> 0 THEN
        RAISE EXCEPTION 'Automation ROI snapshot binding scope is invalid'
            USING ERRCODE = '23514';
    END IF;
    SELECT policy_status, reason_codes INTO v_status, v_reasons
    FROM research_evidence_automation_roi.
        research_evidence_evaluate_automation_roi_bindings(
        v_snapshot.project_id,
        v_snapshot.binding_set_id,
        v_binding_ids,
        v_snapshot.freshness_as_of,
        v_snapshot.evaluated_at
    );
    IF v_snapshot.completeness_status <> 'complete'
       OR v_snapshot.policy_evaluation_status IS DISTINCT FROM v_status
       OR v_snapshot.evaluation_reasons_json IS DISTINCT FROM v_reasons THEN
        RAISE EXCEPTION
            'Automation ROI snapshot policy status or reasons are not authoritative'
            USING ERRCODE = '23514';
    END IF;
END;
$function_body$;

CREATE OR REPLACE FUNCTION research_evidence_automation_roi.
research_evidence_assert_automation_roi_snapshot()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, research_evidence_automation_roi, pg_temp
AS $function_body$
BEGIN
    PERFORM research_evidence_automation_roi.
        research_evidence_validate_automation_roi_snapshot(NEW.id);
    RETURN NULL;
END;
$function_body$;

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
AS $function_body$
DECLARE
    lock_row record;
    v_upstream_schema text;
    v_locked integer := 0;
    v_snapshot_id uuid;
    v_status text;
    v_reasons jsonb;
    v_evaluated_at timestamptz;
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

    IF cardinality(p_binding_record_ids) <> 6
       OR (
           SELECT count(DISTINCT selected_id)
           FROM unnest(p_binding_record_ids) selected_id
       ) <> 6 THEN
        RAISE EXCEPTION 'exactly six distinct binding-record IDs are required'
            USING ERRCODE = '23514';
    END IF;
    IF p_binding_set_id IS NULL OR btrim(p_binding_set_id) = ''
       OR p_request_id IS NULL OR btrim(p_request_id) = ''
       OR p_freshness_as_of IS NULL
       OR p_evaluated_by IS NULL OR btrim(p_evaluated_by) = '' THEN
        RAISE EXCEPTION 'snapshot request identity and freshness must be complete'
            USING ERRCODE = '23514';
    END IF;

    FOR lock_row IN EXECUTE format(
        $query$
        SELECT allocator.project_id, allocator.consumer_contract,
               allocator.binding_set_id, allocator.input_key,
               allocator.evidence_intake_item_id, selected.id
        FROM %I.research_evidence_consumer_input_binding selected
        JOIN %I.research_evidence_consumer_input_binding_sequence_allocator allocator
          ON allocator.project_id = selected.project_id
         AND allocator.consumer_contract = selected.consumer_contract
         AND allocator.binding_set_id = selected.binding_set_id
         AND allocator.input_key = selected.input_key
         AND allocator.evidence_intake_item_id =
             selected.evidence_intake_item_id
        WHERE selected.project_id = $1
          AND selected.id = ANY($2)
        ORDER BY allocator.project_id, allocator.consumer_contract,
                 allocator.binding_set_id, allocator.input_key,
                 allocator.evidence_intake_item_id, selected.id
        FOR UPDATE OF allocator
        $query$,
        v_upstream_schema, v_upstream_schema
    ) USING p_project_id, p_binding_record_ids
    LOOP
        v_locked := v_locked + 1;
    END LOOP;
    IF v_locked <> 6 THEN
        RAISE EXCEPTION
            'selected binding IDs do not resolve to six locked R1.6 inputs'
            USING ERRCODE = '23514';
    END IF;

    v_evaluated_at := clock_timestamp();
    SELECT policy_status, reason_codes INTO v_status, v_reasons
    FROM research_evidence_automation_roi.
        research_evidence_evaluate_automation_roi_bindings(
        p_project_id,
        p_binding_set_id,
        p_binding_record_ids,
        p_freshness_as_of,
        v_evaluated_at
    );
    INSERT INTO research_evidence_automation_roi.
        research_evidence_automation_roi_input_snapshot
        (project_id, consumer_contract, consumer_contract_version,
         binding_set_id, request_id, policy_identifier, policy_version,
         policy_parameters_json, policy_fingerprint, evaluator_version,
         freshness_as_of, completeness_status, policy_evaluation_status,
         evaluation_reasons_json, evaluated_by)
    VALUES (
        p_project_id,
        'deterministic_calculation',
        'automation_roi.evidence_input.v1',
        p_binding_set_id,
        p_request_id,
        'automation_roi.evidence_use',
        '1',
        '{"binding_record_must_be_current":true,"calculation_kind":"automation_roi","consumer_contract":"deterministic_calculation","does_not_satisfy":{"availability_status":[false],"consumer_disposition":["does_not_meet_contract"],"disposition_reasons":["contradiction_declared"],"drift_status":["material_drift"],"lineage_is_current":[false],"review_status":["rejected","needs_revision","withdrawn"]},"indeterminate":{"consumer_disposition":["indeterminate"],"drift_status":["not_assessed","indeterminate"],"freshness_status":["unknown"],"review_status":["not_assessed"]},"qualified":{"consumer_disposition":["qualified"],"freshness_status":["stale"]},"required_roles":["baseline_hours_per_period","post_automation_hours_per_period","fully_loaded_rate_per_hour","periods_per_year","annual_recurring_cost","one_time_implementation_cost"],"satisfies":{"availability_status":true,"consumer_disposition":"meets_contract","drift_status":"no_material_drift","freshness_status":"fresh","lineage_is_current":true,"review_status":"approved"},"status_precedence":["does_not_satisfy","indeterminate","qualified","satisfies"]}'::jsonb,
        'ca7aadce968c35f9839d79b61a4cbb62fe9bc05fcc692e6c773ee36ec4a13c9d',
        'automation_roi.evidence_use.evaluator.v1',
        p_freshness_as_of,
        'complete',
        v_status,
        v_reasons,
        p_evaluated_by
    )
    RETURNING id INTO v_snapshot_id;
    EXECUTE format(
        $query$
        INSERT INTO research_evidence_automation_roi.
            research_evidence_automation_roi_input_snapshot_binding
            (snapshot_id, project_id, consumer_contract, binding_set_id,
             input_role, binding_record_id, linked_at)
        SELECT $1, selected.project_id, selected.consumer_contract,
               selected.binding_set_id, selected.input_key, selected.id, NULL
        FROM %I.research_evidence_consumer_input_binding selected
        WHERE selected.project_id = $2
          AND selected.id = ANY($3)
        ORDER BY CASE selected.input_key
            WHEN 'baseline_hours_per_period' THEN 1
            WHEN 'post_automation_hours_per_period' THEN 2
            WHEN 'fully_loaded_rate_per_hour' THEN 3
            WHEN 'periods_per_year' THEN 4
            WHEN 'annual_recurring_cost' THEN 5
            WHEN 'one_time_implementation_cost' THEN 6
            ELSE 7
        END
        $query$,
        v_upstream_schema
    ) USING v_snapshot_id, p_project_id, p_binding_record_ids;
    PERFORM research_evidence_automation_roi.
        research_evidence_validate_automation_roi_snapshot(v_snapshot_id);
    RETURN v_snapshot_id;
END;
$function_body$;

ALTER TABLE research_evidence_automation_roi.
    research_evidence_automation_roi_input_snapshot
    OWNER TO workflow_research_evidence_owner;
ALTER TABLE research_evidence_automation_roi.
    research_evidence_automation_roi_input_snapshot_binding
    OWNER TO workflow_research_evidence_owner;
ALTER TABLE research_evidence_automation_roi.
    automation_roi_input_snapshot_sequence_allocator
    OWNER TO workflow_research_evidence_owner;
ALTER FUNCTION research_evidence_automation_roi.
    research_evidence_prepare_automation_roi_snapshot()
    OWNER TO workflow_research_evidence_owner;
ALTER FUNCTION research_evidence_automation_roi.
    research_evidence_prepare_automation_roi_snapshot_binding()
    OWNER TO workflow_research_evidence_owner;
ALTER FUNCTION research_evidence_automation_roi.
    research_evidence_evaluate_automation_roi_bindings(
    uuid, text, uuid[], timestamptz, timestamptz
) OWNER TO workflow_research_evidence_owner;
ALTER FUNCTION research_evidence_automation_roi.
    research_evidence_validate_automation_roi_snapshot(uuid)
    OWNER TO workflow_research_evidence_owner;
ALTER FUNCTION research_evidence_automation_roi.
    research_evidence_assert_automation_roi_snapshot()
    OWNER TO workflow_research_evidence_owner;
ALTER FUNCTION research_evidence_automation_roi.
    research_evidence_create_automation_roi_snapshot(
    uuid, text, uuid[], text, timestamptz, text
) OWNER TO workflow_research_evidence_owner;

DO $triggers$
DECLARE
    v_upstream_schema text;
    v_upstream_schema_count integer;
BEGIN
    SELECT count(*), min(upstream_namespace.nspname::text)
    INTO v_upstream_schema_count, v_upstream_schema
    FROM pg_catalog.pg_constraint constraint_info
    JOIN pg_catalog.pg_class binding_relation
      ON binding_relation.oid = constraint_info.conrelid
    JOIN pg_catalog.pg_namespace binding_namespace
      ON binding_namespace.oid = binding_relation.relnamespace
    JOIN pg_catalog.pg_class upstream_relation
      ON upstream_relation.oid = constraint_info.confrelid
    JOIN pg_catalog.pg_namespace upstream_namespace
      ON upstream_namespace.oid = upstream_relation.relnamespace
    WHERE constraint_info.conname = 'fk_recib_calculation_input_role'
      AND constraint_info.contype = 'f'
      AND constraint_info.connamespace = binding_namespace.oid
      AND binding_relation.relname =
          'research_evidence_consumer_input_binding'
      AND binding_relation.relkind = 'r'
      AND upstream_relation.relname = 'approved_calculation_input'
      AND upstream_relation.relkind = 'r'
      AND upstream_namespace.oid = binding_namespace.oid
      AND pg_catalog.octet_length(upstream_namespace.nspname::text) <= 63;
    IF v_upstream_schema_count <> 1 OR v_upstream_schema IS NULL THEN
        RAISE EXCEPTION
            'v59 requires exactly one validated upstream schema'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_trigger
        WHERE tgname = 'trg_rearoisb_prepare_insert'
          AND tgrelid =
              'research_evidence_automation_roi.'
              'research_evidence_automation_roi_input_snapshot_binding'::regclass
          AND NOT tgisinternal
    ) THEN
        CREATE TRIGGER trg_rearoisb_prepare_insert
            BEFORE INSERT
            ON research_evidence_automation_roi.
                research_evidence_automation_roi_input_snapshot_binding
            FOR EACH ROW
            EXECUTE FUNCTION research_evidence_automation_roi.
                research_evidence_prepare_automation_roi_snapshot_binding();
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_trigger
        WHERE tgname = 'trg_rearois_prepare_insert'
          AND tgrelid =
              'research_evidence_automation_roi.'
              'research_evidence_automation_roi_input_snapshot'::regclass
          AND NOT tgisinternal
    ) THEN
        CREATE TRIGGER trg_rearois_prepare_insert
            BEFORE INSERT
            ON research_evidence_automation_roi.
                research_evidence_automation_roi_input_snapshot
            FOR EACH ROW EXECUTE FUNCTION research_evidence_automation_roi.
                research_evidence_prepare_automation_roi_snapshot();
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_trigger
        WHERE tgname = 'trg_rearois_no_mutation'
          AND tgrelid =
              'research_evidence_automation_roi.'
              'research_evidence_automation_roi_input_snapshot'::regclass
          AND NOT tgisinternal
    ) THEN
        EXECUTE format(
            'CREATE TRIGGER trg_rearois_no_mutation
             BEFORE UPDATE OR DELETE
             ON research_evidence_automation_roi.
                 research_evidence_automation_roi_input_snapshot
             FOR EACH ROW EXECUTE FUNCTION %I.slicea_reject_mutation()',
            v_upstream_schema
        );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_trigger
        WHERE tgname = 'trg_rearoisb_no_mutation'
          AND tgrelid =
              'research_evidence_automation_roi.'
              'research_evidence_automation_roi_input_snapshot_binding'::regclass
          AND NOT tgisinternal
    ) THEN
        EXECUTE format(
            'CREATE TRIGGER trg_rearoisb_no_mutation
             BEFORE UPDATE OR DELETE
             ON research_evidence_automation_roi.
                 research_evidence_automation_roi_input_snapshot_binding
             FOR EACH ROW EXECUTE FUNCTION %I.slicea_reject_mutation()',
            v_upstream_schema
        );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_trigger
        WHERE tgname = 'trg_rearois_complete'
          AND tgrelid =
              'research_evidence_automation_roi.'
              'research_evidence_automation_roi_input_snapshot'::regclass
          AND NOT tgisinternal
    ) THEN
        CREATE CONSTRAINT TRIGGER trg_rearois_complete
            AFTER INSERT
            ON research_evidence_automation_roi.
                research_evidence_automation_roi_input_snapshot
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW
            EXECUTE FUNCTION research_evidence_automation_roi.
                research_evidence_assert_automation_roi_snapshot();
    END IF;
END;
$triggers$;

ALTER TABLE research_evidence_automation_roi.
    research_evidence_automation_roi_input_snapshot
    ENABLE ALWAYS TRIGGER trg_rearois_prepare_insert;
ALTER TABLE research_evidence_automation_roi.
    research_evidence_automation_roi_input_snapshot_binding
    ENABLE ALWAYS TRIGGER trg_rearoisb_prepare_insert;

REVOKE ALL ON TABLE
    research_evidence_automation_roi.
        research_evidence_automation_roi_input_snapshot,
    research_evidence_automation_roi.
        research_evidence_automation_roi_input_snapshot_binding,
    research_evidence_automation_roi.
        automation_roi_input_snapshot_sequence_allocator
    FROM PUBLIC, workflow_automation_roi_runtime;
REVOKE ALL ON FUNCTION
    research_evidence_automation_roi.
        research_evidence_prepare_automation_roi_snapshot(),
    research_evidence_automation_roi.
        research_evidence_prepare_automation_roi_snapshot_binding(),
    research_evidence_automation_roi.
        research_evidence_evaluate_automation_roi_bindings(
        uuid, text, uuid[], timestamptz, timestamptz
    ),
    research_evidence_automation_roi.
        research_evidence_validate_automation_roi_snapshot(uuid),
    research_evidence_automation_roi.
        research_evidence_assert_automation_roi_snapshot(),
    research_evidence_automation_roi.
        research_evidence_create_automation_roi_snapshot(
        uuid, text, uuid[], text, timestamptz, text
    )
    FROM PUBLIC, workflow_automation_roi_runtime;

GRANT SELECT ON TABLE
    research_evidence_automation_roi.
        research_evidence_automation_roi_input_snapshot,
    research_evidence_automation_roi.
        research_evidence_automation_roi_input_snapshot_binding
    TO workflow_automation_roi_runtime;
GRANT EXECUTE ON FUNCTION
    research_evidence_automation_roi.
        research_evidence_create_automation_roi_snapshot(
        uuid, text, uuid[], text, timestamptz, text
    )
    TO workflow_automation_roi_runtime;

ALTER DEFAULT PRIVILEGES FOR ROLE workflow_research_evidence_owner
    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

RESET ROLE;
SET ROLE workflow_migration_owner;

DO $remove_temporary_upstream_acl$
DECLARE
    v_upstream_schema text;
    v_upstream_schema_count integer;
BEGIN
    SELECT count(*), min(upstream_namespace.nspname::text)
    INTO v_upstream_schema_count, v_upstream_schema
    FROM pg_catalog.pg_constraint constraint_info
    JOIN pg_catalog.pg_class binding_relation
      ON binding_relation.oid = constraint_info.conrelid
    JOIN pg_catalog.pg_namespace binding_namespace
      ON binding_namespace.oid = binding_relation.relnamespace
    JOIN pg_catalog.pg_class upstream_relation
      ON upstream_relation.oid = constraint_info.confrelid
    JOIN pg_catalog.pg_namespace upstream_namespace
      ON upstream_namespace.oid = upstream_relation.relnamespace
    WHERE constraint_info.conname = 'fk_recib_calculation_input_role'
      AND constraint_info.contype = 'f'
      AND constraint_info.connamespace = binding_namespace.oid
      AND binding_relation.relname =
          'research_evidence_consumer_input_binding'
      AND binding_relation.relkind = 'r'
      AND upstream_relation.relname = 'approved_calculation_input'
      AND upstream_relation.relkind = 'r'
      AND upstream_namespace.oid = binding_namespace.oid
      AND pg_catalog.octet_length(upstream_namespace.nspname::text) <= 63;
    IF v_upstream_schema_count <> 1 OR v_upstream_schema IS NULL THEN
        RAISE EXCEPTION
            'v59 requires exactly one validated upstream schema'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    EXECUTE format(
        'REVOKE REFERENCES ON TABLE
             %I.research_evidence_consumer_input_binding
         FROM workflow_research_evidence_owner',
        v_upstream_schema
    );
    EXECUTE format(
        'REVOKE EXECUTE ON FUNCTION %I.slicea_reject_mutation()
         FROM workflow_research_evidence_owner',
        v_upstream_schema
    );
END;
$remove_temporary_upstream_acl$;

COMMIT;
