---
name: tdd-loop
description: Runs strict test-driven development one behaviour at a time. Picks the next smallest behaviour from the spec or task list, writes one failing test, makes the smallest change that passes it, then refactors with the tests green. A guard script records each step and refuses the usual shortcuts. It rejects a red step that fails for the wrong reason (syntax, import or compile error, missing function, no tests ran) and code written before any failing test. It also rejects a test that was edited, deleted, skipped or focused with .only to reach green. Works with pytest, unittest, Jest, Vitest, go test, cargo test, RSpec, Maven and Gradle. Use when the user asks to do TDD, work test-first, write the test first, follow red green refactor, build a feature test-driven from a spec or user story, or stop the agent from changing tests to make them pass.
---

# TDD Loop

Build one behaviour at a time: a failing test, the least code that passes it, then a cleanup with the tests green. The guard script makes each step checkable, so "I wrote the test first" is a recorded fact rather than a claim.

## 0. Set up once

```bash
python3 scripts/tdd_guard.py detect        # suggests the test command from project files
```

Pick a command that runs once and exits (`npx vitest run`, not `vitest` in watch mode). Prefer one that runs the current test file or test, so cycles stay fast, and run the whole suite before each commit. The guard keeps its state in `.tdd/` at the project root and writes a `.tdd/.gitignore` so git ignores it. Add `.tdd/` to the project's `.gitignore` too if the user wants it listed there.

Run one test:

| Runner | Command |
|---|---|
| pytest | `python3 -m pytest -q tests/test_slug.py::test_lowercases` |
| unittest | `python3 -m unittest tests.test_slug.SlugTest.test_lowercases` |
| Vitest / Jest | `npx vitest run src/slug.test.ts -t "lowercases"` / `npx jest src/slug.test.ts -t "lowercases"` |
| go test | `go test ./slug -run '^TestLowercases$'` |
| cargo | `cargo test lowercases` |
| Maven | `mvn -Dtest=SlugTest#lowercases test` |

## 1. Make a behaviour list

From the spec, issue or user story, write a short list of behaviours, each one observable from outside: "`slugify('Hello World')` returns `hello-world`", "a cart with an expired code is charged full price". Order them so each builds on the last, simplest and most central first. Show the list to the user and keep it updated in the conversation.

Slice vertically. One behaviour goes through every layer it needs (route, service, storage) in a single cycle. Don't write all the models first, then all the services.

## 2. Red: one failing test

Write exactly one test for the next behaviour. Test through the public interface (the function, the endpoint, the CLI), and assert on results the caller can see, not on private helpers or which internal methods were called. If the code doesn't exist yet, add a stub that compiles and returns a placeholder, so the test fails on its assertion rather than on an import.

```bash
python3 scripts/tdd_guard.py red --test "python3 -m pytest -q tests/test_slug.py"
python3 scripts/tdd_guard.py red --test "npx vitest run src/slug.test.ts" --files src/slug.test.ts
python3 scripts/tdd_guard.py red          # later cycles reuse the recorded command
```

`red` passes only when the command fails on an assertion. It refuses if the tests pass already, if the failure is a syntax, import, compile or collection error, a missing function, a missing command, a timeout, or if no tests ran. It records SHA-256 snapshots of the test files: `--files`, plus every test file `git status` shows as changed. Without `--files` it needs at least one test file that is new or changed since the last cycle. It warns when more than one new test appeared, when the new test's name isn't in the failure output, and when source files were already changed. If the error really is the behaviour under test, pass `--allow-error "reason"`, which is recorded.

## 3. Green: the smallest change that passes

Change only production code. Hard-coding the expected value is fine when it is the simplest thing that passes. The next test will force the general version.

```bash
python3 scripts/tdd_guard.py green
```

`green` re-runs the recorded command and passes only if it exits 0 with tests run and no test file changed since red. A changed or deleted test file, or an added skip or focus marker (`@pytest.mark.skip`, `@unittest.skip`, `it.skip`, `xit`, `describe.only`, `t.Skip()`, `#[ignore]`, `@Disabled`), fails the check and prints the diff. This covers older test files too. If the test itself was wrong, revert it and run `red` again, or tell the user why and run `green --allow-test-change "reason"`.

## 4. Refactor with the tests green

Remove duplication, rename, extract, and simplify the test code as well as the production code. Keep the behaviour the same and re-run after each small step.

```bash
python3 scripts/tdd_guard.py refactor
```

It passes when the tests pass and no test file lost tests or assertions, gained a skip marker or was deleted. It lists the test files that changed. If a test was added, write it in the next red cycle instead.

## 5. Commit and repeat

Commit after each refactor if the user wants a commit per cycle; otherwise keep going. Tick the behaviour off the list and return to step 2. `python3 scripts/tdd_guard.py status` shows the phase, the guarded files and the cycle count.

Exit codes for every command: `0` check passed, `1` check failed (the reason is printed), `2` usage or state error, such as `green` with no recorded red. Add `--json` for machine-readable output and `--dir` to point at the project root.

## Report

After each cycle, give one line: behaviour, test name, red reason (assertion), green, refactor done or skipped, commit. At the end, list the behaviours done and the ones left, plus any `--allow-*` overrides and why they were needed.

## What not to do

- Skip red, or write the implementation and then a test that passes on the first run.
- Write several tests before making any of them pass.
- Change, loosen, delete, `.skip`, `xit` or `.only` a test to get to green.
- Mock everything. Mock only what is slow or outside your control (network, clock, payment provider), and use real objects for the rest.
- Assert on implementation details (private methods, call counts on internal collaborators) that break on every refactor.
- Add behaviour during refactor, or refactor while red.
- Run `--allow-test-change` or `--allow-error` without telling the user the reason.
- Delete `.tdd/` to get around a failed check. Fix the cause, or ask the user.
