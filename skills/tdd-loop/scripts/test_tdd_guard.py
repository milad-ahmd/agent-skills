#!/usr/bin/env python3
"""Tests for tdd_guard.py (stdlib unittest, no network).

Run: python3 -m unittest discover -s skills/tdd-loop/scripts -p 'test_*.py'
Each test builds a tiny Python project in a temp git repo and drives the guard with a real
`python3 -m unittest` command. Git runs with an empty global config and a fixed identity.
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from tdd_guard import SKIP_RX, added_tests, classify_failure, detect, is_test_file, no_tests_ran  # noqa: E402

SCRIPT = os.path.join(HERE, "tdd_guard.py")
ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", LC_ALL="C",
           GIT_AUTHOR_NAME="Ada Tester", GIT_AUTHOR_EMAIL="ada@example.com",
           GIT_COMMITTER_NAME="Ada Tester", GIT_COMMITTER_EMAIL="ada@example.com",
           GIT_AUTHOR_DATE="2026-01-01T00:00:00Z", GIT_COMMITTER_DATE="2026-01-01T00:00:00Z")
PY = sys.executable.replace("'", "")
CMD = f"'{PY}' -m unittest discover -s tests -t ."

TEST_LOWER = textwrap.dedent("""\
    import unittest
    from slug import slugify


    class SlugTest(unittest.TestCase):
        def test_lowercases(self):
            self.assertEqual(slugify("Hello"), "hello")
    """)
TEST_SPACES = TEST_LOWER + """
    def test_spaces_become_dashes(self):
        self.assertEqual(slugify("a b"), "a-b")
