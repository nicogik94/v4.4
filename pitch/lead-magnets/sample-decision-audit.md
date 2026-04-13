# Sample Decision Audit
## "Should we expand our B2B SaaS into the enterprise segment in Q3?"

> **About this document.** This is a redacted example of a Decision Audit produced by the Decision Engine — a structured decision-analysis method developed and operated by Nicolás Grinberg. Client identifying details have been changed. The structure, framework usage, and level of rigor are representative of a real engagement. Total time from brief to dossier: **6 hours of analyst time, 11 minutes of compute**. Cost to the client: **$1,500**.

**Date:** March 2026
**Client:** (redacted) — a 28-person B2B SaaS company, $2.1M ARR, SMB-focused
**Decision:** Whether to build an Enterprise tier in Q3 2026 or double down on SMB
**Confidence:** Medium-High (74/100)

---

## 1. Executive summary

**Recommendation: Delay the enterprise expansion by 1 quarter. Use Q3 to run three structured experiments that will materially update the decision by October 15.**

The enterprise pivot is attractive on paper (2 warm inbound conversations, 4× ACV) but four independent lenses flagged the same failure mode: the team lacks the enterprise sales motion, the product lacks 3 table-stakes compliance features, and the opportunity cost is a 6-month SMB roadmap freeze during the window when your closest SMB competitor is raising a Series A.

The three things that would **change** this recommendation:

1. One of the two warm enterprise conversations converts into a paid pilot with a committed timeline by July 15.
2. A SOC 2 Type 2 report can be credibly promised within 90 days (currently unverified).
3. Your SMB competitor's Series A is delayed past Q4 (currently unknown).

---

## 2. Problem framing (Phase 0 — Classify)

**Cynefin domain:** Complex. Multiple interacting forces (product, GTM, competition, financing), cause-and-effect only visible in retrospect. Experimentation beats analysis-alone. *(Justification: the team has never run an enterprise motion, so no reference pattern applies.)*

**Bayes Factor on "this problem is well-understood":** BF = 6. Below the confidence threshold of 10 — we should treat the framing itself as provisional and plan to re-classify after the first experiment.

**Requisite Variety:**
- **Environmental variety** (what you face): 3 customer segments, 2 enterprise buyer personas, 1 known compliance gate
- **System variety** (what you have): 1 GTM motion (SMB self-serve), 0 enterprise sales hires, 0 compliance artifacts
- **Gap:** 3 variety gaps, all on the system side
- **Decision:** Amplify (hire) before Attenuate (narrow scope)

**Data Quality frame:** 62/100. Clarity of goal: good. Clarity of success criterion: weak (no numeric target for "enterprise win").

---

## 3. Hypotheses and verdicts (Phases 1–3)

10 hypotheses generated, gauntleted across 10 frameworks, verdicts after strategy phase:

| ID | Statement | Prior (α,β) | Preliminary verdict | Evidence |
|----|-----------|-------------|---------------------|----------|
| H1 | ≥ 1 of 2 warm enterprise conversations closes within 90 days | (5, 5) | **Needs monitoring** | One is at "exec interest" stage; the other stalled 3 weeks |
| H2 | Current product passes enterprise technical eval without > 4 weeks of work | (2, 8) | **Likely rejected** | No SOC 2, no SSO, no audit log |
| H3 | SMB churn will stay below 3% monthly through Q3 without roadmap attention | (3, 7) | **Likely rejected** | Competitor funding likely; attention war coming |
| H4 | Enterprise ACV will be ≥ 4× SMB ACV | (7, 3) | **Likely confirmed** | Market comps support 5–8× multiple |
| H5 | Hiring a first enterprise AE is findable in 30 days at < $180K OTE | (6, 4) | **Needs monitoring** | Market tight; possible but not guaranteed |
| H6 | Opportunity cost of SMB roadmap freeze is > value of Q3 enterprise progress | (7, 3) | **Likely confirmed** | SMB funnel is the proven channel; freeze is expensive |
| H7 | Both enterprise conversations represent the same latent pattern (not independent signals) | (6, 4) | **Likely confirmed** | Both contacts came from the same investor intro |
| H8 | We can credibly reach SOC 2 Type 2 within 90 days starting today | (3, 7) | **Likely rejected** | Typical timeline is 6–9 months from zero |
| H9 | Enterprise-first messaging will not cannibalize SMB positioning | (5, 5) | **Needs monitoring** | Unclear without a landing-page test |
| H10 | The founder's time is the binding constraint, not capital | (8, 2) | **Likely confirmed** | Burn multiple is fine; founder hours are the scarce resource |

**MECE tests passed:** 5/5. **Portfolio correlation:** 0.37 (below the 0.5 threshold).

---

## 4. Risks and audit findings (Phase 2)

Data-based: **False**. All findings labeled `PREDICTED` — the team has no enterprise sales history to measure against, so this is scenario analysis, not retrospective.

**Top FMEA entries:**

| Component | Failure mode | S | O | D | RPN | Action |
|-----------|--------------|---|---|---|-----|--------|
| Enterprise sales motion | No playbook, first AE hire fails probation | 8 | 6 | 5 | 240 | Commit to a 90-day enterprise learning sprint before any hire |
| Compliance (SOC 2) | Audit timeline slips past enterprise decision window | 8 | 7 | 4 | 224 | Scope Type 1 report first (3 months); negotiate bridge letter |
| SMB roadmap | Competitor ships feature parity during freeze | 7 | 6 | 4 | 168 | Protect 2 engineers for SMB; do not freeze all roadmap |
| Founder bandwidth | Split attention collapses both motions | 9 | 5 | 3 | 135 | Assign one exec to own the enterprise track end-to-end |
| Cash runway | Slower burn from hires + slower revenue | 6 | 5 | 3 | 90 | Model the 9-month no-enterprise baseline as a decision anchor |

