#!/usr/bin/env python3
"""Guard a red -> green -> refactor loop so test-driven development can't be faked. Stdlib only.

Usage:
  python3 tdd_guard.py red --test "CMD" [--files PATH ...] [--allow-error REASON]
  python3 tdd_guard.py green [--allow-test-change REASON]
  python3 tdd_guard.py refactor [--allow-test-change REASON]
  python3 tdd_guard.py status
  python3 tdd_guard.py detect
Common options: --dir DIR (project root, default: current directory) · --timeout SECONDS (default 600,
0 = none) · --json (machine-readable output on stdout)

red       Runs the test command, which MUST fail, and refuses failures for the wrong reason: syntax,
          import, compile or collection errors, a missing function, a command that was not found, a
          timeout, or no tests running at all. Records the command and a SHA-256 snapshot of the test
          files: the ones in --files plus every test file git reports as changed. With no --files it
          needs at least one test file that is new or changed since the last cycle.
green     Re-runs the recorded command, which must pass. Fails if a test file changed since red (prints
          a diff) or a skip/only marker was added, unless --allow-test-change "reason" (recorded).
refactor  Runs the command again, which must pass. Test files may change, but not by removing tests or
          assertions or adding skip markers. Changed test files are listed.
status    Shows the phase, command, guarded files and cycle history.
detect    Suggests the test command from project files (pytest, unittest, vitest, jest, mocha, npm test,
          go test, cargo test, rspec, minitest, Maven, Gradle, PHPUnit, mix, dotnet, dart/flutter, swift).

State lives in DIR/.tdd/ (state.json plus snapshots of test files). Add `.tdd/` to .gitignore.
The test command runs through the shell in DIR, with stdin closed. Use a non-watch command.

Exit codes: 0 check passed · 1 check failed (the reason is printed) · 2 usage or state error
"""
from __future__ import annotations

import argparse
import datetime
import difflib
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time

STATE_DIR = ".tdd"
GIT_ENV = dict(os.environ, LC_ALL="C", LANG="C", GIT_TERMINAL_PROMPT="0")

# --- what counts as a test file --------------------------------------------------------------
TEST_FILE_RX = re.compile(
    r"(^|/)(test_[^/]*\.py|[^/]*_test\.py"
    r"|[^/]*\.(test|spec)\.(js|jsx|ts|tsx|mjs|cjs|mts|cts)"
    r"|__tests__/[^/]*\.(js|jsx|ts|tsx|mjs|cjs|mts|cts)"
    r"|[^/]*_test\.go|[^/]*_spec\.rb|[^/]*_test\.rb|test_[^/]*\.rb"
    r"|[^/]*(Test|Tests|IT)\.(java|kt|cs|php|swift)"
    r"|[^/]*_test\.exs|[^/]*_test\.dart)$"
    r"|(^|/)tests/[^/]*\.rs$")

# Markers that switch a test off, or focus the run so other tests are left out.
SKIP_RX = re.compile(
    r"pytest\.mark\.(skip|skipif|xfail)\b|\bpytest\.(skip|xfail)\s*\(|@unittest\.(skip|skipIf|skipUnless|expectedFailure)\b"
    r"|@(skip|skipIf|skipUnless|expectedFailure)\b|\.skipTest\s*\("
    r"|\b(it|test|describe|context|suite)\.(skip|only|todo)\b|\b(xit|xtest|xdescribe|xcontext|fit|fdescribe)\s*\("
    r"|\bt\.Skip(f|Now)?\s*\(|#\[ignore\]|@Disabled\b|@Ignore\b|\[Ignore\b|\(\s*Skip\s*=|markTest(Skipped|Incomplete)\b"
    r"|^\s*(skip|pending)\b(\s*[(\"']|\s*$)", re.M)
TEST_DEF_RX = re.compile(
    r"^\s*(async\s+)?def\s+test\w*\s*\(|\b(it|test)\s*\(\s*[\"'`]|^\s*func\s+Test\w*\s*\(|#\[test\]"
    r"|@(Test|ParameterizedTest)\b|\[(Fact|Theory|Test|TestMethod)\b|^\s*it\s+[\"']|^\s*test\s+[\"']", re.M)
