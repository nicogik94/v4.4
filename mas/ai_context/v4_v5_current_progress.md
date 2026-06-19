# v4/v5 Current Progress Context

## Current baseline

Repository branch baseline:

- Branch: `main`
- Latest merged baseline: `912e916 Wave 11.1: Add read-only CDP evidence review surface (#63)`
- Wave 11.1 is complete on main.
- This document was refreshed by Wave 11.2 as a docs/context-only update.
- Future context refreshes should replace this baseline with the actual latest merged main commit.

The project remains in a local/operator-first hardening phase. Public SaaS, multi-tenant, autonomous-action, and public-user account capabilities are not implemented.

## Product identity

v4/v5 MAS is a controlled, operator-led decision-analysis engine.

It is:

- local/operator-first
- human-reviewed
- not a chatbot
- not BI
- not public SaaS
- not multi-tenant
- not a public-user account system
- not an autonomous action system

Human review remains mandatory before any client-facing use, public exposure, deployment, auth/security change, or runtime-control change.

Core workflow:

1. classify
2. hypotheses
3. gauntlet
4. audit
5. strategy
6. SQI
7. monitor
8. report

## Completed recent waves

### Wave 10.1 — Pytest CI and provenance foundation

Added GitHub pytest CI and provenance/release foundations.

### Wave 10.2A — Stale workflow run recovery

Hardened stale workflow run recovery so interrupted or stale workflow runs can be detected and recovered more safely.

### Wave 10.2B — Public exposure preflight interlock

Added a public exposure safety interlock. Public binding/exposure is blocked unless explicitly and safely configured.

### Wave 10.2C — Wave automation runbook/scripts

Added wave automation scripts and runbook structure:

- `scripts/wave_sync_main.sh`
- `scripts/wave_start.sh`
- `scripts/wave_verify.sh`
- `scripts/wave_clean_artifacts.sh`
- `scripts/wave_commit.sh`
- `scripts/wave_pr.sh`

### Wave 10.3 — Operator auth control-plane hardening

Added env-based operator authentication for protected write/control-plane endpoints.

Important points:

- Header: `X-MAS-Operator-Key`
- Env key: `MAS_OPERATOR_API_KEY`
- Auth requirement flag: `MAS_REQUIRE_OPERATOR_AUTH`
- Local/default mode does not require operator auth.
- Protected write/control-plane endpoints reject missing/invalid keys when auth is required.
- `/health` remains open.
- Required auth without configured key returns a safe configuration error.
- Raw keys are not exposed in preflight or responses.

### Wave 10.2D — Auto-clean test artifacts after wave verification

Updated `scripts/wave_verify.sh` so known tracked test artifacts are cleaned automatically after verification.

Important points:

- Runs `scripts/wave_clean_artifacts.sh` automatically.
- Cleanup runs on success and failure paths.
- Original verification failure exit code is preserved.
- No `git clean` behavior was added.
- No untracked files are deleted.

### Wave 10.4 — Current progress context refresh

Refreshed the AI/operator progress context for the v4/v5 hardening track.

Important points:

- Captured current local/operator-first posture.
- Preserved product identity and non-overclaim boundaries.
- Documented recent Wave 10 hardening progress.

### Wave 10.5 — Operator authenticated startup/runbook smoke

Added a local-only operator auth startup smoke runbook and operator-invoked smoke script.

Important points:

- Added `ai_context/waves/wave_10_5_operator_auth_smoke.md`.
- Added `docs/runbooks/operator_auth_smoke_runbook.md`.
- Added `scripts/operator_auth_smoke.sh`.
- Script defaults to `http://127.0.0.1:8000`.
- Script refuses non-loopback URLs.
- Script requires `MAS_REQUIRE_OPERATOR_AUTH=true` and `MAS_OPERATOR_API_KEY`.
- Script does not print the raw operator key.
- Protected endpoint smoke uses valid auth plus a missing dummy project to reach safe application-level `404`.

### Wave 10.7 — Release readiness checklist

Added a local/operator-first release readiness checklist.

Important points:

- Added `docs/runbooks/release_readiness_checklist.md`.
- Checklist covers local demo readiness, internal release review readiness, and future deployment/security review readiness.
- It is a human/operator review gate, not deployment automation.
- It explicitly does not prove public SaaS readiness.
- It includes local startup, `/health`, `/runtime/preflight`, operator auth posture, public exposure interlock, `scripts/wave_verify.sh`, artifact cleanup, release evidence packet, and go/no-go decision guidance.

