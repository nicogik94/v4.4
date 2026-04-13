# IMPLEMENTATION GUIDE — v4.0
## Tools, Prompts & Step-by-Step Instructions

**Author:** Nicolás Grinberg · RegexSEO
**Purpose:** This tells you exactly HOW to execute each step: what tool to open, what prompt to paste, what formula to write, what output to expect.

---

## TOOL STACK

| Category | Tool | Purpose | Cost |
|---|---|---|---|
| LLM (Primary) | Claude API (Opus 4.6 / Sonnet 4.6) | Framework analysis, hypothesis generation, all prompts below | $5/$25 MTok (Opus), $3/$15 (Sonnet) |
| LLM (Fallback) | OpenAI GPT-4.1 (legacy) / GPT-5.4 | Alternative provider for redundancy | $2/$8 (4.1), $2.50/$15 (5.4) |
| Spreadsheet | Google Sheets / Excel | Command Center, BETA.DIST, Monte Carlo, FMEA, entropy, convergence | Free |
| Data Sources | Industry-specific (varies by engagement) | Client data, market research, competitive intelligence | Varies |
| Analytics | GA4 + Looker Studio (if web/digital) | Traffic, engagement, conversion + dashboards | Free |
| AI Research | Perplexity / Gemini / Claude | Secondary research, fact-checking, literature review | Free–$20 |
| Project Mgmt | ClickUp / Notion / Sheets | Tasks, sprints, team coordination | Free tier |
| Docs | Google Docs / Word | Strategy docs, playbooks, deliverables | Free |
| CRM | HubSpot Free | Pipeline tracking, contact management | Free |

---

## COMPLETE FORMULA REFERENCE

### Bayesian Core
```
Prior probability:         =BETA.DIST(threshold, alpha, beta, TRUE)
Random sample:             =BETA.INV(RAND(), alpha, beta)
Update rule:               new_α = old_α + successes;  new_β = old_β + failures
P(success):                =alpha/(alpha+beta)
Thompson Sampling:         =BETA.INV(RAND(), current_α, current_β)  → sort desc
Bayes Factor:              BF = P(data|H₁) / P(data|H₂)
90% CI lower:              =PERCENTILE(simulation_results, 0.05)
90% CI upper:              =PERCENTILE(simulation_results, 0.95)
Scenario EV:               =SUMPRODUCT(estimates, probabilities)
```

### Information-Theoretic
```
Shannon entropy:           H(P) = −Σ p(x) × LOG2(p(x))
  In Sheets:               =-SUMPRODUCT(probs, LOG(probs,2))
Normalized entropy:        H_norm = H(P) / LOG2(n)
KL-divergence:             D_KL = SUMPRODUCT(p_new, LOG(p_new/p_old,2))
Convergence:               Stop when H_norm < 0.15 OR D_KL < 0.01
```

### Sequential Analysis
```
O'Brien-Fleming boundaries (5-look, α=0.05):
  Look 1 (t=0.20): z = 4.56
  Look 2 (t=0.40): z = 3.23
  Look 3 (t=0.60): z = 2.63
  Look 4 (t=0.80): z = 2.28
  Look 5 (t=1.00): z = 2.04
Futility:                  Stop if conditional power < 15%
```

### Decision Economics
```
EVPI:                      E[value with perfect info] − E[current best decision]
EVSI:                      E[max_d V(d|z)] − max_d E[V(d)]
ENBS:                      EVSI − Cost(framework)
Apply only if:             ENBS > 0
EVOI decision:             =IF(EV_improvement × P_change > cost, "GATHER", "ACT")
```

### Calibration & Quality
```
Brier score:               BS = (1/N) × SUMPRODUCT((forecasts − outcomes)^2)
ECE:                       Σ (|bin_size|/n) × |accuracy − confidence|
DQ overall:                =GEOMEAN(Frame%, Alternatives%, Info%, Values%, Reasoning%, Commitment%)
Portfolio variance:         Σᵢ Σⱼ wᵢ wⱼ σᵢ σⱼ ρᵢⱼ
```

---

## PROMPTS BY PHASE

### Phase 0 Prompts

