---
name: dependency-weigh-in
description: Weighs a project's installed dependencies on disk and finds what to slim. A read-only script measures node_modules (npm, pnpm, yarn, bun), a Python venv per installed distribution, and Go modules in the module cache, compares the total with real reference sizes (floppy disks, shareware Doom, the Apollo Guidance Computer, CDs) and gives it a weight class from strawweight to black hole. It lists the heaviest packages and what pulls each one in, the heaviest direct dependencies with everything they drag along, duplicate copies to dedupe, dev-only weight, dev tools listed under dependencies, and maintained lighter alternatives for known heavy packages, plus JSON and a shareable SVG weigh-in card. Use when the user asks how big or heavy node_modules is, why node_modules or a venv is so large, which package uses the most disk space, what pulls in a huge dependency, to weigh or shrink their dependencies, to cut install size, or wants the "node_modules heavier than a black hole" meme with real numbers.
---

# Dependency Weigh-in

node_modules jokes land because nobody knows the real number. This skill gets it, makes it shareable, then finds the weight that is actually removable.

## 1. Weigh

```bash
python3 scripts/weigh_in.py                     # project in the current directory
python3 scripts/weigh_in.py path/to/app --top 15
python3 scripts/weigh_in.py --venv .venv        # a venv somewhere other than .venv or venv
python3 scripts/weigh_in.py --svg weigh-in.svg  # also write a 1200x630 card
python3 scripts/weigh_in.py --json              # every number, for follow-up questions
```

The script only reads. It never installs or deletes anything, and weighs only what is already installed.

For node_modules it sizes every package directory, nested copies and pnpm's `node_modules/.pnpm` store included. Symlinks are not followed, so pnpm's top-level links and workspace links are not counted twice. Dependencies are resolved the way Node resolves them (nearest `node_modules` upward), so the "pulled in by" chains and per-dependency totals follow the real install. Dev-only weight comes from `dev: true` in package-lock.json v2/v3, otherwise from that graph.

A Python venv (`.venv`, `venv` or `--venv`) is sized per distribution from `*.dist-info/RECORD`, scripts in `bin/` included, and "required by" comes from `Requires-Dist` without extras.

Go modules listed in go.sum (or go.mod) are sized in `GOMODCACHE` (`go env GOMODCACHE`). Without Go or a cache, Go is skipped with a note. That cache is shared by every Go project on the machine.

Sizes are apparent sizes (the sum of file sizes) with MB = 1,000,000 bytes; the on-disk figure is block usage. Yarn Plug'n'Play projects keep packages as zips in `.yarn/cache` and are not weighed.

Exit codes: `0` weighed · `1` the SVG could not be written · `2` nothing installed to weigh. On `2`, tell the user to install first (`npm ci`, `pnpm install`, `pip install -r requirements.txt` in a venv, `go mod download`) and offer to run it only if they want; do not install on your own.

## 2. Show the weigh-in

Lead with the headline exactly as printed: total, weight class, and the comparison ("about 43 copies of shareware Doom, or 124 floppy disks"). If a card was written, give its path. Then the heaviest five packages, each with its "pulled in by" chain. Keep it short and fun; the numbers are the joke.

## 3. The slimming plan

Rank by expected savings, using only numbers the script printed. A package's current weight is the ceiling on what removing or replacing it can save.

1. Duplicates. Copies of the same package at several versions. Try `npm dedupe` or `pnpm dedupe`, then re-weigh. Copies left afterwards are usually pinned by incompatible version ranges; `npm explain <pkg>` or `pnpm why <pkg>` shows who asks for which.
2. Dev-only weight in production. If this install feeds a server or container, `npm ci --omit=dev` or `pnpm install --prod` leaves out the dev-only total. For a bundled frontend the dev/prod split does not change what users download, so say so.
3. Dev tools under `dependencies` (typescript, eslint, jest, `@types/*`): suggest moving them to `devDependencies`.
4. Heavy direct dependencies. For the top ones, check what the project actually uses (grep the imports). A dependency imported in one file for one helper is a candidate to replace with a few lines of code or a smaller package.
5. Known alternatives the script matched, such as moment, request, aws-sdk v2, node-sass, or CUDA builds of torch on a machine without a GPU. Each is a suggestion; sources are in `references/sources.md`.
6. Caches. A `node_modules/.vite` or `.cache` folder is build cache, not a dependency. Mention it; do not clear it unasked.

For Go, `go mod why -m <module>` shows why a module is needed.

## 4. Change only with a yes, then re-weigh

Ask which items the user wants done. For each one they pick: make the change, reinstall with their package manager, run the tests and build, then run the weigh-in again and report before and after with the real difference.

## Do not

- Delete node_modules, a venv or the Go module cache, or uninstall, upgrade or move packages without the user asking.
- Recommend an alternative that is unmaintained, archived or deprecated, or one you have not checked does what the project uses the old package for.
- Promise savings the numbers do not support. Shared dependencies count in every direct dependency that pulls them in, so per-dependency totals overlap and do not add up to the total.
- Treat this as a security or freshness audit. Vulnerable and outdated packages belong to a dependency audit, not a weigh-in.
