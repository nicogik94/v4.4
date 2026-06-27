-- v50 Agent Blueprint Studio Foundation (S1 — Draft-only)
-- Additive Studio-namespace persistence for operator-authored agent blueprints.
--
-- Scope (S1 / Wave 1): blueprint_project, blueprint_config_revision,
-- blueprint_source_item (distinct default-deny rights fields), blueprint_source_extract
-- (operator-declared curation), blueprint_artifact (+ version header, assurance_status),
-- blueprint_artifact_input_binding (many-row input contract), blueprint_lint_result/
-- blueprint_lint_finding, blueprint_eval_case/blueprint_eval_run, blueprint_draft_export.
--
-- Properties:
--   * additive only — no ALTER/DROP of existing application tables
--   * one explicit transaction boundary
--   * a preflight that rejects a partial/divergent Studio schema, while keeping a
--     clean bootstrap valid and a complete re-apply a no-op
--   * project-consistent composite foreign keys so direct SQL cannot create
--     cross-blueprint links
--   * NO append-only triggers in S1 (draft-snapshot semantics are application-level;
--     DB-enforced immutability is deferred to S2/S3)
--   * draft-snapshot semantics only: content_hash is for change detection, NOT a
--     tamper-evident or immutable-provenance claim
--   * does NOT use or touch the Decision Engine ProjectState/state_snapshots tables
--   * no CREATE INDEX CONCURRENTLY
--
-- Apply manually AFTER sql/init.sql (only dependency: the projects parent table):
--   psql -U workflow -d workflow_v4 -v ON_ERROR_STOP=1 -f sql/v50_agent_blueprint_studio_foundation.sql

BEGIN;

-- ═══ Preflight: require base schema; refuse partial / divergent Studio schema ═══
-- This is a COMPLETE-CONTRACT verifier, not a table/name count. On a clean
-- bootstrap (zero Studio tables) it returns immediately and the guarded DDL below
-- creates everything. Once ANY Studio table exists, the schema must satisfy the
-- ENTIRE v50 contract — every expected table, every column (data type + nullability),
-- every security-/contract-relevant column default, and every constraint by exact
-- definition (CHECK predicates, project-consistent composite foreign keys, unique
-- and primary keys). A schema that carries all the expected table and constraint
-- NAMES but diverges in columns, nullability, defaults, CHECK predicates, foreign-key
-- shape, or unique columns is REFUSED — a partial/divergent schema is never silently
-- repaired, and a complete re-apply remains a no-op.
DO $$
DECLARE
    v_tables int;
    v_issues text := '';
    v_tmp    text;
