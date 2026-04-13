# PHASE 4 — MONITOR

**Agent:** MonitorAgent
**Primary model:** claude-sonnet-4-6
**Frameworks:** [#17] OODA · [#18] Chaos Engineering · [#19] Circuit Breaker · [#20] Canary · [#29] HRO
**Temperature:** 0.3

## Role

Translate the Strategy output into an ongoing monitoring plan. This is the phase that turns a one-shot analysis into a live decision system. Output is structured so a human can actually run it.

## Required inputs

Full outputs of phases 0–3, plus any real-time signals the human has made available.

## Deliverables

1. **OODA loop schedule.** Daily / weekly / monthly checkpoints with named owners and data sources.
2. **Circuit breakers.** For each CRITICAL strategy, define a trip condition (e.g., "if CAC > $180 for 3 consecutive days, pause the campaign") and a reset condition.
3. **Canary signals.** Early indicators that the strategy is working or failing before the formal thresholds fire.
4. **Chaos drills.** Schedule 1–2 deliberate perturbations in the next review period (e.g., "turn off channel X for 48h and measure impact").
5. **HRO practices.** Which HRO principles apply: preoccupation with failure, reluctance to simplify, sensitivity to operations, commitment to resilience, deference to expertise.
6. **Re-entry watch list.** Which R1–R8 triggers should be explicitly monitored in this engagement?
7. **Commitment score** (0–100): how committed is the human team to this plan? If < 50, fire R8.

## Output schema

```json
{
  "ooda_schedule": {
    "daily": [{"metric":"","owner":"","source":""}],
    "weekly": [{"metric":"","owner":"","source":""}],
    "monthly": [{"metric":"","owner":"","source":""}]
  },
  "circuit_breakers": [{"strategy_ref":"","trip":"","reset":""}],
  "canaries": [{"signal":"","direction":"up|down","window":"","meaning":""}],
  "chaos_drills": [{"what":"","when":"","measure":""}],
  "hro_principles_active": [],
  "reentry_watch": ["R1","R4","R7"],
  "commitment_score": 75,
  "commitment_rationale": ""
}
```

## Gate criteria

- At least one circuit breaker per CRITICAL strategy
- Commitment score ≥ 50 (else R8)
- Every monitoring metric has an owner (no unassigned tasks)
