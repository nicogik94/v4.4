# Real Estate Decision Audit Packaged Offer

This package turns the existing Decision Engine into a repeatable Real Estate
Decision Audit workflow for a local operator. It is a docs/templates/tests-only
package around the current engine. It is not a new reasoning mode, not a new
backend workflow, not a new prompt path, and not a first-class backend runtime
template.

Human review is required before any output is shared or acted on. Real estate
findings are directional, not guarantees.

## What This Offer Is

A Real Estate Decision Audit helps an operator structure a decision about a
property, portfolio, market, lease, renovation, capex plan, acquisition screen,
development go/no-go, or buy/sell/hold choice. The operator supplies a brief,
optional source files, property or portfolio context, market and submarket
assumptions, revenue, expense, capex, financing, scenario, operational, legal,
tax, and regulatory context, plus a risk classification. The existing workflow
then runs:

`classify -> hypotheses -> gauntlet -> audit -> strategy -> sqi -> monitor -> report`

Use this offer when a team needs a reviewed real-estate decision analysis, not
when it needs certified valuation work, tax structuring, legal conclusions,
lending or credit decisions, or certainty about a buy/sell/hold outcome. The
output should help a human decision owner inspect assumptions, evidence gaps,
diligence priorities, operational constraints, risk, monitoring conditions, and
first 30/60/90-day diligence actions.

## What This Offer Is Not

- Not investment advice.
- Not financial advice.
- Not legal advice.
- Not tax advice.
- Not appraisal or valuation certification.
- Not lending or credit underwriting.
- Not public SaaS.
- Not a guaranteed buy/sell/hold recommendation engine.
- Not regulated real-estate decision automation.
- Not autonomous acquisition, disposition, leasing, development, financing,
  underwriting, tax, legal, or valuation advice.
- Not a replacement for licensed brokers, appraisers, valuation professionals,
  attorneys, tax advisors, lenders, credit underwriters, inspectors, engineers,
  environmental consultants, insurance advisors, property managers, asset
  managers, or local market experts.
- Not a new reasoning mode, workflow phase, prompt route, provider route,
  queue/runtime path, export schema, or first-class backend runtime template.

## Intake Template

Use [`templates/real-estate-decision-audit-intake.md`](templates/real-estate-decision-audit-intake.md)
before creating the project. The minimum useful intake includes:

- decision type: buy/sell/hold/lease/develop/renovate/market-entry,
- property or portfolio context,
- market and submarket assumptions,
- rent/revenue assumptions,
- expense and capex assumptions,
- financing assumptions,
- sensitivity/scenario assumptions,
- operational constraints,
- regulatory/legal/tax questions for human experts,
- risk classification and human oversight,
- first 30/60/90-day diligence plan,
- success metrics and monitoring,
- files available for upload.

## Example Brief

Use [`examples/real-estate-decision-audit-brief.md`](examples/real-estate-decision-audit-brief.md)
as a paste-ready example. It is an example brief, not a first-class vertical
template, not a first-class backend runtime template, and not a runtime pack.

## Recommended Upload Files

Upload files only when they improve evidence maturity. Uploads are additive and
do not automatically rerun the workflow.

Recommended files:

- property summary, portfolio summary, investment memo, or decision memo,
- rent roll, lease abstracts, occupancy history, renewal history, or lease-up
  plan,
- trailing operating statements, T12/T3 summaries, budget, expense history, or
  variance notes,
- capex plan, renovation budget, inspection notes, engineering reports, or
  environmental reports,
- market and submarket research, broker opinions, sales comps, rent comps, or
  pipeline/supply notes,
- financing term sheet, debt assumptions, covenant summary, or lender
  correspondence approved for local processing,
- zoning, entitlement, permitting, tax, legal, insurance, or regulatory review
  notes,
- asset-management plan, property-management notes, operational constraints, or
  monitoring dashboards.

Do not upload secrets, credentials, API keys, unrelated personal data, tenant
data that is not approved for local processing, regulated data, lender
confidential material, deal documents under sharing restrictions, or files that
the operator is not allowed to process in the local runtime.

## Operator Runbook

1. Confirm the repo is on the intended branch or tag.
2. Start the local runtime and discover the app port with `docker compose port app 8000`.
3. Run local readiness checks: `/health`, `/runtime/preflight`, and
   `/runtime/release-readiness`.
4. Open `dashboards/index.html` and set the API base URL to the discovered
   local base URL.
5. Create a project using `Strategic Decision Audit framing`.
6. Paste the completed intake or example brief into the brief field.
7. Make the real-estate decision type explicit in the brief.
8. Use the existing risk classification selector and rationale field.
9. Upload supporting files only after checking they are appropriate for local
   processing.
10. Run the project and wait for the `report` phase.
11. Review real estate findings, assumption quality, diligence gaps, expert
    review needs, first 30/60/90-day actions, and monitoring conditions before
    relying on the output.
12. Route investment, financial, legal, tax, appraisal, valuation, lending,
    credit, environmental, engineering, insurance, regulatory, or local market
    claims to the right human expert before sharing anything externally.

