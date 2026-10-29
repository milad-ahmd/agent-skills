---
name: bug-detective
description: Debugs code bugs systematically. Builds a minimal repro that fails on demand, logs each hypothesis and how it was tested so the investigation doesn't go in circles, narrows the cause by binary search over code, input or history, and pins down the change that introduced a regression with a git bisect runner that checks both ends first, handles timeouts and flaky repros, and always restores HEAD. Then writes a failing test, fixes the root cause instead of the symptom, and looks for the same bug elsewhere. Use when the user asks to debug their code, fix a bug, find out why a test or function fails, throws an error or returns the wrong result, track down a regression ("it used to work", "which commit broke this"), run git bisect, or chase a flaky or intermittent failure that only happens sometimes.
---

# Bug Detective

Debug by evidence: reproduce, guess, test the guess, narrow by halves, fix the cause, prove it. For a live production outage, use incident-commander first. Come back here once there is a specific bug to fix.

## 1. Reproduce first

Get one command that exits non-zero while the bug is present and 0 once it is fixed: a single test, a script, a curl against a local server. Make it small and deterministic.

- Pin anything that varies between runs, such as random seeds, the clock and timezone, locale, test order (run the one test) and network calls (use a fixture or a local fake).
- Cut the input down to the smallest case that still fails.
- If you will bisect, save it as a script outside the repo (`/tmp/repro.sh`) so it stays the same at every commit.
- **Flaky?** Measure the failure rate before changing anything: `for i in $(seq 50); do ./repro.sh >/dev/null 2>&1 || echo F; done | wc -l`. If it fails 1 run in 10, a fix needs about 30 clean runs to mean much (0.9^30 ≈ 4% chance of passing by luck).
- **Can't reproduce?** Don't guess-fix. List what differs between where it fails and where it doesn't (versions, data, env vars, OS, concurrency), add logging at the suspect boundary, and ask the user for the exact input, stack trace or logs.

## 2. Write down observed vs expected

```
Observed: total() returns 90.0 for a cart of 100.0 with code SAVE10 and no discount rules
Expected: 100.0 (SAVE10 expired on 1 Oct)
Repro:    pytest tests/test_cart.py::test_expired_code   (fails 10/10)
Since:    v2.3.0 was fine, main is not
```

## 3. Keep a hypothesis log

Keep this table in the conversation and update it after every test, so ruled-out ideas stay ruled out:

| # | Hypothesis | Test, and the result that would rule it out | Result | Verdict |
|---|---|---|---|---|
| 1 | Expiry check uses local time, server runs UTC | Run the repro with `TZ=UTC`. Wrong if it still fails | still fails | ruled out |
| 2 | Code cache is read before the expiry check | Log the cache hit in `apply_code()` | cached code applied | confirmed |

- Decide what result would disprove the hypothesis before running the test.
- Change one thing at a time, and revert experiments that didn't help.
- Don't re-test a ruled-out hypothesis without new evidence.
- After three ruled-out guesses in a row, stop guessing and narrow down (step 4).

## 4. Narrow down by halves

In code, check the state at the midpoint of the path with a log line or an assert. Whichever half has the wrong value is where to look next. With input, halve the failing case until removing anything more makes it pass.

In history, bisect when it worked before (a version, a date, a commit). Find a good ref with `git log --until=2026-09-01 -1 --format=%h` or a release tag, then:

```bash
python3 scripts/bisect_run.py --good v2.3.0 -- /tmp/repro.sh             # --bad defaults to HEAD
python3 scripts/bisect_run.py --good v2.3.0 --repeat 5 -- /tmp/repro.sh  # flaky: any failure in 5 runs = bad
python3 scripts/bisect_run.py --good a1b2c3d --timeout 60 --timeout-means bad -- npm test -- parser  # the bug is a hang
python3 scripts/bisect_run.py --good v2.3.0 --json -- 'make -s || exit 125; ./bin/check'
```

How each run is scored: exit 0 is good. Exit 125, any code in `--skip-on-exit`, and a timeout are skip (unless `--timeout-means bad`). Everything else is bad, including crashes and 126/127. One argument after `--` runs through `sh -c`, several run directly. `--timeout` is per run (default 600 s).

What it does for you: refuses to start with uncommitted changes to tracked files (it never stashes or discards them), runs the repro at `--good` (must pass) and `--bad` (must fail) before bisecting, and always runs `git bisect reset`, including on Ctrl-C. If it says the good ref also fails, a command was not found, or the bad ref passes, fix the repro instead of forcing it. If it warns that the first bad commit fails with a different exit code than `--bad`, that commit probably broke the build or the setup, not the behaviour you're after, so re-run with that code in `--skip-on-exit`.

Exit codes: `0` found · `1` only skipped commits left, so it lists the candidates · `2` usage or precondition error · `3` repro check failed at `--good` or `--bad` · `130` interrupted.

The first bad commit is a lead, not the answer. Read it (`git show <sha>`) and explain how the change produces the observed behaviour. If it only bumps a lockfile, the cause is in the dependency, so compare the package's changelog between the two versions.

## 5. Find the root cause

Keep asking why until one explanation accounts for every observation in the log, including the odd ones. A null check, a retry, a catch-all `except`, a longer timeout or a `sleep` hides the symptom unless you can also say why the value was null or why the call was slow. If a guard is the right fix for now, tell the user it is a guard and what the real cause is.

## 6. Write the failing test, then fix

Turn the repro into a test in the project's framework, at the lowest level that shows the bug. Run it and check it fails for the right reason: the assertion matches the bug, not an import error or a missing fixture. Then make the smallest change that fixes the cause.

## 7. Prove it

- The new test passes, and the repro passes. If it was flaky, use the run count from step 1.
- The rest of the suite (or at least the affected package) still passes.
- Debug logging and experiments are removed from the diff.

## 8. Check for siblings

The same mistake often lives in more than one place. Search for the pattern you fixed (`git grep -n 'datetime.now()'`, the same copy-pasted block, other callers of the changed function) and fix them, or list them for the user.

## Report

Keep it short: the cause in one or two sentences with `file:line`, the commit that introduced it if you bisected, the fix, the proof (test name, repro passes N/N), and any siblings found or left.

## Rules

- No fix before a repro. If you can't reproduce it, say so and say what you need.
- Never stash, reset, check out over or discard the user's uncommitted work. Ask them to commit or stash it before bisecting.
- Don't delete, skip or loosen a failing test to make it pass.
- Don't commit or push without the user's OK.
