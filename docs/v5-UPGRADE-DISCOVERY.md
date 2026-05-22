# v5 Upgrade Discovery

This note grounds the v5 upgrade branch before runtime hardening work. It is a
discovery artifact for the v5 upgrade branch / runtime hardening tranche, not a
claim that v5 is fully shipped.

## Preserve

- implemented: The proven v4.4 workflow order is centralized in
  `orchestrator.WORKFLOW_PHASE_SEQUENCE`: `classify -> hypotheses -> gauntlet ->
  audit -> strategy -> sqi -> monitor -> report`.
- implemented: `ProjectState` tracks phase status, phase outputs, summaries,
  policy audit logs, uploaded file manifests, imported evidence, and report
  generation metadata.
- implemented: `exporters.py` and `report_quality.py` contain final
  client/operator export cleanup and freshness checks; this tranche does not
  change report copy or analytical behavior.
- implemented: `/health` is a lightweight compatibility endpoint returning
  status, version, persistence mode, and tracing state.

## Local / Single-Operator Posture

- partial: `store.py` persists to Postgres when `DATABASE_URL` is configured and
  reachable, but silently falls back to in-memory process state otherwise.
- partial: API run state uses process-local sets in `api.py` (`running` and
  `auto_refresh_jobs`). These do not coordinate across processes or survive
  restarts.
- partial: FastAPI `BackgroundTasks` runs the workflow in-process; there is no
  durable worker queue yet.
- partial: upload files are stored on local filesystem under
  `UPLOAD_LAYER.storage_dir`; this is suitable for local/operator use but not a
  multi-node storage design.

## Unsafe To Expose Publicly

- missing: authentication and authorization for public use.
- missing: multi-tenancy, tenant isolation, and per-tenant upload storage.
- missing: durable cross-process run locks and job recovery.
- partial: operator diagnostics may include runtime posture and local storage
  status. Treat `/runtime/preflight` as an operator-only/local endpoint.
- partial: policy audit and project state endpoints are useful for local
  diagnostics but are not public-safe without an auth layer.

## Upload Storage

- implemented: `knowledge/file_parsers.py` bounds accepted upload types and
  parser output. Supported types remain PDF, DOCX, TXT, MD, CSV, and XLSX.
- implemented: `knowledge/files.py` keeps raw bytes out of `ProjectState` and
  stores manifests plus parsed knowledge items.
- partial: storage root is configured by `UPLOAD_STORAGE_DIR`, defaulting to
  `mas/upload_store`.
- partial: before this tranche, filesystem failures while creating or writing
  upload files could escape as raw server errors.
- planned: add deterministic storage writability checks and controlled upload
  storage errors.

## Runtime Dependencies

- implemented: Postgres and Redis are defined in `docker-compose.yml`.
- partial: Postgres is used for state persistence only when configured and
  reachable.
- scaffolded: Redis is installed and configured in Docker, but is not yet used as
  a durable workflow queue or lock manager.
- scaffolded: `runtime/provider_gateway.py` provides provider routing and
  cache-compatible metadata, but this tranche does not alter provider behavior.
- implemented: `observability.py` is a Langfuse wrapper that is disabled unless
  keys are provided.

## v5 Tranche 1

- planned: add `/runtime/preflight` as an operator-only/local diagnostic route.
- planned: check upload store availability and writability.
- planned: report database connectivity when `DATABASE_URL` is configured and
  surface memory fallback as degraded.
- planned: report Redis connectivity when `REDIS_URL` is configured without
  making Redis required.
- planned: return controlled 503 upload errors for storage failures with no raw
  traceback or local path in the client response.
- planned: document process-local job limits and durable-job next steps.

## Not v5 Yet

- missing: durable Redis/Postgres-backed workflow queue.
- missing: cross-process run locks and recovery.
- missing: public auth/security hardening.
- missing: multi-tenant storage and access controls.
- missing: semantic claim defensibility beyond existing report/export quality
  guards.
- missing: enterprise/public SaaS readiness.
