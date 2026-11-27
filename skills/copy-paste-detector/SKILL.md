---
name: copy-paste-detector
description: Finds duplicated code in a repository and merges the copies worth merging. A read-only script strips comments and whitespace, hashes every run of N code lines, extends matches to the longest shared blocks and groups them into clone families with file:line ranges, lines per copy, removable lines and whether each copy is identical, renamed (only names or literals changed, a second tier) or has drifted since it was pasted. It skips vendored, generated and fixture code, reports the duplicated share of code and the most-copied block, writes JSON and an SVG card with no paths, and can fail CI over a percentage. The agent splits families into real and coincidental duplicates, applies the rule of three, then extracts one family at a time into a helper module the code already uses, with tests after each. Use when the user asks to find duplicate or copy-pasted code, detect code clones, check how DRY a codebase is, deduplicate repeated logic, pull repeated blocks into a shared helper, or add a duplication check to CI.
---

# Copy-Paste Detector

A pasted block is harmless until someone fixes a bug in one copy and not the others. This skill finds the copies, shows which ones have already drifted apart, and merges only the ones that should be one thing.

## 1. Scan

```bash
python3 scripts/find_clones.py .                              # headline + top 10 families
python3 scripts/find_clones.py src --min-lines 10 --top 20    # bigger blocks only, more of them
python3 scripts/find_clones.py . --ext py,ts --exclude 'src/generated/*'
python3 scripts/find_clones.py . --json > /tmp/clones.json    # every family and copy
python3 scripts/find_clones.py . --svg clones.svg             # shareable card, no file paths (--show-paths adds them)
python3 scripts/find_clones.py . --fail-over 5                # CI: fail when exact copies pass 5% of code lines
```

The script only reads. It tokenizes each file, drops comments and whitespace, and ignores lines that are only braces or imports. Every window of `--min-lines` code lines (default 6, with at least `--min-tokens` 40 tokens) is hashed; equal windows are extended to the longest shared block, and overlapping matches become one family. A second pass replaces identifiers, strings and numbers with placeholders to find renamed copies. It skips hidden dirs, `node_modules`, `vendor`, `dist`, `build`, `migrations`, test fixtures, minified and generated files. On a laptop it scans about 100k code lines in one second.

Exit codes: `0` no families (or `--fail-over` given and exact duplication is at or under it) · `1` families found (or over the `--fail-over` budget) · `2` bad arguments, missing path, or no source files.

## 2. Read the numbers

| Field | Meaning |
|---|---|
| duplicated % | code lines (non-blank, non-comment) inside an exact copy, every copy counted |
| removable | lines saved if each family became one copy |
| `same` / `renamed` | identical to the family's most common version / only names or literals differ |
| `drifted` | lines were added, removed or changed in this copy; `lines_differ` says how many |
| `partial` | shares under half the block; it overlaps the family rather than copying it |
| `same file`, `tests only` | all copies in one file / all copies in test files |

Look at drifted copies first. Compare one with its family's `same` copy, e.g. `diff <(sed -n 120,181p a.py) <(sed -n 88,149p b.py)`, and check history with `git log -L 120,181:a.py --oneline`. A fix that reached one copy and not the others is a bug, not a refactor: report it, and once the user agrees, fix it in its own commit.

## 3. Triage each family

Open every copy in the top families, by removable lines with drifted ones first, and decide whether the copies mean the same thing.

Merge when they must change together: one business rule, one validation, one retry pattern for the same API. If a fix in one copy would be needed in all of them, they are one thing written twice.

Leave them when the likeness is a coincidence. Test setup blocks, two validators for unrelated forms that look alike today, framework boilerplate and data tables will change for different reasons, and merging them couples code that shouldn't know about each other. A copy that is missing a fix the others have goes to the drift step above before anything is merged.

Apply the rule of three (Don Roberts, via Fowler's *Refactoring*): the first time you write something, just write it; the second time, wince and duplicate it anyway; the third time, extract it. Two copies earn a merge only when they must change together, for example when history shows the same fix landing in both. Sandi Metz's point in "The Wrong Abstraction" also holds: duplication is cheaper than a shared function that needs flags or mode parameters to serve callers that have drifted apart. If the merged version needs a boolean to pick a branch, leave the copies.

## 4. Merge one family at a time

1. Run the tests first and note anything already failing.
2. Find where shared code already lives near the copies (a `utils`, `helpers`, `common` or `lib` module, a base class, a shared package) and how callers import it. Put the helper there. Do not start a new utility module next to an existing one.
3. Extract the family's `same` version. Make a parameter only for what differs between copies; the `renamed` copies show which names and literals those are.
4. Replace each copy with a call, run the tests, and commit that family on its own.
5. Re-run the scan and quote the drop in duplicated % and removable lines.

## Report

```
Copy-paste report: 6.2% of 48,210 code lines are exact copies (41 families, ~1,620 lines removable)
Biggest: #1, 3 copies x 58 lines · Most copied: #4, one block exists 5 times

| #  | Copies x lines | Status        | Decision                       | Why                                          |
| 1  | 3 x 58         | 1 drifted     | merge into billing/totals.py   | one tax rule; drifted copy lacks rounding fix |
| 4  | 5 x 14         | same, tests   | leave                          | per-test setup, changes per test              |
```

Then the families merged so far, test results after each, and the before and after numbers. Give the SVG path if the user wants the card.

## Do not

- Merge because code looks alike. Merge because it means the same thing and changes together.
- Create a helper with one caller, or a new `utils` module when the codebase already has one.
- Add boolean flags or mode parameters so one function can serve copies that have diverged.
- Merge several families in one commit, or move on without running the tests.
- Edit generated, vendored or migration code; change the generator or leave it out with `--exclude`.
- Treat the percentage as a quality grade or a target to drive to zero. Some duplication is the cheaper choice.
- Change code before the user has picked which families to merge. The scan and the report come first.
