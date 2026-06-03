# Technology Readiness & Transfer Audit: research_industry_alignment

Return ONE structured JSON object for the `research_industry_alignment` phase.

General controls:
- Make this an evidence-aware assessment.
- Separate facts, assumptions, and missing evidence in the field values when relevant.
- Do not claim TRL certification, legal patentability, guaranteed commercial transfer, or autonomous decision-making.
- Use operator-reviewed language and avoid unsupported market certainty.

Required JSON keys:
```json
{
  "criteria_scores": {},
  "overall_alignment_score": 0,
  "top_alignment_strengths": [],
  "top_alignment_gaps": [],
  "prioritized_industrial_applications": [],
  "confidence": ""
}
```

Include all 10 research-industry criteria in `criteria_scores`:
- technical_novelty
- patentable_potential
- industrial_application
- functional_advantage
- reproducibility
- scalability
- potential_cost
- industrial_interest
- regulatory_barriers
- trl_4_6_compatibility

Each criterion must be an object with:
```json
{
  "score": 1,
  "evidence": "",
  "gap": "",
  "recommendation": ""
}
```

Rules:
- Scores must be 1-5.
- Compute `overall_alignment_score` as the average of valid 1-5 scores.
- If a criterion lacks evidence, score conservatively and explain the gap.
