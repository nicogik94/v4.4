# Gate A evidence — 2026-08-21 — `4a9cc77`

Canonical, immutable record of the Gate A nightly batch observation for commit
`4a9cc77`. The adjacent `summary_batch.json` is preserved byte-for-byte as
emitted by the run; do not edit it.

## Run

| Field | Value |
|-------|-------|
| Execution date | 2026-08-21 |
| Evaluated SHA | `4a9cc77592b0b14a18f065a7c45920c9282fe528` |
| Workflow run | https://github.com/nicogik94/v4.4/actions/runs/32504996111 |
| Batch ID | `msgbatch_0143K6riMWW86btsDaVgGxdU` |

## Observation

| Field | Value |
|-------|-------|
| `valid_observation` | `true` |
| `quality_measured` | `true` |
| `result_class` | `pass` |

## Result

| Field | Value |
|-------|-------|
| Passed | 9 / 12 (75%) |
| `pass_rate` | `0.75` |
| `threshold` | `0.75` |
| Failed cases | `G01`, `G05`, `G09` |

## Integrity

`summary_batch.json` SHA-256:

```
32fa069894347616918dd32d985be40ef324e52518c6c6cf13da59dd837882d9
```

Verify with:

```bash
sha256sum mas/evals/evidence/gate-a/2026-08-21_4a9cc77/summary_batch.json
```

## Conclusion

Gate A PASS válido y atribuible, aunque sin margen sobre el umbral.

The observation was valid and the quality signal was actually measured, so the
result is attributable to `4a9cc77` rather than inferred. However, `pass_rate`
equals `threshold` exactly — a single additional case regression would drop the
gate below the bar. Improving `G01`, `G05` and `G09` is tracked separately.
