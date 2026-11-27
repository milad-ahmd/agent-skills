#!/usr/bin/env python3
"""Tests for wrapped.py (stdlib unittest, no network).

Run: python3 -m unittest discover -s skills/dev-wrapped/scripts -p 'test_*.py'
Each test builds throwaway git repos with commits at fixed dates, offsets and authors. Git runs with
an empty global config so the user's settings never leak in. The GitHub API is mocked.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import xml.etree.ElementTree as ET
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wrapped  # noqa: E402

wrapped.GIT_ENV.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")  # keep user config out

ME = "ada@example.com"
ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", LC_ALL="C",
           GIT_AUTHOR_NAME="Ada Tester", GIT_COMMITTER_NAME="Ada Tester")
for k in ("GIT_DIR", "GIT_WORK_TREE"):
    ENV.pop(k, None)


class Repo:
    def __init__(self, path, email=None):
        self.path = path
        os.makedirs(path)
        self.git("init", "-q", "-b", "main")
        if email:
            self.git("config", "user.email", email)
        self.n = 0

    def git(self, *args, env=None):
        r = subprocess.run(["git", *args], cwd=self.path, env=env or ENV, capture_output=True, text=True)
        if r.returncode:
            raise AssertionError("git %s: %s" % (" ".join(args), r.stderr))
        return r.stdout.strip()

    def commit(self, when, msg="add thing", email=ME, files=None):
        """when: ISO 8601 with offset, e.g. 2026-03-01T23:30:00+03:30"""
        self.n += 1
        for name, text in (files or {"f%d.py" % self.n: "x = 1\n"}).items():
            full = os.path.join(self.path, name)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            mode = "wb" if isinstance(text, bytes) else "w"
            with open(full, mode) as fh:
                fh.write(text)
        env = dict(ENV, GIT_AUTHOR_EMAIL=email, GIT_COMMITTER_EMAIL=email,
                   GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
        self.git("add", "-A", env=env)
        self.git("commit", "-q", "-m", msg, env=env)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def repo(self, name="proj", email=None):
        return Repo(os.path.join(self.tmp, name), email)

    def run_main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, ENV, clear=True), redirect_stdout(out), redirect_stderr(err):
            code = wrapped.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def stats(self, *args):
        code, out, err = self.run_main("--repos", self.tmp, "--json", *args)
        self.assertEqual(code, 0, err)
        return json.loads(out)


class TestCommits(Base):
    def test_year_filter(self):
        r = self.repo()
        r.commit("2025-06-01T10:00:00+00:00")
        r.commit("2026-06-01T10:00:00+00:00")
        r.commit("2026-06-02T10:00:00+00:00")
        self.assertEqual(self.stats("--email", ME, "--year", "2026")["commits"], 2)
        self.assertEqual(self.stats("--email", ME, "--year", "2025")["commits"], 1)

    def test_year_boundary_uses_commit_local_time(self):
        r = self.repo()
        r.commit("2026-01-01T00:30:00+02:00")  # 2025-12-31 22:30 UTC, but New Year's in its own zone
        s = self.stats("--email", ME, "--year", "2026")
        self.assertEqual(s["commits"], 1)
        self.assertEqual(s["first_commit"]["when"][:10], "2026-01-01")

    def test_author_filter_and_multiple_emails(self):
        r = self.repo()
        r.commit("2026-02-01T10:00:00+00:00", email=ME)
        r.commit("2026-02-02T10:00:00+00:00", email="ADA@work.example")
        r.commit("2026-02-03T10:00:00+00:00", email="someone@else.example")
        self.assertEqual(self.stats("--email", ME, "--year", "2026")["commits"], 1)
        both = self.stats("--email", ME, "--email", "ada@work.example", "--year", "2026")
        self.assertEqual(both["commits"], 2)

    def test_default_email_from_repo_config(self):
        r = self.repo(email=ME)
        r.commit("2026-02-01T10:00:00+00:00")
        r.commit("2026-02-02T10:00:00+00:00", email="other@example.com")
        self.assertEqual(self.stats("--year", "2026")["commits"], 1)

    def test_no_commits_exit_2(self):
        r = self.repo()
        r.commit("2024-01-01T10:00:00+00:00")
        code, _, err = self.run_main("--repos", self.tmp, "--email", ME, "--year", "2026")
        self.assertEqual(code, 2)
        self.assertIn("No commits", err)
        code, _, _ = self.run_main("--repos", os.path.join(self.tmp, "nothing-here"), "--email", ME)
        self.assertEqual(code, 2)

    def test_merges_skipped_and_clones_deduplicated(self):
        r = self.repo()
        r.commit("2026-03-01T10:00:00+00:00")
        r.commit("2026-03-02T10:00:00+00:00")
        subprocess.run(["git", "clone", "-q", r.path, os.path.join(self.tmp, "copy")], env=ENV, check=True)
        self.assertEqual(self.stats("--email", ME, "--year", "2026")["commits"], 2)


class TestStats(Base):
    def test_streak_across_month_boundary(self):
        r = self.repo()
        for d in ("2026-01-30", "2026-01-31", "2026-02-01", "2026-02-02", "2026-02-10", "2026-02-11"):
            r.commit(d + "T12:00:00+00:00")
        s = self.stats("--email", ME, "--year", "2026")
        self.assertEqual(s["longest_streak"], {"days": 4, "start": "2026-01-30", "end": "2026-02-02"})
        self.assertEqual(s["active_days"], 6)

    def test_streak_function_edges(self):
        import datetime as dt
        d = dt.date
        self.assertEqual(wrapped.longest_streak([]), (0, None, None))
        self.assertEqual(wrapped.longest_streak([d(2026, 2, 28), d(2026, 3, 1)])[0], 2)
        self.assertEqual(wrapped.longest_streak([d(2028, 2, 28), d(2028, 3, 1)])[0], 1)  # leap year gap

    def test_hour_histogram_in_commit_offset(self):
        r = self.repo()
        r.commit("2026-04-01T23:30:00+03:30")  # 20:00 UTC; should count as 23
        r.commit("2026-04-02T07:15:00-08:00")
        s = self.stats("--email", ME, "--year", "2026")
        self.assertEqual(s["hours"][23], 1)
        self.assertEqual(s["hours"][7], 1)
        self.assertEqual(s["hours"][20], 0)
        self.assertEqual(s["night_owl_pct"], 50.0)
        self.assertEqual(s["early_bird_pct"], 50.0)

    def test_weekdays_and_busiest(self):
        r = self.repo()
        r.commit("2026-05-02T10:00:00+00:00")  # Saturday
        r.commit("2026-05-02T11:00:00+00:00")
        r.commit("2026-05-04T10:00:00+00:00")  # Monday
        r.commit("2026-07-06T10:00:00+00:00")
        s = self.stats("--email", ME, "--year", "2026")
        self.assertEqual(s["weekdays"], [2, 0, 0, 0, 0, 2, 0])
        self.assertEqual(s["weekend_pct"], 50.0)
        self.assertEqual(s["busiest_day"], {"date": "2026-05-02", "commits": 2})
        self.assertEqual(s["busiest_month"], {"month": "May", "commits": 3})

    def test_lockfiles_vendor_and_binaries_excluded(self):
        r = self.repo()
        r.commit("2026-06-01T10:00:00+00:00", files={
            "app.py": "a\nb\nc\n", "package-lock.json": "x\n" * 500, "vendor/lib/x.go": "y\n" * 300,
            "dist/app.min.js": "z\n" * 200, "logo.png": b"\x89PNG\x00\x01\x02"})
        s = self.stats("--email", ME, "--year", "2026")
        self.assertEqual(s["lines_added"], 3)
        self.assertEqual([l["language"] for l in s["languages"]], ["Python"])
        self.assertEqual(s["most_edited_file"]["path"], "app.py")

    def test_exclude_glob_and_data_files_not_languages(self):
        r = self.repo()
        r.commit("2026-06-01T10:00:00+00:00", files={"app.go": "a\n", "assets/levels/1.csv": "c\n" * 50,
                                                      "config.json": "{}\n" * 5})
        s = self.stats("--email", ME, "--year", "2026", "--exclude", "assets/*")
        self.assertEqual(s["lines_added"], 6)
        self.assertEqual([l["language"] for l in s["languages"]], ["Go"])

    def test_languages_biggest_commit_and_most_edited(self):
        r = self.repo()
        r.commit("2026-06-01T10:00:00+00:00", "feat: server", files={"main.go": "l\n" * 30})
        r.commit("2026-06-02T10:00:00+00:00", "fix: typo", files={"main.go": "l\n" * 31, "ui.ts": "t\n" * 10})
        r.commit("2026-06-03T10:00:00+00:00", "Fixed again", files={"main.go": "m\n" * 31})
        s = self.stats("--email", ME, "--year", "2026")
        self.assertEqual(s["languages"][0]["language"], "Go")
        self.assertEqual(s["biggest_commit"]["subject"], "Fixed again")
        self.assertEqual(s["biggest_commit"]["lines"], 62)
        self.assertEqual(s["most_edited_file"], {"repo": "proj", "path": "main.go", "commits": 3})
        self.assertEqual(s["verbs"][0], {"verb": "fix", "commits": 2})
        self.assertEqual(s["first_commit"]["subject"], "feat: server")
        self.assertEqual(s["last_commit"]["subject"], "Fixed again")

    def test_verb_normalisation(self):
        self.assertEqual(wrapped.verb("feat(api)!: add login"), "add")
        self.assertEqual(wrapped.verb("Fixed the thing"), "fix")
        self.assertEqual(wrapped.verb("refactor: split module"), "refactor")
        self.assertEqual(wrapped.verb("[WIP] Updated docs"), "wip")
        self.assertIsNone(wrapped.verb("123"))
        self.assertIsNone(wrapped.verb("p1: tweak"))


def fake_stats(**over):
    s = {"commits": 100, "months": [9] * 11 + [1], "languages": [{"language": "Go", "pct": 100.0}],
         "night_owl_pct": 5.0, "early_bird_pct": 5.0, "weekend_pct": 10.0, "longest_streak": {"days": 3},
         "lines_added": 1000, "lines_removed": 400, "busiest_month": {"month": "Jan"}}
    s.update(over)
    return s


class TestArchetype(unittest.TestCase):
    def name(self, verbs=None, **over):
        return wrapped.archetype(fake_stats(**over), Counter(verbs or {}))["name"]

    def test_rules(self):
        self.assertEqual(self.name(commits=5), "The Fresh Start")
        self.assertEqual(self.name(night_owl_pct=40.0, weekend_pct=50.0), "The Night Owl")
        self.assertEqual(self.name(early_bird_pct=30.0), "The Early Bird")
        self.assertEqual(self.name(weekend_pct=40.0), "The Weekend Warrior")
        self.assertEqual(self.name(longest_streak={"days": 21}), "The Streak Machine")
        self.assertEqual(self.name({"refactor": 20}), "The Refactorer")
        self.assertEqual(self.name(lines_removed=5000), "The Gardener")
        self.assertEqual(self.name({"fix": 40}), "The Bug Squasher")
        langs = [{"language": x, "pct": 25.0} for x in "ABCD"]
        self.assertEqual(self.name(languages=langs), "The Polyglot")
        self.assertEqual(self.name(months=[50] + [5] * 10 + [0]), "The Sprinter")
        self.assertEqual(self.name(), "The Steady Builder")

    def test_rule_text_explains_choice(self):
        a = wrapped.archetype(fake_stats(night_owl_pct=42.0), Counter())
        self.assertIn("42%", a["rule"])


class TestOutputs(Base):
    def make(self):
        r = self.repo("secret-client-repo")
        r.commit("2026-08-01T23:10:00+02:00", "Fix billing leak in invoices", files={"billing/invoice.py": "a\n" * 9})
        r.commit("2026-08-02T10:00:00+02:00", "add tests", files={"billing/test_invoice.py": "b\n" * 4})

    def card(self, *args):
        path = os.path.join(self.tmp, "card.svg")
        code, _, err = self.run_main("--repos", self.tmp, "--email", ME, "--year", "2026", "--svg", path, *args)
        self.assertEqual(code, 0, err)
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def test_svg_well_formed_private_by_default(self):
        self.make()
        for theme in ("dark", "light"):
            svg = self.card("--theme", theme)
            root = ET.fromstring(svg)
            self.assertEqual((root.get("width"), root.get("height")), ("1200", "630"))
            for secret in ("@", "secret-client-repo", "invoice", "billing"):
                self.assertNotIn(secret, svg)
        self.assertIn("#faf7f2", svg)
        self.assertIn("milad-ahmd/agent-skills", svg)
        self.assertNotIn("milad-ahmd/agent-skills", self.card("--no-credit"))

    def test_svg_show_repos_and_escaping(self):
        self.make()
        os.rename(os.path.join(self.tmp, "secret-client-repo"), os.path.join(self.tmp, "a&b<c>"))
        svg = self.card("--show-repos")
        ET.fromstring(svg)
        self.assertIn("a&amp;b&lt;c&gt;", svg)

    def test_markdown_private_by_default(self):
        self.make()
        code, out, _ = self.run_main("--repos", self.tmp, "--email", ME, "--year", "2026", "--md")
        self.assertEqual(code, 0)
        self.assertIn("## My 2026 in code", out)
        for secret in ("@", "secret-client-repo", "invoice", "billing"):
            self.assertNotIn(secret, out)
        code, out, _ = self.run_main("--repos", self.tmp, "--email", ME, "--year", "2026", "--md",
                                     "--show-repos", "--show-details")
        self.assertIn("secret-client-repo", out)
        self.assertIn("Fix billing leak", out)

    def test_text_report_and_find_repos_skips(self):
        self.make()
        Repo(os.path.join(self.tmp, "node_modules", "dep")).commit("2026-08-03T10:00:00+00:00")
        Repo(os.path.join(self.tmp, "a", "b", "c", "d", "deep")).commit("2026-08-03T10:00:00+00:00")
        found = [os.path.basename(p) for p in wrapped.find_repos([self.tmp], depth=4)]
        self.assertEqual(found, ["secret-client-repo"])
        code, out, _ = self.run_main("--repos", self.tmp, "--email", ME, "--year", "2026")
        self.assertEqual(code, 0)
        self.assertIn("commits        2 across 1 repos", out)
        self.assertNotIn(ME, out)


class TestGitHub(unittest.TestCase):
    REPOS = [
        {"full_name": "ada/a", "fork": False, "language": "Go", "stargazers_count": 3,
         "created_at": "2026-02-01T00:00:00Z", "pushed_at": "2026-10-01T00:00:00Z"},
        {"full_name": "ada/b", "fork": False, "language": "Python", "stargazers_count": 0,
         "created_at": "2023-02-01T00:00:00Z", "pushed_at": "2024-10-01T00:00:00Z"},
        {"full_name": "ada/fork", "fork": True, "language": "C", "stargazers_count": 99,
         "created_at": "2026-02-01T00:00:00Z", "pushed_at": "2026-10-01T00:00:00Z"},
    ]
    STARS = [{"starred_at": "2025-12-31T23:00:00Z"}, {"starred_at": "2026-01-05T00:00:00Z"},
             {"starred_at": "2026-09-09T00:00:00Z"}]

    def fake(self, url, accept="application/vnd.github+json"):
        if "/stargazers" in url:
            self.assertEqual(accept, "application/vnd.github.star+json")
            return self.STARS, "50"
        return self.REPOS, "55"

    def test_mocked_stats(self):
        with mock.patch.object(wrapped, "http_get", side_effect=self.fake) as m:
            g = wrapped.github_stats("ada", 2026)
        self.assertEqual(g["stars_this_year"], 2)
        self.assertEqual(g["repos_created"], 1)
        self.assertEqual(g["public_repos"], 2)
        self.assertEqual(g["languages"], ["Go"])
        self.assertFalse(g["partial"])
        self.assertEqual(m.call_count, 2)  # repos + one stargazer page; zero-star repo not fetched

    def test_stargazers_need_token_falls_back_to_all_time(self):
        def fake(url, accept=None):
            if "/stargazers" in url:
                raise urllib.error.HTTPError(url, 401, "Requires authentication", {}, None)
            return self.REPOS, "55"
        with mock.patch.object(wrapped, "http_get", side_effect=fake):
            g = wrapped.github_stats("ada", 2026)
        self.assertNotIn("stars_this_year", g)
        self.assertIn("token", g["stars_note"])
        self.assertEqual(wrapped.github_stars(g), "3 GitHub stars (all time)")

    def test_offline_and_rate_limit_degrade(self):
        with mock.patch.object(wrapped, "http_get", side_effect=urllib.error.URLError("no route")):
            g = wrapped.github_stats("ada", 2026)
        self.assertIn("offline", g["error"])
        err = urllib.error.HTTPError("u", 403, "forbidden", {}, None)
        with mock.patch.object(wrapped, "http_get", side_effect=err):
            self.assertEqual(wrapped.github_stats("ada", 2026)["error"], "rate limited")
        with mock.patch.object(wrapped, "http_get", side_effect=lambda u, accept=None: (self.REPOS, "1")):
            g = wrapped.github_stats("ada", 2026)
        self.assertTrue(g["partial"])
        self.assertEqual(g["stars_this_year"], 0)


class TestGitHubCli(Base):
    def test_cli_with_github_offline_still_exits_0(self):
        self.repo().commit("2026-03-01T10:00:00+00:00")
        with mock.patch.object(wrapped, "http_get", side_effect=urllib.error.URLError("down")):
            code, out, _ = self.run_main("--repos", self.tmp, "--email", ME, "--year", "2026", "--github", "ada")
        self.assertEqual(code, 0)
        self.assertIn("offline", out)


if __name__ == "__main__":
    unittest.main()
