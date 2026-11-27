---
name: dead-code-funeral
description: Finds dead code in a repository's own source, holds a funeral for it, and deletes it only after the user approves. A read-only script scans Python (with ast) and JavaScript/TypeScript (with regular expressions) for files no other file imports and for top-level functions, classes and exported symbols nothing else refers to, skipping entry points, tests, configs and framework-convention files. Each finding gets a confidence level, its line count, the dates git says it was born and last touched, and an obituary line built from those facts. It prints totals ("12 items, 840 lines that could go"), JSON, and a gravestone SVG card without file paths. The agent checks each item for dynamic imports, reflection and framework wiring, then removes the approved items in small commits and runs the tests. Use when the user asks to find dead code, unused or orphaned files, unused functions or classes, unused exports, code that is never imported or called, what can be safely deleted, or to clean up leftover legacy code.
---

# Dead Code Funeral

Every repo has code nobody calls: the old parser, the v1 endpoint, the helper written for a feature that never shipped. This skill finds it, says a few words over it, and deletes only what the user signs off on.

## 1. Run the scan

```bash
python3 scripts/find_dead.py                        # ranked list, obituaries, totals
python3 scripts/find_dead.py -C path/to/repo --top 50
python3 scripts/find_dead.py --json > dead.json     # every item, every spared entry point
python3 scripts/find_dead.py --svg funeral.svg      # gravestone card, totals only
python3 scripts/find_dead.py --svg funeral.svg --no-credit   # same card without the small "made with agent-skills" line
python3 scripts/find_dead.py --svg funeral.svg --show-paths --now 2026-01-31
```

The script only reads. It covers tracked Python files and the JavaScript/TypeScript family (`.js .jsx .ts .tsx .mjs .cjs .mts .cts`).

| Confidence | Meaning |
|---|---|
| high | a file no other file imports, and its path or module name appears nowhere else |
| medium | a top-level Python function or class, or an exported JS/TS symbol, that no other code names |
| low | as above, but the name shows up in strings or non-code files, or a decorator may register it |

Entry points are never reported. That covers files named like `main`, `index`, `cli` or `__main__`, scripts with a shebang or a `__main__` block, tests and fixtures, config files, files that `package.json` or a `module:function` entry point in `pyproject.toml` points at, file-based routes (`pages/`, `routes/`, Next.js `app/` route files, SvelteKit `+` files), and Django modules such as `models.py`, `admin.py` and `migrations/`. `--json` lists every spared file under `entry_points` with the reason.

Dates come from git. A file is born in the commit that added it (renames are followed) and last touched in its newest commit; a symbol's dates are its oldest and newest surviving lines in `git blame`. Tests count as callers, so code that only tests use is not reported.

Exit codes: `0` nothing found · `1` dead code found · `2` not a git repository, no commits, or bad arguments.

## 2. Verify before the funeral

The scan matches names, not bindings. The JS/TS side uses regular expressions, so it misses computed `import()` paths and path aliases other than `@/` and `~/`. Treat every item as a suspect and run `git grep -n "<name>"` on it. Look for these escape routes:

| Escape route | What to look for |
|---|---|
| Dynamic loading | `importlib.import_module`, `__import__`, `require(variable)`, `import()` with a template string |
| Reflection | `getattr`, dispatch tables keyed by strings, plugin registries |
| Framework wiring | Django settings strings such as `INSTALLED_APPS`, Celery autodiscovery, pytest plugins, route folders in a custom place, custom bundler resolve rules |
| Build and CI | Makefiles, Dockerfiles, workflow YAML, shell scripts |
| Public API | when the report says the repo looks like a published library, exports and `__all__` entries may have callers outside the repo |

Drop the items that turn out to be alive and mark the ones you cannot settle as undecided. A low item stays on the list with the mention that saved it. Read that mention before you suggest deleting it.

## 3. Hold the funeral

Open with the headline totals. Then give each verified item a short eulogy, highest confidence first. Every fact in it comes from the script: the path, the two dates, the line count and the zero callers. The humour has to come from those facts too:

> **utils/legacy_parser.py** (2019-03-04 to 2023-11-02, 212 lines). Outlived by 0 callers. It spent its last three years waiting for an import that never came.

Don't invent history or motives. Don't name authors. If the user wants the card, give the SVG path.

## 4. Bury only what the user approves

1. Show a numbered list grouped by confidence and ask which to delete ("all high", "1, 3, 5", "none").
2. Delete in batches of related items. Removing a symbol includes the imports it leaves unused. Removing a file can include its stale mentions in docs or config when the user agrees.
3. After each batch, run the project's tests plus its type checker, linter or build where it has them. If anything fails, restore the batch and report what broke.
4. Commit each passing batch on its own, for example `chore: remove unused legacy parser and render_v1`, with the deleted line count in the body.
5. Re-run the scan. Deleting code can remove the last caller of something else, so a second round may appear. Offer it instead of starting it.

## Report

Give the headline totals by confidence and the verified list with obituaries. Say what you dropped as alive and why, and list the undecided items. After a deletion, add the commits, the lines removed and the test results.

## Do not

- Delete or comment out anything the user hasn't approved by item or by group.
- Change a published library's public API before the user agrees, even when nothing in the repo uses it.
- Present the scan as proof. It is a list of suspects until step 2 is done.
- Make up facts for the funeral, or name the people who wrote the code.
- Mix deletions with refactors or formatting in the same commit.
