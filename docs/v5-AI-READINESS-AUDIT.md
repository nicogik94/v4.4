# AI Readiness Audit Packaged Offer

This package turns the existing Decision Engine into a repeatable AI Readiness
Audit workflow for a local operator. It is a docs/templates/tests-only package
around the current engine. It is not a new reasoning mode, not a new backend
workflow, not a new prompt path, and not a first-class backend runtime template.

Human review is required before any output is shared or acted on. Readiness
findings are directional, not guarantees.

## What This Offer Is

An AI Readiness Audit helps an operator structure a decision about whether a
team, process, or business unit is ready to pursue a specific AI-enabled
workflow or pilot. The operator supplies a brief, optional source files,
business goals, process and data context, tool-stack constraints, governance
notes, adoption risks, and a risk classification. The existing workflow then
runs:

`classify -> hypotheses -> gauntlet -> audit -> strategy -> sqi -> monitor -> report`

Use this offer when a team needs a reviewed readiness assessment and first
30/60/90-day action plan, not when it needs a guaranteed AI transformation
plan, formal compliance or security sign-off, or autonomous implementation
roadmap. The output should help a human decision owner inspect use-case fit,
maturity gaps, data and integration constraints, adoption risk, governance
constraints, oversight needs, and success metrics.

## What This Offer Is Not

- Not legal advice.
- Not compliance certification.
- Not security certification.
- Not public SaaS.
- Not a guaranteed AI transformation plan.
- Not autonomous implementation, staffing, procurement, security, compliance,
  or governance advice.
- Not a replacement for legal, compliance, security, privacy, finance, HR,
  operations, IT, or domain review.
- Not a new reasoning mode, workflow phase, prompt route, provider route,
  queue/runtime path, export schema, or first-class backend runtime template.

## Intake Template

Use [`templates/ai-readiness-audit-intake.md`](templates/ai-readiness-audit-intake.md)
before creating the project. The minimum useful intake includes:

- business goals and use-case fit,
- process maturity,
- data availability and quality,
- tool stack and integration constraints,
- team capability and adoption risk,
- governance, privacy, and security constraints,
- risk classification and human oversight,
- first 30/60/90-day action plan needs,
- success metrics and monitoring,
- files available for upload.

## Example Brief

Use [`examples/ai-readiness-audit-brief.md`](examples/ai-readiness-audit-brief.md)
as a paste-ready example. It is an example brief, not a first-class vertical
template, not a first-class backend runtime template, and not a runtime pack.

## Recommended Upload Files

Upload files only when they improve evidence maturity. Uploads are additive and
do not automatically rerun the workflow.

Recommended files:

- business goals, strategy notes, use-case shortlist, or initiative charter,
- process maps, SOPs, workflow notes, or exception-handling descriptions,
- data inventory, data dictionary, quality report, sample-safe extracts, or
  lineage notes,
- system architecture, tool-stack inventory, integration notes, or vendor
  constraints,
- team capability, training, support, staffing, or adoption-readiness notes,
- governance, privacy, security, compliance, legal, HR, or risk review notes,
- policy, approval, escalation, or human-oversight requirements,
- current KPI dashboards, baseline metrics, monitoring notes, or success
  metric definitions.

Do not upload secrets, credentials, API keys, unrelated personal data,
regulated data, sensitive employee records, customer data that is not approved
for local processing, or files that the operator is not allowed to process in
the local runtime.

## Operator Runbook

1. Confirm the repo is on the intended branch or tag.
2. Start the local runtime and discover the app port with `docker compose port app 8000`.
3. Run local readiness checks: `/health`, `/runtime/preflight`, and
   `/runtime/release-readiness`.
4. Open `dashboards/index.html` and set the API base URL to the discovered
   local base URL.
5. Create a project using `AI readiness example framing`.
6. Paste the completed intake or example brief into the brief field.
7. Use the existing risk classification selector and rationale field.
8. Upload supporting files only after checking they are appropriate for local
   processing.
9. Run the project and wait for the `report` phase.
10. Review readiness findings, evidence maturity, governance constraints,
    oversight needs, first 30/60/90-day actions, and monitoring conditions
    before relying on the output.
11. Route legal, compliance, security, privacy, HR, IT, finance, safety, or
    domain claims to the right reviewer before sharing anything externally.

