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

DOCTOR = os.path.join(HERE, "skills_doctor.py")
GOOD_DESC = "Deploys the app to production with health checks and rollback steps. Use when the user asks to deploy, release, or ship."


def make_skill(root, name, desc_block, body="# x\n## Rules\n- ok\n"):
    d = os.path.join(root, name)
    os.makedirs(d)
    with open(os.path.join(d, "SKILL.md"), "w") as f:
        f.write(f"---\nname: {name}\n{desc_block}\n---\n{body}")
    return d


def run_doctor(*args):
    return subprocess.run([sys.executable, DOCTOR, *args], capture_output=True, text=True)


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


if __name__ == "__main__":
    unittest.main()
