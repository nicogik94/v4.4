-- W8.1 — durable decision-state coherence foundation
-- Additive, idempotent, and provider-independent. Run after sql/init.sql.

BEGIN;

DO $$
DECLARE
    relation_count INTEGER;
    column_count INTEGER;
    constraint_count INTEGER;
    trigger_count INTEGER;
BEGIN
    SELECT count(*) INTO relation_count
    FROM pg_catalog.pg_class relation
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = pg_catalog.current_schema()
      AND relation.relkind = 'r'
      AND relation.relname IN (
          'decision_input_snapshots',
          'analysis_generations',
          'current_analysis_generations'
      );
    IF relation_count NOT IN (0, 3) THEN
        RAISE EXCEPTION 'v64 partial decision-state schema detected';
    END IF;
    IF relation_count = 3 THEN
        SELECT count(*) INTO column_count
        FROM information_schema.columns
        WHERE table_schema = pg_catalog.current_schema()
          AND table_name IN (
              'decision_input_snapshots',
              'analysis_generations',
              'current_analysis_generations'
          );
        SELECT count(*) INTO constraint_count
        FROM pg_catalog.pg_constraint constraint_info
        JOIN pg_catalog.pg_class relation ON relation.oid = constraint_info.conrelid
        JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = pg_catalog.current_schema()
          AND relation.relname IN (
              'decision_input_snapshots',
              'analysis_generations',
              'current_analysis_generations'
          )
          AND constraint_info.convalidated;
        SELECT count(*) INTO trigger_count
        FROM pg_catalog.pg_trigger trigger_info
        JOIN pg_catalog.pg_class relation ON relation.oid = trigger_info.tgrelid
        JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = pg_catalog.current_schema()
          AND trigger_info.tgname IN (
              'trg_dis_immutable', 'trg_ag_immutable', 'trg_cag_guard'
          )
          AND NOT trigger_info.tgisinternal;
        IF column_count <> 27 OR constraint_count <> 18 OR trigger_count <> 3 THEN
            RAISE EXCEPTION 'v64 decision-state catalog drift detected';
        END IF;
    END IF;
END;
$$;

