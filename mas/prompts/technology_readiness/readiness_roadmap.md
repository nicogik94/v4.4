# Technology Readiness & Transfer Audit: readiness_roadmap

Return exactly one top-level JSON object for the `readiness_roadmap` phase.
The response is not an array; do not return an array at the top level.
Do not return multiple JSON objects.
Emit the gate-critical fields `decision_gates` and `go_no_go_criteria` before the long `roadmap_phases` array.
Keep each array concise and evidence-specific so the single JSON object can close completely.

General controls:
- Make this an evidence-aware assessment.
- Separate facts, assumptions, and missing evidence in the field values when relevant.
- Do not claim TRL certification, legal patentability, guaranteed commercial transfer, or autonomous decision-making.
- Roadmap gates are operator-reviewed go/no-go points, not autonomous decisions.

Required JSON keys:
```json
{
  "decision_gates": [],
  "go_no_go_criteria": [],
  "roadmap_phases": [],
  "timeline": [],
  "resources_needed": [],
  "confidence": ""
}
```

Required roadmap ranges:
- Pre-TRL 3: Diagnosis, 1-2 months.
- TRL 3: Protection and proof of concept, 3-6 months.
- TRL 4: Controlled technical validation, 6-12 months.
- TRL 5: Relevant environment validation, 9-18 months.
- TRL 6: Demonstration and transfer, 12-24 months.

Rules:
- Include evidence needed at each gate.
- Each `roadmap_phases` item should include trl, phase_name, time_range, objective, evidence_needed, and decision_gate.
- State go/no-go criteria conservatively when evidence is missing.
