-- v52 Research Evidence Audit Integrity
-- Additive hardening for v51 event target integrity and sequence allocation.
-- v51 history is immutable: this migration validates and preserves existing rows.

BEGIN;

-- Require the complete v51 contract and reject partial/divergent v52 state.
DO $$
DECLARE
    v_missing        text;
    v_v51_tables     integer;
    v_allocator      boolean;
    v_function       boolean;
    v_trigger        boolean;
    v_v52_objects    integer;
    v_reject_oid     oid;
    v_column_count   integer;
BEGIN
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
        RAISE EXCEPTION 'v52 requires complete v51 sidecar tables, found %', v_v51_tables
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('research_source_metadata_revision_pkey'::text),
        ('uq_rsmr_id_project'),
        ('uq_rsmr_id_project_snapshot'),
        ('research_source_metadata_revision_project_id_fkey'),
        ('fk_rsmr_snapshot_project'),
        ('fk_rsmr_supersedes_same_snapshot'),
        ('ck_rsmr_metadata_object'),
        ('research_fact_metadata_revision_pkey'),
        ('uq_rfmr_id_project'),
        ('uq_rfmr_id_project_fact'),
        ('research_fact_metadata_revision_project_id_fkey'),
        ('fk_rfmr_fact_project'),
        ('fk_rfmr_supersedes_fact_project'),
        ('fk_rfmr_supersedes_same_fact'),
        ('ck_rfmr_metadata_object'),
        ('research_claim_draft_pkey'),
        ('uq_rcd_id_project'),
        ('research_claim_draft_project_id_fkey'),
        ('fk_rcd_supersedes_claim_project'),
        ('ck_rcd_claim_text_present'),
        ('research_evidence_event_pkey'),
        ('research_evidence_event_project_id_fkey'),
        ('uq_ree_entity_sequence'),
        ('ck_ree_entity_type'),
        ('ck_ree_event_type'),
        ('ck_ree_details_object')
    ) AS expected(name)
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        WHERE con.connamespace = current_schema()::regnamespace
          AND con.conname = expected.name
    );
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v52 requires complete v51 constraints: missing %', v_missing
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT p.oid INTO v_reject_oid
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = current_schema()
      AND p.proname = 'slicea_reject_mutation'
      AND p.pronargs = 0;
    IF v_reject_oid IS NULL THEN
        RAISE EXCEPTION 'v52 requires v51 dependency slicea_reject_mutation()'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(tg_name, ', ' ORDER BY tg_name) INTO v_missing
    FROM (VALUES
        ('trg_rsmr_no_mutation'::text, 'research_source_metadata_revision'::text),
        ('trg_rfmr_no_mutation', 'research_fact_metadata_revision'),
        ('trg_rcd_no_mutation', 'research_claim_draft'),
        ('trg_ree_no_mutation', 'research_evidence_event')
    ) AS expected(tg_name, table_name)
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = current_schema()
          AND c.relname = expected.table_name
          AND t.tgname = expected.tg_name
          AND NOT t.tgisinternal
          AND t.tgfoid = v_reject_oid
          AND (t.tgtype & 1) = 1
          AND (t.tgtype & 2) = 2
          AND (t.tgtype & 8) = 8
          AND (t.tgtype & 16) = 16
          AND (t.tgtype & 4) = 0
    );
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v52 requires complete v51 triggers: missing %', v_missing
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT to_regclass(
        format('%I.research_evidence_event_sequence_allocator', current_schema())
    ) IS NOT NULL INTO v_allocator;
    SELECT EXISTS (
        SELECT 1
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = current_schema()
          AND p.proname = 'research_evidence_prepare_event_insert'
          AND p.pronargs = 0
    ) INTO v_function;
    SELECT EXISTS (
        SELECT 1
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = current_schema()
          AND c.relname = 'research_evidence_event'
          AND t.tgname = 'trg_ree_prepare_insert'
          AND NOT t.tgisinternal
    ) INTO v_trigger;

    v_v52_objects :=
        v_allocator::integer + v_function::integer + v_trigger::integer;
    IF v_v52_objects NOT IN (0, 3) THEN
        RAISE EXCEPTION
            'v52 contract violation: partial/divergent state (allocator %, function %, trigger %)',
            v_allocator, v_function, v_trigger
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF v_v52_objects = 3 THEN
        SELECT count(*) INTO v_column_count
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'research_evidence_event_sequence_allocator';
        IF v_column_count <> 4 OR EXISTS (
            SELECT 1
            FROM (VALUES
                ('project_id'::text, 'uuid'::text, 'NO'::text),
                ('entity_type', 'text', 'NO'),
                ('entity_id', 'uuid', 'NO'),
                ('last_sequence', 'integer', 'NO')
            ) AS expected(column_name, data_type, is_nullable)
            LEFT JOIN information_schema.columns c
              ON c.table_schema = current_schema()
             AND c.table_name = 'research_evidence_event_sequence_allocator'
             AND c.column_name = expected.column_name
            WHERE c.column_name IS NULL
               OR c.data_type <> expected.data_type
               OR c.is_nullable <> expected.is_nullable
        ) THEN
            RAISE EXCEPTION 'v52 contract violation: divergent allocator columns'
                USING ERRCODE = 'invalid_schema_definition';
        END IF;

        SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
        FROM (VALUES
            ('research_evidence_event_sequence_allocator_pkey'::text),
            ('fk_reesa_project'),
            ('ck_reesa_entity_type'),
            ('ck_reesa_last_sequence')
        ) AS expected(name)
        WHERE NOT EXISTS (
            SELECT 1
            FROM pg_constraint con
            WHERE con.connamespace = current_schema()::regnamespace
              AND con.conname = expected.name
        );
        IF v_missing IS NOT NULL THEN
            RAISE EXCEPTION 'v52 contract violation: missing allocator constraints %', v_missing
                USING ERRCODE = 'invalid_schema_definition';
        END IF;

        IF NOT EXISTS (
            SELECT 1
            FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_proc p ON p.oid = t.tgfoid
            WHERE n.nspname = current_schema()
              AND c.relname = 'research_evidence_event'
              AND t.tgname = 'trg_ree_prepare_insert'
              AND p.proname = 'research_evidence_prepare_event_insert'
              AND p.prorettype = 'trigger'::regtype
              AND t.tgenabled = 'A'
              AND (t.tgtype & 1) = 1
              AND (t.tgtype & 2) = 2
              AND (t.tgtype & 4) = 4
        ) THEN
            RAISE EXCEPTION 'v52 contract violation: invalid allocator trigger'
                USING ERRCODE = 'invalid_schema_definition';
        END IF;
    END IF;
