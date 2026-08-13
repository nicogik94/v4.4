"""Static architecture guards for the R2.0A-4A Research Evidence consumer.

These assert the ownership boundary in source: the consumer consumes via the
public A-3 entry with internal_analysis fixed, never authorizes, never widens
scope, never writes/commits, adds no migration, and touches no report/export/
client surface. Orchestrator wiring stays byte-stable and fail-closed.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSUMER = ROOT / "mas/research_evidence_context.py"
ORCHESTRATOR = ROOT / "mas/orchestrator.py"
EXPLAINABILITY = ROOT / "mas/explainability.py"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_consumer_assembles_only_through_public_a3_entry():
    text = _text(CONSUMER)
    # exactly one call to the public A-3 entry, and no A-2 assembly/authorization
    assert text.count("project_research_evidence_presentation(") == 1
    forbidden_calls = (
        "assemble_research_evidence_pack(",
        "record_usage_authorization_decision(",
        "record_claim_annotation_revision(",
        "assemble_effective_project_pack(",
        "pack_repository.",
        "binding_service",
        "review_repository",
        "claim_support_repository",
    )
    assert not [token for token in forbidden_calls if token in text]


def test_consumer_fixes_internal_analysis_and_takes_no_caller_scope():
    text = _text(CONSUMER)
    assert "CONSUMER_USAGE_SCOPE = UsageScope.INTERNAL_ANALYSIS" in text
    assert "usage_scope=CONSUMER_USAGE_SCOPE" in text
    # the consumer never mentions the wider disclosure scopes
    assert "operator_dossier" not in text
    assert "client_report" not in text
    # no caller-provided scope is ever threaded into the A-3 call
    assert "usage_scope=usage_scope" not in text
    assert "UsageScope.OPERATOR_DOSSIER" not in text
    assert "UsageScope.CLIENT_REPORT" not in text


def test_consumer_is_read_only_and_never_commits_or_writes():
    text = _text(CONSUMER)
    assert "conn.read_only = True" in text
    assert "conn.autocommit = False" in text
    assert "conn.rollback()" in text
    assert ".commit(" not in text
    for write in ("INSERT INTO", "UPDATE ", "DELETE FROM", "conn.execute("):
        assert write not in text


def test_consumer_enforces_frozen_budget_and_stable_reason_codes():
    text = _text(CONSUMER)
    assert "RESEARCH_EVIDENCE_PROMPT_BUDGET_BYTES = 65536" in text
    for reason in (
        "research_evidence_prompt_overflow",
        "research_evidence_capacity_overflow",
        "research_evidence_unavailable",
        "research_evidence_integrity_error",
    ):
        assert reason in text


def test_consumer_does_not_touch_report_export_or_client_surface():
    text = _text(CONSUMER)
    # coupling-specific tokens; bare words like "automation_roi" legitimately
    # appear in the gating rationale comment and are not coupling.
    forbidden = (
        "import exporters", "from exporters", "client_delivery",
        "monitoring_templates", "report_quality", "scenario_shadow",
        "ScenarioShadow", "automation_roi_api", "build_report_prompt",
        "import scenarios", "from scenarios",
    )
    assert not [token for token in forbidden if token in text]


def test_consumer_is_imported_only_by_orchestrator_and_explainability():
    importers = []
    for path in (ROOT / "mas").glob("*.py"):
        if path.name in ("research_evidence_context.py",):
            continue
        if "research_evidence_context" in path.read_text(encoding="utf-8"):
            importers.append(path.name)
    assert sorted(importers) == ["explainability.py", "orchestrator.py"]


def test_orchestrator_wiring_is_byte_stable_and_fail_closed():
    text = _text(ORCHESTRATOR)
    # research evidence section sits directly against the retrieval slot so an
    # empty section keeps legacy prompts byte-identical
    assert text.count("{retrieval_section}{research_evidence_section}") == 2
    assert "def build_audit_prompt(" in text
    assert "def build_strategy_prompt(" in text
    assert text.count("knowledge_retrieval_section: str | None = None") == 2
    # fail-closed: blocked consumption fails the phase and returns before call_llm
    consume_index = text.index("load_research_evidence_consumption(")
    blocked_index = text.index("if consumption.blocked:")
    call_index = text.index("response: LLMResponse = await call_llm(")
    assert consume_index < blocked_index < call_index


def test_no_new_migration_added():
    assert not list((ROOT / "mas/sql").glob("v62_*.sql"))


def test_explainability_exposes_research_evidence_impact_field():
    text = _text(EXPLAINABILITY)
    assert "research_evidence_impact: Optional[ResearchEvidenceImpactSummary] = None" in text
    assert "build_phase_research_evidence_impact(state, phase)" in text


# ═══ R3 — provenance carry-through surface ═══════════════════════════════════


def test_consumer_uses_stable_keys_not_positional_labels():
    text = _text(CONSUMER)
    assert 'RESEARCH_EVIDENCE_CLAIM_KEY_PREFIX = "REC-"' in text
    assert 'RESEARCH_EVIDENCE_SOURCE_KEY_PREFIX = "RES-"' in text
    assert 'RESEARCH_EVIDENCE_EVIDENCE_KEY_PREFIX = "REE-"' in text
    # No renderer line may reintroduce a phase-local positional reference.
    for positional in ('f"  C{index}', 'f"  S{index}', 'f"  E{index}', 'f"  R{index}'):
        assert positional not in text


def test_provenance_carry_through_reads_only_the_durable_attestation():
    """The downstream register must never re-open Research Evidence."""
    text = _text(CONSUMER)
    start = text.index("def build_research_evidence_provenance_register(")
    end = text.index("def extract_research_evidence_references(")
    register_body = text[start:end]
    assert "policy_audit_log" in register_body
    for forbidden in (
        "project_research_evidence_presentation",
        "open_consumer_connection",
        "load_internal_analysis_projection_sync",
        "asyncio",
    ):
        assert forbidden not in register_body


def test_report_prompt_carries_provenance_byte_stably():
    text = _text(ORCHESTRATOR)
    # Sits directly against the locator register so an empty section keeps
    # Research-Evidence-off report prompts byte-identical.
    assert text.count("{evidence_locator_register}{research_evidence_provenance}") == 2
    assert "build_downstream_provenance_section(state)" in text


def test_attribution_check_observes_and_never_rewrites_the_report():
    text = _text(ORCHESTRATOR)
    start = text.index("def _record_research_evidence_attribution_check(")
    end = text.index("def build_system_prompt(")
    body = text[start:end]
    # Reads the report; never assigns to it, and never touches provider routing.
    assert "state.report" in body
    assert "state.report =" not in body
    for forbidden in ("call_llm", "provider", "fallback", "retry", "phase_status"):
        assert forbidden not in body
    # Runs after the report is recorded, and cannot fail the phase.
    assign_index = text.index("state.report = response.text")
    check_index = text.index("_record_research_evidence_attribution_check(state)")
    assert assign_index < check_index


def test_r3_adds_no_migration():
    """Provenance rides the state snapshot the run already persists.

    v64 belongs to the later W8.1 state-coherence foundation; R3 still adds no
    migration of its own, and v65 remains unused.
    """
    assert not list((ROOT / "mas/sql").glob("v65_*.sql"))
