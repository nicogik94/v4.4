# Automation ROI Audit Packaged Offer

This package turns the existing Decision Engine into a repeatable Automation
ROI Audit workflow for a local operator. It is a docs/templates/tests-only
package around the current engine. It is not a new reasoning mode, not a new
backend workflow, not a new prompt path, and not a first-class backend runtime
template.

Human review is required before any output is shared or acted on. ROI
assumptions are estimates, not guarantees.

## What This Offer Is

An Automation ROI Audit helps an operator structure a decision about which
manual process, internal workflow, or AI-assisted automation candidate deserves
further review. The operator supplies a brief, optional source files, baseline
time and cost assumptions, implementation constraints, and a risk
classification. The existing workflow then runs:

`classify -> hypotheses -> gauntlet -> audit -> strategy -> sqi -> monitor -> report`

Use this offer when a team needs a reviewed comparison of automation candidates,
not when it needs an autonomous implementation plan or a guaranteed savings
calculation. The output should help a human decision owner inspect tradeoffs,
evidence gaps, review burden, and monitoring conditions.

## What This Offer Is Not

- Not legal advice.
- Not financial advice.
- Not public SaaS.
- Not a guaranteed automation recommendation engine.
- Not a guarantee that automation will produce savings.
- Not autonomous procurement, implementation, staffing, or compliance advice.
- Not a replacement for finance, legal, security, compliance, HR, operations,
  or domain review.
- Not a new reasoning mode, workflow phase, prompt route, provider route,
  queue/runtime path, export schema, or first-class backend runtime template.

## Intake Template

Use [`templates/automation-roi-audit-intake.md`](templates/automation-roi-audit-intake.md)
before creating the project. The minimum useful intake includes:

- automation question,
- candidate workflows being compared,
- current workflow baseline,
- volume, time, quality, and cost assumptions,
- implementation constraints and change-management limits,
- known evidence,
- unknowns and risks,
- success criteria,
- stakeholders and decision owner,
- risk classification and rationale,
- files available for upload.

## Example Brief

Use [`examples/automation-roi-audit-brief.md`](examples/automation-roi-audit-brief.md)
as a paste-ready example. It is an example brief, not a first-class vertical
template, not a first-class backend runtime template, and not a runtime pack.

## Recommended Upload Files

Upload files only when they improve evidence maturity. Uploads are additive and
do not automatically rerun the workflow.

Recommended files:

- process maps, SOPs, or workflow notes,
- time studies, queue logs, cycle-time exports, or task-volume summaries,
- quality, rework, error, escalation, or SLA metrics,
- labor-rate assumptions or finance-approved cost ranges,
- vendor quotes, build estimates, integration notes, or implementation plans,
- screenshots or sample artifacts that show the current workflow,
- compliance, legal, security, HR, or privacy review notes when relevant,
- stakeholder interview notes or change-readiness notes.

Do not upload secrets, credentials, API keys, unrelated personal data, employee
performance records, regulated data, or files that the operator is not allowed
to process in the local runtime.

## Operator Runbook

1. Confirm the repo is on the intended branch or tag.
2. Start the local runtime and discover the app port with `docker compose port app 8000`.
3. Run local readiness checks: `/health`, `/runtime/preflight`, and
   `/runtime/release-readiness`.
4. Open `dashboards/index.html` and set the API base URL to the discovered
   local base URL.
5. Create a project using `Automation ROI example framing`.
6. Paste the completed intake or example brief into the brief field.
7. Use the existing risk classification selector and rationale field.
8. Upload supporting files only after checking they are appropriate for local
   processing.
9. Run the project and wait for the `report` phase.
10. Review ROI assumptions, evidence maturity, missing information, and
    monitoring conditions before relying on the output.
11. Route legal, financial, compliance, security, HR, privacy, or domain claims
    to the right reviewer before sharing anything externally.

## Sample Project Framing

- Dashboard framing: `Automation ROI example framing`.
- API/project type value: `automation_roi` as a framing label only.
- Workflow order: `classify -> hypotheses -> gauntlet -> audit -> strategy -> sqi -> monitor -> report`.
- Default posture: local operator workflow with human review required.

This framing label does not create vertical-specific logic, a guaranteed
automation recommendation engine, or a first-class backend runtime template.

## Expected Export Checklist

Before delivery, generate or review these artifacts:

- `report` DOCX or PDF: client-safe after review.
- `client_dossier` DOCX or PDF: client-safe after review.
- `client_monitoring_template` XLSX: client-safe after review.
- `operator_dossier` DOCX or PDF: operator-only.
- `operator_monitoring_template` XLSX: operator-only.
- `machine_archive` ZIP: internal archive only.

Use [`v5-OUTPUT-BOUNDARIES.md`](v5-OUTPUT-BOUNDARIES.md) before sending any
client-facing artifact. Confirm that ROI assumptions are visibly framed as
estimates, not guarantees.

## Client-Safe Positioning Language

Use language like:

"This Automation ROI Audit is a structured, operator-reviewed decision review.
It compares candidate automation workflows, documents baseline assumptions,
identifies evidence gaps, evaluates implementation and review burden, and
suggests monitoring conditions. It is intended to help a human decision owner
decide what evidence is needed before approving, narrowing, or deferring an
automation pilot."

Avoid language that says or implies:

- the engine made the automation decision,
- savings are certain,
- ROI assumptions are audited financial projections,
- legal, financial, HR, privacy, security, or compliance review is complete,
- public SaaS readiness,
- the output is safe to share without human review,
- the package adds a new reasoning mode,
- the package adds a first-class backend runtime template.

## Boundaries And Disclaimers

This is a local operator workflow. It does not add authentication, tenancy,
public deployment hardening, provider routing changes, queue/runtime changes,
report generation changes, export schema changes, regulated vertical logic, or
automation-specific backend execution.

ROI assumptions are estimates, not guarantees. They depend on human-supplied
baseline data, implementation scope, adoption behavior, review burden, quality
impact, and change-management cost. Treat them as planning assumptions that
need finance and operator review, not as financial advice.

Client-safe means the export path applies deterministic cleanup for the intended
audience. It does not mean the output is correct, complete, legally approved,
financially approved, confidential by access control, or safe to forward without
review.

The operator remains accountable for:

- checking evidence maturity,
- validating baseline time, volume, quality, and cost assumptions,
- resolving or marking unavailable critical review questions,
- confirming legal, financial, compliance, security, HR, privacy, safety, or
  domain claims with the right reviewer,
- deciding what is safe to share,
- keeping operator-only and internal archive artifacts internal.

## Related Docs

- [`v5-AUTOMATION-ROI-DEMO-SCRIPT.md`](v5-AUTOMATION-ROI-DEMO-SCRIPT.md)
- [`v5-STRATEGIC-DECISION-AUDIT.md`](v5-STRATEGIC-DECISION-AUDIT.md)
- [`v5-DEMO-WORKFLOW.md`](v5-DEMO-WORKFLOW.md)
- [`v5-ICE-OPERATOR-GUIDE.md`](v5-ICE-OPERATOR-GUIDE.md)
- [`v5-INGESTION-CONTRACT.md`](v5-INGESTION-CONTRACT.md)
- [`v5-OUTPUT-BOUNDARIES.md`](v5-OUTPUT-BOUNDARIES.md)
- [`local-runtime-smoke.md`](local-runtime-smoke.md)
