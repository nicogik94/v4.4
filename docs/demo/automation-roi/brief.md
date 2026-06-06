# TEST Aut-ROI Canonical Demo

This is a paste-ready canonical Automation ROI Audit demo brief. It uses
synthetic demo data only and includes no private or client data.

## Decision Question

Which internal automation candidate should proceed to a high-risk Sprint 0
validation: sales proposal drafting, support triage, finance reconciliation, or
customer success renewal preparation?

## Context

Leadership wants measurable ROI within 60 days without uncontrolled quality,
compliance, finance, or operational risk. The team has one part-time
implementation owner and can use existing systems only. No customer-facing
autonomous action is allowed.

## Constraints

- Use existing systems and approved data access.
- No autonomous customer-facing action.
- Finance, legal, security, privacy, and operations review are required before
  implementation.
- Engineering capacity is limited to one part-time owner.
- The evidence window is 60 days.
- The project should be treated as `high_risk`.

## Known Evidence

- Sales proposal drafting uses about 7 weekly hours and 36 monthly cases.
- Support triage uses about 18 weekly hours and 420 monthly cases.
- Finance reconciliation uses about 10 weekly hours and 160 monthly cases.
- Customer success renewal preparation uses about 11 weekly hours and 74
  monthly cases.
- Review owners are named, but review capacity is constrained.
- Tool budget assumptions are preliminary and require finance review.
- Error, rework, and escalation rates are estimated from synthetic demo notes.

## Unknowns

- True baseline time by workflow.
- Input data quality and completeness.
- Human review burden after automation.
- Adoption risk by team.
- Integration effort and data-access constraints.
- Legal, compliance, security, and privacy review scope.
- Whether measured savings can be proven during Sprint 0.

## Success Criteria

- One candidate is selected for Sprint 0 validation or all candidates are
  deferred with clear evidence gaps.
- Expected time savings are measurable during the 60-day evidence window.
- Quality remains stable or improves.
- A named owner accepts weekly monitoring responsibility.
- At least one clear stop/change-course threshold is defined.
- Evidence pack is ready for human review before client delivery.

## Suggested Files / Evidence To Upload If Available

- `automation_candidate_metrics.csv`
- `current_process_notes.txt`
- `tool_budget_assumptions.txt`
- `risk_and_compliance_notes.txt`
- `sprint0_evidence_pack_notes.txt`

## Expected Output Types

- `report` PDF.
- `client_dossier` PDF.
- `operator_dossier` PDF.
- `client_monitoring_template` XLSX.
- `operator_monitoring_template` XLSX.

## Review Boundary

This demo should produce a hypothesis-driven diagnostic, not a measured audit.
Validate before client delivery. Human review is required for high-risk/finance
automation, and evidence gaps still remain before implementation.
