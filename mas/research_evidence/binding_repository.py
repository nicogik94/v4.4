"""PostgreSQL persistence for R1.6 consumer-input evidence bindings.

The repository records one immutable evaluation per consumer input. Canonical
availability and every other evaluated source status are composed into one SQL
statement snapshot; this module does not recreate availability semantics or
invoke any downstream consumer.
"""
from __future__ import annotations

import json
from typing import Optional

from knowledge.evidence_snapshot import repository as evidence_repository
from psycopg import sql

from .binding_models import (
    ResearchEvidenceConsumerInputBindingCreate,
    ResearchEvidenceConsumerInputBindingRecord,
)


class ResearchEvidenceBindingRepositoryError(ValueError):
    """Base error for scoped binding persistence failures."""


class BindingParentNotFound(ResearchEvidenceBindingRepositoryError):
    """A required same-project intake or consumer input does not exist."""


class BindingIntegrityError(ResearchEvidenceBindingRepositoryError):
    """A binding insert violates the immutable R1.6 database contract."""


class BindingRequestConflict(BindingIntegrityError):
    """A request ID already identifies a different immutable binding."""


def get_binding_by_request_id(
    conn,
    *,
    project_id: str,
    consumer_contract: str,
    binding_set_id: str,
    input_key: str,
    request_id: str,
) -> Optional[ResearchEvidenceConsumerInputBindingRecord]:
    row = conn.execute(
        _BINDING_SELECT
        + """
        WHERE project_id = %s
          AND consumer_contract = %s
          AND binding_set_id = %s
          AND input_key = %s
          AND request_id = %s
        """,
        (
            project_id,
            consumer_contract,
            binding_set_id,
            input_key,
            request_id,
        ),
    ).fetchone()
    return None if row is None else _binding_from_row(row)


def get_effective_binding(
    conn,
    *,
    project_id: str,
    consumer_contract: str,
    binding_set_id: str,
    input_key: str,
) -> Optional[ResearchEvidenceConsumerInputBindingRecord]:
    row = conn.execute(
        _BINDING_SELECT
        + """
        WHERE project_id = %s
          AND consumer_contract = %s
          AND binding_set_id = %s
          AND input_key = %s
        ORDER BY binding_sequence DESC
        LIMIT 1
        """,
        (project_id, consumer_contract, binding_set_id, input_key),
    ).fetchone()
    return None if row is None else _binding_from_row(row)


def ensure_retry_matches(
    existing: ResearchEvidenceConsumerInputBindingRecord,
    binding: ResearchEvidenceConsumerInputBindingCreate,
) -> ResearchEvidenceConsumerInputBindingRecord:
    expected = binding.model_dump()
    actual = {
        field: getattr(existing, field)
        for field in ResearchEvidenceConsumerInputBindingCreate.model_fields
    }
    if actual != expected:
        raise BindingRequestConflict(
            "request_id already identifies a different immutable binding evaluation"
        )
    return existing


