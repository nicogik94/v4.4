-- v4.7 Evidence Snapshot Foundation (Slice A)
-- Additive source-capture persistence for future evidence-backed calculations.
--
-- Scope: SourceBlob, SourceSnapshot, CandidateFactRevision, EvidenceRetentionEvent,
-- IngestOperation. No scenario assumptions, approvals, calculations, manifests,
-- dashboards, APIs, or external-data capability.
--
-- Properties:
--   * additive only — no ALTER/DROP of existing application tables
--   * one explicit transaction boundary
--   * a preflight that rejects a partial/divergent Slice A schema, while keeping
--     clean bootstrap valid and a complete re-apply a no-op
--   * project-consistent composite foreign keys so direct SQL cannot create
--     cross-project links
--   * append-only enforcement via BEFORE UPDATE/DELETE triggers on the four
--     immutable tables (IngestOperation is intentionally mutable)
--   * restrictive foreign keys (ON DELETE RESTRICT) — project deletion is rejected
--     while retained Slice A evidence exists
--   * no CREATE INDEX CONCURRENTLY
--
-- Apply manually AFTER sql/init.sql and sql/outcomes.sql:
--   psql -U workflow -d workflow_v4 -v ON_ERROR_STOP=1 -f sql/v47_evidence_snapshot_foundation.sql

BEGIN;

-- ═══ Preflight: refuse partial / divergent Slice A schema ═══
-- Classifies the current schema as none (clean bootstrap → proceed), complete
-- (matching the v47 contract → downstream guarded DDL is a no-op), or
-- partial/divergent (→ fail clearly). This runs inside the transaction so a
-- divergent schema causes the whole migration to roll back. Unknown partial
-- schema is never silently repaired.
DO $$
DECLARE
    v_tables   int;
    v_present  boolean;
    v_fn_oid   oid;
    r          record;
    v_cols     text[];
