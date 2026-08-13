-- W8.2 — governed direct-input revision lifecycle
-- Additive, idempotent, and provider-independent. Run after v64.

BEGIN;

-- Reapply fails closed before any repair-capable statement. Later migrations
-- may add unrelated objects, so this manifest owns only input_revisions and
-- its lifecycle trigger/function; it does not alter W8.1 snapshot relations.
DO $preflight$
DECLARE
    relation_exists BOOLEAN;
    function_count INTEGER;
    trigger_count INTEGER;
    projects_owner OID;
    problem TEXT;
BEGIN
    SELECT to_regclass(pg_catalog.current_schema() || '.input_revisions') IS NOT NULL
      INTO relation_exists;
    SELECT count(*) INTO function_count
    FROM pg_catalog.pg_proc function_info
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid = function_info.pronamespace
    WHERE namespace.nspname = pg_catalog.current_schema()
      AND function_info.proname = 'input_revision_guard_mutation';
    SELECT count(*) INTO trigger_count
    FROM pg_catalog.pg_trigger trigger_info
    JOIN pg_catalog.pg_class relation ON relation.oid = trigger_info.tgrelid
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = pg_catalog.current_schema()
      AND relation.relname = 'input_revisions'
      AND NOT trigger_info.tgisinternal;

    IF NOT relation_exists THEN
        IF function_count <> 0 OR trigger_count <> 0 THEN
            RAISE EXCEPTION 'v65 partial input-revision schema detected'
                USING ERRCODE = 'invalid_schema_definition';
        END IF;
        RETURN;
    END IF;

    WITH expected(col, typ, required, default_expr, generated) AS (VALUES
        ('affected_field_paths','text[]',true,'',''),
        ('applied_at','timestamp with time zone',false,'',''),
        ('applied_by','text',false,'',''),
        ('decision_id','text',true,'',''),
        ('expected_base_snapshot_id','uuid',true,'',''),
        ('id','uuid',true,'',''),
        ('lifecycle_status','text',true,'''proposed''::text',''),
        ('patch_json','jsonb',true,'',''),
        ('patch_sha256','text',true,'',''),
        ('project_id','uuid',true,'',''),
        ('proposed_at','timestamp with time zone',true,'now()',''),
        ('proposed_by','text',true,'',''),
        ('rationale','text',true,'',''),
        ('rejected_at','timestamp with time zone',false,'',''),
        ('rejected_by','text',false,'',''),
        ('rejection_rationale','text',false,'',''),
        ('resulting_snapshot_id','uuid',false,'',''),
        ('source_kind','text',true,'',''),
        ('source_reference','text',false,'','')
    ), actual(col, typ, required, default_expr, generated) AS (
        SELECT attribute.attname::text,
               pg_catalog.format_type(attribute.atttypid, attribute.atttypmod)::text,
               attribute.attnotnull,
               coalesce(
                   pg_catalog.pg_get_expr(default_info.adbin, default_info.adrelid), ''
               )::text,
               attribute.attgenerated::text
        FROM pg_catalog.pg_attribute attribute
        JOIN pg_catalog.pg_class relation ON relation.oid = attribute.attrelid
        JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
        LEFT JOIN pg_catalog.pg_attrdef default_info
          ON default_info.adrelid = attribute.attrelid
         AND default_info.adnum = attribute.attnum
        WHERE namespace.nspname = pg_catalog.current_schema()
          AND relation.relname = 'input_revisions'
          AND attribute.attnum > 0 AND NOT attribute.attisdropped
    )
    SELECT pg_catalog.string_agg(drift.col || ':' || drift.side, ', ' ORDER BY drift.col)
      INTO problem
    FROM (
        SELECT col, 'missing_or_altered' AS side FROM (
            SELECT * FROM expected EXCEPT SELECT * FROM actual
        ) missing
        UNION ALL
        SELECT col, 'unexpected' FROM (
            SELECT * FROM actual EXCEPT SELECT * FROM expected
        ) unexpected
    ) drift;
    IF problem IS NOT NULL THEN
        RAISE EXCEPTION 'v65 preflight column drift: %', problem
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    WITH expected(name, definition) AS (VALUES
        ('ck_ir_affected_paths','CHECK ((cardinality(affected_field_paths) > 0))'),
        ('ck_ir_lifecycle_shape','CHECK ((((lifecycle_status = ''proposed''::text) AND (applied_by IS NULL) AND (applied_at IS NULL) AND (rejected_by IS NULL) AND (rejected_at IS NULL) AND (resulting_snapshot_id IS NULL) AND (rejection_rationale IS NULL)) OR ((lifecycle_status = ''applied''::text) AND (applied_by IS NOT NULL) AND (applied_at IS NOT NULL) AND (rejected_by IS NULL) AND (rejected_at IS NULL) AND (resulting_snapshot_id IS NOT NULL) AND (rejection_rationale IS NULL)) OR ((lifecycle_status = ''rejected''::text) AND (applied_by IS NULL) AND (applied_at IS NULL) AND (rejected_by IS NOT NULL) AND (rejected_at IS NOT NULL) AND (resulting_snapshot_id IS NULL) AND (rejection_rationale IS NOT NULL))))'),
        ('ck_ir_patch_domain','CHECK (((patch_json - ARRAY[''project_name''::text, ''brief''::text, ''data''::text, ''output_language''::text, ''report_mode''::text, ''observations''::text, ''timer_logs''::text]) = ''{}''::jsonb))'),
        ('ck_ir_patch_object','CHECK (((jsonb_typeof(patch_json) = ''object''::text) AND (patch_json <> ''{}''::jsonb)))'),
        ('ck_ir_result_changes_base','CHECK (((resulting_snapshot_id IS NULL) OR (resulting_snapshot_id <> expected_base_snapshot_id)))'),
        ('ck_ir_sha256','CHECK ((patch_sha256 ~ ''^[0-9a-f]{64}$''::text))'),
        ('ck_ir_status','CHECK ((lifecycle_status = ANY (ARRAY[''proposed''::text, ''applied''::text, ''rejected''::text])))'),
        ('fk_ir_expected_base_same_scope','FOREIGN KEY (expected_base_snapshot_id, project_id, decision_id) REFERENCES decision_input_snapshots(id, project_id, decision_id) ON DELETE RESTRICT'),
        ('fk_ir_project','FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE'),
        ('fk_ir_result_same_scope','FOREIGN KEY (resulting_snapshot_id, project_id, decision_id) REFERENCES decision_input_snapshots(id, project_id, decision_id) ON DELETE RESTRICT'),
        ('input_revisions_pkey','PRIMARY KEY (id)'),
        ('uq_ir_id_scope','UNIQUE (id, project_id, decision_id)')
    ), actual(name, definition) AS (
        SELECT constraint_info.conname::text,
               pg_catalog.replace(
                   pg_catalog.pg_get_constraintdef(constraint_info.oid),
                   pg_catalog.current_schema() || '.', ''
               )::text
        FROM pg_catalog.pg_constraint constraint_info
        JOIN pg_catalog.pg_class relation ON relation.oid = constraint_info.conrelid
        JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = pg_catalog.current_schema()
          AND relation.relname = 'input_revisions'
          AND constraint_info.convalidated
    )
    SELECT pg_catalog.string_agg(drift.name || ':' || drift.side, ', ' ORDER BY drift.name)
      INTO problem
    FROM (
        SELECT name, 'missing_or_altered' AS side FROM (
            SELECT * FROM expected EXCEPT SELECT * FROM actual
        ) missing
        UNION ALL
        SELECT name, 'unexpected' FROM (
            SELECT * FROM actual EXCEPT SELECT * FROM expected
        ) unexpected
    ) drift;
    IF problem IS NOT NULL THEN
        RAISE EXCEPTION 'v65 preflight constraint drift: %', problem
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF function_count <> 1 OR trigger_count <> 1 THEN
        RAISE EXCEPTION 'v65 preflight lifecycle guard drift'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
    SELECT relation.relowner INTO projects_owner
    FROM pg_catalog.pg_class relation
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = pg_catalog.current_schema()
      AND relation.relname = 'projects';
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_trigger trigger_info
        JOIN pg_catalog.pg_class relation ON relation.oid = trigger_info.tgrelid
        JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
        JOIN pg_catalog.pg_proc function_info ON function_info.oid = trigger_info.tgfoid
        JOIN pg_catalog.pg_namespace function_namespace
          ON function_namespace.oid = function_info.pronamespace
        WHERE namespace.nspname = pg_catalog.current_schema()
          AND relation.relname = 'input_revisions'
          AND trigger_info.tgname = 'trg_ir_lifecycle_guard'
          AND trigger_info.tgenabled = 'O'
          AND trigger_info.tgtype = 27
          AND trigger_info.tgattr::text = ''
          AND trigger_info.tgqual IS NULL
          AND trigger_info.tgnargs = 0
          AND function_namespace.nspname = pg_catalog.current_schema()
          AND function_info.proname = 'input_revision_guard_mutation'
    ) THEN
        RAISE EXCEPTION 'v65 preflight trigger semantic drift'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_proc function_info
        JOIN pg_catalog.pg_namespace namespace ON namespace.oid = function_info.pronamespace
        JOIN pg_catalog.pg_language language_info ON language_info.oid = function_info.prolang
        WHERE namespace.nspname = pg_catalog.current_schema()
          AND function_info.proname = 'input_revision_guard_mutation'
          AND pg_catalog.oidvectortypes(function_info.proargtypes) = ''
          AND function_info.prorettype = 'trigger'::pg_catalog.regtype
          AND language_info.lanname = 'plpgsql'
          AND function_info.proconfig = ARRAY['search_path=' || pg_catalog.current_schema()]
          AND function_info.prosrc = $body$
DECLARE
    snapshot_predecessor UUID;
    snapshot_cause TEXT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF NOT EXISTS (SELECT 1 FROM projects WHERE id = OLD.project_id) THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'input revisions are immutable historical records';
    END IF;

    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.decision_id IS DISTINCT FROM OLD.decision_id
       OR NEW.expected_base_snapshot_id IS DISTINCT FROM OLD.expected_base_snapshot_id
       OR NEW.patch_json IS DISTINCT FROM OLD.patch_json
       OR NEW.patch_sha256 IS DISTINCT FROM OLD.patch_sha256
       OR NEW.affected_field_paths IS DISTINCT FROM OLD.affected_field_paths
       OR NEW.rationale IS DISTINCT FROM OLD.rationale
       OR NEW.source_kind IS DISTINCT FROM OLD.source_kind
       OR NEW.source_reference IS DISTINCT FROM OLD.source_reference
       OR NEW.proposed_by IS DISTINCT FROM OLD.proposed_by
       OR NEW.proposed_at IS DISTINCT FROM OLD.proposed_at THEN
        RAISE EXCEPTION 'input revision proposal identity and content are immutable';
    END IF;
    IF OLD.lifecycle_status <> 'proposed' OR NEW.lifecycle_status = 'proposed' THEN
        RAISE EXCEPTION 'terminal input revision lifecycle is immutable';
    END IF;

    IF NEW.lifecycle_status = 'applied' THEN
        SELECT predecessor_snapshot_id, change_cause_id
          INTO snapshot_predecessor, snapshot_cause
        FROM decision_input_snapshots
        WHERE id = NEW.resulting_snapshot_id
          AND project_id = NEW.project_id
          AND decision_id = NEW.decision_id;
        IF NOT FOUND
           OR snapshot_predecessor IS DISTINCT FROM OLD.expected_base_snapshot_id
           OR snapshot_cause IS DISTINCT FROM OLD.id::text THEN
            RAISE EXCEPTION 'applied revision requires exact resulting snapshot lineage';
        END IF;
    ELSIF NEW.lifecycle_status <> 'rejected' THEN
        RAISE EXCEPTION 'invalid input revision lifecycle transition';
    END IF;
    RETURN NEW;
END;
$body$
    ) THEN
        RAISE EXCEPTION 'v65 preflight function semantic drift'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_proc function_info
        JOIN pg_catalog.pg_namespace namespace ON namespace.oid = function_info.pronamespace
        WHERE namespace.nspname = pg_catalog.current_schema()
          AND function_info.proname = 'input_revision_guard_mutation'
          AND (
              function_info.proowner IS DISTINCT FROM projects_owner
              OR function_info.proacl IS NULL
              OR (
                  SELECT count(*) FROM pg_catalog.aclexplode(function_info.proacl)
              ) <> 1
              OR EXISTS (
                  SELECT 1 FROM pg_catalog.aclexplode(function_info.proacl) privilege
                  WHERE privilege.grantor <> function_info.proowner
                     OR privilege.grantee <> function_info.proowner
                     OR privilege.privilege_type <> 'EXECUTE'
                     OR privilege.is_grantable
              )
          )
    ) THEN
        RAISE EXCEPTION 'v65 preflight function owner/ACL drift'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
END
$preflight$;

CREATE TABLE IF NOT EXISTS input_revisions (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL,
    decision_id TEXT NOT NULL,
    expected_base_snapshot_id UUID NOT NULL,
    patch_json JSONB NOT NULL,
    patch_sha256 TEXT NOT NULL,
    affected_field_paths TEXT[] NOT NULL,
    rationale TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_reference TEXT,
    proposed_by TEXT NOT NULL,
    proposed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lifecycle_status TEXT NOT NULL DEFAULT 'proposed',
    applied_by TEXT,
    applied_at TIMESTAMPTZ,
    rejected_by TEXT,
    rejected_at TIMESTAMPTZ,
    resulting_snapshot_id UUID,
    rejection_rationale TEXT,
    CONSTRAINT fk_ir_project FOREIGN KEY (project_id)
        REFERENCES projects (id) ON DELETE CASCADE,
    CONSTRAINT ck_ir_sha256 CHECK (patch_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_ir_patch_object CHECK (
        jsonb_typeof(patch_json) = 'object' AND patch_json <> '{}'::jsonb
    ),
    CONSTRAINT ck_ir_patch_domain CHECK (
        patch_json - ARRAY[
            'project_name', 'brief', 'data', 'output_language', 'report_mode',
            'observations', 'timer_logs'
        ]::TEXT[] = '{}'::jsonb
    ),
    CONSTRAINT ck_ir_affected_paths CHECK (cardinality(affected_field_paths) > 0),
    CONSTRAINT ck_ir_status CHECK (
        lifecycle_status IN ('proposed', 'applied', 'rejected')
    ),
    CONSTRAINT ck_ir_lifecycle_shape CHECK (
        (lifecycle_status = 'proposed'
         AND applied_by IS NULL AND applied_at IS NULL
         AND rejected_by IS NULL AND rejected_at IS NULL
         AND resulting_snapshot_id IS NULL AND rejection_rationale IS NULL)
        OR
        (lifecycle_status = 'applied'
         AND applied_by IS NOT NULL AND applied_at IS NOT NULL
         AND rejected_by IS NULL AND rejected_at IS NULL
         AND resulting_snapshot_id IS NOT NULL AND rejection_rationale IS NULL)
        OR
        (lifecycle_status = 'rejected'
         AND applied_by IS NULL AND applied_at IS NULL
         AND rejected_by IS NOT NULL AND rejected_at IS NOT NULL
         AND resulting_snapshot_id IS NULL AND rejection_rationale IS NOT NULL)
    ),
    CONSTRAINT ck_ir_result_changes_base CHECK (
        resulting_snapshot_id IS NULL
        OR resulting_snapshot_id <> expected_base_snapshot_id
    ),
    CONSTRAINT uq_ir_id_scope UNIQUE (id, project_id, decision_id),
    CONSTRAINT fk_ir_expected_base_same_scope
        FOREIGN KEY (expected_base_snapshot_id, project_id, decision_id)
        REFERENCES decision_input_snapshots (id, project_id, decision_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_ir_result_same_scope
        FOREIGN KEY (resulting_snapshot_id, project_id, decision_id)
        REFERENCES decision_input_snapshots (id, project_id, decision_id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_ir_scope_status_created
    ON input_revisions (project_id, decision_id, lifecycle_status, proposed_at, id);
CREATE INDEX IF NOT EXISTS idx_ir_expected_base
    ON input_revisions (expected_base_snapshot_id);

CREATE OR REPLACE FUNCTION input_revision_guard_mutation()
RETURNS trigger
LANGUAGE plpgsql
SET search_path FROM CURRENT
AS $$
DECLARE
    snapshot_predecessor UUID;
    snapshot_cause TEXT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF NOT EXISTS (SELECT 1 FROM projects WHERE id = OLD.project_id) THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'input revisions are immutable historical records';
    END IF;

    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.decision_id IS DISTINCT FROM OLD.decision_id
       OR NEW.expected_base_snapshot_id IS DISTINCT FROM OLD.expected_base_snapshot_id
       OR NEW.patch_json IS DISTINCT FROM OLD.patch_json
       OR NEW.patch_sha256 IS DISTINCT FROM OLD.patch_sha256
       OR NEW.affected_field_paths IS DISTINCT FROM OLD.affected_field_paths
       OR NEW.rationale IS DISTINCT FROM OLD.rationale
       OR NEW.source_kind IS DISTINCT FROM OLD.source_kind
       OR NEW.source_reference IS DISTINCT FROM OLD.source_reference
       OR NEW.proposed_by IS DISTINCT FROM OLD.proposed_by
       OR NEW.proposed_at IS DISTINCT FROM OLD.proposed_at THEN
        RAISE EXCEPTION 'input revision proposal identity and content are immutable';
    END IF;
    IF OLD.lifecycle_status <> 'proposed' OR NEW.lifecycle_status = 'proposed' THEN
        RAISE EXCEPTION 'terminal input revision lifecycle is immutable';
    END IF;

    IF NEW.lifecycle_status = 'applied' THEN
        SELECT predecessor_snapshot_id, change_cause_id
          INTO snapshot_predecessor, snapshot_cause
        FROM decision_input_snapshots
        WHERE id = NEW.resulting_snapshot_id
          AND project_id = NEW.project_id
          AND decision_id = NEW.decision_id;
        IF NOT FOUND
           OR snapshot_predecessor IS DISTINCT FROM OLD.expected_base_snapshot_id
           OR snapshot_cause IS DISTINCT FROM OLD.id::text THEN
            RAISE EXCEPTION 'applied revision requires exact resulting snapshot lineage';
        END IF;
    ELSIF NEW.lifecycle_status <> 'rejected' THEN
        RAISE EXCEPTION 'invalid input revision lifecycle transition';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_ir_lifecycle_guard ON input_revisions;
CREATE TRIGGER trg_ir_lifecycle_guard
BEFORE UPDATE OR DELETE ON input_revisions
FOR EACH ROW EXECUTE FUNCTION input_revision_guard_mutation();

REVOKE ALL ON FUNCTION input_revision_guard_mutation() FROM PUBLIC;

-- Exact postflight reuses the preflight on every subsequent application. The
-- first application verifies the invariant-bearing relation, guard, and ACL.
DO $postflight$
DECLARE
    problem TEXT;
BEGIN
    IF to_regclass(pg_catalog.current_schema() || '.input_revisions') IS NULL THEN
        RAISE EXCEPTION 'v65 postflight relation missing';
    END IF;
    SELECT pg_catalog.string_agg(required.required_name, ', ' ORDER BY required.required_name)
      INTO problem
    FROM pg_catalog.unnest(ARRAY[
        'ck_ir_affected_paths', 'ck_ir_lifecycle_shape', 'ck_ir_patch_domain',
        'ck_ir_patch_object', 'ck_ir_result_changes_base', 'ck_ir_sha256',
        'ck_ir_status', 'fk_ir_expected_base_same_scope', 'fk_ir_project',
        'fk_ir_result_same_scope', 'input_revisions_pkey', 'uq_ir_id_scope'
    ]) AS required(required_name)
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_constraint constraint_info
        JOIN pg_catalog.pg_class relation ON relation.oid = constraint_info.conrelid
        JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = pg_catalog.current_schema()
          AND relation.relname = 'input_revisions'
          AND constraint_info.conname = required.required_name
          AND constraint_info.convalidated
    );
    IF problem IS NOT NULL THEN
        RAISE EXCEPTION 'v65 postflight constraint missing: %', problem;
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_trigger trigger_info
        JOIN pg_catalog.pg_class relation ON relation.oid = trigger_info.tgrelid
        JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = pg_catalog.current_schema()
          AND relation.relname = 'input_revisions'
          AND trigger_info.tgname = 'trg_ir_lifecycle_guard'
          AND trigger_info.tgenabled = 'O'
          AND NOT trigger_info.tgisinternal
    ) THEN
        RAISE EXCEPTION 'v65 postflight lifecycle trigger missing';
    END IF;
    IF pg_catalog.has_function_privilege(
        'public', 'input_revision_guard_mutation()', 'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'v65 postflight lifecycle guard is public';
    END IF;
END
$postflight$;

COMMIT;
