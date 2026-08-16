"""
v4.1 MAS — Eval Harness (Batch API variant)

Same scoring logic as run_evals.py, but routes the LLM-judge calls through
Anthropic's Message Batches API: 50% cheaper, 24h max turnaround. Use this for:
  - Nightly full-suite runs
  - Large regression sweeps after major prompt rewrites
  - Broad benchmarks across model families

Use the regular `run_evals.py` for PR feedback where latency matters more than cost.

Workflow:
  1. Run all 12 cases through the real pipeline (classify → strategy) — same as real-time
  2. Build all 12 judge prompts
  3. Submit as ONE batch to Anthropic
  4. Poll until ended (typically minutes, max 24h)
  5. Retrieve results, score, write report

Usage:
    python -m evals.run_evals_batch                    # full run, wait for completion
    python -m evals.run_evals_batch --submit-only      # submit and exit (resume later)
    python -m evals.run_evals_batch --resume <batch_id>  # collect a previously submitted batch

─────────────────────────── nightly oracle truth ───────────────────────────

A nightly run is only evidence about product quality if it actually *measured*
quality.  Before this wave the harness could not tell the difference: a judge
response it failed to parse, a batch request the provider errored, and a case
whose judge result never arrived all became ``score 0`` and then flowed through
the ordinary pass/fail path, so provider inability was reported as a quality
regression.  Sixty consecutive scheduled nightlies were red for exactly that
reason and none of them measured anything.

So every run now emits a machine-readable validity contract alongside the
existing counters, and the two are kept apart on purpose:

``valid_observation``
    Did this run obtain a complete, attributable, structurally valid quality
    observation over the whole expected case universe?
``result_class``
    One member of the closed taxonomy already defined by
    ``evals.release_gates``.  ``quality_failure`` is reachable **only** when
    ``valid_observation`` is true, so a provider outage can never be reported
    as a product regression.

An invalid observation is still operationally red — the objective is truthful
red, not cosmetic green.

The same rule governs the *pipeline* half of a case, and for the same reason.
An ordinary ``LLMResponse(ok=False)`` does not raise: the orchestrator marks the
phase failed, records a typed category and returns the state, so a run whose
every phase was provider-dead left no exception behind at all.  Completion is
therefore read from the phase state the product actually records, and it is
proven rather than assumed — a phase that cannot be shown to have completed
fails the case closed instead of counting as a measurement.

Two further ways a run could claim more than it measured are closed the same
way.  A ``--resume`` collection scores pipeline outputs that were produced at
*submit* time, so the submit-time commit is persisted in the batch input cache
and the resume binds to it: a cache carrying no commit, an unreadable one, or
one that disagrees with the resuming checkout fails closed rather than
relabelling outputs from commit A with the SHA of commit B.  And ``--mock``
fabricates its judge scores, so it reports ``mode: mock`` and can never be a
valid observation; its synthetic smoke verdict is kept in its own field, well
away from the measured-quality counters.

Nothing in this module is imported by a product path, and nothing here changes
what the judge is asked, which model answers, which cases run, or where the
pass threshold sits.
"""
import os
import re
import sys
import json
import time
import asyncio
import argparse
import logging
import subprocess
from pathlib import Path
from datetime import datetime
from dataclasses import asdict, dataclass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.run_evals import (
    load_cases, run_case_real, run_case_mock,
    score_deterministic, pass_fail, JUDGE_PROMPT_TEMPLATE,
    JUDGE_MODEL, PASS_THRESHOLD, REAL_CASE_PHASES,
)
from evals import release_gates
# The failure-category vocabulary the provenance ledger already allow-lists from
# `phase_failure_details`. Shared so the nightly and the ledger cannot drift into
# two different opinions of what the orchestrator is allowed to have recorded.
from evals.provenance import STRUCTURAL_FAILURE_KINDS
# The canonical, fence-tolerant parser the real-time harness already uses for
# judge output.  Reused rather than reimplemented: a second divergent JSON
# parser is exactly how ```json fences became a "0/12 quality failure".
from llm_client import parse_json
# Typed provider failure vocabulary.  Failures are classified from the category
# the runtime already assigned them, never from loose prose matching.
from runtime.provider_gateway import (
    PROVIDER_DETAIL_MARKER,
    normalize_error_type,
    normalize_exception_category,
)
from state import ProjectState

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("evals_batch")

NIGHTLY_SCHEMA_VERSION = "nightly_batch_observation.v1"
# The submit-time cache `--resume` reads back. Versioned because the resume path
# now depends on a field the pre-wave layout (a bare list) does not carry.
BATCH_INPUTS_SCHEMA_VERSION = "nightly_batch_inputs.v1"

# ═══ Execution modes ═══
#
# What actually produced this run's judge scores. Recorded explicitly in the
# artifact rather than inferred from judge prose, so `mode` is the machine-
# readable discriminator between a real observation and a synthetic smoke.

EXECUTION_MODE_BATCH = "batch"
EXECUTION_MODE_MOCK = "mock"

EXECUTION_MODES = (EXECUTION_MODE_BATCH, EXECUTION_MODE_MOCK)

# ═══ Per-case judge observation vocabulary ═══
#
# A judge observation is either a real measurement or it is not.  `score` is
# populated *only* under JUDGE_VALID and JUDGE_SYNTHETIC, and only the former is
# `valid`, so no consumer can mistake "the judge did not answer" for "the judge
# answered zero" — nor a fabricated score for a measured one.

JUDGE_VALID = "valid_judge_result"
JUDGE_SYNTHETIC = "synthetic_judge_result"
JUDGE_PROVIDER_OR_BATCH_FAILURE = "provider_or_batch_failure"
JUDGE_MALFORMED = "malformed_judge_response"
JUDGE_MISSING = "missing_judge_response"

JUDGE_STATUSES = (
    JUDGE_VALID,
    JUDGE_SYNTHETIC,
    JUDGE_PROVIDER_OR_BATCH_FAILURE,
    JUDGE_MALFORMED,
    JUDGE_MISSING,
)

# ═══ Per-case pipeline observation vocabulary ═══

PIPELINE_COMPLETE = "complete"
PIPELINE_PROVIDER_FAILURE = "provider_failure"
PIPELINE_HARNESS_FAILURE = "harness_failure"

PIPELINE_STATUSES = (
    PIPELINE_COMPLETE,
    PIPELINE_PROVIDER_FAILURE,
    PIPELINE_HARNESS_FAILURE,
)

