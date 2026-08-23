"""Derived decision-object layer for v4 ProjectState snapshots.

This module keeps tranche 1 additive: existing phase outputs remain canonical,
and decision_objects is rebuilt deterministically from them.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Any

from state import (
    ApprovalRecord,
    ApprovalStatus,
    CalibrationSnapshot,
    Decision,
    DecisionAction,
    DecisionObjectStatus,
    DecisionObjects,
    Evidence,
    OutcomeLink,
    ProjectState,
    Provenance,
    Risk,
    Signal,
    recorded_dq_total,
)


logger = logging.getLogger(__name__)

DECISION_OBJECTS_SCHEMA_VERSION = "1.0"
_HYPOTHESIS_REF_RX = re.compile(r"\b(H\d+)\b", re.I)


def stable_object_id(kind: str, *parts: Any) -> str:
    """Stable semantic id based on normalized payload, not list position."""
    material = "|".join(_normalize_part(part) for part in parts if part is not None)
    digest = hashlib.sha1(material.encode("utf-8")).hexdigest()[:12]
    return f"{kind}_{digest}"


def compute_source_state_hash(state: ProjectState) -> str:
    payload = {
        "project_id": state.project_id,
        "project_name": state.project_name,
        "brief": state.brief,
        "data": state.data,
        "imported_evidence": _dump_jsonable(state.imported_evidence),
        "imported_signals": _dump_jsonable(state.imported_signals),
        "classify": _dump_jsonable(state.classify),
        "hypotheses": _hashable_hypotheses(state),
        "gauntlet": _dump_jsonable(state.gauntlet),
        "audit": _dump_jsonable(state.audit),
        "strategy": _dump_jsonable(state.strategy),
        "monitor": _dump_jsonable(state.monitor),
        "sqi": _dump_jsonable(state.sqi),
        "det_scores": _dump_jsonable(state.det_scores),
        "report": state.report,
        "dq": _dump_jsonable(state.dq),
        "predictions": _dump_jsonable(state.predictions),
        "brier_score": state.brier_score,
        "approvals_granted": _dump_jsonable(state.approvals_granted),
        "risk_classification": state.risk_classification,
        "policy_audit_log": _relevant_audit_events(state),
        "reentry_triggers_fired": _dump_jsonable(state.reentry_triggers_fired),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def mark_decision_objects_stale(state: ProjectState, reason: str = "") -> None:
    current_hash = compute_source_state_hash(state)
    if state.decision_objects is None:
        state.decision_objects = DecisionObjects(
            schema_version=DECISION_OBJECTS_SCHEMA_VERSION,
            rebuilt_at=_iso_now(),
            source_state_hash=current_hash,
            status=DecisionObjectStatus.STALE,
            rebuild_error=reason,
        )
        return

    state.decision_objects.status = DecisionObjectStatus.STALE
    state.decision_objects.source_state_hash = current_hash
    if reason:
        state.decision_objects.rebuild_error = reason


def ensure_decision_objects(state: ProjectState, trigger: str = "system") -> DecisionObjects:
    """Keep decision_objects in sync with the canonical phase outputs."""
    current_hash = compute_source_state_hash(state)
    existing = state.decision_objects
    if (
        existing is not None
        and existing.status == DecisionObjectStatus.FRESH
        and existing.source_state_hash == current_hash
    ):
        return existing

    if existing is not None and existing.source_state_hash != current_hash:
        existing.status = DecisionObjectStatus.STALE
        existing.source_state_hash = current_hash

    try:
        rebuilt = build_decision_objects(state, current_hash=current_hash)
        state.decision_objects = rebuilt
        return rebuilt
    except Exception as exc:  # pragma: no cover - exercised through API/workspace smoke
        logger.exception("decision_objects rebuild failed (%s)", trigger)
        state.decision_objects = _rebuild_failed_snapshot(existing, current_hash, str(exc))
        _log_rebuild_failure(state, trigger, str(exc))
        return state.decision_objects


def build_decision_objects(
    state: ProjectState,
    *,
    current_hash: str | None = None,
    rebuilt_at: str | None = None,
) -> DecisionObjects:
    rebuilt_at = rebuilt_at or _iso_now()
    current_hash = current_hash or compute_source_state_hash(state)
    decision_id = stable_object_id("decision", state.project_id, "primary")

    evidence_items: list[Evidence] = []
    risk_items: list[Risk] = []
    action_items: list[DecisionAction] = []
    signal_items: list[Signal] = []
    approval_items: list[ApprovalRecord] = []
    outcome_items: list[OutcomeLink] = []
    calibration_items: list[CalibrationSnapshot] = []

    evidence_by_id: dict[str, Evidence] = {}
    risk_by_id: dict[str, Risk] = {}
    signal_by_id: dict[str, Signal] = {}
    action_by_id: dict[str, DecisionAction] = {}
    approval_by_id: dict[str, ApprovalRecord] = {}
    hypothesis_evidence_ids: dict[str, set[str]] = {
        h.id: set(getattr(h, "evidence_ids", []) or []) for h in (state.hypotheses or [])
    }

    if state.hypotheses:
        hyp_provenance = _section_provenance(state, "hypotheses", rebuilt_at, default_ref="hypotheses")
        for hypothesis in state.hypotheses:
            if hypothesis.signal:
                signal = Signal(
                    signal_id=stable_object_id("signal", state.project_id, "hypothesis", hypothesis.id, hypothesis.signal),
                    name=hypothesis.signal,
                    description=hypothesis.justification or hypothesis.confirm or hypothesis.reject or hypothesis.text,
                    source_phase="hypotheses",
                    cadence="phase_hypothesis",
                    linked_decision_ids=[decision_id],
                    linked_hypothesis_ids=[hypothesis.id],
                    provenance=_with_source_ref(hyp_provenance, f"hypotheses:{hypothesis.id}:signal"),
                )
                signal_by_id[signal.signal_id] = signal

    if state.gauntlet:
        gauntlet_provenance = _section_provenance(state, "gauntlet", rebuilt_at, default_ref="gauntlet")
        for result in state.gauntlet.results:
            linked_hypothesis_ids = [result.id] if result.id else []
            risk = Risk(
                risk_id=stable_object_id(
                    "risk",
                    state.project_id,
                    "gauntlet",
                    result.id,
                    result.crux,
                    _dump_jsonable(result.top_fmea),
                    result.risk_rank,
                ),
                title=f"Gauntlet risk {result.id or '?'}",
                summary=result.crux or result.fta_cut_set or "Gauntlet-identified risk",
                severity=_severity_from_gauntlet(result.risk_rank, result.top_fmea),
                source_phase="gauntlet",
                linked_decision_ids=[decision_id],
                linked_hypothesis_ids=linked_hypothesis_ids,
            )
            risk_by_id[risk.risk_id] = risk

            for framework in result.frameworks:
                evidence = Evidence(
                    evidence_id=stable_object_id(
                        "evidence",
                        state.project_id,
                        "gauntlet",
                        result.id,
                        framework.get("fw"),
                        framework.get("finding"),
                        framework.get("action"),
                    ),
                    title=f"Gauntlet {framework.get('fw', 'framework')} evidence",
                    summary=str(framework.get("finding", "")).strip(),
                    category="gauntlet_framework",
                    source_phase="gauntlet",
                    linked_decision_ids=[decision_id],
                    linked_hypothesis_ids=linked_hypothesis_ids,
                    linked_risk_ids=[risk.risk_id],
                    provenance=_with_source_ref(
                        gauntlet_provenance,
                        f"gauntlet:{result.id}:{framework.get('fw', 'framework')}",
                    ),
                )
                _add_evidence(evidence_by_id, evidence, hypothesis_evidence_ids)

            if result.crux:
                crux_evidence = Evidence(
                    evidence_id=stable_object_id("evidence", state.project_id, "gauntlet-crux", result.id, result.crux),
                    title=f"Gauntlet crux {result.id or '?'}",
                    summary=result.crux,
                    category="gauntlet_crux",
                    source_phase="gauntlet",
                    linked_decision_ids=[decision_id],
                    linked_hypothesis_ids=linked_hypothesis_ids,
                    linked_risk_ids=[risk.risk_id],
                    provenance=_with_source_ref(gauntlet_provenance, f"gauntlet:{result.id}:crux"),
                )
                _add_evidence(evidence_by_id, crux_evidence, hypothesis_evidence_ids)

    if state.audit:
        audit_provenance = _section_provenance(state, "audit", rebuilt_at, default_ref="audit")
        for index, item in enumerate(state.audit.fmea):
            risk = Risk(
                risk_id=stable_object_id(
                    "risk",
                    state.project_id,
                    "audit-fmea",
                    item.component,
                    item.failure_mode,
                    item.effect,
                    item.rpn,
                ),
                title=f"FMEA: {item.component}",
                summary=item.failure_mode or item.effect or item.action,
                severity=_severity_from_rpn(item.rpn),
                source_phase="audit",
                linked_decision_ids=[decision_id],
            )
            risk_by_id[risk.risk_id] = risk
            evidence = Evidence(
                evidence_id=stable_object_id(
                    "evidence",
                    state.project_id,
                    "audit-fmea",
                    item.component,
                    item.failure_mode,
                    item.evidence,
                    item.action,
                ),
                title=f"FMEA evidence {item.component}",
                summary=item.evidence or item.action or item.effect,
                category="audit_fmea",
                source_phase="audit",
                linked_decision_ids=[decision_id],
                linked_risk_ids=[risk.risk_id],
                provenance=_with_source_ref(audit_provenance, f"audit:fmea:{index}"),
            )
            _add_evidence(evidence_by_id, evidence, hypothesis_evidence_ids)

        for index, item in enumerate(state.audit.hazop):
            risk = Risk(
                risk_id=stable_object_id(
                    "risk",
                    state.project_id,
                    "audit-hazop",
                    item.node,
                    item.deviation,
                    item.consequence,
                ),
                title=f"HAZOP: {item.node}",
                summary=item.deviation or item.consequence,
                severity="medium",
                source_phase="audit",
                linked_decision_ids=[decision_id],
            )
            risk_by_id[risk.risk_id] = risk
            evidence = Evidence(
                evidence_id=stable_object_id(
                    "evidence",
                    state.project_id,
                    "audit-hazop",
                    item.node,
                    item.deviation,
                    item.evidence,
                ),
                title=f"HAZOP evidence {item.node}",
                summary=item.evidence or item.consequence,
                category="audit_hazop",
                source_phase="audit",
                linked_decision_ids=[decision_id],
                linked_risk_ids=[risk.risk_id],
                provenance=_with_source_ref(audit_provenance, f"audit:hazop:{index}"),
            )
            _add_evidence(evidence_by_id, evidence, hypothesis_evidence_ids)

        for index, item in enumerate(state.audit.stpa):
            risk = Risk(
                risk_id=stable_object_id(
                    "risk",
                    state.project_id,
                    "audit-stpa",
                    item.control_action,
                    item.hazard,
                    item.constraint,
                ),
                title=f"STPA: {item.control_action}",
                summary=item.hazard or item.constraint,
                severity="high",
                source_phase="audit",
                linked_decision_ids=[decision_id],
            )
            risk_by_id[risk.risk_id] = risk
            evidence = Evidence(
                evidence_id=stable_object_id(
                    "evidence",
                    state.project_id,
                    "audit-stpa",
                    item.control_action,
                    item.hazard,
                    item.constraint,
                ),
                title=f"STPA evidence {item.control_action}",
                summary=item.constraint or item.hazard,
                category="audit_stpa",
                source_phase="audit",
                linked_decision_ids=[decision_id],
                linked_risk_ids=[risk.risk_id],
                provenance=_with_source_ref(audit_provenance, f"audit:stpa:{index}"),
            )
            _add_evidence(evidence_by_id, evidence, hypothesis_evidence_ids)

        for index, finding in enumerate(state.audit.top_findings):
            finding_evidence = Evidence(
                evidence_id=stable_object_id("evidence", state.project_id, "audit-top-finding", finding),
                title=f"Audit finding {index + 1}",
                summary=finding,
                category="audit_finding",
                source_phase="audit",
                linked_decision_ids=[decision_id],
                provenance=_with_source_ref(audit_provenance, f"audit:top_finding:{index}"),
            )
            _add_evidence(evidence_by_id, finding_evidence, hypothesis_evidence_ids)

    if state.strategy:
        strategy_provenance = _section_provenance(state, "strategy", rebuilt_at, default_ref="strategy")
        for verdict in state.strategy.preliminary_verdicts:
            hypothesis_ids = [verdict.id] if verdict.id else []
            verdict_evidence = Evidence(
                evidence_id=stable_object_id(
                    "evidence",
                    state.project_id,
                    "strategy-verdict",
                    verdict.id,
                    verdict.verdict.value if hasattr(verdict.verdict, "value") else verdict.verdict,
                    verdict.evidence,
                    verdict.monitoring_plan,
                ),
                title=f"Strategy verdict {verdict.id or '?'}",
                summary=verdict.evidence or verdict.monitoring_plan or "Strategy verdict rationale",
                category="strategy_verdict",
                source_phase="strategy",
                linked_decision_ids=[decision_id],
                linked_hypothesis_ids=hypothesis_ids,
                provenance=_with_source_ref(strategy_provenance, f"strategy:verdict:{verdict.id}"),
            )
            _add_evidence(evidence_by_id, verdict_evidence, hypothesis_evidence_ids)

        for action in state.strategy.strategies:
            linked_hypothesis_ids = sorted(set(_extract_hypothesis_refs(action.evidence_chain)))
            evidence_ids = sorted(
                {
                    evidence_id
                    for hypothesis_id in linked_hypothesis_ids
                    for evidence_id in hypothesis_evidence_ids.get(hypothesis_id, set())
                }
            )
            decision_action = DecisionAction(
                action_id=stable_object_id(
                    "action",
                    state.project_id,
                    action.action,
                    action.framework_source,
                    action.priority.value if hasattr(action.priority, "value") else action.priority,
                ),
                title=action.action,
                priority=action.priority.value if hasattr(action.priority, "value") else str(action.priority or ""),
                status="proposed",
                summary=action.justification or action.expected_impact or action.timeline,
                linked_decision_ids=[decision_id],
                linked_hypothesis_ids=linked_hypothesis_ids,
                evidence_ids=evidence_ids,
                source_phase="strategy",
            )
            action_by_id[decision_action.action_id] = decision_action

    if state.monitor:
        monitor_provenance = _section_provenance(state, "monitor", rebuilt_at, default_ref="monitor")
        schedule_groups = {
            "daily": state.monitor.ooda_schedule.daily,
            "weekly": state.monitor.ooda_schedule.weekly,
            "monthly": state.monitor.ooda_schedule.monthly,
        }
        for cadence, items in schedule_groups.items():
            for index, item in enumerate(items):
                signal = Signal(
                    signal_id=stable_object_id(
                        "signal",
                        state.project_id,
                        "monitor-schedule",
                        cadence,
                        item.metric,
                        item.owner,
                        item.source,
                    ),
                    name=item.metric,
                    description=f"{item.owner} monitors via {item.source}",
                    source_phase="monitor",
                    cadence=cadence,
                    linked_decision_ids=[decision_id],
                    provenance=_with_source_ref(monitor_provenance, f"monitor:{cadence}:{index}"),
                )
                signal_by_id[signal.signal_id] = signal

        for index, canary in enumerate(state.monitor.canaries):
            signal = Signal(
                signal_id=stable_object_id(
                    "signal",
                    state.project_id,
                    "monitor-canary",
                    canary.signal,
                    canary.direction,
                    canary.window,
                    canary.meaning,
                ),
                name=canary.signal,
                description=canary.meaning or f"{canary.direction} over {canary.window}",
                source_phase="monitor",
                cadence=canary.window,
                linked_decision_ids=[decision_id],
                provenance=_with_source_ref(monitor_provenance, f"monitor:canary:{index}"),
            )
            signal_by_id[signal.signal_id] = signal

    for imported in state.imported_evidence or []:
        payload = imported.model_dump(mode="json")
        payload["source_phase"] = payload.get("source_phase") or "connector_csv"
        linked_decision_ids = list(payload.get("linked_decision_ids") or [])
        if decision_id not in linked_decision_ids:
            linked_decision_ids.append(decision_id)
        payload["linked_decision_ids"] = sorted(set(linked_decision_ids))
        evidence = Evidence.model_validate(payload)
        _add_evidence(evidence_by_id, evidence, hypothesis_evidence_ids)

    for imported in state.imported_signals or []:
        payload = imported.model_dump(mode="json")
        payload["source_phase"] = payload.get("source_phase") or "connector_csv"
        linked_decision_ids = list(payload.get("linked_decision_ids") or [])
        if decision_id not in linked_decision_ids:
            linked_decision_ids.append(decision_id)
        payload["linked_decision_ids"] = sorted(set(linked_decision_ids))
        signal = Signal.model_validate(payload)
        signal_by_id[signal.signal_id] = signal

    for action_name, approval in (state.approvals_granted or {}).items():
        approval_id = stable_object_id("approval", state.project_id, action_name)
        approval_by_id[approval_id] = ApprovalRecord(
            approval_id=approval_id,
            approval_type="granted_action",
            status=ApprovalStatus.GRANTED,
            requested_at=approval.get("requested_at"),
            resolved_at=approval.get("granted_at"),
            requested_by=approval.get("requested_by", ""),
            resolved_by=approval.get("approved_by", ""),
            scope=action_name,
            reason=approval.get("rationale", ""),
        )

    for event in state.policy_audit_log or []:
        if event.get("event_type") != "policy_gate_blocked":
            continue
        details = event.get("details", {}) or {}
        if details.get("category") != "approval" and not details.get("requires_hitl"):
            continue
        scope = details.get("phase") or details.get("action") or "approval"
        approval_id = stable_object_id("approval", state.project_id, scope)
        if approval_id in approval_by_id:
            continue
        approval_by_id[approval_id] = ApprovalRecord(
            approval_id=approval_id,
            approval_type="hitl_required",
            status=ApprovalStatus.PENDING,
            requested_at=_event_ts_iso(event),
            resolved_at=None,
            requested_by="policy_gate",
            resolved_by="",
            scope=scope,
            reason=details.get("reason", ""),
        )

    for prediction in state.predictions:
        outcome = OutcomeLink(
            outcome_id=stable_object_id(
                "outcome",
                state.project_id,
                prediction.hypothesis_id,
                prediction.phase,
                prediction.framework_used,
            ),
            hypothesis_id=prediction.hypothesis_id,
            phase=prediction.phase,
            predicted_probability=prediction.predicted_probability,
            actual_outcome=prediction.actual_outcome,
            framework_used=prediction.framework_used,
            recorded_at=prediction.timestamp.isoformat() if hasattr(prediction.timestamp, "isoformat") else str(prediction.timestamp),
            notes="",
        )
        outcome_items.append(outcome)

    # Same DQ source as the workspace summary. Reading the never-written
    # state.dq model here would put a contradictory zero into
    # decision_objects.json while the dashboard showed the real score.
    dq_total = recorded_dq_total(state)
    if state.brier_score is not None or state.sqi or state.det_scores or dq_total is not None:
        calibration_items.append(
            CalibrationSnapshot(
                snapshot_id=stable_object_id(
                    "calibration",
                    state.project_id,
                    state.brier_score,
                    getattr(state.sqi, "sqi_overall", None),
                    getattr(state.det_scores, "overall", None),
                    dq_total,
                ),
                recorded_at=rebuilt_at,
                brier_score=state.brier_score,
                sqi_overall=state.sqi.sqi_overall if state.sqi else None,
                det_score_overall=state.det_scores.overall if state.det_scores else None,
                dq_total=dq_total,
                notes="Derived from current project state",
            )
        )

    if state.hypotheses:
        for hypothesis in state.hypotheses:
            hypothesis.evidence_ids = sorted(hypothesis_evidence_ids.get(hypothesis.id, set()))

    evidence_items = sorted(evidence_by_id.values(), key=lambda item: item.evidence_id)
    risk_items = sorted(risk_by_id.values(), key=lambda item: item.risk_id)
    signal_items = sorted(signal_by_id.values(), key=lambda item: item.signal_id)
    action_items = sorted(action_by_id.values(), key=lambda item: item.action_id)
    approval_items = sorted(approval_by_id.values(), key=lambda item: item.approval_id)

    for risk in risk_items:
        risk.evidence_ids = sorted(
            evidence.evidence_id for evidence in evidence_items if risk.risk_id in evidence.linked_risk_ids
        )

    primary_decision = Decision(
        decision_id=decision_id,
        project_id=state.project_id,
        title=state.project_name or "Untitled decision",
        domain=state.classify.domain if state.classify else "",
        summary=_decision_summary(state),
        status=_decision_status(state),
        scenario_key="primary",
        hypothesis_ids=[hypothesis.id for hypothesis in (state.hypotheses or [])],
        risk_ids=[risk.risk_id for risk in risk_items],
        action_ids=[action.action_id for action in action_items],
        signal_ids=[signal.signal_id for signal in signal_items],
        approval_ids=[approval.approval_id for approval in approval_items],
        outcome_ids=[outcome.outcome_id for outcome in outcome_items],
    )

    return DecisionObjects(
        schema_version=DECISION_OBJECTS_SCHEMA_VERSION,
        rebuilt_at=rebuilt_at,
        source_state_hash=current_hash,
        status=DecisionObjectStatus.FRESH,
        rebuild_error="",
        primary_decision=primary_decision,
        evidences=evidence_items,
        risks=risk_items,
        actions=action_items,
        signals=signal_items,
        approvals=approval_items,
        outcomes=outcome_items,
        calibration_snapshots=calibration_items,
    )


def _normalize_part(part: Any) -> str:
    if part is None:
        return ""
    if isinstance(part, str):
        return " ".join(part.split()).strip().lower()
    return json.dumps(_dump_jsonable(part), sort_keys=True, ensure_ascii=True)


def _dump_jsonable(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dict__"):
        return {key: _dump_jsonable(val) for key, val in vars(value).items()}
    if isinstance(value, list):
        return [_dump_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _dump_jsonable(val) for key, val in value.items()}
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    return value


def _hashable_hypotheses(state: ProjectState) -> list[dict[str, Any]] | None:
    if not state.hypotheses:
        return None
    rows: list[dict[str, Any]] = []
    for hypothesis in state.hypotheses:
        payload = _dump_jsonable(hypothesis)
        if isinstance(payload, dict):
            payload.pop("evidence_ids", None)
        rows.append(payload)
    return rows


def _relevant_audit_events(state: ProjectState) -> list[dict[str, Any]]:
    relevant: list[dict[str, Any]] = []
    for event in state.policy_audit_log or []:
        if event.get("event_type") not in {"operator_state_edit", "approval_granted", "policy_gate_blocked"}:
            continue
        details = event.get("details", {}) or {}
        if event.get("event_type") == "policy_gate_blocked" and details.get("category") != "approval":
            continue
        relevant.append(
            {
                "event_type": event.get("event_type"),
                "ts": event.get("ts"),
                "phase": event.get("phase"),
                "details": details,
            }
        )
    return relevant


def _section_provenance(state: ProjectState, section: str, rebuilt_at: str, *, default_ref: str) -> Provenance:
    for event in reversed(state.policy_audit_log or []):
        if event.get("event_type") != "operator_state_edit":
            continue
        details = event.get("details", {}) or {}
        if details.get("section") != section:
            continue
        return Provenance(
            source_type="operator_edit",
            source_ref=default_ref,
            captured_at=_event_ts_iso(event),
            captured_by=details.get("edited_by", "operator"),
            notes=f"operator edit: {section}",
        )
    return Provenance(
        source_type="phase_output",
        source_ref=default_ref,
        captured_at=rebuilt_at,
        captured_by="decision-engine",
    )


def _with_source_ref(provenance: Provenance, source_ref: str) -> Provenance:
    payload = provenance.model_dump()
    payload["source_ref"] = source_ref
    payload["checksum"] = hashlib.sha1(source_ref.encode("utf-8")).hexdigest()[:12]
    return Provenance(**payload)


def _add_evidence(
    evidence_by_id: dict[str, Evidence],
    evidence: Evidence,
    hypothesis_evidence_ids: dict[str, set[str]],
) -> None:
    evidence_by_id[evidence.evidence_id] = evidence
    for hypothesis_id in evidence.linked_hypothesis_ids:
        hypothesis_evidence_ids.setdefault(hypothesis_id, set()).add(evidence.evidence_id)


def _severity_from_rpn(rpn: int) -> str:
    if rpn >= 200:
        return "critical"
    if rpn >= 120:
        return "high"
    if rpn >= 60:
        return "medium"
    return "low"


def _severity_from_gauntlet(risk_rank: int, top_fmea: dict[str, Any]) -> str:
    rpn = int((top_fmea or {}).get("rpn") or 0)
    if rpn:
        return _severity_from_rpn(rpn)
    if risk_rank <= 1:
        return "high"
    if risk_rank == 2:
        return "medium"
    return "low"


def _extract_hypothesis_refs(text: str) -> list[str]:
    return [match.upper() for match in _HYPOTHESIS_REF_RX.findall(text or "")]


def _decision_summary(state: ProjectState) -> str:
    if state.strategy and state.strategy.executive_strategy:
        return state.strategy.executive_strategy[:400]
    if state.classify and state.classify.justification:
        return state.classify.justification[:400]
    return (state.brief or "")[:400]


def _decision_status(state: ProjectState) -> str:
    if state.kill_switch_active:
        return "blocked"
    if state.phase_status.get("report") == "completed" or getattr(state.phase_status.get("report"), "value", None) == "completed":
        return "completed"
    if any(
        getattr(status, "value", status) == "failed"
        for status in (state.phase_status or {}).values()
    ):
        return "blocked"
    if any(
        getattr(status, "value", status) == "stale"
        for status in (state.phase_status or {}).values()
    ):
        return "stale"
    return "active"


def _rebuild_failed_snapshot(
    existing: DecisionObjects | None,
    current_hash: str,
    error: str,
) -> DecisionObjects:
    payload = existing.model_dump(mode="json") if existing is not None else {}
    payload.update(
        {
            "schema_version": DECISION_OBJECTS_SCHEMA_VERSION,
            "rebuilt_at": _iso_now(),
            "source_state_hash": current_hash,
            "status": DecisionObjectStatus.REBUILD_FAILED.value,
            "rebuild_error": error,
        }
    )
    return DecisionObjects.model_validate(payload)


def _log_rebuild_failure(state: ProjectState, trigger: str, error: str) -> None:
    try:
        from policy import log_policy_event

        log_policy_event(
            state,
            "decision_objects_rebuild_failed",
            {
                "trigger": trigger,
                "error": error,
            },
        )
    except Exception:
        logger.debug("decision_objects rebuild failure logging skipped", exc_info=True)


def _event_ts_iso(event: dict[str, Any]) -> str:
    ts = event.get("ts")
    if ts is None:
        return ""
    try:
        return datetime.fromtimestamp(float(ts)).isoformat()
    except Exception:
        return str(ts)


def _iso_now() -> str:
    return datetime.now().isoformat()
