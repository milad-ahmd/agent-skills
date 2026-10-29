#!/usr/bin/env python3
"""Map a codebase's import graph: modules, entry points, hub files, cycles and orphans. Stdlib only.

Usage: python3 code_map.py [path] [--format text|json|mermaid|html] [--out FILE] [--depth N]
                           [--exclude GLOB ...] [--level module|file] [--focus DIR] [--max-nodes N] [--max-edges N]
                           [--top N] [--hub-min N] [--skip-tests] [--max-files N] [--max-bytes N]
  path          repo root or any directory inside it (default: .)
  --format      text (summary, default) · json (full model) · mermaid (flowchart LR, trimmed to the
                heaviest edges) · html (one self-contained interactive page, no network requests)
  --depth       directory levels that make a module (default: picked so the module list stays readable)
  --exclude     glob matched against relative paths and names; repeatable or comma-separated
  --level       mermaid only: draw modules (default) or files
  --focus       mermaid only: draw the files under one folder, with imports resolved against the whole repo

Parsed: Python (import / from-import, relative imports, importlib.import_module("literal")),
JS/TS (import, export-from, require, import(); relative paths, index files, tsconfig/jsconfig
paths + baseUrl, workspace package names) and Go (imports resolved through go.mod). Other
languages are counted but get no edges.
Not visible to a static parse: imports built from strings at runtime, DI containers, reflection,
plugin loading, framework conventions (routes, decorators) and calls between services over HTTP.

Exit codes: 0 ok · 2 nothing to analyse (no source files found, or the path is not a directory)
It only reads files. It never imports or executes the code it maps.
"""
import argparse
import ast
import fnmatch
import html
import json
import os
import posixpath
import re
import sys
import sysconfig
from collections import Counter, defaultdict, deque

SCHEMA_VERSION = 1
SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "venv", ".venv", "env", "vendor", "dist", "build", "__pycache__",
    "target", ".next", ".nuxt", ".svelte-kit", ".turbo", "coverage", ".tox", ".nox", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".gradle", ".idea", ".vscode", ".cache", ".parcel-cache", ".eggs",
    "bower_components", "site-packages", "out", ".terraform", "Pods", "DerivedData",
}
LANG_BY_EXT = {
    ".py": "python", ".go": "go",
    ".ts": "ts", ".tsx": "ts", ".mts": "ts", ".cts": "ts",
    ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js",
    ".vue": "vue", ".svelte": "svelte", ".astro": "astro",
    ".java": "java", ".kt": "kotlin", ".kts": "kotlin", ".scala": "scala", ".groovy": "groovy",
    ".rb": "ruby", ".rs": "rust", ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp",
    ".cs": "csharp", ".fs": "fsharp", ".swift": "swift", ".m": "objc", ".mm": "objc", ".php": "php",
    ".dart": "dart", ".ex": "elixir", ".exs": "elixir", ".erl": "erlang", ".clj": "clojure", ".lua": "lua",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell", ".r": "r", ".jl": "julia", ".zig": "zig",
    ".hs": "haskell", ".ml": "ocaml", ".pl": "perl", ".nim": "nim", ".sol": "solidity",
}
JS_LANGS = {"ts", "js", "vue", "svelte", "astro"}
PARSED = JS_LANGS | {"python", "go"}
RESOLVE_EXTS = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte", ".d.ts")
TS_FOR_JS = {".js": (".ts", ".tsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",)}
NODE_BUILTINS = set(
    "assert async_hooks buffer child_process cluster console constants crypto dgram diagnostics_channel dns "
    "domain events fs http http2 https inspector module net os path perf_hooks process punycode querystring "
    "readline repl stream string_decoder sys test timers tls trace_events tty url util v8 vm wasi "
    "worker_threads zlib".split())
TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs", "e2e"}
TEST_NAME = re.compile(r"(test_.*\.py|.*_test\.(py|go)|conftest\.py|.*\.(test|spec)\.[cm]?[jt]sx?)$")
ENTRY_STEMS = {"main", "cli", "app", "server", "__main__", "manage", "wsgi", "asgi"}
NOT_ORPHAN = re.compile(r"(__init__\.py|conftest\.py|setup\.py|.*\.config\.[cm]?[jt]s|.*\.d\.ts|"
                        r"vite-env\.d\.ts|next-env\.d\.ts)$")
MAIN_GUARD = re.compile(r"""^if\s+__name__\s*==\s*['"]__main__['"]\s*:""", re.M)
GO_MAIN = re.compile(r"^\s*package\s+main\b", re.M)
STATIC_LIMITS = ("imports built from strings at runtime, DI containers, reflection, plugin loading, "
                 "framework conventions (routes, decorators) and calls between services over HTTP")


# --- small helpers ---------------------------------------------------------------------
def pjoin(*parts):
    return posixpath.normpath(posixpath.join(*[p for p in parts if p])) if any(parts) else ""


def lang_of(name):
    ext = os.path.splitext(name)[1]
    return LANG_BY_EXT.get(ext) or LANG_BY_EXT.get(ext.lower())


def is_test(path):
    parts = path.split("/")
    return any(p in TEST_DIRS for p in parts[:-1]) or bool(TEST_NAME.match(parts[-1]))


def excluded(rel, name, pats):
    return any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(name, p) for p in pats)


_SPECIAL = re.compile(r"['\"`/]")


def strip_c_comments(s):
    """Drop // and /* */ comments, keep string literals (JS, TS, Go, JSONC)."""
    out, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c in "'\"`":
            j = i + 1
            while j < n and s[j] != c:
                if s[j] == "\\":
                    j += 1
                elif s[j] == "\n" and c != "`":
                    break
                j += 1
            out.append(s[i:j + 1])
            i = j + 1
        elif s.startswith("//", i):
            j = s.find("\n", i)
            i = n if j < 0 else j
        elif s.startswith("/*", i):
            j = s.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("\n" * s.count("\n", i, j))
            i = j
        else:
            m = _SPECIAL.search(s, i + 1) if c == "/" else _SPECIAL.search(s, i)
            j = m.start() if m else n
            out.append(s[i:j])
            i = j
    return "".join(out)


