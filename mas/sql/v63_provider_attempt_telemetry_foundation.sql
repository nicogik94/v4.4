-- v63 Provider Attempt Telemetry Foundation (R2, remediated)
--
-- Durable, append-only, immutable telemetry for every provider attempt the
-- runtime gateway makes, at the granularity of the *actual HTTP request* — so a
-- paired evaluation can verify, long after the run's process is gone, which
-- provider answered, with which exact model, after how many transport retries,
-- and whether anything was lost.
--
-- ── Why v63 and not v62 ──────────────────────────────────────────────────────
-- v62 is deliberately skipped and remains permanently unused. Five ratified
-- Research Evidence static tests (R2.0A-1 .. R2.0A-4C) and two ratified design
-- documents assert, as evidence that those waves were bounded, that no
-- `v62_*.sql` exists. Taking v62 here would have required rewriting certified
-- assertions about someone else's wave. Taking the next free number leaves every
-- one of them literally true. Migrations here are applied by explicit name via
-- tools/provider_attempt_telemetry_migrate.py, never by scanning a numeric
-- range, so the gap is inert.
--
-- ── Security model ───────────────────────────────────────────────────────────
-- Three roles, none of which is the ordinary application role, and all of which
-- must pre-exist (this migration creates no role and sets no password):
--
--   workflow_provider_telemetry_owner    NOLOGIN. Owns every object here. The
--                                        only identity that can disable a
--                                        trigger, alter a table or truncate.
--   workflow_provider_telemetry_writer   LOGIN. INSERT + SELECT only. Cannot
--                                        UPDATE, DELETE, TRUNCATE, or hold
--                                        TRIGGER privilege, and does not own a
--                                        single object, so it cannot disable the
--                                        append-only guards that constrain it.
--   workflow_provider_telemetry_reader   LOGIN. SELECT only, for export.
--
-- A superuser (and any role with BYPASSRLS-class authority) can still defeat all
-- of this. That is an operational risk this migration cannot close and does not
-- claim to: the guarantee offered here is that the *runtime* cannot erase or
-- rewrite its own telemetry, not that the cluster's superuser cannot.
--
-- ── Reapplication ────────────────────────────────────────────────────────────
-- Reapplying against a database already carrying the complete, undrifted
-- foundation is a no-op. Reapplying against a partial, or a *semantically
-- divergent* one — a same-named table with different columns, a disabled
-- trigger, an altered constraint, a redefined index, a function whose
-- search_path was changed, a changed owner or a widened ACL — fails closed with
-- the exact divergence named. That is the whole point of the postflight below:
-- a one-column table named `provider_attempt` must never satisfy it.
BEGIN;

-- ═══════════════════════════════════════════════════════════════════════════
-- 0. Preflight — required roles, and an all-or-nothing object state
-- ═══════════════════════════════════════════════════════════════════════════

DO $preflight$
DECLARE
    v_missing_roles text;
    v_bad_roles     text;
    v_objects       integer;
BEGIN
    SELECT string_agg(name, ', ' ORDER BY name) INTO v_missing_roles
    FROM (VALUES
        ('workflow_provider_telemetry_owner'),
        ('workflow_provider_telemetry_writer'),
        ('workflow_provider_telemetry_reader')
    ) AS required(name)
    WHERE NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname = required.name
    );

    IF v_missing_roles IS NOT NULL THEN
        RAISE EXCEPTION
            'v63 preflight: required telemetry roles are absent: %. '
            'Provision them before applying this migration; it creates no role '
            'and sets no password.', v_missing_roles
            USING ERRCODE = 'invalid_authorization_specification';
    END IF;

    -- The owner must not be able to log in, and no telemetry role may hold
    -- cluster-level authority.
    SELECT string_agg(rolname, ', ' ORDER BY rolname) INTO v_bad_roles
    FROM pg_catalog.pg_roles
    WHERE rolname IN (
            'workflow_provider_telemetry_owner',
            'workflow_provider_telemetry_writer',
            'workflow_provider_telemetry_reader')
      AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolbypassrls OR rolreplication
           OR (rolname = 'workflow_provider_telemetry_owner' AND rolcanlogin));

    IF v_bad_roles IS NOT NULL THEN
        RAISE EXCEPTION
            'v63 preflight: telemetry role attributes are unsafe: %', v_bad_roles
            USING ERRCODE = 'invalid_authorization_specification';
    END IF;

    -- The applying session must be able to assume the owner role, otherwise the
    -- objects below would be created under the wrong identity.
    IF NOT pg_catalog.pg_has_role(
            current_user, 'workflow_provider_telemetry_owner', 'MEMBER') THEN
        RAISE EXCEPTION
            'v63 preflight: % is not a member of workflow_provider_telemetry_owner',
            current_user
            USING ERRCODE = 'invalid_authorization_specification';
    END IF;

    -- All-or-nothing: 7 tables + 3 functions + 14 triggers + 12 indexes = 36.
    SELECT
      (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
       WHERE n.nspname = current_schema() AND c.relkind = 'r'
         AND c.relname = ANY (ARRAY[
             'provider_telemetry_run', 'provider_telemetry_run_event',
             'provider_telemetry_call', 'provider_sdk_invocation',
             'provider_attempt', 'provider_attempt_event',
             'provider_telemetry_migration_ledger']))
      +
      (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
       WHERE n.nspname = current_schema()
         AND p.proname = ANY (ARRAY[
             'provider_telemetry_reject_mutation',
             'provider_telemetry_array_is_clean',
             'provider_telemetry_has_credential_shape']))
      +
      (SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
       JOIN pg_namespace n ON n.oid = c.relnamespace
       WHERE n.nspname = current_schema() AND NOT t.tgisinternal
         AND t.tgname LIKE 'trg_provider_%')
      +
      (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
       WHERE n.nspname = current_schema() AND c.relkind = 'i'
         AND c.relname LIKE 'idx_provider_%')
    INTO v_objects;

    IF v_objects NOT IN (0, 36) THEN
        RAISE EXCEPTION
            'v63 preflight: partial or divergent telemetry foundation (found % of 36 objects)',
            v_objects
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- ── Drift that reapplication would otherwise silently repair ──
    -- Ownership and function configuration are checked *here*, before any
    -- CREATE OR REPLACE runs. Checking them only in the postflight would mean
    -- `CREATE OR REPLACE FUNCTION` restores a tampered `SET search_path` and the
    -- migration then reports success — repairing evidence of tampering instead
    -- of refusing it.
    IF v_objects = 36 THEN
        SELECT string_agg(format('%s owned by %s', c.relname,
                                 pg_catalog.pg_get_userbyid(c.relowner)), ', ')
          INTO v_bad_roles
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = current_schema()
          AND c.relname = ANY (ARRAY[
              'provider_telemetry_run', 'provider_telemetry_run_event',
              'provider_telemetry_call', 'provider_sdk_invocation',
              'provider_attempt', 'provider_attempt_event',
              'provider_telemetry_migration_ledger'])
          AND pg_catalog.pg_get_userbyid(c.relowner)
              <> 'workflow_provider_telemetry_owner';
        IF v_bad_roles IS NOT NULL THEN
            RAISE EXCEPTION 'v63 preflight: ownership drift: %', v_bad_roles
                USING ERRCODE = 'invalid_schema_definition';
        END IF;

        SELECT string_agg(format('%s(owner=%s,secdef=%s,config=%s)', p.proname,
                                 pg_catalog.pg_get_userbyid(p.proowner),
                                 p.prosecdef,
                                 coalesce(array_to_string(p.proconfig, ','), '<none>')),
                          ', ')
          INTO v_bad_roles
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        JOIN pg_language l ON l.oid = p.prolang
        WHERE n.nspname = current_schema()
          AND p.proname = ANY (ARRAY[
              'provider_telemetry_reject_mutation',
              'provider_telemetry_array_is_clean',
              'provider_telemetry_has_credential_shape'])
          -- IS NOT DISTINCT FROM, not `=`: a function whose search_path was
          -- RESET has a NULL proconfig, and `NULL = ARRAY[...]` is NULL, which
          -- would make `NOT (...)` unknown and silently exclude the row.
          AND NOT (
              pg_catalog.pg_get_userbyid(p.proowner)
                  = 'workflow_provider_telemetry_owner'
              AND p.proconfig IS NOT DISTINCT FROM
                  ARRAY['search_path=pg_catalog']::text[]
              AND (
                  (p.proname = 'provider_telemetry_reject_mutation'
                   AND p.prosecdef AND l.lanname = 'plpgsql'
                   AND p.pronargs = 0 AND p.prorettype = 'trigger'::regtype
                   AND p.provolatile = 'v' AND NOT p.proisstrict
                   AND p.proparallel = 'u' AND NOT p.proretset)
                  OR
                  (p.proname = 'provider_telemetry_array_is_clean'
                   AND NOT p.prosecdef AND l.lanname = 'sql'
                   AND p.pronargs = 1 AND p.provolatile = 'i'
                   AND p.prorettype = 'boolean'::regtype
                   AND NOT p.proisstrict AND p.proparallel = 's'
                   AND NOT p.proretset
                   AND p.proargtypes[0] = 'text[]'::regtype)
                  OR
                  (p.proname = 'provider_telemetry_has_credential_shape'
                   AND NOT p.prosecdef AND l.lanname = 'sql'
                   AND p.pronargs = 1 AND p.provolatile = 'i'
                   AND p.prorettype = 'boolean'::regtype
                   AND NOT p.proisstrict AND p.proparallel = 's'
                   AND NOT p.proretset
                   AND p.proargtypes[0] = 'text'::regtype)));
        IF v_bad_roles IS NOT NULL THEN
            RAISE EXCEPTION
                'v63 preflight: telemetry function definition drift: %', v_bad_roles
                USING ERRCODE = 'invalid_schema_definition';
        END IF;

        -- ── The exact function body, pinned ──
        -- Everything checked above — name, language, argument and return types,
        -- volatility, strictness, parallel safety, SECURITY DEFINER, the fixed
        -- search_path, the owner — is satisfied by a guard whose body has been
        -- replaced with `RETURN COALESCE(NEW, OLD)`, and such a guard permits
        -- every UPDATE and DELETE it exists to refuse. `CREATE OR REPLACE
        -- FUNCTION` below would then install the correct body over the tampered
        -- one and the migration would report `reapplied_noop`: repairing the
        -- evidence of tampering and calling it a no-op. So the implementation
        -- itself is part of the catalog contract, compared against PostgreSQL's
        -- own stored representation (`pg_proc.prosrc`) before any CREATE OR
        -- REPLACE runs.
        SELECT string_agg(format('%s(body_drift,sha256=%s)', p.proname,
                                 encode(sha256(convert_to(p.prosrc, 'UTF8')), 'hex')),
                          ', ')
          INTO v_bad_roles
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = current_schema()
          AND p.proname = ANY (ARRAY[
              'provider_telemetry_reject_mutation',
              'provider_telemetry_array_is_clean',
              'provider_telemetry_has_credential_shape'])
          AND p.prosrc IS DISTINCT FROM (
              CASE p.proname
                  WHEN 'provider_telemetry_reject_mutation' THEN
$body_pre$
BEGIN
    RAISE EXCEPTION
        'provider telemetry is append-only; % on % is not permitted',
        TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END;
$body_pre$
                  WHEN 'provider_telemetry_array_is_clean' THEN
$body_pre$
    SELECT p_values IS NULL
        OR (array_position(p_values, NULL) IS NULL
            AND cardinality(p_values) = (
                SELECT count(DISTINCT item) FROM unnest(p_values) AS item));
$body_pre$
                  WHEN 'provider_telemetry_has_credential_shape' THEN
$body_pre$
    SELECT p_value IS NOT NULL AND (
        p_value ~* 'sk-ant-'
        OR p_value ~* '\ysk-[A-Za-z0-9_-]{8,}'
        OR p_value ~* '\y[rs]k_(live|test)_[A-Za-z0-9]{8,}'
        OR p_value ~* '\ygh[pousr]_[A-Za-z0-9]{8,}'
        OR p_value ~* '\yxox[baprs]-'
        OR p_value ~ '\y(AKIA|ASIA|AROA|AIDA)[A-Z0-9]{12,}'
        OR p_value ~ '\yAIza[A-Za-z0-9_-]{20,}'
        OR p_value ~* 'bearer[[:space:]_.:=-]*[A-Za-z0-9+/=_-]{4,}'
        OR p_value ~* 'basic[[:space:]_.:=-]*[A-Za-z0-9+/=_-]{8,}'
        OR p_value ~* 'authoriz(ation|ed?)[[:space:]_.:=-]'
        OR p_value ~* '(api[_.-]?key|access[_.-]?token|auth[_.-]?token|id[_.-]?token|refresh[_.-]?token|session[_.-]?id|session|secret|passwd|password|credential|cookie|private[_.-]?key)[[:space:]_.-]*[:=]'
        OR p_value ~* '\y(api[_.-]?key|access[_.-]?token|auth[_.-]?token|id[_.-]?token|refresh[_.-]?token|session[_.-]?id|secret|passwd|password|credential|cookie|private[_.-]?key)[[:space:]_.-]+[A-Za-z0-9+/=_-]{6,}'
        OR p_value ~* '[a-z][a-z0-9+.-]*://'
        OR p_value ~ '@'
        OR p_value ~* '%(20|3a|3d|2f|2b)'
        OR p_value ~ '\yeyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}'
    );
$body_pre$
              END);
        IF v_bad_roles IS NOT NULL THEN
            RAISE EXCEPTION
                'v63 preflight: telemetry function body drift: %', v_bad_roles
                USING ERRCODE = 'invalid_schema_definition';
        END IF;

        -- Argument names matter too: a CHECK constraint calls these helpers
        -- positionally, but a renamed parameter is still a rewritten function
        -- and is refused rather than silently replaced.
        SELECT string_agg(format('%s(argnames)', p.proname), ', ') INTO v_bad_roles
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = current_schema()
          AND p.proname = ANY (ARRAY[
              'provider_telemetry_array_is_clean',
              'provider_telemetry_has_credential_shape'])
          AND p.proargnames IS DISTINCT FROM (CASE p.proname
                  WHEN 'provider_telemetry_array_is_clean' THEN ARRAY['p_values']
                  WHEN 'provider_telemetry_has_credential_shape' THEN ARRAY['p_value']
              END)::text[];
        IF v_bad_roles IS NOT NULL THEN
            RAISE EXCEPTION
                'v63 preflight: telemetry helper argument names drifted: %', v_bad_roles
                USING ERRCODE = 'invalid_schema_definition';
        END IF;

        -- ── The guard function's ACL, pinned in PREflight ──
        -- `CREATE OR REPLACE FUNCTION` preserves an existing ACL, but the
        -- REVOKE below re-issues it unconditionally, so a widened guard ACL
        -- would likewise be repaired and the run reported as a no-op. Kept as
        -- its own named refusal, ahead of the general ACL pin below, because
        -- "PUBLIC can call the append-only guard directly" is a distinct and
        -- more serious statement than "an ACL differs from the contract".
        IF EXISTS (
            SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = current_schema()
              AND p.proname = 'provider_telemetry_reject_mutation'
              AND has_function_privilege('public', p.oid, 'EXECUTE')
        ) THEN
            RAISE EXCEPTION
                'v63 preflight: PUBLIC holds EXECUTE on the guard function'
                USING ERRCODE = 'invalid_schema_definition';
        END IF;

        -- ── Every telemetry function's EXECUTE ACL, pinned exactly, in PREflight ──
        --
        -- `CREATE OR REPLACE FUNCTION` preserves an existing ACL, but the
        -- REVOKE/GRANT block in section 5 re-issues all three unconditionally,
        -- so *any* drift in them would be repaired and the run reported as a
        -- no-op. Checking here, before that block runs, is what turns a silent
        -- repair into a refusal.
        --
        -- The contract is the complete privilege set, not a spot check:
        --   reject_mutation()          owner only. SECURITY DEFINER, invoked by
        --                              the trigger machinery, never by a caller.
        --   array_is_clean(text[])     owner + writer. Called from a CHECK
        --   has_credential_shape(text) constraint, which PostgreSQL evaluates
        --                              with the inserting role's privileges —
        --                              so the writer must hold EXECUTE directly
        --                              or every telemetry INSERT fails 42501.
        --
        -- `coalesce(proacl, acldefault(...))` matters: an untouched function
        -- stores NULL, and NULL means "PUBLIC has EXECUTE". Comparing the raw
        -- column would read the most permissive state in the system as "no ACL
        -- to check", which is exactly the false negative this pin closes.
        SELECT string_agg(format('%s=[%s]', d.proname, d.acl), ', ' ORDER BY d.proname)
          INTO v_bad_roles
        FROM (
            SELECT p.proname,
                   coalesce((
                       SELECT string_agg(entry.label, ',' ORDER BY entry.label)
                       FROM (
                           SELECT format('%s:%s',
                                         CASE WHEN a.grantee = 0 THEN 'PUBLIC'
                                              ELSE pg_catalog.pg_get_userbyid(a.grantee)
                                         END,
                                         a.privilege_type) AS label
                           FROM aclexplode(coalesce(
                                    p.proacl,
                                    pg_catalog.acldefault('f', p.proowner))) AS a
                       ) AS entry
                   ), '<none>') AS acl
            FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = current_schema()
              AND p.proname = ANY (ARRAY[
                  'provider_telemetry_reject_mutation',
                  'provider_telemetry_array_is_clean',
                  'provider_telemetry_has_credential_shape'])
        ) AS d
        WHERE d.acl IS DISTINCT FROM (CASE d.proname
                  WHEN 'provider_telemetry_reject_mutation' THEN
                      'workflow_provider_telemetry_owner:EXECUTE'
                  ELSE
                      'workflow_provider_telemetry_owner:EXECUTE,'
                      'workflow_provider_telemetry_writer:EXECUTE'
              END);
        IF v_bad_roles IS NOT NULL THEN
            RAISE EXCEPTION
                'v63 preflight: telemetry function EXECUTE ACL drift: %', v_bad_roles
                USING ERRCODE = 'invalid_schema_definition';
        END IF;
    END IF;

    -- ── No unexpected protected function may exist ──
    -- Checked whether or not the foundation is present: an extra
    -- `provider_telemetry_*` function in this schema is either a colliding
    -- object this migration would not manage or an attacker-supplied helper,
    -- and in both cases applying on top of it is refused rather than ignored.
    SELECT string_agg(format('%s/%s', p.proname, p.pronargs), ', ' ORDER BY p.proname)
      INTO v_bad_roles
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = current_schema()
      AND p.proname LIKE 'provider\_telemetry\_%'
      AND NOT (
          (p.proname = 'provider_telemetry_reject_mutation' AND p.pronargs = 0)
          OR (p.proname = 'provider_telemetry_array_is_clean' AND p.pronargs = 1)
          OR (p.proname = 'provider_telemetry_has_credential_shape'
              AND p.pronargs = 1));
    IF v_bad_roles IS NOT NULL THEN
        RAISE EXCEPTION
            'v63 preflight: unexpected protected telemetry function(s): %', v_bad_roles
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
END
$preflight$;

