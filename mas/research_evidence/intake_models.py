"""Strict models for operator-controlled R1.2 research-evidence intake."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _nonblank(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value.strip()


class ResearchEvidenceIntakeCreate(_StrictModel):
    project_id: str
    source_snapshot_id: str
    source_metadata_revision_id: str
    selection_reason: str
    created_by: str

    @field_validator("selection_reason")
    @classmethod
    def _validate_reason(cls, value: str) -> str:
        return _nonblank(value, field_name="selection_reason")

    @field_validator("created_by")
    @classmethod
    def _validate_actor(cls, value: str) -> str:
        return _nonblank(value, field_name="created_by")


class ResearchEvidenceIntakeRecord(ResearchEvidenceIntakeCreate):
    id: str
    intake_method: str
    state: str
    created_at: datetime


class ResearchEvidenceIntakeItemCreate(_StrictModel):
    project_id: str
    research_evidence_intake_id: str
    item_kind: str
    candidate_fact_revision_id: Optional[str] = None
    fact_metadata_revision_id: Optional[str] = None
    claim_draft_id: Optional[str] = None
    created_by: str

    @field_validator("created_by")
    @classmethod
    def _validate_actor(cls, value: str) -> str:
        return _nonblank(value, field_name="created_by")

    @model_validator(mode="after")
    def _validate_target_shape(self):
        fact_shape = (
            self.item_kind == "candidate_fact"
            and self.candidate_fact_revision_id is not None
            and self.fact_metadata_revision_id is not None
            and self.claim_draft_id is None
        )
        claim_shape = (
            self.item_kind == "claim_draft"
            and self.candidate_fact_revision_id is None
            and self.fact_metadata_revision_id is None
            and self.claim_draft_id is not None
        )
        if not (fact_shape or claim_shape):
            raise ValueError(
                "candidate_fact requires fact and fact-metadata IDs only; "
                "claim_draft requires a claim ID only"
            )
        return self


class ResearchEvidenceIntakeItemRecord(ResearchEvidenceIntakeItemCreate):
    id: str
    source_snapshot_id: str
    state: str
    created_at: datetime
