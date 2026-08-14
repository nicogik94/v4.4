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
    JUDGE_MODEL, PASS_THRESHOLD,
)
from evals import release_gates
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

# ═══ Per-case judge observation vocabulary ═══
#
# A judge observation is either a real measurement or it is not.  `score` is
# populated *only* under JUDGE_VALID, so no consumer can mistake "the judge did
# not answer" for "the judge answered zero".

JUDGE_VALID = "valid_judge_result"
JUDGE_PROVIDER_OR_BATCH_FAILURE = "provider_or_batch_failure"
JUDGE_MALFORMED = "malformed_judge_response"
JUDGE_MISSING = "missing_judge_response"

JUDGE_STATUSES = (
    JUDGE_VALID,
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

# ═══ Validity error codes ═══
#
# Every code below invalidates the whole nightly quality observation.  They are
# grouped by which *primary* classification they imply, and the groups are
# consulted in a fixed order (see `classify_result`).

VALIDITY_SOURCE_SHA_MISSING = "source_sha_missing"
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

# An observation that cannot be bound to a commit, or whose harness itself
# broke, describes nothing — not even a provider outage.  It is reported first.
ATTRIBUTION_AND_HARNESS_CODES = frozenset({
    VALIDITY_SOURCE_SHA_MISSING,
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


def resolve_source_sha(environ: dict | None = None, repo_root: Path | None = None) -> tuple[str, str]:
    """The exact commit this observation describes, and where it came from.

    GitHub's own commit identity is preferred; `git rev-parse HEAD` is the
    deterministic local fallback for provider-free runs. A mutable branch name
    is never accepted, so a summary can never claim a ref where a commit is
    required. Returns `("", "unavailable")` when no exact SHA can be
    established, which fails the observation closed.
    """

    source = os.environ if environ is None else environ
    candidate = str(source.get("GITHUB_SHA", "") or "").strip().lower()
    if _SHA_RE.fullmatch(candidate):
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
    resolved = (completed.stdout or "").strip().lower()
    if completed.returncode == 0 and _SHA_RE.fullmatch(resolved):
        return resolved, "git_rev_parse"
    return "", "unavailable"


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


def pipeline_record_for_output(case: dict, output: dict) -> PipelineRecord:
    """Classify a completed `run_case_real` state.

    `run_case_real` swallows per-phase exceptions into `eval_errors` so the
    judge can still see partial output. A partial case is not a measurement, so
    those recorded errors are read back here and classified from the typed
    category the runtime attached to them.
    """

    if not isinstance(output, dict):
        # Reachable only from a corrupted `--resume` cache. Classified rather
        # than crashed: a harness that dies has told the operator nothing.
        return PipelineRecord(case, {}, PIPELINE_HARNESS_FAILURE, "unreadable_case_output")

    metadata = output.get("ingestion_metadata")
    recorded = list((metadata or {}).get("eval_errors") or []) if isinstance(metadata, dict) else []
    failures = [str(entry) for entry in recorded if EVAL_ERROR_CONFUSED_HALT not in str(entry)]
    if not failures:
        return PipelineRecord(case, output, PIPELINE_COMPLETE)

    for failure in failures:
        category = typed_provider_category(failure)
        if category:
            return PipelineRecord(case, output, PIPELINE_PROVIDER_FAILURE, category)
    # Fail closed into the nearest truthful non-quality classification.
    return PipelineRecord(case, output, PIPELINE_HARNESS_FAILURE, "untyped_phase_failure")


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
) -> NightlyObservation:
    """Assemble the nightly observation and decide whether it may count.

    The denominator is the *expected* universe, always. A case that never
    reached a measurement leaves the denominator alone and invalidates the
    observation instead of shrinking it.
    """

    validity_errors: list[dict] = list(harness_errors or [])

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

        # Quality is measured only when the case completed the pipeline *and*
        # a real judge score came back for it. Anything else keeps `judge_overall`
        # and `passed` null rather than defaulting them to zero/False.
        measured = record.complete and judge.valid
        result = score_deterministic(record.case, record.output)
        if measured:
            result.judge_overall = judge.score
            result.judge_rationale = judge.rationale
            result.passed = pass_fail(result)

        serialized = asdict(result)
        serialized["judge_overall"] = judge.score if measured else None
        serialized["judge_rationale"] = judge.rationale if measured else ""
        serialized["passed"] = result.passed if measured else None
        serialized["quality_measured"] = measured
        serialized["pipeline_status"] = record.status
        serialized["pipeline_detail"] = record.category
        serialized["judge_status"] = judge.status
        serialized["judge_detail"] = judge.detail
        serialized["judge_score"] = judge.score if measured else None
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
        "mode": "batch",
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
        # never be true for a run that did not measure quality at all.
        "ok": observation.valid_observation and observation.result_class == release_gates.RESULT_PASS,
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
    print(f"source_sha:        {summary['source_sha'] or '<unavailable>'}")
    print(f"valid_observation: {summary['valid_observation']}")
    print(f"result_class:      {summary['result_class']}")
    for entry in summary["validity_errors"]:
        cases = ", ".join(entry["case_ids"])
        print(f"VALIDITY ERROR: {entry['code']}" + (f" [{cases}]" if cases else ""))
    if not summary["valid_observation"]:
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
    source_sha, source_sha_origin = resolve_source_sha()
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
        cached = json.loads(cache_file.read_text())
        records = [
            pipeline_record_for_output(entry["case"], entry["output"]) for entry in cached
        ]
        expected_case_ids = [record.case_id for record in records]
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
            judge_observations = {
                record.case_id: JudgeObservation(
                    JUDGE_VALID,
                    score=(
                        70
                        if (record.output.get("classify") or {}).get("domain")
                        == record.case["expected_domain"]
                        else 40
                    ),
                    rationale="mock",
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
                # Cache inputs so --resume can score them later
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / f"batch_inputs_{batch_id}.json").write_text(json.dumps(
                    [{"case": r.case, "output": r.output} for r in records], default=str
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
    )
    summary = write_report(observation, out_dir, batch_id=batch_id)
    _print_outcome(summary)

    # Truthful red: any non-pass result stays operationally red, including the
    # non-quality ones. Only a valid measured pass exits zero.
    if not summary["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
