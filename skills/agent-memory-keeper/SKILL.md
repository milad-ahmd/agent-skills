---
name: agent-memory-keeper
description: Carries a coding agent's context across sessions with two files. MEMORY.md holds durable project lessons (verified commands, conventions, decisions, gotchas, user preferences); it is loaded at the start of work and updated whenever the user corrects the agent. HANDOFF.md holds the state of the current task (goal, status checklist, exact next step, failed attempts, open questions, branch and uncommitted files); it is written when a session ends mid-task and checked against git when the next session resumes. Use when the user says "remember this", "don't forget", "forget that note" or "what do you remember", says "write a handoff", "I'm running out of context", "continue this in a new session", "pick up where we left off", or "hand this to another agent or a teammate", and at the start of any session in a repo with MEMORY.md or HANDOFF.md.
---

# Agent Memory Keeper

Two files with different jobs. Don't mix them.

| | MEMORY.md | HANDOFF.md |
|---|---|---|
| Holds | Lessons that stay true: commands, conventions, decisions, gotchas, preferences | The state of one task: where it stands and what to do next |
| Lifetime | The project's. Edited in place | One task. Overwritten at each handoff, deleted or archived when the task is done |
| Written | As soon as a lesson is learned | When a session ends with the task unfinished |

If it will still be true after this task ships, it belongs in MEMORY.md. If it only matters for this task, it goes in HANDOFF.md.

## Start of a session

1. Read `MEMORY.md` at the repo root if it exists. Follow it unless the user says otherwise.
2. If `HANDOFF.md` exists, run `python3 scripts/handoff.py --check HANDOFF.md`. Exit 0 means branch, HEAD and uncommitted files are as recorded. Exit 1 means drift (someone committed, switched branch, stashed or rebased): tell the user what changed before acting on the plan. Exit 2 means there was nothing to check.
3. Read the whole handoff, then start from "Next step". Don't retry anything under "Tried and failed" unless what it depended on has changed. Ask the open questions first if the next step depends on them.

## Memory mode

Write to MEMORY.md straight away, without being asked, when the user corrects you ("no, we use pnpm"), when you verify a command (test, build, lint, run locally) or hit a gotcha (flaky test, required env var, order of operations), when a decision is made (record the reason), when the user states a preference, or when they say "remember …".

Keep it under ~150 lines and update entries instead of appending duplicates:

```markdown
# Project Memory
_Last updated: 2026-10-25_

## Commands (verified)
- Test: `pnpm test -- --run` (vitest; plain `pnpm test` starts watch mode)
## Conventions
- API handlers live in `src/routes/*`, one file per resource
## Decisions
- 2026-10-12: Chose Zod over Yup, already used in the frontend
## Gotchas
- `DATABASE_URL` must point at the docker DB, or migrations hang
## Preferences
- Conventional Commits; never push without asking
```

"What do you remember?" gets a summary of the file. "Forget X" removes the entry and confirms. "Memory cleanup" merges duplicates, drops stale entries and re-runs the commands to re-verify them.

## Handoff mode

Use it when the user wants to stop and resume later, the context window is nearly full, or the work moves to another machine, agent or teammate.

1. Promote lessons first. Anything learned during the task that outlives it goes into MEMORY.md now.
2. Leave the tree the way the next session should find it (commit if the user wants that), then run `python3 scripts/handoff.py --write HANDOFF.md`. It records branch, HEAD, upstream with ahead/behind, uncommitted, staged and untracked files, stash count, commits and files changed since the branch left main, TODO/FIXME/XXX lines added, and any merge, rebase, cherry-pick or bisect in progress. It won't overwrite an existing file without `--force`.
3. Fill in every `<fill in …>` using `references/handoff-template.md`. The next step must be a command or `file:line`, never "continue the work". List failed attempts with the reason they failed. Keep the file under ~60 lines.
4. Tell the user where the file is and what the next step says. Ask whether to commit it (handing to a teammate through the branch) or keep it out of git (the default).

When the task is done, delete HANDOFF.md, or move it to `docs/handoffs/` if the user wants a record.

## The script

```bash
python3 scripts/handoff.py                      # print the facts
python3 scripts/handoff.py --json               # same, as JSON
python3 scripts/handoff.py --write HANDOFF.md   # skeleton; add --force to replace an old handoff
python3 scripts/handoff.py --check HANDOFF.md   # recorded state vs live repo, then prints the next step
```

Options: `-C DIR`, `--base REF` (when the default branch isn't origin/HEAD, main or master), `--limit N` (list cap, default 25). Exit codes: plain report 0, or 2 outside a git repo. `--write` 0, or 1 if it refused or failed; outside a repo it still writes the skeleton with no git state and prints a note. `--check` 0 match, 1 drift, 2 cannot check. It never stages, checks out or stashes, and the only file contents it prints are the TODO/FIXME/XXX lines.

## Integration

In Claude Code, add `@MEMORY.md` to `CLAUDE.md`. In Cursor, Cline or Codex, reference it from `.cursor/rules`, `.clinerules` or `AGENTS.md`. Don't import HANDOFF.md the same way, since it goes stale; add the line "If HANDOFF.md exists, check it with handoff.py and resume from it" instead. Commit MEMORY.md for team memory or gitignore it for personal memory, and ask the user which the first time.

## Rules

- Never store secrets, tokens, passwords or personal data in either file.
- Only record facts that were verified or that the user stated. Mark anything else `(unverified)`.
- Keep entries short, specific and dated. A long memory gets ignored, and a long handoff hides the next step.
