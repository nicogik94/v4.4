"""
v4 Multi-Agent System — Deterministic Tools
Pure mathematical scoring, Bayesian calculations, convergence checks.
Zero LLM involvement — these are the system's ground truth.
"""
import json
import math
import re
from collections import deque
from typing import Optional
from state import (
    ProjectState, DetScores, Hypothesis, StrategyOutput,
    ClassifyOutput, AuditOutput, Prediction, PhaseStatus
)
from config import GATE_CONFIGS, REENTRY_TRIGGERS, INVALIDATION_MAP
from workflow_templates import TECHNOLOGY_READINESS_PHASE_SEQUENCE, get_downstream_phases


# ═══ CONVERGENCE GATE EVALUATOR ═══

def check_gate(state: ProjectState, phase: str) -> dict:
    """
    Evaluate whether a phase's exit gate is met.
    Returns: {"passed": bool, "blocking": [reasons], "confidence": float}
    Pure deterministic — no LLM.
    """
    config = GATE_CONFIGS.get(phase)
    if not config:
        return {"passed": True, "blocking": [], "confidence": 1.0}

    blocking = []
    output = getattr(state, phase, None)

    # Check required fields
    if output is not None:
        if isinstance(output, list):
            if len(output) == 0:
                blocking.append(f"{phase} output is empty")
        else:
            for field in config.required_fields:
                val = getattr(output, field, None) if hasattr(output, field) else None
                if val is None or val == "" or val == [] or val == {}:
                    blocking.append(f"Missing required: {field}")

    else:
        blocking.append(f"{phase} has no output yet")

    # Phase-specific checks
    if phase == "classify" and state.classify:
        bf = state.classify.bf
        dq_total = sum(state.classify.dq)
        if config.bayesian_threshold and bf <= config.bayesian_threshold:
            blocking.append(f"BF={bf:.1f}, need >{config.bayesian_threshold}")
        if config.dq_minimum and dq_total < config.dq_minimum:
            blocking.append(f"DQ={dq_total:.0f}%, need >={config.dq_minimum}%")

    if phase == "hypotheses" and state.hypotheses:
        if len(state.hypotheses) < 3:
            blocking.append(f"Only {len(state.hypotheses)} hypotheses, need >=3")
        if not state.sealed:
            blocking.append("Hypotheses not sealed")

    confidence = state.phase_confidence.get(phase, 0.0)
    passed = len(blocking) == 0 and confidence >= config.min_confidence

    return {"passed": passed, "blocking": blocking, "confidence": confidence}


# ═══ RE-ENTRY TRIGGER EVALUATOR ═══

def evaluate_reentry_triggers(state: ProjectState) -> list[dict]:
    """Check all R1-R8 re-entry conditions. Returns list of fired triggers."""
    fired = []

    # R1: assumption shift >2σ
    if state.hypotheses and state.audit:
        for h in state.hypotheses:
            prior_p = h.alpha / (h.alpha + h.beta)
            # If audit radically shifted the belief
            for v in (state.strategy.preliminary_verdicts if state.strategy else []):
                if v.id == h.id:
                    if v.verdict.value == "LIKELY_REJECTED" and prior_p > 0.7:
                        fired.append({**REENTRY_TRIGGERS["R1"], "detail": f"{h.id}: prior P={prior_p:.2f} but REJECTED"})

    # R4: portfolio correlation >0.5
    if state.gauntlet and state.gauntlet.portfolio_correlation > 0.5:
        fired.append({**REENTRY_TRIGGERS["R4"], "detail": f"ρ={state.gauntlet.portfolio_correlation:.2f}"})

    # R5: all hypotheses futile
    if state.strategy and state.strategy.preliminary_verdicts:
        rejected = [v for v in state.strategy.preliminary_verdicts if v.verdict.value == "LIKELY_REJECTED"]
        total = len(state.strategy.preliminary_verdicts)
        if total > 0:
            if len(rejected) == total:
                fired.append({**REENTRY_TRIGGERS["R5"], "detail": f"All {total} hypotheses rejected"})
            if len(rejected) / total > 0.5:
                fired.append({**REENTRY_TRIGGERS["R6"], "detail": f"{len(rejected)}/{total} rejected"})

    return fired


# ═══ DOWNSTREAM INVALIDATION ═══

def collect_downstream_phases(changed_phase: str) -> list[str]:
    """Collect all downstream phases that depend on the changed phase."""
    ordered: list[str] = []
    seen: set[str] = set()
    queue = deque(INVALIDATION_MAP.get(changed_phase, []))

    while queue:
        phase = queue.popleft()
        if phase in seen:
            continue
        seen.add(phase)
        ordered.append(phase)
        queue.extend(INVALIDATION_MAP.get(phase, []))

    return ordered


