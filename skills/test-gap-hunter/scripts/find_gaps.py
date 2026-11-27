#!/usr/bin/env python3
"""Rank a repository's source files by how risky their missing tests are. Stdlib only, read-only.

Usage: python3 find_gaps.py [-C REPO] [PATH ...] [--coverage FILE] [--since-days 180] [--now YYYY-MM-DD]
                            [--top 10] [--json]
  -C REPO          repository to scan (default: current directory)
  PATH             limit the scan to these paths (relative to the repo root)
  --coverage FILE  coverage report to read: Cobertura XML or an LCOV tracefile. Without it the script
                   looks for coverage.xml, cobertura.xml, coverage/cobertura-coverage.xml,
                   coverage/coverage.xml, coverage/lcov.info, lcov.info and
                   target/site/cobertura/coverage.xml at the repo root
  --since-days N   churn window: commits that touched each file in the last N days (default 180)
  --now DATE       end of the window (default: today); makes runs reproducible
  --top N          rows in the text table (default 10; --json lists every file)
  --json           machine-readable report on stdout

How it scores each source file (skipped: tests, fixtures, generated, vendored, hidden dirs, migrations,
config, files over 1 MB, and Python files with no functions or classes):
  churn      commits touching the file in the window (`git log --no-merges --name-only`)
  untested   1 - line coverage when the coverage report has the file (evidence "coverage").
             Otherwise a guess from test-file naming (evidence "naming-guess"): 1.0 when no test file
             pairs with it, 0.0 when one does. Pairing rules: Python test_x.py / x_test.py / tests/;
             JS/TS x.test.* / x.spec.* / __tests__/x.*; Go x_test.go in the same directory;
             Java/Kotlin XTest / XTests / XIT / TestX, under the src/test mirror of src/main when the
             source lives there; Ruby x_spec.rb / x_test.rb / test_x.rb. A paired test is not proof
             of coverage and a test with another name is missed, so treat this tier as a lead.
  risk       churn x untested. Ties are broken by untested, then function count.
  functions  Python: functions, methods and classes from `ast`, plus the biggest untested functions of
             the top files (line hits from the report, or every function when guessing). JS/TS and Go:
             a rough regex count. Other languages: not counted.

Coverage formats: Cobertura XML (coverage.py `coverage xml`, istanbul `cobertura`, cobertura-maven):
each <class filename=...> resolved against <sources>/<source>, with line hits from its own <lines>.
LCOV tracefiles (istanbul/jest/c8 `lcov`, geninfo): SF/KF, DA, LF/LH records. Not parsed: JaCoCo XML,
Go coverprofiles, coverage.py JSON. A report entry with no instrumented lines counts as fully covered;
paths from another checkout are matched by their longest suffix that is a tracked file.

Exit codes: 0 report written
            2 not a git repository, bad arguments, or an unreadable or unparseable coverage report
"""
from __future__ import annotations

import argparse
import ast
import datetime as dt
import fnmatch
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

GIT_ENV = dict(os.environ, LC_ALL="C", LANG="C", GIT_TERMINAL_PROMPT="0", GIT_PAGER="cat")
AUTO_REPORTS = ["coverage.xml", "cobertura.xml", "coverage/cobertura-coverage.xml", "coverage/coverage.xml",
                "coverage/lcov.info", "lcov.info", "target/site/cobertura/coverage.xml"]
LANG = {".py": "python", ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js", ".ts": "js", ".tsx": "js",
        ".go": "go", ".java": "java", ".kt": "java", ".rb": "ruby"}
VENDOR_DIRS = {"node_modules", "vendor", "third_party", "third-party", "dist", "build", "out", "site-packages",
               "bower_components", "Pods", ".venv", "venv", ".tox", "coverage", "htmlcov"}
FIXTURE_DIRS = {"fixtures", "__fixtures__", "testdata", "test_data", "__mocks__", "__snapshots__", "mocks"}
GENERATED_DIRS = {"generated", "__generated__", "gen"}
MIGRATION_DIRS = {"migrations", "migrate", "alembic", "flyway"}
GENERATED = ["*_pb2.py", "*_pb2_grpc.py", "*.pb.go", "*.gen.go", "*_gen.go", "*.generated.*", "*.min.js",
             "*.d.ts", "*.bundle.js"]
CONFIG = ["setup.py", "conftest.py", "manage.py", "wsgi.py", "asgi.py", "noxfile.py", "fabfile.py",
          "*.config.js", "*.config.cjs", "*.config.mjs", "*.config.ts", "*rc.js", "*rc.cjs", "Gruntfile.js",
          "Gulpfile.js", "gulpfile.js", "Rakefile", "Gemfile", "*.gemspec", "build.gradle.kts", "settings.gradle.kts"]
