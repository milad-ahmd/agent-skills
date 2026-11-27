#!/usr/bin/env python3
"""Estimate what a codebase would cost to build from scratch with the basic COCOMO model (organic mode), next
to what git says actually happened. Counts files, code, comment and blank lines per language, then applies
Boehm's 1981 equations with the same defaults as SLOCCount and scc. Stdlib only, read-only.

Usage: python3 valuation.py [-C REPO] [--json] [--svg card.svg] [--salary N] [--overhead X] [--currency USD]
                            [--show-authors]
  -C REPO         directory to measure (default: current directory)
  --json          everything as JSON on stdout
  --svg FILE      also write a 1200x630 card
  --salary N      average yearly salary of one developer (default 56286, SLOCCount's year-2000 US figure)
  --overhead X    multiplier for facilities, equipment, management and so on (default 2.4)
  --currency C    label for the salary and cost (default USD); any other currency needs --salary
  --show-authors  list git author names with commit counts (only the count is shown otherwise)

Files: `git ls-files` inside a git work tree (so .gitignore is respected), otherwise a directory walk.
Lockfiles, vendored and build directories, minified and generated files, docs and data files are skipped.

Model (references/sources.md):
  effort (person-months) = 2.4 x KLOC^1.05    schedule (months) = 2.5 x effort^0.38
  people = effort / schedule                  cost = effort / 12 x salary x overhead
KLOC counts code lines only, not comments or blank lines.

Exit codes: 0 estimate printed
            2 bad arguments, no code found, or the SVG could not be written
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from xml.sax.saxutils import escape

CAVEAT = ("COCOMO is a 1981 model estimate, not a valuation; lines of code are a poor proxy for value.")

# Basic COCOMO, organic mode, and the SLOCCount/scc defaults. Sources: references/sources.md
A, B, C, D = 2.4, 1.05, 2.5, 0.38
DEFAULT_SALARY = 56286
DEFAULT_OVERHEAD = 2.4
SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£"}

C_LIKE = (("//",), (("/*", "*/"),), ('"', "'"))
HASH = (("#",), (), ('"', "'"))
# name: (line comment tokens, block comment (start, end) pairs, quote characters)
LANGS = {
    "C": C_LIKE, "C++": C_LIKE, "C#": C_LIKE, "Java": C_LIKE, "Kotlin": C_LIKE, "Scala": C_LIKE,
    "Groovy": C_LIKE, "Dart": C_LIKE, "Objective-C": C_LIKE, "Solidity": C_LIKE, "Protocol Buffers": C_LIKE,
    "JavaScript": (("//",), (("/*", "*/"),), ('"', "'", "`")),
    "TypeScript": (("//",), (("/*", "*/"),), ('"', "'", "`")),
    "Go": (("//",), (("/*", "*/"),), ('"', "'", "`")),
    "Rust": (("//",), (("/*", "*/"),), ('"',)),  # ' starts lifetimes, not strings
    "Swift": (("//",), (("/*", "*/"),), ('"',)),
    "PHP": (("//", "#"), (("/*", "*/"),), ('"', "'")),
    "CSS": ((), (("/*", "*/"),), ('"', "'")),
    "SCSS": (("//",), (("/*", "*/"),), ('"', "'")),
    "Less": (("//",), (("/*", "*/"),), ('"', "'")),
    "Python": HASH, "Shell": HASH, "Perl": HASH, "R": HASH, "Elixir": HASH, "Nim": HASH,
    "Makefile": HASH, "Dockerfile": HASH, "CMake": HASH, "Terraform": (("#", "//"), (("/*", "*/"),), ('"',)),
    "Ruby": (("#",), (("=begin", "=end"),), ('"', "'")),
    "PowerShell": (("#",), (("<#", "#>"),), ('"', "'")),
    "SQL": (("--",), (("/*", "*/"),), ("'",)),
    "Lua": (("--",), (("--[[", "]]"),), ('"', "'")),
    "Haskell": (("--",), (("{-", "-}"),), ('"',)),
    "Elm": (("--",), (("{-", "-}"),), ('"',)),
    "Erlang": (("%",), (), ('"',)),
    "Clojure": ((";",), (), ('"',)),
    "HTML": ((), (("<!--", "-->"),), ()),
    "XML": ((), (("<!--", "-->"),), ()),
    "Vue": (("//",), (("<!--", "-->"), ("/*", "*/")), ()),
    "Svelte": (("//",), (("<!--", "-->"), ("/*", "*/")), ()),
}
EXTENSIONS = {
    ".c": "C", ".h": "C", ".cc": "C++", ".cpp": "C++", ".cxx": "C++", ".hpp": "C++", ".hh": "C++",
    ".cs": "C#", ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin", ".scala": "Scala", ".groovy": "Groovy",
    ".gradle": "Groovy", ".dart": "Dart", ".m": "Objective-C", ".mm": "Objective-C", ".sol": "Solidity",
    ".proto": "Protocol Buffers", ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript",
    ".cjs": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript", ".mts": "TypeScript", ".cts": "TypeScript",
    ".go": "Go", ".rs": "Rust", ".swift": "Swift", ".php": "PHP", ".css": "CSS", ".scss": "SCSS",
    ".less": "Less", ".py": "Python", ".pyi": "Python", ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell",
    ".pl": "Perl", ".pm": "Perl", ".r": "R", ".ex": "Elixir", ".exs": "Elixir", ".nim": "Nim",
    ".mk": "Makefile", ".cmake": "CMake", ".tf": "Terraform", ".rb": "Ruby", ".rake": "Ruby",
    ".ps1": "PowerShell", ".psm1": "PowerShell", ".sql": "SQL", ".lua": "Lua", ".hs": "Haskell",
    ".elm": "Elm", ".erl": "Erlang", ".hrl": "Erlang", ".clj": "Clojure", ".cljs": "Clojure",
    ".html": "HTML", ".htm": "HTML", ".xml": "XML", ".vue": "Vue", ".svelte": "Svelte",
}
FILENAMES = {"Makefile": "Makefile", "GNUmakefile": "Makefile", "Dockerfile": "Dockerfile",
             "CMakeLists.txt": "CMake", "Rakefile": "Ruby", "Gemfile": "Ruby", "Jenkinsfile": "Groovy"}
SHEBANGS = {"python": "Python", "sh": "Shell", "bash": "Shell", "zsh": "Shell", "node": "JavaScript",
            "ruby": "Ruby", "perl": "Perl"}

LOCKFILES = {"package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lock", "bun.lockb",
             "Cargo.lock", "poetry.lock", "Pipfile.lock", "uv.lock", "Gemfile.lock", "composer.lock", "go.sum",
             "mix.lock", "flake.lock", "Podfile.lock", "packages.lock.json", "pubspec.lock", "gradle.lockfile"}
VENDOR_DIRS = {"node_modules", "vendor", "third_party", "third-party", "dist", "build", "__generated__",
               "generated", "bower_components", "Pods", ".yarn", ".git", ".venv", "venv", "__pycache__",
               ".tox", ".mypy_cache", ".next", ".nuxt", "target", "coverage"}
GENERATED = ["*.min.js", "*.min.css", "*.min.mjs", "*.bundle.js", "*.pb.go", "*_pb2.py", "*_pb2_grpc.py",
             "*.generated.*", "*.g.dart", "*.designer.cs"]
DOC_DATA_EXT = {".md", ".mdx", ".rst", ".txt", ".adoc", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
                ".csv", ".tsv", ".svg", ".ipynb", ".lock", ".log", ".env"}


def skip_reason(path):
    """Why a relative path is not counted, or None. Language detection happens later."""
    parts = path.replace(os.sep, "/").split("/")
    name = parts[-1]
    if name in LOCKFILES:
        return "lockfile"
    if any(p in VENDOR_DIRS for p in parts[:-1]):
        return "vendored or build output"
    if any(fnmatch.fnmatch(name, g) for g in GENERATED):
        return "minified or generated"
    if os.path.splitext(name)[1].lower() in DOC_DATA_EXT:
        return "docs or data"
    return None


def language(path, first_line=""):
    name = os.path.basename(path)
    if name in FILENAMES:
        return FILENAMES[name]
    ext = os.path.splitext(name)[1].lower()
    if ext:
        return EXTENSIONS.get(ext)
    if first_line.startswith("#!"):
        words = first_line[2:].replace("/", " ").split()
        if words and words[-1] in ("env",):
            words = words[:-1]
        for w in reversed(words):
            base = w.rstrip("0123456789.")
            if base in SHEBANGS:
                return SHEBANGS[base]
    return None


def classify(line, spec, state):
    """Return (kind, state) where kind is code, comment or blank and state is the open block's end token."""
    line_toks, blocks, quotes = spec
    s = line.strip()
    if not s:
        return ("comment" if state else "blank"), state
    starts = [b[0] for b in blocks]
    if state is None and not any(t in s for t in line_toks) and not any(t in s for t in starts):
        return "code", None
    has_code = has_comment = False
    i, n = 0, len(s)
    while i < n:
        if state:
            has_comment = True
            j = s.find(state, i)
            if j < 0:
                break
            i, state = j + len(state), None
            continue
        ch = s[i]
        if ch.isspace():
            i += 1
            continue
        block = next((b for b in blocks if s.startswith(b[0], i)), None)
        if block:  # checked before line tokens so Lua's --[[ wins over --
            state, i, has_comment = block[1], i + len(block[0]), True
            continue
        if any(s.startswith(t, i) for t in line_toks):
            has_comment = True
            break
        has_code = True
        if ch in quotes:
            j = i + 1
            while j < n and s[j] != ch:
                j += 2 if s[j] == "\\" else 1
            i = j + 1
            continue
        i += 1
    return ("code" if has_code else "comment"), state


