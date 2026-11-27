#!/usr/bin/env python3
"""Compare what a project declares as dependencies with what its code imports and what its lockfiles pin.
Stdlib only, read-only, offline: it never contacts a registry, so it cannot see outdated or vulnerable
versions. Run `npm outdated`, `npm audit`, `pip-audit`, `govulncheck` and friends for those.

Usage: python3 dep_audit.py [-C PROJECT] [--json]
  -C PROJECT   project to read (default: current directory)
  --json       machine-readable report on stdout

What it reads (node_modules, venvs, vendor/, hidden folders other than .github/.storybook/.husky, and dist, build,
out, target or coverage folders that sit next to a project file are skipped):
  manifests   package.json (dependencies, devDependencies, peerDependencies, optionalDependencies);
              pyproject.toml ([project] dependencies and optional-dependencies, [dependency-groups],
              Poetry dependencies, dev-dependencies and groups); requirements*.txt and requirements/*.txt;
              go.mod (require, replace, tool). setup.py, setup.cfg install_requires and Pipfile are not read.
              Each manifest covers the files below its folder; JS and Python files also see the
              dependencies of manifests in parent folders (workspaces), Go files only their own go.mod.
  imports     Python with ast (relative imports and local modules skipped, imports inside try/except or
              `if TYPE_CHECKING:` marked as guarded); JS/TS import, export-from, require() and import()
              specifiers (scoped packages, subpaths, node: builtins, tsconfig paths aliases); Go import paths
              against the longest matching `require`.
  lockfiles   package-lock.json / npm-shrinkwrap.json v2 and v3 (`packages`), yarn.lock v1 (classic),
              pnpm-lock.yaml 9.x (the `packages` keys of the project document), poetry.lock and uv.lock
              (`[[package]]` name and version), go.sum. bun.lock, pdm.lock, Pipfile.lock, pylock.toml,
              yarn berry and other lockfile versions count as "a lockfile exists" but are not parsed.
  TOML        tomllib on Python 3.11+, otherwise a small built-in reader for the subset these files use.

Findings (counted in the exit code when confidence is high or medium):
  unused              declared, never imported, and not referenced from scripts or config files
  undeclared          imported but not declared in any manifest that covers the file
  dev-in-runtime      a dev-only dependency imported from non-test, non-config code
  missing-from-lock   declared but absent from the lockfile, or a go.mod require with no go.sum line
  unlocked            version ranges and no lockfile
  no-manifest         Python files import third-party modules but no Python manifest covers them
Informational, never in the exit code: low-confidence findings, "implicit" dependencies (used only from
package.json scripts, config files or strings), and packages locked at several versions.

Exit codes: 0 nothing at high or medium confidence
            1 findings at high or medium confidence
            2 bad arguments, the project folder is missing, or there is no manifest to read
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import os
import re
import sys
from collections import defaultdict

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

SKIP_DIRS = {"node_modules", ".git", ".hg", ".svn", ".venv", "venv", "__pycache__", ".tox", ".nox", ".mypy_cache",
             ".pytest_cache", ".ruff_cache", ".next", ".nuxt", ".svelte-kit", ".output",
             ".turbo", ".cache", ".parcel-cache", ".yarn", ".pnpm-store", "vendor", "third_party",
             "bower_components", "site-packages", ".eggs", ".gradle", ".idea"}
# Build output folders, skipped only next to a project file, so a source package named `build` is still read.
OUTPUT_DIRS = {"dist", "build", "out", "target", "coverage"}
PROJECT_FILES = {"package.json", "go.mod", "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Cargo.toml",
                 "pom.xml", "build.gradle", "build.gradle.kts"}
MAX_BYTES = 2000000
JS_EXT = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte", ".astro")
NPM_LOCKS = ("package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lock", "bun.lockb")
PY_LOCKS = ("poetry.lock", "uv.lock", "pdm.lock", "Pipfile.lock", "pylock.toml")

# Python stdlib for 3.9 and 3.10: sys.stdlib_module_names of 3.11 (public names), which 3.10+ provides itself.
PY_STDLIB = set(getattr(sys, "stdlib_module_names", ()) or """
abc aifc antigravity argparse array ast asynchat asyncio asyncore atexit audioop base64 bdb binascii bisect
builtins bz2 cProfile calendar cgi cgitb chunk cmath cmd code codecs codeop collections colorsys compileall
concurrent configparser contextlib contextvars copy copyreg crypt csv ctypes curses dataclasses datetime dbm
decimal difflib dis distutils doctest email encodings ensurepip enum errno faulthandler fcntl filecmp
fileinput fnmatch fractions ftplib functools gc genericpath getopt getpass gettext glob graphlib grp gzip
hashlib heapq hmac html http idlelib imaplib imghdr imp importlib inspect io ipaddress itertools json keyword
lib2to3 linecache locale logging lzma mailbox mailcap marshal math mimetypes mmap modulefinder msilib msvcrt
multiprocessing netrc nis nntplib nt ntpath nturl2path numbers opcode operator optparse os ossaudiodev pathlib
pdb pickle pickletools pipes pkgutil platform plistlib poplib posix posixpath pprint profile pstats pty pwd
py_compile pyclbr pydoc pydoc_data pyexpat queue quopri random re readline reprlib resource rlcompleter runpy
sched secrets select selectors shelve shlex shutil signal site smtpd smtplib sndhdr socket socketserver spwd
sqlite3 sre_compile sre_constants sre_parse ssl stat statistics string stringprep struct subprocess sunau
symtable sys sysconfig syslog tabnanny tarfile telnetlib tempfile termios textwrap this threading time timeit
tkinter token tokenize tomllib trace traceback tracemalloc tty turtle turtledemo types typing unicodedata
unittest urllib uu uuid venv warnings wave weakref webbrowser winreg winsound wsgiref xdrlib xml xmlrpc zipapp
zipfile zipimport zlib zoneinfo __future__ _thread""".split())
# require('module').builtinModules (Node 24), first path segment; node:-only ones (node:test, node:sqlite) are
# caught by the node: prefix.
NODE_BUILTINS = set("""assert async_hooks buffer child_process cluster console constants crypto dgram
diagnostics_channel dns domain events fs http http2 https inspector module net os path perf_hooks process punycode
querystring readline repl stream string_decoder sys timers tls trace_events tty url util v8 vm wasi worker_threads
zlib""".split())

# Distributions whose import name differs from the PEP 503 normalised project name. Checked against installed
# top_level.txt/RECORD metadata or the pipreqs import mapping list.
PY_IMPORT_NAMES = {
    "pyyaml": ["yaml"], "pillow": ["PIL"], "beautifulsoup4": ["bs4"], "scikit-learn": ["sklearn"],
    "opencv-python": ["cv2"], "python-dateutil": ["dateutil"], "python-dotenv": ["dotenv"], "attrs": ["attr", "attrs"],
    "protobuf": ["google.protobuf"], "pyzmq": ["zmq"], "pyjwt": ["jwt"], "pycryptodome": ["Crypto"],
    "pycryptodomex": ["Cryptodome"], "pyserial": ["serial"], "pyopenssl": ["OpenSSL"], "websocket-client": ["websocket"],
    "ipython": ["IPython"], "dnspython": ["dns"], "python-slugify": ["slugify"], "python-jose": ["jose"],
    "google-api-python-client": ["googleapiclient"], "pymupdf": ["fitz", "pymupdf"], "discord-py": ["discord"],
    "djangorestframework": ["rest_framework"], "pyusb": ["usb"], "mysql-python": ["MySQLdb"],
}
# shortcut: from the projects' own docs, not checked against metadata; confirm with the package's RECORD if one misfires.
PY_IMPORT_NAMES.update({
    "psycopg2-binary": ["psycopg2"], "grpcio": ["grpc"], "scikit-image": ["skimage"], "mysqlclient": ["MySQLdb"],
    "opencv-python-headless": ["cv2"], "opencv-contrib-python": ["cv2"], "django-cors-headers": ["corsheaders"],
})
PY_HINT = {mod.split(".")[0].lower(): dist for dist, mods in PY_IMPORT_NAMES.items() for mod in mods}
# Run as a CLI, auto-loaded as a plugin, or loaded from a connection string: never imported by name.
PY_IMPLICIT = re.compile(r"^(pytest|pytest-.+|.+-stubs|types-.+|mypy|black|ruff|isort|flake8.*|pylint.*|pre-commit|tox"
                         r"|nox|coverage|twine|build|wheel|setuptools|pip|pip-tools|hatch|hatchling|poetry-core|uv"
                         r"|sphinx.*|mkdocs.*|ipython|ipykernel|jupyter.*|notebook|gunicorn|uvicorn|hypercorn|daphne"
                         r"|waitress|psycopg2|psycopg2-binary|psycopg|psycopg-binary|pymysql|mysqlclient|asyncpg"
                         r"|aiosqlite|oracledb|cx-oracle|pyodbc|bandit|pip-audit|pyright|debugpy|python-dotenv)$")
JS_IMPLICIT = re.compile(r"^(@types/|eslint|@eslint/|.*eslint-(plugin|config)|prettier|@prettier/|babel-|@babel/|postcss"
                         r"|autoprefixer|tailwindcss|@tailwindcss/|typescript$|ts-node$|tsx$|stylelint|@commitlint/"
                         r"|husky$|lint-staged$|.*-loader$|@vitejs/|vite-plugin-|@storybook/|jest-environment-"
                         r"|@testing-library/jest-dom$|.*-preset$|.*-plugin$|nodemon$|concurrently$|rimraf$"
                         r"|npm-run-all|cross-env$|@playwright/test$|playwright$|cypress$|@nestjs/platform-"
                         r"|sass$|sass-embedded$|less$|stylus$)")
# Command names that differ from the package name, from those packages' `bin` fields. Used when node_modules is not
# installed; when it is, every package's own `bin` field is read.
JS_BINS = {"typescript": ["tsc", "tsserver"], "@playwright/test": ["playwright"], "@nestjs/cli": ["nest"]}
GENERIC = {"cli", "core", "test", "kit", "node", "client", "server", "types", "utils", "config", "plugin"}
DEV_GROUP = re.compile(r"^(dev|develop|development|test|tests|testing|lint|linting|docs?|typing|types|style|qa|ci)$", re.I)
DEV_REQ_FILE = re.compile(r"(dev|test|lint|docs?\b|typing|ci\b)", re.I)

TEST_PARTS = {"test", "tests", "testing", "__tests__", "__mocks__", "spec", "specs", "e2e", "cypress", "playwright",
              "fixtures", "testdata"}
TOOL_PARTS = {"scripts", "script", "tools", "tool", ".storybook", ".github", ".husky", "docs", "doc", "examples",
              "example", "benchmarks", "bench", "stories"}
TEST_FILE = re.compile(r"(^test_.*\.py$|_test\.(py|go)$|^conftest\.py$|\.(test|spec|stories|story|cy|e2e)\.[cm]?[jt]sx?$)")
TOOL_FILE = re.compile(r"(\.config\.[cm]?[jt]s$|^\.|\.d\.[cm]?ts$|^(setup|noxfile|tasks|fabfile)\.py$"
                       r"|^(gulpfile|gruntfile)\.)", re.I)
CONFIG_FILE = re.compile(r"^(\..*rc(\.(json|ya?ml|js|cjs|mjs|toml))?|.*\.config\.\w+|[tj]sconfig[\w.-]*\.json|Makefile"
                         r"|GNUmakefile|[Jj]ustfile|Dockerfile[\w.-]*|.*\.dockerfile|Procfile|tox\.ini|pytest\.ini"
                         r"|mypy\.ini|\.flake8|\.pre-commit-config\.ya?ml|(docker-)?compose[\w.-]*\.ya?ml|turbo\.json"
                         r"|nx\.json|angular\.json|vercel\.json|netlify\.toml)$")
DOT_DIRS = {".github", ".storybook", ".husky", ".config", ".vitepress", ".circleci", ".gitlab", ".devcontainer"}
BUNDLERS = re.compile(r"^(vite|webpack|next|nuxt|@sveltejs/kit|parcel|rollup|esbuild|astro|react-scripts|@angular/core"
                      r"|@remix-run/dev|gatsby|@rsbuild/core|@rspack/core)$")
NPM_NAME = re.compile(r"^(@[\w.~-]+/)?[\w.~-]+$")
CONFIG_DIRS = (".github/workflows/", ".husky/", ".storybook/")

JS_RX = re.compile(r"""\b(?:(?:import|export)\s+(type\s+)?(?:[\w$*{}\s,]*?\s+from\s*)?|(?:require(?:\.resolve)?|import)\s*\(\s*)"""
                   r"""['"]([^'"\n]+)['"]""")
CSS_EXT = (".css", ".scss", ".sass", ".less", ".pcss")
CSS_RX = re.compile(r"""@(?:import|use|forward|plugin)\s+(?:url\(\s*)?['"]~?([^'"]+)['"]""")
GO_BLOCK = re.compile(r"(?ms)^import\s*\((.*?)\)")
GO_SINGLE = re.compile(r'(?m)^import\s+(?:[\w.]+\s+)?"([^"]+)"')
DOTTED = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)*$")
PEP508 = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*([^;]*)")
NPM_EXACT = re.compile(r"^=?v?\d+\.\d+\.\d+([-+][\w.+-]*)?$")


def norm_py(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def read(path):
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return None
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


# ---------------------------------------------------------------- TOML
def _skip(s, i, newlines=True):
    while i < len(s):
        if s[i] in " \t\r" or (newlines and s[i] == "\n"):
            i += 1
        elif s[i] == "#":
            while i < len(s) and s[i] != "\n":
                i += 1
        else:
            break
    return i


def _key(s, i):
    parts = []
    while True:
        i = _skip(s, i, False)
        if s[i] in "\"'":
            q = s[i]
            j = s.index(q, i + 1)
            parts.append(s[i + 1:j])
            i = j + 1
        else:
            m = re.compile(r"[A-Za-z0-9_-]+").match(s, i)
            if not m:
                raise ValueError("bad key at offset %d" % i)
            parts.append(m.group(0))
            i = m.end()
        i = _skip(s, i, False)
        if i < len(s) and s[i] == ".":
            i += 1
            continue
        return parts, i


def _value(s, i):
    for q in ('"""', "'''"):
        if s.startswith(q, i):
            j = s.index(q, i + 3)
            return s[i + 3:j], j + 3
    c = s[i]
    if c == '"':
        j, buf = i + 1, []
        while s[j] != '"':
            if s[j] == "\\":
                buf.append(s[j:j + 2])  # escapes kept raw: names and versions never need them
                j += 2
            else:
                buf.append(s[j])
                j += 1
        return "".join(buf), j + 1
    if c == "'":
        j = s.index("'", i + 1)
        return s[i + 1:j], j + 1
    if c in "[{":
        close, out = ("]", []) if c == "[" else ("}", {})
        i += 1
        while True:
            i = _skip(s, i)
            if s[i] == close:
                return out, i + 1
            if close == "]":
                v, i = _value(s, i)
                out.append(v)
            else:
                k, i = _key(s, i)
                v, i = _value(s, _skip(s, i + 1))  # skip '='
                _put(out, k, v)
            i = _skip(s, i)
            if s[i] == ",":
                i += 1
    m = re.compile(r"[^\s,\]}#]+").match(s, i)
    if not m:
        raise ValueError("bad value at offset %d" % i)
    return m.group(0), m.end()


def _put(table, keys, value):
    for k in keys[:-1]:
        table = table.setdefault(k, {})
    table[keys[-1]] = value


def mini_toml(text):
    """The TOML subset manifests and lockfiles use: tables, arrays of tables, strings, arrays, inline tables.
    shortcut: numbers, booleans and dates stay raw strings and escapes are not decoded; fine for names and
    version specifiers, upgrade to tomllib (Python 3.11+) for anything else."""
    root, i, s = {}, 0, text + "\n"
    cur = root
    while True:
        i = _skip(s, i)
        if i >= len(s):
            return root
        if s.startswith("[[", i):
            keys, i = _key(s, i + 2)
            parent = root
            for k in keys[:-1]:
                parent = parent.setdefault(k, {})
                if isinstance(parent, list):
                    parent = parent[-1]
            cur = {}
            parent.setdefault(keys[-1], []).append(cur)
            i = _skip(s, i, False) + 2
        elif s[i] == "[":
            keys, i = _key(s, i + 1)
            cur = root
            for k in keys:
                cur = cur.setdefault(k, {})
                if isinstance(cur, list):
                    cur = cur[-1]
            i = _skip(s, i, False) + 1
        else:
            keys, i = _key(s, i)
            i = _skip(s, i, False)
            if s[i] != "=":
                raise ValueError("expected = at offset %d" % i)
            v, i = _value(s, _skip(s, i + 1, False))
            _put(cur, keys, v)


def load_toml(text):
    return tomllib.loads(text) if tomllib else mini_toml(text)


# ---------------------------------------------------------------- manifests
def is_pinned_pep508(spec):
    spec = spec.strip()
    return spec.startswith("===") or (spec.startswith("==") and "," not in spec and "*" not in spec) or spec.startswith("@")


def is_pinned_poetry(spec):
    return bool(re.match(r"^(==)?\s*\d[\w.+-]*$", spec.strip()))


def parse_requirement(line):
    m = PEP508.match(line)
    return (m.group(1), m.group(3).strip()) if m else (None, "")


def read_requirements(text):
    """[(name, spec)] from a pip requirements file; options, includes, paths and bare URLs are skipped."""
    out = []
    for line in re.sub(r"\\\n", " ", text).splitlines():
        line = re.sub(r"(^|\s)#.*", "", line).strip()
        if not line or line.startswith(("-", ".", "/")) or "://" in line.split("@")[0]:
            continue
        name, spec = parse_requirement(line)
        if name:
            out.append((name, re.sub(r"\s--hash\S*", "", spec).strip()))
    return out


def read_pyproject(data):
    """[(name, spec, kind, group, pinned)] from a parsed pyproject.toml."""
    out = []
    proj = data.get("project") or {}
    for s in proj.get("dependencies") or []:
        name, spec = parse_requirement(s)
        if name:
            out.append((name, spec, "prod", "", is_pinned_pep508(spec)))
    for group, lst in (proj.get("optional-dependencies") or {}).items():
        for s in lst or []:
            name, spec = parse_requirement(s)
            if name:
                out.append((name, spec, "dev" if DEV_GROUP.match(group) else "optional", group, is_pinned_pep508(spec)))
    for group, lst in (data.get("dependency-groups") or {}).items():  # PEP 735: never part of the published package
        for s in lst or []:
            if isinstance(s, str):
                name, spec = parse_requirement(s)
                if name:
                    out.append((name, spec, "dev", group, is_pinned_pep508(spec)))
    poetry = (data.get("tool") or {}).get("poetry") or {}
    tables = [("prod", "", poetry.get("dependencies")), ("dev", "dev", poetry.get("dev-dependencies"))]
    tables += [("prod" if g == "main" else "dev", g, (t or {}).get("dependencies"))
               for g, t in (poetry.get("group") or {}).items()]
    for kind, group, table in tables:
        for name, spec in (table or {}).items():
            if name.lower() == "python":
                continue
            version = spec.get("version", "") if isinstance(spec, dict) else str(spec)
            other = isinstance(spec, dict) and any(k in spec for k in ("git", "path", "url"))
            k = "optional" if isinstance(spec, dict) and spec.get("optional") and kind == "prod" else kind
            out.append((name, version, k, group, other or is_pinned_poetry(version)))
    return out


def read_go_mod(text):
    mod, requires, replaced, tools = "", {}, set(), []
    block = None
    for raw in text.splitlines():
        line = raw.split("//")[0].strip()
        indirect = "// indirect" in raw
        if not line:
            continue
        m = re.match(r"^(require|replace|tool|exclude|retract)\s*\($", line)
        if m:
            block = m.group(1)
            continue
        if line == ")":
            block = None
            continue
        if block:
            verb, rest = block, line
        else:
            verb, _, rest = line.partition(" ")
        rest = rest.strip()
        if verb == "module":
            mod = rest.strip('"')
        elif verb == "require":
            parts = rest.split()
            if len(parts) >= 2:
                requires[parts[0].strip('"')] = {"version": parts[1], "indirect": indirect}
        elif verb == "replace":
            replaced.add(rest.split()[0].strip('"'))
        elif verb == "tool":
            tools.append(rest.strip('"'))
    return mod, requires, replaced, tools


# ---------------------------------------------------------------- lockfiles
def spec_name(spec):
    """'@scope/pkg@^1.0' -> '@scope/pkg'; the version starts at the first '@' after index 0."""
    i = spec.find("@", 1)
    return spec[:i] if i > 0 else spec


def lock_package_lock(text):
    d = json.loads(text)
    pk = d.get("packages")
    if not isinstance(pk, dict):
        return None, "lockfileVersion %s has no `packages` (v1), not parsed" % d.get("lockfileVersion")
    out = defaultdict(set)
    for key, e in pk.items():
        if "node_modules/" in key and isinstance(e, dict) and not e.get("link") and e.get("version"):
            out[key.rsplit("node_modules/", 1)[1]].add(e["version"])
    return out, "v%s" % d.get("lockfileVersion")


def lock_yarn(text):
    if re.search(r"(?m)^__metadata:", text):
        return None, "yarn berry (v2+) lockfile, not parsed"
    out, names = defaultdict(set), []
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        if not line[0].isspace() and line.rstrip().endswith(":"):
            names = [spec_name(s.strip().strip('"')) for s in line.rstrip()[:-1].split(",")]
        elif line.startswith("  version "):
            for n in names:
                out[n].add(line.split(None, 1)[1].strip().strip('"'))
    return out, "v1"


def lock_pnpm(text):
    docs = re.split(r"(?m)^---\s*$", text)
    # pnpm can write its own toolchain as a first YAML document; the project document has dependency sections.
    proj = [d for d in docs if re.search(r"(?m)^ {4}(dev|optional)?[dD]ependencies:", d)] or docs[-1:]
    out, version = defaultdict(set), None
    for doc in proj:
        m = re.search(r"(?m)^lockfileVersion:\s*['\"]?([\d.]+)", doc)
        version = m.group(1) if m else "?"
        if not version.startswith("9."):
            return None, "lockfileVersion %s, only 9.x is parsed" % version
        section = re.search(r"(?ms)^packages:\s*\n(.*?)(?=^\S|\Z)", doc)
        for key in re.findall(r"(?m)^  (\S.*?):\s*$", section.group(1) if section else ""):
            key = key.strip("'\"").split("(")[0]
            name = spec_name(key)
            if name != key:
                out[name].add(key[len(name) + 1:])
    return out, "v%s" % version


def lock_toml(text):
    out = defaultdict(set)
    for p in load_toml(text).get("package") or []:
        if p.get("name") and p.get("version"):
            out[norm_py(p["name"])].add(str(p["version"]))
    return out, "parsed"


LOCK_PARSERS = {"package-lock.json": lock_package_lock, "npm-shrinkwrap.json": lock_package_lock,
                "yarn.lock": lock_yarn, "pnpm-lock.yaml": lock_pnpm, "poetry.lock": lock_toml, "uv.lock": lock_toml}


# ---------------------------------------------------------------- imports
def py_imports(text):
    """{module: guarded} plus dotted string constants (Django settings, Celery, plugin names)."""
    tree = ast.parse(text)
    guarded = set()
    for node in ast.walk(tree):
        body = []
        if isinstance(node, ast.Try):
            body = node.body
        elif isinstance(node, ast.If) and "TYPE_CHECKING" in ast.dump(node.test):
            body = node.body
        for b in body:
            guarded.update(id(n) for n in ast.walk(b))
    mods, strings = {}, set()
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module] + [node.module + "." + a.name for a in node.names if a.name != "*"]
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) < 100 \
                and DOTTED.match(node.value):
            strings.add(node.value.lower())
        for n in names:
            mods[n] = mods.get(n, True) and id(node) in guarded
    return mods, strings


