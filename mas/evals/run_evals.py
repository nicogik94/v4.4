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
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional

# Allow running from repo root or from inside mas/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from state import ProjectState
from orchestrator import run_phase_node
from llm_client import call_llm, LLMResponse, parse_json
from config import ModelConfig, Provider

PASS_THRESHOLD = 0.75  # fail CI if <75% of cases pass
JUDGE_MODEL = "claude-sonnet-4-6"
JUDGE_SYSTEM_PROMPT = "You are a harsh but fair evaluator. Return only JSON."
EVAL_CASES_PATH = Path(__file__).parent / "golden_cases.jsonl"


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
    judge_overall: int = 0               # 0-100 from LLM judge
    judge_rationale: str = ""
    errors: list[str] = field(default_factory=list)


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
) -> dict:
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    pass_rate = passed / total if total else 0.0
    errors = aggregation_errors or []
    return {
        "timestamp": datetime.now().isoformat(),
        "mode": mode,
        "shard_index": shard_index,
        "shard_count": shard_count,
        "case_ids": case_ids,
        "passed": passed,
        "total": total,
        "pass_rate": pass_rate,
        "threshold": threshold,
        "ok": pass_rate >= threshold and not errors,
        "aggregation_errors": errors,
        "cases": [asdict(r) for r in results],
    }


def write_summary(out_dir: Path, summary: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))


def _case_result_from_dict(data: dict) -> CaseResult:
    allowed = set(CaseResult.__dataclass_fields__)
    return CaseResult(**{key: value for key, value in data.items() if key in allowed})


def aggregate_summaries(report_dirs: list[str], *, threshold: float) -> dict:
    expected_ids = _case_ids(load_cases())
    expected_set = set(expected_ids)
    by_id: dict[str, CaseResult] = {}
    duplicates: list[str] = []
    aggregation_errors: list[str] = []

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

    ordered_results = [by_id[case_id] for case_id in expected_ids if case_id in by_id]
    return summarize_results(
        ordered_results,
        threshold=threshold,
        mode="aggregate",
        case_ids=[result.case_id for result in ordered_results],
        aggregation_errors=aggregation_errors,
    )


async def run_case_real(case: dict) -> ProjectState:
    """Run a case through classify + hypotheses + audit + strategy."""
    state = ProjectState(
        project_id=f"eval-{case['id']}",
        project_name=f"eval {case['id']}",
        brief=case["brief"],
        data=_case_data_payload(case),
        created_at=datetime.now(),
    )
    for phase in ["classify", "hypotheses", "gauntlet", "audit", "strategy"]:
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
        1 for m in forbidden if _term_present(m, normalized_flat)
    )

    # Data labeling honesty (audit phase should mark findings PREDICTED when no data)
    audit = output.get("audit") or {}
    if audit:
        expected_data_based = case.get("data_based_expected", False)
        actual_data_based = audit.get("data_based", False)
        r.data_labeling_correct = (expected_data_based == actual_data_based)
    else:
        r.data_labeling_correct = True  # no audit to judge yet

    return r


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


async def judge_case(case: dict, output: dict) -> tuple[int, str]:
    prompt = JUDGE_PROMPT_TEMPLATE.format(
        brief=case["brief"],
        expected_domain=case["expected_domain"],
        min_h=case["min_hypotheses"],
        max_h=case["max_hypotheses"],
        required_fw=", ".join(case.get("must_contain_frameworks", [])),
        must_mention=", ".join(case.get("strategy_must_mention", [])),
        must_not_mention=", ".join(case.get("strategy_must_not_mention", [])),
        data_based=case.get("data_based_expected", False),
        output=json.dumps(_compact_output_for_judge(output), default=str)[:16000],
    )
    resp: LLMResponse = await call_llm(
        "eval_judge",
        JUDGE_SYSTEM_PROMPT,
        prompt,
        config_override=ModelConfig(
            provider=Provider.ANTHROPIC,
            model=JUDGE_MODEL,
            max_tokens=1000,
            temperature=0.0,
        ),
        project_id=f"eval-{case['id']}",
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


def pass_fail(r: CaseResult) -> bool:
    return (
        r.domain_match
        and r.hypothesis_count_ok
        and r.frameworks_covered >= 0.75
        and r.must_mention_hits >= 0.66
        and r.must_not_mention_violations == 0
        and r.data_labeling_correct
        and r.judge_overall >= 65
    )


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", help="Comma-separated case IDs (default: all)")
    parser.add_argument("--mock", action="store_true", help="Skip real LLM calls")
    parser.add_argument("--report", help="Directory to write per-case JSON reports")
    parser.add_argument("--threshold", type=float, default=PASS_THRESHOLD)
    parser.add_argument("--shard-index", type=int, help="Zero-based shard index to run")
    parser.add_argument("--shard-count", type=int, help="Total number of shards")
    parser.add_argument("--aggregate", nargs="+", help="Shard report directories to aggregate")
    args = parser.parse_args()

    if args.aggregate:
        summary = aggregate_summaries(args.aggregate, threshold=args.threshold)
        print(f"Aggregating {len(args.aggregate)} shard report directories")
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
            else:
                print(f"FAIL: pass rate {summary['pass_rate']:.1%} < threshold {args.threshold:.1%}")
            sys.exit(1)
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
            if args.mock:
                output = await run_case_mock(case)
            else:
                state = await run_case_real(case)
                output = state.model_dump(mode="json")
            r = score_deterministic(case, output)
            if not args.mock:
                r.judge_overall, r.judge_rationale = await judge_case(case, output)
            else:
                r.judge_overall = 70 if r.domain_match else 40
                r.judge_rationale = "mock"
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