def count_text(text, lang):
    spec = LANGS[lang]
    counts = {"code": 0, "comment": 0, "blank": 0}
    state = None
    for line in text.splitlines():
        kind, state = classify(line, spec, state)
        counts[kind] += 1
    return counts


def looks_generated(head):
    """Go's `// Code generated ... DO NOT EDIT.` header or an @generated marker in the first lines."""
    return ("Code generated" in head and "DO NOT EDIT" in head) or "@generated" in head


def git(repo, *args):
    env = dict(os.environ, LC_ALL="C")
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, env=env, stdin=subprocess.DEVNULL)


def in_git(repo):
    r = git(repo, "rev-parse", "--is-inside-work-tree")
    return r.returncode == 0 and r.stdout.strip() == b"true"


def list_files(repo):
    """Relative paths to consider, and where they came from."""
    if in_git(repo):
        out = git(repo, "ls-files", "-z").stdout.decode("utf-8", "replace")
        return [p for p in out.split("\0") if p], "git ls-files"
    paths = []
    for dirpath, dirs, files in os.walk(repo):
        dirs[:] = sorted(d for d in dirs if d not in VENDOR_DIRS)
        rel = os.path.relpath(dirpath, repo)
        paths += [f if rel == "." else os.path.join(rel, f) for f in sorted(files)]
    return paths, "directory walk"


