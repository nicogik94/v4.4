"""Caller-transaction-owned persistence for the canonical evidence pack."""
from __future__ import annotations

import json
from typing import Optional

from pydantic import ValidationError

from . import claim_support_repository, review_repository
from .pack_models import (
    MAX_PACK_CLAIMS,
    MAX_PACK_RELATIONSHIPS,
    MAX_PACK_SOURCES,
    ResearchEvidencePackAggregate,
    ResearchEvidencePackAuthorizedClaim,
    ResearchEvidencePackAuthorizedEvidence,
    ResearchEvidencePackAuthorizedRelationship,
    ResearchEvidencePackAuthorizedSource,
    ResearchEvidencePackClaimAnnotation,
    ResearchEvidencePackContext,
    ResearchEvidencePackCounts,
    ResearchEvidencePackExplicitProbability,
    ResearchEvidencePackQuery,
    ResearchEvidenceClaimAnnotationRevisionCreate,
    ResearchEvidenceClaimAnnotationRevisionRecord,
    ResearchEvidenceExplicitProbability,
    ResearchEvidenceProjectContextRevisionCreate,
    ResearchEvidenceProjectContextRevisionRecord,
    ResearchEvidenceUsageAuthorizationDecisionCreate,
    ResearchEvidenceUsageAuthorizationDecisionRecord,
    UsageScope,
)


class ResearchEvidencePackRepositoryError(ValueError):
    pass


class ResearchEvidencePackParentNotFound(ResearchEvidencePackRepositoryError):
    pass


class ResearchEvidencePackIntegrityError(ResearchEvidencePackRepositoryError):
    pass


class ResearchEvidencePackRequestConflict(ResearchEvidencePackIntegrityError):
    pass


class ResearchEvidencePackTransactionError(ResearchEvidencePackRepositoryError):
    pass


class ResearchEvidencePackCapacityError(ResearchEvidencePackIntegrityError):
    pass


def require_read_committed_transaction(conn) -> None:
    """Require the sole supported caller-owned write transaction mode."""
    if conn.autocommit:
        raise ResearchEvidencePackTransactionError(
            "research-evidence pack writes require a non-autocommit connection"
        )
    row = conn.execute("SHOW transaction_isolation").fetchone()
    isolation = "" if row is None else str(row[0])
    isolation = " ".join(isolation.replace("_", " ").lower().split())
    if isolation != "read committed":
        raise ResearchEvidencePackTransactionError(
            "research-evidence pack writes require PostgreSQL READ COMMITTED isolation"
        )


def _sqlstate(exc: Exception) -> str:
    return str(getattr(exc, "sqlstate", None) or getattr(getattr(exc, "diag", None), "sqlstate", "") or "")


def _constraint(exc: Exception) -> str:
    return str(getattr(getattr(exc, "diag", None), "constraint_name", "") or "")


def _message_primary(exc: Exception) -> str:
    return str(getattr(getattr(exc, "diag", None), "message_primary", "") or "")


def _schema_name(exc: Exception) -> str:
    return str(getattr(getattr(exc, "diag", None), "schema_name", "") or "")


def _table_name(exc: Exception) -> str:
    return str(getattr(getattr(exc, "diag", None), "table_name", "") or "")


_TRANSITION_RECOVERY_MESSAGE = (
    "usage decisions must alternate authorization and revocation"
)


def _is_unique_request_recovery(exc: Exception) -> bool:
    return (
        _sqlstate(exc) == "23505"
        and _constraint(exc) == "uq_reuad_scope_request"
    )


def _is_transition_recovery(exc: Exception) -> bool:
    return (
        _sqlstate(exc) == "23514"
        and not _constraint(exc)
        and not _schema_name(exc)
        and not _table_name(exc)
        and _message_primary(exc) == _TRANSITION_RECOVERY_MESSAGE
    )


def lock_project(conn, *, project_id: str) -> None:
    row = conn.execute("SELECT id::text FROM projects WHERE id = %s FOR UPDATE", (project_id,)).fetchone()
    if row is None:
        raise ResearchEvidencePackParentNotFound("project not found")


_CONTEXT_SELECT = """
SELECT id::text, project_id::text, request_id, research_question,
       project_limitations_json, unresolved_gaps_json, actor, context_sequence,
       supersedes_context_revision_id::text, recorded_at
FROM research_evidence_project_context_revision
"""


def _context(row) -> ResearchEvidenceProjectContextRevisionRecord:
    return ResearchEvidenceProjectContextRevisionRecord(
        id=row[0], project_id=row[1], request_id=row[2], research_question=row[3],
        project_limitations=tuple(row[4]), unresolved_gaps=tuple(row[5]), actor=row[6],
        context_sequence=row[7], supersedes_context_revision_id=row[8], recorded_at=row[9],
    )


def get_project_context_revision_by_request_id(conn, *, project_id: str, request_id: str):
    row = conn.execute(_CONTEXT_SELECT + " WHERE project_id = %s AND request_id = %s", (project_id, request_id)).fetchone()
    return None if row is None else _context(row)


def get_effective_project_context_revision(conn, *, project_id: str):
    row = conn.execute(_CONTEXT_SELECT + " WHERE project_id = %s ORDER BY context_sequence DESC LIMIT 1", (project_id,)).fetchone()
    return None if row is None else _context(row)


def ensure_project_context_retry_matches(existing, value):
    if (
        existing.project_id, existing.request_id, existing.research_question,
        existing.project_limitations, existing.unresolved_gaps, existing.actor,
    ) != (
        value.project_id, value.request_id, value.research_question,
        value.project_limitations, value.unresolved_gaps, value.actor,
    ):
        raise ResearchEvidencePackRequestConflict("request_id identifies a different project-context revision")
    return existing


