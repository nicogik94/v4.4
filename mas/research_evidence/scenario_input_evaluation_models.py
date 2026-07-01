"""Strict provenance-only models for R1.7 scenario-input evaluations.

These records describe structural completeness and policy outcomes only.  They
do not represent hypotheses, observations, truth, independence, Bayesian
inputs, posterior updates, scenario runs, or authorization to execute one.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, JsonValue, field_validator, model_validator

from .freshness_models import require_aware_datetime


DependenceDeclaration = Literal[
    "not_assessed",
    "declared_dependent",
    "declared_independent_not_verified",
]
ScenarioInputEvaluationStatus = Literal[
    "does_not_satisfy",
    "indeterminate",
    "qualified",
    "satisfies",
]

EVALUATION_POLICY_IDENTIFIER = "scenario_input.evidence_evaluation"
EVALUATION_POLICY_VERSION = "1"
EVALUATOR_VERSION = "scenario_input.evidence_evaluation.evaluator.v1"
STATUS_PRECEDENCE = (
    "does_not_satisfy",
    "indeterminate",
    "qualified",
    "satisfies",
)
REASON_ORDER = (
    "evidence_unavailable",
    "lineage_not_current",
    "review_rejected",
    "review_needs_revision",
    "review_withdrawn",
    "material_drift",
    "selected_binding_successor",
    "binding_does_not_meet_contract",
    "review_not_assessed",
    "freshness_unknown",
    "drift_not_assessed",
    "drift_indeterminate",
    "binding_indeterminate",
    "dependence_not_assessed",
    "freshness_stale",
    "binding_qualified",
    "dependence_declared_dependent",
    "dependence_declared_independent_not_verified",
)
EVALUATION_POLICY_PARAMETERS = {
    "dependence_outcomes": {
        "declared_dependent": "qualified",
        "declared_independent_not_verified": "qualified",
        "not_assessed": "indeterminate",
    },
    "reason_order": list(REASON_ORDER),
    "satisfies_nonempty_manifest_reachable": False,
    "status_precedence": list(STATUS_PRECEDENCE),
}
EVALUATION_POLICY_CANONICAL_JSON = json.dumps(
    EVALUATION_POLICY_PARAMETERS,
    ensure_ascii=False,
    separators=(",", ":"),
    sort_keys=True,
)
EVALUATION_POLICY_FINGERPRINT = hashlib.sha256(
    EVALUATION_POLICY_CANONICAL_JSON.encode("utf-8")
).hexdigest()


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _nonblank(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value.strip()


def _reject_nonfinite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("JSON descriptor must not contain non-finite numbers")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON descriptor object keys must be strings")
            _reject_nonfinite(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_nonfinite(child)


def canonical_manifest_descriptor(
    namespace: str,
    version: str,
    input_keys: tuple[str, ...],
) -> str:
    """Reproduce the database's UTF-8 length-prefixed structural descriptor."""
    namespace = _nonblank(namespace, field_name="namespace")
    version = _nonblank(version, field_name="version")
    keys = tuple(sorted((_nonblank(key, field_name="input_keys") for key in input_keys)))
    if len(set(keys)) != len(keys):
        raise ValueError("input_keys must be unique")
    lines = [
        "scenario-input-manifest-v1",
        f"namespace={len(namespace.encode('utf-8'))}:{namespace}",
        f"version={len(version.encode('utf-8'))}:{version}",
        f"cardinality={len(keys)}",
    ]
    lines.extend(f"key={len(key.encode('utf-8'))}:{key}" for key in keys)
    return "\n".join(lines) + "\n"


def manifest_fingerprint(descriptor: str) -> str:
    return hashlib.sha256(descriptor.encode("utf-8")).hexdigest()


