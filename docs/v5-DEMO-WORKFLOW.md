# v5 Runtime Foundation Demo Workflow

This guide packages the current Decision Engine into a repeatable local demo
workflow. It describes the v5 runtime foundation. It is not a fully released v5
product and not public SaaS.

## What The Decision Engine Does

The Decision Engine helps an operator turn a difficult business, product, or
operational decision into a structured decision audit. It classifies the
problem, proposes hypotheses, stress-tests assumptions, audits risk, builds a
strategy, reviews quality, defines monitoring, and produces a report for human
review.

Use it when a decision is important enough to justify a structured review and
there is at least a short written brief. Do not use it as an autonomous
decision-maker, a public SaaS system, a guarantee of causal truth, or proof that
every claim is semantically supported by evidence.

## Demo Prerequisites

- Docker Desktop and Docker Compose.
- A configured `.env` in `mas/` with at least one supported model provider key.
- The repo checked out on the intended demo branch or tag, such as
  `v5-runtime-foundation`.
- Local operator access to the canonical/default dashboard at `dashboards/index.html`.

## Start The Local Runtime

From the repo root:

```powershell
cd mas
docker compose up -d --build db redis app
docker compose ps
$appPort = (docker compose port app 8000).Split(":")[-1]
$base = "http://localhost:$appPort"
```

Use the discovered `$base` for demo commands. `http://localhost:8000` is only a
default or fallback when Docker publishes the app on port 8000.

Check runtime readiness:

```powershell
curl.exe "$base/health"
curl.exe "$base/runtime/preflight"
curl.exe "$base/runtime/release-readiness"
python ..\scripts\demo_smoke_check.py --base-url "$base"
```

`/runtime/preflight` and `/runtime/release-readiness` are operator-local
diagnostics. They are not authentication, authorization, tenant isolation, or a
public security boundary.

## Open The Dashboard

Open the canonical dashboard, `dashboards/index.html`, in a browser. Set the API base URL field to the
Docker-discovered `$base` value, for example `http://localhost:8001` when Docker
maps host port 8001 to container port 8000.

Use the Console tab for the demo:

1. Create a new project.
2. Paste one of the example briefs from `docs/demo-briefs/`.
3. Use `minimal_risk` unless the demo intentionally discusses regulated use.
4. Upload supporting files only when you want to demonstrate evidence maturity.
5. Click the project row, then click **Run**.
6. Watch the phase strip until `report` completes.

The workflow order remains:
`classify -> hypotheses -> gauntlet -> audit -> strategy -> sqi -> monitor -> report`.

## Verify Readiness During The Demo

- `/health` returns `status: ok`.
- `/runtime/preflight` reports upload storage and database checks clearly.
- `/runtime/release-readiness` reports `pass`, or a local/operator limitation
  you can explain.
- Starting a duplicate active run returns a controlled conflict rather than
  launching a second unsafe run.
- The report phase completes before exports are treated as reviewable.

## Export The Outputs

Use the dashboard Report export controls or the profile export route:

```powershell
curl.exe -L "$base/projects/<project_id>/export?profile=report&format=docx" --output report.docx
curl.exe -L "$base/projects/<project_id>/export?profile=client_dossier&format=docx" --output client_dossier.docx
curl.exe -L "$base/projects/<project_id>/export?profile=operator_dossier&format=docx" --output operator_dossier.docx
curl.exe -L "$base/projects/<project_id>/export?profile=client_monitoring_template&format=xlsx" --output client_monitoring_template.xlsx
curl.exe -L "$base/projects/<project_id>/export?profile=operator_monitoring_template&format=xlsx" --output operator_monitoring_template.xlsx
```

The client delivery package is script/service based rather than a dashboard
feature:

```powershell
$env:CLIENT_DELIVERY_OUTPUT_DIR = "$env:TEMP\decision-engine-client-delivery"
python ..\scripts\render_example.py
Remove-Item Env:\CLIENT_DELIVERY_OUTPUT_DIR
```

## Review Before Sharing

Before external sharing, inspect:

- evidence maturity and client-use status,
- whether a Sprint 0 evidence pack is required,
- any unsupported certainty or placeholder thresholds,
- client dossier cleanliness,
- operator dossier trace and diagnostics,
- monitoring XLSX owners, cadences, thresholds, and evidence sources,
- whether generated artifacts are stored outside the repo or excluded from git.

## Human Review Checklist

- The recommendation matches the client decision question.
- Evidence maturity is explained plainly.
- Hypotheses are treated as hypotheses, not measured facts.
- Monitoring thresholds are reviewed before use.
- Legal, financial, compliance, safety, or public claims receive domain review.
- The operator decides what is safe to share.

## Known Limitations

- No public SaaS readiness.
- No auth, multi-tenancy, or tenant isolation.
- No autonomous decision-making.
- No guaranteed causal truth.
- No guaranteed semantic evidence proof for every claim.
- No first-class vertical runtime packs are introduced by these demo docs.
- Human review remains required for every output.
