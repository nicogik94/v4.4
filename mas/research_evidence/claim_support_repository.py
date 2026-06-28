"""PostgreSQL persistence and separate read inputs for R1.5 claim support.

This module writes only the pair-scoped assessment ledger.  Availability,
review, freshness, lineage, locator resolution, evidence linkage, and semantic
relationship remain separate contracts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from knowledge.evidence_snapshot import repository as evidence_repo

from .claim_support_models import (
    ResearchEvidenceClaimSupportAssessmentCreate,
    ResearchEvidenceClaimSupportAssessmentRecord,
)


class ResearchEvidenceClaimSupportRepositoryError(ValueError):
    """Base error for scoped claim-support persistence failures."""


class ClaimSupportParentNotFound(ResearchEvidenceClaimSupportRepositoryError):
    """A same-project claim or candidate-fact intake endpoint does not exist."""


class ClaimSupportIntegrityError(ResearchEvidenceClaimSupportRepositoryError):
    """An insert violates the immutable R1.5 database contract."""


class ClaimSupportRequestConflict(ClaimSupportIntegrityError):
    """A request ID already identifies a different immutable assessment."""


@dataclass(frozen=True)
class ClaimEndpointContext:
    project_id: str
    item_id: str
    claim_draft_id: str
    source_snapshot_id: str
    source_blob_id: str
    source_metadata_revision_id: str


@dataclass(frozen=True)
class EvidenceEndpointContext:
    project_id: str
    item_id: str
    source_snapshot_id: str
    source_blob_id: str
    source_metadata_revision_id: str
    candidate_fact_revision_id: str
    fact_metadata_revision_id: str


def get_claim_endpoint_context(
    conn,
    *,
    project_id: str,
    claim_intake_item_id: str,
) -> ClaimEndpointContext:
    row = conn.execute(
        """
        SELECT item.project_id::text, item.id::text, claim.id::text,
               snapshot.id::text, blob.id::text, source_metadata.id::text
        FROM research_evidence_intake_item item
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
         AND source_metadata.source_snapshot_id = intake.source_snapshot_id
        JOIN research_claim_draft claim
          ON claim.id = item.claim_draft_id
         AND claim.project_id = item.project_id
        WHERE item.id = %s
          AND item.project_id = %s
          AND item.item_kind = 'claim_draft'
        """,
        (claim_intake_item_id, project_id),
    ).fetchone()
    if row is None:
        raise ClaimSupportParentNotFound(
            "claim-draft intake item not found for project"
        )
    return ClaimEndpointContext(*row)


def get_evidence_endpoint_context(
    conn,
    *,
    project_id: str,
    evidence_intake_item_id: str,
) -> EvidenceEndpointContext:
    row = conn.execute(
        """
        SELECT item.project_id::text, item.id::text, snapshot.id::text,
               blob.id::text, source_metadata.id::text, fact.id::text,
               fact_metadata.id::text
        FROM research_evidence_intake_item item
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
         AND source_metadata.source_snapshot_id = intake.source_snapshot_id
        JOIN candidate_fact_revision fact
          ON fact.id = item.candidate_fact_revision_id
         AND fact.project_id = item.project_id
         AND fact.source_snapshot_id = item.source_snapshot_id
        JOIN research_fact_metadata_revision fact_metadata
          ON fact_metadata.id = item.fact_metadata_revision_id
         AND fact_metadata.project_id = item.project_id
         AND fact_metadata.candidate_fact_revision_id = fact.id
        WHERE item.id = %s
          AND item.project_id = %s
          AND item.item_kind = 'candidate_fact'
        """,
        (evidence_intake_item_id, project_id),
    ).fetchone()
    if row is None:
        raise ClaimSupportParentNotFound(
            "candidate-fact intake item not found for project"
        )
    return EvidenceEndpointContext(*row)


def require_pair_context(
    conn,
    *,
    project_id: str,
    claim_intake_item_id: str,
    evidence_intake_item_id: str,
) -> tuple[ClaimEndpointContext, EvidenceEndpointContext]:
    claim = get_claim_endpoint_context(
        conn,
        project_id=project_id,
        claim_intake_item_id=claim_intake_item_id,
    )
    evidence = get_evidence_endpoint_context(
        conn,
        project_id=project_id,
        evidence_intake_item_id=evidence_intake_item_id,
    )
    return claim, evidence


def claim_endpoint_is_available(
    conn,
    *,
    project_id: str,
    claim_intake_item_id: str,
) -> bool:
    context = get_claim_endpoint_context(
        conn,
        project_id=project_id,
        claim_intake_item_id=claim_intake_item_id,
    )
    return evidence_repo.snapshot_available(conn, context.source_snapshot_id)


def evidence_endpoint_is_available(
    conn,
    *,
    project_id: str,
    evidence_intake_item_id: str,
) -> bool:
    context = get_evidence_endpoint_context(
        conn,
        project_id=project_id,
        evidence_intake_item_id=evidence_intake_item_id,
    )
    return evidence_repo.fact_available(conn, context.candidate_fact_revision_id)


def claim_endpoint_lineage_is_current(
    conn,
    *,
    project_id: str,
    claim_intake_item_id: str,
) -> bool:
    context = get_claim_endpoint_context(
        conn,
        project_id=project_id,
        claim_intake_item_id=claim_intake_item_id,
    )
    row = conn.execute(
        """
        SELECT
            NOT EXISTS (
                SELECT 1
                FROM research_source_metadata_revision successor
                WHERE successor.project_id = source_metadata.project_id
                  AND successor.source_snapshot_id =
                      source_metadata.source_snapshot_id
                  AND successor.supersedes_metadata_revision_id =
                      source_metadata.id
            )
            AND NOT EXISTS (
                SELECT 1
                FROM research_evidence_event event
                WHERE event.project_id = source_metadata.project_id
                  AND event.entity_type = 'source_metadata_revision'
                  AND event.entity_id = source_metadata.id
                  AND event.event_type IN ('superseded', 'withdrawn')
            )
            AND NOT EXISTS (
                SELECT 1
                FROM research_claim_draft successor
                WHERE successor.project_id = claim.project_id
                  AND successor.supersedes_claim_id = claim.id
            )
            AND NOT EXISTS (
                SELECT 1
                FROM research_evidence_event event
                WHERE event.project_id = claim.project_id
                  AND event.entity_type = 'claim_draft'
                  AND event.entity_id = claim.id
                  AND event.event_type IN ('superseded', 'withdrawn')
            )
        FROM research_source_metadata_revision source_metadata
        JOIN research_claim_draft claim
          ON claim.id = %s AND claim.project_id = source_metadata.project_id
        WHERE source_metadata.id = %s
          AND source_metadata.project_id = %s
        """,
        (
            context.claim_draft_id,
            context.source_metadata_revision_id,
            context.project_id,
        ),
    ).fetchone()
    return bool(row and row[0])


def evidence_endpoint_lineage_is_current(
    conn,
    *,
    project_id: str,
    evidence_intake_item_id: str,
) -> bool:
    context = get_evidence_endpoint_context(
        conn,
        project_id=project_id,
        evidence_intake_item_id=evidence_intake_item_id,
    )
    row = conn.execute(
        """
        SELECT
            NOT EXISTS (
                SELECT 1
                FROM research_source_metadata_revision successor
                WHERE successor.project_id = source_metadata.project_id
                  AND successor.source_snapshot_id =
                      source_metadata.source_snapshot_id
                  AND successor.supersedes_metadata_revision_id =
                      source_metadata.id
            )
            AND NOT EXISTS (
                SELECT 1
                FROM research_evidence_event event
                WHERE event.project_id = source_metadata.project_id
                  AND event.entity_type = 'source_metadata_revision'
                  AND event.entity_id = source_metadata.id
                  AND event.event_type IN ('superseded', 'withdrawn')
            )
            AND NOT EXISTS (
                SELECT 1
                FROM research_fact_metadata_revision successor
                WHERE successor.project_id = fact_metadata.project_id
                  AND successor.candidate_fact_revision_id =
                      fact_metadata.candidate_fact_revision_id
                  AND successor.supersedes_metadata_revision_id =
                      fact_metadata.id
            )
            AND NOT EXISTS (
                SELECT 1
                FROM research_fact_metadata_revision replacement
                WHERE replacement.project_id = fact.project_id
                  AND replacement.supersedes_candidate_fact_revision_id =
                      fact.id
            )
            AND NOT EXISTS (
                SELECT 1
                FROM research_evidence_event event
                WHERE event.project_id = fact_metadata.project_id
                  AND event.entity_type = 'fact_metadata_revision'
                  AND event.entity_id = fact_metadata.id
                  AND event.event_type IN ('superseded', 'withdrawn')
            )
        FROM research_source_metadata_revision source_metadata
        JOIN candidate_fact_revision fact
          ON fact.id = %s AND fact.project_id = source_metadata.project_id
        JOIN research_fact_metadata_revision fact_metadata
          ON fact_metadata.id = %s
         AND fact_metadata.project_id = fact.project_id
         AND fact_metadata.candidate_fact_revision_id = fact.id
        WHERE source_metadata.id = %s
          AND source_metadata.project_id = %s
        """,
        (
            context.candidate_fact_revision_id,
            context.fact_metadata_revision_id,
            context.source_metadata_revision_id,
            context.project_id,
        ),
    ).fetchone()
    return bool(row and row[0])


def get_assessment_by_request_id(
    conn,
    *,
    project_id: str,
    claim_intake_item_id: str,
    evidence_intake_item_id: str,
    request_id: str,
) -> Optional[ResearchEvidenceClaimSupportAssessmentRecord]:
    row = conn.execute(
        _ASSESSMENT_SELECT
        + """
        WHERE project_id = %s
          AND claim_intake_item_id = %s
          AND evidence_intake_item_id = %s
          AND request_id = %s
        """,
        (
            project_id,
            claim_intake_item_id,
            evidence_intake_item_id,
            request_id,
        ),
    ).fetchone()
    return None if row is None else _assessment_from_row(row)


def get_effective_assessment(
    conn,
    *,
    project_id: str,
    claim_intake_item_id: str,
    evidence_intake_item_id: str,
) -> Optional[ResearchEvidenceClaimSupportAssessmentRecord]:
    row = conn.execute(
        _ASSESSMENT_SELECT
        + """
        WHERE project_id = %s
          AND claim_intake_item_id = %s
          AND evidence_intake_item_id = %s
        ORDER BY assessment_sequence DESC
        LIMIT 1
        """,
        (project_id, claim_intake_item_id, evidence_intake_item_id),
    ).fetchone()
    return None if row is None else _assessment_from_row(row)


def list_effective_assessments_for_claim(
    conn,
    *,
    project_id: str,
    claim_intake_item_id: str,
) -> list[ResearchEvidenceClaimSupportAssessmentRecord]:
    rows = conn.execute(
        """
        SELECT DISTINCT ON (evidence_intake_item_id)
               id::text, project_id::text, claim_intake_item_id::text,
               evidence_intake_item_id::text, request_id, locator_resolution,
               locator_rationale, evidence_linkage, evidence_linkage_rationale,
               semantic_relationship, semantic_relationship_rationale,
               assessed_by, assessment_sequence, supersedes_assessment_id::text,
               claim_draft_id::text, claim_source_snapshot_id::text,
               claim_source_blob_id::text,
               claim_source_metadata_revision_id::text,
               evidence_source_snapshot_id::text,
               evidence_source_blob_id::text,
               evidence_source_metadata_revision_id::text,
               candidate_fact_revision_id::text,
               fact_metadata_revision_id::text, assessed_at
        FROM research_evidence_claim_support_assessment
        WHERE project_id = %s AND claim_intake_item_id = %s
        ORDER BY evidence_intake_item_id, assessment_sequence DESC
        """,
        (project_id, claim_intake_item_id),
    ).fetchall()
    return [_assessment_from_row(row) for row in rows]


def ensure_retry_matches(
    existing: ResearchEvidenceClaimSupportAssessmentRecord,
    assessment: ResearchEvidenceClaimSupportAssessmentCreate,
) -> ResearchEvidenceClaimSupportAssessmentRecord:
    expected = (
        assessment.project_id,
        assessment.claim_intake_item_id,
        assessment.evidence_intake_item_id,
        assessment.request_id,
        assessment.locator_resolution,
        assessment.locator_rationale,
        assessment.evidence_linkage,
        assessment.evidence_linkage_rationale,
        assessment.semantic_relationship,
        assessment.semantic_relationship_rationale,
        assessment.assessed_by,
    )
    actual = (
        existing.project_id,
        existing.claim_intake_item_id,
        existing.evidence_intake_item_id,
        existing.request_id,
        existing.locator_resolution,
        existing.locator_rationale,
        existing.evidence_linkage,
        existing.evidence_linkage_rationale,
        existing.semantic_relationship,
        existing.semantic_relationship_rationale,
        existing.assessed_by,
    )
    if actual != expected:
        raise ClaimSupportRequestConflict(
            "request_id already identifies a different immutable "
            "claim-support assessment"
        )
    return existing


def insert_assessment(
    conn,
    assessment: ResearchEvidenceClaimSupportAssessmentCreate,
) -> ResearchEvidenceClaimSupportAssessmentRecord:
    existing = get_assessment_by_request_id(
        conn,
        project_id=assessment.project_id,
        claim_intake_item_id=assessment.claim_intake_item_id,
        evidence_intake_item_id=assessment.evidence_intake_item_id,
        request_id=assessment.request_id,
    )
    if existing is not None:
        return ensure_retry_matches(existing, assessment)

    conn.execute("SAVEPOINT research_evidence_claim_support_insert")
    try:
        row = conn.execute(
            """
            INSERT INTO research_evidence_claim_support_assessment
                (project_id, claim_intake_item_id, evidence_intake_item_id,
                 request_id, locator_resolution, locator_rationale,
                 evidence_linkage, evidence_linkage_rationale,
                 semantic_relationship, semantic_relationship_rationale,
                 assessed_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING
                id::text, project_id::text, claim_intake_item_id::text,
                evidence_intake_item_id::text, request_id, locator_resolution,
                locator_rationale, evidence_linkage, evidence_linkage_rationale,
                semantic_relationship, semantic_relationship_rationale,
                assessed_by, assessment_sequence, supersedes_assessment_id::text,
                claim_draft_id::text, claim_source_snapshot_id::text,
                claim_source_blob_id::text,
                claim_source_metadata_revision_id::text,
                evidence_source_snapshot_id::text,
                evidence_source_blob_id::text,
                evidence_source_metadata_revision_id::text,
                candidate_fact_revision_id::text,
                fact_metadata_revision_id::text, assessed_at
            """,
            (
                assessment.project_id,
                assessment.claim_intake_item_id,
                assessment.evidence_intake_item_id,
                assessment.request_id,
                assessment.locator_resolution,
                assessment.locator_rationale,
                assessment.evidence_linkage,
                assessment.evidence_linkage_rationale,
                assessment.semantic_relationship,
                assessment.semantic_relationship_rationale,
                assessment.assessed_by,
            ),
        ).fetchone()
    except Exception as exc:
        conn.execute("ROLLBACK TO SAVEPOINT research_evidence_claim_support_insert")
        conn.execute("RELEASE SAVEPOINT research_evidence_claim_support_insert")
        if _constraint_name(exc) == "uq_recsa_pair_request":
            existing = get_assessment_by_request_id(
                conn,
                project_id=assessment.project_id,
                claim_intake_item_id=assessment.claim_intake_item_id,
                evidence_intake_item_id=assessment.evidence_intake_item_id,
                request_id=assessment.request_id,
            )
            if existing is not None:
                return ensure_retry_matches(existing, assessment)
        if _sqlstate(exc).startswith("23"):
            raise ClaimSupportIntegrityError(
                "claim-support assessment violates the immutable database contract"
            ) from exc
        raise
    else:
        conn.execute("RELEASE SAVEPOINT research_evidence_claim_support_insert")
    return _assessment_from_row(row)


_ASSESSMENT_SELECT = """
SELECT
    id::text, project_id::text, claim_intake_item_id::text,
    evidence_intake_item_id::text, request_id, locator_resolution,
    locator_rationale, evidence_linkage, evidence_linkage_rationale,
    semantic_relationship, semantic_relationship_rationale, assessed_by,
    assessment_sequence, supersedes_assessment_id::text, claim_draft_id::text,
    claim_source_snapshot_id::text, claim_source_blob_id::text,
    claim_source_metadata_revision_id::text,
    evidence_source_snapshot_id::text, evidence_source_blob_id::text,
    evidence_source_metadata_revision_id::text,
    candidate_fact_revision_id::text, fact_metadata_revision_id::text,
    assessed_at
FROM research_evidence_claim_support_assessment
"""


def _sqlstate(exc: Exception) -> str:
    value = getattr(exc, "sqlstate", None)
    if value:
        return str(value)
    diag = getattr(exc, "diag", None)
    return str(getattr(diag, "sqlstate", "") or "")


def _constraint_name(exc: Exception) -> str:
    diag = getattr(exc, "diag", None)
    return str(getattr(diag, "constraint_name", "") or "")


def _assessment_from_row(row) -> ResearchEvidenceClaimSupportAssessmentRecord:
    return ResearchEvidenceClaimSupportAssessmentRecord(
        id=row[0],
        project_id=row[1],
        claim_intake_item_id=row[2],
        evidence_intake_item_id=row[3],
        request_id=row[4],
        locator_resolution=row[5],
        locator_rationale=row[6],
        evidence_linkage=row[7],
        evidence_linkage_rationale=row[8],
        semantic_relationship=row[9],
        semantic_relationship_rationale=row[10],
        assessed_by=row[11],
        assessment_sequence=row[12],
        supersedes_assessment_id=row[13],
        claim_draft_id=row[14],
        claim_source_snapshot_id=row[15],
        claim_source_blob_id=row[16],
        claim_source_metadata_revision_id=row[17],
        evidence_source_snapshot_id=row[18],
        evidence_source_blob_id=row[19],
        evidence_source_metadata_revision_id=row[20],
        candidate_fact_revision_id=row[21],
        fact_metadata_revision_id=row[22],
        assessed_at=row[23],
    )
