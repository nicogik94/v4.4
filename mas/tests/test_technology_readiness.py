from pathlib import Path
import asyncio
import json
import sys
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import templates.technology_readiness as technology_template
from llm_client import LLMResponse
from orchestrator import (
    PROMPT_BUILDERS,
    WORKFLOW_PHASE_SEQUENCE,
    _invalid_json_shape_diagnostic,
    _parsed_json_matches_phase,
    _phase_json_retry_instruction,
    _repair_technology_readiness_top_level_payload,
    _repair_technology_readiness_truncated_payload,
    _store_phase_output,
    build_technology_readiness_prompt,
    run_phase_node,
    workflow_phase_sequence_for_state,
)
from exporters import (  # noqa: E402
    _technology_readiness_phase_output_digest,
    summarize_phase_outputs,
)
from prompts.loader import PHASE_MODULE_MAP, build_prompt as build_loader_prompt
from state import (
    PhaseStatus,
    ProjectState,
    RoadmapPhase,
    TECHNOLOGY_READINESS_OUTPUT_MODELS,
    TechnologyReadinessNextLevelRecommendationsOutput,
    TechnologyReadinessReadinessRoadmapOutput,
    TechnologyReadinessScopeOutput,
    validate_technology_readiness_output,
)
from tools.technology_readiness import (
    EVIDENCE_CATEGORIES,
    IP_PROTECTION_AXES,
    READINESS_VERDICT_CODES,
    RESEARCH_INDUSTRY_CRITERIA,
    TRL_PHASES,
    build_claim_ledger,
    build_readiness_radar_scorecard,
    build_stage_gate_decision,
    build_tto_handoff_package,
    compute_alignment_score,
    compute_evidence_sufficiency,
    normalize_trl,
    overclaim_warnings,
    rank_technology_readiness_portfolio,
    unknown_evidence_categories,
)
from workflow_templates import (
    STRATEGIC_AUDIT_PHASE_SEQUENCE,
    TECHNOLOGY_READINESS_PHASE_LABELS,
    TECHNOLOGY_READINESS_PHASE_SEQUENCE,
    get_workflow_phase_sequence,
)


PROMPT_DIR = ROOT / "prompts" / "technology_readiness"
DEMO_RUNBOOK = ROOT / "docs" / "demo" / "technology-readiness" / "RUNBOOK.md"


def make_llm_response(text: str, input_tokens: int = 10, output_tokens: int = 5) -> LLMResponse:
    return LLMResponse(
        text=text,
        ok=True,
        model_used="claude-haiku-4-5-20251001",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=0.01,
    )


