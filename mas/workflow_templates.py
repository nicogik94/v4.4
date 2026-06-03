"""Workflow template metadata used by the core orchestrator.

This repository does not have a source-level templates.registry/templates.base
implementation. Template selection is intentionally represented as plain
metadata so the default v4.4 workflow remains unchanged.
"""
from __future__ import annotations

from copy import deepcopy


DEFAULT_PROJECT_TYPE = "strategic_audit"
TECHNOLOGY_READINESS_PROJECT_TYPE = "technology_readiness"

STRATEGIC_AUDIT_PHASE_SEQUENCE = (
    "classify",
    "hypotheses",
    "gauntlet",
    "audit",
    "strategy",
    "sqi",
    "monitor",
    "report",
)

TECHNOLOGY_READINESS_PHASE_SEQUENCE = (
    "scope",
    "scientific_inventory",
    "trl_diagnosis",
    "research_industry_alignment",
    "ip_protection_axis",
    "next_level_recommendations",
    "technical_validation_plan",
    "industrial_transfer_plan",
    "readiness_roadmap",
    "executive_summary",
)

TECHNOLOGY_READINESS_PHASE_LABELS = {
    "scope": "Scope",
    "scientific_inventory": "Scientific Inventory",
    "trl_diagnosis": "TRL Diagnosis",
    "research_industry_alignment": "Research-Industry Alignment",
    "ip_protection_axis": "IP Protection Axis",
    "next_level_recommendations": "Next-Level Recommendations",
    "technical_validation_plan": "Technical Validation Plan",
    "industrial_transfer_plan": "Industrial Transfer Plan",
    "readiness_roadmap": "Readiness Roadmap",
    "executive_summary": "Executive Summary",
}

PROJECT_TYPE_ALIASES = {
    "": DEFAULT_PROJECT_TYPE,
    None: DEFAULT_PROJECT_TYPE,
    "default": DEFAULT_PROJECT_TYPE,
    "strategic": DEFAULT_PROJECT_TYPE,
    "strategic_audit": DEFAULT_PROJECT_TYPE,
    "ai_readiness": "ai_readiness",
    "automation_roi": "automation_roi",
    "technology_readiness": TECHNOLOGY_READINESS_PROJECT_TYPE,
    "technology_readiness_transfer": TECHNOLOGY_READINESS_PROJECT_TYPE,
    "technology_readiness_and_transfer": TECHNOLOGY_READINESS_PROJECT_TYPE,
}

WORKFLOW_TEMPLATES = {
    DEFAULT_PROJECT_TYPE: {
        "project_type": DEFAULT_PROJECT_TYPE,
        "template_id": DEFAULT_PROJECT_TYPE,
        "label": "Strategic Audit",
        "description": "Default v4.4 strategic decision audit workflow.",
        "phase_sequence": STRATEGIC_AUDIT_PHASE_SEQUENCE,
        "prompt_dir": "prompts/phases",
    },
    "ai_readiness": {
        "project_type": "ai_readiness",
        "template_id": "ai_readiness",
        "label": "AI Readiness Audit",
        "description": "AI readiness assessment using the default strategic workflow.",
        "phase_sequence": STRATEGIC_AUDIT_PHASE_SEQUENCE,
        "prompt_dir": "prompts/phases",
    },
    "automation_roi": {
        "project_type": "automation_roi",
        "template_id": "automation_roi",
        "label": "Automation ROI Audit",
        "description": "Automation ROI assessment using the default strategic workflow.",
        "phase_sequence": STRATEGIC_AUDIT_PHASE_SEQUENCE,
        "prompt_dir": "prompts/phases",
    },
    TECHNOLOGY_READINESS_PROJECT_TYPE: {
        "project_type": TECHNOLOGY_READINESS_PROJECT_TYPE,
        "template_id": TECHNOLOGY_READINESS_PROJECT_TYPE,
        "label": "Technology Readiness & Transfer Audit",
        "description": (
            "Operator-reviewed technology maturity and transfer-readiness "
            "assessment using TRL diagnosis, evidence gates, IP review axes, "
            "technical validation planning, and industrial transfer planning."
        ),
        "phase_sequence": TECHNOLOGY_READINESS_PHASE_SEQUENCE,
        "phase_labels": TECHNOLOGY_READINESS_PHASE_LABELS,
        "prompt_dir": "prompts/technology_readiness",
    },
}


def normalize_project_type(project_type: str | None) -> str:
    """Normalize a project type and reject unsupported values."""
    if project_type is None:
        key = None
    elif isinstance(project_type, str):
        key = project_type.strip().lower()
    else:
        key = object()
    normalized = PROJECT_TYPE_ALIASES.get(key)
    if normalized not in WORKFLOW_TEMPLATES:
        known = ", ".join(sorted(WORKFLOW_TEMPLATES))
        raise ValueError(f"Unsupported project_type {project_type!r}. Known project types: {known}")
    return normalized


def get_workflow_template(project_type: str | None) -> dict:
    """Return a copy of the workflow template metadata for project_type."""
    normalized = normalize_project_type(project_type)
    return deepcopy(WORKFLOW_TEMPLATES[normalized])


def list_workflow_templates() -> list[dict]:
    """Return registered workflow templates in stable display order."""
    order = (DEFAULT_PROJECT_TYPE, "ai_readiness", "automation_roi", TECHNOLOGY_READINESS_PROJECT_TYPE)
    return [get_workflow_template(project_type) for project_type in order]


def get_workflow_phase_sequence(project_type: str | None) -> tuple[str, ...]:
    """Return the phase sequence for the given project type."""
    return tuple(get_workflow_template(project_type)["phase_sequence"])


def get_downstream_phases(project_type: str | None, changed_phase: str) -> list[str]:
    """Return downstream phases in template order for invalidation."""
    sequence = get_workflow_phase_sequence(project_type)

    if changed_phase not in sequence:
        return []
    idx = sequence.index(changed_phase)
    return list(sequence[idx + 1 :])


def all_editable_phases() -> set[str]:
    """Return all phases that can be patched through operator edit endpoints."""
    editable: set[str] = set()
    for template in WORKFLOW_TEMPLATES.values():
        editable.update(template["phase_sequence"])
    return editable
