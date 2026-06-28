"""Static boundary tests for the additive R1.4 freshness foundation."""
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


SQL_PATH = ROOT / "sql" / "v55_research_evidence_freshness_foundation.sql"
SQL_RAW = SQL_PATH.read_text(encoding="utf-8")
SQL = _strip_sql_comments(SQL_RAW)
PACKAGE_FILES = (
    ROOT / "research_evidence" / "freshness_models.py",
    ROOT / "research_evidence" / "freshness_repository.py",
    ROOT / "research_evidence" / "freshness_service.py",
)
PACKAGE_TEXT = "\n".join(path.read_text(encoding="utf-8") for path in PACKAGE_FILES)


def test_v55_is_transactional_additive_and_manual():
    assert SQL.count("BEGIN;") == 1
    assert SQL.count("COMMIT;") == 1
    assert "DROP TABLE" not in SQL
    assert "DROP SCHEMA" not in SQL
    assert "CREATE VIEW" not in SQL
    assert "CREATE INDEX CONCURRENTLY" not in SQL
    assert "psql " in SQL_RAW


def test_v55_creates_only_assessment_and_internal_allocator():
    assert SQL.count("CREATE TABLE IF NOT EXISTS") == 2
    assert "research_evidence_intake_item_freshness_assessment (" in SQL
    assert "research_evidence_item_freshness_sequence_allocator (" in SQL
    for parent in (
        "source_blob",
        "source_snapshot",
        "candidate_fact_revision",
        "evidence_retention_event",
        "research_evidence_intake_item",
        "research_evidence_intake_item_review_decision",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {parent} (" not in SQL


def test_v55_never_writes_prior_contract_tables():
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
    ):
        assert f"INSERT INTO {table}" not in SQL
        assert f"UPDATE {table}" not in SQL
        assert f"DELETE FROM {table}" not in SQL


def test_policy_and_linked_evidence_provenance_is_immutable():
    for field in (
        "policy_identifier",
        "policy_version",
        "policy_parameters_json",
        "policy_fingerprint",
        "evaluator_version",
        "basis_timestamp",
        "fresh_through",
        "source_snapshot_id",
        "source_blob_id",
        "candidate_fact_revision_id",
        "fact_metadata_revision_id",
        "linked_hash_algorithm",
        "linked_content_hash",
        "assessed_at",
    ):
        assert field in _assessment_table_definition()
    assert "trg_reifa_no_mutation" in SQL
    assert "EXECUTE FUNCTION slicea_reject_mutation()" in SQL


def test_database_owns_evidence_sequence_predecessor_and_time():
    for sentinel in (
        "NEW.assessment_sequence IS NOT NULL",
        "NEW.supersedes_assessment_id IS NOT NULL",
        "NEW.assessed_at IS NOT NULL",
        "linked evidence is server-assigned",
        "NEW.assessment_sequence := v_next",
        "NEW.supersedes_assessment_id := v_current_id",
        "NEW.assessed_at := clock_timestamp()",
    ):
        assert sentinel in SQL
    assert "ORDER BY assessment_sequence DESC" in PACKAGE_TEXT
    assert "ORDER BY assessed_at" not in PACKAGE_TEXT
    assert "max(assessment_sequence)" not in PACKAGE_TEXT.lower()


def test_hash_evidence_and_material_drift_are_separate():
    assert "content_change_detected" in SQL
    assert "drift_status" in SQL
    assert "NEW.content_change_detected :=" in SQL
    assert "NEW.drift_status :=" not in SQL
    for status in (
        "'not_assessed'",
        "'no_material_drift'",
        "'material_drift'",
        "'indeterminate'",
    ):
        assert status in SQL


def test_freshness_is_read_only_and_claims_are_not_applicable():
    service_text = (
        ROOT / "research_evidence" / "freshness_service.py"
    ).read_text(encoding="utf-8")
    predicate = service_text.split("def item_freshness_status_as_of", 1)[1]
    assert 'return "not_applicable"' in predicate
    assert (
        'return "fresh" if as_of <= assessment.fresh_through else "stale"'
        in predicate
    )
    assert "insert_" not in predicate
    assert ".execute(" not in predicate
    assert "claim-draft intake items are not applicable" in SQL


def test_freshness_does_not_import_or_compose_other_contracts():
    import_lines = "\n".join(
        line
        for line in PACKAGE_TEXT.splitlines()
        if line.startswith("import ") or line.startswith("from ")
    )
    for forbidden in (
        "review",
        "retention",
        "availability",
        "lineage",
        "citation",
        "calculation",
        "retrieval",
        "scenario",
        "workflow",
        "api",
    ):
        assert forbidden not in import_lines.lower()
    assert "item_is_eligible_for_future_use" not in PACKAGE_TEXT
    assert ".commit(" not in PACKAGE_TEXT
    assert ".close(" not in PACKAGE_TEXT


def test_no_prohibited_product_surface_is_added():
    for forbidden in (
        "background job",
        "dashboard",
        "endpoint",
        "monitoring",
        "refresh job",
        "report",
        "retrieval",
        "scenario",
        "workflow",
    ):
        assert forbidden not in PACKAGE_TEXT.lower()


def _assessment_table_definition() -> str:
    return SQL.split(
        "research_evidence_intake_item_freshness_assessment (",
        1,
    )[1].split(");", 1)[0]
