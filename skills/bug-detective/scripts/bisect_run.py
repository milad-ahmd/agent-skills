#!/usr/bin/env python3
"""Find the commit that introduced a regression with `git bisect run`, safely. Stdlib only.

Usage: python3 bisect_run.py --good REF [--bad REF] [options] -- REPRO_COMMAND [ARGS ...]
  --good REF          a commit where the bug is absent (required)
  --bad REF           a commit where the bug is present (default: HEAD)
  --timeout SECONDS   limit for one run of the repro (default: 600, 0 = no limit)
  --timeout-means V   skip (default) or bad: use bad when the regression is a hang
  --repeat N          run the repro up to N times per commit; any failure marks it bad (flaky bugs)
  --skip-on-exit LIST comma-separated exit codes that mean "can't test this commit" (e.g. a build error)
  -C DIR              repository to bisect (default: current directory)
  --json              machine-readable output on stdout

The repro must exit 0 when the bug is absent and non-zero when it is present. One argument after
`--` runs through `sh -c`; several are executed directly. Exit 125 from the repro means skip, as in
git. Before bisecting, the repro is run at --good (must pass) and --bad (must fail). The script
refuses to start with uncommitted changes to tracked files and never stashes or discards anything.
It always runs `git bisect reset` at the end, including on Ctrl-C, SIGTERM and errors.

Exit codes: 0 first bad commit found · 1 bisect ended with several candidates (skipped commits)
            2 usage or precondition error · 3 repro check failed at --good or --bad · 130 interrupted
"""
import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

GIT_ENV = dict(os.environ, LC_ALL="C", LANG="C", GIT_TERMINAL_PROMPT="0")
# older git prints "is the first bad commit", newer git quotes the term: "is the first 'bad' commit"
SHA_RX = re.compile(r"^([0-9a-f]{40,64}) is the first '?bad'? commit", re.M)
NOT_FOUND = {126, 127}


class Stop(Exception):
    """A precondition failed: message for the user, script exit code, report status."""

    def __init__(self, msg, code=2, status="error"):
        super().__init__(msg)
        self.code, self.status = code, status


def git(repo, *args, check=True):
    r = subprocess.run(["git", *args], cwd=repo, env=GIT_ENV, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise Stop(f"git {' '.join(args)} failed: {(r.stderr or r.stdout).strip()}")
    return r.stdout.rstrip()


def raise_interrupt(signum, frame):
    raise KeyboardInterrupt


# --- running the repro ----------------------------------------------------------------
def run_once(cmd, cwd, timeout):
    """Run the repro once in its own process group. Returns a dict with exit, timed_out, seconds, tail."""
    argv = ["sh", "-c", cmd[0]] if len(cmd) == 1 else list(cmd)
    start = time.time()
    try:
        p = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True)
    except FileNotFoundError:
        return {"exit": 127, "timed_out": False, "seconds": 0.0, "tail": f"command not found: {argv[0]}"}
    except PermissionError:
        return {"exit": 126, "timed_out": False, "seconds": 0.0, "tail": f"permission denied: {argv[0]}"}
    timed_out = False
    try:
        out, _ = p.communicate(timeout=timeout or None)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_group(p)
        out, _ = p.communicate()
    except BaseException:
        kill_group(p)
        p.wait()
        raise
    code = p.returncode
    if code < 0:  # killed by a signal (a crash such as SIGSEGV is a failure, like a shell's 128+N)
        code = 128 - code
    lines = out.decode("utf-8", "replace").rstrip().splitlines()
    return {"exit": code, "timed_out": timed_out, "seconds": round(time.time() - start, 3),
            "tail": "\n".join(lines[-15:])}


