# Wave 12.0 - Strategic Decision Audit Pilot Readiness

## Goal

Create a repeatable, operator-led Strategic Decision Audit pilot-readiness
package that connects existing guidance into an internal pilot procedure.

The pilot flow is:

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

## Scope

- Add `docs/runbooks/strategic_decision_audit_pilot_readiness.md`.
- Update this Wave 12.0 context note.
- Add a minimal Wave 12.0 entry to `ai_context/v4_v5_current_progress.md`.
- Keep the change documentation/operator-process only.

## Non-goals

- No real client pilot.
- No Docker, local service startup, provider calls, uploads, exports, or
  generated deliverables.
- No source-code, test, script, dashboard, Docker, database, API, runtime, auth,
  queue, workflow, policy-gate, readiness, report, export, preflight, or eval
  changes.
- No public SaaS, multi-tenancy, public-user account system, autonomous action,
  deployment expansion, or new auth model.
- No semantic claim-verification or delivery-approval claim.

## Source Documents Reviewed

- `START_HERE.md`
- `docs/v5-STRATEGIC-DECISION-AUDIT.md`
- `docs/v5-OUTPUT-BOUNDARIES.md`
- `docs/v5-ICE-OPERATOR-GUIDE.md`
- `docs/local-runtime-smoke.md`
- `docs/templates/strategic-decision-audit-intake.md`
- `docs/examples/strategic-decision-audit-brief.md`
- `docs/demo-briefs/b2b-saas-pilot-expansion.md`
- `docs/runbooks/release_readiness_checklist.md`
- `ai_context/v4_v5_current_progress.md`
- `ai_context/waves/wave_11_8_workflow_runner_consolidation_discovery.md`
- `ai_context/waves/wave_11_9_phase_sequence_alignment.md`

The intake and example brief paths above are the corrected tracked paths under
`docs/templates/` and `docs/examples/`.

## Existing Pilot-Related Docs Discovered

Discovery command output:

```text
/home/nicolas/dev/v4.4/docs/demo-briefs/b2b-saas-pilot-expansion.md
/home/nicolas/dev/v4.4/docs/examples/strategic-decision-audit-brief.md
/home/nicolas/dev/v4.4/docs/templates/strategic-decision-audit-intake.md
/home/nicolas/dev/v4.4/docs/v5-STRATEGIC-DECISION-AUDIT.md
```

No existing pilot-readiness runbook was present under `mas/docs/runbooks/`, and
the discovered documents did not provide the required complete acceptance-gated
pilot-readiness structure.

## Source-Authority Treatment

The new runbook uses this hierarchy:

1. Active runtime behavior and current tests are authoritative when available.
2. `docs/runbooks/release_readiness_checklist.md` and
   `docs/local-runtime-smoke.md` are authoritative for operational runtime and
   readiness commands.
3. `docs/v5-OUTPUT-BOUNDARIES.md` is authoritative for artifact classification
   and sharing boundaries.
4. `docs/v5-STRATEGIC-DECISION-AUDIT.md`, the intake template, the example
   brief, and the ICE operator guide are authoritative for packaged-offer and
   intake guidance.
5. `START_HERE.md` is orientation material only.

The runbook does not copy runtime startup commands from orientation material.
It references the runtime/readiness documents for operational checks and treats
any material unresolved conflict as a stop-and-escalate condition.

## Pilot Flow Defined

The runbook defines:

- pilot entry criteria
- Gate 1: repository and local runtime readiness
- Gate 2: intake and evidence readiness
- Gate 3: controlled workflow execution
- Gate 4: operator review
- Gate 5: delivery-boundary review
- pilot evidence packet
- final pilot decision
- failure, escalation, and recovery
- post-pilot learning
- non-goals

Gate 3 is explicitly limited to creating a `strategic_audit` project and using
the existing eight-phase workflow:

```text
classify -> hypotheses -> gauntlet -> audit -> strategy -> sqi -> monitor -> report
```

## Roles and Human-Review Boundary

The runbook defines these roles:

- Operator
- Decision Owner
- Delivery Reviewer
- Domain Reviewer, when needed

One person may fill multiple roles only when that is explicitly recorded and
does not bypass required review. Human review remains mandatory before any
client-facing delivery, public exposure, deployment, auth/security change, or
runtime-control change.

## Delivery-Boundary Treatment

The runbook separates:

- candidate client-safe artifacts after human review
- operator-only artifacts
- internal-only machine archives

It states that client-safe formatting is not proof of correctness,
completeness, legal approval, financial approval, confidentiality, or safe
forwarding without review. External sharing requires explicit Delivery Reviewer
approval, and a successful internal pilot does not authorize client-facing
delivery.

## Evidence-Packet Definition

The evidence packet contains:

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

It does not require raw prompts, chain-of-thought, API keys, customer secrets,
credentials, or unnecessary personal data.

## GO / GO WITH CAVEATS / NO-GO Definitions

`GO`: all gates pass; a controlled internal pilot may proceed; human review and
separate external-sharing approval remain mandatory.

`GO WITH CAVEATS`: no security/public-exposure/auth blocker exists; named
approvers accept specific documented caveats; the internal pilot may proceed
only within those limits.

`NO-GO`: a mandatory gate fails, critical evidence/review issue remains
unresolved, verification/runtime posture is unsafe or unknown, or approval
ownership is unclear.

## Failure and Escalation Treatment

For `NO-GO` or a failed gate, the runbook requires the Operator to stop the
pilot, avoid external sharing, preserve sanitized evidence, record owner and
next action, classify the remediation path, and avoid changing runtime behavior
through the runbook.

Runtime, auth, export, API, workflow, queue, policy, dashboard, source, or test
issues require a separately scoped engineering wave.

## Verification Summary

Verification completed from `/home/nicolas/dev/v4.4/mas`:

- `git diff --check`: passed with no output.
- `git diff --name-only`: showed the tracked progress context update; new
  untracked docs were confirmed with `git status --short --untracked-files=all`.
- `git status --short --untracked-files=all`: showed only the three allowed
  app-relative files.
- Structure smoke check: `pilot-readiness runbook structure check passed`.
- `scripts/wave_verify.sh`: passed with `739 passed, 1 warning, 72 subtests
  passed`; cleanup restored the tracked SQLite test artifact and left only the
  three allowed app-relative files changed.

## Recommendation for the First Controlled Internal Pilot

Use the example Northstar-style Strategic Decision Audit only as a synthetic
internal dry-run candidate, or use a low-risk internal business decision with a
named Decision Owner and Delivery Reviewer. Defer external sharing by default
for the first pilot. Require a clean repository state, current verification,
completed intake, explicit evidence/unknown markers, and a written evidence
packet before recording `GO` or `GO WITH CAVEATS`.

Do not perform a real client pilot until a human reviewer accepts this runbook,
the first internal candidate, and the evidence-retention boundary.