BEGIN
    -- Count the five Slice A tables in the current schema.
    SELECT count(*) INTO v_tables
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'r'
      AND n.nspname = current_schema()
      AND c.relname = ANY (ARRAY[
          'source_blob', 'source_snapshot', 'candidate_fact_revision',
          'evidence_retention_event', 'ingest_operation']);

    -- Presence: do ANY Slice A objects (table, function, trigger, or named
    -- constraint) already exist in this schema?
    SELECT (v_tables > 0)
        OR EXISTS (
            SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = current_schema() AND p.proname = 'slicea_reject_mutation')
        OR EXISTS (
            SELECT 1 FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = current_schema()
              AND t.tgname = ANY (ARRAY[
                  'trg_source_blob_no_mutation', 'trg_source_snapshot_no_mutation',
                  'trg_cfr_no_mutation', 'trg_retention_no_mutation']))
        OR EXISTS (
            SELECT 1 FROM pg_constraint con JOIN pg_namespace n ON n.oid = con.connamespace
            WHERE n.nspname = current_schema()
              AND con.conname = ANY (ARRAY[
                  'uq_source_blob_id_project', 'uq_source_snapshot_id_project', 'uq_cfr_id_project',
                  'ck_retention_single_target', 'fk_snapshot_blob_project', 'fk_cfr_snapshot_project',
                  'fk_ingest_snapshot_project', 'fk_ret_blob_project', 'fk_ret_snapshot_project',
                  'fk_ret_fact_project']))
        INTO v_present;

    IF NOT v_present THEN
        -- Clean bootstrap: nothing exists yet; proceed to create everything.
        RETURN;
    END IF;

    -- Something exists: the schema must satisfy the full v47 contract by
    -- definition (not merely by object name) or the migration is refused.
    -- Unknown partial/divergent schema is never silently repaired.

    -- All five tables must exist.
    IF v_tables <> 5 THEN
        RAISE EXCEPTION
            'v47 contract violation: expected 5 Slice A tables, found % — schema is partial/divergent',
            v_tables USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- The append-only guard function must exist.
    SELECT p.oid INTO v_fn_oid
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = current_schema() AND p.proname = 'slicea_reject_mutation';
    IF v_fn_oid IS NULL THEN
        RAISE EXCEPTION 'v47 contract violation: slicea_reject_mutation() is missing'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- The three composite unique constraints must reference exactly (id, project_id).
    FOR r IN
        SELECT * FROM (VALUES
            ('uq_source_blob_id_project',     'source_blob'),
            ('uq_source_snapshot_id_project', 'source_snapshot'),
            ('uq_cfr_id_project',             'candidate_fact_revision')
        ) AS x(cn, tbl)
    LOOP
        SELECT array_agg(a.attname::text ORDER BY a.attname) INTO v_cols
        FROM pg_constraint con
        JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = ANY (con.conkey)
        WHERE con.conname = r.cn
          AND con.contype = 'u'
          AND con.conrelid = (current_schema() || '.' || r.tbl)::regclass;
        IF v_cols IS DISTINCT FROM ARRAY['id', 'project_id'] THEN
            RAISE EXCEPTION
                'v47 contract violation: unique constraint % on % must be (id, project_id), found %',
                r.cn, r.tbl, v_cols USING ERRCODE = 'invalid_schema_definition';
        END IF;
    END LOOP;

    -- The six project-consistent composite foreign keys must exist on the
    -- intended tables/columns and reference parent (id, project_id).
    FOR r IN
        SELECT * FROM (VALUES
            ('fk_snapshot_blob_project',    'source_snapshot',          'source_blob',             ARRAY['project_id', 'source_blob_id']),
            ('fk_cfr_snapshot_project',     'candidate_fact_revision',  'source_snapshot',         ARRAY['project_id', 'source_snapshot_id']),
            ('fk_ingest_snapshot_project',  'ingest_operation',         'source_snapshot',         ARRAY['project_id', 'source_snapshot_id']),
            ('fk_ret_blob_project',         'evidence_retention_event', 'source_blob',             ARRAY['project_id', 'source_blob_id']),
            ('fk_ret_snapshot_project',     'evidence_retention_event', 'source_snapshot',         ARRAY['project_id', 'source_snapshot_id']),
            ('fk_ret_fact_project',         'evidence_retention_event', 'candidate_fact_revision', ARRAY['candidate_fact_revision_id', 'project_id'])
        ) AS x(cn, tbl, reftbl, lcols)
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint con
            WHERE con.conname = r.cn
              AND con.contype = 'f'
              AND con.conrelid = (current_schema() || '.' || r.tbl)::regclass
              AND con.confrelid = (current_schema() || '.' || r.reftbl)::regclass
              AND (SELECT array_agg(a.attname::text ORDER BY a.attname)
                   FROM pg_attribute a
                   WHERE a.attrelid = con.conrelid AND a.attnum = ANY (con.conkey)) = r.lcols
              AND (SELECT array_agg(a.attname::text ORDER BY a.attname)
                   FROM pg_attribute a
                   WHERE a.attrelid = con.confrelid AND a.attnum = ANY (con.confkey)) = ARRAY['id', 'project_id']
        ) THEN
            RAISE EXCEPTION
                'v47 contract violation: composite FK % on % -> % is missing or has wrong columns',
                r.cn, r.tbl, r.reftbl USING ERRCODE = 'invalid_schema_definition';
        END IF;
    END LOOP;

    -- The four append-only triggers must be BEFORE UPDATE OR DELETE row triggers
    -- attached to the correct tables and invoking slicea_reject_mutation().
    FOR r IN
        SELECT * FROM (VALUES
            ('trg_source_blob_no_mutation',     'source_blob'),
            ('trg_source_snapshot_no_mutation', 'source_snapshot'),
            ('trg_cfr_no_mutation',             'candidate_fact_revision'),
            ('trg_retention_no_mutation',       'evidence_retention_event')
        ) AS x(tg, tbl)
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_trigger t
            WHERE t.tgname = r.tg
              AND t.tgrelid = (current_schema() || '.' || r.tbl)::regclass
              AND NOT t.tgisinternal
              AND (t.tgtype & 1) = 1    -- row-level
              AND (t.tgtype & 2) = 2    -- BEFORE
              AND (t.tgtype & 8) = 8    -- DELETE
              AND (t.tgtype & 16) = 16  -- UPDATE
              AND (t.tgtype & 4) = 0    -- not INSERT
              AND t.tgfoid = v_fn_oid   -- invokes slicea_reject_mutation()
        ) THEN
            RAISE EXCEPTION
                'v47 contract violation: append-only trigger % on % is missing or has wrong definition '
                '(must be BEFORE UPDATE OR DELETE FOR EACH ROW EXECUTE slicea_reject_mutation)',
                r.tg, r.tbl USING ERRCODE = 'invalid_schema_definition';
        END IF;
    END LOOP;

    -- The retention XOR check constraint must exist on evidence_retention_event.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint con
        WHERE con.conname = 'ck_retention_single_target'
          AND con.contype = 'c'
          AND con.conrelid = (current_schema() || '.evidence_retention_event')::regclass
    ) THEN
        RAISE EXCEPTION 'v47 contract violation: ck_retention_single_target (XOR) is missing'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- All contract checks passed: schema is complete; downstream guarded DDL is a no-op.
