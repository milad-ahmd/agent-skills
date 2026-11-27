#!/usr/bin/env python3
"""Tests for find_gaps.py (stdlib unittest, no network).

Run: python3 -m unittest discover -s skills/test-gap-hunter/scripts -p 'test_*.py'
Each test builds a throwaway git repo with commits at fixed dates, optionally writes a Cobertura or
LCOV report into it, and checks churn, test pairing, coverage parsing, skipping and exit codes.
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
import find_gaps as fg  # noqa: E402

SCRIPT = os.path.join(HERE, "find_gaps.py")
BASE_ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", LC_ALL="C", TZ="UTC")
for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
    BASE_ENV.pop(var, None)
NOW = "2026-11-01"
DEV = ("Robin Example", "robin@example.com")

PY_SRC = "def add(a, b):\n    return a + b\n\n\ndef div(a, b):\n    if b == 0:\n        raise ValueError\n    return a / b\n"


class Repo:
    def __init__(self, root):
        self.path = os.path.join(root, "repo")
        os.makedirs(self.path)
        self.git("init", "-q", "-b", "main")

    def git(self, *args, env=None):
        r = subprocess.run(["git", *args], cwd=self.path, env=env or BASE_ENV, capture_output=True, text=True)
        if r.returncode:
            raise AssertionError("git %s: %s" % (" ".join(args), r.stderr))
        return r.stdout

    def write(self, rel, text):
        full = os.path.join(self.path, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)
        return full

    def commit(self, date, files):
        for rel, text in files.items():
            self.write(rel, text)
        stamp = date + "T12:00:00Z"
        env = dict(BASE_ENV, GIT_AUTHOR_NAME=DEV[0], GIT_AUTHOR_EMAIL=DEV[1], GIT_COMMITTER_NAME=DEV[0],
                   GIT_COMMITTER_EMAIL=DEV[1], GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
        self.git("add", "-A", env=env)
        self.git("commit", "-q", "-m", "change on %s" % date, env=env)

    def run(self, *args):
        return subprocess.run([sys.executable, SCRIPT, "-C", self.path, "--now", NOW, *args], env=BASE_ENV,
                              capture_output=True, text=True, timeout=120)

    def report(self, *args):
        r = self.run("--json", *args)
        if r.returncode:
            raise AssertionError(r.stderr)
        return json.loads(r.stdout)


def by_path(rep):
    return {f["path"]: f for f in rep["files"]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repo(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()


class Naming(unittest.TestCase):
    def test_test_stem_per_language(self):
        cases = {"tests/test_parser.py": "parser", "pkg/parser_test.py": "parser", "src/cart.test.ts": "cart",
                 "src/cart.spec.jsx": "cart", "src/__tests__/cart.js": "cart", "api/handler_test.go": "handler",
                 "src/test/java/a/OrderServiceTest.java": "OrderService", "a/TestOrder.java": "Order",
                 "a/OrderIT.java": "Order", "spec/models/user_spec.rb": "user", "src/parser.py": None,
                 "a/Testimony.java": None, "src/cart.ts": None}
        for path, want in cases.items():
            self.assertEqual(fg.test_stem(path), want, path)

    def test_rough_js_function_count(self):
        js = ("export function a(x) { return x }\nconst b = (y) => y * 2;\nclass K {\n  async load(id) {\n"
              "    if (id) { return 1 }\n    for (const z of y) { }\n  }\n  get name() { return 1 }\n}\n")
        self.assertEqual(fg.shape("src/k.ts", js)[0], 4)

    def test_skip_reasons(self):
        cases = {"tests/fixtures/x.py": "test", "app/fixtures/seed.py": "fixture", "app/migrations/0001_init.py":
                 "migration", "db/migrate/001_users.rb": "migration", "api/v1/user_pb2.py": "generated",
                 "node_modules/lib/index.js": "vendored", "vite.config.ts": "config", "conftest.py": "test",
                 "src/types.d.ts": "generated", "src/billing.py": None}
        for path, want in cases.items():
            self.assertEqual(fg.skip_reason(path), want, path)


class Ranking(Base):
    def test_untested_churning_file_ranks_first_as_guess(self):
        self.repo.commit("2026-10-01", {"app/billing.py": PY_SRC, "app/auth.py": PY_SRC,
                                        "tests/test_auth.py": "def test_x():\n    pass\n"})
        self.repo.commit("2026-10-05", {"app/billing.py": PY_SRC + "\n", "app/auth.py": PY_SRC + "\n"})
        rep = self.repo.report()
        self.assertIsNone(rep["coverage_report"])
        top = rep["files"][0]
        self.assertEqual((top["path"], top["churn"], top["untested"], top["risk"]), ("app/billing.py", 2, 1.0, 2.0))
        self.assertEqual(top["evidence"], "naming-guess")
        auth = by_path(rep)["app/auth.py"]
        self.assertEqual((auth["tests"], auth["risk"]), (["tests/test_auth.py"], 0.0))

    def test_churn_window_respects_now_and_since_days(self):
        self.repo.commit("2025-01-01", {"a.py": PY_SRC})
        self.repo.commit("2026-09-01", {"a.py": PY_SRC + "\n"})
        self.repo.commit("2026-10-20", {"a.py": PY_SRC + "\n\n"})
        self.repo.commit("2026-11-20", {"a.py": PY_SRC + "\n\n\n"})  # after --now
        self.assertEqual(by_path(self.repo.report())["a.py"]["churn"], 2)
        self.assertEqual(by_path(self.repo.report("--since-days", "30"))["a.py"]["churn"], 1)

    def test_go_tests_pair_only_in_same_directory(self):
        self.repo.commit("2026-10-01", {"api/handler.go": "package api\nfunc A() {}\nfunc B() {}\n",
                                        "web/handler.go": "package web\nfunc C() {}\n",
                                        "web/handler_test.go": "package web\n"})
        files = by_path(self.repo.report())
        self.assertEqual(files["api/handler.go"]["tests"], [])
        self.assertEqual(files["web/handler.go"]["tests"], ["web/handler_test.go"])
        self.assertEqual(files["api/handler.go"]["functions"], 2)

    def test_java_pairs_through_src_test_mirror(self):
        body = "class OrderService { }\n"
        self.repo.commit("2026-10-01", {"src/main/java/shop/OrderService.java": body,
                                        "src/main/java/other/OrderService.java": body,
                                        "src/test/java/shop/OrderServiceTest.java": body})
        files = by_path(self.repo.report())
        self.assertEqual(files["src/main/java/shop/OrderService.java"]["tests"],
                         ["src/test/java/shop/OrderServiceTest.java"])
        self.assertEqual(files["src/main/java/other/OrderService.java"]["tests"], [])

    def test_shared_stem_pairs_with_nearest_source(self):
        self.repo.commit("2026-10-01", {"one/scripts/code_map.py": PY_SRC, "two/scripts/code_map.py": PY_SRC,
                                        "two/scripts/test_code_map.py": "def test_x():\n    pass\n"})
        files = by_path(self.repo.report())
        self.assertEqual(files["one/scripts/code_map.py"]["tests"], [])
        self.assertEqual(files["two/scripts/code_map.py"]["tests"], ["two/scripts/test_code_map.py"])

    def test_skips_tests_fixtures_and_empty_python(self):
        self.repo.commit("2026-10-01", {"app/core.py": PY_SRC, "app/__init__.py": "", "app/consts.py": "X = 1\n",
                                        "app/fixtures/data.py": PY_SRC, "app/migrations/0001.py": PY_SRC,
                                        "README.md": "# hi\n"})
        rep = self.repo.report()
        self.assertEqual([f["path"] for f in rep["files"]], ["app/core.py"])
        self.assertEqual(rep["skipped"], {"fixture": 1, "migration": 1, "no functions": 2})

    def test_unparseable_python_is_kept_with_unknown_function_count(self):
        self.repo.commit("2026-10-01", {"app/new.py": "def broken(:\n    pass\n"})
        f = by_path(self.repo.report())["app/new.py"]
        self.assertIsNone(f["functions"])
        self.assertFalse(f["parsed"])

    def test_paths_limit_scope(self):
        self.repo.commit("2026-10-01", {"app/a.py": PY_SRC, "lib/b.py": PY_SRC})
        self.assertEqual([f["path"] for f in self.repo.report("lib")["files"]], ["lib/b.py"])
        self.assertEqual([f["path"] for f in self.repo.report("./lib/")["files"]], ["lib/b.py"])
        self.assertEqual(len(self.repo.report(".")["files"]), 2)

    def test_guess_lists_biggest_functions_first(self):
        self.repo.commit("2026-10-01", {"app/core.py": PY_SRC})
        uf = self.repo.report()["files"][0]["untested_functions"]
        self.assertEqual([f["name"] for f in uf], ["div", "add"])
        self.assertEqual(uf[0]["branches"], 1)
        self.assertIsNone(uf[0]["missed"])

    def test_text_report_says_guess_and_uses_relative_paths(self):
        self.repo.commit("2026-10-01", {"app/core.py": PY_SRC})
        r = self.repo.run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("naming-convention GUESS", r.stdout)
        self.assertIn("app/core.py", r.stdout)
        self.assertNotIn(self.tmp.name, r.stdout)


COBERTURA = """<?xml version="1.0" ?>
<!DOCTYPE coverage SYSTEM "http://cobertura.sourceforge.net/xml/coverage-04.dtd">
<coverage version="7.6.1" timestamp="1" lines-valid="9" lines-covered="5" line-rate="0.5556">
  <sources><source>{src}</source></sources>
  <packages><package name="app" line-rate="0.5">
    <classes>
      <class name="billing.py" filename="app/billing.py" complexity="0" line-rate="0.5">
        <methods/>
        <lines>
          <line number="1" hits="1"/><line number="2" hits="0"/><line number="5" hits="1"/>
          <line number="6" hits="0"/><line number="7" hits="0"/><line number="8" hits="0"/>
        </lines>
      </class>
      <class name="auth.py" filename="app/auth.py" complexity="0" line-rate="1">
        <methods/>
        <lines><line number="1" hits="3"/><line number="2" hits="3"/></lines>
      </class>
      <class name="gone.py" filename="app/gone.py" line-rate="0"><methods/><lines/></class>
    </classes>
  </package></packages>