def kill_group(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def verdict_of(run, skip_codes, timeout_means):
    if run["timed_out"]:
        return timeout_means
    if run["exit"] == 0:
        return "good"
    if run["exit"] == 125 or run["exit"] in skip_codes:
        return "skip"
    return "bad"


def test_commit(cmd, cwd, timeout, repeat, skip_codes, timeout_means):
    """Run the repro up to `repeat` times. Any bad run makes the commit bad; a skip stops early."""
    runs = []
    for _ in range(repeat):
        r = run_once(cmd, cwd, timeout)
        r["verdict"] = verdict_of(r, skip_codes, timeout_means)
        runs.append(r)
        if r["verdict"] != "good":
            return r["verdict"], runs
    return "good", runs


def step_main(a):
    """Called by `git bisect run` for each commit: test HEAD, log the result, exit 0/1/125."""
    signal.signal(signal.SIGTERM, raise_interrupt)
    try:
        sha = git(".", "rev-parse", "HEAD")
        verdict, runs = test_commit(a.cmd, ".", a.timeout, a.repeat, a.skip, a.timeout_means)
        with open(a.log, "a") as f:
            f.write(json.dumps({"commit": sha, "verdict": verdict, "runs": runs}) + "\n")
        last = runs[-1]
        how = "timed out" if last["timed_out"] else f"exit {last['exit']}"
        sys.stderr.write(f"  {sha[:10]}  {verdict:<4}  ({how}, run {len(runs)}/{a.repeat})\n")
        sys.exit({"good": 0, "bad": 1, "skip": 125}[verdict])
    except KeyboardInterrupt:
        sys.exit(130)  # > 127 makes git abort the bisect
    except Exception as e:  # an error here must not be mistaken for "bad"
        sys.stderr.write(f"bisect step error: {e}\n")
        sys.exit(128)


# --- the bisect -----------------------------------------------------------------------
def resolve(repo, ref, flag):
    r = subprocess.run(["git", "rev-parse", "--verify", "--quiet", ref + "^{commit}"], cwd=repo,
                       env=GIT_ENV, capture_output=True, text=True)
    if r.returncode != 0:
        raise Stop(f"{flag} {ref!r} is not a commit in this repository.")
    return r.stdout.strip()


def preflight(repo, a):
    if subprocess.run(["git", "rev-parse", "--git-dir"], cwd=repo, env=GIT_ENV,
                      capture_output=True).returncode != 0:
        raise Stop(f"{repo} is not inside a git repository.")
    if git(repo, "rev-parse", "--is-bare-repository") == "true":
        raise Stop("This is a bare repository; bisect needs a working tree.")
    if subprocess.run(["git", "bisect", "log"], cwd=repo, env=GIT_ENV, capture_output=True).returncode == 0:
        raise Stop("A bisect is already in progress here. Finish it or run `git bisect reset` first.")
    dirty = git(repo, "status", "--porcelain", "--untracked-files=no")
    if dirty:
        raise Stop("The working tree has uncommitted changes to tracked files:\n" + dirty + "\n"
                   "Bisect checks out other commits, which would carry or clobber these changes. Commit them "
                   "(or stash them yourself) and run again. This script never stashes or discards changes.")
    good, bad = resolve(repo, a.good, "--good"), resolve(repo, a.bad, "--bad")
    if good == bad:
        raise Stop("--good and --bad are the same commit.")
    if subprocess.run(["git", "merge-base", "--is-ancestor", bad, good], cwd=repo, env=GIT_ENV).returncode == 0:
        raise Stop("--bad is an ancestor of --good. --good must be the older commit. To find the commit "
                   "that fixed something, invert the repro (`sh -c '! ./repro.sh'`) and swap the refs.")
    warnings = []
    if subprocess.run(["git", "merge-base", "--is-ancestor", good, bad], cwd=repo, env=GIT_ENV).returncode != 0:
        warnings.append("--good is not an ancestor of --bad; git will test their merge base first.")
    first = a.cmd[0].split()[0] if len(a.cmd) == 1 else a.cmd[0]
    path = os.path.abspath(os.path.join(repo, first))
    if os.path.isfile(path) and subprocess.run(["git", "ls-files", "--error-unmatch", path], cwd=repo,
                                               env=GIT_ENV, capture_output=True).returncode == 0:
        warnings.append(f"{first} is tracked in this repo, so it changes (or vanishes) as bisect checks out "
                        "older commits. Copy it outside the repo if it should stay the same.")
    return good, bad, warnings


def check_ref(repo, sha, label, a):
    git(repo, "checkout", "-q", "--detach", sha)
    verdict, runs = test_commit(a.cmd, repo, a.timeout, a.repeat, a.skip, a.timeout_means)
    sys.stderr.write(f"  {label:<5} {sha[:10]}  {verdict}\n")
    return {"commit": sha, "verdict": verdict, "runs": runs}


def explain_check(label, res, a):
    last = res["runs"][-1]
    tail = ("\nLast output:\n" + last["tail"]) if last["tail"] else ""
    if label == "good":
        if res["verdict"] == "skip":
            why = "timed out" if last["timed_out"] else f"exited {last['exit']}"
            return f"The repro can't test --good ({why}, which means skip). Fix the repro or pick another --good."
        if all(r["exit"] in NOT_FOUND for r in res["runs"]):
            return (f"The repro exited {last['exit']} at --good: the command was not found or not executable. "
                    "Check the path (use an absolute path to a script outside the repo)." + tail)
        if last["timed_out"]:
            return "The repro timed out at --good. Raise --timeout or check the repro." + tail
        return (f"The repro also fails at --good (exit {last['exit']}). Either the bug is older than --good "
                "(pick an older ref) or the repro fails for another reason, such as a missing build step or "
                "a test that doesn't exist yet at that commit." + tail)
    if res["verdict"] == "skip":
        return "The repro can't test --bad (it asked to skip or timed out). Fix the repro first." + tail
    hint = "" if a.repeat > 1 else " If the bug is intermittent, use --repeat N."
    return ("The repro passes at --bad, so it doesn't reproduce the bug there." + hint +
            " Check that it exits non-zero on the bug.")


def commit_info(repo, sha):
    fmt = "%H%x00%h%x00%s%x00%an <%ae>%x00%ad"
    full, short, subject, author, date = git(repo, "show", "-s", "--date=iso", "--format=" + fmt, sha).split("\x00")
    files = []
    for line in git(repo, "show", "--numstat", "--format=", sha).splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            files.append({"path": parts[2], "added": parts[0], "deleted": parts[1]})
    return {"commit": full, "short": short, "subject": subject, "author": author, "date": date,
            "files": files, "stat": git(repo, "show", "--stat", "--format=", sha),
            "show": f"git show {short}"}


def bisect(a):
    repo = os.path.abspath(a.C)
    report = {"status": "error", "command": a.cmd, "repeat": a.repeat, "timeout": a.timeout,
              "steps": [], "warnings": []}
    good, bad, report["warnings"] = preflight(repo, a)
    report.update(good=good, bad=bad)
    tmp = tempfile.mkdtemp(prefix="bisect_run_")
    # Run steps from a copy: the skill may live inside the repo being bisected.
    step_script = os.path.join(tmp, "bisect_step.py")
    shutil.copy2(os.path.abspath(__file__), step_script)
    log = os.path.join(tmp, "steps.jsonl")
    started = False
    proc = None
    try:
        git(repo, "bisect", "start")
        started = True
        sys.stderr.write("Checking the repro at both ends first:\n")
        report["verify"] = {"good": check_ref(repo, good, "good", a)}
        if report["verify"]["good"]["verdict"] != "good":
            raise Stop(explain_check("good", report["verify"]["good"], a), 3, "verify_failed")
        report["verify"]["bad"] = check_ref(repo, bad, "bad", a)
        if report["verify"]["bad"]["verdict"] != "bad":
            raise Stop(explain_check("bad", report["verify"]["bad"], a), 3, "verify_failed")
        git(repo, "bisect", "bad", bad)
        git(repo, "bisect", "good", good)
        sys.stderr.write("Bisecting:\n")
        step = [sys.executable, step_script, "--step", "--log", log, "--timeout", str(a.timeout),
                "--repeat", str(a.repeat), "--timeout-means", a.timeout_means,
                "--skip-on-exit", ",".join(map(str, sorted(a.skip))), "--good", good, "--"] + a.cmd
        # stdout is parsed for the result; stderr (one progress line per step) goes straight to the user.
        proc = subprocess.Popen(["git", "bisect", "run"] + step, cwd=repo, env=GIT_ENV, stdout=subprocess.PIPE,
                                text=True, start_new_session=True)
        out, _ = proc.communicate()
        proc = None
        if os.path.exists(log):
            with open(log) as f:
                report["steps"] = [json.loads(line) for line in f if line.strip()]
        m = SHA_RX.search(out)
        if m:
            report["status"] = "found"
            report["first_bad"] = commit_info(repo, m.group(1))
            check_same_failure(report)
        elif "could be any of" in out:
            report["status"] = "ambiguous"
            block = out.split("could be any of:", 1)[1]
            report["candidates"] = [commit_info(repo, s) for s in re.findall(r"^([0-9a-f]{40,64})$", block, re.M)]
            report["message"] = ("Only skipped commits are left, so git can't name one. The first bad commit is "
                                 "one of the candidates. Make the repro work on them (or raise --timeout).")
        else:
            raise Stop("git bisect run stopped without a result:\n" + out.strip()[-2000:])
    except Stop as e:
        e.report = report
        raise
    except KeyboardInterrupt:
        if proc is not None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                kill_group(proc)
        raise
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)  # a second Ctrl-C must not cut the reset short
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        if started:
            r = subprocess.run(["git", "bisect", "reset"], cwd=repo, env=GIT_ENV, capture_output=True, text=True)
            if r.returncode != 0:
                sys.stderr.write("warning: `git bisect reset` failed; run it yourself:\n" + r.stderr)
        shutil.rmtree(tmp, ignore_errors=True)
        signal.signal(signal.SIGINT, signal.default_int_handler)
        signal.signal(signal.SIGTERM, raise_interrupt)
    return report


def check_same_failure(report):
    """Warn when the first bad commit fails differently from --bad (often a build break, not the bug)."""
    sha = report["first_bad"]["commit"]
    hits = [s for s in report["steps"] if s["commit"] == sha] or (
        [report["verify"]["bad"]] if report["verify"]["bad"]["commit"] == sha else [])
    want = {r["exit"] for r in report["verify"]["bad"]["runs"] if r["verdict"] == "bad"}
    got = {r["exit"] for s in hits for r in s["runs"] if r["verdict"] == "bad"}
    if got and want and not got & want:
        report["warnings"].append(
            f"The first bad commit fails with exit {sorted(got)} but --bad fails with exit {sorted(want)}. "
            "It may be a different breakage (a build error, a missing file). If so, re-run with "
            f"--skip-on-exit {','.join(map(str, sorted(got)))}.")


def print_text(rep):
    for w in rep.get("warnings", []):
        print(f"warning: {w}")
    if rep["status"] == "found":
        fb = rep["first_bad"]
        print(f"\nFirst bad commit: {fb['commit']}")
        print(f"  {fb['subject']}")
        print(f"  Author: {fb['author']}")
        print(f"  Date:   {fb['date']}")
        print(f"  Steps:  {len(rep['steps'])} (plus the check at --good and --bad)")
        if fb["stat"]:
            print("\n" + fb["stat"])
        print(f"\nRead it with: {fb['show']}")
    elif rep["status"] == "ambiguous":
        print("\n" + rep["message"])
        for c in rep["candidates"]:
            print(f"  {c['short']}  {c['subject']}")
    else:
        print(rep.get("message", "failed"))