END $$;

-- ═══ Append-only guard function ═══
-- Shared trigger function: rejects any UPDATE or DELETE on immutable tables.
-- CREATE OR REPLACE is idempotent and does not drop the existing function.
CREATE OR REPLACE FUNCTION slicea_reject_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Slice A record is append-only; % on % is not permitted',
        TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;

-- ═══ SourceBlob — logical content identity only ═══
-- Owns NO storage_ref, source locator, capture time, or per-upload metadata.
-- Deduplicated only within a project. The (id, project_id) unique constraint is
-- the target for project-consistent composite foreign keys.
CREATE TABLE IF NOT EXISTS source_blob (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id     UUID NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    hash_algorithm TEXT NOT NULL,
    content_hash   TEXT NOT NULL,
    byte_size      BIGINT NOT NULL CHECK (byte_size >= 0),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by     TEXT NOT NULL DEFAULT '',
    CONSTRAINT uq_source_blob_project_content
        UNIQUE (project_id, hash_algorithm, content_hash),
    CONSTRAINT uq_source_blob_id_project UNIQUE (id, project_id)
);

-- ═══ SourceSnapshot — one immutable capture event ═══
-- Owns the capture-specific storage_ref, source context, locator, captured_at,
-- and ingestion context. References exactly one SourceBlob, project-consistently.
-- Never deduplicated by content hash.
CREATE TABLE IF NOT EXISTS source_snapshot (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_blob_id      UUID NOT NULL,
    project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    storage_ref         TEXT NOT NULL,
    source_kind         TEXT NOT NULL DEFAULT '',
    source_locator      TEXT NOT NULL DEFAULT '',
    ingest_operation_id TEXT NOT NULL DEFAULT '',
    captured_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    captured_by         TEXT NOT NULL DEFAULT '',
    CONSTRAINT uq_source_snapshot_id_project UNIQUE (id, project_id),
    CONSTRAINT fk_snapshot_blob_project
        FOREIGN KEY (source_blob_id, project_id)
        REFERENCES source_blob(id, project_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_source_snapshot_blob ON source_snapshot(source_blob_id);
CREATE INDEX IF NOT EXISTS idx_source_snapshot_project ON source_snapshot(project_id);
CREATE INDEX IF NOT EXISTS idx_source_snapshot_storage_ref ON source_snapshot(storage_ref);

-- ═══ IngestOperation — operational retry/capture state (mutable status) ═══
-- The only Slice A record permitted to transition status. Retry idempotency uses
-- a project-scoped stable operation id. When a snapshot is linked it must belong
-- to the same project (composite FK; NULL snapshot is unconstrained).
CREATE TABLE IF NOT EXISTS ingest_operation (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id         UUID NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    operation_id       TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'committed', 'failed', 'skipped_not_capturable')),
    source_snapshot_id UUID,
    detail             TEXT NOT NULL DEFAULT '',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_ingest_operation_project_op UNIQUE (project_id, operation_id),
    CONSTRAINT fk_ingest_snapshot_project
        FOREIGN KEY (source_snapshot_id, project_id)
        REFERENCES source_snapshot(id, project_id) ON DELETE RESTRICT
);

-- ═══ CandidateFactRevision — direct, typed, source-derived fact only ═══
-- Must reference exactly one SourceSnapshot (NOT NULL), project-consistently.
-- Numeric storage is NUMERIC (Decimal-compatible), never float. Text/categorical
-- facts never carry a numeric value.
CREATE TABLE IF NOT EXISTS candidate_fact_revision (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    source_snapshot_id  UUID NOT NULL,
    fact_type           TEXT NOT NULL
                        CHECK (fact_type IN ('money', 'rate', 'percentage', 'duration', 'count', 'categorical', 'text')),
    numeric_value       NUMERIC,
    text_value          TEXT,
    unit                TEXT NOT NULL DEFAULT '',
    currency_code       TEXT,
    as_of_date          DATE,
    numerator_context   TEXT,
    denominator_context TEXT,
    percentage_basis    TEXT,
    percentage_subtype  TEXT,
    time_unit           TEXT,
    counted_entity      TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by          TEXT NOT NULL DEFAULT '',
    CONSTRAINT uq_cfr_id_project UNIQUE (id, project_id),
    CONSTRAINT fk_cfr_snapshot_project
        FOREIGN KEY (source_snapshot_id, project_id)
        REFERENCES source_snapshot(id, project_id) ON DELETE RESTRICT,
    -- numeric types carry a numeric value; text/categorical never do
    CONSTRAINT ck_cfr_numeric_shape CHECK (
        (fact_type IN ('categorical', 'text') AND numeric_value IS NULL)
        OR (fact_type IN ('money', 'rate', 'percentage', 'duration', 'count') AND numeric_value IS NOT NULL)
    ),
    -- count values must be integral
    CONSTRAINT ck_cfr_count_integral CHECK (
        fact_type <> 'count' OR numeric_value = trunc(numeric_value)
    ),
    -- money requires an ISO-4217-shaped currency code and an as-of date
    CONSTRAINT ck_cfr_money_currency CHECK (
        fact_type <> 'money' OR (currency_code IS NOT NULL AND char_length(currency_code) = 3)
    ),
    CONSTRAINT ck_cfr_money_as_of CHECK (
        fact_type <> 'money' OR as_of_date IS NOT NULL
    ),
    -- categorical/text require textual evidence
    CONSTRAINT ck_cfr_text_value CHECK (
        fact_type NOT IN ('categorical', 'text') OR (text_value IS NOT NULL AND char_length(text_value) > 0)
    )
);

