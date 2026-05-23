# v5 Analysis Quality

This document covers v5 analysis-quality work layered on top of the hardened
runtime foundation. It does not claim causal truth, automatic evidence proof, or
replacement of expert review.

## Tranche 6 Changes

- implemented: hypothesis variable coverage is assessed by a deterministic,
  advisory helper.
- implemented: the hypotheses prompt asks the model to express causal drivers,
  assumptions, validation evidence, owner or approval dependency, timing, and
  recommendation-changing results using the existing hypothesis JSON fields.
- implemented: operator dossiers include a concise variable coverage summary.
- implemented: operator SQI / quality review can flag missing
  decision-critical variable coverage.
- unchanged: the v4.4 workflow order remains
  `classify -> hypotheses -> gauntlet -> audit -> strategy -> sqi -> monitor -> report`.
- unchanged: the hypothesis JSON schema remains the same; no new required keys
  are added.

## What Variable Coverage Means

Variable coverage asks whether the hypothesis set names the decision variables
that could plausibly change the recommendation. Examples include demand/user
segment, channel/acquisition, activation/onboarding, retention/repeat usage,
pricing, operational capacity, measurement/data quality, legal or compliance
constraints, competitive dynamics, implementation complexity, owner/decision
authority, timing/cadence, and validation evidence.

The coverage helper does not require every project to cover every category.
It marks categories as relevant only when the project context or hypotheses
indicate the dimension matters. For example, a legal claim-safety SLA decision
is not forced to cover pricing, acquisition channels, or retention unless those
dimensions appear in the context.

## How It Affects Hypotheses

The prompt now asks hypotheses to be more explicit about:

- the causal driver or decision variable being tested,
- the assumption behind the hypothesis,
- the signal or evidence needed to validate it,
- the confirm/reject gate,
- the owner or approval dependency where relevant,
- the time horizon or review cadence,
- what result would change the recommendation.

Those details must fit inside the existing fields: `text`, `justification`,
`signal`, `confirm`, `reject`, and `portfolio_cluster`.

## Operator Diagnostics

Operator dossiers show:

- covered variable categories,
- missing decision-critical categories,
- evidence needs for missing categories.

The diagnostics are intentionally concise. They avoid keyword dumps, raw
heuristic matches, internal category keys, and debug traces.

## What Remains Unsupported

- no guarantee of causal truth,
- no semantic evidence proof for every claim,
- no automatic business-truth validation,
- no guarantee that the LLM generated a complete hypothesis set,
- no replacement for domain expert review,
- no client-facing raw variable coverage debug output.

Tranche 7 monitoring templates and Tranche 8 quality gates are documented in
`v5-MONITORING-TEMPLATES.md` and `v5-QUALITY-EVALS.md`.

Human review remains required before acting on recommendations, especially
where legal, financial, medical, safety, compliance, or public claims are
involved.