TEST_DIRS = {"test", "tests", "spec", "__tests__", "testing"}
MAX_PARSE_BYTES = 1000000


class Fail(Exception):
    pass


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo, *args], env=GIT_ENV, capture_output=True, stdin=subprocess.DEVNULL)
    if r.returncode:
        raise Fail(r.stderr.decode("utf-8", "replace").strip() or "git %s failed" % args[0])
    return r.stdout.decode("utf-8", "replace")


# ---------- classification ----------

def split(path):
    parts = path.split("/")
    name = parts[-1]
    stem, ext = os.path.splitext(name)
    return parts[:-1], name, stem, ext.lower()


def test_stem(path):
    """The source stem a test file covers, or None when the path is not a test file."""
    dirs, name, stem, ext = split(path)
    lang = LANG.get(ext)
    if lang == "python":
        if stem.startswith("test_"):
            return stem[5:]
        if stem.endswith("_test"):
            return stem[:-5]
        return stem if any(d in TEST_DIRS for d in dirs) and stem not in ("__init__", "conftest") else None
    if lang == "js":
        m = re.match(r"^(.+?)\.(test|spec)$", stem)
        if m:
            return m.group(1)
        return stem if "__tests__" in dirs else None
    if lang == "go":
        return stem[:-5] if stem.endswith("_test") else None
    if lang == "java":
        for suf in ("Tests", "Test", "IT"):
            if stem.endswith(suf) and len(stem) > len(suf):
                return stem[:-len(suf)]
        if stem.startswith("Test") and len(stem) > 4 and stem[4].isupper():
            return stem[4:]
        return stem if "test" in dirs and "src" in dirs else None
    if lang == "ruby":
        for suf in ("_spec", "_test"):
            if stem.endswith(suf):
                return stem[:-len(suf)]
        if stem.startswith("test_"):
            return stem[5:]
        return stem if any(d in ("spec", "test") for d in dirs) else None
    return None


def skip_reason(path):
    """Why a path is not a candidate, or None."""
    dirs, name, _stem, _ext = split(path)
    low = [d.lower() for d in dirs]
    if test_stem(path) is not None or name == "conftest.py" or any(d in TEST_DIRS for d in low):
        return "test"
    if any(d in VENDOR_DIRS for d in dirs):
        return "vendored"
    if any(d.startswith(".") for d in dirs):
        return "hidden dir"
    if any(d in FIXTURE_DIRS for d in low):
        return "fixture"
    if any(d in GENERATED_DIRS for d in low) or any(fnmatch.fnmatch(name, g) for g in GENERATED):
        return "generated"
    if any(d in MIGRATION_DIRS for d in low) or "/".join(low[-2:]) == "db/migrate":
        return "migration"
    if any(fnmatch.fnmatch(name, g) for g in CONFIG):
        return "config"
    return None


