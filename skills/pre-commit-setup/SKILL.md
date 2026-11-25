---
name: pre-commit-setup
description: Sets up git pre-commit hooks that run the project's own formatters and linters on staged files only, plus a secret scan and a large-file check, and mirrors the same checks in CI so git commit --no-verify can't skip them for good. A read-only script detects languages, lockfiles, monorepo layout, existing hook setups (husky, lefthook, simple-git-hooks, custom .git/hooks, core.hooksPath) and tool configs (ruff, black, eslint, prettier, biome, gofmt, golangci-lint, rustfmt). It picks the pre-commit framework for Python, husky + lint-staged for JS/TS, or lefthook for Rust, polyglot repos and monorepos, and prints the exact config file. It never adds a formatter the project doesn't use and never replaces an existing setup. These are git hooks, not Claude Code agent hooks. Use when the user asks to set up pre-commit hooks or a .pre-commit-config.yaml, add husky and lint-staged, configure lefthook, run eslint or prettier before every commit, format staged files on commit, or stop commits that fail lint.
---

# Pre-commit Setup

A good hook is fast enough that nobody reaches for `--no-verify`: format and lint only the staged files, scan the staged diff for secrets, block giant files. Anything slow (type checks, tests, full-repo lint) goes in pre-push or CI. Every check the hook runs also runs in CI, because a hook is a convenience, not a gate.

## 1. Plan

```bash
python3 scripts/hooks_plan.py            # stack, existing hooks, chosen manager, hook list, config text
python3 scripts/hooks_plan.py --json     # same, machine-readable
python3 scripts/hooks_plan.py -C path/to/repo
```

Exit codes: `0` plan printed · `1` a hook setup already exists (reported, nothing written) · `2` not a git repository.

The script reads `git ls-files` (tracked and untracked, gitignore respected) and skips `fixtures/`, `testdata/`, `vendor/` and `node_modules/`. Show the user the recommended manager and its reason, the hook list with where each tool was found, and any warnings.

## 2. If a setup already exists (exit 1)

Extend it, don't replace it. Add the missing hooks from the plan to the existing `.pre-commit-config.yaml`, `.lintstagedrc*` or `lefthook.yml`. If the repo has `core.hooksPath` or custom `.git/hooks` scripts, ask before switching managers: `pre-commit install` refuses to run while `core.hooksPath` is set, and `lefthook install` stops and asks for `--force` or `--reset-hooks-path`.

## 3. Which manager and why

| Repo | Manager | Config written |
|---|---|---|
| Python only (or no tooling found) | pre-commit framework | `.pre-commit-config.yaml` with revs pinned to real tags |
| JS/TS, one `package.json` | husky v9 + lint-staged | `.husky/pre-commit`, `.lintstagedrc.json` |
| Go, Rust, mixed languages, or a monorepo (workspaces, `pnpm-workspace.yaml`, `nx.json`, `turbo.json`, `go.work`, Cargo `[workspace]`, several manifests) | lefthook | `lefthook.yml` using `jobs`, with `root:` per package |

Only tools the project already configures get a hook: ruff from `[tool.ruff]` or `ruff.toml`, `ruff format` only when `[tool.ruff.format]` exists or a Makefile, CI workflow or similar already runs it, black from `[tool.black]`, eslint/prettier/biome from their config files or `package.json` keys, golangci-lint from `.golangci.*`. gofmt and `cargo fmt --check` come with the Go and Rust toolchains. If a tool is missing that the user wants, ask before adding it and its config.

Every plan adds a large-file check (new files over 500 KB, the same limit as pre-commit-hooks `check-added-large-files`) and gitleaks on the staged diff. In husky and lefthook, the gitleaks step prints a notice and skips if the binary isn't installed; CI still runs it.

## 4. Write and install

```bash
python3 scripts/hooks_plan.py --write    # creates the files only if none exist and no setup is found
```

Then run the setup commands the plan prints. For pre-commit they are `pipx install pre-commit`, `pre-commit install`, `pre-commit autoupdate` (moves revs to the latest tags) and `pre-commit run --all-files`.

For husky, install with the project's package manager (`npm install --save-dev husky lint-staged`, or the pnpm, yarn or bun form), then `npm pkg set scripts.prepare=husky` and `npx husky`. Skip `npx husky init` after `--write`, because it overwrites `.husky/pre-commit` with `npm test`.

For lefthook, install the binary (`brew install lefthook`, `npm install --save-dev lefthook`, `pipx install lefthook` or `go install`), then run `lefthook install` and `lefthook run pre-commit --all-files`.

The first all-files run may reformat many files. Commit that as its own "apply formatter" commit, separate from the hook setup, and ask the user first if the diff is large.

## 5. Mirror in CI

Add the plan's CI commands as steps in the existing workflow (create one only if the user wants it). For pre-commit that is `pre-commit run --all-files --show-diff-on-failure`. For husky and lefthook the plan lists check-mode commands (`eslint .`, `prettier --check .`, `biome ci .`, `ruff format --check .`, `test -z "$(gofmt -l .)"`, `golangci-lint run`) plus `gitleaks git --redact --verbose`, which needs `fetch-depth: 0` on checkout to scan history. CI does not repeat the large-file check. For pinning and hardening the workflow, hand off to gh-actions-auditor.

## 6. Slow checks

Time the type check and tests (`time npx tsc --noEmit`, `time pytest -q`). Under about 10 seconds: a `pre-push` hook. Slower: CI only. Clippy and full `golangci-lint run` belong in pre-push or CI too.

## Report

Manager and reason, the hooks and the config each came from, files written, setup commands run and their output, the CI steps added, and how to skip: `git commit --no-verify` (all hooks), `SKIP=<hook-id>` (pre-commit), `HUSKY=0`, `LEFTHOOK=0`.

## Do not

- Overwrite or delete an existing hook config, `.git/hooks` script or `core.hooksPath`. The script refuses; so should you.
- Add a formatter or linter the project doesn't use, or change its config. Ask the user first.
- Run tests, type checks or full-repo lint in pre-commit.
- Use unpinned or invented revs. Revs in the plan were checked against the repos' tags; run `pre-commit autoupdate` rather than editing them by hand.
- Commit the setup or the reformat unless the user asked. Writing commit messages is commit-craft's job; Claude Code agent hooks are agent-guardrails'; scanning git history for leaked keys is env-secrets-guard's.