### Wave 10.8 — Output provenance and readiness wording polish

Polished output/provenance, Technology Readiness, and monitoring presentation without changing core workflow behavior.

Important points:

- Evidence markers, citations, source locators, and provenance fields are traceability aids.
- They should not be treated as proof that every generated claim is semantically supported.
- Client-clean vs operator-only surfaces remain separated.
- Technology Readiness outputs should preserve readiness caveats, external-reliance boundaries, and human-review language.
- Monitoring surfaces should make owner, action, signal, threshold, cadence, canary, circuit-breaker, and review posture easier to act on.

### Wave 10.9 — CI/eval reliability diagnostics

Improved CI/eval and local verification diagnostics without weakening checks.

Important points:

- Updated `scripts/wave_verify.sh` with clearer section headers, working context, rerun guidance, cleanup summary, and final exit-code reporting.
- Updated `scripts/wave_clean_artifacts.sh` with clearer artifact discovery and restore summaries.
- Original failing verification exit code remains preserved.
- Verification remains honest: failures are not hidden, converted into success, or downgraded to warnings.
- Known tracked test artifacts are still cleaned/restored; no `git clean` behavior was added.

### Wave 11.1 — CDP T1c read-only evidence review surface

PR #63 merged as `912e916 Wave 11.1: Add read-only CDP evidence review surface (#63)`.

Important points:

- Added `GET /projects/{project_id}/evidence-review`.
- Exposes existing CDP T1b citation-resolvability results as a read-only operator projection.
- Adds a compact Decision Trace evidence-review summary.
- Adds pure shared CDP caveats/status descriptions in `cdp/review_caveats.py`.
- Updates `tools/cdp_review.py` to use shared caveats and avoid a duplicate caveat source of truth.
- Adds focused tests for endpoint payload, caveats, resolver statuses, malformed markers, empty/missing cases, and non-mutation.
- Verification passed: `705 passed, 1 warning, 67 subtests passed`.

### Wave 11.4 — Delivery Review Readiness Composite

Wave 11.4 adds a read-only advisory Delivery Review Readiness projection on the `wave11-4-delivery-review-readiness` branch.

Important points:

- Adds `delivery_readiness.py` as a pure projector over existing clarification, CDP evidence-review, risk-gate, phase-state, approval-like, and staleness signals.
- Adds `GET /projects/{project_id}/delivery-review-readiness`.
- Adds a compact `delivery_review_readiness` field to workspace summaries; overview includes it through the nested workspace summary.
- The projection does not approve delivery, gate delivery, mutate `ProjectState`, save state, hydrate decision objects in the endpoint, create a workflow phase, or change report/export behavior.
- It does not prove semantic evidence support; human review remains mandatory.
- Focused targeted verification passed: `118 passed, 1 warning, 36 subtests passed`.

### Wave 11.5 — Evidence Quality + Claim-Defensibility Eval Dimension

Wave 11.5 adds a deterministic `citation_resolvability` eval dimension.

Important points:

- Reuses CDP citation-resolvability logic to score marker-to-evidence metadata traceability in eval fixtures.
- Covers exact resolution, ID-only resolution, unknown evidence IDs, malformed markers, and no-marker behavior.
- Preserves CDP caveats in eval output.
- This does not prove semantic claim support or full claim defensibility.
- Evidence Gauge, Defense Index, and Claim Cards remain not implemented.

### Wave 11.5A — Eval Aggregate Provider-Failure Diagnostics

Wave 11.5A classifies eval aggregate provider/quota failures separately from eval-quality failures.

Important points:

- Adds aggregate diagnostics for provider failure count, categories, provider-unavailable-only state, and aggregate failure kind.
- Provider-unavailable-only aggregate failures do not block CI as eval-quality regressions, but remain marked `ok: false` with unknown quality status.
- Aggregation errors, deterministic eval failures, claim-traceability failures, mixed failures, and real eval-quality regressions still fail.
- This does not weaken Wave 11.5 claim-traceability evals and does not hide real eval failures.

### Wave 11.6 — Engine Reliability + Support-Phase Test Hardening

