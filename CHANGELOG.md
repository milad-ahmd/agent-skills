# Changelog

⭐ Star or watch the repo to get notified about new skills, and [request a skill](https://github.com/milad-ahmd/agent-skills/issues/new). Popular requests get built first.

## 2026-10-30: v5.1 · from spec to debugger
Four new skills and two upgrades, picked from what people install most on skills.sh and star most on GitHub. Each one ships a stdlib Python tool with tests.
- 🕵️ **New: bug-detective.** Reproduce first, keep a hypothesis log, narrow by halves, fix with a failing test. `bisect_run.py` checks that the good ref passes and the bad ref fails before it starts, runs `git bisect` with a timeout and `--repeat` for flaky bugs, and always puts your HEAD back, even on Ctrl-C.
- 🎬 **New: demo-recorder.** Records a real demo of your CLI (a VHS `.tape`, or asciinema) or web app (a Playwright script), then shrinks it with ffmpeg and gifsicle. The script stays in the repo so you can re-record after changes.
- 🎭 **New: e2e-flow-tester.** `find_flows.py` maps routes, forms and existing test setup across Next.js, React Router, Remix, SvelteKit, Nuxt, Angular, Django, Rails, Laravel and Express, then the skill writes Playwright tests for the flows that matter, with stable locators and a CI job.
- 📐 **New: plan-griller.** Reads your code, then asks one decision at a time (with a recommended answer) until the plan is concrete, and writes a SPEC.md. `spec_lint.py` flags vague words, requirements with no acceptance criterion, criteria without Given/When/Then and gaps in the test plan.
- 🗺️ **codebase-explainer now measures before it explains.** `code_map.py` builds the import graph for Python, JS/TS (with tsconfig aliases) and Go: entry points, hub files, import cycles, orphans, a Mermaid diagram and a self-contained interactive HTML map.
- 🧠 **agent-memory-keeper adds handoffs.** HANDOFF.md holds the current task's state, next step and dead ends. `handoff.py` collects the git facts agents get wrong and, at the start of the next session, checks the repo still matches what the handoff says.
- 48 skills in total.

## 2026-10-29: v5 · fewer, deeper skills
- 🔥 **Roast your GitHub in the browser:** [milad-ahmd.github.io/agent-skills/roast](https://milad-ahmd.github.io/agent-skills/roast/). No install, no login, no AI: real numbers from GitHub's public API, a /10 scorecard, a concrete glow-up plan and a downloadable scorecard image.
- 🧹 **ai-slop-detector is now a measurable tool:** 120 documented patterns in 9 categories, a weighted 0–100 score, `--json`, `--format github` and `--fail-above` for CI, before/after examples, and a small benchmark (62 passages; ROC-AUC 0.98 on the held-out half, with the caveats spelled out in `bench/README.md`). New [GitHub Action](https://github.com/milad-ahmd/ai-slop-detector#-use-it-in-ci) flags AI-sounding docs and PR descriptions.
- 🩺 **skills-doctor in CI:** `--format github` puts problems on the PR as annotations with file and line, plus a job summary. New [Skills Doctor Action](https://github.com/milad-ahmd/skills-doctor#-use-it-in-ci) for any repo with skills.
- 🛡️ **New: gh-actions-auditor.** Finds pwn requests, script injection, unpinned actions and over-broad permissions in workflows, then fixes them.
- 🪤 **New: prompt-injection-scanner.** Checks skills, CLAUDE.md/AGENTS.md, hooks and MCP configs for hidden Unicode, injection phrasing, exfiltration and unsafe settings before you trust them.
- 🧠 **9 platform skills merged into social-media-manager:** LinkedIn, X, Facebook, Pinterest, Threads, Reddit, Telegram, WhatsApp Business and Google Business Profile now live in `skills/social-media-manager/references/`, loaded one at a time. Less generic advice, more platform facts. Old names still work with `install.sh`, and their web pages redirect.
- 🧪 **Trigger evals:** `evals/` has prompts that should and shouldn't load each skill. CI runs a free offline check (hit@3 95%); `scripts/run_trigger_evals.py --live` runs them against Claude Code.
- CI now runs every skill's unit tests. 44 skills at this release.

## 2026-10-27: v4.3 · overlap as a reading, not a claim
Follow-up from **Wisp**, who re-ran f54e669: all four fixes held, but "0 overlaps" only meant "nothing crossed 0.20".
- 📏 The summary now always prints the **closest description pair and closest trigger-clause pair**, with score, threshold and status (`ok` / `close: review` / `over`).
- Made two near pairs more distinct: youtube-script-writer vs youtube-seo-optimizer (trigger clause 0.25 → 0.21), and instagram-growth-manager vs tiktok-viral-creator (description 0.19 → below the new closest pair).
- The closest description pair is now linkedin-growth-manager vs x-twitter-growth-manager at 0.18, flagged `close: review`. Platform skills share real vocabulary, so this is reported, not hidden.
- New regression test for the reading line.

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
