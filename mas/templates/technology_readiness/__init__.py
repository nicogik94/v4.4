"""Technology Readiness & Transfer template metadata.

The live workflow registry for this repository is workflow_templates.py. This
package exists only as a safe import surface for callers that inspect
templates/technology_readiness; it intentionally does not import
templates.registry, templates.base, WorkflowTemplate, or PhaseSpec.
"""
from workflow_templates import (
    TECHNOLOGY_READINESS_PHASE_LABELS,
    TECHNOLOGY_READINESS_PHASE_SEQUENCE,
    TECHNOLOGY_READINESS_PROJECT_TYPE,
    get_workflow_template,
)


TEMPLATE = get_workflow_template(TECHNOLOGY_READINESS_PROJECT_TYPE)

__all__ = [
    "TEMPLATE",
    "TECHNOLOGY_READINESS_PHASE_LABELS",
    "TECHNOLOGY_READINESS_PHASE_SEQUENCE",
    "TECHNOLOGY_READINESS_PROJECT_TYPE",
]