# ═══ What the product actually records when a phase fails ═══
#
# `run_case_real` only reaches its own `except` branch — and therefore only
# writes `ingestion_metadata.eval_errors` — when a phase *raises*. An ordinary
# `LLMResponse(ok=False)` does not raise: `run_phase_node` sets
# `phase_status[phase] = "failed"`, records a typed
# `phase_failure_details[phase].category`, and returns the state normally. A
# nightly that consulted `eval_errors` alone therefore called a case whose every
# phase was provider-dead "complete", which is the one thing this module exists
# to prevent. Both signals are read now, and `phase_status` is the primary one.

PHASE_STATUS_COMPLETED = "completed"
PHASE_STATUS_FAILED = "failed"
# `PhaseStatus`'s own members. A value outside them is `unknown`, never echoed.
PHASE_STATUS_TOKENS = frozenset({"pending", "running", "completed", "failed", "stale"})

# Categories that mean "the provider could not answer", as opposed to the
# product answering badly. These are the two the runtime assigns from
# `_provider_failure_diagnostic`.
PROVIDER_PHASE_FAILURE_KINDS = frozenset({"provider_error", "quota_exceeded"})

# Every failure category the orchestrator is known to record. Allow-listed, not
# copied: an unrecognized category becomes a fixed token, so no unbounded
# `phase_failure_details` text can travel into the nightly artifact.
KNOWN_PHASE_FAILURE_KINDS = frozenset(STRUCTURAL_FAILURE_KINDS) | frozenset({
    "prerequisite_failed",
    "gate_structural",
    "persisted_strategy_contract",
}) | PROVIDER_PHASE_FAILURE_KINDS

UNKNOWN_PHASE_FAILURE_CATEGORY = "unknown_failure_category"
UNCATEGORIZED_PHASE_FAILURE = "uncategorized_phase_failure"
UNTYPED_PHASE_FAILURE = "untyped_phase_failure"
PHASE_STATE_CATEGORY_PREFIX = "phase_state_"

# ═══ Validity error codes ═══
#
# Every code below invalidates the whole nightly quality observation.  They are
# grouped by which *primary* classification they imply, and the groups are
# consulted in a fixed order (see `classify_result`).

VALIDITY_SOURCE_SHA_MISSING = "source_sha_missing"
VALIDITY_SOURCE_SHA_MALFORMED = "source_sha_malformed"
VALIDITY_SOURCE_SHA_MISMATCH = "source_sha_mismatch"
VALIDITY_HARNESS_FAILURE = "harness_failure"
VALIDITY_PIPELINE_HARNESS_FAILURE = "pipeline_harness_failure"

VALIDITY_PIPELINE_PROVIDER_FAILURE = "pipeline_provider_failure"
VALIDITY_JUDGE_PROVIDER_OR_BATCH_FAILURE = "judge_provider_or_batch_failure"

VALIDITY_EXPECTED_UNIVERSE_EMPTY = "expected_universe_empty"
VALIDITY_INCOMPLETE_CASE_SELECTION = "incomplete_case_selection"
VALIDITY_MISSING_CASE_IDS = "missing_case_ids"
VALIDITY_UNKNOWN_CASE_IDS = "unknown_case_ids"
VALIDITY_DUPLICATE_CASE_IDS = "duplicate_case_ids"
VALIDITY_JUDGE_RESULT_MISSING = "judge_result_missing"
VALIDITY_JUDGE_RESULT_MALFORMED = "judge_result_malformed"
VALIDITY_DENOMINATOR_MISMATCH = "denominator_mismatch"
VALIDITY_SYNTHETIC_JUDGE_SCORES = "synthetic_judge_scores"

# An observation that cannot be bound to a commit — or that is bound to the
# *wrong* commit — or whose harness itself broke, describes nothing about the
# product, not even a provider outage.  It is reported first.
ATTRIBUTION_AND_HARNESS_CODES = frozenset({
    VALIDITY_SOURCE_SHA_MISSING,
    VALIDITY_SOURCE_SHA_MALFORMED,
    VALIDITY_SOURCE_SHA_MISMATCH,
    VALIDITY_HARNESS_FAILURE,
    VALIDITY_PIPELINE_HARNESS_FAILURE,
})

# Provider inability outranks structural incompleteness because it *causes* it:
# when the provider refuses every request, the judge results are missing as a
# consequence, and reporting the consequence would hide the cause.  This is the
# bucket the 43 historical credit-balance nightlies belong in.
PROVIDER_CODES = frozenset({
    VALIDITY_PIPELINE_PROVIDER_FAILURE,
    VALIDITY_JUDGE_PROVIDER_OR_BATCH_FAILURE,
})

STRUCTURAL_CODES = frozenset({
    VALIDITY_EXPECTED_UNIVERSE_EMPTY,
    VALIDITY_INCOMPLETE_CASE_SELECTION,
    VALIDITY_MISSING_CASE_IDS,
    VALIDITY_UNKNOWN_CASE_IDS,
    VALIDITY_DUPLICATE_CASE_IDS,
    VALIDITY_JUDGE_RESULT_MISSING,
    VALIDITY_JUDGE_RESULT_MALFORMED,
    VALIDITY_DENOMINATOR_MISMATCH,
    # A synthetic run is structurally ineligible as release evidence: nothing is
    # broken, there is simply nothing measured to report.
    VALIDITY_SYNTHETIC_JUDGE_SCORES,
})

VALIDITY_CODES = ATTRIBUTION_AND_HARNESS_CODES | PROVIDER_CODES | STRUCTURAL_CODES

# `run_case_real` records this when the product deliberately halts after a
# Confused classification.  That is a measured product outcome, not a failure,
# so it must not invalidate the nightly.
EVAL_ERROR_CONFUSED_HALT = "workflow halted after Confused classification"

# Judge rationale is model prose. It is bounded and whitespace-collapsed before
# it reaches an artifact; raw provider payloads never are recorded at all.
JUDGE_RATIONALE_MAX_CHARS = 400

# Anthropic's closed batch result vocabulary. Anything else is recorded as
# unknown rather than echoed.
BATCH_RESULT_TYPES = ("succeeded", "errored", "canceled", "expired")

# Exception modules whose errors are provider/transport facts rather than
# harness bugs. `normalize_exception_category` is only consulted for these, so a
# plain harness `ValueError` cannot be laundered into `unknown_provider_error`.
PROVIDER_EXCEPTION_MODULES = ("anthropic", "openai", "httpx", "httpcore")

_SHA_RE = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")
# The runtime's safe provider message is `... category=<token>, provider=...`.
# Only the text *before* the provider-detail marker is scanned, so provider
# prose can never supply a category or reach the artifact.
_CATEGORY_RE = re.compile(r"\bcategory=([a-z_]+)")


# ═══════════════════════ source attribution ═══════════════════════

SOURCE_SHA_ORIGIN_RESUME_CACHE = "resume_cache"


