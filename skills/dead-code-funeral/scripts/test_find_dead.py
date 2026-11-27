#!/usr/bin/env python3
"""Tests for find_dead.py (stdlib unittest, no network).

Run: python3 -m unittest discover -s skills/dead-code-funeral/scripts -p 'test_*.py'
Each test builds a throwaway git repo with fixed commit dates, then checks what is reported as dead,
what is spared, the confidence levels, the git dates, the exit codes and the SVG card.
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
SCRIPT = os.path.join(HERE, "find_dead.py")
BASE_ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", LC_ALL="C")
for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
    BASE_ENV.pop(var, None)


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

    def commit(self, date, files):
        for rel, text in files.items():
            full = os.path.join(self.path, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as fh:
                fh.write(text)
        stamp = date + "T12:00:00Z"
        env = dict(BASE_ENV, GIT_AUTHOR_NAME="Dev", GIT_AUTHOR_EMAIL="dev@example.com", GIT_COMMITTER_NAME="Dev",
                   GIT_COMMITTER_EMAIL="dev@example.com", GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
        self.git("add", "-A", env=env)
        self.git("commit", "-q", "-m", "change", env=env)

    def run(self, *args):
        return subprocess.run([sys.executable, SCRIPT, "-C", self.path, "--now", "2026-11-01", *args],
                              env=BASE_ENV, capture_output=True, text=True, timeout=120)

    def report(self, *args):
        r = self.run("--json", *args)
        if r.returncode not in (0, 1):
            raise AssertionError(r.stderr)
        return json.loads(r.stdout)

    def dead(self):
        return {(i["path"], i["name"]): i for i in self.report()["items"]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repo(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()


class PythonFiles(Base):
    def test_unimported_file_is_high_with_dates_and_lines(self):
        self.repo.commit("2019-03-04", {"main.py": "print(1)\n", "utils/legacy_parser.py": "def p(s):\n    return s\n"})
        self.repo.commit("2023-11-02", {"utils/legacy_parser.py": "def p(s):\n    return s.strip()\n\n"})
        it = self.repo.dead()[("utils/legacy_parser.py", None)]
        self.assertEqual(it["confidence"], "high")
        self.assertEqual((it["born"], it["last_touched"], it["lines"]), ("2019-03-04", "2023-11-02", 3))
        self.assertEqual(it["obituary"], "utils/legacy_parser.py, 2019-03-04 to 2023-11-02, 3 lines, "
                                         "outlived by 0 callers")
        self.assertEqual(it["idle_days"], 1095)

    def test_born_date_follows_renames(self):
        self.repo.commit("2018-01-01", {"main.py": "", "old_name.py": "def p():\n    return 1\n"})
        self.repo.git("mv", "old_name.py", "new_name.py")
        self.repo.commit("2021-01-01", {})
        it = self.repo.dead()[("new_name.py", None)]
        self.assertEqual((it["born"], it["last_touched"]), ("2018-01-01", "2021-01-01"))

    def test_absolute_submodule_and_relative_imports_keep_files_alive(self):
        self.repo.commit("2024-01-01", {
            "main.py": "import pkg.a\nfrom pkg import b\npkg.a.x(); b.y()\n",
            "pkg/__init__.py": "", "pkg/a.py": "from .c import z\ndef x():\n    return z()\n",
            "pkg/b.py": "from ..lib.d import w\ndef y():\n    return w()\n",
            "pkg/c.py": "def z():\n    return 1\n", "lib/__init__.py": "", "lib/d.py": "def w():\n    return 2\n"})
        self.assertEqual(self.repo.dead(), {})

    def test_src_layout_import_resolves_by_path_tail(self):
        self.repo.commit("2024-01-01", {"src/app/__init__.py": "", "src/app/core.py": "def go():\n    pass\n",
                                        "src/app/__main__.py": "from app.core import go\ngo()\n"})
        self.assertEqual(self.repo.dead(), {})

    def test_entry_points_tests_and_scripts_are_spared(self):
        self.repo.commit("2024-01-01", {
            "cli.py": "", "pkg/__main__.py": "", "index.ios.js": "", "tests/test_x.py": "def test_a():\n    pass\n",
            "conftest.py": "import pytest\n@pytest.fixture\ndef db():\n    return 1\n",
            "tools/run.py": "def go():\n    pass\nif __name__ == '__main__':\n    go()\n",
            "bin/tool.py": "#!/usr/bin/env python3\nprint(1)\n",
            "docs/conf.py": "project = 'x'\ndef setup(app):\n    pass\n"})
        rep = self.repo.report()
        self.assertEqual(rep["items"], [])
        self.assertEqual(rep["entry_points"]["tools/run.py"], "runnable script")
        self.assertEqual(rep["entry_points"]["tests/test_x.py"], "test")

    def test_pyproject_entry_point_keeps_module_and_function(self):
        self.repo.commit("2024-01-01", {"pyproject.toml": '[project]\nname = "x"\n[project.scripts]\nx = "pkg.tool:run"\n',
                                        "pkg/__init__.py": "", "pkg/tool.py": "def run():\n    pass\n"})
        rep = self.repo.report()
        self.assertEqual(rep["items"], [])
        self.assertEqual(rep["library"], "pyproject.toml declares a package")

    def test_django_conventions_are_spared(self):
        self.repo.commit("2024-01-01", {"shop/models.py": "class Order:\n    pass\n", "shop/admin.py": "",
                                        "shop/migrations/0001_initial.py": "",
                                        "shop/management/commands/sync.py": "class Command:\n    pass\n"})
        rep = self.repo.report()
        self.assertEqual([i for i in rep["items"] if i["kind"] == "file"], [])

    def test_module_path_in_a_string_or_config_makes_it_low(self):
        self.repo.commit("2024-01-01", {"main.py": "import importlib\nimportlib.import_module('plugins.legacy')\n",
                                        "plugins/__init__.py": "", "plugins/legacy.py": "X = 1\n",
                                        "jobs/nightly.py": "X = 2\n", "deploy.yml": "run: python jobs/nightly.py\n"})
        dead = self.repo.dead()
        self.assertEqual(dead[("plugins/legacy.py", None)]["confidence"], "low")
        self.assertEqual(dead[("plugins/legacy.py", None)]["named_in"], ["main.py"])
        self.assertEqual(dead[("jobs/nightly.py", None)]["named_in"], ["deploy.yml"])

    def test_vendored_and_syntax_error_files_are_ignored(self):
        self.repo.commit("2024-01-01", {"vendor/lib.py": "X = 1\n", "node_modules/a/index2.js": "x\n",
                                        "broken.py": "def (:\n"})
        self.assertEqual(self.repo.dead(), {})


class PythonSymbols(Base):
    def test_unused_function_is_medium_with_blame_dates(self):
        self.repo.commit("2020-05-01", {"main.py": "from lib import used\nused()\n",
                                        "lib.py": "def used():\n    return 1\n\n\ndef unused(x):\n    return x\n"})
        self.repo.commit("2022-06-01", {"lib.py": "def used():\n    return 1\n\n\ndef unused(x):\n    return x * 2\n"})
        dead = self.repo.dead()
        self.assertEqual(list(dead), [("lib.py", "unused")])
        it = dead[("lib.py", "unused")]
        self.assertEqual((it["confidence"], it["kind"], it["lines"]), ("medium", "function", 2))
        self.assertEqual((it["born"], it["last_touched"]), ("2020-05-01", "2022-06-01"))

    def test_same_file_use_counts_but_recursion_and_comments_do_not(self):
        self.repo.commit("2024-01-01", {"main.py": "import lib\nlib.a()\n# lib.c() is old\n",
                                        "lib.py": "def a():\n    return b()\ndef b():\n    return 1\n"
                                                  "def c(n):\n    return c(n - 1) if n else 0\n"})
        self.assertEqual(list(self.repo.dead()), [("lib.py", "c")])

    def test_name_in_string_or_decorator_makes_it_low(self):
        self.repo.commit("2024-01-01", {"main.py": "import lib\ngetattr(lib, 'handler')()\n",
                                        "lib.py": "import app\ndef handler():\n    pass\n"
                                                  "@app.route('/x')\ndef view():\n    pass\n"
                                                  "from dataclasses import dataclass\n@dataclass\nclass Row:\n    a: int\n"})
        dead = self.repo.dead()
        self.assertEqual(dead[("lib.py", "handler")]["confidence"], "low")
        self.assertEqual(dead[("lib.py", "view")]["confidence"], "low")
        self.assertIn("app.route", dead[("lib.py", "view")]["reason"])
        self.assertEqual(dead[("lib.py", "Row")]["confidence"], "medium")
        self.assertEqual(dead[("lib.py", "Row")]["kind"], "class")

    def test_symbols_of_a_dead_file_are_not_counted_twice(self):
        self.repo.commit("2024-01-01", {"old.py": "def a():\n    pass\ndef b():\n    pass\n"})
        rep = self.repo.report()
        self.assertEqual([(i["path"], i["name"]) for i in rep["items"]], [("old.py", None)])
        self.assertEqual(rep["totals"]["lines"], 4)


class JavaScript(Base):
    def test_unimported_file_and_unused_export(self):
        self.repo.commit("2024-01-01", {
            "src/index.ts": "import { a } from './lib/util.js';\nconsole.log(a);\n",
            "src/lib/util.ts": "export const a = 1;\nexport function neverUsed(x: number) {\n  return x + 1;\n}\n",
            "src/lib/old.ts": "export const b = 2;\n"})
        dead = self.repo.dead()
        self.assertEqual(set(dead), {("src/lib/old.ts", None), ("src/lib/util.ts", "neverUsed")})
        it = dead[("src/lib/util.ts", "neverUsed")]
        self.assertEqual((it["confidence"], it["kind"], it["start"], it["end"]), ("medium", "export function", 2, 4))

    def test_require_dynamic_import_index_and_reexport_resolve(self):
        self.repo.commit("2024-01-01", {
            "src/main.js": "const x = require('./x');\nimport('./lazy').then(() => {});\nexport * from './comp';\n",
            "src/x.cjs": "module.exports = 1;\n", "src/lazy.mjs": "export default 1;\n",
            "src/comp/index.tsx": "export default function C() { return null; }\n"})
        self.assertEqual(self.repo.dead(), {})

    def test_platform_files_resolve_from_a_bare_import(self):
        self.repo.commit("2024-01-01", {"src/index.js": "import Page from './Page';\n",
                                        "src/Page.web.js": "", "src/Page.native.js": ""})
        self.assertEqual(self.repo.dead(), {})

    def test_alias_import_and_vue_importer(self):
        self.repo.commit("2024-01-01", {
            "src/App.vue": "<script setup>\nimport { fmt } from '@/utils/fmt'\n</script>\n",
            "src/utils/fmt.ts": "export function fmt() {}\n", "src/main.ts": "import App from './App.vue'\n"})
        self.assertEqual(self.repo.dead(), {})

    def test_export_list_and_string_only_use(self):
        self.repo.commit("2024-01-01", {
            "src/index.js": "import { shown } from './m';\nshown();\nconst name = 'hidden';\n",
            "src/m.js": "function shown() {}\nfunction hidden() {\n  return 1;\n}\nexport { shown, hidden };\n"})
        it = self.repo.dead()[("src/m.js", "hidden")]
        self.assertEqual((it["confidence"], it["start"], it["end"]), ("low", 2, 4))
        self.assertEqual(it["named_in"], ["src/index.js"])

    def test_package_json_next_and_config_files_are_spared(self):
        self.repo.commit("2024-01-01", {
            "package.json": json.dumps({"name": "web", "private": True, "main": "./lib/start.js",
                                        "bin": {"w": "bin/w.js"}, "scripts": {"seed": "node scripts/seed.mjs"}}),
            "lib/start.js": "", "bin/w.js": "", "scripts/seed.mjs": "", "pages/about.tsx": "",
            "app/blog/page.tsx": "", "app/routes/home.tsx": "", "src/routes/+page.server.ts": "",
            "src/hooks.server.ts": "", "middleware.ts": "", "vite.config.ts": "", ".eslintrc.cjs": "", "src/Button.stories.tsx": "",
            "src/types.d.ts": "export type T = 1;\n"})
        rep = self.repo.report()
        self.assertEqual(rep["items"], [])
        self.assertIsNone(rep["library"])
        self.assertEqual(rep["entry_points"]["app/blog/page.tsx"], "Next.js route")
        self.assertEqual(rep["entry_points"]["lib/start.js"], "named in package metadata")
        self.assertEqual(rep["entry_points"]["app/routes/home.tsx"], "file-based route")
        self.assertEqual(rep["entry_points"]["src/hooks.server.ts"], "SvelteKit convention")

    def test_strings_and_comments_do_not_count_as_imports(self):
        self.repo.commit("2024-01-01", {"src/index.js": "// import x from './ghost'\nconst s = \"don't\";\n",
                                        "src/ghost.js": "export const g = 1;\n"})
        it = self.repo.dead()[("src/ghost.js", None)]
        self.assertEqual((it["confidence"], it["named_in"]), ("high", []))


class Cli(Base):
    def test_exit_codes(self):
        self.repo.commit("2024-01-01", {"main.py": "print(1)\n"})
        self.assertEqual(self.repo.run().returncode, 0)
        self.assertIn("Nothing to bury.", self.repo.run().stdout)
        self.repo.commit("2024-02-01", {"old.py": "x = 1\n"})
        self.assertEqual(self.repo.run().returncode, 1)
        self.assertEqual(self.repo.run("--now", "yesterday").returncode, 2)
        self.assertEqual(self.repo.run("--top", "0").returncode, 2)

    def test_not_a_repo_and_no_commits(self):
        plain = os.path.join(self.tmp.name, "plain")
        os.makedirs(plain)
        r = subprocess.run([sys.executable, SCRIPT, "-C", plain], env=BASE_ENV, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("not a git repository", r.stderr)
        r = self.repo.run()
        self.assertEqual(r.returncode, 2)
        self.assertIn("no commits", r.stderr)

    def test_ranking_and_totals(self):
        self.repo.commit("2024-01-01", {"main.py": "import lib\nlib.a()\n",
                                        "lib.py": "def a():\n    pass\ndef big():\n    x = 1\n    y = 2\n    return x\n",
                                        "small.py": "x = 1\n", "large.py": "x = 1\ny = 2\nz = 3\n"})
        rep = self.repo.report()
        self.assertEqual([(i["path"], i["name"]) for i in rep["items"]],
                         [("large.py", None), ("small.py", None), ("lib.py", "big")])
        self.assertEqual(rep["totals"], {"items": 3, "lines": 8, "by_confidence": {"high": 2, "medium": 1, "low": 0}})
        self.assertEqual(rep["root"], "repo")

    def test_text_report_and_top(self):
        self.repo.commit("2024-01-01", {"a.py": "x = 1\n", "b.py": "x = 1\n", "c.py": "x = 1\n"})
        out = self.repo.run("--top", "2").stdout
        self.assertIn("Dead code funeral: 3 items, 3 lines that could go", out)
        self.assertIn("... 1 more", out)
        self.assertIn("untouched for 1,035 days", out)

    def test_svg_card_hides_paths_unless_asked(self):
        self.repo.commit("2024-01-01", {"secret_module.py": "x = 1\n"})
        card = os.path.join(self.tmp.name, "card.svg")
        self.assertEqual(self.repo.run("--svg", card).returncode, 1)
        with open(card) as fh:
            body = fh.read()
        ET.fromstring(body)
        self.assertIn("R.I.P.", body)
        self.assertNotIn("secret_module", body)
        self.repo.run("--svg", card, "--show-paths")
        with open(card) as fh:
            self.assertIn("secret_module.py", fh.read())


if __name__ == "__main__":
    unittest.main()
