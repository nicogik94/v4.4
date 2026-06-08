# Technology Readiness & Transfer Audit: next_level_recommendations

Return exactly one top-level JSON object for the `next_level_recommendations` phase.
The response is not an array; do not return an array at the top level.
Specifically, do not return multiple JSON objects.
`recommended_actions` must be an array field inside the single object, but the full phase output must be one object.
Emit the gate-critical fields `current_trl`, `next_target_trl`, `required_evidence`, and `advancement_criteria` before long narrative arrays such as `recommended_actions` or `required_tests`.
Keep each array concise and evidence-specific so the single JSON object can close completely.

General controls:
- Make this an evidence-aware assessment.
- Separate facts, assumptions, and missing evidence in the field values when relevant.
- Do not claim TRL certification, legal patentability, guaranteed commercial transfer, or autonomous decision-making.
- Recommendations are operator-reviewed next steps, not autonomous advancement decisions.

Required JSON keys:
```json
{
  "current_trl": 0,
  "next_target_trl": 0,
  "required_evidence": [],
  "advancement_criteria": [],
  "current_phase_name": "",
  "next_phase_name": "",
  "main_gap_to_next_level": "",
  "recommended_actions": [],
  "required_tests": [],
  "expected_deliverables": [],
  "risks_to_reduce": [],
  "suggested_owners": [],
  "estimated_time_range": "",
  "confidence": ""
}
```

Rules:
- Do not recommend advancement without explicit evidence requirements.
- Explain why current evidence is insufficient for the next level.
- Separate technical, IP, industrial, regulatory, and commercial actions in `recommended_actions`.
- Include concrete `required_tests`, `required_evidence`, and `advancement_criteria`.