CREATE TABLE IF NOT EXISTS decision_input_snapshots (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    decision_id TEXT NOT NULL,
    effective_input_sha256 TEXT NOT NULL,
    effective_input_json JSONB NOT NULL,
    contract_version TEXT NOT NULL,
    predecessor_snapshot_id UUID,
    change_cause_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_dis_sha256 CHECK (effective_input_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT uq_dis_scope_hash UNIQUE (project_id, decision_id, effective_input_sha256),
    CONSTRAINT uq_dis_id_scope UNIQUE (id, project_id, decision_id),
    CONSTRAINT fk_dis_predecessor_same_scope
        FOREIGN KEY (predecessor_snapshot_id, project_id, decision_id)
        REFERENCES decision_input_snapshots (id, project_id, decision_id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS analysis_generations (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL,
    decision_id TEXT NOT NULL,
    effective_input_snapshot_id UUID NOT NULL,
    workflow_fingerprint TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL DEFAULT 'candidate',
    expected_base_generation_id UUID,
    analysis_state_sha256 TEXT NOT NULL,
    analysis_state_json JSONB NOT NULL,
    validated_at TIMESTAMPTZ,
    promoted_at TIMESTAMPTZ,
    terminal_at TIMESTAMPTZ,
    bootstrap_kind TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_ag_project FOREIGN KEY (project_id)
        REFERENCES projects (id) ON DELETE CASCADE,
    CONSTRAINT ck_ag_status CHECK (
        lifecycle_status IN ('candidate', 'accepted', 'failed', 'aborted')
    ),
    CONSTRAINT ck_ag_state_sha256 CHECK (analysis_state_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_ag_terminal_shape CHECK (
        (lifecycle_status = 'candidate' AND promoted_at IS NULL AND terminal_at IS NULL)
        OR (lifecycle_status = 'accepted' AND validated_at IS NOT NULL AND promoted_at IS NOT NULL AND terminal_at IS NULL)
        OR (lifecycle_status IN ('failed', 'aborted') AND promoted_at IS NULL AND terminal_at IS NOT NULL)
    ),
    CONSTRAINT uq_ag_id_scope UNIQUE (id, project_id, decision_id),
    CONSTRAINT fk_ag_snapshot_same_scope
        FOREIGN KEY (effective_input_snapshot_id, project_id, decision_id)
        REFERENCES decision_input_snapshots (id, project_id, decision_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_ag_expected_base_same_scope
        FOREIGN KEY (expected_base_generation_id, project_id, decision_id)
        REFERENCES analysis_generations (id, project_id, decision_id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS current_analysis_generations (
    project_id UUID NOT NULL,
    decision_id TEXT NOT NULL,
    generation_id UUID NOT NULL,
    promoted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (project_id, decision_id),
    CONSTRAINT fk_cag_project FOREIGN KEY (project_id)
        REFERENCES projects (id) ON DELETE CASCADE,
    CONSTRAINT uq_cag_generation UNIQUE (generation_id),
    CONSTRAINT fk_cag_generation_same_scope
        FOREIGN KEY (generation_id, project_id, decision_id)
        REFERENCES analysis_generations (id, project_id, decision_id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_dis_project_created
    ON decision_input_snapshots (project_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_ag_scope_created
    ON analysis_generations (project_id, decision_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_ag_snapshot
    ON analysis_generations (effective_input_snapshot_id);

CREATE OR REPLACE FUNCTION decision_state_reject_snapshot_mutation()
RETURNS trigger
LANGUAGE plpgsql
SET search_path FROM CURRENT
AS $$
BEGIN
    IF TG_OP = 'DELETE' AND NOT EXISTS (
        SELECT 1 FROM projects WHERE id = OLD.project_id
    ) THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'decision input snapshots are immutable';
END;
$$;

DROP TRIGGER IF EXISTS trg_dis_immutable ON decision_input_snapshots;
CREATE TRIGGER trg_dis_immutable
BEFORE UPDATE OR DELETE ON decision_input_snapshots
FOR EACH ROW EXECUTE FUNCTION decision_state_reject_snapshot_mutation();

CREATE OR REPLACE FUNCTION decision_state_guard_generation_mutation()
RETURNS trigger
LANGUAGE plpgsql
SET search_path FROM CURRENT
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF NOT EXISTS (SELECT 1 FROM projects WHERE id = OLD.project_id) THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'analysis generations are immutable historical records';
    END IF;

    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.decision_id IS DISTINCT FROM OLD.decision_id
       OR NEW.effective_input_snapshot_id IS DISTINCT FROM OLD.effective_input_snapshot_id
       OR NEW.workflow_fingerprint IS DISTINCT FROM OLD.workflow_fingerprint
       OR NEW.expected_base_generation_id IS DISTINCT FROM OLD.expected_base_generation_id
       OR NEW.analysis_state_sha256 IS DISTINCT FROM OLD.analysis_state_sha256
       OR NEW.analysis_state_json IS DISTINCT FROM OLD.analysis_state_json
       OR NEW.bootstrap_kind IS DISTINCT FROM OLD.bootstrap_kind
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'analysis generation identity and content are immutable';
    END IF;

    IF OLD.lifecycle_status <> 'candidate' THEN
        RAISE EXCEPTION 'terminal analysis generation lifecycle is immutable';
    END IF;
    IF NEW.lifecycle_status = 'candidate' THEN
        IF OLD.validated_at IS NOT NULL
           OR NEW.validated_at IS NULL
           OR NEW.promoted_at IS NOT NULL
           OR NEW.terminal_at IS NOT NULL THEN
            RAISE EXCEPTION 'invalid candidate validation transition';
        END IF;
    ELSIF NEW.lifecycle_status = 'accepted' THEN
        IF OLD.validated_at IS NULL
           OR NEW.validated_at IS DISTINCT FROM OLD.validated_at
           OR NEW.promoted_at IS NULL
           OR NEW.terminal_at IS NOT NULL THEN
            RAISE EXCEPTION 'only a validated candidate may be accepted';
        END IF;
    ELSIF NEW.lifecycle_status IN ('failed', 'aborted') THEN
        IF NEW.promoted_at IS NOT NULL OR NEW.terminal_at IS NULL THEN
            RAISE EXCEPTION 'invalid terminal candidate transition';
        END IF;
    ELSE
        RAISE EXCEPTION 'invalid analysis generation lifecycle transition';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_ag_immutable ON analysis_generations;
CREATE TRIGGER trg_ag_immutable
BEFORE UPDATE OR DELETE ON analysis_generations
FOR EACH ROW EXECUTE FUNCTION decision_state_guard_generation_mutation();

CREATE OR REPLACE FUNCTION decision_state_guard_current_binding()
RETURNS trigger
LANGUAGE plpgsql
SET search_path FROM CURRENT
AS $$
DECLARE
    generation_status TEXT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF NOT EXISTS (SELECT 1 FROM projects WHERE id = OLD.project_id) THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'current analysis binding cannot be deleted directly';
    END IF;
    SELECT lifecycle_status INTO generation_status
    FROM analysis_generations
    WHERE id = NEW.generation_id
      AND project_id = NEW.project_id
      AND decision_id = NEW.decision_id;
    IF generation_status IS DISTINCT FROM 'accepted' THEN
        RAISE EXCEPTION 'current analysis must reference an accepted same-scope generation';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_cag_guard ON current_analysis_generations;
CREATE TRIGGER trg_cag_guard
BEFORE INSERT OR UPDATE OR DELETE ON current_analysis_generations
FOR EACH ROW EXECUTE FUNCTION decision_state_guard_current_binding();

CREATE OR REPLACE FUNCTION promote_analysis_generation(
    candidate_id UUID,
    expected_base_id UUID DEFAULT NULL
)
RETURNS VOID
LANGUAGE plpgsql
SET search_path FROM CURRENT
AS $$
DECLARE
    candidate analysis_generations%ROWTYPE;
    actual_base UUID;
BEGIN
    SELECT * INTO candidate
    FROM analysis_generations
    WHERE id = candidate_id
    FOR UPDATE;
    IF NOT FOUND OR candidate.lifecycle_status <> 'candidate' THEN
        RAISE EXCEPTION 'generation is not an active candidate';
    END IF;
    IF candidate.validated_at IS NULL THEN
        RAISE EXCEPTION 'candidate has not been validated';
    END IF;
    IF candidate.expected_base_generation_id IS DISTINCT FROM expected_base_id THEN
        RAISE EXCEPTION 'promotion expected-base does not match candidate';
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtextextended(candidate.project_id::text || '|' || candidate.decision_id, 0)
    );
    SELECT generation_id INTO actual_base
    FROM current_analysis_generations
    WHERE project_id = candidate.project_id
      AND decision_id = candidate.decision_id
    FOR UPDATE;
    IF actual_base IS DISTINCT FROM expected_base_id THEN
        RAISE EXCEPTION 'current analysis changed after candidate creation';
    END IF;

    UPDATE analysis_generations
    SET lifecycle_status = 'accepted', promoted_at = NOW()
    WHERE id = candidate_id;

    INSERT INTO current_analysis_generations (project_id, decision_id, generation_id)
    VALUES (candidate.project_id, candidate.decision_id, candidate.id)
    ON CONFLICT (project_id, decision_id) DO UPDATE
    SET generation_id = EXCLUDED.generation_id,
        promoted_at = NOW();
END;
$$;

CREATE OR REPLACE FUNCTION bootstrap_analysis_generation(
    snapshot_id UUID,
    project_scope UUID,
    decision_scope TEXT,
    input_sha256 TEXT,
    input_json JSONB,
    input_contract TEXT,
    generation_id UUID,
    state_sha256 TEXT,
    state_json JSONB
)
RETURNS UUID
LANGUAGE plpgsql
SET search_path FROM CURRENT
AS $$
DECLARE
    existing_current UUID;
    existing_snapshot UUID;
    existing_state_sha256 TEXT;
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended(project_scope::text || '|' || decision_scope, 0)
    );
    SELECT current_generation.generation_id,
           generation.effective_input_snapshot_id,
           generation.analysis_state_sha256
      INTO existing_current, existing_snapshot, existing_state_sha256
    FROM current_analysis_generations current_generation
    JOIN analysis_generations generation
      ON generation.id = current_generation.generation_id
    WHERE current_generation.project_id = project_scope
      AND current_generation.decision_id = decision_scope;
    IF existing_current IS NOT NULL THEN
        IF existing_snapshot = snapshot_id
           AND existing_state_sha256 = state_sha256 THEN
            RETURN existing_current;
        END IF;
        RETURN NULL;
    END IF;

    INSERT INTO decision_input_snapshots (
        id, project_id, decision_id, effective_input_sha256,
        effective_input_json, contract_version
    ) VALUES (
        snapshot_id, project_scope, decision_scope, input_sha256,
        input_json, input_contract
    ) ON CONFLICT (project_id, decision_id, effective_input_sha256) DO NOTHING;

    INSERT INTO analysis_generations (
        id, project_id, decision_id, effective_input_snapshot_id,
        workflow_fingerprint, lifecycle_status,
        analysis_state_sha256, analysis_state_json,
        validated_at, promoted_at, bootstrap_kind
    ) VALUES (
        generation_id, project_scope, decision_scope, snapshot_id,
        'legacy-baseline.v1', 'accepted', state_sha256, state_json,
        NOW(), NOW(), 'legacy_baseline'
    ) ON CONFLICT (id) DO NOTHING;

    INSERT INTO current_analysis_generations (project_id, decision_id, generation_id)
    VALUES (project_scope, decision_scope, generation_id);
    RETURN generation_id;
END;
$$;

-- Core MAS deployments use the owner role for migrations and runtime. Keep
-- these state-transition functions owner-only instead of inheriting
-- PostgreSQL's default PUBLIC EXECUTE privilege.
REVOKE ALL ON FUNCTION decision_state_reject_snapshot_mutation() FROM PUBLIC;
REVOKE ALL ON FUNCTION decision_state_guard_generation_mutation() FROM PUBLIC;
REVOKE ALL ON FUNCTION decision_state_guard_current_binding() FROM PUBLIC;
REVOKE ALL ON FUNCTION promote_analysis_generation(UUID, UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION bootstrap_analysis_generation(
    UUID, UUID, TEXT, TEXT, JSONB, TEXT, UUID, TEXT, JSONB
) FROM PUBLIC;

-- Exact postflight: count-only checks cannot detect same-count weakening. The
-- functions/triggers above are deterministically reissued on reapply; this
-- manifest verifies their exact attachment and every column/constraint that
-- protects identity, scope, immutability, and currentness.
DO $$
DECLARE
    problem TEXT;
    projects_owner OID;
BEGIN
    WITH expected(tbl, col, typ, required, default_expr) AS (VALUES
        ('analysis_generations','analysis_state_json','jsonb',true,''),
        ('analysis_generations','analysis_state_sha256','text',true,''),
        ('analysis_generations','bootstrap_kind','text',false,''),
        ('analysis_generations','created_at','timestamp with time zone',true,'now()'),
        ('analysis_generations','decision_id','text',true,''),
        ('analysis_generations','effective_input_snapshot_id','uuid',true,''),
        ('analysis_generations','expected_base_generation_id','uuid',false,''),
        ('analysis_generations','id','uuid',true,''),
        ('analysis_generations','lifecycle_status','text',true,'''candidate''::text'),
        ('analysis_generations','project_id','uuid',true,''),
        ('analysis_generations','promoted_at','timestamp with time zone',false,''),
        ('analysis_generations','terminal_at','timestamp with time zone',false,''),
        ('analysis_generations','validated_at','timestamp with time zone',false,''),
        ('analysis_generations','workflow_fingerprint','text',true,''),
        ('current_analysis_generations','decision_id','text',true,''),
        ('current_analysis_generations','generation_id','uuid',true,''),
        ('current_analysis_generations','project_id','uuid',true,''),
        ('current_analysis_generations','promoted_at','timestamp with time zone',true,'now()'),
        ('decision_input_snapshots','change_cause_id','text',false,''),
        ('decision_input_snapshots','contract_version','text',true,''),
        ('decision_input_snapshots','created_at','timestamp with time zone',true,'now()'),
        ('decision_input_snapshots','decision_id','text',true,''),
        ('decision_input_snapshots','effective_input_json','jsonb',true,''),
        ('decision_input_snapshots','effective_input_sha256','text',true,''),
        ('decision_input_snapshots','id','uuid',true,''),
        ('decision_input_snapshots','predecessor_snapshot_id','uuid',false,''),
        ('decision_input_snapshots','project_id','uuid',true,'')
    ), actual(tbl, col, typ, required, default_expr) AS (
        SELECT relation.relname::text, attribute.attname::text,
               format_type(attribute.atttypid, attribute.atttypmod)::text,
               attribute.attnotnull,
               coalesce(pg_get_expr(default_info.adbin, default_info.adrelid), '')::text
        FROM pg_attribute attribute
        JOIN pg_class relation ON relation.oid = attribute.attrelid
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        LEFT JOIN pg_attrdef default_info
          ON default_info.adrelid = attribute.attrelid
         AND default_info.adnum = attribute.attnum
        WHERE namespace.nspname = current_schema()
          AND relation.relname IN (
              'decision_input_snapshots', 'analysis_generations',
              'current_analysis_generations'
          )
          AND attribute.attnum > 0 AND NOT attribute.attisdropped
    )
    SELECT string_agg(format('%s.%s(%s)', drift.tbl, drift.col, drift.side), ', ')
      INTO problem
    FROM (
        SELECT tbl, col, 'missing_or_altered' AS side FROM (
            SELECT * FROM expected EXCEPT SELECT * FROM actual
        ) missing
        UNION ALL
        SELECT tbl, col, 'unexpected' FROM (
            SELECT * FROM actual EXCEPT SELECT * FROM expected
        ) unexpected
    ) drift;
    IF problem IS NOT NULL THEN
        RAISE EXCEPTION 'v64 postflight column drift: %', problem
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    WITH expected(tbl, name, definition) AS (VALUES
        ('analysis_generations','analysis_generations_pkey','PRIMARY KEY (id)'),
        ('analysis_generations','ck_ag_state_sha256','CHECK ((analysis_state_sha256 ~ ''^[0-9a-f]{64}$''::text))'),
        ('analysis_generations','ck_ag_status','CHECK ((lifecycle_status = ANY (ARRAY[''candidate''::text, ''accepted''::text, ''failed''::text, ''aborted''::text])))'),
        ('analysis_generations','ck_ag_terminal_shape','CHECK ((((lifecycle_status = ''candidate''::text) AND (promoted_at IS NULL) AND (terminal_at IS NULL)) OR ((lifecycle_status = ''accepted''::text) AND (validated_at IS NOT NULL) AND (promoted_at IS NOT NULL) AND (terminal_at IS NULL)) OR ((lifecycle_status = ANY (ARRAY[''failed''::text, ''aborted''::text])) AND (promoted_at IS NULL) AND (terminal_at IS NOT NULL))))'),
        ('analysis_generations','fk_ag_expected_base_same_scope','FOREIGN KEY (expected_base_generation_id, project_id, decision_id) REFERENCES analysis_generations(id, project_id, decision_id) ON DELETE RESTRICT'),
        ('analysis_generations','fk_ag_project','FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE'),
        ('analysis_generations','fk_ag_snapshot_same_scope','FOREIGN KEY (effective_input_snapshot_id, project_id, decision_id) REFERENCES decision_input_snapshots(id, project_id, decision_id) ON DELETE RESTRICT'),
        ('analysis_generations','uq_ag_id_scope','UNIQUE (id, project_id, decision_id)'),
        ('current_analysis_generations','current_analysis_generations_pkey','PRIMARY KEY (project_id, decision_id)'),
        ('current_analysis_generations','fk_cag_generation_same_scope','FOREIGN KEY (generation_id, project_id, decision_id) REFERENCES analysis_generations(id, project_id, decision_id) ON DELETE RESTRICT'),
        ('current_analysis_generations','fk_cag_project','FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE'),
        ('current_analysis_generations','uq_cag_generation','UNIQUE (generation_id)'),
        ('decision_input_snapshots','ck_dis_sha256','CHECK ((effective_input_sha256 ~ ''^[0-9a-f]{64}$''::text))'),
        ('decision_input_snapshots','decision_input_snapshots_pkey','PRIMARY KEY (id)'),
        ('decision_input_snapshots','decision_input_snapshots_project_id_fkey','FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE'),
        ('decision_input_snapshots','fk_dis_predecessor_same_scope','FOREIGN KEY (predecessor_snapshot_id, project_id, decision_id) REFERENCES decision_input_snapshots(id, project_id, decision_id) ON DELETE RESTRICT'),
        ('decision_input_snapshots','uq_dis_id_scope','UNIQUE (id, project_id, decision_id)'),
        ('decision_input_snapshots','uq_dis_scope_hash','UNIQUE (project_id, decision_id, effective_input_sha256)')
    ), actual(tbl, name, definition) AS (
        SELECT relation.relname::text, constraint_info.conname::text,
               replace(pg_get_constraintdef(constraint_info.oid), current_schema() || '.', '')::text
        FROM pg_constraint constraint_info
        JOIN pg_class relation ON relation.oid = constraint_info.conrelid
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = current_schema()
          AND relation.relname IN (
              'decision_input_snapshots', 'analysis_generations',
              'current_analysis_generations'
          )
    )
    SELECT string_agg(format('%s.%s(%s)', drift.tbl, drift.name, drift.side), ', ')
      INTO problem
    FROM (
        SELECT tbl, name, 'missing_or_altered' AS side FROM (
            SELECT * FROM expected EXCEPT SELECT * FROM actual
        ) missing
        UNION ALL
        SELECT tbl, name, 'unexpected' FROM (
            SELECT * FROM actual EXCEPT SELECT * FROM expected
        ) unexpected
    ) drift;
    IF problem IS NOT NULL THEN
        RAISE EXCEPTION 'v64 postflight constraint drift: %', problem
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    WITH expected(tbl, trigger_name, function_name, trigger_type) AS (VALUES
        ('decision_input_snapshots','trg_dis_immutable','decision_state_reject_snapshot_mutation',27::smallint),
        ('analysis_generations','trg_ag_immutable','decision_state_guard_generation_mutation',27::smallint),
        ('current_analysis_generations','trg_cag_guard','decision_state_guard_current_binding',31::smallint)
    ), actual(tbl, trigger_name, function_name, trigger_type) AS (
        SELECT relation.relname::text, trigger_info.tgname::text,
               function_info.proname::text, trigger_info.tgtype
        FROM pg_trigger trigger_info
        JOIN pg_class relation ON relation.oid = trigger_info.tgrelid
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        JOIN pg_proc function_info ON function_info.oid = trigger_info.tgfoid
        WHERE namespace.nspname = current_schema()
          AND relation.relname IN (
              'decision_input_snapshots', 'analysis_generations',
              'current_analysis_generations'
          )
          AND trigger_info.tgenabled = 'O'
          AND trigger_info.tgqual IS NULL
          AND trigger_info.tgnargs = 0
          AND NOT trigger_info.tgisinternal
    )
    SELECT string_agg(format('%s.%s(%s)', drift.tbl, drift.trigger_name, drift.side), ', ')
      INTO problem
    FROM (
        SELECT tbl, trigger_name, 'missing_or_altered' AS side FROM (
            SELECT * FROM expected EXCEPT SELECT * FROM actual
        ) missing
        UNION ALL
        SELECT tbl, trigger_name, 'unexpected' FROM (
            SELECT * FROM actual EXCEPT SELECT * FROM expected
        ) unexpected
    ) drift;
    IF problem IS NOT NULL THEN
        RAISE EXCEPTION 'v64 postflight trigger drift: %', problem
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    WITH expected(name, arguments, language, security_definer, config) AS (VALUES
        ('decision_state_reject_snapshot_mutation','', 'plpgsql', false, ARRAY['search_path=' || current_schema()]),
        ('decision_state_guard_generation_mutation','', 'plpgsql', false, ARRAY['search_path=' || current_schema()]),
        ('decision_state_guard_current_binding','', 'plpgsql', false, ARRAY['search_path=' || current_schema()]),
        ('promote_analysis_generation','uuid, uuid', 'plpgsql', false, ARRAY['search_path=' || current_schema()]),
        ('bootstrap_analysis_generation','uuid, uuid, text, text, jsonb, text, uuid, text, jsonb', 'plpgsql', false, ARRAY['search_path=' || current_schema()])
    ), actual(name, arguments, language, security_definer, config) AS (
        SELECT function_info.proname::text,
               oidvectortypes(function_info.proargtypes)::text,
               language_info.lanname::text,
               function_info.prosecdef,
               function_info.proconfig
        FROM pg_proc function_info
        JOIN pg_namespace namespace ON namespace.oid = function_info.pronamespace
        JOIN pg_language language_info ON language_info.oid = function_info.prolang
        WHERE namespace.nspname = current_schema()
          AND function_info.proname IN (
              'decision_state_reject_snapshot_mutation',
              'decision_state_guard_generation_mutation',
              'decision_state_guard_current_binding',
              'promote_analysis_generation',
              'bootstrap_analysis_generation'
          )
    )
    SELECT string_agg(drift.name || '(' || drift.arguments || '):' || drift.side, ', ')
      INTO problem
    FROM (
        SELECT name, arguments, 'missing_or_altered' AS side FROM (
            SELECT * FROM expected EXCEPT SELECT * FROM actual
        ) missing
        UNION ALL
        SELECT name, arguments, 'unexpected' FROM (
            SELECT * FROM actual EXCEPT SELECT * FROM expected
        ) unexpected
    ) drift;
    IF problem IS NOT NULL THEN
        RAISE EXCEPTION 'v64 postflight function drift: %', problem
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT relowner INTO projects_owner
    FROM pg_class relation JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = current_schema() AND relation.relname = 'projects';
    SELECT string_agg(relation.relname, ', ') INTO problem
    FROM pg_class relation JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = current_schema()
      AND relation.relname IN (
          'decision_input_snapshots', 'analysis_generations',
          'current_analysis_generations'
      )
      AND (relation.relowner IS DISTINCT FROM projects_owner OR relation.relacl IS NOT NULL);
    IF problem IS NOT NULL THEN
        RAISE EXCEPTION 'v64 postflight owner/ACL drift: %', problem
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT string_agg(function_info.proname, ', ') INTO problem
    FROM pg_proc function_info
    JOIN pg_namespace namespace ON namespace.oid = function_info.pronamespace
    WHERE namespace.nspname = current_schema()
      AND function_info.proname IN (
          'decision_state_reject_snapshot_mutation',
          'decision_state_guard_generation_mutation',
          'decision_state_guard_current_binding',
          'promote_analysis_generation',
          'bootstrap_analysis_generation'
      )
      AND (
          function_info.proowner IS DISTINCT FROM projects_owner
          OR EXISTS (
              SELECT 1
              FROM aclexplode(
                  coalesce(function_info.proacl, acldefault('f', function_info.proowner))
              ) privilege
              WHERE privilege.grantee <> function_info.proowner
          )
      );
    IF problem IS NOT NULL THEN
        RAISE EXCEPTION 'v64 postflight function owner/ACL drift: %', problem
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
END;
$$;

COMMIT;
