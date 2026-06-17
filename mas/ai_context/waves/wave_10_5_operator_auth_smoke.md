# Wave 10.5 — Operator authenticated startup/runbook smoke

## Goal

Add a local-only operator runbook and smoke procedure proving the current runtime can be started, health-checked, preflight-checked, and exercised with operator authentication.

## Scope

This wave should add documentation and, if useful, a small local-only smoke script.

Preferred files:

- `ai_context/waves/wave_10_5_operator_auth_smoke.md`
- `docs/runbooks/operator_auth_smoke_runbook.md`
- optional: `scripts/operator_auth_smoke.sh`

## Required behavior

The runbook must document how to:

1. Start the local runtime.
2. Call `/health` without auth.
3. Call runtime preflight and confirm operator auth status.
4. Configure:
   - `MAS_REQUIRE_OPERATOR_AUTH=true`
   - `MAS_OPERATOR_API_KEY=<local test key>`
5. Call a protected write/control-plane endpoint:
   - missing key should fail with `401`
   - invalid key should fail with `401`
   - valid key should pass auth and then either succeed or reach the expected application-level result, such as a safe `404` for a missing dummy project
6. Stop the local runtime cleanly.
7. Avoid printing raw secrets.

## Constraints

- Localhost only.
- No public exposure.
- No Docker deployment changes.
- No app behavior changes.
- No auth behavior changes.
- No endpoint changes.
- No tests required unless the implementation changes executable behavior.
- The smoke script, if added, must be operator-invoked only and must not auto-start public services.
- The smoke script must not print `MAS_OPERATOR_API_KEY`.
- The smoke script must fail clearly if dependencies or server are missing.

## Discovery requirement

Before writing commands, inspect existing repo files to identify the correct startup command and protected endpoint path.

Use existing files such as:

- `api.py`
- `tests/test_operator_auth.py`
- README/runbook docs if present

Do not guess endpoint paths if the repo already defines them.

## Forbidden files

Do not change:

- `api.py`
- `config.py`
- `runtime/preflight.py`
- exporters
- prompts
- tests
- GitHub workflows
- Docker Compose files
- generated exports
- `scenario_shadow.sqlite3`
- `upload_store/`
- pycache or `.pyc`

## Verification

Run:

- `git diff --check`
- `bash -n scripts/operator_auth_smoke.sh` if a script is added
- `scripts/wave_verify.sh`

Expected final status should include only the wave spec, runbook, and optional smoke script.
