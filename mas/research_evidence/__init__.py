"""Research Evidence foundations for metadata, intake, review, and freshness.

This package attaches operator-declared metadata and draft-only intake groupings
to existing Slice A provenance and records item-scoped assessments. It creates
no parallel blob, snapshot, fact, retention, calculation, report, scenario,
prompt, retrieval, or downstream-use system.
"""
from __future__ import annotations

import config

from .freshness_models import (
    DriftStatus,
    FreshnessStatus,
    ResearchEvidenceIntakeItemFreshnessAssessmentCreate,
    ResearchEvidenceIntakeItemFreshnessAssessmentRecord,
)
from .freshness_service import (
    ResearchEvidenceFreshnessDisabled,
    ResearchEvidenceFreshnessNotApplicable,
    ResearchEvidenceFreshnessTransactionError,
    item_freshness_status_as_of,
    record_item_freshness_assessment,
)
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
from .review_models import (
    ResearchEvidenceIntakeItemReviewDecisionCreate,
    ResearchEvidenceIntakeItemReviewDecisionRecord,
)
from .review_service import (
    ResearchEvidenceReviewDisabled,
    ResearchEvidenceReviewTransactionError,
    ResearchEvidenceReviewUnavailable,
    item_is_eligible_for_future_use,
    record_item_review_decision,
)

SCHEMA_MIGRATION = "v51_research_evidence_sidecar_foundation.sql"
INTAKE_SCHEMA_MIGRATION = "v53_research_evidence_intake_foundation.sql"
REVIEW_SCHEMA_MIGRATION = "v54_research_evidence_review_foundation.sql"
FRESHNESS_SCHEMA_MIGRATION = "v55_research_evidence_freshness_foundation.sql"


def is_enabled() -> bool:
    return config.research_evidence_enabled()


__all__ = [
    "DriftStatus",
    "FRESHNESS_SCHEMA_MIGRATION",
    "FreshnessStatus",
    "INTAKE_SCHEMA_MIGRATION",
    "REVIEW_SCHEMA_MIGRATION",
    "SCHEMA_MIGRATION",
    "ResearchEvidenceIntakeCreate",
    "ResearchEvidenceFreshnessDisabled",
    "ResearchEvidenceFreshnessNotApplicable",
    "ResearchEvidenceFreshnessTransactionError",
    "ResearchEvidenceIntakeItemFreshnessAssessmentCreate",
    "ResearchEvidenceIntakeItemFreshnessAssessmentRecord",
    "ResearchEvidenceIntakeDisabled",
    "ResearchEvidenceIntakeItemCreate",
    "ResearchEvidenceIntakeItemRecord",
    "ResearchEvidenceIntakeRecord",
    "ResearchEvidenceIntakeItemReviewDecisionCreate",
    "ResearchEvidenceIntakeItemReviewDecisionRecord",
    "ResearchEvidenceIntakeTransactionError",
    "ResearchEvidenceSnapshotUnavailable",
    "ResearchEvidenceReviewDisabled",
    "ResearchEvidenceReviewTransactionError",
    "ResearchEvidenceReviewUnavailable",
    "create_intake",
    "create_intake_item",
    "item_freshness_status_as_of",
    "item_is_eligible_for_future_use",
    "is_enabled",
    "record_item_review_decision",
    "record_item_freshness_assessment",
]
