"""
v4.3 MAS — Policy Layer (Deterministic Enforcement)

This is the layer the v2.1 enterprise strategy says every agent system needs:
deterministic policy enforcement that sits OUTSIDE the LLM's control. The LLM
cannot bypass, override, or talk its way out of these checks.

The four enforcement primitives:

1. Reversibility classification — every state-changing action is classified
   read_only / reversible_internal / irreversible_internal / irreversible_external.
   Higher tiers require explicit operator approval; the LLM cannot self-authorize.

2. Per-project budget caps — max tokens, max API cost, max wall-clock seconds,
   max LLM calls, max phase re-entries. Hard caps. The orchestrator checks
   before each LLM call and halts if any cap is breached.

3. Kill switch — operator-triggered immediate halt. State transitions to KILLED
   and no further phases execute. Survives orchestrator restarts because the
   state is persisted in Postgres via store.py.

4. Three-state circuit breaker per phase — CLOSED (normal), DEGRADED (elevated
   failure rate, restricted operation), OPEN (halted, requires manual reset).
   Breaker state is per-phase, not per-project, so a flaky strategy phase doesn't
   block classify or hypotheses.

Fail-soft contract: if the policy module cannot reach the database or returns
an error, the orchestrator logs the failure and continues — but logs the
violation as a high-severity audit event. We choose fail-soft because the
Decision Engine is read-only by default; in a system with irreversible-external
actions, fail-hard would be the correct choice.
"""
from __future__ import annotations
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# REVERSIBILITY CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════

class Reversibility(str, Enum):
    """Classification of an agent action by its blast radius and reversibility."""

    READ_ONLY = "read_only"
    """No state change. Examples: query Postgres, read prior_snapshots, fetch
    a tool description. Always permitted; never gated."""

    REVERSIBLE_INTERNAL = "reversible_internal"
    """Internal state change that can be undone within the system. Examples:
    write phase output to Postgres (can be deleted), update phase status,
    create a new prediction record. Permitted by default; logged."""

    IRREVERSIBLE_INTERNAL = "irreversible_internal"
    """Internal state change that cannot be undone, or whose undo cost is
    significant. Examples: seal a project (immutable), commit to an outcome,
    write to a calibration snapshot that subsequent priors depend on.
    Requires HITL approval unless the project's risk classification is
    minimal-risk."""

    IRREVERSIBLE_EXTERNAL = "irreversible_external"
    """State change that affects parties outside the Decision Engine.
    Examples: send an email, post to Slack, publish a report to a public URL,
    notify a third-party API. Always requires HITL approval regardless of
    risk classification.

    NOTE: The v4.3 Decision Engine has no irreversible_external actions in
    its core flow — the engine produces analysis only. This category exists
    for future actions added in v5+ (e.g., automated client report delivery)
    and as a contract for any custom tools wired into the system."""


@dataclass
class ActionDescriptor:
    """Describes an action the orchestrator wants to perform."""
    name: str
    reversibility: Reversibility
    description: str = ""
    requires_approval: bool = False  # computed by classify()
    blast_radius_note: str = ""


def classify_action(name: str, reversibility: Reversibility,
                    risk_classification: str = "minimal_risk",
                    description: str = "") -> ActionDescriptor:
    """Classify an action and decide whether it requires HITL approval.

    Risk classification feeds into approval decisions: a project classified
    as high-risk under EU AI Act Annex III gets stricter approval requirements.
    """
    requires_approval = False

    if reversibility == Reversibility.IRREVERSIBLE_EXTERNAL:
        requires_approval = True  # always
    elif reversibility == Reversibility.IRREVERSIBLE_INTERNAL:
        if risk_classification in ("high_risk", "limited_risk"):
            requires_approval = True

    return ActionDescriptor(
        name=name,
        reversibility=reversibility,
        description=description,
        requires_approval=requires_approval,
        blast_radius_note=_blast_radius_note(reversibility),
    )


def _blast_radius_note(rev: Reversibility) -> str:
    return {
        Reversibility.READ_ONLY: "no state change",
        Reversibility.REVERSIBLE_INTERNAL: "internal write, undoable",
        Reversibility.IRREVERSIBLE_INTERNAL: "internal write, not undoable without manual recovery",
        Reversibility.IRREVERSIBLE_EXTERNAL: "affects external systems or parties",
    }[rev]


