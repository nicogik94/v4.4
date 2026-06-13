#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo 'Usage: scripts/wave_pr.sh "<PR title>" <body-file>' >&2
}

if [[ $# -ne 2 ]]; then
  usage
  exit 2
fi

pr_title="$1"
body_file="$2"

if [[ -z "$pr_title" ]]; then
  echo "Refusing an empty PR title." >&2
  exit 1
fi

current_branch="$(git branch --show-current)"

if [[ -z "$current_branch" || "$current_branch" == "main" ]]; then
  echo "Refusing to create a PR from main or a detached HEAD." >&2
  exit 1
fi

if [[ -n "$(git status --short)" ]]; then
  echo "Refusing to create a PR with a dirty working tree:" >&2
  git status --short >&2
  exit 1
fi

if [[ ! -f "$body_file" ]]; then
  echo "Body file does not exist: $body_file" >&2
  exit 1
fi

git push -u origin "$current_branch"
gh pr create --base main --head "$current_branch" --title "$pr_title" --body-file "$body_file"