def scan(repo):
    paths, source = list_files(repo)
    langs, skipped = {}, Counter()
    for rel in paths:
        full = os.path.join(repo, rel)
        if os.path.islink(full) or not os.path.isfile(full):
            continue
        reason = skip_reason(rel)
        if reason:
            skipped[reason] += 1
            continue
        try:
            with open(full, "rb") as fh:
                raw = fh.read()
        except OSError:
            skipped["unreadable"] += 1
            continue
        if b"\0" in raw[:8192]:
            skipped["binary"] += 1
            continue
        text = raw.decode("utf-8", "replace")
        lang = language(rel, text.split("\n", 1)[0])
        if not lang:
            skipped["not a recognised language"] += 1
            continue
        if looks_generated("\n".join(text.split("\n", 5)[:5])):
            skipped["minified or generated"] += 1
            continue
        if lang in ("JavaScript", "CSS") and text and len(text) / (text.count("\n") + 1) > 500:
            skipped["minified or generated"] += 1  # shortcut: average line over 500 chars means minified
            continue
        c = count_text(text, lang)
        row = langs.setdefault(lang, {"language": lang, "files": 0, "code": 0, "comment": 0, "blank": 0})
        row["files"] += 1
        for k in c:
            row[k] += c[k]
    rows = sorted(langs.values(), key=lambda r: (-r["code"], r["language"]))
    totals = {k: sum(r[k] for r in rows) for k in ("files", "code", "comment", "blank")}
    return rows, totals, dict(sorted(skipped.items())), source


def cocomo(code_lines, salary, overhead):
    kloc = code_lines / 1000.0
    effort = A * kloc ** B
    schedule = C * effort ** D if effort else 0.0
    people = effort / schedule if schedule else 0.0
    return {"kloc": round(kloc, 3), "effort_person_months": round(effort, 2), "schedule_months": round(schedule, 2),
            "people": round(people, 2), "cost": round(effort / 12 * salary * overhead)}


