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
--   * guarded DDL so a clean apply works and a complete re-apply is a no-op
--   * append-only enforcement via BEFORE UPDATE/DELETE triggers on the four
--     immutable tables (IngestOperation is intentionally mutable)
--   * restrictive foreign keys (ON DELETE RESTRICT) — project deletion is rejected
--     while retained Slice A evidence exists
--   * no CREATE INDEX CONCURRENTLY
--
-- Apply manually AFTER sql/init.sql and sql/outcomes.sql:
--   psql -U workflow -d workflow_v4 -v ON_ERROR_STOP=1 -f sql/v47_evidence_snapshot_foundation.sql

BEGIN;

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
-- Deduplicated only within a project.
CREATE TABLE IF NOT EXISTS source_blob (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id     UUID NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    hash_algorithm TEXT NOT NULL,
    content_hash   TEXT NOT NULL,
    byte_size      BIGINT NOT NULL CHECK (byte_size >= 0),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by     TEXT NOT NULL DEFAULT '',
    CONSTRAINT uq_source_blob_project_content
        UNIQUE (project_id, hash_algorithm, content_hash)
);

-- ═══ SourceSnapshot — one immutable capture event ═══
-- Owns the capture-specific storage_ref, source context, locator, captured_at,
-- and ingestion context. References exactly one SourceBlob. Never deduplicated
-- by content hash: the same bytes captured through separate successful operations
-- may create separate snapshots.
CREATE TABLE IF NOT EXISTS source_snapshot (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_blob_id      UUID NOT NULL REFERENCES source_blob(id) ON DELETE RESTRICT,
    project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    storage_ref         TEXT NOT NULL,
    source_kind         TEXT NOT NULL DEFAULT '',
    source_locator      TEXT NOT NULL DEFAULT '',
    ingest_operation_id TEXT NOT NULL DEFAULT '',
    captured_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    captured_by         TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_source_snapshot_blob ON source_snapshot(source_blob_id);
CREATE INDEX IF NOT EXISTS idx_source_snapshot_project ON source_snapshot(project_id);
CREATE INDEX IF NOT EXISTS idx_source_snapshot_storage_ref ON source_snapshot(storage_ref);

-- ═══ IngestOperation — operational retry/capture state (mutable status) ═══
-- The only Slice A record permitted to transition status. Retry idempotency uses
-- a project-scoped stable operation id (or source-event fingerprint).
CREATE TABLE IF NOT EXISTS ingest_operation (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id         UUID NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    operation_id       TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'committed', 'failed', 'skipped_not_capturable')),
    source_snapshot_id UUID REFERENCES source_snapshot(id) ON DELETE RESTRICT,
    detail             TEXT NOT NULL DEFAULT '',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_ingest_operation_project_op UNIQUE (project_id, operation_id)
);

-- ═══ CandidateFactRevision — direct, typed, source-derived fact only ═══
-- Must reference exactly one SourceSnapshot (NOT NULL). Numeric storage is NUMERIC
-- (Decimal-compatible), never float. Text/categorical facts never carry a numeric
-- value. No operator-modelling values (those belong to future scenario work).
CREATE TABLE IF NOT EXISTS candidate_fact_revision (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    source_snapshot_id  UUID NOT NULL REFERENCES source_snapshot(id) ON DELETE RESTRICT,
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
-- Targets exactly one of blob / snapshot / fact (three explicit FKs + XOR check).
-- legal_hold blocks future physical purge only; tombstone/redact block future use
-- through the Slice A availability resolver. No physical purge in Slice A.
CREATE TABLE IF NOT EXISTS evidence_retention_event (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id                 UUID NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    event_type                 TEXT NOT NULL CHECK (event_type IN ('legal_hold', 'tombstone', 'redact')),
    source_blob_id             UUID REFERENCES source_blob(id) ON DELETE RESTRICT,
    source_snapshot_id         UUID REFERENCES source_snapshot(id) ON DELETE RESTRICT,
    candidate_fact_revision_id UUID REFERENCES candidate_fact_revision(id) ON DELETE RESTRICT,
    reason                     TEXT NOT NULL DEFAULT '',
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by                 TEXT NOT NULL DEFAULT '',
    CONSTRAINT ck_retention_single_target CHECK (
        (CASE WHEN source_blob_id IS NOT NULL THEN 1 ELSE 0 END)
        + (CASE WHEN source_snapshot_id IS NOT NULL THEN 1 ELSE 0 END)
        + (CASE WHEN candidate_fact_revision_id IS NOT NULL THEN 1 ELSE 0 END)
        = 1
    )
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
