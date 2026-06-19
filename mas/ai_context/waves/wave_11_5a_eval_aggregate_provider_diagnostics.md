# Wave 11.5A — Eval Aggregate Provider-Failure Diagnostics

## Goal

Classify eval aggregate failures so external LLM-judge provider/quota failures are distinguishable from real eval-quality regressions and aggregation/schema failures.

## Incident Observed From PR #66

Wave 11.5 / PR #66 passed local verification and shard execution, but the GitHub `evals/aggregate` PR check failed after judge calls returned provider quota errors such as:

`judge error: Provider call failed: category=quota_exceeded, provider=anthropic, model=claude-sonnet-4-6`

That failure mode is external provider availability, not deterministic eval/schema/code quality.

## Scope

- Add aggregate diagnostics to `evals/run_evals.py`.
- Detect conservative provider/quota/rate-limit/unavailable judge failures.
- Add aggregate failure kind classification:
  - `none`
  - `eval_quality_failure`
  - `provider_unavailable`
  - `aggregation_error`
  - `mixed_failure`
- Allow aggregate CLI success only for provider-unavailable-only aggregate failures.
- Add focused mock/offline tests for aggregate classification.
- Update eval documentation and progress context.

## Non-Goals

- No changes to workflow or orchestrator behavior.
- No runtime/job changes.
- No report or export behavior changes.
- No readiness behavior changes.
- No semantic evidence verification.
- No Evidence Gauge.
- No Defense Index.
- No Claim Cards.
- No automatic delivery approval.
- No public SaaS or multi-tenant behavior.
- No autonomous monitoring or action behavior.

## Files Changed

- `evals/run_evals.py`
- `evals/README.md`
- `tests/test_evals_mock.py`
- `ai_context/waves/wave_11_5a_eval_aggregate_provider_diagnostics.md`
- `ai_context/v4_v5_current_progress.md`

## Tests

Focused mock/offline tests cover:

- provider quota-only aggregate failure classified as `provider_unavailable`
- provider quota-only aggregate not labeled as eval-quality failure
- provider quota plus any failed case without provider rationale remains a mixed failure
- aggregation/schema errors remain failing
- claim-traceability failures remain real eval-quality failures
- provider failure count/category fields are present
- aggregate diagnostics avoid semantic-support and delivery-approval language

## CI Behavior

If aggregate failure is provider-unavailable-only, the aggregate command exits successfully after writing diagnostics. The summary still records `ok: false`, `quality_ok: "unknown"`, and `provider_unavailable: true` because the judge did not fully evaluate quality.

The aggregate command still fails for aggregation errors, deterministic failures on cases without provider-failure rationale, claim-traceability failures without provider-failure rationale, schema failures, mixed provider/quality failures, and real eval-quality regressions. Deterministic false fields on the same cases as explicit provider/quota judge failures are treated as unavailable quality judgment, not separate quality failures.

## Caveats

Provider-failure detection is conservative and based on explicit judge/provider failure strings. It does not classify arbitrary failed cases as provider failures.

## Human Review Boundary

Human review remains mandatory. This patch improves CI diagnostics only; it does not approve delivery, weaken evidence review, or certify claim quality.
