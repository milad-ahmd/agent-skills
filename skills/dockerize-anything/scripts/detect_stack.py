#!/usr/bin/env python3
"""Collect the facts needed to containerize a project, and lint any Dockerfile already there. Stdlib only,
read-only, offline.

Usage: python3 detect_stack.py [PATH] [--json]
  PATH    project root to scan (default: current directory)
  --json  machine-readable report on stdout

What it reports, per app (a directory with package.json, pyproject.toml, requirements.txt, Pipfile, setup.py,
manage.py or go.mod):
  runtime      Node, Python and Go versions exactly as written in .nvmrc, .node-version, package.json engines,
               .python-version, pyproject requires-python, Pipfile, go.mod go/toolchain and .tool-versions,
               with the file each came from (looked up from the app dir towards the root). Ranges are not resolved.
  packages     package manager from the lockfile, and the install command that honours that lockfile
  commands     start/build commands from package.json scripts, Procfile, pyproject scripts, [tool.fastapi],
               FastAPI/Flask/Django entry files and Go main packages, each with a confidence level
  port         listen/run calls and PORT defaults with file:line; "reads $PORT, no default" when that is all
  env vars     NAMES the code reads (process.env, import.meta.env, os.environ, os.getenv, os.Getenv) and the
               names in .env.example-style templates. Values are never printed; real .env files are listed by
               path and never opened.
  services     Postgres, MySQL, Redis, MongoDB, RabbitMQ, Kafka from the declared dependencies
Repo-wide: workspace/monorepo declarations, compose files, and for each Dockerfile (or lone .dockerignore) a lint
of rules taken from Docker's own documentation (references/sources.md): base image without a tag or with
:latest, apt-get update alone, apt lists left in the final image, ADD for local files, no USER in the final
stage, COPY of the whole context before the dependency install, shell-form CMD/ENTRYPOINT, and a missing or
incomplete .dockerignore (.git, .env files, node_modules).

What it cannot see: values computed at runtime (a port read from a config object, env names built from
strings), libraries that read env themselves (dj-database-url reads DATABASE_URL), settings classes such as
pydantic BaseSettings (it points at them) or django-environ,
frameworks it does not know, and the real build context (it is inferred from COPY sources; compose files are
not parsed). JS/TS, Python and Go are read with regular expressions, not parsers, and pyproject.toml/Pipfile
with a small TOML subset reader. A base image may already set a non-root user; the USER finding says so.
Test files, node_modules, vendor, virtualenvs and build output are skipped; files over 1 MB are not read and
the walk stops after 20,000 files (the report says when).

Exit codes: 0 report written (lint findings included)
            2 PATH is not a directory, or no app manifest and no Dockerfile was found
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

MAX_FILES = 20000
MAX_BYTES = 1000000
SKIP_DIRS = {".git", "node_modules", "vendor", ".venv", "venv", "env", "__pycache__", "dist", "build", ".next",
             ".nuxt", "target", ".tox", ".mypy_cache", ".pytest_cache", "site-packages", "bower_components",
             ".yarn", ".pnpm-store", "coverage", "htmlcov", ".terraform", ".idea", ".vscode"}
DOCKERFILE_SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", "site-packages", "vendor", "bower_components"}
MANIFESTS = {"package.json": "node", "pyproject.toml": "python", "requirements.txt": "python", "Pipfile": "python",
             "setup.py": "python", "manage.py": "python", "go.mod": "go"}
SOURCE_EXT = {".js": "node", ".mjs": "node", ".cjs": "node", ".ts": "node", ".mts": "node", ".cts": "node",
              ".jsx": "node", ".tsx": "node", ".py": "python", ".go": "go"}
ENV_TEMPLATES = {".env.example", ".env.sample", ".env.template", ".env.dist", "example.env", "sample.env"}
TEST_RE = re.compile(r"(^|/)(tests?|__tests__|spec|e2e|testdata|fixtures?)/|(^|/)test_[^/]*\.py$|_test\.(py|go)$|"
                     r"\.(test|spec)\.[cm]?[jt]sx?$|(^|/)conftest\.py$|(^|/)tests?\.[cm]?[jt]sx?$|"
                     r"(^|/)(playwright|jest|vitest|cypress)\.config\.[cm]?[jt]s$")

SERVICES = {  # dependency name (or Go module prefix) -> service the app talks to
    "postgres": ["pg", "postgres", "pg-promise", "psycopg", "psycopg2", "psycopg2-binary", "asyncpg",
                 "github.com/jackc/pgx", "github.com/lib/pq"],
    "mysql": ["mysql", "mysql2", "mysqlclient", "pymysql", "aiomysql", "github.com/go-sql-driver/mysql"],
    "redis": ["redis", "ioredis", "github.com/redis/go-redis", "github.com/go-redis/redis", "github.com/gomodule/redigo"],
    "mongodb": ["mongodb", "mongoose", "pymongo", "motor", "go.mongodb.org/mongo-driver"],
    "rabbitmq": ["amqplib", "pika", "aio-pika", "github.com/rabbitmq/amqp091-go", "github.com/streadway/amqp"],
    "kafka": ["kafkajs", "kafka-python", "confluent-kafka", "aiokafka", "github.com/segmentio/kafka-go",
              "github.com/confluentinc/confluent-kafka-go", "github.com/ibm/sarama", "github.com/shopify/sarama"],
}

DOCS = {
    "pin": "https://docs.docker.com/build/building/best-practices/#pin-base-image-versions",
    "from": "https://docs.docker.com/reference/dockerfile/#from",
    "apt": "https://docs.docker.com/build/building/best-practices/#apt-get",
    "add": "https://docs.docker.com/build/building/best-practices/#add-or-copy",
    "user": "https://docs.docker.com/build/building/best-practices/#user",
    "order": "https://docs.docker.com/build/cache/optimize/#order-your-layers",
    "json": "https://docs.docker.com/reference/build-checks/json-args-recommended/",
    "ignore": "https://docs.docker.com/build/concepts/context/#dockerignore-files",
    "ignore_what": "https://docs.docker.com/build-cloud/optimization/#dockerignore-files",
    "secrets": "https://docs.docker.com/build/building/secrets/",
}


def read(path, limit=MAX_BYTES):
    try:
        if os.path.getsize(path) > limit:
            return None
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


# ---------------------------------------------------------------- walking

def walk(root):
    """Relative paths of regular files, sorted, skipping vendored/build dirs and symlinks."""
    out, capped = [], False
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not os.path.islink(os.path.join(dirpath, d)))
        rel_dir = os.path.relpath(dirpath, root)
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            out.append(name if rel_dir == "." else rel_dir.replace(os.sep, "/") + "/" + name)
            if len(out) >= MAX_FILES:
                return out, True
    return out, capped


def find_dockerfiles(root):
    """Dockerfiles anywhere, including build/, dist/ and deploy dirs the source walk prunes."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in DOCKERFILE_SKIP and not os.path.islink(os.path.join(dirpath, d)))
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        out += [n if rel_dir == "." else rel_dir + "/" + n for n in sorted(filenames) if is_dockerfile(n)]
        if len(out) >= 500:
            break
    return out