def js_package(spec):
    """Bare specifier -> package name; 'node:' for node: builtins; None for relative, alias or virtual imports."""
    spec = spec.split("?")[0]
    if spec.startswith("node:"):
        return "node:"
    if not spec or spec[0] in "./#~$" or spec.startswith("@/") or "!" in spec or ":" in spec.split("/")[0]:
        return None
    parts = spec.split("/")
    if spec.startswith("@"):
        return "/".join(parts[:2]) if len(parts) > 1 and parts[1] else None
    return parts[0]


def go_imports(text):
    out = GO_SINGLE.findall(text)
    for block in GO_BLOCK.findall(text):
        for line in block.splitlines():
            m = re.search(r'"([^"]+)"', line.split("//")[0])
            if m:
                out.append(m.group(1))
    return out


# ---------------------------------------------------------------- helpers
def role(rel):
    parts = rel.split("/")
    if TEST_FILE.search(parts[-1]) or any(p in TEST_PARTS for p in parts[:-1]):
        return "test"
    if TOOL_FILE.search(parts[-1]) or any(p in TOOL_PARTS for p in parts[:-1]):
        return "tooling"
    return "runtime"


def dirname(rel):
    return rel.rsplit("/", 1)[0] if "/" in rel else ""


def under(rel, d):
    return d == "" or rel == d or rel.startswith(d + "/")


