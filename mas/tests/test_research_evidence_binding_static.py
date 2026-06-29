"""Static boundary tests for the additive R1.6 binding foundation."""
import ast
import hashlib
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _strip_sql_comments(text: str) -> str:
    lines = []
    for line in text.splitlines():
        marker = line.find("--")
        lines.append(line if marker == -1 else line[:marker])
    return "\n".join(lines)


def _uses_semantic_identifier(text: str, term: str) -> bool:
    """Match a complete snake/camel identifier component, ignoring comments."""
    identifiers = []
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Name):
            identifiers.append(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.append(node.attr)
        elif isinstance(node, ast.arg):
            identifiers.append(node.arg)
        elif isinstance(node, ast.keyword) and node.arg is not None:
            identifiers.append(node.arg)
        elif isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            identifiers.append(node.name)
        elif isinstance(node, ast.alias):
            identifiers.extend(
                value for value in (node.name, node.asname) if value is not None
            )
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", node.value)
        ):
            identifiers.append(node.value)

    expected = term.casefold()
    for identifier in identifiers:
        components = re.findall(
            r"[A-Z]+(?=[A-Z][a-z]|\b)|[A-Z]?[a-z]+|[0-9]+",
            identifier.replace("_", " ").replace("-", " "),
        )
        if expected in {component.casefold() for component in components}:
            return True
    return False


SQL_PATH = ROOT / "sql" / "v57_research_evidence_binding_foundation.sql"
SQL_RAW = SQL_PATH.read_text(encoding="utf-8")
SQL = _strip_sql_comments(SQL_RAW)
MODEL_TEXT = (
    ROOT / "research_evidence" / "binding_models.py"
).read_text(encoding="utf-8")
REPOSITORY_TEXT = (
    ROOT / "research_evidence" / "binding_repository.py"
).read_text(encoding="utf-8")
SERVICE_TEXT = (
    ROOT / "research_evidence" / "binding_service.py"
).read_text(encoding="utf-8")
EVIDENCE_REPOSITORY_TEXT = (
    ROOT / "knowledge" / "evidence_snapshot" / "repository.py"
).read_text(encoding="utf-8")
PACKAGE_TEXT = MODEL_TEXT + REPOSITORY_TEXT + SERVICE_TEXT


def test_v57_is_transactional_additive_and_manual():
    assert SQL.count("BEGIN;") == 1
    assert SQL.count("COMMIT;") == 1
    assert "Apply manually" in SQL_RAW
    for forbidden in ("DROP TABLE", "DROP SCHEMA", "CREATE VIEW"):
        assert forbidden not in SQL


def test_declared_prepare_function_hash_matches_normalized_body():
    function_body = SQL_RAW.split("AS $function_body$", 1)[1].split(
        "$function_body$", 1
    )[0]
    declared_hash = re.search(
        r"v_function_hash IS DISTINCT FROM '([0-9a-f]{32})'", SQL
    )
    assert declared_hash is not None
    normalized_body = re.sub(r"\s+", "", function_body)
    actual_hash = hashlib.md5(
        normalized_body.encode("utf-8"), usedforsecurity=False
    ).hexdigest()
    assert actual_hash == declared_hash.group(1)