def parent(rel):
    return rel.rsplit("/", 1)[0] if "/" in rel else "."


def join(d, name):
    return name if d == "." else d + "/" + name


def ancestors(d):
    """d, its parent, ..., '.'"""
    while True:
        yield d
        if d == ".":
            return
        d = parent(d)


# ---------------------------------------------------------------- tiny TOML subset

def parse_toml(text):
    """{table: {key: value}} for the subset pyproject/Pipfile use here: tables, string/array/bool/number values,
    inline tables kept as raw text.
    shortcut: not a full TOML parser (no multi-line inline tables, no escapes beyond \\"), upgrade to tomllib
    once Python 3.11 is the floor. Used on every version so output is the same everywhere."""
    data, table, pending = {}, "", None
    for raw in text.splitlines():
        line = _strip_comment(raw).strip()
        if pending is not None:
            key, buf = pending
            buf += " " + line
            if _balanced(buf):
                data.setdefault(table, {})[key] = _value(buf)
                pending = None
            else:
                pending = (key, buf)
            continue
        if not line:
            continue
        m = re.match(r"^\[\[?\s*([^\]]+?)\s*\]\]?$", line)
        if m:
            table = ".".join(p.strip().strip('"\'') for p in m.group(1).split("."))
            continue
        m = re.match(r'^("[^"]*"|\'[^\']*\'|[A-Za-z0-9_.\-]+)\s*=\s*(.*)$', line)
        if not m:
            continue
        key, val = m.group(1).strip('"\''), m.group(2)
        if val.startswith(('"""', "'''")):
            continue
        if _balanced(val):
            data.setdefault(table, {})[key] = _value(val)
        else:
            pending = (key, val)
    return data


def _strip_comment(line):
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote and line[i - 1] != "\\":
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            return line[:i]
    return line


def _balanced(val):
    return val.count("[") <= val.count("]") and val.count("{") <= val.count("}")


def _value(val):
    val = val.strip()
    if val.startswith("["):
        return [m.group(1) or m.group(2) for m in re.finditer(r'"((?:[^"\\]|\\.)*)"|\'([^\']*)\'', val)]
    if val[:1] in "\"'":
        return val[1:-1] if len(val) > 1 else ""
    if val in ("true", "false"):
        return val == "true"
    return val  # numbers, dates and inline tables stay as text


def dep_name(spec):
    m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._\-]*)", spec)
    return m.group(1).lower().replace("_", "-") if m else ""


# ---------------------------------------------------------------- apps

def find_apps(files):
    apps = {}
    for rel in files:
        lang = MANIFESTS.get(rel.rsplit("/", 1)[-1])
        if lang:
            apps.setdefault((parent(rel), lang), []).append(rel.rsplit("/", 1)[-1])
    return apps


def find_up(root, start, names):
    """First (rel path) of any of names, looking in start and then each parent dir up to root."""
    for d in ancestors(start):
        for n in names:
            p = join(d, n)
            if os.path.isfile(os.path.join(root, p)):
                return p
    return None


def load_json(root, rel):
    try:
        with open(os.path.join(root, rel), encoding="utf-8") as fh:
            v = json.load(fh)
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def tool_versions(root, d, names):
    p = find_up(root, d, [".tool-versions"])
    if not p:
        return None
    for line in (read(os.path.join(root, p)) or "").splitlines():
        parts = line.split("#")[0].split()
        if len(parts) >= 2 and parts[0] in names:
            return {"name": parts[0], "value": " ".join(parts[1:]), "source": p}
    return None


def first_line(root, rel):
    for line in (read(os.path.join(root, rel)) or "").splitlines():
        if line.strip() and not line.strip().startswith("#"):
            return line.strip()
    return ""


def procfile(root, d):
    p = join(d, "Procfile")
    out = []
    text = read(os.path.join(root, p))
    for i, line in enumerate((text or "").splitlines(), 1):
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.+)$", line.strip())
        if m:
            out.append((m.group(1), m.group(2).strip(), "%s:%d" % (p, i)))
    return out