def insert_project_context_revision(conn, value):
    existing = get_project_context_revision_by_request_id(
        conn, project_id=value.project_id, request_id=value.request_id
    )
    if existing is not None:
        return ensure_project_context_retry_matches(existing, value)
    savepoint = "research_evidence_pack_context_insert"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        row = conn.execute(
            """INSERT INTO research_evidence_project_context_revision
               (project_id, request_id, research_question, project_limitations_json,
                unresolved_gaps_json, actor)
               VALUES (%s,%s,%s,%s::jsonb,%s::jsonb,%s)
               RETURNING id::text, project_id::text, request_id, research_question,
                 project_limitations_json, unresolved_gaps_json, actor, context_sequence,
                 supersedes_context_revision_id::text, recorded_at""",
            (value.project_id, value.request_id, value.research_question,
             json.dumps(value.project_limitations), json.dumps(value.unresolved_gaps), value.actor),
        ).fetchone()
    except Exception as exc:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        if _constraint(exc) == "uq_repcr_project_request":
            existing = get_project_context_revision_by_request_id(conn, project_id=value.project_id, request_id=value.request_id)
            if existing is not None:
                return ensure_project_context_retry_matches(existing, value)
        if _sqlstate(exc).startswith("23"):
            raise ResearchEvidencePackIntegrityError("project-context revision violates the immutable contract") from exc
        raise
    conn.execute(f"RELEASE SAVEPOINT {savepoint}")
    return _context(row)


_ANNOTATION_SELECT = """
SELECT id::text, project_id::text, claim_draft_id::text, request_id,
 epistemic_status, confidence_label, decision_relevance, supports_statement,
 does_not_prove, limitations_json, related_claim_draft_ids_json, operator_notes,
 explicit_probability_value, explicit_probability_provided_by,
 explicit_probability_provenance_reference, explicit_probability_provenance_note,
 actor, annotation_sequence, supersedes_annotation_revision_id::text, recorded_at
FROM research_evidence_claim_annotation_revision
"""


def _annotation(row) -> ResearchEvidenceClaimAnnotationRevisionRecord:
    probability = None
    if row[12] is not None:
        probability = ResearchEvidenceExplicitProbability(value=row[12], provided_by=row[13], provenance_reference=row[14], provenance_note=row[15])
    return ResearchEvidenceClaimAnnotationRevisionRecord(
        id=row[0], project_id=row[1], claim_draft_id=row[2], request_id=row[3],
        epistemic_status=row[4], confidence_label=row[5], decision_relevance=row[6],
        supports_statement=row[7], does_not_prove=row[8], limitations=tuple(row[9]),
        related_claim_draft_ids=tuple(row[10]), operator_notes=row[11],
        explicit_probability=probability, actor=row[16], annotation_sequence=row[17],
        supersedes_annotation_revision_id=row[18], recorded_at=row[19],
    )


def get_claim_annotation_revision_by_request_id(conn, *, project_id: str, claim_draft_id: str, request_id: str):
    row = conn.execute(_ANNOTATION_SELECT + " WHERE project_id=%s AND claim_draft_id=%s AND request_id=%s", (project_id, claim_draft_id, request_id)).fetchone()
    return None if row is None else _annotation(row)


def get_effective_claim_annotation_revision(conn, *, project_id: str, claim_draft_id: str):
    row = conn.execute(_ANNOTATION_SELECT + " WHERE project_id=%s AND claim_draft_id=%s ORDER BY annotation_sequence DESC LIMIT 1", (project_id, claim_draft_id)).fetchone()
    return None if row is None else _annotation(row)


def list_effective_project_annotations(conn, *, project_id: str):
    rows = conn.execute(_ANNOTATION_SELECT + " WHERE project_id=%s AND (project_id,claim_draft_id,annotation_sequence) IN (SELECT project_id,claim_draft_id,max(annotation_sequence) FROM research_evidence_claim_annotation_revision WHERE project_id=%s GROUP BY project_id,claim_draft_id) ORDER BY claim_draft_id", (project_id, project_id)).fetchall()
    return [_annotation(row) for row in rows]


def ensure_claim_annotation_retry_matches(existing, value):
    fields = ("project_id","claim_draft_id","request_id","epistemic_status","confidence_label","decision_relevance","supports_statement","does_not_prove","limitations","related_claim_draft_ids","operator_notes","explicit_probability","actor")
    if tuple(getattr(existing, f) for f in fields) != tuple(getattr(value, f) for f in fields):
        raise ResearchEvidencePackRequestConflict("request_id identifies a different claim-annotation revision")
    return existing


def require_claim_and_related_claims(conn, *, project_id: str, claim_draft_id: str, related_claim_draft_ids: tuple[str, ...]) -> None:
    ids = (claim_draft_id,) + related_claim_draft_ids
    count = conn.execute("SELECT count(*) FROM research_claim_draft WHERE project_id=%s AND id = ANY(%s::uuid[])", (project_id, list(ids))).fetchone()[0]
    if count != len(ids):
        raise ResearchEvidencePackParentNotFound("canonical claim or related claim not found for project")


