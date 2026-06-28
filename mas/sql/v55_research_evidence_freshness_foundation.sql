-- v55 Research-Evidence Item Freshness/Drift Foundation (R1.4)
-- Additive, append-only assessments for existing v53 candidate-fact items.
-- This migration does not evaluate or alter availability, retention, lineage,
-- review approval, withdrawal, or any downstream-use contract.
--
-- Apply manually AFTER sql/v47_evidence_snapshot_foundation.sql,
-- sql/v51_research_evidence_sidecar_foundation.sql,
-- sql/v52_research_evidence_audit_integrity.sql,
-- sql/v53_research_evidence_intake_foundation.sql, and
-- sql/v54_research_evidence_review_foundation.sql:
--   psql -U workflow -d workflow_v4 -v ON_ERROR_STOP=1 \
--     -f sql/v55_research_evidence_freshness_foundation.sql

BEGIN;

-- Validate the concrete prior-wave contracts consumed by v55, then classify
-- v55 as absent, complete, or partial/divergent. No prior object is repaired.
DO $$
DECLARE
    v_parent_tables integer;
    v_v55_tables integer;
    v_missing text;
    v_reject_oid oid;
    v_prepare_oid oid;
    v_column_count integer;
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
          'research_evidence_intake', 'research_evidence_intake_item',
          'research_evidence_intake_item_review_decision',
          'research_evidence_item_review_sequence_allocator'
      ]);
    IF v_parent_tables <> 14 THEN
        RAISE EXCEPTION
            'v55 requires complete v47-v54 parent tables, found % of 14',
            v_parent_tables
            USING ERRCODE = 'invalid_schema_definition';
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
        RAISE EXCEPTION 'v55 requires canonical append-only guard'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- These exact keys and links make the server-derived evidence snapshot
    -- project-consistent and bind every assessment to an existing intake item.
    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('uq_source_blob_id_project'::text, 'u'::"char",
         'source_blob'::text, NULL::text, NULL::"char"),
        ('uq_source_snapshot_id_project', 'u', 'source_snapshot', NULL, NULL),
        ('uq_cfr_id_project', 'u', 'candidate_fact_revision', NULL, NULL),
        ('uq_rfmr_id_project_fact', 'u',
         'research_fact_metadata_revision', NULL, NULL),
        ('uq_reii_id_project', 'u', 'research_evidence_intake_item', NULL, NULL),
        ('fk_snapshot_blob_project', 'f', 'source_snapshot',
         'source_blob', 'r'),
        ('fk_cfr_snapshot_project', 'f', 'candidate_fact_revision',
         'source_snapshot', 'r'),
        ('fk_rfmr_fact_project', 'f', 'research_fact_metadata_revision',
         'candidate_fact_revision', 'r'),
        ('fk_reii_intake_snapshot', 'f', 'research_evidence_intake_item',
         'research_evidence_intake', 'r'),
        ('fk_reii_fact_project', 'f', 'research_evidence_intake_item',
         'candidate_fact_revision', 'r'),
        ('fk_reii_fact_metadata_fact', 'f',
         'research_evidence_intake_item',
         'research_fact_metadata_revision', 'r')
    ) AS expected(name, kind, local_table, parent_table, delete_action)
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        WHERE con.connamespace = current_schema()::regnamespace
          AND con.conname = expected.name
          AND con.contype = expected.kind
          AND con.conrelid = expected.local_table::regclass
          AND (
              expected.parent_table IS NULL
              OR (
                  con.confrelid = expected.parent_table::regclass
                  AND con.confdeltype = expected.delete_action
                  AND con.convalidated
              )
          )
    );
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v55 requires complete item/evidence parent graph: %',
            v_missing USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT count(*) INTO v_v55_tables
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'r'
      AND n.nspname = current_schema()
      AND c.relname = ANY (ARRAY[
          'research_evidence_intake_item_freshness_assessment',
          'research_evidence_item_freshness_sequence_allocator'
      ]);

    SELECT p.oid INTO v_prepare_oid
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = current_schema()
      AND p.proname = 'research_evidence_prepare_freshness_assessment_insert'
      AND p.pronargs = 0
      AND p.prorettype = 'trigger'::regtype;

    IF v_v55_tables = 0
       AND v_prepare_oid IS NULL
       AND NOT EXISTS (
           SELECT 1
           FROM pg_trigger t
           JOIN pg_class c ON c.oid = t.tgrelid
           JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = current_schema()
             AND t.tgname IN (
                 'trg_reifa_prepare_insert', 'trg_reifa_no_mutation'
             )
       ) THEN
        RETURN;
    END IF;

    IF v_v55_tables <> 2 OR v_prepare_oid IS NULL THEN
        RAISE EXCEPTION
            'v55 contract violation: partial/divergent freshness foundation'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT count(*) INTO v_column_count
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name =
          'research_evidence_intake_item_freshness_assessment';
    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('id'::text, 'uuid'::text, 'NO'::text),
        ('project_id', 'uuid', 'NO'),
        ('research_evidence_intake_item_id', 'uuid', 'NO'),
        ('request_id', 'text', 'NO'),
        ('policy_identifier', 'text', 'NO'),
        ('policy_version', 'text', 'NO'),
        ('policy_parameters_json', 'jsonb', 'NO'),
        ('policy_fingerprint', 'text', 'NO'),
        ('evaluator_version', 'text', 'NO'),
        ('basis_timestamp', 'timestamp with time zone', 'NO'),
        ('fresh_through', 'timestamp with time zone', 'NO'),
        ('comparison_research_evidence_intake_item_id', 'uuid', 'YES'),
        ('drift_status', 'text', 'NO'),
        ('drift_reason', 'text', 'NO'),
        ('assessed_by', 'text', 'NO'),
        ('assessment_sequence', 'integer', 'NO'),
        ('supersedes_assessment_id', 'uuid', 'YES'),
        ('source_snapshot_id', 'uuid', 'NO'),
        ('source_blob_id', 'uuid', 'NO'),
        ('candidate_fact_revision_id', 'uuid', 'NO'),
        ('fact_metadata_revision_id', 'uuid', 'NO'),
        ('linked_hash_algorithm', 'text', 'NO'),
        ('linked_content_hash', 'text', 'NO'),
        ('comparison_source_snapshot_id', 'uuid', 'YES'),
        ('comparison_source_blob_id', 'uuid', 'YES'),
        ('comparison_candidate_fact_revision_id', 'uuid', 'YES'),
        ('comparison_fact_metadata_revision_id', 'uuid', 'YES'),
        ('comparison_hash_algorithm', 'text', 'YES'),
        ('comparison_content_hash', 'text', 'YES'),
        ('content_change_detected', 'boolean', 'YES'),
        ('assessed_at', 'timestamp with time zone', 'NO')
    ) AS expected(name, data_type, nullable)
    LEFT JOIN information_schema.columns column_info
      ON column_info.table_schema = current_schema()
     AND column_info.table_name =
         'research_evidence_intake_item_freshness_assessment'
     AND column_info.column_name = expected.name
    WHERE column_info.column_name IS NULL
       OR column_info.data_type <> expected.data_type
       OR column_info.is_nullable <> expected.nullable;
    IF v_column_count <> 31 OR v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v55 contract violation: divergent assessment columns %',
            coalesce(v_missing, '(unexpected column count)')
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT count(*) INTO v_column_count
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name =
          'research_evidence_item_freshness_sequence_allocator';
    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('project_id'::text, 'uuid'::text, 'NO'::text),
        ('research_evidence_intake_item_id', 'uuid', 'NO'),
        ('last_sequence', 'integer', 'NO')
    ) AS expected(name, data_type, nullable)
    LEFT JOIN information_schema.columns column_info
      ON column_info.table_schema = current_schema()
     AND column_info.table_name =
         'research_evidence_item_freshness_sequence_allocator'
     AND column_info.column_name = expected.name
    WHERE column_info.column_name IS NULL
       OR column_info.data_type <> expected.data_type
       OR column_info.is_nullable <> expected.nullable;
    IF v_column_count <> 3 OR v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v55 contract violation: divergent allocator columns %',
            coalesce(v_missing, '(unexpected column count)')
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name =
              'research_evidence_intake_item_freshness_assessment'
          AND column_name IN (
              'assessment_sequence', 'supersedes_assessment_id',
              'source_snapshot_id', 'source_blob_id',
              'candidate_fact_revision_id', 'fact_metadata_revision_id',
              'linked_hash_algorithm', 'linked_content_hash',
              'comparison_source_snapshot_id', 'comparison_source_blob_id',
              'comparison_candidate_fact_revision_id',
              'comparison_fact_metadata_revision_id',
              'comparison_hash_algorithm', 'comparison_content_hash',
              'content_change_detected', 'assessed_at'
          )
          AND column_default IS NOT NULL
    ) THEN
        RAISE EXCEPTION
            'v55 contract violation: server-owned fields have defaults'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_attribute a
        JOIN pg_attrdef d
          ON d.adrelid = a.attrelid
         AND d.adnum = a.attnum
        WHERE a.attrelid =
              'research_evidence_intake_item_freshness_assessment'::regclass
          AND a.attname = 'id'
          AND lower(
              regexp_replace(
                  pg_get_expr(d.adbin, d.adrelid, true),
                  '[[:space:]]+', '', 'g'
              )
          ) = 'gen_random_uuid()'
    ) THEN
        RAISE EXCEPTION 'v55 contract violation: divergent assessment id default'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('research_evidence_intake_item_freshness_assessment_pkey'::text,
         'research_evidence_intake_item_freshness_assessment'::text, 'p'::"char"),
        ('uq_reifa_id_project_item',
         'research_evidence_intake_item_freshness_assessment', 'u'),
        ('uq_reifa_item_sequence',
         'research_evidence_intake_item_freshness_assessment', 'u'),
        ('uq_reifa_item_request',
         'research_evidence_intake_item_freshness_assessment', 'u'),
        ('uq_reifa_supersedes_once',
         'research_evidence_intake_item_freshness_assessment', 'u'),
        ('fk_reifa_project',
         'research_evidence_intake_item_freshness_assessment', 'f'),
        ('fk_reifa_item_project',
         'research_evidence_intake_item_freshness_assessment', 'f'),
        ('fk_reifa_supersedes_same_item',
         'research_evidence_intake_item_freshness_assessment', 'f'),
        ('fk_reifa_snapshot_project',
         'research_evidence_intake_item_freshness_assessment', 'f'),
        ('fk_reifa_blob_project',
         'research_evidence_intake_item_freshness_assessment', 'f'),
        ('fk_reifa_fact_project',
         'research_evidence_intake_item_freshness_assessment', 'f'),
        ('fk_reifa_fact_metadata_fact',
         'research_evidence_intake_item_freshness_assessment', 'f'),
        ('fk_reifa_comparison_item_project',
         'research_evidence_intake_item_freshness_assessment', 'f'),
        ('fk_reifa_comparison_snapshot_project',
         'research_evidence_intake_item_freshness_assessment', 'f'),
        ('fk_reifa_comparison_blob_project',
         'research_evidence_intake_item_freshness_assessment', 'f'),
        ('fk_reifa_comparison_fact_project',
         'research_evidence_intake_item_freshness_assessment', 'f'),
        ('fk_reifa_comparison_fact_metadata_fact',
         'research_evidence_intake_item_freshness_assessment', 'f'),
        ('ck_reifa_sequence_positive',
         'research_evidence_intake_item_freshness_assessment', 'c'),
        ('ck_reifa_policy_parameters_object',
         'research_evidence_intake_item_freshness_assessment', 'c'),
        ('ck_reifa_policy_provenance',
         'research_evidence_intake_item_freshness_assessment', 'c'),
        ('ck_reifa_freshness_window',
         'research_evidence_intake_item_freshness_assessment', 'c'),
        ('ck_reifa_distinct_comparison',
         'research_evidence_intake_item_freshness_assessment', 'c'),
        ('ck_reifa_drift_status',
         'research_evidence_intake_item_freshness_assessment', 'c'),
        ('ck_reifa_comparison_shape',
         'research_evidence_intake_item_freshness_assessment', 'c'),
        ('ck_reifa_request_nonblank',
         'research_evidence_intake_item_freshness_assessment', 'c'),
        ('ck_reifa_policy_identifier_nonblank',
         'research_evidence_intake_item_freshness_assessment', 'c'),
        ('ck_reifa_policy_version_nonblank',
         'research_evidence_intake_item_freshness_assessment', 'c'),
        ('ck_reifa_evaluator_version_nonblank',
         'research_evidence_intake_item_freshness_assessment', 'c'),
        ('ck_reifa_drift_reason_nonblank',
         'research_evidence_intake_item_freshness_assessment', 'c'),
        ('ck_reifa_assessed_by_nonblank',
         'research_evidence_intake_item_freshness_assessment', 'c'),
        ('ck_reifa_linked_hash_algorithm_nonblank',
         'research_evidence_intake_item_freshness_assessment', 'c'),
        ('ck_reifa_linked_content_hash_nonblank',
         'research_evidence_intake_item_freshness_assessment', 'c'),
        ('research_evidence_item_freshness_sequence_allocator_pkey',
         'research_evidence_item_freshness_sequence_allocator', 'p'),
        ('fk_reifsa_project',
         'research_evidence_item_freshness_sequence_allocator', 'f'),
        ('fk_reifsa_item_project',
         'research_evidence_item_freshness_sequence_allocator', 'f'),
        ('ck_reifsa_last_sequence',
         'research_evidence_item_freshness_sequence_allocator', 'c')
    ) AS expected(name, table_name, kind)
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
        RAISE EXCEPTION 'v55 contract violation: missing constraints %',
            v_missing USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('uq_reifa_id_project_item'::text,
         ARRAY[
             'id', 'project_id', 'research_evidence_intake_item_id'
         ]::text[]),
        ('uq_reifa_item_sequence',
         ARRAY[
             'project_id', 'research_evidence_intake_item_id',
             'assessment_sequence'
         ]),
        ('uq_reifa_item_request',
         ARRAY[
             'project_id', 'research_evidence_intake_item_id', 'request_id'
         ]),
        ('uq_reifa_supersedes_once', ARRAY['supersedes_assessment_id'])
    ) AS expected(name, columns)
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        WHERE con.connamespace = current_schema()::regnamespace
          AND con.conname = expected.name
          AND con.contype = 'u'
          AND con.conrelid =
              'research_evidence_intake_item_freshness_assessment'::regclass
          AND (
              SELECT array_agg(a.attname::text ORDER BY u.ordinality)
              FROM unnest(con.conkey) WITH ORDINALITY u(attnum, ordinality)
              JOIN pg_attribute a
                ON a.attrelid = con.conrelid AND a.attnum = u.attnum
          ) = expected.columns
    );
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v55 contract violation: divergent unique keys %',
            v_missing USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('fk_reifa_project'::text,
         'research_evidence_intake_item_freshness_assessment'::text,
         'projects'::text, ARRAY['project_id']::text[], ARRAY['id']::text[]),
        ('fk_reifa_item_project',
         'research_evidence_intake_item_freshness_assessment',
         'research_evidence_intake_item',
         ARRAY['research_evidence_intake_item_id', 'project_id'],
         ARRAY['id', 'project_id']),
        ('fk_reifa_supersedes_same_item',
         'research_evidence_intake_item_freshness_assessment',
         'research_evidence_intake_item_freshness_assessment',
         ARRAY[
             'supersedes_assessment_id', 'project_id',
             'research_evidence_intake_item_id'
         ],
         ARRAY['id', 'project_id', 'research_evidence_intake_item_id']),
        ('fk_reifa_snapshot_project',
         'research_evidence_intake_item_freshness_assessment',
         'source_snapshot', ARRAY['source_snapshot_id', 'project_id'],
         ARRAY['id', 'project_id']),
        ('fk_reifa_blob_project',
         'research_evidence_intake_item_freshness_assessment',
         'source_blob', ARRAY['source_blob_id', 'project_id'],
         ARRAY['id', 'project_id']),
        ('fk_reifa_fact_project',
         'research_evidence_intake_item_freshness_assessment',
         'candidate_fact_revision',
         ARRAY['candidate_fact_revision_id', 'project_id'],
         ARRAY['id', 'project_id']),
        ('fk_reifa_fact_metadata_fact',
         'research_evidence_intake_item_freshness_assessment',
         'research_fact_metadata_revision',
         ARRAY[
             'fact_metadata_revision_id', 'project_id',
             'candidate_fact_revision_id'
         ],
         ARRAY['id', 'project_id', 'candidate_fact_revision_id']),
        ('fk_reifa_comparison_item_project',
         'research_evidence_intake_item_freshness_assessment',
         'research_evidence_intake_item',
         ARRAY[
             'comparison_research_evidence_intake_item_id', 'project_id'
         ],
         ARRAY['id', 'project_id']),
        ('fk_reifa_comparison_snapshot_project',
         'research_evidence_intake_item_freshness_assessment',
         'source_snapshot',
         ARRAY['comparison_source_snapshot_id', 'project_id'],
         ARRAY['id', 'project_id']),
        ('fk_reifa_comparison_blob_project',
         'research_evidence_intake_item_freshness_assessment',
         'source_blob',
         ARRAY['comparison_source_blob_id', 'project_id'],
         ARRAY['id', 'project_id']),
        ('fk_reifa_comparison_fact_project',
         'research_evidence_intake_item_freshness_assessment',
         'candidate_fact_revision',
         ARRAY['comparison_candidate_fact_revision_id', 'project_id'],
         ARRAY['id', 'project_id']),
        ('fk_reifa_comparison_fact_metadata_fact',
         'research_evidence_intake_item_freshness_assessment',
         'research_fact_metadata_revision',
         ARRAY[
             'comparison_fact_metadata_revision_id', 'project_id',
             'comparison_candidate_fact_revision_id'
         ],
         ARRAY['id', 'project_id', 'candidate_fact_revision_id']),
        ('fk_reifsa_project',
         'research_evidence_item_freshness_sequence_allocator',
         'projects', ARRAY['project_id'], ARRAY['id']),
        ('fk_reifsa_item_project',
         'research_evidence_item_freshness_sequence_allocator',
         'research_evidence_intake_item',
         ARRAY['research_evidence_intake_item_id', 'project_id'],
         ARRAY['id', 'project_id'])
    ) AS expected(
        name, local_table, parent_table, local_columns, parent_columns
    )
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
        RAISE EXCEPTION 'v55 contract violation: divergent foreign keys %',
            v_missing USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- Check definitions are compared after the same deterministic
    -- normalization used by v54: case/whitespace, redundant parentheses, and
    -- PostgreSQL's implicit text casts do not weaken exact expression checks.
    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('ck_reifa_sequence_positive'::text,
         'research_evidence_intake_item_freshness_assessment'::text,
         'assessment_sequence>=1'::text),
        ('ck_reifa_policy_parameters_object',
         'research_evidence_intake_item_freshness_assessment',
         'jsonb_typeofpolicy_parameters_json=''object'''),
        ('ck_reifa_policy_provenance',
         'research_evidence_intake_item_freshness_assessment',
         'policy_parameters_json<>''{}''::jsonbor'
         || 'policy_fingerprint!~''^[[:space:]]*$'''),
        ('ck_reifa_freshness_window',
         'research_evidence_intake_item_freshness_assessment',
         'fresh_through>=basis_timestamp'),
        ('ck_reifa_distinct_comparison',
         'research_evidence_intake_item_freshness_assessment',
         'comparison_research_evidence_intake_item_idisnullor'
         || 'comparison_research_evidence_intake_item_id<>'
         || 'research_evidence_intake_item_id'),
        ('ck_reifa_drift_status',
         'research_evidence_intake_item_freshness_assessment',
         'drift_status=anyarray[''not_assessed'',''no_material_drift'','
         || '''material_drift'',''indeterminate'']'),
        ('ck_reifa_comparison_shape',
         'research_evidence_intake_item_freshness_assessment',
         'comparison_research_evidence_intake_item_idisnulland'
         || 'comparison_source_snapshot_idisnulland'
         || 'comparison_source_blob_idisnulland'
         || 'comparison_candidate_fact_revision_idisnulland'
         || 'comparison_fact_metadata_revision_idisnulland'
         || 'comparison_hash_algorithmisnulland'
         || 'comparison_content_hashisnulland'
         || 'content_change_detectedisnullor'
         || 'comparison_research_evidence_intake_item_idisnotnulland'
         || 'comparison_source_snapshot_idisnotnulland'
         || 'comparison_source_blob_idisnotnulland'
         || 'comparison_candidate_fact_revision_idisnotnulland'
         || 'comparison_fact_metadata_revision_idisnotnulland'
         || 'comparison_hash_algorithmisnotnulland'
         || 'comparison_content_hashisnotnulland'
         || 'content_change_detectedisnotnull'),
        ('ck_reifa_request_nonblank',
         'research_evidence_intake_item_freshness_assessment',
         'request_id!~''^[[:space:]]*$'''),
        ('ck_reifa_policy_identifier_nonblank',
         'research_evidence_intake_item_freshness_assessment',
         'policy_identifier!~''^[[:space:]]*$'''),
        ('ck_reifa_policy_version_nonblank',
         'research_evidence_intake_item_freshness_assessment',
         'policy_version!~''^[[:space:]]*$'''),
        ('ck_reifa_evaluator_version_nonblank',
         'research_evidence_intake_item_freshness_assessment',
         'evaluator_version!~''^[[:space:]]*$'''),
        ('ck_reifa_drift_reason_nonblank',
         'research_evidence_intake_item_freshness_assessment',
         'drift_reason!~''^[[:space:]]*$'''),
        ('ck_reifa_assessed_by_nonblank',
         'research_evidence_intake_item_freshness_assessment',
         'assessed_by!~''^[[:space:]]*$'''),
        ('ck_reifa_linked_hash_algorithm_nonblank',
         'research_evidence_intake_item_freshness_assessment',
         'linked_hash_algorithm!~''^[[:space:]]*$'''),
        ('ck_reifa_linked_content_hash_nonblank',
         'research_evidence_intake_item_freshness_assessment',
         'linked_content_hash!~''^[[:space:]]*$'''),
        ('ck_reifsa_last_sequence',
         'research_evidence_item_freshness_sequence_allocator',
         'last_sequence>=0')
    ) AS expected(name, table_name, normalized_expression)
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
            'v55 contract violation: divergent freshness check constraints %',
            v_missing USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT p.oid INTO v_prepare_oid
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = current_schema()
      AND p.proname =
          'research_evidence_prepare_freshness_assessment_insert'
      AND p.pronargs = 0
      AND p.prorettype = 'trigger'::regtype
      AND p.prosecdef
      AND p.proconfig = ARRAY['search_path=pg_catalog']
      AND md5(regexp_replace(p.prosrc, '[[:space:]]+', '', 'g')) =
          '00e069880aaded18462df13f12f8d969'
      AND NOT EXISTS (
          SELECT 1
          FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) acl
          WHERE acl.grantee = 0
            AND acl.privilege_type = 'EXECUTE'
      );
    IF v_prepare_oid IS NULL THEN
        RAISE EXCEPTION
            'v55 contract violation: divergent freshness prepare function'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_indexes
        WHERE schemaname = current_schema()
          AND tablename =
              'research_evidence_intake_item_freshness_assessment'
          AND indexname = 'idx_reifa_item_sequence'
          AND indexdef ILIKE
              '%(project_id, research_evidence_intake_item_id, assessment_sequence)%'
    ) OR NOT EXISTS (
        SELECT 1
        FROM pg_indexes
        WHERE schemaname = current_schema()
          AND tablename =
              'research_evidence_intake_item_freshness_assessment'
          AND indexname = 'idx_reifa_comparison_item'
          AND indexdef ILIKE
              '%(project_id, comparison_research_evidence_intake_item_id)%'
          AND indexdef ILIKE
              '%WHERE (comparison_research_evidence_intake_item_id IS NOT NULL)%'
    ) THEN
        RAISE EXCEPTION 'v55 contract violation: divergent freshness indexes'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(tg_name, ', ' ORDER BY tg_name) INTO v_missing
    FROM (VALUES
        ('trg_reifa_prepare_insert'::text, v_prepare_oid, 'A'::"char", 7),
        ('trg_reifa_no_mutation', v_reject_oid, 'O', 27)
    ) AS expected(tg_name, function_oid, enabled, required_bits)
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_trigger t
        WHERE t.tgname = expected.tg_name
          AND t.tgrelid =
              'research_evidence_intake_item_freshness_assessment'::regclass
          AND NOT t.tgisinternal
          AND t.tgfoid = expected.function_oid
          AND t.tgenabled = expected.enabled
          AND (t.tgtype & expected.required_bits) = expected.required_bits
    );
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v55 contract violation: divergent triggers %',
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
              'research_evidence_item_freshness_sequence_allocator'
          AND acl.grantee = 0
    ) THEN
        RAISE EXCEPTION 'v55 contract violation: allocator has PUBLIC privileges'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM (
            SELECT project_id, research_evidence_intake_item_id,
                   count(*)::integer AS row_count,
                   min(assessment_sequence) AS min_sequence,
                   max(assessment_sequence) AS last_sequence
            FROM research_evidence_intake_item_freshness_assessment
            GROUP BY project_id, research_evidence_intake_item_id
        ) history
        FULL JOIN research_evidence_item_freshness_sequence_allocator allocator
          USING (project_id, research_evidence_intake_item_id)
        WHERE history.row_count IS NULL
           OR allocator.last_sequence IS NULL
           OR history.row_count <> allocator.last_sequence
           OR history.min_sequence <> 1
           OR history.last_sequence <> allocator.last_sequence
    ) THEN
        RAISE EXCEPTION 'v55 contract violation: allocator diverges from history'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM research_evidence_intake_item_freshness_assessment assessment
        LEFT JOIN research_evidence_intake_item_freshness_assessment predecessor
          ON predecessor.id = assessment.supersedes_assessment_id
         AND predecessor.project_id = assessment.project_id
         AND predecessor.research_evidence_intake_item_id =
             assessment.research_evidence_intake_item_id
         AND predecessor.assessment_sequence =
             assessment.assessment_sequence - 1
        WHERE (assessment.assessment_sequence = 1
               AND assessment.supersedes_assessment_id IS NOT NULL)
           OR (assessment.assessment_sequence > 1 AND predecessor.id IS NULL)
    ) THEN
        RAISE EXCEPTION 'v55 contract violation: malformed assessment chain'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM research_evidence_intake_item_freshness_assessment assessment
        JOIN research_evidence_intake_item item
          ON item.id = assessment.research_evidence_intake_item_id
         AND item.project_id = assessment.project_id
        JOIN source_snapshot snapshot
          ON snapshot.id = item.source_snapshot_id
         AND snapshot.project_id = item.project_id
        JOIN source_blob blob
          ON blob.id = snapshot.source_blob_id
         AND blob.project_id = snapshot.project_id
        LEFT JOIN research_evidence_intake_item comparison_item
          ON comparison_item.id =
             assessment.comparison_research_evidence_intake_item_id
         AND comparison_item.project_id = assessment.project_id
        LEFT JOIN source_snapshot comparison_snapshot
          ON comparison_snapshot.id = comparison_item.source_snapshot_id
         AND comparison_snapshot.project_id = comparison_item.project_id
        LEFT JOIN source_blob comparison_blob
          ON comparison_blob.id = comparison_snapshot.source_blob_id
         AND comparison_blob.project_id = comparison_snapshot.project_id
        WHERE item.item_kind <> 'candidate_fact'
           OR assessment.source_snapshot_id IS DISTINCT FROM item.source_snapshot_id
           OR assessment.source_blob_id IS DISTINCT FROM snapshot.source_blob_id
           OR assessment.candidate_fact_revision_id IS DISTINCT FROM
              item.candidate_fact_revision_id
           OR assessment.fact_metadata_revision_id IS DISTINCT FROM
              item.fact_metadata_revision_id
           OR assessment.linked_hash_algorithm IS DISTINCT FROM
              blob.hash_algorithm
           OR assessment.linked_content_hash IS DISTINCT FROM blob.content_hash
           OR (
               assessment.comparison_research_evidence_intake_item_id IS NOT NULL
               AND (
                   comparison_item.id IS NULL
                   OR comparison_item.item_kind <> 'candidate_fact'
                   OR assessment.comparison_source_snapshot_id IS DISTINCT FROM
                      comparison_item.source_snapshot_id
                   OR assessment.comparison_source_blob_id IS DISTINCT FROM
                      comparison_snapshot.source_blob_id
                   OR assessment.comparison_candidate_fact_revision_id
                      IS DISTINCT FROM
                      comparison_item.candidate_fact_revision_id
                   OR assessment.comparison_fact_metadata_revision_id
                      IS DISTINCT FROM comparison_item.fact_metadata_revision_id
                   OR assessment.comparison_hash_algorithm IS DISTINCT FROM
                      comparison_blob.hash_algorithm
                   OR assessment.comparison_content_hash IS DISTINCT FROM
                      comparison_blob.content_hash
                   OR assessment.content_change_detected IS DISTINCT FROM (
                       assessment.linked_hash_algorithm <>
                           comparison_blob.hash_algorithm
                       OR assessment.linked_content_hash <>
                           comparison_blob.content_hash
                   )
               )
           )
    ) THEN
        RAISE EXCEPTION
            'v55 contract violation: linked evidence diverges from intake graph'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS
