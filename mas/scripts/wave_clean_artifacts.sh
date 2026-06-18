#!/usr/bin/env bash
set -euo pipefail

section() {
  printf '\n== wave_clean_artifacts: %s ==\n' "$1"
}

info() {
  printf 'wave_clean_artifacts: %s\n' "$1"
}

known_artifact_paths() {
  git ls-files | while IFS= read -r path; do
    case "$path" in
      scenario_shadow.sqlite3|*/scenario_shadow.sqlite3|*/__pycache__/*.pyc)
        printf '%s\n' "$path"
        ;;
    esac
  done
}

section "status before cleanup"
git status --short

section "artifact discovery"
mapfile -t artifact_paths < <(known_artifact_paths)
restore_paths=()
info "tracked artifact paths checked: ${#artifact_paths[@]}"

for path in "${artifact_paths[@]}"; do
  if ! git diff --quiet -- "$path" || ! git diff --cached --quiet -- "$path"; then
    restore_paths+=("$path")
  fi
done

section "artifact restore"
if (( ${#restore_paths[@]} > 0 )); then
  info "restoring modified tracked test artifacts: ${#restore_paths[@]}"
  printf '  %s\n' "${restore_paths[@]}"
  git restore --staged --worktree -- "${restore_paths[@]}"
else
  info "no modified tracked test artifacts found"
fi

section "status after cleanup"
git status --short
