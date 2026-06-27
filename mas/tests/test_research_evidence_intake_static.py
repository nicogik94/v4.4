"""Static boundary tests for the additive R1.2 intake foundation."""
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


SQL_PATH = ROOT / "sql" / "v53_research_evidence_intake_foundation.sql"
SQL_RAW = SQL_PATH.read_text(encoding="utf-8")
SQL = _strip_sql_comments(SQL_RAW)
PACKAGE_FILES = (
    ROOT / "research_evidence" / "intake_models.py",
    ROOT / "research_evidence" / "intake_repository.py",
    ROOT / "research_evidence" / "intake_service.py",
)
PACKAGE_TEXT = "\n".join(path.read_text(encoding="utf-8") for path in PACKAGE_FILES)


def test_v53_is_transactional_additive_and_manual():
    assert SQL.count("BEGIN;") == 1
    assert SQL.count("COMMIT;") == 1
    assert "ALTER TABLE" not in SQL
    assert "DROP TABLE" not in SQL
    assert "DROP SCHEMA" not in SQL
    assert "CREATE VIEW" not in SQL
    assert "CREATE INDEX CONCURRENTLY" not in SQL
    assert "psql " in SQL_RAW


def test_v53_creates_only_two_intake_tables():
    assert SQL.count("CREATE TABLE IF NOT EXISTS ") == 2
    assert "CREATE TABLE IF NOT EXISTS research_evidence_intake (" in SQL
    assert "CREATE TABLE IF NOT EXISTS research_evidence_intake_item (" in SQL
    for parent in (
        "source_blob",
        "source_snapshot",
        "candidate_fact_revision",
        "evidence_retention_event",
        "research_source_metadata_revision",
        "research_fact_metadata_revision",
        "research_claim_draft",
        "research_evidence_event",
        "research_evidence_event_sequence_allocator",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {parent} (" not in SQL


def test_v53_never_writes_parent_or_history_tables():
    for table in (
        "source_blob",
        "source_snapshot",
        "candidate_fact_revision",
        "evidence_retention_event",
        "research_source_metadata_revision",
        "research_fact_metadata_revision",
        "research_claim_draft",
        "research_evidence_event",
        "research_evidence_event_sequence_allocator",
    ):
        assert f"INSERT INTO {table}" not in SQL
        assert f"UPDATE {table}" not in SQL
        assert f"DELETE FROM {table}" not in SQL


def test_contract_is_operator_selected_and_draft_only():
    assert "operator_selected_existing_snapshot" in SQL
    assert "ck_rei_intake_method" in SQL
    assert "ck_rei_state_draft" in SQL
    assert "ck_reii_state_draft" in SQL
    assert "state = 'draft'" in SQL
    for forbidden_state in ("'approved'", "'released'", "'published'", "'active'"):
        assert forbidden_state not in SQL.lower()


def test_intake_nonblank_checks_reject_all_whitespace_classes():
    assert "selection_reason !~ '^[[:space:]]*$'" in SQL
    assert "created_by !~ '^[[:space:]]*$'" in SQL
    assert "char_length(btrim(selection_reason))" not in SQL
    assert SQL.count("char_length(btrim(created_by))") == 1


def test_item_uses_composite_keys_and_only_one_narrow_validation_trigger():
    assert (
        "FOREIGN KEY (research_evidence_intake_id, project_id, source_snapshot_id)"
        in SQL
    )
    assert (
        "FOREIGN KEY (fact_metadata_revision_id, project_id, candidate_fact_revision_id)"
        in SQL
    )
    assert SQL.count("BEFORE INSERT ON research_evidence_intake_item") == 1
    assert "research_evidence_intake_validate_item_snapshot" in SQL
    assert "source_snapshot_id = $3" in SQL


def test_duplicate_bindings_and_append_only_guards_are_database_enforced():
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_reii_intake_candidate_fact" in SQL
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_reii_intake_claim_draft" in SQL
    assert "trg_rei_no_mutation" in SQL
    assert "trg_reii_no_mutation" in SQL
    assert SQL.count("EXECUTE FUNCTION slicea_reject_mutation()") == 2


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
        "freshness",
        "monitor",
        "orchestrator",
        "retrieval",
        "scenario",
        "studio",
        "upload",
        "workspace",
    ):
        assert forbidden not in import_lines.lower()
    assert "capture_upload(" not in PACKAGE_TEXT
    assert ".commit(" not in PACKAGE_TEXT
    assert ".close(" not in PACKAGE_TEXT


def test_public_item_model_does_not_accept_relational_snapshot_key():
    model_text = (ROOT / "research_evidence" / "intake_models.py").read_text(
        encoding="utf-8"
    )
    create_block = model_text.split(
        "class ResearchEvidenceIntakeItemCreate", 1
    )[1].split("class ResearchEvidenceIntakeItemRecord", 1)[0]
    assert "source_snapshot_id:" not in create_block
