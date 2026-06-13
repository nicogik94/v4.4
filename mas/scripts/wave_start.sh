#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: scripts/wave_start.sh <branch-name>" >&2
}

if [[ $# -ne 1 ]]; then
  usage
  exit 2
fi

branch_name="$1"

if [[ -z "$branch_name" || "$branch_name" =~ [[:space:]] ]]; then
  echo "Refusing branch names that are empty or contain whitespace." >&2
  exit 1
fi

if ! git check-ref-format --branch "$branch_name" >/dev/null 2>&1; then
  echo "Refusing invalid branch name: $branch_name" >&2
  exit 1
fi

if git show-ref --verify --quiet "refs/heads/$branch_name"; then
  echo "Refusing to overwrite existing local branch: $branch_name" >&2
  exit 1
fi

if [[ -n "$(git status --short)" ]]; then
  echo "Refusing to start a wave with a dirty working tree:" >&2
  git status --short >&2
  exit 1
fi

git switch main
git pull --ff-only origin main
git switch -c "$branch_name"

echo "Current branch:"
git branch --show-current

echo "Status after branch creation:"
git status --short

echo "Recent history:"
git log --oneline -5
