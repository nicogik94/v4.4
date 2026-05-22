# v5 Runtime Hardening

This document covers the v5 upgrade branch / runtime hardening tranches. It
does not claim v5 is fully shipped.

## Tranche 1 Changes

- implemented: `GET /runtime/preflight` returns operator-local runtime
  diagnostics for version, upload storage, database, Redis, and process-local
  job state.
- implemented: upload storage writability is checked deterministically by the
  upload layer.
- implemented: upload filesystem failures return a controlled `503` API error
  instead of exposing raw tracebacks or local paths to the client.
- implemented: `/health` remains lightweight and backward compatible.
- implemented: v4.4 workflow order remains unchanged.

## Tranche 2 Changes

- implemented: workflow runs are recorded in a `workflow_runs` runtime table
  when Postgres is available.
- implemented: run records track `run_id`, `project_id`, status,
  `current_phase`, timestamps, sanitized error summary, and code version.
- implemented: an active-run uniqueness guard prevents duplicate queued/running
  runs for the same project across API processes when Postgres is available.
- implemented: the API still returns the existing `status` and `project_id`
  fields for workflow starts, and now also returns `run_id`.
- implemented: failed background runs are marked `failed` with sanitized error
  summaries. Raw tracebacks, secrets, and local paths are not intended for API
  responses.
- partial: if Postgres is unavailable, run tracking falls back to process memory
  and the cross-process guard is not active.

## What Is Durable Now

- implemented: queued/running/succeeded/failed workflow-run status is durable
  in Postgres when `DATABASE_URL` is configured and reachable.
- implemented: active-run conflict prevention is backed by a Postgres partial
  unique index on active statuses.
- implemented: `current_phase` is updated during the existing sequential
  workflow run.
- implemented: the v4.4 workflow sequence remains unchanged:
  `classify -> hypotheses -> gauntlet -> audit -> strategy -> sqi -> monitor ->
  report`.

## Operator-Local Preflight

Run the app, discover the host-side published port, then call the diagnostic
route:

```powershell
docker compose up -d --build db redis app
docker compose ps
$appPort = (docker compose port app 8000).Split(":")[-1]
curl.exe "http://localhost:$appPort/health"
curl.exe "http://localhost:$appPort/runtime/preflight"
```

Use the host port shown by `docker compose ps`, for example a mapping like
`0.0.0.0:8001->8000/tcp` means `<host_port>` is `8001`.

The preflight route is an operator-only/local diagnostic. It is not auth,
multi-tenant isolation, public-safe security hardening, or a durable queue.

The preflight response now separates:

- `database`: database connectivity.
- `redis`: Redis connectivity only; Redis is not a worker queue in this tranche.
- `run_state`: whether durable Postgres run state and the cross-process guard
  are active.
- `jobs`: local FastAPI background-task execution posture.

## Workflow Smoke

These commands use the published app port reported by Docker and do not assume
`localhost:8000`.

```powershell
docker compose ps
$appPort = (docker compose port app 8000).Split(":")[-1]
$base = "http://localhost:$appPort"

curl.exe "$base/health"
curl.exe "$base/runtime/preflight"

$body = @{
  name = "v5 durable run smoke"
  brief = "Assess whether to expand a B2B pilot after mixed early customer signals."
  data = "Evidence is intentionally small for a local runtime smoke."
} | ConvertTo-Json
$project = Invoke-RestMethod -Method Post -Uri "$base/projects" -ContentType "application/json" -Body $body
$run = Invoke-RestMethod -Method Post -Uri "$base/projects/$($project.project_id)/run"
$run

try {
  Invoke-RestMethod -Method Post -Uri "$base/projects/$($project.project_id)/run"
} catch {
  $_.Exception.Response.StatusCode.value__
}

do {
  Start-Sleep -Seconds 5
  $state = Invoke-RestMethod -Uri "$base/projects/$($project.project_id)"
  $state.current_phase
  $state.phase_status
} until ($state.phase_status.report -eq "completed" -or $state.phase_status.report -eq "failed")
```

The second run request should return a controlled conflict while the first run
is active. A full workflow run requires the same provider credentials that v4.4
already required.

## Upload Storage Diagnosis

`UPLOAD_STORAGE_DIR` controls the upload storage root. If unset, the runtime
uses the package-local `upload_store` directory.

The preflight upload check verifies:

- the root exists or can be created,
- the root is a directory,
- a small probe file can be written and deleted.

If uploads fail with a controlled `503`, check server logs for the technical
path and OS error. Client responses intentionally omit local paths and raw
tracebacks.

Common causes:

- container mount is missing or read-only,
- host directory permissions changed,
- disk or filesystem I/O error,
- app user cannot create the project upload directory.

## Current Runtime Limits

- partial: FastAPI `BackgroundTasks` still execute workflow runs in the API
  process.
- partial: Postgres state persistence is available when `DATABASE_URL` is
  configured and reachable; otherwise the store falls back to process memory.
- scaffolded: Redis is available in Docker and reported by preflight when
  configured, but Redis is not yet a durable worker or lock layer.
- missing: durable worker queue, interrupted-run recovery, retry state, and
  worker lifecycle management.
- missing: cancellation semantics. No cancellation API or `cancelled` run status
  is implemented in this tranche.

## Tranche 3 Plan

- planned: move workflow execution to a durable Redis/Postgres-backed queue.
- planned: add recovery for interrupted or abandoned active runs.
- planned: add explicit worker lifecycle and retry policy.
- planned: add operator authentication/authorization before exposing
  diagnostics outside a local environment.
- planned: document deployment-specific secrets and storage policies.

## Non-Claims

This tranche does not implement or claim:

- public SaaS readiness,
- enterprise readiness,
- multi-tenancy,
- authentication,
- durable workflow workers,
- durable retry/recovery,
- semantic claim defensibility,
- new reasoning features or template packs.