## Sample Project Framing

- Dashboard framing: `AI readiness example framing`.
- API/project type value: `ai_readiness` as a framing label only.
- Workflow order: `classify -> hypotheses -> gauntlet -> audit -> strategy -> sqi -> monitor -> report`.
- Default posture: local operator workflow with human review required.

This framing label does not create AI-specific backend execution, a guaranteed
AI transformation plan, compliance sign-off, security sign-off, or a first-class
backend runtime template.

## Expected Export Checklist

Before delivery, generate or review these artifacts:

- `report` DOCX or PDF: client-safe after review.
- `client_dossier` DOCX or PDF: client-safe after review.
- `client_monitoring_template` XLSX: client-safe after review.
- `operator_dossier` DOCX or PDF: operator-only.
- `operator_monitoring_template` XLSX: operator-only.
- `machine_archive` ZIP: internal archive only.

Use [`v5-OUTPUT-BOUNDARIES.md`](v5-OUTPUT-BOUNDARIES.md) before sending any
client-facing artifact. Confirm that readiness findings are visibly framed as
directional, not guarantees.

## Client-Safe Positioning Language

Use language like:

"This AI Readiness Audit is a structured, operator-reviewed decision review. It
assesses business goals and use-case fit, process maturity, data availability
and quality, integration constraints, team capability, adoption risk,
governance, privacy, security constraints, risk classification, human oversight,
and success metrics. It is intended to help a human decision owner decide what
evidence, controls, owners, and first 30/60/90-day actions are needed before
approving, narrowing, or deferring an AI pilot."

Avoid language that says or implies:

- the engine certified AI readiness,
- transformation outcomes are certain,
- legal, compliance, security, privacy, HR, finance, IT, or domain review is
  complete,
- the local runtime is ready to operate as public SaaS,
- the output is safe to share without human review,
- the package adds a new reasoning mode,
- the package adds a first-class backend runtime template.

## Boundaries And Disclaimers

This is a local operator workflow. It does not add authentication, tenancy,
public deployment hardening, provider routing changes, queue/runtime changes,
report generation changes, export schema changes, regulated vertical logic, or
AI-specific backend execution.

Readiness findings are directional, not guarantees. They depend on
human-supplied business goals, process evidence, data quality, tool-stack
constraints, team capability, adoption assumptions, governance context, risk
classification, oversight design, success metrics, and monitoring plans. Treat
them as planning inputs that need operator and reviewer judgment, not as legal
advice, not compliance certification, not security certification, or a
guaranteed AI transformation plan.

Client-safe means the export path applies deterministic cleanup for the intended
audience. It does not mean the output is correct, complete, legally approved,
compliance certified, security certified, confidential by access control, or
safe to forward without review.

The operator remains accountable for:

- checking evidence maturity,
- validating business goals and use-case fit,
- reviewing process maturity and data availability,
- confirming tool-stack and integration constraints,
- reviewing team capability and adoption risk,
- validating governance, privacy, security, legal, compliance, HR, finance, IT,
  safety, or domain claims with the right reviewer,
- confirming risk classification and human oversight requirements,
- reviewing first 30/60/90-day actions, success metrics, and monitoring,
- deciding what is safe to share,
- keeping operator-only and internal archive artifacts internal.

## Related Docs

- [`v5-AI-READINESS-DEMO-SCRIPT.md`](v5-AI-READINESS-DEMO-SCRIPT.md)
- [`v5-AUTOMATION-ROI-AUDIT.md`](v5-AUTOMATION-ROI-AUDIT.md)
- [`v5-STRATEGIC-DECISION-AUDIT.md`](v5-STRATEGIC-DECISION-AUDIT.md)
- [`v5-DEMO-WORKFLOW.md`](v5-DEMO-WORKFLOW.md)
- [`v5-ICE-OPERATOR-GUIDE.md`](v5-ICE-OPERATOR-GUIDE.md)
- [`v5-INGESTION-CONTRACT.md`](v5-INGESTION-CONTRACT.md)
- [`v5-OUTPUT-BOUNDARIES.md`](v5-OUTPUT-BOUNDARIES.md)
- [`local-runtime-smoke.md`](local-runtime-smoke.md)
