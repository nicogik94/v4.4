# PROJECT BLUEPRINT — v4.0
## 30-Framework Workflow Template

**Duplicate this document for each new project. Fill in every yellow field.**
**Reference:** Universal-Project-Workflow-v4-FINAL.md for full framework descriptions and convergence formulas.

---

## PROJECT CARD

| Field | Value |
|---|---|
| Project | [fill] |
| Client | [fill] |
| Dates | [Start] – [End] |
| Team | [Name (Role), Name (Role), ...] |
| OODA Tempo | [Daily / Weekly / Sprint] |
| Maturity Level | [1: Ad Hoc / 2: Defined / 3: Quantitative / 4: Managed / 5: Optimizing] |
| Spiral Depth | [Spiral 1: lightweight / Spiral 2: deep-dive] |
| Meta-Learner prediction | [Expected duration: __ wks. P(success): __%. Bottleneck: Phase __] |

---

## PHASE 0 — Classify

### 0.1 Cynefin Classification [#16]

| Domain | Fits? | Evidence |
|---|---|---|
| Clear | [Y/N] | [fill] |
| Complicated | [Y/N] | [fill] |
| Complex | [Y/N] | [fill] |
| Chaotic | [Y/N] | [fill] |
| Mixed/Confusion | [Y/N] | [fill] |

**Classification:** [fill]
**Justification:** [fill]

### 0.1b Bayes Factor Gate

| | Value |
|---|---|
| Leading hypothesis (H₁) | [fill] |
| Nearest competitor (H₂) | [fill] |
| Computed BF | [fill] |
| Action | [Proceed (BF>10) / Gather more (1/3<BF<10) / Reclassify (BF<1/3)] |

### 0.2 Requisite Variety [#30]

| Field | List |
|---|---|
| Environmental variety | [fill: task types, inputs, edge cases] |
| System variety | [fill: capabilities, tools, strategies] |
| Gaps | [fill: where environment > system] |
| Decision | [Amplify (add agents) / Attenuate (constrain inputs)] |

### 0.3 OODA Design [#17]

| OODA Phase | Source / Method |
|---|---|
| Observe | [fill: data sources, frequency] |
| Orient | [fill: which frameworks, synthesis] |
| Decide | [fill: gate reviews, thresholds] |
| Act | [fill: deployments, changes] |
| Loop frequency | [fill] |

### 0.4 Reference-Class Anchor

| Field | Value |
|---|---|
| Base rate from case database | [fill: % success for this project type] |
| Number of comparable past projects | [fill] |
| Brier score on past classifications | [fill] |
| Adjustment from base rate | [fill: reason] |

### 0.5 DQ Score — Frame

| Criterion | Score |
|---|---|
| Right problem being addressed? | __/25 |
| Right stakeholders involved? | __/25 |
| Scope correctly bounded? | __/25 |
| Alternatives properly framed? | __/25 |
| **TOTAL** | **__/100** |

**☐ EXIT GATE: BF > 10 AND DQ Frame ≥ 60% AND Variety gaps documented**

---

## PHASE 1 — Decompose

### 1.1 Ambiguity Check

