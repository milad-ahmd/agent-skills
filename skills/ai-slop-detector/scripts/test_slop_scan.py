#!/usr/bin/env python3
"""Tests for slop_scan.py, the pattern catalog and the benchmark data. Run: python3 -m unittest discover -s scripts"""
from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import slop_scan  # noqa: E402

SLOP = """In today's fast-paced digital world, remote work is more than just a trend. It's a revolution.

Let's dive in! Here's a quick breakdown of why it matters:

- **Flexibility:** Employees can work from anywhere, fostering a culture of trust.
- **Productivity:** Teams leverage cutting-edge tools to collaborate seamlessly.
- **Wellbeing:** It's important to note that commutes play a crucial role in stress.

Whether you're a startup founder, a manager, or an employee, remote work can unlock your full potential.
It's not about where you work. It's about how you work.

In conclusion, the future is bright. I hope this helps!
"""

PLAIN = """The bus to Leiden left at 7:40, which meant leaving the flat by 7:25 if the lift was working and 7:20 if it wasn't.
Most mornings it wasn't. I got used to the stairs. Four floors, sixty-two steps; I counted them in March and never
stopped counting. The driver on the early run knew everyone by their stop rather than their name. He called me
Station Road for two years. When the timetable changed in September the bus came ten minutes later, and for a week
half the regulars stood at the shelter at the old time, stamping their feet and saying nothing, as if the bus might
remember. It didn't. By October we had all adjusted and nobody mentioned it again.
"""


