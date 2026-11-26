#!/usr/bin/env python3
"""Tests for portfolio.py (stdlib unittest, no network).

Run: python3 -m unittest discover -s skills/github-portfolio/scripts -p 'test_*.py'
Builds sites from fixtures/raw.json and drives fetch() with a fake urlopen.
"""
import base64
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import portfolio as pf  # noqa: E402

SCRIPT = os.path.join(HERE, "portfolio.py")
FIXTURE = os.path.join(HERE, "fixtures", "raw.json")
NOW = datetime(2026, 11, 27, tzinfo=timezone.utc)


def load_raw():
    with open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


class Collector(HTMLParser):
    """Records tags, checks nesting, and collects script/style/link sources."""
    VOID = {"meta", "img", "br", "hr", "link", "input", "source"}

    def __init__(self):
        super().__init__()
        self.stack, self.errors, self.tags, self.external, self.imgs = [], [], [], [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.tags.append(tag)
        if tag == "script" or (tag == "link" and a.get("rel") == "stylesheet"):
            self.external.append(a.get("src") or a.get("href") or "inline-script")
        if tag == "img":
            self.imgs.append(a)
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.errors.append("unexpected </%s> with open %s" % (tag, self.stack[-3:]))
        else:
            self.stack.pop()


def parse(page):
    c = Collector()
    c.feed(page)
    c.close()
    return c


class FakeResp:
    def __init__(self, body, remaining=50):
        self.body = json.dumps(body).encode()
        self.headers = {"x-ratelimit-remaining": str(remaining)}

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    """Serves canned responses by URL path; records every call."""

    def __init__(self, routes, limit_after=None):
        self.routes, self.calls, self.limit_after = routes, [], limit_after

    def __call__(self, req, timeout=None):
        path = req.full_url[len(pf.API):]
        self.calls.append(path)
        if self.limit_after is not None and len(self.calls) > self.limit_after:
            raise urllib.error.HTTPError(req.full_url, 403, "rate limit", {"x-ratelimit-remaining": "0",
                                                                           "x-ratelimit-reset": "1795000000"}, None)
        for prefix, body in self.routes.items():
            if path.startswith(prefix):
                return FakeResp(body)
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)


def routes():
    raw = load_raw()
    r = {"/users/ada-dev/repos": raw["repos"], "/users/ada-dev": raw["profile"],
         "/repos/ada-dev/queue-lite/readme": {"encoding": "base64", "content": base64.b64encode(
             raw["readmes"]["queue-lite"].encode()).decode()}}
    for name, langs in raw["languages"].items():
        r["/repos/ada-dev/%s/languages" % name] = langs
    return r


class Selection(unittest.TestCase):
    def setUp(self):
        self.names = [r["name"] for r in pf.select_featured(load_raw()["repos"], "ada-dev", 10, NOW)]

    def test_excludes_forks_archived_and_undescribed(self):
        for skipped in ("linux", "archived-thing", "dotfiles"):
            self.assertNotIn(skipped, self.names)

    def test_excludes_profile_and_pages_repos(self):
        self.assertNotIn("ada-dev", self.names)
        self.assertNotIn("ada-dev.github.io", self.names)

    def test_stars_and_recency_both_count(self):
        self.assertEqual(self.names[0], "queue-lite")
        # old-blog has more stars than ratelimit but has not been touched since 2019
        self.assertLess(self.names.index("ratelimit"), self.names.index("old-blog"))

    def test_top_limits_featured(self):
        data = pf.build(load_raw(), top=3, now=NOW)
        self.assertEqual(sum(x["featured"] for x in data["projects"]), 3)
        self.assertTrue(all(x["featured"] for x in data["projects"][:3]))

    def test_private_repo_never_listed(self):
        raw = load_raw()
        raw["repos"].append(dict(raw["repos"][0], name="secret", private=True))
        self.assertNotIn("secret", [x["name"] for x in pf.build(raw, now=NOW)["projects"]])


class Readme(unittest.TestCase):
    def test_first_paragraph_skips_badges_and_strips_markup(self):
        text = pf.first_paragraph(load_raw()["readmes"]["queue-lite"])
        self.assertTrue(text.startswith("queue-lite runs background jobs"))
        self.assertIn("db.t3.medium instance", text)
        self.assertNotIn("](", text)
        self.assertNotIn("**", text)

    def test_setext_heading_and_code_fence(self):
        md = "Title\n=====\n\n```\ncode here\n```\n\nReal text.\nMore text.\n\nNext para."
        self.assertEqual(pf.first_paragraph(md), "Real text. More text.")

    def test_truncates_long_text(self):
        out = pf.first_paragraph("word " * 200, limit=50)
        self.assertLessEqual(len(out), 53)
        self.assertTrue(out.endswith("..."))


