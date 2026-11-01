---
name: architecture-doctor
description: Finds the architecture problems that cost a team the most time and proposes small, incremental refactors instead of a rewrite. A bundled script measures coupling (fan-in, fan-out, instability), circular dependencies, god files, hubs and layering violations, then weights each finding by git churn, so code that changes every week ranks above code nobody touches. It also finds hidden coupling, files that keep changing in the same commits without importing each other. The agent reads the worst offenders and returns 3-5 ranked refactors, each with evidence, a target shape, a first safe step and a way to verify nothing broke. Use when the user asks to improve the architecture, decide what to refactor first, untangle spaghetti or circular dependencies, split a god class or a file that is too big, fix tight coupling or wrong-way layer imports, pay down tech debt, or draw better module or domain boundaries.
---

# Architecture Doctor

Find the structural problems that slow the team down, prove it with numbers, and fix them in steps small enough to merge on their own. A problem in code nobody changes costs nothing, so churn decides the order.

## 1. Measure

```bash
python3 scripts/arch_report.py .                                   # ranked problems + every table, last 180 days
python3 scripts/arch_report.py . --layers "ui,pages>services>db"   # left may import right, never the reverse
python3 scripts/arch_report.py src --days 90 --top 20 --json > /tmp/arch.json
python3 scripts/arch_report.py . --exclude 'src/generated/*' --exclude '*.pb.go'
```

It parses Python, JS/TS (tsconfig paths) and Go imports, leaves tests out (`--include-tests` keeps them) and works without git, skipping churn with a note. Thresholds: `--god-lines 500 --god-fan-in 5 --hub-min 5 --hot-min 5`, hidden coupling `--min-shared 3 --min-ratio 0.5`, commits touching more than `--max-commit-files 30` files are ignored for co-change. Exit codes: `0` report written, problems or not · `2` not a directory, no source files, or a bad `--layers`.

Instability is I = Ce / (Ca + Ce), where Ca counts files outside that import it and Ce counts files outside it imports. Near 0 means many depend on it, so it should be stable and rarely change; a low-I file with high churn is where changes ripple. A hotspot is commits in the window times lines. The hidden-coupling ratio is shared commits divided by the average commit count of the pair. The score is weight times (1 + commits touching the files): use it to pick what to read, not as a verdict. In Go an import names a package, so every file in it gets the edge, which means Go fan-in is best read at module level.

Exclude generated code (ORM types, protobuf, API clients) first. It dominates god-file lists and nobody edits it by hand.

## 2. Read the top offenders

Open the top 5-8 problems and decide whether each one is real.

For a god file, ask how many unrelated reasons it changes for. Group its functions by caller; groups that never call each other can be split. Look for shallow modules too (Ousterhout): a wrapper whose interface is as large as what it hides adds a hop and no value, while a deep module hides a lot behind a small interface. Merge the shallow ones and keep the deep ones.

For a cycle, find the one import that points the wrong way. For module cycles the report already names the cheapest edge to cut. A layer violation is usually a type or helper that lives in the wrong layer.

## 3. Check which problems actually hurt

```bash
git log --since=180.days --format='%h %ad %s' --date=short -- path/to/file      # why does it keep changing?
git log --since=180.days --format='%h %s' -- a.py b.py | head -20               # what ties a co-changing pair together?
```

Read the commit messages. Repeated "fix" commits or one feature touching five layers is real cost. A hidden-coupling pair usually shares knowledge nobody wrote down: a duplicated constant, schema, enum or protocol. Drop problems that are old and stable, and say you dropped them.

## 4. Propose 3-5 refactors, highest payoff first

Patterns for each problem type are in [references/refactor-patterns.md](references/refactor-patterns.md). For each refactor write:

```
### 1. Split billing/service.py by reason to change   (payoff: high · effort: M)
Evidence:     1,240 lines, imported by 18 files, 37 commits in 180 days (2nd hotspot); co-changes with
              api/invoices.py in 9 of 12 commits with no import between them.
Target shape: invoice building, tax rules and payment calls in three modules behind the current
              public functions, which stay as thin re-exports until callers move.
First step:   Move the tax functions to billing/tax.py and re-export them from service.py. No caller changes.
Verify:       Characterization tests on the public functions pass before and after. arch_report shows
              service.py under 800 lines and billing/tax.py with I near 0.
```

Payoff = churn and reach of the problem. Effort = files touched and test coverage around them. Then list what you looked at and chose not to fix.

## 5. Before anyone refactors

Put tests around the code first. If coverage is thin, the first step is characterization tests that pin current behaviour. Then make one move per commit (move, re-export, update callers, delete the re-export), with each commit building and passing tests. Re-run `arch_report.py` after each step and quote the number that improved.

## What not to do

- No big-bang rewrites or "new architecture" proposals. Every step must be shippable on its own.
- No renames or folder reshuffles for taste. Moving files without cutting a dependency fixes nothing.
- No refactoring code that has no tests around it before adding them.
- Do not invent problems. If cycles are absent, nothing is a god file and churn is spread out, say the structure is healthy and stop.
- Do not treat lazy imports (inside functions, dynamic `import()`) as a fix for a cycle. They hide it.
- Do not edit code unless the user asks. The output is the ranked plan.
