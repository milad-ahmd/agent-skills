#!/usr/bin/env python3
"""Regression tests for skill_checks / skills_doctor (stdlib unittest).

Run: python3 -m unittest discover -s skills/skills-doctor/scripts -p 'test_*.py'
Cases 1-3 come from an external review (Wisp, 2026-10-26).
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from skill_checks import find_secrets, jaccard, parse_frontmatter, trigger_clause  # noqa: E402
from skills_doctor import gh_escape  # noqa: E402

DOCTOR = os.path.join(HERE, "skills_doctor.py")
GOOD_DESC = "Deploys the app to production with health checks and rollback steps. Use when the user asks to deploy, release, or ship."


def make_skill(root, name, desc_block, body="# x\n## Rules\n- ok\n"):
    d = os.path.join(root, name)
    os.makedirs(d)
    with open(os.path.join(d, "SKILL.md"), "w") as f:
        f.write(f"---\nname: {name}\n{desc_block}\n---\n{body}")
    return d


def run_doctor(*args, env=None):
    return subprocess.run([sys.executable, DOCTOR, *args], capture_output=True, text=True, env=env)


class Frontmatter(unittest.TestCase):
    def test_folded_scalar(self):  # review case 2
        fm, _ = parse_frontmatter("---\nname: a\ndescription: >\n  Reviews pull requests\n  for bugs. Use when asked.\n---\nbody")
        self.assertEqual(fm["description"], "Reviews pull requests for bugs. Use when asked.")

    def test_literal_and_strip_chomping(self):
        fm, _ = parse_frontmatter("---\nname: a\ndescription: |-\n  line one\n  line two\n---\n")
        self.assertEqual(fm["description"], "line one\nline two")

    def test_quoted_and_plain_continuation(self):
        fm, _ = parse_frontmatter('---\nname: "a"\ndescription: "Does X.\n  Use when Y."\n---\n')
        self.assertEqual((fm["name"], fm["description"]), ("a", "Does X. Use when Y."))
        fm, _ = parse_frontmatter("---\nname: a\ndescription: Does X\n  and Y. Use when Z.\n---\n")
        self.assertEqual(fm["description"], "Does X and Y. Use when Z.")

    def test_missing(self):
        self.assertIsNone(parse_frontmatter("# no frontmatter")[0])


class Secrets(unittest.TestCase):
    def kinds(self, text):
        return [k for k, _, _ in find_secrets(text)]

    def test_review_case_3(self):
        hits = find_secrets("DATABASE_URL=postgres://admin:hunter2@prod-db.internal:5432/app\n"
                            "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n")
        self.assertEqual(len(hits), 2)
        self.assertTrue(all("hunter2" not in v and "wJalrXUtn" not in v for _, _, v in hits))  # redacted

    def test_more_real_leaks(self):
        for line in ['config = {"client_secret": "9f8e7d6c5b4a3210fedcba98"}', "DB_PASSWORD: Sup3rS3cr3t!2026",
                     "mongodb+srv://app:Pa55w0rd99@cluster0.mongodb.net/db", "export GITHUB_TOKEN=ghx9aF3kL2mN8pQ4rS6tU1vW"]:
            self.assertTrue(self.kinds(line), line)

    def test_no_false_positives(self):
        for line in ["DATABASE_URL=postgres://user:password@localhost:5432/app", "API_KEY=${API_KEY}",
                     "STRIPE_SECRET=process.env.STRIPE_SECRET", "OPENAI_API_KEY=<your-key>", 'password = "changeme"',
                     "AWS_SECRET_ACCESS_KEY=xxxxxxxxxxxxxxxx", 'authors = Counter(c["a"] for c in commits)',
                     'SECRET = re.compile(r"(AKIA[0-9A-Z]{16})")', "hard-coded `password=`/`secret=` values",
                     "token = os.environ.get('GH_TOKEN')", "tokenizer = load_tokenizer(model_name)"]:
            self.assertEqual(self.kinds(line), [], line)


class Overlap(unittest.TestCase):
    def test_trigger_clause(self):
        self.assertEqual(trigger_clause("Does X. Use when the user asks to Y.").strip(), "the user asks to Y.")
        self.assertEqual(trigger_clause("Does X only."), "")

    def test_review_case_1_detects_near_duplicates(self):
        a = "Grows an X account with posts and threads. Use when the user asks to grow on X, write posts or threads, or plan content."
        b = "Grows a Threads account with posts. Use when the user asks to grow on Threads, write posts, or plan content."
        self.assertGreaterEqual(jaccard(trigger_clause(a), trigger_clause(b)), 0.30)


class DoctorCLI(unittest.TestCase):
    def test_exit_codes(self):
        with tempfile.TemporaryDirectory() as t:
            make_skill(t, "warn-only", "description: does stuff")
            self.assertEqual(run_doctor(t).returncode, 0)
            self.assertEqual(run_doctor("--strict", t).returncode, 2)
            make_skill(t, "leaky", f"description: {GOOD_DESC}", "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n")
            self.assertEqual(run_doctor(t).returncode, 1)

    def test_folded_skill_is_clean(self):
        with tempfile.TemporaryDirectory() as t:
            make_skill(t, "folded", "description: >\n  Reviews pull requests for correctness and security issues and writes\n"
                                     "  prioritized comments with evidence. Use when the user asks to review a PR.")
            r = run_doctor("--strict", t)
            self.assertEqual(r.returncode, 0, r.stdout)

    def test_reports_closest_pair_as_reading(self):  # review follow-up: "0 overlaps" must show how close it was
        with tempfile.TemporaryDirectory() as t:
            make_skill(t, "a-skill", f"description: {GOOD_DESC}")
            make_skill(t, "b-skill", "description: Writes Instagram captions and hashtags for posts. Use when the user asks for captions.")
            out = run_doctor(t).stdout
            self.assertIn("overlaps at or above threshold", out)
            self.assertRegex(out, r"closest description\s+\d\.\d\d / threshold 0\.20")
            self.assertRegex(out, r"closest trigger clause\s+\d\.\d\d / threshold 0\.30")

    def test_own_collection_is_strict_clean(self):
        skills = os.path.abspath(os.path.join(HERE, "..", ".."))
        if os.path.isdir(skills) and len(os.listdir(skills)) > 10:
            r = run_doctor("--strict", skills)
            self.assertEqual(r.returncode, 0, r.stdout[-2000:])


class GitHubFormat(unittest.TestCase):
    def test_escaping(self):
        self.assertEqual(gh_escape("50% done\nnext\r"), "50%25 done%0Anext%0D")
        self.assertEqual(gh_escape("a: b, c", prop=True), "a%3A b%2C c")

    def run_gh(self, t, *args):
        summary = os.path.join(t, "summary.md")
        env = dict(os.environ, GITHUB_STEP_SUMMARY=summary, GITHUB_WORKSPACE=t, PYTHONIOENCODING="utf-8")
        r = run_doctor("--format", "github", *args, os.path.join(t, "skills"), env=env)
        if not os.path.exists(summary):
            return r, ""
        with open(summary, encoding="utf-8") as f:
            return r, f.read()

    def test_annotations_lines_and_summary(self):
        with tempfile.TemporaryDirectory() as t:
            root = os.path.join(t, "skills")
            os.makedirs(root)
            make_skill(root, "Bad_Name", f"description: {GOOD_DESC}")
            make_skill(root, "no-desc", "license: MIT")
            make_skill(root, "leaky", f"description: {GOOD_DESC.replace('deploy', 'publish')}", "# x\n\nkey = " + "AKIA" + "Q" * 16 + "\n")
            r, summary = self.run_gh(t, "--strict")
            self.assertEqual(r.returncode, 1, r.stdout)
            out = r.stdout.splitlines()
            self.assertIn("::error file=skills/Bad_Name/SKILL.md,line=2,title=skills-doctor%3A Bad_Name::"
                          "name `Bad_Name` must be kebab-case and ≤ 64 chars", out)
            self.assertIn("::error file=skills/no-desc/SKILL.md,line=1,title=skills-doctor%3A no-desc::frontmatter has no `description`", out)
            secret = [ln for ln in out if ln.startswith("::error file=skills/leaky/SKILL.md,line=7,")]
            self.assertEqual(len(secret), 1, r.stdout)
            self.assertNotIn("Q" * 16, r.stdout + summary)  # never the full value
            self.assertTrue(any(ln.startswith("::warning file=skills/Bad_Name/SKILL.md,line=3,title=skills-doctor%3A overlap::") for ln in out))
            self.assertFalse(any("❌ name" in ln for ln in out))  # issues become annotations, not text lines
            self.assertIn("| Skills checked | Errors | Warnings | Overlaps |", summary)
            self.assertIn("| 3 | 3 | 0 | 1 |", summary)
            self.assertIn("`skills/leaky/SKILL.md:7`", summary)
            self.assertRegex(summary, r"\| description \| \d\.\d\d \| 0\.20 \| over \|")

    def test_clean_run_and_text_default_untouched(self):
        with tempfile.TemporaryDirectory() as t:
            make_skill(os.path.join(t, "skills"), "deployer", f"description: {GOOD_DESC}")
            r, summary = self.run_gh(t, "--strict")
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertNotIn("::", r.stdout)
            self.assertIn("**✅ clean**", summary)
            self.assertIn("| 1 | 0 | 0 | 0 |", summary)
            env = dict(os.environ, GITHUB_STEP_SUMMARY=os.path.join(t, "text.md"))
            r = run_doctor(os.path.join(t, "skills"), env=env)
            self.assertIn("✅ deployer", r.stdout)
            self.assertFalse(os.path.exists(os.path.join(t, "text.md")))  # text mode never writes the summary

    def test_warning_exit_code_and_no_skills(self):
        with tempfile.TemporaryDirectory() as t:
            make_skill(os.path.join(t, "skills"), "warn-only", "description: does stuff")
            r, summary = self.run_gh(t, "--strict")
            self.assertEqual(r.returncode, 2, r.stdout)
            self.assertIn("::warning file=skills/warn-only/SKILL.md,line=3,", r.stdout)
            self.assertIn("⚠️ warnings or overlaps", summary)
            r = run_doctor("--format", "github", os.path.join(t, "nowhere"))
            self.assertEqual(r.returncode, 1)
            self.assertTrue(r.stdout.startswith("::error title=skills-doctor::No skills found"), r.stdout)


if __name__ == "__main__":
    unittest.main()