def common_dirs(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def paired_tests(path, tests_by_stem, sources_by_stem):
    """Test files whose naming says they cover `path`. When several sources share a stem, each test
    pairs with the one whose directory is closest to it."""
    dirs, _name, stem, ext = split(path)
    lang = LANG.get(ext)
    out = []
    for t in tests_by_stem.get((lang, stem), []):
        tdirs = split(t)[0]
        if lang == "go" and tdirs != dirs:
            continue  # Go tests live in the package directory
        if lang == "java" and "main" in dirs and "src" in dirs:
            i = dirs.index("main")
            if tdirs != dirs[:i] + ["test"] + dirs[i + 1:]:
                continue  # src/main/x/Y.java pairs only with src/test/x/YTest.java
        best = max(common_dirs(tdirs, split(s)[0]) for s in sources_by_stem.get((lang, stem), [path]))
        if common_dirs(tdirs, dirs) < best:
            continue
        out.append(t)
    # shortcut: pairing is by file name only; a test named after another module (test_api.py for handlers.py) is missed
    return sorted(out)


# ---------- coverage reports ----------

def parse_cobertura(text):
    """[(filename, sources, {line: hits}, line_rate or None)] from Cobertura XML."""
    root = ET.fromstring(text)
    if root.tag != "coverage":
        raise Fail("not a Cobertura report (root element is <%s>)" % root.tag)
    sources = [(s.text or "").strip() for s in root.iter("source") if (s.text or "").strip()]
    out = []
    for cls in root.iter("class"):
        fn = cls.get("filename")
        if not fn:
            continue
        hits = {}
        for lines in cls.findall("lines"):  # direct child only: <method><lines> repeats the same lines
            for ln in lines.findall("line"):
                try:
                    n, h = int(ln.get("number")), int(float(ln.get("hits", "0")))
                except (TypeError, ValueError):
                    continue
                hits[n] = hits.get(n, 0) + h
        rate = cls.get("line-rate")
        out.append((fn, sources, hits, None if hits else float(rate or 1.0)))  # no <line>: nothing to cover
    return out


def parse_lcov(text):
    """[(filename, [], {line: hits}, line_rate or None)] from an LCOV tracefile."""
    out, cur, hits, lf, lh = [], None, {}, None, None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        tag, _, val = line.partition(":")
        if tag in ("SF", "KF"):
            cur, hits, lf, lh = val, {}, None, None
        elif cur is None:
            continue
        elif tag == "DA":
            f = val.split(",")
            try:
                n, h = int(f[0]), int(float(f[1]))
            except (IndexError, ValueError):
                continue
            hits[n] = hits.get(n, 0) + max(h, 0)
        elif tag == "LF":
            lf = int(val) if val.isdigit() else None
        elif tag == "LH":
            lh = int(val) if val.isdigit() else None
        elif line == "end_of_record":
            rate = None if hits else (lh / lf if lf and lh is not None else 1.0)  # LF:0 = nothing to cover
            out.append((cur, [], hits, rate))
            cur = None
    if not out:
        raise Fail("no SF/end_of_record sections found in LCOV file")
    return out


def load_coverage(report, root, tracked):
    """({repo path: {"hits": {...}, "rate": float}}, format, unmatched count)."""
    try:
        with open(report, encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read()
    except OSError as e:
        raise Fail("cannot read coverage report %s: %s" % (report, e.strerror))
    try:
        if text.lstrip().startswith("<"):
            fmt, entries = "cobertura", parse_cobertura(text)
        else:
            fmt, entries = "lcov", parse_lcov(text)
    except ET.ParseError as e:
        raise Fail("cannot parse coverage report %s: %s" % (os.path.basename(report), e))
    real_root = os.path.realpath(root)
    report_dir = os.path.dirname(os.path.realpath(report))

    def resolve(fn, sources):
        fn = fn.replace("\\", "/")
        cands = []
        for base in sources + [""]:
            c = os.path.join(base, fn)
            cands += [c] if os.path.isabs(c) else [os.path.join(real_root, c), os.path.join(report_dir, c)]
        for c in cands:
            rel = os.path.relpath(os.path.realpath(c), real_root).replace(os.sep, "/")
            if rel in tracked:
                return rel
        parts = [x for x in fn.split("/") if x not in ("", ".")]
        for i in range(len(parts)):  # a report from another checkout: longest tracked suffix wins
            if "/".join(parts[i:]) in tracked:
                return "/".join(parts[i:])
        return None

    cov, unmatched = {}, 0
    for fn, sources, hits, rate in entries:
        path = resolve(fn, sources)
        if path is None:
            unmatched += 1
            continue
        entry = cov.setdefault(path, {"hits": {}, "rate": None})
        for n, h in hits.items():
            entry["hits"][n] = entry["hits"].get(n, 0) + h
        if rate is not None and entry["rate"] is None:
            entry["rate"] = rate
    for entry in cov.values():
        h = entry["hits"]
        if h:
            entry["rate"] = sum(1 for v in h.values() if v > 0) / len(h)
        elif entry["rate"] is None:
            entry["rate"] = 1.0
    return cov, fmt, unmatched


# ---------- code shape ----------

def python_shape(text):
    """(functions, classes, [function info]) or None when the file does not parse on this Python."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    funcs, classes = [], 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            classes += 1
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            branches = sum(isinstance(n, (ast.If, ast.For, ast.While, ast.Try, ast.With, ast.IfExp, ast.BoolOp,
                                          ast.ExceptHandler, ast.AsyncFor, ast.AsyncWith)) for n in ast.walk(node))
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            funcs.append({"name": node.name, "start": start, "end": getattr(node, "end_lineno", node.lineno),
                          "branches": branches})
    return len(funcs), classes, funcs


JS_FUNC = re.compile(r"\bfunction\b|=>|^\s*(?:(?:public|private|protected|static|async|get|set)\s+)*"
                     r"(?!(?:if|for|while|switch|catch|return|function)\b)[A-Za-z_$][\w$]*\s*\([^;\n]*\)\s*\{", re.M)
GO_FUNC = re.compile(r"^func\b", re.M)


def shape(path, text):
    lang = LANG.get(split(path)[3])
    if lang == "python":
        return python_shape(text)
    if lang == "js":
        return len(JS_FUNC.findall(text)), None, None
    if lang == "go":
        return len(GO_FUNC.findall(text)), None, None
    return None


def untested_functions(funcs, cov_entry):
    """Functions sorted by uncovered lines; every function when there is no line data."""
    out = []
    for f in funcs:
        size = f["end"] - f["start"] + 1
        if cov_entry and cov_entry["hits"]:
            lines = [h for n, h in cov_entry["hits"].items() if f["start"] <= n <= f["end"]]
            missed = sum(1 for h in lines if h == 0)
            if not lines or not missed:
                continue
            out.append(dict(f, lines=size, missed=missed, covered=round(1 - missed / len(lines), 2)))
        else:
            out.append(dict(f, lines=size, missed=None, covered=None))
    return sorted(out, key=lambda f: (-(f["missed"] or f["lines"]), -f["branches"], f["start"]))


# ---------- main ----------

def churn(repo, since, until, paths):
    out = git(repo, "log", "--no-merges", "--format=%x01%ct", "--name-only", "-z",
              "--since=%s" % since.strftime("%Y-%m-%dT%H:%M:%SZ"), "HEAD", "--", *paths)
    counts = Counter()
    for rec in out.split("\x01")[1:]:
        fields = rec.split("\0")
        head = fields[0].split("\n", 1)
        if int(head[0]) > until.timestamp():
            continue
        names = ([head[1]] if len(head) > 1 else []) + fields[1:]
        for f in names:
            f = f.strip("\n")
            if f:
                counts[f] += 1
    return counts


def analyse(args):
    try:
        root = git(args.C, "rev-parse", "--show-toplevel").strip()
    except Fail:
        raise Fail("not a git repository: %s" % args.C)
    try:
        git(root, "rev-parse", "--verify", "-q", "HEAD")
    except Fail:
        raise Fail("repository has no commits yet")
    until = dt.datetime.strptime(args.now, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc) + dt.timedelta(days=1) \
        if args.now else dt.datetime.now(dt.timezone.utc)
    since = until - dt.timedelta(days=args.since_days)
    tracked = [p for p in git(root, "ls-files", "-z").split("\0") if p]
    tracked_set = set(tracked)

    report = args.coverage
    if report is None:
        report = next((os.path.join(root, r) for r in AUTO_REPORTS if os.path.isfile(os.path.join(root, r))), None)
    cov, fmt, unmatched, stale = {}, None, 0, False
    if report:
        cov, fmt, unmatched = load_coverage(report, root, tracked_set)
        head_time = int(git(root, "log", "-1", "--format=%ct", "HEAD").strip())
        stale = os.path.getmtime(report) < head_time

    tests_by_stem, test_langs = defaultdict(list), Counter()
    for p in tracked:
        stem = test_stem(p)
        if stem is not None:
            lang = LANG[split(p)[3]]
            tests_by_stem[(lang, stem)].append(p)
            test_langs[lang] += 1

    sources_by_stem = defaultdict(list)
    for p in tracked:
        d, _n, stem, ext = split(p)
        if ext in LANG and skip_reason(p) is None:
            sources_by_stem[(LANG[ext], stem)].append(p)

    scope = [n.replace(os.sep, "/") for n in (os.path.normpath(p) for p in args.paths) if n != "."]
    commits = churn(root, since, until, scope)
    files, skipped = [], Counter()
    for p in tracked:
        if scope and not any(p == s or p.startswith(s + "/") for s in scope):
            continue
        ext = split(p)[3]
        if ext not in LANG and p not in cov:
            continue
        reason = skip_reason(p)
        if reason:
            skipped[reason] += 1
            continue
        full = os.path.join(root, p)
        try:
            if os.path.getsize(full) > MAX_PARSE_BYTES:
                skipped["too large"] += 1
                continue
            with open(full, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            skipped["unreadable"] += 1
            continue
        sh = shape(p, text)
        if LANG.get(ext) == "python" and sh is not None and sh[0] == 0 and sh[1] == 0:
            skipped["no functions"] += 1
            continue
        tests = paired_tests(p, tests_by_stem, sources_by_stem)
        if p in cov:
            evidence, coverage = "coverage", round(cov[p]["rate"], 3)
            untested = 1 - coverage
        else:
            evidence, coverage = "naming-guess", None
            untested = 0.0 if tests else 1.0
        files.append({"path": p, "language": LANG.get(ext, "other"), "churn": commits.get(p, 0),
                      "untested": round(untested, 3), "coverage": coverage, "evidence": evidence, "tests": tests,
                      "functions": sh[0] if sh else None, "classes": sh[1] if sh else None,
                      "parsed": sh is not None or LANG.get(ext) not in ("python", "js", "go"),
                      "risk": round(commits.get(p, 0) * untested, 2), "_funcs": sh[2] if sh else None})
    files.sort(key=lambda f: (-f["risk"], -f["untested"], -(f["functions"] or 0), f["path"]))
    for i, f in enumerate(files):
        funcs = f.pop("_funcs")
        if i < 3 and funcs and f["untested"] > 0:
            f["untested_functions"] = untested_functions(funcs, cov.get(f["path"]))[:5]
    return {
        "window": {"since": since.strftime("%Y-%m-%d"), "until": args.now or until.strftime("%Y-%m-%d"),
                   "days": args.since_days},
        "coverage_report": {"path": os.path.relpath(os.path.realpath(report), os.path.realpath(root)).replace(os.sep, "/"),
                            "format": fmt, "files_matched": len(cov), "entries_unmatched": unmatched,
                            "older_than_head": stale} if report else None,
        "test_files": dict(sorted(test_langs.items())),
        "skipped": dict(sorted(skipped.items())),
        "scoring": "risk = churn (commits in window) x untested (1 - line coverage, or 1.0/0.0 by test naming when "
                   "the file is not in a coverage report)",
        "files": files,
    }


def render(rep, top):
    out = []
    w = rep["window"]
    out.append("Test gaps: churn %s..%s (%d days) x untested" % (w["since"], w["until"], w["days"]))
    c = rep["coverage_report"]
    if c and not c["files_matched"]:
        out.append("Evidence: %s report %s matched no tracked files; every row is a naming-convention GUESS"
                   % (c["format"], c["path"]))
    elif c:
        out.append("Evidence: %s report %s, %d files matched, %d entries unmatched%s"
                   % (c["format"], c["path"], c["files_matched"], c["entries_unmatched"],
                      " (report is older than HEAD: re-run coverage)" if c["older_than_head"] else ""))
    else:
        out.append("Evidence: no coverage report found; every row is a naming-convention GUESS")
    tl = ", ".join("%s %d" % kv for kv in rep["test_files"].items()) or "none"
    sk = ", ".join("%s %d" % kv for kv in rep["skipped"].items()) or "none"
    out.append("Test files seen: %s. Skipped: %s" % (tl, sk))
    out.append("")
    files = rep["files"]
    if not files:
        out.append("No source files to rank.")
        return "\n".join(out)
    out.append("%4s %6s %6s %8s %6s  %-13s %s" % ("#", "risk", "churn", "covered", "funcs", "evidence", "path"))
    for i, f in enumerate(files[:top], 1):
        covd = "%d%%" % round(f["coverage"] * 100) if f["coverage"] is not None else ("paired" if f["tests"] else "none")
        funcs = "?" if f["functions"] is None else str(f["functions"])
        out.append("%4d %6s %6d %8s %6s  %-13s %s" % (i, f["risk"], f["churn"], covd, funcs, f["evidence"], f["path"]))
    for f in files[:3]:
        uf = f.get("untested_functions")
        if not uf:
            continue
        label = "uncovered lines" if uf[0]["missed"] is not None else "no paired test, biggest first"
        out.append("")
        out.append("%s (%s):" % (f["path"], label))
        for fn in uf:
            extra = "%d missed" % fn["missed"] if fn["missed"] is not None else "%d lines" % fn["lines"]
            out.append("  L%-5d %-32s %s, %d branches" % (fn["start"], fn["name"], extra, fn["branches"]))
    out.append("")
    out.append("risk = churn x untested. Naming-guess rows only say whether a test file with a matching name "
               "exists; read the file before trusting the row.")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 epilog="Exit codes: 0 report written; 2 not a git repository, bad arguments, "
                                        "or an unreadable coverage report.")
    ap.add_argument("-C", default=".", metavar="REPO")
    ap.add_argument("paths", nargs="*", metavar="PATH")
    ap.add_argument("--coverage", metavar="FILE")
    ap.add_argument("--since-days", type=int, default=180)
    ap.add_argument("--now", metavar="YYYY-MM-DD")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.since_days < 1 or args.top < 1:
        ap.error("--since-days and --top must be positive")
    if args.now:
        try:
            dt.datetime.strptime(args.now, "%Y-%m-%d")
        except ValueError:
            ap.error("--now expects YYYY-MM-DD")
    if not os.path.isdir(args.C):
        ap.error("no such directory: %s" % args.C)
    try:
        rep = analyse(args)
    except Fail as e:
        print("find_gaps: %s" % e, file=sys.stderr)
        return 2
    print(json.dumps(rep, indent=2) if args.json else render(rep, args.top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