END $$;

LOCK TABLE research_evidence_event IN SHARE ROW EXCLUSIVE MODE;

-- v51 allowed caller-authored polymorphic targets. Refuse invalid history rather
-- than repairing, deleting, resequencing, or inventing audit events.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM research_evidence_event
        WHERE entity_type NOT IN (
            'source_metadata_revision', 'fact_metadata_revision', 'claim_draft'
        )
    ) THEN
        RAISE EXCEPTION 'v52 refuses existing research events with invalid entity types'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM research_evidence_event e
        WHERE
            (e.entity_type = 'source_metadata_revision' AND NOT EXISTS (
                SELECT 1
                FROM research_source_metadata_revision r
                WHERE r.id = e.entity_id AND r.project_id = e.project_id
            ))
            OR
            (e.entity_type = 'fact_metadata_revision' AND NOT EXISTS (
                SELECT 1
                FROM research_fact_metadata_revision r
                WHERE r.id = e.entity_id AND r.project_id = e.project_id
            ))
            OR
            (e.entity_type = 'claim_draft' AND NOT EXISTS (
                SELECT 1
                FROM research_claim_draft r
                WHERE r.id = e.entity_id AND r.project_id = e.project_id
            ))
    ) THEN
        RAISE EXCEPTION 'v52 refuses existing orphan or cross-project research events'
            USING ERRCODE = '23503';
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS research_evidence_event_sequence_allocator (
    project_id    UUID NOT NULL,
    entity_type   TEXT NOT NULL,
    entity_id     UUID NOT NULL,
    last_sequence INTEGER NOT NULL,
    CONSTRAINT research_evidence_event_sequence_allocator_pkey
        PRIMARY KEY (project_id, entity_type, entity_id),
    CONSTRAINT fk_reesa_project
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
    CONSTRAINT ck_reesa_entity_type CHECK (
        entity_type IN ('source_metadata_revision', 'fact_metadata_revision', 'claim_draft')
    ),
    CONSTRAINT ck_reesa_last_sequence CHECK (last_sequence >= 1)
);

