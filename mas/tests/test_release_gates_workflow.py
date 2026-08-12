"""Deterministic proof of the V7 Gate A / Gate B release harness.

Every assertion here is static or fake-driven. No provider call is made, no
secret is read, and no workflow is dispatched.

The authorization matrix parses the real `if:` expressions out of `evals.yml`
and evaluates them against synthetic event contexts, so loosening a guard in the
workflow breaks these tests rather than quietly widening what can spend money.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MAS_ROOT = REPO_ROOT / "mas"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "evals.yml"

sys.path.insert(0, str(MAS_ROOT))
sys.path.insert(0, str(MAS_ROOT / "tests"))

from evals import release_gates  # noqa: E402
from gha_expressions import (  # noqa: E402
    dispatch_context,
    evaluate,
    pull_request_context,
    render,
    with_needs,
)

GATE_A = release_gates.GATE_A
GATE_B = release_gates.GATE_B
LABEL_A = release_gates.GATE_LABELS[GATE_A]
LABEL_B = release_gates.GATE_LABELS[GATE_B]
PAID = release_gates.PAID_EVAL_LABEL

GATE_A_PREFLIGHT = "gate-a-anthropic-preflight"
GATE_A_SHARD = "gate-a-real-eval-shard"
GATE_A_AGGREGATE = "gate-a-aggregate"
GATE_B_PREFLIGHT = "gate-b-openai-preflight"
GATE_B_SHARD = "gate-b-real-eval-shard"
GATE_B_AGGREGATE = "gate-b-aggregate"

LIVE_JOBS = (
    GATE_A_PREFLIGHT,
    GATE_A_SHARD,
    GATE_A_AGGREGATE,
    GATE_B_PREFLIGHT,
    GATE_B_SHARD,
    GATE_B_AGGREGATE,
)
GATE_A_JOBS = (GATE_A_PREFLIGHT, GATE_A_SHARD, GATE_A_AGGREGATE)
GATE_B_JOBS = (GATE_B_PREFLIGHT, GATE_B_SHARD, GATE_B_AGGREGATE)


@lru_cache(maxsize=1)
def _parsed_workflow() -> dict:
    """Parse `evals.yml` exactly once per session.

    The helpers below are called hundreds of times across this module; re-running
    the YAML parser on each call is pure waste and widens the window for any
    interaction with the async telemetry suites that share this process. Callers
    get a deep copy so a mutating test cannot corrupt the shared parse.
    """

    return yaml.safe_load(WORKFLOW_PATH.read_text())


def workflow() -> dict:
    return deepcopy(_parsed_workflow())


def jobs() -> dict:
    return workflow()["jobs"]


def job_if(name: str) -> str:
    return jobs()[name].get("if", "")


def _all_env(node: object) -> list[dict]:
    """Every `env:` mapping anywhere in a job, job-level and step-level."""

    found: list[dict] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "env" and isinstance(value, dict):
                found.append(value)
            else:
                found.extend(_all_env(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_all_env(item))
    return found


def _job_text(name: str) -> str:
    # `width` is deliberately enormous: the default wraps long `run:` lines and
    # would let `--shard-count 6` disappear across a line break, turning a real
    # assertion into one that silently cannot fail.
    return yaml.safe_dump(jobs()[name], default_flow_style=False, width=10**9)


def _run_scripts(name: str) -> list[str]:
    """Every step's `run:` script, verbatim.

    `_job_text` round-trips through the YAML dumper, which re-escapes quotes --
    so a shell-quoting assertion made against it would be asserting about the
    dumper, not about what the runner executes.
    """

    return [
        step["run"]
        for step in jobs()[name].get("steps", [])
        if isinstance(step, dict) and isinstance(step.get("run"), str)
    ]


def _all_run_text(name: str) -> str:
    return "\n".join(_run_scripts(name))


# ════════════════════ §25 static authorization matrix ════════════════════
#
# A live provider job requires BOTH an authorization act and a gate selection.
# Neither alone is sufficient, and no default supplies either.

UNRELATED = "documentation"

AUTHORIZATION_MATRIX = [
    # id, context, jobs expected eligible
    #
    # ── the F-1 rows ──────────────────────────────────────────────────────
    #
    # Label *presence* is sticky; a label *event* is not. The workflow used to
    # test only presence, so adding any label at all to an already-labelled
    # Ready PR re-authorized a full paid eval. A2/A3 are that exact defect.
    (
        "A1 draft + every authorizing label",
        pull_request_context(draft=True, labels=[PAID, LABEL_A], label=PAID),
        (),
    ),
    (
        "A2 ready + paid + gate A, unrelated label added",
        pull_request_context(
            draft=False, labels=[PAID, LABEL_A, UNRELATED], label=UNRELATED
        ),
        (),
    ),
    (
        "A3 ready + paid + gate B, unrelated label added",
        pull_request_context(
            draft=False, labels=[PAID, LABEL_B, UNRELATED], label=UNRELATED
        ),
        (),
    ),
    (
        "A4 paid-eval added while gate A already present",
        pull_request_context(draft=False, labels=[PAID, LABEL_A], label=PAID),
        GATE_A_JOBS,
    ),
    (
        "A5 gate A added while paid-eval already present",
        pull_request_context(draft=False, labels=[PAID, LABEL_A], label=LABEL_A),
        GATE_A_JOBS,
    ),
    (
        "A6 gate B added while paid-eval already present",
        pull_request_context(draft=False, labels=[PAID, LABEL_B], label=LABEL_B),
        GATE_B_JOBS,
    ),
    (
        "A7 both gate labels present",
        pull_request_context(draft=False, labels=[PAID, LABEL_A, LABEL_B], label=PAID),
        (),
    ),
    (
        "A8 synchronize after a prior authorization",
        pull_request_context(draft=False, labels=[PAID, LABEL_A], action="synchronize"),
        (),
    ),
    (
        "A9 reopened",
        pull_request_context(draft=False, labels=[PAID, LABEL_A], action="reopened"),
        (),
    ),
    (
        "A10 unlabeled",
        pull_request_context(
            draft=False, labels=[PAID, LABEL_A], action="unlabeled", label=UNRELATED
        ),
        (),
    ),
    (
        "A11 converted_to_draft",
        pull_request_context(
            draft=True, labels=[PAID, LABEL_A], action="converted_to_draft"
        ),
        (),
    ),
    (
        "A12 ready_for_review in a correct Gate A state",
        pull_request_context(draft=False, labels=[PAID, LABEL_A], action="ready_for_review"),
        GATE_A_JOBS,
    ),
    (
        "A13 ready_for_review in a correct Gate B state",
        pull_request_context(draft=False, labels=[PAID, LABEL_B], action="ready_for_review"),
        GATE_B_JOBS,
    ),
    (
        "A14 gate A added while the competing gate is also present",
        pull_request_context(draft=False, labels=[PAID, LABEL_A, LABEL_B], label=LABEL_A),
        (),
    ),
    #
    # ── incomplete authorization states ───────────────────────────────────
    #
    ("A17 draft + no label", pull_request_context(draft=True, labels=[]), ()),
    ("A18 ready + no label", pull_request_context(draft=False, labels=[]), ()),
    (
        "A19 paid-eval alone, just added",
        pull_request_context(draft=False, labels=[PAID], label=PAID),
        (),
    ),
    (
        "A20 gate label alone, just added",
        pull_request_context(draft=False, labels=[LABEL_A], label=LABEL_A),
        (),
    ),
    (
        "A21 ready_for_review with no labels at all",
        pull_request_context(draft=False, labels=[], action="ready_for_review"),
        (),
    ),
    (
        "A22 opened with a full label set",
        pull_request_context(draft=False, labels=[PAID, LABEL_B], action="opened"),
        (),
    ),
    #
    # ── workflow_dispatch ─────────────────────────────────────────────────
    #
    (
        "A23 dispatch gate A + confirmed",
        dispatch_context(provider_gate=GATE_A, confirm=True),
        GATE_A_JOBS,
    ),
    (
        "A24 dispatch gate B + confirmed",
        dispatch_context(provider_gate=GATE_B, confirm=True),
        GATE_B_JOBS,
    ),
    ("A25 dispatch gate none", dispatch_context(provider_gate="none", confirm=True), ()),
    ("A26 dispatch gate absent", dispatch_context(provider_gate=None, confirm=True), ()),
    ("A27 dispatch invalid gate", dispatch_context(provider_gate="gate_c", confirm=True), ()),
    (
        "A28 dispatch gate A, confirmation omitted",
        dispatch_context(provider_gate=GATE_A, confirm=None),
        (),
    ),
    (
        "A29 dispatch gate A, confirmation false",
        dispatch_context(provider_gate=GATE_A, confirm=False),
        (),
    ),
    (
        "A30 dispatch gate A, confirmation as a string",
        dispatch_context(provider_gate=GATE_A, confirm="true"),
        (),
    ),
    (
        "A31 dispatch gate A, retired RUN-PAID sentinel",
        dispatch_context(provider_gate=GATE_A, confirm="RUN-PAID"),
        (),
    ),
]


@pytest.mark.parametrize(
    "label,context,expected",
    AUTHORIZATION_MATRIX,
    ids=[row[0] for row in AUTHORIZATION_MATRIX],
)
def test_authorization_matrix(label, context, expected):
    """Only the listed jobs may be eligible; every other live job must not be."""

    # Preflights already succeeded, so a job that is skipped here is skipped by
    # authorization alone rather than by an unmet dependency.
    full = with_needs(
        context,
        gate_a_anthropic_preflight="success",
        gate_b_openai_preflight="success",
    )
    eligible = tuple(job for job in LIVE_JOBS if evaluate(job_if(job), full))
    assert eligible == tuple(expected), f"{label}: eligible={eligible}"


def test_no_gate_can_be_eligible_alongside_the_other():
    """A4/A5 exclusivity restated as an invariant over the whole matrix."""

    for label, context, _ in AUTHORIZATION_MATRIX:
        full = with_needs(
            context,
            gate_a_anthropic_preflight="success",
            gate_b_openai_preflight="success",
        )
        a_live = any(evaluate(job_if(job), full) for job in GATE_A_JOBS)
        b_live = any(evaluate(job_if(job), full) for job in GATE_B_JOBS)
        assert not (a_live and b_live), f"{label}: both gates eligible"


def test_this_draft_unlabeled_pr_cannot_run_any_live_job():
    """§32 -- the state this very PR is in."""

    context = with_needs(
        pull_request_context(draft=True, labels=[]),
        gate_a_anthropic_preflight="success",
        gate_b_openai_preflight="success",
    )
    for job in LIVE_JOBS:
        assert not evaluate(job_if(job), context), job


def test_ordinary_synchronize_on_an_unlabeled_pr_authorizes_nothing():
    """§34.20 -- a push to an ordinary PR must not start paid work."""

    context = with_needs(
        pull_request_context(draft=False, labels=["documentation"], action="synchronize"),
        gate_a_anthropic_preflight="success",
        gate_b_openai_preflight="success",
    )
    for job in LIVE_JOBS:
        assert not evaluate(job_if(job), context), job


@pytest.mark.parametrize("gate_label", [LABEL_A, LABEL_B])
@pytest.mark.parametrize(
    "unrelated", ["documentation", "bug", "needs-review", PAID + "-x", ""]
)
def test_f1_an_unrelated_label_never_authorizes_paid_execution(gate_label, unrelated):
    """F-1, the demonstrated MAJOR finding.

    Pre-fix, the guards asked only `action == 'labeled'` and then checked label
    *presence*. On a Ready PR already carrying `paid-eval` and one gate label,
    adding ANY label re-satisfied every clause and a full paid eval started.
    """

    context = with_needs(
        pull_request_context(
            draft=False,
            labels=[PAID, gate_label, unrelated],
            action="labeled",
            label=unrelated,
        ),
        gate_a_anthropic_preflight="success",
        gate_b_openai_preflight="success",
    )
    for job in LIVE_JOBS:
        assert not evaluate(job_if(job), context), job


@pytest.mark.parametrize("gate_label", [LABEL_A, LABEL_B])
def test_f1_only_a_materially_completing_label_authorizes(gate_label):
    """The minimal accepted label set is exactly {paid-eval, this gate's label}.

    Nothing else completes the authorization state, so nothing else may start a
    provider job -- and each guard names only its OWN gate label, so the other
    gate's label cannot authorize it either.
    """

    accepted = set(release_gates.AUTHORIZING_LABELS[
        GATE_A if gate_label == LABEL_A else GATE_B
    ])
    assert accepted == {PAID, gate_label}

    expected = GATE_A_JOBS if gate_label == LABEL_A else GATE_B_JOBS
    for candidate in {PAID, LABEL_A, LABEL_B, "documentation"}:
        context = with_needs(
            pull_request_context(
                draft=False, labels=[PAID, gate_label], action="labeled", label=candidate
            ),
            gate_a_anthropic_preflight="success",
            gate_b_openai_preflight="success",
        )
        eligible = tuple(job for job in LIVE_JOBS if evaluate(job_if(job), context))
        if candidate in accepted:
            assert eligible == expected, f"label {candidate!r} should authorize"
        else:
            assert eligible == (), f"label {candidate!r} must not authorize"


@pytest.mark.parametrize(
    "action", ["opened", "synchronize", "reopened", "unlabeled", "converted_to_draft"]
)
@pytest.mark.parametrize("gate_label", [LABEL_A, LABEL_B])
def test_only_labeled_and_ready_for_review_can_ever_authorize(action, gate_label):
    """Every other trigger type in `on.pull_request.types` authorizes nothing."""

    context = with_needs(
        pull_request_context(
            draft=False, labels=[PAID, gate_label], action=action, label=PAID
        ),
        gate_a_anthropic_preflight="success",
        gate_b_openai_preflight="success",
    )
    for job in LIVE_JOBS:
        assert not evaluate(job_if(job), context), f"{action}: {job}"


def test_every_authorizing_guard_reads_the_event_label_name():
    """The structural form of the F-1 fix, not just its behavior.

    A guard that accepts `labeled` without consulting `github.event.label.name`
    is the defect, whatever else it checks.
    """

    for job in LIVE_JOBS:
        expression = job_if(job)
        assert "github.event.action == 'labeled'" in expression, job
        assert "github.event.label.name" in expression, job


@pytest.mark.parametrize("gate_label", [LABEL_A, LABEL_B])
def test_a_push_to_an_already_authorized_pr_does_not_respend(gate_label):
    """§34.20, the sharper form.

    Labels are sticky: once `paid-eval` and a gate label are on a Ready PR, an
    `if:` that accepts any `pull_request` event would bill a full eval on every
    subsequent push. Paid PR runs are therefore restricted to the events that
    *are* the authorization -- `labeled` and `ready_for_review` -- so spending
    again takes a deliberate act rather than a `git push`.
    """

    context = with_needs(
        pull_request_context(draft=False, labels=[PAID, gate_label], action="synchronize"),
        gate_a_anthropic_preflight="success",
        gate_b_openai_preflight="success",
    )
    for job in LIVE_JOBS:
        assert not evaluate(job_if(job), context), job


# ════════════════════ concurrency / immutable evidence ════════════════════
#
# Two properties, one expression pair:
#
#   A15  an event that authorizes nothing must not be able to destroy paid work
#        that is already running;
#   A16  evidence must stay bound to the commit it was measured on, so a newer
#        authorization gets its own run rather than replacing an older one's
#        results.


def _concurrency(context):
    """The rendered group and the resolved cancel policy for one event."""

    group = render(workflow()["concurrency"]["group"], context)
    cancel = evaluate(workflow()["concurrency"]["cancel-in-progress"], context)
    return group, cancel


AUTHORIZING_EVENTS = [
    ("labeled", lambda sha: pull_request_context(
        draft=False, labels=[PAID, LABEL_A], action="labeled", label=PAID, head_sha=sha)),
    ("ready_for_review", lambda sha: pull_request_context(
        draft=False, labels=[PAID, LABEL_A], action="ready_for_review", head_sha=sha)),
]
ROUTINE_EVENTS = [
    ("synchronize", lambda sha: pull_request_context(
        draft=False, labels=[PAID, LABEL_A], action="synchronize", head_sha=sha)),
    ("opened", lambda sha: pull_request_context(
        draft=False, labels=[], action="opened", head_sha=sha)),
    ("reopened", lambda sha: pull_request_context(
        draft=False, labels=[PAID, LABEL_A], action="reopened", head_sha=sha)),
    ("unlabeled", lambda sha: pull_request_context(
        draft=False, labels=[PAID], action="unlabeled", label=LABEL_A, head_sha=sha)),
    ("converted_to_draft", lambda sha: pull_request_context(
        draft=True, labels=[PAID, LABEL_A], action="converted_to_draft", head_sha=sha)),
]


@pytest.mark.parametrize("name,build", AUTHORIZING_EVENTS, ids=[n for n, _ in AUTHORIZING_EVENTS])
def test_a15_an_event_that_could_spend_never_cancels_work_in_progress(name, build):
    group, cancel = _concurrency(build("AAA"))
    assert cancel is False, f"{name} may cancel a running paid gate"
    assert group.endswith("paid-AAA"), group


@pytest.mark.parametrize("name,build", ROUTINE_EVENTS, ids=[n for n, _ in ROUTINE_EVENTS])
def test_a15_routine_events_share_one_cancellable_bucket(name, build):
    """Only the free mock smoke job runs under these, so superseding is savings."""

    group, cancel = _concurrency(build("AAA"))
    assert cancel is True, name
    assert group.endswith("-routine"), group


def test_a15_an_unrelated_label_cannot_replace_a_running_paid_gate():
    """The F-1 concurrency consequence, stated as its own property.

    An unrelated label lands in the same bucket as the paid run it might have
    displaced -- but `cancel-in-progress` is false there, so it queues behind
    that run instead of terminating it. It also authorizes no job of its own.
    """

    running, _ = _concurrency(
        pull_request_context(draft=False, labels=[PAID, LABEL_A],
                             action="labeled", label=PAID, head_sha="AAA")
    )
    incidental_context = pull_request_context(
        draft=False, labels=[PAID, LABEL_A, "documentation"],
        action="labeled", label="documentation", head_sha="AAA",
    )
    incidental, cancel = _concurrency(incidental_context)

    assert cancel is False
    assert incidental == running

    full = with_needs(incidental_context, gate_a_anthropic_preflight="success",
                      gate_b_openai_preflight="success")
    for job in LIVE_JOBS:
        assert not evaluate(job_if(job), full), job


def test_a15_a_push_cannot_reach_the_bucket_a_paid_gate_runs_in():
    running, _ = _concurrency(
        pull_request_context(draft=False, labels=[PAID, LABEL_A],
                             action="labeled", label=PAID, head_sha="AAA")
    )
    pushed, cancel = _concurrency(
        pull_request_context(draft=False, labels=[PAID, LABEL_A],
                             action="synchronize", head_sha="BBB")
    )
    assert cancel is True
    assert pushed != running


def test_a16_evidence_for_one_sha_is_never_superseded_by_another():
    """A new authorization on a new commit gets its own group and its own run.

    Nothing cancels the older run, so its artifacts keep describing the commit
    they were actually measured on rather than being silently replaced by a
    newer commit's results under the same identity.
    """

    old, _ = _concurrency(
        pull_request_context(draft=False, labels=[PAID, LABEL_A],
                             action="labeled", label=PAID, head_sha="AAA")
    )
    new, cancel = _concurrency(
        pull_request_context(draft=False, labels=[PAID, LABEL_A],
                             action="labeled", label=PAID, head_sha="BBB")
    )
    assert old != new
    assert "AAA" in old and "BBB" in new
    assert cancel is False


def test_a16_the_paid_group_is_keyed_to_the_head_commit():
    expression = workflow()["concurrency"]["group"]
    assert "github.event.pull_request.head.sha" in expression
    assert "format('paid-{0}'" in expression


def test_dispatch_runs_are_also_never_cancelled():
    _, cancel = _concurrency(dispatch_context(provider_gate=GATE_A, confirm=True))
    assert cancel is False


def test_smoke_has_no_if_guard_and_is_always_free():
    smoke = jobs()["smoke"]
    assert "if" not in smoke
    text = _job_text("smoke")
    assert "secrets." not in text
    assert "--mock" in text


# ════════════════════ threshold input safety ════════════════════
#
# `threshold` is the one live input an operator types by hand. It used to be
# interpolated by `${{ }}` straight into the `run:` script of steps holding a
# provider secret, where `0.75; env` is not a bad number -- it is a command.

HOSTILE_THRESHOLDS = [
    "0.75; env",
    "0.75; echo $ANTHROPIC_API_KEY",
    "0.75 && echo pwned",
    "0.75 | tee /tmp/pwned",
    "$(env)",
    "`env`",
    "0.75$(echo INJECTED)",
    "'; env; '",
    '" ; env ; "',
    "0.75\nenv",
    "0.75 > /tmp/pwned",
    "0.75 & env",
    "--threshold 0.0",
    "nan",
    "inf",
    "-inf",
    "1_0",
    "0x1",
    "2.0",
    "-0.5",
    "sk-ant-SENTINEL",
]

# Blank is the ABSENCE of an override, not a hostile value: it legitimately
# resolves to the release default.
BLANK_THRESHOLDS = ["", "   ", "\n", "\t"]


@pytest.mark.parametrize("job", [GATE_A_SHARD, GATE_B_SHARD, GATE_A_AGGREGATE, GATE_B_AGGREGATE])
def test_threshold_is_never_interpolated_into_shell_source(job):
    """The structural property: no `${{ }}` anywhere in an executable script."""

    for script in _run_scripts(job):
        assert "${{" not in script, f"{job}: expression interpolated into shell source"
    assert '--threshold "$EVAL_THRESHOLD"' in _all_run_text(job)


@pytest.mark.parametrize("job", LIVE_JOBS)
def test_no_live_job_interpolates_any_expression_into_a_run_script(job):
    for script in _run_scripts(job):
        assert "${{" not in script, job


@pytest.mark.parametrize("job", [GATE_A_SHARD, GATE_B_SHARD, GATE_A_AGGREGATE, GATE_B_AGGREGATE])
def test_threshold_arrives_through_the_environment(job):
    envs = _all_env(jobs()[job])
    assert any("EVAL_THRESHOLD" in env for env in envs), job


@pytest.mark.parametrize("preflight", [GATE_A_PREFLIGHT, GATE_B_PREFLIGHT])
def test_threshold_is_validated_before_any_credential_is_used(preflight):
    """A malformed threshold must fail as configuration, not as a provider error."""

    steps = jobs()[preflight]["steps"]
    validate_at = next(
        i for i, s in enumerate(steps) if s.get("name") == "Validate threshold input"
    )
    secret_at = [
        i
        for i, s in enumerate(steps)
        if "secrets." in yaml.safe_dump(s.get("env") or {}, width=10**9)
    ]
    assert secret_at, preflight
    assert validate_at < min(secret_at), "threshold validated after a secret is in scope"
    assert "${{" not in steps[validate_at]["run"]


@pytest.mark.parametrize("hostile", HOSTILE_THRESHOLDS)
def test_a_hostile_threshold_is_refused_by_the_real_validator(hostile):
    """The value is read the way the runner supplies it: an env var, not code."""

    with pytest.raises(release_gates.ThresholdError):
        release_gates.threshold_from_env({release_gates.THRESHOLD_ENV_VAR: hostile})


@pytest.mark.parametrize("blank", BLANK_THRESHOLDS)
def test_an_absent_threshold_resolves_to_the_release_default(blank):
    assert release_gates.threshold_from_env(
        {release_gates.THRESHOLD_ENV_VAR: blank}
    ) == 0.75


@pytest.mark.parametrize("hostile", HOSTILE_THRESHOLDS)
def test_a_hostile_threshold_reaches_the_subprocess_as_inert_data(hostile, tmp_path):
    """End-to-end through the actual `--validate-threshold` entry point.

    The value is passed exactly as the workflow passes it. If any of these were
    still shell source, the sentinel would execute; the assertion is that the
    process exits non-zero having executed nothing.
    """



    marker = tmp_path / "pwned"
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(MAS_ROOT),
        release_gates.THRESHOLD_ENV_VAR: hostile.replace("/tmp/pwned", str(marker)),
        "ANTHROPIC_API_KEY": "",
        "OPENAI_API_KEY": "",
        "SENTINEL_SECRET": "sk-ant-DO-NOT-LEAK",
    }
    result = subprocess.run(
        [sys.executable, "-m", "evals.release_gates", "--validate-threshold"],
        cwd=MAS_ROOT, env=env, capture_output=True, text=True, timeout=120,
    )

    assert result.returncode != 0, f"{hostile!r} was accepted"
    assert not marker.exists(), f"{hostile!r} executed a redirect"
    combined = result.stdout + result.stderr
    assert "sk-ant-DO-NOT-LEAK" not in combined
    assert "INJECTED" not in combined
    # The rejected value itself is never echoed into the log of a job that
    # holds a provider secret.
    assert hostile not in combined


def test_the_release_threshold_default_is_unchanged():
    assert release_gates.DEFAULT_THRESHOLD == 0.75
    assert release_gates.threshold_from_env({}) == 0.75
    assert release_gates.threshold_from_env({release_gates.THRESHOLD_ENV_VAR: ""}) == 0.75
    assert release_gates.normalize_threshold("0.75") == 0.75


@pytest.mark.parametrize("good", ["0", "1", "0.0", "1.0", "0.75", ".5", "0.", "+0.75"])
def test_legitimate_thresholds_are_still_accepted(good):
    value = release_gates.normalize_threshold(good)
    assert 0.0 <= value <= 1.0


def test_validate_threshold_entry_point_accepts_the_default(tmp_path):


    result = subprocess.run(
        [sys.executable, "-m", "evals.release_gates", "--validate-threshold"],
        cwd=MAS_ROOT,
        env={
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(MAS_ROOT),
            release_gates.THRESHOLD_ENV_VAR: "0.75",
        },
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# ════════════════════ §26 credential matrix ════════════════════


def _secret_refs(job_name: str) -> set[str]:
    text = _job_text(job_name)
    return {
        secret
        for secret in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")
        if f"secrets.{secret}" in text
    }


def _blanked(job_name: str) -> set[str]:
    blanked = set()
    for env in _all_env(jobs()[job_name]):
        for key, value in env.items():
            if key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY") and value == "":
                blanked.add(key)
    return blanked


@pytest.mark.parametrize("job", GATE_A_JOBS[:2], ids=["preflight", "shard"])
def test_c1_c4_gate_a_receives_anthropic_only(job):
    assert _secret_refs(job) == {"ANTHROPIC_API_KEY"}
    assert "OPENAI_API_KEY" in _blanked(job)
    assert "secrets.OPENAI_API_KEY" not in _job_text(job)


@pytest.mark.parametrize("job", GATE_B_JOBS[:2], ids=["preflight", "shard"])
def test_c2_c5_gate_b_receives_openai_only(job):
    assert _secret_refs(job) == {"OPENAI_API_KEY"}
    assert "ANTHROPIC_API_KEY" in _blanked(job)
    assert "secrets.ANTHROPIC_API_KEY" not in _job_text(job)


def test_c3_smoke_receives_no_provider_secret():
    assert _secret_refs("smoke") == set()


@pytest.mark.parametrize("job", [GATE_A_AGGREGATE, GATE_B_AGGREGATE])
def test_c6_aggregate_needs_no_provider_credential(job):
    assert _secret_refs(job) == set()


def test_no_job_ever_receives_both_provider_secrets():
    for name in jobs():
        assert len(_secret_refs(name)) <= 1, name


def test_every_job_holding_a_secret_blanks_the_other_provider():
    for name in jobs():
        held = _secret_refs(name)
        if not held:
            continue
        other = ({"ANTHROPIC_API_KEY", "OPENAI_API_KEY"} - held).pop()
        assert other in _blanked(name), f"{name} does not blank {other}"


# ════════════════════ §27 dependency matrix ════════════════════


def _needs(job_name: str) -> list[str]:
    value = jobs()[job_name].get("needs", [])
    return [value] if isinstance(value, str) else list(value)


def test_gate_shards_require_their_own_preflight():
    assert GATE_A_PREFLIGHT in _needs(GATE_A_SHARD)
    assert GATE_B_PREFLIGHT in _needs(GATE_B_SHARD)
    assert f"needs.{GATE_A_PREFLIGHT}.result == 'success'" in job_if(GATE_A_SHARD)
    assert f"needs.{GATE_B_PREFLIGHT}.result == 'success'" in job_if(GATE_B_SHARD)


def test_aggregates_require_their_own_gate_jobs():
    assert set(_needs(GATE_A_AGGREGATE)) == {GATE_A_PREFLIGHT, GATE_A_SHARD}
    assert set(_needs(GATE_B_AGGREGATE)) == {GATE_B_PREFLIGHT, GATE_B_SHARD}


def test_no_gate_job_depends_on_the_other_gate():
    """No Gate A job may be gated on Gate B's success, or vice versa.

    Naming the *other gate's label* is expected and required -- that is the
    mutual-exclusion guard. What must never appear is a dependency on the other
    gate's *job*: a `needs:` entry or a `needs.<job>.result` reference.
    """

    for job in GATE_A_JOBS:
        assert not any(name in GATE_B_JOBS for name in _needs(job)), job
        for foreign in GATE_B_JOBS:
            assert f"needs.{foreign}" not in job_if(job), job
    for job in GATE_B_JOBS:
        assert not any(name in GATE_A_JOBS for name in _needs(job)), job
        for foreign in GATE_A_JOBS:
            assert f"needs.{foreign}" not in job_if(job), job


def test_each_gate_guard_excludes_the_other_gates_label():
    """The mutual-exclusion guard itself, asserted positively."""

    assert f"!contains(github.event.pull_request.labels.*.name, '{LABEL_B}')" in job_if(
        GATE_A_PREFLIGHT
    )
    assert f"!contains(github.event.pull_request.labels.*.name, '{LABEL_A}')" in job_if(
        GATE_B_PREFLIGHT
    )


@pytest.mark.parametrize(
    "preflight_result", ["failure", "skipped", "cancelled"], ids=["failed", "skipped", "cancelled"]
)
@pytest.mark.parametrize(
    "gate,shard,aggregate,preflight",
    [
        (GATE_A, GATE_A_SHARD, GATE_A_AGGREGATE, "gate_a_anthropic_preflight"),
        (GATE_B, GATE_B_SHARD, GATE_B_AGGREGATE, "gate_b_openai_preflight"),
    ],
    ids=["gate-a", "gate-b"],
)
def test_a_non_successful_preflight_blocks_shards_and_aggregate(
    preflight_result, gate, shard, aggregate, preflight
):
    """§27 and §34.8 -- a skipped preflight must never produce an aggregate."""

    context = with_needs(
        dispatch_context(provider_gate=gate, confirm=True),
        **{preflight: preflight_result},
    )
    assert not evaluate(job_if(shard), context)
    assert not evaluate(job_if(aggregate), context)


def test_aggregate_uses_always_but_still_requires_preflight_success():
    for job, preflight in ((GATE_A_AGGREGATE, GATE_A_PREFLIGHT), (GATE_B_AGGREGATE, GATE_B_PREFLIGHT)):
        expression = job_if(job)
        assert "always()" in expression
        assert f"needs.{preflight}.result == 'success'" in expression


# ════════════════════ §28 artifact / provenance matrix ════════════════════


def _artifact_names(job_name: str) -> list[str]:
    names = []
    for step in jobs()[job_name].get("steps", []):
        if isinstance(step, dict) and "upload-artifact" in str(step.get("uses", "")):
            names.append(step.get("with", {}).get("name", ""))
    return names


def test_gate_artifacts_carry_their_gate_identity():
    for name in _artifact_names(GATE_A_SHARD) + _artifact_names(GATE_A_AGGREGATE):
        assert "gate-a-anthropic-primary" in name, name
        assert "gate-b" not in name, name
    for name in _artifact_names(GATE_B_SHARD) + _artifact_names(GATE_B_AGGREGATE):
        assert "gate-b-openai-fallback" in name, name
        assert "gate-a" not in name, name


def test_artifact_names_match_the_shared_naming_helper():
    assert release_gates.artifact_name(GATE_A, shard="0").replace(
        "shard-0", "shard-${{ matrix.shard }}"
    ) in _artifact_names(GATE_A_SHARD)
    assert release_gates.artifact_name(GATE_B, shard="0").replace(
        "shard-0", "shard-${{ matrix.shard }}"
    ) in _artifact_names(GATE_B_SHARD)
    assert release_gates.artifact_name(GATE_A) in _artifact_names(GATE_A_AGGREGATE)
    assert release_gates.artifact_name(GATE_B) in _artifact_names(GATE_B_AGGREGATE)


def test_no_artifact_can_be_named_for_a_non_gate():
    for value in (release_gates.GATE_NONE, "", None, "gate_c"):
        with pytest.raises(ValueError):
            release_gates.artifact_name(value)


def test_each_aggregate_downloads_only_its_own_gate_pattern():
    for job, slug, foreign in (
        (GATE_A_AGGREGATE, "gate-a-anthropic-primary", "gate-b"),
        (GATE_B_AGGREGATE, "gate-b-openai-fallback", "gate-a"),
    ):
        patterns = [
            step.get("with", {}).get("pattern", "")
            for step in jobs()[job].get("steps", [])
            if "download-artifact" in str(step.get("uses", ""))
        ]
        assert patterns and all(slug in pattern for pattern in patterns), job
        assert all(foreign not in pattern for pattern in patterns), job


def test_each_gate_declares_its_identity_in_the_environment():
    for job in GATE_A_JOBS:
        assert any(env.get(release_gates.GATE_ENV_VAR) == GATE_A for env in _all_env(jobs()[job])), job
    for job in GATE_B_JOBS:
        assert any(env.get(release_gates.GATE_ENV_VAR) == GATE_B for env in _all_env(jobs()[job])), job


def test_each_aggregate_pins_expected_gate_and_shard_count():
    for job, gate in ((GATE_A_AGGREGATE, GATE_A), (GATE_B_AGGREGATE, GATE_B)):
        text = _job_text(job)
        assert f"--expect-gate {gate}" in text, job
        assert "--expect-shard-count 6" in text, job


# ════════════════════ §29 one authoritative OpenAI preflight ════════════════════


def test_gate_b_invokes_the_audited_m3_module_and_nothing_weaker():
    text = _job_text(GATE_B_PREFLIGHT)
    assert "python -m evals.provider_preflight" in text
    # No inline reimplementation: the module's exit code is the gate.
    assert "chat.completions" not in text
    assert "python -c" not in text


def test_gate_a_invokes_the_anthropic_preflight_module():
    text = _job_text(GATE_A_PREFLIGHT)
    assert "python -m evals.anthropic_preflight" in text
    assert "messages.create" not in text
    assert "python -c" not in text


def test_no_workflow_job_inlines_a_python_heredoc():
    """The step summary moved into a module; nothing may drift back into YAML."""

    for name in jobs():
        assert "python -c" not in _job_text(name), name


# ════════════════════ unchanged eval semantics ════════════════════


def test_case_universe_shard_split_and_threshold_are_unchanged():
    for job in (GATE_A_SHARD, GATE_B_SHARD):
        text = _job_text(job)
        assert jobs()[job]["strategy"]["matrix"]["shard"] == [0, 1, 2, 3, 4, 5]
        assert "--shard-count 6" in text
        # The threshold still defaults to 0.75 and still reaches the runner --
        # but as a quoted environment value, never as shell source. See the
        # injection tests below.
        assert '--threshold "$EVAL_THRESHOLD"' in _all_run_text(job)
        assert jobs()[job]["strategy"]["fail-fast"] is False
    for job in (GATE_A_AGGREGATE, GATE_B_AGGREGATE):
        assert '--threshold "$EVAL_THRESHOLD"' in _all_run_text(job)


def test_both_gates_run_the_same_case_definitions():
    """Comparable evidence: same runner, same shard split, same threshold."""

    def normalize(job):
        return (
            _job_text(job)
            .replace("gate-a-anthropic-primary", "GATE")
            .replace("gate-b-openai-fallback", "GATE")
            .replace("gate_a_anthropic_primary", "GATE")
            .replace("gate_b_openai_fallback", "GATE")
            .replace("Gate A (Anthropic primary)", "GATE")
            .replace("Gate B (OpenAI fallback)", "GATE")
            .replace("Anthropic-primary", "GATE")
            .replace("OpenAI-fallback", "GATE")
        )

    a_lines = [line for line in normalize(GATE_A_SHARD).splitlines() if "run_evals" in line or "shard" in line]
    b_lines = [line for line in normalize(GATE_B_SHARD).splitlines() if "run_evals" in line or "shard" in line]
    assert a_lines == b_lines


def test_provenance_is_enabled_for_both_gate_shards_only():
    text = WORKFLOW_PATH.read_text()
    assert text.count("MAS_EVAL_PROVENANCE") == 2
    for job in (GATE_A_SHARD, GATE_B_SHARD):
        assert any(env.get("MAS_EVAL_PROVENANCE") == "1" for env in _all_env(jobs()[job])), job
    for job in ("smoke", GATE_A_PREFLIGHT, GATE_B_PREFLIGHT, GATE_A_AGGREGATE, GATE_B_AGGREGATE):
        assert "MAS_EVAL_PROVENANCE" not in _job_text(job), job


def test_the_job_set_is_exactly_the_declared_gates_plus_free_smoke():
    """No unaccounted job, and no unaccounted provider-bearing job.

    Replaces the pre-gate `test_workflow_declares_no_new_job...`: a new job that
    quietly carries a provider secret is the failure mode that matters, and it
    is caught here by enumeration rather than by a diff against a base commit.
    """

    assert set(jobs()) == {"smoke", *LIVE_JOBS}
    provider_bearing = sorted(name for name in jobs() if _secret_refs(name))
    assert provider_bearing == sorted(
        [GATE_A_PREFLIGHT, GATE_A_SHARD, GATE_B_PREFLIGHT, GATE_B_SHARD]
    )


def test_workflow_triggers_are_unchanged_apart_from_label_events():
    """The self-path trigger stays; `unlabeled` is added so removing a gate
    label re-evaluates eligibility instead of leaving a stale authorization."""

    triggers = workflow().get("on") or workflow()[True]
    assert set(triggers) == {"pull_request", "workflow_dispatch"}
    paths = triggers["pull_request"]["paths"]
    assert ".github/workflows/evals.yml" in paths
    assert "mas/evals/**" in paths
    types = triggers["pull_request"]["types"]
    assert {"ready_for_review", "converted_to_draft", "labeled", "unlabeled"} <= set(types)
    assert "push" not in triggers, "a push trigger would bypass PR authorization entirely"


def test_provenance_is_scoped_to_the_shard_run_step_only():
    for job in (GATE_A_SHARD, GATE_B_SHARD):
        carrying = [
            step.get("name")
            for step in jobs()[job]["steps"]
            if isinstance(step, dict) and "MAS_EVAL_PROVENANCE" in (step.get("env") or {})
        ]
        assert carrying == ["Run eval shard"], job


def test_dispatch_inputs_default_to_no_gate_and_no_confirmation():
    inputs = workflow()[True]["workflow_dispatch"]["inputs"] if True in workflow() else \
        workflow()["on"]["workflow_dispatch"]["inputs"]
    assert inputs["provider_gate"]["default"] == release_gates.GATE_NONE
    assert inputs["provider_gate"]["options"] == list(release_gates.GATE_CHOICES)
    # A typed boolean, defaulting to false: an unchecked box, not a free-text
    # sentinel whose safety rests on a string comparison.
    assert inputs["confirm_paid_execution"]["type"] == "boolean"
    assert inputs["confirm_paid_execution"]["default"] is False
    assert inputs["threshold"]["default"] == "0.75"


# ════════════════════ result taxonomy (§22 / §23) ════════════════════


def _summary(gate, *, pass_rate=1.0, threshold=0.75, errors=None, provider_unavailable=False):
    return {
        "provider_gate": gate,
        "pass_rate": pass_rate,
        "threshold": threshold,
        "aggregation_errors": errors or [],
        "provider_unavailable": provider_unavailable,
        "passed": 12,
        "total": 12,
    }


TAXONOMY = [
    ("no gate", dict(gate="none", authorized=True, preflight_passed=True, shards_complete=True,
                     summary=_summary("none")), release_gates.RESULT_AUTHORIZATION_NOT_SATISFIED),
    ("unauthorized", dict(gate=GATE_A, authorized=False, preflight_passed=True, shards_complete=True,
                          summary=_summary(GATE_A)), release_gates.RESULT_AUTHORIZATION_NOT_SATISFIED),
    ("preflight failed", dict(gate=GATE_A, authorized=True, preflight_passed=False, shards_complete=True,
                              summary=_summary(GATE_A)), release_gates.RESULT_PREFLIGHT_FAILURE),
    ("preflight not reached", dict(gate=GATE_A, authorized=True, preflight_passed=None, shards_complete=True,
                                   summary=_summary(GATE_A)), release_gates.RESULT_PREFLIGHT_FAILURE),
    ("shards incomplete", dict(gate=GATE_A, authorized=True, preflight_passed=True, shards_complete=False,
                               summary=_summary(GATE_A)), release_gates.RESULT_STRUCTURAL_FAILURE),
    ("no summary", dict(gate=GATE_A, authorized=True, preflight_passed=True, shards_complete=True,
                        summary=None), release_gates.RESULT_INFRASTRUCTURE_FAILURE),
    ("wrong gate in summary", dict(gate=GATE_A, authorized=True, preflight_passed=True, shards_complete=True,
                                   summary=_summary(GATE_B)), release_gates.RESULT_GATE_IDENTITY_MISMATCH),
    ("aggregation errors", dict(gate=GATE_A, authorized=True, preflight_passed=True, shards_complete=True,
                                summary=_summary(GATE_A, errors=["missing shard indices: 3"])),
     release_gates.RESULT_STRUCTURAL_FAILURE),
    ("provider unavailable", dict(gate=GATE_A, authorized=True, preflight_passed=True, shards_complete=True,
                                  summary=_summary(GATE_A, pass_rate=0.2, provider_unavailable=True)),
     release_gates.RESULT_PROVIDER_UNAVAILABLE),
    ("quality failure", dict(gate=GATE_A, authorized=True, preflight_passed=True, shards_complete=True,
                             summary=_summary(GATE_A, pass_rate=0.33)),
     release_gates.RESULT_QUALITY_FAILURE),
    ("pass", dict(gate=GATE_A, authorized=True, preflight_passed=True, shards_complete=True,
                  summary=_summary(GATE_A)), release_gates.RESULT_PASS),
]


@pytest.mark.parametrize("label,kwargs,expected", TAXONOMY, ids=[row[0] for row in TAXONOMY])
def test_gate_result_taxonomy(label, kwargs, expected):
    outcome = release_gates.evaluate_gate_outcome(threshold=0.75, **kwargs)
    assert outcome.result == expected, f"{label}: {outcome}"
    assert outcome.result in release_gates.GATE_RESULTS


def test_provider_unavailability_is_never_reported_as_quality_failure():
    """§22 -- the distinction the V7 live observation could not make."""

    outcome = release_gates.evaluate_gate_outcome(
        gate=GATE_B,
        authorized=True,
        preflight_passed=True,
        shards_complete=True,
        summary=_summary(GATE_B, pass_rate=0.0, provider_unavailable=True),
        threshold=0.75,
    )
    assert outcome.result == release_gates.RESULT_PROVIDER_UNAVAILABLE
    assert outcome.result != release_gates.RESULT_QUALITY_FAILURE


def test_gate_b_is_truthfully_deferred_and_never_claims_release_validation():
    assert "deferred" in release_gates.GATE_CLAIMS[GATE_B].lower()
    assert "Anthropic-only" in release_gates.GATE_CLAIMS[GATE_B]
    assert "NOT primary release validation" in release_gates.GATE_CLAIMS[GATE_B]
    assert "fallback" in release_gates.GATE_CLAIMS[GATE_B].lower()
    assert "release" in release_gates.GATE_CLAIMS[GATE_A].lower()
    assert "fallback" not in release_gates.GATE_CLAIMS[GATE_A].lower()


# ════════════════════ derived model sets ════════════════════


def test_required_models_are_derived_from_certified_routing():
    from config import MODEL_ROUTING, Provider

    anthropic = release_gates.required_models("anthropic")
    for phase in release_gates.EVAL_PHASES:
        config = MODEL_ROUTING[phase]
        if config.provider is Provider.ANTHROPIC:
            assert config.model in anthropic, phase
    assert release_gates.EVAL_JUDGE_MODEL in anthropic


def test_gate_b_probe_set_matches_the_openai_models_the_eval_can_reach():
    from evals import provider_preflight

    assert provider_preflight.PROBE_MODELS == release_gates.required_models("openai")


def test_gate_a_probe_set_matches_the_anthropic_models_the_eval_can_reach():
    from evals import anthropic_preflight

    assert anthropic_preflight.PROBE_MODELS == release_gates.required_models("anthropic")


def test_openai_sdk_is_pinned_to_the_historical_gate_a_resolution():
    requirements = (MAS_ROOT / "requirements.txt").read_text(encoding="utf-8")
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    # GitHub run 31554765254 resolved this exact build before the successful
    # deterministic smoke and Gate A execution. The later 3.0.0 resolution
    # broke the frozen SDK-shape parity suite, so an open upper range is unsafe.
    assert "openai==2.54.0" in requirements.splitlines()
    assert "python -m pip install 'openai==2.54.0'" in workflow
    assert "openai>=1.60.0" not in requirements
    assert "openai>=1.60.0" not in workflow


# ════════════════════ gate summary rendering ════════════════════


def test_step_summary_always_names_the_gate():
    from evals import gate_summary

    for gate in (GATE_A, GATE_B):
        rendered = gate_summary.render(_summary(gate))
        assert gate in rendered
        assert release_gates.GATE_TITLES[gate] in rendered
    a = gate_summary.render(_summary(GATE_A))
    b = gate_summary.render(_summary(GATE_B))
    assert a != b
    assert GATE_B not in a and GATE_A not in b


def test_step_summary_reports_the_taxonomy_result_not_just_a_score():
    from evals import gate_summary

    rendered = gate_summary.render(_summary(GATE_A, pass_rate=0.33))
    assert release_gates.RESULT_QUALITY_FAILURE in rendered
    rendered = gate_summary.render(
        _summary(GATE_A, pass_rate=0.0, provider_unavailable=True)
    )
    assert release_gates.RESULT_PROVIDER_UNAVAILABLE in rendered


def test_step_summary_escapes_table_breaking_values():
    from evals import gate_summary

    summary = _summary(GATE_A)
    summary["cases"] = [{"case_id": "G0|1\nbreak", "passed": True, "judge_overall": 90,
                         "domain_match": True, "frameworks_covered": 1.0, "must_mention_hits": 1.0}]
    rendered = gate_summary.render(summary)
    rows = [line for line in rendered.splitlines() if "G0" in line]
    # The newline must not have split the row in two...
    assert len(rows) == 1, rows
    # ...and the pipe must not have opened an extra column: 7 delimiters for 6
    # cells, counting only pipes that are not backslash-escaped.
    row = rows[0]
    delimiters = sum(
        1
        for index, character in enumerate(row)
        if character == "|" and (index == 0 or row[index - 1] != "\\")
    )
    assert delimiters == 7, row


# ════════════════════ cross-gate artifact refusal (§28) ════════════════════


def _write_shard(tmp_path, gate, index, case_ids):
    directory = tmp_path / f"shard-{index}"
    directory.mkdir()
    (directory / "summary.json").write_text(
        json.dumps(
            {
                "provider_gate": gate,
                "shard_index": index,
                "shard_count": 6,
                "cases": [
                    {"case_id": case_id, "passed": True, "judge_overall": 90}
                    for case_id in case_ids
                ],
            }
        )
    )
    return str(directory)


def test_an_artifact_from_one_gate_is_refused_by_the_other(tmp_path):
    from evals.run_evals import aggregate_summaries, load_cases

    all_ids = [case["id"] for case in load_cases()]
    dirs = [
        _write_shard(tmp_path, GATE_B, index, all_ids[index::6])
        for index in range(6)
    ]

    summary = aggregate_summaries(dirs, threshold=0.75, expect_gate=GATE_A, expect_shard_count=6)

    assert summary["aggregation_errors"], "Gate B shards were accepted as Gate A"
    assert any("gate identity mismatch" in error for error in summary["aggregation_errors"])
    assert summary["ok"] is False
    assert summary["provider_gate"] == GATE_A


def test_matching_gate_shards_aggregate_cleanly(tmp_path):
    from evals.run_evals import aggregate_summaries, load_cases

    all_ids = [case["id"] for case in load_cases()]
    dirs = [
        _write_shard(tmp_path, GATE_B, index, all_ids[index::6])
        for index in range(6)
    ]

    summary = aggregate_summaries(dirs, threshold=0.75, expect_gate=GATE_B, expect_shard_count=6)

    assert summary["aggregation_errors"] == []
    assert summary["provider_gate"] == GATE_B
    assert summary["total"] == len(all_ids)


def test_a_missing_shard_is_a_structural_error_not_a_smaller_denominator(tmp_path):
    from evals.run_evals import aggregate_summaries, load_cases

    all_ids = [case["id"] for case in load_cases()]
    dirs = [
        _write_shard(tmp_path, GATE_A, index, all_ids[index::6])
        for index in range(5)
    ]

    summary = aggregate_summaries(dirs, threshold=0.75, expect_gate=GATE_A, expect_shard_count=6)

    assert any("missing shard indices: 5" in error for error in summary["aggregation_errors"])
    assert summary["ok"] is False


def test_a_gateless_shard_cannot_be_absorbed_into_a_gate_aggregate(tmp_path):
    from evals.run_evals import aggregate_summaries, load_cases

    all_ids = [case["id"] for case in load_cases()]
    dirs = [_write_shard(tmp_path, "none", index, all_ids[index::6]) for index in range(6)]

    summary = aggregate_summaries(dirs, threshold=0.75, expect_gate=GATE_A, expect_shard_count=6)

    assert summary["aggregation_errors"]
    assert summary["ok"] is False


# ════════════════════ mutation tests ════════════════════
#
# The matrices above prove the guards behave correctly TODAY. These prove the
# matrices would NOTICE if the guards were loosened -- which is the only reason
# to trust them as a safety net rather than as documentation.
#
# Every mutation carries its own applicability assertion. A mutation that fails
# to apply -- because the text it rewrites no longer exists -- would otherwise
# masquerade as a detected safeguard, so a non-applying mutation is a harness
# failure and is reported as one, never as a pass.

MUTATION_CONTEXTS = {
    "unrelated label on a fully labelled Ready PR": (
        pull_request_context(
            draft=False, labels=[PAID, LABEL_A, UNRELATED], label=UNRELATED
        ),
        (),
    ),
    "push to a fully authorized PR": (
        pull_request_context(draft=False, labels=[PAID, LABEL_A], action="synchronize"),
        (),
    ),
    "draft PR carrying every label": (
        pull_request_context(draft=True, labels=[PAID, LABEL_A], label=PAID),
        (),
    ),
    "gate label present but paid-eval absent": (
        pull_request_context(draft=False, labels=[LABEL_A], label=LABEL_A),
        (),
    ),
    "both gate labels present": (
        pull_request_context(draft=False, labels=[PAID, LABEL_A, LABEL_B], label=PAID),
        (),
    ),
    "dispatch with no confirmation": (
        dispatch_context(provider_gate=GATE_A, confirm=None),
        (),
    ),
    "dispatch with confirmation false": (
        dispatch_context(provider_gate=GATE_A, confirm=False),
        (),
    ),
    "dispatch with a string confirmation": (
        dispatch_context(provider_gate=GATE_A, confirm="true"),
        (),
    ),
    "dispatch with no gate selected": (
        dispatch_context(provider_gate="none", confirm=True),
        (),
    ),
}


def _eligible_under(guards: dict[str, str], context: dict) -> tuple[str, ...]:
    full = with_needs(
        context,
        gate_a_anthropic_preflight="success",
        gate_b_openai_preflight="success",
    )
    return tuple(job for job in LIVE_JOBS if evaluate(guards[job], full))


def _current_guards() -> dict[str, str]:
    return {job: job_if(job) for job in LIVE_JOBS}


# name -> (old, new) applied to every live guard
GUARD_MUTATIONS = {
    "remove the event.label.name requirement": (
        "&& (github.event.action == 'ready_for_review' "
        "|| (github.event.action == 'labeled' "
        "&& (github.event.label.name == 'paid-eval' "
        "|| github.event.label.name == '{gate_label}')))",
        "&& (github.event.action == 'ready_for_review' "
        "|| github.event.action == 'labeled')",
    ),
    "accept any label event generically": (
        "(github.event.label.name == 'paid-eval' "
        "|| github.event.label.name == '{gate_label}')",
        "true",
    ),
    "drop the draft check": (
        "github.event.pull_request.draft == false && ",
        "",
    ),
    "drop the paid-eval requirement": (
        "contains(github.event.pull_request.labels.*.name, 'paid-eval') && ",
        "",
    ),
    "drop the competing-gate exclusion": (
        "&& !contains(github.event.pull_request.labels.*.name, '{other_label}') ",
        " ",
    ),
    "allow synchronize to authorize": (
        "github.event.action == 'ready_for_review'",
        "(github.event.action == 'ready_for_review' "
        "|| github.event.action == 'synchronize')",
    ),
    "remove the dispatch confirmation": (
        " && inputs.confirm_paid_execution == true",
        "",
    ),
    "weaken the confirmation from boolean true": (
        "inputs.confirm_paid_execution == true",
        "inputs.confirm_paid_execution",
    ),
    "make the dispatch gate default live": (
        "inputs.provider_gate == '{gate}'",
        "(inputs.provider_gate == '{gate}' || inputs.provider_gate == 'none')",
    ),
}


def _apply_guard_mutation(old: str, new: str) -> tuple[dict[str, str], int]:
    """Rewrite every live guard, reporting how many actually changed."""

    mutated, applied = {}, 0
    for job in LIVE_JOBS:
        expression = job_if(job)
        is_a = job in GATE_A_JOBS
        fields = {
            "gate_label": LABEL_A if is_a else LABEL_B,
            "other_label": LABEL_B if is_a else LABEL_A,
            "gate": GATE_A if is_a else GATE_B,
        }
        target, replacement = old.format(**fields), new.format(**fields)
        if target in expression:
            expression = expression.replace(target, replacement)
            applied += 1
        mutated[job] = expression
    return mutated, applied


@pytest.mark.parametrize("name", sorted(GUARD_MUTATIONS))
def test_every_guard_mutation_is_detected(name):
    old, new = GUARD_MUTATIONS[name]
    mutated, applied = _apply_guard_mutation(old, new)

    # Applicability first: a mutation that did not apply proves nothing.
    assert applied == len(LIVE_JOBS), (
        f"mutation {name!r} applied to {applied}/{len(LIVE_JOBS)} guards -- "
        "the guard text it rewrites no longer exists, so this test is not "
        "exercising a safeguard. Fix the mutation, do not delete it."
    )
    assert mutated != _current_guards()

    # ...then detection: at least one context that authorizes nothing today
    # must start authorizing under the mutation.
    escaped = [
        label
        for label, (context, expected) in MUTATION_CONTEXTS.items()
        if _eligible_under(mutated, context) != tuple(expected)
    ]
    assert escaped, (
        f"mutation {name!r} applied cleanly but no matrix row noticed. "
        "The authorization matrix has a hole."
    )


def test_the_unmutated_guards_authorize_nothing_in_any_mutation_context():
    """The control: every mutation context is a genuine non-authorizing state."""

    guards = _current_guards()
    for label, (context, expected) in MUTATION_CONTEXTS.items():
        assert _eligible_under(guards, context) == tuple(expected), label


# ── mutations of things that are not `if:` expressions ──


def test_restoring_the_unsafe_concurrency_policy_is_detected():
    """`cancel-in-progress: true` for everything is the pre-fix policy."""

    unsafe = "true"
    authorizing = pull_request_context(
        draft=False, labels=[PAID, LABEL_A], action="labeled", label=PAID
    )
    assert evaluate(workflow()["concurrency"]["cancel-in-progress"], authorizing) is False
    assert evaluate(unsafe, authorizing) is True


def test_dropping_the_sha_from_the_concurrency_group_is_detected():
    current = workflow()["concurrency"]["group"]
    mutated = current.replace(
        "format('paid-{0}', github.event.pull_request.head.sha || github.sha)",
        "'paid'",
    )
    assert mutated != current, "the SHA-keyed group text no longer exists"

    old = pull_request_context(draft=False, labels=[PAID, LABEL_A],
                               action="labeled", label=PAID, head_sha="AAA")
    new = pull_request_context(draft=False, labels=[PAID, LABEL_A],
                               action="labeled", label=PAID, head_sha="BBB")
    assert render(current, old) != render(current, new)
    assert render(mutated, old) == render(mutated, new)


@pytest.mark.parametrize("job", LIVE_JOBS)
def test_reintroducing_a_shell_interpolated_threshold_is_detected(job):
    scripts = _run_scripts(job)
    mutated = [
        s + "\n--threshold ${{ github.event.inputs.threshold || '0.75' }}" for s in scripts
    ]
    assert all("${{" not in s for s in scripts), job
    assert any("${{" in s for s in mutated)


def test_granting_a_job_both_provider_secrets_is_detected():
    """The credential matrix must fail if a gate is handed both keys."""

    for job in (GATE_A_PREFLIGHT, GATE_B_PREFLIGHT):
        envs = _all_env(jobs()[job])
        rendered = yaml.safe_dump(envs, width=10**9)
        has_anthropic_secret = "secrets.ANTHROPIC_API_KEY" in rendered
        has_openai_secret = "secrets.OPENAI_API_KEY" in rendered
        assert not (has_anthropic_secret and has_openai_secret), job

        mutated = rendered + "\n  ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}\n" \
                             "  OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}\n"
        assert "secrets.ANTHROPIC_API_KEY" in mutated and "secrets.OPENAI_API_KEY" in mutated


def test_removing_the_aggregate_gate_identity_check_is_detected(tmp_path):
    """`--expect-gate` is what stops one gate's shards feeding the other's report."""

    from evals.run_evals import aggregate_summaries, load_cases

    for job, gate in ((GATE_A_AGGREGATE, GATE_A), (GATE_B_AGGREGATE, GATE_B)):
        assert f"--expect-gate {gate}" in _all_run_text(job), job

    all_ids = [case["id"] for case in load_cases()]
    dirs = [_write_shard(tmp_path, GATE_B, i, all_ids[i::6]) for i in range(6)]

    guarded = aggregate_summaries(dirs, threshold=0.75, expect_gate=GATE_A,
                                  expect_shard_count=6)
    assert guarded["aggregation_errors"], "cross-gate shards were absorbed"

    unguarded = aggregate_summaries(dirs, threshold=0.75, expect_gate=None,
                                    expect_shard_count=6)
    assert not unguarded["aggregation_errors"], (
        "dropping --expect-gate changed nothing, so the flag is not what "
        "enforces gate identity and this test proves nothing"
    )


# ════════════════════ diff hygiene ════════════════════
#
# The harness diff must contain the harness and nothing else. A runtime artifact
# that rides along -- a local DB mutated by a mock run, a log, a cache -- is not
# a change anyone reviewed, and it silently rebases the "what changed" question.

CERTIFIED_PRODUCT_BASELINE = "0ead418afa0447ae0a90535aaf8ae392df06b403"
HARNESS_BASELINE = "46c4fbeb303f91c6434f94eb8bb103287f37879b"
OPTION_2_BASELINE = "20a104f370e3cd427d2c3a5c02d72c4110b802a3"

CERTIFIED_PRODUCT_PATHS = (
    "mas/api.py",
    "mas/config.py",
    "mas/extensions/runtime.py",
    "mas/llm_client.py",
    "mas/orchestrator.py",
    "mas/prompts/phases/03-strategy.md",
    "mas/runtime/provider_gateway.py",
    "mas/state.py",
    "mas/tests/evidence_snapshot_pg.py",
    "mas/tests/test_classify_schema_repair.py",
    "mas/tests/test_operator_dossier.py",
    "mas/tests/test_research_evidence_bridge_pg.py",
    "mas/tests/test_runtime_gateway.py",
    "mas/tests/test_strategy_retrieval_integration.py",
    "mas/tests/test_support_phases.py",
    "mas/tests/test_workflow_runner.py",
)

GENERATED_RESIDUE_SUFFIXES = (".sqlite3", ".sqlite", ".db", ".log", ".pyc")


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        pytest.skip(f"git unavailable or ref missing: {result.stderr.strip()[:200]}")
    return result.stdout


def _changed_since_harness_baseline() -> list[str]:
    return [p for p in _git("diff", "--name-only", HARNESS_BASELINE, "--").split("\n") if p]


def test_option_2_changes_only_the_runtime_boundary_inside_the_product_manifest():
    """The new product identity changes only the gateway and its contract tests."""

    changed = set(_git(
        "diff", "--name-only", OPTION_2_BASELINE, "--", *CERTIFIED_PRODUCT_PATHS
    ).splitlines())
    assert changed == {
        "mas/runtime/provider_gateway.py",
        "mas/tests/test_runtime_gateway.py",
    }


def test_no_generated_runtime_data_rides_along_in_the_harness_diff():
    """`mas/scenario_shadow.sqlite3` is a tracked fixture that a local mock eval
    rewrites in place. It reached #118's inventory as residue, not as a change."""

    residue = [
        path
        for path in _changed_since_harness_baseline()
        if path.endswith(GENERATED_RESIDUE_SUFFIXES)
    ]
    assert residue == [], f"generated runtime data in the harness diff: {residue}"


def test_the_scenario_shadow_fixture_is_unchanged():
    changed = _git(
        "diff", "--name-only", HARNESS_BASELINE, "--", "mas/scenario_shadow.sqlite3"
    ).strip()
    assert changed == "", "scenario_shadow.sqlite3 differs from the harness baseline"


def test_the_option_2_closure_diff_touches_only_declared_paths():
    allowed_paths = {
        ".github/workflows/evals.yml",
        "mas/evals/README.md",
        "mas/evals/release_gates.py",
        "mas/requirements.txt",
        "mas/runtime/provider_gateway.py",
        "mas/tests/test_gate_b_fallback_evidence.py",
        "mas/tests/test_provider_attempt_telemetry_capture.py",
        "mas/tests/test_provider_attempt_telemetry_gateway.py",
        "mas/tests/test_release_gates_workflow.py",
        "mas/tests/test_runtime_gateway.py",
    }
    stray = [
        path
        for path in _git("diff", "--name-only", OPTION_2_BASELINE, "--").splitlines()
        if path not in allowed_paths
    ]
    assert stray == [], f"unexpected paths in the Option 2 closure diff: {stray}"
