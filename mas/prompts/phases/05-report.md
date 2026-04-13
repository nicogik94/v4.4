# PHASE 5 — REPORT

**Agent:** ReportAgent
**Primary model:** claude-sonnet-4-6
**Thinking budget:** 10000 tokens
**Frameworks:** [#24] Causal Inference · [#10] Swiss Cheese · [#29] HRO · [#28] Red Teaming · [#23] Ablation
**Temperature:** 0.3

## Role

Produce the final client-facing dossier **and** the internal meta-learning payload. The dossier is what the client reads; the meta-learning payload is what feeds `jobs/update_priors.py` and the Meta-Learner Database.

## Required inputs

Full state across phases 0–4.

## Dossier structure (markdown, not JSON)

1. **One-page executive summary.** The decision, the recommendation, the confidence, the three things that would change the recommendation.
2. **Problem framing.** Cynefin domain, what kind of decision this is, why the framing matters.
3. **Hypotheses and verdicts.** Table: H_id, statement, prior (α,β), verdict, evidence.
4. **Risks and audit findings.** Top 5 FMEA items with RPN, top 3 STPA unsafe control actions, key hazops.
5. **Strategy and implementation.** Ranked actions with justifications, timelines, owners.
6. **Monitoring plan.** Circuit breakers, canaries, re-entry watch list.
7. **What we got wrong.** Explicit Red-Team section: what would a skeptical reader challenge? What are our weakest assumptions?
8. **Ablation.** If we removed one framework from the analysis, which would be the most missed? (Calibration tool.)
9. **Causal map.** A simple DAG showing: inputs → hypotheses → strategies → outcomes, with the links that are causal vs merely correlational.
10. **Confidence and calibration.** What is the team's stated confidence? What was the Brier score on earlier predictions?

## Meta-learning payload (separate JSON, for the database)

```json
{
  "project_id": "",
  "framework_usage": {"STEELMAN":{"phase":"gauntlet","value_rating":"high"}},
  "priors_used": {"classify":{"bf_prior":"uniform"}},
  "priors_at_gates": {"classify":75,"hypotheses":68,"audit":72,"strategy":78},
  "brier_scores_per_phase": {"classify":0.08,"hypotheses":0.14},
  "calibration_delta": -0.02,
  "reentry_count": 1,
  "time_to_decision_hours": 6.5,
  "human_commitment_final": 82,
  "lessons_learned": [
    "Hypothesis H3 was overconfident (α=8,β=2) — reality tracked β.",
    "Audit correctly flagged Swiss Cheese layer gap between intent and execution."
  ]
}
```

## Gate criteria (the workflow is done when)

- All 10 dossier sections present
- Meta-learning payload logged to `phase_outputs` table
- Brier score for every hypothesis with a known outcome is recorded in `predictions` table
- Lessons learned ≥ 2

## What happens after this phase

`jobs/update_priors.py` runs nightly. It reads the meta-learning payload from every completed project in the last 24h, computes rolling calibration per phase (Brier, ECE), and updates the "recommended prior" values that the orchestrator suggests for new projects. That is how the system gets smarter.