## Sample Project Framing

- Dashboard framing: `Strategic Decision Audit framing`.
- API/project type value: `strategic_audit`.
- Real estate package framing lives in the brief/docs only.
- No `real_estate_*` runtime type is introduced.
- Workflow order: `classify -> hypotheses -> gauntlet -> audit -> strategy -> sqi -> monitor -> report`.
- Default posture: local operator workflow with human review required.

This framing does not create real-estate-specific backend execution. It is not
regulated real-estate decision automation, not a guaranteed buy/sell/hold
recommendation engine, and not a first-class backend runtime template.

## Expected Export Checklist

Before delivery, generate or review these artifacts:

- `report` DOCX or PDF: client-safe after review.
- `client_dossier` DOCX or PDF: client-safe after review.
- `client_monitoring_template` XLSX: client-safe after review.
- `operator_dossier` DOCX or PDF: operator-only.
- `operator_monitoring_template` XLSX: operator-only.
- `machine_archive` ZIP: internal archive only.

Use [`v5-OUTPUT-BOUNDARIES.md`](v5-OUTPUT-BOUNDARIES.md) before sending any
client-facing artifact. Confirm that real estate findings are visibly framed as
directional, not guarantees.

## Client-Safe Positioning Language

Use language like:

"This Real Estate Decision Audit is a structured, operator-reviewed decision
review. It organizes property or portfolio context, market and submarket
assumptions, rent/revenue assumptions, expense and capex assumptions, financing
assumptions, scenario sensitivities, operational constraints, diligence gaps,
risk classification, human oversight, and monitoring conditions. It is intended
to help a human decision owner decide what evidence and expert review are
needed before approving, narrowing, deferring, or rejecting a real-estate
decision."

Avoid language that says or implies:

- the engine made the real-estate decision,
- a buy/sell/hold outcome is certain,
- projected rent, expense, capex, financing, tax, value, or market outcomes are
  certain,
- expert real-estate review is complete,
- the local runtime is ready to operate as public SaaS,
- the output is safe to share without human review,
- the package adds a new reasoning mode,
- the package adds a first-class backend runtime template.

## Boundaries And Disclaimers

This is a local operator workflow. It does not add authentication, tenancy,
public deployment hardening, provider routing changes, queue/runtime changes,
report generation changes, export schema changes, regulated vertical logic,
vertical-specific runtime logic, or real-estate-specific backend execution. It
is not regulated real-estate decision automation.

Real estate findings are directional, not guarantees. They depend on
human-supplied property context, portfolio context, market and submarket
assumptions, rent/revenue assumptions, expense and capex assumptions, financing
assumptions, sensitivity/scenario assumptions, operational constraints,
regulatory/legal/tax questions for human experts, risk classification, human
oversight, diligence plans, success metrics, and monitoring plans. Treat them
as planning inputs that need operator and expert judgment, not investment
advice, not financial advice, not legal advice, not tax advice, not appraisal or
valuation certification, and not lending or credit underwriting.

Client-safe means the export path applies deterministic cleanup for the intended
audience. It does not mean the output is correct, complete, legally approved,
tax reviewed, financially approved, valuation certified, appraisal certified,
credit approved, confidential by access control, or safe to forward without
review.

The operator remains accountable for:

- checking evidence maturity,
- validating property or portfolio context,
- reviewing market and submarket assumptions,
- validating rent/revenue, expense, capex, financing, and sensitivity/scenario
  assumptions,
- confirming operational constraints,
- routing regulatory, legal, tax, investment, financial, valuation, appraisal,
  lending, credit, environmental, engineering, insurance, and local market
  questions to the right human expert,
- confirming risk classification and human oversight requirements,
- reviewing first 30/60/90-day diligence actions, success metrics, and
  monitoring,
- deciding what is safe to share,
- keeping operator-only and internal archive artifacts internal.

## Related Docs

- [`v5-REAL-ESTATE-DECISION-DEMO-SCRIPT.md`](v5-REAL-ESTATE-DECISION-DEMO-SCRIPT.md)
- [`v5-AI-READINESS-AUDIT.md`](v5-AI-READINESS-AUDIT.md)
- [`v5-AUTOMATION-ROI-AUDIT.md`](v5-AUTOMATION-ROI-AUDIT.md)
- [`v5-STRATEGIC-DECISION-AUDIT.md`](v5-STRATEGIC-DECISION-AUDIT.md)
- [`v5-DEMO-WORKFLOW.md`](v5-DEMO-WORKFLOW.md)
- [`v5-ICE-OPERATOR-GUIDE.md`](v5-ICE-OPERATOR-GUIDE.md)
- [`v5-INGESTION-CONTRACT.md`](v5-INGESTION-CONTRACT.md)
- [`v5-OUTPUT-BOUNDARIES.md`](v5-OUTPUT-BOUNDARIES.md)
- [`local-runtime-smoke.md`](local-runtime-smoke.md)
