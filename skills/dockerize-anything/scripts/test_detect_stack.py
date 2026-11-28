#!/usr/bin/env python3
"""Tests for detect_stack.py (stdlib unittest, no network, no git).

Run: python3 -m unittest discover -s skills/dockerize-anything/scripts -p 'test_*.py'
Each test writes a small project into a temp dir and checks what the script reports about it.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import detect_stack as ds  # noqa: E402

SCRIPT = os.path.join(HERE, "detect_stack.py")
SENTINEL = "zq" + "sentinel" + "731"  # stands in for a value that must never be printed


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def reset(self):
        self.tmp.cleanup()
        self.setUp()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, files):
        for rel, text in files.items():
            full = os.path.join(self.root, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as fh:
                fh.write(text)

    def scan(self, files=None):
        if files:
            self.write(files)
        return ds.scan(self.root)

    def app(self, r, d=".", lang=None):
        return next(a for a in r["apps"] if a["dir"] == d and (lang is None or a["language"] == lang))

    def rules(self, r, path="Dockerfile"):
        return [f["rule"] for f in next(d for d in r["dockerfiles"] if d["path"] == path)["findings"]]

    def cli(self, *args):
        return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True)


class NodeTests(Case):
    def test_versions_lockfile_and_scripts(self):
        r = self.scan({".nvmrc": "v20.11.0\n", "package.json": json.dumps(
            {"engines": {"node": ">=20"}, "scripts": {"start": "node server.js", "build": "tsc"},
             "dependencies": {"pg": "^8", "ioredis": "^5"}}), "package-lock.json": "{}"})
        a = self.app(r)
        self.assertIn({"name": "node", "value": "v20.11.0", "source": ".nvmrc"}, a["runtime"])
        self.assertIn({"name": "node", "value": ">=20", "source": "package.json engines"}, a["runtime"])
        self.assertEqual(a["packages"]["install"], "npm ci")
        self.assertEqual([c["command"] for c in a["commands"]], ["npm run start", "npm run build"])
        self.assertEqual(a["services"], ["postgres", "redis"])

    def test_lockfile_picks_manager_and_frozen_install(self):
        cases = {"pnpm-lock.yaml": ("", "pnpm install --frozen-lockfile"),
                 "bun.lock": ("{}", "bun install --frozen-lockfile"),
                 "yarn.lock": ("# yarn lockfile v1\n", "yarn install --frozen-lockfile")}
        for lock, (text, install) in cases.items():
            with self.subTest(lock=lock):
                self.reset()
                r = self.scan({"package.json": "{}", lock: text})
                self.assertEqual(self.app(r)["packages"]["install"], install)

    def test_yarn_berry_from_lockfile_or_package_manager(self):
        r = self.scan({"package.json": "{}", "yarn.lock": "__metadata:\n  version: 8\n"})
        self.assertEqual(self.app(r)["packages"]["install"], "yarn install --immutable")
        self.reset()
        r = self.scan({"package.json": json.dumps({"packageManager": "yarn@4.1.0"}), "yarn.lock": ""})
        self.assertEqual(self.app(r)["packages"]["install"], "yarn install --immutable")

    def test_workspace_lockfile_at_root_and_monorepo(self):
        r = self.scan({"package.json": json.dumps({"workspaces": ["apps/*"]}), "pnpm-lock.yaml": "",
                       "apps/web/package.json": "{}", "apps/api/package.json": "{}"})
        self.assertTrue(r["monorepo"])
        self.assertEqual(r["app_dirs"], [".", "apps/api", "apps/web"])
        self.assertEqual(self.app(r, "apps/web")["packages"]["lockfile"], "pnpm-lock.yaml")
        self.assertIn(".: package.json workspaces: apps/*", r["workspace"])

    def test_port_default_env_names_and_tests_skipped(self):
        r = self.scan({"package.json": "{}", "src/index.js":
                       "const { DB_HOST, DB_PORT: dbPort } = process.env\n"
                       "const port = process.env.PORT || 3001\napp.listen(port)\n"
                       "const k = process.env['API_BASE']\n",
                       "src/app.test.js": "process.env.ONLY_IN_TEST; app.listen(9999)\n"})
        a = self.app(r)
        self.assertEqual([p["port"] for p in a["ports"]], [3001])
        self.assertEqual(a["ports"][0]["source"], "src/index.js:2")
        self.assertEqual([e["name"] for e in a["env"]], ["API_BASE", "DB_HOST", "DB_PORT", "PORT"])


class PythonTests(Case):
    def test_versions_uv_and_fastapi(self):
        r = self.scan({".python-version": "3.12\n", "uv.lock": "",
                       "svc/pyproject.toml": '[project]\nname = "svc"\nrequires-python = ">=3.11"  # floor\n'
                                             'dependencies = [\n  "fastapi>=0.110",\n  "asyncpg",\n]\n'
                                             '[tool.fastapi]\nentrypoint = "app.main:app"\n',
                       "svc/app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n"
                                          "if __name__ == '__main__':\n    uvicorn.run(app, port=8100)\n",
                       "svc/app/config.py": "class Settings(BaseSettings):\n    db_url: str\n"})
        a = self.app(r, "svc")
        self.assertIn({"name": "python", "value": "3.12", "source": ".python-version"}, a["runtime"])
        self.assertIn({"name": "python", "value": ">=3.11", "source": "svc/pyproject.toml requires-python"}, a["runtime"])
        self.assertEqual(a["packages"]["install"], "uv sync --locked")
        cmds = [c["command"] for c in a["commands"]]
        self.assertIn("fastapi run", cmds)
        self.assertIn("uvicorn app.main:app", cmds)
        self.assertEqual(a["ports"][0]["port"], 8100)
        self.assertTrue(a["env_hints"][0].startswith("svc/app/config.py:1 class Settings(BaseSettings)"))
        self.assertEqual(a["services"], ["postgres"])

    def test_poetry_and_pipenv(self):
        r = self.scan({"pyproject.toml": '[tool.poetry.dependencies]\npython = "^3.11"\nredis = "^5"\n',
                       "poetry.lock": ""})
        a = self.app(r)
        self.assertEqual(a["packages"]["install"], "poetry install --no-root --only main")
        self.assertIn("^3.11", [v["value"] for v in a["runtime"]])
        self.assertEqual(a["services"], ["redis"])
        self.reset()
        r = self.scan({"Pipfile": '[packages]\npymongo = "*"\n[requires]\npython_version = "3.11"\n', "Pipfile.lock": "{}"})
        a = self.app(r)
        self.assertEqual(a["packages"]["install"], "pipenv sync")
        self.assertEqual(a["services"], ["mongodb"])

    def test_django_procfile_and_env_reads(self):
        r = self.scan({"manage.py": "", "requirements.txt": "Django==5.0\ngunicorn==22.0\n",
                       "Procfile": "web: gunicorn site.wsgi --bind 0.0.0.0:$PORT\nworker: celery -A site worker\n",
                       "site/wsgi.py": "", "site/settings.py":
                       "KEY = os.environ.get(\n    'DJANGO_KEY',\n)\nON = 'DYNO' in os.environ\n"
                       "P = os.getenv('PORT', '8080')\nip = request.environ['REMOTE_ADDR']\n"})
        a = self.app(r)
        self.assertEqual(a["commands"][0]["source"], "Procfile:1")
        self.assertEqual(a["commands"][0]["confidence"], "high")
        self.assertIn("gunicorn site.wsgi:application", [c["command"] for c in a["commands"]])
        self.assertEqual([e["name"] for e in a["env"]], ["DJANGO_KEY", "DYNO", "PORT"])
        self.assertEqual(next(e for e in a["env"] if e["name"] == "DJANGO_KEY")["source"], "site/settings.py:1")
        self.assertIn(8080, [p["port"] for p in a["ports"]])
        self.assertIn("Procfile:1", [p["source"] for p in a["ports"] if p["port"] is None])

    def test_toml_subset(self):
        t = ds.parse_toml('# c\n[project]\n"name" = "x" # trailing\nrequires-python = \'>=3.9\'\n'
                          'dependencies = [\n "a>=1", # one\n "b[extra]",\n]\n[tool.x.y]\nflag = true\n'
                          'doc = """\nmulti\n"""\n')
        self.assertEqual(t["project"]["name"], "x")
        self.assertEqual(t["project"]["requires-python"], ">=3.9")
        self.assertEqual(t["project"]["dependencies"], ["a>=1", "b[extra]"])
        self.assertIs(t["tool.x.y"]["flag"], True)


class GoTests(Case):
    def test_go_mod_main_packages_and_ports(self):
        r = self.scan({"go.mod": "module example.com/svc\n\ngo 1.22\n\ntoolchain go1.22.3\n\nrequire (\n"
                                 "\tgithub.com/jackc/pgx/v5 v5.5.0\n\tgo.mongodb.org/mongo-driver v1.0.0 // indirect\n)\n",
                       "go.sum": "", "cmd/api/main.go": 'package main\n\nfunc main() {\n\thttp.ListenAndServe(":8081", nil)\n}\n',
                       "cmd/worker/main.go": 'package main\n\nfunc main() {\n\tport := os.Getenv("PORT")\n'
                                             '\tif port == "" {\n\t\tport = "9090"\n\t}\n}\n',
                       "internal/x.go": "package internal\n"})
        a = self.app(r)
        self.assertIn({"name": "go", "value": "1.22", "source": "go.mod go"}, a["runtime"])
        self.assertIn({"name": "toolchain", "value": "go1.22.3", "source": "go.mod toolchain"}, a["runtime"])
        self.assertEqual([c["command"] for c in a["commands"]],
                         ["CGO_ENABLED=0 go build -o /out/api ./cmd/api", "CGO_ENABLED=0 go build -o /out/worker ./cmd/worker"])
        self.assertEqual(sorted((p["port"], p["source"]) for p in a["ports"]),
                         [(8081, "cmd/api/main.go:4"), (9090, "cmd/worker/main.go:6")])
        self.assertEqual(a["services"], ["postgres"])  # the indirect mongo driver is not counted

    def test_port_env_without_default(self):
        r = self.scan({"go.mod": "module x\n\ngo 1.21\n", "main.go":
                       'package main\n\nfunc main() {\n\tport := os.Getenv("PORT")\n\tr.Run(":" + port)\n}\n'})
        p = self.app(r)["ports"]
        self.assertEqual(p, [{"port": None, "env": "PORT", "source": "main.go:4", "confidence": "high",
                              "how": "reads $PORT, no default found"}])


class SecrecyTests(Case):
    def test_values_never_printed(self):
        self.write({"package.json": "{}", ".env": "DB_PASS" + "=" + SENTINEL + "\n",
                    ".env.example": "DB_" + "PASS=" + SENTINEL + "\nPORT=\n",
                    "index.js": "const p = process.env.DB_PASS || '%s'\n" % SENTINEL})
        for args in ([self.root], [self.root, "--json"]):
            out = self.cli(*args)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertNotIn(SENTINEL, out.stdout)
        r = json.loads(self.cli(self.root, "--json").stdout)
        self.assertEqual(r["env_files"], [".env"])
        self.assertEqual(self.app(r)["env_templates"], [{"file": ".env.example", "names": ["DB_PASS", "PORT"]}])


class DockerfileTests(Case):
    def lint(self, dockerfile, extra=None):
        files = {"Dockerfile": dockerfile, ".dockerignore": ".git\n"}
        files.update(extra or {})
        return self.rules(self.scan(files))

    def test_base_image_tags(self):
        rules = self.lint("ARG BASE=python:3.12-slim\nFROM node AS a\nFROM node:latest AS b\nFROM ${BASE} AS c\n"
                          "FROM a\nFROM scratch\nFROM alpine@sha256:" + "0" * 64 + "\nFROM ${UNSET}\nUSER app\n")
        self.assertEqual(rules.count("base-image-unpinned"), 2)

    def test_apt_rules_and_exemptions(self):
        rules = self.lint("FROM debian:12 AS build\nRUN apt-get install -y gcc\n"
                          "FROM debian:12\nRUN apt-get update\nRUN apt-get update && apt-get install -y curl\n"
                          "RUN --mount=type=cache,target=/var/lib/apt apt-get install -y git\n"
                          "RUN apt-get update && apt-get install -y jq && rm -rf /var/lib/apt/lists/*\nUSER app\n")
        self.assertEqual(rules.count("apt-update-alone"), 1)
        self.assertEqual(rules.count("apt-lists-not-removed"), 1)  # build stage and cache mount are exempt

    def test_add_local_only(self):
        rules = self.lint("FROM alpine:3.20\nADD https://example.com/x.tgz /x\nADD vendor.tar.gz /v\n"
                          "ADD src /src\nUSER app\n")
        self.assertEqual(rules.count("add-local-file"), 1)

    def test_user_rules(self):
        self.assertIn("no-user", self.lint("FROM python:3.12-slim\nCMD [\"python\"]\n"))
        self.assertIn("no-user", self.lint("FROM python:3.12-slim\nUSER app\nUSER root\n"))
        self.assertNotIn("no-user", self.lint("FROM python:3.12 AS base\nUSER 1000\nFROM base\nCMD [\"x\"]\n"))
        self.assertNotIn("no-user", self.lint("FROM gcr.io/distroless/static:nonroot\n"))

    def test_copy_order(self):
        bad = self.lint("FROM node:20\nCOPY . .\nRUN npm ci\nUSER node\n")
        good = self.lint("FROM python:3.12\nCOPY uv.lock pyproject.toml ./\nRUN uv sync --frozen --no-install-project\n"
                         "COPY . .\nRUN uv sync --locked\nUSER app\n")
        self.assertIn("copy-all-before-install", bad)
        self.assertNotIn("copy-all-before-install", good)
        # a tool install before COPY . . is not the dependency install, and a build script after it is not one either
        tool_first = self.lint("FROM node:20\nRUN npm install -g pnpm\nCOPY . .\nRUN pnpm install\nUSER node\n")
        build_only = self.lint("FROM node:20\nCOPY . .\nRUN yarn build\nUSER node\n")
        self.assertIn("copy-all-before-install", tool_first)
        self.assertNotIn("copy-all-before-install", build_only)
        self.assertTrue(ds.is_install("pip install --no-cache-dir -r requirements.txt"))
        self.assertFalse(ds.is_install("pip install --upgrade pip"))

    def test_shell_form_and_parser(self):
        r = self.scan({"Dockerfile": "# escape=`\nFROM alpine:3.20\nRUN apk add `\n    curl\n"
                                     "RUN <<EOF\napt-get update\nEOF\nUSER app\nCMD node server.js\n",
                       ".dockerignore": ".git\n"})
        f = r["dockerfiles"][0]["findings"]
        self.assertEqual([(x["rule"], x["line"]) for x in f], [("apt-update-alone", 5), ("shell-form-cmd", 9)])
        insts = ds.parse_dockerfile("FROM a:1\nRUN echo 1 \\\n  # note\n  && echo 2\nCMD [\"x\"]\n")
        self.assertEqual([(ln, i) for ln, i, _ in insts], [(1, "FROM"), (2, "RUN"), (5, "CMD")])
        insts = ds.parse_dockerfile("FROM a:1\nRUN cat <<<NOPE\nRUN echo <<END\nUSER x\n")  # no terminator
        self.assertEqual([i for _, i, _ in insts], ["FROM", "RUN", "RUN", "USER"])

    def test_dockerfile_in_pruned_dir_and_guessed_context(self):
        r = self.scan({"go.mod": "module x\n\ngo 1.22\n", "build/package/Dockerfile": "FROM golang:1.22\nCOPY . .\nUSER a\n"})
        d = r["dockerfiles"][0]
        self.assertEqual((d["path"], d["context"]), ("build/package/Dockerfile", "build/package"))
        self.assertEqual([(f["rule"], f["confidence"]) for f in d["findings"]], [("no-dockerignore", "medium")])


class DockerignoreTests(Case):
    def test_matcher_semantics(self):
        pats = ds.ignore_patterns("# c\nnode_modules\n/build/\n**/*.log\n*.md\n!README.md\n")
        self.assertTrue(ds.ignored("node_modules/x/y.js", pats))
        self.assertFalse(ds.ignored("web/node_modules", pats))  # patterns are anchored at the context root
        self.assertTrue(ds.ignored("build", pats))
        self.assertTrue(ds.ignored("a/b/c.log", pats))
        self.assertFalse(ds.ignored("README.md", pats))
        self.assertTrue(ds.ignored("CHANGES.md", pats))
        self.assertFalse(ds.ignored("docs/x.md", pats))

    def test_missing_and_incomplete_ignore(self):
        os.makedirs(os.path.join(self.root, ".git"))
        r = self.scan({"package.json": "{}", ".env.local": "", "Dockerfile": "FROM node:20\nUSER node\n"})
        f = r["dockerfiles"][0]["findings"]
        self.assertEqual([x["rule"] for x in f], ["no-dockerignore"])
        self.assertIn(".env.local", f[0]["message"])
        self.write({".dockerignore": "node_modules\n"})
        self.assertEqual(sorted(self.rules(ds.scan(self.root))), ["dockerignore-misses-env", "dockerignore-misses-git"])

    def test_context_from_copy_sources_and_specific_ignore(self):
        os.makedirs(os.path.join(self.root, ".git"))
        r = self.scan({"uv.lock": "", "pyproject.toml": "", ".dockerignore": ".git\n", "backend/.dockerignore": "",
                       "backend/Dockerfile": "FROM python:3.12\nCOPY uv.lock pyproject.toml ./\nCOPY ./backend /app\nUSER a\n"})
        d = r["dockerfiles"][0]
        self.assertEqual((d["context"], d["dockerignore"], d["findings"]), (".", ".dockerignore", []))
        self.write({"backend/Dockerfile.dockerignore": "*.pyc\n"})
        d = ds.scan(self.root)["dockerfiles"][0]
        self.assertEqual(d["dockerignore"], "backend/Dockerfile.dockerignore")
        self.assertEqual([x["rule"] for x in d["findings"]], ["dockerignore-misses-git"])


class CliTests(Case):
    def test_exit_codes(self):
        self.assertEqual(self.cli(os.path.join(self.root, "missing")).returncode, 2)
        self.assertEqual(self.cli(self.root).returncode, 2)
        self.write({"go.mod": "module x\n\ngo 1.22\n"})
        out = self.cli(self.root)
        self.assertEqual(out.returncode, 0)
        self.assertIn("go 1.22   <- go.mod go", out.stdout)
        self.assertNotIn(self.root, out.stdout)  # no absolute paths in the report


if __name__ == "__main__":
    unittest.main()