class Rendering(unittest.TestCase):
    def setUp(self):
        self.data = pf.build(load_raw(), now=NOW)
        self.page = pf.render(self.data)

    def test_well_formed(self):
        c = parse(self.page)
        self.assertEqual(c.errors, [])
        self.assertEqual(c.stack, [])

    def test_no_scripts_or_external_styles(self):
        c = parse(self.page)
        self.assertEqual(c.external, [])
        self.assertEqual(self.page.count("<style>"), 1)

    def test_hostile_description_is_escaped(self):
        self.assertNotIn("<script>", self.page)
        self.assertIn("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; &quot;quotes&quot; &#x27;single&#x27;",
                      self.page)

    def test_javascript_url_dropped(self):
        self.assertNotIn("javascript:", self.page)
        kafka = next(x for x in self.data["projects"] if x["name"] == "kafka-tools")
        self.assertEqual(pf.safe_url(kafka["live"]), "")

    def test_safe_url(self):
        self.assertEqual(pf.safe_url("ada.example.com"), "https://ada.example.com")
        self.assertEqual(pf.safe_url("http://x.io/a"), "http://x.io/a")
        for bad in ("javascript:alert(1)", "data:text/html,hi", "", "https://"):
            self.assertEqual(pf.safe_url(bad), "")

    def test_landmarks_alt_and_sections(self):
        c = parse(self.page)
        for tag in ("header", "main", "footer", "nav", "section"):
            self.assertIn(tag, c.tags)
        self.assertTrue(all(i.get("alt") for i in c.imgs))
        for heading in ("Featured projects", "Languages", "Recent activity", "Contact:"):
            self.assertIn(heading, self.page)
        self.assertIn("prefers-color-scheme:dark", self.page)
        self.assertIn(":focus-visible", self.page)
        self.assertIn('href="https://queue-lite.example.com"', self.page)

    def test_hidden_project_not_rendered(self):
        for x in self.data["projects"]:
            if x["name"] == "pg-migrate":
                x["featured"] = False
        self.assertNotIn("Zero-downtime schema migrations", pf.render(self.data))

    def test_language_shares_from_bytes_then_fallback(self):
        self.assertEqual(self.data["languages"][0][0], "Go")
        raw = load_raw()
        raw["languages"] = {}
        langs = dict(pf.language_shares(raw))
        self.assertNotIn("C", langs)  # the fork's language does not count
        self.assertIn("Go", langs)


class Overrides(unittest.TestCase):
    def test_round_trip_keeps_edits_and_refreshes_numbers(self):
        first = pf.build(load_raw(), now=NOW)
        edited = json.loads(json.dumps(first))
        q = next(x for x in edited["projects"] if x["name"] == "queue-lite")
        q["blurb"] = "Problem: cron jobs got lost. Built: a Postgres queue. Result: 4,000 jobs/s."
        q["title"] = "Queue Lite"
        edited["projects"].remove(q)
        edited["projects"].insert(1, q)
        next(x for x in edited["projects"] if x["name"] == "ratelimit")["featured"] = False
        edited["profile"]["bio"] = "I build boring, reliable backends."

        raw = load_raw()
        raw["repos"][0]["stargazers_count"] = 500
        raw["repos"].append(dict(raw["repos"][1], name="brand-new", description="New thing"))
        raw["repos"] = [r for r in raw["repos"] if r["name"] != "kafka-tools"]
        second = pf.build(raw, overrides=edited, now=NOW)

        names = [x["name"] for x in second["projects"]]
        self.assertEqual(names[1], "queue-lite")
        q2 = second["projects"][1]
        self.assertEqual((q2["title"], q2["stars"]), ("Queue Lite", 500))
        self.assertTrue(q2["blurb"].startswith("Problem:"))
        self.assertFalse(next(x for x in second["projects"] if x["name"] == "ratelimit")["featured"])
        self.assertNotIn("kafka-tools", names)
        self.assertEqual(names[-1], "brand-new")
        self.assertFalse(second["projects"][-1]["featured"])
        self.assertEqual(second["profile"]["bio"], "I build boring, reliable backends.")

    def test_wrong_types_ignored_and_bad_file_rejected(self):
        first = pf.build(load_raw(), now=NOW)
        ov = copy.deepcopy(first)
        ov["projects"][0]["stack"] = "Go"
        ov["projects"][0]["title"] = 42
        out = pf.build(load_raw(), overrides=ov, now=NOW)
        self.assertEqual(out["projects"][0]["stack"], first["projects"][0]["stack"])
        self.assertEqual(out["projects"][0]["title"], first["projects"][0]["title"])
        with self.assertRaises(pf.Fail):
            pf.build(load_raw(), overrides={"projects": "nope"}, now=NOW)