**0.1 — Cynefin Classification + Bayes Factor**

```
You are an AI systems architect. I'm starting a new project. Here is the brief:

[PASTE BRIEF]

1. CYNEFIN CLASSIFICATION: Classify this project using the Cynefin framework 
   (Clear, Complicated, Complex, Chaotic, Confusion). For each domain, explain 
   why it does or doesn't fit. If the project spans multiple domains, identify 
   which parts fall where.

2. BAYES FACTOR: For the top two candidate domains, estimate the likelihood of 
   the evidence under each. Compute BF = P(evidence|H₁)/P(evidence|H₂). Is 
   BF > 10 (proceed), between 1/3 and 10 (gather more), or < 1/3 (reclassify)?

3. OUTPUT: Table with Domain | Fits? | Evidence | BF Contribution
```

**0.2 — Requisite Variety Audit**

```
Given this project brief, audit the system's variety:

[PASTE BRIEF + CURRENT CAPABILITIES]

1. ENVIRONMENTAL VARIETY: List every distinct task type, input modality, failure 
   mode, and edge case the system must handle.
2. SYSTEM VARIETY: List the current capabilities, tools, agent types, and 
   reasoning strategies available.
3. GAPS: Where does environmental complexity exceed system capacity?
4. RECOMMENDATION: For each gap, should we amplify (add capability) or attenuate 
   (constrain inputs)?

Output as a 4-column table: Gap | Env. Requirement | Current Capability | Amplify/Attenuate
```

**0.3 — OODA Loop Design**

```
Design the OODA loop for this project:

[PASTE PROJECT TYPE + DATA SOURCES + TEAM STRUCTURE]

- OBSERVE: What data sources? How frequently?
- ORIENT: What frameworks synthesize observations? What's the analysis method?
- DECIDE: What decision mechanisms? (Gate review, threshold check, sealed vote)
- ACT: What executions follow decisions?
- LOOP FREQUENCY: How fast should each cycle run? Why?

Output as a structured table.
```

**0.4 — Reference-Class Anchoring**

```
I need a reference-class forecast for this project:

PROJECT TYPE: [fill]
INDUSTRY: [fill]
COMPLEXITY: [1-5]
KEY CHARACTERISTICS: [fill]

1. What is the base rate of success for projects like this? (Use any available 
   data on similar project types, industry benchmarks, or meta-analyses.)
2. What are the most common failure modes for this project type?
3. What adjustment from the base rate is warranted given THIS project's specifics?
4. What's the reference-class-adjusted P(success)?
```

**0.5 — DQ Frame Scoring**

```
Score the Decision Quality "Appropriate Frame" for this project:

[PASTE BRIEF + STAKEHOLDER LIST + SCOPE DEFINITION]

Score each criterion 0-25:
1. Right problem being addressed? (Are we solving the real problem, not a symptom?)
2. Right stakeholders involved? (Who should be at the table who isn't?)
3. Scope correctly bounded? (Too narrow misses context; too broad is unmanageable)
4. Alternatives properly framed? (Are we comparing the right options?)

For each: score, evidence, and what would raise the score by 5+ points.
```

---

### Phase 1 Prompts

**1.1-1.2 — Ambiguity Check + Hypothesis Extraction**

```
You are helping me decompose a project into testable hypotheses.

PROJECT BRIEF: [PASTE]
DATA AVAILABLE: [PASTE: exports, summaries, crawl findings]

Step 1: AMBIGUITY CHECK
- Does this brief match a known project pattern? (RPD check)
- If yes: what pattern? What expectancy violations don't fit?
- If no: what are the 3–4 anchor cues? What frame do they activate?

Step 2: HYPOTHESIS EXTRACTION (20–40 hypotheses)
Template: "We believe [doing X] for [audience Y] will achieve [outcome Z]. 
We will know when [measurable signal]."

For each include:
- Measurable signal with a specific number
- Suggested Beta(α,β) prior from the data
- CONFIRM threshold and REJECT threshold

Output: # | Hypothesis | Signal | Prior α/β | CONFIRM if | REJECT if
```

**1.3 — MECE Verification**