</coverage>
"""

JAVA_COBERTURA = """<coverage line-rate="0.5"><sources><source>src/main/java</source></sources><packages><package name="shop">
<classes>
  <class name="shop.Cart" filename="shop/Cart.java" line-rate="0.5">
    <methods><method name="total" signature="()I"><lines><line number="3" hits="0"/></lines></method></methods>
    <lines><line number="2" hits="1"/><line number="3" hits="0"/></lines>
  </class>
  <class name="shop.Cart$Item" filename="shop/Cart.java" line-rate="1">
    <methods/><lines><line number="9" hits="2"/><line number="10" hits="2"/></lines>
  </class>
</classes></package></packages></coverage>
"""


class Coverage(Base):
    def setUp(self):
        super().setUp()
        self.repo.commit("2026-10-01", {"app/billing.py": PY_SRC, "app/auth.py": PY_SRC, "app/report.py": PY_SRC})
        self.repo.commit("2026-10-02", {"app/billing.py": PY_SRC + "\n", "app/auth.py": PY_SRC + "\n"})

    def test_cobertura_rates_and_risk(self):
        report = self.repo.write("out/coverage.xml", COBERTURA.format(src=self.repo.path))
        rep = self.repo.report("--coverage", report)
        c = rep["coverage_report"]
        self.assertEqual((c["format"], c["path"], c["files_matched"], c["entries_unmatched"]),
                         ("cobertura", "out/coverage.xml", 2, 1))
        files = by_path(rep)
        self.assertEqual((files["app/billing.py"]["coverage"], files["app/billing.py"]["evidence"]), (0.333, "coverage"))
        self.assertEqual(files["app/billing.py"]["risk"], 1.33)
        self.assertEqual(files["app/auth.py"]["risk"], 0.0)

    def test_file_missing_from_report_falls_back_to_guess(self):
        report = self.repo.write("out/coverage.xml", COBERTURA.format(src=self.repo.path))
        f = by_path(self.repo.report("--coverage", report))["app/report.py"]
        self.assertEqual((f["evidence"], f["coverage"], f["untested"]), ("naming-guess", None, 1.0))

    def test_uncovered_functions_from_line_hits(self):
        report = self.repo.write("out/coverage.xml", COBERTURA.format(src=self.repo.path))
        top = self.repo.report("--coverage", report)["files"][0]
        self.assertEqual(top["path"], "app/billing.py")
        self.assertEqual([(f["name"], f["missed"]) for f in top["untested_functions"]], [("div", 3), ("add", 1)])

    def test_cobertura_ignores_method_lines_and_merges_classes(self):
        self.repo.commit("2026-10-03", {"src/main/java/shop/Cart.java": "class Cart {}\n"})
        report = self.repo.write("coverage.xml", JAVA_COBERTURA)
        f = by_path(self.repo.report("--coverage", report))["src/main/java/shop/Cart.java"]
        self.assertEqual(f["coverage"], 0.75)

    def test_lcov_absolute_paths_summed_records_and_lf_fallback(self):
        billing = os.path.join(self.repo.path, "app", "billing.py")
        lcov = ("TN:unit\nSF:%s\nDA:1,1\nDA:2,0\nLF:2\nLH:1\nend_of_record\n"
                "TN:integration\nSF:%s\nDA:2,4\nend_of_record\n"
                "KF:app/auth.py\nLF:4\nLH:1\nend_of_record\n") % (billing, billing)
        report = self.repo.write("coverage/lcov.info", lcov)
        files = by_path(self.repo.report("--coverage", report))
        self.assertEqual(files["app/billing.py"]["coverage"], 1.0)
        self.assertEqual(files["app/auth.py"]["coverage"], 0.25)

    def test_entry_without_instrumented_lines_counts_as_covered(self):
        report = self.repo.write("lcov.info", "SF:app/auth.py\nLF:0\nLH:0\nend_of_record\n")
        f = by_path(self.repo.report("--coverage", report))["app/auth.py"]
        self.assertEqual((f["evidence"], f["coverage"], f["risk"]), ("coverage", 1.0, 0.0))

    def test_relative_cobertura_source_resolves_against_report_dir(self):
        self.repo.commit("2026-10-03", {"svc/src/app/billing.py": PY_SRC})
        xml = COBERTURA.format(src="src")  # coverage.py run inside svc/ writes a relative <source>
        report = self.repo.write("svc/coverage.xml", xml)
        rep = self.repo.report("--coverage", report)
        self.assertEqual(by_path(rep)["svc/src/app/billing.py"]["coverage"], 0.333)
        self.assertEqual(by_path(rep)["app/billing.py"]["evidence"], "naming-guess")

    def test_paths_from_another_checkout_match_by_suffix(self):
        report = self.repo.write("lcov.info", "\ufeffSF:/runner/work/shop/app/auth.py\nDA:1,1\nDA:2,0\nend_of_record\n")
        self.assertEqual(by_path(self.repo.report("--coverage", report))["app/auth.py"]["coverage"], 0.5)

    def test_report_matching_nothing_is_called_a_guess(self):
        report = self.repo.write("lcov.info", "SF:/elsewhere/other.py\nDA:1,1\nend_of_record\n")
        r = self.repo.run("--coverage", report)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("matched no tracked files; every row is a naming-convention GUESS", r.stdout)

    def test_auto_detects_report_at_repo_root(self):
        self.repo.write("coverage/lcov.info", "SF:app/auth.py\nDA:1,0\nDA:2,0\nend_of_record\n")
        rep = self.repo.report()
        self.assertEqual(rep["coverage_report"]["path"], "coverage/lcov.info")
        self.assertEqual(by_path(rep)["app/auth.py"]["coverage"], 0.0)

    def test_flags_report_older_than_head(self):
        report = self.repo.write("coverage/lcov.info", "SF:app/auth.py\nDA:1,1\nend_of_record\n")
        os.utime(report, (1700000000, 1700000000))  # 2023, before the commits
        self.assertTrue(self.repo.report()["coverage_report"]["older_than_head"])


class Errors(Base):
    def test_not_a_git_repo(self):
        plain = os.path.join(self.tmp.name, "plain")
        os.makedirs(plain)
        r = subprocess.run([sys.executable, SCRIPT, "-C", plain], env=BASE_ENV, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("not a git repository", r.stderr)

    def test_repo_without_commits(self):
        r = self.repo.run()
        self.assertEqual(r.returncode, 2)
        self.assertIn("no commits", r.stderr)

    def test_bad_coverage_inputs_exit_2(self):
        self.repo.commit("2026-10-01", {"a.py": PY_SRC})
        bad_xml = self.repo.write("bad.xml", "<coverage><packages>")
        not_lcov = self.repo.write("notes.info", "hello\n")
        for path in (bad_xml, not_lcov, os.path.join(self.repo.path, "missing.xml")):
            r = self.repo.run("--coverage", path)
            self.assertEqual(r.returncode, 2, path)
            self.assertIn("find_gaps:", r.stderr)

    def test_bad_arguments_exit_2(self):
        self.repo.commit("2026-10-01", {"a.py": PY_SRC})
        for args in (["--now", "yesterday"], ["--since-days", "0"], ["--top", "x"]):
            self.assertEqual(self.repo.run(*args).returncode, 2, args)


if __name__ == "__main__":
    unittest.main()
