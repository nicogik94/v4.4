# Wave 10.3 — Auth / Control-Plane Hardening

## Goal

Add a narrow operator-auth/control-plane hardening layer for the local/operator-first v4/v5 API.

This is not full SaaS auth. The goal is to create an explicit operator-auth path that can protect control-plane actions when enabled, while preserving local/default usability.

## Non-goals

- No user accounts.
- No multi-tenancy.
- No login UI.
- No OAuth.
- No billing/accounts model.
- No database schema changes unless clearly unavoidable.
- No Redis/job queue redesign.
- No dashboard redesign.
- No public SaaS deployment.
- No exporter/report/prose changes.
- No prompt/model-routing/eval changes.

## Allowed Files

Prefer only:

- api.py
- config.py
- runtime/preflight.py
- tests/test_runtime_preflight.py
- tests/test_operator_auth.py
- ai_context/waves/wave_10_3_auth_control_plane.md

Only touch another file if absolutely necessary, and stop/report before doing so.

## Forbidden Files

- docker-compose.yml
- GitHub workflow files
- exporter files
- prompt files
- model routing files
- database migration/schema files
- dashboard redesign files
- generated exports
- scenario_shadow.sqlite3
- upload_store/
- __pycache__/
- .pyc
- .zip

## Required Behavior

1. Local/default mode must keep working without auth.
2. Add env-based configuration:
   - MAS_OPERATOR_API_KEY
   - MAS_REQUIRE_OPERATOR_AUTH
3. When operator auth is required, protected control-plane endpoints must reject missing/invalid credentials.
4. Credentials should be passed with:
   - X-MAS-Operator-Key
5. Do not log or expose the raw operator key.
6. /runtime/preflight should report auth posture additively:
   - auth configured or not
   - auth required or not
   - operator auth implemented or not
7. /health should remain usable and should not require auth.
8. Normal local tests and local dashboard use should not break by default.
9. Public exposure preflight behavior from Wave 10.2B must not regress.

## Suggested Protected Scope

Protect only control-plane/write/sensitive actions, not passive health checks.

Good candidates:
- project creation / workflow start / mutation endpoints
- runtime control endpoints

Do not over-protect static/local dashboard assets unless clearly required.

## Stop Conditions

Stop immediately if implementation requires:

- full user account auth
- session/login UI
- OAuth/JWT provider integration
- database schema changes
- multi-tenancy
- Docker Compose changes
- GitHub workflow changes
- dashboard redesign
- broad API refactor
- changing exporter/report behavior
- touching prompts/evals/model routing
- weakening the public exposure interlock from PR #52

## Required Tests

Run:

- git diff --check
- PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_runtime_preflight.py -q
- PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_operator_auth.py -q
- PYTHONDONTWRITEBYTECODE=1 timeout 300s .venv/bin/python -m pytest tests -q

If tests/test_operator_auth.py does not exist before implementation, create it only if needed for narrow auth coverage.

## Expected PR Body

### Summary

Adds minimal operator-auth/control-plane hardening for the local/operator-first API.

### Behavior

- Local/default mode remains usable without auth.
- Operator auth can be required via env.
- Missing/invalid operator key is rejected for protected control-plane endpoints when auth is required.
- /health remains open.
- /runtime/preflight reports auth posture additively.
- No raw secrets are exposed.

### Tests

Include exact targeted and full test results.

### Non-goals

Confirm no user accounts, multi-tenancy, OAuth, database schema changes, Docker Compose changes, exporter changes, prompt changes, model-routing changes, or dashboard redesign.

## Rollback Plan

Revert this PR. The API returns to the previous public-exposure preflight-only posture without operator-auth enforcement.

## Human Review Checklist

- Scope matches auth/control-plane hardening only.
- Local/default mode still works.
- Public exposure interlock still blocks unsafe public intent.
- Auth key is never logged or returned.
- No forbidden files changed.
- Tests are targeted and full-suite green.
