import asyncio
from pathlib import Path
import sys
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api
from ingestion_contract import normalize_project_ingestion
from state import (
    PhaseStatus,
    ProjectState,
    TechnologyReadinessExecutiveSummaryOutput,
    TechnologyReadinessScopeOutput,
)
from workflow_templates import (
    STRATEGIC_AUDIT_PHASE_SEQUENCE,
    TECHNOLOGY_READINESS_PHASE_SEQUENCE,
    all_editable_phases,
    get_workflow_phase_sequence,
    list_workflow_templates,
)


def _scope_payload() -> dict:
    return {
        "technology_name": "Lab coating",
        "assessment_boundary": "prototype chemistry only",
        "target_environment": "pilot manufacturing line",
        "intended_next_milestone": "proof of concept",
        "stakeholders": ["principal investigator"],
        "constraints": [],
        "assumptions": ["operator-reviewed evidence only"],
        "confidence": "preliminary",
    }


def test_workflow_templates_metadata_includes_technology_readiness():
    templates = {template["project_type"]: template for template in list_workflow_templates()}

    assert get_workflow_phase_sequence("strategic_audit") == STRATEGIC_AUDIT_PHASE_SEQUENCE
    assert get_workflow_phase_sequence("ai_readiness") == STRATEGIC_AUDIT_PHASE_SEQUENCE
    assert get_workflow_phase_sequence("automation_roi") == STRATEGIC_AUDIT_PHASE_SEQUENCE
    assert templates["technology_readiness"]["phase_sequence"] == TECHNOLOGY_READINESS_PHASE_SEQUENCE
    assert templates["technology_readiness"]["prompt_dir"] == "prompts/technology_readiness"
    assert "Technology Readiness" in templates["technology_readiness"]["label"]
    assert "scope" in all_editable_phases()
    assert "report" in all_editable_phases()


def test_templates_endpoint_lists_registered_templates():
    response = asyncio.run(api.list_templates())
    templates = {template["project_type"]: template for template in response["templates"]}

    assert "technology_readiness" in templates
    assert templates["technology_readiness"]["phase_sequence"] == TECHNOLOGY_READINESS_PHASE_SEQUENCE


def test_create_project_request_carries_project_type_through_ingestion():
    request = api.CreateProjectRequest(
        brief="Assess a lab prototype for transfer readiness.",
        project_type="technology_readiness_transfer",
    )

    normalized = normalize_project_ingestion(request)

    assert normalized.project_type == "technology_readiness"


def test_project_response_includes_project_type_and_template_phase_status():
    state = ProjectState(
        project_id="tech-response",
        project_name="Tech response",
        brief="Assess prototype readiness.",
        project_type="technology_readiness",
    )

    response = api._to_response(state)

    assert response.project_type == "technology_readiness"
    assert response.current_phase == "scope"
    assert tuple(response.phase_status) == TECHNOLOGY_READINESS_PHASE_SEQUENCE
    assert set(response.phase_status.values()) == {PhaseStatus.PENDING.value}


def test_technology_readiness_phase_payload_can_be_validated_and_applied():
    state = ProjectState(
        project_id="tech-edit",
        project_name="Tech edit",
        brief="Assess prototype readiness.",
        project_type="technology_readiness",
    )

    validated, changed_fields = api._validate_phase_payload("scope", _scope_payload())
    api._apply_phase_output(state, "scope", validated)
    api._finalize_phase_output_edit(state, "scope")

    assert isinstance(state.scope, TechnologyReadinessScopeOutput)
    assert state.phase_status["scope"] == PhaseStatus.COMPLETED
    assert state.phase_summaries["scope"].startswith("SCOPE:")
    assert "scope.technology_name" in changed_fields


def test_analysis_pending_phase_uses_active_template_sequence():
    state = ProjectState(
        project_id="tech-import",
        project_name="Tech import",
        brief="Assess prototype readiness.",
        project_type="technology_readiness",
    )
    state.scope = TechnologyReadinessScopeOutput(**_scope_payload())

    assert api._analysis_pending_phase_for_import(state) == "scope"

    state.executive_summary = TechnologyReadinessExecutiveSummaryOutput(
        current_trl=3,
        target_trl=4,
        readiness_verdict_code="ready_for_controlled_validation",
        readiness_verdict="Preliminary readiness for controlled validation after evidence review.",
        operator_summary="Evidence remains operator-reviewed and preliminary.",
    )

    assert api._analysis_pending_phase_for_import(state) == "executive_summary"


def test_technology_readiness_input_patch_restarts_at_scope():
    async def run_patch():
        state = ProjectState(
            project_id="tech-input-edit",
            project_name="Tech input edit",
            brief="Assess prototype readiness.",
            project_type="technology_readiness",
        )
        state.scope = TechnologyReadinessScopeOutput(**_scope_payload())
        state.phase_status["scope"] = PhaseStatus.COMPLETED

        with (
            patch("api.store.load", new=AsyncMock(return_value=state)),
            patch("api.store.save", new=AsyncMock()),
            patch("api._ensure_project_not_running", new=AsyncMock()),
        ):
            response = await api.patch_project_input(
                state.project_id,
                api.PatchProjectInputRequest(brief="Updated prototype readiness brief."),
            )
        return response, state

    response, state = asyncio.run(run_patch())

    assert response["next_phase"] == "scope"
    assert state.current_phase == "scope"
    assert state.phase_status["scope"] == PhaseStatus.STALE