def test_v57_creates_only_binding_ledger_and_allocator():
    assert SQL.count("CREATE TABLE IF NOT EXISTS") == 2
    assert "research_evidence_consumer_input_binding (" in SQL
    assert (
        "research_evidence_consumer_input_binding_sequence_allocator ("
        in SQL
    )
    for parent in (
        "source_blob",
        "source_snapshot",
        "approved_calculation_input",
        "research_evidence_intake_item",
        "research_evidence_intake_item_review_decision",
        "research_evidence_intake_item_freshness_assessment",
        "research_evidence_claim_support_assessment",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {parent} (" not in SQL


def test_v57_never_writes_prior_wave_or_consumer_tables():
    for table in (
        "source_blob",
        "source_snapshot",
        "candidate_fact_revision",
        "evidence_retention_event",
        "approved_calculation_input",
        "calculation_result",
        "calculation_result_input",
        "research_source_metadata_revision",
        "research_fact_metadata_revision",
        "research_claim_draft",
        "research_evidence_event",
        "research_evidence_intake",
        "research_evidence_intake_item",
        "research_evidence_intake_item_review_decision",
        "research_evidence_intake_item_freshness_assessment",
        "research_evidence_claim_support_assessment",
    ):
        assert f"INSERT INTO {table}" not in SQL
        assert f"UPDATE {table}" not in SQL
        assert f"DELETE FROM {table}" not in SQL


def test_consumer_input_identity_sequence_and_retry_are_database_owned():
    for field in (
        "project_id",
        "consumer_contract",
        "binding_set_id",
        "input_key",
        "binding_sequence",
        "supersedes_binding_id",
        "request_id",
    ):
        assert field in SQL
    for sentinel in (
        "NEW.binding_sequence IS NOT NULL",
        "NEW.supersedes_binding_id IS NOT NULL",
        "NEW.binding_sequence := v_next",
        "NEW.supersedes_binding_id := v_current_id",
        "NEW.evaluated_at := clock_timestamp()",
        "FOR UPDATE",
    ):
        assert sentinel in SQL
    assert "ORDER BY binding_sequence DESC" in REPOSITORY_TEXT
    assert "ORDER BY evaluated_at" not in REPOSITORY_TEXT


def test_reapply_checks_every_binding_check_and_server_owned_defaults():
    first_check = "('ck_recib_consumer_contract'::text,"
    section_start = SQL.index(first_check)
    section_end = SQL.index(
        "expected(name, table_name, normalized_expression)"
    )
    exact_check_section = SQL[section_start:section_end]
    for constraint in (
        "ck_recib_consumer_contract",
        "ck_recib_consumer_shape",
        "ck_recib_claim_pair_shape",
        "ck_recib_review_shape",
        "ck_recib_freshness_shape",
        "ck_recib_consumer_disposition",
        "ck_recib_json_shapes",
        "ck_recib_policy_provenance",
        "ck_recib_nonblank",
        "ck_recib_observation_fingerprint",
        "ck_recib_sequence_positive",
        "ck_recib_allocator_last_sequence",
    ):
        assert constraint in exact_check_section
    assert "server-owned fields have defaults" in SQL
    for field in (
        "calculation_kind",
        "source_snapshot_id",
        "source_blob_id",
        "source_metadata_revision_id",
        "candidate_fact_revision_id",
        "fact_metadata_revision_id",
        "availability_status",
        "retention_basis_json",
        "lineage_is_current",
        "lineage_basis_json",
        "review_decision_id",
        "review_decision_sequence",
        "review_status",
        "freshness_assessment_id",
        "freshness_assessment_sequence",
        "fresh_through",
        "freshness_status",
        "drift_status",
        "locator_resolution",
        "evidence_linkage",
        "semantic_relationship",
        "binding_sequence",
        "supersedes_binding_id",
        "evaluated_at",
    ):
        assert f"'{field}'" in SQL
    assert "allocator fields have defaults" in SQL


def test_source_status_contracts_remain_separate():
    for field in (
        "availability_status",
        "retention_basis_json",
        "lineage_is_current",
        "lineage_basis_json",
        "review_decision_id",
        "review_status",
        "freshness_assessment_id",
        "freshness_status",
        "drift_status",
        "locator_resolution",
        "evidence_linkage",
        "semantic_relationship",
    ):
        assert field in SQL
    for function_name in (
        "binding_availability_status",
        "binding_retention_basis",
        "binding_lineage_is_current",
        "binding_review_status",
        "binding_freshness_status",
        "binding_drift_status",
        "binding_locator_resolution",
        "binding_evidence_linkage",
        "binding_semantic_relationship",
        "binding_consumer_disposition",
    ):
        assert f"def {function_name}" in SERVICE_TEXT


def test_canonical_availability_helper_is_reused_not_recreated():
    assert "def fact_availability_sql(" in EVIDENCE_REPOSITORY_TEXT
    fact_available_body = EVIDENCE_REPOSITORY_TEXT.split(
        "def fact_available(", 1
    )[1]
    assert "fact_availability_sql(" in fact_available_body
    assert "evidence_repository.fact_availability_sql(" in REPOSITORY_TEXT
    assert "event.event_type" in REPOSITORY_TEXT
    assert "event.event_type IN ('tombstone', 'redact')" not in REPOSITORY_TEXT


def test_status_snapshot_is_one_statement_without_global_locks_or_ownership():
    assert "WITH request_input AS MATERIALIZED" in REPOSITORY_TEXT
    assert "evaluated_context AS MATERIALIZED" in REPOSITORY_TEXT
    assert "INSERT INTO research_evidence_consumer_input_binding" in REPOSITORY_TEXT
    assert "LOCK TABLE" not in REPOSITORY_TEXT
    assert "IN SHARE MODE" not in REPOSITORY_TEXT
    for table in (
        "evidence_retention_event",
        "research_source_metadata_revision",
        "research_fact_metadata_revision",
        "research_evidence_event",
        "research_evidence_intake_item_review_decision",
        "research_evidence_intake_item_freshness_assessment",
        "research_evidence_claim_support_assessment",
    ):
        assert table in REPOSITORY_TEXT
    prepare_body = SQL.split("AS $function_body$", 1)[1].split(
        "$function_body$", 1
    )[0]
    for table in (
        "evidence_retention_event",
        "research_source_metadata_revision",
        "research_fact_metadata_revision",
        "research_evidence_event",
        "research_evidence_intake_item_review_decision",
        "research_evidence_intake_item_freshness_assessment",
        "research_evidence_claim_support_assessment",
    ):
        assert table not in prepare_body
    for forbidden in (
        "SET TRANSACTION",
        "transaction_isolation",
        ".commit(",
        ".rollback(",
        ".close(",
    ):
        assert forbidden not in PACKAGE_TEXT


def test_calculation_shape_only_reads_exact_frozen_input_identity():
    assert "approved_calculation_input_id" in MODEL_TEXT
    assert "calculation_input.input_role = context.input_key" in REPOSITORY_TEXT
    assert (
        "calculation_input.candidate_fact_revision_id ="
        in REPOSITORY_TEXT
    )
    assert "context.candidate_fact_revision_id" in REPOSITORY_TEXT
    for forbidden in (
        "compute_automation_roi",
        "request_calculation",
        "insert_calculation_result",
    ):
        assert forbidden not in PACKAGE_TEXT


def test_scenario_shape_has_fingerprint_and_never_infers_stance():
    assert "observation_identity_version" in MODEL_TEXT
    assert "observation_identity_fingerprint" in MODEL_TEXT
    assert "semantic_relationship" in MODEL_TEXT
    for source in (MODEL_TEXT, REPOSITORY_TEXT, SERVICE_TEXT):
        assert not _uses_semantic_identifier(source, "stance")
    assert "ScenarioEvidenceObservation" not in PACKAGE_TEXT


def test_report_shapes_do_not_claim_citation_resolution():
    assert "report_evidence_register" in MODEL_TEXT
    assert "claim and claim-support assessment references must be all-or-none" in MODEL_TEXT
    for forbidden in (
        "citation_ready",
        "citation_resolved",
        "render_citation",
        "generate_report",
    ):
        assert forbidden not in PACKAGE_TEXT


def test_no_aggregate_or_existing_review_predicate_is_added():
    for forbidden in (
        "truth_status",
        "ready_for",
        "all_consumers",
        "item_is_eligible_for_future_use",
    ):
        assert forbidden not in PACKAGE_TEXT
    assert "authorize nothing" in SERVICE_TEXT


def test_feature_gate_savepoints_and_no_connection_ownership():
    assert "config.research_evidence_enabled()" in SERVICE_TEXT
    assert "if conn.autocommit" in SERVICE_TEXT
    assert "SAVEPOINT research_evidence_binding_write" in SERVICE_TEXT
    assert "SAVEPOINT research_evidence_binding_insert" in REPOSITORY_TEXT
    assert ".commit(" not in PACKAGE_TEXT
    assert ".close(" not in PACKAGE_TEXT


def test_no_prohibited_product_import_is_added():
    import_lines = "\n".join(
        line
        for line in PACKAGE_TEXT.splitlines()
        if line.startswith("import ") or line.startswith("from ")
    )
    for forbidden in (
        "api",
        "dashboard",
        "export",
        "monitoring",
        "orchestrator",
        "prompt",
        "report",
        "retrieval",
        "scenario",
        "workflow",
    ):
        assert forbidden not in import_lines.lower()