def _phase_has_material_output(state: ProjectState, phase: str) -> bool:
    if phase == "audit":
        return state.audit is not None or bool(state.audit_raw)
    if phase == "strategy":
        return state.strategy is not None or bool(state.strategy_raw)
    if phase == "report":
        return bool(state.report)
    value = getattr(state, phase, None)
    if isinstance(value, list):
        return len(value) > 0
    return value is not None


def invalidate_downstream(state: ProjectState, changed_phase: str) -> list[str]:
    """Nullify all downstream outputs when an upstream phase changes."""
    project_type = getattr(state, "project_type", "")
    to_invalidate = get_downstream_phases(project_type, changed_phase)
    if not to_invalidate:
        to_invalidate = collect_downstream_phases(changed_phase)
    invalidated = []

    for phase in to_invalidate:
        had_output = _phase_has_material_output(state, phase)
        had_status = state.phase_status.get(phase) != PhaseStatus.PENDING
        had_summary = phase in state.phase_summaries
        had_confidence = phase in state.phase_confidence

        setattr(state, phase, None)
        if phase == "audit":
            state.audit_raw = None
        elif phase == "strategy":
            state.strategy_raw = None
            state.det_scores = None
        elif phase == "report":
            state.report = None
        elif phase == "hypotheses":
            state.sealed = False
            state.seal_date = None

        if had_output or had_status or had_summary or had_confidence:
            state.phase_status[phase] = PhaseStatus.STALE
            state.phase_confidence.pop(phase, None)
            state.phase_summaries.pop(phase, None)
            invalidated.append(phase)

    return invalidated


# ═══ DETERMINISTIC STRATEGY SCORING ═══

def compute_det_scores(strategy: Optional[StrategyOutput]) -> Optional[DetScores]:
    """
    5-dimension quality scores computed from strategy text.
    Zero LLM involvement. This is the ground truth layer.
    """
    if not strategy or not strategy.strategies:
        return None

    strats = strategy.strategies

    # 1. Specificity: SMART dimensions (who/what/how/when/howmuch)
    spec_words = {
        "who": re.compile(r"team|user|client|operator|stakeholder|department", re.I),
        "what": re.compile(r"implement|create|build|fix|add|remove|deploy|design|develop", re.I),
        "how": re.compile(r"\bby\b|\busing\b|\bvia\b|\bthrough\b", re.I),
        "when": re.compile(r"within|week|month|day|sprint|quarter|deadline", re.I),
        "howmuch": re.compile(r"\d+%|\$\d|target|\d+ point|\d+ hour", re.I),
    }
    spec_scores = []
    for s in strats:
        txt = f"{s.action} {s.justification} {s.expected_impact}"
        hits = sum(1 for rx in spec_words.values() if rx.search(txt))
        spec_scores.append(hits)
    specificity = round((sum(spec_scores) / (len(spec_scores) * 5)) * 100) if spec_scores else 0

    # 2. MECE: priority level coverage
    priorities = set(s.priority.value if hasattr(s.priority, 'value') else s.priority for s in strats)
    coverage = len({"CRITICAL", "HIGH", "MEDIUM", "LOW"} & priorities)
    mece = round((coverage / 4) * 100)

    # 3. Evidence linkage
    ev_rx = re.compile(r"H\d|FMEA|RPN|hypothesis|audit|data|observation|finding", re.I)
    evidenced = sum(1 for s in strats if ev_rx.search(f"{s.justification} {s.evidence_chain}"))
    evidence_linkage = round((evidenced / len(strats)) * 100) if strats else 0

    # 4. Consistency: contradiction detection
    contradictions = []
    for i in range(len(strats)):
        for j in range(i + 1, len(strats)):
            a = (strats[i].action or "").lower()
            b = (strats[j].action or "").lower()
            if (("increase" in a and "decrease" in b) or ("add" in a and "remove" in b)):
                words_a = set(a.split())
                words_b = set(b.split())
                if words_a & words_b - {"the", "a", "to", "and", "or", "in", "of"}:
                    contradictions.append(f"{strats[i].priority} vs {strats[j].priority}")
    consistency = max(0, 100 - len(contradictions) * 25)

    # 5. Actionability: timeline + effort populated
    actionable = sum(1 for s in strats if s.timeline and s.effort)
    actionability = round((actionable / len(strats)) * 100) if strats else 0

    overall = round((specificity + mece + evidence_linkage + consistency + actionability) / 5)

    return DetScores(
        overall=overall, specificity=specificity, mece=mece,
        evidence_linkage=evidence_linkage, consistency=consistency,
        actionability=actionability, contradictions=contradictions
    )


