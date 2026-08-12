"""
v4.1 MAS — Eval Harness
Runs the 12 golden decision cases and scores each output with an LLM judge (Sonnet 4.6).
Exits non-zero if the overall pass rate drops below PASS_THRESHOLD — use as a CI gate on
any change to prompts/router.md or prompts/phases/*.

Usage:
    python -m evals.run_evals                      # full run
    python -m evals.run_evals --cases G01,G03      # subset
    python -m evals.run_evals --mock               # skip LLM, test plumbing
    python -m evals.run_evals --shard-index 0 --shard-count 4
    python -m evals.run_evals --aggregate /tmp/eval-shard-*
    python -m evals.run_evals --report evals/out/  # save per-case JSON
"""
import sys
import json
import asyncio
import argparse
import contextlib
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional

# Allow running from repo root or from inside mas/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cdp.citation_resolvability import build_defense_pass_result
from cdp.review_caveats import CDP_REVIEW_CAVEATS
from state import KnowledgeItem, KnowledgeLayerState, ProjectState
from orchestrator import run_phase_node
from llm_client import call_llm, LLMResponse, parse_json
from provider_telemetry import (
    ENTRY_POINT_EVALUATION_JUDGE,
    ENTRY_POINT_EVALUATION_PHASE,
    POSTURE_OBSERVATIONAL,
    configured_posture,
    observe_openai_sdk_requests,
    request_shape_scope,
    response_shape_scope,
    telemetry_scope,
)
from evals import provenance
from evals import release_gates
from config import ModelConfig, Provider

PASS_THRESHOLD = 0.75  # fail CI if <75% of cases pass
JUDGE_MODEL = "claude-sonnet-4-6"
JUDGE_SYSTEM_PROMPT = "You are a harsh but fair evaluator. Return only JSON."
# Unchanged bound on the judge's serialized view of a case. Named so the
# provenance ledger can record how many characters were dropped by it without
# anyone having to re-derive the number from a slice expression.
JUDGE_INPUT_MAX_CHARS = 16000
# The phases a real eval case runs, in order. Previously a local list; named so
# the provenance ledger walks exactly the phases the harness executed.
REAL_CASE_PHASES = ("classify", "hypotheses", "gauntlet", "audit", "strategy")
EVAL_CASES_PATH = Path(__file__).parent / "golden_cases.jsonl"
CITATION_RESOLVABILITY_SCHEMA_VERSION = "citation_resolvability_eval.v0.1"
CITATION_RESOLVABILITY_PASS_STATUSES = {"pass", "not_applicable"}
AGGREGATE_FAILURE_NONE = "none"
AGGREGATE_FAILURE_EVAL_QUALITY = "eval_quality_failure"
AGGREGATE_FAILURE_PROVIDER_UNAVAILABLE = "provider_unavailable"
AGGREGATE_FAILURE_AGGREGATION_ERROR = "aggregation_error"
AGGREGATE_FAILURE_MIXED = "mixed_failure"


def _normalize_eval_text(value) -> str:
    import re
    import unicodedata

    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    text = text.replace("β", "beta").replace("α", "alpha").replace("ρ", "rho")
    text = re.sub(r"[\u2010-\u2015\u2212_/]+", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


_TERM_STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "by",
    "for",
    "from",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}


def _token_matches(expected: str, actual: str) -> bool:
    if expected == actual:
        return True
    if expected + "s" == actual or actual + "s" == expected:
        return True
    if len(expected) >= 5 and len(actual) >= 5:
        if expected.startswith(actual) or actual.startswith(expected):
            return True
    if len(expected) >= 6 and len(actual) >= 6:
        return expected[:6] == actual[:6]
    return False


def _term_present(term: str, normalized_text: str) -> bool:
    normalized_term = _normalize_eval_text(term)
    if not normalized_term:
        return True
    if normalized_term in normalized_text:
        return True
    term_tokens = [
        token
        for token in normalized_term.split()
        if token not in _TERM_STOPWORDS
    ]
    if len(term_tokens) < 2:
        return False
    text_tokens = normalized_text.split()
    return all(
        any(_token_matches(term_token, text_token) for text_token in text_tokens)
        for term_token in term_tokens
    )


def _term_present_exact(term: str, normalized_text: str) -> bool:
    """Exact-substring-only variant of _term_present for must-not-mention checks.

    The multi-token fallback in _term_present fires on negation context
    (e.g. 'do not ignore bugs' triggers 'just ignore bugs') because prohibited
    tokens appear scattered across unrelated sentences.  Must-not-mention only
    needs to catch the literal prohibited phrase, so skip the fuzzy path.
    """
    normalized_term = _normalize_eval_text(term)
    if not normalized_term:
        return True
    return normalized_term in normalized_text


def _is_confused_classification(state: ProjectState) -> bool:
    classify = getattr(state, "classify", None)
    return _normalize_eval_text(getattr(classify, "domain", "")) == "confused"


def _append_eval_error(state: ProjectState, message: str) -> None:
    state.ingestion_metadata.setdefault("eval_errors", []).append(message)


