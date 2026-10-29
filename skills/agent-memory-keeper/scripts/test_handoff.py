#!/usr/bin/env python3
"""Tests for handoff.py (stdlib unittest, no network).

Run: python3 -m unittest discover -s skills/agent-memory-keeper/scripts -p 'test_*.py'
Every test builds throwaway git repos in a temp dir. The user's global and system git config are
ignored, so signing, hooks and aliases can't leak in, and identities and dates are fixed.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import handoff  # noqa: E402

SCRIPT = os.path.join(HERE, "handoff.py")
TEMPLATE = os.path.join(HERE, "..", "references", "handoff-template.md")
GIT_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
    "GIT_EDITOR": "true", "GIT_MERGE_AUTOEDIT": "no",
}
_saved = {}


def read(path):
    with open(path) as fh:
        return fh.read()


def setUpModule():
    for k, v in GIT_ENV.items():
        _saved[k] = os.environ.get(k)
        os.environ[k] = v


def tearDownModule():
    for k, v in _saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


class Repo(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "repo")
        os.makedirs(self.root)
        self.git("init", "-q", "-b", "main")
        self.write("README.md", "hello\n")
        self.commit("init")

    def git(self, *args, cwd=None, check=True):
        r = subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=cwd or self.root,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if check and r.returncode:
            raise AssertionError("git %s failed: %s" % (" ".join(args), r.stderr))
        return r

    def write(self, rel, text):
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(text)

    def commit(self, msg, *paths):
        self.git("add", *(paths or ["-A"]))
        self.git("commit", "-q", "-m", msg)

    def run_cli(self, *args, cwd=None):
        r = subprocess.run([sys.executable, SCRIPT, *args], cwd=cwd or self.root,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return r.returncode, r.stdout, r.stderr

    def facts(self, **kw):
        return handoff.gather(self.root, **kw)


class Report(Repo):
    def test_clean_repo(self):
        f = self.facts()
        self.assertEqual(f["branch"], "main")
        self.assertEqual(f["head"]["subject"], "init")
        self.assertEqual(f["working_tree"]["total"], 0)
        self.assertEqual((f["upstream"], f["in_progress"], f["stash"]), (None, [], 0))
        self.assertEqual(f["base"]["ref"], "main")
        self.assertEqual(f["base"]["commit_count"], 0)
        self.assertFalse(f["memory_file"] or f["handoff_file"])

    def test_dirty_staged_untracked(self):
        self.write("README.md", "changed\n")
        self.write("staged.txt", "s\n")
        self.git("add", "staged.txt")
        self.write("new/one.txt", "n\n")
        self.write("MEMORY.md", "# Project Memory\n")
        f = self.facts()
        wt = f["working_tree"]
        codes = {x["path"]: x["code"] for x in wt["files"]}
        self.assertEqual(codes["README.md"], ".M")
        self.assertEqual(codes["staged.txt"], "A.")
        self.assertEqual(codes["new/"], "??")
        self.assertEqual((wt["staged"], wt["unstaged"], wt["untracked"], wt["total"]), (1, 1, 2, 4))
        self.assertTrue(f["memory_file"])

    def test_list_cap(self):
        for i in range(5):
            self.write("f%d.txt" % i, "x\n")
        wt = self.facts(limit=2)["working_tree"]
        self.assertEqual((wt["total"], len(wt["files"]), wt["truncated"]), (5, 2, True))
        self.assertIn("... and 3 more", handoff.render_state(self.facts(limit=2)))

    def test_branch_ahead_of_origin_main(self):
        origin = os.path.join(self.tmp, "origin.git")
        self.git("init", "-q", "--bare", origin, cwd=self.tmp)
        self.git("remote", "add", "origin", origin)
        self.git("push", "-q", "-u", "origin", "main")
        self.git("remote", "set-head", "origin", "main")
        self.git("checkout", "-q", "-b", "feature/x")
        self.write("a.py", "a = 1\n")
        self.commit("add a")
        self.write("b.py", "b = 2\n")
        self.commit("add b")
        self.git("push", "-q", "-u", "origin", "feature/x")
        self.write("a.py", "a = 3\n")
        self.commit("change a")
        f = self.facts()
        self.assertEqual(f["branch"], "feature/x")
        self.assertEqual(f["upstream"], {"name": "origin/feature/x", "ahead": 1, "behind": 0})
        b = f["base"]
        self.assertEqual(b["ref"], "origin/main")
        self.assertEqual(b["commit_count"], 3)
        self.assertEqual([c["subject"] for c in b["commits"]], ["change a", "add b", "add a"])
        self.assertEqual(sorted(c["path"] for c in b["files_changed"]), ["a.py", "b.py"])
        text = handoff.render_state(f)
        self.assertIn("- Upstream: origin/feature/x (ahead 1, behind 0)", text)
        self.assertIn("3 commits on this branch, 2 files changed", text)

    def test_base_fallback_and_override(self):
        self.git("branch", "-m", "master")
        self.git("checkout", "-q", "-b", "topic")
        self.write("t.txt", "t\n")
        self.commit("topic work")
        self.assertEqual(self.facts()["base"]["ref"], "master")
        self.assertIsNone(self.facts(base="nope")["base"])
        self.assertIn("base nope not found", self.facts(base="nope")["notes"])

    def test_todos_in_uncommitted_and_branch_diff(self):
        self.write("lib.py", "x = 1\n# TODO: old one, already committed\n")
        self.commit("lib")
        self.git("checkout", "-q", "-b", "work")
        self.write("lib.py", "x = 1\n# TODO: old one, already committed\ny = 2  # FIXME: wrong unit\n")
        self.commit("branch change")
        self.write("lib.py", "x = 1\n# TODO: old one, already committed\ny = 2  # FIXME: wrong unit\n"
                             "z = 3  # XXX: dirty hack\nprint('TODO/FIXME mention only')\n")
        self.write("scratch.py", "\n\n# TODO: untracked note\n")
        items = self.facts()["todos"]["items"]
        got = sorted((t["scope"], t["path"], t["line"], t["text"]) for t in items)
        self.assertEqual(got, [
            ("branch", "lib.py", 3, "y = 2  # FIXME: wrong unit"),
            ("uncommitted", "lib.py", 4, "z = 3  # XXX: dirty hack"),
            ("untracked", "scratch.py", 3, "# TODO: untracked note"),
        ])

    def test_merge_in_progress(self):
        self.git("checkout", "-q", "-b", "other")
        self.write("README.md", "other\n")
        self.commit("other")
        self.git("checkout", "-q", "main")
        self.write("README.md", "mine\n")
        self.commit("mine")
        self.assertNotEqual(self.git("merge", "other", check=False).returncode, 0)
        f = self.facts()
        self.assertEqual(f["in_progress"], ["merge"])
        self.assertEqual(f["working_tree"]["conflicted"], 1)
        self.assertIn("- In progress: merge", handoff.render_state(f))

    def test_rebase_in_progress(self):
        self.git("checkout", "-q", "-b", "feat")
        self.write("README.md", "feat\n")
        self.commit("feat")
        self.git("checkout", "-q", "main")
        self.write("README.md", "main moved\n")
        self.commit("main moved")
        self.git("checkout", "-q", "feat")
        self.assertNotEqual(self.git("rebase", "main", check=False).returncode, 0)
        f = self.facts()
        self.assertIsNone(f["branch"])
        self.assertEqual(len(f["in_progress"]), 1)
        self.assertTrue(f["in_progress"][0].startswith("rebase of feat"), f["in_progress"])

    def test_stash_count_and_read_only(self):
        self.write("README.md", "stash me\n")
        self.git("stash", "-q")
        self.write("README.md", "dirty\n")
        before = self.git("status", "--porcelain").stdout
        self.assertEqual(self.facts()["stash"], 1)
        self.assertEqual(self.git("status", "--porcelain").stdout, before)
        self.assertEqual(self.git("stash", "list").stdout.count("\n"), 1)

    def test_cli_json(self):
        code, out, _ = self.run_cli("--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["branch"], "main")


class Write(Repo):
    def test_write_skeleton_and_refuse_overwrite(self):
        code, out, _ = self.run_cli("--write", "HANDOFF.md")
        self.assertEqual(code, 0, out)
        text = read(os.path.join(self.root, "HANDOFF.md"))
        self.assertIn("- Branch: main", text)
        self.assertIn("<fill in", text)
        self.assertNotIn("HANDOFF.md", text.split("## Git state")[1], "the handoff file must not list itself")
        with open(os.path.join(self.root, "HANDOFF.md"), "w") as fh:
            fh.write("keep me\n")
        code, _, err = self.run_cli("--write", "HANDOFF.md")
        self.assertEqual(code, 1)
        self.assertIn("refusing to overwrite", err)
        self.assertEqual(read(os.path.join(self.root, "HANDOFF.md")), "keep me\n")
        code, _, _ = self.run_cli("--write", "HANDOFF.md", "--force")
        self.assertEqual(code, 0)
        self.assertIn("## Next step", read(os.path.join(self.root, "HANDOFF.md")))

    def test_skeleton_short_and_sections_match_template(self):
        self.write("notes.py", "".join("# TODO: item %d\n" % i for i in range(12)))
        text = handoff.skeleton(self.facts(), self.root)
        self.assertLess(len(text.splitlines()), 60)
        self.assertIn("- ... and 7 more", text)
        heads = re.findall(r"^## (.+)$", text, re.M)
        self.assertEqual(heads, handoff.SECTIONS)
        tmpl = read(TEMPLATE)
        block = tmpl.split("````markdown", 1)[1].split("\n````", 1)[0]
        self.assertEqual(re.findall(r"^## (.+)$", block, re.M), handoff.SECTIONS)


class Check(Repo):
    def handoff(self, name="HANDOFF.md"):
        code, out, err = self.run_cli("--write", name)
        self.assertEqual(code, 0, err)

    def test_match(self):
        self.write("wip.py", "w\n")
        self.handoff()
        code, out, _ = self.run_cli("--check", "HANDOFF.md")
        self.assertEqual(code, 0, out)
        self.assertIn("MATCH", out)
        self.assertIn("unfilled", out)

    def test_drift_new_commit_and_dirty_files(self):
        self.write("wip.py", "w\n")
        self.handoff()
        self.commit("commit the wip", "wip.py")
        self.write("other.py", "o\n")
        code, out, _ = self.run_cli("--check", "HANDOFF.md", "--json")
        self.assertEqual(code, 1)
        r = json.loads(out)
        self.assertEqual(r["status"], "drift")
        joined = "\n".join(r["drift"])
        self.assertIn("1 new commit(s)", joined)
        self.assertIn("commit the wip", joined)
        self.assertIn("uncommitted since the handoff: other.py", joined)
        self.assertIn("no longer uncommitted (committed, stashed or reverted): wip.py", joined)

    def test_drift_branch_switch(self):
        self.handoff()
        self.git("checkout", "-q", "-b", "elsewhere")
        code, out, _ = self.run_cli("--check", "HANDOFF.md")
        self.assertEqual(code, 1)
        self.assertIn("branch: recorded main, now elsewhere", out)

    def test_committing_only_the_handoff_is_not_drift(self):
        self.handoff()
        self.commit("add handoff", "HANDOFF.md")
        code, out, _ = self.run_cli("--check", "HANDOFF.md")
        self.assertEqual(code, 0, out)
        self.assertIn("committing the handoff file itself", out)

    def test_hand_written_handoff_and_next_step(self):
        sha = self.git("rev-parse", "--short", "HEAD").stdout.strip()
        with open(os.path.join(self.root, "HANDOFF.md"), "w") as fh:
            fh.write("# Handoff\n\n## Next step\nRun `pytest tests/test_api.py::test_retry`\n\n"
                     "## Git state\n- **Branch:** `main`\n- **HEAD:** `%s` init\n- Uncommitted: 0\n" % sha)
        code, out, _ = self.run_cli("--check", "HANDOFF.md")
        self.assertEqual(code, 0, out)
        self.assertIn("pytest tests/test_api.py::test_retry", out)

    def test_head_rewritten(self):
        self.handoff()
        self.git("commit", "-q", "--amend", "-m", "init, amended")
        code, out, _ = self.run_cli("--check", "HANDOFF.md")
        self.assertEqual(code, 1)
        self.assertIn("diverged", out)

    def test_cannot_check(self):
        with open(os.path.join(self.root, "HANDOFF.md"), "w") as fh:
            fh.write("# Handoff\n\n## Goal\nship it\n")
        self.assertEqual(self.run_cli("--check", "HANDOFF.md")[0], 2)
        self.assertEqual(self.run_cli("--check", "missing.md")[0], 2)


class NotARepo(unittest.TestCase):
    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)

    def run_cli(self, *args):
        r = subprocess.run([sys.executable, SCRIPT, "-C", self.dir, *args], cwd=self.dir,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                           env=dict(os.environ, GIT_CEILING_DIRECTORIES=os.path.dirname(self.dir)))
        return r.returncode, r.stdout, r.stderr

    def test_report_exits_2(self):
        code, out, err = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("not a git repository", err)
        code, out, _ = self.run_cli("--json")
        self.assertEqual((code, "error" in json.loads(out)), (2, True))

    def test_write_skeleton_anyway_then_check_cannot(self):
        p = os.path.join(self.dir, "HANDOFF.md")
        code, _, err = self.run_cli("--write", p)
        self.assertEqual(code, 0)
        self.assertIn("not a git repository", err)
        self.assertIn("Not a git repository", read(p))
        self.assertEqual(self.run_cli("--check", p)[0], 2)


if __name__ == "__main__":
    unittest.main()
