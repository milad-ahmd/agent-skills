#!/usr/bin/env bash
# Collects the diff to review.
#   collect_diff.sh          -> current branch vs default branch (local)
#   collect_diff.sh <PR#>    -> GitHub PR metadata + diff via gh CLI
set -euo pipefail

if [[ $# -ge 1 ]]; then
  pr="$1"
  command -v gh >/dev/null || { echo "gh CLI is required for PR mode: https://cli.github.com" >&2; exit 1; }
  echo "===== PR METADATA ====="
  gh pr view "$pr" --json number,title,body,author,baseRefName,headRefName,additions,deletions,changedFiles \
    --template '#{{.number}} {{.title}} by {{.author.login}} ({{.headRefName}} -> {{.baseRefName}})
+{{.additions}} -{{.deletions}} in {{.changedFiles}} files

{{.body}}
'
  echo "===== DIFF ====="
  gh pr diff "$pr"
  exit 0
fi

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "Not a git repository" >&2; exit 1; }

base="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"
if [[ -z "$base" ]]; then
  for b in origin/main origin/master main master; do
    git rev-parse --verify --quiet "$b" >/dev/null && { base="$b"; break; }
  done
fi
[[ -n "$base" ]] || { echo "Could not determine base branch" >&2; exit 1; }

current="$(git rev-parse --abbrev-ref HEAD)"
merge_base="$(git merge-base "$base" HEAD)"

echo "===== BRANCH: $current vs $base ====="
echo "===== COMMITS ====="
git --no-pager log --oneline "$merge_base"..HEAD
echo "===== FILES ====="
git --no-pager diff --stat "$merge_base"
echo "===== DIFF (committed + uncommitted) ====="
git --no-pager diff "$merge_base"