# Default action map for the v4.3 phases. The Decision Engine is read-only
# by design — the only writes are internal state. No phase produces an
# irreversible_external action.
PHASE_ACTION_MAP: dict[str, Reversibility] = {
    "classify": Reversibility.REVERSIBLE_INTERNAL,
    "hypotheses": Reversibility.REVERSIBLE_INTERNAL,
    "gauntlet": Reversibility.REVERSIBLE_INTERNAL,
    "audit": Reversibility.REVERSIBLE_INTERNAL,
    "strategy": Reversibility.REVERSIBLE_INTERNAL,
    "sqi": Reversibility.REVERSIBLE_INTERNAL,
    "monitor": Reversibility.REVERSIBLE_INTERNAL,
    "report": Reversibility.REVERSIBLE_INTERNAL,
    # Sealing a project is irreversible internal — the gauntlet output cannot
    # be re-opened without operator override. Already gated in orchestrator.
    "seal_project": Reversibility.IRREVERSIBLE_INTERNAL,
    # Writing a calibration snapshot is irreversible internal — subsequent
    # priors will depend on it.
    "write_calibration_snapshot": Reversibility.IRREVERSIBLE_INTERNAL,
}


# ═══════════════════════════════════════════════════════════════════════════
# BUDGET CAPS
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class BudgetCaps:
    """Per-project budget caps. Enforced before every LLM call.

    Defaults are conservative for a single project; production deployments
    should override per project.
    """
    max_total_tokens: int = 2_000_000     # ~$10 worst case at Opus 4.6 pricing
    max_total_cost_usd: float = 25.00     # hard cost cap
    max_wall_clock_seconds: int = 3600    # 1 hour
    max_llm_calls: int = 100              # per project
    max_phase_reentries: int = 3          # per phase
    max_consecutive_failures: int = 3     # circuit breaker trigger


@dataclass
class BudgetConsumed:
    """Tracks budget consumption against caps. Lives on ProjectState."""
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    wall_clock_started_at: Optional[float] = None
    llm_call_count: int = 0
    phase_reentry_counts: dict[str, int] = field(default_factory=dict)
    consecutive_failures: int = 0

    def wall_clock_elapsed(self) -> float:
        if self.wall_clock_started_at is None:
            return 0.0
        return time.monotonic() - self.wall_clock_started_at


@dataclass
class BudgetCheckResult:
    """Result of a budget check. Carries the breach reason if any cap is hit."""
    allowed: bool
    breach_reason: Optional[str] = None
    cap_breached: Optional[str] = None


def check_budget(consumed: BudgetConsumed, caps: BudgetCaps,
                 incremental_tokens: int = 0,
                 incremental_cost_usd: float = 0.0) -> BudgetCheckResult:
    """Check whether a planned LLM call would breach any cap.

    Call BEFORE the LLM call, with the estimated incremental cost.
    Returns allowed=False if the call should be blocked.
    """
    # Token cap
    if consumed.total_tokens + incremental_tokens > caps.max_total_tokens:
        return BudgetCheckResult(
            allowed=False,
            breach_reason=f"would exceed max_total_tokens cap ({caps.max_total_tokens})",
            cap_breached="max_total_tokens",
        )

    # Cost cap
    if consumed.total_cost_usd + incremental_cost_usd > caps.max_total_cost_usd:
        return BudgetCheckResult(
            allowed=False,
            breach_reason=f"would exceed max_total_cost_usd cap (${caps.max_total_cost_usd:.2f})",
            cap_breached="max_total_cost_usd",
        )

    # Wall-clock cap
    elapsed = consumed.wall_clock_elapsed()
    if elapsed > caps.max_wall_clock_seconds:
        return BudgetCheckResult(
            allowed=False,
            breach_reason=f"wall-clock {elapsed:.0f}s exceeded cap {caps.max_wall_clock_seconds}s",
            cap_breached="max_wall_clock_seconds",
        )

    # LLM call cap
    if consumed.llm_call_count >= caps.max_llm_calls:
        return BudgetCheckResult(
            allowed=False,
            breach_reason=f"reached max_llm_calls cap ({caps.max_llm_calls})",
            cap_breached="max_llm_calls",
        )

    # Consecutive failures (circuit breaker)
    if consumed.consecutive_failures >= caps.max_consecutive_failures:
        return BudgetCheckResult(
            allowed=False,
            breach_reason=f"circuit breaker open: {consumed.consecutive_failures} consecutive failures",
            cap_breached="max_consecutive_failures",
        )

    return BudgetCheckResult(allowed=True)


def record_consumption(consumed: BudgetConsumed,
                       tokens: int,
                       cost_usd: float,
                       success: bool) -> None:
    """Record an actual LLM call's consumption AFTER the call completes."""
    consumed.total_tokens += tokens
    consumed.total_cost_usd += cost_usd
    consumed.llm_call_count += 1
    if success:
        consumed.consecutive_failures = 0
    else:
        consumed.consecutive_failures += 1