def ancestors(rel, dirs):
    """Manifest folders that cover file `rel`, nearest first."""
    return sorted((d for d in dirs if under(rel, d)), key=len, reverse=True)


def find_lock(root, d, names):
    """First of `names` in folder d or above it, up to the project root, or up to the enclosing git repository's
    root when the project sits inside one (a workspace package whose lockfile lives at the monorepo root)."""
    top = root
    while not os.path.exists(os.path.join(top, ".git")):
        if os.path.dirname(top) == top:
            top = root
            break
        top = os.path.dirname(top)
    path = os.path.join(root, d) if d else root
    while True:
        for n in names:
            if os.path.isfile(os.path.join(path, n)):
                return os.path.join(path, n)
        if path == top or os.path.dirname(path) == path:
            return None
        path = os.path.dirname(path)


def lock_covers(lock_path, folder):
    """True when the lockfile sits in `folder`, or lists `folder` as a workspace member from a parent folder."""
    base = os.path.dirname(lock_path)
    rel = os.path.relpath(folder, base).replace(os.sep, "/")
    if rel == ".":
        return True
    name, text = os.path.basename(lock_path), read(lock_path) or ""
    try:
        if name in ("package-lock.json", "npm-shrinkwrap.json"):
            return rel in (json.loads(text).get("packages") or {})
        if name == "pnpm-lock.yaml":
            return bool(re.search(r"(?m)^  '?%s'?:\s*$" % re.escape(rel), text))
        if name == "uv.lock":
            return bool(re.search(r'(?:editable|virtual)\s*=\s*"%s"' % re.escape(rel), text))
        if name in PY_LOCKS:
            return False
        ws = json.loads(read(os.path.join(base, "package.json")) or "{}").get("workspaces") or []
    except ValueError:
        return False
    ws = ws.get("packages") or [] if isinstance(ws, dict) else ws
    return any(fnmatch.fnmatch(rel, g.rstrip("/")) for g in ws)


