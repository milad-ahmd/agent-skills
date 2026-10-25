---
name: commit-craft
description: Turns messy working-tree changes into clean, atomic Conventional Commits, splitting unrelated changes into separate commits with clear messages. Use when the user asks to commit, write a commit message, split changes into commits, or clean up git history before a PR.
---

# Commit Craft

Make the git history something reviewers enjoy reading.

## Workflow

1. Inspect state (read-only):
   ```bash
   git status --porcelain
   git --no-pager diff --stat; git --no-pager diff
   git --no-pager diff --cached
   git --no-pager log --oneline -15   # learn the repo's existing message style
   ```
2. **Group changes by intent**, not by file. One commit = one reason to change (a feature, a fix, a refactor, docs, deps, formatting). Formatting-only and dependency bumps always go in their own commits.
3. **Propose the plan first** and wait for confirmation:
   ```text
   1. feat(auth): add refresh-token rotation        → src/auth/token.ts, src/auth/session.ts
   2. test(auth): cover refresh-token rotation      → tests/auth/token.test.ts
   3. chore(deps): bump jsonwebtoken to 9.0.2       → package.json, package-lock.json
   ```
4. Execute each commit with explicit paths (`git add <paths>`, or `git add -p` for files containing mixed intents), then `git commit`.
5. Show the final `git log --oneline` for the new commits.

## Message format (Conventional Commits)

```text
<type>(<optional scope>): <imperative summary, ≤ 72 chars, no period>

<optional body: WHY the change was made, wrapped at 72 chars>

<optional footer: BREAKING CHANGE: ..., Closes #123>
```

Types: `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `style`, `build`, `ci`, `chore`, `revert`.

## Rules

- If the repo already uses a different convention (for example gitmoji or ticket prefixes), **follow the repo**.
- Use the imperative mood ("add", not "added" or "adds"). Describe behaviour, not files.
- Never commit secrets, `.env` files, build output or huge binaries. Warn instead.
- Never run `git push`, `rebase`, `reset --hard` or `--amend` on pushed commits unless the user explicitly asks.
- Mark breaking changes with `!` (for example `feat(api)!:`) and add a `BREAKING CHANGE:` footer.
