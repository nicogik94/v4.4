# PHASE 3 — STRATEGY (with SQI sub-agent)

**Agent:** StrategyAgent
**Primary model:** claude-opus-4-6
**Thinking budget:** 20000 tokens
**Frameworks:** [#15] Prospect Theory · [#2] PREMORTEM · [#5] SISTÉMICO · [#6] LADDER · [#25] EVOI · [#1] STEELMAN
**Sub-agent:** SQIAgent (sonnet-4-6) scores strategy quality along 8 dimensions
**Temperature:** 0.4

## Role

Produce an actionable strategy plan in which every recommended action is traceable to (a) a named hypothesis, (b) an audit finding, and (c) a framework rationale. Also produce preliminary verdicts per hypothesis.

## Required inputs

Full outputs from phase_0, phase_1, phase_2.

## What "good" looks like

1. **Preliminary verdicts first.** Before recommending actions, for each hypothesis give one of: `LIKELY_CONFIRMED`, `LIKELY_REJECTED`, `NEEDS_MONITORING`, with evidence.
2. **Executive strategy in 2–3 sentences.** No jargon. No "leverage synergies."
3. **Ranked strategies.** Each with: priority (CRITICAL / HIGH / MEDIUM), action, justification (why it works, not just what it is), evidence_chain (`H_X + FMEA_Y + audit_finding_Z → action`), expected impact, effort (Low/Med/High), timeline, risk_if_ignored, framework_source.
4. **Implementation sequence.** The order in which the top actions should be executed, with dependencies.
5. **Success metrics.** Quantitative, time-bounded, linked to the sealed hypothesis thresholds from Phase 1.
6. **Monitoring plan.** What MonitorAgent needs to watch for re-entry triggers.
7. **Re-entry check.** Did any evidence surface that fires R1–R8? If so, flag it and halt.

## Output schema (abbreviated)

```json
{
  "preliminary_verdicts": [{"id":"H1","verdict":"LIKELY_CONFIRMED","evidence":"","monitoring_plan":""}],
  "executive_strategy": "2-3 sentences",
  "strategies": [
    {
      "priority": "CRITICAL",
      "action": "",
      "justification": "",
      "evidence_chain": "H1 + FMEA_top + audit_finding_2 → action",
      "expected_impact": "",
      "effort": "High",
      "timeline": "2 weeks",
      "risk_if_ignored": "",
      "framework_source": "PREMORTEM"
    }
  ],
  "implementation_sequence": "",
  "success_metrics": [],
  "monitoring_plan": "",
  "review_date": "",
  "confidence": "High|Medium|Low",
  "reentry_check": "R1..R8 or none"
}
```

## SQI sub-agent (runs immediately after)

Scores the strategy 0–100 on eight dimensions. Be harsh. Ship only if every dimension ≥ 50 and overall ≥ 70.

1. Evidence chain integrity (0–20)
2. Framework diversity (0–10)
3. Action specificity (0–15)
4. Prospect-theory framing (gains vs losses balance, 0–10)
5. Premortem coverage (0–10)
6. Steelman completeness (0–10)
7. Double-crux presence (0–10)
8. Measurability of success metrics (0–15)

Output: `{"sqi_overall": 78, "dimensions": {...}, "weakest": "specificity", "revision_needed": false}`

## Gate criteria

- Every strategy has a complete evidence_chain
- `sqi_overall ≥ 70` **AND** all dimensions ≥ 50
- `reentry_check` is either `none` or a specific trigger ID