def normalize_exact_sha(value: object) -> str:
    """An exact lowercase commit SHA, or `""` for anything else.

    Deliberately strict and shared by every attribution path: a ref name, a
    short SHA, a list, `None` and `"HEAD"` all normalize to `""` and therefore
    all fail the observation closed rather than half-identifying it.
    """

    if not isinstance(value, str):
        return ""
    candidate = value.strip().lower()
    return candidate if _SHA_RE.fullmatch(candidate) else ""


def resolve_source_sha(environ: dict | None = None, repo_root: Path | None = None) -> tuple[str, str]:
    """The exact commit this observation describes, and where it came from.

    GitHub's own commit identity is preferred; `git rev-parse HEAD` is the
    deterministic local fallback for provider-free runs. A mutable branch name
    is never accepted, so a summary can never claim a ref where a commit is
    required. Returns `("", "unavailable")` when no exact SHA can be
    established, which fails the observation closed.
    """

    source = os.environ if environ is None else environ
    candidate = normalize_exact_sha(source.get("GITHUB_SHA", ""))
    if candidate:
        return candidate, "github_sha"

    root = Path(__file__).resolve().parent.parent if repo_root is None else Path(repo_root)
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except Exception:  # noqa: BLE001 - attribution never breaks the harness
        return "", "unavailable"
    resolved = normalize_exact_sha(completed.stdout)
    if completed.returncode == 0 and resolved:
        return resolved, "git_rev_parse"
    return "", "unavailable"


def build_batch_inputs(
    records: list["PipelineRecord"], *, source_sha: str, source_sha_origin: str
) -> dict:
    """The submit-time cache `--resume` will score later.

    The commit is stored *with* the outputs on purpose. These outputs were
    produced by the pipeline at this exact commit; a later resume runs from a
    checkout that may be anything at all, and without this field it had no way
    to know that and simply stamped its own SHA on someone else's results.
    """

    return {
        "schema_version": BATCH_INPUTS_SCHEMA_VERSION,
        "source_sha": source_sha,
        "source_sha_origin": source_sha_origin,
        "cases": [{"case": record.case, "output": record.output} for record in records],
    }


def read_cached_cases(payload: object) -> list[dict]:
    """The `{case, output}` entries of a submit-time cache, in either layout.

    Pre-wave caches are a bare list. They are still *readable* — the operator
    gets full per-case diagnostics — but they carry no commit, so
    `resolve_resume_source_sha` fails them closed regardless.
    """

    entries = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        return []
    return [
        entry
        for entry in entries
        if isinstance(entry, dict) and "case" in entry and "output" in entry
    ]


def resolve_resume_source_sha(
    payload: object, checkout_sha: object
) -> tuple[str, str, dict | None]:
    """Bind a resumed collection to the commit its outputs were produced at.

    Returns `(source_sha, origin, validity_error_or_None)`. The resuming
    checkout's SHA is never substituted for a missing or disagreeing cached one:
    the whole point is that the artifact must not claim commit B for outputs
    created at commit A. Every failure path here lands in
    `ATTRIBUTION_AND_HARNESS_CODES`, so a resume that cannot be attributed can
    reach neither `pass` nor `quality_failure`.

    Diagnostics stay closed-vocabulary. The only variable text ever emitted is a
    pair of already-validated hex SHAs, so no cache content, provider prose or
    credential can travel through this field.
    """

    raw = payload.get("source_sha") if isinstance(payload, dict) else None
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return "", "unavailable", _validity_error(
            VALIDITY_SOURCE_SHA_MISSING, detail="resume_cache_without_source_sha"
        )

    cached = normalize_exact_sha(raw)
    if not cached:
        # Present but not an exact commit. The value itself is never echoed: it
        # is untrusted on-disk content by the time we are reading it back.
        return "", "unavailable", _validity_error(
            VALIDITY_SOURCE_SHA_MALFORMED, detail="resume_cache_source_sha_malformed"
        )

    current = normalize_exact_sha(checkout_sha)
    if not current:
        # Nothing to compare against, so the binding cannot be proven. The
        # cached commit is still the truthful attribution for these outputs.
        return cached, SOURCE_SHA_ORIGIN_RESUME_CACHE, _validity_error(
            VALIDITY_SOURCE_SHA_MISSING, detail="resume_checkout_sha_unavailable"
        )

    if cached != current:
        return cached, SOURCE_SHA_ORIGIN_RESUME_CACHE, _validity_error(
            VALIDITY_SOURCE_SHA_MISMATCH,
            detail=f"submitted_at={cached} resumed_at={current}",
        )

    return cached, SOURCE_SHA_ORIGIN_RESUME_CACHE, None


# ═══════════════════════ failure classification ═══════════════════════


def classify_exception(exc: BaseException) -> tuple[str, str]:
    """Bucket a raised exception as provider inability or harness failure.

    Only exceptions that actually came from a provider transport are given a
    typed provider category. Everything else is a harness failure carrying its
    exception *type name* — never its message, which may quote provider text.
    """

    module = (getattr(type(exc), "__module__", "") or "").split(".", 1)[0]
    from_transport = (
        module in PROVIDER_EXCEPTION_MODULES
        or getattr(exc, "status_code", None) is not None
        # Deliberately not `OSError` at large: a `FileNotFoundError` raised by
        # harness code is a harness bug, not a provider outage.
        or isinstance(exc, (TimeoutError, ConnectionError))
    )
    if from_transport:
        return PIPELINE_PROVIDER_FAILURE, normalize_exception_category(exc)
    return PIPELINE_HARNESS_FAILURE, type(exc).__name__


def typed_provider_category(message: object) -> str:
    """The runtime's own category for a phase failure, or `""` if untyped."""

    head = str(message or "").split(PROVIDER_DETAIL_MARKER, 1)[0]
    match = _CATEGORY_RE.search(head)
    if not match:
        return ""
    return normalize_error_type(match.group(1))


def batch_result_detail(result_type: object) -> str:
    candidate = str(result_type or "").strip().lower()
    if candidate in BATCH_RESULT_TYPES:
        return f"batch_result_{candidate}"
    return "batch_result_unknown"


# ═══════════════════════ judge observations ═══════════════════════


@dataclass(frozen=True)
class JudgeObservation:
    """What the judge actually returned for one case.

    `score` is `None` for every status other than JUDGE_VALID. There is no
    "default zero": a score exists only when one was genuinely parsed.
    """

    status: str
    score: int | None = None
    rationale: str = ""
    detail: str = ""

    @property
    def valid(self) -> bool:
        return self.status == JUDGE_VALID

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "score": self.score,
            "rationale": self.rationale,
            "detail": self.detail,
        }


