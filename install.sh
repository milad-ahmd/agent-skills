#!/usr/bin/env bash
# Install milad-ahmd agent skills.
#   curl -fsSL https://raw.githubusercontent.com/milad-ahmd/agent-skills/main/install.sh | bash               # all skills
#   curl -fsSL https://raw.githubusercontent.com/milad-ahmd/agent-skills/main/install.sh | bash -s -- a11y-fixer sql-optimizer
# Env: SKILLS_DIR (default ~/.claude/skills). Use SKILLS_DIR=.claude/skills for a project-local install.
set -euo pipefail

USER_NAME="milad-ahmd"
ALL=(readme-glow-up pr-reviewer-pro commit-craft test-gap-hunter dependency-doctor env-secrets-guard dockerize-anything changelog-from-git codebase-explainer a11y-fixer sql-optimizer launch-post-writer landing-page-roaster migration-wizard api-to-sdk screenshot-to-component meeting-notes-to-tickets instagram-growth-manager tiktok-viral-creator youtube-seo-optimizer youtube-script-writer content-repurposer social-media-calendar hook-generator ad-copy-generator seo-blog-writer email-campaign-writer brand-voice-guide competitor-spy influencer-outreach social-analytics-reporter)
DEST="${SKILLS_DIR:-$HOME/.claude/skills}"

command -v git >/dev/null || { echo "git is required" >&2; exit 1; }

if [[ $# -gt 0 ]]; then SKILLS=("$@"); else SKILLS=("${ALL[@]}"); fi

mkdir -p "$DEST"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

ok=0
for s in "${SKILLS[@]}"; do
  if ! printf '%s\n' "${ALL[@]}" | grep -qx "$s"; then
    echo "✗ unknown skill: $s (available: ${ALL[*]})" >&2; continue
  fi
  if git clone -q --depth 1 "https://github.com/$USER_NAME/$s.git" "$tmp/$s" 2>/dev/null; then
    rm -rf "${DEST:?}/$s"
    cp -R "$tmp/$s/skills/$s" "$DEST/$s"
    echo "✓ $s"; ok=$((ok+1))
  else
    echo "✗ failed to download $s" >&2
  fi
done

echo
echo "Installed $ok skill(s) into $DEST"
echo "⭐ Like them? Star https://github.com/$USER_NAME/agent-skills"
