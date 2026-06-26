-- v51 Research Evidence Metadata Sidecar Foundation (R1.1)
-- Additive, append-only operator-declared metadata around existing immutable
-- Slice A source_snapshot and candidate_fact_revision records.
--
-- Scope: research_source_metadata_revision, research_fact_metadata_revision,
-- research_claim_draft, research_evidence_event.
--
-- Non-goals:
--   * no new blob, snapshot, fact, calculation, retention, report, scenario,
--     prompt, retrieval, or source-capture system
--   * no source content, quotations, backfill, seed data, views, runtime schema
--     mutation, or migration runner
--   * no references to later vertical tables
--
-- Apply manually AFTER sql/init.sql, sql/outcomes.sql, and
-- sql/v47_evidence_snapshot_foundation.sql:
--   psql -U workflow -d workflow_v4 -v ON_ERROR_STOP=1 -f sql/v51_research_evidence_sidecar_foundation.sql

BEGIN;

-- ═══ Preflight: require v47 and refuse partial / divergent sidecar schema ═══
DO $$
DECLARE
    v_v47_tables int;
    v_tables     int;
    v_present    boolean;
    v_fn_oid     oid;
    v_missing    text;
BEGIN
    SELECT count(*) INTO v_v47_tables
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'r'
      AND n.nspname = current_schema()
      AND c.relname = ANY (ARRAY[
          'source_blob', 'source_snapshot', 'candidate_fact_revision',
          'evidence_retention_event', 'ingest_operation']);
    IF v_v47_tables <> 5 THEN
        RAISE EXCEPTION 'v51 requires complete v47 Slice A tables, found %', v_v47_tables
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT p.oid INTO v_fn_oid
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = current_schema() AND p.proname = 'slicea_reject_mutation';
    IF v_fn_oid IS NULL THEN
        RAISE EXCEPTION 'v51 requires v47: slicea_reject_mutation() is missing'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing
    FROM (VALUES
        ('uq_source_blob_id_project'::text),
        ('uq_source_snapshot_id_project'),
        ('uq_cfr_id_project'),
        ('ck_retention_single_target')
    ) AS expected(name)
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        WHERE con.connamespace = current_schema()::regnamespace
          AND con.conname = expected.name
    );
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v51 requires v47 constraints: missing %', v_missing
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT count(*) INTO v_tables
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'r'
      AND n.nspname = current_schema()
      AND c.relname = ANY (ARRAY[
          'research_source_metadata_revision',
          'research_fact_metadata_revision',
          'research_claim_draft',
          'research_evidence_event']);

    SELECT (v_tables > 0)
        OR EXISTS (
            SELECT 1
            FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = current_schema()
              AND t.tgname = ANY (ARRAY[
                  'trg_rsmr_no_mutation',
                  'trg_rfmr_no_mutation',
                  'trg_rcd_no_mutation',
                  'trg_ree_no_mutation']))
        OR EXISTS (
            SELECT 1
            FROM pg_constraint con
            JOIN pg_namespace n ON n.oid = con.connamespace
            WHERE n.nspname = current_schema()
              AND con.conname = ANY (ARRAY[
                  'uq_rsmr_id_project', 'uq_rsmr_id_project_snapshot',
                  'fk_rsmr_snapshot_project', 'fk_rsmr_supersedes_same_snapshot',
                  'ck_rsmr_metadata_object',
                  'uq_rfmr_id_project', 'uq_rfmr_id_project_fact',
                  'fk_rfmr_fact_project', 'fk_rfmr_supersedes_fact_project',
                  'fk_rfmr_supersedes_same_fact', 'ck_rfmr_metadata_object',
                  'uq_rcd_id_project', 'fk_rcd_supersedes_claim_project',
                  'ck_rcd_claim_text_present',
                  'uq_ree_entity_sequence', 'ck_ree_entity_type',
                  'ck_ree_event_type', 'ck_ree_details_object']))
        INTO v_present;

    IF NOT v_present THEN
        RETURN;
    END IF;

    IF v_tables <> 4 THEN
        RAISE EXCEPTION
            'v51 contract violation: expected 4 research sidecar tables, found % — schema is partial/divergent',
            v_tables USING ERRCODE = 'invalid_schema_definition';
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
        RAISE EXCEPTION 'v51 contract violation: missing sidecar constraints %', v_missing
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
        WHERE t.tgname = expected.tg_name
          AND t.tgrelid = (current_schema() || '.' || expected.table_name)::regclass
          AND NOT t.tgisinternal
          AND (t.tgtype & 1) = 1
          AND (t.tgtype & 2) = 2
          AND (t.tgtype & 8) = 8
          AND (t.tgtype & 16) = 16
          AND (t.tgtype & 4) = 0
          AND t.tgfoid = v_fn_oid
    );
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'v51 contract violation: missing or invalid sidecar triggers %', v_missing
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS research_source_metadata_revision (
    id                              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id                      UUID NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    source_snapshot_id              UUID NOT NULL,
    canonical_source_locator        TEXT NOT NULL DEFAULT '',
    publisher                       TEXT NOT NULL DEFAULT '',
    author                          TEXT NOT NULL DEFAULT '',
    published_at                    TIMESTAMPTZ,
    retrieved_at                    TIMESTAMPTZ,
    citation_label                  TEXT NOT NULL DEFAULT '',
    declared_quality_tier           TEXT NOT NULL DEFAULT '',
    declared_quality_rationale      TEXT NOT NULL DEFAULT '',
    metadata_json                   JSONB NOT NULL DEFAULT '{}'::jsonb,
    supersedes_metadata_revision_id UUID,
    created_by                      TEXT NOT NULL DEFAULT '',
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_rsmr_id_project UNIQUE (id, project_id),
    CONSTRAINT uq_rsmr_id_project_snapshot UNIQUE (id, project_id, source_snapshot_id),
    CONSTRAINT fk_rsmr_snapshot_project
        FOREIGN KEY (source_snapshot_id, project_id)
        REFERENCES source_snapshot(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT fk_rsmr_supersedes_same_snapshot
        FOREIGN KEY (supersedes_metadata_revision_id, project_id, source_snapshot_id)
        REFERENCES research_source_metadata_revision(id, project_id, source_snapshot_id)
        ON DELETE RESTRICT,
    CONSTRAINT ck_rsmr_metadata_object CHECK (jsonb_typeof(metadata_json) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_rsmr_project_created
    ON research_source_metadata_revision(project_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_rsmr_snapshot_created
    ON research_source_metadata_revision(project_id, source_snapshot_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_rsmr_supersedes
    ON research_source_metadata_revision(project_id, supersedes_metadata_revision_id);

CREATE TABLE IF NOT EXISTS research_fact_metadata_revision (
    id                                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id                              UUID NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    candidate_fact_revision_id              UUID NOT NULL,
    stable_fact_key                         TEXT NOT NULL DEFAULT '',
    drift_group_key                         TEXT NOT NULL DEFAULT '',
    supersedes_candidate_fact_revision_id   UUID,
    source_char_range                       TEXT,
    excerpt_hash                            TEXT NOT NULL DEFAULT '',
    citation_locator                        TEXT NOT NULL DEFAULT '',
    metadata_json                           JSONB NOT NULL DEFAULT '{}'::jsonb,
    supersedes_metadata_revision_id         UUID,
    created_by                              TEXT NOT NULL DEFAULT '',
    created_at                              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_rfmr_id_project UNIQUE (id, project_id),
    CONSTRAINT uq_rfmr_id_project_fact UNIQUE (id, project_id, candidate_fact_revision_id),
    CONSTRAINT fk_rfmr_fact_project
        FOREIGN KEY (candidate_fact_revision_id, project_id)
        REFERENCES candidate_fact_revision(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT fk_rfmr_supersedes_fact_project
        FOREIGN KEY (supersedes_candidate_fact_revision_id, project_id)
        REFERENCES candidate_fact_revision(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT fk_rfmr_supersedes_same_fact
        FOREIGN KEY (supersedes_metadata_revision_id, project_id, candidate_fact_revision_id)
        REFERENCES research_fact_metadata_revision(id, project_id, candidate_fact_revision_id)
        ON DELETE RESTRICT,
    CONSTRAINT ck_rfmr_metadata_object CHECK (jsonb_typeof(metadata_json) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_rfmr_project_created
    ON research_fact_metadata_revision(project_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_rfmr_fact_created
    ON research_fact_metadata_revision(project_id, candidate_fact_revision_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_rfmr_supersedes_fact
    ON research_fact_metadata_revision(project_id, supersedes_candidate_fact_revision_id);
CREATE INDEX IF NOT EXISTS idx_rfmr_supersedes_metadata
    ON research_fact_metadata_revision(project_id, supersedes_metadata_revision_id);

CREATE TABLE IF NOT EXISTS research_claim_draft (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    claim_text          TEXT NOT NULL,
    claim_category      TEXT NOT NULL DEFAULT '',
    supersedes_claim_id UUID,
    created_by          TEXT NOT NULL DEFAULT '',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_rcd_id_project UNIQUE (id, project_id),
    CONSTRAINT fk_rcd_supersedes_claim_project
        FOREIGN KEY (supersedes_claim_id, project_id)
        REFERENCES research_claim_draft(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT ck_rcd_claim_text_present CHECK (char_length(claim_text) > 0)
);

CREATE INDEX IF NOT EXISTS idx_rcd_project_created
    ON research_claim_draft(project_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_rcd_supersedes
    ON research_claim_draft(project_id, supersedes_claim_id);

CREATE TABLE IF NOT EXISTS research_evidence_event (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id     UUID NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    entity_type    TEXT NOT NULL,
    entity_id      UUID NOT NULL,
    event_type     TEXT NOT NULL,
    event_sequence INTEGER NOT NULL CHECK (event_sequence >= 1),
    occurred_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor          TEXT NOT NULL DEFAULT '',
    details_json   JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT uq_ree_entity_sequence UNIQUE (project_id, entity_type, entity_id, event_sequence),
    CONSTRAINT ck_ree_entity_type CHECK (
        entity_type IN ('source_metadata_revision', 'fact_metadata_revision', 'claim_draft')
    ),
    CONSTRAINT ck_ree_event_type CHECK (
        event_type IN ('created', 'superseded', 'correction_recorded', 'withdrawn')
    ),
    CONSTRAINT ck_ree_details_object CHECK (jsonb_typeof(details_json) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_ree_project_occurred
    ON research_evidence_event(project_id, occurred_at, id);
CREATE INDEX IF NOT EXISTS idx_ree_entity_sequence
    ON research_evidence_event(project_id, entity_type, entity_id, event_sequence);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_rsmr_no_mutation'
          AND tgrelid = 'research_source_metadata_revision'::regclass
    ) THEN
        CREATE TRIGGER trg_rsmr_no_mutation
            BEFORE UPDATE OR DELETE ON research_source_metadata_revision
            FOR EACH ROW EXECUTE FUNCTION slicea_reject_mutation();
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_rfmr_no_mutation'
          AND tgrelid = 'research_fact_metadata_revision'::regclass
    ) THEN
        CREATE TRIGGER trg_rfmr_no_mutation
            BEFORE UPDATE OR DELETE ON research_fact_metadata_revision
            FOR EACH ROW EXECUTE FUNCTION slicea_reject_mutation();
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_rcd_no_mutation'
          AND tgrelid = 'research_claim_draft'::regclass
    ) THEN
        CREATE TRIGGER trg_rcd_no_mutation
            BEFORE UPDATE OR DELETE ON research_claim_draft
            FOR EACH ROW EXECUTE FUNCTION slicea_reject_mutation();
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_ree_no_mutation'
          AND tgrelid = 'research_evidence_event'::regclass
    ) THEN
        CREATE TRIGGER trg_ree_no_mutation
            BEFORE UPDATE OR DELETE ON research_evidence_event
            FOR EACH ROW EXECUTE FUNCTION slicea_reject_mutation();
    END IF;
END $$;

COMMIT;
