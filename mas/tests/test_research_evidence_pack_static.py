from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PACK_MODULES = [
    ROOT / "mas/research_evidence/pack_models.py",
    ROOT / "mas/research_evidence/pack_repository.py",
    ROOT / "mas/research_evidence/pack_service.py",
]
MIGRATION = ROOT / "mas/sql/v61_research_evidence_pack_foundation.sql"


def test_pack_has_no_project_state_api_or_consumer_surface_dependency():
    text = "\n".join(path.read_text(encoding="utf-8") for path in PACK_MODULES)
    forbidden = (
        "ProjectState", "mas.api", "orchestrator", "exporter", "workspace",
        "dashboard", "connector", "browser", "scraper", "requests.",
        "httpx", "openai", "prompt", "client_report_generator",
    )
    assert not [token for token in forbidden if token in text]


def test_repository_and_service_preserve_caller_transaction_ownership():
    repository = PACK_MODULES[1].read_text(encoding="utf-8")
    service = PACK_MODULES[2].read_text(encoding="utf-8")
    for text in (repository, service):
        assert ".commit(" not in text
        assert ".close(" not in text
        assert 'conn.execute("ROLLBACK")' not in text
    assert "if conn.autocommit" in repository
    assert "SHOW transaction_isolation" in repository
    assert "require_read_committed_transaction(conn)" in service
    assert "ROLLBACK TO SAVEPOINT" in service


def test_migration_is_additive_append_only_and_fixed_search_path():
    text = MIGRATION.read_text(encoding="utf-8")
    assert "DROP TABLE" not in text and "ALTER TYPE" not in text
    assert text.count("ENABLE ALWAYS TRIGGER") == 6
    assert text.count("SECURITY DEFINER SET search_path = pg_catalog") == 4
    assert "FROM PUBLIC" in text
    assert "GRANT " not in text


def test_exact_admitted_inventory_is_frozen_in_documentation():
    doc = (ROOT / "docs/v4.4-R2.0A-1-RESEARCH-EVIDENCE-PACK-FOUNDATION.md").read_text(encoding="utf-8")
    expected = (
        "docs/v4.4-R2.0A-1-RESEARCH-EVIDENCE-PACK-FOUNDATION.md",
        "mas/research_evidence/__init__.py", "mas/research_evidence/pack_models.py",
        "mas/research_evidence/pack_repository.py", "mas/research_evidence/pack_service.py",
        "mas/sql/v61_research_evidence_pack_foundation.sql",
        "mas/tests/evidence_snapshot_pg.py", "mas/tests/test_research_evidence_pack_models.py",
        "mas/tests/test_research_evidence_pack_repository.py",
        "mas/tests/test_research_evidence_pack_service.py",
        "mas/tests/test_research_evidence_pack_schema.py",
        "mas/tests/test_research_evidence_pack_static.py",
    )
    assert all(path in doc for path in expected)
    assert doc.count("mas/") == 11


def test_foundation_documents_deliberate_bom_and_json_null_distinctions():
    doc = (ROOT / "docs/v4.4-R2.0A-1-RESEARCH-EVIDENCE-PACK-FOUNDATION.md").read_text(
        encoding="utf-8"
    )
    assert "U+FEFF is outside the frozen edge-trim set" in doc
    assert "U+FEFF remains preserved deliberately" in doc
    assert "SQL NULL, JSON null, and `[]` are distinct" in doc
