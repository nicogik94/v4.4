# AI Readiness Audit Demo Script

This is a talk track for the AI Readiness Audit packaged offer. It uses the
existing local operator workflow. It is not legal advice, not compliance
certification, not security certification, not public SaaS, not a guaranteed AI
transformation plan, not a new reasoning mode, and not a first-class backend
runtime template.

Human review is required before any output is shared or acted on. Readiness
findings are directional, not guarantees.

## Two-Minute Explanation

AI Readiness Audit packages the current Decision Engine around one clear
operator workflow: intake business goals and use-case fit, process maturity,
data availability and quality, tool-stack constraints, team capability,
governance/privacy/security constraints, risk classification, human oversight,
success metrics, and monitoring; then run the existing eight-phase engine,
review missing information, and export client-safe and operator-only artifacts.

The fixed workflow remains:

`classify -> hypotheses -> gauntlet -> audit -> strategy -> sqi -> monitor -> report`

The engine does not certify readiness, guarantee transformation outcomes,
approve deployment, or replace legal, compliance, security, privacy, HR, IT,
finance, operations, or domain review. It produces structured review material:
hypotheses, stress tests, risk analysis, strategy, quality review, monitoring,
and exportable artifacts for human review.

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
4. Select `AI readiness example framing`.
5. Paste the example brief from
   [`examples/ai-readiness-audit-brief.md`](examples/ai-readiness-audit-brief.md).
6. Use the risk classification selector and add a one-line rationale.
7. Upload supporting files only if they are appropriate for local processing.
8. Open the project row and click **Run**.
9. Explain the phase strip and the fixed phase order.
10. Open **Overview**, **Dossier**, **Report**, and **Control log** as the run
    completes.
11. Review readiness findings, evidence maturity, governance constraints,
    human oversight needs, first 30/60/90-day actions, and monitoring.
12. Review export boundaries, then export client-safe and operator-only outputs
    after report completion.

## What To Say During The Run

- "This is packaging around the existing engine, not a new reasoning mode."
- "This is a docs/templates/tests-only offer, not a first-class backend runtime
  template."
- "Readiness findings are directional, not guarantees."
- "These hypotheses guide what evidence to test next. They are not conclusions
  by themselves."
- "Client-safe means cleaned for review, not guaranteed correct, legally
  approved, compliance certified, or security certified."
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

- confirm the recommendation matches the readiness question,
- validate business goals and use-case fit,
- review process maturity and data availability,
- confirm tool-stack and integration constraints,
- review team capability and adoption risk,
- check governance, privacy, security, legal, compliance, HR, IT, finance,
  safety, or domain claims with the right reviewer,
- confirm risk classification and human oversight requirements,
- review first 30/60/90-day actions, success metrics, monitoring owners,
  cadences, thresholds, and stop conditions,
- keep operator-only and internal archive artifacts internal.

## Boundaries To State Clearly

- Local operator workflow only.
- Human review required.
- Readiness findings are directional, not guarantees.
- Not legal advice.
- Not compliance certification.
- Not security certification.
- Not public SaaS.
- Not a guaranteed AI transformation plan.
- Not autonomous AI approval, implementation, procurement, staffing, security,
  compliance, or governance advice.
- Not a new reasoning mode.
- Not a first-class backend runtime template.
- No auth, tenancy, public deployment hardening, provider routing changes,
  queue/runtime changes, report rewrite, export schema changes, or AI-specific
  backend execution.

## Close

Close by asking which readiness gap would most change the decision. The
practical next step is usually a reviewed client dossier, a Sprint 0 evidence
pack, and a monitoring template with named owners and confirmed stop
conditions.
