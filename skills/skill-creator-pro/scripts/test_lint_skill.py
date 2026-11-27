#!/usr/bin/env python3
"""Tests for the lint_skill.py CLI (stdlib unittest, no network).

Run: python3 -m unittest discover -s skills/skill-creator-pro/scripts -p 'test_*.py'
Each test writes a throwaway skill folder, runs the linter on it and checks the verdict lines and exit
code. The shared parsing and secret rules in skill_checks.py are tested by skills-doctor, not here.
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LINT = os.path.join(HERE, "lint_skill.py")
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")
GOOD_DESC = ("Deploys the app to production with health checks and rollback steps. "
             "Use when the user asks to deploy, release, or ship.")
GOOD_BODY = "# Deploy\n\n## 1. Steps\n\nRun the checks.\n\n## Rules\n\n- Confirm before deploying.\n"


def run(*args, cwd=None):
    return subprocess.run([sys.executable, LINT, *args], cwd=cwd, env=ENV, capture_output=True,
                          encoding="utf-8", timeout=60)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def skill(self, folder="deploy-app", name=None, desc=GOOD_DESC, body=GOOD_BODY, frontmatter=None, files=()):
        d = os.path.join(self.tmp.name, folder)
        os.makedirs(d)
        if frontmatter is None:
            frontmatter = "---\n" + ("name: %s\n" % (folder if name is None else name) if name != "" else "") + \
                          ("description: %s\n" % desc if desc else "") + "---\n"
        with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as fh:
            fh.write(frontmatter + body)
        for rel in files:
            os.makedirs(os.path.dirname(os.path.join(d, rel)), exist_ok=True)
            open(os.path.join(d, rel), "w").close()
        return d

    def lint(self, d, code):
        r = run(d)
        self.assertEqual(r.returncode, code, r.stdout + r.stderr)
        return r.stdout

    def assertVerdict(self, out, errors, warnings):
        self.assertTrue(out.endswith("%s: %d errors, %d warnings\n" % ("FAIL" if errors else "PASS", errors, warnings)), out)


class Valid(Base):
    def test_valid_skill_passes_clean(self):
        out = self.lint(self.skill(files=["scripts/deploy.py"],
                                   body=GOOD_BODY + "\nRun `python3 scripts/deploy.py`.\n"), 0)
        self.assertIn("✅ name `deploy-app`\n", out)
        self.assertIn("✅ description %d chars\n" % len(GOOD_DESC), out)
        self.assertIn("✅ body 11 lines\n", out)
        self.assertVerdict(out, 0, 0)

    def test_trailing_slash_on_path(self):
        self.assertVerdict(self.lint(self.skill() + "/", 0), 0, 0)

    def test_dot_path_from_inside_the_skill(self):
        r = run(".", cwd=self.skill())
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("✅ name `deploy-app`\n", r.stdout)
        self.assertVerdict(r.stdout, 0, 0)

    def test_reference_at_end_of_sentence(self):
        d = self.skill(files=["scripts/deploy.py", "references/api.md"],
                       body=GOOD_BODY + "\nRun scripts/deploy.py. Details are in references/api.md.\n")
        self.assertVerdict(self.lint(d, 0), 0, 0)


class Usage(Base):
    def test_wrong_argument_count_prints_usage(self):
        for args in ((), ("a", "b")):
            r = run(*args)
            self.assertEqual(r.returncode, 1)
            self.assertIn("Usage: python3 lint_skill.py path/to/skill-name", r.stderr)

    def test_missing_skill_md(self):
        d = os.path.join(self.tmp.name, "empty-skill")
        os.makedirs(d)
        r = run(d)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stderr.strip(), "❌ %s not found" % os.path.join(d, "SKILL.md"))


class Frontmatter(Base):
    def test_no_frontmatter(self):
        out = self.lint(self.skill(frontmatter=""), 1)
        self.assertIn("❌ frontmatter must start the file", out)
        self.assertIn("❌ missing `name`\n", out)
        self.assertIn("❌ missing `description`\n", out)
        self.assertVerdict(out, 3, 0)

    def test_unclosed_frontmatter(self):
        out = self.lint(self.skill(frontmatter="---\nname: deploy-app\ndescription: %s\n" % GOOD_DESC), 1)
        self.assertIn("❌ frontmatter must start the file", out)

    def test_missing_name(self):
        out = self.lint(self.skill(name=""), 1)
        self.assertIn("❌ missing `name`\n", out)
        self.assertVerdict(out, 1, 0)

    def test_name_not_kebab_case(self):
        out = self.lint(self.skill(folder="Deploy_App"), 1)
        self.assertIn("❌ `name` must be kebab-case ≤ 64 chars (got `Deploy_App`)\n", out)

    def test_name_longer_than_64(self):
        long_name = "-".join(["deploy"] * 10)  # 69 chars, valid kebab-case
        out = self.lint(self.skill(folder=long_name), 1)
        self.assertIn("(got `%s`)" % long_name, out)

    def test_name_not_matching_folder_is_a_warning(self):
        out = self.lint(self.skill(folder="deploy-app", name="ship-app"), 0)
        self.assertIn("⚠️  `name` (ship-app) should match folder (deploy-app)\n", out)
        self.assertVerdict(out, 0, 1)


class Description(Base):
    def test_missing_description(self):
        out = self.lint(self.skill(desc=""), 1)
        self.assertIn("❌ missing `description`\n", out)
        self.assertVerdict(out, 1, 0)

    def test_description_over_1024_chars(self):
        desc = GOOD_DESC + " Covers" + " blue-green" * 100
        out = self.lint(self.skill(desc=desc), 1)
        self.assertIn("❌ description too long (%d > 1024)\n" % len(desc), out)
        self.assertVerdict(out, 1, 0)

    def test_short_description_is_a_warning(self):
        out = self.lint(self.skill(desc="Deploys apps. Use when asked to deploy."), 0)
        self.assertIn("⚠️  description short (39 chars): add concrete triggers\n", out)

    def test_no_trigger_clause_is_a_warning(self):
        out = self.lint(self.skill(desc="Deploys the app to production with health checks, canary analysis and automatic rollback steps."), 0)
        self.assertIn("⚠️  add 'Use when the user asks to …' trigger phrases\n", out)
        self.assertVerdict(out, 0, 1)

    def test_first_person_is_a_warning(self):
        out = self.lint(self.skill(desc="I deploy the app to production with health checks. Use when the user asks to deploy."), 0)
        self.assertIn("⚠️  write the description in third person\n", out)


class Body(Base):
    def test_long_body_is_a_warning(self):
        out = self.lint(self.skill(body=GOOD_BODY + "step\n" * 500), 0)
        self.assertIn("⚠️  body is 509 lines (> 500): move details to references/\n", out)

    def test_no_headings_and_no_rules(self):
        out = self.lint(self.skill(body="Just do the deploy carefully.\n"), 0)
        self.assertIn("⚠️  no headings: structure as workflow / output / rules\n", out)
        self.assertIn("⚠️  no Rules section: add safety/quality guardrails\n", out)
        self.assertVerdict(out, 0, 2)

    def test_missing_referenced_files(self):
        d = self.skill(files=["scripts/present.py"],
                       body=GOOD_BODY + "\nRun `scripts/present.py`, then `scripts/check.sh`; read references/guide.md\n")
        out = self.lint(d, 1)
        self.assertIn("❌ referenced file missing: scripts/check.sh\n", out)
        self.assertIn("❌ referenced file missing: references/guide.md\n", out)
        self.assertNotIn("present.py", out)
        self.assertVerdict(out, 2, 0)

    def test_secret_is_an_error_and_redacted(self):
        key = "AKIA" + "Q" * 16  # built at runtime so skills-doctor never sees a key-shaped literal here
        out = self.lint(self.skill(body=GOOD_BODY + "\nexport KEY=%s\n" % key), 1)
        self.assertIn("❌ possible secret in SKILL.md:", out)
        self.assertIn("AKIA****", out)
        self.assertNotIn(key, out)


if __name__ == "__main__":
    unittest.main()
