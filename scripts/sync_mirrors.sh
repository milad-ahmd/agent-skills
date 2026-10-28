#!/usr/bin/env bash
# Sync skills/<name>/ from this repo (the source of truth) into each per-skill mirror repo.
#
# Usage: scripts/sync_mirrors.sh [--dry-run] [skill ...]     (no skill names = all skills)
# Env:   MIRRORS_DIR  where mirror clones live (default: the folder containing this repo)
#        REMOTE_BASE  where to clone missing mirrors from (default: https://github.com/milad-ahmd)
#
# Only the skills/<name>/ folder is synced; each mirror's README, LICENSE and assets stay as they are.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MIRRORS_DIR="${MIRRORS_DIR:-$(dirname "$ROOT")}"
REMOTE_BASE="${REMOTE_BASE:-https://github.com/milad-ahmd}"
EXCLUDES=(-x __pycache__ -x '*.pyc' -x .DS_Store)

DRY=0
NAMES=()
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) NAMES+=("$a") ;;
  esac
done
if [[ ${#NAMES[@]} -eq 0 ]]; then
  while IFS= read -r n; do NAMES+=("$n"); done < <(ls "$ROOT/skills")
fi

if [[ -n "$(git -C "$ROOT" status --porcelain -- skills)" ]]; then
  echo "✗ skills/ has uncommitted changes in $ROOT. Commit and push the main repo first." >&2
  exit 1
fi
SRC_SHA="$(git -C "$ROOT" rev-parse HEAD)"
GIT_NAME="$(git -C "$ROOT" config user.name || echo 'Milad Ahmadi')"
GIT_EMAIL="$(git -C "$ROOT" config user.email || echo 'miladahmadi803@gmail.com')"

checked=0 outdated=0 synced=0 failed=0
for n in "${NAMES[@]}"; do
  src="$ROOT/skills/$n"
  m="$MIRRORS_DIR/$n"
  if [[ ! -f "$src/SKILL.md" ]]; then
    echo "✗ $n: no such skill in $ROOT/skills" >&2; failed=$((failed + 1)); continue
  fi
  if [[ ! -d "$m/.git" ]]; then
    if [[ $DRY -eq 1 ]]; then echo "• $n: mirror not cloned yet (would clone)"; continue; fi
    git clone -q "$REMOTE_BASE/$n.git" "$m" || { echo "✗ $n: clone failed" >&2; failed=$((failed + 1)); continue; }
  fi
  if ! git -C "$m" pull -q --rebase; then
    echo "✗ $n: pull failed (local changes or conflict in $m)" >&2; failed=$((failed + 1)); continue
  fi
  checked=$((checked + 1))
  if diff -rq "${EXCLUDES[@]}" "$src" "$m/skills/$n" >/dev/null 2>&1; then
    continue
  fi
  outdated=$((outdated + 1))
  if [[ $DRY -eq 1 ]]; then
    echo "• $n: out of date"
    diff -rq "${EXCLUDES[@]}" "$src" "$m/skills/$n" 2>&1 | sed "s|$ROOT/||; s|$MIRRORS_DIR/||; s|^|    |" || true
    continue
  fi
  rm -rf "$m/skills/$n"
  mkdir -p "$m/skills"
  cp -R "$src" "$m/skills/$n"
  find "$m/skills/$n" \( -name __pycache__ -prune -exec rm -rf {} + \) -o \( \( -name '*.pyc' -o -name .DS_Store \) -delete \)
  grep -qx '__pycache__/' "$m/.gitignore" 2>/dev/null || printf '__pycache__/\n*.pyc\n' >> "$m/.gitignore"
  git -C "$m" add -A
  if git -C "$m" -c user.name="$GIT_NAME" -c user.email="$GIT_EMAIL" commit -q \
       -m "sync from agent-skills@${SRC_SHA:0:7}" \
       -m "Source: https://github.com/milad-ahmd/agent-skills/tree/$SRC_SHA/skills/$n" \
     && git -C "$m" push -q; then
    echo "✓ $n: synced"; synced=$((synced + 1))
  else
    echo "✗ $n: commit or push failed" >&2; failed=$((failed + 1))
  fi
done

if [[ $DRY -eq 1 ]]; then
  echo "Checked $checked mirror(s): $outdated out of date, $failed error(s). (dry run, nothing changed)"
else
  echo "Checked $checked mirror(s): $synced synced, $((checked - outdated)) already up to date, $failed error(s)."
fi
[[ $failed -eq 0 ]]
