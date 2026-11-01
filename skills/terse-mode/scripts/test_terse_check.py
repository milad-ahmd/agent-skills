#!/usr/bin/env python3
"""Tests for terse_check.py (stdlib unittest).

Run: python3 -m unittest discover -s skills/terse-mode/scripts -p 'test_*.py'
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import terse_check as tc  # noqa: E402

SCRIPT = os.path.join(HERE, "terse_check.py")

PADDED = """Great question! You're asking why the migration fails on staging. Let me walk you through it.

It's worth noting that the error is `relation "orders" does not exist`, which basically means the
migration in `db/migrations/0042_add_index.sql` runs before the table exists. I think it might
possibly be an ordering issue.

```bash
# Great question! Let me know if this helps. (inside code: never flagged)
git mv db/migrations/0042_add_index.sql db/migrations/0043_add_index.sql
```

Warning: `DROP TABLE orders` in 0040 is irreversible, so back up staging first. See https://example.com/docs/migrations.

In summary, the migration order is wrong. Hope this helps! Let me know if you have any other questions.
"""
TERSE = """`0042_add_index.sql` runs before `orders` exists: `relation "orders" does not exist`. Rename it:

```bash
# Great question! Let me know if this helps. (inside code: never flagged)
git mv db/migrations/0042_add_index.sql db/migrations/0043_add_index.sql
```

