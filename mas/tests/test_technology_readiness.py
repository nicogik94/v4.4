from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import templates.technology_readiness as technology_template
from orchestrator import (
    PROMPT_BUILDERS,
    WORKFLOW_PHASE_SEQUENCE,
    _parsed_json_matches_phase,
    _phase_json_retry_instruction,
    _store_phase_output,
    workflow_phase_sequence_for_state,
)
from state import ProjectState, TechnologyReadinessScopeOutput
from tools.technology_readiness import (
    EVIDENCE_CATEGORIES,
    IP_PROTECTION_AXES,
    READINESS_VERDICT_CODES,
    RESEARCH_INDUSTRY_CRITERIA,
    TRL_PHASES,
    compute_alignment_score,
    compute_evidence_sufficiency,
    normalize_trl,
    overclaim_warnings,
    unknown_evidence_categories,
)
from workflow_templates import (
    STRATEGIC_AUDIT_PHASE_SEQUENCE,
    TECHNOLOGY_READINESS_PHASE_SEQUENCE,
    get_workflow_phase_sequence,
)


PROMPT_DIR = ROOT / "prompts" / "technology_readiness"


def test_technology_readiness_state_uses_template_sequence():
    state = ProjectState(
        project_id="tech-sequence",
        project_name="Technology readiness",
        brief="Assess a lab prototype for industrial transfer.",
        project_type="technology_readiness",
    )

    assert state.project_type == "technology_readiness"
    assert state.current_phase == "scope"
    assert tuple(state.phase_status) == TECHNOLOGY_READINESS_PHASE_SEQUENCE
    assert workflow_phase_sequence_for_state(state) == TECHNOLOGY_READINESS_PHASE_SEQUENCE


def test_existing_project_type_sequences_are_unchanged():
    assert WORKFLOW_PHASE_SEQUENCE == STRATEGIC_AUDIT_PHASE_SEQUENCE
    assert get_workflow_phase_sequence("strategic_audit") == STRATEGIC_AUDIT_PHASE_SEQUENCE
    assert get_workflow_phase_sequence("ai_readiness") == STRATEGIC_AUDIT_PHASE_SEQUENCE
    assert get_workflow_phase_sequence("automation_roi") == STRATEGIC_AUDIT_PHASE_SEQUENCE
    assert ProjectState(project_id="default", brief="x").current_phase == "classify"


def test_technology_readiness_prompts_exist_and_have_builders():
    state = ProjectState(project_id="tech-prompts", project_type="technology_readiness", brief="x")

    for phase in TECHNOLOGY_READINESS_PHASE_SEQUENCE:
        prompt_file = PROMPT_DIR / f"{phase}.md"
        assert prompt_file.exists(), phase
        assert prompt_file.read_text(encoding="utf-8").strip(), phase
        assert phase in PROMPT_BUILDERS
        built = PROMPT_BUILDERS[phase](state)
        assert "ASSESSMENT CONTEXT:" in built
        assert "Return the requested JSON object only." in built


def test_trl_prompt_has_overclaim_controls():
    text = (PROMPT_DIR / "trl_diagnosis.md").read_text(encoding="utf-8")

    assert "TRL 7+" in text
    assert "Do not assign TRL 8+" in text
    assert "Do not assign TRL 9" in text
    assert "why_not_higher" in text
    assert "This is not certification" in text


def test_research_industry_prompt_lists_all_criteria():
    text = (PROMPT_DIR / "research_industry_alignment.md").read_text(encoding="utf-8")

    for criterion in RESEARCH_INDUSTRY_CRITERIA:
        assert criterion in text
    for field in ("score", "evidence", "gap", "recommendation"):
        assert f'"{field}"' in text


def test_ip_prompt_requires_specialist_review_and_all_axes():
    text = (PROMPT_DIR / "ip_protection_axis.md").read_text(encoding="utf-8")

    for axis in IP_PROTECTION_AXES:
        assert axis in text
    assert "specialist_review_required" in text
    assert "Do not claim legal patentability" in text
    assert "requires specialist review" in text


def test_next_level_prompt_requires_evidence_and_advancement_criteria():
    text = (PROMPT_DIR / "next_level_recommendations.md").read_text(encoding="utf-8")

    assert "required_evidence" in text
    assert "advancement_criteria" in text
    assert "Do not recommend advancement without explicit evidence requirements" in text


def test_roadmap_and_executive_summary_prompt_controls():
    roadmap = (PROMPT_DIR / "readiness_roadmap.md").read_text(encoding="utf-8")
    summary = (PROMPT_DIR / "executive_summary.md").read_text(encoding="utf-8")

    for phase in TRL_PHASES.values():
        assert phase["trl"] in roadmap
        assert phase["time_range"] in roadmap
    for verdict_code in READINESS_VERDICT_CODES:
        assert verdict_code in summary


def test_store_phase_output_validates_technology_readiness_payload():
    state = ProjectState(project_id="store-tech", project_type="technology_readiness", brief="x")
    payload = {
        "technology_name": "Lab coating",
        "assessment_boundary": "prototype chemistry only",
        "target_environment": "pilot manufacturing line",
        "intended_next_milestone": "proof of concept",
        "stakeholders": ["principal investigator"],
        "constraints": [],
        "assumptions": ["operator-reviewed evidence only"],
        "confidence": "preliminary",
    }

    _store_phase_output(state, "scope", payload)

    assert isinstance(state.scope, TechnologyReadinessScopeOutput)
    assert state.scope.technology_name == "Lab coating"
    assert _parsed_json_matches_phase("scope", payload)
    assert not _parsed_json_matches_phase("scope", [payload])
    assert "legal/certification" in _phase_json_retry_instruction("scope")


def test_technology_readiness_helpers_are_deterministic():
    assert normalize_trl("TRL 4.7") == 4
    assert normalize_trl("TRL 99") == 9
    assert compute_alignment_score({"technical_novelty": {"score": 4}, "unknown": 5}) == 4.0

    evidence = compute_evidence_sufficiency(["scientific_basis", "proof_of_concept", "unexpected"])
    assert evidence["coverage_count"] == 2
    assert evidence["unknown_categories"] == ["unexpected"]
    assert unknown_evidence_categories(EVIDENCE_CATEGORIES) == []

    warnings = overclaim_warnings(
        {
            "current_trl": 5,
            "evidence_categories": ["scientific_basis"],
            "specialist_review_required": False,
            "readiness_verdict_code": "unsupported",
        }
    )
    assert any("TRL 5+" in warning for warning in warnings)
    assert any("specialist_review_required=False" in warning for warning in warnings)
    assert any("Unknown readiness verdict code" in warning for warning in warnings)


def test_template_package_does_not_import_missing_base_modules():
    source = (ROOT / "templates" / "technology_readiness" / "__init__.py").read_text(encoding="utf-8")

    assert technology_template.TEMPLATE["project_type"] == "technology_readiness"
    assert technology_template.TEMPLATE["phase_sequence"] == TECHNOLOGY_READINESS_PHASE_SEQUENCE
    assert "from templates.registry" not in source
    assert "from templates.base" not in source
