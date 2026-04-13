# DECISION PLAYBOOK — v4.0
## If/Then Rules for Every Gate, Trigger, and Decision Point

**Author:** Nicolás Grinberg · RegexSEO
**Purpose:** This document answers one question: "Given [situation], what do I do?" Every decision point in the v4 workflow has a pre-committed rule. Do not override these rules after data arrives.

---

## EXIT GATES — Phase-by-Phase

### Phase 0 Exit Gate

| Condition | Met? | If NO |
|---|---|---|
| BF > 10 on domain classification | [Y/N] | Gather more evidence → recompute BF → if BF < 1/3 after 3 attempts, reclassify using Sensemaking [#13] |
| DQ Frame ≥ 60% | [Y/N] | Score below 60% = wrong problem, wrong scope, or wrong stakeholders. Do not proceed. Re-scope with stakeholders. |
| DQ Frame ≥ 80% (if high-stakes) | [Y/N] | For high-stakes projects, 60–79% means proceed with caution flag. Below 60% = stop. |
| Requisite Variety gaps documented | [Y/N] | Undocumented gaps = unmanaged risk. List all gaps, decide amplify/attenuate for each, then proceed. |

**If all conditions met → Phase 1.**
**If any condition fails → do not proceed. Fix the failing condition first.**

### Phase 1 Exit Gate

| Condition | Met? | If NO |
|---|---|---|
| MECE verified (≥ 2 tests pass) | [Y/N] | Decomposition has gaps or overlaps. Run additional MECE tests. If still failing, the decomposition structure is wrong — rebuild from scratch using a different frame. |
| Portfolio correlation < 0.5 | [Y/N] | Hypotheses are too correlated (if one fails, they all fail). Add negatively correlated hypotheses. If impossible, document the concentration risk. |
| All priors written (Beta α/β) | [Y/N] | Any hypothesis without a prior cannot be tested rigorously. Write the prior from available data, even if weak (Beta(1,1) = maximum ignorance). |
| Thresholds sealed | [Y/N] | Unsealed thresholds = moving goalposts. Seal before any data arrives. No exceptions. |
| DQ Alternatives+Info ≥ 60% | [Y/N] | Below 60% = not enough creative alternatives or not enough information. Generate more options or gather more data before proceeding. |

### Phase 2 Exit Gate

| Condition | Met? | If NO |
|---|---|---|
| H_norm < 0.15 | [Y/N] | Uncertainty still too high. Apply next-highest ENBS framework. If no frameworks have ENBS > 0, accept residual uncertainty and document it. |
| OR: No framework with ENBS > 0 | [Y/N] | All remaining frameworks cost more than they're worth. Stop auditing — this is the economically optimal point. |
| DQ Values+Reasoning ≥ 60% | [Y/N] | Values unclear or reasoning unsound. Re-examine trade-offs with stakeholders. |

### Phase 3 Exit Gate

| Condition | Met? | If NO |
|---|---|---|
| All hypotheses crossed a sequential boundary (CONFIRM or REJECT) | [Y/N] | Some hypotheses still in inconclusive zone. Options: (a) continue testing at next interim look, (b) check futility, (c) stage as real option with explicit advance/abandon triggers. |
| OR: Stopped for futility (cond. power < 15%) | [Y/N] | Hypothesis shows no signal. Reformulate (back to Phase 1) or abandon. Do not continue testing — it wastes resources. |
| OR: Staged as real options | [Y/N] | Hypothesis not yet resolved but has explicit Seed/Expand/Scale triggers. This is acceptable — proceed with documented uncertainty. |

### Phase 4 Exit Gate

| Condition | Met? | If NO |
|---|---|---|
| All hypotheses graduated or dropped | [Y/N] | Some still in testing. Continue cycles until P(best) > 0.95 or < 0.05 for each. |
| Reflexion errors decreasing | [Y/N] | Error rate not improving = the self-correction loop isn't working. Diagnose: wrong critique criteria? Missing feedback? Fix the Reflexion process itself (this is double-loop learning). |
| No SLO violations for 2+ cycles | [Y/N] | System still unstable. Do not hand off an unstable system. Continue monitoring and fix violations before proceeding. |

### Phase 5 Exit Gate

| Condition | Met? | If NO |
|---|---|---|
| Commitment-to-Action ≥ 70% | [Y/N] | Stakeholders not aligned. Below 50% → re-enter Phase 4 for stakeholder alignment. 50–69% → proceed with documented adoption risk. |
| Agent Cards delivered | [Y/N] | No cards = undocumented system. Write them before handoff. Non-negotiable. |
| Meta-Learner fed | [Y/N] | Missing this loses the data flywheel value. Fill in all 13 fields in the meta-learner input table. |
| All 6 DQ dimensions scored | [Y/N] | Missing scores = incomplete quality record. Score all six, compute geometric mean. |

---

## RE-ENTRY TRIGGERS — When to Loop Back

| # | Trigger | Detection | Source Phase | Re-Enter Phase | Action |
|---|---|---|---|---|---|
| R1 | Assumption violated by >2σ | Phase 4–5 data contradicts Phase 0–1 assumptions | 4 or 5 | 1 | Regenerate hypotheses with new evidence. Previous hypotheses become priors for new ones. |
| R2 | Domain reclassification needed | New information changes the Cynefin classification | Any | 0 | Full reclassification. Reset BF gate. All downstream phases invalidated. |
| R3 | Scope change (new stakeholder) | Stakeholder changes project scope or requirements | Any | 0 | Re-score DQ Frame. If score changes by >20 points, full reclassification. |
| R4 | Portfolio too correlated | All hypotheses moving in same direction; single failure mode | 3 | 1 | Add negatively correlated hypotheses. Diversify the portfolio. |
| R5 | All hypotheses hit futility | Every active hypothesis has conditional power < 15% | 3 | 1 | The decomposition was wrong. Rebuild from Phase 1 with a different frame. |
| R6 | >50% hypotheses at futility | Majority failing, but some still viable | 3 | 2 | Re-audit with different frameworks. The evidence base may be wrong, not the hypotheses. |
| R7 | SLO violated 3+ consecutive cycles | System degrading during execution | 4 | 3 | Re-model with updated parameters. The probability model is miscalibrated. |
| R8 | Commitment < 50% | Stakeholders not adopting | 5 | 4 | Re-execute with stakeholder alignment focus. Consider Prospect Theory reframing. |

**Re-entry protocol:** When a trigger fires:
1. Log it in the Re-Entry Log (date, trigger, from/to phase, what changed)
2. Carry forward all valid findings — don't restart from zero
3. Update the Decision Dossier with re-entry reason
4. Resume at the target phase with updated priors (not fresh priors)

---

## WEEKLY CYCLE — Step-by-Step Decision Rules

### Step 1: Pull Data (15 min)
- **If data source is unavailable:** Use last week's data. Flag as stale in Weekly Data tab. Do not skip the cycle.
- **If data shows anomaly (>3σ from expected):** Flag for investigation before updating priors. Do not auto-update with anomalous data.

### Step 2: Update Priors (10 min)
- **Formula:** `new_α = old_α + successes; new_β = old_β + failures`
- **If no new data for a hypothesis:** Priors unchanged. Note "no update" in log.
- **If data contradicts prior by >2σ:** This is a double-loop trigger. Flag for re-entry consideration.

### Step 3: Thompson Sampling (5 min)
- **Formula:** `=BETA.INV(RAND(), current_α, current_β)` for each hypothesis
- **Decision:** Highest sampled value = this cycle's focus hypothesis
- **If tied (within 5%):** Choose the one with higher uncertainty (wider posterior) — explore.
- **Override rule:** If a hypothesis has been focus for 3+ consecutive cycles with no movement, force-switch to the next highest.

### Step 4: EVOI Check (5 min)
- **Question:** "Would one more cycle of data change my decision?"
- **If hypothesis clearly above CONFIRM threshold:** No more data needed → graduate
- **If hypothesis clearly below REJECT threshold:** No more data needed → drop
- **If in INCONCLUSIVE zone:** Keep gathering. Estimate cycles remaining to resolution.

### Step 5: Information Gain (5 min)
- **Question:** "Among all pending actions, which reduces the most uncertainty?"
- **Decision:** Execute the highest-information-gain action first
- **If two actions have equal gain:** Choose the cheaper one
- **If no action has meaningful gain:** Skip — all remaining uncertainty is irreducible with available tools

### Step 6: Gate Review Check (2 min)
- **If 4+ weeks since last gate review:** Schedule one within 1 week
- **If <4 weeks but a hypothesis just crossed CONFIRM/REJECT:** Schedule emergency gate review
- **If all hypotheses are in INCONCLUSIVE zone with no movement for 3+ cycles:** Schedule gate review to discuss whether to EXTEND or restructure

---

## GATE REVIEW — 8-Step Protocol

### Step 1: Data Packet (48h before)
- Include: current posteriors, hypothesis status table, weekly trend charts, any anomalies flagged
- **If a reviewer hasn't read the packet by review time:** Postpone. Uninformed votes corrupt the process.

### Step 2: Independent Assessment
- Each reviewer writes their verdict BEFORE any discussion
- **Format:** For each hypothesis: CONFIRM / REJECT / EXTEND with one-sentence justification
- **No talking until all verdicts are written**

### Step 3: Sealed Vote
- All verdicts collected simultaneously (shared doc, screen share)
- **If any reviewer has not submitted:** Do not reveal others'. Wait.

### Step 4: Reveal
- Show all votes at once
- **If unanimous CONFIRM:** Graduate immediately. No discussion needed.
- **If unanimous REJECT:** Drop immediately. No discussion needed.
- **If unanimous EXTEND:** Continue. Set next gate date.
- **If split:** Proceed to Step 5.

### Step 5: DOUBLE_CRUX [#3]
- For each split vote, ask: "What is the ONE belief that, if proven wrong, would change the minority's vote?"
- **If crux is testable:** Design a test. EXTEND until test completes.
- **If crux is untestable:** Move to Step 6 (STEELMAN).

### Step 6: STEELMAN [#1]
- Build the strongest possible case for the minority position
- **If STEELMAN reveals conditions where minority is right AND those conditions plausibly apply:** EXTEND and investigate
- **If STEELMAN fails (no plausible conditions where minority is right):** Majority rules, document dissent

### Step 7: Final Decision
- Majority rules for each hypothesis
- **Documented dissent required:** The minority view is logged with its reasoning. This is the audit trail.

### Step 8: Update
- Change hypothesis status in Command Center
- If any hypothesis pivoted: update all downstream playbooks
- Set next gate date

---

## CONVERGENCE DECISIONS — When Is It "Done?"

### Entropy-Based (Phase 2)

| H_norm Value | Meaning | Action |
|---|---|---|
| > 0.50 | High uncertainty (>50% remaining) | Continue applying frameworks. Prioritize by ENBS. |
| 0.15 – 0.50 | Moderate uncertainty | Continue if any framework has ENBS > 0. Otherwise proceed with documented uncertainty. |
| < 0.15 | Low uncertainty (85%+ resolved) | **Stop.** Phase is complete. More analysis has diminishing returns. |

| D_KL Value | Meaning | Action |
|---|---|---|
| > 0.10 | Frameworks still significantly changing beliefs | Continue — each framework is earning its keep |
| 0.01 – 0.10 | Frameworks making small adjustments | Continue only if ENBS > 0 for remaining frameworks |
| < 0.01 | Frameworks no longer changing beliefs | **Stop.** New frameworks are redundant. |

### Sequential Monitoring (Phase 3)

| Situation | Action |
|---|---|
| Test statistic crosses z-boundary | **CONFIRM** the hypothesis. Stop testing. Graduate. |
| Test statistic stays inside boundary | **Continue** to next interim look. |
| Conditional power < 15% | **Futility stop.** Reformulate or abandon. |
| All 5 looks completed, inside boundary | **Inconclusive.** Stage as real option or accept null. |

### Graduation / Dropping (Phase 4)

| P(it is best) | Action |
|---|---|
| > 0.95 | **Graduate.** Accept as winner. Allocate resources. |
| 0.50 – 0.95 | **Continue.** Close to graduating — one more cycle may resolve. |
| 0.05 – 0.50 | **Continue.** Still viable but not leading. |
| < 0.05 | **Drop.** Stop investing. Redirect resources. |

---

## ADAPTIVE FRAMEWORK SELECTION

### When to Keep a Framework
- Insight score ≥ 5/10 for this project type
- Changed a decision at least once
- Meta-learner recommends it for this project's feature vector

### When to Drop a Framework
- Zero incremental insight for 2 consecutive cycles
- ENBS < 0 (costs more than it's worth)
- Meta-learner has never seen it produce value for this project type

### When to Shift Resources
- Framework A shows 3× insight of Framework B → shift 75% to A
- If only one framework is producing insight → 90% allocation to it, 10% exploration of new ones

---

## REFLEXION PROTOCOL

### After Each Execution Step

1. **Critique** (2 min): What gap between expected and actual outcome?
2. **Diagnose** (3 min): Root cause — wrong assumption? Missing data? Wrong framework?
3. **Prescribe** (2 min): Specific, actionable correction for the next step
4. **Pre-flect** (3 min): Before next step, list top 3 anticipated failure modes and mitigations

### Escalation Rules
- If same error repeats 3× despite correction → this is a double-loop trigger. Question the governing assumption, not just the execution.
- If Reflexion itself isn't producing corrections → question whether the critique criteria are right (triple-loop).

---

## MATURITY PROGRESSION

### How to Level Up

| From → To | Key Actions |
|---|---|
| 1 → 2 (Ad Hoc → Defined) | Use templates for every project. Define roles. Document framework selection criteria. Target: >80% template usage. |
| 2 → 3 (Defined → Quantitative) | Implement Bayesian gates, EVPI stopping, DQ scoring. Start tracking Brier scores. Target: all phase transitions pass quality gates. |
| 3 → 4 (Managed → Managed) | Activate all 3 learning loops. Build meta-learning database. Track calibration across projects. Target: prediction accuracy improving over 12 months. |
| 4 → 5 (Managed → Optimizing) | Full data flywheel operational. Auto framework recommendations. A/B test workflow variations. Target: accuracy >85%, Brier <0.15. |

---

## ESCALATION PATHS

| Situation | Escalate To | With |
|---|---|---|
| Can't resolve Cynefin classification | Senior domain expert | Brief + Cynefin table + failed BF attempts |
| All hypotheses at futility | Project sponsor | Futility evidence + reformulation options + resource request |
| SLO violations for 3+ cycles | Technical lead | SLO trend data + root cause analysis + proposed fix |
| Gate review deadlocked (no DOUBLE_CRUX found) | External reviewer (fresh perspective) | Full data packet + both positions + failed crux search |
| Commitment < 50% at handoff | Executive sponsor | Adoption risk analysis + behavioral change plan + Prospect Theory reframing |
| Re-entry trigger fires for 3rd time | Project sponsor | Pattern analysis: why does this keep happening? Is the project viable? |

---

*"Every decision in this playbook was written before data arrived. That's what makes it a playbook, not a rationalization."*