Warning: `DROP TABLE orders` in 0040 is irreversible; back up staging first. Docs: https://example.com/docs/migrations
Path: db/migrations/0042_add_index.sql
"""


def cats(text):
    return [h["category"] for h in tc.check(text)["hits"]]


class TestCheck(unittest.TestCase):
    def test_finds_every_padding_category(self):
        found = set(cats(PADDED))
        self.assertEqual(found, {"preamble", "restate", "filler", "hedge stack", "recap", "offer"})

    def test_code_blocks_and_inline_code_are_ignored(self):
        r = tc.check("```\nGreat question! basically in order to\n```\nUse `basically --really` here.\n")
        self.assertEqual(r["hits"], [])
        self.assertEqual(r["prose_words"], 2)

    def test_clean_reply_has_no_hits(self):
        r = tc.check("Run `npm install`, then commit package-lock.json.\n\n- Cause: stale lockfile.\n")
        self.assertEqual((r["hits"], r["removable_pct"]), ([], 0.0))

    def test_whole_sentence_rules_count_the_sentence(self):
        r = tc.check("Let me know if you have any other questions.")
        self.assertEqual(r["hits"][0]["category"], "offer")
        self.assertEqual(r["removable_words"], 9)

    def test_in_order_to_saves_two_words(self):
        r = tc.check("Restart the worker in order to reload the config.")
        self.assertEqual([(h["text"], h["words"]) for h in r["hits"]], [("in order to", 2)])

    def test_single_hedge_is_allowed_two_are_a_stack(self):
        self.assertEqual(cats("This is probably a cache issue."), [])
        hits = tc.check("I think this might be a cache issue.")["hits"]
        self.assertEqual([h["category"] for h in hits], ["hedge stack"])
        self.assertEqual(hits[0]["words"], 1)

    def test_sentence_wrapped_over_lines_is_joined(self):
        self.assertIn("hedge stack", cats("I think it\nmight be the cache.\n"))

    def test_list_items_are_separate_sentences(self):
        hits = tc.check("- Sure! Here it is\n- Overall, done.\n")["hits"]
        self.assertEqual([(h["category"], h["line"]) for h in hits], [("preamble", 1), ("recap", 2)])

    def test_preamble_only_at_sentence_start(self):
        self.assertEqual(cats("The fix is sure to work."), [])
        self.assertEqual(cats("Sure, run make test."), ["preamble"])

    def test_percentage(self):
        r = tc.check("Hope this helps! Run make.")
        self.assertEqual(r["prose_words"], 5)
        self.assertEqual(r["removable_words"], 3)
        self.assertEqual(r["removable_pct"], 60.0)

    def test_empty_text(self):
        self.assertEqual(tc.check("")["removable_pct"], 0.0)


class TestMustKeep(unittest.TestCase):
    def test_extracts_each_kind(self):
        keep = tc.must_keep(PADDED)
        self.assertIn("git mv db/migrations/0042_add_index.sql db/migrations/0043_add_index.sql",
                      "\n".join(keep["code block"]))
        self.assertIn("DROP TABLE orders", keep["inline code"])
        self.assertEqual(keep["url"], ["https://example.com/docs/migrations."[:-1]])
        self.assertIn("0040", keep["number"])
        self.assertIn("irreversible", keep["warning"])
        self.assertIn("back up", keep["warning"])

    def test_paths_found_outside_code(self):
        keep = tc.must_keep("Edit src/app/config.py and README.md, then ./run.sh.")
        self.assertIn("src/app/config.py", keep["path"])
        self.assertIn("README.md", keep["path"])
        self.assertIn("./run.sh", keep["path"])

    def test_error_lines(self):
        keep = tc.must_keep("It failed with ModuleNotFoundError: No module named 'yaml'.\n")
        self.assertEqual(keep["error line"], ["ModuleNotFoundError: No module named 'yaml'"])


class TestCompare(unittest.TestCase):
    def test_terse_version_keeps_everything(self):
        r = tc.compare(PADDED, TERSE)
        self.assertEqual(r["missing"], [])
        self.assertGreater(r["saved_words"], 30)

    def test_dropped_command_and_warning_are_reported(self):
        r = tc.compare(PADDED, "Rename the migration so it sorts after 0041.")
        kinds = {m["kind"] for m in r["missing"]}
        self.assertTrue({"code block", "inline code", "warning", "url"} <= kinds)

    def test_whitespace_changes_in_code_are_tolerated(self):
        before = "```\nfoo  --bar\n   baz\n```\n"
        self.assertEqual(tc.compare(before, "```\nfoo --bar\nbaz\n```\n")["missing"], [])

    def test_changed_number_is_missing(self):
        r = tc.compare("Set the timeout to 30s.", "Set the timeout to 60s.")
        self.assertEqual(r["missing"], [{"kind": "number", "item": "30s"}])

    def test_warning_match_is_case_insensitive(self):
        self.assertEqual(tc.compare("This is irreversible.", "IRREVERSIBLE.")["missing"], [])


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.padded = os.path.join(self.tmp, "padded.md")
        self.terse = os.path.join(self.tmp, "terse.md")
        with open(self.padded, "w") as fh:
            fh.write(PADDED)
        with open(self.terse, "w") as fh:
            fh.write(TERSE)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args, stdin=None):
        r = subprocess.run([sys.executable, SCRIPT, *args], input=stdin, capture_output=True, text=True)
        return r.returncode, r.stdout, r.stderr

    def test_exit_codes_for_check(self):
        self.assertEqual(self.run_cli(self.padded)[0], 1)
        self.assertEqual(self.run_cli(self.terse)[0], 0)
        self.assertEqual(self.run_cli(self.padded, "--max-pct", "90")[0], 0)

    def test_stdin_and_json(self):
        code, out, _ = self.run_cli("--json", stdin="Hope this helps!\n")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["by_category"], {"offer": 3})

    def test_compare_exit_codes(self):
        self.assertEqual(self.run_cli("--compare", self.padded, self.terse)[0], 0)
        code, out, _ = self.run_cli("--compare", self.terse, os.devnull)
        self.assertEqual(code, 1)
        self.assertIn("MISSING", out)

    def test_missing_file_is_exit_2(self):
        code, _, err = self.run_cli(os.path.join(self.tmp, "nope.md"))
        self.assertEqual(code, 2)
        self.assertIn("error", err)


if __name__ == "__main__":
    unittest.main()