def insert_claim_annotation_revision(conn, value):
    existing = get_claim_annotation_revision_by_request_id(
        conn, project_id=value.project_id, claim_draft_id=value.claim_draft_id,
        request_id=value.request_id,
    )
    if existing is not None:
        return ensure_claim_annotation_retry_matches(existing, value)
    p = value.explicit_probability
    params = (value.project_id,value.claim_draft_id,value.request_id,value.epistemic_status.value,value.confidence_label.value,value.decision_relevance,value.supports_statement,value.does_not_prove,json.dumps(value.limitations),json.dumps(value.related_claim_draft_ids),value.operator_notes,None if p is None else p.value,None if p is None else p.provided_by.value,None if p is None else p.provenance_reference,None if p is None else p.provenance_note,value.actor)
    savepoint="research_evidence_pack_annotation_insert"; conn.execute(f"SAVEPOINT {savepoint}")
    try:
        row=conn.execute("""INSERT INTO research_evidence_claim_annotation_revision
        (project_id,claim_draft_id,request_id,epistemic_status,confidence_label,decision_relevance,supports_statement,does_not_prove,limitations_json,related_claim_draft_ids_json,operator_notes,explicit_probability_value,explicit_probability_provided_by,explicit_probability_provenance_reference,explicit_probability_provenance_note,actor)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s)
        RETURNING id::text,project_id::text,claim_draft_id::text,request_id,epistemic_status,confidence_label,decision_relevance,supports_statement,does_not_prove,limitations_json,related_claim_draft_ids_json,operator_notes,explicit_probability_value,explicit_probability_provided_by,explicit_probability_provenance_reference,explicit_probability_provenance_note,actor,annotation_sequence,supersedes_annotation_revision_id::text,recorded_at""",params).fetchone()
    except Exception as exc:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}"); conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        if _constraint(exc)=="uq_recar_claim_request":
            existing=get_claim_annotation_revision_by_request_id(conn,project_id=value.project_id,claim_draft_id=value.claim_draft_id,request_id=value.request_id)
            if existing is not None: return ensure_claim_annotation_retry_matches(existing,value)
        if _sqlstate(exc).startswith("23"): raise ResearchEvidencePackIntegrityError("claim annotation violates the immutable contract") from exc
        raise
    conn.execute(f"RELEASE SAVEPOINT {savepoint}"); return _annotation(row)


_AUTH_SELECT="""SELECT id::text,project_id::text,claim_intake_item_id::text,evidence_intake_item_id::text,claim_support_assessment_id::text,usage_scope,decision,reason,actor,request_id,claim_draft_id::text,claim_annotation_revision_id::text,claim_review_decision_id::text,evidence_review_decision_id::text,decision_sequence,supersedes_decision_id::text,recorded_at FROM research_evidence_usage_authorization_decision"""


def _authorization(row):
    return ResearchEvidenceUsageAuthorizationDecisionRecord(id=row[0],project_id=row[1],claim_intake_item_id=row[2],evidence_intake_item_id=row[3],claim_support_assessment_id=row[4],usage_scope=row[5],decision=row[6],reason=row[7],actor=row[8],request_id=row[9],claim_draft_id=row[10],claim_annotation_revision_id=row[11],claim_review_decision_id=row[12],evidence_review_decision_id=row[13],decision_sequence=row[14],supersedes_decision_id=row[15],recorded_at=row[16])


def get_usage_authorization_decision_by_request_id(conn, *, project_id, claim_intake_item_id, evidence_intake_item_id, usage_scope, request_id):
    row=conn.execute(_AUTH_SELECT+" WHERE project_id=%s AND claim_intake_item_id=%s AND evidence_intake_item_id=%s AND usage_scope=%s AND request_id=%s",(project_id,claim_intake_item_id,evidence_intake_item_id,UsageScope(usage_scope).value,request_id)).fetchone(); return None if row is None else _authorization(row)


def get_effective_usage_authorization_decision(conn, *, project_id, claim_intake_item_id, evidence_intake_item_id, usage_scope):
    row=conn.execute(_AUTH_SELECT+" WHERE project_id=%s AND claim_intake_item_id=%s AND evidence_intake_item_id=%s AND usage_scope=%s ORDER BY decision_sequence DESC LIMIT 1",(project_id,claim_intake_item_id,evidence_intake_item_id,UsageScope(usage_scope).value)).fetchone(); return None if row is None else _authorization(row)


def list_effective_project_authorizations(conn, *, project_id):
    return [
        decision
        for decision, _ in _effective_usage_authorization_rows(
            conn, project_id=project_id,
        )
    ]


def ensure_usage_authorization_retry_matches(existing,value):
    fields=("project_id","claim_intake_item_id","evidence_intake_item_id","usage_scope","decision","reason","actor","request_id")
    if tuple(getattr(existing,f) for f in fields)!=tuple(getattr(value,f) for f in fields): raise ResearchEvidencePackRequestConflict("request_id identifies a different usage-authorization decision")
    return existing


def insert_usage_authorization_decision(conn,value):
    # Direct repository callers receive the same bounded transaction-scoped
    # serializer as service callers.  Re-locking a row already locked by this
    # transaction is harmless.
    require_read_committed_transaction(conn)
    lock_project(conn, project_id=value.project_id)
    existing = get_usage_authorization_decision_by_request_id(
        conn, project_id=value.project_id,
        claim_intake_item_id=value.claim_intake_item_id,
        evidence_intake_item_id=value.evidence_intake_item_id,
        usage_scope=value.usage_scope, request_id=value.request_id,
    )
    if existing is not None:
        return ensure_usage_authorization_retry_matches(existing, value)
    savepoint="research_evidence_pack_authorization_insert"; conn.execute(f"SAVEPOINT {savepoint}")
    try:
        row=conn.execute("""INSERT INTO research_evidence_usage_authorization_decision(project_id,claim_intake_item_id,evidence_intake_item_id,usage_scope,decision,reason,actor,request_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id::text,project_id::text,claim_intake_item_id::text,evidence_intake_item_id::text,claim_support_assessment_id::text,usage_scope,decision,reason,actor,request_id,claim_draft_id::text,claim_annotation_revision_id::text,claim_review_decision_id::text,evidence_review_decision_id::text,decision_sequence,supersedes_decision_id::text,recorded_at""",(value.project_id,value.claim_intake_item_id,value.evidence_intake_item_id,value.usage_scope.value,value.decision.value,value.reason,value.actor,value.request_id)).fetchone()
    except Exception as exc:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        request_race = (
            _is_unique_request_recovery(exc)
            or _is_transition_recovery(exc)
        )
        try:
            if request_race:
                existing=get_usage_authorization_decision_by_request_id(conn,project_id=value.project_id,claim_intake_item_id=value.claim_intake_item_id,evidence_intake_item_id=value.evidence_intake_item_id,usage_scope=value.usage_scope,request_id=value.request_id)
                if existing is not None:
                    return ensure_usage_authorization_retry_matches(existing,value)
            if request_race:
                raise ResearchEvidencePackIntegrityError("usage authorization violates the immutable contract") from exc
            raise
        finally:
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
    conn.execute(f"RELEASE SAVEPOINT {savepoint}"); return _authorization(row)


