---
name: codebase-valuation
description: Estimates what a codebase would cost to build from scratch, the "this repo is worth $2M" number people post, done honestly. A read-only script counts files, code, comment and blank lines per language (git ls-files, so .gitignore applies; lockfiles, vendored, minified and generated files skipped), applies Boehm's basic COCOMO organic model with the SLOCCount and scc defaults to get person-months, schedule, team size and cost, and sets that next to what git shows really happened (author count and history span). Salary, overhead and currency are adjustable, there is JSON output and a shareable SVG card, and every output carries the caveat that COCOMO is a 1981 estimate, not a valuation. Use when the user asks how much their repo or project would cost to build or rewrite, what the codebase is worth, for a COCOMO or scc-style cost estimate, how many developers and how long a rewrite needs, how many lines of code per language, or wants a cost-to-build card for a README or post.
---

# Codebase Valuation

"This repo would cost $1.2M to build" posts are fun and usually unsourced. This skill gives the same number with its method, its inputs and its limits printed next to it, plus what git says actually happened.

## 1. Run it

```bash
python3 scripts/valuation.py                         # repository in the current directory
python3 scripts/valuation.py -C path/to/repo
python3 scripts/valuation.py --svg valuation.svg     # also write a 1200x630 card
python3 scripts/valuation.py --svg valuation.svg --no-credit   # same card without the small "made with agent-skills" line
python3 scripts/valuation.py --salary 95000 --overhead 2.0
python3 scripts/valuation.py --salary 80000 --currency EUR
python3 scripts/valuation.py --json                  # every number, for follow-up questions
python3 scripts/valuation.py --show-authors          # author names with commit counts
```

The script only reads. Inside a git work tree it counts tracked files (`git ls-files`); elsewhere it walks the directory. Lockfiles are skipped. So is anything under `node_modules`, `vendor` or a build directory, along with minified and generated files and docs or data files such as Markdown and JSON. The skip counts are printed so the user can see what was left out.

The model is basic COCOMO in organic mode (Boehm, 1981), on code lines only. Effort is 2.4 x KLOC^1.05 person-months. Schedule is 2.5 x effort^0.38 months, and people is effort divided by schedule. Cost is effort / 12 x salary x overhead.

By default the salary is 56,286 USD a year and the overhead 2.4, the same as SLOCCount and scc. That salary is a year-2000 US average, so suggest `--salary` with a current local figure when the user cares about the amount. Any currency other than USD needs `--salary` in that currency. Sources for every constant are in `references/sources.md`.

From git it reports the commit count and how many distinct author emails there are. It also gives the dates of the first and last commit. Names stay hidden unless the user asks for them with `--show-authors`. In a subdirectory only commits that touched it are counted. A shallow clone is flagged because its history is incomplete.

Exit codes: `0` estimate printed · `2` bad arguments or no code found (also when the SVG could not be written). On `2` with "No code found", tell the user which skip reasons the script listed; do not guess at a number.

## 2. Show the result

Start with the per-language table as printed, then the skip line. Give the estimate next, with the salary and overhead that produced it.

Then the comparison, for example "estimated 6.7 people for 14.1 months; git shows 2 authors and 62 commits over 1.1 months". The author count is everyone who ever committed, not the size of the team at any one time. Say so if the user reads it as a team size.

Explain the caveat in one plain sentence. Keep it short. COCOMO is a 1981 model fitted to projects of that era, so this is a rough estimate of the effort to rewrite the code. It is not a price, and lines of code say little about value.

If no card was asked for, offer one for a README or a post. In a README it goes in as an image: `![Cost to build](valuation.svg)`.

## 3. Follow-ups

When the user asks what happens with a different salary, rerun with `--salary` and `--overhead` instead of scaling the number by hand. For one part of the repo, rerun with `-C backend`. When they ask why the number is so high or low, point at the table. The biggest language rows drive KLOC, and generated or copied code that slipped past the skip rules inflates it. Name the files that look like that and let the user decide.

## Do not

- Present the number as market value or as a sale price.
- Use the estimate or the git numbers to judge anyone's productivity or to compare developers.
- Invent or extrapolate numbers the script did not print, or mix in numbers from other tools without saying so.
- Drop the caveat when sharing any of the outputs.
- Print author names unless the user ran or asked for `--show-authors`.