def is_alias(spec, key):
    """tsconfig paths keys ending in * match as prefixes; other alias keys match exactly or as a folder."""
    if key.endswith("*"):
        return bool(key[:-1]) and spec.startswith(key[:-1])
    return spec == key or spec.startswith(key.rstrip("/") + "/")


def word_rx(token):
    return re.compile(r"(?<![\w@/.-])%s(?![\w-])" % re.escape(token), re.I)


def js_config_tokens(name):
    """Short names config files use for plugins and presets, e.g. eslint-plugin-react -> react."""
    toks = []
    for rx, rep in ((r"^eslint-(?:plugin|config)-(.+)$", r"\1"), (r"^(@[^/]+)/eslint-(?:plugin|config)$", r"\1"),
                    (r"^(@[^/]+)/eslint-(?:plugin|config)-(.+)$", r"\1/\2"), (r"^babel-(?:preset|plugin)-(.+)$", r"\1"),
                    (r"^@babel/(?:preset|plugin)-(.+)$", r"@babel/\1"), (r"^jest-environment-(.+)$", r"\1"),
                    (r"^@types/(.+)$", r"\1")):
        if re.match(rx, name):
            toks.append(re.sub(rx, rep, name))
    return toks


def short_rx(token):
    t = re.escape(token)
    return re.compile(r"(?m)(['\"](?:plugin:)?%s(?:/[\w-]+)?['\"]|plugin:%s/|^\s*-\s*['\"]?%s['\"]?\s*$)" % (t, t, t))