| Field | Value |
|---|---|
| Brief classification | [Clear enough / Ambiguous → needs framing] |
| If RPD: pattern matched to | [fill: known prototype] |
| If RPD: expectancy violations | [fill: what doesn't fit] |
| If Sensemaking: anchor cues | [fill: 3–4 cues] |
| If Sensemaking: frame activated | [fill: project type / domain pattern] |
| Reframes needed? | [Y/N: describe] |

### 1.2 Hypothesis Table

| # | Hypothesis | Signal | Prior α/β | CONFIRM if | REJECT if | EXTEND if | Status |
|---|---|---|---|---|---|---|---|
| H1 | [fill] | [fill] | Beta( , ) | [fill] | [fill] | [fill] | OPEN |
| H2 | [fill] | [fill] | Beta( , ) | [fill] | [fill] | [fill] | OPEN |
| H3 | [fill] | [fill] | Beta( , ) | [fill] | [fill] | [fill] | OPEN |
| H4 | [fill] | [fill] | Beta( , ) | [fill] | [fill] | [fill] | OPEN |
| H5 | [fill] | [fill] | Beta( , ) | [fill] | [fill] | [fill] | OPEN |
| ... | [add rows as needed] | | Beta( , ) | | | | |

### 1.3 MECE Verification

| Test | Pass? | Notes |
|---|---|---|
| Opposite-words | [Y/N] | [fill] |
| Process-timeline | [Y/N] | [fill] |
| Mathematical-relationship | [Y/N] | [fill] |
| Framework | [Y/N] | [fill] |
| Negative-space | [Y/N] | [fill] |

**Tests passed:** __/5 (need ≥ 2)
**Uncovered scenarios:** [None / Describe]

### 1.4 Hypothesis Portfolio

| Hypothesis Pair | Correlation (ρ) | Diversified? |
|---|---|---|
| H1 × H2 | [fill] | [Y/N] |
| H1 × H3 | [fill] | [Y/N] |
| H2 × H3 | [fill] | [Y/N] |
| ... | [fill] | [Y/N] |

**Portfolio correlation:** [fill] (target < 0.5)

### 1.5 Gauntlet Record (per high-priority hypothesis)

**Hypothesis: H__**

| Framework | Question | ✓/✗ | Finding | Action? |
|---|---|---|---|---|
| STEELMAN [#1] | Best case FOR? | [ ] | [fill] | [Y/N] |
| PREMORTEM [#2] | If failed, why? | [ ] | [fill] | [Y/N] |
| DOUBLE_CRUX [#3] | Pivotal belief? | [ ] | [fill] | [Y/N] |
| BAYES_LITE [#4] | Prior written? | [ ] | [fill] | [Y/N] |
| SISTÉMICO [#5] | What breaks? | [ ] | [fill] | [Y/N] |
| LADDER [#6] | Chain traced? | [ ] | [fill] | [Y/N] |
| FMEA [#7] | Modes + RPN? | [ ] | [fill] | [Y/N] |
| HAZOP [#8] | Guide words? | [ ] | [fill] | [Y/N] |
| FTA [#9] | Cut sets? | [ ] | [fill] | [Y/N] |
| PROSPECT [#15] | Both frames? | [ ] | [fill] | [Y/N] |

### 1.6 EVOI Assessment

| Hypothesis | EVOI | Cost to Gather | Decision |
|---|---|---|---|
| H__ | [fill] | [fill] | [Gather / Act now] |
| H__ | [fill] | [fill] | [Gather / Act now] |

### 1.7 Threshold Seal

**Thresholds sealed on:** [Date]
**Shared with:** [Names]
**☐ Tab locked (Protect Sheet)?**

### 1.8 DQ Score — Alternatives + Information

| Criterion | Score |
|---|---|
| Creative, diverse alternatives generated? | __/50 |
| Right information gathered? | __/50 |
| **TOTAL** | **__/100** |

**☐ EXIT GATE: MECE verified AND portfolio ρ < 0.5 AND priors written AND thresholds sealed AND DQ ≥ 60%**

---

## PHASE 2 — Audit

### 2.1 FMEA Table [#7]

| Component | Function | Failure Mode | Effect | S | O | D | RPN | Action |
|---|---|---|---|---|---|---|---|---|
| [fill] | [fill] | [fill] | [fill] | | | | | [fill] |
| [fill] | [fill] | [fill] | [fill] | | | | | [fill] |
| [fill] | [fill] | [fill] | [fill] | | | | | [fill] |

### 2.2 HAZOP Table [#8]

| Node | Intent | Guide Word | Deviation | Consequence | Safeguard |
|---|---|---|---|---|---|
| [fill] | [fill] | NO | [fill] | [fill] | [fill] |
| [fill] | [fill] | MORE | [fill] | [fill] | [fill] |
| [fill] | [fill] | LESS | [fill] | [fill] | [fill] |
| [fill] | [fill] | REVERSE | [fill] | [fill] | [fill] |
| [fill] | [fill] | OTHER THAN | [fill] | [fill] | [fill] |
| [fill] | [fill] | AS WELL AS | [fill] | [fill] | [fill] |
| [fill] | [fill] | EARLY / LATE | [fill] | [fill] | [fill] |

### 2.3 STPA Table [#11]

| Control Action | UCA Type | Hazard | Safety Constraint |
|---|---|---|---|
| [fill] | NOT PROVIDED | [fill] | [fill] |
| [fill] | PROVIDED UNSAFELY | [fill] | [fill] |
| [fill] | WRONG TIMING | [fill] | [fill] |
| [fill] | WRONG DURATION | [fill] | [fill] |

### 2.4 Mental Model Simulations [#14]

| Action | Predicted Consequence | Failure Condition | Detection Signal |
|---|---|---|---|
| [fill] | [fill] | [fill] | [fill] |
| [fill] | [fill] | [fill] | [fill] |

### 2.5 Entropy Convergence Log

| After Framework | H_norm | ΔH | D_KL vs prev | Action |
|---|---|---|---|---|
| FMEA | [calc] | — | — | Continue |
| HAZOP | [calc] | [calc] | [calc] | [Continue/Stop] |
| STPA | [calc] | [calc] | [calc] | [Continue/Stop] |
| Mental Models | [calc] | [calc] | [calc] | [Continue/Stop] |
| [other] | [calc] | [calc] | [calc] | [Continue/Stop] |

### 2.6 EVSI Framework Ranking

| Framework | Est. EVSI | Cost | ENBS | Apply? |
|---|---|---|---|---|
| [name] | [fill] | [fill] | [calc] | [Y/N] |
| [name] | [fill] | [fill] | [calc] | [Y/N] |

### 2.7 Calibration Log

| Estimate Made | Confidence | Actual Outcome | Error |
|---|---|---|---|
| [claim] | [%] | [result] | [calc] |
| [claim] | [%] | [result] | [calc] |

### 2.8 DQ Score — Values + Reasoning

| Criterion | Score |
|---|---|
| Clear values and trade-offs? | __/50 |
| Sound reasoning applied? | __/50 |
| **TOTAL** | **__/100** |

**☐ EXIT GATE: H_norm < 0.15 OR D_KL < 0.01 OR no framework with ENBS > 0 AND DQ ≥ 60%**

---

## PHASE 3 — Model

### 3.1 Scenario Table

| Scenario | Hypothesis Rate | Mean Outcome | 90% CI | vs Baseline |
|---|---|---|---|---|
| Pessimistic | [fill] | [fill] | [fill] | [fill] |
| Moderate | [fill] | [fill] | [fill] | [fill] |
| Optimistic | [fill] | [fill] | [fill] | [fill] |
| Seasonal-adjusted | [fill] | [fill] | [fill] | [fill] |

### 3.2 Command Center Tabs

- [ ] Pages / Components
- [ ] Hypotheses
- [ ] Weekly Data
- [ ] Bayesian Reference
- [ ] Scenario Model
- [ ] Conflicts / Cannibalization
- [ ] AI Visibility
- [ ] Content / Action Calendar
- [ ] Deploy Checklist
- [ ] Change Log
- [ ] Gate Review Log
- [ ] Team Dashboard

### 3.3 SLOs

| Metric | Target | Alert Threshold | Tool |
|---|---|---|---|
| [fill] | [fill] | [fill] | [fill] |
| [fill] | [fill] | [fill] | [fill] |

### 3.4 Sequential Monitoring Log

| Hypothesis | Look # | Info Fraction | Current z | z-boundary | Cond. Power | Action |
|---|---|---|---|---|---|---|
| H__ | 1 | 0.20 | [calc] | 4.56 | [calc] | [Continue/Confirm/Futility] |
| H__ | 2 | 0.40 | [calc] | 3.23 | [calc] | [Continue/Confirm/Futility] |
| H__ | 3 | 0.60 | [calc] | 2.63 | [calc] | [Continue/Confirm/Futility] |

### 3.5 Real-Options Status

| Hypothesis | Current Stage | Trigger to Advance | Trigger to Abandon | Status |
|---|---|---|---|---|
| H__ | [Seed/Expand/Scale] | [fill] | [fill] | [Active/Deferred/Abandoned] |
| H__ | [Seed/Expand/Scale] | [fill] | [fill] | [Active/Deferred/Abandoned] |

**☐ EXIT GATE: All hypotheses have crossed boundary OR stopped for futility OR staged as real options**

---

## PHASE 4 — Execute

### 4.1 Deploy Protocol

| Field | Value |
|---|---|
| Canary % | [fill] |
| Monitoring window | [fill] |
| Promotion criteria | [fill] |
| Circuit Breaker threshold | [fill] |
| Fallback behavior | [fill] |
| Rollback trigger | [fill] |

### 4.2 Weekly Update Log

| Week | Data In | Priors Updated | Thompson Priority | EVOI Decision | Info Gain Action | Gate Check |
|---|---|---|---|---|---|---|
| Wk__ | [fill] | H__: α+_, β+_ | Focus: H__ | [Gather/Act] | [Action] | [Y/N] |
| Wk__ | [fill] | H__: α+_, β+_ | Focus: H__ | [Gather/Act] | [Action] | [Y/N] |
| Wk__ | [fill] | H__: α+_, β+_ | Focus: H__ | [Gather/Act] | [Action] | [Y/N] |

### 4.3 Framework Insight Tracker

| Framework | Cycle 1 | Cycle 2 | Cycle 3 | Avg | Action |
|---|---|---|---|---|---|
| [name] | [0–10] | [0–10] | [0–10] | [calc] | [Keep/Shift/Drop] |
| [name] | [0–10] | [0–10] | [0–10] | [calc] | [Keep/Shift/Drop] |

### 4.4 Graduation / Dropping

| Alternative | P(best) | Status |
|---|---|---|
| [name] | [calc] | [Graduate >0.95 / Continue / Drop <0.05] |
| [name] | [calc] | [Graduate >0.95 / Continue / Drop <0.05] |

### 4.5 Reflexion Log

| Cycle | Step | Self-Critique | Diagnosis | Correction | Pre-flect (next step) |
|---|---|---|---|---|---|
| [n] | [step] | [what went wrong] | [root cause] | [fix applied] | [anticipated failure] |
| [n] | [step] | [what went wrong] | [root cause] | [fix applied] | [anticipated failure] |

### 4.6 Gate Review Record

| Date | Hypothesis | Reviewer 1 | Reviewer 2 | Reviewer 3 | Decision | Crux Found | Steelman |
|---|---|---|---|---|---|---|---|
| [date] | H__ | [C/R/E] | [C/R/E] | [C/R/E] | [fill] | [fill] | [fill] |
| [date] | H__ | [C/R/E] | [C/R/E] | [C/R/E] | [fill] | [fill] | [fill] |

### 4.7 Resilience Test Log

| Date | Type | Experiment | Steady State Held? | Fix Applied |
|---|---|---|---|---|
| [date] | Chaos | [fill] | [Y/N] | [fill] |
| [date] | Ablation | [component removed] | [Δ = __%] | [REDUNDANT/CRITICAL/CONTRIBUTING] |

**☐ EXIT GATE: All hypotheses graduated/dropped AND Reflexion errors decreasing AND no SLO violations 2+ cycles**

---

## PHASE 5 — Handoff

### 5.1 Causal Verification [#24]

| Field | Value |
|---|---|
| Causal DAG drawn? | [Y/N] |
| Confounders identified | [fill: list] |
| Counterfactual | [Would outcome happen without intervention? Y/N/Partially] |
| Attribution confidence | [High / Medium / Low] |
| Reasoning | [fill] |

### 5.2 Swiss Cheese Audit [#10]

| Defense Layer | Hole | Active/Latent | Root Cause | Fix Applied |
|---|---|---|---|---|
| [fill] | [fill] | [A / L] | [fill] | [fill] |
| [fill] | [fill] | [A / L] | [fill] | [fill] |

### 5.3 HRO Debrief [#29]

| Principle | Finding |
|---|---|
| Preoccupation with failure | Near-misses caught: __ / Missed: __ |
| Reluctance to simplify | [fill] |
| Sensitivity to operations | [fill] |
| Commitment to resilience | [fill] |
| Deference to expertise | [fill] |

### 5.4 Red Team [#28]

| Field | Value |
|---|---|
| Attack surface | [fill: inputs, APIs, inter-agent, memory] |
| ASR: Content integrity | __% |
| ASR: Prompt injection | __% |
| ASR: Data leakage | __% |
| Items hardened (ASR > 5%) | [fill] |

### 5.5 Commitment-to-Action Score

| Criterion | Score |
|---|---|
| Stakeholders aligned? | __/25 |
| Resources committed? | __/25 |
| Organization ready? | __/25 |
| Behavioral change plan? | __/25 |
| **TOTAL** | **__/100** |

### 5.6 Agent Card

| Field | Value |
|---|---|
| Name | [fill] |
| Capabilities | [fill] |
| Limitations | [fill] |
| Evaluation results | [fill: metrics from Phase 4] |
| Operational boundaries | [fill] |
| Known failure modes | [fill: from FMEA/FTA] |
| Monitoring approach | [fill: SLOs, alerts] |
| Predicted success probability | [fill] |

### 5.7 Handoff Checklist

- [ ] Operating manual (OODA loop instructions)
- [ ] Decision playbooks sealed and transferred
- [ ] Escalation paths documented
- [ ] Framework reference cards for team
- [ ] Training session completed (recorded)
- [ ] Command Center access transferred
- [ ] Change Log current
- [ ] Agent Cards delivered

### 5.8 Meta-Learner Input

| Field | Value |
|---|---|
| Project type | [fill: from Phase 0] |
| Industry | [fill] |
| Complexity (1–5) | [fill] |
| Data richness (1–5) | [fill] |
| Team size | [fill] |
| Duration (weeks) | [fill] |
| Frameworks used | [fill: with insight scores] |
| Phase durations (hours) | P0:__ P1:__ P2:__ P3:__ P4:__ P5:__ |
| Predicted outcome | [fill: from Phase 0] |
| Actual outcome | [fill] |
| Brier score | [fill] |
| Calibration notes | [fill: over/under confident where?] |
| Key learning | [fill: one sentence] |

**☐ EXIT GATE: Commitment ≥ 70% AND Agent Cards delivered AND Meta-Learner fed AND all DQ scored**

---

## DQ SPIDER CHART — Final Scores

| Dimension | Phase | Score |
|---|---|---|
| 1. Appropriate Frame | Phase 0 | __% |
| 2. Creative Alternatives | Phase 1 | __% |
| 3. Relevant Information | Phase 2 | __% |
| 4. Clear Values | Phase 3 | __% |
| 5. Sound Reasoning | Phase 4 | __% |
| 6. Commitment to Action | Phase 5 | __% |
| **Overall DQ (geometric mean)** | | **__% ** |

---

## RE-ENTRY LOG

| Date | Trigger | From Phase | To Phase | What Changed | Outcome |
|---|---|---|---|---|---|
| [date] | [trigger] | [N] | [N] | [fill] | [fill] |
| [date] | [trigger] | [N] | [N] | [fill] | [fill] |