```
I have this decomposition of my project:

[PASTE HYPOTHESIS LIST OR DECOMPOSITION TREE]

Run 5 MECE tests:
1. OPPOSITE-WORDS: Can each category be defined by its opposite?
2. PROCESS-TIMELINE: Does it cover the entire timeline without gaps?
3. MATHEMATICAL: Do the parts sum to the whole?
4. FRAMEWORK: Does a known framework confirm this structure?
5. NEGATIVE-SPACE: Can you identify any scenario NOT covered?

For each test: Pass/Fail + evidence. Identify any gaps or overlaps.
```

**1.4 — Hypothesis Portfolio**

```
I have these hypotheses:

[PASTE HYPOTHESIS TABLE]

Apply portfolio theory:
1. For each pair of hypotheses, estimate the correlation (ρ). Are they 
   positively correlated (fail/succeed together) or negatively correlated 
   (if one fails, the other becomes more informative)?
2. Identify the most correlated pairs (ρ > 0.5) — these represent concentration risk.
3. Suggest 2-3 additional hypotheses that would be negatively correlated 
   with the existing set.
4. Compute the overall portfolio correlation.

Output: Pair | ρ | Risk Level + Suggested additions
```

**1.5 — 10-Framework Gauntlet (per hypothesis)**

```
Analyze hypothesis H[N] through 10 frameworks:

HYPOTHESIS: [paste]

1. STEELMAN: Strongest case FOR the alternative?
2. PREMORTEM: Assume failed in 3 months. Why?
3. DOUBLE_CRUX: ONE belief that flips the decision?
4. BAYES_LITE: Is the Beta prior reasonable? Supporting data?
5. SISTÉMICO: What breaks elsewhere if this succeeds? Fails?
6. LADDER: Inference chain? Where could selection bias enter?
7. FMEA: Top 3 failure modes with S/O/D (1-10) and RPN.
8. HAZOP: NO/MORE/LESS/REVERSE/OTHER THAN/AS WELL AS/EARLY/LATE on key data flow.
9. FTA: Minimal cut set for catastrophic failure?
10. PROSPECT THEORY: Frame as gain vs loss. Does framing change the decision?

Output: Framework | Finding | Action Required (Y/N) | Specific Action
```

---

### Phase 2 Prompts

**2.1 — FMEA Audit**

```
I have a system with these components:

[PASTE: component list, data flows, architecture description, crawl data]

Conduct FMEA:
- For each component's function, list failure modes
- Rate: Severity (1-10), Occurrence (1-10), Detection (1-10)
- Calculate RPN = S×O×D
- Sort by RPN descending
- Recommend actions for top items

Output: Component | Function | Failure Mode | Effect | S | O | D | RPN | Action
```

**2.2 — HAZOP**

```
Here is the data flow architecture:

[PASTE: flow diagram or description]

Apply all HAZOP guide words to each node:
- NO: Input/output doesn't arrive?
- MORE: Too much?
- LESS: Too little?
- REVERSE: Direction/logic inverted?
- OTHER THAN: Something unexpected?
- AS WELL AS: Extra unwanted content?
- EARLY / LATE: Timing off?

For each deviation: Cause | Consequence | Safeguard | Recommended Action
```

**2.3 — STPA**

```
Model this system as a control hierarchy:

[PASTE: roles, agents, relationships]

For each control action, identify Unsafe Control Actions:
- NOT PROVIDED: Controller doesn't issue needed command
- PROVIDED UNSAFELY: Wrong parameters
- WRONG TIMING: Right command, wrong time
- WRONG DURATION: Applied too long/briefly

For each UCA: What's missing from the controller's process model? Derive safety constraint.
```

**2.4 — Mental Model Simulations**

```
For the top 5 findings from FMEA (by RPN):

[PASTE TOP 5]

For each, simulate:
- ACTION: What we plan to do
- PREDICTED CONSEQUENCE: What will happen (limit 2-3 causal factors)
- FAILURE CONDITION: How this could go wrong
- DETECTION SIGNAL: How we'd know it went wrong
- TIMELINE: When we'd see the signal
```

**2.5 — Entropy Calculation (do in Sheets)**

