"""Pydantic models for the R1.1 research-evidence sidecar.

The models intentionally mirror only sidecar metadata fields. Canonical source
provenance remains in Slice A: ``source_snapshot``, ``source_blob``,
``candidate_fact_revision``, and ``evidence_retention_event``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceMetadataRevisionCreate(_StrictModel):
    project_id: str
    source_snapshot_id: str
    canonical_source_locator: str = ""
    publisher: str = ""
    author: str = ""
    published_at: Optional[datetime] = None
    retrieved_at: Optional[datetime] = None
    citation_label: str = ""
    declared_quality_tier: str = ""
    declared_quality_rationale: str = ""
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    supersedes_metadata_revision_id: Optional[str] = None
    created_by: str = ""

    @field_validator("published_at", "retrieved_at")
    @classmethod
    def _require_timezone(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class SourceMetadataRevisionRecord(SourceMetadataRevisionCreate):
    id: str
    created_at: datetime


class FactMetadataRevisionCreate(_StrictModel):
    project_id: str
    candidate_fact_revision_id: str
    stable_fact_key: str = ""
    drift_group_key: str = ""
    supersedes_candidate_fact_revision_id: Optional[str] = None
    source_char_range: Optional[str] = None
    excerpt_hash: str = ""
    citation_locator: str = ""
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    supersedes_metadata_revision_id: Optional[str] = None
    created_by: str = ""


class FactMetadataRevisionRecord(FactMetadataRevisionCreate):
    id: str
    created_at: datetime


class ClaimDraftCreate(_StrictModel):
    project_id: str
    claim_text: str
    claim_category: str = ""
    supersedes_claim_id: Optional[str] = None
    created_by: str = ""


class ClaimDraftRecord(ClaimDraftCreate):
    id: str
    created_at: datetime


class EvidenceEventCreate(_StrictModel):
    project_id: str
    entity_type: str
    entity_id: str
    event_type: str
    event_sequence: int
    actor: str = ""
    details_json: dict[str, Any] = Field(default_factory=dict)


class EvidenceEventRecord(EvidenceEventCreate):
    id: str
    occurred_at: datetime
