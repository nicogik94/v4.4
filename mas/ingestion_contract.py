"""Versioned project ingestion contract normalization."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from workflow_templates import DEFAULT_PROJECT_TYPE, normalize_project_type


LEGACY_CONTRACT_VERSION = "legacy.v1"
CASE_CONTRACT_VERSION = "case.v1"
DEFAULT_INGESTION_SOURCE = "operator"

_MISSING = object()
_LEGACY_PROJECT_FIELDS = {"name", "brief", "data"}


class IngestionContractError(ValueError):
    """Raised when a project creation payload cannot be normalized."""


@dataclass(frozen=True)
class NormalizedIngestionContract:
    name: str
    brief: str
    data: str
    contract_version: str = LEGACY_CONTRACT_VERSION
    source: str = DEFAULT_INGESTION_SOURCE
    external_case_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    project_type: str = DEFAULT_PROJECT_TYPE
    risk_classification: str | None = None
    risk_rationale: str = ""
    risk_set_by: str = "operator"


def normalize_project_ingestion(payload: Mapping[str, Any]) -> NormalizedIngestionContract:
    """Normalize legacy and case.v1 project creation payloads."""
    if not isinstance(payload, Mapping) and hasattr(payload, "model_dump"):
        payload = payload.model_dump()
    if not isinstance(payload, Mapping):
        raise IngestionContractError("Project creation payload must be a JSON object.")

    has_case_envelope = "case" in payload
    has_versioned_root = "contract_version" in payload
    risk_payload = payload

    if has_case_envelope:
        conflicting_fields = sorted(_LEGACY_PROJECT_FIELDS.intersection(payload.keys()))
        if conflicting_fields or has_versioned_root:
            raise IngestionContractError(
                "Project creation payload cannot mix legacy fields with a case envelope."
            )
        case_payload = payload.get("case")
        if not isinstance(case_payload, Mapping):
            raise IngestionContractError("case must be a JSON object.")
        return _normalize_case_v1(case_payload, risk_payload=risk_payload)

    if has_versioned_root:
        return _normalize_case_v1(payload, risk_payload=risk_payload)

    return _normalize_legacy(payload)


def _normalize_legacy(payload: Mapping[str, Any]) -> NormalizedIngestionContract:
    name = _string_field(payload, "name", default="New Project")
    brief = _string_field(payload, "brief", required=True)
    data = _string_field(payload, "data", default="")
    risk = _risk_options(payload)
    project_type = _project_type_option(payload)
    return NormalizedIngestionContract(
        name=name,
        brief=brief,
        data=data,
        contract_version=LEGACY_CONTRACT_VERSION,
        source=DEFAULT_INGESTION_SOURCE,
        external_case_id="",
        metadata={},
        project_type=project_type,
        **risk,
    )


def _normalize_case_v1(
    payload: Mapping[str, Any],
    *,
    risk_payload: Mapping[str, Any],
) -> NormalizedIngestionContract:
    contract_version = _string_field(payload, "contract_version", required=True)
    if contract_version != CASE_CONTRACT_VERSION:
        raise IngestionContractError(f"Unsupported ingestion contract_version: {contract_version}")

    metadata = payload.get("metadata", {})
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise IngestionContractError("metadata must be a JSON object.")

    risk = _risk_options(risk_payload)
    project_type = _project_type_option(payload, fallback_payload=risk_payload)
    return NormalizedIngestionContract(
        name=_string_field(payload, "name", required=True),
        brief=_string_field(payload, "brief", required=True),
        data=_string_field(payload, "data", default=""),
        contract_version=contract_version,
        source=_string_field(payload, "source", default=DEFAULT_INGESTION_SOURCE),
        external_case_id=_string_field(payload, "external_case_id", default=""),
        metadata=dict(metadata),
        project_type=project_type,
        **risk,
    )


def _risk_options(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "risk_classification": _string_field(payload, "risk_classification", default=None),
        "risk_rationale": _string_field(payload, "risk_rationale", default=""),
        "risk_set_by": _string_field(payload, "risk_set_by", default="operator"),
    }


def _project_type_option(
    payload: Mapping[str, Any],
    *,
    fallback_payload: Mapping[str, Any] | None = None,
) -> str:
    value = payload.get("project_type", None)
    if value is None and fallback_payload is not None:
        value = fallback_payload.get("project_type", None)
    try:
        return normalize_project_type(value)
    except ValueError as exc:
        raise IngestionContractError(str(exc)) from exc


def _string_field(
    payload: Mapping[str, Any],
    field_name: str,
    *,
    required: bool = False,
    default: str | None = "",
) -> str | None:
    value = payload.get(field_name, _MISSING)
    if value is _MISSING:
        if required:
            raise IngestionContractError(f"{field_name} is required.")
        return default
    if value is None:
        if required:
            raise IngestionContractError(f"{field_name} is required.")
        return default
    if not isinstance(value, str):
        raise IngestionContractError(f"{field_name} must be a string.")
    return value