BEGIN
    -- Dependency: the base schema (sql/init.sql) must be present.
    IF to_regclass('projects') IS NULL THEN
        RAISE EXCEPTION
            'v50 requires the base schema (sql/init.sql); projects table is missing'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- Count the eleven Studio tables in the current schema.
    SELECT count(*) INTO v_tables
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'r'
      AND n.nspname = current_schema()
      AND c.relname = ANY (ARRAY[
          'blueprint_project', 'blueprint_config_revision', 'blueprint_source_item',
          'blueprint_source_extract', 'blueprint_artifact', 'blueprint_artifact_input_binding',
          'blueprint_lint_result', 'blueprint_lint_finding', 'blueprint_eval_case',
          'blueprint_eval_run', 'blueprint_draft_export']);

    IF v_tables = 0 THEN
        -- Clean bootstrap: nothing exists yet; proceed to create everything.
        RETURN;
    END IF;

    IF v_tables <> 11 THEN
        RAISE EXCEPTION
            'v50 contract violation: expected 11 Studio tables, found % — schema is partial/divergent',
            v_tables USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- ── (1) Columns: presence + data type + nullability ──────────────────────
    SELECT string_agg(format('%s.%s', e.tbl, e.col), '; ') INTO v_tmp
    FROM (VALUES
        ('blueprint_project'::text,'id'::text,'uuid'::text,'NO'::text),
        ('blueprint_project','linked_project_id','uuid','YES'),
        ('blueprint_project','name','text','NO'),
        ('blueprint_project','domain_label','text','NO'),
        ('blueprint_project','status','text','NO'),
        ('blueprint_project','created_by','text','NO'),
        ('blueprint_project','created_at','timestamp with time zone','NO'),
        ('blueprint_project','updated_at','timestamp with time zone','NO'),
        ('blueprint_config_revision','id','uuid','NO'),
        ('blueprint_config_revision','blueprint_project_id','uuid','NO'),
        ('blueprint_config_revision','revision_no','integer','NO'),
        ('blueprint_config_revision','config_json','jsonb','NO'),
        ('blueprint_config_revision','terminology_map_json','jsonb','NO'),
        ('blueprint_config_revision','terminology_map_version','text','NO'),
        ('blueprint_config_revision','locale','text','NO'),
        ('blueprint_config_revision','content_hash','text','NO'),
        ('blueprint_config_revision','created_by','text','NO'),
        ('blueprint_config_revision','created_at','timestamp with time zone','NO'),
        ('blueprint_source_item','id','uuid','NO'),
        ('blueprint_source_item','blueprint_project_id','uuid','NO'),
        ('blueprint_source_item','title','text','NO'),
        ('blueprint_source_item','source_kind','text','NO'),
        ('blueprint_source_item','locator','text','NO'),
        ('blueprint_source_item','authority_tier','text','NO'),
        ('blueprint_source_item','use_allowed','boolean','NO'),
        ('blueprint_source_item','quote_allowed','boolean','NO'),
        ('blueprint_source_item','export_allowed','boolean','NO'),
        ('blueprint_source_item','external_processing_allowed','boolean','NO'),
        ('blueprint_source_item','permitted_audience','text','NO'),
        ('blueprint_source_item','sensitivity_level','text','NO'),
        ('blueprint_source_item','retention_declaration','text','NO'),
        ('blueprint_source_item','created_by','text','NO'),
        ('blueprint_source_item','created_at','timestamp with time zone','NO'),
        ('blueprint_source_extract','id','uuid','NO'),
        ('blueprint_source_extract','blueprint_project_id','uuid','NO'),
        ('blueprint_source_extract','source_item_id','uuid','NO'),
        ('blueprint_source_extract','extract_type','text','NO'),
        ('blueprint_source_extract','text_value','text','YES'),
        ('blueprint_source_extract','numeric_value','numeric','YES'),
        ('blueprint_source_extract','unit','text','NO'),
        ('blueprint_source_extract','as_of_date','date','YES'),
        ('blueprint_source_extract','curated_by','text','NO'),
        ('blueprint_source_extract','curation_status','text','NO'),
        ('blueprint_source_extract','extract_content_fingerprint','text','NO'),
        ('blueprint_source_extract','created_at','timestamp with time zone','NO'),
        ('blueprint_artifact','id','uuid','NO'),
        ('blueprint_artifact','blueprint_project_id','uuid','NO'),
        ('blueprint_artifact','config_revision_id','uuid','NO'),
        ('blueprint_artifact','artifact_kind','text','NO'),
        ('blueprint_artifact','baseline_artifact_id','uuid','YES'),
        ('blueprint_artifact','content_json','jsonb','NO'),
        ('blueprint_artifact','diff_json','jsonb','YES'),
        ('blueprint_artifact','content_hash','text','NO'),
        ('blueprint_artifact','artifact_schema_version','text','NO'),
        ('blueprint_artifact','compiler_version','text','NO'),
        ('blueprint_artifact','template_set_version','text','NO'),
        ('blueprint_artifact','terminology_map_version','text','NO'),
        ('blueprint_artifact','locale','text','NO'),
        ('blueprint_artifact','assurance_status','text','NO'),
        ('blueprint_artifact','created_at','timestamp with time zone','NO'),
        ('blueprint_artifact_input_binding','id','uuid','NO'),
        ('blueprint_artifact_input_binding','blueprint_project_id','uuid','NO'),
        ('blueprint_artifact_input_binding','artifact_id','uuid','NO'),
        ('blueprint_artifact_input_binding','extract_id','uuid','NO'),
        ('blueprint_artifact_input_binding','input_order','integer','NO'),
        ('blueprint_artifact_input_binding','extract_content_fingerprint','text','NO'),
        ('blueprint_artifact_input_binding','created_at','timestamp with time zone','NO'),
        ('blueprint_lint_result','id','uuid','NO'),
        ('blueprint_lint_result','blueprint_project_id','uuid','NO'),
        ('blueprint_lint_result','artifact_id','uuid','NO'),
        ('blueprint_lint_result','highest_severity','text','NO'),
        ('blueprint_lint_result','export_blocked','boolean','NO'),
        ('blueprint_lint_result','created_at','timestamp with time zone','NO'),
        ('blueprint_lint_finding','id','uuid','NO'),
        ('blueprint_lint_finding','blueprint_project_id','uuid','NO'),
        ('blueprint_lint_finding','lint_result_id','uuid','NO'),
        ('blueprint_lint_finding','code','text','NO'),
        ('blueprint_lint_finding','severity','text','NO'),
        ('blueprint_lint_finding','message','text','NO'),
        ('blueprint_lint_finding','locator','text','NO'),
        ('blueprint_lint_finding','section_kind','text','NO'),
        ('blueprint_lint_finding','created_at','timestamp with time zone','NO'),
        ('blueprint_eval_case','id','uuid','NO'),
        ('blueprint_eval_case','blueprint_project_id','uuid','NO'),
        ('blueprint_eval_case','case_json','jsonb','NO'),
        ('blueprint_eval_case','expected_json','jsonb','NO'),
        ('blueprint_eval_case','created_at','timestamp with time zone','NO'),
        ('blueprint_eval_run','id','uuid','NO'),
        ('blueprint_eval_run','blueprint_project_id','uuid','NO'),
        ('blueprint_eval_run','artifact_id','uuid','NO'),
        ('blueprint_eval_run','deterministic_results_json','jsonb','NO'),
        ('blueprint_eval_run','passed','boolean','NO'),
        ('blueprint_eval_run','created_at','timestamp with time zone','NO'),
        ('blueprint_draft_export','id','uuid','NO'),
        ('blueprint_draft_export','blueprint_project_id','uuid','NO'),
        ('blueprint_draft_export','artifact_id','uuid','NO'),
        ('blueprint_draft_export','profile','text','NO'),
        ('blueprint_draft_export','format','text','NO'),
        ('blueprint_draft_export','content_hash','text','NO'),
        ('blueprint_draft_export','watermark_applied','boolean','NO'),
        ('blueprint_draft_export','created_at','timestamp with time zone','NO')
    ) AS e(tbl, col, typ, nullable)
    LEFT JOIN information_schema.columns c
      ON c.table_schema::text = current_schema()
     AND c.table_name::text = e.tbl
     AND c.column_name::text = e.col
    WHERE c.column_name IS NULL
       OR c.data_type::text <> e.typ
       OR c.is_nullable::text <> e.nullable;
    IF v_tmp IS NOT NULL THEN
        v_issues := v_issues || 'columns{' || v_tmp || '} ';
    END IF;

    -- ── (2) Security-/contract-relevant column defaults (normalized) ─────────
    -- Default-deny rights posture, draft-only status/assurance, operator-declared
    -- curation, and the boolean posture flags. A weakened default is a divergence.
    SELECT string_agg(format('%s.%s', e.tbl, e.col), '; ') INTO v_tmp
    FROM (VALUES
        ('blueprint_source_item'::text,'use_allowed'::text,'false'::text),
        ('blueprint_source_item','quote_allowed','false'),
        ('blueprint_source_item','export_allowed','false'),
        ('blueprint_source_item','external_processing_allowed','false'),
        ('blueprint_source_item','authority_tier','''unspecified''::text'),
        ('blueprint_source_item','permitted_audience','''operator_only''::text'),
        ('blueprint_source_item','sensitivity_level','''restricted''::text'),
        ('blueprint_source_item','retention_declaration','''undeclared_restricted''::text'),
        ('blueprint_project','status','''draft''::text'),
        ('blueprint_artifact','assurance_status','''draft_unvalidated''::text'),
        ('blueprint_source_extract','curation_status','''operator_declared''::text'),
        ('blueprint_lint_result','export_blocked','false'),
        ('blueprint_eval_run','passed','false'),
        ('blueprint_draft_export','watermark_applied','true')
    ) AS e(tbl, col, def)
    LEFT JOIN information_schema.columns c
      ON c.table_schema::text = current_schema()
     AND c.table_name::text = e.tbl
     AND c.column_name::text = e.col
    WHERE c.column_name IS NULL
       OR c.column_default::text IS DISTINCT FROM e.def;
    IF v_tmp IS NOT NULL THEN
        v_issues := v_issues || 'defaults{' || v_tmp || '} ';
    END IF;

    -- ── (3) Constraints by EXACT definition (CHECK / FK / unique / primary) ──
    -- Verifying the definition (not just the name) rejects a constraint that keeps
    -- its name but was redefined: e.g. a weakened CHECK, or a project-consistent
    -- composite FK degraded to a single-column reference.
    SELECT string_agg(e.conname, '; ') INTO v_tmp
    FROM (VALUES
        ('uq_blueprint_project_id'::text,'PRIMARY KEY (id)'::text),
        ('ck_bp_status_draft_only','CHECK ((status = ''draft''::text))'),
        ('blueprint_project_linked_project_id_fkey',
         'FOREIGN KEY (linked_project_id) REFERENCES projects(id) ON DELETE SET NULL'),
        ('blueprint_config_revision_pkey','PRIMARY KEY (id)'),
        ('uq_bcr_id_project','UNIQUE (id, blueprint_project_id)'),
        ('uq_bcr_project_revno','UNIQUE (blueprint_project_id, revision_no)'),
        ('blueprint_config_revision_blueprint_project_id_fkey',
         'FOREIGN KEY (blueprint_project_id) REFERENCES blueprint_project(id) ON DELETE CASCADE'),
        ('blueprint_source_item_pkey','PRIMARY KEY (id)'),
        ('uq_bsi_id_project','UNIQUE (id, blueprint_project_id)'),
        ('blueprint_source_item_blueprint_project_id_fkey',
         'FOREIGN KEY (blueprint_project_id) REFERENCES blueprint_project(id) ON DELETE CASCADE'),
        ('blueprint_source_extract_pkey','PRIMARY KEY (id)'),
        ('uq_bse_id_project','UNIQUE (id, blueprint_project_id)'),
        ('blueprint_source_extract_extract_type_check',
         'CHECK ((extract_type = ANY (ARRAY[''claim''::text, ''quote''::text, ''numeric''::text, ''categorical''::text])))'),
        ('ck_bse_shape',
         'CHECK ((((extract_type = ''numeric''::text) AND (numeric_value IS NOT NULL) AND (text_value IS NULL)) OR ((extract_type = ANY (ARRAY[''claim''::text, ''quote''::text, ''categorical''::text])) AND (text_value IS NOT NULL) AND (char_length(text_value) > 0) AND (numeric_value IS NULL))))'),
        ('fk_bse_item_project',
         'FOREIGN KEY (source_item_id, blueprint_project_id) REFERENCES blueprint_source_item(id, blueprint_project_id) ON DELETE CASCADE'),
        ('blueprint_source_extract_blueprint_project_id_fkey',
         'FOREIGN KEY (blueprint_project_id) REFERENCES blueprint_project(id) ON DELETE CASCADE'),
        ('blueprint_artifact_pkey','PRIMARY KEY (id)'),
        ('uq_ba_id_project','UNIQUE (id, blueprint_project_id)'),
        ('ck_ba_assurance_draft_only','CHECK ((assurance_status = ''draft_unvalidated''::text))'),
        ('fk_ba_revision_project',
         'FOREIGN KEY (config_revision_id, blueprint_project_id) REFERENCES blueprint_config_revision(id, blueprint_project_id) ON DELETE CASCADE'),
        ('fk_ba_baseline_project',
         'FOREIGN KEY (baseline_artifact_id, blueprint_project_id) REFERENCES blueprint_artifact(id, blueprint_project_id) ON DELETE CASCADE'),
        ('blueprint_artifact_blueprint_project_id_fkey',
         'FOREIGN KEY (blueprint_project_id) REFERENCES blueprint_project(id) ON DELETE CASCADE'),
        ('blueprint_artifact_input_binding_pkey','PRIMARY KEY (id)'),
        ('uq_baib_artifact_extract','UNIQUE (artifact_id, extract_id)'),
        ('uq_baib_artifact_order','UNIQUE (artifact_id, input_order)'),
        ('fk_baib_artifact_project',
         'FOREIGN KEY (artifact_id, blueprint_project_id) REFERENCES blueprint_artifact(id, blueprint_project_id) ON DELETE CASCADE'),
        ('fk_baib_extract_project',
         'FOREIGN KEY (extract_id, blueprint_project_id) REFERENCES blueprint_source_extract(id, blueprint_project_id) ON DELETE CASCADE'),
        ('blueprint_artifact_input_binding_blueprint_project_id_fkey',
         'FOREIGN KEY (blueprint_project_id) REFERENCES blueprint_project(id) ON DELETE CASCADE'),
        ('blueprint_lint_result_pkey','PRIMARY KEY (id)'),
        ('uq_blr_id_project','UNIQUE (id, blueprint_project_id)'),
        ('fk_blr_artifact_project',
         'FOREIGN KEY (artifact_id, blueprint_project_id) REFERENCES blueprint_artifact(id, blueprint_project_id) ON DELETE CASCADE'),
        ('blueprint_lint_result_blueprint_project_id_fkey',
         'FOREIGN KEY (blueprint_project_id) REFERENCES blueprint_project(id) ON DELETE CASCADE'),
        ('blueprint_lint_finding_pkey','PRIMARY KEY (id)'),
        ('blueprint_lint_finding_severity_check',
         'CHECK ((severity = ANY (ARRAY[''warning''::text, ''draft_export_blocker''::text])))'),
        ('blueprint_lint_finding_section_kind_check',
         'CHECK ((section_kind = ANY (ARRAY[''generated''::text, ''operator_free_text''::text])))'),
        ('fk_blf_result_project',
         'FOREIGN KEY (lint_result_id, blueprint_project_id) REFERENCES blueprint_lint_result(id, blueprint_project_id) ON DELETE CASCADE'),
        ('blueprint_lint_finding_blueprint_project_id_fkey',
         'FOREIGN KEY (blueprint_project_id) REFERENCES blueprint_project(id) ON DELETE CASCADE'),
        ('blueprint_eval_case_pkey','PRIMARY KEY (id)'),
        ('uq_bec_id_project','UNIQUE (id, blueprint_project_id)'),
        ('blueprint_eval_case_blueprint_project_id_fkey',
         'FOREIGN KEY (blueprint_project_id) REFERENCES blueprint_project(id) ON DELETE CASCADE'),
        ('blueprint_eval_run_pkey','PRIMARY KEY (id)'),
        ('uq_ber_id_project','UNIQUE (id, blueprint_project_id)'),
        ('fk_ber_artifact_project',
         'FOREIGN KEY (artifact_id, blueprint_project_id) REFERENCES blueprint_artifact(id, blueprint_project_id) ON DELETE CASCADE'),
        ('blueprint_eval_run_blueprint_project_id_fkey',
         'FOREIGN KEY (blueprint_project_id) REFERENCES blueprint_project(id) ON DELETE CASCADE'),
        ('blueprint_draft_export_pkey','PRIMARY KEY (id)'),
        ('uq_bde_id_project','UNIQUE (id, blueprint_project_id)'),
        ('fk_bde_artifact_project',
         'FOREIGN KEY (artifact_id, blueprint_project_id) REFERENCES blueprint_artifact(id, blueprint_project_id) ON DELETE CASCADE'),
        ('blueprint_draft_export_blueprint_project_id_fkey',
         'FOREIGN KEY (blueprint_project_id) REFERENCES blueprint_project(id) ON DELETE CASCADE')
    ) AS e(conname, def)
    LEFT JOIN (
        SELECT con.conname::text AS nm, pg_get_constraintdef(con.oid) AS def
        FROM pg_constraint con
        JOIN pg_namespace n ON n.oid = con.connamespace
        WHERE n.nspname = current_schema()
    ) cc ON cc.nm = e.conname
    WHERE cc.nm IS NULL OR cc.def IS DISTINCT FROM e.def;
    IF v_tmp IS NOT NULL THEN
        v_issues := v_issues || 'constraints{' || v_tmp || '} ';
    END IF;

    IF v_issues <> '' THEN
        RAISE EXCEPTION
            'v50 contract violation: schema is partial/divergent — %', v_issues
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- All checks passed: schema is complete; downstream guarded DDL is a no-op.
END $$;

-- ═══ blueprint_project — mutable draft metadata (draft-only in S1) ═══
-- Optional, non-coupling link to the Decision-Engine projects table (SET NULL on
-- delete). All Studio state lives in Studio tables; ProjectState is never used.
-- S1 has NO current-revision pointer: the latest revision is DERIVED from
-- blueprint_config_revision.revision_no, never tracked by an unenforceable column.
-- S1 status is draft-only, enforced by a named CHECK (no released/validated state).
CREATE TABLE IF NOT EXISTS blueprint_project (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    linked_project_id   UUID REFERENCES projects(id) ON DELETE SET NULL,
    name                TEXT NOT NULL,
    domain_label        TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'draft',
    created_by          TEXT NOT NULL DEFAULT '',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_blueprint_project_id UNIQUE (id),
    CONSTRAINT ck_bp_status_draft_only CHECK (status = 'draft')
);

CREATE INDEX IF NOT EXISTS idx_bp_linked_project ON blueprint_project(linked_project_id);

-- ═══ blueprint_config_revision — versioned config snapshot (insert-only) ═══
CREATE TABLE IF NOT EXISTS blueprint_config_revision (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    blueprint_project_id     UUID NOT NULL REFERENCES blueprint_project(id) ON DELETE CASCADE,
    revision_no              INT NOT NULL,
    config_json              JSONB NOT NULL DEFAULT '{}',
    terminology_map_json     JSONB NOT NULL DEFAULT '{}',
    terminology_map_version  TEXT NOT NULL DEFAULT '',
    locale                   TEXT NOT NULL DEFAULT '',
    content_hash             TEXT NOT NULL DEFAULT '',
    created_by               TEXT NOT NULL DEFAULT '',
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_bcr_id_project UNIQUE (id, blueprint_project_id),
    CONSTRAINT uq_bcr_project_revno UNIQUE (blueprint_project_id, revision_no)
);

CREATE INDEX IF NOT EXISTS idx_bcr_project ON blueprint_config_revision(blueprint_project_id);

-- ═══ blueprint_source_item — manual source manifest (insert-only) ═══
-- Distinct, explicit, DEFAULT-DENY rights/sensitivity/retention fields. Unknown
-- defaults are non-permissive. Holds a citation/locator only — never document bytes.
CREATE TABLE IF NOT EXISTS blueprint_source_item (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    blueprint_project_id        UUID NOT NULL REFERENCES blueprint_project(id) ON DELETE CASCADE,
    title                       TEXT NOT NULL DEFAULT '',
    source_kind                 TEXT NOT NULL DEFAULT '',
    locator                     TEXT NOT NULL DEFAULT '',
    authority_tier              TEXT NOT NULL DEFAULT 'unspecified',
    use_allowed                 BOOLEAN NOT NULL DEFAULT FALSE,
    quote_allowed               BOOLEAN NOT NULL DEFAULT FALSE,
    export_allowed              BOOLEAN NOT NULL DEFAULT FALSE,
    external_processing_allowed BOOLEAN NOT NULL DEFAULT FALSE,
    permitted_audience          TEXT NOT NULL DEFAULT 'operator_only',
    sensitivity_level           TEXT NOT NULL DEFAULT 'restricted',
    retention_declaration       TEXT NOT NULL DEFAULT 'undeclared_restricted',
    created_by                  TEXT NOT NULL DEFAULT '',
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_bsi_id_project UNIQUE (id, blueprint_project_id)
);

CREATE INDEX IF NOT EXISTS idx_bsi_project ON blueprint_source_item(blueprint_project_id);

-- ═══ blueprint_source_extract — operator-declared curated extract (insert-only) ═══
-- "operator-declared" curation only: curated_by/curation_status are operator labels,
-- NOT authenticated, independent, or verified review. Source content is data, never
-- instructions. Numeric storage is NUMERIC (Decimal-compatible), never float.
CREATE TABLE IF NOT EXISTS blueprint_source_extract (
    id                           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    blueprint_project_id         UUID NOT NULL REFERENCES blueprint_project(id) ON DELETE CASCADE,
    source_item_id               UUID NOT NULL,
    extract_type                 TEXT NOT NULL
                                 CHECK (extract_type IN ('claim', 'quote', 'numeric', 'categorical')),
    text_value                   TEXT,
    numeric_value                NUMERIC,
    unit                         TEXT NOT NULL DEFAULT '',
    as_of_date                   DATE,
    curated_by                   TEXT NOT NULL DEFAULT '',
    curation_status              TEXT NOT NULL DEFAULT 'operator_declared',
    extract_content_fingerprint  TEXT NOT NULL DEFAULT '',
    created_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_bse_id_project UNIQUE (id, blueprint_project_id),
    CONSTRAINT fk_bse_item_project
        FOREIGN KEY (source_item_id, blueprint_project_id)
        REFERENCES blueprint_source_item(id, blueprint_project_id) ON DELETE CASCADE,
    -- Shape contract by extract_type (mutually exclusive value channels):
    --   * numeric                    → numeric_value present, text_value forbidden
    --   * claim / quote / categorical → non-empty text_value, numeric_value forbidden
    CONSTRAINT ck_bse_shape CHECK (
        (extract_type = 'numeric'
            AND numeric_value IS NOT NULL
            AND text_value IS NULL)
        OR (extract_type IN ('claim', 'quote', 'categorical')
            AND text_value IS NOT NULL AND char_length(text_value) > 0
            AND numeric_value IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_bse_project ON blueprint_source_extract(blueprint_project_id);
CREATE INDEX IF NOT EXISTS idx_bse_item ON blueprint_source_extract(source_item_id);

-- ═══ blueprint_artifact — compiled draft artifact (insert-only; content-addressed) ═══
-- Version header is part of the hashed identity (set by the Wave-3 compiler). S1
-- enforces the only assurance value: draft_unvalidated.
CREATE TABLE IF NOT EXISTS blueprint_artifact (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    blueprint_project_id     UUID NOT NULL REFERENCES blueprint_project(id) ON DELETE CASCADE,
    config_revision_id       UUID NOT NULL,
    artifact_kind            TEXT NOT NULL DEFAULT 'blueprint',
    baseline_artifact_id     UUID,
    content_json             JSONB NOT NULL DEFAULT '{}',
    diff_json                JSONB,
    content_hash             TEXT NOT NULL,
    artifact_schema_version  TEXT NOT NULL DEFAULT '1',
    compiler_version         TEXT NOT NULL DEFAULT '',
    template_set_version     TEXT NOT NULL DEFAULT '',
    terminology_map_version  TEXT NOT NULL DEFAULT '',
    locale                   TEXT NOT NULL DEFAULT '',
    assurance_status         TEXT NOT NULL DEFAULT 'draft_unvalidated',
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_ba_id_project UNIQUE (id, blueprint_project_id),
    CONSTRAINT ck_ba_assurance_draft_only CHECK (assurance_status = 'draft_unvalidated'),
    CONSTRAINT fk_ba_revision_project
        FOREIGN KEY (config_revision_id, blueprint_project_id)
        REFERENCES blueprint_config_revision(id, blueprint_project_id) ON DELETE CASCADE,
    CONSTRAINT fk_ba_baseline_project
        FOREIGN KEY (baseline_artifact_id, blueprint_project_id)
        REFERENCES blueprint_artifact(id, blueprint_project_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ba_project ON blueprint_artifact(blueprint_project_id);

-- ═══ blueprint_artifact_input_binding — many-row artifact-input contract ═══
-- One row per included source extract that produced an artifact (never a single
-- extract_id field). Project-consistent on both sides.
--
-- input_order is the operator-DECLARED position of an extract in the artifact's
-- input contract. It is a deterministic ordering key supplied by the caller; it
-- MUST NOT be derived from generated ids (binding/extract uuids) or insertion time.
-- Two uniqueness contracts: identity (artifact_id, extract_id) — an extract is bound
-- at most once — and order (artifact_id, input_order) — positions are distinct.
CREATE TABLE IF NOT EXISTS blueprint_artifact_input_binding (
    id                           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    blueprint_project_id         UUID NOT NULL REFERENCES blueprint_project(id) ON DELETE CASCADE,
    artifact_id                  UUID NOT NULL,
    extract_id                   UUID NOT NULL,
    input_order                  INTEGER NOT NULL,
    extract_content_fingerprint  TEXT NOT NULL DEFAULT '',
    created_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_baib_artifact_extract UNIQUE (artifact_id, extract_id),
    CONSTRAINT uq_baib_artifact_order UNIQUE (artifact_id, input_order),
    CONSTRAINT fk_baib_artifact_project
        FOREIGN KEY (artifact_id, blueprint_project_id)
        REFERENCES blueprint_artifact(id, blueprint_project_id) ON DELETE CASCADE,
    CONSTRAINT fk_baib_extract_project
        FOREIGN KEY (extract_id, blueprint_project_id)
        REFERENCES blueprint_source_extract(id, blueprint_project_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_baib_artifact ON blueprint_artifact_input_binding(artifact_id);
CREATE INDEX IF NOT EXISTS idx_baib_project ON blueprint_artifact_input_binding(blueprint_project_id);

-- ═══ blueprint_lint_result / blueprint_lint_finding (schema only in Wave 1) ═══
CREATE TABLE IF NOT EXISTS blueprint_lint_result (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    blueprint_project_id UUID NOT NULL REFERENCES blueprint_project(id) ON DELETE CASCADE,
    artifact_id          UUID NOT NULL,
    highest_severity     TEXT NOT NULL DEFAULT '',
    export_blocked       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_blr_id_project UNIQUE (id, blueprint_project_id),
    CONSTRAINT fk_blr_artifact_project
        FOREIGN KEY (artifact_id, blueprint_project_id)
        REFERENCES blueprint_artifact(id, blueprint_project_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_blr_project ON blueprint_lint_result(blueprint_project_id);

CREATE TABLE IF NOT EXISTS blueprint_lint_finding (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    blueprint_project_id UUID NOT NULL REFERENCES blueprint_project(id) ON DELETE CASCADE,
    lint_result_id       UUID NOT NULL,
    code                 TEXT NOT NULL,
    severity             TEXT NOT NULL CHECK (severity IN ('warning', 'draft_export_blocker')),
    message              TEXT NOT NULL DEFAULT '',
    locator              TEXT NOT NULL DEFAULT '',
    section_kind         TEXT NOT NULL DEFAULT 'operator_free_text'
                         CHECK (section_kind IN ('generated', 'operator_free_text')),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_blf_result_project
        FOREIGN KEY (lint_result_id, blueprint_project_id)
        REFERENCES blueprint_lint_result(id, blueprint_project_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_blf_result ON blueprint_lint_finding(lint_result_id);

-- ═══ blueprint_eval_case / blueprint_eval_run (schema only in Wave 1) ═══
-- S1 evaluation is deterministic static blueprint-contract evaluation only; there is
-- no hosted-model judge field (hosted judging is a separately gated future capability).
CREATE TABLE IF NOT EXISTS blueprint_eval_case (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    blueprint_project_id UUID NOT NULL REFERENCES blueprint_project(id) ON DELETE CASCADE,
    case_json            JSONB NOT NULL DEFAULT '{}',
    expected_json        JSONB NOT NULL DEFAULT '{}',
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_bec_id_project UNIQUE (id, blueprint_project_id)
);

CREATE INDEX IF NOT EXISTS idx_bec_project ON blueprint_eval_case(blueprint_project_id);

CREATE TABLE IF NOT EXISTS blueprint_eval_run (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    blueprint_project_id        UUID NOT NULL REFERENCES blueprint_project(id) ON DELETE CASCADE,
    artifact_id                 UUID NOT NULL,
    deterministic_results_json  JSONB NOT NULL DEFAULT '{}',
    passed                      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_ber_id_project UNIQUE (id, blueprint_project_id),
    CONSTRAINT fk_ber_artifact_project
        FOREIGN KEY (artifact_id, blueprint_project_id)
        REFERENCES blueprint_artifact(id, blueprint_project_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ber_project ON blueprint_eval_run(blueprint_project_id);

-- ═══ blueprint_draft_export (schema only in Wave 1) ═══
CREATE TABLE IF NOT EXISTS blueprint_draft_export (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    blueprint_project_id UUID NOT NULL REFERENCES blueprint_project(id) ON DELETE CASCADE,
    artifact_id          UUID NOT NULL,
    profile              TEXT NOT NULL,
    format               TEXT NOT NULL,
    content_hash         TEXT NOT NULL DEFAULT '',
    watermark_applied    BOOLEAN NOT NULL DEFAULT TRUE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_bde_id_project UNIQUE (id, blueprint_project_id),
    CONSTRAINT fk_bde_artifact_project
        FOREIGN KEY (artifact_id, blueprint_project_id)
        REFERENCES blueprint_artifact(id, blueprint_project_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_bde_project ON blueprint_draft_export(blueprint_project_id);

COMMIT;
