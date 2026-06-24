-- v4.9 Automation ROI calculation idempotency (Slice B hardening)
-- Additive persistence that gives the deterministic calculate action a durable
-- request identity and an exact calculation-operation identity, so retries and
-- concurrent submissions converge on exactly one CalculationResult.
--
-- Scope: one new MUTABLE table, calculation_request, plus its controlled-transition
-- guard function and trigger. v47 and v48 are NOT modified; calculation_result and
-- calculation_result_input remain append-only and unchanged.
--
-- Two identities:
--   * request identity    UNIQUE (project_id, idempotency_key)
--   * operation identity  UNIQUE (project_id, canonical_request_digest)
-- canonical_request_digest = sha256(project_id + formula_version +
--   sorted input_role -> approved_calculation_input_id map); it is computed by the
--   service. The value-based formula_input_digest is NEVER a uniqueness key:
--   distinct frozen inputs with equal values share it and would wrongly dedupe.
--
-- calculation_request is the only mutable Slice B table. Its guard permits exactly
-- one transition pending -> committed (result link NULL -> non-NULL), rejects
-- DELETE, rejects any change to immutable request fields, and rejects any mutation
-- once committed. All other Slice B tables stay append-only (v48 triggers).
--
-- Properties (mirroring v47/v48):
--   * additive only — no ALTER/DROP of existing application tables
--   * one explicit transaction boundary
--   * a preflight that asserts the v48 dependency and rejects a partial/divergent
--     v49 schema, while keeping clean bootstrap valid and complete re-apply a no-op
--   * project-consistent composite foreign key so direct SQL cannot link a request
--     to another project's result
--   * no CREATE INDEX CONCURRENTLY
--
-- Apply manually AFTER sql/v48_automation_roi_foundation.sql:
--   psql -U workflow -d workflow_v4 -v ON_ERROR_STOP=1 -f sql/v49_automation_roi_calculation_idempotency.sql

BEGIN;

-- ═══ Preflight: require complete v48, refuse partial / divergent v49 ═══
DO $$
DECLARE
    v_b_tables  int;
    v_fn_count  int;
    v_present   boolean;
BEGIN
    -- v48 dependency: the five Slice B tables, the two Slice B guard functions,
    -- and the composite-unique FK target this migration references must exist.
    SELECT count(*) INTO v_b_tables
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'r' AND n.nspname = current_schema()
      AND c.relname = ANY (ARRAY[
          'candidate_fact_extraction_context', 'candidate_fact_approval_decision',
          'approved_calculation_input', 'calculation_result', 'calculation_result_input']);
    IF v_b_tables <> 5 THEN
        RAISE EXCEPTION 'v49 requires v48: expected 5 Slice B tables, found % — apply v48 first', v_b_tables
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT count(*) INTO v_fn_count
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = current_schema()
      AND p.proname = ANY (ARRAY['slicebo_assert_frozen_matches_fact', 'slicebo_assert_result_invariant']);
    IF v_fn_count <> 2 THEN
        RAISE EXCEPTION 'v49 requires v48: Slice B guard functions are missing (found %) — apply v48 first', v_fn_count
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint con
        WHERE con.conname = 'uq_cr_id_project'
          AND con.connamespace = current_schema()::regnamespace
    ) THEN
        RAISE EXCEPTION 'v49 requires v48: uq_cr_id_project on calculation_result is missing — apply v48 first'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- v49 self-state.
    v_present := EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind = 'r' AND n.nspname = current_schema() AND c.relname = 'calculation_request'
    ) OR EXISTS (
        SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = current_schema() AND p.proname = 'sliceb_creq_guard'
    ) OR EXISTS (
        SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = current_schema() AND NOT t.tgisinternal
          AND t.tgname = 'trg_creq_controlled_transition'
    );

    IF NOT v_present THEN
        RETURN;  -- clean bootstrap: nothing exists yet; create everything.
    END IF;

    -- Something exists: it must satisfy the full v49 contract or the migration is
    -- refused. Unknown partial/divergent schema is never silently repaired.
    IF NOT EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind = 'r' AND n.nspname = current_schema() AND c.relname = 'calculation_request'
    ) THEN
        RAISE EXCEPTION 'v49 contract violation: calculation_request table missing — schema is partial/divergent'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = current_schema() AND p.proname = 'sliceb_creq_guard'
    ) THEN
        RAISE EXCEPTION 'v49 contract violation: sliceb_creq_guard() missing — schema is partial/divergent'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = current_schema() AND NOT t.tgisinternal
          AND t.tgname = 'trg_creq_controlled_transition'
    ) THEN
        RAISE EXCEPTION 'v49 contract violation: trg_creq_controlled_transition missing — schema is partial/divergent'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
    -- Complete: downstream guarded DDL is a no-op.
