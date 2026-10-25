---
name: dependency-doctor
description: Audits project dependencies for vulnerabilities, outdated and unused packages, and produces a safe, ordered upgrade plan with breaking-change notes. Use when the user asks to update dependencies, fix npm/pip audit warnings, check for vulnerable packages, or plan a major-version upgrade.
---

# Dependency Doctor

Diagnose first, then treat. Never upgrade everything blindly.

## 1. Diagnose (read-only)

Detect the ecosystem from its lockfile and run the matching tools:

| Ecosystem | Outdated | Vulnerabilities |
|---|---|---|
| npm / pnpm / yarn | `npm outdated` / `pnpm outdated` / `yarn outdated` | `npm audit --json` / `pnpm audit` |
| Python | `pip list --outdated` / `uv pip list --outdated` / `poetry show --outdated` | `pip-audit` |
| Go | `go list -u -m all` | `govulncheck ./...` |
| Rust | `cargo outdated` | `cargo audit` |
| Ruby | `bundle outdated` | `bundle audit` |
| PHP | `composer outdated` | `composer audit` |
| Flutter/Dart | `flutter pub outdated` | — |

If a tool isn't installed, say so and fall back to reading the manifest.
Find **unused** dependencies by searching for each package's import across the source.

## 2. Report

```markdown
## 🩺 Dependency Health: <project>
| Package | Current | Latest | Type | Risk | Action |
|---|---|---|---|---|---|
| lodash | 4.17.15 | 4.17.21 | patch | 🔴 CVE-2021-23337 (high) | upgrade now |
| react | 17.0.2 | 18.3.1 | major | 🟠 breaking | planned migration |
| moment | 2.29.4 | — | — | 🟡 unused | remove |
```

## 3. Treatment plan (ordered from safest to riskiest)

1. Security fixes (patch/minor) → 2. Remove unused packages → 3. Patch and minor bumps in small batches → 4. **One major upgrade at a time**.

For every major upgrade, check the package's CHANGELOG or release notes, list the breaking changes that affect **this** codebase (search for the APIs that changed) and estimate the effort.

## 4. Execute (only after the user confirms)

- Upgrade one batch, reinstall, then run the build, type-check and tests. Stop and report at the first failure.
- Always update the lockfile through the package manager. Never hand-edit lockfiles.
- Never use `--force` or `--legacy-peer-deps`, and never ignore audit findings, without explaining the trade-off.
