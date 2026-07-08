# v5 Monitoring Templates

This document covers v5 Tranche 7 monitoring template exports. The templates
turn existing decision-review outputs into spreadsheet-ready tracking artifacts.
They do not change the workflow, create a new Decision Gates system, or validate
business truth automatically.

## What T7 Adds

- implemented: client and operator XLSX monitoring template exports.
- implemented: deterministic rows projected from Decision Gates, OODA schedule
  items, circuit breakers, canaries, strategy success metrics, and hypotheses
  marked as needing monitoring.
- implemented: spreadsheet formula-injection protection for every exported cell.
- implemented: client/operator separation for internal trace details.
- unchanged: the workflow order remains
  `classify -> hypotheses -> gauntlet -> audit -> strategy -> sqi -> monitor -> report`.
- unchanged: machine archive raw report content is not modified by template
  exports.

## Export Profiles

Use the existing profile export route:

- `profile=client_monitoring_template&format=xlsx`
- `profile=operator_monitoring_template&format=xlsx`

The workbook contains a primary sheet named `Monitoring Template` and
supporting sheets for stop/change criteria, canaries, operator trace views,
metadata, and a downloaded `Review Log` worksheet.

## Column Definitions

Shared client/operator columns:

- `Metric / signal`: the metric, signal, gate, canary, or checkpoint to watch.
- `Decision or hypothesis validated`: the decision, gate, strategy, or
  hypothesis the row supports.
- `Owner / role`: named role or `Operator to define` when absent.
- `Cadence`: review cadence or `Operator to define` when absent.
- `Source / evidence source`: client-safe source label or unavailable-source
  placeholder.
- `Target / good sign`: positive signal or `Validation required`.
- `Warning sign`: warning signal or `Validation required`.
- `Stop/change-course threshold`: gate threshold or
  `Threshold not yet confirmed`.
- `Action if triggered`: operational response to review, pause, escalate, or
  change course.
- `Evidence maturity / validation status`: current evidence maturity and
  validation requirement.
- `Notes`: concise operational context.

Operator-only columns:

- `Row source`: deterministic projection source.
- `Hypothesis IDs`: linked hypothesis IDs when available.
- `Evidence IDs`: linked evidence IDs when available.
- `Internal source refs`: source refs useful for trace/debug review.
- `Diagnostic notes`: source-path notes such as monitor row origin.

Review Log columns:

- `Review date`
- `Reviewer`
- `Review decision / status`
- `Hypothesis / experiment`
- `Signal / metric`
- `Last observed value`
- `Observation date`
- `Evidence / note`
- `Change made`
- `Follow-up owner`
- `Next review date`
- `Follow-up due date`
- `Notes`

## Decision Gates Relationship

Decision Gates remain the source of truth for proceed, extend, stop, and
escalation choices. Monitoring templates project clear Decision Gates first when
they are available. OODA rows, canaries, and circuit breakers are implementation
controls and should not be treated as a second threshold system.

When a Decision Gates section is ambiguous, the template uses safe placeholders
rather than guessing a threshold.

## Spreadsheet Safety

Every string cell is checked before export. Values beginning with `=`, `+`, `-`,
or `@` are prefixed so spreadsheet applications open them as text rather than
formulas.

Client templates also redact raw evidence IDs, `knowledge_...` IDs, upload or
storage refs, source refs, local paths, secrets, and internal diagnostic labels.
Operator templates may retain useful trace fields after redaction of secrets and
local paths.

## Post-Review Use

1. Export the client template for a clean tracking sheet.
2. Export the operator template when traceability is needed.
3. Assign missing owners, cadences, and evidence sources.
4. Confirm thresholds before treating them as approved gates.
5. Use the Review Log worksheet as an operator-controlled tracking artifact.
6. Review the template during the planned OODA cadence.

## Limitations

- no automatic business-truth validation,
- no persisted dashboard review log,
- no semantic proof for every claim,
- no guarantee of causal truth,
- no guarantee that generated thresholds are validated,
- no replacement for operator/client judgment,
- no public SaaS readiness, auth, multi-tenancy, or tenant isolation.

Human review remains required before acting on recommendations or thresholds,
especially where legal, financial, medical, safety, compliance, or public claims
are involved.
