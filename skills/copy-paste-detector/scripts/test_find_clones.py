#!/usr/bin/env python3
"""Tests for find_clones.py (stdlib unittest, no network).

Run: python3 -m unittest discover -s skills/copy-paste-detector/scripts -p 'test_*.py'
Each test writes a few small source files to a temp dir and checks the families the scanner reports.
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
from find_clones import Source, analyse  # noqa: E402

SCRIPT = os.path.join(HERE, "find_clones.py")

BLOCK = """def total_price(order, tax_rate):
    subtotal = sum(item.price * item.quantity for item in order.items)
    discount = order.coupon.amount if order.coupon else 0
    taxed = (subtotal - discount) * (1 + tax_rate)
    if taxed < 0:
        raise ValueError("negative total for order %s" % order.id)
    shipping = 0 if subtotal > 100 else order.shipping_fee
    return round(taxed + shipping, 2)
"""
RENAMED = BLOCK.replace("total_price", "invoice_amount").replace("order", "cart").replace("subtotal", "base") \
    .replace("100", "250").replace("negative total", "bad amount")
OTHER = """def parse_args(argv):
    parser = make_parser()
    opts = parser.parse(argv)
    return opts
"""


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def other(n):
    """Distinct filler so files are not identical outside the block."""
    return "".join("def helper_%d_%d(x):\n    return x * %d + %d\n\n" % (n, i, i, n) for i in range(3))


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        full = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)

    def scan(self, **kw):
        return analyse(self.root, **kw)

    def run_cli(self, *args):
        r = subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout, r.stderr

    def exact(self, rep):
        return [d for d in rep["families"] if d["tier"] == "exact"]

    def renamed(self, rep):
        return [d for d in rep["families"] if d["tier"] == "renamed"]


class ExactClones(Tmp):
    def test_exact_copy_in_two_files(self):
        self.write("a.py", other(1) + BLOCK)
        self.write("b.py", BLOCK + other(2))
        fams = self.exact(self.scan())
        self.assertEqual(len(fams), 1)
        d = fams[0]
        self.assertEqual((d["copies"], d["lines_per_copy"], d["removable_lines"]), (2, 8, 8))
        spans = sorted((m["file"], m["start"], m["end"]) for m in d["members"])
        self.assertEqual(spans, [("a.py", 10, 17), ("b.py", 1, 8)])
        self.assertEqual({m["status"] for m in d["members"]}, {"same"})

    def test_comments_and_whitespace_do_not_matter(self):
        noisy = BLOCK.replace("    subtotal =", "    # sum it up\n\n    subtotal  =").replace(", 2)", ",   2)  # cents")
        self.write("a.py", BLOCK)
        self.write("b.py", noisy)
        self.assertEqual(len(self.exact(self.scan())), 1)

    def test_three_copies_and_headline_picks(self):
        for n in range(3):
            self.write("m%d.py" % n, other(n) + BLOCK)
        rep = self.scan()
        d = self.exact(rep)[0]
        self.assertEqual((d["copies"], d["removable_lines"], d["duplicated_lines"]), (3, 16, 24))
        self.assertEqual(rep["most_copied"], d["id"])
        self.assertEqual(rep["exact"]["duplicated_lines"], 24)
        self.assertAlmostEqual(rep["exact"]["percent"], round(100.0 * 24 / rep["code_lines"], 1))

    def test_block_copied_many_times(self):
        for n in range(70):      # more copies than MAX_BUCKET, so pairing takes the shortcut
            self.write("svc/m%02d.py" % n, other(n) + BLOCK)
        d = self.exact(self.scan())[0]
        self.assertEqual((d["copies"], d["lines_per_copy"]), (70, 8))

    def test_copy_inside_one_file(self):
        self.write("a.py", BLOCK + other(1) + BLOCK.replace("total_price", "total_price_v2"))
        d = self.exact(self.scan())[0]
        self.assertTrue(d["same_file"])
        self.assertEqual(d["copies"], 2)

    def test_tests_only_tag(self):
        self.write("tests/test_a.py", BLOCK)
        self.write("pkg/b_test.py", BLOCK)
        self.assertTrue(self.exact(self.scan())[0]["tests_only"])


class RenamedAndDrift(Tmp):
    def test_renamed_copy_is_second_tier(self):
        self.write("a.py", BLOCK)
        self.write("b.py", RENAMED)
        rep = self.scan()
        self.assertEqual(self.exact(rep), [])
        fams = self.renamed(rep)
        self.assertEqual(len(fams), 1)
        self.assertEqual(sorted(m["status"] for m in fams[0]["members"]), ["renamed", "same"])
        self.assertEqual(rep["exact"]["percent"], 0.0)
        self.assertGreater(rep["renamed"]["extra_lines"], 0)

    def test_renamed_tier_skips_what_exact_already_covers(self):
        self.write("a.py", BLOCK)
        self.write("b.py", BLOCK)
        self.assertEqual(self.renamed(self.scan()), [])

    def test_drifted_copy_is_one_member(self):
        long_block = BLOCK + "\n".join("    log_%d = audit(order, step=%d, label='s%d')" % (i, i, i) for i in range(8))
        drifted = long_block.replace("    shipping = 0 if subtotal > 100 else order.shipping_fee\n",
                                     "    shipping = carrier.quote(order.address, order.weight)\n")
        self.write("a.py", long_block)
        self.write("b.py", drifted)
        fams = self.exact(self.scan())
        self.assertEqual(len(fams), 1)
        statuses = sorted((m["status"], m["lines_differ"]) for m in fams[0]["members"])
        self.assertEqual(statuses, [("drifted", 1), ("same", 0)])
        self.assertEqual(fams[0]["drifted"], 1)

    def test_table_rows_are_not_renamed_clones(self):
        rows = lambda tag: "CASES = [\n" + "".join('    ("%s-%d", "allow", %d, None),\n' % (tag, i, i)
                                                    for i in range(12)) + "]\n"
        self.write("a.py", rows("rm"))
        self.write("b.py", rows("git"))
        self.assertEqual(self.scan()["families"], [])


class Noise(Tmp):
    def test_below_min_lines(self):
        self.write("a.py", OTHER)
        self.write("b.py", OTHER)
        self.assertEqual(self.scan()["families"], [])
        self.assertEqual(len(self.scan(min_lines=3, min_tokens=0)["families"]), 1)

    def test_min_tokens_drops_tiny_lines(self):
        tiny = "".join("x%d = 1\n" % i for i in range(10))
        self.write("a.py", tiny)
        self.write("b.py", tiny)
        self.assertEqual(self.scan()["families"], [])
        self.assertEqual(len(self.scan(min_tokens=0)["families"]), 1)

    def test_braces_and_imports_never_match(self):
        braces = "function f%d() {\n  if (a) {\n    if (b) {\n      go();\n    }\n  }\n}\n" + "}\n" * 3 + "});\n" * 8
        self.write("a.js", braces % 1)
        self.write("b.js", braces % 2)
        imports = "".join("import mod_%d\n" % i for i in range(10)) + "from x import (\n    a,\n)\n"
        self.write("c.py", imports)
        self.write("d.py", imports)
        go = 'package main\n\nimport (\n' + "".join('\t"pkg/mod%d"\n' % i for i in range(10)) + ")\n"
        self.write("e.go", go)
        self.write("f.go", go)
        self.assertEqual(self.scan(min_tokens=0)["families"], [])

    def test_repetitive_lines_are_not_a_self_copy(self):
        self.write("a.py", "".join("register(app, handler, prefix='/api', retries=3, timeout=30)\n" for _ in range(30)))
        self.assertEqual(self.scan()["families"], [])


class Tokenizer(unittest.TestCase):
    def test_comment_markers_inside_strings_are_kept(self):
        s = Source("a.py", 'url = "http://x/#frag"  # comment\nq = a // b\n')
        self.assertEqual(s.exact, ['url = "http://x/#frag"', "q = a / / b"])

    def test_js_block_comments_and_multiline_strings(self):
        s = Source("a.ts", "/* header\n spans */\nconst a = `two\nlines`; // tail\nlet b = 1;\n")
        self.assertEqual(s.exact, ["const a = `two\nlines` ;", "let b = 1 ;"])
        self.assertEqual((s.first, s.last), ([3, 5], [4, 5]))
        self.assertEqual(s.code_lines, [3, 5])

    def test_renamed_form(self):
        s = Source("a.go", 'func Add(a int, b int) int { return a + b + 42 + len("x") }\n')
        self.assertEqual(s.renamed, ["func I ( I int , I int ) int { return I + I + N + I ( S ) }"])


class Files(Tmp):
    def test_vendored_generated_and_fixtures_skipped(self):
        for rel in ("node_modules/x/a.js", "dist/a.js", "tests/fixtures/a.py", "db/migrations/a.py", "lib/a.min.js",
                    "api/a.pb.go", ".venv/lib/a.py", "build/a.py"):
            self.write(rel, BLOCK)
        self.write("src/real.py", BLOCK)
        self.assertEqual(self.scan()["files"], 1)

    def test_exclude_and_ext(self):
        self.write("src/a.py", BLOCK)
        self.write("src/gen/b.py", BLOCK)
        self.write("src/c.txt", BLOCK)
        self.assertEqual(self.scan(excludes=["src/gen/*"])["files"], 1)
        self.assertEqual(self.scan(exts=["txt"])["files"], 1)


class Cli(Tmp):
    def test_exit_codes(self):
        self.write("a.py", BLOCK)
        self.assertEqual(self.run_cli(self.root)[0], 0)
        self.write("b.py", BLOCK)
        code, out, _ = self.run_cli(self.root)
        self.assertEqual(code, 1)
        self.assertIn("this block exists 2 times", out)
        self.assertEqual(self.run_cli(self.root, "--fail-over", "100")[0], 0)
        code, _, err = self.run_cli(self.root, "--fail-over", "5")
        self.assertEqual(code, 1)
        self.assertIn("over the 5.0% budget", err)

    def test_bad_input_exits_2(self):
        self.assertEqual(self.run_cli(os.path.join(self.root, "missing"))[0], 2)
        self.assertEqual(self.run_cli(self.root)[0], 2)          # no source files
        self.write("a.py", BLOCK)
        self.assertEqual(self.run_cli(self.root, "--min-lines", "1")[0], 2)
        self.assertEqual(self.run_cli(self.root, "--fail-over", "lots")[0], 2)

    def test_json_output(self):
        self.write("a.py", BLOCK)
        self.write("b.py", RENAMED)
        code, out, _ = self.run_cli(self.root, "--json")
        rep = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual(set(rep) >= {"files", "code_lines", "seconds", "exact", "renamed", "families"}, True)
        self.assertEqual(rep["families"][0]["tier"], "renamed")

    def test_svg_hides_paths_unless_asked(self):
        self.write("billing_service.py", BLOCK)
        self.write("legacy_invoice.py", BLOCK)
        card = os.path.join(self.root, "card.svg")
        self.run_cli(self.root, "--svg", card)
        text = read(card)
        ET.fromstring(text)
        self.assertNotIn("billing_service", text)
        self.assertIn("2x", text)
        self.assertIn("milad-ahmd/agent-skills", text)
        self.run_cli(self.root, "--svg", card, "--show-paths")
        self.assertIn("billing_service.py", read(card))


if __name__ == "__main__":
    unittest.main()
