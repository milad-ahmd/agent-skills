#!/usr/bin/env python3
"""Tests for find_skill.py (stdlib unittest, no network).

Run: python3 -m unittest discover -s skills/skill-finder/scripts -p 'test_*.py'
Each test builds its own fixture skills folder, catalog and HOME in a temp dir.
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
import find_skill as fs  # noqa: E402

SCRIPT = os.path.join(HERE, "find_skill.py")

FIXTURE_SKILLS = {
    "commit-craft": "Turns messy working-tree changes into clean, atomic Conventional Commits and cleans up git "
                    "history before a PR. Use when the user asks to commit, split commits or tidy git history.",
    "ad-copy-generator": "Writes ad copy for Meta, Google and LinkedIn campaigns with hooks and calls to action. "
                         "Use when the user asks for ads, marketing copy or campaign headlines.",
    "seo-blog-writer": "Writes SEO blog posts from keyword research with outlines and meta descriptions. Use when "
                       "the user asks for a blog post, article or marketing content that ranks.",
    "dockerize-anything": "Writes a small, secure multi-stage Dockerfile and compose file for any repo. Use when the "
                          "user asks to dockerize a project or make a Docker image smaller.",
    "env-secrets-guard": "Finds leaked API keys, tokens and secrets in a repo and its git history before they are "
                         "pushed. Use when the user asks to scan for secrets.",
}
CATALOG = (
    "commit-craft\t🧱\tClean, atomic Conventional Commits\tGit & Workflow\tCommit my changes|Clean up my git history\n"
    "ad-copy-generator\t📣\tAd copy for paid campaigns\tMarketing\tWrite Facebook ads|Ad headlines for launch\n"
    "seo-blog-writer\t✍️\tSEO blog posts that rank\tMarketing\tWrite a blog post about pricing\n"
    "dockerize-anything\t🐳\tSmall, secure Dockerfiles\tDevOps\tDockerize this project|Make my image smaller\n"
    "env-secrets-guard\t🔐\tFind leaked secrets before push\tSecurity\tScan for leaked keys\n"
    "ghost-skill\t👻\tIn the catalog only\tFun\tBoo\n"
)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def make_skill(root, name, desc, fm_name=None):
    write(os.path.join(root, name, "SKILL.md"),
          f"---\nname: {fm_name or name}\ndescription: {desc}\n---\n\n# {name}\n")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.skills = os.path.join(self.tmp, "skills")
        for name, desc in FIXTURE_SKILLS.items():
            make_skill(self.skills, name, desc)
        self.catalog = os.path.join(self.tmp, "catalog.tsv")
        write(self.catalog, CATALOG)
        self.home = os.path.join(self.tmp, "home")
        self.project = os.path.join(self.tmp, "project")
        os.makedirs(self.home)
        os.makedirs(self.project)
        self.index = os.path.join(self.tmp, "index.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args):
        env = dict(os.environ, HOME=self.home)
        r = subprocess.run([sys.executable, SCRIPT, *args], cwd=self.project, env=env,
                           capture_output=True, text=True)
        return r.returncode, r.stdout, r.stderr

    def build(self):
        code, out, err = self.run_cli("--build-index", self.skills, "--catalog", self.catalog, "--out", self.index)
        self.assertEqual(code, 0, err)
        return out, err


class TestFrontmatter(unittest.TestCase):
    def test_plain_scalars(self):
        fm = fs.parse_frontmatter("---\nname: foo\ndescription: Does a thing. Use when asked.\n---\nbody")
        self.assertEqual(fm, {"name": "foo", "description": "Does a thing. Use when asked."})

    def test_folded_description(self):
        text = "---\nname: foo\ndescription: >\n  Line one\n  continues here.\n\n  New paragraph.\nlicense: MIT\n---\n"
        fm = fs.parse_frontmatter(text)
        self.assertEqual(fm["description"], "Line one continues here.\nNew paragraph.")
        self.assertEqual(fm["license"], "MIT")

    def test_folded_strip_and_literal(self):
        fm = fs.parse_frontmatter("---\na: >-\n  x\n  y\nb: |\n  keep\n  lines\n---\n")
        self.assertEqual(fm["a"], "x y")
        self.assertEqual(fm["b"], "keep\nlines")

    def test_quoted_values(self):
        fm = fs.parse_frontmatter("---\nname: \"foo\"\ndescription: 'It''s quoted: yes'\n---\n")
        self.assertEqual(fm["name"], "foo")
        self.assertEqual(fm["description"], "It's quoted: yes")

    def test_plain_value_continued_on_indented_lines(self):
        fm = fs.parse_frontmatter("---\ndescription: starts here\n  and goes on\nname: x\n---\n")
        self.assertEqual(fm["description"], "starts here and goes on")

    def test_crlf_and_bom(self):
        fm = fs.parse_frontmatter("\ufeff---\r\nname: foo\r\ndescription: bar\r\n---\r\nbody")
        self.assertEqual(fm, {"name": "foo", "description": "bar"})

    def test_no_frontmatter(self):
        self.assertIsNone(fs.parse_frontmatter("# Just a heading\n"))
        self.assertIsNone(fs.parse_frontmatter("---\nname: unterminated\n"))


class TestIndexBuild(Base):
    def test_build_merges_catalog(self):
        out, err = self.build()
        self.assertIn("5 skills", out)
        data = json.load(open(self.index, encoding="utf-8"))
        by_name = {s["name"]: s for s in data["skills"]}
        self.assertEqual(sorted(by_name), sorted(FIXTURE_SKILLS))
        self.assertEqual(by_name["commit-craft"]["category"], "Git & Workflow")
        self.assertEqual(by_name["commit-craft"]["examples"], ["Commit my changes", "Clean up my git history"])
        self.assertEqual(by_name["dockerize-anything"]["emoji"], "🐳")
        self.assertIn("ghost-skill", err)  # catalog row without a skill folder is reported, not indexed

    def test_build_without_catalog(self):
        code, out, _ = self.run_cli("--build-index", self.skills, "--out", self.index)
        self.assertEqual(code, 0)
        s = json.load(open(self.index, encoding="utf-8"))["skills"][0]
        self.assertEqual((s["tagline"], s["category"], s["examples"]), ("", "", []))

    def test_build_is_deterministic(self):
        self.build()
        first = open(self.index, "rb").read()
        self.build()
        self.assertEqual(first, open(self.index, "rb").read())

    def test_skips_folders_without_description(self):
        write(os.path.join(self.skills, "broken", "SKILL.md"), "no frontmatter here\n")
        os.makedirs(os.path.join(self.skills, "not-a-skill"))
        index, warnings = fs.build_index(self.skills)
        self.assertNotIn("broken", [s["name"] for s in index["skills"]])
        self.assertTrue(any("broken" in w for w in warnings))

    def test_catalog_parsing_tolerates_short_rows(self):
        write(self.catalog, "# comment\nsolo\t\nfull\t🔧\tTag\tCat\ta|b|\n\n")
        cat = fs.read_catalog(self.catalog)
        self.assertEqual(cat["solo"]["examples"], [])
        self.assertEqual(cat["full"]["examples"], ["a", "b"])

    def test_missing_skills_dir_is_exit_2(self):
        code, _, err = self.run_cli("--build-index", os.path.join(self.tmp, "nope"), "--out", self.index)
        self.assertEqual(code, 2)
        self.assertIn("error", err)


class TestRanking(Base):
    def setUp(self):
        super().setUp()
        self.build()
        self.skills_list = fs.load_index(self.index)

    def top(self, query):
        return fs.rank(query, self.skills_list)[0][1]["name"]

    def test_git_rebase_ranks_git_skill_not_marketing(self):
        ranked = fs.rank("my git rebase has conflicts", self.skills_list)
        self.assertEqual(ranked[0][1]["name"], "commit-craft")
        marketing = {"ad-copy-generator", "seo-blog-writer"}
        self.assertNotIn(ranked[0][1]["name"], marketing)
        self.assertTrue(all(r[0] == 0 for r in ranked if r[1]["name"] in marketing))

    def test_obvious_matches(self):
        self.assertEqual(self.top("make my docker image smaller"), "dockerize-anything")
        self.assertEqual(self.top("I think I pushed an API key, check for leaked secrets"), "env-secrets-guard")
        self.assertEqual(self.top("write facebook ads for our launch"), "ad-copy-generator")

    def test_alias_counts_toward_coverage(self):
        ranked = fs.rank("rebase", self.skills_list)
        self.assertEqual(ranked[0][1]["name"], "commit-craft")
        self.assertEqual(ranked[0][3], 1)

    def test_self_is_excluded(self):
        skills = self.skills_list + [{"name": "skill-finder", "description": "docker docker docker",
                                      "tagline": "", "category": "", "examples": []}]
        self.assertNotIn("skill-finder", [r[1]["name"] for r in fs.rank("docker", skills)])

    def test_one_generic_shared_word_is_not_enough(self):
        result = (3.0, {"name": "dockerize-anything"}, ["secure"], 1, 4)
        self.assertFalse(fs.good_enough(result, 2.0, "secure my wedding venue booking"))
        self.assertTrue(fs.good_enough((3.0, {"name": "dockerize-anything"}, ["docker"], 1, 3), 2.0,
                                       "docker for bakery"))  # word from the skill's name
        self.assertFalse(fs.good_enough((1.0, {"name": "x"}, ["a", "b"], 2, 2), 2.0, "a b"))

    def test_tokens_drop_stopwords_and_stem(self):
        self.assertEqual(fs.tokens("Is there a skill for writing tests?"), ["test"])
        self.assertEqual(fs.stem("commits"), fs.stem("commit"))
        self.assertEqual(fs.stem("pushed"), "push")


class TestSearchCli(Base):
    def setUp(self):
        super().setUp()
        self.build()

    def test_results_json_and_install_commands(self):
        code, out, _ = self.run_cli("make my docker image smaller", "--index", self.index, "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        first = data["results"][0]
        self.assertEqual(first["name"], "dockerize-anything")
        self.assertEqual(first["summary"], "Small, secure Dockerfiles")
        self.assertTrue(first["install"]["curl"].endswith("bash /tmp/agent-skills-install.sh dockerize-anything"))
        self.assertEqual(first["install"]["claude_plugin"],
                         "/plugin install all-skills --marketplace milad-ahmd/agent-skills")
        self.assertIsNone(data["ecosystem_search"])

    def test_no_match_exit_1_with_npx_suggestion(self):
        code, out, _ = self.run_cli("bake sourdough bread at home", "--index", self.index)
        self.assertEqual(code, 1)
        self.assertIn("npx skills find bake sourdough bread home", out)
        code, out, _ = self.run_cli("bake sourdough bread", "--index", self.index, "--json")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["results"], [])

    def test_top_limits_results(self):
        code, out, _ = self.run_cli("write marketing blog ads campaign", "--index", self.index, "--json", "--top", "1")
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)["results"]), 1)

    def test_installed_skill_is_marked(self):
        make_skill(os.path.join(self.home, ".claude", "skills"), "dockerize-anything", "x")
        code, out, _ = self.run_cli("make my docker image smaller", "--index", self.index)
        self.assertEqual(code, 0)
        self.assertIn("installed: ~/.claude/skills/dockerize-anything [claude-code]", out)

    def test_errors_exit_2(self):
        self.assertEqual(self.run_cli("docker", "--index", os.path.join(self.tmp, "missing.json"))[0], 2)
        self.assertEqual(self.run_cli("the and of", "--index", self.index)[0], 2)  # only stopwords
        self.assertEqual(self.run_cli()[0], 2)
        self.assertEqual(self.run_cli("docker", "--installed")[0], 2)


class TestInstalled(Base):
    def test_finds_global_project_and_plugin_skills(self):
        make_skill(os.path.join(self.home, ".claude", "skills"), "alpha", "a")
        make_skill(os.path.join(self.home, ".cursor", "skills"), "beta", "b")
        make_skill(os.path.join(self.home, ".agents", "skills"), "gamma-dir", "c", fm_name="gamma")
        make_skill(os.path.join(self.project, ".claude", "skills"), "delta", "d")
        make_skill(os.path.join(self.home, ".claude", "plugins", "cache", "mkt", "plug", "1.0", "skills"),
                   "epsilon", "e")
        os.makedirs(os.path.join(self.home, ".claude", "skills", "empty-folder"))
        found = fs.find_installed(home=self.home, project=self.project)
        pairs = {(f["name"], f["agent"]) for f in found}
        self.assertEqual(pairs, {("alpha", "claude-code"), ("beta", "cursor"), ("gamma", "codex/cline (shared)"),
                                 ("delta", "claude-code (project)"), ("epsilon", "claude-code plugin")})

    def test_cli_installed_text_and_exit_codes(self):
        code, out, _ = self.run_cli("--installed")
        self.assertEqual(code, 1)
        self.assertIn("No installed skills", out)
        make_skill(os.path.join(self.home, ".claude", "skills"), "alpha", "a")
        code, out, _ = self.run_cli("--installed", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)[0]["name"], "alpha")

    def test_project_equal_to_home_is_not_double_counted(self):
        make_skill(os.path.join(self.home, ".claude", "skills"), "alpha", "a")
        found = fs.find_installed(home=self.home, project=self.home)
        self.assertEqual(len(found), 1)


class TestMainInProcess(Base):
    def test_bad_top_value(self):
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            self.assertEqual(fs.main(["docker", "--top", "0"]), 2)
        self.assertIn("--top", err.getvalue())


class BundledIndex(unittest.TestCase):
    def test_index_lists_every_skill_in_the_collection(self):
        # Only inside the agent-skills repo; catches a new skill added without rebuilding the index.
        skills_dir = os.path.join(HERE, "..", "..")
        if not os.path.exists(os.path.join(skills_dir, "..", "scripts", "catalog.tsv")):
            self.skipTest("not inside the agent-skills repo")
        on_disk = {d for d in os.listdir(skills_dir) if os.path.isfile(os.path.join(skills_dir, d, "SKILL.md"))}
        with open(os.path.join(HERE, "index.json")) as f:
            indexed = {s["name"] for s in json.load(f)["skills"]}
        self.assertEqual(indexed, on_disk, "run: python3 skills/skill-finder/scripts/find_skill.py --build-index skills --catalog scripts/catalog.tsv")


if __name__ == "__main__":
    unittest.main()