def technology_readiness_contract_payloads() -> dict[str, dict]:
    return {
        "scope": {
            "technology_name": "Contract coating",
            "assessment_boundary": "bench prototype through controlled validation planning",
            "target_environment": "pilot coating line",
            "intended_next_milestone": "TRL 4 evidence package",
            "stakeholders": ["research lead", "technology transfer office"],
            "constraints": ["IP review before external demos"],
            "assumptions": ["bench results are directional"],
            "validation_questions": ["Can repeatability be shown?"],
            "evidence_gaps": ["controlled_validation"],
            "confidence": "medium",
        },
        "scientific_inventory": {
            "scientific_basis": ["published chemistry analogy"],
            "critical_components": ["coating precursor"],
            "current_experiments": ["small batch proof of concept"],
            "known_limitations": ["repeatability not demonstrated"],
            "evidence_items": [{"evidence_id": "ev-science", "category": "scientific_basis"}],
            "missing_evidence": ["controlled_validation"],
            "confidence": "preliminary",
        },
        "trl_diagnosis": {
            "current_trl": 3,
            "target_trl": 4,
            "confidence": "medium",
            "current_phase_name": "Protection and proof of concept",
            "evidence_supporting_current_trl": ["ev-science"],
            "why_not_higher": "Controlled validation and reproducibility are missing.",
            "evidence_gaps": ["reproducibility", "controlled_validation"],
            "legal_or_certification_disclaimer": "This is not TRL certification.",
        },
        "research_industry_alignment": {
            "criteria_scores": {
                criterion: {
                    "score": 3,
                    "evidence": "directional planning evidence",
                    "gap": "validation gap",
                    "recommendation": "collect targeted evidence",
                }
                for criterion in RESEARCH_INDUSTRY_CRITERIA
            },
            "overall_alignment_score": 3.0,
            "top_alignment_strengths": ["clear industrial use case"],
            "top_alignment_gaps": ["no partner feedback"],
            "prioritized_industrial_applications": ["pilot coating line"],
            "confidence": "medium",
        },
        "ip_protection_axis": {
            "material_composition": {
                "preliminary_assessment": "Promising but uncertain.",
                "evidence": ["ev-science"],
                "gap": "specialist review missing",
                "disclosure_risk": "Review before external demos.",
                "recommended_review": "Specialist review required.",
            },
            "synthesis_method": {},
            "specific_use": {},
            "device_or_system": {},
            "critical_parameters": {},
            "know_how": {},
            "ip_risk_notes": ["Do not claim legal patentability."],
            "specialist_review_required": True,
            "confidence": "low",
        },
        "next_level_recommendations": {
            "current_trl": 3,
            "next_target_trl": 4,
            "current_phase_name": "Protection and proof of concept",
            "next_phase_name": "Controlled technical validation",
            "main_gap_to_next_level": "Repeatable controlled validation is missing.",
            "recommended_actions": [{"owner": "Technical lead", "action": "run repeatability protocol"}],
            "required_tests": ["repeatability test"],
            "required_evidence": ["reproducibility", "controlled_validation", "ip_review"],
            "expected_deliverables": ["validation report"],
            "risks_to_reduce": ["false-positive lab result"],
            "suggested_owners": ["Technical lead", "IP specialist"],
            "estimated_time_range": "6-12 months",
            "advancement_criteria": ["repeatable result under controlled protocol"],
            "confidence": "medium",
        },
        "technical_validation_plan": {
            "validation_tests": [{"name": "repeatability protocol", "owner": "Technical lead"}],
            "acceptance_criteria": ["three repeatable runs"],
            "measurement_plan": ["capture batch variance"],
            "failure_modes": ["coating instability"],
            "evidence_to_collect": ["controlled_validation"],
            "confidence": "medium",
        },
        "industrial_transfer_plan": {
            "ideal_industrial_partner": "Pilot manufacturing partner",
            "partner_validation_needed": ["partner feedback"],
            "minimum_transfer_package": ["non-confidential brief", "validation protocol"],
            "transfer_model_options": ["sponsored validation"],
            "negotiation_risks": ["premature disclosure"],
            "evidence_required_before_transfer": ["ip_review", "partner_feedback"],
            "confidence": "low",
        },
        "readiness_roadmap": {
            "roadmap_phases": [
                {
                    "trl": "TRL 4",
                    "phase_name": "Controlled technical validation",
                    "time_range": "6-12 months",
                    "objective": "validate repeatability",
                    "evidence_needed": ["controlled_validation"],
                    "decision_gate": "repeatability gate",
                }
            ],
            "timeline": [{"phase": "TRL 4", "range": "6-12 months"}],
            "decision_gates": [{"gate": "repeatability gate"}],
            "resources_needed": ["technical lead"],
            "go_no_go_criteria": ["controlled validation completed"],
            "confidence": "medium",
        },
        "executive_summary": {
            "current_trl": 3,
            "target_trl": 4,
            "readiness_verdict_code": "ready_for_proof_of_concept",
            "readiness_verdict": "Ready for controlled validation planning, not advancement.",
            "top_blockers": ["reproducibility", "IP review"],
            "recommended_next_step": "Run controlled validation protocol.",
            "operator_summary": "Evidence-backed estimate requires operator review.",
            "confidence": "medium",
        },
    }


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


def test_technology_readiness_phase_contracts_cover_validators_state_and_labels():
    payloads = technology_readiness_contract_payloads()
    state = ProjectState(project_id="tech-contracts", project_type="technology_readiness", brief="x")

    assert tuple(TECHNOLOGY_READINESS_OUTPUT_MODELS) == TECHNOLOGY_READINESS_PHASE_SEQUENCE
    assert tuple(TECHNOLOGY_READINESS_PHASE_LABELS) == TECHNOLOGY_READINESS_PHASE_SEQUENCE
    assert set(payloads) == set(TECHNOLOGY_READINESS_PHASE_SEQUENCE)

    for phase in TECHNOLOGY_READINESS_PHASE_SEQUENCE:
        assert phase in ProjectState.model_fields, f"{phase} missing ProjectState storage field"
        assert TECHNOLOGY_READINESS_PHASE_LABELS[phase].strip(), f"{phase} missing display label"
        model = TECHNOLOGY_READINESS_OUTPUT_MODELS[phase]
        output = validate_technology_readiness_output(phase, payloads[phase])
        assert isinstance(output, model), f"{phase} validator did not return typed model"
        setattr(state, phase, output)
        assert getattr(state, phase) is output


def test_technology_readiness_runtime_prompts_are_file_backed_and_loader_aligned():
    state = ProjectState(project_id="tech-prompt-contracts", project_type="technology_readiness", brief="x")

    for phase in TECHNOLOGY_READINESS_PHASE_SEQUENCE:
        prompt_file = PROMPT_DIR / f"{phase}.md"
        prompt_text = prompt_file.read_text(encoding="utf-8").strip()

        assert phase in PROMPT_BUILDERS, f"{phase} missing runtime prompt builder"
        assert PHASE_MODULE_MAP.get(phase) == f"technology_readiness/{phase}.md"
        assert build_loader_prompt(phase, include_router=False).strip() == prompt_text

        # Runtime-active TR prompts are built by orchestrator prompt builders,
        # which read the same prompt files and add assessment context.
        built = PROMPT_BUILDERS[phase](state)
        assert built == build_technology_readiness_prompt(state, phase)
        assert prompt_text in built
        assert "ASSESSMENT CONTEXT:" in built
        assert "Return the requested JSON object only." in built


