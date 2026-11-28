---
name: commit-craft
description: Turns messy working-tree changes into clean, atomic commits in the repo's own message style, splitting unrelated changes into separate commits with clear messages; Conventional Commits when the repo uses them or the user asks. A read-only script lists staged, unstaged and untracked files with line counts and a kind (source, tests, docs, deps, CI, config), flags whitespace-only changes, learns the message style from git log and lints a draft message. Use when the user asks to commit, write a commit message, split changes into commits, or clean up git history before a PR.
---

# Commit Craft

Make the git history something reviewers enjoy reading, in the style the repo already uses.

## 1. Collect the facts (read-only)

```bash
python3 scripts/commit_facts.py            # changes by kind, formatting-only files, groups, house style
python3 scripts/commit_facts.py --json     # the same, machine-readable
python3 scripts/commit_facts.py -n 300     # learn the style from more history
```

The script never stages or commits, and it runs git with optional locks off so even `git status` leaves the index alone. It lists staged, unstaged and untracked files with `+added -deleted` (a plain line count for untracked files, "binary" where there is no text). Each file gets a kind from its path: `source`, `tests`, `docs`, `deps` (a manifest or lockfile), `ci` or `config`. A file whose change vanishes under `git diff -w` is marked "formatting only". Files that are both staged and unstaged are called out, because a commit takes only the staged part, and so are conflicted files. The suggested groups are top-level directory x kind, with formatting-only files in a group of their own.

The house style comes from the last 100 non-merge commits. The verdict is `conventional`, `area-prefix` (Go, Linux and Node.js write `net/http: fix ...`), `mixed` or `free-form`, with a confidence. Around it you get the types and scopes in use, the median subject length, the capitalisation and trailing-period habits, how often commits have a body, and the common trailers such as `Signed-off-by` or `Change-Id`. It also prints the last eight subjects: always read them, because ticket keys, emoji and other habits do not show up in the shares.

Exit codes: `0` report written or lint passed · `1` lint found errors · `2` not a git repository, a git error, or an unreadable message file.

## 2. Plan the commits

Group by intent, not by file. One commit has one reason to change: a feature, a fix, a refactor, docs, a dependency bump or formatting. The script's groups are only a starting point. A fix and its test usually belong together, and one source file can hold two intents, which is what `git add -p` is for.

Formatting-only files and dependency bumps always get commits of their own. The `deps` kind comes from the path alone, so read a `package.json` or `pyproject.toml` diff before you call it a bump. Read any diff you are unsure about with `git --no-pager diff -- <path>` (add `--cached` for the staged side). For a file that is staged and unstaged, ask whether the user wants both parts in.

**Propose the plan and wait for confirmation**, written in the detected style:

```text
1. feat(auth): add refresh-token rotation        → src/auth/token.ts, src/auth/session.ts, tests/auth/token.test.ts
2. style: reformat utils                         → src/utils/format.ts (formatting only)
3. chore(deps): bump jsonwebtoken to 9.0.2       → package.json, package-lock.json
```

Once the user agrees, commit each group with explicit paths (`git add <paths>`, or `git add -p` for mixed files), then `git commit`. Show the final `git log --oneline` for the new commits.

## 3. Write and lint each message

Follow the house style, not a favourite one. In a `conventional` repo write `type(scope)!: summary` with the types and scopes it already uses. In an `area-prefix` repo start with the area changed, as in `http2: fix stream leak`. In a `free-form` or `mixed` repo write plain imperative summaries with the repo's capitalisation and period habit, and do not add `feat:` prefixes unless the user asks for Conventional Commits. Add the trailers the repo uses (`git commit -s` for `Signed-off-by`), and a body when the repo writes them or the reason is not obvious. When the confidence is not `high`, read `git --no-pager log --oneline -20` and any CONTRIBUTING or commitlint config before you decide.

Then check the draft:

```bash
python3 scripts/commit_facts.py --lint "fix(core): stop double retries"
python3 scripts/commit_facts.py --lint-file .git/COMMIT_EDITMSG
python3 scripts/commit_facts.py --lint "..." --conventional   # enforce CC even if the repo does not use it
```

Errors fail with exit 1. They cover a subject over 72 characters (only a warning in a repo whose median subject is longer) and a second line that is not blank. Where Conventional Commits apply, they also cover a subject that is not `type(scope)!: description` and a breaking footer not spelled `BREAKING CHANGE: ` or `BREAKING-CHANGE: `. Warnings never fail. They flag a subject over 50 characters, body lines over 72, a type the repo has never used (when CC applies), and a clash with the repo's case, period or prefix habit. Merge, revert and `fixup!` subjects are left alone. A `--lint-file` is cleaned the way git cleans an edited message (`#` lines and the scissors line go); a `--lint` string is taken as given, like `git commit -m`. Fix the errors and mention the warnings. Where 50 and 72 come from is in [references/sources.md](references/sources.md).

## Message reminders

Use the imperative mood ("add", not "added" or "adds") and describe behaviour rather than files. The body says why the change was made, wrapped at about 72 characters. With Conventional Commits, mark a breaking change with `!` (`feat(api)!:`) and add a `BREAKING CHANGE: ` footer; the usual types are `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `style`, `build`, `ci`, `chore` and `revert`.

## What the script cannot see

It does not know what a change means, since kinds come from paths. It cannot split hunks with mixed intents inside one file. It misses blank-line-only formatting (`-w` keeps blank lines) and does not follow renames, which show as a deletion plus an addition. It assumes `#` as the comment character, does not measure ticket-key or gitmoji habits, and cannot read style rules kept outside the history, such as a commitlint config or CONTRIBUTING.md. It reads every untracked file to count lines, so an unignored dependency folder makes it slow. The style verdict is a judgement from shares, so read the log when its confidence is low or medium.

## Rules

- If the repo already uses a convention (gitmoji, ticket prefixes, `area:` prefixes), **follow the repo**.
- Never commit secrets, `.env` files, build output or huge binaries. Warn instead.
- Never run `git push`, `rebase`, `reset --hard` or `--amend` on pushed commits unless the user explicitly asks.
