"""Static boundary tests for the additive R1.3 review foundation."""
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


SQL_PATH = ROOT / "sql" / "v54_research_evidence_review_foundation.sql"
SQL_RAW = SQL_PATH.read_text(encoding="utf-8")
SQL = _strip_sql_comments(SQL_RAW)
PACKAGE_FILES = (
    ROOT / "research_evidence" / "review_models.py",
    ROOT / "research_evidence" / "review_repository.py",
    ROOT / "research_evidence" / "review_service.py",
)
PACKAGE_TEXT = "\n".join(path.read_text(encoding="utf-8") for path in PACKAGE_FILES)


def test_v54_is_transactional_additive_and_manual():
    assert SQL.count("BEGIN;") == 1
    assert SQL.count("COMMIT;") == 1
    assert "DROP TABLE" not in SQL
    assert "DROP SCHEMA" not in SQL
    assert "CREATE VIEW" not in SQL
    assert "CREATE INDEX CONCURRENTLY" not in SQL
    assert "psql " in SQL_RAW


def test_v54_creates_only_review_decision_and_internal_allocator():
    assert SQL.count("CREATE TABLE IF NOT EXISTS ") == 2
    assert (
        "CREATE TABLE IF NOT EXISTS "
        "research_evidence_intake_item_review_decision (" in SQL
    )
    assert (
        "CREATE TABLE IF NOT EXISTS "
        "research_evidence_item_review_sequence_allocator (" in SQL
    )
    for parent in (
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
    ):
        assert f"CREATE TABLE IF NOT EXISTS {parent} (" not in SQL


def test_v54_never_writes_prior_vertical_tables():
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
    ):
        assert f"INSERT INTO {table}" not in SQL
        assert f"UPDATE {table}" not in SQL
        assert f"DELETE FROM {table}" not in SQL


def test_decision_contract_is_item_scoped_and_has_exact_types():
    for decision_type in (
        "'approved'",
        "'rejected'",
        "'needs_revision'",
        "'withdrawn'",
    ):
        assert decision_type in SQL
    assert "intake_review_decision" not in SQL
    assert "approval_eligible" not in SQL
    assert "source_snapshot_id" not in _decision_table_definition()
    assert "candidate_fact_revision_id" not in _decision_table_definition()
    assert "claim_draft_id" not in _decision_table_definition()


def test_database_owns_order_predecessor_and_recorded_time():
    assert "NEW.decision_sequence IS NOT NULL" in SQL
    assert "NEW.recorded_at IS NOT NULL" in SQL
    assert "NEW.decision_sequence := v_next" in SQL
    assert "NEW.supersedes_decision_id := v_current_id" in SQL
    assert "NEW.recorded_at := clock_timestamp()" in SQL
    assert "ORDER BY recorded_at" not in PACKAGE_TEXT
    assert "ORDER BY id" not in PACKAGE_TEXT
    assert "max(decision_sequence)" not in PACKAGE_TEXT.lower()


def test_retry_chain_and_append_only_constraints_are_present():
    for name in (
        "uq_reird_item_sequence",
        "uq_reird_item_request",
        "uq_reird_supersedes_once",
        "fk_reird_supersedes_same_item",
        "trg_reird_no_mutation",
        "trg_reird_prepare_insert",
    ):
        assert name in SQL
    assert "EXECUTE FUNCTION slicea_reject_mutation()" in SQL
    assert "REVOKE ALL ON TABLE research_evidence_item_review_sequence_allocator" in SQL


def test_nonblank_checks_cover_all_postgresql_whitespace():
    assert "decision_reason !~ '^[[:space:]]*$'" in SQL
    assert "decided_by !~ '^[[:space:]]*$'" in SQL
    assert "request_id !~ '^[[:space:]]*$'" in SQL
    assert "btrim(decision_reason)" not in SQL
    assert "btrim(decided_by)" not in SQL


def test_python_surface_does_not_import_or_call_prohibited_subsystems():
    import_lines = "\n".join(
        line
        for line in PACKAGE_TEXT.splitlines()
        if line.startswith("import ") or line.startswith("from ")
    )
    for forbidden in (
        "api",
        "automation_roi",
        "capture",
        "export",
        "monitor",
        "orchestrator",
        "retrieval",
        "scenario",
        "studio",
        "upload",
        "workspace",
    ):
        assert forbidden not in import_lines.lower()
    assert ".commit(" not in PACKAGE_TEXT
    assert ".close(" not in PACKAGE_TEXT


def test_public_create_model_omits_server_and_parent_detail_fields():
    model_text = (ROOT / "research_evidence" / "review_models.py").read_text(
        encoding="utf-8"
    )
    create_block = model_text.split(
        "class ResearchEvidenceIntakeItemReviewDecisionCreate", 1
    )[1].split(
        "class ResearchEvidenceIntakeItemReviewDecisionRecord", 1
    )[0]
    for field in (
        "decision_sequence:",
        "supersedes_decision_id:",
        "recorded_at:",
        "source_snapshot_id:",
        "candidate_fact_revision_id:",
        "fact_metadata_revision_id:",
        "claim_draft_id:",
        "effective_status:",
        "approval_eligible:",
    ):
        assert field not in create_block


def _decision_table_definition() -> str:
    return SQL.split(
        "CREATE TABLE IF NOT EXISTS "
        "research_evidence_intake_item_review_decision (",
        1,
    )[1].split(");", 1)[0]
