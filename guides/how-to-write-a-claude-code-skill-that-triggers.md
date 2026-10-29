---
title: How to Write a Claude Code Skill That Actually Triggers
description: A practical guide to writing SKILL.md files that Claude Code loads at the right time, with a description formula, before/after examples, a linter and a trigger test, based on building 51 real skills.
date: 2026-10-26
---

You wrote a skill, installed it in `~/.claude/skills/`, asked Claude to do the exact thing it's for, and nothing happened. Or the opposite happens: it fires on unrelated requests and gets in the way.

In almost every case the problem is not the instructions inside the skill. It's **one line**: the `description`.

I learned this while building [51 open-source skills](https://github.com/milad-ahmd/agent-skills). This guide covers what actually makes a skill trigger, with examples you can copy.

## How Claude decides to use a skill

A skill is a folder with a `SKILL.md` file:

```markdown
---
name: pr-reviewer-pro
description: Reviews a pull request or branch diff like a senior engineer...
---

# PR Reviewer Pro
(instructions the agent follows)
```

At the start of a session, Claude doesn't read every skill. It only sees each skill's **name and description**. When your request comes in, it compares the request against those descriptions and loads the full instructions only for the ones that match.

So the description is the **only thing** Claude sees when it decides. A perfect workflow with a vague description will never run.

> If you set `disable-model-invocation: true`, the skill only runs when you call it by hand, and the description matters much less. This guide is about skills Claude should pick up **on its own**.

## The description formula

Every one of my 51 skills uses the same two-part structure:

```text
<What it does, concretely, with the key nouns>. Use when the user asks to <trigger phrase 1>, <trigger phrase 2>, or <trigger phrase 3>.
```

**Part 1: what it does.** Name the concrete objects and outputs: "pull request", "Dockerfile", "git history", "Instagram captions". Abstract words like "helps", "assists" or "improves" give the model nothing to match.

**Part 2: when to use it.** List the phrases people actually type. Users don't say "perform a dependency vulnerability assessment". They say "update my packages" or "fix npm audit warnings".

## Before and after

### ❌ Too vague: never triggers

```yaml
description: Helps with code quality.
```

It matches nothing specific, or everything at once. Claude can't tell when "code quality" means this skill.

### ❌ Accurate but missing triggers

```yaml
description: Analyzes project dependencies for vulnerabilities and outdated versions.
```

Better, but it describes what the skill *is*, not when to *use* it. "Can you update my packages?" may not match.

### ✅ Concrete, with real trigger phrases

```yaml
description: Audits project dependencies for vulnerabilities, outdated and unused packages, and produces a safe, ordered upgrade plan with breaking-change notes. Use when the user asks to update dependencies, fix npm/pip audit warnings, check for vulnerable packages, or plan a major-version upgrade.
```

This is the real description of [dependency-doctor](https://milad-ahmd.github.io/agent-skills/skills/dependency-doctor/). It names the objects (dependencies, packages, audit warnings) and lists four ways people actually ask.

## How long should the description be?

Across my 51 skills, descriptions run from **254 to 526 characters, with a median of 376**. That's two to four sentences.

- **Under about 80 characters:** almost always too vague to trigger reliably.
- **Around 250–500:** room for what it does plus three or four trigger phrases.
- **Over 1024:** too long. Trim it and move details into the body.

## Avoid overlapping skills

Two skills with similar descriptions compete, and Claude may load the wrong one or both. This shows up quickly once you have more than about ten skills.

For example, a "write marketing posts" skill and a "write social media content" skill will fight over every request. Fix it by giving each a **distinct trigger zone**:

- `content-repurposer`: "Use when the user asks to repurpose, recycle, or turn a video, podcast, or article into social posts."
- `hook-generator`: "Use when the user asks for hooks, opening lines, headlines, or how to start a video or post."

Same broad area, but no shared trigger phrases.

## Keep the body short and procedural

Once the skill triggers, the body takes over. What works best:

1. **One line of purpose**, then a **numbered workflow**: investigate, then act, then verify.
2. **An output template**, so results look the same every time.
3. **A Rules section** with guardrails: never invent facts, cite evidence, ask before destructive or public actions.
4. **Scripts for deterministic work.** Parsing, API calls and scanning belong in `scripts/`, not in prose the model has to re-interpret every time.

My skills have a median of **47 lines** and a maximum of 69. Long background material belongs in a separate `references/` file that the skill reads only when needed.

## Lint your skills automatically

I got tired of checking these rules by hand, so I wrote [skills-doctor](https://milad-ahmd.github.io/agent-skills/skills/skills-doctor/), a small Python script with no dependencies. It checks every installed skill for:

- missing or broken frontmatter
- a `name` that doesn't match its folder or isn't kebab-case
- descriptions that are too short, too long, or missing "Use when" language
- referenced scripts that don't exist
- likely leaked secrets
- pairs of skills whose descriptions overlap heavily

```bash
python3 scripts/skills_doctor.py ~/.claude/skills
```

```text
❌ my-skill
     ❌ referenced file missing: scripts/do_it.sh
     ⚠️  description is short (16 chars): may never trigger
     ⚠️  description lacks 'Use when…' trigger language
✅ roast-my-github

5 skills · 3 errors · 2 warnings · 0 overlaps
```

Install it with `npx skills add milad-ahmd/agent-skills --skill skills-doctor`.

## Test triggering before you ship

For every skill, write **three prompts that should trigger it and two that shouldn't**:

| Prompt | Should trigger dependency-doctor? |
|---|---|
| "update my npm packages" | ✅ yes |
| "npm audit shows 12 vulnerabilities, fix them" | ✅ yes |
| "plan our upgrade to React 19" | ✅ yes |
| "review this pull request" | ❌ no |
| "add a dependency injection container" | ❌ no |

Run each one in a fresh session. If a "yes" prompt doesn't trigger, add its wording to the description. If a "no" prompt triggers, the description is too broad.

## Checklist

- The description starts with what the skill does, using concrete nouns
- It includes "Use when the user asks to" with three or four real phrases
- It's roughly 250 to 500 characters
- No other installed skill uses the same trigger phrases
- The body is a short numbered workflow, with an output format and rules
- Deterministic steps are scripts, not prose
- skills-doctor passes with no errors
- It passed the 3-yes / 2-no trigger test

## Copy a working example

Every skill in [milad-ahmd/agent-skills](https://github.com/milad-ahmd/agent-skills) follow this structure and are MIT licensed, so use any of them as a template. To start from scratch, [skill-creator-pro](https://milad-ahmd.github.io/agent-skills/skills/skill-creator-pro/) interviews you about your workflow and writes the SKILL.md for you.
