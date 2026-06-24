"""Slice B lifecycle orchestration (pre-API).

Ties the deterministic engine to the append-only repository: resolve an explicit
six-input request, determine availability, compute, and persist the result with
its role-keyed input links. No HTTP, no projections (PR2).

``request_calculation`` (v49) wraps ``compute_and_persist`` with dual-identity
idempotency so retries and concurrent submissions converge on exactly one result.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from . import repository as repo
from .calculator import FORMULA_VERSION, ROLES, canonical_request_digest, compute_automation_roi


class CalculationRequestError(ValueError):
    """The six-input request is malformed (→ HTTP 422 at the API boundary)."""


class RequestKeyConflict(Exception):
    """The idempotency key was already used for a *different* calculation operation
    (same key, different canonical digest) → HTTP 409."""


@dataclass
class CalculationOutcome:
    """Result of an idempotent calculate request.

    ``replayed`` is ``False`` when this request computed and persisted the result,
    ``True`` when it returned an existing result (same key+digest, or a different
    key resolving to the same canonical operation).
    """

    result_id: str
    replayed: bool


def compute_and_persist(
    conn,
    *,
    project_id: str,
    inputs_by_role: Mapping[str, str],
    computed_by: str = "",
) -> str:
    """Compute and persist one CalculationResult from an explicit role→input map.

    ``inputs_by_role`` must contain exactly the six required roles. Shape errors
    (missing/duplicate/extra/wrong-role) raise ``CalculationRequestError`` and
    persist nothing. Otherwise a result is persisted with status valid /
    not_applicable / blocked, plus exactly six role-keyed input links.

    This is the unguarded compute+store path (no idempotency). Callers that need
    replay/concurrency safety use ``request_calculation``.
    """
    _require_six_roles(inputs_by_role)
    return _compute_and_store(
        conn, project_id=project_id, inputs_by_role=inputs_by_role, computed_by=computed_by
    )


def request_calculation(
    conn,
    *,
    project_id: str,
    inputs_by_role: Mapping[str, str],
    idempotency_key: str,
    computed_by: str = "",
) -> CalculationOutcome:
    """Idempotent calculate: compute exactly once per canonical operation.

    Behavior (per W0):
      * same key + same digest      → replay the existing result;
      * same key + different digest → ``RequestKeyConflict`` (HTTP 409);
      * different key + same digest → replay the existing result;
      * different key + different digest → compute a new result.

    The caller owns the transaction. A claim is taken (``ON CONFLICT DO NOTHING``)
    *before* compute, so the exact-operation unique guarantees at most one result
    per (project, digest). A shape/compatibility failure raises and the caller's
    rollback removes the pending reservation completely.
    """
    _require_six_roles(inputs_by_role)
    digest = canonical_request_digest(project_id, inputs_by_role)

    request_id = repo.claim_request(
        conn,
        project_id=project_id,
        idempotency_key=idempotency_key,
        formula_version=FORMULA_VERSION,
        canonical_request_digest=digest,
        requested_by=computed_by,
    )

    if request_id is None:
        # The claim conflicted. Resolve request identity first, then operation
        # identity. A no-row claim means the conflicting row's owning transaction
        # has already committed, so a matching row is committed-visible here.
        by_key = repo.get_request_by_key(
            conn, project_id=project_id, idempotency_key=idempotency_key
        )
        if by_key is not None:
            if by_key["canonical_request_digest"] != digest:
                raise RequestKeyConflict(
                    "idempotency key already used for a different calculation"
                )
            return _replay(by_key)
        by_digest = repo.get_request_by_digest(
            conn, project_id=project_id, canonical_request_digest=digest
        )
        if by_digest is not None:
            return _replay(by_digest)
        # Neither identity present though the claim conflicted: an in-doubt state
        # that the atomic claim→commit design does not produce. Refuse rather than
        # fabricate a result.
        raise RuntimeError("calculation_request conflict could not be resolved")

    # This request owns the operation: compute, persist, and commit the request.
    result_id = _compute_and_store(
        conn, project_id=project_id, inputs_by_role=inputs_by_role, computed_by=computed_by
    )
    repo.commit_request(conn, project_id=project_id, request_id=request_id, result_id=result_id)
    return CalculationOutcome(result_id=result_id, replayed=False)


# ─────────────────────────────── internals ───────────────────────────────

def _require_six_roles(inputs_by_role: Mapping[str, str]) -> None:
    keys = set(inputs_by_role)
    if keys != set(ROLES):
        missing = sorted(set(ROLES) - keys)
        extra = sorted(keys - set(ROLES))
        raise CalculationRequestError(
            f"exactly the six roles are required; missing={missing} extra={extra}"
        )


def _replay(row: dict) -> CalculationOutcome:
    if row["result_id"] is None:
        # A matching reservation without a committed result is not replayable; the
        # atomic claim→commit design does not produce this, so surface a conflict.
        raise RequestKeyConflict("a matching calculation request is still in progress")
    return CalculationOutcome(result_id=row["result_id"], replayed=True)


def _compute_and_store(
    conn, *, project_id: str, inputs_by_role: Mapping[str, str], computed_by: str
) -> str:
    resolved = {}
    unavailable: list[str] = []
    for role in ROLES:
        frozen = repo.load_frozen_input(conn, project_id=project_id, input_id=inputs_by_role[role])
        if frozen.input_role != role:
            # wrong-role input for this key → shape error, persist nothing
            raise CalculationRequestError(
                f"input {inputs_by_role[role]!r} for role {role!r} is actually {frozen.input_role!r}"
            )
        if not repo.input_consumable(conn, project_id=project_id, frozen=frozen):
            unavailable.append(role)
        resolved[role] = repo.as_resolved_input(frozen)

    computation = compute_automation_roi(resolved, unavailable_roles=tuple(unavailable))

    result_id = repo.insert_calculation_result(
        conn, project_id=project_id, computation=computation, computed_by=computed_by
    )
    for role in ROLES:
        repo.link_result_input(
            conn, project_id=project_id, calculation_result_id=result_id,
            approved_calculation_input_id=inputs_by_role[role], input_role=role,
        )
    return result_id