def _case_data_payload(case: dict) -> str:
    explicit_data = case.get("data") or case.get("evidence") or case.get("metrics")
    if explicit_data:
        return str(explicit_data)
    if case.get("data_based_expected", False):
        return "Operator-provided measured facts from the decision brief:\n" + case["brief"]
    return ""


def _compact_output_for_judge(output: dict) -> dict:
    phase_names = [
        "classify",
        "hypotheses",
        "gauntlet",
        "audit",
        "strategy",
        "ingestion_metadata",
    ]
    return {name: output.get(name) for name in phase_names if output.get(name) is not None}


@dataclass
class CaseResult:
    case_id: str
    passed: bool = False
    domain_match: bool = False
    hypothesis_count_ok: bool = False
    frameworks_covered: float = 0.0      # fraction of required frameworks seen
    must_mention_hits: float = 0.0       # fraction of must_mention terms seen
    must_not_mention_violations: int = 0
    data_labeling_correct: bool = False  # PREDICTED vs MEASURED honesty
    citation_resolvability_ok: bool = True
    citation_resolvability: dict = field(default_factory=dict)
    judge_overall: int = 0               # 0-100 from LLM judge
    judge_rationale: str = ""
    errors: list[str] = field(default_factory=list)
    # Observational only. Never read by pass_fail(); see evals.provenance.
    provenance: dict = field(default_factory=dict)


def load_cases(subset: Optional[set[str]] = None) -> list[dict]:
    cases = []
    for line in EVAL_CASES_PATH.read_text().splitlines():
        if line.strip():
            c = json.loads(line)
            if subset is None or c["id"] in subset:
                cases.append(c)
    return cases


def shard_cases(cases: list[dict], shard_index: int, shard_count: int) -> list[dict]:
    if shard_count < 1:
        raise ValueError("--shard-count must be >= 1")
    if shard_index < 0 or shard_index >= shard_count:
        raise ValueError("--shard-index must be between 0 and shard-count - 1")
    return [case for index, case in enumerate(cases) if index % shard_count == shard_index]


def _case_ids(cases: list[dict]) -> list[str]:
    return [case["id"] for case in cases]


def summarize_results(
    results: list[CaseResult],
    *,
    threshold: float,
    mode: str,
    case_ids: list[str],
    shard_index: int | None = None,
    shard_count: int | None = None,
    aggregation_errors: list[str] | None = None,
    provider_gate: str | None = None,
) -> dict:
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    pass_rate = passed / total if total else 0.0
    errors = aggregation_errors or []
    aggregate_diagnostics = build_aggregate_diagnostics(
        results,
        threshold=threshold,
        pass_rate=pass_rate,
        aggregation_errors=errors,
    )
    return {
        "timestamp": datetime.now().isoformat(),
        "mode": mode,
        # Which release gate produced this result. Resolved from the closed
        # vocabulary, never guessed: an unset or unrecognized value records
        # `none`, so an artifact can state "no gate" but can never silently
        # claim the wrong one.
        "provider_gate": release_gates.normalize_gate(
            release_gates.gate_from_env() if provider_gate is None else provider_gate
        ),
        "shard_index": shard_index,
        "shard_count": shard_count,
        "case_ids": case_ids,
        "passed": passed,
        "total": total,
        "pass_rate": pass_rate,
        "threshold": threshold,
        "ok": pass_rate >= threshold and not errors,
        "aggregation_errors": errors,
        **aggregate_diagnostics,
        # Additive and namespaced: a historical reader that does not know this
        # key is unaffected, and nothing inside it feeds `ok`, `pass_rate`,
        # `passed`, `total` or `threshold` above.
        "eval_provenance": build_aggregate_provenance(
            results, aggregation_errors=errors
        ),
        "cases": [asdict(r) for r in results],
    }


def write_summary(out_dir: Path, summary: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))


def _case_result_from_dict(data: dict) -> CaseResult:
    allowed = set(CaseResult.__dataclass_fields__)
    return CaseResult(**{key: value for key, value in data.items() if key in allowed})


