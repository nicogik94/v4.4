# Wave 10.8 — Output, provenance, Technology Readiness, and monitoring polish

## Goal

Improve the clarity, safety, and operator/client separation of generated outputs and export surfaces without changing core workflow behavior.

This wave should make MAS outputs easier to review, easier to trust, and harder to overclaim.

## Scope

Controlled polish bundle.

Target areas:

1. Export/provenance wording.
2. Operator/client boundary language.
3. Technology Readiness output clarity and caveats.
4. Monitoring template/report clarity.
5. Tests only where output expectations change.

## Desired outcome

Outputs should more clearly communicate:

- what is evidence-backed vs model-generated judgment
- what requires human review
- what should not be treated as public SaaS / autonomous action / fully proven claim
- what monitoring signals, canaries, circuit breakers, and owner actions mean
- what Technology Readiness results can and cannot support

## Constraints

Do not change app behavior unless strictly required for presentation/output polish.

Do not change:

- auth behavior
- API endpoint contracts
- runtime/preflight behavior
- queue/job execution
- database schema
- Docker files
- GitHub workflows
- deployment posture
- security posture
- public exposure posture

Avoid broad refactors.

Do not turn MAS into a chatbot, BI tool, public SaaS, multi-tenant product, or autonomous action system.

Do not update `ai_context/v4_v5_current_progress.md` in this wave unless explicitly requested after merge.

Do not commit, push, open PR, or merge.

## Candidate files to inspect

Inspect before editing:

- `exporters.py`
- `report_quality.py`
- `orchestrator.py`
- `state.py`
- `tools/technology_readiness.py`
- `prompts/technology_readiness/`
- `docs/runbooks/release_readiness_checklist.md`
- `docs/runbooks/operator_auth_smoke_runbook.md`
- relevant tests under `tests/`

Only edit files that directly support the polish goal.

## Verification

Run focused tests for touched areas, then full verification:

- `git diff --check`
- targeted pytest for changed output/export/technology-readiness/monitoring areas
- `scripts/wave_verify.sh`
- `git status --short --untracked-files=all`

## Completion criteria

- Output language is clearer and less overclaiming.
- Operator-only vs client-visible wording is preserved or improved.
- Technology Readiness output avoids unsupported certainty.
- Monitoring output is easier for an operator to act on.
- Existing tests pass.
- No forbidden artifacts remain changed.
