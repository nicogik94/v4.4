"""Slice B operator-workspace read model — navigation data for the dashboard.

A single read-only projection that lets an operator drive the existing Automation
ROI lifecycle (snapshot → candidate fact → decision → frozen input → calculation
→ result) without exposing anything unsafe. It performs *no* writes, runs *no*
calculation, and never reimplements the client-safe filtering owned by
:mod:`knowledge.automation_roi.projections`.

Operator-safe boundary (what this module deliberately omits):

* never returns ``storage_ref``, filesystem paths, raw source content/excerpts,
  internal locator objects, or raw driver payloads;
* never returns actor identity (``captured_by`` / ``extracted_by`` / ``decided_by``
  / ``computed_by`` / ``frozen_by``) or decision sequence numbers;
* opaque identifiers are returned only as values the caller keeps in memory to
  drive the existing write contracts — they are not display text.

It *does* surface the bounded, human-readable, operator-only source pointers that
the current API already supports (``source_locator`` / ``source_char_range``) so
an operator can confirm provenance; the client-safe projection never sees these.

Decision state and permitted actions are derived server-side (via PR1's
``approvals`` helpers); availability is resolved (never stored) via the Slice A
resolver. Identity of the six required roles is owned by the engine's ``ROLES``.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable, Optional

from knowledge.evidence_snapshot import repository as evidence_repo

from . import approvals, repository as repo
from .calculator import ROLES

WORKSPACE_SCHEMA_VERSION = "automation_roi.workspace.v1"
CALCULATION_KIND = "automation_roi"


def _dec_str(value: Optional[Decimal]) -> Optional[str]:
    """Full-precision, JSON-safe string for a Decimal (never float)."""
    return None if value is None else str(value)


def _iso(value) -> Optional[str]:
    return value.isoformat() if value is not None else None


# ─────────────────────────── exact-six role readiness ───────────────────────────

def classify_role_completeness(roles: Iterable[str]) -> dict[str, Any]:
    """Classify a collection of role keys against the exact-six contract.

    Pure helper (no I/O). Reports the four mutually-illuminating conditions the
    operator workspace must distinguish before a calculation can be submitted:

    * ``complete``  — exactly the six roles, once each, nothing else;
    * ``missing``   — required roles with no entry;
    * ``duplicate`` — required roles supplied more than once;
    * ``extra``     — keys that are not one of the six required roles.
    """
    counts: dict[str, int] = {}
    for role in roles:
        counts[role] = counts.get(role, 0) + 1
    present = set(counts)
    required = set(ROLES)
    missing = sorted(required - present)
    extra = sorted(present - required)
    duplicate = sorted(r for r, c in counts.items() if c > 1 and r in required)
    return {
        "complete": not missing and not extra and not duplicate,
        "missing": missing,
        "duplicate": duplicate,
        "extra": extra,
    }


# ─────────────────────────────── read-only loader ───────────────────────────────

def load_workspace(conn, *, project_id: str) -> dict[str, Any]:
    """Build the operator-safe navigation payload for one project. Read-only."""
    snapshots = _load_snapshots(conn, project_id)
    candidate_facts = _load_candidate_facts(conn, project_id)
    frozen_inputs = _load_frozen_inputs(conn, project_id)
    results = _load_results(conn, project_id)

    roles_available = [
        role
        for role in ROLES
        if any(fi["input_role"] == role and fi["consumable"] for fi in frozen_inputs)
    ]
    readiness = classify_role_completeness(roles_available)
    per_role = {
        role: {
            "frozen_input_count": sum(1 for fi in frozen_inputs if fi["input_role"] == role),
            "consumable_count": sum(
                1 for fi in frozen_inputs if fi["input_role"] == role and fi["consumable"]
            ),
        }
        for role in ROLES
    }

    return {
        "schema_version": WORKSPACE_SCHEMA_VERSION,
        "calculation_kind": CALCULATION_KIND,
        "project_id": project_id,
        "roles": list(ROLES),
        "snapshots": snapshots,
        "candidate_facts": candidate_facts,
        "frozen_inputs": frozen_inputs,
        "role_readiness": {
            "complete": readiness["complete"],
            "missing_roles": readiness["missing"],
            "roles": per_role,
        },
        "results": results,
    }


def _load_snapshots(conn, project_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id::text, source_kind, captured_at
        FROM source_snapshot
        WHERE project_id = %s
        ORDER BY captured_at, id
        """,
        (project_id,),
    ).fetchall()
    out = []
    for r in rows:
        out.append({
            "source_snapshot_id": r[0],
            # source_kind is a safe category label (e.g. "pdf"); storage_ref and
            # source_locator (a capture path) are deliberately never selected.
            "source_kind": r[1] or None,
            "captured_at": _iso(r[2]),
            "available": evidence_repo.snapshot_available(conn, r[0]),
        })
    return out


