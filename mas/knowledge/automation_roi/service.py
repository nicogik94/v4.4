"""Slice B lifecycle orchestration (pre-API).

Ties the deterministic engine to the append-only repository: resolve an explicit
six-input request, determine availability, compute, and persist the result with
its role-keyed input links. No HTTP, no projections (PR2).
"""
from __future__ import annotations

from typing import Mapping

from . import repository as repo
from .calculator import ROLES, compute_automation_roi


class CalculationRequestError(ValueError):
    """The six-input request is malformed (→ HTTP 422 at the API boundary in PR2)."""


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
    """
    keys = set(inputs_by_role)
    if keys != set(ROLES):
        missing = sorted(set(ROLES) - keys)
        extra = sorted(keys - set(ROLES))
        raise CalculationRequestError(
            f"exactly the six roles are required; missing={missing} extra={extra}"
        )

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