END $$;

-- ═══ Controlled-transition guard (NOT the append-only slicea_reject_mutation) ═══
-- calculation_request is the single mutable Slice B table. It permits exactly one
-- transition pending -> committed and is otherwise immutable.
CREATE OR REPLACE FUNCTION sliceb_creq_guard()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'calculation_request is delete-protected'
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- UPDATE only beyond this point.
    IF OLD.status = 'committed' THEN
        RAISE EXCEPTION 'calculation_request % is committed and immutable', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NOT (OLD.status = 'pending' AND NEW.status = 'committed') THEN
        RAISE EXCEPTION 'calculation_request permits only the pending -> committed transition'
            USING ERRCODE = 'check_violation';
    END IF;

    -- Immutable request fields may never change.
    IF NEW.id                       IS DISTINCT FROM OLD.id
       OR NEW.project_id            IS DISTINCT FROM OLD.project_id
       OR NEW.calculation_kind      IS DISTINCT FROM OLD.calculation_kind
       OR NEW.formula_version       IS DISTINCT FROM OLD.formula_version
       OR NEW.idempotency_key       IS DISTINCT FROM OLD.idempotency_key
       OR NEW.canonical_request_digest IS DISTINCT FROM OLD.canonical_request_digest
       OR NEW.requested_by          IS DISTINCT FROM OLD.requested_by
       OR NEW.requested_at          IS DISTINCT FROM OLD.requested_at THEN
        RAISE EXCEPTION 'calculation_request immutable fields cannot change'
            USING ERRCODE = 'check_violation';
    END IF;

    -- The result link is set exactly once, on commit (NULL -> non-NULL). The
    -- ck_creq_status_shape CHECK additionally pins committed_at; this is the
    -- transition's own defense in depth.
    IF OLD.result_calculation_result_id IS NOT NULL THEN
        RAISE EXCEPTION 'calculation_request result link is already set'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.result_calculation_result_id IS NULL THEN
        RAISE EXCEPTION 'committing a calculation_request requires a result link'
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ═══ calculation_request — dual-identity, single-transition reservation ═══
CREATE TABLE IF NOT EXISTS calculation_request (
    id                           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id                   UUID NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    calculation_kind             TEXT NOT NULL DEFAULT 'automation_roi'
                                 CHECK (calculation_kind = 'automation_roi'),
    formula_version              TEXT NOT NULL,
    idempotency_key              TEXT NOT NULL
                                 CHECK (char_length(idempotency_key) BETWEEN 1 AND 200),
    canonical_request_digest     TEXT NOT NULL
                                 CHECK (char_length(canonical_request_digest) = 64),
    status                       TEXT NOT NULL DEFAULT 'pending'
                                 CHECK (status IN ('pending', 'committed')),
    result_calculation_result_id UUID,
    requested_by                 TEXT NOT NULL DEFAULT '',
    requested_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    committed_at                 TIMESTAMPTZ,
    -- request identity: one row per (project, idempotency_key)
    CONSTRAINT uq_creq_request_identity   UNIQUE (project_id, idempotency_key),
    -- exact calculation-operation identity: one row per (project, digest), so at
    -- most one result is ever produced for a given six-input operation
    CONSTRAINT uq_creq_operation_identity UNIQUE (project_id, canonical_request_digest),
    CONSTRAINT uq_creq_id_project         UNIQUE (id, project_id),
    -- project-consistent link to the immutable result
    CONSTRAINT fk_creq_result_project
        FOREIGN KEY (result_calculation_result_id, project_id)
        REFERENCES calculation_result(id, project_id) ON DELETE RESTRICT,
    -- value presence is fixed by status
    CONSTRAINT ck_creq_status_shape CHECK (
        (status = 'pending'   AND result_calculation_result_id IS NULL     AND committed_at IS NULL)
        OR (status = 'committed' AND result_calculation_result_id IS NOT NULL AND committed_at IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_creq_project ON calculation_request(project_id);

-- ═══ Trigger (guarded; never drop an existing trigger) ═══
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_creq_controlled_transition'
                   AND tgrelid = 'calculation_request'::regclass) THEN
        CREATE TRIGGER trg_creq_controlled_transition
            BEFORE UPDATE OR DELETE ON calculation_request
            FOR EACH ROW EXECUTE FUNCTION sliceb_creq_guard();
    END IF;
END $$;

COMMIT;
