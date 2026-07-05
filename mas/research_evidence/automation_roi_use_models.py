"""Strict models for immutable R1.6A Automation ROI input snapshots."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, JsonValue, field_validator, model_validator

from .automation_roi_use_policy import REQUIRED_ROLES
from .freshness_models import require_aware_datetime


CompletenessStatus = Literal["complete", "incomplete"]
PolicyEvaluationStatus = Literal[
    "satisfies",
    "qualified",
    "does_not_satisfy",
    "indeterminate",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _nonblank(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value.strip()


class AutomationRoiInputSnapshotCreate(_StrictModel):
    project_id: str
    binding_set_id: str
    binding_record_ids: tuple[str, ...]
    request_id: str
    freshness_as_of: datetime
    evaluated_by: str

    @field_validator("project_id", "binding_set_id", "request_id", "evaluated_by")
    @classmethod
    def _validate_nonblank(cls, value: str, info) -> str:
        return _nonblank(value, field_name=info.field_name)

    @field_validator("binding_record_ids")
    @classmethod
    def _validate_binding_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(
            _nonblank(item, field_name="binding_record_ids") for item in value
        )
        if len(cleaned) != len(REQUIRED_ROLES):
            raise ValueError("exactly six binding_record_ids are required")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("binding_record_ids must be distinct")
        return cleaned

    @field_validator("freshness_as_of")
    @classmethod
    def _validate_as_of(cls, value: datetime) -> datetime:
        return require_aware_datetime(value, field_name="freshness_as_of")


class AutomationRoiInputSnapshotBindingRecord(_StrictModel):
    id: str
    snapshot_id: str
    project_id: str
    consumer_contract: str
    binding_set_id: str
    input_role: str
    binding_record_id: str
    linked_at: datetime

    @field_validator("input_role")
    @classmethod
    def _validate_role(cls, value: str) -> str:
        value = _nonblank(value, field_name="input_role")
        if value not in REQUIRED_ROLES:
            raise ValueError("input_role is not a canonical Automation ROI role")
        return value


class AutomationRoiInputSnapshotRecord(_StrictModel):
    id: str
    project_id: str
    consumer_contract: str
    consumer_contract_version: str
    binding_set_id: str
    snapshot_sequence: int
    request_id: str
    policy_identifier: str
    policy_version: str
    policy_parameters_json: dict[str, JsonValue]
    policy_fingerprint: str
    evaluator_version: str
    freshness_as_of: datetime
    completeness_status: CompletenessStatus
    policy_evaluation_status: PolicyEvaluationStatus
    evaluation_reasons: tuple[str, ...]
    evaluated_by: str
    supersedes_snapshot_id: Optional[str] = None
    evaluated_at: datetime
    bindings: tuple[AutomationRoiInputSnapshotBindingRecord, ...]

    @model_validator(mode="after")
    def _validate_complete_shape(self):
        if self.completeness_status != "complete":
            raise ValueError("persisted snapshots must be complete")
        roles = tuple(binding.input_role for binding in self.bindings)
        if len(roles) != len(REQUIRED_ROLES) or set(roles) != set(REQUIRED_ROLES):
            raise ValueError("snapshot bindings must contain exactly the six roles")
        if not self.evaluation_reasons:
            raise ValueError("evaluation_reasons must not be empty")
        return self