ASSERT_RX = re.compile(
    r"\bassert|\bexpect\s*[(.]|\.should\b|\bt\.(Error|Errorf|Fatal|Fatalf|Fail|FailNow)\s*\(|\bverify\s*\(", re.M)

# --- reading test output ------------------------------------------------------------------------
# A red run that matches one of these failed before it reached an assertion.
HARD_ERRORS = [
    ("syntax error", r"\b(SyntaxError|IndentationError|TabError)\b|PHP Parse error"),
    ("import error", r"\b(ImportError|ModuleNotFoundError|LoadError)\b|Cannot find module|cannot load such file"
                     r"|Failed to resolve import|Failed to load url|does not provide an export named"),
    ("missing name or function", r"\bNameError\b|\bReferenceError\b|AttributeError: module .* has no attribute"
                                 r"|is not a function\b|uninitialized constant|PHP Fatal error"),
    ("compile error", r"\berror TS\d{4}\b|\berror\[E\d{4}\]|could not compile|\[build failed\]|\[setup failed\]"
                      r"|COMPILATION ERROR|cannot find symbol|Compilation failed|\berror CS\d{4}\b|\(CompileError\)"),
    ("collection error", r"errors? during collection|\bERROR collecting\b|An error occurred while loading"),
]
NO_TESTS = (r"\bRan 0 tests\b|NO TESTS RAN|\bcollected 0 items\b|\bno tests ran\b|No tests found|No test files found"
            r"|\bno test files\b|no tests to run|\b0 examples\b|running 0 tests|\b0 tests\b")
FAILURE_EVIDENCE = re.compile(
    r"AssertionError|\bassert(ion)?\b|\bFAIL(ED|URE)?\b|\b[1-9]\d* (failed|failures?)\b|--- FAIL|panicked at"
    r"|\bExpected\b|\bexpected\b|✕|×|Failures:|\bnot equal\b", re.M)


class Stop(Exception):
    def __init__(self, msg, code=2, extra=None):
        super().__init__(msg)
        self.code, self.extra = code, extra or {}


def now():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def is_test_file(path):
    return bool(TEST_FILE_RX.search(path.replace(os.sep, "/")))


# --- running the test command ------------------------------------------------------------------
def run_cmd(cmd, cwd, timeout):
    start = time.time()
    p = subprocess.Popen(cmd, shell=True, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=(os.name == "posix"))
    timed_out = False
    try:
        out, _ = p.communicate(timeout=timeout or None)
    except subprocess.TimeoutExpired:
        timed_out = True
        if os.name == "posix":
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        else:
            p.kill()
        out, _ = p.communicate()
    except BaseException:
        p.kill()
        p.wait()
        raise
    code = p.returncode if p.returncode >= 0 else 128 - p.returncode
    return {"exit": code, "timed_out": timed_out, "seconds": round(time.time() - start, 2),
            "output": out.decode("utf-8", "replace")}


def tail(text, n=25):
    lines = text.rstrip().splitlines()
    return "\n".join(lines[-n:])


def classify_failure(run):
    """Return (ok, label, matching line) for a failing run: ok=False means it failed for the wrong reason."""
    out = run["output"]
    if run["timed_out"]:
        return False, "timeout", "the command did not finish"
    if run["exit"] in (126, 127):
        return False, "command not found", tail(out, 3)
    for label, rx in HARD_ERRORS:
        m = re.search(rx, out)
        if m:
            return False, label, line_at(out, m.start())
    if not FAILURE_EVIDENCE.search(out):
        m = re.search(NO_TESTS, out)
        if m:
            return False, "no tests ran", line_at(out, m.start())
        return True, "unconfirmed", ""
    return True, "assertion", ""


def no_tests_ran(out):
    m = re.search(NO_TESTS, out)
    if m and not re.search(r"\b[1-9]\d* (passed|tests?|examples?)\b|\bRan [1-9]|\bok\s+\S+", out):
        return line_at(out, m.start())
    return ""


def line_at(text, pos):
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start:end if end != -1 else len(text)].strip()[:200]


def failure_lines(out, n=8):
    keep = [ln.rstrip() for ln in out.splitlines() if FAILURE_EVIDENCE.search(ln)]
    return "\n".join(keep[:n]) if keep else tail(out, 10)


