---
name: migration-wizard
description: Plans and executes large codebase migrations incrementally and safely, such as JavaScript→TypeScript, Jest→Vitest, Next.js Pages→App Router, Vue 2→3, React class→hooks, CommonJS→ESM, or any framework or major-version upgrade, verifying each step with build and tests. Use when the user asks to migrate, convert, upgrade, or port a codebase or module.
---

# Migration Wizard

Big migrations fail when they're done as one giant PR. This skill migrates in small, verified steps.

## 1. Assess (read-only)

- Identify the **from → to** versions and find the official migration guide and codemods for them.
- Measure the scope by counting the affected files and APIs with search (`git grep -l "<old api>" | wc -l`).
- Make sure a **safety net** exists: the build, the type-check, the tests and CI. If there are no tests around critical paths, recommend adding characterization tests first (see the `test-gap-hunter` skill).
- Find blockers: dependencies that are incompatible with the target, custom build config, and monkey-patches.

## 2. Plan

Present a phased plan and wait for confirmation:

```markdown
## Migration plan: Jest → Vitest (142 test files)
Phase 0: Prep: add vitest, config, keep jest running in parallel
Phase 1: Run official codemod / mechanical changes (jest.fn → vi.fn)
Phase 2: Migrate leaf modules (utils, no mocks) — 60 files
Phase 3: Modules with module mocks / timers — 70 files
Phase 4: Special cases (snapshots, custom env) — 12 files
Phase 5: Remove jest, update CI & docs
Rollback: each phase is a separate commit; revert phase N without affecting N-1
```

## 3. Execute

- **Codemods first** (official tools such as `@next/codemod`, `ts-migrate`, `vue-codemod`, `jscodeshift`), then fix the rest by hand.
- Order the work **leaves first**: modules with no internal dependents, then up the dependency graph.
- After every batch: build, type-check, lint and test. **Stop at the first failure** and fix it before continuing.
- One commit per phase or batch with a clear message, so each step is easy to review and revert.
- Keep old and new running side by side when possible (for example `allowJs`, or both test runners) so the app stays shippable.

### Recipe notes

- **JS→TS**: enable `allowJs` and `checkJs: false`, rename files leaves-first, and avoid `any`. Use `unknown` plus narrowing, and add `// TODO(ts):` where real types are needed later. Turn on `strict` per directory at the end.
- **Pages→App Router**: migrate route by route. Convert `getServerSideProps`/`getStaticProps` to server components and `fetch` caching, and add `"use client"` only where hooks or events are needed.
- **CommonJS→ESM**: set `"type": "module"`, add file extensions in relative imports, replace `__dirname` with `import.meta.url`, and use dynamic `import()` for conditional requires.

## Rules

- Never change behaviour during a migration. Refactors go in separate commits.
- Don't delete the old setup until the new one passes everything in CI.
- At the end, report the files migrated, what was left behind and why, and the follow-up TODOs.