def _load_candidate_facts(conn, project_id: str) -> list[dict[str, Any]]:
    # ROI-eligible facts only: those with a 1:1 extraction context.
    rows = conn.execute(
        """
        SELECT cfr.id::text, cfr.source_snapshot_id::text, cfr.fact_type,
               cfr.numeric_value, cfr.text_value, cfr.unit, cfr.currency_code,
               cfr.as_of_date, cfr.time_unit,
               ctx.subject_label, ctx.metric_label, ctx.period_basis,
               ctx.source_locator, ctx.source_char_range, ctx.extraction_rationale
        FROM candidate_fact_revision cfr
        JOIN candidate_fact_extraction_context ctx
          ON ctx.candidate_fact_revision_id = cfr.id AND ctx.project_id = cfr.project_id
        WHERE cfr.project_id = %s
        ORDER BY cfr.created_at, cfr.id
        """,
        (project_id,),
    ).fetchall()

    out = []
    for r in rows:
        fact_id = r[0]
        state = approvals.effective_status(
            conn, project_id=project_id, candidate_fact_revision_id=fact_id
        )
        active = approvals.active_approval_id(
            conn, project_id=project_id, candidate_fact_revision_id=fact_id
        )
        # Permitted actions are derived from server state — never inferred client-side.
        permitted = ["reject", "withdraw"] if active is not None else ["approve"]
        out.append({
            "candidate_fact_revision_id": fact_id,
            "source_snapshot_id": r[1],
            "fact_type": r[2],
            "numeric_value": _dec_str(r[3]),
            "text_value": r[4],
            "unit": r[5] or None,
            "currency_code": r[6],
            "as_of_date": _iso(r[7]),
            "time_unit": r[8],
            "subject_label": r[9],
            "metric_label": r[10],
            "period_basis": r[11],
            # Bounded, operator-only source pointers (never reach the client view).
            "source_locator": r[12] or None,
            "source_char_range": r[13],
            "extraction_rationale": r[14] or None,
            "available": evidence_repo.fact_available(conn, fact_id),
            "decision_state": state,
            "active_approval_id": active,
            "permitted_actions": permitted,
        })
    return out


def _load_frozen_inputs(conn, project_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id::text, input_role
        FROM approved_calculation_input
        WHERE project_id = %s
        ORDER BY input_role, id
        """,
        (project_id,),
    ).fetchall()

    out = []
    for r in rows:
        # Reuse PR1's frozen-input load + consumability contract verbatim.
        frozen = repo.load_frozen_input(conn, project_id=project_id, input_id=r[0])
        consumable = repo.input_consumable(conn, project_id=project_id, frozen=frozen)
        out.append({
            "approved_calculation_input_id": frozen.id,
            "input_role": frozen.input_role,
            "candidate_fact_revision_id": frozen.candidate_fact_revision_id,
            "approval_decision_id": frozen.approval_decision_id,
            "resolved_numeric_value": _dec_str(frozen.resolved_numeric_value),
            "resolved_unit": frozen.resolved_unit or None,
            "resolved_currency_code": frozen.resolved_currency_code,
            "resolved_period": frozen.resolved_period,
            "resolved_time_unit": frozen.resolved_time_unit,
            "available": evidence_repo.fact_available(conn, frozen.candidate_fact_revision_id),
            "consumable": consumable,
        })
    return out


def _load_results(conn, project_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id::text, status, formula_version, currency_code, computed_at
        FROM calculation_result
        WHERE project_id = %s
        ORDER BY computed_at DESC, id
        """,
        (project_id,),
    ).fetchall()
    return [
        {
            "result_id": r[0],
            "status": r[1],
            "formula_version": r[2],
            "currency_code": r[3],
            "computed_at": _iso(r[4]),
        }
        for r in rows
    ]
