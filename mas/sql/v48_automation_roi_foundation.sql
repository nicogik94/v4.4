-- v4.8 Automation ROI Foundation (Slice B)
-- Additive persistence that turns approved Slice A evidence into deterministic
-- Automation ROI results with report-ready provenance.
--
-- Scope: CandidateFactExtractionContext, CandidateFactApprovalDecision,
-- ApprovedCalculationInput, CalculationResult, CalculationResultInput.
-- No API, no projections, no dashboards (those are PR2). No new database, no
-- in-memory fallback.
--
-- Properties (mirroring v47):
--   * additive only — no ALTER/DROP of existing application tables
--   * one explicit transaction boundary
--   * a preflight that asserts the v47 dependency and rejects a partial/divergent
--     Slice B schema, while keeping clean bootstrap valid and complete re-apply a no-op
--   * project-consistent composite foreign keys so direct SQL cannot create
--     cross-project links
--   * append-only enforcement via BEFORE UPDATE/DELETE triggers on all five tables
--     (reusing v47's slicea_reject_mutation())
--   * restrictive foreign keys (ON DELETE RESTRICT)
--   * no CREATE INDEX CONCURRENTLY
--
-- Apply manually AFTER sql/init.sql, sql/outcomes.sql, and
-- sql/v47_evidence_snapshot_foundation.sql:
--   psql -U workflow -d workflow_v4 -v ON_ERROR_STOP=1 -f sql/v48_automation_roi_foundation.sql

BEGIN;

-- ═══ Preflight: require v47, refuse partial / divergent Slice B schema ═══
DO $$
DECLARE
    v_b_tables  int;
    v_present   boolean;
    v_fn_count  int;
    v_trg_count int;
BEGIN
    -- v47 dependency: the Slice A append-only guard and the FK-target tables/uniques
    -- that v48 references must already exist. v48 never recreates them.
    IF NOT EXISTS (
        SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = current_schema() AND p.proname = 'slicea_reject_mutation'
    ) THEN
        RAISE EXCEPTION 'v48 requires v47: slicea_reject_mutation() is missing — apply v47 first'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint con
        WHERE con.conname = ANY (ARRAY[
            'uq_cfr_id_project', 'uq_source_snapshot_id_project'])
          AND con.connamespace = current_schema()::regnamespace
    ) THEN
        RAISE EXCEPTION 'v48 requires v47: Slice A composite-unique FK targets are missing — apply v47 first'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- Count the five Slice B tables.
    SELECT count(*) INTO v_b_tables
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'r' AND n.nspname = current_schema()
      AND c.relname = ANY (ARRAY[
          'candidate_fact_extraction_context', 'candidate_fact_approval_decision',
          'approved_calculation_input', 'calculation_result', 'calculation_result_input']);

    SELECT count(*) INTO v_fn_count
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = current_schema()
      AND p.proname = ANY (ARRAY['slicebo_assert_frozen_matches_fact', 'slicebo_assert_result_invariant']);

    SELECT count(*) INTO v_trg_count
    FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = current_schema() AND NOT t.tgisinternal
      AND t.tgname = ANY (ARRAY[
          'trg_cfec_no_mutation', 'trg_cfad_no_mutation', 'trg_aci_no_mutation',
          'trg_cr_no_mutation', 'trg_cri_no_mutation',
          'trg_aci_value_copy', 'trg_cr_result_invariant']);

    v_present := (v_b_tables > 0) OR (v_fn_count > 0) OR (v_trg_count > 0);

    IF NOT v_present THEN
        RETURN;  -- clean bootstrap: nothing exists yet; create everything.
    END IF;

    -- Something exists: it must satisfy the full v48 contract or the migration is
    -- refused. Unknown partial/divergent schema is never silently repaired.
    IF v_b_tables <> 5 THEN
        RAISE EXCEPTION
            'v48 contract violation: expected 5 Slice B tables, found % — schema is partial/divergent',
            v_b_tables USING ERRCODE = 'invalid_schema_definition';
    END IF;
    IF v_fn_count <> 2 THEN
        RAISE EXCEPTION
            'v48 contract violation: expected 2 Slice B guard functions, found %', v_fn_count
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
    IF v_trg_count <> 7 THEN
        RAISE EXCEPTION
            'v48 contract violation: expected 7 Slice B triggers, found %', v_trg_count
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
    -- Complete: downstream guarded DDL is a no-op.