def insert_binding(
    conn,
    binding: ResearchEvidenceConsumerInputBindingCreate,
) -> ResearchEvidenceConsumerInputBindingRecord:
    existing = get_binding_by_request_id(
        conn,
        project_id=binding.project_id,
        consumer_contract=binding.consumer_contract,
        binding_set_id=binding.binding_set_id,
        input_key=binding.input_key,
        request_id=binding.request_id,
    )
    if existing is not None:
        return ensure_retry_matches(existing, binding)

    availability = evidence_repository.fact_availability_sql(
        sql.Identifier("context", "candidate_fact_revision_id")
    )
    statement = sql.SQL(
        """
        WITH request_input AS MATERIALIZED (
            SELECT
                %s::uuid AS project_id,
                %s::text AS consumer_contract,
                %s::text AS consumer_contract_version,
                %s::text AS binding_set_id,
                %s::text AS input_key,
                %s::text AS request_id,
                %s::uuid AS evidence_intake_item_id,
                %s::uuid AS approved_calculation_input_id,
                %s::text AS observation_identity_version,
                %s::text AS observation_identity_fingerprint,
                %s::uuid AS claim_intake_item_id,
                %s::uuid AS claim_support_assessment_id,
                %s::text AS policy_identifier,
                %s::text AS policy_version,
                %s::jsonb AS policy_parameters_json,
                %s::text AS policy_fingerprint,
                %s::text AS evaluator_version,
                %s::timestamptz AS freshness_as_of,
                %s::text AS consumer_disposition,
                %s::jsonb AS disposition_reasons_json,
                %s::text AS evaluated_by
        ),
        evidence_context AS MATERIALIZED (
            SELECT
                request_input.*,
                snapshot.id AS source_snapshot_id,
                blob.id AS source_blob_id,
                source_metadata.id AS source_metadata_revision_id,
                fact.id AS candidate_fact_revision_id,
                fact_metadata.id AS fact_metadata_revision_id
            FROM request_input
            JOIN research_evidence_intake_item item
              ON item.id = request_input.evidence_intake_item_id
             AND item.project_id = request_input.project_id
             AND item.item_kind = 'candidate_fact'
            JOIN research_evidence_intake intake
              ON intake.id = item.research_evidence_intake_id
             AND intake.project_id = item.project_id
             AND intake.source_snapshot_id = item.source_snapshot_id
            JOIN source_snapshot snapshot
              ON snapshot.id = item.source_snapshot_id
             AND snapshot.project_id = item.project_id
            JOIN source_blob blob
              ON blob.id = snapshot.source_blob_id
             AND blob.project_id = snapshot.project_id
            JOIN research_source_metadata_revision source_metadata
              ON source_metadata.id = intake.source_metadata_revision_id
             AND source_metadata.project_id = intake.project_id
             AND source_metadata.source_snapshot_id =
                 intake.source_snapshot_id
            JOIN candidate_fact_revision fact
              ON fact.id = item.candidate_fact_revision_id
             AND fact.project_id = item.project_id
             AND fact.source_snapshot_id = item.source_snapshot_id
            JOIN research_fact_metadata_revision fact_metadata
              ON fact_metadata.id = item.fact_metadata_revision_id
             AND fact_metadata.project_id = item.project_id
             AND fact_metadata.candidate_fact_revision_id = fact.id
        ),
        evaluated_context AS MATERIALIZED (
            SELECT
                context.*,
                calculation_input.calculation_kind,
                {availability_status} AS availability_status,
                retention.basis_json AS retention_basis_json,
                lineage.is_current AS lineage_is_current,
                lineage.basis_json AS lineage_basis_json,
                review.id AS review_decision_id,
                review.decision_sequence AS review_decision_sequence,
                COALESCE(review.decision_type, 'not_assessed')
                    AS review_status,
                freshness.id AS freshness_assessment_id,
                freshness.assessment_sequence
                    AS freshness_assessment_sequence,
                freshness.fresh_through,
                CASE
                    WHEN freshness.id IS NULL THEN 'unknown'
                    WHEN context.freshness_as_of <= freshness.fresh_through
                        THEN 'fresh'
                    ELSE 'stale'
                END AS freshness_status,
                COALESCE(freshness.drift_status, 'not_assessed')
                    AS drift_status,
                support.locator_resolution,
                support.evidence_linkage,
                support.semantic_relationship
            FROM evidence_context context
            LEFT JOIN approved_calculation_input calculation_input
              ON context.consumer_contract = 'deterministic_calculation'
             AND calculation_input.id =
                 context.approved_calculation_input_id
             AND calculation_input.project_id = context.project_id
             AND calculation_input.input_role = context.input_key
             AND calculation_input.candidate_fact_revision_id =
                 context.candidate_fact_revision_id
            CROSS JOIN LATERAL (
                SELECT COALESCE(
                    jsonb_agg(
                        jsonb_build_object(
                            'event_id', event.id::text,
                            'event_type', event.event_type,
                            'source_blob_id', event.source_blob_id::text,
                            'source_snapshot_id',
                                event.source_snapshot_id::text,
                            'candidate_fact_revision_id',
                                event.candidate_fact_revision_id::text,
                            'created_at', event.created_at
                        )
                        ORDER BY event.created_at, event.id
                    ),
                    '[]'::jsonb
                ) AS basis_json
                FROM evidence_retention_event event
                WHERE event.project_id = context.project_id
                  AND (
                        event.source_blob_id = context.source_blob_id
                     OR event.source_snapshot_id =
                        context.source_snapshot_id
                     OR event.candidate_fact_revision_id =
                        context.candidate_fact_revision_id
                  )
            ) retention
            CROSS JOIN LATERAL (
                SELECT
                    NOT EXISTS (
                        SELECT 1
                        FROM research_source_metadata_revision successor
                        WHERE successor.project_id = context.project_id
                          AND successor.source_snapshot_id =
                              context.source_snapshot_id
                          AND successor.supersedes_metadata_revision_id =
                              context.source_metadata_revision_id
                    )
                    AND NOT EXISTS (
                        SELECT 1
                        FROM research_evidence_event event
                        WHERE event.project_id = context.project_id
                          AND event.entity_type =
                              'source_metadata_revision'
                          AND event.entity_id =
                              context.source_metadata_revision_id
                          AND event.event_type IN (
                              'superseded', 'withdrawn'
                          )
                    )
                    AND NOT EXISTS (
                        SELECT 1
                        FROM research_fact_metadata_revision successor
                        WHERE successor.project_id = context.project_id
                          AND successor.candidate_fact_revision_id =
                              context.candidate_fact_revision_id
                          AND successor.supersedes_metadata_revision_id =
                              context.fact_metadata_revision_id
                    )
                    AND NOT EXISTS (
                        SELECT 1
                        FROM research_fact_metadata_revision replacement
                        WHERE replacement.project_id = context.project_id
                          AND replacement.supersedes_candidate_fact_revision_id =
                              context.candidate_fact_revision_id
                    )
                    AND NOT EXISTS (
                        SELECT 1
                        FROM research_evidence_event event
                        WHERE event.project_id = context.project_id
                          AND event.entity_type = 'fact_metadata_revision'
                          AND event.entity_id =
                              context.fact_metadata_revision_id
                          AND event.event_type IN (
                              'superseded', 'withdrawn'
                          )
                    ) AS is_current,
                    COALESCE(
                        (
                            SELECT jsonb_agg(
                                jsonb_build_object(
                                    'kind', basis.kind,
                                    'identity', basis.identity,
                                    'relation', basis.relation
                                )
                                ORDER BY basis.kind, basis.identity,
                                         basis.relation
                            )
                            FROM (
                                SELECT
                                    'source_metadata_successor'::text
                                        AS kind,
                                    successor.id::text AS identity,
                                    'supersedes_source_metadata'::text
                                        AS relation
                                FROM research_source_metadata_revision
                                     successor
                                WHERE successor.project_id =
                                      context.project_id
                                  AND successor.source_snapshot_id =
                                      context.source_snapshot_id
                                  AND
                                      successor.supersedes_metadata_revision_id =
                                      context.source_metadata_revision_id
                                UNION ALL
                                SELECT
                                    'fact_metadata_successor',
                                    successor.id::text,
                                    'supersedes_fact_metadata'
                                FROM research_fact_metadata_revision successor
                                WHERE successor.project_id =
                                      context.project_id
                                  AND successor.candidate_fact_revision_id =
                                      context.candidate_fact_revision_id
                                  AND
                                      successor.supersedes_metadata_revision_id =
                                      context.fact_metadata_revision_id
                                UNION ALL
                                SELECT
                                    'candidate_fact_successor',
                                    successor.id::text,
                                    'supersedes_candidate_fact'
                                FROM research_fact_metadata_revision successor
                                WHERE successor.project_id =
                                      context.project_id
                                  AND
                                      successor.supersedes_candidate_fact_revision_id =
                                      context.candidate_fact_revision_id
                                UNION ALL
                                SELECT
                                    'lineage_event',
                                    event.id::text,
                                    event.event_type
                                FROM research_evidence_event event
                                WHERE event.project_id = context.project_id
                                  AND (
                                        (
                                            event.entity_type =
                                                'source_metadata_revision'
                                            AND event.entity_id =
                                                context.source_metadata_revision_id
                                        )
                                     OR (
                                            event.entity_type =
                                                'fact_metadata_revision'
                                            AND event.entity_id =
                                                context.fact_metadata_revision_id
                                        )
                                  )
                            ) basis
                        ),
                        '[]'::jsonb
                    ) AS basis_json
            ) lineage
            LEFT JOIN LATERAL (
                SELECT id, decision_sequence, decision_type
                FROM research_evidence_intake_item_review_decision
                WHERE project_id = context.project_id
                  AND research_evidence_intake_item_id =
                      context.evidence_intake_item_id
                ORDER BY decision_sequence DESC
                LIMIT 1
            ) review ON true
            LEFT JOIN LATERAL (
                SELECT id, assessment_sequence, fresh_through, drift_status
                FROM research_evidence_intake_item_freshness_assessment
                WHERE project_id = context.project_id
                  AND research_evidence_intake_item_id =
                      context.evidence_intake_item_id
                ORDER BY assessment_sequence DESC
                LIMIT 1
            ) freshness ON true
            LEFT JOIN LATERAL (
                SELECT id, locator_resolution, evidence_linkage,
                       semantic_relationship
                FROM research_evidence_claim_support_assessment
                WHERE id = context.claim_support_assessment_id
                  AND project_id = context.project_id
                  AND claim_intake_item_id =
                      context.claim_intake_item_id
                  AND evidence_intake_item_id =
                      context.evidence_intake_item_id
            ) support ON context.claim_intake_item_id IS NOT NULL
            WHERE (
                    context.consumer_contract <>
                        'deterministic_calculation'
                    OR calculation_input.id IS NOT NULL
                  )
              AND (
                    context.claim_intake_item_id IS NULL
                    OR support.id IS NOT NULL
                  )
        )
        INSERT INTO research_evidence_consumer_input_binding
            (project_id, consumer_contract, consumer_contract_version,
             binding_set_id, input_key, request_id,
             evidence_intake_item_id, approved_calculation_input_id,
             calculation_kind, observation_identity_version,
             observation_identity_fingerprint, claim_intake_item_id,
             claim_support_assessment_id, policy_identifier,
             policy_version, policy_parameters_json, policy_fingerprint,
             evaluator_version, freshness_as_of, consumer_disposition,
             disposition_reasons_json, evaluated_by, source_snapshot_id,
             source_blob_id, source_metadata_revision_id,
             candidate_fact_revision_id, fact_metadata_revision_id,
             availability_status, retention_basis_json,
             lineage_is_current, lineage_basis_json, review_decision_id,
             review_decision_sequence, review_status,
             freshness_assessment_id, freshness_assessment_sequence,
             fresh_through, freshness_status, drift_status,
             locator_resolution, evidence_linkage, semantic_relationship)
        SELECT
            project_id, consumer_contract, consumer_contract_version,
            binding_set_id, input_key, request_id,
            evidence_intake_item_id, approved_calculation_input_id,
            calculation_kind, observation_identity_version,
            observation_identity_fingerprint, claim_intake_item_id,
            claim_support_assessment_id, policy_identifier,
            policy_version, policy_parameters_json, policy_fingerprint,
            evaluator_version, freshness_as_of, consumer_disposition,
            disposition_reasons_json, evaluated_by, source_snapshot_id,
            source_blob_id, source_metadata_revision_id,
            candidate_fact_revision_id, fact_metadata_revision_id,
            availability_status, retention_basis_json,
            lineage_is_current, lineage_basis_json, review_decision_id,
            review_decision_sequence, review_status,
            freshness_assessment_id, freshness_assessment_sequence,
            fresh_through, freshness_status, drift_status,
            locator_resolution, evidence_linkage, semantic_relationship
        FROM evaluated_context
        RETURNING
            id::text, project_id::text, consumer_contract,
            consumer_contract_version, binding_set_id, input_key,
            request_id, evidence_intake_item_id::text,
            approved_calculation_input_id::text, calculation_kind,
            observation_identity_version,
            observation_identity_fingerprint,
            claim_intake_item_id::text,
            claim_support_assessment_id::text, policy_identifier,
            policy_version, policy_parameters_json, policy_fingerprint,
            evaluator_version, freshness_as_of, consumer_disposition,
            disposition_reasons_json, evaluated_by,
            source_snapshot_id::text, source_blob_id::text,
            source_metadata_revision_id::text,
            candidate_fact_revision_id::text,
            fact_metadata_revision_id::text, availability_status,
            retention_basis_json, lineage_is_current, lineage_basis_json,
            review_decision_id::text, review_decision_sequence,
            review_status, freshness_assessment_id::text,
            freshness_assessment_sequence, fresh_through,
            freshness_status, drift_status, locator_resolution,
            evidence_linkage, semantic_relationship, binding_sequence,
            supersedes_binding_id::text, evaluated_at
        """
    ).format(availability_status=availability.expression)
    params = (
        binding.project_id,
        binding.consumer_contract,
        binding.consumer_contract_version,
        binding.binding_set_id,
        binding.input_key,
        binding.request_id,
        binding.evidence_intake_item_id,
        binding.approved_calculation_input_id,
        binding.observation_identity_version,
        binding.observation_identity_fingerprint,
        binding.claim_intake_item_id,
        binding.claim_support_assessment_id,
        binding.policy_identifier,
        binding.policy_version,
        _json_object(binding.policy_parameters_json),
        binding.policy_fingerprint,
        binding.evaluator_version,
        binding.freshness_as_of,
        binding.consumer_disposition,
        _json_array(binding.disposition_reasons),
        binding.evaluated_by,
        *availability.params,
    )
    conn.execute("SAVEPOINT research_evidence_binding_insert")
    try:
        row = conn.execute(statement, params).fetchone()
        if row is None:
            if binding.consumer_contract == "deterministic_calculation":
                raise BindingParentNotFound(
                    "approved calculation input does not match project, role, and fact"
                )
            if binding.claim_intake_item_id is not None:
                raise BindingParentNotFound(
                    "claim-support assessment does not match project and intake pair"
                )
            raise BindingParentNotFound(
                "candidate-fact intake item not found for project"
            )
    except Exception as exc:
        conn.execute("ROLLBACK TO SAVEPOINT research_evidence_binding_insert")
        conn.execute("RELEASE SAVEPOINT research_evidence_binding_insert")
        if _constraint_name(exc) == "uq_recib_scope_request":
            existing = get_binding_by_request_id(
                conn,
                project_id=binding.project_id,
                consumer_contract=binding.consumer_contract,
                binding_set_id=binding.binding_set_id,
                input_key=binding.input_key,
                request_id=binding.request_id,
            )
            if existing is not None:
                return ensure_retry_matches(existing, binding)
        if _sqlstate(exc).startswith("23"):
            raise BindingIntegrityError(
                "binding evaluation violates the immutable database contract"
            ) from exc
        raise
    else:
        conn.execute("RELEASE SAVEPOINT research_evidence_binding_insert")
    return _binding_from_row(row)


