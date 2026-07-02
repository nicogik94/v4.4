-- v58 Research-Evidence Scenario-Input Evaluation Foundation (R1.7)
-- PostgreSQL-only, append-only provenance for structural expected-input
-- manifests and immutable evaluations of exact v57 scenario-input bindings.
-- A "satisfies" status means only structural and policy satisfaction.  It is
-- not truth, independence, semantic validation, Bayesian/run authorization,
-- posterior-update authority, or any other downstream-use authorization.
--
-- Apply manually after v47-v57.  Never apply automatically at application
-- start.  This migration does not create observations or scenario behavior.
-- v58 is an owner/admin-only schema foundation.  Ownership and the ability to
-- alter or drop its ALWAYS triggers are administrative authority, not ordinary
-- application access.  No runtime role is granted here;
-- a later authorized deployment/DBA wave must explicitly establish runtime-role
-- access.

BEGIN;

-- Reject partial or definition-drifted reapplication before creating objects.
DO $preflight$
DECLARE
    v_table_count integer;
    v_missing text;
    v_bad text;
BEGIN
    IF to_regclass('research_evidence_consumer_input_binding') IS NULL
       OR to_regclass(
           'research_evidence_consumer_input_binding_sequence_allocator'
       ) IS NULL THEN
        RAISE EXCEPTION 'v58 requires complete v57 binding foundation'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
    IF to_regprocedure('pg_catalog.sha256(bytea)') IS NULL THEN
        RAISE EXCEPTION 'v58 requires PostgreSQL SHA-256 support'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT count(*) INTO v_table_count
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = current_schema()
      AND c.relkind = 'r'
      AND c.relname = ANY (ARRAY[
          'research_evidence_scenario_input_manifest',
          'research_evidence_scenario_input_manifest_item',
          'research_evidence_scenario_input_evaluation',
          'research_evidence_scenario_input_evaluation_input',
          'research_evidence_scenario_input_evaluation_sequence_allocator'
      ]);
    IF v_table_count = 0
       AND NOT EXISTS (
           SELECT 1
           FROM pg_proc p
           JOIN pg_namespace n ON n.oid = p.pronamespace
           WHERE n.nspname = current_schema()
             AND p.proname LIKE 'research_evidence_%scenario_input%'
       )
       AND NOT EXISTS (
           SELECT 1
           FROM pg_trigger t
           JOIN pg_class c ON c.oid = t.tgrelid
           JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = current_schema()
             AND t.tgname LIKE 'trg_resi%'
       )
       AND NOT EXISTS (
           SELECT 1 FROM pg_indexes
           WHERE schemaname = current_schema()
             AND indexname LIKE 'idx_resi%'
       ) THEN
        RETURN;
    END IF;
    IF v_table_count <> 5 THEN
        RAISE EXCEPTION 'v58 contract violation: partial/divergent tables'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = current_schema()
          AND c.relname = ANY (ARRAY[
              'research_evidence_scenario_input_manifest',
              'research_evidence_scenario_input_manifest_item',
              'research_evidence_scenario_input_evaluation',
              'research_evidence_scenario_input_evaluation_input',
              'research_evidence_scenario_input_evaluation_sequence_allocator'
          ])
          AND c.relowner <> n.nspowner
    ) THEN
        RAISE EXCEPTION 'v58 contract violation: divergent table ownership'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    WITH expected(table_name, column_count) AS (VALUES
        ('research_evidence_scenario_input_manifest'::text, 11),
        ('research_evidence_scenario_input_manifest_item', 5),
        ('research_evidence_scenario_input_evaluation', 31),
        ('research_evidence_scenario_input_evaluation_input', 20),
        ('research_evidence_scenario_input_evaluation_sequence_allocator', 9)
    ),
    actual AS (
        SELECT table_name, count(*)::integer AS column_count
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = ANY (
              SELECT expected.table_name FROM expected
          )
        GROUP BY table_name
    )
    SELECT string_agg(expected.table_name, ', ' ORDER BY expected.table_name)
    INTO v_bad
    FROM expected
    LEFT JOIN actual USING (table_name)
    WHERE actual.column_count IS DISTINCT FROM expected.column_count;
    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION 'v58 contract violation: divergent column counts %', v_bad
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(
        expected.table_name || '.' || expected.column_name,
        ', ' ORDER BY expected.table_name, expected.column_name
    ) INTO v_missing
    FROM (VALUES
        ('research_evidence_scenario_input_manifest'::text, 'id'::text,
         'uuid'::text, 'NO'::text),
        ('research_evidence_scenario_input_manifest', 'project_id', 'uuid', 'NO'),
        ('research_evidence_scenario_input_manifest',
         'registration_request_id', 'text', 'NO'),
        ('research_evidence_scenario_input_manifest',
         'manifest_namespace', 'text', 'NO'),
        ('research_evidence_scenario_input_manifest',
         'manifest_version', 'text', 'NO'),
        ('research_evidence_scenario_input_manifest',
         'canonical_input_keys_json', 'jsonb', 'NO'),
        ('research_evidence_scenario_input_manifest',
         'input_cardinality', 'int4', 'NO'),
        ('research_evidence_scenario_input_manifest',
         'structural_descriptor', 'text', 'NO'),
        ('research_evidence_scenario_input_manifest',
         'manifest_fingerprint', 'text', 'NO'),
        ('research_evidence_scenario_input_manifest',
         'registered_by', 'text', 'NO'),
        ('research_evidence_scenario_input_manifest',
         'registered_at', 'timestamptz', 'NO'),
        ('research_evidence_scenario_input_manifest_item',
         'manifest_id', 'uuid', 'NO'),
        ('research_evidence_scenario_input_manifest_item',
         'project_id', 'uuid', 'NO'),
        ('research_evidence_scenario_input_manifest_item',
         'input_key', 'text', 'NO'),
        ('research_evidence_scenario_input_manifest_item',
         'item_ordinal', 'int4', 'NO'),
        ('research_evidence_scenario_input_manifest_item',
         'linked_at', 'timestamptz', 'NO'),
        ('research_evidence_scenario_input_evaluation', 'id', 'uuid', 'NO'),
        ('research_evidence_scenario_input_evaluation',
         'project_id', 'uuid', 'NO'),
        ('research_evidence_scenario_input_evaluation',
         'request_id', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation',
         'request_payload_json', 'jsonb', 'NO'),
        ('research_evidence_scenario_input_evaluation',
         'request_fingerprint', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation',
         'manifest_id', 'uuid', 'NO'),
        ('research_evidence_scenario_input_evaluation',
         'manifest_version', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation',
         'manifest_cardinality', 'int4', 'NO'),
        ('research_evidence_scenario_input_evaluation',
         'manifest_fingerprint', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation',
         'descriptor_namespace', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation',
         'descriptor_version', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation',
         'descriptor_json', 'jsonb', 'NO'),
        ('research_evidence_scenario_input_evaluation',
         'descriptor_fingerprint', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation',
         'descriptor_declared_by', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation',
         'consumer_contract_version', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation',
         'binding_set_id', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation',
         'binding_policy_identifier', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation',
         'binding_policy_version', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation',
         'binding_policy_fingerprint', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation',
         'binding_evaluator_version', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation',
         'freshness_as_of', 'timestamptz', 'NO'),
        ('research_evidence_scenario_input_evaluation',
         'evaluation_policy_identifier', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation',
         'evaluation_policy_version', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation',
         'evaluation_policy_parameters_json', 'jsonb', 'NO'),
        ('research_evidence_scenario_input_evaluation',
         'evaluation_policy_fingerprint', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation',
         'evaluator_version', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation',
         'evaluation_status', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation',
         'reason_codes_json', 'jsonb', 'NO'),
        ('research_evidence_scenario_input_evaluation',
         'evaluation_sequence', 'int4', 'NO'),
        ('research_evidence_scenario_input_evaluation',
         'predecessor_evaluation_id', 'uuid', 'YES'),
        ('research_evidence_scenario_input_evaluation',
         'evaluated_at', 'timestamptz', 'NO'),
        ('research_evidence_scenario_input_evaluation_input',
         'evaluation_id', 'uuid', 'NO'),
        ('research_evidence_scenario_input_evaluation_input',
         'project_id', 'uuid', 'NO'),
        ('research_evidence_scenario_input_evaluation_input',
         'manifest_id', 'uuid', 'NO'),
        ('research_evidence_scenario_input_evaluation_input',
         'input_key', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation_input',
         'selected_binding_id', 'uuid', 'NO'),
        ('research_evidence_scenario_input_evaluation_input',
         'consumer_contract', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation_input',
         'binding_set_id', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation_input',
         'binding_sequence', 'int4', 'NO'),
        ('research_evidence_scenario_input_evaluation_input',
         'selected_binding_has_successor', 'bool', 'NO'),
        ('research_evidence_scenario_input_evaluation_input',
         'availability_status', 'bool', 'NO'),
        ('research_evidence_scenario_input_evaluation_input',
         'lineage_is_current', 'bool', 'NO'),
        ('research_evidence_scenario_input_evaluation_input',
         'review_status', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation_input',
         'freshness_status', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation_input',
         'drift_status', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation_input',
         'binding_disposition', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation_input',
         'dependence_declaration', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation_input',
         'dependence_rationale', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation_input',
         'input_status', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation_input',
         'reason_codes_json', 'jsonb', 'NO'),
        ('research_evidence_scenario_input_evaluation_input',
         'linked_at', 'timestamptz', 'NO'),
        ('research_evidence_scenario_input_evaluation_sequence_allocator',
         'project_id', 'uuid', 'NO'),
        ('research_evidence_scenario_input_evaluation_sequence_allocator',
         'manifest_id', 'uuid', 'NO'),
        ('research_evidence_scenario_input_evaluation_sequence_allocator',
         'binding_set_id', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation_sequence_allocator',
         'descriptor_namespace', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation_sequence_allocator',
         'descriptor_version', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation_sequence_allocator',
         'descriptor_fingerprint', 'text', 'NO'),
        ('research_evidence_scenario_input_evaluation_sequence_allocator',
         'last_sequence', 'int4', 'NO'),
        ('research_evidence_scenario_input_evaluation_sequence_allocator',
         'last_evaluation_id', 'uuid', 'YES'),
        ('research_evidence_scenario_input_evaluation_sequence_allocator',
         'allocator_updated_at', 'timestamptz', 'NO')
    ) expected(table_name, column_name, udt_name, nullable)
    LEFT JOIN information_schema.columns column_info
      ON column_info.table_schema = current_schema()
     AND column_info.table_name = expected.table_name
     AND column_info.column_name = expected.column_name
    WHERE column_info.column_name IS NULL
       OR column_info.udt_name <> expected.udt_name
       OR column_info.is_nullable <> expected.nullable;
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v58 contract violation: divergent columns %', v_missing
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('research_evidence_scenario_input_manifest_pkey'::text),
        ('uq_resim_id_project'),
        ('uq_resim_project_request'),
        ('fk_resim_project'),
        ('ck_resim_nonblank'),
        ('ck_resim_keys'),
        ('ck_resim_cardinality'),
        ('ck_resim_fingerprint'),
        ('pk_resimi'),
        ('uq_resimi_ordinal'),
        ('uq_resimi_project_key'),
        ('fk_resimi_manifest'),
        ('ck_resimi_nonblank'),
        ('ck_resimi_ordinal'),
        ('research_evidence_scenario_input_evaluation_pkey'),
        ('uq_resie_id_scope'),
        ('uq_resie_id_project_manifest'),
        ('uq_resie_project_request'),
        ('uq_resie_scope_sequence'),
        ('uq_resie_predecessor_once'),
        ('fk_resie_project'),
        ('fk_resie_manifest'),
        ('fk_resie_predecessor'),
        ('ck_resie_nonblank'),
        ('ck_resie_fingerprints'),
        ('ck_resie_status'),
        ('ck_resie_json_shapes'),
        ('ck_resie_policy'),
        ('ck_resie_sequence'),
        ('pk_resiei'),
        ('uq_resiei_manifest_key'),
        ('uq_resiei_binding'),
        ('fk_resiei_evaluation'),
        ('fk_resiei_manifest_item'),
        ('fk_resiei_binding'),
        ('ck_resiei_status'),
        ('ck_resiei_dependence'),
        ('ck_resiei_nonblank'),
        ('ck_resiei_json'),
        ('pk_resie_allocator'),
        ('fk_resie_allocator_manifest'),
        ('fk_resie_allocator_last'),
        ('ck_resie_allocator_sequence')
    ) expected(name)
    WHERE NOT EXISTS (
        SELECT 1 FROM pg_constraint con
        WHERE con.connamespace = current_schema()::regnamespace
          AND con.conname = expected.name
          AND con.convalidated
          AND con.contype = CASE
              WHEN expected.name LIKE 'fk_%' THEN 'f'::"char"
              WHEN expected.name LIKE 'ck_%' THEN 'c'::"char"
              WHEN expected.name LIKE 'uq_%' THEN 'u'::"char"
              ELSE 'p'::"char"
          END
          AND con.conrelid = CASE
              WHEN expected.name = ANY (ARRAY[
                  'research_evidence_scenario_input_manifest_pkey',
                  'uq_resim_id_project', 'uq_resim_project_request',
                  'fk_resim_project', 'ck_resim_nonblank',
                  'ck_resim_keys', 'ck_resim_cardinality',
                  'ck_resim_fingerprint'
              ]) THEN
                  'research_evidence_scenario_input_manifest'::regclass
              WHEN expected.name = ANY (ARRAY[
                  'pk_resimi', 'uq_resimi_ordinal',
                  'uq_resimi_project_key', 'fk_resimi_manifest',
                  'ck_resimi_nonblank', 'ck_resimi_ordinal'
              ]) THEN
                  'research_evidence_scenario_input_manifest_item'::regclass
              WHEN expected.name = ANY (ARRAY[
                  'research_evidence_scenario_input_evaluation_pkey',
                  'uq_resie_id_scope', 'uq_resie_id_project_manifest',
                  'uq_resie_project_request', 'uq_resie_scope_sequence',
                  'uq_resie_predecessor_once', 'fk_resie_project',
                  'fk_resie_manifest', 'fk_resie_predecessor',
                  'ck_resie_nonblank', 'ck_resie_fingerprints',
                  'ck_resie_status', 'ck_resie_json_shapes',
                  'ck_resie_policy', 'ck_resie_sequence'
              ]) THEN
                  'research_evidence_scenario_input_evaluation'::regclass
              WHEN expected.name = ANY (ARRAY[
                  'pk_resiei', 'uq_resiei_manifest_key',
                  'uq_resiei_binding', 'fk_resiei_evaluation',
                  'fk_resiei_manifest_item', 'fk_resiei_binding',
                  'ck_resiei_status', 'ck_resiei_dependence',
                  'ck_resiei_nonblank', 'ck_resiei_json'
              ]) THEN
                  'research_evidence_scenario_input_evaluation_input'::regclass
              ELSE
                  'research_evidence_scenario_input_evaluation_sequence_allocator'
                  ::regclass
          END
          AND obj_description(con.oid, 'pg_constraint') =
              'v58:'
              || md5(regexp_replace(
                  pg_get_constraintdef(con.oid, true),
                  '[[:space:]]+', '', 'g'
              ))
    );
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v58 contract violation: missing constraints %', v_missing
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_constraint con
        JOIN pg_class table_class ON table_class.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = table_class.relnamespace
        LEFT JOIN pg_index index_info ON index_info.indexrelid = con.conindid
        LEFT JOIN pg_class index_class
          ON index_class.oid = index_info.indexrelid
        LEFT JOIN pg_am access_method ON access_method.oid = index_class.relam
        WHERE n.nspname = current_schema()
          AND table_class.relname = ANY (ARRAY[
              'research_evidence_scenario_input_manifest',
              'research_evidence_scenario_input_manifest_item',
              'research_evidence_scenario_input_evaluation',
              'research_evidence_scenario_input_evaluation_input',
              'research_evidence_scenario_input_evaluation_sequence_allocator'
          ])
          AND con.contype IN ('p', 'u')
          AND (
              index_info.indexrelid IS NULL
              OR access_method.amname <> 'btree'
              OR NOT index_info.indisunique
              OR index_info.indisprimary IS DISTINCT FROM
                 (con.contype = 'p')
              OR index_info.indimmediate IS DISTINCT FROM
                 (NOT con.condeferrable)
              OR index_info.indnkeyatts <> cardinality(con.conkey)
              OR index_info.indnatts <> cardinality(con.conkey)
              OR index_info.indpred IS NOT NULL
              OR index_info.indexprs IS NOT NULL
              OR NOT index_info.indisvalid
              OR NOT index_info.indisready
              OR NOT index_info.indislive
              OR EXISTS (
                  SELECT 1
                  FROM unnest(con.conkey)
                       WITH ORDINALITY constraint_key(attnum, position)
                  JOIN pg_attribute attribute
                    ON attribute.attrelid = con.conrelid
                   AND attribute.attnum = constraint_key.attnum
                  LEFT JOIN LATERAL (
                      SELECT index_key.attnum, index_key.opclass,
                             index_key.collation_oid, index_key.options
                      FROM unnest(
                          index_info.indkey::smallint[],
                          index_info.indclass::oid[],
                          index_info.indcollation::oid[],
                          index_info.indoption::smallint[]
                      ) WITH ORDINALITY index_key(
                          attnum, opclass, collation_oid, options, position
                      )
                      WHERE index_key.position = constraint_key.position
                  ) index_key ON true
                  LEFT JOIN pg_opclass opclass
                    ON opclass.oid = index_key.opclass
                  WHERE index_key.attnum
                        IS DISTINCT FROM constraint_key.attnum
                     OR index_key.collation_oid
                        IS DISTINCT FROM attribute.attcollation
                     OR index_key.options IS DISTINCT FROM 0
                     OR NOT COALESCE(opclass.opcdefault, false)
                     OR opclass.opcmethod IS DISTINCT FROM index_class.relam
                     OR opclass.opcintype
                        IS DISTINCT FROM attribute.atttypid
              )
          )
    ) THEN
        RAISE EXCEPTION
            'v58 contract violation: divergent constraint backing indexes'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = ANY (ARRAY[
              'research_evidence_scenario_input_manifest',
              'research_evidence_scenario_input_manifest_item',
              'research_evidence_scenario_input_evaluation',
              'research_evidence_scenario_input_evaluation_input',
              'research_evidence_scenario_input_evaluation_sequence_allocator'
          ])
          AND (
              (
                  column_name = 'id'
                  AND table_name IN (
                      'research_evidence_scenario_input_manifest',
                      'research_evidence_scenario_input_evaluation'
                  )
                  AND lower(regexp_replace(
                      column_default, '[[:space:]]+', '', 'g'
                  )) IS DISTINCT FROM 'gen_random_uuid()'
              )
              OR
              (
                  NOT (
                      column_name = 'id'
                      AND table_name IN (
                          'research_evidence_scenario_input_manifest',
                          'research_evidence_scenario_input_evaluation'
                      )
                  )
                  AND column_default IS NOT NULL
              )
          )
    ) THEN
        RAISE EXCEPTION 'v58 contract violation: divergent column defaults'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('idx_resim_project_namespace_version'::text,
         'research_evidence_scenario_input_manifest'::text,
         ARRAY[
             'project_id', 'manifest_namespace', 'manifest_version',
             'registered_at', 'id', 'manifest_fingerprint',
             'input_cardinality'
         ]::text[], 5),
        ('idx_resie_scope_sequence',
         'research_evidence_scenario_input_evaluation',
         ARRAY[
             'project_id', 'manifest_id', 'binding_set_id',
             'descriptor_namespace', 'descriptor_version',
             'descriptor_fingerprint', 'evaluation_sequence',
             'predecessor_evaluation_id', 'evaluation_status', 'evaluated_at'
         ], 7),
        ('idx_resiei_binding',
         'research_evidence_scenario_input_evaluation_input',
         ARRAY[
             'selected_binding_id', 'evaluation_id',
             'input_key', 'input_status'
         ], 2)
    ) expected(name, table_name, columns, key_count)
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_class index_class
        JOIN pg_namespace n ON n.oid = index_class.relnamespace
        JOIN pg_index index_info ON index_info.indexrelid = index_class.oid
        JOIN pg_class table_class ON table_class.oid = index_info.indrelid
        JOIN pg_am access_method ON access_method.oid = index_class.relam
        CROSS JOIN LATERAL (
            SELECT array_agg(
                attribute.attname::text ORDER BY key.ordinality
            ) AS columns
            FROM unnest(index_info.indkey::smallint[])
                 WITH ORDINALITY key(attnum, ordinality)
            JOIN pg_attribute attribute
              ON attribute.attrelid = index_info.indrelid
             AND attribute.attnum = key.attnum
        ) actual
        WHERE n.nspname = current_schema()
          AND index_class.relname = expected.name
          AND table_class.relname = expected.table_name
          AND access_method.amname = 'btree'
          AND actual.columns = expected.columns
          AND index_info.indnkeyatts = expected.key_count
          AND NOT index_info.indisunique
          AND index_info.indpred IS NULL
          AND index_info.indexprs IS NULL
          AND index_info.indisvalid
          AND index_info.indisready
          AND index_info.indislive
          AND obj_description(index_class.oid, 'pg_class') =
              'v58:' || md5(regexp_replace(
                  pg_get_indexdef(index_class.oid),
                  '[[:space:]]+', '', 'g'
              ))
          AND NOT EXISTS (
              SELECT 1
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
              LEFT JOIN pg_opclass opclass
                ON opclass.oid = index_key.opclass
              WHERE index_key.position <= index_info.indnkeyatts
                AND (
                    index_key.collation_oid IS DISTINCT FROM
                        attribute.attcollation
                    OR index_key.options IS DISTINCT FROM 0
                    OR NOT COALESCE(opclass.opcdefault, false)
                    OR opclass.opcmethod IS DISTINCT FROM index_class.relam
                    OR opclass.opcintype IS DISTINCT FROM attribute.atttypid
                )
          )
    );
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v58 contract violation: divergent indexes %', v_missing
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('research_evidence_scenario_input_policy_state'::text, 8,
         'e4929cb077b982f1f63382a9e67e3c2e'::text),
        ('research_evidence_prepare_scenario_input_manifest', 0,
         '94a4ff392a6bda271f237be082aa4ec3'),
        ('research_evidence_link_scenario_input_manifest_items', 0,
         '5fe8d7255004037593a6252b28056cee'),
        ('research_evidence_prepare_scenario_input_manifest_item', 0,
         'aba5f0e70986e88e1686a4d465232e82'),
        ('research_evidence_check_scenario_input_manifest', 0,
         '917a045469dc29de46321424359e1ad6'),
        ('research_evidence_prepare_scenario_input_evaluation', 0,
         'e23ffcb45046cca9d5ef51830f14df66'),
        ('research_evidence_link_scenario_input_evaluation_inputs', 0,
         '977f9a46f6d896d9a9e0b5f4aeaabe08'),
        ('research_evidence_prepare_scenario_input_evaluation_input', 0,
         'd010943285c2394ad44f12b7188dc566'),
        ('research_evidence_check_scenario_input_evaluation', 0,
         '974e8c95f72b7c3f281e93803be04931'),
        ('research_evidence_register_scenario_input_manifest', 6,
         'ca3659778a46ef2ee1f9fdba0679a249'),
        ('research_evidence_create_scenario_input_evaluation', 1,
         '9777e495ea334c973ac21c26b991cefd')
    ) expected(name, nargs, body_hash)
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = current_schema()
          AND p.proname = expected.name
          AND p.pronargs = expected.nargs
          AND p.proowner = n.nspowner
          AND (
              (
                  expected.nargs = 0
                  AND p.prorettype = 'trigger'::regtype
              )
              OR
              (
                  expected.name =
                      'research_evidence_scenario_input_policy_state'
                  AND pg_get_function_identity_arguments(p.oid) =
                      'p_availability boolean, p_lineage_current boolean, '
                      || 'p_review_status text, p_freshness_status text, '
                      || 'p_drift_status text, p_binding_disposition text, '
                      || 'p_has_successor boolean, p_dependence text'
                  AND p.proretset
                  AND p.prorettype = 'record'::regtype
              )
              OR
              (
                  expected.name =
                      'research_evidence_register_scenario_input_manifest'
                  AND pg_get_function_identity_arguments(p.oid) =
                      'p_project_id uuid, p_request_id text, '
                      || 'p_namespace text, p_version text, '
                      || 'p_input_keys jsonb, p_registered_by text'
                  AND p.proretset
                  AND p.prorettype =
                      'research_evidence_scenario_input_manifest'::regtype
              )
              OR
              (
                  expected.name =
                      'research_evidence_create_scenario_input_evaluation'
                  AND pg_get_function_identity_arguments(p.oid) =
                      'p_request_payload jsonb'
                  AND p.proretset
                  AND p.prorettype =
                      'research_evidence_scenario_input_evaluation'::regtype
              )
          )
          AND md5(regexp_replace(
              p.prosrc, '[[:space:]]+', '', 'g'
          )) = expected.body_hash
          AND NOT p.prosecdef
          AND NOT has_function_privilege('public', p.oid, 'execute')
          AND (
              (
                  expected.name =
                      'research_evidence_scenario_input_policy_state'
                  AND p.prolang = (
                      SELECT oid FROM pg_language WHERE lanname = 'sql'
                  )
                  AND p.provolatile = 'i'
                  AND p.proparallel = 's'
                  AND p.proconfig = ARRAY['search_path=pg_catalog']::text[]
              )
              OR
              (
                  expected.name <>
                      'research_evidence_scenario_input_policy_state'
                  AND p.prolang = (
                      SELECT oid FROM pg_language WHERE lanname = 'plpgsql'
                  )
                  AND p.provolatile = 'v'
                  AND p.proparallel = 'u'
                  AND p.proconfig IS NULL
              )
          )
    );
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v58 contract violation: missing/divergent functions %',
            v_missing USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('trg_resim_prepare_insert'::text,
         'research_evidence_scenario_input_manifest'::text, 7::smallint,
         'research_evidence_prepare_scenario_input_manifest'::text,
         ''::int2vector),
        ('trg_resim_link_items',
         'research_evidence_scenario_input_manifest', 5::smallint,
         'research_evidence_link_scenario_input_manifest_items',
         ''::int2vector),
        ('trg_resim_no_mutation',
         'research_evidence_scenario_input_manifest', 27::smallint,
         'slicea_reject_mutation', ''::int2vector),
        ('trg_resimi_prepare_insert',
         'research_evidence_scenario_input_manifest_item', 7::smallint,
         'research_evidence_prepare_scenario_input_manifest_item',
         ''::int2vector),
        ('trg_resimi_no_mutation',
         'research_evidence_scenario_input_manifest_item', 27::smallint,
         'slicea_reject_mutation', ''::int2vector),
        ('trg_resim_complete',
         'research_evidence_scenario_input_manifest', 5::smallint,
         'research_evidence_check_scenario_input_manifest',
         ''::int2vector),
        ('trg_resimi_complete',
         'research_evidence_scenario_input_manifest_item', 5::smallint,
         'research_evidence_check_scenario_input_manifest',
         ''::int2vector),
        ('trg_resie_prepare_insert',
         'research_evidence_scenario_input_evaluation', 7::smallint,
         'research_evidence_prepare_scenario_input_evaluation',
         ''::int2vector),
        ('trg_resie_link_inputs',
         'research_evidence_scenario_input_evaluation', 5::smallint,
         'research_evidence_link_scenario_input_evaluation_inputs',
         ''::int2vector),
        ('trg_resie_no_mutation',
         'research_evidence_scenario_input_evaluation', 27::smallint,
         'slicea_reject_mutation', ''::int2vector),
        ('trg_resiei_prepare_insert',
         'research_evidence_scenario_input_evaluation_input', 7::smallint,
         'research_evidence_prepare_scenario_input_evaluation_input',
         ''::int2vector),
        ('trg_resiei_no_mutation',
         'research_evidence_scenario_input_evaluation_input', 27::smallint,
         'slicea_reject_mutation', ''::int2vector),
        ('trg_resie_complete',
         'research_evidence_scenario_input_evaluation', 5::smallint,
         'research_evidence_check_scenario_input_evaluation',
         ''::int2vector),
        ('trg_resiei_complete',
         'research_evidence_scenario_input_evaluation_input', 5::smallint,
         'research_evidence_check_scenario_input_evaluation',
         ''::int2vector)
    ) expected(
        name, table_name, trigger_type, function_name, trigger_attributes
    )
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_proc p ON p.oid = t.tgfoid
        WHERE n.nspname = current_schema()
          AND c.relname = expected.table_name
          AND t.tgname = expected.name
          AND t.tgtype = expected.trigger_type
          AND p.proname = expected.function_name
          AND t.tgattr = expected.trigger_attributes
          AND t.tgenabled = 'A'
          AND NOT t.tgisinternal
          AND t.tgnargs = 0
          AND t.tgqual IS NULL
          AND t.tgoldtable IS NULL
          AND t.tgnewtable IS NULL
          AND (
              (
                  expected.name IN (
                      'trg_resim_complete', 'trg_resimi_complete',
                      'trg_resie_complete', 'trg_resiei_complete'
                  )
                  AND t.tgconstraint <> 0
                  AND t.tgdeferrable
                  AND t.tginitdeferred
              )
              OR
              (
                  expected.name NOT IN (
                      'trg_resim_complete', 'trg_resimi_complete',
                      'trg_resie_complete', 'trg_resiei_complete'
                  )
                  AND t.tgconstraint = 0
                  AND NOT t.tgdeferrable
                  AND NOT t.tginitdeferred
              )
          )
    );
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v58 contract violation: divergent triggers %', v_missing
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN LATERAL aclexplode(
            COALESCE(c.relacl, acldefault('r', c.relowner))
        ) acl
        WHERE n.nspname = current_schema()
          AND c.relname = ANY (ARRAY[
              'research_evidence_scenario_input_manifest',
              'research_evidence_scenario_input_manifest_item',
              'research_evidence_scenario_input_evaluation',
              'research_evidence_scenario_input_evaluation_input',
              'research_evidence_scenario_input_evaluation_sequence_allocator'
          ])
          AND acl.grantee <> c.relowner
    ) THEN
        RAISE EXCEPTION
            'v58 contract violation: tables have non-owner ACL privileges'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        CROSS JOIN LATERAL aclexplode(
            COALESCE(p.proacl, acldefault('f', p.proowner))
        ) acl
        WHERE n.nspname = current_schema()
          AND p.proname = ANY (ARRAY[
              'research_evidence_scenario_input_policy_state',
              'research_evidence_prepare_scenario_input_manifest',
              'research_evidence_link_scenario_input_manifest_items',
              'research_evidence_prepare_scenario_input_manifest_item',
              'research_evidence_check_scenario_input_manifest',
              'research_evidence_prepare_scenario_input_evaluation',
              'research_evidence_link_scenario_input_evaluation_inputs',
              'research_evidence_prepare_scenario_input_evaluation_input',
              'research_evidence_check_scenario_input_evaluation',
              'research_evidence_register_scenario_input_manifest',
              'research_evidence_create_scenario_input_evaluation'
          ])
          AND acl.grantee <> p.proowner
    ) THEN
        RAISE EXCEPTION
            'v58 contract violation: functions have non-owner ACL privileges'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM research_evidence_scenario_input_manifest manifest
        LEFT JOIN LATERAL (
            SELECT count(*)::integer AS item_count,
                   jsonb_agg(item.input_key ORDER BY item.item_ordinal) AS keys,
                   min(item.item_ordinal) AS first_ordinal,
                   max(item.item_ordinal) AS last_ordinal,
                   bool_and(item.linked_at = manifest.registered_at)
                       AS timestamps_match
            FROM research_evidence_scenario_input_manifest_item item
            WHERE item.manifest_id = manifest.id
              AND item.project_id = manifest.project_id
        ) state ON true
        WHERE state.item_count <> manifest.input_cardinality
           OR state.keys IS DISTINCT FROM manifest.canonical_input_keys_json
           OR (
               manifest.input_cardinality > 0
               AND (
                   state.first_ordinal <> 1
                   OR state.last_ordinal <> manifest.input_cardinality
               )
           )
           OR NOT COALESCE(state.timestamps_match, true)
    ) THEN
        RAISE EXCEPTION 'v58 contract violation: malformed manifest history'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM research_evidence_scenario_input_manifest manifest
        CROSS JOIN LATERAL (
            SELECT
                'scenario-input-manifest-v1' || E'\n'
                || 'namespace='
                || octet_length(convert_to(
                    manifest.manifest_namespace, 'UTF8'
                ))::text
                || ':' || manifest.manifest_namespace || E'\n'
                || 'version='
                || octet_length(convert_to(
                    manifest.manifest_version, 'UTF8'
                ))::text
                || ':' || manifest.manifest_version || E'\n'
                || 'cardinality='
                || manifest.input_cardinality::text || E'\n'
                || COALESCE(string_agg(
                    'key='
                    || octet_length(convert_to(item.input_key, 'UTF8'))::text
                    || ':' || item.input_key || E'\n',
                    '' ORDER BY item.item_ordinal
                ), '') AS descriptor
            FROM research_evidence_scenario_input_manifest_item item
            WHERE item.manifest_id = manifest.id
              AND item.project_id = manifest.project_id
        ) canonical
        WHERE manifest.structural_descriptor IS DISTINCT FROM
              canonical.descriptor
           OR manifest.manifest_fingerprint IS DISTINCT FROM encode(
               sha256(convert_to(canonical.descriptor, 'UTF8')), 'hex'
           )
    ) THEN
        RAISE EXCEPTION 'v58 contract violation: manifest fingerprint drift'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM research_evidence_scenario_input_evaluation evaluation
        JOIN research_evidence_scenario_input_manifest manifest
          ON manifest.id = evaluation.manifest_id
         AND manifest.project_id = evaluation.project_id
        LEFT JOIN research_evidence_scenario_input_evaluation predecessor
          ON predecessor.id = evaluation.predecessor_evaluation_id
         AND predecessor.project_id = evaluation.project_id
         AND predecessor.manifest_id = evaluation.manifest_id
         AND predecessor.binding_set_id = evaluation.binding_set_id
         AND predecessor.descriptor_namespace =
             evaluation.descriptor_namespace
         AND predecessor.descriptor_version = evaluation.descriptor_version
         AND predecessor.descriptor_fingerprint =
             evaluation.descriptor_fingerprint
         AND predecessor.evaluation_sequence =
             evaluation.evaluation_sequence - 1
        LEFT JOIN LATERAL (
            SELECT count(*)::integer AS child_count,
                   count(DISTINCT child.input_key)::integer AS key_count,
                   count(DISTINCT child.selected_binding_id)::integer
                       AS binding_count,
                   bool_and(child.linked_at = evaluation.evaluated_at)
                       AS timestamps_match
            FROM research_evidence_scenario_input_evaluation_input child
            WHERE child.evaluation_id = evaluation.id
              AND child.project_id = evaluation.project_id
              AND child.manifest_id = evaluation.manifest_id
        ) children ON true
        WHERE evaluation.manifest_version IS DISTINCT FROM
              manifest.manifest_version
           OR evaluation.manifest_cardinality IS DISTINCT FROM
              manifest.input_cardinality
           OR evaluation.manifest_fingerprint IS DISTINCT FROM
              manifest.manifest_fingerprint
           OR evaluation.request_fingerprint IS DISTINCT FROM encode(
               sha256(convert_to(
                   evaluation.request_payload_json::text, 'UTF8'
               )), 'hex'
           )
           OR evaluation.descriptor_fingerprint IS DISTINCT FROM encode(
               sha256(convert_to(evaluation.descriptor_json::text, 'UTF8')),
               'hex'
           )
           OR children.child_count <> evaluation.manifest_cardinality
           OR children.key_count <> evaluation.manifest_cardinality
           OR children.binding_count <> evaluation.manifest_cardinality
           OR NOT COALESCE(children.timestamps_match, false)
           OR (
               evaluation.evaluation_sequence = 1
               AND evaluation.predecessor_evaluation_id IS NOT NULL
           )
           OR (
               evaluation.evaluation_sequence > 1
               AND predecessor.id IS NULL
           )
    ) THEN
        RAISE EXCEPTION
            'v58 contract violation: evaluation predecessor/child integrity drift'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM research_evidence_scenario_input_evaluation evaluation
        JOIN research_evidence_scenario_input_evaluation_input child
          ON child.evaluation_id = evaluation.id
         AND child.project_id = evaluation.project_id
         AND child.manifest_id = evaluation.manifest_id
        LEFT JOIN LATERAL (
            SELECT selected
            FROM jsonb_array_elements(
                evaluation.request_payload_json->'selected_bindings'
            ) selected
            WHERE (selected->>'binding_id')::uuid =
                  child.selected_binding_id
        ) request_member ON true
        LEFT JOIN research_evidence_consumer_input_binding binding
          ON binding.id = child.selected_binding_id
         AND binding.project_id = child.project_id
         AND binding.consumer_contract = child.consumer_contract
         AND binding.binding_set_id = child.binding_set_id
         AND binding.input_key = child.input_key
        LEFT JOIN LATERAL
            research_evidence_scenario_input_policy_state(
                child.availability_status,
                child.lineage_is_current,
                child.review_status,
                child.freshness_status,
                child.drift_status,
                child.binding_disposition,
                child.selected_binding_has_successor,
                child.dependence_declaration
            ) policy ON true
        WHERE request_member.selected IS NULL
           OR request_member.selected->>'dependence_declaration'
              IS DISTINCT FROM child.dependence_declaration
           OR request_member.selected->>'rationale'
              IS DISTINCT FROM child.dependence_rationale
           OR binding.id IS NULL
           OR binding.binding_sequence
              IS DISTINCT FROM child.binding_sequence
           OR binding.availability_status
              IS DISTINCT FROM child.availability_status
           OR binding.lineage_is_current
              IS DISTINCT FROM child.lineage_is_current
           OR binding.review_status IS DISTINCT FROM child.review_status
           OR binding.freshness_status
              IS DISTINCT FROM child.freshness_status
           OR binding.drift_status IS DISTINCT FROM child.drift_status
           OR binding.consumer_disposition
              IS DISTINCT FROM child.binding_disposition
           OR policy.input_status IS DISTINCT FROM child.input_status
           OR policy.reason_codes_json
              IS DISTINCT FROM child.reason_codes_json
    ) THEN
        RAISE EXCEPTION
            'v58 contract violation: request membership/child policy drift'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF EXISTS (
        WITH expected_policy(parameters) AS (VALUES (
            '{"dependence_outcomes":{"declared_dependent":"qualified","declared_independent_not_verified":"qualified","not_assessed":"indeterminate"},"reason_order":["evidence_unavailable","lineage_not_current","review_rejected","review_needs_revision","review_withdrawn","material_drift","selected_binding_successor","binding_does_not_meet_contract","review_not_assessed","freshness_unknown","drift_not_assessed","drift_indeterminate","binding_indeterminate","dependence_not_assessed","freshness_stale","binding_qualified","dependence_declared_dependent","dependence_declared_independent_not_verified"],"satisfies_nonempty_manifest_reachable":false,"status_precedence":["does_not_satisfy","indeterminate","qualified","satisfies"]}'::jsonb
        ))
        SELECT 1
        FROM research_evidence_scenario_input_evaluation evaluation
        CROSS JOIN expected_policy
        LEFT JOIN LATERAL (
            SELECT
                CASE min(CASE child.input_status
                    WHEN 'does_not_satisfy' THEN 1
                    WHEN 'indeterminate' THEN 2
                    WHEN 'qualified' THEN 3
                    ELSE 4
                END)
                    WHEN 1 THEN 'does_not_satisfy'
                    WHEN 2 THEN 'indeterminate'
                    WHEN 3 THEN 'qualified'
                    ELSE 'satisfies'
                END AS status,
                (
                    SELECT jsonb_agg(to_jsonb(code) ORDER BY ordinal)
                    FROM (
                        SELECT DISTINCT ON (code) code, ordinal
                        FROM
                            research_evidence_scenario_input_evaluation_input c
                        CROSS JOIN LATERAL jsonb_array_elements_text(
                            c.reason_codes_json
                        ) AS reason(code)
                        CROSS JOIN LATERAL (
                            SELECT array_position(ARRAY[
                                'evidence_unavailable',
                                'lineage_not_current',
                                'review_rejected',
                                'review_needs_revision',
                                'review_withdrawn',
                                'material_drift',
                                'selected_binding_successor',
                                'binding_does_not_meet_contract',
                                'review_not_assessed',
                                'freshness_unknown',
                                'drift_not_assessed',
                                'drift_indeterminate',
                                'binding_indeterminate',
                                'dependence_not_assessed',
                                'freshness_stale',
                                'binding_qualified',
                                'dependence_declared_dependent',
                                'dependence_declared_independent_not_verified'
                            ]::text[], code) AS ordinal
                        ) position
                        WHERE c.evaluation_id = evaluation.id
                        ORDER BY code, ordinal
                    ) ordered
                ) AS reasons
            FROM research_evidence_scenario_input_evaluation_input child
            WHERE child.evaluation_id = evaluation.id
        ) aggregate ON true
        WHERE evaluation.evaluation_policy_parameters_json
              IS DISTINCT FROM expected_policy.parameters
           OR evaluation.request_payload_json->>'project_id'
              IS DISTINCT FROM evaluation.project_id::text
           OR evaluation.request_payload_json->>'request_id'
              IS DISTINCT FROM evaluation.request_id
           OR evaluation.request_payload_json->'manifest'->>'id'
              IS DISTINCT FROM evaluation.manifest_id::text
           OR evaluation.request_payload_json->'manifest'->>'version'
              IS DISTINCT FROM evaluation.manifest_version
           OR evaluation.request_payload_json->'manifest'->>'fingerprint'
              IS DISTINCT FROM evaluation.manifest_fingerprint
           OR evaluation.request_payload_json->'descriptor'->>'namespace'
              IS DISTINCT FROM evaluation.descriptor_namespace
           OR evaluation.request_payload_json->'descriptor'
                  ->>'descriptor_version'
              IS DISTINCT FROM evaluation.descriptor_version
           OR evaluation.request_payload_json->'descriptor'->'descriptor'
              IS DISTINCT FROM evaluation.descriptor_json
           OR evaluation.request_payload_json->'descriptor'->>'fingerprint'
              IS DISTINCT FROM evaluation.descriptor_fingerprint
           OR evaluation.request_payload_json->'descriptor'->>'declared_by'
              IS DISTINCT FROM evaluation.descriptor_declared_by
           OR CASE
                  WHEN jsonb_typeof(
                      evaluation.request_payload_json->'selected_bindings'
                  ) = 'array'
                  THEN jsonb_array_length(
                      evaluation.request_payload_json->'selected_bindings'
                  ) IS DISTINCT FROM evaluation.manifest_cardinality
                  ELSE true
              END
           OR evaluation.request_payload_json->'binding_contract'
                  ->>'consumer_contract'
              IS DISTINCT FROM 'scenario_input'
           OR evaluation.request_payload_json->'binding_contract'
                  ->>'consumer_contract_version'
              IS DISTINCT FROM evaluation.consumer_contract_version
           OR evaluation.request_payload_json->'binding_contract'
                  ->>'binding_set_id'
              IS DISTINCT FROM evaluation.binding_set_id
           OR evaluation.request_payload_json->'binding_contract'
                  ->>'policy_identifier'
              IS DISTINCT FROM evaluation.binding_policy_identifier
           OR evaluation.request_payload_json->'binding_contract'
                  ->>'policy_version'
              IS DISTINCT FROM evaluation.binding_policy_version
           OR evaluation.request_payload_json->'binding_contract'
                  ->>'policy_fingerprint'
              IS DISTINCT FROM evaluation.binding_policy_fingerprint
           OR evaluation.request_payload_json->'binding_contract'
                  ->>'evaluator_version'
              IS DISTINCT FROM evaluation.binding_evaluator_version
           OR evaluation.request_payload_json->'evaluation_policy'
                  ->>'identifier'
              IS DISTINCT FROM evaluation.evaluation_policy_identifier
           OR evaluation.request_payload_json->'evaluation_policy'
                  ->>'version'
              IS DISTINCT FROM evaluation.evaluation_policy_version
           OR evaluation.request_payload_json->'evaluation_policy'
                  ->'parameters'
              IS DISTINCT FROM evaluation.evaluation_policy_parameters_json
           OR evaluation.request_payload_json->'evaluation_policy'
                  ->>'fingerprint'
              IS DISTINCT FROM evaluation.evaluation_policy_fingerprint
           OR evaluation.request_payload_json->'evaluation_policy'
                  ->>'evaluator_version'
              IS DISTINCT FROM evaluation.evaluator_version
           OR evaluation.request_payload_json->>'freshness_as_of'
              IS DISTINCT FROM to_char(
                  evaluation.freshness_as_of AT TIME ZONE 'UTC',
                  'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
              )
           OR aggregate.status
              IS DISTINCT FROM evaluation.evaluation_status
           OR aggregate.reasons
              IS DISTINCT FROM evaluation.reason_codes_json
    ) THEN
        RAISE EXCEPTION
            'v58 contract violation: header policy/aggregate integrity drift'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM research_evidence_scenario_input_evaluation_sequence_allocator a
        LEFT JOIN LATERAL (
            SELECT count(*)::integer AS row_count,
                   min(e.evaluation_sequence) AS min_sequence,
                   max(e.evaluation_sequence) AS max_sequence,
                   (
                       SELECT latest.id
                       FROM research_evidence_scenario_input_evaluation latest
                       WHERE latest.project_id = a.project_id
                         AND latest.manifest_id = a.manifest_id
                         AND latest.binding_set_id = a.binding_set_id
                         AND latest.descriptor_namespace =
                             a.descriptor_namespace
                         AND latest.descriptor_version =
                             a.descriptor_version
                         AND latest.descriptor_fingerprint =
                             a.descriptor_fingerprint
                       ORDER BY latest.evaluation_sequence DESC, latest.id DESC
                       LIMIT 1
                   ) AS last_id
            FROM research_evidence_scenario_input_evaluation e
            WHERE e.project_id = a.project_id
              AND e.manifest_id = a.manifest_id
              AND e.binding_set_id = a.binding_set_id
              AND e.descriptor_namespace = a.descriptor_namespace
              AND e.descriptor_version = a.descriptor_version
              AND e.descriptor_fingerprint = a.descriptor_fingerprint
        ) state ON true
        WHERE a.last_sequence < 1
           OR a.last_evaluation_id IS NULL
           OR state.row_count <> a.last_sequence
           OR (a.last_sequence > 0 AND state.min_sequence <> 1)
           OR state.max_sequence IS DISTINCT FROM a.last_sequence
           OR state.last_id IS DISTINCT FROM a.last_evaluation_id
           OR (
               a.last_sequence > 0
               AND a.allocator_updated_at IS DISTINCT FROM (
                   SELECT e.evaluated_at
                   FROM research_evidence_scenario_input_evaluation e
                   WHERE e.id = a.last_evaluation_id
               )
           )
    ) THEN
        RAISE EXCEPTION 'v58 contract violation: allocator/history divergence'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- Contract A (no preallocation): committed allocator rows and persisted
    -- history scopes exist one-for-one.  The transient zero row used while the
    -- first BEFORE INSERT trigger owns the scope lock is updated before that
    -- evaluation statement can complete and can never be a valid reapply state.
    IF EXISTS (
        SELECT 1
        FROM (
            SELECT DISTINCT
                   evaluation.project_id,
                   evaluation.manifest_id,
                   evaluation.binding_set_id,
                   evaluation.descriptor_namespace,
                   evaluation.descriptor_version,
                   evaluation.descriptor_fingerprint
            FROM research_evidence_scenario_input_evaluation evaluation
        ) history_scope
        LEFT JOIN
            research_evidence_scenario_input_evaluation_sequence_allocator
            allocator
          ON allocator.project_id = history_scope.project_id
         AND allocator.manifest_id = history_scope.manifest_id
         AND allocator.binding_set_id = history_scope.binding_set_id
         AND allocator.descriptor_namespace =
             history_scope.descriptor_namespace
         AND allocator.descriptor_version = history_scope.descriptor_version
         AND allocator.descriptor_fingerprint =
             history_scope.descriptor_fingerprint
        WHERE allocator.project_id IS NULL
    ) THEN
        RAISE EXCEPTION
            'v58 contract violation: evaluation history has no allocator'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
