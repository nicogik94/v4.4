"""Low-level PostgreSQL reads and inserts for R1.4 freshness assessments.

The database derives linked evidence, content-change evidence, ordering, and
assessment time. This module never evaluates availability, retention, lineage,
review approval, withdrawal, or any downstream-use contract.
"""
from __future__ import annotations

import json
from typing import Optional

from .freshness_models import (
    ResearchEvidenceIntakeItemFreshnessAssessmentCreate,
    ResearchEvidenceIntakeItemFreshnessAssessmentRecord,
)


class ResearchEvidenceFreshnessRepositoryError(ValueError):
    """Base error for scoped freshness persistence failures."""


class FreshnessParentNotFound(ResearchEvidenceFreshnessRepositoryError):
    """The project-scoped intake item does not exist."""


class FreshnessIntegrityError(ResearchEvidenceFreshnessRepositoryError):
    """An assessment insert violates the immutable R1.4 contract."""


class FreshnessRequestConflict(FreshnessIntegrityError):
    """A request ID already identifies a different immutable assessment."""


def get_item_kind(
    conn,
    *,
    project_id: str,
    research_evidence_intake_item_id: str,
) -> str:
    row = conn.execute(
        """
        SELECT item_kind
        FROM research_evidence_intake_item
        WHERE id = %s AND project_id = %s
        """,
        (research_evidence_intake_item_id, project_id),
    ).fetchone()
    if row is None:
        raise FreshnessParentNotFound(
            "research evidence intake item not found for project"
        )
    return str(row[0])


def get_assessment_by_request_id(
    conn,
    *,
    project_id: str,
    research_evidence_intake_item_id: str,
    request_id: str,
) -> Optional[ResearchEvidenceIntakeItemFreshnessAssessmentRecord]:
    row = conn.execute(
        _ASSESSMENT_SELECT
        + """
        WHERE project_id = %s
          AND research_evidence_intake_item_id = %s
          AND request_id = %s
        """,
        (project_id, research_evidence_intake_item_id, request_id),
    ).fetchone()
    return None if row is None else _assessment_from_row(row)


def get_effective_assessment(
    conn,
    *,
    project_id: str,
    research_evidence_intake_item_id: str,
) -> Optional[ResearchEvidenceIntakeItemFreshnessAssessmentRecord]:
    row = conn.execute(
        _ASSESSMENT_SELECT
        + """
        WHERE project_id = %s
          AND research_evidence_intake_item_id = %s
        ORDER BY assessment_sequence DESC
        LIMIT 1
        """,
        (project_id, research_evidence_intake_item_id),
    ).fetchone()
    return None if row is None else _assessment_from_row(row)


def ensure_retry_matches(
    existing: ResearchEvidenceIntakeItemFreshnessAssessmentRecord,
    assessment: ResearchEvidenceIntakeItemFreshnessAssessmentCreate,
) -> ResearchEvidenceIntakeItemFreshnessAssessmentRecord:
    expected = (
        assessment.project_id,
        assessment.research_evidence_intake_item_id,
        assessment.request_id,
        assessment.policy_identifier,
        assessment.policy_version,
        assessment.policy_parameters_json,
        assessment.policy_fingerprint,
        assessment.evaluator_version,
        assessment.basis_timestamp,
        assessment.fresh_through,
        assessment.comparison_research_evidence_intake_item_id,
        assessment.drift_status,
        assessment.drift_reason,
        assessment.assessed_by,
    )
    actual = (
        existing.project_id,
        existing.research_evidence_intake_item_id,
        existing.request_id,
        existing.policy_identifier,
        existing.policy_version,
        existing.policy_parameters_json,
        existing.policy_fingerprint,
        existing.evaluator_version,
        existing.basis_timestamp,
        existing.fresh_through,
        existing.comparison_research_evidence_intake_item_id,
        existing.drift_status,
        existing.drift_reason,
        existing.assessed_by,
    )
    if actual != expected:
        raise FreshnessRequestConflict(
            "request_id already identifies a different immutable freshness assessment"
        )
    return existing


