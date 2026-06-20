# Strategic Decision Audit Pilot Readiness

## Purpose and Pilot Boundary

This runbook defines a repeatable acceptance-gated process for attempting a
controlled internal Strategic Decision Audit pilot:

```text
pilot candidate selection
-> repository and local-runtime readiness
-> intake and evidence readiness
-> controlled workflow execution
-> operator review
-> delivery-boundary review
-> human approval decision
-> pilot evidence capture
-> GO / GO WITH CAVEATS / NO-GO
```

Pilot readiness means readiness to attempt a controlled internal/local
operator-led pilot. It does not mean public-SaaS approval, deployment approval,
client-delivery automation, autonomous operation, external-delivery approval, or
proof that an output is correct.

This pilot boundary is intentionally narrow:

- internal/local operator pilot only
- not public-SaaS approval
- not client-delivery automation
- not semantic claim verification
- not autonomous decision-making
- not legal, financial, compliance, safety, or domain approval
- human review remains mandatory before any client-facing delivery, public
  exposure, deployment, auth/security change, or runtime-control change

A `GO` decision under this runbook never authorizes automatic external sharing.
Any external sharing remains subject to human review and the existing output
boundary guidance.

## Roles and Approval Authority

**Operator:** prepares the pilot, runs approved local checks, records evidence,
and does not self-authorize external sharing.

**Decision Owner:** owns the business decision and reviews the recommendation,
tradeoffs, assumptions, caveats, and monitoring plan.

**Delivery Reviewer:** decides whether any candidate external artifact is
suitable for external sharing after review.

**Domain Reviewer, when needed:** validates legal, financial, compliance,
safety, or specialist claims before those claims are relied on or shared.

One person may fill multiple roles only when that is explicitly recorded in the
pilot evidence packet and does not bypass required review. If ownership is
unclear, the pilot is `NO-GO` until ownership is assigned.

## Reference Documents and Source Authority

Use this runbook as the pilot control procedure and use the linked documents as
the detailed source material:

| Area | Source |
| --- | --- |
| Strategic Decision Audit packaged offer | [`../../../docs/v5-STRATEGIC-DECISION-AUDIT.md`](../../../docs/v5-STRATEGIC-DECISION-AUDIT.md) |
| Intake template | [`../../../docs/templates/strategic-decision-audit-intake.md`](../../../docs/templates/strategic-decision-audit-intake.md) |
| Example brief | [`../../../docs/examples/strategic-decision-audit-brief.md`](../../../docs/examples/strategic-decision-audit-brief.md) |
| Output boundaries | [`../../../docs/v5-OUTPUT-BOUNDARIES.md`](../../../docs/v5-OUTPUT-BOUNDARIES.md) |
| ICE operator guide | [`../../../docs/v5-ICE-OPERATOR-GUIDE.md`](../../../docs/v5-ICE-OPERATOR-GUIDE.md) |
| Local runtime smoke guidance | [`../../../docs/local-runtime-smoke.md`](../../../docs/local-runtime-smoke.md) |
| Release-readiness checklist | [`release_readiness_checklist.md`](release_readiness_checklist.md) |
| Orientation only | [`../../../START_HERE.md`](../../../START_HERE.md) |

Source authority for this pilot package:

1. Active runtime behavior and current tests are authoritative when available.
2. `release_readiness_checklist.md` and `docs/local-runtime-smoke.md` are
   authoritative for operational runtime/readiness commands.
3. `docs/v5-OUTPUT-BOUNDARIES.md` is authoritative for artifact classification
   and sharing boundaries.
4. `docs/v5-STRATEGIC-DECISION-AUDIT.md`, the intake template, the example
   brief, and the ICE operator guide are authoritative for packaged-offer and
   intake guidance.
5. `START_HERE.md` is orientation material only.

Do not copy runtime startup commands from orientation material into pilot
evidence. If source documents materially conflict and this hierarchy cannot
resolve the conflict, stop the pilot and escalate before execution.

## Pilot Entry Criteria

Before Gate 1, the Operator records a pilot candidate with:

- named Operator
- named Decision Owner
- named Delivery Reviewer, or an explicitly deferred external-sharing decision
- defined decision question
- alternatives, constraints, timing, and success criteria
- risk classification and rationale
- known evidence and unknowns marked separately
- approved local-processing files only
- confirmation that no secrets, credentials, or inappropriate personal data are
  included
- confirmation that there is no assumption outputs will be shared externally

Entry is `NO-GO` if the decision question is undefined, approval ownership is
unclear, critical evidence is known to be unavailable without review, or the
candidate requires public SaaS, deployment, multi-tenant, autonomous, or
external-delivery behavior.

## Gate 1 — Repository and Local Runtime Readiness

Purpose: confirm the operator is evaluating a known repository state and, when
an actual pilot is run, a reviewed local runtime posture.

Use [`release_readiness_checklist.md`](release_readiness_checklist.md) and
[`../../../docs/local-runtime-smoke.md`](../../../docs/local-runtime-smoke.md)
for the current operational commands. This runbook intentionally does not copy
those commands.

The Operator records:

- branch and commit SHA
- clean Git status
- standard verification outcome
- confirmation that the local-only/operator-only posture was reviewed
- health, runtime preflight, and release-readiness outcomes reviewed when an
  actual pilot is run
- public-exposure and operator-auth posture understood
- known degraded local dependencies documented

Record observed test results from the current run. Do not require or copy a
fixed historic test count.

Gate decision:

- `GO`: repository state is known, status is clean, required verification
  passes, and runtime posture is understood for the pilot scope.
- `GO WITH CAVEATS`: optional/local dependency degradation is understood,
  recorded, unrelated to public exposure or auth, and accepted by the named
  approvers.
- `NO-GO`: public-exposure failure, auth misconfiguration for an authenticated
  review, failing verification, unexplained runtime failure, unknown commit
  state, or unexpected working-tree changes.

## Gate 2 — Intake and Evidence Readiness

Purpose: confirm the pilot has enough bounded information to run as internal
review material without treating generated output as verified fact.

The Operator records:

- completed Strategic Decision Audit intake
- Decision Owner and stakeholders captured
- known evidence versus unknowns explicitly marked
- uploads reviewed for appropriateness and local processing
- confirmation that no secrets, credentials, or prohibited data are included
- unresolved critical questions identified

Uploads and imported evidence do not automatically rewrite previous analysis.
A deliberate rerun is required before new approved information can affect
downstream analysis or recommendations.

Evidence markers, citations, source locators, and resolved references are
traceability aids. They do not prove semantic support, correctness, legal
approval, financial approval, compliance approval, or delivery approval.

Gate decision:

- `GO`: intake is complete enough for a controlled internal run, critical
  unknowns are marked, and evidence is approved for local processing.
- `GO WITH CAVEATS`: non-critical unknowns remain but are recorded and accepted
  by the Decision Owner for internal pilot limits.
- `NO-GO`: critical decision context is missing, prohibited data is present,
  file authority is unclear, or unresolved critical questions make the pilot
  misleading.

## Gate 3 — Controlled Workflow Execution

Purpose: run only the existing Strategic Decision Audit workflow under operator
control.

The Operator:

- creates a `strategic_audit` project
- uses the existing eight-phase workflow:
  `classify -> hypotheses -> gauntlet -> audit -> strategy -> sqi -> monitor -> report`
- records the project identifier or a sanitized internal reference
- does not claim the workflow made the decision

No automation bypasses human review. The workflow provides review material for
the Decision Owner and reviewers; it does not authorize action, delivery, or
external sharing.

Stop and escalate if there is:

- failed phase
- policy block
- kill switch
- budget breaker
- unresolved critical review question
- suspicious or materially incomplete output

Gate decision:

- `GO`: the controlled run completes without blockers and produces internal
  review material.
- `GO WITH CAVEATS`: a bounded, non-security, non-auth, non-public-exposure
  caveat is documented and accepted for internal review only.
- `NO-GO`: a phase fails, a policy/control blocker appears, output is
  suspicious or materially incomplete, or any required human review question is
  unresolved.

## Gate 4 — Operator Review

Purpose: inspect available operator surfaces before any approval decision.

The Operator reviews the available surfaces:

- Overview
- Workspace
- Decision Trace
- report
- evidence-review / citation-resolvability posture, where available
- delivery-review-readiness posture, where available
- monitoring plan
- uncertainty, caveats, evidence gaps, stale inputs, blocked items, and
  unresolved review questions

