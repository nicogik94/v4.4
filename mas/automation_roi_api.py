"""Slice B PR2 — gated Automation ROI operator API surface.

A self-contained FastAPI ``APIRouter`` mounted by ``api.py`` under
``app.include_router(..., dependencies=[Depends(require_operator_auth)])`` so all
routes inherit the existing operator-auth control plane. Every route is
additionally gated by ``MAS_AUTOMATION_ROI_ENABLED`` (PR1's flag): when the flag
is off, the route returns 404 *before* any project lookup, database access, or
service execution — so no Automation ROI record can be written while disabled.

The write routes reuse PR1's repository/approvals/service/engine contracts
verbatim (no parallel lifecycle logic). All identifiers, resolved values,
provenance, timestamps, and result status are derived server-side. Read routes
return the operator audit projection and the allowlist-only client-safe
projection from ``knowledge.automation_roi.projections``.

Error contract: 401 operator auth (when configured, via the inherited
dependency); 404 flag off / project or resource not found; 409 lifecycle,
availability, approval, or project-consistency violations; 422 malformed payload,
extra fields, invalid fact values, or an invalid exact-six calculation map; 503
authoritative database unavailable. Responses never expose SQL, file paths,
storage references, tracebacks, or credentials.
"""
from __future__ import annotations

import contextlib
import uuid
from datetime import date
from typing import Any, Iterator, Literal, Optional, Union

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

import config
from knowledge.automation_roi import (
    approvals,
    calculator,
    projections,
    repository as repo,
    service,
    workspace,
)
from knowledge.automation_roi.calculator import ROLES
from knowledge.evidence_snapshot import repository as evidence_repo
from knowledge.evidence_snapshot.validation import FactValidationError, validate_fact

router = APIRouter(tags=["automation-roi"])

# Server-derived actor for every Slice B operator write (never client-supplied).
_ACTOR = "operator"

# Fixed, non-secret messages — never interpolate SQL, paths, or exception text.
_DB_UNAVAILABLE = "Automation ROI storage is unavailable"
_CONFLICT = "Request conflicts with the current Automation ROI lifecycle state"

# Fixed, non-secret messages for frozen-input compatibility failures (422). Keyed
# by the calculator's stable reason codes; never interpolate dynamic detail.
_COMPAT_DETAILS = {
    calculator.COMPAT_RATE_UNIT: "Fully loaded rate per hour requires unit 'per_hour'.",
}
_COMPAT_DEFAULT = "The fact's values are incompatible with this input role."

_RoleLiteral = Literal[
    "baseline_hours_per_period",
    "post_automation_hours_per_period",
    "fully_loaded_rate_per_hour",
    "periods_per_year",
    "annual_recurring_cost",
    "one_time_implementation_cost",
]


# ─────────────────────────── connection injection ───────────────────────────
# A single seam so the disposable PostgreSQL test harness can substitute a
# connection pinned to an ephemeral schema. Production opens the authoritative
# MAS database (DATABASE_URL), exactly like Slice A capture.

def open_connection():
    import psycopg

    return psycopg.connect(config.DATABASE_URL)


def _open():
    try:
        return open_connection()
    except Exception:
        raise HTTPException(status_code=503, detail=_DB_UNAVAILABLE) from None


def _integrity_errors() -> tuple[type[BaseException], ...]:
    try:
        import psycopg

        return (
            psycopg.errors.CheckViolation,
            psycopg.errors.ForeignKeyViolation,
            psycopg.errors.UniqueViolation,
            psycopg.errors.ExclusionViolation,
        )
    except Exception:  # pragma: no cover - psycopg always present in this app
        return ()


def _operational_errors() -> tuple[type[BaseException], ...]:
    try:
        import psycopg

        return (psycopg.OperationalError, psycopg.InterfaceError)
    except Exception:  # pragma: no cover
        return ()


@contextlib.contextmanager
def _write_txn() -> Iterator[Any]:
    """One transaction per write request: commit on success, roll back on any error.

    Maps integrity violations (lifecycle / project-consistency) to 409, lost or
    unavailable connections to 503, and re-raises explicit ``HTTPException``s
    (404/409/422) unchanged. Nothing leaks from the underlying driver.
    """
    conn = _open()
    integrity = _integrity_errors()
    operational = _operational_errors()
    try:
        yield conn
        conn.commit()
    except HTTPException:
        _safe_rollback(conn)
        raise
    except integrity:
        _safe_rollback(conn)
        raise HTTPException(status_code=409, detail=_CONFLICT) from None
    except operational:
        _safe_rollback(conn)
        raise HTTPException(status_code=503, detail=_DB_UNAVAILABLE) from None
    except Exception:
        _safe_rollback(conn)
        raise HTTPException(status_code=503, detail=_DB_UNAVAILABLE) from None
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def _safe_rollback(conn) -> None:
    with contextlib.suppress(Exception):
        conn.rollback()


