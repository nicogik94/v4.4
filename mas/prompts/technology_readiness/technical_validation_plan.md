# Technology Readiness & Transfer Audit: technical_validation_plan

Return exactly one top-level JSON object for the `technical_validation_plan` phase.
The response is not an array; do not return an array at the top level.
Do not return multiple JSON objects.
Emit the gate-critical fields `acceptance_criteria` and `evidence_to_collect` before the long `validation_tests` array.
Keep each array concise and evidence-specific so the single JSON object can close completely.

General controls:
- Make this an evidence-aware assessment.
- Separate facts, assumptions, and missing evidence in the field values when relevant.
- Do not claim TRL certification, legal patentability, guaranteed commercial transfer, or autonomous decision-making.
- This plan defines validation evidence to collect; it does not certify performance.

Required JSON keys:
```json
{
  "acceptance_criteria": [],
  "evidence_to_collect": [],
  "validation_tests": [],
  "measurement_plan": [],
  "failure_modes": [],
  "confidence": ""
}
```

Rules:
- Validation tests should state purpose, method, environment, required sample or run count if known, owner role, and evidence output.
- Acceptance criteria must be defensible and mark operator confirmation where thresholds are unknown.
- Include reproducibility and controlled-validation evidence when the target is TRL 4 or higher.
