"""Controlled retrieval eligibility for the knowledge layer.

This module computes backend-authoritative retrieval eligibility and a
structured projection for later reasoning preparation. It does not modify
orchestration semantics and does not expose raw prompt dumps.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime

from pydantic import BaseModel, Field

from state import KnowledgeItemStatus, ProjectState

from .freshness import evaluate_source_status, refresh_knowledge_items
from .projection import ProjectedKnowledgeItem, project_knowledge_item
from .registry import get_freshness_policy, get_source_entry


PHASE_SEQUENCE = (
    "classify",
    "hypotheses",
    "gauntlet",
    "audit",
    "strategy",
    "sqi",
    "monitor",
    "report",
)
PROMPT_FACING_RETRIEVAL_PHASES = ("audit", "strategy")

TRUST_TIER_RANK = {
    "untrusted": 0,
    "external_unknown": 1,
    "low": 1,
    "partner": 2,
    "operator_curated": 3,
    "analyst_verified": 4,
    "official": 5,
}

DEFAULT_ALLOWED_SENSITIVITIES = ["public", "internal"]
DEFAULT_ALLOWED_SOURCE_KINDS = ["offline_fixture"]
DEFAULT_ALLOWED_CONNECTORS = ["offline_fixture"]
DEFAULT_ALLOWED_TEXT_FIELDS = ["title", "summary"]
DEFAULT_ALLOWED_STRUCTURED_KEYS = [
    "region",
    "country",
    "market",
    "topic",
    "category",
    "metric",
    "value",
    "unit",
    "status",
    "score",
    "confidence",
    "trend",
    "date",
    "window",
]


class PromptExposurePolicy(BaseModel):
    allowed_text_fields: list[str] = Field(default_factory=lambda: list(DEFAULT_ALLOWED_TEXT_FIELDS))
    allowed_structured_keys: list[str] = Field(default_factory=lambda: list(DEFAULT_ALLOWED_STRUCTURED_KEYS))
    max_items: int = 5
    max_title_chars: int = 180
    max_summary_chars: int = 420
    max_fact_value_chars: int = 120
    max_facts_per_item: int = 6
    note: str = "Whitelisted projection only; no raw payload dumps."


class PhaseRetrievalPolicy(BaseModel):
    phase: str
    min_trust_tier: str = "operator_curated"
    allowed_sensitivities: list[str] = Field(default_factory=lambda: list(DEFAULT_ALLOWED_SENSITIVITIES))
    allowed_source_kinds: list[str] = Field(default_factory=lambda: list(DEFAULT_ALLOWED_SOURCE_KINDS))
    allowed_connector_types: list[str] = Field(default_factory=lambda: list(DEFAULT_ALLOWED_CONNECTORS))
    freshness_required: list[str] = Field(default_factory=lambda: [KnowledgeItemStatus.FRESH.value])
    prompt_exposure: PromptExposurePolicy = Field(default_factory=PromptExposurePolicy)
    note: str = "Eligibility is backend-derived and phase-specific."


class RetrievalBlockedItem(BaseModel):
    item_id: str
    source_id: str
    source_name: str = ""
    title: str = ""
    freshness_status: str = ""
    trust_tier: str = ""
    sensitivity: str = ""
    source_status: str = ""
    blocked_reasons: list[str] = Field(default_factory=list)


class RetrievalEligibleItem(BaseModel):
    item_id: str
    source_id: str
    source_name: str = ""
    title: str = ""
    freshness_status: str = ""
    trust_tier: str = ""
    sensitivity: str = ""
    source_status: str = ""
    projection: ProjectedKnowledgeItem


class PhaseRetrievalSummary(BaseModel):
    phase: str
    eligible_count: int = 0
    blocked_count: int = 0
    total_items: int = 0
    min_trust_tier: str = ""
    allowed_sensitivities: list[str] = Field(default_factory=list)
    message: str = ""


class ProjectKnowledgeRetrievalSummary(BaseModel):
    project_id: str
    eligibility_source: str = "backend_derived"
    total_items: int = 0
    total_eligible_count: int = 0
    total_blocked_count: int = 0
    phases: list[PhaseRetrievalSummary] = Field(default_factory=list)
    overview: str = ""


class PhaseKnowledgeRetrievalView(BaseModel):
    project_id: str
    phase: str
    eligibility_source: str = "backend_derived"
    policy: PhaseRetrievalPolicy
    eligible_items: list[RetrievalEligibleItem] = Field(default_factory=list)
    blocked_items: list[RetrievalBlockedItem] = Field(default_factory=list)
    overview: str = ""


class RetrievalUsedItemSummary(BaseModel):
    item_id: str = ""
    source_id: str = ""
    source_name: str = ""
    title: str = ""
    observed_at: str = ""
    trust_tier: str = ""
    sensitivity: str = ""
    fact_keys: list[str] = Field(default_factory=list)


class RetrievalPhaseImpactSummary(BaseModel):
    phase: str
    retrieval_used: bool = False
    eligible_count: int = 0
    blocked_count: int = 0
    used_item_count: int = 0
    used_items: list[RetrievalUsedItemSummary] = Field(default_factory=list)
    blocked_reason_summary: list[str] = Field(default_factory=list)
    overview: str = ""


PHASE_RETRIEVAL_POLICIES: dict[str, PhaseRetrievalPolicy] = {
    "classify": PhaseRetrievalPolicy(
        phase="classify",
        min_trust_tier="operator_curated",
        prompt_exposure=PromptExposurePolicy(max_items=4),
    ),
    "hypotheses": PhaseRetrievalPolicy(
        phase="hypotheses",
        min_trust_tier="operator_curated",
        prompt_exposure=PromptExposurePolicy(max_items=4),
    ),
    "gauntlet": PhaseRetrievalPolicy(
        phase="gauntlet",
        min_trust_tier="operator_curated",
        prompt_exposure=PromptExposurePolicy(max_items=5),
    ),
    "audit": PhaseRetrievalPolicy(
        phase="audit",
        min_trust_tier="operator_curated",
        prompt_exposure=PromptExposurePolicy(max_items=6),
    ),
    "strategy": PhaseRetrievalPolicy(
        phase="strategy",
        min_trust_tier="operator_curated",
        prompt_exposure=PromptExposurePolicy(max_items=6),
    ),
    "sqi": PhaseRetrievalPolicy(
        phase="sqi",
        min_trust_tier="operator_curated",
        prompt_exposure=PromptExposurePolicy(max_items=4),
    ),
    "monitor": PhaseRetrievalPolicy(
        phase="monitor",
        min_trust_tier="operator_curated",
        prompt_exposure=PromptExposurePolicy(max_items=8),
    ),
    "report": PhaseRetrievalPolicy(
        phase="report",
        min_trust_tier="operator_curated",
        prompt_exposure=PromptExposurePolicy(max_items=8),
    ),
}


def get_retrieval_policy(phase: str) -> PhaseRetrievalPolicy:
    normalized = (phase or "").strip().lower()
    if normalized not in PHASE_RETRIEVAL_POLICIES:
        raise KeyError(normalized)
    return PHASE_RETRIEVAL_POLICIES[normalized].model_copy(deep=True)


def build_project_retrieval_summary(
    state: ProjectState,
    *,
    now: datetime | None = None,
) -> ProjectKnowledgeRetrievalSummary:
    phase_summaries: list[PhaseRetrievalSummary] = []
    total_eligible = 0
    total_blocked = 0
    total_items = len((state.knowledge_layer.items if state.knowledge_layer else []) or [])
    for phase in PHASE_SEQUENCE:
        view = evaluate_phase_retrieval(state, phase, now=now)
        phase_summaries.append(
            PhaseRetrievalSummary(
                phase=phase,
                eligible_count=len(view.eligible_items),
                blocked_count=len(view.blocked_items),
                total_items=len(view.eligible_items) + len(view.blocked_items),
                min_trust_tier=view.policy.min_trust_tier,
                allowed_sensitivities=list(view.policy.allowed_sensitivities),
                message=view.overview,
            )
        )
        total_eligible += len(view.eligible_items)
        total_blocked += len(view.blocked_items)

    overview = (
        f"{total_eligible} eligible projection(s) and {total_blocked} blocked item-phase evaluations "
        f"across {len(phase_summaries)} phases. Eligibility is backend-derived, whitelist-based, and does not trigger reruns."
    )
    return ProjectKnowledgeRetrievalSummary(
        project_id=state.project_id,
        total_items=total_items,
        total_eligible_count=total_eligible,
        total_blocked_count=total_blocked,
        phases=phase_summaries,
        overview=overview,
    )


def evaluate_phase_retrieval(
    state: ProjectState,
    phase: str,
    *,
    now: datetime | None = None,
) -> PhaseKnowledgeRetrievalView:
    policy = get_retrieval_policy(phase)
    now = now or datetime.now()
    refresh_knowledge_items(state, now=now)

    eligible: list[RetrievalEligibleItem] = []
    blocked: list[RetrievalBlockedItem] = []
    for item in (state.knowledge_layer.items if state.knowledge_layer else []) or []:
        source = get_source_entry(state, item.source_id)
        source_status = evaluate_source_status(state, item.source_id, now=now)
        blocked_reasons = _blocked_reasons(state, item, source_status, source, policy)
        projection = None
        if not blocked_reasons and source is not None:
            projection = project_knowledge_item(
                item,
                source,
                allowed_text_fields=policy.prompt_exposure.allowed_text_fields,
                allowed_structured_keys=policy.prompt_exposure.allowed_structured_keys,
                max_title_chars=policy.prompt_exposure.max_title_chars,
                max_summary_chars=policy.prompt_exposure.max_summary_chars,
                max_fact_value_chars=policy.prompt_exposure.max_fact_value_chars,
                max_facts=policy.prompt_exposure.max_facts_per_item,
            )
            if projection is None:
                blocked_reasons.append("prompt_exposure_policy_filtered_all_fields")

        if blocked_reasons or projection is None:
            blocked.append(
                RetrievalBlockedItem(
                    item_id=item.item_id,
                    source_id=item.source_id,
                    source_name=source.name if source else "",
                    title=item.title,
                    freshness_status=_status_value(item.freshness_status),
                    trust_tier=item.trust_tier,
                    sensitivity=item.sensitivity,
                    source_status=source_status,
                    blocked_reasons=blocked_reasons or ["projection_unavailable"],
                )
            )
            continue

        eligible.append(
            RetrievalEligibleItem(
                item_id=item.item_id,
                source_id=item.source_id,
                source_name=source.name if source else "",
                title=item.title,
                freshness_status=_status_value(item.freshness_status),
                trust_tier=item.trust_tier,
                sensitivity=item.sensitivity,
                source_status=source_status,
                projection=projection,
            )
        )

    eligible.sort(key=lambda record: (record.projection.observed_at, record.item_id), reverse=True)
    blocked.sort(key=lambda record: (record.source_id, record.item_id))

    limited_eligible = eligible[: policy.prompt_exposure.max_items]
    overview = (
        f"{len(limited_eligible)} eligible item(s), {len(blocked)} blocked item(s). "
        f"Only fresh, allowed, whitelisted projections are eligible for {policy.phase}."
    )
    return PhaseKnowledgeRetrievalView(
        project_id=state.project_id,
        phase=policy.phase,
        policy=policy,
        eligible_items=limited_eligible,
        blocked_items=blocked,
        overview=overview,
    )


def build_phase_retrieval_impact(
    state: ProjectState,
    phase: str,
    *,
    now: datetime | None = None,
) -> RetrievalPhaseImpactSummary:
    normalized_phase = (phase or "").strip().lower()
    view = evaluate_phase_retrieval(state, normalized_phase, now=now)
    used_items = _used_items_for_phase(state, normalized_phase)
    blocked_summary = _blocked_reason_summary(view.blocked_items)
    overview = (
        f"{normalized_phase.title()} retrieval {'used' if used_items else 'not used'}; "
        f"eligible now={len(view.eligible_items)}, blocked now={len(view.blocked_items)}."
    )
    if blocked_summary:
        overview += f" Top blocked reasons: {'; '.join(blocked_summary[:3])}."
    return RetrievalPhaseImpactSummary(
        phase=normalized_phase,
        retrieval_used=bool(used_items),
        eligible_count=len(view.eligible_items),
        blocked_count=len(view.blocked_items),
        used_item_count=len(used_items),
        used_items=used_items,
        blocked_reason_summary=blocked_summary,
        overview=overview,
    )


def build_prompt_facing_retrieval_impact(
    state: ProjectState,
    *,
    now: datetime | None = None,
) -> list[RetrievalPhaseImpactSummary]:
    return [
        build_phase_retrieval_impact(state, phase, now=now)
        for phase in PROMPT_FACING_RETRIEVAL_PHASES
    ]


def _blocked_reasons(
    state: ProjectState,
    item,
    source_status: str,
    source,
    policy: PhaseRetrievalPolicy,
) -> list[str]:
    reasons: list[str] = []
    if source is None:
        return ["source_missing"]
    if not source.enabled:
        reasons.append("source_disabled")
    if source.source_kind not in policy.allowed_source_kinds:
        reasons.append("source_kind_disallowed")
    if source.connector_type not in policy.allowed_connector_types:
        reasons.append("connector_type_disallowed")
    if source.access_mode != "manual":
        reasons.append("access_mode_disallowed")
    if source_status != "current":
        reasons.append(f"source_status_{source_status}")

    item_status = _status_value(item.freshness_status)
    if item_status not in policy.freshness_required:
        reasons.append(f"freshness_{item_status}")
    if (item.sensitivity or "").lower() not in {value.lower() for value in policy.allowed_sensitivities}:
        reasons.append("sensitivity_disallowed")
    if _trust_rank(item.trust_tier) < _trust_rank(policy.min_trust_tier):
        reasons.append("trust_tier_below_minimum")

    freshness_policy = get_freshness_policy(state, source.freshness_policy_id)
    if freshness_policy.manual_review_required:
        reasons.append("manual_review_required")

    return reasons


def _trust_rank(value: str) -> int:
    return TRUST_TIER_RANK.get((value or "").strip().lower(), 0)


def _status_value(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _used_items_for_phase(state: ProjectState, phase: str) -> list[RetrievalUsedItemSummary]:
    normalized_phase = (phase or "").strip().lower()
    for event in reversed(state.policy_audit_log or []):
        if str(event.get("event_type") or "") != "knowledge_retrieval_used":
            continue
        details = event.get("details", {}) or {}
        if str(details.get("phase") or event.get("phase") or "").strip().lower() != normalized_phase:
            continue
        items = details.get("used_items", []) or []
        used: list[RetrievalUsedItemSummary] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            used.append(
                RetrievalUsedItemSummary(
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
        return used
    return []


def _blocked_reason_summary(blocked_items: list[RetrievalBlockedItem]) -> list[str]:
    counter: Counter[str] = Counter()
    for item in blocked_items:
        for reason in item.blocked_reasons or []:
            counter[_humanize_blocked_reason(reason)] += 1
    ranked = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    return [f"{reason} x{count}" for reason, count in ranked[:5]]


def _humanize_blocked_reason(reason: str) -> str:
    text = str(reason or "").strip().lower()
    if not text:
        return "unknown"
    if text.startswith("freshness_"):
        return "freshness " + text.replace("freshness_", "", 1)
    if text.startswith("source_status_"):
        return "source status " + text.replace("source_status_", "", 1)
    return text.replace("_", " ")
