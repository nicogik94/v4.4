"""Shadow-mode scenario generation, Monte Carlo scoring, and online updates."""
from __future__ import annotations

import hashlib
import logging
import math
import random
from datetime import datetime, timezone
from statistics import mean
from typing import Optional

from config import SCENARIO_SHADOW
from scenarios.policy import (
    BASELINE_SCENARIO_KEY,
    ScenarioCandidateScore,
    ScenarioComparisonAgainstBaseline,
    ScenarioDefinition,
    ScenarioMetricSnapshot,
    ScenarioObservation,
    ScenarioPosteriorSnapshot,
    ScenarioShadowRunResult,
    bounded_scenarios_for_phase,
    new_request_id,
    phase_runtime_bounds,
)
from scenarios.posteriors import (
    beta_mean,
    beta_std,
    feedback_mean,
    reliability_snapshot,
    sample_beta,
    sample_positive_normal,
    update_beta,
    update_normal_mean,
)
from scenarios.sqlite_store import ScenarioSQLiteStore


logger = logging.getLogger(__name__)


def _get_store(path: Optional[str] = None) -> ScenarioSQLiteStore:
    return ScenarioSQLiteStore(path or SCENARIO_SHADOW.sqlite_path)


class ScenarioShadowEngine:
    def __init__(self, *, store: ScenarioSQLiteStore | None = None):
        self.store = store or _get_store()

    def evaluate_request(
        self,
        *,
        phase: str,
        project_id: str = "",
        baseline_selected_provider: str = "",
        baseline_selected_model: str = "",
        actual_provider_used: str = "",
        actual_model_used: str = "",
        response_ok: bool,
        latency_ms: float,
        cost_usd: float,
        feedback_value: float | None = None,
    ) -> ScenarioShadowRunResult:
        if not SCENARIO_SHADOW.enabled:
            return ScenarioShadowRunResult(
                available=False,
                message="Scenario shadow disabled; deterministic baseline remains active.",
                phase=phase,
                project_id=project_id,
            )

        request_id = new_request_id(phase)
        observed_at = datetime.now(timezone.utc).isoformat()

        try:
            scenarios = bounded_scenarios_for_phase(phase)[: max(1, SCENARIO_SHADOW.max_scenarios)]
            if not scenarios:
                return ScenarioShadowRunResult(
                    available=False,
                    message="No bounded scenarios configured; deterministic baseline remains active.",
                    request_id=request_id,
                    phase=phase,
                    project_id=project_id,
                )

            posteriors = {
                scenario.scenario_key: self._load_or_seed_posterior(phase, scenario)
                for scenario in scenarios
            }
            candidate_scores = self._score_scenarios(request_id, scenarios, posteriors)
            sorted_scores = sorted(
                candidate_scores,
                key=lambda item: (item.expected_utility, item.win_probability, item.reliability_mean),
                reverse=True,
            )
            best_expected = sorted_scores[0]
            baseline = next(
                (candidate for candidate in candidate_scores if candidate.is_baseline),
                sorted_scores[0],
            )
            hitl_reasons = self._hitl_reasons(sorted_scores)
            hitl_recommended = bool(hitl_reasons)
            fallback_to_baseline = hitl_recommended or not best_expected.scenario_key
            recommended = baseline if fallback_to_baseline else best_expected

            actual_success_map = {
                scenario.scenario_key: self._scenario_succeeds(
                    scenario,
                    response_ok=response_ok,
                    latency_ms=latency_ms,
                    cost_usd=cost_usd,
                    feedback_value=feedback_value,
                )
                for scenario in scenarios
            }

            for candidate in candidate_scores:
                self.store.record_observation(
                    ScenarioObservation(
                        request_id=request_id,
                        project_id=project_id,
                        phase=phase,
                        scenario_key=candidate.scenario_key,
                        scenario_label=candidate.label,
                        observed_at=observed_at,
                        baseline_selected_provider=baseline_selected_provider,
                        baseline_selected_model=baseline_selected_model,
                        actual_provider_used=actual_provider_used,
                        actual_model_used=actual_model_used,
                        predicted_success_probability=candidate.predicted_success_probability,
                        actual_success=actual_success_map[candidate.scenario_key],
                        latency_ms=latency_ms,
                        cost_usd=cost_usd,
                        feedback_value=feedback_value,
                        expected_utility=candidate.expected_utility,
                        win_probability=candidate.win_probability,
                        is_baseline=candidate.is_baseline,
                        is_best_expected=candidate.scenario_key == best_expected.scenario_key,
                        is_recommended=candidate.scenario_key == recommended.scenario_key,
                        fallback_to_baseline=fallback_to_baseline,
                        hitl_recommended=hitl_recommended,
                        hitl_reason_summary="; ".join(hitl_reasons),
                        sample_count=min(SCENARIO_SHADOW.monte_carlo_samples, SCENARIO_SHADOW.hard_sample_cap),
                    )
                )
                updated = self._update_posterior(
                    posteriors[candidate.scenario_key],
                    candidate_label=candidate.label,
                    success=actual_success_map[candidate.scenario_key],
                    latency_ms=latency_ms,
                    cost_usd=cost_usd,
                    feedback_value=feedback_value,
                    observed_at=observed_at,
                )
                self.store.save_posterior(updated)

            logger.info(
                "scenario.shadow %s",
                {
                    "phase": phase,
                    "project_id": project_id,
                    "request_id": request_id,
                    "best_expected": best_expected.scenario_key,
                    "recommended": recommended.scenario_key,
                    "fallback_to_baseline": fallback_to_baseline,
                    "hitl_recommended": hitl_recommended,
                    "baseline_selected_provider": baseline_selected_provider,
                    "baseline_selected_model": baseline_selected_model,
                    "actual_provider_used": actual_provider_used,
                    "actual_model_used": actual_model_used,
                    "observed_success": response_ok,
                    "latency_ms": latency_ms,
                    "cost_usd": cost_usd,
                },
            )
            return ScenarioShadowRunResult(
                available=True,
                request_id=request_id,
                phase=phase,
                project_id=project_id,
                best_expected_scenario_key=best_expected.scenario_key,
                recommended_scenario_key=recommended.scenario_key,
                fallback_to_baseline=fallback_to_baseline,
                hitl_recommended=hitl_recommended,
            )
        except Exception as exc:
            logger.warning("scenario shadow unavailable for %s: %s", phase, exc)
            return ScenarioShadowRunResult(
                available=False,
                message="Scenario shadow unavailable; deterministic baseline remains active.",
                request_id=request_id,
                phase=phase,
                project_id=project_id,
            )

    def _load_or_seed_posterior(self, phase: str, scenario: ScenarioDefinition) -> ScenarioPosteriorSnapshot:
        existing = self.store.load_posterior(phase, scenario.scenario_key)
        if existing is not None:
            return existing
        base_latency, base_cost = phase_runtime_bounds(phase)
        return ScenarioPosteriorSnapshot(
            phase=phase,
            scenario_key=scenario.scenario_key,
            label=scenario.label,
            success_alpha=3.0 if scenario.scenario_key == BASELINE_SCENARIO_KEY else 2.0,
            success_beta=2.0 if scenario.scenario_key == BASELINE_SCENARIO_KEY else 2.0,
            feedback_alpha=1.0,
            feedback_beta=1.0,
            latency_mean_ms=scenario.max_latency_ms if scenario.max_latency_ms > 0 else base_latency,
            latency_precision=0.001,
            cost_mean_usd=scenario.max_cost_usd if scenario.max_cost_usd > 0 else base_cost,
            cost_precision=10.0,
            observation_count=0,
            updated_at="",
        )

    def _score_scenarios(
        self,
        request_id: str,
        scenarios: list[ScenarioDefinition],
        posteriors: dict[str, ScenarioPosteriorSnapshot],
    ) -> list[ScenarioCandidateScore]:
        sample_count = min(max(1, SCENARIO_SHADOW.monte_carlo_samples), SCENARIO_SHADOW.hard_sample_cap)
        seed = int(hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:16], 16)
        rng = random.Random(seed)
        wins = {scenario.scenario_key: 0 for scenario in scenarios}
        utilities = {scenario.scenario_key: [] for scenario in scenarios}

        for _ in range(sample_count):
            sample_utilities: dict[str, float] = {}
            for scenario in scenarios:
                posterior = posteriors[scenario.scenario_key]
                reliability_sample = sample_beta(posterior.success_alpha, posterior.success_beta, rng)
                feedback_sample = sample_beta(posterior.feedback_alpha, posterior.feedback_beta, rng)
                latency_sample = sample_positive_normal(posterior.latency_mean_ms, posterior.latency_precision, rng)
                cost_sample = sample_positive_normal(posterior.cost_mean_usd, posterior.cost_precision, rng)
                uncertainty = beta_std(posterior.success_alpha, posterior.success_beta)
                utility = self._utility(
                    scenario,
                    reliability=reliability_sample,
                    feedback=feedback_sample,
                    latency_ms=latency_sample,
                    cost_usd=cost_sample,
                    uncertainty=uncertainty,
                )
                sample_utilities[scenario.scenario_key] = utility
                utilities[scenario.scenario_key].append(utility)
            winner = max(sample_utilities.items(), key=lambda item: item[1])[0]
            wins[winner] += 1

        scores: list[ScenarioCandidateScore] = []
        for scenario in scenarios:
            posterior = posteriors[scenario.scenario_key]
            reliability_mean, reliability_std = reliability_snapshot(posterior)
            scores.append(
                ScenarioCandidateScore(
                    scenario_key=scenario.scenario_key,
                    label=scenario.label,
                    description=scenario.description,
                    expected_utility=round(mean(utilities[scenario.scenario_key]), 6),
                    win_probability=round(wins[scenario.scenario_key] / sample_count, 6),
                    predicted_success_probability=round(reliability_mean, 6),
                    reliability_mean=round(reliability_mean, 6),
                    reliability_std=round(reliability_std, 6),
                    mean_latency_ms=round(posterior.latency_mean_ms, 3),
                    mean_cost_usd=round(posterior.cost_mean_usd, 6),
                    calibration_brier=self._brier_score(scenario.phase, scenario.scenario_key),
                    observation_count=posterior.observation_count,
                    is_baseline=scenario.scenario_key == BASELINE_SCENARIO_KEY,
                )
            )
        return scores

    def _scenario_succeeds(
        self,
        scenario: ScenarioDefinition,
        *,
        response_ok: bool,
        latency_ms: float,
        cost_usd: float,
        feedback_value: float | None,
    ) -> bool:
        if not response_ok:
            return False
        if latency_ms > scenario.max_latency_ms:
            return False
        if cost_usd > scenario.max_cost_usd:
            return False
        if feedback_value is not None and feedback_value < 0.5:
            return False
        return True

    def _utility(
        self,
        scenario: ScenarioDefinition,
        *,
        reliability: float,
        feedback: float,
        latency_ms: float,
        cost_usd: float,
        uncertainty: float,
    ) -> float:
        latency_norm = min(latency_ms / max(scenario.max_latency_ms, 1.0), 2.0)
        cost_norm = min(cost_usd / max(scenario.max_cost_usd, 1e-6), 2.0)
        weights = scenario.weights
        return (
            (weights.reliability * reliability)
            + (weights.feedback * feedback)
            - (weights.latency * latency_norm)
            - (weights.cost * cost_norm)
            - (weights.uncertainty * uncertainty)
        )

    def _hitl_reasons(self, sorted_scores: list[ScenarioCandidateScore]) -> list[str]:
        if not sorted_scores:
            return ["No scenario scores available."]
        top = sorted_scores[0]
        second = sorted_scores[1] if len(sorted_scores) > 1 else None
        reasons: list[str] = []
        if top.win_probability < SCENARIO_SHADOW.hitl_min_top_probability:
            reasons.append(
                f"Top scenario win probability {top.win_probability:.2f} is below {SCENARIO_SHADOW.hitl_min_top_probability:.2f}."
            )
        if top.reliability_mean < SCENARIO_SHADOW.hitl_min_reliability:
            reasons.append(
                f"Top scenario reliability {top.reliability_mean:.2f} is below {SCENARIO_SHADOW.hitl_min_reliability:.2f}."
            )
        if second is not None and abs(top.expected_utility - second.expected_utility) < SCENARIO_SHADOW.hitl_min_margin:
            reasons.append(
                f"Top-two scenario utility gap {abs(top.expected_utility - second.expected_utility):.3f} is below {SCENARIO_SHADOW.hitl_min_margin:.3f}."
            )
        return reasons

    def _update_posterior(
        self,
        snapshot: ScenarioPosteriorSnapshot,
        *,
        candidate_label: str,
        success: bool,
        latency_ms: float,
        cost_usd: float,
        feedback_value: float | None,
        observed_at: str,
    ) -> ScenarioPosteriorSnapshot:
        success_alpha, success_beta = update_beta(snapshot.success_alpha, snapshot.success_beta, success=success)
        feedback_alpha = snapshot.feedback_alpha
        feedback_beta = snapshot.feedback_beta
        if feedback_value is not None:
            feedback_alpha, feedback_beta = update_beta(
                feedback_alpha,
                feedback_beta,
                success=feedback_value >= 0.5,
            )
        latency_mean, latency_precision = update_normal_mean(
            snapshot.latency_mean_ms,
            snapshot.latency_precision,
            latency_ms,
            observation_precision=0.001,
        )
        cost_mean, cost_precision = update_normal_mean(
            snapshot.cost_mean_usd,
            snapshot.cost_precision,
            cost_usd,
            observation_precision=10.0,
        )
        return ScenarioPosteriorSnapshot(
            phase=snapshot.phase,
            scenario_key=snapshot.scenario_key,
            label=candidate_label,
            success_alpha=success_alpha,
            success_beta=success_beta,
            feedback_alpha=feedback_alpha,
            feedback_beta=feedback_beta,
            latency_mean_ms=latency_mean,
            latency_precision=latency_precision,
            cost_mean_usd=cost_mean,
            cost_precision=cost_precision,
            observation_count=snapshot.observation_count + 1,
            updated_at=observed_at,
        )

    def _brier_score(self, phase: str, scenario_key: str) -> float | None:
        rows = self.store.list_observations_for_scenario(phase, scenario_key)
        if not rows:
            return None
        total = 0.0
        for row in rows:
            actual = 1.0 if int(row["actual_success"]) else 0.0
            total += (float(row["predicted_success_probability"]) - actual) ** 2
        return round(total / len(rows), 6)


def run_shadow_evaluation(
    *,
    phase: str,
    project_id: str = "",
    baseline_selected_provider: str = "",
    baseline_selected_model: str = "",
    actual_provider_used: str = "",
    actual_model_used: str = "",
    response_ok: bool,
    latency_ms: float,
    cost_usd: float,
    feedback_value: float | None = None,
    store: ScenarioSQLiteStore | None = None,
) -> ScenarioShadowRunResult:
    engine = ScenarioShadowEngine(store=store)
    return engine.evaluate_request(
        phase=phase,
        project_id=project_id,
        baseline_selected_provider=baseline_selected_provider,
        baseline_selected_model=baseline_selected_model,
        actual_provider_used=actual_provider_used,
        actual_model_used=actual_model_used,
        response_ok=response_ok,
        latency_ms=latency_ms,
        cost_usd=cost_usd,
        feedback_value=feedback_value,
    )