class Fetching(unittest.TestCase):
    def test_fetch_counts_requests_and_decodes_readme(self):
        op = FakeOpener(routes())
        raw = pf.fetch("ada-dev", top=2, client=pf.Client(opener=op))
        self.assertEqual(raw["requests"], len(op.calls))
        self.assertEqual(len(op.calls), 2 + 2 * 2)  # profile, repos, then readme + languages per top repo
        self.assertIn("4,000 jobs/s", raw["readmes"]["queue-lite"])
        self.assertFalse(raw["partial"])

    def test_rate_limit_on_optional_calls_keeps_partial_data(self):
        op = FakeOpener(routes(), limit_after=3)
        raw = pf.fetch("ada-dev", top=6, client=pf.Client(opener=op))
        self.assertTrue(raw["partial"])
        self.assertIn("rate limit", raw["partial_reason"])
        self.assertEqual(len(op.calls), 4)  # stops after the first 403
        self.assertTrue(pf.build(raw, now=NOW)["projects"])

    def test_budget_stops_before_limit(self):
        op = FakeOpener(routes())
        raw = pf.fetch("ada-dev", top=6, client=pf.Client(opener=op, max_requests=4))
        self.assertEqual(len(op.calls), 4)
        self.assertTrue(raw["partial"])

    def test_rate_limit_on_profile_is_fatal(self):
        with self.assertRaises(pf.RateLimited):
            pf.fetch("ada-dev", client=pf.Client(opener=FakeOpener(routes(), limit_after=0)))

    def test_unknown_user_and_bad_name(self):
        with self.assertRaises(pf.Fail) as ctx:
            pf.fetch("nobody-here", client=pf.Client(opener=FakeOpener({})))
        self.assertIn("not found", str(ctx.exception))
        with self.assertRaises(pf.Fail):
            pf.fetch("../etc", client=pf.Client(opener=FakeOpener({})))

    def test_token_sent_only_when_given(self):
        seen = []

        def opener(req, timeout=None):
            seen.append(req.get_header("Authorization"))
            raise urllib.error.HTTPError(req.full_url, 404, "x", {}, None)
        for tok in (None, "t0k"):
            with self.assertRaises(pf.Fail):
                pf.fetch("ada-dev", client=pf.Client(opener=opener, token=tok))
        self.assertEqual(seen, [None, "Bearer t0k"])


class Cli(unittest.TestCase):
    def run_cli(self, *args):
        env = dict(os.environ, GITHUB_TOKEN="")
        return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, env=env, timeout=60)

    def test_from_json_writes_site_and_overrides_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "site")
            r = self.run_cli("--from-json", FIXTURE, "--out", out, "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(sorted(os.listdir(out)), [".nojekyll", "data.json", "index.html"])
            path = os.path.join(out, "data.json")
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            data["projects"][0]["blurb"] = "Edited by hand"
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            r = self.run_cli("--from-json", FIXTURE, "--out", out, "--overrides", path)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(os.path.join(out, "index.html"), encoding="utf-8") as fh:
                self.assertIn("Edited by hand", fh.read())

    def test_bad_input_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self.run_cli("--from-json", os.path.join(tmp, "missing.json"), "--out", tmp)
        self.assertEqual(r.returncode, 2)
        self.assertIn("cannot read", r.stderr)

    def test_publish_instructions_print_only(self):
        r = self.run_cli("--publish-instructions", "--user", "Ada-Dev", "--out", "site")
        self.assertEqual(r.returncode, 0)
        self.assertIn("git remote add origin https://github.com/Ada-Dev/ada-dev.github.io.git", r.stdout)
        self.assertIn("git push -u origin main", r.stdout)


if __name__ == "__main__":
    unittest.main()