Wave 11.6 adds deterministic support-phase reliability tests for gauntlet, SQI, monitor, and small existing monitoring / circuit-breaker interpretation paths.

Important points:

- Improves coverage for support-phase thresholding, output shape preservation, advisory SQI behavior, monitor-plan structure, and surfaced breaker/blocking reasons.
- This is test and context hardening only.
- It does not change workflow routing, report/export behavior, delivery-readiness semantics, eval semantics, or monitoring autonomy.
- MAS remains local/operator-first and human review remains mandatory.

### Wave 11.7 — Repo Hygiene for Generated Artifacts

Wave 11.7 removes tracked generated Python bytecode/cache artifacts from the Git index and ensures future bytecode generation is ignored.

Important points:

- Only generated Python bytecode/cache artifacts are untracked from the index.
- `scenario_shadow.sqlite3` and other sqlite/db or fixture-like artifacts remain tracked and untouched.
- `.gitignore` covers Python bytecode and pytest cache patterns.
- This does not change source behavior, tests, workflow routing, readiness semantics, report/export behavior, eval semantics, or runtime behavior.

### Wave 11.8 — Workflow Runner Consolidation Discovery

Wave 11.8 maps the current workflow runner and execution surfaces to prepare a future decision about whether Wave 11.9 should exist.

Important points:

- This is discovery-only.
- It identifies the API-backed sequential runner as the active operator full-workflow path.
- It does not change runtime behavior, workflow routing, readiness, eval, report/export, auth, preflight, API behavior, tests, or scripts.
- It recommends that any Wave 11.9 work be small, test-first, and avoid actual runner consolidation unless separately scoped.

## Current safety posture

- Local/operator-first.
- Human review remains mandatory.
- Not a chatbot.
- Not BI.
- Not public SaaS.
- Not multi-tenant.
- No public-user account system.
- Not an autonomous action system.
- Public exposure is blocked by preflight unless intentionally and safely configured.
- Operator auth exists for protected write/control-plane actions.
- `/health` remains open for basic runtime health checks.
- `/runtime/preflight` reports operator-local posture and public exposure/auth checks; do not overclaim beyond the current implementation.
- Human review remains required for auth, runtime-control, security, public exposure, deployment-related, or client-facing changes.

## Current verification posture

`scripts/wave_verify.sh` is the main local verification gate.

Current behavior:

- Runs `git diff --check`.
- Runs targeted pytest paths when provided.
- Runs the full pytest suite with `timeout 300s .venv/bin/python -m pytest tests -q`.
- Runs `scripts/wave_clean_artifacts.sh` automatically on exit.
- Prints final `git status --short`.
- Preserves the original verification failure exit code when cleanup also runs.

Wave 10.9 improved:

- diagnostic section headers
- working directory and branch visibility
- rerun guidance for failed commands
- artifact cleanup summaries
- final verification and cleanup exit-code reporting

Verification posture:

- Failures are not hidden.
- Failing tests/evals must not be converted into success.
- Required checks must not be downgraded to warnings.
- Known tracked test artifacts are cleaned/restored by `scripts/wave_clean_artifacts.sh`.
- Cleanup must not delete untracked files and must not use `git clean`.

## Current release/readiness posture

`docs/runbooks/release_readiness_checklist.md` exists and should be used before treating a repo state as ready for:

- local demo readiness
- internal release review readiness
- future deployment/security review readiness

Important boundaries:

- The checklist is operator review guidance, not deployment automation.
- It does not prove public SaaS readiness.
- Public exposure remains blocked by posture/security constraints unless explicitly and safely configured.
- Operator auth, runtime preflight, and public exposure posture should not be overclaimed beyond the current implementation.
- Release evidence should include commit SHA, Git status, test summary, health result, preflight summary, operator auth smoke result if run, and known caveats.

## Output/provenance posture

Current output posture:

- Evidence markers are traceability aids.
- Citations are traceability aids.
- Source locators are traceability aids.
- Provenance surfaces support review and audit.
- None of these should be described as proof of semantic claim support.

Current CDP status:

- T1a Report Citation Discipline: complete.
- T1b Citation Resolvability: complete as a deterministic, review-only, in-memory pass over raw `ProjectState.report`.
- T1c Product Surface: complete as a read-only operator API and compact Decision Trace summary.
- Evidence Gauge: not implemented and out of scope for CDP v0.1.
- Defense Index: not implemented and out of scope for CDP v0.1.
- Claim Cards: not implemented and out of scope for CDP v0.1.
- Full semantic claim-support verification: not implemented.
- Full claim defensibility: not implemented.

