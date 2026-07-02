"""PostgreSQL-only persistence for the R1.7 provenance foundation."""
from __future__ import annotations

import json
from typing import Optional

from .scenario_input_evaluation_models import (
    ScenarioInputEvaluationInputRecord,
    ScenarioInputEvaluationRecord,
    ScenarioInputEvaluationRequest,
    ScenarioInputManifestItemRecord,
    ScenarioInputManifestRecord,
    ScenarioInputManifestRegistration,
)


class ScenarioInputEvaluationRepositoryError(ValueError):
    """Base error for R1.7 persistence failures."""


class ScenarioInputManifestRequestConflict(ScenarioInputEvaluationRepositoryError):
    """A manifest request identity already names another immutable payload."""


class ScenarioInputEvaluationRequestConflict(ScenarioInputEvaluationRepositoryError):
    """An evaluation request identity already names another immutable payload."""


class ScenarioInputEvaluationIntegrityError(ScenarioInputEvaluationRepositoryError):
    """The database rejected a malformed or incoherent structural request."""


def register_manifest(
    conn, registration: ScenarioInputManifestRegistration
) -> ScenarioInputManifestRecord:
    try:
        row = conn.execute(
            """
            SELECT id::text
            FROM research_evidence_register_scenario_input_manifest(
                %s::uuid, %s::text, %s::text, %s::text, %s::jsonb, %s::text
            )
            """,
            (
                registration.project_id,
                registration.request_id,
                registration.namespace,
                registration.version,
                _json(list(registration.input_keys)),
                registration.registered_by,
            ),
        ).fetchone()
    except Exception as exc:
        _raise_scoped(exc, manifest=True)
    if row is None:
        raise ScenarioInputEvaluationIntegrityError(
            "database did not return the registered manifest"
        )
    return get_manifest(conn, manifest_id=row[0])


def get_manifest(
    conn, *, manifest_id: str
) -> Optional[ScenarioInputManifestRecord]:
    row = conn.execute(
        _MANIFEST_SELECT
        + " WHERE manifest.id = %s::uuid"
        + " GROUP BY manifest.id",
        (manifest_id,),
    ).fetchone()
    return None if row is None else _manifest_from_row(row)


def get_manifest_by_request_id(
    conn, *, project_id: str, request_id: str
) -> Optional[ScenarioInputManifestRecord]:
    row = conn.execute(
        _MANIFEST_SELECT
        + " WHERE manifest.project_id = %s::uuid"
        + " AND manifest.registration_request_id = %s"
        + " GROUP BY manifest.id",
        (project_id, request_id),
    ).fetchone()
    return None if row is None else _manifest_from_row(row)


def create_evaluation(
    conn, request: ScenarioInputEvaluationRequest
) -> ScenarioInputEvaluationRecord:
    try:
        row = conn.execute(
            """
            SELECT id::text
            FROM research_evidence_create_scenario_input_evaluation(%s::jsonb)
            """,
            (_json(request.canonical_database_payload()),),
        ).fetchone()
    except Exception as exc:
        _raise_scoped(exc, manifest=False)
    if row is None:
        raise ScenarioInputEvaluationIntegrityError(
            "database did not return the scenario-input evaluation"
        )
    record = get_evaluation(conn, evaluation_id=row[0])
    if record is None:
        raise ScenarioInputEvaluationIntegrityError(
            "database evaluation disappeared before readback"
        )
    return record


def get_evaluation(
    conn, *, evaluation_id: str
) -> Optional[ScenarioInputEvaluationRecord]:
    row = conn.execute(
        _EVALUATION_SELECT
        + " WHERE evaluation.id = %s::uuid"
        + " GROUP BY evaluation.id",
        (evaluation_id,),
    ).fetchone()
    return None if row is None else _evaluation_from_row(row)


def get_evaluation_by_request_id(
    conn, *, project_id: str, request_id: str
) -> Optional[ScenarioInputEvaluationRecord]:
    row = conn.execute(
        _EVALUATION_SELECT
        + " WHERE evaluation.project_id = %s::uuid"
        + " AND evaluation.request_id = %s"
        + " GROUP BY evaluation.id",
        (project_id, request_id),
    ).fetchone()
    return None if row is None else _evaluation_from_row(row)


_MANIFEST_SELECT = """
SELECT
    manifest.id::text, manifest.project_id::text,
    manifest.registration_request_id, manifest.manifest_namespace,
    manifest.manifest_version, manifest.canonical_input_keys_json,
    manifest.input_cardinality, manifest.structural_descriptor,
    manifest.manifest_fingerprint, manifest.registered_by,
    manifest.registered_at,
    COALESCE(
        jsonb_agg(
            jsonb_build_object(
                'manifest_id', item.manifest_id::text,
                'project_id', item.project_id::text,
                'input_key', item.input_key,
                'item_ordinal', item.item_ordinal,
                'linked_at', item.linked_at
            ) ORDER BY item.item_ordinal
        ) FILTER (WHERE item.manifest_id IS NOT NULL),
        '[]'::jsonb
    )
FROM research_evidence_scenario_input_manifest manifest
LEFT JOIN research_evidence_scenario_input_manifest_item item
  ON item.manifest_id = manifest.id
 AND item.project_id = manifest.project_id
"""

