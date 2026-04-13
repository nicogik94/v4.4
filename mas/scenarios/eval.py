"""Read-side evaluation and API summaries for shadow scenario policy."""
from __future__ import annotations

from typing import Optional

from config import PHASES, SCENARIO_SHADOW
from scenarios.policy import (
    BASELINE_SCENARIO_KEY,
    ProjectScenarioShadowView,
    ScenarioCandidateScore,
    ScenarioComparisonAgainstBaseline,
    ScenarioEnsembleMember,
    ScenarioMetricSnapshot,
    ScenarioPhaseShadowView,
)
from scenarios.posteriors import reliability_snapshot
from scenarios.sqlite_store import ScenarioSQLiteStore


def _get_store(path: str | None = None) -> ScenarioSQLiteStore:
    return ScenarioSQLiteStore(path or SCENARIO_SHADOW.sqlite_path)


def build_project_shadow_view(project_id: str, *, store: ScenarioSQLiteStore | None = None) -> ProjectScenarioShadowView:
    store = store or _get_store()
    phases = [phase for phase in PHASES if phase in set(store.list_project_phases(project_id))]
    if not phases:
        return ProjectScenarioShadowView(
            project_id=project_id,
            available=False,
            message="No shadow scenario observations recorded for this project yet.",
        )
    phase_views = [build_phase_shadow_view(project_id, phase, store=store) for phase in phases]
    available = any(view.available for view in phase_views)
    return ProjectScenarioShadowView(
        project_id=project_id,
        available=available,
        message="" if available else "No shadow scenario observations recorded for this project yet.",
        phases=phase_views,
    )


def build_phase_shadow_view(
    project_id: str,
    phase: str,
    *,
    store: ScenarioSQLiteStore | None = None,
) -> ScenarioPhaseShadowView:
    store = store or _get_store()
    request_id = store.latest_request_id(project_id, phase)
    if not request_id:
        return ScenarioPhaseShadowView(
            available=False,
            message="No shadow scenario observations recorded for this phase yet.",
            project_id=project_id,
            phase=phase,
        )

    rows = store.list_request_observations(request_id, phase)
    if not rows:
        return ScenarioPhaseShadowView(
            available=False,
            message="No shadow scenario observations recorded for this phase yet.",
            project_id=project_id,
            phase=phase,
        )

    scenario_scores: list[ScenarioCandidateScore] = []
    baseline_row = None
    best_row = None
    recommended_row = None
    for row in rows:
        posterior = store.load_posterior(phase, str(row["scenario_key"]))
        reliability_mean, reliability_std = (0.5, 0.288675)
        observation_count = 0
        mean_latency = float(row["latency_ms"])
        mean_cost = float(row["cost_usd"])
        if posterior is not None:
            reliability_mean, reliability_std = reliability_snapshot(posterior)
            observation_count = posterior.observation_count
            mean_latency = posterior.latency_mean_ms
            mean_cost = posterior.cost_mean_usd
        score = ScenarioCandidateScore(
            scenario_key=str(row["scenario_key"]),
            label=str(row["scenario_label"]),
            description="",
            expected_utility=float(row["expected_utility"]),
            win_probability=float(row["win_probability"]),
            predicted_success_probability=float(row["predicted_success_probability"]),
            reliability_mean=round(reliability_mean, 6),
            reliability_std=round(reliability_std, 6),
            mean_latency_ms=round(mean_latency, 3),
            mean_cost_usd=round(mean_cost, 6),
            calibration_brier=_brier_score(store, phase, str(row["scenario_key"])),
            observation_count=observation_count,
            is_baseline=bool(row["is_baseline"]),
            is_best_expected=bool(row["is_best_expected"]),
            is_recommended=bool(row["is_recommended"]),
        )
        scenario_scores.append(score)
        if score.is_baseline:
            baseline_row = row
        if score.is_best_expected:
            best_row = row
        if score.is_recommended:
            recommended_row = row

    scenario_scores.sort(key=lambda item: (item.expected_utility, item.win_probability), reverse=True)
    if baseline_row is None:
        baseline_row = next((row for row in rows if str(row["scenario_key"]) == BASELINE_SCENARIO_KEY), rows[0])
    if best_row is None:
        best_row = rows[0]
    if recommended_row is None:
        recommended_row = baseline_row

    baseline_score = next((item for item in scenario_scores if item.scenario_key == str(baseline_row["scenario_key"])), scenario_scores[0])
    best_score = next((item for item in scenario_scores if item.scenario_key == str(best_row["scenario_key"])), scenario_scores[0])
    recommended_score = next((item for item in scenario_scores if item.scenario_key == str(recommended_row["scenario_key"])), baseline_score)
    ensemble = [
        ScenarioEnsembleMember(
            scenario_key=item.scenario_key,
            label=item.label,
            probability=item.win_probability,
        )
        for item in scenario_scores
        if item.win_probability >= SCENARIO_SHADOW.ensemble_min_probability or item.is_best_expected
    ]

    return ScenarioPhaseShadowView(
        available=True,
        project_id=project_id,
        phase=phase,
        request_id=request_id,
        observed_at=str(rows[0]["observed_at"]),
        baseline_executed=True,
        baseline_selected_provider=str(rows[0]["baseline_selected_provider"]),
        baseline_selected_model=str(rows[0]["baseline_selected_model"]),
        actual_provider_used=str(rows[0]["actual_provider_used"]),
        actual_model_used=str(rows[0]["actual_model_used"]),
        sample_count=int(rows[0]["sample_count"]),
        best_expected_scenario_key=best_score.scenario_key,
        best_expected_label=best_score.label,
        recommended_scenario_key=recommended_score.scenario_key,
        recommended_label=recommended_score.label,
        fallback_to_baseline=bool(rows[0]["fallback_to_baseline"]),
        hitl_recommended=bool(rows[0]["hitl_recommended"]),
        hitl_reasons=[reason.strip() for reason in str(rows[0]["hitl_reason_summary"]).split(";") if reason.strip()],
        ensemble=ensemble,
        scenarios=scenario_scores,
        comparison_against_baseline=_comparison_against_baseline(baseline_score, best_score),
        observed_success=bool(baseline_row["actual_success"]),
        observed_latency_ms=float(baseline_row["latency_ms"]),
        observed_cost_usd=float(baseline_row["cost_usd"]),
    )