@contextlib.contextmanager
def _read_conn() -> Iterator[Any]:
    conn = _open()
    operational = _operational_errors()
    try:
        yield conn
    except HTTPException:
        raise
    except operational:
        raise HTTPException(status_code=503, detail=_DB_UNAVAILABLE) from None
    except Exception:
        raise HTTPException(status_code=503, detail=_DB_UNAVAILABLE) from None
    finally:
        with contextlib.suppress(Exception):
            conn.close()


# ─────────────────────────── flag gate + guards ───────────────────────────

def require_roi_enabled() -> None:
    """404 before any lookup/write when the Slice B feature flag is off."""
    if not config.automation_roi_enabled():
        raise HTTPException(status_code=404, detail="Not found")


def _uuid_or_404(value: str, message: str) -> str:
    try:
        uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=404, detail=message) from None
    return value


def _uuid_or_422(value: str, message: str) -> str:
    try:
        uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=422, detail=message) from None
    return value


def _require_project(conn, project_id: str) -> None:
    row = conn.execute("SELECT 1 FROM projects WHERE id = %s", (project_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Project not found")


# ─────────────────────────────── request models ───────────────────────────────

class FactInput(BaseModel):
    """Slice A typed-fact fields. Numbers must be int or numeric string (never a
    JSON float) so a fact can never silently inherit binary floating-point error;
    validation is delegated to the existing Slice A validator."""

    model_config = ConfigDict(extra="forbid")

    fact_type: str
    value: Optional[Union[StrictInt, StrictStr]] = None
    text: Optional[str] = None
    unit: str = ""
    currency_code: Optional[str] = None
    as_of_date: Optional[date] = None
    numerator_context: Optional[str] = None
    denominator_context: Optional[str] = None
    percentage_basis: Optional[str] = None
    percentage_subtype: Optional[str] = None
    time_unit: Optional[str] = None
    counted_entity: Optional[str] = None


class CandidateFactCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_snapshot_id: str
    fact: FactInput
    subject_label: str = Field(min_length=1)
    metric_label: str = Field(min_length=1)
    period_basis: Optional[str] = None
    source_locator: str = ""
    source_char_range: Optional[str] = None
    extraction_rationale: str = Field(default="", max_length=2000)


class DecisionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_type: Literal["approve", "reject", "withdraw"]
    reason: str = Field(default="", max_length=2000)


class FreezeInputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_fact_revision_id: str
    approval_decision_id: str
    input_role: _RoleLiteral


class CalculationInputsMap(BaseModel):
    """Exactly the six required roles, once each — extra/missing keys are rejected."""

    model_config = ConfigDict(extra="forbid")

    baseline_hours_per_period: str
    post_automation_hours_per_period: str
    fully_loaded_rate_per_hour: str
    periods_per_year: str
    annual_recurring_cost: str
    one_time_implementation_cost: str


class CalculationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inputs: CalculationInputsMap


# ─────────────────────────────── write endpoints ───────────────────────────────

@router.post(
    "/projects/{project_id}/automation-roi/candidate-facts",
    status_code=201,
    dependencies=[Depends(require_roi_enabled)],
)
def create_candidate_fact(project_id: str, body: CandidateFactCreateRequest) -> dict[str, Any]:
    """Create one ROI-eligible typed fact plus its 1:1 extraction context, atomically."""
    _uuid_or_404(project_id, "Project not found")
    snapshot_id = _uuid_or_422(body.source_snapshot_id, "Invalid source_snapshot_id")

    with _write_txn() as conn:
        _require_project(conn, project_id)
        snap = conn.execute(
            "SELECT project_id::text FROM source_snapshot WHERE id = %s", (snapshot_id,)
        ).fetchone()
        if snap is None or snap[0] != project_id:
            raise HTTPException(status_code=404, detail="Source snapshot not found")
        if not evidence_repo.snapshot_available(conn, snapshot_id):
            raise HTTPException(status_code=409, detail="Source snapshot is unavailable")

        try:
            validated = validate_fact(
                body.fact.fact_type,
                value=body.fact.value,
                text=body.fact.text,
                unit=body.fact.unit,
                currency_code=body.fact.currency_code,
                as_of_date=body.fact.as_of_date,
                numerator_context=body.fact.numerator_context,
                denominator_context=body.fact.denominator_context,
                percentage_basis=body.fact.percentage_basis,
                percentage_subtype=body.fact.percentage_subtype,
                time_unit=body.fact.time_unit,
                counted_entity=body.fact.counted_entity,
            )
        except FactValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

        cfr_id, ctx_id = repo.create_eligible_fact(
            conn,
            project_id=project_id,
            source_snapshot_id=snapshot_id,
            fact=validated,
            subject_label=body.subject_label,
            metric_label=body.metric_label,
            period_basis=body.period_basis,
            source_locator=body.source_locator,
            source_char_range=body.source_char_range,
            extraction_rationale=body.extraction_rationale,
            actor=_ACTOR,
        )
        response = {
            "candidate_fact_revision_id": cfr_id,
            "extraction_context_id": ctx_id,
            "project_id": project_id,
            "source_snapshot_id": snapshot_id,
            "roi_eligible": True,
        }
    return response


@router.post(
    "/projects/{project_id}/automation-roi/candidate-facts/{fact_id}/decisions",
    status_code=201,
    dependencies=[Depends(require_roi_enabled)],
)
def append_fact_decision(
    project_id: str, fact_id: str, body: DecisionCreateRequest
) -> dict[str, Any]:
    """Append one approve/reject/withdraw decision under existing-core rules.

    The decision sequence and any revoked-approve linkage are derived server-side;
    illegal transitions (re-approve an already-approved fact, or revoke when no
    approval is active) return 409.
    """
    _uuid_or_404(project_id, "Project not found")
    _uuid_or_404(fact_id, "Candidate fact not found")

    with _write_txn() as conn:
        _require_project(conn, project_id)
        fact = conn.execute(
            "SELECT 1 FROM candidate_fact_revision WHERE id = %s AND project_id = %s",
            (fact_id, project_id),
        ).fetchone()
        if fact is None:
            raise HTTPException(status_code=404, detail="Candidate fact not found")

        active = approvals.active_approval_id(
            conn, project_id=project_id, candidate_fact_revision_id=fact_id
        )
        if body.decision_type == "approve":
            if active is not None:
                raise HTTPException(status_code=409, detail="Fact already has an active approval")
            decision_id = approvals.append_decision(
                conn, project_id=project_id, candidate_fact_revision_id=fact_id,
                decision_type="approve", reason=body.reason, actor=_ACTOR,
            )
        else:  # reject / withdraw revoke the active approve
            if active is None:
                raise HTTPException(
                    status_code=409, detail="No active approval to revoke"
                )
            decision_id = approvals.append_decision(
                conn, project_id=project_id, candidate_fact_revision_id=fact_id,
                decision_type=body.decision_type, revokes_decision_id=active,
                reason=body.reason, actor=_ACTOR,
            )
        response = {
            "approval_decision_id": decision_id,
            "decision_type": body.decision_type,
            "candidate_fact_revision_id": fact_id,
        }
    return response


@router.post(
    "/projects/{project_id}/automation-roi/inputs",
    status_code=201,
    dependencies=[Depends(require_roi_enabled)],
)
def freeze_input(project_id: str, body: FreezeInputRequest) -> dict[str, Any]:
    """Freeze one actively-approved, available, ROI-eligible fact into an input.

    Resolved value/unit/currency/period/provenance/timestamps are derived
    server-side from the immutable fact + context; the request carries only the
    three allowed identifiers.
    """
    _uuid_or_404(project_id, "Project not found")
    fact_id = _uuid_or_422(body.candidate_fact_revision_id, "Invalid candidate_fact_revision_id")
    decision_id = _uuid_or_422(body.approval_decision_id, "Invalid approval_decision_id")

    with _write_txn() as conn:
        _require_project(conn, project_id)
        fact = conn.execute(
            "SELECT 1 FROM candidate_fact_revision WHERE id = %s AND project_id = %s",
            (fact_id, project_id),
        ).fetchone()
        if fact is None:
            raise HTTPException(status_code=409, detail="Fact is not eligible for this project")
        if not evidence_repo.fact_available(conn, fact_id):
            raise HTTPException(status_code=409, detail="Source evidence is unavailable")
        try:
            input_id = repo.freeze_input(
                conn, project_id=project_id, candidate_fact_revision_id=fact_id,
                input_role=body.input_role, approval_decision_id=decision_id, frozen_by=_ACTOR,
            )
        except repo.FrozenInputCompatibilityError as exc:
            # Database-compatibility failure: reject before any insert (422), with a
            # fixed message — never expose SQL, the database error, or paths.
            raise HTTPException(
                status_code=422, detail=_COMPAT_DETAILS.get(exc.reason, _COMPAT_DEFAULT)
            ) from None
        except ValueError:
            raise HTTPException(
                status_code=409, detail="Fact cannot be frozen for this role"
            ) from None
        response = {
            "approved_calculation_input_id": input_id,
            "input_role": body.input_role,
            "candidate_fact_revision_id": fact_id,
        }
    return response


@router.post(
    "/projects/{project_id}/automation-roi/calculations",
    status_code=201,
    dependencies=[Depends(require_roi_enabled)],
)
def create_calculation(project_id: str, body: CalculationCreateRequest) -> dict[str, Any]:
    """Persist one deterministic CalculationResult from exactly the six frozen inputs."""
    _uuid_or_404(project_id, "Project not found")
    inputs_by_role = body.inputs.model_dump()
    for role in ROLES:
        _uuid_or_422(inputs_by_role[role], "Invalid approved input id in calculation map")

    with _write_txn() as conn:
        _require_project(conn, project_id)
        try:
            result_id = service.compute_and_persist(
                conn, project_id=project_id, inputs_by_role=inputs_by_role, computed_by=_ACTOR,
            )
        except service.CalculationRequestError:
            raise HTTPException(
                status_code=422, detail="Invalid calculation input set"
            ) from None
        except ValueError:
            # Unknown or cross-project approved-input id.
            raise HTTPException(
                status_code=422, detail="Unknown or cross-project calculation input"
            ) from None

        bundle = projections.load_result_bundle(
            conn, project_id=project_id, result_id=result_id
        )
        response = projections.operator_projection(bundle)
    return response


# ─────────────────────────────── read projections ───────────────────────────────

@router.get(
    "/projects/{project_id}/automation-roi/calculations/{result_id}",
    dependencies=[Depends(require_roi_enabled)],
)
def get_calculation_operator(project_id: str, result_id: str) -> dict[str, Any]:
    """Operator audit projection: inputs, decisions, availability, provenance, diagnostics."""
    _uuid_or_404(project_id, "Project not found")
    _uuid_or_404(result_id, "Calculation result not found")
    with _read_conn() as conn:
        _require_project(conn, project_id)
        bundle = projections.load_result_bundle(
            conn, project_id=project_id, result_id=result_id
        )
        if bundle is None:
            raise HTTPException(status_code=404, detail="Calculation result not found")
        return projections.operator_projection(bundle)


@router.get(
    "/projects/{project_id}/automation-roi/calculations/{result_id}/client",
    dependencies=[Depends(require_roi_enabled)],
)
def get_calculation_client(project_id: str, result_id: str) -> dict[str, Any]:
    """Allowlist-only client-safe projection (operator-authenticated export view)."""
    _uuid_or_404(project_id, "Project not found")
    _uuid_or_404(result_id, "Calculation result not found")
    with _read_conn() as conn:
        _require_project(conn, project_id)
        bundle = projections.load_result_bundle(
            conn, project_id=project_id, result_id=result_id
        )
        if bundle is None:
            raise HTTPException(status_code=404, detail="Calculation result not found")
        return projections.client_projection(bundle)


# ─────────────────────────────── operator workspace ───────────────────────────────

@router.get(
    "/projects/{project_id}/automation-roi/workspace",
    dependencies=[Depends(require_roi_enabled)],
)
def get_workspace(project_id: str) -> dict[str, Any]:
    """Operator-safe navigation data for the Automation ROI workspace.

    Read-only: lists this project's snapshots, ROI-eligible candidate facts and
    their server-derived decision state, frozen inputs, exact-six readiness, and
    calculation-result history. It performs no writes, runs no calculation, and
    never reimplements the client-safe projection (the dashboard calls the
    existing ``/calculations/{id}/client`` endpoint for previews). It returns no
    storage refs, paths, raw source content, actor identity, or decision
    sequence numbers; opaque ids are values only.
    """
    _uuid_or_404(project_id, "Project not found")
    with _read_conn() as conn:
        _require_project(conn, project_id)
        return workspace.load_workspace(conn, project_id=project_id)
