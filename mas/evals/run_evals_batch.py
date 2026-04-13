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
"""
import os
import sys
import json
import time
import asyncio
import argparse
import logging
from pathlib import Path
from datetime import datetime
from dataclasses import asdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.run_evals import (
    load_cases, run_case_real, run_case_mock,
    score_deterministic, pass_fail, JUDGE_PROMPT_TEMPLATE,
    JUDGE_MODEL, PASS_THRESHOLD,
)
from state import ProjectState

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("evals_batch")


def build_batch_requests(cases_with_outputs: list[tuple[dict, dict]]) -> list[dict]:
    """Build the message batch payload — one request per case."""
    requests = []
    for case, output in cases_with_outputs:
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


async def collect_batch_results(batch_id: str) -> dict[str, tuple[int, str]]:
    """Stream results out of a finished batch. Returns {custom_id: (score, rationale)}."""
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    out: dict[str, tuple[int, str]] = {}
    async for entry in await client.messages.batches.results(batch_id):
        cid = entry.custom_id
        if entry.result.type != "succeeded":
            out[cid] = (0, f"batch error: {entry.result.type}")
            continue
        try:
            text = entry.result.message.content[0].text
            data = json.loads(text.strip().strip("`"))
            out[cid] = (int(data.get("score", 0)), data.get("rationale", ""))
        except Exception as e:
            out[cid] = (0, f"parse error: {e}")
    return out


async def run_pipeline_for_all_cases(cases: list[dict], mock: bool) -> list[tuple[dict, dict]]:
    """Run every case through the real (or mock) phase pipeline."""
    cases_with_outputs = []
    for case in cases:
        logger.info(f"  pipeline [{case['id']}] {case['brief'][:60]}...")
        try:
            if mock:
                output = await run_case_mock(case)
            else:
                state = await run_case_real(case)
                output = state.model_dump(mode="json")
            cases_with_outputs.append((case, output))
        except Exception as e:
            logger.error(f"  ERROR on {case['id']}: {e}")
            cases_with_outputs.append((case, {"error": str(e)}))
    return cases_with_outputs


def write_report(results: list, out_dir: Path, batch_id: str | None = None):
    out_dir.mkdir(parents=True, exist_ok=True)
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    pass_rate = passed / total if total else 0.0
    summary = {
        "timestamp": datetime.now().isoformat(),
        "mode": "batch",
        "batch_id": batch_id,
        "passed": passed,
        "total": total,
        "pass_rate": pass_rate,
        "threshold": PASS_THRESHOLD,
        "ok": pass_rate >= PASS_THRESHOLD,
        "cases": [asdict(r) for r in results],
    }
    (out_dir / "summary_batch.json").write_text(json.dumps(summary, indent=2, default=str))
    logger.info(f"Wrote {out_dir}/summary_batch.json")
    return summary


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

    # Resume path: skip pipeline + submission, just collect
    if args.resume:
        logger.info(f"Resuming batch {args.resume}")
        await wait_for_batch(args.resume, args.poll_interval)
        judge_results = await collect_batch_results(args.resume)
        # Need the original outputs to score deterministically — load from cache file
        cache_file = out_dir / f"batch_inputs_{args.resume}.json"
        if not cache_file.exists():
            logger.error(f"No cached inputs for batch {args.resume} at {cache_file}")
            sys.exit(2)
        cached = json.loads(cache_file.read_text())
        cases_with_outputs = [(c["case"], c["output"]) for c in cached]
    else:
        subset = set(args.cases.split(",")) if args.cases else None
        cases = load_cases(subset)
        logger.info(f"Pipeline pass: {len(cases)} cases ({'MOCK' if args.mock else 'REAL'})")

        cases_with_outputs = await run_pipeline_for_all_cases(cases, args.mock)

        if args.mock:
            judge_results = {
                case["id"]: (70 if (out.get("classify") or {}).get("domain") == case["expected_domain"] else 40, "mock")
                for case, out in cases_with_outputs
            }
        else:
            requests = build_batch_requests(cases_with_outputs)
            batch_id = await submit_batch(requests)

            # Cache inputs so --resume can score them later
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"batch_inputs_{batch_id}.json").write_text(json.dumps(
                [{"case": c, "output": o} for c, o in cases_with_outputs], default=str
            ))

            if args.submit_only:
                logger.info(f"Submitted. Resume with: --resume {batch_id}")
                return

            await wait_for_batch(batch_id, args.poll_interval)
            judge_results = await collect_batch_results(batch_id)

    # Score
    results = []
    for case, output in cases_with_outputs:
        r = score_deterministic(case, output)
        score, rationale = judge_results.get(case["id"], (0, "no judge result"))
        r.judge_overall = score
        r.judge_rationale = rationale
        r.passed = pass_fail(r)
        results.append(r)

    summary = write_report(results, out_dir, batch_id=args.resume)
    print(f"\n=== BATCH RESULT: {summary['passed']}/{summary['total']} "
          f"({summary['pass_rate']:.1%}) ===")

    if not summary["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
