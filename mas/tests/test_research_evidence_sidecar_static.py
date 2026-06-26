"""Static safety checks for the R1.1 research-evidence sidecar."""
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _strip_sql_comments(text: str) -> str:
    out = []
    for line in text.splitlines():
        idx = line.find("--")
        out.append(line if idx == -1 else line[:idx])
    return "\n".join(out)


SQL_RAW = (ROOT / "sql" / "v51_research_evidence_sidecar_foundation.sql").read_text(encoding="utf-8")
SQL = _strip_sql_comments(SQL_RAW)
PACKAGE_FILES = [
    ROOT / "research_evidence" / "__init__.py",
    ROOT / "research_evidence" / "models.py",
    ROOT / "research_evidence" / "repository.py",
    ROOT / "research_evidence" / "service.py",
]
PACKAGE_TEXT = "\n".join(path.read_text(encoding="utf-8") for path in PACKAGE_FILES)


def test_migration_is_additive_and_bounded():
    assert SQL.count("BEGIN;") == 1
    assert SQL.count("COMMIT;") == 1
    assert "ALTER TABLE" not in SQL
    assert "DROP TABLE" not in SQL
    assert "DROP SCHEMA" not in SQL
    assert "CREATE INDEX CONCURRENTLY" not in SQL
    assert "CREATE VIEW" not in SQL
    assert "INSERT INTO research_" not in SQL


def test_migration_creates_only_sidecar_tables():
    expected = {
        "research_source_metadata_revision",
        "research_fact_metadata_revision",
        "research_claim_draft",
        "research_evidence_event",
    }
    for table in expected:
        assert f"CREATE TABLE IF NOT EXISTS {table} (" in SQL
    assert SQL.count("CREATE TABLE IF NOT EXISTS ") == len(expected)
    for forbidden in (
        "CREATE TABLE IF NOT EXISTS source_blob",
        "CREATE TABLE IF NOT EXISTS source_snapshot",
        "CREATE TABLE IF NOT EXISTS candidate_fact_revision",
        "CREATE TABLE IF NOT EXISTS evidence_retention_event",
        "CREATE TABLE IF NOT EXISTS candidate_fact_approval_decision",
        "CREATE TABLE IF NOT EXISTS approved_calculation_input",
        "CREATE TABLE IF NOT EXISTS calculation_result",
        "CREATE TABLE IF NOT EXISTS calculation_result_input",
    ):
        assert forbidden not in SQL


def test_migration_depends_on_v47_not_v48():
    assert "source_snapshot(id, project_id)" in SQL
    assert "candidate_fact_revision(id, project_id)" in SQL
    assert "slicea_reject_mutation" in SQL
    for forbidden in (
        "candidate_fact_approval_decision",
        "approved_calculation_input",
        "calculation_result",
        "calculation_result_input",
    ):
        assert forbidden not in SQL


def test_no_status_or_readiness_fields_are_created():
    lowered = SQL.lower()
    for forbidden in (
        "approved",
        "reviewed",
        "supported",
        "fresh",
        "stale",
        "expired",
        "authoritative",
        "calculation_ready",
        "scenario_ready",
        "report_ready",
        "client_ready",
        "availability_status",
        "freshness_status",
        "freshness_due_at",
    ):
        assert forbidden not in lowered


def test_no_competing_availability_or_retention_state():
    lowered = SQL.lower()
    for forbidden_field in (
        "availability_status",
        "available",
        "is_available",
        "retention_status",
        "tombstone",
        "redact",
        "legal_hold",
    ):
        assert forbidden_field not in lowered
    assert "evidence_retention_event" in SQL_RAW
    assert "event_type IN ('created', 'superseded', 'correction_recorded', 'withdrawn')" in SQL


def test_sidecar_package_does_not_import_prohibited_subsystems():
    import_lines = "\n".join(
        line for line in PACKAGE_TEXT.splitlines()
        if line.startswith("import ") or line.startswith("from ")
    )
    for forbidden in (
        "knowledge.automation_roi",
        "automation_roi_api",
        "orchestrator",
        "exporters",
        "scenarios",
        "prompts",
        "agent_blueprint_studio",
        "dashboards",
        "retrieval",
        "workspace",
        "state",
        "store",
    ):
        assert forbidden not in import_lines
