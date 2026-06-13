#!/usr/bin/env bash
set -euo pipefail

known_artifact_paths() {
  git ls-files | awk '/(^|\/)scenario_shadow\.sqlite3$/ || /(^|\/)__pycache__\/.*\.pyc$/ { print }'
}

echo "Status before artifact cleanup:"
git status --short

mapfile -t artifact_paths < <(known_artifact_paths)
restore_paths=()

for path in "${artifact_paths[@]}"; do
  if ! git diff --quiet -- "$path" || ! git diff --cached --quiet -- "$path"; then
    restore_paths+=("$path")
  fi
done

if (( ${#restore_paths[@]} > 0 )); then
  echo "Restoring known tracked test artifacts:"
  printf '  %s\n' "${restore_paths[@]}"
  git restore --staged --worktree -- "${restore_paths[@]}"
else
  echo "No modified tracked test artifacts found."
fi

echo "Status after artifact cleanup:"
git status --short
