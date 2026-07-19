from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PACK_MODULES = [
    ROOT / "mas/research_evidence/pack_models.py",
    ROOT / "mas/research_evidence/pack_repository.py",
    ROOT / "mas/research_evidence/pack_service.py",
]
MIGRATION = ROOT / "mas/sql/v61_research_evidence_pack_foundation.sql"
ASSEMBLY_DOC = ROOT / "docs/v4.4-R2.0A-2-RESEARCH-EVIDENCE-PACK-ASSEMBLY-QUERY.md"


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


def test_assembly_has_no_api_presentation_or_consumer_runtime_coupling():
    text = "\n".join(path.read_text(encoding="utf-8") for path in PACK_MODULES)
    forbidden = (
        "import api", "from api", "mas.api", "dashboards/", "exporters",
        "from .automation_roi_execution", "import automation_roi_execution",
        "from .scenario_input_evaluation", "import scenario_input_evaluation",
        "from orchestrator", "import orchestrator",
    )
    assert not [token for token in forbidden if token in text]


def test_assembly_query_is_read_only_bounded_and_scope_explicit():
    repository = PACK_MODULES[1].read_text(encoding="utf-8")
    service = PACK_MODULES[2].read_text(encoding="utf-8")
    assembly = repository.split("def assemble_effective_project_pack(", 1)[1]
    service_assembly = service.split("def assemble_research_evidence_pack(", 1)[1]
    assert "usage_scope: UsageScope" in assembly
    assert "usage_scope: UsageScope" in service_assembly
    assert "MAX_PACK_CANDIDATE_REPRESENTATIONS + 1" in assembly
    assert "ResearchEvidencePackQuery(" in assembly
    assert "ResearchEvidencePackQuery(" in service_assembly
    forbidden = (
        "INSERT INTO", "UPDATE ", "DELETE FROM", "FOR UPDATE", "LOCK TABLE",
        ".commit(", ".rollback(", 'conn.execute("ROLLBACK")', "SAVEPOINT",
    )
    assert not [token for token in forbidden if token in assembly]
    assert "config.research_evidence_enabled()" in service

    query = repository.split('_PACK_ASSEMBLY_SELECT = """', 1)[1].split('"""', 1)[0]
    head_boundary, remainder = query.split("), candidate_status AS MATERIALIZED (", 1)
    prewide, wide = remainder.split("), eligible AS MATERIALIZED (", 1)
    assert "bounded_authorization_heads AS MATERIALIZED" in head_boundary
    assert "LIMIT %s" in head_boundary
    assert "DISTINCT" not in head_boundary and "ORDER BY" not in head_boundary
    assert "research_evidence_usage_authorization_decision" not in head_boundary
    assert "bounded_authorization_candidates AS MATERIALIZED" in prewide
    assert "later_decision.decision_sequence>d.decision_sequence" in prewide
    assert "research_source_metadata_revision" not in prewide
    assert "research_fact_metadata_revision" not in prewide
    assert "SELECT DISTINCT ON" not in wide


def test_r2_0a_2_adds_no_migration_and_documents_exact_boundaries():
    assert not list((ROOT / "mas/sql").glob("v62_*.sql"))
    doc = ASSEMBLY_DOC.read_text(encoding="utf-8")
    required = (
        "typed empty pack", "current project context", "UsageScope",
        "No historical authorization is used as fallback",
        "Migration v62 remains unused", "R2.0A-1 -> R2.0A-2 -> R2.0A-3 -> R2.0A-4",
        "API;", "UI or dashboard work;", "exports;",
        "Automation ROI execution;", "scenario execution;", "consumer integration;",
    )
    assert not [phrase for phrase in required if phrase not in doc]


def test_package_exports_only_public_assembly_contracts():
    package = (ROOT / "mas/research_evidence/__init__.py").read_text(encoding="utf-8")
    public = (
        "ResearchEvidencePackAggregate", "ResearchEvidencePackAuthorizedClaim",
        "ResearchEvidencePackAuthorizedEvidence",
        "ResearchEvidencePackAuthorizedRelationship",
        "ResearchEvidencePackAuthorizedSource", "ResearchEvidencePackClaimAnnotation",
        "ResearchEvidencePackContext", "ResearchEvidencePackCounts",
        "ResearchEvidencePackExplicitProbability", "ResearchEvidencePackQuery",
        "assemble_research_evidence_pack",
    )
    assert not [name for name in public if package.count(f'"{name}"') != 1]
    assert '"assemble_effective_project_pack"' not in package
    assert '"ResearchEvidencePackCapacityError"' not in package
