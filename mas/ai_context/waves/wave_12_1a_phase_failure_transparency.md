# Wave 12.1A - Phase Failure Transparency

## Goal

Preserve bounded, non-sensitive phase failure diagnostics when a workflow phase
fails, and carry the best available diagnostic into `workflow_runs.error_summary`.

This wave responds to a real local pilot failure where provider fallback worked,
but the operator only saw:

```text
Workflow stopped before completion at phase gauntlet.
```

The underlying parse/schema-validation reason was not preserved in state or in
the durable run summary.

## Scope

- Add additive phase failure diagnostics to `ProjectState`.
- Record safe diagnostics for phase-level failures, including JSON parse/shape
  failures, schema validation failures, policy blocks, LLM call failures, missing
  stored output, and structural gate blocks.
- Preserve structured validation failures for core JSON phases and Technology
  Readiness phases instead of swallowing them inside `_store_phase_output`.
- Clear a phase's stale diagnostic when that phase completes successfully.
- Prefer the phase diagnostic when marking incomplete background runs failed in
  `workflow_runs.error_summary`.
- Add focused tests for gauntlet validation, parse diagnostics, stale-detail
  clearing, and durable run summary preservation.

## Non-goals

- No model routing, provider, token cap, retry-count, prompt, migration,
  `decision_events`, database schema, Docker Compose, or live workflow behavior
  changes.
- No live provider calls.
- No rerun of the failed pilot.
- No inspection, staging, reset, deletion, or restoration of
  `mas/scenario_shadow.sqlite3`.

## Diagnostic Contract

`ProjectState.phase_failure_details` is keyed by phase and stores:

- `phase`
- `category`
- `message`
- `captured_at`

Messages are bounded to 320 characters and sanitized for tracebacks, local paths,
and key-like secrets. Provider failures use controlled categories and fixed
messages, such as `quota_exceeded` with "Provider quota prevented a usable phase
response." Pydantic validation diagnostics are derived from structured
`exc.errors()` entries without input values, context, or URLs, so they preserve
useful field locations such as `results.0.id` without persisting raw model
output.

The durable run table is unchanged. When a background workflow stops before
completion, the API runner uses the failed phase's diagnostic, when present, as
the error detail passed through the existing `workflow_runs.error_summary`
sanitizer.

## Verification Summary

Targeted verification completed from `/home/nicolas/dev/v4.4/mas`:

- `PYTHONDONTWRITEBYTECODE=1 timeout 300s .venv/bin/python -m pytest tests/test_workflow_runner.py -q`
  - `100 passed, 3 subtests passed`
- `PYTHONDONTWRITEBYTECODE=1 timeout 300s .venv/bin/python -m pytest tests/test_classify_schema_repair.py -q`
  - `10 passed`
- `PYTHONDONTWRITEBYTECODE=1 timeout 300s .venv/bin/python -m pytest tests/test_technology_readiness.py -q`
  - `42 passed`

Full-suite verification completed from `/home/nicolas/dev/v4.4/mas`:

- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q`
  - `745 passed, 1 warning, 72 subtests passed`
- `git diff --check`
  - Passed.

## Review Notes

- This is an additive state-field change; existing snapshots load with an empty
  diagnostic map.
- `workflow_runs.error_summary` remains sanitized by the existing runtime helper.
- Structured validation failures still fail the phase; this wave only preserves
  the bounded reason.
