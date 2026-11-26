#!/usr/bin/env python3
"""Tests for bus_factor.py (stdlib unittest, no network).

Run: python3 -m unittest discover -s skills/bus-factor/scripts -p 'test_*.py'
Each test builds a throwaway git repo where several authors commit at known dates, then checks
ownership, recency, the bus-factor search, aliasing, skipping rules, anonymity and the SVG card.
Git runs with an empty global config so the user's settings never leak in.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bus_factor as bf  # noqa: E402

SCRIPT = os.path.join(HERE, "bus_factor.py")
BASE_ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", LC_ALL="C")
for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
    BASE_ENV.pop(var, None)
NOW = "2026-11-01"
PEOPLE = {"alice": ("Alice Liddell", "alice@example.com"), "bob": ("Bob Builder", "bob@example.com"),
          "carol": ("Carol Danvers", "carol@example.com"), "dave": ("Dave Lister", "dave@example.com")}


def lines(n, tag="x"):
    return "".join("%s line %d\n" % (tag, i) for i in range(n))


class Repo:
    def __init__(self, root):
        self.path = os.path.join(root, "repo")
        os.makedirs(self.path)
        self.git("init", "-q", "-b", "main")

    def git(self, *args, env=None):
        r = subprocess.run(["git", *args], cwd=self.path, env=env or BASE_ENV, capture_output=True, text=True)
        if r.returncode:
            raise AssertionError("git %s: %s" % (" ".join(args), r.stderr))
        return r.stdout

    def commit(self, who, date, files, raw=None):
        name, email = raw or PEOPLE[who]
        for rel, text in files.items():
            full = os.path.join(self.path, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as fh:
                fh.write(text)
        stamp = date + "T12:00:00Z"
        env = dict(BASE_ENV, GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email, GIT_COMMITTER_NAME=name,
                   GIT_COMMITTER_EMAIL=email, GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
        self.git("add", "-A", env=env)
        self.git("commit", "-q", "-m", "change by %s" % name, env=env)

    def run(self, *args):
        return subprocess.run([sys.executable, SCRIPT, "-C", self.path, "--now", NOW, *args], env=BASE_ENV,
                              capture_output=True, text=True, timeout=120)

    def report(self, *args):
        r = self.run("--json", *args)
        if r.returncode:
            raise AssertionError(r.stderr)
        return json.loads(r.stdout)


def by_path(rep):
    return {f["path"]: f for f in rep["files"]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repo(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def team_repo(self):
        """alice: core (60 lines), bob: api (30), carol: ui (10). All active in 2026."""
        r = self.repo
        r.commit("alice", "2026-03-01", {"core/engine.py": lines(40, "a"), "core/state.py": lines(20, "a")})
        r.commit("bob", "2026-04-01", {"api/routes.py": lines(30, "b")})
        r.commit("carol", "2026-05-01", {"ui/app.js": lines(10, "c")})
        return r


class TestBusFactorSearch(unittest.TestCase):
    def test_single_owner_majority_gives_one(self):
        g = {frozenset(["a"]): 60, frozenset(["b"]): 40}
        self.assertEqual(bf.bus_factor(g, 100)[:2], (1, ["a"]))

    def test_shared_knowledge_raises_factor(self):
        g = {frozenset(["a", "b"]): 50, frozenset(["c"]): 50}
        self.assertEqual(bf.bus_factor(g, 100)[0], 3)

    def test_already_orphaned_majority_is_zero(self):
        g = {frozenset(): 60, frozenset(["a"]): 40}
        self.assertEqual(bf.bus_factor(g, 100), (0, [], "exact"))

    def crafted(self):
        # A knows the most lines but only redundantly; B and C together orphan 52%.
        return {frozenset(["b"]): 26, frozenset(["c"]): 26, frozenset(["a", "b"]): 10,
                frozenset(["a", "c"]): 10, frozenset(["a", "d"]): 28}

    def test_exact_search_beats_greedy(self):
        k, removed, method = bf.bus_factor(self.crafted(), 100)
        self.assertEqual((k, sorted(removed), method), (2, ["b", "c"], "exact"))

    def test_greedy_fallback_when_budget_is_spent(self):
        old = bf.EXACT_BUDGET
        bf.EXACT_BUDGET = 0
        try:
            k, removed, method = bf.bus_factor(self.crafted(), 100)
        finally:
            bf.EXACT_BUDGET = old
        self.assertEqual((k, removed[0], method), (3, "a", "greedy"))

    def test_labels(self):
        self.assertEqual([bf.letters(i) for i in (0, 25, 26, 27)], ["A", "Z", "AA", "AB"])


class TestSkipping(unittest.TestCase):
    def test_skip_reasons(self):
        self.assertEqual(bf.skip_reason("web/package-lock.json"), "lockfile")
        self.assertEqual(bf.skip_reason("vendor/github.com/x/y.go"), "vendored")
        self.assertEqual(bf.skip_reason("static/app.min.js"), "generated")
        self.assertEqual(bf.skip_reason("api/v1/svc.pb.go"), "generated")
        self.assertEqual(bf.skip_reason("README.md"), "docs")
        self.assertEqual(bf.skip_reason("docs/setup.py"), "docs")
        self.assertIsNone(bf.skip_reason("README.md", include_docs=True))
        self.assertIsNone(bf.skip_reason("src/build_tools.py"))


class TestOwnership(Base):
    def test_owners_shares_and_factor(self):
        rep = self.team_repo().report()
        files = by_path(rep)
        self.assertEqual(files["core/engine.py"]["owner_share"], 1.0)
        self.assertEqual(files["core/engine.py"]["owner"], "Author A")
        self.assertEqual(files["api/routes.py"]["owner"], "Author B")
        self.assertEqual(rep["lines_scanned"], 100)
        self.assertEqual((rep["bus_factor"], rep["removed"], rep["method"]), (1, ["Author A"], "exact"))
        self.assertEqual(rep["orphaned_after_removal_pct"], 60.0)

    def test_mixed_file_share_and_risk(self):
        r = self.repo
        r.commit("alice", "2026-01-01", {"svc.py": lines(17, "a")})
        r.commit("bob", "2026-02-01", {"svc.py": lines(17, "a") + lines(3, "b")})
        f = by_path(r.report())["svc.py"]
        self.assertEqual((f["owner_share"], f["lines"], f["recent_authors"], f["churn"]), (0.85, 20, 2, 2))
        self.assertTrue(f["at_risk"])
        self.assertEqual(f["risk"], 17)

    def test_two_other_recent_authors_clear_the_risk(self):
        r = self.repo
        r.commit("alice", "2026-01-01", {"svc.py": lines(18, "a")})
        r.commit("bob", "2026-02-01", {"svc.py": lines(18, "a") + "b\n"})
        r.commit("carol", "2026-03-01", {"svc.py": lines(18, "a") + "b\nc\n"})
        f = by_path(r.report())["svc.py"]
        self.assertGreaterEqual(f["owner_share"], 0.8)
        self.assertFalse(f["at_risk"])
        self.assertEqual(f["status"], "ok")

    def test_whitespace_only_change_keeps_ownership(self):
        r = self.repo
        r.commit("alice", "2026-01-01", {"m.py": lines(10, "a")})
        r.commit("bob", "2026-02-01", {"m.py": "".join("    " + l + "\n" for l in lines(10, "a").splitlines())})
        f = by_path(r.report())["m.py"]
        self.assertEqual((f["owner"], f["owner_share"]), ("Author A", 1.0))

    def test_directories_and_only_you_know(self):
        rep = self.team_repo().report()
        dirs = {d["dir"]: d for d in rep["dirs"]}
        self.assertEqual((dirs["core"]["files"], dirs["core"]["lines"], dirs["core"]["risk"]), (2, 60, 60))
        self.assertEqual(rep["dirs"][0]["dir"], "core")
        people = {p["person"]: p for p in rep["people"]}
        self.assertEqual(people["Author A"]["only_knower_paths"], ["core/engine.py", "core/state.py"])
        self.assertEqual(people["Author C"]["only_knower_lines"], 10)

    def test_paths_limit_the_scan(self):
        rep = self.team_repo().report("api")
        self.assertEqual(list(by_path(rep)), ["api/routes.py"])


class TestRecency(Base):
    def setUp(self):
        super().setUp()
        self.repo.commit("alice", "2024-06-01", {"legacy/old.py": lines(70, "a")})
        self.repo.commit("bob", "2026-09-01", {"new/fresh.py": lines(30, "b")})

    def test_inactive_owner_leaves_file_orphaned(self):
        rep = self.repo.report()
        f = by_path(rep)["legacy/old.py"]
        self.assertEqual((f["status"], f["recent_authors"], f["owner_active"]), ("orphaned", 0, False))
        self.assertEqual(rep["bus_factor"], 0)
        self.assertEqual(rep["orphaned_now_pct"], 70.0)
        self.assertIn("Bus factor: 0", self.repo.run().stdout)

    def test_wider_window_brings_owner_back(self):
        rep = self.repo.report("--days", "1000")
        self.assertEqual(by_path(rep)["legacy/old.py"]["status"], "single owner")
        self.assertEqual(rep["bus_factor"], 1)

    def test_one_recent_touch_is_not_knowledge_but_two_are(self):
        old = lines(70, "a")
        self.repo.commit("bob", "2026-10-01", {"legacy/old.py": old + "fix 1\n"})
        f = by_path(self.repo.report())["legacy/old.py"]
        self.assertEqual((f["recent_authors"], f["status"]), (1, "orphaned"))
        self.repo.commit("bob", "2026-10-02", {"legacy/old.py": old + "fix 2\n"})
        f = by_path(self.repo.report())["legacy/old.py"]
        self.assertEqual((f["knowers"], f["status"], f["churn"]), (["Author B"], "single owner", 2))

    def test_active_owner_still_knows_untouched_file(self):
        self.repo.commit("alice", "2026-10-01", {"new/other.py": lines(5, "a")})
        f = by_path(self.repo.report())["legacy/old.py"]
        self.assertEqual((f["recent_authors"], f["knowers"], f["status"]), (0, ["Author A"], "single owner"))


class TestIdentities(Base):
    def test_mailmap_merges_identities(self):
        r = self.repo
        r.commit(None, "2026-01-01", {"a.py": lines(10, "x")}, raw=("bob", "bob@old.example"))
        r.commit(None, "2026-02-01", {"a.py": lines(10, "x") + lines(10, "y")}, raw=("Bobby", "bob@new.example"))
        self.assertEqual(len(r.report()["people"]), 2)
        r.commit("alice", "2026-03-01", {".mailmap": "Bob Builder <bob@example.com> <bob@old.example>\n"
                                                    "Bob Builder <bob@example.com> <bob@new.example>\n"})
        rep = r.report("--show-authors")
        owners = [p["person"] for p in rep["people"] if p["lines_owned"]]
        self.assertEqual(owners, ["Bob Builder <bob@example.com>"])
        self.assertEqual(rep["skipped"], {"git metadata": 1})
        self.assertEqual(by_path(rep)["a.py"]["owner_share"], 1.0)

    def test_alias_flag_merges_by_email_or_name(self):
        r = self.repo
        r.commit(None, "2026-01-01", {"a.py": lines(10, "x")}, raw=("bob", "bob@laptop.local"))
        r.commit("bob", "2026-02-01", {"a.py": lines(10, "x") + lines(10, "y")})
        rep = r.report("--alias", "bob@laptop.local=Bob Builder")
        self.assertEqual(len(rep["people"]), 1)
        self.assertEqual(by_path(rep)["a.py"]["owner_share"], 1.0)
        self.assertEqual(len(r.report("--alias", "BOB=Bob Builder")["people"]), 1)

    def test_bad_alias_is_rejected(self):
        self.repo.commit("alice", "2026-01-01", {"a.py": "x\n"})
        self.assertNotEqual(self.repo.run("--alias", "nobody").returncode, 0)


class TestAnonymity(Base):
    def test_default_output_has_no_names_or_emails(self):
        r = self.team_repo()
        out = r.run().stdout + r.run("--json").stdout
        for name, email in PEOPLE.values():
            self.assertNotIn(name.split()[0], out)
            self.assertNotIn(email, out)
        self.assertIn("Author A", out)

    def test_labels_follow_lines_owned_and_are_consistent(self):
        r = self.team_repo()
        rep = r.report()
        self.assertEqual([(p["person"], p["lines_owned"]) for p in rep["people"]],
                         [("Author A", 60), ("Author B", 30), ("Author C", 10)])
        text = r.run().stdout
        self.assertIn("Losing Author A", text)
        self.assertIn("Author C: 1 files, 10 lines", text)

    def test_show_authors_prints_names(self):
        text = self.team_repo().run("--show-authors").stdout
        self.assertIn("Alice Liddell <alice@example.com>", text)


class TestFilesAndExitCodes(Base):
    def test_lockfiles_generated_vendored_and_docs_are_skipped(self):
        r = self.repo
        r.commit("alice", "2026-01-01", {"app.py": lines(5), "package-lock.json": lines(500),
                                         "vendor/lib/x.go": lines(300), "web/app.min.js": lines(200),
                                         "README.md": lines(50), "logo.png": "\x00\x01\x02binary"})
        rep = r.report()
        self.assertEqual(list(by_path(rep)), ["app.py"])
        self.assertEqual(rep["skipped"], {"lockfile": 1, "vendored": 1, "generated": 1, "docs": 1, "binary": 1})
        self.assertIn("README.md", by_path(r.report("--include-docs")))

    def test_max_files_caps_largest_first_and_says_so(self):
        r = self.repo
        r.commit("alice", "2026-01-01", {"big.py": lines(50), "mid.py": lines(20), "small.py": lines(5)})
        rep = r.report("--max-files", "2")
        self.assertEqual(sorted(by_path(rep)), ["big.py", "mid.py"])
        self.assertTrue(rep["capped"])
        self.assertIn("Capped: blamed the 2 largest of 3 files", r.run("--max-files", "2").stdout)

    def test_not_a_git_repo_exits_2(self):
        plain = os.path.join(self.tmp.name, "plain")
        os.makedirs(plain)
        r = subprocess.run([sys.executable, SCRIPT, "-C", plain], env=BASE_ENV, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("not a git repository", r.stderr)

    def test_no_commits_exits_2(self):
        self.assertEqual(self.repo.run().returncode, 2)

    def test_nothing_to_scan_exits_2(self):
        self.repo.commit("alice", "2026-01-01", {"README.md": lines(5), "yarn.lock": lines(5)})
        r = self.repo.run()
        self.assertEqual(r.returncode, 2)
        self.assertIn("nothing to scan", r.stderr)


class TestSvg(Base):
    def card(self, *args):
        path = os.path.join(self.tmp.name, "card.svg")
        r = self.repo.run("--svg", path, *args)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def test_svg_is_well_formed_and_anonymous(self):
        self.team_repo()
        svg = self.card()
        root = ET.fromstring(svg)
        self.assertTrue(root.tag.endswith("svg"))
        texts = " ".join(t.text or "" for t in root.iter("{http://www.w3.org/2000/svg}text"))
        self.assertIn("BUS FACTOR", texts)
        self.assertIn("Author A 60%", texts)
        for name, email in PEOPLE.values():
            self.assertNotIn(name.split()[0], svg)
            self.assertNotIn(email, svg)

    def test_svg_escapes_names_when_shown(self):
        self.repo.commit(None, "2026-01-01", {"a.py": lines(3)}, raw=("Tom & Jerry", "tj@example.com"))
        svg = self.card("--show-authors")
        ET.fromstring(svg)
        self.assertIn("Tom &amp; Jerry", svg)


if __name__ == "__main__":
    unittest.main()
