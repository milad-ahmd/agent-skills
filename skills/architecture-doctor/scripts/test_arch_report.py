#!/usr/bin/env python3
"""Tests for arch_report.py (stdlib unittest, no network).

Run: python3 -m unittest discover -s skills/architecture-doctor/scripts -p 'test_*.py'
Fixtures are written into temp dirs: a Python app and a TS app (tsconfig alias) with a cycle, a god file and a
layer violation, and temp git repos with fixed identities and dates for churn and hidden coupling.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from arch_report import analyse, instability, layer_of, main, parse_layers, render_text  # noqa: E402

SCRIPT = os.path.join(HERE, "arch_report.py")
DAY = 86400


def write(root, files):
    for rel, text in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p) or root, exist_ok=True)
        with open(p, "w") as f:
            f.write(text)


def big(n, prefix="x"):
    return "".join("%s_%d = %d\n" % (prefix, i, i) for i in range(n))


PYAPP = {
    "app/__init__.py": "",
    "app/ui/__init__.py": "",
    "app/services/__init__.py": "",
    "app/db/__init__.py": "",
    "app/core/__init__.py": "",
    "app/ui/views.py": "from app.services import orders\nfrom app.core import big\n",
    "app/ui/forms.py": "from app.core import big\n",
    "app/services/orders.py": "from app.db import models\nfrom app.core import big\n",
    "app/services/billing.py": "from app.core import big\n",
    # layer violation and cycle: db -> ui -> services -> db
    "app/db/models.py": "from app.ui import views\nfrom app.core import big\n",
    "app/core/big.py": big(600),
    "app/core/small.py": big(10),
    "tests/test_orders.py": "from app.services import orders\n",
}

TSAPP = {
    "tsconfig.json": '{\n  // comment\n  "compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}\n}\n',
    "src/ui/Button.tsx": "import { get } from '../services/api'\nexport const B = get\n",
    "src/ui/theme.ts": "export const theme = 1\n",
    "src/services/api.ts": "import { db } from '@/db/client'\nexport const get = db\n",
    "src/db/client.ts": "import { theme } from '@/ui/theme'\nexport const db = theme\n",
}


class Tmp(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="arch-")
        self.root = os.path.join(self.dir, "repo")
        os.makedirs(self.root)
        self._ceiling = os.environ.get("GIT_CEILING_DIRECTORIES")
        os.environ["GIT_CEILING_DIRECTORIES"] = self.dir   # never pick up a repo that happens to contain TMPDIR

    def tearDown(self):
        if self._ceiling is None:
            os.environ.pop("GIT_CEILING_DIRECTORIES", None)
        else:
            os.environ["GIT_CEILING_DIRECTORIES"] = self._ceiling
        shutil.rmtree(self.dir, ignore_errors=True)

    def git(self, *args, when=None):
        env = dict(os.environ, GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="T",
                   GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_NOSYSTEM="1", HOME=self.dir)
        if when is not None:
            env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = "%d +0000" % when
        subprocess.run(["git"] + list(args), cwd=self.root, env=env, check=True, capture_output=True)

    def init_git(self):
        self.git("init", "-q")
        self.n = 0

    def commit(self, files, days_ago=10):
        """Append a line to each file (creating it if needed) and commit them together."""
        self.n += 1
        for rel in files:
            p = os.path.join(self.root, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "a") as f:
                f.write("v_%d = %d\n" % (self.n, self.n))
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "c%d" % self.n, when=int(time.time()) - days_ago * DAY)

    def report(self, **kw):
        return analyse(self.root, **kw)


class Metrics(Tmp):
    def test_instability_formula(self):
        self.assertEqual(instability(0, 4), 1.0)
        self.assertEqual(instability(3, 1), 0.25)
        self.assertIsNone(instability(0, 0))

    def test_file_fan_in_fan_out_and_tests_left_out(self):
        write(self.root, PYAPP)
        r = self.report()
        f = {x["path"]: x for x in r["files"]}
        self.assertNotIn("tests/test_orders.py", f)
        self.assertEqual(f["app/core/big.py"]["fan_in"], 5)
        self.assertEqual(f["app/ui/views.py"]["fan_out"], 2)
        self.assertEqual(f["app/core/big.py"]["instability"], 0.0)
        self.assertEqual(f["app/services/orders.py"]["fan_in"], 1)   # only views; the test file is excluded

    def test_include_tests_adds_test_importers(self):
        write(self.root, PYAPP)
        f = {x["path"]: x for x in self.report(include_tests=True)["files"]}
        self.assertEqual(f["app/services/orders.py"]["fan_in"], 2)

    def test_module_ca_ce_count_files_outside(self):
        write(self.root, PYAPP)
        mods = {m["id"]: m for m in self.report(depth=2)["modules"]}
        core = mods["app/core"]
        self.assertEqual((core["ca"], core["ce"], core["instability"]), (5, 0, 0.0))
        ui = mods["app/ui"]
        self.assertEqual(ui["ce"], 2)          # services/orders.py and core/big.py
        self.assertEqual(ui["ca"], 1)          # db/models.py


class Cycles(Tmp):
    def test_python_cycle_found_and_ranked(self):
        write(self.root, PYAPP)
        r = self.report(use_git=False)
        fc = [c for c in r["cycles"] if c["level"] == "file"]
        self.assertEqual(len(fc), 1)
        self.assertEqual(set(fc[0]["members"]), {"app/db/models.py", "app/services/orders.py", "app/ui/views.py"})
        self.assertTrue(fc[0]["eager"])
        kinds = [p["kind"] for p in r["problems"]]
        self.assertIn("import cycle", kinds)
        self.assertEqual({x["path"]: x["cycle"] for x in r["files"]}["app/ui/views.py"], fc[0]["id"])

    def test_lazy_cycle_scores_below_eager(self):
        write(self.root, {"a.py": "import b\n", "b.py": "import a\n",
                          "c.py": "def f():\n    import d\n", "d.py": "def g():\n    import c\n"})
        r = self.report(use_git=False)
        cyc = {tuple(p["files"]): p for p in r["problems"] if p["kind"] == "import cycle"}
        self.assertGreater(cyc[("a.py", "b.py")]["score"], cyc[("c.py", "d.py")]["score"])
        self.assertIn("lazy, dynamic or type-only", cyc[("c.py", "d.py")]["reason"])

    def test_module_cycle_points_at_cheapest_edge(self):
        write(self.root, {
            "pkg/__init__.py": "", "pkg/a/__init__.py": "", "pkg/b/__init__.py": "",
            "pkg/a/one.py": "from pkg.b import x\n", "pkg/a/two.py": "from pkg.b import x\n",
            "pkg/b/x.py": "", "pkg/b/y.py": "from pkg.a import three\n", "pkg/a/three.py": "",
        })
        r = self.report(depth=2, use_git=False)
        mc = [c for c in r["cycles"] if c["level"] == "module"]
        self.assertEqual(len(mc), 1)
        w = mc[0]["weakest_edge"]
        self.assertEqual((w["source"], w["target"], w["files"]), ("pkg/b", "pkg/a", ["pkg/b/y.py"]))
        self.assertIn("module cycle", [p["kind"] for p in r["problems"]])

    def test_ts_cycle_through_alias(self):
        write(self.root, dict(TSAPP, **{"src/ui/theme.ts": "import { B } from './Button'\nexport const theme = B\n"}))
        r = self.report(use_git=False)
        fc = [c for c in r["cycles"] if c["level"] == "file"]
        self.assertEqual(len(fc), 1)
        self.assertEqual(len(fc[0]["members"]), 4)


class GodFiles(Tmp):
    def test_god_file_flagged(self):
        write(self.root, PYAPP)
        r = self.report(use_git=False)
        self.assertEqual([g["path"] for g in r["god_files"]], ["app/core/big.py"])
        top = [p for p in r["problems"] if p["kind"] == "god file"]
        self.assertEqual(top[0]["files"], ["app/core/big.py"])
        self.assertIn("600 lines", top[0]["reason"])

    def test_big_file_nobody_imports_is_not_a_god_file(self):
        write(self.root, {"lonely.py": big(900)})
        r = self.report(use_git=False)
        self.assertEqual(r["god_files"], [])
        self.assertEqual(r["problems"], [])

    def test_thresholds_are_configurable(self):
        write(self.root, PYAPP)
        self.assertEqual(self.report(use_git=False, god_fan_in=6)["god_files"], [])
        self.assertEqual(len(self.report(use_git=False, hub_min=5)["hubs"]), 1)


class Layers(Tmp):
    def test_parse_layers(self):
        self.assertEqual(parse_layers("ui,pages > services>db/"), [["ui", "pages"], ["services"], ["db"]])
        for bad in ("ui", "ui>>db", ">db", "ui>services>ui"):
            with self.assertRaises(ValueError):
                parse_layers(bad)

    def test_layer_of(self):
        lay = parse_layers("ui>services>db")
        self.assertEqual(layer_of("src/ui/a.ts", lay), 0)
        self.assertEqual(layer_of("src/db/models/x.py", lay), 2)
        self.assertIsNone(layer_of("src/other/x.py", lay))
        self.assertIsNone(layer_of("ui.py", lay))                      # a file name is not a folder
        self.assertEqual(layer_of("ui/services/x.ts", lay), 0)         # outermost folder wins
        lay = parse_layers("src/web>src/core,lib/*.py")
        self.assertEqual(layer_of("src/web/a.py", lay), 0)
        self.assertEqual(layer_of("lib/util.py", lay), 1)
        self.assertIsNone(layer_of("src/webapp/a.py", lay))            # prefix matches whole folders only

    def test_python_layer_violation(self):
        write(self.root, PYAPP)
        r = self.report(layers=parse_layers("ui>services>db"), use_git=False)
        self.assertEqual([(v["source"], v["target"]) for v in r["layer_violations"]],
                         [("app/db/models.py", "app/ui/views.py")])
        lv = [p for p in r["problems"] if p["kind"] == "layer violation"]
        self.assertEqual(lv[0]["files"], ["app/db/models.py"])
        self.assertTrue(any("in no layer" in n for n in r["notes"]))   # app/core is outside the layers

    def test_ts_layer_violation_through_alias(self):
        write(self.root, TSAPP)
        r = self.report(layers=parse_layers("ui>services>db"), use_git=False)
        self.assertEqual([(v["source"], v["target"], v["from_layer"], v["to_layer"]) for v in r["layer_violations"]],
                         [("src/db/client.ts", "src/ui/theme.ts", "db", "ui")])

    def test_lazy_layer_violations_weigh_less(self):
        files = dict(TSAPP)
        files["src/db/client.ts"] = "export const db = 1\n"
        files["src/db/eager.ts"] = "import { theme } from '../ui/theme'\nexport const e = theme\n"
        files["src/services/preload.ts"] = "".join("export const p%d = () => import('../ui/p%d')\n" % (i, i)
                                                   for i in range(6))
        files.update({"src/ui/p%d.ts" % i: "export const x = 1\n" for i in range(6)})
        write(self.root, files)
        r = self.report(layers=parse_layers("ui>services>db"), use_git=False)
        lazy = [v for v in r["layer_violations"] if v["lazy"]]
        self.assertEqual(len(lazy), 6)
        lv = {p["files"][0]: p for p in r["problems"] if p["kind"] == "layer violation"}
        self.assertGreater(lv["src/db/eager.ts"]["score"], lv["src/services/preload.ts"]["score"])
        self.assertIn("6 lazy, dynamic or type-only", lv["src/services/preload.ts"]["reason"])

    def test_layers_that_match_nothing_say_so(self):
        write(self.root, TSAPP)
        r = self.report(layers=parse_layers("web>core"), use_git=False)
        self.assertEqual(r["layer_violations"], [])
        self.assertTrue(any("matched no files" in n for n in r["notes"]))


class History(Tmp):
    def test_churn_counts_only_the_window(self):
        self.init_git()
        self.commit(["a.py"], days_ago=400)
        self.commit(["a.py", "b.py"], days_ago=20)
        self.commit(["a.py"], days_ago=5)
        r = self.report(days=180)
        f = {x["path"]: x for x in r["files"]}
        self.assertEqual((f["a.py"]["commits"], f["b.py"]["commits"]), (2, 1))
        self.assertEqual(r["stats"]["commits"], 2)
        self.assertEqual(r["hotspots"][0]["path"], "a.py")
        self.assertEqual(r["hotspots"][0]["hotspot_score"], 2 * f["a.py"]["loc"])
        self.assertEqual({x["path"]: x for x in self.report(days=1000)["files"]}["a.py"]["commits"], 3)

    def test_hidden_coupling_without_import(self):
        self.init_git()
        for _ in range(4):
            self.commit(["api/handlers.py", "web/forms.py"])
        self.commit(["api/handlers.py"])
        r = self.report()
        hc = r["hidden_coupling"]
        self.assertEqual(len(hc), 1)
        self.assertEqual((hc[0]["a"], hc[0]["b"], hc[0]["shared"]), ("api/handlers.py", "web/forms.py", 4))
        self.assertEqual((hc[0]["commits_a"], hc[0]["commits_b"]), (5, 4))
        self.assertAlmostEqual(hc[0]["ratio"], 0.89, places=2)
        self.assertFalse(hc[0]["same_module"])
        self.assertIn("hidden coupling", [p["kind"] for p in r["problems"]])

    def test_pair_with_an_import_is_not_hidden(self):
        self.init_git()
        write(self.root, {"svc.py": "import util\n"})
        for _ in range(4):
            self.commit(["svc.py", "util.py"])
        self.assertEqual(self.report()["hidden_coupling"], [])

    def test_large_commits_are_skipped_as_noise(self):
        self.init_git()
        names = ["m%02d.py" % i for i in range(35)]
        for _ in range(3):
            self.commit(names)
        r = self.report()
        self.assertEqual(r["hidden_coupling"], [])
        self.assertEqual(r["files"][0]["commits"], 3)                  # still counted as churn
        self.assertTrue(any("3 commit(s) touching more than 30" in n for n in r["notes"]))
        self.assertGreater(len(self.report(max_commit_files=40)["hidden_coupling"]), 0)

    def test_thresholds_for_shared_and_ratio(self):
        self.init_git()
        for _ in range(3):
            self.commit(["a.py", "b.py"])
        for _ in range(9):
            self.commit(["a.py"])
        self.assertEqual(self.report()["hidden_coupling"], [])           # 3 / avg(12, 3) = 0.4
        self.assertEqual(len(self.report(min_ratio=0.4)["hidden_coupling"]), 1)
        self.assertEqual(self.report(min_ratio=0.4, min_shared=4)["hidden_coupling"], [])

    def test_churn_raises_rank(self):
        self.init_git()
        users = {"u%d.py" % i: "import hot\nimport cold\n" for i in range(5)}
        write(self.root, dict(users, **{"hot.py": big(600, "h"), "cold.py": big(600, "c")}))
        self.commit(list(users) + ["cold.py"], days_ago=30)
        for _ in range(6):
            self.commit(["hot.py"])
        r = self.report()
        files = [p["files"][0] for p in r["problems"] if "god file" in p["kind"]]
        self.assertEqual(files, ["hot.py", "cold.py"])
        hot = [p for p in r["problems"] if p["files"] == ["hot.py"]][0]
        self.assertIn("hotspot", hot["kind"])

    def test_subdirectory_of_a_repo(self):
        self.init_git()
        for _ in range(3):
            self.commit(["svc/a.py", "svc/b.py", "other/c.py"])
        r = analyse(os.path.join(self.root, "svc"))
        self.assertEqual(sorted(x["path"] for x in r["files"]), ["a.py", "b.py"])
        self.assertEqual([x["commits"] for x in r["files"]], [3, 3])
        self.assertEqual([(p["a"], p["b"]) for p in r["hidden_coupling"]], [("a.py", "b.py")])

    def test_no_git_repository(self):
        write(self.root, PYAPP)
        r = self.report()
        self.assertFalse(r["git"])
        self.assertIsNone(r["stats"]["commits"])
        self.assertEqual((r["hotspots"], r["hidden_coupling"]), ([], []))
        self.assertNotIn("commits", r["files"][0])
        self.assertTrue(any("not a git repository" in n for n in r["notes"]))
        self.assertNotIn("Hotspots", render_text(r))

    def test_repo_without_commits_in_window(self):
        self.init_git()
        self.commit(["a.py"], days_ago=500)
        r = self.report(days=30)
        self.assertTrue(r["git"])
        self.assertEqual(r["stats"]["commits"], 0)
        self.assertTrue(any("no commits in the last 30 days" in n for n in r["notes"]))


class Cli(Tmp):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, SCRIPT] + list(args), capture_output=True, text=True)

    def test_json_and_text(self):
        write(self.root, PYAPP)
        p = self.run_cli(self.root, "--json", "--layers", "ui>services>db")
        self.assertEqual(p.returncode, 0, p.stderr)
        d = json.loads(p.stdout)
        self.assertEqual(d["schema_version"], 1)
        self.assertEqual([x["rank"] for x in d["problems"]], list(range(1, len(d["problems"]) + 1)))
        t = self.run_cli(self.root, "--top", "2", "--layers", "ui>services>db")
        self.assertEqual(t.returncode, 0)
        for heading in ("Top problems", "Modules", "Cycles:", "God files", "Hubs", "Layer violations", "Notes:"):
            self.assertIn(heading, t.stdout)
        self.assertIn("more (use --top or --json)", t.stdout)

    def test_errors_exit_2(self):
        self.assertEqual(self.run_cli(os.path.join(self.dir, "missing")).returncode, 2)
        self.assertEqual(self.run_cli(self.root).returncode, 2)                     # no source files
        write(self.root, {"a.py": ""})
        p = self.run_cli(self.root, "--layers", "onlyone")
        self.assertEqual(p.returncode, 2)
        self.assertIn("--layers", p.stderr)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main([self.root, "--days", "0"]), 2)
        self.assertIn("must be positive", err.getvalue())

    def test_healthy_code_reports_no_problems(self):
        write(self.root, {"app/main.py": "from app import util\n", "app/util.py": "X = 1\n", "app/__init__.py": ""})
        p = self.run_cli(self.root, "--no-git")
        self.assertEqual(p.returncode, 0)
        self.assertIn("Top problems: none found", p.stdout)
        self.assertIn("git history not used", p.stdout)


class VendoredCopy(unittest.TestCase):
    def test_code_map_matches_codebase_explainer(self):
        # Only inside the agent-skills repo; an installed copy of this skill has no sibling.
        original = os.path.join(HERE, "..", "..", "codebase-explainer", "scripts", "code_map.py")
        if not os.path.exists(original):
            self.skipTest("codebase-explainer is not next to this skill")
        with open(original) as f:
            want = f.read().splitlines()
        with open(os.path.join(HERE, "code_map.py")) as f:
            got = f.read().splitlines()
        self.assertEqual(got[:1] + got[2:], want, "re-copy code_map.py from codebase-explainer")


if __name__ == "__main__":
    unittest.main()
