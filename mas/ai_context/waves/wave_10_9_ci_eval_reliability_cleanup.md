# Wave 10.9 — CI/eval reliability cleanup

## Goal

Improve CI/eval reliability and failure visibility without changing MAS runtime behavior or weakening verification.

This wave should make failures easier to understand, reproduce, and triage.

## Scope

Small reliability/observability cleanup for test/eval automation.

Target areas:

1. CI test summary clarity.
2. Eval output aggregation clarity, if eval runners exist.
3. Failure diagnostics.
4. Local/CI parity where practical.
5. Documentation or script guardrails if useful.

## Desired outcome

Operators should more easily answer:

- what failed
- where it failed
- whether it was pytest, evals, artifact cleanup, or environment setup
- what command to rerun locally
- whether a known eval exception is being documented or hidden

## Constraints

Do not change app behavior.

Do not change:

- auth behavior
- API endpoint contracts
- runtime/preflight behavior
- queue/job execution
- database schema
- Docker files
- deployment posture
- public exposure posture
- product output wording
- prompt behavior

Do not weaken tests.

Do not hide failing tests/evals.

Do not convert real failures into success.

Do not remove meaningful checks.

Do not broaden scope into product/output changes.

Do not modify generated artifacts, `upload_store/`, `scenario_shadow.sqlite3`, `__pycache__/`, `.pyc`, `.zip`, or local Docker Compose edits.

Do not run `git clean`.

Do not update `ai_context/v4_v5_current_progress.md` in this wave.

Do not commit, push, open PR, or merge.

## Candidate files to inspect

First run `git rev-parse --show-toplevel` and treat that as the repo root.

Inspect before editing:

- `.github/workflows/`, from the repo root if present
- `scripts/wave_verify.sh`
- `scripts/wave_clean_artifacts.sh`
- eval-related files discovered with `git ls-files | grep -Ei '(^|/)(eval|evals|evaluation)'`
- CI/test runner files discovered with `git ls-files | grep -Ei '(pytest|workflow|ci|verify|clean_artifacts)'`
- directly relevant tests under `tests/`

If no eval runner or eval directory exists, do not invent one. Report that no eval runner was found and focus on CI/test/script diagnostics.

Only edit files that directly support CI/eval reliability.

## Required guardrails

Any script summary/trap logic must preserve the original failing exit code.

Do not add `continue-on-error` to required verification jobs.

Do not suppress required failures with `|| true`.

Do not remove assertions.

Do not deselect failing tests.

Do not shrink pytest scope in CI unless the original full verification remains intact elsewhere.

Do not replace a failing gate with a warning-only summary.

## Verification

Run:

- `git diff --check`
- relevant targeted checks for changed scripts/evals
- `bash -n scripts/wave_verify.sh scripts/wave_clean_artifacts.sh` if shell scripts changed
- `scripts/wave_verify.sh`
- `git status --short --untracked-files=all`

## Completion criteria

- Failures are easier to diagnose.
- Local rerun command is clear.
- CI output remains honest.
- Tests/evals are not weakened.
- Original failing exit codes are preserved if scripts are changed.
- Full verification passes.
- No forbidden artifacts remain changed.