def run_cli(*args: str, stdin: str = "") -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    old_stdin = sys.stdin
    sys.stdin = io.StringIO(stdin)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = slop_scan.main(list(args))
    finally:
        sys.stdin = old_stdin
    return code, out.getvalue(), err.getvalue()


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(HERE, "patterns.json"), encoding="utf-8") as fh:
            cls.raw = json.load(fh)["patterns"]
        cls.pats = slop_scan.load_patterns()

    def test_size_and_fields(self):
        self.assertGreaterEqual(len(self.raw), 80)
        self.assertLessEqual(len(self.raw), 130)
        ids = [p["id"] for p in self.raw]
        self.assertEqual(len(ids), len(set(ids)), "duplicate pattern ids")
        cats = {"vocabulary", "filler", "opener", "closer", "contrast", "sycophancy", "signposting", "profundity", "structure"}
        for p in self.raw:
            for k in ("id", "category", "weight", "regex", "why", "instead", "example"):
                self.assertTrue(p.get(k) not in (None, ""), f"{p.get('id')}: missing {k}")
            self.assertIn(p["category"], cats, p["id"])
            self.assertRegex(p["id"], r"^[a-z0-9]+(-[a-z0-9]+)*$")
            self.assertTrue(0 < p["weight"] <= 3, p["id"])

    def test_words_weigh_less_than_structures(self):
        avg = lambda c: sum(p["weight"] for p in self.raw if p["category"] == c) / sum(p["category"] == c for p in self.raw)
        for heavier in ("contrast", "sycophancy", "structure", "profundity"):
            self.assertGreater(avg(heavier), avg("vocabulary"), heavier)

    def test_every_example_matches_its_pattern(self):
        for p in self.pats:
            ex = next(r["example"] for r in self.raw if r["id"] == p.id)
            hits = slop_scan.find_hits(ex.split("\n"), [p])
            self.assertTrue(hits, f"{p.id} does not match its example {ex!r}")

    def test_false_positive_guards(self):
        legit = [
            "We report robust standard errors clustered by firm.",
            "Financial leverage increases risk for shareholders.",
            "She grew up in foster care in Ohio.",
            "Patients with elevated blood pressure were excluded.",
            "Paramount Pictures released the film in 1994.",
            "The landscape of Yellowstone was shaped by glaciers.",
            "Water seeps through the bedrock beneath the basin.",
            "There is no other place like it in this world.",
            "The resonator will resonate at 440 Hz.",
            "Hurricanes are not just a coastal problem. Find out how rain could affect you.",
        ]
        for s in legit:
            hits = [h for h in slop_scan.find_hits([s], self.pats) if h["weight"] >= 1]
            self.assertEqual(hits, [], f"false positive on {s!r}: {[h['id'] for h in hits]}")

    def test_overlapping_matches_keep_heaviest(self):
        hits = slop_scan.find_hits(["Let's delve into it."], self.pats)
        self.assertEqual([h["id"] for h in hits], ["lets-dive-in"])

    def test_reference_doc_in_sync(self):
        r = subprocess.run([sys.executable, os.path.join(HERE, "gen_patterns_md.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


class CleaningTests(unittest.TestCase):
    def test_code_urls_front_matter_removed_and_lines_kept(self):
        md = "---\ntitle: Delve\n---\nIntro line.\n\n```python\nleverage = delve()\n```\nUse `seamless` here: https://delve.example/x\n"
        cleaned = slop_scan.clean_text(md)
        self.assertEqual(cleaned.count("\n"), md.count("\n"))
        self.assertNotIn("delve", cleaned.lower())
        self.assertNotIn("seamless", cleaned)
        self.assertIn("Intro line.", cleaned)

    def test_link_text_kept(self):
        self.assertIn("seamless", slop_scan.clean_text("A [seamless](https://x.y) thing"))

    def test_columns_preserved_after_inline_code(self):
        line = "Run `x` and delve in."
        hit = slop_scan.find_hits(slop_scan.clean_text(line).split("\n"), slop_scan.load_patterns())[0]
        self.assertEqual(line[hit["col"] - 1:hit["end_col"] - 1].lower(), "delve")


class ScoringTests(unittest.TestCase):
    def test_slop_scores_high_and_plain_scores_low(self):
        slop, plain = slop_scan.analyze(SLOP), slop_scan.analyze(PLAIN)
        self.assertGreaterEqual(slop["score"], slop_scan.THRESHOLD)
        self.assertEqual(slop["band"], "high")
        self.assertLess(plain["score"], slop_scan.LOW_BAND)
        self.assertEqual(plain["hits"], [])

    def test_score_bounds_and_weights(self):
        self.assertAlmostEqual(sum(slop_scan.WEIGHTS.values()), 1.0)
        for text in ("", "word " * 10, SLOP * 20, PLAIN):
            r = slop_scan.analyze(text)
            self.assertTrue(0 <= r["score"] <= 100)
            for c in r["components"].values():
                self.assertTrue(0 <= c["signal"] <= 1)

    def test_structural_signals(self):
        listy = "\n".join(f"- **Item {i}:** thing number {i} is here." for i in range(12))
        self.assertGreater(slop_scan.analyze(listy)["components"]["lists"]["signal"], 0.9)
        dashy = "It works — mostly — and it is fast — really fast. " * 10
        self.assertEqual(slop_scan.analyze(dashy)["components"]["dashes"]["signal"], 1.0)
        triads = "We need speed, clarity, and focus. " * 10
        self.assertGreater(slop_scan.analyze(triads)["components"]["triads"]["value"], 50)
        uniform = " ".join(["This sentence has exactly seven words in it."] * 12)
        self.assertEqual(slop_scan.analyze(uniform)["components"]["rhythm"]["signal"], 1.0)

    def test_short_text_not_maxed_by_one_word(self):
        self.assertLess(slop_scan.analyze("We delve into the plan for next week with the team.")["score"], slop_scan.THRESHOLD)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        os.makedirs(os.path.join(self.d, "docs", "sub"))
        os.makedirs(os.path.join(self.d, "node_modules"))
        for rel, text in {"docs/slop.md": SLOP, "docs/sub/plain.md": PLAIN, "docs/short.md": "Too short.",
                          "node_modules/x.md": SLOP, "notes.txt": PLAIN}.items():
            with open(os.path.join(self.d, rel), "w", encoding="utf-8") as fh:
                fh.write(text)
        self.cwd = os.getcwd()
        os.chdir(self.d)

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def test_text_output(self):
        code, out, _ = run_cli("docs/slop.md")
        self.assertEqual(code, 0)
        self.assertIn("slop-signal", out)
        self.assertIn("not evidence", out)

    def test_stdin_and_json(self):
        code, out, _ = run_cli("--json", "-", stdin=SLOP)
        self.assertEqual(code, 0)
        data = json.loads(out)
        r = data["results"][0]
        self.assertEqual(r["file"], "<stdin>")
        self.assertGreaterEqual(r["score"], slop_scan.THRESHOLD)
        self.assertTrue(all({"line", "col", "id", "why", "instead"} <= set(h) for h in r["hits"]))

    def test_globs_excludes_and_min_words(self):
        code, out, _ = run_cli("--json", "**/*.md", "--exclude", "docs/sub/**")
        files = {r["file"]: r for r in json.loads(out)["results"]}
        self.assertEqual(set(files), {os.path.join("docs", "slop.md"), os.path.join("docs", "short.md")})
        self.assertIn("min-words", files[os.path.join("docs", "short.md")]["skipped"])

    def test_directory_input(self):
        _, out, _ = run_cli("--json", "docs")
        self.assertEqual(len(json.loads(out)["results"]), 3)

    def test_fail_above(self):
        self.assertEqual(run_cli("--fail-above", "30", "docs/slop.md")[0], 1)
        self.assertEqual(run_cli("--fail-above", "30", "docs/sub/plain.md")[0], 0)
        self.assertEqual(run_cli("--fail-above", "0", "docs/short.md")[0], 0)  # skipped texts never fail

    def test_usage_errors(self):
        self.assertEqual(run_cli()[0], 2)
        self.assertEqual(run_cli("missing.md")[0], 2)
        self.assertEqual(run_cli("nothing/**/*.md")[0], 0)

    def test_github_format(self):
        summary = os.path.join(self.d, "summary.md")
        os.environ["GITHUB_STEP_SUMMARY"] = summary
        try:
            code, out, _ = run_cli("--format", "github", "--fail-above", "30", "docs/slop.md", "docs/sub/plain.md")
        finally:
            del os.environ["GITHUB_STEP_SUMMARY"]
        self.assertEqual(code, 1)
        lines = out.splitlines()
        warnings = [ln for ln in lines if ln.startswith("::warning ")]
        self.assertTrue(0 < len(warnings) <= 10)
        self.assertTrue(all(re.match(r"::warning file=docs/slop\.md,line=\d+,col=\d+,endColumn=\d+,title=", w) for w in warnings))
        self.assertTrue(any(ln.startswith("::error file=docs/slop.md,line=1,") for ln in lines))
        self.assertTrue(any(ln.startswith("::notice file=docs/sub/plain.md,line=1,") for ln in lines))
        with open(summary, encoding="utf-8") as fh:
            self.assertIn("| docs/slop.md |", fh.read())

    def test_github_stdin_has_no_file_property_and_is_escaped(self):
        code, out, _ = run_cli("--format", "github", "--stdin-name", "PR description", "-", stdin=SLOP)
        self.assertEqual(code, 0)
        cmds = [ln for ln in out.splitlines() if ln.startswith("::")]
        self.assertTrue(cmds)
        for c in cmds:
            self.assertNotIn("file=", c)
            self.assertNotIn("\n", c)
        self.assertIn("[PR description]", out)

    def test_escaping(self):
        self.assertEqual(slop_scan._esc_data("100%\nok"), "100%25%0Aok")
        self.assertEqual(slop_scan._esc_prop("a:b,c"), "a%3Ab%2Cc")

    def test_github_output_cannot_be_hijacked_by_input(self):
        evil = SLOP + "\n::error::injected\n%0A::warning::also injected\n"
        _, out, _ = run_cli("--format", "github", "--stdin-name", "x\n::error::name", "-", stdin=evil)
        for line in out.splitlines():
            self.assertFalse(line.startswith("::error"), line)
            if line.startswith("::"):
                self.assertRegex(line, r"^::(warning|notice) ")


class BenchDataTests(unittest.TestCase):
    def test_labels_files_and_sizes(self):
        bench = os.path.join(SKILL, "bench")
        with open(os.path.join(bench, "labels.csv"), newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        counts = {"human": 0, "ai": 0}
        for r in rows:
            path = os.path.join(bench, r["label"], r["id"] + ".txt")
            with open(path, encoding="utf-8") as fh:
                n = len(fh.read().split())
            self.assertTrue(120 <= n <= 400, f"{r['id']}: {n} words")
            self.assertIn(r["split"], ("dev", "test"))
            counts[r["label"]] += 1
        self.assertGreaterEqual(counts["human"], 25)
        self.assertGreaterEqual(counts["ai"], 25)
        total = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(bench) for f in fs)
        self.assertLess(total, 300_000)


if __name__ == "__main__":
    unittest.main()
