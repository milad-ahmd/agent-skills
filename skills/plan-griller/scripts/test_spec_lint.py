#!/usr/bin/env python3
"""Tests for spec_lint.py (stdlib unittest).

Run: python3 -m unittest discover -s skills/plan-griller/scripts -p 'test_*.py'
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from spec_lint import DEFAULT_REQUIRE, lint  # noqa: E402

SCRIPT = os.path.join(HERE, "spec_lint.py")
FX = os.path.join(HERE, "fixtures")
TEMPLATE = os.path.join(HERE, "..", "references", "spec-template.md")

REQS = "- **R1** A signed-in user must be able to save the current search with a name.\n"
CRITS = "- **AC1** (R1) Given a signed-in user, when they click Save and type Bikes, then Bikes is listed.\n"
TESTS = "| Criterion | Test |\n|---|---|\n| AC1 | test_save |\n"
QUESTIONS = "None.\n"


def spec(reqs=REQS, crits=CRITS, tests=TESTS, questions=QUESTIONS, extra=""):
    return ("# Spec: Saved searches\n\n## Problem\n\nUsers retype searches.\n\n## Goals\n\n- Save a search.\n\n"
            "## Non-goals\n\n- Sharing.\n\n## Users and actors\n\n- Signed-in user.\n\n"
            f"## Requirements\n\n{reqs}\n## Acceptance criteria\n\n{crits}\n"
            "## Edge cases and error states\n\n- Empty name: Save is disabled.\n\n"
            "## Data and API changes\n\n- New table saved_searches.\n\n## Rollout, migration and flags\n\n- Flag saved_searches.\n\n"
            f"## Risks\n\nNone: small change.\n\n## Open questions\n\n{questions}\n## Test plan\n\n{tests}{extra}")


def rules(text, **kw):
    return sorted({f["rule"] for f in lint(text, **kw)[0]})


def run(*args):
    r = subprocess.run([sys.executable, SCRIPT, "--json", *args], capture_output=True, text=True)
    return r.returncode, (json.loads(r.stdout) if r.stdout.strip().startswith("{") else None)


class Fixtures(unittest.TestCase):
    def test_good_spec_is_clean_even_on_warnings(self):
        code, out = run(os.path.join(FX, "good_spec.md"), "--fail-on", "warning")
        self.assertEqual((code, out["findings"]), (0, []))
        self.assertEqual((out["stats"]["requirements"], out["stats"]["criteria"], out["stats"]["open_questions"]), (4, 5, 2))

    def test_bad_spec_hits_every_rule(self):
        code, out = run(os.path.join(FX, "bad_spec.md"))
        self.assertEqual(code, 1)
        got = {(f["rule"], f["id"]) for f in out["findings"]}
        for want in [("missing-section", None), ("uncovered-requirement", "R3"), ("duplicate-id", "R1"),
                     ("unresolved", "R5"), ("missing-id", "R5"), ("not-gwt", "AC2"), ("orphan-criterion", "AC3"),
                     ("untested-criterion", "AC3"), ("untested-criterion", "AC4"), ("unknown-reference", "AC4"),
                     ("unknown-reference", "AC7"), ("placeholder", None), ("empty-section", None),
                     ("question-without-owner", "Q1"), ("vague-term", "R2"), ("vague-term", "R3"),
                     ("vague-term", "AC2"), ("weasel-modal", "R2"), ("weasel-modal", "R5")]:
            self.assertIn(want, got)
        missing = [f["message"] for f in out["findings"] if f["rule"] == "missing-section"]
        self.assertEqual(sorted(missing), ["no 'Non-goals' section", "no 'Risks' section"])
        self.assertEqual(out["stats"]["weasel_modals"], 2)
        self.assertNotIn(("question-without-owner", "Q2"), got)

    def test_template_has_every_section_and_only_placeholders(self):
        with open(TEMPLATE, encoding="utf-8") as f:
            findings, stats = lint(f.read())
        self.assertEqual({f["rule"] for f in findings}, {"placeholder"})
        self.assertEqual(set(DEFAULT_REQUIRE) | {"decisions"}, set(stats["sections"]))


class ExitCodes(unittest.TestCase):
    def test_missing_file_and_no_headings(self):
        self.assertEqual(run("/nonexistent/SPEC.md")[0], 2)
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "SPEC.md")
            with open(p, "w") as f:
                f.write("just some prose, no headings\n")
            self.assertEqual(run(p)[0], 2)

    def test_fail_on_warning(self):
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "SPEC.md")
            with open(p, "w") as f:
                f.write(spec(reqs="- **R1** Saving should take one click.\n"))
            self.assertEqual(run(p)[0], 0)
            self.assertEqual(run(p, "--fail-on", "warning")[0], 1)

    def test_require_narrows_sections_and_rejects_unknown_keys(self):
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "SPEC.md")
            with open(p, "w") as f:
                f.write(f"# Spec\n\n## Requirements\n\n{REQS}\n## Acceptance criteria\n\n{CRITS}\n## Test plan\n\n{TESTS}")
            self.assertEqual(run(p)[0], 1)
            code, out = run(p, "--require", "requirements,acceptance,test-plan")
            self.assertEqual((code, out["findings"]), (0, []))
            self.assertEqual(run(p, "--require", "requirments")[0], 2)

    def test_text_output(self):
        r = subprocess.run([sys.executable, SCRIPT, os.path.join(FX, "bad_spec.md")], capture_output=True, text=True)
        self.assertIn("uncovered-requirement", r.stdout)
        self.assertIn("fix: ", r.stdout)


class Rules(unittest.TestCase):
    def test_minimal_spec_is_clean(self):
        self.assertEqual(lint(spec())[0], [])

    def test_numbered_list_counts_as_ids(self):
        s = spec(reqs="1. Users must save a search.\n2. Users must delete a search.\n",
                 crits=CRITS + "- **AC2** (R2) Given a saved search, when the user deletes it, then it is not listed.\n",
                 tests=TESTS + "| AC2 | test_delete |\n")
        findings, stats = lint(s)
        self.assertEqual((findings, stats["requirements"]), ([], 2))

    def test_uncovered_and_orphan(self):
        s = spec(reqs=REQS + "- **R2** Users must delete a search.\n",
                 crits=CRITS + "- **AC2** Given a search, when deleted, then gone.\n", tests=TESTS + "| AC2 | t |\n")
        self.assertEqual(rules(s), ["orphan-criterion", "uncovered-requirement"])

    def test_criteria_grouped_under_requirement_heading(self):
        s = spec(crits="### R1 saving\n\n- **AC1** Given a user, when they save, then it is listed.\n")
        self.assertEqual(lint(s)[0], [])

    def test_multiline_gwt_under_criterion_heading(self):
        s = spec(crits="### AC1 (R1)\n\n- Given a user\n- when they save\n- then it is listed\n")
        findings, stats = lint(s)
        self.assertEqual((findings, stats["criteria"]), ([], 1))
        s = spec(crits="### AC1 (R1)\n\n- When they save\n- then it is listed\n")
        self.assertEqual(rules(s), ["not-gwt"])

    def test_duplicate_criterion_ids(self):
        s = spec(crits=CRITS + CRITS)
        self.assertEqual(rules(s), ["duplicate-id"])

    def test_vague_terms_and_measures(self):
        def vague(req):
            return sorted(f["message"].split("'")[1] for f in lint(spec(reqs=f"- **R1** {req}\n"))[0] if f["rule"] == "vague-term")
        self.assertEqual(vague("The list must load fast."), ["fast"])
        self.assertEqual(vague("The list must load fast (p95 under 300 ms)."), [])
        self.assertEqual(vague("Storage must be secure."), ["secure"])
        self.assertEqual(vague("Storage must be secure: only the owner role can read it."), [])
        self.assertEqual(vague("Must handle errors gracefully and retry as needed."), ["as needed", "handle errors gracefully"])
        self.assertEqual(vague("Must work for various filters, sorts etc."), ["etc.", "various"])
        self.assertEqual(vague("Must be intuitive, seamless, robust and scalable."), ["intuitive", "robust", "scalable", "seamless"])
        self.assertEqual(vague("Must accept the `fast` and `some` flags."), [])

    def test_tbd_is_an_error_and_should_probably_is_vague_not_weasel(self):
        f = lint(spec(reqs="- **R1** Users should probably get an export, format TBD.\n"))[0]
        self.assertEqual(sorted((x["rule"], x["severity"]) for x in f), [("unresolved", "error"), ("vague-term", "warning")])

    def test_weasel_count(self):
        s = spec(reqs="- **R1** Users should save searches and could rename them; it might sync.\n")
        findings, stats = lint(s)
        self.assertEqual(stats["weasel_modals"], 3)
        self.assertEqual([f["message"] for f in findings if f["rule"] == "weasel-modal"], ["R1 says 'could, might, should'"])

    def test_test_plan_coverage(self):
        self.assertEqual(rules(spec(tests="Manual QA.\n")), ["untested-criterion"])
        self.assertEqual(rules(spec(tests=TESTS + "| AC2 | test_x |\n")), ["unknown-reference"])

    def test_open_question_owners(self):
        self.assertEqual(rules(spec(questions="- Q1 Limit per user? (owner: Lee)\n- Q2 Quiet hours? @sam\n")), [])
        self.assertEqual(rules(spec(questions="- Q1 Limit per user?\n")), ["question-without-owner"])
        table = "| Q | Question | Owner |\n|---|---|---|\n| Q1 | Limit? | Lee |\n| Q2 | Hours? | |\n"
        f = lint(spec(questions=table))[0]
        self.assertEqual([(x["rule"], x["id"]) for x in f], [("question-without-owner", "Q2")])

    def test_non_goals_variants(self):
        base = spec()
        no_ng = base.replace("## Non-goals\n\n- Sharing.\n\n", "")
        self.assertEqual(rules(no_ng), ["missing-section"])
        self.assertEqual(rules(no_ng.replace("- Save a search.\n", "- Save a search.\n\n**Non-goals:** sharing.\n")), [])
        combined = no_ng.replace("## Goals", "## Goals and non-goals")
        self.assertEqual(rules(combined), ["missing-section"])
        self.assertEqual(rules(combined.replace("- Save a search.\n", "- Save a search.\n\n### Non-goals\n\n- Sharing.\n")), [])
        self.assertEqual(rules(base.replace("- Sharing.\n", "")), ["empty-section"])

    def test_comments_code_and_html_are_ignored(self):
        s = spec(extra="\n<!-- TBD <fill me in> -->\n\n```\n- **R9** fast etc. TBD <x y>\n```\n\nLine<br>break, <https://example.com>.\n")
        self.assertEqual(lint(s)[0], [])

    def test_placeholder(self):
        self.assertEqual(rules(spec().replace("Users retype searches.", "<problem statement>")), ["placeholder"])

    def test_missing_id_warning(self):
        f = lint(spec(reqs="- Users must save a search.\n"))[0]
        self.assertEqual([(x["rule"], x["id"]) for x in f], [("missing-id", "R1")])


if __name__ == "__main__":
    unittest.main()
