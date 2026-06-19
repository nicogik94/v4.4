# Wave 11.9 - Strategic-Audit Phase-Sequence Alignment

## Goal

Remove one duplicated strategic-audit phase-sequence definition by aligning `orchestrator.WORKFLOW_PHASE_SEQUENCE` with `workflow_templates.STRATEGIC_AUDIT_PHASE_SEQUENCE`.

## Scope

- Replace the duplicated tuple literal in `orchestrator.py` with a direct alias to `STRATEGIC_AUDIT_PHASE_SEQUENCE`.
- Import `STRATEGIC_AUDIT_PHASE_SEQUENCE` from `workflow_templates.py`.
- Extend the existing equality-based alignment test in `tests/test_technology_readiness.py`.
- Update the current progress context for the Wave 11.9 baseline and outcome.

## Non-goals

- No workflow-runner consolidation.
- No workflow semantic changes.
- No phase-order changes.
- No API, queue/run-state, LangGraph, eval, policy-gate, routing, readiness, report/export, auth, preflight, script, dashboard, or database changes.
- No public SaaS, multi-tenancy, autonomous action, public-user auth, or deployment behavior.

## Source alignment

`orchestrator.WORKFLOW_PHASE_SEQUENCE` now aliases `workflow_templates.STRATEGIC_AUDIT_PHASE_SEQUENCE` directly. The strategic-audit phase sequence remains:

1. `classify`
2. `hypotheses`
3. `gauntlet`
4. `audit`
5. `strategy`
6. `sqi`
7. `monitor`
8. `report`

## Runtime behavior

No runtime behavior changed. The alias points to the same immutable tuple value already returned for `strategic_audit` by `get_workflow_phase_sequence`.

API sequential runner, LangGraph, queue/run-state, evals, policy gates, routing, and readiness remain untouched.

## Verification summary

Pre-edit checks confirmed:

- `WORKFLOW_PHASE_SEQUENCE` was a duplicated strategic-audit tuple literal.
- `STRATEGIC_AUDIT_PHASE_SEQUENCE` was an immutable tuple with the same value.
- No tracked Python consumer used identity-sensitive `is` checks for `WORKFLOW_PHASE_SEQUENCE`.
- No tracked Python consumer mutated `WORKFLOW_PHASE_SEQUENCE`.
- Importing `STRATEGIC_AUDIT_PHASE_SEQUENCE` into `orchestrator.py` did not create an import cycle in the smoke check.
- `tests/test_technology_readiness.py` already contained equality-based alignment coverage.

Post-edit verification:

- `git diff --check` produced no output.
- Focused pytest passed: `146 passed, 3 subtests passed`.
- Optional template/API pytest passed: `7 passed`.
- `scripts/wave_verify.sh` passed: `739 passed, 1 warning, 72 subtests passed`.