def node_app(root, d, app):
    pkg_path = join(d, "package.json")
    pkg = load_json(root, pkg_path)
    for name in (".nvmrc", ".node-version"):
        p = find_up(root, d, [name])
        if p:
            app["runtime"].append({"name": "node", "value": first_line(root, p), "source": p})
    engines = pkg.get("engines") if isinstance(pkg.get("engines"), dict) else {}
    if isinstance(engines.get("node"), str):
        app["runtime"].append({"name": "node", "value": engines["node"], "source": pkg_path + " engines"})
    tv = tool_versions(root, d, ("nodejs", "node"))
    if tv:
        app["runtime"].append(dict(tv, name="node"))
    pm_field = pkg.get("packageManager") if isinstance(pkg.get("packageManager"), str) else None
    if pm_field:
        app["runtime"].append({"name": "packageManager", "value": pm_field, "source": pkg_path})

    locks = {"package-lock.json": "npm", "npm-shrinkwrap.json": "npm", "pnpm-lock.yaml": "pnpm",
             "yarn.lock": "yarn", "bun.lock": "bun", "bun.lockb": "bun"}
    found = []
    for lock_dir in ancestors(d):
        found = [join(lock_dir, n) for n in locks if os.path.isfile(os.path.join(root, join(lock_dir, n)))]
        if found:
            break
    if found:
        lock = found[0]
        pm = locks[lock.rsplit("/", 1)[-1]]
        install = {"npm": "npm ci", "pnpm": "pnpm install --frozen-lockfile",
                   "bun": "bun install --frozen-lockfile"}.get(pm)
        if pm == "yarn":
            install = "yarn install --immutable" if yarn_berry(root, lock, pm_field) else "yarn install --frozen-lockfile"
        app["packages"] = {"manager": pm, "lockfile": lock, "install": install}
        if len({locks[f.rsplit("/", 1)[-1]] for f in found}) > 1:
            app["packages"]["note"] = "several lockfiles (%s): ask which one is current" % ", ".join(found)
    else:
        pm = pm_field.split("@")[0] if pm_field else "npm"
        app["packages"] = {"manager": pm, "lockfile": None,
                           "install": "npm install" if pm == "npm" else pm + " install",
                           "note": "no lockfile: the install is not reproducible; suggest committing one"}

    scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
    run = "npm run" if app["packages"]["manager"] == "npm" else app["packages"]["manager"] + " run"
    for kind in ("start", "build"):
        if isinstance(scripts.get(kind), str):
            app["commands"].append({"kind": kind, "command": "%s %s" % (run, kind), "runs": scripts[kind],
                                    "source": pkg_path + " scripts." + kind, "confidence": "high"})
    if "start" not in scripts and isinstance(pkg.get("main"), str):
        app["commands"].append({"kind": "start", "command": "node " + pkg["main"], "source": pkg_path + " main",
                                "confidence": "medium"})
    for kind in ("dev", "start", "serve", "preview"):
        port_from_command(scripts.get(kind), pkg_path + " scripts." + kind, app)
    deps = set()
    for key in ("dependencies", "optionalDependencies"):
        if isinstance(pkg.get(key), dict):
            deps.update(k.lower() for k in pkg[key])
    ws = pkg.get("workspaces")
    if isinstance(ws, dict):
        ws = ws.get("packages")
    if isinstance(ws, list) and ws:
        app["workspace"] = "package.json workspaces: " + ", ".join(str(w) for w in ws)
    return deps


def yarn_berry(root, lock, pm_field):
    if pm_field and pm_field.startswith("yarn@"):
        return not pm_field[5:].startswith("1.")
    try:  # Berry lockfiles carry a __metadata entry; classic ones start with "# yarn lockfile v1"
        with open(os.path.join(root, lock), encoding="utf-8", errors="replace") as fh:
            head = fh.read(4000)
    except OSError:
        return False
    return "__metadata:" in head and "# yarn lockfile v1" not in head


def python_app(root, d, app, files):
    py = parse_toml(read(os.path.join(root, join(d, "pyproject.toml"))) or "")
    pf = parse_toml(read(os.path.join(root, join(d, "Pipfile"))) or "")
    p = find_up(root, d, [".python-version"])
    if p:
        app["runtime"].append({"name": "python", "value": first_line(root, p), "source": p})
    proj = py.get("project", {})
    if isinstance(proj.get("requires-python"), str):
        app["runtime"].append({"name": "python", "value": proj["requires-python"],
                               "source": join(d, "pyproject.toml") + " requires-python"})
    poetry_py = py.get("tool.poetry.dependencies", {}).get("python")
    if isinstance(poetry_py, str):
        app["runtime"].append({"name": "python", "value": poetry_py,
                               "source": join(d, "pyproject.toml") + " tool.poetry.dependencies.python"})
    if isinstance(pf.get("requires", {}).get("python_version"), str):
        app["runtime"].append({"name": "python", "value": pf["requires"]["python_version"],
                               "source": join(d, "Pipfile") + " requires.python_version"})
    tv = tool_versions(root, d, ("python",))
    if tv:
        app["runtime"].append(tv)

    managers = [("uv.lock", "uv", "uv sync --locked"),
                ("poetry.lock", "poetry", "poetry install --no-root --only main"),
                ("Pipfile.lock", "pipenv", "pipenv sync"),
                ("requirements.txt", "pip", "pip install --no-cache-dir -r requirements.txt")]
    found = []
    for lock_dir in ancestors(d):
        found = [(join(lock_dir, f), m, c) for f, m, c in managers if os.path.isfile(os.path.join(root, join(lock_dir, f)))]
        if found:
            break
    if found:
        lock, pm, install = found[0]
        if pm == "pip":
            install = "pip install --no-cache-dir -r " + lock.rsplit("/", 1)[-1]
        app["packages"] = {"manager": pm, "lockfile": lock, "install": install}
        if pm == "pip":
            app["packages"]["note"] = "requirements.txt is not a lockfile unless every line is pinned (==) or hashed"
        if len(found) > 1:
            app["packages"]["note"] = "several dependency files (%s): ask which one is current" % ", ".join(f[0] for f in found)
    elif py:
        app["packages"] = {"manager": "pip", "lockfile": None, "install": "pip install --no-cache-dir .",
                           "note": "no lockfile: the install is not reproducible"}

    deps = set(dep_name(s) for s in proj.get("dependencies", []) if isinstance(s, str))
    deps.update(k.lower() for k in py.get("tool.poetry.dependencies", {}) if k != "python")
    deps.update(k.lower() for k in pf.get("packages", {}))
    req = read(os.path.join(root, join(d, "requirements.txt")))
    for line in (req or "").splitlines():
        if line.strip() and not line.strip().startswith(("#", "-")):
            deps.add(dep_name(line))
    deps.discard("")

    for table in ("project.scripts", "tool.poetry.scripts"):
        for name, target in sorted(py.get(table, {}).items()):
            app["commands"].append({"kind": "script", "command": name, "runs": str(target),
                                    "source": "%s [%s]" % (join(d, "pyproject.toml"), table), "confidence": "low",
                                    "note": "console script; may be a CLI rather than the server"})
    entry = py.get("tool.fastapi", {}).get("entrypoint")
    if isinstance(entry, str):
        app["commands"].append({"kind": "start", "command": "fastapi run", "runs": entry,
                                "source": join(d, "pyproject.toml") + " [tool.fastapi] entrypoint", "confidence": "high"})
    ws = py.get("tool.uv.workspace", {}).get("members")
    if isinstance(ws, list) and ws:
        app["workspace"] = "pyproject [tool.uv.workspace] members: " + ", ".join(ws)
    if os.path.isfile(os.path.join(root, join(d, "manage.py"))):
        wsgi = sorted(f for f in files if f.startswith("" if d == "." else d + "/") and f.endswith("/wsgi.py"))
        if wsgi:
            mod = os.path.relpath(wsgi[0][:-3], d).replace(os.sep, ".")
            app["commands"].append({"kind": "start", "command": "gunicorn %s:application" % mod,
                                    "source": wsgi[0], "confidence": "medium",
                                    "note": "Django; gunicorn is %sa direct dependency" % ("" if "gunicorn" in deps else "not ")})
    return deps


