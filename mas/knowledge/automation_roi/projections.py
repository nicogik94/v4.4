"""Slice B read projections (PR2) — operator audit view and client-safe export view.

Two projections are built from one immutable ``CalculationResult`` bundle:

* :func:`operator_projection` — the full audit view. May expose approved inputs,
  approval-decision metadata, availability state, formula version, provenance
  fingerprints, diagnostics, and internal identifiers needed for audit.
* :func:`client_projection` — an allowlist-only, export-ready view. It includes
  only approved result values, display-ready units/currency, approved assumptions
  and caveats, and safe human-readable source labels *only when the source remains
  available*. It never emits storage references, file paths, raw source
  locators/ranges, raw excerpts, internal UUIDs, actor identity, decision
  metadata, diagnostics, or any unavailable-source content/citation.

The build functions are pure (dict in → dict out) so the allowlist can be tested
without a database; :func:`load_result_bundle` performs the read-only queries.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from knowledge.evidence_snapshot import repository as evidence_repo

from .calculator import ROLES

# Projection schema versions are independent of the engine FORMULA_VERSION: they
# version the *shape* of these read payloads, not the calculation.
OPERATOR_PROJECTION_SCHEMA_VERSION = "automation_roi.operator.v1"
CLIENT_PROJECTION_SCHEMA_VERSION = "automation_roi.client.v1"

CALCULATION_KIND = "automation_roi"

# Diagnostic keys that map to a fixed, source-free, client-safe caveat sentence.
# Only keys on this allowlist ever reach the client view; raw diagnostics never do.
_CLIENT_SAFE_CAVEATS: dict[str, str] = {
    "negative_hours_delta": (
        "Post-automation hours exceed the baseline, so modeled labor savings are negative."
    ),
}

_BLOCKED_CLIENT_CAVEAT = (
    "This result cannot be shown because required source evidence is unavailable "
    "or the approved inputs are not comparable."
)
_NOT_APPLICABLE_CLIENT_CAVEAT = (
    "Return-on-investment percentage is not applicable because the one-time "
    "implementation cost is zero."
)

_MONEY_QUANT = Decimal("0.01")
_PERCENT_QUANT = Decimal("0.01")


# ─────────────────────────────── helpers ───────────────────────────────

def _dec_str(value: Optional[Decimal]) -> Optional[str]:
    """Full-precision, JSON-safe string for a Decimal (never float)."""
    return None if value is None else str(value)


def _money(value: Optional[Decimal]) -> Optional[str]:
    if value is None:
        return None
    return str(Decimal(value).quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP))


def _percent(value: Optional[Decimal]) -> Optional[str]:
    if value is None:
        return None
    return str(Decimal(value).quantize(_PERCENT_QUANT, rounding=ROUND_HALF_UP))


# ─────────────────────────────── loader ───────────────────────────────

def load_result_bundle(conn, *, project_id: str, result_id: str) -> Optional[dict[str, Any]]:
    """Load one result plus its six linked inputs, contexts, and decisions.

    Read-only. Returns ``None`` when the result does not exist for the project.
    Availability is resolved (never stored) via the Slice A resolver.
    """
    row = conn.execute(
        """
        SELECT id::text, status, formula_version, currency_code,
               annual_labor_savings, annual_net_benefit, first_year_net_benefit,
               first_year_roi_percent, roi_percent_status, formula_input_digest,
               provenance_fingerprint, diagnostics, computed_by, computed_at
        FROM calculation_result
        WHERE id = %s AND project_id = %s
        """,
        (result_id, project_id),
    ).fetchone()
    if row is None:
        return None
    result = {
        "id": row[0], "status": row[1], "formula_version": row[2], "currency_code": row[3],
        "annual_labor_savings": row[4], "annual_net_benefit": row[5],
        "first_year_net_benefit": row[6], "first_year_roi_percent": row[7],
        "roi_percent_status": row[8], "formula_input_digest": row[9],
        "provenance_fingerprint": row[10], "diagnostics": row[11] or {},
        "computed_by": row[12], "computed_at": row[13],
    }

    input_rows = conn.execute(
        """
        SELECT cri.input_role,
               aci.id::text, aci.candidate_fact_revision_id::text, aci.approval_decision_id::text,
               aci.resolved_numeric_value, aci.resolved_unit, aci.resolved_currency_code,
               aci.resolved_period, aci.resolved_time_unit, aci.as_of_date,
               ctx.subject_label, ctx.metric_label, ctx.period_basis,
               ctx.source_locator, ctx.source_char_range, ctx.extraction_rationale, ctx.extracted_by,
               d.decision_type, d.decided_by, d.decided_at, d.decision_reason, d.decision_seq
        FROM calculation_result_input cri
        JOIN approved_calculation_input aci
          ON aci.id = cri.approved_calculation_input_id AND aci.project_id = cri.project_id
        JOIN candidate_fact_extraction_context ctx
          ON ctx.candidate_fact_revision_id = aci.candidate_fact_revision_id
         AND ctx.project_id = aci.project_id
        JOIN candidate_fact_approval_decision d
          ON d.id = aci.approval_decision_id AND d.project_id = aci.project_id
        WHERE cri.calculation_result_id = %s AND cri.project_id = %s
        ORDER BY cri.input_role
        """,
        (result_id, project_id),
    ).fetchall()

    inputs: list[dict[str, Any]] = []
    for r in input_rows:
        fact_id = r[2]
        inputs.append({
            "input_role": r[0],
            "approved_calculation_input_id": r[1],
            "candidate_fact_revision_id": fact_id,
            "approval_decision_id": r[3],
            "resolved_numeric_value": r[4],
            "resolved_unit": r[5],
            "resolved_currency_code": r[6],
            "resolved_period": r[7],
            "resolved_time_unit": r[8],
            "as_of_date": r[9],
            "subject_label": r[10],
            "metric_label": r[11],
            "period_basis": r[12],
            "source_locator": r[13],
            "source_char_range": r[14],
            "extraction_rationale": r[15],
            "extracted_by": r[16],
            "decision_type": r[17],
            "decided_by": r[18],
            "decided_at": r[19],
            "decision_reason": r[20],
            "decision_seq": r[21],
            "available": evidence_repo.fact_available(conn, fact_id),
        })

    return {"project_id": project_id, "result": result, "inputs": inputs}


# ─────────────────────────────── operator projection ───────────────────────────────

def operator_projection(bundle: dict[str, Any]) -> dict[str, Any]:
    """Full audit projection — exposes provenance, availability, and identifiers."""
    res = bundle["result"]
    out_inputs = []
    for it in bundle["inputs"]:
        out_inputs.append({
            "input_role": it["input_role"],
            "approved_calculation_input_id": it["approved_calculation_input_id"],
            "candidate_fact_revision_id": it["candidate_fact_revision_id"],
            "approval_decision_id": it["approval_decision_id"],
            "resolved_numeric_value": _dec_str(it["resolved_numeric_value"]),
            "resolved_unit": it["resolved_unit"],
            "resolved_currency_code": it["resolved_currency_code"],
            "resolved_period": it["resolved_period"],
            "resolved_time_unit": it["resolved_time_unit"],
            "as_of_date": it["as_of_date"].isoformat() if it["as_of_date"] else None,
            "subject_label": it["subject_label"],
            "metric_label": it["metric_label"],
            "period_basis": it["period_basis"],
            "source_locator": it["source_locator"],
            "source_char_range": it["source_char_range"],
            "extraction_rationale": it["extraction_rationale"],
            "extracted_by": it["extracted_by"],
            "available": it["available"],
            "approval_decision": {
                "decision_type": it["decision_type"],
                "decision_seq": it["decision_seq"],
                "decided_by": it["decided_by"],
                "decided_at": it["decided_at"].isoformat() if it["decided_at"] else None,
                "decision_reason": it["decision_reason"],
            },
        })

    return {
        "schema_version": OPERATOR_PROJECTION_SCHEMA_VERSION,
        "calculation_kind": CALCULATION_KIND,
        "project_id": bundle["project_id"],
        "result_id": res["id"],
        "status": res["status"],
        "formula_version": res["formula_version"],
        "currency_code": res["currency_code"],
        "annual_labor_savings": _dec_str(res["annual_labor_savings"]),
        "annual_net_benefit": _dec_str(res["annual_net_benefit"]),
        "first_year_net_benefit": _dec_str(res["first_year_net_benefit"]),
        "first_year_roi_percent": _dec_str(res["first_year_roi_percent"]),
        "roi_percent_status": res["roi_percent_status"],
        "formula_input_digest": res["formula_input_digest"],
        "provenance_fingerprint": res["provenance_fingerprint"],
        "diagnostics": res["diagnostics"],
        "computed_by": res["computed_by"],
        "computed_at": res["computed_at"].isoformat() if res["computed_at"] else None,
        "inputs": out_inputs,
        "all_evidence_available": all(it["available"] for it in bundle["inputs"]),
    }


# ─────────────────────────────── client projection ───────────────────────────────

def client_projection(bundle: dict[str, Any]) -> dict[str, Any]:
    """Allowlist-only, export-ready projection.

    Emits only the fields on the explicit allowlist below. It never copies a raw
    source excerpt, locator, range, storage reference, internal identifier, actor
    identity, decision metadata, diagnostics, or any unavailable-source content.
    """
    res = bundle["result"]
    status = res["status"]

    payload: dict[str, Any] = {
        "schema_version": CLIENT_PROJECTION_SCHEMA_VERSION,
        "calculation_kind": CALCULATION_KIND,
        "status": status,
        "result": None,
        "assumptions": [],
        "caveats": [],
    }

    if status == "blocked":
        # Omit all source content and values; show only a safe availability caveat.
        payload["caveats"] = [_BLOCKED_CLIENT_CAVEAT]
        return payload

    # valid / not_applicable — values are present and all evidence is available.
    payload["result"] = {
        "currency": res["currency_code"],
        "annual_labor_savings": _money(res["annual_labor_savings"]),
        "annual_net_benefit": _money(res["annual_net_benefit"]),
        "first_year_net_benefit": _money(res["first_year_net_benefit"]),
        "first_year_roi_percent": _percent(res["first_year_roi_percent"]),
    }

    assumptions = []
    by_role = {it["input_role"]: it for it in bundle["inputs"]}
    for role in ROLES:
        it = by_role.get(role)
        if it is None:
            continue
        # A human-readable source label is shown only when the source is available.
        label = None
        if it["available"]:
            subject = (it["subject_label"] or "").strip()
            metric = (it["metric_label"] or "").strip()
            label = " — ".join(p for p in (subject, metric) if p) or None
        assumptions.append({
            "role": role,
            "label": label,
            "value": _dec_str(it["resolved_numeric_value"]),
            "unit": it["resolved_unit"] or None,
            "currency": it["resolved_currency_code"],
            "period": it["resolved_period"],
        })
    payload["assumptions"] = assumptions

    caveats: list[str] = []
    if status == "not_applicable":
        caveats.append(_NOT_APPLICABLE_CLIENT_CAVEAT)
    for key in res["diagnostics"]:
        safe = _CLIENT_SAFE_CAVEATS.get(key)
        if safe:
            caveats.append(safe)
    payload["caveats"] = caveats
    return payload