END
$preflight$;

CREATE TABLE IF NOT EXISTS research_evidence_scenario_input_manifest (
    id                        UUID NOT NULL DEFAULT gen_random_uuid(),
    project_id                UUID NOT NULL,
    registration_request_id   TEXT NOT NULL,
    manifest_namespace        TEXT NOT NULL,
    manifest_version          TEXT NOT NULL,
    canonical_input_keys_json JSONB NOT NULL,
    input_cardinality         INTEGER NOT NULL,
    structural_descriptor     TEXT NOT NULL,
    manifest_fingerprint      TEXT NOT NULL,
    registered_by             TEXT NOT NULL,
    registered_at             TIMESTAMPTZ NOT NULL,
    CONSTRAINT research_evidence_scenario_input_manifest_pkey PRIMARY KEY (id),
    CONSTRAINT uq_resim_id_project UNIQUE (id, project_id),
    CONSTRAINT uq_resim_project_request
        UNIQUE (project_id, registration_request_id),
    CONSTRAINT fk_resim_project
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
    CONSTRAINT ck_resim_nonblank CHECK (
        registration_request_id !~ '^[[:space:]]*$'
        AND manifest_namespace !~ '^[[:space:]]*$'
        AND manifest_version !~ '^[[:space:]]*$'
        AND structural_descriptor <> ''
        AND registered_by !~ '^[[:space:]]*$'
    ),
    CONSTRAINT ck_resim_keys CHECK (
        jsonb_typeof(canonical_input_keys_json) = 'array'
    ),
    CONSTRAINT ck_resim_cardinality CHECK (
        input_cardinality >= 0
        AND input_cardinality = jsonb_array_length(canonical_input_keys_json)
    ),
    CONSTRAINT ck_resim_fingerprint CHECK (
        manifest_fingerprint ~ '^[0-9a-f]{64}$'
    )
);

