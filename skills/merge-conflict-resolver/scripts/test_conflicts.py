#!/usr/bin/env python3
"""Tests for conflicts.py (stdlib unittest, no network).

Run: python3 -m unittest discover -s skills/merge-conflict-resolver/scripts -p 'test_*.py'
Each test builds a throwaway git repo with a real conflict (merge, rebase, cherry-pick, revert, am,
stash pop, modify/delete, renames, a lockfile) and checks the parsed report. Git runs with an empty
global config, so the user's settings (conflict style, rerere, hooks) never leak in.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from conflicts import parse_hunks  # noqa: E402

SCRIPT = os.path.join(HERE, "conflicts.py")
ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", LC_ALL="C",
           GIT_AUTHOR_NAME="Ada Tester", GIT_AUTHOR_EMAIL="ada@example.com",
           GIT_COMMITTER_NAME="Ada Tester", GIT_COMMITTER_EMAIL="ada@example.com",
           GIT_AUTHOR_DATE="2026-10-01T12:00:00Z", GIT_COMMITTER_DATE="2026-10-01T12:00:00Z")
ENV.pop("GIT_DIR", None)
ENV.pop("GIT_WORK_TREE", None)


class Repo:
    def __init__(self, path):
        self.path = path
        os.makedirs(path)
        self.git("init", "-q", "-b", "main")

    def git(self, *args, check=True):
        r = subprocess.run(["git", *args], cwd=self.path, env=ENV, capture_output=True, text=True)
        if check and r.returncode:
            raise AssertionError("git %s: %s" % (" ".join(args), r.stderr))
        return r.stdout.strip()

    def write(self, name, text, mode="w"):
        full = os.path.join(self.path, name)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, mode) as fh:
            fh.write(text)

    def read(self, name):
        with open(os.path.join(self.path, name)) as fh:
            return fh.read()

    def commit(self, msg, **files):
        for name, text in files.items():
            self.write(name.replace("__", "/"), text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "--short", "HEAD")

    def run(self, *args):
        r = subprocess.run([sys.executable, SCRIPT, "-C", self.path, *args], env=ENV, capture_output=True,
                           text=True, timeout=60)
        return r.returncode, r.stdout, r.stderr

    def report(self, *args):
        code, out, err = self.run("--json", *args)
        return code, json.loads(out)


CONFIG = "host = localhost\nport = 8080\ntimeout = 30\nretries = 3\n"


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Repo(os.path.join(self._tmp.name, "repo"))

    def tearDown(self):
        self._tmp.cleanup()

    def diverge(self, ours, theirs, base=None, name="app.cfg"):
        """main changes `name` to ours, branch feature changes it to theirs."""
        r = self.repo
        r.commit("add config", **{name: base or CONFIG})
        r.git("checkout", "-q", "-b", "feature")
        r.commit("raise timeout for slow uploads", **{name: theirs})
        r.git("checkout", "-q", "main")
        r.commit("move service to port 9090", **{name: ours})

    def file(self, rep, path):
        return next(f for f in rep["files"] if f["path"] == path)


OURS = CONFIG.replace("8080", "9090").replace("timeout = 30", "timeout = 45")
THEIRS = CONFIG.replace("timeout = 30", "timeout = 120")


class TestOperations(Base):
    def test_merge_content_conflict(self):
        self.diverge(OURS, THEIRS)
        self.repo.git("merge", "feature", check=False)
        code, rep = self.repo.report()
        self.assertEqual(code, 1)
        op = rep["operation"]
        self.assertEqual(op["kind"], "merge")
        self.assertIn("merging feature into main", op["description"])
        self.assertEqual(op["continue"], "git merge --continue")
        f = self.file(rep, "app.cfg")
        self.assertEqual((f["kind"], f["status"]), ("content", "UU"))
        self.assertEqual(len(f["hunks"]), 1)
        h = f["hunks"][0]
        self.assertIn("timeout = 45", h["ours"])
        self.assertIn("timeout = 120", h["theirs"])
        self.assertIn("timeout = 30", h["base"])  # rebuilt with git merge-file
        self.assertTrue(h["base_source"].startswith("git merge-file"))
        self.assertEqual(self.repo.read("app.cfg").split("\n")[h["start"] - 1][:7], "<" * 7)
        self.assertEqual(self.repo.read("app.cfg").split("\n")[h["end"] - 1][:7], ">" * 7)
        self.assertEqual([c["subject"] for c in f["commits"]["ours"]], ["move service to port 9090"])
        self.assertEqual([c["subject"] for c in f["commits"]["theirs"]], ["raise timeout for slow uploads"])

    def test_diff3_markers_give_base_directly(self):
        self.repo.git("config", "merge.conflictStyle", "diff3")
        self.diverge(OURS, THEIRS)
        self.repo.git("merge", "feature", check=False)
        _, rep = self.repo.report()
        self.assertEqual(rep["conflict_style"], "diff3")
        h = self.file(rep, "app.cfg")["hunks"][0]
        self.assertEqual(h["base_source"], "markers")
        self.assertIn("timeout = 30", h["base"])

    def test_rebase_reports_progress_and_swapped_sides(self):
        r = self.repo
        r.commit("add config", **{"app.cfg": CONFIG})
        r.git("checkout", "-q", "-b", "feature")
        r.commit("add readme", **{"README": "hi\n"})
        r.commit("raise timeout for slow uploads", **{"app.cfg": THEIRS})
        r.commit("add notes", **{"NOTES": "n\n"})
        stopped = r.git("rev-parse", "HEAD~1")
        r.git("checkout", "-q", "main")
        r.commit("move service to port 9090", **{"app.cfg": OURS})
        r.git("checkout", "-q", "feature")
        r.git("rebase", "main", check=False)
        code, rep = r.report()
        self.assertEqual(code, 1)
        op = rep["operation"]
        self.assertEqual(op["kind"], "rebase")
        self.assertEqual(op["progress"], {"current": 2, "total": 3})
        self.assertIn("rebasing feature onto main, commit 2/3", op["description"])
        self.assertEqual(op["theirs"]["sha"], stopped)
        self.assertIn("rebase onto", op["ours"]["meaning"])
        self.assertEqual(op["continue"], "git rebase --continue")
        h = self.file(rep, "app.cfg")["hunks"][0]
        self.assertIn("timeout = 45", h["ours"])  # ours = main during a rebase
        self.assertIn("timeout = 120", h["theirs"])
        self.assertIn("raise timeout for slow uploads",
                      [c["subject"] for c in self.file(rep, "app.cfg")["commits"]["theirs"]])

    def test_rebase_apply_backend(self):
        self.diverge(OURS, THEIRS)
        self.repo.git("checkout", "-q", "feature")
        self.repo.git("rebase", "--apply", "main", check=False)
        _, rep = self.repo.report()
        self.assertEqual(rep["operation"]["kind"], "rebase")
        self.assertEqual(rep["operation"]["progress"], {"current": 1, "total": 1})

    def test_cherry_pick_sequence(self):
        self.diverge(OURS, THEIRS)
        r = self.repo
        r.git("checkout", "-q", "feature")
        r.commit("add notes", **{"NOTES": "n\n"})
        r.git("checkout", "-q", "main")
        r.git("cherry-pick", "feature~1", "feature", check=False)
        _, rep = r.report()
        op = rep["operation"]
        self.assertEqual(op["kind"], "cherry-pick")
        self.assertEqual(op["theirs"]["sha"], r.git("rev-parse", "feature~1"))
        self.assertEqual(op["progress"], {"remaining_including_current": 2})
        self.assertIn("raise timeout for slow uploads", op["description"])
        self.assertEqual(op["continue"], "git cherry-pick --continue")

    def test_revert_lists_reverted_commit(self):
        r = self.repo
        r.commit("add config", **{"app.cfg": CONFIG})
        bad = r.commit("raise timeout for slow uploads", **{"app.cfg": THEIRS})
        r.commit("tune timeout again", **{"app.cfg": THEIRS.replace("120", "90")})
        r.git("revert", "--no-edit", bad, check=False)
        _, rep = r.report()
        self.assertEqual(rep["operation"]["kind"], "revert")
        f = self.file(rep, "app.cfg")
        self.assertEqual([c["subject"] for c in f["commits"]["theirs"]], ["raise timeout for slow uploads"])
        self.assertEqual([c["subject"] for c in f["commits"]["ours"]], ["tune timeout again"])
        self.assertIn("timeout = 30", f["hunks"][0]["theirs"])

    def test_am(self):
        self.diverge(OURS, THEIRS)
        r = self.repo
        patches = os.path.join(self._tmp.name, "patches")
        r.git("format-patch", "-q", "-1", "feature", "-o", patches)
        r.git("am", "-3", os.path.join(patches, os.listdir(patches)[0]), check=False)
        _, rep = r.report()
        self.assertEqual(rep["operation"]["kind"], "am")
        self.assertIn("raise timeout for slow uploads", rep["operation"]["description"])

    def test_stash_pop_detected_from_labels(self):
        r = self.repo
        r.commit("add config", **{"app.cfg": CONFIG})
        r.write("app.cfg", THEIRS)
        r.git("stash", "-q")
        r.commit("move service to port 9090", **{"app.cfg": OURS})
        r.git("stash", "pop", check=False)
        _, rep = r.report()
        op = rep["operation"]
        self.assertEqual(op["kind"], "stash")
        self.assertIn("stash drop", op["continue"])
        self.assertEqual(op["theirs"]["sha"], r.git("rev-parse", "refs/stash"))

    def test_no_conflicts_exit_0(self):
        self.repo.commit("add config", **{"app.cfg": CONFIG})
        code, rep = self.repo.report()
        self.assertEqual(code, 0)
        self.assertEqual(rep["files"], [])
        self.assertIsNone(rep["operation"]["kind"])

    def test_not_a_repo_exit_2(self):
        empty = os.path.join(self._tmp.name, "plain")
        os.makedirs(empty)
        r = subprocess.run([sys.executable, SCRIPT, "-C", empty], env=ENV, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("not inside a git repository", r.stderr)


class TestKinds(Base):
    def test_modify_delete(self):
        r = self.repo
        r.commit("add files", **{"app.cfg": CONFIG, "old.py": "x = 1\n"})
        r.git("checkout", "-q", "-b", "feature")
        r.git("rm", "-q", "old.py")
        r.git("commit", "-q", "-m", "drop old module")
        r.git("checkout", "-q", "main")
        r.commit("fix old module", **{"old.py": "x = 2\n"})
        r.git("merge", "feature", check=False)
        _, rep = r.report()
        f = self.file(rep, "old.py")
        self.assertEqual((f["kind"], f["status"]), ("modify/delete", "UD"))
        self.assertEqual(f["hunks"], [])
        self.assertEqual([c["subject"] for c in f["commits"]["theirs"]], ["drop old module"])

    def test_rename_rename(self):
        r = self.repo
        r.commit("add util", **{"util.py": "def f():\n    return 1\n"})
        r.git("checkout", "-q", "-b", "feature")
        r.git("mv", "util.py", "helpers.py")
        r.git("commit", "-q", "-m", "rename util to helpers")
        r.git("checkout", "-q", "main")
        r.git("mv", "util.py", "tools.py")
        r.git("commit", "-q", "-m", "rename util to tools")
        r.git("merge", "feature", check=False)
        _, rep = r.report()
        self.assertEqual(self.file(rep, "util.py")["kind"], "both-deleted")
        theirs = self.file(rep, "helpers.py")
        self.assertEqual((theirs["kind"], theirs["renamed_from"]), ("rename", "util.py"))
        self.assertIn("on theirs", theirs["detail"])
        self.assertIn("on ours", self.file(rep, "tools.py")["detail"])

    def test_rename_delete(self):
        r = self.repo
        r.commit("add util", **{"util.py": "def f():\n    return 1\n"})
        r.git("checkout", "-q", "-b", "feature")
        r.git("rm", "-q", "util.py")
        r.git("commit", "-q", "-m", "drop util")
        r.git("checkout", "-q", "main")
        r.git("mv", "util.py", "tools.py")
        r.git("commit", "-q", "-m", "rename util to tools")
        r.git("merge", "feature", check=False)
        _, rep = r.report()
        f = self.file(rep, "tools.py")
        self.assertEqual(f["kind"], "rename")
        self.assertIn("rename/delete", f["detail"])

    def test_add_add(self):
        r = self.repo
        r.commit("init", **{"README": "x\n"})
        r.git("checkout", "-q", "-b", "feature")
        r.commit("add ci on feature", **{"ci.yml": "runs-on: ubuntu-latest\n"})
        r.git("checkout", "-q", "main")
        r.commit("add ci on main", **{"ci.yml": "runs-on: macos-latest\n"})
        r.git("merge", "feature", check=False)
        _, rep = r.report()
        f = self.file(rep, "ci.yml")
        self.assertEqual((f["kind"], f["status"]), ("add/add", "AA"))
        self.assertEqual(len(f["hunks"]), 1)
        self.assertIsNone(f["hunks"][0]["base"])  # no base version exists

    def test_binary(self):
        r = self.repo
        r.write("logo.png", b"\x89PNG\x00\x01", "wb")
        r.commit("add logo")
        r.git("checkout", "-q", "-b", "feature")
        r.write("logo.png", b"\x89PNG\x00\x02", "wb")
        r.commit("new logo")
        r.git("checkout", "-q", "main")
        r.write("logo.png", b"\x89PNG\x00\x03", "wb")
        r.commit("dark logo")
        r.git("merge", "feature", check=False)
        _, rep = r.report()
        f = self.file(rep, "logo.png")
        self.assertTrue(f["binary"])
        self.assertEqual(f["kind"], "binary")

    def test_package_lock_is_flagged_with_regenerate_command(self):
        lock = '{\n  "name": "web",\n  "lockfileVersion": 3,\n  "packages": {\n    "node_modules/a": {\n' \
               '      "version": "%s"\n    }\n  }\n}\n'
        r = self.repo
        r.commit("add lock", **{"web__package-lock.json": lock % "1.0.0", "web__package.json": "{}\n"})
        r.git("checkout", "-q", "-b", "feature")
        r.commit("bump a to 1.2", **{"web__package-lock.json": lock % "1.2.0"})
        r.git("checkout", "-q", "main")
        r.commit("bump a to 1.1", **{"web__package-lock.json": lock % "1.1.0"})
        r.git("merge", "feature", check=False)
        code, rep = r.report()
        f = self.file(rep, "web/package-lock.json")
        self.assertEqual(f["generated"], {"type": "lockfile", "tool": "npm",
                                          "regenerate": "npm install --package-lock-only",
                                          "manifest": "web/package.json"})
        self.assertEqual(rep["summary"]["lockfiles"], 1)
        _, text, _ = r.run()
        self.assertIn("do not hand-merge", text)
        self.assertNotIn("hunk 1", text)  # lockfile hunks are not printed in text mode

    def test_generated_header_detected(self):
        src = "// Code generated by protoc-gen-go. DO NOT EDIT.\npackage pb\nvar V = %d\n"
        self.diverge(src % 2, src % 3, base=src % 1, name="api.pb.go")
        self.repo.git("merge", "feature", check=False)
        _, rep = self.repo.report()
        self.assertEqual(self.file(rep, "api.pb.go")["generated"]["type"], "generated")

    def test_path_filter(self):
        r = self.repo
        r.commit("init", **{"a.txt": "1\n", "b.txt": "1\n"})
        r.git("checkout", "-q", "-b", "feature")
        r.commit("feature", **{"a.txt": "2\n", "b.txt": "2\n"})
        r.git("checkout", "-q", "main")
        r.commit("main", **{"a.txt": "3\n", "b.txt": "3\n"})
        r.git("merge", "feature", check=False)
        _, rep = r.report("b.txt")
        self.assertEqual([f["path"] for f in rep["files"]], ["b.txt"])

    def test_text_output(self):
        self.diverge(OURS, THEIRS)
        self.repo.git("merge", "feature", check=False)
        code, out, _ = self.repo.run()
        self.assertEqual(code, 1)
        self.assertIn("Operation: merging feature into main", out)
        self.assertIn("== app.cfg  [content]", out)
        self.assertIn("raise timeout for slow uploads", out)
        self.assertIn("timeout = 120", out)


class TestParseHunks(unittest.TestCase):
    def test_two_hunks_with_labels_and_ranges(self):
        text = "a\n<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> feature\nb\n<<<<<<< HEAD\n=======\nz\n>>>>>>> feature\n"
        hunks, stray = parse_hunks(text)
        self.assertEqual(stray, [])
        self.assertEqual([(h["start"], h["end"]) for h in hunks], [(2, 6), (8, 11)])
        self.assertEqual((hunks[0]["ours_label"], hunks[0]["theirs_label"]), ("HEAD", "feature"))
        self.assertEqual((hunks[1]["ours"], hunks[1]["theirs"]), ("", "z\n"))

    def test_diff3_base_and_crlf(self):
        text = "<<<<<<< ours\r\nx\r\n||||||| base\r\nw\r\n=======\r\ny\r\n>>>>>>> theirs\r\n"
        (h,), _ = parse_hunks(text)
        self.assertEqual((h["base_label"], h["base"]), ("base", "w\r\n"))

    def test_markdown_underline_is_not_a_conflict(self):
        hunks, stray = parse_hunks("Title\n=======\n\nText\n")
        self.assertEqual((hunks, stray), ([], []))

    def test_unterminated_and_longer_markers(self):
        hunks, stray = parse_hunks("<<<<<<< HEAD\nx\n=======\ny\n")
        self.assertEqual(hunks, [])
        self.assertEqual(stray[0]["line"], 1)
        hunks, stray = parse_hunks("<<<<<<<< HEAD\nx\n========\ny\n>>>>>>>> b\n")
        self.assertEqual((hunks, stray), ([], []))  # 8-char markers are not size-7 markers
        hunks, _ = parse_hunks("<<<<<<<< HEAD\nx\n========\ny\n>>>>>>>> b\n", size=8)
        self.assertEqual(len(hunks), 1)


class TestCheck(Base):
    def conflict(self):
        self.diverge(OURS, THEIRS)
        self.repo.git("merge", "feature", check=False)

    def test_unmerged_paths_fail(self):
        self.conflict()
        code, out, _ = self.repo.run("--check")
        self.assertEqual(code, 1)
        self.assertIn("app.cfg: unmerged path (UU)", out)

    def test_markers_added_by_mistake_fail_with_line(self):
        self.conflict()
        self.repo.git("add", "app.cfg")  # marked resolved without editing
        code, out, _ = self.repo.run("--check")
        self.assertEqual(code, 1)
        self.assertIn("app.cfg:", out)
        self.assertIn("conflict markers (lines", out)

    def test_staged_markers_caught_when_worktree_is_clean(self):
        self.conflict()
        self.repo.git("add", "app.cfg")
        self.repo.write("app.cfg", THEIRS)  # fixed on disk but not re-added
        code, res = self.repo.report("--check")
        self.assertEqual(code, 1)
        self.assertEqual([p["where"] for p in res["problems"]], ["staged"])

    def test_resolved_is_clean(self):
        self.conflict()
        self.repo.write("app.cfg", OURS.replace("timeout = 45", "timeout = 120"))
        self.repo.git("add", "app.cfg")
        code, out, _ = self.repo.run("--check")
        self.assertEqual(code, 0)
        self.assertIn("Clean", out)

    def test_markdown_underlines_and_stray_markers_do_not_fail(self):
        r = self.repo
        r.commit("docs", **{"docs.md": "Title\n=======\n\nSection\n-------\n",
                            "notes.rst": "Heading\n=======\n\n>>>>>>> quoted in a log excerpt\n"})
        code, res = r.report("--check")
        self.assertEqual(code, 0)
        self.assertEqual(res["problems"], [])
        self.assertEqual([w["path"] for w in res["warnings"]], ["notes.rst"])

    def test_custom_marker_size_attribute(self):
        r = self.repo
        r.commit("attrs", **{".gitattributes": "*.txt conflict-marker-size=9\n",
                             "a.txt": "<<<<<<<<< HEAD\nx\n=========\ny\n>>>>>>>>> b\n"})
        code, res = r.report("--check")
        self.assertEqual(code, 1)
        self.assertEqual(res["problems"][0]["path"], "a.txt")


if __name__ == "__main__":
    unittest.main()
