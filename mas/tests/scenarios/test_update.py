import math
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scenarios.models import (  # noqa: E402
    BayesianScenario,
    ScenarioDisplayMode,
    ScenarioEvidenceObservation,
    ScenarioFalsifier,
    ScenarioPriorSource,
    ScenarioUpdateMethod,
)
from scenarios.update import (  # noqa: E402
    beta_binomial_update,
    calibrated_label,
    log_odds_update,
    update_scenario_with_evidence,
)


def falsifier() -> ScenarioFalsifier:
    return ScenarioFalsifier(
        observable="Weekly conversion",
        threshold=">= 10%",
        time_window="4w",
    )


def scenario() -> BayesianScenario:
    return BayesianScenario(
        scenario_id="S1",
        decision_ref="D1",
        hypothesis_ref="H1",
        prior_alpha=2.0,
        prior_beta=2.0,
        prior_source=ScenarioPriorSource.HYPOTHESIS,
        evidence_refs=[],
        update_method=ScenarioUpdateMethod.NONE,
        posterior_alpha=2.0,
        posterior_beta=2.0,
        posterior_mean=0.5,
        posterior_ci_low_90=0.135,
        posterior_ci_high_90=0.865,
        calibrated_label="uncertain",
        evidence_count_n=0,
        display_mode=ScenarioDisplayMode.INTERNAL_ONLY,
        assumptions=[],
        falsifier=falsifier(),
        uncertainty_note="Low evidence internal scenario.",
    )


def observation(evidence_id: str, stance: str, **overrides) -> ScenarioEvidenceObservation:
    payload = {
        "evidence_id": evidence_id,
        "stance": stance,
        "source_record_id": None,
        "source_ref": None,
        "provenance_id": None,
        "locator": None,
        "captured_at": None,
    }
    payload.update(overrides)
    return ScenarioEvidenceObservation(**payload)


def test_beta_binomial_math():
    assert beta_binomial_update(3, 7, 5, 7) == (8, 9)


def test_beta_binomial_invalid_priors_and_counts():
    with pytest.raises(ValueError):
        beta_binomial_update(0, 1, 0, 0)
    with pytest.raises(ValueError):
        beta_binomial_update(1, -1, 0, 0)
    with pytest.raises(ValueError):
        beta_binomial_update(1, 1, -1, 2)
    with pytest.raises(ValueError):
        beta_binomial_update(1, 1, 1, -1)
    with pytest.raises(ValueError):
        beta_binomial_update(1, 1, 3, 2)


def test_log_odds_math():
    assert log_odds_update(0.5, math.log(3)) == pytest.approx(0.75)
    assert log_odds_update(0.25, math.log(3)) == pytest.approx(0.5)


def test_log_odds_invalid_probability():
    with pytest.raises(ValueError):
        log_odds_update(0.0, 1.0)
    with pytest.raises(ValueError):
        log_odds_update(1.0, 1.0)


def test_calibrated_verbal_label_bands():
    assert calibrated_label(0.19) == "very unlikely"
    assert calibrated_label(0.20) == "unlikely"
    assert calibrated_label(0.40) == "uncertain"
    assert calibrated_label(0.60) == "likely"
    assert calibrated_label(0.80) == "very likely"


def test_update_is_pure_and_suppresses_numeric_precision_under_threshold():
    base = scenario()

    result = update_scenario_with_evidence(
        base,
        [observation("E1", "supports", source_record_id="src-1")],
    )

    assert result.status == "updated"
    assert result.scenario is not base
    assert base.posterior_alpha == 2.0
    assert base.evidence_refs == []
    assert result.scenario.posterior_alpha == 3.0
    assert result.scenario.posterior_beta == 2.0
    assert result.scenario.evidence_count_n == 1
    assert result.scenario.display_mode == ScenarioDisplayMode.VERBAL_ONLY


def test_source_of_record_dedup_counts_duplicate_source_once():
    base = scenario()

    result = update_scenario_with_evidence(
        base,
        [
            observation("E1", "supports", source_record_id="same-row"),
            observation("E2", "supports", source_record_id="same-row"),
        ],
    )

    assert result.status == "updated"
    assert result.scenario.posterior_alpha == 3.0
    assert result.scenario.posterior_beta == 2.0
    assert result.scenario.evidence_count_n == 1
    assert result.scenario.evidence_refs == ["E1"]


def test_neutral_and_unknown_are_retained_as_refs_but_not_counted():
    base = scenario()

    result = update_scenario_with_evidence(
        base,
        [
            observation("E-neutral", "neutral", source_ref="fixture://neutral"),
            observation("E-unknown", "unknown", source_ref="fixture://unknown"),
        ],
    )

    assert result.status == "updated"
    assert result.scenario.posterior_alpha == base.posterior_alpha
    assert result.scenario.posterior_beta == base.posterior_beta
    assert result.scenario.evidence_count_n == 0
    assert result.scenario.evidence_refs == ["E-neutral", "E-unknown"]
    assert result.scenario.update_method == ScenarioUpdateMethod.NONE


def test_contradictory_same_source_evidence_returns_typed_result_without_update():
    base = scenario()

    result = update_scenario_with_evidence(
        base,
        [
            observation("E1", "supports", source_record_id="same-row"),
            observation("E2", "refutes", source_record_id="same-row"),
        ],
    )

    assert result.status == "contradiction"
    assert "support/refute conflict" in result.contradiction_reason
    assert result.scenario.model_dump(mode="json") == base.model_dump(mode="json")
    assert set(result.contradictory_evidence_refs) == {"E1", "E2"}


def test_mixed_stance_returns_typed_contradiction_without_update():
    base = scenario()

    result = update_scenario_with_evidence(
        base,
        [observation("E-mixed", "mixed", source_record_id="same-row")],
    )

    assert result.status == "contradiction"
    assert "mixed evidence stance" in result.contradiction_reason
    assert result.scenario.posterior_alpha == base.posterior_alpha
    assert result.scenario.posterior_beta == base.posterior_beta
