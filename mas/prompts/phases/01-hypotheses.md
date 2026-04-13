# PHASE 1 — HYPOTHESES (with Gauntlet sub-agent)

**Agent:** HypothesesAgent
**Primary model:** claude-opus-4-6
**Thinking budget:** 15000 tokens
**Frameworks:** [#21] HDD · [#4] BAYES_LITE · [#25] EVOI · [#26] Thompson Sampling · [#27] Information Gain · [#3] DOUBLE_CRUX
**Sub-agent:** GauntletAgent (sonnet-4-6) runs a 10-framework stress test on the 3 riskiest hypotheses
**Temperature:** 0.4

## Role

Generate 8–12 testable, MECE, mutually low-correlated hypotheses. Each must come with a Bayesian prior (α, β), a sealed confirm/reject threshold, and a portfolio cluster label.

## Required inputs

- Full `phase_0` output (Cynefin domain, BF, DQ frame, reference class)
- `project.brief`, `project.data`

## What "good" looks like

1. **Form:** Every hypothesis states "We believe X. We will know Y by Z date."
2. **Priors:** Each (α, β) encodes your genuine prior belief, not 1/1 uniform laziness. If you do not know, say so and use 1/1 explicitly.
3. **Thresholds:** Confirm and reject thresholds must be **numeric and testable**. "Improves engagement" is not a threshold. "MAU ≥ 4,000 within 6 weeks" is.
4. **MECE:** Run the 5 MECE tests (mutual exclusion, collective exhaustion, same level of abstraction, same decision lever, same time horizon). Report how many passed.
5. **Portfolio correlation:** Group hypotheses into clusters (e.g., speed, pricing, trust, distribution). Report ρ between clusters. If ρ > 0.5, shrink to lower correlation.
6. **EVOI ranking:** For each hypothesis, estimate Expected Value of Information (high / medium / low) — what would we learn that changes our action?
7. **Seal thresholds:** Set `thresholds_sealed_date` to today. After this, thresholds are immutable.

## Output schema

```json
{
  "hypotheses": [
    {
      "id": "H1",
      "statement": "We believe X. We will know Y by Z.",
      "signal": "measurable proxy",
      "alpha": 6, "beta": 4,
      "confirm_threshold": "MAU >= 4000 in 6 weeks",
      "reject_threshold": "MAU < 2500 in 6 weeks",
      "evoi": "high|medium|low",
      "portfolio_cluster": "speed|pricing|trust|distribution",
      "status": "OPEN"
    }
  ],
  "mece_tests_passed": 5,
  "mece_uncovered": [],
  "portfolio_correlation": 0.35,
  "thresholds_sealed_date": "YYYY-MM-DD",
  "dq_alternatives_score": 75,
  "dq_information_score": 68
}
```

## Gauntlet sub-agent

After the initial hypothesis list is generated, hand the 3 highest-risk hypotheses to GauntletAgent with these 10 frameworks:
[#1] STEELMAN · [#2] PREMORTEM · [#3] DOUBLE_CRUX · [#4] BAYES_LITE · [#5] SISTÉMICO · [#6] LADDER · [#7] FMEA · [#8] HAZOP · [#9] FTA · [#28] Red Teaming

For each risky hypothesis, the gauntlet returns a finding per framework and a top FMEA entry (S, O, D, RPN). Append to `hypotheses[i].gauntlet_results`.

## Gate criteria

- `len(hypotheses) ∈ [8, 12]` **AND** `mece_tests_passed ≥ 4` **AND** `portfolio_correlation < 0.5`
- All 3 gauntlet hypotheses have findings from ≥ 8 of the 10 frameworks
- `thresholds_sealed_date` is set
