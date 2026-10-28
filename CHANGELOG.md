# Changelog

A new skill ships **every Monday** ("Skill of the Week"). ⭐ Star or watch the repo to get notified, and [request a skill](https://github.com/milad-ahmd/agent-skills/issues/new).

## 2026-10-26: v4.2 · skills-doctor hardening
Thanks to **Wisp** for an independent review that found real gaps in the linter. Every point was reproduced and fixed:
- 🔀 **Overlap detection could never fire:** the threshold was 0.35, but the highest pair in the collection was 0.29. It now compares whole descriptions (≥ 0.20) **and** the "Use when…" trigger clauses (≥ 0.30). This caught 9 real overlaps between the social platform agents, whose trigger clauses are now distinct.
- 📝 **Folded or literal YAML descriptions** (`>` / `|`) were read as one character and caused false warnings. Folded, literal and quoted multi-line scalars now parse correctly.
- 🔐 **The secret scan missed common leaks:** `AWS_SECRET_ACCESS_KEY=…` with no AKIA prefix, and `scheme://user:pass@host`. It now also detects generic `*SECRET*`/`*PASSWORD*`/`*TOKEN*`/`*API_KEY*` assignments and credentials in URLs, with placeholder and code-expression filtering.
- 🚦 **New `--strict` flag:** exit `2` on warnings or overlaps, so CI can gate on them. The SKILL.md now says "no errors" is not a clean bill of health.
- 🧪 Added 12 regression tests, including the review's exact cases. CI runs them and lints the collection in strict mode.
- `skill-creator-pro`'s `lint_skill.py` now shares the same parser and secret checks.

## 2026-10-25: v4 · 51 skills
- 🎉 New: roast-my-github, codebase-time-machine, ai-slop-detector, startup-idea-validator
- 🦸 New: agent-memory-keeper, skill-creator-pro, skills-doctor
- 🛠️ New: cost-cutter, incident-commander, app-store-optimizer
- ⚙️ Real stdlib Python scripts: GitHub profile stats, git timeline, slop scanner, skills linter/doctor, secret scanner (env-secrets-guard)
- 🎒 Installer: `--pack <role>` and `--list`
- 🎬 Demo GIF gallery recorded from real runs

## v3 · 41 skills
- 🤖 Social platform agents: social-media-manager, LinkedIn, X, Facebook, Pinterest, Threads, Reddit, Telegram, WhatsApp, Google Business Profile

## v2 · 31 skills
- 📣 Marketing: Instagram, TikTok, YouTube SEO and scripts, repurposing, calendar, hooks, ads, SEO blog, email, brand voice, competitors, influencers, analytics

## v1 · 17 skills
- 💻 Developer workflows: PR review, commits, tests, deps, secrets, Docker, changelog, codebase explainer, a11y, SQL, launch posts, landing page roaster, migrations, API SDKs, screenshot to component, meeting notes to tickets, README glow-up

## 🗓️ Upcoming Skills of the Week (vote with 👍 on issues)
- `pitch-deck-builder`: investor deck outline and slide copy
- `job-hunt-copilot`: tailored CV, cover letter and interview prep
- `api-docs-writer`: OpenAPI to friendly docs with examples
- `newsletter-curator`: weekly curated newsletter from your sources
