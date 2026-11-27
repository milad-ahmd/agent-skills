---
name: dependency-doctor
description: Audits project dependencies for vulnerabilities, outdated and unused packages, and produces a safe, ordered upgrade plan with breaking-change notes. A read-only offline script compares package.json, pyproject.toml, requirements.txt and go.mod with the imports in the code and the versions pinned in lockfiles, and lists unused, undeclared and misplaced dev dependencies; the agent then runs npm audit, pip-audit or govulncheck for the vulnerability and freshness side. Use when the user asks to update dependencies, fix npm/pip audit warnings, check for vulnerable packages, or plan a major-version upgrade.
---

# Dependency Doctor

Diagnose first, then treat. Never upgrade everything blindly.

## 1. Collect the local facts

```bash
python3 scripts/dep_audit.py                 # project in the current directory
python3 scripts/dep_audit.py -C path/to/app  # another folder
python3 scripts/dep_audit.py --json          # every dependency with its locked versions, every finding
```

The script only reads files and never contacts a registry, so it cannot say what is outdated or vulnerable.

It reads the dependency sections of package.json, pyproject.toml (`[project]`, optional-dependencies, `[dependency-groups]` and the Poetry tables), `requirements*.txt` and go.mod. setup.py, setup.cfg `install_requires` and Pipfile are not read.

Imports come from Python's `ast`, from JS/TS `import`, `export from`, `require()` and `import()`, from CSS `@import` and `@use`, and from Go import blocks. Node builtins, the Python stdlib, tsconfig `paths` and bundler aliases are skipped. Python import names that differ from the package name are mapped for common cases (`yaml` is PyYAML, `PIL` is Pillow).

Lockfiles parsed: package-lock.json v2/v3, yarn.lock v1, pnpm-lock.yaml 9.x, poetry.lock, uv.lock and go.sum. Other lockfiles (bun.lock, pdm.lock, Pipfile.lock, yarn berry) only count as present.

A manifest covers the files under its folder. JS and Python files also see the dependencies declared in parent folders (workspaces); Go files see only their own go.mod. node_modules, venvs, vendor/ and build output folders (dist, build, out, target, coverage next to a project file) are skipped. A lockfile in a parent folder only counts for workspace members; other nested packages are treated as unlocked, with a note. On Python older than 3.11, TOML goes through a small built-in reader, and the report says which reader was used.

Exit codes: `0` nothing at high or medium confidence · `1` findings at high or medium confidence · `2` bad arguments, missing folder, or no manifest to read.

| Finding | Meaning | Usual fix |
|---|---|---|
| unused | declared, never imported, and not named in scripts, config files or strings | remove it, after the checks in step 3 |
| undeclared | imported but not declared by any manifest covering the file (works only by luck: hoisting, a global install) | add it to the manifest |
| dev-in-runtime | dev-only dependency imported from app code | move it to runtime dependencies |
| missing-from-lock | declared but absent from the lockfile, or a go.mod require with no go.sum line | reinstall or re-lock with the package manager |
| unlocked | version ranges and no lockfile | commit a lockfile (apps); ranges are normal for published libraries |
| no-manifest | Python files import third-party modules and nothing declares them | create requirements or pyproject entries |

"Implicit" lists packages that are not imported but are run from package.json scripts, named in config files (`.eslintrc`, `jest.config.js`, `[tool.ruff]`), named in strings (Django `INSTALLED_APPS`, pino transports) or declared by a Go `tool` directive. "Locked at several versions" is informational; transitive duplicates are normal.

## 2. Add the network facts

Run the commands the script prints under "Needs network", or the row for the project's ecosystem:

| Ecosystem | Outdated | Vulnerabilities |
|---|---|---|
| npm / pnpm / yarn | `npm outdated` / `pnpm outdated` / `yarn outdated` | `npm audit --json` / `pnpm audit` |
| Python | `pip list --outdated` / `uv pip list --outdated` / `poetry show --outdated` | `pip-audit` |
| Go | `go list -u -m all` | `govulncheck ./...` |
| Rust | `cargo outdated` | `cargo audit` |
| Ruby | `bundle outdated` | `bundle audit` |
| PHP | `composer outdated` | `composer audit` |
| Flutter/Dart | `flutter pub outdated` | none |

If a tool isn't installed or there is no network, say so and report only the local facts; do not install tools without asking. Merge both sources into one row per package. The script's locked version is the "Current" column when the tool output is missing.

## 3. Check before calling anything unused or undeclared

The script replaces guessing, not judgement. Low-confidence items are usually loaded by name rather than imported: pytest plugins, type stubs, ESLint or Babel plugins, a DB driver picked from a connection URL, a server started from a Procfile, or a framework default such as `@nestjs/platform-express`. Before recommending a removal, search once more for the name where the script does not look, such as HTML `<script>` tags, shell scripts, notebooks and imports built from variables.

An undeclared JS import can be an alias the script could not read, for example a webpack alias built in a function. An undeclared Python import may come from a distribution with another name; the report adds a hint when it knows one. In a bundled frontend (Vite, Next, webpack) a dev dependency imported from app code is often harmless, so the script marks it low there.

## 4. Report

```markdown
## Dependency Health: <project>
| Package | Current | Latest | Type | Risk | Action |
|---|---|---|---|---|---|
| lodash | 4.17.15 | 4.17.21 | patch | high: CVE-2021-23337 | upgrade now |
| react | 17.0.2 | 18.3.1 | major | breaking | planned migration |
| moment | 2.29.4 | n/a | n/a | unused (high confidence) | remove |
| axios | not declared | 1.7.2 | n/a | undeclared, imported in src/api.js | declare it |
```

## 5. Treatment plan (ordered from safest to riskiest)

Start with security fixes that are patch or minor bumps. Next, declare undeclared imports and sync stale lockfiles, then remove unused packages. After that come patch and minor bumps in small batches, and last, **one major upgrade at a time**.

For every major upgrade, check the package's CHANGELOG or release notes, list the breaking changes that affect **this** codebase (search for the APIs that changed) and estimate the effort. The same order decides which Dependabot or Renovate PRs to merge first.

## 6. Execute (only after the user confirms)

- Upgrade one batch, reinstall, then run the build, type-check and tests. Stop and report at the first failure.
- Always update the lockfile through the package manager. Never hand-edit lockfiles.
- Never use `--force` or `--legacy-peer-deps`, and never ignore audit findings, without explaining the trade-off.
- Re-run `python3 scripts/dep_audit.py` afterwards and show what changed.

## Not this skill

How much disk node_modules or a venv takes belongs to dependency-weigh-in. Leaked keys in the repo belong to env-secrets-guard. Unpinned actions in CI workflows belong to gh-actions-auditor.
