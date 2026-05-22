# v5 Runtime Hardening Tranche 1

This document covers the v5 upgrade branch / runtime hardening tranche. It does
not claim v5 is fully shipped.

## What Changed

- implemented: `GET /runtime/preflight` returns operator-local runtime
  diagnostics for version, upload storage, database, Redis, and process-local
  job state.
- implemented: upload storage writability is checked deterministically by the
  upload layer.
- implemented: upload filesystem failures return a controlled `503` API error
  instead of exposing raw tracebacks or local paths to the client.
- implemented: `/health` remains lightweight and backward compatible.
- implemented: v4.4 workflow order remains unchanged.

## Operator-Local Preflight

Run the app, discover the host-side published port, then call the diagnostic
route:

```powershell
docker compose up -d --build db redis app
docker compose ps
curl.exe http://localhost:<host_port>/health
curl.exe http://localhost:<host_port>/runtime/preflight
```

Use the host port shown by `docker compose ps`, for example a mapping like
`0.0.0.0:8001->8000/tcp` means `<host_port>` is `8001`.

The preflight route is an operator-only/local diagnostic. It is not auth,
multi-tenant isolation, public-safe security hardening, or a durable queue.

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

- partial: workflow runs are tracked in `api.running`, a process-local set.
- partial: FastAPI `BackgroundTasks` execute workflow runs in the API process.
- partial: Postgres state persistence is available when `DATABASE_URL` is
  configured and reachable; otherwise the store falls back to process memory.
- scaffolded: Redis is available in Docker and reported by preflight when
  configured, but Redis is not yet a durable worker or lock layer.
- missing: cross-process run locks, queue recovery, retry state, and worker
  lifecycle management.

## Tranche 2 Plan

- planned: move workflow execution to a durable Redis/Postgres-backed queue.
- planned: add cross-process run locks and recovery for interrupted runs.
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
- semantic claim defensibility,
- new reasoning features or template packs.
