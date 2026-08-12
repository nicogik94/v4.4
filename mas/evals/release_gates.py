"""Gate identity, provider posture and result taxonomy for V7 release validation.

V7 release validation runs as **two different gates that answer two different
questions**.  They share the golden-case universe, the judge rubric and the pass
threshold so their results are comparable, but they do not share a claim:

``gate_a_anthropic_primary``
    Validates the *normal production provider posture*: Anthropic present,
    OpenAI absent.  A PASS is a statement about the release.

``gate_b_openai_fallback``
    Historical compatibility-gate identity. Its FAIL remains preserved, while
    OpenAI fallback is deferred outside the supported Anthropic-only V7 runtime.
    A future PASS would be a statement about that exact experimental candidate,
    **not** about the V7 release.

Neither gate's evidence may be read as the other's.  Every live-capable run
therefore carries a machine-readable gate identity from the workflow input all
the way to the aggregate artifact, and the aggregator refuses to combine shards
whose identity does not match the gate it was told to aggregate.

Nothing in this module makes a provider call, and nothing here is imported by a
certified product path.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from config import (
    FALLBACK_CHAIN,
    MODEL_ROUTING,
    TASK_PROFILE_BY_PHASE,
    TASK_PROFILE_MODEL_CANDIDATES,
    Provider,
)


# ═══ Closed gate vocabulary ═══
#
# `none` is a real member: it is what an unselected gate resolves to, and it is
# the default everywhere.  A gate is never inferred from context -- an absent or
# unrecognized selection resolves to `none`, which authorizes nothing.

GATE_A = "gate_a_anthropic_primary"
GATE_B = "gate_b_openai_fallback"
GATE_NONE = "none"

GATE_IDENTITIES = (GATE_A, GATE_B)
GATE_CHOICES = (GATE_NONE, GATE_A, GATE_B)

GATE_ENV_VAR = "MAS_PROVIDER_GATE"

# The PR labels that select a gate.  Deliberately distinct from `paid-eval`:
# `paid-eval` authorizes *spending*, a gate label chooses *what is spent on*.
# Requiring both means neither a label alone nor Ready alone can start a run.
GATE_LABELS = {
    GATE_A: "gate-a-anthropic-primary",
    GATE_B: "gate-b-openai-fallback",
}
PAID_EVAL_LABEL = "paid-eval"

# ── Which *label event* may authorize which gate ──
#
# Label *presence* says what the PR is asking for.  It does not say that anyone
# just asked for it: presence persists across every later event, so a workflow
# keyed on presence alone treats an unrelated `labeled` event -- adding
# `documentation` -- as a fresh instruction to spend money.  That was F-1.
#
# Authorization therefore needs a second fact: the event itself must be the act
# that *completes* the authorization state.  Exactly two labels can complete it
# for a given gate:
#
#   `paid-eval`      -- spend was just authorized, and this gate was already the
#                       one selected;
#   the gate's label -- this gate was just selected, and spend was already
#                       authorized.
#
# Adding any other label completes nothing and authorizes nothing, however the
# PR happens to be labelled at that moment.
AUTHORIZING_LABELS = {
    GATE_A: (PAID_EVAL_LABEL, GATE_LABELS[GATE_A]),
    GATE_B: (PAID_EVAL_LABEL, GATE_LABELS[GATE_B]),
}

# The `pull_request` actions that can carry an authorization at all.  Every
# other action in the trigger list -- `opened`, `synchronize`, `reopened`,
# `unlabeled`, `converted_to_draft` -- is a state change that authorizes
# nothing, and a push in particular must never re-spend on its own.
AUTHORIZING_ACTIONS = ("labeled", "ready_for_review")

# `workflow_dispatch` confirmation is a typed boolean, not a free-text literal.
#
# The previous free-text `RUN-PAID` sentinel was defended as un-satisfiable by
# omission, which is true -- but it is satisfied by *case-insensitive
# near-misses being rejected*, i.e. it turns a safety property into a string
# comparison, and it gives the operator no schema-level signal that the field is
# a confirmation.  A `type: boolean` input defaults to `false`, is rendered as an
# unchecked box, and cannot be satisfied by any typo.
DISPATCH_CONFIRMATION_INPUT = "confirm_paid_execution"
DISPATCH_GATE_INPUT = "provider_gate"

GATE_TITLES = {
    GATE_A: "Gate A - Anthropic primary release validation",
    GATE_B: "Gate B - OpenAI fallback compatibility (deferred)",
}

GATE_CLAIMS = {
    GATE_A: (
        "The normal V7 production provider posture (Anthropic primary) meets "
        "the release quality threshold."
    ),
    GATE_B: (
        "Historical compatibility evidence for the deferred OpenAI fallback "
        "capability. Supported V7 is Anthropic-only, so this is NOT primary "
        "release validation."
    ),
}


# ═══ Result taxonomy ═══
#
# Provider or infrastructure inability is never reported as a quality failure.
# `quality_failure` is reserved for a run that actually measured quality.

RESULT_PASS = "pass"
RESULT_QUALITY_FAILURE = "quality_failure"
RESULT_PROVIDER_UNAVAILABLE = "provider_unavailable"
RESULT_PREFLIGHT_FAILURE = "preflight_failure"
RESULT_STRUCTURAL_FAILURE = "structural_failure"
RESULT_INFRASTRUCTURE_FAILURE = "infrastructure_failure"
RESULT_AUTHORIZATION_NOT_SATISFIED = "authorization_not_satisfied"
RESULT_GATE_IDENTITY_MISMATCH = "gate_identity_mismatch"

GATE_RESULTS = (
    RESULT_PASS,
    RESULT_QUALITY_FAILURE,
    RESULT_PROVIDER_UNAVAILABLE,
    RESULT_PREFLIGHT_FAILURE,
    RESULT_STRUCTURAL_FAILURE,
    RESULT_INFRASTRUCTURE_FAILURE,
    RESULT_AUTHORIZATION_NOT_SATISFIED,
    RESULT_GATE_IDENTITY_MISMATCH,
)


@dataclass(frozen=True)
class ProviderPosture:
    """Which credential a gate's live jobs receive, and which they must not.

    `blank_providers` is not "unset": the workflow assigns an explicit empty
    string, so a job that inherits a runner-level value still sees blank. The
    historical harness retains both postures, but the supported V7 runtime now
    independently makes OpenAI ineligible even when its key is present.
    """

    gate: str
    available_providers: tuple[str, ...]
    blank_providers: tuple[str, ...]

    def secret_name(self, provider: str) -> str:
        return f"{provider.upper()}_API_KEY"


GATE_POSTURES = {
    GATE_A: ProviderPosture(
        gate=GATE_A,
        available_providers=(Provider.ANTHROPIC.value,),
        blank_providers=(Provider.OPENAI.value,),
    ),
    GATE_B: ProviderPosture(
        gate=GATE_B,
        available_providers=(Provider.OPENAI.value,),
        blank_providers=(Provider.ANTHROPIC.value,),
    ),
}


# ═══ Phases a live eval actually exercises ═══
#
# Kept in step with `run_evals.run_case_real`, plus the judge.  A preflight that
# probed models the eval never reaches would be theatre; one that missed a model
# the eval does reach would fail mid-run after spending money.

EVAL_PHASES = ("classify", "hypotheses", "gauntlet", "audit", "strategy")
EVAL_JUDGE_PHASE = "eval_judge"
EVAL_JUDGE_MODEL = "claude-sonnet-4-6"


def _phase_default_model(phase: str) -> tuple[str, str]:
    config = MODEL_ROUTING.get(phase) or MODEL_ROUTING["audit"]
    return config.provider.value, config.model


def _task_profile(phase: str) -> str:
    return TASK_PROFILE_BY_PHASE.get(phase, TASK_PROFILE_BY_PHASE["audit"])


def required_models(provider: str) -> tuple[str, ...]:
    """Models in each gate's frozen routing inventory, in first-seen order.

    Derived from the routing tables rather than restated. For Anthropic this is
    the supported Gate A execution inventory. For OpenAI it preserves the
    historical Gate B harness inventory even though the production runtime's V7
    eligibility boundary prevents those candidates from executing.
    """

    models: list[str] = []

    def add(candidate_provider: str, model: str) -> None:
        if candidate_provider == provider and model and model not in models:
            models.append(model)

    for phase in EVAL_PHASES:
        add(*_phase_default_model(phase))
        for alias in TASK_PROFILE_MODEL_CANDIDATES.get(_task_profile(phase), []):
            if alias == "phase_default":
                add(*_phase_default_model(phase))
            elif ":" in alias:
                alias_provider, _, alias_model = alias.partition(":")
                add(alias_provider, alias_model)

    # The judge is pinned to Anthropic by `config_override`. The historical
    # candidate inventory appended both providers behind that override, which
    # is why the retained Gate B harness probes both. Supported V7 applies its
    # Anthropic-only eligibility boundary before attempts begin, so this
    # inventory does not authorize or expose an OpenAI production path.
    add(Provider.ANTHROPIC.value, EVAL_JUDGE_MODEL)
    for alias in TASK_PROFILE_MODEL_CANDIDATES.get(_task_profile(EVAL_JUDGE_PHASE), []):
        if alias == "phase_default":
            add(*_phase_default_model(EVAL_JUDGE_PHASE))
        elif ":" in alias:
            alias_provider, _, alias_model = alias.partition(":")
            add(alias_provider, alias_model)

    for model in FALLBACK_CHAIN.get(Provider(provider), []):
        add(provider, model)

    return tuple(models)


def normalize_gate(value: object) -> str:
    """Resolve any input to a member of the closed vocabulary.

    Unrecognized, absent, empty and malformed values all resolve to
    `GATE_NONE`.  There is no "best guess": guessing a gate is exactly the
    confusion this module exists to prevent.
    """

    if not isinstance(value, str):
        return GATE_NONE
    candidate = value.strip().lower()
    return candidate if candidate in GATE_CHOICES else GATE_NONE


def gate_from_env(environ: dict | None = None) -> str:
    source = os.environ if environ is None else environ
    return normalize_gate(source.get(GATE_ENV_VAR, ""))


# ═══ Threshold input ═══
#
# The pass-rate threshold is the one live input an operator types by hand, and
# it used to be interpolated by `${{ }}` directly into the shell source of steps
# that hold a provider secret.  There it was *code*, so `0.75; env` was a
# command.  It is now passed as an environment value and quoted as a single
# argument, and validated here before any provider-touching step runs.
#
# Validation is deliberately strict rather than forgiving: a threshold that
# cannot be read as a number is a *configuration* failure, and configuration
# failures must stop the run before it can become a provider failure.

THRESHOLD_ENV_VAR = "EVAL_THRESHOLD"
DEFAULT_THRESHOLD = 0.75


class ThresholdError(ValueError):
    """An operator-supplied threshold that must not reach a provider job."""


# A plain decimal literal and nothing else. `float()` on its own would accept
# `nan`, `inf`, `1_0`, and embedded newlines -- none of which is a pass rate.
_DECIMAL_RE = re.compile(r"\+?\d+(?:\.\d*)?|\+?\.\d+")


def normalize_threshold(raw: object) -> float:
    """Parse an operator-supplied threshold, or refuse it.

    Accepts only a finite decimal number in ``[0.0, 1.0]``.  Everything else --
    shell metacharacters, command substitutions, newlines, `nan`, `inf`,
    hex/underscore Python float literals -- is refused, because the value's only
    legitimate shape is a plain pass-rate.
    """

    if isinstance(raw, bool):
        raise ThresholdError("threshold must be a number, not a boolean")
    if isinstance(raw, (int, float)):
        text = repr(float(raw))
    elif isinstance(raw, str):
        text = raw.strip()
    else:
        raise ThresholdError(f"threshold must be a number, got {type(raw).__name__}")

    if not text:
        raise ThresholdError("threshold is empty")
    # `float()` accepts `nan`, `inf`, `1_0` and surrounding whitespace/newlines;
    # none of those are a pass rate, so the shape is pinned before conversion.
    #
    # The rejected text is deliberately NOT quoted back. This message reaches
    # the log of a job that holds a provider secret, and echoing operator input
    # there would let a hostile threshold place chosen text in that log.
    if not _DECIMAL_RE.fullmatch(text):
        raise ThresholdError("threshold is not a plain decimal number")
    parsed = float(text)
    if not 0.0 <= parsed <= 1.0:
        # Also not echoed. A parsed float is harmless in isolation, but "never
        # echo operator input" is only a usable invariant if it has no
        # exceptions to reason about at the call site.
        raise ThresholdError("threshold is outside [0.0, 1.0]")
    return parsed


def threshold_from_env(environ: dict | None = None) -> float:
    """The validated threshold for this job, defaulting to the release value."""

    source = os.environ if environ is None else environ
    raw = source.get(THRESHOLD_ENV_VAR, "")
    if not str(raw).strip():
        return DEFAULT_THRESHOLD
    return normalize_threshold(raw)


def is_live_gate(gate: str) -> bool:
    return normalize_gate(gate) in GATE_IDENTITIES


def artifact_name(gate: str, *, shard: object = None) -> str:
    """Artifact name carrying the gate identity in the name itself.

    A name is the one piece of metadata a human reads before downloading, and
    the one CI cannot strip.  `GATE_NONE` never names an artifact.
    """

    normalized = normalize_gate(gate)
    if normalized not in GATE_IDENTITIES:
        raise ValueError(f"refusing to name an artifact for gate {normalized!r}")
    slug = normalized.replace("_", "-")
    if shard is None:
        return f"eval-report-{slug}"
    return f"eval-report-{slug}-shard-{shard}"


@dataclass(frozen=True)
class GateOutcome:
    """A gate's final machine-readable state and the evidence behind it."""

    gate: str
    result: str
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return self.result == RESULT_PASS


