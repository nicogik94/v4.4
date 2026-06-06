# Technology Readiness & Transfer Audit: ip_protection_axis

Return ONE structured JSON object for the `ip_protection_axis` phase.

General controls:
- Make this an evidence-aware assessment.
- Separate facts, assumptions, and missing evidence in the field values when relevant.
- Do not claim TRL certification, legal patentability, guaranteed commercial transfer, or autonomous decision-making.
- Do not claim legal patentability. This phase can only flag preliminary IP review axes.

Required JSON keys:
```json
{
  "material_composition": {},
  "synthesis_method": {},
  "specific_use": {},
  "device_or_system": {},
  "critical_parameters": {},
  "know_how": {},
  "ip_risk_notes": [],
  "specialist_review_required": true,
  "confidence": ""
}
```

For each IP protection axis, use:
```json
{
  "preliminary_assessment": "",
  "evidence": [],
  "gap": "",
  "disclosure_risk": "",
  "recommended_review": ""
}
```

Rules:
- Use preliminary, uncertain, promising, weak, or requires specialist review language.
- Flag disclosure risk before publication, partner conversations, grant disclosures, conference presentations, or external demos.
- `specialist_review_required` should remain true unless the operator supplied a documented specialist review.