# ---------------------------------------------------------------- audit
def audit(root):
    root = os.path.abspath(root)
    files = []
    for d, dirs, names in os.walk(root):
        at_project = bool(PROJECT_FILES & set(names))
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.endswith(".egg-info")
                         and not (at_project and x in OUTPUT_DIRS)
                         and (not x.startswith(".") or x in DOT_DIRS)
                         and not os.path.exists(os.path.join(d, x, "pyvenv.cfg")))
        files += sorted(os.path.relpath(os.path.join(d, n), root).replace(os.sep, "/") for n in names)
    notes, findings, implicit, duplicates, manifests, lockinfo = [], [], [], [], [], {}
    text = lambda rel: read(os.path.join(root, rel))  # noqa: E731
    configs = {f: text(f) or "" for f in files
               if CONFIG_FILE.match(f.rsplit("/", 1)[-1]) or f.startswith(CONFIG_DIRS)}

    def add(kind, eco, name, conf, detail, manifest="", where=()):
        findings.append({"type": kind, "ecosystem": eco, "name": name, "confidence": conf, "manifest": manifest,
                         "detail": detail, "files": sorted(set(where))[:10]})

    def lock_for(d, names):
        path = find_lock(root, d, names)
        if not path:
            return None, None
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        if not lock_covers(path, os.path.join(root, d)):
            notes.append("%s is not a workspace member of %s; treated as having no lockfile" % (d or ".", rel))
            return None, None
        if rel not in lockinfo:
            parser = LOCK_PARSERS.get(os.path.basename(path))
            info = {"path": rel, "parsed": False, "packages": 0, "note": "present, not parsed"}
            if parser:
                try:
                    data, note = parser(read(path) or "")
                except (ValueError, KeyError, IndexError, AttributeError, TypeError) as e:
                    data, note = None, "could not parse: %s" % e
                info.update(note=note, parsed=data is not None, packages=len(data or {}))
                info["_data"] = data
            lockinfo[rel] = info
        return rel, lockinfo[rel].get("_data")

    deps = []  # every declared dependency record

    # ---- npm
    pkg_dirs, local_js, aliases = {}, set(), []
    for f in files:
        base = f.rsplit("/", 1)[-1]
        if base == "package.json":
            try:
                pj = json.loads(text(f) or "")
            except ValueError as e:
                notes.append("%s: not valid JSON (%s)" % (f, e))
                continue
            pkg_dirs[dirname(f)] = (f, pj)
            if isinstance(pj.get("name"), str):
                local_js.add(pj["name"])
        elif re.match(r"^[tj]sconfig[\w.-]*\.json$", base):
            m = re.search(r'"paths"\s*:\s*\{(.*?)\}', configs.get(f, ""), re.S)
            aliases += re.findall(r'"([^"]+)"\s*:', m.group(1)) if m else []
    for c, t in configs.items():  # bundler aliases: vite/webpack resolve.alias, babel module-resolver
        for m in re.finditer(r"\balias\w*['\"]?\s*[:=(]\s*[\[{]", t):
            chunk = t[m.end():m.end() + 1500]
            aliases += re.findall(r"['\"]([^'\"\s]+)['\"]\s*:", chunk) + re.findall(r"find:\s*['\"]([^'\"]+)['\"]", chunk)
    npm_decl = {}
    for d, (f, pj) in pkg_dirs.items():
        recs = {}
        for sec, kind in (("dependencies", "prod"), ("devDependencies", "dev"), ("peerDependencies", "peer"),
                          ("optionalDependencies", "optional")):
            for name, spec in (pj.get(sec) or {}).items():
                r = recs.setdefault(name, {"ecosystem": "npm", "name": name, "manifest": f, "kinds": [], "spec": str(spec),
                                           "locked": [], "used_in": []})
                r["kinds"].append(kind)
        lock_rel, lock = lock_for(d, NPM_LOCKS)
        manifests.append({"path": f, "ecosystem": "npm", "dependencies": len(recs), "lockfile": lock_rel})
        unlocked = []
        for r in recs.values():
            if lock is not None:
                r["locked"] = sorted(lock.get(r["name"], ()))
                if not r["locked"] and set(r["kinds"]) - {"peer"} and r["name"] not in local_js \
                        and not re.match(r"^(workspace|file|link|portal|npm):", r["spec"]):
                    add("missing-from-lock", "npm", r["name"], "medium",
                        "declared in %s but not in %s; reinstall to sync the lockfile" % (f, lock_rel), f)
            spec = r["spec"].rsplit("@", 1)[-1] if r["spec"].startswith("npm:") else r["spec"]
            if set(r["kinds"]) - {"peer"} and r["name"] not in local_js and not NPM_EXACT.match(spec) \
                    and not re.match(r"^\w[\w+]*:", spec):
                unlocked.append(r["name"])
        if not lock_rel and unlocked:
            add("unlocked", "npm", f, "medium", "%d version ranges and no lockfile (%s)" % (len(unlocked), ", ".join(unlocked[:8])), f)
        npm_decl[d] = recs
        deps += recs.values()

    js_files = [f for f in files if f.endswith(JS_EXT + CSS_EXT) and not f.endswith((".min.js", ".min.mjs", ".min.css"))]
    js_undeclared, js_strings = defaultdict(lambda: {"files": [], "conf": "medium", "dir": ""}), {}
    for f in js_files:
        scopes = ancestors(f, pkg_dirs)
        if not scopes:
            continue
        src, rl, css = text(f) or "", role(f), f.endswith(CSS_EXT)
        js_strings[f] = set(re.findall(r"""['"`]([@\w][\w.@/-]*)['"`]""", src))
        for type_only, spec in (("", s) for s in CSS_RX.findall(src)) if css else JS_RX.findall(src):
            name = js_package(spec)
            if not name or name == "node:" or not NPM_NAME.match(name):
                continue
            hit = next((npm_decl[d][name] for d in scopes if name in npm_decl[d]), None)
            if not hit and (css or name in local_js or any(is_alias(spec, a) for a in aliases)):
                continue  # CSS: `@use "variables"` may be a local partial, so CSS only ever marks a package as used
            if hit and css:
                hit["used_in"].append(f)
                continue
            if hit:
                hit["used_in"].append(f)
                if set(hit["kinds"]) == {"dev"} and rl == "runtime" and not type_only:
                    hit.setdefault("dev_runtime", []).append(f)
            elif name not in NODE_BUILTINS:
                u = js_undeclared[name]
                u["files"].append(f)
                u["dir"] = u["dir"] or scopes[0]
                if rl == "runtime":
                    u["conf"] = "high"
    for name, u in sorted(js_undeclared.items()):
        man = pkg_dirs[u["dir"]][0]
        add("undeclared", "npm", name, u["conf"], "imported but not declared in %s or a parent package.json" % man, man, u["files"])

    for d, recs in npm_decl.items():
        f, pj = pkg_dirs[d]
        scripts = " ".join(str(v) for v in (pj.get("scripts") or {}).values())
        extra = json.dumps({k: v for k, v in pj.items() if not k.lower().endswith("dependencies") and k != "scripts"})
        conf_texts = [(c, t) for c, t in configs.items() if under(c, d) or dirname(c) == ""] + [(f, extra)]
        for r in recs.values():
            name = r["name"]
            if r.get("dev_runtime"):
                bundled = any(BUNDLERS.match(n) for n in recs)
                add("dev-in-runtime", "npm", name, "low" if bundled else "medium", "devDependency imported from app "
                    "code" + ("; this package uses a bundler, so it may be inlined at build time" if bundled else
                              "; move it to dependencies"), f, r["dev_runtime"])
            if r["used_in"]:
                continue
            reason = None
            bins = JS_BINS.get(name, []) + [name] + [b for b in [name.rsplit("/", 1)[-1]] if b not in GENERIC]
            nm = find_lock(root, d, ["node_modules/%s/package.json" % name])
            if nm:
                try:
                    b = json.loads(read(nm) or "{}").get("bin")
                    bins += list(b) if isinstance(b, dict) else []
                except ValueError:
                    pass
            if any(word_rx(b).search(scripts) for b in bins):
                reason = "run from package.json scripts"
            if not reason and name.startswith("@types/"):
                base = name[7:]
                base = "@" + base.replace("__", "/") if "__" in base else base  # @types/babel__core -> @babel/core
                if base in NODE_BUILTINS | {"node"} or any(base in rr for rr in npm_decl.values()):
                    reason = "type definitions for %s" % base
            if not reason:
                named = next((s for s, strs in js_strings.items() if under(s, d) and name in strs), None)
                reason = "named in a string in %s (plugin, transport or dynamic load)" % named if named else None
            if not reason:
                for c, t in conf_texts:
                    if word_rx(name).search(t) or any(short_rx(tok).search(t) for tok in js_config_tokens(name)):
                        reason = "referenced in %s" % c
                        break
            if reason:
                implicit.append({"ecosystem": "npm", "name": name, "manifest": f, "reason": reason})
                continue
            kinds = set(r["kinds"])
            conf = "low" if (kinds <= {"peer", "optional"} or JS_IMPLICIT.match(name)) else (
                "high" if "prod" in kinds else "medium")
            add("unused", "npm", name, conf, "%s in %s; no import, script or config reference found"
                % ("/".join(sorted(kinds)), f), f)

    # ---- Python
    py_dirs = defaultdict(list)
    for f in files:
        base, d = f.rsplit("/", 1)[-1], dirname(f)
        if base == "pyproject.toml" or re.match(r"^requirements.*\.txt$", base):
            py_dirs[d].append(f)
        elif d.rsplit("/", 1)[-1] == "requirements" and base.endswith(".txt"):
            py_dirs[dirname(d)].append(f)
    py_decl, tool_tables = {}, set()
    for d, mfiles in py_dirs.items():
        recs = {}
        lock_rel, lock = lock_for(d, PY_LOCKS)
        pinned_req = False
        for f in mfiles:
            raw = text(f) or ""
            if f.endswith(".toml"):
                try:
                    data = load_toml(raw)
                except (ValueError, IndexError) as e:
                    notes.append("%s: could not parse (%s)" % (f, e))
                    continue
                entries = read_pyproject(data)
                tool_tables.update(k.lower() for k in (data.get("tool") or {}))
            else:
                dev = DEV_REQ_FILE.search(f.rsplit("/", 1)[-1].replace("requirements", ""))
                entries = [(n, s, "dev" if dev else "prod", "", is_pinned_pep508(s)) for n, s in read_requirements(raw)]
                pinned_req = pinned_req or (bool(entries) and all(e[4] for e in entries))
            manifests.append({"path": f, "ecosystem": "python", "dependencies": len(entries), "lockfile": lock_rel})
            ranges = [e[0] for e in entries if not e[4]]
            for name, spec, kind, group, _pinned in entries:
                key = norm_py(name)
                r = recs.setdefault(key, {"ecosystem": "python", "name": name, "manifest": f, "kinds": [], "spec": spec,
                                          "locked": [], "used_in": [], "groups": []})
                r["kinds"].append(kind)
                if group:
                    r["groups"].append(group)
                if lock is not None:
                    r["locked"] = sorted(lock.get(key, ()))
                    if not r["locked"] and f.endswith(".toml"):
                        add("missing-from-lock", "python", name, "medium", "declared in %s but not in %s; re-lock"
                            % (f, lock_rel), f)
            if ranges and not lock_rel and not (f.endswith(".toml") and pinned_req):
                add("unlocked", "python", f, "low" if f.endswith(".toml") else "medium",
                    "%d entries use ranges and no lockfile exists (%s)" % (len(ranges), ", ".join(ranges[:8])), f)
        py_decl[d] = recs
        deps += recs.values()
    for f in files:  # setup.cfg / tox.ini sections like [flake8] or [tool:pytest]
        if f.rsplit("/", 1)[-1] in ("setup.cfg", "tox.ini"):
            tool_tables.update(m.lower() for m in re.findall(r"(?m)^\[(?:tool:)?([\w-]+)", text(f) or ""))

    py_files = [f for f in files if f.endswith(".py")]
    local_py = set()
    for f in py_files:
        local_py.update(p[:-3] if p.endswith(".py") else p for p in f.split("/"))
    py_undeclared = defaultdict(lambda: {"files": [], "conf": "low", "dir": None})
    py_strings, unparsed = defaultdict(set), []

    def py_mods(r):
        n = norm_py(r["name"])
        if n in PY_IMPORT_NAMES:
            return [m.lower() for m in PY_IMPORT_NAMES[n]]
        if n.startswith("google-cloud-"):
            return ["google.cloud." + n[13:].replace("-", "_")]
        if n.startswith("azure-"):
            return [n.replace("-", ".")]
        return [n.replace("-", "_")]

    def py_match(mod, r):
        mod = mod.lower()
        return any(mod == m or mod.startswith(m + ".") or m.startswith(mod + ".") for m in py_mods(r))

    for f in py_files:
        try:
            mods, strings = py_imports(text(f) or "")
        except (SyntaxError, ValueError):
            unparsed.append(f)
            continue
        scopes = ancestors(f, py_dirs)
        for d in scopes:
            py_strings[d] |= strings
        rl = role(f)
        for mod, guard in mods.items():
            top = mod.split(".")[0]
            if "." in mod and mod.rsplit(".", 1)[0] in mods:
                continue  # the `from x import y` expansion of an import already seen
            hits = [r for d in scopes for r in py_decl[d].values() if py_match(mod, r)]
            for r in hits:
                r["used_in"].append(f)
                if set(r["kinds"]) == {"dev"} and rl == "runtime" and not guard:
                    r.setdefault("dev_runtime", []).append(f)
            if not hits and top not in PY_STDLIB and top not in local_py:  # declared wins over a same-named local file
                u = py_undeclared[top]
                u["files"].append(f)
                u["dir"] = scopes[0] if scopes else u["dir"]
                conf = "low" if guard else ("high" if rl == "runtime" else "medium")
                u["conf"] = max(u["conf"], conf, key=["low", "medium", "high"].index)
    if unparsed:
        notes.append("%d Python files could not be parsed (syntax errors or Python 2): %s" % (len(unparsed), ", ".join(unparsed[:5])))
    orphans = []
    for top, u in sorted(py_undeclared.items()):
        hint = PY_HINT.get(top.lower())
        if u["dir"] is None:
            orphans.append(top)
            continue
        man = py_dirs[u["dir"]][0]
        add("undeclared", "python", top, u["conf"], "imported but not declared in %s%s"
            % (" / ".join(py_dirs[u["dir"]]), " (the distribution is probably %s)" % hint if hint else ""), man, u["files"])
    if orphans:
        add("no-manifest", "python", ", ".join(orphans[:10]), "medium", "Python files import %d third-party modules "
            "but no pyproject.toml or requirements*.txt covers them" % len(orphans), "",
            [f for t in orphans for f in py_undeclared[t]["files"]])

    for d, recs in py_decl.items():
        conf_texts = [(c, t) for c, t in configs.items() if under(c, d) or dirname(c) == ""]
        for r in recs.values():
            if r.get("dev_runtime"):
                add("dev-in-runtime", "python", r["name"], "medium", "only in a dev group or dev requirements file but "
                    "imported from app code", r["manifest"], r["dev_runtime"])
            if r["used_in"]:
                continue
            n = norm_py(r["name"])
            toks = {n, r["name"].lower()} | {m.split(".")[0] for m in py_mods(r)}
            reason = None
            if toks & tool_tables:
                reason = "configured under [tool.%s] or a setup.cfg/tox.ini section" % sorted(toks & tool_tables)[0]
            elif any(py_match(s, r) for s in py_strings[d]):
                reason = "named in a string (settings, plugin or app list)"
            else:
                for c, t in conf_texts:
                    if any(word_rx(tok).search(t) for tok in toks):
                        reason = "referenced in %s" % c
                        break
            if reason:
                implicit.append({"ecosystem": "python", "name": r["name"], "manifest": r["manifest"], "reason": reason})
                continue
            kinds = set(r["kinds"])
            conf = "low" if PY_IMPLICIT.match(n) else ("high" if "prod" in kinds else "medium")
            add("unused", "python", r["name"], conf, "%s in %s; no import or config reference found"
                % ("/".join(sorted(set(r["groups"]) or kinds)), r["manifest"]), r["manifest"])

    # ---- Go
    go_mods = {}
    for f in files:
        if f.rsplit("/", 1)[-1] == "go.mod":
            mod, req, replaced, tools = read_go_mod(text(f) or "")
            go_mods[dirname(f)] = {"file": f, "module": mod, "requires": req, "replaced": replaced, "tools": tools}
    local_go = {m["module"] for m in go_mods.values() if m["module"]}
    for d, m in go_mods.items():
        sum_rel = (d + "/go.sum") if d else "go.sum"
        has_sum = os.path.isfile(os.path.join(root, sum_rel))
        manifests.append({"path": m["file"], "ecosystem": "go", "dependencies": len(m["requires"]),
                          "lockfile": sum_rel if has_sum else None})
        sums = set()
        if has_sum:
            for line in (text(sum_rel) or "").splitlines():
                p = line.split()
                if len(p) >= 2:
                    sums.add((p[0], p[1].replace("/go.mod", "")))
            lockinfo[sum_rel] = {"path": sum_rel, "parsed": True, "packages": len({s[0] for s in sums}),
                                 "note": "checksums only; versions come from go.mod"}
        majors = defaultdict(list)
        for path, info in m["requires"].items():
            r = {"ecosystem": "go", "name": path, "manifest": m["file"], "kinds": ["indirect" if info["indirect"] else "prod"],
                 "spec": info["version"], "locked": [info["version"]], "used_in": []}
            m.setdefault("recs", {})[path] = r
            deps.append(r)
            majors[re.sub(r"(/v\d+|\.v\d+)$", "", path)].append(path)
            if path not in m["replaced"] and (path, info["version"]) not in sums:
                add("missing-from-lock", "go", path, "medium", "%s %s has no go.sum line%s" % (
                    path, info["version"], "" if has_sum else " (there is no go.sum)"), m["file"])
        for base, paths in majors.items():
            if len(paths) > 1:
                duplicates.append({"ecosystem": "go", "name": base, "versions": sorted(paths), "lockfile": m["file"],
                                   "direct": True})
    go_dirs, go_undeclared = list(go_mods), defaultdict(list)
    for f in files:
        if not f.endswith(".go"):
            continue
        scopes = ancestors(f, go_dirs)
        if not scopes:
            continue
        m = go_mods[scopes[0]]
        for imp in go_imports(text(f) or ""):
            first = imp.split("/")[0]
            if "." not in first or any(imp == lm or imp.startswith(lm + "/") for lm in local_go):
                continue  # stdlib (no dot in the first element) or one of this repo's modules
            owners = [p for p in m["requires"] if imp == p or imp.startswith(p + "/")]
            if owners:
                m["recs"][max(owners, key=len)]["used_in"].append(f)
            else:
                go_undeclared[(imp, m["file"])].append(f)
    for (imp, gm), where in sorted(go_undeclared.items()):
        add("undeclared", "go", imp, "high", "imported but no require in %s provides it" % gm, gm, where)
    for m in go_mods.values():
        for path, r in m.get("recs", {}).items():
            if r["used_in"] or m["requires"][path]["indirect"]:
                continue
            if any(t == path or t.startswith(path + "/") for t in m["tools"]):
                implicit.append({"ecosystem": "go", "name": path, "manifest": m["file"], "reason": "tool directive"})
                continue
            add("unused", "go", path, "high", "direct require in %s but no .go file imports it; `go mod tidy` "
                "would mark it indirect or drop it" % m["file"], m["file"])

    # ---- duplicates in parsed lockfiles
    declared = {r["name"] for r in deps} | {norm_py(r["name"]) for r in deps if r["ecosystem"] == "python"}
    for rel, info in sorted(lockinfo.items()):
        for name, versions in sorted((info.pop("_data", None) or {}).items()):
            if len(versions) > 1:
                duplicates.append({"ecosystem": "python" if rel.endswith(".lock") and not rel.endswith("yarn.lock")
                                   else "npm", "name": name, "versions": sorted(versions), "lockfile": rel,
                                   "direct": name in declared})
    if not manifests:
        return None
    ecos = {m["ecosystem"] for m in manifests}
    nxt = []
    if "npm" in ecos:
        locks = " ".join(lockinfo)
        pm = "pnpm" if "pnpm-lock" in locks else "yarn" if "yarn.lock" in locks else "npm"
        nxt += ["%s outdated" % pm, "%s audit --json" % pm]
    if "python" in ecos:
        reqs = sorted((m["path"] for m in manifests if m["ecosystem"] == "python" and m["path"].endswith(".txt")),
                      key=lambda p: bool(DEV_REQ_FILE.search(p.rsplit("/", 1)[-1].replace("requirements", ""))))
        nxt += ["pip-audit" + (" -r %s" % reqs[0] if reqs else ""), "pip list --outdated (inside the project's venv)"]
    if "go" in ecos:
        nxt += ["go list -u -m all", "govulncheck ./..."]
    for f in findings:
        f["counts"] = f["confidence"] in ("high", "medium")
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda x: (order[x["confidence"]], x["type"], x["ecosystem"], x["name"]))
    duplicates.sort(key=lambda x: (not x["direct"], -len(x["versions"]), x["name"]))
    return {"root": os.path.basename(root) or root, "toml_reader": "tomllib" if tomllib else "built-in subset reader",
            "manifests": manifests, "lockfiles": list(lockinfo.values()),
            "files_scanned": {"js_ts": sum(not f.endswith(CSS_EXT) for f in js_files), "python": len(py_files) - len(unparsed),
                              "go": sum(f.endswith(".go") for f in files)},
            "dependencies": [dict({k: v for k, v in r.items() if k not in ("used_in", "dev_runtime")},
                                  imported_by=len(set(r["used_in"]))) for r in deps],
            "findings": findings, "implicit": implicit, "duplicates": duplicates, "notes": notes,
            "next_commands": nxt}


