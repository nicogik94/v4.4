# Technology Readiness & Transfer Audit: trl_diagnosis

Return ONE structured JSON object for the `trl_diagnosis` phase.

General controls:
- Make this an evidence-aware assessment.
- Separate facts, assumptions, and missing evidence in the field values when relevant.
- Do not claim TRL certification, legal patentability, guaranteed commercial transfer, or autonomous decision-making.
- This is not certification; it is an operator-reviewed readiness assessment.

Required JSON keys:
```json
{
  "current_trl": 0,
  "target_trl": 0,
  "confidence": "",
  "current_phase_name": "",
  "evidence_supporting_current_trl": [],
  "why_not_higher": "",
  "evidence_gaps": [],
  "legal_or_certification_disclaimer": ""
}
```

TRL roadmap:
- Pre-TRL 3: Diagnosis, 1-2 months.
- TRL 3: Protection and proof of concept, 3-6 months.
- TRL 4: Controlled technical validation, 6-12 months.
- TRL 5: Relevant environment validation, 9-18 months.
- TRL 6: Demonstration and transfer, 12-24 months.

Rules:
- Do not assign TRL 7+ without operationally relevant or real-environment evidence.
- Do not assign TRL 8+ without complete system validation.
- Do not assign TRL 9 without sustained real-world operation.
- Always explain why the next TRL is not yet justified in `why_not_higher`.
