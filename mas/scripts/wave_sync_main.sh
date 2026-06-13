#!/usr/bin/env bash
set -euo pipefail

if [[ -n "$(git status --short)" ]]; then
  echo "Refusing to switch branches with a dirty working tree:" >&2
  git status --short >&2
  exit 1
fi

git switch main
git pull --ff-only origin main

echo "Status after syncing main:"
git status --short

echo "Recent history:"
git log --oneline -5
