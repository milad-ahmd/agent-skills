<div align="center">

# 🧰 Agent Skills

### 13 battle-tested skills that make your AI coding agent work like a senior engineer.

**Works with Claude Code · Cline · Cursor · Codex · any agent that reads `SKILL.md`**

[![Stars](https://img.shields.io/github/stars/milad-ahmd/agent-skills?style=social)](https://github.com/milad-ahmd/agent-skills)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
![Skills](https://img.shields.io/badge/skills-13-orange)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](#-contributing)

[Install](#-install-in-10-seconds) · [All skills](#-the-skills) · [What is a skill?](#-what-is-a-skill) · [Awesome list](https://github.com/milad-ahmd/awesome-agent-skills)

</div>

---

## ⚡ Install in 10 seconds

```bash
# all 13 skills
curl -fsSL https://raw.githubusercontent.com/milad-ahmd/agent-skills/main/install.sh | bash

# or only the ones you want
curl -fsSL https://raw.githubusercontent.com/milad-ahmd/agent-skills/main/install.sh | bash -s -- pr-reviewer-pro env-secrets-guard

# project-local install (commit it so your whole team gets the skills)
curl -fsSL https://raw.githubusercontent.com/milad-ahmd/agent-skills/main/install.sh | SKILLS_DIR=.claude/skills bash
```

Then just talk to your agent normally. It picks the right skill automatically.

## 🎯 The skills

### 🔀 Git & Workflow
| Skill | What it does | Try saying |
|---|---|---|
| 🔎 [**pr-reviewer-pro**](https://github.com/milad-ahmd/pr-reviewer-pro) | Senior-engineer PR reviews: severities, `file:line` evidence, suggested fixes | *"Review PR #123"* |
| 🧱 [**commit-craft**](https://github.com/milad-ahmd/commit-craft) | Messy changes → clean, atomic Conventional Commits | *"Commit my changes"* |
| 📝 [**changelog-from-git**](https://github.com/milad-ahmd/changelog-from-git) | Human-readable release notes from git history | *"Write release notes since v1.2"* |

### 🧪 Testing & Quality
| Skill | What it does | Try saying |
|---|---|---|
| 🧪 [**test-gap-hunter**](https://github.com/milad-ahmd/test-gap-hunter) | Finds the riskiest untested code and writes real tests | *"Find test gaps and fix the top 3"* |
| ♿ [**a11y-fixer**](https://github.com/milad-ahmd/a11y-fixer) | WCAG 2.2 AA audit that fixes issues in code | *"Make this app accessible"* |

### 🔐 Security & Maintenance
| Skill | What it does | Try saying |
|---|---|---|
| 🔐 [**env-secrets-guard**](https://github.com/milad-ahmd/env-secrets-guard) | Catches leaked API keys before you push | *"Scan for leaked secrets"* |
| 🩺 [**dependency-doctor**](https://github.com/milad-ahmd/dependency-doctor) | Vulnerable, outdated and unused packages with a safe upgrade plan | *"Check my dependencies"* |

### 🐳 DevOps & Backend
| Skill | What it does | Try saying |
|---|---|---|
| 🐳 [**dockerize-anything**](https://github.com/milad-ahmd/dockerize-anything) | Small, secure, multi-stage Dockerfile and compose | *"Dockerize this project"* |
| ⚡ [**sql-optimizer**](https://github.com/milad-ahmd/sql-optimizer) | Explains slow queries and suggests indexes and rewrites | *"Why is this query slow?"* |

### 📚 Documentation
| Skill | What it does | Try saying |
|---|---|---|
| ✨ [**readme-glow-up**](https://github.com/milad-ahmd/readme-glow-up) | Turns any README into a star magnet | *"Glow up my README"* |
| 🗺️ [**codebase-explainer**](https://github.com/milad-ahmd/codebase-explainer) | Onboarding guide with Mermaid architecture diagrams | *"Explain this codebase"* |

### 🚀 Growth
| Skill | What it does | Try saying |
|---|---|---|
| 🚀 [**launch-post-writer**](https://github.com/milad-ahmd/launch-post-writer) | X thread, Show HN, LinkedIn and Reddit posts from your repo | *"Write launch posts for this repo"* |
| 🔥 [**landing-page-roaster**](https://github.com/milad-ahmd/landing-page-roaster) | Scored, funny, honest roast of your landing page plus rewrites | *"Roast my landing page"* |

## 🤔 What is a skill?

A skill is a folder with a `SKILL.md` file: YAML frontmatter (`name`, `description`) followed by instructions, plus optional scripts. Your agent reads the description and loads the full instructions **only when the task matches**, so your context stays clean and the agent follows a proven workflow instead of improvising.

```text
~/.claude/skills/
└── pr-reviewer-pro/
    ├── SKILL.md
    └── scripts/collect_diff.sh
```

**Agent without native skill support?** Paste any `SKILL.md` into your rules file (`.clinerules`, `.cursor/rules`, `AGENTS.md`, `CLAUDE.md`).

## 🧠 Design principles

Every skill in this collection:
- ✅ **Investigates before acting.** It reads your repo and follows your conventions.
- ✅ **Never invents facts.** It cites evidence and marks unknowns as TODOs.
- ✅ **Asks before anything destructive**, such as pushing, force-pushing, deleting or posting.
- ✅ **Produces a clear, structured output** you can act on immediately.

## 🗺️ Roadmap

- [ ] `migration-wizard`: JS→TS, Jest→Vitest, Pages→App Router
- [ ] `api-to-sdk`: typed client from an OpenAPI spec
- [ ] `screenshot-to-component`: image → component in your design system
- [ ] `meeting-notes-to-tickets`: notes → GitHub, Linear or Jira issues

👉 Want a skill? [Open an issue](https://github.com/milad-ahmd/agent-skills/issues/new). Popular requests get built first.

## 🤝 Contributing

Ideas, bug reports and new skills are welcome. Each skill lives in its own repo, so open issues there. You can also browse community skills in [awesome-agent-skills](https://github.com/milad-ahmd/awesome-agent-skills).

## 📄 License

MIT © [Milad Ahmadi](https://github.com/milad-ahmd)

<div align="center">

### ⭐ If these skills save you time, star the repo so other developers can find them.

</div>
