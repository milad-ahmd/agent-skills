<div align="center">

# 🧰 Agent Skills

### 31 Agent Skills for developers & marketers. Code, ship, and grow on Instagram, TikTok & YouTube.

**Works with Claude Code · Cline · Cursor · Codex · any agent that reads `SKILL.md`**

[![Stars](https://img.shields.io/github/stars/milad-ahmd/agent-skills?style=social)](https://github.com/milad-ahmd/agent-skills)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
![Skills](https://img.shields.io/badge/skills-31-orange)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](#-contributing)

[Install](#-install-in-10-seconds) · [Marketing skills](#-marketing--social-media-skills-new) · [Developer skills](#-developer-skills) · [What is a skill?](#-what-is-a-skill) · [Awesome list](https://github.com/milad-ahmd/awesome-agent-skills)

<img src="assets/demo-install.gif" alt="Installing agent-skills with one command" width="760"/>

</div>

---

## ⚡ Install in 10 seconds

```bash
# all 31 skills
curl -fsSL https://raw.githubusercontent.com/milad-ahmd/agent-skills/main/install.sh | bash

# or only the ones you want
curl -fsSL https://raw.githubusercontent.com/milad-ahmd/agent-skills/main/install.sh | bash -s -- pr-reviewer-pro env-secrets-guard

# project-local install (commit it so your whole team gets the skills)
curl -fsSL https://raw.githubusercontent.com/milad-ahmd/agent-skills/main/install.sh | SKILLS_DIR=.claude/skills bash
```

Then just talk to your agent normally. It picks the right skill automatically.

## 📣 Marketing & Social Media Skills (new!)

Your AI agent can also act as a social media manager, content strategist and growth marketer.

| Skill | What it does | Try saying |
|---|---|---|
| 📸 [**instagram-growth-manager**](https://github.com/milad-ahmd/instagram-growth-manager) | Grow and manage Instagram with bio audits, Reels scripts, carousels, captions, hashtags and growth reviews | *"Audit my Instagram profile and plan next month"* |
| 🎵 [**tiktok-viral-creator**](https://github.com/milad-ahmd/tiktok-viral-creator) | TikTok ideas, 2-second hooks, shot-by-shot scripts and retention analysis | *"Give me 10 TikTok ideas for my fitness app"* |
| 📺 [**youtube-seo-optimizer**](https://github.com/milad-ahmd/youtube-seo-optimizer) | YouTube titles, thumbnails, descriptions, chapters and channel audits for more views | *"Optimize the title and description for this video"* |
| 🎬 [**youtube-script-writer**](https://github.com/milad-ahmd/youtube-script-writer) | High-retention YouTube and Shorts scripts with hooks, open loops and B-roll cues | *"Write a 10-minute YouTube script about X"* |
| ♻️ [**content-repurposer**](https://github.com/milad-ahmd/content-repurposer) | One video or podcast into 15+ posts: Shorts, Reels, threads, LinkedIn, carousel, newsletter | *"Repurpose this transcript into social posts"* |
| 🗓️ [**social-media-calendar**](https://github.com/milad-ahmd/social-media-calendar) | Monthly cross-platform content calendar, exported as CSV for Notion, Sheets, Buffer or Later | *"Plan my social media calendar for November"* |
| 🪝 [**hook-generator**](https://github.com/milad-ahmd/hook-generator) | Scroll-stopping hooks for videos, posts, emails and ads, scored and ranked | *"Give me hooks for a video about X"* |
| 📣 [**ad-copy-generator**](https://github.com/milad-ahmd/ad-copy-generator) | Ad copy for Meta, TikTok, Google and YouTube within each platform's limits, plus an A/B test matrix | *"Write Meta ads for my product"* |
| ✍️ [**seo-blog-writer**](https://github.com/milad-ahmd/seo-blog-writer) | SEO blog posts that rank: intent research, outline, meta tags, FAQ and schema | *"Write an SEO article for 'best budgeting apps'"* |
| 📧 [**email-campaign-writer**](https://github.com/milad-ahmd/email-campaign-writer) | Welcome, launch, cart and newsletter email sequences with subject-line tests | *"Write a 5-email welcome sequence"* |
| 🎨 [**brand-voice-guide**](https://github.com/milad-ahmd/brand-voice-guide) | A brand voice, positioning and personas guide, then rewrites copy on-brand | *"Create a brand voice guide from our website"* |
| 🕵️ [**competitor-spy**](https://github.com/milad-ahmd/competitor-spy) | Competitor analysis of pricing, messaging, socials, ads and reviews, with a SWOT and opportunities | *"Analyze my top 3 competitors"* |
| 🤝 [**influencer-outreach**](https://github.com/milad-ahmd/influencer-outreach) | Influencer and UGC campaigns: vetting, outreach DMs, briefs and tracking | *"Plan a micro-influencer campaign on TikTok"* |
| 📊 [**social-analytics-reporter**](https://github.com/milad-ahmd/social-analytics-reporter) | Social media analytics exports into a clear report with KPIs, reasons and next actions | *"Analyze this Instagram Insights export"* |

<div align="center"><img src="assets/demo-analytics.gif" alt="social-analytics-reporter summarizing an Instagram export" width="680"/></div>

## 💻 Developer Skills

### 🔀 Git & Workflow
| Skill | What it does | Try saying |
|---|---|---|
| 🔎 [**pr-reviewer-pro**](https://github.com/milad-ahmd/pr-reviewer-pro) | Senior-engineer PR reviews: severities, `file:line` evidence, suggested fixes | *"Review PR #123"* |
| 🧱 [**commit-craft**](https://github.com/milad-ahmd/commit-craft) | Messy changes → clean, atomic Conventional Commits | *"Commit my changes"* |
| 📋 [**meeting-notes-to-tickets**](https://github.com/milad-ahmd/meeting-notes-to-tickets) | Meeting notes → de-duplicated GitHub/Linear/Jira tickets | *"Turn these notes into issues"* |
| 📝 [**changelog-from-git**](https://github.com/milad-ahmd/changelog-from-git) | Human-readable release notes from git history | *"Write release notes since v1.2"* |

### 🧪 Testing & Quality
| Skill | What it does | Try saying |
|---|---|---|
| 🧪 [**test-gap-hunter**](https://github.com/milad-ahmd/test-gap-hunter) | Finds the riskiest untested code and writes real tests | *"Find test gaps and fix the top 3"* |

### 🎨 Frontend
| Skill | What it does | Try saying |
|---|---|---|
| 🖼️ [**screenshot-to-component**](https://github.com/milad-ahmd/screenshot-to-component) | Screenshot → component in *your* design system | *"Build this screenshot as a component"* |
| ♿ [**a11y-fixer**](https://github.com/milad-ahmd/a11y-fixer) | WCAG 2.2 AA audit that fixes issues in code | *"Make this app accessible"* |

### 🧙 Refactoring
| Skill | What it does | Try saying |
|---|---|---|
| 🧙 [**migration-wizard**](https://github.com/milad-ahmd/migration-wizard) | Safe, phased migrations: JS→TS, Jest→Vitest, Pages→App Router | *"Migrate this project to TypeScript"* |

### 🔐 Security & Maintenance
| Skill | What it does | Try saying |
|---|---|---|
| 🔐 [**env-secrets-guard**](https://github.com/milad-ahmd/env-secrets-guard) | Catches leaked API keys before you push | *"Scan for leaked secrets"* |
| 🩺 [**dependency-doctor**](https://github.com/milad-ahmd/dependency-doctor) | Vulnerable, outdated and unused packages with a safe upgrade plan | *"Check my dependencies"* |

### 🐳 DevOps & Backend
| Skill | What it does | Try saying |
|---|---|---|
| 🐳 [**dockerize-anything**](https://github.com/milad-ahmd/dockerize-anything) | Small, secure, multi-stage Dockerfile and compose | *"Dockerize this project"* |
| 🔌 [**api-to-sdk**](https://github.com/milad-ahmd/api-to-sdk) | Typed, idiomatic SDK from OpenAPI or your routes | *"Generate a TS SDK from openapi.yaml"* |
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

All 31 skills have shipped. Next up is your idea:

👉 [Request a skill](https://github.com/milad-ahmd/agent-skills/issues/new). Popular requests get built first.

## 🤝 Contributing

Ideas, bug reports and new skills are welcome. Each skill lives in its own repo, so open issues there. You can also browse community skills in [awesome-agent-skills](https://github.com/milad-ahmd/awesome-agent-skills).

## 📄 License

MIT © [Milad Ahmadi](https://github.com/milad-ahmd)

<div align="center">

### ⭐ If these skills save you time, star the repo so other developers can find them.

</div>
