# Real Estate Decision Audit Demo Script

This is a talk track for the Real Estate Decision Audit packaged offer. It uses
the existing local operator workflow. It is not investment advice, not financial
advice, not legal advice, not tax advice, not appraisal or valuation
certification, not lending or credit underwriting, not public SaaS, not a
guaranteed buy/sell/hold recommendation engine, not a new reasoning mode, and
not a first-class backend runtime template.

Human review is required before any output is shared or acted on. Real estate
findings are directional, not guarantees.

## Two-Minute Explanation

Real Estate Decision Audit packages the current Decision Engine around one
clear operator workflow: intake the decision type, property or portfolio
context, market and submarket assumptions, rent/revenue assumptions, expense
and capex assumptions, financing assumptions, sensitivity/scenario assumptions,
operational constraints, regulatory/legal/tax questions for human experts, risk
classification, human oversight, diligence plan, success metrics, and
monitoring; then run the existing eight-phase engine, review missing
information, and export client-safe and operator-only artifacts.

The fixed workflow remains:

`classify -> hypotheses -> gauntlet -> audit -> strategy -> sqi -> monitor -> report`

The engine does not make a real-estate decision, certify value, approve a loan,
complete legal or tax review, or replace real-estate experts. It produces
structured review material: hypotheses, stress tests, risk analysis, strategy,
quality review, monitoring, and exportable artifacts for human review.

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
4. Select `Strategic Decision Audit framing`.
5. Paste the example brief from
   [`examples/real-estate-decision-audit-brief.md`](examples/real-estate-decision-audit-brief.md).
6. Use the risk classification selector and add a one-line rationale.
7. Upload supporting files only if they are appropriate for local processing.
8. Open the project row and click **Run**.
9. Explain the phase strip and the fixed phase order.
10. Open **Overview**, **Dossier**, **Report**, and **Control log** as the run
    completes.
11. Review real estate findings, evidence maturity, expert-review needs, first
    30/60/90-day diligence actions, success metrics, and monitoring.
12. Review export boundaries, then export client-safe and operator-only outputs
    after report completion.

## What To Say During The Run

- "This is packaging around the existing engine, not a new reasoning mode."
- "This is a docs/templates/tests-only offer, not a first-class backend runtime
  template."
- "The dashboard framing remains Strategic Decision Audit framing; the real
  estate package framing lives in the brief and docs."
- "Real estate findings are directional, not guarantees."
- "These hypotheses guide what evidence to test next. They are not conclusions
  by themselves."
- "Client-safe means cleaned for review, not guaranteed correct, legally
  approved, tax reviewed, valuation certified, appraisal certified, or credit
  approved."
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

- confirm the recommendation matches the real-estate decision question,
- validate property or portfolio context,
- review market and submarket assumptions,
- validate rent/revenue, expense, capex, financing, and sensitivity/scenario
  assumptions,
- confirm operational constraints,
- route regulatory, legal, tax, investment, financial, valuation, appraisal,
  lending, credit, environmental, engineering, insurance, and local market
  questions to the right human experts,
- confirm risk classification and human oversight requirements,
- review first 30/60/90-day diligence actions, success metrics, monitoring
  owners, cadences, thresholds, and stop conditions,
- keep operator-only and internal archive artifacts internal.

## Boundaries To State Clearly

- Local operator workflow only.
- Human review required.
- Real estate findings are directional, not guarantees.
- Not investment advice.
- Not financial advice.
- Not legal advice.
- Not tax advice.
- Not appraisal or valuation certification.
- Not lending or credit underwriting.
- Not public SaaS.
- Not a guaranteed buy/sell/hold recommendation engine.
- Not regulated real-estate decision automation.
- Not a new reasoning mode.
- Not a first-class backend runtime template.
- No auth, tenancy, public deployment hardening, provider routing changes,
  queue/runtime changes, report rewrite, export schema changes, dashboard
  redesign, or vertical-specific runtime logic.

## Close

Close by asking which real-estate assumption would most change the decision.
The practical next step is usually a reviewed client dossier, a Sprint 0
diligence pack, and a monitoring template with named owners and confirmed stop
conditions.
