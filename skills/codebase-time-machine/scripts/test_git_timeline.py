#!/usr/bin/env python3
"""Tests for git_timeline.py (stdlib unittest, no network).

Run: python3 -m unittest discover -s skills/codebase-time-machine/scripts -p 'test_*.py'
Each test builds a throwaway git repo where made-up authors commit at fixed dates (renames, a big
refactor, reverts, hotfixes, a long silence), runs the CLI and checks the facts it prints.
Git runs with an empty global config so the user's settings never leak in.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "git_timeline.py")
BASE_ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", LC_ALL="C", TZ="UTC",
                PYTHONIOENCODING="utf-8")
for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
    BASE_ENV.pop(var, None)
PEOPLE = {"ada": ("Ada Quill", "ada@example.com"), "bram": ("Bram Ostrander", "bram@example.com"),
          "cleo": ("Cleo Marsh", "cleo@example.com"), "dmitri": ("Dmitri Vale", "dmitri@example.com"),
          "esme": ("Esme Rook", "esme@example.com"), "felix": ("Felix Tarn", "felix@example.com")}
LONG_URGENT = "URGENT: patch the lexer before the big demo for the investors tomorrow"


def lines(n, tag="x"):
    return "".join("%s line %d\n" % (tag, i) for i in range(n))


class Repo:
    def __init__(self, root):
        self.path = os.path.join(root, "repo")
        os.makedirs(self.path)
        self.git("init", "-q", "-b", "main")

    def env(self, who, date_):
        name, email = PEOPLE[who]
        stamp = date_ if "T" in date_ else date_ + "T12:00:00Z"
        return dict(BASE_ENV, GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email, GIT_COMMITTER_NAME=name,
                    GIT_COMMITTER_EMAIL=email, GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)

    def git(self, *args, env=None):
        r = subprocess.run(["git", *args], cwd=self.path, env=env or BASE_ENV, capture_output=True, text=True)
        if r.returncode:
            raise AssertionError("git %s: %s" % (" ".join(args), r.stderr))
        return r.stdout

    def write(self, rel, text, mode="w"):
        full = os.path.join(self.path, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, mode) as fh:
            fh.write(text)

    def commit(self, who, date_, msg, files=None, append=None, moves=None):
        """files: {path: text} written; append: {path: text} appended; moves: [(old, new)] via git mv."""
        env = self.env(who, date_)
        for rel, text in (files or {}).items():
            self.write(rel, text)
        for rel, text in (append or {}).items():
            self.write(rel, text, "a")
        for old, new in moves or []:
            os.makedirs(os.path.dirname(os.path.join(self.path, new)), exist_ok=True)
            self.git("mv", old, new, env=env)
        self.git("add", "-A", env=env)
        self.git("commit", "-q", "-m", msg, env=env)
        return self.head()

    def revert(self, who, date_, rev):
        self.git("revert", "--no-edit", rev, env=self.env(who, date_))
        return self.head()

    def head(self):
        return self.git("rev-parse", "--short", "HEAD").strip()


def run(*args, cwd=None):
    return subprocess.run([sys.executable, SCRIPT, *args], cwd=cwd, env=BASE_ENV, capture_output=True,
                          encoding="utf-8", timeout=120)


def cast_line(name, n, start, end):
    return "   %-22s %5d commits  %s → %s" % (name, n, start, end)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repo(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def out(self, *args):
        r = run(self.repo.path, *args)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")
        return r.stdout

    def section(self, out, header):
        """Indented lines under the line starting with `header`."""
        rows = out.splitlines()
        i = next(i for i, ln in enumerate(rows) if ln.startswith(header))
        sec = []
        for ln in rows[i + 1:]:
            if not ln.startswith("   "):
                break
            sec.append(ln)
        return sec


class Saga(Base):
    """Ten commits, 2019-2023: four authors, a 500-line refactor, a revert, a 2.4-year silence."""

    def setUp(self):
        super().setUp()
        r, h = self.repo, {}
        h["init"] = r.commit("ada", "2019-03-01", "Initial commit", {"README.md": lines(3, "r"), "src/core.py": lines(10, "c")})
        h["parser"] = r.commit("ada", "2019-05-01", "Add parser", {"src/parser.py": lines(40, "p")})
        r.commit("ada", "2019-07-01", "Tune core", append={"src/core.py": lines(2, "t1")})
        h["tune2"] = r.commit("bram", "2019-09-01", "Tune core again", append={"src/core.py": lines(3, "t2")})
        h["refactor"] = r.commit("bram", "2020-01-10", "Big refactor: split core into engine modules",
                                 {"src/engine/%s.py" % m: lines(100, m) for m in "abcde"})
        h["revert"] = r.revert("bram", "2020-01-12", h["tune2"])
        h["hotfix"] = r.commit("cleo", "2022-06-01", "hotfix: core crash on empty input", append={"src/core.py": "guard\n"})
        h["rename"] = r.commit("cleo", "2022-06-03", "Rename parser to lexer", moves=[("src/parser.py", "src/lexer.py")])
        h["urgent"] = r.commit("ada", "2023-02-01", LONG_URGENT, append={"src/lexer.py": "patched\n"})
        h["typo"] = r.commit("dmitri", "2023-04-01", "Fix typo", {"README.md": lines(2, "r") + "r line 2 fixed\n"})
        self.h = h
        self.output = self.out()

    def test_header_counts_commits_span_and_authors(self):
        self.assertEqual(self.output.splitlines()[0], "📜 10 commits · 2019-03-01 → 2023-04-01 · 4 authors")

    def test_first_and_latest_commit(self):
        self.assertIn('🌱 first: %s "Initial commit" by Ada Quill\n' % self.h["init"], self.output)
        self.assertIn('📍 latest: %s "Fix typo"\n' % self.h["typo"], self.output)

    def test_eras_are_commits_per_year_with_scaled_bars(self):
        self.assertEqual(self.section(self.output, "📈"), [
            "   2019 %s 4" % ("█" * 28), "   2020 %s 2" % ("█" * 14),
            "   2022 %s 2" % ("█" * 14), "   2023 %s 2" % ("█" * 14)])  # the silent 2021 has no row

    def test_cast_ranked_by_commits_with_active_span(self):
        self.assertEqual(self.section(self.output, "🎭"), [
            cast_line("Ada Quill", 4, "2019-03", "2023-02"), cast_line("Bram Ostrander", 3, "2019-09", "2020-01"),
            cast_line("Cleo Marsh", 2, "2022-06", "2022-06"), cast_line("Dmitri Vale", 1, "2023-04", "2023-04")])

    def test_no_email_addresses_printed(self):
        self.assertNotIn("@example.com", self.output)

    def test_biggest_commits_top_three_by_churn(self):
        self.assertEqual(self.section(self.output, "💥"), [
            '   %s     500 lines  "Big refactor: split core into engine modules"' % self.h["refactor"],
            '   %s      40 lines  "Add parser"' % self.h["parser"],
            '   %s      13 lines  "Initial commit"' % self.h["init"]])

    def test_cursed_files_most_changed_first(self):
        cursed = self.section(self.output, "👻")
        self.assertEqual(len(cursed), 5)
        self.assertEqual(cursed[0], "      5×  src/core.py")  # init, 2 tunes, revert, hotfix
        self.assertIn("      2×  README.md", cursed)

    def test_renamed_file_is_counted_under_its_new_path(self):
        cursed = self.section(self.output, "👻")
        self.assertIn("      2×  src/lexer.py", cursed)  # the rename itself + the urgent patch
        self.assertNotIn("=>", self.output)

    def test_longest_silence(self):
        days = (date(2022, 6, 1) - date(2020, 1, 12)).days
        self.assertIn("🌙 longest silence: %d days (2020-01-12 → 2022-06-01)\n" % days, self.output)

    def test_plot_twists_count_and_latest_example(self):
        # git revert's 'Revert "Tune core again"', the hotfix and the URGENT patch; "Fix typo" is not a twist
        self.assertIn('🌪  plot twists (revert/hotfix): 3, e.g. %s "%s"\n' % (self.h["urgent"], LONG_URGENT[:45]),
                      self.output)

    def test_max_keeps_the_newest_commits(self):
        out = self.out("--max", "3")
        self.assertTrue(out.startswith("📜 3 commits · 2022-06-03 → 2023-04-01 · 3 authors\n"), out)
        self.assertIn('🌱 first: %s "Rename parser to lexer"' % self.h["rename"], out)

    def test_max_may_come_before_the_path(self):
        r = run("--max", "2", self.repo.path)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("📜 2 commits · 2023-02-01 → 2023-04-01"), r.stdout)

    def test_defaults_to_current_directory(self):
        r = run(cwd=self.repo.path)
        self.assertEqual((r.returncode, r.stdout), (0, self.output))


class EdgeCases(Base):
    def test_empty_repo_exits_cleanly(self):
        r = run(self.repo.path)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        self.assertEqual(r.stderr.strip(), "No commits found.")

    def test_not_a_repo_exits_cleanly(self):
        plain = os.path.join(self.tmp.name, "plain")
        os.makedirs(plain)
        r = run(plain)
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("Traceback", r.stderr)
        self.assertNotIn("No commits found", r.stderr)
        self.assertTrue(r.stderr.strip())

    def test_single_commit_repo(self):
        h = self.repo.commit("esme", "2024-02-29", "Initial commit", {"main.go": lines(7)})
        self.assertEqual(self.out(), "\n".join([
            "📜 1 commits · 2024-02-29 → 2024-02-29 · 1 authors",
            '🌱 first: %s "Initial commit" by Esme Rook' % h,
            '📍 latest: %s "Initial commit"' % h,
            "📈 commits per year:", "   2024 %s 1" % ("█" * 28),
            "🎭 cast (top authors):", cast_line("Esme Rook", 1, "2024-02", "2024-02"),
            "💥 biggest commits:", '   %s       7 lines  "Initial commit"' % h,
            "👻 cursed files (most changed):", "      1×  main.go",
            "🌪  plot twists (revert/hotfix): 0", ""]))  # no silence line: there is no gap

    def test_same_second_commits_keep_history_order(self):
        first = self.repo.commit("ada", "2024-05-05T09:00:00Z", "First", {"a.txt": "1\n"})
        second = self.repo.commit("ada", "2024-05-05T09:00:00Z", "Second", {"a.txt": "2\n"})
        out = self.out()
        self.assertIn('🌱 first: %s "First"' % first, out)
        self.assertIn('📍 latest: %s "Second"' % second, out)

    def test_merge_commits_are_ignored(self):
        r = self.repo
        r.commit("ada", "2024-01-01", "base", {"a.txt": "a\n"})
        r.git("checkout", "-q", "-b", "feature")
        r.commit("bram", "2024-01-02", "feature work", {"b.txt": "b\n"})
        r.git("checkout", "-q", "main")
        r.commit("ada", "2024-01-03", "main work", {"c.txt": "c\n"})
        r.git("merge", "-q", "--no-ff", "-m", "Merge branch 'feature'", "feature", env=r.env("ada", "2024-01-04"))
        out = self.out()
        self.assertTrue(out.startswith("📜 3 commits · 2024-01-01 → 2024-01-03 · 2 authors"), out)
        self.assertNotIn("Merge branch", out)

    def test_cast_shows_top_five_authors_only(self):
        for rank, who in enumerate(["ada", "bram", "cleo", "dmitri", "esme", "felix"]):
            for i in range(6 - rank):
                self.repo.commit(who, "2024-%02d-%02d" % (rank + 1, i + 1), "work", {"f.txt": "%s %d\n" % (who, i)})
        cast = self.section(self.out(), "🎭")
        self.assertEqual([ln.split()[0] for ln in cast], ["Ada", "Bram", "Cleo", "Dmitri", "Esme"])
        self.assertIn(cast_line("Esme Rook", 2, "2024-05", "2024-05"), cast)

    def test_twist_keywords_are_case_insensitive(self):
        r = self.repo
        r.commit("ada", "2024-01-01", "Revise the docs", {"a.txt": "1\n"})  # "revise" is not "revert"
        r.commit("ada", "2024-01-02", "ROLLBACK release 2.0", {"a.txt": "2\n"})
        h = r.commit("bram", "2024-01-03", "HotFix login loop", {"a.txt": "3\n"})
        r.commit("bram", "2024-01-04", "Add tests", {"b.txt": "1\n"})
        self.assertIn('🌪  plot twists (revert/hotfix): 2, e.g. %s "HotFix login loop"\n' % h, self.out())

    def test_binary_files_count_as_changed_with_zero_churn(self):
        self.repo.write("logo.png", "\x00\x01\x02PNG\x00" * 50, "w")
        h = self.repo.commit("cleo", "2024-03-03", "Add logo", {"notes.txt": "hi\n"})
        out = self.out()
        self.assertIn('   %s       1 lines  "Add logo"' % h, out)
        self.assertIn("      1×  logo.png", out)

    def test_rename_shapes_resolve_to_new_paths(self):
        r = self.repo
        olds = ["a.txt", "src/old.py", "lib/util.py", "src/deep/x.py", "guide.md"]
        news = ["b.txt", "src/new.py", "pkg/util.py", "src/x.py", "docs/guide.md"]
        r.commit("ada", "2024-01-01", "seed", {p: lines(20, p) for p in olds})
        r.commit("ada", "2024-01-02", "move things", moves=list(zip(olds, news)))
        r.commit("bram", "2024-01-03", "touch moved files", append={p: "more\n" for p in news})
        cursed = self.section(self.out(), "👻")
        self.assertEqual(sorted(cursed), sorted("      2×  " + p for p in news))


if __name__ == "__main__":
    unittest.main()
