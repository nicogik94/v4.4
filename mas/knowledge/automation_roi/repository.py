"""Narrow append-only repository for Slice B Automation ROI records.

Insert/read only — there is no update or delete path for any of the five
immutable Slice B tables (the database rejects mutation via triggers). All
functions operate on a caller-supplied synchronous ``psycopg`` connection so the
authoritative MAS database (production) or a dependency-injected disposable
database (tests) can be used interchangeably.

Eligible candidate-fact creation reuses Slice A's typed validation
(``knowledge.evidence_snapshot.validation``) and fact insert
(``knowledge.evidence_snapshot.repository.insert_fact``); Slice B never bypasses
the Slice A fact contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from knowledge.evidence_snapshot import repository as evidence_repo
from knowledge.evidence_snapshot.validation import ValidatedFact

from . import approvals
from .calculator import ResolvedInput, RoiComputation, input_compatibility_reason


class FrozenInputCompatibilityError(ValueError):
    """An approved fact's immutable values violate the database compatibility
    contract for the requested input role.

    Raised *before* any INSERT so the failure is a deterministic, typed
    compatibility error rather than a generic database constraint violation.
    Subclasses ``ValueError`` so existing generic handling still treats it as a
    bad request, but callers should catch it first to map it to its own status.
    ``reason`` is a stable, non-secret code from the calculator contract.
    """

    def __init__(self, input_role: str, reason: str):
        self.input_role = input_role
        self.reason = reason
        super().__init__(f"frozen input incompatible for role {input_role!r}: {reason}")


@dataclass
class FrozenInputRow:
    id: str
    project_id: str
    input_role: str
    candidate_fact_revision_id: str
    approval_decision_id: str
    resolved_numeric_value: Decimal
    resolved_unit: str
    resolved_currency_code: Optional[str]
    resolved_period: Optional[str]
    resolved_time_unit: Optional[str]
    as_of_date: Optional[date]


# ─────────────────────── ROI-eligible candidate fact + context ───────────────────────

def create_eligible_fact(
    conn,
    *,
    project_id: str,
    source_snapshot_id: str,
    fact: ValidatedFact,
    subject_label: str,
    metric_label: str,
    period_basis: Optional[str] = None,
    source_locator: str = "",
    source_char_range: Optional[str] = None,
    extraction_rationale: str = "",
    actor: str = "",
) -> tuple[str, str]:
    """Create a Slice A CandidateFactRevision plus its 1:1 extraction context.

    Returns ``(candidate_fact_revision_id, extraction_context_id)``. Both rows are
    created on the caller's transaction; the context makes the fact ROI-eligible.
    """
    cfr_id = evidence_repo.insert_fact(
        conn, project_id=project_id, source_snapshot_id=source_snapshot_id,
        fact=fact, created_by=actor,
    )
    row = conn.execute(
        """
        INSERT INTO candidate_fact_extraction_context
            (project_id, candidate_fact_revision_id, subject_label, metric_label,
             period_basis, source_locator, source_char_range, extraction_rationale, extracted_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id::text
        """,
        (project_id, cfr_id, subject_label, metric_label, period_basis,
         source_locator, source_char_range, extraction_rationale, actor),
    ).fetchone()
    return cfr_id, row[0]


# ─────────────────────────────── Frozen inputs ───────────────────────────────

def freeze_input(
    conn,
    *,
    project_id: str,
    candidate_fact_revision_id: str,
    input_role: str,
    approval_decision_id: Optional[str] = None,
    frozen_by: str = "",
) -> str:
    """Freeze an approved fact as an ApprovedCalculationInput for one role.

    ``approval_decision_id`` defaults to the fact's current active approve. The
    resolved values are copied from the immutable source fact + context, so the
    database value-copy trigger always passes. Refuses to freeze a fact that is
    not currently approved (no active approve).
    """
    active = approvals.active_approval_id(
        conn, project_id=project_id, candidate_fact_revision_id=candidate_fact_revision_id
    )
    if active is None:
        raise ValueError("fact has no active approval; cannot freeze")
    if approval_decision_id is None:
        approval_decision_id = active
    elif approval_decision_id != active:
        raise ValueError("approval_decision_id is not the fact's active approve")

    fact = conn.execute(
        """
        SELECT numeric_value, unit, currency_code, time_unit, as_of_date
        FROM candidate_fact_revision WHERE id = %s AND project_id = %s
        """,
        (candidate_fact_revision_id, project_id),
    ).fetchone()
    if fact is None:
        raise ValueError("unknown candidate fact for project")
    period = conn.execute(
        """
        SELECT period_basis FROM candidate_fact_extraction_context
        WHERE candidate_fact_revision_id = %s AND project_id = %s
        """,
        (candidate_fact_revision_id, project_id),
    ).fetchone()
    if period is None:
        raise ValueError("fact is not ROI-eligible (no extraction context)")

    numeric_value, unit, currency_code, time_unit, as_of_date = fact
    # Mirror the immutable database value-shape contract before INSERT so an
    # incompatible fact surfaces as a typed compatibility error, not a raw
    # constraint violation. The database constraint stays as defense in depth.
    reason = input_compatibility_reason(
        input_role,
        numeric_value=numeric_value,
        unit=unit,
        currency_code=currency_code,
        time_unit=time_unit,
    )
    if reason is not None:
        raise FrozenInputCompatibilityError(input_role, reason)
    row = conn.execute(
        """
        INSERT INTO approved_calculation_input
            (project_id, input_role, candidate_fact_revision_id, approval_decision_id,
             resolved_numeric_value, resolved_unit, resolved_currency_code,
             resolved_period, resolved_time_unit, as_of_date, frozen_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id::text
        """,
        (project_id, input_role, candidate_fact_revision_id, approval_decision_id,
         numeric_value, unit or "", currency_code, period[0], time_unit, as_of_date, frozen_by),
    ).fetchone()
    return row[0]


def load_frozen_input(conn, *, project_id: str, input_id: str) -> FrozenInputRow:
    row = conn.execute(
        """
        SELECT id::text, project_id::text, input_role, candidate_fact_revision_id::text,
               approval_decision_id::text, resolved_numeric_value, resolved_unit,
               resolved_currency_code, resolved_period, resolved_time_unit, as_of_date
        FROM approved_calculation_input WHERE id = %s AND project_id = %s
        """,
        (input_id, project_id),
    ).fetchone()
    if row is None:
        raise ValueError("unknown approved_calculation_input for project")
    return FrozenInputRow(*row)


def as_resolved_input(frozen: FrozenInputRow) -> ResolvedInput:
    return ResolvedInput(
        input_role=frozen.input_role,
        numeric_value=frozen.resolved_numeric_value,
        unit=frozen.resolved_unit,
        currency_code=frozen.resolved_currency_code,
        period=frozen.resolved_period,
        time_unit=frozen.resolved_time_unit,
        approved_calculation_input_id=frozen.id,
        candidate_fact_revision_id=frozen.candidate_fact_revision_id,
        approval_decision_id=frozen.approval_decision_id,
    )


def input_consumable(conn, *, project_id: str, frozen: FrozenInputRow) -> bool:
    """True iff the frozen input's approve is still active and its evidence available."""
    revoked = conn.execute(
        "SELECT 1 FROM candidate_fact_approval_decision WHERE revokes_decision_id = %s",
        (frozen.approval_decision_id,),
    ).fetchone()
    if revoked is not None:
        return False
    return evidence_repo.fact_available(conn, frozen.candidate_fact_revision_id)


# ─────────────────────────────── Calculation results ───────────────────────────────

def insert_calculation_result(
    conn, *, project_id: str, computation: RoiComputation, computed_by: str = ""
) -> str:
    import json

    row = conn.execute(
        """
        INSERT INTO calculation_result
            (project_id, formula_version, status, currency_code, annual_labor_savings,
             annual_net_benefit, first_year_net_benefit, first_year_roi_percent,
             roi_percent_status, formula_input_digest, provenance_fingerprint,
             diagnostics, computed_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
        RETURNING id::text
        """,
        (project_id, computation.formula_version, computation.status, computation.currency_code,
         computation.annual_labor_savings, computation.annual_net_benefit,
         computation.first_year_net_benefit, computation.first_year_roi_percent,
         computation.roi_percent_status, computation.formula_input_digest,
         computation.provenance_fingerprint, json.dumps(computation.diagnostics), computed_by),
    ).fetchone()
    return row[0]


def link_result_input(
    conn, *, project_id: str, calculation_result_id: str,
    approved_calculation_input_id: str, input_role: str,
) -> str:
    row = conn.execute(
        """
        INSERT INTO calculation_result_input
            (project_id, calculation_result_id, approved_calculation_input_id, input_role)
        VALUES (%s, %s, %s, %s)
        RETURNING id::text
        """,
        (project_id, calculation_result_id, approved_calculation_input_id, input_role),
    ).fetchone()
    return row[0]


def get_result(conn, *, project_id: str, result_id: str) -> Optional[dict]:
    row = conn.execute(
        """
        SELECT id::text, status, formula_version, currency_code, annual_labor_savings,
               annual_net_benefit, first_year_net_benefit, first_year_roi_percent,
               roi_percent_status, formula_input_digest, provenance_fingerprint
        FROM calculation_result WHERE id = %s AND project_id = %s
        """,
        (result_id, project_id),
    ).fetchone()
    if row is None:
        return None
    keys = ("id", "status", "formula_version", "currency_code", "annual_labor_savings",
            "annual_net_benefit", "first_year_net_benefit", "first_year_roi_percent",
            "roi_percent_status", "formula_input_digest", "provenance_fingerprint")
    return dict(zip(keys, row))


def list_result_input_ids(conn, *, project_id: str, result_id: str) -> dict[str, str]:
    rows = conn.execute(
        """
        SELECT input_role, approved_calculation_input_id::text
        FROM calculation_result_input WHERE calculation_result_id = %s AND project_id = %s
        """,
        (result_id, project_id),
    ).fetchall()
    return {r[0]: r[1] for r in rows}


# ─────────────────────── Calculation requests (idempotency, v49) ───────────────────────
# calculation_request is the only mutable Slice B table: a pending reservation is
# claimed conflict-free, then transitioned pending -> committed once the single
# result it owns is persisted. The two unique identities live on the table:
#   request identity    UNIQUE (project_id, idempotency_key)
#   operation identity  UNIQUE (project_id, canonical_request_digest)

def claim_request(
    conn,
    *,
    project_id: str,
    idempotency_key: str,
    formula_version: str,
    canonical_request_digest: str,
    requested_by: str = "",
) -> Optional[str]:
    """Insert a pending request reservation.

    Returns the new request id when this caller wins the claim, or ``None`` when a
    conflicting row already exists (same idempotency key or same canonical digest).
    Uses ``ON CONFLICT DO NOTHING`` so a conflict never aborts the transaction; a
    concurrent in-flight claim blocks this INSERT until that transaction commits or
    rolls back, after which the conflicting row (if any) is committed-visible.
    """
    row = conn.execute(
        """
        INSERT INTO calculation_request
            (project_id, formula_version, idempotency_key, canonical_request_digest,
             requested_by, status)
        VALUES (%s, %s, %s, %s, %s, 'pending')
        ON CONFLICT DO NOTHING
        RETURNING id::text
        """,
        (project_id, formula_version, idempotency_key, canonical_request_digest, requested_by),
    ).fetchone()
    return row[0] if row else None


def commit_request(conn, *, project_id: str, request_id: str, result_id: str) -> None:
    """Transition a claimed pending request to committed, linking its single result.

    The database controlled-transition guard permits only this exact change.
    """
    conn.execute(
        """
        UPDATE calculation_request
        SET status = 'committed',
            result_calculation_result_id = %s,
            committed_at = NOW()
        WHERE id = %s AND project_id = %s
        """,
        (result_id, request_id, project_id),
    )


def get_request_by_key(conn, *, project_id: str, idempotency_key: str) -> Optional[dict]:
    row = conn.execute(
        """
        SELECT id::text, canonical_request_digest, status, result_calculation_result_id::text
        FROM calculation_request WHERE project_id = %s AND idempotency_key = %s
        """,
        (project_id, idempotency_key),
    ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "canonical_request_digest": row[1], "status": row[2], "result_id": row[3]}


def get_request_by_digest(conn, *, project_id: str, canonical_request_digest: str) -> Optional[dict]:
    row = conn.execute(
        """
        SELECT id::text, idempotency_key, status, result_calculation_result_id::text
        FROM calculation_request WHERE project_id = %s AND canonical_request_digest = %s
        """,
        (project_id, canonical_request_digest),
    ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "idempotency_key": row[1], "status": row[2], "result_id": row[3]}
