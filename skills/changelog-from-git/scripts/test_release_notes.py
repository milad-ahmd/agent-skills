#!/usr/bin/env python3
"""Tests for release_notes.py (stdlib unittest, no network).

Run: python3 -m unittest discover -s skills/changelog-from-git/scripts -p 'test_*.py'
Each test builds a throwaway git repo with made-up authors at fixed dates, using real `git revert` and
`git merge --no-ff`, then checks ranges, grouping, PR numbers, reverts, dependency bumps and exit codes.
Git runs with an empty global config so the user's settings never leak in.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import release_notes as rn  # noqa: E402

SCRIPT = os.path.join(HERE, "release_notes.py")
BASE_ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", LC_ALL="C", TZ="UTC")
for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
    BASE_ENV.pop(var, None)
PEOPLE = {"ana": ("Ana Example", "ana@example.com"), "ben": ("Ben Sample", "ben@example.com"),
          "bot": ("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com")}


class Repo:
    def __init__(self, root):
        self.path = os.path.join(root, "repo")
        os.makedirs(self.path)
        self.day = 0
        self.n = 0
        self.git("init", "-q", "-b", "main")

    def env(self, who):
        self.day += 1
        name, email = PEOPLE[who]
        stamp = "2026-10-%02dT12:00:00Z" % self.day
        return dict(BASE_ENV, GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email, GIT_COMMITTER_NAME=name,
                    GIT_COMMITTER_EMAIL=email, GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)

    def git(self, *args, env=None):
        r = subprocess.run(["git", *args], cwd=self.path, env=env or BASE_ENV, capture_output=True, text=True)
        if r.returncode:
            raise AssertionError("git %s: %s" % (" ".join(args), r.stderr))
        return r.stdout.strip()

    def commit(self, msg, who="ana"):
        self.n += 1
        with open(os.path.join(self.path, "f%d.txt" % self.n), "w") as fh:
            fh.write("%s\n" % msg)
        env = self.env(who)
        self.git("add", "-A", env=env)
        self.git("commit", "-q", "-m", msg, env=env)
        return self.git("rev-parse", "HEAD")

    def revert(self, sha, who="ana"):
        self.git("revert", "--no-edit", sha, env=self.env(who))
        return self.git("rev-parse", "HEAD")

    def tag(self, name):
        self.git("tag", name)

    def run(self, *args):
        return subprocess.run([sys.executable, SCRIPT, "-C", self.path, *args], env=BASE_ENV,
                              capture_output=True, text=True, timeout=60)

    def report(self, *args):
        r = self.run("--json", *args)
        if r.returncode:
            raise AssertionError(r.stderr)
        return json.loads(r.stdout)


def descs(rep, group):
    return [e["description"] for e in rep["groups"][group]]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repo(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()


class ParseTests(unittest.TestCase):
    def parse(self, msg):
        return rn.parse_commit("a" * 40, ["b" * 40], "X", "x@example.com", "2026-10-01", msg)

    def test_type_scope_bang(self):
        c = self.parse("feat(api)!: drop v1 endpoints")
        self.assertEqual((c["type"], c["scope"], c["breaking"], c["description"]), ("feat", "api", True, "drop v1 endpoints"))

    def test_type_is_case_insensitive(self):
        self.assertEqual(self.parse("FEAT: add export")["type"], "feat")

    def test_breaking_footer_both_spellings(self):
        for token in ("BREAKING CHANGE", "BREAKING-CHANGE"):
            c = self.parse("fix: timeout unit\n\nBody text.\n\n%s: timeout is now in ms\nand not seconds\nRefs: #4" % token)
            self.assertTrue(c["breaking"], token)
            self.assertEqual(c["breaking_note"], "timeout is now in ms and not seconds")

    def test_lowercase_breaking_footer_is_ignored(self):
        self.assertFalse(self.parse("fix: x\n\nbreaking change: not a footer")["breaking"])

    def test_missing_space_after_colon_is_not_conventional(self):
        c = self.parse("feat:no space")
        self.assertFalse(c["conventional"])

    def test_squash_pr_number_is_stripped(self):
        c = self.parse("fix(ui): crash on empty avatar (#140)")
        self.assertEqual((c["pr"], c["description"]), (140, "crash on empty avatar"))

    def test_bump_levels(self):
        g = {k: [] for k, _ in rn.GROUPS}
        self.assertEqual(rn.bump(g, "v1.3.2"), dict(rn.bump(g, "v1.3.2"), level="patch", next="v1.3.3"))
        g["features"] = [1]
        self.assertEqual(rn.bump(g, "1.3.2")["next"], "1.4.0")
        g["breaking"] = [1]
        self.assertEqual(rn.bump(g, "v1.3.2")["next"], "v2.0.0")
        self.assertIsNone(rn.bump(g, "release-7")["next"])


class RangeTests(Base):
    def test_default_range_starts_at_latest_tag(self):
        self.repo.commit("feat: old thing")
        self.repo.tag("v1.0.0")
        self.repo.commit("feat: add CSV export")
        rep = self.repo.report()
        self.assertEqual(rep["range"]["from"], "v1.0.0")
        self.assertEqual(descs(rep, "features"), ["add CSV export"])
        self.assertEqual(rep["suggested_bump"]["next"], "v1.1.0")

    def test_head_on_tag_uses_previous_tag(self):
        self.repo.commit("feat: one")
        self.repo.tag("v1.0.0")
        self.repo.commit("fix: two")
        self.repo.tag("v1.0.1")
        rep = self.repo.report()
        self.assertEqual((rep["range"]["from"], rep["range"]["to_tag"]), ("v1.0.0", "v1.0.1"))
        self.assertEqual(descs(rep, "fixes"), ["two"])

    def test_no_tags_reads_whole_history(self):
        self.repo.commit("feat: one")
        self.repo.commit("feat: two")
        rep = self.repo.report()
        self.assertTrue(rep["range"]["from_root"])
        self.assertEqual(rep["counts"]["commits"], 2)

    def test_explicit_from_and_to(self):
        a = self.repo.commit("feat: a")
        b = self.repo.commit("fix: b")
        self.repo.commit("perf: c")
        rep = self.repo.report("--from", a, "--to", b)
        self.assertEqual(descs(rep, "fixes"), ["b"])
        self.assertEqual(descs(rep, "performance"), [])


class ExitCodeTests(Base):
    def test_empty_range_exits_1(self):
        self.repo.commit("feat: a")
        r = self.repo.run("--from", "HEAD")
        self.assertEqual(r.returncode, 1)

    def test_bad_ref_exits_2(self):
        self.repo.commit("feat: a")
        self.assertEqual(self.repo.run("--from", "v9.9.9").returncode, 2)

    def test_not_a_repo_exits_2(self):
        plain = os.path.join(self.tmp.name, "plain")
        os.makedirs(plain)
        r = subprocess.run([sys.executable, SCRIPT, "-C", plain], env=dict(BASE_ENV, GIT_CEILING_DIRECTORIES=self.tmp.name),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)


class GroupingTests(Base):
    def test_groups_and_breaking_wins(self):
        self.repo.commit("feat: export")
        self.repo.commit("fix(ui): crash")
        self.repo.commit("perf: faster load")
        self.repo.commit("docs: readme")
        self.repo.commit("refactor: tidy")
        self.repo.commit("feat!: drop old config")
        rep = self.repo.report()
        self.assertEqual(descs(rep, "breaking"), ["drop old config"])
        self.assertEqual(descs(rep, "features"), ["export"])
        self.assertEqual(descs(rep, "other"), ["tidy"])
        self.assertEqual(rep["suggested_bump"]["level"], "major")

    def test_non_conventional_repo_lands_in_other(self):
        self.repo.commit("Add export button")
        self.repo.commit("Fixed the crash")
        rep = self.repo.report()
        self.assertEqual(len(rep["groups"]["other"]), 2)
        self.assertEqual(rep["counts"]["conventional"], 0)
        self.assertIn("group by reading", self.repo.run().stdout)

    def test_dependency_bumps_collapse_but_breaking_bump_stays(self):
        self.repo.commit("feat: real work")
        self.repo.commit("Bump lodash from 4.17.20 to 4.17.21", who="bot")
        self.repo.commit("chore(deps): bump left-pad", who="ben")
        self.repo.commit("build(deps)!: require node 22", who="ben")
        rep = self.repo.report()
        self.assertEqual(rep["counts"]["dependency_bumps"], 2)
        self.assertEqual(descs(rep, "breaking"), ["require node 22"])
        self.assertEqual(rep["contributors"], 2)  # the bot is not a contributor


class RevertTests(Base):
    def test_reverted_pair_disappears(self):
        self.repo.commit("feat: keep me")
        sha = self.repo.commit("feat: flaky idea")
        self.repo.revert(sha)
        rep = self.repo.report()
        self.assertEqual(descs(rep, "features"), ["keep me"])
        self.assertEqual(rep["counts"]["reverted_pairs"], 1)
        self.assertEqual(rep["groups"]["other"], [])

    def test_revert_of_revert_keeps_original(self):
        sha = self.repo.commit("feat: back again")
        r1 = self.repo.revert(sha)
        self.repo.revert(r1)
        rep = self.repo.report()
        self.assertEqual(descs(rep, "features"), ["back again"])
        self.assertEqual(rep["counts"]["reverted_pairs"], 1)

    def test_lone_revert_stays_visible(self):
        sha = self.repo.commit("feat: shipped last release")
        self.repo.tag("v1.0.0")
        self.repo.revert(sha)
        rep = self.repo.report()
        self.assertEqual(len(rep["groups"]["other"]), 1)
        self.assertTrue(rep["groups"]["other"][0]["description"].startswith('Revert "feat: shipped'))

    def test_revert_of_older_release_does_not_eat_a_new_commit(self):
        sha = self.repo.commit("fix: x")
        self.repo.tag("v1.0.0")
        self.repo.revert(sha)
        self.repo.commit("fix: x")
        rep = self.repo.report()
        self.assertEqual(rep["counts"]["reverted_pairs"], 0)
        self.assertEqual(descs(rep, "fixes"), ["x"])

    def test_squash_style_revert_matches_by_subject(self):
        self.repo.commit("feat: x (#12)")
        self.repo.commit('Revert "feat: x (#12)" (#13)')
        rep = self.repo.report()
        self.assertEqual(rep["counts"]["reverted_pairs"], 1)


class MergeAndPrivacyTests(Base):
    def test_merge_pr_number_attached_and_merge_skipped(self):
        self.repo.commit("chore: init")
        self.repo.git("checkout", "-q", "-b", "feature")
        self.repo.commit("feat: search box", who="ben")
        self.repo.git("checkout", "-q", "main")
        self.repo.git("merge", "--no-ff", "-q", "-m", "Merge pull request #12 from someone/feature", "feature",
                      env=self.repo.env("ana"))
        rep = self.repo.report()
        self.assertEqual(rep["groups"]["features"][0]["pr"], 12)
        self.assertEqual(rep["counts"]["merges_skipped"], 1)
        self.assertIn("(#12,", self.repo.run("--md").stdout)

    def test_authors_hidden_unless_asked(self):
        self.repo.commit("feat: a", who="ana")
        self.repo.commit("fix: b", who="ben")
        for args in (("--json",), ("--md",), ()):
            text = self.repo.run(*args).stdout
            self.assertNotIn("Ana", text, args)
            self.assertNotIn("example.com", text, args)
        rep = self.repo.report("--show-authors")
        self.assertEqual(rep["contributors"], 2)
        self.assertIn("Ana Example <ana@example.com>", rep["authors"])

    def test_md_skeleton(self):
        self.repo.commit("feat: a")
        self.repo.tag("v0.1.0")
        self.repo.commit("feat(api)!: rename field\n\nBREAKING CHANGE: `id` is now `uid`")
        md = self.repo.run("--md").stdout
        self.assertTrue(md.startswith("## v1.0.0 - 2026-10-02"))
        self.assertIn("### Breaking changes", md)
        self.assertIn("**api:** rename field -- `id` is now `uid`", md)


if __name__ == "__main__":
    unittest.main()
