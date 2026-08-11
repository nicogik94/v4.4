"""Gate identity, provider posture and result taxonomy for V7 release validation.

V7 release validation runs as **two different gates that answer two different
questions**.  They share the golden-case universe, the judge rubric and the pass
threshold so their results are comparable, but they do not share a claim:

``gate_a_anthropic_primary``
    Validates the *normal production provider posture*: Anthropic present,
    OpenAI absent.  A PASS is a statement about the release.

``gate_b_openai_fallback``
    Validates that when Anthropic cannot service a call, the certified V7
    logical contract remains usable on the OpenAI fallback path: Anthropic
    absent, OpenAI present.  A PASS is a statement about fallback
    compatibility, **not** about the release.

Neither gate's evidence may be read as the other's.  Every live-capable run
therefore carries a machine-readable gate identity from the workflow input all
the way to the aggregate artifact, and the aggregator refuses to combine shards
whose identity does not match the gate it was told to aggregate.

Nothing in this module makes a provider call, and nothing here is imported by a
certified product path.
"""

from __future__ import annotations

import os
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

# The literal a `workflow_dispatch` operator must type to confirm paid spend.
# A checkbox default can drift to `true`; a free-text literal cannot be
# satisfied by omission.
DISPATCH_CONFIRMATION_VALUE = "RUN-PAID"

GATE_TITLES = {
    GATE_A: "Gate A - Anthropic primary release validation",
    GATE_B: "Gate B - OpenAI fallback compatibility",
}

GATE_CLAIMS = {
    GATE_A: (
        "The normal V7 production provider posture (Anthropic primary) meets "
        "the release quality threshold."
    ),
    GATE_B: (
        "When Anthropic cannot service the call, the certified V7 logical "
        "contract remains usable on the OpenAI fallback path. This is NOT "
        "primary release validation."
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
    string, so a job that inherits a runner-level value still sees blank.  The
    runtime reads a blank key as *provider unavailable* and skips that
    provider's candidates, which is precisely how each gate is forced onto the
    path it claims to validate.
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
    """Models of `provider` the live eval can actually reach, in first-seen order.

    Derived from the certified routing tables rather than restated, so a routing
    change cannot silently leave a gate probing the wrong set.  Covers phase
    defaults, task-profile candidates, the fallback chain and the judge
    override -- every candidate source `select_model_candidates` consults.
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

    # The judge is pinned to Anthropic by `config_override`, but the override is
    # only the *first* candidate: the gateway still appends the phase's profile
    # and chain candidates behind it, so a blank Anthropic key routes the judge
    # onto its OpenAI fallback rather than failing the run.  Both providers
    # therefore have a real judge path, and both are probed.
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
