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
| `run_evals_batch.py` | Nightly suites, post-rewrite regression sweeps, broad benchmarks | **~50% cheaper** | ~20 min pipeline pass + a bounded batch wait (default 30 min) |

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
# Full run: bounded-concurrency pipeline pass, then wait for the batch
python -m evals.run_evals_batch

# Submit only — useful for very large suites
python -m evals.run_evals_batch --submit-only
# ... later ...
python -m evals.run_evals_batch --resume <batch_id>

# Subset
python -m evals.run_evals_batch --cases G01,G03,G12

# Scheduling and wait bounds
python -m evals.run_evals_batch --concurrency 1          # one case at a time
python -m evals.run_evals_batch --batch-wait-minutes 45  # wait longer for the batch
```

The batch runner runs the same phase pipeline locally, but submits **all 12 judge calls as a single Anthropic Message Batch**. Same scoring logic, same pass criteria — just half the judge cost. Pipeline calls (classify → strategy) still run real-time because they have inter-call dependencies.

#### Completion bounds

The nightly job is capped at **90 minutes** by the workflow, and that cap is
deliberately not moved. Two bounds keep a run inside it, and both are scheduling
decisions only — the same cases run, through the same phases, with the same
product calls, and the report is always in input-case order.

| Flag | Default | What it bounds |
|---|---|---|
| `--concurrency N` | `6` | Independent cases in the pipeline at once. Must be an integer ≥ 1. |
| `--batch-wait-minutes N` | `30` | How long to wait for a submitted batch before stopping the wait. Must be an integer ≥ 1. |

**`--concurrency`.** The twelve golden cases are independent of one another —
each builds its own `ProjectState` and its own telemetry identity, and no case
reads another's state — so they are scheduled a bounded number at a time. The
five phases *within* a case (classify → hypotheses → gauntlet → audit →
strategy) genuinely depend on each other and always run strictly sequentially.

A full substantive case measures at a **~7.88 minute median**, so twelve of them
one after another centres just past the 90-minute cap: fourteen historical
nightlies were killed at exactly that cap, twelve of them before a single judge
request had even been submitted. The default of 6 is what this same corpus has
already been run at under Gate A, where it completed end to end in ~17 minutes.
`--concurrency 1` restores the old one-case-at-a-time schedule unchanged.

**`--batch-wait-minutes`.** The wait budget must stay below the job timeout,
because a wait that outlives the job cannot end in anything but a SIGKILL — and
a killed job writes no artifact at all, not even one saying it measured nothing.
Every completed batch wait observed on this suite finished in 1.5–18.7 minutes,
so 30 minutes covers the observed range while leaving roughly 40 minutes of
margin for setup and the pipeline pass.

When the budget runs out the run stops waiting **on purpose** and finishes
normally:

- the batch stays submitted, and `batch_inputs_<batch_id>.json` stays on disk;
- `summary_batch.json` is written, naming the `batch_id`;
- the observation is reported as `valid_observation: false`,
  `quality_measured: false`, `ok: false`, with validity code
  `batch_wait_budget_exhausted` and `result_class: infrastructure_failure`;
- the CLI exits non-zero.

It is classified as infrastructure, never as `provider_unavailable`: the
provider accepted the batch and may still be working on it, and nothing about
the product was measured either way. **Nothing is resubmitted automatically** —
buying a second paid batch is an operator decision. Collect the original one
with `--resume <batch_id>` from a checkout at **the same commit** the batch was
submitted from; a resume at any other commit fails closed before touching the
provider.

## CI integration

Two GitHub Actions workflows:

- **`.github/workflows/evals.yml`** — a free mock smoke job on every PR that touches `mas/prompts/**`, `mas/orchestrator.py`, `mas/llm_client.py`, `mas/config.py` or `mas/evals/**`, plus the paid Gate A release gate and retained historical Gate B compatibility harness described below.
- **`.github/workflows/evals-nightly-batch.yml`** — runs `run_evals_batch.py` on a 06:00 UTC cron. Opens a GitHub issue tagged `eval-regression` if the threshold fails, with the failing case IDs in the body.

Set the `ANTHROPIC_API_KEY` secret in repo settings before authorizing Gate A.

## Release provider gates (V7)

The supported V7 production release is **Anthropic-only**. Gate A is the release
gate, and its current release evidence is the formal Gate A run executed on
`aff8f31` and preserved in-repo (see *Canonical Gate A evidence* below). Gate B
is retained as a historical compatibility harness, but OpenAI fallback
capability is deferred and a Gate B PASS is not a V7 release requirement. The
historical Gate B FAIL remains evidence about the deferred capability; it is not
reclassified or erased.

V7 is an internal release/provider milestone inside product version `4.4.0`. It
is not a product version, and `APP_VERSION` did not change for it.

### Canonical Gate A evidence

Cite this run, not an equivalence argument.

| Field | Value |
|-------|-------|
| Evaluated runtime SHA | `aff8f31a9a0feaed8299e81a3b2a374f6b57eb8d` |
| Workflow run | [`32537157412`](https://github.com/nicogik94/v4.4/actions/runs/32537157412) — workflow `evals`, `workflow_dispatch`, conclusion `success` |
| Gate identity | `gate_a_anthropic_primary` |
| Result | PASS — 10/12, `pass_rate` `0.8333333333333334`, `threshold` `0.75`, `ok` `true` |
| Preserved artifact | [`evidence/gate-a/2026-08-21_aff8f31/`](evidence/gate-a/2026-08-21_aff8f31/) |
| Preserved at commit | `b1018028a6e1fe69b678886981911b83cf18de3b` (PR #130) |

Read [`evidence/gate-a/2026-08-21_aff8f31/README.md`](evidence/gate-a/2026-08-21_aff8f31/README.md)
before citing the result. It records the caveats that belong to this PASS —
among them the judge-input truncation count, and why `quality_ok: false` does
not contradict `ok: true`. The adjacent `summary.json` is preserved
byte-for-byte and must not be edited.

**Superseded antecedent.** Before this run, Gate A's PASS was carried forward
onto the Anthropic-only boundary by strict successful-path equivalence at
`3d6b0a9` ("V7 final"). That antecedent is **superseded as current release
evidence** by the formal run above. It is retained here as history: not erased,
not reclassified. It remains the record of how the boundary was closed before a
formal run existed on it.

**`b1018028` preserved the evidence; it is not the evaluated runtime.** The
runtime Gate A ran against is `aff8f31`, ten commits after `3d6b0a9`.

The gates share the golden-case universe, judge rubric, six-shard split and 0.75
threshold, but their artifacts and claims are never interchangeable.

| | Gate A | Gate B |
|---|---|---|
| identity | `gate_a_anthropic_primary` | `gate_b_openai_fallback` |
| Anthropic key | **present** | blank |
| OpenAI key | blank | **present** |
| preflight | `evals.anthropic_preflight` | `evals.provider_preflight` |
| V7 status | **supported; formal PASS on `aff8f31`** | **historical FAIL; capability deferred** |
| a PASS claims | the normal production posture meets the release threshold | the compatibility harness observed usable OpenAI fallback behavior at that exact candidate |
| a PASS does **not** claim | anything about fallback | **anything about the V7 release** |

Gate B is *not* V7 release validation. Its harness and evidence vocabulary remain
for historical traceability and future provider-resilience work. The supported
runtime makes OpenAI ineligible before any provider attempt, regardless of
`OPENAI_API_KEY`; therefore a blank Anthropic key now fails closed instead of
crossing into OpenAI.

Do not authorize Gate B for an Anthropic-only V7 release. Its paid-run controls
remain documented below solely to preserve the historical harness boundary and
must not be interpreted as production eligibility.

### Authorizing a live run

Selecting a gate and authorizing spend are separate acts, and a live job needs
both. Nothing is authorized by default.

* **Pull request** — the PR must be **non-Draft**, carry the **`paid-eval`**
  label, carry **exactly one** gate label (`gate-a-anthropic-primary` or
  `gate-b-openai-fallback`), *and* the event itself must be the act that
  completed that state. Both gate labels at once authorizes neither.
* **Manual dispatch** — choose `provider_gate` (default `none`, which runs
  nothing paid) **and** tick `confirm_paid_execution` (a boolean, default
  `false`).

#### The event must authorize, not just the labels

Label *presence* is sticky: once `paid-eval` and a gate label are on a Ready PR
they stay there, and a guard that tests only presence treats every later
`labeled` event as a fresh instruction to spend. Adding `documentation` to an
already-labelled PR started a full paid eval.

So `github.event.label.name` must itself be a label that **materially completes**
the authorization, which is exactly two labels per gate:

| newly added label | authorizes | only when |
|---|---|---|
| `paid-eval` | that gate | exactly one gate label was already present |
| `gate-a-anthropic-primary` | Gate A | `paid-eval` already present, Gate B label absent |
| `gate-b-openai-fallback` | Gate B | `paid-eval` already present, Gate A label absent |
| anything else | **nothing** | — |

`ready_for_review` also authorizes, but only when the PR is already in a complete
and unambiguous state at that instant.

`opened`, `synchronize`, `reopened`, `unlabeled` and `converted_to_draft`
authorize nothing, ever. A `git push` to a fully-labelled PR does not re-spend.

Re-adding a label that is already present emits no `labeled` event at all.
Removing and re-adding one does — and that is a deliberate human act on a
materially-authorizing label, so it is treated as a genuine new authorization
rather than an accident.

#### Concurrency: what may cancel what

Runs are bucketed by whether they could spend money.

| event class | concurrency group | `cancel-in-progress` |
|---|---|---|
| `labeled`, `ready_for_review`, `workflow_dispatch` | `evals-<pr>-paid-<head-sha>` | **false** |
| everything else | `evals-<pr>-routine` | true |

* An event that authorizes nothing **cannot terminate a paid gate mid-call** —
  routine events are in a different bucket, and same-SHA authorizing events
  queue rather than cancel.
* Evidence stays bound to the commit it was measured on: a new authorization on
  a **new** head SHA gets its own group and its own run, so an older run's
  artifacts are never silently replaced by a newer commit's results under the
  same identity.
* Only the free mock smoke job runs under a routine event, so cancelling those
  costs nothing.

#### Manual dispatch is available

`provider_gate` and `confirm_paid_execution` are both defined in the `evals`
workflow on `main`, so `workflow_dispatch` is a usable route for authorizing a
gate: the operator must **explicitly select a gate** and **confirm the spend**.
Selecting a gate without confirming payment is a dry selection and runs nothing
paid.

The defaults remain the safe ones — `provider_gate: none` and
`confirm_paid_execution: false` — so a dispatch that just accepts the form
authorizes nothing.

`workflow_dispatch` inputs are still read from the workflow file on the
**default branch**. A future change that introduces a *new* input cannot use it
via dispatch until that input exists on `main`; until then, such a change is
authorized by the PR-event route above. Either way the guard fails closed: an
unsupplied gate input is not a gate identity, so nothing paid runs.

#### Threshold is data, never code

`threshold` reaches the runner as a quoted environment value (`$EVAL_THRESHOLD`),
never interpolated into shell source, and is validated by
`python -m evals.release_gates --validate-threshold` **before** any provider
credential is in scope. Only a plain decimal in `[0.0, 1.0]` is accepted; `nan`,
`inf`, shell metacharacters and command substitutions are refused as
configuration errors. The rejected value is never echoed into a log. The release
default remains `0.75`.

#### Operational precondition: the nightly Anthropic cron

`.github/workflows/evals-nightly-batch.yml` runs `evals.run_evals_batch` against
`secrets.ANTHROPIC_API_KEY` on a `0 6 * * *` schedule. It is a **separate
workflow with no concurrency group**, so it neither cancels nor is cancelled by
a release gate — but it does spend on the same Anthropic account.

Before authorizing a live Gate A run, confirm no nightly batch run is active or
imminent. Disabling or cancelling that automation is a **separate act requiring
its own authorization**; it is not part of gate authorization and must not be
done implicitly.

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

#### Canonical formal Gate A evidence

The Gate A (Anthropic primary) run for `aff8f31` (2026-08-21) is preserved verbatim under
[`evidence/gate-a/2026-08-21_aff8f31/`](evidence/gate-a/2026-08-21_aff8f31/): a PASS at
10/12 (83.3%) against the 75% threshold, with `provider_gate: gate_a_anthropic_primary`,
a passed provider preflight, six complete shards and a clean aggregate. This is formal
release gate evidence — distinct from the nightly batch evidence below.

#### Canonical nightly batch evidence

The `evals-nightly-batch` observation for `4a9cc77` (2026-08-21) is preserved verbatim
under [`evidence/nightly/2026-08-21_4a9cc77/`](evidence/nightly/2026-08-21_4a9cc77/):
a valid, quality-measured run at 9/12 (75%). It carries no `provider_gate` and ran no
provider preflight, so it is nightly quality evidence only — not a release gate result.

#### Three identities, three sources

Requested, selected and provider-observed identity are different facts, and the
artifact keeps them apart:

| field | source | shape |
|---|---|---|
| `requested_provider` / `requested_model` | configuration / the request | plain string |
| `selected_provider` / `selected_model` | gateway routing decision | plain string |
| `used_provider` | gateway's record of which provider it called | plain string |
| `effective_model` | **provider observation only** | value envelope |
| `stop_reason`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `reasoning_tokens`, `content_status`, `visible_content_length`, `refusal_status` | **provider observation only** | value envelope |

`LLMResponse.model_used` is **not** provider evidence — every construction site
in both adapters sets it from the *requested* model, and the gateway re-asserts
it from `config.model`. Reading it as an effective model reported the request
back as though the provider had confirmed it, so no provider-observed field is
sourced from it.

When the provider supplies no model identity, `effective_model` carries the
epistemic status (`absent`, `null`, `unsupported`, `invalid`, `unknown`) and a
`value` of `null`. **There is no fallback to the requested or selected model.**

There is no `effective_provider`: no provider echoes its own identity, so
naming one would be a claim nothing supports. The runtime's own routing record
is `used_provider`, and it is described as exactly that.

Usage counters are envelopes for the same reason — `LLMResponse` defaults them
to `0`, which made "never reached a provider" indistinguishable from a genuine
zero. An observed zero is still reported as `{"status": "valid", "value": 0}`.

## Calibration philosophy

The eval harness is intentionally **not** a unit test for single agents. It exercises the full pipeline (classify → hypotheses → gauntlet → audit → strategy) because the interesting regressions live in the handoffs. A prompt change that makes classify 5% better but breaks the audit's data-labeling discipline is a net regression — and this suite catches it.

## Adding new cases

Append a JSON object to `golden_cases.jsonl` with the same schema. Keep briefs realistic — prefer anonymized real client scenarios over synthetic toys. Aim for 1–2 new cases per month as the system accumulates real engagements.

To exercise citation-resolvability, add a small `citation_resolvability_fixture` with `report`, optional `knowledge_items`, and an `expected_status`. Keep these fixtures tiny and deterministic; they are review-only traceability checks, not semantic-support labels.

## Cost control

Full runs cost ~$2–4 each (9 real phase executions × 12 cases × Opus/Sonnet mix). On PRs that touch only non-prompt files, CI skips the suite. For long iteration sessions, use `--mock` to validate plumbing before burning tokens on the real run.
