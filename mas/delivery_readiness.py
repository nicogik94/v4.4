"""Read-only delivery review readiness projection.

This module builds an advisory operator projection from existing persisted
signals. It does not approve delivery, mutate state, save state, run workflow
phases, generate reports, or verify semantic evidence support.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from cdp.citation_resolvability import build_defense_pass_result
from clarifications import build_clarification_summary
from report_quality import assess_risk_classification_gate
from state import ProjectState


DELIVERY_REVIEW_READINESS_SCHEMA_VERSION = "delivery_review_readiness.v0.1"
DELIVERY_REVIEW_READINESS_CAVEATS = [
    "Advisory review-readiness projection only.",
    "This projection does not approve delivery.",
    "This projection does not prove semantic evidence support.",
    "Human review remains mandatory.",
]

DeliveryReviewReadinessStatus = Literal[
    "ready_for_human_review",
    "needs_operator_review",
    "blocked_for_review",
]


class DeliveryReviewReadiness(BaseModel):
    project_id: str
    schema_version: str = DELIVERY_REVIEW_READINESS_SCHEMA_VERSION
    review_ready: bool = False
    status: DeliveryReviewReadinessStatus = "needs_operator_review"
    blocking_reasons: list[str] = Field(default_factory=list)
    review_warnings: list[str] = Field(default_factory=list)
    advisory_signals: list[str] = Field(default_factory=list)
    source_signals: dict[str, Any] = Field(default_factory=dict)
    caveats: list[str] = Field(default_factory=lambda: list(DELIVERY_REVIEW_READINESS_CAVEATS))


def build_delivery_review_readiness(
    project_id: str,
    state: ProjectState,
    *,
    workspace_summary: Any = None,
) -> DeliveryReviewReadiness:
    """Build a JSON-safe advisory readiness projection without mutating state."""
    source_signals: dict[str, Any] = {}
    blocking_reasons: list[str] = []
    review_warnings: list[str] = []

    for key, builder in (
        ("clarifications", _clarification_signal),
        ("evidence_review", _evidence_review_signal),
        ("risk_gate", _risk_gate_signal),
        ("phase_state", lambda value: _phase_state_signal(value, workspace_summary)),
        ("approvals", lambda value: _approvals_signal(value, workspace_summary)),
        ("staleness", _staleness_signal),
    ):
        signal, blockers, warnings = builder(state)
        source_signals[key] = signal
        blocking_reasons.extend(blockers)
        review_warnings.extend(warnings)

    blocking_reasons = _unique(blocking_reasons)
    review_warnings = _unique(review_warnings)
    if blocking_reasons:
        status: DeliveryReviewReadinessStatus = "blocked_for_review"
        review_ready = False
    elif review_warnings:
        status = "needs_operator_review"
        review_ready = False
    else:
        status = "ready_for_human_review"
        review_ready = True

    return DeliveryReviewReadiness(
        project_id=project_id,
        review_ready=review_ready,
        status=status,
        blocking_reasons=blocking_reasons,
        review_warnings=review_warnings,
        advisory_signals=_advisory_signals(status, blocking_reasons, review_warnings),
        source_signals=source_signals,
        caveats=list(DELIVERY_REVIEW_READINESS_CAVEATS),
    )


def _clarification_signal(state: ProjectState) -> tuple[dict[str, Any], list[str], list[str]]:
    summary = build_clarification_summary(state)
    blockers: list[str] = []
    warnings: list[str] = []

    if summary.open_required_count:
        blockers.append(f"{summary.open_required_count} required clarification question(s) are open.")
        status = "blocked"
    elif summary.total_cycles == 0:
        warnings.append("Clarification review has not been generated.")
        status = "unknown"
    elif summary.open_count:
        warnings.append(f"{summary.open_count} optional clarification question(s) remain open.")
        status = "needs_review"
    elif summary.refresh_candidate_phases:
        warnings.append(
            "Clarification answers may require phase review or rerun: "
            + ", ".join(summary.refresh_candidate_phases)
        )
        status = "needs_review"
    else:
        status = "clear"

    return (
        {
            "status": status,
            "total_cycles": summary.total_cycles,
            "latest_cycle_status": summary.latest_cycle_status,
            "total_questions": summary.total_questions,
            "open_count": summary.open_count,
            "open_required_count": summary.open_required_count,
            "refresh_candidate_phases": list(summary.refresh_candidate_phases),
            "next_action": summary.next_action,
        },
        blockers,
        warnings,
    )


def _evidence_review_signal(state: ProjectState) -> tuple[dict[str, Any], list[str], list[str]]:
    result = build_defense_pass_result(state)
    counts = dict(result.summary_counts)
    hard_statuses = ("unknown_evidence_id", "locator_mismatch", "malformed")
    hard_counts = {status: int(counts.get(status, 0) or 0) for status in hard_statuses}
    hard_total = sum(hard_counts.values())
    id_only_count = int(counts.get("resolved_id_only", 0) or 0)
    load_bearing_count = int(counts.get("load_bearing_review_count", 0) or 0)

    blockers: list[str] = []
    warnings: list[str] = []
    if hard_total:
        blockers.append(
            "Evidence review found unresolved or malformed citation marker(s): "
            + ", ".join(f"{name}={count}" for name, count in hard_counts.items() if count)
        )
        status = "blocked"
    elif result.missing_inputs:
        warnings.append("Evidence-review source signals are incomplete: " + ", ".join(result.missing_inputs))
        status = "unknown"
    elif id_only_count:
        warnings.append(f"Evidence review has {id_only_count} ID-only citation resolution(s).")
        status = "needs_review"
    elif load_bearing_count:
        warnings.append(f"Evidence review has {load_bearing_count} load-bearing line(s) requiring review.")
        status = "needs_review"
    else:
        status = "clear"

    return (
        {
            "status": status,
            "schema_version": result.schema_version,
            "source": result.source,
            "summary_counts": counts,
            "hard_blocking_status_counts": hard_counts,
            "id_only_resolution_count": id_only_count,
            "load_bearing_review_count": load_bearing_count,
            "missing_inputs": list(result.missing_inputs),
            "claims_requiring_review_count": len(result.claims_requiring_review),
            "malformed_candidates_count": len(result.malformed_candidates),
            "report_text_preserved": result.report_text_preserved,
            "read_only": True,
        },
        blockers,
        warnings,
    )


def _risk_gate_signal(state: ProjectState) -> tuple[dict[str, Any], list[str], list[str]]:
    try:
        assessment = assess_risk_classification_gate(state)
    except Exception as exc:  # pragma: no cover - defensive around future helper changes
        return (
            {
                "status": "unknown",
                "reason": "risk gate not available to readiness projector without report-quality coupling",
                "error_type": type(exc).__name__,
            },
            [],
            ["Risk gate signal is unknown."],
        )

    if assessment.warning_applies:
        return (
            {
                "status": "failed",
                "warning_applies": True,
                "warning_text": assessment.warning_text,
                "diagnostics": assessment.diagnostics,
            },
            ["Risk classification gate flagged high or critical generated risk under a low/minimal classification."],
            [],
        )

    return (
        {
            "status": "passed",
            "warning_applies": False,
            "warning_text": "",
            "diagnostics": assessment.diagnostics,
        },
        [],
        [],
    )


def _phase_state_signal(
    state: ProjectState,
    workspace_summary: Any = None,
) -> tuple[dict[str, Any], list[str], list[str]]:
    phase_statuses = {
        str(phase): _status_text(status)
        for phase, status in (getattr(state, "phase_status", {}) or {}).items()
    }
    blockers = _workspace_list(workspace_summary, "blocking_reasons")
    if not blockers:
        blockers = _phase_blocking_reasons(state, phase_statuses)

    warnings: list[str] = []
    running_phases = [phase for phase, status in phase_statuses.items() if status == "running"]
    if running_phases:
        warnings.append("Workflow phase(s) still running: " + ", ".join(running_phases))

    return (
        {
            "status": "blocked" if blockers else "clear",
            "current_phase": getattr(state, "current_phase", ""),
            "phase_statuses": phase_statuses,
            "blocking_reasons": list(blockers),
            "running_phases": running_phases,
        },
        list(blockers),
        warnings,
    )


def _approvals_signal(
    state: ProjectState,
    workspace_summary: Any = None,
) -> tuple[dict[str, Any], list[str], list[str]]:
    pending_scopes = _pending_operator_review_scopes(state)
    if _workspace_bool(workspace_summary, "requires_approval") and not pending_scopes:
        pending_scopes.append("workspace_summary")
    pending_scopes = _unique(pending_scopes)

    if pending_scopes:
        return (
            {
                "status": "pending_operator_review",
                "pending_count": len(pending_scopes),
                "pending_scopes": pending_scopes,
            },
            ["Mandatory operator review is still pending for: " + ", ".join(pending_scopes)],
            [],
        )

    return (
        {
            "status": "clear",
            "pending_count": 0,
            "pending_scopes": [],
        },
        [],
        [],
    )


def _staleness_signal(state: ProjectState) -> tuple[dict[str, Any], list[str], list[str]]:
    phase_statuses = {
        str(phase): _status_text(status)
        for phase, status in (getattr(state, "phase_status", {}) or {}).items()
    }
    stale_phases = [phase for phase, status in phase_statuses.items() if status == "stale"]
    decision_object_status = _status_text(getattr(getattr(state, "decision_objects", None), "status", ""))
    import_pending, import_pending_phase, import_pending_message = _import_pending_analysis(state)

    warnings: list[str] = []
    if stale_phases:
        warnings.append("Stale downstream phase(s) need operator review: " + ", ".join(stale_phases))
    if decision_object_status == "stale":
        warnings.append("Decision-object projection is stale.")
    if import_pending:
        warnings.append(import_pending_message or "Imported evidence is pending analysis.")

    return (
        {
            "status": "stale" if warnings else "current",
            "stale_phases": stale_phases,
            "decision_object_status": decision_object_status or "not_available",
            "imported_evidence_pending_analysis": import_pending,
            "imported_evidence_pending_phase": import_pending_phase,
            "imported_evidence_pending_message": import_pending_message,
        },
        [],
        warnings,
    )


def _phase_blocking_reasons(state: ProjectState, phase_statuses: dict[str, str]) -> list[str]:
    reasons: list[str] = []
    if getattr(state, "kill_switch_active", False):
        reasons.append(f"Kill switch active: {getattr(state, 'kill_switch_reason', '') or 'operator halt'}")
    for phase, status in phase_statuses.items():
        if status == "failed":
            reasons.append(f"Phase {phase} failed")
    decision_objects = getattr(state, "decision_objects", None)
    if _status_text(getattr(decision_objects, "status", "")) == "rebuild_failed":
        rebuild_error = str(getattr(decision_objects, "rebuild_error", "") or "").strip()
        reasons.append(f"Decision object rebuild failed: {rebuild_error}" if rebuild_error else "Decision object rebuild failed")
    caps = getattr(state, "budget_caps", {}) or {}
    consumed = getattr(state, "budget_consumed", {}) or {}
    consecutive_failures = _intish(consumed.get("consecutive_failures", 0))
    max_failures = _intish(caps.get("max_consecutive_failures", 0))
    if max_failures and consecutive_failures >= max_failures:
        reasons.append(f"Budget circuit breaker open ({consecutive_failures} consecutive failures)")
    for phase_name, breaker in (getattr(state, "phase_breakers", {}) or {}).items():
        if str((breaker or {}).get("state") or "").strip().lower() == "open":
            reasons.append(f"Phase breaker open: {phase_name}")
    return reasons


def _pending_operator_review_scopes(state: ProjectState) -> list[str]:
    scopes: list[str] = []
    granted_scopes = set()
    for action_name in (getattr(state, "approvals_granted", {}) or {}):
        if action_name:
            granted_scopes.add(str(action_name))

    decision_objects = getattr(state, "decision_objects", None)
    for record in getattr(decision_objects, "approvals", []) or []:
        scope = str(getattr(record, "scope", "") or "").strip() or "operator_review"
        status = _status_text(getattr(record, "status", ""))
        if status == "granted":
            granted_scopes.add(scope)
        elif status == "pending":
            scopes.append(scope)

    for event in getattr(state, "policy_audit_log", []) or []:
        if not isinstance(event, dict) or event.get("event_type") != "policy_gate_blocked":
            continue
        details = event.get("details", {}) or {}
        if details.get("category") != "approval" and not details.get("requires_hitl"):
            continue
        scope = str(details.get("phase") or details.get("action") or event.get("phase") or "operator_review").strip()
        if scope and scope not in granted_scopes:
            scopes.append(scope)

    return _unique(scopes)


def _import_pending_analysis(state: ProjectState) -> tuple[bool, str, str]:
    latest_event = None
    latest_ts = float("-inf")
    for event in getattr(state, "policy_audit_log", []) or []:
        if not isinstance(event, dict) or event.get("event_type") != "connector_import":
            continue
        details = event.get("details", {}) or {}
        if not details.get("analysis_pending"):
            continue
        if not (details.get("evidence_count") or details.get("signal_count")):
            continue
        event_ts = _floatish(event.get("ts"), default=0.0)
        if event_ts >= latest_ts:
            latest_event = event
            latest_ts = event_ts

    if latest_event is None:
        return False, "", ""

    details = latest_event.get("details", {}) or {}
    phase = str(details.get("analysis_pending_phase") or "").strip()
    if not phase:
        return False, "", ""

    completed_at = (getattr(state, "phase_run_completed_at", {}) or {}).get(phase, "")
    if completed_at:
        completed_dt = _parse_iso_datetime(completed_at)
        imported_dt = _parse_event_timestamp(latest_event.get("ts"))
        if completed_dt is not None and imported_dt is not None and completed_dt > imported_dt:
            return False, "", ""

    message = (
        "New imported evidence is available. Rerun analysis to incorporate it."
        if details.get("evidence_count") and not details.get("signal_count")
        else "New imported evidence/signals are available. Rerun analysis to incorporate them."
    )
    return True, phase, message


def _advisory_signals(
    status: DeliveryReviewReadinessStatus,
    blocking_reasons: list[str],
    review_warnings: list[str],
) -> list[str]:
    if status == "blocked_for_review":
        return [f"blocked: {reason}" for reason in blocking_reasons]
    if status == "needs_operator_review":
        return [f"needs_review: {warning}" for warning in review_warnings]
    return ["No hard blockers or review warnings found in available signals."]


def _workspace_list(workspace_summary: Any, field_name: str) -> list[str]:
    value = getattr(workspace_summary, field_name, None) if workspace_summary is not None else None
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return []


def _workspace_bool(workspace_summary: Any, field_name: str) -> bool:
    value = getattr(workspace_summary, field_name, False) if workspace_summary is not None else False
    return bool(value)


def _status_text(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip().lower()


def _intish(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


def _floatish(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_event_timestamp(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(value))
    except (TypeError, ValueError, OSError):
        return None


def _parse_iso_datetime(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value or ""))
    except (TypeError, ValueError):
        return None


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        result.append(text)
        seen.add(text)
    return result
