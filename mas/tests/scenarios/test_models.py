import sys
from pathlib import Path

import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scenarios.models import (  # noqa: E402
    BayesianScenario,
    ScenarioDisplayMode,
    ScenarioFalsifier,
    ScenarioPriorSource,
    ScenarioUpdateMethod,
)
from state import ProjectState  # noqa: E402


def falsifier() -> ScenarioFalsifier:
    return ScenarioFalsifier(
        observable="CTR lift",
        threshold=">= 15%",
        time_window="30d",
    )


def scenario(**overrides) -> BayesianScenario:
    payload = {
        "scenario_id": "S1",
        "decision_ref": "D1",
        "hypothesis_ref": "H1",
        "prior_alpha": 6.0,
        "prior_beta": 4.0,
        "prior_source": ScenarioPriorSource.HYPOTHESIS,
        "evidence_refs": [],
        "update_method": ScenarioUpdateMethod.NONE,
        "posterior_alpha": 6.0,
        "posterior_beta": 4.0,
        "posterior_mean": 0.6,
        "posterior_ci_low_90": 0.35,
        "posterior_ci_high_90": 0.82,
        "calibrated_label": "likely",
        "evidence_count_n": 0,
        "display_mode": ScenarioDisplayMode.VERBAL_ONLY,
        "assumptions": [],
        "falsifier": falsifier(),
        "uncertainty_note": "Assumptions pending T1 internal review.",
    }
    payload.update(overrides)
    return BayesianScenario(**payload)


def test_valid_scenario_accepts_empty_assumptions_with_uncertainty_note():
    item = scenario()

    assert item.scenario_id == "S1"
    assert item.assumptions == []
    assert item.uncertainty_note


def test_invalid_priors_and_posteriors_are_rejected():
    with pytest.raises(ValidationError):
        scenario(prior_alpha=0)

    with pytest.raises(ValidationError):
        scenario(prior_beta=-1)

    with pytest.raises(ValidationError):
        scenario(posterior_alpha=0)

    with pytest.raises(ValidationError):
        scenario(posterior_beta=-0.1)


def test_invalid_evidence_count_is_rejected():
    with pytest.raises(ValidationError):
        scenario(evidence_count_n=-1)


def test_falsifier_requires_observable_threshold_and_time_window():
    with pytest.raises(ValidationError):
        ScenarioFalsifier(observable="", threshold=">= 10", time_window="30d")

    with pytest.raises(ValidationError):
        ScenarioFalsifier(observable="CTR", threshold="", time_window="30d")

    with pytest.raises(ValidationError):
        ScenarioFalsifier(observable="CTR", threshold=">= 10", time_window="")


def test_assumptions_must_be_a_list():
    with pytest.raises(ValidationError):
        scenario(assumptions="implicit assumption")


def test_empty_assumptions_require_uncertainty_note():
    with pytest.raises(ValidationError):
        scenario(assumptions=[], uncertainty_note="")


def test_no_fake_precision_under_threshold():
    with pytest.raises(ValidationError):
        scenario(
            evidence_count_n=9,
            display_mode=ScenarioDisplayMode.INTERNAL_NUMERIC_ALLOWED,
        )

    item = scenario(
        evidence_count_n=10,
        display_mode=ScenarioDisplayMode.INTERNAL_NUMERIC_ALLOWED,
    )

    assert item.display_mode == ScenarioDisplayMode.INTERNAL_NUMERIC_ALLOWED


def test_project_state_load_remains_backward_compatible_without_scenarios_field():
    state = ProjectState(project_id="legacy-scenarios", project_name="Legacy", brief="No scenario field.")
    payload = state.model_dump(mode="json")
    assert "scenarios" not in payload

    loaded = ProjectState.model_validate(payload)

    assert loaded.project_id == "legacy-scenarios"
    assert not hasattr(loaded, "scenarios")
