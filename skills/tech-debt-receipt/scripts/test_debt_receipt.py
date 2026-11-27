#!/usr/bin/env python3
"""Tests for debt_receipt.py (stdlib unittest, no network).

Run: python3 -m unittest discover -s skills/tech-debt-receipt/scripts -p 'test_*.py'
Each repo test builds a throwaway git repo with markers committed at known dates by known authors.
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
from debt_receipt import TICKET_RE, WIDTH, find_markers, price  # noqa: E402

SCRIPT = os.path.join(HERE, "debt_receipt.py")
ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", LC_ALL="C")
for _k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
    ENV.pop(_k, None)
NOW = "2026-01-01"


class Repo:
    def __init__(self, path):
        self.path = path
        os.makedirs(path)
        self.git("init", "-q")

    def git(self, *args, env=None):
        r = subprocess.run(["git", *args], cwd=self.path, env=env or ENV, capture_output=True, text=True)
        if r.returncode:
            raise AssertionError("git %s: %s" % (" ".join(args), r.stderr))
        return r.stdout

    def write(self, name, text):
        full = os.path.join(self.path, name)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb" if isinstance(text, bytes) else "w") as fh:
            fh.write(text)

    def commit(self, date, files, author="Ada Tester"):
        for name, text in files.items():
            self.write(name, text)
        email = author.split()[0].lower() + "@example.com"
        env = dict(ENV, GIT_AUTHOR_NAME=author, GIT_AUTHOR_EMAIL=email, GIT_COMMITTER_NAME=author,
                   GIT_COMMITTER_EMAIL=email, GIT_AUTHOR_DATE=date + "T12:00:00Z",
                   GIT_COMMITTER_DATE=date + "T12:00:00Z")
        self.git("add", "-A", env=env)
        self.git("commit", "-q", "-m", "change on " + date, env=env)

    def run(self, *args):
        r = subprocess.run([sys.executable, SCRIPT, "-C", self.path, "--now", NOW, *args], env=ENV,
                           capture_output=True, text=True, timeout=120)
        return r.returncode, r.stdout, r.stderr

    def report(self, *args):
        code, out, err = self.run("--json", *args)
        self_check = json.loads(out) if out else None
        return code, self_check


def kinds(text, path):
    return [(m["kind"], m["label"], m["blanket"]) for m in find_markers(text, path)]


class RepoCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Repo(os.path.join(self._tmp.name, "shop"))

    def tearDown(self):
        self._tmp.cleanup()

    def item(self, rep, path, line):
        return next(i for i in rep["items"] if i["path"] == path and i["line"] == line)


class Aging(RepoCase):
    def test_blame_dates_each_line_with_its_own_commit(self):
        self.repo.commit("2022-11-01", {"src/billing.py": "x = 1\n# FIXME: rounding for JPY\n"}, "Ada Tester")
        self.repo.commit("2025-12-02", {"src/billing.py": "x = 1\n# FIXME: rounding for JPY\n# TODO: euro\n"},
                         "Bob Builder")
        code, rep = self.repo.report()
        self.assertEqual(code, 0)
        old, new = self.item(rep, "src/billing.py", 2), self.item(rep, "src/billing.py", 3)
        self.assertEqual(old["date"], "2022-11-01")
        self.assertEqual(new["date"], "2025-12-02")
        self.assertEqual(old["age_days"], 1156)
        self.assertEqual(new["age_days"], 29)
        self.assertEqual(rep["contributors"], 2)

    def test_authors_hidden_unless_asked(self):
        self.repo.commit("2024-01-01", {"a.py": "# HACK: retry twice\n"}, "Ada Tester")
        code, out, _ = self.repo.run()
        self.assertNotIn("Ada", out)
        self.assertIn("contributors: 1 (names hidden)", out)
        _, rep = self.repo.report()
        self.assertNotIn("author", rep["items"][0])
        self.assertNotIn("authors", rep)
        code, out, _ = self.repo.run("--show-authors")
        self.assertIn("by Ada Tester", out)
        _, rep = self.repo.report("--show-authors")
        self.assertEqual(rep["authors"], {"Ada Tester": 1})

    def test_uncommitted_marker_is_aged_today(self):
        self.repo.commit("2024-01-01", {"a.py": "x = 1\n"})
        self.repo.write("a.py", "x = 1\n# TODO: new thing\n")
        _, rep = self.repo.report()
        it = rep["items"][0]
        self.assertEqual((it["age_days"], it["commit"]), (0, None))

    def test_max_blame_caps_aging_per_file(self):
        self.repo.commit("2023-01-01", {"a.py": "# TODO: one\n# TODO: two\n# TODO: three\n"})
        _, rep = self.repo.report("--max-blame", "2")
        self.assertEqual(rep["blame_capped_files"], ["a.py"])
        self.assertIsNone(self.item(rep, "a.py", 3)["age_days"])
        self.assertIsNotNone(self.item(rep, "a.py", 1)["age_days"])
        _, out, _ = self.repo.run("--max-blame", "2")
        self.assertIn("aging capped in 1 file(s)", out)


class Markers(unittest.TestCase):
    def test_strings_are_not_comments(self):
        py = 'msg = "TODO: not debt"\nurl = "http://x#y"  # FIXME real one\ns = \'# HACK\'\n'
        self.assertEqual(kinds(py, "a.py"), [("FIXME", "FIXME", False)])
        js = 'const a = "// TODO not debt";\nconst b = `/* FIXME */`;\nlet c = 1; // HACK: real\n'
        self.assertEqual(kinds(js, "a.js"), [("HACK", "HACK", False)])

    def test_python_docstring_is_a_string(self):
        self.assertEqual(kinds('def f():\n    """TODO: docstrings are not scanned"""\n', "a.py"), [])

    def test_block_comments_across_lines_and_other_syntaxes(self):
        self.assertEqual([m["line"] for m in find_markers("/*\n * FIXME: leak\n */\nint x;\n", "a.c")], [2])
        self.assertEqual(kinds("<p>hi</p>\n<!-- XXX: remove banner -->\n", "a.html"), [("XXX", "XXX", False)])
        self.assertEqual(kinds("SELECT 1; -- TODO: index this\n", "q.sql"), [("TODO", "TODO", False)])
        self.assertEqual(kinds("--[[ HACK: global ]]\nlocal x = 1\n", "a.lua"), [("HACK", "HACK", False)])
        self.assertEqual(kinds("FROM alpine\n# temporary pin until 3.20\n", "Dockerfile"),
                         [("TEMPORARY", "TEMPORARY", False)])

    def test_one_debt_item_per_line_most_expensive_kind(self):
        self.assertEqual(kinds("# TODO: temporary HACK around the cache\n", "a.py"), [("HACK", "HACK", False)])

    def test_mentions_and_prose_are_not_debt(self):
        self.assertEqual(kinds('# skips "TODO" and TODO/FIXME mentions\n# a todo list widget\n', "a.py"), [])
        self.assertEqual(kinds("# Clears any temporary state\n# temporarily disabled for the demo\n", "a.py"),
                         [("TEMPORARY", "TEMPORARY", False)])
        self.assertEqual(kinds("# todo: lowercase with a colon counts\n", "a.py"), [("TODO", "TODO", False)])

    def test_markdown_code_spans_are_examples(self):
        md = "Leave `<!-- TODO -->` here.\n```html\n<!-- FIXME in a fence -->\n```\n<!-- HACK: real -->\n"
        self.assertEqual(kinds(md, "README.md"), [("HACK", "HACK", False)])

    def test_python_suppressions(self):
        py = ("import os  # noqa\nimport sys  # noqa: F401\nx = f()  # type: ignore\ny = g()  # type: ignore[attr-defined]\n"
              "if z:  # pragma: no cover\nclass A:  # pylint: disable=all\n")
        self.assertEqual(kinds(py, "a.py"), [
            ("SUPPRESS", "noqa", True), ("SUPPRESS", "noqa", False), ("SUPPRESS", "type: ignore", True),
            ("SUPPRESS", "type: ignore", False), ("SUPPRESS", "pragma: no cover", False),
            ("SUPPRESS", "pylint: disable", True)])

    def test_js_ts_suppressions(self):
        ts = ("// eslint-disable-next-line\nfoo();\n// eslint-disable-next-line no-console -- debug\n"
              "/* eslint-disable */\n// @ts-ignore\n// @ts-expect-error: wrong lib types\n")
        self.assertEqual(kinds(ts, "a.ts"), [
            ("SUPPRESS", "eslint-disable", True), ("SUPPRESS", "eslint-disable", False),
            ("SUPPRESS", "eslint-disable", True), ("SUPPRESS", "@ts-ignore", False),
            ("SUPPRESS", "@ts-expect-error", False)])

    def test_go_java_rust_suppressions(self):
        go = "x := f() //nolint\ny := g() //nolint:errcheck\nz := h() // nolint is prose here\n"
        self.assertEqual(kinds(go, "a.go"), [("SUPPRESS", "nolint", True), ("SUPPRESS", "nolint", False)])
        java = '/** @deprecated use bar */\n@Deprecated\n@SuppressWarnings("unchecked")\nvoid foo() {}\n'
        self.assertEqual(kinds(java, "A.java"), [("DEPRECATED", "@deprecated", False),
                                                 ("SUPPRESS", "@SuppressWarnings", False)])
        rs = "#![allow(warnings)]\n#[allow(dead_code)]\nfn f<'a>(x: &'a str) {} // TODO: lifetimes\n"
        self.assertEqual(kinds(rs, "lib.rs"), [("SUPPRESS", "#[allow]", True), ("SUPPRESS", "#[allow]", False),
                                               ("TODO", "TODO", False)])
        self.assertEqual(kinds("/** @deprecated since 2.0 */\nexport function f() {}\n", "a.js"),
                         [("DEPRECATED", "@deprecated", False)])

    def test_ticket_detection(self):
        for text in ("TODO(#123): x", "FIXME JIRA-45 rounding", "see https://example.com/issues/9", "HACK gh-12"):
            self.assertTrue(TICKET_RE.search(text), text)
        for text in ("TODO: handle UTF-8 input", "FIXME: SHA-256 is slow", "see XEP-0156", "TODO(alice): later", "&#123;"):
            self.assertFalse(TICKET_RE.search(text), text)

    def test_price_formula(self):
        it = {"kind": "FIXME", "label": "FIXME", "blanket": False, "age_days": 730.5, "churn_180d": 5}
        self.assertEqual(price(it), round(8 * 3 * 1.5, 2))
        it.update(kind="SUPPRESS", label="noqa", blanket=True, age_days=0, churn_180d=99)
        self.assertEqual(price(it), 4 * 1 * 3 * 2)  # churn multiplier caps at 3


class Scanning(RepoCase):
    def test_skips_lockfiles_vendored_generated_minified_binary_and_ignored(self):
        self.repo.commit("2024-01-01", {
            "app.py": "# TODO: real\n",
            "package-lock.json": '{"x": 1} // TODO\n',
            "vendor/lib.py": "# FIXME vendored\n",
            "node_modules/x/index.js": "// FIXME dep\n",
            "web/app.min.js": "// HACK minified\n",
            "api_pb2.py": "# TODO generated by name\n",
            "gen/models.go": "// Code generated by sqlc. DO NOT EDIT.\n// TODO: x\n",
            "img.py": b"# TODO\x00\x01binary",
            ".gitignore": "secret.py\n",
        })
        self.repo.write("secret.py", "# FIXME ignored and untracked\n")
        code, rep = self.repo.report()
        self.assertEqual(code, 0)
        self.assertEqual([i["path"] for i in rep["items"]], ["app.py"])
        skipped = rep["files_skipped"]
        for why in ("lockfile", "vendored", "generated", "binary"):
            self.assertIn(why, skipped)

    def test_path_filter(self):
        self.repo.commit("2024-01-01", {"a/x.py": "# TODO a\n", "b/y.py": "# TODO b\n"})
        _, rep = self.repo.report("a")
        self.assertEqual([i["path"] for i in rep["items"]], ["a/x.py"])

    def test_churn_and_ticket_discount(self):
        self.repo.commit("2023-01-01", {"hot.py": "# FIXME: race JIRA-7\n"})
        for d in ("2025-09-01", "2025-10-01", "2025-11-01"):
            self.repo.commit(d, {"hot.py": "# FIXME: race JIRA-7\n" + "x = 1\n" * int(d[5:7])})
        _, rep = self.repo.report()
        it = rep["items"][0]
        self.assertEqual(it["churn_180d"], 3)
        self.assertEqual(it["ticket"], "JIRA-7")
        self.assertAlmostEqual(it["price"], it["full_price"] / 2, places=2)
        self.assertEqual(rep["tracked"], 1)
        self.assertAlmostEqual(rep["total"], rep["subtotal"] - rep["ticket_discount"], places=2)

    def test_pay_first_ranks_old_untracked_fixme_in_hot_file_over_fresh_todo(self):
        self.repo.commit("2021-06-01", {"core.py": "# FIXME: double charge\n", "util.py": "x = 1\n"})
        self.repo.commit("2025-12-20", {"core.py": "# FIXME: double charge\ny = 2\n",
                                        "util.py": "x = 1\n# TODO: rename\n"})
        _, rep = self.repo.report("--top", "2")
        first = rep["pay_first"][0]
        self.assertEqual((first["path"], first["kind"]), ("core.py", "FIXME"))
        self.assertIn("no ticket", first["reasons"])
        self.assertTrue(any("old" in r for r in first["reasons"]))
        self.assertEqual(len(rep["pay_first"]), 2)


class Output(RepoCase):
    def setUp(self):
        super().setUp()
        self.repo.commit("2022-03-15", {
            "src/very/deeply/nested/package/module/billing_service_impl.py": "# FIXME: JPY rounding\n",
            "web/app.ts": "// @ts-ignore\nfoo();\n// TODO(#88): drop polyfill\n// eslint-disable-next-line\n",
        })

    def test_receipt_width_and_totals(self):
        code, out, _ = self.repo.run("--items", "0")
        self.assertEqual(code, 0)
        receipt = out.split("\n\nPAY THESE FIRST")[0].splitlines()
        self.assertTrue(all(len(line) <= WIDTH for line in receipt), [l for l in receipt if len(l) > WIDTH])
        _, rep = self.repo.report()
        kind_sum = round(sum(k["price"] for k in rep["by_kind"].values()), 2)
        self.assertAlmostEqual(kind_sum, rep["subtotal"], places=2)
        self.assertIn("TOTAL DEBT", out)
        self.assertIn("$%.2f" % rep["total"], out)
        self.assertIn("TICKET DISCOUNT (1 tracked)", out)
        self.assertIn("price = base x (1 + age in years)", out)
        self.assertIn("PAY THESE FIRST", out)

    def test_points_currency(self):
        _, out, _ = self.repo.run("--currency", "points")
        self.assertIn(" pts", out)
        self.assertNotIn("$", out.split("PAY THESE FIRST")[0])

    def test_svg_is_well_formed(self):
        path = os.path.join(self._tmp.name, "receipt.svg")
        code, out, _ = self.repo.run("--svg", path)
        self.assertEqual(code, 0)
        root = ET.parse(path).getroot()
        self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
        texts = [(t.text or "").replace("\u00a0", " ") for t in root.iter("{http://www.w3.org/2000/svg}text")]
        self.assertTrue(any("TOTAL DEBT" in t for t in texts))
        # the receipt plus a blank line and the two credit lines
        self.assertEqual(len(texts), len(out.split("\n\nPAY THESE FIRST")[0].splitlines()) + 3)
        self.assertIn("github.com/milad-ahmd/agent-skills", texts[-1])
        self.repo.run("--svg", path, "--no-credit")
        with open(path, encoding="utf-8") as fh:
            self.assertNotIn("milad-ahmd/agent-skills", fh.read())

    def test_fail_over_exit_codes(self):
        _, rep = self.repo.report()
        self.assertEqual(self.repo.run("--fail-over", str(rep["total"] + 1))[0], 0)
        code, _, err = self.repo.run("--fail-over", "1")
        self.assertEqual(code, 1)
        self.assertIn("over the budget", err)


class ExitCodes(unittest.TestCase):
    def test_not_a_git_repo(self):
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run([sys.executable, SCRIPT, "-C", d], env=ENV, capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)
            self.assertIn("not a git repository", r.stderr)

    def test_nothing_scannable(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Repo(os.path.join(d, "r"))
            repo.commit("2024-01-01", {"yarn.lock": "# TODO\n", "logo.png": b"\x89PNG\x00"})
            self.assertEqual(repo.run()[0], 2)

    def test_clean_repo_prints_empty_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Repo(os.path.join(d, "r"))
            repo.commit("2024-01-01", {"a.py": "x = 1\n"})
            code, out, _ = repo.run()
            self.assertEqual(code, 0)
            self.assertIn("nothing owed", out)


if __name__ == "__main__":
    unittest.main()
