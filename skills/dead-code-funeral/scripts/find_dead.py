#!/usr/bin/env python3
"""Find files, functions, classes and exports in a repository that nothing else uses. Stdlib only, read-only.

Usage: python3 find_dead.py [-C REPO] [--json] [--svg card.svg] [--show-paths] [--now YYYY-MM-DD] [--top N]
  -C REPO       repository to scan (default: current directory)
  --json        machine-readable report on stdout
  --svg FILE    write a 1200x630 gravestone card with the totals (no file paths unless --show-paths)
  --no-credit     leave the "made with agent-skills" line off the card
  --show-paths  put the top three item names on the card
  --now DATE    "today" for the days-since-last-touch numbers (default: today)
  --top N       rows in the text report (default 20; --json always lists everything)

What it scans (tracked files only; node_modules, vendor, dist, build, minified and .d.ts files skipped):
  Python      .py files, parsed with ast. Imports (absolute and relative) are resolved by matching the
              dotted name against the tail of each file's path, so src/ layouts and script folders work.
  JS/TS       .js .jsx .ts .tsx .mjs .cjs .mts .cts, scanned with regular expressions for import/export
              ... from, bare import, require() and import() with a literal path. Relative paths are resolved
              with extension, index and platform lookup (a .js specifier also finds the .ts file, ./Foo finds
              Foo.web.js and Foo.native.js); other specifiers
              are tried as path suffixes after stripping @/ or ~/. .vue .svelte .astro .html .mdx files are
              read for imports but never reported.
  Never reported as dead files: entry points (main, index, cli, __main__, __init__, app, server, setup,
  manage, conftest, wsgi, asgi), scripts with a shebang or an `if __name__ == "__main__"` block, tests and
  fixtures, configs (*.config.*, *rc.*, dotfiles, conf.py), stories, files named in package.json main/module/browser/bin/
  exports/scripts or in a "module:function" entry point in pyproject.toml/setup.cfg/setup.py, files under
  pages/ or routes/, Next.js app/ route files and root middleware, SvelteKit + and hooks files, and Django
  modules found by name (models, admin, apps, urls, settings, migrations/, management/commands/,
  templatetags/).

Confidence:
  high    a file no other file imports and whose path or module name appears nowhere else
  medium  a top-level Python function or class, or an exported JS/TS symbol, whose name appears in no
          other code (same file excluded from the definition itself)
  low     as above, but the name or path shows up in strings, config files, docs or templates, or a Python
          definition carries a decorator that may register it with a framework

Dates: a file's "born" is the oldest commit that added it, following renames (one pass of
git log -M --name-status), and "last touched" is its latest commit. A symbol's dates come from git blame
over its lines: the oldest and newest surviving line.

Exit codes: 0 nothing found
            1 dead code found
            2 not a git repository, no commits, or bad arguments
"""
from __future__ import annotations

import argparse
import ast
import datetime as dt
import io
import json
import os
import posixpath
import re
import subprocess
import sys
import tokenize
from xml.sax.saxutils import escape

GIT_ENV = dict(os.environ, LC_ALL="C", LANG="C", GIT_TERMINAL_PROMPT="0", GIT_PAGER="cat")
PY_EXT = (".py",)
JS_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
PLATFORMS = (".web", ".native", ".ios", ".android")  # React Native / bundler platform files: ./Foo -> Foo.web.js
JS_READ_ONLY = (".vue", ".svelte", ".astro", ".html", ".mdx")
TEXT_MAX = 1000000
VENDOR_DIRS = {"node_modules", "vendor", "third_party", "third-party", "dist", "build", "out", ".next",
               "__generated__", "generated", "bower_components", "site-packages", ".venv", "venv", ".git"}
LOCK_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock", "Pipfile.lock",
              "bun.lockb", "bun.lock", "npm-shrinkwrap.json"}
ENTRY_STEMS = {"main", "index", "cli", "__main__", "__init__", "app", "server", "setup", "manage", "conftest",
               "wsgi", "asgi", "noxfile", "fabfile"}
NEXT_ROUTE_STEMS = {"page", "layout", "route", "loading", "error", "not-found", "template", "default",
                    "global-error", "opengraph-image", "twitter-image", "icon", "apple-icon", "sitemap", "robots",
                    "manifest"}