def aggregate_summaries(
    report_dirs: list[str],
    *,
    threshold: float,
    expect_gate: str | None = None,
    expect_shard_count: int | None = None,
) -> dict:
    """Combine shard summaries, refusing to mix release gates.

    `expect_gate` is the gate the caller believes it is aggregating. A shard
    whose recorded identity differs is an aggregation error, not a silently
    absorbed row: Gate A and Gate B make different claims, so combining their
    cases would manufacture a result that describes neither provider posture.
    """

    expected_gate = release_gates.normalize_gate(expect_gate) if expect_gate is not None else None
    expected_ids = _case_ids(load_cases())
    expected_set = set(expected_ids)
    by_id: dict[str, CaseResult] = {}
    duplicates: list[str] = []
    aggregation_errors: list[str] = []
    seen_shards: set[int] = set()
    contributing_dirs = 0

    for report_dir in report_dirs:
        summary_path = Path(report_dir) / "summary.json"
        if not summary_path.exists():
            aggregation_errors.append(f"missing summary.json in {report_dir}")
            continue
        try:
            data = json.loads(summary_path.read_text())
        except Exception as exc:
            aggregation_errors.append(f"failed to read {summary_path}: {exc}")
            continue

        shard_gate = release_gates.normalize_gate(data.get("provider_gate"))
        if expected_gate is not None and shard_gate != expected_gate:
            aggregation_errors.append(
                f"gate identity mismatch in {summary_path}: "
                f"expected {expected_gate}, shard reports {shard_gate}"
            )
            continue

        contributing_dirs += 1
        shard_index = data.get("shard_index")
        if isinstance(shard_index, int):
            seen_shards.add(shard_index)

        for case_data in data.get("cases", []):
            case_id = str(case_data.get("case_id", ""))
            if not case_id:
                aggregation_errors.append(f"case without case_id in {summary_path}")
                continue
            if case_id in by_id:
                duplicates.append(case_id)
                continue
            by_id[case_id] = _case_result_from_dict(case_data)

    actual_set = set(by_id)
    unknown = sorted(actual_set - expected_set)
    missing = [case_id for case_id in expected_ids if case_id not in actual_set]
    if duplicates:
        aggregation_errors.append(f"duplicate case IDs: {', '.join(sorted(duplicates))}")
    if unknown:
        aggregation_errors.append(f"unknown case IDs: {', '.join(unknown)}")
    if missing:
        aggregation_errors.append(f"missing case IDs: {', '.join(missing)}")

    if expect_shard_count is not None:
        # Structural completeness is asserted over the shards that actually
        # reported, not over the directories the glob happened to match: a shard
        # whose job died uploads nothing, and its absence must be an error
        # rather than a smaller denominator.
        if contributing_dirs != expect_shard_count:
            aggregation_errors.append(
                f"expected {expect_shard_count} shard reports, {contributing_dirs} contributed"
            )
        absent = [index for index in range(expect_shard_count) if index not in seen_shards]
        if absent:
            aggregation_errors.append(
                f"missing shard indices: {', '.join(str(index) for index in absent)}"
            )

    ordered_results = [by_id[case_id] for case_id in expected_ids if case_id in by_id]
    return summarize_results(
        ordered_results,
        threshold=threshold,
        mode="aggregate",
        case_ids=[result.case_id for result in ordered_results],
        aggregation_errors=aggregation_errors,
        provider_gate=expected_gate,
    )


def build_aggregate_diagnostics(
    results: list[CaseResult],
    *,
    threshold: float,
    pass_rate: float,
    aggregation_errors: list[str],
) -> dict:
    provider_failures: list[tuple[CaseResult, str]] = []
    real_quality_failures: list[CaseResult] = []
    failed_results = [result for result in results if not result.passed]

    for result in failed_results:
        provider_category = classify_provider_failure(result.judge_rationale)
        if provider_category:
            provider_failures.append((result, provider_category))
        if not _case_failed_due_provider_only(result, provider_category):
            real_quality_failures.append(result)

    provider_failure_categories = sorted({category for _, category in provider_failures})
    provider_failure_only = bool(provider_failures) and not aggregation_errors and not real_quality_failures
    provider_unavailable = provider_failure_only and pass_rate < threshold
    if aggregation_errors:
        aggregate_failure_kind = (
            AGGREGATE_FAILURE_MIXED
            if provider_failures or real_quality_failures
            else AGGREGATE_FAILURE_AGGREGATION_ERROR
        )
    elif provider_unavailable:
        aggregate_failure_kind = AGGREGATE_FAILURE_PROVIDER_UNAVAILABLE
    elif pass_rate < threshold:
        aggregate_failure_kind = (
            AGGREGATE_FAILURE_MIXED
            if provider_failures and real_quality_failures
            else AGGREGATE_FAILURE_EVAL_QUALITY
        )
    else:
        aggregate_failure_kind = AGGREGATE_FAILURE_NONE

    if provider_failure_only:
        quality_ok: bool | str = "unknown"
        evaluation_note = "Quality was not fully evaluated because the judge provider was unavailable."
    elif real_quality_failures:
        quality_ok = False
        evaluation_note = "One or more eval cases failed deterministic checks or non-provider judge scoring."
    else:
        quality_ok = True
        evaluation_note = ""

    return {
        "provider_failure_count": len(provider_failures),
        "provider_failure_categories": provider_failure_categories,
        "provider_failure_detected": bool(provider_failures),
        "provider_failure_only": provider_failure_only,
        "provider_unavailable": provider_unavailable,
        "aggregate_failure_kind": aggregate_failure_kind,
        "quality_ok": quality_ok,
        "quality_evaluation_note": evaluation_note,
        "quality_failure_count": len(real_quality_failures),
        "quality_failure_case_ids": [result.case_id for result in real_quality_failures],
        "provider_failure_case_ids": [result.case_id for result, _ in provider_failures],
    }


def classify_provider_failure(rationale: str) -> str:
    """Return a conservative provider/infra failure category from judge rationale."""
    text = _normalize_provider_failure_text(rationale)
    if not text:
        return ""
    provider_context = (
        "judge error" in text
        or "provider call failed" in text
        or "provider=" in text
        or "batch error" in text
    )
    if not provider_context:
        return ""
    if "quota_exceeded" in text or "quota exceeded" in text or "credit balance" in text:
        return "quota_exceeded"
    if "rate_limit" in text or "rate limit" in text or "rate-limit" in text or "status_code=429" in text:
        return "rate_limit"
    unavailable_terms = (
        "provider_unavailable",
        "provider unavailable",
        "service unavailable",
        "temporarily unavailable",
        "transient provider",
        "overloaded",
        "timed out",
        "timeout",
        "model is not available",
        "billing blocked",
    )
    if any(term in text for term in unavailable_terms):
        return "provider_unavailable"
    return ""


