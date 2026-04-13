"""
v4 Multi-Agent System — State Models (Shared Blackboard)
All project data flows through this typed state. Each agent reads scoped slices and writes structured outputs.
"""
from __future__ import annotations
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Any
from datetime import datetime
from enum import Enum


class PhaseStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STALE = "stale"  # invalidated by upstream change


class Verdict(str, Enum):
    LIKELY_CONFIRMED = "LIKELY_CONFIRMED"
    LIKELY_REJECTED = "LIKELY_REJECTED"
    NEEDS_MONITORING = "NEEDS_MONITORING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class Priority(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class DecisionObjectStatus(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    REBUILD_FAILED = "rebuild_failed"


class KnowledgeItemStatus(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    EXPIRED = "expired"
    QUARANTINED = "quarantined"


class KnowledgeSyncJobStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    GRANTED = "granted"
    REJECTED = "rejected"


class Provenance(BaseModel):
    source_type: str = "phase_output"
    source_ref: str = ""
    captured_at: str = ""
    captured_by: str = ""
    connector: str = ""
    external_uri: str = ""
    checksum: str = ""
    notes: str = ""


class Decision(BaseModel):
    decision_id: str = ""
    project_id: str = ""
    title: str = ""
    domain: str = ""
    summary: str = ""
    status: str = "active"
    scenario_key: str = "primary"
    hypothesis_ids: list[str] = Field(default_factory=list)
    risk_ids: list[str] = Field(default_factory=list)
    action_ids: list[str] = Field(default_factory=list)
    signal_ids: list[str] = Field(default_factory=list)
    approval_ids: list[str] = Field(default_factory=list)
    outcome_ids: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    evidence_id: str = ""
    title: str = ""
    summary: str = ""
    category: str = ""
    source_phase: str = ""
    linked_decision_ids: list[str] = Field(default_factory=list)
    linked_hypothesis_ids: list[str] = Field(default_factory=list)
    linked_risk_ids: list[str] = Field(default_factory=list)
    untrusted_source: bool = False
    provenance: Provenance = Field(default_factory=Provenance)


class Risk(BaseModel):
    risk_id: str = ""
    title: str = ""
    summary: str = ""
    severity: str = "medium"
    source_phase: str = ""
    status: str = "active"
    linked_decision_ids: list[str] = Field(default_factory=list)
    linked_hypothesis_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class DecisionAction(BaseModel):
    action_id: str = ""
    title: str = ""
    priority: str = ""
    status: str = "proposed"
    summary: str = ""
    linked_decision_ids: list[str] = Field(default_factory=list)
    linked_hypothesis_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    source_phase: str = "strategy"


class Signal(BaseModel):
    signal_id: str = ""
    name: str = ""
    description: str = ""
    kind: str = ""
    confidence: Optional[float] = None
    source_phase: str = ""
    cadence: str = ""
    linked_decision_ids: list[str] = Field(default_factory=list)
    linked_hypothesis_ids: list[str] = Field(default_factory=list)
    untrusted_source: bool = False
    provenance: Provenance = Field(default_factory=Provenance)


class ApprovalRecord(BaseModel):
    approval_id: str = ""
    approval_type: str = ""
    status: ApprovalStatus = ApprovalStatus.PENDING
    requested_at: Optional[str] = None
    resolved_at: Optional[str] = None
    requested_by: str = ""
    resolved_by: str = ""
    scope: str = ""
    reason: str = ""


class OutcomeLink(BaseModel):
    outcome_id: str = ""
    hypothesis_id: str = ""
    phase: str = ""
    predicted_probability: float = 0.0
    actual_outcome: Optional[bool] = None
    framework_used: str = ""
    recorded_at: str = ""
    notes: str = ""


class CalibrationSnapshot(BaseModel):
    snapshot_id: str = ""
    recorded_at: str = ""
    brier_score: Optional[float] = None
    sqi_overall: Optional[float] = None
    det_score_overall: Optional[float] = None
    dq_total: Optional[float] = None
    notes: str = ""


class DecisionObjects(BaseModel):
    schema_version: str = "1.0"
    rebuilt_at: str = ""
    source_state_hash: str = ""
    status: DecisionObjectStatus = DecisionObjectStatus.FRESH
    rebuild_error: str = ""
    primary_decision: Optional[Decision] = None
    evidences: list[Evidence] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    actions: list[DecisionAction] = Field(default_factory=list)
    signals: list[Signal] = Field(default_factory=list)
    approvals: list[ApprovalRecord] = Field(default_factory=list)
    outcomes: list[OutcomeLink] = Field(default_factory=list)
    calibration_snapshots: list[CalibrationSnapshot] = Field(default_factory=list)


class SourceRegistryEntry(BaseModel):
    source_id: str = ""
    name: str = ""
    source_kind: str = "offline_fixture"
    connector_type: str = "offline_fixture"
    owner: str = ""
    domain_tags: list[str] = Field(default_factory=list)
    sensitivity: str = "internal"
    trust_tier: str = "operator_curated"
    enabled: bool = True
    access_mode: str = "manual"
    freshness_policy_id: str = "default_offline"
    secret_ref: str = ""
    notes: str = ""
    last_sync_at: str = ""
    last_success_at: str = ""
    last_error: str = ""
    last_checksum_sha256: str = ""


class KnowledgeItem(BaseModel):
    item_id: str = ""
    source_id: str = ""
    source_ref: str = ""
    title: str = ""
    summary: str = ""
    structured_payload: dict[str, Any] = Field(default_factory=dict)
    observed_at: str = ""
    captured_at: str = ""
    effective_at: str = ""
    expires_at: str = ""
    checksum_sha256: str = ""
    provenance: Provenance = Field(default_factory=Provenance)
    freshness_status: KnowledgeItemStatus = KnowledgeItemStatus.FRESH
    trust_tier: str = "operator_curated"
    sensitivity: str = "internal"
    untrusted_source: bool = True
    eligible_for_retrieval: bool = False


class FreshnessPolicy(BaseModel):
    policy_id: str = "default_offline"
    name: str = "Offline fixture default"
    stale_after_hours: int = 72
    expire_after_hours: int = 168
    manual_review_required: bool = False
    allow_stale_read: bool = True
    notes: str = ""


class KnowledgeSyncJob(BaseModel):
    job_id: str = ""
    source_id: str = ""
    requested_at: str = ""
    completed_at: str = ""
    requested_by: str = ""
    status: KnowledgeSyncJobStatus = KnowledgeSyncJobStatus.PENDING
    mode: str = "manual"
    item_count: int = 0
    inserted_count: int = 0
    updated_count: int = 0
    removed_count: int = 0
    error: str = ""
    checksum_sha256: str = ""
    notes: str = ""


class KnowledgeSyncState(BaseModel):
    status: str = "idle"
    last_sync_at: str = ""
    last_success_at: str = ""
    last_error: str = ""
    last_job_id: str = ""
    jobs: list[KnowledgeSyncJob] = Field(default_factory=list)


class KnowledgeLayerState(BaseModel):
    schema_version: str = "0.1"
    sources: list[SourceRegistryEntry] = Field(default_factory=list)
    items: list[KnowledgeItem] = Field(default_factory=list)
    freshness_policies: list[FreshnessPolicy] = Field(default_factory=list)
    sync_state: KnowledgeSyncState = Field(default_factory=KnowledgeSyncState)


# ═══ Phase 0: Classify ═══

class OODALoop(BaseModel):
    observe: str = ""
    orient: str = ""
    decide: str = ""
    act: str = ""
    freq: str = "Weekly"

    @field_validator("observe", "orient", "decide", "act", "freq", mode="before")
    @classmethod
    def _coerce_textish_fields(cls, value):
        """Accept bullet lists from the LLM and flatten them into readable text."""
        if value is None:
            return ""
        if isinstance(value, list):
            return "; ".join(str(item).strip() for item in value if str(item).strip())
        return value


class ClassifyOutput(BaseModel):
    domain: str = ""
    justification: str = ""
    bf: float = 0.0
    variety_env: str = ""
    variety_sys: str = ""
    variety_gaps: Any = ""
    variety_decision: str = ""
    ooda: OODALoop = Field(default_factory=OODALoop)
    rpd_pattern: str = ""
    sensemaking_anchors: Any = ""
    expectancy_violations: Any = ""
    reference_class: str = ""
    dq: list[float] = Field(default_factory=lambda: [0, 0, 0, 0])
    maturity_assessment: str = ""
    spiral_depth: str = ""


# ═══ Phase 1: Hypotheses ═══

class Hypothesis(BaseModel):
    id: str
    text: str
    justification: str = ""
    signal: str = ""
    alpha: float = 1.0
    beta: float = 1.0
    confirm: str = ""
    reject: str = ""
    evoi: str = "medium"
    portfolio_cluster: str = ""
    status: str = "OPEN"
    evidence_ids: list[str] = Field(default_factory=list)


class GauntletResult(BaseModel):
    id: str
    risk_rank: int = 0
    frameworks: list[dict] = Field(default_factory=list)
    crux: str = ""
    top_fmea: dict = Field(default_factory=dict)
    fta_cut_set: str = ""


class GauntletOutput(BaseModel):
    results: list[GauntletResult] = Field(default_factory=list)
    portfolio_correlation: float = 0.0
    mece_gaps: str = ""
    thompson_priority: str = ""
    evoi_ranking: str = ""


# ═══ Phase 2: Audit ═══

class FMEAItem(BaseModel):
    component: str
    failure_mode: str = ""
    effect: str = ""
    s: int = 1
    o: int = 1
    d: int = 1
    rpn: int = 0
    action: str = ""
    evidence: str = ""


class HAZOPItem(BaseModel):
    node: str
    guide_word: str = ""
    deviation: str = ""
    consequence: str = ""
    evidence: str = ""


class STPAItem(BaseModel):
    control_action: str
    uca_type: str = ""
    hazard: str = ""
    constraint: str = ""


class AuditOutput(BaseModel):
    data_based: bool = False
    fmea: list[FMEAItem] = Field(default_factory=list)
    hazop: list[HAZOPItem] = Field(default_factory=list)
    stpa: list[STPAItem] = Field(default_factory=list)
    fta: dict = Field(default_factory=dict)
    swiss_cheese: dict = Field(default_factory=dict)
    top_findings: list[str] = Field(default_factory=list)
    h_norm_estimate: str = ""
    observation_needs: list[str] = Field(default_factory=list)


# ═══ Phase 3: Strategy ═══

class PreliminaryVerdict(BaseModel):
    id: str
    verdict: Verdict = Verdict.NEEDS_MONITORING
    evidence: str = ""
    monitoring_plan: str = ""


class StrategyAction(BaseModel):
    priority: Priority = Priority.MEDIUM
    action: str = ""
    justification: str = ""
    evidence_chain: str = ""
    expected_impact: str = ""
    effort: str = ""
    timeline: str = ""
    risk_if_ignored: str = ""
    framework_source: str = ""


class StrategyOutput(BaseModel):
    preliminary_verdicts: list[PreliminaryVerdict] = Field(default_factory=list)
    executive_strategy: str = ""
    strategies: list[StrategyAction] = Field(default_factory=list)
    implementation_sequence: str = ""
    success_metrics: list[str] = Field(default_factory=list)
    monitoring_plan: str = ""
    review_date: str = ""
    confidence: str = ""
    reentry_check: str = ""


# ═══ Phase 4: Monitor ═══

class MonitorScheduleItem(BaseModel):
    metric: str = ""
    owner: str = ""
    source: str = ""


class MonitorOODASchedule(BaseModel):
    daily: list[MonitorScheduleItem] = Field(default_factory=list)
    weekly: list[MonitorScheduleItem] = Field(default_factory=list)
    monthly: list[MonitorScheduleItem] = Field(default_factory=list)


class MonitorCircuitBreaker(BaseModel):
    strategy_ref: str = ""
    trip: str = ""
    reset: str = ""


class MonitorCanary(BaseModel):
    signal: str = ""
    direction: str = ""
    window: str = ""
    meaning: str = ""


class MonitorChaosDrill(BaseModel):
    what: str = ""
    when: str = ""
    measure: str = ""


class MonitorOutput(BaseModel):
    ooda_schedule: MonitorOODASchedule = Field(default_factory=MonitorOODASchedule)
    circuit_breakers: list[MonitorCircuitBreaker] = Field(default_factory=list)
    canaries: list[MonitorCanary] = Field(default_factory=list)
    chaos_drills: list[MonitorChaosDrill] = Field(default_factory=list)
    hro_principles_active: list[str] = Field(default_factory=list)
    reentry_watch: list[str] = Field(default_factory=list)
    commitment_score: float = 0.0
    commitment_rationale: str = ""


# ═══ SQI ═══

class SQIDimension(BaseModel):
    name: str
    score: float = 0.0
    grade: str = "F"
    finding: str = ""


class RumeltTest(BaseModel):
    consistency: dict = Field(default_factory=lambda: {"pass": False, "note": ""})
    consonance: dict = Field(default_factory=lambda: {"pass": False, "note": ""})
    advantage: dict = Field(default_factory=lambda: {"pass": False, "note": ""})
    feasibility: dict = Field(default_factory=lambda: {"pass": False, "note": ""})


class SQIOutput(BaseModel):
    sqi_overall: float = 0.0
    dimensions: list[SQIDimension] = Field(default_factory=list)
    rumelt_test: RumeltTest = Field(default_factory=RumeltTest)
    opposite_test: list[dict] = Field(default_factory=list)
    wwhtbt: list[dict] = Field(default_factory=list)
    conflicts: list[dict] = Field(default_factory=list)
    weakest_link: str = ""
    improvement_actions: list[str] = Field(default_factory=list)


# ═══ Deterministic Scoring ═══

class DetScores(BaseModel):
    overall: float = 0.0
    specificity: float = 0.0
    mece: float = 0.0
    evidence_linkage: float = 0.0
    consistency: float = 0.0
    actionability: float = 0.0
    contradictions: list[str] = Field(default_factory=list)


# ═══ Phase 5: Report ═══

class DQScores(BaseModel):
    frame: float = 0.0
    alt: float = 0.0
    info: float = 0.0
    val: float = 0.0
    reas: float = 0.0
    commit: float = 0.0


# ═══ Meta-Learning ═══

class Prediction(BaseModel):
    hypothesis_id: str
    predicted_probability: float
    actual_outcome: Optional[bool] = None
    phase: str = ""
    framework_used: str = ""
    timestamp: datetime = Field(default_factory=datetime.now)


# ═══ MASTER PROJECT STATE ═══

class ProjectState(BaseModel):
    """The shared blackboard. All agents read from and write to this."""
    # Identity
    project_id: str = ""
    project_name: str = ""
    created_at: datetime = Field(default_factory=datetime.now)

    # Input
    brief: str = ""
    data: str = ""
    imported_evidence: list[Evidence] = Field(default_factory=list)
    imported_signals: list[Signal] = Field(default_factory=list)
    knowledge_layer: Optional[KnowledgeLayerState] = None

    # Phase tracking
    current_phase: str = "classify"
    phase_status: dict[str, PhaseStatus] = Field(default_factory=lambda: {
        p: PhaseStatus.PENDING for p in [
            "classify", "hypotheses", "gauntlet", "audit",
            "strategy", "sqi", "monitor", "report"
        ]
    })
    phase_confidence: dict[str, float] = Field(default_factory=dict)
    phase_run_completed_at: dict[str, str] = Field(default_factory=dict)
    re_entry_count: dict[str, int] = Field(default_factory=lambda: {
        p: 0 for p in ["classify", "hypotheses", "audit", "strategy", "monitor", "report"]
    })

    # Phase outputs
    classify: Optional[ClassifyOutput] = None
    hypotheses: Optional[list[Hypothesis]] = None
    gauntlet: Optional[GauntletOutput] = None
    sealed: bool = False
    seal_date: Optional[str] = None
    audit: Optional[AuditOutput] = None
    audit_raw: Optional[str] = None
    strategy: Optional[StrategyOutput] = None
    strategy_raw: Optional[str] = None
    monitor: Optional[MonitorOutput] = None
    sqi: Optional[SQIOutput] = None
    det_scores: Optional[DetScores] = None

    # Phase 4: Monitor
    observations: dict[str, str] = Field(default_factory=dict)
    timer_logs: list[dict] = Field(default_factory=list)

    # Phase 5: Report
    report: Optional[str] = None
    dq: DQScores = Field(default_factory=DQScores)

    # Meta-learning
    predictions: list[Prediction] = Field(default_factory=list)
    brier_score: Optional[float] = None

    # Re-entry tracking
    reentry_triggers_fired: list[dict] = Field(default_factory=list)

    # Summaries (compressed versions for downstream context)
    phase_summaries: dict[str, str] = Field(default_factory=dict)
    decision_objects: Optional[DecisionObjects] = None

    # ═══ v4.3 POLICY LAYER (deterministic enforcement) ═══
    # See mas/policy.py for the enforcement logic. These fields are
    # populated and read by the policy gate; the orchestrator does not
    # touch them directly except via policy.policy_gate().

    # EU AI Act risk classification per project. See compliance/eu-ai-act-classification.md
    # for the operator decision tree. Default is minimal_risk; the operator
    # MUST override at intake if the use case is in Annex III.
    risk_classification: str = "minimal_risk"  # minimal_risk | limited_risk | high_risk | prohibited
    risk_classification_rationale: str = ""
    risk_classification_set_by: str = ""

    # Budget caps and consumption tracking. Stored as dicts for Pydantic
    # compatibility; converted to policy.BudgetCaps / BudgetConsumed inside
    # policy.py functions.
    budget_caps: dict = Field(default_factory=lambda: {
        "max_total_tokens": 2_000_000,
        "max_total_cost_usd": 25.00,
        "max_wall_clock_seconds": 3600,
        "max_llm_calls": 100,
        "max_phase_reentries": 3,
        "max_consecutive_failures": 3,
    })
    budget_consumed: dict = Field(default_factory=lambda: {
        "total_tokens": 0,
        "total_cost_usd": 0.0,
        "wall_clock_started_at": None,
        "llm_call_count": 0,
        "phase_reentry_counts": {},
        "consecutive_failures": 0,
    })

    # Kill switch state. Operator-triggered via POST /projects/{id}/kill.
    # The orchestrator checks this before each phase and each LLM call.
    kill_switch_active: bool = False
    kill_switch_reason: str = ""
    kill_switch_triggered_by: str = ""
    kill_switch_triggered_at: Optional[float] = None

    # HITL approvals granted per action name. Operator grants via API.
    approvals_granted: dict[str, dict] = Field(default_factory=dict)

    # Per-phase circuit breaker state. CLOSED | DEGRADED | OPEN.
    phase_breakers: dict[str, dict] = Field(default_factory=dict)

    # Intake sanitization findings. Populated by security.sanitize_brief()
    # at first phase entry. Persisted for audit and compliance review.
    intake_sanitization_findings: Optional[dict] = None

    # Policy audit log — append-only. Every policy event lands here.
    # The operator and any compliance reviewer reads this to verify
    # the deterministic enforcement layer worked correctly.
    policy_audit_log: list[dict] = Field(default_factory=list)