-- Every object below is created as the dedicated owner, so the applying
-- migration role never becomes the owner of a telemetry relation.
SET LOCAL ROLE workflow_provider_telemetry_owner;

-- ═══════════════════════════════════════════════════════════════════════════
-- 1. Append-only guard
-- ═══════════════════════════════════════════════════════════════════════════
-- Owned by this wave rather than reusing v47's slicea_reject_mutation(), so the
-- telemetry relations are restorable into a database that has no Slice A schema.
-- SECURITY DEFINER with a fixed search_path: a caller cannot shadow a catalog
-- object to make the guard resolve to something else.
CREATE OR REPLACE FUNCTION provider_telemetry_reject_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function_body$
BEGIN
    RAISE EXCEPTION
        'provider telemetry is append-only; % on % is not permitted',
        TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END;
$function_body$;

-- A CHECK constraint cannot contain a subquery, so the "no NULL and no duplicate
-- array member" rule is expressed as an IMMUTABLE helper. Same hardening as the
-- guard: fixed search_path, and no EXECUTE for PUBLIC.
--
-- The privilege model this helper needs is *not* the guard's, and the difference
-- is load-bearing. PostgreSQL evaluates a CHECK constraint's function call with
-- the privileges of the role performing the INSERT, so the writer needs EXECUTE
-- on this function directly or every telemetry INSERT fails with 42501. The
-- default ACL on a new function grants EXECUTE to PUBLIC, which is why that was
-- never noticed; it is revoked in section 5 and replaced with an explicit grant
-- to the writer alone, and the resulting ACL is pinned exactly in both the
-- preflight and the postflight.
CREATE OR REPLACE FUNCTION provider_telemetry_array_is_clean(p_values TEXT[])
RETURNS BOOLEAN
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
SET search_path = pg_catalog
AS $array_clean$
    SELECT p_values IS NULL
        OR (array_position(p_values, NULL) IS NULL
            AND cardinality(p_values) = (
                SELECT count(DISTINCT item) FROM unnest(p_values) AS item));
$array_clean$;

-- The credential grammar, restated where the application cannot reach it.
-- provider_telemetry/redaction.py refuses these shapes before a value is ever
-- built into a record, but a positive grammar alone is not enough at this
-- layer: `Bearer_abcdefghijkl` satisfies ck_pae_safe_grammars' identifier
-- pattern character for character, so a writer that bypassed Python — a restore
-- of a hand-edited artifact, a psql session, a future writer — could store a
-- credential in a column typed as an identifier and nothing here would object.
-- Same hardening as the other two helpers: IMMUTABLE, fixed search_path, no
-- EXECUTE for PUBLIC and an explicit EXECUTE grant to the writer, which the
-- CHECK constraints on provider_attempt_event require it to hold directly.
CREATE OR REPLACE FUNCTION provider_telemetry_has_credential_shape(p_value TEXT)
RETURNS BOOLEAN
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
SET search_path = pg_catalog
AS $credential_shape$
    SELECT p_value IS NOT NULL AND (
        p_value ~* 'sk-ant-'
        OR p_value ~* '\ysk-[A-Za-z0-9_-]{8,}'
        OR p_value ~* '\y[rs]k_(live|test)_[A-Za-z0-9]{8,}'
        OR p_value ~* '\ygh[pousr]_[A-Za-z0-9]{8,}'
        OR p_value ~* '\yxox[baprs]-'
        OR p_value ~ '\y(AKIA|ASIA|AROA|AIDA)[A-Z0-9]{12,}'
        OR p_value ~ '\yAIza[A-Za-z0-9_-]{20,}'
        OR p_value ~* 'bearer[[:space:]_.:=-]*[A-Za-z0-9+/=_-]{4,}'
        OR p_value ~* 'basic[[:space:]_.:=-]*[A-Za-z0-9+/=_-]{8,}'
        OR p_value ~* 'authoriz(ation|ed?)[[:space:]_.:=-]'
        OR p_value ~* '(api[_.-]?key|access[_.-]?token|auth[_.-]?token|id[_.-]?token|refresh[_.-]?token|session[_.-]?id|session|secret|passwd|password|credential|cookie|private[_.-]?key)[[:space:]_.-]*[:=]'
        OR p_value ~* '\y(api[_.-]?key|access[_.-]?token|auth[_.-]?token|id[_.-]?token|refresh[_.-]?token|session[_.-]?id|secret|passwd|password|credential|cookie|private[_.-]?key)[[:space:]_.-]+[A-Za-z0-9+/=_-]{6,}'
        OR p_value ~* '[a-z][a-z0-9+.-]*://'
        OR p_value ~ '@'
        OR p_value ~* '%(20|3a|3d|2f|2b)'
        OR p_value ~ '\yeyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}'
    );
$credential_shape$;

-- ═══════════════════════════════════════════════════════════════════════════
-- 2. Relations
-- ═══════════════════════════════════════════════════════════════════════════