def aggregate_exit_code(summary: dict) -> int:
    if summary.get("ok"):
        return 0
    return 1


def _case_failed_due_provider_only(result: CaseResult, provider_category: str) -> bool:
    if result.passed or not provider_category or result.errors:
        return False
    # Provider judge outages make same-case quality fields unavailable; separate
    # failed cases without provider rationale are still real quality failures.
    return result.judge_overall < 65


def _normalize_provider_failure_text(value: str) -> str:
    return " ".join(str(value or "").strip().casefold().split())


# ═══════════ eval failure provenance (observational, default off) ═══════════
#
# Everything below records evidence. None of it decides anything: no branch in
# this module reads a provenance value, and pass_fail() cannot see one. When no
# recorder is bound, every helper here returns the value that makes the harness
# behave exactly as it did before this wave — the same telemetry scope, the same
# phase loop, the same continuation after a failed phase.


def new_provenance_recorder(case_id: str):
    """A recorder for one real case, or ``None`` when provenance is not wanted.

    Refuses to engage when this process is already running the durable attempt
    telemetry: an eval-local sink is the right sink for an eval, and quietly
    displacing a configured durable posture would make this harness the reason
    a run's telemetry went somewhere else.
    """
    try:
        if not provenance.provenance_enabled():
            return None
        if configured_posture() != "off":
            return None
        return provenance.EvalProvenanceRecorder(case_id=case_id)
    except Exception:  # noqa: BLE001 - provenance never breaks a case
        return None


def _disabled_capture_mode() -> str:
    """Why provenance is absent, when it is."""
    try:
        if not provenance.provenance_enabled():
            return provenance.CAPTURE_MODE_DISABLED
        if configured_posture() != "off":
            return provenance.CAPTURE_MODE_DEFERRED
    except Exception:  # noqa: BLE001 - provenance never breaks a case
        pass
    return provenance.CAPTURE_MODE_DISABLED


def _provenance_session_kwargs(recorder) -> dict:
    """Route the runtime's own attempt telemetry into eval memory.

    An explicit observational posture with an in-memory sink, so the durable
    PostgreSQL relations are neither required nor written and the SDK transport
    is not instrumented — the process posture stays off, which is what the
    adapter reads when it decides whether to wrap its client.
    """
    if recorder is None:
        return {}
    return {"posture": POSTURE_OBSERVATIONAL, "sink": recorder}


def _shape_observer(recorder):
    """Bind the recorder as observer for both response and request shape.

    Two independent scopes, deliberately not one: the response observer is fed
    from the adapter after a reply arrives, the request observer from the
    adapter and the transport before the bytes leave. Keeping them separate is
    what stops a reader — or a future edit — from treating "what we sent" and
    "what came back" as one record.
    """
    if recorder is None:
        return contextlib.nullcontext()
    stack = contextlib.ExitStack()
    stack.enter_context(response_shape_scope(recorder))
    stack.enter_context(request_shape_scope(recorder))
    # M4.0. The adapter boundary is observed by wrapping the OpenAI SDK's own
    # `create`, immediately outside `llm_client._call_openai`, because that
    # adapter is a certified product path this observational wave must leave
    # byte-identical. The wrapper publishes and delegates unchanged; if the
    # installed SDK does not have the pinned shape it observes nothing, and the
    # ledger says `unknown` rather than inventing a request.
    stack.enter_context(observe_openai_sdk_requests())
    return stack


def build_case_provenance(case_id: str, output: dict, recorder) -> dict:
    """Assemble one case's ledger. Cannot raise into the harness."""
    try:
        if recorder is None:
            return provenance.empty_case_provenance(
                case_id=case_id, capture_mode=_disabled_capture_mode()
            )
        return provenance.case_provenance(
            case_id=case_id,
            output=output,
            phases=REAL_CASE_PHASES,
            recorder=recorder,
            judge=getattr(recorder, "judge_record", {}),
            capture_mode=provenance.CAPTURE_MODE_TELEMETRY,
        )
    except Exception:  # noqa: BLE001 - provenance never breaks a case
        return provenance.empty_case_provenance(
            case_id=case_id, capture_mode=provenance.CAPTURE_MODE_DISABLED
        )


def build_aggregate_provenance(results, *, aggregation_errors) -> dict:
    """Aggregate counters. Cannot raise into the summary writer."""
    try:
        return provenance.aggregate_provenance(
            results, aggregation_errors=aggregation_errors
        )
    except Exception:  # noqa: BLE001 - provenance never breaks a summary
        return {"schema_version": provenance.SCHEMA_VERSION, "cases_with_provenance": 0}


