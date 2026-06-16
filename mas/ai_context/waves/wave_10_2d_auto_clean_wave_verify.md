# Wave 10.2D — Auto-clean known test artifacts after wave verification

## Goal

Make `scripts/wave_verify.sh` automatically clean known tracked test artifacts after verification, especially `scenario_shadow.sqlite3`, so the commit flow is not interrupted after a green test run.

## Problem

`scripts/wave_verify.sh` currently detects that `scenario_shadow.sqlite3` changed and tells the operator to run `scripts/wave_clean_artifacts.sh`, but it does not run cleanup itself.

## Required behavior

1. `scripts/wave_verify.sh` must still run:
   - `git diff --check`
   - optional targeted pytest paths
   - the full pytest suite
2. After verification finishes, it must run `scripts/wave_clean_artifacts.sh` automatically.
3. Cleanup must run even if targeted tests or the full suite fail.
4. The script must preserve the original verification exit code when tests fail.
5. If tests pass but cleanup fails, the script should fail.
6. Final output must show the git status after cleanup.
7. Do not delete untracked files.
8. Do not run `git clean`.
9. Do not commit, push, merge, or open PR automatically.
10. Do not change application/runtime/auth/exporter behavior.

## Allowed files

Prefer only:

- `scripts/wave_verify.sh`
- `ai_context/waves/wave_10_2d_auto_clean_wave_verify.md`

Only touch another file if absolutely necessary, and stop/report before doing so.

## Forbidden files

- `api.py`
- `config.py`
- `runtime/preflight.py`
- exporter files
- prompt files
- model routing files
- GitHub workflow files
- Docker Compose files
- generated exports
- `scenario_shadow.sqlite3`
- `upload_store/`
- `__pycache__/`
- `.pyc`
- `.zip`

## Required verification

Run:

- `bash -n scripts/wave_verify.sh scripts/wave_clean_artifacts.sh`
- `scripts/wave_verify.sh tests/test_runtime_preflight.py tests/test_operator_auth.py`
- `git status --short`

Expected final status after verification should not include `scenario_shadow.sqlite3`.

## Human review checklist

- Cleanup runs automatically after verification.
- Cleanup runs on both success and failure paths.
- Original test failure code is preserved.
- Known artifact cleanup remains limited to `scripts/wave_clean_artifacts.sh`.
- No untracked files are deleted.
- No app/runtime/exporter behavior changes.
