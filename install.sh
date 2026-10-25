#!/usr/bin/env bash
# Install milad-ahmd agent skills.
#   curl -fsSL https://raw.githubusercontent.com/milad-ahmd/agent-skills/main/install.sh | bash               # all skills
#   curl -fsSL https://raw.githubusercontent.com/milad-ahmd/agent-skills/main/install.sh | bash -s -- a11y-fixer sql-optimizer
#   curl -fsSL https://raw.githubusercontent.com/milad-ahmd/agent-skills/main/install.sh | bash -s -- --pack creator
#   ... | bash -s -- --list
# Env: SKILLS_DIR (default ~/.claude/skills). Use SKILLS_DIR=.claude/skills for a project-local install.
set -euo pipefail

USER_NAME="milad-ahmd"
ALL=(readme-glow-up pr-reviewer-pro commit-craft test-gap-hunter dependency-doctor env-secrets-guard dockerize-anything changelog-from-git codebase-explainer a11y-fixer sql-optimizer launch-post-writer landing-page-roaster migration-wizard api-to-sdk screenshot-to-component meeting-notes-to-tickets instagram-growth-manager tiktok-viral-creator youtube-seo-optimizer youtube-script-writer content-repurposer social-media-calendar hook-generator ad-copy-generator seo-blog-writer email-campaign-writer brand-voice-guide competitor-spy influencer-outreach social-analytics-reporter social-media-manager linkedin-growth-manager x-twitter-growth-manager facebook-page-manager pinterest-growth-manager threads-growth-manager reddit-marketing-agent telegram-channel-manager whatsapp-business-agent google-business-profile-manager roast-my-github startup-idea-validator codebase-time-machine ai-slop-detector agent-memory-keeper skill-creator-pro cost-cutter incident-commander app-store-optimizer skills-doctor)

# Role-based packs
pack_skills() {
  case "$1" in
    indie-hacker)   echo "startup-idea-validator landing-page-roaster launch-post-writer readme-glow-up app-store-optimizer seo-blog-writer x-twitter-growth-manager reddit-marketing-agent cost-cutter commit-craft" ;;
    creator)        echo "social-media-manager instagram-growth-manager tiktok-viral-creator youtube-seo-optimizer youtube-script-writer content-repurposer hook-generator social-media-calendar social-analytics-reporter ai-slop-detector" ;;
    marketer)       echo "social-media-manager brand-voice-guide ad-copy-generator email-campaign-writer seo-blog-writer competitor-spy influencer-outreach social-analytics-reporter hook-generator landing-page-roaster" ;;
    local-business) echo "google-business-profile-manager whatsapp-business-agent facebook-page-manager instagram-growth-manager social-media-calendar brand-voice-guide" ;;
    startup-cto)    echo "pr-reviewer-pro incident-commander cost-cutter env-secrets-guard dependency-doctor migration-wizard test-gap-hunter dockerize-anything codebase-explainer agent-memory-keeper" ;;
    developer)      echo "pr-reviewer-pro commit-craft test-gap-hunter env-secrets-guard dependency-doctor dockerize-anything changelog-from-git codebase-explainer sql-optimizer migration-wizard api-to-sdk agent-memory-keeper" ;;
    skill-builder)  echo "skill-creator-pro skills-doctor agent-memory-keeper" ;;
    fun)            echo "roast-my-github codebase-time-machine landing-page-roaster ai-slop-detector" ;;
    *) return 1 ;;
  esac
}
PACKS="indie-hacker creator marketer local-business startup-cto developer skill-builder fun"
DEST="${SKILLS_DIR:-$HOME/.claude/skills}"

command -v git >/dev/null || { echo "git is required" >&2; exit 1; }

SKILLS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --list) echo "Skills (${#ALL[@]}): ${ALL[*]}"; echo; for p in $PACKS; do echo "pack $p: $(pack_skills $p)"; done; exit 0 ;;
    --pack) shift; list="$(pack_skills "${1:-}")" || { echo "Unknown pack '${1:-}'. Packs: $PACKS" >&2; exit 1; }; for x in $list; do SKILLS+=("$x"); done ;;
    *) SKILLS+=("$1") ;;
  esac
  shift
done
[[ ${#SKILLS[@]} -gt 0 ]] || SKILLS=("${ALL[@]}")

mkdir -p "$DEST"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

# Fetch the whole collection once (all skills live in this repo under skills/)
REPO_DIR="$tmp/agent-skills"
git clone -q --depth 1 "https://github.com/$USER_NAME/agent-skills.git" "$REPO_DIR" || { echo "Download failed" >&2; exit 1; }

ok=0
for s in "${SKILLS[@]}"; do
  if ! printf '%s\n' "${ALL[@]}" | grep -qx "$s"; then
    echo "✗ unknown skill: $s (available: ${ALL[*]})" >&2; continue
  fi
  if [[ -d "$REPO_DIR/skills/$s" ]]; then
    rm -rf "${DEST:?}/$s"
    cp -R "$REPO_DIR/skills/$s" "$DEST/$s"
    echo "✓ $s"; ok=$((ok+1))
  else
    echo "✗ failed to download $s" >&2
  fi
done

echo
echo "Installed $ok skill(s) into $DEST"
echo "⭐ Like them? Star https://github.com/$USER_NAME/agent-skills"
