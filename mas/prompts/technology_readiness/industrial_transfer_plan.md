# Technology Readiness & Transfer Audit: industrial_transfer_plan

Return ONE structured JSON object for the `industrial_transfer_plan` phase.

General controls:
- Make this an evidence-aware assessment.
- Separate facts, assumptions, and missing evidence in the field values when relevant.
- Do not claim TRL certification, legal patentability, guaranteed commercial transfer, or autonomous decision-making.
- Do not promise transfer; describe conditions for an operator-reviewed transfer discussion.

Required JSON keys:
```json
{
  "ideal_industrial_partner": "",
  "partner_validation_needed": [],
  "minimum_transfer_package": [],
  "transfer_model_options": [],
  "negotiation_risks": [],
  "evidence_required_before_transfer": [],
  "confidence": ""
}
```

Rules:
- Identify partner characteristics, not named partners unless supplied by the operator.
- Include technical data package, reproducibility evidence, IP review status, regulatory notes, cost/scalability evidence, and demo evidence when relevant.
- Keep negotiation risk language preliminary and evidence-backed.
