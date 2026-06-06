# v5 Dashboard Canonicalization

## Current Status

`dashboards/index.html` is the canonical/default local operator dashboard.
`dashboards/index-v5.html` remains as a compatibility and explicit v5 entry point that redirects operators to `dashboards/index.html`.

This promotion changed only dashboard/docs/test surfaces. It did not change backend runtime behavior, workflow order, prompts, provider routing, schemas, export/report prose, or machine archive content.

## Canonical Dashboard Checklist

- API base configuration: the canonical dashboard supports query-param, localStorage, default fallback, and live manual edits in the API base input.
- Localhost handling: use Docker-discovered host ports first; `http://localhost:8000` is only a default fallback.
- Project creation: supported through the existing `/projects` API.
- Project selection/loading: supported through existing project state, workspace, audit, files, knowledge, clarification, and overview endpoints.
- File upload: supported through the existing `/projects/{project_id}/files` path.
- Uploaded/parsed/rejected file visibility: existing evidence/source panels remain available.
- Workflow run action: supported through the existing `/projects/{project_id}/run` path.
- Workflow phase visibility: the dashboard displays the eight-phase workflow: `classify -> hypotheses -> gauntlet -> audit -> strategy -> sqi -> monitor -> report`.
- Runtime diagnostics: compact pills check `/health`, `/runtime/preflight`, and `/runtime/release-readiness` fail-soft.
- Runtime Readiness: release blockers, release warnings, and failed/degraded preflight checks are displayed with concise check/message detail.
- Failed run/error display: existing toast and status surfaces remain in place.
- Evidence maturity/source status: existing evidence, source, and workspace views remain available.
- Overview/workspace/trace/report views: existing v5 views remain available.
- Exports: the dashboard exposes report, client dossier, operator dossier, machine archive, client monitoring XLSX, and operator monitoring XLSX through existing profile export routes.
- Client/operator terminology: dashboard demo-framing labels are examples only and do not create backend workflow or vertical runtime-pack changes.

## What Changed

- `dashboards/index.html` now contains the v5 dashboard experience.
- `dashboards/index-v5.html` is a compatibility entry point to the canonical dashboard.
- The dashboard no longer labels the v5 experience as experimental.
- Runtime diagnostic pills include preflight and release-readiness posture in addition to lightweight health.
- Runtime readiness details now show release blockers/warnings and failed/degraded preflight check messages.
- Monitoring template XLSX profile exports remain available from the report/export controls.
- The new-project "template" wording remains "demo framing" to avoid implying a backend workflow or vertical runtime-pack change.

## Still Local Operator Only

- No dashboard auth, multi-tenancy, tenant isolation, or public deployment hardening is implemented.
- No backend workflow, provider, prompt, queue, report, or export behavior is changed by this dashboard.
- The dashboard does not guarantee causal truth, semantic evidence proof, or autonomous decision-making.
- Human operator review remains required before sharing client-facing outputs.

## Run Locally

Start the app, then discover the published Docker port:

```powershell
docker compose ps
$appPort = (docker compose port app 8000).Split(":")[-1]
$base = "http://localhost:$appPort"

curl.exe "$base/health"
curl.exe "$base/runtime/preflight"
curl.exe "$base/runtime/release-readiness"
```

Open `dashboards/index.html` in a browser and set the API base field to `$base`.

If Docker is using default ports, `http://localhost:8000` may work as a fallback. Prefer the discovered port because local overrides may publish the app on a different host port.

## Positioning

Describe this as the canonical local operator dashboard for the v5 runtime foundation. Do not describe it as public SaaS ready, authenticated, multi-tenant, enterprise ready, or autonomous.
