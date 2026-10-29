#!/usr/bin/env python3
"""Tests for code_map.py (stdlib unittest, no network).

Run: python3 -m unittest discover -s skills/codebase-explainer/scripts -p 'test_*.py'
Fixtures: fixtures/pyapp (relative imports, an import-time cycle and a lazy one), fixtures/tsapp
(tsconfig with comments, path aliases, index resolution, require and import()), fixtures/goapp (go.mod).
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from code_map import CodeMap, build, load_jsonc, render_html, render_mermaid  # noqa: E402

SCRIPT = os.path.join(HERE, "code_map.py")
FX = os.path.join(HERE, "fixtures")


def model(name, **kw):
    return build(CodeMap(os.path.join(FX, name)).analyse(), **kw)


def edges(m):
    return {(e["source"], e["target"]) for e in m["edges"]}


def write(root, files):
    for rel, text in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)


class Parse(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.scripts, self._in = [], [], None

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        if tag == "script":
            self._in = dict(attrs)
            self.scripts.append([dict(attrs), ""])

    def handle_endtag(self, tag):
        if tag == "script":
            self._in = None

    def handle_data(self, data):
        if self._in is not None:
            self.scripts[-1][1] += data


class PythonFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = model("pyapp")

    def test_relative_and_absolute_edges(self):
        e = edges(self.m)
        for pair in [("mypkg/__init__.py", "mypkg/core.py"),         # from .core import run
                     ("mypkg/core.py", "mypkg/utils.py"),            # from . import utils (submodule)
                     ("mypkg/sub/deep.py", "mypkg/core.py"),         # from .. import core
                     ("mypkg/sub/deep.py", "mypkg/utils.py"),        # from ..utils import helper
                     ("mypkg/models.py", "mypkg/lazy.py"),           # from mypkg import lazy
                     ("main.py", "mypkg/core.py"),
                     ("tests/test_core.py", "mypkg/core.py")]:
            self.assertIn(pair, e)
        self.assertNotIn(("mypkg/core.py", "os.py"), e)

    def test_external_counts_third_party_only(self):
        self.assertEqual([x["name"] for x in self.m["external"]], ["requests"])
        core = next(f for f in self.m["files"] if f["path"] == "mypkg/core.py")
        self.assertEqual(core["external"], ["requests"])

    def test_lazy_edges(self):
        lazy = {(e["source"], e["target"]) for e in self.m["edges"] if e["lazy"]}
        self.assertIn(("mypkg/lazy.py", "mypkg/models.py"), lazy)   # import inside a function
        self.assertIn(("mypkg/lazy.py", "mypkg/core.py"), lazy)     # under TYPE_CHECKING
        self.assertNotIn(("mypkg/core.py", "mypkg/utils.py"), lazy)

    def test_cycles(self):
        cycles = {tuple(c["members"]): c for c in self.m["cycles"]["files"]}
        self.assertEqual(set(cycles), {("mypkg/core.py", "mypkg/utils.py"), ("mypkg/lazy.py", "mypkg/models.py")})
        self.assertTrue(cycles[("mypkg/core.py", "mypkg/utils.py")]["eager"])
        self.assertFalse(cycles[("mypkg/lazy.py", "mypkg/models.py")]["eager"])
        ex = cycles[("mypkg/core.py", "mypkg/utils.py")]["example"]
        self.assertEqual(ex[0], ex[-1])
        cyc_edges = {(e["source"], e["target"]) for e in self.m["edges"] if e["cycle"]}
        self.assertIn(("mypkg/utils.py", "mypkg/core.py"), cyc_edges)
        self.assertNotIn(("main.py", "mypkg/core.py"), cyc_edges)

    def test_entry_points(self):
        entries = {e["path"]: e["reasons"] for e in self.m["entry_points"]}
        self.assertIn("__main__ guard", entries["main.py"])
        self.assertIn("pyproject.toml script", entries["mypkg/cli.py"])
        self.assertNotIn("tests/test_core.py", entries)
        with tempfile.TemporaryDirectory() as t:
            write(t, {"test_x.py": "import unittest\nif __name__ == '__main__':\n    unittest.main()\n"})
            self.assertEqual(build(CodeMap(t).analyse())["entry_points"], [])

    def test_hubs_and_orphans(self):
        hub = self.m["hubs"][0]
        self.assertEqual(hub["path"], "mypkg/core.py")
        self.assertEqual(hub["test_fan_in"], 1)
        self.assertIn("orphan.py", self.m["orphans"])
        self.assertNotIn("mypkg/__init__.py", self.m["orphans"])
        self.assertNotIn("main.py", self.m["orphans"])

    def test_module_level(self):
        m = model("pyapp", depth=1)
        ids = {r["id"] for r in m["modules"]}
        self.assertEqual(ids, {".", "mypkg", "tests"})
        me = {(e["source"], e["target"]): e["weight"] for e in m["module_edges"]}
        self.assertEqual(me[(".", "mypkg")], 1)
        pkg = next(r for r in m["modules"] if r["id"] == "mypkg")
        self.assertEqual(pkg["fan_in"], 2)
        self.assertEqual(pkg["external"], {"requests": 1})
        self.assertGreater(pkg["loc"], 0)


class TypeScriptFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = model("tsapp")

    def test_alias_index_and_extension_resolution(self):
        e = edges(self.m)
        for pair in [("src/index.ts", "src/lib/index.ts"),   # '@/lib' alias + index resolution
                     ("src/index.ts", "src/util.js"),        # export * from './util'
                     ("src/lib/index.ts", "src/lib/math.ts"),
                     ("src/util.js", "src/lib/math.ts"),     # require('./lib/math')
                     ("src/esm.ts", "src/lib/math.ts"),      # './lib/math.js' -> math.ts
                     ("src/esm.ts", "src/lib/index.ts"),     # exact alias '@lib'
                     ("src/server.ts", "src/lazy.tsx"),      # import('./lazy')
                     ("bin/cli.js", "src/index.ts")]:
            self.assertIn(pair, e)

    def test_comments_ignored_and_externals(self):
        self.assertEqual(self.m["unresolved"], [])
        self.assertEqual(sorted(x["name"] for x in self.m["external"]), ["@tanstack/query", "react"])

    def test_dynamic_and_type_imports_are_lazy(self):
        kinds = {(e["source"], e["target"]): (e["kind"], e["lazy"]) for e in self.m["edges"]}
        self.assertEqual(kinds[("src/server.ts", "src/lazy.tsx")], ("dynamic", True))
        self.assertEqual(kinds[("src/esm.ts", "src/lib/index.ts")], ("type", True))
        self.assertEqual(kinds[("src/util.js", "src/lib/math.ts")], ("require", False))

    def test_package_json_entries(self):
        entries = {e["path"]: e["reasons"] for e in self.m["entry_points"]}
        self.assertIn("package.json bin", entries["bin/cli.js"])
        self.assertIn("package.json main", entries["src/index.ts"])
        self.assertIn("package.json script dev", entries["src/server.ts"])

    def test_jsonc(self):
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "x.json")
            with open(p, "w") as f:
                f.write('{"a": "http://x//y", /* c */ "b": [1, 2,], // tail\n}')
            self.assertEqual(load_jsonc(p), {"a": "http://x//y", "b": [1, 2]})


class GoFixture(unittest.TestCase):
    def test_go_module_resolution(self):
        m = model("goapp")
        e = edges(m)
        self.assertIn(("cmd/server/main.go", "internal/store/store.go"), e)
        self.assertIn(("cmd/server/main.go", "internal/store/cache.go"), e)
        self.assertIn(("cmd/server/main.go", "internal/handlers/h.go"), e)
        self.assertIn(("internal/store/cache.go", "internal/models/m.go"), e)
        self.assertNotIn(("cmd/server/main.go", "internal/store/store_test.go"), e)
        self.assertEqual([x["name"] for x in m["external"]], ["github.com/pkg/errors"])
        self.assertEqual([x["path"] for x in m["entry_points"]], ["cmd/server/main.go"])
        self.assertIn("go package main", m["entry_points"][0]["reasons"])
        self.assertEqual(m["cycles"]["files"], [])


class Outputs(unittest.TestCase):
    ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

    def check_mermaid(self, text):
        lines = text.strip().splitlines()
        self.assertEqual(lines[0], "flowchart LR")
        declared, depth = set(), 0
        for ln in lines[1:]:
            s = ln.strip()
            if s.startswith("%%") or s.startswith(("classDef", "class ", "linkStyle")):
                continue
            if s.startswith("subgraph "):
                depth += 1
                self.assertRegex(s, r'^subgraph [A-Za-z_][A-Za-z0-9_]*\["[^"]*"\]$')
                continue
            if s == "end":
                depth -= 1
                continue
            node = re.match(r'^([^\s\[]+)\["([^"]*)"\]$', s)
            if node:
                self.assertRegex(node.group(1), self.ID)
                declared.add(node.group(1))
                continue
            edge = re.match(r"^(\S+) (-->|-\.->)(\|\d+\|)? (\S+)$", s)
            self.assertTrue(edge, "unexpected mermaid line: %r" % s)
            self.assertIn(edge.group(1), declared)
            self.assertIn(edge.group(4), declared)
        self.assertEqual(depth, 0)

    def test_mermaid_valid_and_ids_sanitized(self):
        with tempfile.TemporaryDirectory() as t:
            write(t, {"end/a.py": "import graph.b\n", "graph/b.py": "from my_mod import c\n",
                      'my-mod v2/"q".py': "import end.a\n", "my_mod/__init__.py": "", "my_mod/c.py": "x = 1\n",
                      "x/y/z.ts": "import '../../end/w';\n", "end/w.ts": "export {};\n"})
            m = build(CodeMap(t).analyse(), depth=1)
            for level in ("module", "file"):
                out = render_mermaid(m, level)
                self.check_mermaid(out)
            self.assertNotIn('"q"', render_mermaid(m, "file"))
        for name in ("pyapp", "tsapp", "goapp"):
            self.check_mermaid(render_mermaid(model(name)))
            self.check_mermaid(render_mermaid(model(name), "file"))

    def test_mermaid_trims_and_marks_cycles(self):
        m = model("pyapp")
        out = render_mermaid(m, "file", max_nodes=4, max_edges=3)
        self.check_mermaid(out)
        self.assertLessEqual(len(re.findall(r" (?:-->|-\.->)", out)), 3)
        self.assertIn("showing", out)
        self.assertIn("linkStyle", render_mermaid(m, "file"))

    def test_mermaid_focus(self):
        out = render_mermaid(model("pyapp"), focus="mypkg/sub")
        self.check_mermaid(out)
        self.assertIn("deep.py", out)
        self.assertNotIn("core.py", out)

    def test_scanning_a_package_folder(self):
        m = build(CodeMap(os.path.join(FX, "pyapp", "mypkg")).analyse())
        self.assertIn(("models.py", "lazy.py"), edges(m))   # from mypkg import lazy
        self.assertNotIn("mypkg", [x["name"] for x in m["external"]])

    def test_html_self_contained_and_escaped(self):
        with tempfile.TemporaryDirectory() as t:
            # a folder named "evil<" holding "script><img ...>.py" gives the path "evil</script><img ...>.py"
            write(t, {"a.py": "x = 1\n", "evil</script><img src=x onerror=alert(1)>.py": "import a\n"})
            m = build(CodeMap(t).analyse())
            self.assertTrue(any("</script>" in f["path"] for f in m["files"]))
            page = render_html(m)
        self.assertNotIn("</script><img", page)
        self.assertEqual(page.lower().count("</script>"), 2)
        p = Parse()
        p.feed(page)
        for tag, attrs in p.tags:
            for k in ("src", "href"):
                v = attrs.get(k) or ""
                self.assertFalse(re.match(r"^\s*(https?:)?//", v), "%s %s=%s" % (tag, k, v))
            self.assertNotIn(tag, ("link", "iframe", "img"))
        self.assertNotIn("http://", page)
        self.assertNotIn("https://", page)
        self.assertNotRegex(page, r"\bfetch\(|XMLHttpRequest|@import")
        blobs = [body for attrs, body in p.scripts if attrs.get("type") == "application/json"]
        self.assertEqual(len(blobs), 1)
        data = json.loads(blobs[0])
        self.assertTrue(any("</script>" in n["id"] for n in data["views"]["files"]["nodes"]))
        self.assertEqual(len([1 for attrs, _ in p.scripts if "src" in attrs]), 0)

    def test_html_css_sizes_only_the_graph(self):
        # a bare svg{height:100%} rule stretched the legend icons, and flex:1 beat height:65vh on phones
        css = render_html(model("pyapp")).split("<style>", 1)[1].split("</style>", 1)[0]
        self.assertNotRegex(css, r"(?m)^svg\s*\{")
        self.assertIn("#graph{width:100%;height:100%", css)
        self.assertRegex(css, r"@media \(max-width:760px\)\{[^@]*#wrap\{flex:none;height:65vh\}")

    def test_html_view_ranks(self):
        page = render_html(model("pyapp"))
        p = Parse()
        p.feed(page)
        data = json.loads(next(b for a, b in p.scripts if a.get("type") == "application/json"))
        files = data["views"]["files"]
        rank = {n["id"]: n["rank"] for n in files["nodes"]}
        self.assertLess(rank["main.py"], rank["mypkg/core.py"])
        self.assertEqual(rank["orphan.py"], -1)
        for e in files["edges"]:
            self.assertEqual(len(e), 5)

    def test_json_schema_stable(self):
        m = model("pyapp")
        self.assertEqual(set(m), {"schema_version", "root", "depth", "stats", "modules", "module_edges", "files",
                                  "edges", "entry_points", "hubs", "cycles", "orphans", "external", "unresolved",
                                  "not_visible"})
        self.assertEqual(m["schema_version"], 1)
        self.assertEqual(set(m["stats"]), {"files", "loc", "languages", "modules", "edges", "module_edges",
                                           "external_packages", "unresolved", "file_cycles", "module_cycles",
                                           "skipped_large", "truncated", "parse_fallbacks"})
        self.assertEqual(set(m["files"][0]), {"path", "module", "lang", "loc", "fan_in", "test_fan_in", "fan_out",
                                              "entry", "test", "in_cycle", "external"})
        self.assertEqual(set(m["modules"][0]), {"id", "files", "loc", "languages", "fan_in", "fan_out", "external",
                                                "entry_points", "in_cycle"})
        self.assertEqual(set(m["edges"][0]), {"source", "target", "kind", "lazy", "cycle"})
        self.assertEqual(set(m["module_edges"][0]), {"source", "target", "weight", "lazy", "cycle"})
        self.assertEqual(set(m["hubs"][0]), {"path", "fan_in", "test_fan_in", "loc", "in_cycle"})
        self.assertEqual(set(m["cycles"]), {"files", "modules"})
        self.assertEqual(set(m["cycles"]["files"][0]), {"members", "size", "eager", "example"})
        self.assertEqual(set(m["entry_points"][0]), {"path", "reasons"})
        self.assertEqual(set(m["external"][0]), {"name", "lang", "files"})
        json.dumps(m)


class Cli(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True)

    def test_exit_2_when_nothing_to_analyse(self):
        with tempfile.TemporaryDirectory() as t:
            write(t, {"README.md": "# hi\n", "node_modules/x/index.js": "require('y')\n"})
            self.assertEqual(self.run_cli(t).returncode, 2)
            self.assertEqual(self.run_cli(os.path.join(t, "missing")).returncode, 2)

    def test_formats_and_exclude(self):
        r = self.run_cli(os.path.join(FX, "pyapp"), "--format", "json", "--exclude", "tests/*,orphan.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        m = json.loads(r.stdout)
        paths = {f["path"] for f in m["files"]}
        self.assertNotIn("tests/test_core.py", paths)
        self.assertNotIn("orphan.py", paths)
        r = self.run_cli(os.path.join(FX, "goapp"))
        self.assertEqual(r.returncode, 0)
        self.assertIn("Entry points", r.stdout)
        self.assertIn("cmd/server/main.go", r.stdout)
        with tempfile.TemporaryDirectory() as t:
            out = os.path.join(t, "map.html")
            r = self.run_cli(os.path.join(FX, "tsapp"), "--format", "html", "--out", out)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(out) as f:
                self.assertTrue(f.read().startswith("<!doctype html>"))

    def test_skip_tests(self):
        m = build(CodeMap(os.path.join(FX, "pyapp"), skip_tests=True).analyse())
        self.assertFalse(any(f["test"] for f in m["files"]))


if __name__ == "__main__":
    unittest.main()