def test_technology_readiness_export_summaries_cover_every_active_phase():
    payloads = technology_readiness_contract_payloads()
    state = ProjectState(
        project_id="tech-export-contracts",
        project_name="Contract coating",
        project_type="technology_readiness",
        brief="Assess contract coating readiness.",
    )

    for phase in TECHNOLOGY_READINESS_PHASE_SEQUENCE:
        setattr(state, phase, validate_technology_readiness_output(phase, payloads[phase]))
        state.phase_status[phase] = PhaseStatus.COMPLETED
        state.phase_confidence[phase] = 0.8

    summary = summarize_phase_outputs(state)
    for phase in TECHNOLOGY_READINESS_PHASE_SEQUENCE:
        digest = _technology_readiness_phase_output_digest(state, phase)
        assert digest.strip(), f"{phase} missing export/report digest"
        assert TECHNOLOGY_READINESS_PHASE_LABELS[phase] in summary
        assert digest in summary


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

    assert "one top-level JSON object" in text
    assert "not an array" in text
    assert "do not return multiple JSON objects" in text
    assert "recommended_actions" in text
    assert "array field inside the single object" in text
    assert "full phase output must be one object" in text
    assert "gate-critical fields" in text
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


def test_technology_readiness_singleton_list_repair_accepts_only_one_object():
    payload = technology_readiness_contract_payloads()["next_level_recommendations"]

    repaired, changed = _repair_technology_readiness_top_level_payload(
        "next_level_recommendations",
        [payload],
    )

    assert changed is True
    assert repaired == payload
    assert _parsed_json_matches_phase("next_level_recommendations", repaired)

    for invalid in ([], ["text"], [payload, payload]):
        repaired, changed = _repair_technology_readiness_top_level_payload(
            "next_level_recommendations",
            invalid,
        )
        assert changed is False
        assert repaired == invalid
        assert not _parsed_json_matches_phase("next_level_recommendations", repaired)


def test_technology_readiness_multi_candidate_repair_selects_one_full_valid_output():
    payload = {
        key: value
        for key, value in technology_readiness_contract_payloads()["next_level_recommendations"].items()
        if key != "confidence"
    }
    incomplete = {
        "current_trl": 3,
        "next_target_trl": 4,
        "current_phase_name": "Protection and proof of concept",
    }
    parsed = [
        {"foo": "bar"},
        "noise",
        incomplete,
        payload,
    ]

    repaired, changed = _repair_technology_readiness_top_level_payload(
        "next_level_recommendations",
        parsed,
    )

    assert changed is True
    assert repaired == payload
    assert _parsed_json_matches_phase("next_level_recommendations", repaired)


def test_technology_readiness_multi_candidate_repair_rejects_zero_valid_candidates():
    payload = technology_readiness_contract_payloads()["next_level_recommendations"]
    incomplete = dict(payload)
    incomplete.pop("recommended_actions")
    parsed = [
        {"foo": "bar"},
        incomplete,
        "noise",
    ]

    repaired, changed = _repair_technology_readiness_top_level_payload(
        "next_level_recommendations",
        parsed,
    )
    diagnostic = _invalid_json_shape_diagnostic(
        "next_level_recommendations",
        repaired,
        json.dumps(parsed),
    )

    assert changed is False
    assert repaired == parsed
    assert not _parsed_json_matches_phase("next_level_recommendations", repaired)
    assert "candidate_dict_count=2" in diagnostic
    assert "valid_candidate_count=0" in diagnostic
    assert "reason=no_valid_candidate" in diagnostic


def test_technology_readiness_multi_candidate_repair_selects_technical_validation_plan():
    payload = {
        "validation_tests": [{"name": "repeatability protocol"}],
        "acceptance_criteria": ["three repeatable runs"],
        "evidence_to_collect": ["raw hydrogen uptake logs"],
    }
    parsed = [
        {"foo": "bar"},
        {"acceptance_criteria": ["three repeatable runs"]},
        payload,
    ]

    repaired, changed = _repair_technology_readiness_top_level_payload(
        "technical_validation_plan",
        parsed,
    )

    assert changed is True
    assert repaired == payload
    assert _parsed_json_matches_phase("technical_validation_plan", repaired)