def _sanitize_rationale(value: object) -> str:
    return " ".join(str(value or "").split())[:JUDGE_RATIONALE_MAX_CHARS]


def parse_judge_payload(text: object) -> JudgeObservation:
    """Turn one judge response into a measurement or a typed malformation.

    Delegates the fence/prose tolerance to the canonical `parse_json`, so
    ```json-fenced output is read here exactly as the real-time harness reads
    it. Diagnostics are fixed codes: the offending payload is never echoed,
    because this artifact is produced by a job holding a provider secret.
    """

    data = parse_json(text) if isinstance(text, str) else None
    if not isinstance(data, dict):
        return JudgeObservation(JUDGE_MALFORMED, detail="judge_response_not_a_json_object")
    if "score" not in data:
        return JudgeObservation(JUDGE_MALFORMED, detail="judge_response_missing_score")

    raw = data["score"]
    # `bool` is an `int` in Python; `True` is not a rubric score.
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return JudgeObservation(JUDGE_MALFORMED, detail="judge_score_not_a_number")
    score = int(raw)
    if not 0 <= score <= 100:
        return JudgeObservation(JUDGE_MALFORMED, detail="judge_score_out_of_range")

    # A genuinely parsed 0 is a legitimate measured score and stays one.
    return JudgeObservation(
        JUDGE_VALID, score=score, rationale=_sanitize_rationale(data.get("rationale", ""))
    )


# ═══════════════════════ pipeline observations ═══════════════════════


@dataclass
class PipelineRecord:
    """One case's trip through the product pipeline, and whether it completed."""

    case: dict
    output: dict
    status: str
    category: str = ""

    @property
    def case_id(self) -> str:
        return str(self.case.get("id", ""))

    @property
    def complete(self) -> bool:
        return self.status == PIPELINE_COMPLETE


def _eval_errors(output: dict) -> list[str]:
    """The eval harness's own recorded exception strings, if any."""

    metadata = output.get("ingestion_metadata")
    if not isinstance(metadata, dict):
        return []
    recorded = metadata.get("eval_errors")
    if not isinstance(recorded, list):
        return []
    return [str(entry) for entry in recorded]


def _confused_halt_recorded(output: dict) -> bool:
    return any(EVAL_ERROR_CONFUSED_HALT in entry for entry in _eval_errors(output))


def _raised_phase_failures(output: dict) -> tuple[dict[str, str], list[str]]:
    """Split recorded eval-harness exceptions by the phase they were raised in.

    `run_case_real` records them as ``f"{phase}: {exc}"``, so the phase comes
    from this harness's own closed list and never from the message. Anything
    that is neither the Confused-halt marker nor a known phase prefix is kept
    aside rather than dropped: an entry we cannot attribute is still evidence
    that something went wrong.
    """

    by_phase: dict[str, str] = {}
    unattributed: list[str] = []
    for entry in _eval_errors(output):
        if EVAL_ERROR_CONFUSED_HALT in entry:
            continue
        for phase in REAL_CASE_PHASES:
            if entry.startswith(f"{phase}: "):
                by_phase.setdefault(phase, entry)
                break
        else:
            unattributed.append(entry)
    return by_phase, unattributed


def _phase_status_token(output: dict, phase: str) -> str:
    """One phase's recorded status, bounded to `PhaseStatus`'s own members."""

    statuses = output.get("phase_status")
    if not isinstance(statuses, dict):
        return "unreadable"
    if phase not in statuses:
        return "missing"
    raw = statuses[phase]
    token = str(getattr(raw, "value", raw) or "").strip().lower()
    return token if token in PHASE_STATUS_TOKENS else "unknown"


def recorded_failure_category(output: dict, phase: str) -> str:
    """The runtime's own category for a failed phase, allow-listed.

    Returns `""` when the phase failed with no diagnostic at all. A category the
    allow-list does not know becomes `UNKNOWN_PHASE_FAILURE_CATEGORY`, so a
    corrupt or hostile `phase_failure_details` entry can neither claim a
    provider outage nor put its own text in the artifact.
    """

    details = output.get("phase_failure_details")
    if not isinstance(details, dict):
        return ""
    entry = details.get(phase)
    if not isinstance(entry, dict):
        return ""
    raw = entry.get("category")
    if not isinstance(raw, str):
        return UNKNOWN_PHASE_FAILURE_CATEGORY if raw is not None else ""
    category = raw.strip().lower()
    if not category:
        return ""
    return category if category in KNOWN_PHASE_FAILURE_KINDS else UNKNOWN_PHASE_FAILURE_CATEGORY


def expected_real_phases(output: dict) -> tuple[str, ...]:
    """The phases this case was genuinely expected to run.

    Normally the whole canonical sequence. After a Confused classification the
    product deliberately stops and `run_case_real` breaks out of the loop, so
    the later phases were never meant to run: demanding that they completed
    would turn a measured product outcome into a pipeline failure.
    """

    if _confused_halt_recorded(output):
        return REAL_CASE_PHASES[:1]
    return REAL_CASE_PHASES


def _raised_failure_record(case: dict, output: dict, message: str) -> PipelineRecord:
    category = typed_provider_category(message)
    if category:
        return PipelineRecord(case, output, PIPELINE_PROVIDER_FAILURE, category)
    # Fail closed into the nearest truthful non-quality classification. The
    # message itself is never carried: it is a raw `str(exc)`.
    return PipelineRecord(case, output, PIPELINE_HARNESS_FAILURE, UNTYPED_PHASE_FAILURE)


def _recorded_failure_record(case: dict, output: dict, phase: str) -> PipelineRecord:
    category = recorded_failure_category(output, phase)
    if not category:
        # FAILED with no diagnostic at all. Non-complete either way, and
        # attributed to the harness because nothing recorded says a provider
        # was involved — a false provider attribution is its own untruth.
        return PipelineRecord(case, output, PIPELINE_HARNESS_FAILURE, UNCATEGORIZED_PHASE_FAILURE)
    if category in PROVIDER_PHASE_FAILURE_KINDS:
        return PipelineRecord(case, output, PIPELINE_PROVIDER_FAILURE, category)
    return PipelineRecord(case, output, PIPELINE_HARNESS_FAILURE, category)


