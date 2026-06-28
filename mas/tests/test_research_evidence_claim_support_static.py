"""Static boundary tests for the additive R1.5 claim-support foundation."""
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


SQL_PATH = ROOT / "sql" / "v56_research_evidence_claim_support_foundation.sql"
SQL_RAW = SQL_PATH.read_text(encoding="utf-8")
SQL = _strip_sql_comments(SQL_RAW)
MODEL_TEXT = (
    ROOT / "research_evidence" / "claim_support_models.py"
).read_text(encoding="utf-8")
REPOSITORY_TEXT = (
    ROOT / "research_evidence" / "claim_support_repository.py"
).read_text(encoding="utf-8")
SERVICE_TEXT = (
    ROOT / "research_evidence" / "claim_support_service.py"
).read_text(encoding="utf-8")
PACKAGE_TEXT = MODEL_TEXT + REPOSITORY_TEXT + SERVICE_TEXT


def test_v56_is_transactional_additive_and_manual():
    assert SQL.count("BEGIN;") == 1
    assert SQL.count("COMMIT;") == 1
    assert "Apply manually" in SQL_RAW
    for forbidden in ("DROP TABLE", "DROP SCHEMA", "CREATE VIEW"):
        assert forbidden not in SQL


def test_v56_creates_only_pair_ledger_and_internal_allocator():
    assert SQL.count("CREATE TABLE IF NOT EXISTS") == 2
    assert "research_evidence_claim_support_assessment (" in SQL
    assert "research_evidence_claim_support_sequence_allocator (" in SQL
    for parent in (
        "source_blob",
        "source_snapshot",
        "research_claim_draft",
        "research_evidence_intake_item",
        "research_evidence_intake_item_review_decision",
        "research_evidence_intake_item_freshness_assessment",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {parent} (" not in SQL


def test_v56_never_writes_prior_wave_tables():
    for table in (
        "source_blob",
        "source_snapshot",
        "candidate_fact_revision",
        "evidence_retention_event",
        "research_source_metadata_revision",
        "research_fact_metadata_revision",
        "research_claim_draft",
        "research_evidence_event",
        "research_evidence_intake",
        "research_evidence_intake_item",
        "research_evidence_intake_item_review_decision",
        "research_evidence_intake_item_freshness_assessment",
    ):
        assert f"INSERT INTO {table}" not in SQL
        assert f"UPDATE {table}" not in SQL
        assert f"DELETE FROM {table}" not in SQL


def test_pair_identity_sequence_retry_and_predecessor_are_database_owned():
    for field in (
        "project_id",
        "claim_intake_item_id",
        "evidence_intake_item_id",
        "assessment_sequence",
        "supersedes_assessment_id",
        "request_id",
    ):
        assert field in SQL
    for sentinel in (
        "NEW.assessment_sequence IS NOT NULL",
        "NEW.supersedes_assessment_id IS NOT NULL",
        "NEW.assessment_sequence := v_next",
        "NEW.supersedes_assessment_id := v_current_id",
        "NEW.assessed_at := clock_timestamp()",
        "FOR UPDATE",
    ):
        assert sentinel in SQL
    assert "ORDER BY assessment_sequence DESC" in REPOSITORY_TEXT
    assert "ORDER BY assessed_at" not in REPOSITORY_TEXT


def test_claim_and_evidence_shapes_are_separate_same_project_lookups():
    prepare = SQL.split(
        "CREATE FUNCTION research_evidence_prepare_claim_support_insert()", 1
    )[1]
    assert "item.item_kind = ''claim_draft''" in prepare
    assert "item.item_kind = ''candidate_fact''" in prepare
    assert prepare.count("item.project_id = $2") >= 2
    assert "claim_intake.research_evidence_intake_id =" not in prepare
    assert "evidence_intake.research_evidence_intake_id =" not in prepare


def test_dimensions_and_rationales_are_independent_operator_declarations():
    for field in (
        "locator_resolution",
        "locator_rationale",
        "evidence_linkage",
        "evidence_linkage_rationale",
        "semantic_relationship",
        "semantic_relationship_rationale",
    ):
        assert field in SQL
        assert field in MODEL_TEXT
    assert "NEW.locator_resolution :=" not in SQL
    assert "NEW.evidence_linkage :=" not in SQL
    assert "NEW.semantic_relationship :=" not in SQL
    assert "not_linked" in SQL


def test_separate_read_inputs_do_not_create_aggregate_readiness():
    for function_name in (
        "claim_support_claim_is_available",
        "claim_support_evidence_is_available",
        "claim_support_claim_lineage_is_current",
        "claim_support_evidence_lineage_is_current",
        "claim_support_claim_review_decision",
        "claim_support_evidence_review_decision",
        "claim_support_evidence_freshness_status_as_of",
        "claim_support_locator_resolution",
        "claim_support_evidence_linkage",
        "claim_support_semantic_relationship",
    ):
        assert f"def {function_name}" in SERVICE_TEXT
    for forbidden in (
        "citation_ready",
        "ready_for",
        "eligible_for",
        "item_is_eligible_for_future_use",
    ):
        assert forbidden not in PACKAGE_TEXT


def test_feature_gate_savepoints_and_no_connection_ownership():
    assert "config.research_evidence_enabled()" in SERVICE_TEXT
    assert "if conn.autocommit" in SERVICE_TEXT
    assert "SAVEPOINT research_evidence_claim_support_write" in SERVICE_TEXT
    assert "SAVEPOINT research_evidence_claim_support_insert" in REPOSITORY_TEXT
    assert ".commit(" not in PACKAGE_TEXT
    assert ".close(" not in PACKAGE_TEXT


def test_no_prohibited_product_surface_is_added():
    import_lines = "\n".join(
        line
        for line in PACKAGE_TEXT.splitlines()
        if line.startswith("import ") or line.startswith("from ")
    )
    for forbidden in (
        "api",
        "calculation",
        "dashboard",
        "export",
        "monitoring",
        "prompt",
        "report",
        "retrieval",
        "scenario",
        "workflow",
    ):
        assert forbidden not in import_lines.lower()