CREATE INDEX IF NOT EXISTS idx_cfr_snapshot ON candidate_fact_revision(source_snapshot_id);
CREATE INDEX IF NOT EXISTS idx_cfr_project ON candidate_fact_revision(project_id);

-- ═══ EvidenceRetentionEvent — append-only logical retention event ═══
-- Targets exactly one of blob / snapshot / fact (three explicit FKs + XOR check),
-- each project-consistent with the event. legal_hold blocks future physical purge
-- only; tombstone/redact block future use through the availability resolver.
CREATE TABLE IF NOT EXISTS evidence_retention_event (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id                 UUID NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    event_type                 TEXT NOT NULL CHECK (event_type IN ('legal_hold', 'tombstone', 'redact')),
    source_blob_id             UUID,
    source_snapshot_id         UUID,
    candidate_fact_revision_id UUID,
    reason                     TEXT NOT NULL DEFAULT '',
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by                 TEXT NOT NULL DEFAULT '',
    CONSTRAINT ck_retention_single_target CHECK (
        (CASE WHEN source_blob_id IS NOT NULL THEN 1 ELSE 0 END)
        + (CASE WHEN source_snapshot_id IS NOT NULL THEN 1 ELSE 0 END)
        + (CASE WHEN candidate_fact_revision_id IS NOT NULL THEN 1 ELSE 0 END)
        = 1
    ),
    CONSTRAINT fk_ret_blob_project
        FOREIGN KEY (source_blob_id, project_id)
        REFERENCES source_blob(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT fk_ret_snapshot_project
        FOREIGN KEY (source_snapshot_id, project_id)
        REFERENCES source_snapshot(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT fk_ret_fact_project
        FOREIGN KEY (candidate_fact_revision_id, project_id)
        REFERENCES candidate_fact_revision(id, project_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_retention_blob ON evidence_retention_event(source_blob_id);
CREATE INDEX IF NOT EXISTS idx_retention_snapshot ON evidence_retention_event(source_snapshot_id);
CREATE INDEX IF NOT EXISTS idx_retention_fact ON evidence_retention_event(candidate_fact_revision_id);

-- ═══ Append-only triggers (guarded; never drop an existing trigger) ═══
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_source_blob_no_mutation'
          AND tgrelid = 'source_blob'::regclass
    ) THEN
        CREATE TRIGGER trg_source_blob_no_mutation
            BEFORE UPDATE OR DELETE ON source_blob
            FOR EACH ROW EXECUTE FUNCTION slicea_reject_mutation();
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_source_snapshot_no_mutation'
          AND tgrelid = 'source_snapshot'::regclass
    ) THEN
        CREATE TRIGGER trg_source_snapshot_no_mutation
            BEFORE UPDATE OR DELETE ON source_snapshot
            FOR EACH ROW EXECUTE FUNCTION slicea_reject_mutation();
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_cfr_no_mutation'
          AND tgrelid = 'candidate_fact_revision'::regclass
    ) THEN
        CREATE TRIGGER trg_cfr_no_mutation
            BEFORE UPDATE OR DELETE ON candidate_fact_revision
            FOR EACH ROW EXECUTE FUNCTION slicea_reject_mutation();
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_retention_no_mutation'
          AND tgrelid = 'evidence_retention_event'::regclass
    ) THEN
        CREATE TRIGGER trg_retention_no_mutation
            BEFORE UPDATE OR DELETE ON evidence_retention_event
            FOR EACH ROW EXECUTE FUNCTION slicea_reject_mutation();
    END IF;
END $$;

COMMIT;