def pipeline_record_for_output(case: dict, output: dict) -> PipelineRecord:
    """Classify a returned `run_case_real` state, from the state it recorded.

    Two independent signals say a phase did not do its job, and both are read:

    * `phase_status` / `phase_failure_details` — what the *orchestrator* records
      when a phase fails without raising. This is the ordinary provider-failure
      path: `LLMResponse(ok=False)` never raises, so it never reaches
      `eval_errors`, and reading `eval_errors` alone classified an entirely
      provider-dead case as `complete`.
    * `ingestion_metadata.eval_errors` — what *this harness* records when a
      phase raises. Still honoured, unchanged.

    The canonical phase order is walked and the first phase that did not
    demonstrably complete decides the classification, so the root cause is what
    gets reported. That matters most in the shape the product actually produces:
    a provider outage fails `classify`, the circuit breaker then opens and the
    remaining phases fail `policy_blocked`. Naming the cascade would hide the
    outage that caused it.

    Completion is proven, never assumed: a phase that is neither `completed` nor
    `failed` — pending, running, stale, absent, unreadable — is a case that
    cannot be shown to have run, and it fails closed.
    """

    if not isinstance(output, dict):
        # Reachable only from a corrupted `--resume` cache. Classified rather
        # than crashed: a harness that dies has told the operator nothing.
        return PipelineRecord(case, {}, PIPELINE_HARNESS_FAILURE, "unreadable_case_output")

    raised, unattributed = _raised_phase_failures(output)
    expected = expected_real_phases(output)

    for phase in expected:
        if phase in raised:
            return _raised_failure_record(case, output, raised[phase])
        status = _phase_status_token(output, phase)
        if status == PHASE_STATUS_COMPLETED:
            continue
        if status == PHASE_STATUS_FAILED:
            return _recorded_failure_record(case, output, phase)
        return PipelineRecord(
            case, output, PIPELINE_HARNESS_FAILURE, f"{PHASE_STATE_CATEGORY_PREFIX}{status}"
        )

    # A recorded exception outside the expected set — an unattributable entry,
    # or one for a phase the Confused halt skipped — still invalidates the case.
    leftover = [raised[phase] for phase in raised if phase not in expected]
    leftover.extend(unattributed)
    if leftover:
        return _raised_failure_record(case, output, leftover[0])

    return PipelineRecord(case, output, PIPELINE_COMPLETE)


# ═══════════════════════ the nightly validity contract ═══════════════════════


@dataclass
class NightlyObservation:
    """The full machine-readable answer to "may this nightly count?"."""

    source_sha: str
    source_sha_origin: str
    expected_case_ids: list[str]
    observed_case_ids: list[str]
    golden_case_ids: list[str]
    result_class: str
    valid_observation: bool
    quality_measured: bool
    validity_errors: list[dict]
    cases: list[dict]
    passed: int
    total: int
    pass_rate: float
    threshold: float
    execution_mode: str = EXECUTION_MODE_BATCH
    # The synthetic harness's own verdict, and *only* that: did `--mock` run
    # every case through the plumbing and would its fabricated scores have
    # passed? `None` outside mock mode. Kept in its own field precisely so it
    # can never be confused with `ok`, which answers a different question.
    synthetic_smoke_ok: bool | None = None


def _validity_error(code: str, *, case_ids=(), detail: str = "") -> dict:
    return {"code": code, "case_ids": list(case_ids), "detail": detail}


def classify_result(validity_errors: list[dict], *, pass_rate: float, threshold: float) -> str:
    """Fold the validity evidence into exactly one closed-vocabulary result.

    The order is deliberate and mirrors `release_gates.evaluate_gate_outcome`:
    the earliest unmet precondition wins, so a run that never obtained a
    measurement can never be reported as a quality failure.
    """

    codes = {str(entry.get("code", "")) for entry in validity_errors}
    if codes & ATTRIBUTION_AND_HARNESS_CODES:
        return release_gates.RESULT_INFRASTRUCTURE_FAILURE
    if codes & PROVIDER_CODES:
        return release_gates.RESULT_PROVIDER_UNAVAILABLE
    if codes:
        return release_gates.RESULT_STRUCTURAL_FAILURE
    if pass_rate < threshold:
        return release_gates.RESULT_QUALITY_FAILURE
    return release_gates.RESULT_PASS