# ═══════════════════════════════════════════════════════════════════════════
# KILL SWITCH
# ═══════════════════════════════════════════════════════════════════════════

class KillSwitchState(str, Enum):
    INACTIVE = "inactive"      # normal operation
    ARMED = "armed"            # operator armed; next phase check will halt
    TRIGGERED = "triggered"    # already halted by a previous check


def check_kill_switch(state) -> bool:
    """Return True if the kill switch is active. Should be called before
    each phase and before each LLM call.

    Operates on ProjectState; lazy import to avoid circular dependency.
    """
    return getattr(state, "kill_switch_active", False)


def trigger_kill_switch(state, reason: str, triggered_by: str = "operator") -> None:
    """Activate the kill switch on a project. The orchestrator will halt
    on its next phase or LLM call check.

    This is the deterministic enforcement primitive. The LLM cannot disarm
    or bypass it.
    """
    state.kill_switch_active = True
    state.kill_switch_reason = reason
    state.kill_switch_triggered_by = triggered_by
    state.kill_switch_triggered_at = time.time()
    logger.warning(
        f"KILL SWITCH TRIGGERED for project {getattr(state, 'project_id', 'unknown')}: "
        f"{reason} (by {triggered_by})"
    )


# ═══════════════════════════════════════════════════════════════════════════
# CIRCUIT BREAKER (per phase)
# ═══════════════════════════════════════════════════════════════════════════

class BreakerState(str, Enum):
    CLOSED = "closed"      # normal
    DEGRADED = "degraded"  # elevated failures, restricted operation
    OPEN = "open"          # halted, requires manual reset


@dataclass
class PhaseBreaker:
    state: BreakerState = BreakerState.CLOSED
    failure_count: int = 0
    last_failure_at: Optional[float] = None
    opened_at: Optional[float] = None


def evaluate_breaker(breaker: PhaseBreaker, success: bool,
                     degrade_threshold: int = 2,
                     open_threshold: int = 3) -> BreakerState:
    """Update breaker state based on the latest phase outcome."""
    if success:
        # Success closes the breaker (with one-success-resets-degraded heuristic)
        if breaker.state == BreakerState.DEGRADED:
            breaker.state = BreakerState.CLOSED
            breaker.failure_count = 0
        return breaker.state

    # Failure
    breaker.failure_count += 1
    breaker.last_failure_at = time.time()

    if breaker.failure_count >= open_threshold:
        if breaker.state != BreakerState.OPEN:
            breaker.state = BreakerState.OPEN
            breaker.opened_at = time.time()
            logger.error(f"CIRCUIT BREAKER OPEN: failure_count={breaker.failure_count}")
    elif breaker.failure_count >= degrade_threshold:
        breaker.state = BreakerState.DEGRADED
        logger.warning(f"CIRCUIT BREAKER DEGRADED: failure_count={breaker.failure_count}")

    return breaker.state


def reset_breaker(breaker: PhaseBreaker) -> None:
    """Manually reset a breaker. Operator action only."""
    breaker.state = BreakerState.CLOSED
    breaker.failure_count = 0
    breaker.opened_at = None


# ═══════════════════════════════════════════════════════════════════════════
# UNIFIED POLICY GATE
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class GateDecision:
    """Result of running the policy gate before a phase or LLM call."""
    allowed: bool
    reason: Optional[str] = None
    requires_hitl_approval: bool = False
    breach_category: Optional[str] = None  # "kill_switch" | "budget" | "breaker" | "approval"


