---
name: mas-r1-validate
description: Explicit-only validation workflow for bounded MAS research-evidence changes using safe local checks and explicitly authorized disposable PostgreSQL databases.
---

# MAS R1 Validation

## Purpose and non-goals

- Validate an explicitly identified research-evidence change.
- Never design, edit, repair, commit, push, create PRs, change PR state, merge, or start a new wave.
- Treat validation as evidence collection, not approval authority.

## Required invocation inputs

Require all of the following before validation:

- Active wave and branch.
- Exact changed-file allowlist.
- Validation target, such as a v54 migration or named repository/service change.
- Approved test commands or an explicitly approved test-selection rule.
- Explicit authorization for Docker and PostgreSQL if either is needed.
- Whether a disposable database must be created and dropped.

If any material input is missing, return `HOLD` and perform no execution.

## Validation levels

- `STATIC`: Use read-only Git diff, changed-file allowlist, whitespace checks, and targeted static tests only.
- `LOCAL_TESTS`: Run approved Python test commands without Docker or PostgreSQL changes.
- `POSTGRESQL`: Run only when Docker/database execution is explicitly authorized.

Do not escalate from one level to another without explicit authorization.

## PostgreSQL safety contract

For `POSTGRESQL` validation:

- Work only from `/home/nicolas/dev/v4.4/mas`.
- Generate a new disposable database name prefixed exactly `mas_research_evidence_test_`.
- Reject any database named `workflow_v4`.
- Verify the target database name before creating, migrating, testing, and dropping it.
- Use only a temporary test DSN pointing to that disposable database.
- Never apply migrations to `workflow_v4`.
- Never run an automatic migration runner.
- Never run `docker compose down -v`.
- Always report whether disposable database cleanup completed.
- If setup, test execution, or cleanup fails, report `FAIL` or `SKIPPED` accurately and stop instead of retrying destructive operations.
- Never claim cleanup completed without command output confirming it.

## Required checks

- Enforce the approved changed-file allowlist before and after validation.
- Run `git diff --check` whenever tracked changes exist.
- Run only approved test commands.
- For SQL changes with authorized PostgreSQL validation, require:
  - Focused PostgreSQL schema, repository, and service tests relevant to the active migration.
  - The complete relevant `test_research_evidence_*.py` regression suite.
  - Migration reapply/drift checks when those tests exist.
- Do not call an unrun test `PASS`.
- Use only `PASS`, `FAIL`, `SKIPPED`, or `NOT_APPLICABLE` for verification status.

## Output contract

Report:

- Active wave, validation level, branch, and target.
- Changed-file allowlist result before and after validation.
- Each command run, summarized result, and status.
- Disposable database name only when PostgreSQL is authorized.
- Cleanup status.
- Unresolved risks and skipped checks.
- Final gate: `SHIP`, `ITERATE`, or `HOLD`.

Use `SHIP` only when all required authorized checks passed, file scope is clean, and cleanup is confirmed where applicable.

## Permanent boundaries

- Never alter v47 through v54.
- Never add a migration runner.
- Never add API, UI, reports, exports, retrieval, citations, calculations, scenarios, workflows, monitoring, prompts, or downstream consumers.
- Never duplicate canonical availability or retention logic.
- Keep approval, freshness, drift, claim support, citation readiness, calculation eligibility, and downstream use as separate contracts.
- Never inspect, enumerate, stage, modify, restore, clean, copy, delete, or otherwise touch `mas/upload_store/` or `mas/scenario_shadow.sqlite3`.
- Never read or expose `.env` values.
- Never run `git clean`, destructive Git reset, or a migration against a non-disposable database.
