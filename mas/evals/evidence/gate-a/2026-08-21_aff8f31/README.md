# Gate A evidence — 2026-08-21 — `aff8f31`

Canonical, immutable record of the **formal release Gate A (Anthropic primary)** run for
commit `aff8f31`. The adjacent `summary.json` is preserved byte-for-byte as emitted by the
gate's aggregate job; do not edit it.

**Result: PASS — 10/12 (83.3%) against a 75% threshold.**

## Run

| Field | Value |
|-------|-------|
| Execution date | 2026-08-21 |
| Evaluated head SHA | `aff8f31a9a0feaed8299e81a3b2a374f6b57eb8d` |
| Workflow | `evals` (`workflow_dispatch`) |
| Workflow run | https://github.com/nicogik94/v4.4/actions/runs/32537157412 |
| Run conclusion | `success` |
| Aggregate artifact | `eval-report-gate-a-anthropic-primary` (ID `9466094020`) |
| Artifact URL | https://github.com/nicogik94/v4.4/actions/runs/32537157412/artifacts/9466094020 |
| Aggregate `timestamp` | `2026-08-21T23:48:00.501428` |

## Gate identity

| Field | Value |
|-------|-------|
| `mode` | `aggregate` |
| `provider_gate` | `gate_a_anthropic_primary` |

The artifact carries the gate identity, so it cannot be confused with a nightly batch
observation or absorbed as the wrong gate: `release_gates.evaluate_gate_outcome` rejects a
summary whose `provider_gate` does not match as `gate_identity_mismatch`.

## Pipeline completeness

| Job | Conclusion |
|-----|------------|
| `Gate A (Anthropic primary) - provider preflight` | `success` |
| `Gate A (Anthropic primary) - eval shard (0…5)` | `success` (all six) |
| `Gate A (Anthropic primary) - aggregate` | `success` |

The formal provider preflight passed, all **six shards** completed, and the aggregate ran
over the full set. All 12 case IDs (`G01`…`G12`) are present in the aggregate.

## Clean aggregation

| Field | Value |
|-------|-------|
| `aggregation_errors` | `[]` |
| `aggregate_failure_kind` | `none` |
| `provider_failure_count` | `0` |
| `provider_failure_case_ids` | `[]` |
| `eval_provenance.cases_with_structural_failure` | `0` |

No aggregation error, no provider failure, no structural failure. Nothing provider-side or
infrastructural was counted as a quality signal.

## Result

| Field | Value |
|-------|-------|
| Passed | 10 / 12 |
| `pass_rate` | `0.8333333333333334` (83.3%) |
| `threshold` | `0.75` |
| `ok` | `true` |
| `quality_failure_case_ids` | `G01`, `G09` |
| `quality_failure_count` | `2` |

The two failing cases are `G01` (HVAC SEO→GEO pivot) and `G09` (acquisition
accept/counter/walk). Margin over the bar is one case: 8.3 points, i.e. the gate would
still pass at 9/12 = 75% but not below.

## Precisions

**SHA attribution.** `summary.json` carries `provider_gate`, but it has **no `source_sha`
field**. The attribution of this evidence to `aff8f31a9a0feaed8299e81a3b2a374f6b57eb8d`
comes from the immutable `head_sha` of workflow run `32537157412` and from the artifact's
own run metadata (artifact `9466094020` → run `32537157412` → `head_sha aff8f31…`), not
from any field inside the JSON.

**`quality_ok: false` does not contradict `ok: true`.** `quality_ok` is `false` because two
individual cases failed their checks (`G01`, `G09`); the artifact's own
`quality_evaluation_note` reads *"One or more eval cases failed deterministic checks or
non-provider judge scoring."* The gate decision `ok: true` is a separate fact: it is the
threshold comparison, and 83.3% ≥ 75%. A gate can pass with individual case failures — that
is what a pass-rate threshold means.

**Judge-input truncation caveat.** `eval_provenance.judge_inputs_truncated_count` is `11`.
This is preserved as a caveat on the run: judge inputs were truncated for 11 of the 70
recorded invocations, so judge scores were produced over shortened inputs. Provenance is
observational only — per the artifact's own `scoring_notice`, it does not alter
`passed`, `total`, `pass_rate`, `threshold` or `ok`, and no corrected pass rate is derived
from it. It is recorded here so the PASS is read with it in view, not to qualify the
decision.

**No `valid_observation`.** That field belongs to the nightly batch contract
(`nightly_batch_observation.v1`) and is **absent** from this Gate A aggregate. It is not
used, inferred, or substituted here. Gate A validity is established by the gate identity,
the passed preflight, the six complete shards and the empty `aggregation_errors` — not by a
nightly field.

## Integrity

`summary.json` SHA-256:

```
b7e21d446afc66b919a9b379a7ee839572da19ca98b3c3c31f40f565857622b0
```

Source artifact ZIP SHA-256 (`eval-report-gate-a-anthropic-primary`, 16687 bytes,
containing exactly one entry, `summary.json`):

```
27da75771dce742f7d70916fb43b864c7eefbb73b8c1ab7ef4f149245ca17b8d
```

Verify the preserved file with:

```bash
sha256sum mas/evals/evidence/gate-a/2026-08-21_aff8f31/summary.json
```
