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
8. **Variable coverage:** Use the existing fields to make the causal driver or decision variable explicit. Cover relevant variables such as demand/user segment, channel/acquisition, activation/onboarding, retention/repeat usage, monetization/pricing, operational capacity, data quality/measurement, legal/compliance/claim-safety, competitive dynamics, implementation complexity, owner/decision authority, time horizon/cadence, and evidence required to validate. Do not force irrelevant categories.
9. **Validation clarity:** State the assumption, validation evidence, owner or approval dependency, timing/cadence, and what would change the recommendation inside the existing `text`, `justification`, `signal`, `confirm`, `reject`, or `portfolio_cluster` fields where relevant. Do not add new output keys.

## Output schema

```json
[
  {
    "id": "H1",
    "text": "We believe X. We will know Y by Z.",
    "justification": "Brief explanation grounded in the brief, classify output, or available data.",
    "signal": "measurable proxy",
    "alpha": 6,
    "beta": 4,
    "confirm": "MAU >= 4000 in 6 weeks",
    "reject": "MAU < 2500 in 6 weeks",
    "evoi": "high|medium|low",
    "portfolio_cluster": "speed|pricing|trust|distribution",
    "status": "OPEN"
  }
]
```

Do not add keys beyond the schema above.

## Gauntlet sub-agent

After the initial hypothesis list is generated, hand the 3 highest-risk hypotheses to GauntletAgent with these 10 frameworks:
[#1] STEELMAN · [#2] PREMORTEM · [#3] DOUBLE_CRUX · [#4] BAYES_LITE · [#5] SISTÉMICO · [#6] LADDER · [#7] FMEA · [#8] HAZOP · [#9] FTA · [#28] Red Teaming

For each risky hypothesis, the gauntlet returns a finding per framework and a top FMEA entry (S, O, D, RPN). Append to `hypotheses[i].gauntlet_results`.

## Gate criteria

- `len(hypotheses) ∈ [8, 12]` **AND** `mece_tests_passed ≥ 4` **AND** `portfolio_correlation < 0.5`
- All 3 gauntlet hypotheses have findings from ≥ 8 of the 10 frameworks
- `thresholds_sealed_date` is set