def test_technology_readiness_multi_candidate_repair_rejects_zero_technical_validation_candidates():
    parsed = [
        {"foo": "bar"},
        {"acceptance_criteria": ["three repeatable runs"]},
    ]

    repaired, changed = _repair_technology_readiness_top_level_payload(
        "technical_validation_plan",
        parsed,
    )
    diagnostic = _invalid_json_shape_diagnostic(
        "technical_validation_plan",
        repaired,
        json.dumps(parsed),
    )

    assert changed is False
    assert repaired == parsed
    assert not _parsed_json_matches_phase("technical_validation_plan", repaired)
    assert "candidate_dict_count=2" in diagnostic
    assert "valid_candidate_count=0" in diagnostic
    assert "reason=no_valid_candidate" in diagnostic


def test_technology_readiness_multi_candidate_repair_rejects_ambiguous_technical_validation_candidates():
    first_payload = {
        "validation_tests": [{"name": "repeatability protocol"}],
        "acceptance_criteria": ["three repeatable runs"],
        "evidence_to_collect": ["raw hydrogen uptake logs"],
    }
    second_payload = {
        "validation_tests": [{"name": "measurement stability protocol"}],
        "acceptance_criteria": ["calibrated instrument variation documented"],
        "evidence_to_collect": ["gauge repeatability record"],
    }
    parsed = [first_payload, second_payload]

    repaired, changed = _repair_technology_readiness_top_level_payload(
        "technical_validation_plan",
        parsed,
    )
    diagnostic = _invalid_json_shape_diagnostic(
        "technical_validation_plan",
        repaired,
        json.dumps(parsed),
    )

    assert changed is False
    assert repaired == parsed
    assert not _parsed_json_matches_phase("technical_validation_plan", repaired)
    assert "candidate_dict_count=2" in diagnostic
    assert "valid_candidate_count=2" in diagnostic
    assert "reason=ambiguous_multiple_candidates" in diagnostic


def test_technology_readiness_multi_candidate_repair_rejects_ambiguous_candidates():
    payload = technology_readiness_contract_payloads()["next_level_recommendations"]
    second_payload = {
        **payload,
        "main_gap_to_next_level": "Industrial partner validation is also missing.",
    }
    parsed = [payload, second_payload]

    repaired, changed = _repair_technology_readiness_top_level_payload(
        "next_level_recommendations",
        parsed,
    )
    diagnostic = _invalid_json_shape_diagnostic(
        "next_level_recommendations",
        repaired,
        json.dumps(parsed),
    )

    assert changed is False
    assert repaired == parsed
    assert not _parsed_json_matches_phase("next_level_recommendations", repaired)
    assert "candidate_dict_count=2" in diagnostic
    assert "valid_candidate_count=2" in diagnostic
    assert "reason=ambiguous_multiple_candidates" in diagnostic


def test_technology_readiness_multi_candidate_repair_rejects_list_of_strings():
    parsed = ["current_trl", "next_target_trl"]

    repaired, changed = _repair_technology_readiness_top_level_payload(
        "next_level_recommendations",
        parsed,
    )
    diagnostic = _invalid_json_shape_diagnostic(
        "next_level_recommendations",
        repaired,
        json.dumps(parsed),
    )

    assert changed is False
    assert repaired == parsed
    assert not _parsed_json_matches_phase("next_level_recommendations", repaired)
    assert "candidate_dict_count=0" in diagnostic
    assert "valid_candidate_count=0" in diagnostic
    assert "reason=no_valid_candidate" in diagnostic


def test_technology_readiness_truncated_payload_repair_recovers_completed_top_level_fields():
    text = """
{
  "current_trl": 2,
  "next_target_trl": 3,
  "current_phase_name": "Technology concept",
  "next_phase_name": "Experimental proof of concept",
  "main_gap_to_next_level": "Replication evidence is missing.",
  "required_evidence": [
    "replicated hydrogen uptake logs"
  ],
  "advancement_criteria": [
    "operator-reviewed reproducibility package"
  ],
  "recommended_actions": [
    "Run Sprint 0 replication package."
  ],
  "required_tests": [
"""

    repaired = _repair_technology_readiness_truncated_payload(
        "next_level_recommendations",
        text,
    )

    assert repaired == {
        "current_trl": 2,
        "next_target_trl": 3,
        "current_phase_name": "Technology concept",
        "next_phase_name": "Experimental proof of concept",
        "main_gap_to_next_level": "Replication evidence is missing.",
        "required_evidence": ["replicated hydrogen uptake logs"],
        "advancement_criteria": ["operator-reviewed reproducibility package"],
        "recommended_actions": ["Run Sprint 0 replication package."],
    }


def test_technology_readiness_truncated_payload_repair_requires_phase_anchors():
    text = """
{
  "current_trl": 2,
  "next_target_trl": 3,
  "main_gap_to_next_level": "Replication evidence is missing.",
  "recommended_actions": [
    "Run Sprint 0 replication package."
  ],
  "required_tests": [
"""

    assert _repair_technology_readiness_truncated_payload(
        "next_level_recommendations",
        text,
    ) is None
    assert _repair_technology_readiness_truncated_payload("strategy", text) is None

    missing_gate_field = """
{
  "current_trl": 2,
  "next_target_trl": 3,
  "main_gap_to_next_level": "Replication evidence is missing.",
  "recommended_actions": [
    "Run Sprint 0 replication package."
  ],
  "required_evidence": [
    "replicated hydrogen uptake logs"
  ],
  "required_tests": [
"""
    assert _repair_technology_readiness_truncated_payload(
        "next_level_recommendations",
        missing_gate_field,
    ) is None