# --- git and files ----------------------------------------------------------------------------------
def git(root, *args):
    try:
        r = subprocess.run(["git", *args], cwd=root, env=GIT_ENV, capture_output=True)
    except FileNotFoundError:
        return None
    return r.stdout if r.returncode == 0 else None


def git_info(root):
    top = git(root, "rev-parse", "--show-toplevel")
    if top is None:
        return None
    head = git(root, "rev-parse", "--verify", "-q", "HEAD")
    return {"top": os.path.realpath(top.decode().strip()), "head": head.decode().strip() if head else None}


def changed_test_files(root, info):
    """Test files under root that differ from HEAD or are untracked, as paths relative to root."""
    if not info:
        return []
    out = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", ".")
    if out is None:
        return []
    entries, files, skip_next = out.decode("utf-8", "replace").split("\0"), [], False
    for e in entries:
        if skip_next:  # the source path of a rename
            skip_next = False
            continue
        if len(e) < 4:
            continue
        status, path = e[:2], e[3:]
        if "R" in status or "C" in status:
            skip_next = True
        rel = os.path.relpath(os.path.join(info["top"], path), root).replace(os.sep, "/")
        if not rel.startswith("../") and not rel.startswith(STATE_DIR + "/") and is_test_file(rel):
            files.append(rel)
    return sorted(set(files))


def read_bytes(root, rel):
    try:
        with open(os.path.join(root, rel), "rb") as f:
            return f.read()
    except (FileNotFoundError, IsADirectoryError):
        return None


def head_bytes(root, info, rel, rev):
    if not info or not rev:
        return None
    path = os.path.relpath(os.path.join(root, rel), info["top"]).replace(os.sep, "/")
    return git(info["top"], "show", f"{rev}:{path}")


def file_hash(data):
    return sha256(data) if data is not None else None


# --- state ----------------------------------------------------------------------------------------------
class State:
    def __init__(self, root):
        self.root = root
        self.dir = os.path.join(root, STATE_DIR)
        self.path = os.path.join(self.dir, "state.json")
        self.data = {"version": 1, "phase": None, "test_cmd": None, "cycle": None, "known": {}, "history": []}
        if os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    self.data.update(json.load(f))
            except (OSError, ValueError) as e:
                raise Stop(f"Can't read {self.path}: {e}. Fix or delete it.")

    def save(self):
        os.makedirs(os.path.join(self.dir, "snapshots"), exist_ok=True)
        ignore = os.path.join(self.dir, ".gitignore")
        if not os.path.exists(ignore):
            with open(ignore, "w") as f:
                f.write("*\n")
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    def put_snapshot(self, data):
        if data is None:
            return None
        h = sha256(data)
        p = os.path.join(self.dir, "snapshots", h)
        if not os.path.exists(p):
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(data)
        return h

    def get_snapshot(self, h):
        if h is None:
            return None
        try:
            with open(os.path.join(self.dir, "snapshots", h), "rb") as f:
                return f.read()
        except FileNotFoundError:
            raise Stop(f"Snapshot {h[:12]} is missing from {self.dir}/snapshots. Run red again.")


def snapshot_files(st, rels):
    return {rel: st.put_snapshot(read_bytes(st.root, rel)) for rel in rels}


# --- comparing test files -------------------------------------------------------------------------------
def text_of(data):
    return data.decode("utf-8", "replace") if data is not None else ""


def compare(st, info, baseline, base_rev):
    """Compare current test files with `baseline` ({rel: sha or None}). Files git reports as changed but not
    in the baseline are compared with base_rev (HEAD when the baseline was taken). Returns a list of changes."""
    rels = set(baseline) | set(changed_test_files(st.root, info))
    changes = []
    for rel in sorted(rels):
        if rel in baseline:
            old = st.get_snapshot(baseline[rel])
        else:
            old = head_bytes(st.root, info, rel, base_rev)
        new = read_bytes(st.root, rel)
        if file_hash(old) == file_hash(new):
            continue
        o, n = text_of(old), text_of(new)
        diff = list(difflib.unified_diff(o.splitlines(), n.splitlines(), f"a/{rel}", f"b/{rel}", lineterm="", n=2))
        added = [ln[1:] for ln in diff if ln.startswith("+") and not ln.startswith("+++")]
        changes.append({
            "file": rel, "status": "deleted" if new is None else ("new" if old is None else "modified"),
            "guarded": rel in baseline,
            "skips_before": len(SKIP_RX.findall(o)), "skips_after": len(SKIP_RX.findall(n)),
            "skip_lines": [ln.strip() for ln in added if SKIP_RX.search(ln)][:5],
            "tests_before": len(TEST_DEF_RX.findall(o)), "tests_after": len(TEST_DEF_RX.findall(n)),
            "asserts_before": len(ASSERT_RX.findall(o)), "asserts_after": len(ASSERT_RX.findall(n)),
            "diff": "\n".join(diff[:80]) + ("\n... (diff truncated)" if len(diff) > 80 else ""),
        })
    return changes