-- ── The run envelope: what work this run expects to produce ──
-- Attempt rows alone cannot prove a call is missing: a call never made and a
-- call whose telemetry was lost look identical. This is the manifest that makes
-- a wholly absent call detectable.
CREATE TABLE IF NOT EXISTS provider_telemetry_run (
    run_sequence            BIGINT GENERATED ALWAYS AS IDENTITY,
    telemetry_run_id        UUID PRIMARY KEY,
    posture                 TEXT NOT NULL,
    telemetry_required      BOOLEAN NOT NULL,
    entry_point             TEXT NOT NULL,
    project_id              UUID,
    external_project_id     TEXT NOT NULL DEFAULT '',
    external_run_id         TEXT NOT NULL DEFAULT '',
    job_id                  TEXT NOT NULL DEFAULT '',
    source_commit           TEXT NOT NULL DEFAULT '',
    schema_version          INTEGER NOT NULL,
    runtime_fingerprint     TEXT NOT NULL,
    expected_phases         TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    started_at              TIMESTAMPTZ NOT NULL,
    recorded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_provider_telemetry_run_sequence UNIQUE (run_sequence),
    CONSTRAINT ck_ptr_posture CHECK (posture IN ('observational', 'strict')),
    CONSTRAINT ck_ptr_strict_requires_telemetry
        CHECK (posture <> 'strict' OR telemetry_required),
    CONSTRAINT ck_ptr_schema_version CHECK (schema_version >= 1),
    CONSTRAINT ck_ptr_entry_point CHECK (entry_point ~ '^[a-z][a-z0-9_]{0,63}$'),
    CONSTRAINT ck_ptr_runtime_fingerprint CHECK (runtime_fingerprint ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_ptr_source_commit CHECK (source_commit ~ '^[0-9a-zA-Z._-]{0,64}$'),
    -- An array member may be neither NULL nor duplicated: either would make the
    -- expected-phase set uninterpretable.
    CONSTRAINT ck_ptr_expected_phases_sane
        CHECK (provider_telemetry_array_is_clean(expected_phases))
);

-- ── Run lifecycle: worker registration, drain results, reconciliation ──
CREATE TABLE IF NOT EXISTS provider_telemetry_run_event (
    run_event_sequence      BIGINT GENERATED ALWAYS AS IDENTITY,
    event_id                UUID PRIMARY KEY,
    telemetry_run_id        UUID NOT NULL,
    event_kind              TEXT NOT NULL,
    worker_id               TEXT NOT NULL DEFAULT '',
    posture                 TEXT NOT NULL,
    observed_at             TIMESTAMPTZ NOT NULL,
    started_events          BIGINT NOT NULL DEFAULT 0,
    terminal_events         BIGINT NOT NULL DEFAULT 0,
    unmatched_starts        BIGINT NOT NULL DEFAULT 0,
    undurable_events        BIGINT NOT NULL DEFAULT 0,
    ambiguous_events        BIGINT NOT NULL DEFAULT 0,
    dropped_events          BIGINT NOT NULL DEFAULT 0,
    expected_calls          BIGINT NOT NULL DEFAULT 0,
    observed_calls          BIGINT NOT NULL DEFAULT 0,
    drain_status            TEXT NOT NULL DEFAULT 'unknown',
    reconciliation_status   TEXT NOT NULL DEFAULT 'pending',
    -- The digest of the expected-work manifest this reconciliation is about.
    -- A run that declares four phases and produces nothing cannot reconcile
    -- `complete` without naming the manifest it is claiming to have completed.
    expected_work_digest    TEXT NOT NULL DEFAULT '',
    detail                  TEXT NOT NULL DEFAULT '',
    recorded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_ptre_sequence UNIQUE (run_event_sequence),
    CONSTRAINT ck_ptre_kind CHECK (
        event_kind IN ('worker_registered', 'worker_drained', 'reconciliation')),
    CONSTRAINT ck_ptre_posture CHECK (posture IN ('observational', 'strict')),
    CONSTRAINT ck_ptre_drain CHECK (
        drain_status IN ('unknown', 'drained', 'failed', 'timeout')),
    CONSTRAINT ck_ptre_reconciliation CHECK (
        reconciliation_status IN ('pending', 'complete', 'incomplete', 'uncertified')),
    CONSTRAINT ck_ptre_counts CHECK (
        started_events >= 0 AND terminal_events >= 0 AND unmatched_starts >= 0
        AND undurable_events >= 0 AND ambiguous_events >= 0 AND dropped_events >= 0
        AND expected_calls >= 0 AND observed_calls >= 0),
    -- A run cannot be reported complete while it still knows something is wrong.
    -- A completeness claim must name the manifest it is about, and the digest
    -- must be a digest. Enforced here rather than only in Python so a writer
    -- that bypassed the dataclasses cannot store an unqualified `complete`.
    CONSTRAINT ck_ptre_digest_shape CHECK (
        expected_work_digest = '' OR expected_work_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_ptre_complete_binds_manifest CHECK (
        reconciliation_status <> 'complete'
        OR expected_work_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_ptre_complete_is_clean CHECK (
        reconciliation_status <> 'complete'
        OR (unmatched_starts = 0 AND undurable_events = 0 AND ambiguous_events = 0
            AND dropped_events = 0 AND drain_status IN ('unknown', 'drained')))
);

-- ── One logical model call ──
CREATE TABLE IF NOT EXISTS provider_telemetry_call (
    call_sequence               BIGINT GENERATED ALWAYS AS IDENTITY,
    call_id                     UUID PRIMARY KEY,
    telemetry_run_id            UUID NOT NULL,
    posture                     TEXT NOT NULL,
    entry_point                 TEXT NOT NULL,
    project_id                  UUID,
    external_project_id         TEXT NOT NULL DEFAULT '',
    external_run_id             TEXT NOT NULL DEFAULT '',
    job_id                      TEXT NOT NULL DEFAULT '',
    phase                       TEXT NOT NULL DEFAULT '',
    worker_id                   TEXT NOT NULL DEFAULT '',
    requested_provider          TEXT NOT NULL,
    requested_model             TEXT NOT NULL,
    request_config_fingerprint  TEXT NOT NULL,
    routing_decision_fingerprint TEXT NOT NULL,
    candidate_count             INTEGER NOT NULL,
    started_at                  TIMESTAMPTZ NOT NULL,
    recorded_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_ptc_sequence UNIQUE (call_sequence),
    CONSTRAINT ck_ptc_posture CHECK (posture IN ('observational', 'strict')),
    CONSTRAINT ck_ptc_entry_point CHECK (entry_point ~ '^[a-z][a-z0-9_]{0,63}$'),
    CONSTRAINT ck_ptc_identity CHECK (requested_provider <> '' AND requested_model <> ''),
    CONSTRAINT ck_ptc_candidate_count CHECK (candidate_count >= 1),
    CONSTRAINT ck_ptc_config_fingerprint
        CHECK (request_config_fingerprint ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_ptc_routing_fingerprint
        CHECK (routing_decision_fingerprint ~ '^[0-9a-f]{64}$')
);

-- ── One gateway attempt: a frozen candidate at a frozen retry ordinal ──
-- A skipped candidate lives here too: it never reached the network, so it has no
-- HTTP attempt at all, and the constraints below enforce that it carries nothing
-- a provider could have supplied.
CREATE TABLE IF NOT EXISTS provider_sdk_invocation (
    invocation_sequence         BIGINT GENERATED ALWAYS AS IDENTITY,
    invocation_id               UUID PRIMARY KEY,
    call_id                     UUID NOT NULL,
    telemetry_run_id            UUID NOT NULL,
    posture                     TEXT NOT NULL,
    entry_point                 TEXT NOT NULL,
    project_id                  UUID,
    external_project_id         TEXT NOT NULL DEFAULT '',
    external_run_id             TEXT NOT NULL DEFAULT '',
    job_id                      TEXT NOT NULL DEFAULT '',
    phase                       TEXT NOT NULL DEFAULT '',
    worker_id                   TEXT NOT NULL DEFAULT '',
    invocation_kind             TEXT NOT NULL,
    provider                    TEXT NOT NULL,
    requested_model             TEXT NOT NULL,
    candidate_ordinal           INTEGER NOT NULL,
    retry_ordinal               INTEGER NOT NULL,
    attempt_ordinal             INTEGER NOT NULL,
    breaker_state_before        TEXT NOT NULL,
    breaker_failure_count_before INTEGER,
    breaker_snapshot_status_before TEXT NOT NULL,
    fallback_candidate          BOOLEAN NOT NULL DEFAULT FALSE,
    fallback_from_provider      TEXT NOT NULL DEFAULT '',
    fallback_from_model         TEXT NOT NULL DEFAULT '',
    request_config_fingerprint  TEXT NOT NULL,
    routing_decision_fingerprint TEXT NOT NULL,
    started_at                  TIMESTAMPTZ NOT NULL,
    recorded_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_psi_sequence UNIQUE (invocation_sequence),
    CONSTRAINT uq_psi_call_attempt UNIQUE (call_id, attempt_ordinal),
    CONSTRAINT ck_psi_posture CHECK (posture IN ('observational', 'strict')),
    CONSTRAINT ck_psi_entry_point CHECK (entry_point ~ '^[a-z][a-z0-9_]{0,63}$'),
    CONSTRAINT ck_psi_kind CHECK (invocation_kind IN ('provider_call', 'skipped_candidate')),
    CONSTRAINT ck_psi_identity CHECK (provider <> '' AND requested_model <> ''),
    -- Ordinals are 1-based. A zero ordinal in the previous design made "the
    -- first attempt" and "no attempt" indistinguishable.
    CONSTRAINT ck_psi_ordinals CHECK (
        candidate_ordinal >= 1 AND retry_ordinal >= 1 AND attempt_ordinal >= 1),
    CONSTRAINT ck_psi_breaker_state CHECK (
        breaker_state_before IN ('closed', 'open', 'unknown')),
    CONSTRAINT ck_psi_breaker_status CHECK (
        breaker_snapshot_status_before IN ('valid', 'unknown')),
    -- A breaker snapshot is atomic: either a real state with a real count, or
    -- honestly unknown with neither. "closed with zero failures" is never
    -- storable as a stand-in for a reading that failed.
    CONSTRAINT ck_psi_breaker_atomic CHECK (
        (breaker_snapshot_status_before = 'valid'
         AND breaker_state_before IN ('closed', 'open')
         AND breaker_failure_count_before IS NOT NULL
         AND breaker_failure_count_before >= 0)
        OR
        (breaker_snapshot_status_before = 'unknown'
         AND breaker_state_before = 'unknown'
         AND breaker_failure_count_before IS NULL)),
    CONSTRAINT ck_psi_fallback CHECK (
        (NOT fallback_candidate AND fallback_from_provider = '' AND fallback_from_model = '')
        OR (fallback_candidate AND fallback_from_provider <> '')),
    CONSTRAINT ck_psi_config_fingerprint
        CHECK (request_config_fingerprint ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_psi_routing_fingerprint
        CHECK (routing_decision_fingerprint ~ '^[0-9a-f]{64}$')
);

-- ── One ACTUAL provider HTTP request — the fail-closed start ──
-- Written synchronously immediately before the bytes leave. One row per HTTP
-- request, so an SDK invocation that retried internally three times produces
-- three rows with three identities and three ordinals.
CREATE TABLE IF NOT EXISTS provider_attempt (
    attempt_sequence            BIGINT GENERATED ALWAYS AS IDENTITY,
    attempt_id                  UUID PRIMARY KEY,
    invocation_id               UUID NOT NULL,
    call_id                     UUID NOT NULL,
    telemetry_run_id            UUID NOT NULL,
    posture                     TEXT NOT NULL,
    worker_id                   TEXT NOT NULL DEFAULT '',
    provider                    TEXT NOT NULL,
    requested_model             TEXT NOT NULL,
    http_retry_ordinal          INTEGER NOT NULL,
    request_method              TEXT NOT NULL DEFAULT 'POST',
    request_path                TEXT NOT NULL DEFAULT '',
    request_started_at          TIMESTAMPTZ NOT NULL,
    recorded_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_pa_sequence UNIQUE (attempt_sequence),
    CONSTRAINT uq_pa_invocation_ordinal UNIQUE (invocation_id, http_retry_ordinal),
    CONSTRAINT ck_pa_posture CHECK (posture IN ('observational', 'strict')),
    CONSTRAINT ck_pa_identity CHECK (provider <> '' AND requested_model <> ''),
    CONSTRAINT ck_pa_ordinal CHECK (http_retry_ordinal >= 1),
    CONSTRAINT ck_pa_method CHECK (request_method ~ '^[A-Z]{3,10}$'),
    -- The path only. A query string is where a proxy or a misconfigured base URL
    -- would carry a token, so the grammar cannot express one.
    CONSTRAINT ck_pa_path CHECK (request_path ~ '^[A-Za-z0-9/._-]{0,128}$')
);

-- ── Everything that happens after a start ──
-- Append-only by construction: an event never replaces a start or an earlier
-- observation, so rich metadata captured before a later transformation failure
-- survives it.
CREATE TABLE IF NOT EXISTS provider_attempt_event (
    event_sequence              BIGINT GENERATED ALWAYS AS IDENTITY,
    event_id                    UUID PRIMARY KEY,
    subject_kind                TEXT NOT NULL,
    subject_id                  UUID NOT NULL,
    call_id                     UUID NOT NULL,
    telemetry_run_id            UUID NOT NULL,
    event_kind                  TEXT NOT NULL,
    event_ordinal               INTEGER NOT NULL,
    is_terminal                 BOOLEAN NOT NULL,
    observed_at                 TIMESTAMPTZ NOT NULL,
    worker_id                   TEXT NOT NULL DEFAULT '',

    transport_outcome           TEXT NOT NULL DEFAULT '',
    http_status                 INTEGER,
    http_status_status          TEXT NOT NULL DEFAULT 'absent',
    provider_request_id         TEXT,
    provider_request_id_status  TEXT NOT NULL DEFAULT 'absent',
    retry_after                 TEXT,
    retry_after_status          TEXT NOT NULL DEFAULT 'absent',

    -- Provider-reported metadata. Every value column is paired with a status
    -- column, so "absent", "explicitly null", "valid", "invalid", "redacted",
    -- "unsupported" and "unknown value" stay seven different facts instead of
    -- collapsing into one NULL.
    provider_response_id        TEXT,
    provider_response_id_status TEXT NOT NULL DEFAULT 'absent',
    effective_model             TEXT,
    effective_model_status      TEXT NOT NULL DEFAULT 'absent',
    stop_reason                 TEXT,
    stop_reason_status          TEXT NOT NULL DEFAULT 'absent',
    input_tokens                BIGINT,
    input_tokens_status         TEXT NOT NULL DEFAULT 'absent',
    output_tokens               BIGINT,
    output_tokens_status        TEXT NOT NULL DEFAULT 'absent',
    cache_read_tokens           BIGINT,
    cache_read_tokens_status    TEXT NOT NULL DEFAULT 'absent',
    cache_creation_tokens       BIGINT,
    cache_creation_tokens_status TEXT NOT NULL DEFAULT 'absent',

    breaker_state_after         TEXT NOT NULL DEFAULT 'unknown',
    breaker_failure_count_after INTEGER,
    breaker_snapshot_status_after TEXT NOT NULL DEFAULT 'unknown',

    error_category              TEXT NOT NULL DEFAULT '',
    error_identity              TEXT NOT NULL DEFAULT '',
    failure_class               TEXT NOT NULL DEFAULT '',
    value_details               TEXT NOT NULL DEFAULT '',
    response_metadata_fingerprint TEXT NOT NULL,
    schema_version              INTEGER NOT NULL,
    recorded_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_pae_sequence UNIQUE (event_sequence),
    CONSTRAINT uq_pae_subject_ordinal UNIQUE (subject_id, event_ordinal),
    CONSTRAINT ck_pae_subject_kind CHECK (subject_kind IN ('sdk_invocation', 'http_attempt')),
    CONSTRAINT ck_pae_event_kind CHECK (event_kind IN (
        'completed', 'provider_failure', 'cancelled', 'unknown', 'skipped',
        'observation', 'transformation_failure', 'capture_failure')),
    CONSTRAINT ck_pae_ordinal CHECK (event_ordinal >= 1),
    -- The terminal flag and the event kind cannot disagree: reconciliation reads
    -- the flag, and a mismatch would let a non-terminal event satisfy a start.
    CONSTRAINT ck_pae_terminal_agreement CHECK (
        is_terminal = (event_kind IN (
            'completed', 'provider_failure', 'cancelled', 'unknown', 'skipped'))),
    CONSTRAINT ck_pae_transport_outcome CHECK (
        transport_outcome IN ('', 'response', 'transport_error', 'cancelled', 'unknown')),
    CONSTRAINT ck_pae_schema_version CHECK (schema_version >= 1),
    CONSTRAINT ck_pae_metadata_fingerprint
        CHECK (response_metadata_fingerprint ~ '^[0-9a-f]{64}$'),

    -- Every status column draws on the same closed vocabulary.
    CONSTRAINT ck_pae_value_statuses CHECK (
        http_status_status = ANY (ARRAY['absent','null','valid','invalid','redacted','unsupported','unknown_value'])
        AND provider_request_id_status = ANY (ARRAY['absent','null','valid','invalid','redacted','unsupported','unknown_value'])
        AND retry_after_status = ANY (ARRAY['absent','null','valid','invalid','redacted','unsupported','unknown_value'])
        AND provider_response_id_status = ANY (ARRAY['absent','null','valid','invalid','redacted','unsupported','unknown_value'])
        AND effective_model_status = ANY (ARRAY['absent','null','valid','invalid','redacted','unsupported','unknown_value'])
        AND stop_reason_status = ANY (ARRAY['absent','null','valid','invalid','redacted','unsupported','unknown_value'])
        AND input_tokens_status = ANY (ARRAY['absent','null','valid','invalid','redacted','unsupported','unknown_value'])
        AND output_tokens_status = ANY (ARRAY['absent','null','valid','invalid','redacted','unsupported','unknown_value'])
        AND cache_read_tokens_status = ANY (ARRAY['absent','null','valid','invalid','redacted','unsupported','unknown_value'])
        AND cache_creation_tokens_status = ANY (ARRAY['absent','null','valid','invalid','redacted','unsupported','unknown_value'])),

    -- Exact absence semantics, in both directions: a value may exist only when
    -- its status says `valid`, and a `valid` status must carry a value. The
    -- database enforces this independently of the application, so a writer that
    -- bypassed the Python model still cannot store an uninterpretable row.
    CONSTRAINT ck_pae_value_status_agreement CHECK (
        (http_status IS NOT NULL) = (http_status_status = 'valid')
        AND (provider_request_id IS NOT NULL) = (provider_request_id_status = 'valid')
        AND (retry_after IS NOT NULL) = (retry_after_status = 'valid')
        AND (provider_response_id IS NOT NULL) = (provider_response_id_status = 'valid')
        AND (effective_model IS NOT NULL) = (effective_model_status = 'valid')
        AND (stop_reason IS NOT NULL) = (stop_reason_status = 'valid')
        AND (input_tokens IS NOT NULL) = (input_tokens_status = 'valid')
        AND (output_tokens IS NOT NULL) = (output_tokens_status = 'valid')
        AND (cache_read_tokens IS NOT NULL) = (cache_read_tokens_status = 'valid')
        AND (cache_creation_tokens IS NOT NULL) = (cache_creation_tokens_status = 'valid')),

    -- Usage counters are exact nonnegative integers within a plausible bound.
    -- A float, a boolean or a numeric string never reaches here: it is refused
    -- in Python as `invalid` and stored as NULL with that status.
    CONSTRAINT ck_pae_usage_bounds CHECK (
        (input_tokens IS NULL OR (input_tokens >= 0 AND input_tokens <= 2147483647))
        AND (output_tokens IS NULL OR (output_tokens >= 0 AND output_tokens <= 2147483647))
        AND (cache_read_tokens IS NULL OR (cache_read_tokens >= 0 AND cache_read_tokens <= 2147483647))
        AND (cache_creation_tokens IS NULL OR (cache_creation_tokens >= 0 AND cache_creation_tokens <= 2147483647))
        AND (http_status IS NULL OR (http_status >= 100 AND http_status <= 599))),

    -- Safe metadata grammars, enforced by the database rather than trusted from
    -- the application. These mirror provider_telemetry/redaction.py exactly.
    CONSTRAINT ck_pae_safe_grammars CHECK (
        (provider_response_id IS NULL OR provider_response_id ~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$')
        AND (provider_request_id IS NULL OR provider_request_id ~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$')
        AND (effective_model IS NULL OR effective_model ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')
        AND (stop_reason IS NULL OR stop_reason ~ '^[A-Za-z][A-Za-z0-9_-]{0,63}$')
        AND (retry_after IS NULL OR retry_after ~ '^[0-9]{1,10}(\.[0-9]{1,6})?$')
        AND error_category ~ '^[a-z0-9_]{0,64}$'
        AND failure_class ~ '^[A-Za-z0-9_.-]{0,64}$'
        -- PostgreSQL's regex engine caps a bound at 255 repetitions, so the two
        -- longer fields state their grammar and their length separately rather
        -- than as one `{0,n}` the engine would reject outright.
        AND error_identity ~ '^[A-Za-z0-9_.= -]*$' AND length(error_identity) <= 256
        AND value_details ~ '^[A-Za-z0-9_;=-]*$' AND length(value_details) <= 512),

    -- The identifier grammars above are *positive* and therefore not enough on
    -- their own: `Bearer_abcdefghijkl` matches the response-id pattern
    -- character for character. Credential shapes are refused separately, so a
    -- writer that never went through provider_telemetry/redaction.py still
    -- cannot store a credential in a column typed as an identifier.
    CONSTRAINT ck_pae_no_credential_shape CHECK (
        NOT provider_telemetry_has_credential_shape(provider_response_id)
        AND NOT provider_telemetry_has_credential_shape(provider_request_id)
        AND NOT provider_telemetry_has_credential_shape(effective_model)
        AND NOT provider_telemetry_has_credential_shape(stop_reason)
        AND NOT provider_telemetry_has_credential_shape(retry_after)),

    CONSTRAINT ck_pae_breaker_state CHECK (
        breaker_state_after IN ('closed', 'open', 'unknown')),
    CONSTRAINT ck_pae_breaker_status CHECK (
        breaker_snapshot_status_after IN ('valid', 'unknown')),
    CONSTRAINT ck_pae_breaker_atomic CHECK (
        (breaker_snapshot_status_after = 'valid'
         AND breaker_state_after IN ('closed', 'open')
         AND breaker_failure_count_after IS NOT NULL
         AND breaker_failure_count_after >= 0)
        OR
        (breaker_snapshot_status_after = 'unknown'
         AND breaker_state_after = 'unknown'
         AND breaker_failure_count_after IS NULL)),

    -- A skipped candidate never reached a provider, so it can carry no
    -- provider-supplied value at all.
    CONSTRAINT ck_pae_skipped_is_empty CHECK (
        event_kind <> 'skipped'
        OR (provider_response_id IS NULL AND stop_reason IS NULL
            AND effective_model IS NULL AND input_tokens IS NULL
            AND output_tokens IS NULL AND cache_read_tokens IS NULL
            AND cache_creation_tokens IS NULL AND http_status IS NULL
            AND transport_outcome = '')),

    -- Only an HTTP-attempt event can describe transport.
    CONSTRAINT ck_pae_transport_is_http CHECK (
        subject_kind = 'http_attempt' OR (transport_outcome = '' AND http_status IS NULL))
);

-- ── Migration ledger ──
-- A migration history that can be rewritten proves nothing, so this relation
-- carries the same append-only guards as the telemetry it describes.
CREATE TABLE IF NOT EXISTS provider_telemetry_migration_ledger (
    ledger_sequence         BIGINT GENERATED ALWAYS AS IDENTITY,
    ledger_id               UUID PRIMARY KEY,
    migration_name          TEXT NOT NULL,
    migration_sha256        TEXT NOT NULL,
    schema_version          INTEGER NOT NULL,
    applied_at              TIMESTAMPTZ NOT NULL,
    applied_by              TEXT NOT NULL,
    outcome                 TEXT NOT NULL,

    CONSTRAINT uq_ptml_sequence UNIQUE (ledger_sequence),
    CONSTRAINT ck_ptml_name CHECK (migration_name ~ '^v[0-9]+_[a-z0-9_]+\.sql$'),
    CONSTRAINT ck_ptml_sha CHECK (migration_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_ptml_schema_version CHECK (schema_version >= 1),
    CONSTRAINT ck_ptml_outcome CHECK (outcome IN ('applied', 'reapplied_noop', 'verified')),
    CONSTRAINT ck_ptml_applied_by CHECK (applied_by ~ '^[A-Za-z0-9_.-]{1,64}$')
);

-- ═══════════════════════════════════════════════════════════════════════════
-- 3. Indexes
-- ═══════════════════════════════════════════════════════════════════════════

CREATE INDEX IF NOT EXISTS idx_provider_telemetry_run_started
    ON provider_telemetry_run (external_run_id, started_at, run_sequence);
CREATE INDEX IF NOT EXISTS idx_provider_telemetry_run_project
    ON provider_telemetry_run (project_id, started_at, run_sequence);
CREATE INDEX IF NOT EXISTS idx_provider_telemetry_run_event_run
    ON provider_telemetry_run_event (telemetry_run_id, observed_at, run_event_sequence);
CREATE INDEX IF NOT EXISTS idx_provider_telemetry_call_run
    ON provider_telemetry_call (telemetry_run_id, started_at, call_sequence);
CREATE INDEX IF NOT EXISTS idx_provider_telemetry_call_project
    ON provider_telemetry_call (project_id, started_at, call_sequence);
CREATE INDEX IF NOT EXISTS idx_provider_sdk_invocation_call
    ON provider_sdk_invocation (call_id, attempt_ordinal);
CREATE INDEX IF NOT EXISTS idx_provider_sdk_invocation_run
    ON provider_sdk_invocation (telemetry_run_id, started_at, invocation_sequence);
CREATE INDEX IF NOT EXISTS idx_provider_attempt_invocation
    ON provider_attempt (invocation_id, http_retry_ordinal);
CREATE INDEX IF NOT EXISTS idx_provider_attempt_run
    ON provider_attempt (telemetry_run_id, request_started_at, attempt_sequence);
CREATE INDEX IF NOT EXISTS idx_provider_attempt_event_subject
    ON provider_attempt_event (subject_id, event_ordinal);
-- Reconciliation reads exactly this: terminal events for one run.
CREATE INDEX IF NOT EXISTS idx_provider_attempt_event_terminal
    ON provider_attempt_event (telemetry_run_id, subject_kind, subject_id)
    WHERE is_terminal;

-- ── Exactly one terminal event per lifecycle subject ──
-- The Python capture buffer already refuses to append a second terminal, but a
-- process-local flag is not a guarantee: two workers, a redelivered event, a
-- restored artifact, or any writer that bypassed the dataclasses entirely can
-- still produce two terminal rows for one subject — and reconciliation reads
-- `is_terminal` to decide whether a start was matched, so two of them make the
-- chain ambiguous exactly where completeness is being claimed. A partial unique
-- index makes the second one impossible rather than merely unlikely:
-- PostgreSQL refuses the duplicate, and two concurrent transactions racing to
-- insert the same subject's terminal cannot both commit — the second blocks on
-- the first and is rejected when the first commits.
--
-- The predicate is deliberately `WHERE is_terminal` alone and the key is
-- `subject_id` alone: a subject_id is a UUID minted per invocation and per HTTP
-- attempt, so it is already unique across subject kinds, and including
-- subject_kind in the key would allow one terminal *per kind*.
-- ck_pae_terminal_agreement keeps `is_terminal` and `event_kind` in agreement,
-- so this also means one terminal *kind* per subject. Non-terminal
-- observations are outside the predicate and stay freely appendable, including
-- after the terminal event.
CREATE UNIQUE INDEX IF NOT EXISTS idx_provider_attempt_event_one_terminal
    ON provider_attempt_event (subject_id)
    WHERE is_terminal;

-- ═══════════════════════════════════════════════════════════════════════════
-- 4. Append-only enforcement
-- ═══════════════════════════════════════════════════════════════════════════
-- Two triggers per relation. The TRUNCATE trigger is not redundant: a
-- BEFORE UPDATE OR DELETE row trigger does not fire for TRUNCATE, so without it
-- the whole log could be erased without tripping the guard.
DO $triggers$
DECLARE
    v_table text;
BEGIN
    FOREACH v_table IN ARRAY ARRAY[
        'provider_telemetry_run', 'provider_telemetry_run_event',
        'provider_telemetry_call', 'provider_sdk_invocation',
        'provider_attempt', 'provider_attempt_event',
        'provider_telemetry_migration_ledger'
    ] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = current_schema() AND NOT t.tgisinternal
              AND c.relname = v_table
              AND t.tgname = 'trg_' || v_table || '_no_mutation'
        ) THEN
            EXECUTE format(
                'CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I '
                'FOR EACH ROW EXECUTE FUNCTION provider_telemetry_reject_mutation()',
                'trg_' || v_table || '_no_mutation', v_table);
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = current_schema() AND NOT t.tgisinternal
              AND c.relname = v_table
              AND t.tgname = 'trg_' || v_table || '_no_truncate'
        ) THEN
            EXECUTE format(
                'CREATE TRIGGER %I BEFORE TRUNCATE ON %I '
                'FOR EACH STATEMENT EXECUTE FUNCTION provider_telemetry_reject_mutation()',
                'trg_' || v_table || '_no_truncate', v_table);
        END IF;
    END LOOP;
END
$triggers$;

-- ═══════════════════════════════════════════════════════════════════════════
-- 5. Privileges
-- ═══════════════════════════════════════════════════════════════════════════
DO $grants$
DECLARE
    v_table text;
BEGIN
    FOREACH v_table IN ARRAY ARRAY[
        'provider_telemetry_run', 'provider_telemetry_run_event',
        'provider_telemetry_call', 'provider_sdk_invocation',
        'provider_attempt', 'provider_attempt_event',
        'provider_telemetry_migration_ledger'
    ] LOOP
        EXECUTE format('REVOKE ALL ON TABLE %I FROM PUBLIC', v_table);
        -- The writer gets exactly INSERT and SELECT. No UPDATE, no DELETE, no
        -- TRUNCATE, no TRIGGER, no REFERENCES: the runtime cannot rewrite,
        -- erase, or disarm its own telemetry.
        EXECUTE format(
            'GRANT INSERT, SELECT ON TABLE %I TO workflow_provider_telemetry_writer',
            v_table);
        EXECUTE format(
            'GRANT SELECT ON TABLE %I TO workflow_provider_telemetry_reader', v_table);
    END LOOP;
END
$grants$;

-- The guard function runs SECURITY DEFINER; PUBLIC has no business executing it
-- directly, and only the trigger machinery needs to.
REVOKE ALL ON FUNCTION provider_telemetry_reject_mutation() FROM PUBLIC;

-- ── The CHECK-constraint helpers: least privilege, stated rather than default ──
-- PostgreSQL checks EXECUTE on a CHECK constraint's function as the *inserting*
-- role, so these two are genuinely required by the writer at INSERT time. Left
-- at PostgreSQL's default ACL they were executable by PUBLIC — by every role in
-- the database — which is both more than the design needs and, because a
-- default ACL is stored as NULL, invisible to any check that compares ACLs.
-- Revoking and re-granting explicitly makes the requirement a written contract:
-- the exact ACL is pinned in the preflight (which refuses drift before this
-- block could repair it) and again in the postflight.
--
-- The owner keeps EXECUTE implicitly, as the function's owner; the reader needs
-- none, because a CHECK constraint is never evaluated on SELECT.
REVOKE ALL ON FUNCTION provider_telemetry_array_is_clean(TEXT[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION provider_telemetry_has_credential_shape(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION provider_telemetry_array_is_clean(TEXT[])
    TO workflow_provider_telemetry_writer;
GRANT EXECUTE ON FUNCTION provider_telemetry_has_credential_shape(TEXT)
    TO workflow_provider_telemetry_writer;

RESET ROLE;

-- ═══════════════════════════════════════════════════════════════════════════
-- 6. Postflight — the complete catalog contract
-- ═══════════════════════════════════════════════════════════════════════════
-- Everything below verifies what was actually created against what this file
-- promises, category by category. A same-named object with different semantics
-- is a failure, not a pass.

DO $postflight$
DECLARE
    v_problem text;
    v_schema  text := current_schema();
BEGIN
    -- ── 6a. Schema identity and table persistence ──
    SELECT string_agg(format('%s:relpersistence=%s', c.relname, c.relpersistence), ', ')
      INTO v_problem
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = v_schema AND c.relkind = 'r'
      AND c.relname = ANY (ARRAY[
          'provider_telemetry_run', 'provider_telemetry_run_event',
          'provider_telemetry_call', 'provider_sdk_invocation',
          'provider_attempt', 'provider_attempt_event',
          'provider_telemetry_migration_ledger'])
      AND c.relpersistence <> 'p';
    IF v_problem IS NOT NULL THEN
        RAISE EXCEPTION 'v63 postflight: telemetry relations must be permanent: %', v_problem
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- ── 6b. Ownership ──
    SELECT string_agg(format('%s owned by %s', c.relname,
                             pg_catalog.pg_get_userbyid(c.relowner)), ', ')
      INTO v_problem
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = v_schema
      AND c.relname = ANY (ARRAY[
          'provider_telemetry_run', 'provider_telemetry_run_event',
          'provider_telemetry_call', 'provider_sdk_invocation',
          'provider_attempt', 'provider_attempt_event',
          'provider_telemetry_migration_ledger'])
      AND pg_catalog.pg_get_userbyid(c.relowner) <> 'workflow_provider_telemetry_owner';
    IF v_problem IS NOT NULL THEN
        RAISE EXCEPTION 'v63 postflight: wrong ownership: %', v_problem
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = v_schema AND p.proname = 'provider_telemetry_reject_mutation'
          AND pg_catalog.pg_get_userbyid(p.proowner) = 'workflow_provider_telemetry_owner'
    ) THEN
        RAISE EXCEPTION 'v63 postflight: guard function is missing or wrongly owned'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- ── 6c. Guard function definition (security, language, search_path) ──
    IF NOT EXISTS (
        SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        JOIN pg_language l ON l.oid = p.prolang
        WHERE n.nspname = v_schema
          AND p.proname = 'provider_telemetry_reject_mutation'
          AND p.pronargs = 0
          AND p.prorettype = 'trigger'::regtype
          AND p.prosecdef
          AND l.lanname = 'plpgsql'
          AND p.proconfig = ARRAY['search_path=pg_catalog']::text[]
    ) THEN
        RAISE EXCEPTION
            'v63 postflight: provider_telemetry_reject_mutation() does not match its '
            'required definition (plpgsql, SECURITY DEFINER, search_path=pg_catalog, '
            'returns trigger, zero arguments)'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- ── The complete EXECUTE ACL of all three functions, as just installed ──
    -- The preflight refuses drift it finds on the way in; this states what the
    -- file promises on the way out, so a first application into an empty schema
    -- — where the preflight has nothing to check — is certified too.
    SELECT string_agg(format('%s=[%s]', d.proname, d.acl), ', ' ORDER BY d.proname)
      INTO v_problem
    FROM (
        SELECT p.proname,
               coalesce((
                   SELECT string_agg(entry.label, ',' ORDER BY entry.label)
                   FROM (
                       SELECT format('%s:%s',
                                     CASE WHEN a.grantee = 0 THEN 'PUBLIC'
                                          ELSE pg_catalog.pg_get_userbyid(a.grantee)
                                     END,
                                     a.privilege_type) AS label
                       FROM aclexplode(coalesce(
                                p.proacl,
                                pg_catalog.acldefault('f', p.proowner))) AS a
                   ) AS entry
               ), '<none>') AS acl
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = v_schema
          AND p.proname = ANY (ARRAY[
              'provider_telemetry_reject_mutation',
              'provider_telemetry_array_is_clean',
              'provider_telemetry_has_credential_shape'])
    ) AS d
    WHERE d.acl IS DISTINCT FROM (CASE d.proname
              WHEN 'provider_telemetry_reject_mutation' THEN
                  'workflow_provider_telemetry_owner:EXECUTE'
              ELSE
                  'workflow_provider_telemetry_owner:EXECUTE,'
                  'workflow_provider_telemetry_writer:EXECUTE'
          END);
    IF v_problem IS NOT NULL THEN
        RAISE EXCEPTION
            'v63 postflight: telemetry function EXECUTE ACL is not the required '
            'set: %', v_problem
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- The writer must actually be able to run both CHECK helpers, and PUBLIC
    -- and the reader must not. Asserted through `has_function_privilege` as
    -- well as through the ACL text above, because these are two different
    -- questions: one is "the catalog says what we wrote", the other is "the
    -- privilege system answers the way the runtime needs".
    IF EXISTS (
        SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = v_schema
          AND p.proname = ANY (ARRAY[
              'provider_telemetry_array_is_clean',
              'provider_telemetry_has_credential_shape'])
          AND NOT has_function_privilege(
                  'workflow_provider_telemetry_writer', p.oid, 'EXECUTE')
    ) THEN
        RAISE EXCEPTION
            'v63 postflight: the writer cannot execute a CHECK-constraint helper, '
            'so every telemetry INSERT would fail'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    IF EXISTS (
        SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = v_schema
          AND p.proname = ANY (ARRAY[
              'provider_telemetry_reject_mutation',
              'provider_telemetry_array_is_clean',
              'provider_telemetry_has_credential_shape'])
          AND (has_function_privilege('public', p.oid, 'EXECUTE')
               OR has_function_privilege(
                      'workflow_provider_telemetry_reader', p.oid, 'EXECUTE'))
    ) THEN
        RAISE EXCEPTION
            'v63 postflight: PUBLIC or the reader holds EXECUTE on a telemetry function'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- The body this file just installed must be the body this file declares.
    -- Stated twice on purpose: the preflight refuses a *tampered* body before
    -- CREATE OR REPLACE can repair it, and this refuses a body that never
    -- matched — an edited copy of this file, or a replacement committed
    -- concurrently between the two checks.
    SELECT string_agg(format('%s(sha256=%s)', p.proname,
                             encode(sha256(convert_to(p.prosrc, 'UTF8')), 'hex')), ', ')
      INTO v_problem
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = v_schema
      AND p.proname = ANY (ARRAY[
          'provider_telemetry_reject_mutation',
          'provider_telemetry_array_is_clean',
          'provider_telemetry_has_credential_shape'])
      AND p.prosrc IS DISTINCT FROM (
          CASE p.proname
              WHEN 'provider_telemetry_reject_mutation' THEN
$body_post$
BEGIN
    RAISE EXCEPTION
        'provider telemetry is append-only; % on % is not permitted',
        TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END;
$body_post$
              WHEN 'provider_telemetry_array_is_clean' THEN
$body_post$
    SELECT p_values IS NULL
        OR (array_position(p_values, NULL) IS NULL
            AND cardinality(p_values) = (
                SELECT count(DISTINCT item) FROM unnest(p_values) AS item));
$body_post$
              WHEN 'provider_telemetry_has_credential_shape' THEN
$body_post$
    SELECT p_value IS NOT NULL AND (
        p_value ~* 'sk-ant-'
        OR p_value ~* '\ysk-[A-Za-z0-9_-]{8,}'
        OR p_value ~* '\y[rs]k_(live|test)_[A-Za-z0-9]{8,}'
        OR p_value ~* '\ygh[pousr]_[A-Za-z0-9]{8,}'
        OR p_value ~* '\yxox[baprs]-'
        OR p_value ~ '\y(AKIA|ASIA|AROA|AIDA)[A-Z0-9]{12,}'
        OR p_value ~ '\yAIza[A-Za-z0-9_-]{20,}'
        OR p_value ~* 'bearer[[:space:]_.:=-]*[A-Za-z0-9+/=_-]{4,}'
        OR p_value ~* 'basic[[:space:]_.:=-]*[A-Za-z0-9+/=_-]{8,}'
        OR p_value ~* 'authoriz(ation|ed?)[[:space:]_.:=-]'
        OR p_value ~* '(api[_.-]?key|access[_.-]?token|auth[_.-]?token|id[_.-]?token|refresh[_.-]?token|session[_.-]?id|session|secret|passwd|password|credential|cookie|private[_.-]?key)[[:space:]_.-]*[:=]'
        OR p_value ~* '\y(api[_.-]?key|access[_.-]?token|auth[_.-]?token|id[_.-]?token|refresh[_.-]?token|session[_.-]?id|secret|passwd|password|credential|cookie|private[_.-]?key)[[:space:]_.-]+[A-Za-z0-9+/=_-]{6,}'
        OR p_value ~* '[a-z][a-z0-9+.-]*://'
        OR p_value ~ '@'
        OR p_value ~* '%(20|3a|3d|2f|2b)'
        OR p_value ~ '\yeyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}'
    );
$body_post$
          END);
    IF v_problem IS NOT NULL THEN
        RAISE EXCEPTION 'v63 postflight: telemetry function body drift: %', v_problem
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- ── 6d. Triggers: presence, exact definition, and ENABLED state ──
    -- A disabled trigger is the exact drift the previous design could not see:
    -- the object is still there, and the table is no longer append-only.
    -- `tgtype` is compared as an exact bitmask rather than by matching the
    -- rendered definition text: it pins timing, level and the exact event set in
    -- one value, and is immune to how PostgreSQL happens to order the events
    -- when it renders `pg_get_triggerdef`.
    --   ROW(1) | BEFORE(2) | DELETE(8) | UPDATE(16)      = 27
    --   STATEMENT(0) | BEFORE(2) | TRUNCATE(32)          = 34
    SELECT string_agg(missing.detail, '; ') INTO v_problem FROM (
        SELECT format('%s.%s', t.relname, t.tgname) AS detail
        FROM (
            SELECT tbl AS relname,
                   'trg_' || tbl || '_no_mutation' AS tgname,
                   27::smallint AS tgtype
            FROM unnest(ARRAY[
                'provider_telemetry_run', 'provider_telemetry_run_event',
                'provider_telemetry_call', 'provider_sdk_invocation',
                'provider_attempt', 'provider_attempt_event',
                'provider_telemetry_migration_ledger']) AS tbl
            UNION ALL
            SELECT tbl, 'trg_' || tbl || '_no_truncate', 34::smallint
            FROM unnest(ARRAY[
                'provider_telemetry_run', 'provider_telemetry_run_event',
                'provider_telemetry_call', 'provider_sdk_invocation',
                'provider_attempt', 'provider_attempt_event',
                'provider_telemetry_migration_ledger']) AS tbl
        ) AS t
        WHERE NOT EXISTS (
            SELECT 1
            FROM pg_trigger tg
            JOIN pg_class c ON c.oid = tg.tgrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_proc p ON p.oid = tg.tgfoid
            WHERE n.nspname = v_schema AND NOT tg.tgisinternal
              AND c.relname = t.relname AND tg.tgname = t.tgname
              AND p.proname = 'provider_telemetry_reject_mutation'
              -- 'O' = origin: the trigger fires. 'D' = disabled.
              AND tg.tgenabled = 'O'
              AND tg.tgtype = t.tgtype
              -- No WHEN clause and no argument list: either would let the guard
              -- be bypassed for a chosen row.
              AND tg.tgqual IS NULL
              AND tg.tgnargs = 0
        )
    ) AS missing;
    IF v_problem IS NOT NULL THEN
        RAISE EXCEPTION
            'v63 postflight: append-only triggers absent, disabled, or redefined: %',
            v_problem
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- ── 6e. ACLs ──
    -- The writer must hold exactly INSERT+SELECT; the reader exactly SELECT;
    -- PUBLIC nothing at all.
    SELECT string_agg(detail, '; ') INTO v_problem FROM (
        SELECT format('%s:writer', t.tbl) AS detail
        FROM unnest(ARRAY[
            'provider_telemetry_run', 'provider_telemetry_run_event',
            'provider_telemetry_call', 'provider_sdk_invocation',
            'provider_attempt', 'provider_attempt_event',
            'provider_telemetry_migration_ledger']) AS t(tbl)
        WHERE NOT (
            has_table_privilege('workflow_provider_telemetry_writer', t.tbl, 'INSERT')
            AND has_table_privilege('workflow_provider_telemetry_writer', t.tbl, 'SELECT')
            AND NOT has_table_privilege('workflow_provider_telemetry_writer', t.tbl, 'UPDATE')
            AND NOT has_table_privilege('workflow_provider_telemetry_writer', t.tbl, 'DELETE')
            AND NOT has_table_privilege('workflow_provider_telemetry_writer', t.tbl, 'TRUNCATE')
            AND NOT has_table_privilege('workflow_provider_telemetry_writer', t.tbl, 'TRIGGER')
            AND NOT has_table_privilege('workflow_provider_telemetry_writer', t.tbl, 'REFERENCES'))
        UNION ALL
        SELECT format('%s:reader', t.tbl)
        FROM unnest(ARRAY[
            'provider_telemetry_run', 'provider_telemetry_run_event',
            'provider_telemetry_call', 'provider_sdk_invocation',
            'provider_attempt', 'provider_attempt_event',
            'provider_telemetry_migration_ledger']) AS t(tbl)
        WHERE NOT (
            has_table_privilege('workflow_provider_telemetry_reader', t.tbl, 'SELECT')
            AND NOT has_table_privilege('workflow_provider_telemetry_reader', t.tbl, 'INSERT')
            AND NOT has_table_privilege('workflow_provider_telemetry_reader', t.tbl, 'UPDATE')
            AND NOT has_table_privilege('workflow_provider_telemetry_reader', t.tbl, 'DELETE')
            AND NOT has_table_privilege('workflow_provider_telemetry_reader', t.tbl, 'TRUNCATE')
            AND NOT has_table_privilege('workflow_provider_telemetry_reader', t.tbl, 'TRIGGER'))
        UNION ALL
        SELECT format('%s:public', t.tbl)
        FROM unnest(ARRAY[
            'provider_telemetry_run', 'provider_telemetry_run_event',
            'provider_telemetry_call', 'provider_sdk_invocation',
            'provider_attempt', 'provider_attempt_event',
            'provider_telemetry_migration_ledger']) AS t(tbl)
        WHERE has_table_privilege('public', t.tbl, 'SELECT')
           OR has_table_privilege('public', t.tbl, 'INSERT')
           OR has_table_privilege('public', t.tbl, 'UPDATE')
           OR has_table_privilege('public', t.tbl, 'DELETE')
    ) AS acl_problems;
    IF v_problem IS NOT NULL THEN
        RAISE EXCEPTION 'v63 postflight: ACL contract violated: %', v_problem
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- ── 6f. Free-standing: no foreign key to any application table ──
    -- Telemetry must never fail because a parent row is missing, must never take
    -- a lock on a table a run writes, and must be restorable on its own.
    IF EXISTS (
        SELECT 1 FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = v_schema AND con.contype = 'f'
          AND c.relname = ANY (ARRAY[
              'provider_telemetry_run', 'provider_telemetry_run_event',
              'provider_telemetry_call', 'provider_sdk_invocation',
              'provider_attempt', 'provider_attempt_event',
              'provider_telemetry_migration_ledger'])
    ) THEN
        RAISE EXCEPTION 'v63 postflight: telemetry relations must declare no foreign key'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- ── 6g. Identity columns ──
    -- Every relation's ordering key must be GENERATED ALWAYS (not BY DEFAULT):
    -- BY DEFAULT would let a writer supply its own sequence value and forge
    -- insertion order.
    SELECT string_agg(format('%s.%s', t.tbl, t.col), ', ') INTO v_problem FROM (VALUES
        ('provider_telemetry_run', 'run_sequence'),
        ('provider_telemetry_run_event', 'run_event_sequence'),
        ('provider_telemetry_call', 'call_sequence'),
        ('provider_sdk_invocation', 'invocation_sequence'),
        ('provider_attempt', 'attempt_sequence'),
        ('provider_attempt_event', 'event_sequence'),
        ('provider_telemetry_migration_ledger', 'ledger_sequence')
    ) AS t(tbl, col)
    WHERE NOT EXISTS (
        SELECT 1 FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = v_schema AND c.relname = t.tbl AND a.attname = t.col
          AND a.attidentity = 'a'
          AND a.atttypid = 'bigint'::regtype
    );
    IF v_problem IS NOT NULL THEN
        RAISE EXCEPTION
            'v63 postflight: ordering keys must be BIGINT GENERATED ALWAYS AS IDENTITY: %',
            v_problem
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- ── 6h. Columns: name, exact type, nullability, default, identity ──
    -- The pinned contract below is the whole reason a same-named table cannot
    -- pass: a `provider_attempt` with one column, or with `input_tokens` widened
    -- to `numeric`, or with `is_terminal` made nullable, diverges here and the
    -- migration refuses. The comparison runs in both directions, so an *extra*
    -- column is a divergence too.
    WITH expected(tbl, col, typ, is_notnull, dflt, col_identity) AS (VALUES
        ('provider_attempt','attempt_id','uuid',true,'',''),
        ('provider_attempt','attempt_sequence','bigint',true,'','a'),
        ('provider_attempt','call_id','uuid',true,'',''),
        ('provider_attempt','http_retry_ordinal','integer',true,'',''),
        ('provider_attempt','invocation_id','uuid',true,'',''),
        ('provider_attempt','posture','text',true,'',''),
        ('provider_attempt','provider','text',true,'',''),
        ('provider_attempt','recorded_at','timestamp with time zone',true,'now()',''),
        ('provider_attempt','request_method','text',true,'''POST''::text',''),
        ('provider_attempt','request_path','text',true,'''''::text',''),
        ('provider_attempt','request_started_at','timestamp with time zone',true,'',''),
        ('provider_attempt','requested_model','text',true,'',''),
        ('provider_attempt','telemetry_run_id','uuid',true,'',''),
        ('provider_attempt','worker_id','text',true,'''''::text',''),
        ('provider_attempt_event','breaker_failure_count_after','integer',false,'',''),
        ('provider_attempt_event','breaker_snapshot_status_after','text',true,'''unknown''::text',''),
        ('provider_attempt_event','breaker_state_after','text',true,'''unknown''::text',''),
        ('provider_attempt_event','cache_creation_tokens','bigint',false,'',''),
        ('provider_attempt_event','cache_creation_tokens_status','text',true,'''absent''::text',''),
        ('provider_attempt_event','cache_read_tokens','bigint',false,'',''),
        ('provider_attempt_event','cache_read_tokens_status','text',true,'''absent''::text',''),
        ('provider_attempt_event','call_id','uuid',true,'',''),
        ('provider_attempt_event','effective_model','text',false,'',''),
        ('provider_attempt_event','effective_model_status','text',true,'''absent''::text',''),
        ('provider_attempt_event','error_category','text',true,'''''::text',''),
        ('provider_attempt_event','error_identity','text',true,'''''::text',''),
        ('provider_attempt_event','event_id','uuid',true,'',''),
        ('provider_attempt_event','event_kind','text',true,'',''),
        ('provider_attempt_event','event_ordinal','integer',true,'',''),
        ('provider_attempt_event','event_sequence','bigint',true,'','a'),
        ('provider_attempt_event','failure_class','text',true,'''''::text',''),
        ('provider_attempt_event','http_status','integer',false,'',''),
        ('provider_attempt_event','http_status_status','text',true,'''absent''::text',''),
        ('provider_attempt_event','input_tokens','bigint',false,'',''),
        ('provider_attempt_event','input_tokens_status','text',true,'''absent''::text',''),
        ('provider_attempt_event','is_terminal','boolean',true,'',''),
        ('provider_attempt_event','observed_at','timestamp with time zone',true,'',''),
        ('provider_attempt_event','output_tokens','bigint',false,'',''),
        ('provider_attempt_event','output_tokens_status','text',true,'''absent''::text',''),
        ('provider_attempt_event','provider_request_id','text',false,'',''),
        ('provider_attempt_event','provider_request_id_status','text',true,'''absent''::text',''),
        ('provider_attempt_event','provider_response_id','text',false,'',''),
        ('provider_attempt_event','provider_response_id_status','text',true,'''absent''::text',''),
        ('provider_attempt_event','recorded_at','timestamp with time zone',true,'now()',''),
        ('provider_attempt_event','response_metadata_fingerprint','text',true,'',''),
        ('provider_attempt_event','retry_after','text',false,'',''),
        ('provider_attempt_event','retry_after_status','text',true,'''absent''::text',''),
        ('provider_attempt_event','schema_version','integer',true,'',''),
        ('provider_attempt_event','stop_reason','text',false,'',''),
        ('provider_attempt_event','stop_reason_status','text',true,'''absent''::text',''),
        ('provider_attempt_event','subject_id','uuid',true,'',''),
        ('provider_attempt_event','subject_kind','text',true,'',''),
        ('provider_attempt_event','telemetry_run_id','uuid',true,'',''),
        ('provider_attempt_event','transport_outcome','text',true,'''''::text',''),
        ('provider_attempt_event','value_details','text',true,'''''::text',''),
        ('provider_attempt_event','worker_id','text',true,'''''::text',''),
        ('provider_sdk_invocation','attempt_ordinal','integer',true,'',''),
        ('provider_sdk_invocation','breaker_failure_count_before','integer',false,'',''),
        ('provider_sdk_invocation','breaker_snapshot_status_before','text',true,'',''),
        ('provider_sdk_invocation','breaker_state_before','text',true,'',''),
        ('provider_sdk_invocation','call_id','uuid',true,'',''),
        ('provider_sdk_invocation','candidate_ordinal','integer',true,'',''),
        ('provider_sdk_invocation','entry_point','text',true,'',''),
        ('provider_sdk_invocation','external_project_id','text',true,'''''::text',''),
        ('provider_sdk_invocation','external_run_id','text',true,'''''::text',''),
        ('provider_sdk_invocation','fallback_candidate','boolean',true,'false',''),
        ('provider_sdk_invocation','fallback_from_model','text',true,'''''::text',''),
        ('provider_sdk_invocation','fallback_from_provider','text',true,'''''::text',''),
        ('provider_sdk_invocation','invocation_id','uuid',true,'',''),
        ('provider_sdk_invocation','invocation_kind','text',true,'',''),
        ('provider_sdk_invocation','invocation_sequence','bigint',true,'','a'),
        ('provider_sdk_invocation','job_id','text',true,'''''::text',''),
        ('provider_sdk_invocation','phase','text',true,'''''::text',''),
        ('provider_sdk_invocation','posture','text',true,'',''),
        ('provider_sdk_invocation','project_id','uuid',false,'',''),
        ('provider_sdk_invocation','provider','text',true,'',''),
        ('provider_sdk_invocation','recorded_at','timestamp with time zone',true,'now()',''),
        ('provider_sdk_invocation','request_config_fingerprint','text',true,'',''),
        ('provider_sdk_invocation','requested_model','text',true,'',''),
        ('provider_sdk_invocation','retry_ordinal','integer',true,'',''),
        ('provider_sdk_invocation','routing_decision_fingerprint','text',true,'',''),
        ('provider_sdk_invocation','started_at','timestamp with time zone',true,'',''),
        ('provider_sdk_invocation','telemetry_run_id','uuid',true,'',''),
        ('provider_sdk_invocation','worker_id','text',true,'''''::text',''),
        ('provider_telemetry_call','call_id','uuid',true,'',''),
        ('provider_telemetry_call','call_sequence','bigint',true,'','a'),
        ('provider_telemetry_call','candidate_count','integer',true,'',''),
        ('provider_telemetry_call','entry_point','text',true,'',''),
        ('provider_telemetry_call','external_project_id','text',true,'''''::text',''),
        ('provider_telemetry_call','external_run_id','text',true,'''''::text',''),
        ('provider_telemetry_call','job_id','text',true,'''''::text',''),
        ('provider_telemetry_call','phase','text',true,'''''::text',''),
        ('provider_telemetry_call','posture','text',true,'',''),
        ('provider_telemetry_call','project_id','uuid',false,'',''),
        ('provider_telemetry_call','recorded_at','timestamp with time zone',true,'now()',''),
        ('provider_telemetry_call','request_config_fingerprint','text',true,'',''),
        ('provider_telemetry_call','requested_model','text',true,'',''),
        ('provider_telemetry_call','requested_provider','text',true,'',''),
        ('provider_telemetry_call','routing_decision_fingerprint','text',true,'',''),
        ('provider_telemetry_call','started_at','timestamp with time zone',true,'',''),
        ('provider_telemetry_call','telemetry_run_id','uuid',true,'',''),
        ('provider_telemetry_call','worker_id','text',true,'''''::text',''),
        ('provider_telemetry_migration_ledger','applied_at','timestamp with time zone',true,'',''),
        ('provider_telemetry_migration_ledger','applied_by','text',true,'',''),
        ('provider_telemetry_migration_ledger','ledger_id','uuid',true,'',''),
        ('provider_telemetry_migration_ledger','ledger_sequence','bigint',true,'','a'),
        ('provider_telemetry_migration_ledger','migration_name','text',true,'',''),
        ('provider_telemetry_migration_ledger','migration_sha256','text',true,'',''),
        ('provider_telemetry_migration_ledger','outcome','text',true,'',''),
        ('provider_telemetry_migration_ledger','schema_version','integer',true,'',''),
        ('provider_telemetry_run','entry_point','text',true,'',''),
        ('provider_telemetry_run','expected_phases','text[]',true,'ARRAY[]::text[]',''),
        ('provider_telemetry_run','external_project_id','text',true,'''''::text',''),
        ('provider_telemetry_run','external_run_id','text',true,'''''::text',''),
        ('provider_telemetry_run','job_id','text',true,'''''::text',''),
        ('provider_telemetry_run','posture','text',true,'',''),
        ('provider_telemetry_run','project_id','uuid',false,'',''),
        ('provider_telemetry_run','recorded_at','timestamp with time zone',true,'now()',''),
        ('provider_telemetry_run','run_sequence','bigint',true,'','a'),
        ('provider_telemetry_run','runtime_fingerprint','text',true,'',''),
        ('provider_telemetry_run','schema_version','integer',true,'',''),
        ('provider_telemetry_run','source_commit','text',true,'''''::text',''),
        ('provider_telemetry_run','started_at','timestamp with time zone',true,'',''),
        ('provider_telemetry_run','telemetry_required','boolean',true,'',''),
        ('provider_telemetry_run','telemetry_run_id','uuid',true,'',''),
        ('provider_telemetry_run_event','ambiguous_events','bigint',true,'0',''),
        ('provider_telemetry_run_event','detail','text',true,'''''::text',''),
        ('provider_telemetry_run_event','drain_status','text',true,'''unknown''::text',''),
        ('provider_telemetry_run_event','expected_work_digest','text',true,'''''::text',''),
        ('provider_telemetry_run_event','dropped_events','bigint',true,'0',''),
        ('provider_telemetry_run_event','event_id','uuid',true,'',''),
        ('provider_telemetry_run_event','event_kind','text',true,'',''),
        ('provider_telemetry_run_event','expected_calls','bigint',true,'0',''),
        ('provider_telemetry_run_event','observed_at','timestamp with time zone',true,'',''),
        ('provider_telemetry_run_event','observed_calls','bigint',true,'0',''),
        ('provider_telemetry_run_event','posture','text',true,'',''),
        ('provider_telemetry_run_event','reconciliation_status','text',true,'''pending''::text',''),
        ('provider_telemetry_run_event','recorded_at','timestamp with time zone',true,'now()',''),
        ('provider_telemetry_run_event','run_event_sequence','bigint',true,'','a'),
        ('provider_telemetry_run_event','started_events','bigint',true,'0',''),
        ('provider_telemetry_run_event','telemetry_run_id','uuid',true,'',''),
        ('provider_telemetry_run_event','terminal_events','bigint',true,'0',''),
        ('provider_telemetry_run_event','undurable_events','bigint',true,'0',''),
        ('provider_telemetry_run_event','unmatched_starts','bigint',true,'0',''),
        ('provider_telemetry_run_event','worker_id','text',true,'''''::text','')
    ), actual(tbl, col, typ, is_notnull, dflt, col_identity) AS (
        SELECT c.relname::text, a.attname::text,
               format_type(a.atttypid, a.atttypmod)::text,
               a.attnotnull,
               coalesce(pg_catalog.pg_get_expr(d.adbin, d.adrelid), '')::text,
               a.attidentity::text
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE n.nspname = v_schema AND a.attnum > 0 AND NOT a.attisdropped
          AND c.relname = ANY (ARRAY[
              'provider_telemetry_run', 'provider_telemetry_run_event',
              'provider_telemetry_call', 'provider_sdk_invocation',
              'provider_attempt', 'provider_attempt_event',
              'provider_telemetry_migration_ledger'])
    )
    SELECT string_agg(format('%s.%s(%s)', d.tbl, d.col, d.side), '; ' ORDER BY d.tbl, d.col)
      INTO v_problem
    FROM (
        SELECT tbl, col, 'missing_or_altered' AS side FROM (
            SELECT * FROM expected EXCEPT SELECT * FROM actual) AS m
        UNION ALL
        SELECT tbl, col, 'unexpected' FROM (
            SELECT * FROM actual EXCEPT SELECT * FROM expected) AS u
    ) AS d;
    IF v_problem IS NOT NULL THEN
        RAISE EXCEPTION 'v63 postflight: column contract violated: %', v_problem
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- ── 6i. Constraints, by exact definition ──
    -- Names are not enough: a CHECK renamed to the right thing but weakened to
    -- `true` would otherwise pass. `pg_get_constraintdef` is the normalized,
    -- catalog-rendered definition, so this compares semantics.
    WITH expected(tbl, name, def) AS (VALUES
        ('provider_attempt','ck_pa_identity','CHECK (((provider <> ''''::text) AND (requested_model <> ''''::text)))'),
        ('provider_attempt','ck_pa_method','CHECK ((request_method ~ ''^[A-Z]{3,10}$''::text))'),
        ('provider_attempt','ck_pa_ordinal','CHECK ((http_retry_ordinal >= 1))'),
        ('provider_attempt','ck_pa_path','CHECK ((request_path ~ ''^[A-Za-z0-9/._-]{0,128}$''::text))'),
        ('provider_attempt','ck_pa_posture','CHECK ((posture = ANY (ARRAY[''observational''::text, ''strict''::text])))'),
        ('provider_attempt','provider_attempt_pkey','PRIMARY KEY (attempt_id)'),
        ('provider_attempt','uq_pa_invocation_ordinal','UNIQUE (invocation_id, http_retry_ordinal)'),
        ('provider_attempt','uq_pa_sequence','UNIQUE (attempt_sequence)'),
        ('provider_attempt_event','ck_pae_breaker_atomic','CHECK ((((breaker_snapshot_status_after = ''valid''::text) AND (breaker_state_after = ANY (ARRAY[''closed''::text, ''open''::text])) AND (breaker_failure_count_after IS NOT NULL) AND (breaker_failure_count_after >= 0)) OR ((breaker_snapshot_status_after = ''unknown''::text) AND (breaker_state_after = ''unknown''::text) AND (breaker_failure_count_after IS NULL))))'),
        ('provider_attempt_event','ck_pae_breaker_state','CHECK ((breaker_state_after = ANY (ARRAY[''closed''::text, ''open''::text, ''unknown''::text])))'),
        ('provider_attempt_event','ck_pae_breaker_status','CHECK ((breaker_snapshot_status_after = ANY (ARRAY[''valid''::text, ''unknown''::text])))'),
        ('provider_attempt_event','ck_pae_event_kind','CHECK ((event_kind = ANY (ARRAY[''completed''::text, ''provider_failure''::text, ''cancelled''::text, ''unknown''::text, ''skipped''::text, ''observation''::text, ''transformation_failure''::text, ''capture_failure''::text])))'),
        ('provider_attempt_event','ck_pae_no_credential_shape','CHECK (((NOT provider_telemetry_has_credential_shape(provider_response_id)) AND (NOT provider_telemetry_has_credential_shape(provider_request_id)) AND (NOT provider_telemetry_has_credential_shape(effective_model)) AND (NOT provider_telemetry_has_credential_shape(stop_reason)) AND (NOT provider_telemetry_has_credential_shape(retry_after))))'),
        ('provider_attempt_event','ck_pae_metadata_fingerprint','CHECK ((response_metadata_fingerprint ~ ''^[0-9a-f]{64}$''::text))'),
        ('provider_attempt_event','ck_pae_ordinal','CHECK ((event_ordinal >= 1))'),
        ('provider_attempt_event','ck_pae_safe_grammars','CHECK ((((provider_response_id IS NULL) OR (provider_response_id ~ ''^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$''::text)) AND ((provider_request_id IS NULL) OR (provider_request_id ~ ''^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$''::text)) AND ((effective_model IS NULL) OR (effective_model ~ ''^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$''::text)) AND ((stop_reason IS NULL) OR (stop_reason ~ ''^[A-Za-z][A-Za-z0-9_-]{0,63}$''::text)) AND ((retry_after IS NULL) OR (retry_after ~ ''^[0-9]{1,10}(\.[0-9]{1,6})?$''::text)) AND (error_category ~ ''^[a-z0-9_]{0,64}$''::text) AND (failure_class ~ ''^[A-Za-z0-9_.-]{0,64}$''::text) AND (error_identity ~ ''^[A-Za-z0-9_.= -]*$''::text) AND (length(error_identity) <= 256) AND (value_details ~ ''^[A-Za-z0-9_;=-]*$''::text) AND (length(value_details) <= 512)))'),
        ('provider_attempt_event','ck_pae_schema_version','CHECK ((schema_version >= 1))'),
        ('provider_attempt_event','ck_pae_skipped_is_empty','CHECK (((event_kind <> ''skipped''::text) OR ((provider_response_id IS NULL) AND (stop_reason IS NULL) AND (effective_model IS NULL) AND (input_tokens IS NULL) AND (output_tokens IS NULL) AND (cache_read_tokens IS NULL) AND (cache_creation_tokens IS NULL) AND (http_status IS NULL) AND (transport_outcome = ''''::text))))'),
        ('provider_attempt_event','ck_pae_subject_kind','CHECK ((subject_kind = ANY (ARRAY[''sdk_invocation''::text, ''http_attempt''::text])))'),
        ('provider_attempt_event','ck_pae_terminal_agreement','CHECK ((is_terminal = (event_kind = ANY (ARRAY[''completed''::text, ''provider_failure''::text, ''cancelled''::text, ''unknown''::text, ''skipped''::text]))))'),
        ('provider_attempt_event','ck_pae_transport_is_http','CHECK (((subject_kind = ''http_attempt''::text) OR ((transport_outcome = ''''::text) AND (http_status IS NULL))))'),
        ('provider_attempt_event','ck_pae_transport_outcome','CHECK ((transport_outcome = ANY (ARRAY[''''::text, ''response''::text, ''transport_error''::text, ''cancelled''::text, ''unknown''::text])))'),
        ('provider_attempt_event','ck_pae_usage_bounds','CHECK ((((input_tokens IS NULL) OR ((input_tokens >= 0) AND (input_tokens <= 2147483647))) AND ((output_tokens IS NULL) OR ((output_tokens >= 0) AND (output_tokens <= 2147483647))) AND ((cache_read_tokens IS NULL) OR ((cache_read_tokens >= 0) AND (cache_read_tokens <= 2147483647))) AND ((cache_creation_tokens IS NULL) OR ((cache_creation_tokens >= 0) AND (cache_creation_tokens <= 2147483647))) AND ((http_status IS NULL) OR ((http_status >= 100) AND (http_status <= 599)))))'),
        ('provider_attempt_event','ck_pae_value_status_agreement','CHECK ((((http_status IS NOT NULL) = (http_status_status = ''valid''::text)) AND ((provider_request_id IS NOT NULL) = (provider_request_id_status = ''valid''::text)) AND ((retry_after IS NOT NULL) = (retry_after_status = ''valid''::text)) AND ((provider_response_id IS NOT NULL) = (provider_response_id_status = ''valid''::text)) AND ((effective_model IS NOT NULL) = (effective_model_status = ''valid''::text)) AND ((stop_reason IS NOT NULL) = (stop_reason_status = ''valid''::text)) AND ((input_tokens IS NOT NULL) = (input_tokens_status = ''valid''::text)) AND ((output_tokens IS NOT NULL) = (output_tokens_status = ''valid''::text)) AND ((cache_read_tokens IS NOT NULL) = (cache_read_tokens_status = ''valid''::text)) AND ((cache_creation_tokens IS NOT NULL) = (cache_creation_tokens_status = ''valid''::text))))'),
        ('provider_attempt_event','ck_pae_value_statuses','CHECK (((http_status_status = ANY (ARRAY[''absent''::text, ''null''::text, ''valid''::text, ''invalid''::text, ''redacted''::text, ''unsupported''::text, ''unknown_value''::text])) AND (provider_request_id_status = ANY (ARRAY[''absent''::text, ''null''::text, ''valid''::text, ''invalid''::text, ''redacted''::text, ''unsupported''::text, ''unknown_value''::text])) AND (retry_after_status = ANY (ARRAY[''absent''::text, ''null''::text, ''valid''::text, ''invalid''::text, ''redacted''::text, ''unsupported''::text, ''unknown_value''::text])) AND (provider_response_id_status = ANY (ARRAY[''absent''::text, ''null''::text, ''valid''::text, ''invalid''::text, ''redacted''::text, ''unsupported''::text, ''unknown_value''::text])) AND (effective_model_status = ANY (ARRAY[''absent''::text, ''null''::text, ''valid''::text, ''invalid''::text, ''redacted''::text, ''unsupported''::text, ''unknown_value''::text])) AND (stop_reason_status = ANY (ARRAY[''absent''::text, ''null''::text, ''valid''::text, ''invalid''::text, ''redacted''::text, ''unsupported''::text, ''unknown_value''::text])) AND (input_tokens_status = ANY (ARRAY[''absent''::text, ''null''::text, ''valid''::text, ''invalid''::text, ''redacted''::text, ''unsupported''::text, ''unknown_value''::text])) AND (output_tokens_status = ANY (ARRAY[''absent''::text, ''null''::text, ''valid''::text, ''invalid''::text, ''redacted''::text, ''unsupported''::text, ''unknown_value''::text])) AND (cache_read_tokens_status = ANY (ARRAY[''absent''::text, ''null''::text, ''valid''::text, ''invalid''::text, ''redacted''::text, ''unsupported''::text, ''unknown_value''::text])) AND (cache_creation_tokens_status = ANY (ARRAY[''absent''::text, ''null''::text, ''valid''::text, ''invalid''::text, ''redacted''::text, ''unsupported''::text, ''unknown_value''::text]))))'),
        ('provider_attempt_event','provider_attempt_event_pkey','PRIMARY KEY (event_id)'),
        ('provider_attempt_event','uq_pae_sequence','UNIQUE (event_sequence)'),
        ('provider_attempt_event','uq_pae_subject_ordinal','UNIQUE (subject_id, event_ordinal)'),
        ('provider_sdk_invocation','ck_psi_breaker_atomic','CHECK ((((breaker_snapshot_status_before = ''valid''::text) AND (breaker_state_before = ANY (ARRAY[''closed''::text, ''open''::text])) AND (breaker_failure_count_before IS NOT NULL) AND (breaker_failure_count_before >= 0)) OR ((breaker_snapshot_status_before = ''unknown''::text) AND (breaker_state_before = ''unknown''::text) AND (breaker_failure_count_before IS NULL))))'),
        ('provider_sdk_invocation','ck_psi_breaker_state','CHECK ((breaker_state_before = ANY (ARRAY[''closed''::text, ''open''::text, ''unknown''::text])))'),
        ('provider_sdk_invocation','ck_psi_breaker_status','CHECK ((breaker_snapshot_status_before = ANY (ARRAY[''valid''::text, ''unknown''::text])))'),
        ('provider_sdk_invocation','ck_psi_config_fingerprint','CHECK ((request_config_fingerprint ~ ''^[0-9a-f]{64}$''::text))'),
        ('provider_sdk_invocation','ck_psi_entry_point','CHECK ((entry_point ~ ''^[a-z][a-z0-9_]{0,63}$''::text))'),
        ('provider_sdk_invocation','ck_psi_fallback','CHECK ((((NOT fallback_candidate) AND (fallback_from_provider = ''''::text) AND (fallback_from_model = ''''::text)) OR (fallback_candidate AND (fallback_from_provider <> ''''::text))))'),
        ('provider_sdk_invocation','ck_psi_identity','CHECK (((provider <> ''''::text) AND (requested_model <> ''''::text)))'),
        ('provider_sdk_invocation','ck_psi_kind','CHECK ((invocation_kind = ANY (ARRAY[''provider_call''::text, ''skipped_candidate''::text])))'),
        ('provider_sdk_invocation','ck_psi_ordinals','CHECK (((candidate_ordinal >= 1) AND (retry_ordinal >= 1) AND (attempt_ordinal >= 1)))'),
        ('provider_sdk_invocation','ck_psi_posture','CHECK ((posture = ANY (ARRAY[''observational''::text, ''strict''::text])))'),
        ('provider_sdk_invocation','ck_psi_routing_fingerprint','CHECK ((routing_decision_fingerprint ~ ''^[0-9a-f]{64}$''::text))'),
        ('provider_sdk_invocation','provider_sdk_invocation_pkey','PRIMARY KEY (invocation_id)'),
        ('provider_sdk_invocation','uq_psi_call_attempt','UNIQUE (call_id, attempt_ordinal)'),
        ('provider_sdk_invocation','uq_psi_sequence','UNIQUE (invocation_sequence)'),
        ('provider_telemetry_call','ck_ptc_candidate_count','CHECK ((candidate_count >= 1))'),
        ('provider_telemetry_call','ck_ptc_config_fingerprint','CHECK ((request_config_fingerprint ~ ''^[0-9a-f]{64}$''::text))'),
        ('provider_telemetry_call','ck_ptc_entry_point','CHECK ((entry_point ~ ''^[a-z][a-z0-9_]{0,63}$''::text))'),
        ('provider_telemetry_call','ck_ptc_identity','CHECK (((requested_provider <> ''''::text) AND (requested_model <> ''''::text)))'),
        ('provider_telemetry_call','ck_ptc_posture','CHECK ((posture = ANY (ARRAY[''observational''::text, ''strict''::text])))'),
        ('provider_telemetry_call','ck_ptc_routing_fingerprint','CHECK ((routing_decision_fingerprint ~ ''^[0-9a-f]{64}$''::text))'),
        ('provider_telemetry_call','provider_telemetry_call_pkey','PRIMARY KEY (call_id)'),
        ('provider_telemetry_call','uq_ptc_sequence','UNIQUE (call_sequence)'),
        ('provider_telemetry_migration_ledger','ck_ptml_applied_by','CHECK ((applied_by ~ ''^[A-Za-z0-9_.-]{1,64}$''::text))'),
        ('provider_telemetry_migration_ledger','ck_ptml_name','CHECK ((migration_name ~ ''^v[0-9]+_[a-z0-9_]+\.sql$''::text))'),
        ('provider_telemetry_migration_ledger','ck_ptml_outcome','CHECK ((outcome = ANY (ARRAY[''applied''::text, ''reapplied_noop''::text, ''verified''::text])))'),
        ('provider_telemetry_migration_ledger','ck_ptml_schema_version','CHECK ((schema_version >= 1))'),
        ('provider_telemetry_migration_ledger','ck_ptml_sha','CHECK ((migration_sha256 ~ ''^[0-9a-f]{64}$''::text))'),
        ('provider_telemetry_migration_ledger','provider_telemetry_migration_ledger_pkey','PRIMARY KEY (ledger_id)'),
        ('provider_telemetry_migration_ledger','uq_ptml_sequence','UNIQUE (ledger_sequence)'),
        ('provider_telemetry_run','ck_ptr_entry_point','CHECK ((entry_point ~ ''^[a-z][a-z0-9_]{0,63}$''::text))'),
        ('provider_telemetry_run','ck_ptr_expected_phases_sane','CHECK (provider_telemetry_array_is_clean(expected_phases))'),
        ('provider_telemetry_run','ck_ptr_posture','CHECK ((posture = ANY (ARRAY[''observational''::text, ''strict''::text])))'),
        ('provider_telemetry_run','ck_ptr_runtime_fingerprint','CHECK ((runtime_fingerprint ~ ''^[0-9a-f]{64}$''::text))'),
        ('provider_telemetry_run','ck_ptr_schema_version','CHECK ((schema_version >= 1))'),
        ('provider_telemetry_run','ck_ptr_source_commit','CHECK ((source_commit ~ ''^[0-9a-zA-Z._-]{0,64}$''::text))'),
        ('provider_telemetry_run','ck_ptr_strict_requires_telemetry','CHECK (((posture <> ''strict''::text) OR telemetry_required))'),
        ('provider_telemetry_run','provider_telemetry_run_pkey','PRIMARY KEY (telemetry_run_id)'),
        ('provider_telemetry_run','uq_provider_telemetry_run_sequence','UNIQUE (run_sequence)'),
        ('provider_telemetry_run_event','ck_ptre_complete_binds_manifest','CHECK (((reconciliation_status <> ''complete''::text) OR (expected_work_digest ~ ''^[0-9a-f]{64}$''::text)))'),
        ('provider_telemetry_run_event','ck_ptre_digest_shape','CHECK (((expected_work_digest = ''''::text) OR (expected_work_digest ~ ''^[0-9a-f]{64}$''::text)))'),
        ('provider_telemetry_run_event','ck_ptre_complete_is_clean','CHECK (((reconciliation_status <> ''complete''::text) OR ((unmatched_starts = 0) AND (undurable_events = 0) AND (ambiguous_events = 0) AND (dropped_events = 0) AND (drain_status = ANY (ARRAY[''unknown''::text, ''drained''::text])))))'),
        ('provider_telemetry_run_event','ck_ptre_counts','CHECK (((started_events >= 0) AND (terminal_events >= 0) AND (unmatched_starts >= 0) AND (undurable_events >= 0) AND (ambiguous_events >= 0) AND (dropped_events >= 0) AND (expected_calls >= 0) AND (observed_calls >= 0)))'),
        ('provider_telemetry_run_event','ck_ptre_drain','CHECK ((drain_status = ANY (ARRAY[''unknown''::text, ''drained''::text, ''failed''::text, ''timeout''::text])))'),
        ('provider_telemetry_run_event','ck_ptre_kind','CHECK ((event_kind = ANY (ARRAY[''worker_registered''::text, ''worker_drained''::text, ''reconciliation''::text])))'),
        ('provider_telemetry_run_event','ck_ptre_posture','CHECK ((posture = ANY (ARRAY[''observational''::text, ''strict''::text])))'),
        ('provider_telemetry_run_event','ck_ptre_reconciliation','CHECK ((reconciliation_status = ANY (ARRAY[''pending''::text, ''complete''::text, ''incomplete''::text, ''uncertified''::text])))'),
        ('provider_telemetry_run_event','provider_telemetry_run_event_pkey','PRIMARY KEY (event_id)'),
        ('provider_telemetry_run_event','uq_ptre_sequence','UNIQUE (run_event_sequence)')
    ), actual(tbl, name, def) AS (
        SELECT c.relname::text, con.conname::text,
               replace(pg_catalog.pg_get_constraintdef(con.oid), v_schema || '.', '')::text
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = v_schema
          AND c.relname = ANY (ARRAY[
              'provider_telemetry_run', 'provider_telemetry_run_event',
              'provider_telemetry_call', 'provider_sdk_invocation',
              'provider_attempt', 'provider_attempt_event',
              'provider_telemetry_migration_ledger'])
    )
    SELECT string_agg(format('%s.%s(%s)', d.tbl, d.name, d.side), '; ' ORDER BY d.tbl, d.name)
      INTO v_problem
    FROM (
        SELECT tbl, name, 'missing_or_altered' AS side FROM (
            SELECT * FROM expected EXCEPT SELECT * FROM actual) AS m
        UNION ALL
        SELECT tbl, name, 'unexpected' FROM (
            SELECT * FROM actual EXCEPT SELECT * FROM expected) AS u
    ) AS d;
    IF v_problem IS NOT NULL THEN
        RAISE EXCEPTION 'v63 postflight: constraint contract violated: %', v_problem
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- ── 6j. Indexes, by exact definition ──
    -- Includes the indexed expressions and their order, and the partial-index
    -- predicate: an index re-created on the same columns in a different order,
    -- or without its WHERE clause, is a different index.
    WITH expected(tbl, name, def) AS (VALUES
        ('provider_attempt','idx_provider_attempt_invocation','CREATE INDEX idx_provider_attempt_invocation ON provider_attempt USING btree (invocation_id, http_retry_ordinal)'),
        ('provider_attempt','idx_provider_attempt_run','CREATE INDEX idx_provider_attempt_run ON provider_attempt USING btree (telemetry_run_id, request_started_at, attempt_sequence)'),
        ('provider_attempt','provider_attempt_pkey','CREATE UNIQUE INDEX provider_attempt_pkey ON provider_attempt USING btree (attempt_id)'),
        ('provider_attempt','uq_pa_invocation_ordinal','CREATE UNIQUE INDEX uq_pa_invocation_ordinal ON provider_attempt USING btree (invocation_id, http_retry_ordinal)'),
        ('provider_attempt','uq_pa_sequence','CREATE UNIQUE INDEX uq_pa_sequence ON provider_attempt USING btree (attempt_sequence)'),
        ('provider_attempt_event','idx_provider_attempt_event_subject','CREATE INDEX idx_provider_attempt_event_subject ON provider_attempt_event USING btree (subject_id, event_ordinal)'),
        ('provider_attempt_event','idx_provider_attempt_event_one_terminal','CREATE UNIQUE INDEX idx_provider_attempt_event_one_terminal ON provider_attempt_event USING btree (subject_id) WHERE is_terminal'),
        ('provider_attempt_event','idx_provider_attempt_event_terminal','CREATE INDEX idx_provider_attempt_event_terminal ON provider_attempt_event USING btree (telemetry_run_id, subject_kind, subject_id) WHERE is_terminal'),
        ('provider_attempt_event','provider_attempt_event_pkey','CREATE UNIQUE INDEX provider_attempt_event_pkey ON provider_attempt_event USING btree (event_id)'),
        ('provider_attempt_event','uq_pae_sequence','CREATE UNIQUE INDEX uq_pae_sequence ON provider_attempt_event USING btree (event_sequence)'),
        ('provider_attempt_event','uq_pae_subject_ordinal','CREATE UNIQUE INDEX uq_pae_subject_ordinal ON provider_attempt_event USING btree (subject_id, event_ordinal)'),
        ('provider_sdk_invocation','idx_provider_sdk_invocation_call','CREATE INDEX idx_provider_sdk_invocation_call ON provider_sdk_invocation USING btree (call_id, attempt_ordinal)'),
        ('provider_sdk_invocation','idx_provider_sdk_invocation_run','CREATE INDEX idx_provider_sdk_invocation_run ON provider_sdk_invocation USING btree (telemetry_run_id, started_at, invocation_sequence)'),
        ('provider_sdk_invocation','provider_sdk_invocation_pkey','CREATE UNIQUE INDEX provider_sdk_invocation_pkey ON provider_sdk_invocation USING btree (invocation_id)'),
        ('provider_sdk_invocation','uq_psi_call_attempt','CREATE UNIQUE INDEX uq_psi_call_attempt ON provider_sdk_invocation USING btree (call_id, attempt_ordinal)'),
        ('provider_sdk_invocation','uq_psi_sequence','CREATE UNIQUE INDEX uq_psi_sequence ON provider_sdk_invocation USING btree (invocation_sequence)'),
        ('provider_telemetry_call','idx_provider_telemetry_call_project','CREATE INDEX idx_provider_telemetry_call_project ON provider_telemetry_call USING btree (project_id, started_at, call_sequence)'),
        ('provider_telemetry_call','idx_provider_telemetry_call_run','CREATE INDEX idx_provider_telemetry_call_run ON provider_telemetry_call USING btree (telemetry_run_id, started_at, call_sequence)'),
        ('provider_telemetry_call','provider_telemetry_call_pkey','CREATE UNIQUE INDEX provider_telemetry_call_pkey ON provider_telemetry_call USING btree (call_id)'),
        ('provider_telemetry_call','uq_ptc_sequence','CREATE UNIQUE INDEX uq_ptc_sequence ON provider_telemetry_call USING btree (call_sequence)'),
        ('provider_telemetry_migration_ledger','provider_telemetry_migration_ledger_pkey','CREATE UNIQUE INDEX provider_telemetry_migration_ledger_pkey ON provider_telemetry_migration_ledger USING btree (ledger_id)'),
        ('provider_telemetry_migration_ledger','uq_ptml_sequence','CREATE UNIQUE INDEX uq_ptml_sequence ON provider_telemetry_migration_ledger USING btree (ledger_sequence)'),
        ('provider_telemetry_run','idx_provider_telemetry_run_project','CREATE INDEX idx_provider_telemetry_run_project ON provider_telemetry_run USING btree (project_id, started_at, run_sequence)'),
        ('provider_telemetry_run','idx_provider_telemetry_run_started','CREATE INDEX idx_provider_telemetry_run_started ON provider_telemetry_run USING btree (external_run_id, started_at, run_sequence)'),
        ('provider_telemetry_run','provider_telemetry_run_pkey','CREATE UNIQUE INDEX provider_telemetry_run_pkey ON provider_telemetry_run USING btree (telemetry_run_id)'),
        ('provider_telemetry_run','uq_provider_telemetry_run_sequence','CREATE UNIQUE INDEX uq_provider_telemetry_run_sequence ON provider_telemetry_run USING btree (run_sequence)'),
        ('provider_telemetry_run_event','idx_provider_telemetry_run_event_run','CREATE INDEX idx_provider_telemetry_run_event_run ON provider_telemetry_run_event USING btree (telemetry_run_id, observed_at, run_event_sequence)'),
        ('provider_telemetry_run_event','provider_telemetry_run_event_pkey','CREATE UNIQUE INDEX provider_telemetry_run_event_pkey ON provider_telemetry_run_event USING btree (event_id)'),
        ('provider_telemetry_run_event','uq_ptre_sequence','CREATE UNIQUE INDEX uq_ptre_sequence ON provider_telemetry_run_event USING btree (run_event_sequence)')
    ), actual(tbl, name, def) AS (
        SELECT tablename::text, indexname::text,
               replace(indexdef, v_schema || '.', '')::text
        FROM pg_indexes
        WHERE schemaname = v_schema
          AND tablename = ANY (ARRAY[
              'provider_telemetry_run', 'provider_telemetry_run_event',
              'provider_telemetry_call', 'provider_sdk_invocation',
              'provider_attempt', 'provider_attempt_event',
              'provider_telemetry_migration_ledger'])
    )
    SELECT string_agg(format('%s.%s(%s)', d.tbl, d.name, d.side), '; ' ORDER BY d.tbl, d.name)
      INTO v_problem
    FROM (
        SELECT tbl, name, 'missing_or_altered' AS side FROM (
            SELECT * FROM expected EXCEPT SELECT * FROM actual) AS m
        UNION ALL
        SELECT tbl, name, 'unexpected' FROM (
            SELECT * FROM actual EXCEPT SELECT * FROM expected) AS u
    ) AS d;
    IF v_problem IS NOT NULL THEN
        RAISE EXCEPTION 'v63 postflight: index contract violated: %', v_problem
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    -- ── 6k. Role memberships ──
    -- Neither the writer nor the reader may be a member of the owner: membership
    -- would hand them ownership authority and make every guarantee above
    -- bypassable from the runtime.
    SELECT string_agg(rolname, ', ' ORDER BY rolname) INTO v_problem
    FROM pg_catalog.pg_roles
    WHERE rolname IN ('workflow_provider_telemetry_writer',
                      'workflow_provider_telemetry_reader')
      AND pg_catalog.pg_has_role(rolname, 'workflow_provider_telemetry_owner', 'MEMBER');
    IF v_problem IS NOT NULL THEN
        RAISE EXCEPTION
            'v63 postflight: runtime roles must not be members of the owner role: %',
            v_problem
            USING ERRCODE = 'invalid_authorization_specification';
    END IF;
END
$postflight$;

COMMIT;