def load_jsonc(path):
    """json.load that tolerates comments and trailing commas (tsconfig style). None on failure."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = strip_c_comments(f.read())
        text = re.sub(r'("(?:\\.|[^"\\])*")|,(?=\s*[}\]])', lambda m: m.group(1) or "", text)
        return json.loads(text)
    except (OSError, ValueError):
        return None


def stdlib_names():
    names = set(sys.builtin_module_names) | {"__future__", "__main__", "typing_extensions"}
    if hasattr(sys, "stdlib_module_names"):
        return names | set(sys.stdlib_module_names)
    paths = sysconfig.get_paths()
    for key in ("stdlib", "platstdlib"):
        p = paths.get(key)
        for d in (p, os.path.join(p or "", "lib-dynload")):
            try:
                for n in os.listdir(d):
                    base = n.split(".")[0]
                    if base.isidentifier() and base != "site":
                        names.add(base)
            except OSError:
                pass
    return names


def tarjan(nodes, adj):
    """Strongly connected components, iterative (no recursion limit). Returns a list of member lists."""
    index, low, on, stack, out, counter = {}, {}, set(), [], [], [0]

    def push(v):
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)

    for v in nodes:
        if v in index:
            continue
        push(v)
        work = [(v, iter(adj.get(v, ())))]
        while work:
            node, it = work[-1]
            for w in it:
                if w not in index:
                    push(w)
                    work.append((w, iter(adj.get(w, ()))))
                    break
                if w in on:
                    low[node] = min(low[node], index[w])
            else:
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    comp = []
                    while True:
                        w = stack.pop()
                        on.discard(w)
                        comp.append(w)
                        if w == node:
                            break
                    out.append(sorted(comp))
    return out


def cycle_path(start, members, adj):
    """Shortest cycle through `start` that stays inside `members` (BFS)."""
    prev, q = {}, deque()
    for w in adj.get(start, ()):
        if w in members and w not in prev:
            prev[w] = start
            q.append(w)
    while q:
        v = q.popleft()
        if v == start:
            break
        for w in adj.get(v, ()):
            if w in members and w not in prev:
                prev[w] = v
                q.append(w)
    if start not in prev:
        return [start]
    path, v = [start], prev[start]
    while v != start:
        path.append(v)
        v = prev[v]
    return [start] + path[1:][::-1] + [start]


def ranks(n, edges):
    """Layer index per node for a left-to-right layout: importers left, dependencies right. -1 = isolated."""
    adj = defaultdict(set)
    touched = set()
    for s, t in edges:
        if s != t:
            adj[s].add(t)
            touched.update((s, t))
    comps = tarjan(range(n), adj)
    comp_of = {v: i for i, c in enumerate(comps) for v in c}
    cadj, indeg = defaultdict(set), Counter()
    for s in adj:
        for t in adj[s]:
            a, b = comp_of[s], comp_of[t]
            if a != b and b not in cadj[a]:
                cadj[a].add(b)
                indeg[b] += 1
    rank = [0] * len(comps)
    q = deque(sorted(i for i in range(len(comps)) if indeg[i] == 0))
    while q:
        c = q.popleft()
        for d in sorted(cadj[c]):
            rank[d] = max(rank[d], rank[c] + 1)
            indeg[d] -= 1
            if indeg[d] == 0:
                q.append(d)
    return [rank[comp_of[v]] if v in touched else -1 for v in range(n)]


# --- import extraction -----------------------------------------------------------------
class _PyImports(ast.NodeVisitor):
    def __init__(self):
        self.out, self.lazy = [], 0

    def _nested(self, node):
        self.lazy += 1
        self.generic_visit(node)
        self.lazy -= 1

    visit_FunctionDef = visit_AsyncFunctionDef = visit_Lambda = _nested

    def visit_If(self, node):
        t = node.test
        if (isinstance(t, ast.Name) and t.id == "TYPE_CHECKING") or \
                (isinstance(t, ast.Attribute) and t.attr == "TYPE_CHECKING"):
            self.lazy += 1
            for s in node.body:
                self.visit(s)
            self.lazy -= 1
            for s in node.orelse:
                self.visit(s)
        else:
            self.generic_visit(node)

    def visit_Import(self, node):
        for a in node.names:
            self.out.append((0, a.name, None, self.lazy > 0))

    def visit_ImportFrom(self, node):
        self.out.append((node.level or 0, node.module or "", [a.name for a in node.names], self.lazy > 0))

    def visit_Call(self, node):
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
        if name in ("import_module", "__import__") and node.args and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str):
            spec = node.args[0].value
            level = len(spec) - len(spec.lstrip("."))
            self.out.append((level, spec[level:], None, True))
        self.generic_visit(node)


PY_IMPORT_RX = re.compile(r"^([ \t]*)import[ \t]+([\w. \t,]+)", re.M)
PY_FROM_RX = re.compile(r"^([ \t]*)from[ \t]+(\.*)([\w.]*)[ \t]+import[ \t]+(\([^)]*\)|[^\n#;]+)", re.M)


def py_imports(text):
    """[(level, module, names|None, lazy)] and whether parsing needed the regex fallback."""
    try:
        v = _PyImports()
        v.visit(ast.parse(text))
        return v.out, False
    except (SyntaxError, ValueError, RecursionError):
        out = []
        for m in PY_IMPORT_RX.finditer(text):
            for part in m.group(2).split(","):
                name = part.strip().split()[0] if part.strip() else ""
                if name:
                    out.append((0, name, None, bool(m.group(1))))
        for m in PY_FROM_RX.finditer(text):
            names = [p.strip().split()[0] for p in m.group(4).strip("()").replace("\n", " ").split(",") if p.strip()]
            out.append((len(m.group(2)), m.group(3), names, bool(m.group(1))))
        return out, True


JS_RX = [
    (re.compile(r"""\bimport\s+(type\s+)?(?:[\w$*{}\s,]+?\s+from\s*)?(['"])([^'"\n]+)\2"""), "import"),
    (re.compile(r"""\bexport\s+(type\s+)?(?:\*(?:\s+as\s+[\w$]+)?|\{[^}]*\})\s*from\s*(['"])([^'"\n]+)\2"""),
     "re-export"),
    (re.compile(r"""\brequire\s*\(\s*()(['"`])([^'"`\n]+)\2\s*\)"""), "require"),
    (re.compile(r"""\bimport\s*\(\s*()(['"`])([^'"`\n]+)\2\s*[,)]"""), "dynamic"),
]


def js_imports(text):
    """[(spec, kind, lazy)] from JS/TS source (comments removed first)."""
    text = strip_c_comments(text)
    out = []
    for rx, kind in JS_RX:
        for m in rx.finditer(text):
            spec = m.group(3)
            if "${" in spec:
                continue
            typed = bool(m.group(1))
            out.append((spec, "type" if typed else kind, typed or kind == "dynamic"))
    return out


GO_BLOCK = re.compile(r"^\s*import\s*\(([^)]*)\)", re.M)
GO_LINE = re.compile(r'^\s*import\s+(?:[\w.]+\s+)?"([^"]+)"', re.M)
GO_SPEC = re.compile(r'"([^"]+)"')


def go_imports(text):
    text = strip_c_comments(text)
    out = [m.group(1) for m in GO_LINE.finditer(text)]
    for b in GO_BLOCK.finditer(text):
        out += GO_SPEC.findall(b.group(1))
    return out, bool(GO_MAIN.search(text))


