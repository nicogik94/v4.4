# AI Readiness Audit Example Brief

This is a paste-ready example brief for demo use, not a first-class vertical
template, not a first-class backend runtime template, and not a runtime pack. It
is for a local operator workflow. It is not legal advice, not compliance
certification, not security certification, not public SaaS, not a guaranteed AI
transformation plan, and not a new reasoning mode.

Human review is required before any output is shared or acted on. Readiness
findings are directional, not guarantees.

## Readiness Question

Is Northstar Benefits ready to pilot AI-assisted support intake triage for
internal benefits questions in the next 90 days, or should the team first close
process, data, governance, and adoption gaps?

## Business Goals And Use-Case Fit

Northstar Benefits wants to reduce time spent routing repetitive internal
benefits questions while preserving HR review and employee trust. The candidate
use case is AI-assisted summarization and categorization of incoming benefits
questions before an HR specialist reviews and responds. The decision is whether
to approve a narrow pilot, narrow the use case further, defer until readiness
gaps are closed, or gather more evidence before a pilot.

## Process Maturity

The support process is partially documented. Questions arrive through email,
Slack, and a benefits portal. HR coordinators manually categorize the request,
look up policy references, and route complex cases to specialists. Edge cases
include leave, accommodations, payroll, medical plan disputes, and employee
relations issues. Human review is already required before any response leaves
the HR team, but escalation criteria are inconsistently documented.

## Data Availability And Quality

- Historical intake messages are available in the benefits portal, but email
  and Slack records are fragmented.
- The team has a policy document library, but some documents are outdated or
  duplicated.
- The current category labels are inconsistent across coordinators.
- No approved sample-safe extract has been prepared yet.
- The strongest data gap is whether source documents are current enough to
  support reliable summaries for human review.

## Tool Stack And Integration Constraints

The team uses a benefits portal, shared policy documents, Slack, email, and an
HRIS. The HRIS cannot be connected during the pilot without security and privacy
approval. Any pilot must preserve source links, avoid autonomous employee
responses, and operate inside a reviewed workflow. SSO, access control, logging,
and retention requirements are not fully documented.

## Team Capability And Adoption Risk

The decision owner is the VP of People Operations. The process owner is the HR
operations manager. HR coordinators would use the tool; benefits specialists
would review complex cases. The team has limited AI experience and is concerned
about employee trust, inconsistent review behavior, and extra workload if the
pilot creates too many false positives. Change management and training will be
needed before any live use.

## Governance, Privacy, And Security Constraints

Employee benefits questions may contain sensitive personal information. Privacy,
security, legal, HR, and IT review are required before uploading any real
employee content or connecting source systems. The pilot must not automate
employee-impacting decisions, must not send autonomous responses, and must keep
human review and escalation visible. Auditability should include source links,
reviewer identity, escalation reason, and monitoring records.

## Risk Classification And Human Oversight

Suggested classification: `limited_risk`, subject to legal, privacy, security,
HR, and compliance review. The rationale is that the pilot supports internal
triage and summarization, but the subject matter can affect employees and may
include sensitive data. Human oversight must include specialist review before
any response, escalation rules for sensitive categories, override ability, and
weekly monitoring.

## First 30/60/90-Day Action Plan

- First 30 days: confirm owner, scope the narrowest supported use case, prepare
  sample-safe examples, refresh policy documents, document escalation rules, and
  complete privacy/security review for local evidence handling.
- First 60 days: validate category labels, define reviewer workflow, create
  training notes, test summaries against sample-safe cases, and set monitoring
  thresholds for quality, cycle time, and escalation.
- First 90 days: run a limited reviewed pilot only if governance and data gaps
  are closed, compare results against baseline metrics, and decide whether to
  scale, narrow, or defer.

## Success Metrics And Monitoring

- Primary metrics: intake routing cycle time, summary quality, escalation
  accuracy, reviewer workload, and coordinator adoption.
- Baseline: current cycle time is estimated at one to two business days, but
  the source is manager observation rather than a validated export.
- Monitoring cadence: weekly during any pilot.
- Warning threshold: more than 10 percent of reviewed summaries require major
  correction or coordinator workload increases.
- Stop threshold: any autonomous employee response, unapproved sensitive data
  use, repeated escalation failure, or unresolved privacy/security issue.

## Known Evidence

- HR coordinators estimate 300 to 450 benefits questions per month.
- The highest-volume topics are eligibility, plan documents, dependents, leave,
  and payroll timing.
- Benefits specialists say incomplete context causes repeated back-and-forth.
- Policy documents exist, but ownership and freshness are inconsistent.
- The VP of People Operations is willing to sponsor a reviewed pilot only if
  privacy and security approve the evidence handling plan.

## Unknowns

- Whether current source documents are sufficiently accurate and fresh.
- Whether historical request categories are reliable enough for evaluation.
- Whether sample-safe evidence can cover sensitive edge cases.
- Whether review workload decreases or increases after AI assistance.
- Whether employees and HR coordinators will trust the reviewed workflow.

## What A Good Recommendation Should Resolve

- Whether Northstar should approve, narrow, defer, or gather more evidence
  before a pilot.
- Which readiness gaps are most material.
- Which governance, privacy, security, data, process, adoption, and oversight
  requirements must be closed first.
- What first 30/60/90-day actions should be assigned.
- What monitoring signals define success, warning, and stop conditions.
- What evidence would change the readiness finding.

## Suggested Files / Evidence To Upload If Available

- Business goals or initiative charter.
- Benefits support process map or SOP.
- Policy library inventory and freshness notes.
- Sample-safe ticket summaries or anonymized category counts approved for local
  processing.
- Tool-stack inventory and integration constraints.
- Privacy, security, legal, HR, compliance, or IT review notes.
- Training, adoption, or change-readiness notes.
- Baseline metrics or dashboard summaries.

## Expected Output Types

- Client-safe after review: `report`, `client_dossier`,
  `client_monitoring_template`.
- Operator-only: `operator_dossier`, `operator_monitoring_template`.
- Internal archive: `machine_archive`.

## Human Review Reminder

Treat hypotheses, scores, readiness findings, thresholds, and recommendations
as review material. The operator must verify evidence maturity, validate
business goals and use-case fit, confirm process and data assumptions, route
legal, compliance, security, privacy, HR, IT, finance, safety, and domain claims
to the right reviewers, resolve critical review questions, confirm human
oversight, and decide what is safe to share.