_EVALUATION_SELECT = """
SELECT
    evaluation.id::text, evaluation.project_id::text,
    evaluation.request_id, evaluation.request_payload_json,
    evaluation.request_fingerprint, evaluation.manifest_id::text,
    evaluation.manifest_version, evaluation.manifest_cardinality,
    evaluation.manifest_fingerprint, evaluation.descriptor_namespace,
    evaluation.descriptor_version, evaluation.descriptor_json,
    evaluation.descriptor_fingerprint,
    evaluation.descriptor_declared_by,
    evaluation.consumer_contract_version, evaluation.binding_set_id,
    evaluation.binding_policy_identifier,
    evaluation.binding_policy_version,
    evaluation.binding_policy_fingerprint,
    evaluation.binding_evaluator_version, evaluation.freshness_as_of,
    evaluation.evaluation_policy_identifier,
    evaluation.evaluation_policy_version,
    evaluation.evaluation_policy_parameters_json,
    evaluation.evaluation_policy_fingerprint,
    evaluation.evaluator_version, evaluation.evaluation_status,
    evaluation.reason_codes_json, evaluation.evaluation_sequence,
    evaluation.predecessor_evaluation_id::text, evaluation.evaluated_at,
    COALESCE(
        jsonb_agg(
            jsonb_build_object(
                'evaluation_id', child.evaluation_id::text,
                'project_id', child.project_id::text,
                'manifest_id', child.manifest_id::text,
                'input_key', child.input_key,
                'selected_binding_id', child.selected_binding_id::text,
                'binding_sequence', child.binding_sequence,
                'selected_binding_has_successor',
                    child.selected_binding_has_successor,
                'availability_status', child.availability_status,
                'lineage_is_current', child.lineage_is_current,
                'review_status', child.review_status,
                'freshness_status', child.freshness_status,
                'drift_status', child.drift_status,
                'binding_disposition', child.binding_disposition,
                'dependence_declaration', child.dependence_declaration,
                'dependence_rationale', child.dependence_rationale,
                'input_status', child.input_status,
                'reason_codes', child.reason_codes_json,
                'linked_at', child.linked_at
            ) ORDER BY child.input_key COLLATE "C", child.selected_binding_id
        ) FILTER (WHERE child.evaluation_id IS NOT NULL),
        '[]'::jsonb
    )
FROM research_evidence_scenario_input_evaluation evaluation
LEFT JOIN research_evidence_scenario_input_evaluation_input child
  ON child.evaluation_id = evaluation.id
 AND child.project_id = evaluation.project_id
 AND child.manifest_id = evaluation.manifest_id
"""


def _manifest_from_row(row) -> ScenarioInputManifestRecord:
    keys = _loaded(row[5])
    items = _loaded(row[11])
    return ScenarioInputManifestRecord(
        id=row[0],
        project_id=row[1],
        request_id=row[2],
        namespace=row[3],
        version=row[4],
        input_keys=tuple(keys),
        input_cardinality=row[6],
        structural_descriptor=row[7],
        manifest_fingerprint=row[8],
        registered_by=row[9],
        registered_at=row[10],
        items=tuple(ScenarioInputManifestItemRecord(**item) for item in items),
    )


def _evaluation_from_row(row) -> ScenarioInputEvaluationRecord:
    values = [_loaded(value) for value in row]
    inputs = tuple(
        ScenarioInputEvaluationInputRecord(
            **{
                **child,
                "reason_codes": tuple(child.pop("reason_codes")),
            }
        )
        for child in values[31]
    )
    return ScenarioInputEvaluationRecord(
        id=values[0],
        project_id=values[1],
        request_id=values[2],
        request_payload=values[3],
        request_fingerprint=values[4],
        manifest_id=values[5],
        manifest_version=values[6],
        manifest_cardinality=values[7],
        manifest_fingerprint=values[8],
        descriptor_namespace=values[9],
        descriptor_version=values[10],
        descriptor=values[11],
        descriptor_fingerprint=values[12],
        descriptor_declared_by=values[13],
        consumer_contract_version=values[14],
        binding_set_id=values[15],
        binding_policy_identifier=values[16],
        binding_policy_version=values[17],
        binding_policy_fingerprint=values[18],
        binding_evaluator_version=values[19],
        freshness_as_of=values[20],
        evaluation_policy_identifier=values[21],
        evaluation_policy_version=values[22],
        evaluation_policy_parameters=values[23],
        evaluation_policy_fingerprint=values[24],
        evaluator_version=values[25],
        evaluation_status=values[26],
        reason_codes=tuple(values[27]),
        evaluation_sequence=values[28],
        predecessor_evaluation_id=values[29],
        evaluated_at=values[30],
        inputs=inputs,
    )


def _loaded(value):
    return json.loads(value) if isinstance(value, str) and value[:1] in "[{" else value


def _json(value) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _raise_scoped(exc: Exception, *, manifest: bool) -> None:
    message = str(exc).lower()
    if "immutable manifest request conflict" in message:
        raise ScenarioInputManifestRequestConflict(
            "request_id already identifies a different immutable manifest"
        ) from exc
    if "immutable evaluation request conflict" in message:
        raise ScenarioInputEvaluationRequestConflict(
            "request_id already identifies a different immutable evaluation"
        ) from exc
    if _sqlstate(exc).startswith("22") or _sqlstate(exc).startswith("23"):
        noun = "manifest" if manifest else "scenario-input evaluation"
        raise ScenarioInputEvaluationIntegrityError(
            f"{noun} violates the immutable database contract"
        ) from exc
    raise exc


def _sqlstate(exc: Exception) -> str:
    value = getattr(exc, "sqlstate", None)
    if value:
        return str(value)
    diag = getattr(exc, "diag", None)
    return str(getattr(diag, "sqlstate", "") or "")