APP_RE = {"fastapi": re.compile(r"^(\w+)\s*(?::\s*\w+\s*)?=\s*FastAPI\("),
          "flask": re.compile(r"^(\w+)\s*=\s*Flask\(")}


def python_entry(app, d, rel, text, deps):
    for fw, rx in APP_RE.items():
        for i, line in enumerate(text.splitlines(), 1):
            m = rx.match(line)
            if not m:
                continue
            mod = os.path.relpath(rel[:-3], d).replace(os.sep, ".")
            mod = mod[4:] if mod.startswith("src.") else mod
            server = "uvicorn" if fw == "fastapi" else "gunicorn"
            app["commands"].append({"kind": "start", "command": "%s %s:%s" % (server, mod, m.group(1)),
                                    "source": "%s:%d" % (rel, i), "confidence": "medium",
                                    "note": "%s app object; %s is %sa direct dependency" % (
                                        fw, server, "" if server in deps else "not ")})
            return


def go_app(root, d, app, files):
    gomod = join(d, "go.mod")
    for line in (read(os.path.join(root, gomod)) or "").splitlines():
        m = re.match(r"^\s*(go|toolchain)\s+(\S+)", line)
        if m:
            app["runtime"].append({"name": m.group(1), "value": m.group(2), "source": gomod + " " + m.group(1)})
    tv = tool_versions(root, d, ("golang", "go"))
    if tv:
        app["runtime"].append(dict(tv, name="go"))
    app["packages"] = {"manager": "go modules", "lockfile": join(d, "go.sum") if os.path.isfile(
        os.path.join(root, join(d, "go.sum"))) else None, "install": "go mod download"}
    if os.path.isfile(os.path.join(root, join(d, "vendor/modules.txt"))):
        app["packages"]["note"] = "vendor/modules.txt present: build with -mod=vendor and skip go mod download"
    deps, in_block = set(), False
    for line in (read(os.path.join(root, gomod)) or "").splitlines():
        if "// indirect" in line:  # pulled in by a dependency, not used by this module directly
            continue
        s = line.split("//")[0].strip()
        if s.startswith("require ("):
            in_block = True
        elif in_block and s == ")":
            in_block = False
        elif in_block and s:
            deps.add(s.split()[0].lower())
        elif s.startswith("require "):
            deps.add(s.split()[1].lower())
    prefix = "" if d == "." else d + "/"
    mains = set()
    for rel in files:
        if rel.startswith(prefix) and rel.endswith(".go") and not rel.endswith("_test.go") and app_dir_of(rel, "go") == d:
            head = read(os.path.join(root, rel)) or ""
            if re.search(r"^package main\b", head, re.M) and re.search(r"^func main\(\)", head, re.M):
                mains.add(parent(rel))
    for m in sorted(mains):
        pkg = "." if m == d else "./" + os.path.relpath(m, d).replace(os.sep, "/")
        out = "app" if pkg == "." else pkg.rsplit("/", 1)[-1]
        app["commands"].append({"kind": "build", "command": "CGO_ENABLED=0 go build -o /out/%s %s" % (out, pkg),
                                "source": m + "/ (package main)" if m != "." else "./ (package main)",
                                "confidence": "high" if len(mains) == 1 else "medium"})
    return deps


APP_DIRS = {}  # language -> sorted app dirs, set by scan(); used to attribute source files to apps


def app_dir_of(rel, lang):
    best = None
    for d in APP_DIRS.get(lang, []):
        if d == "." or rel.startswith(d + "/"):
            if best is None or len(d) > len(best):
                best = d
    return best


# ---------------------------------------------------------------- env names and ports

ENV_RX = {
    "node": [re.compile(r"process\.env\.([A-Za-z_][A-Za-z0-9_]*)"),
             re.compile(r"process\.env\[\s*['\"`]([A-Za-z_][A-Za-z0-9_]*)['\"`]\s*\]"),
             re.compile(r"import\.meta\.env\.([A-Za-z_][A-Za-z0-9_]*)")],
    "python": [re.compile(r"(?<![\w.])(?:os\.)?environ(?:\.get\(|\.setdefault\(|\[)\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]"),
               re.compile(r"(?<![\w.])(?:os\.)?getenv\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]"),
               re.compile(r"['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s+(?:not\s+)?in\s+(?:os\.)?environ\b")],
    "go": [re.compile(r"\bos\.(?:Getenv|LookupEnv)\(\s*\"([A-Za-z_][A-Za-z0-9_]*)\"")],
}
DESTRUCTURE_RX = re.compile(r"\{([^}]*)\}\s*=\s*process\.env\b")
VITE_BUILTIN = {"MODE", "DEV", "PROD", "SSR", "BASE_URL"}
SETTINGS_RX = re.compile(r"^class\s+(\w+)\s*\(\s*(?:[\w.]*\.)?BaseSettings\s*[,)]")
PORT_RX = {
    "node": [(re.compile(r"process\.env\.PORT\s*(?:\|\||\?\?)\s*['\"]?(\d{2,5})\b"), "high", "PORT default"),
             (re.compile(r"\.listen\(\s*['\"]?(\d{2,5})\b"), "high", "listen call"),
             (re.compile(r"\b(?:const|let|var)\s+port\s*=\s*['\"]?(\d{2,5})\b", re.I), "medium", "port constant")],
    "python": [(re.compile(r"\.run\([^)]*\bport\s*=\s*(\d{2,5})\b"), "high", "run call"),
               (re.compile(r"(?:getenv|environ\.get)\(\s*['\"]PORT['\"]\s*,\s*['\"]?(\d{2,5})\b"), "high", "PORT default"),
               (re.compile(r"\brunserver\s+(?:[\d.]+:)?(\d{2,5})\b"), "high", "runserver")],
    "go": [(re.compile(r"(?:ListenAndServe(?:TLS)?|\.Run|\.Start|\.Listen|net\.Listen)\([^)]*\"[^\"]*:(\d{2,5})\""), "high", "listen call"),
           (re.compile(r"\bAddr\s*:\s*\"[^\"]*:(\d{2,5})\""), "high", "server Addr")],
}
PORT_ENV_RX = {"node": re.compile(r"process\.env\.PORT\b|process\.env\[\s*['\"]PORT['\"]"),
               "python": re.compile(r"(?:getenv|environ\.get|environ\[)\(?\s*['\"]PORT['\"]"),
               "go": re.compile(r"os\.(?:Getenv|LookupEnv)\(\s*\"PORT\"\)")}
