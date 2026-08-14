"""Static proof of the nightly workflow's single-observation safety.

These tests parse the **real** `.github/workflows/evals-nightly-batch.yml`.
Nothing here dispatches a workflow, reads a secret or makes a provider call.

Two things are proved:

1.  *Single-observation safety.*  The nightly spends provider money on every
    execution and has two mutually ignorant triggers (a 06:00 schedule and
    manual dispatch).  The workflow must declare one concurrency boundary that
    both share, and it must never cancel work that has already been paid for.

2.  *The frozen surface.*  Schedule, dispatch availability, the 90-minute
    timeout, and the Anthropic-only provider posture are all explicitly
    unchanged by this remediation, and the issue-creation side effect is gone
    without any GitHub write replacing it or any permission widening.

The concurrency assertions live in a helper that is applied to the real parse
*and* to deliberately mutated copies, so weakening the policy in the YAML — by
deleting it, by flipping `cancel-in-progress`, or by scoping the group per-ref —
fails a test rather than quietly widening what can run twice.
"""

from __future__ import annotations

import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "evals-nightly-batch.yml"

NIGHTLY_JOB = "nightly-batch"
NIGHTLY_SCHEDULE = "0 6 * * *"
NIGHTLY_TIMEOUT_MINUTES = 90

# The concurrency group must be a constant: any `github.*` interpolation gives a
# differently-triggered run its own group and lets two nightlies overlap.
_EXPRESSION_RE = re.compile(r"\$\{\{")


@lru_cache(maxsize=1)
def _parsed_workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text())


def workflow() -> dict:
    return deepcopy(_parsed_workflow())


def triggers(parsed: dict) -> dict:
    # PyYAML reads the bare `on:` key as the boolean True (YAML 1.1).
    return parsed.get("on") or parsed[True]


def nightly_job(parsed: dict) -> dict:
    return parsed["jobs"][NIGHTLY_JOB]


def workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def workflow_directives() -> str:
    """The workflow with whole-line comments removed.

    The comments explain *why* a side effect and a permission are absent, and
    naturally name them. A text scan looking for the side effect itself must
    read what the workflow instructs, not what it explains.
    """

    return "\n".join(
        line for line in workflow_text().splitlines() if not line.lstrip().startswith("#")
    )


# ═══════════════ the concurrency contract, as a reusable assertion ═══════════════


def assert_single_observation_concurrency(parsed: dict) -> None:
    """The nightly may have at most one provider-bearing execution in flight.

    Applied to the real workflow and to mutated copies. Each clause below maps
    to one required property, so a mutation that breaks any single property
    fails here with the reason attached.
    """

    concurrency = parsed.get("concurrency")
    assert concurrency is not None, "nightly declares no concurrency policy"
    assert isinstance(concurrency, dict), "nightly concurrency must carry an explicit group"

    group = concurrency.get("group")
    assert isinstance(group, str) and group.strip(), "nightly concurrency group is empty"

    # A shared boundary: schedule and workflow_dispatch resolve to the same
    # group only if the group interpolates nothing about the run.
    assert not _EXPRESSION_RE.search(group), (
        "nightly concurrency group interpolates run context, so differently "
        f"triggered nightlies would not share it: {group!r}"
    )

    cancel = concurrency.get("cancel-in-progress", False)
    assert cancel is False, (
        "nightly must not cancel an in-progress paid observation; "
        f"cancel-in-progress is {cancel!r}"
    )

    # Workflow-level, not job-level: a job-scoped group would leave the rest of
    # the workflow free to run twice.
    for job_name, job in parsed.get("jobs", {}).items():
        assert "concurrency" not in job, f"job {job_name} declares its own concurrency boundary"


# ── the real workflow ──


def test_real_workflow_declares_single_observation_concurrency():
    assert_single_observation_concurrency(workflow())


def test_schedule_and_manual_dispatch_share_one_concurrency_boundary():
    parsed = workflow()
    declared = triggers(parsed)

    assert "schedule" in declared
    assert "workflow_dispatch" in declared
    # One workflow-level group, no per-trigger override anywhere: whichever
    # trigger fires, GitHub resolves the same group name.
    assert parsed["concurrency"]["group"] == "evals-nightly-batch"
    assert not _EXPRESSION_RE.search(parsed["concurrency"]["group"])
    assert all("concurrency" not in job for job in parsed["jobs"].values())


def test_in_progress_paid_work_is_never_cancelled():
    assert workflow()["concurrency"]["cancel-in-progress"] is False


def test_nightly_concurrency_does_not_couple_to_the_paid_pr_gate():
    """The release gate's group must stay a different boundary."""

    gate = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "evals.yml").read_text())

    assert gate["concurrency"]["group"] != workflow()["concurrency"]["group"]
    # The gate's group is per-PR/per-ref by design; the nightly's is global.
    assert _EXPRESSION_RE.search(gate["concurrency"]["group"])


def test_nightly_concurrency_does_not_block_unrelated_ci():
    """The group is scoped to this workflow, not to CI at large."""

    group = workflow()["concurrency"]["group"]
    for other in ("evals.yml", "tests.yml"):
        parsed = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / other).read_text())
        assert parsed.get("concurrency", {}).get("group") != group


# ── mutation tests: weakening the YAML must fail ──


def test_mutation_removing_concurrency_is_caught():
    mutated = workflow()
    del mutated["concurrency"]

    with pytest.raises(AssertionError, match="no concurrency policy"):
        assert_single_observation_concurrency(mutated)


