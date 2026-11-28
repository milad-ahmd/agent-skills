# Changelog

⭐ Star or watch the repo to get notified about new skills, and [request a skill](https://github.com/milad-ahmd/agent-skills/issues/new). Popular requests get built first.

## 2026-11-28: v5.9 · four more skills check before they write
Four everyday developer skills were instructions only. Each now runs a read-only stdlib Python script first, tried on real open-source projects and every finding checked by hand.
- 🐳 **dockerize-anything.** `detect_stack.py` reads the runtime version, package manager and lockfile, the install command that honours it, start commands, the port (with file:line) and the *names* of env vars the code reads, and never opens `.env`. An existing Dockerfile and `.dockerignore` get a lint with each rule cited from Docker's docs: unpinned base, no USER, `ADD` for local files, apt cache left behind, `COPY . .` before the install, `.env` or `.git` in the build context.
- ♿ **a11y-fixer.** `a11y_scan.py` scans HTML, JSX/TSX, Vue and Svelte for 15 failures static markup can decide (missing alt, unlabelled controls, nameless buttons and links, click handlers with no keyboard path, positive tabindex, invalid roles), each mapped to its WCAG 2.2 criterion with a confidence level. `--fail-on high` makes it a CI gate. Colour contrast and anything set at runtime are out of scope, and it says so.
- ✨ **readme-glow-up.** `readme_audit.py` finds broken relative links and images, in-page anchors that don't resolve (GitHub's slug rules, checked against GitHub's own renderer, emoji headings included), images with no alt, code fences with no language, missing sections, and install commands that don't match the repo, such as `npm run test` with no test script. External URLs are listed, never fetched. It found two GIFs with no alt text in this README; both are fixed.
- 🧱 **commit-craft.** `commit_facts.py` lists staged, unstaged and untracked changes by kind, marks whitespace-only files as formatting, and measures the repo's own message style from its history (Conventional Commits, `area:` prefixes or free-form) so the agent matches it instead of imposing one. `--lint` checks a message; its Conventional Commits parser is the same one changelog-from-git uses, and CI keeps the two identical.
- The three v5.8 mirror repos now show real sample output in their READMEs.
- 1,191 unit tests in total; 46 skills now run a script first.

## 2026-11-28: v5.8 · three core dev skills now work from facts
Three of the most-used developer skills were instructions only. Each now runs a read-only stdlib Python script first, with tests, so the agent starts from your repo instead of guessing.
- 🧪 **test-gap-hunter.** `find_gaps.py` ranks source files by risk = commits in the last 180 days × how untested they are. It reads Cobertura (`coverage.xml`) and LCOV (`lcov.info`) reports when they exist; without one it pairs sources and tests by each language's naming conventions and labels every row as a guess. For Python it lists the biggest untested functions in the top files.
- 📝 **changelog-from-git.** `release_notes.py` reads the commits since the last tag, groups them by Conventional Commits 1.0.0 type with breaking changes first, takes PR numbers from squash and merge subjects, drops commits that were later reverted, folds dependency bumps into one counted line and suggests the next semver bump. `--md` prints a CHANGELOG section for the agent to rewrite in plain language.
- 🩺 **dependency-doctor.** `dep_audit.py` compares what package.json, pyproject.toml, requirements files and go.mod declare with what the code imports, and checks lockfiles (package-lock, pnpm, yarn v1, poetry, uv, go.sum): unused, undeclared, dev-only packages imported by app code, and manifests with no lockfile. Packages used only from scripts or config are listed separately, never called unused. Registry checks stay with `npm audit`, `pip-audit` and `govulncheck`, which the agent runs itself.
- 🔐 **env-secrets-guard:** the wider hard-coded secret rule from v5.7.1 no longer flags translation files: matches in `locales/`, `i18n/`, `lang/` or `translations/` folders, values that repeat the key name, and values that are one capitalised word (`"secret": "Geheimnis"`) are skipped. Key detectors such as AWS and GitHub tokens still scan those folders.
- 1,083 unit tests in total; 42 skills now run a script first.

## 2026-11-28: v5.7.1 · every script has tests
The six skills that shipped a script without tests now have them (144 new tests, 1,000 in total). Writing them turned up real bugs, all fixed:
- ⏳ **codebase-time-machine:** an empty repo or a folder that isn't a repo crashed; renamed files showed up as fake "cursed files"; commits made in the same second came out in the wrong order; `--max N` before the path was read as the path.
- 🔥 **roast-my-github:** one lazy-commit pattern could never match, an empty commit message crashed, and API errors printed tracebacks. New `--from-json FILE` runs it on saved API responses.
- 🔐 **env-secrets-guard:** file names with non-ASCII characters were skipped without a warning, Anthropic keys were also reported as OpenAI keys, and the hard-coded secret rule missed the most common forms: `DB_PASSWORD="…"`, `OPENAI_API_KEY="…"`, `SECRET_KEY = "…"` and JSON `"password": "…"`. Repos with those will now fail the scan, as they should.
- 📊 **social-analytics-reporter:** a row with extra cells crashed, `--top 0` printed every row, ID columns such as Meta post IDs were summed as metrics, a first-column "Total" row is now dropped so it isn't counted twice, and unreadable files gave a traceback.
- 🔎 **pr-reviewer-pro:** a branch with no common history with its base exited silently; it now says why.
- 🛠️ **skill-creator-pro:** `lint_skill.py .` warned about a folder named "."; a script path at the end of a sentence was reported missing.

## 2026-11-27: v5.7 · funerals, diets and clone families
Four more skills that make a number worth posting and then clean something up. Each ships a read-only stdlib Python tool with tests and a shareable card without paths or names.
- 🪦 **New: dead-code-funeral.** `find_dead.py` finds Python and JS/TS files nothing imports and top-level functions, classes and exports nothing names, skips entry points, tests and framework-convention files, and dates each item from git. The agent checks every suspect for dynamic loading and framework wiring, writes the obituaries from those facts, and deletes only approved items in tested commits.
- 🥗 **New: repo-diet.** `repo_diet.py` compares .git with the checkout, lists the biggest blobs ever committed (with the commit that added them and whether they still exist), committed junk with the .gitignore line that fixes it, and Git LFS candidates. History rewrites are printed as `git filter-repo` commands with their cost spelled out, never run.
- 👯 **New: copy-paste-detector.** `find_clones.py` groups duplicated code into clone families, in an exact tier and a renamed tier, marks copies that have drifted apart, and reports the % of duplicated lines; `--fail-over` turns it into a CI budget. About 1 second on a 100k-line repo.
- 💰 **New: codebase-valuation.** `valuation.py` counts code per language and applies basic COCOMO (organic mode, the constants and defaults SLOCCount and scc use, all sourced), then shows what git says really happened. Every output says it is a 1981 model estimate, not a valuation.
- 71 skills in total.

## 2026-11-27: v5.6 · roasts, buses and floppy disks
Four more skills that make something worth posting and leave the repo better. Each ships a stdlib Python tool with tests, and people stay anonymous unless you ask.
- 🪵 **New: commit-roast.** `commit_stats.py` scores a repo's commit history out of 100 (descriptive subjects, imperative mood, length, bodies on big commits, Conventional Commits, fix-the-fix chains, shouting) with the formula shown, and finds the five most roastable facts with evidence. The skill then writes commit rules that fit what this repo gets wrong.
- 🚌 **New: bus-factor.** `bus_factor.py` measures who knows each file from git blame and recent commits, finds files only one person owns, and computes the team's bus factor (an exact search, or a greedy upper bound after Avelino et al. 2016, counting lines). A shareable card shows the headline without names.
- ⚖️ **New: dependency-weigh-in.** `weigh_in.py` weighs node_modules (npm, pnpm, yarn, bun), venvs and Go modules, says how many floppy disks or copies of shareware Doom that is (every reference size is sourced), and lists the heaviest packages with what pulls them in, duplicate copies, dev-only weight and lighter alternatives.
- 🪪 **New: repo-card.** `repo_card.py` renders a 1280x640 social preview from your repo's real name, tagline, languages, license and stars in three templates, converts it to PNG with whatever converter the machine has, and prints og:image tags. You upload it in the repo's settings yourself.
- 67 skills at this release.

## 2026-11-27: v5.5 · wrapped season
Four skills that make something worth sharing and stay useful after the screenshot. Each ships a stdlib Python tool with tests.
- 🎁 **New: dev-wrapped.** `wrapped.py` reads your own commits across local repos for a year: commits, active days, streaks, hour and weekday patterns, languages by changed lines, favourite commit verbs and an archetype (The Night Owl, The Weekend Warrior…), then draws a 1200x630 card in a dark or light theme. The card and markdown recap show no repo names, paths or emails unless you ask.
- 🧾 **New: tech-debt-receipt.** `debt_receipt.py` finds every TODO, FIXME, HACK, XXX and lint suppression in tracked files, ages each with git blame, prices it by kind, age and how often its file changes, and prints a receipt (text or SVG) with the formula at the bottom. Then a ranked list of what to pay first, and `--fail-over` for a CI debt budget. Authors stay hidden unless you ask.
- 🌐 **New: github-portfolio.** `portfolio.py` builds a one-page portfolio from your public GitHub data inside the unauthenticated rate limit: featured projects, languages, recent activity. No JavaScript, light and dark themes, every repo description escaped. A `data.json` keeps your own wording across rebuilds.
- 🔤 **New: name-checker.** `check_names.py` checks candidate names against domains (DNS, then RDAP through the IANA bootstrap), GitHub, npm, PyPI, crates.io and Homebrew, applies each registry's naming rules, and ranks them. A timeout or rate limit shows as unknown, never as free.
- 63 skills at this release.

## 2026-11-25: v5.4 · triage, hooks and a shared language
Three new skills, picked from the most-installed skills on skills.sh that this collection didn't cover. Each ships a stdlib Python tool with tests.
- 🏷️ **New: issue-triage.** `triage.py` reads open issues (a saved `gh issue list --json` file, or `--repo` to fetch them read-only) and finds duplicate candidates by text similarity, bug reports missing a version, steps or expected/actual, threads waiting on the reporter or on maintainers, label suggestions and a priority order. The agent drafts replies; labels, comments and closes go to GitHub only after you approve each batch.
- 📖 **New: domain-modeler.** `domain_terms.py` pulls the domain words out of class, type, table and enum names across Python, TS/JS, Go, Java, Kotlin, C#, Ruby, SQL and Prisma, and flags one concept under several names, the same name in two contexts, and near-duplicate spellings. The skill turns that into a GLOSSARY.md and a domain doc, and `--glossary` checks new code against it in CI.
- 🚦 **New: pre-commit-setup.** `hooks_plan.py` detects the stack, the tools already configured and any existing hook setup, then picks pre-commit, husky + lint-staged or lefthook and writes the config (never over an existing one). Hooks run on staged files only, and the same checks are mirrored in CI so `--no-verify` can't skip them for good.
- 59 skills at this release.

## 2026-11-05: v5.3 · durable agent workflows
- ⏪ **New: durable-agent-workflows.** Turns a long, costly Python agent job into a durable workflow with [rewindable](https://github.com/milad-ahmd/rewindable): every model call, tool call and side effect becomes a journaled step, so a crash resumes where it stopped without re-paying finished calls, irreversible tools wait for a human, and a run can be rewound to a step after a fix. `durability_audit.py` reads the code and lists every call that would break replay (network and SDK calls, clock, randomness, file writes outside a step, tools that move money without approval) with the `ctx` replacement.
- skill-finder now leads with the questions it answers ("is there a skill for X?"), after live evals showed it was skipped for them.
- agent-guardrails points at `/hooks` after install.
- 56 skills at this release.

## 2026-11-01: v5.2 · the skills people install most
Seven new skills, picked from the most-installed skills on skills.sh that this collection didn't cover yet. Six ship a stdlib Python tool with tests.
- 🔀 **New: merge-conflict-resolver.** `conflicts.py` names the operation (merge, rebase with progress, cherry-pick, revert, am, stash pop) and what ours and theirs mean in it, then shows each hunk with base, both sides and the commits behind each side, so the agent merges intents instead of picking a side. Lockfiles are regenerated, never hand-merged, and `--check` finds leftover markers, staged ones included.
- 🚧 **New: agent-guardrails.** A Claude Code PreToolUse hook (with Cursor and Codex formats) that denies or asks before force pushes to main, `rm -rf` outside the project, reading `.env` and keys, `curl | bash`, `DROP TABLE` and `--no-verify`. It parses chains, wrappers, `bash -c` and `$(...)`, merges into settings.json with a backup, and ships a 59-command self-test. A seatbelt, not a sandbox, and the skill says so.
- 🏛️ **New: architecture-doctor.** `arch_report.py` measures fan-in, fan-out and instability, cycles, god files, layer violations and hidden coupling (files that change together without importing each other), weights each by git churn, and the skill turns the top ones into small, separately shippable refactors.
- 💅 **New: ui-taste.** `ui_lint.py` scores CSS, SCSS and Tailwind 0-100: font sizes, near-duplicate colours, off-grid spacing, radii and shadows, contrast, `!important`, arbitrary values and the generic AI look. `--baseline` shows the before/after.
- 🔴 **New: tdd-loop.** `tdd_guard.py` records red, green and refactor. It refuses a red that fails on an import or syntax error, and a green reached by editing, deleting or skipping the test.
- 🧭 **New: skill-finder.** Describe a task and get the matching skill from this collection, or a `npx skills find` search of the wider registry, installed only after you say yes.
- ✂️ **New: terse-mode.** Shorter replies that keep every command, path, number and warning. `terse_check.py --compare` proves nothing was lost.
- skills-doctor no longer reads `scripts/index.json` as a missing `scripts/index.js`.
- 55 skills at this release.

## 2026-10-30: v5.1 · from spec to debugger
Four new skills and two upgrades, picked from what people install most on skills.sh and star most on GitHub. Each one ships a stdlib Python tool with tests.
- 🕵️ **New: bug-detective.** Reproduce first, keep a hypothesis log, narrow by halves, fix with a failing test. `bisect_run.py` checks that the good ref passes and the bad ref fails before it starts, runs `git bisect` with a timeout and `--repeat` for flaky bugs, and always puts your HEAD back, even on Ctrl-C.
- 🎬 **New: demo-recorder.** Records a real demo of your CLI (a VHS `.tape`, or asciinema) or web app (a Playwright script), then shrinks it with ffmpeg and gifsicle. The script stays in the repo so you can re-record after changes.
- 🎭 **New: e2e-flow-tester.** `find_flows.py` maps routes, forms and existing test setup across Next.js, React Router, Remix, SvelteKit, Nuxt, Angular, Django, Rails, Laravel and Express, then the skill writes Playwright tests for the flows that matter, with stable locators and a CI job.
- 📐 **New: plan-griller.** Reads your code, then asks one decision at a time (with a recommended answer) until the plan is concrete, and writes a SPEC.md. `spec_lint.py` flags vague words, requirements with no acceptance criterion, criteria without Given/When/Then and gaps in the test plan.
- 🗺️ **codebase-explainer now measures before it explains.** `code_map.py` builds the import graph for Python, JS/TS (with tsconfig aliases) and Go: entry points, hub files, import cycles, orphans, a Mermaid diagram and a self-contained interactive HTML map.
- 🧠 **agent-memory-keeper adds handoffs.** HANDOFF.md holds the current task's state, next step and dead ends. `handoff.py` collects the git facts agents get wrong and, at the start of the next session, checks the repo still matches what the handoff says.
- 48 skills at this release.

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
