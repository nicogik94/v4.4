"""Static scope and safety checks for v52 research-evidence hardening."""
import hashlib
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

V51_PATH = ROOT / "sql" / "v51_research_evidence_sidecar_foundation.sql"
V52_PATH = ROOT / "sql" / "v52_research_evidence_audit_integrity.sql"
MODELS_PATH = ROOT / "research_evidence" / "models.py"
REPOSITORY_PATH = ROOT / "research_evidence" / "repository.py"
SERVICE_PATH = ROOT / "research_evidence" / "service.py"

V51_SHA256 = "352efe6ee938c8108d9e9f5eeb013510b0cb0aa87d2de059eaf09767c6fab623"
V52 = V52_PATH.read_text(encoding="utf-8")
MODELS = MODELS_PATH.read_text(encoding="utf-8")
REPOSITORY = REPOSITORY_PATH.read_text(encoding="utf-8")
SERVICE = SERVICE_PATH.read_text(encoding="utf-8")


def _imports(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines()
        if line.startswith("import ") or line.startswith("from ")
    )


def test_v51_is_immutable():
    assert hashlib.sha256(V51_PATH.read_bytes()).hexdigest() == V51_SHA256


def test_v52_is_bounded_to_allocator_function_and_trigger():
    assert V52.count("BEGIN;") == 1
    assert V52.count("COMMIT;") == 1
    assert V52.count("CREATE TABLE IF NOT EXISTS ") == 1
    assert "CREATE TABLE IF NOT EXISTS research_evidence_event_sequence_allocator" in V52
    assert "FUNCTION research_evidence_prepare_event_insert()" in V52
    assert "CREATE TRIGGER trg_ree_prepare_insert" in V52
    assert "ENABLE ALWAYS TRIGGER trg_ree_prepare_insert" in V52
    for forbidden in (
        "CREATE TABLE IF NOT EXISTS source_blob",
        "CREATE TABLE IF NOT EXISTS source_snapshot",
        "CREATE TABLE IF NOT EXISTS candidate_fact_revision",
        "CREATE TABLE IF NOT EXISTS evidence_retention_event",
        "CREATE TABLE IF NOT EXISTS approved_calculation_input",
        "CREATE VIEW",
        "DROP TABLE",
        "DROP SCHEMA",
    ):
        assert forbidden not in V52


def test_event_sequence_is_database_allocated_only():
    insert_event = REPOSITORY.split("def insert_event(", 1)[1].split(
        "def list_source_metadata_revisions(", 1
    )[0]
    assert "(project_id, entity_type, entity_id, event_type, actor, details_json)" in insert_event
    assert "next_event_sequence" not in REPOSITORY
    assert re.search(r"MAX\s*\(\s*event_sequence", REPOSITORY, re.IGNORECASE) is None
    assert "class EvidenceEventCreate" not in MODELS
    withdrawal = MODELS.split("class WithdrawalCommand", 1)[1]
    assert "event_sequence" not in withdrawal


def test_service_uses_savepoint_without_owning_connection_lifecycle():
    assert 'conn.execute("SAVEPOINT research_evidence_write")' in SERVICE
    assert 'conn.execute("ROLLBACK TO SAVEPOINT research_evidence_write")' in SERVICE
    assert SERVICE.count('conn.execute("RELEASE SAVEPOINT research_evidence_write")') == 2
    assert ".commit(" not in SERVICE
    assert ".rollback(" not in SERVICE
    assert ".close(" not in SERVICE
    assert re.search(r"\bUPDATE\s+research_", SERVICE, re.IGNORECASE) is None
    assert re.search(r"\bDELETE\s+FROM\s+research_", SERVICE, re.IGNORECASE) is None


def test_v52_sidecar_code_has_no_prohibited_subsystem_imports():
    import_text = _imports("\n".join((MODELS, REPOSITORY, SERVICE)))
    for forbidden in (
        "knowledge.",
        "automation_roi",
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
        assert forbidden not in import_text
