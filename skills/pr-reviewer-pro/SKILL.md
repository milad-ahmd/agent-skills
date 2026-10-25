---
name: pr-reviewer-pro
description: Reviews a pull request or local branch diff like a senior engineer, covering correctness, security, performance, naming, and missing tests, and produces prioritized, actionable comments (optionally posted to GitHub). Use when the user asks to review a PR, a diff, a branch, or "check my changes before I push".
---

# PR Reviewer Pro

Give a review that a senior engineer would be proud of: few, high-signal, actionable comments.

## 1. Collect the diff

Use the helper script (works for both cases):

```bash
bash scripts/collect_diff.sh            # local: current branch vs default branch
bash scripts/collect_diff.sh 123        # GitHub PR #123 (needs `gh` CLI)
```

Then read surrounding code for every changed hunk; never review a hunk in isolation.
Also read the PR description / commit messages to understand the *intent*.

## 2. Review checklist (in priority order)

| Severity | Look for |
|---|---|
| 🔴 **Blocker** | Bugs, broken logic, data loss, race conditions, unhandled errors, security holes (injection, authz bypass, secrets in code, unsafe deserialization, SSRF, XSS), breaking API changes without migration |
| 🟠 **Major** | N+1 queries, unbounded loops/memory, missing input validation, missing tests for new logic, wrong abstractions, concurrency issues |
| 🟡 **Minor** | Naming, readability, duplicated code, dead code, missing types, unclear errors, logging gaps |
| 💡 **Nit / Praise** | Style suggestions (only if no linter covers it) and genuine praise for good work |

Also check: does the change actually do what the PR says? Are docs/changelog/migrations updated?

## 3. Rules

- **Evidence or silence.** Every comment cites `file:line` and explains *why* it matters. No vague "consider refactoring".
- **Suggest the fix.** Include a code snippet with the proposed change whenever possible.
- **Respect project conventions.** Judge against the repo's existing patterns, linters and style, not your personal taste.
- **Max ~10 comments.** Merge duplicates; drop nits if there are blockers.
- **Don't flag what tooling catches** (formatting when Prettier/Black/gofmt is configured).
- If unsure, phrase it as a question rather than a claim.

## 4. Output format

```markdown
## PR Review: <title>

**Verdict:** ✅ Approve | 💬 Approve with comments | ❌ Request changes
**Summary:** 2–3 sentences on what the PR does and overall quality.

### 🔴 Blockers
1. `path/file.ts:42`: <problem>. <why it matters>.
   ```suggestion
   <fixed code>
   ```
### 🟠 Major
### 🟡 Minor
### 💡 Nits & praise
### 🧪 Suggested tests
- <specific test case that is missing>
```

## 5. Posting to GitHub (only if the user asks)

```bash
gh pr review <number> --comment --body-file review.md          # general comment
gh pr review <number> --request-changes --body-file review.md  # if blockers exist
```

Always show the review to the user and get confirmation before posting.