**Top STPA unsafe control actions:**
- "Commit to SOC 2 Type 2 in 90 days" — provided; causes reputational hazard if slipped
- "Freeze SMB roadmap" — not provided with sufficient scope boundaries; over-freezes the wrong things

**Swiss Cheese:** The existing hole stack (no enterprise playbook, no compliance, no enterprise AE) means a single enterprise commitment failure propagates all the way through.

---

## 5. Strategy (Phase 3)

**SQI score: 78/100.** Weakest dimension: specificity of success metrics (62). All ten strategies trace to at least one hypothesis and one audit finding.

**Top 5 ranked actions:**

1. **[CRITICAL]** Run three 6-week enterprise experiments in Q3 (not a full pivot). **Justification:** Converts the bet from "pivot or not" into "collect information cheaply." Evidence chain: H1 + H2 + FMEA enterprise motion → structured experiments. **Timeline:** 6 weeks. **Framework source:** HDD + EVOI.

2. **[CRITICAL]** Start SOC 2 Type 1 intake immediately (before the decision is final). **Justification:** Whichever way you go, this reduces time-to-yes on enterprise deals by 60%. Evidence chain: H8 + STPA unsafe action → pre-commitment. **Timeline:** Week 1. **Framework source:** Real options.

3. **[HIGH]** Protect 2 engineers on the SMB roadmap, no exceptions. **Justification:** H3 + FMEA competitor funding → hedge against the attention war. Evidence chain: H3 + H6 → SMB protection. **Timeline:** Week 1. **Framework source:** Premortem.

4. **[HIGH]** Assign a single exec owner for the enterprise track, with a kill-switch. **Justification:** Founder bandwidth is the binding constraint (H10). Evidence chain: H10 + FMEA founder bandwidth → single-owner model. **Timeline:** Week 1. **Framework source:** HRO deference to expertise.

5. **[MEDIUM]** Run a landing-page split test on enterprise-first vs SMB-first positioning, behind a feature flag. **Justification:** Cheapest way to test H9 without committing the website. Evidence chain: H9 → A/B test. **Timeline:** 2 weeks. **Framework source:** Canary.

---

## 6. Monitoring plan (Phase 4)

**Circuit breakers:**
- If enterprise conversation 1 stalls > 4 weeks past July 15 → pause all enterprise hires
- If SMB monthly churn exceeds 3.5% for 2 consecutive months → return 1 of the protected engineers to enterprise track
- If SOC 2 Type 1 intake slips past week 3 → reset the 90-day clock and re-run this audit

**Canaries:**
- Weekly: enterprise pipeline stage movement (direction > magnitude)
- Weekly: SMB NPS (early churn signal)
- Bi-weekly: engineering throughput on the SMB protected work

**Re-entry triggers to watch:** R1 (assumption shift on the competitor funding), R4 (correlation between H1 and H7 — if both fail for the same underlying reason), R7 (strategy SLO breach on enterprise experiments).

**Commitment score:** 71/100. The exec team is aligned on the recommendation but the founder is still emotionally attached to the full-pivot framing.

---

## 7. What we got wrong (Red Team)

A skeptical reader would challenge three things:

1. **The base rate assumption on enterprise sales hiring (H5).** I used a 60% "findable" prior, but the specific vertical is tight. A more honest prior is (4, 6), not (6, 4). This weakens the case for delaying rather than fully committing.
2. **H7 is a hunch, not a finding.** "Both conversations came from the same investor intro" is true but I did not verify that they share a buying pattern. They might be independent signals from a shared origin.
3. **The 6-hour time estimate does not account for the founder's cognitive load.** Running experiments in parallel with a working SMB motion is harder than the plan implies.

None of these collapse the recommendation, but the confidence should probably be 68/100, not 74/100. The meta-learner will track this.

---

## 8. Ablation check

If we removed one framework from this analysis, which would be the most missed?

**Answer: EVOI (Expected Value of Information).** It is the framework that turned a binary "pivot or not" question into "what information would resolve this cheapest?" — which is the recommendation. Without EVOI the analysis would have forced a premature commitment in either direction.

---

## 9. Causal map

```
(competitor funding) ──┐
                       ├─► SMB attention war ──► roadmap protection decision
(founder bandwidth) ──┤
                       └─► enterprise track owner assignment

(warm intros) ─► enterprise interest ─► pilot decision ─► full pivot decision
                                             ▲
                                             │
                                    (SOC 2 readiness)
```

Solid arrows = causal with supporting evidence. Dashed arrows would be correlational-only (none in this map).

---

## 10. Calibration

The team's stated confidence in the original pivot plan was **82/100**. Our analysis suggests **68–74/100** is better calibrated. The delta of **~10 points** is the primary signal the meta-learner will log. We will re-score once the experiments resolve.

---

## What you would get

If you engage the Decision Engine for a real project like this one:

- **A 10-section dossier** like this one, tailored to your decision, within 5 business days
- **A 60-minute walk-through call** where we defend every claim and show our work
- **The structured monitoring plan** you can hand to whoever owns execution
- **Post-decision calibration** — we log the outcome and improve our priors for your next decision

**Price:** $1,500 for a Decision Audit Lite like this one. $5,000–$15,000 for a full engagement with quantitative modeling and executive workshops.

**To book a free 30-minute decision intake call:** `<your booking link>`
or email **`<your email>`** with a 2–3 sentence description of the decision.

---

*© 2026 Nicolás Grinberg · Universal Project Workflow v4.1*
