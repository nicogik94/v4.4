#!/usr/bin/env bash
set -euo pipefail

section() {
  printf '\n== wave_verify: %s ==\n' "$1"
}

info() {
  printf 'wave_verify: %s\n' "$1"
}

scenario_shadow_changed() {
  local path

  while IFS= read -r path; do
    if [[ "$path" == */scenario_shadow.sqlite3 || "$path" == "scenario_shadow.sqlite3" ]]; then
      if ! git diff --quiet -- "$path" || ! git diff --cached --quiet -- "$path"; then
        return 0
      fi
    fi
  done < <(git ls-files)

  return 1
}

cleanup_and_report_final_status() {
  local verification_exit=$?
  local cleanup_exit=0
  trap - EXIT
  set +e

  section "artifact cleanup"
  info "running scripts/wave_clean_artifacts.sh"
  scripts/wave_clean_artifacts.sh
  cleanup_exit=$?

  if (( cleanup_exit != 0 )); then
    echo "wave_verify: artifact cleanup failed with exit code $cleanup_exit." >&2
  else
    info "artifact cleanup passed"
  fi

  section "final git status"
  git status --short || true

  if scenario_shadow_changed; then
    echo "wave_verify: scenario_shadow.sqlite3 remains changed after cleanup; inspect scripts/wave_clean_artifacts.sh output before committing." >&2
  fi

  section "final result"
  info "verification exit code: $verification_exit"
  info "cleanup exit code: $cleanup_exit"

  if (( verification_exit != 0 )); then
    echo "wave_verify: verification failed; rerun the failing command shown above, then rerun scripts/wave_verify.sh." >&2
    exit "$verification_exit"
  fi

  if (( cleanup_exit != 0 )); then
    echo "wave_verify: cleanup failed after verification passed; inspect artifact cleanup output above." >&2
    exit "$cleanup_exit"
  fi

  info "verification and cleanup passed"
  exit 0
}

trap cleanup_and_report_final_status EXIT

section "environment"
info "working directory: $(pwd)"
info "branch: $(git branch --show-current 2>/dev/null || printf 'unknown')"

section "diff check"
info "rerun: git diff --check"
git diff --check

section "targeted pytest"
if (( $# > 0 )); then
  for target in "$@"; do
    printf -v quoted_target '%q' "$target"
    info "target: $target"
    info "rerun: PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest $quoted_target -q"
    PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest "$target" -q
  done
else
  info "no targeted pytest paths provided"
fi

section "full pytest"
info "rerun: PYTHONDONTWRITEBYTECODE=1 timeout 300s .venv/bin/python -m pytest tests -q"
PYTHONDONTWRITEBYTECODE=1 timeout 300s .venv/bin/python -m pytest tests -q