def history(repo, show_authors):
    """What git says actually happened under this directory, or None outside a git repo."""
    if not in_git(repo):
        return None
    # At the top level count every commit; in a subdirectory only commits that touched it.
    sub = git(repo, "rev-parse", "--show-prefix").stdout.strip()
    r = git(repo, "log", "--format=%at%x09%aE%x09%aN", *(["--", "."] if sub else []))
    if r.returncode:
        return None  # no commits yet
    stamps, by_email, names = [], Counter(), {}
    for line in r.stdout.decode("utf-8", "replace").splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3 or not parts[0].isdigit():
            continue
        stamps.append(int(parts[0]))
        key = parts[1].lower() or parts[2]
        by_email[key] += 1
        names.setdefault(key, parts[2])
    if not stamps:
        return None
    first, last = min(stamps), max(stamps)
    day = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")  # noqa: E731
    out = {"commits": len(stamps), "authors": len(by_email), "first_commit": day(first), "last_commit": day(last),
           "span_months": round((last - first) / 86400.0 / 30.44, 1),
           "shallow": git(repo, "rev-parse", "--is-shallow-repository").stdout.strip() == b"true"}
    if show_authors:
        out["author_list"] = [{"name": names[k], "commits": n} for k, n in by_email.most_common()]
    return out


def money(n, currency):
    sym = SYMBOLS.get(currency)
    return (sym + "{:,}".format(n)) if sym else "{:,} {}".format(n, currency)


def months(m):
    return "%.1f months" % m if m >= 1 else "%.0f days" % (m * 30.44)


def headline(data):
    e = data["estimate"]
    return "%.1f people for %s" % (e["people"], months(e["schedule_months"]))


def actual_line(data):
    g = data["git"]
    if not g:
        return "no git history to compare with"
    return "%s, %s over %s" % (plural(g["authors"], "author"), plural(g["commits"], "commit"),
                               months(g["span_months"]))


def plural(n, word):
    return "{:,} {}{}".format(n, word, "" if n == 1 else "s")


def report(data):
    out = ["Codebase valuation: %s (%s, %s files counted)" % (data["repo"], data["file_source"],
                                                             "{:,}".format(data["totals"]["files"])), ""]
    w = max([len("Language")] + [len(r["language"]) for r in data["languages"]])
    fmt = "%-{0}s %7s %10s %9s %9s".format(w)
    out.append(fmt % ("Language", "Files", "Code", "Comment", "Blank"))
    for r in data["languages"] + [dict(data["totals"], language="Total")]:
        out.append(fmt % (r["language"], "{:,}".format(r["files"]), "{:,}".format(r["code"]),
                          "{:,}".format(r["comment"]), "{:,}".format(r["blank"])))
    if data["skipped"]:
        out.append("Skipped: " + ", ".join("%d %s" % (n, k) for k, n in data["skipped"].items()))
    e, m = data["estimate"], data["model"]
    out += ["", "Basic COCOMO, organic mode, on %s KLOC of code:" % "{:,.1f}".format(e["kloc"]),
            "  Effort    %.1f person-months" % e["effort_person_months"],
            "  Schedule  %s" % months(e["schedule_months"]),
            "  People    %.1f" % e["people"],
            "  Cost      %s  (salary %s a year x overhead %s)" % (money(e["cost"], m["currency"]),
                                                                money(m["salary"], m["currency"]), m["overhead"]),
            "", "Estimated: " + headline(data)]
    g = data["git"]
    if g:
        out.append("Actually: %s, %s to %s" % (actual_line(data), g["first_commit"], g["last_commit"]))
        if g["shallow"]:
            out.append("Note: shallow clone, so git history is incomplete")
        for a in g.get("author_list", []):
            out.append("  %5d  %s" % (a["commits"], a["name"]))
    else:
        out.append("Actually: not a git repository, so there is no history to compare with")
    out += ["", "Caveat: " + CAVEAT]
    return "\n".join(out) + "\n"