TEST_NAME_RX = re.compile(r"def\s+(test\w*)|func\s+(Test\w*)|\b(?:it|test)\s*\(\s*([\"'`])(.+?)\3"
                          r"|^\s*(?:it|test)\s+([\"'])(.+?)\5|fn\s+(\w+)")


def added_tests(old, new):
    """Names of test definitions on lines that `new` adds to `old`."""
    out = []
    for ln in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0):
        if ln.startswith("+") and not ln.startswith("+++") and TEST_DEF_RX.search(ln[1:]):
            m = TEST_NAME_RX.search(ln[1:])
            if m:
                out.append(next(g for i, g in enumerate(m.groups()) if g and i not in (2, 4)))
    return out


def skips_added(c):
    return c["skips_after"] > c["skips_before"]


# --- commands -------------------------------------------------------------------------------------------
def cmd_red(a, st):
    info = git_info(st.root)
    test_cmd = a.test or st.data.get("test_cmd")
    if not test_cmd:
        hint = detect(st.root)
        raise Stop("No test command. Pass --test \"CMD\"" +
                   (f", for example --test \"{hint[0]['command']}\"." if hint else "."))
    files = []
    for f in a.files or []:
        rel = os.path.relpath(os.path.abspath(os.path.join(st.root, f)), st.root).replace(os.sep, "/")
        if read_bytes(st.root, rel) is None:
            raise Stop(f"--files {f}: no such file under {st.root}.")
        files.append(rel)
    changed = changed_test_files(st.root, info)
    known = st.data.get("known", {})
    fresh = [r for r in changed if file_hash(read_bytes(st.root, r)) != known.get(r)]
    if not files:
        if not info:
            raise Stop("Not a git repository, so changed test files can't be detected. Pass --files PATH ...")
        if not fresh:
            raise Stop("No test file is new or changed since the last cycle. Write the failing test first "
                       "(red comes before any implementation), or pass --files PATH ...")
    guarded = sorted(set(files) | set(changed))
    run = run_cmd(test_cmd, st.root, a.timeout)
    rep = {"check": "red", "command": test_cmd, "exit": run["exit"], "seconds": run["seconds"],
           "files": guarded, "warnings": []}
    if run["exit"] == 0 and not run["timed_out"]:
        rep["message"] = ("The test command passed, so there is no failing test. Either the behaviour already "
                          "exists (pick the next behaviour), the new test isn't run by this command, or the "
                          "implementation was written before the test.")
        rep["output_tail"] = tail(run["output"], 15)
        return rep, 1
    ok, label, line = classify_failure(run)
    rep["reason"] = label
    if not ok and not a.allow_error:
        rep["message"] = (f"The tests fail for the wrong reason ({label}): {line}\n"
                          "Red must be an assertion failure in the new test. Fix the test or add a stub "
                          "(the function exists and returns a placeholder) so it fails on its assertion. If this "
                          "error is the behaviour under test, re-run with --allow-error \"reason\".")
        rep["output_tail"] = tail(run["output"])
        return rep, 1
    if not ok:
        rep["warnings"].append(f"Accepted a {label} failure: {a.allow_error}")
    if label == "unconfirmed":
        rep["warnings"].append("Couldn't find an assertion failure in the output. Read it and check the new test "
                               "is the one failing.")
    names = []
    for rel in fresh or files:
        old = st.get_snapshot(known[rel]) if rel in known else head_bytes(st.root, info, rel,
                                                                          info and info["head"])
        names += added_tests(text_of(old), text_of(read_bytes(st.root, rel)))
    rep["new_tests"] = names
    if len(names) > 1:
        rep["warnings"].append(f"{len(names)} new tests in this cycle ({', '.join(names[:5])}). Write one test per "
                               "behaviour, then go green.")
    if names and not any(n in run["output"] for n in names):
        rep["warnings"].append("None of the new tests (" + ", ".join(names[:5]) + ") is named in the output. "
                               "Check the failure comes from the new test, not an older one.")
    if info:
        src = [p for p in (git(st.root, "status", "--porcelain=v1", "--untracked-files=all", "--", ".") or b"")
               .decode("utf-8", "replace").splitlines() if p[3:] and not is_test_file(p[3:])
               and not p[3:].startswith(STATE_DIR) and "__pycache__/" not in p and not p.endswith(".pyc")]
        if src:
            rep["warnings"].append(f"{len(src)} non-test file(s) already changed before red. Make sure none of it "
                                   "is the implementation for this test.")
    data = st.data
    if data.get("phase") in ("green", "refactor") and data.get("cycle"):
        data["history"].append(data["cycle"])
    elif data.get("phase") == "red":
        rep["warnings"].append("Replaced the open red cycle that never went green.")
    data["phase"], data["test_cmd"] = "red", test_cmd
    data["cycle"] = {"number": len(data["history"]) + 1, "test_cmd": test_cmd, "head": info and info["head"],
                     "red_at": now(), "red_reason": label, "new_files": fresh or files,
                     "files": snapshot_files(st, guarded), "allowed": [], "refactors": 0}
    if a.allow_error and not ok:
        data["cycle"]["allowed"].append({"phase": "red", "reason": a.allow_error, "at": now()})
    st.save()
    rep["failure"] = failure_lines(run["output"])
    rep["message"] = "RED: the test fails on an assertion. Make the smallest change that passes it, then run green."
    return rep, 0