def build_observation(
    *,
    pipeline_records: list[PipelineRecord],
    judge_observations: dict[str, JudgeObservation],
    expected_case_ids: list[str],
    golden_case_ids: list[str],
    threshold: float,
    source_sha: str,
    source_sha_origin: str,
    harness_errors: list[dict] | None = None,
    execution_mode: str = EXECUTION_MODE_BATCH,
) -> NightlyObservation:
    """Assemble the nightly observation and decide whether it may count.

    The denominator is the *expected* universe, always. A case that never
    reached a measurement leaves the denominator alone and invalidates the
    observation instead of shrinking it.

    Only a genuinely measured judge score reaches the numerator, so a synthetic
    run contributes nothing to `passed`/`pass_rate` no matter how its fabricated
    scores would have scored.
    """

    validity_errors: list[dict] = list(harness_errors or [])

    # An unrecognized mode is a harness bug, not a licence to guess. It is
    # reported as the most conservative label *and* invalidates the run.
    mode = execution_mode
    if mode not in EXECUTION_MODES:
        mode = EXECUTION_MODE_BATCH
        validity_errors.append(
            _validity_error(VALIDITY_HARNESS_FAILURE, detail="unknown_execution_mode")
        )

    if not source_sha:
        validity_errors.append(
            _validity_error(VALIDITY_SOURCE_SHA_MISSING, detail=source_sha_origin or "unavailable")
        )

    expected = list(expected_case_ids)
    expected_set = set(expected)
    if not expected:
        validity_errors.append(_validity_error(VALIDITY_EXPECTED_UNIVERSE_EMPTY))
    elif expected_set != set(golden_case_ids):
        validity_errors.append(
            _validity_error(
                VALIDITY_INCOMPLETE_CASE_SELECTION,
                case_ids=sorted(expected_set.symmetric_difference(golden_case_ids)),
                detail="expected universe is not the complete golden-case universe",
            )
        )

    observed: list[str] = []
    duplicates: list[str] = []
    for record in pipeline_records:
        case_id = record.case_id
        if case_id in observed:
            duplicates.append(case_id)
            continue
        observed.append(case_id)

    if duplicates:
        validity_errors.append(
            _validity_error(VALIDITY_DUPLICATE_CASE_IDS, case_ids=sorted(set(duplicates)))
        )

    observed_set = set(observed)
    missing = [case_id for case_id in expected if case_id not in observed_set]
    unknown = sorted(observed_set - expected_set)
    if missing:
        validity_errors.append(_validity_error(VALIDITY_MISSING_CASE_IDS, case_ids=missing))
    if unknown:
        validity_errors.append(_validity_error(VALIDITY_UNKNOWN_CASE_IDS, case_ids=unknown))

    stray_judges = sorted(set(judge_observations) - expected_set)
    if stray_judges:
        validity_errors.append(
            _validity_error(
                VALIDITY_UNKNOWN_CASE_IDS,
                case_ids=stray_judges,
                detail="judge result for a case outside the expected universe",
            )
        )

    provider_pipeline: list[str] = []
    harness_pipeline: list[str] = []
    judge_provider: list[str] = []
    judge_malformed: list[str] = []
    judge_missing: list[str] = []
    judge_synthetic: list[str] = []

    cases: list[dict] = []
    seen: set[str] = set()
    for record in pipeline_records:
        case_id = record.case_id
        if case_id in seen:
            continue
        seen.add(case_id)

        judge = judge_observations.get(
            case_id, JudgeObservation(JUDGE_MISSING, detail="no judge result for this case")
        )
        if case_id in expected_set:
            if record.status == PIPELINE_PROVIDER_FAILURE:
                provider_pipeline.append(case_id)
            elif record.status == PIPELINE_HARNESS_FAILURE:
                harness_pipeline.append(case_id)
            if judge.status == JUDGE_PROVIDER_OR_BATCH_FAILURE:
                judge_provider.append(case_id)
            elif judge.status == JUDGE_MALFORMED:
                judge_malformed.append(case_id)
            elif judge.status == JUDGE_MISSING:
                judge_missing.append(case_id)
            elif judge.status == JUDGE_SYNTHETIC:
                judge_synthetic.append(case_id)

        # Quality is measured only when the case completed the pipeline *and*
        # a real judge score came back for it. Anything else keeps `judge_overall`
        # and `passed` null rather than defaulting them to zero/False.
        measured = record.complete and judge.valid
        result = score_deterministic(record.case, record.output)
        if measured:
            result.judge_overall = judge.score
            result.judge_rationale = judge.rationale
            result.passed = pass_fail(result)

        # The synthetic smoke verdict is scored on a throwaway copy so a
        # fabricated score can never touch `result`, and it is reported under
        # its own key so it can never be read as a measured pass.
        synthetic_passed = None
        if record.complete and judge.status == JUDGE_SYNTHETIC:
            probe = score_deterministic(record.case, record.output)
            probe.judge_overall = judge.score or 0
            synthetic_passed = pass_fail(probe)

        serialized = asdict(result)
        serialized["judge_overall"] = judge.score if measured else None
        serialized["judge_rationale"] = judge.rationale if measured else ""
        serialized["passed"] = result.passed if measured else None
        serialized["quality_measured"] = measured
        serialized["pipeline_status"] = record.status
        serialized["pipeline_detail"] = record.category
        serialized["judge_status"] = judge.status
        serialized["judge_detail"] = judge.detail
        # The raw observed score, whatever produced it. It is `None` for every
        # status that never yielded one, and `judge_status` — never this field —
        # says whether it was measured or fabricated.
        serialized["judge_score"] = judge.score
        serialized["synthetic_passed"] = synthetic_passed
        cases.append(serialized)

    if provider_pipeline:
        validity_errors.append(
            _validity_error(VALIDITY_PIPELINE_PROVIDER_FAILURE, case_ids=sorted(provider_pipeline))
        )
    if harness_pipeline:
        validity_errors.append(
            _validity_error(VALIDITY_PIPELINE_HARNESS_FAILURE, case_ids=sorted(harness_pipeline))
        )
    if judge_provider:
        validity_errors.append(
            _validity_error(
                VALIDITY_JUDGE_PROVIDER_OR_BATCH_FAILURE, case_ids=sorted(judge_provider)
            )
        )
    if judge_malformed:
        validity_errors.append(
            _validity_error(VALIDITY_JUDGE_RESULT_MALFORMED, case_ids=sorted(judge_malformed))
        )
    if judge_missing:
        validity_errors.append(
            _validity_error(VALIDITY_JUDGE_RESULT_MISSING, case_ids=sorted(judge_missing))
        )

    # Asserted from two independent directions: the declared mode, and the judge
    # statuses actually present. Either one alone disqualifies the run, so a
    # synthetic score smuggled into a run labelled `batch` still fails closed,
    # and a mock run that produced no judge results at all is still not evidence.
    if mode == EXECUTION_MODE_MOCK or judge_synthetic:
        validity_errors.append(
            _validity_error(
                VALIDITY_SYNTHETIC_JUDGE_SCORES,
                case_ids=sorted(judge_synthetic),
                detail="judge scores were fabricated by the harness, not measured",
            )
        )

    measured_ids = {entry["case_id"] for entry in cases if entry["quality_measured"]}
    # Backstop, asserted rather than assumed: a pass rate is only ever a valid
    # claim when the measured set is exactly the expected set. Every known way
    # to break that already appended a more specific code above; this catches
    # any future path that does not, so the denominator can never shrink
    # silently.
    if measured_ids != expected_set and not validity_errors:
        validity_errors.append(
            _validity_error(
                VALIDITY_DENOMINATOR_MISMATCH,
                case_ids=sorted(expected_set.symmetric_difference(measured_ids)),
            )
        )

    # Only expected cases may reach the numerator. A contaminating case is
    # already a validity error; letting it also add to `passed` would print a
    # pass rate above 1.0 over the expected denominator.
    passed = sum(
        1 for entry in cases if entry["passed"] is True and entry["case_id"] in expected_set
    )
    total = len(expected) if expected else len(cases)
    pass_rate = passed / total if total else 0.0

    valid_observation = not validity_errors
    result_class = classify_result(validity_errors, pass_rate=pass_rate, threshold=threshold)

    # "Did the synthetic harness work?" — computed only in mock mode, from the
    # synthetic column alone, and never folded into the evidence fields above.
    synthetic_smoke_ok = None
    if mode == EXECUTION_MODE_MOCK:
        smoke_codes = {str(entry.get("code", "")) for entry in validity_errors}
        synthetic_smoke_ok = (
            smoke_codes == {VALIDITY_SYNTHETIC_JUDGE_SCORES}
            and bool(expected)
            and all(entry["synthetic_passed"] is True for entry in cases)
            and {entry["case_id"] for entry in cases} == expected_set
        )

    return NightlyObservation(
        source_sha=source_sha,
        source_sha_origin=source_sha_origin,
        expected_case_ids=expected,
        observed_case_ids=observed,
        golden_case_ids=list(golden_case_ids),
        result_class=result_class,
        valid_observation=valid_observation,
        quality_measured=valid_observation,
        validity_errors=validity_errors,
        cases=cases,
        passed=passed,
        total=total,
        pass_rate=pass_rate,
        threshold=threshold,
        execution_mode=mode,
        synthetic_smoke_ok=synthetic_smoke_ok,
    )


