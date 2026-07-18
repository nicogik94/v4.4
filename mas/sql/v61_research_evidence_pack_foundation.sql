-- v61 Canonical Research Evidence Pack Foundation (R2.0A-1)
-- Additive append-only context, annotation, and explicit usage authorization.
BEGIN;

DO $$
DECLARE
    v_parent_count integer;
    v_object_count integer;
BEGIN
    SELECT count(*) INTO v_parent_count
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = current_schema() AND c.relkind = 'r'
      AND c.relname = ANY (ARRAY[
        'projects', 'research_claim_draft', 'research_evidence_intake_item',
        'research_evidence_intake_item_review_decision',
        'research_evidence_claim_support_assessment'
      ]);
    IF v_parent_count <> 5 THEN
        RAISE EXCEPTION 'v61 requires the complete canonical research-evidence parent graph'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname=current_schema() AND p.proname='slicea_reject_mutation'
          AND p.pronargs=0 AND p.prorettype='trigger'::regtype
    ) THEN
        RAISE EXCEPTION 'v61 requires canonical append-only mutation guard'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;

    SELECT
      (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname=current_schema() AND c.relkind='r'
         AND c.relname=ANY(ARRAY[
           'research_evidence_project_context_revision',
           'research_evidence_project_context_sequence_allocator',
           'research_evidence_claim_annotation_revision',
           'research_evidence_claim_annotation_sequence_allocator',
           'research_evidence_usage_authorization_decision',
           'research_evidence_usage_authorization_sequence_allocator']))
      +
      (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
       WHERE n.nspname=current_schema() AND p.pronargs=0
         AND p.proname=ANY(ARRAY[
           'research_evidence_prepare_project_context_insert',
           'research_evidence_prepare_claim_annotation_insert',
           'research_evidence_prepare_usage_authorization_insert']))
      +
      (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
       WHERE n.nspname=current_schema() AND p.pronargs=3
         AND p.proname='research_evidence_pack_string_array_valid')
      +
      (SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
       JOIN pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname=current_schema() AND NOT t.tgisinternal
         AND t.tgname=ANY(ARRAY[
           'trg_repcr_prepare_insert','trg_repcr_no_mutation',
           'trg_recar_prepare_insert','trg_recar_no_mutation',
           'trg_reuad_prepare_insert','trg_reuad_no_mutation']))
      +
      (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname=current_schema() AND c.relkind='i'
         AND c.relname=ANY(ARRAY[
           'idx_repcr_project_sequence','idx_recar_project_claim_sequence',
           'idx_reuad_scope_sequence']))
    INTO v_object_count;

    IF v_object_count NOT IN (0, 19) THEN
        RAISE EXCEPTION 'v61 contract violation: partial/divergent evidence-pack foundation'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
    IF v_object_count = 19 AND (
        EXISTS (
          SELECT 1 FROM (VALUES
            ('trg_repcr_prepare_insert'::text,'research_evidence_project_context_revision'::text,7,'research_evidence_prepare_project_context_insert'::text),
            ('trg_repcr_no_mutation','research_evidence_project_context_revision',27,'slicea_reject_mutation'),
            ('trg_recar_prepare_insert','research_evidence_claim_annotation_revision',7,'research_evidence_prepare_claim_annotation_insert'),
            ('trg_recar_no_mutation','research_evidence_claim_annotation_revision',27,'slicea_reject_mutation'),
            ('trg_reuad_prepare_insert','research_evidence_usage_authorization_decision',7,'research_evidence_prepare_usage_authorization_insert'),
            ('trg_reuad_no_mutation','research_evidence_usage_authorization_decision',27,'slicea_reject_mutation')
          ) e(name,relation_name,trigger_type,function_name)
          WHERE NOT EXISTS (
            SELECT 1 FROM pg_trigger t
            JOIN pg_class c ON c.oid=t.tgrelid
            JOIN pg_proc p ON p.oid=t.tgfoid
            WHERE c.relnamespace=current_schema()::regnamespace
              AND c.relname=e.relation_name AND t.tgname=e.name
              AND p.pronamespace=current_schema()::regnamespace
              AND p.proname=e.function_name AND p.proargtypes=''::oidvector
              AND t.tgtype=e.trigger_type AND (t.tgtype & 30)=e.trigger_type-1
              AND t.tgenabled='A' AND NOT t.tgisinternal AND t.tgnargs=0
              AND octet_length(t.tgargs)=0 AND t.tgattr=''::int2vector
              AND t.tgqual IS NULL AND NOT t.tgdeferrable
              AND NOT t.tginitdeferred AND t.tgconstraint=0
          )
        ) OR EXISTS (
          SELECT 1 FROM pg_class c
          WHERE c.relnamespace=current_schema()::regnamespace
            AND c.relname=ANY(ARRAY[
              'research_evidence_project_context_revision',
              'research_evidence_project_context_sequence_allocator',
              'research_evidence_claim_annotation_revision',
              'research_evidence_claim_annotation_sequence_allocator',
              'research_evidence_usage_authorization_decision',
              'research_evidence_usage_authorization_sequence_allocator'])
            AND has_table_privilege('public',c.oid,
              'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
        ) OR EXISTS (
          SELECT 1 FROM pg_proc p
          WHERE p.pronamespace=current_schema()::regnamespace
            AND p.proname=ANY(ARRAY[
              'research_evidence_pack_string_array_valid',
              'research_evidence_prepare_project_context_insert',
              'research_evidence_prepare_claim_annotation_insert',
              'research_evidence_prepare_usage_authorization_insert'])
            AND has_function_privilege('public',p.oid,'EXECUTE')
        )
    ) THEN
        RAISE EXCEPTION 'v61 contract violation: divergent trigger identity or ACLs'
            USING ERRCODE = 'invalid_schema_definition';
    END IF;
END $$;

DO $$ BEGIN
IF NOT EXISTS (SELECT 1 FROM pg_proc WHERE pronamespace=current_schema()::regnamespace AND proname='research_evidence_pack_string_array_valid' AND pronargs=3) THEN
EXECUTE $create_function$ CREATE FUNCTION research_evidence_pack_string_array_valid(
    value jsonb, maximum_items integer, maximum_length integer
) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER SET search_path = pg_catalog
AS $function_body$
DECLARE
    v_count integer;
    v_distinct integer;
    v_lengths_valid boolean;
    v_trim_chars constant text :=
        pg_catalog.chr(9) || pg_catalog.chr(10) ||
        pg_catalog.chr(11) || pg_catalog.chr(12) ||
        pg_catalog.chr(13) || pg_catalog.chr(28) ||
        pg_catalog.chr(29) || pg_catalog.chr(30) ||
        pg_catalog.chr(31) || pg_catalog.chr(32) ||
        pg_catalog.chr(133) || pg_catalog.chr(160) ||
        pg_catalog.chr(5760) || pg_catalog.chr(8192) ||
        pg_catalog.chr(8193) || pg_catalog.chr(8194) ||
        pg_catalog.chr(8195) || pg_catalog.chr(8196) ||
        pg_catalog.chr(8197) || pg_catalog.chr(8198) ||
        pg_catalog.chr(8199) || pg_catalog.chr(8200) ||
        pg_catalog.chr(8201) || pg_catalog.chr(8202) ||
        pg_catalog.chr(8232) || pg_catalog.chr(8233) ||
        pg_catalog.chr(8239) || pg_catalog.chr(8287) ||
        pg_catalog.chr(12288);
BEGIN
    IF value IS NULL OR jsonb_typeof(value) <> 'array'
       OR maximum_items < 0 OR maximum_length < 1
       OR jsonb_array_length(value) > maximum_items THEN
        RETURN false;
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.jsonb_array_elements(value) AS element(member)
        WHERE pg_catalog.jsonb_typeof(member) <> 'string'
    ) THEN
        RETURN false;
    END IF;
    SELECT count(*), count(DISTINCT normalized_item),
           coalesce(bool_and(
               pg_catalog.char_length(normalized_item)
                   BETWEEN 1 AND maximum_length
           ), true)
      INTO v_count, v_distinct, v_lengths_valid
    FROM (
        SELECT pg_catalog.btrim(member #>> '{}', v_trim_chars)
                   AS normalized_item
        FROM pg_catalog.jsonb_array_elements(value) AS element(member)
    ) normalized;
    RETURN v_count = pg_catalog.jsonb_array_length(value)
       AND v_count = v_distinct
       AND v_lengths_valid;
EXCEPTION WHEN OTHERS THEN RETURN false;
END;
$function_body$ $create_function$;
END IF; END $$;
REVOKE ALL ON FUNCTION research_evidence_pack_string_array_valid(jsonb, integer, integer) FROM PUBLIC;

CREATE TABLE IF NOT EXISTS research_evidence_project_context_revision (
    id UUID NOT NULL,
    project_id UUID NOT NULL,
    request_id TEXT NOT NULL,
    research_question TEXT NOT NULL,
    project_limitations_json JSONB NOT NULL,
    unresolved_gaps_json JSONB NOT NULL,
    actor TEXT NOT NULL,
    context_sequence INTEGER NOT NULL,
    supersedes_context_revision_id UUID,
    recorded_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT research_evidence_project_context_revision_pkey PRIMARY KEY (id),
    CONSTRAINT uq_repcr_id_project UNIQUE (id, project_id),
    CONSTRAINT uq_repcr_project_sequence UNIQUE (project_id, context_sequence),
    CONSTRAINT uq_repcr_project_request UNIQUE (project_id, request_id),
    CONSTRAINT uq_repcr_supersedes_once UNIQUE (supersedes_context_revision_id),
    CONSTRAINT fk_repcr_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
    CONSTRAINT fk_repcr_supersedes_same_project
      FOREIGN KEY (supersedes_context_revision_id, project_id)
      REFERENCES research_evidence_project_context_revision(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT ck_repcr_sequence_positive CHECK (context_sequence >= 1),
    CONSTRAINT ck_repcr_request CHECK (request_id=btrim(request_id) AND char_length(request_id) BETWEEN 1 AND 128),
    CONSTRAINT ck_repcr_question CHECK (research_question=btrim(research_question) AND char_length(research_question) BETWEEN 1 AND 2000),
    CONSTRAINT ck_repcr_actor CHECK (actor=btrim(actor) AND char_length(actor) BETWEEN 1 AND 200),
    CONSTRAINT ck_repcr_limitations CHECK (research_evidence_pack_string_array_valid(project_limitations_json, 10, 500)),
    CONSTRAINT ck_repcr_gaps CHECK (research_evidence_pack_string_array_valid(unresolved_gaps_json, 10, 500))
);

CREATE TABLE IF NOT EXISTS research_evidence_project_context_sequence_allocator (
    project_id UUID NOT NULL PRIMARY KEY,
    last_sequence INTEGER NOT NULL,
    CONSTRAINT fk_repcsa_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
    CONSTRAINT ck_repcsa_last_sequence CHECK (last_sequence >= 0)
);

CREATE TABLE IF NOT EXISTS research_evidence_claim_annotation_revision (
    id UUID NOT NULL,
    project_id UUID NOT NULL,
    claim_draft_id UUID NOT NULL,
    request_id TEXT NOT NULL,
    epistemic_status TEXT NOT NULL,
    confidence_label TEXT NOT NULL,
    decision_relevance TEXT NOT NULL,
    supports_statement TEXT NOT NULL,
    does_not_prove TEXT NOT NULL,
    limitations_json JSONB NOT NULL,
    related_claim_draft_ids_json JSONB NOT NULL,
    operator_notes TEXT,
    explicit_probability_value NUMERIC(7,6),
    explicit_probability_provided_by TEXT,
    explicit_probability_provenance_reference TEXT,
    explicit_probability_provenance_note TEXT,
    actor TEXT NOT NULL,
    annotation_sequence INTEGER NOT NULL,
    supersedes_annotation_revision_id UUID,
    recorded_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT research_evidence_claim_annotation_revision_pkey PRIMARY KEY (id),
    CONSTRAINT uq_recar_id_project_claim UNIQUE (id, project_id, claim_draft_id),
    CONSTRAINT uq_recar_claim_sequence UNIQUE (project_id, claim_draft_id, annotation_sequence),
    CONSTRAINT uq_recar_claim_request UNIQUE (project_id, claim_draft_id, request_id),
    CONSTRAINT uq_recar_supersedes_once UNIQUE (supersedes_annotation_revision_id),
    CONSTRAINT fk_recar_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
    CONSTRAINT fk_recar_claim_project FOREIGN KEY (claim_draft_id, project_id)
      REFERENCES research_claim_draft(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT fk_recar_supersedes_same_claim
      FOREIGN KEY (supersedes_annotation_revision_id, project_id, claim_draft_id)
      REFERENCES research_evidence_claim_annotation_revision(id, project_id, claim_draft_id) ON DELETE RESTRICT,
    CONSTRAINT ck_recar_sequence_positive CHECK (annotation_sequence >= 1),
    CONSTRAINT ck_recar_request CHECK (request_id=btrim(request_id) AND char_length(request_id) BETWEEN 1 AND 128),
    CONSTRAINT ck_recar_epistemic CHECK (epistemic_status IN ('reported_fact','observation','estimate','inference','assumption','hypothesis')),
    CONSTRAINT ck_recar_confidence CHECK (confidence_label IN ('high','medium','low','unknown')),
    CONSTRAINT ck_recar_relevance CHECK (decision_relevance=btrim(decision_relevance) AND char_length(decision_relevance) BETWEEN 1 AND 1000),
    CONSTRAINT ck_recar_supports CHECK (supports_statement=btrim(supports_statement) AND char_length(supports_statement) BETWEEN 1 AND 2000),
    CONSTRAINT ck_recar_does_not_prove CHECK (does_not_prove=btrim(does_not_prove) AND char_length(does_not_prove) BETWEEN 1 AND 2000),
    CONSTRAINT ck_recar_limitations CHECK (research_evidence_pack_string_array_valid(limitations_json, 10, 500)),
    CONSTRAINT ck_recar_related_array CHECK (jsonb_typeof(related_claim_draft_ids_json)='array' AND jsonb_array_length(related_claim_draft_ids_json) <= 20),
    CONSTRAINT ck_recar_notes CHECK (operator_notes IS NULL OR (operator_notes=btrim(operator_notes) AND char_length(operator_notes) BETWEEN 1 AND 2000)),
    CONSTRAINT ck_recar_actor CHECK (actor=btrim(actor) AND char_length(actor) BETWEEN 1 AND 200),
    CONSTRAINT ck_recar_probability_shape CHECK (
      (explicit_probability_value IS NULL AND explicit_probability_provided_by IS NULL
       AND explicit_probability_provenance_reference IS NULL AND explicit_probability_provenance_note IS NULL)
      OR
      (explicit_probability_value IS NOT NULL
       AND explicit_probability_provided_by IS NOT NULL
       AND explicit_probability_provenance_reference IS NOT NULL
       AND explicit_probability_provenance_note IS NOT NULL
       AND explicit_probability_value BETWEEN 0 AND 1
       AND explicit_probability_provided_by IN ('source','operator')
       AND explicit_probability_provenance_reference=btrim(explicit_probability_provenance_reference)
       AND explicit_probability_provenance_note=btrim(explicit_probability_provenance_note)
       AND char_length(explicit_probability_provenance_reference) BETWEEN 1 AND 500
       AND char_length(explicit_probability_provenance_note) BETWEEN 1 AND 1000)
    )
);

CREATE TABLE IF NOT EXISTS research_evidence_claim_annotation_sequence_allocator (
    project_id UUID NOT NULL,
    claim_draft_id UUID NOT NULL,
    last_sequence INTEGER NOT NULL,
    CONSTRAINT research_evidence_claim_annotation_sequence_allocator_pkey PRIMARY KEY (project_id, claim_draft_id),
    CONSTRAINT fk_recasa_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
    CONSTRAINT fk_recasa_claim FOREIGN KEY (claim_draft_id, project_id) REFERENCES research_claim_draft(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT ck_recasa_last_sequence CHECK (last_sequence >= 0)
);

CREATE TABLE IF NOT EXISTS research_evidence_usage_authorization_decision (
    id UUID NOT NULL,
    project_id UUID NOT NULL,
    claim_intake_item_id UUID NOT NULL,
    evidence_intake_item_id UUID NOT NULL,
    claim_support_assessment_id UUID NOT NULL,
    usage_scope TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL,
    actor TEXT NOT NULL,
    request_id TEXT NOT NULL,
    claim_draft_id UUID NOT NULL,
    claim_annotation_revision_id UUID NOT NULL,
    claim_review_decision_id UUID NOT NULL,
    evidence_review_decision_id UUID NOT NULL,
    decision_sequence INTEGER NOT NULL,
    supersedes_decision_id UUID,
    recorded_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT research_evidence_usage_authorization_decision_pkey PRIMARY KEY (id),
    CONSTRAINT uq_reuad_id_project_scope UNIQUE (id, project_id, claim_intake_item_id, evidence_intake_item_id, usage_scope),
    CONSTRAINT uq_reuad_scope_sequence UNIQUE (project_id, claim_intake_item_id, evidence_intake_item_id, usage_scope, decision_sequence),
    CONSTRAINT uq_reuad_scope_request UNIQUE (project_id, claim_intake_item_id, evidence_intake_item_id, usage_scope, request_id),
    CONSTRAINT uq_reuad_supersedes_once UNIQUE (supersedes_decision_id),
    CONSTRAINT fk_reuad_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
    CONSTRAINT fk_reuad_claim_item FOREIGN KEY (claim_intake_item_id, project_id) REFERENCES research_evidence_intake_item(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT fk_reuad_evidence_item FOREIGN KEY (evidence_intake_item_id, project_id) REFERENCES research_evidence_intake_item(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT fk_reuad_assessment_exact FOREIGN KEY (claim_support_assessment_id, project_id, claim_intake_item_id, evidence_intake_item_id)
      REFERENCES research_evidence_claim_support_assessment(id, project_id, claim_intake_item_id, evidence_intake_item_id) ON DELETE RESTRICT,
    CONSTRAINT fk_reuad_claim_draft FOREIGN KEY (claim_draft_id, project_id) REFERENCES research_claim_draft(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT fk_reuad_annotation_exact FOREIGN KEY (claim_annotation_revision_id, project_id, claim_draft_id)
      REFERENCES research_evidence_claim_annotation_revision(id, project_id, claim_draft_id) ON DELETE RESTRICT,
    CONSTRAINT fk_reuad_claim_review_exact FOREIGN KEY (claim_review_decision_id, project_id, claim_intake_item_id)
      REFERENCES research_evidence_intake_item_review_decision(id, project_id, research_evidence_intake_item_id) ON DELETE RESTRICT,
    CONSTRAINT fk_reuad_evidence_review_exact FOREIGN KEY (evidence_review_decision_id, project_id, evidence_intake_item_id)
      REFERENCES research_evidence_intake_item_review_decision(id, project_id, research_evidence_intake_item_id) ON DELETE RESTRICT,
    CONSTRAINT fk_reuad_supersedes_same_scope FOREIGN KEY (supersedes_decision_id, project_id, claim_intake_item_id, evidence_intake_item_id, usage_scope)
      REFERENCES research_evidence_usage_authorization_decision(id, project_id, claim_intake_item_id, evidence_intake_item_id, usage_scope) ON DELETE RESTRICT,
    CONSTRAINT ck_reuad_scope CHECK (usage_scope IN ('internal_analysis','operator_dossier','client_report')),
    CONSTRAINT ck_reuad_decision CHECK (decision IN ('authorized','revoked')),
    CONSTRAINT ck_reuad_sequence_positive CHECK (decision_sequence >= 1),
    CONSTRAINT ck_reuad_reason CHECK (reason=btrim(reason) AND char_length(reason) BETWEEN 1 AND 1000),
    CONSTRAINT ck_reuad_actor CHECK (actor=btrim(actor) AND char_length(actor) BETWEEN 1 AND 200),
    CONSTRAINT ck_reuad_request CHECK (request_id=btrim(request_id) AND char_length(request_id) BETWEEN 1 AND 128)
);

CREATE TABLE IF NOT EXISTS research_evidence_usage_authorization_sequence_allocator (
    project_id UUID NOT NULL,
    claim_intake_item_id UUID NOT NULL,
    evidence_intake_item_id UUID NOT NULL,
    usage_scope TEXT NOT NULL,
    last_sequence INTEGER NOT NULL,
    CONSTRAINT research_evidence_usage_authorization_sequence_allocator_pkey PRIMARY KEY (project_id, claim_intake_item_id, evidence_intake_item_id, usage_scope),
    CONSTRAINT fk_reuasa_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
    CONSTRAINT fk_reuasa_claim_item FOREIGN KEY (claim_intake_item_id, project_id) REFERENCES research_evidence_intake_item(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT fk_reuasa_evidence_item FOREIGN KEY (evidence_intake_item_id, project_id) REFERENCES research_evidence_intake_item(id, project_id) ON DELETE RESTRICT,
    CONSTRAINT ck_reuasa_scope CHECK (usage_scope IN ('internal_analysis','operator_dossier','client_report')),
    CONSTRAINT ck_reuasa_last_sequence CHECK (last_sequence >= 0)
);

CREATE INDEX IF NOT EXISTS idx_repcr_project_sequence ON research_evidence_project_context_revision(project_id, context_sequence DESC);
CREATE INDEX IF NOT EXISTS idx_recar_project_claim_sequence ON research_evidence_claim_annotation_revision(project_id, claim_draft_id, annotation_sequence DESC);
CREATE INDEX IF NOT EXISTS idx_reuad_scope_sequence ON research_evidence_usage_authorization_decision(project_id, claim_intake_item_id, evidence_intake_item_id, usage_scope, decision_sequence DESC);

DO $$ BEGIN
IF NOT EXISTS (SELECT 1 FROM pg_proc WHERE pronamespace=current_schema()::regnamespace AND proname='research_evidence_prepare_project_context_insert' AND pronargs=0) THEN
EXECUTE $create_function$ CREATE FUNCTION research_evidence_prepare_project_context_insert()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $function_body$
DECLARE
    v_last integer;
    v_count integer;
    v_current uuid;
    v_valid boolean;
    v_trim_chars constant text :=
        pg_catalog.chr(9) || pg_catalog.chr(10) ||
        pg_catalog.chr(11) || pg_catalog.chr(12) ||
        pg_catalog.chr(13) || pg_catalog.chr(28) ||
        pg_catalog.chr(29) || pg_catalog.chr(30) ||
        pg_catalog.chr(31) || pg_catalog.chr(32) ||
        pg_catalog.chr(133) || pg_catalog.chr(160) ||
        pg_catalog.chr(5760) || pg_catalog.chr(8192) ||
        pg_catalog.chr(8193) || pg_catalog.chr(8194) ||
        pg_catalog.chr(8195) || pg_catalog.chr(8196) ||
        pg_catalog.chr(8197) || pg_catalog.chr(8198) ||
        pg_catalog.chr(8199) || pg_catalog.chr(8200) ||
        pg_catalog.chr(8201) || pg_catalog.chr(8202) ||
        pg_catalog.chr(8232) || pg_catalog.chr(8233) ||
        pg_catalog.chr(8239) || pg_catalog.chr(8287) ||
        pg_catalog.chr(12288);
BEGIN
    IF NEW.project_limitations_json IS NULL
       OR NEW.unresolved_gaps_json IS NULL THEN
      RAISE EXCEPTION 'project context collections reject SQL NULL'
        USING ERRCODE='23514';
    END IF;
    NEW.id := gen_random_uuid();
    IF NEW.context_sequence IS NOT NULL OR NEW.supersedes_context_revision_id IS NOT NULL OR NEW.recorded_at IS NOT NULL THEN
      RAISE EXCEPTION 'context sequence, predecessor and timestamp are server-owned' USING ERRCODE='23514'; END IF;
    IF pg_catalog.jsonb_typeof(NEW.project_limitations_json) <> 'array'
       OR pg_catalog.jsonb_typeof(NEW.unresolved_gaps_json) <> 'array'
       OR EXISTS (
         SELECT 1 FROM pg_catalog.jsonb_array_elements(
           NEW.project_limitations_json
         ) element(member)
         WHERE pg_catalog.jsonb_typeof(member) <> 'string'
       )
       OR EXISTS (
         SELECT 1 FROM pg_catalog.jsonb_array_elements(
           NEW.unresolved_gaps_json
         ) element(member)
         WHERE pg_catalog.jsonb_typeof(member) <> 'string'
       ) THEN
      RAISE EXCEPTION 'project context collections must contain only strings'
        USING ERRCODE='23514';
    END IF;
    SELECT coalesce(
             pg_catalog.jsonb_agg(
               pg_catalog.to_jsonb(pg_catalog.btrim(member #>> '{}',v_trim_chars))
               ORDER BY ordinality
             ),
             '[]'::jsonb
           )
      INTO NEW.project_limitations_json
      FROM pg_catalog.jsonb_array_elements(NEW.project_limitations_json)
           WITH ORDINALITY element(member,ordinality);
    SELECT coalesce(
             pg_catalog.jsonb_agg(
               pg_catalog.to_jsonb(pg_catalog.btrim(member #>> '{}',v_trim_chars))
               ORDER BY ordinality
             ),
             '[]'::jsonb
           )
      INTO NEW.unresolved_gaps_json
      FROM pg_catalog.jsonb_array_elements(NEW.unresolved_gaps_json)
           WITH ORDINALITY element(member,ordinality);
    EXECUTE format(
      'SELECT %I.research_evidence_pack_string_array_valid($1,10,500)
          AND %I.research_evidence_pack_string_array_valid($2,10,500)',
      TG_TABLE_SCHEMA,TG_TABLE_SCHEMA)
      INTO v_valid
      USING NEW.project_limitations_json,NEW.unresolved_gaps_json;
    IF NOT coalesce(v_valid,false) THEN
      RAISE EXCEPTION 'project context collections are invalid'
        USING ERRCODE='23514';
    END IF;
    EXECUTE format('INSERT INTO %I.research_evidence_project_context_sequence_allocator(project_id,last_sequence) VALUES($1,0) ON CONFLICT(project_id) DO NOTHING', TG_TABLE_SCHEMA) USING NEW.project_id;
    EXECUTE format('SELECT last_sequence FROM %I.research_evidence_project_context_sequence_allocator WHERE project_id=$1 FOR UPDATE', TG_TABLE_SCHEMA) INTO v_last USING NEW.project_id;
    EXECUTE format('SELECT count(*)::integer FROM %I.research_evidence_project_context_revision WHERE project_id=$1', TG_TABLE_SCHEMA) INTO v_count USING NEW.project_id;
    IF v_last > 0 THEN EXECUTE format('SELECT id FROM %I.research_evidence_project_context_revision WHERE project_id=$1 AND context_sequence=$2', TG_TABLE_SCHEMA) INTO v_current USING NEW.project_id,v_last; END IF;
    IF v_count <> v_last OR (v_last > 0 AND v_current IS NULL) THEN RAISE EXCEPTION 'malformed project-context chain' USING ERRCODE='23514'; END IF;
    NEW.context_sequence := v_last+1; NEW.supersedes_context_revision_id := v_current; NEW.recorded_at := clock_timestamp();
    EXECUTE format('UPDATE %I.research_evidence_project_context_sequence_allocator SET last_sequence=$2 WHERE project_id=$1', TG_TABLE_SCHEMA) USING NEW.project_id,NEW.context_sequence;
    RETURN NEW;
END; $function_body$ $create_function$;
END IF; END $$;

DO $$ BEGIN
IF NOT EXISTS (SELECT 1 FROM pg_proc WHERE pronamespace=current_schema()::regnamespace AND proname='research_evidence_prepare_claim_annotation_insert' AND pronargs=0) THEN
EXECUTE $create_function$ CREATE FUNCTION research_evidence_prepare_claim_annotation_insert()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $function_body$
DECLARE
    v_last integer;
    v_count integer;
    v_current uuid;
    v_valid boolean;
    v_trim_chars constant text :=
        pg_catalog.chr(9) || pg_catalog.chr(10) ||
        pg_catalog.chr(11) || pg_catalog.chr(12) ||
        pg_catalog.chr(13) || pg_catalog.chr(28) ||
        pg_catalog.chr(29) || pg_catalog.chr(30) ||
        pg_catalog.chr(31) || pg_catalog.chr(32) ||
        pg_catalog.chr(133) || pg_catalog.chr(160) ||
        pg_catalog.chr(5760) || pg_catalog.chr(8192) ||
        pg_catalog.chr(8193) || pg_catalog.chr(8194) ||
        pg_catalog.chr(8195) || pg_catalog.chr(8196) ||
        pg_catalog.chr(8197) || pg_catalog.chr(8198) ||
        pg_catalog.chr(8199) || pg_catalog.chr(8200) ||
        pg_catalog.chr(8201) || pg_catalog.chr(8202) ||
        pg_catalog.chr(8232) || pg_catalog.chr(8233) ||
        pg_catalog.chr(8239) || pg_catalog.chr(8287) ||
        pg_catalog.chr(12288);
BEGIN
    IF NEW.limitations_json IS NULL
       OR NEW.related_claim_draft_ids_json IS NULL THEN
      RAISE EXCEPTION 'claim annotation collections reject SQL NULL'
        USING ERRCODE='23514';
    END IF;
    NEW.id := gen_random_uuid();
    IF NEW.annotation_sequence IS NOT NULL OR NEW.supersedes_annotation_revision_id IS NOT NULL OR NEW.recorded_at IS NOT NULL THEN
      RAISE EXCEPTION 'annotation sequence, predecessor and timestamp are server-owned' USING ERRCODE='23514'; END IF;
    IF pg_catalog.jsonb_typeof(NEW.limitations_json) <> 'array'
       OR EXISTS (
         SELECT 1 FROM pg_catalog.jsonb_array_elements(NEW.limitations_json)
              element(member)
         WHERE pg_catalog.jsonb_typeof(member) <> 'string'
       ) THEN
      RAISE EXCEPTION 'annotation limitations must contain only strings'
        USING ERRCODE='23514';
    END IF;
    SELECT coalesce(
             pg_catalog.jsonb_agg(
               pg_catalog.to_jsonb(pg_catalog.btrim(member #>> '{}',v_trim_chars))
               ORDER BY ordinality
             ),
             '[]'::jsonb
           )
      INTO NEW.limitations_json
      FROM pg_catalog.jsonb_array_elements(NEW.limitations_json)
           WITH ORDINALITY element(member,ordinality);
    EXECUTE format(
      'SELECT %I.research_evidence_pack_string_array_valid($1,10,500)',
      TG_TABLE_SCHEMA)
      INTO v_valid USING NEW.limitations_json;
    IF NOT coalesce(v_valid,false) THEN
      RAISE EXCEPTION 'annotation limitations are invalid' USING ERRCODE='23514';
    END IF;
    IF pg_catalog.jsonb_typeof(NEW.related_claim_draft_ids_json) <> 'array'
       OR EXISTS (
         SELECT 1
         FROM pg_catalog.jsonb_array_elements(
           NEW.related_claim_draft_ids_json
         ) element(member)
         WHERE pg_catalog.jsonb_typeof(member) <> 'string'
       ) THEN
      RAISE EXCEPTION 'related claims must contain only UUID strings'
        USING ERRCODE='23503';
    END IF;
    BEGIN
      SELECT coalesce(
               pg_catalog.jsonb_agg(
                 pg_catalog.to_jsonb(
                   (pg_catalog.btrim(member #>> '{}',v_trim_chars)::uuid)::text
                 ) ORDER BY ordinality
               ),
               '[]'::jsonb
             )
        INTO NEW.related_claim_draft_ids_json
        FROM pg_catalog.jsonb_array_elements(
               NEW.related_claim_draft_ids_json
             ) WITH ORDINALITY element(member,ordinality);
    EXCEPTION WHEN invalid_text_representation THEN
      RAISE EXCEPTION 'related claims must contain only UUID strings'
        USING ERRCODE='23503';
    END;
    EXECUTE format(
      'SELECT count(*) = jsonb_array_length($1) AND count(*) = count(DISTINCT claim_id)
       FROM (SELECT value::uuid claim_id FROM pg_catalog.jsonb_array_elements_text($1)) ids
       JOIN %I.research_claim_draft c ON c.id=ids.claim_id AND c.project_id=$2
       WHERE ids.claim_id <> $3', TG_TABLE_SCHEMA)
      INTO v_valid USING NEW.related_claim_draft_ids_json,NEW.project_id,NEW.claim_draft_id;
    IF NOT coalesce(v_valid,false) THEN RAISE EXCEPTION 'related claims must be distinct same-project canonical claims and exclude self' USING ERRCODE='23503'; END IF;
    EXECUTE format('INSERT INTO %I.research_evidence_claim_annotation_sequence_allocator(project_id,claim_draft_id,last_sequence) VALUES($1,$2,0) ON CONFLICT(project_id,claim_draft_id) DO NOTHING', TG_TABLE_SCHEMA) USING NEW.project_id,NEW.claim_draft_id;
    EXECUTE format('SELECT last_sequence FROM %I.research_evidence_claim_annotation_sequence_allocator WHERE project_id=$1 AND claim_draft_id=$2 FOR UPDATE', TG_TABLE_SCHEMA) INTO v_last USING NEW.project_id,NEW.claim_draft_id;
    EXECUTE format('SELECT count(*)::integer FROM %I.research_evidence_claim_annotation_revision WHERE project_id=$1 AND claim_draft_id=$2', TG_TABLE_SCHEMA) INTO v_count USING NEW.project_id,NEW.claim_draft_id;
    IF v_last > 0 THEN EXECUTE format('SELECT id FROM %I.research_evidence_claim_annotation_revision WHERE project_id=$1 AND claim_draft_id=$2 AND annotation_sequence=$3', TG_TABLE_SCHEMA) INTO v_current USING NEW.project_id,NEW.claim_draft_id,v_last; END IF;
    IF v_count <> v_last OR (v_last > 0 AND v_current IS NULL) THEN RAISE EXCEPTION 'malformed claim-annotation chain' USING ERRCODE='23514'; END IF;
    NEW.annotation_sequence:=v_last+1; NEW.supersedes_annotation_revision_id:=v_current; NEW.recorded_at:=clock_timestamp();
    EXECUTE format('UPDATE %I.research_evidence_claim_annotation_sequence_allocator SET last_sequence=$3 WHERE project_id=$1 AND claim_draft_id=$2', TG_TABLE_SCHEMA) USING NEW.project_id,NEW.claim_draft_id,NEW.annotation_sequence;
    RETURN NEW;
END; $function_body$ $create_function$;
END IF; END $$;

DO $$ BEGIN
IF NOT EXISTS (SELECT 1 FROM pg_proc WHERE pronamespace=current_schema()::regnamespace AND proname='research_evidence_prepare_usage_authorization_insert' AND pronargs=0) THEN
EXECUTE $create_function$ CREATE FUNCTION research_evidence_prepare_usage_authorization_insert()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $function_body$
DECLARE v_last integer; v_count integer; v_previous_id uuid; v_previous_decision text; v_previous_assessment uuid; v_previous_annotation uuid; v_previous_claim_review uuid; v_previous_evidence_review uuid; v_claim uuid; v_annotation uuid; v_assessment uuid; v_locator text; v_linkage text; v_semantic text; v_claim_review uuid; v_claim_review_type text; v_evidence_review uuid; v_evidence_review_type text;
BEGIN
    NEW.id := gen_random_uuid();
    IF NEW.claim_support_assessment_id IS NOT NULL OR NEW.claim_draft_id IS NOT NULL OR NEW.claim_annotation_revision_id IS NOT NULL
       OR NEW.claim_review_decision_id IS NOT NULL OR NEW.evidence_review_decision_id IS NOT NULL
       OR NEW.decision_sequence IS NOT NULL OR NEW.supersedes_decision_id IS NOT NULL OR NEW.recorded_at IS NOT NULL THEN
      RAISE EXCEPTION 'authorization basis, sequence, predecessor and timestamp are server-owned' USING ERRCODE='23514'; END IF;
    EXECUTE format('SELECT claim_draft_id FROM %I.research_evidence_intake_item WHERE id=$1 AND project_id=$2 AND item_kind=''claim_draft''', TG_TABLE_SCHEMA) INTO v_claim USING NEW.claim_intake_item_id,NEW.project_id;
    IF v_claim IS NULL THEN
      RAISE EXCEPTION 'claim intake item not found for project' USING ERRCODE='23503'; END IF;
    EXECUTE format('SELECT 1 FROM %I.research_evidence_intake_item WHERE id=$1 AND project_id=$2 AND item_kind=''candidate_fact''', TG_TABLE_SCHEMA) INTO v_count USING NEW.evidence_intake_item_id,NEW.project_id;
    IF v_count IS NULL THEN RAISE EXCEPTION 'evidence intake item not found for project' USING ERRCODE='23503'; END IF;
    EXECUTE format('INSERT INTO %I.research_evidence_usage_authorization_sequence_allocator(project_id,claim_intake_item_id,evidence_intake_item_id,usage_scope,last_sequence) VALUES($1,$2,$3,$4,0) ON CONFLICT(project_id,claim_intake_item_id,evidence_intake_item_id,usage_scope) DO NOTHING', TG_TABLE_SCHEMA) USING NEW.project_id,NEW.claim_intake_item_id,NEW.evidence_intake_item_id,NEW.usage_scope;
    EXECUTE format('SELECT last_sequence FROM %I.research_evidence_usage_authorization_sequence_allocator WHERE project_id=$1 AND claim_intake_item_id=$2 AND evidence_intake_item_id=$3 AND usage_scope=$4 FOR UPDATE', TG_TABLE_SCHEMA) INTO v_last USING NEW.project_id,NEW.claim_intake_item_id,NEW.evidence_intake_item_id,NEW.usage_scope;
    IF v_last > 0 THEN EXECUTE format('SELECT id,decision,claim_support_assessment_id,claim_annotation_revision_id,claim_review_decision_id,evidence_review_decision_id FROM %I.research_evidence_usage_authorization_decision WHERE project_id=$1 AND claim_intake_item_id=$2 AND evidence_intake_item_id=$3 AND usage_scope=$4 AND decision_sequence=$5',TG_TABLE_SCHEMA) INTO v_previous_id,v_previous_decision,v_previous_assessment,v_previous_annotation,v_previous_claim_review,v_previous_evidence_review USING NEW.project_id,NEW.claim_intake_item_id,NEW.evidence_intake_item_id,NEW.usage_scope,v_last; END IF;
    IF v_last=0 AND NEW.decision<>'authorized' THEN RAISE EXCEPTION 'first usage decision must be authorized' USING ERRCODE='23514'; END IF;
    IF v_last>0 AND v_previous_id IS NULL THEN RAISE EXCEPTION 'malformed usage-authorization chain' USING ERRCODE='23514'; END IF;
    IF v_last>0 AND v_previous_decision=NEW.decision THEN RAISE EXCEPTION 'usage decisions must alternate authorization and revocation' USING ERRCODE='23514'; END IF;
    IF NEW.decision='authorized' THEN
      EXECUTE format('SELECT id FROM %I.research_evidence_claim_annotation_revision WHERE project_id=$1 AND claim_draft_id=$2 ORDER BY annotation_sequence DESC LIMIT 1',TG_TABLE_SCHEMA) INTO v_annotation USING NEW.project_id,v_claim;
      EXECUTE format('SELECT id,locator_resolution,evidence_linkage,semantic_relationship FROM %I.research_evidence_claim_support_assessment WHERE project_id=$1 AND claim_intake_item_id=$2 AND evidence_intake_item_id=$3 ORDER BY assessment_sequence DESC LIMIT 1',TG_TABLE_SCHEMA) INTO v_assessment,v_locator,v_linkage,v_semantic USING NEW.project_id,NEW.claim_intake_item_id,NEW.evidence_intake_item_id;
      EXECUTE format('SELECT id,decision_type FROM %I.research_evidence_intake_item_review_decision WHERE project_id=$1 AND research_evidence_intake_item_id=$2 ORDER BY decision_sequence DESC LIMIT 1',TG_TABLE_SCHEMA) INTO v_claim_review,v_claim_review_type USING NEW.project_id,NEW.claim_intake_item_id;
      EXECUTE format('SELECT id,decision_type FROM %I.research_evidence_intake_item_review_decision WHERE project_id=$1 AND research_evidence_intake_item_id=$2 ORDER BY decision_sequence DESC LIMIT 1',TG_TABLE_SCHEMA) INTO v_evidence_review,v_evidence_review_type USING NEW.project_id,NEW.evidence_intake_item_id;
      IF v_annotation IS NULL OR v_assessment IS NULL OR v_locator<>'resolvable' OR v_linkage<>'linked' OR v_semantic NOT IN ('support','qualification') OR v_claim_review_type<>'approved' OR v_evidence_review_type<>'approved' THEN RAISE EXCEPTION 'positive authorization requires current annotation, support and approvals' USING ERRCODE='23514'; END IF;
      NEW.claim_support_assessment_id:=v_assessment; NEW.claim_annotation_revision_id:=v_annotation; NEW.claim_review_decision_id:=v_claim_review; NEW.evidence_review_decision_id:=v_evidence_review;
    ELSE
      NEW.claim_support_assessment_id:=v_previous_assessment; NEW.claim_annotation_revision_id:=v_previous_annotation; NEW.claim_review_decision_id:=v_previous_claim_review; NEW.evidence_review_decision_id:=v_previous_evidence_review;
    END IF;
    NEW.claim_draft_id:=v_claim; NEW.decision_sequence:=v_last+1; NEW.supersedes_decision_id:=v_previous_id; NEW.recorded_at:=clock_timestamp();
    EXECUTE format('UPDATE %I.research_evidence_usage_authorization_sequence_allocator SET last_sequence=$5 WHERE project_id=$1 AND claim_intake_item_id=$2 AND evidence_intake_item_id=$3 AND usage_scope=$4',TG_TABLE_SCHEMA) USING NEW.project_id,NEW.claim_intake_item_id,NEW.evidence_intake_item_id,NEW.usage_scope,NEW.decision_sequence;
    RETURN NEW;
END; $function_body$ $create_function$;
END IF; END $$;

REVOKE ALL ON FUNCTION research_evidence_prepare_project_context_insert() FROM PUBLIC;
REVOKE ALL ON FUNCTION research_evidence_prepare_claim_annotation_insert() FROM PUBLIC;
REVOKE ALL ON FUNCTION research_evidence_prepare_usage_authorization_insert() FROM PUBLIC;
REVOKE ALL ON TABLE research_evidence_project_context_revision FROM PUBLIC;
REVOKE ALL ON TABLE research_evidence_project_context_sequence_allocator FROM PUBLIC;
REVOKE ALL ON TABLE research_evidence_claim_annotation_revision FROM PUBLIC;
REVOKE ALL ON TABLE research_evidence_claim_annotation_sequence_allocator FROM PUBLIC;
REVOKE ALL ON TABLE research_evidence_usage_authorization_decision FROM PUBLIC;
REVOKE ALL ON TABLE research_evidence_usage_authorization_sequence_allocator FROM PUBLIC;

DO $$ BEGIN
  IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='trg_repcr_prepare_insert' AND tgrelid='research_evidence_project_context_revision'::regclass) THEN CREATE TRIGGER trg_repcr_prepare_insert BEFORE INSERT ON research_evidence_project_context_revision FOR EACH ROW EXECUTE FUNCTION research_evidence_prepare_project_context_insert(); END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='trg_repcr_no_mutation' AND tgrelid='research_evidence_project_context_revision'::regclass) THEN CREATE TRIGGER trg_repcr_no_mutation BEFORE UPDATE OR DELETE ON research_evidence_project_context_revision FOR EACH ROW EXECUTE FUNCTION slicea_reject_mutation(); END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='trg_recar_prepare_insert' AND tgrelid='research_evidence_claim_annotation_revision'::regclass) THEN CREATE TRIGGER trg_recar_prepare_insert BEFORE INSERT ON research_evidence_claim_annotation_revision FOR EACH ROW EXECUTE FUNCTION research_evidence_prepare_claim_annotation_insert(); END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='trg_recar_no_mutation' AND tgrelid='research_evidence_claim_annotation_revision'::regclass) THEN CREATE TRIGGER trg_recar_no_mutation BEFORE UPDATE OR DELETE ON research_evidence_claim_annotation_revision FOR EACH ROW EXECUTE FUNCTION slicea_reject_mutation(); END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='trg_reuad_prepare_insert' AND tgrelid='research_evidence_usage_authorization_decision'::regclass) THEN CREATE TRIGGER trg_reuad_prepare_insert BEFORE INSERT ON research_evidence_usage_authorization_decision FOR EACH ROW EXECUTE FUNCTION research_evidence_prepare_usage_authorization_insert(); END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='trg_reuad_no_mutation' AND tgrelid='research_evidence_usage_authorization_decision'::regclass) THEN CREATE TRIGGER trg_reuad_no_mutation BEFORE UPDATE OR DELETE ON research_evidence_usage_authorization_decision FOR EACH ROW EXECUTE FUNCTION slicea_reject_mutation(); END IF;
END $$;

ALTER TABLE research_evidence_project_context_revision ENABLE ALWAYS TRIGGER trg_repcr_prepare_insert;
ALTER TABLE research_evidence_project_context_revision ENABLE ALWAYS TRIGGER trg_repcr_no_mutation;
ALTER TABLE research_evidence_claim_annotation_revision ENABLE ALWAYS TRIGGER trg_recar_prepare_insert;
ALTER TABLE research_evidence_claim_annotation_revision ENABLE ALWAYS TRIGGER trg_recar_no_mutation;
ALTER TABLE research_evidence_usage_authorization_decision ENABLE ALWAYS TRIGGER trg_reuad_prepare_insert;
ALTER TABLE research_evidence_usage_authorization_decision ENABLE ALWAYS TRIGGER trg_reuad_no_mutation;

-- Exact reapply validation. It is deliberately after creation so the same checks
-- validate both first apply and a complete no-op reapply; partial state was rejected above.
DO $$
DECLARE v_bad text; v_expected_owner oid;
BEGIN
  IF EXISTS (
    WITH expected(relation_name,ordinal,column_name,type_oid,type_modifier,
                  not_null,numeric_precision,numeric_scale) AS (VALUES
      ('research_evidence_project_context_revision'::text,1,'id','uuid'::regtype::oid,-1,true,NULL::integer,NULL::integer),
      ('research_evidence_project_context_revision',2,'project_id','uuid'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_project_context_revision',3,'request_id','text'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_project_context_revision',4,'research_question','text'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_project_context_revision',5,'project_limitations_json','jsonb'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_project_context_revision',6,'unresolved_gaps_json','jsonb'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_project_context_revision',7,'actor','text'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_project_context_revision',8,'context_sequence','integer'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_project_context_revision',9,'supersedes_context_revision_id','uuid'::regtype::oid,-1,false,NULL,NULL),
      ('research_evidence_project_context_revision',10,'recorded_at','timestamptz'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_project_context_sequence_allocator',1,'project_id','uuid'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_project_context_sequence_allocator',2,'last_sequence','integer'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_claim_annotation_revision',1,'id','uuid'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_claim_annotation_revision',2,'project_id','uuid'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_claim_annotation_revision',3,'claim_draft_id','uuid'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_claim_annotation_revision',4,'request_id','text'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_claim_annotation_revision',5,'epistemic_status','text'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_claim_annotation_revision',6,'confidence_label','text'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_claim_annotation_revision',7,'decision_relevance','text'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_claim_annotation_revision',8,'supports_statement','text'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_claim_annotation_revision',9,'does_not_prove','text'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_claim_annotation_revision',10,'limitations_json','jsonb'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_claim_annotation_revision',11,'related_claim_draft_ids_json','jsonb'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_claim_annotation_revision',12,'operator_notes','text'::regtype::oid,-1,false,NULL,NULL),
      ('research_evidence_claim_annotation_revision',13,'explicit_probability_value','numeric'::regtype::oid,458762,false,7,6),
      ('research_evidence_claim_annotation_revision',14,'explicit_probability_provided_by','text'::regtype::oid,-1,false,NULL,NULL),
      ('research_evidence_claim_annotation_revision',15,'explicit_probability_provenance_reference','text'::regtype::oid,-1,false,NULL,NULL),
      ('research_evidence_claim_annotation_revision',16,'explicit_probability_provenance_note','text'::regtype::oid,-1,false,NULL,NULL),
      ('research_evidence_claim_annotation_revision',17,'actor','text'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_claim_annotation_revision',18,'annotation_sequence','integer'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_claim_annotation_revision',19,'supersedes_annotation_revision_id','uuid'::regtype::oid,-1,false,NULL,NULL),
      ('research_evidence_claim_annotation_revision',20,'recorded_at','timestamptz'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_claim_annotation_sequence_allocator',1,'project_id','uuid'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_claim_annotation_sequence_allocator',2,'claim_draft_id','uuid'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_claim_annotation_sequence_allocator',3,'last_sequence','integer'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_usage_authorization_decision',1,'id','uuid'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_usage_authorization_decision',2,'project_id','uuid'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_usage_authorization_decision',3,'claim_intake_item_id','uuid'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_usage_authorization_decision',4,'evidence_intake_item_id','uuid'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_usage_authorization_decision',5,'claim_support_assessment_id','uuid'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_usage_authorization_decision',6,'usage_scope','text'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_usage_authorization_decision',7,'decision','text'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_usage_authorization_decision',8,'reason','text'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_usage_authorization_decision',9,'actor','text'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_usage_authorization_decision',10,'request_id','text'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_usage_authorization_decision',11,'claim_draft_id','uuid'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_usage_authorization_decision',12,'claim_annotation_revision_id','uuid'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_usage_authorization_decision',13,'claim_review_decision_id','uuid'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_usage_authorization_decision',14,'evidence_review_decision_id','uuid'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_usage_authorization_decision',15,'decision_sequence','integer'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_usage_authorization_decision',16,'supersedes_decision_id','uuid'::regtype::oid,-1,false,NULL,NULL),
      ('research_evidence_usage_authorization_decision',17,'recorded_at','timestamptz'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_usage_authorization_sequence_allocator',1,'project_id','uuid'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_usage_authorization_sequence_allocator',2,'claim_intake_item_id','uuid'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_usage_authorization_sequence_allocator',3,'evidence_intake_item_id','uuid'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_usage_authorization_sequence_allocator',4,'usage_scope','text'::regtype::oid,-1,true,NULL,NULL),
      ('research_evidence_usage_authorization_sequence_allocator',5,'last_sequence','integer'::regtype::oid,-1,true,NULL,NULL)
    ), actual AS (
      SELECT c.relname::text,a.attnum::integer,a.attname::text,a.atttypid,
             a.atttypmod,a.attnotnull,
             CASE WHEN a.atttypid='numeric'::regtype
                  THEN ((a.atttypmod-4) >> 16) & 65535 END,
             CASE WHEN a.atttypid='numeric'::regtype
                  THEN (a.atttypmod-4) & 65535 END
      FROM pg_class c JOIN pg_attribute a ON a.attrelid=c.oid
      WHERE c.relnamespace=current_schema()::regnamespace
        AND c.relname=ANY(ARRAY[
          'research_evidence_project_context_revision','research_evidence_project_context_sequence_allocator',
          'research_evidence_claim_annotation_revision','research_evidence_claim_annotation_sequence_allocator',
          'research_evidence_usage_authorization_decision','research_evidence_usage_authorization_sequence_allocator'])
        AND a.attnum>0 AND NOT a.attisdropped
        AND NOT a.atthasdef AND a.attidentity='' AND a.attgenerated=''
    )
    (SELECT * FROM expected EXCEPT SELECT * FROM actual)
    UNION ALL
    (SELECT * FROM actual EXCEPT SELECT * FROM expected)
  ) THEN
    RAISE EXCEPTION 'v61 contract violation: exact column manifest mismatch'
      USING ERRCODE='invalid_schema_definition';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM pg_class relation
    JOIN pg_attribute attribute ON attribute.attrelid=relation.oid
    JOIN pg_type attribute_type ON attribute_type.oid=attribute.atttypid
    WHERE relation.relnamespace=current_schema()::regnamespace
      AND relation.relname=ANY(ARRAY[
        'research_evidence_project_context_revision',
        'research_evidence_project_context_sequence_allocator',
        'research_evidence_claim_annotation_revision',
        'research_evidence_claim_annotation_sequence_allocator',
        'research_evidence_usage_authorization_decision',
        'research_evidence_usage_authorization_sequence_allocator'])
      AND attribute.attnum>0 AND NOT attribute.attisdropped
      AND attribute.attcollation<>attribute_type.typcollation
  ) THEN
    RAISE EXCEPTION 'v61 contract violation: exact column collation manifest mismatch'
      USING ERRCODE='invalid_schema_definition';
  END IF;
  IF EXISTS (
    WITH column_acl_state AS (
      SELECT namespace.nspname::text schema_name,
             relation.relname::text relation_name,
             attribute.attname::text column_name,
             attribute.attacl IS NULL acl_is_null,
             acl.grantee,acl.grantor,acl.privilege_type,acl.is_grantable
      FROM pg_class relation
      JOIN pg_namespace namespace ON namespace.oid=relation.relnamespace
      JOIN pg_attribute attribute ON attribute.attrelid=relation.oid
      LEFT JOIN LATERAL aclexplode(attribute.attacl) acl ON true
      WHERE namespace.oid=current_schema()::regnamespace
        AND relation.relname=ANY(ARRAY[
          'research_evidence_project_context_revision',
          'research_evidence_project_context_sequence_allocator',
          'research_evidence_claim_annotation_revision',
          'research_evidence_claim_annotation_sequence_allocator',
          'research_evidence_usage_authorization_decision',
          'research_evidence_usage_authorization_sequence_allocator'])
        AND attribute.attnum>0 AND NOT attribute.attisdropped
    )
    SELECT 1 FROM column_acl_state
    WHERE NOT acl_is_null OR grantee IS NOT NULL OR grantor IS NOT NULL
       OR privilege_type IS NOT NULL OR is_grantable IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'v61 contract violation: exact column ACL manifest mismatch'
      USING ERRCODE='invalid_schema_definition';
  END IF;
  IF EXISTS (
    SELECT 1 FROM pg_class relation
    WHERE relation.relnamespace=current_schema()::regnamespace
      AND relation.relname=ANY(ARRAY[
        'research_evidence_project_context_revision',
        'research_evidence_project_context_sequence_allocator',
        'research_evidence_claim_annotation_revision',
        'research_evidence_claim_annotation_sequence_allocator',
        'research_evidence_usage_authorization_decision',
        'research_evidence_usage_authorization_sequence_allocator'])
      AND (relation.relrowsecurity OR relation.relforcerowsecurity)
  ) THEN
    RAISE EXCEPTION 'v61 contract violation: exact relation RLS state mismatch'
      USING ERRCODE='invalid_schema_definition';
  END IF;
  SELECT string_agg(name,', ' ORDER BY name) INTO v_bad FROM (VALUES
    ('research_evidence_project_context_revision'::text,10),
    ('research_evidence_project_context_sequence_allocator',2),
    ('research_evidence_claim_annotation_revision',20),
    ('research_evidence_claim_annotation_sequence_allocator',3),
    ('research_evidence_usage_authorization_decision',17),
    ('research_evidence_usage_authorization_sequence_allocator',5)
  ) e(name,columns) WHERE (SELECT count(*) FROM information_schema.columns c WHERE c.table_schema=current_schema() AND c.table_name=e.name)<>e.columns;
  IF v_bad IS NOT NULL THEN RAISE EXCEPTION 'v61 contract violation: divergent columns %',v_bad USING ERRCODE='invalid_schema_definition'; END IF;

  IF EXISTS (
    SELECT 1 FROM information_schema.columns c
    WHERE c.table_schema=current_schema()
      AND c.table_name=ANY(ARRAY[
        'research_evidence_project_context_revision',
        'research_evidence_project_context_sequence_allocator',
        'research_evidence_claim_annotation_revision',
        'research_evidence_claim_annotation_sequence_allocator',
        'research_evidence_usage_authorization_decision',
        'research_evidence_usage_authorization_sequence_allocator'])
      AND (
        (c.column_name IN ('id','project_id','supersedes_context_revision_id',
          'claim_draft_id','supersedes_annotation_revision_id',
          'claim_intake_item_id','evidence_intake_item_id',
          'claim_support_assessment_id','claim_annotation_revision_id',
          'claim_review_decision_id','evidence_review_decision_id',
          'supersedes_decision_id') AND c.data_type<>'uuid')
        OR (c.column_name IN ('context_sequence','annotation_sequence',
          'decision_sequence','last_sequence') AND c.data_type<>'integer')
        OR (c.column_name IN ('project_limitations_json','unresolved_gaps_json',
          'limitations_json','related_claim_draft_ids_json') AND c.data_type<>'jsonb')
        OR (c.column_name='recorded_at' AND c.data_type<>'timestamp with time zone')
        OR (c.column_name='explicit_probability_value'
            AND (c.data_type<>'numeric' OR c.numeric_precision<>7 OR c.numeric_scale<>6))
        OR (c.column_name IN ('request_id','research_question','actor',
          'epistemic_status','confidence_label','decision_relevance',
          'supports_statement','does_not_prove','operator_notes',
          'explicit_probability_provided_by',
          'explicit_probability_provenance_reference',
          'explicit_probability_provenance_note','usage_scope','decision','reason')
            AND c.data_type<>'text')
      )
  ) THEN
    RAISE EXCEPTION 'v61 contract violation: divergent column types'
      USING ERRCODE='invalid_schema_definition';
  END IF;

  IF EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=ANY(ARRAY['research_evidence_project_context_revision','research_evidence_project_context_sequence_allocator','research_evidence_claim_annotation_revision','research_evidence_claim_annotation_sequence_allocator','research_evidence_usage_authorization_decision','research_evidence_usage_authorization_sequence_allocator']) AND column_default IS NOT NULL)
     OR EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=ANY(ARRAY['research_evidence_project_context_revision','research_evidence_claim_annotation_revision','research_evidence_usage_authorization_decision']) AND is_nullable='YES' AND column_name NOT IN ('supersedes_context_revision_id','supersedes_annotation_revision_id','operator_notes','explicit_probability_value','explicit_probability_provided_by','explicit_probability_provenance_reference','explicit_probability_provenance_note','supersedes_decision_id')) THEN
    RAISE EXCEPTION 'v61 contract violation: divergent defaults or nullability' USING ERRCODE='invalid_schema_definition'; END IF;

  SELECT string_agg(name,', ' ORDER BY name) INTO v_bad FROM (VALUES
   ('fk_repcr_project'::text),('fk_repcr_supersedes_same_project'),('fk_recar_project'),('fk_recar_claim_project'),('fk_recar_supersedes_same_claim'),
   ('fk_reuad_project'),('fk_reuad_claim_item'),('fk_reuad_evidence_item'),('fk_reuad_assessment_exact'),('fk_reuad_claim_draft'),('fk_reuad_annotation_exact'),('fk_reuad_claim_review_exact'),('fk_reuad_evidence_review_exact'),('fk_reuad_supersedes_same_scope')
  ) e(name) WHERE NOT EXISTS(SELECT 1 FROM pg_constraint c WHERE c.connamespace=current_schema()::regnamespace AND c.conname=e.name AND c.contype='f' AND c.confdeltype='r' AND c.convalidated);
  IF v_bad IS NOT NULL THEN RAISE EXCEPTION 'v61 contract violation: missing or divergent restrictive foreign keys %',v_bad USING ERRCODE='invalid_schema_definition'; END IF;

  SELECT string_agg(name,', ' ORDER BY name) INTO v_bad FROM (VALUES
    ('research_evidence_project_context_revision_pkey'::text),
    ('uq_repcr_id_project'),('uq_repcr_project_sequence'),
    ('uq_repcr_project_request'),('uq_repcr_supersedes_once'),
    ('fk_repcr_project'),('fk_repcr_supersedes_same_project'),
    ('ck_repcr_sequence_positive'),('ck_repcr_request'),('ck_repcr_question'),
    ('ck_repcr_actor'),('ck_repcr_limitations'),('ck_repcr_gaps'),
    ('research_evidence_project_context_sequence_allocator_pkey'),
    ('fk_repcsa_project'),('ck_repcsa_last_sequence'),
    ('research_evidence_claim_annotation_revision_pkey'),
    ('uq_recar_id_project_claim'),('uq_recar_claim_sequence'),
    ('uq_recar_claim_request'),('uq_recar_supersedes_once'),
    ('fk_recar_project'),('fk_recar_claim_project'),
    ('fk_recar_supersedes_same_claim'),('ck_recar_sequence_positive'),
    ('ck_recar_request'),('ck_recar_epistemic'),('ck_recar_confidence'),
    ('ck_recar_relevance'),('ck_recar_supports'),('ck_recar_does_not_prove'),
    ('ck_recar_limitations'),('ck_recar_related_array'),('ck_recar_notes'),
    ('ck_recar_actor'),('ck_recar_probability_shape'),
    ('research_evidence_claim_annotation_sequence_allocator_pkey'),
    ('fk_recasa_project'),('fk_recasa_claim'),('ck_recasa_last_sequence'),
    ('research_evidence_usage_authorization_decision_pkey'),
    ('uq_reuad_id_project_scope'),('uq_reuad_scope_sequence'),
    ('uq_reuad_scope_request'),('uq_reuad_supersedes_once'),
    ('fk_reuad_project'),('fk_reuad_claim_item'),('fk_reuad_evidence_item'),
    ('fk_reuad_assessment_exact'),('fk_reuad_claim_draft'),
    ('fk_reuad_annotation_exact'),('fk_reuad_claim_review_exact'),
    ('fk_reuad_evidence_review_exact'),('fk_reuad_supersedes_same_scope'),
    ('ck_reuad_scope'),('ck_reuad_decision'),('ck_reuad_sequence_positive'),
    ('ck_reuad_reason'),('ck_reuad_actor'),('ck_reuad_request'),
    ('research_evidence_usage_authorization_sequence_allocator_pkey'),
    ('fk_reuasa_project'),('fk_reuasa_claim_item'),
    ('fk_reuasa_evidence_item'),('ck_reuasa_scope'),
    ('ck_reuasa_last_sequence')
  ) expected(name)
  WHERE NOT EXISTS (
    SELECT 1 FROM pg_constraint c
    WHERE c.connamespace=current_schema()::regnamespace
      AND c.conname=expected.name AND c.convalidated
  );
  IF v_bad IS NOT NULL THEN RAISE EXCEPTION 'v61 contract violation: missing constraints %',v_bad USING ERRCODE='invalid_schema_definition'; END IF;

  SELECT string_agg(name,', ' ORDER BY name) INTO v_bad FROM (VALUES
    ('ck_recar_actor'::text,'research_evidence_claim_annotation_revision'::text,'{17}'::text,'8839e18b6c504c57a9f13548d334dcef'::text),
    ('ck_recar_confidence','research_evidence_claim_annotation_revision','{6}','b88116419a16f2ec6de81d35049eb966'),
    ('ck_recar_does_not_prove','research_evidence_claim_annotation_revision','{9}','c2327b237940ea37623f3c3f4f0f5b07'),
    ('ck_recar_epistemic','research_evidence_claim_annotation_revision','{5}','e6ef95f6ce6e14663ecaf7d1e58b54b3'),
    ('ck_recar_limitations','research_evidence_claim_annotation_revision','{10}','049abb07de7bd84d6a06dd853dbcc943'),
    ('ck_recar_notes','research_evidence_claim_annotation_revision','{12}','82191a04953521e0448bc585c03423a4'),
    ('ck_recar_probability_shape','research_evidence_claim_annotation_revision','{13,14,15,16}','9422b4e6c9d966daf799464a99e16bb2'),
    ('ck_recar_related_array','research_evidence_claim_annotation_revision','{11}','6fb6e5889b4aa600038b8bcb62ed6db7'),
    ('ck_recar_relevance','research_evidence_claim_annotation_revision','{7}','3a31da98f2c5676f4d2f4276f1984ea8'),
    ('ck_recar_request','research_evidence_claim_annotation_revision','{4}','dffc3e462e66eeadea2c0403a44547a3'),
    ('ck_recar_sequence_positive','research_evidence_claim_annotation_revision','{18}','23a06ddfe4643095a91c3b5bdf92c836'),
    ('ck_recar_supports','research_evidence_claim_annotation_revision','{8}','4495ab81e1e89a1058ad7194165be30c'),
    ('ck_recasa_last_sequence','research_evidence_claim_annotation_sequence_allocator','{3}','3454f3c52c011d164991a7af7e9863bb'),
    ('ck_repcr_actor','research_evidence_project_context_revision','{7}','8839e18b6c504c57a9f13548d334dcef'),
    ('ck_repcr_gaps','research_evidence_project_context_revision','{6}','bb580a7ef54748b6cd19622b9defd217'),
    ('ck_repcr_limitations','research_evidence_project_context_revision','{5}','8bb2f834112a42d97debf6dff9996e3f'),
    ('ck_repcr_question','research_evidence_project_context_revision','{4}','14d15515c3294c1fd0f5d67fb6cb7173'),
    ('ck_repcr_request','research_evidence_project_context_revision','{3}','dffc3e462e66eeadea2c0403a44547a3'),
    ('ck_repcr_sequence_positive','research_evidence_project_context_revision','{8}','96ee9a5047882135e6de60bc8ffc5345'),
    ('ck_repcsa_last_sequence','research_evidence_project_context_sequence_allocator','{2}','3454f3c52c011d164991a7af7e9863bb'),
    ('ck_reuad_actor','research_evidence_usage_authorization_decision','{9}','8839e18b6c504c57a9f13548d334dcef'),
    ('ck_reuad_decision','research_evidence_usage_authorization_decision','{7}','022c11c5bd11a90a16afe214e679087f'),
    ('ck_reuad_reason','research_evidence_usage_authorization_decision','{8}','ac37bccddf048b462fdb7faad0ac7efb'),
    ('ck_reuad_request','research_evidence_usage_authorization_decision','{10}','dffc3e462e66eeadea2c0403a44547a3'),
    ('ck_reuad_scope','research_evidence_usage_authorization_decision','{6}','e858b61fe57267234047d26c2a3207bc'),
    ('ck_reuad_sequence_positive','research_evidence_usage_authorization_decision','{15}','033230813db3b4db28f7579e0acf9589'),
    ('ck_reuasa_last_sequence','research_evidence_usage_authorization_sequence_allocator','{5}','3454f3c52c011d164991a7af7e9863bb'),
    ('ck_reuasa_scope','research_evidence_usage_authorization_sequence_allocator','{4}','e858b61fe57267234047d26c2a3207bc')
  ) e(name,relation_name,key_columns,definition_hash)
  WHERE NOT EXISTS(
    SELECT 1 FROM pg_constraint c JOIN pg_class r ON r.oid=c.conrelid
    WHERE c.connamespace=current_schema()::regnamespace
      AND c.conname=e.name AND r.relname=e.relation_name
      AND c.contype='c' AND c.conkey::text=e.key_columns
      AND c.convalidated AND NOT c.condeferrable AND NOT c.condeferred
      AND md5(pg_get_constraintdef(c.oid,true))=e.definition_hash
  );
  IF v_bad IS NOT NULL THEN
    RAISE EXCEPTION 'v61 contract violation: exact CHECK manifest mismatch %',v_bad
      USING ERRCODE='invalid_schema_definition';
  END IF;

  SELECT string_agg(name,', ' ORDER BY name) INTO v_bad FROM (VALUES
    ('fk_recar_claim_project'::text,'research_evidence_claim_annotation_revision'::text,'research_claim_draft'::text,'{3,2}'::text,'{1,2}'::text),
    ('fk_recar_project','research_evidence_claim_annotation_revision','projects','{2}','{1}'),
    ('fk_recar_supersedes_same_claim','research_evidence_claim_annotation_revision','research_evidence_claim_annotation_revision','{19,2,3}','{1,2,3}'),
    ('fk_recasa_claim','research_evidence_claim_annotation_sequence_allocator','research_claim_draft','{2,1}','{1,2}'),
    ('fk_recasa_project','research_evidence_claim_annotation_sequence_allocator','projects','{1}','{1}'),
    ('fk_repcr_project','research_evidence_project_context_revision','projects','{2}','{1}'),
    ('fk_repcr_supersedes_same_project','research_evidence_project_context_revision','research_evidence_project_context_revision','{9,2}','{1,2}'),
    ('fk_repcsa_project','research_evidence_project_context_sequence_allocator','projects','{1}','{1}'),
    ('fk_reuad_annotation_exact','research_evidence_usage_authorization_decision','research_evidence_claim_annotation_revision','{12,2,11}','{1,2,3}'),
    ('fk_reuad_assessment_exact','research_evidence_usage_authorization_decision','research_evidence_claim_support_assessment','{5,2,3,4}','{1,2,3,4}'),
    ('fk_reuad_claim_draft','research_evidence_usage_authorization_decision','research_claim_draft','{11,2}','{1,2}'),
    ('fk_reuad_claim_item','research_evidence_usage_authorization_decision','research_evidence_intake_item','{3,2}','{1,2}'),
    ('fk_reuad_claim_review_exact','research_evidence_usage_authorization_decision','research_evidence_intake_item_review_decision','{13,2,3}','{1,2,3}'),
    ('fk_reuad_evidence_item','research_evidence_usage_authorization_decision','research_evidence_intake_item','{4,2}','{1,2}'),
    ('fk_reuad_evidence_review_exact','research_evidence_usage_authorization_decision','research_evidence_intake_item_review_decision','{14,2,4}','{1,2,3}'),
    ('fk_reuad_project','research_evidence_usage_authorization_decision','projects','{2}','{1}'),
    ('fk_reuad_supersedes_same_scope','research_evidence_usage_authorization_decision','research_evidence_usage_authorization_decision','{16,2,3,4,6}','{1,2,3,4,6}'),
    ('fk_reuasa_claim_item','research_evidence_usage_authorization_sequence_allocator','research_evidence_intake_item','{2,1}','{1,2}'),
    ('fk_reuasa_evidence_item','research_evidence_usage_authorization_sequence_allocator','research_evidence_intake_item','{3,1}','{1,2}'),
    ('fk_reuasa_project','research_evidence_usage_authorization_sequence_allocator','projects','{1}','{1}')
  ) e(name,relation_name,referenced_relation,local_keys,referenced_keys)
  WHERE NOT EXISTS(
    SELECT 1 FROM pg_constraint c
    JOIN pg_class local_relation ON local_relation.oid=c.conrelid
    JOIN pg_class referenced_relation ON referenced_relation.oid=c.confrelid
    WHERE c.connamespace=current_schema()::regnamespace AND c.conname=e.name
      AND local_relation.relnamespace=current_schema()::regnamespace
      AND local_relation.relname=e.relation_name
      AND referenced_relation.relnamespace=current_schema()::regnamespace
      AND referenced_relation.relname=e.referenced_relation
      AND c.contype='f' AND c.conkey::text=e.local_keys
      AND c.confkey::text=e.referenced_keys
      AND c.confupdtype='a' AND c.confdeltype='r' AND c.confmatchtype='s'
      AND c.convalidated AND NOT c.condeferrable AND NOT c.condeferred
  );
  IF v_bad IS NOT NULL OR (
    SELECT count(*) FROM pg_constraint c JOIN pg_class r ON r.oid=c.conrelid
    WHERE r.relnamespace=current_schema()::regnamespace
      AND r.relname=ANY(ARRAY[
        'research_evidence_project_context_revision','research_evidence_project_context_sequence_allocator',
        'research_evidence_claim_annotation_revision','research_evidence_claim_annotation_sequence_allocator',
        'research_evidence_usage_authorization_decision','research_evidence_usage_authorization_sequence_allocator'])
  )<>66 THEN
    RAISE EXCEPTION 'v61 contract violation: exact constraint manifest mismatch %',coalesce(v_bad,'extra constraint')
      USING ERRCODE='invalid_schema_definition';
  END IF;
  IF EXISTS (
    SELECT 1 FROM pg_constraint c JOIN pg_class r ON r.oid=c.conrelid
    WHERE r.relnamespace=current_schema()::regnamespace
      AND r.relname=ANY(ARRAY[
        'research_evidence_project_context_revision','research_evidence_project_context_sequence_allocator',
        'research_evidence_claim_annotation_revision','research_evidence_claim_annotation_sequence_allocator',
        'research_evidence_usage_authorization_decision','research_evidence_usage_authorization_sequence_allocator'])
      AND (NOT c.convalidated OR c.condeferrable OR c.condeferred)
  ) THEN
    RAISE EXCEPTION 'v61 contract violation: constraint validation or deferrability mismatch'
      USING ERRCODE='invalid_schema_definition';
  END IF;

  SELECT string_agg(name,', ' ORDER BY name) INTO v_bad FROM (VALUES
    ('research_evidence_project_context_revision_pkey'::text,'research_evidence_project_context_revision'::text,'p'::"char",'{1}'::text),
    ('uq_repcr_id_project','research_evidence_project_context_revision','u','{1,2}'),
    ('uq_repcr_project_sequence','research_evidence_project_context_revision','u','{2,8}'),
    ('uq_repcr_project_request','research_evidence_project_context_revision','u','{2,3}'),
    ('uq_repcr_supersedes_once','research_evidence_project_context_revision','u','{9}'),
    ('research_evidence_project_context_sequence_allocator_pkey','research_evidence_project_context_sequence_allocator','p','{1}'),
    ('research_evidence_claim_annotation_revision_pkey','research_evidence_claim_annotation_revision','p','{1}'),
    ('uq_recar_id_project_claim','research_evidence_claim_annotation_revision','u','{1,2,3}'),
    ('uq_recar_claim_sequence','research_evidence_claim_annotation_revision','u','{2,3,18}'),
    ('uq_recar_claim_request','research_evidence_claim_annotation_revision','u','{2,3,4}'),
    ('uq_recar_supersedes_once','research_evidence_claim_annotation_revision','u','{19}'),
    ('research_evidence_claim_annotation_sequence_allocator_pkey','research_evidence_claim_annotation_sequence_allocator','p','{1,2}'),
    ('research_evidence_usage_authorization_decision_pkey','research_evidence_usage_authorization_decision','p','{1}'),
    ('uq_reuad_id_project_scope','research_evidence_usage_authorization_decision','u','{1,2,3,4,6}'),
    ('uq_reuad_scope_sequence','research_evidence_usage_authorization_decision','u','{2,3,4,6,15}'),
    ('uq_reuad_scope_request','research_evidence_usage_authorization_decision','u','{2,3,4,6,10}'),
    ('uq_reuad_supersedes_once','research_evidence_usage_authorization_decision','u','{16}'),
    ('research_evidence_usage_authorization_sequence_allocator_pkey','research_evidence_usage_authorization_sequence_allocator','p','{1,2,3,4}')
  ) expected(name,relation_name,constraint_type,key_attributes)
  WHERE NOT EXISTS (
    SELECT 1
    FROM pg_constraint constraint_info
    JOIN pg_class relation ON relation.oid=constraint_info.conrelid
    JOIN pg_class index_relation ON index_relation.oid=constraint_info.conindid
    JOIN pg_index index_info ON index_info.indexrelid=index_relation.oid
                              AND index_info.indrelid=relation.oid
    JOIN pg_am access_method ON access_method.oid=index_relation.relam
    WHERE constraint_info.connamespace=current_schema()::regnamespace
      AND constraint_info.conname=expected.name
      AND relation.relnamespace=current_schema()::regnamespace
      AND relation.relname=expected.relation_name
      AND constraint_info.contype=expected.constraint_type
      AND constraint_info.conkey::text=expected.key_attributes
      AND constraint_info.convalidated
      AND NOT constraint_info.condeferrable
      AND NOT constraint_info.condeferred
      AND index_relation.relnamespace=current_schema()::regnamespace
      AND index_relation.relname=expected.name
      AND index_relation.relkind='i'
      AND index_relation.reloptions IS NULL
      AND access_method.amname='btree'
      AND index_info.indisprimary=(expected.constraint_type='p')
      AND index_info.indisunique
      AND NOT index_info.indisexclusion
      AND index_info.indisvalid AND index_info.indisready
      AND index_info.indislive AND index_info.indimmediate
      AND NOT index_info.indnullsnotdistinct
      AND index_info.indkey::text=
          pg_catalog.btrim(
            pg_catalog.translate(expected.key_attributes,'{},', '   ')
          )
      AND index_info.indnkeyatts=index_info.indnatts
      AND index_info.indexprs IS NULL AND index_info.indpred IS NULL
      AND NOT EXISTS (
        SELECT 1 FROM generate_series(0,index_info.indnkeyatts-1) position
        WHERE index_info.indoption[position]<>0
      )
      AND NOT EXISTS (
        SELECT 1 FROM generate_series(0,index_info.indnkeyatts-1) position
        JOIN pg_attribute attribute
          ON attribute.attrelid=index_info.indrelid
         AND attribute.attnum=index_info.indkey[position]
        JOIN pg_type attribute_type ON attribute_type.oid=attribute.atttypid
        LEFT JOIN pg_opclass opclass ON opclass.oid=index_info.indclass[position]
        WHERE opclass.opcmethod<>index_relation.relam
           OR NOT opclass.opcdefault
           OR opclass.opcintype<>attribute.atttypid
           OR index_info.indcollation[position]<>attribute_type.typcollation
      )
  );
  IF v_bad IS NOT NULL THEN
    RAISE EXCEPTION 'v61 contract violation: exact primary/unique constraint manifest mismatch %',v_bad
      USING ERRCODE='invalid_schema_definition';
  END IF;

  SELECT string_agg(name,', ' ORDER BY name) INTO v_bad FROM (VALUES
    ('trg_repcr_prepare_insert'::text,'research_evidence_project_context_revision'::text,7,'research_evidence_prepare_project_context_insert'::text),
    ('trg_repcr_no_mutation','research_evidence_project_context_revision',27,'slicea_reject_mutation'),
    ('trg_recar_prepare_insert','research_evidence_claim_annotation_revision',7,'research_evidence_prepare_claim_annotation_insert'),
    ('trg_recar_no_mutation','research_evidence_claim_annotation_revision',27,'slicea_reject_mutation'),
    ('trg_reuad_prepare_insert','research_evidence_usage_authorization_decision',7,'research_evidence_prepare_usage_authorization_insert'),
    ('trg_reuad_no_mutation','research_evidence_usage_authorization_decision',27,'slicea_reject_mutation')
  ) e(name,relation_name,trigger_type,function_name) WHERE NOT EXISTS(
    SELECT 1 FROM pg_trigger t
    JOIN pg_class c ON c.oid=t.tgrelid
    JOIN pg_proc p ON p.oid=t.tgfoid
    WHERE c.relnamespace=current_schema()::regnamespace
      AND c.relname=e.relation_name AND t.tgname=e.name
      AND p.pronamespace=current_schema()::regnamespace
      AND p.proname=e.function_name AND p.proargtypes=''::oidvector
      AND t.tgtype=e.trigger_type AND (t.tgtype & 30)=e.trigger_type-1
      AND t.tgenabled='A' AND NOT t.tgisinternal AND t.tgnargs=0
      AND octet_length(t.tgargs)=0 AND t.tgattr=''::int2vector
      AND t.tgqual IS NULL AND NOT t.tgdeferrable
      AND NOT t.tginitdeferred AND t.tgconstraint=0
  );
  IF v_bad IS NOT NULL OR (
    SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
    WHERE c.relnamespace=current_schema()::regnamespace AND NOT t.tgisinternal
      AND c.relname=ANY(ARRAY[
        'research_evidence_project_context_revision',
        'research_evidence_project_context_sequence_allocator',
        'research_evidence_claim_annotation_revision',
        'research_evidence_claim_annotation_sequence_allocator',
        'research_evidence_usage_authorization_decision',
        'research_evidence_usage_authorization_sequence_allocator'])
  )<>6 THEN
    RAISE EXCEPTION 'v61 contract violation: exact trigger manifest mismatch %',coalesce(v_bad,'extra trigger')
      USING ERRCODE='invalid_schema_definition';
  END IF;

  SELECT string_agg(name,', ' ORDER BY name) INTO v_bad FROM (VALUES
    ('research_evidence_project_context_revision_pkey'::text,'research_evidence_project_context_revision'::text,true,true,'1'::text,'0'::text),
    ('uq_repcr_id_project','research_evidence_project_context_revision',true,false,'1 2','0 0'),
    ('uq_repcr_project_sequence','research_evidence_project_context_revision',true,false,'2 8','0 0'),
    ('uq_repcr_project_request','research_evidence_project_context_revision',true,false,'2 3','0 0'),
    ('uq_repcr_supersedes_once','research_evidence_project_context_revision',true,false,'9','0'),
    ('idx_repcr_project_sequence','research_evidence_project_context_revision',false,false,'2 8','0 3'),
    ('research_evidence_project_context_sequence_allocator_pkey','research_evidence_project_context_sequence_allocator',true,true,'1','0'),
    ('research_evidence_claim_annotation_revision_pkey','research_evidence_claim_annotation_revision',true,true,'1','0'),
    ('uq_recar_id_project_claim','research_evidence_claim_annotation_revision',true,false,'1 2 3','0 0 0'),
    ('uq_recar_claim_sequence','research_evidence_claim_annotation_revision',true,false,'2 3 18','0 0 0'),
    ('uq_recar_claim_request','research_evidence_claim_annotation_revision',true,false,'2 3 4','0 0 0'),
    ('uq_recar_supersedes_once','research_evidence_claim_annotation_revision',true,false,'19','0'),
    ('idx_recar_project_claim_sequence','research_evidence_claim_annotation_revision',false,false,'2 3 18','0 0 3'),
    ('research_evidence_claim_annotation_sequence_allocator_pkey','research_evidence_claim_annotation_sequence_allocator',true,true,'1 2','0 0'),
    ('research_evidence_usage_authorization_decision_pkey','research_evidence_usage_authorization_decision',true,true,'1','0'),
    ('uq_reuad_id_project_scope','research_evidence_usage_authorization_decision',true,false,'1 2 3 4 6','0 0 0 0 0'),
    ('uq_reuad_scope_sequence','research_evidence_usage_authorization_decision',true,false,'2 3 4 6 15','0 0 0 0 0'),
    ('uq_reuad_scope_request','research_evidence_usage_authorization_decision',true,false,'2 3 4 6 10','0 0 0 0 0'),
    ('uq_reuad_supersedes_once','research_evidence_usage_authorization_decision',true,false,'16','0'),
    ('idx_reuad_scope_sequence','research_evidence_usage_authorization_decision',false,false,'2 3 4 6 15','0 0 0 0 3'),
    ('research_evidence_usage_authorization_sequence_allocator_pkey','research_evidence_usage_authorization_sequence_allocator',true,true,'1 2 3 4','0 0 0 0')
  ) e(name,relation_name,is_unique,is_primary,key_attributes,key_options)
  WHERE NOT EXISTS(
    SELECT 1 FROM pg_class index_class
    JOIN pg_namespace index_namespace ON index_namespace.oid=index_class.relnamespace
    JOIN pg_index i ON i.indexrelid=index_class.oid
    JOIN pg_class table_class ON table_class.oid=i.indrelid
    JOIN pg_am access_method ON access_method.oid=index_class.relam
    WHERE index_namespace.oid=current_schema()::regnamespace
      AND index_class.relname=e.name AND index_class.relkind='i'
      AND index_class.reloptions IS NULL
      AND table_class.relnamespace=current_schema()::regnamespace
      AND table_class.relname=e.relation_name
      AND access_method.amname='btree'
      AND i.indisunique=e.is_unique AND i.indisprimary=e.is_primary
      AND NOT i.indisexclusion AND i.indisvalid AND i.indisready AND i.indislive
      AND i.indimmediate AND NOT i.indnullsnotdistinct
      AND i.indkey::text=e.key_attributes AND i.indoption::text=e.key_options
      AND i.indnkeyatts=i.indnatts AND i.indexprs IS NULL AND i.indpred IS NULL
      AND NOT EXISTS(
        SELECT 1 FROM generate_series(0,i.indnkeyatts-1) position
        JOIN pg_attribute attribute
          ON attribute.attrelid=i.indrelid
         AND attribute.attnum=i.indkey[position]
        JOIN pg_type attribute_type ON attribute_type.oid=attribute.atttypid
        LEFT JOIN pg_opclass opclass ON opclass.oid=i.indclass[position]
        WHERE opclass.opcmethod<>index_class.relam OR NOT opclass.opcdefault
          OR opclass.opcintype<>attribute.atttypid
          OR i.indcollation[position]<>attribute_type.typcollation
      )
  );
  IF v_bad IS NOT NULL OR (
    SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid
    WHERE c.relnamespace=current_schema()::regnamespace
      AND c.relname=ANY(ARRAY[
        'research_evidence_project_context_revision','research_evidence_project_context_sequence_allocator',
        'research_evidence_claim_annotation_revision','research_evidence_claim_annotation_sequence_allocator',
        'research_evidence_usage_authorization_decision','research_evidence_usage_authorization_sequence_allocator'])
  )<>21 THEN
    RAISE EXCEPTION 'v61 contract violation: exact index manifest mismatch %',coalesce(v_bad,'extra index')
      USING ERRCODE='invalid_schema_definition';
  END IF;

  SELECT string_agg(name,', ' ORDER BY name) INTO v_bad FROM (VALUES
    ('research_evidence_pack_string_array_valid'::text,'3802 23 23'::text,'boolean'::regtype::oid,'i'::"char",'5095af3186b70edd7639534addcd0e53'::text),
    ('research_evidence_prepare_claim_annotation_insert','', 'trigger'::regtype::oid,'v'::"char",'da31e5238de34c616edc4519ebe31660'),
    ('research_evidence_prepare_project_context_insert','', 'trigger'::regtype::oid,'v'::"char",'8badc5b2d4f5b588af8c01785c58d252'),
    ('research_evidence_prepare_usage_authorization_insert','', 'trigger'::regtype::oid,'v'::"char",'5fe986d5b76e21b281dd0f94385325af')
  ) expected(name,argument_types,return_type,volatility,body_hash)
  WHERE NOT EXISTS(
    SELECT 1 FROM pg_proc p JOIN pg_language language ON language.oid=p.prolang
    WHERE p.pronamespace=current_schema()::regnamespace
      AND p.proname=expected.name AND p.proargtypes::text=expected.argument_types
      AND p.prorettype=expected.return_type AND language.lanname='plpgsql'
      AND p.prokind='f' AND NOT p.proretset AND p.pronargdefaults=0
      AND p.provariadic=0
      AND p.prosecdef AND p.provolatile=expected.volatility
      AND NOT p.proisstrict AND p.proparallel='u'
      AND p.proconfig=ARRAY['search_path=pg_catalog']::text[]
      AND md5(p.prosrc)=expected.body_hash
  );
  IF v_bad IS NOT NULL OR (
    SELECT count(*) FROM pg_proc p
    WHERE p.pronamespace=current_schema()::regnamespace
      AND p.proname=ANY(ARRAY[
        'research_evidence_pack_string_array_valid','research_evidence_prepare_project_context_insert',
        'research_evidence_prepare_claim_annotation_insert','research_evidence_prepare_usage_authorization_insert'])
  )<>4 THEN
    RAISE EXCEPTION 'v61 contract violation: exact function manifest mismatch %',coalesce(v_bad,'extra overload')
      USING ERRCODE='invalid_schema_definition';
  END IF;
  SELECT assessment.relowner INTO v_expected_owner
  FROM pg_class assessment
  JOIN pg_class allocator
    ON allocator.relnamespace=assessment.relnamespace
   AND allocator.relname='research_evidence_claim_support_sequence_allocator'
  JOIN pg_proc prepare
    ON prepare.pronamespace=assessment.relnamespace
   AND prepare.proname='research_evidence_prepare_claim_support_insert'
   AND prepare.proargtypes=''::oidvector
  WHERE assessment.relnamespace=current_schema()::regnamespace
    AND assessment.relname='research_evidence_claim_support_assessment'
    AND allocator.relowner=assessment.relowner
    AND prepare.proowner=assessment.relowner;
  IF v_expected_owner IS NULL OR EXISTS (
    SELECT 1 FROM pg_class c
    WHERE c.relnamespace=current_schema()::regnamespace
      AND c.relname=ANY(ARRAY[
        'research_evidence_project_context_revision','research_evidence_project_context_sequence_allocator',
        'research_evidence_claim_annotation_revision','research_evidence_claim_annotation_sequence_allocator',
        'research_evidence_usage_authorization_decision','research_evidence_usage_authorization_sequence_allocator'])
      AND c.relowner<>v_expected_owner
    UNION ALL
    SELECT 1 FROM pg_proc p
    WHERE p.pronamespace=current_schema()::regnamespace
      AND p.proname=ANY(ARRAY[
        'research_evidence_pack_string_array_valid','research_evidence_prepare_project_context_insert',
        'research_evidence_prepare_claim_annotation_insert','research_evidence_prepare_usage_authorization_insert'])
      AND p.proowner<>v_expected_owner
  ) THEN
    RAISE EXCEPTION 'v61 contract violation: canonical owner mismatch'
      USING ERRCODE='invalid_schema_definition';
  END IF;
  IF EXISTS (
    WITH actual AS (
      SELECT 'r'::text object_type,c.oid object_oid,acl.grantee,acl.grantor,
             acl.privilege_type,acl.is_grantable
      FROM pg_class c
      CROSS JOIN LATERAL aclexplode(
        coalesce(c.relacl,acldefault('r',c.relowner))
      ) acl
      WHERE c.relnamespace=current_schema()::regnamespace
        AND c.relname=ANY(ARRAY[
          'research_evidence_project_context_revision',
          'research_evidence_project_context_sequence_allocator',
          'research_evidence_claim_annotation_revision',
          'research_evidence_claim_annotation_sequence_allocator',
          'research_evidence_usage_authorization_decision',
          'research_evidence_usage_authorization_sequence_allocator'])
      UNION ALL
      SELECT 'f'::text,p.oid,acl.grantee,acl.grantor,
             acl.privilege_type,acl.is_grantable
      FROM pg_proc p
      CROSS JOIN LATERAL aclexplode(
        coalesce(p.proacl,acldefault('f',p.proowner))
      ) acl
      WHERE p.pronamespace=current_schema()::regnamespace
        AND p.proname=ANY(ARRAY[
          'research_evidence_pack_string_array_valid',
          'research_evidence_prepare_project_context_insert',
          'research_evidence_prepare_claim_annotation_insert',
          'research_evidence_prepare_usage_authorization_insert'])
    ), expected AS (
      SELECT 'r'::text,c.oid,v_expected_owner,v_expected_owner,
             privilege_type,false
      FROM pg_class c
      CROSS JOIN (VALUES
        ('INSERT'::text),('SELECT'),('UPDATE'),('DELETE'),('TRUNCATE'),
        ('REFERENCES'),('TRIGGER')
      ) privilege(privilege_type)
      WHERE c.relnamespace=current_schema()::regnamespace
        AND c.relname=ANY(ARRAY[
          'research_evidence_project_context_revision',
          'research_evidence_project_context_sequence_allocator',
          'research_evidence_claim_annotation_revision',
          'research_evidence_claim_annotation_sequence_allocator',
          'research_evidence_usage_authorization_decision',
          'research_evidence_usage_authorization_sequence_allocator'])
      UNION ALL
      SELECT 'f'::text,p.oid,v_expected_owner,v_expected_owner,
             'EXECUTE'::text,false
      FROM pg_proc p
      WHERE p.pronamespace=current_schema()::regnamespace
        AND p.proname=ANY(ARRAY[
          'research_evidence_pack_string_array_valid',
          'research_evidence_prepare_project_context_insert',
          'research_evidence_prepare_claim_annotation_insert',
          'research_evidence_prepare_usage_authorization_insert'])
    ), difference AS (
      (SELECT * FROM actual EXCEPT SELECT * FROM expected)
      UNION ALL
      (SELECT * FROM expected EXCEPT SELECT * FROM actual)
    )
    SELECT 1 FROM difference
  ) THEN
    RAISE EXCEPTION 'v61 contract violation: effective ACL mismatch'
      USING ERRCODE='invalid_schema_definition';
  END IF;

  IF EXISTS (
    WITH history AS (
      SELECT project_id,count(*)::integer row_count,
             min(context_sequence) minimum_sequence,
             max(context_sequence) maximum_sequence
      FROM research_evidence_project_context_revision GROUP BY project_id
    )
    SELECT 1 FROM history
    FULL JOIN research_evidence_project_context_sequence_allocator allocator
      USING(project_id)
    WHERE history.project_id IS NULL OR allocator.project_id IS NULL
       OR history.minimum_sequence<>1
       OR history.maximum_sequence<>allocator.last_sequence
       OR history.row_count<>allocator.last_sequence
  ) OR EXISTS (
    SELECT 1
    FROM research_evidence_project_context_revision current_row
    LEFT JOIN research_evidence_project_context_revision predecessor
      ON predecessor.project_id=current_row.project_id
     AND predecessor.context_sequence=current_row.context_sequence-1
    WHERE (current_row.context_sequence=1
           AND current_row.supersedes_context_revision_id IS NOT NULL)
       OR (current_row.context_sequence>1 AND (
           predecessor.id IS NULL OR
           current_row.supersedes_context_revision_id IS DISTINCT FROM predecessor.id))
  ) THEN
    RAISE EXCEPTION 'v61 contract violation: project-context allocator/history discontinuity'
      USING ERRCODE='invalid_schema_definition';
  END IF;

  IF EXISTS (
    WITH history AS (
      SELECT project_id,claim_draft_id,count(*)::integer row_count,
             min(annotation_sequence) minimum_sequence,
             max(annotation_sequence) maximum_sequence
      FROM research_evidence_claim_annotation_revision
      GROUP BY project_id,claim_draft_id
    )
    SELECT 1 FROM history
    FULL JOIN research_evidence_claim_annotation_sequence_allocator allocator
      USING(project_id,claim_draft_id)
    WHERE history.project_id IS NULL OR allocator.project_id IS NULL
       OR history.minimum_sequence<>1
       OR history.maximum_sequence<>allocator.last_sequence
       OR history.row_count<>allocator.last_sequence
  ) OR EXISTS (
    SELECT 1
    FROM research_evidence_claim_annotation_revision current_row
    LEFT JOIN research_evidence_claim_annotation_revision predecessor
      ON predecessor.project_id=current_row.project_id
     AND predecessor.claim_draft_id=current_row.claim_draft_id
     AND predecessor.annotation_sequence=current_row.annotation_sequence-1
    WHERE (current_row.annotation_sequence=1
           AND current_row.supersedes_annotation_revision_id IS NOT NULL)
       OR (current_row.annotation_sequence>1 AND (
           predecessor.id IS NULL OR
           current_row.supersedes_annotation_revision_id IS DISTINCT FROM predecessor.id))
  ) THEN
    RAISE EXCEPTION 'v61 contract violation: claim-annotation allocator/history discontinuity'
      USING ERRCODE='invalid_schema_definition';
  END IF;

  IF EXISTS (
    WITH history AS (
      SELECT project_id,claim_intake_item_id,evidence_intake_item_id,usage_scope,
             count(*)::integer row_count,min(decision_sequence) minimum_sequence,
             max(decision_sequence) maximum_sequence
      FROM research_evidence_usage_authorization_decision
      GROUP BY project_id,claim_intake_item_id,evidence_intake_item_id,usage_scope
    )
    SELECT 1 FROM history
    FULL JOIN research_evidence_usage_authorization_sequence_allocator allocator
      USING(project_id,claim_intake_item_id,evidence_intake_item_id,usage_scope)
    WHERE history.project_id IS NULL OR allocator.project_id IS NULL
       OR history.minimum_sequence<>1
       OR history.maximum_sequence<>allocator.last_sequence
       OR history.row_count<>allocator.last_sequence
  ) OR EXISTS (
    WITH ordered AS (
      SELECT current_row.*,
             lag(decision) OVER (
               PARTITION BY project_id,claim_intake_item_id,
                            evidence_intake_item_id,usage_scope
               ORDER BY decision_sequence
             ) previous_decision
      FROM research_evidence_usage_authorization_decision current_row
    )
    SELECT 1 FROM ordered current_row
    LEFT JOIN research_evidence_usage_authorization_decision predecessor
      ON predecessor.project_id=current_row.project_id
     AND predecessor.claim_intake_item_id=current_row.claim_intake_item_id
     AND predecessor.evidence_intake_item_id=current_row.evidence_intake_item_id
     AND predecessor.usage_scope=current_row.usage_scope
     AND predecessor.decision_sequence=current_row.decision_sequence-1
    WHERE (current_row.decision_sequence=1 AND (
             current_row.supersedes_decision_id IS NOT NULL
             OR current_row.decision<>'authorized'))
       OR (current_row.decision_sequence>1 AND (
             predecessor.id IS NULL
             OR current_row.supersedes_decision_id IS DISTINCT FROM predecessor.id
             OR current_row.previous_decision=current_row.decision))
  ) THEN
    RAISE EXCEPTION 'v61 contract violation: authorization allocator/history discontinuity'
      USING ERRCODE='invalid_schema_definition';
  END IF;
END $$;

COMMIT;