_EFFECTIVE_AUTHORIZATION_SELECT = """
WITH latest_decisions AS (
  SELECT DISTINCT ON (
    claim_intake_item_id,evidence_intake_item_id,usage_scope
  ) d.*
  FROM research_evidence_usage_authorization_decision d
  WHERE d.project_id=%s
  ORDER BY claim_intake_item_id,evidence_intake_item_id,usage_scope,
           decision_sequence DESC
)
SELECT d.id::text,d.project_id::text,d.claim_intake_item_id::text,
       d.evidence_intake_item_id::text,d.claim_support_assessment_id::text,
       d.usage_scope,d.decision,d.reason,d.actor,d.request_id,
       d.claim_draft_id::text,d.claim_annotation_revision_id::text,
       d.claim_review_decision_id::text,d.evidence_review_decision_id::text,
       d.decision_sequence,d.supersedes_decision_id::text,d.recorded_at,
       evidence_item.source_snapshot_id::text
FROM latest_decisions d
JOIN research_evidence_intake_item claim_item
  ON claim_item.id=d.claim_intake_item_id
 AND claim_item.project_id=d.project_id
 AND claim_item.item_kind='claim_draft'
 AND claim_item.claim_draft_id=d.claim_draft_id
JOIN research_evidence_intake_item evidence_item
  ON evidence_item.id=d.evidence_intake_item_id
 AND evidence_item.project_id=d.project_id
 AND evidence_item.item_kind='candidate_fact'
JOIN research_evidence_claim_annotation_revision annotation
  ON annotation.id=d.claim_annotation_revision_id
 AND annotation.project_id=d.project_id
 AND annotation.claim_draft_id=d.claim_draft_id
JOIN research_evidence_claim_support_assessment support
  ON support.id=d.claim_support_assessment_id
 AND support.project_id=d.project_id
 AND support.claim_intake_item_id=d.claim_intake_item_id
 AND support.evidence_intake_item_id=d.evidence_intake_item_id
JOIN research_evidence_intake_item_review_decision claim_review
  ON claim_review.id=d.claim_review_decision_id
 AND claim_review.project_id=d.project_id
 AND claim_review.research_evidence_intake_item_id=d.claim_intake_item_id
JOIN research_evidence_intake_item_review_decision evidence_review
  ON evidence_review.id=d.evidence_review_decision_id
 AND evidence_review.project_id=d.project_id
 AND evidence_review.research_evidence_intake_item_id=d.evidence_intake_item_id
WHERE d.decision='authorized'
  AND annotation.annotation_sequence=(
    SELECT max(current_annotation.annotation_sequence)
    FROM research_evidence_claim_annotation_revision current_annotation
    WHERE current_annotation.project_id=d.project_id
      AND current_annotation.claim_draft_id=d.claim_draft_id
  )
  AND support.assessment_sequence=(
    SELECT max(current_support.assessment_sequence)
    FROM research_evidence_claim_support_assessment current_support
    WHERE current_support.project_id=d.project_id
      AND current_support.claim_intake_item_id=d.claim_intake_item_id
      AND current_support.evidence_intake_item_id=d.evidence_intake_item_id
  )
  AND support.locator_resolution='resolvable'
  AND support.evidence_linkage='linked'
  AND support.semantic_relationship IN ('support','qualification')
  AND claim_review.decision_sequence=(
    SELECT max(current_claim_review.decision_sequence)
    FROM research_evidence_intake_item_review_decision current_claim_review
    WHERE current_claim_review.project_id=d.project_id
      AND current_claim_review.research_evidence_intake_item_id=d.claim_intake_item_id
  )
  AND claim_review.decision_type='approved'
  AND evidence_review.decision_sequence=(
    SELECT max(current_evidence_review.decision_sequence)
    FROM research_evidence_intake_item_review_decision current_evidence_review
    WHERE current_evidence_review.project_id=d.project_id
      AND current_evidence_review.research_evidence_intake_item_id=d.evidence_intake_item_id
  )
  AND evidence_review.decision_type='approved'
ORDER BY d.usage_scope, d.claim_draft_id, evidence_item.source_snapshot_id,
         d.decision_sequence, d.id
"""


