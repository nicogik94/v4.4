"""Static architecture guards for the R2.0A-4B operator bridge.

These assert the bounded contract in source: the bridge writes only through
existing validated services (no raw SQL writes, no repository write bypass),
mechanically fixes ``internal_analysis`` (no caller-selected usage scope),
requires a typed confirmation for authorization/revocation, reuses the exact
A-4A renderer + byte budget without truncation, keeps previews read-only, and
adds no migration (v62 stays unused). The candidate-fact wrapper stays bounded.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BRIDGE = ROOT / "mas/tools/research_evidence_bridge.py"
FACT_SERVICE = ROOT / "mas/knowledge/evidence_snapshot/fact_service.py"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ─────────────────────────── no raw SQL writes ──────────────────────────


def test_bridge_has_no_raw_sql_writes():
    text = _text(BRIDGE)
    for write in ("INSERT INTO", "UPDATE ", "DELETE FROM", "TRUNCATE", "DROP "):
        assert write not in text, f"bridge must not contain raw SQL write: {write!r}"


def test_bridge_does_not_bypass_write_repositories():
    text = _text(BRIDGE)
    # Writes must flow through *_service; the CLI never calls low-level inserts.
    forbidden = (
        "insert_fact(",
        "pack_repository",
        "repo.insert",
        "insert_intake(",
        "insert_item(",
        "insert_decision(",
        "insert_assessment(",
        "insert_usage_authorization_decision(",
        "insert_source_metadata_revision(",
    )
    present = [token for token in forbidden if token in text]
    assert present == [], f"bridge bypasses a write repository: {present}"


def test_candidate_fact_created_through_bounded_service_only():
    text = _text(BRIDGE)
    assert (
        "from knowledge.evidence_snapshot.fact_service import" in text
        and "create_candidate_fact_revision" in text
    )


# ─────────────────────────── usage scope is fixed ───────────────────────


def test_usage_scope_is_mechanically_fixed_to_internal_analysis():
    text = _text(BRIDGE)
    assert 'FIXED_USAGE_SCOPE_VALUE = "internal_analysis"' in text
    # No operator-selectable usage scope option anywhere.
    assert "--usage-scope" not in text
    assert "--usage_scope" not in text
    assert 'add_argument("--scope"' not in text
    # The wider disclosure scopes are only ever named as things authorization
    # does NOT extend to — never passed as a write scope.
    assert "usage_scope=UsageScope.OPERATOR_DOSSIER" not in text
    assert "usage_scope=UsageScope.CLIENT_REPORT" not in text


def test_authorization_writes_fix_internal_analysis():
    text = _text(BRIDGE)
    assert "usage_scope=FIXED_USAGE_SCOPE_VALUE" in text
    # Both authorize and revoke go through one guarded helper.
    assert "_run_authorization(" in text
    assert "record_usage_authorization_decision(" in text


# ─────────────────────────── confirmation gate ──────────────────────────


def test_authorization_requires_typed_confirmation():
    text = _text(BRIDGE)
    assert "_check_confirmation(" in text
    assert "_expected_confirmation(" in text
    # Confirmation echoes project + claim + evidence identities.
    assert "f\"{project_id} {claim_item} {evidence_item}\"" in text
    # A failed confirmation must never emit a COMMIT.
    assert "confirmation did not match" in text


def test_authorization_preview_names_out_of_scope_uses():
    text = _text(BRIDGE)
    for scope in ("operator_dossier", "client_report", "exports", "publication"):
        assert scope in text


# ─────────────────────────── byte budget reuse ──────────────────────────


def test_bridge_reuses_a4a_renderer_and_budget_without_truncation():
    text = _text(BRIDGE)
    assert "render_research_evidence_block(" in text
    assert "RESEARCH_EVIDENCE_PROMPT_BUDGET_BYTES" in text
    # The four exact preview outcomes are present and there is no partial block.
    for status in (
        "EMPTY",
        "WITHIN_LIMIT",
        "WOULD_BLOCK_PROMPT_OVERFLOW",
        "WOULD_BLOCK_CAPACITY_OVERFLOW",
    ):
        assert status in text
    # No truncation / "first N" slicing of the rendered block.
    assert "block[:" not in text
    assert ".truncate" not in text


# ─────────────────────────── read-only previews ─────────────────────────


def test_previews_are_read_only_and_never_commit():
    text = _text(BRIDGE)
    assert "def _configure_readonly_connection(conn):" in text
    assert "conn.read_only = True" in text
    # Read runner never commits.
    body = text.split("def _run_readonly(")[1].split("def ")[0]
    assert ".commit(" not in body


# ─────────────────────────── no migration / v62 ─────────────────────────


def test_bridge_adds_no_migration_and_leaves_v62_unused():
    text = _text(BRIDGE)
    # No migration file is referenced or applied (v62 stays unused).
    assert "v62_" not in text
    assert "apply_v62" not in text
    assert "CREATE TABLE" not in text
    assert "CREATE MIGRATION" not in text


def test_no_new_sql_migration_files_added():
    sql_dir = ROOT / "mas/sql"
    assert not list(sql_dir.glob("v62_*.sql"))


# ─────────────────────────── candidate-fact wrapper ─────────────────────


def test_fact_service_is_a_bounded_validated_wrapper():
    text = _text(FACT_SERVICE)
    # Feature gated on the snapshot flag (touches v47 facts).
    assert "config.evidence_snapshot_enabled()" in text
    # Rejects autocommit and preserves caller transaction ownership.
    assert "conn.autocommit" in text
    assert ".commit(" not in text
    # Uses the canonical v47 validation + repository seam; adds no new storage.
    assert "from .validation import" in text
    assert "ValidatedFact" in text
    assert "validate_fact" in text
    assert "repo.insert_fact(" in text
    assert "CREATE TABLE" not in text
    # Binds the fact to an existing same-project snapshot; never infers from
    # Knowledge and never creates authorization.
    assert "source_snapshot" in text
    assert "record_usage_authorization_decision" not in text
    assert "knowledge_" not in text


# ─────────────────── MAJOR 1: canonical candidate-fact validation ────────────


def test_fact_service_revalidates_directly_constructed_facts():
    text = _text(FACT_SERVICE)
    # It does not trust isinstance alone: it reconstructs a canonical fact from
    # every field through validate_fact and persists ONLY that canonical value.
    assert "def _canonicalize(" in text
    assert "validate_fact(" in text
    assert "canonical = _canonicalize(fact)" in text
    assert "fact=canonical" in text
    # Requires an EXPLICITLY pinned READ COMMITTED: isolation_level=None (server
    # default) is rejected, and REPEATABLE READ / SERIALIZABLE stay rejected.
    assert "READ_COMMITTED" in text
    assert "_require_caller_owned_read_committed(" in text
    assert "isolation is None" in text
    assert "does not prove READ COMMITTED" in text
    # Canonicalization is called before the savepoint context manager opens.
    body = text.split("def create_candidate_fact_revision(")[1]
    assert body.index("_canonicalize(fact)") < body.index("with _fact_write(conn)")


# ─────────────────── P2-A: non-finite numeric facts rejected ─────────────────


def test_fact_service_rejects_non_finite_numeric_before_any_sql():
    text = _text(FACT_SERVICE)
    # Decimal.is_finite() is the authoritative check, with a fixed, non-echoing
    # message; it is enforced inside canonicalization (before any SAVEPOINT/SQL).
    assert "def _reject_non_finite_numeric(" in text
    assert ".is_finite()" in text
    assert '"numeric candidate facts must be finite"' in text
    # The guard runs both before AND after validate_fact within _canonicalize, so
    # every numeric profile is rejected uniformly (a bounded comparison on a NaN
    # would otherwise raise a non-canonical error), and always before the savepoint.
    canon = text.split("def _canonicalize(")[1].split("\ndef ", 1)[0]
    assert canon.count("_reject_non_finite_numeric(") == 2
    assert canon.index("_reject_non_finite_numeric(fact.numeric_value)") < (
        canon.index("canonical = validate_fact(")
    )


def test_bridge_fact_create_rejects_non_finite_before_its_validate_fact():
    # The CLI runs its own validate_fact; for a bound-comparing profile a NaN
    # there raises a non-canonical decimal.InvalidOperation. cmd_fact_create must
    # reject a non-finite numeric value with the canonical FactValidationError
    # BEFORE that call.
    text = _text(BRIDGE)
    body = text.split("def cmd_fact_create(")[1].split("\ndef cmd_", 1)[0]
    assert ".is_finite()" in body
    assert '"numeric candidate facts must be finite"' in body
    assert body.index("not numeric_value.is_finite()") < (
        body.index("validated = validate_fact(")
    )


# ─────────────────── MAJOR 2: catalog-exact write preflight ──────────────────


def test_bridge_write_path_enforces_failclosed_preflight():
    text = _text(BRIDGE)
    # A single shared fail-closed enforcement helper, wired into both write runners.
    assert "def _enforce_write_preflight(" in text
    assert text.count("_enforce_write_preflight(conn, args)") >= 2
    assert "BridgePreflightError" in text
    # An explicitly configured DATABASE_URL env var is required for writes.
    assert "def _require_configured_database_url(" in text
    assert 'os.getenv("DATABASE_URL"' in text
    # The connection seam uses the CURRENT DATABASE_URL env var (the one the guard
    # checks), not only the import-time config.DATABASE_URL snapshot, so a write
    # can never target the stale localhost fallback after passing the env guard.
    seam = text.split("def _open_authoritative_connection(")[1].split("\ndef ", 1)[0]
    assert 'os.environ.get("DATABASE_URL"' in seam
    # READ COMMITTED is verified, not silently assumed; the old silent catch is gone.
    assert "SHOW transaction_isolation" in text
    assert "server default is READ COMMITTED" not in text
    # Runtime identity fingerprint is derived + required + compared, and now
    # binds the connection namespace (current_schema).
    assert "def _runtime_fingerprint(" in text
    assert "inet_server_addr" in text and "inet_server_port" in text
    assert "--expect-runtime-fingerprint" in text
    assert 'identity["current_schema"]' in text


# ─────────────── P2-B: socket-safe runtime cluster fingerprint ───────────────


def test_runtime_identity_is_socket_safe_and_fails_closed():
    text = _text(BRIDGE)
    # Over a Unix socket inet_server_addr()/port() are NULL, so the identity folds
    # in a stable, non-secret cluster discriminator: the control-file
    # system_identifier and the configured `port` GUC.
    assert "pg_control_system()" in text
    assert "system_identifier" in text
    assert "current_setting('port'" in text
    assert 'identity["system_identifier"]' in text
    assert 'identity["configured_port"]' in text
    # Fails closed when the cluster identity cannot be read; never a silent
    # fallback to the collision-prone address/port-only fingerprint.
    ident = text.split("def _runtime_identity(")[1].split("\ndef ", 1)[0]
    assert "BridgePreflightError" in ident
    assert "refusing to fingerprint an ambiguous runtime" in ident
    # No DSN, credential, or data directory is ever part of the emitted identity.
    for secret in ("DATABASE_URL", "password", "data_directory", "conn.info"):
        assert secret not in ident
    # Clone-safe: system_identifier is copied by a physical base backup, so the
    # fingerprint also folds in a non-emitted per-running-cluster socket endpoint.
    assert "def _runtime_socket_endpoint(" in text
    fp = text.split("def _runtime_fingerprint(")[1].split("\ndef ", 1)[0]
    assert "_runtime_socket_endpoint(conn)" in fp
    # The socket endpoint is read (non-emitted) and never enters _runtime_identity.
    assert "_runtime_socket_endpoint(" not in ident


def test_bridge_has_a_catalog_exact_manifest():
    text = _text(BRIDGE)
    # A bounded, closed catalog manifest (relations+relkind, functions+signature,
    # write-boundary triggers, dedicated v59/v60 relations, roles).
    for symbol in (
        "CATALOG_MAIN_RELATIONS", "CATALOG_DEDICATED_RELATIONS",
        "CATALOG_FUNCTIONS", "CATALOG_TRIGGERS", "CATALOG_DEDICATED_SCHEMA",
        "def _collect_catalog(", "def _trigger_state(", "def _relation_relkind(",
        "def _function_semantics(", "def _certify_function(",
        "def _topology_security_findings(",
        "def _namespace_findings(", "REQUIRED_ROLE_ATTRIBUTES", "def _role_ready(",
    ):
        assert symbol in text, symbol
    # Append-only mutation guards for every ledger the bridge writes.
    for tgname in (
        "trg_cfr_no_mutation", "trg_rsmr_no_mutation", "trg_rfmr_no_mutation",
        "trg_rcd_no_mutation", "trg_ree_no_mutation", "trg_rei_no_mutation",
        "trg_reii_no_mutation", "trg_reird_no_mutation", "trg_reifa_no_mutation",
        "trg_recsa_no_mutation", "trg_repcr_no_mutation", "trg_recar_no_mutation",
        "trg_reuad_no_mutation",
    ):
        assert tgname in text, tgname
    # Preparatory / validation triggers are checked too.
    assert "trg_ree_prepare_insert" in text
    assert "trg_reii_validate_snapshot" in text
    assert "slicea_reject_mutation" in text
    # v59/v60 topology security + connection namespace are verified.
    assert "has_schema_privilege" in text
    assert "pg_auth_members" in text
    assert "def _current_schema(" in text
    # The seven readiness categories are distinguished.
    for flag in ("relations_ready", "functions_ready", "triggers_ready",
                 "constraints_ready", "roles_ready", "topology_security_ready",
                 "namespace_ready"):
        assert flag in text, flag


def test_trigger_check_is_schema_and_semantics_exact():
    text = _text(BRIDGE)
    # MAJOR 1: the trigger's bound function is verified by SCHEMA + identity
    # args + result, not proname alone (so a same-name decoy-schema function is
    # rejected). The manifest function must resolve to current_schema.
    assert "func_schema" in text and "func_name" in text
    assert "func_args" in text and "func_result" in text
    assert 'state["func_schema"] != cur_schema' in text
    # MAJOR 2: complete trigger semantics are frozen (args, column list, WHEN
    # qual, deferrability, constraint identity, not-internal, tgtype, enabled).
    for col in ("tgnargs", "tgargs", "tgattr", "tgqual", "tgdeferrable",
                "tginitdeferred", "tgconstraint", "tgisinternal", "tgtype",
                "tgenabled"):
        assert col in text, col
    assert "def _trigger_problem(" in text
    # MAJOR 2: per-ledger inventory rejects EXTRA triggers, not just missing.
    assert "def _ledger_trigger_names(" in text
    assert '":extra"' in text or ":extra" in text


def test_function_manifest_freezes_semantics_not_only_identity():
    """Iteration-4 MAJOR: function BODY + properties are frozen, not just identity.

    Identity alone is forgeable — CREATE OR REPLACE with the same schema, name
    and signature but a `RETURN NEW` body preserves every identity fact while
    neutralising the append-only guard.
    """
    text = _text(BRIDGE)
    # Every frozen catalog property is actually read from pg_proc.
    for column in ("prokind", "proretset", "pronargdefaults", "provariadic",
                   "prosecdef", "provolatile", "proisstrict", "proparallel",
                   "proconfig", "md5(p.prosrc)", "prorettype", "lanname"):
        assert column in text, column
    # ...and compared against the manifest.
    assert "_FUNCTION_SEMANTIC_KEYS" in text
    for key in ("prosrc_md5", "prosecdef", "proconfig", "provolatile",
                "proisstrict", "proparallel", "prokind", "proretset"):
        assert f'"{key}"' in text, key
    # Overload drift is rejected by an exact count.
    assert '"overloads"' in text
    assert "overloads=" in text
    # The security posture is NOT assumed uniform: the v47 guard is SECURITY
    # INVOKER with no proconfig; the v52-v61 prepare functions are SECURITY
    # DEFINER with a fixed search_path.
    assert "_SECURITY_INVOKER_NO_CONFIG" in text
    assert "_SECURITY_DEFINER_PG_CATALOG" in text
    assert "search_path=pg_catalog" in text
    # Every protected trigger function carries a frozen body fingerprint.
    import tools.research_evidence_bridge as bridge
    assert len(bridge.CATALOG_FUNCTIONS) == 9
    for name, spec in bridge.CATALOG_FUNCTIONS.items():
        assert len(spec["prosrc_md5"]) == 32, name
        assert spec["overloads"] == 1, name
        assert spec["result"] == "trigger", name
    assert bridge.CATALOG_FUNCTIONS["slicea_reject_mutation"]["prosecdef"] is False
    assert bridge.CATALOG_FUNCTIONS["slicea_reject_mutation"]["proconfig"] is None
    for name, spec in bridge.CATALOG_FUNCTIONS.items():
        if name == "slicea_reject_mutation":
            continue
        assert spec["prosecdef"] is True, name
        assert spec["proconfig"] == ("search_path=pg_catalog",), name
    # Body fingerprints are distinct per function (no copy-paste placeholder).
    fingerprints = {s["prosrc_md5"] for s in bridge.CATALOG_FUNCTIONS.values()}
    assert len(fingerprints) == 9


def test_trigger_binds_to_the_certified_function_oid():
    """The trigger must bind to the exact certified function, by OID."""
    text = _text(BRIDGE)
    assert "t.tgfoid" in text
    assert '"tgfoid"' in text
    assert "certified_oid" in text
    assert 'state["tgfoid"] != certified_oid' in text
    # A function that failed certification fails its triggers closed.
    assert "func_not_certified" in text
    assert "certified_oids" in text


# ───────── ITERATION 5: load-bearing request-id uniqueness ──────────────────


def test_constraint_manifest_covers_every_request_id_write_path():
    """Iteration-5 MAJOR: the idempotency contract is frozen as a catalog fact.

    Each request-id-bearing bridge write promises an idempotent retry, and each
    repository implements that promise by recovering from the *named* UNIQUE
    violation. The manifest must therefore cover exactly those constraints.
    """
    import tools.research_evidence_bridge as bridge

    manifest = {
        conname: (relation, columns)
        for relation, conname, columns in bridge.CATALOG_CONSTRAINTS
    }
    assert manifest == {
        "uq_reird_item_request": (
            "research_evidence_intake_item_review_decision",
            ("project_id", "research_evidence_intake_item_id", "request_id"),
        ),
        "uq_reifa_item_request": (
            "research_evidence_intake_item_freshness_assessment",
            ("project_id", "research_evidence_intake_item_id", "request_id"),
        ),
        "uq_recsa_pair_request": (
            "research_evidence_claim_support_assessment",
            ("project_id", "claim_intake_item_id", "evidence_intake_item_id",
             "request_id"),
        ),
        "uq_repcr_project_request": (
            "research_evidence_project_context_revision",
            ("project_id", "request_id"),
        ),
        "uq_recar_claim_request": (
            "research_evidence_claim_annotation_revision",
            ("project_id", "claim_draft_id", "request_id"),
        ),
        "uq_reuad_scope_request": (
            "research_evidence_usage_authorization_decision",
            ("project_id", "claim_intake_item_id", "evidence_intake_item_id",
             "usage_scope", "request_id"),
        ),
    }
    # Every manifest constraint's last column is the request key it protects.
    for _relation, conname, columns in bridge.CATALOG_CONSTRAINTS:
        assert columns[-1] == "request_id", conname


def test_constraint_manifest_matches_the_ratified_migrations():
    """Repository truth, not the manifest, is authoritative for name + columns.

    Parses each ``CONSTRAINT <name> UNIQUE (...)`` clause out of the ratified
    v54/v55/v56/v61 migration text and compares it to the frozen manifest, so the
    manifest cannot drift away from the migrations it claims to mirror.
    """
    import re

    import tools.research_evidence_bridge as bridge

    sql = "\n".join(
        (ROOT / "mas/sql" / name).read_text(encoding="utf-8")
        for name in (
            "v54_research_evidence_review_foundation.sql",
            "v55_research_evidence_freshness_foundation.sql",
            "v56_research_evidence_claim_support_foundation.sql",
            "v61_research_evidence_pack_foundation.sql",
        )
    )
    for _relation, conname, columns in bridge.CATALOG_CONSTRAINTS:
        match = re.search(
            r"CONSTRAINT\s+" + conname + r"\s+UNIQUE\s*\(([^)]*)\)", sql
        )
        assert match is not None, f"{conname} not found in the ratified migrations"
        declared = tuple(
            part.strip() for part in match.group(1).split(",") if part.strip()
        )
        assert declared == columns, (conname, declared, columns)


def test_constraint_checks_are_catalog_exact_and_index_backed():
    text = _text(BRIDGE)
    assert "CATALOG_CONSTRAINTS" in text
    assert "def _constraint_state(" in text
    assert "def _constraint_problem(" in text
    # Constraint-level facts: type, ordered key columns, validity, deferrability.
    for column in ("con.contype", "con.convalidated", "con.condeferrable",
                   "con.condeferred", "con.conkey", "con.conindid",
                   "con.conrelid"):
        assert column in text, column
    # Ordered conkey resolution (a set comparison would accept a permutation).
    assert "WITH ORDINALITY" in text
    assert "ORDER BY k.ord" in text
    # The backing index actually enforces it: same relation, unique, valid,
    # ready, live, immediate, neither an expression nor a partial index.
    for column in ("i.indrelid", "i.indisunique", "i.indisvalid", "i.indisready",
                   "i.indislive", "i.indimmediate", "i.indexprs", "i.indpred"):
        assert column in text, column
    for reason in ("index_missing", "index_relation", "index_not_unique",
                   "index_not_valid", "index_not_ready", "index_not_live",
                   "index_not_immediate", "index_expression", "index_partial",
                   "contype", "columns", "not_validated", "deferrable",
                   "initially_deferred"):
        assert f'"{reason}"' in text, reason


def test_constraints_ready_is_wired_into_every_write_verdict():
    text = _text(BRIDGE)
    # Collected, aggregated, enforced before a write, and reported.
    assert '"constraints_ready": not bad_constraints' in text
    catalog_ready = text.split("def _catalog_ready(")[1].split("\ndef ")[0]
    assert "constraints_ready" in catalog_ready
    preflight_enforcement = text.split("def _enforce_write_preflight(")[1].split("\ndef ")[0]
    assert '("constraints_ready"' in preflight_enforcement
    cmd = text.split("def cmd_preflight(")[1].split("\ndef ")[0]
    assert "constraints_ready=constraints_ready" in cmd
    assert "bad_constraints=catalog.get(" in cmd
    # research/fact/overall verdicts derive from _catalog_ready, so the new
    # category gates all three.
    assert "and catalog_ready" in cmd
    assert "fact_writes_allowed = research_writes_allowed and snapshot_enabled" in cmd
    assert "writes_allowed = research_writes_allowed and requested_target_ready" in cmd


def test_bridge_never_repairs_a_drifted_constraint():
    text = _text(BRIDGE)
    # The bridge diagnoses and blocks; it never issues remedial DDL.
    for ddl in ("ADD CONSTRAINT", "DROP CONSTRAINT", "CREATE UNIQUE INDEX",
                "CREATE INDEX", "ALTER TABLE"):
        assert ddl not in text, ddl


def test_role_escalation_is_recursive():
    text = _text(BRIDGE)
    # MAJOR 3: runtime reachability uses a recursive traversal, not a fixed join.
    assert "WITH RECURSIVE" in text
    assert "runtime_role_escalation" in text
    # Canonical membership options (admin/inherit/set) are all validated.
    assert "set_option" in text and "inherit_option" in text and "admin_option" in text
    assert "owner_membership_options" in text


def test_preflight_readiness_fidelity_flags_present():
    text = _text(BRIDGE)
    # MINOR 2: preflight distinguishes whole-topology eligibility, fact-write
    # eligibility, supplied-target readiness, and the overall conjunction.
    for flag in ("research_writes_allowed", "fact_writes_allowed",
                 "requested_target_ready", "writes_allowed"):
        assert flag in text, flag


# ─────────────────── MAJOR 3: true trace inspection ─────────────────────────


def test_trace_inspect_reads_persisted_state_not_a_simulation():
    text = _text(BRIDGE)
    # It never runs the live consumer nor a fresh simulated consumption.
    assert "load_research_evidence_consumption" not in text
    assert "SimpleNamespace" not in text
    # It reads the persisted ProjectState from state_snapshots (read-only)…
    assert "FROM state_snapshots" in text
    assert "ProjectState.model_validate(" in text
    # …and never through store.load (which could create tables / a write pool).
    assert "store.load" not in text
    # …and reports the stored attestation via the canonical impact builder.
    assert "build_phase_research_evidence_impact(" in text
    assert "not_recorded" in text


def test_trace_inspect_omits_unavailable_rendered_bytes():
    # MAJOR 3: the impact summary does not expose block_bytes, so the trace phase
    # builders omit rendered_utf8_bytes entirely rather than fabricating a 0.
    text = _text(BRIDGE)
    for builder in ("def _empty_phase_trace(", "def _phase_trace_from_impact("):
        body = text.split(builder)[1].split("\ndef ")[0]
        assert "rendered_utf8_bytes" not in body, builder
    # projection-preview remains the command that reports the rendered size.
    assert "rendered_utf8_bytes" in text.split("command: projection-preview")[1]


# ─────────── REVIEW FINDING 1: direct execution bootstrap ───────────


def test_bridge_bootstraps_repo_root_before_application_imports():
    """`python mas/tools/research_evidence_bridge.py` must import cleanly.

    Python puts `mas/tools` at sys.path[0] for a direct invocation, so the
    application imports need the repository's `mas` root on sys.path FIRST.
    """
    text = _text(BRIDGE)
    bootstrap = "sys.path.insert(0, str(ROOT))"
    assert bootstrap in text
    # The root is derived from this file — never an environment-specific
    # absolute path and never a PYTHONPATH requirement pushed onto the operator.
    assert "ROOT = Path(__file__).resolve().parents[1]" in text
    assert "/home/" not in text and "C:\\" not in text
    # The bootstrap precedes every application import.
    bootstrap_at = text.index(bootstrap)
    for application_import in ("import config", "import psycopg"):
        assert text.index(application_import) > bootstrap_at, application_import


def test_bridge_bootstrap_matches_the_repository_tool_pattern():
    """The same bounded bootstrap the other repository tools already use."""
    text = _text(BRIDGE)
    reference = _text(ROOT / "mas/tools/cdp_review.py")
    for line in (
        "ROOT = Path(__file__).resolve().parents[1]",
        "if str(ROOT) not in sys.path:",
        "sys.path.insert(0, str(ROOT))",
    ):
        assert line in reference, line
        assert line in text, line


# ─────────── REVIEW FINDING 2: absent state_snapshots is probed ───────────


def test_trace_inspect_probes_state_snapshots_existence():
    """Absence is detected by a catalog probe, never by catching a DB error."""
    text = _text(BRIDGE)
    body = text.split("def cmd_trace_inspect(")[1].split("\ndef ")[0]
    # A read-only existence probe runs BEFORE the SELECT…
    assert "to_regclass('state_snapshots')" in body
    assert body.index("to_regclass('state_snapshots')") < body.index(
        "FROM state_snapshots"
    )
    assert "state_table_present" in body
    # …so no exception recovery is needed and no arbitrary database error can be
    # reinterpreted as absence. Checked against CODE only, ignoring comments.
    code = "\n".join(
        line for line in body.splitlines() if not line.lstrip().startswith("#")
    )
    assert "UndefinedTable" not in code
    assert "psycopg.errors" not in code
    assert "sqlstate" not in code
    # The tool still never creates the relation it reads.
    assert "CREATE TABLE" not in body


def test_trace_inspect_reports_invalid_state():
    # A malformed persisted ProjectState is reported as invalid_state, not
    # not_recorded, and never falls back to the current projection.
    text = _text(BRIDGE)
    assert "invalid_state" in text
    assert "_TRACE_INVALID_STATE" in text


def test_trace_inspect_impact_builder_only_called_inside_error_boundary():
    """REVIEW FINDING 3: decoding the persisted attestation is a validation
    boundary. The canonical impact builder can raise on a model-valid state whose
    nested RE attestation is malformed, so its ONLY call site must sit inside a
    bounded try/except that marks the history invalid_state — never bare.
    """
    text = _text(BRIDGE)
    body = text.split("def cmd_trace_inspect(")[1].split("\ndef ")[0]
    # Invoked exactly once, and inside the bounded boundary.
    assert body.count("build_phase_research_evidence_impact(") == 1
    call_at = body.index("build_phase_research_evidence_impact(")
    before, after = body[:call_at], body[call_at:]
    # A try: opens the boundary immediately before the call, with no intervening
    # except between that try and the call…
    last_try = before.rfind("try:")
    assert last_try != -1
    assert "except" not in before[last_try:]
    # …and its handler marks the persisted history invalid rather than letting
    # the exception escape as a generic command error.
    assert "except Exception as exc:" in after
    handler = after.split("except Exception as exc:")[1]
    assert "state_valid = False" in handler
    assert "reconstructed" in handler
    # The boundary never falls back to the live projection or consumer.
    assert "project_research_evidence_presentation" not in body
    assert "store.load" not in body


def test_projection_preview_capacity_overflow_omits_rendered_bytes():
    # MINOR 1: no fabricated zero rendered-byte value anywhere; the capacity
    # overflow branch (no block rendered) omits the key entirely.
    text = _text(BRIDGE)
    assert '"rendered_utf8_bytes": 0' not in text
    body = text.split("def cmd_projection_preview(")[1].split("\ndef ")[0]
    capacity_branch = body.split("WOULD_BLOCK_CAPACITY_OVERFLOW")[1].split("}")[0]
    assert "rendered_utf8_bytes" not in capacity_branch


# ─────────────────── MAJOR 4: authorization preview source identity ──────────


def test_authorization_preview_uses_evidence_source_metadata():
    text = _text(BRIDGE)
    # The evidence citation label comes from the EVIDENCE endpoint's metadata…
    assert "evidence_ctx.source_metadata_revision_id" in text
    assert '"evidence_source_citation_label"' in text
    # …the claim label is exposed separately, never collapsed into one field.
    assert "claim_ctx.source_metadata_revision_id" in text
    assert '"claim_source_citation_label"' in text
    assert '"source_citation_label"' not in text
