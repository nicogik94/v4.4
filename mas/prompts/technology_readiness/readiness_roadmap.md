# Technology Readiness & Transfer Audit: readiness_roadmap

Return ONE structured JSON object for the `readiness_roadmap` phase.

General controls:
- Make this an evidence-aware assessment.
- Separate facts, assumptions, and missing evidence in the field values when relevant.
- For fields shown as text lists, return arrays of plain strings, not arrays of objects; use object arrays only where the schema explicitly shows objects.
- Do not claim TRL certification, legal patentability, guaranteed commercial transfer, or autonomous decision-making.
- Roadmap gates are operator-reviewed go/no-go points, not autonomous decisions.

Required JSON keys:
```json
{
  "roadmap_phases": [],
  "timeline": [],
  "decision_gates": [],
  "resources_needed": [],
  "go_no_go_criteria": [],
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