Resolved citation markers remain traceability signals, not proof of semantic
claim support or delivery approval.

Gate decision:

- `GO`: the Operator can present the output as internal review material with
  caveats, uncertainty, evidence gaps, and monitoring posture clearly recorded.
- `GO WITH CAVEATS`: review limitations are explicit, assigned, and accepted by
  the Decision Owner for a bounded internal pilot.
- `NO-GO`: unresolved critical questions, stale or contradictory evidence,
  suspicious output, unreviewed specialist claims, or unclear monitoring
  ownership would make the pilot misleading.

## Gate 5 — Delivery-Boundary Review

Purpose: decide whether any artifact may even be considered for external
sharing after human review.

Use [`../../../docs/v5-OUTPUT-BOUNDARIES.md`](../../../docs/v5-OUTPUT-BOUNDARIES.md)
as the artifact boundary source of truth. Separate artifacts into:

- candidate client-safe artifacts after human review
- operator-only artifacts
- internal-only machine archives

Client-safe formatting is not proof of correctness, completeness, legal
approval, financial approval, confidentiality, or safe forwarding without
review.

External sharing requires explicit Delivery Reviewer approval. A successful
internal pilot does not automatically authorize any client-facing delivery.

Gate decision:

- `GO`: delivery boundaries are understood, no external sharing is assumed, and
  any candidate external artifact has a separate named review path.
- `GO WITH CAVEATS`: external sharing is deferred or limited, with the caveat
  recorded and accepted by the Delivery Reviewer.
- `NO-GO`: an operator-only or internal archive artifact is proposed for client
  delivery, candidate output contains boundary leaks, or Delivery Reviewer
  approval is missing for external sharing.

## Pilot Evidence Packet

Maintain a concise internal record containing:

- pilot date
- branch
- commit SHA
- verification summary
- local health/preflight/release-readiness outcome, when actually run
- risk classification and rationale
- project identifier or sanitized internal reference
- Operator, Decision Owner, Delivery Reviewer, and Domain Reviewer roles
- final pilot decision
- known caveats
- external-sharing decision
- retained internal artifacts
- follow-up actions and owner

Do not require or retain raw prompts, chain-of-thought, API keys, customer
secrets, credentials, or unnecessary personal data.

## Final Pilot Decision

**GO:** all gates pass; a controlled internal pilot may proceed; human review
and separate external-sharing approval remain mandatory.

**GO WITH CAVEATS:** no security/public-exposure/auth blocker exists; named
approvers accept specific documented caveats; the internal pilot may proceed
only within those limits.

**NO-GO:** a mandatory gate fails, critical evidence/review issue remains
unresolved, verification/runtime posture is unsafe or unknown, or approval
ownership is unclear.

## Failure, Escalation, and Recovery

For `NO-GO` or a failed gate:

- stop the pilot
- do not share externally
- preserve sanitized evidence of the failure
- record the owner and next action
- identify whether remediation is documentation, operator training, runtime
  triage, or a new scoped engineering wave
- do not change runtime behavior through this runbook

If the failure points to source-code, test, API, dashboard, runtime, auth,
export, queue, workflow, deployment, or policy behavior, open a separately
scoped engineering wave. Do not patch behavior as part of pilot readiness.

## Post-Pilot Learning

After a completed or stopped internal pilot, capture:

- intake gaps
- evidence gaps
- workflow/runtime blockers
- review friction
- unclear operator UX
- export/delivery confusion
- candidate future waves
- sanitized screenshots or sample artifacts that could inform a later
  visual-system/UX exploration

This is retrospective learning, not automated self-modification. MAS does not
modify its workflow, prompts, policies, runtime controls, or delivery behavior
based on pilot observations without a separate human-approved change.

## Non-Goals

This runbook does not create or authorize:

- public SaaS
- multi-tenancy
- autonomous monitoring/actions
- new auth model
- deployment expansion
- semantic claim-verification claims
- runner refactor
- new backend workflow
- external delivery without human review
- new workflow routing, phase order, queues, run state, reports, exports,
  preflight, evals, API contracts, dashboards, or runtime behavior