CDP T1c non-overclaiming boundaries:

- CDP T1c is not semantic evidence verification.
- A resolved marker means marker-to-registered-evidence metadata traceability only.
- `resolved_exact` is stronger than `resolved_id_only`, but neither proves semantic support.
- Evidence-review output is advisory and review-only; it does not approve client delivery.
- CDP T1c does not mutate `ProjectState`, mutate report text, save state, add a workflow graph node, add a new phase, or change routing, gates, re-entry, SQI, report generation, or exports.
- Human review remains mandatory.

Current client/operator boundary:

- Keep operator-only warnings and internal diagnostics out of client-facing reports unless a surface is explicitly operator-facing.
- Client-facing outputs should remain clean, bounded, and reviewed.
- Operator-facing outputs may include traceability diagnostics useful for review.

Technology Readiness and monitoring posture:

- Technology Readiness outputs should retain caveats about evidence gaps, external reliance, and human review.
- Readiness language should not imply unsupported certainty or advancement approval.
- Monitoring plans should keep owner, action, signal, threshold, cadence, canary, circuit-breaker, and human-review posture clear.
- MAS does not perform autonomous external monitoring or autonomous actions.

## Current verified test status

Current expected local verification shape:

- Full test suite: `705 passed, 1 warning, 67 subtests passed`

Treat this as orientation only. Future waves must rerun the relevant verification command rather than relying on this recorded baseline.

Recent GitHub/main baseline:

- `912e916 Wave 11.1: Add read-only CDP evidence review surface (#63)`
- Wave 11.1 CDP T1c read-only operator evidence-review surface is merged.

## Wave automation workflow

Preferred wave flow:

```bash
scripts/wave_sync_main.sh
scripts/wave_start.sh <branch-name>
scripts/wave_verify.sh <optional targeted tests>
scripts/wave_commit.sh "<commit message>" <explicit files>
scripts/wave_pr.sh "<PR title>" <PR body file>
```

`wave_verify.sh` now runs artifact cleanup automatically after tests and prints clearer diagnostics and rerun guidance.

## Git hygiene

Never commit:

- `scenario_shadow.sqlite3`
- `__pycache__/`
- `.pyc`
- generated exports
- `upload_store/`
- `.zip`
- local `docker-compose.yml` edits unless explicitly intended

Use explicit-file commits through `scripts/wave_commit.sh`.

## Recommended Wave 11.3 direction

Wave 11.3 should be discovery-first before implementing a new feature.

Recommended discovery approach:

- Use Claude Code or a comparable repo review pass for discovery/design only.
- Review current architecture, tests, runbooks, product surfaces, and operator workflows before selecting implementation.
- Run an ARVV-style audit before committing to a feature wave.
- Keep the final implementation choice dependent on that review, not on assumptions in this context file.
- Do not jump straight into a large implementation.

Candidate areas to evaluate:

1. Delivery-readiness composite signal, read-only/advisory.
2. Operator review workflow consolidation around evidence review and clarifications.
3. Claim-defensibility eval dimension.
4. ICE productization.
5. Repo hygiene for tracked artifacts, if still present.
6. Runtime/job durability, only if operator pain justifies it.

## What not to do next

Do not:

- jump into public SaaS
- build multi-tenancy yet
- make MAS an autonomous agent
- broaden MAS into a generic chatbot
- broaden MAS into generic BI
- implement a large feature without a focused wave spec and tests
- overclaim operator auth, runtime preflight, public exposure, provenance, or readiness beyond the current implementation
- overclaim CDP T1c as semantic evidence verification, full claim defensibility, delivery approval, Evidence Gauge, Defense Index, or Claim Cards

## Current guidance for future agents

Keep changes small, reviewable, and scoped to an explicit wave.

Preserve distinctions between implemented, partial, scaffolded, planned, and not implemented work.

Prefer documentation, verification, operator-safety, and review-quality polish before adding public/product expansion features.

Do not introduce public SaaS behavior, multi-tenancy, public authentication flows, autonomous action behavior, or deployment posture changes without a dedicated security/design wave.
