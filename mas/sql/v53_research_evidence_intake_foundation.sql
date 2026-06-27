-- v53 Controlled Research-Evidence Intake Foundation (R1.2)
-- Additive, operator-selected, draft-only groupings around existing canonical
-- v47 snapshots and existing v51 metadata/fact/claim records.
--
-- Apply manually AFTER sql/v47_evidence_snapshot_foundation.sql,
-- sql/v51_research_evidence_sidecar_foundation.sql, and
-- sql/v52_research_evidence_audit_integrity.sql:
--   psql -U workflow -d workflow_v4 -v ON_ERROR_STOP=1 -f sql/v53_research_evidence_intake_foundation.sql

BEGIN;

-- Require the concrete parent keys and integrity objects used by v53, then
-- classify v53 itself as absent, complete, or partial/divergent. This migration
-- never repairs or mutates v47/v51/v52 objects or their rows.
DO $$
DECLARE
    v_v47_tables integer;
    v_v51_tables integer;
    v_v53_tables integer;
    v_present    boolean;
    v_missing    text;
    v_reject_oid oid;
    v_validate_oid oid;
BEGIN
    SELECT count(*) INTO v_v47_tables
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'r'
      AND n.nspname = current_schema()
      AND c.relname = ANY (ARRAY[
          'source_blob', 'source_snapshot', 'ingest_operation',
          'candidate_fact_revision', 'evidence_retention_event'
      ]);
    IF v_v47_tables <> 5 THEN
        RAISE EXCEPTION 'v53 requires complete v47 parent tables, found %', v_v47_tables
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT count(*) INTO v_v51_tables
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'r'
      AND n.nspname = current_schema()
      AND c.relname = ANY (ARRAY[
          'research_source_metadata_revision',
          'research_fact_metadata_revision',
          'research_claim_draft',
          'research_evidence_event'
      ]);
    IF v_v51_tables <> 4 THEN
        RAISE EXCEPTION 'v53 requires complete v51 parent tables, found %', v_v51_tables
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT p.oid INTO v_reject_oid
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = current_schema()
      AND p.proname = 'slicea_reject_mutation'
      AND p.pronargs = 0
      AND p.prorettype = 'trigger'::regtype;
    IF v_reject_oid IS NULL THEN
        RAISE EXCEPTION 'v53 requires append-only guard slicea_reject_mutation()'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- These are the exact existing composite targets and parent-link guarantees
    -- consumed by v53. Constraint-name presence alone is insufficient.
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        WHERE con.conname = 'uq_source_snapshot_id_project'
          AND con.connamespace = current_schema()::regnamespace
          AND con.contype = 'u'
          AND con.conrelid = 'source_snapshot'::regclass
          AND (
              SELECT array_agg(a.attname::text ORDER BY u.ordinality)
              FROM unnest(con.conkey) WITH ORDINALITY u(attnum, ordinality)
              JOIN pg_attribute a
                ON a.attrelid = con.conrelid AND a.attnum = u.attnum
          ) = ARRAY['id', 'project_id']
    ) THEN
        RAISE EXCEPTION 'v53 requires v47 key uq_source_snapshot_id_project(id, project_id)'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        WHERE con.conname = 'uq_cfr_id_project'
          AND con.connamespace = current_schema()::regnamespace
          AND con.contype = 'u'
          AND con.conrelid = 'candidate_fact_revision'::regclass
          AND (
              SELECT array_agg(a.attname::text ORDER BY u.ordinality)
              FROM unnest(con.conkey) WITH ORDINALITY u(attnum, ordinality)
              JOIN pg_attribute a
                ON a.attrelid = con.conrelid AND a.attnum = u.attnum
          ) = ARRAY['id', 'project_id']
    ) OR NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        WHERE con.conname = 'fk_cfr_snapshot_project'
          AND con.connamespace = current_schema()::regnamespace
          AND con.contype = 'f'
          AND con.conrelid = 'candidate_fact_revision'::regclass
          AND con.confrelid = 'source_snapshot'::regclass
          AND con.confdeltype = 'r'
    ) THEN
        RAISE EXCEPTION 'v53 requires v47 candidate-fact project/snapshot contract'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        WHERE con.conname = 'uq_rsmr_id_project_snapshot'
          AND con.connamespace = current_schema()::regnamespace
          AND con.contype = 'u'
          AND con.conrelid = 'research_source_metadata_revision'::regclass
          AND (
              SELECT array_agg(a.attname::text ORDER BY u.ordinality)
              FROM unnest(con.conkey) WITH ORDINALITY u(attnum, ordinality)
              JOIN pg_attribute a
                ON a.attrelid = con.conrelid AND a.attnum = u.attnum
          ) = ARRAY['id', 'project_id', 'source_snapshot_id']
    ) OR NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        WHERE con.conname = 'uq_rfmr_id_project_fact'
          AND con.connamespace = current_schema()::regnamespace
          AND con.contype = 'u'
          AND con.conrelid = 'research_fact_metadata_revision'::regclass
          AND (
              SELECT array_agg(a.attname::text ORDER BY u.ordinality)
              FROM unnest(con.conkey) WITH ORDINALITY u(attnum, ordinality)
              JOIN pg_attribute a
                ON a.attrelid = con.conrelid AND a.attnum = u.attnum
          ) = ARRAY['id', 'project_id', 'candidate_fact_revision_id']
    ) OR NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        WHERE con.conname = 'uq_rcd_id_project'
          AND con.connamespace = current_schema()::regnamespace
          AND con.contype = 'u'
          AND con.conrelid = 'research_claim_draft'::regclass
          AND (
              SELECT array_agg(a.attname::text ORDER BY u.ordinality)
              FROM unnest(con.conkey) WITH ORDINALITY u(attnum, ordinality)
              JOIN pg_attribute a
                ON a.attrelid = con.conrelid AND a.attnum = u.attnum
          ) = ARRAY['id', 'project_id']
    ) THEN
        RAISE EXCEPTION 'v53 requires exact v51 composite parent keys'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- These parent FKs make the composite-key targets meaningful: canonical
    -- facts remain tied to their snapshot, metadata remains tied to its canonical
    -- parent, and claims remain project-scoped.
    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('fk_cfr_snapshot_project'::text, 'candidate_fact_revision'::text,
         'source_snapshot'::text,
         ARRAY['source_snapshot_id', 'project_id']::text[],
         ARRAY['id', 'project_id']::text[]),
        ('fk_rsmr_snapshot_project', 'research_source_metadata_revision',
         'source_snapshot',
         ARRAY['source_snapshot_id', 'project_id'], ARRAY['id', 'project_id']),
        ('fk_rfmr_fact_project', 'research_fact_metadata_revision',
         'candidate_fact_revision',
         ARRAY['candidate_fact_revision_id', 'project_id'],
         ARRAY['id', 'project_id']),
        ('research_claim_draft_project_id_fkey', 'research_claim_draft',
         'projects', ARRAY['project_id'], ARRAY['id'])
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
        RAISE EXCEPTION 'v53 requires exact canonical/sidecar parent foreign keys: %',
            v_missing USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- v53 composes with the v51 service contract only after v52 has made event
    -- targeting and sequencing database-enforced.
    IF to_regclass(
        format('%I.research_evidence_event_sequence_allocator', current_schema())
    ) IS NULL OR NOT EXISTS (
        SELECT 1
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = current_schema()
          AND p.proname = 'research_evidence_prepare_event_insert'
          AND p.pronargs = 0
          AND p.prorettype = 'trigger'::regtype
    ) OR NOT EXISTS (
        SELECT 1
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_proc p ON p.oid = t.tgfoid
        WHERE n.nspname = current_schema()
          AND c.relname = 'research_evidence_event'
          AND t.tgname = 'trg_ree_prepare_insert'
          AND NOT t.tgisinternal
          AND t.tgenabled = 'A'
          AND p.proname = 'research_evidence_prepare_event_insert'
          AND (t.tgtype & 1) = 1
          AND (t.tgtype & 2) = 2
          AND (t.tgtype & 4) = 4
    ) THEN
        RAISE EXCEPTION 'v53 requires complete v52 event-integrity foundation'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT count(*) INTO v_v53_tables
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'r'
      AND n.nspname = current_schema()
      AND c.relname = ANY (ARRAY[
          'research_evidence_intake', 'research_evidence_intake_item'
      ]);

    SELECT (v_v53_tables > 0)
        OR EXISTS (
            SELECT 1
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = current_schema()
              AND p.proname = 'research_evidence_intake_validate_item_snapshot'
        )
        OR EXISTS (
            SELECT 1
            FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = current_schema()
              AND t.tgname = ANY (ARRAY[
                  'trg_rei_no_mutation', 'trg_reii_no_mutation',
                  'trg_reii_validate_snapshot'
              ]))
        OR EXISTS (
            SELECT 1
            FROM pg_constraint con
            JOIN pg_namespace n ON n.oid = con.connamespace
            WHERE n.nspname = current_schema()
              AND con.conname LIKE ANY (ARRAY['uq_rei_%', 'fk_rei_%', 'ck_rei_%']))
        OR EXISTS (
            SELECT 1
            FROM pg_indexes
            WHERE schemaname = current_schema()
              AND indexname = ANY (ARRAY[
                  'uq_reii_intake_candidate_fact',
                  'uq_reii_intake_claim_draft'
              ]))
        INTO v_present;

    IF NOT v_present THEN
        RETURN;
    END IF;

    IF v_v53_tables <> 2 THEN
        RAISE EXCEPTION
            'v53 contract violation: expected 2 intake tables, found % — partial/divergent state',
            v_v53_tables USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- Exact column sets, types, and nullability.
    IF (
        SELECT count(*) FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'research_evidence_intake'
    ) <> 9 OR EXISTS (
        SELECT 1
        FROM (VALUES
            ('id'::text, 'uuid'::text, 'NO'::text),
            ('project_id', 'uuid', 'NO'),
            ('source_snapshot_id', 'uuid', 'NO'),
            ('source_metadata_revision_id', 'uuid', 'NO'),
            ('intake_method', 'text', 'NO'),
            ('state', 'text', 'NO'),
            ('selection_reason', 'text', 'NO'),
            ('created_by', 'text', 'NO'),
            ('created_at', 'timestamp with time zone', 'NO')
        ) AS expected(column_name, data_type, is_nullable)
        LEFT JOIN information_schema.columns c
          ON c.table_schema = current_schema()
         AND c.table_name = 'research_evidence_intake'
         AND c.column_name = expected.column_name
        WHERE c.column_name IS NULL
           OR c.data_type <> expected.data_type
           OR c.is_nullable <> expected.is_nullable
    ) THEN
        RAISE EXCEPTION 'v53 contract violation: divergent research_evidence_intake columns'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF (
        SELECT count(*) FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'research_evidence_intake_item'
    ) <> 11 OR EXISTS (
        SELECT 1
        FROM (VALUES
            ('id'::text, 'uuid'::text, 'NO'::text),
            ('project_id', 'uuid', 'NO'),
            ('research_evidence_intake_id', 'uuid', 'NO'),
            ('source_snapshot_id', 'uuid', 'NO'),
            ('item_kind', 'text', 'NO'),
            ('candidate_fact_revision_id', 'uuid', 'YES'),
            ('fact_metadata_revision_id', 'uuid', 'YES'),
            ('claim_draft_id', 'uuid', 'YES'),
            ('state', 'text', 'NO'),
            ('created_by', 'text', 'NO'),
            ('created_at', 'timestamp with time zone', 'NO')
        ) AS expected(column_name, data_type, is_nullable)
        LEFT JOIN information_schema.columns c
          ON c.table_schema = current_schema()
         AND c.table_name = 'research_evidence_intake_item'
         AND c.column_name = expected.column_name
        WHERE c.column_name IS NULL
           OR c.data_type <> expected.data_type
           OR c.is_nullable <> expected.is_nullable
    ) THEN
        RAISE EXCEPTION 'v53 contract violation: divergent research_evidence_intake_item columns'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('research_evidence_intake_pkey'::text),
        ('uq_rei_id_project'),
        ('uq_rei_id_project_snapshot'),
        ('fk_rei_project'),
        ('fk_rei_snapshot_project'),
        ('fk_rei_source_metadata_snapshot'),
        ('ck_rei_intake_method'),
        ('ck_rei_state_draft'),
        ('ck_rei_selection_reason_nonblank'),
        ('ck_rei_created_by_nonblank'),
        ('research_evidence_intake_item_pkey'),
        ('uq_reii_id_project'),
        ('fk_reii_project'),
        ('fk_reii_intake_snapshot'),
        ('fk_reii_fact_project'),
        ('fk_reii_fact_metadata_fact'),
        ('fk_reii_claim_project'),
        ('ck_reii_item_kind'),
        ('ck_reii_state_draft'),
        ('ck_reii_created_by_nonblank'),
        ('ck_reii_target_shape')
    ) AS expected(name)
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        WHERE con.connamespace = current_schema()::regnamespace
          AND con.conname = expected.name
    );
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v53 contract violation: missing constraints %', v_missing
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- The service relies on server-owned draft/method defaults; a complete
    -- reapply must reject altered defaults rather than silently restore them.
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'research_evidence_intake'
          AND column_name = 'intake_method'
          AND column_default = '''operator_selected_existing_snapshot''::text'
    ) OR NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'research_evidence_intake'
          AND column_name = 'state'
          AND column_default = '''draft''::text'
    ) OR NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'research_evidence_intake_item'
          AND column_name = 'state'
          AND column_default = '''draft''::text'
    ) THEN
        RAISE EXCEPTION 'v53 contract violation: divergent server-owned defaults'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- Validate the exact unique-key column order consumed by composite FKs.
    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('uq_rei_id_project'::text, 'research_evidence_intake'::text,
         ARRAY['id', 'project_id']::text[]),
        ('uq_rei_id_project_snapshot', 'research_evidence_intake',
         ARRAY['id', 'project_id', 'source_snapshot_id']),
        ('uq_reii_id_project', 'research_evidence_intake_item',
         ARRAY['id', 'project_id'])
    ) AS expected(name, table_name, columns)
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        WHERE con.connamespace = current_schema()::regnamespace
          AND con.conname = expected.name
          AND con.contype = 'u'
          AND con.conrelid = expected.table_name::regclass
          AND (
              SELECT array_agg(a.attname::text ORDER BY u.ordinality)
              FROM unnest(con.conkey) WITH ORDINALITY u(attnum, ordinality)
              JOIN pg_attribute a
                ON a.attrelid = con.conrelid AND a.attnum = u.attnum
          ) = expected.columns
    );
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v53 contract violation: divergent unique keys %', v_missing
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- Exact single-value lifecycle/method checks and nonblank operator controls.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint con
        WHERE con.connamespace = current_schema()::regnamespace
          AND con.conname = 'ck_rei_intake_method'
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
          ) = 'intake_method=''operator_selected_existing_snapshot'''
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint con
        WHERE con.connamespace = current_schema()::regnamespace
          AND con.conname = 'ck_rei_state_draft'
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
          ) = 'state=''draft'''
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint con
        WHERE con.connamespace = current_schema()::regnamespace
          AND con.conname = 'ck_reii_state_draft'
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
          ) = 'state=''draft'''
    ) THEN
        RAISE EXCEPTION 'v53 contract violation: divergent method or draft-only checks'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('ck_rei_selection_reason_nonblank'::text, 'selection_reason'::text),
        ('ck_rei_created_by_nonblank', 'created_by')
    ) AS expected(name, column_name)
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
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
    );
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v53 contract violation: divergent intake nonblank checks %',
            v_missing
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        WHERE con.connamespace = current_schema()::regnamespace
          AND con.conname = 'ck_reii_created_by_nonblank'
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
          ) = 'char_lengthbtrimcreated_by>0'
    ) THEN
        RAISE EXCEPTION 'v53 contract violation: divergent item nonblank check'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint con
        WHERE con.connamespace = current_schema()::regnamespace
          AND con.conname = 'ck_reii_item_kind'
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
          ) = 'item_kind=anyarray[''candidate_fact'',''claim_draft'']'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint con
        WHERE con.connamespace = current_schema()::regnamespace
          AND con.conname = 'ck_reii_target_shape'
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
              'item_kind=''candidate_fact'''
              || 'andcandidate_fact_revision_idisnotnull'
              || 'andfact_metadata_revision_idisnotnull'
              || 'andclaim_draft_idisnull'
              || 'oritem_kind=''claim_draft'''
              || 'andcandidate_fact_revision_idisnull'
              || 'andfact_metadata_revision_idisnull'
              || 'andclaim_draft_idisnotnull'
    ) THEN
        RAISE EXCEPTION 'v53 contract violation: divergent item kind or target-shape check'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- Revalidate the exact FK targets, column order, and restrictive deletion.
    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('fk_rei_project'::text, 'research_evidence_intake'::text, 'projects'::text,
         ARRAY['project_id']::text[], ARRAY['id']::text[]),
        ('fk_rei_snapshot_project', 'research_evidence_intake', 'source_snapshot',
         ARRAY['source_snapshot_id', 'project_id'], ARRAY['id', 'project_id']),
        ('fk_rei_source_metadata_snapshot', 'research_evidence_intake',
         'research_source_metadata_revision',
         ARRAY['source_metadata_revision_id', 'project_id', 'source_snapshot_id'],
         ARRAY['id', 'project_id', 'source_snapshot_id']),
        ('fk_reii_project', 'research_evidence_intake_item', 'projects',
         ARRAY['project_id'], ARRAY['id']),
        ('fk_reii_intake_snapshot', 'research_evidence_intake_item',
         'research_evidence_intake',
         ARRAY['research_evidence_intake_id', 'project_id', 'source_snapshot_id'],
         ARRAY['id', 'project_id', 'source_snapshot_id']),
        ('fk_reii_fact_project', 'research_evidence_intake_item',
         'candidate_fact_revision',
         ARRAY['candidate_fact_revision_id', 'project_id'], ARRAY['id', 'project_id']),
        ('fk_reii_fact_metadata_fact', 'research_evidence_intake_item',
         'research_fact_metadata_revision',
         ARRAY['fact_metadata_revision_id', 'project_id', 'candidate_fact_revision_id'],
         ARRAY['id', 'project_id', 'candidate_fact_revision_id']),
        ('fk_reii_claim_project', 'research_evidence_intake_item',
         'research_claim_draft',
         ARRAY['claim_draft_id', 'project_id'], ARRAY['id', 'project_id'])
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
        RAISE EXCEPTION 'v53 contract violation: divergent foreign keys %', v_missing
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = current_schema()
          AND tablename = 'research_evidence_intake_item'
          AND indexname = 'uq_reii_intake_candidate_fact'
          AND indexdef ILIKE '%UNIQUE%'
          AND indexdef ILIKE '%(research_evidence_intake_id, candidate_fact_revision_id)%'
          AND indexdef ILIKE '%WHERE (candidate_fact_revision_id IS NOT NULL)%'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = current_schema()
          AND tablename = 'research_evidence_intake_item'
          AND indexname = 'uq_reii_intake_claim_draft'
          AND indexdef ILIKE '%UNIQUE%'
          AND indexdef ILIKE '%(research_evidence_intake_id, claim_draft_id)%'
          AND indexdef ILIKE '%WHERE (claim_draft_id IS NOT NULL)%'
    ) THEN
        RAISE EXCEPTION 'v53 contract violation: missing or divergent duplicate-binding indexes'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT p.oid INTO v_validate_oid
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = current_schema()
      AND p.proname = 'research_evidence_intake_validate_item_snapshot'
      AND p.pronargs = 0
      AND p.prorettype = 'trigger'::regtype
      AND p.prosecdef
      AND p.proconfig = ARRAY['search_path=pg_catalog']
      AND position('candidate_fact_revision' IN p.prosrc) > 0
      AND position('source_snapshot_id = $3' IN p.prosrc) > 0
      AND position('RETURN NEW' IN p.prosrc) > 0
      AND NOT EXISTS (
          SELECT 1
          FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) acl
          WHERE acl.grantee = 0
            AND acl.privilege_type = 'EXECUTE'
      );
    IF v_validate_oid IS NULL THEN
        RAISE EXCEPTION 'v53 contract violation: item snapshot validator is missing'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(tg_name, ', ' ORDER BY tg_name) INTO v_missing
    FROM (VALUES
        ('trg_rei_no_mutation'::text, 'research_evidence_intake'::text,
         v_reject_oid, 2 + 8 + 16, 'O'::"char"),
        ('trg_reii_no_mutation', 'research_evidence_intake_item',
         v_reject_oid, 2 + 8 + 16, 'O'::"char"),
        ('trg_reii_validate_snapshot', 'research_evidence_intake_item',
         v_validate_oid, 2 + 4, 'O'::"char")
    ) AS expected(tg_name, table_name, function_oid, required_bits, enabled)
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_trigger t
        WHERE t.tgname = expected.tg_name
          AND t.tgrelid = expected.table_name::regclass
          AND NOT t.tgisinternal
          AND t.tgfoid = expected.function_oid
          AND t.tgenabled = expected.enabled
          AND (t.tgtype & 1) = 1
          AND (t.tgtype & (2 + 4 + 8 + 16)) = expected.required_bits
    );
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v53 contract violation: missing or divergent triggers %', v_missing
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS research_evidence_intake (
    id                          UUID NOT NULL DEFAULT gen_random_uuid(),
    project_id                  UUID NOT NULL,
    source_snapshot_id          UUID NOT NULL,
    source_metadata_revision_id UUID NOT NULL,
    intake_method               TEXT NOT NULL DEFAULT 'operator_selected_existing_snapshot',
    state                       TEXT NOT NULL DEFAULT 'draft',
    selection_reason            TEXT NOT NULL,
    created_by                  TEXT NOT NULL,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT research_evidence_intake_pkey PRIMARY KEY (id),
    CONSTRAINT uq_rei_id_project UNIQUE (id, project_id),
    CONSTRAINT uq_rei_id_project_snapshot UNIQUE (id, project_id, source_snapshot_id),
    CONSTRAINT fk_rei_project
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
    CONSTRAINT fk_rei_snapshot_project
        FOREIGN KEY (source_snapshot_id, project_id)
        REFERENCES source_snapshot(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT fk_rei_source_metadata_snapshot
        FOREIGN KEY (source_metadata_revision_id, project_id, source_snapshot_id)
        REFERENCES research_source_metadata_revision(id, project_id, source_snapshot_id)
        ON DELETE RESTRICT,
    CONSTRAINT ck_rei_intake_method CHECK (
        intake_method = 'operator_selected_existing_snapshot'
    ),
    CONSTRAINT ck_rei_state_draft CHECK (state = 'draft'),
    CONSTRAINT ck_rei_selection_reason_nonblank CHECK (
        selection_reason !~ '^[[:space:]]*$'
    ),
    CONSTRAINT ck_rei_created_by_nonblank CHECK (
        created_by !~ '^[[:space:]]*$'
    )
);

CREATE INDEX IF NOT EXISTS idx_rei_project_created
    ON research_evidence_intake(project_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_rei_snapshot_created
    ON research_evidence_intake(project_id, source_snapshot_id, created_at, id);

CREATE TABLE IF NOT EXISTS research_evidence_intake_item (
    id                          UUID NOT NULL DEFAULT gen_random_uuid(),
    project_id                  UUID NOT NULL,
    research_evidence_intake_id UUID NOT NULL,
    source_snapshot_id          UUID NOT NULL,
    item_kind                   TEXT NOT NULL,
    candidate_fact_revision_id  UUID,
    fact_metadata_revision_id   UUID,
    claim_draft_id              UUID,
    state                       TEXT NOT NULL DEFAULT 'draft',
    created_by                  TEXT NOT NULL,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT research_evidence_intake_item_pkey PRIMARY KEY (id),
    CONSTRAINT uq_reii_id_project UNIQUE (id, project_id),
    CONSTRAINT fk_reii_project
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
    CONSTRAINT fk_reii_intake_snapshot
        FOREIGN KEY (research_evidence_intake_id, project_id, source_snapshot_id)
        REFERENCES research_evidence_intake(id, project_id, source_snapshot_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_reii_fact_project
        FOREIGN KEY (candidate_fact_revision_id, project_id)
        REFERENCES candidate_fact_revision(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT fk_reii_fact_metadata_fact
        FOREIGN KEY (fact_metadata_revision_id, project_id, candidate_fact_revision_id)
        REFERENCES research_fact_metadata_revision(
            id, project_id, candidate_fact_revision_id
        ) ON DELETE RESTRICT,
    CONSTRAINT fk_reii_claim_project
        FOREIGN KEY (claim_draft_id, project_id)
        REFERENCES research_claim_draft(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT ck_reii_item_kind CHECK (
        item_kind IN ('candidate_fact', 'claim_draft')
    ),
    CONSTRAINT ck_reii_state_draft CHECK (state = 'draft'),
    CONSTRAINT ck_reii_created_by_nonblank CHECK (
        char_length(btrim(created_by)) > 0
    ),
    CONSTRAINT ck_reii_target_shape CHECK (
        (
            item_kind = 'candidate_fact'
            AND candidate_fact_revision_id IS NOT NULL
            AND fact_metadata_revision_id IS NOT NULL
            AND claim_draft_id IS NULL
        )
        OR
        (
            item_kind = 'claim_draft'
            AND candidate_fact_revision_id IS NULL
            AND fact_metadata_revision_id IS NULL
            AND claim_draft_id IS NOT NULL
        )
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_reii_intake_candidate_fact
    ON research_evidence_intake_item(
        research_evidence_intake_id, candidate_fact_revision_id
    )
    WHERE candidate_fact_revision_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_reii_intake_claim_draft
    ON research_evidence_intake_item(
        research_evidence_intake_id, claim_draft_id
    )
    WHERE claim_draft_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_reii_project_intake_created
    ON research_evidence_intake_item(
        project_id, research_evidence_intake_id, created_at, id
    );

-- v47 has no unique (fact id, project id, snapshot id) key, so this one narrow
-- trigger proves that a candidate fact uses the snapshot copied from its intake.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = current_schema()
          AND p.proname = 'research_evidence_intake_validate_item_snapshot'
          AND p.pronargs = 0
    ) THEN
        EXECUTE $create_function$
            CREATE FUNCTION research_evidence_intake_validate_item_snapshot()
            RETURNS trigger
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = pg_catalog
            AS $function_body$
            DECLARE
                v_matches boolean;
            BEGIN
                IF NEW.item_kind = 'candidate_fact' THEN
                    EXECUTE format(
                        'SELECT EXISTS (
                            SELECT 1
                            FROM %I.candidate_fact_revision
                            WHERE id = $1
                              AND project_id = $2
                              AND source_snapshot_id = $3
                        )',
                        TG_TABLE_SCHEMA
                    )
                    INTO v_matches
                    USING NEW.candidate_fact_revision_id,
                          NEW.project_id,
                          NEW.source_snapshot_id;

                    IF NOT v_matches THEN
                        RAISE EXCEPTION
                            'candidate fact % does not belong to snapshot % in project %',
                            NEW.candidate_fact_revision_id,
                            NEW.source_snapshot_id,
                            NEW.project_id
                            USING ERRCODE = '23503';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $function_body$
        $create_function$;
    END IF;
END $$;

REVOKE ALL ON FUNCTION research_evidence_intake_validate_item_snapshot() FROM PUBLIC;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_rei_no_mutation'
          AND tgrelid = 'research_evidence_intake'::regclass
          AND NOT tgisinternal
    ) THEN
        CREATE TRIGGER trg_rei_no_mutation
            BEFORE UPDATE OR DELETE ON research_evidence_intake
            FOR EACH ROW EXECUTE FUNCTION slicea_reject_mutation();
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_reii_no_mutation'
          AND tgrelid = 'research_evidence_intake_item'::regclass
          AND NOT tgisinternal
    ) THEN
        CREATE TRIGGER trg_reii_no_mutation
            BEFORE UPDATE OR DELETE ON research_evidence_intake_item
            FOR EACH ROW EXECUTE FUNCTION slicea_reject_mutation();
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_reii_validate_snapshot'
          AND tgrelid = 'research_evidence_intake_item'::regclass
          AND NOT tgisinternal
    ) THEN
        CREATE TRIGGER trg_reii_validate_snapshot
            BEFORE INSERT ON research_evidence_intake_item
            FOR EACH ROW
            EXECUTE FUNCTION research_evidence_intake_validate_item_snapshot();
    END IF;
END $$;

COMMIT;
