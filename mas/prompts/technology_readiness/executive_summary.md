# Technology Readiness & Transfer Audit: executive_summary

Return ONE structured JSON object for the `executive_summary` phase.

General controls:
- Make this an evidence-aware assessment.
- Separate facts, assumptions, and missing evidence in the field values when relevant.
- Do not claim TRL certification, legal patentability, guaranteed commercial transfer, or autonomous decision-making.
- This is an operator-reviewed summary, not a certification, legal opinion, or guaranteed transfer decision.

Required JSON keys:
```json
{
  "current_trl": 0,
  "target_trl": 0,
  "readiness_verdict_code": "not_assessable",
  "readiness_verdict": "",
  "top_blockers": [],
  "recommended_next_step": "",
  "operator_summary": "",
  "confidence": ""
}
```

Allowed readiness verdict codes:
- not_assessable
- pre_trl_diagnosis
- ready_for_proof_of_concept
- ready_for_controlled_validation
- ready_for_relevant_environment_validation
- ready_for_industrial_demo
- ready_for_transfer_discussion
- not_ready_due_to_evidence_gaps

Rules:
- State the defensible current TRL.
- State why a higher TRL is not justified.
- Include top blockers and the next action.
- Do not claim certification, legal patentability, or guaranteed transfer.
