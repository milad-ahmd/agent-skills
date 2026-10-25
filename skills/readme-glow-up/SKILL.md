---
name: readme-glow-up
description: Rewrites a repository's README into a polished, star-worthy page with badges, a hero tagline, demo section, quickstart, features, usage, and contributing info. Use when the user asks to improve, rewrite, polish, or "glow up" a README, or when a repo has a missing or weak README.
---

# README Glow-Up

Turn a boring or missing README into one that makes visitors star the repo within 10 seconds.

## Workflow

1. **Investigate the repo before writing anything**
   - Read the existing `README*` (if any) and keep every factual detail.
   - Detect the stack from manifests: `package.json`, `pyproject.toml`, `requirements.txt`, `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle`, `Gemfile`, `composer.json`, `pubspec.yaml`, `Dockerfile`.
   - Find the real install/run/test commands (scripts section, Makefile, CI workflows in `.github/workflows`).
   - Find the license file, the GitHub remote (`git remote get-url origin`) and any existing screenshots/GIFs in the repo.
2. **Never invent facts.** No fake benchmarks, users, features, or commands. If something is unknown, leave a clearly marked `<!-- TODO: ... -->` comment.
3. **Write the new README** using the structure below.
4. **Show a short summary** of what changed and list any TODOs the user must fill in (e.g. "record a demo GIF").

## Structure

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

## Badge recipes (shields.io)

Replace `OWNER/REPO` with values from the git remote:

- License: `https://img.shields.io/github/license/OWNER/REPO`
- Stars: `https://img.shields.io/github/stars/OWNER/REPO?style=social`
- Release: `https://img.shields.io/github/v/release/OWNER/REPO`
- CI: `https://img.shields.io/github/actions/workflow/status/OWNER/REPO/<workflow-file>`
- npm: `https://img.shields.io/npm/v/<package>` · PyPI: `https://img.shields.io/pypi/v/<package>`

Only add a badge if the thing it measures actually exists.

## Style rules

- The first screen (title, tagline, badges, demo) must sell the project on its own.
- Short sentences, active voice, no marketing fluff ("blazingly", "revolutionary").
- Every code block must be copy-pasteable and tagged with a language.
- Keep it scannable: headings, bullets, tables. Move long reference docs to `docs/`.
- Preserve existing sections the author clearly cares about (sponsors, citations, acknowledgements).