GO_PORT_DEFAULT = re.compile(r"=\s*\"(?::)?(\d{2,5})\"")
CMD_PORT_RX = re.compile(r"(?:--port[= ]|(?<![\w-])-p\s+|\bPORT=|--bind[= ]\S*:|(?<![\w-])-b\s+\S*:)(\d{2,5})\b")


def port_from_command(cmd, source, app):
    if not isinstance(cmd, str):
        return
    m = CMD_PORT_RX.search(cmd)
    if m:
        app["ports"].append({"port": int(m.group(1)), "source": source, "confidence": "high", "how": "command flag"})
    elif "$PORT" in cmd or "${PORT" in cmd:
        app["ports"].append({"port": None, "env": "PORT", "source": source, "confidence": "high",
                             "how": "command reads $PORT"})


def scan_source(app, lang, rel, text):
    lines = text.splitlines()
    found = []  # (offset, name); matched on the whole text so a call split over lines still counts
    for rx in ENV_RX[lang]:
        found += [(m.start(), m.group(1)) for m in rx.finditer(text)
                  if not (m.group(0).startswith("import.meta") and m.group(1) in VITE_BUILTIN)]
    if lang == "node":
        for m in DESTRUCTURE_RX.finditer(text):
            found += [(m.start(), n.split(":")[0].split("=")[0].strip()) for n in m.group(1).split(",")]
    for off, n in sorted(found):
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", n):
            e = app["env"].setdefault(n, {"name": n, "source": "%s:%d" % (rel, text.count("\n", 0, off) + 1), "count": 0})
            e["count"] += 1
    for i, line in enumerate(lines, 1):
        if lang == "python":
            m = SETTINGS_RX.match(line)
            if m:
                app["env_hints"].append("%s:%d class %s(BaseSettings): its field names are env vars (pydantic-settings);"
                                        " read it for the full list" % (rel, i, m.group(1)))
        hit = False
        for rx, conf, how in PORT_RX[lang]:
            m = rx.search(line)
            if m:
                app["ports"].append({"port": int(m.group(1)), "source": "%s:%d" % (rel, i), "confidence": conf, "how": how})
                hit = True
                break
        if not hit and PORT_ENV_RX[lang].search(line):
            default = None
            if lang == "go":
                for j in range(i, min(i + 6, len(lines))):
                    m = GO_PORT_DEFAULT.search(lines[j])
                    if m:
                        default = (int(m.group(1)), j + 1)
                        break
            if default:
                app["ports"].append({"port": default[0], "env": "PORT", "source": "%s:%d" % (rel, default[1]),
                                     "confidence": "medium", "how": "default after reading $PORT"})
            else:
                app["ports"].append({"port": None, "env": "PORT", "source": "%s:%d" % (rel, i), "confidence": "high",
                                     "how": "reads $PORT, no default found"})


def env_template_names(root, rel):
    names = []
    for line in (read(os.path.join(root, rel)) or "").splitlines():
        m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if m:
            names.append(m.group(1))
    return names


def is_real_env(name):
    """.env, .env.local, .env.production ... but not the templates."""
    return (name == ".env" or name.startswith(".env.")) and name not in ENV_TEMPLATES


# ---------------------------------------------------------------- Dockerfile lint

# a project dependency install: no package names after the command (so "npm install -g pnpm", "yarn build" and
# "pip install poetry" don't count), or pip/uv pip with -r, -e or "."
INSTALL_RX = re.compile(r"^(?:(?:npm (?:ci|install|i)|pnpm (?:install|i)|bun install|yarn(?: install)?)(?:\s+-[-\w=.]+)*"
                        r"|(?:python3? -m )?(?:uv )?pip3? install\b(?=.*(?:\s-r\s|\s-e\s|\s\.(?:\[[\w,]+\])?(?:\s|$))).*"
                        r"|poetry install\b.*|uv sync\b.*|pipenv (?:install|sync)\b.*|go mod download\b.*)$")


def is_install(run):
    for seg in re.split(r"&&|\|\||;|\n", run):
        seg = re.sub(r"^(?:\s*--\S+)*\s*(?:\w+=\S*\s+)*", "", seg).strip()  # RUN flags and VAR=x prefixes
        if INSTALL_RX.match(seg):
            return True
    return False
ARCHIVE_RX = re.compile(r"\.(tar|tar\.gz|tgz|tar\.bz2|tbz2?|tar\.xz|txz|tar\.zst)$", re.I)


def parse_dockerfile(text):
    """[(line_no, INSTRUCTION, args)] with continuations joined and heredoc bodies appended."""
    lines = text.splitlines()
    escape = "\\"
    for line in lines:  # parser directives sit at the very top
        m = re.match(r"^#\s*escape\s*=\s*(\S)\s*$", line, re.I)
        if m:
            escape = m.group(1)
            continue
        if not re.match(r"^#\s*\w+\s*=", line):
            break
    out, i = [], 0
    while i < len(lines):
        start, buf = i + 1, lines[i]
        i += 1
        if not buf.strip() or buf.lstrip().startswith("#"):
            continue
        while buf.rstrip().endswith(escape) and i < len(lines):
            buf = buf.rstrip()[:-1]
            nxt = lines[i]
            i += 1
            if nxt.lstrip().startswith("#"):
                buf += " \\"  # comment lines inside a continuation are dropped
                continue
            buf += " " + nxt.strip()
        for tag in re.findall(r"(?<!<)<<-?\s*[\"']?([A-Za-z_][A-Za-z0-9_]*)[\"']?", buf):
            end = next((j for j in range(i, len(lines)) if lines[j].strip() == tag), None)
            if end is None:  # no terminator: not a heredoc after all, don't swallow the rest of the file
                continue
            buf += "\n" + "\n".join(lines[i:end])
            i = end + 1
        m = re.match(r"^\s*([A-Za-z]+)\s*(.*)$", buf, re.S)
        if m:
            out.append((start, m.group(1).upper(), m.group(2).strip()))
    return out


