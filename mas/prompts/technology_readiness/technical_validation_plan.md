# Technology Readiness & Transfer Audit: technical_validation_plan

Return ONE structured JSON object for the `technical_validation_plan` phase.

General controls:
- Make this an evidence-aware assessment.
- Separate facts, assumptions, and missing evidence in the field values when relevant.
- Do not claim TRL certification, legal patentability, guaranteed commercial transfer, or autonomous decision-making.
- This plan defines validation evidence to collect; it does not certify performance.

Required JSON keys:
```json
{
  "validation_tests": [],
  "acceptance_criteria": [],
  "measurement_plan": [],
  "failure_modes": [],
  "evidence_to_collect": [],
  "confidence": ""
}
```

Rules:
- Validation tests should state purpose, method, environment, required sample or run count if known, owner role, and evidence output.
- Acceptance criteria must be defensible and mark operator confirmation where thresholds are unknown.
- Include reproducibility and controlled-validation evidence when the target is TRL 4 or higher.
