"""
v4.1 MAS — Eval Harness
Runs the 12 golden decision cases and scores each output with an LLM judge (Sonnet 4.6).
Exits non-zero if the overall pass rate drops below PASS_THRESHOLD — use as a CI gate on
any change to prompts/router.md or prompts/phases/*.

Usage:
    python -m evals.run_evals                      # full run
    python -m evals.run_evals --cases G01,G03      # subset
    python -m evals.run_evals --mock               # skip LLM, test plumbing
    python -m evals.run_evals --report evals/out/  # save per-case JSON
"""
import os
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
from llm_client import call_llm, LLMResponse
from config import ModelConfig, Provider

PASS_THRESHOLD = 0.75  # fail CI if <75% of cases pass
JUDGE_MODEL = "claude-sonnet-4-6"
JUDGE_SYSTEM_PROMPT = "You are a harsh but fair evaluator. Return only JSON."
EVAL_CASES_PATH = Path(__file__).parent / "golden_cases.jsonl"


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


async def run_case_real(case: dict) -> ProjectState:
    """Run a case through classify + hypotheses + audit + strategy."""
    state = ProjectState(
        project_id=f"eval-{case['id']}",
        project_name=f"eval {case['id']}",
        brief=case["brief"],
        data="",
        created_at=datetime.now(),
    )
    for phase in ["classify", "hypotheses", "gauntlet", "audit", "strategy"]:
        try:
            state = await run_phase_node(state, phase)
        except Exception as e:
            # Log error but continue — judge can still evaluate partial output
            state.errors = getattr(state, "errors", [])
            state.errors.append(f"{phase}: {e}")
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
    flat = json.dumps(output, ensure_ascii=False).lower()
    required_fw = [fw.lower() for fw in case.get("must_contain_frameworks", [])]
    if required_fw:
        hits = sum(1 for fw in required_fw if fw in flat)
        r.frameworks_covered = hits / len(required_fw)
    else:
        r.frameworks_covered = 1.0

    # Must-mention / must-not-mention
    must = [m.lower() for m in case.get("strategy_must_mention", [])]
    if must:
        hits = sum(1 for m in must if m in flat)
        r.must_mention_hits = hits / len(must)
    else:
        r.must_mention_hits = 1.0

    forbidden = [m.lower() for m in case.get("strategy_must_not_mention", [])]
    r.must_not_mention_violations = sum(1 for m in forbidden if m in flat)

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
        output=json.dumps(output, default=str)[:8000],
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
        data = json.loads(resp.text.strip().strip("`"))
        return int(data.get("score", 0)), data.get("rationale", "")
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
    args = parser.parse_args()

    subset = set(args.cases.split(",")) if args.cases else None
    cases = load_cases(subset)
    print(f"Running {len(cases)} cases ({'MOCK' if args.mock else 'REAL'})")

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
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    pass_rate = passed / total if total else 0.0
    print(f"\n=== RESULT: {passed}/{total} passed ({pass_rate:.1%}) ===")

    if args.report:
        out_dir = Path(args.report)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "summary.json").write_text(json.dumps({
            "timestamp": datetime.now().isoformat(),
            "passed": passed,
            "total": total,
            "pass_rate": pass_rate,
            "threshold": args.threshold,
            "ok": pass_rate >= args.threshold,
            "cases": [asdict(r) for r in results],
        }, indent=2, default=str))
        print(f"Report written to {out_dir}/summary.json")

    if pass_rate < args.threshold:
        print(f"FAIL: pass rate {pass_rate:.1%} < threshold {args.threshold:.1%}")
        sys.exit(1)
    print("PASS")


if __name__ == "__main__":
    asyncio.run(main())
