# v4.1 Eval Harness

Regression test suite for the MAS. Guards every change to `prompts/router.md` and `prompts/phases/*.md` against quality drift.

## What it tests

12 golden decision cases spanning the realistic distribution of v4 engagements:

| ID  | Scenario | Domain |
|-----|----------|--------|
| G01 | HVAC SEO→GEO pivot (HappyHiller) | Complex |
| G02 | SaaS founder pivot-or-persist | Complex |
| G03 | Decision Audit Lite price anchor | Complicated |
| G04 | MOF Hamilton-syringe methodology | Complicated |
| G05 | First Choice Florence SC market entry | Complicated |
| G06 | Solo-consultant wedge vs full-engagement pricing | Complicated |
| G07 | Ship-vs-delay bug decision | Simple |
| G08 | E-commerce conversion collapse | Chaotic |
| G09 | Acquisition accept/counter/walk | Complex |
| G10 | Drone delivery regulatory policy | Complex |
| G11 | Academic manuscript acceptance | Complicated |
| G12 | Nonsense brief ("hi") — should halt | Confused |

Cases cover every Cynefin domain including Confused (G12, which tests the "halt and request clarification" path). Real client contexts are preserved so the harness catches regressions that matter for live engagements.

## Scoring

Each case is scored on **eight deterministic checks** plus an **LLM judge** (Sonnet 4.6):

1. `domain_match` — classify agent picked the expected Cynefin domain
2. `hypothesis_count_ok` — within the expected range (usually 8–12)
3. `frameworks_covered ≥ 0.75` — at least 75% of required frameworks appear in the trace
4. `must_mention_hits ≥ 0.66` — at least 66% of expected strategy concepts surface
5. `must_not_mention_violations == 0` — no red-flag phrases ("guaranteed ranking", "trust your gut", etc.)
6. `data_labeling_correct` — audit phase honestly labels PREDICTED vs MEASURED
7. `citation_resolvability_ok` — CDP citation-resolvability fixture, when present, matches the expected traceability status
8. `judge_overall ≥ 65` — LLM judge gives at least 65/100

A case passes only if **all eight** are satisfied. The suite passes overall if **≥75% of cases pass**. Below that, CI fails and prevents merge.

### Citation Resolvability Dimension

The `citation_resolvability` dimension is deterministic and offline. It reuses `cdp.citation_resolvability.build_defense_pass_result` and reports:

- `score`
- marker and resolver-status counts
- `unresolved_count`
- `status`
- CDP caveats

This dimension is resolvability / traceability only. It does not verify semantic evidence support, does not prove full claim defensibility, does not approve delivery, and does not implement Evidence Gauge, Defense Index, or Claim Cards.

Fixture status meanings:

- `pass` — markers resolve exactly to registered evidence locator metadata.
- `partial` — markers are known but ID-only or otherwise require operator review.
- `fail` — unresolved, locator-mismatched, or malformed markers are present.
- `no_markers` — no citation markers were found; this is not evidence of semantic support.
- `unknown` — required report or evidence-registry inputs are missing.
- `not_applicable` — no citation-resolvability fixture or report output was supplied for that eval case.

## Two runners, two purposes

| Runner | When to use | Cost | Latency |
|---|---|---|---|
| `run_evals.py` | PR feedback, iterative dev, single-case debugging | ~$2–4 / full suite | ~3 minutes |
| `run_evals_batch.py` | Nightly suites, post-rewrite regression sweeps, broad benchmarks | **~50% cheaper** | minutes to 24h (Anthropic Batch API SLA) |

The split is deliberate: keep the dev loop tight on PRs, and let cost dominate on big runs.

### Real-time runner (PR loop)

```bash
# Full run
python -m evals.run_evals

# Subset
python -m evals.run_evals --cases G01,G03,G12

# Mock mode (no LLM calls, tests plumbing only)
python -m evals.run_evals --mock

# Write per-case JSON reports
python -m evals.run_evals --report evals/out/
```

### Aggregate Diagnostics

Shard aggregation classifies aggregate failures explicitly:

- `none` — aggregate passed.
- `eval_quality_failure` — deterministic checks or non-provider judge scoring failed.
- `provider_unavailable` — every failed case has an explicit provider/quota/rate-limit/unavailable judge rationale and there are no aggregation errors.
- `aggregation_error` — shard reports were missing, duplicated, malformed, or incomplete.
- `mixed_failure` — provider failures and real eval/aggregation failures appeared together.

Aggregate summaries include `provider_failure_count`, `provider_failure_categories`, `provider_failure_detected`, `provider_failure_only`, `provider_unavailable`, `aggregate_failure_kind`, and `quality_ok`.

CI does not treat a provider-unavailable-only aggregate as an eval-quality regression. It still writes `ok: false` and `quality_ok: "unknown"` because the judge did not fully evaluate quality. Deterministic false fields on those same provider-failed cases are not treated as separate quality failures. Aggregation errors, deterministic failures on cases without provider-failure rationale, claim-traceability failures without provider-failure rationale, schema failures, mixed failures, and real quality regressions remain blocking.

### Batch runner (nightly + regression sweeps)

```bash
# Full run, wait for completion (typically minutes for 12 cases)
python -m evals.run_evals_batch

# Submit only — useful for very large suites
python -m evals.run_evals_batch --submit-only
# ... later ...
python -m evals.run_evals_batch --resume <batch_id>

# Subset
python -m evals.run_evals_batch --cases G01,G03,G12
```

The batch runner runs the same phase pipeline locally, but submits **all 12 judge calls as a single Anthropic Message Batch**. Same scoring logic, same pass criteria — just half the judge cost. Pipeline calls (classify → strategy) still run real-time because they have inter-call dependencies.

## CI integration

Two GitHub Actions workflows:

- **`.github/workflows/evals.yml`** — runs `run_evals.py` (real-time) on every PR that touches `mas/prompts/**`, `mas/orchestrator.py`, `mas/llm_client.py`, or `mas/config.py`. Fast feedback, fails build if pass rate < 75%.
- **`.github/workflows/evals-nightly-batch.yml`** — runs `run_evals_batch.py` on a 06:00 UTC cron. Opens a GitHub issue tagged `eval-regression` if the threshold fails, with the failing case IDs in the body.

Set the `ANTHROPIC_API_KEY` secret in repo settings before enabling either.

## Calibration philosophy

The eval harness is intentionally **not** a unit test for single agents. It exercises the full pipeline (classify → hypotheses → gauntlet → audit → strategy) because the interesting regressions live in the handoffs. A prompt change that makes classify 5% better but breaks the audit's data-labeling discipline is a net regression — and this suite catches it.

## Adding new cases

Append a JSON object to `golden_cases.jsonl` with the same schema. Keep briefs realistic — prefer anonymized real client scenarios over synthetic toys. Aim for 1–2 new cases per month as the system accumulates real engagements.

To exercise citation-resolvability, add a small `citation_resolvability_fixture` with `report`, optional `knowledge_items`, and an `expected_status`. Keep these fixtures tiny and deterministic; they are review-only traceability checks, not semantic-support labels.

## Cost control

Full runs cost ~$2–4 each (9 real phase executions × 12 cases × Opus/Sonnet mix). On PRs that touch only non-prompt files, CI skips the suite. For long iteration sessions, use `--mock` to validate plumbing before burning tokens on the real run.