def _effective_usage_authorization_rows(conn, *, project_id: str):
    rows = conn.execute(_EFFECTIVE_AUTHORIZATION_SELECT, (project_id,)).fetchall()
    effective = []
    claim_endpoint_effectiveness = {}
    evidence_endpoint_effectiveness = {}
    for row in rows:
        decision = _authorization(row[:17])
        try:
            if decision.claim_intake_item_id not in claim_endpoint_effectiveness:
                claim_endpoint_effectiveness[decision.claim_intake_item_id] = (
                    claim_support_repository.claim_endpoint_is_available(
                        conn, project_id=project_id,
                        claim_intake_item_id=decision.claim_intake_item_id,
                    )
                    and claim_support_repository.claim_endpoint_lineage_is_current(
                        conn, project_id=project_id,
                        claim_intake_item_id=decision.claim_intake_item_id,
                    )
                )
            if decision.evidence_intake_item_id not in evidence_endpoint_effectiveness:
                evidence_endpoint_effectiveness[decision.evidence_intake_item_id] = (
                    claim_support_repository.evidence_endpoint_is_available(
                        conn, project_id=project_id,
                        evidence_intake_item_id=decision.evidence_intake_item_id,
                    )
                    and claim_support_repository.evidence_endpoint_lineage_is_current(
                        conn, project_id=project_id,
                        evidence_intake_item_id=decision.evidence_intake_item_id,
                    )
                )
            endpoints_effective = (
                claim_endpoint_effectiveness[decision.claim_intake_item_id]
                and evidence_endpoint_effectiveness[decision.evidence_intake_item_id]
            )
        except (ResearchEvidencePackRepositoryError, ValueError):
            endpoints_effective = False
        if endpoints_effective:
            effective.append((decision, row[17]))
    return effective


def effective_project_pack_member_counts(conn, *, project_id: str) -> tuple[int,int]:
    rows = _effective_usage_authorization_rows(conn, project_id=project_id)
    return len({source_snapshot_id for _, source_snapshot_id in rows}), len({decision.claim_draft_id for decision, _ in rows})


def effective_project_pack_capacity(
    conn, *, project_id: str, claim_draft_id: str,
    evidence_intake_item_id: str,
) -> tuple[int, int, bool, bool]:
    target = conn.execute(
        """SELECT source_snapshot_id::text
           FROM research_evidence_intake_item
           WHERE project_id=%s AND id=%s AND item_kind='candidate_fact'""",
        (project_id, evidence_intake_item_id),
    ).fetchone()
    rows = _effective_usage_authorization_rows(conn, project_id=project_id)
    sources = {source_snapshot_id for _, source_snapshot_id in rows}
    claims = {decision.claim_draft_id for decision, _ in rows}
    return (
        len(sources), len(claims),
        target is not None and target[0] in sources,
        claim_draft_id in claims,
    )


def project_pack_member_presence(conn, *, project_id: str, claim_draft_id: str, evidence_intake_item_id: str) -> tuple[bool, bool]:
    target = conn.execute(
        """SELECT source_snapshot_id::text
           FROM research_evidence_intake_item
           WHERE project_id=%s AND id=%s AND item_kind='candidate_fact'""",
        (project_id, evidence_intake_item_id),
    ).fetchone()
    if target is None:
        return False, False
    rows = _effective_usage_authorization_rows(conn, project_id=project_id)
    return (
        any(source_snapshot_id == target[0] for _, source_snapshot_id in rows),
        any(decision.claim_draft_id == claim_draft_id for decision, _ in rows),
    )


def usage_authorization_is_effective(conn, decision) -> bool:
    if decision is None:
        return False
    return any(
        current.id == decision.id
        for current, _ in _effective_usage_authorization_rows(
            conn, project_id=decision.project_id,
        )
    )


