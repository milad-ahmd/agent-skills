---
name: test-gap-hunter
description: Finds the riskiest untested code in a project and writes meaningful tests for it using the project's existing test framework (pytest, Go testing, JUnit, RSpec) and conventions. A read-only script ranks files by recent changes times missing coverage, from a Cobertura or LCOV report or test-file naming, to show what a refactor could break untested. Use when the user asks to improve coverage, add tests, find untested code, or harden a module before a refactor.
---

# Test Gap Hunter

Write the tests that actually catch bugs, not tests that only inflate coverage.

## 1. Run the scan

```bash
python3 scripts/find_gaps.py                          # top 10 gaps, untested functions of the top 3
python3 scripts/find_gaps.py src/payments             # limit to paths
python3 scripts/find_gaps.py --coverage coverage.xml  # Cobertura XML or an LCOV tracefile
python3 scripts/find_gaps.py --since-days 90 --top 20 --json
```

The script only reads. For each source file it counts the commits in the window (180 days by default; `--now YYYY-MM-DD` pins the end) and multiplies that churn by how untested the file looks. Coverage comes first. It reads the report you pass with `--coverage`, or one it finds at the repo root such as `coverage.xml`, `coverage/lcov.info` or `target/site/cobertura/coverage.xml`, and uses 1 - line coverage.

Files the report doesn't list, or every file when there is no report, fall back to a naming guess. A file scores 1.0 when no test pairs with it by name (`test_x.py`, `x_test.go`, `x.spec.ts`, `__tests__/`, `XTest.java` under the `src/test` mirror, `x_spec.rb`) and 0.0 when one does. Each row says which evidence it used. Report paths from a CI checkout are matched by their longest suffix that is a tracked file.

Python files also get function, class and branch counts from `ast`. JS/TS and Go get a rough function count. It skips tests, fixtures, generated and vendored code, hidden directories, migrations, config, files over 1 MB and Python files with no functions, and prints the skip counts. JaCoCo XML, Go coverprofiles and coverage.py JSON are not parsed; convert them to Cobertura or work from the guess.

Exit codes: `0` report written · `2` not a git repository, no commits, bad arguments, or an unreadable coverage report.

## 2. Get better evidence if it is cheap

When the header says every row is a GUESS and the project already has a coverage tool configured, run it once (`pytest --cov --cov-report=xml`, `vitest run --coverage --coverage.reporter=lcov`, `jest --coverage`) and scan again. Regenerate the report if the header says it is older than HEAD. Don't install a coverage tool without asking.

Know what the guess gets wrong. It misses tests named after another module, like `test_api.py` exercising `handlers.py`, and it trusts any test whose name matches. Grep the test tree for the module or its exported names before you call a file untested.

## 3. Re-rank by what breaks

The script knows churn and missing tests. It doesn't know what the code does, so read the top 10 to 15 candidates. Move up business logic, money, auth, permissions, parsing, date/time handling and concurrency, then code with many branches and error paths (the `branches` count for Python) and public APIs that other modules depend on. Thin glue, CLI wiring and code that is about to be deleted move down.

Present the top 5–10 gaps with a one-line reason each, using the numbers: `billing.py: 9 commits in 180 days, 22% covered, refund() has 31 uncovered lines`. Write tests for the ones the user picks. Default to the top 3.

## 4. Detect the test setup

Find the framework (Jest, Vitest, pytest, Go `testing`, JUnit, RSpec, PHPUnit, XCTest, flutter_test...), where test files live and how they are named, the mocking and fixture style, and the command that runs the tests (package scripts, Makefile, CI). Open two existing tests next to the target before you write one.

## 5. Write behaviour tests

Read each picked file in full. Every test should protect a behaviour you can name, and you should know which bug would make it fail. If you can't name one, skip that test. For Python, start with the functions the scan lists as uncovered, biggest first.

Coverage padding is the failure mode here. A test that calls a function and asserts nothing, or only that it didn't throw, catches nothing. Neither does a snapshot of whatever the code returns today, or a test that only checks a mock was called with what the test told it to receive.

A good test covers the happy path and the edges: empty, None or null, zero, negative, max values, unicode, time zones, duplicates. It covers the error paths too, such as invalid input, a failing dependency or a timeout. Its name describes behaviour (`returns 401 when token is expired`). Keep one behaviour per test, Arrange–Act–Assert, and no logic inside tests. Mock only at boundaries like network, clock, filesystem and randomness, never the unit under test.

## 6. Run and iterate

Run the new tests until they pass. If a test reveals a real bug, **don't change the test to make it pass**. Report the bug. If coverage was measured, regenerate it and scan again to show the before and after for the files you touched.

## Rules

Match existing conventions exactly, from file placement and imports to helpers, fixtures and assertion style. Add no new test dependencies without asking. Tests must be deterministic, with no real network calls and with fixed clocks and seeds. Say which rows came from coverage and which from the naming guess.

New features written test-first belong to tdd-loop. Browser user flows belong to e2e-flow-tester, and chasing a known bug belongs to bug-detective.

## Report

List the ranked gaps with their evidence. For each test file added, list its tests with one line per test on the behaviour it protects. Add the before/after coverage if you measured it, and any bugs found.
