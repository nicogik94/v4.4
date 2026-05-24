# v5 Dashboard Canonicalization Readiness

## Current Status

`dashboards/index.html` remains the canonical local operator dashboard.
`dashboards/index-v5.html` is a controlled experimental local demo dashboard. It is not promoted to canonical status and is not a public SaaS interface.

This audit focused on dashboard-side parity only. It did not change backend runtime behavior, workflow order, prompts, provider routing, schemas, export/report prose, or machine archive content.

## Parity Checklist

- API base configuration: v5 now supports query-param, localStorage, default fallback, and live manual edits in the API base input.
- Localhost handling: use Docker-discovered host ports first; `http://localhost:8000` is only a default fallback.
- Project creation: supported through the existing `/projects` API.
- Project selection/loading: supported through existing project state, workspace, audit, files, knowledge, clarification, and overview endpoints.
- File upload: supported through the existing `/projects/{project_id}/files` path.
- Uploaded/parsed/rejected file visibility: existing evidence/source panels remain available.
- Workflow run action: supported through the existing `/projects/{project_id}/run` path.
- Workflow phase visibility: v5 displays the eight-phase workflow: `classify -> hypotheses -> gauntlet -> audit -> strategy -> sqi -> monitor -> report`.
- Runtime diagnostics: v5 now checks `/health`, `/runtime/preflight`, and `/runtime/release-readiness` fail-soft.
- Failed run/error display: existing toast and status surfaces remain in place.
- Evidence maturity/source status: existing evidence, source, and workspace views remain available.
- Overview/workspace/trace/report views: existing v5 views remain available.
- Exports: v5 exposes report, client dossier, operator dossier, machine archive, client monitoring XLSX, and operator monitoring XLSX through existing profile export routes.
- Client/operator terminology: v5 remains labeled as a controlled experimental local demo; demo framing is not a runtime template pack.

## What Was Fixed

- API base edits now affect API calls without requiring code changes, so local Docker mappings such as `http://localhost:8001` can be used.
- Runtime diagnostic pills include preflight and release-readiness posture in addition to lightweight health.
- Monitoring template XLSX profile exports are available from the v5 report/export controls.
- The new-project "template" wording was changed to "demo framing" to avoid implying a backend workflow or vertical runtime-pack change.
- The dashboard labels itself as controlled and experimental; canonical status remains with `index.html`.

## Still Experimental

- `index-v5.html` is not the canonical dashboard.
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

Open `dashboards/index-v5.html` in a browser and set the API base field to `$base`.

If Docker is using default ports, `http://localhost:8000` may work as a fallback. Prefer the discovered port because local overrides may publish the app on `8001`.

## Do Not Overclaim

Describe this as controlled local dashboard readiness for the v5 runtime foundation. Do not describe it as canonical, public SaaS ready, authenticated, multi-tenant, enterprise ready, or autonomous.