def split_flags(args):
    flags, rest = [], args.split()
    while rest and rest[0].startswith("--"):
        flags.append(rest.pop(0))
    return flags, rest


def lint_dockerfile(root, rel, files):
    insts = parse_dockerfile(read(os.path.join(root, rel)) or "")
    findings, stages, global_args = [], [], {}
    for ln, ins, args in insts:
        if ins == "ARG" and not stages:
            for part in args.split():
                k, _, v = part.partition("=")
                global_args[k] = v.strip('"\'')
        elif ins == "FROM":
            _, rest = split_flags(args)
            image = rest[0] if rest else ""
            name = rest[2].lower() if len(rest) >= 3 and rest[1].lower() == "as" else None
            stages.append({"line": ln, "image": image, "name": name, "insts": []})
        elif stages:
            stages[-1]["insts"].append((ln, ins, args))
    names = {s["name"] for s in stages if s["name"]}

    def add(rule, ln, conf, msg, *docs):
        findings.append({"rule": rule, "line": ln, "confidence": conf, "message": msg, "docs": [DOCS[d] for d in docs]})

    for s in stages:
        image = re.sub(r"\$\{?(\w+)\}?", lambda m: global_args.get(m.group(1)) or m.group(0), s["image"])
        if not image or image.lower() == "scratch" or image.lower() in names or "$" in image or "@sha256:" in image:
            continue
        last = image.rsplit("/", 1)[-1]
        if ":" not in last:
            add("base-image-unpinned", s["line"], "high", "FROM %s has no tag, so the builder uses :latest" % image,
                "from", "pin")
        elif last.endswith(":latest"):
            add("base-image-unpinned", s["line"], "high", "FROM %s uses the mutable :latest tag" % image, "pin")

    final = stages[-1] if stages else None
    chain, cur = [], final  # the final stage and the stages it is built FROM (they ship in the image)
    while cur and cur not in chain:
        chain.append(cur)
        cur = next((s for s in stages if s["name"] and s["name"] == cur["image"].lower()), None)

    for s in stages:
        first_install = first_copy_all = None
        for ln, ins, args in s["insts"]:
            flags, rest = split_flags(args)
            if ins == "RUN":
                if re.search(r"\bapt-get\b[^;&|]*\bupdate\b", args) and not re.search(r"\bapt-get\b[^;&|]*\binstall\b", args):
                    add("apt-update-alone", ln, "high", "apt-get update in its own RUN; later installs can use a stale"
                        " cached package index", "apt")
                if (s in chain and re.search(r"\bapt-get\b[^;&|]*\binstall\b", args)
                        and "/var/lib/apt/lists" not in args and not re.search(r"--mount=[^ ]*target=/var/lib/apt", args)):
                    add("apt-lists-not-removed", ln, "medium", "apt-get install without rm -rf /var/lib/apt/lists/* in"
                        " the same RUN; the package index stays in the image", "apt")
                if first_install is None and is_install(args):
                    first_install = ln
            elif ins in ("COPY", "ADD") and not any(f.startswith("--from") for f in flags):
                srcs = rest[:-1]
                if first_copy_all is None and any(x.rstrip("/") in (".", "./", "") or x == "./" for x in srcs):
                    first_copy_all = ln
                if ins == "ADD":
                    local = [x for x in srcs if not re.match(r"^(https?://|git@|ssh://)", x) and not ARCHIVE_RX.search(x)
                             and not x.endswith(".git")]
                    if local:
                        add("add-local-file", ln, "medium", "ADD %s copies local files; COPY is the documented choice"
                            " (ADD is for remote URLs, Git and archives)" % " ".join(local), "add")
        if first_copy_all and first_install and first_copy_all < first_install:
            add("copy-all-before-install", first_copy_all, "medium",
                "the whole context is copied before the dependency install on line %d, so any source change re-runs the"
                " install; copy the manifest and lockfile first" % first_install, "order")

    if final:
        user, cur = None, final
        for s in reversed(chain):
            for ln, ins, args in s["insts"]:
                if ins == "USER":
                    user = args.split(":")[0].strip()
        base = chain[-1]["image"].lower()
        if (user is None or user in ("root", "0")) and "nonroot" not in base:
            add("no-user", final["line"], "medium",
                ("the final stage switches back to USER %s" % user) if user else
                "no USER instruction in the final stage; it runs as the base image's user (check whether %s sets one)"
                % chain[-1]["image"], "user")
        for ln, ins, args in final["insts"]:
            if ins in ("CMD", "ENTRYPOINT") and args and not args.startswith("["):
                add("shell-form-" + ins.lower(), ln, "medium", "%s in shell form runs under /bin/sh -c, which does not"
                    " pass signals such as SIGTERM to the app; use the JSON (exec) form" % ins, "json")

    context, reason = guess_context(root, rel, insts)
    ignore = find_dockerignore(root, rel, context)
    findings += lint_ignore(root, context, ignore, files, guessed=reason.startswith("no specific"))
    findings.sort(key=lambda f: (f["line"] or 0, f["rule"]))
    expose = [a for _, i, a in (final["insts"] if final else []) if i == "EXPOSE"]
    return {"path": rel, "context": context, "context_reason": reason, "dockerignore": ignore,
            "stages": len(stages), "expose": expose, "findings": findings}


def guess_context(root, rel, insts):
    """Build context = the nearest dir (Dockerfile dir, then parents) where every local COPY/ADD source exists."""
    srcs = []
    for _, ins, args in insts:
        flags, rest = split_flags(args)
        if ins in ("COPY", "ADD") and not any(f.startswith("--from") for f in flags) and not args.startswith("["):
            srcs += [x for x in rest[:-1] if not re.match(r"^(https?://|git@|ssh://)", x) and "$" not in x]
        if ins == "RUN":
            for m in re.finditer(r"--mount=([^ ]+)", args):
                opts = dict(o.partition("=")[::2] for o in m.group(1).split(","))
                if opts.get("type") == "bind" and "from" not in opts and opts.get("source"):
                    srcs.append(opts["source"])
    srcs = [x for x in srcs if x.strip("./")]
    dfdir = parent(rel)
    if not srcs:
        return dfdir, "no specific COPY sources; assumed the Dockerfile's directory"
    best = None
    for d in ancestors(dfdir):
        base = os.path.join(root, d)
        hits = sum(1 for x in srcs if glob.glob(os.path.join(base, x.lstrip("/"))))
        if best is None or hits > best[1]:
            best = (d, hits)
        if hits == len(srcs):
            return d, "every COPY/ADD source exists here"
    return best[0], "%d of %d COPY/ADD sources exist here; check the build command" % (best[1], len(srcs))


