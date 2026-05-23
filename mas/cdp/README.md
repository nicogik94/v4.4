# CDP — Claim Defensibility Pass

## Current status

| Component | Status | Notes |
|---|---|---|
| T0 Discovery | Complete | Repo capabilities and blockers were inspected before implementation. |
| T1a Report Citation Discipline | Complete | Behaviorally validated under primary Anthropic. Reports can emit canonical evidence markers. |
| T1b Citation Resolvability | Complete | Review-only, in-memory citation resolvability over raw `ProjectState.report`. |
| Shared citation marker format | Complete | Marker format is centralized in `cdp.citation_format`. |
| T1c Product Surface | Not started | Should be planned separately as read-only surface work. |
| Evidence Gauge | Not started | Out of scope for `cdp.v0.1`. |
| Defense Index | Not started | Out of scope for `cdp.v0.1`. |
| Claim Cards | Not started | Out of scope for `cdp.v0.1`. |

## Module map

- `cdp/citation_format.py`  
  Defines the canonical evidence citation marker format, the `locator unavailable` literal, and the shared marker regex.

- `cdp/citation_resolvability.py`  
  Implements CDP T1b as a deterministic, review-only, in-memory post-pass over raw `ProjectState.report`.

## Hard constraints

CDP v0.1 must not:

- persist anything to `ProjectState`
- add a graph node
- add API wiring
- add export or renderer integration
- auto-strip or rewrite claims
- claim full claim defensibility
- mutate report text
- silently normalize malformed markers

## Resolver statuses

- `resolved_exact` — evidence ID exists and the locator exactly matches concrete registered metadata.
- `resolved_id_only` — evidence ID exists, but only ID-level resolution is possible.
- `unknown_evidence_id` — marker ID is absent from the evidence registry.
- `locator_mismatch` — evidence ID exists, but the marker locator conflicts with registered metadata.
- `malformed` — evidence marker text is non-canonical, escaped, placeholder-shaped, or otherwise invalid.

## Known limitations

CDP v0.1 is intentionally narrow:

- line-level review triage only
- does not verify whether cited evidence semantically supports the claim
- no Evidence Gauge
- no calibration anchoring
- no Defense Index
- no claim cards
- no auto-stripping
- no product-surface integration

A resolved marker means only that the marker maps to known evidence locator metadata. It does not prove that the evidence supports the claim.

## Next recommended tranche

Plan **T1c — Read-only CDP Surface**.

T1c should decide where `DefensePassResult` should be visible without changing the core workflow or overclaiming the artifact’s defensibility.