_PACK_ASSEMBLY_SELECT = """
WITH latest_decisions AS MATERIALIZED (
  SELECT DISTINCT ON (
    d.claim_intake_item_id,d.evidence_intake_item_id,d.usage_scope
  ) d.*
  FROM research_evidence_usage_authorization_decision d
  WHERE d.project_id=%s AND d.usage_scope=%s
  ORDER BY d.claim_intake_item_id,d.evidence_intake_item_id,d.usage_scope,
           d.decision_sequence DESC,d.id DESC
), eligible AS MATERIALIZED (
  SELECT
    d.id::text AS authorization_decision_id,
    d.claim_intake_item_id::text,
    d.evidence_intake_item_id::text,
    d.claim_support_assessment_id::text,
    d.claim_draft_id::text,
    d.claim_annotation_revision_id::text,
    d.claim_review_decision_id::text,
    d.evidence_review_decision_id::text,
    d.decision_sequence AS authorization_sequence,
    d.recorded_at AS authorized_at,
    evidence_snapshot.id::text AS source_snapshot_id,
    evidence_blob.id::text AS source_blob_id,
    evidence_source_metadata.id::text AS source_metadata_revision_id,
    evidence_snapshot.source_kind,
    evidence_snapshot.source_locator,
    evidence_snapshot.captured_at,
    evidence_source_metadata.canonical_source_locator,
    evidence_source_metadata.publisher,
    evidence_source_metadata.author,
    evidence_source_metadata.published_at,
    evidence_source_metadata.retrieved_at,
    evidence_source_metadata.citation_label,
    evidence_source_metadata.declared_quality_tier,
    evidence_source_metadata.declared_quality_rationale,
    fact.id::text AS candidate_fact_revision_id,
    fact_metadata.id::text AS fact_metadata_revision_id,
    fact.fact_type,
    fact.numeric_value,
    fact.text_value,
    fact.unit,
    fact.currency_code,
    fact.as_of_date,
    fact.numerator_context,
    fact.denominator_context,
    fact.percentage_basis,
    fact.percentage_subtype,
    fact.time_unit,
    fact.counted_entity,
    fact_metadata.stable_fact_key,
    fact_metadata.source_char_range,
    fact_metadata.citation_locator,
    claim.claim_text,
    claim.claim_category,
    annotation.epistemic_status,
    annotation.confidence_label,
    annotation.decision_relevance,
    annotation.supports_statement,
    annotation.does_not_prove,
    annotation.limitations_json,
    annotation.related_claim_draft_ids_json,
    annotation.explicit_probability_value,
    annotation.explicit_probability_provided_by,
    annotation.explicit_probability_provenance_reference,
    annotation.explicit_probability_provenance_note,
    annotation.annotation_sequence,
    annotation.recorded_at AS annotation_recorded_at,
    support.locator_resolution,
    support.evidence_linkage,
    support.semantic_relationship
  FROM latest_decisions d
  JOIN research_evidence_intake_item claim_item
    ON claim_item.id=d.claim_intake_item_id
   AND claim_item.project_id=d.project_id
   AND claim_item.item_kind='claim_draft'
   AND claim_item.claim_draft_id=d.claim_draft_id
  JOIN research_evidence_intake claim_intake
    ON claim_intake.id=claim_item.research_evidence_intake_id
   AND claim_intake.project_id=d.project_id
   AND claim_intake.source_snapshot_id=claim_item.source_snapshot_id
  JOIN source_snapshot claim_snapshot
    ON claim_snapshot.id=claim_item.source_snapshot_id
   AND claim_snapshot.project_id=d.project_id
  JOIN source_blob claim_blob
    ON claim_blob.id=claim_snapshot.source_blob_id
   AND claim_blob.project_id=d.project_id
  JOIN research_source_metadata_revision claim_source_metadata
    ON claim_source_metadata.id=claim_intake.source_metadata_revision_id
   AND claim_source_metadata.project_id=d.project_id
   AND claim_source_metadata.source_snapshot_id=claim_snapshot.id
  JOIN research_claim_draft claim
    ON claim.id=d.claim_draft_id AND claim.project_id=d.project_id
  JOIN research_evidence_intake_item evidence_item
    ON evidence_item.id=d.evidence_intake_item_id
   AND evidence_item.project_id=d.project_id
   AND evidence_item.item_kind='candidate_fact'
  JOIN research_evidence_intake evidence_intake
    ON evidence_intake.id=evidence_item.research_evidence_intake_id
   AND evidence_intake.project_id=d.project_id
   AND evidence_intake.source_snapshot_id=evidence_item.source_snapshot_id
  JOIN source_snapshot evidence_snapshot
    ON evidence_snapshot.id=evidence_item.source_snapshot_id
   AND evidence_snapshot.project_id=d.project_id
  JOIN source_blob evidence_blob
    ON evidence_blob.id=evidence_snapshot.source_blob_id
   AND evidence_blob.project_id=d.project_id
  JOIN research_source_metadata_revision evidence_source_metadata
    ON evidence_source_metadata.id=evidence_intake.source_metadata_revision_id
   AND evidence_source_metadata.project_id=d.project_id
   AND evidence_source_metadata.source_snapshot_id=evidence_snapshot.id
  JOIN candidate_fact_revision fact
    ON fact.id=evidence_item.candidate_fact_revision_id
   AND fact.project_id=d.project_id
   AND fact.source_snapshot_id=evidence_snapshot.id
  JOIN research_fact_metadata_revision fact_metadata
    ON fact_metadata.id=evidence_item.fact_metadata_revision_id
   AND fact_metadata.project_id=d.project_id
   AND fact_metadata.candidate_fact_revision_id=fact.id
  JOIN research_evidence_claim_annotation_revision annotation
    ON annotation.id=d.claim_annotation_revision_id
   AND annotation.project_id=d.project_id
   AND annotation.claim_draft_id=d.claim_draft_id
  JOIN research_evidence_claim_support_assessment support
    ON support.id=d.claim_support_assessment_id
   AND support.project_id=d.project_id
   AND support.claim_intake_item_id=d.claim_intake_item_id
   AND support.evidence_intake_item_id=d.evidence_intake_item_id
   AND support.claim_draft_id=claim.id
   AND support.claim_source_snapshot_id=claim_snapshot.id
   AND support.claim_source_blob_id=claim_blob.id
   AND support.claim_source_metadata_revision_id=claim_source_metadata.id
   AND support.evidence_source_snapshot_id=evidence_snapshot.id
   AND support.evidence_source_blob_id=evidence_blob.id
   AND support.evidence_source_metadata_revision_id=evidence_source_metadata.id
   AND support.candidate_fact_revision_id=fact.id
   AND support.fact_metadata_revision_id=fact_metadata.id
  JOIN research_evidence_intake_item_review_decision claim_review
    ON claim_review.id=d.claim_review_decision_id
   AND claim_review.project_id=d.project_id
   AND claim_review.research_evidence_intake_item_id=d.claim_intake_item_id
  JOIN research_evidence_intake_item_review_decision evidence_review
    ON evidence_review.id=d.evidence_review_decision_id
   AND evidence_review.project_id=d.project_id
   AND evidence_review.research_evidence_intake_item_id=d.evidence_intake_item_id
  WHERE d.decision='authorized'
    AND annotation.annotation_sequence=(
      SELECT max(current_annotation.annotation_sequence)
      FROM research_evidence_claim_annotation_revision current_annotation
      WHERE current_annotation.project_id=d.project_id
        AND current_annotation.claim_draft_id=d.claim_draft_id
    )
    AND support.assessment_sequence=(
      SELECT max(current_support.assessment_sequence)
      FROM research_evidence_claim_support_assessment current_support
      WHERE current_support.project_id=d.project_id
        AND current_support.claim_intake_item_id=d.claim_intake_item_id
        AND current_support.evidence_intake_item_id=d.evidence_intake_item_id
    )
    AND support.locator_resolution='resolvable'
    AND support.evidence_linkage='linked'
    AND support.semantic_relationship IN ('support','qualification')
    AND claim_review.decision_sequence=(
      SELECT max(current_claim_review.decision_sequence)
      FROM research_evidence_intake_item_review_decision current_claim_review
      WHERE current_claim_review.project_id=d.project_id
        AND current_claim_review.research_evidence_intake_item_id=d.claim_intake_item_id
    )
    AND claim_review.decision_type='approved'
    AND evidence_review.decision_sequence=(
      SELECT max(current_evidence_review.decision_sequence)
      FROM research_evidence_intake_item_review_decision current_evidence_review
      WHERE current_evidence_review.project_id=d.project_id
        AND current_evidence_review.research_evidence_intake_item_id=d.evidence_intake_item_id
    )
    AND evidence_review.decision_type='approved'
    AND NOT EXISTS (
      SELECT 1 FROM evidence_retention_event retention
      WHERE retention.project_id=d.project_id
        AND retention.event_type IN ('tombstone','redact')
        AND (retention.source_snapshot_id=claim_snapshot.id
             OR retention.source_blob_id=claim_blob.id)
    )
    AND NOT EXISTS (
      SELECT 1 FROM evidence_retention_event retention
      WHERE retention.project_id=d.project_id
        AND retention.event_type IN ('tombstone','redact')
        AND (retention.candidate_fact_revision_id=fact.id
             OR retention.source_snapshot_id=evidence_snapshot.id
             OR retention.source_blob_id=evidence_blob.id)
    )
    AND NOT EXISTS (
      SELECT 1 FROM research_source_metadata_revision successor
      WHERE successor.project_id=d.project_id
        AND successor.source_snapshot_id=claim_source_metadata.source_snapshot_id
        AND successor.supersedes_metadata_revision_id=claim_source_metadata.id
    )
    AND NOT EXISTS (
      SELECT 1 FROM research_evidence_event event
      WHERE event.project_id=d.project_id
        AND event.entity_type='source_metadata_revision'
        AND event.entity_id=claim_source_metadata.id
        AND event.event_type IN ('superseded','withdrawn')
    )
    AND NOT EXISTS (
      SELECT 1 FROM research_claim_draft successor
      WHERE successor.project_id=d.project_id
        AND successor.supersedes_claim_id=claim.id
    )
    AND NOT EXISTS (
      SELECT 1 FROM research_evidence_event event
      WHERE event.project_id=d.project_id
        AND event.entity_type='claim_draft'
        AND event.entity_id=claim.id
        AND event.event_type IN ('superseded','withdrawn')
    )
    AND NOT EXISTS (
      SELECT 1 FROM research_source_metadata_revision successor
      WHERE successor.project_id=d.project_id
        AND successor.source_snapshot_id=evidence_source_metadata.source_snapshot_id
        AND successor.supersedes_metadata_revision_id=evidence_source_metadata.id
    )
    AND NOT EXISTS (
      SELECT 1 FROM research_evidence_event event
      WHERE event.project_id=d.project_id
        AND event.entity_type='source_metadata_revision'
        AND event.entity_id=evidence_source_metadata.id
        AND event.event_type IN ('superseded','withdrawn')
    )
    AND NOT EXISTS (
      SELECT 1 FROM research_fact_metadata_revision successor
      WHERE successor.project_id=d.project_id
        AND successor.candidate_fact_revision_id=fact_metadata.candidate_fact_revision_id
        AND successor.supersedes_metadata_revision_id=fact_metadata.id
    )
    AND NOT EXISTS (
      SELECT 1 FROM research_fact_metadata_revision replacement
      WHERE replacement.project_id=d.project_id
        AND replacement.supersedes_candidate_fact_revision_id=fact.id
    )
    AND NOT EXISTS (
      SELECT 1 FROM research_evidence_event event
      WHERE event.project_id=d.project_id
        AND event.entity_type='fact_metadata_revision'
        AND event.entity_id=fact_metadata.id
        AND event.event_type IN ('superseded','withdrawn')
    )
), canonical_relationships AS (
  SELECT DISTINCT ON (claim_draft_id,candidate_fact_revision_id) *
  FROM eligible
  ORDER BY claim_draft_id,candidate_fact_revision_id,
           claim_intake_item_id,evidence_intake_item_id,
           authorization_sequence DESC,authorization_decision_id
)
SELECT * FROM canonical_relationships
ORDER BY claim_draft_id,source_snapshot_id,candidate_fact_revision_id,
         authorization_sequence,authorization_decision_id
LIMIT %s
"""