DJANGO_STEMS = {"models", "admin", "apps", "urls", "settings"}
DJANGO_DIRS = {"migrations", "templatetags"}
TEST_DIRS = {"test", "tests", "__tests__", "__mocks__", "spec", "e2e", "testing", "fixtures", "__fixtures__",
             "testdata"}
PLAIN_DECORATORS = {"dataclass", "dataclasses.dataclass", "total_ordering", "functools.total_ordering",
                    "lru_cache", "functools.lru_cache", "cache", "functools.cache", "wraps", "contextmanager",
                    "contextlib.contextmanager", "staticmethod", "classmethod", "property"}
CONF_RANK = {"high": 0, "medium": 1, "low": 2}
WORD = re.compile(r"[A-Za-z_$][\w$]*")
JS_TOKENS = re.compile(r"//[^\n]*|/\*.*?\*/|'(?:\\.|[^'\\\n])*'|\"(?:\\.|[^\"\\\n])*\"|`(?:\\.|[^`\\])*`", re.S)
JS_IMPORTS = [re.compile(r"""\b(?:import|export)\s[^'"`;]*?\bfrom\s*['"]([^'"\n]+)['"]"""),
              re.compile(r"""\bimport\s*['"]([^'"\n]+)['"]"""),
              re.compile(r"""\b(?:require|import)\s*\(\s*['"]([^'"\n]+)['"]\s*\)""")]
JS_EXPORT_DECL = re.compile(r"^export\s+(?:declare\s+)?(?:async\s+)?(?:abstract\s+)?"
                            r"(function\*?|class|const|let|var|interface|type|enum)\s+([A-Za-z_$][\w$]*)", re.M)
JS_EXPORT_LIST = re.compile(r"^export\s*(?:type\s*)?\{([^}]*)\}\s*(;|$)", re.M)
ENTRY_SPEC = re.compile(r"""=\s*["']?([A-Za-z_][\w.]*)\s*:\s*([A-Za-z_][\w.]*)""")


class GitError(Exception):
    pass


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo, *args], env=GIT_ENV, capture_output=True, stdin=subprocess.DEVNULL)
    if r.returncode:
        raise GitError(r.stderr.decode("utf-8", "replace").strip() or "git %s failed" % args[0])
    return r.stdout.decode("utf-8", "replace")


def skipped(path):
    parts = path.split("/")
    name = parts[-1]
    return (any(p in VENDOR_DIRS for p in parts[:-1]) or name in LOCK_NAMES or ".min." in name
            or name.endswith((".d.ts", ".d.mts", ".d.cts", ".map")))


def is_test(path):
    parts = path.split("/")
    name = parts[-1]
    stem = name.split(".")[0]
    return (any(p in TEST_DIRS for p in parts[:-1])
            or stem.startswith("test_") or stem.endswith("_test") or stem in ("conftest", "tests")
            or re.search(r"\.(test|spec|e2e|cy|stories|story)\.[a-z]+$", name) is not None)


def convention_reason(path, text):
    """Why a file is used without being imported, or None."""
    parts = path.split("/")
    name = parts[-1]
    stem = name.rsplit(".", 1)[0]
    dirs = parts[:-1]
    if is_test(path):
        return "test"
    if name.split(".")[0] in ENTRY_STEMS:  # index.ios.js, main.web.ts
        return "entry point name"
    if name.startswith(".") or ".config." in name or re.search(r"rc\.[cm]?[jt]s$", name) or \
            stem.endswith(("config", "Config")) or stem == "conf":
        return "config"
    if text.startswith("#!") or re.search(r"""^if\s+__name__\s*==\s*['"]__main__['"]""", text, re.M):
        return "runnable script"
    if name.endswith(JS_EXT) and ("app" in dirs and stem in NEXT_ROUTE_STEMS
                                  or stem in ("middleware", "instrumentation") and len(dirs) <= 1):
        return "Next.js route"
    if name.endswith(JS_EXT) and ("pages" in dirs or "routes" in dirs):
        return "file-based route"
    if name.startswith(("+", "hooks.")) and name.endswith(JS_EXT):
        return "SvelteKit convention"
    if name.endswith(".py") and (stem in DJANGO_STEMS or stem.startswith("settings")
                                 or DJANGO_DIRS & set(dirs) or "/management/commands/" in "/" + path):
        return "Django convention"
    return None


