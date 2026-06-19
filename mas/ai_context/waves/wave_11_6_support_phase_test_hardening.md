# Wave 11.6 — Engine Reliability + Support-Phase Test Hardening

## Goal

Add deterministic coverage around the weakest support-phase reliability areas identified in Wave 11.3: gauntlet, SQI, monitor, and small existing monitoring / circuit-breaker interpretation paths.

This wave improves confidence in existing behavior. It does not introduce new product behavior.

## Scope

- Add focused unit tests for gauntlet support-phase threshold behavior.
- Add tests that gauntlet absence is safe for downstream prompt construction.
- Add tests that gauntlet remains an internal support phase, not a delivery approval gate.
- Add SQI tests for structured output shape, weakest-link preservation, and advisory status.
- Add monitor tests for monitoring-plan structure, existing gate configuration, and non-autonomous wording boundaries.
- Add small tests that policy, workspace, and delivery-readiness projections surface kill switch, failed phase, budget circuit-breaker, and phase-breaker states.

## Non-Goals

- No workflow routing redesign.
- No runner consolidation.
- No report or export behavior changes.
- No delivery-readiness semantic expansion.
- No eval semantics changes.
- No semantic evidence verification.
- No Evidence Gauge.
- No Defense Index.
- No Claim Cards.
- No automatic delivery approval.
- No autonomous monitoring or action behavior.
- No public SaaS, multi-tenant, chatbot, or BI behavior.

## Tests Added

Focused deterministic tests in `tests/test_support_phases.py` cover:

- graph hypotheses node skips gauntlet when fewer than three hypotheses are present
- graph hypotheses node runs gauntlet at three hypotheses and preserves gauntlet output shape
- downstream prompts tolerate absent gauntlet output
- gauntlet is reversible internal policy work and does not require delivery approval
- SQI phase stores `sqi_overall`, dimensions, `weakest_link`, and improvement actions
- low SQI is surfaced as an advisory score without becoming a blocking or approval signal
- monitor output remains a monitoring plan with OODA, canaries, circuit breakers, and re-entry watch
- monitor gate behavior matches the existing human-driven config
- open phase breakers are reported by policy gate without autonomous action
- workspace and delivery-readiness projections surface kill switch, failed phase, budget circuit-breaker, and phase-breaker reasons

Regression verification should include existing delivery-readiness and eval mock tests so Wave 11.4, Wave 11.5, and Wave 11.5A boundaries remain intact.

## Source Files Changed

None. This wave is test and context hardening only.

## Behavior Intentionally Unchanged

- Support-phase routing and existing runner behavior are unchanged.
- SQI remains advisory self-inspection.
- Monitor remains a plan-structuring phase, not live autonomous monitoring.
- Circuit-breaker and kill-switch states are reported and enforced by existing deterministic policy/readiness/workspace code; this wave does not add auto-action behavior.
- Delivery Review Readiness remains advisory and does not approve delivery.
- Report/export behavior is unchanged.
- Wave 11.5A provider-quota aggregate diagnostics remain unchanged; this wave does not edit eval code or eval semantics.

## Human Review Boundary

MAS remains a local/operator-first decision-analysis engine. It is not a chatbot, BI surface, public SaaS, multi-tenant account system, or autonomous action system. Human review remains mandatory before delivery or operational action.
