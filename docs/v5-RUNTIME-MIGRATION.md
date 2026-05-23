# v5 Runtime Migration And Rollback Notes

This document covers runtime storage changes added during the v5 runtime
hardening branch. It is migration guidance for operators, not a public
deployment or security-readiness claim.

## Runtime Schema Ensure

Runtime schema ensure is idempotent and additive. Existing Postgres volumes are
upgraded at runtime when the app checks durable run state or durable queue
posture. Fresh Postgres volumes also receive the same tables through
`sql/init.sql`.

The ensure steps are designed to:

- create missing runtime tables,
- add missing additive columns,
- create missing indexes,
- backfill safe defaults such as `heartbeat_at`,
- avoid dropping data,
- avoid requiring a manual database reset.

If schema ensure fails, client-facing diagnostics should report a sanitized
failure and point operators to server logs. API responses must not expose raw
SQL errors, database URLs, passwords, local paths, tracebacks, uploaded content,
or provider payloads.

## `workflow_runs`

`workflow_runs` stores durable workflow-run state when Postgres is available.
It tracks:

- `run_id`,
- `project_id`,
- `status`,
- `current_phase`,
- `created_at`,
- `started_at`,
- `finished_at`,
- `heartbeat_at`,
- sanitized `error_summary`,
- `code_version`.

An active-run partial unique index prevents duplicate queued/running runs for a
project across API processes. If Postgres is unavailable, the runtime falls back
to process-local memory and the cross-process guard is not active.

## `heartbeat_at`

`heartbeat_at` is updated during active workflow progress. Existing rows are
backfilled with `COALESCE(heartbeat_at, started_at, created_at)`.

Abandoned-run recovery treats queued/running rows as stale only after the
configured threshold. `WORKFLOW_RUN_STALE_AFTER_SECONDS` defaults to `3600` when
missing, invalid, zero, or negative, and positive values below `300` are clamped
to `300`.

## `workflow_jobs`

`workflow_jobs` stores durable workflow queue rows when Postgres is available.
It tracks:

- `job_id`,
- `run_id`,
- `project_id`,
- `status`,
- `attempt_count`,
- `max_attempts`,
- `created_at`,
- `available_at`,
- `started_at`,
- `finished_at`,
- sanitized `error_summary`.

The durable queue is the source of truth for queued jobs, but the current
runtime still drains jobs inside the API process. There is no separate external
worker service in this tranche. Retry metadata exists, but automatic retries are
disabled by default.

If a job is enqueued and local API background-drain scheduling fails, the job
remains queued and visible in `/runtime/preflight`.

## Safe Inspection

Use local/operator database access. Avoid pasting secrets or full connection
URLs into shared logs.

```powershell
docker compose ps
docker compose exec -T db psql -U workflow -d workflow_v4 -c "SELECT run_id, project_id, status, current_phase, heartbeat_at, finished_at, error_summary FROM workflow_runs ORDER BY created_at DESC LIMIT 10;"
docker compose exec -T db psql -U workflow -d workflow_v4 -c "SELECT job_id, run_id, project_id, status, attempt_count, max_attempts, available_at, finished_at, error_summary FROM workflow_jobs ORDER BY created_at DESC LIMIT 10;"
```

Do not query or export uploaded content, provider payloads, API keys, or raw
local filesystem paths as part of routine release diagnostics.

## Rollback Caveats

- No destructive migrations are intended.
- Rolling back code does not automatically remove additive tables, columns, or
  indexes.
- Do not drop `workflow_runs` or `workflow_jobs` during normal rollback unless
  an operator explicitly accepts losing runtime history and queued-job state.
- If a rollback is needed while jobs are queued or running, inspect
  `workflow_jobs` and `workflow_runs` first. Prefer letting active work finish
  or marking abandoned runtime rows through the supported runtime path before
  switching versions.
- After rollback, verify `/health`; if the older runtime lacks
  `/runtime/preflight`, use that absence as expected older behavior rather than
  a schema failure.

## Generated Artifact Hygiene

Do not commit generated or local runtime artifacts:

- `exports/`,
- `upload_store/`,
- `upload_store.bad-*`,
- `scenario_shadow.sqlite3`,
- `__pycache__/`,
- `.pyc` files,
- local Docker override files,
- manual smoke files.

## Non-Claims

The v5 runtime foundation is hardened, but this does not provide public SaaS
readiness, authentication, authorization, multi-tenancy, tenant isolation,
separate worker service lifecycle, retry scheduling, cancellation, semantic
claim-defensibility guarantees, or replacement for human review.
