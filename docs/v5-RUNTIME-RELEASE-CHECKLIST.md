# v5 Runtime Release Checklist

This checklist is for the v5 upgrade branch / runtime hardening release path.
It does not claim v5 is fully shipped or public-deployment ready.

## Prerequisites

- Work from the intended release branch, for example
  `v5-runtime-release-hardening`.
- Confirm the v4.4 stable baseline and v5 runtime hardening commits are present.
- Keep the analytical workflow order unchanged:
  `classify -> hypotheses -> gauntlet -> audit -> strategy -> sqi -> monitor -> report`.
- Keep `APP_VERSION` at the existing configured value unless a separate version
  tranche explicitly changes it.
- Do not commit local generated artifacts such as `exports/`, `upload_store/`,
  `scenario_shadow.sqlite3`, `__pycache__/`, `.pyc`, or local smoke output.
- Do not commit local `docker-compose.yml` port overrides unless there is an
  intentional product change.

## Branch And Tag Expectations

- `v4.4-stable-baseline` remains the proven workflow baseline.
- The v5 runtime branch should include Tranches 1-4 before release hardening:
  runtime preflight, upload-store hardening, durable `workflow_runs`,
  abandoned-run recovery, durable `workflow_jobs`, and API-process queue drain.
- A release cut should tag only after targeted and full tests pass, docs are
  current, and manual smoke confirms runtime diagnostics.

## Runtime Diagnostics

Run the app, discover the published host port, and call diagnostics:

```powershell
docker compose ps
$appPort = (docker compose port app 8000).Split(":")[-1]
$base = "http://localhost:$appPort"

curl.exe "$base/health"
curl.exe "$base/runtime/preflight"
curl.exe "$base/runtime/release-readiness"
```

Expected:

- `/health` remains lightweight and backward compatible.
- `/runtime/preflight` returns `operator_only: true` and sanitized checks for
  version, upload storage, database, Redis, run state, workflow queue, and
  API-process drain posture.
- `/runtime/release-readiness` reuses the same preflight aggregation path and
  returns `release_gate: pass | warn | block`.

The diagnostic routes are operator-local only. They are not authentication,
authorization, tenant isolation, or a security boundary and are unsafe for
public exposure until auth and network hardening exist.

## Release Checks

- Upload store: preflight reports writable upload storage. If upload storage
  fails, the API should return controlled `503` upload errors without raw local
  paths or tracebacks.
- Database: preflight reports Postgres connectivity when `DATABASE_URL` is
  configured. If unavailable, diagnostics should be sanitized.
- Redis: preflight reports connectivity if configured. Redis is not required for
  workflow queue execution in this tranche.
- `workflow_runs`: preflight reports durable run state active,
  cross-process run guard enabled, active run count, stale run count, and
  abandoned-run recovery availability when Postgres is healthy.
- `workflow_jobs`: preflight reports durable queue active, queued/running/failed
  job counts, retry metadata, and API-process background drain dependency.
- Duplicate run guard: a second workflow start for the same active project
  returns controlled `409`.
- Queue drain: a workflow run enqueues a durable job, drains through the API
  process, completes through `report`, and returns queue counts to the expected
  idle state.

## Workflow Smoke

Use Docker's published host port:

```powershell
docker compose ps
$appPort = (docker compose port app 8000).Split(":")[-1]
$base = "http://localhost:$appPort"

$body = @{
  name = "v5 runtime release smoke"
  brief = "Assess whether to expand a small B2B pilot after mixed early customer signals."
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
  Start-Sleep -Seconds 10
  $state = Invoke-RestMethod -Uri "$base/projects/$($project.project_id)"
  $state.current_phase
  $state.phase_status
} until ($state.phase_status.report -eq "completed" -or $state.phase_status.report -eq "failed")

curl.exe "$base/runtime/preflight"
curl.exe "$base/runtime/release-readiness"
```

Expected:

- The first run response includes `run_id`.
- The duplicate active run returns `409`.
- The workflow reaches `report` with the existing v4.4 workflow order.
- Queue counts return to the expected idle state after completion.
- Preflight remains `ok` or `degraded` only for known local/runtime limits, not
  `fail`.

## Rollback Notes

- Runtime schema ensure is intended to be additive and idempotent.
- No destructive migrations are intended in these tranches.
- Rolling back application code may leave additive runtime tables or columns in
  Postgres. Do not drop them during normal rollback unless an operator has
  confirmed they are no longer needed.
- If rollback is needed, stop the app, deploy the prior known-good image or
  commit, preserve Postgres data, and verify `/health` before resuming work.
- If queued jobs exist during rollback, inspect them first and decide whether to
  let the newer runtime drain them before switching code.

## Known Limitations

- No public deployment without auth and network hardening.
- No multi-tenancy or tenant isolation.
- No separate external worker service; the API process still drains durable
  workflow jobs.
- No automatic retry scheduler; retry metadata exists with conservative
  defaults.
- No cancellation API.
- No semantic claim-defensibility guarantee.
- Human review remains required for outputs.
- Analysis quality, template packs, and report-content improvements are future
  work, not part of this release-hardening tranche.

## Do Not Overclaim

This release path hardens the v5 runtime foundation. It does not make the
system public SaaS ready, enterprise ready, authenticated, multi-tenant, or
semantically self-defending.
