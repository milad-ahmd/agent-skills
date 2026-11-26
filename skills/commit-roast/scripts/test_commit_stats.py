#!/usr/bin/env python3
"""Tests for commit_stats.py (stdlib unittest, no network).

Run: python3 -m unittest discover -s skills/commit-roast/scripts -p 'test_*.py'
Each test builds a throwaway git repo with crafted messages, dates and timezone offsets. Git runs with
an empty global config so the user's settings never leak in.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import commit_stats as cs  # noqa: E402

ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", LC_ALL="C",
           GIT_AUTHOR_EMAIL="ada@example.com", GIT_COMMITTER_EMAIL="ada@example.com")
for k in ("GIT_DIR", "GIT_WORK_TREE"):
    ENV.pop(k, None)
cs.GIT_ENV.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")


class Repo:
    def __init__(self, path):
        self.path = path
        os.makedirs(path)
        self.git("init", "-q", "-b", "main")
        self.n = 0

    def git(self, *args, env=None):
        r = subprocess.run(["git", *args], cwd=self.path, env=env or ENV, capture_output=True, text=True)
        if r.returncode:
            raise AssertionError("git %s: %s" % (" ".join(args), r.stderr))
        return r.stdout.strip()

    def commit(self, msg, when=None, files=None, author="Ada Tester"):
        """when: ISO 8601 with offset. Default: one hour apart per commit, UTC midday-ish."""
        self.n += 1
        when = when or "2026-03-02T%02d:%02d:00+00:00" % (8 + self.n // 60, self.n % 60)
        for name, text in (files or {"f%d.txt" % self.n: "x\n"}).items():
            full = os.path.join(self.path, name)
            os.makedirs(os.path.dirname(full) or self.path, exist_ok=True)
            with open(full, "w") as fh:
                fh.write(text)
        env = dict(ENV, GIT_AUTHOR_NAME=author, GIT_COMMITTER_NAME=author,
                   GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
        self.git("add", "-A", env=env)
        self.git("commit", "-q", "--allow-empty", "-m", msg, env=env)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = Repo(os.path.join(self.tmp, "proj"))

    def run_main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cs.main(["-C", self.repo.path, *args])
        return code, out.getvalue(), err.getvalue()

    def stats(self, *args):
        code, out, err = self.run_main("--json", *args)
        self.assertEqual(code, 0, err)
        return json.loads(out)


class TestParsing(Base):
    def test_conventional_types_and_scope(self):
        for m in ("feat(api): add login", "fix: handle null user", "feat!: drop node 16",
                  "Update readme", "feat:missing space", "chore(deps)!: bump x"):
            self.repo.commit(m)
        c = self.stats()["conventional"]
        self.assertEqual(c["count"], 4)
        self.assertEqual(c["with_scope"], 2)
        self.assertEqual(c["types"], {"feat": 2, "fix": 1, "chore": 1})

    def test_imperative_detection(self):
        for m in ("Add retry to client", "fix: handle timeout", "Added logging", "fixes the parser",
                  "Updating deps", "README tweaks"):
            self.repo.commit(m)
        im = self.stats()["imperative"]
        self.assertEqual((im["imperative"], im["not_imperative"], im["unclassified"]), (2, 3, 1))
        self.assertEqual(im["pct"], 40.0)
        self.assertEqual(set(im["top_not_imperative"]), {"added", "fixes", "updating"})

    def test_mood_function(self):
        self.assertEqual(cs.mood("refactor(core): Stopped caching")[0], "not")
        self.assertEqual(cs.mood("Use the new API")[0], "imperative")
        self.assertEqual(cs.mood("Made it faster")[0], "not")
        self.assertEqual(cs.mood("Typescript migration")[0], None)

    def test_one_word_lazy_and_hall_of_shame(self):
        for m in ("wip", "wip", "WIP", "fix", "fix.", ".", "Add search endpoint for orders"):
            self.repo.commit(m)
        s = self.stats()
        self.assertEqual(s["one_word"]["count"], 6)
        self.assertEqual(s["lazy"]["count"], 6)
        self.assertEqual(s["hall_of_shame"][0], {"message": "wip", "count": 3})
        self.assertIn({"message": "fix", "count": 2}, s["hall_of_shame"])

    def test_subject_lengths_and_over_72(self):
        self.repo.commit("x" * 80)
        self.repo.commit("Add a short one")
        s = self.stats()
        self.assertEqual(s["subject_over_72"]["count"], 1)
        self.assertEqual(s["subject_over_72"]["longest"]["length"], 80)
        self.assertTrue(s["subject_over_72"]["longest"]["message"].endswith("..."))
        self.assertEqual(s["subject_length"]["max"], 80)
        self.assertEqual(s["subject_length"]["buckets"]["73+"], 1)

    def test_merges_counted_separately(self):
        self.repo.commit("Add base")
        self.repo.git("checkout", "-q", "-b", "side")
        self.repo.commit("Add side feature")
        self.repo.git("checkout", "-q", "main")
        self.repo.commit("Add main feature")
        env = dict(ENV, GIT_AUTHOR_NAME="A", GIT_COMMITTER_NAME="A")
        self.repo.git("merge", "-q", "--no-ff", "-m", "Merge branch 'side'", "side", env=env)
        s = self.stats()
        self.assertEqual((s["commits"], s["merges"]), (3, 1))
        self.assertNotIn("merge", s["conventional"]["types"])


class TestHabits(Base):
    def test_swearing_is_censored_everywhere(self):
        self.repo.commit("fix this shit")
        self.repo.commit("fix this shit")
        self.repo.commit("Add tests", files={"a.txt": "a\n"})
        code, out, _ = self.run_main()
        self.assertEqual(code, 0)
        self.assertNotIn("shit", out)
        self.assertIn("s***", out)
        s = self.stats()
        self.assertEqual(s["swearing"]["count"], 2)
        self.assertNotIn("shit", json.dumps(s))
        self.assertEqual(cs.censor("WTF is this damned thing"), "W** is this d***** thing")

    def test_all_caps_and_emoji(self):
        self.repo.commit("FIX EVERYTHING NOW")
        self.repo.commit("Add CI")  # short caps-free, not counted
        self.repo.commit("Add dark mode \U0001F525")
        self.repo.commit(":sparkles: Add search")
        s = self.stats()
        self.assertEqual(s["all_caps"]["count"], 1)
        self.assertEqual(s["emoji"]["count"], 2)

    def test_big_commit_without_body(self):
        self.repo.commit("Add generated client", files={"big.txt": "l\n" * 250})
        self.repo.commit("Add schema\n\nThe schema mirrors the v2 API.", files={"s.txt": "l\n" * 300})
        self.repo.commit("Add stuff\n\nSigned-off-by: Ada <ada@example.com>", files={"t.txt": "l\n" * 210})
        bb = self.stats()["big_without_body"]
        self.assertEqual((bb["big_commits"], bb["without_body"]), (3, 2))  # a trailer is not a body
        self.assertEqual(bb["examples"][0]["lines"], 250)

    def test_fix_the_fix_chain(self):
        f = {"app.py": "1\n"}
        self.repo.commit("Add app", "2026-03-02T10:00:00+00:00", f)
        self.repo.commit("fix app", "2026-03-02T10:10:00+00:00", {"app.py": "2\n"})
        self.repo.commit("fix again", "2026-03-02T10:20:00+00:00", {"app.py": "3\n"})
        self.repo.commit("fix: really fixed", "2026-03-02T10:50:00+00:00", {"app.py": "4\n"})
        self.repo.commit("fix other file", "2026-03-02T11:00:00+00:00", {"other.py": "1\n"})  # no shared file
        self.repo.commit("fix app later", "2026-03-02T15:00:00+00:00", {"app.py": "5\n"})  # > 1 hour
        fc = self.stats()["fix_chains"]
        self.assertEqual((fc["chains"], fc["commits"]), (1, 3))
        self.assertEqual([e["message"] for e in fc["longest"]], ["fix app", "fix again", "fix: really fixed"])

    def test_late_night_and_friday_use_commit_timezone(self):
        self.repo.commit("Add a", "2026-03-04T02:30:00+03:30")   # 23:00 UTC, but 02:30 local
        self.repo.commit("Add b", "2026-03-06T18:00:00-05:00")   # Friday evening local, Saturday UTC
        self.repo.commit("Add c", "2026-03-04T12:00:00+00:00")
        s = self.stats()
        self.assertEqual(s["late_night"]["count"], 1)
        self.assertEqual(s["friday_evening"]["count"], 1)

    def test_reverts_typos_issue_refs_and_giants(self):
        self.repo.commit("Add parser", files={"p.py": "l\n" * 50})
        self.repo.commit('Revert "Add parser"', files={"p.py": "x\n"})
        self.repo.commit("revert: undo flag", files={"q.py": "x\n"})
        self.repo.commit("Fix typo", files={"r.md": "a\n"})
        self.repo.commit("fix typos in docs", files={"r.md": "b\n"})
        self.repo.commit("Add endpoint (#42)")
        self.repo.commit("Add billing\n\nRefs PAY-123")
        s = self.stats()
        self.assertEqual(s["reverts"]["count"], 2)
        self.assertEqual(s["typo_commits"]["count"], 2)
        self.assertEqual(s["issue_refs"]["count"], 2)
        self.assertEqual(s["giants"][0]["message"], "Revert \"Add parser\"")
        self.assertEqual(s["giants"][0]["lines"], 51)
        self.assertEqual(len(s["giants"]), 3)


class TestScoreAndOutput(Base):
    def test_clean_repo_scores_higher_than_messy(self):
        for m in ("feat(api): add order search", "fix(db): close idle connections",
                  "docs: explain retry settings", "refactor: extract payment client"):
            self.repo.commit(m)
        clean = self.stats()["score"]
        messy = Repo(os.path.join(self.tmp, "messy"))
        for i, m in enumerate(("wip", "asdf", "FIXED EVERYTHING", "fixed stuff", "fix", "x" * 90)):
            messy.commit(m, "2026-03-02T10:%02d:00+00:00" % (i * 5), {"same.py": "%d\n" % i})
        self.repo = messy
        bad = self.stats()["score"]
        self.assertGreater(clean, 90)
        self.assertLess(bad, 60)
        self.assertGreater(clean - bad, 30)

    def test_score_parts_sum_and_formula(self):
        self.repo.commit("wip")
        self.repo.commit("Add login form")
        s = self.stats()
        self.assertEqual(sum(p["weight"] for p in s["score_parts"].values()), 100)
        self.assertAlmostEqual(sum(p["points"] for p in s["score_parts"].values()), s["score"], delta=1)
        self.assertIn("30 x descriptive subjects", s["score_formula"])

    def test_anonymous_by_default(self):
        self.repo.commit("wip", author="Grace Hopper")
        self.repo.commit("wip", author="Grace Hopper")
        code, out, _ = self.run_main()
        self.assertEqual(code, 0)
        self.assertNotIn("Grace", out)
        self.assertNotIn("Grace", json.dumps(self.stats()))
        self.assertNotIn("ada@example.com", out)
        named = self.stats("--show-authors")
        self.assertEqual(named["by_author"][0], {"author": "Grace Hopper", "commits": 2, "lazy": 2})
        self.assertEqual(named["roastable"][0]["evidence"][0]["author"], "Grace Hopper")

    def test_roastable_facts_have_evidence(self):
        for i in range(4):
            self.repo.commit("wip")
        self.repo.commit("FIX IT")
        self.repo.commit("Add the feature")
        r = self.stats()["roastable"]
        self.assertTrue(1 <= len(r) <= 5)
        self.assertIn('"wip" was the whole message 4 times', r[0]["fact"])
        self.assertEqual(len(r[0]["evidence"]), 3)
        self.assertEqual(len(r[0]["evidence"][0]["sha"]), 8)

    def test_filters_author_since_max_count(self):
        self.repo.commit("Add a", "2025-01-01T10:00:00+00:00", author="Old Dev")
        self.repo.commit("Add b", "2026-03-01T10:00:00+00:00", author="New Dev")
        self.repo.commit("Add c", "2026-03-02T10:00:00+00:00", author="New Dev")
        self.assertEqual(self.stats("--author", "Old Dev")["commits"], 1)
        self.assertEqual(self.stats("--since", "2026-01-01")["commits"], 2)
        self.assertEqual(self.stats("--max-count", "1")["commits"], 1)
        self.assertEqual(self.stats("HEAD~1..HEAD")["commits"], 1)

    def test_text_report_sections(self):
        self.repo.commit("wip")
        self.repo.commit("wip")
        code, out, _ = self.run_main()
        self.assertEqual(code, 0)
        for section in ("HYGIENE SCORE", "HALL OF SHAME", "GIANT COMMITS", "MOST ROASTABLE"):
            self.assertIn(section, out)


class TestExitCodes(Base):
    def test_not_a_repo_exit_2(self):
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cs.main(["-C", plain])
        self.assertEqual(code, 2)
        self.assertIn("Not a git repository", err.getvalue())

    def test_empty_repo_and_no_match_exit_2(self):
        self.assertEqual(self.run_main()[0], 2)
        self.repo.commit("Add a")
        code, _, err = self.run_main("--author", "nobody-matches-this")
        self.assertEqual(code, 2)
        self.assertIn("No non-merge commits", err)


if __name__ == "__main__":
    unittest.main()