def _metric_snapshot(score: ScenarioCandidateScore) -> ScenarioMetricSnapshot:
    return ScenarioMetricSnapshot(
        scenario_key=score.scenario_key,
        label=score.label,
        observation_count=score.observation_count,
        reliability_mean=score.reliability_mean,
        reliability_std=score.reliability_std,
        calibration_brier=score.calibration_brier,
        mean_latency_ms=score.mean_latency_ms,
        mean_cost_usd=score.mean_cost_usd,
    )


def _comparison_against_baseline(
    baseline: ScenarioCandidateScore,
    candidate: ScenarioCandidateScore,
) -> ScenarioComparisonAgainstBaseline:
    calibration_delta = None
    if baseline.calibration_brier is not None and candidate.calibration_brier is not None:
        calibration_delta = round(candidate.calibration_brier - baseline.calibration_brier, 6)
    return ScenarioComparisonAgainstBaseline(
        baseline=_metric_snapshot(baseline),
        candidate=_metric_snapshot(candidate),
        reliability_delta=round(candidate.reliability_mean - baseline.reliability_mean, 6),
        calibration_delta=calibration_delta,
        cost_delta_usd=round(candidate.mean_cost_usd - baseline.mean_cost_usd, 6),
        latency_delta_ms=round(candidate.mean_latency_ms - baseline.mean_latency_ms, 3),
    )


def _brier_score(store: ScenarioSQLiteStore, phase: str, scenario_key: str) -> Optional[float]:
    rows = store.list_observations_for_scenario(phase, scenario_key)
    if not rows:
        return None
    total = 0.0
    for row in rows:
        actual = 1.0 if bool(row["actual_success"]) else 0.0
        total += (float(row["predicted_success_probability"]) - actual) ** 2
    return round(total / len(rows), 6)