def _put_unique(target: dict, key: str, value, *, label: str) -> None:
    existing = target.get(key)
    if existing is not None and existing != value:
        raise ResearchEvidencePackIntegrityError(
            f"conflicting canonical {label} rows in evidence-pack assembly"
        )
    target[key] = value


def _assembly_members(row, usage_scope: UsageScope):
    probability = None
    if row[50] is not None:
        probability = ResearchEvidencePackExplicitProbability(
            value=row[50], provided_by=row[51], provenance_reference=row[52],
            provenance_note=row[53],
        )
    annotation = ResearchEvidencePackClaimAnnotation(
        annotation_revision_id=row[5], claim_draft_id=row[4],
        annotation_sequence=row[54], epistemic_status=row[43],
        confidence_label=row[44], decision_relevance=row[45],
        supports_statement=row[46], does_not_prove=row[47],
        limitations=tuple(row[48]), related_claim_draft_ids=tuple(row[49]),
        explicit_probability=probability, recorded_at=row[55],
    )
    claim = ResearchEvidencePackAuthorizedClaim(
        claim_draft_id=row[4], claim_text=row[41], claim_category=row[42],
        annotation=annotation,
    )
    source = ResearchEvidencePackAuthorizedSource(
        source_snapshot_id=row[10], source_blob_id=row[11],
        source_metadata_revision_id=row[12], source_kind=row[13],
        source_locator=row[14], captured_at=row[15],
        canonical_source_locator=row[16], publisher=row[17], author=row[18],
        published_at=row[19], retrieved_at=row[20], citation_label=row[21],
        declared_quality_tier=row[22], declared_quality_rationale=row[23],
    )
    evidence = ResearchEvidencePackAuthorizedEvidence(
        candidate_fact_revision_id=row[24], source_snapshot_id=row[10],
        fact_metadata_revision_id=row[25],
        fact_type=row[26], numeric_value=row[27], text_value=row[28], unit=row[29],
        currency_code=row[30], as_of_date=row[31], numerator_context=row[32],
        denominator_context=row[33], percentage_basis=row[34],
        percentage_subtype=row[35], time_unit=row[36], counted_entity=row[37],
        stable_fact_key=row[38], source_char_range=row[39], citation_locator=row[40],
    )
    relationship = ResearchEvidencePackAuthorizedRelationship(
        authorization_decision_id=row[0], claim_intake_item_id=row[1],
        evidence_intake_item_id=row[2], claim_support_assessment_id=row[3],
        claim_draft_id=row[4], candidate_fact_revision_id=row[24],
        source_snapshot_id=row[10], claim_annotation_revision_id=row[5],
        claim_review_decision_id=row[6], evidence_review_decision_id=row[7],
        usage_scope=usage_scope, authorization_sequence=row[8], authorized_at=row[9],
        locator_resolution=row[56], evidence_linkage=row[57],
        semantic_relationship=row[58],
    )
    return claim, source, evidence, relationship


