# Technology Readiness & Transfer Audit: scientific_inventory

Return ONE structured JSON object for the `scientific_inventory` phase.

General controls:
- Make this an evidence-aware assessment.
- Separate facts, assumptions, and missing evidence in the field values when relevant.
- Do not claim TRL certification, legal patentability, guaranteed commercial transfer, or autonomous decision-making.
- Use operator-reviewed language and cite only supplied or operator-confirmed evidence.

Required JSON keys:
```json
{
  "scientific_basis": [],
  "critical_components": [],
  "current_experiments": [],
  "known_limitations": [],
  "evidence_items": [],
  "missing_evidence": [],
  "confidence": ""
}
```

Rules:
- `evidence_items` must describe available evidence with category, source or locator if available, and what it supports.
- Use these evidence categories when possible: scientific_basis, proof_of_concept, reproducibility, controlled_validation, relevant_environment, industrial_validation, cost_scalability, regulatory_review, ip_review, partner_feedback.
- If evidence is not supplied, mark the item as missing instead of inventing results.
