"""Deterministic proof of the V7 Gate A / Gate B release harness.

Every assertion here is static or fake-driven. No provider call is made, no
secret is read, and no workflow is dispatched.

The authorization matrix parses the real `if:` expressions out of `evals.yml`
and evaluates them against synthetic event contexts, so loosening a guard in the
workflow breaks these tests rather than quietly widening what can spend money.
"""

from __future__ import annotations

import json
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
    with_needs,
)

GATE_A = release_gates.GATE_A
GATE_B = release_gates.GATE_B
LABEL_A = release_gates.GATE_LABELS[GATE_A]
LABEL_B = release_gates.GATE_LABELS[GATE_B]
PAID = release_gates.PAID_EVAL_LABEL
CONFIRM = release_gates.DISPATCH_CONFIRMATION_VALUE

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


# ════════════════════ §25 static authorization matrix ════════════════════
#
# A live provider job requires BOTH an authorization act and a gate selection.
# Neither alone is sufficient, and no default supplies either.

AUTHORIZATION_MATRIX = [
    # id, context, jobs expected eligible
    ("A1 draft + no label", pull_request_context(draft=True, labels=[]), ()),
    ("A2 ready + no label", pull_request_context(draft=False, labels=[]), ()),
    ("A3 draft + paid-eval", pull_request_context(draft=True, labels=[PAID]), ()),
    (
        "A3b draft + paid-eval + gate A",
        pull_request_context(draft=True, labels=[PAID, LABEL_A]),
        (),
    ),
    (
        "A4 ready + paid-eval + gate A",
        pull_request_context(draft=False, labels=[PAID, LABEL_A]),
        GATE_A_JOBS,
    ),
    (
        "A5 ready + paid-eval + gate B",
        pull_request_context(draft=False, labels=[PAID, LABEL_B]),
        GATE_B_JOBS,
    ),
    (
        "A6 dispatch gate A + confirmation",
        dispatch_context(provider_gate=GATE_A, confirm=CONFIRM),
        GATE_A_JOBS,
    ),
    (
        "A7 dispatch gate B + confirmation",
        dispatch_context(provider_gate=GATE_B, confirm=CONFIRM),
        GATE_B_JOBS,
    ),
    ("A8a dispatch gate none", dispatch_context(provider_gate="none", confirm=CONFIRM), ()),
    ("A8b dispatch gate absent", dispatch_context(provider_gate=None, confirm=CONFIRM), ()),
    ("A8c dispatch invalid gate", dispatch_context(provider_gate="gate_c", confirm=CONFIRM), ()),
    (
        "A8d dispatch gate A, no confirmation",
        dispatch_context(provider_gate=GATE_A, confirm=None),
        (),
    ),
    (
        "A8e dispatch gate A, wrong confirmation",
        dispatch_context(provider_gate=GATE_A, confirm="yes"),
        (),
    ),
    (
        "A8f ready + gate label but no paid-eval",
        pull_request_context(draft=False, labels=[LABEL_A]),
        (),
    ),
    (
        "A8g ready + paid-eval + BOTH gate labels",
        pull_request_context(draft=False, labels=[PAID, LABEL_A, LABEL_B]),
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
        pull_request_context(draft=False, labels=["documentation"]),
        gate_a_anthropic_preflight="success",
        gate_b_openai_preflight="success",
    )
    for job in LIVE_JOBS:
        assert not evaluate(job_if(job), context), job


def test_smoke_has_no_if_guard_and_is_always_free():
    smoke = jobs()["smoke"]
    assert "if" not in smoke
    text = _job_text("smoke")
    assert "secrets." not in text
    assert "--mock" in text


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
        dispatch_context(provider_gate=gate, confirm=CONFIRM),
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
        assert "--threshold ${{ github.event.inputs.threshold || '0.75' }}" in text
        assert jobs()[job]["strategy"]["fail-fast"] is False
    for job in (GATE_A_AGGREGATE, GATE_B_AGGREGATE):
        assert "--threshold ${{ github.event.inputs.threshold || '0.75' }}" in _job_text(job)


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
    assert inputs["confirm_paid_execution"]["default"] == ""


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


def test_gate_b_pass_never_claims_primary_release_validation():
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
