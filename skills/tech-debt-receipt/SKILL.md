---
name: tech-debt-receipt
description: Prints a repository's tech debt as a shop receipt people screenshot and share, then a ranked plan to pay it down. A read-only script finds every TODO, FIXME, HACK, XXX, temporary workaround, @deprecated and lint or type-check suppression (eslint-disable, noqa, type ignore, @ts-ignore, nolint, @SuppressWarnings) in tracked files, ages each one with git blame, notes whether it links a ticket, and prices it by kind, age and how often its file changed in the last 180 days. Output is a 42-column receipt with subtotals and TOTAL DEBT, an SVG image of it, JSON, and a "pay these first" list; a --fail-over budget turns it into a CI check. Use when the user asks to find or count TODO and FIXME comments, see how old the TODOs are, audit eslint-disable or noqa suppressions, make a tech debt receipt or report, list stale hacks and workarounds, decide which TODOs to fix first, or fail CI when the TODO count or debt grows.
---

# Tech Debt Receipt

Every TODO is a small loan. This skill prints the bill, then helps pay off the expensive part first.

## 1. Print the receipt

```bash
python3 scripts/debt_receipt.py                        # receipt, then "pay these first"
python3 scripts/debt_receipt.py --svg receipt.svg      # also a receipt-shaped image
python3 scripts/debt_receipt.py --svg receipt.svg --no-credit   # same card without the small "made with agent-skills" line
python3 scripts/debt_receipt.py --json                 # every item with age, ticket, churn, price
python3 scripts/debt_receipt.py src/ --items 0         # one directory, every line item
python3 scripts/debt_receipt.py --currency points      # "12.4 pts" instead of "$12.40"
```

The script only reads. It scans `git ls-files` (so ignored files are out), and skips binaries, lockfiles, vendored directories, minified files and generated files. It reads comments for `#`, `//`, `/* */`, `--` and `<!-- -->` languages and ignores markers inside string literals, Markdown code spans, and quoted mentions like `"TODO"`. Python docstrings are treated as strings, so TODOs in them are missed. Other options: `--top N`, `--max-blame N` (per-file cap on blamed lines for huge repos; the receipt says when it applied), `--now YYYY-MM-DD`, `--show-authors`.

Exit codes: `0` receipt printed · `1` `--fail-over N` given and TOTAL DEBT is over N · `2` not a git repository, a git error, or no scannable files.

## 2. Show it

Paste the receipt verbatim in a code block so the columns line up, and mention the SVG path if one was written. Say in one line what the price means: the formula is printed at the bottom of the receipt.

| Factor | Where it comes from |
|---|---|
| base | FIXME and HACK 8, XXX 6, temporary 5, deprecated 4, TODO 3, suppressions 2-6 (`@ts-ignore` 6, `@ts-expect-error` 3, `pragma: no cover` 2) |
| x (1 + age in years) | `git blame -w` of the marker line, honouring `.git-blame-ignore-revs`. This is when the line last changed, so a reformat makes debt look younger |
| x (1 + commits / 10, max 3) | commits that touched the file in the last 180 days |
| x 2 | blanket suppression that names no rule (`# noqa`, `//nolint`, bare `eslint-disable`) |
| 50% off | the comment links a ticket: `#123`, `ABC-45`, `gh-7` or a URL |

The price ranks items; it is not an effort estimate.

## 3. Explain the top 5

For each item in "pay these first" (or `pay_first` in the JSON):

1. Read the code around it (`sed -n '<line-15>,<line+15>p' <path>`) and say in one or two sentences what the debt actually is.
2. Check whether it is still real. The bug may be fixed already, the deprecated function may have no callers left (grep for it), the temporary workaround may guard a library version the project no longer uses.
3. For a suppression, remove it locally and run the linter or type checker on that file to see whether the warning still fires. Put the suppression back afterwards.
4. Say what the fix is and how big it is, from a one-line change to a small PR to something that needs a design decision first.

## 4. Offer next steps

Ask which of these the user wants. Do none of them unasked.

- **Turn the top items into issues.** Draft one per item: title, `path:line`, the quoted comment, its age, why it ranks high, the suggested fix. Create them (for example with `gh issue create`) only after the user approves the list. Then add the issue link to each comment, so the next receipt applies the ticket discount.
- **Fix the cheap ones in one small PR.** Obsolete TODOs whose work is done, suppressions the linter no longer needs, `@ts-ignore` changed to `@ts-expect-error` (TypeScript reports an unused `@ts-expect-error`, so it cannot outlive the error). Run the tests and the linter before proposing the PR.
- **Add a CI budget.** Run `python3 scripts/debt_receipt.py --fail-over <N>` with N a little above today's total. Blame needs full history: on GitHub Actions set `fetch-depth: 0` on `actions/checkout`, otherwise every line looks as old as the shallow clone. Prices grow with age, so the total rises slowly even with no new debt; tell the user to raise the budget on purpose or pay something off.

## Do not

- Shame or rank people. Authors are hidden unless `--show-authors` is passed, and blame only shows who last touched the line, not who wrote the debt. Talk about code.
- Delete a TODO, FIXME or HACK comment without doing the work or the user agreeing it is obsolete. Removing the comment hides the debt; it does not pay it.
- Mass-remove suppressions with a search-and-replace. Remove them one at a time and run the linter or type checker after each.
- Create issues, commit, open PRs or edit CI config without the user's go-ahead.