def test_next_level_recommendations_runtime_repairs_truncated_top_level_object():
    state = ProjectState(
        project_id="tr-next-level-truncated-object",
        project_type="technology_readiness",
        brief="Assess a coating for transfer readiness.",
    )
    text = """
{
  "current_trl": 2,
  "next_target_trl": 3,
  "current_phase_name": "Technology concept",
  "next_phase_name": "Experimental proof of concept",
  "main_gap_to_next_level": "Replication evidence is missing.",
  "required_evidence": [
    "replicated hydrogen uptake logs"
  ],
  "advancement_criteria": [
    "operator-reviewed reproducibility package"
  ],
  "recommended_actions": [
    "Run Sprint 0 replication package."
  ],
  "required_tests": [
"""
    call_mock = AsyncMock(return_value=make_llm_response(text, 10, 5))

    with patch("orchestrator.call_llm", new=call_mock):
        with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
            updated = asyncio.run(run_phase_node(state, "next_level_recommendations"))

    assert call_mock.await_count == 1
    assert updated.phase_status["next_level_recommendations"] == PhaseStatus.COMPLETED
    assert isinstance(
        updated.next_level_recommendations,
        TechnologyReadinessNextLevelRecommendationsOutput,
    )
    assert updated.next_level_recommendations.recommended_actions == [
        {"value": "Run Sprint 0 replication package."}
    ]


def test_technology_readiness_singleton_list_repair_does_not_apply_to_strategic_audit():
    payload = {"executive_strategy": "Do not unwrap strategic output."}

    repaired, changed = _repair_technology_readiness_top_level_payload("strategy", [payload])

    assert changed is False
    assert repaired == [payload]
    assert not _parsed_json_matches_phase("strategy", repaired)


def test_technology_readiness_multi_candidate_repair_does_not_apply_to_strategic_audit():
    payload = {"executive_strategy": "Do not unwrap strategic output."}
    parsed = [payload, payload]

    repaired, changed = _repair_technology_readiness_top_level_payload("strategy", parsed)
    diagnostic = _invalid_json_shape_diagnostic("strategy", repaired, json.dumps(parsed))

    assert changed is False
    assert repaired == parsed
    assert not _parsed_json_matches_phase("strategy", repaired)
    assert "candidate_dict_count" not in diagnostic
    assert "valid_candidate_count" not in diagnostic


def test_next_level_recommendations_runtime_unwraps_singleton_list_and_normalizes_actions():
    state = ProjectState(
        project_id="tr-next-level-singleton-list",
        project_type="technology_readiness",
        brief="Assess a coating for transfer readiness.",
    )
    payload = {
        **technology_readiness_contract_payloads()["next_level_recommendations"],
        "recommended_actions": [
            "Run three repeatability batches under a controlled protocol.",
            "Complete preliminary IP specialist review before external demos.",
        ],
    }
    call_mock = AsyncMock(return_value=make_llm_response(json.dumps([payload]), 10, 5))

    with patch("orchestrator.call_llm", new=call_mock):
        with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
            updated = asyncio.run(run_phase_node(state, "next_level_recommendations"))

    assert call_mock.await_count == 1
    assert updated.phase_status["next_level_recommendations"] == PhaseStatus.COMPLETED
    assert isinstance(
        updated.next_level_recommendations,
        TechnologyReadinessNextLevelRecommendationsOutput,
    )
    assert updated.next_level_recommendations.recommended_actions == [
        {"value": "Run three repeatability batches under a controlled protocol."},
        {"value": "Complete preliminary IP specialist review before external demos."},
    ]


def test_next_level_recommendations_runtime_selects_multi_candidate_and_normalizes_actions():
    state = ProjectState(
        project_id="tr-next-level-multi-candidate-list",
        project_type="technology_readiness",
        brief="Assess a coating for transfer readiness.",
    )
    payload = {
        **technology_readiness_contract_payloads()["next_level_recommendations"],
        "recommended_actions": [
            "Run three repeatability batches under a controlled protocol.",
            "Complete preliminary IP specialist review before external demos.",
        ],
    }
    incomplete = {
        "current_trl": 3,
        "next_target_trl": 4,
        "current_phase_name": "Protection and proof of concept",
    }
    parsed_response = [
        {"foo": "bar"},
        incomplete,
        "noise",
        payload,
    ]
    call_mock = AsyncMock(return_value=make_llm_response(json.dumps(parsed_response), 10, 5))

    with patch("orchestrator.call_llm", new=call_mock):
        with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
            updated = asyncio.run(run_phase_node(state, "next_level_recommendations"))

    assert call_mock.await_count == 1
    assert updated.phase_status["next_level_recommendations"] == PhaseStatus.COMPLETED
    assert isinstance(
        updated.next_level_recommendations,
        TechnologyReadinessNextLevelRecommendationsOutput,
    )
    assert updated.next_level_recommendations.recommended_actions == [
        {"value": "Run three repeatability batches under a controlled protocol."},
        {"value": "Complete preliminary IP specialist review before external demos."},
    ]


