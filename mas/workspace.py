"""Backend-computed command-center workspace summaries."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from clarifications import ClarificationSummary, build_clarification_summary
from decision_objects import compute_source_state_hash, ensure_decision_objects
from delivery_readiness import DeliveryReviewReadiness, build_delivery_review_readiness
from ingestion_contract import DEFAULT_INGESTION_SOURCE, LEGACY_CONTRACT_VERSION
from knowledge.freshness import build_knowledge_health
from knowledge.retrieval import RetrievalPhaseImpactSummary, build_prompt_facing_retrieval_impact
from report_quality import assess_decision_memo_pilot_plan_quality
from state import (
    DEFAULT_OUTPUT_LANGUAGE,
    DEFAULT_REPORT_MODE,
    ApprovalRecord,
    DecisionObjectStatus,
    Evidence,
    ProjectState,
    Risk,
)


class ScoreSummary(BaseModel):
    sqi_overall: Optional[float] = None
    det_score_overall: Optional[float] = None
    brier_score: Optional[float] = None
    # None when the project has no DQ breakdown at all. A real DQ total of 0.0
    # and a missing one are different facts, and the dashboard renders the
    # missing case as an em dash rather than as a zero score.
    dq_total: Optional[float] = None


class DecisionObjectHealth(BaseModel):
    status: str = DecisionObjectStatus.STALE.value
    schema_version: str = ""
    rebuilt_at: str = ""
    source_state_hash: str = ""
    current_state_hash: str = ""
    rebuild_error: str = ""
    decision_count: int = 0
    evidence_count: int = 0
    risk_count: int = 0
    action_count: int = 0
    signal_count: int = 0
    approval_count: int = 0


class WorkspaceHypothesisRow(BaseModel):
    hypothesis_id: str
    text: str = ""
    justification: str = ""
    signal: str = ""
    status: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_count: int = 0
    probability: Optional[float] = None


class KnowledgeHealthSummary(BaseModel):
    status: str = "unconfigured"
    message: str = ""
    source_count: int = 0
    enabled_source_count: int = 0
    item_count: int = 0
    fresh_item_count: int = 0
    stale_item_count: int = 0
    expired_item_count: int = 0
    quarantined_item_count: int = 0
    due_source_count: int = 0
    failed_source_count: int = 0
    last_sync_at: str = ""
    last_success_at: str = ""


class WorkspaceInputContract(BaseModel):
    contract_version: str = LEGACY_CONTRACT_VERSION
    source: str = DEFAULT_INGESTION_SOURCE
    external_case_id: str = ""
    metadata_keys: list[str] = Field(default_factory=list)


class WorkspaceResponseMetadata(BaseModel):
    response_schema_version: str = "workspace.summary.v1"
    generated_by: str = "mas.workspace"
    provenance: str = "backend_computed"
    input_contract_version: str = LEGACY_CONTRACT_VERSION


class WorkspaceReportOutputMetadata(BaseModel):
    current_output_language: str = DEFAULT_OUTPUT_LANGUAGE
    current_report_mode: str = DEFAULT_REPORT_MODE
    metadata_status: str = "not_generated"
    generated_output_language: Optional[str] = None
    generated_report_mode: Optional[str] = None
    rerun_required: bool = False
    rerun_notice: str = ""


class WorkspaceReportQualityFinding(BaseModel):
    rule_name: str
    message: str
    location: str = ""
    excerpt: str = ""
    severity: str = "advisory"


class WorkspaceReportQualitySummary(BaseModel):
    checked: bool = False
    status: str = "not_applicable"
    report_mode: str = DEFAULT_REPORT_MODE
    output_language: str = DEFAULT_OUTPUT_LANGUAGE
    finding_count: int = 0
    findings: list[WorkspaceReportQualityFinding] = Field(default_factory=list)


class WorkspaceSummary(BaseModel):
    project_id: str
    project_name: str
    current_phase: str
    project_status: str
    input_contract: WorkspaceInputContract = Field(default_factory=WorkspaceInputContract)
    response_metadata: WorkspaceResponseMetadata = Field(default_factory=WorkspaceResponseMetadata)
    report_output: WorkspaceReportOutputMetadata = Field(default_factory=WorkspaceReportOutputMetadata)
    report_quality: WorkspaceReportQualitySummary = Field(default_factory=WorkspaceReportQualitySummary)
    workflow_running: bool = False
    phase_statuses: dict[str, str] = Field(default_factory=dict)
    blocking_reasons: list[str] = Field(default_factory=list)
    requires_approval: bool = False
    has_stale_downstream: bool = False
    imported_evidence_pending_analysis: bool = False
    imported_evidence_pending_phase: str = ""
    imported_evidence_pending_message: str = ""
    retrieval_visibility: list[RetrievalPhaseImpactSummary] = Field(default_factory=list)
    active_risk_count: int = 0
    last_reentry_at: Optional[str] = None
    score_summary: ScoreSummary = Field(default_factory=ScoreSummary)
    decision_object_health: DecisionObjectHealth = Field(default_factory=DecisionObjectHealth)
    knowledge_health: KnowledgeHealthSummary = Field(default_factory=KnowledgeHealthSummary)
    clarification_summary: ClarificationSummary = Field(default_factory=ClarificationSummary)
    delivery_review_readiness: DeliveryReviewReadiness = Field(
        default_factory=lambda: DeliveryReviewReadiness(project_id="")
    )
    active_risks: list[Risk] = Field(default_factory=list)
    evidence_timeline: list[Evidence] = Field(default_factory=list)
    hypothesis_table: list[WorkspaceHypothesisRow] = Field(default_factory=list)
    approvals_panel: list[ApprovalRecord] = Field(default_factory=list)
    reentry_history: list[dict] = Field(default_factory=list)


class QueueItem(BaseModel):
    project_id: str
    project_name: str
    current_phase: str
    project_status: str
    input_contract: WorkspaceInputContract = Field(default_factory=WorkspaceInputContract)
    response_metadata: WorkspaceResponseMetadata = Field(
        default_factory=lambda: WorkspaceResponseMetadata(response_schema_version="workspace.queue_item.v1")
    )
    workflow_running: bool = False
    blocking_reasons: list[str] = Field(default_factory=list)
    requires_approval: bool = False
    has_stale_downstream: bool = False
    active_risk_count: int = 0
    decision_object_status: str = DecisionObjectStatus.STALE.value
    knowledge_status: str = "unconfigured"
    sqi_overall: Optional[float] = None
    brier_score: Optional[float] = None
    updated_at: str = ""


def build_workspace_summary(state: ProjectState, *, workflow_running: bool = False) -> WorkspaceSummary:
    decision_objects = ensure_decision_objects(state, trigger="workspace")
    current_hash = compute_source_state_hash(state)
    health_status = (
        DecisionObjectStatus.STALE.value
        if decision_objects.source_state_hash and decision_objects.source_state_hash != current_hash
        else decision_objects.status.value if hasattr(decision_objects.status, "value") else str(decision_objects.status)
    )
    decision_object_health = DecisionObjectHealth(
        status=health_status,
        schema_version=decision_objects.schema_version,
        rebuilt_at=decision_objects.rebuilt_at,
        source_state_hash=decision_objects.source_state_hash,
        current_state_hash=current_hash,
        rebuild_error=decision_objects.rebuild_error,
        decision_count=1 if decision_objects.primary_decision else 0,
        evidence_count=len(decision_objects.evidences),
        risk_count=len(decision_objects.risks),
        action_count=len(decision_objects.actions),
        signal_count=len(decision_objects.signals),
        approval_count=len(decision_objects.approvals),
    )

    phase_statuses = {
        phase: status.value if hasattr(status, "value") else str(status)
        for phase, status in (state.phase_status or {}).items()
    }
    blocking_reasons = _blocking_reasons(state, decision_object_health)
    requires_approval = any(
        (approval.status.value if hasattr(approval.status, "value") else str(approval.status)) == "pending"
        for approval in decision_objects.approvals
    )
    has_stale_downstream = any(status == "stale" for status in phase_statuses.values()) or health_status == "stale"
    import_pending, import_pending_phase, import_pending_message = _import_pending_analysis(state)
    knowledge_health = KnowledgeHealthSummary(**build_knowledge_health(state))
    clarification_summary = build_clarification_summary(state)
    retrieval_visibility = build_prompt_facing_retrieval_impact(state)
    project_status = _project_status(state, blocking_reasons, requires_approval, has_stale_downstream)
    input_contract = _input_contract(state)
    report_output = _report_output_metadata(state)
    report_quality = _report_quality_summary(state)
    active_risks = sorted(
        [risk for risk in decision_objects.risks if risk.status == "active"],
        key=lambda risk: (_severity_rank(risk.severity), risk.title.lower()),
        reverse=True,
    )
    evidence_timeline = sorted(
        decision_objects.evidences,
        key=lambda evidence: (evidence.provenance.captured_at, evidence.evidence_id),
        reverse=True,
    )
    hypothesis_table = []
    for hypothesis in state.hypotheses or []:
        probability = None
        if (hypothesis.alpha + hypothesis.beta) > 0:
            probability = round(hypothesis.alpha / (hypothesis.alpha + hypothesis.beta), 4)
        hypothesis_table.append(
            WorkspaceHypothesisRow(
                hypothesis_id=hypothesis.id,
                text=hypothesis.text,
                justification=hypothesis.justification,
                signal=hypothesis.signal,
                status=hypothesis.status,
                evidence_ids=list(hypothesis.evidence_ids or []),
                evidence_count=len(hypothesis.evidence_ids or []),
                probability=probability,
            )
        )

    return WorkspaceSummary(
        project_id=state.project_id,
        project_name=state.project_name,
        current_phase=state.current_phase,
        project_status=project_status,
        input_contract=input_contract,
        response_metadata=_response_metadata("workspace.summary.v1", input_contract),
        report_output=report_output,
        report_quality=report_quality,
        workflow_running=workflow_running,
        phase_statuses=phase_statuses,
        blocking_reasons=blocking_reasons,
        requires_approval=requires_approval,
        has_stale_downstream=has_stale_downstream,
        imported_evidence_pending_analysis=import_pending,
        imported_evidence_pending_phase=import_pending_phase,
        imported_evidence_pending_message=import_pending_message,
        retrieval_visibility=retrieval_visibility,
        active_risk_count=len(active_risks),
        last_reentry_at=_last_reentry_at(state),
        score_summary=ScoreSummary(
            sqi_overall=state.sqi.sqi_overall if state.sqi else None,
            det_score_overall=state.det_scores.overall if state.det_scores else None,
            brier_score=state.brier_score,
            dq_total=_dq_total(state),
        ),
        decision_object_health=decision_object_health,
        knowledge_health=knowledge_health,
        clarification_summary=clarification_summary,
        delivery_review_readiness=build_delivery_review_readiness(state.project_id, state),
        active_risks=active_risks[:12],
        evidence_timeline=evidence_timeline[:24],
        hypothesis_table=hypothesis_table,
        approvals_panel=sorted(
            decision_objects.approvals,
            key=lambda approval: (
                0 if (approval.status.value if hasattr(approval.status, "value") else str(approval.status)) == "pending" else 1,
                approval.scope,
            ),
        ),
        reentry_history=list(state.reentry_triggers_fired or []),
    )


def _dq_total(state: ProjectState) -> Optional[float]:
    """Total decision-quality score, or None when no DQ has been recorded.

    ``state.dq`` is always present as an all-zero default, so an all-zero
    breakdown means "never scored" rather than "scored zero". Reporting 0.0 for
    it would put a real-looking DQ total of zero on the dashboard for every
    project that has not been scored yet, which is a worse claim than admitting
    the score is missing. ``decision_objects`` already treats a positive sum as
    the test for "scores exist"; this uses the same rule.
    """
    if not state.dq:
        return None
    total = float(sum(state.dq.model_dump().values()))
    return total if total > 0 else None


def build_queue_item(state: ProjectState, *, workflow_running: bool = False) -> QueueItem:
    workspace = build_workspace_summary(state, workflow_running=workflow_running)
    return QueueItem(
        project_id=workspace.project_id,
        project_name=workspace.project_name,
        current_phase=workspace.current_phase,
        project_status=workspace.project_status,
        input_contract=workspace.input_contract,
        response_metadata=_response_metadata("workspace.queue_item.v1", workspace.input_contract),
        workflow_running=workspace.workflow_running,
        blocking_reasons=workspace.blocking_reasons,
        requires_approval=workspace.requires_approval,
        has_stale_downstream=workspace.has_stale_downstream,
        active_risk_count=workspace.active_risk_count,
        decision_object_status=workspace.decision_object_health.status,
        knowledge_status=workspace.knowledge_health.status,
        sqi_overall=workspace.score_summary.sqi_overall,
        brier_score=workspace.score_summary.brier_score,
        updated_at=workspace.decision_object_health.rebuilt_at,
    )


def _input_contract(state: ProjectState) -> WorkspaceInputContract:
    raw_metadata = getattr(state, "ingestion_metadata", None)
    metadata_keys = sorted(str(key) for key in raw_metadata.keys()) if isinstance(raw_metadata, dict) else []
    return WorkspaceInputContract(
        contract_version=getattr(state, "ingestion_contract_version", "") or LEGACY_CONTRACT_VERSION,
        source=getattr(state, "ingestion_source", "") or DEFAULT_INGESTION_SOURCE,
        external_case_id=getattr(state, "ingestion_external_case_id", "") or "",
        metadata_keys=metadata_keys,
    )


def _response_metadata(schema_version: str, input_contract: WorkspaceInputContract) -> WorkspaceResponseMetadata:
    return WorkspaceResponseMetadata(
        response_schema_version=schema_version,
        input_contract_version=input_contract.contract_version,
    )


def _blocking_reasons(state: ProjectState, health: DecisionObjectHealth) -> list[str]:
    reasons: list[str] = []
    if state.kill_switch_active:
        reasons.append(f"Kill switch active: {state.kill_switch_reason or 'operator halt'}")
    for phase, status in (state.phase_status or {}).items():
        if (status.value if hasattr(status, "value") else str(status)) == "failed":
            reasons.append(f"Phase {phase} failed")
    if health.status == DecisionObjectStatus.REBUILD_FAILED.value:
        reasons.append(f"Decision object rebuild failed: {health.rebuild_error}")
    caps = state.budget_caps or {}
    consumed = state.budget_consumed or {}
    consecutive_failures = int(consumed.get("consecutive_failures", 0) or 0)
    max_failures = int(caps.get("max_consecutive_failures", 0) or 0)
    if max_failures and consecutive_failures >= max_failures:
        reasons.append(f"Budget circuit breaker open ({consecutive_failures} consecutive failures)")
    for phase_name, breaker in (state.phase_breakers or {}).items():
        if (breaker or {}).get("state") == "open":
            reasons.append(f"Phase breaker open: {phase_name}")
    return reasons


def _report_output_metadata(state: ProjectState) -> WorkspaceReportOutputMetadata:
    current_language = str(getattr(state, "output_language", DEFAULT_OUTPUT_LANGUAGE) or DEFAULT_OUTPUT_LANGUAGE)
    current_mode = str(getattr(state, "report_mode", DEFAULT_REPORT_MODE) or DEFAULT_REPORT_MODE)
    generated_language = _generated_report_metadata_value(getattr(state, "report_output_language", None))
    generated_mode = _generated_report_metadata_value(getattr(state, "report_output_mode", None))
    report_exists = bool(getattr(state, "report", None))
    if not report_exists:
        metadata_status = "not_generated"
        rerun_required = False
    elif generated_language is None or generated_mode is None:
        metadata_status = "legacy_metadata_unknown"
        rerun_required = True
    else:
        metadata_status = "generated"
        rerun_required = current_language != generated_language or current_mode != generated_mode
    notice = ""
    if metadata_status == "legacy_metadata_unknown":
        notice = (
            "Report was generated before output metadata was recorded. "
            f"Rerun the report phase for {current_language} / {current_mode} before using mode-specific exports."
        )
    elif rerun_required:
        notice = (
            f"Report was generated with {generated_language} / {generated_mode}. "
            f"Rerun the report phase for {current_language} / {current_mode} to affect the report."
        )
    return WorkspaceReportOutputMetadata(
        current_output_language=current_language,
        current_report_mode=current_mode,
        metadata_status=metadata_status,
        generated_output_language=generated_language,
        generated_report_mode=generated_mode,
        rerun_required=rerun_required,
        rerun_notice=notice,
    )


def _generated_report_metadata_value(value: object) -> str | None:
    normalized = str(value).strip() if isinstance(value, str) else ""
    return normalized or None


def _report_quality_summary(state: ProjectState) -> WorkspaceReportQualitySummary:
    result = assess_decision_memo_pilot_plan_quality(state)
    findings = [
        WorkspaceReportQualityFinding(
            rule_name=finding.rule_name,
            message=finding.message,
            location=finding.location,
            excerpt=finding.excerpt,
            severity=finding.severity,
        )
        for finding in result.findings[:25]
    ]
    return WorkspaceReportQualitySummary(
        checked=result.checked,
        status=result.status,
        report_mode=result.report_mode,
        output_language=result.output_language,
        finding_count=len(result.findings),
        findings=findings,
    )


def _project_status(
    state: ProjectState,
    blocking_reasons: list[str],
    requires_approval: bool,
    has_stale_downstream: bool,
) -> str:
    if blocking_reasons:
        return "blocked"
    if requires_approval:
        return "review_required"
    if has_stale_downstream:
        return "stale"
    report_status = state.phase_status.get("report")
    if (report_status.value if hasattr(report_status, "value") else str(report_status)) == "completed" and state.report:
        return "completed"
    return "safe_to_proceed"


def _severity_rank(severity: str) -> int:
    return {
        "critical": 4,
        "high": 3,
        "medium": 2,
        "low": 1,
    }.get((severity or "").lower(), 0)


def _last_reentry_at(state: ProjectState) -> Optional[str]:
    for trigger in reversed(state.reentry_triggers_fired or []):
        ts = trigger.get("ts")
        if ts:
            return str(ts)
    return None


def _import_pending_analysis(state: ProjectState) -> tuple[bool, str, str]:
    latest_event = None
    latest_ts = float("-inf")
    for event in state.policy_audit_log or []:
        if event.get("event_type") != "connector_import":
            continue
        details = event.get("details", {}) or {}
        if not details.get("analysis_pending"):
            continue
        if not (details.get("evidence_count") or details.get("signal_count")):
            continue
        try:
            event_ts = float(event.get("ts") or 0.0)
        except (TypeError, ValueError):
            event_ts = 0.0
        if event_ts >= latest_ts:
            latest_event = event
            latest_ts = event_ts

    if latest_event is None:
        return False, "", ""

    details = latest_event.get("details", {}) or {}
    phase = str(details.get("analysis_pending_phase") or "").strip()
    if not phase:
        return False, "", ""

    completed_at = (state.phase_run_completed_at or {}).get(phase, "")
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


def _parse_event_timestamp(value: object) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(value))
    except (TypeError, ValueError, OSError):
        return None


def _parse_iso_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
