"""Strict models for pair-scoped R1.5 claim-support assessments.

The three assessment dimensions are independent operator declarations.  They do
not establish semantic truth, citation readiness, approval, or downstream-use
eligibility.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


LocatorResolution = Literal[
    "not_assessed",
    "resolvable",
    "unresolvable",
    "indeterminate",
]
EvidenceLinkage = Literal[
    "not_assessed",
    "linked",
    "not_linked",
    "indeterminate",
]
SemanticRelationship = Literal[
    "not_assessed",
    "support",
    "contradiction",
    "qualification",
    "insufficient_evidence",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _nonblank(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value.strip()


class ResearchEvidenceClaimSupportAssessmentCreate(_StrictModel):
    project_id: str
    claim_intake_item_id: str
    evidence_intake_item_id: str
    request_id: str
    locator_resolution: LocatorResolution
    locator_rationale: str
    evidence_linkage: EvidenceLinkage
    evidence_linkage_rationale: str
    semantic_relationship: SemanticRelationship
    semantic_relationship_rationale: str
    assessed_by: str

    @field_validator(
        "request_id",
        "locator_rationale",
        "evidence_linkage_rationale",
        "semantic_relationship_rationale",
        "assessed_by",
    )
    @classmethod
    def _validate_nonblank(cls, value: str, info) -> str:
        return _nonblank(value, field_name=info.field_name)

    @model_validator(mode="after")
    def _validate_distinct_endpoints(self):
        if self.claim_intake_item_id == self.evidence_intake_item_id:
            raise ValueError("claim and evidence intake items must differ")
        return self


class ResearchEvidenceClaimSupportAssessmentRecord(
    ResearchEvidenceClaimSupportAssessmentCreate
):
    id: str
    assessment_sequence: int
    supersedes_assessment_id: Optional[str] = None
    claim_draft_id: str
    claim_source_snapshot_id: str
    claim_source_blob_id: str
    claim_source_metadata_revision_id: str
    evidence_source_snapshot_id: str
    evidence_source_blob_id: str
    evidence_source_metadata_revision_id: str
    candidate_fact_revision_id: str
    fact_metadata_revision_id: str
    assessed_at: datetime
