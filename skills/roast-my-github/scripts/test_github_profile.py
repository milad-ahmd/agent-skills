#!/usr/bin/env python3
"""Tests for github_profile.py (stdlib unittest, no network).

Run: python3 -m unittest discover -s skills/roast-my-github/scripts -p 'test_*.py'
Stats come from fixtures/pixel-pigeon.json, saved API responses for a made-up user, fed in through
collect(user, raw) or the --from-json flag. HTTP is exercised by patching urllib's urlopen, and the
clock is frozen wherever a stat depends on today's date.
"""
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from datetime import datetime, timezone
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import github_profile as gp  # noqa: E402

SCRIPT = os.path.join(HERE, "github_profile.py")
FIXTURE = os.path.join(HERE, "fixtures", "pixel-pigeon.json")
# any accidental HTTP call from a subprocess fails fast instead of reaching GitHub
CLI_ENV = {k: v for k, v in os.environ.items() if k not in ("GITHUB_TOKEN", "GH_TOKEN")}
CLI_ENV.update(PYTHONIOENCODING="utf-8", HTTPS_PROXY="http://127.0.0.1:9", https_proxy="http://127.0.0.1:9")


class FrozenDT(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 6, 1, tzinfo=timezone.utc)


def load_fixture():
    with open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


def repo(name, stars=0, lang=None, pushed="2026-05-01T00:00:00Z", fork=False, **kw):
    r = dict(name=name, fork=fork, language=lang, pushed_at=pushed, archived=False, stargazers_count=stars,
             description="d", license={"key": "mit"}, topics=["t"])
    r.update(kw)
    return r


def raw_with(repos=(), events=(), **user):
    u = dict(login="nobody-here", name=None, bio=None, blog="", followers=0, following=0,
             created_at="2026-01-01T00:00:00Z", public_repos=len(repos))
    u.update(user)
    return {"user": u, "repos": list(repos), "events": list(events)}


