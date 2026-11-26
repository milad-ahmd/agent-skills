#!/usr/bin/env python3
"""Tests for weigh_in.py (stdlib unittest, no network, never runs npm, pip or go).

Run: python3 -m unittest discover -s skills/dependency-weigh-in/scripts -p 'test_*.py'
Each test builds a synthetic install in a temp dir: an npm tree with nested duplicates and a
package-lock v3, a pnpm .pnpm layout with top-level symlinks, a venv with dist-info RECORD files,
and a fake Go module cache.
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
import weigh_in as w  # noqa: E402

SCRIPT = os.path.join(HERE, "weigh_in.py")
KB = 1000


def write(path, data=b"", size=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if isinstance(data, str):
        data = data.encode()
    with open(path, "wb") as fh:
        fh.write(data if size is None else b"x" * size)


def pkg(base, name, version, size, deps=None, **extra):
    meta = dict(name=name, version=version, dependencies=deps or {}, **extra)
    write(os.path.join(base, "package.json"), json.dumps(meta))
    write(os.path.join(base, "index.js"), size=size)
    return os.path.getsize(os.path.join(base, "package.json")) + size


class Tmp(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def p(self, *parts):
        return os.path.join(self.root, *parts)

    def run_cli(self, *args, env=None):
        return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, timeout=60,
                              env=env or os.environ)


class NpmBase(Tmp):
    """app -> express -> debug@2 (nested), app -> mocha (dev) -> debug@4 -> ms, moment in dependencies."""

    def setUp(self):
        super().setUp()
        nm = self.p("node_modules")
        write(self.p("package.json"), json.dumps({
            "name": "app", "dependencies": {"express": "^4", "moment": "^2", "typescript": "^5"},
            "devDependencies": {"mocha": "^10"}}))
        self.express = pkg(os.path.join(nm, "express"), "express", "4.19.0", 50 * KB, {"debug": "2.6.9"})
        self.debug2 = pkg(os.path.join(nm, "express", "node_modules", "debug"), "debug", "2.6.9", 20 * KB)
        self.debug4 = pkg(os.path.join(nm, "debug"), "debug", "4.3.4", 30 * KB, {"ms": "^2"})
        self.ms = pkg(os.path.join(nm, "ms"), "ms", "2.1.3", 5 * KB)
        self.mocha = pkg(os.path.join(nm, "mocha"), "mocha", "10.0.0", 200 * KB, {"debug": "^4"})
        self.moment = pkg(os.path.join(nm, "moment"), "moment", "2.30.1", 400 * KB)
        self.ts = pkg(os.path.join(nm, "typescript"), "typescript", "5.4.0", 10 * KB)
        self.types = pkg(os.path.join(nm, "@types", "node"), "@types/node", "20.0.0", 15 * KB)
        write(os.path.join(nm, ".package-lock.json"), "{}")

    def lock(self):
        packages = {"": {"name": "app"},
                    "node_modules/express": {"version": "4.19.0"},
                    "node_modules/express/node_modules/debug": {"version": "2.6.9"},
                    "node_modules/debug": {"version": "4.3.4", "dev": True},
                    "node_modules/ms": {"version": "2.1.3", "dev": True},
                    "node_modules/mocha": {"version": "10.0.0", "dev": True},
                    "node_modules/moment": {"version": "2.30.1"},
                    "node_modules/typescript": {"version": "5.4.0"},
                    "node_modules/@types/node": {"version": "20.0.0", "dev": True}}
        write(self.p("package-lock.json"), json.dumps({"lockfileVersion": 3, "packages": packages}))

    def node(self, top=20):
        return w.weigh(self.root, top=top)["ecosystems"][0]


class NpmTree(NpmBase):
    def test_finds_nested_and_scoped_packages(self):
        e = self.node()
        paths = {r["path"] for r in e["heaviest"]}
        self.assertIn("node_modules/express/node_modules/debug", paths)
        self.assertIn("node_modules/@types/node", paths)
        self.assertEqual(e["packages"], 8)
        self.assertEqual(e["manager"], "npm")

    def test_own_size_excludes_nested_node_modules(self):
        row = next(r for r in self.node()["heaviest"] if r["name"] == "express")
        self.assertEqual(row["bytes"], self.express)

    def test_total_counts_every_file_once(self):
        e = self.node()
        parts = self.express + self.debug2 + self.debug4 + self.ms + self.mocha + self.moment + self.ts + self.types
        self.assertEqual(e["bytes"], parts + 2)  # + .package-lock.json
        self.assertEqual(e["unattributed_bytes"], 2)

    def test_duplicate_versions(self):
        d = self.node()["duplicates"]
        self.assertEqual([x["name"] for x in d], ["debug"])
        self.assertEqual(d[0]["versions"], ["2.6.9", "4.3.4"])
        self.assertEqual(d[0]["extra_bytes"], min(self.debug2, self.debug4))

    def test_dev_only_from_lockfile_v3(self):
        self.lock()
        e = self.node()
        self.assertIn("package-lock.json v3", e["dev_source"])
        self.assertEqual(e["dev_only_bytes"], self.debug4 + self.ms + self.mocha + self.types)

    def test_dev_only_from_graph_without_lockfile(self):
        e = self.node()
        self.assertIn("dependency graph", e["dev_source"])
        # @types/node is not reachable from any prod dependency, so the graph calls it dev-only
        self.assertEqual(e["dev_only_bytes"], self.debug4 + self.ms + self.mocha + self.types)

    def test_lockfile_v1_falls_back_with_note(self):
        write(self.p("package-lock.json"), json.dumps({"lockfileVersion": 1, "dependencies": {}}))
        e = self.node()
        self.assertTrue(any("lockfile v1" in n for n in e["notes"]))

    def test_pulled_in_by_chain(self):
        ms = next(r for r in self.node()["heaviest"] if r["name"] == "ms")
        self.assertEqual(ms["pulled_in_by"], ["mocha", "debug", "ms"])
        self.assertFalse(ms["direct"])

    def test_heaviest_direct_includes_closure(self):
        mocha = next(d for d in self.node()["heaviest_direct"] if d["name"] == "mocha")
        self.assertEqual(mocha["kind"], "dev")
        # resolved like Node does: mocha finds the hoisted debug@4, not express's nested debug@2
        self.assertEqual(mocha["bytes"], self.mocha + self.debug4 + self.ms)
        self.assertEqual(mocha["packages"], 3)
        express = next(d for d in self.node()["heaviest_direct"] if d["name"] == "express")
        self.assertEqual(express["bytes"], self.express + self.debug2)

    def test_dev_tools_listed_under_dependencies(self):
        self.assertEqual(self.node()["dev_tools_in_dependencies"], ["typescript"])

    def test_suggests_alternative_for_moment(self):
        s = self.node()["suggestions"]
        self.assertEqual([x["package"] for x in s], ["moment"])
        self.assertEqual(s[0]["bytes"], self.moment)
        self.assertIn("dayjs", s[0]["instead"])

    def test_symlinked_package_not_counted(self):
        os.symlink(self.p("node_modules", "moment"), self.p("node_modules", "moment-link"))
        e = self.node()
        self.assertEqual(e["packages"], 8)

    def test_build_cache_noted(self):
        write(self.p("node_modules", ".vite", "deps", "chunk.js"), size=1200 * KB)
        e = self.node()
        self.assertIn("node_modules/.vite is a tool cache, not a package: 1.2 MB", e["notes"])
        self.assertEqual(e["unattributed_bytes"], 1200 * KB + 2)

    def test_top_limits_rows(self):
        self.assertEqual(len(self.node(top=3)["heaviest"]), 3)


class PnpmLayout(Tmp):
    def setUp(self):
        super().setUp()
        nm = self.p("node_modules")
        store = os.path.join(nm, ".pnpm")
        write(self.p("package.json"), json.dumps({"name": "app", "dependencies": {"foo": "1", "@scope/bar": "2"}}))
        self.foo = pkg(os.path.join(store, "foo@1.0.0", "node_modules", "foo"), "foo", "1.0.0", 100 * KB,
                       {"qar": "^1"})
        self.bar = pkg(os.path.join(store, "@scope+bar@2.0.0", "node_modules", "@scope", "bar"), "@scope/bar",
                       "2.0.0", 40 * KB, {"qar": "^2"})
        self.qar1 = pkg(os.path.join(store, "qar@1.0.0", "node_modules", "qar"), "qar", "1.0.0", 10 * KB)
        self.qar2 = pkg(os.path.join(store, "qar@2.0.0", "node_modules", "qar"), "qar", "2.0.0", 12 * KB)
        # dependency edges and top-level entries are symlinks
        os.symlink("../../qar@1.0.0/node_modules/qar", os.path.join(store, "foo@1.0.0", "node_modules", "qar"))
        os.symlink("../../qar@2.0.0/node_modules/qar", os.path.join(store, "@scope+bar@2.0.0", "node_modules", "qar"))
        os.symlink(".pnpm/foo@1.0.0/node_modules/foo", os.path.join(nm, "foo"))
        os.makedirs(os.path.join(nm, "@scope"))
        os.symlink("../.pnpm/@scope+bar@2.0.0/node_modules/@scope/bar", os.path.join(nm, "@scope", "bar"))
        write(os.path.join(store, "lock.yaml"), "lockfileVersion: '9.0'\n")

    def test_reads_store_once(self):
        e = w.weigh(self.root)["ecosystems"][0]
        self.assertEqual(e["manager"], "pnpm")
        self.assertEqual(e["packages"], 4)
        names = sorted(r["name"] for r in e["heaviest"])
        self.assertEqual(names, ["@scope/bar", "foo", "qar", "qar"])
        self.assertEqual(e["bytes"], self.foo + self.bar + self.qar1 + self.qar2 + len("lockfileVersion: '9.0'\n"))
        self.assertEqual(e["dedupe_command"], "pnpm dedupe")

    def test_duplicates_and_chain(self):
        e = w.weigh(self.root)["ecosystems"][0]
        self.assertEqual(e["duplicates"][0]["name"], "qar")
        qar = next(r for r in e["heaviest"] if (r["name"], r["version"]) == ("qar", "2.0.0"))
        self.assertEqual(qar["pulled_in_by"], ["@scope/bar", "qar"])

    def test_hard_links_are_reported(self):
        foo = os.path.join(self.p("node_modules", ".pnpm", "foo@1.0.0", "node_modules", "foo"), "index.js")
        os.link(foo, self.p("store-copy.js"))  # stands in for pnpm's content-addressable store
        e = w.weigh(self.root)["ecosystems"][0]
        self.assertEqual(e["hard_linked"], 100 * KB)
        self.assertTrue(any("more than one hard link" in n for n in e["notes"]))


class Venv(Tmp):
    def dist(self, sp, name, version, files, requires=()):
        info = os.path.join(sp, "%s-%s.dist-info" % (name.replace("-", "_"), version))
        meta = "Metadata-Version: 2.1\nName: %s\nVersion: %s\n" % (name, version)
        meta += "".join("Requires-Dist: %s\n" % r for r in requires) + "\nlong description\nName: wrong\n"
        write(os.path.join(info, "METADATA"), meta)
        rows = []
        for rel, size in files.items():
            write(os.path.normpath(os.path.join(sp, rel)), size=size)
            rows.append("%s,sha256=abc,%d" % (rel, size))
        rows.append("%s/METADATA,," % os.path.basename(info))
        rows.append("%s/RECORD,," % os.path.basename(info))
        rows.append("gone/missing.py,sha256=x,10")
        write(os.path.join(info, "RECORD"), "\n".join(rows) + "\n")
        return sum(files.values()) + len(meta) + os.path.getsize(os.path.join(info, "RECORD"))

    def setUp(self):
        super().setUp()
        write(self.p(".venv", "pyvenv.cfg"), "home = /usr/bin\n")
        sp = self.p(".venv", "lib", "python3.11", "site-packages")
        self.torch = self.dist(sp, "torch", "2.5.0", {"torch/__init__.py": 300 * KB},
                               ["nvidia-cublas-cu12 ; platform_system == 'Linux'", "filelock"])
        self.cublas = self.dist(sp, "nvidia-cublas-cu12", "12.4.5", {"nvidia/cublas/lib.so": 500 * KB})
        self.filelock = self.dist(sp, "filelock", "3.16.1", {"filelock/__init__.py": 2 * KB},
                                  ["pytest ; extra == \"testing\""])
        self.pytest = self.dist(sp, "pytest", "8.3.0", {"pytest/__init__.py": 20 * KB,
                                                         "../../../bin/pytest": 1 * KB})
        write(os.path.join(sp, "loose.pth"), size=7)

    def py(self):
        return w.weigh(self.root)["ecosystems"][0]

    def test_sizes_from_record(self):
        e = self.py()
        self.assertEqual(e["ecosystem"], "python")
        sizes = {r["name"]: r["bytes"] for r in e["heaviest"]}
        self.assertEqual(sizes, {"torch": self.torch, "nvidia-cublas-cu12": self.cublas,
                                 "filelock": self.filelock, "pytest": self.pytest})
        self.assertEqual(e["unattributed_bytes"], 7)

    def test_bin_scripts_count_toward_total(self):
        e = self.py()
        self.assertEqual(e["bytes"], self.torch + self.cublas + self.filelock + self.pytest + 7)

    def test_required_by_skips_extras(self):
        rows = {r["name"]: r for r in self.py()["heaviest"]}
        self.assertEqual(rows["nvidia-cublas-cu12"]["required_by"], ["torch"])
        self.assertEqual(rows["filelock"]["required_by"], ["torch"])
        self.assertEqual(rows["pytest"]["required_by"], [])

    def test_python_suggestions(self):
        s = self.py()["suggestions"]
        self.assertEqual(s[0]["package"], "nvidia-*")
        self.assertEqual(s[0]["matched"], ["nvidia-cublas-cu12"])

    def test_dist_info_name_fallback(self):
        sp = self.p("solo")
        info = os.path.join(sp, "my_pkg-1.0.dist-info")
        write(os.path.join(info, "RECORD"), "my_pkg-1.0.dist-info/RECORD,,\n")
        d = w.read_dist(info, sp)
        self.assertEqual((d["name"], d["version"]), ("my_pkg", "1.0"))


class GoCache(Tmp):
    def test_module_cache_with_case_encoding(self):
        cache = self.p("modcache")
        write(self.p("proj", "go.mod"), "module example.com/app\n\ngo 1.22\n\nrequire (\n"
              "\tgithub.com/BurntSushi/toml v1.4.0\n\tgolang.org/x/text v0.20.0 // indirect\n)\n")
        write(self.p("proj", "go.sum"),
              "github.com/BurntSushi/toml v1.4.0 h1:aaa=\ngithub.com/BurntSushi/toml v1.4.0/go.mod h1:bbb=\n"
              "golang.org/x/text v0.20.0 h1:ccc=\ngolang.org/x/text v0.20.0/go.mod h1:ddd=\n"
              "golang.org/x/mod v0.1.0/go.mod h1:eee=\n")
        write(os.path.join(cache, "github.com", "!burnt!sushi", "toml@v1.4.0", "decode.go"), size=30 * KB)
        write(os.path.join(cache, "golang.org", "x", "text@v0.20.0", "x.go"), size=90 * KB)
        env = dict(os.environ, GOMODCACHE=cache)
        r = self.run_cli(self.p("proj"), "--json", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        e = json.loads(r.stdout)["ecosystems"][0]
        self.assertEqual([(m["name"], m["direct"]) for m in e["heaviest"]],
                         [("golang.org/x/text", False), ("github.com/BurntSushi/toml", True)])
        self.assertEqual(e["bytes"], 120 * KB)

    def test_go_skipped_without_toolchain(self):
        write(self.p("go.mod"), "module x\n")
        env = dict(os.environ, PATH=self.p("empty-bin"))
        env.pop("GOMODCACHE", None)
        r = self.run_cli(self.root, env=env)
        self.assertEqual(r.returncode, 2)
        self.assertIn("go is not on PATH", r.stderr)


class Comparisons(unittest.TestCase):
    def test_floppy_math(self):
        rows, head = w.compare(1474560 * 10)
        floppy = next(r for r in rows if r["reference"] == "floppy disks")
        self.assertAlmostEqual(floppy["count"], 10.0)
        self.assertEqual(head["reference"], "copies of shareware Doom")

    def test_headline_picks_largest_reference_that_fits(self):
        self.assertTrue(w.headline(737280000 * 3).startswith("about 3.0 CD-ROMs (or 1,500 floppy disks)"))
        self.assertEqual(w.headline(2 * 1474560), "about 2.0 floppy disks")
        self.assertEqual(w.headline(100), "lighter than one Super Mario Bros. cartridge")

    def test_apollo_figure(self):
        agc = next(b for b, s, _ in w.REFERENCES if "Apollo" in s)
        self.assertEqual(agc, (36864 + 2048) * 16 // 8)

    def test_weight_classes(self):
        self.assertEqual(w.weight_class(999), "strawweight")
        self.assertEqual(w.weight_class(150e6), "middleweight")
        self.assertEqual(w.weight_class(2e9), "black hole")

    def test_human_sizes(self):
        self.assertEqual(w.human(999), "999 B")
        self.assertEqual(w.human(1500000), "1.5 MB")


class Cli(NpmBase):
    def test_svg_is_well_formed(self):
        out = self.p("card.svg")
        r = self.run_cli(self.root, "--svg", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        root = ET.parse(out).getroot()
        self.assertTrue(root.tag.endswith("svg"))
        texts = " ".join(t.text or "" for t in root.iter("{http://www.w3.org/2000/svg}text"))
        self.assertIn("DEPENDENCY WEIGH-IN", texts)
        self.assertIn("moment", texts)

    def test_text_and_json(self):
        r = self.run_cli(self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("pulled in by mocha > debug > ms", r.stdout)
        self.assertIn("weight class: STRAWWEIGHT", r.stdout)
        data = json.loads(self.run_cli(self.root, "--json").stdout)
        self.assertEqual(data["ecosystems"][0]["ecosystem"], "node")

    def test_nothing_installed_exits_2(self):
        empty = self.p("empty")
        os.makedirs(empty)
        write(os.path.join(empty, "package.json"), "{}")
        r = self.run_cli(empty)
        self.assertEqual(r.returncode, 2)
        self.assertIn("Install the dependencies first", r.stderr)
        self.assertEqual(r.stdout, "")

    def test_yarn_pnp_note(self):
        pnp = self.p("pnp")
        write(os.path.join(pnp, ".pnp.cjs"), "//")
        r = self.run_cli(pnp)
        self.assertEqual(r.returncode, 2)
        self.assertIn("Plug'n'Play", r.stderr)


if __name__ == "__main__":
    unittest.main()
