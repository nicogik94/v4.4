# Automation ROI Audit Demo Script

This is a talk track for the Automation ROI Audit packaged offer. It uses the
existing local operator workflow. It is not legal advice, not financial advice,
not public SaaS, not a guaranteed automation recommendation engine, not a new
reasoning mode, and not a first-class backend runtime template.

Human review is required before any output is shared or acted on. ROI
assumptions are estimates, not guarantees.

## Two-Minute Explanation

Automation ROI Audit packages the current Decision Engine around one clear
operator workflow: intake candidate automation workflows, document baseline
assumptions, run the existing eight-phase engine, review missing information,
and export client-safe and operator-only artifacts.

The fixed workflow remains:

`classify -> hypotheses -> gauntlet -> audit -> strategy -> sqi -> monitor -> report`

The engine does not approve automation, calculate guaranteed savings, or replace
finance, legal, compliance, security, HR, privacy, operations, or domain review.
It produces structured review material: hypotheses, stress tests, risk analysis,
strategy, quality review, monitoring, and exportable artifacts for human review.

## Local Runtime Setup

From the repo root:

```powershell
cd mas
docker compose up -d --build db redis app
docker compose ps
$appPort = (docker compose port app 8000).Split(":")[-1]
$base = "http://localhost:$appPort"
curl.exe "$base/health"
curl.exe "$base/runtime/preflight"
curl.exe "$base/runtime/release-readiness"
python ..\scripts\demo_smoke_check.py --base-url "$base"
```

Use the Docker-discovered `$base`. Mention `http://localhost:8000` only as a
fallback when Docker publishes the app on that host port.

## Ten-Minute Demo Flow

1. Open `dashboards/index.html`.
2. Set the API base URL to the discovered local `$base`.
3. Click **New**.
4. Select `Automation ROI example framing`.
5. Paste the example brief from
   [`examples/automation-roi-audit-brief.md`](examples/automation-roi-audit-brief.md).
6. Use the risk classification selector and add a one-line rationale.
7. Upload supporting files only if they are appropriate for local processing.
8. Open the project row and click **Run**.
9. Explain the phase strip and the fixed workflow order.
10. Open **Overview**, **Dossier**, **Report**, and **Control log** as the run
    completes.
11. Review ROI assumptions, evidence maturity, and operator review support for
    missing information.
12. Export client-safe and operator-only outputs after report completion.

## What To Say During The Run

- "This is packaging around the existing engine, not a new reasoning mode."
- "This is a docs/templates/tests-only offer, not a first-class backend runtime
  template."
- "ROI assumptions are estimates, not guarantees."
- "These hypotheses guide what evidence to test next. They are not conclusions
  by themselves."
- "Client-safe means cleaned for review, not guaranteed correct, financially
  approved, or legally approved."
- "The operator decides what is safe to share."
- "The local runtime checks are operator diagnostics, not public deployment
  hardening."

## Expected Exports

- `report` DOCX or PDF: client-safe after review.
- `client_dossier` DOCX or PDF: client-safe after review.
- `client_monitoring_template` XLSX: client-safe after review.
- `operator_dossier` DOCX or PDF: operator-only.
- `operator_monitoring_template` XLSX: operator-only.
- `machine_archive` ZIP: internal archive only.

## Review Before Sharing

Before showing client-facing artifacts:

- confirm the recommendation matches the automation question,
- validate baseline time, volume, quality, and cost assumptions,
- review evidence maturity and unresolved operator review support questions,
- check that hypotheses are not presented as measured facts,
- review monitoring owners, cadences, thresholds, and stop conditions,
- route legal, financial, compliance, security, HR, privacy, safety, or domain
  claims to the right reviewer,
- keep operator-only and internal archive artifacts internal.

## Boundaries To State Clearly

- Local operator workflow only.
- Human review required.
- ROI assumptions are estimates, not guarantees.
- Not legal advice.
- Not financial advice.
- Not public SaaS.
- Not a guaranteed automation recommendation engine.
- Not autonomous automation approval, procurement, staffing, or implementation.
- Not a new reasoning mode.
- Not a first-class backend runtime template.
- No auth, tenancy, public deployment hardening, provider routing changes,
  queue/runtime changes, report rewrite, export schema changes, or
  automation-specific backend execution.

## Close

Close by asking which baseline assumption would most change the recommendation.
The practical next step is usually a reviewed client dossier, a Sprint 0
evidence pack, and a monitoring template with named owners and confirmed stop
conditions.
