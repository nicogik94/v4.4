# Wave Implementation Runbook

A wave is a tightly scoped implementation pass with a named goal, explicit allowed files, explicit forbidden files, required verification, and a human-reviewed PR. Waves keep repeated Codex implementation work predictable by separating the requested change from unrelated cleanup or opportunistic refactors.

## Required Lifecycle

1. Sync main.
2. Create a wave branch.
3. Run Codex from the wave spec.
4. Verify targeted tests and the full test suite.
5. Clean known local test artifacts.
6. Commit only explicit intended files.
7. Open a PR.
8. Complete human review.
9. Merge after review.
10. Sync main after merge.

The helper scripts in `scripts/wave_*.sh` automate the Git and verification steps while refusing broad or unsafe operations.

## Forbidden Default Behavior

- No broad refactors.
- No unrelated cleanup.
- No committing test artifacts.
- No merging without review.

## Current Recommended Wave Order

1. 10.3 auth/control-plane hardening.
2. 10.4 durable job/lock layer if still needed.
3. 10.5 ingestion/versioned contracts and observability cleanup.
