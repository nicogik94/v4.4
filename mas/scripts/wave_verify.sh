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

report_final_status() {
  local exit_code=$?
  trap - EXIT

  echo "Final git status:"
  git status --short || true

  if scenario_shadow_changed; then
    echo "scenario_shadow.sqlite3 changed; run scripts/wave_clean_artifacts.sh before committing." >&2
  fi

  exit "$exit_code"
}

trap report_final_status EXIT

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
