"""Scenario-policy definitions for shadow-mode evaluation."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from config import SCENARIO_SHADOW


BASELINE_SCENARIO_KEY = "baseline_deterministic"


class ScenarioPolicyWeights(BaseModel):
    reliability: float = 0.6
    cost: float = 0.15
    latency: float = 0.15
    feedback: float = 0.05
    uncertainty: float = 0.05


class ScenarioDefinition(BaseModel):
    scenario_key: str
    label: str
    description: str
    phase: str
    max_latency_ms: float
    max_cost_usd: float
    weights: ScenarioPolicyWeights
    hitl_uncertainty_threshold: float = 0.2


class ScenarioPosteriorSnapshot(BaseModel):
    scenario_key: str
    label: str
    phase: str
    observation_count: int = 0
    success_alpha: float = 1.0
    success_beta: float = 1.0
    feedback_alpha: float = 1.0
    feedback_beta: float = 1.0
    latency_mean_ms: float = 2500.0
    latency_precision: float = 0.001
    cost_mean_usd: float = 0.15
    cost_precision: float = 10.0
    updated_at: str = ""


class ScenarioCandidateScore(BaseModel):
    scenario_key: str
    label: str
    description: str
    expected_utility: float
    win_probability: float
    predicted_success_probability: float
    reliability_mean: float
    reliability_std: float
    mean_latency_ms: float
    mean_cost_usd: float
    calibration_brier: Optional[float] = None
    observation_count: int = 0
    is_baseline: bool = False
    is_best_expected: bool = False
    is_recommended: bool = False


class ScenarioEnsembleMember(BaseModel):
    scenario_key: str
    label: str
    probability: float


class ScenarioMetricSnapshot(BaseModel):
    scenario_key: str
    label: str
    observation_count: int = 0
    reliability_mean: float = 0.0
    reliability_std: float = 0.0
    calibration_brier: Optional[float] = None
    mean_latency_ms: float = 0.0
    mean_cost_usd: float = 0.0


class ScenarioComparisonAgainstBaseline(BaseModel):
    baseline: ScenarioMetricSnapshot
    candidate: ScenarioMetricSnapshot
    reliability_delta: float = 0.0
    calibration_delta: Optional[float] = None
    cost_delta_usd: float = 0.0
    latency_delta_ms: float = 0.0


class ScenarioPhaseShadowView(BaseModel):
    available: bool = True
    message: str = ""
    project_id: str = ""
    phase: str
    request_id: str = ""
    observed_at: str = ""
    shadow_mode: bool = True
    baseline_executed: bool = True
    baseline_selected_provider: str = ""
    baseline_selected_model: str = ""
    actual_provider_used: str = ""
    actual_model_used: str = ""
    sample_count: int = 0
    best_expected_scenario_key: str = BASELINE_SCENARIO_KEY
    best_expected_label: str = "Baseline deterministic"
    recommended_scenario_key: str = BASELINE_SCENARIO_KEY
    recommended_label: str = "Baseline deterministic"
    fallback_to_baseline: bool = True
    hitl_recommended: bool = False
    hitl_reasons: list[str] = Field(default_factory=list)
    ensemble: list[ScenarioEnsembleMember] = Field(default_factory=list)
    scenarios: list[ScenarioCandidateScore] = Field(default_factory=list)
    comparison_against_baseline: Optional[ScenarioComparisonAgainstBaseline] = None
    observed_success: bool = False
    observed_latency_ms: float = 0.0
    observed_cost_usd: float = 0.0


class ProjectScenarioShadowView(BaseModel):
    project_id: str
    available: bool = True
    message: str = ""
    phases: list[ScenarioPhaseShadowView] = Field(default_factory=list)


class ScenarioShadowRunResult(BaseModel):
    available: bool = True
    message: str = ""
    request_id: str = ""
    project_id: str = ""
    phase: str
    best_expected_scenario_key: str = BASELINE_SCENARIO_KEY
    recommended_scenario_key: str = BASELINE_SCENARIO_KEY
    fallback_to_baseline: bool = True
    hitl_recommended: bool = False


class ScenarioObservation(BaseModel):
    request_id: str
    project_id: str = ""
    phase: str
    scenario_key: str
    scenario_label: str
    observed_at: str
    baseline_selected_provider: str = ""
    baseline_selected_model: str = ""
    actual_provider_used: str = ""
    actual_model_used: str = ""
    predicted_success_probability: float
    actual_success: bool
    latency_ms: float
    cost_usd: float
    feedback_value: Optional[float] = None
    expected_utility: float
    win_probability: float
    is_baseline: bool = False
    is_best_expected: bool = False
    is_recommended: bool = False
    fallback_to_baseline: bool = False
    hitl_recommended: bool = False
    hitl_reason_summary: str = ""
    sample_count: int = 0


def phase_runtime_bounds(phase: str) -> tuple[float, float]:
    bounds = {
        "classify": (6000.0, 0.08),
        "hypotheses": (18000.0, 0.45),
        "gauntlet": (12000.0, 0.25),
        "audit": (15000.0, 0.30),
        "strategy": (22000.0, 0.70),
        "sqi": (12000.0, 0.20),
        "monitor": (8000.0, 0.12),
        "report": (18000.0, 0.35),
    }
    return bounds.get(phase, (12000.0, 0.25))


def bounded_scenarios_for_phase(phase: str) -> list[ScenarioDefinition]:
    baseline_latency, baseline_cost = phase_runtime_bounds(phase)
    scenarios = [
        ScenarioDefinition(
            scenario_key=BASELINE_SCENARIO_KEY,
            label="Baseline deterministic",
            description="Shadow mirror of the current deterministic runtime posture.",
            phase=phase,
            max_latency_ms=baseline_latency,
            max_cost_usd=baseline_cost,
            weights=ScenarioPolicyWeights(reliability=0.62, cost=0.14, latency=0.14, feedback=0.05, uncertainty=0.05),
            hitl_uncertainty_threshold=0.2,
        ),
        ScenarioDefinition(
            scenario_key="reliability_guarded",
            label="Reliability guarded",
            description="Favors higher acceptance reliability and lower uncertainty.",
            phase=phase,
            max_latency_ms=baseline_latency * 1.2,
            max_cost_usd=baseline_cost * 1.25,
            weights=ScenarioPolicyWeights(reliability=0.75, cost=0.08, latency=0.07, feedback=0.05, uncertainty=0.12),
            hitl_uncertainty_threshold=0.16,
        ),
        ScenarioDefinition(
            scenario_key="latency_guarded",
            label="Latency guarded",
            description="Penalizes slower calls and tighter latency envelopes.",
            phase=phase,
            max_latency_ms=baseline_latency * 0.65,
            max_cost_usd=baseline_cost * 1.05,
            weights=ScenarioPolicyWeights(reliability=0.48, cost=0.1, latency=0.3, feedback=0.04, uncertainty=0.08),
            hitl_uncertainty_threshold=0.2,
        ),
        ScenarioDefinition(
            scenario_key="cost_guarded",
            label="Cost guarded",
            description="Penalizes higher call cost and stricter cost envelopes.",
            phase=phase,
            max_latency_ms=baseline_latency * 1.05,
            max_cost_usd=baseline_cost * 0.7,
            weights=ScenarioPolicyWeights(reliability=0.5, cost=0.28, latency=0.1, feedback=0.04, uncertainty=0.08),
            hitl_uncertainty_threshold=0.2,
        ),
    ]
    return scenarios[: SCENARIO_SHADOW.max_scenarios]


def new_request_id(phase: str) -> str:
    return f"{phase}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