After each framework, recalculate in Google Sheets:
```
Step 1: List alternatives and their current probability estimates
Step 2: Calculate entropy: =-SUMPRODUCT(probs, LOG(probs, 2))
Step 3: Normalize: =entropy / LOG(COUNT(alternatives), 2)
Step 4: If this isn't the first framework, calculate:
        D_KL = SUMPRODUCT(new_probs, LOG(new_probs/old_probs, 2))
Step 5: Log H_norm and D_KL in the entropy convergence table
Step 6: Stop if H_norm < 0.15 OR D_KL < 0.01
```

---

### Phase 3 Prompts

**3.2 — Command Center Structure**

```
Create a Google Sheets structure for a project Command Center with 12 tabs:

1. Pages/Components — per-item metrics, status, ownership
2. Hypotheses — #, hypothesis, signal, prior α/β, thresholds, status, week updated
3. Weekly Data — automated intake from data sources
4. Bayesian Reference — BETA.DIST calculations and update history
5. Scenario Model — Monte Carlo inputs/outputs (Pessimistic/Moderate/Optimistic/Seasonal)
6. Conflicts/Cannibalization — tracking overlaps and resolutions
7. AI Visibility — GEO/AEO audit results (30-prompt cycle)
8. Content/Action Calendar — editorial schedule linked to hypotheses
9. Deploy Checklist — per-change verification steps
10. Change Log — date, what, who, why, version
11. Gate Review Log — date, hypothesis, votes, decision, crux, steelman
12. Team Dashboard — role-based summary view

For each tab: exact column headers, data validation dropdowns, and conditional formatting rules.
```

**3.4 — Sequential Monitoring Setup (do in Sheets)**

```
Step 1: Define K = number of planned interim looks (typically 5)
Step 2: Create a table with columns: Look #, Info Fraction, z-boundary
Step 3: For 5-look O'Brien-Fleming at α=0.05, use these boundaries:
        Look 1 (0.20): 4.56
        Look 2 (0.40): 3.23
        Look 3 (0.60): 2.63
        Look 4 (0.80): 2.28
        Look 5 (1.00): 2.04
Step 4: After each look, compute your test statistic z
Step 5: Compare z to boundary. If z > boundary → CONFIRM
Step 6: Also compute conditional power. If < 15% → FUTILITY STOP
```

---

### Phase 4 Prompts

**4.3 — Adaptive Framework Selection (Claude prompt)**

```
I've been tracking framework insight scores across [N] cycles:

[PASTE FRAMEWORK INSIGHT TABLE]

1. Which frameworks are producing the most insight? (Rank by average score)
2. Which should I drop? (Zero insight for 2+ cycles)
3. How should I reallocate effort? (3× rule: if A = 3× B, shift 75% to A)
4. Are there frameworks NOT in my current set that would add value for 
   this project type? Suggest 1-2 based on the pattern of what's working.
```

**4.5 — Reflexion Self-Correction (after each execution step)**

```
I just completed this execution step:

STEP: [describe what was done]
EXPECTED OUTCOME: [what I predicted would happen]
ACTUAL OUTCOME: [what actually happened]

1. CRITIQUE: What's the gap between expected and actual?
2. DIAGNOSE: Root cause — wrong assumption? Missing data? Wrong framework?
3. PRESCRIBE: Specific, actionable correction for my next step
4. PRE-FLECT: Before the next step [describe next step], what are the top 3 
   anticipated failure modes and how do I mitigate each?
```

**4.6 — Gate Review Disagreement Resolution**

```
We have a disagreement on hypothesis H[N]:

HYPOTHESIS: [paste]
CURRENT DATA: [paste]
VOTES: [list each reviewer's vote and reasoning]

1. DOUBLE_CRUX: What is the ONE underlying belief that, if proven wrong, 
   would change the minority voter's position? And the majority's?
2. Is this crux testable? If yes, design a test (2 sentences).
3. STEELMAN: Build the strongest possible case for the minority position. 
   What conditions would make them right?
4. RECOMMENDATION: Should we CONFIRM, REJECT, or EXTEND? Why?
```

**4.7a — Chaos Engineering Design**

