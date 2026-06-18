# Wave 11.1 — CDP T1c Read-only Claim/Evidence Review Surface

## Goal

Expose the existing CDP T1b citation-resolvability pass as a read-only operator review surface.

The purpose is to help the operator review report claim/evidence traceability before delivery.

## Scope

Add a small, read-only CDP T1c surface.

Minimum required surface:

- one read-only operator API endpoint
- tests proving projection correctness, caveats, edge cases, and non-mutation

Allowed implementation areas:

- `api.py`
- `explainability.py`
- `workspace.py` only if needed for a compact operator summary
- `dashboards/index.html` only if needed for a compact read-only UI block
- `cdp/review_caveats.py` may be added if needed to hold pure shared CDP caveat constants
- `tests/`
- existing CDP modules may be imported/reused, but should not be broadened unless absolutely required

Optional surface:

- Add a compact read-only Decision Trace, Workspace, or dashboard summary only if it is a small projection of the endpoint/CDP result and does not require UI redesign or model sprawl.
- If this becomes non-trivial, defer the UI summary to Wave 11.2 and report that decision.

## Required behavior

Use the existing CDP logic:

- `cdp.citation_resolvability.build_defense_pass_result`
- existing `DefensePassResult` fields
- existing resolver statuses:
  - `resolved_exact`
  - `resolved_id_only`
  - `unknown_evidence_id`
  - `locator_mismatch`
  - `malformed`

Add a read-only operator endpoint, for example:

- `GET /projects/{project_id}/evidence-review`

The response should include:

- `project_id`
- `schema_version`
- `source`
- `summary_counts`
- `resolutions`
- `load_bearing_reviews`
- `claims_requiring_review`
- `missing_inputs`
- `extraction_limitations`
- anti-overclaiming labels/caveats

## Hard constraints

Do not:

- persist CDP results to `ProjectState`
- mutate `ProjectState`
- mutate report text
- rewrite, strip, normalize, or repair claims
- add a workflow graph node
- add a new phase
- change routing, gates, re-entry, SQI, report generation, or exports
- add client-facing claims
- add Evidence Gauge
- add Defense Index
- add Claim Cards
- claim semantic evidence support
- claim full claim defensibility
- change auth/public-exposure posture
- touch Docker/runtime/preflight behavior
- commit generated artifacts
- call `ensure_decision_objects` in the evidence-review endpoint
- call `store.save` in the evidence-review endpoint
- import `tools.cdp_review` from `api.py`, `explainability.py`, `workspace.py`, or dashboard-facing runtime code

If anti-overclaiming labels are needed outside the CLI tool, move/share them through a tiny pure CDP module such as `cdp/review_caveats.py`, then update `tools.cdp_review` to import the same constants.

## Wording constraints

Every surface must preserve this meaning:

- CDP v0.1 is review-only citation resolvability.
- Resolved markers are traceability aids only.
- `resolved_exact` means marker-to-registered-locator metadata matched.
- `resolved_id_only` means evidence-ID traceability only and is weaker than `resolved_exact`.
- Unresolved or malformed markers require operator review.
- CDP does not verify semantic support.
- CDP does not prove full claim defensibility.
- CDP does not approve delivery.
- CDP does not rewrite, strip, or correct report text.

## Tests required

Add or update tests covering:

1. Endpoint returns a valid read-only CDP review payload.
2. Endpoint returns 404 for missing project.
3. Response includes anti-overclaiming labels/caveats.
4. Response exposes expected resolver statuses.
5. Empty report / missing registry cases do not crash.
6. Malformed marker cases surface as review items.
7. Non-mutation: state before and after endpoint call is unchanged.
8. Decision trace/workspace/dashboard summary, if added, is compact, read-only, and caveated.

## Verification

Run targeted tests first.

Then run:

- `scripts/wave_verify.sh`

Before finishing, show:

- `git diff --check`
- `git diff --stat`
- `git diff --name-only`
- `git status --short --untracked-files=all`

Also show an out-of-scope check. Expected output should be empty.

## Acceptance criteria

- CDP review is visible to the operator without using exports or the CLI.
- All CDP output is read-only and caveated.
- No report or state mutation occurs.
- Existing CDP caveats remain intact.
- No runtime/API auth/public-exposure posture changes occur.
- Full verification passes.
