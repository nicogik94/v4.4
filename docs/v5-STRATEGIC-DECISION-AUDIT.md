# Strategic Decision Audit Packaged Offer

This package turns the existing Decision Engine into a repeatable Strategic
Decision Audit workflow for a local operator. It is packaging around the
current engine, not a new reasoning mode, not a new backend workflow, and not a
new prompt path.

Human review is required before any output is shared or acted on.

## What This Offer Is

A Strategic Decision Audit helps an operator structure an important business,
product, or operating decision. The operator supplies a brief, optional source
files, and a risk classification. The existing workflow then runs:

`classify -> hypotheses -> gauntlet -> audit -> strategy -> sqi -> monitor -> report`

Use this offer when a decision has meaningful downside, multiple plausible
paths, unclear evidence, or stakeholders who need a reviewed recommendation.

## What This Offer Is Not

- Not legal advice.
- Not financial advice.
- Not public SaaS.
- Not a guaranteed recommendation engine.
- Not autonomous decision-making.
- Not a replacement for expert, compliance, safety, legal, financial, or domain
  review.
- Not a new reasoning mode, workflow phase, prompt route, provider route,
  export schema, or runtime architecture.

## Intake Template

Use [`templates/strategic-decision-audit-intake.md`](templates/strategic-decision-audit-intake.md)
before creating the project. The minimum useful intake includes:

- decision question,
- alternatives being compared,
- context and constraints,
- known evidence,
- unknowns and risks,
- success criteria,
- stakeholders and decision owner,
- timing and capacity limits,
- risk classification and rationale,
- files available for upload.

## Example Brief

Use [`examples/strategic-decision-audit-brief.md`](examples/strategic-decision-audit-brief.md)
as a paste-ready example. It is an example brief, not a first-class vertical
template or runtime pack.

## Recommended Upload Files

Upload files only when they improve evidence maturity. Uploads are additive and
do not automatically rerun the workflow.

Recommended files:

- decision memo or strategy note,
- current operating metrics,
- customer or stakeholder interview notes,
- CRM, pipeline, usage, or support exports,
- budget, capacity, or staffing assumptions,
- pricing, renewal, or implementation evidence,
- risk register or postmortem notes,
- compliance, legal, or security review notes when relevant.

Do not upload secrets, credentials, API keys, unrelated personal data, or files
that the operator is not allowed to process in the local runtime.

## Operator Runbook

1. Confirm the repo is on the intended branch or tag.
2. Start the local runtime and discover the app port with `docker compose port app 8000`.
3. Run local readiness checks: `/health`, `/runtime/preflight`, and
   `/runtime/release-readiness`.
4. Open `dashboards/index.html` and set the API base URL to the discovered
   local base URL.
5. Create a project using `Strategic Decision Audit framing`.
6. Paste the completed intake or example brief into the brief field.
7. Use the existing risk classification selector and rationale field.
8. Upload supporting files only after checking they are appropriate for local
   processing.
9. Run the project and wait for the `report` phase.
10. Use operator review support for missing information before relying on the
    output.
11. Review the report, evidence maturity, monitoring plan, and export boundary
    before sharing anything externally.

## Sample Project Framing

- Dashboard framing: `Strategic Decision Audit framing`.
- API/project type value: `strategic_audit`.
- Workflow order: `classify -> hypotheses -> gauntlet -> audit -> strategy -> sqi -> monitor -> report`.
- Default posture: local operator workflow with human review required.

This framing label does not create vertical-specific logic.

## Expected Export Checklist

Before delivery, generate or review these artifacts:

- `report` DOCX or PDF: client-safe after review.
- `client_dossier` DOCX or PDF: client-safe after review.
- `client_monitoring_template` XLSX: client-safe after review.
- `operator_dossier` DOCX or PDF: operator-only.
- `operator_monitoring_template` XLSX: operator-only.
- `machine_archive` ZIP: internal archive only.

Use [`v5-OUTPUT-BOUNDARIES.md`](v5-OUTPUT-BOUNDARIES.md) before sending any
client-facing artifact.

## Client-Safe Positioning Language

Use language like:

"This Strategic Decision Audit is a structured, operator-reviewed decision
review. It maps the decision question, competing hypotheses, evidence gaps,
risks, recommended path, and monitoring plan. It is intended to help a human
decision owner review the tradeoffs and decide what evidence is needed next."

Avoid language that says or implies:

- the engine made the decision,
- guaranteed recommendation,
- legal-advice coverage,
- financial-advice coverage,
- public SaaS readiness,
- the output is safe to share without human review,
- the package adds a new reasoning mode.

## Boundaries And Disclaimers

This is a local operator workflow. It does not add authentication, tenancy,
public deployment hardening, provider routing changes, queue/runtime changes,
report generation changes, export schema changes, or regulated vertical logic.

Client-safe means the export path applies deterministic cleanup for the intended
audience. It does not mean the output is correct, complete, legally approved,
financially approved, confidential by access control, or safe to forward without
review.

The operator remains accountable for:

- checking evidence maturity,
- resolving or marking unavailable critical review questions,
- confirming legal, financial, compliance, safety, or domain claims with the
  right reviewer,
- deciding what is safe to share,
- keeping operator-only and internal archive artifacts internal.

## Related Docs

- [`v5-STRATEGIC-DECISION-DEMO-SCRIPT.md`](v5-STRATEGIC-DECISION-DEMO-SCRIPT.md)
- [`v5-DEMO-WORKFLOW.md`](v5-DEMO-WORKFLOW.md)
- [`v5-ICE-OPERATOR-GUIDE.md`](v5-ICE-OPERATOR-GUIDE.md)
- [`v5-INGESTION-CONTRACT.md`](v5-INGESTION-CONTRACT.md)
- [`v5-OUTPUT-BOUNDARIES.md`](v5-OUTPUT-BOUNDARIES.md)
- [`local-runtime-smoke.md`](local-runtime-smoke.md)