def read_text(repo, path):
    try:
        with open(os.path.join(repo, path), "rb") as fh:
            data = fh.read(TEXT_MAX + 1)
    except OSError:
        return None
    if len(data) > TEXT_MAX or b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", "replace")


# ---------- per-language scanning ----------

def py_scan(path, text):
    """Imports, top-level definitions, code identifiers and string text of one Python file."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    pkg = path.split("/")[:-1]
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = pkg[:len(pkg) - node.level + 1] if node.level else []
            if node.level and node.level - 1 > len(pkg):
                continue
            mod = base + (node.module.split(".") if node.module else [])
            if mod:
                imports.append(".".join(mod))
            imports += [".".join(mod + [a.name]) for a in node.names if a.name != "*"]
    defs = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name.startswith("__") and node.name.endswith("__"):
                continue
            decos = [ast.unparse(d) if hasattr(ast, "unparse") else "decorator" for d in node.decorator_list]
            decos = [d for d in decos if d.split("(")[0] not in PLAIN_DECORATORS]
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            defs.append({"name": node.name, "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                         "start": start, "end": getattr(node, "end_lineno", node.lineno), "decorators": decos})
    idents, strings = {}, []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.NAME:
                idents.setdefault(tok.string, []).append(tok.start[0])
            elif tok.type == tokenize.STRING or tok.type == getattr(tokenize, "FSTRING_MIDDLE", -1):
                strings.append(tok.string)
                if "f" in re.match(r"[rRbBuUfF]*", tok.string).group(0).lower():
                    # before Python 3.12 an f-string is one STRING token: names inside {} still count as code
                    for w in WORD.findall(" ".join(re.findall(r"\{([^{}]*)\}", tok.string))):
                        idents.setdefault(w, []).append(tok.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return None
    return {"imports": imports, "defs": defs, "idents": idents, "strings": "\n".join(strings)}


def js_scan(path, text):
    strings = []

    def strip(m):
        s = m.group(0)
        if s[0] in "'\"`":
            strings.append(s[1:-1])
            return '""' + "\n" * s.count("\n")
        return "\n" * s.count("\n")

    code = JS_TOKENS.sub(strip, text)
    no_comments = JS_TOKENS.sub(lambda m: m.group(0) if m.group(0)[0] in "'\"`" else "", text)
    imports = [m.group(1) for rx in JS_IMPORTS for m in rx.finditer(no_comments)]
    lines = text.split("\n")
    defs = []
    if path.endswith(JS_EXT):
        for m in JS_EXPORT_DECL.finditer(code):
            start = code.count("\n", 0, m.start()) + 1
            defs.append({"name": m.group(2), "kind": "export " + m.group(1).rstrip("*"), "start": start,
                         "end": js_block_end(lines, start), "decorators": []})
        for m in JS_EXPORT_LIST.finditer(code):
            for item in m.group(1).split(","):
                bits = item.split()
                if not bits or bits[0] == "type" and len(bits) == 1:
                    continue
                bits = [b for b in bits if b != "type"]
                local, public = bits[0], bits[-1]
                if public == "default":
                    continue
                decl = re.search(r"^(?:async\s+)?(?:function\*?|class|const|let|var|interface|type|enum)\s+%s\b"
                                 % re.escape(local), code, re.M)
                start = code.count("\n", 0, decl.start()) + 1 if decl else code.count("\n", 0, m.start()) + 1
                end = js_block_end(lines, start) if decl else start
                defs.append({"name": public, "local": local, "kind": "export", "start": start, "end": end,
                             "decorators": [], "self_refs": 1 if decl else 0})
    idents = {}
    for n, line in enumerate(code.split("\n"), 1):
        for w in WORD.findall(line):
            idents.setdefault(w, []).append(n)
    return {"imports": imports, "defs": defs, "idents": idents, "strings": "\n".join(strings)}


def js_block_end(lines, start):
    # shortcut: a declaration ends before the next line that starts in column 0 (formatter-style code);
    # hand-formatted code may be miscounted, use a real parser if line counts must be exact.
    j = start
    while j < len(lines) and (not lines[j].strip() or lines[j][:1] in " \t})]"):
        j += 1
    while j > start and not lines[j - 1].strip():
        j -= 1
    return j


# ---------- resolution ----------

class Index:
    def __init__(self, files):
        self.files = set(files)
        self.py = {}
        for f in files:
            if f.endswith(".py"):
                parts = f[:-3].split("/")
                if parts[-1] == "__init__":
                    parts = parts[:-1]
                for i in range(len(parts)):
                    self.py.setdefault(".".join(parts[i:]), []).append(f)

    def py_module(self, dotted):
        return self.py.get(dotted, [])

    @staticmethod
    def js_candidates(base):
        out = [base]
        for b in dict.fromkeys([base, re.sub(r"\.[cm]?jsx?$", "", base)]):  # a .js specifier may mean the .ts file
            for b2 in [b] + [b + p for p in PLATFORMS]:
                out += [b2 + e for e in JS_EXT + JS_READ_ONLY]
            out += [b + "/index" + p + e for p in ("",) + PLATFORMS for e in JS_EXT]
        return out

    def js_resolve(self, importer, spec):
        spec = spec.split("?")[0]
        if spec.startswith("."):
            base = posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec))
            return [c for c in self.js_candidates(base) if c in self.files]
        spec = re.sub(r"^[@~#]/", "", spec)
        out = []
        for c in self.js_candidates(spec):
            out += [f for f in self.suffix_index().get(c.rsplit("/", 1)[-1], []) if f == c or f.endswith("/" + c)]
        return out

    def suffix_index(self):
        if not hasattr(self, "_names"):
            self._names = {}
            for f in self.files:
                self._names.setdefault(f.rsplit("/", 1)[-1], []).append(f)
        return self._names


def package_json_entries(repo, files, index):
    alive = set()
    for pj in [f for f in files if f.rsplit("/", 1)[-1] == "package.json"]:
        try:
            with open(os.path.join(repo, pj), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        root = posixpath.dirname(pj)
        values = []

        def walk(v):
            if isinstance(v, str):
                values.extend(v.split())
            elif isinstance(v, dict):
                for x in v.values():
                    walk(x)
            elif isinstance(v, list):
                for x in v:
                    walk(x)

        for key in ("main", "module", "browser", "bin", "exports", "scripts", "types", "source"):
            walk(data.get(key))
        for v in values:
            if "/" in v or "." in v:
                base = posixpath.normpath(posixpath.join(root, v.lstrip("./") if v.startswith("./") else v))
                alive.update(c for c in index.js_candidates(base) + [base] if c in index.files)
    return alive


def looks_like_library(repo, files):
    for f in files:
        name = f.rsplit("/", 1)[-1]
        if name == "package.json" and "/" not in f:
            try:
                with open(os.path.join(repo, f), encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and data.get("name") and not data.get("private"):
                return "package.json has a name and is not private"
        if f in ("setup.py", "setup.cfg"):
            return f + " exists"
        if f == "pyproject.toml":
            text = read_text(repo, f) or ""
            if re.search(r"^\[(project|tool\.poetry)\]", text, re.M):
                return "pyproject.toml declares a package"
    return None


# ---------- analysis ----------

def analyse(repo, now):
    try:
        root = git(repo, "rev-parse", "--show-toplevel").strip()
    except (GitError, OSError) as e:
        raise GitError("not a git repository: %s" % e)
    try:
        git(root, "rev-parse", "--verify", "-q", "HEAD")
    except GitError:
        raise GitError("the repository has no commits yet")
    files = [f for f in git(root, "ls-files", "-z").split("\0") if f and not skipped(f)]
    files = [f for f in files if os.path.isfile(os.path.join(root, f))]
    index = Index(files)
    texts, scans = {}, {}
    for f in files:
        t = read_text(root, f)
        if t is None:
            continue
        texts[f] = t
        if f.endswith(PY_EXT):
            scans[f] = py_scan(f, t)
        elif f.endswith(JS_EXT + JS_READ_ONLY):
            scans[f] = js_scan(f, t)

    imported_by = {}
    for f, s in scans.items():
        if not s:
            continue
        for spec in s["imports"]:
            targets = index.py_module(spec) if f.endswith(".py") else index.js_resolve(f, spec)
            for t in targets:
                if t != f:
                    imported_by.setdefault(t, set()).add(f)

    alive_files = package_json_entries(root, files, index)
    entry_names = set()
    for cfg in ("pyproject.toml", "setup.cfg", "setup.py"):
        for f in [x for x in files if x.rsplit("/", 1)[-1] == cfg]:
            for m in ENTRY_SPEC.finditer(texts.get(f, "")):
                alive_files.update(index.py_module(m.group(1)))
                entry_names.add(m.group(2).split(".")[0])

    # where each word appears outside code: string literals in source files, and all other text files
    other_words, other_blobs = {}, {}
    for f, t in texts.items():
        s = scans.get(f)
        blob = s["strings"] if s else t
        if s is None and f.endswith(PY_EXT + JS_EXT):
            blob = t  # unparseable source: treat everything as text
        other_blobs[f] = blob
        for w in set(WORD.findall(blob)):
            other_words.setdefault(w, set()).add(f)
    code_words = {}
    for f, s in scans.items():
        for w in (s or {}).get("idents", ()):
            code_words.setdefault(w, set()).add(f)

    items, entries = [], {}
    dead_files = set()
    for f, s in scans.items():
        if not f.endswith(PY_EXT + JS_EXT) or s is None:
            continue
        why = convention_reason(f, texts[f])
        if f in alive_files:
            why = why or "named in package metadata"
        if why:
            entries[f] = why
            continue
        if imported_by.get(f):
            continue
        parts = f.rsplit(".", 1)[0].split("/")
        keys = {f, parts[-1] + "." + f.rsplit(".", 1)[1]}  # full path and file name
        for i in range(len(parts) - 1):  # path tails of 2+ parts, with / and (Python) with dots
            keys.add("/".join(parts[i:]))
            if f.endswith(".py"):
                keys.add(".".join(parts[i:]))
        named_in = sorted(g for g, b in other_blobs.items() if g != f and any(k in b for k in keys))
        n = texts[f].count("\n") + (0 if texts[f].endswith("\n") or not texts[f] else 1)
        items.append({"kind": "file", "path": f, "name": None, "start": 1, "end": n, "lines": n,
                      "confidence": "low" if named_in else "high", "named_in": named_in[:5],
                      "reason": "mentioned in other files but never imported" if named_in
                      else "no other file imports it"})
        dead_files.add(f)

    for f, s in scans.items():
        if not f.endswith(PY_EXT + JS_EXT) or s is None or f in dead_files or entries.get(f) in ("test", "config"):
            continue
        for d in s["defs"]:
            name, local = d["name"], d.get("local", d["name"])
            if name in entry_names:
                continue
            elsewhere = (code_words.get(name, set()) | code_words.get(local, set())) - {f}
            here = sum(1 for ln in s["idents"].get(local, ()) if not d["start"] <= ln <= d["end"]) \
                - d.get("self_refs", 0)
            if elsewhere or here > 0:
                continue
            named_in = sorted(other_words.get(name, set()) - {f})
            if name in WORD.findall(s["strings"]):
                named_in.append(f + " (its own strings)")
            conf, reason = "medium", "no other code refers to it"
            if named_in:
                conf, reason = "low", "named only in strings or non-code files"
            elif d["decorators"]:
                conf, reason = "low", "decorated with @%s, a framework may call it" % d["decorators"][0]
            lines = d["end"] - d["start"] + 1
            items.append({"kind": d["kind"], "path": f, "name": name, "start": d["start"], "end": d["end"],
                          "lines": lines, "confidence": conf, "named_in": named_in[:5], "reason": reason})

    born_last = file_dates(root, {i["path"] for i in items if i["kind"] == "file"})
    for it in items:
        it.update(born_last.get(it["path"], {"born": None, "last_touched": None}) if it["kind"] == "file"
                  else symbol_dates(root, it))
        it["idle_days"] = (now - dt.date.fromisoformat(it["last_touched"])).days if it["last_touched"] else None
        it["obituary"] = obituary(it)
    items.sort(key=lambda i: (CONF_RANK[i["confidence"]], -i["lines"], i["last_touched"] or "", i["path"]))
    return {"root": root, "now": now.isoformat(), "files_scanned": sum(1 for s in scans.values() if s),
            "items": items, "entry_points": entries, "library": looks_like_library(root, files),
            "totals": {"items": len(items), "lines": sum(i["lines"] for i in items),
                       "by_confidence": {c: sum(1 for i in items if i["confidence"] == c) for c in CONF_RANK}}}


def file_dates(root, paths):
    """Birth (oldest add, following renames) and last-touch dates for files, from one pass over git log."""
    if not paths:
        return {}
    out = git(root, "-c", "core.quotepath=off", "log", "-M", "--name-status", "--format=@%ad", "--date=short")
    alias = {p: p for p in paths}  # name in history -> current path
    res, date = {}, None
    for line in out.splitlines():  # newest commit first
        if line.startswith("@"):
            date = line[1:]
            continue
        bits = line.split("\t")
        if len(bits) < 2:
            continue
        cur = alias.get(bits[-1])
        if not cur:
            continue
        rec = res.setdefault(cur, {"born": None, "last_touched": date})
        if bits[0] == "A" or bits[0][:1] in "RC":
            rec["born"] = date  # overwritten by older adds as the walk goes back
        if bits[0][:1] == "R":
            alias[bits[1]] = cur
    return res


def symbol_dates(root, it):
    try:
        out = git(root, "blame", "--line-porcelain", "-L", "%d,%d" % (it["start"], it["end"]), "--", it["path"])
    except GitError:
        return {"born": None, "last_touched": None}
    stamps, sha = [], None
    for line in out.splitlines():
        if re.match(r"^[0-9a-f]{40} ", line):
            sha = line[:40]
        elif line.startswith("author-time ") and sha and sha.strip("0"):
            stamps.append(int(line.split()[1]))
    if not stamps:
        return {"born": None, "last_touched": None}
    day = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat()  # noqa: E731
    return {"born": day(min(stamps)), "last_touched": day(max(stamps))}


def label(it):
    return it["path"] if it["kind"] == "file" else "%s::%s" % (it["path"], it["name"])


def obituary(it):
    span = "%s to %s" % (it["born"], it["last_touched"]) if it["born"] else "never committed"
    tail = "outlived by 0 callers"
    if it["named_in"]:
        tail = "survived only by mentions in %s" % ", ".join(it["named_in"][:3])
    elif it["reason"].startswith("decorated"):
        tail = "outlived by 0 direct callers, %s" % it["reason"]
    return "%s, %s, %d line%s, %s" % (label(it), span, it["lines"], "" if it["lines"] == 1 else "s", tail)


# ---------- output ----------

def headline(rep):
    t = rep["totals"]
    return "%d item%s, %s lines that could go" % (t["items"], "" if t["items"] == 1 else "s", format(t["lines"], ","))


def text_report(rep, top):
    t = rep["totals"]
    out = ["Dead code funeral: %s" % headline(rep),
           "  %d high, %d medium, %d low confidence; %d source files scanned, %d entry points kept"
           % (t["by_confidence"]["high"], t["by_confidence"]["medium"], t["by_confidence"]["low"],
              rep["files_scanned"], len(rep["entry_points"]))]
    if rep["library"]:
        out.append("  Looks like a published library (%s): unused exports may have outside users." % rep["library"])
    if not rep["items"]:
        return "\n".join(out + ["", "Nothing to bury."]) + "\n"
    out += ["", "  #  confidence  kind             lines  born        last touched  item"]
    for n, it in enumerate(rep["items"][:top], 1):
        out.append("%3d  %-10s  %-15s %6d  %-10s  %-12s  %s" % (
            n, it["confidence"], it["kind"], it["lines"], it["born"] or "-", it["last_touched"] or "-", label(it)))
    if len(rep["items"]) > top:
        out.append("  ... %d more (use --top or --json)" % (len(rep["items"]) - top))
    out += ["", "Obituaries"] + ["  " + it["obituary"] + (
        ", untouched for %s days" % format(it["idle_days"], ",") if it["idle_days"] is not None else "")
        for it in rep["items"][:top]]
    out += ["", "Not checked: dynamic imports with computed paths, path aliases other than @/ and ~/, framework",
            "conventions beyond the built-in list, reflection, and callers outside this repository."]
    return "\n".join(out) + "\n"


CREDIT = "made with github.com/milad-ahmd/agent-skills"


def credit(svg, on=True):
    """Sign the card in its free top-right corner so a shared card links back; --no-credit turns it off."""
    if not on:
        return svg
    return svg.replace("</svg>", '<text x="1160" y="44" font-size="16" fill="#71717a" text-anchor="end">%s</text>\n</svg>' % CREDIT)


def svg_card(rep, show_paths):
    t = rep["totals"]
    el = ['<rect width="1200" height="630" fill="#15171c"/>',
          '<rect x="0" y="560" width="1200" height="70" fill="#1f3a24"/>',
          '<path d="M340 560 V220 A260 160 0 0 1 860 220 V560 Z" fill="#3a3f47" stroke="#5b616b" stroke-width="6"/>']

    def text(x, y, body, size, color="#e5e7eb", weight="400", anchor="middle"):
        el.append('<text x="%d" y="%d" font-size="%d" font-weight="%s" fill="%s" text-anchor="%s">%s</text>'
                  % (x, y, size, weight, color, anchor, escape(body)))

    text(600, 150, "R.I.P.", 64, "#f4f4f5", "800")
    text(600, 290, format(t["lines"], ","), 110, "#f4f4f5", "800")
    text(600, 335, "lines of dead code", 30, "#d4d4d8")
    c = t["by_confidence"]
    text(600, 390, "%d item%s: %d high, %d medium, %d low" % (
        t["items"], "" if t["items"] == 1 else "s", c["high"], c["medium"], c["low"]), 24, "#a1a1aa")
    born = sorted(i["born"] for i in rep["items"] if i["born"])
    if born:
        text(600, 430, "oldest born %s" % born[0], 22, "#a1a1aa")
    if show_paths:
        for n, it in enumerate(rep["items"][:3]):
            text(600, 475 + n * 28, label(it)[-48:], 18, "#d4d4d8")
    text(60, 605, "%d source files scanned, %d entry points spared" % (
        rep["files_scanned"], len(rep["entry_points"])), 20, "#9ca3af", anchor="start")
    text(1140, 605, "as of %s" % rep["now"], 20, "#9ca3af", anchor="end")
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630" '
            'font-family="Inter, \'Helvetica Neue\', Arial, sans-serif">\n<title>%s</title>\n%s\n</svg>\n'
            % (escape("Dead code: " + headline(rep)), "\n".join(el)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__[__doc__.index("What it scans"):])
    ap.add_argument("-C", dest="repo", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--svg", metavar="FILE")
    ap.add_argument("--no-credit", action="store_true", help="leave the \"made with agent-skills\" line off the card")
    ap.add_argument("--show-paths", action="store_true")
    ap.add_argument("--now", help="YYYY-MM-DD (default today)")
    ap.add_argument("--top", type=int, default=20)
    a = ap.parse_args(argv)
    try:
        now = dt.date.fromisoformat(a.now) if a.now else dt.date.today()
    except ValueError:
        ap.error("--now must be YYYY-MM-DD")
    if a.top < 1:
        ap.error("--top must be at least 1")
    try:
        rep = analyse(a.repo, now)
    except GitError as e:
        print("find_dead: %s" % e, file=sys.stderr)
        return 2
    if a.svg:
        with open(a.svg, "w", encoding="utf-8") as fh:
            fh.write(credit(svg_card(rep, a.show_paths), not a.no_credit))
    if a.json:
        rep = dict(rep, root=os.path.basename(rep["root"]))
        json.dump(rep, sys.stdout, indent=2)
        print()
    else:
        sys.stdout.write(text_report(rep, a.top))
        if a.svg:
            print("\nCard written to %s" % a.svg)
    return 1 if rep["items"] else 0


if __name__ == "__main__":
    sys.exit(main())