def parse_args(argv):
    if "--" not in argv:
        sys.stderr.write("error: put the repro command after `--`, e.g. "
                         "bisect_run.py --good v1.2 -- npm test -- parser\n")
        sys.exit(2)
    i = argv.index("--")
    ap = argparse.ArgumentParser(description="Find the commit that introduced a regression, safely.",
                                 usage="%(prog)s --good REF [--bad REF] [options] -- REPRO_COMMAND [ARGS ...]")
    ap.add_argument("--good", required=True)
    ap.add_argument("--bad", default="HEAD")
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--timeout-means", choices=["skip", "bad"], default="skip")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--skip-on-exit", default="")
    ap.add_argument("-C", default=".", metavar="DIR")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--step", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--log", help=argparse.SUPPRESS)
    a = ap.parse_args(argv[:i])
    a.cmd = argv[i + 1:]
    if not a.cmd:
        ap.error("no repro command after `--`")
    if a.repeat < 1:
        ap.error("--repeat must be at least 1")
    if a.timeout < 0:
        ap.error("--timeout must be 0 (no limit) or more")
    try:
        a.skip = {int(x) for x in a.skip_on_exit.split(",") if x.strip()}
    except ValueError:
        ap.error("--skip-on-exit takes comma-separated integers, e.g. 2,77")
    if a.skip & {0}:
        ap.error("--skip-on-exit can't include 0 (that means the bug is absent)")
    return a


def main(argv=None):
    a = parse_args(sys.argv[1:] if argv is None else argv)
    if a.step:
        step_main(a)
    signal.signal(signal.SIGTERM, raise_interrupt)
    try:
        rep = bisect(a)
        code = 0 if rep["status"] == "found" else 1
    except Stop as e:
        rep = dict(getattr(e, "report", None) or {"command": a.cmd})
        rep.update(status=e.status, message=str(e))
        code = e.code
    except KeyboardInterrupt:
        rep, code = {"status": "interrupted", "command": a.cmd,
                     "message": "Interrupted. Bisect was reset and HEAD restored."}, 130
    if a.json:
        print(json.dumps(rep, indent=2))
    else:
        print_text(rep)
    sys.exit(code)


if __name__ == "__main__":
    main()