research_evidence_intake_item_freshness_assessment (
    id                                      UUID NOT NULL DEFAULT gen_random_uuid(),
    project_id                              UUID NOT NULL,
    research_evidence_intake_item_id        UUID NOT NULL,
    request_id                              TEXT NOT NULL,
    policy_identifier                       TEXT NOT NULL,
    policy_version                          TEXT NOT NULL,
    policy_parameters_json                  JSONB NOT NULL,
    policy_fingerprint                      TEXT NOT NULL DEFAULT '',
    evaluator_version                       TEXT NOT NULL,
    basis_timestamp                         TIMESTAMPTZ NOT NULL,
    fresh_through                           TIMESTAMPTZ NOT NULL,
    comparison_research_evidence_intake_item_id UUID,
    drift_status                            TEXT NOT NULL,
    drift_reason                            TEXT NOT NULL,
    assessed_by                             TEXT NOT NULL,
    assessment_sequence                     INTEGER NOT NULL,
    supersedes_assessment_id                UUID,
    source_snapshot_id                      UUID NOT NULL,
    source_blob_id                          UUID NOT NULL,
    candidate_fact_revision_id              UUID NOT NULL,
    fact_metadata_revision_id               UUID NOT NULL,
    linked_hash_algorithm                   TEXT NOT NULL,
    linked_content_hash                     TEXT NOT NULL,
    comparison_source_snapshot_id           UUID,
    comparison_source_blob_id               UUID,
    comparison_candidate_fact_revision_id   UUID,
    comparison_fact_metadata_revision_id    UUID,
    comparison_hash_algorithm               TEXT,
    comparison_content_hash                 TEXT,
    content_change_detected                 BOOLEAN,
    assessed_at                             TIMESTAMPTZ NOT NULL,
    CONSTRAINT research_evidence_intake_item_freshness_assessment_pkey
        PRIMARY KEY (id),
    CONSTRAINT uq_reifa_id_project_item
        UNIQUE (id, project_id, research_evidence_intake_item_id),
    CONSTRAINT uq_reifa_item_sequence
        UNIQUE (
            project_id, research_evidence_intake_item_id, assessment_sequence
        ),
    CONSTRAINT uq_reifa_item_request
        UNIQUE (project_id, research_evidence_intake_item_id, request_id),
    CONSTRAINT uq_reifa_supersedes_once UNIQUE (supersedes_assessment_id),
    CONSTRAINT fk_reifa_project
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
    CONSTRAINT fk_reifa_item_project
        FOREIGN KEY (research_evidence_intake_item_id, project_id)
        REFERENCES research_evidence_intake_item(id, project_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_reifa_supersedes_same_item
        FOREIGN KEY (
            supersedes_assessment_id, project_id,
            research_evidence_intake_item_id
        )
        REFERENCES research_evidence_intake_item_freshness_assessment(
            id, project_id, research_evidence_intake_item_id
        ) ON DELETE RESTRICT,
    CONSTRAINT fk_reifa_snapshot_project
        FOREIGN KEY (source_snapshot_id, project_id)
        REFERENCES source_snapshot(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT fk_reifa_blob_project
        FOREIGN KEY (source_blob_id, project_id)
        REFERENCES source_blob(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT fk_reifa_fact_project
        FOREIGN KEY (candidate_fact_revision_id, project_id)
        REFERENCES candidate_fact_revision(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT fk_reifa_fact_metadata_fact
        FOREIGN KEY (
            fact_metadata_revision_id, project_id,
            candidate_fact_revision_id
        )
        REFERENCES research_fact_metadata_revision(
            id, project_id, candidate_fact_revision_id
        ) ON DELETE RESTRICT,
    CONSTRAINT fk_reifa_comparison_item_project
        FOREIGN KEY (
            comparison_research_evidence_intake_item_id, project_id
        )
        REFERENCES research_evidence_intake_item(id, project_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_reifa_comparison_snapshot_project
        FOREIGN KEY (comparison_source_snapshot_id, project_id)
        REFERENCES source_snapshot(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT fk_reifa_comparison_blob_project
        FOREIGN KEY (comparison_source_blob_id, project_id)
        REFERENCES source_blob(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT fk_reifa_comparison_fact_project
        FOREIGN KEY (comparison_candidate_fact_revision_id, project_id)
        REFERENCES candidate_fact_revision(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT fk_reifa_comparison_fact_metadata_fact
        FOREIGN KEY (
            comparison_fact_metadata_revision_id, project_id,
            comparison_candidate_fact_revision_id
        )
        REFERENCES research_fact_metadata_revision(
            id, project_id, candidate_fact_revision_id
        ) ON DELETE RESTRICT,
    CONSTRAINT ck_reifa_sequence_positive CHECK (assessment_sequence >= 1),
    CONSTRAINT ck_reifa_policy_parameters_object CHECK (
        jsonb_typeof(policy_parameters_json) = 'object'
    ),
    CONSTRAINT ck_reifa_policy_provenance CHECK (
        policy_parameters_json <> '{}'::jsonb
        OR policy_fingerprint !~ '^[[:space:]]*$'
    ),
    CONSTRAINT ck_reifa_freshness_window CHECK (
        fresh_through >= basis_timestamp
    ),
    CONSTRAINT ck_reifa_distinct_comparison CHECK (
        comparison_research_evidence_intake_item_id IS NULL
        OR comparison_research_evidence_intake_item_id <>
           research_evidence_intake_item_id
    ),
    CONSTRAINT ck_reifa_drift_status CHECK (
        drift_status IN (
            'not_assessed', 'no_material_drift',
            'material_drift', 'indeterminate'
        )
    ),
    CONSTRAINT ck_reifa_comparison_shape CHECK (
        (
            comparison_research_evidence_intake_item_id IS NULL
            AND comparison_source_snapshot_id IS NULL
            AND comparison_source_blob_id IS NULL
            AND comparison_candidate_fact_revision_id IS NULL
            AND comparison_fact_metadata_revision_id IS NULL
            AND comparison_hash_algorithm IS NULL
            AND comparison_content_hash IS NULL
            AND content_change_detected IS NULL
        )
        OR
        (
            comparison_research_evidence_intake_item_id IS NOT NULL
            AND comparison_source_snapshot_id IS NOT NULL
            AND comparison_source_blob_id IS NOT NULL
            AND comparison_candidate_fact_revision_id IS NOT NULL
            AND comparison_fact_metadata_revision_id IS NOT NULL
            AND comparison_hash_algorithm IS NOT NULL
            AND comparison_content_hash IS NOT NULL
            AND content_change_detected IS NOT NULL
        )
    ),
    CONSTRAINT ck_reifa_request_nonblank CHECK (
        request_id !~ '^[[:space:]]*$'
    ),
    CONSTRAINT ck_reifa_policy_identifier_nonblank CHECK (
        policy_identifier !~ '^[[:space:]]*$'
    ),
    CONSTRAINT ck_reifa_policy_version_nonblank CHECK (
        policy_version !~ '^[[:space:]]*$'
    ),
    CONSTRAINT ck_reifa_evaluator_version_nonblank CHECK (
        evaluator_version !~ '^[[:space:]]*$'
    ),
    CONSTRAINT ck_reifa_drift_reason_nonblank CHECK (
        drift_reason !~ '^[[:space:]]*$'
    ),
    CONSTRAINT ck_reifa_assessed_by_nonblank CHECK (
        assessed_by !~ '^[[:space:]]*$'
    ),
    CONSTRAINT ck_reifa_linked_hash_algorithm_nonblank CHECK (
        linked_hash_algorithm !~ '^[[:space:]]*$'
    ),
    CONSTRAINT ck_reifa_linked_content_hash_nonblank CHECK (
        linked_content_hash !~ '^[[:space:]]*$'
    )
);

CREATE TABLE IF NOT EXISTS
research_evidence_item_freshness_sequence_allocator (
    project_id                       UUID NOT NULL,
    research_evidence_intake_item_id UUID NOT NULL,
    last_sequence                    INTEGER NOT NULL,
    CONSTRAINT research_evidence_item_freshness_sequence_allocator_pkey
        PRIMARY KEY (project_id, research_evidence_intake_item_id),
    CONSTRAINT fk_reifsa_project
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
    CONSTRAINT fk_reifsa_item_project
        FOREIGN KEY (research_evidence_intake_item_id, project_id)
        REFERENCES research_evidence_intake_item(id, project_id)
        ON DELETE RESTRICT,
    CONSTRAINT ck_reifsa_last_sequence CHECK (last_sequence >= 0)
);

CREATE INDEX IF NOT EXISTS idx_reifa_item_sequence
    ON research_evidence_intake_item_freshness_assessment(
        project_id, research_evidence_intake_item_id, assessment_sequence
    );

CREATE INDEX IF NOT EXISTS idx_reifa_comparison_item
    ON research_evidence_intake_item_freshness_assessment(
        project_id, comparison_research_evidence_intake_item_id
    )
    WHERE comparison_research_evidence_intake_item_id IS NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = current_schema()
          AND p.proname =
              'research_evidence_prepare_freshness_assessment_insert'
          AND p.pronargs = 0
    ) THEN
        EXECUTE $create_function$
            CREATE FUNCTION
            research_evidence_prepare_freshness_assessment_insert()
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
                v_is_claim boolean;
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
                IF NEW.assessed_at IS NOT NULL THEN
                    RAISE EXCEPTION 'assessed_at is server-assigned'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.source_snapshot_id IS NOT NULL
                   OR NEW.source_blob_id IS NOT NULL
                   OR NEW.candidate_fact_revision_id IS NOT NULL
                   OR NEW.fact_metadata_revision_id IS NOT NULL
                   OR NEW.linked_hash_algorithm IS NOT NULL
                   OR NEW.linked_content_hash IS NOT NULL
                   OR NEW.comparison_source_snapshot_id IS NOT NULL
                   OR NEW.comparison_source_blob_id IS NOT NULL
                   OR NEW.comparison_candidate_fact_revision_id IS NOT NULL
                   OR NEW.comparison_fact_metadata_revision_id IS NOT NULL
                   OR NEW.comparison_hash_algorithm IS NOT NULL
                   OR NEW.comparison_content_hash IS NOT NULL
                   OR NEW.content_change_detected IS NOT NULL THEN
                    RAISE EXCEPTION 'linked evidence is server-assigned'
                        USING ERRCODE = '23514';
                END IF;

                EXECUTE format(
                    'SELECT item.source_snapshot_id,
                            snapshot.source_blob_id,
                            fact.id,
                            fact_metadata.id,
                            blob.hash_algorithm,
                            blob.content_hash
                     FROM %I.research_evidence_intake_item item
                     JOIN %I.source_snapshot snapshot
                       ON snapshot.id = item.source_snapshot_id
                      AND snapshot.project_id = item.project_id
                     JOIN %I.source_blob blob
                       ON blob.id = snapshot.source_blob_id
                      AND blob.project_id = snapshot.project_id
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
                    TG_TABLE_SCHEMA, TG_TABLE_SCHEMA
                )
                INTO NEW.source_snapshot_id,
                     NEW.source_blob_id,
                     NEW.candidate_fact_revision_id,
                     NEW.fact_metadata_revision_id,
                     NEW.linked_hash_algorithm,
                     NEW.linked_content_hash
                USING NEW.research_evidence_intake_item_id, NEW.project_id;
                GET DIAGNOSTICS v_context_rows = ROW_COUNT;

                IF v_context_rows <> 1 THEN
                    EXECUTE format(
                        'SELECT EXISTS (
                            SELECT 1
                            FROM %I.research_evidence_intake_item
                            WHERE id = $1
                              AND project_id = $2
                              AND item_kind = ''claim_draft''
                        )',
                        TG_TABLE_SCHEMA
                    ) INTO v_is_claim
                    USING NEW.research_evidence_intake_item_id,
                          NEW.project_id;
                    IF v_is_claim THEN
                        RAISE EXCEPTION
                            'claim-draft intake items are not applicable'
                            USING ERRCODE = '23514';
                    END IF;
                    RAISE EXCEPTION
                        'candidate-fact intake item not found for project'
                        USING ERRCODE = '23503';
                END IF;

                IF NEW.comparison_research_evidence_intake_item_id IS NOT NULL
                THEN
                    EXECUTE format(
                        'SELECT item.source_snapshot_id,
                                snapshot.source_blob_id,
                                fact.id,
                                fact_metadata.id,
                                blob.hash_algorithm,
                                blob.content_hash
                         FROM %I.research_evidence_intake_item item
                         JOIN %I.source_snapshot snapshot
                           ON snapshot.id = item.source_snapshot_id
                          AND snapshot.project_id = item.project_id
                         JOIN %I.source_blob blob
                           ON blob.id = snapshot.source_blob_id
                          AND blob.project_id = snapshot.project_id
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
                        TG_TABLE_SCHEMA, TG_TABLE_SCHEMA
                    )
                    INTO NEW.comparison_source_snapshot_id,
                         NEW.comparison_source_blob_id,
                         NEW.comparison_candidate_fact_revision_id,
                         NEW.comparison_fact_metadata_revision_id,
                         NEW.comparison_hash_algorithm,
                         NEW.comparison_content_hash
                    USING NEW.comparison_research_evidence_intake_item_id,
                          NEW.project_id;
                    GET DIAGNOSTICS v_context_rows = ROW_COUNT;
                    IF v_context_rows <> 1 THEN
                        RAISE EXCEPTION
                            'comparison candidate-fact intake item not found'
                            USING ERRCODE = '23503';
                    END IF;
                    NEW.content_change_detected :=
                        NEW.linked_hash_algorithm <>
                            NEW.comparison_hash_algorithm
                        OR NEW.linked_content_hash <>
                            NEW.comparison_content_hash;
                ELSE
                    NEW.content_change_detected := NULL;
                END IF;

                EXECUTE format(
                    'INSERT INTO
                         %I.research_evidence_item_freshness_sequence_allocator
                         (project_id, research_evidence_intake_item_id,
                          last_sequence)
                     VALUES ($1, $2, 0)
                     ON CONFLICT (
                         project_id, research_evidence_intake_item_id
                     ) DO NOTHING',
                    TG_TABLE_SCHEMA
                ) USING NEW.project_id,
                          NEW.research_evidence_intake_item_id;

                EXECUTE format(
                    'SELECT last_sequence
                     FROM
                         %I.research_evidence_item_freshness_sequence_allocator
                     WHERE project_id = $1
                       AND research_evidence_intake_item_id = $2
                     FOR UPDATE',
                    TG_TABLE_SCHEMA
                ) INTO v_last
                USING NEW.project_id,
                      NEW.research_evidence_intake_item_id;

                EXECUTE format(
                    'SELECT count(*)::integer,
                            min(assessment_sequence),
                            max(assessment_sequence)
                     FROM
                         %I.research_evidence_intake_item_freshness_assessment
                     WHERE project_id = $1
                       AND research_evidence_intake_item_id = $2',
                    TG_TABLE_SCHEMA
                ) INTO v_count, v_min, v_max
                USING NEW.project_id,
                      NEW.research_evidence_intake_item_id;

                IF v_count <> v_last
                   OR (v_last = 0 AND (v_min IS NOT NULL OR v_max IS NOT NULL))
                   OR (v_last > 0 AND (v_min <> 1 OR v_max <> v_last)) THEN
                    RAISE EXCEPTION 'malformed freshness assessment chain'
                        USING ERRCODE = '23514';
                END IF;

                EXECUTE format(
                    'SELECT EXISTS (
                        SELECT 1
                        FROM
                            %I.research_evidence_intake_item_freshness_assessment a
                        LEFT JOIN
                            %I.research_evidence_intake_item_freshness_assessment p
                          ON p.id = a.supersedes_assessment_id
                         AND p.project_id = a.project_id
                         AND p.research_evidence_intake_item_id =
                             a.research_evidence_intake_item_id
                         AND p.assessment_sequence =
                             a.assessment_sequence - 1
                        WHERE a.project_id = $1
                          AND a.research_evidence_intake_item_id = $2
                          AND (
                              (a.assessment_sequence = 1
                               AND a.supersedes_assessment_id IS NOT NULL)
                              OR
                              (a.assessment_sequence > 1 AND p.id IS NULL)
                          )
                    )',
                    TG_TABLE_SCHEMA, TG_TABLE_SCHEMA
                ) INTO v_malformed
                USING NEW.project_id,
                      NEW.research_evidence_intake_item_id;
                IF v_malformed THEN
                    RAISE EXCEPTION 'malformed freshness assessment chain'
                        USING ERRCODE = '23514';
                END IF;

                IF v_last > 0 THEN
                    EXECUTE format(
                        'SELECT id
                         FROM
                             %I.research_evidence_intake_item_freshness_assessment
                         WHERE project_id = $1
                           AND research_evidence_intake_item_id = $2
                           AND assessment_sequence = $3',
                        TG_TABLE_SCHEMA
                    ) INTO v_current_id
                    USING NEW.project_id,
                          NEW.research_evidence_intake_item_id,
                          v_last;
                    IF v_current_id IS NULL THEN
                        RAISE EXCEPTION
                            'malformed freshness assessment chain'
                            USING ERRCODE = '23514';
                    END IF;
                    NEW.supersedes_assessment_id := v_current_id;
                END IF;

                v_next := v_last + 1;
                EXECUTE format(
                    'UPDATE
                         %I.research_evidence_item_freshness_sequence_allocator
                     SET last_sequence = $3
                     WHERE project_id = $1
                       AND research_evidence_intake_item_id = $2',
                    TG_TABLE_SCHEMA
                ) USING NEW.project_id,
                          NEW.research_evidence_intake_item_id,
                          v_next;

                NEW.assessment_sequence := v_next;
                NEW.assessed_at := clock_timestamp();
                RETURN NEW;
            END;
            $function_body$
        $create_function$;
    END IF;
END $$;

REVOKE ALL ON FUNCTION
    research_evidence_prepare_freshness_assessment_insert()
    FROM PUBLIC;
REVOKE ALL ON TABLE
    research_evidence_item_freshness_sequence_allocator
    FROM PUBLIC;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_reifa_prepare_insert'
          AND tgrelid =
              'research_evidence_intake_item_freshness_assessment'::regclass
          AND NOT tgisinternal
    ) THEN
        CREATE TRIGGER trg_reifa_prepare_insert
            BEFORE INSERT
            ON research_evidence_intake_item_freshness_assessment
            FOR EACH ROW
            EXECUTE FUNCTION
                research_evidence_prepare_freshness_assessment_insert();
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_reifa_no_mutation'
          AND tgrelid =
              'research_evidence_intake_item_freshness_assessment'::regclass
          AND NOT tgisinternal
    ) THEN
        CREATE TRIGGER trg_reifa_no_mutation
            BEFORE UPDATE OR DELETE
            ON research_evidence_intake_item_freshness_assessment
            FOR EACH ROW EXECUTE FUNCTION slicea_reject_mutation();
    END IF;
END $$;

ALTER TABLE research_evidence_intake_item_freshness_assessment
    ENABLE ALWAYS TRIGGER trg_reifa_prepare_insert;

COMMIT;