# --- the map -----------------------------------------------------------------------------
class CodeMap:
    def __init__(self, root, excludes=(), max_files=20000, max_bytes=1_000_000, skip_tests=False):
        self.root = os.path.abspath(root)
        self.excludes = list(excludes)
        self.max_files, self.max_bytes, self.skip_tests = max_files, max_bytes, skip_tests
        self.files = {}          # rel -> {"lang", "loc", "test", ...}
        self.cfg = {"tsconfig": [], "package": [], "gomod": [], "pyproject": [], "setup": []}
        self.skipped_large = 0
        self.truncated = False
        self.parse_errors = 0
        self.edges = {}          # (src, dst) -> {"kind", "lazy"}
        self.external = defaultdict(set)   # file -> {package}
        self.ext_lang = {}
        self.unresolved = []
        self.entries = defaultdict(list)   # file -> [reasons]

    # walk
    def walk(self):
        for dirpath, dirs, names in os.walk(self.root):
            rel_dir = os.path.relpath(dirpath, self.root).replace(os.sep, "/")
            rel_dir = "" if rel_dir == "." else rel_dir
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info")
                             and not excluded(pjoin(rel_dir, d), d, self.excludes))
            for n in sorted(names):
                rel = pjoin(rel_dir, n)
                if excluded(rel, n, self.excludes):
                    continue
                if n in ("tsconfig.json", "jsconfig.json"):
                    self.cfg["tsconfig"].append(rel)
                elif n == "package.json":
                    self.cfg["package"].append(rel)
                elif n == "go.mod":
                    self.cfg["gomod"].append(rel)
                elif n == "pyproject.toml":
                    self.cfg["pyproject"].append(rel)
                elif n in ("setup.py", "setup.cfg"):
                    self.cfg["setup"].append(rel)
                lang = lang_of(n)
                if not lang or n.endswith((".min.js", ".bundle.js")):
                    continue
                test = is_test(rel)
                if test and self.skip_tests:
                    continue
                path = os.path.join(dirpath, n)
                try:
                    if not os.path.isfile(path):
                        continue
                    if os.path.getsize(path) > self.max_bytes:
                        self.skipped_large += 1
                        continue
                except OSError:
                    continue
                if len(self.files) >= self.max_files:
                    self.truncated = True
                    return
                self.files[rel] = {"lang": lang, "test": test, "loc": 0}

    def read(self, rel):
        try:
            with open(os.path.join(self.root, rel), encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return ""

    def add_edge(self, src, dst, kind, lazy):
        if src == dst or dst not in self.files:
            return
        e = self.edges.get((src, dst))
        if e is None:
            self.edges[(src, dst)] = {"kind": kind, "lazy": lazy}
        elif e["lazy"] and not lazy:
            self.edges[(src, dst)] = {"kind": kind, "lazy": False}

    def add_external(self, src, name, lang):
        self.external[src].add(name)
        self.ext_lang.setdefault(name, lang)

    def miss(self, src, spec):
        self.unresolved.append({"file": src, "import": spec})

    # python
    def py_setup(self):
        pyfiles = [f for f, i in self.files.items() if i["lang"] == "python"]
        self.py_set = set(pyfiles)
        self.py_pkgs = {posixpath.dirname(f) for f in pyfiles if posixpath.basename(f) == "__init__.py"}
        roots = {""}
        if any(f.startswith("src/") for f in pyfiles):
            roots.add("src")
        for d in self.py_pkgs:
            top = d
            while top and posixpath.dirname(top) in self.py_pkgs:
                top = posixpath.dirname(top)
            roots.add(posixpath.dirname(top) if top else "")
        # scanning a package folder itself: its modules are also importable as <folder>.<module>
        own = os.path.basename(self.root) if "" in self.py_pkgs else ""
        self.py_index = defaultdict(list)
        for f in pyfiles:
            for r in roots:
                if r and not f.startswith(r + "/"):
                    continue
                parts = (f[len(r) + 1:] if r else f)[:-3].split("/")
                if parts[-1] == "__init__":
                    parts = parts[:-1]
                if own and not r:
                    names = [[own] + parts] + ([parts] if parts else [])
                else:
                    names = [parts] if parts else []
                for ps in names:
                    if all(p.isidentifier() for p in ps):
                        self.py_index[".".join(ps)].append(f)
        self.stdlib = stdlib_names()

    def py_file(self, base):
        for c in (base + ".py", pjoin(base, "__init__.py")):
            if c in self.py_set:
                return c
        return None

    def py_lookup(self, dotted, src):
        cands = self.py_index.get(dotted)
        if not cands:
            return None
        sp = src.split("/")

        def score(c):
            cp = c.split("/")
            k = 0
            while k < min(len(cp), len(sp)) and cp[k] == sp[k]:
                k += 1
            return (-k, len(c), c)
        return min(cands, key=score)

    def py_find(self, dotted, src):
        d = posixpath.dirname(src)
        script_dir = d not in self.py_pkgs   # a script's own folder is on sys.path
        parts = dotted.split(".")
        for k in range(len(parts), 0, -1):
            if script_dir:
                hit = self.py_file(pjoin(d, *parts[:k]))
                if hit:
                    return hit
            hit = self.py_lookup(".".join(parts[:k]), src)
            if hit:
                return hit
        return None

    def py_resolve(self, src, text):
        imports, fallback = py_imports(text)
        self.parse_errors += fallback
        if MAIN_GUARD.search(text):
            self.entries[src].append("__main__ guard")
        for level, module, names, lazy in imports:
            kind = "import"
            if level:
                base = posixpath.dirname(src)
                ok = True
                for _ in range(level - 1):
                    if not base:
                        ok = False
                        break
                    base = posixpath.dirname(base)
                if not ok:
                    self.miss(src, "." * level + module)
                    continue
                modpath = pjoin(base, *module.split(".")) if module else base
                hits, need_mod = [], not names or "*" in names
                for n in names or []:
                    t = self.py_file(pjoin(modpath, n)) if n != "*" else None
                    if t:
                        hits.append(t)
                    elif n != "*":
                        need_mod = True
                if need_mod:
                    t = self.py_file(modpath)
                    if t:
                        hits.append(t)
                    elif module or not hits:
                        self.miss(src, "." * level + module)
                for t in hits:
                    self.add_edge(src, t, kind, lazy)
                continue
            if not module:
                continue
            targets = []
            for n in names or [None]:
                full = module + "." + n if n and n != "*" else module
                hit = self.py_find(full, src)
                if hit and hit not in targets:
                    targets.append(hit)
            if targets:
                for t in targets:
                    self.add_edge(src, t, kind, lazy)
            else:
                top = module.split(".")[0]
                if top not in self.stdlib:
                    self.add_external(src, top, "python")

    # js / ts
    def js_setup(self):
        self.ts_cache = {}
        # tsconfig.json wins over jsconfig.json in the same folder
        self.ts_dirs = {posixpath.dirname(p): p for p in sorted(self.cfg["tsconfig"], key=lambda p: p.endswith("tsconfig.json"))}
        self.pkgs = {}           # name -> dir
        self.pkg_dirs = set()
        for p in self.cfg["package"]:
            data = load_jsonc(os.path.join(self.root, p))
            if not isinstance(data, dict):
                continue
            d = posixpath.dirname(p)
            self.pkg_dirs.add(d)
            if isinstance(data.get("name"), str):
                self.pkgs[data["name"]] = (d, data)
        self.pkg_names = sorted(self.pkgs, key=len, reverse=True)

    def ts_options(self, cfg_rel, depth=0):
        if cfg_rel in self.ts_cache:
            return self.ts_cache[cfg_rel]
        self.ts_cache[cfg_rel] = res = {"base": None, "paths": None, "paths_dir": None}
        data = load_jsonc(os.path.join(self.root, cfg_rel)) if depth < 6 else None
        if not isinstance(data, dict):
            return res
        d = posixpath.dirname(cfg_rel)
        ext = data.get("extends")
        for parent in ([ext] if isinstance(ext, str) else ext if isinstance(ext, list) else []):
            if isinstance(parent, str) and parent.startswith("."):
                prel = pjoin(d, parent if parent.endswith(".json") else parent + ".json")
                if not prel.startswith("../") and os.path.isfile(os.path.join(self.root, prel)):
                    res.update({k: v for k, v in self.ts_options(prel, depth + 1).items() if v is not None})
        co = data.get("compilerOptions") or {}
        if isinstance(co.get("baseUrl"), str):
            res["base"] = pjoin(d, co["baseUrl"])
        if isinstance(co.get("paths"), dict):
            res["paths"] = [(k, [t for t in v if isinstance(t, str)]) for k, v in co["paths"].items() if isinstance(v, list)]
            res["paths_dir"] = d
        return res

    def ts_for(self, src):
        d = posixpath.dirname(src)
        while True:
            if d in self.ts_dirs:
                return self.ts_options(self.ts_dirs[d])
            if not d:
                return None
            d = posixpath.dirname(d)

    def js_file(self, base):
        if base.startswith("../") or base == "..":
            return None, False
        stem, ext = posixpath.splitext(base)
        cands = [base] + [stem + e for e in TS_FOR_JS.get(ext, ())]
        cands += [base + e for e in RESOLVE_EXTS] + [pjoin(base, "index" + e) for e in RESOLVE_EXTS]
        for c in cands:
            if c in self.files:
                return c, True
        return None, os.path.exists(os.path.join(self.root, base))

    def js_package(self, spec):
        for name in self.pkg_names:
            if spec == name or spec.startswith(name + "/"):
                d, data = self.pkgs[name]
                rest = spec[len(name) + 1:]
                if rest:
                    for b in (pjoin(d, rest), pjoin(d, "src", rest)):
                        hit, _ = self.js_file(b)
                        if hit:
                            return hit
                    return None
                for field in ("source", "module", "main"):
                    if isinstance(data.get(field), str):
                        hit, _ = self.js_file(pjoin(d, data[field]))
                        if hit:
                            return hit
                for b in (pjoin(d, "src", "index"), pjoin(d, "index")):
                    hit, _ = self.js_file(b)
                    if hit:
                        return hit
        return None

    def js_resolve(self, src, text):
        opts = None
        for spec, kind, lazy in js_imports(text):
            if spec.startswith("."):
                hit, exists = self.js_file(pjoin(posixpath.dirname(src), spec))
                if hit:
                    self.add_edge(src, hit, kind, lazy)
                elif not exists:
                    self.miss(src, spec)
                continue
            if "://" in spec:
                self.add_external(src, spec, "js")
                continue
            if opts is None:
                opts = self.ts_for(src) or {}
            hit = None
            if opts.get("paths"):
                best = None
                for pat, targets in opts["paths"]:
                    if "*" in pat:
                        pre, suf = pat.split("*", 1)
                        if spec.startswith(pre) and spec.endswith(suf) and len(spec) >= len(pre) + len(suf):
                            star = spec[len(pre):len(spec) - len(suf)]
                            if best is None or len(pre) > best[0]:
                                best = (len(pre), [t.replace("*", star) for t in targets])
                    elif pat == spec:
                        best = (10 ** 6, targets)
                for t in (best[1] if best else []):
                    hit, _ = self.js_file(pjoin(opts["base"] if opts["base"] is not None else opts["paths_dir"], t))
                    if hit:
                        break
                if best and not hit:
                    self.miss(src, spec)
                    continue
            if not hit:
                hit = self.js_package(spec)
            if not hit and opts.get("base") is not None and not spec.startswith("@"):
                hit, _ = self.js_file(pjoin(opts["base"], spec))
            if hit:
                self.add_edge(src, hit, kind, lazy)
                continue
            if spec.startswith(("node:", "bun:")) or spec.split("/")[0] in NODE_BUILTINS:
                continue
            if spec.startswith(("@/", "~", "#", "$")):   # an alias we couldn't resolve, never an npm name
                self.miss(src, spec)
                continue
            parts = spec.split("/")
            self.add_external(src, "/".join(parts[:2]) if spec.startswith("@") else parts[0], "js")

    # go
    def go_setup(self):
        self.go_mods = []
        for p in self.cfg["gomod"]:
            m = re.search(r"^\s*module\s+(\S+)", self.read(p), re.M)
            if m:
                self.go_mods.append((m.group(1).strip('"'), posixpath.dirname(p)))
        self.go_mods.sort(key=lambda x: -len(x[0]))
        self.go_pkg = defaultdict(list)
        for f, i in self.files.items():
            if i["lang"] == "go" and not f.endswith("_test.go"):
                self.go_pkg[posixpath.dirname(f)].append(f)

    def go_resolve(self, src, text):
        imports, is_main = go_imports(text)
        if is_main and not src.endswith("_test.go"):
            self.entries[src].append("go package main")
        for imp in imports:
            for mp, d in self.go_mods:
                if imp == mp or imp.startswith(mp + "/"):
                    targets = self.go_pkg.get(pjoin(d, imp[len(mp):].lstrip("/")), [])
                    for t in targets:
                        self.add_edge(src, t, "import", False)
                    if not targets:
                        self.miss(src, imp)
                    break
            else:
                if "." in imp.split("/")[0]:
                    self.add_external(src, "/".join(imp.split("/")[:3]), "go")

    # entry points declared in manifests
    def manifest_entries(self):
        for p in self.cfg["package"]:
            data = load_jsonc(os.path.join(self.root, p))
            if not isinstance(data, dict):
                continue
            d = posixpath.dirname(p)
            bins = data.get("bin")
            bins = [bins] if isinstance(bins, str) else list(bins.values()) if isinstance(bins, dict) else []
            for field, vals in (("bin", bins), ("main", [data.get("main")]), ("module", [data.get("module")])):
                for v in vals:
                    if isinstance(v, str):
                        hit, _ = self.js_file(pjoin(d, v))
                        if hit:
                            self.entries[hit].append("package.json " + field)
            scripts = data.get("scripts") if isinstance(data.get("scripts"), dict) else {}
            for sname, cmd in sorted(scripts.items()):
                if not isinstance(cmd, str):
                    continue
                for tok in re.findall(r"[\w./@-]+\.(?:[cm]?[jt]sx?|py)\b", cmd):
                    hit, _ = self.js_file(pjoin(d, tok)) if not tok.endswith(".py") else (pjoin(d, tok), True)
                    if hit in self.files:
                        self.entries[hit].append("package.json script " + sname)
        for p in self.cfg["pyproject"] + self.cfg["setup"]:
            text = self.read(p)
            mods = []
            if p.endswith(".toml"):
                for sec in re.finditer(r"^\[(project\.scripts|project\.gui-scripts|tool\.poetry\.scripts)\]\s*$"
                                       r"(.*?)(?=^\[|\Z)", text, re.M | re.S):
                    mods += re.findall(r"""^\s*["']?[\w.-]+["']?\s*=\s*["']([\w.]+)(?::[\w.]+)?["']""", sec.group(2), re.M)
            else:
                mods += re.findall(r"""[\w.-]+\s*=\s*([\w.]+):[\w.]+""", text)
            for mod in mods:
                hit = self.py_find(mod, p)
                if hit:
                    self.entries[hit].append(posixpath.basename(p) + " script")
        for f, info in self.files.items():
            if info["test"]:
                continue
            stem = posixpath.splitext(posixpath.basename(f))[0]
            d = posixpath.dirname(f)
            if stem in ENTRY_STEMS or (stem == "index" and (d in self.pkg_dirs or d == "" or (
                    posixpath.basename(d) == "src" and (posixpath.dirname(d) in self.pkg_dirs or posixpath.dirname(d) == "")))):
                if info["lang"] in PARSED:
                    self.entries[f].append("file name")

    # run
    def analyse(self):
        self.walk()
        self.py_setup()
        self.js_setup()
        self.go_setup()
        for f, info in self.files.items():
            text = self.read(f)
            info["loc"] = sum(1 for line in text.splitlines() if line.strip())
            if info["lang"] == "python":
                self.py_resolve(f, text)
            elif info["lang"] in JS_LANGS:
                self.js_resolve(f, text)
            elif info["lang"] == "go":
                self.go_resolve(f, text)
        self.manifest_entries()
        for f in [f for f in self.entries if self.files[f]["test"]]:
            del self.entries[f]   # unittest.main() guards are not how the app starts
        return self


def module_of(path, depth):
    parts = path.split("/")[:-1]
    return "/".join(parts[:depth]) or "."


def auto_depth(paths):
    """Go one level deeper while there are few modules and going deeper still splits or renames them."""
    def mods(d):
        return {module_of(p, d) for p in paths}
    d = 1
    while d < 6 and len(mods(d)) < 8 and mods(d) != mods(d + 1) and len(mods(d + 1)) <= 60:
        d += 1
    return d


def build(cm, depth=None, hub_min=3):
    """Turn a CodeMap into the stable JSON model (schema_version 1)."""
    files = cm.files
    paths = sorted(files)
    depth = depth or auto_depth(paths)
    mod = {p: module_of(p, depth) for p in paths}
    out_adj, in_adj = defaultdict(set), defaultdict(set)
    for s, t in cm.edges:
        out_adj[s].add(t)
        in_adj[t].add(s)

    # cycles
    adj = {k: sorted(v) for k, v in out_adj.items()}
    eager_adj = defaultdict(list)
    for (s, t), e in sorted(cm.edges.items()):
        if not e["lazy"]:
            eager_adj[s].append(t)
    eager_sccs = [set(c) for c in tarjan(paths, eager_adj) if len(c) > 1]
    file_cycles, scc_of = [], {}
    for c in tarjan(paths, adj):
        if len(c) < 2:
            continue
        members = set(c)
        for v in c:
            scc_of[v] = len(file_cycles)
        eager = next((e for e in eager_sccs if e <= members), None)
        start = min(eager) if eager else c[0]
        path = cycle_path(start, eager, eager_adj) if eager else cycle_path(start, members, adj)
        file_cycles.append({"members": c, "size": len(c), "eager": bool(eager), "example": path})
    file_cycles.sort(key=lambda x: (not x["eager"], -x["size"], x["members"][0]))

    # files
    file_rows, hubs = [], []
    for p in paths:
        importers = in_adj.get(p, set())
        tests = sum(1 for s in importers if files[s]["test"])
        file_rows.append({
            "path": p, "module": mod[p], "lang": files[p]["lang"], "loc": files[p]["loc"],
            "fan_in": len(importers), "test_fan_in": tests, "fan_out": len(out_adj.get(p, ())),
            "entry": p in cm.entries, "test": files[p]["test"], "in_cycle": p in scc_of,
            "external": sorted(cm.external.get(p, ())),
        })
        if not files[p]["test"] and len(importers) - tests >= hub_min:
            hubs.append({"path": p, "fan_in": len(importers) - tests, "test_fan_in": tests, "loc": files[p]["loc"],
                         "in_cycle": p in scc_of})
    hubs.sort(key=lambda h: (-h["fan_in"], -h["loc"], h["path"]))
    edges = [{"source": s, "target": t, "kind": e["kind"], "lazy": e["lazy"],
              "cycle": s in scc_of and scc_of.get(s) == scc_of.get(t)}
             for (s, t), e in sorted(cm.edges.items())]

    # modules
    mods = sorted(set(mod.values()))
    m_files, m_loc, m_lang, m_ext, m_entry = defaultdict(int), defaultdict(int), defaultdict(Counter), \
        defaultdict(Counter), defaultdict(list)
    for p in paths:
        m = mod[p]
        m_files[m] += 1
        m_loc[m] += files[p]["loc"]
        m_lang[m][files[p]["lang"]] += 1
        for x in cm.external.get(p, ()):
            m_ext[m][x] += 1
        if p in cm.entries:
            m_entry[m].append(p)
    m_src = defaultdict(set)
    m_lazy = defaultdict(lambda: True)
    for (s, t), e in cm.edges.items():
        a, b = mod[s], mod[t]
        if a != b:
            m_src[(a, b)].add(s)
            m_lazy[(a, b)] = m_lazy[(a, b)] and e["lazy"]
    m_adj = defaultdict(list)
    for a, b in sorted(m_src):
        m_adj[a].append(b)
    mscc_of, mod_cycles = {}, []
    for c in tarjan(mods, m_adj):
        if len(c) > 1:
            for v in c:
                mscc_of[v] = len(mod_cycles)
            mod_cycles.append({"members": c, "size": len(c), "example": cycle_path(c[0], set(c), m_adj)})
    module_edges = [{"source": a, "target": b, "weight": len(srcs), "lazy": m_lazy[(a, b)],
                     "cycle": a in mscc_of and mscc_of.get(a) == mscc_of.get(b)}
                    for (a, b), srcs in sorted(m_src.items())]
    m_in, m_out = Counter(), Counter()
    for a, b in m_src:
        m_out[a] += 1
        m_in[b] += 1
    module_rows = [{"id": m, "files": m_files[m], "loc": m_loc[m], "languages": dict(sorted(m_lang[m].items())),
                    "fan_in": m_in[m], "fan_out": m_out[m], "external": dict(m_ext[m].most_common()),
                    "entry_points": m_entry[m], "in_cycle": m in mscc_of} for m in mods]

    # declared or detected entry points first, guesses from the file name last
    entries = sorted(({"path": p, "reasons": sorted(set(r))} for p, r in cm.entries.items()),
                     key=lambda e: (e["reasons"] == ["file name"], e["path"]))
    orphans = [p for p in paths if files[p]["lang"] in PARSED and not files[p]["test"] and p not in cm.entries
               and not in_adj.get(p) and not NOT_ORPHAN.match(posixpath.basename(p))]
    ext_count = Counter()
    for p, names in cm.external.items():
        for n in names:
            ext_count[n] += 1
    external = [{"name": n, "lang": cm.ext_lang.get(n, ""), "files": c}
                for n, c in sorted(ext_count.items(), key=lambda x: (-x[1], x[0]))]
    langs = defaultdict(lambda: {"files": 0, "loc": 0})
    for p in paths:
        langs[files[p]["lang"]]["files"] += 1
        langs[files[p]["lang"]]["loc"] += files[p]["loc"]
    return {
        "schema_version": SCHEMA_VERSION,
        "root": os.path.basename(cm.root) or cm.root,
        "depth": depth,
        "stats": {
            "files": len(paths), "loc": sum(f["loc"] for f in files.values()),
            "languages": dict(sorted(langs.items(), key=lambda x: -x[1]["files"])),
            "modules": len(mods), "edges": len(edges), "module_edges": len(module_edges),
            "external_packages": len(external), "unresolved": len(cm.unresolved),
            "file_cycles": len(file_cycles), "module_cycles": len(mod_cycles),
            "skipped_large": cm.skipped_large, "truncated": cm.truncated, "parse_fallbacks": cm.parse_errors,
        },
        "modules": module_rows,
        "module_edges": module_edges,
        "files": file_rows,
        "edges": edges,
        "entry_points": entries,
        "hubs": hubs,
        "cycles": {"files": file_cycles, "modules": mod_cycles},
        "orphans": orphans,
        "external": external,
        "unresolved": cm.unresolved[:200],
        "not_visible": STATIC_LIMITS,
    }


# --- renderers ---------------------------------------------------------------------------
def render_text(m, top=10):
    s = m["stats"]
    langs = ", ".join("%s %d" % (k, v["files"]) for k, v in s["languages"].items())
    out = ["Code map of %s: %d files, %s LOC (%s)" % (m["root"], s["files"], format(s["loc"], ","), langs),
           "%d modules at depth %d, %d internal file edges, %d module edges, %d external packages"
           % (s["modules"], m["depth"], s["edges"], s["module_edges"], s["external_packages"])]
    if s["truncated"]:
        out.append("Stopped at --max-files: the map is partial.")
    out += ["", "Modules (by LOC)", "  %-40s %6s %8s %7s %8s %5s" % ("module", "files", "LOC", "fan-in", "fan-out", "ext")]
    for r in sorted(m["modules"], key=lambda r: (-r["loc"], r["id"]))[:max(top, 15)]:
        out.append("  %-40s %6d %8d %7d %8d %5d%s" % (r["id"][:40], r["files"], r["loc"], r["fan_in"], r["fan_out"],
                                                      len(r["external"]), "  (cycle)" if r["in_cycle"] else ""))
    if len(m["modules"]) > max(top, 15):
        out.append("  ... %d more" % (len(m["modules"]) - max(top, 15)))
    out += ["", "Entry points (%d)" % len(m["entry_points"])]
    for e in m["entry_points"][:top]:
        out.append("  %s  [%s]" % (e["path"], ", ".join(e["reasons"])))
    if len(m["entry_points"]) > top:
        out.append("  ... %d more" % (len(m["entry_points"]) - top))
    out += ["", "Hubs: most imported files, change with care (%d)" % len(m["hubs"])]
    for h in m["hubs"][:top]:
        out.append("  %s  imported by %d files%s, %d LOC%s" % (
            h["path"], h["fan_in"], " (+%d tests)" % h["test_fan_in"] if h["test_fan_in"] else "", h["loc"],
            ", in a cycle" if h["in_cycle"] else ""))
    fc = m["cycles"]["files"]
    out += ["", "Import cycles: %d between files, %d between modules" % (len(fc), len(m["cycles"]["modules"]))]
    for c in fc[:top]:
        out.append("  %s %s" % ("[import-time]" if c["eager"] else "[lazy only]  ", " -> ".join(c["example"])))
        if c["size"] > len(c["example"]) - 1:
            out.append("      (%d files in this tangle)" % c["size"])
    for c in m["cycles"]["modules"][:top]:
        out.append("  [modules]     %s" % " -> ".join(c["example"]))
    out += ["", "Orphans: not imported and not an entry point (%d)" % len(m["orphans"])]
    out += ["  " + p for p in m["orphans"][:top]]
    if len(m["orphans"]) > top:
        out.append("  ... %d more" % (len(m["orphans"]) - top))
    out += ["", "External packages (top %d of %d)" % (min(top, len(m["external"])), len(m["external"]))]
    out += ["  %-30s %4d file(s)  (%s)" % (x["name"][:30], x["files"], x["lang"]) for x in m["external"][:top]]
    if s["unresolved"]:
        out += ["", "Unresolved local imports: %d (first: %s in %s)" % (
            s["unresolved"], m["unresolved"][0]["import"], m["unresolved"][0]["file"])]
    if s["parse_fallbacks"]:
        out.append("Python files read with the regex fallback (syntax newer than this interpreter): %d" % s["parse_fallbacks"])
    out += ["", "Not visible to this static parse: " + STATIC_LIMITS + "."]
    return "\n".join(out) + "\n"


def mermaid_id(name, used, prefix="n_"):
    base = prefix + re.sub(r"[^A-Za-z0-9_]", "_", name)
    nid, i = base, 2
    while nid in used and used[nid] != name:
        nid = "%s_%d" % (base, i)
        i += 1
    used[nid] = name
    return nid


def mermaid_text(s):
    return s.replace("&", "#amp;").replace('"', "#quot;").replace("<", "#lt;").replace(">", "#gt;")


def render_mermaid(m, level="module", max_nodes=25, max_edges=40, focus=None):
    if focus:
        focus = focus.strip("/").replace(os.sep, "/")
        inside = lambda p: focus in (".", "") or p == focus or p.startswith(focus + "/")  # noqa: E731
        level = "file"
    else:
        inside = lambda p: True  # noqa: E731
    if level == "file":
        nodes = {f["path"]: f for f in m["files"] if inside(f["path"])}
        edges = [dict(e, weight=1) for e in m["edges"] if inside(e["source"]) and inside(e["target"])]
        group = {p: f["module"] for p, f in nodes.items()}
        label = {p: "%s<br/>%d LOC" % (mermaid_text(posixpath.basename(p)), f["loc"]) for p, f in nodes.items()}
        entry = {p for p, f in nodes.items() if f["entry"]}
    else:
        nodes = {r["id"]: r for r in m["modules"]}
        edges = m["module_edges"]
        group = {i: (posixpath.dirname(i) if "/" in i else "") for i in nodes}
        label = {i: "%s<br/>%d file%s, %d LOC" % (mermaid_text("(root)" if i == "." else posixpath.basename(i)),
                                                    r["files"], "" if r["files"] == 1 else "s", r["loc"])
                 for i, r in nodes.items()}
        entry = {i for i, r in nodes.items() if r["entry_points"]}
    picked_nodes, picked = [], []
    for e in sorted(edges, key=lambda e: (-e["weight"], not e["cycle"], e["source"], e["target"])):
        if len(picked) >= max_edges:
            break
        new = [x for x in (e["source"], e["target"]) if x not in picked_nodes]
        if len(picked_nodes) + len(new) > max_nodes:
            continue
        picked_nodes += new
        picked.append(e)
    if not picked:
        weight = {k: v.get("loc", 0) for k, v in nodes.items()}
        picked_nodes = sorted(nodes, key=lambda k: (-weight[k], k))[:max_nodes]
    used = {}
    ids = {n: mermaid_id("root" if n == "." else n, used) for n in sorted(picked_nodes)}
    lines = ["flowchart LR"]
    if len(picked_nodes) < len(nodes) or len(picked) < len(edges):
        lines.append("  %%%% showing %d of %d %ss and %d of %d edges (heaviest first)"
                     % (len(picked_nodes), len(nodes), level, len(picked), len(edges)))
    groups = defaultdict(list)
    for n in sorted(picked_nodes):
        groups[group[n]].append(n)
    gused = {}
    for g in sorted(groups):
        indent = "  "
        if g and len(groups) > 1:
            lines.append('  subgraph %s["%s"]' % (mermaid_id("root" if g == "." else g, gused, "sg_"),
                                                        mermaid_text("(root)" if g == "." else g)))
            indent = "    "
        for n in groups[g]:
            lines.append('%s%s["%s"]' % (indent, ids[n], label[n]))
        if indent == "    ":
            lines.append("  end")
    cyc = []
    for i, e in enumerate(picked):
        arrow = "-.->" if e.get("lazy") else "-->"
        w = e.get("weight", 1)
        lines.append("  %s %s%s %s" % (ids[e["source"]], arrow, "|%d|" % w if w > 1 else "", ids[e["target"]]))
        if e.get("cycle"):
            cyc.append(str(i))
    marked = [ids[n] for n in sorted(picked_nodes) if n in entry]
    if marked:
        lines.append("  classDef entry stroke-width:3px")
        lines.append("  class %s entry" % ",".join(marked))
    if cyc:
        lines.append("  linkStyle %s stroke:#d6336c,stroke-width:2px" % ",".join(cyc))
    return "\n".join(lines) + "\n"


def html_view(nodes, edges):
    idx = {n["id"]: i for i, n in enumerate(nodes)}
    pairs = [(idx[e["source"]], idx[e["target"]]) for e in edges]
    r = ranks(len(nodes), pairs)
    for n, rk in zip(nodes, r):
        n["rank"] = rk
    return {"nodes": nodes, "edges": [[idx[e["source"]], idx[e["target"]], e.get("weight", 1),
                                       1 if e["cycle"] else 0, 1 if e["lazy"] else 0] for e in edges]}


def render_html(m, max_files=800):
    hub_set = {h["path"] for h in m["hubs"]}
    mod_hubs = Counter(f["module"] for f in m["files"] if f["path"] in hub_set)
    members = defaultdict(list)
    for f in m["files"]:
        members[f["module"]].append(f["path"])
    mods = [{"id": r["id"], "label": "(root)" if r["id"] == "." else r["id"], "group": r["id"].split("/")[0],
             "loc": r["loc"], "files": r["files"], "entry": bool(r["entry_points"]), "hub": mod_hubs[r["id"]] > 0,
             "cycle": r["in_cycle"], "fin": r["fan_in"], "fout": r["fan_out"],
             "ext": list(r["external"])[:12], "items": members[r["id"]][:200]} for r in m["modules"]]
    views = {"modules": html_view(mods, m["module_edges"]), "files": None}
    if len(m["files"]) <= max_files:
        fnodes = [{"id": f["path"], "label": posixpath.basename(f["path"]), "group": f["module"], "loc": f["loc"],
                   "entry": f["entry"], "hub": f["path"] in hub_set, "cycle": f["in_cycle"], "test": f["test"],
                   "fin": f["fan_in"], "fout": f["fan_out"], "ext": f["external"][:12]} for f in m["files"]]
        views["files"] = html_view(fnodes, m["edges"])
    data = {"root": m["root"], "stats": m["stats"], "depth": m["depth"], "views": views,
            "max_files": max_files, "not_visible": STATIC_LIMITS}
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    # JSON escapes keep the payload inert inside <script>: no "</script>", "<!--" or "]]>" can appear.
    blob = (blob.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))
    return HTML.replace("__TITLE__", html.escape("Code map: " + m["root"])).replace("__DATA__", blob)


HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#fafaf9;--fg:#1c1c1e;--muted:#6b6b72;--panel:#ffffff;--line:#e2e2e6;--edge:#a3a7ad;--cycle:#d6336c;
--out:#1f6feb;--in:#e8590c;--node-stroke:#ffffff;--match:#f0b400}
@media (prefers-color-scheme: dark){:root{--bg:#131315;--fg:#ececf1;--muted:#9b9ba5;--panel:#1c1c20;--line:#2d2d33;
--edge:#5d6168;--cycle:#ff6b9a;--out:#58a6ff;--in:#ff922b;--node-stroke:#131315;--match:#ffd43b}}
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);font:14px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif}
header{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;padding:10px 16px;border-bottom:1px solid var(--line)}
header h1{font-size:15px;margin:0;font-weight:600}
#stats{color:var(--muted);font-size:12px}
.controls{display:flex;gap:6px;margin-left:auto;flex-wrap:wrap}
input[type=search]{background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:5px 8px;width:220px;max-width:60vw}
button{background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:5px 10px;cursor:pointer;font:inherit;font-size:13px}
button[aria-pressed=true]{border-color:var(--out);color:var(--out)}
button:disabled{opacity:.45;cursor:not-allowed}
main{display:flex;height:calc(100% - 52px)}
#wrap{flex:1;position:relative;min-width:0}
#graph{width:100%;height:100%;display:block;cursor:grab;touch-action:none}
#graph.panning{cursor:grabbing}
aside{width:320px;border-left:1px solid var(--line);background:var(--panel);overflow:auto;padding:12px 14px}
aside h2{font-size:14px;margin:0 0 6px;word-break:break-all}
aside h3{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin:14px 0 4px}
aside ul{list-style:none;margin:0;padding:0}
aside li button{border:0;background:none;padding:2px 0;text-align:left;color:var(--fg);word-break:break-all}
aside li button:hover{text-decoration:underline}
.meta{color:var(--muted);font-size:12px}
.legend{font-size:12px;color:var(--muted)}
.legend div{display:flex;align-items:center;gap:8px;margin:3px 0}
.legend svg{flex:none}
.sw{display:inline-block;width:22px;height:0;border-top:2px solid var(--edge)}
.edge{fill:none;stroke:var(--edge);stroke-width:1.1;opacity:.75}
.edge.lazy{stroke-dasharray:4 3}
.edge.cycle{stroke:var(--cycle);opacity:.95;stroke-width:1.6}
.edge.out{stroke:var(--out);opacity:1;stroke-width:2}
.edge.in{stroke:var(--in);opacity:1;stroke-width:2}
.edge.dim{opacity:.07}
.node circle.dot{stroke:var(--node-stroke);stroke-width:1.5}
.node.hub circle.dot{stroke:var(--fg);stroke-width:2.5}
.node circle.ring{fill:none;stroke:var(--fg);stroke-width:1.2}
.node text{fill:var(--fg);font-size:11px;paint-order:stroke;stroke:var(--bg);stroke-width:3px;stroke-linejoin:round}
.node{cursor:pointer}
.node.dim{opacity:.15}
.node.sel circle.dot{stroke:var(--out);stroke-width:3}
.node.match circle.dot{stroke:var(--match);stroke-width:3}
.m-def{fill:var(--edge)}.m-cyc{fill:var(--cycle)}.m-out{fill:var(--out)}.m-in{fill:var(--in)}
#note{position:absolute;left:12px;bottom:10px;font-size:12px;color:var(--muted);max-width:70%}
@media (max-width:760px){main{flex-direction:column;height:auto}#wrap{flex:none;height:65vh}aside{width:auto;border-left:0;border-top:1px solid var(--line)}}
</style>
</head>
<body>
<header>
  <h1 id="title"></h1>
  <span id="stats"></span>
  <div class="controls">
    <input type="search" id="q" placeholder="Search path (Enter to jump)" aria-label="Search">
    <button id="v-modules" aria-pressed="true">Modules</button>
    <button id="v-files" aria-pressed="false">Files</button>
    <button id="fit">Fit</button>
  </div>
</header>
<main>
  <div id="wrap">
    <svg id="graph" role="img" aria-label="Import graph">
      <defs>
        <marker id="a-def" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path class="m-def" d="M0,0L10,5L0,10z"/></marker>
        <marker id="a-cyc" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path class="m-cyc" d="M0,0L10,5L0,10z"/></marker>
        <marker id="a-out" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path class="m-out" d="M0,0L10,5L0,10z"/></marker>
        <marker id="a-in" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path class="m-in" d="M0,0L10,5L0,10z"/></marker>
      </defs>
      <g id="vp"></g>
    </svg>
    <div id="note"></div>
  </div>
  <aside>
    <div id="panel"><p class="meta">Click a node to see what it imports and what imports it. Drag to pan, scroll to zoom.</p></div>
    <h3>Legend</h3>
    <div class="legend">
      <div><svg width="22" height="14"><circle cx="7" cy="7" r="5" fill="#4e79a7"/></svg>colour: folder · size: lines of code</div>
      <div><svg width="22" height="14"><circle cx="7" cy="7" r="4" fill="#4e79a7"/><circle cx="7" cy="7" r="6.3" fill="none" stroke="currentColor"/></svg>entry point</div>
      <div><svg width="22" height="14"><circle cx="7" cy="7" r="5" fill="#4e79a7" stroke="currentColor" stroke-width="2"/></svg>hub (many importers)</div>
      <div><span class="sw" style="border-color:var(--cycle)"></span>edge inside an import cycle</div>
      <div><span class="sw" style="border-top-style:dashed"></span>lazy, dynamic or type-only import</div>
      <div><span class="sw" style="border-color:var(--out)"></span>imported by the selection</div>
      <div><span class="sw" style="border-color:var(--in)"></span>imports the selection</div>
      <div>left to right: importers before the code they depend on</div>
    </div>
    <h3>Not visible to a static parse</h3>
    <p class="meta" id="limits"></p>
  </aside>
</main>
<script type="application/json" id="cm-data">__DATA__</script>
<script>
(function () {
  "use strict";
  var D = JSON.parse(document.getElementById("cm-data").textContent);
  var svg = document.getElementById("graph"), vp = document.getElementById("vp");
  var NS = svg.namespaceURI;
  var PALETTE = ["#4e79a7", "#f28e2b", "#59a14f", "#b07aa1", "#76b7b2", "#edc948", "#ff9da7", "#9c755f",
                 "#e15759", "#86bcb6", "#8cd17d", "#d4a6c8", "#499894", "#f1ce63", "#a0cbe8", "#bab0ac"];
  var view, V, pos, nodeEls, edgeEls, sel = -1, t = {k: 1, x: 20, y: 20};

  function $(id) { return document.getElementById(id); }
  function mk(tag, attrs, parent) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function h(tag, text, parent) {
    var e = document.createElement(tag);
    if (text != null) e.textContent = text;
    if (parent) parent.appendChild(e);
    return e;
  }
  function fmt(n) { return Number(n).toLocaleString(); }

  var s = D.stats;
  $("title").textContent = "Code map: " + D.root;
  $("stats").textContent = fmt(s.files) + " files · " + fmt(s.modules) + " modules (depth " + D.depth + ") · " +
    fmt(s.loc) + " LOC · " + fmt(s.edges) + " imports · " + s.file_cycles + " file cycles";
  $("limits").textContent = D.not_visible + ".";
  if (!D.views.files) {
    $("v-files").disabled = true;
    $("v-files").title = "More than " + D.max_files + " files: file view left out to keep the page fast";
  }

  function layout(v) {
    var n = v.nodes.length, maxRank = 0, i;
    v.nodes.forEach(function (nd) { if (nd.rank > maxRank) maxRank = nd.rank; });
    var byRank = {}, nb = [], col = [], y = [], bary = [];
    for (i = 0; i < n; i++) nb.push([]);
    v.edges.forEach(function (e) { nb[e[0]].push(e[1]); nb[e[1]].push(e[0]); });
    v.nodes.forEach(function (nd, i) {
      var r = nd.rank < 0 ? maxRank + 1 : nd.rank;
      (byRank[r] = byRank[r] || []).push(i);
    });
    var cap = Math.max(14, Math.ceil(Math.sqrt(n) * 1.6)), columns = [];
    Object.keys(byRank).map(Number).sort(function (a, b) { return a - b; }).forEach(function (r) {
      var list = byRank[r].sort(function (a, b) {
        var A = v.nodes[a], B = v.nodes[b];
        return A.group < B.group ? -1 : A.group > B.group ? 1 : A.id < B.id ? -1 : 1;
      });
      for (var k = 0; k < list.length; k += cap) columns.push(list.slice(k, k + cap));
    });
    columns.forEach(function (c, ci) { c.forEach(function (i, k) { col[i] = ci; y[i] = k - (c.length - 1) / 2; }); });
    for (var sweep = 0; sweep < 6; sweep++) {
      columns.forEach(function (c) {
        c.forEach(function (i) {
          var sum = 0, m = 0;
          nb[i].forEach(function (j) { if (col[j] !== col[i]) { sum += y[j]; m++; } });
          bary[i] = m ? sum / m : y[i];
        });
        c.sort(function (a, b) { return bary[a] - bary[b] || a - b; });
        c.forEach(function (i, k) { y[i] = k - (c.length - 1) / 2; });
      });
    }
    var x = 0, p = [];
    columns.forEach(function (c) {
      var w = 0;
      c.forEach(function (i) { w = Math.max(w, v.nodes[i].label.length); p[i] = {x: x, y: y[i] * 34}; });
      x += 70 + Math.min(w, 46) * 6.6;
    });
    return p;
  }
  function radius(nd) { return Math.max(5, Math.min(22, 4 + Math.sqrt(nd.loc || 0) / 3)); }

  function edgePath(a, b, ra, rb) {
    if (b.x > a.x + 1) {
      var x1 = a.x + ra, x2 = b.x - rb, dx = (x2 - x1) / 2;
      return "M" + x1 + "," + a.y + "C" + (x1 + dx) + "," + a.y + " " + (x2 - dx) + "," + b.y + " " + x2 + "," + b.y;
    }
    var drop = 40 + Math.abs(a.x - b.x) / 4;
    return "M" + a.x + "," + (a.y + ra) + "C" + a.x + "," + (a.y + ra + drop) + " " + b.x + "," + (b.y + rb + drop) +
      " " + b.x + "," + (b.y + rb);
  }

  function render(name) {
    view = name; V = D.views[name]; sel = -1;
    $("v-modules").setAttribute("aria-pressed", name === "modules");
    $("v-files").setAttribute("aria-pressed", name === "files");
    while (vp.firstChild) vp.removeChild(vp.firstChild);
    pos = layout(V);
    var groups = {}, gi = 0;
    V.nodes.map(function (n) { return n.group; }).sort().forEach(function (g) {
      if (!(g in groups)) groups[g] = PALETTE[gi++ % PALETTE.length];
    });
    var ge = mk("g", {}, vp), gn = mk("g", {}, vp);
    edgeEls = V.edges.map(function (e) {
      var a = V.nodes[e[0]], b = V.nodes[e[1]];
      var el = mk("path", {d: edgePath(pos[e[0]], pos[e[1]], radius(a), radius(b)),
                           "class": "edge" + (e[3] ? " cycle" : "") + (e[4] ? " lazy" : ""),
                           "marker-end": e[3] ? "url(#a-cyc)" : "url(#a-def)"}, ge);
      var tt = mk("title", {}, el);
      tt.textContent = a.id + " \u2192 " + b.id + (e[2] > 1 ? " (" + e[2] + " files)" : "");
      return el;
    });
    nodeEls = V.nodes.map(function (nd, i) {
      var r = radius(nd);
      var g = mk("g", {"class": "node" + (nd.hub ? " hub" : ""), transform: "translate(" + pos[i].x + "," + pos[i].y + ")"}, gn);
      if (nd.entry) mk("circle", {"class": "ring", r: r + 3.5}, g);
      mk("circle", {"class": "dot", r: r, fill: groups[nd.group]}, g);
      var tx = mk("text", {x: r + 5, dy: "0.35em"}, g);
      tx.textContent = nd.label;
      var tt = mk("title", {}, g);
      tt.textContent = nd.id + "\n" + fmt(nd.loc) + " LOC" + (nd.files ? " in " + nd.files + (nd.files === 1 ? " file" : " files") : "") +
        "\nimports " + nd.fout + " · imported by " + nd.fin;
      g.addEventListener("click", function (ev) { ev.stopPropagation(); select(i); });
      return g;
    });
    $("note").textContent = V.nodes.length + " nodes, " + V.edges.length + " edges";
    showDefault();
    fit();
  }

  function showDefault() {
    var p = $("panel");
    while (p.firstChild) p.removeChild(p.firstChild);
    h("p", "Click a node to see what it imports and what imports it. Drag to pan, scroll to zoom.", p).className = "meta";
  }

  function clear() {
    sel = -1;
    nodeEls.forEach(function (g) { g.classList.remove("dim", "sel", "match"); });
    edgeEls.forEach(function (el, k) {
      el.classList.remove("dim", "out", "in");
      el.setAttribute("marker-end", V.edges[k][3] ? "url(#a-cyc)" : "url(#a-def)");
    });
    showDefault();
  }

  function list(title, idxs, p) {
    h("h3", title + " (" + idxs.length + ")", p);
    var ul = h("ul", null, p);
    idxs.slice(0, 300).forEach(function (j) {
      var b = h("button", V.nodes[j].id, h("li", null, ul));
      b.addEventListener("click", function () { select(j); center(j); });
    });
  }

  function select(i) {
    sel = i;
    var outs = [], ins = [], keep = {};
    keep[i] = 1;
    V.edges.forEach(function (e, k) {
      var el = edgeEls[k];
      el.classList.remove("dim", "out", "in");
      if (e[0] === i) { el.classList.add("out"); el.setAttribute("marker-end", "url(#a-out)"); outs.push(e[1]); keep[e[1]] = 1; }
      else if (e[1] === i) { el.classList.add("in"); el.setAttribute("marker-end", "url(#a-in)"); ins.push(e[0]); keep[e[0]] = 1; }
      else { el.classList.add("dim"); el.setAttribute("marker-end", e[3] ? "url(#a-cyc)" : "url(#a-def)"); }
    });
    nodeEls.forEach(function (g, j) {
      g.classList.remove("match");
      g.classList.toggle("dim", !keep[j]);
      g.classList.toggle("sel", j === i);
    });
    var nd = V.nodes[i], p = $("panel");
    while (p.firstChild) p.removeChild(p.firstChild);
    h("h2", view === "modules" ? nd.label : nd.id, p);
    var tags = [fmt(nd.loc) + " LOC"];
    if (nd.files) tags.push(nd.files + (nd.files === 1 ? " file" : " files"));
    if (nd.entry) tags.push("entry point");
    if (nd.hub) tags.push(view === "modules" ? "contains a hub file" : "hub");
    if (nd.cycle) tags.push("in an import cycle");
    if (nd.test) tags.push("test");
    h("div", tags.join(" · "), p).className = "meta";
    var byName = function (a, b) { return V.nodes[a].id < V.nodes[b].id ? -1 : 1; };
    list("Imports", outs.sort(byName), p);
    list("Imported by", ins.sort(byName), p);
    if (nd.ext && nd.ext.length) { h("h3", "External packages", p); h("div", nd.ext.join(", "), p).className = "meta"; }
    if (nd.items) {
      h("h3", "Files (" + nd.items.length + (nd.files > nd.items.length ? " of " + nd.files : "") + ")", p);
      var ul = h("ul", null, p);
      nd.items.forEach(function (f) { h("li", f, ul).className = "meta"; });
    }
  }

  function apply() { vp.setAttribute("transform", "translate(" + t.x + "," + t.y + ") scale(" + t.k + ")"); }
  function fit() {
    var b = vp.getBBox(), W = svg.clientWidth || 800, H = svg.clientHeight || 600;
    if (!b.width && !b.height) { t = {k: 1, x: 20, y: 20}; apply(); return; }
    t.k = Math.max(0.05, Math.min(1.6, (W - 40) / (b.width || 1), (H - 40) / (b.height || 1)));
    t.x = (W - b.width * t.k) / 2 - b.x * t.k;
    t.y = (H - b.height * t.k) / 2 - b.y * t.k;
    apply();
  }
  function center(i) {
    var W = svg.clientWidth || 800, H = svg.clientHeight || 600;
    t.k = Math.max(t.k, 0.9);
    t.x = W / 2 - pos[i].x * t.k;
    t.y = H / 2 - pos[i].y * t.k;
    apply();
  }

  svg.addEventListener("wheel", function (e) {
    e.preventDefault();
    var r = svg.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
    var k2 = Math.max(0.05, Math.min(8, t.k * Math.exp(-e.deltaY * 0.0015)));
    t.x = px - (px - t.x) * (k2 / t.k);
    t.y = py - (py - t.y) * (k2 / t.k);
    t.k = k2;
    apply();
  }, {passive: false});
  var drag = null, moved = false;
  svg.addEventListener("pointerdown", function (e) { drag = {x: e.clientX, y: e.clientY, tx: t.x, ty: t.y}; moved = false; });
  window.addEventListener("pointermove", function (e) {
    if (!drag) return;
    var dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (!moved && Math.abs(dx) + Math.abs(dy) < 4) return;
    moved = true;
    svg.classList.add("panning");
    t.x = drag.tx + dx; t.y = drag.ty + dy;
    apply();
  });
  window.addEventListener("pointerup", function () { drag = null; svg.classList.remove("panning"); });
  svg.addEventListener("click", function (e) {
    if (moved) { moved = false; e.stopPropagation(); return; }
    if (!e.target.closest || !e.target.closest(".node")) clear();
  }, true);

  var q = $("q");
  function matches(text) {
    var needle = text.trim().toLowerCase(), hits = [];
    V.nodes.forEach(function (nd, j) { if (needle && nd.id.toLowerCase().indexOf(needle) >= 0) hits.push(j); });
    return hits;
  }
  q.addEventListener("input", function () {
    var hits = matches(q.value), set = {};
    if (!q.value.trim()) { clear(); return; }
    hits.forEach(function (j) { set[j] = 1; });
    nodeEls.forEach(function (g, j) { g.classList.remove("sel"); g.classList.toggle("match", !!set[j]); g.classList.toggle("dim", !set[j]); });
    edgeEls.forEach(function (el) { el.classList.add("dim"); });
  });
  q.addEventListener("keydown", function (e) {
    if (e.key === "Enter") { var hits = matches(q.value); if (hits.length) { select(hits[0]); center(hits[0]); } }
    if (e.key === "Escape") { q.value = ""; clear(); }
  });
  $("v-modules").addEventListener("click", function () { render("modules"); });
  $("v-files").addEventListener("click", function () { if (D.views.files) render("files"); });
  $("fit").addEventListener("click", fit);
  window.addEventListener("resize", function () { if (sel < 0) fit(); });
  render("modules");
})();
</script>
</body>
</html>
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description="Map a codebase's import graph (Python, JS/TS, Go).")
    ap.add_argument("path", nargs="?", default=".")
    ap.add_argument("--format", choices=("text", "json", "mermaid", "html"), default="text")
    ap.add_argument("--out", help="write to this file instead of stdout")
    ap.add_argument("--depth", type=int, help="directory levels per module (default: auto)")
    ap.add_argument("--exclude", action="append", default=[], help="glob to skip (repeatable, comma-separated ok)")
    ap.add_argument("--level", choices=("module", "file"), default="module", help="mermaid: draw modules or files")
    ap.add_argument("--focus", help="mermaid: draw only the files under this folder (implies --level file)")
    ap.add_argument("--max-nodes", type=int, default=25, help="mermaid: node limit (default 25)")
    ap.add_argument("--max-edges", type=int, default=40, help="mermaid: edge limit (default 40)")
    ap.add_argument("--top", type=int, default=10, help="text: rows per list (default 10)")
    ap.add_argument("--hub-min", type=int, default=3, help="non-test importers needed to count as a hub (default 3)")
    ap.add_argument("--skip-tests", action="store_true", help="leave test files out of the map")
    ap.add_argument("--max-files", type=int, default=20000, help="stop after this many source files (default 20000)")
    ap.add_argument("--max-bytes", type=int, default=1_000_000, help="skip larger files, e.g. bundles (default 1 MB)")
    ap.add_argument("--html-max-files", type=int, default=800, help="html: include the file view up to this many files")
    a = ap.parse_args(argv)
    if not os.path.isdir(a.path):
        print("code_map: %s is not a directory" % a.path, file=sys.stderr)
        return 2
    excludes = [p.strip() for x in a.exclude for p in x.split(",") if p.strip()]
    cm = CodeMap(a.path, excludes, a.max_files, a.max_bytes, a.skip_tests).analyse()
    if not cm.files:
        print("code_map: no source files found under %s" % a.path, file=sys.stderr)
        return 2
    model = build(cm, depth=a.depth if a.depth and a.depth > 0 else None, hub_min=a.hub_min)
    if a.format == "json":
        out = json.dumps(model, indent=2) + "\n"
    elif a.format == "mermaid":
        out = render_mermaid(model, a.level, a.max_nodes, a.max_edges, a.focus)
    elif a.format == "html":
        out = render_html(model, a.html_max_files)
    else:
        out = render_text(model, a.top)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(out)
        print("code_map: wrote %s (%d files, %d modules, %d edges)" % (
            a.out, model["stats"]["files"], model["stats"]["modules"], model["stats"]["edges"]), file=sys.stderr)
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
