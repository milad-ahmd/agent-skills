---
name: commit-roast
description: Roasts a repository's commit history with numbers, then turns the worst habits into commit message rules that fit the repo. A read-only script runs git log over a range and measures message length, one-word and lazy messages, a hall of shame of repeated messages like wip, fix, update and asdf, ALL CAPS, swearing (censored) and emoji, Conventional Commits adoption and types, imperative mood, subjects over 72 characters, big commits with no body, fix-the-fix chains, late-night and Friday-evening commits, reverts, giant commits, typo commits and issue references. It prints a 0-100 commit hygiene score with its formula and the five most roastable facts with sha evidence, with authors anonymous by default. Use when the user asks to roast our commits or a repo's commit history, asks how bad the commit messages in this repo are, wants a commit hygiene score, counts wip or "fix" commits, or wants commit message rules, a commit-msg hook or commitlint based on what the history does wrong.
---

# Commit Roast

Funny about the history, useful about the next commit. Every joke needs a number from the script behind it.

## 1. Get the numbers

```bash
python3 scripts/commit_stats.py                           # last 1000 commits of HEAD
python3 scripts/commit_stats.py -C path/to/repo --since "6 months ago"
python3 scripts/commit_stats.py main~300..main --max-count 0   # a range, no cap
python3 scripts/commit_stats.py --author "@acme.com" --json
python3 scripts/commit_stats.py --show-authors            # only if the user asks for names
```

The script only runs `git log`. Merge commits are counted on their own and left out of every message statistic. Times use each commit's author date in its own timezone, so 01:30 in Berlin is late night even if CI ran in UTC. Messages in the output are cut to 60 characters and swear words are censored (`s***`).

Exit codes: `0` report printed · `2` not a git repository, a git error, or no non-merge commits in the range.

How it decides: imperative mood checks whether the first word after any `type(scope):` prefix is a base verb from a built-in list ("add", "fix") or one of its other forms ("added", "fixes", "fixing"); other first words are unclassified. A fix-the-fix chain is two or more consecutive fix commits that share a file, each within an hour of the previous one. Big means over 200 changed lines; a body of only `Signed-off-by:` style trailers doesn't count.

## 2. Deliver the roast

Read the output first. If the hall of shame is a bot (`chore: rebuild site [skip ci]`, release tooling, Dependabot), say so and roast the humans' commits instead, or rerun with `--author`.

Start with the score card: the hygiene score and its seven parts, copied from the output, never rounded up. Then 5-8 roast lines, mostly from `MOST ROASTABLE`. Each one names a number and, where there is one, a short sha: "`wip` was the entire message 41 times. That's not a commit history, that's a loading bar." Close with one honest compliment the numbers support, such as 100% Conventional Commits or zero reverts.

Roast the habits and the history, never a person. Authors stay anonymous unless the user passes `--show-authors` or asks; even then, no ranking of colleagues in anything meant for sharing. Keep censored words censored.

## 3. Five rules for this repo

Write five rules that each fix something the numbers showed, worst first, with the number that earned it. Pick from these or write your own:

| The numbers show | Rule |
|---|---|
| one-word or lazy messages, a long hall of shame | Say what changed and why it was needed: "Fix retry loop that ignored timeout", not "fix" |
| low imperative rate | Write the subject as a command: "Add", "Fix", "Remove", not "Added" or "Fixes" |
| subjects over 72 characters | Keep the subject to about 50 characters, at most 72; details go in the body |
| big commits with no body | Over ~200 changed lines gets a body: what, why, and what you tested |
| fix-the-fix chains, typo commits | Run the tests before committing; squash follow-up fixes into the commit they fix before the PR (`git commit --fixup <sha>` then `git rebase -i --autosquash`) |
| giant commits | One reason to change per commit; split refactors, formatting and features |
| low issue refs | Put the ticket in the subject or a footer (`Refs PAY-123`, `#42`) |
| mixed or no prefixes | Adopt Conventional Commits: `type(scope)!: description` |
| late-night or Friday-evening share | Not a message rule. Mention it once, kindly, and move on |

Point to the `commit-craft` skill for writing good commits from staged changes, and to `pre-commit-setup` for installing hooks.

## 4. Offer enforcement (only if the user wants it)

A plain git hook, no dependencies. Git passes the message file as `$1` and aborts the commit on a non-zero exit. Save as `.git/hooks/commit-msg` and `chmod +x` it:

```sh
#!/bin/sh
subject=$(sed -n '/^#/d; /^[[:space:]]*$/d; p; q' "$1")
case "$subject" in Merge*|Revert*|fixup!*|squash!*) exit 0 ;; esac
[ ${#subject} -le 72 ] || { echo "commit-msg: subject is ${#subject} chars, keep it <= 72" >&2; exit 1; }
[ "$(echo "$subject" | wc -w)" -ge 2 ] || { echo "commit-msg: say what changed, not just '$subject'" >&2; exit 1; }
# Uncomment to require Conventional Commits:
# echo "$subject" | grep -Eq '^[a-z]+(\([^)]*\))?!?: .+' || { echo "commit-msg: use type(scope): description" >&2; exit 1; }
```

For JS/TS teams, commitlint with Conventional Commits: `npm install -D @commitlint/cli @commitlint/config-conventional`, a `commitlint.config.js` containing `export default { extends: ['@commitlint/config-conventional'] };`, and with husky, `echo "npx --no -- commitlint --edit \$1" > .husky/commit-msg`. Hooks can be skipped with `--no-verify`, so suggest running the same check in CI on PR commits.

## 5. Share line (optional)

```text
Our commit history scored 47/100. "wip" appears 41 times.
We have rules now.
```

## Do not

- Name, rank or quote individual authors unless the user asked for it, and never in something meant to be posted.
- Invent or round stats, or roast something the script didn't measure.
- Rewrite shared history with `git rebase`, `commit --amend`, `filter-repo` or force-push just to clean up old messages.
- Install hooks, add dependencies or edit CI without the user's go-ahead.
- Print uncensored swear words from commit messages.
