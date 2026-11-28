#!/usr/bin/env python3
"""Tests for commit_facts.py (stdlib unittest, no network).

Run: python3 -m unittest discover -s skills/commit-craft/scripts -p 'test_*.py'
Each test builds a throwaway git repo with made-up authors and fixed dates, then checks the change
report, the house-style detection and the message lint. Git runs with an empty global config.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import commit_facts as cf  # noqa: E402

SCRIPT = os.path.join(HERE, "commit_facts.py")
RELEASE_NOTES = os.path.join(HERE, "..", "..", "changelog-from-git", "scripts", "release_notes.py")
BASE_ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", LC_ALL="C", TZ="UTC",
                GIT_AUTHOR_NAME="Ada Example", GIT_AUTHOR_EMAIL="ada@example.com",
                GIT_COMMITTER_NAME="Ada Example", GIT_COMMITTER_EMAIL="ada@example.com",
                GIT_AUTHOR_DATE="2026-10-01T12:00:00Z", GIT_COMMITTER_DATE="2026-10-01T12:00:00Z")
for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
    BASE_ENV.pop(var, None)


class Repo:
    def __init__(self, root):
        self.path = os.path.join(root, "repo")
        os.makedirs(self.path)
        self.git("init", "-q", "-b", "main")

    def git(self, *args):
        r = subprocess.run(["git", *args], cwd=self.path, env=BASE_ENV, capture_output=True, text=True)
        if r.returncode:
            raise AssertionError("git %s: %s" % (" ".join(args), r.stderr))
        return r.stdout

    def write(self, rel, text):
        full = os.path.join(self.path, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as fh:
            fh.write(text)

    def commit(self, message, files=None):
        for rel, text in (files or {"log.txt": message + "\n"}).items():
            self.write(rel, text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def run(self, *args):
        return subprocess.run([sys.executable, SCRIPT, "-C", self.path, *args], env=BASE_ENV,
                              capture_output=True, text=True, timeout=60)

    def report(self, *args):
        r = self.run("--json", *args)
        if r.returncode not in (0, 1):
            raise AssertionError(r.stderr)
        return json.loads(r.stdout)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repo(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def files(self, rep):
        return {f["path"]: f for f in rep["files"]}


class RegexTests(unittest.TestCase):
    @unittest.skipUnless(os.path.exists(RELEASE_NOTES), "changelog-from-git not installed alongside")
    def test_cc_regex_lines_match_release_notes(self):
        def lines(path):
            with open(path, encoding="utf-8") as fh:
                return [ln for ln in fh if ln.startswith(("SUBJECT_RE =", "BREAKING_RE =", "FOOTER_RE ="))]
        self.assertEqual(len(lines(SCRIPT)), 3)
        self.assertEqual(lines(SCRIPT), lines(RELEASE_NOTES))


class KindTests(unittest.TestCase):
    def test_kinds(self):
        cases = {"package-lock.json": "deps", "services/api/go.sum": "deps", "requirements-dev.txt": "deps",
                 ".github/workflows/ci.yml": "ci", "Jenkinsfile": "ci", "README.md": "docs",
                 "docs/guide/setup.txt": "docs", "LICENSE": "docs", "tests/test_auth.py": "tests",
                 "src/auth/token.test.ts": "tests", "pkg/http/client_test.go": "tests",
                 "src/main/java/AppTest.java": "tests", ".eslintrc.json": "config", "vite.config.ts": "config",
                 "Dockerfile.dev": "config", "src/auth/token.ts": "source", "cmd/main.go": "source",
                 "src/history.ts": "source", "LicenseManager.java": "source", "app/authors_controller.rb": "source",
                 "LICENSE.txt": "docs", "CHANGES.rst": "docs"}
        for path, kind in cases.items():
            self.assertEqual(cf.kind_of(path), kind, path)


class ChangeTests(Base):
    def setUp(self):
        super().setUp()
        self.repo.commit("feat: initial", {"src/app.py": "def f():\n    return 1\n", "src/util.py": "x = 1\ny = 2\n",
                                           "README.md": "# App\n", "package.json": "{}\n"})

    def test_staged_unstaged_untracked_with_counts(self):
        self.repo.write("src/app.py", "def f():\n    return 2\n\ndef g():\n    return 3\n")
        self.repo.git("add", "src/app.py")
        self.repo.write("README.md", "# App\n\nUsage.\n")
        self.repo.write("tests/test_app.py", "import app\n\n\ndef test_f():\n    pass\n")
        f = self.files(self.repo.report())
        self.assertEqual(f["src/app.py"]["staged"]["added"], 4)
        self.assertEqual(f["src/app.py"]["staged"]["deleted"], 1)
        self.assertIsNone(f["src/app.py"]["unstaged"])
        self.assertEqual((f["README.md"]["unstaged"]["added"], f["README.md"]["kind"]), (2, "docs"))
        self.assertEqual(f["tests/test_app.py"]["untracked"]["lines"], 5)
        self.assertEqual(f["tests/test_app.py"]["kind"], "tests")

    def test_whitespace_only_is_formatting(self):
        self.repo.write("src/util.py", "x  =  1\ny = 2   \n")
        self.repo.write("src/app.py", "def f():\n    return 9\n")
        rep = self.repo.report()
        self.assertEqual(rep["formatting_only"], ["src/util.py"])
        kinds = {(g["dir"], g["kind"]): g["files"] for g in rep["groups"]}
        self.assertEqual(kinds[("*", "formatting")], ["src/util.py"])
        self.assertEqual(kinds[("src", "source")], ["src/app.py"])

    def test_blank_line_only_change_is_not_formatting(self):
        self.repo.write("src/util.py", "x = 1\n\ny = 2\n")
        self.assertEqual(self.repo.report()["formatting_only"], [])

    def test_mode_only_change_is_not_formatting(self):
        os.chmod(os.path.join(self.repo.path, "src/util.py"), 0o755)
        rep = self.repo.report()
        self.assertIn("src/util.py", self.files(rep))
        self.assertEqual(rep["formatting_only"], [])

    def test_binary_untracked_and_staged(self):
        with open(os.path.join(self.repo.path, "logo.png"), "wb") as fh:
            fh.write(b"\x89PNG\0\0\x01\x02")
        f = self.files(self.repo.report())
        self.assertIsNone(f["logo.png"]["untracked"]["lines"])
        self.repo.git("add", "logo.png")
        f = self.files(self.repo.report())
        self.assertIsNone(f["logo.png"]["staged"]["added"])
        self.assertFalse(f["logo.png"]["formatting_only"])

    def test_staged_and_unstaged(self):
        self.repo.write("src/app.py", "def f():\n    return 2\n")
        self.repo.git("add", "src/app.py")
        self.repo.write("src/app.py", "def f():\n    return 3\n")
        rep = self.repo.report()
        self.assertEqual(rep["staged_and_unstaged"], ["src/app.py"])
        self.assertIn("Staged AND unstaged", self.repo.run().stdout)

    def test_deps_grouped_apart_from_source(self):
        self.repo.write("package.json", '{"dependencies": {}}\n')
        self.repo.write("package-lock.json", "{}\n")
        self.repo.write("src/app.py", "def f():\n    return 5\n")
        groups = [(g["dir"], g["kind"]) for g in self.repo.report()["groups"]]
        self.assertEqual(groups, [("src", "source"), ("(root)", "deps")])

    def test_conflict_is_reported_not_staged(self):
        self.repo.git("checkout", "-q", "-b", "other")
        self.repo.commit("fix: other", {"src/util.py": "x = 3\n"})
        self.repo.git("checkout", "-q", "main")
        self.repo.commit("fix: main", {"src/util.py": "x = 4\n"})
        subprocess.run(["git", "merge", "-q", "other"], cwd=self.repo.path, env=BASE_ENV, capture_output=True)
        rep = self.repo.report()
        self.assertEqual(rep["conflicted"], ["src/util.py"])
        self.assertEqual(rep["staged_and_unstaged"], [])

    def test_read_only(self):
        self.repo.write("src/app.py", "def f():\n    return 7\n")
        self.repo.write("new.txt", "hi\n")
        index = os.path.join(self.repo.path, ".git", "index")
        self.repo.git("status", "--porcelain")  # let git refresh the index before taking the snapshot

        def snapshot():
            with open(index, "rb") as fh:
                return fh.read(), os.stat(index).st_mtime_ns
        before = snapshot()
        self.repo.run()
        self.repo.run("--lint", "fix: x")
        self.assertEqual(snapshot(), before)
        self.assertEqual(self.repo.git("status", "--porcelain"), " M src/app.py\n?? new.txt\n")

    def test_clean_tree_and_not_a_repo(self):
        self.assertIn("Nothing to commit", self.repo.run().stdout)
        r = subprocess.run([sys.executable, SCRIPT, "-C", self.tmp.name], env=BASE_ENV, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)


class StyleTests(Base):
    def test_conventional_repo(self):
        for i in range(24):
            self.repo.commit(["feat(api): add endpoint %d" % i, "fix: handle case %d" % i, "chore(deps): bump %d" % i][i % 3])
        self.repo.commit('Revert "feat(api): add endpoint 0"')
        st = self.repo.report()["style"]
        self.assertEqual((st["commits"], st["verdict"]), (24, "conventional"))
        self.assertEqual(st["recent_subjects"][0], 'Revert "feat(api): add endpoint 0"')
        self.assertEqual(st["types"], {"feat": 8, "fix": 8, "chore": 8})
        self.assertEqual(st["scopes"], {"api": 8, "deps": 8})
        self.assertEqual((st["capitalisation"], st["trailing_period"]), ("lowercase", "no period"))
        self.assertEqual(st["confidence"], "medium")  # under 50 commits

    def test_area_prefix_repo_is_not_conventional(self):
        for i in range(30):
            self.repo.commit(["net/http: fix timeout %d" % i, "runtime: reduce pauses %d" % i, "cmd/go: add flag %d" % i][i % 3])
        st = self.repo.report()["style"]
        self.assertEqual(st["verdict"], "area-prefix")
        self.assertEqual(st["common_type_share"], 0)
        self.assertEqual(st["conventional_share"], 0.33)  # runtime: matches the bare CC shape

    def test_free_form_with_bodies_and_trailers(self):
        for i in range(20):
            self.repo.commit("Fix the parser for case %d.\n\nLonger reason here.\n\nSigned-off-by: Ada Example <ada@example.com>" % i)
        st = self.repo.report()["style"]
        self.assertEqual(st["verdict"], "free-form")
        self.assertEqual((st["capitalisation"], st["trailing_period"], st["body_share"]), ("capitalised", "period", 1.0))
        self.assertEqual(st["trailers"], {"Signed-off-by": 20})

    def test_trailers_counted_once_per_commit_ignoring_case(self):
        self.repo.commit("Add a\n\nWhy.\n\nReviewed-by: Bo Sample <bo@example.com>\nReviewed-by: Cy Sample <cy@example.com>")
        self.repo.commit("Add b\n\nWhy.\n\nreviewed-by: Bo Sample <bo@example.com>")
        trailers = self.repo.report()["style"]["trailers"]  # spelling of the newest commit wins
        self.assertEqual({k.lower(): v for k, v in trailers.items()}, {"reviewed-by": 2})

    def test_trailer_only_body_is_not_a_body(self):
        self.repo.commit("Add thing\n\nCo-authored-by: Bo Sample <bo@example.com>")
        st = self.repo.report()["style"]
        self.assertEqual((st["body_share"], st["trailers"]), (0, {"Co-authored-by": 1}))

    def test_no_history(self):
        st = self.repo.report()["style"]
        self.assertEqual((st["commits"], st["verdict"], st["confidence"]), (0, "unknown", "low"))


class LintTests(Base):
    def lint(self, message, *args):
        r = self.repo.run("--json", "--lint", message, *args)
        rep = json.loads(r.stdout)
        return r.returncode, {(f["level"], f["rule"]) for f in rep["findings"]}, rep

    def cc_history(self):
        for i in range(20):
            self.repo.commit(["feat: add part %d" % i, "fix(core): repair part %d" % i][i % 2])

    def test_good_conventional_message_passes(self):
        self.cc_history()
        rc, found, rep = self.lint("fix(core): stop double retries\n\nRetries doubled after a timeout.\n\nRefs: #12")
        self.assertEqual((rc, found, rep["conventional_enforced"]), (0, set(), True))

    def test_subject_lengths(self):
        rc, found, _ = self.lint("x" * 60)
        self.assertEqual((rc, found), (0, {("warning", "subject-length")}))
        rc, found, _ = self.lint("x" * 73)
        self.assertEqual((rc, found), (1, {("error", "subject-length")}))

    def test_long_subject_only_warns_in_a_long_subject_repo(self):
        for i in range(5):
            self.repo.commit("Rework the retry loop so slow upstream calls stop doubling every request %d" % i)
        rc, found, _ = self.lint("x" * 80)
        self.assertEqual((rc, found), (0, {("warning", "subject-length")}))

    def test_blank_line_and_body_wrap(self):
        long_line = "word " * 20
        rc, found, _ = self.lint("Add thing\nno blank\n\n%s\n    %s\nSee https://example.com/%s" % (long_line, long_line, "a" * 80))
        self.assertEqual(rc, 1)
        self.assertEqual(found, {("error", "blank-line"), ("warning", "body-wrap")})

    def test_conventional_only_enforced_when_repo_uses_it(self):
        for i in range(20):
            self.repo.commit("Improve the cache %d" % i)
        rc, found, rep = self.lint("Make the cache faster")
        self.assertEqual((rc, rep["conventional_enforced"]), (0, False))
        rc, found, _ = self.lint("Make the cache faster", "--conventional")
        self.assertIn(("error", "conventional-subject"), found)

    def test_breaking_footer_format(self):
        self.cc_history()
        rc, found, _ = self.lint("feat!: drop v1\n\nWhy.\n\nBreaking change: v1 is gone")
        self.assertEqual(rc, 1)
        self.assertIn(("error", "breaking-footer"), found)
        rc, found, _ = self.lint("feat!: drop v1\n\nWhy.\n\nBREAKING-CHANGE: v1 is gone")
        self.assertEqual((rc, found), (0, set()))

    def test_unknown_type_and_house_case(self):
        self.cc_history()
        rc, found, _ = self.lint("feet: Add part")
        self.assertEqual(rc, 0)
        self.assertEqual(found, {("warning", "unknown-type"), ("warning", "house-case")})

    def test_cc_prefix_not_pushed_on_free_form_or_area_repo(self):
        for i in range(20):
            self.repo.commit("http2: fix stream leak %d" % i)
        rc, found, rep = self.lint("feat: Add stream pool")
        self.assertEqual((rc, rep["conventional_enforced"]), (0, False))
        self.assertEqual(found, {("warning", "house-prefix"), ("warning", "house-case")})
        self.assertEqual(self.lint("http2: add stream pool")[1], set())

    def test_git_generated_subjects_skip_length_and_cc(self):
        self.cc_history()
        rc, found, _ = self.lint('Revert "feat(api): add the very long endpoint name that pushes this past the limit"')
        self.assertEqual((rc, found), (0, set()))

    def test_prose_about_breaking_changes_is_not_a_footer(self):
        self.cc_history()
        rc, found, _ = self.lint("feat: add v2 client\n\nBreaking changes in the old client are listed in the docs.")
        self.assertEqual((rc, found), (0, set()))

    def test_forced_conventional_does_not_argue_with_house_style(self):
        for i in range(20):
            self.repo.commit("improve the cache %d" % i)
        rc, found, _ = self.lint("feat: add cache warmup", "--conventional")
        self.assertEqual((rc, found), (0, set()))

    def test_comments_dropped_from_files_only(self):
        rc, found, _ = self.lint("#42 fix crash on start")  # like `git commit -m`: kept
        self.assertEqual((rc, found), (0, set()))
        path = os.path.join(self.tmp.name, "COMMIT_EDITMSG")
        with open(path, "w") as fh:
            fh.write("# Please enter the message\nAdd cache\n\n%s\n%s\n" % (cf.SCISSORS, "z" * 120))
        r = self.repo.run("--json", "--lint-file", path)
        self.assertEqual((r.returncode, json.loads(r.stdout)["findings"]), (0, []))

    def test_lint_file_and_exit_codes(self):
        path = os.path.join(self.tmp.name, "MSG")
        with open(path, "w") as fh:
            fh.write("")
        r = self.repo.run("--lint-file", path)
        self.assertEqual(r.returncode, 1)
        self.assertIn("empty", r.stdout)
        self.assertEqual(self.repo.run("--lint-file", path + ".missing").returncode, 2)


if __name__ == "__main__":
    unittest.main()