def assemble_effective_project_pack(
    conn, *, project_id: str, usage_scope: UsageScope,
) -> ResearchEvidencePackAggregate:
    """Assemble one bounded current pack without taking transaction ownership."""
    query = ResearchEvidencePackQuery(
        project_id=project_id, usage_scope=usage_scope,
    )
    project = conn.execute(
        "SELECT id::text FROM projects WHERE id=%s", (query.project_id,),
    ).fetchone()
    if project is None:
        raise ResearchEvidencePackParentNotFound("project not found")

    rows = conn.execute(
        _PACK_ASSEMBLY_SELECT,
        (query.project_id, query.usage_scope.value, MAX_PACK_RELATIONSHIPS + 1),
    ).fetchall()
    if len(rows) > MAX_PACK_RELATIONSHIPS:
        raise ResearchEvidencePackCapacityError(
            f"current pack exceeds {MAX_PACK_RELATIONSHIPS} canonical relationships"
        )
    if not rows:
        return ResearchEvidencePackAggregate(
            project_id=query.project_id, usage_scope=query.usage_scope,
        )

    claims: dict[str, ResearchEvidencePackAuthorizedClaim] = {}
    sources: dict[str, ResearchEvidencePackAuthorizedSource] = {}
    evidence_items: dict[str, ResearchEvidencePackAuthorizedEvidence] = {}
    relationships: dict[tuple[str, str], ResearchEvidencePackAuthorizedRelationship] = {}
    try:
        for row in rows:
            claim, source, evidence, relationship = _assembly_members(
                row, query.usage_scope,
            )
            _put_unique(claims, claim.claim_draft_id, claim, label="claim")
            _put_unique(sources, source.source_snapshot_id, source, label="source")
            _put_unique(
                evidence_items, evidence.candidate_fact_revision_id, evidence,
                label="evidence",
            )
            relationship_key = (
                relationship.claim_draft_id,
                relationship.candidate_fact_revision_id,
            )
            existing = relationships.get(relationship_key)
            if existing is not None and existing != relationship:
                raise ResearchEvidencePackIntegrityError(
                    "conflicting duplicate canonical relationship in evidence-pack assembly"
                )
            relationships[relationship_key] = relationship

        if len(sources) > MAX_PACK_SOURCES:
            raise ResearchEvidencePackCapacityError(
                f"current pack exceeds {MAX_PACK_SOURCES} distinct source snapshots"
            )
        if len(claims) > MAX_PACK_CLAIMS:
            raise ResearchEvidencePackCapacityError(
                f"current pack exceeds {MAX_PACK_CLAIMS} distinct canonical claims"
            )

        current_context = get_effective_project_context_revision(
            conn, project_id=query.project_id,
        )
        context = None if current_context is None else ResearchEvidencePackContext(
            context_revision_id=current_context.id,
            context_sequence=current_context.context_sequence,
            research_question=current_context.research_question,
            project_limitations=current_context.project_limitations,
            unresolved_gaps=current_context.unresolved_gaps,
            recorded_at=current_context.recorded_at,
        )
        ordered_claims = tuple(sorted(claims.values(), key=lambda item: item.claim_draft_id))
        ordered_sources = tuple(sorted(sources.values(), key=lambda item: item.source_snapshot_id))
        ordered_evidence = tuple(sorted(
            evidence_items.values(),
            key=lambda item: (item.source_snapshot_id, item.candidate_fact_revision_id),
        ))
        ordered_relationships = tuple(sorted(
            relationships.values(),
            key=lambda item: (
                item.claim_draft_id, item.source_snapshot_id,
                item.candidate_fact_revision_id, item.authorization_sequence,
                item.authorization_decision_id,
            ),
        ))
        return ResearchEvidencePackAggregate(
            project_id=query.project_id, usage_scope=query.usage_scope,
            context=context, claims=ordered_claims, sources=ordered_sources,
            evidence=ordered_evidence, relationships=ordered_relationships,
            counts=ResearchEvidencePackCounts(
                source_count=len(ordered_sources), claim_count=len(ordered_claims),
                evidence_count=len(ordered_evidence),
                relationship_count=len(ordered_relationships),
            ),
        )
    except ValidationError as exc:
        raise ResearchEvidencePackIntegrityError(
            "structurally invalid persisted state in evidence-pack assembly"
        ) from exc
