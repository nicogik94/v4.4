import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scenarios.calibration import (  # noqa: E402
    scenario_to_calibration_snapshot,
    scenario_to_prediction,
)
from scenarios.models import (  # noqa: E402
    BayesianScenario,
    ScenarioDisplayMode,
    ScenarioFalsifier,
    ScenarioPriorSource,
    ScenarioUpdateMethod,
)
from state import CalibrationSnapshot, Prediction  # noqa: E402


def scenario() -> BayesianScenario:
    return BayesianScenario(
        scenario_id="S-cal",
        decision_ref="D1",
        hypothesis_ref="H-cal",
        prior_alpha=4.0,
        prior_beta=6.0,
        prior_source=ScenarioPriorSource.HYPOTHESIS,
        evidence_refs=["E1"],
        update_method=ScenarioUpdateMethod.BETA_BINOMIAL,
        posterior_alpha=7.0,
        posterior_beta=8.0,
        posterior_mean=7.0 / 15.0,
        posterior_ci_low_90=0.22,
        posterior_ci_high_90=0.72,
        calibrated_label="uncertain",
        evidence_count_n=5,
        display_mode=ScenarioDisplayMode.VERBAL_ONLY,
        assumptions=[],
        falsifier=ScenarioFalsifier(
            observable="Retention",
            threshold=">= 80%",
            time_window="90d",
        ),
        uncertainty_note="Internal calibration helper test.",
    )


def test_scenario_to_prediction_uses_existing_prediction_shape():
    result = scenario_to_prediction(
        scenario(),
        actual_outcome=True,
        phase="strategy",
        framework_used="bayesian_scenarios_t1",
    )

    assert isinstance(result, Prediction)
    assert result.hypothesis_id == "H-cal"
    assert result.predicted_probability == 7.0 / 15.0
    assert result.actual_outcome is True
    assert result.phase == "strategy"
    assert result.framework_used == "bayesian_scenarios_t1"


def test_scenario_to_calibration_snapshot_uses_existing_snapshot_shape():
    result = scenario_to_calibration_snapshot(
        scenario(),
        brier_score=0.12,
        sqi_overall=80,
        det_score_overall=77,
        dq_total=90,
    )

    assert isinstance(result, CalibrationSnapshot)
    assert result.snapshot_id.startswith("scenario_calibration_")
    assert result.recorded_at
    assert result.brier_score == 0.12
    assert result.sqi_overall == 80
    assert result.det_score_overall == 77
    assert result.dq_total == 90
    assert "Internal Bayesian scenario calibration adapter" in result.notes
