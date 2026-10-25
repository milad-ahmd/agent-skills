---
name: agent-memory-keeper
description: Gives your coding agent a persistent project memory. It maintains a concise MEMORY.md of verified commands, conventions, decisions, gotchas, and user preferences, reads it at the start of work, and updates it whenever the user corrects the agent or a lesson is learned, so the same mistakes are not repeated across sessions. Use at the start of any session in a project with MEMORY.md, or when the user says "remember", "don't forget", "you did this wrong again", "what do you remember", or "forget".
---

# Agent Memory Keeper 🧠

Stop re-learning the same project every session.

## When to read

At the **start of every task** in a repo, read `MEMORY.md` at the repo root if it exists. Obey it unless the user says otherwise.

## When to write (immediately, without waiting to be asked)

- The user **corrects** you ("no, we use pnpm", "don't touch generated files").
- You discover a **verified command** (how to test, build, lint or run locally) or a **gotcha** (a flaky test, a required env var, an order of operations).
- A **decision** is made, with the reason for it.
- The user states a **preference** (code style, commit style, review strictness, language).
- The user says "remember …".

## Format (keep it under ~150 lines; update entries instead of appending duplicates)

```markdown
# Project Memory
_Last updated: 2026-10-25_

## Commands (verified)
- Test: `pnpm test -- --run` (vitest; plain `pnpm test` starts watch mode)
## Conventions
- API handlers live in `src/routes/*`, one file per resource
## Decisions
- 2026-10-12: Chose Zod over Yup: already used in the frontend
## Gotchas
- `DATABASE_URL` must point at the docker DB, or migrations hang
## Preferences
- Conventional Commits; never push without asking
## Open threads
- Migrating auth to v2: halfway, see `docs/auth-v2.md`
```

## Commands the user can say

- "What do you remember?" → summarise MEMORY.md.
- "Forget X" → remove the entry and confirm.
- "Memory cleanup" → merge duplicates, delete stale entries, re-verify the commands.

## Integration

- **Claude Code**: add `@MEMORY.md` to `CLAUDE.md` so it loads automatically.
- **Cline / Cursor / Codex**: reference it from `.clinerules`, `.cursor/rules` or `AGENTS.md`.
- Team memory: commit the file. Personal memory: add it to `.gitignore`. Ask the user which they prefer the first time.

## Rules

- **Never store secrets, tokens, passwords or personal data.**
- Only record facts that have been verified, or that the user stated. Mark anything uncertain with `(unverified)`.
- Keep entries short, specific and dated. Memory that is too long gets ignored.
