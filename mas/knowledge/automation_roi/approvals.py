"""Deterministic, append-only approval chain for Slice B.

Approval state is never stored mutably: every verdict is an append-only
``candidate_fact_approval_decision`` row ordered by a per-fact integer
``decision_seq`` (never by timestamp). MVP decision types are ``approve``,
``reject``, ``withdraw``. A ``reject``/``withdraw`` must name the exact ``approve``
of the same fact it revokes; an ``approve`` is *active* iff no row revokes it.

All functions take a caller-supplied synchronous ``psycopg`` connection; the
caller owns the transaction and the feature flag.
"""
from __future__ import annotations

from typing import Optional

REVOCATION_TYPES = ("reject", "withdraw")


def next_decision_seq(conn, *, project_id: str, candidate_fact_revision_id: str) -> int:
    row = conn.execute(
        """
        SELECT COALESCE(MAX(decision_seq), 0) + 1
        FROM candidate_fact_approval_decision
        WHERE candidate_fact_revision_id = %s AND project_id = %s
        """,
        (candidate_fact_revision_id, project_id),
    ).fetchone()
    return int(row[0])


def append_decision(
    conn,
    *,
    project_id: str,
    candidate_fact_revision_id: str,
    decision_type: str,
    revokes_decision_id: Optional[str] = None,
    reason: str = "",
    actor: str = "",
) -> str:
    """Append one decision to a fact's chain and return its id.

    ``approve`` must not name a revoked decision; ``reject``/``withdraw`` must name
    the active ``approve`` they revoke. The database enforces the shape, the
    same-fact linkage, and revoke-once.
    """
    if decision_type == "approve" and revokes_decision_id is not None:
        raise ValueError("approve decisions do not revoke another decision")
    if decision_type in REVOCATION_TYPES and revokes_decision_id is None:
        raise ValueError(f"{decision_type} must reference the approve decision it revokes")

    revoked_decision_type = "approve" if revokes_decision_id is not None else None
    seq = next_decision_seq(
        conn, project_id=project_id, candidate_fact_revision_id=candidate_fact_revision_id
    )
    row = conn.execute(
        """
        INSERT INTO candidate_fact_approval_decision
            (project_id, candidate_fact_revision_id, decision_type, decision_seq,
             revokes_decision_id, revoked_decision_type, decision_reason, decided_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id::text
        """,
        (project_id, candidate_fact_revision_id, decision_type, seq,
         revokes_decision_id, revoked_decision_type, reason, actor),
    ).fetchone()
    return row[0]


def active_approval_id(conn, *, project_id: str, candidate_fact_revision_id: str) -> Optional[str]:
    """Return the active approve decision id for a fact, or None.

    An approve is active iff no decision revokes it. The highest-``decision_seq``
    active approve is returned deterministically.
    """
    row = conn.execute(
        """
        SELECT d.id::text
        FROM candidate_fact_approval_decision d
        WHERE d.candidate_fact_revision_id = %s AND d.project_id = %s
          AND d.decision_type = 'approve'
          AND NOT EXISTS (
              SELECT 1 FROM candidate_fact_approval_decision r
              WHERE r.revokes_decision_id = d.id)
        ORDER BY d.decision_seq DESC
        LIMIT 1
        """,
        (candidate_fact_revision_id, project_id),
    ).fetchone()
    return row[0] if row else None


def effective_status(conn, *, project_id: str, candidate_fact_revision_id: str) -> str:
    """'approved' if an active approve exists, else 'revoked' if any decision exists, else 'none'."""
    if active_approval_id(
        conn, project_id=project_id, candidate_fact_revision_id=candidate_fact_revision_id
    ):
        return "approved"
    row = conn.execute(
        """
        SELECT 1 FROM candidate_fact_approval_decision
        WHERE candidate_fact_revision_id = %s AND project_id = %s LIMIT 1
        """,
        (candidate_fact_revision_id, project_id),
    ).fetchone()
    return "revoked" if row else "none"
