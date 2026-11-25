#!/usr/bin/env python3
"""Tests for triage.py (stdlib unittest, never calls GitHub).

Run: python3 -m unittest discover -s skills/issue-triage/scripts -p 'test_*.py'
fixtures/issues.json holds 15 open issues of a made-up CLI with known duplicates (#101/#108/#119,
#102/#117), bug reports missing details, stale issues and all three reaction formats. The --repo
path runs against a fake `gh` placed first on PATH, which records its arguments.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import triage  # noqa: E402

SCRIPT = os.path.join(HERE, "triage.py")
FIXTURE = os.path.join(HERE, "fixtures", "issues.json")
NOW = "2026-11-25T00:00:00Z"


def run(*args, stdin=None, env=None):
    return subprocess.run([sys.executable, SCRIPT, *args], input=stdin, capture_output=True,
                          text=True, timeout=60, env=env)


def report(*args):
    r = run(FIXTURE, "--json", "--now", NOW, *args)
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    rep["by"] = {i["number"]: i for i in rep["issues"]}
    return rep


def issue(number=1, title="t", body="", author="rep", comments=(), labels=(), **extra):
    d = {"number": number, "title": title, "body": body, "author": {"login": author},
         "labels": [{"name": n} for n in labels], "comments": list(comments),
         "createdAt": "2026-11-20T00:00:00Z", "updatedAt": "2026-11-20T00:00:00Z"}
    d.update(extra)
    return d


def one(raw, days=30):
    return triage.triage([raw], triage.parse_time(NOW), days, 0.3)["issues"][0]


class Duplicates(unittest.TestCase):
    def test_known_pairs_found_with_score_and_shared_terms(self):
        pairs = {(p["original"], p["duplicate"]): p for p in report()["duplicates"]}
        self.assertEqual(set(pairs), {(102, 117), (101, 119), (101, 108)})
        self.assertIn("yaml", pairs[(102, 117)]["shared_terms"])
        self.assertGreaterEqual(pairs[(102, 117)]["score"], 0.35)
        self.assertIn("unicode", pairs[(101, 108)]["shared_terms"])

    def test_threshold_controls_candidates(self):
        self.assertEqual(report("--threshold", "0.95")["duplicates"], [])
        self.assertIn([108, 119], [[p["original"], p["duplicate"]] for p in report("--threshold", "0.3")["duplicates"]])

    def test_duplicate_count_feeds_priority_reasons(self):
        i = report()["by"][101]
        self.assertEqual(i["duplicates"], 2)
        self.assertIn("2 duplicate candidates", i["reasons"])

    def test_pasted_logs_keep_only_error_lines(self):
        text = "see log\n```\nratelimit nebula preview\nError: rate limit hit\n```\nend"
        self.assertEqual(triage.without_logs(text), "see log\nError: rate limit hit\nend")

    def test_plural_stemming(self):
        self.assertEqual(triage.tokens("crashes files the config"), ["crash", "file", "config"])


class MissingInfo(unittest.TestCase):
    def test_vague_bug_misses_everything(self):
        self.assertEqual(report()["by"][103]["missing"], ["version", "steps", "expected/actual", "error/logs"])

    def test_complete_reports_miss_nothing(self):
        by = report()["by"]
        for n in (101, 107, 110):
            self.assertEqual(by[n]["missing"], [], n)

    def test_unanswered_issue_form_fields_count_as_missing(self):
        # "### Version\n\n_No response_" must not pass the version or expected checks
        self.assertEqual(report()["by"][113]["missing"], ["version", "expected/actual", "error/logs"])

    def test_reporter_comment_supplies_info_but_others_do_not(self):
        body = "Export fails. Steps: 1. run export. Expected a file, actual nothing. Log: ```x```"
        reporter = issue(body=body, comments=[{"author": {"login": "rep"}, "body": "on v2.4.1"}])
        other = issue(body=body, comments=[{"author": {"login": "x"}, "body": "on v2.4.1"}])
        self.assertEqual(one(reporter)["missing"], [])
        self.assertEqual(one(other)["missing"], ["version"])

    def test_features_questions_and_docs_are_not_bug_checked(self):
        by = report()["by"]
        for n in (102, 104, 112, 117):
            self.assertFalse(by[n]["bug_like"], n)
            self.assertEqual(by[n]["missing"], [], n)


class Staleness(unittest.TestCase):
    def test_stale_issues_and_who_they_wait_on(self):
        by = report()["by"]
        self.assertTrue(by[109]["stale"])
        self.assertEqual(by[109]["last_word"], "none")
        self.assertEqual((by[106]["last_word"], by[106]["last_by"]), ("maintainer", "maint-amy"))
        self.assertEqual(by[106]["idle_days"], 75)
        self.assertFalse(by[110]["stale"])
        self.assertEqual(by[110]["last_word"], "reporter")

    def test_days_option(self):
        self.assertFalse(any(i["stale"] for i in report("--days", "200")["issues"]))

    def test_drive_by_comment_keeps_waiting_on_reporter(self):
        i = one(issue(comments=[
            {"author": {"login": "amy"}, "authorAssociation": "MEMBER", "body": "Which version?"},
            {"author": {"login": "bob"}, "authorAssociation": "NONE", "body": "+1"}]))
        self.assertEqual(i["last_word"], "maintainer")
        self.assertIn("waiting on reporter", i["reasons"])

    def test_without_association_any_other_person_counts_as_maintainer(self):
        i = one(issue(comments=[{"author": {"login": "amy"}, "body": "Which version?"}]))
        self.assertEqual(i["last_word"], "maintainer")
        self.assertTrue(i["maintainer_replied"])


class FieldVariants(unittest.TestCase):
    def test_reaction_formats(self):
        by = report()["by"]
        self.assertEqual(by[101]["reactions"], 12)  # reactionGroups (gh)
        self.assertEqual(by[119]["reactions"], 1)   # reactions, same group shape
        self.assertEqual(by[107]["reactions"], 4)   # reactions, REST {"total_count": N}

    def test_rest_style_user_and_comment_count(self):
        i = one({"number": 5, "title": "Crash on save", "body": "", "user": {"login": "u"},
                 "comments": 3, "labels": ["bug"], "updatedAt": "2026-01-01T00:00:00Z"})
        self.assertEqual((i["author"], i["comments"], i["labels"]), ("u", 3, ["bug"]))
        self.assertTrue(i["stale"])


class LabelsAndPriority(unittest.TestCase):
    def test_label_suggestions(self):
        by = report()["by"]
        names = {n: [s["label"] for s in by[n]["suggested_labels"]] for n in by}
        self.assertEqual(names[117], ["feature"])
        self.assertEqual(names[104], ["question"])
        self.assertEqual(names[105], ["docs", "good first issue"])
        self.assertIn("bug", names[103])
        self.assertEqual(names[101], [])  # already labelled bug
        self.assertEqual(names[111], [])  # enhancement already covers feature

    def test_priority_and_attention_order(self):
        rep = report()
        by = rep["by"]
        self.assertEqual(by[110]["priority"], "P1")  # data loss
        self.assertEqual(by[107]["priority"], "P1")  # token leak
        self.assertEqual(by[102]["priority"], "P2")  # 20 reactions, no severity
        self.assertEqual(by[105]["priority"], "P3")
        self.assertEqual(rep["attention"][0], 101)
        self.assertEqual(rep["attention"][-1], 106)  # waiting on the reporter

    def test_feature_demand_caps_at_p2_and_log_words_are_not_severity(self):
        many = [{"content": "THUMBS_UP", "users": {"totalCount": 60}}]
        self.assertEqual(one(issue(title="Add dark mode", labels=["enhancement"], reactionGroups=many))["priority"], "P2")
        logged = issue(title="Wrong exit code", body="```\nsecurityPolicy: expose leak\n```")
        self.assertEqual(one(logged)["severe"], [])

    def test_text_report_sections(self):
        r = run(FIXTURE, "--now", NOW, "--top", "3")
        self.assertEqual(r.returncode, 0, r.stderr)
        for head in ("Needs attention first", "Duplicate candidates", "missing repro details",
                     "Stale (no activity for 30+ days)", "Label suggestions",
                     "#117 may duplicate #102", "waiting on reporter (last reply @maint-amy)"):
            self.assertIn(head, r.stdout)
        self.assertNotIn("  4. #", r.stdout)

    def test_text_sections_are_capped(self):
        r = run(FIXTURE, "--now", NOW, "--max", "1")
        self.assertIn("... 3 more (see --json)", r.stdout)  # 4 stale issues, 1 shown


class Input(unittest.TestCase):
    def test_stdin(self):
        with open(FIXTURE) as fh:
            r = run("-", "--json", "--now", NOW, stdin=fh.read())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["count"], 15)

    def test_empty_list(self):
        r = run("-", "--now", NOW, stdin="[]")
        self.assertEqual(r.returncode, 0)
        self.assertIn("0 open issues", r.stdout)

    def test_bad_input_exits_2(self):
        self.assertEqual(run("-", stdin="not json").returncode, 2)
        self.assertEqual(run("-", stdin='{"issues": []}').returncode, 2)
        self.assertEqual(run(FIXTURE, "--repo", "a/b").returncode, 2)
        self.assertEqual(run(os.path.join(HERE, "missing.json")).returncode, 2)


class GhFetch(unittest.TestCase):
    """--repo runs a fake gh first on PATH; it records argv and prints the fixture."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = os.path.join(self.tmp.name, "argv.json")
        gh = os.path.join(self.tmp.name, "gh")
        with open(gh, "w") as fh:
            fh.write("#!%s\nimport json, os, sys\n"
                     "open(os.environ['FAKE_GH_LOG'], 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
                     "code = int(os.environ.get('FAKE_GH_EXIT', '0'))\n"
                     "if code:\n    sys.stderr.write('gh: not logged in\\n'); sys.exit(code)\n"
                     "sys.stdout.write(open(%r).read())\n" % (sys.executable, FIXTURE))
        os.chmod(gh, os.stat(gh).st_mode | stat.S_IEXEC)
        self.env = dict(os.environ, PATH=self.tmp.name + os.pathsep + os.environ.get("PATH", ""),
                        FAKE_GH_LOG=self.log)

    def tearDown(self):
        self.tmp.cleanup()

    def calls(self):
        with open(self.log) as fh:
            return [json.loads(line) for line in fh]

    def test_runs_exactly_one_read_only_gh_command(self):
        r = run("--repo", "acme/acme-cli", "--json", "--now", NOW, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["source"], "acme/acme-cli")
        self.assertEqual(self.calls(), [[
            "issue", "list", "--repo", "acme/acme-cli", "--state", "open", "--limit", "500", "--json",
            "number,title,body,labels,comments,reactionGroups,createdAt,updatedAt,author,url"]])

    def test_warns_when_limit_reached(self):
        r = run("--repo", "acme/acme-cli", "--limit", "15", "--now", NOW, env=self.env)
        self.assertEqual(r.returncode, 0)
        self.assertIn("--limit", r.stderr)

    def test_gh_failure_exits_2(self):
        env = dict(self.env, FAKE_GH_EXIT="1")
        r = run("--repo", "acme/acme-cli", env=env)
        self.assertEqual(r.returncode, 2)
        self.assertIn("not logged in", r.stderr)


if __name__ == "__main__":
    unittest.main()
