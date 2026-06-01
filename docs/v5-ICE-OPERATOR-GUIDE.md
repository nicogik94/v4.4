# v5 ICE Operator Guide

ICE in this repo means the deterministic clarification workflow: the operator can generate missing-information questions, answer them, mark answers unavailable, and review the saved clarification state before relying on downstream outputs.

This is operator review support. It is not a chatbot, not a new reasoning phase, not access control, not output-level secrecy, and not workflow routing.

## When To Use It

Use ICE before or after a run when the brief may be missing decision-critical information:

- deadline or time window
- success metric or target outcome
- alternatives being compared
- hard constraints or dependencies
- stakeholder or decision owner
- evidence/source material
- monitoring or kill criteria
- budget, resource, or capacity limits

The questions are generated deterministically from the stored project context. They do not call an LLM and do not feed prompts by themselves.

## Operator Workflow

1. Open the project in `dashboards/index.html`.
2. In the Follow-up questions panel, click **Generate questions**.
3. Answer each critical/high question when the answer is known.
4. Use **Mark unavailable** only when the operator cannot supply the answer.
5. Review the saved answer rows and answer previews.
6. If the panel says an answer affects a completed phase, rerun or review the affected phase/report before treating the output as final.

ICE answers are stored as project context for operator review. They do not automatically rerun phases, invalidate outputs, rewrite reports, or change gates.

## Reading The Summary

The dashboard and API expose a derived summary:

- `total` is all generated clarification questions.
- `open` is unanswered questions still active.
- `required open` is open critical/high work.
- `answered` and `unavailable` show operator disposition.
- `resolved` is answered/unavailable divided by active non-superseded questions.
- `latest cycle status` distinguishes no cycle, no questions, required open, optional open, resolved, and superseded states.
- `next action` is deterministic guidance for the operator.

If `required open` is nonzero, treat the report as internal review material until those questions are answered or explicitly marked unavailable and the affected output has been reviewed.

## Review And Refresh

ICE does not hold, reopen, or refresh phases automatically. When an answer is saved after an affected phase has already completed, the summary marks that phase as a refresh candidate. The operator decides whether to rerun or manually review the affected output.

Typical next actions:

- `Answer critical/high clarification questions before regenerating or sharing outputs.`
- `Answer remaining clarification questions or mark them unavailable.`
- `Review saved clarification answers and rerun affected phase(s): ...`
- `Review saved clarification answers before final delivery.`

These labels are guidance only. They do not change workflow status.

## Boundaries

ICE deliberately stays narrow in this wave:

- no chatbot UI
- no output-level secrecy or compartmentalization
- no new auth, tenancy, or public deployment control
- no new LLM prompt or phase
- no report wording rewrite
- no machine archive schema change
- no monitoring template change
- no automatic workflow routing

Human review remains required before sharing client-facing outputs.