def policy_gate(state, action: str, reversibility: Reversibility,
                incremental_tokens: int = 0,
                incremental_cost_usd: float = 0.0) -> GateDecision:
    """The single entry point the orchestrator calls before any state-
    changing action. Combines all enforcement primitives into one decision.

    Returns allowed=True iff:
      - kill switch is not active, AND
      - budget caps are not breached, AND
      - the relevant phase circuit breaker is not OPEN, AND
      - the action does not require HITL approval that has not been granted.

    The orchestrator does not need to know the details of how the decision
    was made — it only needs to honor it.

    Note on type adapters: ProjectState stores budget_caps and budget_consumed
    as plain dicts (for Pydantic compatibility). This function converts to
    BudgetCaps/BudgetConsumed dataclasses internally and writes any updates
    back via record_consumption_to_state().
    """
    # 1. Kill switch
    if check_kill_switch(state):
        return GateDecision(
            allowed=False,
            reason=f"kill switch active: {getattr(state, 'kill_switch_reason', 'no reason given')}",
            breach_category="kill_switch",
        )

    # 2. Budget — convert dict → dataclass for the check
    consumed_dict = getattr(state, "budget_consumed", None)
    caps_dict = getattr(state, "budget_caps", None)
    if consumed_dict is not None and caps_dict is not None:
        consumed = _consumed_from_dict(consumed_dict)
        caps = _caps_from_dict(caps_dict)
        budget_check = check_budget(consumed, caps, incremental_tokens, incremental_cost_usd)
        if not budget_check.allowed:
            return GateDecision(
                allowed=False,
                reason=budget_check.breach_reason,
                breach_category="budget",
            )

    # 3. Phase circuit breaker
    breakers_dict = getattr(state, "phase_breakers", {}) or {}
    breaker_dict = breakers_dict.get(action)
    if breaker_dict and breaker_dict.get("state") == BreakerState.OPEN.value:
        return GateDecision(
            allowed=False,
            reason=f"phase circuit breaker OPEN for {action}",
            breach_category="breaker",
        )

    # 4. HITL approval
    risk = getattr(state, "risk_classification", "minimal_risk")
    descriptor = classify_action(action, reversibility, risk_classification=risk)
    if descriptor.requires_approval:
        approvals = getattr(state, "approvals_granted", {}) or {}
        if action not in approvals:
            return GateDecision(
                allowed=False,
                reason=f"action {action} requires HITL approval (reversibility={reversibility.value}, risk={risk})",
                requires_hitl_approval=True,
                breach_category="approval",
            )

    return GateDecision(allowed=True)


# ═══════════════════════════════════════════════════════════════════════════
# DICT ↔ DATACLASS ADAPTERS (for Pydantic-friendly state storage)
# ═══════════════════════════════════════════════════════════════════════════

def _caps_from_dict(d: dict) -> BudgetCaps:
    return BudgetCaps(
        max_total_tokens=d.get("max_total_tokens", 2_000_000),
        max_total_cost_usd=d.get("max_total_cost_usd", 25.00),
        max_wall_clock_seconds=d.get("max_wall_clock_seconds", 3600),
        max_llm_calls=d.get("max_llm_calls", 100),
        max_phase_reentries=d.get("max_phase_reentries", 3),
        max_consecutive_failures=d.get("max_consecutive_failures", 3),
    )


def _consumed_from_dict(d: dict) -> BudgetConsumed:
    return BudgetConsumed(
        total_tokens=d.get("total_tokens", 0),
        total_cost_usd=d.get("total_cost_usd", 0.0),
        wall_clock_started_at=d.get("wall_clock_started_at"),
        llm_call_count=d.get("llm_call_count", 0),
        phase_reentry_counts=d.get("phase_reentry_counts", {}),
        consecutive_failures=d.get("consecutive_failures", 0),
    )


def record_consumption_to_state(state, tokens: int, cost_usd: float, success: bool) -> None:
    """Update budget_consumed dict on ProjectState after an LLM call.

    This is the orchestrator-friendly wrapper around record_consumption()
    that handles the dict ↔ dataclass conversion.
    """
    consumed_dict = getattr(state, "budget_consumed", None)
    if consumed_dict is None:
        return

    consumed_dict["total_tokens"] = consumed_dict.get("total_tokens", 0) + tokens
    consumed_dict["total_cost_usd"] = consumed_dict.get("total_cost_usd", 0.0) + cost_usd
    consumed_dict["llm_call_count"] = consumed_dict.get("llm_call_count", 0) + 1
    if success:
        consumed_dict["consecutive_failures"] = 0
    else:
        consumed_dict["consecutive_failures"] = consumed_dict.get("consecutive_failures", 0) + 1


def start_wall_clock(state) -> None:
    """Initialize the wall-clock budget meter on first phase entry."""
    consumed_dict = getattr(state, "budget_consumed", None)
    if consumed_dict is None:
        return
    if consumed_dict.get("wall_clock_started_at") is None:
        consumed_dict["wall_clock_started_at"] = time.monotonic()


# ═══════════════════════════════════════════════════════════════════════════
# AUDIT LOGGING
# ═══════════════════════════════════════════════════════════════════════════

def log_policy_event(state, event_type: str, details: dict) -> None:
    """Append a policy event to the project's audit log. The audit log is
    persisted with the project state and is the source of truth for any
    compliance review.

    event_type values:
      - "kill_switch_triggered"
      - "budget_check_failed"
      - "circuit_breaker_state_change"
      - "approval_required"
      - "approval_granted"
      - "intake_sanitization_finding"
    """
    audit_log = getattr(state, "policy_audit_log", None)
    if audit_log is None:
        return

    audit_log.append({
        "ts": time.time(),
        "event_type": event_type,
        "phase": getattr(state, "current_phase", None),
        "details": details,
    })