class Frozen(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(gp, "datetime", FrozenDT)
        p.start()
        self.addCleanup(p.stop)

    def collect(self, raw):
        return gp.collect(raw["user"]["login"], raw)


class FixtureStats(Frozen):
    def setUp(self):
        super().setUp()
        self.d = self.collect(load_fixture())

    def test_profile_fields(self):
        d = self.d
        self.assertEqual((d["login"], d["name"], d["followers"], d["following"], d["public_repos"]),
                         ("pixel-pigeon", "Pixel Pigeon", 31, 4, 8))
        self.assertEqual(d["account_age_years"], 8.0)

    def test_forks_are_excluded_from_own_stats(self):
        self.assertEqual((self.d["own_repos"], self.d["forks"]), (7, 1))
        self.assertEqual(self.d["total_stars"], 65)  # the 900-star fork does not count
        self.assertNotIn("forked-lib", [r["name"] for r in self.d["top_repos"]])
        self.assertNotIn("C", dict(self.d["languages"]))

    def test_top_repos_sorted_by_stars_capped_at_five(self):
        self.assertEqual([(r["name"], r["stars"]) for r in self.d["top_repos"]],
                         [("coo-cli", 42), ("loft", 12), ("breadcrumbs", 7), ("feather-ui", 3), ("Pixel-Pigeon", 1)])
        self.assertIsNone(self.d["top_repos"][2]["desc"])

    def test_languages_counted_and_null_language_skipped(self):
        self.assertEqual(self.d["languages"],
                         [("Python", 2), ("Go", 1), ("JavaScript", 1), ("TypeScript", 1), ("Rust", 1)])

    def test_hygiene_counts(self):
        d = self.d
        self.assertEqual((d["no_description"], d["no_license"], d["no_topics"]), (2, 3, 4))
        self.assertEqual((d["zero_star_repos"], d["archived"]), (2, 1))

    def test_stale_ignores_archived_and_recent(self):
        self.assertEqual(self.d["stale_over_1y"], 2)  # breadcrumbs + seed-scripts, not archived old-nest

    def test_profile_readme_match_is_case_insensitive(self):
        self.assertTrue(self.d["has_profile_readme_repo"])

    def test_commit_messages(self):
        d = self.d
        self.assertEqual((d["recent_commit_msgs"], d["lazy_commit_msgs"]), (8, 6))
        self.assertEqual(d["lazy_examples"], ["fix", "wip", "......", "Update.", ""])


class LazyRegex(unittest.TestCase):
    def lazy(self, m):
        return bool(gp.LAZY.match(m.strip()) or len(m.strip()) < 6)

    def test_lazy_messages(self):
        for m in ("update", "Update.", "FIX", "wip", "changes", "stuff", "asdfff", "......", "........",
                  "final final", "minor", "misc", "temp", "typo", ""):
            self.assertTrue(self.lazy(m), m)

    def test_real_messages(self):
        for m in ("Update parser for nested lists", "updates", "fixtures for the CLI", "Add retry with jitter",
                  "testing harness overhaul"):
            self.assertFalse(self.lazy(m), m)


class EdgeCases(Frozen):
    def test_zero_repos(self):
        d = self.collect(raw_with())
        self.assertEqual((d["own_repos"], d["forks"], d["total_stars"]), (0, 0, 0))
        self.assertEqual((d["top_repos"], d["languages"], d["stale_over_1y"]), ([], [], 0))
        self.assertFalse(d["has_profile_readme_repo"])

    def test_forks_only(self):
        d = self.collect(raw_with([repo("a", 50, "Go", fork=True), repo("nobody-here", 9, fork=True)]))
        self.assertEqual((d["own_repos"], d["forks"], d["total_stars"], d["top_repos"]), (0, 2, 0, []))
        self.assertFalse(d["has_profile_readme_repo"])  # a fork named like the user is not a profile README

    def test_language_list_capped_at_six(self):
        langs = ["Go", "Go", "Go", "Rust", "Rust", "C", "Java", "Ruby", "Lua", "Zig"]
        d = self.collect(raw_with([repo("r%d" % i, lang=l) for i, l in enumerate(langs)]))
        self.assertEqual(len(d["languages"]), 6)
        self.assertEqual(list(d["languages"][0]), ["Go", 3])
        self.assertEqual(list(d["languages"][1]), ["Rust", 2])

    def test_push_events_without_commits(self):
        # GitHub may omit payload.commits; that must read as "no commit data", not crash
        d = self.collect(raw_with(events=[{"type": "PushEvent", "payload": {}}]))
        self.assertEqual((d["recent_commit_msgs"], d["lazy_commit_msgs"], d["lazy_examples"]), (0, 0, []))

    def test_stale_boundary(self):
        d = self.collect(raw_with([repo("edge", pushed="2025-06-01T00:00:00Z"),
                                   repo("over", pushed="2025-05-31T00:00:00Z")]))
        self.assertEqual(d["stale_over_1y"], 1)  # 365 days is not stale, 366 is


class TextOutput(Frozen):
    def run_main(self, raw, *flags):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "raw.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(raw, fh)
            out = io.StringIO()
            with mock.patch.object(sys, "argv", ["github_profile.py", raw["user"]["login"], *flags, "--from-json", path]), \
                    mock.patch.object(gp, "get", side_effect=AssertionError("network used")), redirect_stdout(out):
                gp.main()
        return out.getvalue()

    def test_fixture_report(self):
        out = self.run_main(load_fixture())
        self.assertIn("pixel-pigeon (Pixel Pigeon): 8.0y on GitHub", out)
        self.assertIn("repos 7 own + 1 forks", out)
        self.assertIn("65 stars total", out)
        self.assertIn("no description 2/7 · no license 3/7 · no topics 4/7", out)
        self.assertIn("untouched >1 year 2/7 · zero-star 2/7 · archived 1", out)
        self.assertIn("top: coo-cli ★42, loft ★12", out)
        self.assertIn('lazy commit msgs 6/8 recent e.g. "fix", "wip", "......"', out)

    def test_empty_profile_report(self):
        out = self.run_main(raw_with())
        self.assertIn("nobody-here (no name)", out)
        self.assertIn("bio: — none — | site: — none — | profile README: no", out)
        self.assertIn("repos 0 own + 0 forks", out)
        self.assertNotIn("lazy commit msgs", out)

    def test_json_flag(self):
        d = json.loads(self.run_main(load_fixture(), "--json"))
        self.assertEqual((d["own_repos"], d["stale_over_1y"]), (7, 2))


class Fetch(unittest.TestCase):
    def fake_get(self, pages):
        calls = []

        def get(path):
            calls.append(path)
            if path == "/users/pp":
                return {"login": "pp"}
            if "/repos?" in path:
                page = int(path.split("&page=")[1].split("&")[0])
                return pages[page - 1] if page <= len(pages) else []
            return [{"type": "WatchEvent"}]
        return get, calls

    def test_pagination_stops_on_short_page(self):
        get, calls = self.fake_get([[repo("a%d" % i) for i in range(100)], [repo("b%d" % i) for i in range(30)]])
        with mock.patch.object(gp, "get", get):
            raw = gp.fetch("pp")
        self.assertEqual(len(raw["repos"]), 130)
        self.assertEqual(sum("/repos?" in c for c in calls), 2)
        self.assertEqual(raw["events"], [{"type": "WatchEvent"}])
        self.assertIn("/users/pp/events/public?per_page=100", calls)

    def test_exactly_one_full_page(self):
        get, calls = self.fake_get([[repo("a%d" % i) for i in range(100)]])
        with mock.patch.object(gp, "get", get):
            self.assertEqual(len(gp.fetch("pp")["repos"]), 100)
        self.assertEqual(sum("/repos?" in c for c in calls), 2)

    def test_page_cap(self):
        get, calls = self.fake_get([[repo("x")] * 100] * 12)
        with mock.patch.object(gp, "get", get):
            self.assertEqual(len(gp.fetch("pp")["repos"]), 1000)
        self.assertEqual(sum("/repos?" in c for c in calls), 10)


class HttpLayer(unittest.TestCase):
    def fail_with(self, exc):
        with mock.patch.object(gp.urllib.request, "urlopen", side_effect=exc), \
                self.assertRaises(SystemExit) as cm:
            gp.get("/users/pp")
        return str(cm.exception.code)

    def http_error(self, code):
        return urllib.error.HTTPError("https://api.github.com/users/pp", code, "err", {}, None)

    def test_not_found(self):
        self.assertIn("User not found", self.fail_with(self.http_error(404)))

    def test_rate_limit(self):
        for code in (403, 429):
            self.assertIn("rate limit", self.fail_with(self.http_error(code)))

    def test_other_http_error_exits_cleanly(self):
        self.assertIn("GitHub API error 500", self.fail_with(self.http_error(500)))

    def test_offline_exits_cleanly(self):
        self.assertIn("Could not reach GitHub", self.fail_with(urllib.error.URLError("no route")))

    def captured_request(self, env):
        seen = {}

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def urlopen(req, timeout=None):
            seen["req"] = req
            return Resp(b'{"login": "pp"}')
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(gp.urllib.request, "urlopen", urlopen):
            self.assertEqual(gp.get("/users/pp"), {"login": "pp"})
        return seen["req"]

    def test_token_sent_when_set(self):
        fake = "test-token"
        req = self.captured_request({"GH_TOKEN": fake})
        self.assertEqual(req.get_header("Authorization"), "Bearer " + fake)
        self.assertEqual(req.full_url, "https://api.github.com/users/pp")

    def test_no_token_anonymous(self):
        self.assertIsNone(self.captured_request({}).get_header("Authorization"))


class Cli(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, encoding="utf-8",
                              timeout=60, env=CLI_ENV)

    def test_from_json_offline(self):
        r = self.run_cli("pixel-pigeon", "--json", "--from-json", FIXTURE)
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(r.stdout)
        self.assertEqual((d["login"], d["own_repos"], d["total_stars"], d["lazy_commit_msgs"]), ("pixel-pigeon", 7, 65, 6))

    def test_help_mentions_seam(self):
        r = self.run_cli("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("--from-json FILE", r.stdout)

    def test_no_args(self):
        r = self.run_cli()
        self.assertEqual(r.returncode, 1)
        self.assertIn("Usage", r.stderr)

    def test_bad_from_json(self):
        with tempfile.TemporaryDirectory() as td:
            bad = os.path.join(td, "bad.json")
            with open(bad, "w") as fh:
                fh.write("{not json")
            shapes = []
            for i, text in enumerate(("{}", "[]", '{"user": {}}')):
                shapes.append(os.path.join(td, "shape%d.json" % i))
                with open(shapes[-1], "w") as fh:
                    fh.write(text)
            for args in (("pp", "--from-json", bad), ("pp", "--from-json", os.path.join(td, "missing.json")),
                         ("pp", "--from-json")) + tuple(("pp", "--from-json", s) for s in shapes):
                r = self.run_cli(*args)
                self.assertEqual(r.returncode, 1, args)
                self.assertNotIn("Traceback", r.stderr)


if __name__ == "__main__":
    unittest.main()
