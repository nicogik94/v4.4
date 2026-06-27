"""Research Evidence Metadata Sidecar (R1.1) and controlled intake (R1.2).

This package attaches operator-declared metadata and draft-only intake groupings
to existing Slice A provenance. It creates no parallel blob, snapshot, fact,
retention, calculation, report, scenario, prompt, or retrieval system.
"""
from __future__ import annotations

import config

from .intake_models import (
    ResearchEvidenceIntakeCreate,
    ResearchEvidenceIntakeItemCreate,
    ResearchEvidenceIntakeItemRecord,
    ResearchEvidenceIntakeRecord,
)
from .intake_service import (
    ResearchEvidenceIntakeDisabled,
    ResearchEvidenceIntakeTransactionError,
    ResearchEvidenceSnapshotUnavailable,
    create_intake,
    create_intake_item,
)

SCHEMA_MIGRATION = "v51_research_evidence_sidecar_foundation.sql"
INTAKE_SCHEMA_MIGRATION = "v53_research_evidence_intake_foundation.sql"


def is_enabled() -> bool:
    return config.research_evidence_enabled()


__all__ = [
    "INTAKE_SCHEMA_MIGRATION",
    "SCHEMA_MIGRATION",
    "ResearchEvidenceIntakeCreate",
    "ResearchEvidenceIntakeDisabled",
    "ResearchEvidenceIntakeItemCreate",
    "ResearchEvidenceIntakeItemRecord",
    "ResearchEvidenceIntakeRecord",
    "ResearchEvidenceIntakeTransactionError",
    "ResearchEvidenceSnapshotUnavailable",
    "create_intake",
    "create_intake_item",
    "is_enabled",
]
