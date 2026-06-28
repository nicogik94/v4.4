-- v54 Controlled Research-Evidence Item Review Foundation (R1.3)
-- Additive, append-only operator review decisions for existing v53 intake items.
--
-- Apply manually AFTER sql/v47_evidence_snapshot_foundation.sql,
-- sql/v51_research_evidence_sidecar_foundation.sql,
-- sql/v52_research_evidence_audit_integrity.sql, and
-- sql/v53_research_evidence_intake_foundation.sql:
--   psql -U workflow -d workflow_v4 -v ON_ERROR_STOP=1 -f sql/v54_research_evidence_review_foundation.sql

BEGIN;

-- Validate only the concrete parent contracts used by v54. Existing v47-v53
-- objects and history are never repaired, rewritten, or resequenced.
DO $$
DECLARE
    v_parent_tables integer;
    v_v54_tables integer;
    v_present boolean;
    v_missing text;
    v_reject_oid oid;
    v_prepare_oid oid;
BEGIN
    SELECT count(*) INTO v_parent_tables
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'r'
      AND n.nspname = current_schema()
      AND c.relname = ANY (ARRAY[
          'source_blob', 'source_snapshot', 'ingest_operation',
          'candidate_fact_revision', 'evidence_retention_event',
          'research_source_metadata_revision',
          'research_fact_metadata_revision',
          'research_claim_draft', 'research_evidence_event',
          'research_evidence_event_sequence_allocator',
          'research_evidence_intake', 'research_evidence_intake_item'
      ]);
    IF v_parent_tables <> 12 THEN
        RAISE EXCEPTION
            'v54 requires complete v47/v51/v52/v53 parent tables, found % of 12',
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
        RAISE EXCEPTION 'v54 requires append-only guard slicea_reject_mutation()'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- v54 consumes the v53 item identity key and relies on the immutable item
    -- graph remaining project- and snapshot-scoped.
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        WHERE con.connamespace = current_schema()::regnamespace
          AND con.conname = 'uq_reii_id_project'
          AND con.contype = 'u'
          AND con.conrelid = 'research_evidence_intake_item'::regclass
          AND (
              SELECT array_agg(a.attname::text ORDER BY u.ordinality)
              FROM unnest(con.conkey) WITH ORDINALITY u(attnum, ordinality)
              JOIN pg_attribute a
                ON a.attrelid = con.conrelid AND a.attnum = u.attnum
          ) = ARRAY['id', 'project_id']
    ) THEN
        RAISE EXCEPTION 'v54 requires v53 key uq_reii_id_project(id, project_id)'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('fk_snapshot_blob_project'::text, 'source_snapshot'::text,
         'source_blob'::text, ARRAY['source_blob_id', 'project_id']::text[],
         ARRAY['id', 'project_id']::text[]),
        ('fk_ret_blob_project', 'evidence_retention_event', 'source_blob',
         ARRAY['source_blob_id', 'project_id'], ARRAY['id', 'project_id']),
        ('fk_ret_snapshot_project', 'evidence_retention_event',
         'source_snapshot', ARRAY['source_snapshot_id', 'project_id'],
         ARRAY['id', 'project_id']),
        ('fk_ret_fact_project', 'evidence_retention_event',
         'candidate_fact_revision',
         ARRAY['candidate_fact_revision_id', 'project_id'],
         ARRAY['id', 'project_id']),
        ('fk_rei_snapshot_project', 'research_evidence_intake',
         'source_snapshot', ARRAY['source_snapshot_id', 'project_id'],
         ARRAY['id', 'project_id']),
        ('fk_rei_source_metadata_snapshot', 'research_evidence_intake',
         'research_source_metadata_revision',
         ARRAY['source_metadata_revision_id', 'project_id',
               'source_snapshot_id'],
         ARRAY['id', 'project_id', 'source_snapshot_id']),
        ('fk_reii_intake_snapshot', 'research_evidence_intake_item',
         'research_evidence_intake',
         ARRAY['research_evidence_intake_id', 'project_id',
               'source_snapshot_id'],
         ARRAY['id', 'project_id', 'source_snapshot_id']),
        ('fk_reii_fact_project', 'research_evidence_intake_item',
         'candidate_fact_revision',
         ARRAY['candidate_fact_revision_id', 'project_id'],
         ARRAY['id', 'project_id']),
        ('fk_reii_fact_metadata_fact', 'research_evidence_intake_item',
         'research_fact_metadata_revision',
         ARRAY['fact_metadata_revision_id', 'project_id',
               'candidate_fact_revision_id'],
         ARRAY['id', 'project_id', 'candidate_fact_revision_id']),
        ('fk_reii_claim_project', 'research_evidence_intake_item',
         'research_claim_draft', ARRAY['claim_draft_id', 'project_id'],
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
        RAISE EXCEPTION 'v54 requires complete v53 parent graph: missing %', v_missing
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('ck_retention_single_target'::text),
        ('evidence_retention_event_event_type_check'),
        ('ck_ree_entity_type'),
        ('ck_ree_event_type')
    ) AS expected(name)
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        WHERE con.connamespace = current_schema()::regnamespace
          AND con.conname = expected.name
          AND con.contype = 'c'
          AND con.convalidated
    );
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v54 requires complete v47/v51 checks: missing %', v_missing
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(tg_name, ', ' ORDER BY tg_name) INTO v_missing
    FROM (VALUES
        ('trg_source_blob_no_mutation'::text, 'source_blob'::text, 'O'::"char"),
        ('trg_source_snapshot_no_mutation', 'source_snapshot', 'O'),
        ('trg_cfr_no_mutation', 'candidate_fact_revision', 'O'),
        ('trg_retention_no_mutation', 'evidence_retention_event', 'O'),
        ('trg_rsmr_no_mutation', 'research_source_metadata_revision', 'O'),
        ('trg_rfmr_no_mutation', 'research_fact_metadata_revision', 'O'),
        ('trg_rcd_no_mutation', 'research_claim_draft', 'O'),
        ('trg_ree_no_mutation', 'research_evidence_event', 'O'),
        ('trg_rei_no_mutation', 'research_evidence_intake', 'O'),
        ('trg_reii_no_mutation', 'research_evidence_intake_item', 'O')
    ) AS expected(tg_name, table_name, enabled)
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_trigger t
        WHERE t.tgname = expected.tg_name
          AND t.tgrelid = expected.table_name::regclass
          AND NOT t.tgisinternal
          AND t.tgfoid = v_reject_oid
          AND t.tgenabled = expected.enabled
          AND (t.tgtype & 1) = 1
          AND (t.tgtype & (2 + 8 + 16)) = (2 + 8 + 16)
    );
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v54 requires exact v47/v51/v53 append-only guards: missing %',
            v_missing
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- The review availability resolver reads these exact v47/v51 relationships.
    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('fk_cfr_snapshot_project'::text, 'candidate_fact_revision'::text,
         'source_snapshot'::text,
         ARRAY['source_snapshot_id', 'project_id']::text[],
         ARRAY['id', 'project_id']::text[]),
        ('fk_rsmr_snapshot_project', 'research_source_metadata_revision',
         'source_snapshot', ARRAY['source_snapshot_id', 'project_id'],
         ARRAY['id', 'project_id']),
        ('fk_rsmr_supersedes_same_snapshot',
         'research_source_metadata_revision',
         'research_source_metadata_revision',
         ARRAY['supersedes_metadata_revision_id', 'project_id',
               'source_snapshot_id'],
         ARRAY['id', 'project_id', 'source_snapshot_id']),
        ('fk_rfmr_fact_project', 'research_fact_metadata_revision',
         'candidate_fact_revision',
         ARRAY['candidate_fact_revision_id', 'project_id'],
         ARRAY['id', 'project_id']),
        ('fk_rfmr_supersedes_fact_project',
         'research_fact_metadata_revision', 'candidate_fact_revision',
         ARRAY['supersedes_candidate_fact_revision_id', 'project_id'],
         ARRAY['id', 'project_id']),
        ('fk_rfmr_supersedes_same_fact',
         'research_fact_metadata_revision',
         'research_fact_metadata_revision',
         ARRAY['supersedes_metadata_revision_id', 'project_id',
               'candidate_fact_revision_id'],
         ARRAY['id', 'project_id', 'candidate_fact_revision_id']),
        ('fk_rcd_supersedes_claim_project', 'research_claim_draft',
         'research_claim_draft',
         ARRAY['supersedes_claim_id', 'project_id'],
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
        RAISE EXCEPTION 'v54 requires complete v47/v51 lineage contracts: missing %',
            v_missing USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint con
        WHERE con.connamespace = current_schema()::regnamespace
          AND con.conname = 'evidence_retention_event_event_type_check'
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
          ) = 'event_type=anyarray[''legal_hold'',''tombstone'',''redact'']'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint con
        WHERE con.connamespace = current_schema()::regnamespace
          AND con.conname = 'ck_ree_entity_type'
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
          ) =
              'entity_type=anyarray[''source_metadata_revision'','
              || '''fact_metadata_revision'',''claim_draft'']'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint con
        WHERE con.connamespace = current_schema()::regnamespace
          AND con.conname = 'ck_ree_event_type'
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
          ) =
              'event_type=anyarray[''created'',''superseded'','
              || '''correction_recorded'',''withdrawn'']'
    ) THEN
        RAISE EXCEPTION 'v54 requires exact v47 retention and v51 event vocabularies'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = current_schema()
          AND p.proname = 'research_evidence_prepare_event_insert'
          AND p.pronargs = 0
          AND p.prorettype = 'trigger'::regtype
          AND p.prosecdef
          AND p.proconfig = ARRAY['search_path=pg_catalog']
    ) OR NOT EXISTS (
        SELECT 1
        FROM pg_trigger t
        JOIN pg_proc p ON p.oid = t.tgfoid
        WHERE t.tgname = 'trg_ree_prepare_insert'
          AND t.tgrelid = 'research_evidence_event'::regclass
          AND NOT t.tgisinternal
          AND t.tgenabled = 'A'
          AND p.proname = 'research_evidence_prepare_event_insert'
          AND (t.tgtype & (1 + 2 + 4)) = (1 + 2 + 4)
    ) THEN
        RAISE EXCEPTION 'v54 requires complete v52 event-integrity foundation'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT count(*) INTO v_v54_tables
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'r'
      AND n.nspname = current_schema()
      AND c.relname = ANY (ARRAY[
          'research_evidence_intake_item_review_decision',
          'research_evidence_item_review_sequence_allocator'
      ]);

    SELECT (v_v54_tables > 0)
        OR EXISTS (
            SELECT 1
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = current_schema()
              AND p.proname = 'research_evidence_prepare_item_review_insert'
        )
        OR EXISTS (
            SELECT 1
            FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = current_schema()
              AND t.tgname = ANY (ARRAY[
                  'trg_reird_prepare_insert', 'trg_reird_no_mutation'
              ]))
        OR EXISTS (
            SELECT 1
            FROM pg_constraint con
            JOIN pg_namespace n ON n.oid = con.connamespace
            WHERE n.nspname = current_schema()
              AND con.conname LIKE ANY (ARRAY[
                  'uq_reird_%', 'fk_reird_%', 'ck_reird_%',
                  'fk_reirsa_%', 'ck_reirsa_%'
              ]))
        INTO v_present;

    IF NOT v_present THEN
        RETURN;
    END IF;

    IF v_v54_tables <> 2 THEN
        RAISE EXCEPTION
            'v54 contract violation: expected 2 review tables, found % — partial/divergent state',
            v_v54_tables USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF (
        SELECT count(*) FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'research_evidence_intake_item_review_decision'
    ) <> 10 OR EXISTS (
        SELECT 1
        FROM (VALUES
            ('id'::text, 'uuid'::text, 'NO'::text),
            ('project_id', 'uuid', 'NO'),
            ('research_evidence_intake_item_id', 'uuid', 'NO'),
            ('decision_type', 'text', 'NO'),
            ('decision_sequence', 'integer', 'NO'),
            ('supersedes_decision_id', 'uuid', 'YES'),
            ('decision_reason', 'text', 'NO'),
            ('decided_by', 'text', 'NO'),
            ('request_id', 'text', 'NO'),
            ('recorded_at', 'timestamp with time zone', 'NO')
        ) AS expected(column_name, data_type, is_nullable)
        LEFT JOIN information_schema.columns c
          ON c.table_schema = current_schema()
         AND c.table_name = 'research_evidence_intake_item_review_decision'
         AND c.column_name = expected.column_name
        WHERE c.column_name IS NULL
           OR c.data_type <> expected.data_type
           OR c.is_nullable <> expected.is_nullable
    ) THEN
        RAISE EXCEPTION 'v54 contract violation: divergent review-decision columns'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF (
        SELECT count(*) FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'research_evidence_item_review_sequence_allocator'
    ) <> 3 OR EXISTS (
        SELECT 1
        FROM (VALUES
            ('project_id'::text, 'uuid'::text, 'NO'::text),
            ('research_evidence_intake_item_id', 'uuid', 'NO'),
            ('last_sequence', 'integer', 'NO')
        ) AS expected(column_name, data_type, is_nullable)
        LEFT JOIN information_schema.columns c
          ON c.table_schema = current_schema()
         AND c.table_name = 'research_evidence_item_review_sequence_allocator'
         AND c.column_name = expected.column_name
        WHERE c.column_name IS NULL
           OR c.data_type <> expected.data_type
           OR c.is_nullable <> expected.is_nullable
    ) THEN
        RAISE EXCEPTION 'v54 contract violation: divergent allocator columns'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('research_evidence_intake_item_review_decision_pkey'::text),
        ('uq_reird_id_project_item'),
        ('uq_reird_item_sequence'),
        ('uq_reird_item_request'),
        ('uq_reird_supersedes_once'),
        ('fk_reird_project'),
        ('fk_reird_item_project'),
        ('fk_reird_supersedes_same_item'),
        ('ck_reird_decision_type'),
        ('ck_reird_sequence_positive'),
        ('ck_reird_reason_nonblank'),
        ('ck_reird_decided_by_nonblank'),
        ('ck_reird_request_id_nonblank'),
        ('research_evidence_item_review_sequence_allocator_pkey'),
        ('fk_reirsa_project'),
        ('fk_reirsa_item_project'),
        ('ck_reirsa_last_sequence')
    ) AS expected(name)
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        WHERE con.connamespace = current_schema()::regnamespace
          AND con.conname = expected.name
          AND con.convalidated
    );
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v54 contract violation: missing constraints %', v_missing
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'research_evidence_intake_item_review_decision'
          AND column_name IN (
              'decision_sequence', 'supersedes_decision_id', 'recorded_at'
          )
          AND column_default IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'v54 contract violation: server-owned review fields have defaults'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_attribute a
        JOIN pg_attrdef d
          ON d.adrelid = a.attrelid
         AND d.adnum = a.attnum
        WHERE a.attrelid =
              'research_evidence_intake_item_review_decision'::regclass
          AND a.attname = 'id'
          AND lower(
              regexp_replace(
                  pg_get_expr(d.adbin, d.adrelid, true),
                  '[[:space:]]+', '', 'g'
              )
          ) = 'gen_random_uuid()'
    ) THEN
        RAISE EXCEPTION 'v54 contract violation: divergent review-decision id default'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('uq_reird_id_project_item'::text,
         ARRAY['id', 'project_id', 'research_evidence_intake_item_id']::text[]),
        ('uq_reird_item_sequence',
         ARRAY['project_id', 'research_evidence_intake_item_id',
               'decision_sequence']),
        ('uq_reird_item_request',
         ARRAY['project_id', 'research_evidence_intake_item_id', 'request_id']),
        ('uq_reird_supersedes_once', ARRAY['supersedes_decision_id'])
    ) AS expected(name, columns)
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        WHERE con.connamespace = current_schema()::regnamespace
          AND con.conname = expected.name
          AND con.contype = 'u'
          AND con.conrelid =
              'research_evidence_intake_item_review_decision'::regclass
          AND (
              SELECT array_agg(a.attname::text ORDER BY u.ordinality)
              FROM unnest(con.conkey) WITH ORDINALITY u(attnum, ordinality)
              JOIN pg_attribute a
                ON a.attrelid = con.conrelid AND a.attnum = u.attnum
          ) = expected.columns
    );
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v54 contract violation: divergent unique keys %', v_missing
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        WHERE con.connamespace = current_schema()::regnamespace
          AND con.conname =
              'research_evidence_intake_item_review_decision_pkey'
          AND con.contype = 'p'
          AND con.conrelid =
              'research_evidence_intake_item_review_decision'::regclass
          AND (
              SELECT array_agg(a.attname::text ORDER BY u.ordinality)
              FROM unnest(con.conkey) WITH ORDINALITY u(attnum, ordinality)
              JOIN pg_attribute a
                ON a.attrelid = con.conrelid AND a.attnum = u.attnum
          ) = ARRAY['id']
    ) OR NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        WHERE con.connamespace = current_schema()::regnamespace
          AND con.conname =
              'research_evidence_item_review_sequence_allocator_pkey'
          AND con.contype = 'p'
          AND con.conrelid =
              'research_evidence_item_review_sequence_allocator'::regclass
          AND (
              SELECT array_agg(a.attname::text ORDER BY u.ordinality)
              FROM unnest(con.conkey) WITH ORDINALITY u(attnum, ordinality)
              JOIN pg_attribute a
                ON a.attrelid = con.conrelid AND a.attnum = u.attnum
          ) = ARRAY['project_id', 'research_evidence_intake_item_id']
    ) THEN
        RAISE EXCEPTION 'v54 contract violation: divergent primary keys'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('fk_reird_project'::text,
         'research_evidence_intake_item_review_decision'::text,
         'projects'::text, ARRAY['project_id']::text[], ARRAY['id']::text[]),
        ('fk_reird_item_project',
         'research_evidence_intake_item_review_decision',
         'research_evidence_intake_item',
         ARRAY['research_evidence_intake_item_id', 'project_id'],
         ARRAY['id', 'project_id']),
        ('fk_reird_supersedes_same_item',
         'research_evidence_intake_item_review_decision',
         'research_evidence_intake_item_review_decision',
         ARRAY['supersedes_decision_id', 'project_id',
               'research_evidence_intake_item_id'],
         ARRAY['id', 'project_id', 'research_evidence_intake_item_id']),
        ('fk_reirsa_project',
         'research_evidence_item_review_sequence_allocator',
         'projects', ARRAY['project_id'], ARRAY['id']),
        ('fk_reirsa_item_project',
         'research_evidence_item_review_sequence_allocator',
         'research_evidence_intake_item',
         ARRAY['research_evidence_intake_item_id', 'project_id'],
         ARRAY['id', 'project_id'])
    ) AS expected(name, local_table, parent_table, local_columns, parent_columns)
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
        RAISE EXCEPTION 'v54 contract violation: divergent foreign keys %', v_missing
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint con
        WHERE con.connamespace = current_schema()::regnamespace
          AND con.conname = 'ck_reird_decision_type'
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
          ) = 'decision_type=anyarray[''approved'',''rejected'',''needs_revision'',''withdrawn'']'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint con
        WHERE con.connamespace = current_schema()::regnamespace
          AND con.conname = 'ck_reird_sequence_positive'
          AND con.contype = 'c'
          AND translate(
              regexp_replace(
                  lower(pg_get_expr(con.conbin, con.conrelid, true)),
                  '[[:space:]]+', '', 'g'
              ),
              '()', ''
          ) = 'decision_sequence>=1'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint con
        WHERE con.connamespace = current_schema()::regnamespace
          AND con.conname = 'ck_reirsa_last_sequence'
          AND con.contype = 'c'
          AND translate(
              regexp_replace(
                  lower(pg_get_expr(con.conbin, con.conrelid, true)),
                  '[[:space:]]+', '', 'g'
              ),
              '()', ''
          ) = 'last_sequence>=0'
    ) OR EXISTS (
        SELECT 1
        FROM (VALUES
            ('ck_reird_reason_nonblank'::text, 'decision_reason'::text),
            ('ck_reird_decided_by_nonblank', 'decided_by'),
            ('ck_reird_request_id_nonblank', 'request_id')
        ) AS expected(name, column_name)
        WHERE NOT EXISTS (
            SELECT 1 FROM pg_constraint con
            WHERE con.connamespace = current_schema()::regnamespace
              AND con.conname = expected.name
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
              ) = expected.column_name || '!~''^[[:space:]]*$'''
        )
    ) THEN
        RAISE EXCEPTION 'v54 contract violation: divergent decision/nonblank checks'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT p.oid INTO v_prepare_oid
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = current_schema()
      AND p.proname = 'research_evidence_prepare_item_review_insert'
      AND p.pronargs = 0
      AND p.prorettype = 'trigger'::regtype
      AND p.prosecdef
      AND p.proconfig = ARRAY['search_path=pg_catalog']
      AND md5(regexp_replace(p.prosrc, '[[:space:]]+', '', 'g')) =
          'fb147d821ac9f1e55790ea344735cc89'
      AND NOT EXISTS (
          SELECT 1
          FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) acl
          WHERE acl.grantee = 0
            AND acl.privilege_type = 'EXECUTE'
      );
    IF v_prepare_oid IS NULL THEN
        RAISE EXCEPTION 'v54 contract violation: divergent review insert function'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_indexes
        WHERE schemaname = current_schema()
          AND tablename =
              'research_evidence_intake_item_review_decision'
          AND indexname = 'idx_reird_item_sequence'
          AND indexdef ILIKE
              '%(project_id, research_evidence_intake_item_id, decision_sequence)%'
    ) THEN
        RAISE EXCEPTION 'v54 contract violation: divergent review sequence index'
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
          AND c.relname = 'research_evidence_item_review_sequence_allocator'
          AND acl.grantee = 0
    ) THEN
        RAISE EXCEPTION 'v54 contract violation: allocator has PUBLIC privileges'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(tg_name, ', ' ORDER BY tg_name) INTO v_missing
    FROM (VALUES
        ('trg_reird_prepare_insert'::text,
         v_prepare_oid, 2 + 4, 'A'::"char"),
        ('trg_reird_no_mutation', v_reject_oid, 2 + 8 + 16, 'O'::"char")
    ) AS expected(tg_name, function_oid, required_bits, enabled)
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_trigger t
        WHERE t.tgname = expected.tg_name
          AND t.tgrelid =
              'research_evidence_intake_item_review_decision'::regclass
          AND NOT t.tgisinternal
          AND t.tgfoid = expected.function_oid
          AND t.tgenabled = expected.enabled
          AND (t.tgtype & 1) = 1
          AND (t.tgtype & (2 + 4 + 8 + 16)) = expected.required_bits
    );
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v54 contract violation: missing or divergent triggers %',
            v_missing USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM (
            SELECT project_id, research_evidence_intake_item_id,
                   count(*)::integer AS row_count,
                   min(decision_sequence) AS min_sequence,
                   max(decision_sequence) AS last_sequence
            FROM research_evidence_intake_item_review_decision
            GROUP BY project_id, research_evidence_intake_item_id
        ) history
        FULL JOIN research_evidence_item_review_sequence_allocator allocator
          USING (project_id, research_evidence_intake_item_id)
        WHERE history.row_count IS DISTINCT FROM allocator.last_sequence
           OR history.min_sequence IS DISTINCT FROM 1
           OR history.last_sequence IS DISTINCT FROM allocator.last_sequence
    ) THEN
        RAISE EXCEPTION 'v54 contract violation: allocator diverges from decision history'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM research_evidence_intake_item_review_decision decision
        LEFT JOIN research_evidence_intake_item_review_decision predecessor
          ON predecessor.id = decision.supersedes_decision_id
         AND predecessor.project_id = decision.project_id
         AND predecessor.research_evidence_intake_item_id =
             decision.research_evidence_intake_item_id
         AND predecessor.decision_sequence = decision.decision_sequence - 1
        WHERE (decision.decision_sequence = 1
               AND decision.supersedes_decision_id IS NOT NULL)
           OR (decision.decision_sequence > 1 AND predecessor.id IS NULL)
    ) THEN
        RAISE EXCEPTION 'v54 contract violation: malformed review decision chain'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS research_evidence_intake_item_review_decision (
    id                               UUID NOT NULL DEFAULT gen_random_uuid(),
    project_id                       UUID NOT NULL,
    research_evidence_intake_item_id UUID NOT NULL,
    decision_type                    TEXT NOT NULL,
    decision_sequence                INTEGER NOT NULL,
    supersedes_decision_id           UUID,
    decision_reason                  TEXT NOT NULL,
    decided_by                       TEXT NOT NULL,
    request_id                       TEXT NOT NULL,
    recorded_at                      TIMESTAMPTZ NOT NULL,
    CONSTRAINT research_evidence_intake_item_review_decision_pkey
        PRIMARY KEY (id),
    CONSTRAINT uq_reird_id_project_item
        UNIQUE (id, project_id, research_evidence_intake_item_id),
    CONSTRAINT uq_reird_item_sequence
        UNIQUE (project_id, research_evidence_intake_item_id, decision_sequence),
    CONSTRAINT uq_reird_item_request
        UNIQUE (project_id, research_evidence_intake_item_id, request_id),
    CONSTRAINT uq_reird_supersedes_once UNIQUE (supersedes_decision_id),
    CONSTRAINT fk_reird_project
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
    CONSTRAINT fk_reird_item_project
        FOREIGN KEY (research_evidence_intake_item_id, project_id)
        REFERENCES research_evidence_intake_item(id, project_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_reird_supersedes_same_item
        FOREIGN KEY (
            supersedes_decision_id, project_id,
            research_evidence_intake_item_id
        )
        REFERENCES research_evidence_intake_item_review_decision(
            id, project_id, research_evidence_intake_item_id
        ) ON DELETE RESTRICT,
    CONSTRAINT ck_reird_decision_type CHECK (
        decision_type IN ('approved', 'rejected', 'needs_revision', 'withdrawn')
    ),
    CONSTRAINT ck_reird_sequence_positive CHECK (decision_sequence >= 1),
    CONSTRAINT ck_reird_reason_nonblank CHECK (
        decision_reason !~ '^[[:space:]]*$'
    ),
    CONSTRAINT ck_reird_decided_by_nonblank CHECK (
        decided_by !~ '^[[:space:]]*$'
    ),
    CONSTRAINT ck_reird_request_id_nonblank CHECK (
        request_id !~ '^[[:space:]]*$'
    )
);

CREATE TABLE IF NOT EXISTS research_evidence_item_review_sequence_allocator (
    project_id                       UUID NOT NULL,
    research_evidence_intake_item_id UUID NOT NULL,
    last_sequence                    INTEGER NOT NULL,
    CONSTRAINT research_evidence_item_review_sequence_allocator_pkey
        PRIMARY KEY (project_id, research_evidence_intake_item_id),
    CONSTRAINT fk_reirsa_project
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
    CONSTRAINT fk_reirsa_item_project
        FOREIGN KEY (research_evidence_intake_item_id, project_id)
        REFERENCES research_evidence_intake_item(id, project_id)
        ON DELETE RESTRICT,
    CONSTRAINT ck_reirsa_last_sequence CHECK (last_sequence >= 0)
);

CREATE INDEX IF NOT EXISTS idx_reird_item_sequence
    ON research_evidence_intake_item_review_decision(
        project_id, research_evidence_intake_item_id, decision_sequence
    );

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = current_schema()
          AND p.proname = 'research_evidence_prepare_item_review_insert'
          AND p.pronargs = 0
    ) THEN
        EXECUTE $create_function$
            CREATE FUNCTION research_evidence_prepare_item_review_insert()
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
                IF NEW.decision_sequence IS NOT NULL THEN
                    RAISE EXCEPTION 'decision_sequence is server-assigned'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.recorded_at IS NOT NULL THEN
                    RAISE EXCEPTION 'recorded_at is server-assigned'
                        USING ERRCODE = '23514';
                END IF;

                EXECUTE format(
                    'INSERT INTO %I.research_evidence_item_review_sequence_allocator
                        (project_id, research_evidence_intake_item_id, last_sequence)
                     VALUES ($1, $2, 0)
                     ON CONFLICT (project_id, research_evidence_intake_item_id)
                     DO NOTHING',
                    TG_TABLE_SCHEMA
                ) USING NEW.project_id, NEW.research_evidence_intake_item_id;

                EXECUTE format(
                    'SELECT last_sequence
                     FROM %I.research_evidence_item_review_sequence_allocator
                     WHERE project_id = $1
                       AND research_evidence_intake_item_id = $2
                     FOR UPDATE',
                    TG_TABLE_SCHEMA
                ) INTO v_last
                USING NEW.project_id, NEW.research_evidence_intake_item_id;

                EXECUTE format(
                    'SELECT count(*)::integer,
                            min(decision_sequence),
                            max(decision_sequence)
                     FROM %I.research_evidence_intake_item_review_decision
                     WHERE project_id = $1
                       AND research_evidence_intake_item_id = $2',
                    TG_TABLE_SCHEMA
                ) INTO v_count, v_min, v_max
                USING NEW.project_id, NEW.research_evidence_intake_item_id;

                IF v_count <> v_last
                   OR (v_last = 0 AND (v_min IS NOT NULL OR v_max IS NOT NULL))
                   OR (v_last > 0 AND (v_min <> 1 OR v_max <> v_last)) THEN
                    RAISE EXCEPTION 'malformed review decision chain'
                        USING ERRCODE = '23514';
                END IF;

                EXECUTE format(
                    'SELECT EXISTS (
                        SELECT 1
                        FROM %I.research_evidence_intake_item_review_decision d
                        LEFT JOIN %I.research_evidence_intake_item_review_decision p
                          ON p.id = d.supersedes_decision_id
                         AND p.project_id = d.project_id
                         AND p.research_evidence_intake_item_id =
                             d.research_evidence_intake_item_id
                         AND p.decision_sequence = d.decision_sequence - 1
                        WHERE d.project_id = $1
                          AND d.research_evidence_intake_item_id = $2
                          AND (
                              (d.decision_sequence = 1
                               AND d.supersedes_decision_id IS NOT NULL)
                              OR
                              (d.decision_sequence > 1 AND p.id IS NULL)
                          )
                    )',
                    TG_TABLE_SCHEMA, TG_TABLE_SCHEMA
                ) INTO v_malformed
                USING NEW.project_id, NEW.research_evidence_intake_item_id;
                IF v_malformed THEN
                    RAISE EXCEPTION 'malformed review decision chain'
                        USING ERRCODE = '23514';
                END IF;

                IF v_last = 0 THEN
                    IF NEW.supersedes_decision_id IS NOT NULL THEN
                        RAISE EXCEPTION 'first review decision cannot have a predecessor'
                            USING ERRCODE = '23514';
                    END IF;
                    IF NEW.decision_type = 'withdrawn' THEN
                        RAISE EXCEPTION 'withdrawn requires an existing review decision'
                            USING ERRCODE = '23514';
                    END IF;
                ELSE
                    EXECUTE format(
                        'SELECT id
                         FROM %I.research_evidence_intake_item_review_decision
                         WHERE project_id = $1
                           AND research_evidence_intake_item_id = $2
                           AND decision_sequence = $3',
                        TG_TABLE_SCHEMA
                    ) INTO v_current_id
                    USING NEW.project_id,
                          NEW.research_evidence_intake_item_id,
                          v_last;
                    IF v_current_id IS NULL THEN
                        RAISE EXCEPTION 'malformed review decision chain'
                            USING ERRCODE = '23514';
                    END IF;
                    IF NEW.supersedes_decision_id IS NOT NULL
                       AND NEW.supersedes_decision_id <> v_current_id THEN
                        RAISE EXCEPTION 'stale review predecessor'
                            USING ERRCODE = '23514';
                    END IF;
                    NEW.supersedes_decision_id := v_current_id;
                END IF;

                v_next := v_last + 1;
                EXECUTE format(
                    'UPDATE %I.research_evidence_item_review_sequence_allocator
                     SET last_sequence = $3
                     WHERE project_id = $1
                       AND research_evidence_intake_item_id = $2',
                    TG_TABLE_SCHEMA
                ) USING NEW.project_id,
                          NEW.research_evidence_intake_item_id,
                          v_next;

                NEW.decision_sequence := v_next;
                NEW.recorded_at := clock_timestamp();
                RETURN NEW;
            END;
            $function_body$
        $create_function$;
    END IF;
END $$;

REVOKE ALL ON FUNCTION research_evidence_prepare_item_review_insert()
    FROM PUBLIC;
REVOKE ALL ON TABLE research_evidence_item_review_sequence_allocator
    FROM PUBLIC;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_reird_prepare_insert'
          AND tgrelid =
              'research_evidence_intake_item_review_decision'::regclass
          AND NOT tgisinternal
    ) THEN
        CREATE TRIGGER trg_reird_prepare_insert
            BEFORE INSERT
            ON research_evidence_intake_item_review_decision
            FOR EACH ROW
            EXECUTE FUNCTION research_evidence_prepare_item_review_insert();
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_reird_no_mutation'
          AND tgrelid =
              'research_evidence_intake_item_review_decision'::regclass
          AND NOT tgisinternal
    ) THEN
        CREATE TRIGGER trg_reird_no_mutation
            BEFORE UPDATE OR DELETE
            ON research_evidence_intake_item_review_decision
            FOR EACH ROW EXECUTE FUNCTION slicea_reject_mutation();
    END IF;
END $$;

ALTER TABLE research_evidence_intake_item_review_decision
    ENABLE ALWAYS TRIGGER trg_reird_prepare_insert;

COMMIT;