class ScenarioInputManifestRegistration(_StrictModel):
    project_id: str
    request_id: str
    namespace: str
    version: str
    input_keys: tuple[str, ...]
    registered_by: str

    @field_validator("request_id", "namespace", "version", "registered_by")
    @classmethod
    def _validate_nonblank(cls, value: str, info) -> str:
        return _nonblank(value, field_name=info.field_name)

    @field_validator("input_keys")
    @classmethod
    def _validate_keys(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        keys = tuple(_nonblank(key, field_name="input_keys") for key in value)
        if len(keys) != len(set(keys)):
            raise ValueError("input_keys must be unique")
        return keys


class ScenarioInputManifestItemRecord(_StrictModel):
    manifest_id: str
    project_id: str
    input_key: str
    item_ordinal: int
    linked_at: datetime


class ScenarioInputManifestRecord(ScenarioInputManifestRegistration):
    id: str
    input_keys: tuple[str, ...]
    input_cardinality: int
    structural_descriptor: str
    manifest_fingerprint: str
    registered_at: datetime
    items: tuple[ScenarioInputManifestItemRecord, ...] = ()


class OpaqueHypothesisDescriptor(_StrictModel):
    """Caller-declared, explicitly non-canonical and non-authoritative JSON."""

    namespace: str
    descriptor_version: str
    descriptor: JsonValue
    declared_by: str

    @field_validator("namespace", "descriptor_version", "declared_by")
    @classmethod
    def _validate_nonblank(cls, value: str, info) -> str:
        return _nonblank(value, field_name=info.field_name)

    @field_validator("descriptor", mode="before")
    @classmethod
    def _validate_descriptor(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("JSON descriptor must not be null")
        _reject_nonfinite(value)
        return value


class ScenarioInputBindingSelection(_StrictModel):
    binding_id: str
    dependence_declaration: DependenceDeclaration
    rationale: str

    @field_validator("binding_id", "rationale")
    @classmethod
    def _validate_nonblank(cls, value: str, info) -> str:
        return _nonblank(value, field_name=info.field_name)


class ScenarioInputEvaluationRequest(_StrictModel):
    project_id: str
    request_id: str
    manifest_id: str
    descriptor: OpaqueHypothesisDescriptor
    selected_bindings: tuple[ScenarioInputBindingSelection, ...]
    freshness_as_of: datetime

    @field_validator("request_id", "manifest_id")
    @classmethod
    def _validate_nonblank(cls, value: str, info) -> str:
        return _nonblank(value, field_name=info.field_name)

    @field_validator("freshness_as_of")
    @classmethod
    def _validate_freshness_as_of(cls, value: datetime) -> datetime:
        return require_aware_datetime(value, field_name="freshness_as_of")

    @model_validator(mode="after")
    def _validate_unique_binding_ids(self):
        binding_ids = [selection.binding_id for selection in self.selected_bindings]
        if len(binding_ids) != len(set(binding_ids)):
            raise ValueError("selected binding UUIDs must be unique")
        return self

    def canonical_database_payload(self) -> dict[str, JsonValue]:
        """Return only caller-permitted fields; PostgreSQL canonicalizes them."""
        return {
            "project_id": self.project_id,
            "request_id": self.request_id,
            "manifest_id": self.manifest_id,
            "descriptor": {
                "namespace": self.descriptor.namespace,
                "descriptor_version": self.descriptor.descriptor_version,
                "descriptor": self.descriptor.descriptor,
                "declared_by": self.descriptor.declared_by,
            },
            "selected_bindings": [
                selection.model_dump() for selection in self.selected_bindings
            ],
            "freshness_as_of": self.freshness_as_of.isoformat(),
        }


class ScenarioInputEvaluationInputRecord(_StrictModel):
    evaluation_id: str
    project_id: str
    manifest_id: str
    input_key: str
    selected_binding_id: str
    binding_sequence: int
    selected_binding_has_successor: bool
    availability_status: bool
    lineage_is_current: bool
    review_status: str
    freshness_status: str
    drift_status: str
    binding_disposition: str
    dependence_declaration: DependenceDeclaration
    dependence_rationale: str
    input_status: ScenarioInputEvaluationStatus
    reason_codes: tuple[str, ...]
    linked_at: datetime


class ScenarioInputEvaluationRecord(_StrictModel):
    id: str
    project_id: str
    request_id: str
    request_payload: dict[str, JsonValue]
    request_fingerprint: str
    manifest_id: str
    manifest_version: str
    manifest_cardinality: int
    manifest_fingerprint: str
    descriptor_namespace: str
    descriptor_version: str
    descriptor: JsonValue
    descriptor_fingerprint: str
    descriptor_declared_by: str
    consumer_contract_version: str
    binding_set_id: str
    binding_policy_identifier: str
    binding_policy_version: str
    binding_policy_fingerprint: str
    binding_evaluator_version: str
    freshness_as_of: datetime
    evaluation_policy_identifier: str
    evaluation_policy_version: str
    evaluation_policy_parameters: dict[str, JsonValue]
    evaluation_policy_fingerprint: str
    evaluator_version: str
    evaluation_status: ScenarioInputEvaluationStatus
    reason_codes: tuple[str, ...]
    evaluation_sequence: int
    predecessor_evaluation_id: Optional[str]
    evaluated_at: datetime
    inputs: tuple[ScenarioInputEvaluationInputRecord, ...] = ()
