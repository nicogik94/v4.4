from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / "mas/research_evidence/presentation_projection_models.py"
POLICY = ROOT / "mas/research_evidence/presentation_projection_policy.py"
SERVICE = ROOT / "mas/research_evidence/presentation_projection_service.py"
PRESENTATION_MODULES = (MODELS, POLICY, SERVICE)
PACKAGE = ROOT / "mas/research_evidence/__init__.py"
DOC = ROOT / "docs/v4.4-R2.0A-3-RESEARCH-EVIDENCE-PRESENTATION-PROJECTIONS.md"

PUBLIC_EXPORTS = (
    "PRESENTATION_POLICY_CANONICAL_JSON",
    "PRESENTATION_POLICY_FINGERPRINT",
    "PRESENTATION_POLICY_IDENTIFIER",
    "PRESENTATION_POLICY_PARAMETERS",
    "PRESENTATION_POLICY_VERSION",
    "PRESENTATION_PROJECTION_DESCRIPTOR_VERSION",
    "ResearchEvidencePresentationClaim",
    "ResearchEvidencePresentationContext",
    "ResearchEvidencePresentationEvidence",
    "ResearchEvidencePresentationProbability",
    "ResearchEvidencePresentationProjection",
    "ResearchEvidencePresentationProjectionDisabled",
    "ResearchEvidencePresentationProjectionError",
    "ResearchEvidencePresentationProjectionIntegrityError",
    "ResearchEvidencePresentationRelationship",
    "ResearchEvidencePresentationSource",
    "allowed_presentation_fields",
    "canonical_presentation_projection_descriptor",
    "presentation_projection_fingerprint",
    "project_research_evidence_pack",
    "project_research_evidence_presentation",
    "required_presentation_fields",
)


def _module_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in PRESENTATION_MODULES
    )


def test_presentation_modules_contain_no_sql_or_transaction_surface():
    text = _module_text()
    forbidden = (
        "conn.execute", "cursor", "SELECT ", "INSERT INTO", "UPDATE ",
        "DELETE FROM", "FOR UPDATE", "LOCK TABLE", "SAVEPOINT", ".commit(",
        ".rollback(", "psycopg", "autocommit", "transaction_isolation",
    )
    assert not [token for token in forbidden if token in text]


def test_presentation_modules_have_no_consumer_api_or_model_call_coupling():
    text = _module_text()
    forbidden = (
        "ProjectState", "mas.api", "import api", "from api", "orchestrator",
        "exporters", "client_delivery", "dashboard", "workspace", "connector",
        "browser", "requests.", "httpx", "openai", "llm", "prompt",
        "scenario", "automation_roi", "ai_readiness", "strategic",
    )
    assert not [token for token in forbidden if token in text]


def test_presentation_service_reuses_pack_assembly_without_duplicating_it():
    text = SERVICE.read_text(encoding="utf-8")
    assert "config.research_evidence_enabled()" in text
    assert text.count("pack_service.assemble_research_evidence_pack(") == 1
    assert "return project_research_evidence_pack(pack)" in text
    assert "_PACK_ASSEMBLY_SELECT" not in text
    assert "research_evidence_usage_authorization_decision" not in text
    assert "class ResearchEvidencePresentationProjectionDisabled" in text
    assert "class ResearchEvidencePresentationProjectionIntegrityError" in text
    models_text = MODELS.read_text(encoding="utf-8")
    assert "class ResearchEvidencePackAggregate" not in models_text
    assert "_require_pinned_policy_identity" in models_text


def test_projection_scope_is_derived_from_the_pack_not_the_caller():
    text = SERVICE.read_text(encoding="utf-8")
    pure = text.split("def project_research_evidence_pack(", 1)[1]
    pure = pure.split("def project_research_evidence_presentation(", 1)[0]
    assert "scope = pack.usage_scope" in pure
    assert "usage_scope=scope" in pure
    entry = text.split("def project_research_evidence_presentation(", 1)[1]
    assert "ResearchEvidencePackQuery(" in entry
    assert "usage_scope: UsageScope" in entry


def test_policy_module_is_frozen_versioned_and_default_deny():
    text = POLICY.read_text(encoding="utf-8")
    required = (
        "PRESENTATION_POLICY_IDENTIFIER = \"research_evidence_presentation_disclosure\"",
        "PRESENTATION_POLICY_VERSION = \"1.0.0\"",
        "PRESENTATION_POLICY_PARAMETERS",
        "PRESENTATION_POLICY_CANONICAL_JSON",
        "PRESENTATION_POLICY_FINGERPRINT",
        "\"default\": \"deny\"",
        "sha256",
        "sort_keys=True",
        "_validate_frozen_policy()",
    )
    assert not [token for token in required if token not in text]
    assert "denylist" not in text.replace("allow-all-minus-denylist", "")


def test_r2_0a_3_adds_no_migration_and_documents_exact_boundaries():
    assert not list((ROOT / "mas/sql").glob("v62_*.sql"))
    doc = DOC.read_text(encoding="utf-8")
    required = (
        "Migration v62 remains unused",
        "R2.0A-1 -> R2.0A-2 -> R2.0A-3 -> R2.0A-4",
        "A-3 does not create evidence authorization.",
        "DEFAULT DENY",
        "omission-only",
        "Membership in equals membership out.",
        "MAS_RESEARCH_EVIDENCE_ENABLED",
        "astimezone(UTC).isoformat()",
        "research-evidence-presentation-projection-v1",
        "research_evidence_presentation_disclosure",
        "- API;",
        "- exports;",
        "- report generation and rendering;",
        "- client delivery integration;",
        "- Automation ROI consumption;",
        "- scenario consumption;",
        "- Strategic Decision Audit consumption;",
        "- AI Readiness consumption;",
        "- consumer input binding activation;",
        "- persisted projection or delivery attestation state; and",
        "- migration v62.",
    )
    assert not [phrase for phrase in required if phrase not in doc]


def test_documented_inventory_is_exact():
    doc = DOC.read_text(encoding="utf-8")
    expected = (
        "docs/v4.4-R2.0A-3-RESEARCH-EVIDENCE-PRESENTATION-PROJECTIONS.md",
        "mas/research_evidence/__init__.py",
        "mas/research_evidence/presentation_projection_models.py",
        "mas/research_evidence/presentation_projection_policy.py",
        "mas/research_evidence/presentation_projection_service.py",
        "mas/tests/test_research_evidence_presentation_models.py",
        "mas/tests/test_research_evidence_presentation_service.py",
        "mas/tests/test_research_evidence_presentation_pg.py",
        "mas/tests/test_research_evidence_presentation_static.py",
    )
    assert not [path for path in expected if path not in doc]
    assert doc.count("mas/") == 8


def test_package_exports_presentation_contracts_exactly_once():
    package = PACKAGE.read_text(encoding="utf-8")
    assert not [
        name for name in PUBLIC_EXPORTS if package.count(f'"{name}"') != 1
    ]
    internal = (
        '"_canonical_value"', '"_projected_member"', '"_member_policy"',
        '"PRESENTATION_MEMBER_KINDS"', '"_validate_frozen_policy"',
    )
    assert not [name for name in internal if name in package]


def test_presentation_modules_never_write_or_own_transactions():
    text = SERVICE.read_text(encoding="utf-8")
    entry = text.split("def project_research_evidence_presentation(", 1)[1]
    for token in ("commit", "rollback", "execute", "lock"):
        assert token not in entry
