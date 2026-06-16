#!/usr/bin/env bash
set -euo pipefail

scenario_shadow_changed() {
  local path

  while IFS= read -r path; do
    if ! git diff --quiet -- "$path" || ! git diff --cached --quiet -- "$path"; then
      return 0
    fi
  done < <(git ls-files | awk '/(^|\/)scenario_shadow\.sqlite3$/ { print }')

  return 1
}

cleanup_and_report_final_status() {
  local verification_exit=$?
  local cleanup_exit=0
  trap - EXIT
  set +e

  echo "Running artifact cleanup:"
  scripts/wave_clean_artifacts.sh
  cleanup_exit=$?

  if (( cleanup_exit != 0 )); then
    echo "Artifact cleanup failed with exit code $cleanup_exit." >&2
  fi

  echo "Final git status after cleanup:"
  git status --short || true

  if scenario_shadow_changed; then
    echo "scenario_shadow.sqlite3 remains changed after cleanup; inspect scripts/wave_clean_artifacts.sh output before committing." >&2
  fi

  if (( verification_exit != 0 )); then
    exit "$verification_exit"
  fi

  exit "$cleanup_exit"
}

trap cleanup_and_report_final_status EXIT

git diff --check

if (( $# > 0 )); then
  for target in "$@"; do
    echo "Running targeted pytest: $target"
    PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest "$target" -q
  done
else
  echo "No targeted pytest paths provided."
fi

echo "Running full pytest suite:"
PYTHONDONTWRITEBYTECODE=1 timeout 300s .venv/bin/python -m pytest tests -q
