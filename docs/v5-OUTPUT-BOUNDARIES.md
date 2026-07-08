# v5 Output Boundaries

This guide defines which exports are intended for client review, operator review, and internal machine retention. These boundaries are deterministic export filters. They are not authentication, authorization, tenancy, encryption, or deployment isolation.

Human review remains required before any client delivery.

## Profile Matrix

| Profile | Formats | Intended audience | Boundary |
|---|---:|---|---|
| `report` | DOCX, PDF | Client-safe after human review | Final report only, with client-facing cleanup and operator/runtime diagnostics removed. |
| `decision_memo_pilot_plan` | DOCX, PDF | Client-safe after human review | Available only when `report_output_mode=decision_memo_pilot_plan`; exports the stored decision memo with the technical appendix separated. |
| `client_dossier` | DOCX, PDF | Client-safe after human review | Structured client dossier with client-safe metadata, open client-visible clarification questions, and client-safe evidence wording. |
| `client_monitoring_template` | XLSX | Client-safe after human review | Monitoring worksheet without operator trace columns or explicit internal/runtime/operator-only metadata. |
| `operator_dossier` | DOCX, PDF | Operator-only | Preserves operator diagnostics after unsafe string redaction, including quality warnings, policy summaries, trace summaries, and freshness metadata. |
| `operator_monitoring_template` | XLSX | Operator-only | Includes the client monitoring columns plus operator trace columns for evidence IDs, internal source refs, row source, and diagnostic notes. |
| `machine_archive` | ZIP | Internal machine archive | Sanitized archive for backup/debug. Its schema is intentionally stable and not a client deliverable. |

## Client-Safe Profiles

The client-safe profiles are:

- `report`
- `decision_memo_pilot_plan`
- `client_dossier`
- `client_monitoring_template`

These profiles are designed to remove explicit operator-only and runtime metadata while preserving useful decision content. They still require review by a human operator before sharing.

Client outputs may include:

- recommendation and rationale text
- client-safe caveats and validation reminders
- client-visible open clarification questions
- monitoring signals, owners, cadence, thresholds, and actions after client cleanup
- evidence maturity and source-locator status phrased for review

## Output Configuration Metadata

Projects support `output_language` (`en`, `es-MX`) and `report_mode`
(`standard`, `decision_memo_pilot_plan`). Existing projects and callers default
to `en` and `standard`.

Generated reports also store `report_output_language` and
`report_output_mode`. Export and dashboard behavior that depends on the report
mode uses the generated metadata, not the current desired configuration. If a
project setting changes after report generation, the old report is not
re-labeled; the dashboard should show that a report rerun is required for the
new setting to affect the report.

## Operator-Only Profiles

The operator-only profiles are:

- `operator_dossier`
- `operator_monitoring_template`

These profiles are for internal review. They may include policy summaries, freshness metadata, quality warnings, trace columns, evidence IDs, source refs after unsafe string redaction, and other operator diagnostics needed to review whether a client deliverable is ready.

Do not send operator-only profiles to clients.

## Internal Machine Archive

`machine_archive` is an internal ZIP archive. It is meant for machine-readable retention, backup, debug, and reproducibility workflows. It is not a polished report and is not client-facing.

The archive may include sanitized files such as:

- `project_state.json`
- `phase_outputs.json`
- `decision_objects.json`
- `clarifications.json`
- `evidence_locator_register.json`
- `uploaded_file_manifest.json`
- `policy_summary.json`
- `export_manifest.json`

Do not change the archive schema as part of client output filtering work.

## Must Not Be Sent To Clients

Do not send client-facing outputs that expose:

- constraint adherence warnings intended for operators
- telemetry privacy notes intended for operators
- `policy_audit_log`
- `raw_provider_payload`
- raw prompts or `raw_prompt`
- `project_state.json`
- `machine_archive` internals
- runtime or preflight metadata
- local filesystem paths, upload-store paths, or storage refs
- raw provider tokens, API keys, credentials, secrets, or chain-of-thought-like scratchpad content
- operator-only clarification questions or answers marked restricted, operator, internal, sensitive, confidential, or `client_visible=false`

If a client-safe profile still contains one of these items, treat that as a boundary bug and do not deliver the artifact until it is corrected.

## Human Review Reminder

Client-safe means the export path applies deterministic cleanup for the intended audience. It does not mean the output is correct, complete, legally approved, confidential by access control, or safe to forward without review.

Before sending any client-facing artifact:

1. Use a client-safe profile.
2. Review the content for correctness and omitted context.
3. Resolve or explicitly mark unavailable critical/high clarifications.
4. Confirm report freshness and evidence maturity warnings.
5. Keep the machine archive and operator-only exports internal.
