"""Internal calibration helpers for Bayesian scenarios.

The helpers here only adapt scenario objects to existing state calibration
records. They do not expose calibration through routes or product surfaces.
"""
from __future__ import annotations

import hashlib
from datetime import datetime

from scenarios.models import BayesianScenario
from state import CalibrationSnapshot, Prediction


def scenario_to_prediction(
    scenario: BayesianScenario,
    *,
    actual_outcome: bool | None = None,
    phase: str = "scenario",
    framework_used: str = "bayesian_scenarios_t1",
) -> Prediction:
    """Convert a scenario to the existing internal Prediction structure."""
    probability = _scenario_probability(scenario)
    return Prediction(
        hypothesis_id=scenario.hypothesis_ref,
        predicted_probability=probability,
        actual_outcome=actual_outcome,
        phase=phase,
        framework_used=framework_used,
        timestamp=scenario.updated_ts,
    )


def scenario_to_calibration_snapshot(
    scenario: BayesianScenario,
    *,
    brier_score: float | None = None,
    sqi_overall: float | None = None,
    det_score_overall: float | None = None,
    dq_total: float | None = None,
    notes: str | None = None,
) -> CalibrationSnapshot:
    """Convert a scenario to the existing internal CalibrationSnapshot shape."""
    recorded_at = _isoformat(scenario.updated_ts)
    snapshot_id = _stable_snapshot_id(scenario, recorded_at)
    return CalibrationSnapshot(
        snapshot_id=snapshot_id,
        recorded_at=recorded_at,
        brier_score=brier_score,
        sqi_overall=sqi_overall,
        det_score_overall=det_score_overall,
        dq_total=dq_total,
        notes=notes or f"Internal Bayesian scenario calibration adapter for {scenario.scenario_id}",
    )


def _scenario_probability(scenario: BayesianScenario) -> float:
    if scenario.posterior_mean is not None:
        return scenario.posterior_mean
    return scenario.prior_alpha / (scenario.prior_alpha + scenario.prior_beta)


def _stable_snapshot_id(scenario: BayesianScenario, recorded_at: str) -> str:
    material = "|".join(
        [
            scenario.scenario_id,
            scenario.hypothesis_ref,
            recorded_at,
            f"{_scenario_probability(scenario):.12f}",
        ]
    )
    digest = hashlib.sha1(material.encode("utf-8")).hexdigest()[:12]
    return f"scenario_calibration_{digest}"


def _isoformat(value: datetime) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)