def test_technology_readiness_normalizes_dict_shaped_string_lists_into_typed_models():
    state = ProjectState(project_id="store-tech-dict-lists", project_type="technology_readiness", brief="x")
    payload = {
        "technology_name": "NanoSeal-H2",
        "assessment_boundary": "prototype chemistry only",
        "target_environment": "pilot line",
        "intended_next_milestone": "controlled validation",
        "stakeholders": [
            {"role": "Technology Transfer Office", "note": "not formally engaged"},
        ],
        "constraints": [
            {"constraint": "No external demo", "note": "IP review incomplete"},
        ],
        "assumptions": [
            {"assumption": "Bench result transfers", "status": "unproven"},
        ],
        "validation_questions": [
            {"question": "Can repeatability be shown?", "owner": "technical lead"},
        ],
        "evidence_gaps": [
            {"category": "controlled_validation", "note": "missing"},
        ],
        "confidence": "medium",
    }

    _store_phase_output(state, "scope", payload)

    assert isinstance(state.scope, TechnologyReadinessScopeOutput)
    assert state.scope.technology_name == "NanoSeal-H2"
    assert state.scope.stakeholders == ["Technology Transfer Office - not formally engaged"]
    assert state.scope.constraints == ["constraint: No external demo; note: IP review incomplete"]
    assert state.scope.assumptions == ["assumption: Bench result transfers; status: unproven"]
    assert state.scope.validation_questions == ["technical lead - Can repeatability be shown?"]
    assert state.scope.evidence_gaps == ["controlled_validation - missing"]


def test_technology_readiness_normalizes_list_model_fields_from_strings_and_nested_dict_lists():
    output = validate_technology_readiness_output(
        "readiness_roadmap",
        {
            "roadmap_phases": [
                "Pre-TRL 3 diagnosis",
                {
                    "trl": "TRL 4",
                    "phase_name": "Controlled technical validation",
                    "evidence_needed": [
                        {"category": "controlled_validation", "note": "repeatability protocol"},
                    ],
                },
            ],
            "timeline": ["6-12 months"],
            "decision_gates": ["controlled validation gate"],
            "resources_needed": [{"role": "technical lead"}],
            "go_no_go_criteria": [{"criterion": "three repeatable runs"}],
            "confidence": "medium",
        },
    )

    assert isinstance(output, TechnologyReadinessReadinessRoadmapOutput)
    assert all(isinstance(item, RoadmapPhase) for item in output.roadmap_phases)
    assert output.roadmap_phases[0].phase_name == "Pre-TRL 3 diagnosis"
    assert output.roadmap_phases[1].evidence_needed == ["controlled_validation - repeatability protocol"]
    assert output.timeline == [{"value": "6-12 months"}]
    assert output.decision_gates == [{"value": "controlled validation gate"}]
    assert output.resources_needed == ["role: technical lead"]
    assert output.go_no_go_criteria == ["criterion: three repeatable runs"]


def test_technology_readiness_schema_invalid_first_response_gets_one_repair_attempt():
    state = ProjectState(project_id="tr-schema-repair", project_type="technology_readiness", brief="Assess a coating.")
    invalid = {
        "technology_name": "NanoSeal-H2",
        "assessment_boundary": {"invalid": "dict for string field"},
        "target_environment": "pilot line",
        "intended_next_milestone": "controlled validation",
        "stakeholders": ["TTO"],
        "constraints": [],
        "assumptions": [],
        "confidence": "medium",
    }
    repaired = {
        "technology_name": "NanoSeal-H2",
        "assessment_boundary": "prototype chemistry only",
        "target_environment": "pilot line",
        "intended_next_milestone": "controlled validation",
        "stakeholders": ["TTO"],
        "constraints": [],
        "assumptions": [],
        "confidence": "medium",
    }
    call_mock = AsyncMock(
        side_effect=[
            make_llm_response(json.dumps(invalid), 10, 5),
            make_llm_response(json.dumps(repaired), 11, 6),
        ]
    )

    with patch("orchestrator.call_llm", new=call_mock):
        with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
            updated = asyncio.run(run_phase_node(state, "scope"))

    assert call_mock.await_count == 2
    assert updated.phase_status["scope"] == PhaseStatus.COMPLETED
    assert isinstance(updated.scope, TechnologyReadinessScopeOutput)
    assert updated.scope.assessment_boundary == "prototype chemistry only"
    assert updated.budget_consumed["llm_call_count"] == 2


