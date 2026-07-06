"""Strict contracts for deterministic R1.6B Automation ROI execution."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue, field_validator, model_validator


CalculationStatus = Literal["valid", "not_applicable", "blocked"]
RoiPercentStatus = Literal["computed", "not_applicable", "blocked"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _uuid_text(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a UUID string")
    try:
        return str(UUID(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{field_name} must be a UUID string") from exc


def _nonblank(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value.strip()


class AutomationRoiExecutionRequest(_StrictModel):
    """The complete caller-supplied request; all other data is server-derived."""

    project_id: str
    input_snapshot_id: str
    idempotency_key: str

    @field_validator("project_id", "input_snapshot_id")
    @classmethod
    def _validate_uuid(cls, value: str, info) -> str:
        return _uuid_text(value, field_name=info.field_name)

    @field_validator("idempotency_key")
    @classmethod
    def _validate_key(cls, value: str) -> str:
        return _nonblank(value, field_name="idempotency_key")


class AutomationRoiInputManifestItem(_StrictModel):
    input_role: str
    numeric_value: Decimal
    unit: str
    period: Optional[str] = None
    currency_code: Optional[str] = None
    time_unit: Optional[str] = None
    binding_id: str
    approved_calculation_input_id: str
    candidate_fact_revision_id: str
    approval_decision_id: str

    @field_validator(
        "binding_id",
        "approved_calculation_input_id",
        "candidate_fact_revision_id",
        "approval_decision_id",
    )
    @classmethod
    def _validate_ids(cls, value: str, info) -> str:
        return _uuid_text(value, field_name=info.field_name)

    @field_validator("numeric_value")
    @classmethod
    def _validate_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("numeric_value must be finite")
        return value


class AutomationRoiCalculationResult(_StrictModel):
    id: str
    project_id: str
    input_snapshot_id: str
    consumer_contract: str
    binding_set_id: str
    idempotency_key: str
    operation_digest: str
    requested_by: str
    computed_at: datetime
    formula_identifier: str
    formula_version: str
    formula_fingerprint: str
    assumption_set_version: str
    assumptions_json: dict[str, JsonValue]
    input_manifest_json: dict[str, JsonValue]
    input_digest: str
    provenance_fingerprint: str
    output_units_json: dict[str, JsonValue]
    status: CalculationStatus
    currency_code: Optional[str] = None
    annual_labor_savings: Optional[Decimal] = None
    annual_net_benefit: Optional[Decimal] = None
    first_year_net_benefit: Optional[Decimal] = None
    first_year_roi_percent: Optional[Decimal] = None
    roi_percent_status: RoiPercentStatus
    diagnostics_json: dict[str, JsonValue]

    @field_validator("id", "project_id", "input_snapshot_id")
    @classmethod
    def _validate_ids(cls, value: str, info) -> str:
        return _uuid_text(value, field_name=info.field_name)

    @field_validator(
        "consumer_contract",
        "binding_set_id",
        "idempotency_key",
        "requested_by",
        "formula_identifier",
        "formula_version",
        "assumption_set_version",
    )
    @classmethod
    def _validate_nonblank(cls, value: str, info) -> str:
        return _nonblank(value, field_name=info.field_name)

    @field_validator(
        "operation_digest",
        "formula_fingerprint",
        "input_digest",
        "provenance_fingerprint",
    )
    @classmethod
    def _validate_digest(cls, value: str, info) -> str:
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)
        ):
            raise ValueError(f"{info.field_name} must be lowercase SHA-256 hex")
        return value

    @model_validator(mode="after")
    def _validate_status_shape(self):
        money = (
            self.annual_labor_savings,
            self.annual_net_benefit,
            self.first_year_net_benefit,
        )
        if self.status == "blocked":
            if self.roi_percent_status != "blocked":
                raise ValueError("blocked result requires blocked ROI status")
            if self.currency_code is not None or any(value is not None for value in money):
                raise ValueError("blocked result cannot contain calculated money outputs")
            if self.first_year_roi_percent is not None:
                raise ValueError("blocked result cannot contain ROI percent")
        elif self.status == "not_applicable":
            if self.roi_percent_status != "not_applicable":
                raise ValueError("not_applicable result requires matching ROI status")
            if self.currency_code is None or any(value is None for value in money):
                raise ValueError("not_applicable result requires calculated money outputs")
            if self.first_year_roi_percent is not None:
                raise ValueError("not_applicable result cannot contain ROI percent")
        else:
            if self.roi_percent_status != "computed":
                raise ValueError("valid result requires computed ROI status")
            if (
                self.currency_code is None
                or any(value is None for value in money)
                or self.first_year_roi_percent is None
            ):
                raise ValueError("valid result requires every output")
        return self