-- On reapplication the allocator must exactly represent committed event history.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM (
            SELECT project_id, entity_type, entity_id, max(event_sequence) AS last_sequence
            FROM research_evidence_event
            GROUP BY project_id, entity_type, entity_id
        ) e
        FULL JOIN research_evidence_event_sequence_allocator a
          USING (project_id, entity_type, entity_id)
        WHERE e.last_sequence IS DISTINCT FROM a.last_sequence
    ) AND EXISTS (
        SELECT 1
        FROM pg_trigger
        WHERE tgname = 'trg_ree_prepare_insert'
          AND tgrelid = 'research_evidence_event'::regclass
          AND NOT tgisinternal
    ) THEN
        RAISE EXCEPTION 'v52 contract violation: allocator diverges from event history'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
END $$;

INSERT INTO research_evidence_event_sequence_allocator
    (project_id, entity_type, entity_id, last_sequence)
SELECT project_id, entity_type, entity_id, max(event_sequence)
FROM research_evidence_event
GROUP BY project_id, entity_type, entity_id
ON CONFLICT (project_id, entity_type, entity_id) DO NOTHING;

CREATE OR REPLACE FUNCTION research_evidence_prepare_event_insert()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_target_exists boolean;
    v_sequence      integer;
BEGIN
    CASE NEW.entity_type
        WHEN 'source_metadata_revision' THEN
            EXECUTE format(
                'SELECT EXISTS (
                    SELECT 1 FROM %I.research_source_metadata_revision
                    WHERE id = $1 AND project_id = $2
                )',
                TG_TABLE_SCHEMA
            ) INTO v_target_exists USING NEW.entity_id, NEW.project_id;
        WHEN 'fact_metadata_revision' THEN
            EXECUTE format(
                'SELECT EXISTS (
                    SELECT 1 FROM %I.research_fact_metadata_revision
                    WHERE id = $1 AND project_id = $2
                )',
                TG_TABLE_SCHEMA
            ) INTO v_target_exists USING NEW.entity_id, NEW.project_id;
        WHEN 'claim_draft' THEN
            EXECUTE format(
                'SELECT EXISTS (
                    SELECT 1 FROM %I.research_claim_draft
                    WHERE id = $1 AND project_id = $2
                )',
                TG_TABLE_SCHEMA
            ) INTO v_target_exists USING NEW.entity_id, NEW.project_id;
        ELSE
            RAISE EXCEPTION 'invalid research evidence entity type: %', NEW.entity_type
                USING ERRCODE = '23514';
    END CASE;

    IF NOT v_target_exists THEN
        RAISE EXCEPTION
            'research evidence target % (%) does not exist in project %',
            NEW.entity_type, NEW.entity_id, NEW.project_id
            USING ERRCODE = '23503';
    END IF;

    EXECUTE format(
        'INSERT INTO %I.research_evidence_event_sequence_allocator AS allocator
            (project_id, entity_type, entity_id, last_sequence)
         VALUES ($1, $2, $3, 1)
         ON CONFLICT (project_id, entity_type, entity_id)
         DO UPDATE SET last_sequence = allocator.last_sequence + 1
         RETURNING last_sequence',
        TG_TABLE_SCHEMA
    ) INTO v_sequence USING NEW.project_id, NEW.entity_type, NEW.entity_id;

    NEW.event_sequence := v_sequence;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION research_evidence_prepare_event_insert() FROM PUBLIC;
REVOKE ALL ON TABLE research_evidence_event_sequence_allocator FROM PUBLIC;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_trigger
        WHERE tgname = 'trg_ree_prepare_insert'
          AND tgrelid = 'research_evidence_event'::regclass
          AND NOT tgisinternal
    ) THEN
        CREATE TRIGGER trg_ree_prepare_insert
            BEFORE INSERT ON research_evidence_event
            FOR EACH ROW
            EXECUTE FUNCTION research_evidence_prepare_event_insert();
    END IF;
END $$;

ALTER TABLE research_evidence_event
    ENABLE ALWAYS TRIGGER trg_ree_prepare_insert;

COMMIT;
