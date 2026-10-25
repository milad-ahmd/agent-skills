---
name: test-gap-hunter
description: Finds the riskiest untested code in a project and writes meaningful tests for it using the project's existing test framework and conventions. Use when the user asks to improve coverage, add tests, find untested code, or harden a module before a refactor.
---

# Test Gap Hunter

Write the tests that actually catch bugs, not tests that only inflate coverage.

## Workflow

1. **Detect the test setup.** Find the framework (Jest, Vitest, pytest, Go `testing`, JUnit, RSpec, PHPUnit, XCTest, flutter_test...), the test file locations and naming, the mocking and fixture style, and the command used to run tests (package scripts, Makefile, CI).
2. **Measure, if possible.** Run the coverage tool when one is configured (`vitest --coverage`, `jest --coverage`, `pytest --cov`, `go test -cover ./...`). If not, map source files to test files by name.
3. **Rank the gaps by risk**, not by line count:
   - business logic, money, auth, permissions, parsing, date/time handling, concurrency
   - code with many branches and error paths
   - recently changed code (`git log --since=90.days --name-only`)
   - public APIs that other modules depend on
4. **Present the top 5–10 gaps** with a one-line reason each, then write tests for the ones the user picks (default: the top 3).
5. **Run the new tests** and iterate until they pass. If a test reveals a real bug, **don't change the test to make it pass**. Report the bug.

## What a good test covers

- The happy path, plus **edge cases**: empty, null/None, zero, negative, max values, unicode, time zones, duplicates.
- **Error paths**: invalid input, failing dependency, timeout.
- Names that describe behaviour, such as `returns 401 when token is expired`.
- Arrange–Act–Assert structure, one behaviour per test, no logic inside tests.
- Mocks only at boundaries (network, clock, filesystem, randomness). Don't mock the unit under test.

## Rules

- Match existing conventions exactly: file placement, imports, helpers, fixtures, assertion style.
- Add no new test dependencies without asking.
- Tests must be deterministic: no real network calls, fixed clocks and seeds.
- Report at the end: the files added, the number of tests, the before/after coverage (if measured) and any bugs found.
