from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SQL = ROOT / "mas/sql/v60_research_evidence_automation_roi_execution.sql"
SCHEMA_TEST = (
    ROOT / "mas/tests/test_research_evidence_automation_roi_execution_schema.py"
)
ALLOWED = {
    "mas/research_evidence/__init__.py",
    "mas/research_evidence/automation_roi_execution_models.py",
    "mas/research_evidence/automation_roi_execution_policy.py",
    "mas/research_evidence/automation_roi_execution_repository.py",
    "mas/research_evidence/automation_roi_execution_service.py",
    "mas/sql/v60_research_evidence_automation_roi_execution.sql",
    "mas/tests/evidence_snapshot_pg.py",
    "mas/tests/test_research_evidence_automation_roi_execution_models.py",
    "mas/tests/test_research_evidence_automation_roi_execution_policy.py",
    "mas/tests/test_research_evidence_automation_roi_execution_repository.py",
    "mas/tests/test_research_evidence_automation_roi_execution_service.py",
    "mas/tests/test_research_evidence_automation_roi_execution_schema.py",
    "mas/tests/test_research_evidence_automation_roi_execution_static.py",
}


def test_migration_is_v60_and_does_not_name_legacy_result_tables():
    text = SQL.read_text(encoding="utf-8")
    assert SQL.name == "v60_research_evidence_automation_roi_execution.sql"
    assert "automation_roi_calculation_result" in text
    assert "calculation_result " not in text.replace(
        "automation_roi_calculation_result", ""
    )
    assert "calculation_result_input" not in text


def test_controlled_function_has_fixed_search_path_and_no_policy_reevaluation():
    text = SQL.read_text(encoding="utf-8")
    assert text.count(
        "SET search_path = pg_catalog, research_evidence_automation_roi, pg_temp"
    ) == 2
    function = text.split("research_evidence_execute_automation_roi(", 1)[1]
    assert "policy_evaluation_status <> 'satisfies'" in function
    assert "freshness_status" not in function
    assert "review_status" not in function
    assert "consumer_disposition" not in function


def test_reapply_validates_exact_mutation_trigger_function_oid():
    text = SQL.read_text(encoding="utf-8")
    schema_test = SCHEMA_TEST.read_text(encoding="utf-8")
    assert "v_mutation_guard_oid oid;" in text
    assert "trigger_info.tgfoid = v_mutation_guard_oid" in text
    assert "trigger_info.tgattr = ''::int2vector" in text
    assert (
        "test_reapply_rejects_same_shape_wrong_trigger_function_drift"
        in schema_test
    )
    assert "research_evidence_prepare_automation_roi_result()" in schema_test
    assert "trigger_info.tgfoid, wrong_function.oid" in schema_test


@pytest.mark.parametrize(
    "excluded",
    [
        "api/",
        "dashboard",
        "report",
        "export",
        "scenario",
        "workflow_v4",
        "prompt",
        "llm",
        "agent_blueprint",
    ],
)
def test_admitted_paths_do_not_introduce_excluded_integrations(excluded):
    implementation = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in (
            ROOT / "mas/research_evidence/automation_roi_execution_models.py",
            ROOT / "mas/research_evidence/automation_roi_execution_policy.py",
            ROOT / "mas/research_evidence/automation_roi_execution_repository.py",
            ROOT / "mas/research_evidence/automation_roi_execution_service.py",
        )
    )
    assert excluded not in implementation


def test_git_change_inventory_is_admitted():
    import subprocess

    tracked = subprocess.run(
        ["git", "diff", "--name-only", "--", *sorted(ALLOWED)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    untracked = subprocess.run(
        [
            "git",
            "ls-files",
            "--others",
            "--exclude-standard",
            "--",
            *sorted(ALLOWED),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert set(tracked + untracked) <= ALLOWED