_BINDING_SELECT = """
SELECT
    id::text, project_id::text, consumer_contract,
    consumer_contract_version, binding_set_id, input_key, request_id,
    evidence_intake_item_id::text, approved_calculation_input_id::text,
    calculation_kind, observation_identity_version,
    observation_identity_fingerprint, claim_intake_item_id::text,
    claim_support_assessment_id::text, policy_identifier, policy_version,
    policy_parameters_json, policy_fingerprint, evaluator_version,
    freshness_as_of, consumer_disposition, disposition_reasons_json,
    evaluated_by, source_snapshot_id::text, source_blob_id::text,
    source_metadata_revision_id::text, candidate_fact_revision_id::text,
    fact_metadata_revision_id::text, availability_status,
    retention_basis_json, lineage_is_current, lineage_basis_json,
    review_decision_id::text, review_decision_sequence, review_status,
    freshness_assessment_id::text, freshness_assessment_sequence,
    fresh_through, freshness_status, drift_status, locator_resolution,
    evidence_linkage, semantic_relationship, binding_sequence,
    supersedes_binding_id::text, evaluated_at
FROM research_evidence_consumer_input_binding
"""


def _json_object(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _json_array(value) -> str:
    return json.dumps(list(value), sort_keys=True, separators=(",", ":"))


def _sqlstate(exc: Exception) -> str:
    value = getattr(exc, "sqlstate", None)
    if value:
        return str(value)
    diag = getattr(exc, "diag", None)
    return str(getattr(diag, "sqlstate", "") or "")


def _constraint_name(exc: Exception) -> str:
    diag = getattr(exc, "diag", None)
    return str(getattr(diag, "constraint_name", "") or "")


def _binding_from_row(row) -> ResearchEvidenceConsumerInputBindingRecord:
    policy_parameters = row[16]
    disposition_reasons = row[21]
    retention_basis = row[29]
    lineage_basis = row[31]
    for index, value in (
        (16, policy_parameters),
        (21, disposition_reasons),
        (29, retention_basis),
        (31, lineage_basis),
    ):
        if isinstance(value, str):
            parsed = json.loads(value)
            if index == 16:
                policy_parameters = parsed
            elif index == 21:
                disposition_reasons = parsed
            elif index == 29:
                retention_basis = parsed
            else:
                lineage_basis = parsed
    return ResearchEvidenceConsumerInputBindingRecord(
        id=row[0],
        project_id=row[1],
        consumer_contract=row[2],
        consumer_contract_version=row[3],
        binding_set_id=row[4],
        input_key=row[5],
        request_id=row[6],
        evidence_intake_item_id=row[7],
        approved_calculation_input_id=row[8],
        calculation_kind=row[9],
        observation_identity_version=row[10],
        observation_identity_fingerprint=row[11],
        claim_intake_item_id=row[12],
        claim_support_assessment_id=row[13],
        policy_identifier=row[14],
        policy_version=row[15],
        policy_parameters_json=policy_parameters,
        policy_fingerprint=row[17],
        evaluator_version=row[18],
        freshness_as_of=row[19],
        consumer_disposition=row[20],
        disposition_reasons=tuple(disposition_reasons),
        evaluated_by=row[22],
        source_snapshot_id=row[23],
        source_blob_id=row[24],
        source_metadata_revision_id=row[25],
        candidate_fact_revision_id=row[26],
        fact_metadata_revision_id=row[27],
        availability_status=row[28],
        retention_basis=tuple(retention_basis),
        lineage_is_current=row[30],
        lineage_basis=tuple(lineage_basis),
        review_decision_id=row[32],
        review_decision_sequence=row[33],
        review_status=row[34],
        freshness_assessment_id=row[35],
        freshness_assessment_sequence=row[36],
        fresh_through=row[37],
        freshness_status=row[38],
        drift_status=row[39],
        locator_resolution=row[40],
        evidence_linkage=row[41],
        semantic_relationship=row[42],
        binding_sequence=row[43],
        supersedes_binding_id=row[44],
        evaluated_at=row[45],
    )
