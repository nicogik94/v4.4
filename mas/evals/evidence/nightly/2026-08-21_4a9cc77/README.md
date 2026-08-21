# Nightly batch evidence — 2026-08-21 — `4a9cc77`

Canonical, immutable record of the **nightly batch quality observation** for commit
`4a9cc77`, produced by the `evals-nightly-batch` workflow. The adjacent
`summary_batch.json` is preserved byte-for-byte as emitted; do not edit it.

> **This is not Gate A evidence.** See [What this is not](#what-this-is-not).

## Run

| Field | Value |
|-------|-------|
| Execution date | 2026-08-21 |
| Evaluated SHA | `4a9cc77592b0b14a18f065a7c45920c9282fe528` |
| Workflow | `evals-nightly-batch` |
| Workflow run | https://github.com/nicogik94/v4.4/actions/runs/32504996111 |
| Batch ID | `msgbatch_0143K6riMWW86btsDaVgGxdU` |
| `schema_version` | `nightly_batch_observation.v1` |

## Observation validity

| Field | Value |
|-------|-------|
| `valid_observation` | `true` |
| `quality_measured` | `true` |
| `validity_errors` | `[]` |
| `result_class` | `pass` |

The observation is valid and the quality dimension was genuinely measured: all 12
expected cases were observed, and the run did not degrade into a provider,
preflight, or infrastructure failure being misread as a quality signal. The
artifact records the SHA it evaluated, so the result is **attributable** to
`4a9cc77` rather than inferred from historical equivalence.

## Result

| Field | Value |
|-------|-------|
| Passed | 9 / 12 (75%) |
| `pass_rate` | `0.75` |
| `threshold` | `0.75` |
| Failed cases | `G01`, `G05`, `G09` |

`pass_rate` equals the nightly `threshold` exactly — zero margin. One further case
regression would put this observation below its bar.

## What this is

A valid, SHA-attributable **nightly quality observation**. It is legitimate evidence
of measured suite quality at `4a9cc77` and can be cited as such.

## What this is not

This artifact does **not** satisfy the formal release Gate A contract, and must not
be presented as release evidence:

- **No `provider_gate`.** The artifact carries no gate identity. `release_gates.evaluate_gate_outcome`
  rejects a summary without a matching `provider_gate` as `gate_identity_mismatch`.
- **No formal provider preflight.** `evals-nightly-batch` invokes `run_evals_batch`
  without selecting `MAS_PROVIDER_GATE` or running the gate's provider preflight.
- **No Gate A aggregate.** The gate's sharded aggregation step (with `--expect-gate`
  and `--expect-shard-count`) never ran, so no gate aggregate exists.
- **Different threshold semantics.** The 75% bar here is the nightly threshold, not a
  release gate decision.

The formal Gate A attempt at this same SHA —
[run 32524308407](https://github.com/nicogik94/v4.4/actions/runs/32524308407) —
failed at the Anthropic provider preflight (`ANTHROPIC_PROVIDER_PREFLIGHT=FAIL`,
`TypeError`, under `anthropic 1.0.0`), with all shard and aggregate jobs skipped.
No Gate A artifact exists for `4a9cc77`.

## Integrity

`summary_batch.json` SHA-256:

```
32fa069894347616918dd32d985be40ef324e52518c6c6cf13da59dd837882d9
```

Verify with:

```bash
sha256sum mas/evals/evidence/nightly/2026-08-21_4a9cc77/summary_batch.json
```

## Conclusion

Valid nightly observation, attributable to `4a9cc77`, with quality genuinely
measured at 9/12 (75%) — useful as quality evidence, but **not** equivalent to Gate A
and **not** release evidence.