def need_cycle(st, phases, what):
    if st.data.get("phase") not in phases or not st.data.get("cycle"):
        have = st.data.get("phase") or "none"
        hint = {"green": "Run red with a failing test first. Green without a recorded red is not TDD.",
                "refactor": "Refactor needs a green cycle. Run red, then green."}[what]
        raise Stop(f"{what} can't run in phase '{have}'. {hint}")
    return st.data["cycle"]


def report_changes(changes):
    lines = []
    for c in changes:
        lines.append(f"  {c['file']} ({c['status']}, tests {c['tests_before']}->{c['tests_after']}, "
                     f"assertions {c['asserts_before']}->{c['asserts_after']})")
        for s in c["skip_lines"]:
            lines.append(f"    skip/only marker added: {s}")
    return "\n".join(lines)


def cmd_green(a, st):
    cyc = need_cycle(st, ("red",), "green")
    info = git_info(st.root)
    changes = compare(st, info, cyc["files"], cyc.get("head"))
    rep = {"check": "green", "command": cyc["test_cmd"], "warnings": [],
           "test_changes": [{k: v for k, v in c.items() if k != "diff"} for c in changes]}
    if changes and not a.allow_test_change:
        skip = [c["file"] for c in changes if skips_added(c)]
        rep["message"] = ("Test files changed since red" + (" (skip/only markers added)" if skip else "") +
                          ". Make the test pass by changing the code, not the test.\n" + report_changes(changes) +
                          "\n\n" + "\n".join(c["diff"] for c in changes) +
                          "\n\nIf the test itself was wrong, revert it and run red again, or re-run green with "
                          "--allow-test-change \"reason\".")
        return rep, 1
    run = run_cmd(cyc["test_cmd"], st.root, a.timeout)
    rep.update(exit=run["exit"], seconds=run["seconds"])
    if run["exit"] != 0 or run["timed_out"]:
        rep["message"] = ("Still failing" + (" (timed out)" if run["timed_out"] else "") +
                          ". Keep changing the implementation, not the test.")
        rep["output_tail"] = tail(run["output"])
        return rep, 1
    none = no_tests_ran(run["output"])
    if none:
        rep["message"] = f"The command passed but no tests ran ({none}). Check the test is still collected."
        return rep, 1
    if changes:
        cyc["allowed"].append({"phase": "green", "reason": a.allow_test_change, "at": now(),
                               "files": [c["file"] for c in changes]})
        rep["warnings"].append(f"Test change allowed: {a.allow_test_change}")
    cyc["files"] = snapshot_files(st, sorted(set(cyc["files"]) | {c["file"] for c in changes}))
    cyc["green_at"] = now()
    st.data["known"].update({k: v for k, v in cyc["files"].items() if v})
    st.data["phase"] = "green"
    st.save()
    rep["message"] = "GREEN: all tests pass and the tests were not touched. Refactor now if it helps, then run refactor."
    return rep, 0