```
I have a system with these components:

[PASTE ARCHITECTURE]

Design 3 chaos experiments:
1. What happens if [critical component] fails completely?
2. What happens if [data source] sends garbage/stale data?
3. What happens if [integration point] responds with 10× latency?

For each:
- Steady-state metric and its normal value
- What SHOULD happen (graceful degradation)
- What MIGHT happen (cascading failure)
- Pass/fail criteria
- Blast radius (what percentage to test on)
```

**4.7b — Ablation Study**

```
Here is my system with [N] components:

[LIST ALL COMPONENTS AND THEIR ROLES]

For each component:
- What happens if we remove it entirely?
- Estimated performance delta (% change in primary metric)
- Classification: REDUNDANT (<5% delta → remove), CRITICAL (>20% delta → add 
  backup), or CONTRIBUTING (5-20% → keep as-is)

Which components can be simplified or merged?
```

---

### Phase 5 Prompts

**5.1 — Causal Verification**

```
I'm claiming these results:

[PASTE RESULTS WITH NUMBERS]

My interventions were:

[LIST WHAT WE CHANGED]

1. CAUSAL DAG: Draw the causal graph. Intervention → Mediators → Outcome. 
   What are the confounders? (Seasonality, algorithm updates, competitors, market)
2. COUNTERFACTUAL: Could results have happened WITHOUT our intervention?
3. ATTRIBUTION: High/Medium/Low confidence with reasoning.
4. What would make us WRONG about causation?
```

**5.2 — Swiss Cheese Post-Mortem**

```
During this project, we had these failures/near-misses:

[LIST INCIDENTS]

For each, apply Swiss Cheese:
1. Every defense layer that should have caught it
2. The "hole" in each layer
3. ACTIVE failure (operator error) or LATENT condition (systemic)?
4. Latent conditions to fix for prevention
5. How to ensure layer diversity (different mechanisms per layer)
```

**5.3 — HRO Debrief**

```
Conduct an HRO debrief for this project:

1. PREOCCUPATION WITH FAILURE: What near-misses did we catch? What did we miss? 
   What weak signals should we have noticed?
2. RELUCTANCE TO SIMPLIFY: Where did we oversimplify? What nuance did we flatten?
3. SENSITIVITY TO OPERATIONS: Did we stay close to ground truth? Where did our 
   model diverge from reality?
4. COMMITMENT TO RESILIENCE: When something unexpected happened, how fast did we 
   adapt? What slowed us down?
5. DEFERENCE TO EXPERTISE: Did decisions flow to whoever knew most? Were there 
   cases where hierarchy overrode expertise?
```

**5.4 — Red Team**

```
Red team my deliverables before handoff:

SYSTEM: [describe]
DELIVERABLES: [list]

1. Try to break the model: What inputs produce nonsensical outputs?
2. Edge cases we haven't tested?
3. If a hostile actor wanted wrong recommendations, how?
4. Most likely failure in first 30 days without us?
5. Rate ASR for each: Content integrity, Data pipeline, Decision logic, Human oversight.
```

**5.8 — Meta-Learner Reflection (end of project)**

```
Project just completed. Help me extract meta-learning insights:

PROJECT FEATURES:
- Type: [fill]
- Complexity: [1-5]
- Data richness: [1-5]
- Duration: [weeks]

FRAMEWORK PERFORMANCE:
[PASTE INSIGHT SCORES TABLE]

PREDICTIONS VS ACTUALS:
- Predicted duration: __ → Actual: __
- Predicted P(success): __% → Actual: [success/failure]
- Predicted bottleneck: Phase __ → Actual: Phase __

1. Which frameworks over-performed? Under-performed? Why?
2. Where was I over-confident? Under-confident?
3. What ONE thing should I do differently on the next project of this type?
4. Update my Brier score: [compute]
5. If I did this project again, what would I skip? What would I add?
```

---

## PROMPT INDEX — Quick Copy