def test_technology_readiness_schema_invalid_retry_fails_after_exactly_one_repair_attempt():
    state = ProjectState(project_id="tr-schema-repair-fails", project_type="technology_readiness", brief="Assess a coating.")
    invalid = {
        "technology_name": "NanoSeal-H2",
        "assessment_boundary": {"invalid": "dict for string field"},
        "target_environment": "pilot line",
        "intended_next_milestone": "controlled validation",
        "stakeholders": ["TTO"],
        "constraints": [],
        "assumptions": [],
        "confidence": "medium",
    }
    still_invalid = {
        **invalid,
        "target_environment": {"still": "invalid"},
    }
    call_mock = AsyncMock(
        side_effect=[
            make_llm_response(json.dumps(invalid), 10, 5),
            make_llm_response(json.dumps(still_invalid), 11, 6),
        ]
    )

    with patch("orchestrator.call_llm", new=call_mock):
        with patch("priors.get_prior_hint", new=AsyncMock(return_value="")):
            updated = asyncio.run(run_phase_node(state, "scope"))

    assert call_mock.await_count == 2
    assert updated.phase_status["scope"] == PhaseStatus.FAILED
    assert updated.phase_confidence["scope"] == 0.0
    assert updated.scope is None


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


def test_stage_gate_decision_blocks_advancement_without_required_evidence():
    trl_3_to_4 = build_stage_gate_decision(
        {
            "current_trl": 3,
            "next_target_trl": 4,
            "evidence_categories": ["scientific_basis", "proof_of_concept"],
            "required_evidence": ["reproducibility"],
        }
    )
    assert trl_3_to_4["decision"] == "hold"
    assert any("reproducibility" in gap for gap in trl_3_to_4["blocking_gaps"])

    trl_4_to_5 = build_stage_gate_decision(
        {
            "current_trl": 4,
            "next_target_trl": 5,
            "evidence_categories": ["reproducibility", "controlled_validation"],
        }
    )
    assert trl_4_to_5["decision"] == "hold"
    assert any("relevant_environment" in gap for gap in trl_4_to_5["blocking_gaps"])

    trl_6_to_7 = build_stage_gate_decision(
        {
            "current_trl": 6,
            "next_target_trl": 7,
            "evidence_categories": ["reproducibility", "controlled_validation", "relevant_environment"],
        }
    )
    assert trl_6_to_7["decision"] == "hold"
    assert any("industrial_validation" in gap or "partner_feedback" in gap for gap in trl_6_to_7["blocking_gaps"])

    unknown_current = build_stage_gate_decision(
        {
            "current_trl": 0,
            "next_target_trl": 3,
            "evidence_categories": ["scientific_basis", "proof_of_concept"],
        }
    )
    assert unknown_current["decision"] != "proceed"


def test_stage_gate_proceed_requires_supplied_evidence_and_ip_conditions():
    proceed = build_stage_gate_decision(
        {
            "current_trl": 3,
            "next_target_trl": 4,
            "evidence_categories": ["reproducibility"],
            "required_evidence": ["reproducibility"],
        }
    )
    assert proceed["decision"] == "proceed"

    conditional = build_stage_gate_decision(
        {
            "current_trl": 3,
            "next_target_trl": 4,
            "evidence_categories": ["controlled_validation"],
            "ip_claims_present": True,
        }
    )
    assert conditional["decision"] == "proceed_with_conditions"
    assert "ip_review" in conditional["required_evidence"]


def test_claim_ledger_downgrades_missing_evidence_and_flags_ip_claims():
    ledger = build_claim_ledger(
        {
            "current_trl": 3,
            "confidence": "high",
            "why_not_higher": "controlled validation missing",
            "required_evidence": ["controlled_validation"],
            "readiness_verdict": "ready only for proof-of-concept review",
            "ip_claims": ["material composition may have a preliminary protection path"],
            "evidence_categories": ["scientific_basis"],
        }
    )

    current_claim = next(claim for claim in ledger["claims"] if claim["claim_id"] == "trl-current")
    ip_claim = next(claim for claim in ledger["claims"] if claim["claim_id"] == "ip-1")

    assert current_claim["label"] == "hypothesis"
    assert current_claim["confidence"] == "low"
    assert "controlled validation missing" in current_claim["limitations"]
    assert current_claim["validate_with"] == ["controlled_validation"]
    assert ip_claim["label"] != "fact"
    assert ip_claim["validate_with"] == ["ip_review"]
    assert any("Unsupported high-confidence" in warning for warning in ledger["warnings"])


