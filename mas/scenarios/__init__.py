"""Scenario-policy shadow-mode package."""

from scenarios.engine import ScenarioShadowEngine, run_shadow_evaluation
from scenarios.eval import build_phase_shadow_view, build_project_shadow_view
from scenarios.policy import (
    BASELINE_SCENARIO_KEY,
    ProjectScenarioShadowView,
    ScenarioPhaseShadowView,
    ScenarioShadowRunResult,
)
from scenarios.sqlite_store import ScenarioSQLiteStore
from scenarios.calibration import scenario_to_calibration_snapshot, scenario_to_prediction
from scenarios.models import (
    BayesianScenario,
    ScenarioDisplayMode,
    ScenarioEvidenceObservation,
    ScenarioFalsifier,
    ScenarioPriorSource,
    ScenarioUpdateMethod,
    ScenarioUpdateResult,
)
from scenarios.update import (
    beta_binomial_update,
    build_scenario_from_hypothesis,
    calibrated_label,
    log_odds_update,
    update_scenario_with_evidence,
    update_scenario_with_log_odds,
)

__all__ = [
    "BASELINE_SCENARIO_KEY",
    "ProjectScenarioShadowView",
    "ScenarioPhaseShadowView",
    "ScenarioSQLiteStore",
    "ScenarioShadowEngine",
    "ScenarioShadowRunResult",
    "build_phase_shadow_view",
    "build_project_shadow_view",
    "run_shadow_evaluation",
    "BayesianScenario",
    "ScenarioDisplayMode",
    "ScenarioEvidenceObservation",
    "ScenarioFalsifier",
    "ScenarioPriorSource",
    "ScenarioUpdateMethod",
    "ScenarioUpdateResult",
    "beta_binomial_update",
    "build_scenario_from_hypothesis",
    "calibrated_label",
    "log_odds_update",
    "scenario_to_calibration_snapshot",
    "scenario_to_prediction",
    "update_scenario_with_evidence",
    "update_scenario_with_log_odds",
]