def insert_assessment(
    conn,
    assessment: ResearchEvidenceIntakeItemFreshnessAssessmentCreate,
) -> ResearchEvidenceIntakeItemFreshnessAssessmentRecord:
    existing = get_assessment_by_request_id(
        conn,
        project_id=assessment.project_id,
        research_evidence_intake_item_id=(
            assessment.research_evidence_intake_item_id
        ),
        request_id=assessment.request_id,
    )
    if existing is not None:
        return ensure_retry_matches(existing, assessment)

    conn.execute("SAVEPOINT research_evidence_freshness_insert")
    try:
        row = conn.execute(
            """
            INSERT INTO research_evidence_intake_item_freshness_assessment
                (project_id, research_evidence_intake_item_id, request_id,
                 policy_identifier, policy_version, policy_parameters_json,
                 policy_fingerprint, evaluator_version, basis_timestamp,
                 fresh_through, comparison_research_evidence_intake_item_id,
                 drift_status, drift_reason, assessed_by)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s,
                    %s, %s, %s)
            RETURNING
                id::text, project_id::text,
                research_evidence_intake_item_id::text, request_id,
                policy_identifier, policy_version, policy_parameters_json,
                policy_fingerprint, evaluator_version, basis_timestamp,
                fresh_through,
                comparison_research_evidence_intake_item_id::text,
                drift_status, drift_reason, assessed_by, assessment_sequence,
                supersedes_assessment_id::text, source_snapshot_id::text,
                source_blob_id::text, candidate_fact_revision_id::text,
                fact_metadata_revision_id::text, linked_hash_algorithm,
                linked_content_hash, comparison_source_snapshot_id::text,
                comparison_source_blob_id::text,
                comparison_candidate_fact_revision_id::text,
                comparison_fact_metadata_revision_id::text,
                comparison_hash_algorithm, comparison_content_hash,
                content_change_detected, assessed_at
            """,
            (
                assessment.project_id,
                assessment.research_evidence_intake_item_id,
                assessment.request_id,
                assessment.policy_identifier,
                assessment.policy_version,
                _json_object(assessment.policy_parameters_json),
                assessment.policy_fingerprint,
                assessment.evaluator_version,
                assessment.basis_timestamp,
                assessment.fresh_through,
                assessment.comparison_research_evidence_intake_item_id,
                assessment.drift_status,
                assessment.drift_reason,
                assessment.assessed_by,
            ),
        ).fetchone()
    except Exception as exc:
        conn.execute("ROLLBACK TO SAVEPOINT research_evidence_freshness_insert")
        conn.execute("RELEASE SAVEPOINT research_evidence_freshness_insert")
        if _constraint_name(exc) == "uq_reifa_item_request":
            existing = get_assessment_by_request_id(
                conn,
                project_id=assessment.project_id,
                research_evidence_intake_item_id=(
                    assessment.research_evidence_intake_item_id
                ),
                request_id=assessment.request_id,
            )
            if existing is not None:
                return ensure_retry_matches(existing, assessment)
        if _sqlstate(exc).startswith("23"):
            raise FreshnessIntegrityError(
                "freshness assessment violates the immutable database contract"
            ) from exc
        raise
    else:
        conn.execute("RELEASE SAVEPOINT research_evidence_freshness_insert")
    return _assessment_from_row(row)


_ASSESSMENT_SELECT = """
SELECT
    id::text, project_id::text, research_evidence_intake_item_id::text,
    request_id, policy_identifier, policy_version, policy_parameters_json,
    policy_fingerprint, evaluator_version, basis_timestamp, fresh_through,
    comparison_research_evidence_intake_item_id::text, drift_status,
    drift_reason, assessed_by, assessment_sequence,
    supersedes_assessment_id::text, source_snapshot_id::text,
    source_blob_id::text, candidate_fact_revision_id::text,
    fact_metadata_revision_id::text, linked_hash_algorithm,
    linked_content_hash, comparison_source_snapshot_id::text,
    comparison_source_blob_id::text,
    comparison_candidate_fact_revision_id::text,
    comparison_fact_metadata_revision_id::text, comparison_hash_algorithm,
    comparison_content_hash, content_change_detected, assessed_at
FROM research_evidence_intake_item_freshness_assessment
"""


def _json_object(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _sqlstate(exc: Exception) -> str:
    value = getattr(exc, "sqlstate", None)
    if value:
        return str(value)
    diag = getattr(exc, "diag", None)
    return str(getattr(diag, "sqlstate", "") or "")


def _constraint_name(exc: Exception) -> str:
    diag = getattr(exc, "diag", None)
    return str(getattr(diag, "constraint_name", "") or "")


def _assessment_from_row(
    row,
) -> ResearchEvidenceIntakeItemFreshnessAssessmentRecord:
    parameters = row[6]
    if isinstance(parameters, str):
        parameters = json.loads(parameters)
    return ResearchEvidenceIntakeItemFreshnessAssessmentRecord(
        id=row[0],
        project_id=row[1],
        research_evidence_intake_item_id=row[2],
        request_id=row[3],
        policy_identifier=row[4],
        policy_version=row[5],
        policy_parameters_json=parameters,
        policy_fingerprint=row[7],
        evaluator_version=row[8],
        basis_timestamp=row[9],
        fresh_through=row[10],
        comparison_research_evidence_intake_item_id=row[11],
        drift_status=row[12],
        drift_reason=row[13],
        assessed_by=row[14],
        assessment_sequence=row[15],
        supersedes_assessment_id=row[16],
        source_snapshot_id=row[17],
        source_blob_id=row[18],
        candidate_fact_revision_id=row[19],
        fact_metadata_revision_id=row[20],
        linked_hash_algorithm=row[21],
        linked_content_hash=row[22],
        comparison_source_snapshot_id=row[23],
        comparison_source_blob_id=row[24],
        comparison_candidate_fact_revision_id=row[25],
        comparison_fact_metadata_revision_id=row[26],
        comparison_hash_algorithm=row[27],
        comparison_content_hash=row[28],
        content_change_detected=row[29],
        assessed_at=row[30],
    )