"""


def run(*args, cwd, env=ENV):
    r = subprocess.run([sys.executable, SCRIPT, *args], cwd=cwd, env=env, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


class Project:
    """A git repo with slug.py (identity function) and an empty tests/ package, committed."""

    def __init__(self, tmp, git=True):
        self.path = tmp
        self.write("slug.py", "def slugify(s):\n    return s\n")
        self.write("tests/__init__.py", "")
        if git:
            self.git("init", "-q")
            self.git("add", "-A")
            self.git("commit", "-q", "-m", "init")

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.path, env=ENV, check=True, capture_output=True)

    def write(self, rel, text):
        p = os.path.join(self.path, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)

    def guard(self, *args):
        return run(*args, cwd=self.path)

    def state(self):
        with open(os.path.join(self.path, ".tdd", "state.json")) as f:
            return json.load(f)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.p = Project(os.path.realpath(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def red_ok(self, test=TEST_LOWER):
        self.p.write("tests/test_slug.py", test)
        code, out = self.p.guard("red", "--test", CMD)
        self.assertEqual(code, 0, out)
        return out

    def implement_lower(self):
        self.p.write("slug.py", "def slugify(s):\n    return s.lower()\n")


class RedTests(Base):
    def test_red_accepts_a_real_assertion_failure_and_records_state(self):
        out = self.red_ok()
        self.assertIn("RED", out)
        self.assertIn("AssertionError", out)
        st = self.p.state()
        self.assertEqual(st["phase"], "red")
        self.assertEqual(st["test_cmd"], CMD)
        self.assertEqual(list(st["cycle"]["files"]), ["tests/test_slug.py"])
        self.assertEqual(len(st["cycle"]["files"]["tests/test_slug.py"]), 64)
        self.assertTrue(os.path.exists(os.path.join(self.p.path, ".tdd", ".gitignore")))

    def test_red_refuses_when_the_tests_already_pass(self):
        self.implement_lower()
        self.p.write("tests/test_slug.py", TEST_LOWER)
        code, out = self.p.guard("red", "--test", CMD)
        self.assertEqual(code, 1)
        self.assertIn("there is no failing test", out)
        self.assertFalse(os.path.exists(os.path.join(self.p.path, ".tdd", "state.json")))

    def test_red_refuses_a_syntax_error(self):
        self.p.write("tests/test_slug.py", TEST_LOWER.replace('"hello")', '"hello"'))
        code, out = self.p.guard("red", "--test", CMD)
        self.assertEqual(code, 1)
        self.assertIn("wrong reason (syntax error)", out)

    def test_red_refuses_an_import_error(self):
        self.p.write("tests/test_slug.py", TEST_LOWER.replace("from slug", "from slugs"))
        code, out = self.p.guard("red", "--test", CMD)
        self.assertEqual(code, 1)
        self.assertIn("wrong reason (import error)", out)
        self.assertIn("stub", out)

    def test_red_refuses_a_missing_function(self):
        self.p.write("tests/test_slug.py", TEST_LOWER.replace("from slug import slugify", "import slug")
                     .replace("slugify(", "slug.make_slug("))
        code, out = self.p.guard("red", "--test", CMD)
        self.assertEqual(code, 1)
        self.assertIn("missing name or function", out)

    def test_red_refuses_when_no_tests_ran(self):
        self.p.write("tests/test_slug.py", "import unittest\n\n\nclass Empty(unittest.TestCase):\n    pass\n")
        code, out = self.p.guard("red", "--test", CMD)  # 3.9-3.11 exit 0 ("Ran 0 tests"), 3.12+ exit 5
        self.assertEqual(code, 1, out)
        code, out = self.p.guard("red", "--test", "echo 'collected 0 items'; exit 5")
        self.assertEqual(code, 1)
        self.assertIn("no tests ran", out)

    def test_red_refuses_command_not_found_and_timeout(self):
        self.p.write("tests/test_slug.py", TEST_LOWER)
        code, out = self.p.guard("red", "--test", "definitely-not-a-test-runner-xyz")
        self.assertEqual(code, 1)
        self.assertIn("command not found", out)
        code, out = self.p.guard("red", "--test", f"'{PY}' -c 'import time; time.sleep(5)'", "--timeout", "0.5")
        self.assertEqual(code, 1)
        self.assertIn("timeout", out)

    def test_allow_error_accepts_and_records_the_reason(self):
        self.p.write("tests/test_slug.py", TEST_LOWER.replace("from slug", "from slugs"))
        code, out = self.p.guard("red", "--test", CMD, "--allow-error", "the module is the behaviour")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.p.state()["cycle"]["allowed"][0]["reason"], "the module is the behaviour")

    def test_red_needs_a_new_or_changed_test_file(self):
        code, out = self.p.guard("red", "--test", CMD)
        self.assertEqual(code, 2)
        self.assertIn("Write the failing test first", out)

    def test_red_outside_git_needs_files(self):
        with tempfile.TemporaryDirectory() as d:
            p = Project(os.path.realpath(d), git=False)
            p.write("tests/test_slug.py", TEST_LOWER)
            code, out = p.guard("red", "--test", CMD)
            self.assertEqual(code, 2)
            self.assertIn("--files", out)
            code, out = p.guard("red", "--test", CMD, "--files", "tests/test_slug.py")
            self.assertEqual(code, 0, out)

    def test_red_warns_about_several_new_tests_and_early_source_changes(self):
        self.p.write("notes.py", "x = 1\n")
        out = self.red_ok(TEST_SPACES)
        self.assertIn("2 new tests in this cycle", out)
        self.assertIn("non-test file(s) already changed", out)

    def test_red_reuses_the_recorded_command(self):
        self.red_ok()
        code, out = self.p.guard("red")
        self.assertEqual(code, 0, out)
        self.assertIn("Replaced the open red cycle", out)


class GreenTests(Base):
    def test_green_without_red_is_a_state_error(self):
        code, out = self.p.guard("green")
        self.assertEqual(code, 2)
        self.assertIn("Run red with a failing test first", out)

    def test_green_fails_while_the_test_still_fails(self):
        self.red_ok()
        code, out = self.p.guard("green")
        self.assertEqual(code, 1)
        self.assertIn("Still failing", out)
        self.assertEqual(self.p.state()["phase"], "red")

    def test_green_passes_after_implementing(self):
        self.red_ok()
        self.implement_lower()
        code, out = self.p.guard("green", "--json")
        self.assertEqual(code, 0, out)
        self.assertTrue(json.loads(out)["ok"])
        st = self.p.state()
        self.assertEqual(st["phase"], "green")
        self.assertIn("tests/test_slug.py", st["known"])

    def test_green_refuses_an_edited_test_and_shows_the_diff(self):
        self.red_ok()
        self.p.write("tests/test_slug.py", TEST_LOWER.replace('"hello")', '"Hello")'))
        code, out = self.p.guard("green")
        self.assertEqual(code, 1)
        self.assertIn("Test files changed since red", out)
        self.assertIn('+        self.assertEqual(slugify("Hello"), "Hello")', out)

    def test_green_refuses_a_deleted_test_file(self):
        self.red_ok()
        os.remove(os.path.join(self.p.path, "tests", "test_slug.py"))
        code, out = self.p.guard("green")
        self.assertEqual(code, 1)
        self.assertIn("deleted", out)

    def test_green_refuses_an_added_skip_marker(self):
        self.red_ok()
        self.p.write("tests/test_slug.py", TEST_LOWER.replace("    def test_lowercases",
                                                              "    @unittest.skip('later')\n    def test_lowercases"))
        code, out = self.p.guard("green")
        self.assertEqual(code, 1)
        self.assertIn("skip/only markers added", out)
        self.assertIn("@unittest.skip('later')", out)

    def test_green_refuses_skipping_an_older_committed_test(self):
        self.p.write("tests/test_old.py", "import unittest\n\n\nclass Old(unittest.TestCase):\n"
                                          "    def test_old(self):\n        self.assertTrue(True)\n")
        self.p.git("add", "-A")
        self.p.git("commit", "-q", "-m", "old test")
        self.red_ok()
        self.implement_lower()
        self.p.write("tests/test_old.py", "import unittest\n\n\nclass Old(unittest.TestCase):\n"
                                          "    @unittest.skip('flaky')\n"
                                          "    def test_old(self):\n        self.assertTrue(True)\n")
        code, out = self.p.guard("green")
        self.assertEqual(code, 1)
        self.assertIn("tests/test_old.py", out)

    def test_allow_test_change_passes_and_is_recorded(self):
        self.red_ok()
        self.implement_lower()
        self.p.write("tests/test_slug.py", TEST_LOWER.replace("Hello", "HeLLo"))
        code, out = self.p.guard("green", "--allow-test-change", "typo in the input")
        self.assertEqual(code, 0, out)
        self.assertIn("Test change allowed: typo in the input", out)
        allowed = self.p.state()["cycle"]["allowed"]
        self.assertEqual(allowed[0]["phase"], "green")
        self.assertEqual(allowed[0]["files"], ["tests/test_slug.py"])

    def test_empty_reason_is_a_usage_error(self):
        self.red_ok()
        code, _ = self.p.guard("green", "--allow-test-change", " ")
        self.assertEqual(code, 2)


class RefactorAndStateTests(Base):
    def go_green(self):
        self.red_ok()
        self.implement_lower()
        code, out = self.p.guard("green")
        self.assertEqual(code, 0, out)

    def test_refactor_before_green_is_a_state_error(self):
        self.red_ok()
        code, out = self.p.guard("refactor")
        self.assertEqual(code, 2)

    def test_refactor_passes_with_code_changes_and_fails_when_behaviour_breaks(self):
        self.go_green()
        self.p.write("slug.py", "def slugify(text):\n    return text.lower()\n")
        code, out = self.p.guard("refactor")
        self.assertEqual(code, 0, out)
        self.p.write("slug.py", "def slugify(text):\n    return text\n")
        code, out = self.p.guard("refactor")
        self.assertEqual(code, 1)
        self.assertIn("broke the tests", out)

    def test_refactor_refuses_removed_assertions(self):
        self.go_green()
        self.p.write("tests/test_slug.py", TEST_LOWER.replace('self.assertEqual(slugify("Hello"), "hello")',
                                                              'slugify("Hello")'))
        code, out = self.p.guard("refactor")
        self.assertEqual(code, 1)
        self.assertIn("assertions 1->0", out)

    def test_refactor_reports_test_changes_and_warns_on_new_tests(self):
        self.go_green()
        self.p.write("tests/test_slug.py", TEST_LOWER + "\n    def test_more(self):\n"
                                                        "        self.assertEqual(slugify('x'), 'x')\n")
        code, out = self.p.guard("refactor")
        self.assertEqual(code, 0, out)
        self.assertIn("tests 1->2", out)
        self.assertIn("new red cycle", out)

    def test_state_persists_across_cycles(self):
        self.go_green()
        self.assertEqual(self.p.guard("refactor")[0], 0)
        self.p.write("tests/test_slug.py", TEST_SPACES)
        code, out = self.p.guard("red")
        self.assertEqual(code, 0, out)
        self.assertNotIn("2 new tests", out)  # test_lowercases belongs to cycle 1
        st = self.p.state()
        self.assertEqual(len(st["history"]), 1)
        self.assertEqual(st["cycle"]["number"], 2)
        code, out = self.p.guard("status", "--json")
        rep = json.loads(out)
        self.assertEqual((code, rep["phase"], rep["cycles_done"]), (0, "red", 1))

    def test_corrupt_state_is_a_state_error(self):
        os.makedirs(os.path.join(self.p.path, ".tdd"))
        with open(os.path.join(self.p.path, ".tdd", "state.json"), "w") as f:
            f.write("{not json")
        code, out = self.p.guard("status")
        self.assertEqual(code, 2)
        self.assertIn("Can't read", out)


class DetectTests(unittest.TestCase):
    def detect_with(self, files):
        with tempfile.TemporaryDirectory() as d:
            for rel, text in files.items():
                with open(os.path.join(d, rel), "w") as f:
                    f.write(text)
            return [c["command"] for c in detect(d)]

    def test_detect_project_types(self):
        self.assertEqual(self.detect_with({"pyproject.toml": "[tool.pytest.ini_options]\n"}), ["python3 -m pytest -q"])
        self.assertIn("python3 -m unittest discover", self.detect_with({"setup.py": ""})[0])
        js = self.detect_with({"package.json": json.dumps({"devDependencies": {"vitest": "^2"},
                                                           "scripts": {"test": "vitest"}}),
                               "pnpm-lock.yaml": ""})
        self.assertEqual(js, ["npx vitest run", "pnpm test"])
        self.assertEqual(self.detect_with({"package.json": json.dumps({"devDependencies": {"jest": "29"}})}),
                         ["npx jest"])
        self.assertEqual(self.detect_with({"package.json": json.dumps(
            {"scripts": {"test": "echo \"Error: no test specified\" && exit 1"}})}), [])
        self.assertEqual(self.detect_with({"go.mod": "module x\n"}), ["go test ./..."])
        self.assertEqual(self.detect_with({"Cargo.toml": "[package]\n"}), ["cargo test"])
        self.assertEqual(self.detect_with({"Gemfile": "gem 'rspec'\n"}), ["bundle exec rspec"])
        self.assertEqual(self.detect_with({"pom.xml": "", "mvnw": ""}), ["./mvnw test"])
        self.assertEqual(self.detect_with({"build.gradle.kts": ""}), ["gradle test"])

    def test_detect_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            code, out = run("detect", "--json", cwd=d)
            self.assertEqual((code, json.loads(out)["candidates"]), (1, []))
            with open(os.path.join(d, "go.mod"), "w") as f:
                f.write("module x\n")
            code, out = run("detect", cwd=d)
            self.assertEqual(code, 0)
            self.assertIn("go test ./...", out)


class HeuristicTests(unittest.TestCase):
    def cls(self, output, exit_code=1):
        return classify_failure({"output": output, "exit": exit_code, "timed_out": False})[:2]

    def test_wrong_reason_patterns_across_languages(self):
        self.assertEqual(self.cls("Cannot find module './slug' from 'slug.test.ts'")[1], "import error")
        self.assertEqual(self.cls("src/a.ts(3,1): error TS2304: Cannot find name 'slugify'.")[1], "compile error")
        self.assertEqual(self.cls("FAIL\texample.com/slug [build failed]")[1], "compile error")
        self.assertEqual(self.cls("error[E0425]: cannot find function `slugify`")[1], "compile error")
        self.assertEqual(self.cls("TypeError: slugify is not a function")[1], "missing name or function")
        self.assertEqual(self.cls("ERROR collecting tests/test_x.py")[1], "collection error")

    def test_real_failures_are_accepted_even_next_to_empty_packages(self):
        go = "?   \texample.com/cmd\t[no test files]\n--- FAIL: TestSlug (0.00s)\n    slug_test.go:9: got Hello\nFAIL"
        self.assertEqual(self.cls(go), (True, "assertion"))
        jest = "FAIL src/slug.test.ts\n  ✕ lowercases (3 ms)\n  Expected: \"hello\"\n  Received: \"Hello\""
        self.assertEqual(self.cls(jest), (True, "assertion"))
        self.assertEqual(self.cls("No tests found, exiting with code 1")[1], "no tests ran")

    def test_no_tests_ran_on_a_passing_run(self):
        self.assertTrue(no_tests_ran("\nRan 0 tests in 0.000s\n\nOK\n"))
        self.assertEqual(no_tests_ran("ok  \texample.com/slug\t0.01s\n?   \texample.com/cmd\t[no test files]"), "")

    def test_skip_markers(self):
        for line in ("@pytest.mark.skip(reason='x')", "@unittest.skip('x')", "it.skip('a', () => {})",
                     "xit('a', () => {})", "describe.only('a', () => {})", "\tt.Skip(\"later\")", "#[ignore]",
                     "@Disabled", "self.skipTest('x')", "test.todo('a')"):
            self.assertTrue(SKIP_RX.search(line), line)
        for line in ("def test_skips_blank_lines(self):", "result = skip_list(items)", "it('skips empties', f)"):
            self.assertFalse(SKIP_RX.search(line), line)

    def test_test_file_patterns_and_added_test_names(self):
        for path in ("tests/test_x.py", "pkg/x_test.go", "src/a.test.ts", "src/__tests__/a.js", "spec/a_spec.rb",
                     "src/test/java/FooTest.java", "tests/integration.rs"):
            self.assertTrue(is_test_file(path), path)
        for path in ("src/testing.py", "src/contest.ts", "src/main.rs", "README.md"):
            self.assertFalse(is_test_file(path), path)
        self.assertEqual(added_tests(TEST_LOWER, TEST_SPACES), ["test_spaces_become_dashes"])
        self.assertEqual(added_tests("", "it('lowercases', () => {})\nfunc TestSlug(t *testing.T) {"),
                         ["lowercases", "TestSlug"])


if __name__ == "__main__":
    unittest.main()