# ═══ BAYESIAN CALCULATIONS ═══

def compute_posterior(alpha: float, beta: float, successes: int = 0, failures: int = 0) -> tuple[float, float]:
    """Update Beta prior with observed data."""
    return alpha + successes, beta + failures


def compute_bayes_factor(alpha: float, beta: float) -> float:
    """Bayes Factor: strength of evidence for the dominant hypothesis."""
    lower = min(alpha, beta)
    if lower <= 0:
        return float("inf")
    return max(alpha, beta) / lower


def compute_brier_score(predictions: list[Prediction]) -> Optional[float]:
    """Brier score: lower is better. 0 = perfect, 0.25 = random."""
    scored = [p for p in predictions if p.actual_outcome is not None]
    if not scored:
        return None
    bs = sum((p.predicted_probability - (1.0 if p.actual_outcome else 0.0)) ** 2 for p in scored)
    return bs / len(scored)


def compute_ece(predictions: list[Prediction], n_bins: int = 10) -> Optional[float]:
    """Expected Calibration Error: measures if P=0.7 events happen 70% of the time."""
    scored = [p for p in predictions if p.actual_outcome is not None]
    if not scored:
        return None

    bins = [[] for _ in range(n_bins)]
    for p in scored:
        idx = min(int(p.predicted_probability * n_bins), n_bins - 1)
        bins[idx].append(p)

    ece = 0.0
    for bin_preds in bins:
        if not bin_preds:
            continue
        avg_conf = sum(p.predicted_probability for p in bin_preds) / len(bin_preds)
        avg_acc = sum(1.0 if p.actual_outcome else 0.0 for p in bin_preds) / len(bin_preds)
        ece += (len(bin_preds) / len(scored)) * abs(avg_conf - avg_acc)

    return ece


# ═══ CONTEXT SUMMARIZER ═══

def summarize_phase_output(phase: str, state: ProjectState) -> str:
    """Create compressed summary of a phase output for downstream context injection.
    Keeps token usage manageable across phases."""

    if phase == "classify" and state.classify:
        c = state.classify
        return f"DOMAIN:{c.domain} BF={c.bf:.1f} GAPS:{c.variety_gaps[:200]} RPD:{c.rpd_pattern} DQ={sum(c.dq)}"

    if phase == "hypotheses" and state.hypotheses:
        lines = [
            f"{h.id}[P={h.alpha/(h.alpha+h.beta)*100:.0f}%]:{h.text[:50]} "
            f"WHY:{(h.justification or 'n/a')[:50]}"
            for h in state.hypotheses[:10]
        ]
        return "HYPOTHESES:\n" + "\n".join(lines)

    if phase == "gauntlet" and state.gauntlet:
        lines = [f"{r.id}:crux=\"{r.crux[:60]}\"" for r in state.gauntlet.results[:5]]
        return f"GAUNTLET: ρ={state.gauntlet.portfolio_correlation:.2f}\n" + "\n".join(lines)

    if phase == "audit" and state.audit:
        a = state.audit
        fmea_top = sorted(a.fmea, key=lambda f: f.rpn, reverse=True)[:5]
        return f"AUDIT({'data' if a.data_based else 'predicted'}):\n" + \
               "\n".join(f"FMEA:{f.component} RPN={f.rpn}" for f in fmea_top) + \
               f"\nFINDINGS:{'; '.join(a.top_findings[:5])}"

    if phase == "strategy" and state.strategy:
        s = state.strategy
        return f"STRATEGY:{s.executive_strategy[:200]}\n" + \
               "\n".join(f"[{a.priority}]{a.action[:60]}" for a in s.strategies[:5])

    if phase == "monitor" and state.monitor:
        m = state.monitor
        return (
            f"MONITOR:commitment={m.commitment_score:.0f} "
            f"canaries={len(m.canaries)} "
            f"breakers={len(m.circuit_breakers)} "
            f"watch={','.join(m.reentry_watch[:5])}"
        )

    if phase in TECHNOLOGY_READINESS_PHASE_SEQUENCE:
        output = getattr(state, phase, None)
        if output is None:
            return ""
        payload = output.model_dump(mode="json") if hasattr(output, "model_dump") else output
        return f"{phase.upper()}:" + json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )[:500]
    return ""