def evaluate_gate_outcome(
    *,
    gate: str,
    authorized: bool,
    preflight_passed: bool | None,
    shards_complete: bool | None,
    summary: dict | None,
    threshold: float,
) -> GateOutcome:
    """Fold a gate's evidence into exactly one result.

    Order matters and is deliberate: the earliest unmet precondition wins, so a
    run that never got a provider can never be reported as a quality failure.
    `None` means "not reached", which is distinct from `False` ("reached and
    failed") and is reported as a structural rather than quality problem.
    """

    normalized = normalize_gate(gate)
    if normalized not in GATE_IDENTITIES:
        return GateOutcome(normalized, RESULT_AUTHORIZATION_NOT_SATISFIED, ("no gate selected",))
    if not authorized:
        return GateOutcome(normalized, RESULT_AUTHORIZATION_NOT_SATISFIED, ("paid authorization not satisfied",))
    if preflight_passed is not True:
        return GateOutcome(normalized, RESULT_PREFLIGHT_FAILURE, ("provider preflight did not pass",))
    if shards_complete is not True:
        return GateOutcome(normalized, RESULT_STRUCTURAL_FAILURE, ("live shards did not complete",))
    if not isinstance(summary, dict):
        return GateOutcome(normalized, RESULT_INFRASTRUCTURE_FAILURE, ("no aggregate summary",))

    summary_gate = normalize_gate(summary.get("provider_gate"))
    if summary_gate != normalized:
        return GateOutcome(
            normalized,
            RESULT_GATE_IDENTITY_MISMATCH,
            (f"summary carries gate {summary_gate!r}",),
        )

    if summary.get("aggregation_errors"):
        return GateOutcome(normalized, RESULT_STRUCTURAL_FAILURE, ("aggregation errors present",))

    # `build_aggregate_diagnostics` is splatted into the summary's top level,
    # so that is the authoritative location; the nested form is accepted only
    # so a caller holding a diagnostics sub-dict is not silently misread.
    diagnostics = summary
    if isinstance(summary.get("aggregate_diagnostics"), dict):
        diagnostics = summary["aggregate_diagnostics"]
    if diagnostics.get("provider_unavailable"):
        return GateOutcome(normalized, RESULT_PROVIDER_UNAVAILABLE, ("provider unavailable during eval",))

    pass_rate = summary.get("pass_rate")
    if not isinstance(pass_rate, (int, float)):
        return GateOutcome(normalized, RESULT_INFRASTRUCTURE_FAILURE, ("summary carries no pass_rate",))
    if pass_rate < threshold:
        return GateOutcome(normalized, RESULT_QUALITY_FAILURE, (f"pass_rate {pass_rate:.4f} < {threshold}",))

    return GateOutcome(normalized, RESULT_PASS, ())


def _main() -> int:
    """`python -m evals.release_gates --validate-threshold`.

    Runs as the first step of each gate's preflight job, before any provider
    credential is used, so a malformed threshold fails the gate as a
    configuration error rather than surfacing later as a provider or quality
    problem.  The value is never echoed back: it is operator input, and a
    diagnostic that prints it would put attacker-chosen text in a log line of a
    job that holds a secret.
    """

    import argparse

    parser = argparse.ArgumentParser(prog="evals.release_gates")
    parser.add_argument(
        "--validate-threshold",
        action="store_true",
        help=f"Validate ${THRESHOLD_ENV_VAR} and exit non-zero if it is unusable",
    )
    args = parser.parse_args()

    if not args.validate_threshold:
        parser.error("nothing to do; pass --validate-threshold")

    try:
        threshold = threshold_from_env()
    except ThresholdError as exc:
        print(f"THRESHOLD REJECTED: {exc.args[0] if exc.args else 'invalid'}")
        print("Refusing to start a provider job with an unusable threshold.")
        return 2
    print(f"Threshold OK: {threshold}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    raise SystemExit(_main())