async def run_case_real(case: dict, recorder=None) -> ProjectState:
    """Run a case through classify + hypotheses + audit + strategy.

    The loop, the Confused halt and the continue-after-failure behavior are
    unchanged: this wave observes that behavior and does not alter it.
    """
    state = ProjectState(
        project_id=f"eval-{case['id']}",
        project_name=f"eval {case['id']}",
        brief=case["brief"],
        data=_case_data_payload(case),
        created_at=datetime.now(),
    )
    phases = list(REAL_CASE_PHASES)
    # `eval-<id>` is not a UUID, and is deliberately not treated as one: it is
    # recorded as an external project identity, never cast into a UUID column.
    with _shape_observer(recorder):
        async with telemetry_scope(
            entry_point=ENTRY_POINT_EVALUATION_PHASE,
            project_id=state.project_id,
            run_id=state.project_id,
            expected_phases=tuple(phases),
            **_provenance_session_kwargs(recorder),
        ):
            for phase in phases:
                try:
                    state = await run_phase_node(state, phase)
                    if phase == "classify" and _is_confused_classification(state):
                        _append_eval_error(state, "workflow halted after Confused classification")
                        break
                except Exception as e:
                    # Log error but continue — judge can still evaluate partial output
                    _append_eval_error(state, f"{phase}: {e}")
    return state


async def run_case_mock(case: dict) -> dict:
    """Lightweight mock — exercises plumbing without LLM calls.

    Mock mode is a smoke check for imports, case loading, deterministic
    scoring, reporting, and CLI threshold handling. It echoes each case's
    expected framework and must-mention terms into mock-only fields so the
    normal pass/fail plumbing can run without weakening real eval standards.
    """
    return {
        "mock": True,
        "brief": case["brief"],
        "classify": {"domain": case["expected_domain"]},
        "hypotheses": [{"id": f"H{i+1}"} for i in range(case.get("min_hypotheses", 0))],
        "mock_expected_frameworks": case.get("must_contain_frameworks", []),
        "mock_expected_strategy_terms": case.get("strategy_must_mention", []),
    }


def score_deterministic(case: dict, output: dict) -> CaseResult:
    """Deterministic checks that do not need an LLM judge."""
    r = CaseResult(case_id=case["id"])

    # Domain match
    classify = (output.get("classify") or {}) if isinstance(output, dict) else {}
    actual_domain = classify.get("domain", "")
    r.domain_match = (actual_domain == case["expected_domain"])

    # Hypothesis count
    hyps = output.get("hypotheses") or []
    n = len(hyps)
    r.hypothesis_count_ok = case["min_hypotheses"] <= n <= case["max_hypotheses"]

    # Frameworks covered
    flat = json.dumps(output, ensure_ascii=False)
    normalized_flat = _normalize_eval_text(flat)
    required_fw = [fw.lower() for fw in case.get("must_contain_frameworks", [])]
    if required_fw:
        hits = sum(1 for fw in required_fw if _term_present(fw, normalized_flat))
        r.frameworks_covered = hits / len(required_fw)
    else:
        r.frameworks_covered = 1.0

    # Must-mention / must-not-mention
    must = [m.lower() for m in case.get("strategy_must_mention", [])]
    if must:
        hits = sum(1 for m in must if _term_present(m, normalized_flat))
        r.must_mention_hits = hits / len(must)
    else:
        r.must_mention_hits = 1.0

    forbidden = [m.lower() for m in case.get("strategy_must_not_mention", [])]
    r.must_not_mention_violations = sum(
        1 for m in forbidden if _term_present_exact(m, normalized_flat)
    )

    # Data labeling honesty (audit phase should mark findings PREDICTED when no data)
    audit = output.get("audit") or {}
    if audit:
        expected_data_based = case.get("data_based_expected", False)
        actual_data_based = audit.get("data_based", False)
        r.data_labeling_correct = (expected_data_based == actual_data_based)
    else:
        r.data_labeling_correct = True  # no audit to judge yet

    r.citation_resolvability = score_citation_resolvability(case, output)
    r.citation_resolvability_ok = _citation_resolvability_ok(case, r.citation_resolvability)

    return r