def render(rep):
    out = ["Dependency audit: %s  (offline: manifests, imports and lockfiles; no registry lookups)" % rep["root"]]
    out.append("Manifests: " + ", ".join("%s (%d)" % (m["path"], m["dependencies"]) for m in rep["manifests"]))
    out.append("Lockfiles: " + (", ".join("%s %s" % (lk["path"], ("(%d packages)" % lk["packages"]) if lk["parsed"]
                                                     else "(%s)" % lk["note"]) for lk in rep["lockfiles"]) or "none"))
    fs = rep["files_scanned"]
    out.append("Scanned: %d JS/TS, %d Python, %d Go files" % (fs["js_ts"], fs["python"], fs["go"]))
    counted = [f for f in rep["findings"] if f["counts"]]
    low = [f for f in rep["findings"] if not f["counts"]]
    out.append("")
    out.append("Findings: %d (high/medium)" % len(counted) + ("  + %d low confidence" % len(low) if low else ""))
    shown = (counted + low)[:40]
    for f in shown:
        where = (" <- " + ", ".join(f["files"][:3]) + (" +%d" % (len(f["files"]) - 3) if len(f["files"]) > 3 else "")) \
            if f["files"] and f["type"] != "missing-from-lock" else ""
        out.append("  %-18s %-6s %-6s %s: %s%s" % (f["type"], f["confidence"], f["ecosystem"], f["name"], f["detail"], where))
    if len(counted) + len(low) > len(shown):
        out.append("  ... %d more in --json" % (len(counted) + len(low) - len(shown)))
    if rep["implicit"]:
        out.append("")
        out.append("Implicit (not imported, but used from scripts or config; check before removing):")
        out += ["  %-6s %s: %s" % (i["ecosystem"], i["name"], i["reason"]) for i in rep["implicit"][:25]]
        if len(rep["implicit"]) > 25:
            out.append("  ... %d more in --json" % (len(rep["implicit"]) - 25))
    if rep["duplicates"]:
        direct = sum(d["direct"] for d in rep["duplicates"])
        out.append("")
        out.append("Locked at several versions (info): %d packages, %d of them direct" % (len(rep["duplicates"]), direct))
        out += ["  %-6s %s: %s%s" % (d["ecosystem"], d["name"], ", ".join(d["versions"][:5]), "  (direct)" if d["direct"] else "")
                for d in rep["duplicates"][:10]]
    if rep["notes"]:
        out.append("")
        out += ["Note: " + n for n in rep["notes"]]
    out.append("")
    out.append("Needs network, run yourself: " + " · ".join(rep["next_commands"]))
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 epilog="Exit codes: 0 nothing at high/medium confidence, 1 findings, "
                                        "2 bad arguments or nothing to read",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-C", dest="project", default=".", help="project folder (default: current directory)")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    args = ap.parse_args(argv)
    if not os.path.isdir(args.project):
        print("dep_audit: %s is not a folder" % args.project, file=sys.stderr)
        return 2
    rep = audit(args.project)
    if rep is None:
        print("dep_audit: no package.json, pyproject.toml, requirements*.txt or go.mod under %s" % args.project,
              file=sys.stderr)
        return 2
    print(json.dumps(rep, indent=2) if args.json else render(rep))
    return 1 if any(f["counts"] for f in rep["findings"]) else 0


if __name__ == "__main__":
    sys.exit(main())