# ═══════════════════════ batch plumbing ═══════════════════════


def build_batch_requests(records: list[PipelineRecord]) -> list[dict]:
    """Build the message batch payload — one request per case."""
    requests = []
    for record in records:
        case, output = record.case, record.output
        prompt = JUDGE_PROMPT_TEMPLATE.format(
            brief=case["brief"],
            expected_domain=case["expected_domain"],
            min_h=case["min_hypotheses"],
            max_h=case["max_hypotheses"],
            required_fw=", ".join(case.get("must_contain_frameworks", [])),
            must_mention=", ".join(case.get("strategy_must_mention", [])),
            must_not_mention=", ".join(case.get("strategy_must_not_mention", [])),
            data_based=case.get("data_based_expected", False),
            output=json.dumps(output, default=str)[:8000],
        )
        requests.append({
            "custom_id": case["id"],
            "params": {
                "model": JUDGE_MODEL,
                "max_tokens": 1024,
                "system": "You are a harsh but fair evaluator. Return only JSON.",
                "messages": [{"role": "user", "content": prompt}],
            },
        })
    return requests


async def submit_batch(requests: list[dict]) -> str:
    """Submit a Message Batch and return its ID."""
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    batch = await client.messages.batches.create(requests=requests)
    logger.info(f"Submitted batch {batch.id} with {len(requests)} requests")
    return batch.id


async def wait_for_batch(batch_id: str, poll_interval: int = 30, max_wait: int = 86400) -> dict:
    """Poll a batch until ended. Returns the final batch object."""
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    waited = 0
    while waited < max_wait:
        batch = await client.messages.batches.retrieve(batch_id)
        status = batch.processing_status
        counts = batch.request_counts
        logger.info(
            f"Batch {batch_id}: {status} "
            f"(succeeded={counts.succeeded}, errored={counts.errored}, "
            f"processing={counts.processing}, canceled={counts.canceled}, expired={counts.expired})"
        )
        if status == "ended":
            return batch
        await asyncio.sleep(poll_interval)
        waited += poll_interval
    raise TimeoutError(f"Batch {batch_id} did not finish within {max_wait}s")


async def collect_batch_results(batch_id: str) -> dict[str, JudgeObservation]:
    """Stream results out of a finished batch, classifying each entry.

    A finished batch does not mean every requested observation succeeded, so
    every entry is classified: errored/canceled/expired become provider-or-batch
    failures, unreadable or unparseable content becomes a malformed response, and
    neither becomes a zero.
    """
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    out: dict[str, JudgeObservation] = {}
    async for entry in await client.messages.batches.results(batch_id):
        cid = entry.custom_id
        result_type = getattr(entry.result, "type", "")
        if result_type != "succeeded":
            out[cid] = JudgeObservation(
                JUDGE_PROVIDER_OR_BATCH_FAILURE, detail=batch_result_detail(result_type)
            )
            continue
        try:
            text = entry.result.message.content[0].text
        except Exception:  # noqa: BLE001 - a shape we cannot read is malformed, not zero
            out[cid] = JudgeObservation(
                JUDGE_MALFORMED, detail="judge_response_shape_unreadable"
            )
            continue
        out[cid] = parse_judge_payload(text)
    return out


async def run_pipeline_for_all_cases(cases: list[dict], mock: bool) -> list[PipelineRecord]:
    """Run every case through the real (or mock) phase pipeline.

    An exception is recorded as a typed pipeline failure with an empty output.
    It is deliberately no longer serialized as `{"error": <str(exc)>}`: that put
    raw provider text into the next provider prompt and into the artifact, and
    it let a crashed case keep flowing through quality scoring.
    """
    records: list[PipelineRecord] = []
    for case in cases:
        logger.info(f"  pipeline [{case['id']}] {case['brief'][:60]}...")
        try:
            if mock:
                output = await run_case_mock(case)
                records.append(PipelineRecord(case, output, PIPELINE_COMPLETE))
            else:
                state = await run_case_real(case)
                records.append(pipeline_record_for_output(case, state.model_dump(mode="json")))
        except Exception as exc:  # noqa: BLE001 - classified, never quality-scored
            status, category = classify_exception(exc)
            logger.error(f"  ERROR on {case['id']}: {status} ({category})")
            records.append(PipelineRecord(case, {}, status, category))
    return records


def write_report(observation: NightlyObservation, out_dir: Path, batch_id: str | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema_version": NIGHTLY_SCHEMA_VERSION,
        "timestamp": datetime.now().isoformat(),
        # What produced the judge scores. `batch` is a real provider batch;
        # `mock` is synthetic and can never be a valid observation.
        "mode": observation.execution_mode,
        "batch_id": batch_id,
        # ── attribution ──
        "source_sha": observation.source_sha,
        "source_sha_origin": observation.source_sha_origin,
        # ── validity contract ──
        "valid_observation": observation.valid_observation,
        "result_class": observation.result_class,
        "validity_errors": observation.validity_errors,
        "quality_measured": observation.quality_measured,
        "expected_case_ids": observation.expected_case_ids,
        "observed_case_ids": observation.observed_case_ids,
        "golden_case_ids": observation.golden_case_ids,
        # ── counters (denominator is always the expected universe) ──
        "passed": observation.passed,
        "total": observation.total,
        "pass_rate": observation.pass_rate,
        "threshold": observation.threshold,
        # `ok` now means "a valid observation that measured a pass", so it can
        # never be true for a run that did not measure quality at all — a mock
        # run included, however well its fabricated scores did.
        "ok": observation.valid_observation and observation.result_class == release_gates.RESULT_PASS,
        # Operational success of the synthetic harness, `None` outside mock mode.
        # Deliberately not part of the evidence contract.
        "synthetic_smoke_ok": observation.synthetic_smoke_ok,
        "cases": observation.cases,
    }
    (out_dir / "summary_batch.json").write_text(json.dumps(summary, indent=2, default=str))
    logger.info(f"Wrote {out_dir}/summary_batch.json")
    return summary


def _batch_stage_error(stage: str, exc: BaseException, case_ids: list[str]) -> dict:
    """One typed validity error for a failed submit/wait/collect stage."""

    status, category = classify_exception(exc)
    code = (
        VALIDITY_JUDGE_PROVIDER_OR_BATCH_FAILURE
        if status == PIPELINE_PROVIDER_FAILURE
        else VALIDITY_HARNESS_FAILURE
    )
    return _validity_error(code, case_ids=list(case_ids), detail=f"batch_{stage}:{category}")


