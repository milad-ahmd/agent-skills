---
name: skill-creator-pro
description: Designs and writes new Agent Skills (SKILL.md) from a described workflow. It produces a precise trigger description, a step-by-step workflow, rules, an output format, optional helper scripts, and a README, then validates the result with a linter. Use when the user wants to create, write, improve, or package an agent skill, SKILL.md, Claude skill, or reusable agent workflow.
---

# Skill Creator Pro

Turn "I keep explaining this to my agent" into a reusable skill.

## 1. Interview (keep it short)

1. What task should this automate? Ask for 2–3 real example requests the user would type.
2. What does a **great** result look like? What do agents usually get wrong today?
3. Which tools, commands, files or APIs are involved? Are there any irreversible actions?
4. Who will use it (personal, team, public) and in which agents?

## 2. Write the description first (it controls when the skill triggers)

`<What it does, concretely, with key nouns>. Use when the user asks to <trigger phrases 1>, <2>, <3>.`
- Third person, specific, at most about 1024 characters, and including the words users actually type.
- Test it in your head: would this description match the 3 example requests? Would it wrongly match unrelated requests?

## 3. Structure

```text
<skill-name>/                 # kebab-case, ≤ 64 chars, same as `name`
├── SKILL.md                  # frontmatter (name, description) + instructions, ideally < 500 lines
├── scripts/                  # deterministic helpers (stdlib-only where possible)
└── references/               # long docs loaded only when needed
```

SKILL.md body: **purpose (1 line) → workflow (numbered steps) → output format (template) → rules (safety and quality)**.
- Write instructions, not an essay. Use imperative steps and concrete commands.
- Put fragile, repetitive or exact operations (parsing, API calls) in **scripts**. Keep judgment in the instructions.
- Add **guardrails**: confirm before destructive or public actions, never invent facts, cite evidence.

## 4. Validate

```bash
python3 scripts/lint_skill.py path/to/<skill-name>
```

Fix every ❌ and review the ⚠️ warnings. Then dry-run the skill against the user's 3 example requests and refine.

## 5. Package (optional)

Write a README with a one-line pitch, install steps (`~/.claude/skills/` or the project's `.claude/skills/`), example prompts and a license. Offer to publish it as a GitHub repo, and to submit it to an awesome list.

## Rules

- Each skill does one job. Split skills that try to do everything.
- Never embed secrets. Read them from environment variables instead.
