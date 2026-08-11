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

### Failure provenance (`eval_provenance.v1`)

A pass rate is only interpretable if you can tell a case that reasoned badly
from a case whose model returned nothing to reason with. Reports therefore carry
an **observational** provenance block: `summary.eval_provenance` at the top
level, and `provenance` on each case.

It is off by default. `MAS_EVAL_PROVENANCE=1` enables it, and CI sets it on the
real eval shard step only. When it is off, or in `--mock`, the block is present
and says `captured: false` — never a clean-looking record of nothing.

Per phase it records: whether the phase started, its final status
(`completed` / `structural_failure` / `skipped` / `expected_halt` / `unknown`),
the first response's content status and stop reason, whether a structured repair
was issued and whether it worked, the failure kind from a closed vocabulary, and
whether the eval kept running afterwards. Per provider invocation it records
provider, requested and effective model, candidate/retry ordinals, stop reason,
input/output/cache tokens, reasoning tokens where the SDK reports them, and the
response's content and refusal **status**. The judge record adds its requested
and effective configuration and the pre/post-truncation length of its input.

Two rules govern what is stored. **Only metadata**: never a prompt, a response,
a refusal, reasoning text, a header, a credential, or a digest of any of them —
visible output is a status plus a character count, a refusal is a status alone.
**Only observation**: nothing in the block is read by `pass_fail`, `passed`,
`total`, `pass_rate`, `threshold` or `ok`, and no corrected, adjusted or
counterfactual pass rate is derived from it. Fields this build cannot observe
without changing a certified runtime file — the orchestrator's deterministic
Strategy payload repair, for instance — are recorded as `unknown` with a reason
rather than guessed. Summaries written before this wave aggregate unchanged.

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

- **`.github/workflows/evals.yml`** — a free mock smoke job on every PR that touches `mas/prompts/**`, `mas/orchestrator.py`, `mas/llm_client.py`, `mas/config.py` or `mas/evals/**`, plus two **paid release gates** described below.
- **`.github/workflows/evals-nightly-batch.yml`** — runs `run_evals_batch.py` on a 06:00 UTC cron. Opens a GitHub issue tagged `eval-regression` if the threshold fails, with the failing case IDs in the body.

Set the `ANTHROPIC_API_KEY` secret in repo settings before enabling either.

## Release provider gates (V7)

Release validation runs as **two gates that make two different claims**. They
share the golden-case universe, the judge rubric, the six-shard split and the
0.75 threshold — so their results are comparable — but a PASS means something
different in each, and their artifacts are never interchangeable.

| | Gate A | Gate B |
|---|---|---|
| identity | `gate_a_anthropic_primary` | `gate_b_openai_fallback` |
| Anthropic key | **present** | blank |
| OpenAI key | blank | **present** |
| preflight | `evals.anthropic_preflight` | `evals.provider_preflight` |
| a PASS claims | the normal production posture meets the release threshold | the certified contract stays usable on the OpenAI fallback path |
| a PASS does **not** claim | anything about fallback | **anything about the release** |

Gate B is *not* release validation. It exists because a blank Anthropic key
makes every Anthropic candidate unavailable, which is the only way to reach the
OpenAI fallback candidates without changing routing code.

### Authorizing a live run

Selecting a gate and authorizing spend are separate acts, and a live job needs
both. Nothing is authorized by default.

* **Pull request** — the PR must be **non-Draft**, carry the **`paid-eval`**
  label, and carry **exactly one** gate label (`gate-a-anthropic-primary` or
  `gate-b-openai-fallback`). Both gate labels at once authorizes neither.
* **Manual dispatch** — choose `provider_gate` (default `none`, which runs
  nothing paid) **and** type `RUN-PAID` into `confirm_paid_execution`.

Each preflight probes exactly the models that gate's provider can actually be
routed to, derived from the routing tables by
`release_gates.required_models()` rather than restated, and requires **usable
visible text** from each — not merely that the SDK call returned.

### Reading the result

`summary.json` carries `provider_gate`, and the aggregator is told which gate it
is aggregating (`--expect-gate`) and how many shards must have contributed
(`--expect-shard-count`). A shard from the other gate is refused as an
aggregation error rather than silently absorbed, so a Gate B artifact can never
be counted as Gate A evidence.

Results use a closed taxonomy that never reports provider or infrastructure
inability as a quality failure: `pass`, `quality_failure`,
`provider_unavailable`, `preflight_failure`, `structural_failure`,
`infrastructure_failure`, `authorization_not_satisfied`,
`gate_identity_mismatch`.

Shard runs set `MAS_EVAL_PROVENANCE=1`, so a failing gate can be attributed:
effective model, stop reason, visible-content status and length, refusal status
and reasoning tokens where the provider reports them. Reasoning-token counts are
recorded only when observed — an unreported count stays `unknown`, never `0`.
No prompt, response, refusal or reasoning **text** is ever recorded.

## Calibration philosophy

The eval harness is intentionally **not** a unit test for single agents. It exercises the full pipeline (classify → hypotheses → gauntlet → audit → strategy) because the interesting regressions live in the handoffs. A prompt change that makes classify 5% better but breaks the audit's data-labeling discipline is a net regression — and this suite catches it.

## Adding new cases

Append a JSON object to `golden_cases.jsonl` with the same schema. Keep briefs realistic — prefer anonymized real client scenarios over synthetic toys. Aim for 1–2 new cases per month as the system accumulates real engagements.

To exercise citation-resolvability, add a small `citation_resolvability_fixture` with `report`, optional `knowledge_items`, and an `expected_status`. Keep these fixtures tiny and deterministic; they are review-only traceability checks, not semantic-support labels.

## Cost control

Full runs cost ~$2–4 each (9 real phase executions × 12 cases × Opus/Sonnet mix). On PRs that touch only non-prompt files, CI skips the suite. For long iteration sessions, use `--mock` to validate plumbing before burning tokens on the real run.