def _print_outcome(summary: dict) -> None:
    print(
        f"\n=== BATCH RESULT: {summary['passed']}/{summary['total']} "
        f"({summary['pass_rate']:.1%}) ==="
    )
    print(f"mode:              {summary['mode']}")
    print(f"source_sha:        {summary['source_sha'] or '<unavailable>'}")
    print(f"valid_observation: {summary['valid_observation']}")
    print(f"result_class:      {summary['result_class']}")
    for entry in summary["validity_errors"]:
        cases = ", ".join(entry["case_ids"])
        print(f"VALIDITY ERROR: {entry['code']}" + (f" [{cases}]" if cases else ""))
    if summary["mode"] == EXECUTION_MODE_MOCK:
        print(f"synthetic_smoke_ok: {summary['synthetic_smoke_ok']}")
        print(
            "MOCK RUN: judge scores are fabricated. This artifact is a smoke "
            "check and is NOT release-quality evidence."
        )
    elif not summary["valid_observation"]:
        print(
            "INVALID OBSERVATION: this run did not measure product quality and is "
            "NOT evidence of a quality regression."
        )


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", help="Comma-separated case IDs (default: all)")
    parser.add_argument("--mock", action="store_true", help="Skip real LLM calls")
    parser.add_argument("--report", default="evals/out/", help="Output directory")
    parser.add_argument("--submit-only", action="store_true",
                        help="Submit batch and exit (use --resume to collect)")
    parser.add_argument("--resume", help="Collect results from an existing batch ID")
    parser.add_argument("--poll-interval", type=int, default=30)
    args = parser.parse_args()

    out_dir = Path(args.report)
    golden_case_ids = [case["id"] for case in load_cases()]
    checkout_sha, checkout_sha_origin = resolve_source_sha()
    source_sha, source_sha_origin = checkout_sha, checkout_sha_origin
    execution_mode = EXECUTION_MODE_MOCK if args.mock else EXECUTION_MODE_BATCH
    harness_errors: list[dict] = []
    judge_observations: dict[str, JudgeObservation] = {}
    batch_id = args.resume

    # Resume path: skip pipeline + submission, just collect
    if args.resume:
        logger.info(f"Resuming batch {args.resume}")
        # Need the original outputs to score deterministically — load from cache file
        cache_file = out_dir / f"batch_inputs_{args.resume}.json"
        if not cache_file.exists():
            logger.error(f"No cached inputs for batch {args.resume} at {cache_file}")
            sys.exit(2)
        try:
            cached = json.loads(cache_file.read_text())
        except Exception:  # noqa: BLE001 - an unreadable cache is a harness failure
            cached = None
            harness_errors.append(
                _validity_error(VALIDITY_HARNESS_FAILURE, detail="resume_cache_unreadable")
            )
        records = [
            pipeline_record_for_output(entry["case"], entry["output"])
            for entry in read_cached_cases(cached)
        ]
        expected_case_ids = [record.case_id for record in records]

        # These outputs were produced at the *submitting* checkout, so that is
        # the commit this observation describes — never today's. A cache that
        # cannot prove the two are the same commit fails closed here, before a
        # single provider call is made: there is nothing to be learned by
        # waiting up to 24h for results that can never be valid evidence.
        source_sha, source_sha_origin, attribution_error = resolve_resume_source_sha(
            cached, checkout_sha
        )
        if attribution_error is not None:
            logger.error(
                "Resume attribution failed (%s); not collecting batch results",
                attribution_error["code"],
            )
            harness_errors.append(attribution_error)
        else:
            try:
                await wait_for_batch(args.resume, args.poll_interval)
                judge_observations = await collect_batch_results(args.resume)
            except Exception as exc:  # noqa: BLE001 - classified, never quality-scored
                harness_errors.append(_batch_stage_error("resume", exc, expected_case_ids))
    else:
        subset = set(args.cases.split(",")) if args.cases else None
        cases = load_cases(subset)
        expected_case_ids = [case["id"] for case in cases]
        logger.info(f"Pipeline pass: {len(cases)} cases ({'MOCK' if args.mock else 'REAL'})")

        records = await run_pipeline_for_all_cases(cases, args.mock)

        if args.mock:
            # Fabricated, and typed as such. `JUDGE_SYNTHETIC` is what makes the
            # whole run ineligible as evidence; the `mock` rationale is prose and
            # is never what any consumer decides on.
            judge_observations = {
                record.case_id: JudgeObservation(
                    JUDGE_SYNTHETIC,
                    score=(
                        70
                        if (record.output.get("classify") or {}).get("domain")
                        == record.case["expected_domain"]
                        else 40
                    ),
                    rationale="mock",
                    detail="synthetic_score_from_mock_harness",
                )
                for record in records
            }
        else:
            requests = build_batch_requests(records)
            try:
                batch_id = await submit_batch(requests)
            except Exception as exc:  # noqa: BLE001 - classified, never quality-scored
                harness_errors.append(_batch_stage_error("submit", exc, expected_case_ids))
                batch_id = None

            if batch_id is not None:
                # Cache inputs so --resume can score them later, *with* the
                # commit they were produced at.
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / f"batch_inputs_{batch_id}.json").write_text(json.dumps(
                    build_batch_inputs(
                        records,
                        source_sha=source_sha,
                        source_sha_origin=source_sha_origin,
                    ),
                    default=str,
                ))

                if args.submit_only:
                    logger.info(f"Submitted. Resume with: --resume {batch_id}")
                    return

                try:
                    await wait_for_batch(batch_id, args.poll_interval)
                    judge_observations = await collect_batch_results(batch_id)
                except Exception as exc:  # noqa: BLE001 - classified, never quality-scored
                    harness_errors.append(_batch_stage_error("collect", exc, expected_case_ids))
            elif args.submit_only:
                logger.error("Batch submission failed; nothing to resume")
                sys.exit(1)

    observation = build_observation(
        pipeline_records=records,
        judge_observations=judge_observations,
        expected_case_ids=expected_case_ids,
        golden_case_ids=golden_case_ids,
        threshold=PASS_THRESHOLD,
        source_sha=source_sha,
        source_sha_origin=source_sha_origin,
        harness_errors=harness_errors,
        execution_mode=execution_mode,
    )
    summary = write_report(observation, out_dir, batch_id=batch_id)
    _print_outcome(summary)

    # A mock run is a provider-free smoke check, and its exit status answers the
    # only question it can answer: did the synthetic harness run everything
    # cleanly? That stays independent of evidence eligibility, which the
    # artifact already reports as false.
    if summary["mode"] == EXECUTION_MODE_MOCK:
        if not summary["synthetic_smoke_ok"]:
            sys.exit(1)
        return

    # Truthful red: any non-pass result stays operationally red, including the
    # non-quality ones. Only a valid measured pass exits zero.
    if not summary["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
