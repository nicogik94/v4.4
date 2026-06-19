# Wave 11.5 — Evidence Quality + Claim-Defensibility Eval Dimension

## Goal

Add a deterministic eval dimension for CDP citation-resolvability discipline so evidence-review traceability quality cannot silently regress.

The dimension remains resolvability / traceability only. It does not verify semantic evidence support and does not prove full claim defensibility.

## Scope

- Add a `citation_resolvability` eval summary to `evals/run_evals.py`.
- Reuse `cdp.citation_resolvability.build_defense_pass_result`.
- Preserve CDP caveats from `cdp.review_caveats`.
- Add compact golden-case fixtures for exact, ID-only, unknown-ID, malformed-marker, and no-marker behavior.
- Add focused deterministic tests in the eval test suite.
- Update eval documentation.

## Non-Goals

- No semantic evidence verification.
- No full claim-defensibility proof.
- No automatic delivery approval.
- No Evidence Gauge.
- No Defense Index.
- No Claim Cards.
- No workflow routing changes.
- No report or export behavior changes.
- No readiness behavior changes.
- No runtime/job changes.
- No auth/preflight changes.
- No public SaaS or multi-tenant behavior.
- No autonomous monitoring/action behavior.

## Files Changed

- `evals/run_evals.py`
- `evals/README.md`
- `evals/golden_cases.jsonl`
- `tests/test_evals_mock.py`
- `ai_context/waves/wave_11_5_claim_defensibility_evals.md`
- `ai_context/v4_v5_current_progress.md`

## Tests

Focused tests cover:

- dimension presence in eval output
- exact resolver status improving/passing the dimension
- ID-only resolution producing partial warning status, not semantic-support language
- unknown evidence IDs and malformed markers degrading the dimension
- no-marker cases not overclaiming success
- mock eval path compatibility
- CDP caveat preservation
- absence of overclaiming metric/field names

Wave verification remains `scripts/wave_verify.sh`.

## Caveats

- Citation-resolvability scoring is based on marker-to-evidence metadata traceability only.
- Exact marker resolution is stronger than ID-only resolution, but neither proves the cited evidence supports the claim.
- Missing markers, missing registries, unresolved IDs, locator mismatches, and malformed markers are review signals.
- Human review remains mandatory.

## Human Review Boundary

This eval dimension helps detect regressions in evidence marker discipline. It does not approve delivery, replace operator review, or certify claim truth.