def svg_card(data):
    bg, panel, fg, muted, accent = "#10141a", "#1d232c", "#f4f4f5", "#a1a1aa", "#3ecf8e"
    el = ['<rect width="1200" height="630" fill="%s"/>' % bg,
          '<rect x="0" y="0" width="12" height="630" fill="%s"/>' % accent]

    def text(x, y, body, size, color, weight="400"):
        el.append('<text x="%d" y="%d" font-size="%d" font-weight="%s" fill="%s">%s</text>'
                  % (x, y, size, weight, color, escape(body)))

    e, m = data["estimate"], data["model"]
    text(60, 72, "%s: COST TO BUILD FROM SCRATCH" % data["repo"].upper()[:32], 24, accent, "700")
    text(60, 168, money(e["cost"], m["currency"]), 96, fg, "800")
    text(60, 218, "Estimated: " + headline(data), 30, fg, "600")
    text(60, 262, ("Really built by " if data["git"] else "") + actual_line(data), 30, accent, "600")
    rows = data["languages"][:5]
    peak = float(rows[0]["code"]) if rows and rows[0]["code"] else 1.0
    text(60, 322, "{:,} lines of code".format(data["totals"]["code"]), 24, fg, "700")
    for i, r in enumerate(rows):
        y = 342 + i * 40
        w = max(4, int(520 * r["code"] / peak))
        el.append('<rect x="60" y="%d" width="520" height="26" rx="6" fill="%s"/>' % (y, panel))
        el.append('<rect x="60" y="%d" width="%d" height="26" rx="6" fill="%s" opacity="0.85"/>' % (y, w, accent))
        text(600, y + 20, "%s  %s" % (r["language"][:20], "{:,}".format(r["code"])), 20, fg)
    text(60, 568, "salary %s a year x overhead %s, %s KLOC" % (money(m["salary"], m["currency"]), m["overhead"],
                                                             "{:,.1f}".format(e["kloc"])), 20, muted)
    text(60, 600, CAVEAT, 18, muted)
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630" '
            'font-family="Inter, \'Helvetica Neue\', Arial, sans-serif">\n<title>%s</title>\n%s\n</svg>\n'
            % (escape("Codebase valuation: %s, estimated %s" % (money(e["cost"], m["currency"]), headline(data))),
               "\n".join(el)))


def valuate(repo, salary=DEFAULT_SALARY, overhead=DEFAULT_OVERHEAD, currency="USD", show_authors=False):
    repo = os.path.abspath(repo)
    rows, totals, skipped, source = scan(repo)
    return {"repo": os.path.basename(repo) or repo, "file_source": source, "languages": rows, "totals": totals,
            "skipped": skipped,
            "model": {"name": "basic COCOMO, organic mode", "a": A, "b": B, "c": C, "d": D,
                      "salary": salary, "overhead": overhead, "currency": currency},
            "estimate": cocomo(totals["code"], salary, overhead),
            "git": history(repo, show_authors), "caveat": CAVEAT}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog="Exit codes: 0 estimate printed, 2 bad arguments, no code found or SVG "
                                       "not written.\n" + CAVEAT)
    p.add_argument("-C", dest="repo", default=".")
    p.add_argument("--json", action="store_true")
    p.add_argument("--svg")
    p.add_argument("--salary", type=float)
    p.add_argument("--overhead", type=float, default=DEFAULT_OVERHEAD)
    p.add_argument("--currency", default="USD")
    p.add_argument("--show-authors", action="store_true")
    args = p.parse_args(argv)
    currency = args.currency.strip().upper()
    if not currency.isalpha() or len(currency) != 3:
        p.error("--currency must be a three-letter code such as USD or EUR")
    if currency != "USD" and args.salary is None:
        p.error("the default salary is in USD; pass --salary in %s too" % currency)
    if args.salary is not None and args.salary <= 0 or args.overhead <= 0:
        p.error("--salary and --overhead must be positive")
    if not os.path.isdir(args.repo):
        p.error("not a directory: %s" % args.repo)
    salary = int(args.salary) if args.salary is not None else DEFAULT_SALARY
    data = valuate(args.repo, salary, args.overhead, currency, args.show_authors)
    if not data["totals"]["code"]:
        print("No code found in %s (skipped: %s)." % (os.path.abspath(args.repo), json.dumps(data["skipped"])),
              file=sys.stderr)
        return 2
    if args.svg:
        try:
            with open(args.svg, "w", encoding="utf-8") as fh:
                fh.write(svg_card(data))
        except OSError as err:
            print("could not write %s: %s" % (args.svg, err), file=sys.stderr)
            return 2
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        sys.stdout.write(report(data))
        if args.svg:
            print("Card written to " + args.svg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