def test_mutation_cancel_in_progress_true_is_caught():
    mutated = workflow()
    mutated["concurrency"]["cancel-in-progress"] = True

    with pytest.raises(AssertionError, match="in-progress paid observation"):
        assert_single_observation_concurrency(mutated)


def test_mutation_ref_scoped_group_is_caught():
    """A per-ref group would let a dispatch overlap the scheduled nightly."""

    mutated = workflow()
    mutated["concurrency"]["group"] = "${{ github.workflow }}-${{ github.ref }}"

    with pytest.raises(AssertionError, match="would not share it"):
        assert_single_observation_concurrency(mutated)


def test_mutation_job_scoped_concurrency_is_caught():
    mutated = workflow()
    mutated["jobs"][NIGHTLY_JOB]["concurrency"] = {"group": "x", "cancel-in-progress": False}

    with pytest.raises(AssertionError, match="own concurrency boundary"):
        assert_single_observation_concurrency(mutated)


def test_mutation_empty_group_is_caught():
    mutated = workflow()
    mutated["concurrency"]["group"] = "   "

    with pytest.raises(AssertionError, match="group is empty"):
        assert_single_observation_concurrency(mutated)


# ═══════════════ the frozen surface ═══════════════


def test_daily_schedule_is_unchanged():
    schedule = triggers(workflow())["schedule"]

    assert schedule == [{"cron": NIGHTLY_SCHEDULE}]


def test_workflow_dispatch_remains_available():
    assert "workflow_dispatch" in triggers(workflow())


def test_timeout_remains_ninety_minutes():
    """Deliberately unchanged: nightly completion reliability is deferred."""

    assert nightly_job(workflow())["timeout-minutes"] == NIGHTLY_TIMEOUT_MINUTES


def test_locked_dependencies_are_still_installed():
    steps = nightly_job(workflow())["steps"]
    install = " ".join(str(step.get("run", "")) for step in steps)

    assert "validate_requirements_lock.py" in install
    assert "requirements.lock.txt" in install


def test_the_evaluation_still_runs_the_full_batch_harness():
    steps = nightly_job(workflow())["steps"]
    commands = [str(step.get("run", "")) for step in steps]

    assert any("evals.run_evals_batch" in command for command in commands)
    # No `--cases` narrowing and no `--mock`: the nightly measures the full
    # golden universe against the real pipeline.
    assert not any("--cases" in command or "--mock" in command for command in commands)


def test_report_artifact_is_still_uploaded_under_failure():
    steps = nightly_job(workflow())["steps"]
    uploads = [step for step in steps if str(step.get("uses", "")).startswith("actions/upload-artifact")]

    assert len(uploads) == 1
    assert uploads[0]["if"] in ("always()", "${{ always() }}")
    assert uploads[0]["with"]["path"] == "mas/evals/out/"


# ═══════════════ issue side effect removed, nothing replacing it ═══════════════


def test_issue_creation_step_is_absent():
    for step in nightly_job(workflow())["steps"]:
        assert "Open issue on regression" != step.get("name")
        assert not str(step.get("uses", "")).startswith("actions/github-script")

    text = workflow_directives()
    assert "issues.create" not in text
    assert "github-script" not in text
    assert "eval-regression" not in text


def test_no_github_write_side_effect_replaces_it():
    """Not an issue, comment, label, review, release, dispatch or webhook."""

    text = workflow_directives()
    forbidden = (
        "issues.create",
        "issues.createComment",
        "createComment",
        "addLabels",
        "setLabels",
        "createRelease",
        "createDispatchEvent",
        "repos.create",
        "pulls.create",
        "gh issue",
        "gh pr comment",
        "peter-evans/",
        "slack",
        "webhook",
        "curl -X POST",
    )
    lowered = text.lower()
    for marker in forbidden:
        assert marker.lower() not in lowered, marker


def test_no_write_permission_is_declared_anywhere():
    parsed = workflow()
    text = workflow_directives()

    # The remediation neither adds nor widens permissions; the workflow simply
    # no longer needs any write scope.
    assert "permissions" not in parsed
    assert all("permissions" not in job for job in parsed["jobs"].values())
    assert "issues: write" not in text
    assert "issues:" not in text


def test_evaluation_failure_still_fails_the_job():
    """Removing the issue step must not suppress the harness's non-zero exit."""

    steps = nightly_job(workflow())["steps"]
    eval_step = next(step for step in steps if "evals.run_evals_batch" in str(step.get("run", "")))

    assert "continue-on-error" not in eval_step
    assert "continue-on-error" not in nightly_job(workflow())
    assert "if" not in eval_step
    assert "|| true" not in str(eval_step["run"])


# ═══════════════ provider posture ═══════════════


def test_anthropic_is_the_only_provider_credential_in_this_workflow():
    text = workflow_directives()
    secrets = set(re.findall(r"secrets\.([A-Z0-9_]+)", text))

    assert secrets == {"ANTHROPIC_API_KEY"}
    assert "OPENAI_API_KEY" not in text


def test_no_second_provider_secret_is_wired_into_any_step():
    for step in nightly_job(workflow())["steps"]:
        env = step.get("env") or {}
        provider_keys = {key for key in env if key.endswith("_API_KEY")}
        assert provider_keys <= {"ANTHROPIC_API_KEY"}, provider_keys


def test_only_one_job_holds_the_provider_secret():
    parsed = workflow()

    assert list(parsed["jobs"]) == [NIGHTLY_JOB]
    holders = [
        step.get("name") or step.get("uses")
        for step in nightly_job(parsed)["steps"]
        if "ANTHROPIC_API_KEY" in (step.get("env") or {})
    ]
    assert holders == ["Run batch evals"]