END $$;

-- ═══ Guard functions ═══

-- Value-copy integrity: a frozen input's resolved_* must equal the immutable
-- source CandidateFactRevision (and the extraction context's period_basis).
-- Because facts are append-only/immutable, the invariant holds permanently.
CREATE OR REPLACE FUNCTION slicebo_assert_frozen_matches_fact()
RETURNS TRIGGER AS $$
DECLARE
    f         candidate_fact_revision%ROWTYPE;
    v_period  text;
BEGIN
    SELECT * INTO f FROM candidate_fact_revision
     WHERE id = NEW.candidate_fact_revision_id AND project_id = NEW.project_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'frozen input references unknown fact % in project %',
            NEW.candidate_fact_revision_id, NEW.project_id USING ERRCODE = 'foreign_key_violation';
    END IF;

    SELECT period_basis INTO v_period
    FROM candidate_fact_extraction_context
    WHERE candidate_fact_revision_id = NEW.candidate_fact_revision_id AND project_id = NEW.project_id;
    -- FOUND reliably reflects whether a context row exists (the nullable
    -- period_basis is not a presence signal). The fk_aci_eligible_context FK
    -- remains the primary eligibility enforcement; this is defense-in-depth.
    IF NOT FOUND THEN
        RAISE EXCEPTION 'frozen input fact % has no extraction context (ROI-ineligible)',
            NEW.candidate_fact_revision_id USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.resolved_numeric_value IS DISTINCT FROM f.numeric_value
       OR NEW.resolved_unit          IS DISTINCT FROM f.unit
       OR NEW.resolved_currency_code IS DISTINCT FROM f.currency_code
       OR NEW.resolved_time_unit     IS DISTINCT FROM f.time_unit
       OR NEW.as_of_date             IS DISTINCT FROM f.as_of_date
       OR NEW.resolved_period        IS DISTINCT FROM v_period THEN
        RAISE EXCEPTION
            'frozen input resolved_* must equal the source fact / context (fact %)',
            NEW.candidate_fact_revision_id USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Deferred result invariant: every persisted result links exactly the six
-- required roles; valid/not_applicable additionally require each linked input's
-- approve to be active and its evidence available. blocked is exempt.
CREATE OR REPLACE FUNCTION slicebo_assert_result_invariant()
RETURNS TRIGGER AS $$
DECLARE
    v_status text;
    v_roles  text[];
    v_bad    int;
BEGIN
    SELECT status INTO v_status FROM calculation_result WHERE id = NEW.id;
    IF v_status IS NULL THEN
        RETURN NULL;  -- result row absent (append-only prevents deletion); defensive
    END IF;

    SELECT array_agg(input_role ORDER BY input_role) INTO v_roles
    FROM calculation_result_input WHERE calculation_result_id = NEW.id;

    IF v_roles IS DISTINCT FROM ARRAY[
        'annual_recurring_cost', 'baseline_hours_per_period', 'fully_loaded_rate_per_hour',
        'one_time_implementation_cost', 'periods_per_year', 'post_automation_hours_per_period'
    ] THEN
        RAISE EXCEPTION
            'Slice B result % must link exactly the six required input roles, found %',
            NEW.id, v_roles USING ERRCODE = 'check_violation';
    END IF;

    IF v_status IN ('valid', 'not_applicable') THEN
        SELECT count(*) INTO v_bad
        FROM calculation_result_input cri
        JOIN approved_calculation_input aci ON aci.id = cri.approved_calculation_input_id
        WHERE cri.calculation_result_id = NEW.id
          AND (
              EXISTS (
                  SELECT 1 FROM candidate_fact_approval_decision r
                  WHERE r.revokes_decision_id = aci.approval_decision_id)
              OR EXISTS (
                  SELECT 1
                  FROM candidate_fact_revision f
                  JOIN source_snapshot s ON s.id = f.source_snapshot_id
                  JOIN evidence_retention_event e
                    ON e.event_type IN ('tombstone', 'redact')
                   AND (e.candidate_fact_revision_id = f.id
                        OR e.source_snapshot_id = s.id
                        OR e.source_blob_id = s.source_blob_id)
                  WHERE f.id = aci.candidate_fact_revision_id)
          );
        IF v_bad > 0 THEN
            RAISE EXCEPTION
                'Slice B result % is % but % linked input(s) are revoked or unavailable',
                NEW.id, v_status, v_bad USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- ═══ CandidateFactExtractionContext — ROI-eligibility carrier (1:1 with a fact) ═══
CREATE TABLE IF NOT EXISTS candidate_fact_extraction_context (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id                 UUID NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    candidate_fact_revision_id UUID NOT NULL,
    subject_label              TEXT NOT NULL,
    metric_label               TEXT NOT NULL,
    period_basis               TEXT,
    source_locator             TEXT NOT NULL DEFAULT '',
    source_char_range          TEXT,
    extraction_rationale       TEXT NOT NULL DEFAULT '',
    extracted_by               TEXT NOT NULL DEFAULT '',
    extracted_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_cfec_id_project   UNIQUE (id, project_id),
    CONSTRAINT uq_cfec_fact_project UNIQUE (candidate_fact_revision_id, project_id),
    CONSTRAINT fk_cfec_fact_project
        FOREIGN KEY (candidate_fact_revision_id, project_id)
        REFERENCES candidate_fact_revision(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT ck_cfec_rationale_bound CHECK (char_length(extraction_rationale) <= 2000),
    CONSTRAINT ck_cfec_labels CHECK (char_length(subject_label) > 0 AND char_length(metric_label) > 0)
);

CREATE INDEX IF NOT EXISTS idx_cfec_fact ON candidate_fact_extraction_context(candidate_fact_revision_id);
CREATE INDEX IF NOT EXISTS idx_cfec_project ON candidate_fact_extraction_context(project_id);

-- ═══ CandidateFactApprovalDecision — append-only deterministic decision chain ═══
CREATE TABLE IF NOT EXISTS candidate_fact_approval_decision (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id                 UUID NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    candidate_fact_revision_id UUID NOT NULL,
    decision_type              TEXT NOT NULL CHECK (decision_type IN ('approve', 'reject', 'withdraw')),
    decision_seq               INTEGER NOT NULL CHECK (decision_seq >= 1),
    revokes_decision_id        UUID,
    revoked_decision_type      TEXT,
    decision_reason            TEXT NOT NULL DEFAULT '',
    decided_by                 TEXT NOT NULL DEFAULT '',
    decided_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_cfad_id_project              UNIQUE (id, project_id),
    CONSTRAINT uq_cfad_id_fact_project         UNIQUE (id, candidate_fact_revision_id, project_id),
    CONSTRAINT uq_cfad_id_type_fact_project    UNIQUE (id, decision_type, candidate_fact_revision_id, project_id),
    CONSTRAINT uq_cfad_fact_seq                UNIQUE (candidate_fact_revision_id, decision_seq),
    CONSTRAINT fk_cfad_fact_project
        FOREIGN KEY (candidate_fact_revision_id, project_id)
        REFERENCES candidate_fact_revision(id, project_id) ON DELETE RESTRICT,
    -- approve rows carry no revocation; reject/withdraw must revoke an approve
    CONSTRAINT ck_cfad_revoke_shape CHECK (
        (revokes_decision_id IS NULL AND revoked_decision_type IS NULL AND decision_type = 'approve')
        OR (revokes_decision_id IS NOT NULL AND revoked_decision_type = 'approve'
            AND decision_type IN ('reject', 'withdraw'))
    ),
    -- a revoke must target an 'approve' of the SAME fact (purely FK-enforced)
    CONSTRAINT fk_cfad_revokes_approve
        FOREIGN KEY (revokes_decision_id, revoked_decision_type, candidate_fact_revision_id, project_id)
        REFERENCES candidate_fact_approval_decision(id, decision_type, candidate_fact_revision_id, project_id)
        ON DELETE RESTRICT
);

-- an approve can be revoked at most once
CREATE UNIQUE INDEX IF NOT EXISTS uq_cfad_revokes_once
    ON candidate_fact_approval_decision(revokes_decision_id)
    WHERE revokes_decision_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cfad_fact ON candidate_fact_approval_decision(candidate_fact_revision_id);
CREATE INDEX IF NOT EXISTS idx_cfad_project ON candidate_fact_approval_decision(project_id);

-- ═══ ApprovedCalculationInput — immutable freeze of one approved fact per role ═══
CREATE TABLE IF NOT EXISTS approved_calculation_input (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id                 UUID NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    calculation_kind           TEXT NOT NULL DEFAULT 'automation_roi'
                               CHECK (calculation_kind = 'automation_roi'),
    input_role                 TEXT NOT NULL CHECK (input_role IN (
                                   'baseline_hours_per_period', 'post_automation_hours_per_period',
                                   'fully_loaded_rate_per_hour', 'periods_per_year',
                                   'annual_recurring_cost', 'one_time_implementation_cost')),
    candidate_fact_revision_id UUID NOT NULL,
    approval_decision_id       UUID NOT NULL,
    approval_decision_type     TEXT NOT NULL DEFAULT 'approve' CHECK (approval_decision_type = 'approve'),
    resolved_numeric_value     NUMERIC NOT NULL,
    resolved_unit              TEXT NOT NULL DEFAULT '',
    resolved_currency_code     TEXT,
    resolved_period            TEXT,
    resolved_time_unit         TEXT,
    as_of_date                 DATE,
    frozen_by                  TEXT NOT NULL DEFAULT '',
    frozen_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_aci_id_project      UNIQUE (id, project_id),
    CONSTRAINT uq_aci_id_role_project UNIQUE (id, input_role, project_id),
    -- decision is an 'approve' of the SAME fact (composite FK incl. decision_type)
    CONSTRAINT fk_aci_decision_approve
        FOREIGN KEY (approval_decision_id, approval_decision_type, candidate_fact_revision_id, project_id)
        REFERENCES candidate_fact_approval_decision(id, decision_type, candidate_fact_revision_id, project_id)
        ON DELETE RESTRICT,
    -- fact is ROI-eligible (has exactly one extraction context)
    CONSTRAINT fk_aci_eligible_context
        FOREIGN KEY (candidate_fact_revision_id, project_id)
        REFERENCES candidate_fact_extraction_context(candidate_fact_revision_id, project_id)
        ON DELETE RESTRICT,
    -- per-role typing of the frozen resolved values
    CONSTRAINT ck_aci_role_typing CHECK (
        CASE input_role
            WHEN 'baseline_hours_per_period'      THEN resolved_time_unit = 'hours' AND resolved_numeric_value >= 0
            WHEN 'post_automation_hours_per_period' THEN resolved_time_unit = 'hours' AND resolved_numeric_value >= 0
            WHEN 'fully_loaded_rate_per_hour'     THEN resolved_unit = 'per_hour' AND resolved_currency_code IS NOT NULL AND resolved_numeric_value >= 0
            WHEN 'periods_per_year'               THEN resolved_numeric_value = trunc(resolved_numeric_value) AND resolved_numeric_value >= 1
            WHEN 'annual_recurring_cost'          THEN resolved_currency_code IS NOT NULL AND resolved_numeric_value >= 0
            WHEN 'one_time_implementation_cost'   THEN resolved_currency_code IS NOT NULL AND resolved_numeric_value >= 0
            ELSE FALSE
        END
    )
);

CREATE INDEX IF NOT EXISTS idx_aci_fact ON approved_calculation_input(candidate_fact_revision_id);
CREATE INDEX IF NOT EXISTS idx_aci_project ON approved_calculation_input(project_id);

-- ═══ CalculationResult — immutable deterministic output ═══
CREATE TABLE IF NOT EXISTS calculation_result (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id             UUID NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    calculation_kind       TEXT NOT NULL DEFAULT 'automation_roi'
                           CHECK (calculation_kind = 'automation_roi'),
    formula_version        TEXT NOT NULL,
    status                 TEXT NOT NULL CHECK (status IN ('valid', 'not_applicable', 'blocked')),
    currency_code          TEXT,
    annual_labor_savings   NUMERIC,
    annual_net_benefit     NUMERIC,
    first_year_net_benefit NUMERIC,
    first_year_roi_percent NUMERIC,
    roi_percent_status     TEXT NOT NULL DEFAULT 'computed'
                           CHECK (roi_percent_status IN ('computed', 'not_applicable', 'blocked')),
    formula_input_digest   TEXT NOT NULL,
    provenance_fingerprint TEXT NOT NULL,
    diagnostics            JSONB NOT NULL DEFAULT '{}'::jsonb,
    computed_by            TEXT NOT NULL DEFAULT '',
    computed_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_cr_id_project UNIQUE (id, project_id),
    -- value presence is fixed by status
    CONSTRAINT ck_cr_value_shape CHECK (
        (status = 'valid' AND annual_labor_savings IS NOT NULL AND annual_net_benefit IS NOT NULL
            AND first_year_net_benefit IS NOT NULL AND first_year_roi_percent IS NOT NULL)
        OR (status = 'not_applicable' AND annual_labor_savings IS NOT NULL AND annual_net_benefit IS NOT NULL
            AND first_year_net_benefit IS NOT NULL AND first_year_roi_percent IS NULL)
        OR (status = 'blocked' AND annual_labor_savings IS NULL AND annual_net_benefit IS NULL
            AND first_year_net_benefit IS NULL AND first_year_roi_percent IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_cr_project ON calculation_result(project_id);

-- ═══ CalculationResultInput — immutable link result→input, keyed by role ═══
CREATE TABLE IF NOT EXISTS calculation_result_input (
    id                            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id                    UUID NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    calculation_result_id         UUID NOT NULL,
    approved_calculation_input_id UUID NOT NULL,
    input_role                    TEXT NOT NULL,
    CONSTRAINT uq_cri_result_role UNIQUE (calculation_result_id, input_role),
    CONSTRAINT fk_cri_result_project
        FOREIGN KEY (calculation_result_id, project_id)
        REFERENCES calculation_result(id, project_id) ON DELETE RESTRICT,
    -- result_input.input_role must equal the frozen input's input_role (composite FK)
    CONSTRAINT fk_cri_input_role
        FOREIGN KEY (approved_calculation_input_id, input_role, project_id)
        REFERENCES approved_calculation_input(id, input_role, project_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_cri_result ON calculation_result_input(calculation_result_id);
CREATE INDEX IF NOT EXISTS idx_cri_input ON calculation_result_input(approved_calculation_input_id);

-- ═══ Triggers (guarded; never drop an existing trigger) ═══
DO $$
BEGIN
    -- append-only on all five tables (reuse v47's slicea_reject_mutation())
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_cfec_no_mutation'
                   AND tgrelid = 'candidate_fact_extraction_context'::regclass) THEN
        CREATE TRIGGER trg_cfec_no_mutation BEFORE UPDATE OR DELETE ON candidate_fact_extraction_context
            FOR EACH ROW EXECUTE FUNCTION slicea_reject_mutation();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_cfad_no_mutation'
                   AND tgrelid = 'candidate_fact_approval_decision'::regclass) THEN
        CREATE TRIGGER trg_cfad_no_mutation BEFORE UPDATE OR DELETE ON candidate_fact_approval_decision
            FOR EACH ROW EXECUTE FUNCTION slicea_reject_mutation();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_aci_no_mutation'
                   AND tgrelid = 'approved_calculation_input'::regclass) THEN
        CREATE TRIGGER trg_aci_no_mutation BEFORE UPDATE OR DELETE ON approved_calculation_input
            FOR EACH ROW EXECUTE FUNCTION slicea_reject_mutation();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_cr_no_mutation'
                   AND tgrelid = 'calculation_result'::regclass) THEN
        CREATE TRIGGER trg_cr_no_mutation BEFORE UPDATE OR DELETE ON calculation_result
            FOR EACH ROW EXECUTE FUNCTION slicea_reject_mutation();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_cri_no_mutation'
                   AND tgrelid = 'calculation_result_input'::regclass) THEN
        CREATE TRIGGER trg_cri_no_mutation BEFORE UPDATE OR DELETE ON calculation_result_input
            FOR EACH ROW EXECUTE FUNCTION slicea_reject_mutation();
    END IF;

    -- value-copy integrity on frozen-input insert
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_aci_value_copy'
                   AND tgrelid = 'approved_calculation_input'::regclass) THEN
        CREATE TRIGGER trg_aci_value_copy BEFORE INSERT ON approved_calculation_input
            FOR EACH ROW EXECUTE FUNCTION slicebo_assert_frozen_matches_fact();
    END IF;

    -- deferred result invariant (checked at commit)
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_cr_result_invariant'
                   AND tgrelid = 'calculation_result'::regclass) THEN
        CREATE CONSTRAINT TRIGGER trg_cr_result_invariant AFTER INSERT ON calculation_result
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION slicebo_assert_result_invariant();
    END IF;
END $$;

COMMIT;
