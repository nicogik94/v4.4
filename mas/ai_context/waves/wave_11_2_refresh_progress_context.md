# Wave 11.2 — Refresh v4/v5 Progress Context After Wave 11.1

## Goal

Refresh the AI-readable v4/v5 progress context after Wave 11.1.

This is a docs/context-only wave. It should make future AI agents, Codex sessions, and operators aware that CDP T1c is now complete as a read-only operator evidence-review surface.

## Baseline

Expected latest merged main commit:

- `912e916` — Wave 11.1: Add read-only CDP evidence review surface (#63)

If local `main` does not contain this commit, stop and report.

## Scope

Allowed files:

- `ai_context/v4_v5_current_progress.md`
- `ai_context/waves/wave_11_2_refresh_progress_context.md`

Do not edit app/runtime/source/test files.

## Required updates

Update `ai_context/v4_v5_current_progress.md` to reflect:

- Wave 11.1 is complete and merged.
- PR #63 added CDP T1c read-only operator evidence-review surface.
- New endpoint:
  - `GET /projects/{project_id}/evidence-review`
- CDP T1c exposes existing CDP T1b citation-resolvability results as a read-only operator projection.
- Decision Trace now includes a compact evidence-review summary.
- Shared CDP caveats/status descriptions live in a pure CDP module.
- `tools/cdp_review.py` imports shared caveats instead of keeping a second source of truth.
- Focused API tests were added for payload shape, caveats, resolver statuses, malformed markers, empty/missing cases, and non-mutation.
- Full verification for Wave 11.1 passed:
  - `705 passed, 1 warning, 67 subtests passed`

## Non-overclaiming requirements

The context must preserve these boundaries:

- CDP T1c is read-only.
- CDP T1c does not mutate ProjectState.
- CDP T1c does not mutate report text.
- CDP T1c does not save state.
- CDP T1c does not add a workflow graph node.
- CDP T1c does not add a new phase.
- CDP T1c does not change routing, gates, re-entry, SQI, report generation, or exports.
- CDP T1c does not add Evidence Gauge, Defense Index, or Claim Cards.
- CDP T1c does not prove semantic evidence support.
- CDP T1c does not prove full claim defensibility.
- CDP T1c does not approve delivery.
- Human review remains mandatory.

## Recommended next-wave posture

After Wave 11.2, recommend a discovery-first Wave 11.3 before implementation.

Possible candidates to evaluate:

1. Delivery-readiness composite signal, read-only/advisory.
2. Operator review workflow consolidation around evidence review + clarifications.
3. Claim-defensibility eval dimension.
4. ICE productization.
5. Repo hygiene for tracked artifacts, if still present.
6. Runtime/job durability, only if operator pain justifies it.

Do not declare the next implementation wave as certain unless repo evidence strongly supports it.

## Verification

Before finishing, show:

- `git diff --check`
- `git diff --stat`
- `git diff --name-only`
- `git status --short --untracked-files=all`

Expected changed files:

- `mas/ai_context/v4_v5_current_progress.md`
- `mas/ai_context/waves/wave_11_2_refresh_progress_context.md`

No tests are required for this docs/context-only wave unless the implementer edits runtime/source/test files by mistake.

## Implementation note

This wave updates only AI-readable context. It records Wave 11.1 / PR #63 as the latest merged baseline, captures CDP T1c as complete only as a read-only operator API/trace surface, and preserves that Evidence Gauge, Defense Index, Claim Cards, semantic claim-support verification, and full claim defensibility are not implemented.

## Acceptance criteria

- Progress context accurately reflects Wave 11.1 completion.
- Implementation status vocabulary remains clear: implemented, partial, scaffolded, planned, not implemented.
- CDP T1c boundaries are not overclaimed.
- Future agents can understand where v4/v5 stands after Wave 11.1.
- No source/runtime/test files are edited.
