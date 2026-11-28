---
name: readme-glow-up
description: Rewrites a repository's README into a polished, star-worthy page with badges, a hero tagline, demo section, quickstart, features, usage, and contributing info. A read-only script audits the existing README first, finding broken relative links and images, broken heading anchors, missing sections, a weak first screen, and install commands that do not match the repo. Use when the user asks to improve, rewrite, polish, or "glow up" a README, or when a repo has a missing or weak README.
---

# README Glow-Up

Turn a boring or missing README into one that makes visitors star the repo within 10 seconds. Start from facts, then write.

## 1. Run the audit

```bash
python3 scripts/readme_audit.py -C /path/to/repo           # text report
python3 scripts/readme_audit.py -C /path/to/repo --json    # every finding, heading id and URL
python3 scripts/readme_audit.py --readme docs/index.md --first-screen 40
```

The script picks the README the way GitHub does (`.github/`, then the root, then `docs/`) and names the file it used. It only reads, and it never goes online. With no README at all it exits `2`: skip to step 2, then audit the new file in step 4.

It checks every relative link and image against the repo. A target is broken when the file is missing or exists only with different letter case, because GitHub paths are case-sensitive even when your laptop is not. A file git does not track gets a warning. In-page `#anchors` are matched against the ids GitHub gives headings, and `[text][ref]` links without a definition are flagged.

It also lists images without alt text, code fences without a language, headings that skip a level, and the heading count, badge count and length. For the first screen it reports the title, the one-line description and the first line where install or usage shows up. Sections (install, usage, examples, configuration, contributing, license) are matched by heading words, and the LICENSE file is compared with the license the README claims.

Shell blocks under install, usage and development headings are checked against the repo. Examples: `npm install` with no package.json, `npm run x` with no such script, `pip install -r` naming a missing file, a `make` target that does not exist, no compose file or Dockerfile. It also flags package names that differ from pyproject.toml, package.json, Cargo.toml or go.mod.

Each finding has a severity (error, warning or info) and a confidence (high, medium or low). Only high-confidence facts are errors, so fix every error in the rewrite. Medium and low findings need judgement. A different package name may be a prerequisite on purpose, and a library whose quickstart is its usage does not need a separate usage section.

Exit codes: `0` audited, nothing broken · `1` audited, at least one error · `2` no README, not Markdown, or a bad path.

What it cannot see: external URLs and badges are never fetched, so the report lists them under "not checked". Say that in your summary instead of calling them fine. Links that leave the repo (`../../issues`) and paths inside submodules are listed the same way. Markdown is read line by line rather than with a full CommonMark parser. TOML, setup.py and Makefiles are read with regular expressions, and files a block downloads or builds first are unknown to it. License families come from key phrases. `references/sources.md` cites the source of every rule.

## 2. Investigate the repo before writing anything

Read the existing README and keep every factual detail. Detect the stack from the manifests (`package.json`, `pyproject.toml`, `requirements.txt`, `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle`, `Gemfile`, `composer.json`, `pubspec.yaml`, `Dockerfile`). Find the real install, run and test commands in the scripts section, the Makefile and `.github/workflows`; where the audit flagged a command, find the one that works. Note the license file, the GitHub remote (`git remote get-url origin`) and any screenshots or GIFs already in the repo.

Never invent facts. No fake benchmarks, users, features or commands. If something is unknown, leave a clearly marked `<!-- TODO: ... -->` comment.

## 3. Write the new README

```markdown
<div align="center">

# <emoji> Project Name

**One punchy line: what it does + for whom + why it's better.**

[badges: license, version/release, CI status, language, stars]

[Quickstart](#-quickstart) · [Features](#-features) · [Docs](#-usage) · [Contributing](#-contributing)

<img src="docs/demo.gif" alt="demo" width="700"/>  <!-- TODO if missing -->

</div>

## ✨ Why <Project>?        → 2–4 sentences: the pain + the fix
## 🚀 Quickstart           → copy-paste commands, working in < 60 seconds
## 🎯 Features             → 4–8 bullets, benefit first, emoji prefix
## 📖 Usage                → real examples with expected output
## ⚙️ Configuration        → table: option | default | description (only if config exists)
## 🗺️ Roadmap              → only if TODO/issues suggest one
## 🤝 Contributing         → how to run locally + PR guidance
## 📄 License              → from LICENSE file
## ⭐ Star History / call to action → "If this saved you time, a ⭐ helps others find it."
```

Some emoji end in an invisible U+FE0F that GitHub keeps in the anchor, so link to those headings only after the audit confirms the id:

```markdown
## ⚙️ Configuration     ← reached by #%EF%B8%8F-configuration, not #-configuration
```

Badge recipes (shields.io), with `OWNER/REPO` taken from the git remote. Only add a badge if the thing it measures actually exists, and give each one alt text.

```text
license  https://img.shields.io/github/license/OWNER/REPO
stars    https://img.shields.io/github/stars/OWNER/REPO?style=social
release  https://img.shields.io/github/v/release/OWNER/REPO
CI       https://img.shields.io/github/actions/workflow/status/OWNER/REPO/<workflow-file>
npm      https://img.shields.io/npm/v/<package>
PyPI     https://img.shields.io/pypi/v/<package>
```

Style rules. The first screen (title, tagline, badges, demo) has to sell the project on its own, with install or a link to it inside the first 30 lines. Write short sentences in the active voice, with no marketing fluff ("blazingly", "revolutionary"). Every code block must be copy-pasteable and tagged with a language. Keep the page scannable with headings, bullets and tables, and move long reference docs to `docs/`. Preserve the sections the author clearly cares about, such as sponsors, citations and acknowledgements.

## 4. Re-run the audit and report

Run the audit on the new README. It should exit `0`: no broken links or anchors, and no install command the repo contradicts. Then summarise what changed and which audit errors were fixed. List the TODOs the user must fill in (for example "record a demo GIF"), and say that external links and badges were not checked.
