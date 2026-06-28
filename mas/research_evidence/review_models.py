"""Strict models for controlled R1.3 intake-item review decisions."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator


DecisionType = Literal["approved", "rejected", "needs_revision", "withdrawn"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _nonblank(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value.strip()


class ResearchEvidenceIntakeItemReviewDecisionCreate(_StrictModel):
    project_id: str
    research_evidence_intake_item_id: str
    decision_type: DecisionType
    decision_reason: str
    decided_by: str
    request_id: str

    @field_validator("decision_reason", "decided_by", "request_id")
    @classmethod
    def _validate_nonblank(cls, value: str, info) -> str:
        return _nonblank(value, field_name=info.field_name)


class ResearchEvidenceIntakeItemReviewDecisionRecord(
    ResearchEvidenceIntakeItemReviewDecisionCreate
):
    id: str
    decision_sequence: int
    supersedes_decision_id: Optional[str] = None
    recorded_at: datetime
