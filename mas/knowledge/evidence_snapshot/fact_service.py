"""Bounded production service for creating one canonical CandidateFactRevision.

Discovery for R2.0A-4B found no existing *validated public production service*
that creates a bare ``candidate_fact_revision``:

* ``knowledge.evidence_snapshot.repository.insert_fact`` is a low-level append
  seam (its own docstring calls it a "narrow insert/read repository ... used by
  tests and deliberate typed callers"); it performs no feature gating, no
  transaction-ownership guard, and no same-project snapshot binding.
* ``knowledge.automation_roi.repository.create_eligible_fact`` reuses that seam
  but couples every fact to a Slice-B ``candidate_fact_extraction_context`` row,
  which is Automation-ROI-specific storage and out of scope here.
* ``research_evidence.service.create_fact_metadata_revision`` only attaches the
  R1.1 metadata sidecar to an *already existing* fact id.

This module is therefore the smallest bounded wrapper around the canonical v47
validation (:mod:`knowledge.evidence_snapshot.validation`) and the canonical v47
fact insert seam (:func:`knowledge.evidence_snapshot.repository.insert_fact`).
It:

* is inert unless ``MAS_EVIDENCE_SNAPSHOT_ENABLED`` is set (it touches v47 facts);
* accepts a :class:`ValidatedFact` but **never trusts it on type alone**:
  :class:`ValidatedFact` is a plain dataclass that any caller can construct
  directly, bypassing :func:`validate_fact`. The service therefore reconstructs
  a canonical fact from every field of the supplied value through the canonical
  :func:`validate_fact` and persists only that canonical result — a forged or
  internally inconsistent ``ValidatedFact`` is rejected *before any SQL runs*;
* binds the fact to an existing *same-project* ``source_snapshot``;
* preserves caller transaction ownership (it never commits, rejects an
  autocommit connection, and rejects any transaction isolation other than
  READ COMMITTED) and wraps its work in a savepoint so a failure rolls back only
  this fact;
* adds no new storage, creates no authorization, and never infers a fact from
  Knowledge automatically.
"""
from __future__ import annotations

from contextlib import contextmanager

import config

from . import repository as repo
from .validation import ValidatedFact, validate_fact


class CandidateFactServiceDisabled(RuntimeError):
    """Raised when a fact write is attempted while Slice A capture is disabled."""


class CandidateFactServiceTransactionError(RuntimeError):
    """Raised when caller-owned atomicity cannot be preserved."""


class CandidateFactSourceSnapshotNotFound(ValueError):
    """The bound source snapshot does not exist for the given project."""


def _require_enabled() -> None:
    if not config.evidence_snapshot_enabled():
        raise CandidateFactServiceDisabled(
            "Evidence snapshot capture is disabled "
            "(set MAS_EVIDENCE_SNAPSHOT_ENABLED to enable it)"
        )


def _require_caller_owned_read_committed(conn) -> None:
    """Reject autocommit and require an EXPLICITLY pinned READ COMMITTED level.

    Uses only the driver's Python-side connection attributes so this guard runs
    *before* any SQL or SAVEPOINT is issued. ``isolation_level is None`` merely
    delegates to the server/session default and does **not** prove the
    transaction is READ COMMITTED, so it is rejected; the caller must pin
    ``psycopg.IsolationLevel.READ_COMMITTED`` explicitly. REPEATABLE READ and
    SERIALIZABLE are rejected.
    """
    if conn.autocommit:
        raise CandidateFactServiceTransactionError(
            "candidate-fact writes require a non-autocommit connection"
        )
    isolation = getattr(conn, "isolation_level", None)
    if isolation is None:
        raise CandidateFactServiceTransactionError(
            "candidate-fact writes require an explicitly pinned READ COMMITTED "
            "isolation level (isolation_level=None delegates to the server "
            "default and does not prove READ COMMITTED)"
        )
    name = getattr(isolation, "name", str(isolation)).upper()
    if name != "READ_COMMITTED":
        raise CandidateFactServiceTransactionError(
            "candidate-fact writes require READ COMMITTED isolation "
            f"(got {name})"
        )


def _canonicalize(fact: ValidatedFact) -> ValidatedFact:
    """Revalidate a supplied ``ValidatedFact`` field-by-field via ``validate_fact``.

    ``ValidatedFact`` is directly constructible and does not enforce the
    :func:`validate_fact` rules, so a caller can hand us a forged or internally
    inconsistent value. Reconstructing the canonical fact from every field
    re-applies the full typed contract (currency/date, rate contexts, percentage
    subtype bounds, duration unit/sign, count integrality/entity, non-empty
    text) and returns a clean value; a violation raises ``FactValidationError``
    before the caller reaches any SQL or SAVEPOINT.
    """
    return validate_fact(
        fact.fact_type,
        value=fact.numeric_value,
        text=fact.text_value,
        unit=fact.unit,
        currency_code=fact.currency_code,
        as_of_date=fact.as_of_date,
        numerator_context=fact.numerator_context,
        denominator_context=fact.denominator_context,
        percentage_basis=fact.percentage_basis,
        percentage_subtype=fact.percentage_subtype,
        time_unit=fact.time_unit,
        counted_entity=fact.counted_entity,
    )


@contextmanager
def _fact_write(conn):
    conn.execute("SAVEPOINT evidence_snapshot_fact_write")
    try:
        yield
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT evidence_snapshot_fact_write")
        conn.execute("RELEASE SAVEPOINT evidence_snapshot_fact_write")
        raise
    else:
        conn.execute("RELEASE SAVEPOINT evidence_snapshot_fact_write")


def _source_snapshot_exists(conn, *, project_id: str, source_snapshot_id: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM source_snapshot
        WHERE id = %s AND project_id = %s
        """,
        (source_snapshot_id, project_id),
    ).fetchone()
    return row is not None


def create_candidate_fact_revision(
    conn,
    *,
    project_id: str,
    source_snapshot_id: str,
    fact: ValidatedFact,
    created_by: str = "",
) -> str:
    """Create one validated, source-bound ``candidate_fact_revision``.

    Returns the new fact id on the caller's transaction. The caller owns the
    connection lifecycle and the COMMIT/ROLLBACK decision; this service never
    commits.

    Every rejection below (bad type, disabled feature, autocommit/isolation,
    missing snapshot id, invalid fact value) happens *before* any SQL or
    SAVEPOINT is issued; only a validated, canonical fact is ever persisted.
    """
    if not isinstance(fact, ValidatedFact):
        raise TypeError(
            "create_candidate_fact_revision requires a validated ValidatedFact"
        )
    _require_enabled()
    _require_caller_owned_read_committed(conn)
    if not source_snapshot_id:
        raise CandidateFactSourceSnapshotNotFound(
            "candidate fact requires an existing source_snapshot_id"
        )
    # Re-derive a canonical fact from the supplied value and persist ONLY that.
    # A forged/invalid ValidatedFact raises here, before any SQL runs.
    canonical = _canonicalize(fact)
    with _fact_write(conn):
        if not _source_snapshot_exists(
            conn, project_id=project_id, source_snapshot_id=source_snapshot_id
        ):
            raise CandidateFactSourceSnapshotNotFound(
                "source snapshot does not exist for this project"
            )
        return repo.insert_fact(
            conn,
            project_id=project_id,
            source_snapshot_id=source_snapshot_id,
            fact=canonical,
            created_by=created_by,
        )
