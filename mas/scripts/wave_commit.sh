#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo 'Usage: scripts/wave_commit.sh "<commit message>" <file1> [file2 ...]' >&2
}

if (( $# < 2 )); then
  usage
  exit 2
fi

commit_message="$1"
shift

if [[ -z "$commit_message" ]]; then
  echo "Refusing an empty commit message." >&2
  exit 1
fi

current_branch="$(git branch --show-current)"

if [[ -z "$current_branch" || "$current_branch" == "main" ]]; then
  echo "Refusing to commit from main or a detached HEAD." >&2
  exit 1
fi

if ! git diff --cached --quiet; then
  echo "Refusing to stage files because the index already has staged changes:" >&2
  git diff --cached --name-only >&2
  exit 1
fi

for path in "$@"; do
  if [[ -z "$path" ]]; then
    echo "Refusing an empty file path." >&2
    exit 1
  fi

  if [[ -d "$path" ]]; then
    echo "Refusing directory path; pass explicit files only: $path" >&2
    exit 1
  fi

  case "$path" in
    *[\*\?\[]*)
      echo "Refusing pathspec metacharacters; pass explicit files only: $path" >&2
      exit 1
      ;;
    scenario_shadow.sqlite3|*/scenario_shadow.sqlite3|docker-compose.yml|*/docker-compose.yml|upload_store|upload_store/*|*/upload_store|*/upload_store/*|exports|exports/*|*/exports|*/exports/*|*__pycache__*|*.sqlite|*.sqlite3|*.pyc|*.zip)
      echo "Refusing protected artifact or infrastructure path: $path" >&2
      exit 1
      ;;
  esac
done

git add -- "$@"

if git diff --cached --quiet; then
  echo "No changes staged from the explicit file paths." >&2
  exit 1
fi

echo "Staged files:"
git diff --cached --name-only

echo "Staged diff stat:"
git diff --cached --stat

git commit -m "$commit_message"