def find_dockerignore(root, rel, context):
    specific = rel + ".dockerignore"
    if os.path.isfile(os.path.join(root, specific)):
        return specific
    p = join(context, ".dockerignore")
    return p if os.path.isfile(os.path.join(root, p)) else None


def ignore_patterns(text):
    pats = []
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        s = line.strip()
        neg = s.startswith("!")
        s = s[1:].strip() if neg else s
        s = os.path.normpath(s).replace(os.sep, "/").strip("/") if s else ""
        if not s or s == ".":
            continue
        pats.append((neg, re.compile("^" + glob_to_re(s) + "$")))
    return pats


def glob_to_re(p):
    out, i = "", 0
    while i < len(p):
        c = p[i]
        if p.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
            continue
        if p.startswith("**", i):
            out += ".*"
            i += 2
            continue
        if c == "*":
            out += "[^/]*"
        elif c == "?":
            out += "[^/]"
        elif c == "[":
            j = p.find("]", i + 1)
            if j == -1:
                out += re.escape(c)
            else:
                body = p[i + 1:j]
                out += "[" + ("^" + body[1:] if body.startswith("^") else body).replace("\\", "\\\\") + "]"
                i = j
        elif c == "\\" and i + 1 < len(p):
            i += 1
            out += re.escape(p[i])
        else:
            out += re.escape(c)
        i += 1
    return out


def ignored(path, pats):
    """Docker semantics: a pattern matches the path or any parent dir; the last matching line wins."""
    parts = path.split("/")
    prefixes = ["/".join(parts[:k]) for k in range(1, len(parts) + 1)]
    result = False
    for neg, rx in pats:
        if any(rx.match(p) for p in prefixes):
            result = not neg
    return result


def lint_ignore(root, context, ignore, files, guessed=False):
    """guessed: the context is an assumption, so every finding drops to medium confidence."""
    findings = []
    prefix = "" if context == "." else context + "/"
    ctx_files = [f[len(prefix):] for f in files if f.startswith(prefix)]
    at_stake = []
    if os.path.isdir(os.path.join(root, context, ".git")) or os.path.isfile(os.path.join(root, context, ".git")):
        at_stake.append((".git", ".git", "high", "ignore_what"))
    for f in ctx_files:
        if is_real_env(f.rsplit("/", 1)[-1]):
            at_stake.append((f, f, "high", "secrets"))
    for f in ctx_files:
        if f.rsplit("/", 1)[-1] == "package.json":
            nm = join(parent(f), "node_modules")
            at_stake.append((nm, nm, "medium", "ignore_what"))
    if not ignore:
        what = sorted({w for w, _, _, _ in at_stake})
        findings.append({"rule": "no-dockerignore", "line": None, "confidence": "high",
                         "message": "no .dockerignore for context %s, so everything in it is sent to the builder%s" % (
                             context, (": " + ", ".join(what[:6]) + (" ..." if len(what) > 6 else "")) if what else ""),
                         "docs": [DOCS["ignore"], DOCS["ignore_what"]]})
        return _cap(findings, guessed)
    pats = ignore_patterns(read(os.path.join(root, ignore)) or "")
    for what, path, conf, doc in at_stake:
        if not ignored(path, pats):
            kind = "env file" if doc == "secrets" else what.rsplit("/", 1)[-1]
            findings.append({"rule": "dockerignore-misses-" + ("env" if doc == "secrets" else kind.strip(".")),
                             "line": None, "confidence": conf,
                             "message": "%s does not exclude %s (%s%s)" % (
                                 ignore, path, "would be " if kind == "node_modules" else "", "in the build context"
                                 if kind != "env file" else "in the build context; a broad COPY would put it in the image"),
                             "docs": [DOCS["ignore"], DOCS[doc]]})
    return _cap(findings, guessed)


def _cap(findings, guessed):
    for f in findings:
        if guessed and f["confidence"] == "high":
            f["confidence"] = "medium"
            f["message"] += " (build context assumed; check the build command)"
    return findings


def is_dockerfile(name):
    low = name.lower()
    return (low in ("dockerfile", "containerfile") or low.startswith("dockerfile.") or low.endswith(".dockerfile")) \
        and not low.endswith(".dockerignore")


# ---------------------------------------------------------------- scan

