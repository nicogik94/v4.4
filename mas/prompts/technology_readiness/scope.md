# Technology Readiness & Transfer Audit: scope

Return ONE structured JSON object for the `scope` phase.

General controls:
- Make this an evidence-aware assessment.
- Separate facts, assumptions, and missing evidence in the field values when relevant.
- Do not claim TRL certification, legal patentability, guaranteed commercial transfer, or autonomous decision-making.
- Use operator-reviewed language: "preliminary", "requires review", "not yet supported", and "operator should confirm" where evidence is incomplete.

Required JSON keys:
```json
{
  "technology_name": "",
  "assessment_boundary": "",
  "target_environment": "",
  "intended_next_milestone": "",
  "stakeholders": [],
  "constraints": [],
  "assumptions": [],
  "confidence": ""
}
```

Rules:
- Define the assessment boundary narrowly enough that TRL and transfer readiness can be evaluated.
- State the intended next milestone without implying approval or certification.
- If the technology, environment, stakeholders, or constraints are unclear, mark them as missing evidence or assumptions.
