"""Derived explainability and decision-trace views.

This layer is backend-derived from existing persisted artifacts. It does not
expose raw chain-of-thought and does not change orchestration semantics.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from pydantic import BaseModel, Field

from config import FRAMEWORKS_BY_PHASE, GATE_CONFIGS
from decision_objects import ensure_decision_objects
from knowledge.freshness import build_knowledge_health
from knowledge.retrieval import RetrievalPhaseImpactSummary, build_phase_retrieval_impact
from state import ProjectState
from tools.scoring import check_gate


TRACE_PHASE_SEQUENCE = (
    "classify", "hypotheses", "gauntlet", "audit",
    "strategy", "sqi", "monitor", "report",
)
PROMPT_FACING_KNOWLEDGE_PHASES = ("audit", "strategy")

PHASE_PURPOSES = {
    "classify": "Frame the decision domain, operating context, and problem structure.",
    "hypotheses": "Generate falsifiable hypotheses and define what would confirm or reject them.",
    "gauntlet": "Stress-test the riskiest hypotheses with adversarial and failure-oriented frameworks.",
    "audit": "Enumerate failure modes, operational risks, and observation needs.",
    "strategy": "Turn the working hypotheses and audit into recommendations and implementation actions.",
    "sqi": "Score strategy quality with deterministic and evaluative checks.",
    "monitor": "Define monitoring, canaries, circuit breakers, and re-entry watch signals.",
    "report": "Synthesize the decision dossier into the final operator-facing report.",
}


@dataclass(frozen=True)
class _InputDescriptor:
    label: str
    kind: str


PHASE_INPUTS: dict[str, list[_InputDescriptor]] = {
    "classify": [
        _InputDescriptor("brief", "project_input"),
        _InputDescriptor("data", "project_input"),
    ],
    "hypotheses": [
        _InputDescriptor("brief", "project_input"),
        _InputDescriptor("data", "project_input"),
        _InputDescriptor("classify", "phase_output"),
    ],
    "gauntlet": [
        _InputDescriptor("hypotheses", "phase_output"),
        _InputDescriptor("brief", "project_input"),
    ],
    "audit": [
        _InputDescriptor("classify", "phase_output"),
        _InputDescriptor("hypotheses", "phase_output"),
        _InputDescriptor("gauntlet", "phase_output"),
        _InputDescriptor("data", "project_input"),
    ],
    "strategy": [
        _InputDescriptor("classify", "phase_output"),
        _InputDescriptor("hypotheses", "phase_output"),
        _InputDescriptor("gauntlet", "phase_output"),
        _InputDescriptor("audit", "phase_output"),
        _InputDescriptor("data", "project_input"),
    ],
    "sqi": [
        _InputDescriptor("strategy", "phase_output"),
        _InputDescriptor("classify", "phase_output"),
        _InputDescriptor("brief", "project_input"),
    ],
    "monitor": [
        _InputDescriptor("strategy", "phase_output"),
        _InputDescriptor("observations", "operator_input"),
        _InputDescriptor("timer_logs", "operator_input"),
        _InputDescriptor("brief", "project_input"),
    ],
    "report": [
        _InputDescriptor("classify", "phase_output"),
        _InputDescriptor("hypotheses", "phase_output"),
        _InputDescriptor("gauntlet", "phase_output"),
        _InputDescriptor("audit", "phase_output"),
        _InputDescriptor("strategy", "phase_output"),
        _InputDescriptor("monitor", "phase_output"),
        _InputDescriptor("observations", "operator_input"),
        _InputDescriptor("timer_logs", "operator_input"),
        _InputDescriptor("brief", "project_input"),
    ],
}


class TraceInputSummary(BaseModel):
    label: str
    kind: str
    summary: str = ""


class GateTraceSummary(BaseModel):
    configured: bool = False
    kind: str = "deterministic"
    passed: bool = True
    blocking: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    source: str = "current_check"
    note: str = ""


class UncertaintySummary(BaseModel):
    open_questions: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    would_change_conclusion: list[str] = Field(default_factory=list)
    monitor_next: list[str] = Field(default_factory=list)


class LogicSeparationSummary(BaseModel):
    deterministic_logic: list[str] = Field(default_factory=list)
    model_judgment: list[str] = Field(default_factory=list)
    policy_enforcement: list[str] = Field(default_factory=list)
    runtime_metadata: list[str] = Field(default_factory=list)
    knowledge_inputs: list[str] = Field(default_factory=list)


class KnowledgeUsageSummary(BaseModel):
    item_id: str = ""
    source_id: str = ""
    source_name: str = ""
    title: str = ""
    observed_at: str = ""
    trust_tier: str = ""
    sensitivity: str = ""
    fact_keys: list[str] = Field(default_factory=list)


class PhaseTraceSummary(BaseModel):
    phase: str
    purpose: str = ""
    status: str = "pending"
    inputs_used: list[TraceInputSummary] = Field(default_factory=list)
    frameworks_used: list[str] = Field(default_factory=list)
    output_summary: str = ""
    confidence: Optional[float] = None
    gate_result: GateTraceSummary = Field(default_factory=GateTraceSummary)
    next_step: str = ""
    retrieval_impact: Optional[RetrievalPhaseImpactSummary] = None
    knowledge_usage: list[KnowledgeUsageSummary] = Field(default_factory=list)
    logic_separation: LogicSeparationSummary = Field(default_factory=LogicSeparationSummary)
    uncertainty: UncertaintySummary = Field(default_factory=UncertaintySummary)


class SupportingEvidenceSummary(BaseModel):
    evidence_id: str = ""
    title: str = ""
    summary: str = ""
    source_phase: str = ""
    source_type: str = ""


class ActionExplanation(BaseModel):
    action_id: str = ""
    claim: str = ""
    priority: str = ""
    justification: str = ""
    evidence_chain: str = ""
    supporting_evidence: list[SupportingEvidenceSummary] = Field(default_factory=list)
    supporting_findings: list[str] = Field(default_factory=list)
    deterministic_checks: list[str] = Field(default_factory=list)
    confidence_label: str = ""
    confidence_score: Optional[float] = None
    uncertainty: UncertaintySummary = Field(default_factory=UncertaintySummary)


class ProjectTrace(BaseModel):
    project_id: str
    project_name: str
    current_phase: str
    phases: list[PhaseTraceSummary] = Field(default_factory=list)


class ExplainabilityReport(BaseModel):
    project_id: str
    project_name: str
    current_phase: str
    overview: str = ""
    phase_traces: list[PhaseTraceSummary] = Field(default_factory=list)
    strategy_explanations: list[ActionExplanation] = Field(default_factory=list)
    uncertainty_summary: UncertaintySummary = Field(default_factory=UncertaintySummary)
    logic_separation: LogicSeparationSummary = Field(default_factory=LogicSeparationSummary)
    policy_highlights: list[str] = Field(default_factory=list)
    runtime_summary: list[str] = Field(default_factory=list)


def build_project_trace(state: ProjectState) -> ProjectTrace:
    traces = [build_phase_trace(state, phase) for phase in TRACE_PHASE_SEQUENCE]
    return ProjectTrace(
        project_id=state.project_id,
        project_name=state.project_name,
        current_phase=state.current_phase,
        phases=traces,
    )


def build_phase_trace(state: ProjectState, phase: str) -> PhaseTraceSummary:
    phase = phase.strip().lower()
    if phase not in TRACE_PHASE_SEQUENCE:
        raise KeyError(phase)

    status = _normalized_status(state.phase_status.get(phase, "pending"))
    confidence = state.phase_confidence.get(phase)
    gate_trace = _gate_trace(state, phase, status, confidence)
    return PhaseTraceSummary(
        phase=phase,
        purpose=PHASE_PURPOSES.get(phase, ""),
        status=status,
        inputs_used=_phase_inputs(state, phase),
        frameworks_used=list(FRAMEWORKS_BY_PHASE.get(phase, [])),
        output_summary=_phase_output_summary(state, phase),
        confidence=confidence,
        gate_result=gate_trace,
        next_step=_next_step(state, phase, status),
        retrieval_impact=_phase_retrieval_impact(state, phase),
        knowledge_usage=_phase_knowledge_usage(state, phase),
        logic_separation=_logic_for_phase(state, phase, gate_trace),
        uncertainty=_phase_uncertainty(state, phase),
    )


def _gate_trace(
    state: ProjectState,
    phase: str,
    status: str,
    confidence: Optional[float],
) -> GateTraceSummary:
    gate = check_gate(state, phase)
    configured = phase in GATE_CONFIGS
    passed = bool(gate.get("passed", True))
    blocking = list(gate.get("blocking", []))
    gate_confidence = float(gate.get("confidence", confidence or 0.0) or 0.0)
    source = "current_check"
    note = ""

    # Explainability should not imply that a phase was blocked when the recorded
    # workflow state shows that it completed and downstream work proceeded.
    if status == "completed" and not passed:
        passed = True
        blocking = []
        gate_confidence = max(gate_confidence, 1.0)
        source = "recorded_completion"
        note = (
            "This phase completed in the recorded workflow. The current deterministic "
            "gate recomputation differs from the persisted completed outcome."
        )

    return GateTraceSummary(
        configured=configured,
        passed=passed,
        blocking=blocking,
        confidence=gate_confidence,
        source=source,
        note=note,
    )


def build_explainability_report(state: ProjectState) -> ExplainabilityReport:
    traces = [build_phase_trace(state, phase) for phase in TRACE_PHASE_SEQUENCE]
    return ExplainabilityReport(
        project_id=state.project_id,
        project_name=state.project_name,
        current_phase=state.current_phase,
        overview=_overview(state, traces),
        phase_traces=traces,
        strategy_explanations=_strategy_action_explanations(state),
        uncertainty_summary=_project_uncertainty(state),
        logic_separation=_project_logic(state, traces),
        policy_highlights=_policy_highlights(state),
        runtime_summary=_runtime_summary(state),
    )


def _phase_inputs(state: ProjectState, phase: str) -> list[TraceInputSummary]:
    items: list[TraceInputSummary] = []
    for descriptor in PHASE_INPUTS.get(phase, []):
        summary = _input_summary(state, descriptor.label)
        if not summary:
            continue
        items.append(TraceInputSummary(label=descriptor.label, kind=descriptor.kind, summary=summary))
    return items


def _input_summary(state: ProjectState, label: str) -> str:
    if label == "brief":
        return _clip(state.brief, 140)
    if label == "data":
        return _clip(state.data, 140)
    if label == "observations":
        if not state.observations:
            return ""
        return f"{len(state.observations)} observation(s)"
    if label == "timer_logs":
        if not state.timer_logs:
            return ""
        return f"{len(state.timer_logs)} timer log entry(ies)"
    if label == "report":
        return _clip(state.report or "", 180)
    return _clip(state.phase_summaries.get(label, ""), 180)


def _phase_output_summary(state: ProjectState, phase: str) -> str:
    if phase == "report" and state.report:
        return _clip(state.report, 280)
    return state.phase_summaries.get(phase, "") or _fallback_output_summary(state, phase)


def _fallback_output_summary(state: ProjectState, phase: str) -> str:
    value = getattr(state, phase, None)
    if isinstance(value, list):
        return f"{len(value)} item(s) generated"
    if value is None:
        return ""
    return f"{phase} output present"


def _next_step(state: ProjectState, phase: str, status: str) -> str:
    if status == "failed":
        return f"Resolve blockers and rerun {phase}."
    if status == "stale":
        return f"Upstream changes invalidated {phase}; rerun from this phase."
    if status == "running":
        return f"{phase} is currently running."
    if status == "pending":
        return f"Run {phase}."

    phase_index = TRACE_PHASE_SEQUENCE.index(phase)
    for downstream in TRACE_PHASE_SEQUENCE[phase_index + 1:]:
        downstream_status = _normalized_status(state.phase_status.get(downstream, "pending"))
        if downstream_status != "completed":
            return f"Proceed to {downstream}."
    if phase == "report":
        return "Review the report, approvals, and monitoring plan."
    if phase_index < len(TRACE_PHASE_SEQUENCE) - 1:
        return f"Proceed to {TRACE_PHASE_SEQUENCE[phase_index + 1]}."
    return "No further workflow step is required."


def _logic_for_phase(state: ProjectState, phase: str, gate: GateTraceSummary) -> LogicSeparationSummary:
    knowledge = build_knowledge_health(state)
    retrieval_impact = _phase_retrieval_impact(state, phase)
    knowledge_usage = _phase_knowledge_usage(state, phase)
    policy_events = _phase_policy_events(state, phase)
    deterministic_logic = [
        f"Gate check ({'configured' if gate.configured else 'not configured'}): passed={gate.passed}",
        f"Phase status: {_normalized_status(state.phase_status.get(phase, 'pending'))}",
    ]
    if gate.blocking:
        deterministic_logic.append("Blocking conditions: " + "; ".join(gate.blocking[:4]))
    if phase == "strategy" and state.det_scores:
        deterministic_logic.append(
            f"Deterministic strategy scores: overall={state.det_scores.overall}, evidence_linkage={state.det_scores.evidence_linkage}, actionability={state.det_scores.actionability}"
        )
    if phase == "sqi" and state.sqi:
        deterministic_logic.append(f"SQI overall: {state.sqi.sqi_overall}")

    model_judgment: list[str] = []
    output_summary = _phase_output_summary(state, phase)
    if output_summary:
        model_judgment.append(f"Output summary: {output_summary}")
    if phase == "classify" and state.classify:
        model_judgment.append(f"Classification justification: {_clip(state.classify.justification, 160)}")
    if phase == "hypotheses" and state.hypotheses:
        model_judgment.append(f"Hypotheses generated: {len(state.hypotheses)}")
    if phase == "strategy" and state.strategy:
        model_judgment.append(f"Executive strategy: {_clip(state.strategy.executive_strategy, 160)}")
        model_judgment.append(f"Recommendations proposed: {len(state.strategy.strategies)}")
    if phase == "monitor" and state.monitor:
        model_judgment.append(f"Commitment rationale: {_clip(state.monitor.commitment_rationale, 160)}")

    runtime_metadata = []
    completed_at = (state.phase_run_completed_at or {}).get(phase, "")
    if completed_at:
        runtime_metadata.append(f"Completed at: {completed_at}")
    runtime_metadata.append(
        f"Project runtime counters: llm_calls={int((state.budget_consumed or {}).get('llm_call_count', 0) or 0)}, tokens={int((state.budget_consumed or {}).get('total_tokens', 0) or 0)}"
    )

    knowledge_inputs = []
    if phase in PROMPT_FACING_KNOWLEDGE_PHASES and knowledge_usage:
        knowledge_inputs.append(
            f"{phase.title()} prompt used retrieval-approved knowledge item(s): "
            + "; ".join(
                f"{item.title or item.item_id} ({item.source_name or item.source_id})"
                for item in knowledge_usage[:4]
            )
        )
        if retrieval_impact and retrieval_impact.blocked_reason_summary:
            knowledge_inputs.append(
                f"{phase.title()} blocked reasons: " + "; ".join(retrieval_impact.blocked_reason_summary[:3])
            )
    elif phase in PROMPT_FACING_KNOWLEDGE_PHASES and knowledge.get("item_count", 0):
        knowledge_inputs.append(
            f"Knowledge layer has {knowledge.get('item_count', 0)} item(s), status={knowledge.get('status', 'unknown')}; {phase} prompt used no retrieval-approved knowledge items."
        )
        if retrieval_impact and retrieval_impact.blocked_reason_summary:
            knowledge_inputs.append(
                f"{phase.title()} blocked reasons: " + "; ".join(retrieval_impact.blocked_reason_summary[:3])
            )
    elif knowledge.get("item_count", 0):
        knowledge_inputs.append(
            f"Knowledge layer has {knowledge.get('item_count', 0)} item(s), status={knowledge.get('status', 'unknown')}; not yet used in prompt-facing reasoning."
        )

    return LogicSeparationSummary(
        deterministic_logic=deterministic_logic,
        model_judgment=model_judgment,
        policy_enforcement=policy_events,
        runtime_metadata=runtime_metadata,
        knowledge_inputs=knowledge_inputs,
    )


def _phase_uncertainty(state: ProjectState, phase: str) -> UncertaintySummary:
    if phase == "classify" and state.classify:
        return UncertaintySummary(
            open_questions=_as_list(state.classify.expectancy_violations),
            missing_evidence=_missing_phase_evidence(state, phase),
            would_change_conclusion=[],
            monitor_next=[],
        )
    if phase == "hypotheses" and state.hypotheses:
        return UncertaintySummary(
            open_questions=[f"{h.id}: {_clip(h.justification or h.text, 120)}" for h in state.hypotheses[:4] if h.status == "OPEN"],
            missing_evidence=_missing_phase_evidence(state, phase),
            would_change_conclusion=[
                f"{h.id}: confirm={_clip(h.confirm, 80)}; reject={_clip(h.reject, 80)}"
                for h in state.hypotheses[:4]
                if h.confirm or h.reject
            ],
            monitor_next=[_clip(h.signal, 80) for h in state.hypotheses[:4] if h.signal],
        )
    if phase == "audit" and state.audit:
        return UncertaintySummary(
            open_questions=[_clip(item, 140) for item in state.audit.top_findings[:3]],
            missing_evidence=_missing_phase_evidence(state, phase),
            would_change_conclusion=[],
            monitor_next=[_clip(item, 140) for item in state.audit.observation_needs[:4]],
        )
    if phase == "strategy":
        return _project_uncertainty(state)
    if phase == "monitor" and state.monitor:
        return UncertaintySummary(
            open_questions=[f"Re-entry watch: {item}" for item in state.monitor.reentry_watch[:4]],
            missing_evidence=[],
            would_change_conclusion=[],
            monitor_next=[_clip(canary.signal, 80) for canary in state.monitor.canaries[:5]],
        )
    return UncertaintySummary(
        missing_evidence=_missing_phase_evidence(state, phase),
    )


def _missing_phase_evidence(state: ProjectState, phase: str) -> list[str]:
    missing: list[str] = []
    if phase == "hypotheses" and state.hypotheses:
        for hypothesis in state.hypotheses:
            if not (hypothesis.evidence_ids or []):
                missing.append(f"{hypothesis.id} has no linked evidence objects yet.")
    if phase == "strategy" and state.strategy:
        decision_objects = ensure_decision_objects(state, trigger="explainability.strategy")
        evidence_by_id = {item.evidence_id: item for item in decision_objects.evidences}
        for action in decision_objects.actions:
            if not action.evidence_ids:
                missing.append(f"Action '{action.title}' has no linked evidence objects.")
            elif not any(evidence_by_id.get(item_id) for item_id in action.evidence_ids):
                missing.append(f"Action '{action.title}' links to evidence IDs that are not currently materialized.")
    return missing[:6]


def _strategy_action_explanations(state: ProjectState) -> list[ActionExplanation]:
    if not state.strategy:
        return []

    decision_objects = ensure_decision_objects(state, trigger="explainability.strategy")
    evidence_by_id = {item.evidence_id: item for item in decision_objects.evidences}
    explanations: list[ActionExplanation] = []
    strategy_gate = _gate_trace(
        state,
        "strategy",
        _normalized_status(state.phase_status.get("strategy", "pending")),
        state.phase_confidence.get("strategy"),
    )
    for index, action in enumerate(state.strategy.strategies):
        if not decision_objects.actions:
            break
        linked_action = _match_decision_action(action, decision_objects.actions, index)
        evidence_items = [
            evidence_by_id[evidence_id]
            for evidence_id in linked_action.evidence_ids
            if evidence_id in evidence_by_id
        ]
        linked_hypotheses = set(linked_action.linked_hypothesis_ids or [])
        supporting_findings = _supporting_findings_for_action(state, linked_hypotheses, evidence_items)
        explanations.append(
            ActionExplanation(
                action_id=linked_action.action_id,
                claim=action.action,
                priority=action.priority.value if hasattr(action.priority, "value") else str(action.priority or ""),
                justification=action.justification,
                evidence_chain=action.evidence_chain,
                supporting_evidence=[
                    SupportingEvidenceSummary(
                        evidence_id=item.evidence_id,
                        title=item.title,
                        summary=item.summary,
                        source_phase=item.source_phase,
                        source_type=item.provenance.source_type,
                    )
                    for item in evidence_items[:8]
                ],
                supporting_findings=supporting_findings,
                deterministic_checks=_strategy_deterministic_checks(state, strategy_gate, linked_action, evidence_items),
                confidence_label=state.strategy.confidence or "",
                confidence_score=state.phase_confidence.get("strategy"),
                uncertainty=_uncertainty_for_action(state, linked_hypotheses, evidence_items),
            )
        )
    return explanations


def _match_decision_action(action, decision_actions, fallback_index: int):
    priority = action.priority.value if hasattr(action.priority, "value") else str(action.priority or "")
    for candidate in decision_actions:
        if candidate.title == action.action and candidate.priority == priority:
            return candidate
    if fallback_index < len(decision_actions):
        return decision_actions[fallback_index]
    return decision_actions[-1]


def _strategy_deterministic_checks(
    state: ProjectState,
    gate: GateTraceSummary,
    action,
    evidence_items: list,
) -> list[str]:
    checks = [
        f"Strategy gate passed={gate.passed}; blocking={len(gate.blocking or [])}; source={gate.source}",
        f"Linked evidence objects: {len(evidence_items)}",
        f"Linked hypotheses: {len(action.linked_hypothesis_ids or [])}",
    ]
    if gate.note:
        checks.append(f"Gate note: {gate.note}")
    if state.det_scores:
        checks.extend(
            [
                f"Det score overall={state.det_scores.overall}",
                f"Evidence linkage={state.det_scores.evidence_linkage}",
                f"Actionability={state.det_scores.actionability}",
            ]
        )
        if state.det_scores.contradictions:
            checks.append("Contradictions flagged: " + "; ".join(state.det_scores.contradictions[:3]))
    return checks


def _supporting_findings_for_action(state: ProjectState, linked_hypotheses: set[str], evidence_items: list) -> list[str]:
    findings: list[str] = []
    for verdict in state.strategy.preliminary_verdicts if state.strategy else []:
        if verdict.id in linked_hypotheses:
            verdict_label = verdict.verdict.value if hasattr(verdict.verdict, "value") else str(verdict.verdict)
            findings.append(f"{verdict.id}: {verdict_label} — {_clip(verdict.evidence or verdict.monitoring_plan, 140)}")
    for result in state.gauntlet.results if state.gauntlet else []:
        if result.id in linked_hypotheses:
            mode = (result.top_fmea or {}).get("mode", "")
            rpn = (result.top_fmea or {}).get("rpn", "")
            detail = f"{result.id}: crux={_clip(result.crux, 120)}"
            if mode or rpn:
                detail += f" | top_fmea={mode} RPN={rpn}"
            findings.append(detail)
    if state.audit:
        for finding in state.audit.top_findings[:3]:
            if any(item.source_phase == "audit" for item in evidence_items):
                findings.append(f"Audit finding: {_clip(finding, 140)}")
    return _dedupe(findings)[:8]


def _uncertainty_for_action(state: ProjectState, linked_hypotheses: set[str], evidence_items: list) -> UncertaintySummary:
    open_questions: list[str] = []
    missing_evidence: list[str] = []
    would_change: list[str] = []
    monitor_next: list[str] = []

    if not evidence_items:
        missing_evidence.append("No linked evidence objects are currently attached to this recommendation.")
    for hypothesis in state.hypotheses or []:
        if hypothesis.id not in linked_hypotheses:
            continue
        if hypothesis.status == "OPEN":
            open_questions.append(f"{hypothesis.id} remains OPEN.")
        if hypothesis.confirm or hypothesis.reject:
            would_change.append(
                f"{hypothesis.id}: confirm={_clip(hypothesis.confirm, 90)}; reject={_clip(hypothesis.reject, 90)}"
            )
        if hypothesis.signal:
            monitor_next.append(f"Track {hypothesis.signal}")
        if not (hypothesis.evidence_ids or []):
            missing_evidence.append(f"{hypothesis.id} has no linked evidence objects yet.")
    for verdict in state.strategy.preliminary_verdicts if state.strategy else []:
        if verdict.id in linked_hypotheses and verdict.monitoring_plan:
            monitor_next.append(_clip(verdict.monitoring_plan, 120))
    if state.audit and state.audit.observation_needs:
        monitor_next.extend(_clip(item, 120) for item in state.audit.observation_needs[:3])
    return UncertaintySummary(
        open_questions=_dedupe(open_questions)[:6],
        missing_evidence=_dedupe(missing_evidence)[:6],
        would_change_conclusion=_dedupe(would_change)[:6],
        monitor_next=_dedupe(monitor_next)[:6],
    )


def _project_uncertainty(state: ProjectState) -> UncertaintySummary:
    open_questions: list[str] = []
    missing_evidence: list[str] = []
    would_change: list[str] = []
    monitor_next: list[str] = []

    if state.gauntlet and state.gauntlet.mece_gaps:
        open_questions.append(_clip(state.gauntlet.mece_gaps, 140))
    if state.audit:
        open_questions.extend(_clip(item, 140) for item in state.audit.top_findings[:3])
        monitor_next.extend(_clip(item, 120) for item in state.audit.observation_needs[:4])
    if state.strategy:
        for verdict in state.strategy.preliminary_verdicts:
            verdict_label = verdict.verdict.value if hasattr(verdict.verdict, "value") else str(verdict.verdict)
            if verdict_label == "NEEDS_MONITORING":
                open_questions.append(f"{verdict.id}: {_clip(verdict.evidence or verdict.monitoring_plan, 140)}")
            if verdict.monitoring_plan:
                monitor_next.append(_clip(verdict.monitoring_plan, 120))
        if state.strategy.reentry_check:
            open_questions.append(f"Re-entry check: {_clip(state.strategy.reentry_check, 120)}")
    if state.monitor:
        monitor_next.extend(_clip(canary.signal, 80) for canary in state.monitor.canaries[:5])
    for hypothesis in state.hypotheses or []:
        if not (hypothesis.evidence_ids or []):
            missing_evidence.append(f"{hypothesis.id} has no linked evidence objects yet.")
        if hypothesis.confirm or hypothesis.reject:
            would_change.append(
                f"{hypothesis.id}: confirm={_clip(hypothesis.confirm, 90)}; reject={_clip(hypothesis.reject, 90)}"
            )
    knowledge = build_knowledge_health(state)
    if knowledge.get("status") in {"stale", "expired", "sync_failed"}:
        missing_evidence.append(
            f"Knowledge layer status is {knowledge.get('status')}; external current-awareness inputs may need review."
        )
    return UncertaintySummary(
        open_questions=_dedupe(open_questions)[:8],
        missing_evidence=_dedupe(missing_evidence)[:8],
        would_change_conclusion=_dedupe(would_change)[:8],
        monitor_next=_dedupe(monitor_next)[:8],
    )


def _project_logic(state: ProjectState, traces: list[PhaseTraceSummary]) -> LogicSeparationSummary:
    deterministic = _dedupe(
        item
        for trace in traces
        for item in trace.logic_separation.deterministic_logic
    )
    model = _dedupe(
        item
        for trace in traces
        for item in trace.logic_separation.model_judgment
    )
    policy = _dedupe(_policy_highlights(state))
    runtime = _runtime_summary(state)
    knowledge = []
    knowledge_health = build_knowledge_health(state)
    prompt_usage_notes = []
    for phase in PROMPT_FACING_KNOWLEDGE_PHASES:
        phase_usage = _phase_knowledge_usage(state, phase)
        if phase_usage:
            prompt_usage_notes.append(
                f"{phase.title()} used retrieval-approved knowledge item(s): "
                + "; ".join(
                    f"{item.title or item.item_id} ({item.source_name or item.source_id})"
                    for item in phase_usage[:4]
                )
            )
    if prompt_usage_notes:
        knowledge.extend(prompt_usage_notes[:6])
    elif knowledge_health.get("source_count", 0):
        knowledge.append(
            f"Knowledge layer status={knowledge_health.get('status', 'unknown')} with {knowledge_health.get('item_count', 0)} item(s); not yet used in prompt-facing reasoning."
        )
    return LogicSeparationSummary(
        deterministic_logic=deterministic[:12],
        model_judgment=model[:12],
        policy_enforcement=policy[:12],
        runtime_metadata=runtime[:12],
        knowledge_inputs=knowledge[:6],
    )


def _policy_highlights(state: ProjectState) -> list[str]:
    highlights = [
        f"Risk classification: {state.risk_classification}",
    ]
    if state.kill_switch_active:
        highlights.append(f"Kill switch active: {state.kill_switch_reason or 'operator halt'}")
    if state.approvals_granted:
        highlights.append(f"Approvals granted: {len(state.approvals_granted)}")
    for event in reversed(state.policy_audit_log or []):
        event_type = str(event.get("event_type") or "")
        if event_type in {"policy_gate_blocked", "approval_granted", "connector_import", "knowledge_sync", "knowledge_sync_batch", "knowledge_source_upserted", "knowledge_retrieval_used"}:
            details = event.get("details", {}) or {}
            highlights.append(f"{event_type}: {_clip(str(details), 160)}")
        if len(highlights) >= 8:
            break
    return _dedupe(highlights)


def _runtime_summary(state: ProjectState) -> list[str]:
    consumed = state.budget_consumed or {}
    summary = [
        f"LLM calls: {int(consumed.get('llm_call_count', 0) or 0)}",
        f"Total tokens: {int(consumed.get('total_tokens', 0) or 0)}",
        f"Total cost (USD): {float(consumed.get('total_cost_usd', 0.0) or 0.0):.4f}",
        f"Current phase: {state.current_phase}",
    ]
    if state.phase_run_completed_at:
        latest_phase = max(state.phase_run_completed_at.items(), key=lambda item: item[1])[0]
        summary.append(f"Latest completed phase: {latest_phase}")
    return summary


def _phase_policy_events(state: ProjectState, phase: str) -> list[str]:
    items: list[str] = []
    for event in reversed(state.policy_audit_log or []):
        if str(event.get("phase") or "") != phase:
            continue
        event_type = str(event.get("event_type") or "")
        details = event.get("details", {}) or {}
        items.append(f"{event_type}: {_clip(str(details), 140)}")
        if len(items) >= 4:
            break
    return items


def _overview(state: ProjectState, traces: list[PhaseTraceSummary]) -> str:
    completed = sum(1 for trace in traces if trace.status == "completed")
    usage_notes = []
    for phase in PROMPT_FACING_KNOWLEDGE_PHASES:
        phase_usage = _phase_knowledge_usage(state, phase)
        if phase_usage:
            usage_notes.append(f"{phase} used {len(phase_usage)} retrieval-approved knowledge item(s)")
    retrieval_note = ""
    if usage_notes:
        retrieval_note = " " + "; ".join(note.capitalize() for note in usage_notes) + "."
    return (
        f"{completed}/{len(traces)} phases have completed. "
        f"Current phase is {state.current_phase}. "
        f"Trace summaries separate deterministic checks from model judgment and policy events."
        f"{retrieval_note}"
    )


def _phase_knowledge_usage(state: ProjectState, phase: str) -> list[KnowledgeUsageSummary]:
    normalized_phase = (phase or "").strip().lower()
    if normalized_phase not in PROMPT_FACING_KNOWLEDGE_PHASES:
        return []

    for event in reversed(state.policy_audit_log or []):
        if str(event.get("event_type") or "") != "knowledge_retrieval_used":
            continue
        details = event.get("details", {}) or {}
        if str(details.get("phase") or event.get("phase") or "").strip().lower() != normalized_phase:
            continue
        items = details.get("used_items", []) or []
        usage: list[KnowledgeUsageSummary] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            usage.append(
                KnowledgeUsageSummary(
                    item_id=str(item.get("item_id") or ""),
                    source_id=str(item.get("source_id") or ""),
                    source_name=str(item.get("source_name") or ""),
                    title=str(item.get("title") or ""),
                    observed_at=str(item.get("observed_at") or ""),
                    trust_tier=str(item.get("trust_tier") or ""),
                    sensitivity=str(item.get("sensitivity") or ""),
                    fact_keys=[str(value) for value in (item.get("fact_keys") or []) if str(value).strip()],
                )
            )
        return usage
    return []


def _phase_retrieval_impact(state: ProjectState, phase: str) -> Optional[RetrievalPhaseImpactSummary]:
    normalized_phase = (phase or "").strip().lower()
    if normalized_phase not in PROMPT_FACING_KNOWLEDGE_PHASES:
        return None
    return build_phase_retrieval_impact(state, normalized_phase)


def _normalized_status(status: object) -> str:
    return str(getattr(status, "value", status or "pending")).lower()


def _as_list(value: object) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [_clip(str(item), 140) for item in value if str(item).strip()]
    return [_clip(str(value), 140)]


def _clip(value: object, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _dedupe(items) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered
