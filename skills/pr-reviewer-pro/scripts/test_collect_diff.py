#!/usr/bin/env python3
"""Tests for collect_diff.sh (stdlib unittest, no network).

Run: python3 -m unittest discover -s skills/pr-reviewer-pro/scripts -p 'test_*.py'
Local mode runs against throwaway git repos (some cloned from a local "upstream" so origin/* refs
exist); PR mode runs against a stub `gh` on PATH, so nothing ever talks to GitHub.
"""
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "collect_diff.sh")
BASH = shutil.which("bash") or "/bin/bash"
BASE_ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", TZ="UTC",
                GIT_AUTHOR_NAME="Pat Example", GIT_AUTHOR_EMAIL="pat@example.com",
                GIT_COMMITTER_NAME="Pat Example", GIT_COMMITTER_EMAIL="pat@example.com",
                GIT_AUTHOR_DATE="2026-11-01T12:00:00Z", GIT_COMMITTER_DATE="2026-11-01T12:00:00Z")
for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
    BASE_ENV.pop(var, None)
HEADERS = ["===== BRANCH:", "===== COMMITS =====", "===== FILES =====", "===== DIFF (committed + uncommitted) ====="]


class Repo:
    def __init__(self, path, branch="main", clone_of=None):
        self.path = path
        if clone_of:
            subprocess.run(["git", "clone", "-q", clone_of, path], env=BASE_ENV, check=True, capture_output=True)
        else:
            os.makedirs(path)
            self.git("init", "-q", "-b", branch)

    def git(self, *args):
        r = subprocess.run(["git", *args], cwd=self.path, env=BASE_ENV, capture_output=True, text=True)
        if r.returncode:
            raise AssertionError("git %s: %s" % (" ".join(args), r.stderr))
        return r.stdout

    def write(self, rel, data):
        full = os.path.join(self.path, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb" if isinstance(data, bytes) else "w") as fh:
            fh.write(data)

    def commit(self, files, msg):
        for rel, data in files.items():
            self.write(rel, data)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)

    def collect(self, *args, env=None):
        return subprocess.run([BASH, SCRIPT, *args], cwd=self.path, env=env or BASE_ENV,
                              capture_output=True, text=True, timeout=60)


def section(out, header):
    """Text between `header` and the next ===== line."""
    rest = out.split(header, 1)[1].split("\n", 1)[1]
    return rest.split("=====", 1)[0]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def local(self, branch="main"):
        repo = Repo(os.path.join(self.root, "repo"), branch)
        repo.commit({"app.py": "print('v1')\n"}, "base commit")
        return repo

    def clone(self, upstream_branch="main"):
        up = Repo(os.path.join(self.root, "upstream"), upstream_branch)
        up.commit({"app.py": "print('v1')\n"}, "base commit")
        return Repo(os.path.join(self.root, "clone"), clone_of=up.path)

    def ok(self, repo, *args):
        r = repo.collect(*args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout


class BaseBranchDetection(Base):
    def test_origin_head_wins_even_for_non_main_default(self):
        repo = self.clone("trunk")
        repo.git("checkout", "-q", "-b", "feature")
        self.assertIn("===== BRANCH: feature vs origin/trunk =====", self.ok(repo))

    def test_origin_main_preferred_over_local_main_without_origin_head(self):
        repo = self.clone("main")
        repo.git("remote", "set-head", "origin", "-d")
        repo.git("checkout", "-q", "-b", "feature")
        self.assertIn("vs origin/main =====", self.ok(repo))

    def test_origin_master_fallback(self):
        repo = self.clone("master")
        repo.git("remote", "set-head", "origin", "-d")
        repo.git("checkout", "-q", "-b", "feature")
        self.assertIn("vs origin/master =====", self.ok(repo))

    def test_local_main_fallback(self):
        repo = self.local("main")
        repo.git("checkout", "-q", "-b", "feature")
        self.assertIn("===== BRANCH: feature vs main =====", self.ok(repo))

    def test_local_master_fallback(self):
        repo = self.local("master")
        repo.git("checkout", "-q", "-b", "feature")
        self.assertIn("===== BRANCH: feature vs master =====", self.ok(repo))

    def test_no_base_branch(self):
        repo = self.local("develop")
        r = repo.collect()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        self.assertIn("Could not determine base branch", r.stderr)

    def test_not_a_git_repo(self):
        plain = os.path.join(self.root, "plain")
        os.makedirs(plain)
        r = subprocess.run([BASH, SCRIPT], cwd=plain, capture_output=True, text=True,
                           env=dict(BASE_ENV, GIT_CEILING_DIRECTORIES=os.path.dirname(self.root)))
        self.assertEqual(r.returncode, 1)
        self.assertIn("Not a git repository", r.stderr)

    def test_no_common_history(self):
        repo = self.local("main")
        repo.git("checkout", "-q", "--orphan", "island")
        repo.commit({"other.py": "x = 1\n"}, "unrelated root")
        r = repo.collect()
        self.assertEqual(r.returncode, 1)
        self.assertIn("No common ancestor between main and HEAD", r.stderr)

    def test_detached_head(self):
        repo = self.local("main")
        repo.commit({"app.py": "print('v2')\n"}, "second")
        repo.git("checkout", "-q", "--detach", "HEAD")
        self.assertIn("===== BRANCH: HEAD vs main =====", self.ok(repo))


class LocalDiffOutput(Base):
    def setUp(self):
        super().setUp()
        self.repo = self.local("main")
        self.repo.git("checkout", "-q", "-b", "feature")

    def test_sections_in_order(self):
        self.repo.commit({"app.py": "print('v2')\n"}, "bump version")
        out = self.ok(self.repo)
        positions = [out.index(h) for h in HEADERS]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("bump version", section(out, "===== COMMITS ====="))
        self.assertIn("app.py", section(out, "===== FILES ====="))
        diff = section(out, "===== DIFF (committed + uncommitted) =====")
        self.assertIn("-print('v1')", diff)
        self.assertIn("+print('v2')", diff)

    def test_only_branch_changes_since_merge_base(self):
        self.repo.commit({"feature.py": "f = 1\n"}, "feature work")
        self.repo.git("checkout", "-q", "main")
        self.repo.commit({"mainline.py": "m = 1\n"}, "mainline work")
        self.repo.git("checkout", "-q", "feature")
        out = self.ok(self.repo)
        self.assertIn("feature work", out)
        self.assertNotIn("mainline", out)
        self.assertNotIn("base commit", section(out, "===== COMMITS ====="))

    def test_uncommitted_and_staged_changes_included(self):
        self.repo.write("app.py", "print('dirty')\n")
        self.repo.write("staged.py", "s = 1\n")
        self.repo.git("add", "staged.py")
        diff = section(self.ok(self.repo), "===== DIFF (committed + uncommitted) =====")
        self.assertIn("+print('dirty')", diff)
        self.assertIn("+s = 1", diff)

    def test_empty_diff(self):
        out = self.ok(self.repo)
        for h in HEADERS:
            self.assertIn(h, out)
        self.assertEqual(section(out, "===== COMMITS =====").strip(), "")
        self.assertTrue(out.rstrip().endswith("===== DIFF (committed + uncommitted) ====="))

    def test_on_base_branch_itself(self):
        self.repo.git("checkout", "-q", "main")
        out = self.ok(self.repo)
        self.assertIn("===== BRANCH: main vs main =====", out)
        self.assertTrue(out.rstrip().endswith("===== DIFF (committed + uncommitted) ====="))

    def test_binary_file(self):
        self.repo.commit({"logo.png": b"\x89PNG\r\n\x1a\n\x00\x01\x02\xff"}, "add logo")
        out = self.ok(self.repo)
        self.assertIn("logo.png", section(out, "===== FILES ====="))
        self.assertIn("Binary files /dev/null and b/logo.png differ", out)

    def test_rename(self):
        body = "".join("line %d\n" % i for i in range(20))
        self.repo.git("checkout", "-q", "main")
        self.repo.commit({"old_name.py": body}, "add module")
        self.repo.git("checkout", "-q", "-B", "feature")
        self.repo.git("mv", "old_name.py", "new_name.py")
        self.repo.commit({}, "rename module")
        diff = section(self.ok(self.repo), "===== DIFF (committed + uncommitted) =====")
        self.assertIn("rename from old_name.py", diff)
        self.assertIn("rename to new_name.py", diff)

    def test_deleted_file(self):
        self.repo.git("rm", "-q", "app.py")
        self.repo.commit({}, "drop app")
        self.assertIn("deleted file mode", self.ok(self.repo))


STUB_GH = """#!/bin/sh
echo "$@" >> "$GH_LOG"
case "$1 $2" in
  "pr view") [ -n "$GH_FAIL" ] && { echo "no pull requests found" >&2; exit 1; }
             echo "#42 Add feature by octo (feature -> main)"; echo "+3 -1 in 2 files"; echo; echo "Body text" ;;
  "pr diff") echo "diff --git a/app.py b/app.py" ;;
esac
"""


class PrMode(Base):
    def setUp(self):
        super().setUp()
        self.bin = os.path.join(self.root, "bin")
        os.makedirs(self.bin)
        gh = os.path.join(self.bin, "gh")
        with open(gh, "w") as fh:
            fh.write(STUB_GH)
        os.chmod(gh, 0o755)
        self.log = os.path.join(self.root, "gh.log")
        self.cwd = os.path.join(self.root, "anywhere")  # PR mode does not need a git checkout
        os.makedirs(self.cwd)

    def run_pr(self, **extra):
        env = dict(BASE_ENV, PATH=self.bin + os.pathsep + BASE_ENV.get("PATH", ""), GH_LOG=self.log, **extra)
        return subprocess.run([BASH, SCRIPT, "42"], cwd=self.cwd, env=env, capture_output=True, text=True, timeout=60)

    def test_metadata_then_diff(self):
        r = self.run_pr()
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertLess(out.index("===== PR METADATA ====="), out.index("#42 Add feature"))
        self.assertLess(out.index("#42 Add feature"), out.index("===== DIFF ====="))
        self.assertLess(out.index("===== DIFF ====="), out.index("diff --git a/app.py"))
        with open(self.log) as fh:
            calls = [c for c in fh.read().splitlines() if c.startswith("pr ")]  # --template spans lines
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0].startswith("pr view 42 --json number,title,body,author,baseRefName,headRefName,"))
        self.assertIn("--template", calls[0])
        self.assertEqual(calls[1], "pr diff 42")

    def test_gh_failure_stops_script(self):
        r = self.run_pr(GH_FAIL="1")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("===== DIFF =====", r.stdout)
        self.assertIn("no pull requests found", r.stderr)

    def test_missing_gh(self):
        empty = os.path.join(self.root, "empty")
        os.makedirs(empty)
        r = subprocess.run([BASH, SCRIPT, "42"], cwd=self.cwd, env=dict(BASE_ENV, PATH=empty),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 1)
        self.assertIn("gh CLI is required", r.stderr)
        self.assertEqual(r.stdout, "")


if __name__ == "__main__":
    unittest.main()
