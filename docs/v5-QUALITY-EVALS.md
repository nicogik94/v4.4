# v5 Quality Evals

This document covers v5 Tranche 8 quality-polish checks for client/operator
separation and export regression coverage. It is a review checklist, not a
claim that outputs are automatically true or production-safe.

## What T8 Validates

- client exports stay concise, non-technical, and free of raw internal IDs or
  diagnostics,
- operator exports keep useful trace, evidence, threshold, and coverage
  diagnostics after redaction,
- monitoring template XLSX exports stay spreadsheet-safe and deterministic,
- machine archive report content is not changed by client/operator exports,
- workflow order remains
  `classify -> hypotheses -> gauntlet -> audit -> strategy -> sqi -> monitor -> report`.
- decision memo / pilot plan reports receive deterministic, advisory,
  non-blocking quality findings when generated in that mode.

## Client / Operator Separation Rules

Client-facing outputs should not expose:

- raw `knowledge_...`, evidence, source, file, upload, or storage identifiers,
- local paths, secrets, provider payloads, or chain-of-thought,
- BF/RPN/internal confidence jargon unless normalized,
- raw variable-coverage keys or debug labels,
- unsupported “confirmed” causal language under Partial or Hypothesis-only
  evidence,
- unresolved placeholder phrases such as “provisional threshold”.

Operator outputs may include evidence IDs, hypothesis IDs, threshold
classification, variable coverage, and monitoring-template trace fields when
they help review/debug the decision. Operator outputs must still redact secrets,
provider payloads, chain-of-thought, local paths, and unsafe storage refs.

## Export Safety Checklist

- Report, client dossier, and operator dossier exports still build in supported
  formats.
- Client citation cleanup does not leave orphaned sentence fragments.
- Evidence maturity language is visible and does not overclaim validation.
- Narrative file counts do not contradict structured upload/parse counts.
- Machine archive raw report content remains invariant across client/operator
  export generation.

## Monitoring Template Checklist

- Client XLSX contains stable headers and no raw internal identifiers.
- Operator XLSX retains trace columns after redaction.
- Formula-like cells beginning with `=`, `+`, `-`, or `@` are escaped.
- Workbook cell content is deterministic.
- Monitoring templates remain operational tracking artifacts and do not create a
  second Decision Gates system.

## Evidence Maturity Language Checks

- Hypothesis-only evidence remains labeled as internal planning.
- Partial evidence remains labeled as requiring targeted validation.
- Validated language requires concrete locators/imported evidence signals.
- Evidence markers identify source material; they do not prove semantic support
  by themselves.

## Decision Memo / Pilot Plan QA

The deterministic helper for `decision_memo_pilot_plan` checks:

- required memo sections and separated technical appendix,
- duplicate or empty headings,
- malformed markdown table boundaries,
- likely incomplete final sentence,
- contradictory explicit counts versus immediately following enumerations,
- unsupported numeric-claim patterns in the main memo,
- source-supported labels without concrete locators from the existing register,
- evidence-maturity versus certainty-wording mismatch,
- generated report language versus required heading language mismatch,
- monitoring-signal text versus generated monitoring-template rows.

These findings are advisory and surfaced through workspace/dashboard state. They
do not block report completion and do not claim to prove semantic truth.

## Limitations

- no guarantee of causal truth,
- no semantic evidence proof for every claim,
- evidence marker resolvability is checked, but title/filename presence depends on
  available locator metadata; missing source metadata remains operator-review
  required,
- no automatic business-truth validation,
- no replacement for expert/domain review,
- no public SaaS readiness,
- no auth, multi-tenancy, or tenant isolation.

Human review remains required before acting on recommendations or thresholds,
especially for legal, financial, medical, safety, compliance, or public claims.
