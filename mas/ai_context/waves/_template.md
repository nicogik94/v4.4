# Wave Name

TBD

## Goal

TBD

## Non-goals

- TBD

## Allowed Files

- TBD

## Forbidden Files

- TBD

## Stop Conditions

- Stop if the current branch is not the expected wave branch.
- Stop if the preflight working tree is not clean.
- Stop if implementation requires touching forbidden files.
- Stop if required tests cannot be run or fail for reasons unrelated to known local environment limits.

## Required Tests

- `git diff --check`
- Targeted pytest paths: TBD
- `PYTHONDONTWRITEBYTECODE=1 timeout 300s .venv/bin/python -m pytest tests -q`

## Implementation Notes

TBD

## Expected PR Body

### Summary

- TBD

### Tests

- TBD

### Risk

- TBD

## Rollback Plan

TBD

## Human Review Checklist

- Scope matches the wave goal.
- No forbidden files changed.
- No unrelated cleanup or broad refactors.
- Known test artifacts are not committed.
- Targeted and full verification results are included in the PR.