def score_citation_resolvability(case: dict, output: dict) -> dict:
    """Score review-only citation resolvability without semantic support claims."""
    state = _citation_eval_state(case, output)
    if state is None:
        return _citation_eval_payload(
            status="not_applicable",
            score=0.0,
            warnings=["No report/evidence fixture was supplied for citation-resolvability eval."],
            missing_inputs=["citation_resolvability_fixture_missing"],
        )

    result = build_defense_pass_result(state)
    counts = dict(result.summary_counts)
    resolved_exact_count = int(counts.get("resolved_exact", 0) or 0)
    resolved_id_only_count = int(counts.get("resolved_id_only", 0) or 0)
    unknown_evidence_id_count = int(counts.get("unknown_evidence_id", 0) or 0)
    locator_mismatch_count = int(counts.get("locator_mismatch", 0) or 0)
    malformed_marker_count = int(counts.get("malformed", 0) or 0)
    canonical_marker_count = int(counts.get("canonical_marker_count", 0) or 0)
    marker_count = canonical_marker_count + malformed_marker_count
    unresolved_count = unknown_evidence_id_count + locator_mismatch_count + malformed_marker_count
    load_bearing_review_count = int(counts.get("load_bearing_review_count", 0) or 0)

    warnings: list[str] = []
    if not str(getattr(state, "report", "") or "").strip():
        status = "unknown"
        warnings.append("Report text is missing; citation resolvability cannot be evaluated.")
    elif marker_count == 0:
        status = "no_markers"
        warnings.append("No evidence citation markers were found; this is not evidence of semantic support.")
    elif unresolved_count:
        status = "fail"
        warnings.append("Unresolved or malformed citation marker(s) require operator review.")
    elif result.missing_inputs:
        status = "unknown"
        warnings.append("Citation-resolvability inputs are incomplete.")
    elif resolved_id_only_count or load_bearing_review_count:
        status = "partial"
        if resolved_id_only_count:
            warnings.append("ID-only citation resolution is weaker than exact locator resolution.")
        if load_bearing_review_count:
            warnings.append("Load-bearing line-level review prompts require operator review.")
    else:
        status = "pass"

    score = _citation_resolvability_score(
        marker_count=marker_count,
        resolved_exact_count=resolved_exact_count,
        resolved_id_only_count=resolved_id_only_count,
        unresolved_count=unresolved_count,
        status=status,
    )
    return _citation_eval_payload(
        status=status,
        score=score,
        marker_count=marker_count,
        resolved_exact_count=resolved_exact_count,
        resolved_id_only_count=resolved_id_only_count,
        unknown_evidence_id_count=unknown_evidence_id_count,
        locator_mismatch_count=locator_mismatch_count,
        malformed_marker_count=malformed_marker_count,
        unresolved_count=unresolved_count,
        load_bearing_review_count=load_bearing_review_count,
        missing_inputs=list(result.missing_inputs),
        warnings=warnings,
    )


def _citation_resolvability_ok(case: dict, summary: dict) -> bool:
    fixture = case.get("citation_resolvability_fixture") or {}
    expected_status = str(fixture.get("expected_status") or "").strip()
    if expected_status:
        if str(summary.get("status") or "") != expected_status:
            return False
        expected_min_score = fixture.get("expected_min_score")
        if expected_min_score is not None:
            try:
                return float(summary.get("score", 0.0) or 0.0) >= float(expected_min_score)
            except (TypeError, ValueError):
                return False
        return True
    return str(summary.get("status") or "") in CITATION_RESOLVABILITY_PASS_STATUSES


def _citation_resolvability_score(
    *,
    marker_count: int,
    resolved_exact_count: int,
    resolved_id_only_count: int,
    unresolved_count: int,
    status: str,
) -> float:
    if status in {"not_applicable", "unknown", "no_markers"} or marker_count <= 0:
        return 0.0
    weighted = resolved_exact_count + (0.6 * resolved_id_only_count) - unresolved_count
    return round(max(0.0, min(1.0, weighted / marker_count)), 4)


