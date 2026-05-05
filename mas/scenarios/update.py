"""Pure deterministic update helpers for internal Bayesian scenarios."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Iterable

from scipy.stats import beta as beta_distribution

from scenarios.models import (
    BayesianScenario,
    ScenarioDisplayMode,
    ScenarioEvidenceObservation,
    ScenarioEvidenceStance,
    ScenarioPriorSource,
    ScenarioUpdateMethod,
    ScenarioUpdateResult,
)


LOW_EVIDENCE_NUMERIC_THRESHOLD = 10


def beta_binomial_update(alpha: float, beta: float, k: int, n: int) -> tuple[float, float]:
    """Conjugate Beta-Binomial update returning (alpha + k, beta + n - k)."""
    _validate_positive_beta_params(alpha, beta)
    if k < 0 or n < 0:
        raise ValueError("evidence counts must be non-negative")
    if k > n:
        raise ValueError("k cannot exceed n")
    return alpha + k, beta + n - k


def log_odds_update(prior_probability: float, log_likelihood_ratio: float) -> float:
    """Update a probability by adding a log likelihood ratio in logit space."""
    if prior_probability <= 0.0 or prior_probability >= 1.0:
        raise ValueError("prior_probability must be strictly between 0 and 1")
    if not math.isfinite(log_likelihood_ratio):
        raise ValueError("log_likelihood_ratio must be finite")

    prior_logit = math.log(prior_probability / (1.0 - prior_probability))
    posterior_logit = prior_logit + log_likelihood_ratio
    if posterior_logit >= 0:
        z = math.exp(-posterior_logit)
        return 1.0 / (1.0 + z)
    z = math.exp(posterior_logit)
    return z / (1.0 + z)


def calibrated_label(probability: float) -> str:
    """Return a calibrated verbal label for an internal probability."""
    if not math.isfinite(probability) or probability < 0.0 or probability > 1.0:
        raise ValueError("probability must be between 0 and 1")
    if probability < 0.20:
        return "very unlikely"
    if probability < 0.40:
        return "unlikely"
    if probability < 0.60:
        return "uncertain"
    if probability < 0.80:
        return "likely"
    return "very likely"


def posterior_probability_summary(alpha: float, beta: float) -> tuple[float, float, float]:
    """Return internal posterior mean and 90% credible interval."""
    _validate_positive_beta_params(alpha, beta)
    mean = alpha / (alpha + beta)
    low = float(beta_distribution.ppf(0.05, alpha, beta))
    high = float(beta_distribution.ppf(0.95, alpha, beta))
    return mean, low, high


def build_scenario_from_hypothesis(
    hypothesis: object,
    *,
    scenario_id: str | None = None,
    decision_ref: str | None = None,
    prior_source: ScenarioPriorSource = ScenarioPriorSource.HYPOTHESIS,
    falsifier: object,
    assumptions: list[str] | None = None,
    uncertainty_note: str = "Internal T1 scenario seeded from an existing v4 hypothesis.",
    created_ts: datetime | None = None,
) -> BayesianScenario:
    """Create an internal scenario from an existing v4-style Hypothesis object."""
    hypothesis_ref = str(getattr(hypothesis, "id", "") or "").strip()
    if not hypothesis_ref:
        raise ValueError("hypothesis must expose a non-empty id")

    prior_alpha = float(getattr(hypothesis, "alpha", 1.0))
    prior_beta = float(getattr(hypothesis, "beta", 1.0))
    _validate_positive_beta_params(prior_alpha, prior_beta)
    posterior_mean, ci_low, ci_high = posterior_probability_summary(prior_alpha, prior_beta)
    evidence_refs = [str(item) for item in list(getattr(hypothesis, "evidence_ids", []) or []) if str(item).strip()]
    evidence_count = len(evidence_refs)
    return BayesianScenario(
        scenario_id=scenario_id or f"scenario_{hypothesis_ref}",
        decision_ref=decision_ref,
        hypothesis_ref=hypothesis_ref,
        prior_alpha=prior_alpha,
        prior_beta=prior_beta,
        prior_source=prior_source,
        evidence_refs=evidence_refs,
        update_method=ScenarioUpdateMethod.NONE,
        posterior_alpha=prior_alpha,
        posterior_beta=prior_beta,
        posterior_mean=posterior_mean,
        posterior_ci_low_90=ci_low,
        posterior_ci_high_90=ci_high,
        calibrated_label=calibrated_label(posterior_mean),
        evidence_count_n=evidence_count,
        display_mode=_display_mode_for_count(evidence_count),
        assumptions=assumptions or [],
        falsifier=falsifier,
        uncertainty_note=uncertainty_note,
        created_ts=created_ts or _utc_now(),
        updated_ts=created_ts or _utc_now(),
    )


def update_scenario_with_evidence(
    scenario: BayesianScenario,
    observations: Iterable[ScenarioEvidenceObservation],
) -> ScenarioUpdateResult:
    """Apply deduplicated source-of-record evidence to a scenario without mutation."""
    deduped, contradiction = _dedupe_observations(list(observations))
    if contradiction:
        return ScenarioUpdateResult(
            status="contradiction",
            scenario=scenario.model_copy(deep=True),
            evidence_refs=list(scenario.evidence_refs),
            contradiction_reason=contradiction,
            contradictory_evidence_refs=[item.evidence_id for item in deduped],
        )

    successes = sum(1 for item in deduped if item.stance == "supports")
    failures = sum(1 for item in deduped if item.stance == "refutes")
    counted_n = successes + failures
    evidence_refs = _merge_refs(scenario.evidence_refs, [item.evidence_id for item in deduped])

    if counted_n == 0:
        updated = _copy_scenario(
            scenario,
            evidence_refs=evidence_refs,
            update_method=ScenarioUpdateMethod.NONE,
            updated_ts=_utc_now(),
        )
        return ScenarioUpdateResult(status="updated", scenario=updated, evidence_refs=evidence_refs)

    base_alpha = scenario.posterior_alpha if scenario.posterior_alpha is not None else scenario.prior_alpha
    base_beta = scenario.posterior_beta if scenario.posterior_beta is not None else scenario.prior_beta
    posterior_alpha, posterior_beta = beta_binomial_update(base_alpha, base_beta, successes, counted_n)
    posterior_mean, ci_low, ci_high = posterior_probability_summary(posterior_alpha, posterior_beta)
    evidence_count = scenario.evidence_count_n + counted_n
    updated = _copy_scenario(
        scenario,
        evidence_refs=evidence_refs,
        update_method=ScenarioUpdateMethod.BETA_BINOMIAL,
        posterior_alpha=posterior_alpha,
        posterior_beta=posterior_beta,
        posterior_mean=posterior_mean,
        posterior_ci_low_90=ci_low,
        posterior_ci_high_90=ci_high,
        calibrated_label=calibrated_label(posterior_mean),
        evidence_count_n=evidence_count,
        display_mode=_display_mode_for_count(evidence_count),
        uncertainty_note=_uncertainty_note_for_count(evidence_count),
        updated_ts=_utc_now(),
    )
    return ScenarioUpdateResult(status="updated", scenario=updated, evidence_refs=evidence_refs)


def update_scenario_with_log_odds(
    scenario: BayesianScenario,
    *,
    log_likelihood_ratio: float,
    evidence_refs: list[str] | None = None,
) -> ScenarioUpdateResult:
    """Apply a log-odds update to a scenario without changing Beta parameters."""
    prior_probability = (
        scenario.posterior_mean
        if scenario.posterior_mean is not None
        else scenario.prior_alpha / (scenario.prior_alpha + scenario.prior_beta)
    )
    posterior = log_odds_update(prior_probability, log_likelihood_ratio)
    merged_refs = _merge_refs(scenario.evidence_refs, evidence_refs or [])
    updated = _copy_scenario(
        scenario,
        evidence_refs=merged_refs,
        update_method=ScenarioUpdateMethod.LOG_ODDS,
        posterior_mean=posterior,
        posterior_ci_low_90=None,
        posterior_ci_high_90=None,
        calibrated_label=calibrated_label(posterior),
        updated_ts=_utc_now(),
    )
    return ScenarioUpdateResult(status="updated", scenario=updated, evidence_refs=merged_refs)


def _validate_positive_beta_params(alpha: float, beta: float) -> None:
    if alpha <= 0 or beta <= 0:
        raise ValueError("alpha and beta must be positive")
    if not math.isfinite(alpha) or not math.isfinite(beta):
        raise ValueError("alpha and beta must be finite")


def _dedupe_observations(
    observations: list[ScenarioEvidenceObservation],
) -> tuple[list[ScenarioEvidenceObservation], str]:
    grouped: dict[str, list[ScenarioEvidenceObservation]] = {}
    for item in observations:
        grouped.setdefault(_source_of_record_key(item), []).append(item)

    deduped: list[ScenarioEvidenceObservation] = []
    contradiction = ""
    for key in sorted(grouped):
        items = grouped[key]
        stances: set[ScenarioEvidenceStance] = {item.stance for item in items}
        if "mixed" in stances:
            contradiction = f"mixed evidence stance for source-of-record {key}"
            deduped.extend(items)
            continue
        if "supports" in stances and "refutes" in stances:
            contradiction = f"support/refute conflict for source-of-record {key}"
            deduped.extend(items)
            continue
        deduped.append(_representative_observation(items))

    return deduped, contradiction


def _representative_observation(items: list[ScenarioEvidenceObservation]) -> ScenarioEvidenceObservation:
    for stance in ("supports", "refutes", "neutral", "unknown"):
        for item in items:
            if item.stance == stance:
                return item
    return items[0]


def _source_of_record_key(observation: ScenarioEvidenceObservation) -> str:
    for field_name in ("source_record_id", "provenance_id", "source_ref", "locator", "evidence_id"):
        value = getattr(observation, field_name)
        if value:
            return f"{field_name}:{str(value).strip()}"
    return f"evidence_id:{observation.evidence_id}"


def _merge_refs(existing: list[str], incoming: list[str]) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for item in existing + incoming:
        ref = str(item or "").strip()
        if ref and ref not in seen:
            refs.append(ref)
            seen.add(ref)
    return refs


def _copy_scenario(scenario: BayesianScenario, **updates: object) -> BayesianScenario:
    payload = scenario.model_dump(mode="python")
    payload.update(updates)
    return BayesianScenario.model_validate(payload)


def _display_mode_for_count(evidence_count_n: int) -> ScenarioDisplayMode:
    if evidence_count_n < LOW_EVIDENCE_NUMERIC_THRESHOLD:
        return ScenarioDisplayMode.VERBAL_ONLY
    return ScenarioDisplayMode.INTERNAL_NUMERIC_ALLOWED


def _uncertainty_note_for_count(evidence_count_n: int) -> str:
    if evidence_count_n < LOW_EVIDENCE_NUMERIC_THRESHOLD:
        return "Fewer than 10 source-of-record observations; internal numeric display is suppressed."
    return "At least 10 source-of-record observations; internal numeric calculation is allowed."


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