CREATE TABLE IF NOT EXISTS research_evidence_scenario_input_manifest_item (
    manifest_id  UUID NOT NULL,
    project_id   UUID NOT NULL,
    input_key    TEXT NOT NULL,
    item_ordinal INTEGER NOT NULL,
    linked_at    TIMESTAMPTZ NOT NULL,
    CONSTRAINT pk_resimi PRIMARY KEY (manifest_id, input_key),
    CONSTRAINT uq_resimi_ordinal UNIQUE (manifest_id, item_ordinal),
    CONSTRAINT uq_resimi_project_key
        UNIQUE (manifest_id, project_id, input_key),
    CONSTRAINT fk_resimi_manifest
        FOREIGN KEY (manifest_id, project_id)
        REFERENCES research_evidence_scenario_input_manifest(id, project_id)
        ON DELETE RESTRICT,
    CONSTRAINT ck_resimi_nonblank CHECK (input_key !~ '^[[:space:]]*$'),
    CONSTRAINT ck_resimi_ordinal CHECK (item_ordinal >= 1)
);

CREATE TABLE IF NOT EXISTS research_evidence_scenario_input_evaluation (
    id                                UUID NOT NULL DEFAULT gen_random_uuid(),
    project_id                        UUID NOT NULL,
    request_id                        TEXT NOT NULL,
    request_payload_json              JSONB NOT NULL,
    request_fingerprint               TEXT NOT NULL,
    manifest_id                       UUID NOT NULL,
    manifest_version                  TEXT NOT NULL,
    manifest_cardinality              INTEGER NOT NULL,
    manifest_fingerprint              TEXT NOT NULL,
    descriptor_namespace              TEXT NOT NULL,
    descriptor_version                TEXT NOT NULL,
    descriptor_json                   JSONB NOT NULL,
    descriptor_fingerprint            TEXT NOT NULL,
    descriptor_declared_by            TEXT NOT NULL,
    consumer_contract_version         TEXT NOT NULL,
    binding_set_id                    TEXT NOT NULL,
    binding_policy_identifier         TEXT NOT NULL,
    binding_policy_version            TEXT NOT NULL,
    binding_policy_fingerprint        TEXT NOT NULL,
    binding_evaluator_version         TEXT NOT NULL,
    freshness_as_of                   TIMESTAMPTZ NOT NULL,
    evaluation_policy_identifier      TEXT NOT NULL,
    evaluation_policy_version         TEXT NOT NULL,
    evaluation_policy_parameters_json JSONB NOT NULL,
    evaluation_policy_fingerprint     TEXT NOT NULL,
    evaluator_version                 TEXT NOT NULL,
    evaluation_status                 TEXT NOT NULL,
    reason_codes_json                 JSONB NOT NULL,
    evaluation_sequence               INTEGER NOT NULL,
    predecessor_evaluation_id         UUID,
    evaluated_at                      TIMESTAMPTZ NOT NULL,
    CONSTRAINT research_evidence_scenario_input_evaluation_pkey
        PRIMARY KEY (id),
    CONSTRAINT uq_resie_id_scope UNIQUE (
        id, project_id, manifest_id, binding_set_id,
        descriptor_namespace, descriptor_version, descriptor_fingerprint
    ),
    CONSTRAINT uq_resie_id_project_manifest
        UNIQUE (id, project_id, manifest_id),
    CONSTRAINT uq_resie_project_request UNIQUE (project_id, request_id),
    CONSTRAINT uq_resie_scope_sequence UNIQUE (
        project_id, manifest_id, binding_set_id,
        descriptor_namespace, descriptor_version, descriptor_fingerprint,
        evaluation_sequence
    ),
    CONSTRAINT uq_resie_predecessor_once UNIQUE (predecessor_evaluation_id),
    CONSTRAINT fk_resie_project
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
    CONSTRAINT fk_resie_manifest
        FOREIGN KEY (manifest_id, project_id)
        REFERENCES research_evidence_scenario_input_manifest(id, project_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_resie_predecessor FOREIGN KEY (
        predecessor_evaluation_id, project_id, manifest_id, binding_set_id,
        descriptor_namespace, descriptor_version, descriptor_fingerprint
    ) REFERENCES research_evidence_scenario_input_evaluation(
        id, project_id, manifest_id, binding_set_id,
        descriptor_namespace, descriptor_version, descriptor_fingerprint
    ) ON DELETE RESTRICT,
    CONSTRAINT ck_resie_nonblank CHECK (
        request_id !~ '^[[:space:]]*$'
        AND manifest_version !~ '^[[:space:]]*$'
        AND descriptor_namespace !~ '^[[:space:]]*$'
        AND descriptor_version !~ '^[[:space:]]*$'
        AND descriptor_declared_by !~ '^[[:space:]]*$'
        AND consumer_contract_version !~ '^[[:space:]]*$'
        AND binding_set_id !~ '^[[:space:]]*$'
        AND binding_policy_identifier !~ '^[[:space:]]*$'
        AND binding_policy_version !~ '^[[:space:]]*$'
        AND binding_policy_fingerprint !~ '^[[:space:]]*$'
        AND binding_evaluator_version !~ '^[[:space:]]*$'
        AND evaluator_version !~ '^[[:space:]]*$'
    ),
    CONSTRAINT ck_resie_fingerprints CHECK (
        request_fingerprint ~ '^[0-9a-f]{64}$'
        AND manifest_fingerprint ~ '^[0-9a-f]{64}$'
        AND descriptor_fingerprint ~ '^[0-9a-f]{64}$'
        AND evaluation_policy_fingerprint ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_resie_status CHECK (
        evaluation_status IN (
            'does_not_satisfy', 'indeterminate', 'qualified', 'satisfies'
        )
    ),
    CONSTRAINT ck_resie_json_shapes CHECK (
        jsonb_typeof(request_payload_json) = 'object'
        AND jsonb_typeof(evaluation_policy_parameters_json) = 'object'
        AND jsonb_typeof(reason_codes_json) = 'array'
        AND manifest_cardinality > 0
    ),
    CONSTRAINT ck_resie_policy CHECK (
        evaluation_policy_identifier = 'scenario_input.evidence_evaluation'
        AND evaluation_policy_version = '1'
        AND evaluation_policy_fingerprint =
            '70d65b9b32fcf55dfef889a5dbde6d9679bf76e7ae57389d559a9416a6c2a699'
        AND evaluator_version =
            'scenario_input.evidence_evaluation.evaluator.v1'
    ),
    CONSTRAINT ck_resie_sequence CHECK (evaluation_sequence >= 1)
);

CREATE TABLE IF NOT EXISTS
research_evidence_scenario_input_evaluation_input (
    evaluation_id                 UUID NOT NULL,
    project_id                    UUID NOT NULL,
    manifest_id                   UUID NOT NULL,
    input_key                     TEXT NOT NULL,
    selected_binding_id           UUID NOT NULL,
    consumer_contract             TEXT NOT NULL,
    binding_set_id                TEXT NOT NULL,
    binding_sequence              INTEGER NOT NULL,
    selected_binding_has_successor BOOLEAN NOT NULL,
    availability_status           BOOLEAN NOT NULL,
    lineage_is_current            BOOLEAN NOT NULL,
    review_status                 TEXT NOT NULL,
    freshness_status              TEXT NOT NULL,
    drift_status                  TEXT NOT NULL,
    binding_disposition           TEXT NOT NULL,
    dependence_declaration        TEXT NOT NULL,
    dependence_rationale          TEXT NOT NULL,
    input_status                  TEXT NOT NULL,
    reason_codes_json             JSONB NOT NULL,
    linked_at                     TIMESTAMPTZ NOT NULL,
    CONSTRAINT pk_resiei PRIMARY KEY (evaluation_id, input_key),
    CONSTRAINT uq_resiei_manifest_key
        UNIQUE (evaluation_id, manifest_id, input_key),
    CONSTRAINT uq_resiei_binding
        UNIQUE (evaluation_id, selected_binding_id),
    CONSTRAINT fk_resiei_evaluation FOREIGN KEY (
        evaluation_id, project_id, manifest_id
    ) REFERENCES research_evidence_scenario_input_evaluation(
        id, project_id, manifest_id
    ) ON DELETE RESTRICT,
    CONSTRAINT fk_resiei_manifest_item FOREIGN KEY (
        manifest_id, project_id, input_key
    ) REFERENCES research_evidence_scenario_input_manifest_item(
        manifest_id, project_id, input_key
    ) ON DELETE RESTRICT,
    CONSTRAINT fk_resiei_binding FOREIGN KEY (
        selected_binding_id, project_id, consumer_contract,
        binding_set_id, input_key
    ) REFERENCES research_evidence_consumer_input_binding(
        id, project_id, consumer_contract, binding_set_id, input_key
    ) ON DELETE RESTRICT,
    CONSTRAINT ck_resiei_status CHECK (
        input_status IN (
            'does_not_satisfy', 'indeterminate', 'qualified', 'satisfies'
        )
    ),
    CONSTRAINT ck_resiei_dependence CHECK (
        dependence_declaration IN (
            'not_assessed',
            'declared_dependent',
            'declared_independent_not_verified'
        )
    ),
    CONSTRAINT ck_resiei_nonblank CHECK (
        input_key !~ '^[[:space:]]*$'
        AND consumer_contract = 'scenario_input'
        AND binding_set_id !~ '^[[:space:]]*$'
        AND dependence_rationale !~ '^[[:space:]]*$'
        AND binding_sequence >= 1
    ),
    CONSTRAINT ck_resiei_json CHECK (
        jsonb_typeof(reason_codes_json) = 'array'
        AND jsonb_array_length(reason_codes_json) >= 1
    )
);

CREATE TABLE IF NOT EXISTS
research_evidence_scenario_input_evaluation_sequence_allocator (
    project_id             UUID NOT NULL,
    manifest_id            UUID NOT NULL,
    binding_set_id         TEXT NOT NULL,
    descriptor_namespace   TEXT NOT NULL,
    descriptor_version     TEXT NOT NULL,
    descriptor_fingerprint TEXT NOT NULL,
    last_sequence          INTEGER NOT NULL,
    last_evaluation_id     UUID,
    allocator_updated_at   TIMESTAMPTZ NOT NULL,
    CONSTRAINT pk_resie_allocator PRIMARY KEY (
        project_id, manifest_id, binding_set_id,
        descriptor_namespace, descriptor_version, descriptor_fingerprint
    ),
    CONSTRAINT fk_resie_allocator_manifest
        FOREIGN KEY (manifest_id, project_id)
        REFERENCES research_evidence_scenario_input_manifest(id, project_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_resie_allocator_last FOREIGN KEY (
        last_evaluation_id, project_id, manifest_id, binding_set_id,
        descriptor_namespace, descriptor_version, descriptor_fingerprint
    ) REFERENCES research_evidence_scenario_input_evaluation(
        id, project_id, manifest_id, binding_set_id,
        descriptor_namespace, descriptor_version, descriptor_fingerprint
    ) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_resie_allocator_sequence CHECK (
        -- Contract A permits zero only as a transaction-local first-insert
        -- transition.  Reapply rejects every committed zero/historyless row.
        last_sequence >= 0
        AND (
            (last_sequence = 0 AND last_evaluation_id IS NULL)
            OR (last_sequence > 0 AND last_evaluation_id IS NOT NULL)
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_resim_project_namespace_version
    ON research_evidence_scenario_input_manifest(
        project_id, manifest_namespace, manifest_version, registered_at, id
    ) INCLUDE (manifest_fingerprint, input_cardinality);

CREATE INDEX IF NOT EXISTS idx_resie_scope_sequence
    ON research_evidence_scenario_input_evaluation(
        project_id, manifest_id, binding_set_id,
        descriptor_namespace, descriptor_version, descriptor_fingerprint,
        evaluation_sequence
    ) INCLUDE (
        predecessor_evaluation_id, evaluation_status, evaluated_at
    );

CREATE INDEX IF NOT EXISTS idx_resiei_binding
    ON research_evidence_scenario_input_evaluation_input(
        selected_binding_id, evaluation_id
    ) INCLUDE (input_key, input_status);

DO $index_seal$
DECLARE
    v_index record;
BEGIN
    FOR v_index IN
        SELECT c.relname,
               'v58:' || md5(regexp_replace(
                   pg_get_indexdef(c.oid), '[[:space:]]+', '', 'g'
               )) AS seal
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = current_schema()
          AND c.relname = ANY (ARRAY[
              'idx_resim_project_namespace_version',
              'idx_resie_scope_sequence',
              'idx_resiei_binding'
          ])
    LOOP
        EXECUTE format(
            'COMMENT ON INDEX %I IS %L', v_index.relname, v_index.seal
        );
    END LOOP;
END
$index_seal$;

-- Seal every constraint definition for exact reapply drift detection.  A
-- same-name drop/recreate loses this comment; an in-place definition change
-- changes the computed hash and is rejected by the preflight above.
DO $constraint_seal$
DECLARE
    v_constraint record;
BEGIN
    FOR v_constraint IN
        SELECT con.oid, con.conname, c.relname,
               'v58:' || md5(regexp_replace(
                   pg_get_constraintdef(con.oid, true),
                   '[[:space:]]+', '', 'g'
               )) AS seal
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = current_schema()
          AND c.relname = ANY (ARRAY[
              'research_evidence_scenario_input_manifest',
              'research_evidence_scenario_input_manifest_item',
              'research_evidence_scenario_input_evaluation',
              'research_evidence_scenario_input_evaluation_input',
              'research_evidence_scenario_input_evaluation_sequence_allocator'
          ])
    LOOP
        EXECUTE format(
            'COMMENT ON CONSTRAINT %I ON %I IS %L',
            v_constraint.conname, v_constraint.relname, v_constraint.seal
        );
    END LOOP;
END
$constraint_seal$;

CREATE OR REPLACE FUNCTION research_evidence_scenario_input_policy_state(
    p_availability boolean,
    p_lineage_current boolean,
    p_review_status text,
    p_freshness_status text,
    p_drift_status text,
    p_binding_disposition text,
    p_has_successor boolean,
    p_dependence text
) RETURNS TABLE(input_status text, reason_codes_json jsonb)
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
SET search_path = pg_catalog
AS $policy$
    WITH reason(code, ordinal, applies) AS (VALUES
        ('evidence_unavailable', 1, NOT p_availability),
        ('lineage_not_current', 2, NOT p_lineage_current),
        ('review_rejected', 3, p_review_status = 'rejected'),
        ('review_needs_revision', 4, p_review_status = 'needs_revision'),
        ('review_withdrawn', 5, p_review_status = 'withdrawn'),
        ('material_drift', 6, p_drift_status = 'material_drift'),
        ('selected_binding_successor', 7, p_has_successor),
        ('binding_does_not_meet_contract', 8,
            p_binding_disposition = 'does_not_meet_contract'),
        ('review_not_assessed', 9, p_review_status = 'not_assessed'),
        ('freshness_unknown', 10, p_freshness_status = 'unknown'),
        ('drift_not_assessed', 11, p_drift_status = 'not_assessed'),
        ('drift_indeterminate', 12, p_drift_status = 'indeterminate'),
        ('binding_indeterminate', 13,
            p_binding_disposition = 'indeterminate'),
        ('dependence_not_assessed', 14, p_dependence = 'not_assessed'),
        ('freshness_stale', 15, p_freshness_status = 'stale'),
        ('binding_qualified', 16, p_binding_disposition = 'qualified'),
        ('dependence_declared_dependent', 17,
            p_dependence = 'declared_dependent'),
        ('dependence_declared_independent_not_verified', 18,
            p_dependence = 'declared_independent_not_verified')
    )
    SELECT
        CASE
            WHEN bool_or(applies AND ordinal <= 8)
                THEN 'does_not_satisfy'
            WHEN bool_or(applies AND ordinal BETWEEN 9 AND 14)
                THEN 'indeterminate'
            WHEN bool_or(applies AND ordinal BETWEEN 15 AND 18)
                THEN 'qualified'
            ELSE 'satisfies'
        END,
        jsonb_agg(to_jsonb(code) ORDER BY ordinal) FILTER (WHERE applies)
    FROM reason
$policy$;

CREATE OR REPLACE FUNCTION research_evidence_prepare_scenario_input_manifest()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
AS $manifest_prepare$
DECLARE
    v_key_count integer;
    v_distinct_count integer;
    v_existing record;
BEGIN
    IF NEW.input_cardinality IS NOT NULL
       OR NEW.structural_descriptor IS NOT NULL
       OR NEW.manifest_fingerprint IS NOT NULL
       OR NEW.registered_at IS NOT NULL THEN
        RAISE EXCEPTION 'manifest derived fields are server-owned'
            USING ERRCODE = '23514';
    END IF;
    NEW.registration_request_id := btrim(NEW.registration_request_id);
    NEW.manifest_namespace := btrim(NEW.manifest_namespace);
    NEW.manifest_version := btrim(NEW.manifest_version);
    NEW.registered_by := btrim(NEW.registered_by);
    IF NEW.registration_request_id = ''
       OR NEW.manifest_namespace = ''
       OR NEW.manifest_version = ''
       OR NEW.registered_by = ''
       OR jsonb_typeof(NEW.canonical_input_keys_json) <> 'array' THEN
        RAISE EXCEPTION 'malformed manifest registration payload'
            USING ERRCODE = '22023';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM jsonb_array_elements(NEW.canonical_input_keys_json) value
        WHERE jsonb_typeof(value) <> 'string'
           OR btrim(value #>> '{}') = ''
    ) THEN
        RAISE EXCEPTION 'manifest keys must be nonblank strings'
            USING ERRCODE = '22023';
    END IF;
    SELECT count(*), count(DISTINCT btrim(value #>> '{}'))
    INTO v_key_count, v_distinct_count
    FROM jsonb_array_elements(NEW.canonical_input_keys_json) value;
    IF v_key_count <> v_distinct_count THEN
        RAISE EXCEPTION 'manifest keys must be unique'
            USING ERRCODE = '23505';
    END IF;
    SELECT COALESCE(
        jsonb_agg(to_jsonb(input_key) ORDER BY input_key COLLATE "C"),
        '[]'::jsonb
    )
    INTO NEW.canonical_input_keys_json
    FROM (
        SELECT btrim(value #>> '{}') AS input_key
        FROM jsonb_array_elements(NEW.canonical_input_keys_json) value
    ) canonical;
    NEW.input_cardinality := v_key_count;
    NEW.structural_descriptor :=
        'scenario-input-manifest-v1' || E'\n'
        || 'namespace='
        || octet_length(convert_to(NEW.manifest_namespace, 'UTF8'))::text
        || ':' || NEW.manifest_namespace || E'\n'
        || 'version='
        || octet_length(convert_to(NEW.manifest_version, 'UTF8'))::text
        || ':' || NEW.manifest_version || E'\n'
        || 'cardinality=' || NEW.input_cardinality::text || E'\n'
        || COALESCE((
            SELECT string_agg(
                'key=' || octet_length(convert_to(input_key, 'UTF8'))::text
                || ':' || input_key || E'\n',
                '' ORDER BY input_key COLLATE "C"
            )
            FROM jsonb_array_elements_text(
                NEW.canonical_input_keys_json
            ) AS key(input_key)
        ), '');
    NEW.manifest_fingerprint := encode(
        sha256(convert_to(NEW.structural_descriptor, 'UTF8')), 'hex'
    );

    EXECUTE format(
        'SELECT id, manifest_namespace, manifest_version,
                canonical_input_keys_json, registered_by
         FROM %I.research_evidence_scenario_input_manifest
         WHERE project_id = $1 AND registration_request_id = $2
         FOR KEY SHARE',
        TG_TABLE_SCHEMA
    ) INTO v_existing
    USING NEW.project_id, NEW.registration_request_id;
    IF v_existing.id IS NOT NULL THEN
        IF v_existing.manifest_namespace IS DISTINCT FROM NEW.manifest_namespace
           OR v_existing.manifest_version IS DISTINCT FROM NEW.manifest_version
           OR v_existing.canonical_input_keys_json IS DISTINCT FROM
              NEW.canonical_input_keys_json
           OR v_existing.registered_by IS DISTINCT FROM NEW.registered_by THEN
            RAISE EXCEPTION 'immutable manifest request conflict'
                USING ERRCODE = '23505';
        END IF;
        RETURN NULL;
    END IF;
    NEW.registered_at := clock_timestamp();
    RETURN NEW;
END
$manifest_prepare$;

CREATE OR REPLACE FUNCTION research_evidence_prepare_scenario_input_manifest_item()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
AS $manifest_item_prepare$
DECLARE
    v_ordinal integer;
    v_registered_at timestamptz;
BEGIN
    IF NEW.item_ordinal IS NOT NULL OR NEW.linked_at IS NOT NULL THEN
        RAISE EXCEPTION 'manifest item derived fields are server-owned'
            USING ERRCODE = '23514';
    END IF;
    EXECUTE format(
        'SELECT key.ordinality::integer, manifest.registered_at
         FROM %I.research_evidence_scenario_input_manifest manifest
         CROSS JOIN LATERAL jsonb_array_elements_text(
             manifest.canonical_input_keys_json
         ) WITH ORDINALITY key(input_key, ordinality)
         WHERE manifest.id = $1
           AND manifest.project_id = $2
           AND key.input_key = $3',
        TG_TABLE_SCHEMA
    ) INTO v_ordinal, v_registered_at
    USING NEW.manifest_id, NEW.project_id, NEW.input_key;
    IF v_ordinal IS NULL THEN
        RAISE EXCEPTION 'manifest item is not in canonical manifest payload'
            USING ERRCODE = '23514';
    END IF;
    NEW.item_ordinal := v_ordinal;
    NEW.linked_at := v_registered_at;
    RETURN NEW;
END
$manifest_item_prepare$;

CREATE OR REPLACE FUNCTION research_evidence_link_scenario_input_manifest_items()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
AS $manifest_link$
BEGIN
    EXECUTE format(
         'INSERT INTO %I.research_evidence_scenario_input_manifest_item
             (manifest_id, project_id, input_key)
         SELECT $1, $2, input_key
         FROM jsonb_array_elements_text($3) AS key(input_key)',
        TG_TABLE_SCHEMA
    ) USING NEW.id, NEW.project_id, NEW.canonical_input_keys_json;
    RETURN NULL;
END
$manifest_link$;

CREATE OR REPLACE FUNCTION research_evidence_check_scenario_input_manifest()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
AS $manifest_complete$
DECLARE
    v_manifest_id uuid;
    v_project_id uuid;
    v_valid boolean;
BEGIN
    IF TG_TABLE_NAME = 'research_evidence_scenario_input_manifest' THEN
        v_manifest_id := NEW.id;
        v_project_id := NEW.project_id;
    ELSE
        v_manifest_id := NEW.manifest_id;
        v_project_id := NEW.project_id;
    END IF;
    EXECUTE format(
        'SELECT count(item.*) = manifest.input_cardinality
                AND COALESCE(
                    jsonb_agg(
                        to_jsonb(item.input_key)
                        ORDER BY item.item_ordinal
                    ) FILTER (WHERE item.input_key IS NOT NULL),
                    ''[]''::jsonb
                ) = manifest.canonical_input_keys_json
                AND COALESCE(
                    bool_and(item.linked_at = manifest.registered_at), true
                )
         FROM %I.research_evidence_scenario_input_manifest manifest
         LEFT JOIN %I.research_evidence_scenario_input_manifest_item item
           ON item.manifest_id = manifest.id
          AND item.project_id = manifest.project_id
         WHERE manifest.id = $1 AND manifest.project_id = $2
         GROUP BY manifest.id',
        TG_TABLE_SCHEMA, TG_TABLE_SCHEMA
    ) INTO v_valid USING v_manifest_id, v_project_id;
    IF NOT COALESCE(v_valid, false) THEN
        RAISE EXCEPTION 'manifest completeness violation'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END
$manifest_complete$;

CREATE OR REPLACE FUNCTION research_evidence_prepare_scenario_input_evaluation()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
AS $evaluation_prepare$
DECLARE
    v_payload jsonb;
    v_descriptor jsonb;
    v_selected jsonb;
    v_project uuid;
    v_request_id text;
    v_manifest_id uuid;
    v_descriptor_namespace text;
    v_descriptor_version text;
    v_descriptor_json jsonb;
    v_descriptor_fingerprint text;
    v_declared_by text;
    v_freshness_text text;
    v_freshness timestamptz;
    v_manifest record;
    v_count integer;
    v_locked integer := 0;
    v_keys jsonb;
    v_contract_version text;
    v_binding_set text;
    v_binding_policy_id text;
    v_binding_policy_version text;
    v_binding_policy_fingerprint text;
    v_binding_evaluator text;
    v_coherence integer;
    v_freshness_count integer;
    v_canonical_selected jsonb;
    v_status text;
    v_reasons jsonb;
    v_policy_text constant text :=
        '{"dependence_outcomes":{"declared_dependent":"qualified","declared_independent_not_verified":"qualified","not_assessed":"indeterminate"},"reason_order":["evidence_unavailable","lineage_not_current","review_rejected","review_needs_revision","review_withdrawn","material_drift","selected_binding_successor","binding_does_not_meet_contract","review_not_assessed","freshness_unknown","drift_not_assessed","drift_indeterminate","binding_indeterminate","dependence_not_assessed","freshness_stale","binding_qualified","dependence_declared_dependent","dependence_declared_independent_not_verified"],"satisfies_nonempty_manifest_reachable":false,"status_precedence":["does_not_satisfy","indeterminate","qualified","satisfies"]}';
    v_policy_fingerprint text;
    v_existing record;
    v_last integer;
    v_last_id uuid;
    v_history record;
    v_lock record;
BEGIN
    IF NEW.project_id IS NOT NULL
       OR NEW.request_id IS NOT NULL
       OR NEW.request_fingerprint IS NOT NULL
       OR NEW.manifest_id IS NOT NULL
       OR NEW.manifest_version IS NOT NULL
       OR NEW.manifest_cardinality IS NOT NULL
       OR NEW.manifest_fingerprint IS NOT NULL
       OR NEW.descriptor_namespace IS NOT NULL
       OR NEW.descriptor_version IS NOT NULL
       OR NEW.descriptor_json IS NOT NULL
       OR NEW.descriptor_fingerprint IS NOT NULL
       OR NEW.descriptor_declared_by IS NOT NULL
       OR NEW.consumer_contract_version IS NOT NULL
       OR NEW.binding_set_id IS NOT NULL
       OR NEW.binding_policy_identifier IS NOT NULL
       OR NEW.binding_policy_version IS NOT NULL
       OR NEW.binding_policy_fingerprint IS NOT NULL
       OR NEW.binding_evaluator_version IS NOT NULL
       OR NEW.freshness_as_of IS NOT NULL
       OR NEW.evaluation_policy_identifier IS NOT NULL
       OR NEW.evaluation_policy_version IS NOT NULL
       OR NEW.evaluation_policy_parameters_json IS NOT NULL
       OR NEW.evaluation_policy_fingerprint IS NOT NULL
       OR NEW.evaluator_version IS NOT NULL
       OR NEW.evaluation_status IS NOT NULL
       OR NEW.reason_codes_json IS NOT NULL
       OR NEW.evaluation_sequence IS NOT NULL
       OR NEW.predecessor_evaluation_id IS NOT NULL
       OR NEW.evaluated_at IS NOT NULL THEN
        RAISE EXCEPTION 'evaluation derived fields are server-owned'
            USING ERRCODE = '23514';
    END IF;
    v_payload := NEW.request_payload_json;
    IF jsonb_typeof(v_payload) <> 'object'
       OR (SELECT count(*) FROM jsonb_object_keys(v_payload)) <> 6
       OR NOT v_payload ?& ARRAY[
           'project_id', 'request_id', 'manifest_id', 'descriptor',
           'selected_bindings', 'freshness_as_of'
       ] THEN
        RAISE EXCEPTION 'malformed evaluation request payload'
            USING ERRCODE = '22023';
    END IF;
    BEGIN
        v_project := (v_payload->>'project_id')::uuid;
        v_manifest_id := (v_payload->>'manifest_id')::uuid;
        v_request_id := btrim(v_payload->>'request_id');
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'malformed project, manifest, or request identity'
            USING ERRCODE = '22023';
    END;
    IF v_request_id = '' THEN
        RAISE EXCEPTION 'request_id must not be blank'
            USING ERRCODE = '22023';
    END IF;
    v_descriptor := v_payload->'descriptor';
    IF jsonb_typeof(v_descriptor) <> 'object'
       OR (SELECT count(*) FROM jsonb_object_keys(v_descriptor)) <> 4
       OR NOT v_descriptor ?& ARRAY[
           'namespace', 'descriptor_version', 'descriptor', 'declared_by'
       ]
       OR v_descriptor->'descriptor' = 'null'::jsonb THEN
        RAISE EXCEPTION 'malformed opaque descriptor'
            USING ERRCODE = '22023';
    END IF;
    v_descriptor_namespace := btrim(v_descriptor->>'namespace');
    v_descriptor_version := btrim(v_descriptor->>'descriptor_version');
    v_descriptor_json := v_descriptor->'descriptor';
    v_declared_by := btrim(v_descriptor->>'declared_by');
    IF v_descriptor_namespace = ''
       OR v_descriptor_version = ''
       OR v_declared_by = '' THEN
        RAISE EXCEPTION 'opaque descriptor text must not be blank'
            USING ERRCODE = '22023';
    END IF;
    v_descriptor_fingerprint := encode(
        sha256(convert_to(v_descriptor_json::text, 'UTF8')), 'hex'
    );
    v_freshness_text := v_payload->>'freshness_as_of';
    IF v_freshness_text IS NULL
       OR v_freshness_text !~
          '(Z|z|[+-][0-9]{2}(:?[0-9]{2})?)$' THEN
        RAISE EXCEPTION 'freshness_as_of must include a timezone'
            USING ERRCODE = '22023';
    END IF;
    BEGIN
        v_freshness := v_freshness_text::timestamptz;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'malformed freshness_as_of'
            USING ERRCODE = '22023';
    END;
    v_selected := v_payload->'selected_bindings';
    IF jsonb_typeof(v_selected) <> 'array' THEN
        RAISE EXCEPTION 'selected_bindings must be an array'
            USING ERRCODE = '22023';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(v_selected) selected
        WHERE jsonb_typeof(selected) <> 'object'
           OR (SELECT count(*) FROM jsonb_object_keys(selected)) <> 3
           OR NOT selected ?& ARRAY[
               'binding_id', 'dependence_declaration', 'rationale'
           ]
           OR selected->>'dependence_declaration' NOT IN (
               'not_assessed',
               'declared_dependent',
               'declared_independent_not_verified'
           )
           OR btrim(selected->>'rationale') = ''
    ) THEN
        RAISE EXCEPTION 'malformed dependence declaration or rationale'
            USING ERRCODE = '22023';
    END IF;
    BEGIN
        SELECT count(*), count(DISTINCT (selected->>'binding_id')::uuid)
        INTO v_count, v_coherence
        FROM jsonb_array_elements(v_selected) selected;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'selected binding identity must be a UUID'
            USING ERRCODE = '22023';
    END;
    IF v_count <> v_coherence THEN
        RAISE EXCEPTION 'selected binding UUIDs must be unique'
            USING ERRCODE = '23505';
    END IF;

    EXECUTE format(
        'SELECT manifest_version, input_cardinality, manifest_fingerprint,
                canonical_input_keys_json
         FROM %I.research_evidence_scenario_input_manifest
         WHERE id = $1 AND project_id = $2',
        TG_TABLE_SCHEMA
    ) INTO v_manifest USING v_manifest_id, v_project;
    IF v_manifest.manifest_version IS NULL
       OR v_manifest.input_cardinality = 0
       OR v_count <> v_manifest.input_cardinality THEN
        RAISE EXCEPTION 'manifest selection is missing or incomplete'
            USING ERRCODE = '23514';
    END IF;

    -- Read only lock-routing identity before locking; policy and successor
    -- state are read only after every selected v57 allocator is locked.
    EXECUTE format(
        'WITH selected AS (
             SELECT (value->>''binding_id'')::uuid AS binding_id
             FROM jsonb_array_elements($1) value
         )
         SELECT count(binding.id)::integer,
                COALESCE(
                    jsonb_agg(
                        to_jsonb(binding.input_key)
                        ORDER BY binding.input_key COLLATE "C"
                    ) FILTER (WHERE binding.id IS NOT NULL),
                    ''[]''::jsonb
                )
         FROM selected
         LEFT JOIN %I.research_evidence_consumer_input_binding binding
           ON binding.id = selected.binding_id
          AND binding.project_id = $2
          AND binding.consumer_contract = ''scenario_input''',
        TG_TABLE_SCHEMA
    ) INTO v_count, v_keys USING v_selected, v_project;
    IF v_count <> v_manifest.input_cardinality
       OR v_keys IS DISTINCT FROM v_manifest.canonical_input_keys_json THEN
        RAISE EXCEPTION
            'selected bindings must exactly match manifest project and keys'
            USING ERRCODE = '23514';
    END IF;

    FOR v_lock IN EXECUTE format(
        'WITH selected AS (
             SELECT (value->>''binding_id'')::uuid AS binding_id
             FROM jsonb_array_elements($1) value
         )
         SELECT allocator.project_id
         FROM selected
         JOIN %I.research_evidence_consumer_input_binding binding
           ON binding.id = selected.binding_id
         JOIN %I.research_evidence_consumer_input_binding_sequence_allocator
              allocator
           ON allocator.project_id = binding.project_id
          AND allocator.consumer_contract = binding.consumer_contract
          AND allocator.binding_set_id = binding.binding_set_id
          AND allocator.input_key = binding.input_key
         ORDER BY allocator.project_id, allocator.consumer_contract,
                  allocator.binding_set_id, allocator.input_key
         FOR UPDATE OF allocator',
        TG_TABLE_SCHEMA, TG_TABLE_SCHEMA
    ) USING v_selected
    LOOP
        v_locked := v_locked + 1;
    END LOOP;
    IF v_locked <> v_manifest.input_cardinality THEN
        RAISE EXCEPTION 'selected v57 binding allocator is missing'
            USING ERRCODE = '23514';
    END IF;

    EXECUTE format(
        $state$
        WITH selected AS (
            SELECT
                (value->>'binding_id')::uuid AS binding_id,
                value->>'dependence_declaration' AS dependence,
                btrim(value->>'rationale') AS rationale
            FROM jsonb_array_elements($1) value
        ),
        state AS (
            SELECT binding.*, selected.dependence, selected.rationale,
                   allocator.last_sequence > binding.binding_sequence
                       AS has_successor,
                   policy.input_status, policy.reason_codes_json
            FROM selected
            JOIN %I.research_evidence_consumer_input_binding binding
              ON binding.id = selected.binding_id
             AND binding.project_id = $2
             AND binding.consumer_contract = 'scenario_input'
            JOIN %I.research_evidence_consumer_input_binding_sequence_allocator
                 allocator
              ON allocator.project_id = binding.project_id
             AND allocator.consumer_contract = binding.consumer_contract
             AND allocator.binding_set_id = binding.binding_set_id
             AND allocator.input_key = binding.input_key
            CROSS JOIN LATERAL
                %I.research_evidence_scenario_input_policy_state(
                    binding.availability_status,
                    binding.lineage_is_current,
                    binding.review_status,
                    binding.freshness_status,
                    binding.drift_status,
                    binding.consumer_disposition,
                    allocator.last_sequence > binding.binding_sequence,
                    selected.dependence
                ) policy
        )
        SELECT
            min(consumer_contract_version),
            min(binding_set_id),
            min(policy_identifier),
            min(policy_version),
            min(policy_fingerprint),
            min(evaluator_version),
            count(DISTINCT (
                consumer_contract_version, binding_set_id,
                policy_identifier, policy_version,
                policy_fingerprint, evaluator_version
            ))::integer,
            count(DISTINCT freshness_as_of)::integer,
            jsonb_agg(
                jsonb_build_object(
                    'binding_id', id::text,
                    'dependence_declaration', dependence,
                    'rationale', rationale
                )
                ORDER BY input_key COLLATE "C", id
            ),
            CASE min(CASE input_status
                WHEN 'does_not_satisfy' THEN 1
                WHEN 'indeterminate' THEN 2
                WHEN 'qualified' THEN 3
                ELSE 4
            END)
                WHEN 1 THEN 'does_not_satisfy'
                WHEN 2 THEN 'indeterminate'
                WHEN 3 THEN 'qualified'
                ELSE 'satisfies'
            END,
            (
                SELECT jsonb_agg(to_jsonb(code) ORDER BY ordinal)
                FROM (
                    SELECT DISTINCT ON (code) code, ordinal
                    FROM state source
                    CROSS JOIN LATERAL jsonb_array_elements_text(
                        source.reason_codes_json
                    ) AS reason(code)
                    CROSS JOIN LATERAL (
                        SELECT array_position(ARRAY[
                            'evidence_unavailable',
                            'lineage_not_current',
                            'review_rejected',
                            'review_needs_revision',
                            'review_withdrawn',
                            'material_drift',
                            'selected_binding_successor',
                            'binding_does_not_meet_contract',
                            'review_not_assessed',
                            'freshness_unknown',
                            'drift_not_assessed',
                            'drift_indeterminate',
                            'binding_indeterminate',
                            'dependence_not_assessed',
                            'freshness_stale',
                            'binding_qualified',
                            'dependence_declared_dependent',
                            'dependence_declared_independent_not_verified'
                        ]::text[], code) AS ordinal
                    ) position
                    ORDER BY code, ordinal
                ) ordered
            )
        FROM state
        $state$,
        TG_TABLE_SCHEMA, TG_TABLE_SCHEMA, TG_TABLE_SCHEMA
    ) INTO
        v_contract_version, v_binding_set, v_binding_policy_id,
        v_binding_policy_version, v_binding_policy_fingerprint,
        v_binding_evaluator, v_coherence, v_freshness_count,
        v_canonical_selected, v_status, v_reasons
    USING v_selected, v_project;
    IF v_coherence <> 1
       OR v_freshness_count <> 1 THEN
        RAISE EXCEPTION
            'selected bindings have incoherent contract, set, policy, evaluator, or freshness'
            USING ERRCODE = '23514';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM jsonb_array_elements(v_selected) selected
        WHERE true
    ) OR v_binding_policy_fingerprint = '' THEN
        RAISE EXCEPTION 'binding policy fingerprint must be coherent and nonblank'
            USING ERRCODE = '23514';
    END IF;
    EXECUTE format(
        'SELECT bool_and(binding.freshness_as_of = $2) AS all_match
         FROM jsonb_array_elements($1) selected
         JOIN %I.research_evidence_consumer_input_binding binding
           ON binding.id = (selected->>''binding_id'')::uuid',
        TG_TABLE_SCHEMA
    ) INTO v_existing USING v_selected, v_freshness;
    IF NOT COALESCE(v_existing.all_match, false) THEN
        RAISE EXCEPTION 'supplied freshness_as_of does not match every binding'
            USING ERRCODE = '23514';
    END IF;

    v_policy_fingerprint := encode(
        sha256(convert_to(v_policy_text, 'UTF8')), 'hex'
    );
    IF v_policy_fingerprint <>
       '70d65b9b32fcf55dfef889a5dbde6d9679bf76e7ae57389d559a9416a6c2a699' THEN
        RAISE EXCEPTION 'evaluation policy fingerprint drift'
            USING ERRCODE = '23514';
    END IF;
    NEW.request_payload_json := jsonb_build_object(
        'project_id', v_project::text,
        'request_id', v_request_id,
        'manifest', jsonb_build_object(
            'id', v_manifest_id::text,
            'version', v_manifest.manifest_version,
            'fingerprint', v_manifest.manifest_fingerprint
        ),
        'descriptor', jsonb_build_object(
            'namespace', v_descriptor_namespace,
            'descriptor_version', v_descriptor_version,
            'descriptor', v_descriptor_json,
            'fingerprint', v_descriptor_fingerprint,
            'declared_by', v_declared_by
        ),
        'selected_bindings', v_canonical_selected,
        'binding_contract', jsonb_build_object(
            'consumer_contract', 'scenario_input',
            'consumer_contract_version', v_contract_version,
            'binding_set_id', v_binding_set,
            'policy_identifier', v_binding_policy_id,
            'policy_version', v_binding_policy_version,
            'policy_fingerprint', v_binding_policy_fingerprint,
            'evaluator_version', v_binding_evaluator
        ),
        'evaluation_policy', jsonb_build_object(
            'identifier', 'scenario_input.evidence_evaluation',
            'version', '1',
            'parameters', v_policy_text::jsonb,
            'fingerprint', v_policy_fingerprint,
            'evaluator_version',
                'scenario_input.evidence_evaluation.evaluator.v1'
        ),
        'freshness_as_of', to_char(
            v_freshness AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
        )
    );
    NEW.request_fingerprint := encode(
        sha256(convert_to(NEW.request_payload_json::text, 'UTF8')), 'hex'
    );

    EXECUTE format(
        'SELECT id, request_fingerprint
         FROM %I.research_evidence_scenario_input_evaluation
         WHERE project_id = $1 AND request_id = $2
         FOR KEY SHARE',
        TG_TABLE_SCHEMA
    ) INTO v_existing USING v_project, v_request_id;
    IF v_existing.id IS NOT NULL THEN
        IF v_existing.request_fingerprint <> NEW.request_fingerprint THEN
            RAISE EXCEPTION 'immutable evaluation request conflict'
                USING ERRCODE = '23505';
        END IF;
        RETURN NULL;
    END IF;

    -- All v57 allocator locks are held before this R1.7 allocator is touched.
    EXECUTE format(
        'INSERT INTO
             %I.research_evidence_scenario_input_evaluation_sequence_allocator
             (project_id, manifest_id, binding_set_id,
              descriptor_namespace, descriptor_version,
              descriptor_fingerprint, last_sequence, last_evaluation_id,
              allocator_updated_at)
         VALUES ($1, $2, $3, $4, $5, $6, 0, NULL, clock_timestamp())
         ON CONFLICT (
             project_id, manifest_id, binding_set_id,
             descriptor_namespace, descriptor_version,
             descriptor_fingerprint
         ) DO NOTHING',
        TG_TABLE_SCHEMA
    ) USING
        v_project, v_manifest_id, v_binding_set, v_descriptor_namespace,
        v_descriptor_version, v_descriptor_fingerprint;
    EXECUTE format(
        'SELECT last_sequence, last_evaluation_id
         FROM %I.research_evidence_scenario_input_evaluation_sequence_allocator
         WHERE project_id = $1 AND manifest_id = $2 AND binding_set_id = $3
           AND descriptor_namespace = $4 AND descriptor_version = $5
           AND descriptor_fingerprint = $6
         FOR UPDATE',
        TG_TABLE_SCHEMA
    ) INTO v_last, v_last_id USING
        v_project, v_manifest_id, v_binding_set, v_descriptor_namespace,
        v_descriptor_version, v_descriptor_fingerprint;
    EXECUTE format(
        'SELECT count(*)::integer AS row_count,
                min(evaluation_sequence) AS min_sequence,
                max(evaluation_sequence) AS max_sequence,
                (
                    SELECT latest.id
                    FROM %I.research_evidence_scenario_input_evaluation latest
                    WHERE latest.project_id = $1
                      AND latest.manifest_id = $2
                      AND latest.binding_set_id = $3
                      AND latest.descriptor_namespace = $4
                      AND latest.descriptor_version = $5
                      AND latest.descriptor_fingerprint = $6
                    ORDER BY latest.evaluation_sequence DESC, latest.id DESC
                    LIMIT 1
                ) AS last_id
         FROM %I.research_evidence_scenario_input_evaluation
         WHERE project_id = $1 AND manifest_id = $2 AND binding_set_id = $3
           AND descriptor_namespace = $4 AND descriptor_version = $5
           AND descriptor_fingerprint = $6',
        TG_TABLE_SCHEMA, TG_TABLE_SCHEMA
    ) INTO v_history USING
        v_project, v_manifest_id, v_binding_set, v_descriptor_namespace,
        v_descriptor_version, v_descriptor_fingerprint;
    IF v_history.row_count <> v_last
       OR (v_last = 0 AND v_last_id IS NOT NULL)
       OR (
           v_last > 0
           AND (
               v_history.min_sequence <> 1
               OR v_history.max_sequence IS DISTINCT FROM v_last
               OR v_history.last_id IS DISTINCT FROM v_last_id
           )
       ) THEN
        RAISE EXCEPTION 'malformed evaluation sequence history'
            USING ERRCODE = '23514';
    END IF;

    NEW.project_id := v_project;
    NEW.request_id := v_request_id;
    NEW.manifest_id := v_manifest_id;
    NEW.manifest_version := v_manifest.manifest_version;
    NEW.manifest_cardinality := v_manifest.input_cardinality;
    NEW.manifest_fingerprint := v_manifest.manifest_fingerprint;
    NEW.descriptor_namespace := v_descriptor_namespace;
    NEW.descriptor_version := v_descriptor_version;
    NEW.descriptor_json := v_descriptor_json;
    NEW.descriptor_fingerprint := v_descriptor_fingerprint;
    NEW.descriptor_declared_by := v_declared_by;
    NEW.consumer_contract_version := v_contract_version;
    NEW.binding_set_id := v_binding_set;
    NEW.binding_policy_identifier := v_binding_policy_id;
    NEW.binding_policy_version := v_binding_policy_version;
    NEW.binding_policy_fingerprint := v_binding_policy_fingerprint;
    NEW.binding_evaluator_version := v_binding_evaluator;
    NEW.freshness_as_of := v_freshness;
    NEW.evaluation_policy_identifier :=
        'scenario_input.evidence_evaluation';
    NEW.evaluation_policy_version := '1';
    NEW.evaluation_policy_parameters_json := v_policy_text::jsonb;
    NEW.evaluation_policy_fingerprint := v_policy_fingerprint;
    NEW.evaluator_version :=
        'scenario_input.evidence_evaluation.evaluator.v1';
    NEW.evaluation_status := v_status;
    NEW.reason_codes_json := v_reasons;
    NEW.evaluation_sequence := v_last + 1;
    NEW.predecessor_evaluation_id := v_last_id;
    NEW.evaluated_at := clock_timestamp();
    EXECUTE format(
        'UPDATE
             %I.research_evidence_scenario_input_evaluation_sequence_allocator
         SET last_sequence = $7, last_evaluation_id = $8,
             allocator_updated_at = $9
         WHERE project_id = $1 AND manifest_id = $2 AND binding_set_id = $3
           AND descriptor_namespace = $4 AND descriptor_version = $5
           AND descriptor_fingerprint = $6',
        TG_TABLE_SCHEMA
    ) USING
        v_project, v_manifest_id, v_binding_set, v_descriptor_namespace,
        v_descriptor_version, v_descriptor_fingerprint,
        NEW.evaluation_sequence, NEW.id, NEW.evaluated_at;
    RETURN NEW;
END
$evaluation_prepare$;

CREATE OR REPLACE FUNCTION research_evidence_prepare_scenario_input_evaluation_input()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
AS $input_prepare$
DECLARE
    v_state record;
BEGIN
    IF NEW.project_id IS NOT NULL
       OR NEW.manifest_id IS NOT NULL
       OR NEW.input_key IS NOT NULL
       OR NEW.consumer_contract IS NOT NULL
       OR NEW.binding_set_id IS NOT NULL
       OR NEW.binding_sequence IS NOT NULL
       OR NEW.selected_binding_has_successor IS NOT NULL
       OR NEW.availability_status IS NOT NULL
       OR NEW.lineage_is_current IS NOT NULL
       OR NEW.review_status IS NOT NULL
       OR NEW.freshness_status IS NOT NULL
       OR NEW.drift_status IS NOT NULL
       OR NEW.binding_disposition IS NOT NULL
       OR NEW.dependence_declaration IS NOT NULL
       OR NEW.dependence_rationale IS NOT NULL
       OR NEW.input_status IS NOT NULL
       OR NEW.reason_codes_json IS NOT NULL
       OR NEW.linked_at IS NOT NULL THEN
        RAISE EXCEPTION 'evaluation input derived fields are server-owned'
            USING ERRCODE = '23514';
    END IF;
    EXECUTE format(
        $input$
        SELECT evaluation.project_id, evaluation.manifest_id,
               binding.input_key, binding.consumer_contract,
               binding.binding_set_id, binding.binding_sequence,
               allocator.last_sequence > binding.binding_sequence
                   AS has_successor,
               binding.availability_status, binding.lineage_is_current,
               binding.review_status, binding.freshness_status,
               binding.drift_status, binding.consumer_disposition,
               selected->>'dependence_declaration'
                   AS dependence_declaration,
               selected->>'rationale' AS rationale,
               policy.input_status, policy.reason_codes_json,
               evaluation.evaluated_at
        FROM %I.research_evidence_scenario_input_evaluation evaluation
        CROSS JOIN LATERAL jsonb_array_elements(
            evaluation.request_payload_json->'selected_bindings'
        ) selected
        JOIN %I.research_evidence_consumer_input_binding binding
          ON binding.id = (selected->>'binding_id')::uuid
         AND binding.id = $2
         AND binding.project_id = evaluation.project_id
         AND binding.consumer_contract = 'scenario_input'
         AND binding.binding_set_id = evaluation.binding_set_id
        JOIN %I.research_evidence_consumer_input_binding_sequence_allocator
             allocator
          ON allocator.project_id = binding.project_id
         AND allocator.consumer_contract = binding.consumer_contract
         AND allocator.binding_set_id = binding.binding_set_id
         AND allocator.input_key = binding.input_key
        CROSS JOIN LATERAL
            %I.research_evidence_scenario_input_policy_state(
                binding.availability_status,
                binding.lineage_is_current,
                binding.review_status,
                binding.freshness_status,
                binding.drift_status,
                binding.consumer_disposition,
                allocator.last_sequence > binding.binding_sequence,
                selected->>'dependence_declaration'
            ) policy
        WHERE evaluation.id = $1
        $input$,
        TG_TABLE_SCHEMA, TG_TABLE_SCHEMA, TG_TABLE_SCHEMA, TG_TABLE_SCHEMA
    ) INTO v_state USING NEW.evaluation_id, NEW.selected_binding_id;
    IF v_state.project_id IS NULL THEN
        RAISE EXCEPTION 'binding is not selected by evaluation payload'
            USING ERRCODE = '23514';
    END IF;
    NEW.project_id := v_state.project_id;
    NEW.manifest_id := v_state.manifest_id;
    NEW.input_key := v_state.input_key;
    NEW.consumer_contract := v_state.consumer_contract;
    NEW.binding_set_id := v_state.binding_set_id;
    NEW.binding_sequence := v_state.binding_sequence;
    NEW.selected_binding_has_successor := v_state.has_successor;
    NEW.availability_status := v_state.availability_status;
    NEW.lineage_is_current := v_state.lineage_is_current;
    NEW.review_status := v_state.review_status;
    NEW.freshness_status := v_state.freshness_status;
    NEW.drift_status := v_state.drift_status;
    NEW.binding_disposition := v_state.consumer_disposition;
    NEW.dependence_declaration := v_state.dependence_declaration;
    NEW.dependence_rationale := v_state.rationale;
    NEW.input_status := v_state.input_status;
    NEW.reason_codes_json := v_state.reason_codes_json;
    NEW.linked_at := v_state.evaluated_at;
    RETURN NEW;
END
$input_prepare$;

CREATE OR REPLACE FUNCTION research_evidence_link_scenario_input_evaluation_inputs()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
AS $input_link$
BEGIN
    EXECUTE format(
        'INSERT INTO %I.research_evidence_scenario_input_evaluation_input
             (evaluation_id, selected_binding_id)
         SELECT $1, (selected->>''binding_id'')::uuid
         FROM jsonb_array_elements(
             $2->''selected_bindings''
         ) selected',
        TG_TABLE_SCHEMA
    ) USING NEW.id, NEW.request_payload_json;
    RETURN NULL;
END
$input_link$;

CREATE OR REPLACE FUNCTION research_evidence_check_scenario_input_evaluation()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
AS $evaluation_complete$
DECLARE
    v_evaluation_id uuid;
    v_valid boolean;
BEGIN
    IF TG_TABLE_NAME = 'research_evidence_scenario_input_evaluation' THEN
        v_evaluation_id := NEW.id;
    ELSE
        v_evaluation_id := NEW.evaluation_id;
    END IF;
    EXECUTE format(
        $check$
        SELECT
            count(child.*) = evaluation.manifest_cardinality
            AND count(DISTINCT child.input_key) =
                evaluation.manifest_cardinality
            AND count(DISTINCT child.selected_binding_id) =
                evaluation.manifest_cardinality
            AND bool_and(child.linked_at = evaluation.evaluated_at)
            AND CASE min(CASE child.input_status
                WHEN 'does_not_satisfy' THEN 1
                WHEN 'indeterminate' THEN 2
                WHEN 'qualified' THEN 3
                ELSE 4
            END)
                WHEN 1 THEN 'does_not_satisfy'
                WHEN 2 THEN 'indeterminate'
                WHEN 3 THEN 'qualified'
                ELSE 'satisfies'
            END = evaluation.evaluation_status
            AND (
                SELECT jsonb_agg(to_jsonb(code) ORDER BY ordinal)
                FROM (
                    SELECT DISTINCT ON (code) code, ordinal
                    FROM %I.research_evidence_scenario_input_evaluation_input c
                    CROSS JOIN LATERAL jsonb_array_elements_text(
                        c.reason_codes_json
                    ) AS reason(code)
                    CROSS JOIN LATERAL (
                        SELECT array_position(ARRAY[
                            'evidence_unavailable',
                            'lineage_not_current',
                            'review_rejected',
                            'review_needs_revision',
                            'review_withdrawn',
                            'material_drift',
                            'selected_binding_successor',
                            'binding_does_not_meet_contract',
                            'review_not_assessed',
                            'freshness_unknown',
                            'drift_not_assessed',
                            'drift_indeterminate',
                            'binding_indeterminate',
                            'dependence_not_assessed',
                            'freshness_stale',
                            'binding_qualified',
                            'dependence_declared_dependent',
                            'dependence_declared_independent_not_verified'
                        ]::text[], code) AS ordinal
                    ) position
                    WHERE c.evaluation_id = evaluation.id
                    ORDER BY code, ordinal
                ) ordered
            ) = evaluation.reason_codes_json
        FROM %I.research_evidence_scenario_input_evaluation evaluation
        LEFT JOIN %I.research_evidence_scenario_input_evaluation_input child
          ON child.evaluation_id = evaluation.id
        WHERE evaluation.id = $1
        GROUP BY evaluation.id
        $check$,
        TG_TABLE_SCHEMA, TG_TABLE_SCHEMA, TG_TABLE_SCHEMA
    ) INTO v_valid USING v_evaluation_id;
    IF NOT COALESCE(v_valid, false) THEN
        RAISE EXCEPTION 'evaluation child/completeness integrity violation'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END
$evaluation_complete$;

CREATE OR REPLACE FUNCTION research_evidence_register_scenario_input_manifest(
    p_project_id uuid,
    p_request_id text,
    p_namespace text,
    p_version text,
    p_input_keys jsonb,
    p_registered_by text
) RETURNS SETOF research_evidence_scenario_input_manifest
LANGUAGE plpgsql
SECURITY INVOKER
AS $register_manifest$
DECLARE
    v_id uuid;
BEGIN
    BEGIN
        INSERT INTO research_evidence_scenario_input_manifest(
            project_id, registration_request_id, manifest_namespace,
            manifest_version, canonical_input_keys_json, registered_by
        ) VALUES (
            p_project_id, p_request_id, p_namespace,
            p_version, p_input_keys, p_registered_by
        ) RETURNING id INTO v_id;
    EXCEPTION WHEN unique_violation THEN
        -- A concurrent winner is now visible.  Re-run the same trigger path;
        -- it returns NULL for an exact retry and raises for payload conflict.
        INSERT INTO research_evidence_scenario_input_manifest(
            project_id, registration_request_id, manifest_namespace,
            manifest_version, canonical_input_keys_json, registered_by
        ) VALUES (
            p_project_id, p_request_id, p_namespace,
            p_version, p_input_keys, p_registered_by
        ) RETURNING id INTO v_id;
    END;
    IF v_id IS NULL THEN
        SELECT id INTO v_id
        FROM research_evidence_scenario_input_manifest
        WHERE project_id = p_project_id
          AND registration_request_id = btrim(p_request_id);
    END IF;
    RETURN QUERY
    SELECT * FROM research_evidence_scenario_input_manifest WHERE id = v_id;
END
$register_manifest$;

CREATE OR REPLACE FUNCTION research_evidence_create_scenario_input_evaluation(
    p_request_payload jsonb
) RETURNS SETOF research_evidence_scenario_input_evaluation
LANGUAGE plpgsql
SECURITY INVOKER
AS $create_evaluation$
DECLARE
    v_id uuid;
    v_project_id uuid;
    v_request_id text;
BEGIN
    BEGIN
        INSERT INTO research_evidence_scenario_input_evaluation(
            request_payload_json
        ) VALUES (p_request_payload)
        RETURNING id INTO v_id;
    EXCEPTION WHEN unique_violation THEN
        INSERT INTO research_evidence_scenario_input_evaluation(
            request_payload_json
        ) VALUES (p_request_payload)
        RETURNING id INTO v_id;
    END;
    IF v_id IS NULL THEN
        v_project_id := (p_request_payload->>'project_id')::uuid;
        v_request_id := btrim(p_request_payload->>'request_id');
        SELECT id INTO v_id
        FROM research_evidence_scenario_input_evaluation
        WHERE project_id = v_project_id AND request_id = v_request_id;
    END IF;
    RETURN QUERY
    SELECT * FROM research_evidence_scenario_input_evaluation WHERE id = v_id;
END
$create_evaluation$;

REVOKE ALL ON FUNCTION research_evidence_scenario_input_policy_state(
    boolean, boolean, text, text, text, text, boolean, text
) FROM PUBLIC;
REVOKE ALL ON FUNCTION
    research_evidence_prepare_scenario_input_manifest() FROM PUBLIC;
REVOKE ALL ON FUNCTION
    research_evidence_link_scenario_input_manifest_items() FROM PUBLIC;
REVOKE ALL ON FUNCTION
    research_evidence_prepare_scenario_input_manifest_item() FROM PUBLIC;
REVOKE ALL ON FUNCTION
    research_evidence_check_scenario_input_manifest() FROM PUBLIC;
REVOKE ALL ON FUNCTION
    research_evidence_prepare_scenario_input_evaluation() FROM PUBLIC;
REVOKE ALL ON FUNCTION
    research_evidence_link_scenario_input_evaluation_inputs() FROM PUBLIC;
REVOKE ALL ON FUNCTION
    research_evidence_prepare_scenario_input_evaluation_input() FROM PUBLIC;
REVOKE ALL ON FUNCTION
    research_evidence_check_scenario_input_evaluation() FROM PUBLIC;
REVOKE ALL ON FUNCTION research_evidence_register_scenario_input_manifest(
    uuid, text, text, text, jsonb, text
) FROM PUBLIC;
REVOKE ALL ON FUNCTION research_evidence_create_scenario_input_evaluation(
    jsonb
) FROM PUBLIC;
REVOKE ALL ON TABLE
    research_evidence_scenario_input_manifest,
    research_evidence_scenario_input_manifest_item,
    research_evidence_scenario_input_evaluation,
    research_evidence_scenario_input_evaluation_input,
    research_evidence_scenario_input_evaluation_sequence_allocator
    FROM PUBLIC;

DO $triggers$
DECLARE
    v_statement text;
BEGIN
    FOREACH v_statement IN ARRAY ARRAY[
        $sql$CREATE TRIGGER trg_resim_prepare_insert
            BEFORE INSERT ON research_evidence_scenario_input_manifest
            FOR EACH ROW EXECUTE FUNCTION
                research_evidence_prepare_scenario_input_manifest()$sql$,
        $sql$CREATE TRIGGER trg_resim_link_items
            AFTER INSERT ON research_evidence_scenario_input_manifest
            FOR EACH ROW EXECUTE FUNCTION
                research_evidence_link_scenario_input_manifest_items()$sql$,
        $sql$CREATE TRIGGER trg_resim_no_mutation
            BEFORE UPDATE OR DELETE
            ON research_evidence_scenario_input_manifest
            FOR EACH ROW EXECUTE FUNCTION slicea_reject_mutation()$sql$,
        $sql$CREATE TRIGGER trg_resimi_prepare_insert
            BEFORE INSERT ON research_evidence_scenario_input_manifest_item
            FOR EACH ROW EXECUTE FUNCTION
                research_evidence_prepare_scenario_input_manifest_item()$sql$,
        $sql$CREATE TRIGGER trg_resimi_no_mutation
            BEFORE UPDATE OR DELETE
            ON research_evidence_scenario_input_manifest_item
            FOR EACH ROW EXECUTE FUNCTION slicea_reject_mutation()$sql$,
        $sql$CREATE CONSTRAINT TRIGGER trg_resim_complete
            AFTER INSERT ON research_evidence_scenario_input_manifest
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION
                research_evidence_check_scenario_input_manifest()$sql$,
        $sql$CREATE CONSTRAINT TRIGGER trg_resimi_complete
            AFTER INSERT ON research_evidence_scenario_input_manifest_item
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION
                research_evidence_check_scenario_input_manifest()$sql$,
        $sql$CREATE TRIGGER trg_resie_prepare_insert
            BEFORE INSERT ON research_evidence_scenario_input_evaluation
            FOR EACH ROW EXECUTE FUNCTION
                research_evidence_prepare_scenario_input_evaluation()$sql$,
        $sql$CREATE TRIGGER trg_resie_link_inputs
            AFTER INSERT ON research_evidence_scenario_input_evaluation
            FOR EACH ROW EXECUTE FUNCTION
                research_evidence_link_scenario_input_evaluation_inputs()$sql$,
        $sql$CREATE TRIGGER trg_resie_no_mutation
            BEFORE UPDATE OR DELETE
            ON research_evidence_scenario_input_evaluation
            FOR EACH ROW EXECUTE FUNCTION slicea_reject_mutation()$sql$,
        $sql$CREATE TRIGGER trg_resiei_prepare_insert
            BEFORE INSERT
            ON research_evidence_scenario_input_evaluation_input
            FOR EACH ROW EXECUTE FUNCTION
                research_evidence_prepare_scenario_input_evaluation_input()$sql$,
        $sql$CREATE TRIGGER trg_resiei_no_mutation
            BEFORE UPDATE OR DELETE
            ON research_evidence_scenario_input_evaluation_input
            FOR EACH ROW EXECUTE FUNCTION slicea_reject_mutation()$sql$,
        $sql$CREATE CONSTRAINT TRIGGER trg_resie_complete
            AFTER INSERT ON research_evidence_scenario_input_evaluation
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION
                research_evidence_check_scenario_input_evaluation()$sql$,
        $sql$CREATE CONSTRAINT TRIGGER trg_resiei_complete
            AFTER INSERT
            ON research_evidence_scenario_input_evaluation_input
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION
                research_evidence_check_scenario_input_evaluation()$sql$
    ]
    LOOP
        BEGIN
            EXECUTE v_statement;
        EXCEPTION WHEN duplicate_object THEN
            NULL;
        END;
    END LOOP;
END
$triggers$;

ALTER TABLE research_evidence_scenario_input_manifest
    ENABLE ALWAYS TRIGGER trg_resim_prepare_insert;
ALTER TABLE research_evidence_scenario_input_manifest
    ENABLE ALWAYS TRIGGER trg_resim_link_items;
ALTER TABLE research_evidence_scenario_input_manifest
    ENABLE ALWAYS TRIGGER trg_resim_no_mutation;
ALTER TABLE research_evidence_scenario_input_manifest
    ENABLE ALWAYS TRIGGER trg_resim_complete;
ALTER TABLE research_evidence_scenario_input_manifest_item
    ENABLE ALWAYS TRIGGER trg_resimi_prepare_insert;
ALTER TABLE research_evidence_scenario_input_manifest_item
    ENABLE ALWAYS TRIGGER trg_resimi_no_mutation;
ALTER TABLE research_evidence_scenario_input_manifest_item
    ENABLE ALWAYS TRIGGER trg_resimi_complete;
ALTER TABLE research_evidence_scenario_input_evaluation
    ENABLE ALWAYS TRIGGER trg_resie_prepare_insert;
ALTER TABLE research_evidence_scenario_input_evaluation
    ENABLE ALWAYS TRIGGER trg_resie_link_inputs;
ALTER TABLE research_evidence_scenario_input_evaluation
    ENABLE ALWAYS TRIGGER trg_resie_no_mutation;
ALTER TABLE research_evidence_scenario_input_evaluation
    ENABLE ALWAYS TRIGGER trg_resie_complete;
ALTER TABLE research_evidence_scenario_input_evaluation_input
    ENABLE ALWAYS TRIGGER trg_resiei_prepare_insert;
ALTER TABLE research_evidence_scenario_input_evaluation_input
    ENABLE ALWAYS TRIGGER trg_resiei_no_mutation;
ALTER TABLE research_evidence_scenario_input_evaluation_input
    ENABLE ALWAYS TRIGGER trg_resiei_complete;

COMMIT;
