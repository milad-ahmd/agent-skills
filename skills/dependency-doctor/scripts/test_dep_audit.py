#!/usr/bin/env python3
"""Tests for dep_audit.py (stdlib unittest, no network).

Run: python3 -m unittest discover -s skills/dependency-doctor/scripts -p 'test_*.py'
Each test writes a small made-up project into a temp folder (manifests, source files, lockfiles) and checks
what the audit reports. Lockfile fixtures carry no integrity or checksum values beyond short fake ones.
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
import dep_audit as da  # noqa: E402

SCRIPT = os.path.join(HERE, "dep_audit.py")


class Project(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self._tmp.name, "proj")
        os.makedirs(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, files, base=None):
        for rel, body in files.items():
            path = os.path.join(base or self.root, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                fh.write(body if isinstance(body, str) else json.dumps(body))

    def audit(self, files=None):
        if files:
            self.write(files)
        return da.audit(self.root)

    def find(self, rep, kind, name):
        return [f for f in rep["findings"] if f["type"] == kind and f["name"] == name]

    def implicit(self, rep):
        return {i["name"]: i["reason"] for i in rep["implicit"]}


class NpmTests(Project):
    def test_unused_used_undeclared_and_builtins(self):
        rep = self.audit({
            "package.json": {"dependencies": {"left-pad": "1.3.0", "lodash": "4.17.21", "@acme/ui": "2.0.0",
                                              "chalk": "5.3.0", "zod": "3.22.0", "events": "3.3.0"}},
            "package-lock.json": {"lockfileVersion": 3, "packages": {"": {}}},
            "src/index.js": textwrap.dedent("""\
                import { merge } from 'lodash/merge';
                import Button from "@acme/ui/button";
                const chalk = require('chalk');
                export { z } from 'zod';
                const fs = require('node:fs');
                import path from 'path';
                import EventEmitter from 'events';
                import axios from 'axios';
                const lazy = () => import('dayjs');
                import local from './local';
                """)})
        unused = {f["name"] for f in rep["findings"] if f["type"] == "unused"}
        self.assertEqual(unused, {"left-pad"})
        self.assertEqual(self.find(rep, "unused", "left-pad")[0]["confidence"], "high")
        undeclared = {f["name"]: f["confidence"] for f in rep["findings"] if f["type"] == "undeclared"}
        self.assertEqual(undeclared, {"axios": "high", "dayjs": "high"})

    def test_dev_dependency_in_runtime_code(self):
        rep = self.audit({
            "package.json": {"dependencies": {"express": "4.19.0"},
                             "devDependencies": {"debug": "4.3.4", "jest": "29.7.0", "type-fest": "4.0.0"}},
            "package-lock.json": {"lockfileVersion": 3, "packages": {}},
            "src/server.js": "const express = require('express');\nconst debug = require('debug');\n",
            "src/types.ts": "import type { Simplify } from 'type-fest';\n",
            "src/server.test.js": "const { describe } = require('jest');\n"})
        hits = self.find(rep, "dev-in-runtime", "debug")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["confidence"], "medium")
        self.assertEqual(hits[0]["files"], ["src/server.js"])
        self.assertFalse(self.find(rep, "dev-in-runtime", "type-fest"))
        self.assertFalse(self.find(rep, "dev-in-runtime", "jest"))

    def test_bundled_app_lowers_dev_in_runtime(self):
        rep = self.audit({
            "package.json": {"devDependencies": {"vite": "5.0.0", "react": "18.3.1"}},
            "package-lock.json": {"lockfileVersion": 3, "packages": {"node_modules/vite": {"version": "5.0.0"},
                                                                     "node_modules/react": {"version": "18.3.1"}}},
            "vite.config.js": "import { defineConfig } from 'vite';\nexport default defineConfig({});\n",
            "src/main.jsx": "import React from 'react';\n"})
        self.assertEqual(self.find(rep, "dev-in-runtime", "react")[0]["confidence"], "low")
        self.assertFalse(any(f["counts"] for f in rep["findings"]))

    def test_scripts_configs_types_and_strings_are_implicit(self):
        rep = self.audit({
            "package.json": {"scripts": {"build": "tsc -p .", "lint": "eslint ."},
                             "dependencies": {"pino": "9.0.0", "pino-pretty": "11.0.0"},
                             "devDependencies": {"typescript": "5.4.0", "eslint": "9.0.0",
                                                 "eslint-plugin-react": "7.34.0", "@types/node": "20.0.0",
                                                 "jest-environment-jsdom": "29.7.0", "stylelint": "16.0.0"}},
            "package-lock.json": {"lockfileVersion": 3, "packages": {}},
            ".eslintrc.json": {"plugins": ["react"]},
            "jest.config.js": "module.exports = { testEnvironment: 'jsdom' };\n",
            "src/log.js": "const pino = require('pino');\nmodule.exports = pino({ transport: { target: 'pino-pretty' } });\n"})
        imp = self.implicit(rep)
        self.assertIn("scripts", imp["typescript"])
        self.assertIn("scripts", imp["eslint"])
        self.assertIn(".eslintrc.json", imp["eslint-plugin-react"])
        self.assertIn("jest.config.js", imp["jest-environment-jsdom"])
        self.assertIn("node", imp["@types/node"])
        self.assertIn("string", imp["pino-pretty"])
        stylelint = self.find(rep, "unused", "stylelint")
        self.assertEqual(stylelint[0]["confidence"], "low")

    def test_aliases_workspaces_and_css_imports(self):
        rep = self.audit({
            "package.json": {"name": "root", "workspaces": ["packages/*"], "dependencies": {"react": "18.3.1"},
                             "devDependencies": {"tw-animate-css": "1.0.0"}},
            "package-lock.json": {"lockfileVersion": 3, "packages": {}},
            "tsconfig.json": '{\n  // comment\n  "compilerOptions": {"paths": {"@app/*": ["src/*"]}}\n}\n',
            "vite.config.ts": "export default { resolve: { alias: { '@assets': '/attached_assets' } } };\n",
            "src/a.tsx": "import x from '@app/thing';\nimport logo from '@assets/logo.png';\nimport u from '@acme/utils';\n",
            "src/index.css": '@import "tw-animate-css";\n@use "variables";\n',
            "packages/utils/package.json": {"name": "@acme/utils", "dependencies": {"ms": "2.1.3"}},
            "packages/utils/index.js": "const React = require('react');\nconst left = require('left-pad');\n"})
        self.assertEqual({f["name"] for f in rep["findings"] if f["type"] == "undeclared"}, {"left-pad"})
        self.assertEqual(self.find(rep, "undeclared", "left-pad")[0]["manifest"], "packages/utils/package.json")
        self.assertFalse(self.find(rep, "unused", "tw-animate-css"))
        self.assertFalse(self.find(rep, "unused", "react"))
        self.assertTrue(self.find(rep, "unused", "ms"))


class LockfileTests(Project):
    def test_package_lock_versions_duplicates_and_missing(self):
        rep = self.audit({
            "package.json": {"dependencies": {"debug": "^4.3.0", "ms": "^2.1.0", "ghost": "^1.0.0"}},
            "package-lock.json": {"lockfileVersion": 3, "packages": {
                "": {"dependencies": {"debug": "^4.3.0"}},
                "node_modules/debug": {"version": "4.3.4"},
                "node_modules/ms": {"version": "2.1.3"},
                "node_modules/debug/node_modules/ms": {"version": "2.1.2"},
                "node_modules/@acme/lib": {"resolved": "packages/lib", "link": True}}},
            "index.js": "require('debug'); require('ms'); require('ghost');\n"})
        dep = {d["name"]: d for d in rep["dependencies"]}
        self.assertEqual(dep["debug"]["locked"], ["4.3.4"])
        self.assertEqual(dep["ms"]["locked"], ["2.1.2", "2.1.3"])
        self.assertEqual([(d["name"], d["direct"]) for d in rep["duplicates"]], [("ms", True)])
        self.assertTrue(self.find(rep, "missing-from-lock", "ghost"))
        self.assertFalse(self.find(rep, "unlocked", "package.json"))

    def test_v1_lock_counts_as_lock_but_is_not_parsed(self):
        rep = self.audit({"package.json": {"dependencies": {"ms": "^2.1.0"}},
                          "package-lock.json": {"lockfileVersion": 1, "dependencies": {"ms": {"version": "2.1.3"}}},
                          "index.js": "require('ms');\n"})
        self.assertFalse(rep["lockfiles"][0]["parsed"])
        self.assertIn("v1", rep["lockfiles"][0]["note"])
        self.assertEqual(rep["findings"], [])

    def test_yarn_classic_and_berry(self):
        text = textwrap.dedent('''\
            # yarn lockfile v1


            "@babel/core@^7.0.0", "@babel/core@^7.1.0":
              version "7.1.2"
              resolved "https://registry.example/core.tgz"

            ms@^2.0.0:
              version "2.0.0"

            ms@^2.1.1:
              version "2.1.3"
            ''')
        data, note = da.lock_yarn(text)
        self.assertEqual(note, "v1")
        self.assertEqual(dict(data), {"@babel/core": {"7.1.2"}, "ms": {"2.0.0", "2.1.3"}})
        data, note = da.lock_yarn("__metadata:\n  version: 6\n")
        self.assertIsNone(data)
        self.assertIn("berry", note)

    def test_pnpm_skips_toolchain_document_and_peer_suffixes(self):
        text = textwrap.dedent("""\
            ---
            lockfileVersion: '9.0'

            importers:

              .:
                packageManagerDependencies:
                  pnpm:
                    specifier: 9.0.0
                    version: 9.0.0

            packages:

              '@pnpm/exe@9.0.0':
                resolution: {integrity: fake}

            ---
            lockfileVersion: '9.0'

            importers:

              .:
                dependencies:
                  react-dom:
                    specifier: ^18.0.0
                    version: 18.3.1(react@18.3.1)

            packages:

              react-dom@18.3.1:
                resolution: {integrity: fake}

              '@scope/pkg@1.0.0':
                resolution: {integrity: fake}

              '@scope/pkg@2.0.0':
                resolution: {integrity: fake}

            snapshots:

              react-dom@18.3.1(react@18.3.1):
                dependencies:
                  react: 18.3.1
            """)
        data, note = da.lock_pnpm(text)
        self.assertEqual(note, "v9.0")
        self.assertEqual(dict(data), {"react-dom": {"18.3.1"}, "@scope/pkg": {"1.0.0", "2.0.0"}})
        data, note = da.lock_pnpm("lockfileVersion: '6.0'\n\nimporters:\n\n  .:\n    dependencies:\n")
        self.assertIsNone(data)
        self.assertIn("6.0", note)

    def test_parent_lockfile_only_covers_workspace_members(self):
        rep = self.audit({
            "package.json": {"workspaces": ["packages/*"], "dependencies": {"ms": "2.1.3"}},
            "package-lock.json": {"lockfileVersion": 3, "packages": {"": {}, "packages/a": {},
                                                                     "node_modules/ms": {"version": "2.1.3"}}},
            "index.js": "require('ms');\n",
            "packages/a/package.json": {"dependencies": {"debug": "4.3.4"}},
            "packages/a/index.js": "require('debug');\n",
            "examples/demo/package.json": {"dependencies": {"chalk": "5.3.0"}},
            "examples/demo/index.js": "require('chalk');\n"})
        self.assertTrue(self.find(rep, "missing-from-lock", "debug"))
        self.assertFalse(self.find(rep, "missing-from-lock", "chalk"))
        self.assertTrue(any("examples/demo is not a workspace member" in n for n in rep["notes"]))

    def test_build_output_skipped_only_next_to_a_project_file(self):
        rep = self.audit({"package.json": {"dependencies": {"ms": "2.1.3"}},
                          "package-lock.json": {"lockfileVersion": 3, "packages": {"node_modules/ms": {"version": "2.1.3"}}},
                          "dist/bundle.js": "require('ms'); require('left-pad');\n",
                          "src/build/index.js": "require('ms');\n"})
        self.assertEqual(rep["findings"], [])
        self.assertEqual(rep["files_scanned"]["js_ts"], 1)

    def test_ranges_without_lockfile(self):
        rep = self.audit({"package.json": {"dependencies": {"ms": "^2.1.0", "debug": "4.3.4"}},
                          "index.js": "require('ms'); require('debug');\n"})
        hit = self.find(rep, "unlocked", "package.json")
        self.assertEqual(hit[0]["confidence"], "medium")
        self.assertIn("ms", hit[0]["detail"])
        self.assertNotIn("debug", hit[0]["detail"])

    def test_lockfile_found_at_repo_root_but_not_outside_a_repo(self):
        mono = os.path.join(self._tmp.name, "mono")
        self.write({".git/HEAD": "ref: refs/heads/main\n", "yarn.lock": "# yarn lockfile v1\n",
                    "apps/web/package.json": "{}"}, base=mono)
        self.assertTrue(da.find_lock(os.path.join(mono, "apps", "web"), "", da.NPM_LOCKS).endswith("yarn.lock"))
        self.write({"yarn.lock": "# stray\n"}, base=self._tmp.name)
        self.assertIsNone(da.find_lock(self.root, "", da.NPM_LOCKS))

    def test_poetry_and_uv_locks(self):
        lock = textwrap.dedent('''\
            version = 1
            requires-python = ">=3.9"

            [[package]]
            name = "Requests"
            version = "2.31.0"
            source = { registry = "https://pypi.org/simple" }
            dependencies = [
                { name = "idna" },
            ]

            [[package]]
            name = "idna"
            version = "3.6"

            [[package]]
            name = "idna"
            version = "3.7"

            [package.optional-dependencies]
            all = [{ name = "x" }]
            ''')
        data, _ = da.lock_toml(lock)
        self.assertEqual(dict(data), {"requests": {"2.31.0"}, "idna": {"3.6", "3.7"}})
        rep = self.audit({"pyproject.toml": '[project]\nname = "app"\ndependencies = ["requests>=2", "flask>=3"]\n',
                          "uv.lock": lock, "app.py": "import requests\nimport flask\n"})
        self.assertTrue(self.find(rep, "missing-from-lock", "flask"))
        self.assertFalse(self.find(rep, "unlocked", "pyproject.toml"))
        self.assertEqual([d["name"] for d in rep["duplicates"]], ["idna"])


class PythonTests(Project):
    def test_import_names_stdlib_local_and_guarded(self):
        rep = self.audit({
            "requirements.txt": "PyYAML==6.0.1\nPillow==10.0.0  # images\nbeautifulsoup4==4.12.3 \\\n"
                                "    --hash=sha256:fake\nrequests==2.31.0\n-r other.txt\n--index-url https://x.example\n",
            "app/__init__.py": "",
            "app/main.py": textwrap.dedent("""\
                import os, json
                import yaml
                from PIL import Image
                from bs4 import BeautifulSoup
                from . import helpers
                from app import util
                import numpy as np
                try:
                    import ujson
                except ImportError:
                    ujson = None
                """),
            "app/util.py": "", "app/helpers.py": ""})
        self.assertEqual({f["name"] for f in rep["findings"] if f["type"] == "unused"}, {"requests"})
        undeclared = {f["name"]: f["confidence"] for f in rep["findings"] if f["type"] == "undeclared"}
        self.assertEqual(undeclared, {"numpy": "high", "ujson": "low"})

    def test_dev_requirements_and_implicit_tools(self):
        rep = self.audit({
            "requirements.txt": "flask==3.0.0\n",
            "requirements-dev.txt": "pytest==8.0.0\npytest-cov==5.0.0\nfreezegun==1.5.0\nruff==0.4.0\n",
            "requirements-docker.txt": "gunicorn==22.0.0\n",
            "pyproject.toml": "[tool.ruff]\nline-length = 100\n",
            "app.py": "import flask\nimport freezegun\nimport gunicorn\n",
            "tests/test_app.py": "import pytest\n"})
        self.assertEqual(self.find(rep, "dev-in-runtime", "freezegun")[0]["files"], ["app.py"])
        self.assertFalse(self.find(rep, "dev-in-runtime", "gunicorn"))  # "docker" is not a dev file name
        self.assertEqual(self.find(rep, "unused", "pytest-cov")[0]["confidence"], "low")
        self.assertIn("ruff", self.implicit(rep))
        self.assertFalse([f for f in rep["findings"] if f["type"] == "undeclared"])

    def test_dev_group_imported_from_app_and_settings_strings(self):
        rep = self.audit({
            "pyproject.toml": textwrap.dedent('''\
                [project]
                name = "shop"
                dependencies = [
                    "django>=5",  # web
                    "django-cors-headers>=4",
                ]

                [dependency-groups]
                dev = ["rich>=13"]
                '''),
            "poetry.lock": '[[package]]\nname = "django"\nversion = "5.0"\n\n[[package]]\nname = "django-cors-headers"\n'
                           'version = "4.3"\n\n[[package]]\nname = "rich"\nversion = "13.7"\n',
            "shop/settings.py": "INSTALLED_APPS = ['django.contrib.admin', 'corsheaders']\n",
            "shop/views.py": "from django.http import HttpResponse\nfrom rich import print\n"})
        self.assertTrue(self.find(rep, "dev-in-runtime", "rich"))
        self.assertIn("string", self.implicit(rep)["django-cors-headers"])
        self.assertFalse(self.find(rep, "unused", "django"))

    def test_declared_package_wins_over_same_named_local_file(self):
        rep = self.audit({"requirements.txt": "celery==5.4.0\n",
                          "proj/celery.py": "from celery import Celery\napp = Celery('proj')\n"})
        self.assertEqual(rep["findings"], [])

    def test_python_without_manifest(self):
        rep = self.audit({"package.json": {"dependencies": {}}, "tool.py": "import requests\nimport os\n"})
        hit = self.find(rep, "no-manifest", "requests")
        self.assertEqual(len(hit), 1)


class TomlTests(unittest.TestCase):
    SAMPLE = textwrap.dedent('''\
        # comment
        [project]
        name = "demo"
        dependencies = [
          "httpx>=0.27",   # trailing comment
          'attrs',
        ]
        [project.optional-dependencies]
        test = ["pytest>=8"]
        docs = []

        [tool.poetry.dependencies]
        python = "^3.9"
        pendulum = { version = "3.0.0", optional = true }
        mylib = { path = "../mylib", develop = true }

        [tool.poetry.group.lint.dependencies]
        black = "24.1.0"

        [[tool.poetry.source]]
        name = "mirror"
        url = "https://pypi.example/simple"
        ''')

    def test_mini_reader_subset(self):
        data = da.mini_toml(self.SAMPLE)
        self.assertEqual(data["project"]["dependencies"], ["httpx>=0.27", "attrs"])
        self.assertEqual(data["tool"]["poetry"]["dependencies"]["pendulum"], {"version": "3.0.0", "optional": "true"})
        self.assertEqual(data["tool"]["poetry"]["source"][0]["name"], "mirror")
        entries = {e[0]: e[2:] for e in da.read_pyproject(data)}
        self.assertEqual(entries["httpx"], ("prod", "", False))
        self.assertEqual(entries["pytest"], ("dev", "test", False))
        self.assertEqual(entries["pendulum"][0], "optional")
        self.assertEqual(entries["mylib"], ("prod", "", True))
        self.assertEqual(entries["black"], ("dev", "lint", True))
        self.assertNotIn("python", entries)

    @unittest.skipUnless(da.tomllib, "tomllib needs Python 3.11+")
    def test_mini_reader_agrees_with_tomllib_on_entries(self):
        self.assertEqual(da.read_pyproject(da.mini_toml(self.SAMPLE)), da.read_pyproject(da.tomllib.loads(self.SAMPLE)))


class GoTests(Project):
    GO_MOD = textwrap.dedent("""\
        module example.com/app

        go 1.24

        require (
        \tgithub.com/acme/log v1.2.0
        \tgithub.com/acme/unused v0.3.0
        \tgithub.com/acme/yaml v1.0.0
        \tgithub.com/acme/yaml/v2 v2.1.0
        \tgithub.com/acme/deep v0.1.0 // indirect
        \texample.com/shared v0.0.0
        )

        require github.com/acme/gen v1.0.0

        tool github.com/acme/gen/cmd/gen

        replace example.com/shared => ../shared
        """)

    def test_go_imports_requires_and_sums(self):
        rep = self.audit({
            "go.mod": self.GO_MOD,
            "go.sum": "github.com/acme/log v1.2.0 h1:fake=\ngithub.com/acme/log v1.2.0/go.mod h1:fake=\n"
                      "github.com/acme/yaml v1.0.0/go.mod h1:fake=\ngithub.com/acme/yaml/v2 v2.1.0/go.mod h1:fake=\n"
                      "github.com/acme/unused v0.3.0/go.mod h1:fake=\ngithub.com/acme/deep v0.1.0/go.mod h1:fake=\n",
            "main.go": textwrap.dedent("""\
                package main

                import (
                \t"fmt"
                \tlg "github.com/acme/log/sub"
                \t_ "github.com/acme/yaml"
                \t"github.com/acme/yaml/v2" // second major
                \t"example.com/app/internal/x"
                \t"github.com/other/thing"
                )
                """),
            "internal/x/x.go": 'package x\n\nimport "example.com/shared/util"\n'})
        self.assertEqual({f["name"] for f in rep["findings"] if f["type"] == "unused"}, {"github.com/acme/unused"})
        self.assertEqual({f["name"] for f in rep["findings"] if f["type"] == "undeclared"}, {"github.com/other/thing"})
        self.assertEqual({f["name"] for f in rep["findings"] if f["type"] == "missing-from-lock"}, {"github.com/acme/gen"})
        self.assertEqual(self.implicit(rep), {"github.com/acme/gen": "tool directive"})
        self.assertEqual([(d["name"], len(d["versions"])) for d in rep["duplicates"]], [("github.com/acme/yaml", 2)])

    def test_nested_module_uses_its_own_go_mod(self):
        rep = self.audit({"go.mod": "module example.com/root\n\nrequire github.com/acme/log v1.0.0\n",
                          "go.sum": "github.com/acme/log v1.0.0/go.mod h1:fake=\n",
                          "main.go": 'package main\n\nimport "github.com/acme/log"\n',
                          "tools/go.mod": "module example.com/tools\n",
                          "tools/main.go": 'package main\n\nimport "github.com/acme/log"\n'})
        hit = self.find(rep, "undeclared", "github.com/acme/log")
        self.assertEqual(hit[0]["manifest"], "tools/go.mod")
        self.assertFalse(self.find(rep, "unused", "github.com/acme/log"))


class HelperTests(unittest.TestCase):
    def test_js_package(self):
        cases = {"lodash": "lodash", "lodash/fp/map": "lodash", "@scope/pkg/sub/path": "@scope/pkg",
                 "node:fs": "node:", "./x": None, "#internal": None, "@/components": None, "virtual:pwa": None,
                 "~/utils": None, "style-loader!./a.css": None, "react?raw": "react", "@scope": None}
        for spec, want in cases.items():
            self.assertEqual(da.js_package(spec), want, spec)

    def test_requirement_parsing(self):
        got = da.read_requirements("Django[argon2]>=4.2,<5 ; python_version >= '3.10'\n-e .\n./local.whl\n"
                                   "pkg @ https://example.com/pkg.whl\nhttps://example.com/x.whl\n# only a comment\n")
        self.assertEqual(got, [("Django", ">=4.2,<5"), ("pkg", "@ https://example.com/pkg.whl")])
        self.assertTrue(da.is_pinned_pep508("==1.2.3"))
        self.assertFalse(da.is_pinned_pep508("==1.2.*"))
        self.assertFalse(da.is_pinned_pep508(">=1"))


class CliTests(Project):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, timeout=60)

    def test_exit_codes_and_json(self):
        self.write({"package.json": {"dependencies": {"ms": "2.1.3"}}, "index.js": "require('ms');\n",
                    "package-lock.json": {"lockfileVersion": 3, "packages": {"node_modules/ms": {"version": "2.1.3"}}}})
        r = self.run_cli("-C", self.root, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        rep = json.loads(r.stdout)
        self.assertEqual(rep["findings"], [])
        self.assertIn("npm audit --json", rep["next_commands"])
        self.write({"index.js": "require('ms'); require('left-pad');\n"})
        r = self.run_cli("-C", self.root)
        self.assertEqual(r.returncode, 1)
        self.assertIn("undeclared", r.stdout)
        self.assertIn("left-pad", r.stdout)

    def test_nothing_to_read(self):
        self.assertEqual(self.run_cli("-C", self.root).returncode, 2)
        self.assertEqual(self.run_cli("-C", os.path.join(self.root, "missing")).returncode, 2)
        self.assertEqual(self.run_cli("--bogus").returncode, 2)


if __name__ == "__main__":
    unittest.main()
