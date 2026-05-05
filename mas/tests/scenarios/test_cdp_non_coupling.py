import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cdp.citation_resolvability import build_defense_pass_result  # noqa: E402
from scenarios.models import (  # noqa: E402
    BayesianScenario,
    ScenarioDisplayMode,
    ScenarioEvidenceObservation,
    ScenarioFalsifier,
    ScenarioPriorSource,
    ScenarioUpdateMethod,
)
from scenarios.update import update_scenario_with_evidence  # noqa: E402
from state import Hypothesis, KnowledgeItem, KnowledgeLayerState, ProjectState  # noqa: E402


def test_scenarios_package_does_not_import_cdp():
    scenario_files = Path(ROOT, "scenarios").glob("*.py")

    for path in scenario_files:
        source = path.read_text(encoding="utf-8")
        assert "import cdp" not in source
        assert "from cdp" not in source


def _cdp_state(report: str) -> ProjectState:
    state = ProjectState(project_id="cdp-non-coupling", project_name="CDP", brief="Citation status.")
    state.knowledge_layer = KnowledgeLayerState(
        items=[
            KnowledgeItem(
                item_id="ev-x",
                source_id="src",
                source_ref="fixture://source#chunk=1",
                structured_payload={"chunk_index": 1},
            )
        ]
    )
    state.hypotheses = [Hypothesis(id="H1", text="Hypothesis one", alpha=3, beta=2, evidence_ids=["ev-x"])]
    state.report = report
    return state


def _scenario() -> BayesianScenario:
    return BayesianScenario(
        scenario_id="S-cdp",
        decision_ref="D1",
        hypothesis_ref="H1",
        prior_alpha=3.0,
        prior_beta=2.0,
        prior_source=ScenarioPriorSource.HYPOTHESIS,
        evidence_refs=["ev-x"],
        update_method=ScenarioUpdateMethod.NONE,
        posterior_alpha=3.0,
        posterior_beta=2.0,
        posterior_mean=0.6,
        posterior_ci_low_90=0.25,
        posterior_ci_high_90=0.9,
        calibrated_label="likely",
        evidence_count_n=1,
        display_mode=ScenarioDisplayMode.VERBAL_ONLY,
        assumptions=[],
        falsifier=ScenarioFalsifier(
            observable="Observable metric",
            threshold=">= target",
            time_window="30d",
        ),
        uncertainty_note="CDP status is intentionally not an input.",
    )


def _scenario_output_for_comparison(scenario: BayesianScenario) -> dict:
    return {
        "posterior_alpha": scenario.posterior_alpha,
        "posterior_beta": scenario.posterior_beta,
        "posterior_mean": scenario.posterior_mean,
        "posterior_ci_low_90": scenario.posterior_ci_low_90,
        "posterior_ci_high_90": scenario.posterior_ci_high_90,
        "calibrated_label": scenario.calibrated_label,
        "update_method": scenario.update_method,
        "falsifier": scenario.falsifier.model_dump(mode="json"),
    }


def test_cdp_resolution_status_does_not_change_scenario_output():
    exact_state = _cdp_state("Claim [Evidence: ev-x | chunk=1].")
    mismatch_state = _cdp_state("Claim [Evidence: ev-x | chunk=9].")
    exact_result = build_defense_pass_result(exact_state)
    mismatch_result = build_defense_pass_result(mismatch_state)
    exact_statuses = {item.status for item in exact_result.resolutions}
    mismatch_statuses = {item.status for item in mismatch_result.resolutions}
    assert "resolved_exact" in exact_statuses
    assert "locator_mismatch" in mismatch_statuses

    observation = ScenarioEvidenceObservation(
        evidence_id="ev-x",
        stance="supports",
        source_record_id="source-row-1",
        source_ref="fixture://source#chunk=1",
        provenance_id=None,
        locator="chunk=1",
        captured_at=None,
    )

    exact_scenario = update_scenario_with_evidence(_scenario(), [observation]).scenario
    mismatch_scenario = update_scenario_with_evidence(_scenario(), [observation]).scenario

    assert _scenario_output_for_comparison(exact_scenario) == _scenario_output_for_comparison(mismatch_scenario)
