"""Summary-first operator overview surface."""
from __future__ import annotations

from pydantic import BaseModel, Field

from explainability import build_explainability_report
from knowledge.files import list_uploaded_files
from workspace import WorkspaceSummary, build_workspace_summary
from state import ProjectState


class OverviewHypothesisRow(BaseModel):
    hypothesis_id: str
    text: str = ""
    status: str = ""
    probability: float | None = None
    justification: str = ""


class OverviewStrategyCard(BaseModel):
    priority: str = ""
    action: str = ""
    expected_impact: str = ""
    risk_if_ignored: str = ""
    justification: str = ""
    evidence_chain: str = ""


class OverviewMetricCard(BaseModel):
    label: str
    value: str = ""
    detail: str = ""


class OverviewJustificationBlock(BaseModel):
    title: str
    summary: str = ""


class OverviewFileRow(BaseModel):
    file_id: str
    filename: str
    role: str = ""
    parser_kind: str = ""
    parse_status: str = ""
    uploaded_at: str = ""
    import_mode: str = ""
    knowledge_item_count: int = 0
    evidence_count: int = 0
    signal_count: int = 0


class OperatorOverviewSummary(BaseModel):
    project_id: str
    project_name: str
    project_status: str = ""
    current_recommendation: str = ""
    decision_summary: str = ""
    why_it_recommends_this: list[OverviewJustificationBlock] = Field(default_factory=list)
    hypotheses: list[OverviewHypothesisRow] = Field(default_factory=list)
    strategy_cards: list[OverviewStrategyCard] = Field(default_factory=list)
    key_metrics: list[OverviewMetricCard] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    sources_and_files_message: str = ""
    files: list[OverviewFileRow] = Field(default_factory=list)
    next_operator_action: str = ""
    workspace: WorkspaceSummary


def build_operator_overview(state: ProjectState) -> OperatorOverviewSummary:
    workspace = build_workspace_summary(state)
    explain = build_explainability_report(state)
    files = [
        OverviewFileRow(
            file_id=manifest.file_id,
            filename=manifest.filename,
            role=manifest.role.value if hasattr(manifest.role, "value") else str(manifest.role),
            parser_kind=manifest.parser_kind,
            parse_status=manifest.parse_summary.status.value if hasattr(manifest.parse_summary.status, "value") else str(manifest.parse_summary.status),
            uploaded_at=manifest.uploaded_at,
            import_mode=manifest.import_mode,
            knowledge_item_count=manifest.parse_summary.knowledge_item_count,
            evidence_count=manifest.parse_summary.evidence_count,
            signal_count=manifest.parse_summary.signal_count,
        )
        for manifest in list_uploaded_files(state)
    ]

    strategy_cards = [
        OverviewStrategyCard(
            priority=str(item.priority.value if hasattr(item.priority, "value") else item.priority or ""),
            action=item.action,
            expected_impact=item.expected_impact,
            risk_if_ignored=item.risk_if_ignored,
            justification=item.justification,
            evidence_chain=item.evidence_chain,
        )
        for item in ((state.strategy.strategies if state.strategy else []) or [])
    ]
    hypotheses = list(workspace.hypothesis_table or [])
    metric_cards = [
        OverviewMetricCard(label="SQI", value=_metric_value(workspace.score_summary.sqi_overall), detail="Strategy quality index"),
        OverviewMetricCard(label="Deterministic score", value=_metric_value(workspace.score_summary.det_score_overall), detail="Rule-based coherence and actionability"),
        OverviewMetricCard(label="Brier", value=_metric_value(workspace.score_summary.brier_score), detail="Calibration on resolved predictions"),
        OverviewMetricCard(label="DQ total", value=_metric_value(workspace.score_summary.dq_total), detail="Decision quality composite"),
    ]
    if state.monitor is not None:
        metric_cards.append(
            OverviewMetricCard(
                label="Commitment",
                value=_metric_value(state.monitor.commitment_score),
                detail=state.monitor.commitment_rationale or "Monitoring commitment",
            )
        )
    for impact in workspace.retrieval_visibility or []:
        metric_cards.append(
            OverviewMetricCard(
                label=f"{impact.phase.title()} retrieval",
                value=f"{impact.used_item_count} used / {impact.eligible_count} eligible",
                detail=impact.overview,
            )
        )

    justification_blocks = [
        OverviewJustificationBlock(
            title="Executive strategy",
            summary=(state.strategy.executive_strategy if state.strategy else "") or "No strategy recommendation yet.",
        )
    ]
    for item in (explain.strategy_explanations or [])[:3]:
        justification_blocks.append(
            OverviewJustificationBlock(
                title=item.claim or "Recommendation",
                summary=item.justification or item.evidence_chain or "No explicit justification recorded.",
            )
        )

    current_recommendation = (
        (state.strategy.executive_strategy if state.strategy else "")
        or (state.report.splitlines()[0].strip("# ").strip() if state.report else "")
        or "No recommendation generated yet."
    )
    next_operator_action = _next_operator_action(workspace)
    sources_message = _sources_message(workspace, files)

    return OperatorOverviewSummary(
        project_id=state.project_id,
        project_name=state.project_name,
        project_status=workspace.project_status,
        current_recommendation=current_recommendation,
        decision_summary=explain.overview or workspace.imported_evidence_pending_message or "No decision summary available yet.",
        why_it_recommends_this=justification_blocks,
        hypotheses=[
            OverviewHypothesisRow(
                hypothesis_id=item.hypothesis_id,
                text=item.text,
                status=item.status,
                probability=item.probability,
                justification=item.justification,
            )
            for item in hypotheses
        ],
        strategy_cards=strategy_cards,
        key_metrics=metric_cards,
        open_questions=(
            list(explain.uncertainty_summary.open_questions or [])
            + list(explain.uncertainty_summary.missing_evidence or [])
            + list(explain.uncertainty_summary.monitor_next or [])
        )[:10],
        sources_and_files_message=sources_message,
        files=files,
        next_operator_action=next_operator_action,
        workspace=workspace,
    )


def _metric_value(value) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def _sources_message(workspace: WorkspaceSummary, files: list[OverviewFileRow]) -> str:
    message_parts = [
        workspace.knowledge_health.message or "No knowledge-source message.",
    ]
    if workspace.imported_evidence_pending_analysis:
        message_parts.append(workspace.imported_evidence_pending_message or "Imported evidence requires rerun to affect downstream outputs.")
    if files:
        message_parts.append(f"{len(files)} uploaded file(s) available.")
    else:
        message_parts.append("No uploaded files yet.")
    return " ".join(part for part in message_parts if part)


def _next_operator_action(workspace: WorkspaceSummary) -> str:
    if workspace.blocking_reasons:
        return f"Resolve blocking issue: {workspace.blocking_reasons[0]}"
    if workspace.requires_approval:
        return "Review and resolve pending approvals before proceeding."
    if workspace.imported_evidence_pending_analysis:
        return workspace.imported_evidence_pending_message or "Rerun analysis to incorporate imported evidence."
    if workspace.has_stale_downstream:
        return "Rerun the stale downstream phases when ready."
    if workspace.project_status == "completed":
        return "Review the report, trace, and exports before sharing externally."
    return "Continue with the next pending phase or upload more context if needed."
