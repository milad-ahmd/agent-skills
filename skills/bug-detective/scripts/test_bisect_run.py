#!/usr/bin/env python3
"""Tests for bisect_run.py (stdlib unittest, no network).

Run: python3 -m unittest discover -s skills/bug-detective/scripts -p 'test_*.py'
Each test builds a throwaway git repo in a temp dir with a regression at a known commit. Git runs
with an empty global config, so the user's hooks, signing and identity settings are never used.
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from bisect_run import run_once, verdict_of  # noqa: E402

SCRIPT = os.path.join(HERE, "bisect_run.py")
ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", LC_ALL="C",
           GIT_AUTHOR_NAME="Ada Tester", GIT_AUTHOR_EMAIL="ada@example.com",
           GIT_COMMITTER_NAME="Ada Tester", GIT_COMMITTER_EMAIL="ada@example.com")
NO_BUG = "! grep -q bug f"


class Repo:
    """A temp repo whose file `f` holds contents[i] at commit i, on branch main."""

    def __init__(self, tmp, contents):
        self.path = os.path.join(tmp, "repo")
        os.mkdir(self.path)
        self.git("init", "-q", "-b", "main")
        self.shas = []
        for i, text in enumerate(contents):
            with open(os.path.join(self.path, "f"), "w") as fh:
                fh.write(f"{text}\n{i}\n")
            self.git("add", "f")
            self.git("commit", "-q", "-m", f"commit {i}: {text}")
            self.shas.append(self.git("rev-parse", "HEAD"))

    def git(self, *args, check=True):
        r = subprocess.run(["git", *args], cwd=self.path, env=ENV, capture_output=True, text=True)
        if check and r.returncode:
            raise AssertionError(r.stderr)
        return r.stdout.strip()

    def head(self):
        r = subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], cwd=self.path, env=ENV,
                           capture_output=True, text=True)
        return r.stdout.strip() or self.git("rev-parse", "HEAD")

    def bisecting(self):
        return subprocess.run(["git", "bisect", "log"], cwd=self.path, env=ENV,
                              capture_output=True).returncode == 0

    def read(self):
        with open(os.path.join(self.path, "f")) as fh:
            return fh.read()

    def run(self, *args, cmd=NO_BUG):
        r = subprocess.run([sys.executable, SCRIPT, "-C", self.path, "--json", *args, "--", cmd],
                           env=ENV, capture_output=True, text=True, timeout=60)
        return r.returncode, json.loads(r.stdout), r.stderr


REGRESSION_AT_5 = ["ok", "ok", "ok", "ok", "ok", "bug", "bug", "bug"]


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def repo(self, contents=REGRESSION_AT_5):
        return Repo(self.tmp, contents)

    def assertRestored(self, repo, head="refs/heads/main"):
        self.assertEqual(repo.head(), head)
        self.assertFalse(repo.bisecting())
        self.assertEqual(repo.git("status", "--porcelain", "--untracked-files=no"), "")


class Bisect(Base):
    def test_finds_first_bad_commit(self):
        repo = self.repo()
        code, rep, _ = repo.run("--good", repo.shas[0])
        self.assertEqual(code, 0, rep)
        self.assertEqual(rep["status"], "found")
        fb = rep["first_bad"]
        self.assertEqual(fb["commit"], repo.shas[5])
        self.assertEqual(fb["subject"], "commit 5: bug")
        self.assertIn("Ada Tester <ada@example.com>", fb["author"])
        self.assertEqual([x["path"] for x in fb["files"]], ["f"])
        self.assertEqual(fb["show"], "git show " + fb["short"])
        self.assertTrue(1 <= len(rep["steps"]) <= 4)
        self.assertEqual((rep["verify"]["good"]["verdict"], rep["verify"]["bad"]["verdict"]), ("good", "bad"))
        self.assertRestored(repo)
        self.assertEqual(repo.read(), "bug\n7\n")

    def test_text_output(self):
        repo = self.repo()
        r = subprocess.run([sys.executable, SCRIPT, "-C", repo.path, "--good", repo.shas[0], "--", NO_BUG],
                           env=ENV, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0)
        self.assertIn("First bad commit: " + repo.shas[5], r.stdout)
        self.assertIn("Read it with: git show", r.stdout)

    def test_restores_detached_head(self):
        repo = self.repo()
        repo.git("checkout", "-q", "--detach", repo.shas[6])
        code, rep, _ = repo.run("--good", repo.shas[0], "--bad", repo.shas[7])
        self.assertEqual((code, rep["first_bad"]["commit"]), (0, repo.shas[5]))
        self.assertRestored(repo, head=repo.shas[6])

    def test_bad_defaults_to_head_and_accepts_any_ref(self):
        repo = self.repo()
        repo.git("tag", "v1", repo.shas[2])
        code, rep, _ = repo.run("--good", "v1")
        self.assertEqual((code, rep["bad"], rep["first_bad"]["commit"]), (0, repo.shas[7], repo.shas[5]))


class Refusals(Base):
    def test_dirty_tree_is_refused_and_left_alone(self):
        repo = self.repo()
        with open(os.path.join(repo.path, "f"), "w") as fh:
            fh.write("my unsaved work\n")
        code, rep, _ = repo.run("--good", repo.shas[0])
        self.assertEqual((code, rep["status"]), (2, "error"))
        self.assertIn("uncommitted changes", rep["message"])
        self.assertEqual(repo.read(), "my unsaved work\n")
        self.assertFalse(repo.bisecting())
        self.assertEqual(repo.git("stash", "list"), "")

    def test_untracked_files_are_fine(self):
        repo = self.repo()
        with open(os.path.join(repo.path, "notes.txt"), "w") as fh:
            fh.write("scratch\n")
        code, rep, _ = repo.run("--good", repo.shas[0])
        self.assertEqual(code, 0, rep)
        self.assertTrue(os.path.exists(os.path.join(repo.path, "notes.txt")))

    def test_bad_ref_and_reversed_refs(self):
        repo = self.repo()
        code, rep, _ = repo.run("--good", "no-such-ref")
        self.assertEqual(code, 2)
        self.assertIn("not a commit", rep["message"])
        code, rep, _ = repo.run("--good", repo.shas[7], "--bad", repo.shas[0])
        self.assertEqual(code, 2)
        self.assertIn("older commit", rep["message"])

    def test_bisect_already_running(self):
        repo = self.repo()
        repo.git("bisect", "start")
        code, rep, _ = repo.run("--good", repo.shas[0])
        self.assertEqual(code, 2)
        self.assertIn("already in progress", rep["message"])
        repo.git("bisect", "reset")


class Verification(Base):
    def test_good_ref_also_fails(self):
        repo = self.repo()
        code, rep, _ = repo.run("--good", repo.shas[0], cmd="echo boom; exit 3")
        self.assertEqual((code, rep["status"]), (3, "verify_failed"))
        self.assertIn("also fails at --good (exit 3)", rep["message"])
        self.assertIn("boom", rep["message"])
        self.assertEqual(rep["steps"], [])
        self.assertRestored(repo)

    def test_command_not_found(self):
        repo = self.repo()
        code, rep, _ = repo.run("--good", repo.shas[0], cmd="./no-such-repro.sh")
        self.assertEqual(code, 3)
        self.assertIn("not found or not executable", rep["message"])
        self.assertRestored(repo)

    def test_bad_ref_passes(self):
        repo = self.repo()
        code, rep, _ = repo.run("--good", repo.shas[0], cmd="true")
        self.assertEqual((code, rep["status"]), (3, "verify_failed"))
        self.assertIn("passes at --bad", rep["message"])
        self.assertIn("--repeat", rep["message"])
        self.assertRestored(repo)


class TimeoutsAndFlakes(Base):
    def test_timeout_means_skip(self):
        # commits 2-3 hang (untestable), 5+ have the bug: the hung commits are skipped
        repo = self.repo(["ok", "ok", "ok hang", "ok hang", "ok", "bug", "bug", "bug"])
        code, rep, _ = repo.run("--good", repo.shas[0], "--timeout", "0.3",
                                cmd="grep -q hang f && sleep 5; ! grep -q bug f")
        self.assertEqual((code, rep["first_bad"]["commit"]), (0, repo.shas[5]), rep)
        skipped = [s for s in rep["steps"] if s["verdict"] == "skip"]
        self.assertTrue(skipped and skipped[0]["runs"][-1]["timed_out"])
        self.assertRestored(repo)

    def test_timeout_means_bad_for_a_hang_regression(self):
        repo = self.repo(["ok", "ok", "ok", "ok", "ok", "hang", "hang", "hang"])
        code, rep, _ = repo.run("--good", repo.shas[0], "--timeout", "0.3", "--timeout-means", "bad",
                                cmd="grep -q hang f && sleep 5; exit 0")
        self.assertEqual((code, rep["first_bad"]["commit"]), (0, repo.shas[5]), rep)

    def test_repeat_catches_a_flaky_bug(self):
        # with the bug present the repro fails only on every 3rd run (a counter file outside the repo)
        counter = os.path.join(self.tmp, "count")
        flaky = (f"grep -q bug f || exit 0; n=$(( $(cat {counter} 2>/dev/null || echo 0) + 1 )); "
                 f"echo $n > {counter}; [ $((n % 3)) -ne 0 ]")
        repo = self.repo()
        code, rep, _ = repo.run("--good", repo.shas[0], cmd=flaky)
        self.assertEqual(code, 3)
        self.assertIn("--repeat", rep["message"])
        os.remove(counter)
        code, rep, _ = repo.run("--good", repo.shas[0], "--repeat", "3", cmd=flaky)
        self.assertEqual((code, rep["first_bad"]["commit"]), (0, repo.shas[5]), rep)
        self.assertRestored(repo)

    def test_different_failure_at_first_bad_commit_warns(self):
        # commit 4 breaks the build (exit 2); the real bug (exit 1) arrives at 5
        repo = self.repo(["ok", "ok", "ok", "ok", "broken", "bug", "bug", "bug"])
        cmd = "grep -q broken f && exit 2; ! grep -q bug f"
        code, rep, _ = repo.run("--good", repo.shas[0], cmd=cmd)
        self.assertEqual((code, rep["first_bad"]["commit"]), (0, repo.shas[4]))
        self.assertTrue(any("--skip-on-exit 2" in w for w in rep["warnings"]), rep["warnings"])
        code, rep, _ = repo.run("--good", repo.shas[0], "--skip-on-exit", "2", cmd=cmd)
        self.assertEqual(rep["status"], "ambiguous")
        self.assertEqual({c["commit"] for c in rep["candidates"]}, {repo.shas[4], repo.shas[5]})


class Interrupt(Base):
    def interrupt_when_hung(self, contents):
        repo = self.repo(contents)
        mark, pidfile = os.path.join(self.tmp, "hung"), os.path.join(self.tmp, "pid")
        cmd = f"if grep -q hang f; then echo $$ > {pidfile}; touch {mark}; sleep 30; fi; ! grep -q bug f"
        p = subprocess.Popen([sys.executable, SCRIPT, "-C", repo.path, "--json", "--good", repo.shas[0],
                              "--timeout", "0", "--", cmd], env=ENV, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        deadline = time.time() + 20
        while not os.path.exists(mark) and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(os.path.exists(mark), "repro never reached the hang")
        time.sleep(0.1)
        p.send_signal(signal.SIGINT)
        out, _ = p.communicate(timeout=20)
        self.assertEqual(p.returncode, 130)
        self.assertEqual(json.loads(out)["status"], "interrupted")
        self.assertRestored(repo)
        self.assertEqual(repo.read(), f"{contents[-1]}\n{len(contents) - 1}\n")
        with open(pidfile) as fh:
            pid = int(fh.read())
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        self.fail("the hung repro was left running")

    def test_ctrl_c_during_bisect_step(self):
        self.interrupt_when_hung(["ok"] + ["hang"] * 6 + ["bug"])

    def test_ctrl_c_during_check_of_good_ref(self):
        self.interrupt_when_hung(["ok hang", "ok", "ok", "bug"])


class Units(unittest.TestCase):
    def test_verdicts(self):
        def v(code, timed_out=False, skip=(), means="skip"):
            return verdict_of({"exit": code, "timed_out": timed_out}, set(skip), means)
        self.assertEqual([v(0), v(1), v(124), v(126), v(127), v(139)], ["good"] + ["bad"] * 5)
        self.assertEqual([v(125), v(2, skip=[2]), v(-1, True), v(-1, True, means="bad")],
                         ["skip", "skip", "skip", "bad"])

    def test_run_once(self):
        self.assertEqual(run_once(["exit 4"], ".", 5)["exit"], 4)
        self.assertEqual(run_once(["definitely-not-a-command-xyz"], ".", 5)["exit"], 127)
        r = run_once(["sleep 5"], ".", 0.2)
        self.assertTrue(r["timed_out"])
        self.assertLess(r["seconds"], 3)
        self.assertEqual(run_once([sys.executable, "-c", "print('hi')"], ".", 5)["tail"], "hi")


if __name__ == "__main__":
    unittest.main()