def scan(root):
    root = os.path.abspath(root)
    files, capped = walk(root)
    raw_apps = find_apps(files)
    APP_DIRS.clear()
    for d, lang in raw_apps:
        APP_DIRS.setdefault(lang, []).append(d)
    apps = {}
    for (d, lang), manifests in sorted(raw_apps.items()):
        app = {"dir": d, "language": lang, "manifests": sorted(manifests), "runtime": [], "packages": None,
               "commands": [], "ports": [], "env": {}, "env_hints": [], "env_templates": [], "services": []}
        if lang == "node":
            deps = node_app(root, d, app)
        elif lang == "python":
            deps = python_app(root, d, app, files)
        else:
            deps = go_app(root, d, app, files)
        for n, (kind, cmd, src) in enumerate(procfile(root, d)):
            app["commands"].insert(n, {"kind": "start" if kind == "web" else kind, "command": cmd,
                                       "source": src, "confidence": "high" if kind == "web" else "medium"})
            port_from_command(cmd, src, app)
        app["services"] = sorted(svc for svc, names in SERVICES.items()
                                 if any(dep == n or dep.startswith(n + "/") for dep in deps for n in names))
        app["_deps"] = deps
        apps[(d, lang)] = app

    for rel in files:
        lang = SOURCE_EXT.get(os.path.splitext(rel)[1])
        if not lang or TEST_RE.search(rel):
            continue
        d = app_dir_of(rel, lang)
        if d is None:
            continue
        text = read(os.path.join(root, rel))
        if text is None:
            continue
        app = apps[(d, lang)]
        scan_source(app, lang, rel, text)
        if lang == "python":
            python_entry(app, d, rel, text, app["_deps"])

    env_files = [f for f in files if is_real_env(f.rsplit("/", 1)[-1])]
    for rel in files:
        if rel.rsplit("/", 1)[-1] in ENV_TEMPLATES:
            owner = max((a for a in apps.values() if a["dir"] == "." or rel.startswith(a["dir"] + "/")),
                        key=lambda a: len(a["dir"]), default=None)
            if owner:
                owner["env_templates"].append({"file": rel, "names": env_template_names(root, rel)})

    out_apps = []
    for app in apps.values():
        app.pop("_deps")
        app["env"] = sorted(app["env"].values(), key=lambda e: e["name"])
        seen, ports = set(), []
        for p in app["ports"]:
            key = (p.get("port"), p["source"])
            if key not in seen:
                seen.add(key)
                ports.append(p)
        app["ports"] = ports
        out_apps.append(app)

    workspace = sorted({a["dir"] + ": " + a["workspace"] for a in out_apps if a.get("workspace")})
    for name in ("pnpm-workspace.yaml", "go.work", "lerna.json", "nx.json", "turbo.json"):
        workspace += [f + " present" for f in files if f.rsplit("/", 1)[-1] == name]
    app_dirs = sorted({a["dir"] for a in out_apps})
    dockerfiles = [lint_dockerfile(root, f, files) for f in find_dockerfiles(root)]
    linted = {d["dockerignore"] for d in dockerfiles}
    lone_ignores = [f for f in files if f.rsplit("/", 1)[-1] == ".dockerignore" and f not in linted]
    if not dockerfiles:
        for f in lone_ignores:
            dockerfiles.append({"path": None, "context": parent(f), "context_reason": "no Dockerfile; linted the"
                                " .dockerignore on its own", "dockerignore": f, "stages": 0, "expose": [],
                                "findings": lint_ignore(root, parent(f), f, files)})
    compose = [f for f in files if re.match(r"^(docker-)?compose(\.[\w.-]+)?\.ya?ml$", f.rsplit("/", 1)[-1])]
    return {
        "root": os.path.basename(root) or root,
        "apps": out_apps,
        "monorepo": len(app_dirs) > 1 or bool(workspace),
        "app_dirs": app_dirs,
        "workspace": workspace,
        "env_files": env_files,
        "compose_files": compose,
        "dockerfiles": dockerfiles,
        "files_scanned": len(files),
        "capped": capped,
    }


# ---------------------------------------------------------------- output

def render(r):
    out = ["Stack facts for %s (%d files%s)" % (r["root"], r["files_scanned"], ", capped" if r["capped"] else "")]
    if r["monorepo"]:
        out.append("Monorepo: %d app dirs (%s). Ask which app to containerize." % (len(r["app_dirs"]), ", ".join(r["app_dirs"])))
        out += ["  workspace: " + w for w in r["workspace"]]
    for a in r["apps"]:
        out.append("")
        out.append("[%s] %s  (%s)" % (a["dir"], a["language"], ", ".join(a["manifests"])))
        for v in a["runtime"]:
            out.append("  runtime   %s %s   <- %s" % (v["name"], v["value"], v["source"]))
        if not a["runtime"]:
            out.append("  runtime   not pinned in the project; ask or pick the current LTS and say so")
        p = a["packages"]
        if p:
            out.append("  packages  %s, lockfile %s, install: %s" % (p["manager"], p["lockfile"] or "none", p["install"]))
            if p.get("note"):
                out.append("            note: " + p["note"])
        for c in a["commands"]:
            out.append("  %-9s %s%s   <- %s [%s]%s" % (c["kind"], c["command"], " (= %s)" % c["runs"] if c.get("runs") else "",
                                                     c["source"], c["confidence"], "  " + c["note"] if c.get("note") else ""))
        for pt in a["ports"]:
            out.append("  port      %s   <- %s [%s] %s" % (pt["port"] if pt["port"] else "$" + pt.get("env", "PORT"),
                                                        pt["source"], pt["confidence"], pt["how"]))
        if not a["ports"]:
            out.append("  port      not found in code or commands")
        if a["env"]:
            out.append("  env vars  " + ", ".join(e["name"] for e in a["env"]))
            out.append("            first seen: " + ", ".join("%s %s" % (e["name"], e["source"]) for e in a["env"][:8])
                       + (" ..." if len(a["env"]) > 8 else ""))
        for t in a["env_templates"]:
            out.append("  env tmpl  %s: %s" % (t["file"], ", ".join(t["names"]) or "(no names)"))
        for h in a["env_hints"]:
            out.append("  env hint  " + h)
        if a["services"]:
            out.append("  services  " + ", ".join(a["services"]) + " (from dependencies)")
    if r["env_files"]:
        out += ["", "Env files (not opened, values never read): " + ", ".join(r["env_files"])]
    if r["compose_files"]:
        out += ["", "Compose files: " + ", ".join(r["compose_files"])]
    for d in r["dockerfiles"]:
        out += ["", "Dockerfile %s  (context %s: %s; .dockerignore: %s)" % (
            d["path"] or "(none)", d["context"], d["context_reason"], d["dockerignore"] or "none")]
        if d["expose"]:
            out.append("  EXPOSE " + ", ".join(d["expose"]))
        for f in d["findings"]:
            out.append("  %s%s [%s] %s" % ("L%d " % f["line"] if f["line"] else "", f["rule"], f["confidence"], f["message"]))
            out.append("      " + " ".join(f["docs"]))
        if not d["findings"]:
            out.append("  no lint findings")
    if not r["dockerfiles"]:
        out += ["", "No Dockerfile yet."]
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 epilog=__doc__.split("\n\n", 1)[1], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", default=".")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not os.path.isdir(args.path):
        print("error: %s is not a directory" % args.path, file=sys.stderr)
        return 2
    r = scan(args.path)
    if not r["apps"] and not r["dockerfiles"]:
        print("error: no package.json, pyproject.toml, requirements.txt, Pipfile, setup.py, manage.py, go.mod or"
              " Dockerfile found under %s" % r["root"], file=sys.stderr)
        return 2
    print(json.dumps(r, indent=2) if args.json else render(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
