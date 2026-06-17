# v4/v5 Current Progress Context

## Current baseline

Repository branch baseline:

- Branch: `main`
- Latest known commit: `d4ce78d Auto-clean test artifacts after wave verification (#55)`

The project is currently in a local/operator-first hardening phase before any public SaaS or multi-tenant expansion.

## Product identity

v4/v5 is a controlled, operator-led decision-analysis engine. It is not a chatbot, not a BI dashboard, and not a public self-serve SaaS product yet.

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

## Current safety posture

Current safety posture:

- Local/operator-first.
- Not public SaaS.
- Not multi-tenant.
- No public-user account system yet.
- Public exposure is blocked by preflight unless intentionally and safely configured.
- Operator auth exists for protected write/control-plane actions.
- `/health` remains open for basic runtime health checks.
- Human review remains required for auth, runtime-control, security, public exposure, and deployment-related changes.

## Current verified test status

Recent local verification:

- Full test suite: `696 passed, 1 warning, 67 subtests passed`
- Targeted operator auth tests: `5 passed, 1 warning`
- Targeted runtime preflight tests: `26 passed, 10 subtests passed`

Recent GitHub CI:

- PR #55 pytest CI passed.

## Wave automation workflow

Preferred wave flow:

```bash
scripts/wave_sync_main.sh
scripts/wave_start.sh <branch-name>
scripts/wave_verify.sh <optional targeted tests>
scripts/wave_commit.sh "<commit message>" <explicit files>
scripts/wave_pr.sh "<PR title>" <PR body file>
```

`wave_verify.sh` now runs artifact cleanup automatically after tests.

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

## Recommended next implementation candidates

Good next small waves:

1. Operator authenticated startup/runbook smoke.
2. Release readiness checklist.
3. Export/provenance polish.
4. Technology Readiness UX/output polish.
5. Monitoring template polish.
6. CI/eval aggregation reliability cleanup.

## Current guidance for future agents

Keep changes small and reviewable.

Prefer documentation, verification, and operator-safety polish before adding public/product expansion features.

Do not introduce public SaaS behavior, multi-tenancy, or public authentication flows without a dedicated security/design wave.