def _citation_eval_payload(
    *,
    status: str,
    score: float,
    marker_count: int = 0,
    resolved_exact_count: int = 0,
    resolved_id_only_count: int = 0,
    unknown_evidence_id_count: int = 0,
    locator_mismatch_count: int = 0,
    malformed_marker_count: int = 0,
    unresolved_count: int = 0,
    load_bearing_review_count: int = 0,
    missing_inputs: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict:
    return {
        "schema_version": CITATION_RESOLVABILITY_SCHEMA_VERSION,
        "source": "cdp.citation_resolvability.build_defense_pass_result",
        "review_only": True,
        "traceability_only": True,
        "score": score,
        "marker_count": marker_count,
        "resolved_exact_count": resolved_exact_count,
        "resolved_id_only_count": resolved_id_only_count,
        "unknown_evidence_id_count": unknown_evidence_id_count,
        "locator_mismatch_count": locator_mismatch_count,
        "malformed_marker_count": malformed_marker_count,
        "unresolved_count": unresolved_count,
        "load_bearing_review_count": load_bearing_review_count,
        "status": status,
        "warnings": list(warnings or []),
        "missing_inputs": list(missing_inputs or []),
        "caveats": list(CDP_REVIEW_CAVEATS),
    }


def _citation_eval_state(case: dict, output: dict) -> ProjectState | None:
    fixture = case.get("citation_resolvability_fixture")
    if isinstance(fixture, dict):
        return _citation_fixture_state(case, fixture)

    if not isinstance(output, dict):
        return None
    report = output.get("report")
    if not isinstance(report, str) or not report.strip():
        return None
    payload = {
        "project_id": output.get("project_id") or f"eval-{case.get('id', 'case')}",
        "project_name": output.get("project_name") or f"eval {case.get('id', 'case')}",
        "brief": output.get("brief") or case.get("brief", ""),
    }
    payload.update(output)
    return ProjectState.model_validate(payload)


def _citation_fixture_state(case: dict, fixture: dict) -> ProjectState:
    state = ProjectState(
        project_id=f"eval-{case.get('id', 'case')}-citation",
        project_name=f"eval {case.get('id', 'case')} citation fixture",
        brief=case.get("brief", ""),
        report=str(fixture.get("report") or ""),
    )
    knowledge_items = []
    for item in fixture.get("knowledge_items", []) or []:
        if not isinstance(item, dict):
            continue
        knowledge_items.append(
            KnowledgeItem(
                item_id=str(item.get("item_id") or item.get("evidence_id") or ""),
                evidence_id=str(item.get("evidence_id") or item.get("item_id") or ""),
                source_id=str(item.get("source_id") or "eval_fixture"),
                source_ref=str(item.get("source_ref") or ""),
                locator=str(item.get("locator") or ""),
                title=str(item.get("title") or ""),
                structured_payload=dict(item.get("structured_payload") or {}),
            )
        )
    if knowledge_items:
        state.knowledge_layer = KnowledgeLayerState(items=knowledge_items)
    return state


JUDGE_PROMPT_TEMPLATE = """You are evaluating the output of an AI decision workflow against a rubric.

CASE BRIEF:
{brief}

EXPECTED:
- Domain: {expected_domain}
- Hypothesis count: {min_h}-{max_h}
- Required frameworks: {required_fw}
- Strategy must mention: {must_mention}
- Strategy must NOT mention: {must_not_mention}
- Data-based audit expected: {data_based}

ACTUAL OUTPUT (JSON):
{output}

Score the output 0-100 on overall quality. Penalize heavily for: missing frameworks, ungrounded strategies, dishonest data labeling, hypothesis count out of range, jargon-only (no concrete actions), verdicts with no evidence.

Return JSON only: {{"score": 0-100, "rationale": "2-3 sentences", "critical_failures": []}}"""


async def judge_case(case: dict, output: dict, recorder=None) -> tuple[int, str]:
    # Serialized once so the ledger can record how many characters the unchanged
    # 16000-character bound dropped. The bound, the slice and the judge's model,
    # provider, token cap, temperature, system prompt and rubric are untouched.
    serialized_output = json.dumps(_compact_output_for_judge(output), default=str)
    judge_input = serialized_output[:JUDGE_INPUT_MAX_CHARS]
    prompt = JUDGE_PROMPT_TEMPLATE.format(
        brief=case["brief"],
        expected_domain=case["expected_domain"],
        min_h=case["min_hypotheses"],
        max_h=case["max_hypotheses"],
        required_fw=", ".join(case.get("must_contain_frameworks", [])),
        must_mention=", ".join(case.get("strategy_must_mention", [])),
        must_not_mention=", ".join(case.get("strategy_must_not_mention", [])),
        data_based=case.get("data_based_expected", False),
        output=judge_input,
    )
    judge_config = ModelConfig(
        provider=Provider.ANTHROPIC,
        model=JUDGE_MODEL,
        max_tokens=1000,
        temperature=0.0,
    )
    with _shape_observer(recorder):
        async with telemetry_scope(
            entry_point=ENTRY_POINT_EVALUATION_JUDGE,
            project_id=f"eval-{case['id']}",
            run_id=f"eval-{case['id']}",
            phase="eval_judge",
            expected_phases=("eval_judge",),
            **_provenance_session_kwargs(recorder),
        ):
            resp: LLMResponse = await call_llm(
                "eval_judge",
                JUDGE_SYSTEM_PROMPT,
                prompt,
                config_override=judge_config,
                project_id=f"eval-{case['id']}",
            )
    _record_judge_provenance(
        recorder,
        judge_config=judge_config,
        pre_truncation_chars=len(serialized_output),
        post_truncation_chars=len(judge_input),
        response=resp,
    )
    if not resp.ok:
        return 0, f"judge error: {resp.error}"
    try:
        data = parse_json(resp.text)
        if not isinstance(data, dict):
            raise ValueError("judge response did not contain a JSON object")
        if "score" not in data:
            raise ValueError("judge response missing score")
        return int(data["score"]), data.get("rationale", "")
    except Exception as e:
        return 0, f"judge parse error: {e}"


def _record_judge_provenance(
    recorder,
    *,
    judge_config: ModelConfig,
    pre_truncation_chars: int,
    post_truncation_chars: int,
    response,
) -> None:
    """Record what the judge was asked for and what answered. Never raises."""
    if recorder is None:
        return
    try:
        recorder.judge_record = provenance.judge_provenance(
            requested_provider=judge_config.provider.value,
            requested_model=judge_config.model,
            requested_max_tokens=judge_config.max_tokens,
            requested_temperature=judge_config.temperature,
            input_chars_pre_truncation=pre_truncation_chars,
            input_chars_post_truncation=post_truncation_chars,
            response=response,
            recorder=recorder,
        )
    except Exception:  # noqa: BLE001 - provenance never breaks a case
        recorder.note(provenance.NOTE_RECORDER_FAULT)


def pass_fail(r: CaseResult) -> bool:
    return (
        r.domain_match
        and r.hypothesis_count_ok
        and r.frameworks_covered >= 0.75
        and r.must_mention_hits >= 0.66
        and r.must_not_mention_violations == 0
        and r.data_labeling_correct
        and r.citation_resolvability_ok
        and r.judge_overall >= 65
    )


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", help="Comma-separated case IDs (default: all)")
    parser.add_argument("--mock", action="store_true", help="Skip real LLM calls")
    parser.add_argument("--report", help="Directory to write per-case JSON reports")
    # `type=float` alone accepts `nan`, `inf` and out-of-range values, any of
    # which silently changes what the gate means. `normalize_threshold` refuses
    # them, so a malformed threshold is an argparse error before a single case
    # runs -- never a quality verdict.
    parser.add_argument(
        "--threshold",
        type=release_gates.normalize_threshold,
        default=PASS_THRESHOLD,
    )
    parser.add_argument("--shard-index", type=int, help="Zero-based shard index to run")
    parser.add_argument("--shard-count", type=int, help="Total number of shards")
    parser.add_argument("--aggregate", nargs="+", help="Shard report directories to aggregate")
    parser.add_argument(
        "--expect-gate",
        choices=release_gates.GATE_CHOICES,
        help="Release gate whose shards these must be; mismatched shards are refused",
    )
    parser.add_argument(
        "--expect-shard-count",
        type=int,
        help="Number of shards that must have contributed a report",
    )
    args = parser.parse_args()

    if args.aggregate:
        summary = aggregate_summaries(
            args.aggregate,
            threshold=args.threshold,
            expect_gate=args.expect_gate,
            expect_shard_count=args.expect_shard_count,
        )
        print(f"Aggregating {len(args.aggregate)} shard report directories")
        print(f"Release gate: {summary.get('provider_gate')}")
        for error in summary.get("aggregation_errors", []):
            print(f"AGGREGATION ERROR: {error}")
        print(
            f"\n=== RESULT: {summary['passed']}/{summary['total']} passed "
            f"({summary['pass_rate']:.1%}) ==="
        )
        if args.report:
            out_dir = Path(args.report)
            write_summary(out_dir, summary)
            print(f"Report written to {out_dir}/summary.json")
        if not summary["ok"]:
            if summary.get("aggregation_errors"):
                print("FAIL: aggregate report is incomplete or invalid")
            elif summary.get("aggregate_failure_kind") == AGGREGATE_FAILURE_PROVIDER_UNAVAILABLE:
                print("PROVIDER UNAVAILABLE: judge provider/quota failure prevented full quality evaluation")
                print(f"Provider failure categories: {', '.join(summary.get('provider_failure_categories') or [])}")
                print(
                    "FAIL CLOSED: provider unavailability prevented quality evaluation; "
                    "this is not an eval-quality regression"
                )
            else:
                kind = summary.get("aggregate_failure_kind") or AGGREGATE_FAILURE_EVAL_QUALITY
                print(f"FAIL: {kind}; pass rate {summary['pass_rate']:.1%} < threshold {args.threshold:.1%}")
            sys.exit(aggregate_exit_code(summary))
        print("PASS")
        return

    if (args.shard_index is None) != (args.shard_count is None):
        parser.error("--shard-index and --shard-count must be provided together")

    subset = set(args.cases.split(",")) if args.cases else None
    cases = load_cases(subset)
    if args.shard_index is not None and args.shard_count is not None:
        cases = shard_cases(cases, args.shard_index, args.shard_count)
    mode = "mock" if args.mock else "real"
    shard_label = ""
    if args.shard_index is not None and args.shard_count is not None:
        shard_label = f" shard {args.shard_index}/{args.shard_count}"
    print(f"Running {len(cases)} cases ({mode.upper()}{shard_label})")

    results = []
    for case in cases:
        print(f"  [{case['id']}] {case['brief'][:60]}...")
        try:
            recorder = None
            if args.mock:
                output = await run_case_mock(case)
            else:
                recorder = new_provenance_recorder(case["id"])
                state = await run_case_real(case, recorder)
                output = state.model_dump(mode="json")
            r = score_deterministic(case, output)
            if not args.mock:
                r.judge_overall, r.judge_rationale = await judge_case(
                    case, output, recorder
                )
                r.provenance = build_case_provenance(case["id"], output, recorder)
            else:
                r.judge_overall = 70 if r.domain_match else 40
                r.judge_rationale = "mock"
                r.provenance = provenance.empty_case_provenance(
                    case_id=case["id"], capture_mode=provenance.CAPTURE_MODE_MOCK
                )
            r.passed = pass_fail(r)
            results.append(r)
            status = "✓" if r.passed else "✗"
            print(f"     {status} judge={r.judge_overall} domain={r.domain_match} fw={r.frameworks_covered:.2f}")
        except Exception as e:
            r = CaseResult(case_id=case["id"], errors=[str(e)])
            results.append(r)
            print(f"     ERROR: {e}")

    # Aggregate
    summary = summarize_results(
        results,
        threshold=args.threshold,
        mode=mode,
        case_ids=_case_ids(cases),
        shard_index=args.shard_index,
        shard_count=args.shard_count,
    )
    print(f"\n=== RESULT: {summary['passed']}/{summary['total']} passed ({summary['pass_rate']:.1%}) ===")

    if args.report:
        out_dir = Path(args.report)
        write_summary(out_dir, summary)
        print(f"Report written to {out_dir}/summary.json")

    if args.shard_index is not None and args.shard_count is not None:
        print("Shard complete; global threshold gate is deferred to aggregation")
        print("PASS")
        return

    if summary["pass_rate"] < args.threshold:
        print(f"FAIL: pass rate {summary['pass_rate']:.1%} < threshold {args.threshold:.1%}")
        sys.exit(1)
    print("PASS")


if __name__ == "__main__":
    asyncio.run(main())
