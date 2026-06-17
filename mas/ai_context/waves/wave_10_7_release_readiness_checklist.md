# Wave 10.7 — Release readiness checklist

## Goal

Add a local/operator-first release readiness checklist that tells an operator what must be verified before treating the current repo state as ready for a local demo, internal release, or any future deployment review.

## Scope

Documentation-first.

Expected deliverable:

- `docs/runbooks/release_readiness_checklist.md`

Optional:

- update `ai_context/v4_v5_current_progress.md` only if needed after completion

## Required coverage

The checklist should cover:

1. Clean Git state.
2. Current main commit.
3. Local runtime startup.
4. Health check.
5. Runtime preflight.
6. Operator auth posture.
7. Public exposure interlock.
8. Full test verification.
9. Artifact cleanup.
10. No forbidden files staged.
11. Human review boundaries.
12. Explicit “not public SaaS / not multi-tenant” posture.
13. Go / no-go release decision.

## Constraints

Do not change app behavior.

Do not change auth behavior.

Do not change API endpoints.

Do not modify:

- `api.py`
- `config.py`
- `runtime/preflight.py`
- tests
- exporters
- prompts
- Docker files
- GitHub workflows
- generated artifacts

Do not print raw `MAS_OPERATOR_API_KEY`.

Do not use `set -x`.

Do not run `git clean`.

Keep it local/operator-first.
