-- v57 Research-Evidence Consumer-Input Binding Foundation (R1.6)
-- Additive, append-only evaluations for existing v53 candidate-fact intake
-- items.  Bindings do not execute calculations or scenarios, generate reports,
-- render citations, or authorize any downstream action.
--
-- Availability, retention provenance, lineage, review, freshness, drift, claim
-- support, and the consumer-input disposition remain separate recorded inputs.
-- No field establishes truth, approval, citation readiness, or execution rights.
--
-- Apply manually after v47-v56.  Never apply automatically at application start.

BEGIN;

-- Classify a prior v57 application before creating anything.  Complete objects
-- are accepted only when exact definitions and history invariants still match.
DO $$
DECLARE
    v_parent_tables integer;
    v_v57_tables integer;
    v_prepare_oid oid;
    v_reject_oid oid;
    v_missing text;
    v_column_count integer;
    v_function_hash text;
BEGIN
    SELECT count(*) INTO v_parent_tables
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'r'
      AND n.nspname = current_schema()
      AND c.relname = ANY (ARRAY[
          'projects', 'source_blob', 'source_snapshot',
          'candidate_fact_revision', 'evidence_retention_event',
          'approved_calculation_input',
          'research_source_metadata_revision',
          'research_fact_metadata_revision', 'research_claim_draft',
          'research_evidence_event', 'research_evidence_intake',
          'research_evidence_intake_item',
          'research_evidence_intake_item_review_decision',
          'research_evidence_intake_item_freshness_assessment',
          'research_evidence_claim_support_assessment'
      ]);
    IF v_parent_tables <> 15 THEN
        RAISE EXCEPTION 'v57 requires complete v47-v56 parent tables, found %',
            v_parent_tables USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT p.oid INTO v_reject_oid
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = current_schema()
      AND p.proname = 'slicea_reject_mutation'
      AND p.pronargs = 0
      AND p.prorettype = 'trigger'::regtype
      AND md5(regexp_replace(p.prosrc, '[[:space:]]+', '', 'g')) =
          '71da4b330a4af0bdcdd8687a6627e2af';
    IF v_reject_oid IS NULL THEN
        RAISE EXCEPTION 'v57 requires canonical append-only guard'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT count(*) INTO v_v57_tables
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'r'
      AND n.nspname = current_schema()
      AND c.relname = ANY (ARRAY[
          'research_evidence_consumer_input_binding',
          'research_evidence_consumer_input_binding_sequence_allocator'
      ]);

    SELECT p.oid INTO v_prepare_oid
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = current_schema()
      AND p.proname = 'research_evidence_prepare_binding_insert'
      AND p.pronargs = 0
      AND p.prorettype = 'trigger'::regtype;

    IF v_v57_tables = 0
       AND v_prepare_oid IS NULL
       AND NOT EXISTS (
           SELECT 1
           FROM pg_trigger t
           JOIN pg_class c ON c.oid = t.tgrelid
           JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = current_schema()
             AND t.tgname IN (
                 'trg_recib_prepare_insert', 'trg_recib_no_mutation'
             )
       )
       AND NOT EXISTS (
           SELECT 1 FROM pg_indexes
           WHERE schemaname = current_schema()
             AND indexname = 'idx_recib_scope_sequence'
       ) THEN
        RETURN;
    END IF;

    IF v_v57_tables <> 2 OR v_prepare_oid IS NULL THEN
        RAISE EXCEPTION
            'v57 contract violation: partial/divergent binding foundation'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT count(*) INTO v_column_count
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'research_evidence_consumer_input_binding';
    IF v_column_count <> 46 THEN
        RAISE EXCEPTION 'v57 contract violation: divergent binding column count'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('id'::text, 'uuid'::text, 'NO'::text),
        ('project_id', 'uuid', 'NO'),
        ('consumer_contract', 'text', 'NO'),
        ('consumer_contract_version', 'text', 'NO'),
        ('binding_set_id', 'text', 'NO'),
        ('input_key', 'text', 'NO'),
        ('request_id', 'text', 'NO'),
        ('evidence_intake_item_id', 'uuid', 'NO'),
        ('approved_calculation_input_id', 'uuid', 'YES'),
        ('calculation_kind', 'text', 'YES'),
        ('observation_identity_version', 'text', 'YES'),
        ('observation_identity_fingerprint', 'text', 'YES'),
        ('claim_intake_item_id', 'uuid', 'YES'),
        ('claim_support_assessment_id', 'uuid', 'YES'),
        ('policy_identifier', 'text', 'NO'),
        ('policy_version', 'text', 'NO'),
        ('policy_parameters_json', 'jsonb', 'NO'),
        ('policy_fingerprint', 'text', 'NO'),
        ('evaluator_version', 'text', 'NO'),
        ('freshness_as_of', 'timestamp with time zone', 'NO'),
        ('consumer_disposition', 'text', 'NO'),
        ('disposition_reasons_json', 'jsonb', 'NO'),
        ('evaluated_by', 'text', 'NO'),
        ('source_snapshot_id', 'uuid', 'NO'),
        ('source_blob_id', 'uuid', 'NO'),
        ('source_metadata_revision_id', 'uuid', 'NO'),
        ('candidate_fact_revision_id', 'uuid', 'NO'),
        ('fact_metadata_revision_id', 'uuid', 'NO'),
        ('availability_status', 'boolean', 'NO'),
        ('retention_basis_json', 'jsonb', 'NO'),
        ('lineage_is_current', 'boolean', 'NO'),
        ('lineage_basis_json', 'jsonb', 'NO'),
        ('review_decision_id', 'uuid', 'YES'),
        ('review_decision_sequence', 'integer', 'YES'),
        ('review_status', 'text', 'NO'),
        ('freshness_assessment_id', 'uuid', 'YES'),
        ('freshness_assessment_sequence', 'integer', 'YES'),
        ('fresh_through', 'timestamp with time zone', 'YES'),
        ('freshness_status', 'text', 'NO'),
        ('drift_status', 'text', 'NO'),
        ('locator_resolution', 'text', 'YES'),
        ('evidence_linkage', 'text', 'YES'),
        ('semantic_relationship', 'text', 'YES'),
        ('binding_sequence', 'integer', 'NO'),
        ('supersedes_binding_id', 'uuid', 'YES'),
        ('evaluated_at', 'timestamp with time zone', 'NO')
    ) expected(name, data_type, nullable)
    LEFT JOIN information_schema.columns column_info
      ON column_info.table_schema = current_schema()
     AND column_info.table_name =
         'research_evidence_consumer_input_binding'
     AND column_info.column_name = expected.name
    WHERE column_info.column_name IS NULL
       OR column_info.data_type <> expected.data_type
       OR column_info.is_nullable <> expected.nullable;
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v57 contract violation: divergent binding columns %',
            v_missing USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'research_evidence_consumer_input_binding'
          AND column_name IN (
              'calculation_kind',
              'source_snapshot_id', 'source_blob_id',
              'source_metadata_revision_id',
              'candidate_fact_revision_id', 'fact_metadata_revision_id',
              'availability_status', 'retention_basis_json',
              'lineage_is_current', 'lineage_basis_json',
              'review_decision_id', 'review_decision_sequence',
              'review_status', 'freshness_assessment_id',
              'freshness_assessment_sequence', 'fresh_through',
              'freshness_status', 'drift_status',
              'locator_resolution', 'evidence_linkage',
              'semantic_relationship', 'binding_sequence',
              'supersedes_binding_id', 'evaluated_at'
          )
          AND column_default IS NOT NULL
    ) THEN
        RAISE EXCEPTION
            'v57 contract violation: server-owned fields have defaults'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_attribute attribute
        JOIN pg_attrdef default_info
          ON default_info.adrelid = attribute.attrelid
         AND default_info.adnum = attribute.attnum
        WHERE attribute.attrelid =
              'research_evidence_consumer_input_binding'::regclass
          AND attribute.attname = 'id'
          AND lower(regexp_replace(
              pg_get_expr(
                  default_info.adbin, default_info.adrelid, true
              ),
              '[[:space:]]+', '', 'g'
          )) = 'gen_random_uuid()'
    ) THEN
        RAISE EXCEPTION 'v57 contract violation: divergent binding id default'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT count(*) INTO v_column_count
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name =
          'research_evidence_consumer_input_binding_sequence_allocator';
    IF v_column_count <> 6 THEN
        RAISE EXCEPTION 'v57 contract violation: divergent allocator columns'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('project_id'::text, 'uuid'::text, 'NO'::text),
        ('consumer_contract', 'text', 'NO'),
        ('binding_set_id', 'text', 'NO'),
        ('input_key', 'text', 'NO'),
        ('evidence_intake_item_id', 'uuid', 'NO'),
        ('last_sequence', 'integer', 'NO')
    ) expected(name, data_type, nullable)
    LEFT JOIN information_schema.columns column_info
      ON column_info.table_schema = current_schema()
     AND column_info.table_name =
         'research_evidence_consumer_input_binding_sequence_allocator'
     AND column_info.column_name = expected.name
    WHERE column_info.column_name IS NULL
       OR column_info.data_type <> expected.data_type
       OR column_info.is_nullable <> expected.nullable;
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v57 contract violation: divergent allocator columns %',
            v_missing USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name =
              'research_evidence_consumer_input_binding_sequence_allocator'
          AND column_default IS NOT NULL
    ) THEN
        RAISE EXCEPTION
            'v57 contract violation: allocator fields have defaults'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('research_evidence_consumer_input_binding_pkey'::text, 'p'::"char"),
        ('uq_recib_id_project_scope', 'u'),
        ('uq_recib_scope_sequence', 'u'),
        ('uq_recib_scope_request', 'u'),
        ('uq_recib_supersedes_once', 'u'),
        ('fk_recib_project', 'f'),
        ('fk_recib_evidence_item_project', 'f'),
        ('fk_recib_calculation_input_role', 'f'),
        ('fk_recib_claim_item_project', 'f'),
        ('fk_recib_claim_support_pair', 'f'),
        ('fk_recib_snapshot_project', 'f'),
        ('fk_recib_blob_project', 'f'),
        ('fk_recib_source_metadata_snapshot', 'f'),
        ('fk_recib_fact_project', 'f'),
        ('fk_recib_fact_metadata_fact', 'f'),
        ('fk_recib_review_decision_item', 'f'),
        ('fk_recib_freshness_assessment_item', 'f'),
        ('fk_recib_supersedes_same_scope', 'f'),
        ('ck_recib_consumer_contract', 'c'),
        ('ck_recib_consumer_shape', 'c'),
        ('ck_recib_claim_pair_shape', 'c'),
        ('ck_recib_review_shape', 'c'),
        ('ck_recib_freshness_shape', 'c'),
        ('ck_recib_consumer_disposition', 'c'),
        ('ck_recib_json_shapes', 'c'),
        ('ck_recib_policy_provenance', 'c'),
        ('ck_recib_nonblank', 'c'),
        ('ck_recib_observation_fingerprint', 'c'),
        ('ck_recib_sequence_positive', 'c'),
        ('pk_recib_sequence_allocator',
         'p'),
        ('fk_recib_allocator_project', 'f'),
        ('fk_recib_allocator_evidence_item', 'f'),
        ('ck_recib_allocator_last_sequence', 'c')
    ) expected(name, kind)
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        WHERE con.connamespace = current_schema()::regnamespace
          AND con.conname = expected.name
          AND con.contype = expected.kind
          AND con.convalidated
    );
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v57 contract violation: missing constraints %',
            v_missing USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('research_evidence_consumer_input_binding_pkey'::text,
         'research_evidence_consumer_input_binding'::text,
         'p'::"char", ARRAY['id']::text[]),
        ('uq_recib_id_project_scope',
         'research_evidence_consumer_input_binding', 'u',
         ARRAY['id', 'project_id', 'consumer_contract',
               'binding_set_id', 'input_key']),
        ('uq_recib_scope_sequence',
         'research_evidence_consumer_input_binding', 'u',
         ARRAY['project_id', 'consumer_contract', 'binding_set_id',
               'input_key', 'binding_sequence']),
        ('uq_recib_scope_request',
         'research_evidence_consumer_input_binding', 'u',
         ARRAY['project_id', 'consumer_contract', 'binding_set_id',
               'input_key', 'request_id']),
        ('uq_recib_supersedes_once',
         'research_evidence_consumer_input_binding', 'u',
         ARRAY['supersedes_binding_id']),
        ('pk_recib_sequence_allocator',
         'research_evidence_consumer_input_binding_sequence_allocator', 'p',
         ARRAY['project_id', 'consumer_contract', 'binding_set_id',
               'input_key'])
    ) expected(name, table_name, kind, columns)
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        WHERE con.connamespace = current_schema()::regnamespace
          AND con.conname = expected.name
          AND con.conrelid = expected.table_name::regclass
          AND con.contype = expected.kind
          AND con.convalidated
          AND NOT con.condeferrable
          AND NOT con.condeferred
          AND (
              SELECT array_agg(a.attname::text ORDER BY u.ordinality)
              FROM unnest(con.conkey) WITH ORDINALITY u(attnum, ordinality)
              JOIN pg_attribute a
                ON a.attrelid = con.conrelid AND a.attnum = u.attnum
          ) = expected.columns
          AND EXISTS (
              SELECT 1
              FROM pg_index index_info
              WHERE index_info.indexrelid = con.conindid
                AND index_info.indrelid = con.conrelid
                AND index_info.indisunique
                AND index_info.indisvalid
                AND index_info.indisready
                AND index_info.indimmediate
                AND index_info.indisprimary = (expected.kind = 'p')
                AND NOT index_info.indnullsnotdistinct
                AND index_info.indpred IS NULL
                AND index_info.indexprs IS NULL
                AND index_info.indnkeyatts =
                    cardinality(expected.columns)
                AND index_info.indnatts = cardinality(expected.columns)
          )
    );
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v57 contract violation: divergent binding keys %',
            v_missing USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('fk_recib_project'::text,
         'research_evidence_consumer_input_binding'::text, 'projects'::text,
         ARRAY['project_id']::text[], ARRAY['id']::text[]),
        ('fk_recib_evidence_item_project',
         'research_evidence_consumer_input_binding',
         'research_evidence_intake_item',
         ARRAY['evidence_intake_item_id', 'project_id'],
         ARRAY['id', 'project_id']),
        ('fk_recib_calculation_input_role',
         'research_evidence_consumer_input_binding',
         'approved_calculation_input',
         ARRAY['approved_calculation_input_id', 'input_key', 'project_id'],
         ARRAY['id', 'input_role', 'project_id']),
        ('fk_recib_claim_item_project',
         'research_evidence_consumer_input_binding',
         'research_evidence_intake_item',
         ARRAY['claim_intake_item_id', 'project_id'],
         ARRAY['id', 'project_id']),
        ('fk_recib_claim_support_pair',
         'research_evidence_consumer_input_binding',
         'research_evidence_claim_support_assessment',
         ARRAY['claim_support_assessment_id', 'project_id',
               'claim_intake_item_id', 'evidence_intake_item_id'],
         ARRAY['id', 'project_id', 'claim_intake_item_id',
               'evidence_intake_item_id']),
        ('fk_recib_snapshot_project',
         'research_evidence_consumer_input_binding', 'source_snapshot',
         ARRAY['source_snapshot_id', 'project_id'],
         ARRAY['id', 'project_id']),
        ('fk_recib_blob_project',
         'research_evidence_consumer_input_binding', 'source_blob',
         ARRAY['source_blob_id', 'project_id'], ARRAY['id', 'project_id']),
        ('fk_recib_source_metadata_snapshot',
         'research_evidence_consumer_input_binding',
         'research_source_metadata_revision',
         ARRAY['source_metadata_revision_id', 'project_id',
               'source_snapshot_id'],
         ARRAY['id', 'project_id', 'source_snapshot_id']),
        ('fk_recib_fact_project',
         'research_evidence_consumer_input_binding',
         'candidate_fact_revision',
         ARRAY['candidate_fact_revision_id', 'project_id'],
         ARRAY['id', 'project_id']),
        ('fk_recib_fact_metadata_fact',
         'research_evidence_consumer_input_binding',
         'research_fact_metadata_revision',
         ARRAY['fact_metadata_revision_id', 'project_id',
               'candidate_fact_revision_id'],
         ARRAY['id', 'project_id', 'candidate_fact_revision_id']),
        ('fk_recib_review_decision_item',
         'research_evidence_consumer_input_binding',
         'research_evidence_intake_item_review_decision',
         ARRAY['review_decision_id', 'project_id',
               'evidence_intake_item_id'],
         ARRAY['id', 'project_id',
               'research_evidence_intake_item_id']),
        ('fk_recib_freshness_assessment_item',
         'research_evidence_consumer_input_binding',
         'research_evidence_intake_item_freshness_assessment',
         ARRAY['freshness_assessment_id', 'project_id',
               'evidence_intake_item_id'],
         ARRAY['id', 'project_id',
               'research_evidence_intake_item_id']),
        ('fk_recib_supersedes_same_scope',
         'research_evidence_consumer_input_binding',
         'research_evidence_consumer_input_binding',
         ARRAY['supersedes_binding_id', 'project_id', 'consumer_contract',
               'binding_set_id', 'input_key'],
         ARRAY['id', 'project_id', 'consumer_contract',
               'binding_set_id', 'input_key']),
        ('fk_recib_allocator_project',
         'research_evidence_consumer_input_binding_sequence_allocator',
         'projects', ARRAY['project_id'], ARRAY['id']),
        ('fk_recib_allocator_evidence_item',
         'research_evidence_consumer_input_binding_sequence_allocator',
         'research_evidence_intake_item',
         ARRAY['evidence_intake_item_id', 'project_id'],
         ARRAY['id', 'project_id'])
    ) expected(name, local_table, parent_table, local_columns, parent_columns)
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        WHERE con.connamespace = current_schema()::regnamespace
          AND con.conname = expected.name
          AND con.contype = 'f'
          AND con.conrelid = expected.local_table::regclass
          AND con.confrelid = expected.parent_table::regclass
          AND con.confupdtype = 'a'
          AND con.confdeltype = 'r'
          AND con.confmatchtype = 's'
          AND con.convalidated
          AND NOT con.condeferrable
          AND NOT con.condeferred
          AND (
              SELECT array_agg(a.attname::text ORDER BY u.ordinality)
              FROM unnest(con.conkey) WITH ORDINALITY u(attnum, ordinality)
              JOIN pg_attribute a
                ON a.attrelid = con.conrelid AND a.attnum = u.attnum
          ) = expected.local_columns
          AND (
              SELECT array_agg(a.attname::text ORDER BY u.ordinality)
              FROM unnest(con.confkey) WITH ORDINALITY u(attnum, ordinality)
              JOIN pg_attribute a
                ON a.attrelid = con.confrelid AND a.attnum = u.attnum
          ) = expected.parent_columns
    );
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION
            'v57 contract violation: divergent binding foreign keys %',
            v_missing USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('ck_recib_consumer_contract'::text,
         'research_evidence_consumer_input_binding'::text,
         'consumer_contract=anyarray['
         || '''deterministic_calculation'',''scenario_input'','
         || '''report_evidence_register'']'),
        ('ck_recib_consumer_shape',
         'research_evidence_consumer_input_binding',
         'consumer_contract=''deterministic_calculation'''
         || 'andapproved_calculation_input_idisnotnull'
         || 'andcalculation_kindisnotnull'
         || 'andobservation_identity_versionisnull'
         || 'andobservation_identity_fingerprintisnull'
         || 'andclaim_intake_item_idisnull'
         || 'andclaim_support_assessment_idisnull'
         || 'orconsumer_contract=''scenario_input'''
         || 'andapproved_calculation_input_idisnull'
         || 'andcalculation_kindisnull'
         || 'andobservation_identity_versionisnotnull'
         || 'andobservation_identity_fingerprintisnotnull'
         || 'orconsumer_contract=''report_evidence_register'''
         || 'andapproved_calculation_input_idisnull'
         || 'andcalculation_kindisnull'
         || 'andobservation_identity_versionisnull'
         || 'andobservation_identity_fingerprintisnull'),
        ('ck_recib_claim_pair_shape',
         'research_evidence_consumer_input_binding',
         'claim_intake_item_idisnull'
         || 'andclaim_support_assessment_idisnull'
         || 'andlocator_resolutionisnull'
         || 'andevidence_linkageisnull'
         || 'andsemantic_relationshipisnull'
         || 'orclaim_intake_item_idisnotnull'
         || 'andclaim_support_assessment_idisnotnull'
         || 'andconsumer_contract=anyarray['
         || '''scenario_input'',''report_evidence_register'']'
         || 'andlocator_resolutionisnotnull'
         || 'andevidence_linkageisnotnull'
         || 'andsemantic_relationshipisnotnull'),
        ('ck_recib_review_shape',
         'research_evidence_consumer_input_binding',
         'review_decision_idisnull'
         || 'andreview_decision_sequenceisnull'
         || 'andreview_status=''not_assessed'''
         || 'orreview_decision_idisnotnull'
         || 'andreview_decision_sequenceisnotnull'
         || 'andreview_decision_sequence>=1'
         || 'andreview_status=anyarray['
         || '''approved'',''rejected'',''needs_revision'',''withdrawn'']'),
        ('ck_recib_freshness_shape',
         'research_evidence_consumer_input_binding',
         'freshness_assessment_idisnull'
         || 'andfreshness_assessment_sequenceisnull'
         || 'andfresh_throughisnull'
         || 'andfreshness_status=''unknown'''
         || 'anddrift_status=''not_assessed'''
         || 'orfreshness_assessment_idisnotnull'
         || 'andfreshness_assessment_sequenceisnotnull'
         || 'andfreshness_assessment_sequence>=1'
         || 'andfresh_throughisnotnull'
         || 'andfreshness_status=anyarray[''fresh'',''stale'']'
         || 'anddrift_status=anyarray[''not_assessed'','
         || '''no_material_drift'',''material_drift'',''indeterminate'']'),
        ('ck_recib_consumer_disposition',
         'research_evidence_consumer_input_binding',
         'consumer_disposition=anyarray['
         || '''meets_contract'',''qualified'',''does_not_meet_contract'','
         || '''indeterminate'']'),
        ('ck_recib_json_shapes',
         'research_evidence_consumer_input_binding',
         'jsonb_typeofpolicy_parameters_json=''object'''
         || 'andjsonb_typeofdisposition_reasons_json=''array'''
         || 'andjsonb_array_lengthdisposition_reasons_json>=1'
         || 'andjsonb_typeofretention_basis_json=''array'''
         || 'andjsonb_typeoflineage_basis_json=''array'''),
        ('ck_recib_policy_provenance',
         'research_evidence_consumer_input_binding',
         'policy_parameters_json<>''{}'''
         || 'orpolicy_fingerprint!~''^[[:space:]]*$'''),
        ('ck_recib_nonblank',
         'research_evidence_consumer_input_binding',
         'consumer_contract_version!~''^[[:space:]]*$'''
         || 'andbinding_set_id!~''^[[:space:]]*$'''
         || 'andinput_key!~''^[[:space:]]*$'''
         || 'andrequest_id!~''^[[:space:]]*$'''
         || 'andpolicy_identifier!~''^[[:space:]]*$'''
         || 'andpolicy_version!~''^[[:space:]]*$'''
         || 'andevaluator_version!~''^[[:space:]]*$'''
         || 'andevaluated_by!~''^[[:space:]]*$'''
         || 'andobservation_identity_versionisnull'
         || 'orobservation_identity_version!~''^[[:space:]]*$'''),
        ('ck_recib_observation_fingerprint',
         'research_evidence_consumer_input_binding',
         'observation_identity_fingerprintisnull'
         || 'orobservation_identity_fingerprint~''^[0-9a-f]{64}$'''),
        ('ck_recib_sequence_positive',
         'research_evidence_consumer_input_binding',
         'binding_sequence>=1'),
        ('ck_recib_allocator_last_sequence',
         'research_evidence_consumer_input_binding_sequence_allocator',
         'last_sequence>=0')
    ) expected(name, table_name, normalized_expression)
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        WHERE con.connamespace = current_schema()::regnamespace
          AND con.conname = expected.name
          AND con.conrelid = expected.table_name::regclass
          AND con.contype = 'c'
          AND replace(
              replace(
                  translate(
                      regexp_replace(
                          lower(pg_get_expr(con.conbin, con.conrelid, true)),
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
        RAISE EXCEPTION
            'v57 contract violation: divergent binding check constraints %',
            v_missing USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_class index_class
        JOIN pg_namespace n ON n.oid = index_class.relnamespace
        JOIN pg_index index_info ON index_info.indexrelid = index_class.oid
        JOIN pg_am access_method ON access_method.oid = index_class.relam
        WHERE n.nspname = current_schema()
          AND index_class.relname = 'idx_recib_scope_sequence'
          AND index_info.indrelid =
              'research_evidence_consumer_input_binding'::regclass
          AND access_method.amname = 'btree'
          AND index_info.indisvalid
          AND index_info.indisready
          AND index_info.indislive
          AND NOT index_info.indisunique
          AND NOT index_info.indisprimary
          AND NOT index_info.indisexclusion
          AND NOT index_info.indnullsnotdistinct
          AND index_info.indpred IS NULL
          AND index_info.indexprs IS NULL
          AND index_info.indnkeyatts = 5
          AND index_info.indnatts = 5
          AND (
              SELECT array_agg(
                  pg_get_indexdef(
                      index_info.indexrelid, key_position, true
                  )
                  ORDER BY key_position
              )
              FROM generate_series(
                  1, index_info.indnkeyatts
              ) key_position
          ) = ARRAY[
              'project_id', 'consumer_contract', 'binding_set_id',
              'input_key', 'binding_sequence'
          ]::text[]
    ) THEN
        RAISE EXCEPTION 'v57 contract violation: divergent binding index'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT md5(regexp_replace(p.prosrc, '[[:space:]]+', '', 'g'))
      INTO v_function_hash
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = current_schema()
      AND p.proname = 'research_evidence_prepare_binding_insert'
      AND p.pronargs = 0
      AND p.prorettype = 'trigger'::regtype
      AND p.prosecdef
      AND p.proconfig = ARRAY['search_path=pg_catalog']
      AND NOT EXISTS (
          SELECT 1
          FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) acl
          WHERE acl.grantee = 0 AND acl.privilege_type = 'EXECUTE'
      );
    IF v_function_hash IS DISTINCT FROM '141c51b3229468a325a10d1319c081a9' THEN
        RAISE EXCEPTION
            'v57 contract violation: divergent binding prepare function'
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
          AND c.relname =
              'research_evidence_consumer_input_binding_sequence_allocator'
          AND acl.grantee = 0
    ) THEN
        RAISE EXCEPTION 'v57 contract violation: allocator has PUBLIC privileges'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        WHERE t.tgname = 'trg_recib_prepare_insert'
          AND t.tgrelid =
              'research_evidence_consumer_input_binding'::regclass
          AND t.tgfoid = v_prepare_oid
          AND t.tgenabled = 'A'
          AND NOT t.tgisinternal
          AND t.tgtype = 7
          AND t.tgnargs = 0
          AND t.tgattr = ''::int2vector
          AND t.tgqual IS NULL
          AND t.tgoldtable IS NULL
          AND t.tgnewtable IS NULL
          AND t.tgconstraint = 0
          AND NOT t.tgdeferrable
          AND NOT t.tginitdeferred
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        WHERE t.tgname = 'trg_recib_no_mutation'
          AND t.tgrelid =
              'research_evidence_consumer_input_binding'::regclass
          AND t.tgfoid = v_reject_oid
          AND t.tgenabled = 'O'
          AND NOT t.tgisinternal
          AND t.tgtype = 27
          AND t.tgnargs = 0
          AND t.tgattr = ''::int2vector
          AND t.tgqual IS NULL
          AND t.tgoldtable IS NULL
          AND t.tgnewtable IS NULL
          AND t.tgconstraint = 0
          AND NOT t.tgdeferrable
          AND NOT t.tginitdeferred
    ) THEN
        RAISE EXCEPTION 'v57 contract violation: divergent binding triggers'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM (
            SELECT project_id, consumer_contract, binding_set_id, input_key,
                   count(*)::integer AS row_count,
                   min(binding_sequence) AS min_sequence,
                   max(binding_sequence) AS max_sequence
            FROM research_evidence_consumer_input_binding
            GROUP BY project_id, consumer_contract, binding_set_id, input_key
        ) history
        FULL JOIN
            research_evidence_consumer_input_binding_sequence_allocator allocator
          USING (project_id, consumer_contract, binding_set_id, input_key)
        WHERE history.project_id IS NULL
           OR allocator.project_id IS NULL
           OR history.row_count <> allocator.last_sequence
           OR history.min_sequence <> 1
           OR history.max_sequence <> allocator.last_sequence
    ) THEN
        RAISE EXCEPTION
            'v57 contract violation: allocator diverges from binding history'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM research_evidence_consumer_input_binding binding
        LEFT JOIN research_evidence_consumer_input_binding predecessor
          ON predecessor.id = binding.supersedes_binding_id
         AND predecessor.project_id = binding.project_id
         AND predecessor.consumer_contract = binding.consumer_contract
         AND predecessor.binding_set_id = binding.binding_set_id
         AND predecessor.input_key = binding.input_key
         AND predecessor.binding_sequence = binding.binding_sequence - 1
        WHERE (binding.binding_sequence = 1
               AND binding.supersedes_binding_id IS NOT NULL)
           OR (binding.binding_sequence > 1 AND predecessor.id IS NULL)
    ) THEN
        RAISE EXCEPTION 'v57 contract violation: malformed binding chain'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM research_evidence_consumer_input_binding binding
        LEFT JOIN research_evidence_intake_item item
          ON item.id = binding.evidence_intake_item_id
         AND item.project_id = binding.project_id
        LEFT JOIN research_evidence_intake intake
          ON intake.id = item.research_evidence_intake_id
         AND intake.project_id = item.project_id
         AND intake.source_snapshot_id = item.source_snapshot_id
        LEFT JOIN source_snapshot snapshot
          ON snapshot.id = item.source_snapshot_id
         AND snapshot.project_id = item.project_id
        LEFT JOIN source_blob blob
          ON blob.id = snapshot.source_blob_id
         AND blob.project_id = snapshot.project_id
        LEFT JOIN research_source_metadata_revision source_metadata
          ON source_metadata.id = binding.source_metadata_revision_id
         AND source_metadata.project_id = binding.project_id
         AND source_metadata.source_snapshot_id =
             binding.source_snapshot_id
        LEFT JOIN candidate_fact_revision fact
          ON fact.id = binding.candidate_fact_revision_id
         AND fact.project_id = binding.project_id
         AND fact.source_snapshot_id = binding.source_snapshot_id
        LEFT JOIN research_fact_metadata_revision fact_metadata
          ON fact_metadata.id = binding.fact_metadata_revision_id
         AND fact_metadata.project_id = binding.project_id
         AND fact_metadata.candidate_fact_revision_id =
             binding.candidate_fact_revision_id
        LEFT JOIN research_evidence_intake_item claim_item
          ON claim_item.id = binding.claim_intake_item_id
         AND claim_item.project_id = binding.project_id
        LEFT JOIN approved_calculation_input calculation_input
          ON calculation_input.id = binding.approved_calculation_input_id
         AND calculation_input.project_id = binding.project_id
        LEFT JOIN research_evidence_intake_item_review_decision review
          ON review.id = binding.review_decision_id
         AND review.project_id = binding.project_id
         AND review.research_evidence_intake_item_id =
             binding.evidence_intake_item_id
        LEFT JOIN research_evidence_intake_item_freshness_assessment freshness
          ON freshness.id = binding.freshness_assessment_id
         AND freshness.project_id = binding.project_id
         AND freshness.research_evidence_intake_item_id =
             binding.evidence_intake_item_id
        LEFT JOIN research_evidence_claim_support_assessment support
          ON support.id = binding.claim_support_assessment_id
         AND support.project_id = binding.project_id
         AND support.claim_intake_item_id = binding.claim_intake_item_id
         AND support.evidence_intake_item_id =
             binding.evidence_intake_item_id
        WHERE item.id IS NULL
           OR item.item_kind <> 'candidate_fact'
           OR intake.id IS NULL
           OR snapshot.id IS NULL
           OR blob.id IS NULL
           OR source_metadata.id IS NULL
           OR fact.id IS NULL
           OR fact_metadata.id IS NULL
           OR binding.source_snapshot_id IS DISTINCT FROM item.source_snapshot_id
           OR binding.source_blob_id IS DISTINCT FROM snapshot.source_blob_id
           OR binding.source_metadata_revision_id IS DISTINCT FROM
              intake.source_metadata_revision_id
           OR binding.candidate_fact_revision_id IS DISTINCT FROM
              item.candidate_fact_revision_id
           OR binding.fact_metadata_revision_id IS DISTINCT FROM
              item.fact_metadata_revision_id
           OR (
               binding.claim_intake_item_id IS NOT NULL
               AND (
                   claim_item.id IS NULL
                   OR claim_item.item_kind <> 'claim_draft'
               )
           )
           OR (
               binding.consumer_contract = 'deterministic_calculation'
               AND (
                   calculation_input.id IS NULL
                   OR calculation_input.input_role IS DISTINCT FROM
                      binding.input_key
                   OR calculation_input.candidate_fact_revision_id IS DISTINCT
                      FROM binding.candidate_fact_revision_id
                   OR calculation_input.calculation_kind IS DISTINCT FROM
                      binding.calculation_kind
               )
           )
           OR (
               binding.review_decision_id IS NOT NULL
               AND (
                   review.id IS NULL
                   OR review.decision_sequence IS DISTINCT FROM
                      binding.review_decision_sequence
                   OR review.decision_type IS DISTINCT FROM
                      binding.review_status
               )
           )
           OR (
               binding.freshness_assessment_id IS NOT NULL
               AND (
                   freshness.id IS NULL
                   OR freshness.assessment_sequence IS DISTINCT FROM
                      binding.freshness_assessment_sequence
                   OR freshness.fresh_through IS DISTINCT FROM
                      binding.fresh_through
                   OR freshness.drift_status IS DISTINCT FROM
                      binding.drift_status
               )
           )
           OR (
               binding.claim_support_assessment_id IS NOT NULL
               AND (
                   support.id IS NULL
                   OR support.locator_resolution IS DISTINCT FROM
                      binding.locator_resolution
                   OR support.evidence_linkage IS DISTINCT FROM
                      binding.evidence_linkage
                   OR support.semantic_relationship IS DISTINCT FROM
                      binding.semantic_relationship
               )
           )
    ) THEN
        RAISE EXCEPTION
            'v57 contract violation: evaluated identities diverge from source records'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM
            research_evidence_consumer_input_binding_sequence_allocator allocator
        JOIN research_evidence_consumer_input_binding binding
          ON binding.project_id = allocator.project_id
         AND binding.consumer_contract = allocator.consumer_contract
         AND binding.binding_set_id = allocator.binding_set_id
         AND binding.input_key = allocator.input_key
        WHERE binding.evidence_intake_item_id IS DISTINCT FROM
              allocator.evidence_intake_item_id
    ) THEN
        RAISE EXCEPTION
            'v57 contract violation: allocator evidence identity diverges'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS research_evidence_consumer_input_binding (
    id                               UUID NOT NULL DEFAULT gen_random_uuid(),
    project_id                       UUID NOT NULL,
    consumer_contract                TEXT NOT NULL,
    consumer_contract_version        TEXT NOT NULL,
    binding_set_id                   TEXT NOT NULL,
    input_key                        TEXT NOT NULL,
    request_id                       TEXT NOT NULL,
    evidence_intake_item_id          UUID NOT NULL,
    approved_calculation_input_id    UUID,
    calculation_kind                 TEXT,
    observation_identity_version     TEXT,
    observation_identity_fingerprint TEXT,
    claim_intake_item_id             UUID,
    claim_support_assessment_id      UUID,
    policy_identifier                TEXT NOT NULL,
    policy_version                   TEXT NOT NULL,
    policy_parameters_json           JSONB NOT NULL,
    policy_fingerprint               TEXT NOT NULL DEFAULT '',
    evaluator_version                TEXT NOT NULL,
    freshness_as_of                  TIMESTAMPTZ NOT NULL,
    consumer_disposition             TEXT NOT NULL,
    disposition_reasons_json         JSONB NOT NULL,
    evaluated_by                     TEXT NOT NULL,
    source_snapshot_id               UUID NOT NULL,
    source_blob_id                   UUID NOT NULL,
    source_metadata_revision_id      UUID NOT NULL,
    candidate_fact_revision_id       UUID NOT NULL,
    fact_metadata_revision_id        UUID NOT NULL,
    availability_status              BOOLEAN NOT NULL,
    retention_basis_json             JSONB NOT NULL,
    lineage_is_current               BOOLEAN NOT NULL,
    lineage_basis_json               JSONB NOT NULL,
    review_decision_id               UUID,
    review_decision_sequence         INTEGER,
    review_status                    TEXT NOT NULL,
    freshness_assessment_id          UUID,
    freshness_assessment_sequence    INTEGER,
    fresh_through                    TIMESTAMPTZ,
    freshness_status                 TEXT NOT NULL,
    drift_status                     TEXT NOT NULL,
    locator_resolution               TEXT,
    evidence_linkage                 TEXT,
    semantic_relationship            TEXT,
    binding_sequence                 INTEGER NOT NULL,
    supersedes_binding_id            UUID,
    evaluated_at                     TIMESTAMPTZ NOT NULL,
    CONSTRAINT research_evidence_consumer_input_binding_pkey
        PRIMARY KEY (id),
    CONSTRAINT uq_recib_id_project_scope
        UNIQUE (
            id, project_id, consumer_contract, binding_set_id, input_key
        ),
    CONSTRAINT uq_recib_scope_sequence
        UNIQUE (
            project_id, consumer_contract, binding_set_id, input_key,
            binding_sequence
        ),
    CONSTRAINT uq_recib_scope_request
        UNIQUE (
            project_id, consumer_contract, binding_set_id, input_key, request_id
        ),
    CONSTRAINT uq_recib_supersedes_once UNIQUE (supersedes_binding_id),
    CONSTRAINT fk_recib_project
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
    CONSTRAINT fk_recib_evidence_item_project
        FOREIGN KEY (evidence_intake_item_id, project_id)
        REFERENCES research_evidence_intake_item(id, project_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_recib_calculation_input_role
        FOREIGN KEY (approved_calculation_input_id, input_key, project_id)
        REFERENCES approved_calculation_input(id, input_role, project_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_recib_claim_item_project
        FOREIGN KEY (claim_intake_item_id, project_id)
        REFERENCES research_evidence_intake_item(id, project_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_recib_claim_support_pair
        FOREIGN KEY (
            claim_support_assessment_id, project_id,
            claim_intake_item_id, evidence_intake_item_id
        )
        REFERENCES research_evidence_claim_support_assessment(
            id, project_id, claim_intake_item_id, evidence_intake_item_id
        ) ON DELETE RESTRICT,
    CONSTRAINT fk_recib_snapshot_project
        FOREIGN KEY (source_snapshot_id, project_id)
        REFERENCES source_snapshot(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT fk_recib_blob_project
        FOREIGN KEY (source_blob_id, project_id)
        REFERENCES source_blob(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT fk_recib_source_metadata_snapshot
        FOREIGN KEY (
            source_metadata_revision_id, project_id, source_snapshot_id
        )
        REFERENCES research_source_metadata_revision(
            id, project_id, source_snapshot_id
        ) ON DELETE RESTRICT,
    CONSTRAINT fk_recib_fact_project
        FOREIGN KEY (candidate_fact_revision_id, project_id)
        REFERENCES candidate_fact_revision(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT fk_recib_fact_metadata_fact
        FOREIGN KEY (
            fact_metadata_revision_id, project_id, candidate_fact_revision_id
        )
        REFERENCES research_fact_metadata_revision(
            id, project_id, candidate_fact_revision_id
        ) ON DELETE RESTRICT,
    CONSTRAINT fk_recib_review_decision_item
        FOREIGN KEY (
            review_decision_id, project_id, evidence_intake_item_id
        )
        REFERENCES research_evidence_intake_item_review_decision(
            id, project_id, research_evidence_intake_item_id
        ) ON DELETE RESTRICT,
    CONSTRAINT fk_recib_freshness_assessment_item
        FOREIGN KEY (
            freshness_assessment_id, project_id, evidence_intake_item_id
        )
        REFERENCES research_evidence_intake_item_freshness_assessment(
            id, project_id, research_evidence_intake_item_id
        ) ON DELETE RESTRICT,
    CONSTRAINT fk_recib_supersedes_same_scope
        FOREIGN KEY (
            supersedes_binding_id, project_id, consumer_contract,
            binding_set_id, input_key
        )
        REFERENCES research_evidence_consumer_input_binding(
            id, project_id, consumer_contract, binding_set_id, input_key
        ) ON DELETE RESTRICT,
    CONSTRAINT ck_recib_consumer_contract CHECK (
        consumer_contract IN (
            'deterministic_calculation',
            'scenario_input',
            'report_evidence_register'
        )
    ),
    CONSTRAINT ck_recib_consumer_shape CHECK (
        (
            consumer_contract = 'deterministic_calculation'
            AND approved_calculation_input_id IS NOT NULL
            AND calculation_kind IS NOT NULL
            AND observation_identity_version IS NULL
            AND observation_identity_fingerprint IS NULL
            AND claim_intake_item_id IS NULL
            AND claim_support_assessment_id IS NULL
        )
        OR
        (
            consumer_contract = 'scenario_input'
            AND approved_calculation_input_id IS NULL
            AND calculation_kind IS NULL
            AND observation_identity_version IS NOT NULL
            AND observation_identity_fingerprint IS NOT NULL
        )
        OR
        (
            consumer_contract = 'report_evidence_register'
            AND approved_calculation_input_id IS NULL
            AND calculation_kind IS NULL
            AND observation_identity_version IS NULL
            AND observation_identity_fingerprint IS NULL
        )
    ),
    CONSTRAINT ck_recib_claim_pair_shape CHECK (
        (
            claim_intake_item_id IS NULL
            AND claim_support_assessment_id IS NULL
            AND locator_resolution IS NULL
            AND evidence_linkage IS NULL
            AND semantic_relationship IS NULL
        )
        OR
        (
            claim_intake_item_id IS NOT NULL
            AND claim_support_assessment_id IS NOT NULL
            AND consumer_contract IN (
                'scenario_input', 'report_evidence_register'
            )
            AND locator_resolution IS NOT NULL
            AND evidence_linkage IS NOT NULL
            AND semantic_relationship IS NOT NULL
        )
    ),
    CONSTRAINT ck_recib_review_shape CHECK (
        (
            review_decision_id IS NULL
            AND review_decision_sequence IS NULL
            AND review_status = 'not_assessed'
        )
        OR
        (
            review_decision_id IS NOT NULL
            AND review_decision_sequence IS NOT NULL
            AND review_decision_sequence >= 1
            AND review_status IN (
                'approved', 'rejected', 'needs_revision', 'withdrawn'
            )
        )
    ),
    CONSTRAINT ck_recib_freshness_shape CHECK (
        (
            freshness_assessment_id IS NULL
            AND freshness_assessment_sequence IS NULL
            AND fresh_through IS NULL
            AND freshness_status = 'unknown'
            AND drift_status = 'not_assessed'
        )
        OR
        (
            freshness_assessment_id IS NOT NULL
            AND freshness_assessment_sequence IS NOT NULL
            AND freshness_assessment_sequence >= 1
            AND fresh_through IS NOT NULL
            AND freshness_status IN ('fresh', 'stale')
            AND drift_status IN (
                'not_assessed', 'no_material_drift',
                'material_drift', 'indeterminate'
            )
        )
    ),
    CONSTRAINT ck_recib_consumer_disposition CHECK (
        consumer_disposition IN (
            'meets_contract', 'qualified',
            'does_not_meet_contract', 'indeterminate'
        )
    ),
    CONSTRAINT ck_recib_json_shapes CHECK (
        jsonb_typeof(policy_parameters_json) = 'object'
        AND jsonb_typeof(disposition_reasons_json) = 'array'
        AND jsonb_array_length(disposition_reasons_json) >= 1
        AND jsonb_typeof(retention_basis_json) = 'array'
        AND jsonb_typeof(lineage_basis_json) = 'array'
    ),
    CONSTRAINT ck_recib_policy_provenance CHECK (
        policy_parameters_json <> '{}'::jsonb
        OR policy_fingerprint !~ '^[[:space:]]*$'
    ),
    CONSTRAINT ck_recib_nonblank CHECK (
        consumer_contract_version !~ '^[[:space:]]*$'
        AND binding_set_id !~ '^[[:space:]]*$'
        AND input_key !~ '^[[:space:]]*$'
        AND request_id !~ '^[[:space:]]*$'
        AND policy_identifier !~ '^[[:space:]]*$'
        AND policy_version !~ '^[[:space:]]*$'
        AND evaluator_version !~ '^[[:space:]]*$'
        AND evaluated_by !~ '^[[:space:]]*$'
        AND (
            observation_identity_version IS NULL
            OR observation_identity_version !~ '^[[:space:]]*$'
        )
    ),
    CONSTRAINT ck_recib_observation_fingerprint CHECK (
        observation_identity_fingerprint IS NULL
        OR observation_identity_fingerprint ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_recib_sequence_positive CHECK (binding_sequence >= 1)
);

CREATE TABLE IF NOT EXISTS
research_evidence_consumer_input_binding_sequence_allocator (
    project_id        UUID NOT NULL,
    consumer_contract TEXT NOT NULL,
    binding_set_id    TEXT NOT NULL,
    input_key         TEXT NOT NULL,
    evidence_intake_item_id UUID NOT NULL,
    last_sequence     INTEGER NOT NULL,
    CONSTRAINT pk_recib_sequence_allocator
        PRIMARY KEY (
            project_id, consumer_contract, binding_set_id, input_key
        ),
    CONSTRAINT fk_recib_allocator_project
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
    CONSTRAINT fk_recib_allocator_evidence_item
        FOREIGN KEY (evidence_intake_item_id, project_id)
        REFERENCES research_evidence_intake_item(id, project_id)
        ON DELETE RESTRICT,
    CONSTRAINT ck_recib_allocator_last_sequence CHECK (last_sequence >= 0)
);

CREATE INDEX IF NOT EXISTS idx_recib_scope_sequence
    ON research_evidence_consumer_input_binding(
        project_id, consumer_contract, binding_set_id, input_key,
        binding_sequence
    );

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = current_schema()
          AND p.proname = 'research_evidence_prepare_binding_insert'
          AND p.pronargs = 0
    ) THEN
        EXECUTE $create_function$
            CREATE FUNCTION research_evidence_prepare_binding_insert()
            RETURNS trigger
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = pg_catalog
            AS $function_body$
            DECLARE
                v_last integer;
                v_next integer;
                v_count integer;
                v_min integer;
                v_max integer;
                v_current_id uuid;
                v_malformed boolean;
            BEGIN
                IF NEW.binding_sequence IS NOT NULL
                   OR NEW.supersedes_binding_id IS NOT NULL THEN
                    RAISE EXCEPTION 'binding sequence is server-assigned'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.evaluated_at IS NOT NULL THEN
                    RAISE EXCEPTION 'evaluated_at is server-assigned'
                        USING ERRCODE = '23514';
                END IF;

                EXECUTE format(
                    'INSERT INTO
                         %I.research_evidence_consumer_input_binding_sequence_allocator
                         (project_id, consumer_contract, binding_set_id,
                          input_key, evidence_intake_item_id, last_sequence)
                     VALUES ($1, $2, $3, $4, $5, 0)
                     ON CONFLICT (
                         project_id, consumer_contract, binding_set_id, input_key
                     ) DO NOTHING',
                    TG_TABLE_SCHEMA
                ) USING NEW.project_id, NEW.consumer_contract,
                          NEW.binding_set_id, NEW.input_key,
                          NEW.evidence_intake_item_id;

                EXECUTE format(
                    'SELECT last_sequence
                     FROM
                         %I.research_evidence_consumer_input_binding_sequence_allocator
                     WHERE project_id = $1
                       AND consumer_contract = $2
                       AND binding_set_id = $3
                       AND input_key = $4
                       AND evidence_intake_item_id = $5
                     FOR UPDATE',
                    TG_TABLE_SCHEMA
                ) INTO v_last
                USING NEW.project_id, NEW.consumer_contract,
                      NEW.binding_set_id, NEW.input_key,
                      NEW.evidence_intake_item_id;
                IF v_last IS NULL THEN
                    RAISE EXCEPTION
                        'binding scope cannot change evidence intake item'
                        USING ERRCODE = '23514';
                END IF;

                EXECUTE format(
                    'SELECT count(*)::integer,
                            min(binding_sequence), max(binding_sequence)
                     FROM %I.research_evidence_consumer_input_binding
                     WHERE project_id = $1
                       AND consumer_contract = $2
                       AND binding_set_id = $3
                       AND input_key = $4',
                    TG_TABLE_SCHEMA
                ) INTO v_count, v_min, v_max
                USING NEW.project_id, NEW.consumer_contract,
                      NEW.binding_set_id, NEW.input_key;
                IF v_count <> v_last
                   OR (v_last = 0 AND (v_min IS NOT NULL OR v_max IS NOT NULL))
                   OR (v_last > 0 AND (v_min <> 1 OR v_max <> v_last)) THEN
                    RAISE EXCEPTION 'malformed binding evaluation chain'
                        USING ERRCODE = '23514';
                END IF;

                EXECUTE format(
                    'SELECT EXISTS (
                        SELECT 1
                        FROM %I.research_evidence_consumer_input_binding b
                        LEFT JOIN
                            %I.research_evidence_consumer_input_binding p
                          ON p.id = b.supersedes_binding_id
                         AND p.project_id = b.project_id
                         AND p.consumer_contract = b.consumer_contract
                         AND p.binding_set_id = b.binding_set_id
                         AND p.input_key = b.input_key
                         AND p.binding_sequence = b.binding_sequence - 1
                        WHERE b.project_id = $1
                          AND b.consumer_contract = $2
                          AND b.binding_set_id = $3
                          AND b.input_key = $4
                          AND (
                              (b.binding_sequence = 1
                               AND b.supersedes_binding_id IS NOT NULL)
                              OR
                              (b.binding_sequence > 1 AND p.id IS NULL)
                          )
                    )',
                    TG_TABLE_SCHEMA, TG_TABLE_SCHEMA
                ) INTO v_malformed
                USING NEW.project_id, NEW.consumer_contract,
                      NEW.binding_set_id, NEW.input_key;
                IF v_malformed THEN
                    RAISE EXCEPTION 'malformed binding evaluation chain'
                        USING ERRCODE = '23514';
                END IF;

                IF v_last > 0 THEN
                    EXECUTE format(
                        'SELECT id
                         FROM %I.research_evidence_consumer_input_binding
                         WHERE project_id = $1
                           AND consumer_contract = $2
                           AND binding_set_id = $3
                           AND input_key = $4
                           AND binding_sequence = $5',
                        TG_TABLE_SCHEMA
                    ) INTO v_current_id
                    USING NEW.project_id, NEW.consumer_contract,
                          NEW.binding_set_id, NEW.input_key, v_last;
                    IF v_current_id IS NULL THEN
                        RAISE EXCEPTION 'malformed binding evaluation chain'
                            USING ERRCODE = '23514';
                    END IF;
                    NEW.supersedes_binding_id := v_current_id;
                END IF;

                v_next := v_last + 1;
                EXECUTE format(
                    'UPDATE
                         %I.research_evidence_consumer_input_binding_sequence_allocator
                     SET last_sequence = $5
                     WHERE project_id = $1
                       AND consumer_contract = $2
                       AND binding_set_id = $3
                       AND input_key = $4',
                    TG_TABLE_SCHEMA
                ) USING NEW.project_id, NEW.consumer_contract,
                          NEW.binding_set_id, NEW.input_key, v_next;

                NEW.binding_sequence := v_next;
                NEW.evaluated_at := clock_timestamp();
                RETURN NEW;
            END;
            $function_body$
        $create_function$;
    END IF;
END $$;

REVOKE ALL ON FUNCTION research_evidence_prepare_binding_insert()
    FROM PUBLIC;
REVOKE ALL ON TABLE
    research_evidence_consumer_input_binding_sequence_allocator
    FROM PUBLIC;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_recib_prepare_insert'
          AND tgrelid =
              'research_evidence_consumer_input_binding'::regclass
          AND NOT tgisinternal
    ) THEN
        CREATE TRIGGER trg_recib_prepare_insert
            BEFORE INSERT ON research_evidence_consumer_input_binding
            FOR EACH ROW
            EXECUTE FUNCTION research_evidence_prepare_binding_insert();
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_recib_no_mutation'
          AND tgrelid =
              'research_evidence_consumer_input_binding'::regclass
          AND NOT tgisinternal
    ) THEN
        CREATE TRIGGER trg_recib_no_mutation
            BEFORE UPDATE OR DELETE
            ON research_evidence_consumer_input_binding
            FOR EACH ROW EXECUTE FUNCTION slicea_reject_mutation();
    END IF;
END $$;

ALTER TABLE research_evidence_consumer_input_binding
    ENABLE ALWAYS TRIGGER trg_recib_prepare_insert;

COMMIT;