| Step | First Words |
|---|---|
| 0.1 | "You are an AI systems architect. Classify using Cynefin..." |
| 0.2 | "Given this project brief, audit the variety..." |
| 0.3 | "Design the OODA loop for this project..." |
| 0.4 | "I need a reference-class forecast..." |
| 0.5 | "Score the Decision Quality Appropriate Frame..." |
| 1.1-2 | "Decompose into testable hypotheses. RPD check..." |
| 1.3 | "Run 5 MECE tests on this decomposition..." |
| 1.4 | "Apply portfolio theory to these hypotheses..." |
| 1.5 | "Analyze hypothesis H[N] through 10 frameworks..." |
| 2.1 | "Conduct FMEA on these components..." |
| 2.2 | "Apply HAZOP guide words to each node..." |
| 2.3 | "Model as control hierarchy, identify UCAs..." |
| 2.4 | "For top 5 FMEA findings, simulate..." |
| 3.2 | "Create Command Center with 12 tabs..." |
| 4.3 | "Track framework insight, rank, drop, reallocate..." |
| 4.5 | "Reflexion: critique, diagnose, prescribe, pre-flect..." |
| 4.6 | "Disagreement on H[N]. DOUBLE_CRUX + STEELMAN..." |
| 4.7a | "Design 3 chaos experiments..." |
| 4.7b | "Ablation: remove each component, measure delta..." |
| 5.1 | "Causal DAG. Confounders? Counterfactual?..." |
| 5.2 | "Swiss Cheese: defense layers, holes, active/latent..." |
| 5.3 | "HRO debrief: 5 principles..." |
| 5.4 | "Red team deliverables: break it, edge cases, ASR..." |
| 5.8 | "Meta-learning reflection: frameworks, predictions, Brier..." |

---

## WEEKLY CYCLE — TIMED CHECKLIST (42 min total)

| Step | Time | Tool | Action |
|---|---|---|---|
| 1. Pull data | 15 min | GSC / GA4 | Export → paste into Weekly Data tab |
| 2. Update priors | 10 min | Google Sheets | new_α = old_α + successes; new_β = old_β + failures |
| 3. Thompson priority | 5 min | Google Sheets | =BETA.INV(RAND(), α, β) per hypothesis → sort desc → top = focus |
| 4. EVOI check | 5 min | Mental / Sheets | Would one more cycle change my decision? |
| 5. Info Gain ranking | 5 min | Mental | Which pending action reduces most uncertainty? |
| 6. Gate review check | 2 min | Gate Review Log | 4+ weeks since last? Schedule. Hypothesis crossed threshold? Emergency review. |

---

## GOOGLE SHEETS SETUP GUIDE

### Entropy Tracking Tab

| Column A | Column B | Column C | Column D | Column E |
|---|---|---|---|---|
| Framework Applied | H_norm | ΔH | D_KL | Action |
| (text) | `=-SUMPRODUCT(B2:B11,LOG(B2:B11,2))/LOG(COUNT(B2:B11),2)` | `=C_prev - C_current` | `=SUMPRODUCT(new,LOG(new/old,2))` | `=IF(B<0.15,"STOP",IF(D<0.01,"STOP","CONTINUE"))` |

### Sequential Monitoring Tab

| Column A | Column B | Column C | Column D | Column E |
|---|---|---|---|---|
| Look # | Info Fraction | z-boundary | Observed z | Decision |
| 1 | 0.20 | 4.56 | [from test] | `=IF(D2>C2,"CONFIRM","CONTINUE")` |
| 2 | 0.40 | 3.23 | [from test] | `=IF(D3>C3,"CONFIRM","CONTINUE")` |

### Thompson Sampling Tab

| Column A | Column B | Column C | Column D | Column E |
|---|---|---|---|---|
| Hypothesis | Current α | Current β | Sample | Rank |
| H1 | [value] | [value] | `=BETA.INV(RAND(),B2,C2)` | `=RANK(D2,D$2:D$20)` |

### Brier Score Tracker

| Column A | Column B | Column C | Column D |
|---|---|---|---|
| Prediction | Forecast (0-1) | Outcome (0 or 1) | Squared Error |
| [description] | [probability] | [actual] | `=(B2-C2)^2` |
| **Brier Score** | | | `=AVERAGE(D2:D100)` |

---

*"The system stays. The consultant leaves. The team keeps running."*