def test_claim_ledger_uses_supplied_evidence_ids_without_inventing_them():
    ledger = build_claim_ledger(
        {
            "current_trl": 4,
            "confidence": "high",
            "evidence_ids": ["ev-controlled"],
            "evidence_categories": ["controlled_validation"],
        }
    )

    current_claim = next(claim for claim in ledger["claims"] if claim["claim_id"] == "trl-current")
    assert current_claim["label"] == "fact"
    assert current_claim["evidence_ids"] == ["ev-controlled"]
    assert ledger["warnings"] == []


def test_readiness_radar_caps_scores_without_evidence():
    empty = build_readiness_radar_scorecard({"current_trl": 6, "research_industry_alignment_score": 5})

    assert set(empty) == {
        "technical_readiness",
        "evidence_readiness",
        "ip_readiness",
        "market_application_readiness",
        "scaling_readiness",
        "regulatory_readiness",
        "transfer_readiness",
        "partner_readiness",
    }
    assert empty["technical_readiness"]["score"] <= 1
    assert empty["evidence_readiness"]["confidence"] == "low"
    assert empty["ip_readiness"]["score"] <= 2
    assert empty["partner_readiness"]["score"] <= 2

    stronger = build_readiness_radar_scorecard(
        {
            "current_trl": 5,
            "research_industry_alignment_score": 4,
            "evidence_categories": [
                "scientific_basis",
                "proof_of_concept",
                "reproducibility",
                "controlled_validation",
                "relevant_environment",
                "ip_review",
                "partner_feedback",
            ],
        }
    )
    assert stronger["evidence_readiness"]["score"] > empty["evidence_readiness"]["score"]
    assert stronger["ip_readiness"]["score"] > empty["ip_readiness"]["score"]
    assert stronger["partner_readiness"]["score"] > empty["partner_readiness"]["score"]


def test_tto_handoff_package_keeps_confidential_and_non_confidential_boundaries():
    package = build_tto_handoff_package(
        {
            "technology_name": "Lab coating",
            "current_trl": 3,
            "evidence_categories": ["scientific_basis", "proof_of_concept"],
        }
    )

    assert "non_confidential_summary" in package
    assert "confidential_technical_appendix_outline" in package
    assert "disclosure_risk_notes" in package
    combined = "\n".join(str(value) for value in package.values())
    assert "Disclosure risk before publication or external demos" in combined
    assert "Specialist review required" in combined
    assert "not legal advice" in combined
    assert "legal patentability" in combined
    assert "guaranteed transfer" in combined
    assert "legally patentable" not in combined.lower()


def test_portfolio_helper_ranks_supported_evidence_above_unsupported_high_trl():
    ranked = rank_technology_readiness_portfolio(
        [
            {
                "project_id": "unsupported-high-trl",
                "technology_name": "Unsupported high TRL",
                "current_trl": 6,
                "target_trl": 7,
                "research_industry_alignment_score": 5,
                "evidence_categories": ["scientific_basis"],
            },
            {
                "project_id": "supported-validation",
                "technology_name": "Supported validation",
                "current_trl": 4,
                "target_trl": 5,
                "research_industry_alignment_score": 4,
                "evidence_categories": [
                    "scientific_basis",
                    "proof_of_concept",
                    "reproducibility",
                    "controlled_validation",
                    "relevant_environment",
                    "ip_review",
                    "partner_feedback",
                ],
            },
            {
                "project_id": "early-uncertain",
                "technology_name": "Early uncertain research",
                "current_trl": 2,
                "research_industry_alignment_score": 2,
                "evidence_categories": [],
            },
        ]
    )

    assert ranked[0]["project_id"] == "supported-validation"
    assert ranked[0]["recommended_priority"] in {"high", "medium"}
    assert next(row for row in ranked if row["project_id"] == "unsupported-high-trl")["recommended_priority"] == "defer"
    assert next(row for row in ranked if row["project_id"] == "early-uncertain")["recommended_priority"] == "defer"


def test_technology_readiness_demo_runbook_covers_wave_b_flow():
    assert DEMO_RUNBOOK.exists()
    text = DEMO_RUNBOOK.read_text(encoding="utf-8")

    for required in (
        "technology_readiness",
        "Technology Readiness & Transfer Audit",
        "/templates",
        "brief.md",
        "supporting-data.md",
        "Current TRL likely 3",
        "Next target likely TRL 4",
        "reproducibility not demonstrated",
        "controlled validation protocol",
        "technology_readiness_workbook",
        "Claim ledger",
        "Stage-gate decision",
        "not TRL certification",
        "legal patentability advice",
        "guarantee of commercial transfer",
    ):
        assert required in text


def test_template_package_does_not_import_missing_base_modules():
    source = (ROOT / "templates" / "technology_readiness" / "__init__.py").read_text(encoding="utf-8")

    assert technology_template.TEMPLATE["project_type"] == "technology_readiness"
    assert technology_template.TEMPLATE["phase_sequence"] == TECHNOLOGY_READINESS_PHASE_SEQUENCE
    assert "from templates.registry" not in source
    assert "from templates.base" not in source
