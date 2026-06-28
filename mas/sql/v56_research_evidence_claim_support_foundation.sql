-- v56 Research-Evidence Claim-Support Assessment Foundation (R1.5)
-- Additive, append-only operator assessments linking existing v53 claim-draft
-- intake items to existing v53 candidate-fact intake items.
--
-- Locator resolution, evidence linkage, and semantic relationship are separate
-- declarations.  No value in this ledger proves truth, approval, citation
-- readiness, availability, freshness, lineage currency, or downstream eligibility.
--
-- Apply manually after v47, v51, v52, v53, v54, and v55.

BEGIN;

-- Validate the concrete prior-wave graph consumed by v56 and classify v56 as
-- absent, complete, or partial/divergent.  No prior object or row is repaired.
DO $$
DECLARE
    v_parent_tables integer;
    v_v56_tables integer;
    v_prepare_oid oid;
    v_reject_oid oid;
    v_column_count integer;
    v_missing text;
BEGIN
    SELECT count(*) INTO v_parent_tables
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'r'
      AND n.nspname = current_schema()
      AND c.relname = ANY (ARRAY[
          'source_blob', 'source_snapshot', 'candidate_fact_revision',
          'evidence_retention_event', 'ingest_operation',
          'research_source_metadata_revision',
          'research_fact_metadata_revision', 'research_claim_draft',
          'research_evidence_event',
          'research_evidence_event_sequence_allocator',
          'research_evidence_intake',
          'research_evidence_intake_item',
          'research_evidence_intake_item_review_decision',
          'research_evidence_item_review_sequence_allocator',
          'research_evidence_intake_item_freshness_assessment',
          'research_evidence_item_freshness_sequence_allocator'
      ]);
    IF v_parent_tables <> 16 THEN
        RAISE EXCEPTION 'v56 requires complete v47-v55 parent tables, found %',
            v_parent_tables USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT p.oid INTO v_reject_oid
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = current_schema()
      AND p.proname = 'slicea_reject_mutation'
      AND p.pronargs = 0
      AND p.prorettype = 'trigger'::regtype;
    IF v_reject_oid IS NULL THEN
        RAISE EXCEPTION 'v56 requires canonical append-only guard'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('uq_reii_id_project'::text),
        ('uq_rei_id_project_snapshot'),
        ('uq_rsmr_id_project_snapshot'),
        ('uq_rfmr_id_project_fact'),
        ('uq_rcd_id_project'),
        ('uq_reird_item_request'),
        ('uq_reifa_item_request')
    ) expected(name)
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        WHERE con.connamespace = current_schema()::regnamespace
          AND con.conname = expected.name
          AND con.convalidated
    );
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v56 requires complete v47-v55 parent keys: %',
            v_missing USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT count(*) INTO v_v56_tables
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'r'
      AND n.nspname = current_schema()
      AND c.relname = ANY (ARRAY[
          'research_evidence_claim_support_assessment',
          'research_evidence_claim_support_sequence_allocator'
      ]);

    SELECT p.oid INTO v_prepare_oid
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = current_schema()
      AND p.proname = 'research_evidence_prepare_claim_support_insert'
      AND p.pronargs = 0
      AND p.prorettype = 'trigger'::regtype;

    IF v_v56_tables = 0
       AND v_prepare_oid IS NULL
       AND NOT EXISTS (
           SELECT 1
           FROM pg_trigger t
           JOIN pg_class c ON c.oid = t.tgrelid
           JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = current_schema()
             AND t.tgname IN (
                 'trg_recsa_prepare_insert', 'trg_recsa_no_mutation'
             )
       ) THEN
        RETURN;
    END IF;

    IF v_v56_tables <> 2 OR v_prepare_oid IS NULL THEN
        RAISE EXCEPTION
            'v56 contract violation: partial/divergent claim-support foundation'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT count(*) INTO v_column_count
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'research_evidence_claim_support_assessment';
    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('id'::text, 'uuid'::text, 'NO'::text),
        ('project_id', 'uuid', 'NO'),
        ('claim_intake_item_id', 'uuid', 'NO'),
        ('evidence_intake_item_id', 'uuid', 'NO'),
        ('request_id', 'text', 'NO'),
        ('locator_resolution', 'text', 'NO'),
        ('locator_rationale', 'text', 'NO'),
        ('evidence_linkage', 'text', 'NO'),
        ('evidence_linkage_rationale', 'text', 'NO'),
        ('semantic_relationship', 'text', 'NO'),
        ('semantic_relationship_rationale', 'text', 'NO'),
        ('assessed_by', 'text', 'NO'),
        ('assessment_sequence', 'integer', 'NO'),
        ('supersedes_assessment_id', 'uuid', 'YES'),
        ('claim_draft_id', 'uuid', 'NO'),
        ('claim_source_snapshot_id', 'uuid', 'NO'),
        ('claim_source_blob_id', 'uuid', 'NO'),
        ('claim_source_metadata_revision_id', 'uuid', 'NO'),
        ('evidence_source_snapshot_id', 'uuid', 'NO'),
        ('evidence_source_blob_id', 'uuid', 'NO'),
        ('evidence_source_metadata_revision_id', 'uuid', 'NO'),
        ('candidate_fact_revision_id', 'uuid', 'NO'),
        ('fact_metadata_revision_id', 'uuid', 'NO'),
        ('assessed_at', 'timestamp with time zone', 'NO')
    ) expected(name, data_type, nullable)
    LEFT JOIN information_schema.columns column_info
      ON column_info.table_schema = current_schema()
     AND column_info.table_name =
         'research_evidence_claim_support_assessment'
     AND column_info.column_name = expected.name
    WHERE column_info.column_name IS NULL
       OR column_info.data_type <> expected.data_type
       OR column_info.is_nullable <> expected.nullable;
    IF v_column_count <> 24 OR v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v56 contract violation: divergent assessment columns %',
            coalesce(v_missing, '(unexpected column count)')
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT count(*) INTO v_column_count
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name =
          'research_evidence_claim_support_sequence_allocator';
    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('project_id'::text, 'uuid'::text, 'NO'::text),
        ('claim_intake_item_id', 'uuid', 'NO'),
        ('evidence_intake_item_id', 'uuid', 'NO'),
        ('last_sequence', 'integer', 'NO')
    ) expected(name, data_type, nullable)
    LEFT JOIN information_schema.columns column_info
      ON column_info.table_schema = current_schema()
     AND column_info.table_name =
         'research_evidence_claim_support_sequence_allocator'
     AND column_info.column_name = expected.name
    WHERE column_info.column_name IS NULL
       OR column_info.data_type <> expected.data_type
       OR column_info.is_nullable <> expected.nullable;
    IF v_column_count <> 4 OR v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v56 contract violation: divergent allocator columns %',
            coalesce(v_missing, '(unexpected column count)')
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'research_evidence_claim_support_assessment'
          AND column_name IN (
              'assessment_sequence', 'supersedes_assessment_id',
              'claim_draft_id', 'claim_source_snapshot_id',
              'claim_source_blob_id', 'claim_source_metadata_revision_id',
              'evidence_source_snapshot_id', 'evidence_source_blob_id',
              'evidence_source_metadata_revision_id',
              'candidate_fact_revision_id', 'fact_metadata_revision_id',
              'assessed_at'
          )
          AND column_default IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'v56 contract violation: server-owned fields have defaults'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_attribute a
        JOIN pg_attrdef d
          ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE a.attrelid =
              'research_evidence_claim_support_assessment'::regclass
          AND a.attname = 'id'
          AND lower(regexp_replace(
              pg_get_expr(d.adbin, d.adrelid, true),
              '[[:space:]]+', '', 'g'
          )) = 'gen_random_uuid()'
    ) THEN
        RAISE EXCEPTION 'v56 contract violation: divergent assessment id default'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('research_evidence_claim_support_assessment_pkey'::text,
         'research_evidence_claim_support_assessment'::text, 'p'::"char"),
        ('uq_recsa_id_project_pair',
         'research_evidence_claim_support_assessment', 'u'),
        ('uq_recsa_pair_sequence',
         'research_evidence_claim_support_assessment', 'u'),
        ('uq_recsa_pair_request',
         'research_evidence_claim_support_assessment', 'u'),
        ('uq_recsa_supersedes_once',
         'research_evidence_claim_support_assessment', 'u'),
        ('fk_recsa_project',
         'research_evidence_claim_support_assessment', 'f'),
        ('fk_recsa_claim_item_project',
         'research_evidence_claim_support_assessment', 'f'),
        ('fk_recsa_evidence_item_project',
         'research_evidence_claim_support_assessment', 'f'),
        ('fk_recsa_supersedes_same_pair',
         'research_evidence_claim_support_assessment', 'f'),
        ('fk_recsa_claim_draft_project',
         'research_evidence_claim_support_assessment', 'f'),
        ('fk_recsa_claim_snapshot_project',
         'research_evidence_claim_support_assessment', 'f'),
        ('fk_recsa_claim_blob_project',
         'research_evidence_claim_support_assessment', 'f'),
        ('fk_recsa_claim_source_metadata_snapshot',
         'research_evidence_claim_support_assessment', 'f'),
        ('fk_recsa_evidence_snapshot_project',
         'research_evidence_claim_support_assessment', 'f'),
        ('fk_recsa_evidence_blob_project',
         'research_evidence_claim_support_assessment', 'f'),
        ('fk_recsa_evidence_source_metadata_snapshot',
         'research_evidence_claim_support_assessment', 'f'),
        ('fk_recsa_fact_project',
         'research_evidence_claim_support_assessment', 'f'),
        ('fk_recsa_fact_metadata_fact',
         'research_evidence_claim_support_assessment', 'f'),
        ('ck_recsa_sequence_positive',
         'research_evidence_claim_support_assessment', 'c'),
        ('ck_recsa_distinct_items',
         'research_evidence_claim_support_assessment', 'c'),
        ('ck_recsa_locator_resolution',
         'research_evidence_claim_support_assessment', 'c'),
        ('ck_recsa_evidence_linkage',
         'research_evidence_claim_support_assessment', 'c'),
        ('ck_recsa_semantic_relationship',
         'research_evidence_claim_support_assessment', 'c'),
        ('ck_recsa_request_nonblank',
         'research_evidence_claim_support_assessment', 'c'),
        ('ck_recsa_locator_rationale_nonblank',
         'research_evidence_claim_support_assessment', 'c'),
        ('ck_recsa_linkage_rationale_nonblank',
         'research_evidence_claim_support_assessment', 'c'),
        ('ck_recsa_semantic_rationale_nonblank',
         'research_evidence_claim_support_assessment', 'c'),
        ('ck_recsa_assessed_by_nonblank',
         'research_evidence_claim_support_assessment', 'c'),
        ('research_evidence_claim_support_sequence_allocator_pkey',
         'research_evidence_claim_support_sequence_allocator', 'p'),
        ('fk_recsa_allocator_project',
         'research_evidence_claim_support_sequence_allocator', 'f'),
        ('fk_recsa_allocator_claim_item',
         'research_evidence_claim_support_sequence_allocator', 'f'),
        ('fk_recsa_allocator_evidence_item',
         'research_evidence_claim_support_sequence_allocator', 'f'),
        ('ck_recsa_allocator_last_sequence',
         'research_evidence_claim_support_sequence_allocator', 'c')
    ) expected(name, table_name, kind)
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        WHERE con.connamespace = current_schema()::regnamespace
          AND con.conname = expected.name
          AND con.conrelid = expected.table_name::regclass
          AND con.contype = expected.kind
          AND con.convalidated
    );
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v56 contract violation: missing constraints %',
            v_missing USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- Primary and unique key columns are exact, including pair-scoped request
    -- identity and the predecessor single-use rule.
    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('research_evidence_claim_support_assessment_pkey'::text,
         ARRAY['id']::text[]),
        ('uq_recsa_id_project_pair',
         ARRAY['id', 'project_id', 'claim_intake_item_id',
               'evidence_intake_item_id']),
        ('uq_recsa_pair_sequence',
         ARRAY['project_id', 'claim_intake_item_id',
               'evidence_intake_item_id', 'assessment_sequence']),
        ('uq_recsa_pair_request',
         ARRAY['project_id', 'claim_intake_item_id',
               'evidence_intake_item_id', 'request_id']),
        ('uq_recsa_supersedes_once',
         ARRAY['supersedes_assessment_id']),
        ('research_evidence_claim_support_sequence_allocator_pkey',
         ARRAY['project_id', 'claim_intake_item_id',
               'evidence_intake_item_id'])
    ) expected(name, columns)
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        WHERE con.connamespace = current_schema()::regnamespace
          AND con.conname = expected.name
          AND (
              SELECT array_agg(a.attname::text ORDER BY u.ordinality)
              FROM unnest(con.conkey) WITH ORDINALITY u(attnum, ordinality)
              JOIN pg_attribute a
                ON a.attrelid = con.conrelid AND a.attnum = u.attnum
          ) = expected.columns
    );
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v56 contract violation: divergent keys %',
            v_missing USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('fk_recsa_project'::text,
         'research_evidence_claim_support_assessment'::text,
         'projects'::text, ARRAY['project_id']::text[], ARRAY['id']::text[]),
        ('fk_recsa_claim_item_project',
         'research_evidence_claim_support_assessment',
         'research_evidence_intake_item',
         ARRAY['claim_intake_item_id', 'project_id'],
         ARRAY['id', 'project_id']),
        ('fk_recsa_evidence_item_project',
         'research_evidence_claim_support_assessment',
         'research_evidence_intake_item',
         ARRAY['evidence_intake_item_id', 'project_id'],
         ARRAY['id', 'project_id']),
        ('fk_recsa_supersedes_same_pair',
         'research_evidence_claim_support_assessment',
         'research_evidence_claim_support_assessment',
         ARRAY['supersedes_assessment_id', 'project_id',
               'claim_intake_item_id', 'evidence_intake_item_id'],
         ARRAY['id', 'project_id', 'claim_intake_item_id',
               'evidence_intake_item_id']),
        ('fk_recsa_claim_draft_project',
         'research_evidence_claim_support_assessment',
         'research_claim_draft',
         ARRAY['claim_draft_id', 'project_id'], ARRAY['id', 'project_id']),
        ('fk_recsa_claim_snapshot_project',
         'research_evidence_claim_support_assessment',
         'source_snapshot',
         ARRAY['claim_source_snapshot_id', 'project_id'],
         ARRAY['id', 'project_id']),
        ('fk_recsa_claim_blob_project',
         'research_evidence_claim_support_assessment',
         'source_blob',
         ARRAY['claim_source_blob_id', 'project_id'],
         ARRAY['id', 'project_id']),
        ('fk_recsa_claim_source_metadata_snapshot',
         'research_evidence_claim_support_assessment',
         'research_source_metadata_revision',
         ARRAY['claim_source_metadata_revision_id', 'project_id',
               'claim_source_snapshot_id'],
         ARRAY['id', 'project_id', 'source_snapshot_id']),
        ('fk_recsa_evidence_snapshot_project',
         'research_evidence_claim_support_assessment',
         'source_snapshot',
         ARRAY['evidence_source_snapshot_id', 'project_id'],
         ARRAY['id', 'project_id']),
        ('fk_recsa_evidence_blob_project',
         'research_evidence_claim_support_assessment',
         'source_blob',
         ARRAY['evidence_source_blob_id', 'project_id'],
         ARRAY['id', 'project_id']),
        ('fk_recsa_evidence_source_metadata_snapshot',
         'research_evidence_claim_support_assessment',
         'research_source_metadata_revision',
         ARRAY['evidence_source_metadata_revision_id', 'project_id',
               'evidence_source_snapshot_id'],
         ARRAY['id', 'project_id', 'source_snapshot_id']),
        ('fk_recsa_fact_project',
         'research_evidence_claim_support_assessment',
         'candidate_fact_revision',
         ARRAY['candidate_fact_revision_id', 'project_id'],
         ARRAY['id', 'project_id']),
        ('fk_recsa_fact_metadata_fact',
         'research_evidence_claim_support_assessment',
         'research_fact_metadata_revision',
         ARRAY['fact_metadata_revision_id', 'project_id',
               'candidate_fact_revision_id'],
         ARRAY['id', 'project_id', 'candidate_fact_revision_id']),
        ('fk_recsa_allocator_project',
         'research_evidence_claim_support_sequence_allocator',
         'projects', ARRAY['project_id'], ARRAY['id']),
        ('fk_recsa_allocator_claim_item',
         'research_evidence_claim_support_sequence_allocator',
         'research_evidence_intake_item',
         ARRAY['claim_intake_item_id', 'project_id'],
         ARRAY['id', 'project_id']),
        ('fk_recsa_allocator_evidence_item',
         'research_evidence_claim_support_sequence_allocator',
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
          AND con.confdeltype = 'r'
          AND con.convalidated
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
        RAISE EXCEPTION 'v56 contract violation: divergent foreign keys %',
            v_missing USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('ck_recsa_sequence_positive'::text,
         'research_evidence_claim_support_assessment'::text,
         'assessment_sequence>=1'::text),
        ('ck_recsa_distinct_items',
         'research_evidence_claim_support_assessment',
         'claim_intake_item_id<>evidence_intake_item_id'),
        ('ck_recsa_locator_resolution',
         'research_evidence_claim_support_assessment',
         'locator_resolution=anyarray[''not_assessed'',''resolvable'','
         || '''unresolvable'',''indeterminate'']'),
        ('ck_recsa_evidence_linkage',
         'research_evidence_claim_support_assessment',
         'evidence_linkage=anyarray[''not_assessed'',''linked'','
         || '''not_linked'',''indeterminate'']'),
        ('ck_recsa_semantic_relationship',
         'research_evidence_claim_support_assessment',
         'semantic_relationship=anyarray[''not_assessed'',''support'','
         || '''contradiction'',''qualification'',''insufficient_evidence'']'),
        ('ck_recsa_request_nonblank',
         'research_evidence_claim_support_assessment',
         'request_id!~''^[[:space:]]*$'''),
        ('ck_recsa_locator_rationale_nonblank',
         'research_evidence_claim_support_assessment',
         'locator_rationale!~''^[[:space:]]*$'''),
        ('ck_recsa_linkage_rationale_nonblank',
         'research_evidence_claim_support_assessment',
         'evidence_linkage_rationale!~''^[[:space:]]*$'''),
        ('ck_recsa_semantic_rationale_nonblank',
         'research_evidence_claim_support_assessment',
         'semantic_relationship_rationale!~''^[[:space:]]*$'''),
        ('ck_recsa_assessed_by_nonblank',
         'research_evidence_claim_support_assessment',
         'assessed_by!~''^[[:space:]]*$'''),
        ('ck_recsa_allocator_last_sequence',
         'research_evidence_claim_support_sequence_allocator',
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
              translate(
                  regexp_replace(
                      lower(pg_get_expr(con.conbin, con.conrelid, true)),
                      '[[:space:]]+', '', 'g'
                  ),
                  '()', ''
              ),
              '::text', ''
          ) = expected.normalized_expression
    );
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION
            'v56 contract violation: divergent claim-support check constraints %',
            v_missing USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT p.oid INTO v_prepare_oid
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = current_schema()
      AND p.proname = 'research_evidence_prepare_claim_support_insert'
      AND p.pronargs = 0
      AND p.prorettype = 'trigger'::regtype
      AND p.prosecdef
      AND p.proconfig = ARRAY['search_path=pg_catalog']
      AND md5(regexp_replace(p.prosrc, '[[:space:]]+', '', 'g')) =
          'f491f24034dd24bb8ae4edb2dad8981a'
      AND NOT EXISTS (
          SELECT 1
          FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) acl
          WHERE acl.grantee = 0 AND acl.privilege_type = 'EXECUTE'
      );
    IF v_prepare_oid IS NULL THEN
        RAISE EXCEPTION
            'v56 contract violation: divergent claim-support prepare function'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_class index_class
        JOIN pg_namespace n ON n.oid = index_class.relnamespace
        JOIN pg_index index_info ON index_info.indexrelid = index_class.oid
        WHERE n.nspname = current_schema()
          AND index_class.relname = 'idx_recsa_pair_sequence'
          AND index_info.indrelid =
              'research_evidence_claim_support_assessment'::regclass
          AND index_info.indisvalid
          AND index_info.indisready
          AND NOT index_info.indisunique
          AND index_info.indpred IS NULL
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
              'project_id', 'claim_intake_item_id',
              'evidence_intake_item_id', 'assessment_sequence'
          ]::text[]
    ) THEN
        RAISE EXCEPTION 'v56 contract violation: divergent claim-support index'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('trg_recsa_prepare_insert'::text, v_prepare_oid, 'A'::"char", 7),
        ('trg_recsa_no_mutation', v_reject_oid, 'O'::"char", 27)
    ) expected(name, function_oid, enabled, trigger_type)
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_trigger t
        WHERE t.tgname = expected.name
          AND t.tgrelid =
              'research_evidence_claim_support_assessment'::regclass
          AND t.tgfoid = expected.function_oid
          AND t.tgenabled = expected.enabled
          AND t.tgtype = expected.trigger_type
          AND NOT t.tgisinternal
    );
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v56 contract violation: divergent triggers %',
            v_missing USING ERRCODE = 'invalid_schema_definition';
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
              'research_evidence_claim_support_sequence_allocator'
          AND acl.grantee = 0
    ) THEN
        RAISE EXCEPTION 'v56 contract violation: allocator has PUBLIC privileges'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM (
            SELECT project_id, claim_intake_item_id, evidence_intake_item_id,
                   count(*)::integer AS row_count,
                   min(assessment_sequence) AS min_sequence,
                   max(assessment_sequence) AS max_sequence
            FROM research_evidence_claim_support_assessment
            GROUP BY project_id, claim_intake_item_id, evidence_intake_item_id
        ) history
        FULL JOIN research_evidence_claim_support_sequence_allocator allocator
          USING (project_id, claim_intake_item_id, evidence_intake_item_id)
        WHERE history.project_id IS NULL
           OR allocator.project_id IS NULL
           OR history.row_count <> allocator.last_sequence
           OR history.min_sequence <> 1
           OR history.max_sequence <> allocator.last_sequence
    ) THEN
        RAISE EXCEPTION
            'v56 contract violation: allocator diverges from pair history'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM research_evidence_claim_support_assessment assessment
        LEFT JOIN research_evidence_claim_support_assessment predecessor
          ON predecessor.id = assessment.supersedes_assessment_id
         AND predecessor.project_id = assessment.project_id
         AND predecessor.claim_intake_item_id =
             assessment.claim_intake_item_id
         AND predecessor.evidence_intake_item_id =
             assessment.evidence_intake_item_id
         AND predecessor.assessment_sequence =
             assessment.assessment_sequence - 1
        WHERE (assessment.assessment_sequence = 1
               AND assessment.supersedes_assessment_id IS NOT NULL)
           OR (assessment.assessment_sequence > 1 AND predecessor.id IS NULL)
    ) THEN
        RAISE EXCEPTION 'v56 contract violation: malformed pair chain'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM research_evidence_claim_support_assessment assessment
        JOIN research_evidence_intake_item claim_item
          ON claim_item.id = assessment.claim_intake_item_id
         AND claim_item.project_id = assessment.project_id
        JOIN research_evidence_intake claim_intake
          ON claim_intake.id = claim_item.research_evidence_intake_id
         AND claim_intake.project_id = claim_item.project_id
         AND claim_intake.source_snapshot_id = claim_item.source_snapshot_id
        JOIN source_snapshot claim_snapshot
          ON claim_snapshot.id = claim_item.source_snapshot_id
         AND claim_snapshot.project_id = claim_item.project_id
        JOIN research_evidence_intake_item evidence_item
          ON evidence_item.id = assessment.evidence_intake_item_id
         AND evidence_item.project_id = assessment.project_id
        JOIN research_evidence_intake evidence_intake
          ON evidence_intake.id = evidence_item.research_evidence_intake_id
         AND evidence_intake.project_id = evidence_item.project_id
         AND evidence_intake.source_snapshot_id =
             evidence_item.source_snapshot_id
        JOIN source_snapshot evidence_snapshot
          ON evidence_snapshot.id = evidence_item.source_snapshot_id
         AND evidence_snapshot.project_id = evidence_item.project_id
        WHERE claim_item.item_kind <> 'claim_draft'
           OR evidence_item.item_kind <> 'candidate_fact'
           OR assessment.claim_draft_id IS DISTINCT FROM
              claim_item.claim_draft_id
           OR assessment.claim_source_snapshot_id IS DISTINCT FROM
              claim_item.source_snapshot_id
           OR assessment.claim_source_blob_id IS DISTINCT FROM
              claim_snapshot.source_blob_id
           OR assessment.claim_source_metadata_revision_id IS DISTINCT FROM
              claim_intake.source_metadata_revision_id
           OR assessment.evidence_source_snapshot_id IS DISTINCT FROM
              evidence_item.source_snapshot_id
           OR assessment.evidence_source_blob_id IS DISTINCT FROM
              evidence_snapshot.source_blob_id
           OR assessment.evidence_source_metadata_revision_id IS DISTINCT FROM
              evidence_intake.source_metadata_revision_id
           OR assessment.candidate_fact_revision_id IS DISTINCT FROM
              evidence_item.candidate_fact_revision_id
           OR assessment.fact_metadata_revision_id IS DISTINCT FROM
              evidence_item.fact_metadata_revision_id
    ) THEN
        RAISE EXCEPTION
            'v56 contract violation: linked identities diverge from intake graph'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS research_evidence_claim_support_assessment (
    id                                   UUID NOT NULL DEFAULT gen_random_uuid(),
    project_id                           UUID NOT NULL,
    claim_intake_item_id                 UUID NOT NULL,
    evidence_intake_item_id              UUID NOT NULL,
    request_id                           TEXT NOT NULL,
    locator_resolution                   TEXT NOT NULL,
    locator_rationale                    TEXT NOT NULL,
    evidence_linkage                     TEXT NOT NULL,
    evidence_linkage_rationale           TEXT NOT NULL,
    semantic_relationship                TEXT NOT NULL,
    semantic_relationship_rationale      TEXT NOT NULL,
    assessed_by                          TEXT NOT NULL,
    assessment_sequence                  INTEGER NOT NULL,
    supersedes_assessment_id             UUID,
    claim_draft_id                       UUID NOT NULL,
    claim_source_snapshot_id             UUID NOT NULL,
    claim_source_blob_id                 UUID NOT NULL,
    claim_source_metadata_revision_id    UUID NOT NULL,
    evidence_source_snapshot_id          UUID NOT NULL,
    evidence_source_blob_id              UUID NOT NULL,
    evidence_source_metadata_revision_id UUID NOT NULL,
    candidate_fact_revision_id           UUID NOT NULL,
    fact_metadata_revision_id            UUID NOT NULL,
    assessed_at                          TIMESTAMPTZ NOT NULL,
    CONSTRAINT research_evidence_claim_support_assessment_pkey
        PRIMARY KEY (id),
    CONSTRAINT uq_recsa_id_project_pair
        UNIQUE (
            id, project_id, claim_intake_item_id, evidence_intake_item_id
        ),
    CONSTRAINT uq_recsa_pair_sequence
        UNIQUE (
            project_id, claim_intake_item_id, evidence_intake_item_id,
            assessment_sequence
        ),
    CONSTRAINT uq_recsa_pair_request
        UNIQUE (
            project_id, claim_intake_item_id, evidence_intake_item_id,
            request_id
        ),
    CONSTRAINT uq_recsa_supersedes_once UNIQUE (supersedes_assessment_id),
    CONSTRAINT fk_recsa_project
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
    CONSTRAINT fk_recsa_claim_item_project
        FOREIGN KEY (claim_intake_item_id, project_id)
        REFERENCES research_evidence_intake_item(id, project_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_recsa_evidence_item_project
        FOREIGN KEY (evidence_intake_item_id, project_id)
        REFERENCES research_evidence_intake_item(id, project_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_recsa_supersedes_same_pair
        FOREIGN KEY (
            supersedes_assessment_id, project_id,
            claim_intake_item_id, evidence_intake_item_id
        )
        REFERENCES research_evidence_claim_support_assessment(
            id, project_id, claim_intake_item_id, evidence_intake_item_id
        ) ON DELETE RESTRICT,
    CONSTRAINT fk_recsa_claim_draft_project
        FOREIGN KEY (claim_draft_id, project_id)
        REFERENCES research_claim_draft(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT fk_recsa_claim_snapshot_project
        FOREIGN KEY (claim_source_snapshot_id, project_id)
        REFERENCES source_snapshot(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT fk_recsa_claim_blob_project
        FOREIGN KEY (claim_source_blob_id, project_id)
        REFERENCES source_blob(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT fk_recsa_claim_source_metadata_snapshot
        FOREIGN KEY (
            claim_source_metadata_revision_id, project_id,
            claim_source_snapshot_id
        )
        REFERENCES research_source_metadata_revision(
            id, project_id, source_snapshot_id
        ) ON DELETE RESTRICT,
    CONSTRAINT fk_recsa_evidence_snapshot_project
        FOREIGN KEY (evidence_source_snapshot_id, project_id)
        REFERENCES source_snapshot(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT fk_recsa_evidence_blob_project
        FOREIGN KEY (evidence_source_blob_id, project_id)
        REFERENCES source_blob(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT fk_recsa_evidence_source_metadata_snapshot
        FOREIGN KEY (
            evidence_source_metadata_revision_id, project_id,
            evidence_source_snapshot_id
        )
        REFERENCES research_source_metadata_revision(
            id, project_id, source_snapshot_id
        ) ON DELETE RESTRICT,
    CONSTRAINT fk_recsa_fact_project
        FOREIGN KEY (candidate_fact_revision_id, project_id)
        REFERENCES candidate_fact_revision(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT fk_recsa_fact_metadata_fact
        FOREIGN KEY (
            fact_metadata_revision_id, project_id, candidate_fact_revision_id
        )
        REFERENCES research_fact_metadata_revision(
            id, project_id, candidate_fact_revision_id
        ) ON DELETE RESTRICT,
    CONSTRAINT ck_recsa_sequence_positive CHECK (assessment_sequence >= 1),
    CONSTRAINT ck_recsa_distinct_items CHECK (
        claim_intake_item_id <> evidence_intake_item_id
    ),
    CONSTRAINT ck_recsa_locator_resolution CHECK (
        locator_resolution IN (
            'not_assessed', 'resolvable', 'unresolvable', 'indeterminate'
        )
    ),
    CONSTRAINT ck_recsa_evidence_linkage CHECK (
        evidence_linkage IN (
            'not_assessed', 'linked', 'not_linked', 'indeterminate'
        )
    ),
    CONSTRAINT ck_recsa_semantic_relationship CHECK (
        semantic_relationship IN (
            'not_assessed', 'support', 'contradiction', 'qualification',
            'insufficient_evidence'
        )
    ),
    CONSTRAINT ck_recsa_request_nonblank CHECK (
        request_id !~ '^[[:space:]]*$'
    ),
    CONSTRAINT ck_recsa_locator_rationale_nonblank CHECK (
        locator_rationale !~ '^[[:space:]]*$'
    ),
    CONSTRAINT ck_recsa_linkage_rationale_nonblank CHECK (
        evidence_linkage_rationale !~ '^[[:space:]]*$'
    ),
    CONSTRAINT ck_recsa_semantic_rationale_nonblank CHECK (
        semantic_relationship_rationale !~ '^[[:space:]]*$'
    ),
    CONSTRAINT ck_recsa_assessed_by_nonblank CHECK (
        assessed_by !~ '^[[:space:]]*$'
    )
);

CREATE TABLE IF NOT EXISTS
research_evidence_claim_support_sequence_allocator (
    project_id              UUID NOT NULL,
    claim_intake_item_id    UUID NOT NULL,
    evidence_intake_item_id UUID NOT NULL,
    last_sequence           INTEGER NOT NULL,
    CONSTRAINT research_evidence_claim_support_sequence_allocator_pkey
        PRIMARY KEY (
            project_id, claim_intake_item_id, evidence_intake_item_id
        ),
    CONSTRAINT fk_recsa_allocator_project
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
    CONSTRAINT fk_recsa_allocator_claim_item
        FOREIGN KEY (claim_intake_item_id, project_id)
        REFERENCES research_evidence_intake_item(id, project_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_recsa_allocator_evidence_item
        FOREIGN KEY (evidence_intake_item_id, project_id)
        REFERENCES research_evidence_intake_item(id, project_id)
        ON DELETE RESTRICT,
    CONSTRAINT ck_recsa_allocator_last_sequence CHECK (last_sequence >= 0)
);

CREATE INDEX IF NOT EXISTS idx_recsa_pair_sequence
    ON research_evidence_claim_support_assessment(
        project_id, claim_intake_item_id, evidence_intake_item_id,
        assessment_sequence
    );

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = current_schema()
          AND p.proname = 'research_evidence_prepare_claim_support_insert'
          AND p.pronargs = 0
    ) THEN
        EXECUTE $create_function$
            CREATE FUNCTION research_evidence_prepare_claim_support_insert()
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
                v_context_rows integer;
            BEGIN
                IF NEW.assessment_sequence IS NOT NULL THEN
                    RAISE EXCEPTION 'assessment_sequence is server-assigned'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.supersedes_assessment_id IS NOT NULL THEN
                    RAISE EXCEPTION
                        'supersedes_assessment_id is server-assigned'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.claim_draft_id IS NOT NULL
                   OR NEW.claim_source_snapshot_id IS NOT NULL
                   OR NEW.claim_source_blob_id IS NOT NULL
                   OR NEW.claim_source_metadata_revision_id IS NOT NULL
                   OR NEW.evidence_source_snapshot_id IS NOT NULL
                   OR NEW.evidence_source_blob_id IS NOT NULL
                   OR NEW.evidence_source_metadata_revision_id IS NOT NULL
                   OR NEW.candidate_fact_revision_id IS NOT NULL
                   OR NEW.fact_metadata_revision_id IS NOT NULL THEN
                    RAISE EXCEPTION 'linked identities are server-assigned'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.assessed_at IS NOT NULL THEN
                    RAISE EXCEPTION 'assessed_at is server-assigned'
                        USING ERRCODE = '23514';
                END IF;

                EXECUTE format(
                    'SELECT claim.id,
                            snapshot.id,
                            blob.id,
                            source_metadata.id
                     FROM %I.research_evidence_intake_item item
                     JOIN %I.research_evidence_intake intake
                       ON intake.id = item.research_evidence_intake_id
                      AND intake.project_id = item.project_id
                      AND intake.source_snapshot_id = item.source_snapshot_id
                     JOIN %I.source_snapshot snapshot
                       ON snapshot.id = item.source_snapshot_id
                      AND snapshot.project_id = item.project_id
                     JOIN %I.source_blob blob
                       ON blob.id = snapshot.source_blob_id
                      AND blob.project_id = snapshot.project_id
                     JOIN %I.research_source_metadata_revision source_metadata
                       ON source_metadata.id = intake.source_metadata_revision_id
                      AND source_metadata.project_id = intake.project_id
                      AND source_metadata.source_snapshot_id =
                          intake.source_snapshot_id
                     JOIN %I.research_claim_draft claim
                       ON claim.id = item.claim_draft_id
                      AND claim.project_id = item.project_id
                     WHERE item.id = $1
                       AND item.project_id = $2
                       AND item.item_kind = ''claim_draft''',
                    TG_TABLE_SCHEMA, TG_TABLE_SCHEMA, TG_TABLE_SCHEMA,
                    TG_TABLE_SCHEMA, TG_TABLE_SCHEMA, TG_TABLE_SCHEMA
                )
                INTO NEW.claim_draft_id,
                     NEW.claim_source_snapshot_id,
                     NEW.claim_source_blob_id,
                     NEW.claim_source_metadata_revision_id
                USING NEW.claim_intake_item_id, NEW.project_id;
                GET DIAGNOSTICS v_context_rows = ROW_COUNT;
                IF v_context_rows <> 1 THEN
                    RAISE EXCEPTION
                        'claim-draft intake item not found for project'
                        USING ERRCODE = '23503';
                END IF;

                EXECUTE format(
                    'SELECT snapshot.id,
                            blob.id,
                            source_metadata.id,
                            fact.id,
                            fact_metadata.id
                     FROM %I.research_evidence_intake_item item
                     JOIN %I.research_evidence_intake intake
                       ON intake.id = item.research_evidence_intake_id
                      AND intake.project_id = item.project_id
                      AND intake.source_snapshot_id = item.source_snapshot_id
                     JOIN %I.source_snapshot snapshot
                       ON snapshot.id = item.source_snapshot_id
                      AND snapshot.project_id = item.project_id
                     JOIN %I.source_blob blob
                       ON blob.id = snapshot.source_blob_id
                      AND blob.project_id = snapshot.project_id
                     JOIN %I.research_source_metadata_revision source_metadata
                       ON source_metadata.id = intake.source_metadata_revision_id
                      AND source_metadata.project_id = intake.project_id
                      AND source_metadata.source_snapshot_id =
                          intake.source_snapshot_id
                     JOIN %I.candidate_fact_revision fact
                       ON fact.id = item.candidate_fact_revision_id
                      AND fact.project_id = item.project_id
                      AND fact.source_snapshot_id = item.source_snapshot_id
                     JOIN %I.research_fact_metadata_revision fact_metadata
                       ON fact_metadata.id = item.fact_metadata_revision_id
                      AND fact_metadata.project_id = item.project_id
                      AND fact_metadata.candidate_fact_revision_id = fact.id
                     WHERE item.id = $1
                       AND item.project_id = $2
                       AND item.item_kind = ''candidate_fact''',
                    TG_TABLE_SCHEMA, TG_TABLE_SCHEMA, TG_TABLE_SCHEMA,
                    TG_TABLE_SCHEMA, TG_TABLE_SCHEMA, TG_TABLE_SCHEMA,
                    TG_TABLE_SCHEMA
                )
                INTO NEW.evidence_source_snapshot_id,
                     NEW.evidence_source_blob_id,
                     NEW.evidence_source_metadata_revision_id,
                     NEW.candidate_fact_revision_id,
                     NEW.fact_metadata_revision_id
                USING NEW.evidence_intake_item_id, NEW.project_id;
                GET DIAGNOSTICS v_context_rows = ROW_COUNT;
                IF v_context_rows <> 1 THEN
                    RAISE EXCEPTION
                        'candidate-fact intake item not found for project'
                        USING ERRCODE = '23503';
                END IF;

                EXECUTE format(
                    'INSERT INTO
                         %I.research_evidence_claim_support_sequence_allocator
                         (project_id, claim_intake_item_id,
                          evidence_intake_item_id, last_sequence)
                     VALUES ($1, $2, $3, 0)
                     ON CONFLICT (
                         project_id, claim_intake_item_id,
                         evidence_intake_item_id
                     ) DO NOTHING',
                    TG_TABLE_SCHEMA
                ) USING NEW.project_id, NEW.claim_intake_item_id,
                          NEW.evidence_intake_item_id;

                EXECUTE format(
                    'SELECT last_sequence
                     FROM
                         %I.research_evidence_claim_support_sequence_allocator
                     WHERE project_id = $1
                       AND claim_intake_item_id = $2
                       AND evidence_intake_item_id = $3
                     FOR UPDATE',
                    TG_TABLE_SCHEMA
                ) INTO v_last
                USING NEW.project_id, NEW.claim_intake_item_id,
                      NEW.evidence_intake_item_id;

                EXECUTE format(
                    'SELECT count(*)::integer,
                            min(assessment_sequence),
                            max(assessment_sequence)
                     FROM %I.research_evidence_claim_support_assessment
                     WHERE project_id = $1
                       AND claim_intake_item_id = $2
                       AND evidence_intake_item_id = $3',
                    TG_TABLE_SCHEMA
                ) INTO v_count, v_min, v_max
                USING NEW.project_id, NEW.claim_intake_item_id,
                      NEW.evidence_intake_item_id;

                IF v_count <> v_last
                   OR (v_last = 0 AND (v_min IS NOT NULL OR v_max IS NOT NULL))
                   OR (v_last > 0 AND (v_min <> 1 OR v_max <> v_last)) THEN
                    RAISE EXCEPTION 'malformed claim-support assessment chain'
                        USING ERRCODE = '23514';
                END IF;

                EXECUTE format(
                    'SELECT EXISTS (
                        SELECT 1
                        FROM %I.research_evidence_claim_support_assessment a
                        LEFT JOIN
                            %I.research_evidence_claim_support_assessment p
                          ON p.id = a.supersedes_assessment_id
                         AND p.project_id = a.project_id
                         AND p.claim_intake_item_id =
                             a.claim_intake_item_id
                         AND p.evidence_intake_item_id =
                             a.evidence_intake_item_id
                         AND p.assessment_sequence =
                             a.assessment_sequence - 1
                        WHERE a.project_id = $1
                          AND a.claim_intake_item_id = $2
                          AND a.evidence_intake_item_id = $3
                          AND (
                              (a.assessment_sequence = 1
                               AND a.supersedes_assessment_id IS NOT NULL)
                              OR
                              (a.assessment_sequence > 1 AND p.id IS NULL)
                          )
                    )',
                    TG_TABLE_SCHEMA, TG_TABLE_SCHEMA
                ) INTO v_malformed
                USING NEW.project_id, NEW.claim_intake_item_id,
                      NEW.evidence_intake_item_id;
                IF v_malformed THEN
                    RAISE EXCEPTION 'malformed claim-support assessment chain'
                        USING ERRCODE = '23514';
                END IF;

                IF v_last > 0 THEN
                    EXECUTE format(
                        'SELECT id
                         FROM %I.research_evidence_claim_support_assessment
                         WHERE project_id = $1
                           AND claim_intake_item_id = $2
                           AND evidence_intake_item_id = $3
                           AND assessment_sequence = $4',
                        TG_TABLE_SCHEMA
                    ) INTO v_current_id
                    USING NEW.project_id, NEW.claim_intake_item_id,
                          NEW.evidence_intake_item_id, v_last;
                    IF v_current_id IS NULL THEN
                        RAISE EXCEPTION
                            'malformed claim-support assessment chain'
                            USING ERRCODE = '23514';
                    END IF;
                    NEW.supersedes_assessment_id := v_current_id;
                END IF;

                v_next := v_last + 1;
                EXECUTE format(
                    'UPDATE
                         %I.research_evidence_claim_support_sequence_allocator
                     SET last_sequence = $4
                     WHERE project_id = $1
                       AND claim_intake_item_id = $2
                       AND evidence_intake_item_id = $3',
                    TG_TABLE_SCHEMA
                ) USING NEW.project_id, NEW.claim_intake_item_id,
                          NEW.evidence_intake_item_id, v_next;

                NEW.assessment_sequence := v_next;
                NEW.assessed_at := clock_timestamp();
                RETURN NEW;
            END;
            $function_body$
        $create_function$;
    END IF;
END $$;

REVOKE ALL ON FUNCTION research_evidence_prepare_claim_support_insert()
    FROM PUBLIC;
REVOKE ALL ON TABLE research_evidence_claim_support_sequence_allocator
    FROM PUBLIC;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_recsa_prepare_insert'
          AND tgrelid =
              'research_evidence_claim_support_assessment'::regclass
          AND NOT tgisinternal
    ) THEN
        CREATE TRIGGER trg_recsa_prepare_insert
            BEFORE INSERT ON research_evidence_claim_support_assessment
            FOR EACH ROW
            EXECUTE FUNCTION research_evidence_prepare_claim_support_insert();
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_recsa_no_mutation'
          AND tgrelid =
              'research_evidence_claim_support_assessment'::regclass
          AND NOT tgisinternal
    ) THEN
        CREATE TRIGGER trg_recsa_no_mutation
            BEFORE UPDATE OR DELETE
            ON research_evidence_claim_support_assessment
            FOR EACH ROW EXECUTE FUNCTION slicea_reject_mutation();
    END IF;
END $$;

ALTER TABLE research_evidence_claim_support_assessment
    ENABLE ALWAYS TRIGGER trg_recsa_prepare_insert;

COMMIT;