def cmd_refactor(a, st):
    cyc = need_cycle(st, ("green", "refactor"), "refactor")
    info = git_info(st.root)
    changes = compare(st, info, cyc["files"], cyc.get("head"))
    rep = {"check": "refactor", "command": cyc["test_cmd"], "warnings": [],
           "test_changes": [{k: v for k, v in c.items() if k != "diff"} for c in changes]}
    weakened = [c for c in changes if skips_added(c) or c["tests_after"] < c["tests_before"]
                or c["asserts_after"] < c["asserts_before"] or c["status"] == "deleted"]
    if weakened and not a.allow_test_change:
        rep["message"] = ("Refactoring removed tests or assertions, deleted a test file, or added skip/only markers:\n"
                          + report_changes(weakened) + "\n\n" + "\n".join(c["diff"] for c in weakened) +
                          "\n\nRestore them, or re-run with --allow-test-change \"reason\".")
        return rep, 1
    run = run_cmd(cyc["test_cmd"], st.root, a.timeout)
    rep.update(exit=run["exit"], seconds=run["seconds"])
    if run["exit"] != 0 or run["timed_out"]:
        rep["message"] = "The refactor broke the tests. Undo the last step or fix it; behaviour must not change."
        rep["output_tail"] = tail(run["output"])
        return rep, 1
    none = no_tests_ran(run["output"])
    if none:
        rep["message"] = f"The command passed but no tests ran ({none})."
        return rep, 1
    if any(c["tests_after"] > c["tests_before"] for c in changes):
        rep["warnings"].append("New tests were added during refactor. A new behaviour belongs in a new red cycle.")
    if weakened:
        cyc["allowed"].append({"phase": "refactor", "reason": a.allow_test_change, "at": now(),
                               "files": [c["file"] for c in weakened]})
        rep["warnings"].append(f"Test change allowed: {a.allow_test_change}")
    cyc["files"] = snapshot_files(st, sorted(set(cyc["files"]) | {c["file"] for c in changes}))
    cyc["refactors"] = cyc.get("refactors", 0) + 1
    st.data["known"].update({k: v for k, v in cyc["files"].items() if v})
    st.data["phase"] = "refactor"
    st.save()
    rep["message"] = ("REFACTOR: tests still pass." + (" Changed test files:\n" + report_changes(changes)
                                                       if changes else " No test files changed.") +
                      "\nCommit if the user wants a commit per cycle, then pick the next behaviour and run red.")
    return rep, 0


def cmd_status(a, st):
    d = st.data
    cyc = d.get("cycle")
    rep = {"check": "status", "phase": d.get("phase"), "command": d.get("test_cmd"),
           "cycles_done": len(d.get("history", [])) + (1 if d.get("phase") in ("green", "refactor") else 0),
           "cycle": None, "state_file": st.path}
    if cyc:
        files = []
        for rel, h in sorted(cyc["files"].items()):
            files.append({"file": rel, "changed_since_snapshot": file_hash(read_bytes(st.root, rel)) != h})
        rep["cycle"] = {k: cyc.get(k) for k in ("number", "red_at", "green_at", "refactors", "allowed", "new_files")}
        rep["cycle"]["files"] = files
    nxt = {None: "write one failing test, then run red", "red": "make it pass with the smallest change, then run green",
           "green": "refactor with tests green, then run refactor (or start the next red)",
           "refactor": "commit if wanted, then the next red"}
    rep["message"] = f"Phase: {d.get('phase') or 'none'}. Next: {nxt.get(d.get('phase'), '')}."
    return rep, 0


# --- detect ---------------------------------------------------------------------------------------------
def _read(root, name):
    try:
        with open(os.path.join(root, name), encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _exists(root, *names):
    return any(os.path.exists(os.path.join(root, n)) for n in names)


def detect(root):
    out = []

    def add(runner, command, why):
        out.append({"runner": runner, "command": command, "why": why})

    py = [n for n in ("pyproject.toml", "setup.cfg", "tox.ini", "pytest.ini", "setup.py") if _exists(root, n)]
    reqs = " ".join(_read(root, n) for n in os.listdir(root) if n.startswith("requirements") and n.endswith(".txt"))
    blob = " ".join(_read(root, n) for n in py) + reqs
    if (_exists(root, "pytest.ini", "conftest.py") or "[tool.pytest" in blob or "[tool:pytest]" in blob
            or "[pytest]" in blob or re.search(r"\bpytest\b", blob)):
        add("pytest", "python3 -m pytest -q", "pytest config or dependency found")
    elif py or reqs or _has_py_tests(root):
        where = " -s tests" if os.path.isdir(os.path.join(root, "tests")) else ""
        add("unittest", f"python3 -m unittest discover{where} -p 'test_*.py'", "Python project without pytest")
    pkg_text = _read(root, "package.json")
    if pkg_text:
        try:
            pkg = json.loads(pkg_text)
        except ValueError:
            pkg = {}
        deps = {}
        for k in ("dependencies", "devDependencies"):
            deps.update(pkg.get(k) or {})
        script = (pkg.get("scripts") or {}).get("test", "")
        if "vitest" in deps or "vitest" in script:
            add("vitest", "npx vitest run", "vitest in package.json (`run` avoids watch mode)")
        if "jest" in deps or re.search(r"\bjest\b", script):
            add("jest", "npx jest", "jest in package.json")
        if "mocha" in deps or "mocha" in script:
            add("mocha", "npx mocha", "mocha in package.json")
        if script and "no test specified" not in script:
            pm = ("pnpm test" if _exists(root, "pnpm-lock.yaml") else "yarn test" if _exists(root, "yarn.lock")
                  else "bun run test" if _exists(root, "bun.lockb", "bun.lock") else "npm test")
            add("package script", pm, f"scripts.test = {script!r}; check it doesn't start a watcher")
    if _exists(root, "go.mod"):
        add("go", "go test ./...", "go.mod")
    if _exists(root, "Cargo.toml"):
        add("cargo", "cargo test", "Cargo.toml")
    gem = _read(root, "Gemfile")
    if _exists(root, ".rspec") or "rspec" in gem or os.path.isdir(os.path.join(root, "spec")):
        add("rspec", "bundle exec rspec", ".rspec, spec/ or rspec in Gemfile")
    elif gem and (os.path.isdir(os.path.join(root, "test")) or "minitest" in gem):
        add("minitest", "bundle exec rake test", "Gemfile with test/ or minitest")
    if _exists(root, "pom.xml"):
        add("maven", "./mvnw test" if _exists(root, "mvnw") else "mvn test", "pom.xml")
    if _exists(root, "build.gradle", "build.gradle.kts"):
        add("gradle", "./gradlew test" if _exists(root, "gradlew") else "gradle test", "build.gradle")
    if "phpunit" in _read(root, "composer.json") or _exists(root, "phpunit.xml", "phpunit.xml.dist"):
        add("phpunit", "vendor/bin/phpunit", "phpunit in composer.json or phpunit.xml")
    if _exists(root, "mix.exs"):
        add("exunit", "mix test", "mix.exs")
    if any(n.endswith((".sln", ".csproj", ".fsproj")) for n in os.listdir(root)):
        add("dotnet", "dotnet test", ".sln or project file")
    pub = _read(root, "pubspec.yaml")
    if pub:
        add("flutter" if "flutter" in pub else "dart", "flutter test" if "flutter" in pub else "dart test",
            "pubspec.yaml")
    if _exists(root, "Package.swift"):
        add("swift", "swift test", "Package.swift")
    if not out and re.search(r"(?m)^test:", _read(root, "Makefile")):
        add("make", "make test", "Makefile has a test target")
    return out


def _has_py_tests(root):
    for d in (root, os.path.join(root, "tests")):
        if os.path.isdir(d) and any(n.startswith("test_") and n.endswith(".py") for n in os.listdir(d)):
            return True
    return False


def cmd_detect(a, st):
    found = detect(st.root)
    if not found:
        return {"check": "detect", "candidates": [], "message": "No test setup recognised. Pass --test yourself."}, 1
    msg = "\n".join(f"  {c['command']:<45} {c['runner']}: {c['why']}" for c in found)
    return {"check": "detect", "candidates": found, "message": "Suggested test commands (first is the best guess):\n"
            + msg + "\nRun one behaviour's tests while iterating if the full suite is slow."}, 0


# --- main -----------------------------------------------------------------------------------------------
def print_text(rep, code):
    if rep.get("check") == "status" and "cycles_done" in rep:
        print(rep["message"])
        if rep.get("command"):
            print(f"Command: {rep['command']}")
        print(f"Cycles done: {rep['cycles_done']}")
        cyc = rep.get("cycle")
        if cyc:
            print(f"Cycle {cyc['number']}: red {cyc['red_at']}" + (f", green {cyc['green_at']}" if cyc.get("green_at")
                                                                   else "") + f", refactors {cyc['refactors']}")
            for f in cyc["files"]:
                print(f"  {f['file']}" + ("  (changed since snapshot)" if f["changed_since_snapshot"] else ""))
            for al in cyc["allowed"] or []:
                print(f"  allowed in {al['phase']}: {al['reason']}")
        return
    print(rep.get("message", ""))
    if rep.get("failure") and code == 0:
        print("\nFailure:\n" + rep["failure"])
    if rep.get("output_tail"):
        print("\nOutput (tail):\n" + rep["output_tail"])
    for w in rep.get("warnings", []):
        print(f"warning: {w}")


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="tdd_guard.py", description="Guard a red -> green -> refactor TDD loop.",
                                 epilog="Exit codes: 0 check passed, 1 check failed, 2 usage or state error.")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dir", default=".", help="project root holding .tdd/ (default: current directory)")
    common.add_argument("--json", action="store_true", help="machine-readable output")
    common.add_argument("--timeout", type=float, default=600.0, help="seconds per test run, 0 = no limit")
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("red", parents=[common], help="the new test must fail for the right reason")
    r.add_argument("--test", help="test command (default: the one recorded last time)")
    r.add_argument("--files", nargs="+", help="test files to guard (default: changed test files from git status)")
    r.add_argument("--allow-error", metavar="REASON", help="accept a syntax/import/compile-style failure, recorded")
    for name, h in (("green", "the recorded command must pass with the tests untouched"),
                    ("refactor", "tests still pass and were not weakened")):
        p = sub.add_parser(name, parents=[common], help=h)
        p.add_argument("--allow-test-change", metavar="REASON", help="accept a test-file change, recorded")
    sub.add_parser("status", parents=[common], help="show the current phase and guarded files")
    sub.add_parser("detect", parents=[common], help="suggest the test command for this project")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        sys.exit(2)
    if a.timeout < 0:
        ap.error("--timeout must be 0 or more")
    for flag in ("allow_error", "allow_test_change"):
        if getattr(a, flag, None) is not None and not getattr(a, flag).strip():
            ap.error(f"--{flag.replace('_', '-')} needs a reason")
    return a


def main(argv=None):
    a = parse_args(sys.argv[1:] if argv is None else argv)
    root = os.path.realpath(a.dir)
    if not os.path.isdir(root):
        sys.stderr.write(f"error: --dir {a.dir} is not a directory\n")
        return 2
    try:
        st = State(root)
        rep, code = {"red": cmd_red, "green": cmd_green, "refactor": cmd_refactor,
                     "status": cmd_status, "detect": cmd_detect}[a.cmd](a, st)
    except Stop as e:
        rep, code = dict(e.extra, check=a.cmd, message=str(e)), e.code
    except KeyboardInterrupt:
        rep, code = {"check": a.cmd, "message": "Interrupted; state not changed."}, 2
    rep["ok"] = code == 0
    if a.json:
        print(json.dumps(rep, indent=2))
    else:
        print_text(rep, code)
    return code


if __name__ == "__main__":
    sys.exit(main())
